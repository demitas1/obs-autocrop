import pytest

from obs_autocrop.models import CropRect


def test_from_relative_settings():
    s = {"left": 400, "top": 0, "bottom": 50, "right": 100, "relative": True}
    assert CropRect.from_filter_settings(s, 1920, 1080) == CropRect(400, 0, 1420, 1030)


def test_relative_is_default():
    assert CropRect.from_filter_settings({"left": 10}, 1920, 1080) == CropRect(10, 0, 1910, 1080)


def test_absolute_roundtrip():
    r = CropRect(100, 50, 1280, 720)
    assert CropRect.from_filter_settings(r.to_filter_settings(), 1920, 1080) == r


def test_clamp_inside_is_noop():
    r = CropRect(100, 50, 1280, 720)
    assert r.clamp(1920, 1080) == (r, [])


@pytest.mark.parametrize("rect, expected", [
    (CropRect(-5, -1, 100, 100), CropRect(0, 0, 100, 100)),
    (CropRect(1900, 0, 100, 100), CropRect(1900, 0, 20, 100)),
    (CropRect(0, 0, 4000, 2000), CropRect(0, 0, 1920, 1080)),
    (CropRect(0, 0, 3, 3), CropRect(0, 0, 16, 16)),
    (CropRect(1915, 1075, 100, 100), CropRect(1904, 1064, 16, 16)),
])
def test_clamp(rect, expected):
    out, warnings = rect.clamp(1920, 1080)
    assert out == expected
    assert len(warnings) == 1
