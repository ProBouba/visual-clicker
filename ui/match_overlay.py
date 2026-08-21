"""MatchOverlay — a transparent, click-through, always-on-top widget that briefly
flashes a highlight rectangle around the location of the last detection.

Purely visual/diagnostic: makes it immediately obvious *where* the engine
thinks it found a match, instead of only reading coordinates out of the log.
Click-through (WA_TransparentForMouseEvents) so it never interferes with the
automation actually clicking through to the target application underneath.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class MatchOverlay(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        geo = QGuiApplication.primaryScreen().virtualGeometry()
        self.setGeometry(geo)
        self._origin = geo.topLeft()

        self._rect: QRect = QRect()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def flash(self, global_x: int, global_y: int, width: int, height: int, duration_ms: int = 450) -> None:
        # Coordinates arrive in physical pixels (same space as mss/PyAutoGUI); this
        # widget's own geometry is in Qt logical pixels, so convert back down using
        # the primary screen's device pixel ratio for visual alignment.
        dpr = QGuiApplication.primaryScreen().devicePixelRatio() or 1.0
        local_x = (global_x / dpr) - self._origin.x()
        local_y = (global_y / dpr) - self._origin.y()
        self._rect = QRect(int(local_x), int(local_y), max(1, int(width / dpr)), max(1, int(height / dpr)))
        self.show()
        self.update()
        self._hide_timer.start(duration_ms)

    def paintEvent(self, event) -> None:
        if self._rect.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(0, 255, 120), 3)
        painter.setPen(pen)
        painter.drawRect(self._rect)
