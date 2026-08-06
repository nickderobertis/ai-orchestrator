"""Starting over after a provider death, rather than resuming a conversation that died.

A harness binds a *named* conversation to the identity that created it. So a relaunch
that reuses the name asks the identity which just refused for a session it may no
longer hold, and the answer — `No conversation found with session ID ...` — is another
death, which earns another relaunch, which asks again. Whole lineages have been spent
on that loop without one turn of work.

A relaunch therefore never resumes. It takes a name of its own, which is also what
frees the fallback chain to run the turn on whichever identity still can: a fresh
conversation has no binding to carry across harnesses. What it does carry is context —
a bounded rendering of what the dead conversation recorded, given to the next dispatch
as prompt text. That is deliberately a *seed* rather than a restoration: it is short
enough to be read, it is redacted, and it never claims the prior session is resumable.

The transcript is read back from oneharness history, which is the only durable record
of a conversation this harness did not itself write. A history read that fails, times
out, or finds nothing degrades to no seed at all — the relaunch still happens, with the
task alone, exactly as it did before this existed.
"""

from __future__ import annotations

from typing import Any

from .history import HistoryError, all_sessions, session_records
from .redaction import redact

#: What separates a relaunch's conversation name from the one it replaces. A suffix
#: rather than a fresh random name so an operator reading `just history-show` can still
#: see which step's lineage a relaunched conversation belongs to.
RELAUNCH_MARK = "#relaunch"
#: How much of the dead conversation's transcript is carried forward. A prompt seed,
#: not a restore: enough to say what was being done and how far it got, and bounded so
#: a long-running conversation cannot push the actual task out of the next one.
SEED_CHARS = 4_000
#: How many recorded turns are considered, newest last. The tail is what matters — the
#: turn that was interrupted and the ones immediately before it.
SEED_TURNS = 6
#: Bound on one rendered turn, so a single enormous turn cannot consume the whole seed.
SEED_TURN_CHARS = 800

_SEED_HEADING = "## Prior session context (bounded)"


def relaunch_session(session: str, attempt: int) -> str:
    """The conversation name for relaunch ``attempt`` of ``session``.

    Attempt 0 is the original name, so a dispatch that never died keeps the session
    it always had — including the turn-cap resume, which *wants* to continue its
    conversation and is not a relaunch.
    """
    return session if attempt <= 0 else f"{session}{RELAUNCH_MARK}{attempt}"


def _rendered_turn(record: Any) -> str | None:
    """One recorded turn as a line of prompt context, or ``None`` when it carries none.

    Both halves are optional in a history record and either may be absent for a turn
    that never completed — which is precisely the kind of turn this reads.
    """
    if not isinstance(record, dict):
        return None
    parts = [
        f"{label}: {' '.join(str(value).split())}"
        for label, key in (("asked", "prompt"), ("answered", "text"))
        if isinstance(value := record.get(key), str) and value.strip()
    ]
    if not parts:
        return None
    return redact("\n".join(parts))[:SEED_TURN_CHARS]


def transcript_seed(session: str, *, oneharness_bin: str = "oneharness") -> str | None:
    """A bounded rendering of what conversation ``session`` recorded, or ``None``.

    Every failure is silent and answers ``None``: a store this host cannot read, a
    name nothing recorded, a conversation that died before its first turn. None of
    those is a reason to hold up a relaunch — the seed is an improvement to it, never
    a precondition for it.
    """
    try:
        recorded = [
            item for item in all_sessions(oneharness_bin=oneharness_bin) if item.name == session
        ]
    except HistoryError:
        return None
    for item in recorded:  # newest first; the first one that has any turns wins
        try:
            records = session_records(item)
        except HistoryError:
            continue
        turns = [line for record in records[-SEED_TURNS:] if (line := _rendered_turn(record))]
        if turns:
            # Kept from the tail, so what survives the bound is the most recent work.
            return "\n\n".join(turns)[-SEED_CHARS:]
    return None


def seeded_task(task: str, *, dead_session: str, seed: str | None) -> str:
    """The task a relaunched dispatch is given: what happened, then the work itself.

    The context comes first and says plainly that the prior conversation is gone, so
    the agent does not spend a turn trying to address it — the failure that produced
    this seed is exactly an agent being handed a session that no longer exists.
    """
    if not seed:
        return task
    return (
        f"{_SEED_HEADING}\n\n"
        f"A previous dispatch of this work ran as conversation `{dead_session}` and its "
        "provider refused before it finished. That conversation is NOT being resumed and "
        "cannot be — this is a fresh session, and the excerpt below is all that was kept "
        "of it. Continue the work from here.\n\n"
        f"{seed}\n\n"
        f"{task}"
    )
