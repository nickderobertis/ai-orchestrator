#!/usr/bin/env bash
# `just watch <run-id>` — the one command this host watches a run with, and the command
# AGENTS.md's watch rule names. That rule states why watching needs a command at all and
# what a watch owes; this file states only what this wrapper does about it.
#
# Three things, none of which the engine's verb does for it. It asks the installed
# engine whether it has the verb at all, because this recipe lands before the pin that
# carries it. It reads the verb's **machine-readable** form and composes the operator's
# lines from fields, never from the verb's own prose. And it treats everything crossing
# that boundary — the run it was named, the stream, the cursor — as untrusted input,
# because two of those end up inside a command line an operator copies.
set -euo pipefail

# The pin that carries the verb. Named in the refusal below rather than compared
# against a number here: what decides is whether the installed engine has the verb,
# and a version comparison would have to be widened by hand at the carrying release
# and would go on refusing if that guess were wrong.
ENGINE_PIN="config/onepipeline.version"

# What this wrapper exits with when it cannot do its job at all: no run named, no verb
# to delegate to, or — the one that matters most — a machine-readable stream it could
# not read. It is deliberately none of the terminal conditions below, because the
# whole point of those is that a caller branches on them, and a status that could
# mean either "the run settled" or "this watch could not tell" is worth nothing.
EXIT_CANNOT_WATCH=2

# The word a caller anchors the cursor on; AGENTS.md's watch rule says why the quoted
# resume sentence below cannot be the only form, and
# `tests/test_watch_and_release_reading_guidance.py` holds the two together. The local
# invariant is that no producer can forge this line: the renderer's own open with
# `event`, `heartbeat`, `terminal` or `record`, this file's with `watch:`, and no field
# reaches either with its control characters intact.
CURSOR_PREFIX="watch-cursor"

# Every line here is written as it happens, which is half of what makes silence
# readable: what a caller then sees is its own pipeline's to decide, and AGENTS.md's
# watch rule carries the per-filter measurements. What this file owes is the half it
# controls — nothing here may buffer its own lines, and the renderer flushes per record.
#
# Everything this reaches — the engine wrapper, the renderer — is named relative to this,
# and its failure is refused here rather than left to `set -e`, so a checkout this cannot
# resolve itself in says which command could not start rather than nothing at all.
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
if ! root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; then
  echo "watch: this could not resolve the checkout it lives in from ${BASH_SOURCE[0]}, so it cannot reach the engine wrapper or the renderer it watches through. Run 'just watch' from a checkout of this repository; nothing was watched." >&2
  exit "$EXIT_CANNOT_WATCH"
fi

# The two tables below copy somebody else's interface, and are the one place this
# repository states that copy: `--print-surface` renders them, and the recipe, the
# journeys and the gate all read them from there.

# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] `tests/test_watch_surface_drift.py` is the gate over this table, and it reconciles it against the installed engine's own `watch --help` on every uncached run.
WATCH_OPTIONS=(--timeout --tick-interval --cursor --until --filter --all)

# Every terminal condition, as `status:name:phrase`. Five of them, each with its own
# exit status so a caller branches on the status rather than on prose. `3` is the
# status the engine already assigns to "nothing is driving this run"; `1` and `2` are
# already spoken for as its queued and refused, so the remaining conditions take the
# free statuses above them.
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] `tests/test_watch_surface_drift.py` drives the installed verb into every condition a static runs root can produce and compares the pairing against these rows. Three of the five cannot be: `surface-waiting`, `elapsed` and `node-settled` are all answers about a *live* run, and the engine proves liveness from the driving process rather than from the ledger, so a run root carrying a forged lock still answers `nothing-driving` — a fixture that got past that would assert this repository's guess at what the engine inspects, which is not a reconciliation. Those three are declared in that module's `UNDRIVABLE_CONDITIONS`, the declaration is asserted to be exactly them in both directions so the set cannot quietly grow; `tests/e2e/test_watch_selector_e2e.py` drives all three against the installed engine over a run it launches, and `tests/e2e/test_watch_recipe_e2e.py` holds this wrapper's branching on all of them.
# Each name is the engine's **own** word for the condition, as its terminal record
# spells it, so the gate below compares the two for equality rather than reading one
# for the other. Its own phrasing is what a caller sees.
#
# `node-settled` is the one a caller only ever meets by asking for it: it is what the
# two selectors below that name nodes return on, and the terminal record beside it
# carries *which* node — which the engine renders into its own phrase, so this
# wrapper's summary line names the condition and the stream above it names the node.
WATCH_CONDITIONS=(
  "0:settled:the run settled"
  "3:nothing-driving:nothing is driving this run — the state to intervene in"
  "4:surface-waiting:a blocking planner surface is waiting to be answered"
  "5:elapsed:the wait elapsed with the run still live"
  "6:node-settled:a node this wait was told to return on settled"
)

