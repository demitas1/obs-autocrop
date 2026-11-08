import json
import shutil
from pathlib import Path

import pytest

from obs_autocrop.models import CameraInfo, CameraSourceConfig, ObsRunning, ResolvedCamera
from obs_autocrop.obsctl.device_id import plan_device_id
from obs_autocrop.obsctl.scene_file import SceneCollection, dumps, restore
from obs_autocrop.platforms.linux.v4l2_source import V4L2SourceAdapter, fourcc_to_int, int_to_fourcc

FIXTURE = Path(__file__).parent / "fixtures" / "scene_collection.json"
CAM = "Video Capture Device (V4L2)"
CAM_UUID = "183abde5-3a1c-49d4-803f-11b2024f6cd0"
adapter = V4L2SourceAdapter()


@pytest.fixture
def scene_path(tmp_path):
    p = tmp_path / "scenes" / "Untitled.json"
    p.parent.mkdir()
    shutil.copy(FIXTURE, p)
    return p


# ---------------------------------------------------------------- v4l2 decode / encode

@pytest.mark.parametrize("settings, expected", [
    ({"device_id": "/dev/video0", "pixelformat": 1196444237},
     CameraSourceConfig("/dev/video0", None, None, "MJPG", None)),
    ({"device_id": "/dev/video0", "pixelformat": -1, "resolution": -1, "framerate": -1},
     CameraSourceConfig("/dev/video0", None, None, None, None)),
    # E1 で確認した OBS 32 の値
    ({"device_id": "/dev/video0", "pixelformat": 1196444237,
      "resolution": 16492674418800, "framerate": 4294967326},
     CameraSourceConfig("/dev/video0", 3840, 2160, "MJPG", 30.0)),
    ({"resolution": 8246337209400, "framerate": 4294967356},
     CameraSourceConfig(None, 1920, 1080, None, 60.0)),
])
def test_decode(settings, expected):
    assert adapter.decode(settings) == expected


def test_encode_roundtrip_and_none_is_skipped():
    cfg = CameraSourceConfig("/dev/v4l/by-id/x", 3840, 2160, "MJPG", 30)
    enc = adapter.encode(cfg)
    assert enc == {"device_id": "/dev/v4l/by-id/x", "resolution": 16492674418800,
                   "pixelformat": 1196444237, "framerate": 4294967326}
    assert adapter.decode(enc) == CameraSourceConfig("/dev/v4l/by-id/x", 3840, 2160, "MJPG", 30.0)
    assert adapter.encode(CameraSourceConfig(device_ref="/dev/video2")) == {"device_id": "/dev/video2"}


def test_encode_ntsc_fps():
    enc = adapter.encode(CameraSourceConfig(fps=30000 / 1001))
    assert enc["framerate"] == (1001 << 32) | 30000


def test_fourcc():
    assert int_to_fourcc(1196444237) == "MJPG"
    assert fourcc_to_int("YUYV") == 1448695129
    with pytest.raises(ValueError):
        fourcc_to_int("MJPEG")


def test_encode_requires_both_dimensions():
    with pytest.raises(ValueError):
        adapter.encode(CameraSourceConfig(width=1920))


# ---------------------------------------------------------------- scene collection

def test_find_source_by_name_and_uuid(scene_path):
    sc = SceneCollection.load(scene_path)
    assert sc.find_source(CAM)["id"] == "v4l2_input"
    assert sc.find_source(name="wrong", uuid=CAM_UUID)["name"] == CAM
    with pytest.raises(LookupError):
        sc.find_source("nope")


def test_find_filter(scene_path):
    sc = SceneCollection.load(scene_path)
    src = sc.find_source(CAM)
    assert sc.find_filter(src, "Crop/Pad")["id"] == "crop_filter"
    with pytest.raises(LookupError, match="フィルタを追加"):
        sc.find_filter(src, "Missing")


def test_get_camera_config_absent_keys(scene_path):
    sc = SceneCollection.load(scene_path)
    cfg = sc.get_camera_config(sc.find_source(CAM), adapter)
    assert cfg == CameraSourceConfig("/dev/video0", None, None, "MJPG", None)
    assert cfg.unset_fields() == ["width", "height", "fps"]


def test_wrong_source_kind(scene_path):
    sc = SceneCollection.load(scene_path)
    with pytest.raises(ValueError):
        sc.get_camera_config(sc.find_source("Image"), adapter)


def test_set_camera_config_changes_and_diff(scene_path):
    sc = SceneCollection.load(scene_path)
    src = sc.find_source(CAM)
    changes = sc.set_camera_config(
        src, CameraSourceConfig("/dev/v4l/by-id/cam", 3840, 2160, "MJPG", 30), adapter)
    assert [c[0] for c in changes] == ["device_id", "resolution", "framerate"]  # pixelformat は同じ
    assert sc.is_modified()
    d = sc.diff()
    assert any(l.startswith("+") and '"device_id": "/dev/v4l/by-id/cam"' in l for l in d.splitlines())
    assert '"resolution": 16492674418800' in d


