#!/usr/bin/env bash
# `scripts/hold-run-lease.sh [DIRECTORY]` — hold the occupancy lease on the run root
# this dispatch is working in, for as long as its session is live.
#
# What deletes an unheld run root, what this does and does not cover, and the upstream
# fix: docs/run-root-reclamation.md.
#
# Two constraints shape the code:
#
# 1. Nothing here may fail a session. It runs first in `scripts/session-setup.sh`,
#    ahead of the provisioning whose minutes are the exposure, so every path that
#    cannot hold the lease says why and exits 0. That is why `-e` is off below.
# 2. The lease must be released the moment the session stops being live, or this
#    becomes the thing keeping dead scratch on this host's disk. The holder re-asks
#    `onevcs`'s own liveness question every POLL_SECONDS, and the OS releases its
#    lock if it dies.
#
# The session record is read here rather than asked for with `onevcs session
# holders`, which answers the same question: a freshly cut worktree has no `.venv`
# yet, so there is no `onevcs` to ask for most of the window this covers.
# llmlint: ignore[robust_shell] `-e` is deliberate per constraint 1 above: this runs first at session start, every failure path below is handled and reported explicitly with its own exit-0 branch, and aborting on the first one would leave the lease unheld *and* the dispatch unprovisioned. The detached holder's launch is verified by the confirmation loop in `main`, which quotes the holder's own log rather than assuming it worked.
set -uo pipefail

readonly SELF="${BASH_SOURCE[0]}"
#: How often the holder re-asks whether the session it protects is still live, and so
#: also how long a dead run root stays protected from the ordinary reclaimer.
readonly POLL_SECONDS=10
#: How long the holder queues for the lease. It only ever waits behind another
#: holder's brief exclusive contender, so a minute is generous.
readonly ACQUIRE_SECONDS=60
#: How long the parent waits for its holder to prove it took the lease.
readonly CONFIRM_SECONDS=5
#: What `flock` exits with when the lease is held by somebody else, chosen so that
#: contention — the answer this script is asking for — is distinguishable from
#: `flock` failing, which shares exit 1 with a hundred other things.
readonly CONFLICT_EXIT=9
#: What this script will carry as a session token, which is a constraint of its own
#: rather than a copy of onevcs's minting: the value becomes a file name and one line of
#: a `KEY=value` protocol here, so it is held to what is safe as both. It is deliberately
#: a superset of the `s-<hex>` onevcs mints — a token this refuses is one no reader here
#: could act on safely, whoever wrote it. Declared once and handed to the reader below,
#: so the grammar has one spelling in this file.
readonly TOKEN_PATTERN='[A-Za-z0-9][A-Za-z0-9._-]{0,127}'

#: What every line this script writes is prefixed with, which is also how the relay
#: below tells the holder's own diagnosis — each of which names its repair — from
#: whatever bash or the OS wrote to that same stream instead.
readonly LOG_PREFIX='hold-run-lease: '

log() { printf '%s%s\n' "$LOG_PREFIX" "$*" >&2; }

# The onevcs state root, spelled the way onevcs itself spells it: `ONEVCS_HOME` when
# it is set to something non-empty, `~/.onevcs` otherwise.
#
# One thing is refused that onevcs would take: a root whose path carries a line break.
# The lock path is derived from this and travels back through a line-oriented
# protocol, so such a root could invent a field rather than name a directory. Exit 2
# says that, which the caller reports differently from "there is no root at all".
onevcs_home() {
  local root
  if [ -n "${ONEVCS_HOME:-}" ]; then
    root="$ONEVCS_HOME"
  elif [ -n "${HOME:-}" ]; then
    root="$HOME/.onevcs"
  else
    return 1
  fi
  case "$root" in
    *$'\n'* | *$'\r'*) return 2 ;;
  esac
  printf '%s\n' "$root"
}

# One field out of a resolved session, or nothing when it carries no such field.
field() {
  local resolved="$1" wanted="$2" name value
  while IFS='=' read -r name value; do
    if [ "$name" = "$wanted" ]; then
      printf '%s\n' "$value"
      return 0
    fi
  done <<<"$resolved"
  return 1
}

