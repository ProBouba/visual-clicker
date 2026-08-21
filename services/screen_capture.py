"""Screen capture service built on mss.

mss reports monitor geometry (including for setups where the primary monitor
does NOT start at (0, 0) — e.g. a secondary monitor placed to the left/above
the primary) via `sct.monitors`. Index 0 is the special "all monitors combined"
virtual bounding box; indices 1..N are the individual physical monitors.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import mss
import numpy as np


@dataclass
class MonitorInfo:
    index: int  # 1-based, matches mss.monitors index (0 = "all monitors" virtual box)
    left: int
    top: int
    width: int
    height: int

    @property
    def name(self) -> str:
        return f"Monitor {self.index} ({self.width}x{self.height} @ {self.left},{self.top})"


class ScreenCaptureService:
    """Thin wrapper around mss with helpers for full-virtual-desktop and region capture.

    Each MonitoringWorker thread must create its OWN ScreenCaptureService instance,
    since mss's underlying `sct` object is not thread-safe / not guaranteed to work
    when shared across threads.
    """

    def __init__(self) -> None:
        self._sct = mss.mss()

    def list_monitors(self) -> List[MonitorInfo]:
        monitors = self._sct.monitors
        result = []
        for idx in range(1, len(monitors)):
            m = monitors[idx]
            result.append(MonitorInfo(index=idx, left=m["left"], top=m["top"], width=m["width"], height=m["height"]))
        return result

    def virtual_desktop_bbox(self) -> MonitorInfo:
        """The bounding box covering ALL monitors combined (mss.monitors[0])."""
        m = self._sct.monitors[0]
        return MonitorInfo(index=0, left=m["left"], top=m["top"], width=m["width"], height=m["height"])

    def capture_region(self, left: int, top: int, width: int, height: int) -> np.ndarray:
        """Capture an arbitrary screen region (in global/virtual-desktop coordinates)
        and return it as a BGR numpy array (OpenCV-compatible, no alpha channel)."""
        region = {"left": left, "top": top, "width": max(1, width), "height": max(1, height)}
        shot = self._sct.grab(region)
        arr = np.array(shot)  # BGRA
        return arr[:, :, :3]  # drop alpha -> BGR

    def capture_full_virtual_desktop(self) -> tuple[np.ndarray, int, int]:
        """Capture everything across all monitors. Returns (bgr_array, origin_left, origin_top)
        so callers can translate array-local match coordinates back to global screen coords."""
        bbox = self.virtual_desktop_bbox()
        arr = self.capture_region(bbox.left, bbox.top, bbox.width, bbox.height)
        return arr, bbox.left, bbox.top

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass
