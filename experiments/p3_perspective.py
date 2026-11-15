"""P3: 射影補正の実機での総合確認（PLAN.md 12章、15章）。

    .venv/bin/python experiments/p3_perspective.py measure [--frames 20] [--label X]   # OBS 終了中
    .venv/bin/python experiments/p3_perspective.py apply --yes [--from FILE]            # OBS 起動中
    .venv/bin/python experiments/p3_perspective.py verify --yes [--label X]             # OBS 起動中
    .venv/bin/python experiments/p3_perspective.py restore FILE --yes                   # OBS 起動中

measure: カメラを OpenCV で開いて計測し、外側の四隅から補正後の矩形 R と行列を求める。縦横比は
         [perspective] aspect_source に従う（既定 markers: マーカーから推定。target_mm があれば照合する）。シェーダーと同じ
         計算で補正した画像を作って再検出し、結果を予測する。experiments/out/p3-last.json に保存する。
apply  : 整合チェック（フィルタの順序、device_id、素のサイズ）のあと、Perspective に行列、Crop/Pad に R を
         設定する。適用前の状態を p3-before-*.json に保存する（restore で戻せる）。
verify : Crop/Pad を一時的に無効にして補正後の画像全体を原寸で撮り、マーカーを再検出する。外側の四隅が R の
         四隅に来ること、各マーカーが正方形に写ること、四隅の縦横比が採用値と一致することを確認する。
         終了時に Crop/Pad の有効状態を戻す（数秒間、出力に映る）。
シーンアイテムの配置は e5_layout.py apply で行う。結果は experiments/out/p3-log.jsonl に追記する。
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e4_aruco as e4  # noqa: E402  共通の小さな関数（カメラの解決、フォーマット、保存）を再利用する

from obs_autocrop.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from obs_autocrop.models import CropRect  # noqa: E402
from obs_autocrop.obsctl.shader_filter import PerspectiveFilter, check_filter_order, load_shader  # noqa: E402
from obs_autocrop.platforms import get_platform  # noqa: E402
from obs_autocrop.vision import capture as vcap  # noqa: E402
from obs_autocrop.vision import detect as vdet  # noqa: E402
from obs_autocrop.vision.homography import (frames_from_detections, marker_ratio, rect_corners_edge,  # noqa: E402
                                            solve, to_edge, warp_like_shader)
from obs_autocrop.vision.markers import POSITIONS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "experiments" / "out"
LOG = OUT / "p3-log.jsonl"
LAST = OUT / "p3-last.json"
PAD_PX = 64  # 補正後の確認で足す白い余白（px）


def log(rec: dict) -> None:
    OUT.mkdir(exist_ok=True)
    rec = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), **rec}
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def stamp() -> str:
    return f"{dt.datetime.now():%Y%m%d-%H%M%S}"


def pcfg(cfg) -> dict:
    p = dict(cfg.get("perspective", {}))
    if p.get("target_mm"):
        p["target_mm"] = tuple(float(v) for v in p["target_mm"])
    return p


def quad_of(m: vdet.Measurement) -> list:
    return [m.corners[m.positions[p]] for p in POSITIONS]


def check_rectified(frame: np.ndarray, rect: CropRect, det, ids, aspect: float) -> dict:
    """補正後の画像（クロップ前の全体）でマーカーを再検出し、R との一致と正方形らしさを調べる。

    R がフレームの端に接すると、補正後のマーカーも端に接して検出されないため、白で余白を足して検出する。
    """
    m2, dets2 = vdet.measure([frame], det, ids, min_valid=1, pad_px=PAD_PX)
    quad2 = np.array(quad_of(m2))
    expect = rect_corners_edge(rect) - 0.5  # 画素中心の座標
    err = quad2 - expect
    ratios = [marker_ratio(to_edge(dets2[0][i][0])) for i in ids if len(dets2[0].get(i, [])) == 1]
    e = to_edge(quad2)
    top, bottom = np.linalg.norm(e[1] - e[0]), np.linalg.norm(e[2] - e[3])
    left, right = np.linalg.norm(e[3] - e[0]), np.linalg.norm(e[2] - e[1])
    quad_aspect = ((left + right) / 2) / ((top + bottom) / 2)
    return {
        "corner_err_max": round(float(np.abs(err).max()), 3),
        "corner_err": np.round(err, 3).tolist(),
        "crop_from_rectified": m2.crop.__dict__,
        "marker_ratio": [round(r, 4) for r in ratios],
        "marker_ratio_dev_max": round(float(max(abs(r - 1) for r in ratios)), 4) if ratios else None,
        "quad_aspect": round(float(quad_aspect), 5),
        "quad_aspect_vs_target": round(float(quad_aspect / aspect - 1), 5),
        "skew_warnings": [w for w in m2.warnings if "傾き" in w],
    }


# ---------------------------------------------------------------- measure（OBS 終了中）

def cmd_measure(cfg, plat, args) -> int:
    e4.require_obs_stopped(plat)
    p = pcfg(cfg)
    node, fmt = e4.resolve_node(cfg, plat), e4.capture_format(cfg, plat)
    mc = cfg.get("measure", {})
    ids = tuple(cfg.get("markers", {}).get("ids", [0, 1, 2, 3]))
    det = e4.detector(cfg)
    s = stamp()
    print(f"camera {node}  format {fmt}  frames={args.frames}")
    frames = vcap.capture(plat.capture_backend, node, fmt, n=args.frames, warmup=mc.get("warmup_frames", 30))
    m, dets = vdet.measure(frames, det, ids, mc.get("min_valid", 5), 0, mc.get("skew_tol_ratio", 0.01))
    quad = quad_of(m)
    fa = frames_from_detections(dets, m.positions)
    res = solve(quad, fmt.width, fmt.height, target_mm=p.get("target_mm"),
                aspect_source=p.get("aspect_source", "markers"), frames_for_aspect=fa,
                aspect_verify=True, verify_tol=p.get("aspect_verify_tol", 0.01),
                residual_warn=p.get("aspect_residual_warn", 0.01),
                max_skew_warn_deg=p.get("max_skew_warn_deg", 25))
    est = res.estimate
    print(f"valid frames {m.frames_valid}/{m.frames_total}  外側の四隅（画素中心）: "
          + ", ".join(f"{pos}={tuple(round(v, 1) for v in m.corners[i])}" for pos, i in m.positions.items()))
    print(f"傾き（上・右・下・左）: {[round(a, 2) for a in res.skew_deg]}°  補正前の外接矩形: {m.crop}")
    print(f"縦横比: 採用 {res.aspect:.5f}（{res.aspect_source}）  マーカーからの推定 {est.aspect:.5f}"
          f"（target_mm との差 {est.aspect / (p['target_mm'][1] / p['target_mm'][0]) - 1:+.3%}、"
          f"残差 最大 {est.residual_max:.3%}、フレーム間 {est.spread:.3%}）" if p.get("target_mm") else "")
    print(f"R = {res.rect}")
    print("M =", np.array2string(np.array(res.matrix), precision=6, suppress_small=True).replace("\n", ""))
    for w in res.warnings + m.warnings:
        print(f"WARN: {w}")
    # 予測: シェーダーと同じ計算で補正して再検出する
    rectified, _ = warp_like_shader(frames[-1], np.array(res.matrix))
    try:
        pred = check_rectified(rectified, res.rect, det, ids, res.aspect)
        print(f"予測（補正後に再検出）: 四隅の誤差 最大 {pred['corner_err_max']}px  "
              f"マーカーの縦横比の偏り 最大 {pred['marker_ratio_dev_max']:.3%}  四隅の縦横比と採用値の差 "
              f"{pred['quad_aspect_vs_target']:+.3%}")
    except vdet.MeasurementFailed as e:  # 予測は確認用なので、失敗しても計算結果は保存する
        pred = {"error": str(e)}
        print(f"WARN: 予測（補正後の再検出）に失敗しました: {e}")
    e4.save_img(frames[-1], f"p3-{s}-frame.jpg", 95)
    ov = e4.save_img(vdet.draw_overlay(frames[-1], dets[-1], m), f"p3-{s}-overlay.jpg")
    rr = rectified.copy()
    r = res.rect
    cv2.rectangle(rr, (r.x, r.y), (r.x + r.w - 1, r.y + r.h - 1), (0, 255, 0), 3)
    rp = e4.save_img(rr, f"p3-{s}-rectified-sim.jpg")
    print(f"saved: {ov}\n       {rp}（補正の予測。緑が R）")
    out = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), "node": node, "format": str(fmt),
           "measurement": m.to_dict(), "perspective": res.to_dict(), "prediction": pred, "label": args.label}
    LAST.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"       {LAST}")
    log({"cmd": "measure", **out})
    return 0


# ---------------------------------------------------------------- apply / verify / restore（OBS 起動中）

def setup(cfg, plat):
    ws = e4.connect(cfg, plat)
    t, p = cfg["target"], cfg.get("perspective", {})
    shader = load_shader(ROOT / p["shader_path"]) if p.get("shader_path") else load_shader()
    pf = PerspectiveFilter(ws, t["source_name"], p.get("filter_name", "Perspective"), shader)
    return ws, pf, t.get("filter_name", "Crop/Pad")


def cmd_apply(cfg, plat, args) -> int:
    if not args.yes:
        sys.exit("OBS の射影補正の行列とクロップを変更します。--yes を付けて実行してください")
    data = json.loads(args.src.read_text())
    res = data["perspective"]
    rect = CropRect(**res["rect"])
    fw, fh = res["frame_size"]
    m = np.array(res["matrix"], float)
    s = stamp()
    ws, pf, crop_name = setup(cfg, plat)
    with ws:
        st = pf.require_ready()
        crop = ws.get_filter(pf.source, crop_name)
        problems = check_filter_order(ws.list_filters(pf.source), crop_name, pf.name)
        dev = ws.get_input_settings(pf.source)["inputSettings"].get("device_id")
        if not plat.source_adapter.same_device(dev, data["node"]):
            problems.append(f"device_id {dev!r} が計測したカメラ {data['node']} と一致しません")
        # 素のサイズ（射影補正はサイズを変えないので、Crop/Pad だけを一時的に無効にして取得する）
        ws.set_filter_enabled(pf.source, crop_name, False)
        try:
            time.sleep(0.5)
            raw = ws.get_source_size(pf.source)
        finally:
            ws.set_filter_enabled(pf.source, crop_name, crop.enabled)
        if raw != (fw, fh):
            problems.append(f"ソースの素のサイズ {raw} が計測時の {fw}x{fh} と一致しません")
        if problems and not args.force:
            for x in problems:
                print(f"ERROR: {x}")
            print("中断しました（--force で警告のみにできます）")
            return 1
        before = OUT / f"p3-before-{s}.json"
        before.write_text(json.dumps({"source": pf.source, "perspective": {"name": pf.name, "enabled": st.enabled,
                                                                          "matrix": st.matrix},
                                      "crop": {"name": crop_name, "enabled": crop.enabled,
                                               "settings": crop.settings}}, ensure_ascii=False, indent=2))
        pf.set_matrix(m)
        pf.set_enabled(True)
        ws.set_crop(pf.source, crop_name, rect)
        ws.set_filter_enabled(pf.source, crop_name, True)
        time.sleep(0.5)
        size = ws.get_source_size(pf.source)
        shot = OUT / f"p3-{s}-applied.png"
        shot.write_bytes(ws.screenshot(pf.source))
    ok = size == (rect.w, rect.h)
    print(f"applied: matrix -> {pf.name}, crop {rect} -> source size {size} {'OK' if ok else 'NG'}")
    for x in problems:
        print(f"WARN: {x}")
    print(f"before : {before}（restore で戻せます）\nshot   : {shot}")
    print("シーンアイテムの配置: .venv/bin/python experiments/e5_layout.py apply --yes")
    log({"cmd": "apply", "rect": rect.__dict__, "size": size, "ok": ok, "before": str(before),
         "problems": problems})
    return 0 if ok else 1


def cmd_verify(cfg, plat, args) -> int:
    if not args.yes:
        sys.exit("Crop/Pad を一時的に無効にして補正後の全体を撮影します（出力に映る）。--yes を付けて実行してください")
    data = json.loads(args.src.read_text())
    res = data["perspective"]
    rect = CropRect(**res["rect"])
    ids = tuple(cfg.get("markers", {}).get("ids", [0, 1, 2, 3]))
    s = stamp()
    ws, pf, crop_name = setup(cfg, plat)
    with ws:
        st = pf.require_ready()
        if not st.enabled or not np.allclose(st.matrix, res["matrix"]):
            print("WARN: Perspective の行列が計測結果と違うか、無効です（apply を実行しましたか）")
        crop = ws.get_filter(pf.source, crop_name)
        ws.set_filter_enabled(pf.source, crop_name, False)
        try:
            time.sleep(0.6)
            png = ws.screenshot(pf.source)  # 原寸（時間がかかる）
        finally:
            ws.set_filter_enabled(pf.source, crop_name, crop.enabled)
    img = cv2.cvtColor(np.array(Image.open(io.BytesIO(png)).convert("RGB")), cv2.COLOR_RGB2BGR)
    path = e4.save_img(img, f"p3-{s}-verify-full.png")
    r = check_rectified(img, rect, e4.detector(cfg), ids, res["aspect"])
    print(f"補正後（OBS の出力、クロップ前）{img.shape[1]}x{img.shape[0]}: 外側の四隅と R の四隅の差 最大 "
          f"{r['corner_err_max']}px")
    print(f"  各マーカーの縦横比: {r['marker_ratio']}（1 からの偏り 最大 {r['marker_ratio_dev_max']:.3%}）")
    print(f"  四隅の縦横比 {r['quad_aspect']:.5f}  採用値 {res['aspect']:.5f}  差 {r['quad_aspect_vs_target']:+.3%}")
    print(f"  補正後の外接矩形 {r['crop_from_rectified']}  R {rect.__dict__}")
    for w in r["skew_warnings"]:
        print(f"  WARN: {w}")
    zm = vdet.measure([img], e4.detector(cfg), ids, min_valid=1, pad_px=PAD_PX)[0]
    e4.save_img(vdet.corner_zooms(img, zm), f"p3-{s}-verify-corners.png")
    print(f"saved: {path}")
    log({"cmd": "verify", "label": args.label, "rect": rect.__dict__, **r})
    return 0 if r["corner_err_max"] <= args.tol else 1


def cmd_restore(cfg, plat, args) -> int:
    if not args.yes:
        sys.exit("射影補正とクロップを適用前の状態に戻します。--yes を付けて実行してください")
    b = json.loads(args.file.read_text())
    ws, pf, _ = setup(cfg, plat)
    with ws:
        if b["perspective"]["matrix"]:
            pf.set_matrix(b["perspective"]["matrix"])
        pf.set_enabled(bool(b["perspective"]["enabled"]))
        c = b["crop"]
        ws.set_filter_settings(pf.source, c["name"], c["settings"], overlay=False)
        ws.set_filter_enabled(pf.source, c["name"], c["enabled"])
        time.sleep(0.5)
        size = ws.get_source_size(pf.source)
    print(f"restored -> source size {size}")
    log({"cmd": "restore", "file": str(args.file), "size": size})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("measure")
    q.add_argument("--frames", type=int, default=20)
    q.add_argument("--label", default="")
    q = sub.add_parser("apply")
    q.add_argument("--from", dest="src", type=Path, default=LAST)
    q.add_argument("--yes", action="store_true")
    q.add_argument("--force", action="store_true")
    q = sub.add_parser("verify")
    q.add_argument("--from", dest="src", type=Path, default=LAST)
    q.add_argument("--yes", action="store_true")
    q.add_argument("--label", default="")
    q.add_argument("--tol", type=float, default=1.0, help="四隅の許容差（px）")
    q = sub.add_parser("restore")
    q.add_argument("file", type=Path)
    q.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    plat = get_platform(cfg)
    return {"measure": cmd_measure, "apply": cmd_apply, "verify": cmd_verify,
            "restore": cmd_restore}[args.cmd](cfg, plat, args)


if __name__ == "__main__":
    sys.exit(main())
