import math

import cv2
import numpy as np
import pytest

from obs_autocrop.models import CropRect
from obs_autocrop.vision.detect import make_detector, measure
from obs_autocrop.vision.homography import (PerspectiveError, apply, check_quad, check_rotation,
                                            compute_matrix, rotation_deg,
                                            estimate_aspect, frames_from_detections, marker_ratio,
                                            matrix_settings, normalize, rect_corners_edge, skew_angles,
                                            solve, target_rect, to_edge, warp_like_shader)
from obs_autocrop.vision.markers import POSITIONS, get_dictionary

DICT = get_dictionary("DICT_4X4_50")
FRAME = (1920, 1080)


# ---------------------------------------------------------------- 合成シーン

def make_plane(target_mm=(600, 340), px_per_mm=2.0, marker_mm=40, margin_px=60):
    """対象面（灰色）の四隅の内側にマーカーを置いた画像と、対象面の四隅（画素の境界の座標）。"""
    pw, ph = round(target_mm[0] * px_per_mm), round(target_mm[1] * px_per_mm)
    ms = round(marker_mm * px_per_mm)
    q = ms // 3  # quiet zone
    img = np.full((ph + 2 * margin_px, pw + 2 * margin_px), 255, np.uint8)
    x0, y0 = margin_px, margin_px
    img[y0:y0 + ph, x0:x0 + pw] = 200
    spots = {0: (x0, y0), 1: (x0 + pw - ms, y0), 2: (x0 + pw - ms, y0 + ph - ms), 3: (x0, y0 + ph - ms)}
    for i, (x, y) in spots.items():
        img[max(y - q, 0):y + ms + q, max(x - q, 0):x + ms + q] = 255
        img[y:y + ms, x:x + ms] = cv2.aruco.generateImageMarker(DICT, i, ms)
    corners = np.array([[x0, y0], [x0 + pw, y0], [x0 + pw, y0 + ph], [x0, y0 + ph]], np.float64)
    return img, corners


def project(plane, plane_corners_edge, dst_corners_edge, frame=FRAME, noise=0.0, seed=0):
    """対象面を、四隅が dst（画素の境界の座標）に来るように射影してフレームを作る。"""
    # warpPerspective は画素中心の座標で写すため、境界の座標から 0.5 引いて対応を作る
    g = cv2.getPerspectiveTransform((plane_corners_edge - 0.5).astype(np.float32),
                                    (np.asarray(dst_corners_edge) - 0.5).astype(np.float32))
    out = cv2.warpPerspective(plane, g, frame, flags=cv2.INTER_LINEAR, borderValue=128)
    out = cv2.GaussianBlur(out, (3, 3), 0.6)
    if noise:
        rng = np.random.default_rng(seed)
        out = np.clip(out + rng.normal(0, noise, out.shape), 0, 255).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)


TRAPEZOIDS = {
    "keystone": [[420, 160], [1500, 200], [1620, 930], [300, 900]],
    "rotated": [[380, 260], [1460, 150], [1560, 760], [480, 880]],
    "mild": [[400, 200], [1520, 205], [1515, 820], [405, 815]],
}


def measure_scene(frame_img, n=5):
    m, dets = measure([frame_img] * n, make_detector())
    quad = [m.corners[m.positions[p]] for p in POSITIONS]
    return m, dets, quad


# ---------------------------------------------------------------- 基本の計算

def test_identity_when_quad_is_rect():
    rect = CropRect(100, 50, 800, 450)
    m = compute_matrix(rect_corners_edge(rect), rect, *FRAME)
    assert np.allclose(m, np.eye(3), atol=1e-12)
    assert matrix_settings(m)["m00"] == pytest.approx(1.0)


def test_matrix_maps_rect_corners_back():
    quad = np.array(TRAPEZOIDS["keystone"], float)
    rect, _ = target_rect(quad, *FRAME, 340 / 600)
    m = compute_matrix(quad, rect, *FRAME)
    back = apply(m, normalize(rect_corners_edge(rect), *FRAME))
    assert np.abs(back - normalize(quad, *FRAME)).max() < 1e-9
    assert m[2, 2] == 1.0


def test_target_rect_no_downscale_and_aspect():
    quad = np.array(TRAPEZOIDS["keystone"], float)
    rect, w = target_rect(quad, *FRAME, 340 / 600)
    top, right, bottom, left = (np.linalg.norm(quad[(i + 1) % 4] - quad[i]) for i in range(4))
    assert rect.w >= int(max(top, bottom)) and rect.h >= int(max(left, right)) - 1
    assert rect.h == round(rect.w * 340 / 600)
    assert 0 <= rect.x and rect.x + rect.w <= FRAME[0] and 0 <= rect.y and rect.y + rect.h <= FRAME[1]
    assert w == []


