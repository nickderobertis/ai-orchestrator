#!/usr/bin/env bash
# `just repos-bootstrap` — run each registered sibling checkout's own `just bootstrap`
# ahead of the dispatch that will publish through its gate (#1140). Session setup calls
# it last, with `--detach` (#1233); docs/host-setup.md, "The sibling gates", is the operator's account of what it
# reads, what it reports, and how it is bounded. The list is read through
# `scripts/registered-checkouts.sh`, the reader `just repos-apply` registers from, so
# the two never disagree about which path a line names.
#
# llmlint: ignore-file[tool_output_is_signal] The per-checkout table is the product: an
# operator runs this, and session setup relays it, to read which sibling gates are in
# force and which one refused, and a summary line alone would say bootstraps ran without
# saying whose.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(dirname -- "$script_dir")"
# shellcheck source=scripts/registered-checkouts.sh
. "$script_dir/registered-checkouts.sh"

#: The nesting guard: exported onto every sibling bootstrap this runs, and read on entry.
readonly NESTED_VARIABLE=ORCHESTRATOR_REPOS_BOOTSTRAP_ACTIVE
#: The list override a journey names, so a run of the real session setup provisions a
#: stand-in of the journey's own rather than this host's siblings. `--checkouts` wins.
readonly CHECKOUTS_VARIABLE=ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS
#: How long a caller waits on a checkout another caller is bootstrapping. Long, because
#: what it waits on is a whole bootstrap — a Rust workspace's `cargo fetch` among them —
#: and a wait that expires is reported as `refused` rather than retried.
readonly LOCK_WAIT_SECONDS=1800

usage() {
    cat >&2 <<'USAGE'
usage: repos-bootstrap.sh [--checkouts FILE] [--detach]

Run each registered sibling checkout's own `just bootstrap`, once per change to that
checkout's HEAD or bootstrap inputs, one caller at a time per checkout.

  --checkouts FILE  Read the checkout list from FILE (default config/onevcs.checkouts,
                    or the file ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS names).
  --detach          Start each bootstrap that is due as a job detached from this
                    process and return at once, reporting the jobs earlier calls
                    started instead of waiting on them. Session setup runs this.

Prints one line per listed checkout and a summary; exits 1 when any bootstrap failed
or was refused, and 0 otherwise. The memo, and each checkout's log and job state, are
under `${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/repos-bootstrap/`.
USAGE
}

checkouts_file="$repo_root/config/onevcs.checkouts"
if [[ -n ${!CHECKOUTS_VARIABLE:-} ]]; then
    checkouts_file=${!CHECKOUTS_VARIABLE}
