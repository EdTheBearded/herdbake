#!/usr/bin/env bash
# Open the most recently written task log for the active build.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/build-context.sh"

build_dir="$(herdbake_build_dir)" || {
  herdbake_notify 'No conf/local.conf was found from the active pane.'
  exit 1
}
log="$(python3 "$HERDBAKE_PLUGIN_ROOT/scripts/failure-console.py" --build-dir "$build_dir" --path-only)" || {
  herdbake_notify 'No task logs were found for the active build.'
  exit 1
}
command="$(herdbake_shell_join bash "$HERDBAKE_PLUGIN_ROOT/scripts/pager.sh" "$log" ERROR)"
herdbake_open_terminal "$build_dir" "$command" 'Herdbake latest task log'
