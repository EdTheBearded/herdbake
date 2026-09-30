#!/usr/bin/env bash
# Shared, read-only build-context helpers for Herdbake actions.
set -euo pipefail

HERDBAKE_PLUGIN_ROOT="${HERDR_PLUGIN_ROOT:?HERDR_PLUGIN_ROOT not set}"
HERDBAKE_HERDR_BIN="${HERDR_BIN_PATH:-herdr}"

herdbake_notify() {
  "$HERDBAKE_HERDR_BIN" notification show 'Herdbake' --body "$1" --sound request \
    >/dev/null 2>&1 || true
}

herdbake_active_pane_cwd() {
  local pane_id="${HERDR_PANE_ID:-}"
  [ -n "$pane_id" ] || return 1

  "$HERDBAKE_HERDR_BIN" pane get "$pane_id" 2>/dev/null | python3 -c '
import json
import sys

try:
    pane = json.load(sys.stdin)["result"]["pane"]
    cwd = pane.get("foreground_cwd") or pane.get("cwd")
    if not cwd:
        raise ValueError("pane has no working directory")
except Exception:
    sys.exit(1)

print(cwd)
'
}

herdbake_build_dir() {
  local pane_cwd local_conf
  pane_cwd="$(herdbake_active_pane_cwd)" || return 1
  local_conf="$("$HERDBAKE_PLUGIN_ROOT/scripts/setup-local-conf.sh" --find --cwd "$pane_cwd" 2>/dev/null)" || return 1
  dirname "$(dirname "$local_conf")"
}

herdbake_shell_join() {
  python3 -c 'import shlex, sys; print(shlex.join(sys.argv[1:]))' "$@"
}

herdbake_open_terminal() {
  local build_dir="$1" command="$2" title="$3" keep_open="${4:-1}" wrapped
  wrapped="$(herdbake_shell_join bash "$HERDBAKE_PLUGIN_ROOT/scripts/with-build-env.sh" "$build_dir" /bin/sh -c "$command")"
  "$HERDBAKE_HERDR_BIN" plugin pane open \
    --plugin herdbake \
    --entrypoint terminal \
    --cwd "$build_dir" \
    --env "HERDR_PLUGIN_ROOT=$HERDBAKE_PLUGIN_ROOT" \
    --env "HERDBAKE_CMD=$wrapped" \
    --env "HERDBAKE_KEEP_OPEN=$keep_open" \
    --env "HERDBAKE_TITLE=$title" \
    --focus \
    >/dev/null
}
