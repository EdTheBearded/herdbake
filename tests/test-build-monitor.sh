#!/usr/bin/env bash
# Offline tests for the build monitor. No Herdr or BitBake needed: fixtures
# mimic real cooker logs and Herdr calls are recorded instead of sent.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT
export HERDR_PLUGIN_STATE_DIR="$TMPDIR_TEST/state"
export HERDR_PLUGIN_CONFIG_DIR="$TMPDIR_TEST/config"
unset HERDBAKE_MONITOR

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

# bitbake -g / -p / -s: a log without task lines, summary or errors.
cat > "$log_dir/graph.log" <<'EOF'
NOTE: Started PRServer with DBfile: /b/cache/prserv.sqlite3
NOTE: PN build list saved to 'pn-buildlist'
NOTE: Task dependencies saved to 'task-depends.dot'
EOF

# A parse error ends the run without any summary.
cat > "$log_dir/parse-error.log" <<'EOF'
ERROR: ParseError at /l/meta/recipes-core/busybox/busybox_1.36.1.bb:12: unparsed line
EOF

result="$(cd "$ROOT/scripts" && python3 - "$TMPDIR_TEST" <<'PY'
import importlib.util, os, pathlib, sys, time
spec = importlib.util.spec_from_file_location("bm", "build-monitor.py")
bm = importlib.util.module_from_spec(spec); spec.loader.exec_module(bm)
tmp = pathlib.Path(sys.argv[1])
d = tmp / "build/tmp/log/cooker/qemuarm"

# ---- log parsing -----------------------------------------------------------
for name in ("ok", "fail", "ctrlc", "stopped"):
    ls = bm.LogState(str(d / f"{name}.log")); ls.update()
    print(name, ls.phase, ls.n, ls.m, ls.percent(), ls.current, ls.errors,
          ls.warnings, ls.failed, ls.interrupted)

# ---- build result (#1) -----------------------------------------------------
def result(name):
    if name is None:
        return bm.build_result(None)
    ls = bm.LogState(str(d / f"{name}.log")); ls.update()
    return bm.build_result(ls)
for name in ("ok", "fail", "ctrlc", "stopped", "graph", "parse-error", None):
    print("result", name, *result(name))

# ---- finish(): what reaches Herdr -----------------------------------------
calls = []
bm.try_call = lambda method, params=None: calls.append((method, params)) or {}
def finish(log, pane):
    calls.clear()
    mon = bm.Monitor()
    ls = None
    if log:
        ls = bm.LogState(str(d / f"{log}.log")); ls.update()
    b = {"pid": 1, "started": time.time() - 5, "target": "t", "builddir": str(tmp / "build"),
         "pane": pane, "agent": None, "mode": "own" if pane else "token", "log": ls,
         "display": "bitbake"}
    if pane:
        mon.owned[pane["pane_id"]] = 1
    mon.finish(b)
    methods = [m for m, _ in calls]
    agent = [p["state"] for m, p in calls if m == "pane.report_agent"]
    return (b["status"], "notify" if "notification.show" in methods else "quiet",
            "released" if "pane.release_agent" in methods else "kept",
            agent[0] if agent else "-")
pane = {"pane_id": "w1:p1", "label": None}
print("finish ok-pane", *finish("ok", pane))
print("finish graph-pane", *finish("graph", pane))
print("finish nolog-pane", *finish(None, pane))
print("finish ok-nopane", *finish("ok", None))       # #11: outside Herdr is silent

# ---- find_cooker_log (#2) --------------------------------------------------
fd = tmp / "fresh/tmp/log/cooker/m"
fd.mkdir(parents=True)
now = time.time()
def utc_name(t):
    return time.strftime("%Y%m%d%H%M%S", time.gmtime(t)) + ".log"
prev = fd / utc_name(now - 600)          # previous build: started 10 min ago,
prev.write_text("NOTE: Tasks Summary: Attempted 1 tasks of which 0 didn't need to be rerun and all succeeded.\n")
os.utime(prev, (now - 0.3, now - 0.3))   # ...finished just before the next client.
started = now
print("log prev-only", bm.find_cooker_log(str(tmp / "fresh"), started))
mine = fd / utc_name(now + 1)            # this build's log appears a moment later
mine.write_text("NOTE: Running task 1 of 2 (/l/a_1.0.bb:do_fetch)\n")
os.utime(mine, (now + 1, now + 1))
print("log mine", bm.find_cooker_log(str(tmp / "fresh"), started) == str(mine))
old = fd / utc_name(now - 900)
old.write_text("x\n")
os.utime(old, (now - 5, now - 5))        # finished 5 s before: always excluded
print("log old-excluded", bm.find_cooker_log(str(tmp / "fresh"), started) == str(mine))
print("log claimed", bm.find_cooker_log(str(tmp / "fresh"), started, {str(mine)}))

