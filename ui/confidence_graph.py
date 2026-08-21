"""ConfidenceGraph — small sparkline showing a target's recent match confidence
history, with the confidence threshold drawn as a reference line. Helps spot a
target that's drifting toward unreliable before it silently stops matching."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class ConfidenceGraph(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(60)
        self._history: List[float] = []
        self._threshold: float = 0.85

    def set_data(self, history: List[float], threshold: float) -> None:
        self._history = history
        self._threshold = threshold
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(20, 20, 20))

        if not self._history:
            painter.setPen(QColor(120, 120, 120))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No detection data yet")
            return

        ty = h - int(self._threshold * h)
        painter.setPen(QPen(QColor(255, 179, 0), 1, Qt.PenStyle.DashLine))
        painter.drawLine(0, ty, w, ty)

        n = len(self._history)
        step = w / max(1, n - 1) if n > 1 else 0
        pen = QPen(QColor(0, 200, 255), 2)
        painter.setPen(pen)

        points = []
        for i, val in enumerate(self._history):
            x = int(i * step)
            y = h - int(max(0.0, min(1.0, val)) * h)
            points.append((x, y))

        for i in range(1, len(points)):
            painter.drawLine(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])

        last_val = self._history[-1]
        color = QColor(76, 175, 80) if last_val >= self._threshold else QColor(244, 67, 54)
        painter.setPen(color)
        painter.drawText(4, 12, f"{last_val:.2f}")
