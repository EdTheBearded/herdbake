#!/usr/bin/env bash
# Regression test: retain BitBake's quoted `sh -c` terminal command.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - "$ROOT" <<'PY'
import shlex
import subprocess
import sys

sys.path.insert(0, sys.argv[1] + "/scripts")
import route

argv = [
    "sh",
    "-c",
    'printf "%s\\n" "menuconfig command preserved"',
]
captured = {}
real_open_pane = route.open_pane
route.load_config = lambda: ([], {})
route.resolve_rule = lambda title, rules, fallback: {}
route.open_pane = lambda title, command, rule: captured.update(
    title=title, command=command, rule=rule
)
route.sys.argv = ["route.py", "--title=linux-syna Configuration", *argv]
route.main()

assert captured["title"] == "linux-syna Configuration"
command = captured["command"]
result = subprocess.run(
    ["/bin/sh", "-c", command],
    check=True,
    capture_output=True,
    text=True,
)

assert result.stdout == "menuconfig command preserved\n", result.stdout

# BitBake calls shlex.split() after expanding its custom-terminal format.
# The managed command must therefore keep a title with spaces in one argument
# without putting literal quote characters into it.
formatted = 'python3 /tmp/route.py --title="{title}" {command}'.format(
    title="linux-syna Configuration", command="wrapper --flag"
)
assert shlex.split(formatted)[2] == "--title=linux-syna Configuration"

captured_args = {}
route.subprocess.run = lambda args, check: captured_args.setdefault("args", args)
real_open_pane("Configuration", "make menuconfig", {"placement": "popup"})
assert "HERDBAKE_KEEP_OPEN=1" in captured_args["args"]
command_env = next(arg for arg in captured_args["args"] if arg.startswith("HERDBAKE_CMD="))
assert "scripts/with-build-env.sh" in command_env
PY

echo 'ok: route command quoting'
