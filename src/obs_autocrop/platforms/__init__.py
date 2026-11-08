"""プラットフォーム実装の選択（PLAN.md 3.1）。

共通層は OS 依存の機能を get_platform() 経由でのみ使う。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from obs_autocrop.platforms.base import CameraLocator, CaptureSourceAdapter, ObsPaths, ObsProcess


class UnsupportedPlatform(RuntimeError):
    pass


@dataclass
class Platform:
    camera_locator: CameraLocator
    obs_paths: ObsPaths
    obs_process: ObsProcess
    source_adapter: CaptureSourceAdapter


def get_platform(config: dict | None = None) -> Platform:
    """sys.platform に応じた実装を返す。config は config.toml 全体（[platform] と [obs] を見る）。"""
    config = config or {}
    flavor = config.get("platform", {}).get("obs_flavor", "native")
    config_dir = config.get("obs", {}).get("config_dir") or None
    if sys.platform.startswith("linux"):
        from obs_autocrop.platforms.linux.camera import LinuxCameraLocator
        from obs_autocrop.platforms.linux.obs_paths import LinuxObsPaths
        from obs_autocrop.platforms.linux.obs_process import LinuxObsProcess
        from obs_autocrop.platforms.linux.v4l2_source import V4L2SourceAdapter

        return Platform(
            camera_locator=LinuxCameraLocator(),
            obs_paths=LinuxObsPaths(config_dir, flavor),
            obs_process=LinuxObsProcess(config.get("obs", {}).get("launch_cmd")),
            source_adapter=V4L2SourceAdapter(),
        )
    raise UnsupportedPlatform(f"unsupported platform: {sys.platform} (Linux only)")
