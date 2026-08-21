"""Logging service.

Writes to a rotating log file on disk AND keeps a small in-memory ring buffer
that the GUI's LogWidget subscribes to via a Qt signal, so the UI never has to
poll or read the log file back.
"""
from __future__ import annotations

import logging
import logging.handlers
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Deque

from PySide6.QtCore import QObject, Signal


class LogLevel(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class LogEntry:
    timestamp: str
    level: LogLevel
    message: str


class LoggingService(QObject):
    entry_logged = Signal(object)  # emits LogEntry

    def __init__(self, logs_dir: str | Path, max_buffer: int = 500) -> None:
        super().__init__()
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.buffer: Deque[LogEntry] = deque(maxlen=max_buffer)

        self._logger = logging.getLogger("visual_clicker")
        self._logger.setLevel(logging.DEBUG)
        self._logger.handlers.clear()

        handler = logging.handlers.RotatingFileHandler(
            self.logs_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        self._logger.addHandler(handler)

    def _emit(self, level: LogLevel, message: str) -> None:
        import datetime

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        entry = LogEntry(timestamp=ts, level=level, message=message)
        self.buffer.append(entry)
        self.entry_logged.emit(entry)

        py_level = {
            LogLevel.INFO: logging.INFO,
            LogLevel.SUCCESS: logging.INFO,
            LogLevel.WARNING: logging.WARNING,
            LogLevel.ERROR: logging.ERROR,
        }[level]
        self._logger.log(py_level, message)

    def info(self, message: str) -> None:
        self._emit(LogLevel.INFO, message)

    def success(self, message: str) -> None:
        self._emit(LogLevel.SUCCESS, message)

    def warning(self, message: str) -> None:
        self._emit(LogLevel.WARNING, message)

    def error(self, message: str) -> None:
        self._emit(LogLevel.ERROR, message)
