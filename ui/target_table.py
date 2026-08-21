"""TargetTable — QTableWidget-based list of all configured targets."""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QWidget

from models.target import Target
from ui.preview_widget import PreviewWidget

COLUMNS = [
    "Preview", "Name", "Enabled", "Confidence", "Cooldown", "Priority",
    "State", "Clicks", "Last Detection", "Last Confidence",
]


class TargetTable(QTableWidget):
    target_enabled_toggled = Signal(str, bool)  # target_id, new_enabled_state
    selection_changed_id = Signal(str)           # target_id or "" if none

    def __init__(self, parent=None) -> None:
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setAlternatingRowColors(True)
        self.itemSelectionChanged.connect(self._on_selection_changed)

        self._row_by_id: dict[str, int] = {}

    def selected_target_id(self) -> Optional[str]:
        ids = self.selected_target_ids()
        return ids[0] if ids else None

    def selected_target_ids(self) -> list[str]:
        rows = sorted({idx.row() for idx in self.selectionModel().selectedRows()})
        by_row = {r: tid for tid, r in self._row_by_id.items()}
        return [by_row[r] for r in rows if r in by_row]

    def select_all_targets(self) -> None:
        self.selectAll()

    def _on_selection_changed(self) -> None:
        tid = self.selected_target_id()
        self.selection_changed_id.emit(tid or "")

    def refresh(self, targets: list[Target]) -> None:
        previously_selected = self.selected_target_id()

        self.setRowCount(0)
        self._row_by_id.clear()

        for target in sorted(targets, key=lambda t: t.order_index):
            self._append_row(target)

        if previously_selected and previously_selected in self._row_by_id:
            self.selectRow(self._row_by_id[previously_selected])

    def update_row(self, target: Target) -> None:
        row = self._row_by_id.get(target.id)
        if row is None:
            return
        self._fill_row(row, target)

    def _append_row(self, target: Target) -> None:
        row = self.rowCount()
        self.insertRow(row)
        self._row_by_id[target.id] = row
        self._fill_row(row, target)

    def _fill_row(self, row: int, target: Target) -> None:
        preview = PreviewWidget(size=40)
        preview.set_image(target.template_path)
        container = QWidget()
        lay = QHBoxLayout(container)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(preview)
        self.setCellWidget(row, 0, container)

        self.setItem(row, 1, QTableWidgetItem(target.name))

        check = QCheckBox()
        check.setChecked(target.enabled)
        check.stateChanged.connect(lambda state, tid=target.id: self.target_enabled_toggled.emit(tid, bool(state)))
        check_container = QWidget()
        check_lay = QHBoxLayout(check_container)
        check_lay.addWidget(check)
        check_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        check_lay.setContentsMargins(0, 0, 0, 0)
        self.setCellWidget(row, 2, check_container)

        self.setItem(row, 3, QTableWidgetItem(f"{target.confidence_threshold:.2f}"))
        self.setItem(row, 4, QTableWidgetItem(f"{target.cooldown_ms} ms"))
        self.setItem(row, 5, QTableWidgetItem(str(target.priority)))
        self.setItem(row, 6, QTableWidgetItem(target.state.value))
        self.setItem(row, 7, QTableWidgetItem(str(target.click_count)))

        last_det = "-"
        if target.last_detection_ts > 0:
            last_det = time.strftime("%H:%M:%S", time.localtime(target.last_detection_ts))
        self.setItem(row, 8, QTableWidgetItem(last_det))
        self.setItem(row, 9, QTableWidgetItem(f"{target.last_confidence:.2f}" if target.last_confidence else "-"))

        # store target id on the name item for convenient lookups
        self.item(row, 1).setData(Qt.ItemDataRole.UserRole, target.id)
