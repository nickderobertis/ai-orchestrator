#!/usr/bin/env python3
"""The consuming half of `onepipeline unwatched`: the `Stop` hook that refuses to
end a manager's turn while a run it launched has nothing watching it.

AGENTS.md's watch rule is what this enforces, and why it is a hook rather than
another paragraph is written there: prose is remembered by the model or it is not,
and the six properties in that section are each written from a watch that went
silent a different way. A `Stop` hook fires before a turn ends and may refuse the
stop with a reason the model reads, so the question gets asked whether or not
anybody remembered to ask it.

**Its whole job is one exit status.** The verb answers `6` for a run it has
*proven* nothing is watching, `0` for nothing to report, and puts everything it
could not resolve on standard error without changing either — so this branches on
one number and never on prose. A `6` blocks the stop, a `0` ends the turn silently,
and **every ending in between says so and ends the turn**: an engine this cannot
find, one it cannot run, one that ran past its bound, one answering a status it
cannot use, and a memory it cannot keep each put one warning in front of the person
and refuse nothing. Never a refusal, because none of them is evidence that a run is
unwatched, which is the only thing this hook ever blocks on; never silence either,
because a guard that fails silent is worse than no guard — the operator goes on
believing every run they own is covered, where an absent guard is at least visible as
an absence. The warning is one turn's worth of noise about the hook; what it buys is
that the watch invariant is never quietly unenforced.

**The session comes off the payload and never out of the environment.**
`.claude/settings.json` is tracked, so every dispatched claude-code worker in this
repository inherits this hook — and every dispatch inherits its manager's
`ONEPIPELINE_LAUNCHER_SESSION`. A hook that read the environment would block every
worker's turn on its manager's unwatched runs. The worker's own session id owns no
run, which is what makes this silent there, so the payload's is the only session
this asks about: it is passed as `--session`, and the environment's is removed from
the child besides, so the guarantee is this repository's own rather than borrowed
from the verb's precedence.

llmlint: ignore-file[tool_output_is_signal] No ending in this file writes a diagnostic
to either stream, and that is its contract rather than a swallowed error: the harness
reads this hook's standard output as its decision, so a diagnostic there is a malformed
decision, and standard error is shown to the model on an ordinary turn. What an ending
that could not answer does instead is compose one `systemMessage` object — the harness's
own channel for a warning to the person — and end the turn; the one ending that stays
silent is a payload this cannot read, which names no session to say anything about. It is
file-scoped because the property is the file's: an engine that will not answer and a
memory that will not take are one rule applied at each site, and suppressing them one at a
time would leave the next ending added here writing to a stream by accident.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, NewType

#: The status the verb answers when a run this session owns has nothing watching it.
#: The one number this hook acts on; every other status ends the turn.
RUNS_UNWATCHED = 6

#: How long the verb is given before this gives up on it and ends the turn, saying so.
#:
#: The verb's own cost is proportional to the run roots under the runs root, and it
#: reads no run's merged event store, so this is generous by two orders of magnitude
#: on the host it was written for. It is bounded at all because this runs at the end
#: of *every* manager turn: a verb that wedged would hold the session open with
#: nothing to show for it, which is the one failure a supervision aid must not have.
#: `.claude/settings.json` gives the hook its own longer bound above this one, so the
#: warning below is what a caller meets rather than a killed hook.
VERB_TIMEOUT_SECONDS = 10

#: The environment name the verb falls back to when no session is named, removed from
#: the child for the reason the module docstring gives.
LAUNCHER_SESSION_ENV = "ONEPIPELINE_LAUNCHER_SESSION"

# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] These two name fields of
# the *harness's* Stop payload, which is another program's contract and one it publishes no
# machine-readable schema for — so there is nothing on this host to reconcile a copy
# against, and a second copy anywhere would be the drift this rule is about. This file is
# therefore the one source: it is the only thing that reads the payload, and
# `tests/unwatched/test_unwatched_and_stop_hook_e2e.py` composes the payloads it drives this hook
# with by reading these very declarations rather than restating them.
#: The field of that payload naming the session whose turn is ending. It is the whole of
#: what this hook asks about, and deliberately not the session its own environment carries
#: — see the module docstring.
SESSION_FIELD = "session_id"

#: The field saying this stop follows a block of this hook's own, which is what the guard
#: below is a guard on.
CONTINUATION_FIELD = "stop_hook_active"
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


#: A launching session, as the harness names one and as the verb compares them. A type
#: of its own because every other string here is a *path*, and the one mistake this hook
#: must not make is asking about a session it was not handed.
Session = NewType("Session", str)


class Stopping(NamedTuple):
    """What this hook reads off one Stop payload.

    A value rather than a pair, because the two halves answer different questions and a
    positional tuple leaves each call site restating which is which: one is *who* to ask
    about, and the other is *whether anything was already said* about them.
    """

    #: The session to ask the verb about.
    session: Session
    #: Whether this stop follows a block this hook made.
    continues_a_block: bool


def main() -> int:
    """Read the payload, ask the verb about its session, and block, stand aside or stay silent.

    **Only a positively determined unwatched run blocks, and only once this hook has
    recorded that it did.** That is the same principle the watcher record is read under
    — an answer that could not be obtained is not an answer — pointed the other way,
    and the direction matters. For *reporting* a run, every unknown resolves toward
    reporting it, because being wrong there costs one re-armed watch. For *blocking*,
    the irreversible act is the block itself: it wedges the operator's own session,
    which is the one thing this hook must never do. A block this hook could not write
    down would be repeated on every continuation, since nothing would ever say the
    condition had not moved; a memory it cannot read on a continuation leaves it unable
    to say whether the condition moved at all. Both are conditions it says so about and
    steps out of the way for — and so is a verb it could not ask at all, which is not
    evidence either way and leaves the turn unguarded rather than clear.
    """
    stopping = _payload()
    if stopping is None:
        return 0
    session = stopping.session
    reported = _reported(session)
    match reported:
        case Unanswered(why):
            _forget(session)
            return _unguarded(why)
        case None:
            _forget(session)
            return 0
    digest = hashlib.sha256(reported.encode("utf-8")).hexdigest()
    if stopping.continues_a_block:
        remembered = _remembered(session)
        if isinstance(remembered, Unkept):
            return _stand_aside(
                reported, f"could not read what it last blocked on: {remembered.why}"
            )
        if remembered == digest:
            # The manager was told this and did nothing, so a second identical block
            # would hold the session open on a condition that has not moved. What
            # ends the guard is the condition changing, never a count of how often
            # it fired.
            return 0
    # Recorded *before* the block rather than after, because the record is what makes
    # the block safe to make: without it the next continuation cannot tell an unchanged
    # condition from a moved one, and would block again on the same answer for ever.
    recorded = _remember(session, digest)
    if isinstance(recorded, Unkept):
        return _stand_aside(reported, f"could not record what it would block on: {recorded.why}")
    # llmlint: ignore[tool_output_is_signal] this object *is* the hook's answer — the
    # harness reads standard output as the hook's decision — and the reason is the verb's
    # own lines, which are what name the runs to watch. There is nothing to reduce it to.
    sys.stdout.write(json.dumps({"decision": "block", "reason": reported}) + "\n")
    return 0


def _payload() -> Stopping | None:
    """The session to ask about and whether this stop follows a block of this hook's.

    `None` for a payload this cannot read, which is one of the silent endings: a
    hook handed something it does not understand knows nothing about whether a run
    is watched, and saying so on a manager's terminal every turn would be noise
    about the hook rather than about the runs.

    A blank or absent `session_id` is that same nothing. It is refused here rather
    than passed on, because the verb reads the environment when no session is named
    and the environment is exactly the session this must not ask about. One carrying a
    NUL is refused for the other reason a session can be unaskable: no argument vector
    can carry it, so handing it on ends this half before the verb is asked, and that
    reads as the wrapper's loud ending rather than as the payload this cannot read.
    """
    try:
        read = json.loads(sys.stdin.read())
    except (ValueError, OSError):
        return None
    if not isinstance(read, dict):
        return None
    session = read.get(SESSION_FIELD)
    if not isinstance(session, str) or not session.strip() or "\0" in session:
        return None
    return Stopping(Session(session), read.get(CONTINUATION_FIELD) is True)


class Unanswered(NamedTuple):
    """A question the verb did not answer, and why.

    Its own value rather than `None`, for the reason `Unkept` below is: "nothing is
    unwatched" and "this could not ask" are different answers with different consequences.
    The first is the ordinary end of a turn. The second is a turn ending with the watch
    invariant unenforced, and one nobody is told about is a guard that has stopped guarding
    while the operator goes on believing it has not.
    """

    why: str


def _reported(session: Session) -> str | None | Unanswered:
    """What the verb names as unwatched for `session`.

    `None` when it names nothing, and `Unanswered` for every way this could not get an
    answer: no binary to ask, one the kernel would not run, one that ran past its bound,
    and one answering a status this cannot use. Those are four different host conditions
    and not one of them is evidence that a run is unwatched — which is the only thing this
    hook ever blocks on — so none of them blocks. But neither are they evidence that none
    is, so none of them is silent: each names itself, and `main` puts that in front of the
    person.
    """
    binary = _binary()
    if binary is None:
        return Unanswered(
            f"found no `onepipeline` to ask, at {_installed_binary()} or on the search path"
        )
    environment = dict(os.environ)
    environment.pop(LAUNCHER_SESSION_ENV, None)
    try:
        answered = subprocess.run(
            [binary, "unwatched", "--session", session],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=VERB_TIMEOUT_SECONDS,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Unanswered(
            f"gave up on `{binary} unwatched` after its {VERB_TIMEOUT_SECONDS}s bound"
        )
    except (OSError, subprocess.SubprocessError) as failure:
        return Unanswered(f"could not run `{binary} unwatched`: {failure}")
    if answered.returncode == 0:
        return None
    if answered.returncode != RUNS_UNWATCHED:
        return Unanswered(
            f"got status {answered.returncode} from `{binary} unwatched`, which is neither "
            f"0 nor {RUNS_UNWATCHED}{_said_on_stderr(answered.stderr)}"
        )
    if not answered.stdout.strip():
        return Unanswered(
            f"got status {RUNS_UNWATCHED} from `{binary} unwatched` naming no run"
            f"{_said_on_stderr(answered.stderr)}"
        )
    # llmlint: ignore[boundary_inputs_validated] What this returns is handed to the model
    # verbatim as the block's `reason`, and that is the contract rather than an omission:
    # those lines are what tell the person which runs to watch, this hook decides on the
    # status alone and never reads a run out of them, and `json.dumps` is what carries
    # them. There is no structure here this depends on, so there is none to validate.
    return answered.stdout


def _said_on_stderr(stderr: str) -> str:
    """The verb's own account of a status this cannot use, for the warning that names it.

    A refusal names its reason on standard error and nowhere else, so a warning that
    dropped it would tell the person the number and not the cause.
    """
    said = stderr.strip()
    return f" — it said: {said}" if said else ""


def _binary() -> str | None:
    """The `onepipeline` to ask, reached without this checkout's project environment.

    This checkout's own `.venv/bin` first and whatever is on the search path after
    it, which is how `just plans` reaches its own pinned CLI — and deliberately not
    `uv run`, which every `just` view here goes through. `uv` takes an **exclusive**
    lock on the project environment, and this runs at the end of every manager turn
    on a host that runs test tiers continuously: a hook that waited on that lock
    would make the length of a turn a property of what else the host is doing.
    """
    installed = _installed_binary()
    if installed.is_file() and os.access(installed, os.X_OK):
        return str(installed)
    return shutil.which("onepipeline")


def _installed_binary() -> Path:
    """Where this checkout's own session setup installs the engine."""
    return Path(__file__).resolve().parent.parent / ".venv" / "bin" / "onepipeline"