def test_target_rect_shrinks_to_fit():
    quad = np.array([[10, 10], [1910, 10], [1910, 1070], [10, 1070]], float)
    rect, w = target_rect(quad, *FRAME, 0.9)  # 縦長にすると高さが足りない
    assert rect.h <= FRAME[1] - 128 and rect.w <= FRAME[0] - 128 and w


def test_target_rect_keeps_margin_from_frame_edges():
    # 上寄りの対象面で R を外接矩形の中心に置くと上にはみ出す → 端から 64px 離して置く（P3 で確認）
    quad = np.array([[600, 20], [1300, 25], [1400, 700], [500, 695]], float)
    rect, w = target_rect(quad, *FRAME, 1.0)
    assert rect.y == 64 and w == []
    rect0, _ = target_rect(quad, *FRAME, 1.0, margin_px=0)
    assert rect0.y == 0 and (rect0.w, rect0.h) == (rect.w, rect.h)  # 位置だけが変わる


@pytest.mark.parametrize("quad", [
    [[0, 0], [0, 100], [100, 100], [100, 0]],      # 反時計回り
    [[0, 0], [100, 100], [100, 0], [0, 100]],      # 交差
])
def test_check_quad_rejects(quad):
    with pytest.raises(PerspectiveError):
        check_quad(np.array(quad, float))


def test_skew_angles():
    assert skew_angles([[0, 0], [100, 0], [100, 50], [0, 50]]) == pytest.approx([0, 0, 0, 0])
    a = skew_angles([[0, 0], [100, 10], [100, 60], [0, 50]])
    assert a[0] == pytest.approx(math.degrees(math.atan(0.1)))


def test_marker_ratio_orientation_independent():
    sq = [[0, 0], [2, 0], [2, 1], [0, 1]]  # 横 2、縦 1
    assert marker_ratio(sq) == pytest.approx(0.5)
    assert marker_ratio(np.roll(np.array(sq), 1, axis=0)) == pytest.approx(0.5)


# ---------------------------------------------------------------- 合成画像での総合（P2 の完了条件）

@pytest.mark.parametrize("name", list(TRAPEZOIDS))
def test_rectified_corners_land_on_rect(name):
    plane, pc = make_plane()
    frame = project(plane, pc, np.array(TRAPEZOIDS[name], float), noise=2.0)
    m, dets, quad = measure_scene(frame)
    res = solve(quad, *FRAME, aspect_source="target_mm", target_mm=(600, 340))
    assert res.rect.h == round(res.rect.w * 340 / 600)
    # シェーダーと同じ計算で補正し、補正後の画像でマーカーを検出し直す
    rectified, inside = warp_like_shader(frame, np.array(res.matrix))
    m2, _, quad2 = measure_scene(rectified)
    expect = rect_corners_edge(res.rect) - 0.5  # 画素中心の座標
    err = np.abs(np.array(quad2) - expect).max()
    assert err <= 1.0, (name, err, quad2, expect.tolist())
    # 補正後のクロップ（外接矩形）は R と ±1px で一致
    for a, b in zip((m2.crop.x, m2.crop.y, m2.crop.w, m2.crop.h),
                    (res.rect.x, res.rect.y, res.rect.w, res.rect.h)):
        assert abs(a - b) <= 1


@pytest.mark.parametrize("target_mm", [(600, 340), (400, 300), (300, 400), (500, 500)])
@pytest.mark.parametrize("name", ["keystone", "rotated"])
def test_aspect_from_markers_within_half_percent(target_mm, name):
    plane, pc = make_plane(target_mm=target_mm, px_per_mm=1.6)
    dst = np.array(TRAPEZOIDS[name], float)
    if target_mm[1] > target_mm[0]:  # 縦長の対象面は縦長の台形に写す
        c = dst.mean(axis=0)
        dst = (dst - c) * [0.55, 1.0] + c
    frames = [project(plane, pc, dst, noise=2.0, seed=s) for s in range(5)]
    m, dets = measure(frames, make_detector())
    est = estimate_aspect(frames_from_detections(dets, m.positions))
    truth = target_mm[1] / target_mm[0]
    assert abs(est.aspect / truth - 1) < 0.005, (target_mm, name, est.aspect, truth)
    assert est.residual_max < 0.01


