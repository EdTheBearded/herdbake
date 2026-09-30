#!/usr/bin/env bash
# Entrypoint for the "terminal" managed pane: runs whatever command the
# herdbake tmux shim routed here.
set -euo pipefail

if [ -z "${HERDBAKE_CMD:-}" ]; then
  echo "herdbake: HERDBAKE_CMD not set" >&2
  exec "${SHELL:-bash}"
fi

if /bin/sh -c "${HERDBAKE_CMD}"; then
  status=0
else
  status=$?
  echo >&2
  echo "herdbake: terminal command exited with status $status" >&2
fi

if [ "${HERDBAKE_KEEP_OPEN:-0}" = "1" ] || [ "$status" -ne 0 ]; then
  echo >&2
  echo "Press Enter to close this pane." >&2
  read -r _ || true
fi

exit "$status"
