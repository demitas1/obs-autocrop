"""E2: シーンコレクションの読み書き（PLAN.md 12章）。

    .venv/bin/python experiments/e2_scene_file.py inspect
    .venv/bin/python experiments/e2_scene_file.py set-device --mode resolved|link|none [--write]
    .venv/bin/python experiments/e2_scene_file.py restore BACKUP [--write]
    .venv/bin/python experiments/e2_scene_file.py verify --expect DEVICE_ID [--label X]

inspect / set-device（--write なし）/ restore（--write なし）は読み取りのみ。
--write は OBS の終了中のみ実行でき、書き込む前に backups/ にバックアップを取る。
verify は OBS 起動後に websocket で device_id・ソースサイズ・映像を確認する
（E1 の残り: wait_ready / wait_source_size の所要時間も記録する）。
結果は experiments/out/e2-log.jsonl に追記する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import time
from pathlib import Path

from obs_autocrop.config import DEFAULT_CONFIG_PATH, camera_identity, load_config
from obs_autocrop.models import CameraSourceConfig, ObsRunning
from obs_autocrop.obsctl.device_id import MODES, plan_device_id
from obs_autocrop.obsctl.scene_file import SceneCollection, restore
from obs_autocrop.platforms import get_platform

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "backups"
OUT = ROOT / "experiments" / "out"
LOG = OUT / "e2-log.jsonl"


def log(rec: dict) -> None:
    OUT.mkdir(exist_ok=True)
    rec = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), **rec}
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def load_target(cfg: dict, plat):
    path = plat.obs_paths.collection_path(cfg.get("obs", {}).get("collection") or None)
    sc = SceneCollection.load(path)
    t = cfg.get("target", {})
    src = sc.find_source(t.get("source_name"), t.get("source_uuid") or None)
    return sc, src


def cmd_inspect(cfg, plat, args) -> int:
    sc, src = load_target(cfg, plat)
    adapter = plat.source_adapter
    cam = sc.get_camera_config(src, adapter)
    print(f"collection: {sc.name}  ({sc.path})")
    print(f"source    : {src['name']!r} id={src['id']} uuid={src.get('uuid')}")
    print(f"settings  : {src.get('settings')}")
    print(f"decoded   : {cam}")
    if cam.unset_fields():
        print(f"  未指定（-1 またはキーなし）: {cam.unset_fields()}")
    for i, f in enumerate(src.get("filters", [])):
        print(f"filter[{i}] {f['name']!r} id={f['id']} enabled={f.get('enabled')} {f.get('settings')}")
    resolved = plat.camera_locator.resolve(camera_identity(cfg.get("camera", {})))
    print(f"camera    : {resolved.node}  link={resolved.link}")
    for mode in MODES:
        new, warnings = plan_device_id(cam.device_ref, resolved, mode, adapter)
        print(f"  mode={mode:8} -> device_id {'(変更しない)' if new is None else new}"
              + (f"  WARN: {warnings}" if warnings else ""))
    print(f"OBS running: {plat.obs_process.is_running()}")
    return 0


def cmd_set_device(cfg, plat, args) -> int:
    sc, src = load_target(cfg, plat)
    adapter = plat.source_adapter
    cur = sc.get_camera_config(src, adapter)
    resolved = plat.camera_locator.resolve(camera_identity(cfg.get("camera", {})))
    new, warnings = plan_device_id(cur.device_ref, resolved, args.mode, adapter)
    for w in warnings:
        print(f"WARN: {w}")
    changes = sc.set_camera_config(src, CameraSourceConfig(device_ref=new), adapter) if new else []
    print(f"mode={args.mode}: device_id {cur.device_ref!r} -> "
          f"{'(変更しない)' if new is None else repr(new)}")
    if not sc.is_modified():
        print("変更なし")
        log({"cmd": "set-device", "mode": args.mode, "write": args.write, "changes": [],
             "warnings": warnings})
        return 0
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
    log({"cmd": "set-device", "mode": args.mode, "write": True, "changes": changes,
         "backup": str(backup), "warnings": warnings, "node": resolved.node, "link": resolved.link})
    return 0


def cmd_restore(cfg, plat, args) -> int:
    path = plat.obs_paths.collection_path(cfg.get("obs", {}).get("collection") or None)
    cur = SceneCollection.load(path)
    target = SceneCollection(path, json.loads(args.backup.read_text(encoding="utf-8")))
    # 現在のファイルとバックアップの差分を表示する
    cur_data, cur.data = cur.data, target.data
    cur._original = cur_data
    print(cur.diff() or "差分なし")
    if not args.write:
        print("\n(dry-run) 戻すには --write を付けてください（OBS を終了してから）")
        return 0
    try:
        pre = restore(args.backup, path, write=True, is_obs_running=plat.obs_process.is_running,
                      backup_dir=BACKUP_DIR, name=cur.name)
    except ObsRunning as e:
        print(f"ERROR: {e}")
        return 1
    print(f"restored: {path} <- {args.backup}\n現在のファイルの退避: {pre}")
    log({"cmd": "restore", "backup": str(args.backup), "pre_restore": str(pre)})
    return 0


def cmd_verify(cfg, plat, args) -> int:
    from PIL import Image, ImageStat

    from obs_autocrop.obsctl.ws_client import ObsWs

    t = cfg.get("target", {})
    source = t["source_name"]
    ws_cfg = cfg.get("obs", {}).get("websocket", {})
    password = ws_cfg.get("password") or plat.obs_paths.websocket_config()["password"]
    ws = ObsWs(ws_cfg.get("host", "localhost"), ws_cfg.get("port", 4455), password)
    t0 = time.monotonic()
    ws.wait_ready(timeout=args.timeout)
    t_ready = time.monotonic() - t0
    rec: dict = {"cmd": "verify", "label": args.label, "wait_ready_s": round(t_ready, 2)}
    with ws:
        t1 = time.monotonic()
        try:
            size = ws.wait_source_size(source, timeout=args.timeout)
            rec["wait_source_size_s"] = round(time.monotonic() - t1, 2)
            rec["source_size"] = size
        except TimeoutError as e:
            rec["wait_source_size_s"] = None
            rec["error"] = str(e)
            size = None
        dev = ws.get_input_settings(source)["inputSettings"].get("device_id")
        rec["device_id"] = dev
        rec["device_id_ok"] = dev == args.expect if args.expect else None
        if size:
            img = Image.open(io.BytesIO(ws.screenshot(source, width=640))).convert("L")
            rec["screenshot_mean_luma"] = round(ImageStat.Stat(img).mean[0], 1)
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    log(rec)
    return 0 if rec.get("device_id_ok") in (True, None) and size else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inspect")
    p = sub.add_parser("set-device")
    p.add_argument("--mode", choices=MODES, required=True)
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("restore")
    p.add_argument("backup", type=Path)
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("--expect", help="期待する device_id")
    p.add_argument("--label", default="")
    p.add_argument("--timeout", type=float, default=30)
    args = ap.parse_args()

    cfg = load_config(args.config)
    plat = get_platform(cfg)
    return {"inspect": cmd_inspect, "set-device": cmd_set_device,
            "restore": cmd_restore, "verify": cmd_verify}[args.cmd](cfg, plat, args)


if __name__ == "__main__":
    sys.exit(main())
