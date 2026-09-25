#!/usr/bin/env bash
# Route a person's new board comments on one run's follow-ups back to that run:
# `just follow-ups-handle-comments <run-id> [--detach] [--to SOURCE]`.
#
# `orchestrator/follow_up_comments.py` decides which comments are feedback and writes the
# file; this script only parses the invocation, resolves the drafts root through the one
# helper, and hands that file to `scripts/follow-ups.sh --feedback --comments`, so every
# refusal that path makes is made there once rather than restated here.
#
# `--comments` selects the feedback mode explicitly because this caller knows the
# file's origin; an initial task may also quote comments without changing its mode.
set -euo pipefail

usage="just follow-ups-handle-comments <run-id> [--detach] [--to SOURCE]"

fail() {
    echo "follow-ups-handle-comments: $1; $2" >&2
    exit 2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own checkout stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
checkout=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd) || fail "this recipe could not resolve the checkout it was run from" \
    "run it from a checkout, so the board and drafts root it reads are that checkout's"

python="$checkout/.venv/bin/python3"
[ -x "$python" ] || fail "this checkout has no Python interpreter at $python" \
    "provision this checkout with 'just bootstrap', then retry"

run=${1:-}
case "$run" in
    "" | -*) fail "name the run whose board comments to handle as the first word" "usage: $usage" ;;
esac
# ShellCheck cannot follow a path built from "$checkout" at run time, so it is named.
# shellcheck source=scripts/plan-brief.sh
. "$checkout/scripts/plan-brief.sh"
# llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
[[ "$run" =~ $PLAN_SAFE_RUN_ID ]] || fail "'$run' is not a run id: a run id is one word of letters, digits, '_' and '-'" \
    "name the run as 'just runs' lists it"
shift

# The board is named to the module and to `scripts/follow-ups.sh` only when the caller
# named one, so the default is each program's own `followups` rather than a third copy.
board=()
passed=()
while [ $# -gt 0 ]; do
    case "$1" in
        --to)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                fail "--to was given no value" "usage: $usage"
            fi
            board=(--board "$2")
            passed+=(--to "$2")
            shift 2
            ;;
        --to=*)
            [ -n "${1#--to=}" ] || fail "--to was given no value" "usage: $usage"
            board=(--board "${1#--to=}")
            passed+=(--to "${1#--to=}")
            shift
            ;;
        --detach)
            passed+=(--detach)
            shift
            ;;
        *)
            fail "'$1' is not an option of this recipe" "usage: $usage"
            ;;
    esac
done
# Every plan-store command below — the drafts-root resolution, then the module's board
# read — runs with this checkout's own board credential established first, the way
# `scripts/plan-store.sh` establishes it before every other board command: from the
# gitignored root `.env`, never overriding a name this process already defines. Without
# this, a host that keeps the token only in that file was refused here by the store's own
# `GH_PROJECTS_TOKEN is missing or empty` until a person exported the file by hand.
credentials_helper="$checkout/scripts/credentials-env.sh"
if [ ! -f "$credentials_helper" ] || [ ! -r "$credentials_helper" ]; then
    fail "required helper is not a readable regular file: $credentials_helper" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
# shellcheck source=scripts/credentials-env.sh
if ! . "$credentials_helper"; then
    fail "the credentials helper at $credentials_helper is readable but could not be loaded" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
export_host_credentials follow-ups-handle-comments || exit "$?"

# The drafts root is the one every follow-ups launch reads, resolved by the one helper,
# named to ShellCheck because the path it is sourced from is built at run time.
# shellcheck source=scripts/follow-up-env.sh
. "$checkout/scripts/follow-up-env.sh"
export_follow_up_drafts follow-ups-handle-comments || exit "$?"
drafts_root=${!FOLLOW_UP_DRAFTS_ROOT_ENV}

feedback=$(cd -- "$checkout" && "$python" -m orchestrator.follow_up_comments feedback \
    --root "$drafts_root" ${board[@]+"${board[@]}"} "$run") || exit 2

# llmlint: ignore[tool_output_is_signal] This line is the only place the gathering's path is printed: with `--detach` nothing after it names the file, and a refusal `follow-ups.sh` makes before launching is repaired by re-running with this path, so the launch line that follows cannot carry it for both.
echo "follow-ups-handle-comments: wrote run $run's new board comments to $feedback; re-dispatching with it as feedback" >&2
exec "$checkout/scripts/follow-ups.sh" "$run" --feedback "$feedback" --comments ${passed[@]+"${passed[@]}"}
