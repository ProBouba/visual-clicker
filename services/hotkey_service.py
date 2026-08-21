"""Global hotkey service using the `keyboard` library.

`keyboard` hooks at the OS level on Windows and works even when the app
window isn't focused, which is required for a global emergency-stop hotkey.
On Windows this generally does NOT require admin rights for a normal desktop
session, but some elevated/foreground applications (e.g. games running as
admin) can block a non-elevated hook from receiving their input. We surface
that possibility via a warning rather than silently failing.

`keyboard` callbacks fire on its own internal listener thread, never the Qt
GUI thread — so we bounce everything through a Qt Signal (thread-safe queued
connection) instead of calling into the GUI directly from the hotkey thread.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

try:
    import keyboard as kb
    HOTKEYS_AVAILABLE = True
except Exception:
    kb = None
    HOTKEYS_AVAILABLE = False


class HotkeyService(QObject):
    start_pause_triggered = Signal()
    stop_triggered = Signal()
    emergency_stop_triggered = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._registered_hotkeys: list[str] = []
        self.active = False

    def register(self, start_pause_key: str, stop_key: str, emergency_key: str) -> Optional[str]:
        """Returns an error message string on failure, or None on success."""
        if not HOTKEYS_AVAILABLE:
            return "The 'keyboard' library is unavailable (missing dependency or insufficient OS permissions)."

        self.unregister_all()
        try:
            kb.add_hotkey(start_pause_key, lambda: self.start_pause_triggered.emit())
            self._registered_hotkeys.append(start_pause_key)

            kb.add_hotkey(stop_key, lambda: self.stop_triggered.emit())
            self._registered_hotkeys.append(stop_key)

            kb.add_hotkey(emergency_key, lambda: self.emergency_stop_triggered.emit())
            self._registered_hotkeys.append(emergency_key)

            self.active = True
            return None
        except Exception as exc:
            self.unregister_all()
            return f"Failed to register global hotkeys: {exc}"

    def unregister_all(self) -> None:
        if not HOTKEYS_AVAILABLE:
            return
        for key in self._registered_hotkeys:
            try:
                kb.remove_hotkey(key)
            except (KeyError, ValueError):
                pass
        self._registered_hotkeys.clear()
        self.active = False
