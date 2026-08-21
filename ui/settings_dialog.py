"""SettingsDialog — app-wide preferences (monitoring speed, hotkeys)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox, QWidget
)

from models.settings import AppSettings, MonitoringSpeed


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)
        self.settings = settings

        form = QFormLayout(self)

        self.speed_combo = QComboBox()
        self.speed_combo.addItems([MonitoringSpeed.LOW, MonitoringSpeed.NORMAL, MonitoringSpeed.HIGH, MonitoringSpeed.CUSTOM])
        self.speed_combo.setCurrentText(settings.monitoring_speed)
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)
        form.addRow("Monitoring speed:", self.speed_combo)

        self.custom_interval_spin = QSpinBox()
        self.custom_interval_spin.setRange(30, 10000)
        self.custom_interval_spin.setSuffix(" ms")
        self.custom_interval_spin.setValue(settings.custom_interval_ms)
        self.custom_interval_spin.setEnabled(settings.monitoring_speed == MonitoringSpeed.CUSTOM)
        form.addRow("Custom interval:", self.custom_interval_spin)

        self.hotkeys_check = QCheckBox("Enable global hotkeys")
        self.hotkeys_check.setChecked(settings.hotkeys_enabled)
        form.addRow("", self.hotkeys_check)

        self.start_pause_edit = QLineEdit(settings.hotkey_start_pause)
        form.addRow("Start/Pause hotkey:", self.start_pause_edit)

        self.stop_edit = QLineEdit(settings.hotkey_stop)
        form.addRow("Stop hotkey:", self.stop_edit)

        self.emergency_edit = QLineEdit(settings.hotkey_emergency_stop)
        form.addRow("Emergency Stop hotkey:", self.emergency_edit)

        self.adaptive_check = QCheckBox("Adaptive interval (slow down polling when nothing's detected)")
        self.adaptive_check.setChecked(settings.adaptive_interval_enabled)
        form.addRow("", self.adaptive_check)

        self.adaptive_idle_spin = QSpinBox()
        self.adaptive_idle_spin.setRange(1, 500)
        self.adaptive_idle_spin.setValue(settings.adaptive_idle_cycles_before_backoff)
        form.addRow("Idle cycles before backoff:", self.adaptive_idle_spin)

        self.adaptive_max_spin = QSpinBox()
        self.adaptive_max_spin.setRange(100, 30000)
        self.adaptive_max_spin.setSuffix(" ms")
        self.adaptive_max_spin.setValue(settings.adaptive_max_interval_ms)
        form.addRow("Max backed-off interval:", self.adaptive_max_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_speed_changed(self, text: str) -> None:
        self.custom_interval_spin.setEnabled(text == MonitoringSpeed.CUSTOM)

    def _on_accept(self) -> None:
        self.settings.monitoring_speed = self.speed_combo.currentText()
        self.settings.custom_interval_ms = self.custom_interval_spin.value()
        self.settings.hotkeys_enabled = self.hotkeys_check.isChecked()
        self.settings.hotkey_start_pause = self.start_pause_edit.text().strip() or "f9"
        self.settings.hotkey_stop = self.stop_edit.text().strip() or "f10"
        self.settings.hotkey_emergency_stop = self.emergency_edit.text().strip() or "f12"
        self.settings.adaptive_interval_enabled = self.adaptive_check.isChecked()
        self.settings.adaptive_idle_cycles_before_backoff = self.adaptive_idle_spin.value()
        self.settings.adaptive_max_interval_ms = self.adaptive_max_spin.value()
        self.accept()
