#!/usr/bin/env python3
"""Report useful, local build storage and activity indicators without a build."""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


def directory_size(path: Path) -> str:
    if not path.exists():
        return "not present"
    result = shutil.disk_usage(path)
    try:
        import subprocess

        output = subprocess.check_output(["du", "-sh", str(path)], text=True)
        return output.split("\t", 1)[0]
    except (OSError, subprocess.SubprocessError):
        return f"{result.used // (1024 * 1024)} MiB used on filesystem"


def latest_path(path: Path) -> str:
    if not path.exists():
        return "not present"
    entries = [entry for entry in path.rglob("*") if entry.is_file()]
    if not entries:
        return "no files"
    latest = max(entries, key=lambda entry: entry.stat().st_mtime)
    timestamp = datetime.fromtimestamp(latest.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    return f"{latest} ({timestamp})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    args = parser.parse_args()
    build_dir = args.build_dir.resolve()
    tmp_dir = build_dir / "tmp"

    print(f"Herdbake build health: {build_dir}\n")
    disk = shutil.disk_usage(build_dir)
    print(f"Filesystem free: {disk.free // (1024 * 1024 * 1024)} GiB / {disk.total // (1024 * 1024 * 1024)} GiB")
    print(f"tmp: {directory_size(tmp_dir)}")
    print(f"sstate-cache: {directory_size(build_dir / 'sstate-cache')}")
    print(f"downloads: {directory_size(build_dir / 'downloads')}")
    print(f"buildhistory: {directory_size(build_dir / 'buildhistory')}")
    print()
    print(f"Latest cooker log: {latest_path(tmp_dir / 'log' / 'cooker')}")
    print(f"Latest buildstats data: {latest_path(tmp_dir / 'buildstats')}")
    print("\nThis report did not run BitBake or change build state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
