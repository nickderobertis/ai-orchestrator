"""The supervisory tier's own record: what drives a run, and what it is doing.

oneharness records these sessions; for most of this repository's life nothing served
them, which is why the tier that drives every run was missing from every
history-derived view. Serving them is `timeline`'s job, so this module is only the
part history cannot supply, and it holds two things:

* **A bounded local capture.** One small JSON record per supervisory session, written
  by the dispatch layer at the seam it already controls: `dispatch.launch_orchestrator`
  opens the orchestrator's, `channel.relay_supervisor` appends one bounded turn per
  orchestrator turn (it is invoked once per turn and is the only in-process view of
  one), and the check-in dispatcher opens and closes its own. It carries session
  metadata, timing, a bounded transcript, and — when the harness refused the write —
  the failure itself, so the failure is *recorded* rather than inferred from a session
  that is missing. It is deliberately a summary and never a replacement: when history
  did record the session, the served span points at that conversation and the capture
  stands down.
* **The driver's observable state.** Whether the launched orchestrator's pid is alive,
  which phase of its loop the run's own recorded state places it in, and how long ago
  anything of it was last observed doing something. Derived, never asserted by the
  agent: an agent that has stopped talking cannot report that it stopped.

Nothing here is authoritative. The journal and the ledger still decide; this only
makes the tier that drives them visible.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, cast, get_args

from .config import ConfigError
from .coordination import advisory_lock, atomic_json
from .labels import AgentRole
from .redaction import redact
from .runs import latest_round, load_mapping, process_may_be_live

#: Where one run's supervisory captures live, beside the journal they explain.
CAPTURE_DIR = "supervisory"
CAPTURE_SCHEMA_VERSION = 1

#: How much of one supervisory turn the capture keeps. This is a *summary* whose whole
#: point is to survive when the transcript did not, so it is bounded on both axes: the
#: newest few turns, each cut to a readable head. A run that captured everything would
#: reimplement history inside the run directory.
MAX_CAPTURED_TURNS = 20
MAX_CAPTURED_TURN_CHARS = 800

#: Session names reach the filesystem, so they are validated rather than sanitized: the
#: two producers (`orchestrator-<run-id>`, `check-in-<run-id>-<round>`) already satisfy
#: this, and anything else is a caller bug that must be loud instead of writing a
#: capture under a name no reader will look for. The bound is the filesystem's own,
#: less the extension, rather than an arbitrary one — a run id is only bounded by what
#: its own directory name can carry, so a shorter cap here would refuse a run this
#: harness had already created.
MAX_SESSION_NAME_CHARS = 250
_SESSION_NAME = re.compile(rf"[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_SESSION_NAME_CHARS - 1}}}\Z")

#: How oneharness reports a history write it could not complete. Owned here because
#: two readers need the same answer: `graph` classifies a dispatch that died this way
#: as an infrastructure failure, and the capture records the same text as the reason a
#: session is missing from history.
#:
#: Matched for any harness. This was once believed to be codex-specific and is not;
#: `docs/telemetry.md` carries the measurement that ruled that out, and scoping these
#: patterns to one harness would silently stop capturing the others.
HISTORY_WRITE_FAILURE_PATTERNS = (
    re.compile(r"cannot write v[0-9]+(?:\.[0-9]+)* history telemetry", re.IGNORECASE),
    re.compile(
        r"new history (?:record|run) lacks complete v[0-9]+(?:\.[0-9]+)* telemetry",
        re.IGNORECASE,
    ),
)

#: The semantic roles a capture may name. `labels.AgentRole` is the one source: a
#: capture is written by the dispatch layer for a role that layer already stamps, so a
#: record naming anything else was not written by this scheme and has no claim on a run.
_AGENT_ROLES: frozenset[str] = frozenset(get_args(AgentRole))

#: Where one supervisory session stands. Its two *terminal* words are taken from the
#: vocabulary `conversations._status_state` folds a record status onto, so a served
#: span reads identically whether it came from history or from a capture. That
#: borrowing is what makes this a second source, so it has a drift gate:
#: `tests/test_supervisory.py` fails if a terminal status here stops being one that
#: fold can produce. ``running`` is deliberately *not* borrowed — a capture is written
#: open and closed later, and the fold has no word for a session still speaking.
CaptureStatus = Literal["running", "completed", "failed"]
_CAPTURE_STATUSES: frozenset[str] = frozenset(get_args(CaptureStatus))

#: Which part of its loop the launched orchestrator is in, derived from what the run
#: itself recorded. Mirrored by the ``dag-model`` schema and ``docs/dag-ui/design.md``
#: and reconciled by ``scripts/check-dag-state-contract.py``.
#:
#: ``surfacing`` wins over a running round because a blocking surface is what the
#: orchestrator is *waiting on*, whatever else it started; ``finished`` wins over
#: everything because a written report ends the loop.
SupervisoryPhase = Literal[
    "starting",
    "driving-round",
    "executing-run-plan",
    "reviewing-results",
    "surfacing",
    "finished",
]

_ORCHESTRATOR_DIR = "orchestrator"


class CapturedTurn(TypedDict):
    """One bounded supervisory turn: when it was recorded, and its head."""

    at: str
    text: str


class CaptureRecord(TypedDict):
    """The on-disk capture. Written by the dispatch layer, read by the timeline."""

    schema_version: int
    session: str
    agent_role: str
    started_at: str
    status: str
    finished_at: NotRequired[str | None]
    persona: NotRequired[str]
    round: NotRequired[int]
    harness: NotRequired[str]
    history_failure: NotRequired[str]
    turns: NotRequired[list[CapturedTurn]]


class CaptureError(ValueError):
    """A capture was asked for under a session name this scheme cannot write."""


@dataclass(frozen=True)
class SupervisoryCapture:
    """One validated capture, as every reader sees it.

    Every field is rechecked on read: a capture is a file on disk that a concurrent
    writer or a hand edit can have replaced, and a malformed one must degrade to "not
    captured" rather than reach a served payload.
    """

    session: str
    agent_role: str
    started_at: str
    status: CaptureStatus
    finished_at: str | None = None
    persona: str | None = None
    round: int | None = None
    harness: str | None = None
    history_failure: str | None = None
    turns: tuple[CapturedTurn, ...] = ()

    @property
    def transcript_tail(self) -> str:
        """The captured turns as one bounded block, newest last."""
        return "\n".join(f"{turn['at']} {turn['text']}" for turn in self.turns)


def history_write_failure(text: str | None) -> str | None:
    """The bounded harness history-write failure ``text`` reports, if it reports one.

    Matched on a *tail* by every caller here, because the producing stream is a whole
    harness log and the answer is a one-line reason a session is missing.
    """
    if not text:
        return None
    for line in reversed(text.splitlines()):
        if any(pattern.search(line) for pattern in HISTORY_WRITE_FAILURE_PATTERNS):
            return bounded_note(line)
    return None


def bounded_note(raw: str) -> str:
    """One recorded note, redacted and cut to a single readable line."""
    return " ".join(redact(raw[:MAX_CAPTURED_TURN_CHARS]).split())


def validate_session(session: object) -> str:
    """Return ``session`` when this scheme can carry it, else raise `CaptureError`."""
    if not isinstance(session, str) or _SESSION_NAME.match(session) is None:
        raise CaptureError(
            f"supervisory session name {session!r} must be 1-{MAX_SESSION_NAME_CHARS} ASCII "
            "letters/digits/dot/underscore/hyphen starting alphanumeric"
        )
    return session


def validate_agent_role(agent_role: object) -> str:
    """Return ``agent_role`` when it is one this scheme serves, else raise `CaptureError`.

    The role is what a served span is attributed to and what the fallback matches a
    recorded conversation by, so it is a contract rather than a free label. Checked on
    the way in as well as on the way out: a reader that rejects an unknown role turns
    a bad write into a capture silently missing from the timeline, which is exactly
    the invisibility the capture exists to end.
    """
    if not isinstance(agent_role, str) or agent_role not in _AGENT_ROLES:
        raise CaptureError(
            f"supervisory agent role {agent_role!r} must be one of {sorted(_AGENT_ROLES)}"
        )
    return agent_role


def capture_path(run_dir: Path, session: str) -> Path:
    return run_dir / CAPTURE_DIR / f"{validate_session(session)}.json"


def _now_stamp(at: float | None) -> str:
    return datetime.fromtimestamp(time.time() if at is None else at, UTC).isoformat()


def open_capture(
    run_dir: Path,
    *,
    session: str,
    agent_role: str,
    persona: str | None = None,
    round_number: int | None = None,
    started_at: float | None = None,
) -> Path:
    """Record that one supervisory session has started, and return its capture path.

    Written before the session runs, so a session that never returns — the wedged
    orchestrator this exists for — still has a start, a role, and an open end.

    One capture per supervisory *scope*, not per attempt: a check-in that failed is
    retried within the same round, and a file per attempt would grow without bound
    while a run kept retrying. Re-opening therefore reuses the existing record — the
    accumulated bounded turns and any harness history-write refusal already observed
    carry forward, while ``status`` and the open end describe the attempt now running.
    A refusal is sticky on purpose: "this round's check-in had a session the harness
    refused to record" stays true once it has happened, and a later attempt that
    succeeded must not erase the reason an earlier session is missing from history.
    """
    path = capture_path(run_dir, session)
    previous = _existing(path)
    record: CaptureRecord = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "session": session,
        "agent_role": validate_agent_role(agent_role),
        "started_at": previous["started_at"] if previous else _now_stamp(started_at),
        "status": "running",
        "finished_at": None,
        "turns": list(previous["turns"] or []) if previous else [],
    }
    if persona:
        record["persona"] = persona
    if round_number is not None:
        record["round"] = round_number
    if previous is not None and (failure := previous.get("history_failure")):
        record["history_failure"] = failure
    atomic_json(path, record)
    return path


def _existing(path: Path) -> CaptureRecord | None:
    """The capture already at ``path``, or ``None`` when there is no usable one."""
    try:
        return _record(load_mapping(path))
    except (ConfigError, OSError):
        return None


def _mutate(run_dir: Path, session: str, apply: Callable[[CaptureRecord], None]) -> bool:
    """Read-modify-write one capture under its own lock; ``False`` when there is none.

    Silent on a missing or unreadable capture by design — including a session this
    scheme cannot name: capture is an *addition* to a dispatch, and failing a live
    orchestrator turn because its summary file went missing, or because its run id
    was longer than a filename, would trade the thing being recorded for the record
    of it. Only `open_capture` is loud, at the seam that chose the name.

    An absent capture is answered before the lock rather than inside it. This runs on
    the orchestrator's own turn boundary, and a `run-plan` driven outside `just
    orchestrate` has no capture at all — so the common case must not pay for a lock
    file, a wait, and an fsync to discover there is nothing to write.
    """
    try:
        path = capture_path(run_dir, session)
    except CaptureError:
        return False
    if not path.is_file():
        return False
    try:
        with advisory_lock(f"supervisory-capture:{path}"):
            try:
                raw = load_mapping(path)
            except (ConfigError, OSError):
                return False
            record = _record(raw)
            if record is None:
                return False
            apply(record)
            atomic_json(path, record)
    except (ConfigError, OSError, TimeoutError):
        return False
    return True


def record_turn(run_dir: Path, session: str, text: str, *, at: float | None = None) -> bool:
    """Append one bounded turn to an open capture, dropping the oldest past the cap."""
    stamp = _now_stamp(at)
    note = bounded_note(text)
    if not note:
        return False

    def apply(record: CaptureRecord) -> None:
        turns = list(record.get("turns") or [])
        turns.append({"at": stamp, "text": note})
        record["turns"] = turns[-MAX_CAPTURED_TURNS:]

    return _mutate(run_dir, session, apply)


def close_capture(
    run_dir: Path,
    session: str,
    *,
    status: CaptureStatus,
    harness: str | None = None,
    history_failure: str | None = None,
    finished_at: float | None = None,
) -> bool:
    """Close one capture, recording the harness history-write failure when there was one."""
    stamp = _now_stamp(finished_at)

    def apply(record: CaptureRecord) -> None:
        record["status"] = status
        record["finished_at"] = stamp
        if harness:
            record["harness"] = harness
        if history_failure:
            record["history_failure"] = bounded_note(history_failure)

    return _mutate(run_dir, session, apply)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _stamp_text(value: object) -> str | None:
    """One recorded timestamp string, or ``None`` when it is not one."""
    text = _text(value)
    return text if text is not None and _parse(text) is not None else None


def _turns(value: object) -> tuple[CapturedTurn, ...]:
    if not isinstance(value, list):
        return ()
    found: list[CapturedTurn] = []
    for item in value[-MAX_CAPTURED_TURNS:]:
        if not isinstance(item, dict):
            continue
        at = _text(item.get("at"))
        text = _text(item.get("text"))
        # A turn nothing can place in time is dropped rather than served: it becomes an
        # event's `at` on a rendered timeline, where an unparseable one is a lie.
        if at is not None and text is not None and _parse(at) is not None:
            found.append({"at": at, "text": text[:MAX_CAPTURED_TURN_CHARS]})
    return tuple(found)


def _capture_status(value: str) -> CaptureStatus:
    """One recorded status, folded onto the vocabulary a served span may carry.

    An unrecognised value reads as still running rather than as a terminal state: a
    capture is written open and closed by a later call, so the honest reading of a
    status this scheme never wrote is that nothing has closed it.
    """
    return cast(CaptureStatus, value) if value in _CAPTURE_STATUSES else "running"


def _record(raw: Mapping[str, Any]) -> CaptureRecord | None:
    """One raw capture mapping as the mutable record, or ``None`` when unusable.

    Every identifying field is revalidated here rather than trusted for having been
    written by `open_capture`: this is a file on disk that a hand edit can have
    replaced, and what it carries becomes a served span's id, its role label, and its
    place in time. A name this scheme would refuse to write, a role outside the one
    `labels.AgentRole` declares, or a start nothing can parse each make the whole
    record absent rather than reaching a payload as any of the three.
    """
    role = _text(raw.get("agent_role"))
    started = _text(raw.get("started_at"))
    try:
        session = validate_session(raw.get("session"))
    except CaptureError:
        return None
    if (
        raw.get("schema_version") != CAPTURE_SCHEMA_VERSION
        or role not in _AGENT_ROLES
        or started is None
        or _parse(started) is None
    ):
        return None
    assert role is not None
    record: CaptureRecord = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "session": session,
        "agent_role": role,
        "started_at": started,
        "status": _text(raw.get("status")) or "running",
        # An end nothing can parse reads as "not closed", which is the honest state for
        # a capture whose finish this scheme cannot place in time.
        "finished_at": _stamp_text(raw.get("finished_at")),
        "turns": list(_turns(raw.get("turns"))),
    }
    if (persona := _text(raw.get("persona"))) is not None:
        record["persona"] = persona
    if (harness := _text(raw.get("harness"))) is not None:
        record["harness"] = harness
    if (failure := _text(raw.get("history_failure"))) is not None:
        # Rebounded on read, not only on write: the file can have been replaced since,
        # and an unbounded reason would reach a served span as a whole harness log.
        record["history_failure"] = bounded_note(failure)
    number = raw.get("round")
    if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
        record["round"] = number
    return record


def _capture(raw: Mapping[str, Any]) -> SupervisoryCapture | None:
    record = _record(raw)
    if record is None:
        return None
    return SupervisoryCapture(
        session=record["session"],
        agent_role=record["agent_role"],
        started_at=record["started_at"],
        status=_capture_status(record["status"]),
        finished_at=record.get("finished_at"),
        persona=record.get("persona"),
        round=record.get("round"),
        harness=record.get("harness"),
        history_failure=record.get("history_failure"),
        turns=_turns(record.get("turns")),
    )


def load_captures(run_dir: Path) -> list[SupervisoryCapture]:
    """Every readable capture of one run, oldest start first.

    An unreadable or malformed file is skipped rather than raised: this is an optional
    source that exists to add a session a view would otherwise lack, so a bad record
    must cost only itself.
    """
    directory = run_dir / CAPTURE_DIR
    if not directory.is_dir():
        return []
    try:
        entries = sorted(path for path in directory.iterdir() if path.suffix == ".json")
    except OSError:
        return []
    found: list[SupervisoryCapture] = []
    for path in entries:
        try:
            raw = load_mapping(path)
        except (ConfigError, OSError):
            continue
        if (capture := _capture(raw)) is not None:
            found.append(capture)
    return sorted(found, key=lambda item: (item.started_at, item.session))


@dataclass(frozen=True)
class DriverState:
    """What this host can honestly say about the process driving one run.

    ``alive`` is the recorded pid's liveness under `runs.process_may_be_live`, which
    resolves every uncertainty toward "still there" — so a driver reported dead here
    is one this host *proved* is gone, which is exactly the claim a planner needs
    before concluding that nothing is driving the run.
    """

    session: str
    pid: int | None
    alive: bool
    phase: SupervisoryPhase
    round: int | None
    #: When this host last observed the driver doing anything, as the newest of the
    #: evidence in `_last_activity_at`, or ``None`` when nothing timeable was recorded.
    #: Deliberately *activity* and not "model request": only a captured turn proves a
    #: turn, and the other evidence is a file the driver's own processes write.
    last_activity_at: float | None
    history_failure: str | None = None

    def last_activity_age(self, *, now: float | None = None) -> float | None:
        if self.last_activity_at is None:
            return None
        return max(0.0, (time.time() if now is None else now) - self.last_activity_at)

    @property
    def dead(self) -> bool:
        """Proven gone while its loop had not finished — nothing is driving this run."""
        return not self.alive and self.phase != "finished"

    def describe(self, *, now: float | None = None) -> str:
        """One planner-facing line: liveness first, then phase, then how long silent."""
        where = f"{self.phase}" + (f" (round {self.round})" if self.round is not None else "")
        pid = f"pid {self.pid}" if self.pid is not None else "no recorded pid"
        age = self.last_activity_age(now=now)
        elapsed = (
            "no observed activity"
            if age is None
            else f"last observed activity {int(age) // 60}m{int(age) % 60:02d} ago"
        )
        if self.dead:
            return (
                f"DRIVER DEAD ({pid} is gone) — phase {where}, {elapsed}; "
                "nothing is driving this run"
            )
        # "running", never "alive": these views already use a bare ` alive ` to name a
        # *node*, and a run-level line wearing the same word made the node's own row
        # unfindable in the output an operator (and a test) selects lines from.
        liveness = "running" if self.alive else "stopped"
        return f"driver {liveness} ({pid}) — phase {where}, {elapsed}"


def _launch_status(run_dir: Path) -> Mapping[str, Any] | None:
    try:
        return load_mapping(run_dir / _ORCHESTRATOR_DIR / "status.json")
    except (ConfigError, OSError):
        return None


def _reported(run_dir: Path) -> bool:
    """Whether the launched orchestrator wrote the report that ends its loop."""
    report = run_dir / _ORCHESTRATOR_DIR / "report.json"
    try:
        return report.is_file() and report.stat().st_size > 0
    except OSError:
        return False


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _stderr_tail(run_dir: Path, limit: int = 8_192) -> str:
    """The tail of the driver's own harness stderr, bounded.

    Read from the end because the interesting line is the last one: this is a live
    log that grows for hours, and the reason a session is missing from history is
    whatever the harness said most recently.
    """
    path = run_dir / _ORCHESTRATOR_DIR / "stderr.log"
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _captured_failure(captures: Sequence[SupervisoryCapture]) -> str | None:
    """The newest harness history-write refusal any of this run's captures recorded.

    Reported on the driver's line because it is a fact about the *tier*, not about one
    session: a refused write means some supervisory transcript this run produced is not
    in history, and the planner reading that line is the one who needs to know that the
    capture is all there is. The driver's own harness log is preferred when it carries
    one, since that is the only place a detached driver's own refusal appears at all.

    Ordered by parsed instant, not by the recorded text: `_record` guarantees every
    start parses, and two captures written under different offsets order the opposite
    way as strings — which would answer with a refusal that is not the newest.
    """
    recorded = [
        (parsed, capture.history_failure)
        for capture in captures
        if capture.history_failure and (parsed := _parse(capture.started_at)) is not None
    ]
    return max(recorded, key=lambda item: item[0])[1] if recorded else None


# llmlint: ignore[changed_behavior_has_e2e] The phases a live journey can hold still
# for are covered end to end (executing-run-plan, surfacing, finished). These three
# cannot be: `starting` is the sub-second window before `run-plan` writes round-01, and
# reaching `reviewing-results` or a non-live `driving-round` means catching a real
# orchestrator between writing a result and opening the next round. Asserting them
# through a journey would mean racing the driver and reporting the loser as a defect,
# so they are proven against recorded round state in `tests/test_supervisory.py`.
def _round_phase(run_dir: Path) -> tuple[SupervisoryPhase, int | None]:
    """The phase the latest recorded round places the driver in, and that round."""
    latest = latest_round(run_dir)
    if latest is None:
        return "starting", None
    number, round_dir = latest
    if (round_dir / "result.json").is_file():
        return "reviewing-results", number
    try:
        state = load_mapping(round_dir / "status.json")
    except (ConfigError, OSError):
        return "driving-round", number
    owner = state.get("pid")
    if state.get("status") == "running" and process_may_be_live(
        owner if isinstance(owner, int) and not isinstance(owner, bool) else -1, state.get("host")
    ):
        return "executing-run-plan", number
    return "driving-round", number


def _last_activity_at(run_dir: Path, captures: Sequence[SupervisoryCapture]) -> float | None:
    """When this host last observed the driver doing something, newest evidence wins.

    Deliberately *activity* rather than "model request", because only one of the four
    inputs proves a turn: a captured turn is written by `channel.relay_supervisor` as
    an orchestrator turn ends. The other three are files the driver's own processes
    write — its harness stderr, appended while a turn runs and the only in-flight
    signal a session with no completed turn produces at all; the supervisor verdict the
    relay persists per turn; and the launch stamp, which is the floor so a driver that
    has done nothing yet is still placed in time. Reporting the newest of them answers
    "how long has this been silent", which is the question a planner is asking.
    """
    stamps = [
        stamp
        for stamp in (
            _mtime(run_dir / _ORCHESTRATOR_DIR / "stderr.log"),
            _mtime(run_dir / "channel" / "planner-verdict.json"),
            _mtime(run_dir / _ORCHESTRATOR_DIR / "status.json"),
        )
        if stamp is not None
    ]
    stamps.extend(
        parsed.timestamp()
        for capture in captures
        if capture.agent_role == "orchestrator"
        for turn in capture.turns
        if (parsed := _parse(turn["at"])) is not None
    )
    return max(stamps, default=None)


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def driver_state(
    run_dir: Path, captures: Sequence[SupervisoryCapture] | None = None
) -> DriverState | None:
    """Observe the launched orchestrator driving ``run_dir``, or ``None`` for no launch.

    ``None`` means this run was never launched through `just orchestrate` — a hand-run
    `just run-plan` has no driver to report, and inventing one would be a claim about
    a process that does not exist.
    """
    status = _launch_status(run_dir)
    if status is None:
        return None
    found = list(captures) if captures is not None else load_captures(run_dir)
    raw_pid = status.get("pid")
    pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None
    reported = _reported(run_dir)
    alive = not reported and process_may_be_live(pid if pid is not None else -1, status.get("host"))
    if reported:
        phase: SupervisoryPhase = "finished"
        number: int | None = None
    elif (run_dir / "channel" / "planner-pending.json").is_file():
        phase, number = "surfacing", _round_phase(run_dir)[1]
    else:
        phase, number = _round_phase(run_dir)
    return DriverState(
        session=f"orchestrator-{run_dir.name}",
        pid=pid,
        alive=alive,
        phase=phase,
        round=number,
        last_activity_at=_last_activity_at(run_dir, found),
        history_failure=(history_write_failure(_stderr_tail(run_dir)) or _captured_failure(found)),
    )


def driver_indicator(run_dir: Path, *, now: float | None = None) -> str | None:
    """One line for the planner views, or ``None`` when there is nothing to report.

    A finished driver is silent: the run's own row already says how it ended, and a
    line per settled run would bury the one run whose driver is dying.
    """
    state = driver_state(run_dir)
    if state is None or state.phase == "finished":
        return None
    line = state.describe(now=now)
    if state.history_failure is not None:
        line += (
            f"; harness history write failed ({state.history_failure}) — the supervisory "
            f"transcripts it lost are the bounded local captures under "
            f"{run_dir.name}/{CAPTURE_DIR}/"
        )
    return line


__all__ = [
    "CAPTURE_DIR",
    "CAPTURE_SCHEMA_VERSION",
    "HISTORY_WRITE_FAILURE_PATTERNS",
    "MAX_CAPTURED_TURNS",
    "MAX_CAPTURED_TURN_CHARS",
    "CaptureError",
    "CaptureStatus",
    "CapturedTurn",
    "DriverState",
    "SupervisoryCapture",
    "SupervisoryPhase",
    "bounded_note",
    "capture_path",
    "close_capture",
    "driver_indicator",
    "driver_state",
    "history_write_failure",
    "load_captures",
    "open_capture",
    "record_turn",
    "validate_session",
]
