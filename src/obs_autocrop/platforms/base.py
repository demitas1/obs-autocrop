"""OS 依存処理のインターフェース（PLAN.md 3.1）。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from obs_autocrop.models import CameraIdentity, CameraInfo, ResolvedCamera


class CameraLocator(Protocol):
    def list_cameras(self) -> list[CameraInfo]:
        """映像キャプチャ用のデバイスを列挙する。"""
        ...

    def resolve(self, identity: CameraIdentity) -> ResolvedCamera:
        """識別子を解決する。0 件なら CameraNotFound、2 件以上なら CameraAmbiguous。"""
        ...

    def config_snippet(self, camera: CameraInfo) -> str:
        """config.toml の [camera] に貼り付けられる候補設定を返す。"""
        ...


class ObsPaths(Protocol):
    config_root: Path

    def websocket_config(self) -> dict:
        """obs-websocket の設定を {enabled, port, auth_required, password} で返す。"""
        ...
