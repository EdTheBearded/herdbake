#!/usr/bin/env python3
"""Display registered Herdbake actions alongside their active Herdr shortcuts."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import textwrap
from pathlib import Path

import tomllib


GROUPS = (
    ("GET STARTED", ("help", "setup", "doctor")),
    ("DEBUG & INSPECT", ("logs", "failure-console", "layers")),
    ("BUILD WORKFLOWS", ("kernel", "graph")),
    ("BUILD HEALTH", ("health",)),
)


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def apply(self, codes: str, text: str) -> str:
        return f"\033[{codes}m{text}\033[0m" if self.enabled else text

    def accent(self, text: str) -> str:
        return self.apply("38;5;215;1", text)

    def muted(self, text: str) -> str:
        return self.apply("38;5;245", text)

    def badge(self, text: str) -> str:
        return self.apply("48;5;236;38;5;223;1", f" {text} ")


def action_shortcuts(config_path: Path) -> dict[str, list[str]]:
    if not config_path.is_file():
        return {}
    try:
        with config_path.open("rb") as stream:
            commands = tomllib.load(stream).get("keys", {}).get("command", [])
    except (OSError, tomllib.TOMLDecodeError):
        return {}

    shortcuts: dict[str, list[str]] = {}
    for item in commands:
        action = item.get("command")
        key = item.get("key")
        if isinstance(action, str) and action.startswith("herdbake.") and isinstance(key, str):
            shortcuts.setdefault(action, []).append(key)
    return shortcuts


def grouped_actions(actions: list[dict[str, object]]) -> list[tuple[str, list[dict[str, object]]]]:
    by_id = {str(action["id"]): action for action in actions}
    result = []
    for title, identifiers in GROUPS:
        group = [by_id[identifier] for identifier in identifiers if identifier in by_id]
        if group:
            result.append((title, group))
    known = {identifier for _, identifiers in GROUPS for identifier in identifiers}
    remaining = [action for action in actions if str(action["id"]) not in known]
    if remaining:
        result.append(("OTHER", remaining))
    return result


def print_reference(actions: list[dict[str, object]], shortcuts: dict[str, list[str]]) -> None:
    style = Style(sys.stdout.isatty())
    columns = max(72, min(shutil.get_terminal_size(fallback=(92, 40)).columns, 108))
    rule = "─" * (columns - 2)

    print(style.accent(f"╭{rule}╮"))
    print(style.accent("│") + style.accent("  HERDBAKE  /  COMMAND CENTER".ljust(columns - 2)) + style.accent("│"))
    subtitle = f"  {len(actions)} registered actions · shortcuts from active Herdr config"
    print(style.accent("│") + style.muted(subtitle.ljust(columns - 2)) + style.accent("│"))
    print(style.accent(f"╰{rule}╯"))

    shortcut_width = min(22, max(16, columns // 4))
    action_width = max(24, min(28, columns // 3))
    description_width = columns - shortcut_width - action_width - 8
    for group, entries in grouped_actions(actions):
        print()
        print(style.accent(f"◆ {group}"))
        for action in entries:
            action_id = str(action["id"])
            command = f"herdbake.{action_id}"
            key = ", ".join(shortcuts.get(command, [])) or "unbound"
            short_key = textwrap.shorten(key, width=shortcut_width - 2, placeholder="…")
            label = str(action["title"]).removeprefix("Herdbake: ")
            description = textwrap.shorten(label, width=description_width, placeholder="…")
            badge = style.badge(short_key) + " " * max(0, shortcut_width - len(short_key) - 2)
            print(
                f"  {badge}"
                f" {style.muted(command.ljust(action_width))} {description}"
            )

    print()
    print(style.muted("  Enter closes this overlay  ·  Unbound actions remain available in the command palette."))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("HERDR_CONFIG_PATH", "~/.config/herdr/config.toml")).expanduser())
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()

    manifest = args.plugin_root / "herdr-plugin.toml"
    with manifest.open("rb") as stream:
        actions = tomllib.load(stream).get("actions", [])
    shortcuts = action_shortcuts(args.config)

    print_reference(actions, shortcuts)
    if not args.no_wait:
        try:
            input()
        except EOFError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