fi
detach=0 job_checkout=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --checkouts) [[ $# -ge 2 ]] || { usage; exit 2; }; checkouts_file=$2; shift 2 ;;
        --detach) detach=1; shift ;;
        # Internal: the detached job `--detach` starts, run with the checkout's lock
        # already held on descriptor 9. Not in the usage, because it is how `--detach`
        # hands over a job rather than a way to bootstrap; run by hand, it bootstraps
        # only under that checkout's own lock, which it takes if free.
        --job) [[ $# -ge 2 ]] || { usage; exit 2; }; job_checkout=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "repos-bootstrap: unknown argument '$1'" >&2; usage; exit 2 ;;
    esac
done

if [[ -z $job_checkout && ! -f $checkouts_file ]]; then
    echo "repos-bootstrap: checkout list $checkouts_file does not exist" >&2
    exit 2
fi

if [[ -n ${!NESTED_VARIABLE:-} ]]; then
    echo "repos-bootstrap: nested inside another repos-bootstrap, which is provisioning the siblings; nothing to do here"
    exit 0
fi

required_tools=(just flock sha256sum git)
# `setsid` is what detaches a job from a session hook, so only `--detach` needs it.
[[ $detach -eq 0 ]] || required_tools+=(setsid)
# llmlint: ignore[changed_behavior_has_e2e] Every host this runs on has all five — `just` is what invoked it, `git` and `sha256sum` are what every recipe here assumes, and `flock` and `setsid` ship with util-linux — so a journey would have to build a PATH holding `just` and `bash` but not `flock`, to prove that `command -v` answers absence, which is bash's behaviour rather than this script's.
for required in "${required_tools[@]}"; do
    if ! command -v "$required" >/dev/null 2>&1; then
        echo "repos-bootstrap: '$required' is not on PATH, so no sibling checkout can be provisioned; install it and run 'just repos-bootstrap' again" >&2
        exit 2
    fi
done

if [[ -z ${HOME:-} || $HOME != /* || ! -d $HOME ]]; then
    echo "repos-bootstrap: HOME must name an existing absolute directory" >&2
    exit 2
fi
# The XDG specification has a relative `XDG_CACHE_HOME` ignored; here it is refused by
# name instead, because a memo written relative to whichever directory this was run
# from is one no later session start would find.
if [[ -n ${XDG_CACHE_HOME:-} && $XDG_CACHE_HOME != /* ]]; then
    echo "repos-bootstrap: XDG_CACHE_HOME must be an absolute path, not '$XDG_CACHE_HOME'; unset it or give it a directory" >&2
    exit 2
fi
memo_root="${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/repos-bootstrap"
if ! mkdir -p "$memo_root"; then
    echo "repos-bootstrap: cannot create the memo root $memo_root; repair its parent's permissions and retry" >&2
    exit 2
fi

# The identity a checkout's `origin` names, in the `host/owner/name` form `onevcs`
# files it under, so that both spellings a clone can carry — `https://host/owner/name`
# with or without `.git`, and `user@host:owner/name` — compare equal. Empty when the
# directory is not itself a checkout, or has no origin, which compares equal to
# nothing: git would otherwise answer for whichever repository encloses a directory.
#
# llmlint: ignore[boundary_inputs_validated] The form is not validated because the value
# is never acted on: it is compared for equality with this checkout's own origin put
# through the same normalization, so a spelling this reduction handles oddly equals only a
# sibling carrying that same odd spelling — this repository again — and a local or malformed
# origin equals nothing and runs. A false skip needs an origin naming another repository
# that reduces to exactly `github.com/nickderobertis/ai-orchestrator`, and no form git
# accepts does; every origin `config/onevcs.checkouts` registers is `https://github.com/…`.
origin_identity() {
    local toplevel origin
    toplevel=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || return 0
    [[ $toplevel == "$1" ]] || return 0
    origin=$(git -C "$1" config --get remote.origin.url 2>/dev/null) || return 0
    origin=${origin%/}
    origin=${origin%.git}
    origin=${origin#*://}
    origin=${origin#*@}
    printf '%s\n' "${origin/:/\/}"
}
this_identity=$(origin_identity "$(cd -- "$repo_root" && pwd -P)")

# Where one checkout's memo lives: keyed on the checkout's physical path, because two
# checkouts of one repository are two toolchains to provision.
memo_dir_for() {
    local key
    key=$(printf '%s' "$1" | sha256sum | cut -c1-16)
    printf '%s/%s\n' "$memo_root" "$key"
}

# Every whitespace-separated token of the `bootstrap` recipe body $2 that names a
# regular file under the checkout $1, relative to it, one per line. This is the whole
# of what a reader can name without executing the recipe: a token is stripped of the
# quotes and the `@`/`-` prefixes a recipe line carries and then tested as a path, so
# `./scripts/setup.sh` is found and a file the body reaches only through a variable,
# a glob or a program's own configuration is not — those move the memo only through
# `HEAD`.
tokens_naming_files() {
    local checkout=$1 body=$2 token
    tr -s '[:space:]' '\n' <<<"$body" | while IFS= read -r token; do
        token=${token#@}
        token=${token#-}
        token=${token#\"}
        token=${token%\"}
        token=${token#\'}
        token=${token%\'}
        # Relative to the checkout and staying inside it: no absolute token, no `..`
        # component anywhere, and no unexpanded variable reference.
        [[ -n $token && $token != /* && $token != *'{{'* ]] || continue
        [[ $token != .. && $token != ../* && $token != */../* && $token != */.. ]] || continue
        [[ -f $checkout/$token ]] || continue
        printf '%s\n' "$token"
    done | sort -u
}

# The checkout's own justfile, under the spellings `just` accepts, or nothing. Named
# explicitly on every `just` invocation below, because `just` otherwise searches
# upward and a checkout without one would run whatever a parent directory defines.
own_justfile() {
    local name
    for name in justfile Justfile .justfile; do
        if [[ -f $1/$name ]]; then
            printf '%s\n' "$1/$name"
            return 0
        fi
    done
    return 1
}

checkout_just() {
    local checkout=$1
    shift
    just --justfile "$(own_justfile "$checkout")" --working-directory "$checkout" "$@"
}

# What a checkout's bootstrap is a function of, as one digest: its `HEAD`, the justfile
# as `just` parses it, and the content of every file the recipe body names. Printed on
# stdout; a checkout `just` cannot read a `bootstrap` recipe out of fails this with the
# reason on stderr, which the caller reports.
fingerprint() {
    local checkout=$1 head parsed body
    head=$(git -C "$checkout" rev-parse HEAD 2>/dev/null) || head=no-commit
    parsed=$(checkout_just "$checkout" --dump 2>&1) || { printf '%s\n' "$parsed" >&2; return 1; }
    body=$(checkout_just "$checkout" --show bootstrap 2>&1) || { printf '%s\n' "$body" >&2; return 1; }
    {
        printf 'head %s\n' "$head"
        printf 'justfile %s\n' "$(sha256sum <<<"$parsed" | cut -d' ' -f1)"
        tokens_naming_files "$checkout" "$body" | while IFS= read -r input; do
            printf 'input %s %s\n' "$input" "$(sha256sum <"$checkout/$input" | cut -d' ' -f1)"
        done
    } | sha256sum | cut -d' ' -f1
}

# One checkout's job state, `state` beside its memo: `key value` lines, replaced whole
# through a rename so a reader never meets half of one. `status` is `running` while a
# bootstrap is under way and `done`, `failed` or `stale` once it has ended, `pid` the
# process holding the lock, and `head` the checkout's `HEAD` when it began — what a
# later session start compares to call a running job stale. The rename that writes an
# ending is the sentinel a caller waiting on the job reads.
write_state() {
    local memo_dir=$1
    shift
    printf '%s\n' "$@" >"$memo_dir/state.new" && mv -f "$memo_dir/state.new" "$memo_dir/state"
}

# Whether `$2` is a value `write_state` writes under the key `$1`: the state file is read
# back into a report and into decisions, so a field in any other shape — a file edited
# or truncated by hand — is read as absent rather than trusted.
valid_state_value() {
    case $1 in
        status) [[ $2 =~ ^(running|done|failed|stale)$ ]] ;;
        pid) [[ $2 =~ ^[1-9][0-9]*$ ]] ;;
        head) [[ $2 =~ ^([0-9a-f]{40}|[0-9a-f]{64}|no-commit)$ ]] ;;
        exit) [[ $2 =~ ^[0-9]+$ ]] ;;
        started | finished) [[ $2 == unknown || $2 =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] ;;
        memo) [[ $2 == not-written ]] ;;
        *) return 1 ;;
    esac
}

