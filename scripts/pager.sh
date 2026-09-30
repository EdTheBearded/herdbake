#!/usr/bin/env bash
# Page a text report while preferring bat's highlighting when it is available.
set -euo pipefail

file="${1:?usage: pager.sh FILE [INITIAL_SEARCH]}"
search="${2:-}"

if [ ! -f "$file" ]; then
  echo "herdbake: cannot page missing file: $file" >&2
  exit 1
fi

bat_command=""
if command -v bat >/dev/null 2>&1; then
  bat_command="bat"
elif command -v batcat >/dev/null 2>&1; then
  # Debian and Ubuntu package the executable under this name.
  bat_command="batcat"
fi

if [ -n "$bat_command" ] && command -v less >/dev/null 2>&1; then
  pager=(less -R)
  if [ -n "$search" ]; then
    pager+=(-p "$search")
  fi
  printf -v pager_command '%q ' "${pager[@]}"
  echo 'Herdbake viewer: bat highlighting; arrows/PgUp/PgDn scroll, q closes.'
  exec "$bat_command" --style=plain --paging=always --pager "$pager_command" "$file"
fi

echo 'Tip: install bat for syntax-highlighted, paged output (it may be named batcat on Debian/Ubuntu).'
if command -v less >/dev/null 2>&1; then
  pager=(less -R)
  if [ -n "$search" ]; then
    pager+=(-p "$search")
  fi
  echo 'Herdbake viewer: less; arrows/PgUp/PgDn scroll, q closes.'
  exec "${pager[@]}" "$file"
fi

if command -v more >/dev/null 2>&1; then
  echo 'Herdbake viewer: more; press Space to advance and q to close.'
  exec more "$file"
fi

echo 'herdbake: no interactive pager is available; printing the complete report.' >&2
cat "$file"
