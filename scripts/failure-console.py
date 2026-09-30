#!/usr/bin/env python3
"""Show the latest BitBake task log without rerunning or cleaning anything."""
from __future__ import annotations

import argparse
from pathlib import Path


def latest_log(build_dir: Path) -> Path | None:
    logs = list((build_dir / "tmp" / "work").glob("**/temp/log.do_*"))
    return max(logs, key=lambda path: path.stat().st_mtime) if logs else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--lines", type=int, default=240)
    parser.add_argument("--path-only", action="store_true")
    args = parser.parse_args()

    log = latest_log(args.build_dir)
    if log is None:
        print(f"herdbake: no task logs found under {args.build_dir / 'tmp/work'}")
        return 1

    if args.path_only:
        print(log)
        return 0

    print(f"Herdbake latest task log\n{log}\n")
    print(f"{'=' * 72}\n")
    with log.open(errors="replace") as stream:
        lines = stream.readlines()
    print("".join(lines[-args.lines:]), end="" if lines and lines[-1].endswith("\n") else "\n")

    temp_dir = log.parent
    task = log.name.removeprefix("log.").split(".", 1)[0]
    runs = sorted(temp_dir.glob(f"run.{task}*"), key=lambda path: path.stat().st_mtime)
    print(f"\n{'=' * 72}")
    print("Read-only console; it has not retried, cleaned, or changed the build.")
    if runs:
        print(f"Latest reproducible task wrapper: {runs[-1]}")
    print(f"Full task log: {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
