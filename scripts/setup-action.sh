#!/usr/bin/env bash
# Plugin action: locate the active build's local.conf, then ask before writing.
set -euo pipefail

PLUGIN_ROOT="${HERDR_PLUGIN_ROOT:?HERDR_PLUGIN_ROOT not set}"
HERDR_BIN="${HERDR_BIN_PATH:-herdr}"
FINDER="$PLUGIN_ROOT/scripts/setup-local-conf.sh"

notify() {
  "$HERDR_BIN" notification show 'Herdbake setup' --body "$1" --sound request >/dev/null 2>&1 || true
}

pane_id="${HERDR_PANE_ID:-}"
if [ -z "$pane_id" ]; then
  notify 'No active pane was supplied; focus the build pane and run setup again.'
  exit 1
fi

pane_cwd="$("$HERDR_BIN" pane get "$pane_id" 2>/dev/null | python3 -c '
import json, sys
try:
    pane = json.load(sys.stdin)["result"]["pane"]
except Exception:
    sys.exit(1)
cwd = pane.get("foreground_cwd") or pane.get("cwd")
if not cwd:
    sys.exit(1)
print(cwd)
' )" || {
  notify 'Could not determine the active pane directory.'
  exit 1
}

local_conf="$("$FINDER" --find --cwd "$pane_cwd" 2>/dev/null)" || {
  notify "No conf/local.conf was found from $pane_cwd upward."
  exit 1
}

choice_file="$(mktemp "${TMPDIR:-/tmp}/herdbake-setup-choice.XXXXXX")"
trap 'rm -f "$choice_file"' EXIT

"$HERDR_BIN" plugin pane open \
  --plugin herdbake \
  --entrypoint setup-confirm \
  --env "HERDBAKE_SETUP_FILE=$local_conf" \
  --env "HERDBAKE_SETUP_CHOICE_FILE=$choice_file" \
  --focus \
  >/dev/null

deadline=$((SECONDS + 300))
choice=''
while [ "$SECONDS" -lt "$deadline" ]; do
  choice="$(cat "$choice_file" 2>/dev/null || true)"
  [ -n "$choice" ] && break
  sleep 0.1
done

if [ "$choice" != 'yes' ]; then
  notify 'Setup cancelled; local.conf was not changed.'
  exit 0
fi

if "$FINDER" --apply --file "$local_conf" >/dev/null; then
  "$HERDR_BIN" notification show 'Herdbake setup complete' \
    --body "Configured $local_conf" --sound done >/dev/null 2>&1 || true
else
  notify "Could not update $local_conf; see the plugin action log."
  exit 1
fi
