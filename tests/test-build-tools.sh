#!/usr/bin/env bash
# Focused tests for the non-mutating build inspection helpers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT

build_dir="$TMPDIR_TEST/build"
mkdir -p "$build_dir/conf" "$build_dir/tmp/work/example/temp"
printf 'MACHINE = "qemuarm"\n' > "$build_dir/conf/local.conf"
"$ROOT/scripts/setup-local-conf.sh" --apply --file "$build_dir/conf/local.conf" >/dev/null

# Popup panes are not descendants of the developer's shell.  Verify that the
# shared launcher can recover the environment from an absolute BBLAYERS path.
fake_poky="$TMPDIR_TEST/layers/poky"
mkdir -p "$fake_poky/meta"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'export HERDBAKE_TEST_OE_INIT=1' \
  'cd "$1"' > "$fake_poky/oe-init-build-env"
printf 'BBLAYERS ?= " %s/meta "\n' "$fake_poky" > "$build_dir/conf/bblayers.conf"
bootstrap_output="$(HERDR_PLUGIN_ROOT="$ROOT" bash "$ROOT/scripts/with-build-env.sh" "$build_dir" bash -c 'printf "%s:%s:%s\\n" "$HERDBAKE_TEST_OE_INIT" "$OE_TERMINAL" "$PWD"')"
[[ "$bootstrap_output" == "1:custom:$build_dir" ]]
route_command="$(HERDR_PLUGIN_ROOT="$ROOT" bash "$ROOT/scripts/with-build-env.sh" "$build_dir" bash -c 'printf %s "$OE_TERMINAL_CUSTOMCMD"')"
[[ "$route_command" == *'/scripts/route.py --title="{title}" {command}'* ]]
postconf_output="$(HERDR_PLUGIN_ROOT="$ROOT" bash "$ROOT/scripts/with-build-env.sh" "$build_dir" bash -c 'grep -F "OE_TERMINAL = \"custom\"" "$BBPOSTCONF"')"
[[ "$postconf_output" == 'OE_TERMINAL = "custom"' ]]

log="$build_dir/tmp/work/example/temp/log.do_compile.1"
run="$build_dir/tmp/work/example/temp/run.do_compile.1"
printf 'first line\nERROR: compiler failed\nlast line\n' > "$log"
printf '#!/bin/sh\nfalse\n' > "$run"

failure_output="$(python3 "$ROOT/scripts/failure-console.py" --build-dir "$build_dir")"
[[ "$failure_output" == *'ERROR: compiler failed'* ]]
[[ "$failure_output" == *"$run"* ]]
[[ "$(python3 "$ROOT/scripts/failure-console.py" --build-dir "$build_dir" --path-only)" == "$log" ]]

choice="$TMPDIR_TEST/choice"
printf 'core-image-minimal\nyes\n' | \
  HERDBAKE_PROMPT_FILE="$choice" HERDBAKE_PROMPT_MODE=graph \
  bash "$ROOT/scripts/prompt.sh" >/dev/null
[[ "$(sed -n '1p' "$choice")" = 'core-image-minimal' ]]
[[ "$(sed -n '2p' "$choice")" = 'yes' ]]

printf '\n' | HERDBAKE_PROMPT_FILE="$choice" HERDBAKE_PROMPT_MODE=kernel \
  bash "$ROOT/scripts/prompt.sh" >/dev/null
[[ "$(sed -n '1p' "$choice")" = 'virtual/kernel' ]]

mkdir -p "$build_dir/tmp/log/cooker" "$build_dir/tmp/buildstats/run"
printf 'cooker log\n' > "$build_dir/tmp/log/cooker/latest.log"
printf 'buildstats\n' > "$build_dir/tmp/buildstats/run/stats"
printf 'CONFIG_TEST=y\n' > "$build_dir/tmp/work/example/defconfig"
health_output="$(python3 "$ROOT/scripts/build-health.py" --build-dir "$build_dir")"
[[ "$health_output" == *'Latest cooker log:'* ]]
[[ "$health_output" == *'This report did not run BitBake or change build state.'* ]]

python3 - "$ROOT" "$build_dir" <<'PY'
import importlib.util
import sys
from pathlib import Path

