#!/usr/bin/env bash
# `just publish-branch` and `just repo-recover` — land one branch, with a drafted body.
#
# `scripts/land-branch.sh <publish-branch|recover> <branch> --repo <checkout> [...]`.
#
# Both recipes were argument passthroughs to `onevcs`, which opens a change request
# with whatever body it was given — none, from an operator naming a branch. This is
# what puts `scripts/draft-pr-body.sh` in front of the two verbs that open one, so the
# body arrives without anyone remembering to draft it.
#
# What it owes the caller, in order of who wins:
#
#   * **Every argument reaches `onevcs` unchanged and in order.** This is a
#     passthrough that adds one flag, not a command line of its own. `--no-draft` is
#     the single exception, consumed here because `onevcs` has no such option.
#   * **A caller who brought a body keeps it.** `--body` or `--body-file` in the
#     argument list means the caller has already decided what the change request says,
#     so no turn is spent and the list is forwarded verbatim.
#   * **`--no-draft` skips drafting**, which is the escape for a bulk landing: an
#     operator working down `just recoverable` over dozens of branches pays one agent
#     turn per branch otherwise.
#   * **Drafting never blocks a landing and never retries.** Whatever the drafter
#     exits with other than 0, its own diagnostic goes to the operator's stderr and the
#     verb runs anyway, with no body — exactly what a run's publication closeout does
#     when its drafting graph produces nothing. A branch that could not be described is
#     still a branch that has to land.
#   * **A `local-direct` identity is never drafted for.** That policy builds the base's
#     squash commit itself and opens no change request, so there is no description for a
#     body to be, and the turn is spent on prose nothing will ever read. The workflow is
#     taken from `onevcs rules check`, never from `onevcs resolve`'s own `workflow`
#     field — every identity on this host registers as `remote` while its rules resolve
#     `local-direct`, so reading one for the other answers the wrong thing for all of
#     them — and an answer this cannot read is drafted for exactly as before, which is
#     the rule every other read here follows: miss rather than skip a body somebody
#     wanted.
#
# **The turn is spent before the push**, because the body is an argument to `onevcs`
# and the verb is what pushes — so a branch the merge path then refuses has paid for a
# body nothing used. `docs/repo-lifecycle.md` has why that is accepted.
#
# Reading the branch and `--repo` out of the argument list is what drafting needs, and
# a list this cannot read that way is landed exactly as it is today: the point is that
# adding a drafter never turns a working invocation into a refusal. `onevcs` remains
# the one thing that judges the arguments.
#
# **`--repo` may name a registered alias**, which `onevcs` takes and the drafter — which
# reads a directory — cannot, so a value that is not a directory is put back to the
# registry with `onevcs resolve`. A value the registry does not know either lands with
# no body, by the rule above: this wrapper adds no refusal `onevcs` would not make.
# `docs/repo-lifecycle.md` has what being stricter than the verb cost while it lasted.
#
# llmlint: ignore-file[boundary_inputs_validated] This is a passthrough: `onevcs` is the
# one thing that judges these arguments, and a second opinion here would refuse
# invocations the verb accepts — the failure this wrapper must not introduce. What it
# reads out of the list is read for drafting alone, and a list it cannot read that way
# is forwarded whole and landed with no body rather than refused.
#
# llmlint: ignore-file[tool_output_is_signal] What the verb verified and where it
# published the branch — the merge path's verdict, the route taken, and the change
# request's URL — is the product an operator runs this for, and the drafter's own
# one-line ending on stderr is what says why a change request opened with no body.
set -euo pipefail

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || {
  printf 'land-branch: the checkout this recipe was run from could not be resolved; run it from a checkout, so the drafter it names is that checkout'"'"'s\n' >&2
  exit 2
}
readonly script_dir
readonly DRAFTER="$script_dir/draft-pr-body.sh"

# How long this waiter may queue for the identity's merge-queue lock. Both verbs below
# publish, and a `local-direct` publication runs the complete gate inside the clone it
# holds under that lock — so a sibling publishing at the same time is queued for as long
# as that gate takes, which is longer than `onevcs`'s own default on this host
# (ai-orchestrator#1164). Derived from what the gate last measured, through the one
# helper every waiter here sources; a caller who named a bound of their own keeps it.
lock_timeout_helper="$script_dir/lock-timeout.sh"
if [ ! -f "$lock_timeout_helper" ] || [ ! -r "$lock_timeout_helper" ]; then
    echo "land-branch: required helper is not a readable regular file: $lock_timeout_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/lock-timeout.sh
