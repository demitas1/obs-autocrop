"""プラットフォーム実装の選択（PLAN.md 3.1）。

共通層は OS 依存の機能を get_platform() 経由でのみ使う。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from obs_autocrop.platforms.base import CameraLocator


class UnsupportedPlatform(RuntimeError):
    pass


@dataclass
class Platform:
    camera_locator: CameraLocator


def get_platform(options: dict | None = None) -> Platform:
    """sys.platform に応じた実装を返す。options は config.toml の [platform]。"""
    if sys.platform.startswith("linux"):
        from obs_autocrop.platforms.linux.camera import LinuxCameraLocator

        return Platform(camera_locator=LinuxCameraLocator())
    raise UnsupportedPlatform(f"unsupported platform: {sys.platform} (Linux only)")
