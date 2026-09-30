#!/usr/bin/env python3
"""Guide menuconfig and an explicit follow-up savedefconfig in one pane."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def list_defconfigs(build_dir: Path) -> None:
    files = sorted(
        (path for path in (build_dir / "tmp" / "work").glob("**/defconfig") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        print("No generated defconfig file was found under tmp/work.")
        return
    print("Generated defconfig files:")
    for path in files[:10]:
        print(path)


def run_task(build_dir: Path, recipe: str, task: str) -> int:
    print(f"\nRunning: bitbake {recipe} -c {task}\n")
    return subprocess.run(["bitbake", recipe, "-c", task], cwd=build_dir).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--recipe", required=True)
    args = parser.parse_args()

    status = run_task(args.build_dir, args.recipe, "menuconfig")
    if status:
        return status

    print("\nmenuconfig completed. The configuration pane remains independently available.")
    while True:
        choice = input("[s] save defconfig  [l] list generated defconfigs  [q] quit: ").strip().lower()
        if choice in {"q", ""}:
            return 0
        if choice == "l":
            list_defconfigs(args.build_dir)
            continue
        if choice == "s":
            print("savedefconfig writes generated output in the recipe work area.")
            confirm = input("Type yes to run it: ").strip().lower()
            if confirm != "yes":
                print("savedefconfig was not run.")
                continue
            status = run_task(args.build_dir, args.recipe, "savedefconfig")
            if status == 0:
                list_defconfigs(args.build_dir)
            continue
        print("Choose s, l, or q.")


if __name__ == "__main__":
    raise SystemExit(main())
