#!/usr/bin/env bash
# Open a read-only local build health report.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/build-context.sh"

build_dir="$(herdbake_build_dir)" || {
  herdbake_notify 'No conf/local.conf was found from the active pane.'
  exit 1
}
command="$(herdbake_shell_join python3 "$HERDBAKE_PLUGIN_ROOT/scripts/build-health.py" --build-dir "$build_dir")"
herdbake_open_terminal "$build_dir" "$command" 'Herdbake build health'
