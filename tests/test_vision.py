import cv2
import numpy as np
import pytest
from PIL import Image

from obs_autocrop.models import CameraSourceConfig, CropRect
from obs_autocrop.platforms.linux.controls import parse_get_ctrl, parse_list_ctrls
from obs_autocrop.vision.capture import (CaptureError, CaptureFormat, FormatMismatch, capture,
                                         resolve_format)
from obs_autocrop.vision.detect import (MeasurementFailed, crop_from_corners, make_detector, measure,
                                        skew_warnings)
from obs_autocrop.vision.markers import (get_dictionary, make_spec, marker_tile, recommended_size_mm,
                                         render_sheet, save_markers)

DICT = get_dictionary("DICT_4X4_50")


# ---------------------------------------------------------------- markers

def test_recommended_size_mm():
    # 600x340mm を 3840x2160 で写す: max(0.15625, 0.1574) * 50 = 7.87 → 8
    assert recommended_size_mm(600, 340, 3840, 2160, 50) == 8
    assert recommended_size_mm(384, 216, 3840, 2160, 50) == 5  # ちょうど 5.0 は切り上げない


def test_make_spec_rounds_to_whole_cells():
    s = make_spec(DICT, 8, quiet_cells=2, dpi=300)
    assert s.cells == 6
    assert s.cell_px == 16  # 8mm = 94.5px → 15.75 → 16
    assert s.actual_mm == pytest.approx(96 * 25.4 / 300)
    t = marker_tile(DICT, 0, s)
    assert t.shape == (s.tile_px, s.tile_px) == (160, 160)
    assert t[0, 0] == 255 and t[32, 32] == 0  # quiet zone は白、黒枠の角は黒


def test_render_sheet_a4_and_save(tmp_path):
    s = make_spec(DICT, 30)
    pages = render_sheet(DICT, [0, 1, 2, 3], s)
    assert len(pages) == 1
    assert pages[0].size == (2480, 3508)  # A4 @300dpi
    files = save_markers(tmp_path, DICT, [0, 1, 2, 3], s)
    assert files["pdf"].read_bytes().startswith(b"%PDF")
    assert Image.open(files["id2"]).size == (s.tile_px, s.tile_px)


def test_render_sheet_paginates_large_markers():
    pages = render_sheet(DICT, [0, 1, 2, 3], make_spec(DICT, 60))  # 1 列 x 2 段
    assert len(pages) == 2


def test_generated_tile_is_detected():
    s = make_spec(DICT, 10)
    img = np.full((400, 400), 255, np.uint8)
    t = marker_tile(DICT, 3, s)
    img[50:50 + t.shape[0], 60:60 + t.shape[1]] = t
    corners, ids, _ = make_detector().detectMarkers(img)
    assert ids is not None and ids.flatten().tolist() == [3]


# ---------------------------------------------------------------- detect

S = 60
POS = {0: (100, 80), 1: (1700 - S, 80), 2: (1700 - S, 900 - S), 3: (100, 900 - S)}


def synth(pos=POS, rotate: dict | None = None, size=(1920, 1080)) -> np.ndarray:
    img = np.full((size[1], size[0]), 255, np.uint8)
    for i, (x, y) in pos.items():
        m = cv2.aruco.generateImageMarker(DICT, i, S)
        if rotate and i in rotate:
            m = np.rot90(m, rotate[i])
        img[y:y + S, x:x + S] = m
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


@pytest.mark.parametrize("blur", [False, True])
def test_measure_exact_outer_edges(blur):
    f = synth()
    if blur:
        f = cv2.GaussianBlur(f, (5, 5), 1.0)
    m, dets = measure([f] * 5, make_detector())
    # 黒枠の外縁: 左 100、上 80、右 1700（排他）、下 900（排他）
    assert m.crop == CropRect(100, 80, 1600, 820)
    assert m.frames_valid == 5 and m.warnings == []
    assert len(dets) == 5


def test_measure_margin_and_to_dict():
    m, _ = measure([synth()] * 5, make_detector(), margin_px=10)
    assert m.crop == CropRect(90, 70, 1620, 840)
    d = m.to_dict()
    assert d["crop"] == {"x": 90, "y": 70, "w": 1620, "h": 840}
    assert set(d["corners"]) == {"0", "1", "2", "3"}


def test_measure_fails_when_marker_missing():
    pos = dict(POS)
    del pos[2]
    with pytest.raises(MeasurementFailed, match="4 枚揃った"):
        measure([synth(pos)] * 5, make_detector())


def test_measure_min_valid():
    good, bad = synth(), synth({k: v for k, v in POS.items() if k != 1})
    m, _ = measure([good] * 5 + [bad] * 5, make_detector(), min_valid=5)
    assert m.frames_valid == 5 and m.frames_total == 10
    with pytest.raises(MeasurementFailed):
        measure([good] * 4 + [bad] * 6, make_detector(), min_valid=5)


def test_rotation_and_id_placement_do_not_matter():
    # マーカーを回転し、ID の置き場所も入れ替える（カメラが 180° 回転している場合など）
    pos = {2: POS[0], 3: POS[1], 0: POS[2], 1: POS[3]}
    m, _ = measure([synth(pos, rotate={0: 1, 1: 2, 2: 3})] * 5, make_detector())
    assert m.crop == CropRect(100, 80, 1600, 820)
    assert m.positions == {"top-left": 2, "top-right": 3, "bottom-right": 0, "bottom-left": 1}
    assert m.warnings == []