# The session this directory is the worktree of, as `KEY=value` lines, or a single
# `REASON=` line saying why there is nothing to hold.
#
# Every field is validated rather than trusted, and the protocol is part of what is
# validated: these lines are parsed by the shell, so a value carrying a newline or an
# `=` could invent a field, and one carrying a path separator could aim `flock`
# somewhere else. A token narrows the search to that one record, which is what the
# holder re-asks with — the record still has to name this worktree, so a token is a
# shortcut and never a weaker question.
resolve_session() {
  local sessions="$1" locks="$2" directory="$3" token="${4:-}"
  python3 - "$sessions" "$locks" "$directory" "$token" "$TOKEN_PATTERN" <<'PY'
import hashlib
import json
import os
import re
import sys

sessions, locks, directory, wanted_token, token_pattern = sys.argv[1:6]

#: What to do about a record this reader will not act on. One clause, because every
#: one of these means the same thing: onevcs did not write this, and the run root it
#: claims to name will not be protected until something does.
REPAIR = "close and re-open that session, or remove the record, so onevcs writes it again"

#: The one token grammar, handed in by the shell. Unanchored, and matched with
#: `fullmatch` below: `$` would accept a trailing newline, which is the one character
#: this protocol cannot carry.
TOKEN = re.compile(token_pattern)


def answer(**fields):
    """Emit one resolved session, or one reason, as lines the shell reads back.

    Every value is flattened to one line here rather than at each call: paths, record
    contents and OS error strings all reach this protocol, and a value carrying a line
    break would invent a field rather than fill one. Flattened rather than refused,
    because a reason is prose an operator reads and truncating it would lose the part
    that says what to do; the *path* values are separately required to be one line
    already, since those are what `flock` is aimed at.
    """
    for key, value in fields.items():
        line = str(value).replace("\r", " ").replace("\n", " ")
        print(f"{key}={line}")
    raise SystemExit(0)


def carriable(value):
    """Whether one line of the protocol can hold this value without inventing another."""
    return "\n" not in value and "\r" not in value


try:
    wanted = os.path.realpath(directory)
except OSError as error:
    answer(
        REASON=f"{directory} cannot be resolved: {error} — run this from inside the "
        "dispatch worktree, or name a directory that exists"
    )


def candidates():
    if wanted_token:
        yield os.path.join(sessions, f"{wanted_token}.json")
        return
    try:
        entries = sorted(os.scandir(sessions), key=lambda entry: entry.name)
    except OSError as error:
        answer(
            REASON=f"no session records could be read from {sessions}: {error} — check "
            "that it is a readable directory, and that ONEVCS_HOME names the state "
            "root this host's onevcs uses"
        )
    for entry in entries:
        if entry.name.endswith(".json"):
            yield entry.path


found = None
found_at = ""
skipped = []
for path in candidates():
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError) as error:
        # Skipped rather than fatal — one half-written record must not cost every
        # other session its protection — but counted, so a decline can say that some
        # records could not be read at all rather than implying none matched.
        skipped.append(f"{path}: {error}")
        continue
    if not isinstance(record, dict):
        skipped.append(f"{path}: it holds no object")
        continue
    worktree = record.get("worktree")
    if not isinstance(worktree, str):
        continue
    try:
        recorded = os.path.realpath(worktree)
    except (OSError, ValueError) as error:
        # Skipped and counted like an unreadable record, for the same reason and one
        # more: `realpath` refuses an embedded NUL outright, so a record carrying one
        # would leave this reader dying on a traceback and the caller reporting a
        # sessions directory it cannot read — naming neither the record nor the field.
        skipped.append(f"{path}: its worktree cannot be resolved: {error}")
        continue
    if recorded != wanted:
        continue
    found = record
    found_at = path
    break
if found is None:
    unread = f" ({len(skipped)} record(s) could not be read: {'; '.join(skipped[:3])})" if skipped else ""
    answer(
        REASON=f"{directory} is not the worktree of any recorded session{unread} — an "
        "ordinary checkout has no run root to protect and needs nothing; if this is a "
        f"dispatch worktree, `onevcs session holders <repo>` says what {sessions} "
        "records for it"
    )

token = found.get("token")
run_root = found.get("run_root")
state = found.get("state")
owner_pid = found.get("owner_pid")
owner_started = found.get("owner_started")
if not isinstance(token, str) or not TOKEN.fullmatch(token):
    answer(REASON=f"{found_at} names no usable session token — {REPAIR}")
if not isinstance(run_root, str) or not os.path.isabs(run_root) or not carriable(run_root):
    answer(REASON=f"{found_at} names no usable run root for session {token} — {REPAIR}")
if state != "open":
    answer(
    REASON=f"session {token} is {state!r} rather than open, so its run root is the "
    "reclaimer's to take — nothing needs doing unless that session is still working"
)
# A pid of zero is what onevcs's own liveness treats as no owner at all, and a
# negative one would be a signal target rather than a process.
if not isinstance(owner_pid, int) or isinstance(owner_pid, bool) or owner_pid <= 0:
    answer(REASON=f"{found_at} names no owning process for session {token} — {REPAIR}")
