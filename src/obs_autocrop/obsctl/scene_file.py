"""シーンコレクション JSON の読み書き（PLAN.md 6.1）。OS 非依存。

- 未知のキーはすべて保持し、変更するキー以外には手を触れない（dict はキーの順序を保持する）。
- OBS と同じ形式（indent=4、末尾改行なし、ensure_ascii=False）で書き出す（E2 で確認）。
  浮動小数点は OBS が 17 桁で書くため表記だけ変わるが、値は同じ。
- 書き込みは write=True のときだけ。OBS の起動中は拒否し、書き込む前にバックアップを取る。
"""

from __future__ import annotations

import copy
import datetime as dt
import difflib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from obs_autocrop.models import CameraSourceConfig, ObsRunning
from obs_autocrop.platforms.base import CaptureSourceAdapter


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=4)


class SceneCollection:
    def __init__(self, path: Path, data: dict):
        self.path = Path(path)
        self.data = data
        self._original = copy.deepcopy(data)

    @classmethod
    def load(cls, path: Path) -> "SceneCollection":
        with open(path, encoding="utf-8") as f:
            return cls(path, json.load(f))

    @property
    def name(self) -> str:
        return self.data.get("name") or self.path.stem

    # ------------------------------------------------------------ lookup

    def find_source(self, name: str | None = None, uuid: str | None = None) -> dict:
        """uuid を指定した場合はそれを優先する（PLAN.md 4.3）。"""
        sources = self.data.get("sources", [])
        if uuid:
            hits = [s for s in sources if s.get("uuid") == uuid]
        else:
            hits = [s for s in sources if s.get("name") == name]
        if not hits:
            raise LookupError(f"source not found: {uuid or name!r}")
        return hits[0]

    @staticmethod
    def find_filter(source: dict, name: str) -> dict:
        for f in source.get("filters", []):
            if f.get("name") == name:
                return f
        raise LookupError(f"filter not found in {source.get('name')!r}: {name!r}"
                          "（OBS の画面でフィルタを追加してください）")

    # ------------------------------------------------------------ camera config

    def get_camera_config(self, source: dict, adapter: CaptureSourceAdapter) -> CameraSourceConfig:
        self._check_kind(source, adapter)
        return adapter.decode(source.get("settings", {}))

    def set_camera_config(self, source: dict, config: CameraSourceConfig,
                          adapter: CaptureSourceAdapter) -> list[tuple[str, object, object]]:
        """config のうち None でない項目を設定し、変更点 (キー, 旧, 新) を返す。"""
        self._check_kind(source, adapter)
        settings = source.setdefault("settings", {})
        changes = []
        for k, v in adapter.encode(config).items():
            old = settings.get(k)
            if old != v:
                changes.append((k, old, v))
                settings[k] = v
        return changes

    @staticmethod
    def _check_kind(source: dict, adapter: CaptureSourceAdapter) -> None:
        if source.get("id") != adapter.source_kind:
            raise ValueError(f"source {source.get('name')!r} is {source.get('id')}, "
                             f"not {adapter.source_kind}")

    # ------------------------------------------------------------ save

    def is_modified(self) -> bool:
        return self.data != self._original

    def diff(self) -> str:
        a = dumps(self._original).splitlines()
        b = dumps(self.data).splitlines()
        return "\n".join(difflib.unified_diff(a, b, f"{self.path.name} (current)",
                                              f"{self.path.name} (new)", lineterm="", n=2))

    def save(self, *, write: bool, is_obs_running: Callable[[], bool],
             backup_dir: Path) -> Path | None:
        """write=False なら何もしない（差分の表示は呼び出し側で diff() を使う）。

        write=True なら OBS が起動していないことを確認し、元のファイルをバックアップしてから、
        一時ファイルに書き出して os.replace で置き換える。バックアップのパスを返す。
        """
        if not write:
            return None
        if is_obs_running():
            raise ObsRunning("OBS が起動中です。OBS を終了してから実行してください")
        backup = backup_file(self.path, backup_dir, self.name)
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(dumps(self.data))
            shutil.copymode(self.path, tmp)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._original = copy.deepcopy(self.data)
        return backup


def backup_file(path: Path, backup_dir: Path, name: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"{name}.{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    shutil.copy2(path, dst)
    return dst


def restore(backup: Path, path: Path, *, write: bool, is_obs_running: Callable[[], bool],
            backup_dir: Path, name: str) -> Path | None:
    """バックアップから戻す。戻す前の現在のファイルもバックアップする。"""
    json.loads(backup.read_text(encoding="utf-8"))  # 壊れたファイルで上書きしない
    if not write:
        return None
    if is_obs_running():
        raise ObsRunning("OBS が起動中です。OBS を終了してから実行してください")
    pre = backup_file(path, backup_dir, name + ".pre-restore")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    shutil.copyfile(backup, tmp)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)
    return pre


def rebase_scale_ref(data: dict) -> list[str]:
    """シーンアイテムの scale_ref を現在のキャンバス（data["resolution"]）に合わせる。

    E2 で確認: キャンバスのサイズを変えても scale_ref は古いまま残り、Studio モードで
    「シーンを複製」が有効だとプログラム側の拡大率がずれる。保存値は
    scale_rel = scale × scale_ref 高 / キャンバス高 の関係にあるため、
    scale_ref をキャンバスにし、scale_rel を scale と同じ値にする。
    data をその場で変更し、変更したアイテムの説明を返す。
    """
    cw, ch = data["resolution"]["x"], data["resolution"]["y"]
    changed = []
    for s in [*data.get("sources", []), *data.get("groups", [])]:
        if s.get("id") not in ("scene", "group"):
            continue
        for it in s.get("settings", {}).get("items", []):
            ref = it.get("scale_ref")
            if not ref or (ref["x"], ref["y"]) == (cw, ch):
                continue
            label = f"{s.get('name')}/{it.get('name')}"
            k = ref["y"] / ch
            if any(abs(it["scale_rel"][a] - it["scale"][a] * k) > 1e-6 for a in ("x", "y")):
                raise ValueError(f"{label}: scale_rel が想定の関係と一致しないため修正しません")
            if it.get("bounds", {}).get("x") or it.get("bounds", {}).get("y"):
                raise ValueError(f"{label}: bounds を使うアイテムは未対応です")
            it["scale_ref"] = {"x": float(cw), "y": float(ch)}
            it["scale_rel"] = {"x": it["scale"]["x"], "y": it["scale"]["y"]}
            changed.append(f"{label}: scale_ref {ref['x']:g}x{ref['y']:g} -> {cw}x{ch}")
    return changed
