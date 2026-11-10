#!/usr/bin/env bash
# The fingerprint of the bar the envelope reviewer judges against, as
# config/onepipeline.launch.yaml names it for `envelope_reviewer_bar`.
#
# The engine runs this command — one argv word, no shell and no arguments — when it looks
# for a recorded pass, and keys each pass under the run's `validator-passes/` on what it
# prints, so a moved bar runs the reviewer again. `orchestrator/envelope_review.py`'s
# `--bar-fingerprint` is the one digest of that bar; this script only enters the checkout
# it belongs to, so the digest is this checkout's wherever the command is run from. A
# command that fails or prints nothing keys nothing, and the engine reviews uncached.
set -euo pipefail
# Session setup provisions this checkout, so no `uv run` here syncs it, however it is run.
export UV_NO_SYNC=1

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || {
    echo "envelope-review-bar: could not resolve the checkout this command was run from; run it by its path inside a checkout" >&2
    exit 2
}
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when the checkout above a directory this script has just entered stops being enterable mid-run; a journey producing that would have to remove the checkout it is running from.
cd -- "${script_dir%/scripts}" || {
    echo "envelope-review-bar: could not enter the checkout at ${script_dir%/scripts}; run the command by its path inside a readable checkout" >&2
    exit 2
}
exec uv run python -m orchestrator.envelope_review --bar-fingerprint