# Absent is a record written before onevcs recorded process identity. onevcs reads
# that as stale, and so does this: a pid alone cannot survive pid reuse.
if not isinstance(owner_started, int) or isinstance(owner_started, bool):
    answer(
    REASON=f"{found_at} records no creation identity for pid {owner_pid}, which is "
    f"what tells its owner from a reused pid — {REPAIR}"
)

answer(
    TOKEN=token,
    RUN_ROOT=run_root,
    OWNER_PID=owner_pid,
    OWNER_STARTED=owner_started,
    # `lock::path_for` in onevcs: the sha256 of the lease identity, which for a run
    # root is `run:<path>`, under the state root's `locks` directory.
    LOCK=os.path.join(
        locks, hashlib.sha256(f"run:{run_root}".encode()).hexdigest() + ".lock"
    ),
)
PY
}

# The creation identity Linux gives a pid: field 22 of `/proc/<pid>/stat`, counted
# from the last `)` because the command name is parenthesized and may contain spaces.
# Empty output means there is no such live process.
process_started() {
  local pid="$1" stat trailing
  local -a fields
  stat="$(cat "/proc/$pid/stat" 2>/dev/null)" || return 0
  trailing="${stat##*)}"
  read -r -a fields <<<"$trailing"
  # A zombie or a dying process is not an owner: onevcs reads both as stale.
  case "${fields[0]:-}" in
    Z | X | '') return 0 ;;
  esac
  printf '%s\n' "${fields[19]:-}"
}

# Whether the session recorded for this directory is still the live one this holder
# was started for — the whole of it, re-read: a session that closes, an owner that
# exits, a pid reused by something else, and a record that has since been rewritten
# for a *different* owner all end the hold.
session_is_live() {
  local sessions="$1" locks="$2" directory="$3" token="$4" pid="$5" started="$6"
  local resolved
  resolved="$(resolve_session "$sessions" "$locks" "$directory" "$token")" || return 1
  [ "$(field "$resolved" TOKEN)" = "$token" ] || return 1
  [ "$(field "$resolved" OWNER_PID)" = "$pid" ] || return 1
  [ "$(field "$resolved" OWNER_STARTED)" = "$started" ] || return 1
  [ "$(process_started "$pid")" = "$started" ]
}

# The holder: resolve this directory's session, take its lease shared, then hold it
# until the session stops being live. Runs detached, because a `SessionStart` hook
# must neither wait on it nor have its own output held open by it — so this end writes
# its diagnostics to a log the parent names and reads back.
#
# It resolves the session itself rather than being handed what the parent resolved:
# it re-resolves on every poll anyway, so a second copy of those values could only be
# the stale one, and one argument is a boundary this can validate exactly as the
# public entry point does.
hold() {
  local directory="$1" home sessions locks resolved
  local lock token pid started
  home="$(onevcs_home)" || return 1
  sessions="$home/sessions"
  locks="$home/locks"
  resolved="$(resolve_session "$sessions" "$locks" "$directory")" || return 1
  if ! token="$(field "$resolved" TOKEN)"; then
    log "$(field "$resolved" REASON)"
    return 1
  fi
  lock="$(field "$resolved" LOCK)"
  pid="$(field "$resolved" OWNER_PID)"
  started="$(field "$resolved" OWNER_STARTED)"
  # llmlint: ignore[changed_behavior_has_e2e] This is the window between the holder resolving the session and taking its lease — microseconds a journey cannot stop inside without a debugger attached to a detached process; what it reports is the same ending the release journey drives.
  if [ "$(process_started "$pid")" != "$started" ]; then
    log "session $token stopped being live between resolving it and taking its lease, so nothing is held — re-run 'bash $SELF $directory' if that session is live again"
    return 1
  fi
  if ! exec 9>>"$lock"; then
    log "the lease file $lock could not be opened for writing — check the permissions on it and on its directory"
    return 1
  fi
  # llmlint: ignore[changed_behavior_has_e2e] Reaching this needs something to hold this lease exclusively for ACQUIRE_SECONDS while the session stays live; only the reclaimer takes it exclusively and holds it for microseconds, so a journey could arrange it only by holding the lock itself for a minute — proving what `flock --wait` does rather than what this script does with the answer.
  if ! flock --shared --wait "$ACQUIRE_SECONDS" 9; then
    log "the lease on $lock was still held exclusively after ${ACQUIRE_SECONDS}s — find its holder with 'fuser -v $lock' and let that command finish"
    return 1
  fi
  while session_is_live "$sessions" "$locks" "$directory" "$token" "$pid" "$started"; do
    # A `sleep` that will not sleep would turn this into a busy loop holding a lease,
    # so it ends the hold rather than spinning on it.
    # llmlint: ignore[changed_behavior_has_e2e] Reaching this means delivering a signal to a detached process a journey never learns the pid of, since `setsid` is what puts it outside the caller's process group; what it does with the answer — release and say so — is what the release journey already drives.
    if ! sleep "$POLL_SECONDS"; then
      log "the ${POLL_SECONDS}s poll was interrupted, so this hold on $lock ends here — re-run 'bash $SELF $directory' to take it again"
      return 1
    fi
  done
}

