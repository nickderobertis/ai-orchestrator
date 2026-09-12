# shellcheck shell=bash
# The ONE source of the install lock both self-heals serialize on.
#
# `scripts/workspace-install.sh` and `scripts/onetaskgraph-install.sh` provision two
# different things and lock in the same place, `<root>/.logs`. One implementation
# rather than one per installer, so neither can be hardened without the other:
# `tests/test_install_lock_source.py` holds both to it.
#
# Every step below either creates its own object or acts on a descriptor it has already
# opened and read back, because checking a path and then operating on it again is two
# lookups, and what answers the first is not what receives the second.
#
# Each lock holds two descriptors for the life of the sourcing shell: the directory,
# re-bound to its own name once the lock is taken, and the lock file `flock` locks.
# The first lock a shell takes holds 8 and 9 and a second holds 6 and 7, since closing
# a descriptor releases the `flock` on it; a forced workspace install over a link into
# another checkout's tree holds that tree's lock and its own at once. A third is
# refused. A caller that needs its own descriptors stays off those four, and arms no
# EXIT trap of its own: the fallback below releases its mutex directories from the
# sourcing shell's one EXIT trap, and `trap` replaces rather than chains.
# `tests/test_install_lock_source.py` holds both installers to that.
#
# Strict mode is established here rather than inherited from the sourcing caller,
# exactly as scripts/credentials-env.sh and scripts/codex-alt-home.sh do: a failure in
# the resolution below must abort rather than fall through to provisioning unserialized.
set -euo pipefail

#: How long the `flock`-less fallback waits, as a poll interval and a count of them.
#: `flock` waits forever because the kernel releases its lock when the holder dies; a
#: directory is released by nothing, so this one is bounded and says what to remove when
#: it expires.
INSTALL_LOCK_POLL_SECONDS=0.2
INSTALL_LOCK_WAIT_SECONDS=120
INSTALL_LOCK_POLLS=600
#: How many polls in a row must find the mutex absent before an absence is read as a
#: `mkdir` that cannot succeed rather than as a race this caller lost. One is not enough,
#: because losing the race and the winner releasing before the loser looks is an ordinary
#: interleaving rather than a fault; a `mkdir` that genuinely cannot create fails this way
#: every time, so it reaches this bound in well under a second and still refuses.
INSTALL_LOCK_ABSENCES=3

#: The caller and every fallback mutex taken, held outside the function so the EXIT trap
#: that releases the mutexes can still name them once that function has returned.
INSTALL_LOCK_CALLER=
INSTALL_LOCK_MUTEXES=()
#: How many locks this shell holds, which is what picks the descriptor pair below.
INSTALL_LOCK_HELD=0
#: The most locks one shell can hold: the descriptor pairs are spelled out, because a
#: redirection's descriptor is a literal in the bash 3.2 this file has to parse on —
#: `exec {fd}<` is 4.1 — and two is what the forced install over a shared tree needs.
INSTALL_LOCK_LIMIT=2

# What one `stat` reports, from whichever this platform has: `-c` is GNU and `-f` is
# BSD/macOS. `-L` so a descriptor under /dev/fd is read as what it holds open.
install_lock_stat() {
    local answer
    answer=$(stat -L -c "$1" "$3" 2>/dev/null || stat -L -f "$2" "$3" 2>/dev/null) || return 1
    # Folded here rather than by `${answer,,}` at each reader: that operator is bash 4.0
    # and the stock macOS this fallback exists for ships 3.2, where it is a syntax error.
    printf '%s\n' "$answer" | tr '[:upper:]' '[:lower:]'
}

