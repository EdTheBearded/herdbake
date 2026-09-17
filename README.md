# herdbake

A [Herdr](https://herdr.dev) plugin that routes bitbake's (Yocto /
OpenEmbedded) `menuconfig`, `devshell`, and `ccmake` terminal spawns
into Herdr panes - popup, split, or tab - instead of an external
terminal window, automatically, in any pane you're already using.

## How it works

bitbake's `Custom` terminal class (`meta/lib/oe/terminal.py`) is its
highest-priority terminal type: if `OE_TERMINAL_CUSTOMCMD` is set, it
always wins over every GUI terminal and over tmux, and execs that
command with `{title}` and `{command}` substituted for every task
that opens an interactive terminal (menuconfig, devshell, ccmake,
...).

herdbake auto-exports `OE_TERMINAL=custom` and
`OE_TERMINAL_CUSTOMCMD=<path-to-route.py> "{title}" {command}` into
every eligible pane:

- once at Herdr startup, for all existing panes, and
- on every new pane as it's created (via the `pane.created` event
  hook),

but only into panes whose foreground is a bare, idle shell with
nothing typed or running - never into a pane running an agent, editor,
or other program, so nothing gets an unexpected line of input.
herdbake also extends `BB_ENV_PASSTHROUGH_ADDITIONS`, since bitbake
only pulls a fixed allowlist of variables from the shell into its
datastore and `OE_TERMINAL*` aren't in it by default.

When bitbake calls `OE_TERMINAL_CUSTOMCMD`, it lands on
`scripts/route.py`, which matches the task's title against a
configurable ruleset and opens the matching Herdr placement (popup /
split / tab) running the actual command - instead of letting bitbake
open a real terminal window.

No changes are made to bitbake, `local.conf`, or any shell rc file -
everything is injected at runtime by the plugin. Panes are otherwise
completely normal; nothing is faked or shadowed on `PATH`, so real
tmux usage (if you have it) is unaffected.

## Requirements

- Herdr >= 0.9.0
- Python >= 3.11 (for `tomllib`; without it herdbake still works with
  its built-in defaults, just without a custom config file)
- Linux or macOS

## Install

```bash
herdr plugin link ~/herdbake
```

Auto-injection starts on the next Herdr server start (via the
`[[startup]]` hook) and for every pane created afterward. To activate
it immediately in already-open panes without restarting Herdr, run:

```bash
herdr plugin action invoke herdbake.status   # check current state
```

(the toggle action below also re-injects into existing panes when
turned back on).

## Usage

Just run bitbake as usual, in any regular shell pane:

```bash
bitbake virtual/kernel -c menuconfig
bitbake torizon-docker -c devshell
```

The terminal spawn is routed into a Herdr popup/split/tab per the
ruleset below, instead of opening an external terminal window.

## Controls

```bash
herdr plugin action invoke herdbake.status   # show enabled/disabled + active config path
herdr plugin action invoke herdbake.toggle   # turn auto-injection on/off
```

Disabling stops new panes from being injected; panes already injected
keep working until closed.

## Configuration

Default rules ship in `config/bitbake-terminal.default.toml`. To
customize, copy it to the plugin's config directory as
`bitbake-terminal.toml`:

```bash
cp config/bitbake-terminal.default.toml "$(herdr plugin config-dir herdbake)/bitbake-terminal.toml"
```

Each rule matches a task's window title (a shell glob, not a regex)
and picks a placement:

```toml
[[rule]]
name = "menuconfig"
match = "* Configuration"
placement = "popup"
width = "80%"
height = "80%"

[[rule]]
name = "devshell"
match = "OpenEmbedded Developer*Shell"
placement = "split"
direction = "down"      # used when prompt_direction = false

[[rule]]
name = "ccmake"
match = "* - ccmake"
placement = "split"
direction = "right"     # fallback if the prompt below times out
prompt_direction = true
prompt_timeout_ms = 15000

[default]                # anything unmatched falls back here
placement = "split"
direction = "down"
```

Set `prompt_direction = true` on any `placement = "split"` rule to be
asked, at the moment that task's terminal opens, whether to split
horizontally or vertically; `direction` is used if the prompt times
out.

Adding support for a new task or recipe is just one more `[[rule]]`
block - no code changes required. Config changes take effect on the
next bitbake invocation; no reload needed.

## Status

Early (v0.2) - verified end-to-end against a real Yocto/Torizon OS build
tree: `bitbake -c menuconfig` opens a live `mconf` session inside a
Herdr popup and completes normally when closed. Not yet run across a
wide range of Yocto releases. Feedback and issues welcome.

## License

MIT - see [LICENSE](LICENSE).