def test_whole_image_rotated_180():
    f = cv2.rotate(synth(), cv2.ROTATE_180)
    m, _ = measure([f] * 5, make_detector())
    # 1920x1080 の 180° 回転: 外縁は 左 1920-1700, 上 1080-900
    assert m.crop == CropRect(220, 180, 1600, 820)


def test_crop_from_corners_clamps():
    rect, w = crop_from_corners([(-3.0, -3.0), (99.5, -3.0), (99.5, 49.5), (-3.0, 49.5)], 80, 40)
    assert rect == CropRect(0, 0, 80, 40)
    assert w


def test_skew_warnings():
    rect = CropRect(0, 0, 1000, 500)
    pts = [(0, 0), (1000, 0), (1000, 500), (0, 500)]
    assert skew_warnings(pts, rect, 0.01) == []
    pts[3] = (20, 500)  # 左辺が 20px 傾く（許容 10px）
    assert len(skew_warnings(pts, rect, 0.01)) == 1


# ---------------------------------------------------------------- capture

def test_resolve_format():
    fmt, w = resolve_format(CameraSourceConfig("/dev/video0", 3840, 2160, "MJPG", 30.0), {})
    assert fmt == CaptureFormat(3840, 2160, "MJPG", 30.0) and w == []
    fmt, w = resolve_format(CameraSourceConfig("/dev/video0", None, None, "MJPG", None),
                            {"width": 1920, "height": 1080, "fourcc": "YUYV", "fps": 30})
    assert fmt == CaptureFormat(1920, 1080, "MJPG", 30.0)
    assert len(w) == 3
    with pytest.raises(ValueError):
        resolve_format(CameraSourceConfig(), {})


class FakeCap:
    def __init__(self, w, h, fourcc="MJPG", opened=True, frame_size=None):
        self.props = {cv2.CAP_PROP_FRAME_WIDTH: w, cv2.CAP_PROP_FRAME_HEIGHT: h,
                      cv2.CAP_PROP_FOURCC: float(cv2.VideoWriter_fourcc(*fourcc))}
        self.opened = opened
        self.frame_size = frame_size or (w, h)
        self.reads = 0
        self.released = False
        self.set_order = []

    def isOpened(self):
        return self.opened

    def set(self, k, v):
        self.set_order.append(k)
        return True

    def get(self, k):
        return self.props.get(k, 0.0)

    def read(self):
        self.reads += 1
        w, h = self.frame_size
        return True, np.zeros((h, w, 3), np.uint8)

    def release(self):
        self.released = True


class FakeBackend:
    def __init__(self, cap):
        self.cap = cap

    def open(self, node):
        return self.cap


def test_capture_ok_order_warmup_release():
    cap = FakeCap(64, 32)
    frames = capture(FakeBackend(cap), "/dev/x", CaptureFormat(64, 32, "MJPG", 30), n=3, warmup=5)
    assert len(frames) == 3 and cap.reads == 8 and cap.released
    assert cap.set_order == [cv2.CAP_PROP_FOURCC, cv2.CAP_PROP_FRAME_WIDTH,
                             cv2.CAP_PROP_FRAME_HEIGHT, cv2.CAP_PROP_FPS]


@pytest.mark.parametrize("cap, exc", [
    (FakeCap(64, 32, opened=False), CaptureError),
    (FakeCap(32, 32), FormatMismatch),
    (FakeCap(64, 32, fourcc="YUYV"), FormatMismatch),
    (FakeCap(64, 32, frame_size=(32, 16)), FormatMismatch),
])
def test_capture_errors_release(cap, exc):
    with pytest.raises(exc):
        capture(FakeBackend(cap), "/dev/x", CaptureFormat(64, 32, "MJPG"), n=1, warmup=0)
    assert cap.released


# ---------------------------------------------------------------- controls (v4l2-ctl の出力)

LIST = """
User Controls

                     brightness 0x00980900 (int)    : min=1 max=64 step=1 default=32 value=32 flags=0x00001000
           power_line_frequency 0x00980918 (menu)   : min=0 max=2 default=1 value=2 (60 Hz)
Camera Controls

                 focus_absolute 0x009a090a (int)    : min=0 max=1023 step=1 default=512 value=400 flags=inactive, 0x00001000
     focus_automatic_continuous 0x009a090c (bool)   : default=1 value=1
"""


def test_parse_list_ctrls():
    c = parse_list_ctrls(LIST)
    assert c["brightness"] == {"type": "int", "min": 1, "max": 64, "step": 1, "default": 32,
                               "value": 32, "flags": "0x00001000", "inactive": False}
    assert c["power_line_frequency"]["value"] == 2
    assert c["focus_absolute"]["inactive"] is True
    assert c["focus_automatic_continuous"] == {"type": "bool", "default": 1, "value": 1,
                                               "inactive": False}


def test_parse_get_ctrl():
    assert parse_get_ctrl("focus_absolute: 400\nzoom_absolute: 0\n") == {
        "focus_absolute": 400, "zoom_absolute": 0}