# One field of a checkout's job state, or nothing. Read line by line rather than
# sourced, so the file is data whatever it holds.
state_field() {
    local file=$1/state key value
    [[ -f $file ]] || return 0
    # A file that cannot be read is read as absent, like one holding no such field.
    while read -r key value; do
        if [[ $key == "$2" ]]; then
            valid_state_value "$key" "$value" && printf '%s\n' "$value"
            return 0
        fi
    done <"$file" 2>/dev/null || true
}

# The checkout's `HEAD`, or `no-commit` on a branch with no commit yet; fails, printing
# nothing, when `HEAD` or its branch names a commit git cannot read. The two failures of
# `HEAD^{commit}` are told apart by the branch ref itself: `rev-parse --verify` resolves a
# ref that exists without reading the commit it names, so it fails only for a branch that
# has no ref at all — unborn — and a ref naming a missing commit is refused.
head_of() {
    local head branch
    if head=$(git -C "$1" rev-parse --verify --quiet "HEAD^{commit}"); then
        printf '%s\n' "$head"
    elif branch=$(git -C "$1" symbolic-ref --quiet HEAD) \
        && ! git -C "$1" rev-parse --verify --quiet "$branch" >/dev/null; then
        printf 'no-commit\n'
    else
        return 1
    fi
}

now() {
    date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || printf 'unknown\n'
}

# Exactly the repository's own bootstrap, from its own root, in this environment plus
# the nesting guard. `VIRTUAL_ENV` is dropped because a caller running under this
# checkout's `uv run` carries it, and a sibling's `uv sync` would otherwise be told to
# target an environment that is not its own. Its stdin is `/dev/null` so that a
# bootstrap that prompts reads end-of-file rather than waiting on a session hook's
# terminal, and fails where its log can say so. Output goes wherever the caller's
# stdout and stderr point.
run_bootstrap() {
    (cd "$1" && env -u VIRTUAL_ENV "$NESTED_VARIABLE=1" just \
        --justfile "$(own_justfile "$1")" --working-directory "$1" bootstrap) </dev/null
}

