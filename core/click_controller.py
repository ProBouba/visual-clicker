"""Click controller — translates a detection into an actual mouse action.

Isolated behind a thin interface so the detection engine never talks to
pyautogui directly, and so click execution can be swapped out/mocked
(e.g. for the "Preview Detection" mode, which must never actually click).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

import pyautogui

from models.target import ClickType, Target

# Safety: don't let a runaway automation loop take over the mouse with zero
# ability to abort. pyautogui's FAILSAFE throws if the mouse is slammed into
# a screen corner, which doubles as a manual physical emergency stop.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0  # we manage our own delays explicitly, don't want pyautogui's implicit 0.1s tax


@dataclass
class ClickResult:
    success: bool
    x: int
    y: int
    error: str = ""


class ClickController:
    def click_for_target(self, target: Target, match_x: int, match_y: int) -> ClickResult:
        """match_x/match_y = center of the detected match, in global screen coordinates."""
        x = match_x + target.click_offset_x
        y = match_y + target.click_offset_y

        if target.randomize_offset and target.randomize_radius_px > 0:
            r = target.randomize_radius_px
            x += random.randint(-r, r)
            y += random.randint(-r, r)

        try:
            if target.delay_before_click_ms > 0:
                time.sleep(target.delay_before_click_ms / 1000.0)

            if target.click_type == ClickType.LEFT:
                pyautogui.click(x=x, y=y, button="left")
            elif target.click_type == ClickType.RIGHT:
                pyautogui.click(x=x, y=y, button="right")
            elif target.click_type == ClickType.DOUBLE:
                pyautogui.doubleClick(x=x, y=y, button="left")

            if target.delay_after_click_ms > 0:
                time.sleep(target.delay_after_click_ms / 1000.0)

            return ClickResult(success=True, x=x, y=y)
        except pyautogui.FailSafeException:
            return ClickResult(success=False, x=x, y=y, error="Emergency failsafe triggered (mouse in corner)")
        except Exception as exc:  # pyautogui can raise various OS-level errors
            return ClickResult(success=False, x=x, y=y, error=str(exc))
