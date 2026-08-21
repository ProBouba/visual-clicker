"""Persistence layer.

Handles loading/saving targets + app settings to JSON, and manages the
screenshots/ directory so template images are stored as separate PNG files
rather than embedded as base64 blobs inside the JSON config.

Supports multiple named "profiles" (e.g. one automation setup per game/app),
each with its own settings.json + targets living in the same screenshots
folder (screenshots are namespaced by target id so collisions are impossible).
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import List

from models.target import Target
from models.settings import AppSettings


class ConfigManager:
    def __init__(self, base_dir: str | Path = "."):
        self.base_dir = Path(base_dir)
        self.config_dir = self.base_dir / "config"
        self.screenshots_dir = self.base_dir / "screenshots"
        self.logs_dir = self.base_dir / "logs"
        self.profiles_dir = self.config_dir / "profiles"

        for d in (self.config_dir, self.screenshots_dir, self.logs_dir, self.profiles_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.app_settings_path = self.config_dir / "app_settings.json"

    # ------------------------------------------------------------------
    # App-wide settings (not profile specific)
    # ------------------------------------------------------------------

    def load_app_settings(self) -> AppSettings:
        if not self.app_settings_path.exists():
            return AppSettings()
        try:
            with open(self.app_settings_path, "r", encoding="utf-8") as f:
                return AppSettings.from_dict(json.load(f))
        except (json.JSONDecodeError, OSError, TypeError, KeyError):
            return AppSettings()

    def save_app_settings(self, settings: AppSettings) -> None:
        with open(self.app_settings_path, "w", encoding="utf-8") as f:
            json.dump(settings.to_dict(), f, indent=2)

    # ------------------------------------------------------------------
    # Profiles (each profile = one settings.json holding a list of targets)
    # ------------------------------------------------------------------

    def profile_path(self, profile_name: str) -> Path:
        safe_name = "".join(c for c in profile_name if c.isalnum() or c in ("-", "_")) or "default"
        return self.profiles_dir / f"{safe_name}.json"

    def list_profiles(self) -> List[str]:
        return sorted(p.stem for p in self.profiles_dir.glob("*.json"))

    def load_targets(self, profile_name: str = "default") -> List[Target]:
        path = self.profile_path(profile_name)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [Target.from_dict(t) for t in data.get("targets", [])]
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
            raise ConfigLoadError(f"Failed to load profile '{profile_name}': {exc}") from exc

    def save_targets(self, targets: List[Target], profile_name: str = "default") -> None:
        path = self.profile_path(profile_name)
        payload = {"version": 1, "targets": [t.to_dict() for t in targets]}
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        tmp_path.replace(path)  # atomic-ish swap, avoids corrupting config on crash mid-write

    def delete_profile(self, profile_name: str) -> None:
        path = self.profile_path(profile_name)
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------
    # Screenshot file management
    # ------------------------------------------------------------------

    def screenshot_path_for(self, target_id: str) -> Path:
        return self.screenshots_dir / f"{target_id}.png"

    def delete_screenshot(self, target: Target) -> None:
        if target.template_path:
            p = Path(target.template_path)
            if p.exists() and p.is_relative_to(self.screenshots_dir.resolve().parent) is False:
                pass  # best-effort; still try to remove below
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------

    def export_profile(self, profile_name: str, export_zip_path: str | Path) -> None:
        """Bundle a profile's JSON + all referenced screenshots into a single .zip."""
        targets = self.load_targets(profile_name)
        profile_json_path = self.profile_path(profile_name)
        export_zip_path = Path(export_zip_path)

        with zipfile.ZipFile(export_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(profile_json_path, arcname="profile.json")
            for t in targets:
                if t.template_path and Path(t.template_path).exists():
                    zf.write(t.template_path, arcname=f"screenshots/{Path(t.template_path).name}")

    def import_profile(self, import_zip_path: str | Path, new_profile_name: str) -> int:
        """Import a .zip created by export_profile(). Returns number of targets imported."""
        import_zip_path = Path(import_zip_path)
        with zipfile.ZipFile(import_zip_path, "r") as zf:
            with zf.open("profile.json") as f:
                data = json.load(f)
            targets = [Target.from_dict(t) for t in data.get("targets", [])]

            for name in zf.namelist():
                if name.startswith("screenshots/") and not name.endswith("/"):
                    dest = self.screenshots_dir / Path(name).name
                    with zf.open(name) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)

            # Re-point template_path to this installation's screenshots dir
            for t in targets:
                if t.template_path:
                    t.template_path = str(self.screenshots_dir / Path(t.template_path).name)

        self.save_targets(targets, new_profile_name)
        return len(targets)


class ConfigLoadError(Exception):
    """Raised when a profile/config file exists but cannot be parsed."""
