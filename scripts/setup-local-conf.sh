#!/usr/bin/env bash
# Finds or updates exactly one Yocto build configuration file for herdbake.
set -euo pipefail

START_MARKER='# >>> herdbake terminal routing >>>'
END_MARKER='# <<< herdbake terminal routing <<<'
PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROUTE_CMD="$PLUGIN_ROOT/scripts/route.py"

usage() {
  cat >&2 <<'EOF'
usage:
  setup-local-conf.sh --find --cwd <directory>
  setup-local-conf.sh --apply --file <path/to/conf/local.conf>
EOF
  exit 2
}

canonical_file() {
  local file="$1" directory base
  directory="$(dirname "$file")"
  base="$(basename "$file")"
  [ "$base" = 'local.conf' ] || {
    echo "herdbake: refusing to change a file not named local.conf: $file" >&2
    return 1
  }
  [ "$(basename "$directory")" = 'conf' ] || {
    echo "herdbake: refusing to change a local.conf outside a conf directory: $file" >&2
    return 1
  }
  [ -d "$directory" ] || {
    echo "herdbake: local.conf directory does not exist: $directory" >&2
    return 1
  }
  directory="$(cd "$directory" && pwd -P)"
  printf '%s/%s\n' "$directory" "$base"
}

find_local_conf() {
  local start="$1" candidate parent

  if [ -n "${BUILDDIR:-}" ] && [ -f "$BUILDDIR/conf/local.conf" ]; then
    canonical_file "$BUILDDIR/conf/local.conf"
    return
  fi

  start="$(cd "$start" && pwd -P)" || return 1
  while :; do
    candidate="$start/conf/local.conf"
    if [ -f "$candidate" ]; then
      canonical_file "$candidate"
      return
    fi
    parent="$(dirname "$start")"
    [ "$parent" = "$start" ] && break
    start="$parent"
  done

  echo "herdbake: no conf/local.conf found from $1 upward" >&2
  return 1
}

apply_setup() {
  local requested="$1" local_conf directory temporary mode start_count end_count
  local_conf="$(canonical_file "$requested")" || return 1
  [ -f "$local_conf" ] || {
    echo "herdbake: local.conf does not exist: $local_conf" >&2
    return 1
  }

  start_count="$(grep -Fxc "$START_MARKER" "$local_conf" || true)"
  end_count="$(grep -Fxc "$END_MARKER" "$local_conf" || true)"
  if ! { [ "$start_count" = 0 ] && [ "$end_count" = 0 ]; } && \
     ! { [ "$start_count" = 1 ] && [ "$end_count" = 1 ]; }; then
    echo "herdbake: refusing to update malformed herdbake block in $local_conf" >&2
    return 1
  fi

  directory="$(dirname "$local_conf")"
  temporary="$(mktemp "$directory/.local.conf.herdbake.XXXXXX")"
  trap 'rm -f "$temporary"' RETURN
  mode="$(stat -c '%a' "$local_conf" 2>/dev/null || stat -f '%Lp' "$local_conf")"

  if [ "$start_count" = 1 ]; then
    awk -v start="$START_MARKER" -v end="$END_MARKER" '
      $0 == start { in_block = 1; next }
      $0 == end { in_block = 0; next }
      !in_block { print }
    ' "$local_conf" > "$temporary"
  else
    cat "$local_conf" > "$temporary"
  fi

  cat >> "$temporary" <<EOF

$START_MARKER
# Managed by herdbake. Re-run the setup action to update this block.
OE_TERMINAL = "custom"
# Keep the outer BitBake value single-quoted. BitBake formats this value before
# splitting it, so the inner double quotes group titles containing spaces.
OE_TERMINAL_CUSTOMCMD = 'python3 $ROUTE_CMD --title="{title}" {command}'
$END_MARKER
EOF

  chmod "$mode" "$temporary"
  mv "$temporary" "$local_conf"
  trap - RETURN
  echo "herdbake: configured $local_conf"
}

mode="${1:-}"
shift || true
case "$mode" in
  --find)
    [ "${1:-}" = '--cwd' ] && [ -n "${2:-}" ] || usage
    [ "$#" = 2 ] || usage
    find_local_conf "$2"
    ;;
  --apply)
    [ "${1:-}" = '--file' ] && [ -n "${2:-}" ] || usage
    [ "$#" = 2 ] || usage
    apply_setup "$2"
    ;;
  *) usage ;;
esac
