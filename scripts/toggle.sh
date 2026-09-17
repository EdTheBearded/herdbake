#!/usr/bin/env bash
# "Herdbake: toggle auto-injection" action.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./lib.sh

FLAG="$HERDBAKE_ENABLED_FLAG"

if herdbake_is_enabled; then
  echo "0" > "$FLAG"
  "${HERDR_BIN_PATH:-herdr}" notification show "Herdbake disabled" \
    --body "New panes will not get OE_TERMINAL routing until re-enabled." || true
  echo "herdbake: disabled. Existing injected panes keep working until closed."
else
  rm -f "$FLAG"
  "${HERDR_BIN_PATH:-herdr}" notification show "Herdbake enabled" \
    --body "Auto-injecting OE_TERMINAL routing into eligible panes." || true
  echo "herdbake: enabled. Injecting into existing eligible panes now."
  ./inject-all.sh
fi