# `bash -c '... && bitbake a && bitbake b'`: the shell execs into b, so b keeps
# the shell's pid and start time. The previous poll saw that pid as bash.
mon = bm.Monitor()
mon.prev_argv = {500: ["bash", "-c", "x"], 501: ["python3", "/x/bin/bitbake", "a"]}
print("start exec", round(now - mon.start_time(500, now - 300, now)) <= bm.POLL_S,
      mon.start_time(501, now - 300, now) == now - 300,
      mon.start_time(502, now - 300, now) == now - 300)

# ---- step(): `bitbake a && bitbake b` end to end (#2) ----------------------
# Fake process table and clock; Herdr calls are recorded by the stubbed try_call.
sd = tmp / "seq/tmp/log/cooker/m"
sd.mkdir(parents=True)
clock = [now]
procs = {}                                   # pid -> (argv, start)
class FakeSnap:
    def __init__(self):
        self.argv = {p: a for p, (a, _) in procs.items()}
        self.ppid = {p: 1 for p in procs}
    def clients(self):
        return sorted(p for p, a in self.argv.items() if bm.is_bitbake_client(a))
    def herdbake_terminals(self):
        return []
real = (bm.ProcSnapshot, bm.proc_stat, bm.proc_env, bm.time.time)
bm.ProcSnapshot = FakeSnap
bm.proc_stat = lambda p: ("python3", 1, procs[p][1]) if p in procs else None
bm.proc_env = lambda p: {"BUILDDIR": str(tmp / "seq")}
bm.time.time = lambda: clock[0]
seqmon = bm.Monitor()
seqmon.pmap.refresh = lambda: None
def write_log(t, text, mtime):
    f = sd / utc_name(t)
    f.write_text(text)
    os.utime(f, (mtime, mtime))
    return f
BB = ["python3", "/x/bin/bitbake", "-c", "compile", "zlib-native"]
procs[700] = (BB, now)                                     # a starts
seqmon.step()
la = write_log(now, "NOTE: Running task 1 of 18 (/l/z_1.bb:do_fetch)\n", now + 1)
clock[0] = now + 2; seqmon.step()
la.write_text(la.read_text() + "NOTE: Tasks Summary: Attempted 18 tasks of which 0 "
              "didn't need to be rerun and all succeeded.\n"); os.utime(la, (now + 20, now + 20))
del procs[700]; procs[701] = (BB, now + 20.5)              # a exits, b starts
clock[0] = now + 21; seqmon.step()
lb = write_log(now + 21, "NOTE: Running task 1 of 18 (/l/z_1.bb:do_fetch)\n", now + 22)
clock[0] = now + 23; seqmon.step()
lb.write_text(lb.read_text() + "ERROR: z do_compile failed\nNOTE: Tasks Summary: Attempted 5 "
              "tasks of which 0 didn't need to be rerun and 1 failed.\n"); os.utime(lb, (now + 40, now + 40))
del procs[701]
clock[0] = now + 41; seqmon.step()
for f in seqmon.finished:
    print("seq", f["pid"], os.path.basename(f["log"].path) == (la if f["pid"] == 700 else lb).name,
          f["status"])
bm.ProcSnapshot, bm.proc_stat, bm.proc_env, bm.time.time = real

# ---- waiting detection (#3) ------------------------------------------------
bd = str(tmp / "build")
routed = {"pid": 10, "busy": True, "routed": True, "builddir": os.path.realpath(bd),
          "title": "Linux/arm64 Kernel Configuration", "descendants": {11}}
viewer = {"pid": 20, "busy": True, "routed": False, "builddir": os.path.realpath(bd),
          "title": "Herdbake log navigator", "descendants": {21}}
idle = dict(routed, pid=30, busy=False)
w = bm.waiting_terminal([viewer], bd, 99)
print("waiting viewer", w and w["pid"])
w = bm.waiting_terminal([viewer, routed], bd, 99)
print("waiting routed", w and w["pid"], bm.interactive_name(w["title"]))
print("waiting idle", bm.waiting_terminal([idle], bd, 99))
print("waiting other-dir", bm.waiting_terminal([routed], str(tmp / "other"), 99))
print("waiting own-child", bm.waiting_terminal([routed], bd, 11))