# Whether anything holds the lease right now, which is the question `reclaim` asks of
# it: an exclusive take that is refused means somebody is working in there. Taking it
# non-blocking and dropping it at once changes nothing either way. `flock` failing for
# any other reason is reported and answered `no`, because this is the *evidence* the
# lease is held and an error is not that evidence.
lease_is_held() {
  local lock="$1" status complaint
  complaint="$(flock --exclusive --nonblock --conflict-exit-code "$CONFLICT_EXIT" "$lock" true 2>&1)"
  status=$?
  case "$status" in
    0) return 1 ;;
    "$CONFLICT_EXIT") return 0 ;;
    *)
      log "flock could not answer whether $lock is held (exit $status): ${complaint:-it said nothing} — check that the file exists and this user may open it"
      return 1
      ;;
  esac
}

# Everything the holder said about why it could not take the lease, so a failure to
# confirm names a cause instead of a timeout.
holder_diagnosis() {
  local holder_log="$1" holder="$2" lock_of="$3" said=''
  # llmlint: ignore[changed_behavior_has_e2e] Covered by the journey that makes the holder log unreadable; this same branch is also reached when the log is deleted mid-read, which no journey can time.
  if [ -e "$holder_log" ] && ! said="$(tr '\n' ' ' <"$holder_log")"; then
    printf 'the holder log %s exists but could not be read: check its permissions and that it is a file, then re-run this script\n' "$holder_log"
    return 0
  fi
  if [ -n "$said" ]; then
    # Only this script's own lines carry their own repair. Anything else on that
    # stream is bash's or the OS's — a `setsid` that is not on PATH, an interpreter
    # that could not start — which says what broke and never what to do about it, so
    # the relay supplies the next action rather than passing prose that ends nowhere.
    case "$said" in
      *"$LOG_PREFIX"*) printf '%s\n' "$said" ;;
      *)
        printf 'the holder never reached its own diagnostics and said only: %s — that is its launch failing rather than the lease, so check that setsid and bash are on this PATH, then run it in the foreground to see the whole of it: bash %s --hold <worktree>\n' \
          "$said" "$SELF"
        ;;
    esac
    return 0
  fi
  # llmlint: ignore[changed_behavior_has_e2e] Both endings need the holder to be slower than CONFIRM_SECONDS or to die without writing its log, which is a scheduling race rather than a state a journey can build; the branch they explain is driven by the unopenable-lease journey.
  if kill -0 "$holder" 2>/dev/null; then
    printf 'the holder (pid %s) is still starting and has not reported a failure — wait and re-check with "flock --exclusive --nonblock %s true", or read %s\n' \
      "$holder" "$lock_of" "$holder_log"
  else
    printf 'the holder exited without writing to %s — re-run it in the foreground to see why: bash %s --hold ...\n' \
      "$holder_log" "$SELF"
  fi
}