def _memory(session: Session) -> Path:
    """Where this remembers what it last blocked `session` on.

    Outside the tracked tree, because nothing a hook writes every turn belongs in a
    repository, and outside the runs root, because that root is the run's own record
    of itself and this is a fact about a *conversation*. One file per session, named
    by the digest of the session id rather than by the id: the id is somebody else's
    string arriving on standard input, and joining one onto a path is how a name
    becomes a directory traversal.
    """
    state = os.environ.get("XDG_STATE_HOME", "")
    # Ignored unless it is absolute, which is the specification's own rule for this
    # variable and is a boundary check as well as a courtesy: a relative one would put
    # this hook's memory under whatever directory the harness happened to run it in, and
    # the *runs root* is a relative default of exactly that shape.
    root = Path(state) if os.path.isabs(state) else Path.home() / ".local" / "state"
    named = hashlib.sha256(session.encode("utf-8")).hexdigest()
    return root / "ai-orchestrator" / "stop-unwatched" / named


class Unkept(NamedTuple):
    """A memory this hook could not keep — could not read back, or could not write — and why.

    One name for both halves because both are the same failure from `main`'s seat: a
    record this hook cannot rely on, whichever direction it was going. Its own value
    rather than `None`, because absence and failure are different answers with different
    consequences: a memory that is *absent* is a session nothing has blocked yet, which is
    safe to block; one that *cannot be read* leaves this hook unable to say whether the
    condition moved, and one that *cannot be written* is a block the next continuation
    could not tell from a moved condition — and neither is.
    """

    why: str


