#!/usr/bin/env bash
# Open the newest relevant build log at its first error.
set -euo pipefail

build_dir="${1:?build directory is required}"
plugin_root="${HERDR_PLUGIN_ROOT:?HERDR_PLUGIN_ROOT not set}"
log="$(python3 "$plugin_root/scripts/log-navigator.py" --build-dir "$build_dir")" || {
  echo "herdbake: no cooker or task log found under $build_dir/tmp" >&2
  exit 1
}

exec bash "$plugin_root/scripts/pager.sh" "$log" ERROR
