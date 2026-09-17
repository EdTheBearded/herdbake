#!/usr/bin/env bash
# "Herdbake: show status" action.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./lib.sh

if herdbake_is_enabled; then
  echo "herdbake: enabled (auto-injecting into new eligible panes)"
else
  echo "herdbake: disabled (run 'Herdbake: toggle auto-injection' to re-enable)"
fi

echo
echo "config: ${HERDBAKE_CONFIG_OVERRIDE:-${HERDR_PLUGIN_CONFIG_DIR:-<unset>}/bitbake-terminal.toml}"
echo "(falls back to bundled config/bitbake-terminal.default.toml if absent)"
