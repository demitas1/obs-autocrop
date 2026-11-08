"""ArUco マーカーの仕様と生成（PLAN.md 7.1）。OS 非依存。

- ID は 0〜3 の 4 枚。四隅のどこに置いても、どの向きで置いてもよい（detect.py が画像上の
  配置から四隅と外側の角を決める。E4 でカメラが 180° 回転して設置されていたため）。
- 印刷用 PDF は実寸（既定 300dpi）。セルが整数ピクセルになるよう大きさを丸め、実際の寸法を返す。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

POSITIONS = ("top-left", "top-right", "bottom-right", "bottom-left")
MM_PER_INCH = 25.4
A4_MM = (210.0, 297.0)


def get_dictionary(name: str = "DICT_4X4_50"):
    if not hasattr(cv2.aruco, name):
        raise ValueError(f"unknown ArUco dictionary: {name}")
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def marker_cells(dictionary) -> int:
    """黒枠を含むマーカー 1 辺のセル数（4x4 なら 6）。"""
    return dictionary.markerSize + 2


def recommended_size_mm(view_w_mm: float, view_h_mm: float, width_px: int, height_px: int,
                        target_px: int = 50) -> int:
    """マーカーが target_px ピクセル程度に写る大きさ（mm、1mm 単位で切り上げ）。"""
    mm_per_px = max(view_w_mm / width_px, view_h_mm / height_px)
    return math.ceil(target_px * mm_per_px - 1e-9)


def mm_to_px(mm: float, dpi: int) -> float:
    return mm * dpi / MM_PER_INCH


@dataclass(frozen=True)
class MarkerSpec:
    size_mm: float          # 要求した黒枠の 1 辺
    actual_mm: float        # セルを整数ピクセルに丸めた後の 1 辺
    cell_px: int
    cells: int
    quiet_cells: int
    dpi: int

    @property
    def marker_px(self) -> int:
        return self.cell_px * self.cells

    @property
    def tile_px(self) -> int:
        """quiet zone を含む切り取り範囲の 1 辺。"""
        return self.cell_px * (self.cells + 2 * self.quiet_cells)


def make_spec(dictionary, size_mm: float, quiet_cells: int = 2, dpi: int = 300) -> MarkerSpec:
    cells = marker_cells(dictionary)
    cell_px = max(1, round(mm_to_px(size_mm, dpi) / cells))
    actual = cell_px * cells * MM_PER_INCH / dpi
    return MarkerSpec(size_mm, actual, cell_px, cells, quiet_cells, dpi)


def marker_tile(dictionary, marker_id: int, spec: MarkerSpec) -> np.ndarray:
    """quiet zone 付きのマーカー画像（グレースケール、白背景）。"""
    m = cv2.aruco.generateImageMarker(dictionary, marker_id, spec.marker_px, borderBits=1)
    q = spec.cell_px * spec.quiet_cells
    return cv2.copyMakeBorder(m, q, q, q, q, cv2.BORDER_CONSTANT, value=255)


def _font(px: int):
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render_sheet(dictionary, ids: list[int], spec: MarkerSpec, *, page_mm=A4_MM,
                 margin_mm: float = 12, scale_bar_mm: int = 50) -> list[Image.Image]:
    """印刷用ページ（PIL Image のリスト）。

    各マーカーの周囲（quiet zone の外周）に薄い灰色の切り取り線、下にラベルを描く。
    ページ下部に印刷倍率の確認用スケール（scale_bar_mm）を描く。
    """
    dpi = spec.dpi
    pw, ph = (round(mm_to_px(v, dpi)) for v in page_mm)
    margin = round(mm_to_px(margin_mm, dpi))
    label_px = max(round(mm_to_px(3.5, dpi)), 12)
    gap = round(mm_to_px(8, dpi))
    tile = spec.tile_px
    font = _font(label_px)
    small = _font(round(label_px * 0.8))
    labels = [f"ID {mid}" for mid in ids]
    label_w = max(round(font.getlength(t)) for t in labels)
    cell_w = max(tile, label_w) + gap
    cell_h = tile + label_px * 3 + gap
    footer = round(mm_to_px(30, dpi))
    if tile > pw - 2 * margin or cell_h - gap > ph - 2 * margin - footer:
        raise ValueError(f"marker {spec.actual_mm:.1f}mm does not fit on the page")
    cols = max(1, (pw - 2 * margin + gap) // cell_w)
    rows = max(1, (ph - 2 * margin - footer + gap) // cell_h)
    per_page = cols * rows
    pages = []
    for start in range(0, len(ids), per_page):
        page = Image.new("L", (pw, ph), 255)
        d = ImageDraw.Draw(page)
        for k, mid in enumerate(ids[start:start + per_page]):
            r, c = divmod(k, cols)
            x0 = margin + c * cell_w
            y0 = margin + r * cell_h
            page.paste(Image.fromarray(marker_tile(dictionary, mid, spec)), (x0, y0))
            d.rectangle([x0, y0, x0 + tile - 1, y0 + tile - 1], outline=190, width=1)
            d.text((x0, y0 + tile + label_px // 3), labels[start + k], fill=0, font=font)
            d.text((x0, y0 + tile + label_px * 4 // 3), f"{spec.actual_mm:.1f} mm", fill=90, font=small)
        # スケールと注意書き
        bar = round(mm_to_px(scale_bar_mm, dpi))
        by = ph - margin - round(mm_to_px(16, dpi))
        d.line([margin, by, margin + bar, by], fill=0, width=3)
        for x in (margin, margin + bar):
            d.line([x, by - label_px // 2, x, by + label_px // 2], fill=0, width=3)
        d.text((margin + bar + label_px, by - label_px // 2),
               f"{scale_bar_mm} mm  /  print at 100% (no fit-to-page)  /  {dpi} dpi", fill=0, font=small)
        d.text((margin, by + label_px), "Put one marker in each corner of the area (any order, any rotation), "
               "with a black corner exactly on the corner of the area.", fill=0, font=small)
        pages.append(page)
    return pages


def save_markers(out_dir: Path, dictionary, ids: list[int], spec: MarkerSpec) -> dict[str, Path]:
    """PDF（全ページ）と個別 PNG（quiet zone 付き、dpi 情報付き）を保存する。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"aruco-{spec.actual_mm:.1f}mm"
    pages = render_sheet(dictionary, ids, spec)
    pdf = out_dir / f"{stem}.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=spec.dpi)
    out = {"pdf": pdf}
    for mid in ids:
        png = out_dir / f"{stem}-id{mid}.png"
        Image.fromarray(marker_tile(dictionary, mid, spec)).save(png, dpi=(spec.dpi, spec.dpi))
        out[f"id{mid}"] = png
    return out
