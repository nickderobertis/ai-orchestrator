#!/usr/bin/env bash
# The self-heal for this checkout's own plan-store CLI, and the third of the three
# `scripts/nx.sh` performs beside `workspace-install.sh` and `python-install.sh`.
#
# Provisioning installs the standalone `onetaskgraph` release archive into
# `<root>/.venv/bin` rather than a directory the whole host shares, so the binary a
# checkout reads is the release *it* pinned. That destination is ignored state, which
# a freshly created worktree and a publication clone arrive without — and unlike the
# shared path it replaced, nothing else on the host puts one there. Session setup runs
# on a `SessionStart` hook and reaches neither of those, so without this the gate a
# publication runs in its own clone resolved no CLI at all and every recipe and test
# that reads the plan store failed with "No such file or directory".
#
# The install itself is not duplicated here: `scripts/session-setup.sh` defines it and
# returns when sourced, so this is the entry point and that file stays the one source
# of what an install does and which release it adopts. It exits immediately once the
# binary already reports this checkout's pin, which is one `--version` and no network.
set -euo pipefail

# Under errexit, a `cd` or `pwd` that fails ends this here rather than leaving the
# resolution below reading a path that is not this script's own.
script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

if [[ $# -gt 0 ]]; then
    echo "onetaskgraph-install: expected no arguments, got '$*'; rerun it with none — the install it performs is already idempotent, so there is no flag to reinstall or refresh with" >&2
    exit 2
fi

# uv's own "the environment is provided, do not touch it" signal, read exactly as
# `scripts/python-install.sh` reads it: this binary lives in that environment, so a
# caller that pointed uv at one it provisioned elsewhere is not installed over.
if [[ -n ${UV_NO_SYNC+set} ]]; then
    # Spelled as case-insensitive globs rather than folded with `${UV_NO_SYNC,,}`: that
    # operator is bash 4.0, and on the stock macOS bash 3.2 this fallback below exists
    # for it is a parse error — which would take the whole file, not just this branch.
    case "$UV_NO_SYNC" in
        1 | [Tt][Rr][Uu][Ee] | [Yy][Ee][Ss] | [Oo][Nn] | [Yy]) exit 0 ;;
        0 | [Ff][Aa][Ll][Ss][Ee] | [Nn][Oo] | [Oo][Ff][Ff] | [Nn]) ;;
        *)
            echo "onetaskgraph-install: UV_NO_SYNC='$UV_NO_SYNC' is not a value uv reads; set it to 1/true/yes/on/y to keep the environment you provided, to 0/false/no/off/n to provision this checkout's own, or leave it unset" >&2
            exit 2
            ;;
    esac
fi

# A workspace declaring no adopted release has no plan-store CLI to provision.
# `tests/fixtures/nx-cache` is one: the cache contract is proven by copying this
# wrapper chain into a TypeScript-only tree and running it for real.
if [[ ! -f "$repo_root/config/onetaskgraph.version" ]]; then
    exit 0
fi

lock_dir="$repo_root/.logs"
lock_file="$lock_dir/onetaskgraph-install.lock"

# What one of these reports, from whichever `stat` this platform has: `-c` is GNU and
# `-f` is BSD/macOS. `-L` so a descriptor under /dev/fd is read as what it holds open.
stat_of() {
    local answer
    answer=$(stat -L -c "$1" "$3" 2>/dev/null || stat -L -f "$2" "$3" 2>/dev/null) || return 1
    # Folded here rather than by `${answer,,}` at each reader: that operator is bash 4.0
    # and the stock macOS this fallback exists for ships 3.2, where it is a syntax error.
    printf '%s\n' "$answer" | tr '[:upper:]' '[:lower:]'
}

