import numpy as np
import pytest

from obs_autocrop.obsctl.shader_filter import (KIND, PerspectiveFilter, ShaderFilterError,
                                               check_filter_order, load_shader, shader_version)
from obs_autocrop.obsctl.ws_client import FilterInfo


def test_repo_shader_has_version():
    text = load_shader()
    assert shader_version(text) == "v1"
    for k in ("m00", "m22", "outside_black", "mainImage"):
        assert k in text


@pytest.mark.parametrize("text, v", [
    ("// obs-autocrop-perspective v2\nfoo", "v2"),
    ("﻿// obs-autocrop-perspective v3\n", "v3"),
    ("// something else\n", None),
    ("", None),
    (None, None),
])
def test_shader_version(text, v):
    assert shader_version(text) == v


class FakeWs:
    """ObsWs のうち PerspectiveFilter が使うメソッドだけを持つ偽物（状態は dict で持つ）。"""

    def __init__(self, filters=None, compile_error=None):
        self.filters = [dict(name=f.name, kind=f.kind, enabled=f.enabled, settings=dict(f.settings))
                        for f in (filters or [])]
        self.compile_error = compile_error
        self.calls = []

    def _get(self, name):
        return next(f for f in self.filters if f["name"] == name)

    def list_filters(self, source):
        return [FilterInfo(i, f["name"], f["kind"], f["enabled"], dict(f["settings"]))
                for i, f in enumerate(self.filters)]

    def create_filter(self, source, name, kind, settings):
        self.calls.append(("create", name))
        s = dict(settings)
        if self.compile_error:
            s["last_error"] = self.compile_error
        self.filters.append(dict(name=name, kind=kind, enabled=True, settings=s))

    def set_filter_index(self, source, name, index):
        self.calls.append(("index", name, index))
        f = self._get(name)
        self.filters.remove(f)
        self.filters.insert(index, f)

    def set_filter_enabled(self, source, name, enabled):
        self._get(name)["enabled"] = enabled

    def set_filter_settings(self, source, name, settings, overlay=True):
        self._get(name)["settings"].update(settings)

    def remove_filter(self, source, name):
        self.calls.append(("remove", name))
        self.filters = [f for f in self.filters if f["name"] != name]


def crop(enabled=True):
    return FilterInfo(0, "Crop/Pad", "crop_filter", enabled, {})


def color():
    return FilterInfo(1, "Color Correction", "color_filter_v2", True, {})


def test_install_puts_filter_first_with_identity():
    ws = FakeWs([crop(), color()])
    pf = PerspectiveFilter(ws, "Cam", compile_wait_s=0)
    st = pf.install()
    assert [f.name for f in ws.list_filters("Cam")] == ["Perspective", "Crop/Pad", "Color Correction"]
    assert st.exists and st.index == 0 and st.enabled and st.kind == KIND
    assert st.up_to_date and st.version == "v1"
    assert np.allclose(st.matrix, np.eye(3))
    assert pf.require_ready().exists
    with pytest.raises(ShaderFilterError, match="既に"):
        pf.install()


def test_install_compile_error_removes_filter():
    ws = FakeWs([crop()], compile_error="error C1503: undefined variable")
    pf = PerspectiveFilter(ws, "Cam", compile_wait_s=0)
    with pytest.raises(ShaderFilterError, match="コンパイル"):
        pf.install()
    assert [f.name for f in ws.list_filters("Cam")] == ["Crop/Pad"]


def test_outdated_version_requires_reinstall_and_keeps_state():
    ws = FakeWs([crop()])
    old = PerspectiveFilter(ws, "Cam", shader_text="// obs-autocrop-perspective v0\nold", compile_wait_s=0)
    old.install(enabled=False)
    m = np.array([[1, 0, 0.1], [0, 1, 0], [0, 0, 1]], float)
    old.set_matrix(m)
    pf = PerspectiveFilter(ws, "Cam", compile_wait_s=0)
    st = pf.status()
    assert st.version == "v0" and not st.up_to_date
    with pytest.raises(ShaderFilterError, match="版"):
        pf.require_ready()
    st = pf.reinstall()
    assert st.up_to_date and st.index == 0 and st.enabled is False
    assert np.allclose(st.matrix, m)


def test_require_ready_missing():
    with pytest.raises(ShaderFilterError, match="install"):
        PerspectiveFilter(FakeWs([crop()]), "Cam").require_ready()


def P(index=0, enabled=True, kind=KIND):
    return FilterInfo(index, "Perspective", kind, enabled, {})


@pytest.mark.parametrize("filters, persp, n_problems", [
    ([crop(), color()], None, 0),                                     # 従来どおり
    ([P(0), FilterInfo(1, "Crop/Pad", "crop_filter", True, {})], None, 1),  # 射影補正を使わないのに前段がある
    ([P(0), FilterInfo(1, "Crop/Pad", "crop_filter", True, {})], "Perspective", 0),
    ([P(0, enabled=False), FilterInfo(1, "Crop/Pad", "crop_filter", True, {})], "Perspective", 0),
    ([crop(), color()], "Perspective", 1),                            # 射影補正のフィルタがない
    ([FilterInfo(0, "Crop/Pad", "crop_filter", True, {}), P(1)], "Perspective", 2),  # 順序が逆
    ([P(0, kind="color_filter_v2"), FilterInfo(1, "Crop/Pad", "crop_filter", True, {})], "Perspective", 1),
    ([P(0), FilterInfo(1, "Sharpen", "sharpness_filter_v2", True, {}),
      FilterInfo(2, "Crop/Pad", "crop_filter", True, {})], "Perspective", 1),  # 間にほかのフィルタ
])
def test_check_filter_order(filters, persp, n_problems):
    assert len(check_filter_order(filters, "Crop/Pad", persp)) == n_problems
