#!/usr/bin/env bash
# `just follow-up <run-id> --title TITLE --repository HOST/OWNER/NAME [--path PATH]... < body.md`:
# the manager's way to record an unverified, non-blocking follow-up against a run.
#
# It is the same draft every dispatch writes, through the same command: this establishes
# the drafting seam exactly as a launch does (scripts/follow-up-env.sh) and then runs
# scripts/follow-up-draft.sh as `--as manager --run <run-id>`. The run is the recipe's
# first word rather than a flag because a manager's session carries no run of its own to
# default to, and the author is fixed because the recipe is the manager's: a draft this
# wrote naming a node or a member would claim a provenance nothing stamped.
set -euo pipefail

usage="just follow-up <run-id> --title TITLE --repository HOST/OWNER/NAME [--path PATH]... < body.md"

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd); then
    echo "follow-up: the checkout this recipe belongs to could not be resolved; run it from a readable checkout, then retry" >&2
    exit 2
fi

run=${1:-}
case "$run" in
    -h | --help)
        # The contract is the drafting command's own `--help`, so a checkout that has lost
        # that command has no help to give; say so, and how to repair it, rather than
        # leaving the shell's bare `exec` failure as the answer.
        draft_command="$script_dir/follow-up-draft.sh"
        if [ ! -f "$draft_command" ] || [ ! -r "$draft_command" ] || [ ! -x "$draft_command" ]; then
            echo "follow-up: the follow-up drafting command is not an executable file at $draft_command, and its --help is this recipe's; restore it from the repository and 'chmod +x' it, then retry" >&2
            exit 2
        fi
        exec "$draft_command" --help
        ;;
    "" | -*)
        echo "follow-up: refused: name the run this follow-up came from as the first word; usage: $usage" >&2
        exit 2
        ;;
esac
shift

for argument in "$@"; do
    case "$argument" in
        --as | --as=* | --run | --run=* | --member | --member=*)
            echo "follow-up: refused: $argument is this recipe's to decide — a manager's draft is --as manager for the run named first; drop it, or run \"\$ORCHESTRATOR_FOLLOW_UP_DRAFT\" directly for another author. usage: $usage" >&2
            exit 2
            ;;
    esac
done

helper="$script_dir/follow-up-env.sh"
if [ ! -f "$helper" ] || [ ! -r "$helper" ]; then
    echo "follow-up: required helper is not a readable regular file: $helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/follow-up-env.sh
if ! . "$helper"; then
    echo "follow-up: the helper at $helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
export_follow_up_drafts follow-up || exit "$?"

exec "${!FOLLOW_UP_DRAFT_ENV}" --as manager --run "$run" "$@"