def _remembered(session: Session) -> str | None | Unkept:
    """What this last blocked `session` on.

    `None` when nothing did, and `Unkept` when this cannot say — which are different
    answers, and the class above states why.
    """
    try:
        return _memory(session).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as failure:
        return Unkept(f"{failure.strerror or failure}: {_memory(session)}")
    except UnicodeDecodeError as failure:
        # A record this hook can open but cannot decode answers the question no better
        # than one it may not open: whether the condition moved is read out of it, and
        # bytes that are not UTF-8 say nothing about that.
        return Unkept(f"{failure.reason}: {_memory(session)}")


def _remember(session: Session, digest: str) -> None | Unkept:
    """Record what this is about to block on, or say why it could not.

    The record is a precondition of the block rather than a courtesy beside it: a block
    nothing wrote down is one the next continuation would make again, on an answer that
    has not moved, and again after that. So a memory this hook cannot write is a block it
    must not make.
    """
    path = _memory(session)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(digest + "\n", encoding="utf-8")
    except OSError as failure:
        return Unkept(f"{failure.strerror or failure}: {path}")
    return None


def _stand_aside(reported: str, because: str) -> int:
    """End the turn without refusing it, and say why, when runs *are* unwatched.

    Silence would swallow the verb's own answer, and a block would hold the session on a
    record this hook cannot keep, so what it does instead is put both in front of the
    person — the runs, and why it refused nothing over them — which is exactly the shape
    of "says so and steps out of the way".
    """
    lines = reported.rstrip("\n").splitlines()
    return _warn(
        f"stop-unwatched-guard: {len(lines)} run(s) this session owns are unwatched, and "
        f"this hook did not refuse the turn because it {because}. Arm a watch on each "
        "with `just watch <run-id>`:\n" + "\n".join(lines)
    )


