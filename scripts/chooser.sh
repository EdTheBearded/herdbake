#!/usr/bin/env bash
# Entrypoint for the "chooser" popup: asks the user for a split
# direction when a rule has prompt_direction = true, and writes the
# answer to HERDBAKE_CHOICE_FILE for the shim to read.
set -euo pipefail

: "${HERDBAKE_CHOICE_FILE:?HERDBAKE_CHOICE_FILE not set}"
: "${HERDBAKE_TITLE:=terminal}"

echo "herdbake: choose split direction for '${HERDBAKE_TITLE}'"
echo
echo "  [h] horizontal split (side by side)"
echo "  [v] vertical split (stacked)"
echo

read -n1 -rp "> " choice || true
echo

case "${choice:-}" in
  h|H) echo "right" > "${HERDBAKE_CHOICE_FILE}" ;;
  v|V) echo "down"  > "${HERDBAKE_CHOICE_FILE}" ;;
  *)   echo "down"  > "${HERDBAKE_CHOICE_FILE}" ;;
esac
