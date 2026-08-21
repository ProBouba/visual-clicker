"""Detection engine.

Pure logic, no Qt/UI imports here (besides plain dataclasses) so it can be
unit-tested in isolation and so it never risks touching widgets from a
background thread. Communicates outward via plain Python callables/return
values; the MonitoringWorker (which DOES run on a QThread) is responsible
for turning DetectionEvent objects into Qt signals for the GUI.

Workflow per cycle:
  1. Capture each UNIQUE search region once (targets sharing a region, or all
     using "full screen", share a single capture — avoids redundant mss calls).
  2. Match every enabled target against its region's capture (best-of-N
     templates, optionally multi-scale), recording a visibility map for ALL
     enabled targets regardless of cooldown — this is what lets the
     "requires_target_id" condition system see whether a *different* target
     is currently visible even if that other target itself isn't click-eligible
     this cycle.
  3. Drop matches whose center falls inside an exclusion region.
  4. Build the click-eligible subset (not on cooldown, under click limit),
     sorted by priority, and emit DetectionEvents for eligible targets that
     are visible, satisfy their minimum-visible-duration, and satisfy their
     condition (if any).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from core.template_matcher import TemplateMatcher, MatchResult
from models.target import DetectionState, SearchRegion, Target
from services.screen_capture import ScreenCaptureService


@dataclass
class DetectionEvent:
    target: Target
    confidence: float
    global_x: int  # center point, global screen coords
    global_y: int
    match_width: int
    match_height: int


@dataclass
class _Visibility:
    found: bool
    confidence: float
    global_x: int = -1
    global_y: int = -1
    width: int = 0
    height: int = 0


class DetectionEngine:
    def __init__(self, capture: ScreenCaptureService, matcher: Optional[TemplateMatcher] = None) -> None:
        self.capture = capture
        self.matcher = matcher or TemplateMatcher()
        # first-seen timestamps for targets that require a minimum visible duration
        # before they're allowed to be clicked
        self._visible_since: dict[str, float] = {}

    def run_cycle(self, targets: List[Target], now: Optional[float] = None) -> List[DetectionEvent]:
        now = now if now is not None else time.time()
        events: List[DetectionEvent] = []

        enabled_targets = [t for t in targets if t.enabled and t.template_path]
        region_cache = self._capture_unique_regions(enabled_targets)

        # Pass 1: visibility map for every enabled target (independent of cooldown),
        # so condition checks ("requires_target_id") can see cross-target visibility.
        visibility: dict[str, _Visibility] = {}
        for target in enabled_targets:
            screen = region_cache[self._region_key(target.search_region)]
            match = self._match_target(target, screen)

            if match.found:
                gx, gy = self._to_global(target, match)
                if self._inside_exclusion(target, gx, gy):
                    match = MatchResult(found=False, confidence=match.confidence)
                else:
                    visibility[target.id] = _Visibility(True, match.confidence, gx, gy, match.width, match.height)

            if target.id not in visibility:
                visibility[target.id] = _Visibility(False, match.confidence)

            target.push_confidence_sample(match.confidence)
            target.last_confidence = match.confidence

        # Pass 2: build events for click-eligible targets.
        eligible = [
            t for t in enabled_targets
            if not t.is_on_cooldown(now) and not t.has_reached_click_limit()
        ]
        eligible.sort(key=lambda t: (-t.priority, t.order_index))

        for target in eligible:
            vis = visibility[target.id]

            if not vis.found:
                self._visible_since.pop(target.id, None)
                target.state = DetectionState.IDLE
                continue

            target.state = DetectionState.VISIBLE

            if target.min_visible_duration_ms > 0:
                first_seen = self._visible_since.get(target.id)
                if first_seen is None:
                    self._visible_since[target.id] = now
                    continue
                if (now - first_seen) * 1000.0 < target.min_visible_duration_ms:
                    continue
            else:
                self._visible_since[target.id] = now

            if target.requires_target_id:
                required = visibility.get(target.requires_target_id)
                if required is None or not required.found:
                    continue  # condition not satisfied this cycle

            events.append(
                DetectionEvent(
                    target=target,
                    confidence=vis.confidence,
                    global_x=vis.global_x,
                    global_y=vis.global_y,
                    match_width=vis.width,
                    match_height=vis.height,
                )
            )

        return events

    def test_target(self, target: Target) -> tuple[MatchResult, tuple[int, int]]:
        """Single-shot check used by 'Test Target' / 'Preview Detection' — never mutates
        target state or affects cooldown bookkeeping."""
        if target.search_region:
            r = target.search_region
            screen = self.capture.capture_region(r.x, r.y, r.width, r.height)
        else:
            screen, _, _ = self.capture.capture_full_virtual_desktop()

        match = self._match_target(target, screen)
        if match.found:
            gx, gy = self._to_global(target, match)
            if self._inside_exclusion(target, gx, gy):
                return MatchResult(found=False, confidence=match.confidence), (-1, -1)
            return match, (gx, gy)
        return match, (-1, -1)

    # ------------------------------------------------------------------

    def _region_key(self, region: Optional[SearchRegion]) -> tuple:
        if region is None:
            return ("full",)
        return (region.x, region.y, region.width, region.height)

    def _capture_unique_regions(self, targets: List[Target]) -> dict[tuple, np.ndarray]:
        cache: dict[tuple, np.ndarray] = {}
        needs_full = any(t.search_region is None for t in targets)
        if needs_full:
            screen, _, _ = self.capture.capture_full_virtual_desktop()
            cache[("full",)] = screen

        seen_regions: dict[tuple, SearchRegion] = {}
        for t in targets:
            if t.search_region is not None:
                seen_regions[self._region_key(t.search_region)] = t.search_region

        for key, region in seen_regions.items():
            cache[key] = self.capture.capture_region(region.x, region.y, region.width, region.height)

        return cache

    def _match_target(self, target: Target, screen: np.ndarray) -> MatchResult:
        paths = target.all_template_paths()
        if not paths:
            return MatchResult(found=False)
        return self.matcher.match_best_of(
            screen,
            paths,
            confidence_threshold=target.confidence_threshold,
            grayscale=target.grayscale_matching,
            multi_scale=target.multi_scale_matching,
            scale_range_percent=target.scale_range_percent,
        )

    def _inside_exclusion(self, target: Target, gx: int, gy: int) -> bool:
        for region in target.exclusion_regions:
            if region.x <= gx <= region.x + region.width and region.y <= gy <= region.y + region.height:
                return True
        return False

    def _to_global(self, target: Target, match: MatchResult) -> tuple[int, int]:
        cx, cy = match.center
        if target.search_region:
            return target.search_region.x + cx, target.search_region.y + cy
        bbox = self.capture.virtual_desktop_bbox()
        return bbox.left + cx, bbox.top + cy
