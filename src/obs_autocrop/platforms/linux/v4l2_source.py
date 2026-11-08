"""CaptureSourceAdapter の Linux 実装: OBS の v4l2_input（PLAN.md 4.2）。

E1 で確認したパック形式（OBS 32）:
- resolution = (幅 << 32) | 高さ
- framerate  = (分子 << 32) | 分母（フレーム間隔。30fps = 1/30）
- pixelformat = FourCC（リトルエンディアンの整数）
キーがない場合と -1 はどちらも「変更しない」（既定値 -1）。
"""

from __future__ import annotations

import os
from fractions import Fraction

from obs_autocrop.models import CameraSourceConfig

MASK32 = 0xFFFFFFFF


def fourcc_to_int(s: str) -> int:
    b = s.encode("ascii")
    if len(b) != 4:
        raise ValueError(f"FourCC must be 4 characters: {s!r}")
    return int.from_bytes(b, "little")


def int_to_fourcc(v: int) -> str:
    return v.to_bytes(4, "little").decode("ascii")


def _get(settings: dict, key: str) -> int | None:
    v = settings.get(key, -1)
    return None if v == -1 else v


class V4L2SourceAdapter:
    source_kind = "v4l2_input"

    def decode(self, settings: dict) -> CameraSourceConfig:
        res, rate, fmt = (_get(settings, k) for k in ("resolution", "framerate", "pixelformat"))
        fps = None
        if rate is not None:
            num, den = rate >> 32, rate & MASK32
            fps = den / num if num else None
        return CameraSourceConfig(
            device_ref=settings.get("device_id") or None,
            width=None if res is None else res >> 32,
            height=None if res is None else res & MASK32,
            fourcc=None if fmt is None else int_to_fourcc(fmt),
            fps=fps,
        )

    def encode(self, config: CameraSourceConfig) -> dict:
        """config のうち None でない項目を settings のキーと値にする。"""
        out: dict = {}
        if config.device_ref is not None:
            out["device_id"] = config.device_ref
        if (config.width is None) != (config.height is None):
            raise ValueError("width と height は両方指定する")
        if config.width is not None:
            out["resolution"] = (config.width << 32) | config.height
        if config.fourcc is not None:
            out["pixelformat"] = fourcc_to_int(config.fourcc)
        if config.fps is not None:
            interval = Fraction(1) / Fraction(config.fps).limit_denominator(1001)
            out["framerate"] = (interval.numerator << 32) | interval.denominator
        return out

    def same_device(self, device_ref: str | None, node: str) -> bool:
        return bool(device_ref) and os.path.realpath(device_ref) == os.path.realpath(node)
