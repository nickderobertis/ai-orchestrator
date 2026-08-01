"""Host-visible FIFO channel between a live planner and onejudge's supervisor."""

# llmlint: ignore-file[modern_domain_modeling] mappings preserve the upstream JSON wire contract
# llmlint: ignore-file[structural_pattern_matching] explicit checks give precise boundary errors
# llmlint: ignore-file[changed_behavior_has_e2e] real journeys e2e; malformed branches unit tested
# These dictionaries are the thin, validated onejudge JSON wire contract. The real launch,
# just recipes, FIFO round trips, timeout, and reattach run e2e; exhaustive malformed-input
# and unavailable-peer branches stay deterministic unit tests rather than timing-heavy e2e.

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import math
import os
import queue
import select
import socket
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast, get_args

from .config import ConfigError
from .coordination import advisory_lock, atomic_json
from .edits import EDIT_PROTOCOL_VERSION, EditCommand, EditError, apply_edit, parse_commands
from .environment import CHANNEL_ENV_PREFIX
from .journal import JOURNAL_NAME, JournalError, JournalSink, NullJournal, open_journal
from .runs import (
    RunId,
    latest_round,
    load_mapping,
    process_may_be_live,
    resolve_supervision_run,
    round_appears_in_flight,
    validate_run_id,
)


class ChannelError(Exception):
    """The channel payload or transport is invalid."""


class ChannelTimeout(TimeoutError):
    """The other side did not rendezvous before the bounded deadline."""


CHANNEL_DIR_ENV = f"{CHANNEL_ENV_PREFIX}DIR"
CHANNEL_RUN_ID_ENV = f"{CHANNEL_ENV_PREFIX}RUN_ID"
CHANNEL_ENDPOINTS = ("up.fifo", "down.fifo")
HEARTBEAT_FILE = "heartbeat.json"
HEARTBEAT_SURFACE_FILE = "heartbeat-surface.json"
DEFAULT_HEARTBEAT_INTERVAL = 1800.0
#: Accepted graph edits, and how far the reconciler has consumed them. The down
#: FIFO cannot carry them: `relay_supervisor` and the reconciler's own receiver
#: both read that endpoint, so whichever wins the race decides whether a command
#: reaches the graph — and the relay has no graph to apply it to. Submission
#: therefore appends here first, and the reconciler drains from here, so delivery
#: no longer depends on which reader consumed the frame.
COMMAND_QUEUE_FILE = "commands.jsonl"
COMMAND_CURSOR_FILE = "commands-cursor.json"
#: The reconciler's verdict on each accepted command, by queue sequence. This is
#: what makes acceptance mean *applied*: the submitter waits here for the answer
#: rather than exiting on "queued" and learning the outcome later, or never.
COMMAND_OUTCOME_FILE = "command-outcomes.jsonl"

PlannerSurfaceKind = Literal[
    "supervisor",
    "milestone",
    "blocker",
    "choice",
    "proposal",
    "heartbeat",
    "closeout",
]
PLANNER_SURFACE_KINDS: frozenset[PlannerSurfaceKind] = frozenset(get_args(PlannerSurfaceKind))


def _heartbeat_frame(run_id: str, round_number: int, message: str) -> dict[str, Any]:
    return {
        "op": "supervisor",
        "run_id": run_id,
        "round": round_number,
        "surface": {"kind": "heartbeat", "message": message, "blocking": False},
        "messages": [],
    }


class ProposalSink(Protocol):
    def propose(self, node: str, message: str) -> None: ...

    def propose_blocking(self, node: str, message: str) -> None: ...

    def defer_blocking(self, node: str, message: str) -> None: ...

    def persist_replies(self) -> None: ...

    def drain_commands(self) -> tuple[QueuedCommand, ...]: ...

    def record_outcome(self, seq: int, *, applied: bool, reason: str) -> None: ...

    def heartbeat_tick(self) -> None: ...


class CheckInDispatcher(Protocol):
    def __call__(self) -> None: ...


def _heartbeat_path(channel_dir: Path) -> Path:
    return channel_dir / HEARTBEAT_FILE


def _validated_interval(value: Any, *, field: str = "heartbeat_interval") -> float:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ChannelError(f"{field} must be a positive, finite number of seconds")
    return float(value)


def initialize_heartbeat(channel_dir: Path, interval_s: float = DEFAULT_HEARTBEAT_INTERVAL) -> None:
    """Seed heartbeat state once without resetting an existing run's clock."""
    interval = _validated_interval(interval_s, field="heartbeat interval")
    path = _heartbeat_path(channel_dir)
    with advisory_lock(f"channel-heartbeat:{channel_dir.resolve()}"):
        if not path.exists():
            atomic_json(
                path,
                {
                    "last_surface_at": time.time(),
                    "last_attempt_at": 0.0,
                    "interval_s": interval,
                    "due": False,
                    "in_flight": False,
                    "claim": None,
                    "enabled": True,
                },
            )


