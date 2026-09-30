#!/usr/bin/env bash
# Integration tests for the idempotent local.conf setup writer.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP="$ROOT/scripts/setup-local-conf.sh"
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_contains() {
  local file="$1" expected="$2"
  grep -Fqx "$expected" "$file" || fail "expected '$expected' in $file"
}

assert_count() {
  local file="$1" expected="$2" want="$3"
  local got
  got="$(grep -Fxc "$expected" "$file" || true)"
  [ "$got" = "$want" ] || fail "expected $want copies of '$expected' in $file, got $got"
}

build_dir="$TMPDIR_TEST/build"
mkdir -p "$build_dir/conf"
local_conf="$build_dir/conf/local.conf"
printf '# existing user setting\nMACHINE = "qemuarm"\n' > "$local_conf"

"$SETUP" --apply --file "$local_conf"

assert_contains "$local_conf" '# existing user setting'
assert_contains "$local_conf" 'MACHINE = "qemuarm"'
assert_contains "$local_conf" 'OE_TERMINAL = "custom"'
assert_contains "$local_conf" '# >>> herdbake terminal routing >>>'
assert_contains "$local_conf" '# <<< herdbake terminal routing <<<'
assert_contains "$local_conf" "OE_TERMINAL_CUSTOMCMD = 'python3 $ROOT/scripts/route.py --title=\"{title}\" {command}'"

"$SETUP" --apply --file "$local_conf"
assert_count "$local_conf" '# >>> herdbake terminal routing >>>' 1
assert_count "$local_conf" 'OE_TERMINAL = "custom"' 1

if "$SETUP" --apply --file "$TMPDIR_TEST/missing/conf/local.conf"; then
  fail 'writer accepted a missing local.conf'
fi

echo 'ok: setup-local-conf'
