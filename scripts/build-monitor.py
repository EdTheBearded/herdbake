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
_BOOT = None


def boot_time() -> float:
    global _BOOT
    if _BOOT is None:
        for line in open("/proc/stat"):
            if line.startswith("btime "):
                _BOOT = float(line.split()[1])
    return _BOOT


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
    """`python3 /x/bin/bitbake ...` or `/x/bin/bitbake ...` (not -server/-worker/-layers)."""
    for a in argv[:2]:
        if os.path.basename(a) == "bitbake":
            return True
    return False


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


def locate(pid: int, pmap: PaneMap):
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
        if st[1] <= 1:
            break
        cur = st[1]
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


def find_cooker_log(builddir: str, started: float) -> str | None:
    """Newest cooker log written since the client started."""
    cands = []
    for tmp in pathlib.Path(builddir).glob("tmp*/log/cooker/*/*.log"):
        if tmp.name.startswith("console-latest") or tmp.is_symlink():
            continue
        try:
            mt = tmp.stat().st_mtime
        except OSError:
            continue
        if mt >= started - 5:
            cands.append((mt, str(tmp)))
    return max(cands)[1] if cands else None


# --------------------------------------------------------------------------
# Build tracking
# --------------------------------------------------------------------------
def scan_clients():
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        argv = proc_argv(pid)
        if argv and is_bitbake_client(argv):
            out.append(pid)
    # drop nested clients (bitbake invoked by bitbake, e.g. -c devshell wrappers)
    pids = set(out)
    roots = []
    for pid in out:
        cur, nested = pid, False
        for _ in range(64):
            st = proc_stat(cur)
            if not st or st[1] <= 1:
                break
            cur = st[1]
            if cur in pids:
                nested = True
                break
        if not nested:
            roots.append(pid)
    return roots


def children_map() -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            st = proc_stat(int(d))
            if st:
                kids.setdefault(st[1], []).append(int(d))
    return kids


def descendants(pid: int, kids: dict[int, list[int]]) -> set[int]:
    out, todo = set(), [pid]
    while todo:
        for c in kids.get(todo.pop(), []):
            if c not in out:
                out.add(c)
                todo.append(c)
    return out


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


def scan_herdbake_terminals(kids: dict[int, list[int]]) -> list[dict]:
    """Herdbake popup terminals (scripts/pane-entry.sh) that are still running
    their command. A live child means the user is inside menuconfig/devshell
    (or an action popup is still working); once the command exits,
    pane-entry.sh only waits on `read` and has no children."""
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        argv = proc_argv(pid)
        if not any(a.endswith("scripts/pane-entry.sh") for a in argv[:3]):
            continue
        env = proc_env(pid)
        cmd = env.get("HERDBAKE_CMD", "")
        bd = terminal_build_dir(cmd)
        out.append({
            "pid": pid, "busy": bool(kids.get(pid)),
            "builddir": os.path.realpath(bd) if bd else None,
            "title": env.get("HERDBAKE_TITLE") or "",
            "descendants": descendants(pid, kids),
        })
    return out


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


def short_target(argv: list[str]) -> str:
    args = argv[2:] if os.path.basename(argv[0]).startswith("python") else argv[1:]
    words, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("-c", "--cmd"):
            skip = True
            words.append(None)  # placeholder for task
            continue
        if a.startswith("-"):
            continue
        words.append(a)
    task = None
    if None in words:
        i = args.index("-c") if "-c" in args else args.index("--cmd")
        task = args[i + 1] if i + 1 < len(args) else None
        words = [w for w in words if w]
    t = " ".join(words) or "bitbake"
    return f"{t} -c {task}" if task else t