def _timestamp(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _claim_holder(value: object) -> dict[str, Any] | None:
    """The recorded lease owner, or ``None`` for a claim nobody can be shown to hold.

    Unreadable owner metadata is *not* a live holder here, unlike the run-level
    liveness views that read the same shape. Those decide whether to address a run
    somebody else may be supervising, so an unknown resolves toward "still working".
    This decides whether one check-in dispatch may start, and an unknown that
    resolves that way is the wedge itself: a claim nobody can prove is held silences
    the pacemaker for the life of the run, which is strictly worse than a duplicate
    read-only check-in. The pid/host probe below is still the shared one.
    """
    if not isinstance(value, Mapping):
        return None
    pid = value.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        return None
    return {"pid": pid, "host": value.get("host"), "at": value.get("at")}


def _claim_may_be_held(state: Mapping[str, Any]) -> bool:
    """Whether an in-flight claim still has a process that could hand it back."""
    if not state["in_flight"]:
        return False
    holder = _claim_holder(state.get("claim"))
    if holder is None:
        return False
    return process_may_be_live(holder["pid"], holder["host"])


def _load_heartbeat(channel_dir: Path) -> dict[str, Any] | None:
    path = _heartbeat_path(channel_dir)
    if not path.is_file():
        return None
    value = load_mapping(path)
    last = value.get("last_surface_at")
    interval = value.get("interval_s")
    due = value.get("due")
    enabled = value.get("enabled")
    in_flight = value.get("in_flight", False)
    last_attempt = value.get("last_attempt_at", 0.0)
    if (
        not _timestamp(last)
        or not isinstance(due, bool)
        or not isinstance(enabled, bool)
        or not isinstance(in_flight, bool)
        or not _timestamp(last_attempt)
    ):
        raise ChannelError("heartbeat state is invalid")
    _validated_interval(interval, field="heartbeat interval_s")
    state = {
        **value,
        "in_flight": in_flight,
        "last_attempt_at": float(last_attempt),
        "claim": _claim_holder(value.get("claim")),
    }
    # `retry_not_before` was an absolute deadline computed from the interval in force
    # when an attempt failed, so lowering the interval could not bring the retry
    # forward. `last_attempt_at` records the same fact as a timestamp and is compared
    # against the *current* interval on every tick, which is what keeps the interval
    # the only knob. A state file written by the older build is read here and its dead
    # deadline dropped rather than carried forward.
    state.pop("retry_not_before", None)
    return state


def heartbeat_state(channel_dir: Path) -> dict[str, Any] | None:
    """Read validated heartbeat state, or None for legacy/non-channel runs."""
    with advisory_lock(f"channel-heartbeat:{channel_dir.resolve()}"):
        return _load_heartbeat(channel_dir)


def _due_anchor(state: Mapping[str, Any]) -> float:
    """When the current interval started counting.

    The later of the last update a planner actually read and the last check-in
    attempt that settled. Anchoring on delivery alone would re-arm the pacemaker
    instantly after every queued update nobody read; anchoring on the attempt alone
    would let a run that is being supervised normally drift. Taking the later of the
    two gives one rule for both, and — because it is compared against the interval
    on every tick rather than baked into a stored deadline — lowering the interval
    mid-flight brings the next check-in forward immediately.
    """
    return max(float(state["last_surface_at"]), float(state["last_attempt_at"]))


def mark_heartbeat_due(channel_dir: Path, *, now: float | None = None) -> None:
    """Persist the sticky due bit once the configured interval has elapsed."""
    with advisory_lock(f"channel-heartbeat:{channel_dir.resolve()}"):
        state = _load_heartbeat(channel_dir)
        if state is None or state["due"] or not state["enabled"]:
            return
        current = time.time() if now is None else now
        if current - _due_anchor(state) >= float(state["interval_s"]):
            state["due"] = True
            atomic_json(_heartbeat_path(channel_dir), state)


def record_surface(channel_dir: Path, *, now: float | None = None) -> None:
    """Reset the pacemaker after a planner-visible up-channel surface."""
    with advisory_lock(f"channel-heartbeat:{channel_dir.resolve()}"):
        state = _load_heartbeat(channel_dir)
        if state is None:
            return
        state["last_surface_at"] = time.time() if now is None else now
        state["due"] = False
        state["in_flight"] = False
        state["claim"] = None
        atomic_json(_heartbeat_path(channel_dir), state)


def claim_heartbeat(channel_dir: Path, *, now: float | None = None) -> bool:
    """Atomically claim one due agent check-in, reclaiming a dead holder's lease.

    The claim is a **lease on dispatching a check-in**, not a lock held until a
    planner reads the result: it is taken here and handed back by
    `finish_heartbeat_attempt` when the dispatch settles either way. That is the
    whole distinction the pacemaker turns on. Held to delivery, one unread update
    silenced the pacemaker for the rest of the run, and no interval change or
    read-only view could reach it — the wedge was the claim, not the clock.

    A lease still has to survive its holder dying mid-dispatch, so it records the pid
    and host that took it and a later tick reclaims one whose holder is provably
    gone. `process_may_be_live` is the same probe the abandoned-round path uses.
    """
    with advisory_lock(f"channel-heartbeat:{channel_dir.resolve()}"):
        state = _load_heartbeat(channel_dir)
        if state is None or not state["enabled"] or not state["due"]:
            return False
        if _claim_may_be_held(state):
            return False
        state["in_flight"] = True
        state["claim"] = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "at": time.time() if now is None else now,
        }
        atomic_json(_heartbeat_path(channel_dir), state)
        return True


def release_heartbeat_claim(channel_dir: Path) -> None:
    """Drop an in-flight claim without resetting the clock or the due signal.

    The discard paths use this: a queued update thrown away unread has no dispatch
    left to hand its lease back, and a lease nothing will ever release is how an
    ignored pacemaker goes permanently quiet instead of escalating. The clock and the
    due bit are deliberately left alone — nobody has been updated, so the staleness
    the views report keeps growing from the last update the planner actually saw.
    """
    with advisory_lock(f"channel-heartbeat:{channel_dir.resolve()}"):
        state = _load_heartbeat(channel_dir)
        if state is None or not state["in_flight"]:
            return
        state["in_flight"] = False
        state["claim"] = None
        atomic_json(_heartbeat_path(channel_dir), state)


def finish_heartbeat_attempt(
    channel_dir: Path, *, succeeded: bool, now: float | None = None
) -> None:
    """Hand back a dispatch lease and restart the interval from this attempt.

    Both outcomes land here and both restart the clock, because both are a check-in
    the harness has now spent: a queued update is one interval's worth of reporting
    whether or not a planner reads it, and a failed synthesis has already cost its
    turn. What separates them is only what the planner can see afterwards — the
    queued update, and the growing staleness the views report against it.
    """
    with advisory_lock(f"channel-heartbeat:{channel_dir.resolve()}"):
        state = _load_heartbeat(channel_dir)
        if state is None:
            return
        state["in_flight"] = False
        state["claim"] = None
        state["due"] = False
        state["last_attempt_at"] = time.time() if now is None else now
        atomic_json(_heartbeat_path(channel_dir), state)


def fail_heartbeat_claim(channel_dir: Path, *, now: float | None = None) -> None:
    """Release a failed synthesis claim until its next retry interval."""
    finish_heartbeat_attempt(channel_dir, succeeded=False, now=now)


def apply_heartbeat_reply(channel_dir: Path, response: Mapping[str, Any]) -> None:
    """Apply an optional live interval update without changing verdict semantics."""
    if "heartbeat_interval" not in response:
        return
    setting = response["heartbeat_interval"]
    enabled = setting is not False
    interval = None if setting is False else _validated_interval(setting)
    with advisory_lock(f"channel-heartbeat:{channel_dir.resolve()}"):
        state = _load_heartbeat(channel_dir)
        if state is None:
            raise ChannelError("heartbeat state is unavailable")
        state["enabled"] = enabled
        if interval is not None:
            state["interval_s"] = interval
        atomic_json(_heartbeat_path(channel_dir), state)
        if not enabled:
            with suppress(FileNotFoundError):
                (channel_dir / HEARTBEAT_SURFACE_FILE).unlink()


def due_indicator(channel_dir: Path, *, now: float | None = None) -> str | None:
    state = heartbeat_state(channel_dir)
    if state is None or not state["enabled"] or not state["due"]:
        return None
    elapsed = max(0.0, (time.time() if now is None else now) - float(state["last_surface_at"]))
    return f"planner update due ({int(elapsed // 60)}m since last update)"


def planner_wait_indicator(channel_dir: Path) -> str | None:
    """Describe a surface whose delivery or reply is holding orchestration open."""
    pending = channel_dir / "planner-pending.json"
    if not pending.is_file():
        return None
    surface = _validated_persisted_surface(load_mapping(pending))
    action = "planner decision" if surface["blocking"] else "planner reply"
    return f"waiting for {action}: {surface['kind']}: {surface['message']}"


@dataclass(frozen=True)
class PendingSurface:
    """One surface the channel is holding that no planner has consumed."""

    kind: str
    message: str
    queued_at: float


#: The durable queue: every file that holds a surface *nobody has read yet*. The
#: queued check-in update, and the blocker preserved for the next reply-ready relay.
#: `planner-pending.json` is deliberately not one of them — it outlives delivery,
#: waiting for a reply, so counting it here would report a surface a planner has
#: already read as unread. `planner_wait_indicator` is what reports that one.
QUEUED_SURFACE_FILES = (HEARTBEAT_SURFACE_FILE, "deferred-blocker.json")


def _queued_surface(path: Path) -> PendingSurface | None:
    """Read one queued surface, degrading to its bare existence when unreadable.

    The file existing *is* the queued fact, and this feeds planner-facing views that
    must not go quiet on damaged state: a surface whose kind and message cannot be
    read is still a surface nobody has read, so it is reported with what is known
    rather than dropped. That is the whole failure this reporting exists to prevent.
    """
    try:
        queued_at = path.stat().st_mtime
    except OSError:
        return None
    kind, message = "unknown", "unreadable queued surface"
    with suppress(ChannelError, ConfigError, OSError):
        value = load_mapping(path)
        # A check-in queues the whole wire frame; a deferred blocker queues the
        # surface alone. Both carry the same surface shape, one level apart.
        raw = value.get("surface") if isinstance(value.get("surface"), Mapping) else value
        surface = _validated_persisted_surface(cast(Mapping[str, Any], raw))
        kind, message = surface["kind"], surface["message"]
    return PendingSurface(kind, message, queued_at)