# Open `$2` for reading on descriptor `$1`, in the sourcing shell: `exec` with only a
# redirection binds the caller's own descriptor table, and a function runs in it. Spelled
# per descriptor because the number in a redirection is a literal, and the four here are
# the whole of what `install_lock_take` hands out. Returns what the redirection did, so a
# path that cannot be opened is a status the caller names rather than a silent death.
install_lock_open() {
    case $1 in
        6) exec 6<"$2" ;;
        7) exec 7<"$2" ;;
        8) exec 8<"$2" ;;
        9) exec 9<"$2" ;;
        *)
            echo "install_lock_open: descriptor '$1' is not one this helper holds a lock on; this is a defect in scripts/install-lock.sh rather than in the checkout, so report it" >&2
            return 1
            ;;
    esac
}

# Release every fallback mutex this shell took, in the EXIT trap the fallback arms.
install_lock_release_mutexes() {
    local mutex
    # Never empty here: the trap that calls this is armed only after the first mutex
    # is recorded, so the expansion is quoted whole and needs no empty-array guard.
    for mutex in "${INSTALL_LOCK_MUTEXES[@]}"; do
        rmdir "$mutex" 2>/dev/null || echo "$INSTALL_LOCK_CALLER: could not release the install lock ${mutex}; remove that directory, or the next install waits ${INSTALL_LOCK_WAIT_SECONDS}s for one nothing holds" >&2
    done
}

