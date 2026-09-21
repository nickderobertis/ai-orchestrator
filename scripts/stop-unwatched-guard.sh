#!/usr/bin/env bash
# The `Stop` hook `.claude/settings.json` registers, and the thin half of it: this
# resolves the interpreter and hands the payload on standard input to
# `scripts/stop-unwatched-guard.py`, which is where the contract lives.
#
# It is two files because the harness registers a command, and what that command has
# to do is parse somebody else's JSON off standard input and compose JSON back. Doing
# either in shell is how a session id carrying a quote becomes a broken object, and the
# harness reads this hook's standard output as its decision.
#
# Its own four endings — a checkout this cannot resolve itself in, a Python half that
# is not beside it, an interpreter it cannot find, and a half that ran and answered
# nothing — each write the `systemMessage` object the Python half's unanswered endings
# write, and that is not the exception to the paragraph above it looks like. The hazard
# the split guards against is *untrusted data* — a session id, a run's lines — landing
# inside an object composed by hand, and each of these four strings is a constant:
# nothing is interpolated into it and no input can malform it. What a silent ending here
# would cost is the one thing this hook must not be — a turn that ended unguarded, read
# by the person as a turn the hook cleared — so do not restore the silence.
#
# Strict mode all the same, and each fallible step below says what it does about its own
# failure. `errexit` left off would make silence the shell's default rather than this
# file's decision, and a step added later would inherit a behaviour nobody chose.
set -euo pipefail

# llmlint: ignore-block[tool_output_is_signal] Nothing here writes a diagnostic to a
# stream: standard output is this hook's decision and standard error reaches the model,
# and what the four endings below write on standard output is the harness's own
# warning object rather than an error message, which is the header's whole point.
# Bash's own expansion rather than `dirname`, and `command -v` rather than `which`, so
# that nothing below this line needs a `PATH`. A hook runs in whatever environment the
# harness hands it, and a shell reaching for a program that is not there writes to
# standard error — which is the one stream this must never touch.
source=${BASH_SOURCE[0]}
case "$source" in
  */*) here=${source%/*} ;;
  *) here=. ;;
esac

# The one answer this file gives, in four wordings: the turn ends unguarded, which
# condition, and how to ask by hand. Each is a constant — see the header for why that is
# what makes writing it here safe — and each is written whole, so that no `printf`
# format or variable ever sits between this file and the bytes the harness parses.
unguarded() {
  printf '%s\n' "$1"
}

# `cd`'s own complaint is sent nowhere: standard error reaches the model, and the
# warning below is what says this.
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and this line; no journey can produce that without racing the filesystem the test itself runs on. `scripts/dispatch-env.sh` carries the same directive for the same arm.
if ! root="$(cd -- "$here/.." 2>/dev/null && pwd -P)"; then
  # shellcheck disable=SC2016  # The backticks are text the person reads; single quotes are what keep them from running.
  unguarded '{"systemMessage": "stop-unwatched-guard: this turn ends unguarded, because the hook could not resolve the checkout it runs from. Whether a run this session owns is unwatched was not asked; ask it yourself with `just unwatched`."}'
  exit 0
fi

guard="$root/scripts/stop-unwatched-guard.py"
if [ ! -f "$guard" ]; then
  # shellcheck disable=SC2016  # The backticks are text the person reads; single quotes are what keep them from running.
  unguarded '{"systemMessage": "stop-unwatched-guard: this turn ends unguarded, because the hook found no scripts/stop-unwatched-guard.py beside it to ask with. Whether a run this session owns is unwatched was not asked; ask it yourself with `just unwatched`."}'
  exit 0
fi

# This checkout's own interpreter first and the system one after it, which the guard is
# stdlib-only to make possible. Not `uv run`, for the reason the guard's `_binary` gives.
python="$root/.venv/bin/python3"
if [ ! -x "$python" ]; then
  python=$(command -v python3 || true)
fi
if [ -z "$python" ]; then
  # shellcheck disable=SC2016  # The backticks are text the person reads; single quotes are what keep them from running.
  unguarded '{"systemMessage": "stop-unwatched-guard: this turn ends unguarded, because the hook found no python3 to run scripts/stop-unwatched-guard.py with. Whether a run this session owns is unwatched was not asked; ask it yourself with `just unwatched`."}'
  exit 0
fi

# Run rather than `exec`, so that this script chooses its own exit status in every
# case: an `exec` that fails to replace the shell leaves bash exiting 126 or 127, and a
# loud status is the one thing no ending here may have. The guard answers on standard
# output and exits 0 on every path it controls, so a status that is not 0 is a half that
# answered nothing — an interpreter that could not run it, or a crash before it wrote —
# and it is the fourth ending this file says so about rather than one `errexit` or a
# `|| true` decides in silence. Standard error is dropped for the same reason: the
# contract gives the harness nothing on that stream, the guard writes nothing there on
# any path it controls, and what does reach it is a complaint about the launch — bash's
# own `cannot execute` for an interpreter whose shebang names nothing, a traceback from
# a half that crashed — which the constant below already reports as the turn ending
# unguarded, and which `just unwatched` by hand reproduces where a person needs to read it.
if "$python" "$guard" 2>/dev/null; then
  exit 0
fi
# shellcheck disable=SC2016  # The backticks are text the person reads; single quotes are what keep them from running.
unguarded '{"systemMessage": "stop-unwatched-guard: this turn ends unguarded, because scripts/stop-unwatched-guard.py ended with a status other than 0 without answering. Whether a run this session owns is unwatched was not asked; ask it yourself with `just unwatched`."}'
exit 0
# llmlint: ignore-end[tool_output_is_signal]