# ---- claim mode (#7) -------------------------------------------------------
mon = bm.Monitor()
print("claim plain", mon.claim_mode({"pane_id": "a"}))
print("claim agent", mon.claim_mode({"pane_id": "b", "agent": "claude"}))
print("claim stale-bitbake", mon.claim_mode({"pane_id": "c", "agent": "bitbake"}))
mon.owned["d"] = 1
print("claim owned", mon.claim_mode({"pane_id": "d", "agent": "bitbake"}))

# ---- process matching (#8) / targets (#12) --------------------------------
cl = bm.is_bitbake_client
print("client", cl(["python3", "/x/bin/bitbake", "core-image"]), cl(["/x/bin/bitbake", "a"]),
      cl(["python3", "/x/bin/bitbake-server", "decafbad"]), cl(["vim", "bitbake"]),
      cl(["less", "bitbake"]), cl([]))
st = bm.short_target
print("target", st(["python3", "/x/bin/bitbake", "-f", "-c", "compile", "zlib-native"]))
print("target", st(["/x/bin/bitbake", "-k", "torizon-docker"]))
print("target", st(["python3", "/x/bin/bitbake", "-R", "conf/extra.conf", "core-image-minimal"]))
print("target", st(["/x/bin/bitbake", "-ccompile", "--mc", "x", "busybox"]))
print("target", st(["/x/bin/bitbake", "--cmd=fetch", "-r", "pre.conf", "-I", "x", "busybox"]))
print("target", st(["/x/bin/bitbake", "-e"]))

# ---- own processes only (#5) ----------------------------------------------
snap = bm.ProcSnapshot()
uids = {os.stat(f"/proc/{p}").st_uid for p in list(snap.ppid)[:200] if os.path.exists(f"/proc/{p}")}
print("snapshot own-only", uids <= {os.getuid()}, os.getpid() in snap.ppid)

# ---- misc ------------------------------------------------------------------
print("waiting-name", bm.interactive_name("Linux Kernel Configuration"),
      bm.interactive_name("OpenEmbedded Developer Shell"))
print("builddir", bm.terminal_build_dir("bash /p/scripts/with-build-env.sh '/b/my build' /bin/sh -c 'x'"))
print("describe", bm.describe({"waiting": "menuconfig"}))
PY
)"
printf '%s\n' "$result"

expect() { grep -qxF -- "$1" <<<"$result" || { echo "MISSING: $1" >&2; exit 1; }; }
expect 'ok tasks 18 18 100 zlib-native:do_compile 0 1 False False'
expect 'fail tasks 3 9 33 pam-ssh-agent-auth:do_compile 1 0 True False'
expect 'ctrlc tasks 143 148 96 perl-native:do_compile 0 0 False True'
expect 'stopped tasks 10 50 20 busybox:do_fetch 0 0 False True'
expect 'result ok ok succeeded'
expect 'result fail failed FAILED'
expect 'result ctrlc interrupted interrupted'
expect 'result stopped interrupted interrupted'
expect 'result graph neutral finished'
expect 'result parse-error failed FAILED'
expect 'result None neutral finished'
expect 'finish ok-pane ok notify kept idle'
expect 'finish graph-pane neutral quiet released -'
expect 'finish nolog-pane neutral quiet released -'
expect 'finish ok-nopane ok quiet kept -'
expect 'log prev-only None'
expect 'log mine True'
expect 'log old-excluded True'
expect 'log claimed None'
expect 'start exec True True True'
expect 'seq 700 True ok'
expect 'seq 701 True failed'
expect 'waiting viewer None'
expect 'waiting routed 10 menuconfig'
expect 'waiting idle None'
expect 'waiting other-dir None'
expect 'waiting own-child None'
expect 'claim plain own'
expect 'claim agent token'
expect 'claim stale-bitbake own'
expect 'claim owned own'
expect 'client True True False False False False'
expect 'target zlib-native -c compile'
expect 'target torizon-docker'
expect 'target core-image-minimal'
expect 'target busybox -c compile'
expect 'target busybox -c fetch'
expect 'target bitbake'
expect 'snapshot own-only True True'
expect 'waiting-name menuconfig devshell'
expect 'builddir /b/my build'
grep -q 'describe ⏸ waiting for you in menuconfig' <<<"$result"

