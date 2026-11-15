"""P1: websocket による射影補正の行列の設定（PLAN.md 12章、15章）。

    .venv/bin/python experiments/p1_perspective.py status
    .venv/bin/python experiments/p1_perspective.py install --yes      # カメラのソースの先頭に追加（単位行列）
    .venv/bin/python experiments/p1_perspective.py check --yes        # 単位行列・既知の行列の見た目（配信・録画していないとき）
    .venv/bin/python experiments/p1_perspective.py live --yes [--steps 20] [--interval 0.5]   # 配信・録画中の更新
    .venv/bin/python experiments/p1_perspective.py reinstall --yes    # シェーダーの版が古い場合に作り直す
    .venv/bin/python experiments/p1_perspective.py remove --yes

対象は config.toml の [target] source_name、フィルタ名は [perspective] filter_name（既定 Perspective）。
install / reinstall / remove はシーンコレクションを変える。check は Crop/Pad を一時的に無効にする
（出力に映る）。live は配信・録画中に行列を変える。いずれも実行前にユーザーの確認を取ること。
check・live は終了時に元の状態（行列・有効状態・Crop/Pad の有効状態）に戻す。
結果は experiments/out/p1-log.jsonl と p1-*.png に保存する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from obs_autocrop.config import DEFAULT_CONFIG_PATH, load_config
from obs_autocrop.obsctl.shader_filter import PerspectiveFilter, check_filter_order, load_shader
from obs_autocrop.obsctl.ws_client import ObsWs
from obs_autocrop.platforms import get_platform
from obs_autocrop.vision.homography import warp_like_shader

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "experiments" / "out"
LOG = OUT / "p1-log.jsonl"
SETTLE_S = 0.4
SHOT_W = 1920  # 比較用のスクリーンショットの幅（4K の原寸 PNG は遅い。P0）


def log(rec: dict) -> None:
    OUT.mkdir(exist_ok=True)
    rec = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), **rec}
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def setup(cfg):
    ws_cfg = cfg.get("obs", {}).get("websocket", {})
    password = ws_cfg.get("password") or get_platform(cfg).obs_paths.websocket_config()["password"]
    ws = ObsWs(ws_cfg.get("host", "localhost"), ws_cfg.get("port", 4455), password)
    ws.wait_ready(timeout=30)
    t, p = cfg.get("target", {}), cfg.get("perspective", {})
    shader = load_shader(ROOT / p["shader_path"]) if p.get("shader_path") else load_shader()
    pf = PerspectiveFilter(ws, t["source_name"], p.get("filter_name", "Perspective"), shader)
    return ws, pf, t.get("filter_name", "Crop/Pad"), p


def need_yes(args, what: str) -> None:
    if not args.yes:
        sys.exit(f"{what}。--yes を付けて実行してください")


def grab(ws: ObsWs, source: str, width: int = SHOT_W) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(ws.screenshot(source, width=width))).convert("RGB")).astype(np.int16)


def mad(a, b, mask=None) -> float:
    d = np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16))
    return round(float(d[mask].mean() if mask is not None else d.mean()), 3)


def print_status(pf: PerspectiveFilter, ws: ObsWs, crop_name: str) -> dict:
    st = pf.status()
    filters = ws.list_filters(pf.source)
    order = check_filter_order(filters, crop_name, pf.name if st.exists else None)
    d = {**st.__dict__, "up_to_date": st.up_to_date,
         "filters": [(f.index, f.name, f.kind, f.enabled) for f in filters], "order_problems": order}
    print(json.dumps(d, ensure_ascii=False, indent=2))
    return d


# ---------------------------------------------------------------- commands

def cmd_status(cfg, args) -> int:
    ws, pf, crop_name, _ = setup(cfg)
    with ws:
        print_status(pf, ws, crop_name)
    return 0


def cmd_install(cfg, args) -> int:
    need_yes(args, "カメラのソースに射影補正のフィルタを追加します（シーンコレクションが変わる）")
    ws, pf, crop_name, p = setup(cfg)
    with ws:
        st = pf.install(enabled=True, outside_black=p.get("outside") == "black")
        print(f"installed: {pf.source} / {pf.name} index={st.index} version={st.version} enabled={st.enabled}")
        d = print_status(pf, ws, crop_name)
    log({"cmd": "install", **{k: d[k] for k in ("index", "version", "filters", "order_problems")}})
    return 0


def cmd_reinstall(cfg, args) -> int:
    need_yes(args, "射影補正のフィルタを作り直します（シーンコレクションが変わる）")
    ws, pf, crop_name, _ = setup(cfg)
    with ws:
        before = pf.status()
        st = pf.reinstall()
        print(f"reinstalled: version {before.version} -> {st.version}")
    log({"cmd": "reinstall", "from": before.version, "to": st.version})
    return 0


def cmd_remove(cfg, args) -> int:
    need_yes(args, "射影補正のフィルタを削除します（シーンコレクションが変わる）")
    ws, pf, _, _ = setup(cfg)
    with ws:
        pf.remove()
    print("removed")
    log({"cmd": "remove"})
    return 0


CASES = [
    ("translate_x+0.05", np.array([[1, 0, 0.05], [0, 1, 0], [0, 0, 1]], float)),
    ("scale_0.9_center", np.array([[0.9, 0, 0.05], [0, 0.9, 0.05], [0, 0, 1]], float)),
    ("perspective", np.array([[1, 0.03, 0], [0, 1, 0], [0.08, 0.05, 1]], float)),
]


def cmd_check(cfg, args) -> int:
    need_yes(args, "Crop/Pad を一時的に無効にし、射影補正の行列を変えて比較します（出力に映る）")
    ws, pf, crop_name, _ = setup(cfg)
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    OUT.mkdir(exist_ok=True)
    rec: dict = {"cmd": "check", "cases": []}
    with ws:
        s0 = ws.output_stats()
        if s0["rec_active"] or s0["stream_active"]:
            sys.exit("配信中または録画中です。check は停止中に実行してください（配信・録画中は live）")
        st0 = pf.require_ready()
        crop0 = ws.get_filter(pf.source, crop_name)
        try:
            ws.set_filter_enabled(pf.source, crop_name, False)  # 全体（素のフレーム）で比較する
            pf.identity()
            # 素の画像とカメラのノイズの基準（射影補正を無効にして 2 枚）
            pf.set_enabled(False)
            time.sleep(SETTLE_S)
            raw1 = grab(ws, pf.source)
            time.sleep(0.2)
            raw2 = grab(ws, pf.source)
            noise = mad(raw1, raw2)
            pf.set_enabled(True)
            time.sleep(SETTLE_S)
            ident = grab(ws, pf.source)
            rec["noise_mad"] = noise
            rec["identity_mad"] = mad(ident, raw1)
            print(f"size {raw1.shape[1]}x{raw1.shape[0]}  ノイズ（素の 2 枚）{noise}  単位行列 vs 素 {rec['identity_mad']}")
            Image.fromarray(raw1.astype(np.uint8)).save(OUT / f"p1-{stamp}-raw.png")
            for name, m in CASES:
                pf.set_matrix(m)
                time.sleep(SETTLE_S)
                got = grab(ws, pf.source)
                exp, inside = warp_like_shader(raw1.astype(np.uint8), m)
                r = {"case": name, "vs_expected": mad(got, exp, inside), "vs_raw": mad(got, raw1, inside),
                     "outside_px": int((~inside).sum())}
                rec["cases"].append(r)
                Image.fromarray(got.astype(np.uint8)).save(OUT / f"p1-{stamp}-{name}.png")
                print(f"  {name:18} 期待画像との差 {r['vs_expected']:6.3f}  （補正しない場合の差 {r['vs_raw']:6.3f}）")
            # 反映の速さ（幅 320 のスクリーンショットで判定）
            pf.identity()
            time.sleep(SETTLE_S)
            base = grab(ws, pf.source, 320)
            t0 = time.monotonic()
            pf.set_matrix(CASES[0][1])
            rec["latency_s"] = None
            while time.monotonic() - t0 < 3:
                if mad(grab(ws, pf.source, 320), base) > max(2 * noise, 1.0):
                    rec["latency_s"] = round(time.monotonic() - t0, 3)
                    break
            print(f"反映の速さ: {rec['latency_s']}s（スクリーンショット取得を含む）")
        finally:
            pf.set_matrix(st0.matrix if st0.matrix else np.eye(3))
            pf.set_enabled(bool(st0.enabled))
            ws.set_filter_enabled(pf.source, crop_name, crop0.enabled)
            time.sleep(SETTLE_S)
            st1 = pf.status()
            rec["restored_ok"] = (np.allclose(st1.matrix, st0.matrix) and st1.enabled == st0.enabled
                                  and ws.get_filter(pf.source, crop_name).enabled == crop0.enabled)
            print(f"restored ok={rec['restored_ok']}")
    log(rec)
    return 0


def live_sequence(steps: int) -> list[np.ndarray]:
    """単位行列の近くで、平行移動・拡大縮小・射影を少しずつ変える行列の列。"""
    seq = []
    for i in range(steps):
        a = [0, 1, 2, 1, 0, -1, -2, -1][i % 8] / 2
        m = np.array([[1 - 0.02 * abs(a), 0, 0.01 * a],
                      [0, 1 - 0.02 * abs(a), 0.01 * abs(a)],
                      [0.02 * a, 0, 1]], float)
        seq.append(m)
    return seq


def cmd_live(cfg, args) -> int:
    need_yes(args, "配信・録画中に射影補正の行列を変えます")
    ws, pf, _, _ = setup(cfg)
    r: dict = {"cmd": "live", "interval_s": args.interval, "steps": args.steps, "samples": []}
    with ws:
        s0 = ws.output_stats()
        if not (s0["rec_active"] or s0["stream_active"]):
            sys.exit("配信も録画も開始されていません。OBS で開始してから実行してください")
        st0 = pf.require_ready()
        print(f"rec={s0['rec_active']} stream={s0['stream_active']}  filter enabled={st0.enabled}")
        r["samples"].append({"step": "before", **s0})
        try:
            if not st0.enabled:
                pf.set_enabled(True)
            for i, m in enumerate(live_sequence(args.steps)):
                pf.set_matrix(m)
                time.sleep(args.interval)
                s = ws.output_stats()
                r["samples"].append({"step": i, **s})
                print(f"  [{i:2}] m02={m[0, 2]:+.3f} m20={m[2, 0]:+.3f}  rec={s['rec_active']}/{s['rec_duration_ms']}ms "
                      f"stream={s['stream_active']}  skip(render/out)={s['render_skipped']}/{s['output_skipped']} "
                      f"cpu={s['cpu']}%")
        finally:
            pf.set_matrix(st0.matrix if st0.matrix else np.eye(3))
            pf.set_enabled(bool(st0.enabled))
            time.sleep(args.interval)
            st1 = pf.status()
            r["restored_ok"] = np.allclose(st1.matrix, st0.matrix) and st1.enabled == st0.enabled
            r["samples"].append({"step": "after", **ws.output_stats()})
            print(f"restored ok={r['restored_ok']}")
    a, b = r["samples"][0], r["samples"][-1]
    durs = [x["rec_duration_ms"] for x in r["samples"] if x["rec_active"]]
    r["summary"] = {
        "elapsed_s": round(b["t"] - a["t"], 2),
        "render_skipped": b["render_skipped"] - a["render_skipped"],
        "render_total": b["render_total"] - a["render_total"],
        "output_skipped": b["output_skipped"] - a["output_skipped"],
        "output_total": b["output_total"] - a["output_total"],
        "rec_active_throughout": all(x["rec_active"] for x in r["samples"]) if a["rec_active"] else None,
        "rec_duration_monotonic": all(x < y for x, y in zip(durs, durs[1:])) if durs else None,
        "stream_active_throughout": all(x["stream_active"] for x in r["samples"]) if a["stream_active"] else None,
        "stream_reconnecting_seen": any(x["stream_reconnecting"] for x in r["samples"]),
        "stream_skipped": (b["stream_skipped"] or 0) - (a["stream_skipped"] or 0) if a["stream_active"] else None,
    }
    print("summary:", json.dumps(r["summary"], ensure_ascii=False))
    log(r)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("install", "reinstall", "remove", "check", "live"):
        p = sub.add_parser(name)
        p.add_argument("--yes", action="store_true")
        if name == "live":
            p.add_argument("--steps", type=int, default=20)
            p.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args()
    cfg = load_config(args.config)
    return {"status": cmd_status, "install": cmd_install, "reinstall": cmd_reinstall, "remove": cmd_remove,
            "check": cmd_check, "live": cmd_live}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
