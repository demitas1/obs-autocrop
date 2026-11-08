"""シーンアイテムの自動配置（PLAN.md 14章）。OS 非依存・外部依存なし。

クロップ後のソースを、アスペクト比を保ったままキャンバスの内側に収まる最大の大きさにし、
指定した位置（アンカー）に合わせる。

OBS の変換（境界なし、libobs の update_item_transform）:
    幅 W = (ソース幅 - アイテムのクロップ) × scaleX （符号付き。負なら左右反転）
    原点 O = アライメントで決まる点（LEFT: 0、RIGHT: W、どちらでもない: W/2。高さも同様）
    キャンバス上の x = positionX - O + u × W   (u は 0〜1)
よって表示範囲は x ∈ [positionX - O + min(0, W), positionX - O + max(0, W)]。
回転と境界（bounds）を使うアイテムは対象外とする。

OBS 32 は位置を整数に丸めて保存する（3025.83 → 3026。E5 で確認）。表示範囲の端を画素の境界に
そろえるため、表示サイズは軸ごとに整数へ切り捨て、拡大率は軸ごとに 表示サイズ / ソースサイズ とする
（アスペクト比の誤差は 1px 未満）。位置も整数にする。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ALIGN_LEFT, ALIGN_RIGHT, ALIGN_TOP, ALIGN_BOTTOM = 1, 2, 4, 8
ALIGN_TOP_LEFT = ALIGN_LEFT | ALIGN_TOP
ANCHORS = ("top-left", "top-right", "top-center")


class LayoutUnsupported(ValueError):
    pass


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def __str__(self) -> str:
        return f"({self.x0:.1f}, {self.y0:.1f})-({self.x1:.1f}, {self.y1:.1f}) [{self.w:.1f}x{self.h:.1f}]"


def _origin(align: int, size: float, lo: int, hi: int) -> float:
    if align & lo:
        return 0.0
    if align & hi:
        return size
    return size / 2


def content_size(t: dict) -> tuple[int, int]:
    """アイテムのクロップを除いたソースの大きさ（ソースのピクセル）。"""
    w = int(t["sourceWidth"]) - int(t.get("cropLeft", 0)) - int(t.get("cropRight", 0))
    h = int(t["sourceHeight"]) - int(t.get("cropTop", 0)) - int(t.get("cropBottom", 0))
    return w, h


def check_supported(t: dict) -> None:
    if t.get("boundsType", "OBS_BOUNDS_NONE") != "OBS_BOUNDS_NONE":
        raise LayoutUnsupported(f"境界（{t['boundsType']}）を使うアイテムは未対応です")
    if float(t.get("rotation", 0)) % 360 != 0:
        raise LayoutUnsupported(f"回転（{t['rotation']}°）しているアイテムは未対応です")
    w, h = content_size(t)
    if w <= 0 or h <= 0:
        raise LayoutUnsupported(f"ソースの大きさが 0 です（{w}x{h}）。映像が来ているか確認してください")


def item_box(t: dict) -> Box:
    """GetSceneItemTransform の値からキャンバス上の表示範囲を求める。"""
    check_supported(t)
    w, h = content_size(t)
    W, H = w * float(t["scaleX"]), h * float(t["scaleY"])
    align = int(t.get("alignment", ALIGN_TOP_LEFT))
    ox = float(t["positionX"]) - _origin(align, W, ALIGN_LEFT, ALIGN_RIGHT)
    oy = float(t["positionY"]) - _origin(align, H, ALIGN_TOP, ALIGN_BOTTOM)
    return Box(ox + min(0.0, W), oy + min(0.0, H), ox + max(0.0, W), oy + max(0.0, H))


def fit_scale(w: int, h: int, canvas_w: int, canvas_h: int) -> float:
    """アスペクト比を保ってキャンバスの内側に収まる最大の拡大率（縦または横がいっぱい）。"""
    return min(canvas_w / w, canvas_h / h)


def target_box(dw: float, dh: float, canvas_w: int, canvas_h: int, anchor: str) -> Box:
    if anchor == "top-left":
        x0 = 0.0
    elif anchor == "top-right":
        x0 = canvas_w - dw
    elif anchor == "top-center":
        x0 = math.floor((canvas_w - dw) / 2)
    else:
        raise ValueError(f"unknown anchor: {anchor!r} (choose from {ANCHORS})")
    return Box(x0, 0.0, x0 + dw, dh)


def plan_layout(t: dict, canvas: tuple[int, int], anchor: str = "top-left") -> tuple[dict, Box]:
    """SetSceneItemTransform に渡す値と、配置後の表示範囲を返す（表示範囲・位置は整数）。

    反転（scale の符号）は保ち、アライメントは左上にそろえる（OBS は中央・右のアライメントで
    原点を整数に切り捨てるため、左上にすると位置が正確になる）。
    """
    check_supported(t)
    cw, ch = canvas
    w, h = content_size(t)
    s = fit_scale(w, h, cw, ch)
    dw, dh = (min(math.floor(v * s + 1e-6), c) for v, c in ((w, cw), (h, ch)))
    sx = dw / w * (-1 if float(t["scaleX"]) < 0 else 1)
    sy = dh / h * (-1 if float(t["scaleY"]) < 0 else 1)
    box = target_box(dw, dh, cw, ch, anchor)
    W, H = w * sx, h * sy
    new = {
        "alignment": ALIGN_TOP_LEFT,
        "positionX": box.x0 - min(0.0, W),
        "positionY": box.y0 - min(0.0, H),
        "scaleX": sx,
        "scaleY": sy,
    }
    return new, box
