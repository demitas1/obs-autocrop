"""P0: obs-shaderfilter の実機確認（PLAN.md 12章、15章）。

    .venv/bin/python experiments/p0_shaderfilter.py create --yes [--source Image]
    .venv/bin/python experiments/p0_shaderfilter.py settings [--source Image]
    .venv/bin/python experiments/p0_shaderfilter.py check [--source Image]
    .venv/bin/python experiments/p0_shaderfilter.py error-test --yes [--source Image]
    .venv/bin/python experiments/p0_shaderfilter.py remove --yes [--source Image]

create    : テスト用のソース（既定 Image）に User-defined shader（shader_filter）を追加する。
            シェーダーは shaders/perspective.shader の本文を shader_text に書き込む（インライン）。
            シーンコレクションが変わるため、実行前にユーザーの確認を取ること。
settings  : フィルタの設定（GetSourceFilter）を表示する。読み取りのみ。
check     : テスト用フィルタの行列を websocket で変え、スクリーンショットを OpenCV で同じ規約
            （画素中心 (i+0.5)/W、バイリニア補間）で計算した期待画像と比較する。最後に単位行列に戻す。
error-test: 壊れたシェーダーを設定し、エラーの出方（設定の last_error、ログ）を確認して元に戻す。
remove    : テスト用フィルタを削除する。
結果は experiments/out/p0-log.jsonl と p0-*.png に保存する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from obs_autocrop.config import DEFAULT_CONFIG_PATH, load_config
from obs_autocrop.obsctl.ws_client import ObsWs
from obs_autocrop.platforms import get_platform

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "experiments" / "out"
LOG = OUT / "p0-log.jsonl"
SHADER = ROOT / "shaders" / "perspective.shader"
FILTER_NAME = "P0 Perspective Test"
KIND = "shader_filter"
KEYS = [f"m{r}{c}" for r in range(3) for c in range(3)]
IDENTITY = np.eye(3)
SETTLE_S = 0.3


def log(rec: dict) -> None:
    OUT.mkdir(exist_ok=True)
    rec = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), **rec}
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def connect(cfg) -> ObsWs:
    ws_cfg = cfg.get("obs", {}).get("websocket", {})
    password = ws_cfg.get("password") or get_platform(cfg).obs_paths.websocket_config()["password"]
    ws = ObsWs(ws_cfg.get("host", "localhost"), ws_cfg.get("port", 4455), password)
    ws.wait_ready(timeout=30)
    return ws


def matrix_settings(m: np.ndarray) -> dict:
    return {k: float(m.flat[i]) for i, k in enumerate(KEYS)}


def grab(ws: ObsWs, source: str) -> np.ndarray:
    """RGBA の配列。"""
    return np.array(Image.open(io.BytesIO(ws.screenshot(source))).convert("RGBA"))


def expected(raw: np.ndarray, m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """シェーダーと同じ計算の期待画像と、入力の範囲内にある画素のマスク。"""
    h, w = raw.shape[:2]
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    uu, vv = np.meshgrid(u, v)
    x = m[0, 0] * uu + m[0, 1] * vv + m[0, 2]
    y = m[1, 0] * uu + m[1, 1] * vv + m[1, 2]
    z = m[2, 0] * uu + m[2, 1] * vv + m[2, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        sx, sy = x / z, y / z
    inside = (z > 0) & (sx >= 0) & (sx <= 1) & (sy >= 0) & (sy <= 1)
    map_x = (sx * w - 0.5).astype(np.float32)
    map_y = (sy * h - 0.5).astype(np.float32)
    out = cv2.remap(raw, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    out[~inside] = 0
    return out, inside


def compare(got: np.ndarray, exp: np.ndarray, inside: np.ndarray) -> dict:
    d = np.abs(got.astype(np.int16) - exp.astype(np.int16))
    rgb_in = d[inside][:, :3]
    # 境界の 1px は補間の差が大きいので除いた統計も出す
    core = cv2.erode(inside.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    rgb_core = d[core][:, :3]
    return {
        "inside_px": int(inside.sum()),
        "mean_abs": round(float(rgb_in.mean()), 3) if rgb_in.size else None,
        "core_mean_abs": round(float(rgb_core.mean()), 3) if rgb_core.size else None,
        "core_p99": int(np.percentile(rgb_core, 99)) if rgb_core.size else None,
        "core_max": int(rgb_core.max()) if rgb_core.size else None,
        "outside_alpha_max": int(got[~inside][:, 3].max()) if (~inside).any() else None,
    }


def find_filter(ws: ObsWs, source: str):
    return next((f for f in ws.list_filters(source) if f.name == FILTER_NAME), None)


# ---------------------------------------------------------------- commands

def cmd_create(cfg, args) -> int:
    if not args.yes:
        sys.exit(f"{args.source} にフィルタ {FILTER_NAME!r} を追加します。--yes を付けて実行してください")
    with connect(cfg) as ws:
        if find_filter(ws, args.source):
            print("既に存在します")
            return 0
        ws.create_filter(args.source, FILTER_NAME, KIND,
                         {"shader_text": SHADER.read_text(), **matrix_settings(IDENTITY)})
        f = ws.get_filter(args.source, FILTER_NAME)
    print(f"created: {args.source} / {FILTER_NAME} kind={f.kind} index={f.index} enabled={f.enabled}")
    print("settings keys:", sorted(f.settings))
    log({"cmd": "create", "source": args.source, "keys": sorted(f.settings)})
    return 0


def cmd_settings(cfg, args) -> int:
    with connect(cfg) as ws:
        f = ws.get_filter(args.source, FILTER_NAME)
    s = dict(f.settings)
    if "shader_text" in s:
        s["shader_text"] = f"<{len(s['shader_text'])} chars>"
    print(json.dumps({"index": f.index, "kind": f.kind, "enabled": f.enabled, "settings": s},
                     ensure_ascii=False, indent=2))
    return 0


CASES = [
    ("identity", IDENTITY),
    ("translate_x+0.25", np.array([[1, 0, 0.25], [0, 1, 0], [0, 0, 1]], float)),
    ("scale_0.5", np.array([[0.5, 0, 0], [0, 0.5, 0], [0, 0, 1]], float)),
    ("scale_0.5_center", np.array([[0.5, 0, 0.25], [0, 0.5, 0.25], [0, 0, 1]], float)),
    ("perspective", np.array([[1, 0.1, 0], [0, 1, 0], [0.3, 0.2, 1]], float)),
]


def cmd_check(cfg, args) -> int:
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    OUT.mkdir(exist_ok=True)
    results = []
    with connect(cfg) as ws:
        f = find_filter(ws, args.source)
        if f is None:
            sys.exit(f"{FILTER_NAME!r} がありません。先に create を実行してください")
        ws.set_filter_enabled(args.source, FILTER_NAME, False)
        time.sleep(SETTLE_S)
        raw = grab(ws, args.source)
        ws.set_filter_enabled(args.source, FILTER_NAME, True)
        try:
            for name, m in CASES:
                t0 = time.monotonic()
                ws.set_filter_settings(args.source, FILTER_NAME, matrix_settings(m))
                t_set = time.monotonic() - t0
                time.sleep(SETTLE_S)
                got = grab(ws, args.source)
                exp, inside = expected(raw, m)
                r = {"case": name, "size": list(got.shape[1::-1]), "set_s": round(t_set, 3),
                     **compare(got, exp, inside)}
                Image.fromarray(got).save(OUT / f"p0-{stamp}-{name}.png")
                results.append(r)
                print(json.dumps(r, ensure_ascii=False))
            # 反映の速さ: 設定してから画像が変わるまで（小さいスクリーンショットの取得時間 約 0.09s を含む）。
            # 原寸の PNG は取得に 1 秒以上かかるため、幅 320 で比較する。
            def small():
                b = ws.screenshot(args.source, width=320)
                return np.array(Image.open(io.BytesIO(b)).convert("L")).astype(np.int16)

            ws.set_filter_settings(args.source, FILTER_NAME, matrix_settings(IDENTITY))
            time.sleep(SETTLE_S)
            base = small()
            t0 = time.monotonic()
            ws.set_filter_settings(args.source, FILTER_NAME, matrix_settings(CASES[1][1]))
            latency = None
            while time.monotonic() - t0 < 3:
                if np.abs(small() - base).mean() > 1:
                    latency = round(time.monotonic() - t0, 3)
                    break
            print(f"latency (set → screenshot changed): {latency}s")
        finally:
            ws.set_filter_settings(args.source, FILTER_NAME, matrix_settings(IDENTITY))
    Image.fromarray(raw).save(OUT / f"p0-{stamp}-raw.png")
    log({"cmd": "check", "source": args.source, "results": results, "latency_s": latency})
    return 0


BROKEN = "float4 mainImage(VertData v_in) : TARGET\n{\n\treturn undefined_symbol;\n}\n"


def cmd_error_test(cfg, args) -> int:
    if not args.yes:
        sys.exit("テスト用フィルタに壊れたシェーダーを一時的に設定します。--yes を付けて実行してください")
    with connect(cfg) as ws:
        good = ws.get_filter(args.source, FILTER_NAME).settings.get("shader_text")
        try:
            ws.set_filter_settings(args.source, FILTER_NAME, {"shader_text": BROKEN})
            time.sleep(1.0)
            s = ws.get_filter(args.source, FILTER_NAME).settings
            size = ws.get_source_size(args.source) if args.source != "" else None
            print(f"last_error: {s.get('last_error')!r}")
            print(f"source size while broken: {size}")
        finally:
            ws.set_filter_settings(args.source, FILTER_NAME, {"shader_text": good})
            time.sleep(0.5)
        after = ws.get_filter(args.source, FILTER_NAME).settings.get("last_error")
    print(f"restored. last_error after restore: {after!r}")
    log({"cmd": "error-test", "last_error": s.get("last_error"), "size": size, "after": after})
    return 0


def cmd_remove(cfg, args) -> int:
    if not args.yes:
        sys.exit(f"{args.source} のフィルタ {FILTER_NAME!r} を削除します。--yes を付けて実行してください")
    with connect(cfg) as ws:
        ws.remove_filter(args.source, FILTER_NAME)
    print("removed")
    log({"cmd": "remove", "source": args.source})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("create", "settings", "check", "error-test", "remove"):
        p = sub.add_parser(name)
        p.add_argument("--source", default="Image")
        if name in ("create", "error-test", "remove"):
            p.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    return {"create": cmd_create, "settings": cmd_settings, "check": cmd_check,
            "error-test": cmd_error_test, "remove": cmd_remove}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
