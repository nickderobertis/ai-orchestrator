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

# `--help` is a question and this is its answer, so exiting 0 with more than a line is
# the whole of what the path is for. The two options and the shape of a report are what
# an operator came here to read.
# llmlint: ignore[tool_output_is_signal] --help answers a question; reason above.
usage() {
  cat <<'USAGE'
Usage: just sweep [--dry-run] [--min-age-hours HOURS]

Reclaims every family of dead working directory this host accumulates, through the
two published verbs that own them: `oneagentgraph sweep` and `onevcs sweep`.

  --dry-run                Report what would be reclaimed and remove nothing.
  --min-age-hours HOURS    Leave anything written inside this many hours alone.
                           This recipe passes 4 when you name none, and both verbs
                           mean the same thing by it. Their own default is 24, which
                           on this host reclaimed nothing at all.

A sweep that examined every family and left nothing to act on says so in a short form
naming how many candidates it judged and how many it took. Two of those readings look
alike and are not: nothing reclaimed with candidates examined means every one of them
was live or within retention, and nothing reclaimed with none examined means there was
nothing to judge — so they are two different sentences. Free space is what says whether
this host has room; the reclaimed figure is a statement about these families only.

A verb that failed, a family neither of them examined, or a report this recipe cannot
read those counts out of, prints both reports and the trailer naming what was left
unlooked-at. `--dry-run` always prints them: it removes nothing, so those reports are
the answer it was asked for.

The host scratch root — $TMPDIR, or /tmp — is one of those families. It is measured
and named there, and nothing here removes anything under it.
USAGE
}

# Prefixed `just sweep` rather than with this script's base name, which is the
# convention elsewhere here: `oneagentgraph sweep` writes its own report under a bare
# `sweep:` prefix, and a refusal from the composition must not read as one of its lines.
die() {
  printf 'just sweep: %s\n' "$1" >&2
  exit 2
}

#: The floor this recipe passes when the caller names none, in hours. Two measured
#: constraints: a whole number, because `oneagentgraph sweep` refuses a fractional hour
#: where `onevcs sweep` takes one; and 4 rather than the 24 they default to, because at
#: 24 the composed sweep reclaimed 0 B on this host while `--min-age-hours 4` reclaimed
#: 23.9 GB. Why, and what it is not, is docs/orchestration.md, "The recorded run".
DEFAULT_MIN_AGE_HOURS=4

