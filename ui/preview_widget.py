"""PreviewWidget — renders a target's saved template image as a thumbnail."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel


class PreviewWidget(QLabel):
    def __init__(self, size: int = 64, parent=None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: #1e1e1e; border: 1px solid #3a3a3a; border-radius: 4px;")
        self.set_image(None)

    def set_image(self, path: Optional[str]) -> None:
        if path and Path(path).exists():
            pix = QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(
                    self._size - 4, self._size - 4,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.setPixmap(scaled)
                return
        self.setPixmap(QPixmap())
        self.setText("—")
