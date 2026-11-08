import base64
import json

import pytest

from obs_autocrop.models import CropRect
from obs_autocrop.obsctl import ws_client
from obs_autocrop.obsctl.ws_client import ObsWs
from obs_autocrop.platforms.linux.obs_paths import LinuxObsPaths


class FakeReqClient:
    """obsws_python.ReqClient の send(raw=True) だけを模す。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.sent = []
        self.responses = {
            "GetVersion": {"obsVersion": "32.2.0", "obsWebSocketVersion": "5.7.4", "rpcVersion": 1},
            "GetSceneList": {"scenes": [{"sceneName": "Other"}, {"sceneName": "Scene"}]},
            "GetSceneItemTransform": {"sceneItemTransform": {"sourceWidth": 1220.0, "sourceHeight": 880.0}},
            "GetSourceFilter": {"filterIndex": 0, "filterKind": "crop_filter", "filterEnabled": True,
                                "filterSettings": {"left": 450, "right": 250, "top": 0,
                                                   "bottom": 200, "relative": True}},
            "GetSourceScreenshot": {"imageData": "data:image/png;base64,"
                                    + base64.b64encode(b"PNGDATA").decode()},
        }

    def send(self, request_type, data=None, raw=False):
        assert raw
        self.sent.append((request_type, data))
        if request_type == "GetSceneItemList":
            items = [] if data["sceneName"] == "Other" else [{"sourceName": "Cam", "sceneItemId": 7}]
            return {"sceneItems": items}
        return self.responses.get(request_type)

    def disconnect(self):
        pass


@pytest.fixture
def ws(monkeypatch):
    monkeypatch.setattr(ws_client.obsws, "ReqClient", FakeReqClient)
    c = ObsWs("localhost", 4455, "secret")
    c.connect()
    return c


def test_connect_passes_explicit_params_and_hides_password(ws):
    assert ws._client.kwargs == {"host": "localhost", "port": 4455, "password": "secret", "timeout": 5}
    assert "secret" not in repr(ws)


def test_find_scene_item_and_size(ws):
    ref = ws.find_scene_item("Cam")
    assert (ref.scene, ref.item_id) == ("Scene", 7)
    assert ws.get_source_size("Cam") == (1220, 880)


def test_get_crop_from_relative(ws):
    assert ws.get_crop("Cam", "Crop/Pad", 1920, 1080) == CropRect(450, 0, 1220, 880)


def test_set_crop_sends_absolute_overlay(ws):
    ws.set_crop("Cam", "Crop/Pad", CropRect(100, 50, 1280, 720))
    req, data = ws._client.sent[-1]
    assert req == "SetSourceFilterSettings"
    assert data["overlay"] is True
    assert data["filterSettings"] == {"relative": False, "left": 100, "top": 50, "cx": 1280, "cy": 720}


def test_screenshot_decodes_data_uri(ws):
    assert ws.screenshot("Cam") == b"PNGDATA"


def test_obs_paths_websocket_config(tmp_path):
    p = tmp_path / "plugin_config/obs-websocket"
    p.mkdir(parents=True)
    (p / "config.json").write_text(json.dumps({
        "server_enabled": True, "server_port": 4455, "auth_required": True, "server_password": "pw"}))
    assert LinuxObsPaths(tmp_path).websocket_config() == {
        "enabled": True, "port": 4455, "auth_required": True, "password": "pw"}
    (p / "config.json").write_text(json.dumps({
        "server_enabled": True, "server_port": 4455, "auth_required": False, "server_password": "pw"}))
    assert LinuxObsPaths(tmp_path).websocket_config()["password"] == ""
