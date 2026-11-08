"""obs-websocket v5 クライアント（obsws-python のラッパー、PLAN.md 6.2）。

注意（E1 で確認）:
- obsws-python は host / port / password をどれも渡さないと、カレントディレクトリの
  config.toml を自前で読みにいく。本ラッパーは常に明示的に渡す。
- obsws-python は INFO レベルで接続パラメータ（パスワードを含む）をログに出し、
  リクエストの失敗も例外を送出する前に ERROR でトレースバックを出す。
  例外は呼び出し側で扱うため、ロガーは CRITICAL に上げて抑止する。
- OBS の起動直後は、接続できてもリクエストが 207（NotReady）で失敗する（E2 で確認）。
  wait_ready はこれも再試行する。
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass

import obsws_python as obsws

from obs_autocrop.models import CropRect

logging.getLogger("obsws_python").setLevel(logging.CRITICAL)

NOT_READY = 207  # obs-websocket RequestStatus::NotReady


@dataclass(frozen=True)
class SceneItemRef:
    scene: str
    item_id: int


@dataclass(frozen=True)
class FilterInfo:
    index: int
    name: str
    kind: str
    enabled: bool
    settings: dict


class ObsWs:
    def __init__(self, host: str = "localhost", port: int = 4455, password: str = "",
                 timeout: float = 5):
        self.host, self.port, self._password, self.timeout = host, port, password, timeout
        self._client: obsws.ReqClient | None = None

    def __repr__(self) -> str:  # パスワードを出さない
        return f"ObsWs(host={self.host!r}, port={self.port})"

    # ------------------------------------------------------------ connection

    def connect(self) -> dict:
        self._client = obsws.ReqClient(host=self.host, port=self.port,
                                       password=self._password, timeout=self.timeout)
        try:
            return self.version()
        except Exception:
            self.disconnect()
            raise

    def wait_ready(self, timeout: float = 30, interval: float = 0.5) -> dict:
        """OBS が接続を受け付け、リクエストに応答できるようになるまで待つ。"""
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.connect()
            except obsws.error.OBSSDKRequestError as e:
                if e.code != NOT_READY or time.monotonic() >= deadline:
                    raise
            except (ConnectionError, OSError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise
            time.sleep(interval)

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.disconnect()
            self._client = None

    def __enter__(self) -> "ObsWs":
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def _req(self, request_type: str, data: dict | None = None) -> dict:
        """生のリクエストを送り、responseData を dict で返す。"""
        if self._client is None:
            raise RuntimeError("not connected")
        return self._client.send(request_type, data, raw=True) or {}

    # ------------------------------------------------------------ queries

    def version(self) -> dict:
        return self._req("GetVersion")

    def list_inputs(self, kind: str | None = None) -> list[dict]:
        return self._req("GetInputList", {"inputKind": kind} if kind else None)["inputs"]

    def list_v4l2_inputs(self) -> list[dict]:
        return self.list_inputs("v4l2_input")

    def get_input_settings(self, source: str) -> dict:
        return self._req("GetInputSettings", {"inputName": source})

    def list_filters(self, source: str) -> list[FilterInfo]:
        filters = self._req("GetSourceFilterList", {"sourceName": source})["filters"]
        return [FilterInfo(f["filterIndex"], f["filterName"], f["filterKind"],
                           f["filterEnabled"], f["filterSettings"]) for f in filters]

    def get_filter(self, source: str, filter_name: str) -> FilterInfo:
        f = self._req("GetSourceFilter", {"sourceName": source, "filterName": filter_name})
        return FilterInfo(f["filterIndex"], filter_name, f["filterKind"],
                          f["filterEnabled"], f["filterSettings"])

    def find_scene_item(self, source: str) -> SceneItemRef:
        """ソースを含む最初のシーンとシーンアイテム ID を返す。"""
        for scene in self._req("GetSceneList")["scenes"]:
            items = self._req("GetSceneItemList", {"sceneName": scene["sceneName"]})["sceneItems"]
            for it in items:
                if it["sourceName"] == source:
                    return SceneItemRef(scene["sceneName"], it["sceneItemId"])
        raise LookupError(f"source not found in any scene: {source}")

    def get_source_size(self, source: str) -> tuple[int, int]:
        ref = self.find_scene_item(source)
        t = self._req("GetSceneItemTransform",
                      {"sceneName": ref.scene, "sceneItemId": ref.item_id})["sceneItemTransform"]
        return int(t["sourceWidth"]), int(t["sourceHeight"])

    def wait_source_size(self, source: str, timeout: float = 15, interval: float = 0.5) -> tuple[int, int]:
        deadline = time.monotonic() + timeout
        while True:
            w, h = self.get_source_size(source)
            if w and h:
                return w, h
            if time.monotonic() >= deadline:
                raise TimeoutError(f"source size of {source!r} is still 0")
            time.sleep(interval)

    def screenshot(self, source: str, width: int | None = None, fmt: str = "png") -> bytes:
        """ソースのスクリーンショットを画像のバイト列で返す。width 省略時は元の解像度。"""
        data = {"sourceName": source, "imageFormat": fmt}
        if width:
            data["imageWidth"] = width
        uri = self._req("GetSourceScreenshot", data)["imageData"]
        return base64.b64decode(uri.split(",", 1)[1])

    # ------------------------------------------------------------ crop

    def get_crop(self, source: str, filter_name: str, src_w: int, src_h: int) -> CropRect:
        return CropRect.from_filter_settings(self.get_filter(source, filter_name).settings, src_w, src_h)

    def set_crop(self, source: str, filter_name: str, rect: CropRect) -> None:
        self.set_filter_settings(source, filter_name, rect.to_filter_settings(), overlay=True)

    def set_filter_settings(self, source: str, filter_name: str, settings: dict,
                            overlay: bool = True) -> None:
        self._req("SetSourceFilterSettings", {"sourceName": source, "filterName": filter_name,
                                              "filterSettings": settings, "overlay": overlay})

    def set_filter_enabled(self, source: str, filter_name: str, enabled: bool) -> None:
        self._req("SetSourceFilterEnabled", {"sourceName": source, "filterName": filter_name,
                                             "filterEnabled": enabled})
