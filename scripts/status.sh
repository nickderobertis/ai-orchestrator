#!/usr/bin/env bash
# `just status` — a run's live state, with what it is running *in* beside it.
#
# The reading added beside the published view, and why it exists, are
# `scripts/supervision-readings.py`'s. What is decided here is where it goes: ABOVE the
# `providers:` line rather than after the view, because that line is where `AGENTS.md`
# tells a supervisor to cut a watch before matching words in what is above it, so a
# reading below the cut is one no watch following that guidance could see.
#
# llmlint: ignore-file[tool_output_is_signal] The requested run report is this viewing
# command's whole product, and the reading added beside it is the other half of what an
# operator runs it for; summarizing either away would leave nothing.
#
# llmlint: ignore-file[boundary_inputs_validated] This is a passthrough: `onepipeline
# status` is the one thing that judges its own arguments, and validating them here would
# refuse an invocation the published verb accepts.
set -euo pipefail

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || {
  printf 'status: the checkout this recipe was run from could not be resolved; run it from a checkout, so the view it delegates to and the readings beside it come from one\n' >&2
  exit 2
}

# The repository's own interpreter when this is a provisioned checkout, and the system
# one otherwise — the filter is stdlib-only precisely so both work. The second branch is
# not hypothetical and is driven for real: every row of
# `tests/e2e/test_delegated_recipes_e2e.py` runs in a throwaway checkout that has no
# `.venv` at all, so the two rows for these views take this fallback on every run of that
# journey — removing this line fails them with a missing interpreter rather than with a
# changed command line.
python="$root/.venv/bin/python3"
[ -x "$python" ] || python=python3

"$root/scripts/onepipeline.sh" status "$@" |
  "$python" "$root/scripts/supervision-readings.py" status
