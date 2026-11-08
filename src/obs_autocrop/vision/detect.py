"""ArUco の検出とクロップ算出（PLAN.md 7.4）。OS 非依存。

外側の角は「4 枚の中心から最も遠い角」、四隅の割り当ては画像上の座標で決める。
マーカーの向きや ID の置き場所には依存しない（E4 で変更。PLAN.md 7.4 の 3）。

座標系: OpenCV の角座標は画素中心を整数とする（画素 i の中心が i）。マーカーの黒枠の外縁が
画素の境界にある場合、角は x = i - 0.5 のように出る。クロップは画素の境界で決まるため、
+0.5 して「画素の境界の座標」（画素 i は [i, i+1)）に直してから外側へ丸める。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from obs_autocrop.models import CropRect
from obs_autocrop.vision.markers import POSITIONS, get_dictionary

ROUND_EPS = 0.05  # 境界ちょうどの値が誤差で外側に 1px はみ出さないようにする余裕


class MeasurementFailed(RuntimeError):
    pass


def make_detector(dictionary: str = "DICT_4X4_50", win_max: int = 103,
                  win_step: int = 10) -> cv2.aruco.ArucoDetector:
    """win_max: 適応的二値化の窓の最大（OpenCV の既定は 23）。

    E4: 4K で 1 辺 230px 程度（1 セル 38px）のマーカーは、既定の窓では光沢で黒が浮いた 1 枚を
    検出できなかった。103 にすると検出でき、処理時間は 4K 1 枚 44ms → 63ms。
    """
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.adaptiveThreshWinSizeMax = win_max
    params.adaptiveThreshWinSizeStep = win_step
    return cv2.aruco.ArucoDetector(get_dictionary(dictionary), params)


def detect(frame: np.ndarray, detector: cv2.aruco.ArucoDetector) -> dict[int, list[np.ndarray]]:
    """ID → 検出された角の配列（4x2、マーカー自身の向きで 左上・右上・右下・左下）のリスト。"""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)
    out: dict[int, list[np.ndarray]] = {}
    if ids is None:
        return out
    for c, i in zip(corners, ids.flatten()):
        out.setdefault(int(i), []).append(c.reshape(4, 2).astype(np.float64))
    return out


@dataclass
class Measurement:
    frame_size: tuple[int, int]
    ids: tuple[int, ...]
    corners: dict[int, tuple[float, float]]       # ID → 外側の角（フレーム間の中央値、画素中心座標）
    corner_std: dict[int, tuple[float, float]]
    crop: CropRect
    frames_total: int
    frames_valid: int
    positions: dict[str, int]                     # 左上・右上・右下・左下 → ID（画像上の配置）
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["crop"] = asdict(self.crop)
        d["corners"] = {str(k): v for k, v in self.corners.items()}
        d["corner_std"] = {str(k): v for k, v in self.corner_std.items()}
        return d


def crop_from_corners(p: list[tuple[float, float]], frame_w: int, frame_h: int,
                      margin_px: int = 0) -> tuple[CropRect, list[str]]:
    """左上・右上・右下・左下の外側の角（画素中心座標）からクロップ矩形を求める（7.4 の 5, 6）。"""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = [(x + 0.5, y + 0.5) for x, y in p]
    left = math.floor(min(x0, x3) - margin_px + ROUND_EPS)
    top = math.floor(min(y0, y1) - margin_px + ROUND_EPS)
    right = math.ceil(max(x1, x2) + margin_px - ROUND_EPS)
    bottom = math.ceil(max(y2, y3) + margin_px - ROUND_EPS)
    warnings = []
    cl, ct, cr, cb = max(left, 0), max(top, 0), min(right, frame_w), min(bottom, frame_h)
    if (cl, ct, cr, cb) != (left, top, right, bottom):
        warnings.append(f"矩形 ({left},{top})-({right},{bottom}) がフレームからはみ出したため切り詰めました")
    if cr - cl < CropRect.MIN_SIZE or cb - ct < CropRect.MIN_SIZE:
        raise MeasurementFailed(f"矩形が小さすぎます: ({cl},{ct})-({cr},{cb})")
    return CropRect(cl, ct, cr - cl, cb - ct), warnings


def skew_warnings(p: list[tuple[float, float]], rect: CropRect, ratio: float) -> list[str]:
    """縦横の辺が軸に平行かどうか（7.4 の 7）。回転・スキューは補正しない。"""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    tol_x, tol_y = ratio * rect.w, ratio * rect.h
    checks = [("左辺 |P0.x-P3.x|", abs(x0 - x3), tol_x), ("右辺 |P1.x-P2.x|", abs(x1 - x2), tol_x),
              ("上辺 |P0.y-P1.y|", abs(y0 - y1), tol_y), ("下辺 |P3.y-P2.y|", abs(y3 - y2), tol_y)]
    return [f"傾き: {name} = {v:.1f}px > {tol:.1f}px" for name, v, tol in checks if v > tol]


def outer_corner_index(markers: dict[int, np.ndarray]) -> dict[int, int]:
    """各マーカーで、4 枚の中心から最も遠い角の番号（マーカーの向きに依存しない）。"""
    center = np.mean([c.mean(axis=0) for c in markers.values()], axis=0)
    return {i: int(np.argmax(np.linalg.norm(c - center, axis=1))) for i, c in markers.items()}


def assign_positions(points: dict[int, tuple[float, float]]) -> list[int]:
    """外側の角の座標から、左上・右上・右下・左下にあたる ID を返す。"""
    ids = list(points)
    tl = min(ids, key=lambda i: points[i][0] + points[i][1])
    br = max(ids, key=lambda i: points[i][0] + points[i][1])
    tr = max(ids, key=lambda i: points[i][0] - points[i][1])
    bl = min(ids, key=lambda i: points[i][0] - points[i][1])
    order = [tl, tr, br, bl]
    if len(set(order)) != 4:
        raise MeasurementFailed(f"マーカーの配置から四隅を決められません: {points}")
    return order


def measure(frames: list[np.ndarray], detector: cv2.aruco.ArucoDetector,
            ids: tuple[int, ...] = (0, 1, 2, 3), min_valid: int = 5, margin_px: int = 0,
            skew_tol_ratio: float = 0.01) -> tuple[Measurement, list[dict[int, list[np.ndarray]]]]:
    """複数フレームから計測する（7.4 の 2〜8）。検出結果（フレームごと）も返す。"""
    if not frames:
        raise MeasurementFailed("フレームがありません")
    h, w = frames[0].shape[:2]
    all_dets = [detect(f, detector) for f in frames]
    valid = [d for d in all_dets if all(len(d.get(i, [])) == 1 for i in ids)]
    if len(valid) < min_valid:
        seen = {i: sum(len(d.get(i, [])) == 1 for d in all_dets) for i in ids}
        dup = {i: sum(len(d.get(i, [])) > 1 for d in all_dets) for i in ids}
        raise MeasurementFailed(
            f"4 枚揃ったフレームが {len(valid)}/{len(frames)}（必要 {min_valid}）。"
            f"ID ごとの検出フレーム数 {seen}" + (f"、重複 {dup}" if any(dup.values()) else ""))
    # 外側の角は 4 枚の中心から最も遠い角とし、四隅の割り当ても座標で決める（向き・ID の配置に依存しない）
    stacks = {i: np.array([d[i][0] for d in valid]) for i in ids}  # (n, 4, 2)
    med_markers = {i: np.median(stacks[i], axis=0) for i in ids}
    k_outer = outer_corner_index(med_markers)
    outer = {i: stacks[i][:, k_outer[i], :] for i in ids}          # (n, 2)
    med = {i: tuple(float(v) for v in np.median(outer[i], axis=0)) for i in ids}
    std = {i: tuple(float(v) for v in np.std(outer[i], axis=0)) for i in ids}
    order = assign_positions(med)
    pts = [med[i] for i in order]
    rect, warnings = crop_from_corners(pts, w, h, margin_px)
    warnings += skew_warnings(pts, rect, skew_tol_ratio)
    positions = {POSITIONS[k]: i for k, i in enumerate(order)}
    return Measurement((w, h), tuple(ids), med, std, rect, len(frames), len(valid), positions,
                       warnings), all_dets


def draw_overlay(frame: np.ndarray, dets: dict[int, list[np.ndarray]], m: Measurement) -> np.ndarray:
    """検出したマーカー、外側の角、クロップ矩形を描いた画像。"""
    img = frame.copy() if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    th = max(2, img.shape[1] // 960)
    if dets:
        ids = np.array([[i] for i, cs in dets.items() for _ in cs], dtype=np.int32)
        cs = [c.reshape(1, 4, 2).astype(np.float32) for cs_ in dets.values() for c in cs_]
        cv2.aruco.drawDetectedMarkers(img, cs, ids)
    r = m.crop
    cv2.rectangle(img, (r.x, r.y), (r.x + r.w - 1, r.y + r.h - 1), (0, 255, 0), th)
    for x, y in m.corners.values():
        cv2.circle(img, (round(x), round(y)), 6 * th, (0, 0, 255), th)
    cv2.putText(img, f"crop x={r.x} y={r.y} w={r.w} h={r.h}", (r.x + 20 * th, r.y + 60 * th),
                cv2.FONT_HERSHEY_SIMPLEX, th, (0, 255, 0), th)
    return img


def corner_zooms(frame: np.ndarray, m: Measurement, half: int = 24, scale: int = 8) -> np.ndarray:
    """各マーカーの外側の角の周辺を拡大し、左上・右上・右下・左下の順に横に並べた画像。

    ±1px の目視確認用。緑の線はクロップの境界（画素の境界）、赤い点は検出した外側の角。
    黒枠の外縁が緑の線に接していればよい（傾いている場合は、外側にある角だけが接する）。
    """
    r = m.crop
    h, w = frame.shape[:2]
    tiles = []
    for pos in POSITIONS:
        px, py = m.corners[m.positions[pos]]
        cx, cy = int(round(px + 0.5)), int(round(py + 0.5))  # 角に最も近い画素の境界
        x0, y0 = cx - half, cy - half
        patch = np.full((2 * half, 2 * half, 3), 128, np.uint8)
        sx0, sy0, sx1, sy1 = max(x0, 0), max(y0, 0), min(x0 + 2 * half, w), min(y0 + 2 * half, h)
        if sx1 > sx0 and sy1 > sy0:
            src = frame[sy0:sy1, sx0:sx1]
            if src.ndim == 2:
                src = cv2.cvtColor(src, cv2.COLOR_GRAY2BGR)
            patch[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = src
        big = cv2.resize(patch, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        n = big.shape[0]
        for bx in (r.x, r.x + r.w):
            if 0 <= (bx - x0) * scale <= n:
                cv2.line(big, ((bx - x0) * scale, 0), ((bx - x0) * scale, n), (0, 255, 0), 1)
        for by in (r.y, r.y + r.h):
            if 0 <= (by - y0) * scale <= n:
                cv2.line(big, (0, (by - y0) * scale), (n, (by - y0) * scale), (0, 255, 0), 1)
        dot = (int((px + 0.5 - x0) * scale), int((py + 0.5 - y0) * scale))
        cv2.circle(big, dot, 4, (0, 0, 255), -1)
        cv2.putText(big, pos, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        tiles.append(cv2.copyMakeBorder(big, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    return np.hstack(tiles)
