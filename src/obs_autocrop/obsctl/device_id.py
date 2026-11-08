"""device_id の書き込みモード（PLAN.md 5.3）。OS 非依存。"""

from __future__ import annotations

from obs_autocrop.models import ResolvedCamera
from obs_autocrop.platforms.base import CaptureSourceAdapter

MODES = ("resolved", "link", "none")


def plan_device_id(current: str | None, resolved: ResolvedCamera, mode: str,
                   adapter: CaptureSourceAdapter) -> tuple[str | None, list[str]]:
    """書き込む device_id（変更しない場合は None）と警告を返す。"""
    if mode == "resolved":
        return resolved.node, []
    if mode == "link":
        if not resolved.link:
            raise ValueError("恒久リンクがないため link モードは使えません")
        return resolved.link, []
    if mode == "none":
        if adapter.same_device(current, resolved.node):
            return None, []
        return None, [f"device_id {current!r} は解決したカメラ {resolved.node} と一致しません"]
    raise ValueError(f"unknown device_id mode: {mode!r} (choose from {MODES})")
