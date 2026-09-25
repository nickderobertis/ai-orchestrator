#!/usr/bin/env bash
# `just unfinished` — what this manager session still owes before its turn can end: runs
# it launched that nothing is watching, and branches those runs left preserved that are
# neither landed nor acknowledged with a reason. The thin half: this resolves the
# interpreter, establishes the manager session both halves are asked about, and hands
# the arguments to `orchestrator/unfinished.py`, which is where the contract lives — the
# two halves, the headings, the `--json` object and the exit statuses.
# `--print-surface` prints that status vocabulary, one line per status.
#
# Not `uv run`, for the reason `scripts/unpublished.sh` gives: `uv` takes an exclusive
# lock on the project environment. This checkout's own interpreter first and the system
# one after it, which the module is stdlib-only to make possible; `PYTHONPATH` is this
# checkout's root and nothing else.
#
# llmlint: ignore-file[boundary_inputs_validated] Every argument is forwarded whole to the
# module, whose argparse is the one thing that judges it: a refusal here would be a second
# grammar to keep in step with the first, and the module answers `2` for what it refuses.
set -euo pipefail

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on. `scripts/unpublished.sh` carries the same directive for the same arm.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || {
  printf 'unfinished: the checkout this recipe was run from could not be resolved; run it from a checkout, so the interpreter it names is that checkout'"'"'s\n' >&2
  exit 1
}

# The manager session both halves are asked about, through `scripts/launcher-session.sh`,
# the one definition of who is acting on this host. It exports
# ONEPIPELINE_LAUNCHER_SESSION or leaves it unset; `--session <ID>` names one instead.
launcher_session_helper="$root/scripts/launcher-session.sh"
if [ ! -f "$launcher_session_helper" ] || [ ! -r "$launcher_session_helper" ]; then
  echo "unfinished: required helper is not a readable regular file: $launcher_session_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
  exit 1
fi
# shellcheck source=scripts/launcher-session.sh
if ! . "$launcher_session_helper"; then
  echo "unfinished: the helper at $launcher_session_helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
  exit 1
fi

python="$root/.venv/bin/python3"
if [ ! -x "$python" ]; then
  python=$(command -v python3 || true)
fi
if [ -z "$python" ]; then
  echo "unfinished: found no python3 to run orchestrator/unfinished.py with; run 'just bootstrap' from $root to provision this checkout's own" >&2
  exit 1
fi

module="$root/orchestrator/unfinished.py"
if [ ! -f "$module" ] || [ ! -r "$module" ]; then
  echo "unfinished: the view's module is not a readable regular file: $module; restore it from the repository, then retry" >&2
  exit 1
fi

PYTHONPATH="$root"
export PYTHONPATH

shopt -s execfail
# llmlint: ignore[tool_output_is_signal] This process is replaced by the module, whose two headed halves are the requested product and whose exit status is the contract a consumer branches on.
exec "$python" -m orchestrator.unfinished "$@" || {
  status=$?
  echo "unfinished: could not execute the interpreter $python (exit $status), so the view did not run; run 'just bootstrap' from $root to reprovision this checkout's .venv, or remove it so the python3 on the search path is used" >&2
  exit 1
}
