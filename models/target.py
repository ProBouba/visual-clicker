"""Target data model — the core unit of configuration for a visual automation target."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class ClickType(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    DOUBLE = "double"


class DetectionState(str, Enum):
    IDLE = "idle"
    VISIBLE = "visible"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


@dataclass
class SearchRegion:
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[dict]) -> Optional["SearchRegion"]:
        if not d:
            return None
        return SearchRegion(x=d["x"], y=d["y"], width=d["width"], height=d["height"])


@dataclass
class Target:
    """A single visual automation target.

    NOTE: This class is intentionally NOT frozen/hashable via dataclass eq on all fields,
    because targets mutate constantly (last_detection, click_count, etc.) during monitoring.
    We give it a stable identity-based __hash__/__eq__ keyed on `id` so it can safely be used
    in sets/dicts (e.g. cooldown trackers, "currently visible" sets) without breaking when
    mutable fields change.
    """

    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New Target"
    template_path: str = ""
    additional_template_paths: list[str] = field(default_factory=list)  # extra samples; best-of-N match

    # Order / priority
    order_index: int = 0
    priority: int = 0  # higher = more important

    # Detection settings
    enabled: bool = True
    confidence_threshold: float = 0.85
    grayscale_matching: bool = False
    detection_interval_ms: int = 500
    search_region: Optional[SearchRegion] = None
    exclusion_regions: list[SearchRegion] = field(default_factory=list)  # matches inside these are ignored
    multi_scale_matching: bool = False
    scale_range_percent: int = 20  # search +/- this % around 100% when multi_scale_matching is on
    requires_target_id: Optional[str] = None  # this target only fires if the referenced target is ALSO visible

    # Click settings
    click_type: ClickType = ClickType.LEFT
    click_offset_x: int = 0
    click_offset_y: int = 0
    randomize_offset: bool = False
    randomize_radius_px: int = 3
    delay_before_click_ms: int = 0
    delay_after_click_ms: int = 100

    # Behavior settings
    cooldown_ms: int = 1500
    max_click_count: int = 0  # 0 = unlimited
    stop_after_click: bool = False
    min_visible_duration_ms: int = 0

    # Runtime / stats (persisted so history survives restarts)
    click_count: int = 0
    detection_count: int = 0
    failure_count: int = 0
    last_detection_ts: float = 0.0
    last_click_ts: float = 0.0
    last_confidence: float = 0.0
    last_click_x: int = -1
    last_click_y: int = -1
    confidence_sum: float = 0.0  # used to compute running average confidence
    confidence_history: list[float] = field(default_factory=list)  # capped ring buffer, most recent last

    # Transient runtime-only state (not persisted in a meaningful way, but harmless to store)
    state: DetectionState = DetectionState.IDLE

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Target):
            return NotImplemented
        return self.id == other.id

    # --- convenience / derived properties -------------------------------

    @property
    def average_confidence(self) -> float:
        if self.detection_count == 0:
            return 0.0
        return self.confidence_sum / self.detection_count

    def is_on_cooldown(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        return (now - self.last_click_ts) * 1000.0 < self.cooldown_ms

    def has_reached_click_limit(self) -> bool:
        return self.max_click_count > 0 and self.click_count >= self.max_click_count

    def all_template_paths(self) -> list[str]:
        paths = [self.template_path] if self.template_path else []
        paths.extend(p for p in self.additional_template_paths if p)
        return paths

    def push_confidence_sample(self, confidence: float, max_history: int = 60) -> None:
        self.confidence_history.append(confidence)
        if len(self.confidence_history) > max_history:
            self.confidence_history = self.confidence_history[-max_history:]

    # --- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["click_type"] = self.click_type.value
        d["state"] = self.state.value
        d["search_region"] = self.search_region.to_dict() if self.search_region else None
        d["exclusion_regions"] = [r.to_dict() for r in self.exclusion_regions]
        return d

    @staticmethod
    def from_dict(d: dict) -> "Target":
        d = dict(d)  # shallow copy, don't mutate caller's dict
        d["click_type"] = ClickType(d.get("click_type", ClickType.LEFT.value))
        d["state"] = DetectionState(d.get("state", DetectionState.IDLE.value))
        d["search_region"] = SearchRegion.from_dict(d.get("search_region"))
        d["exclusion_regions"] = [SearchRegion.from_dict(r) for r in d.get("exclusion_regions", []) if r]
        # Drop any unknown keys gracefully (forward/backward compatibility)
        valid_fields = {f for f in Target.__dataclass_fields__.keys()}
        d = {k: v for k, v in d.items() if k in valid_fields}
        return Target(**d)
