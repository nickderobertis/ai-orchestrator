#!/usr/bin/env bash
# The command every dispatch and the monitor draft a non-blocking follow-up with, reached
# through `$ORCHESTRATOR_FOLLOW_UP_DRAFT`, and the one `just follow-up` runs for the manager:
#
#     "$ORCHESTRATOR_FOLLOW_UP_DRAFT" --title TITLE --repository HOST/OWNER/NAME \
#         [--path PATH]... [--as node|monitor|manager] [--member NAME] \
#         [--run RUN-ID] < body.md
#
# A wrapper over `onefollowup draft`, which records the draft and owns its format. This
# passes it what the dispatch environment says and nothing else: the batch key, which is the
# run's public id as the engine reads it (`--run`, else `ONEPIPELINE_RUN_ID`); the author
# label (`node:<ONEPIPELINE_NODE_ID>`, `monitor:<member>` or `manager`, from `--as`); and the
# transcript pointer stamp — the engine command that reads the turns a draft came from. It
# runs onefollowup with this checkout's configuration and store — or the ones
# `ONEFOLLOWUP_CONFIG` and `ONEFOLLOWUP_STORE` name — whichever directory the caller drafts
# from, so a worker in another repository's worktree drafts into this host's drafts source.
# Every other argument goes to `onefollowup draft` as it was given.
set -euo pipefail

usage="\"\$ORCHESTRATOR_FOLLOW_UP_DRAFT\" --title TITLE --repository HOST/OWNER/NAME [--path PATH]... [--as node|monitor|manager] [--member NAME] [--run RUN-ID] < body.md"

checkout=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
onefollowup="$checkout/.venv/bin/onefollowup"
engine="$checkout/node_modules/.bin/onepipeline"

author=node
member=""
run=${ONEPIPELINE_RUN_ID:-}
passed=()
while [ $# -gt 0 ]; do
    case "$1" in
        -h | --help)
            cat <<EOF
usage: $usage

Records one unverified follow-up for the run's follow-up batch; a follow-up agent verifies it
after the run. The body, read from standard input, carries four headings, each with content:
## What happened, ## Where, ## Why it is out of scope, ## Evidence.
EOF
            exit 0
            ;;
        --as | --member | --run)
            [ $# -ge 2 ] || { echo "follow-up-draft: $1 was given no value; usage: $usage" >&2; exit 2; }
            case "$1" in
                --as) author=$2 ;;
                --member) member=$2 ;;
                --run) run=$2 ;;
            esac
            shift 2
            ;;
        *)
            passed+=("$1")
            shift
            ;;
    esac
done

[ -n "$run" ] || { echo "follow-up-draft: no run is named: pass --run RUN-ID, or draft from inside a dispatch, whose ONEPIPELINE_RUN_ID names it; usage: $usage" >&2; exit 2; }

stamps=()
case "$author" in
    node)
        node=${ONEPIPELINE_NODE_ID:-}
        label="node${node:+:$node}"
        stamps=(--stamp "transcript=onepipeline transcript $run${node:+ $node}")
        ;;
    monitor)
        [ -n "$member" ] || { echo "follow-up-draft: --as monitor names its member with --member; usage: $usage" >&2; exit 2; }
        label="monitor:$member"
        stamps=(--stamp "transcript=onepipeline monitor $run --filter '{\"include\":[{\"member\":\"$member\"}]}'")
        ;;
    manager) label=manager ;;
    *) echo "follow-up-draft: --as is node, monitor or manager, not '$author'; usage: $usage" >&2; exit 2 ;;
esac

batch=$("$engine" status "$run" --json | jq -er '.public_id')

exec "$onefollowup" --config "${ONEFOLLOWUP_CONFIG:-$checkout/onefollowup.yaml}" \
    --store "${ONEFOLLOWUP_STORE:-$checkout}" draft \
    --batch "$batch" --author "$label" ${stamps[@]+"${stamps[@]}"} ${passed[@]+"${passed[@]}"}
