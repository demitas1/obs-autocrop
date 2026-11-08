"""ObsPaths の Linux 実装（PLAN.md 4.1）。"""

from __future__ import annotations

import json
from pathlib import Path

NATIVE_CONFIG_DIR = Path.home() / ".config/obs-studio"
FLATPAK_CONFIG_DIR = Path.home() / ".var/app/com.obsproject.Studio/config/obs-studio"


class LinuxObsPaths:
    def __init__(self, config_dir: Path | str | None = None, flavor: str = "native"):
        if config_dir:
            self.config_root = Path(config_dir).expanduser()
        else:
            self.config_root = FLATPAK_CONFIG_DIR if flavor == "flatpak" else NATIVE_CONFIG_DIR

    @property
    def websocket_config_path(self) -> Path:
        return self.config_root / "plugin_config/obs-websocket/config.json"

    def websocket_config(self) -> dict:
        """obs-websocket の設定を {enabled, port, auth_required, password} で返す。"""
        d = json.loads(self.websocket_config_path.read_text())
        return {
            "enabled": d.get("server_enabled", False),
            "port": d.get("server_port", 4455),
            "auth_required": d.get("auth_required", False),
            "password": d.get("server_password", "") if d.get("auth_required") else "",
        }
