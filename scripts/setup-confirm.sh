#!/usr/bin/env bash
# Focused overlay for confirming the exact local.conf herdbake will update.
set -euo pipefail

: "${HERDBAKE_SETUP_FILE:?HERDBAKE_SETUP_FILE not set}"
: "${HERDBAKE_SETUP_CHOICE_FILE:?HERDBAKE_SETUP_CHOICE_FILE not set}"

echo 'Configure BitBake terminal routing?'
echo
echo "  $HERDBAKE_SETUP_FILE"
echo
echo 'This adds or updates only the marked herdbake block in that file.'
read -r -n1 -p 'Proceed? [y/N] ' choice || true
echo

case "${choice:-}" in
  y|Y) printf 'yes\n' > "$HERDBAKE_SETUP_CHOICE_FILE" ;;
  *)   printf 'no\n' > "$HERDBAKE_SETUP_CHOICE_FILE" ;;
esac
