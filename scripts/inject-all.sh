#!/usr/bin/env bash
# Startup hook: injects OE_TERMINAL routing into every existing
# eligible pane when Herdr (re)starts with herdbake enabled.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./lib.sh

herdbake_is_enabled || exit 0

HERDR_BIN="${HERDR_BIN_PATH:-herdr}"

"$HERDR_BIN" pane list 2>/dev/null | python3 -c '
import json, sys
data = json.load(sys.stdin)
for p in data.get("result", {}).get("panes", []):
    print(p["pane_id"])
' | while read -r pane_id; do
  if herdbake_pane_is_safe_shell "$pane_id"; then
    herdbake_inject_pane "$pane_id"
  fi
done
