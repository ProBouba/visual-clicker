"""BulkEditDialog — edit a field across many targets at once.

Every field has its own "apply to selected" checkbox (unchecked by default),
so you can change, say, just the cooldown across 10 targets without having to
re-specify every other setting for all of them. Only checked fields are
written back to each target; everything else is left untouched.
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget
)

from models.target import ClickType, Target


class _Field:
    """Pairs an 'apply this?' checkbox with the actual input control."""

    def __init__(self, control: QWidget) -> None:
        self.enable_check = QCheckBox("Apply")
        self.control = control
        self.control.setEnabled(False)
        self.enable_check.toggled.connect(self.control.setEnabled)

    def row(self) -> QWidget:
        container = QWidget()
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.enable_check)
        lay.addWidget(self.control, 1)
        return container


class BulkEditDialog(QDialog):
    def __init__(self, targets: List[Target], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.targets = targets
        self.setWindowTitle(f"Edit {len(targets)} Target(s)")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Editing {len(targets)} target(s). Check 'Apply' next to any field you want to\n"
            "overwrite on all of them — unchecked fields are left as-is per target."
        ))

        form = QFormLayout()
        layout.addLayout(form)

        self.enabled_field = _Field(self._make_checkbox())
        form.addRow("Enabled:", self.enabled_field.row())

        self.confidence_field = _Field(self._make_confidence_spin())
        form.addRow("Confidence threshold:", self.confidence_field.row())

        self.grayscale_field = _Field(self._make_checkbox())
        form.addRow("Grayscale matching:", self.grayscale_field.row())

        self.multi_scale_field = _Field(self._make_checkbox())
        form.addRow("Multi-scale matching:", self.multi_scale_field.row())

        self.priority_field = _Field(self._make_int_spin(0, 100))
        form.addRow("Priority:", self.priority_field.row())

        self.cooldown_field = _Field(self._make_int_spin(0, 600000, " ms"))
        form.addRow("Cooldown:", self.cooldown_field.row())

        self.interval_field = _Field(self._make_int_spin(30, 10000, " ms"))
        form.addRow("Detection interval:", self.interval_field.row())

        self.max_clicks_field = _Field(self._make_int_spin(0, 100000))
        form.addRow("Max click count:", self.max_clicks_field.row())

        self.click_type_field = _Field(self._make_click_type_combo())
        form.addRow("Click type:", self.click_type_field.row())

        self.stop_after_click_field = _Field(self._make_checkbox())
        form.addRow("Disable target after click:", self.stop_after_click_field.row())

        self.delay_before_field = _Field(self._make_int_spin(0, 10000, " ms"))
        form.addRow("Delay before click:", self.delay_before_field.row())

        self.delay_after_field = _Field(self._make_int_spin(0, 10000, " ms"))
        form.addRow("Delay after click:", self.delay_after_field.row())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------

    @staticmethod
    def _make_checkbox() -> QCheckBox:
        return QCheckBox()

    @staticmethod
    def _make_confidence_spin() -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(0.10, 1.00)
        s.setSingleStep(0.01)
        s.setDecimals(2)
        s.setValue(0.85)
        return s

    @staticmethod
    def _make_int_spin(lo: int, hi: int, suffix: str = "") -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        if suffix:
            s.setSuffix(suffix)
        return s

    @staticmethod
    def _make_click_type_combo() -> QComboBox:
        c = QComboBox()
        c.addItems([ClickType.LEFT.value, ClickType.RIGHT.value, ClickType.DOUBLE.value])
        return c

    # ------------------------------------------------------------------

    def apply_to_targets(self) -> List[Target]:
        """Applies every checked field to every target in-place and returns the list
        so the caller can persist them."""
        for t in self.targets:
            if self.enabled_field.enable_check.isChecked():
                t.enabled = self.enabled_field.control.isChecked()
            if self.confidence_field.enable_check.isChecked():
                t.confidence_threshold = self.confidence_field.control.value()
            if self.grayscale_field.enable_check.isChecked():
                t.grayscale_matching = self.grayscale_field.control.isChecked()
            if self.multi_scale_field.enable_check.isChecked():
                t.multi_scale_matching = self.multi_scale_field.control.isChecked()
            if self.priority_field.enable_check.isChecked():
                t.priority = self.priority_field.control.value()
            if self.cooldown_field.enable_check.isChecked():
                t.cooldown_ms = self.cooldown_field.control.value()
            if self.interval_field.enable_check.isChecked():
                t.detection_interval_ms = self.interval_field.control.value()
            if self.max_clicks_field.enable_check.isChecked():
                t.max_click_count = self.max_clicks_field.control.value()
            if self.click_type_field.enable_check.isChecked():
                t.click_type = ClickType(self.click_type_field.control.currentText())
            if self.stop_after_click_field.enable_check.isChecked():
                t.stop_after_click = self.stop_after_click_field.control.isChecked()
            if self.delay_before_field.enable_check.isChecked():
                t.delay_before_click_ms = self.delay_before_field.control.value()
            if self.delay_after_field.enable_check.isChecked():
                t.delay_after_click_ms = self.delay_after_field.control.value()
        return self.targets
