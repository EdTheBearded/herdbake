#!/usr/bin/env bash
# Open an interactive log viewer focused on the newest failure-bearing log.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/build-context.sh"

build_dir="$(herdbake_build_dir)" || {
  herdbake_notify 'No conf/local.conf was found from the active pane.'
  exit 1
}
command="$(herdbake_shell_join bash "$HERDBAKE_PLUGIN_ROOT/scripts/log-navigator.sh" "$build_dir")"
herdbake_open_terminal "$build_dir" "$command" 'Herdbake log navigator'
