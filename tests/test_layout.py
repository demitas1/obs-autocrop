import pytest

from obs_autocrop.obsctl.layout import (ALIGN_TOP_LEFT, LayoutUnsupported, fit_scale, item_box,
                                        plan_layout)

# 実環境の値（E4 で計測したクロップを適用した直後、Studio モードのプレビュー側）
REAL = {"alignment": 5, "boundsType": "OBS_BOUNDS_NONE", "cropBottom": 0, "cropLeft": 0,
        "cropRight": 0, "cropTop": 0, "positionX": 2994.0, "positionY": 2160.0, "rotation": 0.0,
        "scaleX": -1.2270492315292358, "scaleY": -1.2272727489471436,
        "sourceHeight": 1649.0, "sourceWidth": 2310.0}
CANVAS = (3840, 2160)


def test_item_box_flipped_top_left_alignment():
    b = item_box(REAL)
    assert b.x1 == 2994.0 and b.y1 == 2160.0
    assert b.x0 == pytest.approx(2994 - 2310 * 1.2270492315292358)
    assert b.h == pytest.approx(1649 * 1.2272727489471436)


@pytest.mark.parametrize("align, pos", [
    (5, (100, 50)),          # 左上
    (0, (100 + 400, 50 + 300)),  # 中央
    (10, (900, 650)),        # 右下
])
def test_item_box_alignments(align, pos):
    t = {"alignment": align, "positionX": pos[0], "positionY": pos[1], "scaleX": 2.0, "scaleY": 2.0,
         "sourceWidth": 400, "sourceHeight": 300}
    b = item_box(t)
    assert (b.x0, b.y0, b.x1, b.y1) == (100, 50, 900, 650)


def test_fit_scale():
    assert fit_scale(2310, 1649, 3840, 2160) == pytest.approx(2160 / 1649)  # 縦がいっぱい
    assert fit_scale(3000, 1000, 3840, 2160) == pytest.approx(3840 / 3000)  # 横がいっぱい


@pytest.mark.parametrize("anchor, x0", [
    ("top-left", 0),
    ("top-right", 3840 - 3025),
    ("top-center", (3840 - 3025) // 2),
])
def test_plan_layout_flipped(anchor, x0):
    new, box = plan_layout(REAL, CANVAS, anchor)
    # 縦がいっぱい（2160）、横は 2310 * 2160/1649 = 3025.8 を切り捨てて 3025
    assert (box.x0, box.y0, box.w, box.h) == (x0, 0, 3025, 2160)
    assert new["scaleX"] == pytest.approx(-3025 / 2310) and new["scaleY"] == pytest.approx(-2160 / 1649)
    assert abs(new["scaleX"] / new["scaleY"] - 1) < 1 / 3025  # アスペクト比の誤差は 1px 未満
    assert new["alignment"] == ALIGN_TOP_LEFT
    assert float(new["positionX"]).is_integer() and float(new["positionY"]).is_integer()
    # 設定後の変換から求めた表示範囲が目標と一致する
    after = item_box({**REAL, **new})
    for a, b in zip((after.x0, after.y0, after.x1, after.y1), (box.x0, box.y0, box.x1, box.y1)):
        assert a == pytest.approx(b)


def test_plan_layout_positive_scale_wide_source():
    t = {"alignment": 0, "positionX": 0, "positionY": 0, "scaleX": 0.3, "scaleY": 0.3,
         "sourceWidth": 3000, "sourceHeight": 1000, "rotation": 0}
    new, box = plan_layout(t, CANVAS, "top-left")
    assert (new["positionX"], new["positionY"]) == (0, 0)
    assert box.w == pytest.approx(3840) and box.h == pytest.approx(1280)  # 横がいっぱい
    assert item_box({**t, **new}).h == pytest.approx(1280)


def test_item_crop_is_taken_into_account():
    t = {**REAL, "cropLeft": 10, "cropRight": 0, "cropTop": 49, "cropBottom": 0}
    new, box = plan_layout(t, CANVAS)
    assert box.h == pytest.approx(2160)
    assert box.w == int(2300 * 2160 / 1600)


@pytest.mark.parametrize("patch", [
    {"boundsType": "OBS_BOUNDS_SCALE_INNER"},
    {"rotation": 90.0},
    {"sourceWidth": 0},
])
def test_unsupported(patch):
    with pytest.raises(LayoutUnsupported):
        plan_layout({**REAL, **patch}, CANVAS)


def test_unknown_anchor():
    with pytest.raises(ValueError):
        plan_layout(REAL, CANVAS, "bottom-left")