def test_save_dry_run_does_nothing(scene_path, tmp_path):
    before = scene_path.read_bytes()
    sc = SceneCollection.load(scene_path)
    sc.set_camera_config(sc.find_source(CAM), CameraSourceConfig(device_ref="/dev/video9"), adapter)
    assert sc.save(write=False, is_obs_running=lambda: False, backup_dir=tmp_path / "b") is None
    assert scene_path.read_bytes() == before
    assert not (tmp_path / "b").exists()


def test_save_refuses_when_obs_running(scene_path, tmp_path):
    before = scene_path.read_bytes()
    sc = SceneCollection.load(scene_path)
    sc.set_camera_config(sc.find_source(CAM), CameraSourceConfig(device_ref="/dev/video9"), adapter)
    with pytest.raises(ObsRunning):
        sc.save(write=True, is_obs_running=lambda: True, backup_dir=tmp_path / "b")
    assert scene_path.read_bytes() == before


def test_save_writes_backup_and_preserves_unknown_keys(scene_path, tmp_path):
    before = scene_path.read_bytes()
    original = json.loads(before)
    sc = SceneCollection.load(scene_path)
    sc.set_camera_config(sc.find_source(CAM), CameraSourceConfig(device_ref="/dev/video9"), adapter)
    backup = sc.save(write=True, is_obs_running=lambda: False, backup_dir=tmp_path / "b")

    assert backup.read_bytes() == before
    assert backup.name.startswith("Untitled.")
    written = json.loads(scene_path.read_text())
    # device_id 以外は元と同じ（未知のキーも順序も保持）
    original["sources"][0]["settings"]["device_id"] = "/dev/video9"
    assert written == original
    assert list(written) == list(original)
    assert list(written["sources"][0]["settings"]) == list(original["sources"][0]["settings"])
    # OBS と同じ形式
    assert scene_path.read_text() == dumps(written)
    assert not list(scene_path.parent.glob(".*"))  # 一時ファイルが残っていない


def test_unchanged_save_is_byte_identical(scene_path, tmp_path):
    before = scene_path.read_bytes()
    SceneCollection.load(scene_path).save(write=True, is_obs_running=lambda: False,
                                          backup_dir=tmp_path / "b")
    assert scene_path.read_bytes() == before


def test_restore(scene_path, tmp_path):
    before = scene_path.read_bytes()
    sc = SceneCollection.load(scene_path)
    sc.set_camera_config(sc.find_source(CAM), CameraSourceConfig(device_ref="/dev/video9"), adapter)
    backup = sc.save(write=True, is_obs_running=lambda: False, backup_dir=tmp_path / "b")
    assert scene_path.read_bytes() != before

    with pytest.raises(ObsRunning):
        restore(backup, scene_path, write=True, is_obs_running=lambda: True,
                backup_dir=tmp_path / "b", name="Untitled")
    pre = restore(backup, scene_path, write=True, is_obs_running=lambda: False,
                  backup_dir=tmp_path / "b", name="Untitled")
    assert scene_path.read_bytes() == before
    assert json.loads(pre.read_text())["sources"][0]["settings"]["device_id"] == "/dev/video9"


# ---------------------------------------------------------------- device_id modes

RESOLVED = ResolvedCamera(node="/dev/video0", link="/dev/v4l/by-id/usb-cam-video-index0",
                          info=CameraInfo(node="/dev/video0", name="cam"))


def test_device_id_modes(monkeypatch):
    assert plan_device_id("/dev/video2", RESOLVED, "resolved", adapter) == ("/dev/video0", [])
    assert plan_device_id("/dev/video2", RESOLVED, "link", adapter) == (RESOLVED.link, [])
    monkeypatch.setattr("os.path.realpath", lambda p: "/dev/video0" if "by-id" in p else p)
    assert plan_device_id(RESOLVED.link, RESOLVED, "none", adapter) == (None, [])
    new, warnings = plan_device_id("/dev/video2", RESOLVED, "none", adapter)
    assert new is None and len(warnings) == 1
    with pytest.raises(ValueError):
        plan_device_id("/dev/video0", RESOLVED, "bogus", adapter)


# ---------------------------------------------------------------- scale_ref

def test_rebase_scale_ref(scene_path):
    from obs_autocrop.obsctl.scene_file import rebase_scale_ref

    sc = SceneCollection.load(scene_path)
    items = sc.find_source("Scene")["settings"]["items"]
    # フィクスチャはキャンバス 4K、scale_ref 1920x1080 の状態（E2 で修正する前の実ファイル）
    assert sc.data["resolution"] == {"x": 3840, "y": 2160}
    assert all(it["scale_ref"] == {"x": 1920.0, "y": 1080.0} for it in items)
    changed = rebase_scale_ref(sc.data)
    assert len(changed) == 2
    for it in items:
        assert it["scale_ref"] == {"x": 3840.0, "y": 2160.0}
        assert it["scale_rel"] == it["scale"]
    assert rebase_scale_ref(sc.data) == []  # 2 回目は何もしない


def test_rebase_scale_ref_refuses_unexpected(scene_path):
    from obs_autocrop.obsctl.scene_file import rebase_scale_ref

    sc = SceneCollection.load(scene_path)
    sc.find_source("Scene")["settings"]["items"][0]["scale_rel"]["x"] = 0.1
    with pytest.raises(ValueError):
        rebase_scale_ref(sc.data)