# Take the install lock named `$3` under `$2`'s `.logs`, for the caller named `$1`.
#
# Returns 0 holding it, or 1 having said on stderr what could not be done and what an
# operator does about it. Provisioning unserialized is the one thing a caller must not do
# instead, so every way this can refuse names itself — including the `exec` redirections
# a script otherwise dies on without a word.
install_lock_take() {
    # Named the way scripts/credentials-env.sh names its own required input: a caller
    # that forgot one would otherwise abort on `1: unbound variable`, which says nothing
    # about which helper was called wrong.
    local caller=${1:?install_lock_take: the name of the calling installer is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/workspace-install.sh passes workspace-install, then retry}
    local repo_root=${2:?install_lock_take: the checkout the lock lives in is required; pass its root as the second argument, then retry}
    local name=${3:?install_lock_take: the name of the lock file is required, so two installers of one checkout do not serialize on each other; pass it as the third argument, then retry}
    local lock_dir="$repo_root/.logs" lock_file mutex dir_fd file_fd held held_mode opened opened_links polls absences
    lock_file="$lock_dir/$name"
    mutex="$lock_file.d"
    INSTALL_LOCK_CALLER=$caller

    # Which pair this lock is held on, decided by how many this shell already holds:
    # taking a second lock on the first one's descriptors would close them, and closing
    # the descriptor `flock` locked is what releases that lock.
    case $INSTALL_LOCK_HELD in
        0) dir_fd=8 file_fd=9 ;;
        1) dir_fd=6 file_fd=7 ;;
        *)
            echo "$caller: this shell already holds $INSTALL_LOCK_HELD install locks and scripts/install-lock.sh holds at most $INSTALL_LOCK_LIMIT; a third is a defect in the caller rather than in the checkout, so report it" >&2
            return 1
            ;;
    esac

    if ! mkdir -m 700 "$lock_dir" 2>/dev/null; then
        # `mkdir` refuses an existing path outright, a symbolic link included rather than
        # followed, so nothing below is reached by a link having been resolved.
        if [[ -L "$lock_dir" ]]; then
            echo "$caller: '$lock_dir' is a symbolic link, and this wrapper secures that directory and writes the install lock inside it rather than following a link out of the checkout; replace it with a directory of its own and retry" >&2
            return 1
        fi
        if [[ -e "$lock_dir" && ! -d "$lock_dir" ]]; then
            echo "$caller: cannot prepare '$lock_dir' for the install lock: it exists and is not a directory, so the lock has nowhere to live; move whatever is at that path aside and retry" >&2
            return 1
        fi
        if [[ ! -d "$lock_dir" ]]; then
            echo "$caller: cannot prepare '$lock_dir' for the install lock; repair its parent's permissions and retry" >&2
            return 1
        fi
    fi
    # Opened once and read back, whichever way it got here — created just now or already
    # there. Every question about this directory is asked of the descriptor from here, so a
    # path swapped after the tests above answers none of them.
    if ! install_lock_open "$dir_fd" "$lock_dir"; then
        echo "$caller: cannot open '$lock_dir' to secure it for the install lock; repair its permissions and retry" >&2
        return 1
    fi
    if ! held=$(install_lock_stat '%F %a' '%HT %Lp' "/dev/fd/$dir_fd"); then
        echo "$caller: cannot read back what '$lock_dir' opened as, so nothing here can establish it is a directory this checkout may lock in; repair that path and retry" >&2
        return 1
    fi
    # Matched whole and split on the one space both formats put between their two fields, so
    # a `stat` that answered something else — a diagnostic on stdout, a format this platform
    # does not know, a field it left empty — is refused here rather than having a mode read
    # off its tail. `directory` is the whole of the type either format spells for one.
    held_mode=${held#directory }
    if [[ $held == "$held_mode" ]]; then
        echo "$caller: '$lock_dir' opened as '$held' rather than a directory, so it was replaced while this wrapper was preparing it; retry" >&2
        return 1
    fi
    case $held_mode in
        "" | *[!0-7]*)
            echo "$caller: this platform's stat reported '$lock_dir' as '$held', whose mode field is not octal, so nothing here can establish it is secured; report that stat's output and retry" >&2
            return 1
            ;;
    esac
    # Bound back to the name, as the lock file's descriptor is below: being a directory is
    # not being *this checkout's* directory, and everything after this — the securing, and
    # the lock file created inside it — is reached through `$lock_dir` again. `-L` catches a
    # link put there since, and `-ef` catches a different directory swapped in, which would
    # leave this holding one inode while the path named another.
    if [[ -L "$lock_dir" ]] || [[ ! "$lock_dir" -ef "/dev/fd/$dir_fd" ]]; then
        echo "$caller: '$lock_dir' no longer names the directory this wrapper opened, so it was replaced while this wrapper was preparing it; retry" >&2
        return 1
    fi
    # Secured only when it is not already, so a directory this wrapper just made at 700
    # performs no privileged step at all — and the one that does performs it on the
    # descriptor above rather than on the name.
    if [[ $held_mode != 700 ]] && ! chmod 700 "/dev/fd/$dir_fd"; then
        echo "$caller: '$lock_dir' is mode $held_mode rather than 700 and this user cannot secure it; run 'chmod 700 $lock_dir' as its owner and retry" >&2
        return 1
    fi

    # Asked before the creation below, and it is bash's `noclobber` that makes the order
    # matter: on a path that exists and is not a regular file it does not refuse but falls
    # through to an ordinary `O_CREAT` open, and opening a fifo for writing waits for a
    # reader — so a wrapper that reached one here would hang rather than say anything. `-f`
    # resolves a link, so this covers a link at a fifo as well as a fifo.
    if [[ -e "$lock_file" && ! -f "$lock_file" ]]; then
        echo "$caller: '$lock_file' exists and is not a regular file, so this wrapper cannot serialize on it; move whatever is at that path aside and retry" >&2
        return 1
    fi
    # Created with O_EXCL, which refuses a symbolic link rather than creating what one
    # points at — so this and not the open below is what first meets a link at this path,
    # and no file outside the checkout is brought into existence by either. Failing is
    # ordinary otherwise: it means an install ran here before.
    # llmlint: ignore[boundary_inputs_validated] Creating through the name rather than the held directory descriptor: portable shell has no `openat`, and `/dev/fd/N/<name>` is Linux's alone. `O_EXCL` refuses a link here, the descriptor opened below is read back and bound to this name, and the directory is re-bound once the lock is held, which `test_the_self_heal_names_the_serialization_it_could_not_take[swapped-lock-directory]` drives.
    if ! (set -C; : >"$lock_file") 2>/dev/null; then
        if [[ -L "$lock_file" ]]; then
            echo "$caller: the install lock path '$lock_file' is a symbolic link, and this wrapper creates and opens that path itself rather than following a link out of the checkout; remove it and retry" >&2
            return 1
        fi
    fi
    # Guarded, so a lock file that turned unreadable names the path rather than dying
    # silently. Read-only, because nothing writes into this lock — `flock` locks whatever
    # mode the descriptor was opened in — and write access over an inode not yet
    # established as this checkout's own is authority a hard link elsewhere would abuse.
    # The trade: a fifo raced in behind the shape check above waits here rather than
    # being refused below, a same-user race inside a directory held at 700, against a
    # hard link that needs no race at all.
    if ! install_lock_open "$file_fd" "$lock_file"; then
        echo "$caller: cannot open the install lock at '$lock_file'; repair its permissions and retry" >&2
        return 1
    fi
    # Read back off the descriptor rather than the name, so what is established is true of
    # the inode this wrapper is holding open, and read off a descriptor that asked for no
    # more than reading. A hard link is the case the count catches: it is no symbolic link
    # and no irregular file, and it shares its inode with a name that may sit outside this
    # checkout entirely.
    if ! opened=$(install_lock_stat '%F %h' '%HT %l' "/dev/fd/$file_fd"); then
        echo "$caller: cannot read back what the install lock at '$lock_file' opened as, so nothing here can establish it is this checkout's own; remove it and retry" >&2
        return 1
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
        echo "$caller: the install lock at '$lock_file' opened as '$opened' rather than a regular file, so this wrapper cannot serialize on it; move whatever is at that path aside and retry" >&2
        return 1
    fi
    case $opened_links in
        "" | *[!0-9]*)
            echo "$caller: this platform's stat reported the install lock at '$lock_file' as '$opened', whose link-count field is not a number, so nothing here can establish the inode it holds is this checkout's own; report that stat's output and retry" >&2
            return 1
            ;;
    esac
    if [[ $opened_links == 0 ]]; then
        echo "$caller: the install lock at '$lock_file' was unlinked while this wrapper was opening it, so nothing that comes along later would serialize against the descriptor it is holding; retry" >&2
        return 1
    fi
    if [[ $opened_links != 1 ]]; then
        echo "$caller: the install lock at '$lock_file' has $opened_links names rather than one, so the inode this wrapper opened is also reachable outside this checkout; remove it and retry" >&2
        return 1
    fi
    # The last thing the name can still do is not be what was opened. Bash cannot open a
    # path without following a link at the leaf, so the name is bound to the descriptor
    # afterwards instead: `-L` catches a link that is there now, and `-ef` catches one that
    # was there at the open and has since been put back — the name would then resolve to
    # some other inode than the one being held. Both are pure bash, so both reach the 3.2
    # the fallback below exists for.
    if [[ -L "$lock_file" ]] || [[ ! "$lock_file" -ef "/dev/fd/$file_fd" ]]; then
        echo "$caller: '$lock_file' no longer names the inode this wrapper opened as the install lock, so it was replaced while this wrapper was preparing it; retry" >&2
        return 1
    fi

    # `flock` is util-linux and is not on a stock macOS, which this repository's installers
    # build release targets for, so it is preferred rather than required. The fallback is a
    # directory: `mkdir` is atomic on every filesystem this runs on, and the loser of the
    # race is the caller whose `mkdir` failed.
    if command -v flock >/dev/null 2>&1; then
        if ! flock "$file_fd"; then
            echo "$caller: cannot serialize the locked install: flock could not lock '$lock_file', which is the lock itself refusing rather than another install holding it, so check that this checkout's .logs is on a filesystem supporting locks and retry" >&2
            return 1
        fi
    else
        # Bounded, because a directory outlives the process that made it: `flock` releases
        # when its holder dies and this cannot, so a wait that never ended would inherit a
        # crashed install forever. The refusal names the directory to remove.
        polls=0
        absences=0
        # llmlint: ignore[boundary_inputs_validated] The same as at the lock file: portable shell has no `mkdirat`. A mutex made in a swapped directory is refused by the re-binding after acquisition, which `test_the_fallback_refuses_a_lock_directory_swapped_while_it_took_its_mutex` drives, and each shape this name can hold has its own refusal in the loop.
        until mkdir "$mutex" 2>/dev/null; do
            # Only a directory is an install holding this. Anything else there is something
            # waiting cannot resolve, and waiting the budget out would report it as a lock
            # that was never released — the one repair that cannot help, since no install
            # ever made it. Asked before the emptiness test below, because `-e` is false for
            # a dangling link and that path is occupied by one all the same.
            if [[ -L "$mutex" || ( -e "$mutex" && ! -d "$mutex" ) ]]; then
                echo "$caller: '$mutex' is where the install lock goes and it is not a directory, so no install is holding it and none can take it; move whatever is at that path aside and retry" >&2
                return 1
            fi
            # An absent path here is two states wearing one appearance, and only one of them
            # is a fault. The `mkdir` above failed because a peer held this, and a peer that
            # released it in the moment between that failure and this test leaves nothing
            # behind to find — so an absent path is ordinarily the race being lost, and
            # refusing on the first sight of one reports a permissions fault that does not
            # exist. What parts them is whether it persists: a `mkdir` that cannot create
            # this fails the same way on every poll and reaches the bound below in under a
            # second, where a lost race is answered by the very next `mkdir`. The count is
            # reset rather than accumulated, because an install taken and released while this
            # waits is this loop working rather than evidence toward a refusal.
            if [[ ! -e "$mutex" ]]; then
                absences=$((absences + 1))
                if ((absences >= INSTALL_LOCK_ABSENCES)); then
                    echo "$caller: cannot create the install lock '$mutex', and nothing is holding it; repair the permissions of '$lock_dir' and retry" >&2
                    return 1
                fi
            else
                absences=0
            fi
            if ((polls >= INSTALL_LOCK_POLLS)); then
                echo "$caller: waited ${INSTALL_LOCK_WAIT_SECONDS}s for the install lock '$mutex' and it was never released; if no other install is running then one died holding it, so remove that directory and retry" >&2
                return 1
            fi
            if ! sleep "$INSTALL_LOCK_POLL_SECONDS"; then
                echo "$caller: cannot wait for the install lock '$mutex' because sleep failed, so this would spin rather than wait; repair that command on this PATH and retry" >&2
                return 1
            fi
            polls=$((polls + 1))
        done
        # Held by the sourcing shell, so it is released when that shell ends however it ends.
        # Recorded before the trap is armed, and the trap reads the record rather than
        # this call's own path: a second lock taken in this shell is a second mutex the
        # one trap has to release, and re-arming it would drop the first.
        INSTALL_LOCK_MUTEXES[${#INSTALL_LOCK_MUTEXES[@]}]=$mutex
        trap install_lock_release_mutexes EXIT
    fi

    # Both the lock file and the fallback mutex are created through `$lock_dir` again, and no
    # portable shell can create *inside* a descriptor: `openat` has no `sh` spelling, and the
    # `/dev/fd/8/name` form that would stand in for one resolves this way on Linux and not on
    # the stock macOS this file's `flock` fallback and BSD `stat` exist for. So the directory
    # is bound to the descriptor once more now that the lock is held, which is the last moment
    # a swap behind any of that can still be caught. What it prevents is the harm rather than
    # the swap: a lock taken somewhere this wrapper does not hold serializes against nothing,
    # so the next caller would install concurrently with this one. Refusing costs a retry.
    if [[ -L "$lock_dir" ]] || [[ ! "$lock_dir" -ef "/dev/fd/$dir_fd" ]]; then
        echo "$caller: '$lock_dir' was replaced while this wrapper was taking the install lock, so what it holds is not the lock a second install would wait on and provisioning now would race one; retry" >&2
        return 1
    fi
    INSTALL_LOCK_HELD=$((INSTALL_LOCK_HELD + 1))
}