def test_solve_markers_and_verify_warning():
    plane, pc = make_plane()
    frame = project(plane, pc, np.array(TRAPEZOIDS["keystone"], float), noise=2.0)
    m, dets, quad = measure_scene(frame)
    frames = frames_from_detections(dets, m.positions)
    res = solve(quad, *FRAME, aspect_source="markers", frames_for_aspect=frames)
    assert res.aspect == pytest.approx(340 / 600, rel=0.005)
    assert res.estimate is not None and res.warnings == []
    # 実寸の入力ミス（600x400）を照合で検出する
    bad = solve(quad, *FRAME, aspect_source="target_mm", target_mm=(600, 400), aspect_verify=True,
                frames_for_aspect=frames)
    assert bad.aspect == pytest.approx(400 / 600)
    assert any("違います" in w for w in bad.warnings)


def test_solve_requires_inputs_and_warns_on_skew():
    quad = [[420, 160], [1500, 200], [1620, 930], [300, 900]]
    with pytest.raises(PerspectiveError):
        solve(quad, *FRAME, aspect_source="target_mm")  # target_mm なし
    with pytest.raises(PerspectiveError):
        solve(quad, *FRAME)  # 既定は markers。角の情報なし
    steep = [[600, 100], [1300, 100], [1800, 1000], [100, 1000]]
    res = solve(steep, *FRAME, aspect_source="target_mm", target_mm=(600, 340), max_skew_warn_deg=20)
    assert any("傾き" in w for w in res.warnings)
    assert res.to_dict()["rect"]["w"] == res.rect.w


def _rotate(quad, deg):
    q = np.array(quad, float)
    c = q.mean(axis=0)
    a = math.radians(deg)
    r = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return (q - c) @ r.T + c


BASE = [[560, 240], [1360, 240], [1360, 840], [560, 840]]


def test_rotation_guard():
    assert rotation_deg(BASE) == pytest.approx(0)
    assert rotation_deg(_rotate(BASE, 20)) == pytest.approx(20)
    check_rotation(_rotate(BASE, 30))
    with pytest.raises(PerspectiveError, match="回転"):
        check_rotation(_rotate(BASE, 40))


def test_large_rotation_is_rejected_not_misassigned():
    # 50° 回転すると四隅の割り当てが 90° ずれ、外見上は −40° になる。誤った結果を返さず中断する
    plane, pc = make_plane()
    frame = project(plane, pc, _rotate(BASE, 50), noise=2.0)
    m, dets, quad = measure_scene(frame)
    with pytest.raises(PerspectiveError, match="回転"):
        solve(quad, *FRAME, aspect_source="target_mm", target_mm=(600, 340))


@pytest.mark.parametrize("k", [0.15, 0.3, 0.45])
def test_strong_keystone_with_larger_markers(k):
    """縦横比の推定の精度はマーカーの写る大きさに依存する（P2）。約 120px なら 0.5% 以内。"""
    q = np.array(BASE, float)
    d = 800 * k / 2
    q[0, 0] += d
    q[1, 0] -= d
    plane, pc = make_plane(marker_mm=80)
    frames = [project(plane, pc, q, noise=2.0, seed=s) for s in range(5)]
    m, dets = measure(frames, make_detector())
    quad = [m.corners[m.positions[p]] for p in POSITIONS]
    res = solve(quad, *FRAME, aspect_source="markers",
                frames_for_aspect=frames_from_detections(dets, m.positions))
    assert abs(res.aspect / (340 / 600) - 1) < 0.005, res.aspect
    rectified, _ = warp_like_shader(frames[0], np.array(res.matrix))
    _, _, quad2 = measure_scene(rectified)
    assert np.abs(np.array(quad2) - (rect_corners_edge(res.rect) - 0.5)).max() <= 1.0


def test_default_is_markers_and_verify_works_in_markers_mode():
    plane, pc = make_plane()
    frame = project(plane, pc, np.array(TRAPEZOIDS["keystone"], float), noise=2.0)
    m, dets, quad = measure_scene(frame)
    frames = frames_from_detections(dets, m.positions)
    res = solve(quad, *FRAME, frames_for_aspect=frames)  # 実寸なし
    assert res.aspect_source == "markers"
    assert res.aspect == pytest.approx(340 / 600, rel=0.005)
    ok = solve(quad, *FRAME, frames_for_aspect=frames, target_mm=(600, 340), aspect_verify=True)
    assert ok.aspect == res.aspect and ok.warnings == []          # 採用はマーカーの推定値のまま
    bad = solve(quad, *FRAME, frames_for_aspect=frames, target_mm=(600, 400), aspect_verify=True)
    assert bad.aspect == res.aspect and any("違います" in w for w in bad.warnings)
