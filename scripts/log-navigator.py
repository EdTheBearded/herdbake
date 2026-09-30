#!/usr/bin/env python3
"""Find the newest cooker or task log for interactive navigation."""
from __future__ import annotations

import argparse
from pathlib import Path


def latest_log(build_dir: Path) -> Path | None:
    cooker_logs = list((build_dir / "tmp" / "log" / "cooker").glob("*"))
    task_logs = list((build_dir / "tmp" / "work").glob("**/temp/log.do_*"))
    candidates = [path for path in [*cooker_logs, *task_logs] if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    args = parser.parse_args()
    log = latest_log(args.build_dir)
    if log is None:
        return 1
    print(log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
