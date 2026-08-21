"""LogWidget — live activity log, color-coded by level."""
from __future__ import annotations

from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

from services.logging_service import LogEntry, LogLevel

LEVEL_COLORS = {
    LogLevel.INFO: "#d4d4d4",
    LogLevel.SUCCESS: "#4caf50",
    LogLevel.WARNING: "#ffb300",
    LogLevel.ERROR: "#f44336",
}


class LogWidget(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)
        self.setStyleSheet("background-color: #141414; color: #d4d4d4; font-family: Consolas, monospace; font-size: 11px;")

    def append_entry(self, entry: LogEntry) -> None:
        color = LEVEL_COLORS.get(entry.level, "#d4d4d4")
        html = f'<span style="color:#888">[{entry.timestamp}]</span> <span style="color:{color}">{self._escape(entry.message)}</span>'
        self.appendHtml(html)
        self.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
