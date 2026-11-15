"""射影補正（台形補正）の計算（PLAN.md 15章、docs/PLAN_perspective.md 4章）。OS 非依存。

座標の規約（PLAN.md 15.2 の 1、P0 で確認）:
- 入力の角は 7.4 と同じ画素中心の座標（画素 i の中心が i）。+0.5 して画素の境界の座標にし、
  幅・高さで割って正規化する（u = (x + 0.5) / W）。シェーダーの UV（画素中心が (i + 0.5) / W）と一致する。
- 補正後の矩形 R は画素の境界の整数座標（CropRect）。その四隅は u = R.x / W などで正規化する。
- 行列 M は「出力の UV → 入力の UV」（= H⁻¹）で、M[2][2] = 1 に正規化する（shaders/perspective.shader）。

四隅の順序は 左上・右上・右下・左下（7.4 の 3 で割り当てた外側の角）。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from obs_autocrop.models import CropRect

MATRIX_KEYS = [f"m{r}{c}" for r in range(3) for c in range(3)]
UNIT_SQUARE = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float64)


class PerspectiveError(ValueError):
    pass


# ---------------------------------------------------------------- 基本

def to_edge(points) -> np.ndarray:
    """画素中心の座標 → 画素の境界の座標。"""
    return np.asarray(points, np.float64).reshape(-1, 2) + 0.5


def normalize(points_edge, width: int, height: int) -> np.ndarray:
    return np.asarray(points_edge, np.float64).reshape(-1, 2) / (width, height)


def apply(m: np.ndarray, points) -> np.ndarray:
    p = np.asarray(points, np.float64).reshape(-1, 2)
    q = np.c_[p, np.ones(len(p))] @ np.asarray(m, np.float64).T
    return q[:, :2] / q[:, 2:3]


def side_lengths(quad) -> tuple[float, float, float, float]:
    """上・右・下・左の辺の長さ。"""
    q = np.asarray(quad, np.float64).reshape(4, 2)
    return tuple(float(np.linalg.norm(q[(i + 1) % 4] - q[i])) for i in range(4))


def skew_angles(quad) -> list[float]:
    """各辺が軸（上下は水平、左右は垂直）からずれている角度（度）。上・右・下・左の順。"""
    q = np.asarray(quad, np.float64).reshape(4, 2)
    out = []
    for i in range(4):
        dx, dy = q[(i + 1) % 4] - q[i]
        ang = math.degrees(math.atan2(abs(dy), abs(dx)))  # 水平からの角度
        out.append(ang if i % 2 == 0 else 90.0 - ang)
    return out


MAX_ROTATION_DEG = 35.0


def rotation_deg(quad) -> float:
    """面内の回転（上辺と下辺の向きの平均、度。時計回りが正）。"""
    q = np.asarray(quad, np.float64).reshape(4, 2)
    angs = [math.degrees(math.atan2(*(q[b] - q[a])[::-1])) for a, b in ((0, 1), (3, 2))]
    return float(np.mean(angs))


def check_rotation(quad, limit: float = MAX_ROTATION_DEG) -> None:
    """四隅の割り当て（7.4 の 3、x+y / x−y）は面内の回転が 45° に近づくと 90° ずれる（P2 で確認）。

    ずれると誤った行列・縦横比になるため、回転が大きい場合は中断する。カメラの 180° 回転は
    ソースの座標のまま扱うので、ここでの回転には含まれない。
    """
    r = rotation_deg(quad)
    if abs(r) > limit:
        raise PerspectiveError(f"面内の回転が大きすぎます（{r:.1f}°、許容 ±{limit:.0f}°）。"
                               "四隅を正しく割り当てられないため、カメラかマーカーの向きを直してください")


def check_quad(quad) -> None:
    """凸で、左上・右上・右下・左下の順に時計回り（画像座標）であること。"""
    q = np.asarray(quad, np.float64).reshape(4, 2)
    cross = []
    for i in range(4):
        a, b, c = q[i], q[(i + 1) % 4], q[(i + 2) % 4]
        cross.append((b - a)[0] * (c - b)[1] - (b - a)[1] * (c - b)[0])
    if not all(v > 0 for v in cross):
        raise PerspectiveError(f"四隅が凸の四角形（左上・右上・右下・左下の順）になっていません: {q.tolist()}")


# ---------------------------------------------------------------- 補正後の矩形 R

def target_rect(quad_edge, width: int, height: int, aspect: float) -> tuple[CropRect, list[str]]:
    """補正後の矩形 R（縦横比 aspect = 高さ / 幅、PLAN.md 15.3）。

    - 大きさ: 再サンプリングで縮小しないよう、補正前の上下の辺の長い方を幅、左右の辺の長い方を高さの
      下限とし、両方を満たす最小の大きさにする。フレームに収まらなければ縮小して警告する。
    - 位置: 補正前の四隅の外接矩形の中心に合わせ、フレーム内に収める。
    - 座標は画素の境界の整数。
    """
    if aspect <= 0:
        raise PerspectiveError(f"縦横比が不正です: {aspect}")
    top, right, bottom, left = side_lengths(quad_edge)
    w = max(max(top, bottom), max(left, right) / aspect)
    warnings = []
    if w > width or w * aspect > height:
        s = min(width / w, height / (w * aspect))
        warnings.append(f"補正後の矩形がフレームに収まらないため {s:.3f} 倍に縮小しました（実効解像度が下がります）")
        w *= s
    rw = min(int(math.floor(w + 1e-6)), width)
    rh = min(int(round(rw * aspect)), height)
    q = np.asarray(quad_edge, np.float64).reshape(4, 2)
    cx, cy = (q[:, 0].min() + q[:, 0].max()) / 2, (q[:, 1].min() + q[:, 1].max()) / 2
    x = int(round(cx - rw / 2))
    y = int(round(cy - rh / 2))
    x = min(max(x, 0), width - rw)
    y = min(max(y, 0), height - rh)
    return CropRect(x, y, rw, rh), warnings


def rect_corners_edge(rect: CropRect) -> np.ndarray:
    return np.array([[rect.x, rect.y], [rect.x + rect.w, rect.y],
                     [rect.x + rect.w, rect.y + rect.h], [rect.x, rect.y + rect.h]], np.float64)


# ---------------------------------------------------------------- 行列

def compute_matrix(quad_edge, rect: CropRect, width: int, height: int) -> np.ndarray:
    """出力の UV（R の四隅）→ 入力の UV（補正前の四隅）の行列 M（M[2][2] = 1）。

    cv2.getPerspectiveTransform は float32 の点を受けるため、倍精度で 4 点から直接解く。
    """
    m = _solve_homography(normalize(rect_corners_edge(rect), width, height),
                          normalize(quad_edge, width, height))
    return m / m[2, 2]


def _solve_homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """4 点の対応 src → dst の射影行列（倍精度、h22 = 1）。"""
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    h = np.linalg.solve(np.array(a, np.float64), np.array(b, np.float64))
    return np.append(h, 1.0).reshape(3, 3)


def check_matrix(m: np.ndarray, quad_edge, rect: CropRect, width: int, height: int,
                 tol: float = 1e-6) -> None:
    if abs(np.linalg.det(m)) < 1e-9:
        raise PerspectiveError("行列が退化しています")
    back = apply(m, normalize(rect_corners_edge(rect), width, height))
    err = float(np.abs(back - normalize(quad_edge, width, height)).max())
    if err > tol:
        raise PerspectiveError(f"行列で四隅が戻りません（誤差 {err:.2e}）")


def matrix_settings(m: np.ndarray) -> dict:
    """シェーダーフィルタの設定（uniform 名 → 値）。"""
    return {k: float(np.asarray(m).flat[i]) for i, k in enumerate(MATRIX_KEYS)}


def warp_like_shader(img: np.ndarray, m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """shaders/perspective.shader と同じ計算で補正した画像と、入力の範囲内のマスク（P0 で一致を確認）。"""
    h, w = img.shape[:2]
    uu, vv = np.meshgrid((np.arange(w) + 0.5) / w, (np.arange(h) + 0.5) / h)
    x = m[0, 0] * uu + m[0, 1] * vv + m[0, 2]
    y = m[1, 0] * uu + m[1, 1] * vv + m[1, 2]
    z = m[2, 0] * uu + m[2, 1] * vv + m[2, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        sx, sy = x / z, y / z
    inside = (z > 0) & (sx >= 0) & (sx <= 1) & (sy >= 0) & (sy <= 1)
    out = cv2.remap(img, (sx * w - 0.5).astype(np.float32), (sy * h - 0.5).astype(np.float32),
                    cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    out[~inside] = 0
    return out, inside


# ---------------------------------------------------------------- 縦横比の推定（aspect_source = "markers"）

@dataclass
class AspectEstimate:
    aspect: float                    # 推定した r（= 高さ / 幅）、フレームごとの推定値の中央値
    per_frame: list[float]
    residual_max: float              # 推定した r での各マーカーの |log a_i| の最大（比率）
    residual_mean: float
    spread: float                    # フレーム間のばらつき（最大 / 最小 − 1）
    markers: int


def marker_ratio(corners_unit) -> float:
    """単位正方形へ写したマーカーの「縦 / 横」（辺を軸へ射影した長さで測る。向きに依存しない）。"""
    q = np.asarray(corners_unit, np.float64).reshape(4, 2)
    hs, vs = [], []
    for i in range(4):
        dx, dy = np.abs(q[(i + 1) % 4] - q[i])
        (hs if dx >= dy else vs).append(dx if dx >= dy else dy)
    if len(hs) != 2 or len(vs) != 2:
        raise PerspectiveError("マーカーの辺を縦横に分類できません")
    return float(np.mean(vs) / np.mean(hs))


def estimate_aspect_frame(quad_edge, markers_edge: list) -> tuple[float, list[float]]:
    """1 フレーム分。外側の四隅を単位正方形に写す変換 H_1 で各マーカーを写し、

    a_i(1) = 縦 / 横 とする。1 × r の矩形へ写す変換は H_1 のあとに縦を r 倍するだけなので、
    軸へ射影した長さでは a_i(r) = r · a_i(1)。Σ(log a_i(r))² を最小にする r は閉じた形で
    r = exp(−mean(log a_i(1)))（docs/PLAN_perspective.md 4.2 の 1 変数最適化と同じ解）。
    """
    h1 = _solve_homography(np.asarray(quad_edge, np.float64).reshape(4, 2), UNIT_SQUARE)
    logs = [math.log(marker_ratio(apply(h1, mk))) for mk in markers_edge]
    r = math.exp(-float(np.mean(logs)))
    return r, [lg + math.log(r) for lg in logs]


def estimate_aspect(frames: list[tuple]) -> AspectEstimate:
    """frames: [(外側の四隅（境界座標）, [各マーカーの 4 角（境界座標）]), ...]。"""
    if not frames:
        raise PerspectiveError("縦横比の推定に使えるフレームがありません")
    per, residuals = [], []
    for quad, markers in frames:
        r, res = estimate_aspect_frame(quad, markers)
        per.append(r)
        residuals.append(res)
    r = float(np.median(per))
    # 最終値 r での残差（全フレーム・全マーカー）
    final = [abs(lg - math.log(p) + math.log(r)) for p, res in zip(per, residuals) for lg in res]
    return AspectEstimate(r, per, float(math.expm1(max(final))), float(math.expm1(np.mean(final))),
                          float(max(per) / min(per) - 1), len(frames[0][1]))


# ---------------------------------------------------------------- まとめ

@dataclass
class PerspectiveResult:
    frame_size: tuple[int, int]
    quad: list                       # 外側の四隅（画素中心の座標、左上・右上・右下・左下）
    aspect: float
    aspect_source: str               # "target_mm" | "markers"
    rect: CropRect
    matrix: list                     # 3x3（出力の UV → 入力の UV）
    skew_deg: list[float]
    estimate: AspectEstimate | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rect"] = asdict(self.rect)
        return d


def solve(quad_px, width: int, height: int, *, target_mm: tuple[float, float] | None = None,
          aspect_source: str = "target_mm", frames_for_aspect: list | None = None,
          aspect_verify: bool = False, verify_tol: float = 0.01, residual_warn: float = 0.01,
          max_skew_warn_deg: float = 25.0) -> PerspectiveResult:
    """外側の四隅（画素中心の座標）から、補正後の矩形 R と行列 M を求める。"""
    quad_edge = to_edge(quad_px)
    check_quad(quad_edge)
    check_rotation(quad_edge)
    warnings: list[str] = []
    estimate = None
    if aspect_source == "markers" or aspect_verify:
        if not frames_for_aspect:
            raise PerspectiveError("マーカーからの縦横比の推定には、マーカーの角の情報が必要です")
        estimate = estimate_aspect(frames_for_aspect)
        if estimate.residual_max > residual_warn:
            warnings.append(f"縦横比の推定の残差が大きい（最大 {estimate.residual_max:.2%}）。"
                            "紙の反り・浮き、検出不良、レンズ歪みの可能性があります")
    if aspect_source == "target_mm":
        if not target_mm:
            raise PerspectiveError("target_mm（対象面の実寸 W, H）を指定してください")
        aspect = target_mm[1] / target_mm[0]
        if aspect_verify and estimate:
            diff = estimate.aspect / aspect - 1
            if abs(diff) > verify_tol:
                warnings.append(f"target_mm の縦横比 {aspect:.4f} とマーカーからの推定 {estimate.aspect:.4f} が "
                                f"{diff:+.2%} 違います（実寸の入力ミスや取り付けのずれの可能性）")
    elif aspect_source == "markers":
        aspect = estimate.aspect
    else:
        raise PerspectiveError(f"unknown aspect_source: {aspect_source!r}")
    skew = skew_angles(quad_edge)
    if max(skew) > max_skew_warn_deg:
        warnings.append(f"傾きが大きい（最大 {max(skew):.1f}°）。補正で画質が落ちます。カメラを正対に近づけてください")
    rect, w = target_rect(quad_edge, width, height, aspect)
    warnings += w
    m = compute_matrix(quad_edge, rect, width, height)
    check_matrix(m, quad_edge, rect, width, height)
    return PerspectiveResult((width, height), np.asarray(quad_px, float).tolist(), aspect, aspect_source,
                             rect, m.tolist(), skew, estimate, warnings)


def frames_from_detections(dets_list: list[dict], positions: dict[str, int]) -> list[tuple]:
    """detect.measure の検出結果（フレームごと）から、縦横比の推定に使う入力を作る。

    4 枚とも 1 つずつ検出されたフレームだけを使う。外側の角は 7.4 の 3 と同じく
    「4 枚の中心から最も遠い角」、四隅の順序は positions（左上・右上・右下・左下 → ID）。
    """
    from obs_autocrop.vision.detect import outer_corner_index
    from obs_autocrop.vision.markers import POSITIONS

    order = [positions[p] for p in POSITIONS]
    out = []
    for d in dets_list:
        if not all(len(d.get(i, [])) == 1 for i in order):
            continue
        mk = {i: d[i][0] for i in order}
        k = outer_corner_index(mk)
        quad = to_edge([mk[i][k[i]] for i in order])
        out.append((quad, [to_edge(mk[i]) for i in order]))
    return out
