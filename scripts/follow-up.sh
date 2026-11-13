#!/usr/bin/env bash
# `just follow-up <run-id> --title TITLE --repository HOST/OWNER/NAME [--path PATH]... < body.md`:
# the manager's way to record an unverified, non-blocking follow-up against a run.
#
# The same draft every dispatch writes, through the same command, `scripts/follow-up-draft.sh`
# over `onefollowup draft`, as `--as manager --run <run-id>`. The run is the recipe's first
# word because a manager's session carries no run of its own, and the author is fixed because
# the recipe is the manager's.
set -euo pipefail

usage="just follow-up <run-id> --title TITLE --repository HOST/OWNER/NAME [--path PATH]... < body.md"
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || {
    echo "follow-up: refused: this checkout's scripts directory could not be resolved; run the recipe from a readable checkout, then retry" >&2
    exit 2
}
draft="$here/follow-up-draft.sh"
# Checked before either exec below, which would otherwise end on the shell's bare
# "No such file" with nothing saying how to restore it.
[ -x "$draft" ] || {
    echo "follow-up: refused: $draft is missing or not executable; restore it with 'git checkout -- scripts/follow-up-draft.sh', then retry" >&2
    exit 2
}

run=${1:-}
case "$run" in
    -h | --help) exec "$draft" --help ;;
    "" | -*)
        echo "follow-up: refused: name the run this follow-up came from as the first word; usage: $usage" >&2
        exit 2
        ;;
esac
shift
for argument in "$@"; do
    case "$argument" in
        --as | --as=* | --run | --run=* | --member | --member=*)
            echo "follow-up: refused: $argument is this recipe's to decide — a manager's draft is --as manager for the run named first; usage: $usage" >&2
            exit 2
            ;;
    esac
done

exec "$draft" --as manager --run "$run" "$@"
