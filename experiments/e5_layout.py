"""シーンアイテムの自動配置（PLAN.md 14章）。

    .venv/bin/python experiments/e5_layout.py status [--anchor A]
    .venv/bin/python experiments/e5_layout.py apply --yes [--anchor A] [--transition]
    .venv/bin/python experiments/e5_layout.py restore FILE --yes [--transition]

anchor: top-left（左上を左上に）| top-right（右上を右上に）| top-center（上辺中央を上辺中央に）。
        省略時は config.toml の [layout] anchor（既定 top-left）。
status : 読み取りのみ。現在の変換・表示範囲と、配置後の値を表示する。
apply  : クロップ後のソースをアスペクト比を保ってキャンバスの内側いっぱいにし、アンカーに合わせる。
         変更前の変換を experiments/out/e5-transform-before-*.json に保存する。
         Studio モードではプレビューにのみ反映される。--transition でプログラムへ反映する
         （配信中・録画中は出力が切り替わるので注意）。
restore: 保存した変換に戻す。
結果は experiments/out/e5-log.jsonl に追記する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from obs_autocrop.config import DEFAULT_CONFIG_PATH, load_config
from obs_autocrop.obsctl.layout import ANCHORS, LayoutUnsupported, item_box, plan_layout
from obs_autocrop.obsctl.ws_client import ObsWs
from obs_autocrop.platforms import get_platform

OUT = Path(__file__).resolve().parent / "out"
LOG = OUT / "e5-log.jsonl"
KEYS = ("alignment", "positionX", "positionY", "scaleX", "scaleY")


def log(rec: dict) -> None:
    OUT.mkdir(exist_ok=True)
    rec = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), **rec}
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def connect(cfg) -> ObsWs:
    ws_cfg = cfg.get("obs", {}).get("websocket", {})
    password = ws_cfg.get("password") or get_platform(cfg).obs_paths.websocket_config()["password"]
    ws = ObsWs(ws_cfg.get("host", "localhost"), ws_cfg.get("port", 4455), password)
    ws.wait_ready(timeout=cfg.get("obs", {}).get("ready_timeout_s", 30))
    return ws


def show(t: dict) -> str:
    return ", ".join(f"{k}={t[k]:.4f}" if isinstance(t[k], float) else f"{k}={t[k]}" for k in KEYS)


def studio_notice(ws: ObsWs, transition: bool) -> bool:
    if not ws.studio_mode():
        return False
    if transition:
        ws.trigger_studio_transition()
        print("Studio モード: トランジションを実行してプログラムに反映しました")
    else:
        print("Studio モード: プレビューにのみ反映されています（「シーンを複製」が有効な場合）。"
              "プログラムへはトランジションで反映してください（--transition でも可）")
    return True


def cmd_status(cfg, args) -> int:
    source = cfg["target"]["source_name"]
    with connect(cfg) as ws:
        ref = ws.find_scene_item(source)
        t = ws.get_item_transform(ref)
        canvas = ws.canvas_size()
        studio = ws.studio_mode()
    print(f"item    : {ref.scene} / {source} (id {ref.item_id})  source {t['sourceWidth']:g}x{t['sourceHeight']:g}")
    print(f"canvas  : {canvas[0]}x{canvas[1]}  studio mode: {studio}")
    print(f"current : {show(t)}")
    try:
        print(f"          表示範囲 {item_box(t)}")
        new, box = plan_layout(t, canvas, args.anchor)
    except LayoutUnsupported as e:
        print(f"ERROR: {e}")
        return 1
    print(f"plan    : anchor={args.anchor}  {show(new)}")
    print(f"          表示範囲 {box}")
    return 0


def cmd_apply(cfg, args) -> int:
    if not args.yes:
        sys.exit("シーンアイテムの位置と拡大率を変更します。--yes を付けて実行してください")
    source = cfg["target"]["source_name"]
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    with connect(cfg) as ws:
        ref = ws.find_scene_item(source)
        t = ws.get_item_transform(ref)
        canvas = ws.canvas_size()
        try:
            new, box = plan_layout(t, canvas, args.anchor)
        except LayoutUnsupported as e:
            print(f"ERROR: {e}")
            return 1
        OUT.mkdir(exist_ok=True)
        backup = OUT / f"e5-transform-before-{stamp}.json"
        backup.write_text(json.dumps({"scene": ref.scene, "source": source, "item_id": ref.item_id,
                                      "transform": {k: t[k] for k in KEYS}}, indent=2))
        ws.set_item_transform(ref, new)
        time.sleep(0.3)
        after = ws.get_item_transform(ref)
        studio = studio_notice(ws, args.transition)
        shot = OUT / f"e5-{stamp}-scene.png"
        shot.write_bytes(ws.screenshot(ref.scene, width=1280))  # シーン（プレビュー側）
    got = item_box(after)
    ok = all(abs(a - b) < 0.5 for a, b in zip((got.x0, got.y0, got.x1, got.y1),
                                               (box.x0, box.y0, box.x1, box.y1)))
    print(f"before : {show(t)}  表示範囲 {item_box(t)}")
    print(f"after  : {show(after)}  表示範囲 {got}  {'OK' if ok else 'NG'}")
    print(f"backup : {backup}（restore で戻せます）")
    print(f"shot   : {shot}（シーンのプレビュー側）")
    log({"cmd": "apply", "anchor": args.anchor, "canvas": canvas, "before": {k: t[k] for k in KEYS},
         "after": {k: after[k] for k in KEYS}, "box": str(got), "ok": ok, "studio": studio,
         "transition": args.transition, "backup": str(backup)})
    return 0 if ok else 1


def cmd_restore(cfg, args) -> int:
    if not args.yes:
        sys.exit("シーンアイテムの変換を戻します。--yes を付けて実行してください")
    b = json.loads(args.file.read_text())
    with connect(cfg) as ws:
        ref = ws.find_scene_item(b["source"])
        ws.set_item_transform(ref, b["transform"])
        time.sleep(0.3)
        after = ws.get_item_transform(ref)
        studio_notice(ws, args.transition)
    print(f"restored: {show(after)}  表示範囲 {item_box(after)}")
    log({"cmd": "restore", "file": str(args.file), "after": {k: after[k] for k in KEYS}})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--anchor", choices=ANCHORS)
        if name == "apply":
            p.add_argument("--yes", action="store_true")
            p.add_argument("--transition", action="store_true")
    p = sub.add_parser("restore")
    p.add_argument("file", type=Path)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--transition", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if getattr(args, "anchor", "x") is None:
        args.anchor = cfg.get("layout", {}).get("anchor", "top-left")
        if args.anchor not in ANCHORS:
            sys.exit(f"[layout] anchor は {ANCHORS} のいずれかにしてください: {args.anchor!r}")
    return {"status": cmd_status, "apply": cmd_apply, "restore": cmd_restore}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
