"""E1: websocket 通信（PLAN.md 12章）。

    .venv/bin/python experiments/e1_websocket.py status
    .venv/bin/python experiments/e1_websocket.py probe --yes

status: 読み取り専用。接続、ソースとフィルタの列挙、ソースサイズ、クロップ値、スクリーンショット。
probe : OBS の状態を一時的に変更する（終了時に元に戻す）。配信・録画中には実行しないこと。
  1. Crop/Pad を無効化して sourceWidth/Height とスクリーンショットのサイズを取得
     → sourceWidth がフィルタ適用前か後か / スクリーンショットがフィルタ適用後か
  2. relative=false で既知の矩形を設定し、サイズと画像の一致を確認
     → cx/cy が幅・高さとして解釈されるか
  3. 元の設定と有効状態に戻す
live  : 配信中・録画中にクロップを連続で変更し、出力が途切れないかを監視する（終了時に元に戻す）。
  事前にユーザーが OBS で配信または録画を開始しておく。
結果は experiments/out/e1-*.json と PNG に保存する。
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
from obs_autocrop.models import CropRect
from obs_autocrop.obsctl.ws_client import ObsWs
from obs_autocrop.platforms import get_platform

OUT = Path(__file__).resolve().parent / "out"
TEST_RECT = CropRect(100, 50, 1280, 720)
SETTLE_S = 0.5  # 設定変更がレンダリングに反映されるまで待つ


def connect(cfg: dict) -> ObsWs:
    ws_cfg = cfg.get("obs", {}).get("websocket", {})
    password = ws_cfg.get("password", "")
    if not password:
        oc = get_platform(cfg).obs_paths.websocket_config()
        if not oc["enabled"]:
            sys.exit("obs-websocket が無効です（OBS: ツール → WebSocket サーバー設定）")
        password = oc["password"]
        print(f"password: obs-websocket config.json から読み込み（auth_required={oc['auth_required']}）")
    ws = ObsWs(ws_cfg.get("host", "localhost"), ws_cfg.get("port", 4455), password)
    ws.connect()
    return ws


def shot(ws: ObsWs, source: str, tag: str, stamp: str) -> Image.Image:
    img = Image.open(io.BytesIO(ws.screenshot(source))).convert("RGB")
    path = OUT / f"e1-{stamp}-{tag}.png"
    img.save(path)
    print(f"  screenshot[{tag}]: {img.size[0]}x{img.size[1]} -> {path.name}")
    return img


def cmd_status(cfg: dict, args) -> dict:
    t = cfg.get("target", {})
    source, filt = t["source_name"], t["filter_name"]
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    r: dict = {}
    with connect(cfg) as ws:
        v = ws.version()
        r["version"] = {k: v.get(k) for k in ("obsVersion", "obsWebSocketVersion", "rpcVersion",
                                              "platform", "supportedImageFormats")}
        print(f"OBS {v['obsVersion']} / obs-websocket {v['obsWebSocketVersion']} (rpc {v['rpcVersion']})")

        r["v4l2_inputs"] = ws.list_v4l2_inputs()
        print("v4l2 inputs:", [i["inputName"] for i in r["v4l2_inputs"]])

        s = ws.get_input_settings(source)
        r["input_settings"] = s
        print(f"input settings [{source}]: {s}")

        r["filters"] = [f.__dict__ for f in ws.list_filters(source)]
        for f in r["filters"]:
            print(f"  filter[{f['index']}] {f['name']!r} kind={f['kind']} enabled={f['enabled']} "
                  f"settings={f['settings']}")

        ref = ws.find_scene_item(source)
        r["scene_item"] = ref.__dict__
        w, h = ws.wait_source_size(source)
        r["source_size"] = [w, h]
        print(f"scene item: {ref}  sourceWidth/Height={w}x{h}")

        crop = ws.get_filter(source, filt)
        r["crop_filter"] = crop.__dict__
        print(f"crop filter: {crop.settings} enabled={crop.enabled}")

        img = shot(ws, source, "status", stamp)
        r["screenshot_size"] = list(img.size)
    return r


def mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    return float(np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).mean())


def cmd_probe(cfg: dict, args) -> dict:
    if not args.yes:
        sys.exit("probe は OBS の状態を一時的に変更します。--yes を付けて実行してください")
    t = cfg.get("target", {})
    source, filt = t["source_name"], t["filter_name"]
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    r: dict = {"test_rect": TEST_RECT.__dict__}

    with connect(cfg) as ws:
        orig = ws.get_filter(source, filt)
        r["original"] = {"settings": orig.settings, "enabled": orig.enabled}
        print(f"original: {orig.settings} enabled={orig.enabled}")
        try:
            # 0. 現状
            r["s0_size"] = ws.get_source_size(source)
            img0 = shot(ws, source, "0-original", stamp)
            r["s0_shot"] = img0.size
            print(f"[0] original     sourceSize={r['s0_size']}")

            # 1. Crop/Pad 無効
            ws.set_filter_enabled(source, filt, False)
            time.sleep(SETTLE_S)
            r["s1_size"] = ws.get_source_size(source)
            img1 = shot(ws, source, "1-crop-disabled", stamp)
            r["s1_shot"] = img1.size
            print(f"[1] crop disabled sourceSize={r['s1_size']}")
            ws.set_filter_enabled(source, filt, True)

            # 2. relative=false で既知の矩形
            ws.set_crop(source, filt, TEST_RECT)
            time.sleep(SETTLE_S)
            r["s2_settings"] = ws.get_filter(source, filt).settings
            r["s2_size"] = ws.get_source_size(source)
            # 無効化中の画像と同時刻に近い比較のため、もう一度素の画像を撮る
            img2 = shot(ws, source, "2-test-rect", stamp)
            r["s2_shot"] = img2.size
            print(f"[2] test rect    settings={r['s2_settings']} sourceSize={r['s2_size']}")

            x, y, w, h = TEST_RECT.x, TEST_RECT.y, TEST_RECT.w, TEST_RECT.h
            if img2.size == (w, h) and img1.size[0] >= x + w and img1.size[1] >= y + h:
                # cx/cy=幅/高さ、left/top=オフセットなら img1 の該当領域と一致するはず
                r["diff_expected_region"] = mean_abs_diff(img2, img1.crop((x, y, x + w, y + h)))
                r["diff_origin_region"] = mean_abs_diff(img2, img1.crop((0, 0, w, h)))
                print(f"    mean|diff| vs raw[{x}:{x+w},{y}:{y+h}] = {r['diff_expected_region']:.2f}"
                      f"   vs raw[0:{w},0:{h}] = {r['diff_origin_region']:.2f}")
        finally:
            # 3. 元に戻す（overlay=False で設定を丸ごと置き換える）
            ws.set_filter_settings(source, filt, orig.settings, overlay=False)
            ws.set_filter_enabled(source, filt, orig.enabled)
            time.sleep(SETTLE_S)
            after = ws.get_filter(source, filt)
            r["restored"] = {"settings": after.settings, "enabled": after.enabled,
                             "ok": after.settings == orig.settings and after.enabled == orig.enabled}
            r["s3_size"] = ws.get_source_size(source)
            print(f"[3] restored     {after.settings} enabled={after.enabled} "
                  f"ok={r['restored']['ok']} sourceSize={r['s3_size']}")
    return r


def output_status(ws: ObsWs) -> dict:
    st, rec, stream = ws._req("GetStats"), ws._req("GetRecordStatus"), ws._req("GetStreamStatus")
    return {
        "t": round(time.monotonic(), 3),
        "render_skipped": st["renderSkippedFrames"], "render_total": st["renderTotalFrames"],
        "output_skipped": st["outputSkippedFrames"], "output_total": st["outputTotalFrames"],
        "cpu": round(st["cpuUsage"], 1),
        "rec_active": rec["outputActive"], "rec_duration_ms": rec.get("outputDuration"),
        "rec_bytes": rec.get("outputBytes"),
        "stream_active": stream["outputActive"], "stream_reconnecting": stream.get("outputReconnecting"),
        "stream_skipped": stream.get("outputSkippedFrames"), "stream_total": stream.get("outputTotalFrames"),
        "stream_congestion": stream.get("outputCongestion"),
    }


def crop_sequence(base: CropRect, src_w: int, src_h: int, steps: int) -> list[CropRect]:
    """位置とサイズを少しずつ動かす矩形の列（範囲内に収める）。"""
    seq = []
    for i in range(steps):
        d = [0, 20, 40, 20, 0, -20, -40, -20][i % 8]
        r = CropRect(base.x + d, base.y + abs(d), base.w - 2 * d, base.h + d)
        seq.append(r.clamp(src_w, src_h)[0])
    return seq


def cmd_live(cfg: dict, args) -> dict:
    if not args.yes:
        sys.exit("live は配信・録画中の OBS の状態を変更します。--yes を付けて実行してください")
    t = cfg.get("target", {})
    source, filt = t["source_name"], t["filter_name"]
    r: dict = {"interval_s": args.interval, "steps": args.steps, "samples": []}

    with connect(cfg) as ws:
        s0 = output_status(ws)
        if not (s0["rec_active"] or s0["stream_active"]):
            sys.exit("配信も録画も開始されていません。OBS で開始してから実行してください")
        orig = ws.get_filter(source, filt)
        r["original"] = {"settings": orig.settings, "enabled": orig.enabled}
        # 素のサイズ: 一時無効化は出力に映るため、relative の設定から逆算する
        w, h = ws.get_source_size(source)
        c = orig.settings
        if not c.get("relative", True):
            sys.exit("relative=false の設定からは素のサイズを求められません")
        src_w, src_h = w + c.get("left", 0) + c.get("right", 0), h + c.get("top", 0) + c.get("bottom", 0)
        base = CropRect.from_filter_settings(c, src_w, src_h)
        r["raw_size"], r["base"] = [src_w, src_h], base.__dict__
        print(f"raw {src_w}x{src_h}, base {base}, rec={s0['rec_active']} stream={s0['stream_active']}")
        r["samples"].append({"step": "before", **s0})
        try:
            for i, rect in enumerate(crop_sequence(base, src_w, src_h, args.steps)):
                ws.set_crop(source, filt, rect)
                time.sleep(args.interval)
                s = output_status(ws)
                s["rect"] = rect.__dict__
                s["source_size"] = ws.get_source_size(source)
                r["samples"].append({"step": i, **s})
                print(f"  [{i:2}] {rect.x},{rect.y},{rect.w}x{rect.h} size={s['source_size']} "
                      f"rec={s['rec_active']}/{s['rec_duration_ms']}ms stream={s['stream_active']} "
                      f"skip(render/out)={s['render_skipped']}/{s['output_skipped']} cpu={s['cpu']}%")
        finally:
            ws.set_filter_settings(source, filt, orig.settings, overlay=False)
            ws.set_filter_enabled(source, filt, orig.enabled)
            time.sleep(args.interval)
            after = ws.get_filter(source, filt)
            r["restored_ok"] = after.settings == orig.settings and after.enabled == orig.enabled
            s = output_status(ws)
            r["samples"].append({"step": "after", **s})
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
        "rec_duration_gain_ms": (durs[-1] - durs[0]) if durs else None,
        "stream_active_throughout": all(x["stream_active"] for x in r["samples"]) if a["stream_active"] else None,
        "stream_reconnecting_seen": any(x["stream_reconnecting"] for x in r["samples"]),
    }
    print("summary:", r["summary"])
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="読み取り専用の確認")
    p = sub.add_parser("probe", help="一時的に設定を変えて要確認事項を調べる（元に戻す）")
    p.add_argument("--yes", action="store_true", help="OBS の状態変更を許可する")
    p = sub.add_parser("live", help="配信・録画中にクロップを連続変更して出力を監視する（元に戻す）")
    p.add_argument("--yes", action="store_true", help="OBS の状態変更を許可する")
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args()

    cfg = load_config(args.config)
    OUT.mkdir(exist_ok=True)
    r = {"status": cmd_status, "probe": cmd_probe, "live": cmd_live}[args.cmd](cfg, args)
    r["timestamp"] = dt.datetime.now().isoformat(timespec="seconds")
    out = OUT / f"e1-{args.cmd}-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
