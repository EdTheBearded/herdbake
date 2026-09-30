#!/usr/bin/env bash
# Source the build's oe-init-build-env before executing a popup command.
set -euo pipefail

build_dir="${1:?build directory is required}"
shift
plugin_root="${HERDR_PLUGIN_ROOT:?HERDR_PLUGIN_ROOT not set}"

if ! command -v bitbake >/dev/null 2>&1; then
  oe_init="$(python3 "$plugin_root/scripts/find-oe-init.py" --build-dir "$build_dir")" || {
    echo "herdbake: bitbake is not on PATH and oe-init-build-env was not found from $build_dir/conf/bblayers.conf" >&2
    exit 127
  }

  # oe-init-build-env establishes PATH, BBPATH, BUILDDIR, and the working
  # directory exactly as a developer's source command would, but only for this
  # popup process tree.
  set +u
  source "$oe_init" "$build_dir" >/dev/null
  set -u
fi

# The action popup can launch BitBake before the user has run the setup action
# for this build. Preserve the custom-terminal settings explicitly so BitBake
# uses route.py instead of falling back to an external terminal.
export OE_TERMINAL=custom
export OE_TERMINAL_CUSTOMCMD="python3 $plugin_root/scripts/route.py --title=\"{title}\" {command}"
export BB_ENV_PASSTHROUGH_ADDITIONS="${BB_ENV_PASSTHROUGH_ADDITIONS:+$BB_ENV_PASSTHROUGH_ADDITIONS }OE_TERMINAL OE_TERMINAL_CUSTOMCMD"

# A running BitBake server keeps the environment from when it was first
# started, so environment passthrough alone cannot change its terminal choice.
# BBPOSTCONF is handled by the client for every invocation and asks that server
# to reparse this short, temporary post-configuration file. This makes action
# launches (notably kernel menuconfig) route correctly without changing the
# build's local.conf.
routing_conf="$(mktemp "${TMPDIR:-/tmp}/herdbake-terminal.XXXXXX.conf")"
trap 'rm -f "$routing_conf"' EXIT
cat > "$routing_conf" <<EOF
OE_TERMINAL = "custom"
OE_TERMINAL_CUSTOMCMD = 'python3 $plugin_root/scripts/route.py --title="{title}" {command}'
EOF
export BBPOSTCONF="${BBPOSTCONF:+$BBPOSTCONF }$routing_conf"

# Do not exec: the EXIT trap must remove the temporary post-configuration file
# after the BitBake client has completed its reparse and task.
"$@"
