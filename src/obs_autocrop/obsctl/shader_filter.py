"""射影補正のシェーダーフィルタの操作（PLAN.md 15章、docs/PLAN_perspective.md 6章）。OS 非依存。

P0 で確認したこと:
- フィルタの種別 ID は `shader_filter`（obs-shaderfilter 2.6.1）。
- uniform（m00〜m22、outside_black）は設定の同名のキーで受け取り、変更は即座に反映される。
- `shader_text` を websocket で書き換えても再コンパイルされない。本文を変えるときはフィルタを作り直す。
- コンパイルに失敗すると設定の `last_error` にエラー文が入り、映像は素通しになる。

シェーダーの本文はリポジトリの shaders/perspective.shader を正とし、インラインで書き込む（15.4 の 3）。
1 行目の版の印（`// obs-autocrop-perspective vN`）で OBS 側の本文が古いかどうかを判定する。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from obs_autocrop.vision.homography import MATRIX_KEYS, matrix_settings

KIND = "shader_filter"
VERSION_PREFIX = "// obs-autocrop-perspective"
DEFAULT_SHADER = Path(__file__).resolve().parents[3] / "shaders" / "perspective.shader"


class ShaderFilterError(RuntimeError):
    pass


def load_shader(path: Path | str | None = None) -> str:
    p = Path(path) if path else DEFAULT_SHADER
    text = p.read_text(encoding="utf-8")
    if shader_version(text) is None:
        raise ShaderFilterError(f"{p} の 1 行目に版の印（{VERSION_PREFIX} vN）がありません")
    return text


def shader_version(text: str | None) -> str | None:
    if not text:
        return None
    first = text.lstrip("﻿").splitlines()[0].strip() if text.strip() else ""
    return first[len(VERSION_PREFIX):].strip() if first.startswith(VERSION_PREFIX) else None


@dataclass
class FilterStatus:
    exists: bool
    index: int | None = None
    enabled: bool | None = None
    kind: str | None = None
    version: str | None = None          # OBS 側の本文の版
    expected_version: str | None = None  # リポジトリの本文の版
    last_error: str | None = None
    matrix: list | None = None
    outside_black: bool | None = None

    @property
    def up_to_date(self) -> bool:
        return self.exists and self.version == self.expected_version


def check_filter_order(filters, crop_name: str, perspective_name: str | None) -> list[str]:
    """整合チェック（PLAN.md 9.2 の 3、15.2 の 5）。問題の一覧を返す。

    - 射影補正を使わない場合（perspective_name が None）: Crop/Pad より前に有効なフィルタがないこと。
    - 使う場合: 射影補正のフィルタ（shader_filter）が先頭にあり、Crop/Pad より前にほかの有効な
      フィルタがないこと。射影補正のフィルタが無効でもよい（補正のオン・オフは有効・無効で切り替える）。
    """
    crop = next((f for f in filters if f.name == crop_name), None)
    if crop is None:
        return [f"フィルタ {crop_name!r} がありません"]
    problems = []
    before = [f for f in filters if f.index < crop.index]
    if perspective_name is None:
        others = [f for f in before if f.enabled]
    else:
        persp = next((f for f in filters if f.name == perspective_name), None)
        if persp is None:
            problems.append(f"射影補正のフィルタ {perspective_name!r} がありません")
        else:
            if persp.kind != KIND:
                problems.append(f"{perspective_name!r} の種別が {persp.kind}（{KIND} ではない）")
            if persp.index != min(f.index for f in filters):
                problems.append(f"{perspective_name!r} が先頭にありません（index {persp.index}）")
            if persp.index > crop.index:
                problems.append(f"{perspective_name!r} が {crop_name!r} より後ろにあります")
        others = [f for f in before if f.enabled and f.name != perspective_name]
    if others:
        problems.append(f"{crop_name!r} より前に有効なフィルタがあります: {[f.name for f in others]}")
    return problems


class PerspectiveFilter:
    """カメラのソースに付けた射影補正フィルタ。ws は ObsWs（または同じメソッドを持つもの）。"""

    def __init__(self, ws, source: str, name: str = "Perspective", shader_text: str | None = None,
                 compile_wait_s: float = 1.0):
        self.ws = ws
        self.source = source
        self.name = name
        self.shader_text = shader_text if shader_text is not None else load_shader()
        self.compile_wait_s = compile_wait_s

    def _find(self):
        return next((f for f in self.ws.list_filters(self.source) if f.name == self.name), None)

    def status(self) -> FilterStatus:
        f = self._find()
        expected = shader_version(self.shader_text)
        if f is None:
            return FilterStatus(False, expected_version=expected)
        s = f.settings
        m = None
        if all(k in s for k in MATRIX_KEYS):
            m = np.array([s[k] for k in MATRIX_KEYS], float).reshape(3, 3).tolist()
        return FilterStatus(True, f.index, f.enabled, f.kind, shader_version(s.get("shader_text")),
                            expected, s.get("last_error") or None, m, bool(s.get("outside_black", False)))

    def install(self, *, enabled: bool = True, matrix=None, outside_black: bool = False) -> FilterStatus:
        """フィルタを作り、先頭に移す。既にあれば何もしない。コンパイルに失敗したら削除して例外。

        シーンコレクションが変わるため、呼び出し側でユーザーの確認を取ること。
        """
        if self._find() is not None:
            raise ShaderFilterError(f"{self.source!r} に {self.name!r} は既にあります")
        m = np.eye(3) if matrix is None else np.asarray(matrix, float)
        settings = {"shader_text": self.shader_text, **matrix_settings(m), "outside_black": outside_black}
        self.ws.create_filter(self.source, self.name, KIND, settings)
        try:
            self.ws.set_filter_index(self.source, self.name, 0)
            self.ws.set_filter_enabled(self.source, self.name, enabled)
            st = self._wait_compiled()
        except BaseException:
            self.ws.remove_filter(self.source, self.name)
            raise
        return st

    def _wait_compiled(self) -> FilterStatus:
        deadline = time.monotonic() + self.compile_wait_s
        while True:
            st = self.status()
            if st.last_error:
                raise ShaderFilterError(f"シェーダーのコンパイルに失敗しました: {st.last_error.strip()}")
            if time.monotonic() >= deadline:
                return st
            time.sleep(0.1)

    def reinstall(self) -> FilterStatus:
        """本文が古い場合に作り直す（行列・有効状態・outside_black は引き継ぐ）。"""
        st = self.status()
        if not st.exists:
            raise ShaderFilterError(f"{self.name!r} がありません")
        self.ws.remove_filter(self.source, self.name)
        return self.install(enabled=bool(st.enabled), matrix=st.matrix, outside_black=bool(st.outside_black))

    def require_ready(self) -> FilterStatus:
        """行列を設定する前の確認。フィルタがない・本文が古い・エラーのときは例外。"""
        st = self.status()
        if not st.exists:
            raise ShaderFilterError(f"{self.source!r} に射影補正のフィルタ {self.name!r} がありません"
                                    "（install で追加してください）")
        if st.kind != KIND:
            raise ShaderFilterError(f"{self.name!r} の種別が {st.kind}（{KIND} ではない）")
        if not st.up_to_date:
            raise ShaderFilterError(f"{self.name!r} のシェーダーの版が {st.version}（期待 {st.expected_version}）。"
                                    "reinstall で作り直してください")
        if st.last_error:
            raise ShaderFilterError(f"{self.name!r} のシェーダーにエラーがあります: {st.last_error.strip()}")
        return st

    def set_matrix(self, m) -> None:
        self.ws.set_filter_settings(self.source, self.name, matrix_settings(np.asarray(m, float)))

    def identity(self) -> None:
        self.set_matrix(np.eye(3))

    def set_enabled(self, enabled: bool) -> None:
        self.ws.set_filter_enabled(self.source, self.name, enabled)

    def set_outside_black(self, black: bool) -> None:
        self.ws.set_filter_settings(self.source, self.name, {"outside_black": black})

    def remove(self) -> None:
        self.ws.remove_filter(self.source, self.name)
