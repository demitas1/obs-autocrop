"""E4: ArUco 認識（PLAN.md 12章）。

    .venv/bin/python experiments/e4_aruco.py markers --view 600x340 [--target-px 50] | --size-mm N
    .venv/bin/python experiments/e4_aruco.py controls list|check
    .venv/bin/python experiments/e4_aruco.py controls set --yes
    .venv/bin/python experiments/e4_aruco.py snap
    .venv/bin/python experiments/e4_aruco.py measure [--runs 10] [--label X]
    .venv/bin/python experiments/e4_aruco.py apply --yes [--from FILE]
    .venv/bin/python experiments/e4_aruco.py restore-crop FILE --yes

markers : 印刷用 PDF と個別 PNG を experiments/out/markers/ に作る（読み取りのみ）。
controls: [camera.controls] と現在値の比較（list/check は読み取りのみ。set はカメラの値を変更する）。
snap    : 1 枚撮影し、検出結果を重ねた画像を保存する（配置・ピントの確認用）。OBS の終了中に実行する。
measure : runs 回、デバイスを開き直して計測し、クロップ値のばらつきを集計する。OBS の終了中に実行する。
          最後の結果を experiments/out/e4-last.json に保存する。
apply   : e4-last.json のクロップを websocket で OBS に適用する（OBS の起動中。整合チェックあり）。
          適用前のフィルタ設定を experiments/out/e4-crop-before-*.json に保存する。
restore-crop: 保存したフィルタ設定に戻す。
結果は experiments/out/e4-log.jsonl に追記する。
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

from obs_autocrop.config import DEFAULT_CONFIG_PATH, camera_identity, load_config
from obs_autocrop.models import CropRect
from obs_autocrop.obsctl.scene_file import SceneCollection
from obs_autocrop.platforms import get_platform
from obs_autocrop.vision import capture as vcap
from obs_autocrop.vision import detect as vdet
from obs_autocrop.vision import markers as vmk

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "experiments" / "out"
LOG = OUT / "e4-log.jsonl"
LAST = OUT / "e4-last.json"


def stamp() -> str:
    return f"{dt.datetime.now():%Y%m%d-%H%M%S}"


def log(rec: dict) -> None:
    OUT.mkdir(exist_ok=True)
    rec = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), **rec}
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def resolve_node(cfg, plat) -> str:
    r = plat.camera_locator.resolve(camera_identity(cfg.get("camera", {})))
    return r.node


def capture_format(cfg, plat) -> vcap.CaptureFormat:
    """OBS のシーンコレクションのソース設定を優先し、-1 は [camera.format] で補う（9.1）。"""
    path = plat.obs_paths.collection_path(cfg.get("obs", {}).get("collection") or None)
    sc = SceneCollection.load(path)
    t = cfg.get("target", {})
    src = sc.find_source(t.get("source_name"), t.get("source_uuid") or None)
    fmt, warnings = vcap.resolve_format(sc.get_camera_config(src, plat.source_adapter),
                                        cfg.get("camera", {}).get("format", {}))
    for w in warnings:
        print(f"WARN: {w}")
    return fmt


def require_obs_stopped(plat) -> None:
    if plat.obs_process.is_running():
        sys.exit("OBS が起動中です。カメラを開けないため、OBS を終了してから実行してください")


def save_img(img: np.ndarray, name: str, quality: int = 92) -> Path:
    OUT.mkdir(exist_ok=True)
    p = OUT / name
    cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, quality] if p.suffix == ".jpg" else [])
    return p


# ---------------------------------------------------------------- markers

def cmd_markers(cfg, plat, args) -> int:
    mcfg = cfg.get("markers", {})
    fmt_cfg = cfg.get("camera", {}).get("format", {})
    d = vmk.get_dictionary(mcfg.get("dictionary", "DICT_4X4_50"))
    ids = list(mcfg.get("ids", [0, 1, 2, 3]))
    if args.size_mm:
        size = args.size_mm
    elif args.view:
        vw, vh = (float(v) for v in args.view.lower().split("x"))
        W, H = fmt_cfg.get("width", 3840), fmt_cfg.get("height", 2160)
        target = args.target_px or mcfg.get("target_px", 50)
        size = vmk.recommended_size_mm(vw, vh, W, H, target)
        print(f"view {vw:g}x{vh:g}mm @ {W}x{H}: {max(vw / W, vh / H):.4f} mm/px "
              f"-> {target}px = {size}mm")
    else:
        sys.exit("--view WxH（mm）か --size-mm N を指定してください")
    spec = vmk.make_spec(d, size, mcfg.get("quiet_zone_cells", 2), args.dpi)
    files = vmk.save_markers(OUT / "markers", d, ids, spec)
    print(f"marker: {spec.actual_mm:.2f}mm（要求 {size}mm、{spec.cells} セル x {spec.cell_px}px @ {spec.dpi}dpi）"
          f"、切り取り範囲 {spec.tile_px * 25.4 / spec.dpi:.1f}mm")
    for k, p in files.items():
        print(f"  {k}: {p}")
    log({"cmd": "markers", "size_mm": size, "actual_mm": spec.actual_mm, "dpi": spec.dpi,
         "files": {k: str(v) for k, v in files.items()}})
    return 0


# ---------------------------------------------------------------- controls

def cmd_controls(cfg, plat, args) -> int:
    node = resolve_node(cfg, plat)
    want = {k: int(v) for k, v in cfg.get("camera", {}).get("controls", {}).items()}
    ctl = plat.camera_controls
    if args.action == "list":
        for name, info in ctl.list_controls(node).items():
            extra = " (inactive)" if info.get("inactive") else ""
            rng = f"min={info.get('min')} max={info.get('max')} " if "min" in info else ""
            print(f"{name:32} {info['type']:5} {rng}default={info.get('default')} "
                  f"value={info.get('value')}{extra}")
        return 0
    if not want:
        print("[camera.controls] が空です")
        return 0
    if args.action == "set":
        if not args.yes:
            sys.exit("カメラのコントロールを変更します。--yes を付けて実行してください")
        before = ctl.get(node, list(want))
        ctl.set(node, want)
        print(f"set: {want}")
        log({"cmd": "controls-set", "node": node, "before": before, "values": want})
    cur = ctl.get(node, list(want))
    diff = {k: (v, cur.get(k)) for k, v in want.items() if cur.get(k) != v}
    for k, v in want.items():
        print(f"  {k:32} want={v:<8} current={cur.get(k)}{'  <-- 不一致' if k in diff else ''}")
    rec = {"cmd": f"controls-{args.action}", "label": args.label, "node": node, "current": cur,
           "mismatch": diff, "obs_running": plat.obs_process.is_running()}
    log(rec)
    print("OK: すべて一致" if not diff else f"NG: {len(diff)} 件不一致")
    return 0 if not diff else 1


# ---------------------------------------------------------------- snap / measure

def detector(cfg):
    mc = cfg.get("measure", {})
    return vdet.make_detector(cfg.get("markers", {}).get("dictionary", "DICT_4X4_50"),
                              mc.get("adaptive_win_max", 103), mc.get("adaptive_win_step", 10))


def cmd_snap(cfg, plat, args) -> int:
    require_obs_stopped(plat)
    node, fmt = resolve_node(cfg, plat), capture_format(cfg, plat)
    mc = cfg.get("measure", {})
    frame = vcap.capture(plat.capture_backend, node, fmt, n=1, warmup=mc.get("warmup_frames", 30))[0]
    s = stamp()
    raw = save_img(frame, f"e4-{s}-snap.jpg")
    dets = vdet.detect(frame, detector(cfg))
    found = {i: len(v) for i, v in dets.items()}
    print(f"{fmt} from {node}: 検出 {found or 'なし'}")
    img = frame.copy()
    if dets:
        ids = np.array([[i] for i, cs in dets.items() for _ in cs], dtype=np.int32)
        cs = [c.reshape(1, 4, 2).astype(np.float32) for v in dets.values() for c in v]
        cv2.aruco.drawDetectedMarkers(img, cs, ids)
        for i, v in dets.items():
            for c in v:
                side = float(np.mean(np.linalg.norm(c - np.roll(c, 1, axis=0), axis=1)))
                print(f"  ID {i}: 1 辺 {side:.1f}px  中心 ({c[:, 0].mean():.0f}, {c[:, 1].mean():.0f})")
    ov = save_img(img, f"e4-{s}-snap-overlay.jpg")
    print(f"saved: {raw}\n       {ov}")
    log({"cmd": "snap", "node": node, "format": str(fmt), "found": found, "image": str(raw)})
    return 0


def summarize(crops: list[CropRect]) -> dict:
    out = {}
    for k in ("x", "y", "w", "h"):
        vals = [getattr(c, k) for c in crops]
        out[k] = {"min": min(vals), "max": max(vals), "spread": max(vals) - min(vals),
                  "median": float(np.median(vals))}
    # ±1px 以内: 全計測が中央値から 1px 以内
    out["within_1px"] = all(abs(getattr(c, k) - out[k]["median"]) <= 1
                            for c in crops for k in ("x", "y", "w", "h"))
    return out


def cmd_measure(cfg, plat, args) -> int:
    require_obs_stopped(plat)
    node, fmt = resolve_node(cfg, plat), capture_format(cfg, plat)
    mc = cfg.get("measure", {})
    ids = tuple(cfg.get("markers", {}).get("ids", [0, 1, 2, 3]))
    det = detector(cfg)
    s = stamp()
    print(f"camera {node}  format {fmt}  runs={args.runs}")
    results, crops = [], []
    last = None
    for run in range(1, args.runs + 1):
        t0 = time.monotonic()
        frames = vcap.capture(plat.capture_backend, node, fmt, n=mc.get("measure_frames", 10),
                              warmup=mc.get("warmup_frames", 30))
        t_cap = time.monotonic() - t0
        try:
            m, dets = vdet.measure(frames, det, ids, mc.get("min_valid", 5), mc.get("margin_px", 0),
                                   mc.get("skew_tol_ratio", 0.01))
        except vdet.MeasurementFailed as e:
            print(f"[{run:2}] 失敗: {e}")
            results.append({"run": run, "error": str(e)})
            save_img(frames[-1], f"e4-{s}-run{run}-failed.jpg")
            continue
        std = max(max(v) for v in m.corner_std.values())
        print(f"[{run:2}] crop x={m.crop.x} y={m.crop.y} w={m.crop.w} h={m.crop.h}  "
              f"valid {m.frames_valid}/{m.frames_total}  corner std max {std:.3f}px  "
              f"capture {t_cap:.1f}s" + (f"  WARN {m.warnings}" if m.warnings else ""))
        results.append({"run": run, **m.to_dict(), "capture_s": round(t_cap, 2)})
        crops.append(m.crop)
        last = (m, frames[-1], dets[-1])
    if not crops:
        log({"cmd": "measure", "label": args.label, "format": str(fmt), "runs": results})
        return 1
    summ = summarize(crops)
    m, frame, dets = last
    ov = save_img(vdet.draw_overlay(frame, dets, m), f"e4-{s}-overlay.jpg")
    zm = save_img(vdet.corner_zooms(frame, m), f"e4-{s}-corners.png")
    save_img(frame, f"e4-{s}-frame.jpg", 95)
    print("\nsummary (" + f"{len(crops)}/{args.runs} runs):")
    for k in ("x", "y", "w", "h"):
        v = summ[k]
        print(f"  {k}: median {v['median']:g}  min {v['min']}  max {v['max']}  spread {v['spread']}")
    print(f"  ±1px 以内: {'OK' if summ['within_1px'] else 'NG'}")
    print(f"  配置（画像上）: {m.positions}")
    print(f"saved: {ov}\n       {zm}（4 隅の拡大。緑線がクロップ境界）")
    rec = {"cmd": "measure", "label": args.label, "node": node, "format": str(fmt),
           "runs": results, "summary": summ, "overlay": str(ov)}
    log(rec)
    LAST.write_text(json.dumps({"timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                                "node": node, "format": str(fmt), **m.to_dict(),
                                "summary": summ}, ensure_ascii=False, indent=2))
    print(f"       {LAST}")
    return 0 if summ["within_1px"] and len(crops) == args.runs else 1


# ---------------------------------------------------------------- apply

def connect(cfg, plat):
    from obs_autocrop.obsctl.ws_client import ObsWs

    ws_cfg = cfg.get("obs", {}).get("websocket", {})
    password = ws_cfg.get("password") or plat.obs_paths.websocket_config()["password"]
    ws = ObsWs(ws_cfg.get("host", "localhost"), ws_cfg.get("port", 4455), password)
    ws.wait_ready(timeout=cfg.get("obs", {}).get("ready_timeout_s", 30))
    return ws


def screenshot(ws, source: str, name: str) -> Path:
    from PIL import Image

    p = OUT / name
    Image.open(io.BytesIO(ws.screenshot(source))).convert("RGB").save(p)
    return p


def cmd_apply(cfg, plat, args) -> int:
    if not args.yes:
        sys.exit("OBS のクロップを変更します。--yes を付けて実行してください")
    data = json.loads(args.src.read_text())
    rect = CropRect(**data["crop"])
    fw, fh = data["frame_size"]
    t = cfg.get("target", {})
    source, filt = t["source_name"], t.get("filter_name", "Crop/Pad")
    s = stamp()
    with connect(cfg, plat) as ws:
        # 9.2 の整合チェック（素のサイズはフィルタを無効にして取得する。E1 で確認した post-filter のため）
        filters = ws.list_filters(source)
        mine = next((f for f in filters if f.name == filt), None)
        if mine is None:
            sys.exit(f"フィルタ {filt!r} が {source!r} にありません")
        before = [f for f in filters if f.index < mine.index and f.enabled]
        dev = ws.get_input_settings(source)["inputSettings"].get("device_id")
        node = resolve_node(cfg, plat)
        problems = []
        if before:
            problems.append(f"{filt} より前に有効なフィルタがあります: {[f.name for f in before]}")
        if not plat.source_adapter.same_device(dev, node):
            problems.append(f"device_id {dev!r} が計測したカメラ {node} と一致しません")
        backup = OUT / f"e4-crop-before-{s}.json"
        backup.write_text(json.dumps({"source": source, "filter": filt, "enabled": mine.enabled,
                                      "settings": mine.settings}, ensure_ascii=False, indent=2))
        ws.set_filter_enabled(source, filt, False)
        try:
            time.sleep(0.5)  # 無効化がレンダリングに反映されるまで待つ（E1）
            raw = ws.get_source_size(source)
        finally:
            ws.set_filter_enabled(source, filt, mine.enabled)
        if raw != (fw, fh):
            problems.append(f"ソースの素のサイズ {raw} が計測時の {fw}x{fh} と一致しません")
        if problems and not args.force:
            for p in problems:
                print(f"ERROR: {p}")
            print("中断しました（--force で警告のみにできます）")
            return 1
        for p in problems:
            print(f"WARN: {p}")
        ws.set_crop(source, filt, rect)
        if not mine.enabled:
            ws.set_filter_enabled(source, filt, True)
        time.sleep(0.5)
        size = ws.get_source_size(source)
        shot = screenshot(ws, source, f"e4-{s}-applied.png")
    ok = size == (rect.w, rect.h)
    print(f"applied: {rect} -> source size {size} {'OK' if ok else 'NG'}")
    print(f"before : {backup}（restore-crop で戻せます）\nshot   : {shot}")
    log({"cmd": "apply", "crop": data["crop"], "source_size": size, "ok": ok,
         "before": str(backup), "screenshot": str(shot), "problems": problems})
    return 0 if ok else 1


def cmd_restore_crop(cfg, plat, args) -> int:
    if not args.yes:
        sys.exit("OBS のクロップを変更します。--yes を付けて実行してください")
    b = json.loads(args.file.read_text())
    with connect(cfg, plat) as ws:
        # overlay=False で丸ごと置き換える（relative 形式に戻す場合に cx/cy が残らないように）
        ws.set_filter_settings(b["source"], b["filter"], b["settings"], overlay=False)
        ws.set_filter_enabled(b["source"], b["filter"], b["enabled"])
        size = ws.get_source_size(b["source"])
    print(f"restored: {b['settings']} -> source size {size}")
    log({"cmd": "restore-crop", "file": str(args.file), "source_size": size})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("markers")
    p.add_argument("--view", help="カメラに映る範囲 WxH（mm）")
    p.add_argument("--size-mm", type=float)
    p.add_argument("--target-px", type=int)
    p.add_argument("--dpi", type=int, default=300)
    p = sub.add_parser("controls")
    p.add_argument("action", choices=["list", "check", "set"])
    p.add_argument("--yes", action="store_true")
    p.add_argument("--label", default="")
    sub.add_parser("snap")
    p = sub.add_parser("measure")
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--label", default="")
    p = sub.add_parser("apply")
    p.add_argument("--from", dest="src", type=Path, default=LAST)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("restore-crop")
    p.add_argument("file", type=Path)
    p.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    plat = get_platform(cfg)
    return {"markers": cmd_markers, "controls": cmd_controls, "snap": cmd_snap,
            "measure": cmd_measure, "apply": cmd_apply,
            "restore-crop": cmd_restore_crop}[args.cmd](cfg, plat, args)


if __name__ == "__main__":
    sys.exit(main())
