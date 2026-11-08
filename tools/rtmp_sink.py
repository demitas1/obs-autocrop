#!/usr/bin/env python3
"""検証用のローカル RTMP 受信サーバー（ffmpeg を使う）と、映像ファイルのタイムスタンプ検査。

外部に配信せずに OBS の配信出力を試すためのツール。標準ライブラリのみで動き、
ffmpeg / ffprobe が必要。

    # 受信して保存する（OBS の配信先: サーバー rtmp://127.0.0.1:1935/live、ストリームキー test）
    python3 tools/rtmp_sink.py serve [--port 1935] [--app live] [--key test] [--out FILE] [--loop]

    # 保存しない（受信だけ）
    python3 tools/rtmp_sink.py serve --discard

    # 映像ファイル（受信した FLV や OBS の録画）のタイムスタンプを検査する
    python3 tools/rtmp_sink.py analyze FILE [--fps 30]

serve は 1 回の配信を受け終わると終了する（--loop で次の接続を待ち続ける）。Ctrl+C で終了。
既定では 127.0.0.1 でのみ待ち受ける。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "experiments" / "out"


def require(tool: str) -> None:
    if shutil.which(tool) is None:
        sys.exit(f"{tool} が見つかりません（sudo apt install ffmpeg）")


def serve_once(url: str, out: Path | None) -> int:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats",
           "-listen", "1", "-i", url, "-c", "copy"]
    cmd += ["-f", "flv", str(out)] if out else ["-f", "null", "-"]
    return subprocess.run(cmd).returncode


def cmd_serve(args) -> int:
    require("ffmpeg")
    url = f"rtmp://{args.host}:{args.port}/{args.app}/{args.key}"
    print(f"listening: {url}")
    print(f"  OBS の設定: サービス=カスタム, サーバー=rtmp://{args.host}:{args.port}/{args.app}, "
          f"ストリームキー={args.key}")
    try:
        while True:
            out = None
            if not args.discard:
                out = args.out or DEFAULT_OUT_DIR / f"rtmp-{dt.datetime.now():%Y%m%d-%H%M%S}.flv"
                out.parent.mkdir(parents=True, exist_ok=True)
                print(f"  saving to: {out}")
            rc = serve_once(url, out)
            print(f"stream ended (ffmpeg rc={rc})")
            if out and out.exists():
                print_report(analyze(out, args.fps))
            if not args.loop:
                return rc
            args.out = None  # 2 回目以降は新しいファイル名にする
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


def analyze(path: Path, fps: float = 30) -> dict:
    """映像ストリームのパケットのタイムスタンプから、欠落（間隔の広がり）を調べる。"""
    require("ffprobe")
    meta = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,avg_frame_rate",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    pts = sorted(float(l.split(",")[0]) for l in out.splitlines() if l.strip() and l != "N/A")
    frame = 1 / fps
    gaps = [(round(a, 3), round((b - a) * 1000, 1)) for a, b in zip(pts, pts[1:]) if b - a > 1.5 * frame]
    span = pts[-1] - pts[0] if len(pts) > 1 else 0
    return {
        "file": str(path),
        "streams": meta.get("streams", []),
        "duration_s": float(meta.get("format", {}).get("duration", 0) or 0),
        "packets": len(pts),
        "expected_packets": round(span * fps) + 1 if pts else 0,
        "gaps_over_1.5_frames": gaps,  # (時刻 s, 間隔 ms)
        "max_interval_ms": round(max((b - a for a, b in zip(pts, pts[1:])), default=0) * 1000, 1),
    }


def print_report(r: dict) -> None:
    for s in r["streams"]:
        if s.get("codec_type") == "video":
            print(f"  video: {s.get('codec_name')} {s.get('width')}x{s.get('height')} "
                  f"{s.get('avg_frame_rate')}")
    print(f"  duration={r['duration_s']:.2f}s packets={r['packets']} "
          f"(expected {r['expected_packets']}) max_interval={r['max_interval_ms']}ms")
    gaps = r["gaps_over_1.5_frames"]
    print(f"  gaps>1.5 frames: {len(gaps)}" + (f" {gaps[:10]}" if gaps else ""))


def cmd_analyze(args) -> int:
    r = analyze(args.file, args.fps)
    print_report(r)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)  # パイプ経由でも進捗をすぐ出す
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="RTMP を受信する")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=1935)
    p.add_argument("--app", default="live")
    p.add_argument("--key", default="test")
    p.add_argument("--out", type=Path, help="保存先（既定: experiments/out/rtmp-<時刻>.flv）")
    p.add_argument("--discard", action="store_true", help="保存しない")
    p.add_argument("--loop", action="store_true", help="配信終了後も次の接続を待つ")
    p.add_argument("--fps", type=float, default=30, help="検査で使うフレームレート")
    p = sub.add_parser("analyze", help="映像ファイルのタイムスタンプを検査する")
    p.add_argument("file", type=Path)
    p.add_argument("--fps", type=float, default=30)
    p.add_argument("--json", action="store_true", help="結果を JSON でも出力する")
    args = ap.parse_args()
    return {"serve": cmd_serve, "analyze": cmd_analyze}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
