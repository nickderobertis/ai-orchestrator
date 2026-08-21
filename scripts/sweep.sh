#!/usr/bin/env bash
# `just sweep` — reclaims every family of dead working directory this host
# accumulates, through the two published verbs that own them, and reports the ones
# neither reaches. What an operator does about each family, and why the sections are
# rationed, is docs/orchestration.md, "The recorded run".
#
# Four constraints shape the code:
#
# 1. Both verbs run whatever the other did, and a failed one contributes its families
#    to the not-examined list rather than being absent from both.
# 2. Neither verb's report is rewritten. Held back is not rewritten: constraint 4
#    decides whether one is printed, never what it says.
# 3. Nothing after the verbs may abort the run — a measurement that fails degrades to
#    a phrase rather than to `set -e`.
# 4. Three cases decide the output, at one `if` near the bottom. A dry run prints the
#    sections because they are what it was asked for; a verb that failed or a family
#    neither examined prints them because they are the operator's next action; a sweep
#    that judged everything and left nothing to act on is one line, so the sections
#    mean something when they are there.
#
# llmlint: ignore-file[boundary_inputs_validated] Option shapes are checked below,
# before either verb runs. `--min-age-hours`'s value is the verbs' to judge and they do
# not accept the same numbers, so narrowing it here would refuse invocations they take.
set -euo pipefail

# llmlint: ignore[tool_output_is_signal] `--help` is a question and this is its
# answer, so exiting 0 with more than a line is the whole of what the path is for.
# The two options and the shape of a report are what an operator came here to read.
usage() {
  cat <<'USAGE'
Usage: just sweep [--dry-run] [--min-age-hours HOURS]

Reclaims every family of dead working directory this host accumulates, through the
two published verbs that own them: `oneagentgraph sweep` and `onevcs sweep`.

  --dry-run                Report what would be reclaimed and remove nothing.
  --min-age-hours HOURS    Leave anything written inside this many hours alone.
                           Both verbs default to 24 and mean the same thing by it.

A sweep that examined every family and left nothing to act on says so in one line.
A verb that failed, or a family neither of them examined, prints both reports and the
trailer naming what was left unlooked-at. `--dry-run` always prints them: it removes
nothing, so those reports are the answer it was asked for.
USAGE
}

# Prefixed `just sweep` rather than with this script's base name, which is the
# convention elsewhere here: `oneagentgraph sweep` writes its own report under a bare
# `sweep:` prefix, and a refusal from the composition must not read as one of its lines.
die() {
  printf 'just sweep: %s\n' "$1" >&2
  exit 2
}

