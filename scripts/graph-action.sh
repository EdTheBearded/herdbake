#!/usr/bin/env bash
# Explicitly generate BitBake dependency graph files for one target.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/build-context.sh"

build_dir="$(herdbake_build_dir)" || {
  herdbake_notify 'No conf/local.conf was found from the active pane.'
  exit 1
}
choice_file="$(mktemp "${TMPDIR:-/tmp}/herdbake-graph.XXXXXX")"
trap 'rm -f "$choice_file"' EXIT
"$HERDBAKE_HERDR_BIN" plugin pane open \
  --plugin herdbake --entrypoint prompt --focus \
  --env "HERDBAKE_PROMPT_FILE=$choice_file" --env HERDBAKE_PROMPT_MODE=graph \
  >/dev/null
deadline=$((SECONDS + 300))
while [ "$SECONDS" -lt "$deadline" ] && [ ! -s "$choice_file" ]; do sleep 0.1; done
mapfile -t answer < "$choice_file"
target="${answer[0]:-}"
confirmation="${answer[1]:-}"

if [[ ! "$target" =~ ^[A-Za-z0-9_+.:/@%=-]+$ ]]; then
  herdbake_notify 'A valid recipe or target is required for graph generation.'
  exit 1
fi
[ "$confirmation" = yes ] || {
  herdbake_notify 'Dependency graph generation was not confirmed.'
  exit 0
}
command="$(herdbake_shell_join python3 "$HERDBAKE_PLUGIN_ROOT/scripts/dependency-graph.py" --build-dir "$build_dir" --target "$target")"
herdbake_open_terminal "$build_dir" "$command" "Herdbake graph: $target"