# ---- daemon: single instance (#9) and persistent opt-out (#15) -------------
# Count only daemons that use this test's state dir (a real watcher may run).
mon="$ROOT/scripts/build-monitor.py"
export HERDR_SOCKET_PATH="$TMPDIR_TEST/no-herdr.sock"
test_daemons() {
  local n=0 pid
  for pid in $(pgrep -f "$mon daemon" || true); do
    tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null \
      | grep -qxF "HERDR_PLUGIN_STATE_DIR=$HERDR_PLUGIN_STATE_DIR" && n=$((n + 1))
  done
  echo "$n"
}
trap 'python3 "$mon" stop >/dev/null 2>&1 || true; rm -rf "$TMPDIR_TEST"' EXIT
python3 "$mon" ensure-daemon >/dev/null &
python3 "$mon" ensure-daemon >/dev/null &
wait
sleep 1.5
daemons="$(test_daemons)"
[[ "$daemons" == 1 ]] || { echo "expected 1 daemon, got $daemons" >&2; exit 1; }
python3 "$mon" stop >/dev/null
mkdir -p "$HERDR_PLUGIN_CONFIG_DIR"
touch "$HERDR_PLUGIN_CONFIG_DIR/monitor.disabled"
[[ "$(python3 "$mon" ensure-daemon)" == *disabled* ]]
rm "$HERDR_PLUGIN_CONFIG_DIR/monitor.disabled"
[[ "$(HERDBAKE_MONITOR=0 python3 "$mon" ensure-daemon)" == *disabled* ]]
sleep 0.5
daemons="$(test_daemons)"
[[ "$daemons" == 0 ]] || { echo "opt-out started a daemon" >&2; exit 1; }

# ---- sidebar setup is explicit and validated (#6) --------------------------
fake_bin="$TMPDIR_TEST/bin"
mkdir -p "$fake_bin"
cat > "$fake_bin/herdr" <<'EOF'
#!/bin/sh
# Fake `herdr config check`: reject files containing "broken", accept the rest.
if [ "$1 $2" = "config check" ]; then
  if grep -q broken "$HERDR_CONFIG_PATH"; then echo "config: issues found"; exit 1; fi
  echo "config: ok"; exit 0
fi
exit 0
EOF
chmod +x "$fake_bin/herdr"
cfg="$TMPDIR_TEST/herdr-config.toml"
printf '[ui.sidebar.agents]\nrows = [\n  ["state_icon", "agent"],\n  ["workspace", "tab"],\n]\n' > "$cfg"
HERDR_BIN_PATH="$fake_bin/herdr" HERDR_CONFIG_PATH="$cfg" python3 "$mon" setup-sidebar >/dev/null
grep -qF '"$hb_build"' "$cfg"
python3 - "$cfg" <<'PY'
import sys
try:
    import tomllib
except ImportError:          # Python < 3.11: structural check only
    tomllib = None
text = open(sys.argv[1]).read()
if tomllib:
    rows = tomllib.loads(text)["ui"]["sidebar"]["agents"]["rows"]
    assert rows[1] == [{"token": "$hb_build", "fg": "#f9e2af"}], rows
else:
    assert text.index('"$hb_build"') < text.index('["workspace", "tab"]')
PY
# A config the check rejects is left byte-identical.
printf 'broken = [\n' > "$cfg"
before="$(sha256sum "$cfg")"
HERDR_BIN_PATH="$fake_bin/herdr" HERDR_CONFIG_PATH="$cfg" python3 "$mon" setup-sidebar >/dev/null
[[ "$(sha256sum "$cfg")" == "$before" ]]
# A missing herdr binary also leaves it untouched (no FileNotFoundError).
printf '[ui]\n' > "$cfg"
before="$(sha256sum "$cfg")"
HERDR_BIN_PATH="$TMPDIR_TEST/missing-herdr" HERDR_CONFIG_PATH="$cfg" python3 "$mon" setup-sidebar >/dev/null
[[ "$(sha256sum "$cfg")" == "$before" ]]
# The startup hook never edits the config.
grep -q 'ensure-daemon' "$ROOT/herdr-plugin.toml"
! grep -n 'setup_sidebar\|setup-sidebar' "$mon" | grep -v -e 'def cmd_setup_sidebar' -e '"setup-sidebar": cmd_setup_sidebar' -e '_capture(cmd_setup_sidebar)' | grep -q .

echo "build monitor tests passed"
