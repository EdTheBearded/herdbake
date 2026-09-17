#!/usr/bin/env bash
# pane.created event hook: injects OE_TERMINAL routing into a
# newly-created pane, if herdbake is enabled and the pane is a bare
# shell with nothing running in it yet.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./lib.sh

herdbake_is_enabled || exit 0

PANE_ID="$(python3 -c '
import json, os
try:
    data = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))
except Exception:
    data = {}
pane = data.get("pane") or {}
print(pane.get("pane_id", ""))
')"

[ -n "$PANE_ID" ] || exit 0

# A brand-new pane's shell may not have taken over the foreground yet;
# give it a moment before checking/injecting.
sleep 0.3

if herdbake_pane_is_safe_shell "$PANE_ID"; then
  herdbake_inject_pane "$PANE_ID"
fi
