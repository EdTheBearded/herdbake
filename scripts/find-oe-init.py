#!/usr/bin/env python3
"""Locate oe-init-build-env using absolute layer paths in bblayers.conf."""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def find_oe_init(build_dir: Path) -> Path | None:
    bblayers = build_dir / "conf" / "bblayers.conf"
    if not bblayers.is_file():
        return None

    # A configured BBLAYERS value contains absolute layer paths. Checking the
    # layer, then its parents, covers both poky/meta and direct layer entries.
    paths = re.findall(r"(/[^\s\"\\]+)", bblayers.read_text(errors="replace"))
    checked: set[Path] = set()
    for raw_path in paths:
        layer = Path(raw_path)
        for candidate_dir in (layer, *layer.parents[:4]):
            candidate = candidate_dir / "oe-init-build-env"
            if candidate in checked:
                continue
            checked.add(candidate)
            if candidate.is_file():
                return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    args = parser.parse_args()
    found = find_oe_init(args.build_dir.resolve())
    if found is None:
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