# A bare `just sweep` forwards nothing of the caller's, so every expansion of this
# list below has to survive it holding only the default above.
forwarded=()
dry_run=0
min_age_given=0
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
      min_age_given=1
      shift 2
      ;;
    --min-age-hours=*)
      [[ ${1#*=} =~ ^[0-9]+(\.[0-9]+)?$ ]] ||
        die "--min-age-hours takes a number of hours, not '${1#*=}'"
      forwarded+=("$1")
      min_age_given=1
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

# Named rather than left to the verbs' own default, so that the number an operator
# reads back in either report is the one this recipe chose and the reasoning above is
# reachable from it.
[ "$min_age_given" -ne 0 ] || forwarded+=(--min-age-hours "$DEFAULT_MIN_AGE_HOURS")

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

#: How `oneagentgraph sweep` names what it looked at and what it took. Two counts, one
#: per line it already writes: `examined family "runs" at <path> — N director(y|ies)`,
#: and one summary `reclaimed <size> from N director(y|ies);` — spelled `would reclaim`
#: under `--dry-run`. Restated here rather than inferred, and held against the
#: installed verb by `tests/e2e/test_sweep_e2e.py`, so a release that rewords either
#: line fails there instead of leaving this recipe quietly counting nothing.
# shellcheck disable=SC2016 # `$0` below is awk's whole record, not this shell's name.
ONEAGENTGRAPH_COUNTS='
BEGIN { examined = 0; reclaimed = 0; saw_examined = 0; saw_reclaimed = 0 }
$0 ~ /^sweep: examined family "[^"]*" at .* [0-9]+ director(y|ies)$/ {
  line = $0
  sub(/ director(y|ies)$/, "", line)
  count = split(line, field, " ")
  examined += field[count]
  saw_examined = 1
  next
}
$0 ~ /^sweep: (reclaimed|would reclaim) .* from [0-9]+ director(y|ies);/ {
  line = $0
  sub(/ director(y|ies);.*$/, "", line)
  count = split(line, field, " ")
  reclaimed += field[count]
  saw_reclaimed = 1
  next
}
END {
  print saw_examined ? examined : "unreadable"
  print saw_reclaimed ? reclaimed : "unreadable"
}
'

#: The same two counts as `onevcs sweep` writes them. Its examined families are listed
#: one per line as `  <family> — N run root(s) in <path>`, and a family nothing has cut
#: a root in yet says so in words instead of with a zero — which is a count of none and
#: is read as one. Its summary is `onevcs sweep: reclaimed N workspace(s), ...`.
# shellcheck disable=SC2016 # `$0` below is awk's whole record, not this shell's name.
ONEVCS_COUNTS='
BEGIN { examined = 0; reclaimed = 0; saw_examined = 0; saw_reclaimed = 0 }
$0 ~ /^  [^ ]+ — [0-9]+ run root\(s\) in / {
  line = $0
  sub(/ run root\(s\) in .*$/, "", line)
  count = split(line, field, " ")
  examined += field[count]
  saw_examined = 1
  next
}
$0 ~ /^  [^ ]+ — nothing has cut a run root at .* yet$/ { saw_examined = 1; next }
$0 ~ /^onevcs sweep: (reclaimed|would reclaim) [0-9]+ workspace\(s\),/ {
  line = $0
  sub(/ workspace\(s\),.*$/, "", line)
  count = split(line, field, " ")
  reclaimed += field[count]
  saw_reclaimed = 1
  next
}
END {
  print saw_examined ? examined : "unreadable"
  print saw_reclaimed ? reclaimed : "unreadable"
}
'

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
  # Constraint 3, at one of the two places that can breach it: this root belongs to
  # other checkouts and is the likeliest here to be unreadable, and both measurements
  # here run *after* both verbs have swept, so a `find` or `du` that aborted would
  # discard both reports and this trailer for a bare errno. Each number degrades to a
  # phrase instead.
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

#: The prefix `oneagentgraph` puts on every directory of the family it owns under the
#: host scratch root. Measured on the adopted release rather than assumed: a directory
#: beside them without it is counted in that family's directory total and then judged
#: by nothing — neither reclaimed nor retained with a reason — so it belongs to no verb
#: this recipe composes, and the numbers below leave the prefixed ones out.
ONEAGENTGRAPH_SCRATCH_PREFIX='oneagentgraph-'

#: A `uv` lock, of which this recipe's own is always one: `uv run` takes one in
#: `$TMPDIR` on its way to each verb, so no sweep can observe a root without one, and
#: the pattern cannot tell that one from another `uv`'s. Counting them would leave this
#: family non-empty on every host that has ever swept and take the one-line form with
#: it. The *count* is the whole of the exclusion: nothing is taken out of the size to
#: match, and a real lock has no bytes to take. The entry below says both, because an
#: exclusion an operator cannot see turns a count of part of the root into the root's.
UV_LOCK_TRANSIENT='uv-*.lock'

#: How many name groups the entry below names. Three, because one hides the shape of
#: the tail and a screenful is the skimming this trailer is rationed to avoid: on this
#: host the first is 91% of the root on its own, and the second and third are what say
#: whether the rest is one more producer or ten thousand small ones.
SCRATCH_NAMED_GROUPS=3

#: The one measurement of that root: its size on the first line, then the largest name
#: groups under it. One `awk` rather than a pipeline per number because the walk above
#: is the expensive part and this reads its output twice for nothing otherwise — and
#: because a `head` closing a pipe early is a SIGPIPE this script would exit on.
# shellcheck disable=SC2016 # `$1` and `$2` below are awk fields, not shell
# parameters: expanding them here would hand awk an empty program.
SCRATCH_MEASUREMENT='
function human(kib) {
  if (kib < 1024) return sprintf("%d KiB", kib)
  if (kib < 1048576) return sprintf("%.1f MiB", kib / 1024)
  if (kib < 1073741824) return sprintf("%.1f GiB", kib / 1048576)
  return sprintf("%.1f TiB", kib / 1073741824)
}

# A trailing id is what makes 3589 directories of one producer read as 3589 producers,
# and on this host that producer is 91% of the root. So fold one off the end while the
# tail looks like an id — four or more characters after a separator, carrying a digit —
# and stop while three characters of name are left, so a name that is all id keeps
# itself rather than collapsing into every other one.
function stem(name,   previous, tail) {
  do {
    previous = name
    if (match(name, /[-._][0-9A-Za-z]+$/) && RSTART > 3) {
      tail = substr(name, RSTART + 1)
      if (length(tail) >= 4 && tail ~ /[0-9]/) name = substr(name, 1, RSTART - 1)
    }
  } while (name != previous)
  return name
}

BEGIN { FS = "\t" }

{
  size = $1 + 0
  name = $2
  if (name == root) { total = size; next }
  sub(/^.*\//, "", name)
  if (index(name, prefix) == 1) { owned += size; next }
  key = stem(name)
  grouped[key] += size
  members[key] += 1
  if (!(key in example)) example[key] = name
}

END {
  family = total - owned
  print human(family < 0 ? 0 : family)
  # A group at a time, largest first, ties broken by name so that one root measured
  # twice reports the same way twice.
  for (rank = 0; rank < groups; rank++) {
    best = ""
    for (key in grouped) {
      if (best == "" || grouped[key] > grouped[best]) best = key
      else if (grouped[key] == grouped[best] && key < best) best = key
    }
    if (best == "") break
    if (members[best] > 1)
      printf "      %s and %d more like it — %s\n", example[best], members[best] - 1, human(grouped[best])
    else
      printf "      %s — %s\n", example[best], human(grouped[best])
    delete grouped[best]
  }
}
'

# The host scratch root: whatever is under it that `oneagentgraph` did not prefix is a
# family neither verb examines. Measured and named, never touched — proving one of
# these dead is the non-reference test both verbs already implement.
scratch_root="${TMPDIR:-/tmp}"

# The whole entry, or nothing at all when this root holds nothing the verbs above did
# not already own. A function for the early return: threading "no such family" back
# through the measurements as an empty string is what prints `0 KiB across  entries`.
scratch_root_entry() {
  [ -d "$scratch_root" ] || return 0

  local incomplete=0 partial=0 count size measured largest
  # Every top-level entry, files as well as directories: a loose file fills a device
  # as well as a directory does, and the account this family is reported for was taken
  # in entries. What `oneagentgraph` prefixed is out because a verb above examined it.
  # The size is the whole root's less that family, so a loose file is in both numbers
  # even though `du -d 1` lists no file for it to be a name group.
  if ! count="$(find "$scratch_root" -mindepth 1 -maxdepth 1 \
    ! -name "$ONEAGENTGRAPH_SCRATCH_PREFIX*" ! -name "$UV_LOCK_TRANSIENT" \
    2>/dev/null | wc -l)"; then
    count='an unreadable number of'
    incomplete=1
  elif [ "$count" -eq 0 ]; then
    # The root exists and holds nothing this recipe's verbs did not examine or write,
    # so there is no family to report — the same silence the pre-adoption worktree root
    # keeps when it is not on this host at all.
    return 0
  fi

  # One walk answers both the size and the shape: `-x` keeps it off anything mounted
  # under the root, and `-d 1` lists each top-level directory beside the total, which
  # is what the name groups below are built from.
  measured="$(du -kx -d 1 -- "$scratch_root" 2>/dev/null)" || partial=1
  # A root this sweep could not list is a root whose total cannot be trusted either:
  # `du` reports what it could reach, and reporting that as the family's size would
  # understate exactly the family this trailer exists to stop understating.
  if [ "$incomplete" -ne 0 ] || [ -z "$measured" ]; then
    size='an unmeasurable size'
    largest=''
    incomplete=1
  else
    largest="$(printf '%s\n' "$measured" | awk \
      -v root="$scratch_root" \
      -v prefix="$ONEAGENTGRAPH_SCRATCH_PREFIX" \
      -v groups="$SCRATCH_NAMED_GROUPS" \
      "$SCRATCH_MEASUREMENT")"
    size="$(printf '%s\n' "$largest" | head -n 1)"
    largest="$(printf '%s\n' "$largest" | tail -n +2)"
    [ "$partial" -eq 0 ] || size="at least $size"
  fi

  printf '%s' "$scratch_root — $size across $count entries, and no verb here examines"
  printf ' any of it.'
  if [ -n "$largest" ]; then
    printf '\n%s' "$largest"
    printf '\n    Those are its largest by name, each trailing id folded into the name'
    printf '\n    in front of it. What oneagentgraph prefixes %s is left' \
      "$ONEAGENTGRAPH_SCRATCH_PREFIX"
    printf '\n    out of every number here: that family is its own and was judged above.'
  fi
  # The one thing under this root in neither list unless it is named here. Constraint 4
  # rations the sections, so this is one sentence and it is unconditional: a reader who
  # sees the family at all sees what its numbers leave out.
  printf '\n    A %s is out of that count: uv run holds one here for the length' \
    "$UV_LOCK_TRANSIENT"
  printf '\n    of one command, so every sweep would otherwise count its own. Its'
  printf '\n    bytes stay in the size above, and a real one has none.'
  printf '\n    Nothing here reclaims any of it, and nothing here writes to it. Both'
  printf '\n    verbs remove a directory only once they can prove no live process names'
  printf '\n    it, and neither owns this root — so what is under it is yours to read'
  printf '\n    and remove by hand, after checking that what wrote it has finished.'
  if [ "$partial" -ne 0 ] && [ "$incomplete" -eq 0 ]; then
    printf '\n    Part of this root could not be read, so that size is a floor and not'
    printf '\n    a total: the family is at least this large.'
  fi
  if [ "$incomplete" -ne 0 ]; then
    printf '\n    A number above is missing rather than zero, because this sweep could'
    printf '\n    not get it: check the root with ls -ld and re-run. A disk-usage'
    printf '\n    account that silently leaves this family out is the failure the'
    printf '\n    trailer exists to prevent.'
  fi
  return 0
}

scratch_entry="$(scratch_root_entry)"
[ -z "$scratch_entry" ] || unexamined+=("$scratch_entry")

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
  # Said here as well as in the one-line forms, because this is the report in front of
  # an operator who came looking at a full disk — and the `Reclaimed:` line above it is
  # the number they are most likely to read as the answer.
  printf '    %s\n' "$FREE_SPACE_NOTE"
}

#: Two numbers read out of one verb's report: how many candidates it examined, and how
#: many of them it took. Either answers `unreadable` when the report carries no line
#: this recipe recognizes, which is a release rewording one of them — never a zero,
#: because "examined nothing" and "cannot tell what was examined" are the two answers
#: this whole distinction exists to keep apart.
counted() {
  local report="$1" program="$2"
  printf '%s\n' "$report" | awk "$program"
}

# Constraint 3 at the third place that can breach it: this runs after both verbs have
# swept, so an `awk` that fell over must not discard their reports. A missing count
# degrades to the word below and takes the long form, where the verbs' own lines are.
counts_readable=1
examined_candidates=0
reclaimed_candidates=0
counted_values=()
mapfile -t counted_values < <(
  counted "$oneagentgraph_report" "$ONEAGENTGRAPH_COUNTS"
  counted "$onevcs_report" "$ONEVCS_COUNTS"
)
# Four numbers, in this order: what oneagentgraph examined and took, then what onevcs
# did. Anything else — a short list, a word, an empty line — is a report this recipe
# could not read, never a zero.
if [ "${#counted_values[@]}" -ne 4 ]; then
  counts_readable=0
else
  for counted_value in "${counted_values[@]}"; do
    [[ $counted_value =~ ^[0-9]+$ ]] || counts_readable=0
  done
fi
if [ "$counts_readable" -ne 0 ]; then
  examined_candidates=$((counted_values[0] + counted_values[2]))
  reclaimed_candidates=$((counted_values[1] + counted_values[3]))
fi

#: The clause every verdict below ends with, because the number in front of it is the
#: one a reader mistakes for an all-clear. A sweep takes only what it can *prove* dead,
#: in the families named beside it, so what says this host has room is free space.
#: Part of the verdict rather than a paragraph under it: a successful sweep is one line.
FREE_SPACE_CLAUSE='free space (df -h) is what says this host has room, not this line'

#: The same thing at length, for the long form alone. There the reader arrived because
#: something is wrong, the sections are already in front of them, and the `Reclaimed:`
#: line above is the number they are most likely to read as the answer.
FREE_SPACE_NOTE='What says this host has room is free space — df -h — never the reclaimed
    figure: a sweep takes only what it can prove dead, in the families named
    here and in no others.'

# Constraint 4, as its three cases.
if [ "$dry_run" -ne 0 ]; then
  # `--dry-run` removes nothing, so these reports are the only thing it produces: a
  # rehearsal reduced to one line answers none of the question — which candidates, in
  # which family, retained for what reason — that the flag exists to ask.
  # llmlint: ignore[tool_output_is_signal] a rehearsal is its report; reason above.
  print_sections
elif [ "${#unexamined[@]}" -ne 0 ] ||
  [ "$oneagentgraph_status" -ne 0 ] ||
  [ "$onevcs_status" -ne 0 ]; then
  # Exiting 0 here does not mean there was nothing to report. A family neither verb
  # examined is the sweep working and saying what it could not reach, and that is the
  # operator's next action; going quiet would hide the family that has actually filled
  # this host's disk. Nor may it exit non-zero to earn the right to speak: an unreached
  # family is not a failed sweep, and the status a caller branches on is not free to
  # move.
  # llmlint: ignore[tool_output_is_signal] an unreached family is the answer; above.
  print_sections
elif [ "$counts_readable" -eq 0 ]; then
  # Neither verb failed and no family went unlooked-at, but this recipe could not read
  # how many candidates were judged out of what they wrote — a release reworded a line
  # above. The one-line forms below would then have to guess between "examined nothing"
  # and "cannot tell", which is exactly the conflation they exist to end, so the
  # operator gets the verbs' own reports instead.
  # llmlint: ignore[tool_output_is_signal] an unreadable count is the answer; above.
  print_sections
elif [ "$examined_candidates" -eq 0 ]; then
  # Nothing was reclaimed because there was nothing to judge. Said in its own sentence
  # because the state below says the opposite thing with the same number: a host whose
  # families are empty and a host whose every candidate is alive both reclaim nothing,
  # and only one of them is evidence that the sweep is working.
  printf 'just sweep: nothing reclaimed — no candidate was examined; every family (%s; %s) was empty; %s.\n' \
    "oneagentgraph $ONEAGENTGRAPH_FAMILIES" "onevcs $ONEVCS_FAMILIES" "$FREE_SPACE_CLAUSE"
elif [ "$reclaimed_candidates" -eq 0 ]; then
  printf 'just sweep: nothing reclaimed — %d candidate(s) examined across every family (%s; %s), all live or within retention; %s.\n' \
    "$examined_candidates" "oneagentgraph $ONEAGENTGRAPH_FAMILIES" "onevcs $ONEVCS_FAMILIES" \
    "$FREE_SPACE_CLAUSE"
else
  printf 'just sweep: reclaimed %d of %d candidate(s) examined — every family examined: %s; %s; %s.\n' \
    "$reclaimed_candidates" "$examined_candidates" \
    "oneagentgraph $ONEAGENTGRAPH_FAMILIES" "onevcs $ONEVCS_FAMILIES" "$FREE_SPACE_CLAUSE"
fi

# A sweeper that failed leaves a family unswept, and an operator watching for a full
# disk has to be able to see that in the status as well as in the report.
if [ "$oneagentgraph_status" -ne 0 ] || [ "$onevcs_status" -ne 0 ]; then
  exit 1
fi
