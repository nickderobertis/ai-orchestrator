#!/usr/bin/env bash
# Say whether a judged base has been left behind by its own origin ref.
#
# `just lint-llm-diff <base>` resolves whatever it is handed to a commit and judges the
# diff from there, and a name resolves silently however stale the ref behind it is.
# Inside a session clone that is the ordinary case rather than the exception: the local
# branch is cut once and the origin moves on, so a worker naming `main` is handed a
# verdict over commits its own branch does not carry — twelve of them where the branch
# had five, seven of those already landed. The two refs that disagree are the whole of
# what a reader needs, and neither the judge nor Nx can see the disagreement at all: the
# stale base resolves to a commit, that commit keys the cache, and the answer is a
# perfectly valid verdict over the wrong range.
#
# What this decides is one comparison and nothing else:
#
#   stdout empty, exit 0   nothing to say — the base names no origin ref of its own, the
#                          two are level, the base is ahead of it, or the two have
#                          diverged with neither containing the other
#   one line, exit 0       the base is a strict ancestor of its own origin ref, which is
#                          the ref having moved past it; the line names both refs and
#                          both commits, and the caller adds its own remedy
#   exit 2                 no comparison was made — this was called with no base, or git
#                          failed rather than answering; the reason is on stderr
#
# **Strictly behind, and nothing wider.** A base that has diverged from its origin ref
# holds commits that ref does not, so the ref has not moved *past* it and the diff from
# it is the branch's own; refusing that would refuse a worker judging against a base
# somebody rebased, which is a verdict they still want. What is refused is the one shape
# where every commit of the base is already on the ref and the ref has more: there the
# extra commits are unambiguously work the base has not caught up with.
#
# **"Its own origin ref" is the upstream git records for it, and `origin/<base>` only
# where git records none.** The upstream is the answer wherever there is one, because a
# branch tracking a second remote is tracking it deliberately; the fallback covers the
# ordinary clone where a base was fetched but never checked out with tracking. A base
# that is already a remote-tracking ref — `origin/main`, which is what
# `scripts/comparison-base.sh` hands the gate — names neither, so the default path
# through this recipe is unaffected.
set -euo pipefail

#: What git answers when the question was well formed and the answer is "no": a ref that
#: does not resolve, a ref that is not there, a commit that is not an ancestor. Anything
#: else git exits with is the repository or the filesystem failing, which is a different
#: thing entirely — read as an absent upstream it would report a stale base as fresh, and
#: read as a fresh base it would let the verdict this exists to prevent be judged anyway.
GIT_SAID_NO=1

#: Where `asked` leaves what git printed. A variable rather than this script's own stdout
#: because stdout is the answer to the caller: `asked` inside a command substitution would
#: run in a subshell, and the refusal below would exit that subshell instead of this
#: script — the operational failure reported as a fresh base, one layer in.
ASKED=""

fail() {
    echo "base-freshness: $1; $2" >&2
    exit 2
}

# Ask git one yes-or-no question, and part its "no" from its failure to answer.
#
#   0  git answered yes; what it printed is in `ASKED`
#   1  git answered no
#
# Anything else ends this script naming the command and the status, because a caller
# handed a silent answer cannot tell which of the two it got.
asked() {
    local answered=0
    ASKED=$("$@" 2>/dev/null) || answered=$?
    case "$answered" in
        0) return 0 ;;
        "$GIT_SAID_NO")
            ASKED=""
            return 1
            ;;
    esac
    fail "'$*' exited $answered rather than answering, so whether this base has been left behind is unknown" \
        "check that this is a readable git repository, then retry"
}

base=${1:-}
[[ -n $base ]] || fail "no base was named" "call it as: base-freshness.sh <base>"

# An unresolvable base is the caller's own refusal to make, and it makes a better one:
# it knows what it was about to judge. Nothing is said here.
asked git rev-parse --verify --quiet "$base^{commit}" || exit 0
base_sha=$ASKED

if asked git rev-parse --symbolic-full-name --verify --quiet "$base@{upstream}"; then
    origin_ref=$ASKED
elif asked git show-ref --verify --quiet "refs/remotes/origin/$base"; then
    origin_ref="refs/remotes/origin/$base"
else
    exit 0
fi

asked git rev-parse --verify --quiet "$origin_ref^{commit}" || exit 0
origin_sha=$ASKED
[[ $origin_sha != "$base_sha" ]] || exit 0
asked git merge-base --is-ancestor "$base_sha" "$origin_sha" || exit 0

# Both refs and both commits, because the reader's next move is to look at the two of
# them: the short name they typed against the one that has moved past it.
printf "'%s' is behind its own origin ref: '%s' is at %s and '%s' is at %s\n" \
    "$base" "$base" "$base_sha" "${origin_ref#refs/remotes/}" "$origin_sha"
