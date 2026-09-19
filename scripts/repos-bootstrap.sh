#!/usr/bin/env bash
# `just repos-bootstrap` — run each registered sibling checkout's own `just bootstrap`
# ahead of the dispatch that will publish through its gate (#1140). Session setup calls
# it last; docs/host-setup.md, "The sibling gates", is the operator's account of what it
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
usage: repos-bootstrap.sh [--checkouts FILE]

Run each registered sibling checkout's own `just bootstrap`, once per change to that
checkout's HEAD or bootstrap inputs, one caller at a time per checkout.

  --checkouts FILE  Read the checkout list from FILE (default config/onevcs.checkouts,
                    or the file ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS names).

Prints one line per listed checkout and a summary; exits 1 when any bootstrap failed
or was refused, and 0 otherwise. The memo is under
`${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/repos-bootstrap/`.
USAGE
}

checkouts_file="$repo_root/config/onevcs.checkouts"
if [[ -n ${!CHECKOUTS_VARIABLE:-} ]]; then
    checkouts_file=${!CHECKOUTS_VARIABLE}
fi
while [[ $# -gt 0 ]]; do
    case $1 in
        --checkouts) [[ $# -ge 2 ]] || { usage; exit 2; }; checkouts_file=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "repos-bootstrap: unknown argument '$1'" >&2; usage; exit 2 ;;
    esac
done

if [[ ! -f $checkouts_file ]]; then
    echo "repos-bootstrap: checkout list $checkouts_file does not exist" >&2
    exit 2
fi

if [[ -n ${!NESTED_VARIABLE:-} ]]; then
    echo "repos-bootstrap: nested inside another repos-bootstrap, which is provisioning the siblings; nothing to do here"
    exit 0
fi

# llmlint: ignore[changed_behavior_has_e2e] Every host this runs on has all four — `just` is what invoked it, `git` and `sha256sum` are what every recipe here assumes, and `flock` ships with util-linux — so a journey would have to build a PATH holding `just` and `bash` but not `flock`, to prove that `command -v` answers absence, which is bash's behaviour rather than this script's.
for required in just flock sha256sum git; do
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

ran=0 unchanged=0 skipped=0 refused=0 failed=0
report() {
    printf '  %-10s %-20s %s\n' "$1" "$2" "$3"
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
    # llmlint: ignore[changed_behavior_has_e2e] Reaching the refusal below means another caller held one checkout's bootstrap for half an hour; a journey could arrange that only by holding the lock itself for that long, which would prove what `flock -w` does rather than what this script reports.
    if ! flock -w "$LOCK_WAIT_SECONDS" 9; then
        report refused "held over ${LOCK_WAIT_SECONDS}s" "$path  (another caller's bootstrap; log: $log)"
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
        report unchanged "" "$path"
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
    # Exactly the repository's own bootstrap, from its own root, in this environment
    # plus the nesting guard. `VIRTUAL_ENV` is dropped because a caller running under
    # this checkout's `uv run` carries it, and a sibling's `uv sync` would otherwise
    # be told to target an environment that is not its own. Its stdin is `/dev/null`
    # so that a bootstrap that prompts reads end-of-file rather than waiting on a
    # session hook's terminal, and fails where its log can say so.
    if (cd "$checkout" && env -u VIRTUAL_ENV "$NESTED_VARIABLE=1" just \
        --justfile "$(own_justfile "$checkout")" --working-directory "$checkout" bootstrap) \
        </dev/null >"$log" 2>&1; then
        # llmlint: ignore[changed_behavior_has_e2e] The third of the three writes the comment above the lock file covers.
        if printf '%s\n' "$stamp" >"$memo_dir/stamp"; then
            report ran "" "$path  (log: $log)"
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
        report failed "exit $status" "$path  (log: $log)"
        failed=$((failed + 1))
    fi
    exec 9>&-
done 3<<<"$listed"

echo "repos-bootstrap: $ran ran, $unchanged unchanged, $skipped skipped, $refused refused, $failed failed"
if [[ $failed -gt 0 || $refused -gt 0 ]]; then
    exit 1
fi
