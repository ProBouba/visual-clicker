"""AutomationManager — the top-level orchestrator.

Owns the target list, the persistence layer, the background MonitoringWorker
thread, and running statistics. The UI (MainWindow) talks almost exclusively
to this class rather than reaching into the worker/engine/persistence layers
directly, which keeps GUI code and automation logic cleanly separated per the
project's architecture requirements.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from PySide6.QtCore import QObject, Signal

from core.persistence import ConfigManager
from models.settings import AppSettings
from models.target import DetectionState, Target
from services.logging_service import LoggingService
from services.monitoring_worker import MonitoringWorker


class AutomationState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class SessionStats:
    started_at: float = 0.0
    total_clicks: int = 0
    total_detections: int = 0
    last_target_name: str = "-"
    last_click_time: str = "-"
    last_confidence: float = 0.0
    detections_this_minute: int = 0
    _minute_bucket_start: float = field(default_factory=time.time)

    def register_detection(self, target_name: str, confidence: float) -> None:
        self.total_detections += 1
        self.last_target_name = target_name
        self.last_confidence = confidence
        now = time.time()
        if now - self._minute_bucket_start > 60:
            self._minute_bucket_start = now
            self.detections_this_minute = 0
        self.detections_this_minute += 1

    def register_click(self) -> None:
        self.total_clicks += 1
        self.last_click_time = time.strftime("%H:%M:%S")


class AutomationManager(QObject):
    state_changed = Signal(str)          # AutomationState value
    stats_updated = Signal()
    target_updated = Signal(str)          # target_id — fired when a target's runtime stats change
    log_message = Signal(str, str)        # level, message  (mirrors LoggingService for convenience)
    match_visualized = Signal(int, int, int, int)  # global_x, global_y, width, height — for the on-screen highlight
    trash_changed = Signal()               # fired when the undo-delete trash contents change

    MAX_TRASH_SIZE = 15

    def __init__(self, base_dir: str = ".", profile: str = "default") -> None:
        super().__init__()
        self.config = ConfigManager(base_dir)
        self.logger = LoggingService(self.config.logs_dir)
        self.app_settings: AppSettings = self.config.load_app_settings()

        self.profile = profile
        self.targets: List[Target] = []
        self._target_lock = threading.Lock()
        self.stats = SessionStats()
        self.state = AutomationState.STOPPED
        self.worker: Optional[MonitoringWorker] = None
        self._trash: List[Target] = []  # soft-deleted targets, for Undo Delete

        self._load()

    # ------------------------------------------------------------------
    # Target CRUD (always lock-protected since the worker thread reads this list)
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            self.targets = self.config.load_targets(self.profile)
            self.logger.info(f"Loaded {len(self.targets)} target(s) from profile '{self.profile}'.")
        except Exception as exc:
            self.targets = []
            self.logger.error(f"Failed to load profile '{self.profile}': {exc}")

    def save(self) -> None:
        with self._target_lock:
            snapshot = list(self.targets)
        self.config.save_targets(snapshot, self.profile)
        self.config.save_app_settings(self.app_settings)

    def add_target(self, target: Target) -> None:
        with self._target_lock:
            target.order_index = len(self.targets)
            self.targets.append(target)
        self.save()
        self.logger.success(f"Target '{target.name}' added.")

    def update_target(self, target: Target) -> None:
        with self._target_lock:
            for i, t in enumerate(self.targets):
                if t.id == target.id:
                    self.targets[i] = target
                    break
        self.save()

    def delete_target(self, target_id: str) -> None:
        with self._target_lock:
            target = next((t for t in self.targets if t.id == target_id), None)
            if target is None:
                return
            self.targets.remove(target)
            for i, t in enumerate(self.targets):
                t.order_index = i
        # Soft delete: keep the target (and its screenshot file) around for a
        # short-lived undo, rather than destroying it immediately. Only the
        # oldest entry gets permanently purged (screenshot + all) once the
        # trash exceeds its cap.
        self._trash.append(target)
        purged: Optional[Target] = None
        if len(self._trash) > self.MAX_TRASH_SIZE:
            purged = self._trash.pop(0)
        self.save()
        self.trash_changed.emit()
        self.logger.info(f"Target '{target.name}' deleted (undo available).")
        if purged is not None:
            self.config.delete_screenshot(purged)

    def undo_delete(self, target_id: Optional[str] = None) -> Optional[Target]:
        """Restores the most recently deleted target, or a specific one by id if given."""
        if not self._trash:
            return None
        if target_id is None:
            target = self._trash.pop()
        else:
            target = next((t for t in self._trash if t.id == target_id), None)
            if target is None:
                return None
            self._trash.remove(target)

        with self._target_lock:
            target.order_index = len(self.targets)
            self.targets.append(target)
        self.save()
        self.trash_changed.emit()
        self.logger.success(f"Target '{target.name}' restored.")
        return target

    def trash_items(self) -> List[Target]:
        return list(self._trash)

    def empty_trash(self) -> None:
        for t in self._trash:
            self.config.delete_screenshot(t)
        self._trash.clear()
        self.trash_changed.emit()

    def move_target(self, target_id: str, direction: int) -> None:
        """direction: -1 to move up, +1 to move down."""
        with self._target_lock:
            idx = next((i for i, t in enumerate(self.targets) if t.id == target_id), None)
            if idx is None:
                return
            new_idx = idx + direction
            if not (0 <= new_idx < len(self.targets)):
                return
            self.targets[idx], self.targets[new_idx] = self.targets[new_idx], self.targets[idx]
            for i, t in enumerate(self.targets):
                t.order_index = i
        self.save()

    def get_target(self, target_id: str) -> Optional[Target]:
        return next((t for t in self.targets if t.id == target_id), None)

    # ------------------------------------------------------------------
    # Automation lifecycle
    # ------------------------------------------------------------------

    def start(self, preview_only: bool = False) -> None:
        if self.worker is not None and self.worker.isRunning():
            if self.worker.is_paused():
                self.worker.request_resume()
                self._set_state(AutomationState.RUNNING)
                self.logger.info("Automation resumed.")
            return

        interval = self.app_settings.effective_interval_ms()
        self.worker = MonitoringWorker(
            get_targets=lambda: self.targets,
            target_lock=self._target_lock,
            interval_ms=interval,
            preview_only=preview_only,
            adaptive_enabled=self.app_settings.adaptive_interval_enabled,
            adaptive_idle_cycles=self.app_settings.adaptive_idle_cycles_before_backoff,
            adaptive_max_interval_ms=self.app_settings.adaptive_max_interval_ms,
        )
        self.worker.detection_occurred.connect(self._on_detection)
        self.worker.click_performed.connect(self._on_click)
        self.worker.click_failed.connect(self._on_click_failed)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.stats_tick.connect(self.stats_updated.emit)

        self.stats.started_at = time.time()
        self.worker.start()
        self._set_state(AutomationState.RUNNING)
        mode = "Preview (no clicks)" if preview_only else "Automation"
        self.logger.success(f"{mode} started. Monitoring interval: {interval} ms.")

    def pause(self) -> None:
        if self.worker is None:
            return
        self.worker.request_pause()
        self._set_state(AutomationState.PAUSED)
        self.logger.info("Automation paused.")

    def resume(self) -> None:
        if self.worker is None:
            return
        self.worker.request_resume()
        self._set_state(AutomationState.RUNNING)
        self.logger.info("Automation resumed.")

    def stop(self) -> None:
        if self.worker is None:
            self._set_state(AutomationState.STOPPED)
            return
        self.worker.request_stop()
        self.worker.wait(3000)
        self.worker = None
        self._set_state(AutomationState.STOPPED)
        self.logger.info("Automation stopped.")

    def emergency_stop(self) -> None:
        """Immediately halts everything. Distinguished from stop() by intent/urgency —
        functionally it forces the same hard stop, but always logs at error/warning level
        and is reachable from the global hotkey even when the window isn't focused."""
        if self.worker is not None:
            self.worker.request_stop()
            self.worker.wait(3000)
            self.worker = None
        self._set_state(AutomationState.STOPPED)
        self.logger.warning("EMERGENCY STOP triggered. All automation halted immediately.")

    def _set_state(self, state: AutomationState) -> None:
        self.state = state
        self.state_changed.emit(state.value)

    # ------------------------------------------------------------------
    # Worker signal handlers (run on GUI thread — Qt queues these automatically)
    # ------------------------------------------------------------------

    def _on_detection(self, target_id: str, confidence: float, x: int, y: int, width: int, height: int) -> None:
        target = self.get_target(target_id)
        name = target.name if target else target_id
        self.stats.register_detection(name, confidence)
        self.logger.info(f"Target \"{name}\" detected — confidence {confidence:.2f} at ({x}, {y})")
        self.target_updated.emit(target_id)
        self.match_visualized.emit(x - width // 2, y - height // 2, width, height)

    def _on_click(self, target_id: str, x: int, y: int) -> None:
        target = self.get_target(target_id)
        name = target.name if target else target_id
        self.stats.register_click()
        self.logger.success(f"Clicked \"{name}\" at ({x}, {y})")
        self.target_updated.emit(target_id)

    def _on_click_failed(self, target_id: str, error: str) -> None:
        target = self.get_target(target_id)
        name = target.name if target else target_id
        self.logger.error(f"Click failed for \"{name}\": {error}")
        if "failsafe" in error.lower():
            self.emergency_stop()
        else:
            self._set_state(AutomationState.ERROR)

    def _on_error(self, message: str) -> None:
        self.logger.error(message)
        self._set_state(AutomationState.ERROR)