if ! . "$lock_timeout_helper"; then
    echo "land-branch: the helper at $lock_timeout_helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
export_lock_timeout land-branch

#: The interpreter that reads `onevcs resolve`'s answer. This checkout's own where it
#: has one, as `scripts/draft-pr-body.sh` picks it for the same read: a landing runs
#: from a checkout whose environment is already synced, and a host python is the
#: fallback rather than the choice.
python="$script_dir/../.venv/bin/python3"
[ -x "$python" ] || python=python3
readonly python

#: Reads the checkout out of one `onevcs resolve` answer. The field is
#: `publication_checkout`, which is the directory the named repository is published
#: from — for an alias, the checkout that alias names.
readonly RESOLVE_PROGRAM='
import json, sys

report = json.load(sys.stdin)
checkout = report.get("publication_checkout") if isinstance(report, dict) else None
sys.stdout.write(checkout if isinstance(checkout, str) else "")
'

#: The options `onevcs publish-branch` and `onevcs recover` take a separate value for.
#: Needed to find the first positional — the branch — without mistaking an option's
#: value for it. An option this does not know about is not guessed at: the argument
#: list is forwarded and no body is drafted, which is what this wrapper did before it
#: drafted anything.
readonly VALUED_OPTIONS=" --repo --title --policy --body --body-file "

fail() {
  printf 'land-branch: %s\n' "$1" >&2
  exit 2
}

verb="${1:-}"
case "$verb" in
  publish-branch | recover) shift ;;
  *) fail "the verb must be 'publish-branch' or 'recover', got '${verb:-nothing}'" ;;
esac

# Read for drafting only. `forwarded` is what `onevcs` is given, and nothing below
# removes anything from it but `--no-draft`.
forwarded=()
branch=""
checkout=""
caller_has_body=0
#: What `--repo` was given, before the resolution below rewrites `checkout` into a
#: directory. `onevcs rules check` is asked about the value the operator typed, because
#: an alias is what the registry maps and what that verb takes.
repo_argument=""
drafting=1
readable=1
positional_seen=0
only_positionals=0
#: Where `--` sits in `forwarded`, once one has been seen. The drafted `--body-file`
#: goes in front of it: everything behind the marker is a positional to `onevcs`, so a
#: flag appended after it is read as one and refuses the whole landing.
marker_at=""

while [ $# -gt 0 ]; do
  argument="$1"
  shift
  if [ "$only_positionals" -eq 1 ]; then
    forwarded+=("$argument")
    positional_seen=$((positional_seen + 1))
    [ "$positional_seen" -ne 1 ] || branch="$argument"
    continue
  fi
  case "$argument" in
    --no-draft)
      # Consumed rather than forwarded: it is this wrapper's option, and `onevcs`
      # would refuse it as unknown.
      drafting=0
      ;;
    --)
      only_positionals=1
      marker_at=${#forwarded[@]}
      forwarded+=("$argument")
      ;;
    --body | --body-file | --body=* | --body-file=*)
      caller_has_body=1
      forwarded+=("$argument")
      case "$argument" in
        --body | --body-file)
          [ $# -gt 0 ] || continue
          forwarded+=("$1")
          shift
          ;;
      esac
      ;;
    --repo=*)
      checkout="${argument#--repo=}"
      repo_argument="$checkout"
      forwarded+=("$argument")
      ;;
    --repo)
      forwarded+=("$argument")
      if [ $# -gt 0 ]; then
        checkout="$1"
        repo_argument="$1"
        forwarded+=("$1")
        shift
      fi
      ;;
    --title=* | --policy=*)
      forwarded+=("$argument")
      ;;
    -*)
      forwarded+=("$argument")
      case "$VALUED_OPTIONS" in
        *" $argument "*)
          if [ $# -gt 0 ]; then
            forwarded+=("$1")
            shift
          fi
          ;;
        *)
          # An option nothing here knows the shape of: whether the next word is its
          # value or the branch is unanswerable, so the branch is unread from here on
          # and this lands with no body rather than drafting for the wrong branch.
          readable=0
          ;;
      esac
      ;;
    *)
      forwarded+=("$argument")
      positional_seen=$((positional_seen + 1))
      [ "$positional_seen" -ne 1 ] || branch="$argument"
      ;;
  esac
done

