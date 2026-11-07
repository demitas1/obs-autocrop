"""E3: カメラの恒久識別（PLAN.md 12章）。

読み取り専用。カメラを開かないので OBS の起動中でも実行できる。

    .venv/bin/python experiments/e3_devices.py list
    .venv/bin/python experiments/e3_devices.py check --label "baseline"
    .venv/bin/python experiments/e3_devices.py summary

check は config.toml の [camera] に書いた by_id / by_path / vendor_id+product_id を
それぞれ単独で解決し、結果を experiments/out/e3-log.jsonl に追記する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from obs_autocrop.config import DEFAULT_CONFIG_PATH, camera_identities, load_config
from obs_autocrop.models import CameraAmbiguous, CameraNotFound
from obs_autocrop.platforms import get_platform

LOG = Path(__file__).resolve().parent / "out" / "e3-log.jsonl"


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def cmd_list(args) -> int:
    loc = get_platform().camera_locator
    cams = loc.list_cameras()
    if not cams:
        print("キャプチャデバイスが見つかりません")
        return 1
    for c in cams:
        print(f"{c.node}: {c.name!r} {c.vendor_id}:{c.product_id} serial={c.serial} "
              f"port={c.extra.get('id_path')}")
        for l in (*c.by_id, *c.by_path):
            print(f"    {l}")
        print()
        print(loc.config_snippet(c))
        print()
    return 0


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    ids = camera_identities(cfg.get("camera", {}))
    loc = get_platform().camera_locator
    cams = loc.list_cameras()

    print(f"[{args.label}] boot_id={boot_id()}")
    print("cameras: " + (", ".join(f"{c.node}(serial={c.serial}, port={c.extra.get('id_path')})"
                                   for c in cams) or "(none)"))
    results = []
    for ident in ids:
        r = {"kind": ident.kind, "identity": ident.describe()}
        try:
            res = loc.resolve(ident)
            r.update(ok=True, node=res.node, link=res.link, serial=res.info.serial,
                     port=res.info.extra.get("id_path"))
            print(f"  OK   {ident.kind:8} -> {res.node}  serial={res.info.serial}  link={res.link}")
        except CameraNotFound as e:
            r.update(ok=False, error="CameraNotFound")
            print(f"  FAIL {ident.kind:8} -> CameraNotFound ({e.identity.describe()})")
        except CameraAmbiguous as e:
            r.update(ok=False, error="CameraAmbiguous", matches=[m.node for m in e.matches])
            print(f"  FAIL {ident.kind:8} -> CameraAmbiguous {[m.node for m in e.matches]}")
        results.append(r)

    rec = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "label": args.label,
        "boot_id": boot_id(),
        "cameras": [{"node": c.node, "serial": c.serial, "port": c.extra.get("id_path"),
                     "by_id": c.by_id, "by_path": c.by_path} for c in cams],
        "results": results,
    }
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"logged: {LOG}")
    return 0


def cmd_summary(args) -> int:
    if not LOG.exists():
        print("ログがありません")
        return 1
    for line in LOG.read_text().splitlines():
        rec = json.loads(line)
        cells = [f"{r['kind']}={r['node'] if r['ok'] else r['error']}" for r in rec["results"]]
        ports = ",".join(c["port"] or "" for c in rec["cameras"])
        print(f"{rec['timestamp']} boot={rec['boot_id'][:8]} [{rec['label']}] port={ports}  "
              + "  ".join(cells))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="カメラ候補と設定の候補を表示する")
    p = sub.add_parser("check", help="config.toml の各識別子を解決してログに記録する")
    p.add_argument("--label", required=True, help="状況の説明（例: 'baseline', 'replug'）")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub.add_parser("summary", help="ログを一覧表示する")
    args = ap.parse_args()
    return {"list": cmd_list, "check": cmd_check, "summary": cmd_summary}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
