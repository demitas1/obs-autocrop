"""config.toml の読み込み（PLAN.md 付録B）。"""

from __future__ import annotations

import sys
from pathlib import Path

from obs_autocrop.models import CameraIdentity

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path("config.toml")


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def camera_identities(camera: dict) -> list[CameraIdentity]:
    """[camera] で指定された識別子を優先度順（by-id → by-path → usb）にすべて返す。"""
    out = []
    if camera.get("by_id"):
        out.append(CameraIdentity(kind="by-id", value=camera["by_id"]))
    if camera.get("by_path"):
        out.append(CameraIdentity(kind="by-path", value=camera["by_path"]))
    if camera.get("vendor_id") and camera.get("product_id"):
        out.append(CameraIdentity(
            kind="usb",
            vendor_id=camera["vendor_id"],
            product_id=camera["product_id"],
            serial=camera.get("serial", ""),
            name=camera.get("name", ""),
        ))
    return out


def camera_identity(camera: dict) -> CameraIdentity:
    """解決に使う識別子（最も優先度の高いもの）を返す。"""
    ids = camera_identities(camera)
    if not ids:
        raise ValueError("[camera] に by_id / by_path / vendor_id+product_id のいずれかを指定してください")
    return ids[0]
