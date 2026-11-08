#!/usr/bin/env python3
"""キャンバスのサイズを変えた後に、シーンアイテムの scale_ref を合わせる（PLAN.md 13章 E2）。

キャンバスを変えても各アイテムの scale_ref は古いまま残り、Studio モードで「シーンを複製」が
有効だと、起動直後のプログラム側の配置（拡大率）がずれる。これを修正する。

    .venv/bin/python tools/fix_scale_ref.py [--collection NAME] [--write]

既定は dry-run（差分の表示のみ）。--write は OBS の終了中のみ実行でき、
書き込む前に backups/ にバックアップを取る。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from obs_autocrop.config import DEFAULT_CONFIG_PATH, load_config
from obs_autocrop.models import ObsRunning
from obs_autocrop.obsctl.scene_file import SceneCollection, rebase_scale_ref
from obs_autocrop.platforms import get_platform

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    ap.add_argument("--collection", help="コレクション名（省略時は現在のコレクション）")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config) if args.config.exists() else {}
    plat = get_platform(cfg)
    collection = args.collection or cfg.get("obs", {}).get("collection") or None
    sc = SceneCollection.load(plat.obs_paths.collection_path(collection))
    res = sc.data["resolution"]
    print(f"collection: {sc.name} ({sc.path})  canvas {res['x']}x{res['y']}")
    try:
        changed = rebase_scale_ref(sc.data)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1
    if not changed:
        print("修正の必要はありません")
        return 0
    print("\n".join(f"  {c}" for c in changed))
    print(sc.diff())
    if not args.write:
        print("\n(dry-run) 書き込むには --write を付けてください（OBS を終了してから）")
        return 0
    try:
        backup = sc.save(write=True, is_obs_running=plat.obs_process.is_running, backup_dir=BACKUP_DIR)
    except ObsRunning as e:
        print(f"ERROR: {e}")
        return 1
    print(f"written: {sc.path}\nbackup : {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