# The detached job `--detach` starts, holding the checkout's lock on descriptor 9 until
# it exits. It fingerprints the checkout itself, under that lock, and writes the memo only
# when the bootstrap succeeded with `HEAD` where it began; how it ended goes to its state.
job_main() {
    local checkout=$1 memo_dir head stamp status
    # The same checks the list walk makes before it starts a job, and the lock, so a
    # hand-run `--job` bootstraps nothing unlocked and nothing but a checkout root.
    if [[ $checkout != /* || ! -d $checkout ]] \
        || [[ $(git -C "$checkout" rev-parse --show-toplevel 2>/dev/null) != "$checkout" ]] \
        || ! own_justfile "$checkout" >/dev/null; then
        echo "repos-bootstrap: --job takes a checkout root holding its own justfile" >&2
        exit 2
    fi
    memo_dir=$(memo_dir_for "$checkout")
    # Descriptor 9 has to be this checkout's own lock file, and hold it: another file
    # locked on it would leave the checkout's real lock free for a second job. `flock -n`
    # keeps the lock `--detach` handed over, and takes it when free, as any caller may.
    if ! { true >&9; } 2>/dev/null || [[ ! /dev/fd/9 -ef $memo_dir/lock ]] || ! flock -n 9; then
        echo "repos-bootstrap: --job runs only under the lock --detach hands it" >&2
        exit 2
    fi
    # llmlint: ignore[changed_behavior_has_e2e] The list walk read this `HEAD` a moment before starting the job and refuses `unreadable HEAD` when it cannot, which a journey drives; failing here needs the checkout to break in between.
    if ! head=$(head_of "$checkout"); then
        echo "repos-bootstrap: the HEAD of $checkout names no commit git can read" >&2
        exit 2
    fi
    # llmlint: ignore[changed_behavior_has_e2e] The list walk wrote this same state file a moment before starting the job and refuses `unwritable state` when it cannot, which a journey drives; failing here needs the memo directory to turn unwritable in between, and saying so in the log is the whole of the handling.
    record() {
        write_state "$memo_dir" "$@" "pid $$" "head $head" || echo "repos-bootstrap: state not written to $memo_dir"
    }
    record "status running" "started $(now)"
    echo "repos-bootstrap: job $$ bootstrapping $checkout at $head"
    # llmlint: ignore[changed_behavior_has_e2e] The list walk fingerprinted this checkout successfully a moment before starting this job, so this fails only when its justfile is broken in the instant between the two; recording `failed` is what hands that to the next session start, and a journey could reach it only by racing the handoff.
    if ! stamp=$(fingerprint "$checkout"); then
        record "status failed" "exit 2" "finished $(now)"
        exit 0
    fi
    status=0
    run_bootstrap "$checkout" || status=$?
    if [[ $(head_of "$checkout" || true) != "$head" ]]; then
        echo "repos-bootstrap: $checkout moved from $head while its bootstrap ran; no memo written"
        record "status stale" "exit $status" "finished $(now)"
    elif [[ $status -ne 0 ]]; then
        echo "repos-bootstrap: bootstrap of $checkout exited $status; no memo written"
        record "status failed" "exit $status" "finished $(now)"
    # llmlint: ignore[changed_behavior_has_e2e] The memo directory's contents refusing a write after its lock file was opened in it; the journey over an unwritable memo root covers what that looks like.
    elif printf '%s\n' "$stamp" >"$memo_dir/stamp"; then
        echo "repos-bootstrap: bootstrap of $checkout completed; memo written"
        record "status done" "exit 0" "finished $(now)"
    else
        # Provisioned, and recorded as such nowhere: the next session start runs it
        # again, which is the safe side of a memo it could not write.
        echo "repos-bootstrap: bootstrap of $checkout completed; memo not written"
        record "status done" "exit 0" "memo not-written" "finished $(now)"
    fi
}

if [[ -n $job_checkout ]]; then
    job_main "$job_checkout"
    exit 0
fi

ran=0 unchanged=0 skipped=0 refused=0 failed=0 started=0 running=0 stale=0
report() {
    printf '  %-10s %-20s %s\n' "$1" "$2" "$3"
}

# `--detach`, over a checkout whose lock another caller holds: that caller's job is
# reported from the state it recorded, and nothing is started beside it. A job whose
# checkout's `HEAD` has moved since it began is `stale` — its bootstrap is of a tree
# the checkout no longer is, so it writes no memo and the start after it ends runs
# the checkout again.
report_held() {
    local checkout=$1 memo_dir=$2 path=$3 log=$4 began pid holder current
    began=$(state_field "$memo_dir" head)
    pid=$(state_field "$memo_dir" pid)
    holder="another caller"
    [[ $(state_field "$memo_dir" status) != running || -z $pid ]] || holder="pid $pid"
    if ! current=$(head_of "$checkout"); then
        report refused "unreadable HEAD; $holder still running" "$path  (log: $log)"
        refused=$((refused + 1))
    elif [[ -n $began && $current != "$began" ]]; then
        report stale "HEAD moved since ${began:0:12}; $holder still running" "$path  (log: $log)"
        stale=$((stale + 1))
    else
        report running "$holder" "$path  (log: $log)"
        running=$((running + 1))
    fi
}

# `--detach`, over a checkout whose lock this caller holds and whose memo does not
# match: report how the last job ended, when it did not complete, and start the next
# one, handing it the lock on descriptor 9. A failed job's log is kept as
# `bootstrap.failed.log` so the line naming it still names its output once the next
# job's log replaces `bootstrap.log`. The state is written `running` before the job
# starts — the job then records its own process over it — so no reader sees the ending
# the job is replacing, and no job fast enough to finish first has its ending undone.
start_job() {
    local checkout=$1 memo_dir=$2 path=$3 log=$4 head=$5 previous previous_exit job kept waited recorded pending status
    previous=$(state_field "$memo_dir" status)
    previous_exit=$(state_field "$memo_dir" exit)
    kept="$memo_dir/bootstrap.failed.log"
    # llmlint: ignore[changed_behavior_has_e2e] A rename within the memo directory fails only where creating a file there would, and the journeys drive that as the `unwritable state` refusal below; a journey could not make the rename alone fail without also refusing the writes around it.
    if [[ $previous == failed || $previous == running ]] && ! mv -f "$log" "$kept" 2>/dev/null; then
        # Not kept apart, so not truncated either: the next job appends to it.
        kept=$log
    fi
    # The log the job's output is redirected into, opened here first so a log that
    # cannot be written refuses by name rather than as a job that never started.
    if { [[ $kept == "$log" ]] && ! : >>"$log"; } || { [[ $kept != "$log" ]] && ! : >"$log"; }; then
        report refused "unwritable log" "$path: $log"
        refused=$((refused + 1))
        return 0
    fi
    if ! write_state "$memo_dir" "status running" "head $head" "started $(now)"; then
        report refused "unwritable state" "$path: $memo_dir"
        refused=$((refused + 1))
        return 0
    fi
    # `-w` keeps `$!` the process whose exit is the job's, whether or not `setsid` has to
    # fork to leave this process group.
    setsid -w bash "$script_dir/repos-bootstrap.sh" --job "$checkout" </dev/null >>"$log" 2>&1 3<&- &
    job=$!
    # A job has started once it records its own process, which it does as soon as it has
    # re-checked its checkout, lock and `HEAD`. One that exits before that is reaped here
    # and its exit status collected; one still alive without a record when the wait runs
    # out is starting under load, and is reported as started but not yet recorded.
    # llmlint: ignore-block[changed_behavior_has_e2e] Every detached journey drives this wait
    # to its ordinary end, the job's record appearing within milliseconds; running out the
    # bound needs a host on which `bash` takes half a minute to start and write one file,
    # which a journey could arrange only by stalling the host it runs on.
    for ((waited = 0; waited < 600; waited++)); do
        [[ -z $(state_field "$memo_dir" pid) ]] || break
        kill -0 "$job" 2>/dev/null || break
        sleep 0.05
    done
    # llmlint: ignore-end[changed_behavior_has_e2e]
    recorded=$(state_field "$memo_dir" pid)
    pending=""
    # llmlint: ignore[changed_behavior_has_e2e] Only a job still unrecorded when the bound above runs out reaches this, which needs the same stalled host the wait's own reason names.
    [[ -n $recorded ]] || pending="; not yet recorded"
    # llmlint: ignore[changed_behavior_has_e2e] `setsid` is checked on PATH before anything runs, `bash` is what is running this, and the job's own checks are the list walk's repeated under the lock this caller holds, so a job gone without a record needs the host to lose a binary or the checkout to change in the moment between.
    if [[ -z $recorded ]] && ! kill -0 "$job" 2>/dev/null; then
        status=0
        wait "$job" || status=$?
        report refused "job did not start: exit $status" "$path  (log: $log)"
        refused=$((refused + 1))
        return 0
    fi
    case $previous in
        failed)
            report failed "exit ${previous_exit:-unrecorded}; started again$pending" "$path  (log: $kept)"
            failed=$((failed + 1))
            ;;
        running)
            report failed "killed before it finished; started again$pending" "$path  (log: $kept)"
            failed=$((failed + 1))
            ;;
        stale)
            report stale "HEAD moved under the last job; started again$pending" "$path  (log: $log)"
            stale=$((stale + 1))
            ;;
        *)
            report started "job ${recorded:-$job}$pending" "$path  (log: $log)"
            started=$((started + 1))
            ;;
    esac
}

# The list is read whole before anything runs, so a line the reader refuses refuses
# the run before it has provisioned anything. It is walked on a descriptor of its own
# rather than stdin, because everything the loop runs — `git`, the sibling's `just`, and
# the sibling's bootstrap itself — inherits stdin, and one that read it would have eaten
# every checkout listed after it, leaving them neither provisioned nor reported.
listed=$(registered_checkout_paths "$checkouts_file") || exit 2
echo "repos-bootstrap: memo $memo_root"
while IFS= read -r -u 3 path; do
    [[ -n $path ]] || continue
    if [[ ! -d $path ]]; then
        report skip "not on this host" "$path"
        skipped=$((skipped + 1))
        continue
    fi
    if ! checkout=$(cd -- "$path" 2>/dev/null && pwd -P); then
        report refused "unreadable directory" "$path"
        refused=$((refused + 1))
        continue
    fi
    # A registered checkout is a repository's root: a listed directory that is not one
    # — a file's parent, a directory inside a checkout — is refused rather than handed
    # a `just bootstrap` that would run whatever justfile it holds.
    if [[ $(git -C "$checkout" rev-parse --show-toplevel 2>/dev/null) != "$checkout" ]]; then
        report refused "not a checkout root" "$path"
        refused=$((refused + 1))
        continue
    fi
    if [[ -n $this_identity && $(origin_identity "$checkout") == "$this_identity" ]]; then
        report skip "this repository" "$path"
        skipped=$((skipped + 1))
        continue
    fi
    if ! own_justfile "$checkout" >/dev/null; then
        report skip "no justfile" "$path"
        skipped=$((skipped + 1))
        continue
    fi
    memo_dir=$(memo_dir_for "$checkout")
    if ! mkdir -p "$memo_dir"; then
        report refused "no memo directory" "$path: $memo_dir"
        refused=$((refused + 1))
        continue
    fi
    log="$memo_dir/bootstrap.log"
    # The lock is held for the whole fingerprint-decide-run-record sequence, and the
    # checkout is fingerprinted only once it is held: a caller that waited reads what
    # the caller it waited on recorded against the checkout as it stands when its wait
    # ends, so a `HEAD` or input that moved while it waited is bootstrapped rather than
    # read as the tree the earlier caller recorded. The file is opened for append so
    # that an open never truncates what another holder's descriptor is aimed at.
    # llmlint: ignore[changed_behavior_has_e2e] This refusal and the two memo writes below it can fail only inside a memo directory the line above just created or found writable — a directory this process owns whose contents refuse a write — and inducing that mid-run would prove the kernel's permission check rather than the report; what a memo root that cannot be written looks like is driven by the journey over an unwritable root.
    if ! exec 9>>"$memo_dir/lock"; then
        report refused "no lock file" "$path: $memo_dir/lock"
        refused=$((refused + 1))
        continue
    fi
    # `--detach` never waits: a lock held is a job under way, reported rather than
    # joined, so a session start returns whatever that job is doing.
    if [[ $detach -eq 1 ]] && ! flock -n 9; then
        report_held "$checkout" "$memo_dir" "$path" "$log"
        exec 9>&-
        continue
    fi
    # llmlint: ignore[changed_behavior_has_e2e] Reaching the refusal below means another caller held one checkout's bootstrap for half an hour; a journey could arrange that only by holding the lock itself for that long, which would prove what `flock -w` does rather than what this script reports.
    if [[ $detach -eq 0 ]] && ! flock -w "$LOCK_WAIT_SECONDS" 9; then
        report refused "held over ${LOCK_WAIT_SECONDS}s" "$path  (another caller's bootstrap; log: $log)"
        refused=$((refused + 1))
        exec 9>&-
        continue
    fi
    if ! head=$(head_of "$checkout"); then
        report refused "unreadable HEAD" "$path"
        refused=$((refused + 1))
        exec 9>&-
        continue
    fi
    if ! stamp=$(fingerprint "$checkout" 2>"$memo_dir/refusal"); then
        # The sibling's `just` wrote the reason, so it is folded onto one line and
        # stripped of every control character — an escape sequence included — before
        # it is put into this report; the file keeps the original.
        reason=$(tr '\n' ' ' <"$memo_dir/refusal" | tr -d '\000-\037\177')
        if [[ $reason == *"does not contain recipe"* ]]; then
            report skip "no bootstrap recipe" "$path"
            skipped=$((skipped + 1))
        else
            report refused "unreadable justfile" "$path: $reason"
            refused=$((refused + 1))
        fi
        exec 9>&-
        continue
    fi
    if [[ -f $memo_dir/stamp && $(<"$memo_dir/stamp") == "$stamp" ]]; then
        completed=""
        if [[ $(state_field "$memo_dir" status) == "done" ]]; then
            finished=$(state_field "$memo_dir" finished)
            completed="completed${finished:+ $finished}"
        fi
        report unchanged "$completed" "$path"
        unchanged=$((unchanged + 1))
        exec 9>&-
        continue
    fi
    # llmlint: ignore[changed_behavior_has_e2e] The second of the three writes the comment above the lock file covers.
    if ! printf '%s\n' "$checkout" >"$memo_dir/checkout"; then
        report refused "unwritable memo" "$path: $memo_dir"
        refused=$((refused + 1))
        exec 9>&-
        continue
    fi
    if [[ $detach -eq 1 ]]; then
        start_job "$checkout" "$memo_dir" "$path" "$log" "$head"
        exec 9>&-
        continue
    fi
    # The state a detached call reads while this holds the lock, and after it: written
    # beside the memo as a job's is, and noted on the line when it could not be.
    state_note=""
    # llmlint: ignore[changed_behavior_has_e2e] Another write into the memo directory the comment above the lock file covers.
    write_state "$memo_dir" "status running" "pid $$" "head $head" "started $(now)" \
        || state_note="state not written"
    if run_bootstrap "$checkout" >"$log" 2>&1; then
        # llmlint: ignore[changed_behavior_has_e2e] Another write into the memo directory the comment above the lock file covers.
        write_state "$memo_dir" "status done" "pid $$" "head $head" "exit 0" "finished $(now)" \
            || state_note="state not written"
        # llmlint: ignore[changed_behavior_has_e2e] The third of the three writes the comment above the lock file covers.
        if printf '%s\n' "$stamp" >"$memo_dir/stamp"; then
            report ran "$state_note" "$path  (log: $log)"
        else
            # Provisioned, and recorded as such nowhere: the next caller runs it again,
            # which is the safe side of a memo it could not write.
            report ran "memo not written" "$path  (log: $log)"
        fi
        ran=$((ran + 1))
    else
        # No stamp is written, and whatever stamp an earlier success left names a tree
        # this one is not, so the next caller runs this checkout's bootstrap again.
        status=$?
        # llmlint: ignore[changed_behavior_has_e2e] Another write into the memo directory the comment above the lock file covers.
        write_state "$memo_dir" "status failed" "pid $$" "head $head" "exit $status" "finished $(now)" \
            || state_note="; state not written"
        report failed "exit $status$state_note" "$path  (log: $log)"
        failed=$((failed + 1))
    fi
    exec 9>&-
done 3<<<"$listed"

if [[ $detach -eq 1 ]]; then
    echo "repos-bootstrap: $started started, $running running, $stale stale, $unchanged unchanged, $skipped skipped, $refused refused, $failed failed"
else
    echo "repos-bootstrap: $ran ran, $unchanged unchanged, $skipped skipped, $refused refused, $failed failed"
fi
if [[ $failed -gt 0 || $refused -gt 0 ]]; then
    exit 1
fi
