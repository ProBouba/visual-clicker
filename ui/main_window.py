"""MainWindow — top-level application window.

Wires together the TargetTable, dashboard, log widget, and global controls,
delegating all actual automation logic to AutomationManager. This class
should stay "thin": UI event handling + calling into the manager, no
detection/click/persistence logic of its own.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import List, Optional

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QStatusBar, QVBoxLayout, QWidget
)

from core.automation_manager import AutomationManager, AutomationState
from core.persistence import ConfigLoadError
from models.target import Target
from services.hotkey_service import HotkeyService
from ui.bulk_edit_dialog import BulkEditDialog
from ui.log_widget import LogWidget
from ui.match_overlay import MatchOverlay
from ui.overlay import ScreenshotSelectionOverlay, SelectionResult
from ui.settings_dialog import SettingsDialog
from ui.target_editor import TargetEditor
from ui.target_table import TargetTable
from ui.test_dialog import TestTargetDialog

STATE_COLORS = {
    AutomationState.STOPPED: "#888888",
    AutomationState.RUNNING: "#4caf50",
    AutomationState.PAUSED: "#ffb300",
    AutomationState.ERROR: "#f44336",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Visual Target Clicker")
        self.resize(1100, 700)

        self.manager = AutomationManager(base_dir=".", profile="default")
        self.hotkeys = HotkeyService()
        self._pending_overlay: Optional[ScreenshotSelectionOverlay] = None
        self._record_mode_active = False

        self.match_overlay = MatchOverlay()

        self._build_ui()
        self._connect_signals()
        self._apply_hotkeys()

        self.table.refresh(self.manager.targets)
        self._on_trash_changed()

        self._dashboard_timer = QTimer(self)
        self._dashboard_timer.timeout.connect(self._refresh_dashboard)
        self._dashboard_timer.start(1000)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_global_controls())

        splitter = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.addWidget(self._build_target_actions())
        self.table = TargetTable()
        top_layout.addWidget(self.table)
        splitter.addWidget(top)

        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.addWidget(self._build_dashboard())
        self.log_widget = LogWidget()
        log_box = QGroupBox("Activity Log")
        log_layout = QVBoxLayout(log_box)
        log_layout.addWidget(self.log_widget)
        bottom.addWidget(log_box)
        bottom.setSizes([350, 650])
        splitter.addWidget(bottom)

        splitter.setSizes([450, 250])
        root.addWidget(splitter)

        self.setStatusBar(QStatusBar())

    def _build_global_controls(self) -> QWidget:
        box = QGroupBox("Automation Control")
        layout = QHBoxLayout(box)

        self.start_btn = QPushButton("▶ Start")
        self.start_btn.clicked.connect(self._on_start)
        layout.addWidget(self.start_btn)

        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.clicked.connect(self._on_pause_resume)
        layout.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.clicked.connect(self._on_stop)
        layout.addWidget(self.stop_btn)

        self.preview_btn = QPushButton("👁 Preview Detection (no clicks)")
        self.preview_btn.clicked.connect(self._on_preview)
        layout.addWidget(self.preview_btn)

        layout.addStretch()

        self.emergency_btn = QPushButton("🛑 EMERGENCY STOP")
        self.emergency_btn.setStyleSheet(
            "background-color: #b71c1c; color: white; font-weight: bold; padding: 6px 14px;"
        )
        self.emergency_btn.clicked.connect(self._on_emergency_stop)
        layout.addWidget(self.emergency_btn)

        self.state_label = QLabel("● STOPPED")
        self.state_label.setStyleSheet(f"color: {STATE_COLORS[AutomationState.STOPPED]}; font-weight: bold;")
        layout.addWidget(self.state_label)

        settings_btn = QPushButton("⚙ Settings")
        settings_btn.clicked.connect(self._on_open_settings)
        layout.addWidget(settings_btn)

        return box

    def _build_target_actions(self) -> QWidget:
        box = QGroupBox("Targets")
        layout = QHBoxLayout(box)

        def add_btn(label: str, slot) -> QPushButton:
            b = QPushButton(label)
            b.clicked.connect(slot)
            layout.addWidget(b)
            return b

        add_btn("Add Target", self._on_add_target)
        add_btn("Record Mode…", self._on_record_mode)
        add_btn("Edit", self._on_edit_target)
        add_btn("Edit All…", self._on_edit_all_targets)
        add_btn("Rename", self._on_rename_target)
        add_btn("Duplicate", self._on_duplicate_target)
        add_btn("Delete", self._on_delete_target)
        self.undo_delete_btn = add_btn("Undo Delete", self._on_undo_delete)
        self.undo_delete_btn.setEnabled(False)
        add_btn("Test", self._on_test_target)
        add_btn("Move Up", lambda: self._on_move_target(-1))
        add_btn("Move Down", lambda: self._on_move_target(1))
        layout.addStretch()
        add_btn("Export Profile…", self._on_export_profile)
        add_btn("Import Profile…", self._on_import_profile)

        return box

    def _build_dashboard(self) -> QWidget:
        box = QGroupBox("Monitoring Dashboard")
        layout = QVBoxLayout(box)

        self.dash_status = QLabel()
        self.dash_targets = QLabel()
        self.dash_dpm = QLabel()
        self.dash_clicks = QLabel()
        self.dash_last_target = QLabel()
        self.dash_last_click = QLabel()
        self.dash_last_conf = QLabel()
        self.dash_interval = QLabel()

        for w in (
            self.dash_status, self.dash_targets, self.dash_dpm, self.dash_clicks,
            self.dash_last_target, self.dash_last_click, self.dash_last_conf, self.dash_interval,
        ):
            w.setStyleSheet("font-family: Consolas, monospace;")
            layout.addWidget(w)
        layout.addStretch()

        self._refresh_dashboard()
        return box

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.manager.logger.entry_logged.connect(self.log_widget.append_entry)
        self.manager.state_changed.connect(self._on_state_changed)
        self.manager.target_updated.connect(self._on_target_updated)
        self.manager.stats_updated.connect(self._refresh_dashboard)
        self.manager.match_visualized.connect(self.match_overlay.flash)
        self.manager.trash_changed.connect(self._on_trash_changed)

        self.table.target_enabled_toggled.connect(self._on_target_enabled_toggled)

        self.hotkeys.start_pause_triggered.connect(self._on_start_pause_hotkey)
        self.hotkeys.stop_triggered.connect(self._on_stop)
        self.hotkeys.emergency_stop_triggered.connect(self._on_emergency_stop)

    def _apply_hotkeys(self) -> None:
        s = self.manager.app_settings
        if not s.hotkeys_enabled:
            self.hotkeys.unregister_all()
            return
        error = self.hotkeys.register(s.hotkey_start_pause, s.hotkey_stop, s.hotkey_emergency_stop)
        if error:
            self.manager.logger.warning(f"Hotkeys unavailable: {error}")

    # ------------------------------------------------------------------
    # Global control handlers
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        if not self.manager.targets:
            QMessageBox.information(self, "No targets", "Add at least one target before starting automation.")
            return
        self.manager.start(preview_only=False)

    def _on_preview(self) -> None:
        if not self.manager.targets:
            QMessageBox.information(self, "No targets", "Add at least one target before previewing detection.")
            return
        self.manager.start(preview_only=True)

    def _on_pause_resume(self) -> None:
        if self.manager.state == AutomationState.RUNNING:
            self.manager.pause()
        elif self.manager.state == AutomationState.PAUSED:
            self.manager.resume()

    def _on_start_pause_hotkey(self) -> None:
        if self.manager.state in (AutomationState.STOPPED,):
            self._on_start()
        else:
            self._on_pause_resume()

    def _on_stop(self) -> None:
        self.manager.stop()

    def _on_emergency_stop(self) -> None:
        self.manager.emergency_stop()

    def _on_state_changed(self, state_value: str) -> None:
        state = AutomationState(state_value)
        self.state_label.setText(f"● {state.value.upper()}")
        self.state_label.setStyleSheet(f"color: {STATE_COLORS[state]}; font-weight: bold;")
        self.pause_btn.setText("▶ Resume" if state == AutomationState.PAUSED else "⏸ Pause")

    def _on_target_updated(self, target_id: str) -> None:
        target = self.manager.get_target(target_id)
        if target:
            self.table.update_row(target)

    def _on_open_settings(self) -> None:
        dlg = SettingsDialog(self.manager.app_settings, self)
        if dlg.exec():
            self.manager.save()
            self._apply_hotkeys()
            if self.manager.worker is not None:
                self.manager.worker.set_interval_ms(self.manager.app_settings.effective_interval_ms())
                self.manager.worker.adaptive_enabled = self.manager.app_settings.adaptive_interval_enabled
                self.manager.worker.adaptive_idle_cycles = self.manager.app_settings.adaptive_idle_cycles_before_backoff
                self.manager.worker.adaptive_max_interval_ms = self.manager.app_settings.adaptive_max_interval_ms

    def _refresh_dashboard(self) -> None:
        m = self.manager
        enabled_count = sum(1 for t in m.targets if t.enabled)
        self.dash_status.setText(f"Status:          {m.state.value.upper()}")
        self.dash_targets.setText(f"Targets:         {len(m.targets)} total, {enabled_count} enabled")
        self.dash_dpm.setText(f"Detections/min:  {m.stats.detections_this_minute}")
        self.dash_clicks.setText(f"Total clicks:    {m.stats.total_clicks}")
        self.dash_last_target.setText(f"Last detected:   {m.stats.last_target_name}")
        self.dash_last_click.setText(f"Last click time: {m.stats.last_click_time}")
        self.dash_last_conf.setText(f"Last confidence: {m.stats.last_confidence:.2f}")
        self.dash_interval.setText(f"Interval:        {m.app_settings.effective_interval_ms()} ms")

    # ------------------------------------------------------------------
    # Target management handlers
    # ------------------------------------------------------------------

    def _selected_target(self) -> Optional[Target]:
        tid = self.table.selected_target_id()
        return self.manager.get_target(tid) if tid else None

    def _selected_targets(self) -> List[Target]:
        ids = self.table.selected_target_ids()
        return [t for tid in ids if (t := self.manager.get_target(tid)) is not None]

    def _on_add_target(self) -> None:
        self.hide()
        overlay = ScreenshotSelectionOverlay(capture_image=True)
        self._pending_overlay = overlay
        overlay.selection_made.connect(self._on_new_target_captured)
        overlay.selection_cancelled.connect(self.show)
        overlay.show()

    def _on_new_target_captured(self, result: SelectionResult) -> None:
        self.show()
        target = Target(id=str(uuid.uuid4()), name="New Target")
        path = self.manager.config.screenshot_path_for(target.id)
        cv2.imwrite(str(path), result.image_bgr)
        target.template_path = str(path)

        editor = TargetEditor(target, self.manager.config, self.manager.targets, self)
        if editor.exec():
            self.manager.add_target(editor.result_target())
            self.table.refresh(self.manager.targets)
        else:
            # cancelled — clean up the orphaned screenshot file
            Path(path).unlink(missing_ok=True)

    def _on_record_mode(self) -> None:
        """Capture several targets back-to-back without reopening the picker each time.
        Each capture opens a quick name prompt (not the full editor) so the flow stays
        fast; use 'Edit' afterward to fine-tune any individual target. Press Escape on
        the selection overlay to stop recording."""
        self._record_mode_active = True
        self._start_record_capture()

    def _start_record_capture(self) -> None:
        if not self._record_mode_active:
            return
        self.hide()
        overlay = ScreenshotSelectionOverlay(capture_image=True)
        self._pending_overlay = overlay
        overlay.selection_made.connect(self._on_record_capture_made)
        overlay.selection_cancelled.connect(self._on_record_mode_stopped)
        overlay.show()

    def _on_record_capture_made(self, result: SelectionResult) -> None:
        self.show()
        count = sum(1 for t in self.manager.targets if t.name.startswith("Recorded Target"))
        default_name = f"Recorded Target {count + 1}"
        name, ok = QInputDialog.getText(self, "Name This Target", "Target name:", text=default_name)
        if ok and name.strip():
            target = Target(id=str(uuid.uuid4()), name=name.strip())
            path = self.manager.config.screenshot_path_for(target.id)
            cv2.imwrite(str(path), result.image_bgr)
            target.template_path = str(path)
            self.manager.add_target(target)
            self.table.refresh(self.manager.targets)

        if self._record_mode_active:
            self._start_record_capture()

    def _on_record_mode_stopped(self) -> None:
        self._record_mode_active = False
        self.show()

    def _on_edit_target(self) -> None:
        targets = self._selected_targets()
        if not targets:
            return
        if len(targets) == 1:
            self._edit_single_target(targets[0])
        else:
            self._edit_multiple_targets(targets)

    def _on_edit_all_targets(self) -> None:
        if not self.manager.targets:
            QMessageBox.information(self, "No targets", "There are no targets to edit.")
            return
        self._edit_multiple_targets(list(self.manager.targets))

    def _edit_single_target(self, target: Target) -> None:
        editor = TargetEditor(target, self.manager.config, self.manager.targets, self)
        if editor.exec():
            self.manager.update_target(editor.result_target())
            self.table.refresh(self.manager.targets)

    def _edit_multiple_targets(self, targets: List[Target]) -> None:
        import copy
        working_copies = [copy.deepcopy(t) for t in targets]
        dlg = BulkEditDialog(working_copies, self)
        if dlg.exec():
            edited = dlg.apply_to_targets()
            for t in edited:
                self.manager.update_target(t)
            self.table.refresh(self.manager.targets)

    def _on_rename_target(self) -> None:
        target = self._selected_target()
        if not target:
            return
        new_name, ok = QInputDialog.getText(self, "Rename Target", "New name:", text=target.name)
        if ok and new_name.strip():
            target.name = new_name.strip()
            self.manager.update_target(target)
            self.table.refresh(self.manager.targets)

    def _on_duplicate_target(self) -> None:
        target = self._selected_target()
        if not target:
            return
        import copy
        new_target = copy.deepcopy(target)
        new_target.id = str(uuid.uuid4())
        new_target.name = f"{target.name} (copy)"
        new_target.click_count = 0
        new_target.detection_count = 0
        new_target.last_detection_ts = 0.0
        new_target.last_click_ts = 0.0
        new_target.confidence_history = []

        if target.template_path and Path(target.template_path).exists():
            new_path = self.manager.config.screenshot_path_for(new_target.id)
            new_path.write_bytes(Path(target.template_path).read_bytes())
            new_target.template_path = str(new_path)

        self.manager.add_target(new_target)
        self.table.refresh(self.manager.targets)

    def _on_delete_target(self) -> None:
        targets = self._selected_targets()
        if not targets:
            return
        names = ", ".join(t.name for t in targets)
        reply = QMessageBox.question(
            self, "Delete Target(s)",
            f"Delete {len(targets)} target(s): {names}?\nYou can undo this with 'Undo Delete' until the trash fills up."
        )
        if reply == QMessageBox.StandardButton.Yes:
            for t in targets:
                self.manager.delete_target(t.id)
            self.table.refresh(self.manager.targets)

    def _on_undo_delete(self) -> None:
        restored = self.manager.undo_delete()
        if restored:
            self.table.refresh(self.manager.targets)

    def _on_trash_changed(self) -> None:
        self.undo_delete_btn.setEnabled(len(self.manager.trash_items()) > 0)

    def _on_test_target(self) -> None:
        target = self._selected_target()
        if not target:
            return
        dlg = TestTargetDialog(target, self)
        dlg.exec()

    def _on_move_target(self, direction: int) -> None:
        target = self._selected_target()
        if not target:
            return
        self.manager.move_target(target.id, direction)
        self.table.refresh(self.manager.targets)

    def _on_target_enabled_toggled(self, target_id: str, enabled: bool) -> None:
        target = self.manager.get_target(target_id)
        if target:
            target.enabled = enabled
            self.manager.update_target(target)

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------

    def _on_export_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Profile", "profile.zip", "Zip files (*.zip)")
        if path:
            try:
                self.manager.config.export_profile(self.manager.profile, path)
                QMessageBox.information(self, "Exported", f"Profile exported to {path}")
            except Exception as exc:
                QMessageBox.critical(self, "Export failed", str(exc))

    def _on_import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Profile", "", "Zip files (*.zip)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "Import Profile", "New profile name:", text="imported")
        if not ok or not name.strip():
            return
        try:
            count = self.manager.config.import_profile(path, name.strip())
            QMessageBox.information(self, "Imported", f"Imported {count} target(s) into profile '{name.strip()}'.")
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self.manager.stop()
        self.hotkeys.unregister_all()
        self.match_overlay.close()
        self.manager.save()
        super().closeEvent(event)