# Every condition a caller may hand `--until`, as the engine spells it. This is the
# vocabulary a supervisor types rather than the endings above — the two overlap and are
# not the same list, because `surface` is a condition to ask for and `surface-waiting`
# is the ending it produces, and `node=<ID>` is a shape rather than a word.
#
# It is restated here for one reason: this repository's own refusals tell an operator
# what to type, and a wrapper that names a condition the engine dropped sends them into
# a refusal from a verb they did not think they were arguing with.
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] `tests/test_watch_surface_drift.py` hands every one of these to the installed verb over a real run and fails on the refusal an unrecognised condition gets, so this table is reconciled against the engine's own parser rather than against a copy of its help.
WATCH_UNTIL=(surface settled nothing-driving node-settled "node=<ID>")

# The wait that has no bound at all, distinct from the `0` whose published meaning is to
# read the run once and return. Named here because both values reach an operator through
# this wrapper's own usage lines, and a wrapper offering a spelling the engine does not
# take would compose a watch refused before it watched anything.
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] Reconciled by the same module, which hands this value to the installed verb over a real run and requires it to be accepted rather than refused as a wait the verb cannot take.
WATCH_TIMEOUT_UNBOUNDED=none

# The gate's input rather than an operator's: one row per option, per `--until`
# condition and per status, the wait that has no bound, and the word the cursor is
# emitted under, read by `tests/test_watch_surface_drift.py` and
# `tests/test_watch_and_release_reading_guidance.py`. Every row here is one of those
# readers' rather than an operator's, and this is not a mode anybody watches a run with.
# llmlint: ignore[tool_output_is_signal] a table of rows is what the reader of this asked for; one line could not carry it.
print_surface() {
  local option entry
  echo "verb watch"
  echo "pin $ENGINE_PIN"
  # llmlint: ignore[tool_output_is_signal] The same table and the same reason as the directive above this function, restated because a line-scoped directive does not reach the function body: this row is a gate's input rather than a line anybody watches a run with.
  echo "cursor-prefix $CURSOR_PREFIX"
  # llmlint: ignore[tool_output_is_signal] The same table and the same reason as the two directives above: this row is a gate's input rather than a line anybody watches a run with, and `--print-surface` is not a mode anybody watches a run in.
  echo "timeout-unbounded $WATCH_TIMEOUT_UNBOUNDED"
  for option in "${WATCH_OPTIONS[@]}"; do echo "option $option"; done
  # llmlint: ignore[tool_output_is_signal] One row per `--until` condition, for the same reader and the same reason as every other row this function writes: the drift gate compares this list against the installed parser, and a list cannot be one line.
  for entry in "${WATCH_UNTIL[@]}"; do echo "until $entry"; done
  for entry in "${WATCH_CONDITIONS[@]}"; do
    local rest=${entry#*:}
    echo "status ${entry%%:*} ${rest%%:*}"
  done
}

phrase_for() {
  local entry
  for entry in "${WATCH_CONDITIONS[@]}"; do
    if [ "${entry%%:*}" = "$1" ]; then
      local rest=${entry#*:}
      echo "${rest#*:}"
      return 0
    fi
  done
  return 1
}

if [ "${1:-}" = --print-surface ]; then
  print_surface
  exit 0
fi

if [ "$#" -eq 0 ]; then
  echo "watch: name the run to watch: just watch <run-id> [--timeout SECONDS|none] [--tick-interval SECONDS] [--cursor CURSOR] [--until CONDITION]... [--filter SPEC | --all]" >&2
  exit "$EXIT_CANNOT_WATCH"
fi

# The run this is a watch of. Every line below names it — the terminal summary, and the
# resume command an operator copies — so it is found rather than assumed to be first,
# and then checked rather than trusted. An invocation of nothing but options names no
# run at all, and a run carrying a space or a shell metacharacter would compose a resume
# command that does something other than resume a watch, so both are refused: this is
# somebody else's word being put into a command line, which is the same boundary the
# renderer holds the verb's own cursor to.
run=
skip=0
for argument in "$@"; do
  if [ "$skip" -eq 1 ]; then
    skip=0
    continue
  fi
  case "$argument" in
    --all) ;;
    --*=*) ;;
    --*) skip=1 ;;
    *)
      run="$argument"
      break
      ;;
  esac
