#!/usr/bin/env python3
"""Run the most useful read-only bitbake-layers reports in one pane."""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path


REPORTS = ("show-layers", "show-overlayed", "show-appends", "show-cross-depends")


def pager_script() -> Path:
    return Path(__file__).with_name("pager.sh")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    args = parser.parse_args()

    failed = False
    report_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="herdbake-layers-", suffix=".log", delete=False
        ) as output:
            report_path = Path(output.name)
            output.write(f"Herdbake layer inspector: {args.build_dir}\n\n")
            for report in REPORTS:
                output.write(f"{'=' * 18} bitbake-layers {report} {'=' * 18}\n")
                result = subprocess.run(
                    ["bitbake-layers", report],
                    cwd=args.build_dir,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                if result.returncode:
                    failed = True
                    output.write(f"\nherdbake: {report} exited with status {result.returncode}\n\n")
                else:
                    output.write("\n")
            output.flush()

        pager = subprocess.run(["bash", str(pager_script()), str(report_path)])
        return int(failed or pager.returncode != 0)
    finally:
        if report_path is not None:
            try:
                report_path.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
