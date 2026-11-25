#!/usr/bin/env python3
"""Herdbake build monitor - live BitBake progress per Herdr pane.

One script, several entrypoints (see herdr-plugin.toml):

  ensure-daemon   start the detached watcher if it is not running (startup hook)
  daemon          the watcher loop itself (normally launched by ensure-daemon)
  stop            stop the watcher and clear everything it reported
  scan            one-shot scan, print JSON (debugging)
  open-list       open the popup with the running-builds list
  list-tui        the popup body
  setup-sidebar   append a `$hb_build` row to [ui.sidebar.agents] in Herdr's config

How it works
  * /proc is scanned for BitBake *client* processes (`python3 .../bin/bitbake`).
    The cooker (bitbake-server) daemonises and is reparented to init, so only
    the client links a build to the terminal it was started from.
  * Walking the client's parent chain until it meets a pane's shell PID maps the
    build to a Herdr pane. Agent processes met on the way (hermes, claude,
    codex, ...) or AI_AGENT in the client's environment mark it agent-launched.
  * Progress comes from the cooker log <TMPDIR>/log/cooker/<machine>/<ts>.log
    ("Running [setscene ]task N of M (...)"). Before tasks start (parsing), the
    knotty footer on the pane's screen is used when the pane shows it.
  * Reporting:
      - plain shell pane  -> pane.report_agent (agent "bitbake", working/idle) +
                             state label with the progress, so it shows up in the
                             sidebar like any agent and gets done notifications.
      - agent-owned pane  -> only a `hb_build` metadata token, so the agent keeps its
                             own lifecycle state.
      - every build pane  -> `hb_build` token (add `$hb_build` to a sidebar row to see it).
      - build end         -> Herdr notification (success / failure).
"""

from __future__ import annotations

import calendar
import fcntl
import json
import os
import pathlib
import re
import signal
import socket
import subprocess
import sys
import time

SOURCE = "plugin:herdbake:monitor"
AGENT_LABEL = "bitbake"
TOKEN = "hb_build"
POLL_S = float(os.environ.get("HERDBAKE_MONITOR_POLL_S", "2"))
DONE_HOLD_S = int(os.environ.get("HERDBAKE_MONITOR_HOLD_S", "900"))  # keep "done" this long
SOCKET_GONE_EXIT_S = 300

# Process names / argv basenames that identify a coding agent in the parent chain.
AGENT_NAMES = {
    "hermes": "hermes", "claude": "claude", "codex": "codex", "opencode": "opencode",
    "pi": "pi", "droid": "droid", "amp": "amp", "gemini": "gemini",
    "cursor-agent": "cursor", "copilot": "copilot", "kimi": "kimi", "grok": "grok",
    "aider": "aider", "goose": "goose", "kiro-cli": "kiro", "qwen": "qwen",
    "crush": "crush", "cline": "cline",
}

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
SELF = pathlib.Path(__file__).resolve()
HERE = SELF.parent


def state_dir() -> pathlib.Path:
    d = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    p = pathlib.Path(d) if d else pathlib.Path(
        os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local/state")
    ) / "herdr/plugins/herdbake"
    p = p / "monitor"
    p.mkdir(parents=True, exist_ok=True)
    return p


def socket_path() -> str:
    return os.environ.get("HERDR_SOCKET_PATH") or str(
        pathlib.Path.home() / ".config/herdr/herdr.sock"
    )


# --------------------------------------------------------------------------
# Herdr socket client
# --------------------------------------------------------------------------
class HerdrError(Exception):
    pass


_req = 0


