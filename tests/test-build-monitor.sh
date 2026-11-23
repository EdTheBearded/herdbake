#!/usr/bin/env bash
# Offline tests for the build monitor's log parsing and helpers. No Herdr or
# BitBake needed: fixtures mimic real cooker logs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT
export HERDR_PLUGIN_STATE_DIR="$TMPDIR_TEST/state"

log_dir="$TMPDIR_TEST/build/tmp/log/cooker/qemuarm"
mkdir -p "$log_dir"

cat > "$log_dir/ok.log" <<'EOF'
Sstate summary: Wanted 2 Local 2 Mirrors 0 Missed 0 Current 3 (100% match, 100% complete)
NOTE: Running setscene task 4 of 5 (virtual:native:/l/meta/recipes-core/zlib/zlib_1.3.1.bb:do_populate_sysroot_setscene)
NOTE: Running task 17 of 18 (virtual:native:/l/meta/recipes-core/zlib/zlib_1.3.1.bb:do_configure)
NOTE: Running task 18 of 18 (virtual:native:/l/meta/recipes-core/zlib/zlib_1.3.1.bb:do_compile)
WARNING: zlib-native do_compile is tainted from a forced run
NOTE: Tasks Summary: Attempted 18 tasks of which 12 didn't need to be rerun and all succeeded.
EOF

cat > "$log_dir/fail.log" <<'EOF'
NOTE: Running task 3 of 9 (/l/meta-oe/recipes-extended/pam/pam-ssh-agent-auth_0.10.3.bb:do_compile)
ERROR: pam-ssh-agent-auth-0.10.3-r0 do_compile: oe_runmake failed
NOTE: Tasks Summary: Attempted 3 tasks of which 2 didn't need to be rerun and 1 failed.
EOF

# Ctrl-C: BitBake drains running tasks and still says "all succeeded".
cat > "$log_dir/ctrlc.log" <<'EOF'
NOTE: Running task 143 of 148 (virtual:native:/l/meta/recipes-devtools/perl/perl_5.38.4.bb:do_compile)
NOTE: Tasks Summary: Attempted 143 tasks of which 140 didn't need to be rerun and all succeeded.
EOF

cat > "$log_dir/stopped.log" <<'EOF'
NOTE: Running task 10 of 50 (/l/meta/recipes-core/busybox/busybox_1.36.1.bb:do_fetch)
ERROR: Command execution failed: Stopped build
EOF

result="$(cd "$ROOT/scripts" && python3 - "$log_dir" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bm", "build-monitor.py")
bm = importlib.util.module_from_spec(spec); spec.loader.exec_module(bm)
d = sys.argv[1]
for name in ("ok", "fail", "ctrlc", "stopped"):
    ls = bm.LogState(f"{d}/{name}.log"); ls.update()
    print(name, ls.phase, ls.n, ls.m, ls.percent(), ls.current, ls.errors,
          ls.warnings, ls.failed, ls.interrupted)
print("target", bm.short_target(["python3", "/x/bin/bitbake", "-f", "-c", "compile", "zlib-native"]))
print("target", bm.short_target(["/x/bin/bitbake", "-k", "torizon-docker"]))
print("client", bm.is_bitbake_client(["python3", "/x/bin/bitbake", "core-image"]),
      bm.is_bitbake_client(["python3", "/x/bin/bitbake-server", "decafbad"]))
print("waiting", bm.interactive_name("Linux Kernel Configuration"),
      bm.interactive_name("OpenEmbedded Developer Shell"))
print("builddir", bm.terminal_build_dir("bash /p/scripts/with-build-env.sh '/b/my build' /bin/sh -c 'x'"))
print("describe", bm.describe({"waiting": "menuconfig"}))
PY
)"
printf '%s\n' "$result"

grep -qx 'ok tasks 18 18 100 zlib-native:do_compile 0 1 False False' <<<"$result"
grep -qx 'fail tasks 3 9 33 pam-ssh-agent-auth:do_compile 1 0 True False' <<<"$result"
grep -qx 'ctrlc tasks 143 148 96 perl-native:do_compile 0 0 False True' <<<"$result"
grep -qx 'stopped tasks 10 50 20 busybox:do_fetch 0 0 False True' <<<"$result"
grep -qx 'target zlib-native -c compile' <<<"$result"
grep -qx 'target torizon-docker' <<<"$result"
grep -qx 'client True False' <<<"$result"
grep -qx 'waiting menuconfig devshell' <<<"$result"
grep -qx 'builddir /b/my build' <<<"$result"
grep -q  'describe ⏸ waiting for you in menuconfig' <<<"$result"

echo "build monitor tests passed"
