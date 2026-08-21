"""ScreenshotSelectionOverlay — fullscreen transparent click-and-drag capture overlay.

Spans the full virtual desktop (the bounding box of ALL monitors combined),
using QGuiApplication.primaryScreen().virtualGeometry() so a monitor placed
above/left of the primary (i.e. with negative coordinates) is still fully
covered. The user drags a rectangle; on release we capture that exact region
via mss (not a Qt screenshot) so the captured pixels match 1:1 what the
detection engine will later see, avoiding any Qt-vs-OS color/DPI mismatch.

IMPORTANT — DPI scaling:
Qt reports widget geometry and mouse positions in *logical* pixels, but mss
(and the Win32 APIs it wraps, and PyAutoGUI's clicks) always work in
*physical* pixels. On any Windows display set to something other than 100%
scaling (125%, 150%, etc. are extremely common), 1 logical pixel != 1
physical pixel, so a selection rectangle drawn using raw Qt coordinates would
capture the wrong region — shifted and/or the wrong size versus what was
actually dragged on screen.

We fix this by multiplying every Qt-space coordinate by the active screen's
`devicePixelRatio` before it's used for capture or stored as global screen
coordinates, so everything downstream (mss capture, OpenCV matching,
PyAutoGUI clicks — which are all physical-pixel-based) stays consistent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QMouseEvent, QPainter, QPaintEvent, QKeyEvent, QPen
from PySide6.QtWidgets import QWidget

from services.screen_capture import ScreenCaptureService


@dataclass
class SelectionResult:
    image_bgr: np.ndarray
    global_x: int
    global_y: int
    width: int
    height: int


class ScreenshotSelectionOverlay(QWidget):
    selection_made = Signal(object)  # emits SelectionResult
    selection_cancelled = Signal()

    def __init__(self, parent: Optional[QWidget] = None, capture_image: bool = True) -> None:
        super().__init__(parent)
        self.capture_image = capture_image
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

        geo = QGuiApplication.primaryScreen().virtualGeometry()
        self.setGeometry(geo)
        self._virtual_origin = QPoint(geo.x(), geo.y())
        # Fallback scale if we can't resolve a specific screen at selection time
        # (e.g. selection somehow ends up off any screen). Per-monitor DPI is
        # resolved properly in _finish_selection() via QGuiApplication.screenAt().
        self._fallback_dpr = QGuiApplication.primaryScreen().devicePixelRatio()

        self._dragging = False
        self._start_pos: Optional[QPoint] = None
        self._current_pos: Optional[QPoint] = None
        self._capture = ScreenCaptureService()

        # See module docstring: without this, showing the overlay from inside
        # an already-running QDialog.exec() (e.g. from TargetEditor's "Replace
        # Screenshot" / "Set Search Region" buttons) leaves the overlay unable
        # to receive mouse/keyboard input, because Qt's application-modal block
        # from the still-running outer exec() only lets the current modal
        # window (or windows properly registered as modal) receive events.
        # Explicitly marking the overlay itself as application-modal registers
        # it in that same modal stack so it works correctly whether it's shown
        # from the main window (non-modal context) or from inside a modal
        # dialog.
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.grabKeyboard()

    def closeEvent(self, event) -> None:
        self.releaseKeyboard()
        self._capture.close()
        super().closeEvent(event)

    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_pos = event.position().toPoint()
            self._current_pos = self._start_pos
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._current_pos = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._current_pos = event.position().toPoint()
            self._finish_selection()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.releaseKeyboard()
            self.selection_cancelled.emit()
            self.close()

    # ------------------------------------------------------------------

    def _selection_rect_local(self) -> Optional[QRect]:
        if self._start_pos is None or self._current_pos is None:
            return None
        return QRect(self._start_pos, self._current_pos).normalized()

    def _finish_selection(self) -> None:
        rect = self._selection_rect_local()
        if rect is None or rect.width() < 4 or rect.height() < 4:
            self.selection_cancelled.emit()
            self.close()
            return

        # Local (widget-space, LOGICAL pixels) coords -> global virtual-desktop
        # coords, still logical.
        logical_x = self._virtual_origin.x() + rect.x()
        logical_y = self._virtual_origin.y() + rect.y()

        # Resolve the actual screen the selection was made on so we use its real
        # DPI scale factor (per-monitor DPI setups are common on Windows).
        screen = QGuiApplication.screenAt(self.mapToGlobal(rect.center()))
        dpr = screen.devicePixelRatio() if screen is not None else self._fallback_dpr

        # Convert logical -> physical pixels. This is the coordinate space mss,
        # OpenCV, and PyAutoGUI all actually operate in.
        global_x = round(logical_x * dpr)
        global_y = round(logical_y * dpr)
        phys_width = round(rect.width() * dpr)
        phys_height = round(rect.height() * dpr)

        # Hide before capturing so the overlay itself isn't captured in the screenshot.
        #
        # A single hide() + processEvents() is NOT reliably enough: on Windows,
        # the compositor (DWM) can take a frame or two to actually remove the
        # overlay's pixels from the screen, especially for a translucent
        # always-on-top window. Capturing too early grabs a frame where the
        # dimmed overlay (or a fade-out remnant of it) is still on screen,
        # which is exactly what shows up as "the screenshot doesn't match
        # what I selected". We hide, pump the event loop, and then wait a
        # short real amount of time before grabbing pixels.
        self.hide()
        for _ in range(3):
            QGuiApplication.processEvents()
        QThread.msleep(120)
        QGuiApplication.processEvents()

        try:
            if self.capture_image:
                img = self._capture.capture_region(global_x, global_y, phys_width, phys_height)
            else:
                img = np.zeros((1, 1, 3), dtype=np.uint8)  # placeholder; caller only wants the region geometry
            result = SelectionResult(
                image_bgr=img, global_x=global_x, global_y=global_y, width=phys_width, height=phys_height
            )
            self.selection_made.emit(result)
        finally:
            self.releaseKeyboard()
            self.close()

    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dim the whole virtual desktop
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        rect = self._selection_rect_local()
        if rect is not None:
            # Punch a "clear" hole where the selection is, so the user sees real pixels there
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.restore()

            pen = QPen(QColor(0, 200, 255), 2)
            painter.setPen(pen)
            painter.drawRect(rect)

            label = f"{rect.width()} x {rect.height()}"
            painter.setPen(QColor(255, 255, 255))
            font = QFont()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            label_pos = QPoint(rect.x(), max(0, rect.y() - 8))
            painter.drawText(label_pos, label)
        else:
            painter.setPen(QColor(255, 255, 255))
            font = QFont()
            font.setPointSize(14)
            painter.setFont(font)
            hint = "Drag to select an area  •  Esc to cancel"
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, hint)
