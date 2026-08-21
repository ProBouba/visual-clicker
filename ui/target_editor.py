"""TargetEditor — full per-target configuration dialog.

Covers detection settings, click settings, and behavior settings as specified.
Screenshot (re)capture and search-region selection both delegate to the
ScreenshotSelectionOverlay so the exact same drag-select workflow is reused.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import List, Optional

import cv2
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget, QMessageBox
)

from core.persistence import ConfigManager
from models.target import ClickType, SearchRegion, Target
from ui.confidence_graph import ConfidenceGraph
from ui.overlay import ScreenshotSelectionOverlay, SelectionResult
from ui.preview_widget import PreviewWidget


class TargetEditor(QDialog):
    def __init__(
        self,
        target: Target,
        config: ConfigManager,
        all_targets: Optional[List[Target]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Configure Target — {target.name}")
        self.setMinimumWidth(520)
        self.config = config
        self.original_target = target
        self.target = copy.deepcopy(target)  # edit a copy; only commit on Accept
        # other targets, for the "requires this target also visible" condition dropdown —
        # excludes self so a target can never depend on itself.
        self.all_targets = [t for t in (all_targets or []) if t.id != target.id]
        self._pending_overlay: Optional[ScreenshotSelectionOverlay] = None

        self._build_ui()
        self._load_from_target()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.name_edit = QLineEdit()
        name_row.addWidget(self.name_edit)
        layout.addLayout(name_row)

        preview_row = QHBoxLayout()
        self.preview = PreviewWidget(size=96)
        preview_row.addWidget(self.preview)
        btn_col = QVBoxLayout()
        self.replace_btn = QPushButton("Replace Screenshot…")
        self.replace_btn.clicked.connect(self._on_replace_screenshot)
        btn_col.addWidget(self.replace_btn)
        preview_row.addLayout(btn_col)
        preview_row.addStretch()
        layout.addLayout(preview_row)

        tabs = QTabWidget()
        tabs.addTab(self._build_detection_tab(), "Detection")
        tabs.addTab(self._build_templates_tab(), "Templates")
        tabs.addTab(self._build_click_tab(), "Click")
        tabs.addTab(self._build_behavior_tab(), "Behavior")
        tabs.addTab(self._build_history_tab(), "History")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_detection_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.10, 1.00)
        self.confidence_spin.setSingleStep(0.01)
        self.confidence_spin.setDecimals(2)
        form.addRow("Confidence threshold:", self.confidence_spin)

        self.grayscale_check = QCheckBox("Match using grayscale (faster, ignores color)")
        form.addRow("", self.grayscale_check)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(30, 10000)
        self.interval_spin.setSuffix(" ms")
        form.addRow("Detection interval:", self.interval_spin)

        region_row = QHBoxLayout()
        self.region_label = QLabel("Full screen")
        region_row.addWidget(self.region_label)
        self.set_region_btn = QPushButton("Set Search Region…")
        self.set_region_btn.clicked.connect(self._on_set_search_region)
        region_row.addWidget(self.set_region_btn)
        self.clear_region_btn = QPushButton("Clear")
        self.clear_region_btn.clicked.connect(self._on_clear_search_region)
        region_row.addWidget(self.clear_region_btn)
        form.addRow("Search region:", region_row)

        exclusion_row = QHBoxLayout()
        self.exclusion_label = QLabel("None")
        exclusion_row.addWidget(self.exclusion_label)
        add_exclusion_btn = QPushButton("Add Exclusion Zone…")
        add_exclusion_btn.clicked.connect(self._on_add_exclusion)
        exclusion_row.addWidget(add_exclusion_btn)
        clear_exclusion_btn = QPushButton("Clear")
        clear_exclusion_btn.clicked.connect(self._on_clear_exclusions)
        exclusion_row.addWidget(clear_exclusion_btn)
        form.addRow("Exclusion zones:", exclusion_row)

        self.multi_scale_check = QCheckBox("Multi-scale matching (tries several sizes — more robust, slower)")
        form.addRow("", self.multi_scale_check)

        self.scale_range_spin = QSpinBox()
        self.scale_range_spin.setRange(5, 50)
        self.scale_range_spin.setSuffix(" %")
        form.addRow("Scale search range (+/-):", self.scale_range_spin)

        self.condition_combo = QComboBox()
        self.condition_combo.addItem("(none)", None)
        for t in self.all_targets:
            self.condition_combo.addItem(t.name, t.id)
        form.addRow("Only click if also visible:", self.condition_combo)

        note = QLabel(
            "Note: fixed-scale matching is exact but brittle to display-scaling changes.\n"
            "Enable multi-scale matching if this target keeps failing after Windows DPI\n"
            "or window-size changes, at the cost of extra CPU per detection cycle."
        )
        note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow(note)

        return w

    def _build_templates_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(
            "Extra screenshot samples of the same target (e.g. captured in different\n"
            "lighting or UI states). Detection matches against all of them and uses\n"
            "whichever scores highest — more robust than a single fixed template."
        ))

        self.templates_list = QListWidget()
        layout.addWidget(self.templates_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Sample…")
        add_btn.clicked.connect(self._on_add_template_sample)
        btn_row.addWidget(add_btn)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._on_remove_template_sample)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return w

    def _build_click_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.click_type_combo = QComboBox()
        self.click_type_combo.addItems([ClickType.LEFT.value, ClickType.RIGHT.value, ClickType.DOUBLE.value])
        form.addRow("Click type:", self.click_type_combo)

        offset_row = QHBoxLayout()
        self.offset_x_spin = QSpinBox()
        self.offset_x_spin.setRange(-2000, 2000)
        self.offset_y_spin = QSpinBox()
        self.offset_y_spin.setRange(-2000, 2000)
        offset_row.addWidget(QLabel("X:"))
        offset_row.addWidget(self.offset_x_spin)
        offset_row.addWidget(QLabel("Y:"))
        offset_row.addWidget(self.offset_y_spin)
        form.addRow("Click offset (from center):", offset_row)

        self.randomize_check = QCheckBox("Randomize click position")
        form.addRow("", self.randomize_check)

        self.randomize_radius_spin = QSpinBox()
        self.randomize_radius_spin.setRange(0, 100)
        self.randomize_radius_spin.setSuffix(" px")
        form.addRow("Randomize radius:", self.randomize_radius_spin)

        self.delay_before_spin = QSpinBox()
        self.delay_before_spin.setRange(0, 10000)
        self.delay_before_spin.setSuffix(" ms")
        form.addRow("Delay before click:", self.delay_before_spin)

        self.delay_after_spin = QSpinBox()
        self.delay_after_spin.setRange(0, 10000)
        self.delay_after_spin.setSuffix(" ms")
        form.addRow("Delay after click:", self.delay_after_spin)

        return w

    def _build_behavior_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.enabled_check = QCheckBox("Enabled")
        form.addRow("", self.enabled_check)

        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 100)
        form.addRow("Priority (higher = first):", self.priority_spin)

        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(0, 600000)
        self.cooldown_spin.setSuffix(" ms")
        form.addRow("Cooldown after click:", self.cooldown_spin)

        self.max_clicks_spin = QSpinBox()
        self.max_clicks_spin.setRange(0, 100000)
        self.max_clicks_spin.setSpecialValueText("Unlimited")
        form.addRow("Max click count:", self.max_clicks_spin)

        self.stop_after_click_check = QCheckBox("Disable target after first click")
        form.addRow("", self.stop_after_click_check)

        self.min_visible_spin = QSpinBox()
        self.min_visible_spin.setRange(0, 30000)
        self.min_visible_spin.setSuffix(" ms")
        form.addRow("Minimum visible duration before click:", self.min_visible_spin)

        return w

    def _build_history_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("Recent match confidence (dashed line = current threshold):"))
        self.confidence_graph = ConfidenceGraph()
        layout.addWidget(self.confidence_graph)
        stats_label = QLabel(
            f"Total detections: {self.target.detection_count}   "
            f"Total clicks: {self.target.click_count}   "
            f"Average confidence: {self.target.average_confidence:.2f}   "
            f"Failures: {self.target.failure_count}"
        )
        stats_label.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(stats_label)
        layout.addStretch()
        return w

    # ------------------------------------------------------------------

    def _load_from_target(self) -> None:
        t = self.target
        self.name_edit.setText(t.name)
        self.preview.set_image(t.template_path)

        self.confidence_spin.setValue(t.confidence_threshold)
        self.grayscale_check.setChecked(t.grayscale_matching)
        self.interval_spin.setValue(t.detection_interval_ms)
        self._refresh_region_label()
        self._refresh_exclusion_label()
        self.multi_scale_check.setChecked(t.multi_scale_matching)
        self.scale_range_spin.setValue(t.scale_range_percent)
        idx = self.condition_combo.findData(t.requires_target_id)
        self.condition_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._refresh_templates_list()
        self.confidence_graph.set_data(t.confidence_history, t.confidence_threshold)

        self.click_type_combo.setCurrentText(t.click_type.value)
        self.offset_x_spin.setValue(t.click_offset_x)
        self.offset_y_spin.setValue(t.click_offset_y)
        self.randomize_check.setChecked(t.randomize_offset)
        self.randomize_radius_spin.setValue(t.randomize_radius_px)
        self.delay_before_spin.setValue(t.delay_before_click_ms)
        self.delay_after_spin.setValue(t.delay_after_click_ms)

        self.enabled_check.setChecked(t.enabled)
        self.priority_spin.setValue(t.priority)
        self.cooldown_spin.setValue(t.cooldown_ms)
        self.max_clicks_spin.setValue(t.max_click_count)
        self.stop_after_click_check.setChecked(t.stop_after_click)
        self.min_visible_spin.setValue(t.min_visible_duration_ms)

    def _refresh_region_label(self) -> None:
        r = self.target.search_region
        self.region_label.setText(f"{r.width} x {r.height} @ ({r.x}, {r.y})" if r else "Full screen")

    def _refresh_exclusion_label(self) -> None:
        n = len(self.target.exclusion_regions)
        self.exclusion_label.setText("None" if n == 0 else f"{n} zone(s)")

    def _refresh_templates_list(self) -> None:
        self.templates_list.clear()
        for path in self.target.additional_template_paths:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.templates_list.addItem(item)

    # ------------------------------------------------------------------

    def _on_replace_screenshot(self) -> None:
        self.hide()
        overlay = ScreenshotSelectionOverlay(capture_image=True)
        self._pending_overlay = overlay
        overlay.selection_made.connect(self._on_new_screenshot_selected)
        overlay.selection_cancelled.connect(self.show)
        overlay.destroyed.connect(lambda: self.show() if self.isHidden() else None)
        overlay.show()

    def _on_new_screenshot_selected(self, result: SelectionResult) -> None:
        path = self.config.screenshot_path_for(self.target.id)
        cv2.imwrite(str(path), result.image_bgr)
        self.target.template_path = str(path)
        self.preview.set_image(str(path))
        self.show()

    def _on_set_search_region(self) -> None:
        self.hide()
        overlay = ScreenshotSelectionOverlay(capture_image=False)
        self._pending_overlay = overlay
        overlay.selection_made.connect(self._on_region_selected)
        overlay.selection_cancelled.connect(self.show)
        overlay.show()

    def _on_region_selected(self, result: SelectionResult) -> None:
        self.target.search_region = SearchRegion(x=result.global_x, y=result.global_y, width=result.width, height=result.height)
        self._refresh_region_label()
        self.show()

    def _on_clear_search_region(self) -> None:
        self.target.search_region = None
        self._refresh_region_label()

    def _on_add_exclusion(self) -> None:
        self.hide()
        overlay = ScreenshotSelectionOverlay(capture_image=False)
        self._pending_overlay = overlay
        overlay.selection_made.connect(self._on_exclusion_selected)
        overlay.selection_cancelled.connect(self.show)
        overlay.show()

    def _on_exclusion_selected(self, result: SelectionResult) -> None:
        self.target.exclusion_regions.append(
            SearchRegion(x=result.global_x, y=result.global_y, width=result.width, height=result.height)
        )
        self._refresh_exclusion_label()
        self.show()

    def _on_clear_exclusions(self) -> None:
        self.target.exclusion_regions.clear()
        self._refresh_exclusion_label()

    def _on_add_template_sample(self) -> None:
        self.hide()
        overlay = ScreenshotSelectionOverlay(capture_image=True)
        self._pending_overlay = overlay
        overlay.selection_made.connect(self._on_template_sample_selected)
        overlay.selection_cancelled.connect(self.show)
        overlay.show()

    def _on_template_sample_selected(self, result: SelectionResult) -> None:
        import uuid
        path = self.config.screenshots_dir / f"{self.target.id}_sample_{uuid.uuid4().hex[:8]}.png"
        cv2.imwrite(str(path), result.image_bgr)
        self.target.additional_template_paths.append(str(path))
        self._refresh_templates_list()
        self.show()

    def _on_remove_template_sample(self) -> None:
        item = self.templates_list.currentItem()
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path in self.target.additional_template_paths:
            self.target.additional_template_paths.remove(path)
            Path(path).unlink(missing_ok=True)
        self._refresh_templates_list()

    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Please give this target a name.")
            return
        if not self.target.template_path:
            QMessageBox.warning(self, "Missing screenshot", "This target has no screenshot captured yet.")
            return

        t = self.target
        t.name = name
        t.confidence_threshold = self.confidence_spin.value()
        t.grayscale_matching = self.grayscale_check.isChecked()
        t.detection_interval_ms = self.interval_spin.value()
        t.multi_scale_matching = self.multi_scale_check.isChecked()
        t.scale_range_percent = self.scale_range_spin.value()
        t.requires_target_id = self.condition_combo.currentData()

        t.click_type = ClickType(self.click_type_combo.currentText())
        t.click_offset_x = self.offset_x_spin.value()
        t.click_offset_y = self.offset_y_spin.value()
        t.randomize_offset = self.randomize_check.isChecked()
        t.randomize_radius_px = self.randomize_radius_spin.value()
        t.delay_before_click_ms = self.delay_before_spin.value()
        t.delay_after_click_ms = self.delay_after_spin.value()

        t.enabled = self.enabled_check.isChecked()
        t.priority = self.priority_spin.value()
        t.cooldown_ms = self.cooldown_spin.value()
        t.max_click_count = self.max_clicks_spin.value()
        t.stop_after_click = self.stop_after_click_check.isChecked()
        t.min_visible_duration_ms = self.min_visible_spin.value()

        self.accept()

    def result_target(self) -> Target:
        return self.target
