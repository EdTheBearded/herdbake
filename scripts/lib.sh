#!/usr/bin/env bash
# Shared helpers for herdbake's injection scripts.
set -euo pipefail

HERDBAKE_ENABLED_FLAG="${HERDR_PLUGIN_STATE_DIR:?HERDR_PLUGIN_STATE_DIR not set}/enabled"
HERDR_BIN="${HERDR_BIN_PATH:-herdr}"

herdbake_is_enabled() {
  # Enabled by default: absent flag file means "on". A flag file
  # containing "0" means the toggle action turned it off.
  [ ! -f "$HERDBAKE_ENABLED_FLAG" ] || [ "$(cat "$HERDBAKE_ENABLED_FLAG")" != "0" ]
}

# True (exit 0) only when the pane's foreground is a bare, idle shell -
# nothing typed, no app/agent/subprocess running in it. Injecting into
# anything else could submit our export line as input to that program.
herdbake_pane_is_safe_shell() {
  local pane_id="$1"
  "$HERDR_BIN" pane process-info --pane "$pane_id" 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
info = data.get("result", {}).get("process_info")
if not info:
    sys.exit(1)
procs = info.get("foreground_processes", [])
shell_pid = info.get("shell_pid")
fg_pgid = info.get("foreground_process_group_id")
# Safe only if there is exactly one foreground process and it IS the shell.
if len(procs) != 1:
    sys.exit(1)
proc = procs[0]
if proc.get("pid") != shell_pid:
    sys.exit(1)
name = (proc.get("name") or "").lower()
# Reject known agent/app process names defensively even if pid matched.
if any(x in name for x in ("claude", "codex", "hermes", "node", "python", "vim", "nvim", "nano", "less", "man", "htop", "top")):
    sys.exit(1)
sys.exit(0)
'
}

herdbake_inject_pane() {
  local pane_id="$1"
  local plugin_root="${HERDR_PLUGIN_ROOT:?HERDR_PLUGIN_ROOT not set}"
  local route_cmd="${plugin_root}/scripts/route.py"

  # bitbake invokes OE_TERMINAL_CUSTOMCMD from bitbake-server's own
  # process tree, entirely separate from the herdbake plugin hook's
  # process - it never inherits HERDR_PLUGIN_ROOT/CONFIG_DIR/BIN_PATH
  # the way this hook script did. Those must be exported directly
  # into the target pane's shell so route.py can find the plugin's
  # config override and call back into Herdr correctly, instead of
  # silently falling back to its bundled defaults.
  #
  # bitbake only pulls a fixed allowlist of variables from the shell
  # environment into its datastore (see bb.utils.preserved_envvars());
  # OE_TERMINAL and OE_TERMINAL_CUSTOMCMD are not in it. The allowlist
  # itself is extensible via BB_ENV_PASSTHROUGH_ADDITIONS, which IS
  # always inherited - so both custom vars must be added there too.
  # bitbake re-reads all of this on every invocation
  # (bb.cooker.Cooker.updateConfigOpts), including against an
  # already-running bitbake-server, so no server restart is needed.
  "$HERDR_BIN" pane run "$pane_id" \
    "export HERDR_PLUGIN_ROOT='${plugin_root}'; export HERDR_PLUGIN_CONFIG_DIR='${HERDR_PLUGIN_CONFIG_DIR:-}'; export HERDR_BIN_PATH='${HERDR_BIN}'; export OE_TERMINAL=custom; export OE_TERMINAL_CUSTOMCMD='python3 ${route_cmd} \"{title}\" {command}'; export BB_ENV_PASSTHROUGH_ADDITIONS=\"OE_TERMINAL OE_TERMINAL_CUSTOMCMD \${BB_ENV_PASSTHROUGH_ADDITIONS:-}\"; export HERDBAKE_ACTIVE=1" \
    >/dev/null 2>&1 || true

  "$HERDR_BIN" pane report-metadata "$pane_id" \
    --source "plugin:herdbake" \
    --display-agent "🔥 herdbake" \
    --ttl-ms 86400000 \
    >/dev/null 2>&1 || true
}
