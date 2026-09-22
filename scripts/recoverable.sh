#!/usr/bin/env bash
# `just recoverable` — every preserved branch, and the command that lands it *here*.
#
# `onevcs recoverable` renders its own argv in the `Resume:` line it prints to be
# pasted, and the drafting the engine's landing verbs add is reached through the `just`
# recipes, which name the drafter — so each resume command is re-rendered in its `just`
# form.
# Nothing else on the line moves: `onevcs`'s own quoting of a path a shell would split
# is what still reaches the terminal, and every other line is passed through untouched,
# because reformatting another repository's report here would make this listing drift
# from the one `onevcs recoverable` prints everywhere else.
#
# `--json` is passed through whole and unrewritten. That form's `recover_command` is
# the machine-readable argv other consumers read, and rewriting a published field into
# a command only this checkout has would be a lie about what `onevcs` reports.
#
# llmlint: ignore-file[boundary_inputs_validated] This is a passthrough: `onevcs
# recoverable` is the one thing that judges its own arguments, and validating them here
# would refuse an invocation the published verb accepts. This wrapper reads the list for
# one thing only — whether `--json` is in it — and forwards it whole either way.
#
# llmlint: ignore-file[tool_output_is_signal] The requested recovery inventory is this
# viewing command's whole product, and the resume command is the line an operator runs
# it to obtain.
set -euo pipefail

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || {
  printf 'recoverable: the checkout this recipe was run from could not be resolved; run it from a checkout, so the interpreter it names is that checkout'"'"'s\n' >&2
  exit 2
}

for argument in "$@"; do
  case "$argument" in
    --json)
      exec uv run onevcs recoverable "$@"
      ;;
  esac
done

# The repository's own interpreter when this is a provisioned checkout, and the system
# one otherwise — the filter is stdlib-only precisely so both work.
python="$root/.venv/bin/python3"
[ -x "$python" ] || python=python3

# Anchored on the verb rather than on the `Resume:` label, because the verb is the
# part `onevcs` publishes: `--json`'s `recover_command` is `["onevcs", <verb>,
# <branch>, "--repo", <path>]`, and the human line is that argv rendered. A release
# that renames the label still has its resume commands rewritten; one that stops
# printing commands has nothing rewritten rather than something mangled.
#
# `integrate` is mapped although `recoverable` prints only the two landing verbs a
# preserved branch can earn: the recipe name differs from the published verb for
# `recover` alone, so mapping the third costs one row and leaves nothing to notice if
# a release starts pointing at the train.
# The single quotes are the point rather than an oversight: this is a Python program,
# and the `$` in its own strings is Python's, not the shell's.
# shellcheck disable=SC2016
REWRITE_RESUME_COMMANDS='
import re, sys

RECIPES = {"publish-branch": "publish-branch", "recover": "repo-recover", "integrate": "integrate"}

# Not preceded by a character that would make this part of some longer word or path —
# a `/usr/local/bin/onevcs publish-branch` is somebody naming a binary, not this
# listing offering a command — and not followed by one, which is what keeps
# `onevcs recoverable` out of the `onevcs recover` rewrite.
INVOCATION = re.compile(r"(?<![\w./-])onevcs (" + "|".join(RECIPES) + r")(?![\w-])")

for line in sys.stdin:
    sys.stdout.write(INVOCATION.sub(lambda found: f"just {RECIPES[found.group(1)]}", line))
    sys.stdout.flush()
'

uv run onevcs recoverable "$@" | "$python" -c "$REWRITE_RESUME_COMMANDS"
