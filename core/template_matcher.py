"""Template matching using OpenCV.

Fixed-scale matching only (per project decision): the saved template is
matched against the screen at its native captured resolution. This is the
simplest and fastest approach and is correct as long as the user's display
scaling / resolution stays constant between capture time and run time.

If a template starts failing to match, it is almost certainly because the
Windows display scaling (DPI) changed, the target window was resized, or a
different monitor with a different resolution is now being used. There is no
silent multi-scale fallback here by design; instead the GUI surfaces a clear
warning (see DetectionEngine) so the user can simply re-capture the target
rather than fighting a non-deterministic auto-scale search.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


@dataclass
class MatchResult:
    found: bool
    confidence: float = 0.0
    x: int = 0          # top-left x of match, in the coordinate space that was searched
    y: int = 0          # top-left y of match
    width: int = 0
    height: int = 0

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


class TemplateCache:
    """Caches loaded template images (and their grayscale variants) so we never
    re-read/re-decode a PNG from disk on every single detection cycle."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, np.ndarray, np.ndarray]] = {}
        # maps template_path -> (mtime, bgr_array, gray_array)

    def get(self, template_path: str) -> Optional[tuple[np.ndarray, np.ndarray]]:
        path = Path(template_path)
        if not path.exists():
            self._cache.pop(template_path, None)
            return None

        mtime = path.stat().st_mtime
        cached = self._cache.get(template_path)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]

        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        self._cache[template_path] = (mtime, img, gray)
        return img, gray

    def invalidate(self, template_path: str) -> None:
        self._cache.pop(template_path, None)

    def clear(self) -> None:
        self._cache.clear()


class TemplateMatcher:
    """Stateless-per-call matcher; template loading/caching is delegated to TemplateCache
    so the same matcher instance can be reused cheaply across many detection cycles."""

    def __init__(self, cache: Optional[TemplateCache] = None) -> None:
        self.cache = cache or TemplateCache()

    def match(
        self,
        screen_bgr: np.ndarray,
        template_path: str,
        confidence_threshold: float,
        grayscale: bool = False,
    ) -> MatchResult:
        loaded = self.cache.get(template_path)
        if loaded is None:
            return MatchResult(found=False)

        template_bgr, template_gray = loaded
        th, tw = template_gray.shape[:2]
        sh, sw = screen_bgr.shape[:2]

        if th > sh or tw > sw:
            # Template is larger than the search area (e.g. a restricted region that's
            # smaller than the template) — matchTemplate would raise, so bail out cleanly.
            return MatchResult(found=False)

        if grayscale:
            screen_proc = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
            template_proc = template_gray
        else:
            screen_proc = screen_bgr
            template_proc = template_bgr

        result = cv2.matchTemplate(screen_proc, template_proc, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val >= confidence_threshold:
            return MatchResult(
                found=True,
                confidence=float(max_val),
                x=int(max_loc[0]),
                y=int(max_loc[1]),
                width=tw,
                height=th,
            )
        return MatchResult(found=False, confidence=float(max_val))

    def match_multi_scale(
        self,
        screen_bgr: np.ndarray,
        template_path: str,
        confidence_threshold: float,
        grayscale: bool = False,
        scale_range_percent: int = 20,
        scale_step_percent: int = 5,
    ) -> MatchResult:
        """Tries the template at several scales around 100% and returns the best match.

        Used as an opt-in fallback for targets where the exact captured resolution can no
        longer be guaranteed to match live (display scaling changed, window resized, etc.).
        More expensive than a single fixed-scale match, so it's only run when the target
        has multi_scale_matching enabled.
        """
        loaded = self.cache.get(template_path)
        if loaded is None:
            return MatchResult(found=False)

        template_bgr, template_gray = loaded
        sh, sw = screen_bgr.shape[:2]

        if grayscale:
            screen_proc = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        else:
            screen_proc = screen_bgr

        best = MatchResult(found=False)
        scale_range = max(0, scale_range_percent)
        step = max(1, scale_step_percent)

        scales = range(-scale_range, scale_range + 1, step)
        for pct in scales:
            scale = 1.0 + (pct / 100.0)
            if scale <= 0:
                continue

            th0, tw0 = template_gray.shape[:2]
            tw = max(1, round(tw0 * scale))
            th = max(1, round(th0 * scale))
            if th > sh or tw > sw or th < 4 or tw < 4:
                continue

            src = template_gray if grayscale else template_bgr
            resized = cv2.resize(src, (tw, th), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)

            result = cv2.matchTemplate(screen_proc, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best.confidence:
                best = MatchResult(
                    found=max_val >= confidence_threshold,
                    confidence=float(max_val),
                    x=int(max_loc[0]),
                    y=int(max_loc[1]),
                    width=tw,
                    height=th,
                )

        return best

    def match_best_of(
        self,
        screen_bgr: np.ndarray,
        template_paths: list[str],
        confidence_threshold: float,
        grayscale: bool = False,
        multi_scale: bool = False,
        scale_range_percent: int = 20,
    ) -> MatchResult:
        """Matches against several template samples for the same target (e.g. captured in
        slightly different states/lighting) and returns whichever scores highest."""
        best = MatchResult(found=False)
        for path in template_paths:
            if multi_scale:
                result = self.match_multi_scale(
                    screen_bgr, path, confidence_threshold, grayscale, scale_range_percent
                )
            else:
                result = self.match(screen_bgr, path, confidence_threshold, grayscale)
            if result.confidence > best.confidence:
                best = result
        return best