# Concurrent callers serialize here because nothing beneath this does: the install
# writes one destination and re-verifies it, so a loser reads what a winner is writing.
# Below the pin check rather than above it, so a workspace with nothing to provision
# gains no lock directory from an Nx invocation.
#
# Every step below either creates its own object or acts on a descriptor it has already
# opened and read back. Checking a path and then operating on that path again is two
# lookups, and what answers the first is not what receives the second — which is how a
# `chmod` comes to secure, and an open to open, something swapped in between the two.
if ! mkdir -m 700 "$lock_dir" 2>/dev/null; then
    # `mkdir` refuses an existing path outright, a symbolic link included rather than
    # followed, so nothing below is reached by a link having been resolved.
    if [[ -L "$lock_dir" ]]; then
        echo "onetaskgraph-install: '$lock_dir' is a symbolic link, and this wrapper secures that directory and writes the install lock inside it rather than following a link out of the checkout; replace it with a directory of its own and retry" >&2
        exit 1
    fi
    if [[ -e "$lock_dir" && ! -d "$lock_dir" ]]; then
        echo "onetaskgraph-install: '$lock_dir' exists and is not a directory, so the install lock has nowhere to live; move whatever is at that path aside and retry" >&2
        exit 1
    fi
    if [[ ! -d "$lock_dir" ]]; then
        echo "onetaskgraph-install: cannot prepare '$lock_dir' for the install lock; repair its parent's permissions and retry" >&2
        exit 1
    fi
fi
# Opened once and read back, whichever way it got here — created just now or already
# there. Every question about this directory is asked of the descriptor from here, so a
# path swapped after the tests above answers none of them.
if ! exec 8<"$lock_dir"; then
    echo "onetaskgraph-install: cannot open '$lock_dir' to secure it for the install lock; repair its permissions and retry" >&2
    exit 1
fi
if ! held=$(stat_of '%F %a' '%HT %Lp' /dev/fd/8); then
    echo "onetaskgraph-install: cannot read back what '$lock_dir' opened as, so nothing here can establish it is a directory this checkout may lock in; repair that path and retry" >&2
    exit 1
fi
# Matched whole and split on the one space both formats put between their two fields, so
# a `stat` that answered something else — a diagnostic on stdout, a format this platform
# does not know, a field it left empty — is refused here rather than having a mode read
# off its tail. `directory` is the whole of the type either format spells for one.
held_mode=${held#directory }
if [[ $held == "$held_mode" ]]; then
    echo "onetaskgraph-install: '$lock_dir' opened as '$held' rather than a directory, so it was replaced while this wrapper was preparing it; retry" >&2
    exit 1
fi
case $held_mode in
    "" | *[!0-7]*)
        echo "onetaskgraph-install: this platform's stat reported '$lock_dir' as '$held', whose mode field is not octal, so nothing here can establish it is secured; report that stat's output and retry" >&2
        exit 1
        ;;
esac
# Bound back to the name, as the lock file's descriptor is below: being a directory is
# not being *this checkout's* directory, and everything after this — the securing, and
# the lock file created inside it — is reached through `$lock_dir` again. `-L` catches a
# link put there since, and `-ef` catches a different directory swapped in, which would
# leave this holding one inode while the path named another.
if [[ -L "$lock_dir" ]] || [[ ! "$lock_dir" -ef /dev/fd/8 ]]; then
    echo "onetaskgraph-install: '$lock_dir' no longer names the directory this wrapper opened, so it was replaced while this wrapper was preparing it; retry" >&2
    exit 1
fi
# Secured only when it is not already, so a directory this wrapper just made at 700
# performs no privileged step at all — and the one that does performs it on the
# descriptor above rather than on the name.
if [[ $held_mode != 700 ]] && ! chmod 700 /dev/fd/8; then
    echo "onetaskgraph-install: '$lock_dir' is mode $held_mode rather than 700 and this user cannot secure it; run 'chmod 700 $lock_dir' as its owner and retry" >&2
    exit 1
fi

# Asked before the creation below, and it is bash's `noclobber` that makes the order
# matter: on a path that exists and is not a regular file it does not refuse but falls
# through to an ordinary `O_CREAT` open, and opening a fifo for writing waits for a
# reader — so a wrapper that reached one here would hang rather than say anything. `-f`
# resolves a link, so this covers a link at a fifo as well as a fifo.
if [[ -e "$lock_file" && ! -f "$lock_file" ]]; then
    echo "onetaskgraph-install: '$lock_file' exists and is not a regular file, so this wrapper cannot serialize on it; move whatever is at that path aside and retry" >&2
    exit 1
