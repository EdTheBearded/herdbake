#!/usr/bin/env python3
"""Report whether an active Yocto build is ready for Herdbake routing."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


START = "# >>> herdbake terminal routing >>>"
END = "# <<< herdbake terminal routing <<<"


def check(label: str, ok: bool, detail: str) -> bool:
    print(f"{'OK' if ok else 'FAIL'}  {label}: {detail}")
    return ok


def command_version(command: str) -> str | None:
    if not shutil.which(command):
        return None
    try:
        result = subprocess.run(
            [command, "--version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return command
    return (result.stdout or result.stderr).splitlines()[0] if result.returncode == 0 else command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, required=True)
    args = parser.parse_args()

    build_dir = args.build_dir.resolve()
    plugin_root = args.plugin_root.resolve()
    local_conf = build_dir / "conf" / "local.conf"
    route = plugin_root / "scripts" / "route.py"
    print(f"Herdbake doctor: {build_dir}")
    print()

    good = True
    good &= check("build directory", build_dir.is_dir(), str(build_dir))
    good &= check("local.conf", local_conf.is_file(), str(local_conf))
    good &= check("router", route.is_file(), str(route))

    if local_conf.is_file():
        content = local_conf.read_text(errors="replace")
        start = content.count(START)
        end = content.count(END)
        managed = start == end == 1
        good &= check("managed block", managed, "exactly one block" if managed else f"start={start}, end={end}")
        expected = f"python3 {route} --title=\"{{title}}\" {{command}}"
        current = expected in content
        good &= check(
            "terminal command",
            current,
            "BitBake-safe title formatting" if current else "re-run Herdbake setup to update it",
        )

    good &= check("Python", sys.version_info >= (3, 11), sys.version.split()[0])
    try:
        compile(route.read_text(), str(route), "exec")
        router_valid = True
    except (OSError, SyntaxError):
        router_valid = False
    good &= check("router syntax", router_valid, "valid Python" if router_valid else "cannot compile route.py")

    for command in ("herdr", "bitbake", "bitbake-layers"):
        version = command_version(command)
        good &= check(command, version is not None, version or "not found on PATH")

    print()
    if good:
        print("Ready. Interactive BitBake tasks should route into Herdr panes.")
        return 0
    print("Not ready. Correct the failed checks above; the doctor made no changes.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