main() {
  local directory absolute sessions locks resolved reason home holder holder_log
  local token run_root lock owner_pid owner_started
  if [ "$#" -gt 1 ]; then
    log "takes at most one directory, not $# arguments — invoke it as: bash $SELF [/path/to/worktree]"
    return 0
  fi
  directory="${1:-$PWD}"
  # Absolute from here on. The holder is handed this path and leaves for `/` before it
  # holds, so a relative one would name nothing by the time it is used; resolving it
  # here rather than refusing it means `bash hold-run-lease.sh .` works, which is how
  # it is typed by hand. A directory that does not exist keeps its spelling and is
  # declined below by the reader, which says so with the path the caller gave.
  if absolute="$(cd "$directory" 2>/dev/null && pwd -P)"; then
    directory="$absolute"
  fi
  home="$(onevcs_home)"
  case "$?" in
    0) ;;
    2)
      log "nothing to hold: the onevcs state root carries a line break, which no path this script can act on may — set ONEVCS_HOME to a directory whose name is one line"
      return 0
      ;;
    *)
      log "nothing to hold: no onevcs state root — set ONEVCS_HOME or HOME to the directory holding sessions/ and locks/"
      return 0
      ;;
  esac
  sessions="$home/sessions"
  locks="$home/locks"
  if ! command -v python3 >/dev/null 2>&1; then
    log "nothing to hold: no python3 on PATH to read a session record with — install python3 or put it on this dispatch's PATH"
    return 0
  fi
  if ! command -v flock >/dev/null 2>&1; then
    log "nothing to hold: no flock on PATH to take an occupancy lease with — install util-linux or put flock on this dispatch's PATH"
    return 0
  fi
  # llmlint: ignore[changed_behavior_has_e2e] The reader is a `python3` that always exits 0, having turned every failure into a `REASON=` line the journeys do drive; this branch is reachable only by that interpreter dying on a signal, which no journey can arrange without doubling the interpreter itself.
  if ! resolved="$(resolve_session "$sessions" "$locks" "$directory")"; then
    log "nothing to hold: reading a session record for $directory failed outright — check that $sessions is a readable directory"
    return 0
  fi
  if ! token="$(field "$resolved" TOKEN)"; then
    # Not an error: `just bootstrap` runs session setup in ordinary checkouts too,
    # and one of those has no run root to protect. A record this declined over is
    # named by the reason, which says which field of it could not be used.
    reason="$(field "$resolved" REASON)"
    log "nothing to hold: ${reason:-no session was resolved and the reader gave no reason — re-run \"bash $SELF $directory\" and read its output}"
    return 0
  fi
  run_root="$(field "$resolved" RUN_ROOT)"
  lock="$(field "$resolved" LOCK)"
  owner_pid="$(field "$resolved" OWNER_PID)"
  owner_started="$(field "$resolved" OWNER_STARTED)"
  if [ "$(process_started "$owner_pid")" != "$owner_started" ]; then
    log "nothing to hold: session $token is open but the process that opened it (pid $owner_pid) is gone, so its run root is the reclaimer's to take — if that run is finished nothing needs doing; if it is not, publish or import its branch before the root goes"
    return 0
  fi
  # llmlint: ignore[changed_behavior_has_e2e] This branch needs `onevcs`'s own locks directory to be uncreatable, which is a broken state root rather than a state a journey can build without making every other `onevcs` call in it fail first.
  if ! mkdir -p "$locks" 2>/dev/null; then
    log "nothing to hold: $locks could not be created — check the permissions on $home"
    return 0
  fi
  # Beside the lock it is about: the holder has no stdio of its own, and a lease that
  # could not be taken has to leave a reason somewhere the parent can quote.
  holder_log="${lock%.lock}.holder.log"
  # Detached into its own session deliberately. The hold has to outlive the
  # `SessionStart` hook that starts it — that hook is seconds of a dispatch that runs
  # for hours — and its own liveness check is what ends it, so it cannot outlive the
  # work it protects.
  setsid bash "$SELF" --hold "$directory" </dev/null >/dev/null 2>"$holder_log" &
  holder=$!
  local waited=0
  while [ "$waited" -lt "$CONFIRM_SECONDS" ]; do
    if lease_is_held "$lock"; then
      log "holding the occupancy lease on $run_root (session $token, owner pid $owner_pid)"
      return 0
    fi
    sleep 1 || break
    waited=$((waited + 1))
  done
  log "could not confirm the occupancy lease on $run_root within ${CONFIRM_SECONDS}s: $(holder_diagnosis "$holder_log" "$holder" "$lock")"
  return 0
}

if [ "${1:-}" = "--hold" ]; then
  shift
  # The internal re-entry is a boundary like any other: `setsid` re-enters this file by
  # name, so what arrives here is argv rather than this process's own locals. It takes
  # the same one directory the public entry takes, and additionally requires it
  # absolute, because the holder leaves that directory for `/` before it holds — it may
  # have to outlive it.
  if [ "$#" -ne 1 ]; then
    log "--hold takes exactly one absolute directory, not $# arguments — invoke it as: bash $SELF --hold /path/to/worktree"
    exit 1
  fi
  case "$1" in
    /*) ;;
    *)
      log "--hold takes an absolute directory, not '$1' — resolve it first, with \$(cd '$1' && pwd -P)"
      exit 1
      ;;
  esac
  # `cd` writes its own error to the stderr above this line, which is the holder log the
  # parent quotes; what this adds is what to do about it.
  # llmlint: ignore[changed_behavior_has_e2e] `cd /` fails only where the root directory is unreachable to this process, which a journey cannot build without a mount namespace of its own.
  if ! cd /; then
    log "--hold could not leave $PWD for /, the directory it may have to outlive — start it from a directory this user may leave"
    exit 1
  fi
  hold "$1"
  exit 0
fi

main "$@"
