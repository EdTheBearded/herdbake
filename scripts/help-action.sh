#!/usr/bin/env bash
# Open the command/shortcut reference without requiring a Yocto build pane.
set -euo pipefail

plugin_root="${HERDR_PLUGIN_ROOT:?HERDR_PLUGIN_ROOT not set}"
herdr_bin="${HERDR_BIN_PATH:-herdr}"

"$herdr_bin" plugin pane open \
  --plugin herdbake \
  --entrypoint help \
  --env "HERDR_PLUGIN_ROOT=$plugin_root" \
  --focus \
  >/dev/null