def _unguarded(because: str) -> int:
    """End the turn without refusing it, and say that nothing asked the question.

    The other ending that is neither a block nor silence, and the one a guard most easily
    gets wrong: with no answer there is nothing to block on, and it is tempting to read
    "nothing to block on" as "nothing to say". It is not. This hook is what makes the
    watch invariant automatic rather than remembered, so a turn it could not guard is a
    turn the person has to guard themselves, and has to be told so.
    """
    return _warn(
        f"stop-unwatched-guard: this turn ends unguarded, because the hook {because}. "
        "Whether a run this session owns is unwatched was not asked; ask it yourself with "
        "`just unwatched`."
    )


def _warn(warning: str) -> int:
    """Put `warning` in front of the person and end the turn normally.

    The harness renders `systemMessage` to the user as a warning and, with no `decision`
    beside it, ends the turn — so this is the one channel a hook has that reaches a person
    without either refusing the stop or writing a diagnostic to a stream the harness reads
    as something else.
    """
    sys.stdout.write(json.dumps({"systemMessage": warning}) + "\n")
    return 0


def _forget(session: Session) -> None:
    """Drop what was remembered for `session`, this stop having nothing to block on.

    Every ending that is not a block, rather than the "nothing is unwatched" one alone,
    because what the memory is *for* is telling a continuation whether the condition
    moved — and a turn that ended without blocking leaves no block for the next one to be
    a continuation of. Dropping it after a verb that could not answer costs nothing
    either: the next ordinary stop is not a continuation, so it blocks on what it finds
    whether or not anything was remembered.
    """
    try:
        _memory(session).unlink(missing_ok=True)
    except OSError:
        return


if __name__ == "__main__":
    sys.exit(main())
