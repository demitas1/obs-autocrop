"""ObsPaths の Linux 実装（PLAN.md 4.1）。"""

from __future__ import annotations

import configparser
import json
from pathlib import Path

NATIVE_CONFIG_DIR = Path.home() / ".config/obs-studio"
FLATPAK_CONFIG_DIR = Path.home() / ".var/app/com.obsproject.Studio/config/obs-studio"


def read_ini(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # OBS のキーは大文字小文字を区別する
    cp.read(path, encoding="utf-8-sig")
    return {s: dict(cp[s]) for s in cp.sections()}


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

    def scenes_dir(self) -> Path:
        """OBS 31+ は global.ini の [Locations] SceneCollections を優先する（E0）。"""
        loc = read_ini(self.config_root / "global.ini").get("Locations", {})
        base = Path(loc["SceneCollections"]) / "obs-studio" if loc.get("SceneCollections") else self.config_root
        return base / "basic/scenes"

    def current_collection(self) -> tuple[str, str]:
        """現在のコレクションの (名前, ファイル名)。user.ini を優先し、なければ global.ini（E0）。"""
        for fname in ("user.ini", "global.ini"):
            basic = read_ini(self.config_root / fname).get("Basic", {})
            if basic.get("SceneCollectionFile"):
                return basic.get("SceneCollection", ""), basic["SceneCollectionFile"]
        raise FileNotFoundError("現在のシーンコレクションが user.ini / global.ini に見つかりません")

    def collection_path(self, name: str | None = None) -> Path:
        """コレクションのファイルパス。name 省略時は現在のコレクション。

        E0: user.ini の SceneCollectionFile は拡張子付き（"Untitled.json"）だった。
        表示名とファイル名は一致するとは限らないため、名前指定時はファイル内の "name" で探す。
        """
        d = self.scenes_dir()
        if not name:
            fname = self.current_collection()[1]
            return d / (fname if fname.endswith(".json") else fname + ".json")
        for p in sorted(d.glob("*.json")):
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("name") == name:
                    return p
            except (OSError, ValueError):
                continue
        return d / f"{name}.json"
