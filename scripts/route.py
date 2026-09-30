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
command, instead of letting bitbake open a real terminal window. The
setup action places this command in the build's conf/local.conf, so it
does not need to type into an existing terminal pane.
"""
import fnmatch
import os
import shlex
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
    },
    {
        "name": "devshell",
        "match": "OpenEmbedded Developer*Shell",
        "placement": "popup",
    },
]
DEFAULT_FALLBACK = {"placement": "popup"}


def plugin_root():
    return os.environ.get(
        "HERDR_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def build_dir():
    """Return the build directory for a BitBake-spawned terminal command."""
    candidates = [os.environ.get("BUILDDIR"), os.getcwd()]
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(candidate, "conf", "local.conf")):
            return candidate
    return os.getcwd()


def load_config():
    rules = list(DEFAULT_RULES)
    fallback = dict(DEFAULT_FALLBACK)

    candidates = []
    config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not config_dir:
        # local.conf launches route.py outside the plugin hook process, so
        # discover the user config directory through Herdr in that mode.
        try:
            result = subprocess.run(
                [herdr_bin(), "plugin", "config-dir", "herdbake"],
                check=True,
                capture_output=True,
                text=True,
            )
            config_dir = result.stdout.strip() or None
        except (OSError, subprocess.CalledProcessError):
            config_dir = None
    if config_dir:
        candidates.append(os.path.join(config_dir, "bitbake-terminal.toml"))
    root = plugin_root()
    candidates.append(
        os.path.join(root, "config", "bitbake-terminal.default.toml")
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
    placement = rule.get("placement", "popup")
    root = plugin_root()
    wrapped_command = shlex.join(
        [
            "bash",
            os.path.join(root, "scripts", "with-build-env.sh"),
            build_dir(),
            "/bin/sh",
            "-c",
            command,
        ]
    )
    args = [
        herdr_bin(), "plugin", "pane", "open",
        "--plugin", "herdbake",
        "--entrypoint", "terminal",
        "--env", "HERDBAKE_CMD=%s" % wrapped_command,
        # Keep completed interactive terminals visible until the user dismisses
        # them, so menuconfig output and follow-up controls do not vanish.
        "--env", "HERDBAKE_KEEP_OPEN=1",
        "--env", "HERDR_PLUGIN_ROOT=%s" % root,
        "--cwd", build_dir(),
        "--focus",
    ]

    # Omitting placement lets the terminal entrypoint's compact popup defaults
    # apply. Explicit split/tab rules remain available to users who override
    # the bundled configuration intentionally.
    if placement != "popup":
        args += ["--placement", placement]

    if placement == "split":
        direction = rule.get("direction", "down")
        if rule.get("prompt_direction"):
            direction = prompt_direction(
                title, direction, int(rule.get("prompt_timeout_ms", 15000))
            )
        args += ["--direction", direction]

    subprocess.run(args, check=True)


def main():
    if len(sys.argv) < 3:
        sys.stderr.write(
            "usage: route.sh <title> <command...>\n"
            "(this is meant to be invoked by bitbake via "
            "OE_TERMINAL_CUSTOMCMD, not run directly)\n"
        )
        sys.exit(2)

    if sys.argv[1].startswith("--title="):
        title = sys.argv[1].removeprefix("--title=")
        command_args = sys.argv[2:]
    else:
        # Keep configured builds from older Herdbake versions working until
        # their owner next runs setup. The current setup format uses --title
        # so BitBake can safely format titles that contain spaces.
        title = sys.argv[1]
        command_args = sys.argv[2:]
    # BitBake parses the custom-terminal command before invoking us. Rebuild
    # its remaining command arguments as a shell-safe command line for
    # pane-entry.sh; a plain space join would lose embedded quoting.
    command = shlex.join(command_args)

    rules, fallback = load_config()
    rule = resolve_rule(title, rules, fallback)
    open_pane(title, command, rule)


if __name__ == "__main__":
    main()
