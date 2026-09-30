#!/usr/bin/env python3
"""Generate and optionally render BitBake dependency graphs for one target."""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


GRAPH_FILES = ("pn-depends.dot", "task-depends.dot")
RENDER_TIMEOUT_SECONDS = 10


def render_graph(dot: str, source: Path, destination: Path) -> bool:
    try:
        render = subprocess.run(
            [dot, "-Tsvg", str(source), "-o", str(destination)],
            timeout=RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            f"Skipped {destination.name}: Graphviz exceeded "
            f"{RENDER_TIMEOUT_SECONDS} seconds. The .dot file is available."
        )
        return False
    if render.returncode == 0:
        print(destination)
        return True
    print(f"Graphviz exited with status {render.returncode}; {source.name} remains available.")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    build_dir = args.build_dir.resolve()

    print(f"Herdbake dependency graph: {args.target}")
    print(f"Writing BitBake graph artifacts in {build_dir}\n")
    result = subprocess.run(["bitbake", "-g", args.target], cwd=build_dir)
    if result.returncode:
        return result.returncode

    print("\nGenerated:")
    for filename in ("pn-buildlist", *GRAPH_FILES):
        path = build_dir / filename
        if path.exists():
            print(path)

    dot = shutil.which("dot")
    if dot:
        for filename in GRAPH_FILES:
            source = build_dir / filename
            destination = source.with_suffix(".svg")
            if source.exists():
                render_graph(dot, source, destination)
    else:
        print("Graphviz 'dot' is not installed; .dot files remain available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