def pending_surfaces(channel_dir: Path) -> tuple[PendingSurface, ...]:
    """Every queued, unconsumed planner surface for one run, oldest first."""
    found = [
        queued
        for name in QUEUED_SURFACE_FILES
        if (queued := _queued_surface(channel_dir / name)) is not None
    ]
    return tuple(sorted(found, key=lambda surface: surface.queued_at))


def _age(seconds: float) -> str:
    """One compact, non-negative age: ``45s``, ``12m``, ``3h``."""
    elapsed = max(0.0, seconds)
    if elapsed < 60:
        return f"{int(elapsed)}s"
    if elapsed < 3600:
        return f"{int(elapsed // 60)}m"
    return f"{int(elapsed // 3600)}h"


def _unread_since(channel_dir: Path, queued: Sequence[PendingSurface]) -> float:
    """When this channel was last read, at the latest.

    Not the queued file's own age. The pacemaker keeps exactly one check-in pending
    and *replaces* it each interval, so the file is always young while the channel
    may have gone unread for hours — reporting the file's age would reset the very
    number that is supposed to escalate. The heartbeat's `last_surface_at` is the
    last surface a planner actually consumed, which is the fact worth reporting;
    the oldest queued file is the fallback for a run that has no heartbeat state.
    """
    oldest = queued[0].queued_at
    with suppress(ChannelError, ConfigError, OSError):
        state = heartbeat_state(channel_dir)
        if state is not None:
            return min(oldest, float(state["last_surface_at"]))
    return oldest


def pending_surface_indicator(run_dir: Path, *, now: float | None = None) -> str | None:
    """One line naming a run's unread surfaces and the command that reads them.

    This is what a planner who never attached to the channel sees from the commands
    they already run. Both halves matter: the growing age is the escalation, because
    the pacemaker deliberately keeps exactly one check-in pending rather than piling
    up duplicates, so an ignored channel gets *louder* here instead of quieter; and
    the literal command is what stops the reader reconstructing run state by hand.
    """
    channel_dir = run_dir / "channel"
    queued = pending_surfaces(channel_dir)
    if not queued:
        return None
    age = _age((time.time() if now is None else now) - _unread_since(channel_dir, queued))
    noun = "update" if len(queued) == 1 else "updates"
    them = "it" if len(queued) == 1 else "them"
    return (
        f"{len(queued)} planner {noun} waiting, unread for {age}; read {them} with: "
        f"just channel-next {run_dir.name} --runs-dir {run_dir.parent}"
    )


def discard_surface_from_a_finished_round(run_dir: Path) -> bool:
    """Drop a queued check-in whose round is over, releasing its dispatch lease.

    A queued surface names the round it was written for, and `channel-next` refuses
    one that does not match the active round — so a surface that outlives its round
    used to be unconsumable *and* still counted as the run's one pending update. The
    round transition and the next read both call this, which is why the state has an
    operator remedy at all: whichever happens first clears it, and the pacemaker
    queues a fresh update describing the round that is actually running.

    Discarded rather than kept consumable, deliberately: the update describes work
    in a round that has finished, and handing a planner a stale snapshot is the
    misreport this whole surface exists to prevent. The clock is untouched, so the
    views keep reporting how long the channel has gone unread.
    """
    surface = run_dir / "channel" / HEARTBEAT_SURFACE_FILE
    latest = latest_round(run_dir)
    if latest is None:
        return False
    with advisory_lock(f"channel-heartbeat-surface:{surface.resolve()}"):
        if not surface.is_file():
            return False
        queued_round: object = None
        with suppress(ChannelError, ConfigError, OSError):
            queued_round = load_mapping(surface).get("round")
        # A frame whose round cannot be read is unconsumable for the same reason a
        # stale one is — `channel-next` validates it against the active round — so it
        # is cleared here too rather than left to occupy the run's one pending slot.
        current = (
            isinstance(queued_round, int)
            and not isinstance(queued_round, bool)
            and queued_round >= latest[0]
        )
        if current:
            return False
        surface.unlink(missing_ok=True)
    release_heartbeat_claim(run_dir / "channel")
    return True


def _queued_detail(
    surface: Mapping[str, Any], *, source: str, workstream: str | None
) -> dict[str, Any]:
    """The ``planner-surface-queued`` payload: what was sent, and where it came from.

    ``workstream`` names the node whose work provoked the surface when one did; a
    check-in update covers every active workstream at once and names none.
    """
    return {
        "kind": str(surface["kind"]),
        "message": str(surface["message"]),
        "blocking": bool(surface.get("blocking", False)),
        "source": source,
        "workstream": workstream,
    }


@dataclass(frozen=True)
class QueuedCommand:
    """One accepted graph edit awaiting the reconciler, with where it belongs."""

    seq: int
    round: int
    command: EditCommand


def _command_lock(channel_dir: Path) -> str:
    return f"channel-commands:{channel_dir.resolve()}"


def _queued(line: str) -> QueuedCommand | None:
    """Parse one queue record, returning None for anything unreadable."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    seq, round_number, payload = record.get("seq"), record.get("round"), record.get("command")
    if (
        not isinstance(seq, int)
        or isinstance(seq, bool)
        or not isinstance(round_number, int)
        or isinstance(round_number, bool)
        or not isinstance(payload, dict)
    ):
        return None
    try:
        commands = parse_commands({"version": EDIT_PROTOCOL_VERSION, "commands": [payload]})
    except EditError:
        return None
    return QueuedCommand(seq, round_number, commands[0])


def _read_queue(channel_dir: Path) -> list[QueuedCommand]:
    path = channel_dir / COMMAND_QUEUE_FILE
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8", errors="replace")
    return [
        queued
        for line in raw.splitlines()
        if line.strip() and (queued := _queued(line)) is not None
    ]


def submit_commands(
    channel_dir: Path, commands: Sequence[EditCommand], *, round_number: int
) -> tuple[int, ...]:
    """Durably accept graph edits for one round and return their queue sequences."""
    if not commands:
        return ()
    channel_dir.mkdir(parents=True, exist_ok=True)
    with advisory_lock(_command_lock(channel_dir)):
        next_seq = max((item.seq for item in _read_queue(channel_dir)), default=0) + 1
        accepted = tuple(range(next_seq, next_seq + len(commands)))
        with (channel_dir / COMMAND_QUEUE_FILE).open("a", encoding="utf-8") as handle:
            for seq, command in zip(accepted, commands, strict=True):
                handle.write(
                    json.dumps(
                        {"seq": seq, "round": round_number, "command": command.payload},
                        sort_keys=True,
                    )
                    + "\n"
                )
            handle.flush()
            os.fsync(handle.fileno())
    return accepted


def _consumed(channel_dir: Path) -> int:
    """How far the reconciler has claimed, from the durable cursor."""
    cursor_path = channel_dir / COMMAND_CURSOR_FILE
    if not cursor_path.is_file():
        return 0
    value = load_mapping(cursor_path).get("consumed")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ChannelError("command cursor is invalid")
    return value


def claim_commands(channel_dir: Path) -> tuple[QueuedCommand, ...]:
    """Claim every unconsumed queued command, advancing the durable cursor.

    Commands from another round are claimed too rather than left behind: the
    caller reports them as rejected, which is the whole point of the queue — an
    accepted command is either applied or answered, never silently retained.
    """
    with advisory_lock(_command_lock(channel_dir)):
        claimed = tuple(
            item for item in _read_queue(channel_dir) if item.seq > _consumed(channel_dir)
        )
        if claimed:
            atomic_json(
                channel_dir / COMMAND_CURSOR_FILE, {"consumed": max(item.seq for item in claimed)}
            )
        return claimed


def pending_commands(channel_dir: Path) -> tuple[QueuedCommand, ...]:
    """Read accepted, unclaimed commands without consuming them."""
    with advisory_lock(_command_lock(channel_dir)):
        return tuple(item for item in _read_queue(channel_dir) if item.seq > _consumed(channel_dir))


def unanswered_commands(channel_dir: Path) -> tuple[QueuedCommand, ...]:
    """Every accepted command with no recorded verdict, claimed or not.

    Claiming advances the cursor before the reconciler can answer, so "unclaimed"
    is the wrong question at teardown: a command consumed by a reconciler that then
    stopped is exactly the one that would otherwise be left unanswered forever.
    """
    with advisory_lock(_command_lock(channel_dir)):
        answered = set(command_outcomes(channel_dir))
        return tuple(item for item in _read_queue(channel_dir) if item.seq not in answered)


@dataclass(frozen=True)
class CommandOutcome:
    """The reconciler's verdict on one accepted command."""

    seq: int
    applied: bool
    reason: str