done

if [ -z "$run" ]; then
  echo "watch: these arguments name no run to watch, only options. Name the run first: just watch <run-id> [--timeout SECONDS|none] [--tick-interval SECONDS] [--cursor CURSOR] [--until CONDITION]... [--filter SPEC | --all]" >&2
  exit "$EXIT_CANNOT_WATCH"
fi
if ! [[ "$run" =~ ^[A-Za-z0-9._:+/=-]{1,256}$ ]]; then
  echo "watch: '$run' is not a run id this will put into a command line — a run id is an opaque token, and this one carries characters a copied resume command would not survive. Name the run as 'just runs' lists it." >&2
  exit "$EXIT_CANNOT_WATCH"
fi

# Does the installed engine have the verb at all? Asked as a **positive** question of
# the root's own command list rather than by probing the verb and reading a failure as
# its absence: a probe that fails for any other reason — a broken install, an engine
# that cannot start — would be reported as a missing verb, sending an operator to adopt
# a release that was never the trouble. So an engine that cannot be asked at all is its
# own answer, carrying what the engine itself said.
if ! engine_help=$("$root/scripts/onepipeline.sh" --help 2>&1); then
  # The engine's own words reach a terminal here, so they are stripped of control
  # characters and bounded first — for the reason `scripts/watch-render.py` strips a
  # record's fields, and because a failing engine is exactly the one whose output is
  # least likely to be well formed.
  said=$(printf '%s' "$engine_help" | tr -d '\000-\010\013\014\016-\037\177' | tr '\n' ' ' | cut -c1-500)
  echo "watch: the onepipeline installed here could not be asked what verbs it has, so this cannot tell whether it offers a watch verb. It said: ${said:-nothing at all}. Repair the installation — 'just bootstrap' reinstalls the pinned releases — and retry." >&2
  exit "$EXIT_CANNOT_WATCH"
fi
if ! grep -qE '^[[:space:]]+watch([[:space:]]|$)' <<<"$engine_help"; then
  echo "watch: the onepipeline installed here offers no watch verb, so there is nothing for this recipe to delegate to. The verb is a sibling change on the engine, and what carries it here is $ENGINE_PIN — adopt an engine release carrying it and this command works. Until then, watch the run with 'just monitor <run-id>' beside 'just status <run-id>', and read the unread-surface line yourself." >&2
  exit "$EXIT_CANNOT_WATCH"
fi

# What is handed to the verb: the caller's own arguments, unchanged. The verb writes
# **both** forms unconditionally — the operator's lines on standard error and one NDJSON
# record per line on standard output — so there is no machine-readable form to ask for
# and none to ask for twice. This reads the second descriptor, which is the one the
# renderer below is pointed at.
#
# llmlint: ignore[boundary_inputs_validated] The verb is the authority on its own command line, and these reach it as an argv array rather than a shell string, so nothing is interpreted on the way. Validating option names and arity here would mean a second copy of the engine's surface — the very duplication the drift gate exists to prevent — and would refuse an option the engine grew before this repository noticed. What this *does* validate is every value it re-emits itself: the run above and the cursor the renderer hands back, both of which end up inside a command line an operator copies.
forwarded=("$@")

# The repository's own interpreter when this is a provisioned checkout, and the system
# one otherwise — the renderer is stdlib-only precisely so both work. Resolved before
# the pipeline rather than named inside it: an unresolvable `python3` there dies as the
# shell's own `command not found` in the middle of what looks like a watch, and a
# supervisor reads that as the run having gone wrong rather than as this checkout
# missing the interpreter the renderer needs.
python="$root/.venv/bin/python3"
if [ ! -x "$python" ]; then
  python=$(command -v python3 || true)
fi
if [ -z "$python" ]; then
  echo "watch: this needs a python3 to read the watch verb's machine-readable output with, and there is none — neither $root/.venv/bin/python3 nor a python3 on PATH. Provision this checkout with 'just bootstrap', or put a python3 on PATH; nothing was watched." >&2
  exit "$EXIT_CANNOT_WATCH"
fi

