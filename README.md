# herdbake

A [Herdr](https://herdr.dev) plugin that routes bitbake's (Yocto /
OpenEmbedded) `menuconfig`, `devshell`, and `ccmake` terminal spawns
into compact Herdr popup windows instead of an external terminal window.

## How it works

bitbake's `Custom` terminal class (`meta/lib/oe/terminal.py`) is its
highest-priority terminal type: if `OE_TERMINAL_CUSTOMCMD` is set, it
always wins over every GUI terminal and over tmux, and execs that
command with `{title}` and `{command}` substituted for every task
that opens an interactive terminal (menuconfig, devshell, ccmake,
...).

The **Herdbake: set up BitBake terminal routing** action finds the
active build directory's `conf/local.conf`, shows its exact absolute
path for confirmation, and adds an idempotent managed block that sets
`OE_TERMINAL=custom` and
`OE_TERMINAL_CUSTOMCMD=<path-to-route.py> --title="{title}" {command}`.
Because these are BitBake configuration values rather than shell
environment variables, they apply equally to commands launched from a
regular shell or an already-running agent. Nothing is typed into a
terminal pane.

When bitbake calls `OE_TERMINAL_CUSTOMCMD`, it lands on
`scripts/route.py`, which matches the task's title against a
configurable ruleset and opens a compact Herdr popup running the actual
command - instead of letting bitbake open a real terminal window.

Only the marked herdbake block in the selected `conf/local.conf` is
managed; existing settings are preserved. Panes are otherwise
completely normal, and nothing is faked or shadowed on `PATH`, so real
tmux usage (if you have it) is unaffected.

## Requirements

- Herdr >= 0.9.0
- Python >= 3.11 (for `tomllib`; without it herdbake still works with
  its built-in defaults, just without a custom config file)
- Linux or macOS

## Install

```bash
herdr plugin install EdTheBearded/herdbake
```

For local development instead, clone the repo and link your working
copy:

```bash
git clone https://github.com/EdTheBearded/herdbake ~/herdbake
herdr plugin link ~/herdbake
```

From a pane at or below the Yocto build directory, run:

```bash
herdr plugin action invoke herdbake.setup
```

Herdbake finds the nearest parent `conf/local.conf` (or
`$BUILDDIR/conf/local.conf` when available), then asks before changing
it. If no such file can be found, it makes no change.

## Usage

After setup, run bitbake as usual from any shell or agent pane:

```bash
bitbake virtual/kernel -c menuconfig
bitbake torizon-docker -c devshell
```

The terminal spawn is routed into a compact Herdr popup per the ruleset below,
instead of opening an external terminal window.

## Commands and shortcuts

The command center (`prefix+alt+m`) is a small modal popup that shows these
commands and the bindings active in your Herdr configuration. Every command is
also available from Herdr's command palette or as
`herdr plugin action invoke herdbake.<command>`.

| Shortcut | Command | Use it to |
| --- | --- | --- |
| `prefix+h` | `setup` | find the active build's `local.conf` and enable terminal routing after confirmation |
| `prefix+alt+d` | `doctor` | check routing, build discovery, and required tools without changing anything |
| `prefix+alt+f` | `failure-console` | open the newest task failure log in a scrollable viewer |
| `prefix+alt+l` | `layers` | inspect configured layers, overlays, appends, and cross-depends in a scrollable report |
| `prefix+alt+shift+h` | `health` | review local build directory, filesystem, and recent activity without running BitBake |
| `prefix+alt+shift+g` | `graph` | generate a confirmed BitBake dependency graph |
| `prefix+alt+shift+f` | `logs` | open the newest cooker or task log in a scrollable viewer |
| `prefix+alt+shift+k` | `kernel` | run kernel `menuconfig`, then optionally and separately confirm `savedefconfig` |
| `prefix+alt+m` | `help` | open the modal command center and see your live shortcuts |

The table shows the recommended non-conflicting bindings for Herdr's standard
`prefix` leader. You can change them in Herdr's `config.toml`; the command
center reads that file at display time and shows your actual bindings.

### Build tools

All build tools use the build associated with the focused pane. They open a
new compact popup and never type into, reuse, or modify that pane. Popup
processes do not inherit the focused shell's Yocto environment; when `bitbake`
is absent from `PATH`, Herdbake reads the build's `conf/bblayers.conf`, finds
the nearest `oe-init-build-env`, and sources it only for that popup process.
Herdbake action launches also use BitBake's temporary post-configuration
mechanism, so their interactive terminals route into Herdr even if this build
has not yet been configured with the Setup action.

**Command reference** opens as a centered, warm-toned popup command center,
grouping every registered Herdbake action and showing its currently configured
shortcut. It reads the active Herdr configuration at display time, so custom
bindings appear automatically.

- **Diagnose active build** checks the managed `local.conf` block, router
  syntax, and required tools. It does not modify configuration.
- **Open latest task log**, **inspect layers**, and **navigate latest build
  log** open complete reports in a scrollable pager. Herdbake uses `bat` (or
  Debian/Ubuntu's `batcat`) with `less` when both are installed; otherwise it
  recommends `bat` and falls back to `less`, then `more`. Use arrows or Page
  Up/Page Down to scroll and `q` to return. Failure and general logs start at
  their first `ERROR` when supported.
- **Inspect layers** runs `bitbake-layers show-layers`, `show-overlayed`,
  `show-appends`, and `show-cross-depends` in one pane.
- **Show build health** reports filesystem capacity, local build-directory
  sizes, and the latest cooker/buildstats activity without running BitBake.
- **Generate dependency graph** runs `bitbake -g <target>` only after `yes`
  confirmation. It writes or replaces `pn-buildlist`, `pn-depends.dot`, and
  `task-depends.dot` in the build directory, then renders available `.dot`
  files as SVG when Graphviz is installed. SVG rendering is capped at 10
  seconds, so very large task graphs remain usable as `.dot` files instead of
  holding the popup open indefinitely.
- **Kernel configuration workflow** runs `menuconfig` for an explicit kernel
  target, then offers an explicit, separately confirmed `savedefconfig` and
  lists generated `defconfig` files. Interactive terminal panes remain open
  after their commands finish until dismissed.

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

[[rule]]
name = "devshell"
match = "OpenEmbedded Developer*Shell"
placement = "popup"

[[rule]]
name = "ccmake"
match = "* - ccmake"
placement = "popup"

[default]                # anything unmatched falls back here
placement = "popup"
```

All bundled actions and routed terminals use the same compact popup. An
override can still use `placement = "split"` or `placement = "tab"`
deliberately when a persistent terminal layout is more useful.

Adding support for a new task or recipe is just one more `[[rule]]`
block - no code changes required. Config changes take effect on the
next bitbake invocation; no reload needed.

## Status

Early (v0.4) - terminal routing and the read-only build tools have been
tested against a Yocto/Torizon OS build tree. Not yet run across a wide range
of Yocto releases. Feedback and issues welcome via
[GitHub Issues](https://github.com/EdTheBearded/herdbake/issues).

## License

MIT - see [LICENSE](LICENSE).
