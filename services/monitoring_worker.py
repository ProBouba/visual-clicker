"""MonitoringWorker — runs the detection/click loop on a dedicated QThread.

This is the only place where the "continuously watch the screen and click"
loop actually executes. It never touches GUI widgets directly; it only emits
Qt signals, which Qt automatically marshals to the main thread's event loop
via the queued-connection mechanism (safe by construction as long as we
never call widget methods from here).

A threading.Lock protects reads of the shared target list, since the GUI
thread may add/edit/delete targets while this thread is iterating them.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from PySide6.QtCore import QThread, Signal

from core.click_controller import ClickController, ClickResult
from core.detection_engine import DetectionEngine, DetectionEvent
from models.target import DetectionState, Target
from services.screen_capture import ScreenCaptureService


class MonitoringWorker(QThread):
    detection_occurred = Signal(str, float, int, int, int, int)      # target_id, confidence, x, y, width, height
    click_performed = Signal(str, int, int)                    # target_id, x, y
    click_failed = Signal(str, str)                             # target_id, error message
    cycle_completed = Signal(int)                                # number of detections this cycle
    error_occurred = Signal(str)                                 # unexpected/fatal error message
    stats_tick = Signal()                                        # emitted every cycle, for dashboard refresh

    def __init__(
        self,
        get_targets: Callable[[], List[Target]],
        target_lock: threading.Lock,
        interval_ms: int = 500,
        preview_only: bool = False,
        adaptive_enabled: bool = False,
        adaptive_idle_cycles: int = 20,
        adaptive_max_interval_ms: int = 3000,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._get_targets = get_targets
        self._lock = target_lock
        self.base_interval_ms = interval_ms
        self.interval_ms = interval_ms
        self.preview_only = preview_only

        self.adaptive_enabled = adaptive_enabled
        self.adaptive_idle_cycles = max(1, adaptive_idle_cycles)
        self.adaptive_max_interval_ms = max(interval_ms, adaptive_max_interval_ms)
        self._idle_cycle_count = 0

        self._running = threading.Event()
        self._paused = threading.Event()
        self._stop_requested = threading.Event()

    # --- external control (safe to call from GUI thread) -----------------

    def request_stop(self) -> None:
        self._stop_requested.set()
        self._paused.clear()
        self._running.clear()

    def request_pause(self) -> None:
        self._paused.set()

    def request_resume(self) -> None:
        self._paused.clear()

    def set_interval_ms(self, interval_ms: int) -> None:
        self.base_interval_ms = max(30, interval_ms)
        self.interval_ms = self.base_interval_ms
        self._idle_cycle_count = 0

    def is_paused(self) -> bool:
        return self._paused.is_set()

    # --- QThread entry point ----------------------------------------------

    def run(self) -> None:
        # Each thread needs its own mss instance — mss is not guaranteed thread-safe.
        capture = ScreenCaptureService()
        engine = DetectionEngine(capture)
        clicker = ClickController()

        self._running.set()
        self._stop_requested.clear()

        try:
            while not self._stop_requested.is_set():
                if self._paused.is_set():
                    time.sleep(0.05)
                    continue

                cycle_start = time.time()

                with self._lock:
                    targets_snapshot = list(self._get_targets())

                try:
                    events: List[DetectionEvent] = engine.run_cycle(targets_snapshot)
                except Exception as exc:  # OpenCV/mss errors etc. must never kill silent-clicking loop
                    self.error_occurred.emit(f"Detection cycle failed: {exc}")
                    events = []

                for event in events:
                    self.detection_occurred.emit(
                        event.target.id, event.confidence, event.global_x, event.global_y,
                        event.match_width, event.match_height,
                    )

                    if self.preview_only:
                        continue

                    result: ClickResult = clicker.click_for_target(event.target, event.global_x, event.global_y)
                    if result.success:
                        self._apply_click_bookkeeping(event)
                        self.click_performed.emit(event.target.id, result.x, result.y)
                    else:
                        self.click_failed.emit(event.target.id, result.error)

                    if event.target.stop_after_click:
                        event.target.enabled = False

                self.cycle_completed.emit(len(events))
                self.stats_tick.emit()

                # Adaptive interval: back off polling speed after a stretch of
                # idle (no-detection) cycles to save CPU, snap back to base speed
                # the moment something is actually detected.
                if self.adaptive_enabled:
                    if events:
                        self._idle_cycle_count = 0
                        self.interval_ms = self.base_interval_ms
                    else:
                        self._idle_cycle_count += 1
                        if self._idle_cycle_count >= self.adaptive_idle_cycles:
                            self.interval_ms = min(self.adaptive_max_interval_ms, self.interval_ms + self.base_interval_ms)

                elapsed_ms = (time.time() - cycle_start) * 1000.0
                remaining = max(0.0, (self.interval_ms - elapsed_ms) / 1000.0)
                time.sleep(remaining)
        except Exception as exc:
            self.error_occurred.emit(f"Fatal monitoring error: {exc}")
        finally:
            capture.close()
            self._running.clear()

    def _apply_click_bookkeeping(self, event: DetectionEvent) -> None:
        t = event.target
        now = time.time()
        t.click_count += 1
        t.detection_count += 1
        t.confidence_sum += event.confidence
        t.last_detection_ts = now
        t.last_click_ts = now
        t.last_confidence = event.confidence
        t.last_click_x = event.global_x
        t.last_click_y = event.global_y
        t.state = DetectionState.COOLDOWN
