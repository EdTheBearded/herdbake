#!/usr/bin/env python3
"""herdbake route command - the value of OE_TERMINAL_CUSTOMCMD.

bitbake's terminal.bbclass Custom terminal class (the highest-priority
terminal type it tries, see meta/lib/oe/terminal.py) execs
`OE_TERMINAL_CUSTOMCMD` with `{title}` and `{command}` substituted, for
every task that opens an interactive terminal (menuconfig, devshell,
ccmake, ...). This script receives that call directly - no faking of
tmux or any other terminal binary, and no changes to bitbake, local.conf,
or any shell rc file.

It matches the title against a configurable ruleset and opens the
matching Herdr placement (popup / split / tab) running the actual
command, instead of letting bitbake open a real terminal window.
"""
import fnmatch
import os
import subprocess
import sys
import tempfile
import time

try:
    import tomllib
except ImportError:  # Python < 3.11: built-in defaults still work
    tomllib = None

DEFAULT_RULES = [
    {
        "name": "menuconfig",
        "match": "* Configuration",
        "placement": "popup",
        "width": "80%",
        "height": "80%",
    },
    {
        "name": "devshell",
        "match": "OpenEmbedded Developer*Shell",
        "placement": "split",
        "direction": "down",
    },
]
DEFAULT_FALLBACK = {"placement": "split", "direction": "down"}


def load_config():
    rules = list(DEFAULT_RULES)
    fallback = dict(DEFAULT_FALLBACK)

    candidates = []
    config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if config_dir:
        candidates.append(os.path.join(config_dir, "bitbake-terminal.toml"))
    plugin_root = os.environ.get("HERDR_PLUGIN_ROOT")
    if plugin_root:
        candidates.append(
            os.path.join(plugin_root, "config", "bitbake-terminal.default.toml")
        )

    if tomllib is not None:
        for path in candidates:
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    data = tomllib.load(f)
                if "rule" in data:
                    rules = data["rule"]
                if "default" in data:
                    fallback = data["default"]
                break

    return rules, fallback


def resolve_rule(title, rules, fallback):
    for rule in rules:
        pattern = rule.get("match", "*")
        if fnmatch.fnmatchcase(title, pattern):
            return rule
    merged = {"name": "default"}
    merged.update(fallback)
    return merged


def herdr_bin():
    return os.environ.get("HERDR_BIN_PATH", "herdr")


def prompt_direction(title, fallback_direction, timeout_ms):
    fd, choice_path = tempfile.mkstemp(prefix="herdbake-choice-")
    os.close(fd)
    try:
        subprocess.run(
            [
                herdr_bin(), "plugin", "pane", "open",
                "--plugin", "herdbake",
                "--entrypoint", "chooser",
                "--placement", "popup",
                "--env", "HERDBAKE_CHOICE_FILE=%s" % choice_path,
                "--env", "HERDBAKE_TITLE=%s" % title,
            ],
            check=True,
        )
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            with open(choice_path) as f:
                content = f.read().strip()
            if content:
                return content
            time.sleep(0.1)
        return fallback_direction
    finally:
        try:
            os.unlink(choice_path)
        except OSError:
            pass


def open_pane(title, command, rule):
    placement = rule.get("placement", "split")
    args = [
        herdr_bin(), "plugin", "pane", "open",
        "--plugin", "herdbake",
        "--entrypoint", "terminal",
        "--placement", placement,
        "--env", "HERDBAKE_CMD=%s" % command,
        "--cwd", os.getcwd(),
        "--focus",
    ]

    if placement == "split":
        direction = rule.get("direction", "down")
        if rule.get("prompt_direction"):
            direction = prompt_direction(
                title, direction, int(rule.get("prompt_timeout_ms", 15000))
            )
        args += ["--direction", direction]

    width = rule.get("width")
    height = rule.get("height")
    if width:
        args += ["--width", str(width)]
    if height:
        args += ["--height", str(height)]

    subprocess.run(args, check=True)


def main():
    if len(sys.argv) < 3:
        sys.stderr.write(
            "usage: route.sh <title> <command...>\n"
            "(this is meant to be invoked by bitbake via "
            "OE_TERMINAL_CUSTOMCMD, not run directly)\n"
        )
        sys.exit(2)

    title = sys.argv[1]
    command = " ".join(sys.argv[2:])

    rules, fallback = load_config()
    rule = resolve_rule(title, rules, fallback)
    open_pane(title, command, rule)


if __name__ == "__main__":
    main()