fi
# Created with O_EXCL, which refuses a symbolic link rather than creating what one
# points at — so this and not the open below is what first meets a link at this path,
# and no file outside the checkout is brought into existence by either. Failing is
# ordinary otherwise: it means an install ran here before.
# llmlint: ignore[boundary_inputs_validated] The finding here asks this creation to be bound to the `$lock_dir` descriptor rather than reached through its name, and no portable shell can do that: `openat` has no `sh` spelling, and `/dev/fd/8/onetaskgraph-install.lock` resolves relative to the held directory on Linux and not on the stock macOS this file's `flock` fallback and BSD `stat` exist for — so taking it would trade away the portability this wrapper is built for. What can be answered portably is answered: `O_EXCL` refuses a link at this path rather than creating what it points at, the descriptor opened below asks only to read and is read back and bound to this name, and the directory is bound to its own descriptor again once the lock is held, so a swap in this window is refused before anything is provisioned rather than installed under. `test_the_self_heal_names_the_serialization_it_could_not_take[swapped-lock-directory]` drives that.
if ! (set -C; : >"$lock_file") 2>/dev/null; then
    if [[ -L "$lock_file" ]]; then
        echo "onetaskgraph-install: the install lock path '$lock_file' is a symbolic link, and this wrapper creates and opens that path itself rather than following a link out of the checkout; remove it and retry" >&2
        exit 1
    fi
fi
# Guarded, not assumed, for the reason its sibling states: bash reports a failed `exec`
# redirection rather than taking the shell down with it, so a lock file that turned
# unreadable still names the path instead of dying silently.
#
# Read-only, and asking for no more than that is the whole of why: nothing writes into
# this lock — `flock` takes its lock from the descriptor whatever mode it was opened in,
# and the fallback below locks with a directory instead — so write access is authority
# this wrapper has no use for, over an inode it has not yet established is its own. A
# hard link is what makes that concrete: the checks below refuse a second name for the
# file, but under `9<>` they ran after the open had already taken write access on
# whatever else names that inode, and a read-only one refused the whole wrapper with
# `cannot open` rather than with the refusal that fits.
#
# What that gives up is the reason this was `9<>` before: opening a fifo for reading
# waits for a writer where read-write does not, so a fifo raced in behind the shape
# check above waits here rather than being refused below. That is a same-user race
# inside a directory this wrapper holds at 700 and has bound to its descriptor, against
# a hard link that needs no race at all, so the trade goes this way.
if ! exec 9<"$lock_file"; then
    echo "onetaskgraph-install: cannot open the install lock at '$lock_file'; repair its permissions and retry" >&2
    exit 1
fi
# Read back off the descriptor rather than the name, so what is established is true of
# the inode this wrapper is holding open, and read off a descriptor that asked for no
# more than reading. A hard link is the case the count catches: it is no symbolic link
# and no irregular file, and it shares its inode with a name that may sit outside this
# checkout entirely.
if ! opened=$(stat_of '%F %h' '%HT %l' /dev/fd/9); then
    echo "onetaskgraph-install: cannot read back what the install lock at '$lock_file' opened as, so nothing here can establish it is this checkout's own; remove it and retry" >&2
    exit 1