path = sys.argv[1] + "/scripts/log-navigator.py"
spec = importlib.util.spec_from_file_location("log_navigator", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
assert module.latest_log(Path(sys.argv[2])).name == "latest.log"

path = sys.argv[1] + "/scripts/kernel-workflow.py"
spec = importlib.util.spec_from_file_location("kernel_workflow", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
defconfig = Path(sys.argv[2]) / "tmp/work/example/defconfig"
assert defconfig in list((Path(sys.argv[2]) / "tmp/work").glob("**/defconfig"))

path = sys.argv[1] + "/scripts/dependency-graph.py"
spec = importlib.util.spec_from_file_location("dependency_graph", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
original_run = module.subprocess.run
module.subprocess.run = lambda *args, **kwargs: (_ for _ in ()).throw(
    module.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
)
assert not module.render_graph("dot", Path("graph.dot"), Path("graph.svg"))
module.subprocess.run = original_run
PY

config="$TMPDIR_TEST/herdr-config.toml"
printf '%s\n' \
  '[[keys.command]]' \
  'key = "prefix+alt+m"' \
  'type = "plugin_action"' \
  'command = "herdbake.help"' > "$config"
help_output="$(python3 "$ROOT/scripts/help.py" --plugin-root "$ROOT" --config "$config" --no-wait)"
[[ "$help_output" == *'prefix+alt+m'* ]]
[[ "$help_output" == *'herdbake.help'* ]]
[[ "$help_output" != *'herdbake.variable'* ]]

pager_bin="$TMPDIR_TEST/pager-bin"
mkdir -p "$pager_bin"
printf '%s\n' \
  '#!/bin/sh' \
  'last=""' \
  'for argument in "$@"; do last="$argument"; done' \
  'printf "fake bat: %s\\n" "$*"' \
  'cat "$last"' > "$pager_bin/bat"
chmod +x "$pager_bin/bat"
pager_output="$(PATH="$pager_bin:$PATH" /bin/bash "$ROOT/scripts/pager.sh" "$log" ERROR)"
[[ "$pager_output" == *'bat highlighting'* ]]
[[ "$pager_output" == *'fake bat:'* ]]

fallback_bin="$TMPDIR_TEST/fallback-bin"
mkdir -p "$fallback_bin"
printf '%s\n' \
  '#!/bin/sh' \
  'printf "fake less: %s\\n" "$*"' > "$fallback_bin/less"
chmod +x "$fallback_bin/less"
fallback_output="$(PATH="$fallback_bin" /bin/bash "$ROOT/scripts/pager.sh" "$log" ERROR)"
[[ "$fallback_output" == *'install bat'* ]]
[[ "$fallback_output" == *'fake less:'* ]]

printf '%s\n' \
  '#!/bin/sh' \
  'last=""' \
  'for argument in "$@"; do last="$argument"; done' \
  'printf "fake batcat: %s\\n" "$*"' > "$fallback_bin/batcat"
chmod +x "$fallback_bin/batcat"
batcat_output="$(PATH="$fallback_bin" /bin/bash "$ROOT/scripts/pager.sh" "$log" ERROR)"
[[ "$batcat_output" == *'bat highlighting'* ]]
[[ "$batcat_output" == *'fake batcat:'* ]]

printf '%s\n' \
  '#!/bin/sh' \
  'printf "report from %s\\n" "$1"' > "$pager_bin/bitbake-layers"
chmod +x "$pager_bin/bitbake-layers"
layer_output="$(PATH="$pager_bin:$PATH" python3 "$ROOT/scripts/layer-inspector.py" --build-dir "$build_dir")"
[[ "$layer_output" == *'bitbake-layers show-layers'* ]]
[[ "$layer_output" == *'report from show-cross-depends'* ]]

log_output="$(PATH="$pager_bin:$PATH" HERDR_PLUGIN_ROOT="$ROOT" /bin/bash "$ROOT/scripts/log-navigator.sh" "$build_dir")"
[[ "$log_output" == *'fake bat:'* ]]
[[ "$log_output" == *'latest.log'* ]]

bin_dir="$TMPDIR_TEST/bin"
mkdir -p "$bin_dir"
for command in herdr bitbake bitbake-layers; do
  ln -s /usr/bin/true "$bin_dir/$command"
done
doctor_output="$(PATH="$bin_dir:$PATH" python3 "$ROOT/scripts/doctor.py" --build-dir "$build_dir" --plugin-root "$ROOT")"
[[ "$doctor_output" == *'Ready. Interactive BitBake tasks should route into Herdr panes.'* ]]

python3 - "$ROOT" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
with (root / 'herdr-plugin.toml').open('rb') as manifest:
    data = tomllib.load(manifest)
panes = {pane['id']: pane for pane in data['panes']}
assert panes and all(pane['placement'] == 'popup' for pane in panes.values())
assert {action['id'] for action in data['actions']} == {
    'setup', 'help', 'doctor', 'failure-console', 'layers', 'health', 'graph',
    'logs', 'kernel',
}

# Registered actions only open declared popup entrypoints or delegate to the
# shared popup-terminal helper. This prevents a new action from introducing a
# split/tab panel by accident.
for action in data['actions']:
    text = (root / action['command'][0]).read_text()
    entrypoints = re.findall(r'--entrypoint\s+([a-z-]+)', text)
    if entrypoints:
        assert all(panes[entrypoint]['placement'] == 'popup' for entrypoint in entrypoints)
    else:
        assert 'herdbake_open_terminal' in text, action['id']
PY

echo 'ok: build tools'