# A bare `just sweep` forwards nothing, so every expansion of this list below
# has to survive it being empty.
forwarded=()
dry_run=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      forwarded+=("$1")
      shift
      ;;
    --min-age-hours)
      [ "$#" -ge 2 ] || die '--min-age-hours needs a number of hours after it'
      [[ $2 =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "--min-age-hours takes a number of hours, not '$2'"
      forwarded+=("$1" "$2")
      shift 2
      ;;
    --min-age-hours=*)
      [[ ${1#*=} =~ ^[0-9]+(\.[0-9]+)?$ ]] ||
        die "--min-age-hours takes a number of hours, not '${1#*=}'"
      forwarded+=("$1")
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unrecognized argument '$1'; this sweep takes --dry-run and --min-age-hours"
      ;;
  esac
done

# Captured rather than streamed: constraint 4 cannot be decided until both verbs have
# answered. Only stdout is held — an error belongs to the operator the instant the verb
# writes it, so stderr goes straight out.
oneagentgraph_status=0
oneagentgraph_report="$(uv run oneagentgraph sweep ${forwarded[@]+"${forwarded[@]}"})" ||
  oneagentgraph_status=$?

onevcs_status=0
onevcs_report="$(uv run onevcs sweep ${forwarded[@]+"${forwarded[@]}"})" || onevcs_status=$?

#: A restatement rather than a pointer back into the reports above, held against what
#: the installed verbs report examining by `tests/e2e/test_sweep_e2e.py`.
ONEAGENTGRAPH_FAMILIES='runs, temp'
ONEVCS_FAMILIES='publications, recoveries'

# A failed sweeper is reported with what to do about it and not only with its status:
# "a family went unswept" is not something an operator can act on without the command
# that shows why.
unswept() {
  local verb="$1" families="$2" status="$3"
  printf '%s %s — %s sweep exited %s, so nothing in these families was judged.' \
    "$verb" "$families" "$verb" "$status"
  printf '\n    Its error is in its own section above. Re-run uv run %s sweep with' "$verb"
  printf '\n    the same options to see it alone and fix what it names, then sweep'
  printf '\n    again: until it succeeds no total here accounts for these families.'
}

examined=()
unexamined=()

if [ "$oneagentgraph_status" -eq 0 ]; then
  examined+=("oneagentgraph $ONEAGENTGRAPH_FAMILIES — reported above")
else
  unexamined+=("$(unswept oneagentgraph "$ONEAGENTGRAPH_FAMILIES" "$oneagentgraph_status")")
fi

if [ "$onevcs_status" -eq 0 ]; then
  examined+=("onevcs $ONEVCS_FAMILIES — reported above")
else
  unexamined+=("$(unswept onevcs "$ONEVCS_FAMILIES" "$onevcs_status")")
fi

# The pre-adoption worktree root: neither verb's, and never this one's to delete —
# every directory under it is a *registered* git worktree whose lender still lists it
# and whose branch can still hold unpublished work. Named and measured, nothing more.
legacy_worktrees="${AI_ORCHESTRATOR_HOME:-$HOME/.ai-orchestrator}/worktrees"
if [ -d "$legacy_worktrees" ]; then
  # Constraint 3, at the one place that can breach it: this root belongs to other
  # checkouts and is the likeliest here to be unreadable, and it is measured last, so
  # a `find` or `du` that aborted would discard both verbs' reports and this trailer
  # for a bare errno. Each number degrades to a phrase instead.
  legacy_incomplete=0
  if ! legacy_count="$(find "$legacy_worktrees" -mindepth 1 -maxdepth 1 -type d 2>/dev/null |
    wc -l)"; then
    legacy_count='an unreadable number of'
    legacy_incomplete=1
  fi
  if ! legacy_size="$(du -sh -- "$legacy_worktrees" 2>/dev/null | cut -f1)" ||
    [ -z "$legacy_size" ]; then
    legacy_size='an unmeasurable size'
    legacy_incomplete=1
  fi
  unexamined+=("$(
    printf '%s' "$legacy_worktrees — $legacy_size across $legacy_count directories."
    printf '\n    No verb here reclaims one: these are registered git worktrees rather than'
    printf '\n    scratch — each is still listed by the checkout that lent it, and each can'
    printf '\n    still hold a branch nothing has published. Land or discard that branch'
    printf '\n    first — just recoverable names the verb for it — then remove the tree'
    printf '\n    with git worktree remove in the lender.'
    if [ "$legacy_incomplete" -ne 0 ]; then
      printf '\n    A number above is missing rather than zero, because this sweep could'
      printf '\n    not get it: check the root with ls -ld and re-run. A disk-usage'
      printf '\n    account that silently leaves this family out is the failure the'
      printf '\n    trailer exists to prevent.'
    fi
  )")
fi

# The long form: each verb's report as it wrote it, then the trailer neither can.
print_sections() {
  printf '=== oneagentgraph sweep — the scratch a dispatch leaves behind ===\n'
  printf '%s\n' "$oneagentgraph_report"
  printf '\n=== onevcs sweep — the workspaces a publication leaves behind ===\n'
  printf '%s\n' "$onevcs_report"

  printf '\n=== just sweep — what this run looked at ===\n'
  printf 'Families examined:\n'
  if [ "${#examined[@]}" -eq 0 ]; then
    printf '  none — every sweeper this recipe composes failed\n'
  else
    printf '  %s\n' "${examined[@]}"
  fi
  printf 'Families not examined:\n'
  if [ "${#unexamined[@]}" -eq 0 ]; then
    printf '  none\n'
  else
    printf '  %s\n' "${unexamined[@]}"
  fi
}

# Constraint 4, as its three cases.
if [ "$dry_run" -ne 0 ]; then
  # llmlint: ignore[tool_output_is_signal] `--dry-run` removes nothing, so these
  # reports are the only thing it produces: a rehearsal reduced to one line answers
  # none of the question — which candidates, in which family, retained for what
  # reason — that the flag exists to ask.
  print_sections
elif [ "${#unexamined[@]}" -ne 0 ] ||
  [ "$oneagentgraph_status" -ne 0 ] ||
  [ "$onevcs_status" -ne 0 ]; then
  # llmlint: ignore[tool_output_is_signal] Exiting 0 here does not mean there was
  # nothing to report. A family neither verb examined is the sweep working and saying
  # what it could not reach, and that is the operator's next action; going quiet
  # would hide the family that has actually filled this host's disk. Nor may it exit
  # non-zero to earn the right to speak: an unreached family is not a failed sweep,
  # and the status a caller branches on is not free to move.
  print_sections
else
  printf 'just sweep: nothing to act on — every family examined: %s; %s.\n' \
    "oneagentgraph $ONEAGENTGRAPH_FAMILIES" "onevcs $ONEVCS_FAMILIES"
fi

# A sweeper that failed leaves a family unswept, and an operator watching for a full
# disk has to be able to see that in the status as well as in the report.
if [ "$oneagentgraph_status" -ne 0 ] || [ "$onevcs_status" -ne 0 ]; then
  exit 1
fi
