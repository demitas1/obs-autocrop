"""CameraControls の Linux 実装: v4l2-ctl（PLAN.md 7.3）。"""

from __future__ import annotations

import re
import subprocess

V4L2_CTL = "v4l2-ctl"
_LINE = re.compile(r"^\s*(\w+)\s+0x[0-9a-f]+\s+\((\w+)\)\s*:\s*(.*)$")
_KV = re.compile(r"(\w+)=(\S+)")


class ControlError(RuntimeError):
    pass


def parse_list_ctrls(text: str) -> dict[str, dict]:
    """`v4l2-ctl --list-ctrls` の出力を {名前: {type, min, max, step, default, value, inactive}} にする。"""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        name, typ, rest = m.groups()
        info: dict = {"type": typ}
        for k, v in _KV.findall(rest):
            if k == "flags":
                info["flags"] = v
                continue
            try:
                info[k] = int(v)
            except ValueError:
                info[k] = v
        info["inactive"] = "inactive" in info.get("flags", "")
        out[name] = info
    return out


def parse_get_ctrl(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = int(v.strip())
    return out


class LinuxCameraControls:
    def _run(self, node: str, *args: str) -> str:
        p = subprocess.run([V4L2_CTL, "-d", node, *args], capture_output=True, text=True)
        if p.returncode != 0:
            raise ControlError(f"v4l2-ctl {' '.join(args)}: {p.stderr.strip() or p.returncode}")
        return p.stdout

    def list_controls(self, node: str) -> dict[str, dict]:
        return parse_list_ctrls(self._run(node, "--list-ctrls"))

    def get(self, node: str, names: list[str]) -> dict[str, int]:
        if not names:
            return {}
        return parse_get_ctrl(self._run(node, f"--get-ctrl={','.join(names)}"))

    def set(self, node: str, values: dict[str, int]) -> None:
        for k, v in values.items():
            self._run(node, f"--set-ctrl={k}={int(v)}")