def call(method: str, params: dict | None = None, timeout: float = 3.0):
    global _req
    _req += 1
    msg = {"id": f"herdbake-monitor:{_req}", "method": method, "params": params or {}}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(socket_path())
        s.sendall((json.dumps(msg) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    doc = json.loads(buf.decode("utf-8", "replace"))
    if "error" in doc:
        raise HerdrError(f"{method}: {doc['error']}")
    return doc.get("result", {})


def try_call(method, params=None):
    try:
        return call(method, params)
    except (OSError, ValueError, HerdrError) as e:
        log(f"warn: {e}")
        return None


# --------------------------------------------------------------------------
# /proc helpers
# --------------------------------------------------------------------------
_CLK = os.sysconf("SC_CLK_TCK")
_BOOT = (0.0, 0.0)   # (boot epoch, computed at)


def boot_time() -> float:
    """Boot time as a precise epoch. /proc/stat's btime is whole seconds, which
    makes process start times up to 1 s early - enough to confuse the cooker
    log of a build with the one that finished just before it."""
    global _BOOT
    now = time.time()
    if now - _BOOT[1] > 60:
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
        _BOOT = (time.time() - up, now)
    return _BOOT[0]


def proc_stat(pid: int):
    """(comm, ppid, start_epoch) or None."""
    try:
        raw = open(f"/proc/{pid}/stat").read()
    except OSError:
        return None
    lp, rp = raw.index("("), raw.rindex(")")
    comm = raw[lp + 1:rp]
    rest = raw[rp + 2:].split()
    return comm, int(rest[1]), boot_time() + int(rest[19]) / _CLK


def proc_argv(pid: int) -> list[str]:
    try:
        return [a for a in open(f"/proc/{pid}/cmdline", "rb").read().decode(
            "utf-8", "replace").split("\0") if a]
    except OSError:
        return []


def proc_env(pid: int) -> dict:
    try:
        raw = open(f"/proc/{pid}/environ", "rb").read().decode("utf-8", "replace")
    except OSError:
        return {}
    env = {}
    for kv in raw.split("\0"):
        k, sep, v = kv.partition("=")
        if sep:
            env[k] = v
    return env


def proc_cwd(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def is_bitbake_client(argv: list[str]) -> bool:
    """`python3 /x/bin/bitbake ...` or `/x/bin/bitbake ...` (not -server/-worker/-layers,
    and not `vim bitbake` / `less bitbake` on Poky's top-level bitbake/ dir)."""
    if not argv:
        return False
    if os.path.basename(argv[0]) == "bitbake":
        return True
    return (len(argv) > 1 and os.path.basename(argv[0]).startswith("python")
            and os.path.basename(argv[1]) == "bitbake")


def agent_of(pid: int) -> str | None:
    st = proc_stat(pid)
    argv = proc_argv(pid)
    names = set()
    if st:
        names.add(st[0])
    for a in argv[:2]:
        names.add(os.path.basename(a))
    for n in names:
        if n in AGENT_NAMES:
            return AGENT_NAMES[n]
    return None


# --------------------------------------------------------------------------
# Herdr pane map
# --------------------------------------------------------------------------
class PaneMap:
    """shell_pid -> pane dict, refreshed lazily (process_info is per-pane)."""

    def __init__(self):
        self.by_shell: dict[int, dict] = {}
        self.panes: dict[str, dict] = {}
        self.stamp = 0.0

    def refresh(self):
        res = try_call("pane.list")
        if res is None:
            return
        panes = {p["pane_id"]: p for p in res.get("panes", [])}
        by_shell = {}
        for pid_, p in panes.items():
            # Reuse cached shell pids for panes whose terminal did not change.
            old = next((v for v in self.by_shell.values()
                        if v["pane_id"] == pid_ and v.get("terminal_id") == p.get("terminal_id")), None)
            shell = old["_shell_pid"] if old else None
            if shell is None or not os.path.exists(f"/proc/{shell}"):
                info = try_call("pane.process_info", {"pane_id": pid_})
                shell = ((info or {}).get("process_info") or {}).get("shell_pid")
            if shell:
                p["_shell_pid"] = shell
                by_shell[shell] = p
        self.by_shell, self.panes, self.stamp = by_shell, panes, time.time()

    def labels(self):
        ws = {w["workspace_id"]: w.get("label") or w["workspace_id"]
              for w in ((try_call("workspace.list") or {}).get("workspaces") or [])}
        tabs = {t["tab_id"]: t.get("label") or str(t.get("number"))
                for t in ((try_call("tab.list") or {}).get("tabs") or [])}
        return ws, tabs


def locate(pid: int, pmap: PaneMap, snap: ProcSnapshot | None = None):
    """Walk parents of `pid`. Returns (pane | None, agent | None, chain)."""
    agent, chain, cur = None, [], pid
    for _ in range(64):
        st = proc_stat(cur)
        if not st:
            break
        if cur != pid:
            chain.append(st[0])
            a = agent_of(cur)
            if a and not agent:
                agent = a
        if cur in pmap.by_shell:
            return pmap.by_shell[cur], agent, chain
        ppid = snap.ppid.get(cur, st[1]) if snap else st[1]
        if ppid <= 1:
            break
        cur = ppid
    return None, agent, chain


# --------------------------------------------------------------------------
# Cooker log parsing
# --------------------------------------------------------------------------
RUN_RE = re.compile(r"^NOTE: Running (setscene )?task (\d+) of (\d+) \((.*)\)\s*$")
SUMMARY_RE = re.compile(r"^NOTE: Tasks Summary: Attempted (\d+) tasks of which (\d+) didn't need to be rerun and (.*)$")
FOOTER_RE = re.compile(
    r"(Loading cache|Parsing recipes|Initialising tasks|Checking sstate mirror object availability"
    r"|Sstate object availability|Setscene tasks|Currently\s+\d+\s+running tasks \(\d+ of \d+\)"
    r"|No currently running tasks \(\d+ of \d+\)|Waiting for \d+ running tasks to finish)[^%\n]*?(\d+)%"
)


def short_task(spec: str) -> str:
    """'virtual:native:/x/zlib_1.3.1.bb:do_compile' -> 'zlib-native:do_compile'."""
    m = re.match(r"(?:(?:virtual|mc):([^:/]+):)*(?:mc:[^:]*:)?(.*?\.bb(?:append)?):(\S+)$", spec)
    if not m:
        return spec[-60:]
    variant = ""
    v = re.findall(r"virtual:([^:]+):", spec)
    if v:
        variant = "-" + v[-1]
    name = os.path.basename(m.group(2)).rsplit(".", 1)[0]
    if "_" in name:
        base, ver = name.rsplit("_", 1)
        if ver[:1].isdigit() or ver.startswith("git"):
            name = base
    return f"{name}{variant}:{m.group(3)}"


class LogState:
    def __init__(self, path: str):
        self.path, self.offset = path, 0
        self.phase = None          # "setscene" | "tasks"
        self.n = self.m = 0
        self.sn = self.sm = 0
        self.current = ""
        self.errors = self.warnings = 0
        self.summary = None        # text after "and"
        self.failed = False
        self.interrupted = False

    def update(self):
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return
        if not data:
            return
        cut = data.rfind(b"\n")
        if cut < 0:
            return
        self.offset += cut + 1
        for line in data[:cut].decode("utf-8", "replace").splitlines():
            m = RUN_RE.match(line)
            if m:
                n, tot = int(m.group(2)), int(m.group(3))
                if m.group(1):
                    self.phase, self.sn, self.sm = "setscene", n, tot
                else:
                    self.phase, self.n, self.m = "tasks", n, tot
                self.current = short_task(m.group(4))
                continue
            if line.startswith("ERROR:"):
                if "Stopped build" in line or "Build was interrupted" in line:
                    self.interrupted = True
                else:
                    self.errors += 1
            elif line.startswith("WARNING:"):
                self.warnings += 1
            s = SUMMARY_RE.match(line)
            if s:
                self.summary = s.group(3)
                self.failed = "failed" in s.group(3)
                # Ctrl-C: bitbake drains running tasks and still says "all
                # succeeded", but attempted < planned total. A real failure
                # also stops early, so only apply this when nothing failed.
                if self.m and int(s.group(1)) < self.m and not self.failed:
                    self.interrupted = True

    def percent(self):
        if self.phase == "tasks" and self.m:
            return int(100 * self.n / self.m)
        if self.phase == "setscene" and self.sm:
            return int(100 * self.sn / self.sm)
        return None


def build_result(ls: LogState | None) -> tuple[str, str]:
    """(status, result text) for a finished build.

    status is ok | failed | interrupted | neutral. "interrupted" needs evidence
    (a "Stopped build" ERROR, or fewer tasks attempted than planned); a run with
    no summary and no errors (bitbake -e / -p / -s / -g, or no readable log) is
    neutral rather than a failure.
    """
    if ls and ls.interrupted:
        return "interrupted", "interrupted"
    if ls and ls.summary is not None:
        if ls.failed or ls.errors:
            return "failed", "FAILED"
        return "ok", "succeeded"
    if ls and ls.errors:
        return "failed", "FAILED"
    return "neutral", "finished"


LOG_START_SLACK_S = 0.5   # start-time jitter (jiffies, uptime sampling)


def log_name_epoch(path: pathlib.Path) -> float | None:
    """Epoch encoded in a cooker log name (BB_CONSOLELOG = .../${DATETIME}.log, UTC)."""
    if not re.fullmatch(r"\d{14}", path.stem):
        return None
    try:
        return float(calendar.timegm(time.strptime(path.stem, "%Y%m%d%H%M%S")))
    except ValueError:
        return None


def find_cooker_log(builddir: str, started: float,
                    exclude: set[str] | frozenset[str] = frozenset()) -> str | None:
    """Newest cooker log that belongs to a build started at `started`.

    Knotty asks the server for BB_CONSOLELOG after the client is up, so this
    build's log can't predate the client. Guards against picking up the
    previous build's log in `bitbake a && bitbake b`, where b starts the instant
    a writes its last line:
      * the log must have been modified after the client started,
      * a log whose name says its build started well before this client and
        that has not been written since is the earlier build's log, and
      * logs already attributed to another tracked build (`exclude`) are skipped.
    The caller keeps re-checking while the build runs and switches to a newer
    log when one appears.
    """
    cands = []
    for tmp in pathlib.Path(builddir).glob("tmp*/log/cooker/*/*.log"):
        if tmp.name.startswith("console-latest") or tmp.is_symlink():
            continue
        if str(tmp) in exclude:
            continue
        try:
            st = tmp.stat()
        except OSError:
            continue
        if st.st_mtime < started - LOG_START_SLACK_S:
            continue
        named = log_name_epoch(tmp)
        if named is not None and named < started - 2 and st.st_mtime < started + 3:
            continue
        cands.append((st.st_mtime, str(tmp)))
    return max(cands)[1] if cands else None


# --------------------------------------------------------------------------
# Build tracking
# --------------------------------------------------------------------------
class ProcSnapshot:
    """One pass over /proc per poll: pid -> (ppid, argv), own processes only.

    Other users' processes are skipped (#5: on shared build hosts their cmdline
    is readable but environ/cwd are not, so they could only produce noise), and
    every scan below works from this single snapshot instead of re-walking /proc.
    """

    def __init__(self):
        uid = os.getuid()
        self.ppid: dict[int, int] = {}
        self.argv: dict[int, list[str]] = {}
        self.kids: dict[int, list[int]] = {}
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            pid = int(d)
            try:
                if os.stat(f"/proc/{d}").st_uid != uid:
                    continue
            except OSError:
                continue
            st = proc_stat(pid)
            if not st:
                continue
            self.ppid[pid] = st[1]
            self.kids.setdefault(st[1], []).append(pid)
            self.argv[pid] = proc_argv(pid)

    def descendants(self, pid: int) -> set[int]:
        out, todo = set(), [pid]
        while todo:
            for c in self.kids.get(todo.pop(), []):
                if c not in out:
                    out.add(c)
                    todo.append(c)
        return out

    def clients(self) -> list[int]:
        """Top-level bitbake clients (nested ones, e.g. bitbake run by bitbake, dropped)."""
        found = {pid for pid, argv in self.argv.items() if is_bitbake_client(argv)}
        roots = []
        for pid in found:
            cur, nested = pid, False
            for _ in range(64):
                cur = self.ppid.get(cur, 0)
                if cur <= 1:
                    break
                if cur in found:
                    nested = True
                    break
            if not nested:
                roots.append(pid)
        return sorted(roots)

    def herdbake_terminals(self) -> list[dict]:
        """Herdbake popup terminals (scripts/pane-entry.sh).

        `routed` marks the ones route.py opened for an interactive BitBake task
        (menuconfig, devshell, ...): only those mean BitBake is waiting on the
        user (#3). The same entrypoint also hosts the log viewer, doctor, graph
        and other action popups, which must not mark a build as blocked.
        A live child means the command is still running; once it exits,
        pane-entry.sh only waits on `read` and has no children.
        """
        out = []
        for pid, argv in self.argv.items():
            if not any(a.endswith("scripts/pane-entry.sh") for a in argv[:3]):
                continue
            env = proc_env(pid)
            bd = terminal_build_dir(env.get("HERDBAKE_CMD", ""))
            out.append({
                "pid": pid, "busy": bool(self.kids.get(pid)),
                "routed": env.get("HERDBAKE_ROUTED") == "1",
                "builddir": os.path.realpath(bd) if bd else None,
                "title": env.get("HERDBAKE_TITLE") or "",
                "origin": env.get("HERDBAKE_ORIGIN_PANE") or "",
                "descendants": self.descendants(pid),
            })
        return out


def scan_clients() -> list[int]:
    return ProcSnapshot().clients()


def terminal_build_dir(cmd: str) -> str | None:
    """Build dir from a HERDBAKE_CMD ('bash .../with-build-env.sh <dir> ...')."""
    import shlex
    try:
        words = shlex.split(cmd)
    except ValueError:
        return None
    for i, w in enumerate(words[:-1]):
        if w.endswith("with-build-env.sh"):
            return words[i + 1]
    return None


def waiting_terminal(terms: list[dict], builddir: str, pid: int) -> dict | None:
    """The routed interactive terminal this build is blocked on, if any."""
    bd = os.path.realpath(builddir) if builddir else None
    if not bd:
        return None
    return next((t for t in terms
                 if t["routed"] and t["busy"] and t["builddir"] == bd
                 and pid not in t["descendants"]), None)


def interactive_name(title: str) -> str:
    t = title.lower()
    for key in ("menuconfig", "devshell", "ccmake", "nconfig", "xconfig", "devpyshell"):
        if key in t:
            return key
    if "developer" in t and "python shell" in t:
        return "devpyshell"
    if "developer" in t and "shell" in t:   # "OpenEmbedded Developer Shell"
        return "devshell"
    if t.endswith(" configuration"):
        return "menuconfig"
    return title or "interactive terminal"


def describe(b: dict) -> str:
    if b.get("waiting"):
        return f"⏸ waiting for you in {b['waiting']} (herdbake popup)"
    ls: LogState | None = b.get("log")
    pct = ls.percent() if ls else None
    if ls and ls.phase == "tasks":
        s = f"{pct}% task {ls.n}/{ls.m}"
    elif ls and ls.phase == "setscene":
        s = f"{pct}% setscene {ls.sn}/{ls.sm}"
    elif b.get("footer"):
        s = b["footer"]
    elif ls:
        s = "preparing tasks"   # cooker log exists, parsing/sstate checks running
    else:
        s = "parsing"
    if ls and ls.current and ls.phase:
        s += f" · {ls.current}"
    return s


# bitbake options that take a value (from bb.main.create_bitbake_parser).
_BB_VALUE_OPTS = {
    "-u", "--ui", "-c", "--cmd", "-C", "--clear-stamp", "--runall", "--runonly",
    "-S", "--dump-signatures", "-b", "--buildfile", "-l", "--log-domains",
    "-w", "--write-log", "-B", "--bind", "-T", "--idle-timeout", "--remote-server",
    "--token", "-r", "--read", "-R", "--postread", "-I", "--ignore-deps", "--mc",
}
_BB_SHORT_VALUE_OPTS = {o for o in _BB_VALUE_OPTS if len(o) == 2}


def short_target(argv: list[str]) -> str:
    """'bitbake -R conf/x.conf -ccompile zlib-native' -> 'zlib-native -c compile'."""
    args = argv[2:] if argv and os.path.basename(argv[0]).startswith("python") else argv[1:]
    targets, task, i = [], None, 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            targets += args[i:]
            break
        if a.startswith("--"):
            opt, eq, val = a.partition("=")
            if opt in _BB_VALUE_OPTS and not eq:
                val = args[i] if i < len(args) else ""
                i += 1
            if opt == "--cmd":
                task = val
            continue
        if a.startswith("-") and len(a) > 1:
            opt = a[:2]
            if opt in _BB_SHORT_VALUE_OPTS:
                val = a[2:]
                if not val:
                    val = args[i] if i < len(args) else ""
                    i += 1
                if opt == "-c":
                    task = val
            continue
        targets.append(a)
    t = " ".join(targets) or "bitbake"
    return f"{t} -c {task}" if task else t


class Monitor:
    def __init__(self):
        self.pmap = PaneMap()
        self.builds: dict[int, dict] = {}        # pid -> build
        self.finished: list[dict] = []           # recently finished (for list + release)
        self.owned: dict[str, int] = {}          # pane_id -> seq (we hold report_agent)
        self.seq = int(time.time() * 1000)
        self.prev_argv: dict[int, list[str]] | None = None   # last poll's snapshot

    def claimed_logs(self, but: dict | None = None) -> set[str]:
        """Cooker logs already attributed to other tracked or recent builds."""
        return {x["log"].path for x in list(self.builds.values()) + self.finished
                if x is not but and x.get("log")}

    def start_time(self, pid: int, proc_start: float, now: float) -> float:
        """When this process became a bitbake client.

        `bash -c '... && bitbake a && bitbake b'` execs the last command, so b
        keeps the shell's pid and start time - from before a even ran. If the
        previous poll saw this pid running something else, it exec'd into
        bitbake since then.
        """
        if self.prev_argv is not None:
            before = self.prev_argv.get(pid)
            if before is not None and not is_bitbake_client(before):
                return max(proc_start, now - POLL_S)
        return proc_start

    def nseq(self):
        self.seq += 1
        return self.seq

    # ---- reporting ------------------------------------------------------
    def report(self, b: dict, state: str, label: str):
        pane = b.get("pane")
        if not pane:
            return
        pid_ = pane["pane_id"]
        # Only push when something visible changed: every report fires Herdr
        # pane events that other plugins' hooks react to. Refresh token TTL
        # at least every ~20 s so it does not expire mid-build.
        key = (state, label)
        if b.get("_last") == key and time.time() - b.get("_last_t", 0) < 20:
            return
        b["_last"], b["_last_t"] = key, time.time()
        token = {"working": f"⚙ {label}", "blocked": label}.get(state, label)
        if b["mode"] == "own":
            try_call("pane.report_agent", {
                "pane_id": pid_, "source": SOURCE, "agent": AGENT_LABEL,
                "state": state, "message": label, "seq": self.nseq()})
            self.owned[pid_] = self.seq
            try_call("pane.report_metadata", {
                "pane_id": pid_, "source": SOURCE, "agent": AGENT_LABEL,
                "display_agent": b["display"],
                "state_labels": {state: label[:80]},
                "tokens": {TOKEN: token[:120]},
                "ttl_ms": 6 * 3600 * 1000, "seq": self.nseq()})
        else:
            try_call("pane.report_metadata", {
                "pane_id": pid_, "source": SOURCE,
                "tokens": {TOKEN: token[:120]},
                "ttl_ms": 60_000 if state in ("working", "blocked") else DONE_HOLD_S * 1000,
                "seq": self.nseq()})

    def release(self, pane_id: str):
        if pane_id in self.owned:
            try_call("pane.release_agent", {"pane_id": pane_id, "source": SOURCE,
                                            "agent": AGENT_LABEL, "seq": self.nseq()})
            self.owned.pop(pane_id, None)
        try_call("pane.report_metadata", {"pane_id": pane_id, "source": SOURCE,
                                          "clear_display_agent": True, "clear_state_labels": True,
                                          "tokens": {TOKEN: None}, "seq": self.nseq()})

    def popup_origin(self, pid: int, terms: list[dict]):
        """If `pid` runs inside a herdbake popup, return (popup, origin pane).

        Herdbake actions pass the pane they were invoked from explicitly as
        HERDBAKE_ORIGIN_PANE (#10); HERDR_PANE_ID on a popup process is not a
        reliable stand-in.
        """
        for t in terms:
            if pid in t["descendants"]:
                pane_id = t.get("origin") or None
                return t, self.pmap.panes.get(pane_id) if pane_id else None
        return None, None

    def claim_mode(self, pane: dict | None) -> str:
        """"own": report a bitbake agent on the pane; "token": metadata only.

        Own the lifecycle on panes Herdr does not attribute to an agent, or that
        we already hold from an earlier build (#7: a second build in the same
        shell pane would otherwise see agent == "bitbake" and degrade to tokens).
        """
        if not pane:
            return "token"
        if not pane.get("agent") or pane["pane_id"] in self.owned:
            return "own"
        if pane.get("agent") == AGENT_LABEL:
            return "own"
        return "token"

    # ---- main loop step -------------------------------------------------
    def step(self):
        now = time.time()
        snap = ProcSnapshot()
        pids = snap.clients()
        new = [p for p in pids if p not in self.builds]
        terms = snap.herdbake_terminals() if pids else []
        if new and (now - self.pmap.stamp > 1):
            self.pmap.refresh()
        for pid in new:
            st = proc_stat(pid)
            if not st:
                continue
            started = self.start_time(pid, st[2], now)
            argv = snap.argv.get(pid) or proc_argv(pid)
            env = proc_env(pid)
            pane, agent, chain = locate(pid, self.pmap, snap)
            via_popup = False
            if pane is None:
                # Herdbake action popups (graph, kernel, ...) are not panes;
                # attribute their builds to the pane the action was invoked from.
                popup, origin = self.popup_origin(pid, terms)
                if popup:
                    pane, via_popup = origin, origin is not None
            if not agent and env.get("AI_AGENT"):
                agent = env["AI_AGENT"].replace("-agent", "")
            builddir = env.get("BUILDDIR") or proc_cwd(pid) or ""
            mode = self.claim_mode(pane)
            if pane:
                # A new build replaces any finished-build badge on this pane; the
                # old entries stay listed (and keep their logs claimed) but must
                # not release the pane when their hold expires.
                for f in self.finished:
                    if f.get("pane") and f["pane"]["pane_id"] == pane["pane_id"]:
                        f["released"] = True
            b = {
                "pid": pid, "started": started, "argv": argv, "target": short_target(argv),
                "builddir": builddir, "pane": pane, "agent": agent, "chain": chain,
                "mode": mode, "log": None, "footer": None, "via_popup": via_popup,
                "waiting": None,
                "display": f"bitbake ({agent})" if agent else "bitbake",
            }
            self.builds[pid] = b
            log(f"new build pid={pid} pane={pane and pane['pane_id']} agent={agent} "
                f"mode={mode} popup={via_popup} dir={builddir} target={b['target']}")

        for pid, b in list(self.builds.items()):
            alive = pid in pids
            if alive and b["builddir"]:
                # Keep looking while the build runs (#2). Switch only when the
                # current log already ended (has a Tasks Summary) - i.e. it was
                # the previous build's - never away from a log still in progress,
                # and never for a build that has exited (it would grab the log
                # of the build that started right after it).
                cur = b["log"]
                if cur is None or cur.summary is not None:
                    path = find_cooker_log(b["builddir"], b["started"], self.claimed_logs(but=b))
                    if path and (cur is None or path != cur.path):
                        if cur is not None:
                            log(f"build pid={pid} switched log {cur.path} -> {path}")
                        b["log"] = LogState(path)
            if b["log"]:
                b["log"].update()
            if alive:
                # A routed menuconfig/devshell terminal for this build dir still
                # running its command means BitBake is blocked on the user.
                waiting = waiting_terminal(terms, b["builddir"], pid)
                b["waiting"] = interactive_name(waiting["title"]) if waiting else None
                if b["pane"] and (not b["log"] or not b["log"].phase) and b["mode"] == "own":
                    b["footer"] = self.read_footer(b["pane"]["pane_id"])
                self.report(b, "blocked" if b["waiting"] else "working", describe(b))
            else:
                self.finish(b)
                del self.builds[pid]

        # release panes whose "done" display has expired
        keep = []
        for f in self.finished:
            if now - f["ended"] > DONE_HOLD_S:
                pane = f.get("pane")
                if pane and not f.get("released") and not any(
                        x.get("pane") and x["pane"]["pane_id"] == pane["pane_id"]
                        for x in self.builds.values()):
                    self.release(pane["pane_id"])
            else:
                keep.append(f)
        self.finished = keep
        self.prev_argv = snap.argv
        self.write_state()

    def read_footer(self, pane_id: str) -> str | None:
        res = try_call("pane.read", {"pane_id": pane_id, "source": "visible", "lines": 6})
        text = ""
        if res:
            r = res.get("read") or res
            text = r.get("text") or r.get("content") or ""
        hits = FOOTER_RE.findall(text)
        if not hits:
            return None
        what, pct = hits[-1]
        what = re.sub(r"\s+", " ", what).strip()
        return f"{pct}% {what.lower()}"

    def finish(self, b: dict):
        ls: LogState | None = b["log"]
        dur = int(time.time() - b["started"])
        dur_s = f"{dur // 60}m{dur % 60:02d}s" if dur >= 60 else f"{dur}s"
        status, result = build_result(ls)
        icon = {"ok": "✔", "failed": "✖", "interrupted": "✖", "neutral": "■"}[status]
        label = f"{icon} {result} {dur_s}"
        if ls and ls.m:
            label += f" · {ls.m} tasks"
        if ls and ls.errors:
            label += f" · {ls.errors} err"
        b["result"], b["label"], b["ended"], b["status"] = result, label, time.time(), status
        pane = b.get("pane")
        if status == "neutral":
            # No tasks ran (bitbake -e / -p / -s / -g, or a log we can't read):
            # nothing worth a 15-minute badge or a notification. Drop the claim now.
            if pane:
                self.release(pane["pane_id"])
            b["released"] = True
        else:
            self.report(b, "idle", label)
            # Builds outside Herdr (no pane) are listed in the popup only.
            if pane:
                who = f" (by {b['agent']})" if b["agent"] else ""
                try_call("notification.show", {
                    "title": f"bitbake {b['target']} {result}{who}",
                    "body": f"{label} in {pane.get('label') or pane['pane_id']}"
                            f" · {os.path.basename(b['builddir'])}",
                    "sound": "done" if status == "ok" else "request"})
        log(f"finished pid={b['pid']} {label}")
        self.finished.append(b)

    def write_state(self):
        def ser(b, running):
            ls = b.get("log")
            p = b.get("pane") or {}
            return {
                "pid": b["pid"], "running": running, "target": b["target"],
                "builddir": b["builddir"], "agent": b["agent"],
                "pane_id": p.get("pane_id"), "workspace_id": p.get("workspace_id"),
                "tab_id": p.get("tab_id"), "pane_label": p.get("label"),
                "started": b["started"], "ended": b.get("ended"),
                "status": describe(b) if running else b.get("label"),
                "percent": ls.percent() if ls else None,
                "log": ls.path if ls else None,
                "errors": ls.errors if ls else 0, "warnings": ls.warnings if ls else 0,
                "via_popup": b.get("via_popup", False), "waiting": b.get("waiting"),
            }
        doc = {"updated": time.time(),
               "builds": [ser(b, True) for b in self.builds.values()]
               + [ser(b, False) for b in self.finished]}
        tmp = state_dir() / "builds.json.tmp"
        tmp.write_text(json.dumps(doc, indent=1))
        tmp.replace(state_dir() / "builds.json")

    def cleanup(self):
        for pane_id in list(self.owned):
            self.release(pane_id)
        for b in self.builds.values():
            if b.get("pane"):
                self.release(b["pane"]["pane_id"])


# --------------------------------------------------------------------------
# daemon management
# --------------------------------------------------------------------------
def log(msg: str):
    line = f"{time.strftime('%F %T')} {msg}\n"
    try:
        with open(state_dir() / "monitor.log", "a") as f:
            f.write(line)
    except OSError:
        pass


def pidfile() -> pathlib.Path:
    return state_dir() / "daemon.pid"


def code_version() -> str:
    """Changes whenever this script changes (plugin update / branch switch)."""
    try:
        st = SELF.stat()
        return f"{int(st.st_mtime)}-{st.st_size}"
    except OSError:
        return "?"


def config_dir() -> pathlib.Path | None:
    d = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not d:
        try:
            d = subprocess.run([HERDR, "plugin", "config-dir", "herdbake"],
                               capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            d = ""
    return pathlib.Path(d) if d else None


def disabled_marker() -> pathlib.Path | None:
    cd = config_dir()
    return cd / "monitor.disabled" if cd else None


def monitor_disabled() -> bool:
    """Persistent opt-out (#15): HERDBAKE_MONITOR=0 in Herdr's environment, or a
    `monitor.disabled` file in the plugin config dir (what `s` in the popup
    writes when it stops the watcher)."""
    if os.environ.get("HERDBAKE_MONITOR", "").strip().lower() in ("0", "off", "false", "no"):
        return True
    m = disabled_marker()
    return bool(m and m.exists())


def read_pidfile() -> tuple[int | None, str]:
    try:
        parts = pidfile().read_text().split()
        return int(parts[0]), (parts[1] if len(parts) > 1 else "")
    except (OSError, ValueError, IndexError):
        return None, ""


def running_pid() -> int | None:
    pid, _ = read_pidfile()
    if pid is None:
        return None
    argv = proc_argv(pid)
    if any(a.endswith("build-monitor.py") for a in argv) and "daemon" in argv:
        return pid
    return None


def cmd_daemon():
    # One watcher per user (#9): two ensure-daemon calls racing (startup hook +
    # monitor action) must not leave two daemons reporting every build twice.
    lock = open(state_dir() / "daemon.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    pidfile().write_text(f"{os.getpid()} {code_version()}\n")
    mon = Monitor()
    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    log(f"daemon start pid={os.getpid()} version={code_version()} socket={socket_path()}")
    gone_since = None
    while not stop["flag"]:
        if not os.path.exists(socket_path()):
            gone_since = gone_since or time.time()
            if time.time() - gone_since > SOCKET_GONE_EXIT_S:
                log("herdr socket gone, exiting")
                break
        else:
            gone_since = None
        try:
            mon.step()
        except Exception as e:  # never die on a single bad scan
            log(f"error: {e!r}")
        time.sleep(POLL_S)
    mon.cleanup()
    log("daemon stop")
    try:
        if read_pidfile()[0] == os.getpid():
            pidfile().unlink()
    except OSError:
        pass


def cmd_ensure_daemon(force: bool = False):
    if monitor_disabled() and not force:
        print("herdbake monitor disabled (HERDBAKE_MONITOR=0 or monitor.disabled); not starting")
        return
    pid = running_pid()
    if pid:
        if read_pidfile()[1] == code_version():
            print(f"herdbake monitor already running (pid {pid})")
            return
        # The script changed since this daemon started (plugin update): restart.
        log(f"code changed, restarting daemon pid={pid}")
        _stop_pid(pid)
    logf = open(state_dir() / "daemon.out", "a")
    p = subprocess.Popen([sys.executable, str(SELF), "daemon"],
                         stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                         start_new_session=True, close_fds=True, cwd=str(HERE.parent))
    print(f"herdbake monitor started (pid {p.pid})")


def _stop_pid(pid: int):
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not os.path.exists(f"/proc/{pid}"):
            break
        time.sleep(0.1)


def cmd_stop():
    pid = running_pid()
    if not pid:
        print("herdbake monitor not running")
        return
    _stop_pid(pid)
    print(f"herdbake monitor stopped (pid {pid})")


def set_disabled(disabled: bool):
    m = disabled_marker()
    if not m:
        return
    if disabled:
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text("Created by the Herdbake build monitor popup. Delete to re-enable.\n")
    else:
        m.unlink(missing_ok=True)


def cmd_scan():
    pm = PaneMap()
    pm.refresh()
    snap = ProcSnapshot()
    out = []
    for pid in snap.clients():
        st = proc_stat(pid)
        if not st:
            continue
        pane, agent, chain = locate(pid, pm, snap)
        env = proc_env(pid)
        bd = env.get("BUILDDIR") or proc_cwd(pid)
        lp = find_cooker_log(bd, st[2]) if bd else None
        ls = LogState(lp) if lp else None
        if ls:
            ls.update()
        out.append({"pid": pid, "target": short_target(snap.argv.get(pid, [])),
                    "pane": pane and pane["pane_id"], "pane_agent": pane and pane.get("agent"),
                    "agent": agent or env.get("AI_AGENT"), "chain": chain, "builddir": bd,
                    "log": lp, "status": describe({"log": ls})})
    print(json.dumps(out, indent=1))


# --------------------------------------------------------------------------
# list popup
# --------------------------------------------------------------------------
def cmd_open_list():
    subprocess.run([HERDR, "plugin", "pane", "open", "--plugin", "herdbake",
                    "--entrypoint", "monitor", "--focus"],
                   stdout=subprocess.DEVNULL)


def _ago(t):
    d = int(time.time() - t)
    return f"{d // 3600}h{d % 3600 // 60:02d}m" if d >= 3600 else f"{d // 60}m{d % 60:02d}s"


def cmd_list_tui():
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    pm = PaneMap()
    flash = ""
    sel = 0   # cursor (#14: selection works for any number of builds)

    def read_key() -> str:
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            return ch
        # Arrow keys arrive as ESC [ A/B; a lone ESC closes the popup.
        if select.select([sys.stdin], [], [], 0.05)[0] and sys.stdin.read(1) == "[":
            return {"A": "up", "B": "down"}.get(sys.stdin.read(1), "")
        return "esc"

    try:
        while True:
            try:
                doc = json.loads((state_dir() / "builds.json").read_text())
            except (OSError, ValueError):
                doc = {"builds": [], "updated": 0}
            ws, tabs = pm.labels()
            rows = doc["builds"]
            sel = max(0, min(sel, len(rows) - 1))
            status = ""
            if not running_pid():
                status = ("   \033[31m(monitor disabled)\033[0m" if monitor_disabled()
                          else "   \033[31m(monitor not running)\033[0m")
            out = ["\033[H\033[2J\033[1m BitBake builds\033[0m" + status, ""]
            if not rows:
                out.append("  no bitbake builds running")
            for i, r in enumerate(rows):
                where = "outside herdr"
                if r["pane_id"]:
                    where = (f"{ws.get(r['workspace_id'], r['workspace_id'])} › "
                             f"tab {tabs.get(r['tab_id'], r['tab_id'])} › "
                             f"{r['pane_label'] or r['pane_id']}")
                if r.get("via_popup"):
                    where += " (herdbake popup)"
                who = f"\033[35m{r['agent']}\033[0m" if r["agent"] else "\033[36muser\033[0m"
                if r["running"]:
                    state = "\033[31m⏸\033[0m" if r.get("waiting") else "\033[33m●\033[0m"
                else:
                    st = (r["status"] or "")[:1]
                    state = {"✔": "\033[32m✔\033[0m", "■": "\033[2m■\033[0m"}.get(st, "\033[31m✖\033[0m")
                age = _ago(r["started"]) if r["running"] else f"ended {_ago(r['ended'])} ago"
                cursor = "\033[7m›\033[0m" if i == sel else " "
                out.append(f"{cursor}{state} \033[1m{r['target']}\033[0m  ({who})  {age}")
                out.append(f"     {r['status']}")
                out.append(f"     {where}  ·  {r['builddir']}")
                if r["errors"] or r["warnings"]:
                    out.append(f"     {r['errors']} errors, {r['warnings']} warnings")
                out.append("")
            out.append("\033[2m ↑/↓ or j/k select · enter jump to pane · l open log · "
                       "s start/stop monitor · b add sidebar row · q close\033[0m")
            if flash:
                out.append(f"\033[33m {flash}\033[0m")
                flash = ""
            sys.stdout.write("\n".join(out))
            sys.stdout.flush()
            if not select.select([sys.stdin], [], [], 1.0)[0]:
                continue
            key = read_key()
            if key in ("q", "esc"):
                return
            if key in ("up", "k"):
                sel = max(0, sel - 1)
            elif key in ("down", "j"):
                sel = min(len(rows) - 1, sel + 1) if rows else 0
            elif key == "s":
                if running_pid():
                    cmd_stop()
                    set_disabled(True)   # persists across Herdr restarts (#15)
                    flash = "monitor stopped and disabled (s again to re-enable)"
                else:
                    set_disabled(False)
                    with _quiet():
                        cmd_ensure_daemon()
                    flash = ("monitor started" if not monitor_disabled()
                             else "HERDBAKE_MONITOR=0 is set in Herdr's environment")
            elif key == "b":
                buf = _capture(cmd_setup_sidebar)
                flash = buf.splitlines()[0] if buf else "done"
            elif key in ("\n", "\r") and rows:
                r = rows[sel]
                if r["pane_id"]:
                    try:
                        call("pane.focus", {"pane_id": r["pane_id"]})
                    except Exception as e:
                        flash = f"focus failed: {e}"
                        continue
                    return
                flash = "this build is not in a Herdr pane"
            elif key == "l" and rows:
                path = rows[sel]["log"]
                if path:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                    subprocess.run(["less", "+G", path])
                    tty.setcbreak(fd)
                else:
                    flash = "no cooker log for this build"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _capture(fn) -> str:
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue().strip()


def _quiet():
    import contextlib
    import io
    return contextlib.redirect_stdout(io.StringIO())


# --------------------------------------------------------------------------
# sidebar setup: append ["$hb_build"] row to [ui.sidebar.agents]
# --------------------------------------------------------------------------
SIDEBAR_ROW = '  [{ token = "$hb_build", fg = "#f9e2af" }],\n'


def sidebar_with_row(text: str) -> str | None:
    """Config text with the $hb_build row added, or None if no safe spot exists."""
    m = re.search(r"^\[ui\.sidebar\.agents\]\s*\n(?:.*\n)*?rows\s*=\s*\[\n", text, re.M)
    if m:
        anchor = '  ["workspace", "tab"],'
        rest = text[m.end():]
        if anchor not in rest:
            return None
        return text[:m.end()] + rest.replace(anchor, SIDEBAR_ROW + anchor, 1)
    return text.rstrip("\n") + ("\n\n" if text else "") + (
        "[ui.sidebar.agents]\nrows = [\n"
        '  ["state_icon", "agent"],\n' + SIDEBAR_ROW + '  ["workspace", "tab"],\n]\n')


def cmd_setup_sidebar():
    """One-time, explicit: add the progress row to Herdr's config (#6).

    Never run automatically. The candidate is validated with `herdr config
    check` in a temp file *before* the real config is replaced, so a failure at
    any point leaves config.toml untouched.
    """
    import tempfile
    cfg = pathlib.Path(os.environ.get("HERDR_CONFIG_PATH")
                       or pathlib.Path.home() / ".config/herdr/config.toml")
    text = cfg.read_text() if cfg.exists() else ""
    if '"$hb_build"' in text:
        print("sidebar already has $hb_build")
        return
    new = sidebar_with_row(text)
    if new is None:
        print("could not find a place in [ui.sidebar.agents].rows; add this row manually:\n"
              + SIDEBAR_ROW)
        return
    cfg.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(cfg.parent), prefix=".herdbake-", suffix=".toml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new)
        try:
            chk = subprocess.run([HERDR, "config", "check"], capture_output=True, text=True,
                                 env={**os.environ, "HERDR_CONFIG_PATH": tmp})
        except OSError as e:
            print(f"could not validate the new config ({e}); config.toml left untouched")
            return
        if chk.returncode != 0 or "ok" not in chk.stdout:
            print("config check failed; config.toml left untouched:\n" + chk.stdout + chk.stderr)
            return
        if cfg.exists():
            bak = cfg.with_suffix(".toml.bak-herdbake")
            bak.write_text(text)
        os.replace(tmp, cfg)
        tmp = None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    subprocess.run([HERDR, "server", "reload-config"], capture_output=True)
    print(f"added $hb_build row to {cfg}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {
        "daemon": cmd_daemon, "ensure-daemon": cmd_ensure_daemon, "stop": cmd_stop,
        "scan": cmd_scan, "open-list": cmd_open_list, "list-tui": cmd_list_tui,
        "setup-sidebar": cmd_setup_sidebar,
    }[cmd]()


if __name__ == "__main__":
    main()
