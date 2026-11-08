"""ObsProcess の Linux 実装（PLAN.md 6.4）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import psutil

FLATPAK_APP_ID = "com.obsproject.Studio"


class LinuxObsProcess:
    def __init__(self, launch_cmd: list[str] | None = None):
        self.launch_cmd = launch_cmd or ["obs"]

    def running_pids(self) -> list[int]:
        pids = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            cmd = p.info["cmdline"] or []
            # E0: PPA 版は name="obs"。Flatpak 版は cmdline にアプリ ID を含む
            if p.info["name"] == "obs" or any(FLATPAK_APP_ID in c for c in cmd):
                pids.append(p.info["pid"])
        return pids

    def is_running(self) -> bool:
        return bool(self.running_pids())

    def launch(self, collection: str | None = None, scene: str | None = None,
               extra_args: list[str] | tuple = (), log_path: Path | None = None) -> subprocess.Popen:
        cmd = [*self.launch_cmd]
        if collection:
            cmd += ["--collection", collection]
        if scene:
            cmd += ["--scene", scene]
        cmd += list(extra_args)
        out = open(log_path, "ab") if log_path else subprocess.DEVNULL
        return subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
