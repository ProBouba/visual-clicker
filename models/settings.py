"""AppSettings dataclass."""
from __future__ import annotations

from dataclasses import dataclass, asdict


class MonitoringSpeed:
    LOW = "low"        # 1500 ms
    NORMAL = "normal"  # 500 ms
    HIGH = "high"       # 150 ms
    CUSTOM = "custom"


SPEED_INTERVALS_MS = {
    MonitoringSpeed.LOW: 1500,
    MonitoringSpeed.NORMAL: 500,
    MonitoringSpeed.HIGH: 150,
}


@dataclass
class AppSettings:
    monitoring_speed: str = MonitoringSpeed.NORMAL
    custom_interval_ms: int = 500
    hotkeys_enabled: bool = True
    hotkey_start_pause: str = "f9"
    hotkey_stop: str = "f10"
    hotkey_emergency_stop: str = "f12"
    last_profile: str = "default"
    window_geometry: str = ""  # base64 QByteArray, optional

    # Adaptive interval: when nothing has been detected for a while, back off
    # the polling interval (up to adaptive_max_interval_ms) to save CPU; snap
    # back to the configured base interval the instant something IS detected.
    adaptive_interval_enabled: bool = False
    adaptive_idle_cycles_before_backoff: int = 20
    adaptive_max_interval_ms: int = 3000

    def effective_interval_ms(self) -> int:
        if self.monitoring_speed == MonitoringSpeed.CUSTOM:
            return max(30, self.custom_interval_ms)
        return SPEED_INTERVALS_MS.get(self.monitoring_speed, 500)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "AppSettings":
        valid = set(AppSettings.__dataclass_fields__.keys())
        return AppSettings(**{k: v for k, v in d.items() if k in valid})