@dataclass(frozen=True)
class CommandVerdicts:
    """What became of one submission's commands within the caller's deadline."""

    applied: tuple[CommandOutcome, ...]
    rejected: tuple[CommandOutcome, ...]
    #: Accepted, durable, and not yet reconciled. Not a rejection: the command is
    #: still queued, so the submitter must be told to wait rather than resubmit.
    unreconciled: tuple[int, ...]


def record_command_outcome(channel_dir: Path, seq: int, *, applied: bool, reason: str) -> None:
    """Durably answer one accepted command, so its submitter cannot be left guessing."""
    with (
        advisory_lock(_command_lock(channel_dir)),
        (channel_dir / COMMAND_OUTCOME_FILE).open("a", encoding="utf-8") as handle,
    ):
        handle.write(
            json.dumps({"seq": seq, "applied": applied, "reason": reason}, sort_keys=True) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def _outcome(line: str) -> CommandOutcome | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    seq, applied, reason = record.get("seq"), record.get("applied"), record.get("reason")
    if (
        not isinstance(seq, int)
        or isinstance(seq, bool)
        or not isinstance(applied, bool)
        or not isinstance(reason, str)
    ):
        return None
    return CommandOutcome(seq, applied, reason)


def command_outcomes(channel_dir: Path) -> dict[int, CommandOutcome]:
    """Every recorded verdict, keyed by queue sequence."""
    path = channel_dir / COMMAND_OUTCOME_FILE
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8", errors="replace")
    found: dict[int, CommandOutcome] = {}
    for line in raw.splitlines():
        if line.strip() and (outcome := _outcome(line)) is not None:
            found.setdefault(outcome.seq, outcome)
    return found


def await_command_outcomes(
    channel_dir: Path, seqs: Sequence[int], *, timeout: float
) -> CommandVerdicts:
    """Wait for the reconciler's verdict on each accepted command, bounded by ``timeout``.

    The bound is the point, so it is validated here rather than trusted: a
    non-finite deadline never compares true and would wait forever.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ChannelError("timeout must be finite and non-negative")
    deadline = time.monotonic() + timeout
    outstanding = list(seqs)
    found: dict[int, CommandOutcome] = {}
    while True:
        found = {seq: outcome for seq, outcome in command_outcomes(channel_dir).items()}
        outstanding = [seq for seq in seqs if seq not in found]
        if not outstanding or time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    answered = [found[seq] for seq in seqs if seq in found]
    return CommandVerdicts(
        applied=tuple(outcome for outcome in answered if outcome.applied),
        rejected=tuple(outcome for outcome in answered if not outcome.applied),
        unreconciled=tuple(outstanding),
    )


def live_round(run_dir: Path) -> int:
    """The round currently accepting graph edits, else raise a stated rejection."""
    latest = latest_round(run_dir)
    if latest is None:
        raise ChannelError("this run has no recorded round, so no graph edit can be applied")
    number, round_dir = latest
    if (round_dir / "result.json").is_file() or not round_appears_in_flight(round_dir):
        raise ChannelError(
            f"round-{number:02d} is not executing, so no graph edit can be applied; "
            "reply with a verdict and let the orchestrator open the next round"
        )
    return number


def validate_commands(run_dir: Path, round_number: int, commands: Sequence[EditCommand]) -> None:
    """Reject a command that cannot be applied to the live graph, with the reason.

    Validation runs through the reconciler's own `apply_edit`, against the graph
    projected from the authoritative event log, so the answer a submitter gets is
    the answer the reconciler would give rather than a second, drifting rulebook.
    """
    from .graph import parse_graph
    from .plan import PlanError
    from .projection import ProjectionError, project_run

    try:
        projection = project_run(run_dir / JOURNAL_NAME, RunId(run_dir.name), round_number)
        graph = parse_graph(dict(projection.plan))
    except (ConfigError, PlanError, ProjectionError, OSError) as exc:
        raise ChannelError(f"cannot validate against the live graph: {exc}") from exc
    attestations = list(projection.attestations)
    states = dict(projection.node_states)
    for index, command in enumerate(commands):
        try:
            graph, operations = apply_edit(graph, command, states=states, attestations=attestations)
        except EditError as exc:
            raise ChannelError(f"command #{index} ({command.op}) cannot be applied: {exc}") from exc
        for operation in operations:
            if operation["kind"] == "human-attested":
                ref = str(operation["detail"]["ref"])
                attestations.append(ref)
                states[ref] = "done"


def create_channel(
    run_dir: Path, *, heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL
) -> Path:
    """Create (or validate) the two FIFOs and durable channel metadata."""
    interval = _validated_interval(heartbeat_interval, field="heartbeat interval")
    channel_dir = run_dir / "channel"
    channel_dir.mkdir(parents=True, exist_ok=True)
    with advisory_lock(f"channel-create:{channel_dir.resolve()}"):
        for name in CHANNEL_ENDPOINTS:
            path = channel_dir / name
            if path.exists():
                if not path.is_fifo():
                    raise ChannelError(f"channel endpoint is not a FIFO: {path}")
            else:
                os.mkfifo(path, 0o600)
        atomic_json(channel_dir / "channel.json", {"schema_version": 1})
    initialize_heartbeat(channel_dir, interval)
    return channel_dir


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ChannelTimeout("channel rendezvous timed out")
    return remaining


def _acknowledgment_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.ack")


@contextmanager
def _channel_lock(path: Path, purpose: str, deadline: float) -> Iterator[None]:
    """Hold one endpoint lock within the caller's transport deadline."""
    lock_path = path.with_name(f"{path.name}.{purpose}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.01, _remaining(deadline)))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_message(path: Path, *, timeout: float) -> dict[str, Any]:
    """Read exactly one locked, newline-delimited JSON mapping with a bounded wait."""
    if not math.isfinite(timeout) or timeout < 0:
        raise ChannelError("timeout must be finite and non-negative")
    deadline = time.monotonic() + timeout
    with _channel_lock(path, "read", deadline):
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        acknowledge = False
        try:
            data = bytearray()
            while b"\n" not in data:
                ready, _, _ = select.select([fd], [], [], _remaining(deadline))
                if not ready:
                    raise ChannelTimeout("channel read timed out")
                chunk = os.read(fd, 65536)
                if not chunk:
                    time.sleep(min(0.01, _remaining(deadline)))
                    continue
                data.extend(chunk)

            # After seeing the writer's final newline, the reader closes this FIFO and
            # creates its acknowledgment while still holding the read lock. The writer
            # retains the write lock until that acknowledgment appears, so queued writers
            # wait on the write lock; they do not wait for this read lock to be released.
            acknowledge = True
            line, trailing = bytes(data).split(b"\n", 1)
            if trailing:
                raise ChannelError("channel frame contains trailing data")
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ChannelError("channel frame is not valid JSON") from exc
            if not isinstance(value, dict):
                raise ChannelError("channel frame must be a JSON object")
            return value
        finally:
            os.close(fd)
            if acknowledge:
                _acknowledgment_path(path).touch(mode=0o600)


def write_message(path: Path, value: Mapping[str, Any], *, timeout: float) -> None:
    """Write one serialized JSON-line frame after bounded writer/reader rendezvous."""
    if not math.isfinite(timeout) or timeout < 0:
        raise ChannelError("timeout must be finite and non-negative")
    encoded = (json.dumps(dict(value), separators=(",", ":")) + "\n").encode()
    deadline = time.monotonic() + timeout
    with _channel_lock(path, "write", deadline):
        acknowledgment = _acknowledgment_path(path)
        with suppress(FileNotFoundError):
            acknowledgment.unlink()
        while True:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as exc:
                if exc.errno != errno.ENXIO:
                    raise
                time.sleep(min(0.01, _remaining(deadline)))
        try:
            written = 0
            while written < len(encoded):
                _, ready, _ = select.select([], [fd], [], _remaining(deadline))
                if not ready:
                    raise ChannelTimeout("channel write timed out")
                try:
                    written += os.write(fd, encoded[written:])
                except BlockingIOError:
                    continue
        finally:
            os.close(fd)
        # Do not hand the writer lock to the next frame until the reader has consumed
        # this one and closed its descriptor. The sidecar is only a rendezvous token;
        # the JSON line remains the complete external frame.
        while not acknowledgment.is_file():
            time.sleep(min(0.01, _remaining(deadline)))
        acknowledgment.unlink()


def _surface(request: Mapping[str, Any], run_id: str, round_number: int) -> dict[str, Any]:
    kind = request.get("kind", "supervisor")
    message = request.get("message") or request.get("task")
    messages = request.get("messages", [])
    if not isinstance(kind, str) or not isinstance(message, str):
        raise ChannelError("supervisor request must contain string kind/message or task")
    if not isinstance(messages, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("role"), str)
        and isinstance(item.get("content"), str)
        for item in messages
    ):
        raise ChannelError("supervisor request messages must contain role/content strings")
    if messages:
        last = messages[-1]
        content = last.get("content") if isinstance(last, dict) else None
        if isinstance(content, str):
            try:
                emitted = json.loads(content)
            except json.JSONDecodeError:
                emitted = None
            if (
                isinstance(emitted, dict)
                and isinstance(emitted.get("kind"), str)
                and isinstance(emitted.get("message"), str)
            ):
                kind = emitted["kind"]
                message = emitted["message"]
                if "options" in emitted:
                    request = {**request, "options": emitted["options"]}
    surface: dict[str, Any] = {"kind": kind, "message": message}
    if kind == "proposal":
        surface["blocking"] = True
    options = request.get("options")
    if options is not None:
        if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
            raise ChannelError("supervisor options must be a list of strings")
        surface["options"] = options
    return {
        "op": "supervisor",
        "run_id": run_id,
        "round": round_number,
        "surface": surface,
        "messages": messages,
    }


def _validated_persisted_surface(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a persisted planner surface before forwarding it."""
    kind = value.get("kind")
    message = value.get("message")
    blocking = value.get("blocking", True)
    if not isinstance(kind, str) or not isinstance(message, str) or not isinstance(blocking, bool):
        raise ChannelError("persisted planner surface has invalid kind, message, or blocking")
    if kind not in PLANNER_SURFACE_KINDS:
        raise ChannelError(f"persisted planner surface has unsupported kind: {kind!r}")
    surface: dict[str, Any] = {"kind": kind, "message": message, "blocking": blocking}
    if "options" in value:
        options = value["options"]
        if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
            raise ChannelError("persisted planner surface options must be a list of strings")
        surface["options"] = options
    return surface


def _validated_heartbeat_surface(
    value: Mapping[str, Any], run_id: str, expected_round: int
) -> dict[str, Any]:
    """Validate the durable, externally mutable heartbeat-surface frame."""
    round_number = value.get("round")
    if (
        not isinstance(round_number, int)
        or isinstance(round_number, bool)
        or round_number != expected_round
    ):
        raise ChannelError("heartbeat surface round does not match the active round")
    if value.get("run_id") != run_id:
        raise ChannelError("heartbeat surface run id does not match the active run")
    surface = value.get("surface")
    message = surface.get("message") if isinstance(surface, Mapping) else None
    if not isinstance(message, str) or not message.strip():
        raise ChannelError("heartbeat surface values are invalid")
    expected = _heartbeat_frame(run_id, expected_round, message)
    if value != expected:
        raise ChannelError("heartbeat surface frame is invalid")
    return expected


def _reply(value: Mapping[str, Any]) -> dict[str, Any]:
    heartbeat = value.get("heartbeat_interval")
    if "heartbeat_interval" in value and heartbeat is not False:
        _validated_interval(heartbeat)
    try:
        commands = parse_commands(value)
    except EditError as exc:
        raise ChannelError(str(exc)) from exc
    completion = value.get("completion")
    reason = value.get("reason")
    if completion is None and commands:
        completes = [command for command in commands if command.op == "complete"]
        complete_reason = completes[-1].payload.get("reason") if completes else None
        if completes and not isinstance(complete_reason, str):
            raise ChannelError("complete edit requires a string reason")
        response: dict[str, Any] = {
            "completion": bool(completes),
            "reason": complete_reason or "versioned edit commands",
            "version": EDIT_PROTOCOL_VERSION,
            "commands": [command.payload for command in commands],
        }
        if not completes:
            response["message"] = "apply live graph edits"
        if "heartbeat_interval" in value:
            response["heartbeat_interval"] = heartbeat
        return response
    if not isinstance(completion, bool) or not isinstance(reason, str):
        raise ChannelError("reply requires boolean completion and string reason")
    if completion:
        response = {"completion": True, "reason": reason}
        if commands:
            response.update(
                {
                    "version": EDIT_PROTOCOL_VERSION,
                    "commands": [command.payload for command in commands],
                }
            )
        if "heartbeat_interval" in value:
            response["heartbeat_interval"] = heartbeat
        return response
    message = value.get("message")
    if not isinstance(message, str):
        raise ChannelError("continue reply requires string message")
    response = {"completion": False, "message": message, "reason": reason}
    if commands:
        response.update(
            {
                "version": EDIT_PROTOCOL_VERSION,
                "commands": [command.payload for command in commands],
            }
        )
    if "heartbeat_interval" in value:
        response["heartbeat_interval"] = heartbeat
    return response


class ProposalPump:
    """Service mid-run proposal round trips without writing graph state off-thread."""

    def __init__(
        self,
        channel_dir: Path,
        run_id: str,
        round_number: int,
        *,
        journal: JournalSink | None = None,
        dispatch_check_in: CheckInDispatcher | None = None,
    ) -> None:
        self._channel_dir = channel_dir
        self._run_id = run_id
        self._round = round_number
        self._proposals: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._replies: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._awaiting_reply = threading.Event()
        self._reply_received = threading.Event()
        self._answered: set[tuple[str, str]] = set()
        self._answer_lock = threading.Lock()
        self._journal = journal or NullJournal()
        self._dispatch_check_in = dispatch_check_in
        self._thread = threading.Thread(target=self._service, daemon=True)
        self._receiver = threading.Thread(target=self._receive, daemon=True)
        self._pacemaker = threading.Thread(target=self._pace, daemon=True)
        self._thread.start()
        self._receiver.start()
        self._pacemaker.start()

    def propose(self, node: str, message: str) -> None:
        signature = (node, message)
        with self._answer_lock:
            if signature in self._answered:
                return
        self._proposals.put(
            {
                "op": "supervisor",
                "run_id": self._run_id,
                "round": self._round,
                "surface": {
                    "kind": "proposal",
                    "message": f"{node}: {message}",
                    "blocking": False,
                },
                "messages": [],
                "proposal_id": f"{node}:{message}",
            }
        )

    def propose_blocking(self, node: str, message: str) -> None:
        """Surface a liveness failure that requires planner intervention."""
        surface = {
            "kind": "proposal",
            "message": f"{node}: {message}",
            "blocking": True,
        }
        # A terminal node can end the graph immediately after this call. Preserve
        # the blocker for the outer supervisor relay, but do not advertise it as
        # reply-ready until either this pump or that relay is listening.
        atomic_json(self._channel_dir / "deferred-blocker.json", surface)
        self._proposals.put(
            {
                "op": "supervisor",
                "run_id": self._run_id,
                "round": self._round,
                "surface": surface,
                "messages": [],
                "proposal_id": f"{node}:{message}",
            }
        )

    def defer_blocking(self, node: str, message: str) -> None:
        """Preserve a terminal blocker for the next reply-ready supervisor relay."""
        atomic_json(
            self._channel_dir / "deferred-blocker.json",
            {
                "kind": "proposal",
                "message": f"{node}: {message}",
                "blocking": True,
            },
        )

    def persist_replies(self) -> None:
        """Persist transport replies on the reconciler's single-writer thread."""
        while True:
            try:
                response = self._replies.get_nowait()
            except queue.Empty:
                return
            atomic_json(self._channel_dir / "planner-verdict.json", response)

    def drain_commands(self) -> tuple[QueuedCommand, ...]:
        """Claim durably accepted commands for this round, rejecting the rest aloud.

        Read from the durable queue rather than from this pump's FIFO receiver: the
        relay competes for the same endpoint and would otherwise consume a frame
        whose commands nothing then applies. Each claimed command keeps its queue
        sequence so the reconciler can answer it by name.
        """
        commands: list[QueuedCommand] = []
        for item in claim_commands(self._channel_dir):
            if item.round != self._round:
                reason = f"submitted for round {item.round}, which is no longer executing"
                self.propose("reconciler", f"rejected {item.command.op}: {reason}")
                self.record_outcome(item.seq, applied=False, reason=reason)
                continue
            commands.append(item)
        return tuple(commands)

    def record_outcome(self, seq: int, *, applied: bool, reason: str) -> None:
        """Answer one claimed command, so `channel-reply` can report its fate."""
        record_command_outcome(self._channel_dir, seq, applied=applied, reason=reason)

    def heartbeat_tick(self) -> None:
        mark_heartbeat_due(self._channel_dir)

    def close(self) -> None:
        self._stop.set()
        self._proposals.put(None)
        self._thread.join()
        self._receiver.join()
        self._pacemaker.join()
        self.persist_replies()
        # Answer every accepted command this round did not: one still queued, and one
        # the reconciler claimed — advancing the cursor — but stopped before deciding.
        # Both are the same failure to a waiting submitter, so both are rejected here
        # rather than left to look applied to a reader of the ledger.
        claim_commands(self._channel_dir)
        for item in unanswered_commands(self._channel_dir):
            reason = f"round {self._round} finished before the reconciler answered it"
            self._journal.append(
                "edit-rejected",
                detail={
                    # `EditPayload` is a closed TypedDict union that the journal's open
                    # `DetailValue` cannot express; the payload was validated as JSON by
                    # `parse_commands` before it was ever queued.
                    "command": cast(Any, item.command.payload),
                    "round": item.round,
                    "reason": reason,
                },
            )
            self.record_outcome(item.seq, applied=False, reason=reason)

    def _pace(self) -> None:
        """Keep checking the durable clock while the reconciler is inside a node."""
        while not self._stop.wait(0.05):
            try:
                mark_heartbeat_due(self._channel_dir)
                state = heartbeat_state(self._channel_dir)
            except (ChannelError, ConfigError, OSError):
                return
            # A queued update no longer gates the next check-in. Exactly one stays
            # pending — the fresh one replaces it — so the invariant holds while the
            # reporting keeps moving: an ignored planner gets a current update every
            # interval rather than one three-hour-old snapshot and silence. The lease
            # `claim_heartbeat` takes is what keeps two from being dispatched at once.
            if (
                state is not None
                and state["enabled"]
                and state["due"]
                and not (self._channel_dir / "planner-pending.json").is_file()
                and self._dispatch_check_in is not None
                and claim_heartbeat(self._channel_dir)
            ):
                threading.Thread(target=self._dispatch_claimed_check_in, daemon=True).start()

    def _dispatch_claimed_check_in(self) -> None:
        try:
            assert self._dispatch_check_in is not None
            self._dispatch_check_in()
            # The lease covers the dispatch, not the reading. Handing it back here is
            # what lets the next interval fall due; held to consumption, one check-in
            # nobody read was the last the run ever sent.
            finish_heartbeat_attempt(self._channel_dir, succeeded=True)
        except Exception as exc:
            fail_heartbeat_claim(self._channel_dir)
            with (
                advisory_lock(f"channel-check-in-log:{self._channel_dir.resolve()}"),
                (self._channel_dir / "check-in.log").open("a", encoding="utf-8") as stream,
            ):
                stream.write(
                    json.dumps(
                        {
                            "at": time.time(),
                            "succeeded": False,
                            "detail": f"{type(exc).__name__}: {exc}",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )

    def _service(self) -> None:
        while (proposal := self._proposals.get()) is not None:
            surface = proposal["surface"]
            node, message = str(surface["message"]).split(": ", 1)
            signature = (node, message)
            with self._answer_lock:
                if signature in self._answered:
                    continue
            if surface.get("blocking") is not True:
                atomic_json(self._channel_dir / "planner-pending.json", surface)
            # Recorded before the write, not after it: the write blocks until a planner
            # reads the frame, so a record written after it would only ever describe a
            # surface that was delivered. This one says the surface was *sent* — and
            # a journal that cannot take it must not be what stops the sending.
            with suppress(JournalError, OSError):
                self._journal.append(
                    "planner-surface-queued",
                    detail=_queued_detail(surface, source="proposal", workstream=node),
                )
            self._reply_received.clear()
            self._awaiting_reply.set()
            while True:
                if self._stop.is_set():
                    return
                try:
                    write_message(self._channel_dir / "up.fifo", proposal, timeout=0.1)
                    record_surface(self._channel_dir)
                    self._journal.append(
                        "planner-surfaced",
                        detail={
                            "kind": str(surface["kind"]),
                            "message": str(surface["message"]),
                            "blocking": bool(surface["blocking"]),
                        },
                    )
                    break
                except ChannelTimeout:
                    continue
                except OSError:
                    return
            while not self._stop.is_set() and not self._reply_received.wait(0.1):
                pass
            if self._reply_received.is_set():
                with self._answer_lock:
                    self._answered.add(signature)
                with suppress(FileNotFoundError):
                    (self._channel_dir / "planner-pending.json").unlink()
            self._awaiting_reply.clear()

    def _receive(self) -> None:
        """Continuously receive planner edits, independent of proposal timing."""
        while not self._stop.is_set():
            try:
                response = _reply(read_message(self._channel_dir / "down.fifo", timeout=0.1))
                apply_heartbeat_reply(self._channel_dir, response)
                # Commands travel the durable queue, not this frame: whichever reader
                # wins the endpoint, the reconciler still claims every accepted edit.
                self._replies.put(response)
                if self._awaiting_reply.is_set():
                    self._reply_received.set()
            except ChannelTimeout:
                continue
            except (ChannelError, OSError):
                return


# llmlint: ignore[names_match_behavior] onejudge sends final evals to its supervisor command
def relay_supervisor(channel_dir: Path, run_id: str, round_number: int, *, timeout: float) -> int:
    """Relay one command-provider supervisor request to the live planner."""
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise ChannelError("supervisor request must be a JSON object")
        operation = request.get("op")
        if operation not in {"supervisor", "judge"}:
            raise ChannelError("relay request op must be 'supervisor' or 'judge'")
        if operation == "judge":
            judge_kind = request.get("kind")
            if judge_kind not in {"boolean", "score"}:
                raise ChannelError("judge kind must be 'boolean' or 'score'")
            state_path = channel_dir / "planner-verdict.json"
            completed = False
            if state_path.is_file():
                persisted_completion = load_mapping(state_path).get("completion")
                if not isinstance(persisted_completion, bool):
                    raise ChannelError("persisted planner completion must be boolean")
                completed = persisted_completion
            if judge_kind == "boolean":
                print(
                    json.dumps({"value": completed, "reason": "mirrors the live planner verdict"})
                )
            else:
                maximum = request.get("max", 5)
                if (
                    not isinstance(maximum, int | float)
                    or isinstance(maximum, bool)
                    or not math.isfinite(maximum)
                    or maximum < 0
                ):
                    raise ChannelError("numeric judge max must be a non-negative number")
                print(json.dumps({"value": maximum, "reason": "live planner completed the run"}))
            return 0
        surfaced = _surface(request, run_id, round_number)
        pending_path = channel_dir / "planner-pending.json"
        deferred_blocker = channel_dir / "deferred-blocker.json"
        if deferred_blocker.is_file():
            surfaced["surface"] = _validated_persisted_surface(load_mapping(deferred_blocker))
        elif pending_path.is_file():
            pending = load_mapping(pending_path)
            if pending.get("blocking") is True:
                surfaced["surface"] = _validated_persisted_surface(pending)
        atomic_json(pending_path, surfaced["surface"])
        write_message(channel_dir / "up.fifo", surfaced, timeout=timeout)
        record_surface(channel_dir)
        response = _reply(read_message(channel_dir / "down.fifo", timeout=timeout))
        apply_heartbeat_reply(channel_dir, response)
        with suppress(FileNotFoundError):
            (channel_dir / "planner-pending.json").unlink()
        with suppress(FileNotFoundError):
            deferred_blocker.unlink()
        atomic_json(channel_dir / "planner-verdict.json", response)
    except (
        ChannelError,
        ChannelTimeout,
        ConfigError,
        EditError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        print(f"relay-supervisor: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(response))
    return 0


def _finished(run_dir: Path) -> bool:
    report = run_dir / "orchestrator" / "report.json"
    if report.is_file() and report.stat().st_size > 0:
        try:
            load_mapping(report)
        except (ConfigError, OSError):
            pass
        else:
            return True
    orchestrator_status = run_dir / "orchestrator" / "status.json"
    if orchestrator_status.is_file():
        try:
            owner = load_mapping(orchestrator_status)
            pid = owner.get("pid")
            if (
                owner.get("status") == "running"
                and isinstance(pid, int)
                and not isinstance(pid, bool)
                and owner.get("host") == socket.gethostname()
            ):
                os.kill(pid, 0)
                return False
        except (ConfigError, OSError, ProcessLookupError):
            pass
    latest = latest_round(run_dir)
    status_path = (
        latest[1] / "status.json"
        if latest is not None
        else run_dir / "orchestrator" / "status.json"
    )
    if not status_path.is_file():
        return False
    try:
        status = load_mapping(status_path)
        pid = status.get("pid")
        host = status.get("host")
        if (
            status.get("status") != "running"
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid < 1
        ):
            return True
        if not isinstance(host, str) or host != socket.gethostname():
            return False
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, ValueError):
        return False
    return False


def next_surface(run_dir: Path, *, timeout: float) -> dict[str, Any]:
    """Consume this run's next planner surface, or report why there is none.

    The one place a surface is taken off the channel, so `channel-next` and `watch`
    cannot drift into two different ideas of what "attached" means: the durable
    check-in queue is drained first, then the live FIFO, and a settled run answers
    ``{"status": "finished"}`` rather than waiting out the timeout.
    """
    discard_surface_from_a_finished_round(run_dir)
    heartbeat_surface = run_dir / "channel" / HEARTBEAT_SURFACE_FILE
    pending_reply = (run_dir / "channel" / "planner-pending.json").is_file()
    latest = latest_round(run_dir)
    round_finished = latest is not None and (latest[1] / "result.json").is_file()
    if pending_reply or round_finished:
        with suppress(FileNotFoundError):
            heartbeat_surface.unlink()
            # The queued update is gone unread, so the claim it was held under has to
            # go with it. Left set, it suppresses every later check-in for the rest of
            # the run: the planner who ignored the channel longest would be told least.
            release_heartbeat_claim(run_dir / "channel")
    with advisory_lock(f"channel-heartbeat-surface:{heartbeat_surface.resolve()}"):
        if heartbeat_surface.is_file():
            if latest is None:
                raise ChannelError("heartbeat surface has no active round")
            value = _validated_heartbeat_surface(
                load_mapping(heartbeat_surface), run_dir.name, latest[0]
            )
            heartbeat_surface.unlink()
            surface = value["surface"]
            record_surface(run_dir / "channel")
            open_journal(run_dir, RunId(run_dir.name), int(value["round"])).append(
                "planner-surfaced",
                detail={
                    "kind": "heartbeat",
                    "message": str(surface["message"]),
                    "blocking": False,
                },
            )
            return value
    if not pending_reply and _finished(run_dir):
        return {"status": "finished"}
    try:
        return read_message(run_dir / "channel" / "up.fifo", timeout=timeout)
    except ChannelTimeout:
        return (
            {"status": "finished"} if _finished(run_dir) else {"status": "running", "surface": None}
        )


# llmlint: ignore[changed_behavior_has_e2e] the real bridge timeout/success/reattach journey is e2e;
# malformed framing and transport failures are deterministic boundary branches exercised in unit.
def main_next(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read the next live orchestrator surface")
    parser.add_argument("run_id")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        run_dir = args.runs_dir / resolve_supervision_run(args.runs_dir, args.run_id)
    except ConfigError as exc:
        print(f"channel-next: {exc}", file=sys.stderr)
        return 2
    try:
        value = next_surface(run_dir, timeout=args.timeout)
    except (ChannelError, ConfigError, OSError) as exc:
        print(f"channel-next: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value))
    return 0


# llmlint: ignore[changed_behavior_has_e2e] the real reply FIFO journey is e2e while malformed
# JSON, reply contracts, and absent-rendezvous errors are exhaustively exercised in unit tests.
def main_reply(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reply to the live orchestrator supervisor")
    parser.add_argument("run_id")
    parser.add_argument("reply", nargs="?", default="-")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    accepted: tuple[int, ...] = ()
    try:
        raw = (
            sys.stdin.read() if args.reply == "-" else Path(args.reply).read_text(encoding="utf-8")
        )
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ChannelError("reply must be a JSON object")
        # The reply now waits for the reconciler's verdict, so its bound has to be one.
        _validated_interval(args.timeout, field="timeout")
        resolved = resolve_supervision_run(args.runs_dir, args.run_id)
        run_dir = args.runs_dir / resolved
        response = _reply(value)
        commands = parse_commands(response)
        # `complete` is a closeout verdict rather than a graph mutation, so it rides
        # the reply itself and stays legal at a round boundary where no graph is live.
        edits = [command for command in commands if command.op != "complete"]
        if commands:
            try:
                round_number: int | None = live_round(run_dir)
            except ChannelError:
                if edits:
                    raise
                round_number = None
            if round_number is not None:
                validate_commands(run_dir, round_number, commands)
                accepted = submit_commands(run_dir / "channel", commands, round_number=round_number)
        write_message(run_dir / "channel" / "down.fifo", response, timeout=args.timeout)
    except (
        ChannelError,
        ChannelTimeout,
        ConfigError,
        EditError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        # Accepted edits are already durable, so a transport failure after acceptance
        # must not read as "nothing happened": resubmitting them would apply them twice.
        queued = (
            ""
            if not accepted
            else f"; edit(s) {', '.join(f'#{seq}' for seq in accepted)} were accepted and "
            "will be applied — do not resubmit them"
        )
        print(
            f"channel-reply: {exc}; check the run id and reply shape, then rerun the command"
            f"{queued}",
            file=sys.stderr,
        )
        return 2
    if not accepted:
        return 0
    # Apply-or-reject, synchronously. An edit that passed submission can still lose a
    # race to the frontier it was validated against, and "queued" is not an answer:
    # this waits for the reconciler's verdict and reports a rejection to the caller
    # that issued it, rather than leaving it as a proposal to be noticed later.
    verdicts = await_command_outcomes(run_dir / "channel", accepted, timeout=args.timeout)
    if verdicts.rejected:
        for outcome in verdicts.rejected:
            print(
                f"channel-reply: edit #{outcome.seq} was rejected by the reconciler: "
                f"{outcome.reason}",
                file=sys.stderr,
            )
        return 2
    if verdicts.unreconciled:
        print(
            "channel-reply: edit(s) "
            + ", ".join(f"#{seq}" for seq in verdicts.unreconciled)
            + f" were accepted but not reconciled within {args.timeout:g}s; they remain "
            "queued — check `just monitor` rather than resubmitting them",
            file=sys.stderr,
        )
        return 1
    return 0


def main_surface(argv: list[str] | None = None) -> int:
    """Queue one agent-authored, non-blocking status update for the planner."""
    parser = argparse.ArgumentParser(description="Surface a non-blocking planner status update")
    parser.add_argument("run_id")
    parser.add_argument("message", nargs="?", default="-")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        _validated_interval(args.timeout, field="timeout")
        message = sys.stdin.read() if args.message == "-" else args.message
        if not message.strip():
            raise ChannelError("status update must be a non-empty string")
        resolved = resolve_supervision_run(args.runs_dir, args.run_id)
        channel_dir = args.runs_dir / resolved / "channel"
        latest = latest_round(args.runs_dir / resolved)
        if latest is None:
            raise ChannelError("status update requires an active round")
        destination = channel_dir / HEARTBEAT_SURFACE_FILE
        with advisory_lock(f"channel-heartbeat-surface:{destination.resolve()}"):
            state = heartbeat_state(channel_dir)
            if state is None or not state["enabled"] or not state["due"] or not state["in_flight"]:
                raise ChannelError("status update has no active check-in claim")
            if (channel_dir / "planner-pending.json").is_file():
                raise ChannelError("a planner surface is already pending")
            # An update already queued is *replaced* rather than refused. The claim
            # above still admits one check-in at a time, so this can only be the next
            # interval's agent overwriting a snapshot nobody read — and the current
            # description of the run is strictly the more useful of the two.
            frame = _heartbeat_frame(str(resolved), latest[0], message)
            atomic_json(destination, frame)
        # Journalled outside the queue lock and only once the update is durable: this
        # is the record that separates "nobody sent an update" from "an update was
        # sent and nobody read it". Delivery still appends `planner-surfaced`, which
        # may never happen — that gap is the evidence, so it is written here rather
        # than being folded into the delivered record. A journal this cannot write is
        # not allowed to lose the update itself, per the journal's own contract.
        with suppress(JournalError, OSError):
            open_journal(args.runs_dir / resolved, RunId(resolved), latest[0]).append(
                "planner-surface-queued",
                detail=_queued_detail(frame["surface"], source="check-in", workstream=None),
            )
    except (ChannelError, ChannelTimeout, ConfigError, OSError) as exc:
        print(
            f"channel-surface: {exc}; correct the input or channel state, then retry",
            file=sys.stderr,
        )
        return 2
    return 0


def _main_convenience(kind: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Send a planner {kind} reply")
    parser.add_argument("run_id")
    parser.add_argument("text", nargs="?")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if kind == "approve" and args.text is not None:
        parser.error("approve does not accept a message")
    if kind in {"reject", "continue"} and not args.text:
        parser.error(f"{kind} requires a message")
    payload = (
        {"completion": True, "reason": "approved"}
        if kind == "approve"
        else {"completion": False, "reason": args.text, "message": args.text}
    )
    import io

    original = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps(payload))
        return main_reply(
            [args.run_id, "--runs-dir", str(args.runs_dir), "--timeout", str(args.timeout)]
        )
    finally:
        sys.stdin = original


def main_approve(argv: list[str] | None = None) -> int:
    return _main_convenience("approve", argv)


def main_reject(argv: list[str] | None = None) -> int:
    return _main_convenience("reject", argv)


def main_continue(argv: list[str] | None = None) -> int:
    return _main_convenience("continue", argv)


def main_relay(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("channel_dir", type=Path)
    parser.add_argument("run_id")
    parser.add_argument("round", type=int)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        run_id = str(validate_run_id(args.run_id))
        if args.round < 1:
            raise ChannelError("round must be a positive integer")
        channel_dir = args.channel_dir.resolve()
        if channel_dir.parent.name != run_id:
            raise ChannelError("channel directory must belong to the requested run id")
        metadata = load_mapping(channel_dir / "channel.json")
        if metadata.get("schema_version") != 1 or not all(
            (channel_dir / name).is_fifo() for name in CHANNEL_ENDPOINTS
        ):
            raise ChannelError("channel directory has invalid metadata or endpoints")
    except (ChannelError, ConfigError, ValueError) as exc:
        parser.error(str(exc))
    return relay_supervisor(channel_dir, run_id, args.round, timeout=args.timeout)


if __name__ == "__main__":  # pragma: no cover - exercised through the installed console script
    raise SystemExit(main_relay())
