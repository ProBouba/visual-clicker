"""TestTargetDialog — one-shot 'Test Target' check, shows found/confidence/coords."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from core.detection_engine import DetectionEngine
from models.target import Target
from services.screen_capture import ScreenCaptureService


class TestTargetDialog(QDialog):
    def __init__(self, target: Target, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Test Detection — {target.name}")
        self.setMinimumWidth(360)
        self.target = target

        layout = QVBoxLayout(self)
        self.result_label = QLabel("Running detection…")
        layout.addWidget(self.result_label)

        rerun_btn = QPushButton("Test Again")
        rerun_btn.clicked.connect(self._run_test)
        layout.addWidget(rerun_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._run_test()

    def _run_test(self) -> None:
        capture = ScreenCaptureService()
        try:
            engine = DetectionEngine(capture)
            match, (gx, gy) = engine.test_target(self.target)
        finally:
            capture.close()

        if match.found:
            self.result_label.setText(
                f"✅ Found\nConfidence: {match.confidence:.3f} (threshold {self.target.confidence_threshold:.2f})\n"
                f"Position: ({gx}, {gy})\nSize: {match.width} x {match.height}"
            )
        else:
            self.result_label.setText(
                f"❌ Not found\nBest confidence: {match.confidence:.3f} (threshold {self.target.confidence_threshold:.2f})\n"
                "Try lowering the confidence threshold or re-capturing the target."
            )
