"""E0: 実環境の検査（PLAN.md 12章）。

読み取り専用。OBS の起動中でも実行できる。
結果を標準出力に表示し、experiments/out/e0-<タイムスタンプ>.json に保存する。
パスワード類はマスクする。

    .venv/bin/python experiments/e0_inspect.py [--obs-config DIR] [--no-save]
"""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "out"
SECRET_KEY_RE = re.compile(r"pass|secret|token|key$", re.I)

CANDIDATE_CONFIG_DIRS = {
    "native": Path.home() / ".config/obs-studio",
    "flatpak": Path.home() / ".var/app/com.obsproject.Studio/config/obs-studio",
    "snap": Path.home() / "snap/obs-studio/current/.config/obs-studio",
}


# ---------------------------------------------------------------- helpers

def run(cmd: list[str], timeout: float = 10) -> dict:
    if shutil.which(cmd[0]) is None:
        return {"cmd": cmd, "error": "not found"}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "error": "timeout"}
    return {"cmd": cmd, "rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def mask(obj):
    if isinstance(obj, dict):
        return {
            k: ("***" if SECRET_KEY_RE.search(k) and v not in ("", None) else mask(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [mask(v) for v in obj]
    return obj


def read_ini(path: Path) -> dict[str, dict[str, str]] | None:
    if not path.is_file():
        return None
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # OBS のキーは大文字小文字を区別する
    cp.read(path, encoding="utf-8-sig")
    return {s: dict(cp[s]) for s in cp.sections()}


def fourcc_str(v: int) -> str:
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))


def decode_v4l2_settings(s: dict) -> dict:
    """v4l2_input の settings のうち、パックされた値をデコードする。キーが無いものは 'absent'。"""
    out: dict = {}
    if "pixelformat" in s:
        v = s["pixelformat"]
        out["pixelformat"] = None if v == -1 else fourcc_str(v)
    else:
        out["pixelformat"] = "absent"
    if "resolution" in s:
        v = s["resolution"]
        out["resolution"] = None if v == -1 else f"{v >> 16}x{v & 0xFFFF}"
    else:
        out["resolution"] = "absent"
    if "framerate" in s:
        v = s["framerate"]
        out["framerate"] = None if v == -1 else f"{v >> 16}/{v & 0xFFFF}"
    else:
        out["framerate"] = "absent"
    return out


# ---------------------------------------------------------------- sections

def inspect_install() -> dict:
    info: dict = {"which_obs": shutil.which("obs")}
    if info["which_obs"]:
        info["obs_realpath"] = str(Path(info["which_obs"]).resolve())
        info["version"] = run(["obs", "--version"]).get("stdout", "").strip()
    dpkg = run(["dpkg-query", "-W", "-f=${Package} ${Version} ${Status}\n", "obs-studio"])
    info["dpkg"] = dpkg.get("stdout", "").strip() or None
    pol = run(["apt-cache", "policy", "obs-studio"])
    info["apt_policy"] = pol.get("stdout", "").strip() or None
    fp = run(["flatpak", "list", "--app", "--columns=application,version"])
    info["flatpak"] = [l for l in fp.get("stdout", "").splitlines() if "obsproject" in l] or None
    sn = run(["snap", "list", "obs-studio"])
    info["snap"] = sn.get("stdout", "").strip() if sn.get("rc") == 0 else None
    return info


def inspect_config(explicit: Path | None) -> dict:
    cands = {k: str(p) for k, p in CANDIDATE_CONFIG_DIRS.items()}
    exists = {k: CANDIDATE_CONFIG_DIRS[k].is_dir() for k in cands}
    root = explicit or next((CANDIDATE_CONFIG_DIRS[k] for k, e in exists.items() if e), None)
    info: dict = {"candidates": cands, "exists": exists, "root": str(root) if root else None}
    if root is None:
        return info

    global_ini = read_ini(root / "global.ini")
    user_ini = read_ini(root / "user.ini")
    info["global_ini_sections"] = {s: sorted(v) for s, v in (global_ini or {}).items()}
    info["user_ini_sections"] = {s: sorted(v) for s, v in (user_ini or {}).items()}
    info["global_ini_Locations"] = (global_ini or {}).get("Locations")

    # 現在のコレクション名の取得元
    src = {}
    for fname, ini in (("user.ini", user_ini), ("global.ini", global_ini)):
        basic = (ini or {}).get("Basic", {})
        src[fname] = {k: basic.get(k) for k in ("SceneCollection", "SceneCollectionFile",
                                                 "Profile", "ProfileDir")}
    info["current_collection_sources"] = src
    chosen = next((v for v in src.values() if v.get("SceneCollectionFile")), None)

    # OBS 31+ の [Locations] SceneCollections があればそこを基準にする
    loc = (global_ini or {}).get("Locations", {})
    scenes_base = Path(loc["SceneCollections"]) / "obs-studio" if loc.get("SceneCollections") else root
    scenes_dir = scenes_base / "basic/scenes"
    info["scenes_dir"] = str(scenes_dir)
    info["scene_files"] = sorted(p.name for p in scenes_dir.glob("*.json")) if scenes_dir.is_dir() else []
    if chosen:
        info["current_collection_file"] = str(scenes_dir / chosen["SceneCollectionFile"])

    ws = root / "plugin_config/obs-websocket/config.json"
    info["websocket_config_path"] = str(ws)
    if ws.is_file():
        info["websocket_config"] = mask(json.loads(ws.read_text()))
    else:
        info["websocket_config"] = None
    return info


def inspect_scene_collection(path: Path) -> dict:
    d = json.loads(path.read_text())
    info: dict = {
        "path": str(path),
        "top_keys": list(d),
        "version": d.get("version"),
        "current_scene": d.get("current_scene"),
        "current_program_scene": d.get("current_program_scene"),
        "canvases": d.get("canvases"),
        "sources": [],
    }
    for s in d.get("sources", []):
        e = {
            "name": s.get("name"),
            "uuid": s.get("uuid"),
            "id": s.get("id"),
            "versioned_id": s.get("versioned_id"),
            "entry_keys": list(s),
            "settings_keys": list(s.get("settings", {})),
            "filters": [
                {"index": i, "name": f.get("name"), "id": f.get("id"),
                 "enabled": f.get("enabled"), "settings": f.get("settings"),
                 "entry_keys": list(f)}
                for i, f in enumerate(s.get("filters", []))
            ],
        }
        if s.get("id") == "v4l2_input":
            e["settings"] = s.get("settings")
            e["decoded"] = decode_v4l2_settings(s.get("settings", {}))
        if s.get("id") == "scene":
            e["items"] = [
                {k: it.get(k) for k in ("name", "source_uuid", "id", "visible")}
                for it in s.get("settings", {}).get("items", [])
            ]
            e["item_keys"] = sorted({k for it in s.get("settings", {}).get("items", []) for k in it})
        info["sources"].append(e)
    return info


def inspect_processes() -> dict:
    try:
        import psutil
    except ImportError:
        return {"error": "psutil not installed"}
    procs = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        name = p.info["name"] or ""
        cmd = p.info["cmdline"] or []
        joined = " ".join(cmd)
        if name == "obs" or "obs-studio" in joined or "com.obsproject.Studio" in joined \
                or (cmd and Path(cmd[0]).name == "obs"):
            procs.append(p.info)
    return {"matches": procs}


def _usb_parent(dev):
    return dev.find_parent("usb", "usb_device")


def inspect_cameras() -> dict:
    info: dict = {"v4l_links": {}}
    for kind in ("by-id", "by-path"):
        d = Path("/dev/v4l") / kind
        info["v4l_links"][kind] = (
            {p.name: str(p.resolve()) for p in sorted(d.iterdir())} if d.is_dir() else None
        )
    try:
        import pyudev
    except ImportError:
        info["udev"] = {"error": "pyudev not installed"}
        return info

    devices = []
    for dev in pyudev.Context().list_devices(subsystem="video4linux"):
        props = dict(dev.properties)
        attrs = {}
        for a in ("index", "name"):
            try:
                attrs[a] = dev.attributes.asstring(a)
            except KeyError:
                attrs[a] = None
        usb = _usb_parent(dev)
        usb_info = None
        if usb is not None:
            usb_info = {}
            for a in ("speed", "version", "busnum", "devpath", "idVendor", "idProduct",
                      "serial", "manufacturer", "product"):
                try:
                    usb_info[a] = usb.attributes.asstring(a).strip()
                except KeyError:
                    usb_info[a] = None
        node = dev.device_node
        e = {
            "node": node,
            "attributes": attrs,
            "is_capture": ":capture:" in props.get("ID_V4L_CAPABILITIES", ""),
            "device_links": sorted(dev.device_links),
            "properties": {k: v for k, v in props.items()
                           if k.startswith(("ID_", "DEVLINKS", "DEVPATH", "MAJOR", "MINOR"))},
            "usb_device": usb_info,
        }
        if e["is_capture"] and attrs.get("index") == "0":
            e["formats"] = run(["v4l2-ctl", "-d", node, "--list-formats-ext"]).get("stdout")
            e["current_format"] = run(["v4l2-ctl", "-d", node, "--get-fmt-video", "--get-parm"]).get("stdout")
            e["controls"] = run(["v4l2-ctl", "-d", node, "--list-ctrls-menus"]).get("stdout")
        devices.append(e)
    info["udev"] = sorted(devices, key=lambda x: x["node"] or "")
    info["v4l2_list_devices"] = run(["v4l2-ctl", "--list-devices"]).get("stdout")
    return info


# ---------------------------------------------------------------- report

def summarize(r: dict) -> str:
    L = []
    ins, cfg = r["install"], r["config"]
    L.append("== OBS install")
    L.append(f"  which obs : {ins.get('which_obs')} -> {ins.get('obs_realpath')}")
    L.append(f"  version   : {ins.get('version')}")
    L.append(f"  dpkg      : {ins.get('dpkg')}")
    L.append(f"  flatpak   : {ins.get('flatpak')}   snap: {ins.get('snap')}")

    L.append("== OBS config")
    L.append(f"  root      : {cfg.get('root')}   candidates exist: {cfg.get('exists')}")
    L.append(f"  [Locations]: {cfg.get('global_ini_Locations')}")
    for f, v in (cfg.get("current_collection_sources") or {}).items():
        L.append(f"  {f:10}: {v}")
    L.append(f"  scenes_dir: {cfg.get('scenes_dir')}  files: {cfg.get('scene_files')}")
    L.append(f"  current   : {cfg.get('current_collection_file')}")
    L.append(f"  websocket : {cfg.get('websocket_config_path')}")
    L.append(f"              {cfg.get('websocket_config')}")

    sc = r.get("scene_collection")
    if sc:
        L.append("== Scene collection")
        L.append(f"  top_keys  : {sc['top_keys']}")
        for s in sc["sources"]:
            L.append(f"  - {s['name']!r} id={s['id']} uuid={s['uuid']}")
            if s["id"] == "v4l2_input":
                L.append(f"      settings: {s['settings']}")
                L.append(f"      decoded : {s['decoded']}")
            for f in s["filters"]:
                L.append(f"      filter[{f['index']}] {f['name']!r} id={f['id']} "
                         f"enabled={f['enabled']} settings={f['settings']}")
            if s["id"] == "scene":
                for it in s["items"]:
                    L.append(f"      item: {it}")

    L.append("== OBS processes")
    for p in r["processes"].get("matches", []):
        L.append(f"  pid={p['pid']} name={p['name']} exe={p['exe']} cmdline={p['cmdline']}")
    if not r["processes"].get("matches"):
        L.append("  (not running)")

    cam = r["cameras"]
    L.append("== Cameras")
    for kind, links in cam["v4l_links"].items():
        L.append(f"  /dev/v4l/{kind}:")
        for k, v in (links or {}).items():
            L.append(f"    {k} -> {v}")
    for d in cam.get("udev", []) if isinstance(cam.get("udev"), list) else []:
        p = d["properties"]
        L.append(f"  {d['node']}: name={d['attributes']['name']!r} index={d['attributes']['index']} "
                 f"capture={d['is_capture']} vid:pid={p.get('ID_VENDOR_ID')}:{p.get('ID_MODEL_ID')} "
                 f"serial={p.get('ID_SERIAL_SHORT')} usb_speed={(d['usb_device'] or {}).get('speed')}Mbps")
        if d.get("current_format"):
            for line in d["current_format"].splitlines():
                if re.search(r"Width/Height|Pixel Format|Frames per second", line):
                    L.append(f"      current {line.strip()}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--obs-config", type=Path, help="OBS 設定ルート（省略時は候補から自動検出）")
    ap.add_argument("--no-save", action="store_true", help="JSON を保存しない")
    args = ap.parse_args()

    r: dict = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "python": sys.version,
        "install": inspect_install(),
        "config": inspect_config(args.obs_config),
    }
    cur = r["config"].get("current_collection_file")
    r["scene_collection"] = inspect_scene_collection(Path(cur)) if cur and Path(cur).is_file() else None
    r["processes"] = inspect_processes()
    r["cameras"] = inspect_cameras()

    print(summarize(r))
    if not args.no_save:
        OUT_DIR.mkdir(exist_ok=True)
        out = OUT_DIR / f"e0-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
        out.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        print(f"\nsaved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
