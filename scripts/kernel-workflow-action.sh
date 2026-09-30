#!/usr/bin/env bash
# Prompt for a kernel target and open the menuconfig/defconfig workflow.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/build-context.sh"

build_dir="$(herdbake_build_dir)" || {
  herdbake_notify 'No conf/local.conf was found from the active pane.'
  exit 1
}
choice_file="$(mktemp "${TMPDIR:-/tmp}/herdbake-kernel.XXXXXX")"
trap 'rm -f "$choice_file"' EXIT
"$HERDBAKE_HERDR_BIN" plugin pane open \
  --plugin herdbake --entrypoint prompt --focus \
  --env "HERDBAKE_PROMPT_FILE=$choice_file" --env HERDBAKE_PROMPT_MODE=kernel \
  >/dev/null
deadline=$((SECONDS + 300))
while [ "$SECONDS" -lt "$deadline" ] && [ ! -s "$choice_file" ]; do sleep 0.1; done
recipe="$(sed -n '1p' "$choice_file")"

if [[ ! "$recipe" =~ ^[A-Za-z0-9_+.:/@%=-]+$ ]]; then
  herdbake_notify 'A valid kernel recipe or target is required.'
  exit 1
fi
command="$(herdbake_shell_join python3 "$HERDBAKE_PLUGIN_ROOT/scripts/kernel-workflow.py" --build-dir "$build_dir" --recipe "$recipe")"
herdbake_open_terminal "$build_dir" "$command" "Herdbake kernel: $recipe"