land() {
  # llmlint: ignore[tool_output_is_signal] see the file-scoped note above.
  exec uv run onevcs "$verb" ${forwarded[@]+"${forwarded[@]}"}
}

if [ "$drafting" -eq 0 ] || [ "$caller_has_body" -eq 1 ] || [ "$readable" -eq 0 ] ||
  [ -z "$branch" ] || [ -z "$checkout" ]; then
  land
fi

# What publishing this identity actually does. `local-direct` opens no change request —
# it builds the base's squash commit itself — so a drafted body describes nothing and the
# turn is waste: measured on this repository's own publication journeys, which are
# `local-direct` throughout and were paying an agent turn each to describe a change
# request that never existed.
#
# Read from `rules check` and never from `onevcs resolve`'s own `workflow`, which is what
# `onevcs register` derived from the origin and is explicitly not the routing;
# `orchestrator/publication_guard.py` reads the same line the same way for the same
# reason. Only a definite `local-direct` skips: an unreadable answer, an unregistered
# value, or a policy this does not know drafts exactly as before, because this wrapper
# adds no refusal and takes away no body somebody wanted.
if policy=$(uv run onevcs rules check "$repo_argument" 2>/dev/null); then
  publication_policy=$(printf '%s\n' "$policy" | sed -n 's/^publication:[[:space:]]*\([^[:space:]]*\).*/\1/p' | head -n 1)
  if [ "$publication_policy" = local-direct ]; then
    land
  fi
fi

# What `--repo` named, as a directory the drafter can read the branch from. Asked of
# `onevcs` because the registry is what maps an alias — or an identity key, or an origin
# URL — to a checkout, and the layout it keeps them under is not this repository's to
# reproduce. Asked below the drafting guard rather than in the reading loop, so a landing
# that drafts nothing spends no subprocess on a value nothing will use.
if [ ! -d "$checkout" ]; then
  # Silent on both halves: an unregistered value is `onevcs`'s own refusal on stderr and
  # nothing for the reader to parse, and the operator's ending is the drafter's one-line
  # diagnostic naming the value they typed. A second line would report the fault twice.
  if resolved=$({ uv run onevcs resolve "$checkout" | "$python" -c "$RESOLVE_PROGRAM"; } 2>/dev/null); then
    [ -z "$resolved" ] || checkout="$resolved"
  fi
fi

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when the host's temporary directory cannot be created at all; driving it would mean breaking the filesystem the suite itself runs on.
scratch="$(mktemp -d -- "${TMPDIR:-/tmp}/orchestrator-land-branch-XXXXXXXX")" ||
  fail "a temporary directory for the drafted body could not be created; check that ${TMPDIR:-/tmp} is writable, then retry"
body="$scratch/body.md"
# The drafted body outlives nothing: `onevcs` has read it by the time the verb
# returns, and leaving it behind would leave a change request's prose in the host's
# temporary directory.
# shellcheck disable=SC2329,SC2317  # Invoked from the EXIT trap below, which shellcheck cannot follow.
clean_up() {
  # Reported rather than swallowed: the file holds a change request's prose, and an
  # operator whose temporary directory is keeping copies of it should hear so. It is
  # not fatal — the landing's own verdict is what the caller ran this for.
  rm -rf -- "$scratch" ||
    printf 'land-branch: the drafted body could not be removed from %s; remove it by hand\n' "$scratch" >&2
}
trap clean_up EXIT
# So the EXIT trap above runs for an interrupted landing too.
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# Its stdout is silent with `--out`, so the only thing this can put in front of the
# verb's own report is why no body was drafted: the ending, and — where a drafting
# member died — what killed it and where the events saying so were kept.
if "$DRAFTER" "$branch" --repo "$checkout" --out "$body"; then
  if [ -n "$marker_at" ]; then
    # In front of `--`, which is the only place an option is still an option. The
    # caller's own arguments keep their order on both sides of it.
    forwarded=(
      "${forwarded[@]:0:$marker_at}"
      --body-file "$body"
      "${forwarded[@]:$marker_at}"
    )
  else
    forwarded+=(--body-file "$body")
  fi
fi

# Not `exec`: the trap has to run once `onevcs` is done, so the drafted body is
# removed rather than left in the host's temporary directory.
# llmlint: ignore[tool_output_is_signal] see the file-scoped note above.
uv run onevcs "$verb" ${forwarded[@]+"${forwarded[@]}"}
