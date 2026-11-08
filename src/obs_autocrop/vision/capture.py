"""計測用キャプチャ（PLAN.md 7.2, 9.1）。OS 非依存。

デバイスの開き方は CaptureBackend（platforms/）に委ね、ここでは設定順序・検証・ウォームアップ・解放を担う。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

from obs_autocrop.models import CameraSourceConfig
from obs_autocrop.platforms.base import CaptureBackend


class CaptureError(RuntimeError):
    pass


class FormatMismatch(CaptureError):
    pass


@dataclass(frozen=True)
class CaptureFormat:
    width: int
    height: int
    fourcc: str
    fps: float | None = None

    def __str__(self) -> str:
        fps = f" {self.fps:g}fps" if self.fps else ""
        return f"{self.width}x{self.height} {self.fourcc}{fps}"


def resolve_format(obs: CameraSourceConfig, default: dict) -> tuple[CaptureFormat, list[str]]:
    """OBS ソースの設定を優先し、未指定（-1）の項目は [camera.format] で補う（PLAN.md 9.1 の 1, 2）。"""
    warnings = []
    vals = {}
    for key in ("width", "height", "fourcc", "fps"):
        v = getattr(obs, key)
        if v is None:
            v = default.get(key)
            if v is None and key != "fps":
                raise ValueError(f"OBS のソースと [camera.format] のどちらにも {key} がありません")
            warnings.append(f"OBS のソースで {key} が未指定のため [camera.format] の {v!r} を使います")
        vals[key] = v
    return CaptureFormat(int(vals["width"]), int(vals["height"]), str(vals["fourcc"]),
                         float(vals["fps"]) if vals["fps"] else None), warnings


def decode_fourcc(v: float) -> str:
    i = int(v)
    return "".join(chr((i >> (8 * k)) & 0xFF) for k in range(4))


@contextmanager
def open_camera(backend: CaptureBackend, node: str, fmt: CaptureFormat) -> Iterator[cv2.VideoCapture]:
    """FOURCC → 幅・高さ → FPS の順に設定し、実際の値を検証してから渡す。終了時に必ず解放する。"""
    cap = backend.open(node)
    try:
        if not cap.isOpened():
            raise CaptureError(f"{node} を開けません（OBS など他のプログラムが使用中の可能性があります）")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fmt.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, fmt.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, fmt.height)
        if fmt.fps:
            cap.set(cv2.CAP_PROP_FPS, fmt.fps)
        actual = (decode_fourcc(cap.get(cv2.CAP_PROP_FOURCC)),
                  int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        if actual != (fmt.fourcc, fmt.width, fmt.height):
            raise FormatMismatch(f"要求 {fmt} に対して実際は {actual[1]}x{actual[2]} {actual[0]}")
        yield cap
    finally:
        cap.release()


def read_frames(cap: cv2.VideoCapture, fmt: CaptureFormat, n: int, warmup: int = 30) -> list[np.ndarray]:
    """最初の warmup フレームを捨て、n フレームを返す。フレームの実寸も検証する。"""
    frames = []
    for i in range(warmup + n):
        ok, frame = cap.read()
        if not ok or frame is None:
            raise CaptureError(f"フレームを取得できません（{i} フレーム目）")
        if frame.shape[:2] != (fmt.height, fmt.width):
            raise FormatMismatch(f"フレームの実寸 {frame.shape[1]}x{frame.shape[0]} が {fmt} と一致しません")
        if i >= warmup:
            frames.append(frame)
    return frames


def capture(backend: CaptureBackend, node: str, fmt: CaptureFormat, n: int,
            warmup: int = 30) -> list[np.ndarray]:
    with open_camera(backend, node, fmt) as cap:
        return read_frames(cap, fmt, n, warmup)


def wait_device_free(backend: CaptureBackend, node: str, timeout: float = 5,
                     interval: float = 0.5) -> bool:
    """解放後にデバイスを再び開けることを確認する（OBS に渡す前、PLAN.md 7.2 の 6）。"""
    deadline = time.monotonic() + timeout
    while True:
        cap = backend.open(node)
        try:
            if cap.isOpened():
                return True
        finally:
            cap.release()
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