fi
# Matched whole against the two types that are this file, so a `stat` that answered
# something else — a diagnostic on stdout, a format this platform does not know, a field
# it left empty — is refused rather than having a link count read off its tail. GNU spells
# a zero-length one `regular empty file`, and this lock is created empty and stays that way.
opened_links=${opened#regular file }
if [[ $opened == "$opened_links" ]]; then
    opened_links=${opened#regular empty file }
fi
if [[ $opened == "$opened_links" ]]; then
    echo "onetaskgraph-install: the install lock at '$lock_file' opened as '$opened' rather than a regular file, so this wrapper cannot serialize on it; move whatever is at that path aside and retry" >&2
    exit 1
fi
case $opened_links in
    "" | *[!0-9]*)
        echo "onetaskgraph-install: this platform's stat reported the install lock at '$lock_file' as '$opened', whose link-count field is not a number, so nothing here can establish the inode it holds is this checkout's own; report that stat's output and retry" >&2
        exit 1
        ;;
esac
if [[ $opened_links == 0 ]]; then
    echo "onetaskgraph-install: the install lock at '$lock_file' was unlinked while this wrapper was opening it, so nothing that comes along later would serialize against the descriptor it is holding; retry" >&2
    exit 1
fi
if [[ $opened_links != 1 ]]; then
    echo "onetaskgraph-install: the install lock at '$lock_file' has $opened_links names rather than one, so the inode this wrapper opened is also reachable outside this checkout; remove it and retry" >&2
    exit 1
fi
# The last thing the name can still do is not be what was opened. Bash cannot open a
# path without following a link at the leaf, so the name is bound to the descriptor
# afterwards instead: `-L` catches a link that is there now, and `-ef` catches one that
# was there at the open and has since been put back — the name would then resolve to
# some other inode than the one being held. Both are pure bash, so both reach the 3.2
# the fallback below exists for.
if [[ -L "$lock_file" ]] || [[ ! "$lock_file" -ef /dev/fd/9 ]]; then
    echo "onetaskgraph-install: '$lock_file' no longer names the inode this wrapper opened as the install lock, so it was replaced while this wrapper was preparing it; retry" >&2
    exit 1
fi

# `flock` is util-linux and is not on a stock macOS, which this installer builds a
# release target for, so it is preferred rather than required. The fallback is a
# directory: `mkdir` is atomic on every filesystem this runs on, and the loser of the
# race is the caller whose `mkdir` failed.
mutex_dir="$lock_dir/onetaskgraph-install.lock.d"
#: How long the fallback waits, as a poll interval and a count of them. `flock` waits
#: forever because the kernel releases its lock when the holder dies; a directory is
#: released by nothing, so this one is bounded and says what to remove when it expires.
LOCK_POLL_SECONDS=0.2
LOCK_WAIT_SECONDS=120
LOCK_POLLS=600
if command -v flock >/dev/null 2>&1; then
    if ! flock 9; then
        echo "onetaskgraph-install: flock could not lock '$lock_file'; that is the lock itself refusing rather than another install holding it, so check that this checkout's .logs is on a filesystem supporting locks and retry" >&2
        exit 1
    fi
else
    # Bounded, because a directory outlives the process that made it: `flock` releases
    # when its holder dies and this cannot, so a wait that never ended would inherit a
    # crashed install forever. The refusal names the directory to remove.
    polls=0
    # llmlint: ignore[boundary_inputs_validated] The same demand as at the lock file above, and the same answer: binding this creation to the `$lock_dir` descriptor needs `mkdirat`, which portable shell has no spelling for, and the `/dev/fd/8/name` form that would stand in for one is Linux's alone. A mutex made in a directory swapped out from under this is refused by the binding below the acquisition rather than provisioned under, which `test_the_fallback_refuses_a_lock_directory_swapped_while_it_took_its_mutex` drives, and the shapes this path can meet at that name — a link, a non-directory, an absent one, one never released — each have their own refusal in the loop below.
    until mkdir "$mutex_dir" 2>/dev/null; do
        # Only a directory is an install holding this. Anything else there is something
        # waiting cannot resolve, and waiting the budget out would report it as a lock
        # that was never released — the one repair that cannot help, since no install
        # ever made it. Asked before the emptiness test below, because `-e` is false for
        # a dangling link and that path is occupied by one all the same.
        if [[ -L "$mutex_dir" || ( -e "$mutex_dir" && ! -d "$mutex_dir" ) ]]; then
            echo "onetaskgraph-install: '$mutex_dir' is where the install lock goes and it is not a directory, so no install is holding it and none can take it; move whatever is at that path aside and retry" >&2
            exit 1
        fi
        if [[ ! -e "$mutex_dir" ]]; then
            echo "onetaskgraph-install: cannot create the install lock '$mutex_dir', and nothing is holding it; repair the permissions of '$lock_dir' and retry" >&2
            exit 1
        fi
        if ((polls >= LOCK_POLLS)); then
            echo "onetaskgraph-install: waited ${LOCK_WAIT_SECONDS}s for the install lock '$mutex_dir' and it was never released; if no other install is running then one died holding it, so remove that directory and retry" >&2
            exit 1
        fi
        if ! sleep "$LOCK_POLL_SECONDS"; then
            echo "onetaskgraph-install: cannot wait for the install lock '$mutex_dir' because sleep failed, so this would spin rather than wait; repair that command on this PATH and retry" >&2
            exit 1
        fi
        polls=$((polls + 1))
    done
    # Held by this shell, so it is released when this shell ends however it ends.
    trap 'rmdir "$mutex_dir" 2>/dev/null || echo "onetaskgraph-install: could not release the install lock ${mutex_dir}; remove that directory, or the next install waits ${LOCK_WAIT_SECONDS}s for one nothing holds" >&2' EXIT
fi

# Both the lock file and the fallback mutex are created through `$lock_dir` again, and no
# portable shell can create *inside* a descriptor: `openat` has no `sh` spelling, and the
# `/dev/fd/8/name` form that would stand in for one resolves this way on Linux and not on
# the stock macOS this file's `flock` fallback and BSD `stat` exist for. So the directory
# is bound to the descriptor once more now that the lock is held, which is the last moment
# a swap behind any of that can still be caught. What it prevents is the harm rather than
# the swap: a lock taken somewhere this wrapper does not hold serializes against nothing,
# so the next caller would install concurrently with this one. Refusing costs a retry.
if [[ -L "$lock_dir" ]] || [[ ! "$lock_dir" -ef /dev/fd/8 ]]; then
    echo "onetaskgraph-install: '$lock_dir' was replaced while this wrapper was taking the install lock, so what it holds is not the lock a second install would wait on and provisioning now would race one; retry" >&2
    exit 1
fi

# Sourced rather than run: the guard at the foot of that file returns once its
# functions and its adopted versions are defined, without provisioning anything else.
# Validated first, as every other wrapper here validates a helper it loads: a `source`
# of something that is not a readable regular file fails as shell noise naming nothing.
setup="$script_dir/session-setup.sh"
if [ ! -f "$setup" ] || [ ! -r "$setup" ]; then
    echo "onetaskgraph-install: required helper is not a readable regular file: $setup; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 1
fi

# That sourced guard is also why the two lines below carry a suppression. shellcheck
# inlines a followed file, reads its `return 0` as ending this script, and calls
# everything after the `source` unreachable — which is true of the copy it inlined and
# false of the one bash runs, where `return` only ends the sourcing.
# shellcheck source=scripts/session-setup.sh
# shellcheck disable=SC2317  # reached: that `return` ends the sourcing, not this script.
if ! source "$setup"; then
    echo "onetaskgraph-install: $setup is readable but refused to load; it declines an adopted version file it cannot read or parse and names which one above, so correct that file or run 'just bootstrap', then retry" >&2
    exit 1
fi
# That file turns errexit off for itself — session setup continues past optional
# failures deliberately — and this wrapper does not, so take it back.
# shellcheck disable=SC2317  # reached: that `return` ends the sourcing, not this script.
set -e

# shellcheck disable=SC2317  # reached: session-setup.sh's `return` ends the sourcing, not this script.
if ! install_onetaskgraph; then
    echo "onetaskgraph-install: could not provision onetaskgraph $ADOPTED_ONETASKGRAPH_VERSION into $ONETASKGRAPH_BIN; a download, a checksum or an unpack names itself above, and the steps that report nothing are a staging directory, a checksum tool, the final version check, and the plan root beside that binary — so check that this checkout's .venv/bin and .plans are writable. 'just session-setup' performs the same install" >&2
    exit 1
fi
