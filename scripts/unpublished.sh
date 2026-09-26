#!/usr/bin/env bash
# `just unpublished` — the preserved-but-unpublished branches this host is holding onto,
# what each costs in disk, and the `just` command that lands it. The thin half: this
# resolves the interpreter, establishes the manager session the acknowledgement is
# keyed on, and hands the arguments to `orchestrator/unpublished.py`, which is where the
# contract lives — the row shape, the counting rule, the exit statuses, the
# acknowledgement file. `--print-surface` prints that vocabulary — one line per status,
# naming the condition a consumer branches on — for the drift test the guard node writes.
#
# Two files because what the module does — join `onevcs`'s JSON to session records and
# compose JSON back, the `Stop` hook's `--stop-verdict` answer among it — is the wrong
# work for shell.
#
# Not `uv run`: `uv` takes an exclusive lock on the project environment, and a hook
# reading this view runs at the end of every turn.
# This checkout's own interpreter first and the system one after it, which the module is
# stdlib-only to make possible; `PYTHONPATH` is set to this checkout's root and to nothing
# else, as `scripts/plan-check.sh` sets it, so the package imports from the tree in front
# of the operator rather than from an installed copy of another one.
#
# llmlint: ignore-file[boundary_inputs_validated] Every argument is forwarded whole to the
# module, whose argparse is the one thing that judges it: a refusal here would be a second
# grammar to keep in step with the first, and the module answers `2` for what it refuses.
set -euo pipefail

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on. `scripts/recoverable.sh` carries the same directive for the same arm.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || {
  printf 'unpublished: the checkout this recipe was run from could not be resolved; run it from a checkout, so the interpreter it names is that checkout'"'"'s\n' >&2
  exit 1
}

# The manager session an acknowledgement is keyed on — and the one `onepipeline`
# attributes a launch to — through `scripts/launcher-session.sh`, the one definition of
# who is acting on this host, which `scripts/onepipeline.sh` and
# `scripts/telemetry-server.sh` source too, so no two of them derive it differently. It
# exports ONEPIPELINE_LAUNCHER_SESSION or leaves it unset; a session nothing identifies
# stays unidentified, and the module refuses to acknowledge on its behalf.
script_dir="$root/scripts"
launcher_session_helper="$script_dir/launcher-session.sh"
if [ ! -f "$launcher_session_helper" ] || [ ! -r "$launcher_session_helper" ]; then
  echo "unpublished: required helper is not a readable regular file: $launcher_session_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
  exit 1
fi
# shellcheck source=scripts/launcher-session.sh
if ! . "$launcher_session_helper"; then
  echo "unpublished: the helper at $launcher_session_helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
  exit 1
fi

python="$root/.venv/bin/python3"
if [ ! -x "$python" ]; then
  python=$(command -v python3 || true)
fi
if [ -z "$python" ]; then
  echo "unpublished: found no python3 to run orchestrator/unpublished.py with; run 'just bootstrap' from $root to provision this checkout's own" >&2
  exit 1
fi

module="$root/orchestrator/unpublished.py"
if [ ! -f "$module" ] || [ ! -r "$module" ]; then
  echo "unpublished: the view's module is not a readable regular file: $module; restore it from the repository, then retry" >&2
  exit 1
fi

PYTHONPATH="$root"
export PYTHONPATH

# `execfail` so a failed `exec` — an interpreter that is executable but will not start,
# such as a `.venv` whose base interpreter was removed — returns here rather than ending
# the shell with bash's own one-line message as the whole report.
shopt -s execfail
# llmlint: ignore[tool_output_is_signal] This process is replaced by the module, whose listing is the requested product and whose exit status is the contract a consumer branches on.
exec "$python" -m orchestrator.unpublished "$@" || {
  status=$?
  echo "unpublished: could not execute the interpreter $python (exit $status), so the view did not run; run 'just bootstrap' from $root to reprovision this checkout's .venv, or remove it so the python3 on the search path is used" >&2
  exit 1
}