# Where the renderer leaves the cursor for the resume hint below. Its own failure is
# named here for the reason above: `mktemp`'s bare diagnostic says nothing about what
# this command was doing or what to do next, and under `set -e` it would end the watch
# with no line of this command's own.
if ! cursor_file=$(mktemp); then
  echo "watch: this could not create the temporary file it reads the resume cursor back through, in ${TMPDIR:-/tmp} — nothing was watched. Check that directory exists and is writable, or point TMPDIR at one that is, and retry." >&2
  exit "$EXIT_CANNOT_WATCH"
fi
# `|| true` because this runs on the way out: a trap command that fails is a trap that
# can replace the terminal status this command chose with one of its own, and which of
# the terminal conditions ended the watch is the whole of what a caller branches on.
trap 'rm -f "$cursor_file" || true' EXIT

set +e
# llmlint: ignore[tool_output_is_signal] Watching a run as it happens is the whole of what this command is for: the per-event and per-heartbeat lines the renderer writes are its product, and the unread-surface count inside a heartbeat is the one signal AGENTS.md forbids filtering out.
"$root/scripts/onepipeline.sh" watch "${forwarded[@]}" |
  "$python" "$root/scripts/watch-render.py" --cursor-file "$cursor_file"
# Both halves in one read: an assignment is itself a command, so reading
# `PIPESTATUS[0]` into a variable is what replaces the array before the second half
# can be read out of it.
statuses=("${PIPESTATUS[@]}")
set -e
engine_status=${statuses[0]}
render_status=${statuses[1]}

# **An unreadable stream is a failure, and never one of the terminal conditions.** The
# whole of what this wrapper knows about a run comes from two things the verb produced
# together — its machine-readable stream and its exit status — and a stream this could
# not read is a stream whose events, heartbeats and unread-surface counts never reached
# the caller. Handing back the engine's status anyway would report a run as settled on
# the strength of a channel that carried nothing, which is exactly the silence-read-as-
# progress this command exists to end: the operator sees a clean exit and stops looking.
# So the refusal replaces the terminal status rather than being printed beside it.
if [ "$render_status" -ne 0 ]; then
  echo "watch: ${run:-the run}: the watch verb exited $engine_status, but its machine-readable output could not be read — the diagnostic above names each record that could not be read. Nothing was watched that this can vouch for, so no terminal condition is reported: read the run with 'just status ${run:-<run-id>}' and treat this watch as not having happened." >&2
  exit "$EXIT_CANNOT_WATCH"
fi

# The renderer writes this file only when a terminal record named a cursor it was
# willing to hand back, so an empty or absent one is the ordinary "no cursor this time".
# A read that fails is a real anomaly — this file is the script's own `mktemp` — but it
# loses the resume hint and nothing else, because the watch is over and its terminal
# condition already read. So it is named here and the watch still reports its ending,
# rather than dying on `cat`'s own diagnostic past a watch that succeeded.
cursor=
if [ -s "$cursor_file" ]; then
  # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own `mktemp` becomes unreadable between the renderer writing it and this line; no journey can produce that without racing the filesystem the test itself runs on.
  if ! cursor=$(cat "$cursor_file"); then
    echo "watch: the watch itself was read whole, but its resume cursor could not be read back from $cursor_file, so no resume command is printed below. The ending reported below stands; start the next watch without --cursor, and it repeats what this one already showed." >&2
    cursor=
  fi
fi
resume=
if [ -n "$cursor" ]; then
  # Emitted whichever ending is about to be reported: a caller resuming after the
  # engine's own queued or refused wants the cursor as much as one resuming after a
  # terminal condition, and both re-read what the last watch showed without it.
  # llmlint: ignore[tool_output_is_signal] This is the machine-readable half of what the command already reports, and it is the half a caller re-arming a watch reads; the sentence below carries it for a person and is a different reader.
  printf '%s %s\n' "$CURSOR_PREFIX" "$cursor"
  resume=" — resume with 'just watch ${run:-<run-id>} --cursor $cursor'"
fi

# One summary line past the stream already written: which of the terminal
# conditions ended the watch, and the command that resumes from where it stopped.
# llmlint: ignore[tool_output_is_signal] Reducing this away would leave a caller with an exit status and no statement of what happened, and the resume command with nowhere to be printed.
if phrase=$(phrase_for "$engine_status"); then
  echo "watch: ${run:-the run}: $phrase$resume"
else
  echo "watch: ${run:-the run}: the watch verb ended at exit status $engine_status, which is none of the terminal conditions it returns on — 1 and 2 are the engine's own queued and refused. Read the run with 'just status ${run:-<run-id>}' to see where it stands$resume" >&2
fi

exit "$engine_status"
