#!/usr/bin/env bash
# Read back how GitHub shows the sample (throwaway, spike-sample-ticket): the board item's field
# values on the followups project, and GitHub's own rendered HTML of the issue body and of its
# comments, printing every `<details>` element so whether it opens collapsed (no `open`
# attribute) and whether its Markdown rendered (lists, code) can be read. A GraphQL read only.
#
#   spikes/follow-up-sample-ticket/verify-render.sh <issue number>
#
# The board credential is loaded by the plan-store wrapper for this one command and handed to
# `gh` as its token; the value is never printed or written.
set -euo pipefail
number=${1:?pass the sample issue number}
PLAN_STORE=${PLAN_STORE:-/home/whoop/ai-orchestrator/scripts/plan-store.sh}
query='query($n:Int!){repository(owner:"nickderobertis",name:"ai-orchestrator"){issue(number:$n){
  url state body bodyHTML
  comments(first:20){nodes{url bodyHTML}}
  projectItems(first:5){nodes{project{number title} fieldValues(first:40){nodes{
    ... on ProjectV2ItemFieldTextValue{text field{... on ProjectV2FieldCommon{name}}}
    ... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}}}}}}}}'
# shellcheck disable=SC2016  # expanded by the inner shell, after the wrapper exported it
"$PLAN_STORE" bash -c 'GH_TOKEN="$GH_PROJECTS_TOKEN" exec gh api graphql -F n="$1" -f query="$2"' _ "$number" "$query" |
    python3 "$(dirname -- "$0")/render_report.py"
