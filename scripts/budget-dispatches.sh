#!/usr/bin/env bash
# How many dispatches are running on this host, read once from `just host`.
#
# The `dispatches` condition the root `budgets.yaml` declares, which onebudgetspec records
# beside every result measured from that file, and what the pre-push hook samples while
# the gate runs (`scripts/lock-timeout.sh`), so that a slow gate can be read against how
# busy the host was. A budget's verdict never depends on it: it is there for the manager's
# judgement of an over-budget result.
#
# The engine's view prints one row per live dispatch — run, node, launcher, age — and has
# no machine-readable form, so the rows are counted by that shape, and every other line
# has to be one the view is known to print. Where the count cannot be read this fails,
# printing nothing on stdout, and onebudgetspec records `unknown`: the view refusing or
# printing nothing, a runs root it says it cannot read, a row it marks UNPROVEN, whose
# dispatch it could not prove alive or gone, or a line of a shape it is not known to print.
set -euo pipefail

if ! root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd); then
  echo "budget-dispatches: the checkout holding $0 cannot be entered, so how many dispatches are running is unknown; make that checkout readable, or run this from an intact one" >&2
  exit 1
fi

# No sync: a read of the engine's view needs the installed engine as it stands, and a
# sync would queue behind, or hold, the lock on this checkout's environment that every
# `uv run` of the gate this is sampled beside waits on. The view's own stderr is let
# through, since it is the account of why a failed read failed.
if ! view=$(cd "$root" && UV_NO_SYNC=1 just host); then
  echo "budget-dispatches: 'just host' failed (its report is above), so how many dispatches are running is unknown; run 'just host' from $root and repair what it reports" >&2
  exit 1
fi

awk -v root="$root" '
  function refuse(why) { bad = why; exit }
  # llmlint: ignore[changed_behavior_has_e2e] Only an engine whose view changed shape prints no host header, and no journey may double the engine to produce one; the branch refuses to count rather than misread.
  NR == 1 { if ($1 != "host") refuse("its first line is not the host header"); next }
  /the runs root cannot be read/ { refuse("it could not read the runs root") }
  /^  [^ ]+ +[^ ]+ +[^ ]+ +[0-9]+[hms][0-9hms]*( |$)/ {
    if ($0 ~ /UNPROVEN:/) refuse("it could not prove a dispatch alive or gone")
    count++
    next
  }
  /^  reading / || /^  free space: / || /^  no live dispatches$/ { next }
  /^  [0-9]+ stale registry entr/ || /^[0-9]+ run root\(s\) skipped: / { next }
  # llmlint: ignore[changed_behavior_has_e2e] Only an engine whose view changed shape prints a line of none of the shapes above, and no journey may double the engine to produce one; the branch refuses to count rather than misread.
  { refuse("it printed a line of a shape this count does not know: " $0) }
  END {
    # llmlint: ignore[changed_behavior_has_e2e] Only an engine whose view changed shape prints nothing at all, and no journey may double the engine to produce that; the branch refuses to count rather than report zero.
    if (NR == 0) bad = "it printed nothing"
    if (bad != "") {
      print "budget-dispatches: just host answered, but " bad "; the count is unknown. Run '\''just host'\'' from " root " to read the view whole, and repair the runs root it names, or this count if the view has changed shape" > "/dev/stderr"
      exit 1
    }
    print count + 0
  }
' <<<"$view"
