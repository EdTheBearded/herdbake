#!/usr/bin/env bash
# Small overlay form used by the graph and kernel actions.
set -euo pipefail

: "${HERDBAKE_PROMPT_FILE:?HERDBAKE_PROMPT_FILE not set}"
: "${HERDBAKE_PROMPT_MODE:?HERDBAKE_PROMPT_MODE not set}"

case "$HERDBAKE_PROMPT_MODE" in
  graph)
    echo 'Herdbake dependency graph'
    echo
    echo 'This writes BitBake .dot graph files into the build directory.'
    read -r -p 'Recipe or target: ' target || true
    read -r -p 'Type yes to generate or overwrite graph files: ' confirmation || true
    printf '%s\n%s\n' "${target:-}" "${confirmation:-}" > "$HERDBAKE_PROMPT_FILE"
    ;;
  kernel)
    echo 'Herdbake kernel configuration workflow'
    echo
    read -r -p 'Kernel recipe or target [virtual/kernel]: ' recipe || true
    printf '%s\n' "${recipe:-virtual/kernel}" > "$HERDBAKE_PROMPT_FILE"
    ;;
  *)
    echo "herdbake: unknown prompt mode: $HERDBAKE_PROMPT_MODE" >&2
    exit 2
    ;;
esac
