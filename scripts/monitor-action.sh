#!/usr/bin/env bash
# Open the build monitor popup (running/recent BitBake builds across panes).
set -euo pipefail

plugin_root="${HERDR_PLUGIN_ROOT:?HERDR_PLUGIN_ROOT not set}"
herdr_bin="${HERDR_BIN_PATH:-herdr}"

# The popup reads the watcher's state; make sure the watcher is up.
python3 "$plugin_root/scripts/build-monitor.py" ensure-daemon >/dev/null

"$herdr_bin" plugin pane open \
  --plugin herdbake \
  --entrypoint monitor \
  --env "HERDR_PLUGIN_ROOT=$plugin_root" \
  --focus \
  >/dev/null