class Monitor:
    def __init__(self):
        self.pmap = PaneMap()
        self.builds: dict[int, dict] = {}        # pid -> build
        self.finished: list[dict] = []           # recently finished (for list + release)
        self.owned: dict[str, int] = {}          # pane_id -> seq (we hold report_agent)
        self.seq = int(time.time() * 1000)

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
        """If `pid` runs inside a herdbake popup, return (popup, origin pane)."""
        for t in terms:
            if pid in t["descendants"]:
                env = proc_env(t["pid"])
                pane_id = None
                try:
                    ctx = json.loads(env.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
                    pane_id = ctx.get("focused_pane_id") or ctx.get("pane_id")
                except ValueError:
                    pass
                pane_id = pane_id or env.get("HERDR_PANE_ID")
                return t, self.pmap.panes.get(pane_id) if pane_id else None
        return None, None

    # ---- main loop step -------------------------------------------------
    def step(self):
        now = time.time()
        pids = scan_clients()
        new = [p for p in pids if p not in self.builds]
        kids = children_map() if pids else {}
        terms = scan_herdbake_terminals(kids) if pids else []
        if new and (now - self.pmap.stamp > 1):
            self.pmap.refresh()
        for pid in new:
            st = proc_stat(pid)
            if not st:
                continue
            argv = proc_argv(pid)
            env = proc_env(pid)
            pane, agent, chain = locate(pid, self.pmap)
            via_popup = False
            if pane is None:
                # Herdbake action popups (graph, kernel, layers...) are not panes;
                # attribute their builds to the pane the action was invoked from.
                popup, origin = self.popup_origin(pid, terms)
                if popup:
                    pane, via_popup = origin, True
            if not agent and env.get("AI_AGENT"):
                agent = env["AI_AGENT"].replace("-agent", "")
            builddir = env.get("BUILDDIR") or proc_cwd(pid) or ""
            # Own the lifecycle only on panes Herdr does not already attribute to an
            # agent; otherwise just add a token so the agent keeps its own state.
            mode = "own" if pane and not pane.get("agent") else "token"
            b = {
                "pid": pid, "started": st[2], "argv": argv, "target": short_target(argv),
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
            if b["log"] is None and b["builddir"]:
                path = find_cooker_log(b["builddir"], b["started"])
                if path:
                    b["log"] = LogState(path)
            if b["log"]:
                b["log"].update()
            if alive:
                # A herdbake terminal (menuconfig/devshell routed by route.py) for
                # this build dir that is still running its command means bitbake
                # is blocked on the user, not making progress.
                bd = os.path.realpath(b["builddir"]) if b["builddir"] else None
                waiting = next((t for t in terms if t["busy"] and t["builddir"] == bd
                                and pid not in t["descendants"]), None)
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
                if pane and not any(x.get("pane") and x["pane"]["pane_id"] == pane["pane_id"]
                                    for x in self.builds.values()):
                    self.release(pane["pane_id"])
            else:
                keep.append(f)
        self.finished = keep
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
        if ls and ls.interrupted:
            ok, result = False, "interrupted"
        elif ls and ls.summary is not None:
            ok = not ls.failed and ls.errors == 0
            result = "succeeded" if ok else "FAILED"
        elif ls and ls.errors:
            ok, result = False, "FAILED"
        else:
            ok, result = False, "interrupted"
        icon = "✔" if ok else "✖"
        label = f"{icon} {result} {dur_s}"
        if ls and ls.m:
            label += f" · {ls.m} tasks"
        if ls and ls.errors:
            label += f" · {ls.errors} err"
        b["result"], b["label"], b["ended"] = result, label, time.time()
        self.report(b, "idle", label)
        where = ""
        if b["pane"]:
            where = f" in {b['pane'].get('label') or b['pane']['pane_id']}"
        who = f" (by {b['agent']})" if b["agent"] else ""
        try_call("notification.show", {
            "title": f"bitbake {b['target']} {result}{who}",
            "body": f"{label}{where} · {os.path.basename(b['builddir'])}",
            "sound": "done" if ok else "request"})
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


def running_pid() -> int | None:
    try:
        pid = int(pidfile().read_text())
    except (OSError, ValueError):
        return None
    argv = proc_argv(pid)
    if any(a.endswith("build-monitor.py") for a in argv) and "daemon" in argv:
        return pid
    return None


def cmd_daemon():
    pidfile().write_text(str(os.getpid()))
    mon = Monitor()
    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    log(f"daemon start pid={os.getpid()} socket={socket_path()}")
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
        pidfile().unlink()
    except OSError:
        pass


def cmd_ensure_daemon():
    # herdr-usage regenerates its [ui.sidebar.agents] block when its settings
    # are saved, dropping our row; re-add it at startup unless opted out.
    if (state_dir() / "sidebar-installed").exists() and not (state_dir() / "no-sidebar").exists():
        try:
            cmd_setup_sidebar(quiet=True)
        except Exception as e:
            log(f"sidebar re-add failed: {e!r}")
    pid = running_pid()
    if pid:
        print(f"herdbake monitor already running (pid {pid})")
        return
    logf = open(state_dir() / "daemon.out", "a")
    p = subprocess.Popen([sys.executable, str(SELF), "daemon"],
                         stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                         start_new_session=True, close_fds=True, cwd=str(HERE.parent))
    print(f"herdbake monitor started (pid {p.pid})")


def cmd_stop():
    pid = running_pid()
    if not pid:
        print("herdbake monitor not running")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not os.path.exists(f"/proc/{pid}"):
            break
        time.sleep(0.1)
    print(f"herdbake monitor stopped (pid {pid})")


def cmd_scan():
    pm = PaneMap()
    pm.refresh()
    out = []
    for pid in scan_clients():
        st = proc_stat(pid)
        pane, agent, chain = locate(pid, pm)
        env = proc_env(pid)
        bd = env.get("BUILDDIR") or proc_cwd(pid)
        lp = find_cooker_log(bd, st[2]) if bd else None
        ls = LogState(lp) if lp else None
        if ls:
            ls.update()
        out.append({"pid": pid, "target": short_target(proc_argv(pid)),
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
    try:
        while True:
            try:
                doc = json.loads((state_dir() / "builds.json").read_text())
            except (OSError, ValueError):
                doc = {"builds": [], "updated": 0}
            ws, tabs = pm.labels()
            rows = doc["builds"]
            out = ["\033[H\033[2J\033[1m BitBake builds\033[0m"
                   + ("" if running_pid() else "   \033[31m(monitor not running)\033[0m"), ""]
            if not rows:
                out.append("  no bitbake builds running")
            for i, r in enumerate(rows, 1):
                where = "outside herdr"
                if r["pane_id"]:
                    where = (f"{ws.get(r['workspace_id'], r['workspace_id'])} › "
                             f"tab {tabs.get(r['tab_id'], r['tab_id'])} › "
                             f"{r['pane_label'] or r['pane_id']}")
                if r.get("via_popup"):
                    where += " (herdbake popup)"
                who = f"\033[35m{r['agent']}\033[0m" if r["agent"] else "\033[36muser\033[0m"
                state = ("\033[31m⏸\033[0m" if r.get("waiting") else "\033[33m●\033[0m") if r["running"] else (
                    "\033[32m✔\033[0m" if (r["status"] or "").startswith("✔") else "\033[31m✖\033[0m")
                age = _ago(r["started"]) if r["running"] else f"ended {_ago(r['ended'])} ago"
                out.append(f" {state} [{i}] \033[1m{r['target']}\033[0m  ({who})  {age}")
                out.append(f"       {r['status']}")
                out.append(f"       {where}  ·  {r['builddir']}")
                if r["errors"] or r["warnings"]:
                    out.append(f"       {r['errors']} errors, {r['warnings']} warnings")
                out.append("")
            out.append("\033[2m 1-9 jump to pane · l open log · s start/stop monitor · "
                       "b add sidebar row · q close\033[0m")
            if flash:
                out.append(f"\033[33m {flash}\033[0m")
                flash = ""
            sys.stdout.write("\n".join(out))
            sys.stdout.flush()
            if select.select([sys.stdin], [], [], 1.0)[0]:
                ch = sys.stdin.read(1)
                if ch in ("q", "\x1b"):
                    return
                if ch == "s":
                    if running_pid():
                        cmd_stop()
                        flash = "monitor stopped"
                    else:
                        cmd_ensure_daemon()
                        flash = "monitor started"
                    continue
                if ch == "b":
                    import contextlib
                    import io
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        cmd_setup_sidebar()
                    flash = buf.getvalue().strip().splitlines()[0] if buf.getvalue().strip() else "done"
                    continue
                if ch.isdigit() and 1 <= int(ch) <= len(rows):
                    r = rows[int(ch) - 1]
                    if r["pane_id"]:
                        try:
                            call("pane.focus", {"pane_id": r["pane_id"]})
                        except Exception as e:
                            print(f"\n focus failed: {e}")
                            time.sleep(2)
                        return
                if ch == "l":
                    sys.stdout.write("\n log of build #: ")
                    sys.stdout.flush()
                    n = sys.stdin.read(1)
                    if n.isdigit() and 1 <= int(n) <= len(rows) and rows[int(n) - 1]["log"]:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old)
                        subprocess.run(["less", "+G", rows[int(n) - 1]["log"]])
                        tty.setcbreak(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# --------------------------------------------------------------------------
# sidebar setup: append ["$hb_build"] row to [ui.sidebar.agents]
# --------------------------------------------------------------------------
def cmd_setup_sidebar(quiet=False):
    cfg = pathlib.Path(os.environ.get("HERDR_CONFIG_PATH")
                       or pathlib.Path.home() / ".config/herdr/config.toml")
    text = cfg.read_text() if cfg.exists() else ""
    (state_dir() / "sidebar-installed").touch()
    if '"$hb_build"' in text:
        if not quiet:
            print("sidebar already has $hb_build")
        return
    row = '  [{ token = "$hb_build", fg = "#f9e2af" }],\n'
    m = re.search(r"^\[ui\.sidebar\.agents\]\s*\n(?:.*\n)*?rows\s*=\s*\[\n", text, re.M)
    if m:
        new = text[:m.end()] + text[m.end():].replace(
            '  ["workspace", "tab"],', row + '  ["workspace", "tab"],', 1) \
            if '  ["workspace", "tab"],' in text[m.end():] else None
        if new is None:
            print("could not find a place in [ui.sidebar.agents].rows; add this row manually:\n" + row)
            return
    else:
        new = text.rstrip("\n") + ("\n\n" if text else "") + (
            "[ui.sidebar.agents]\nrows = [\n"
            '  ["state_icon", "agent"],\n' + row + '  ["workspace", "tab"],\n]\n')
    bak = cfg.with_suffix(f".toml.bak-herdbake-{time.strftime('%Y%m%d%H%M%S')}")
    bak.write_text(text)
    cfg.write_text(new)
    chk = subprocess.run([HERDR, "config", "check"], capture_output=True, text=True)
    if chk.returncode != 0 or "ok" not in chk.stdout:
        cfg.write_text(text)
        print("config check failed, restored original:\n" + chk.stdout + chk.stderr)
        return
    subprocess.run([HERDR, "server", "reload-config"], capture_output=True)
    print(f"added $hb_build row (backup: {bak})")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {
        "daemon": cmd_daemon, "ensure-daemon": cmd_ensure_daemon, "stop": cmd_stop,
        "scan": cmd_scan, "open-list": cmd_open_list, "list-tui": cmd_list_tui,
        "setup-sidebar": cmd_setup_sidebar,
    }[cmd]()


if __name__ == "__main__":
    main()
