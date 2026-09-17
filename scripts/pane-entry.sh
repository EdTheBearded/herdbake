#!/usr/bin/env bash
# Entrypoint for the "terminal" managed pane: runs whatever command the
# herdbake tmux shim routed here.
set -euo pipefail

if [ -z "${HERDBAKE_CMD:-}" ]; then
  echo "herdbake: HERDBAKE_CMD not set" >&2
  exec "${SHELL:-bash}"
fi

exec /bin/sh -c "${HERDBAKE_CMD}"
