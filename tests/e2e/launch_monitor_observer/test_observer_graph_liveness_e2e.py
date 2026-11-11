"""What holds `graphs/dag-scope.yaml`'s monitor to being a paced, foreground conversation.

The observer graph every `just orchestrate` attaches used to stay alive only because its
monitor was a conversation that took its next turn the moment its judge answered: the
pinned reader refused a document whose members were all scheduled, and the remedy it
named settled the whole graph after one turn each. The `oneagentgraph` the adopted
engine links lifted both halves — a member declares whether it is `background`, and a
`schedule` on a two-party member paces one conversation rather than starting a second —
so the monitor is now held five minutes between turns and the graph stays open because
the document *says* the monitor is foreground. The write-up for a manager is in
`docs/orchestration.md`; this module is what keeps it honest, by driving the reader and
the launch rather than describing them:

* the shipped document validates under the pinned reader, declares the monitor paced
  and foreground — `every: 300`, `start_after: 0`, `background: false` — and declares it
  the graph's one member, carrying the `progress-report` note that times the run's
  progress report;
* a real launch naming no override reads back the document the observer graph was
  actually given, so the shipped cadence of that note is what a run gets;
* a real launch under the shipped document, with the hold overridden small the published
  way, shows two consecutive monitor turns no closer together than the hold, the run
  watched between them, a surface read restarting the progress-report clock and no
  other, and the observer ending at settlement without waiting the hold out;
* real launches with the note's cadence compressed the published way: one whose manager
  reads nothing gets the note once per span and one report raised for each, and one whose
  manager reads a surface gets the clock restarted by that read and no note before the
  clock next fires;
* the refusal that remains: a document nothing holds open — every member scheduled and
  background, and a first turn deferred — is refused by the reader, in words the write-up
  quotes.

Only the paid provider is substituted, at both seams a member can reach one through.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml
from fake_backend import (
    OBSERVER_ANSWER_ENV,
    OBSERVER_MEMBER_ENV,
    OBSERVER_REPORTS_ON_ENV,
    PROGRESS_REPORT,
    PROGRESS_REPORT_KIND,
    PROMPT_LOG_ENV,
    TURN_GATE_CEILING_SECONDS,
    TURN_GATE_ENV,
    TURN_GATE_RELEASED,
)
from project_fixtures import project_from_plan
from test_orchestrate_launch_e2e import _environment as _launched_environment
from test_orchestrate_launch_e2e import _node
from unpublished_registry import process_started
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

# What the recipes these journeys run leave in the temporary directory, by design or
# from a published tool they run, is this module's own and goes with it
# (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: The observer graph every `just orchestrate` attaches, and the one member it names.
DAG_SCOPE_GRAPH = "graphs/dag-scope.yaml"
MONITOR_MEMBER = "monitor"

#: Where the finding is written up for a manager, and what the last journey reads back.
FINDING_DOCUMENT = "docs/orchestration.md"

MONITOR_HOLD_SECONDS = 300
MONITOR_FIRST_TURN_SECONDS = 0

#: The scheduled note that times the run's progress report, as the shipped document
#: declares it: thirty minutes in which the planner read no update, offered to the
#: monitor's agent side, its clock restarted by every surface the planner reads.
PROGRESS_REPORT_NOTE = "progress-report"
PROGRESS_REPORT_EVERY_SECONDS = 1800
PROGRESS_REPORT_START_AFTER_SECONDS = 1800
PROGRESS_REPORT_ADDRESSEE = "worker"
PROGRESS_REPORT_RESET_ON = {"source": "pipeline", "kind": "planner-surfaced"}

#: The schema the shipped document has to declare: `background` requires 8, a `schedule`
#: on a two-party member 9 and `scheduled_notes` 11, the first that admits every field.
SCHEMA_VERSION = 11

#: The lines a member's fields are written on, read with a reader written for this one
#: document rather than with a YAML library — the workspace installs none, and
#: `oneagentgraph validate` on the real document is what holds it well-formed.
SCHEDULE_LINE = re.compile(r"^\s*schedule:\s*\{(?P<fields>[^}]*)\}\s*$")
SCHEDULE_FIELD = re.compile(r"(?P<name>[a-z_]+):\s*(?P<value>[a-z0-9]+)")
BACKGROUND_LINE = re.compile(r"^\s*background:\s*(?P<value>true|false)\s*$")
VERSION_LINE = re.compile(r"^version:\s*(?P<version>\d+)\s*$", re.MULTILINE)

#: What the pinned reader says when nothing holds a run open. Split into the claims it
#: makes, so a reword that keeps the refusal fails on the wording rather than on the
#: behaviour: that nothing holds the run open, that a deferred first turn never comes
#: due, and the declaration this document uses as its answer.
REFUSAL_NAMES_THE_CAUSE = "nothing holds this run open"
REFUSAL_NAMES_THE_DEFERRED = "never comes due"
REFUSAL_NAMES_THE_ANSWER = "`background: false`"

LAUNCHED_RUN = "observer-liveness-paced"
HELD_NODE = "held"

#: The hold the launch overrides in place of the shipped five minutes.
PACED_HOLD_SECONDS = 12

#: How many of the monitor's holds must have opened before the worker is let go: two
#: completed holds, the second turn being the pacing and the third the evidence a hold is
#: survived. The rest of what releases it is `_evidenced`'s.
RELEASE_AT_HOLD = 3

#: When the gate is released whether or not the holds came, so a monitor that is not
#: being paced fails on the assertions below rather than on the gate's own ceiling.
RELEASE_CEILING_SECONDS = 180

#: The heartbeat bound this launch hands `oneagentgraph` through its documented
#: `ONEAGENTGRAPH_HEARTBEAT_TIMEOUT` (its `docs/contract.md`, "Liveness"), which beats
#: every quarter of it. The default 60 would beat every 15 s, which a 12-second hold
#: contains or misses by phase alone; at 20 no two beats are further apart than
#: `HEARTBEAT_GAP_SECONDS`, a quarter plus a second of scheduling slack, so every hold
#: longer than that contains one.
HEARTBEAT_BOUND_ENV = "ONEAGENTGRAPH_HEARTBEAT_TIMEOUT"
HEARTBEAT_BOUND_SECONDS = 20
HEARTBEAT_GAP_SECONDS = HEARTBEAT_BOUND_SECONDS / 4 + 1.0

#: How far short of the hold two consecutive turns may open and still be the hold's
#: doing. Measured on the linked oneagentgraph 0.3.19: a conversation held 12 seconds
#: opened its turns 11.93 to 12.11 seconds apart, and one held 2 seconds 1.90 to 2.11 —
#: the scheduler's clock is coarse by about a tenth of a second in either direction, and
#: an unpaced conversation opens its next turn within a tenth of a second of the judge
#: answering, so half a second tells the two apart with room to spare.
CLOCK_GRANULARITY_SECONDS = 0.5

#: What the stand-in monitor says on every turn: an ordinary quiet turn, so what is
#: measured is the graph's pacing and not anything the model's words caused.
SAID_ON_A_QUIET_TURN = "read the detailed stream since the cursor; nothing needed raising"

#: How often the run's `just status` is read while the launch is live, and the two
#: verdicts that view prints for an observer that has stopped watching. A reading taken
#: inside a hold is what says the hold is not read as a death.
STATUS_POLL_SECONDS = 2.0
OBSERVER_DEAD = "OBSERVER DEAD"
OBSERVER_NOT_RESTARTED = "OBSERVER NOT RESTARTED"

#: Where `oneagentgraph` writes its own event log, named by this journey so it reads
#: this run's graph and never a concurrent dispatch's.
GRAPH_STATE_ENV = "ONEAGENTGRAPH_STATE_DIR"


class Answered(NamedTuple):
    """What the pinned reader answered about one document, accepting it or refusing it."""

    status: int
    #: Both streams, because a refusal is written to stderr and an acceptance to stdout
    #: and these journeys assert on each.
    said: str


def _validated(graph: Path) -> Answered:
    """Ask the pinned reader about one document, the way the launch path asks."""
    ran = subprocess.run(
        ["oneagentgraph", "validate", str(graph)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    return Answered(status=ran.returncode, said=f"{ran.stdout}\n{ran.stderr}")


class Schedule(NamedTuple):
    """A member's `schedule`, in the reader's own fields; `None` where the document omits one."""

    every: int | None
    start_after: int | None
    #: Whether the schedule names any field beyond those two — a reset of its own, say.
    others: tuple[str, ...]


class Declared(NamedTuple):
    """One member's liveness fields, as the shipped document writes them."""

    schedule: Schedule
    #: `None` where the member says nothing, which for a scheduled member means
    #: background.
    background: bool | None


def _member_block(member: str) -> list[str]:
    """The lines of one member of the shipped document, comments included."""
    lines = (REPO_ROOT / DAG_SCOPE_GRAPH).read_text(encoding="utf-8").splitlines()
    opened = next(
        (index for index, line in enumerate(lines) if line.strip() == f"{member}:"),
        None,
    )
    assert opened is not None, (
        f"{DAG_SCOPE_GRAPH} declares no `{member}` member, so this journey is reading a "
        "document that has moved on without it"
    )
    indent = len(lines[opened]) - len(lines[opened].lstrip())
    block = [lines[opened]]
    for line in lines[opened + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    return block


def _declared(member: str) -> Declared:
    """What the shipped document declares about one member's schedule and liveness."""
    schedule = Schedule(every=None, start_after=None, others=())
    background: bool | None = None
    for line in _member_block(member):
        if line.strip().startswith("#"):
            continue
        if scheduled := SCHEDULE_LINE.match(line):
            fields = {
                field.group("name"): field.group("value")
                for field in SCHEDULE_FIELD.finditer(scheduled.group("fields"))
            }
            schedule = Schedule(
                every=int(fields["every"]) if "every" in fields else None,
                start_after=int(fields["start_after"]) if "start_after" in fields else None,
                others=tuple(sorted(set(fields) - {"every", "start_after"})),
            )
        if backgrounded := BACKGROUND_LINE.match(line):
            background = backgrounded.group("value") == "true"
    return Declared(schedule=schedule, background=background)


def _shipped_document_nothing_holds_open(written_to: Path) -> Path:
    """The shipped document with its monitor's `background: false` removed and deferred.

    Taken from the shipped file rather than retyped, so what is refused below is this
    repository's own observer graph with the one declaration that holds it open removed
    — the scheduled note, the schema version and every comment travel verbatim. The monitor's
    first turn is deferred to its period, which is what an omitted `start_after` means,
    because a scheduled member that took an immediate turn would still fire once. Its
    relative refs are rewritten to absolute, which is what they already resolve to: a
    ref is resolved against the directory the document was read from, and this copy is
    read from a temporary one.
    """
    lines = (REPO_ROOT / DAG_SCOPE_GRAPH).read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed_background = False
    deferred = False
    for line in lines:
        backgrounded = BACKGROUND_LINE.match(line)
        if backgrounded is not None and backgrounded.group("value") == "false":
            removed_background = True
            continue
        if (scheduled := SCHEDULE_LINE.match(line)) and "start_after" in scheduled.group("fields"):
            line = re.sub(r"start_after:\s*\d+", f"start_after: {MONITOR_HOLD_SECONDS}", line)
            deferred = True
        kept.append(line)
    assert removed_background, (
        f"{DAG_SCOPE_GRAPH} declares no `background: false`, so there is nothing for this "
        "journey to remove"
    )
    assert deferred, (
        f"{DAG_SCOPE_GRAPH} declares no `start_after`, so this journey cannot defer the "
        "monitor's first turn"
    )
    written_to.write_text("\n".join(kept).replace("../", f"{REPO_ROOT}/") + "\n", encoding="utf-8")
    return written_to


def test_the_shipped_observer_graph_declares_a_paced_foreground_monitor() -> None:
    """`graphs/dag-scope.yaml` loads, and says the monitor is paced and holds the run open.

    A document the reader refuses attaches no observer at all: the run is driven, reports
    plain `ACTIVE`, and nothing watches it. And a document the reader accepts with the
    monitor's `background: false` missing is one the refusal journey below shows cannot
    exist — so what is read here, field by field, is the contract every other file in
    this change restates: the hold, the immediate first turn, the foreground
    declaration, and the monitor as the graph's one member.
    """
    validated = _validated(REPO_ROOT / DAG_SCOPE_GRAPH)
    assert validated.status == 0, (
        f"{DAG_SCOPE_GRAPH} is not a document the pinned reader will run, so every "
        f"launch from this checkout attaches no observer:\n{validated.said}"
    )

    declared = VERSION_LINE.search((REPO_ROOT / DAG_SCOPE_GRAPH).read_text("utf-8"))
    version = int(declared.group("version")) if declared is not None else None
    assert version == SCHEMA_VERSION, (
        f"{DAG_SCOPE_GRAPH} declares schema {version}, and `background` needs 8, a "
        f"two-party `schedule` 9 and `scheduled_notes` 11: {SCHEMA_VERSION} is the first "
        "that admits all three"
    )

    monitor = _declared(MONITOR_MEMBER)
    assert monitor.schedule.every == MONITOR_HOLD_SECONDS, (
        f"the `{MONITOR_MEMBER}` member is no longer paced one turn per "
        f"{MONITOR_HOLD_SECONDS} seconds: {monitor.schedule}"
    )
    assert monitor.schedule.start_after == MONITOR_FIRST_TURN_SECONDS, (
        f"the `{MONITOR_MEMBER}` member no longer opens with the wave: {monitor.schedule}. "
        "An omitted `start_after` defaults to `every`, which leaves the first five minutes "
        "of every run unwatched"
    )
    assert monitor.schedule.others == (), (
        f"the `{MONITOR_MEMBER}` member's schedule states {monitor.schedule.others}, so "
        f"something other than its hold paces its turns — a reset of its own would restart "
        f"the hold on every planner read rather than the progress-report note's clock: "
        f"{monitor.schedule}"
    )
    assert monitor.background is False, (
        f"the `{MONITOR_MEMBER}` member no longer declares `background: false`, which is "
        "the one declaration that keeps the observer graph alive between its turns"
    )

    members = yaml.safe_load((REPO_ROOT / DAG_SCOPE_GRAPH).read_text("utf-8"))["members"]
    assert sorted(members) == [MONITOR_MEMBER], (
        f"{DAG_SCOPE_GRAPH} declares the members {sorted(members)}; the monitor is the one "
        "observer member, and it writes the progress report its scheduled note times"
    )


class Envelope(NamedTuple):
    """One event the observer graph recorded, narrowed to what is read below."""

    kind: str
    at: datetime
    #: Who produced it — `member` is the one read here.
    member: str | None
    #: Its kind's own body: a turn's `role`, a settlement's `members`.
    payload: dict[str, object]


def _instant(stamped: str) -> datetime:
    """A stream timestamp as an instant, so two of them can be subtracted."""
    return datetime.strptime(stamped.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")


def _graph_events(scratch: Path, *, live: bool = False) -> list[Envelope]:
    """The dag-scope graph's own record of this run, which names the member per event.

    The graph writes these into the scratch this journey named, so this is that run's
    record and no other's. Read here rather than off the run's journal because the
    member and the turn's role are exactly what the pacing is measured on. Read `live`,
    while the graph is still writing, a line cut short at the end of the file is skipped
    rather than decoded; it is read whole on the next pass.
    """
    # llmlint: ignore-block[tests_mirror_real_usage] No operator view renders which member
    # a turn belongs to, and which member paused is the whole question: `just status`
    # reports the observer's verdicts and `just monitor` the run's stream, neither the
    # member behind a turn, so the graph's own record is the one place the pacing can be
    # measured until a view renders it.
    events: list[Envelope] = []
    for log in sorted(scratch.glob("dag-scope-*/events.jsonl")):
        for line in log.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            # `oneagentgraph` owns this schema; `kind`, `ts`, `labels.member` and the
            # payload's `role` are what is read, each narrowed here.
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                if live:
                    continue
                raise
            assert isinstance(decoded, dict), decoded
            labels = decoded.get("labels") or {}
            payload = decoded.get("payload") or {}
            member = labels.get("member") if isinstance(labels, dict) else None
            events.append(
                Envelope(
                    kind=str(decoded.get("kind")),
                    at=_instant(str(decoded.get("ts"))),
                    member=member if isinstance(member, str) else None,
                    payload=payload if isinstance(payload, dict) else {},
                )
            )
    return events
    # llmlint: ignore-end[tests_mirror_real_usage]


def _of(events: list[Envelope], kind: str, member: str | None = None) -> list[Envelope]:
    """Every event of one kind, optionally of one member, in the order recorded."""
    return [
        event
        for event in events
        if event.kind == kind and (member is None or event.member == member)
    ]


def _note_events(events: list[Envelope], kind: str) -> list[Envelope]:
    """Every event of one kind about the `progress-report` note, in the order recorded."""
    return [
        event
        for event in _of(events, kind)
        if event.payload.get("scheduled_note") == PROGRESS_REPORT_NOTE
    ]


def _note_resets(events: list[Envelope]) -> list[Envelope]:
    """Every restart of the `progress-report` clock that names a planner read as its cause."""
    return [
        event
        for event in _note_events(events, "cron-reset")
        if isinstance(cause := event.payload.get("cause"), dict)
        and {key: cause.get(key) for key in PROGRESS_REPORT_RESET_ON} == PROGRESS_REPORT_RESET_ON
    ]


def _agent_turns(events: list[Envelope], member: str) -> list[Envelope]:
    """Every turn one member's agent side opened, which is what a paced turn is."""
    return [
        event
        for event in _of(events, "turn-started", member)
        if event.payload.get("role") == "assistant"
    ]


def _judge_closes(events: list[Envelope], member: str) -> list[Envelope]:
    """Every turn one member's judge side finished, which is where a hold is counted from."""
    return [
        event
        for event in _of(events, "turn-completed", member)
        if event.payload.get("role") == "user"
    ]


#: The line `just monitor --filter detailed` renders the driver's hook firing on: the
#: event's own stamp first, then the graph column, then the kind.
LET_GO_LINE = re.compile(r"^(?P<at>\S+)\s+\S+\s+run-hook-fired\b", re.MULTILINE)

#: The line the same stream renders the held node's settlement on — the run's own, since
#: the run holds that one node.
SETTLED_LINE = re.compile(rf"^(?P<at>\S+)\s+graph:{HELD_NODE}\s+node-settled\b", re.MULTILINE)


class Ending(NamedTuple):
    """When the run settled and when the driver let go of it, off its detailed stream."""

    settled: datetime | None
    let_go: datetime | None


def _ending(environment: dict[str, str]) -> Ending:
    """When the run settled and the driver fired the run-end hook, off its detailed stream.

    The held node's `node-settled` is the run's settlement, the instant after which the
    driver's closeout may cancel whatever the observer is doing. `just orchestrate` names
    a hook for both endings, and the driver fires it last: after the engine loop has
    finished, the observer has been cancelled and the settlement announced, and before
    the hook's own command runs. So its `run-hook-fired` event is
    the engine's stamp for having let go of the run, on the clock the graph's events
    carry — which is what the closing assertion below needs, because the launch process
    returns only once that hook's `just` recipe has, and how long a recipe takes on a
    loaded host says nothing about whether a hold was cancelled.
    """
    rendered = subprocess.run(
        ["just", "monitor", LAUNCHED_RUN, "--filter", "detailed"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    said = rendered.stdout + rendered.stderr
    fired, settled = LET_GO_LINE.search(said), SETTLED_LINE.search(said)
    return Ending(
        settled=_instant(settled.group("at")) if settled else None,
        let_go=_instant(fired.group("at")) if fired else None,
    )


def _evidenced(events: list[Envelope], readings: list[StatusReading]) -> bool:
    """Whether the record already carries what the run is held open to show.

    `RELEASE_AT_HOLD` holds opened, the progress-report clock restarted by the surface
    read, and a `just status` read wholly inside one hold. Each lands wherever the host's
    load puts the turns around it, so the run is kept open until each has happened rather
    than for a time in which each usually does.
    """
    holds = _holds(events)
    return (
        len(_judge_closes(events, MONITOR_MEMBER)) >= RELEASE_AT_HOLD
        and bool(_note_resets(events))
        and any(
            hold.covers(one.began) and hold.covers(one.ended) for hold in holds for one in readings
        )
    )


def _release_when_evidenced(
    state: Path, gate: Path, readings: list[StatusReading], stop: threading.Event
) -> None:
    """Let the held worker go once the live record is `_evidenced`, or at the ceiling."""
    deadline = time.monotonic() + e2e_timeout(RELEASE_CEILING_SECONDS)
    while not stop.is_set() and time.monotonic() < deadline:
        if _evidenced(_graph_events(state, live=True), list(readings)):
            break
        time.sleep(0.2)
    (gate / TURN_GATE_RELEASED).touch()


class Hold(NamedTuple):
    """One hold of the monitor's conversation: from the judge closing a turn to the next."""

    opened: datetime
    closed: datetime

    def covers(self, at: datetime) -> bool:
        return self.opened <= at <= self.closed


def _holds(events: list[Envelope]) -> list[Hold]:
    """Every hold the monitor's conversation completed: between consecutive agent turns."""
    closes = _judge_closes(events, MONITOR_MEMBER)
    opens = _agent_turns(events, MONITOR_MEMBER)
    holds: list[Hold] = []
    for closed_turn in closes:
        following = [opened for opened in opens if opened.at > closed_turn.at]
        if following:
            holds.append(Hold(opened=closed_turn.at, closed=following[0].at))
    return holds


class StatusReading(NamedTuple):
    """One `just status` of the live run, and when the read began and ended."""

    began: datetime
    ended: datetime
    #: The recipe's exit status, because a view that failed prints neither verdict either.
    status: int
    said: str


def _status_readings(
    launch: subprocess.Popen[str], environment: dict[str, str], readings: list[StatusReading]
) -> None:
    """Read the run's status through the real recipe until the launch returns.

    Cut at the `providers:` boundary the way `AGENTS.md` tells a watch to cut it, because
    everything below it is `oneagentgraph health`'s report about the host. Each reading
    keeps both instants, so a reading is credited to a hold only when the whole of it
    fell inside one. Each is appended to `readings` as it is taken, which the thread
    holding the worker reads.
    """
    consumed = False
    while launch.poll() is None:
        consumed = consumed or _a_surface_raised_and_consumed(environment)
        began = datetime.now(UTC)
        status = subprocess.run(
            ["just", "status", LAUNCHED_RUN],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        said = (status.stdout + status.stderr).split("\n  providers:", 1)[0]
        readings.append(
            StatusReading(began=began, ended=datetime.now(UTC), status=status.returncode, said=said)
        )
        time.sleep(STATUS_POLL_SECONDS)


#: The text of the one surface a paced launch raises and reads, as a planner would.
CONSUMED_SURFACE = "a planner-visible update, raised so that reading it resets a clock"


def _a_surface_raised_and_consumed(environment: dict[str, str]) -> bool:
    """Raise one surface on the live run and read it through the planner's own recipe.

    Reading a surface journals `planner-surfaced`, which the observer graph follows on
    the run's journal, and the `progress-report` note's `reset_on` matches it — so this is
    what makes the graph record which of its clocks a read restarts. `False` until the run
    exists to raise on.
    """
    raised = subprocess.run(
        [str(REPO_ROOT / "node_modules" / ".bin" / "onepipeline"), "surface", "--kind", "check-in"]
        + [LAUNCHED_RUN],
        cwd=REPO_ROOT,
        env=environment,
        input=CONSUMED_SURFACE,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    if raised.returncode != 0:
        return False
    read = subprocess.run(
        ["just", "channel-next", LAUNCHED_RUN],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert read.returncode == 0, read.stderr
    return True


#: The file `oneagentgraph` takes as a request to stop the whole graph, under the graph's
#: own `signals/`: what its `cancel` verb writes, and what the driver's closeout asks the
#: observer through.
STOP_SIGNAL = "signals/stop"


class ObserverEnd(NamedTuple):
    """The observer graph's own record of how it ended, read once the launch returned."""

    #: Whether the graph's stop request is on its record.
    stop_requested: bool
    #: Whether the process `owner.lock` names — pid and creation identity — still runs.
    owner_alive: bool


def _observer_end(state: Path) -> ObserverEnd:
    """Read the dag-scope graph's stop request and its owner off the graph's own state.

    `owner.lock` holds the owning pid and the creation identity Linux gave it, so a pid
    reused since is not read as the graph still running.
    """
    graphs = sorted(state.glob("dag-scope-*"))
    assert len(graphs) == 1, f"expected one observer graph under {state}: {graphs}"
    pid, started = (graphs[0] / "owner.lock").read_text("utf-8").split()
    try:
        alive = process_started(int(pid)) == int(started)
    except FileNotFoundError:
        alive = False
    return ObserverEnd(stop_requested=(graphs[0] / STOP_SIGNAL).exists(), owner_alive=alive)


class Paced(NamedTuple):
    """One real launch under the shipped document, paced small, and what it recorded."""

    events: list[Envelope]
    readings: list[StatusReading]
    #: When the run settled, by the engine's own clock: the held node's `node-settled`.
    #: `None` when the stream renders no such settlement.
    settled: datetime | None
    #: When the driver let go of the run, by the engine's own clock: the instant it
    #: fired the run-end hook, which it does after cancelling the observer and announcing
    #: the settlement and before the hook's command runs. `None` when the stream renders
    #: no such firing.
    let_go: datetime | None
    #: How the observer graph stood once the launch had returned: whether the driver
    #: asked it to stop, and whether its owning process outlived the launch.
    observer: ObserverEnd
    #: How the launch process exited.
    status: int
    printed: str


def _paced_launch(tmp_path: Path, oneharness_bin: str) -> Paced:
    """Launch a one-node run under the shipped observer graph, with the hold small.

    Everything between `just orchestrate` and the model is real: the driver, the observer
    graph, `oneagentgraph`, the onejudge conversation and its channel-served judge side.
    The worker is held so the run outlasts several holds; the monitor's own words are
    scripted quiet, so the pacing measured is the graph's and not the model's.
    """
    environment = _launched_environment(tmp_path, oneharness_bin)
    # llmlint: ignore-block[live_tier_compiles_and_requires_credential] The boundary under
    # test is the graph's pacing of a conversation, and a credentialed turn would prove
    # nothing more about it; the paid provider is the one thing this suite doubles.
    # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
    environment[OBSERVER_ANSWER_ENV] = SAID_ON_A_QUIET_TURN
    # llmlint: ignore-end[live_tier_compiles_and_requires_credential]
    environment[OBSERVER_MEMBER_ENV] = MONITOR_MEMBER
    gate = tmp_path / "turn-gate"
    gate.mkdir()
    environment[TURN_GATE_ENV] = str(gate)
    environment[HEARTBEAT_BOUND_ENV] = str(HEARTBEAT_BOUND_SECONDS)
    state = tmp_path / "graph-state"
    environment[GRAPH_STATE_ENV] = str(state)
    environment[PROMPT_LOG_ENV] = str(tmp_path / "prompts.jsonl")
    plan = tmp_path / "paced.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": LAUNCHED_RUN,
                "goal": {"text": "prove a paced monitor keeps watching between its turns"},
                "tasks": [_node(id=HELD_NODE)],
            }
        ),
        encoding="utf-8",
    )
    # Streamed to a file rather than a pipe nobody drains: an attached launch prints the
    # whole merged event stream, and a full pipe buffer stops the driver mid-run.
    printed = tmp_path / "launch.log"
    with printed.open("w", encoding="utf-8") as streaming:
        launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
            [
                "just",
                "orchestrate",
                project_from_plan(plan),
                "--set",
                f"members.{MONITOR_MEMBER}.schedule.every={PACED_HOLD_SECONDS}",
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdout=streaming,
            stderr=subprocess.STDOUT,
        )
        stop = threading.Event()
        readings: list[StatusReading] = []
        releaser = threading.Thread(
            target=_release_when_evidenced, args=(state, gate, readings, stop)
        )
        releaser.start()
        try:
            _status_readings(launch, environment, readings)
            status = launch.wait(timeout=e2e_timeout(300))
            observer = _observer_end(state)
        finally:
            stop.set()
            releaser.join()
            launch.kill()
            launch.wait(timeout=e2e_timeout(60))
            subprocess.run(
                ["just", "stop", LAUNCHED_RUN],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=e2e_timeout(60),
                check=False,
            )
    print(f"observer stop returned at {datetime.now(UTC).isoformat()}")
    ending = _ending(environment)
    return Paced(
        events=_graph_events(state),
        readings=readings,
        settled=ending.settled,
        let_go=ending.let_go,
        observer=observer,
        status=status,
        printed=printed.read_text("utf-8"),
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `xdist_group` selects
# an xdist worker under this suite's `--dist loadgroup`, not a test tier; the tiers here
# split by what a test reads, which is what each one's Nx cache key has to cover.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This journey replaces
# the one-turn-each settlement journey this module used to spend a launch on, in the
# `tests/e2e` tree that pre-dates this change; which Nx project owns that tree is a
# property of the tree rather than of anything here — `AGENTS.md` records the tier split
# as a deliberate decision, and re-homing the launch journeys into a new project is
# enforcement configuration this change may not move in order to pass.
# llmlint: ignore-block[shell_test_tiers_stay_split] Same site, same reason; and this is
# a pytest journey over the real recipe, not a shell test suite.
@pytest.mark.xdist_group("observer-graph-liveness")
def test_a_paced_monitor_keeps_the_run_watched_between_its_turns(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The shipped document, launched for real, paces the monitor and stays alive for it.

    Six claims, each read off the graph's own record of one launch or off the run's
    status while that launch was live:

    * the monitor opens with the wave, and its next agent turn opens no sooner than the
      hold after its judge answered — two consecutive turns at least `every` apart;
    * the run is watched through the hold: the graph does not settle, the member's own
      heartbeat lands inside every hold long enough to contain one, and `just status`
      read inside one reports neither `OBSERVER DEAD` nor `OBSERVER NOT RESTARTED`;
    * reading a planner surface restarts the progress-report note's clock, the one clock
      the launched document gives a `reset_on`, and no other — the monitor's hold among
      them;
    * the observer ends when the run settles rather than when the hold would have: the
      driver asks the graph to stop and its process is gone by the time the launch
      returns, with the monitor's schedule never firing after a hold that opened once
      the run had settled; and a turn its own closeout cancelled after the settlement is
      the observer being ended, never a death.

    Reverting the document's `schedule` fails before any of it: `--set
    members.monitor.schedule.every` on a member with no schedule is refused by the reader
    (`this graph has no schedule`), the launch attaches no observer, and the graph records
    nothing for the run. Reverting `background: false` makes the monitor a background
    conversation that ends at quiescence after the wave's turn, so the turns counted
    below never come.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    paced = _paced_launch(tmp_path, oneharness_bin)
    assert paced.status == 0, (
        f"the attached launch exited {paced.status}, so what follows is read off a run "
        f"that did not settle cleanly:\n{paced.printed}"
    )
    events = paced.events
    assert events, f"the dag-scope graph recorded nothing for this run:\n{paced.printed}"

    started = _of(events, "graph-started")
    assert started, f"the observer graph never started:\n{paced.printed}"
    # A turn the driver's own closeout cancels once the run has settled is the observer
    # being ended, not dying: the linked release journals that cancel as a
    # `provider-failure` death with cause `cancelled`. Every other death counts, and so
    # does a cancel before the settlement, which nothing in this run asked for.
    assert paced.settled is not None, (
        f"the run's detailed stream renders no settlement of `{HELD_NODE}`, so a death "
        f"cannot be placed before or after it:\n{paced.printed}"
    )
    died = [
        death
        for death in _of(events, "member-died")
        if not (death.payload.get("cause") == "cancelled" and death.at >= paced.settled)
    ]
    assert not died, (
        f"a member of the observer graph died during a paced run: {died}\n{paced.printed}"
    )

    turns = _agent_turns(events, MONITOR_MEMBER)
    assert len(turns) >= 3, (
        f"the `{MONITOR_MEMBER}` member took {len(turns)} agent turn(s) in a run whose "
        f"worker waited for hold {RELEASE_AT_HOLD} at a {PACED_HOLD_SECONDS}s hold, so "
        f"it is not being paced through the run — or not held open between its turns at "
        f"all:\n{paced.printed}"
    )
    # A turn the schedule defers opens on the member's `cron-fired`, as every later turn
    # does; the first one opening on none is the conversation opening with the wave.
    monitor_fired = _of(events, "cron-fired", MONITOR_MEMBER)
    fired_first = [firing.at.isoformat() for firing in monitor_fired if firing.at <= turns[0].at]
    assert not fired_first, (
        f"the monitor's first turn opened on its schedule firing at {fired_first}, which is "
        "a deferred first turn: `start_after: 0` is what opens the conversation with the "
        f"wave, and the document no longer says it:\n{paced.printed}"
    )
    gaps = [
        (later.at - earlier.at).total_seconds()
        for earlier, later in zip(turns, turns[1:], strict=False)
    ]
    assert all(gap >= PACED_HOLD_SECONDS - CLOCK_GRANULARITY_SECONDS for gap in gaps), (
        f"consecutive `{MONITOR_MEMBER}` agent turns opened {gaps} seconds apart, and the "
        f"graph was told to hold {PACED_HOLD_SECONDS}s between them; a turn that opened "
        f"sooner is a conversation the graph is not pacing:\n{paced.printed}"
    )

    holds = _holds(events)
    assert len(holds) >= 2, f"fewer than two holds completed: {holds}\n{paced.printed}"
    settled = _of(events, "graph-settled")
    for hold in holds:
        assert not any(hold.covers(one.at) for one in settled), (
            f"the observer graph settled inside a hold {hold}, so the monitor's "
            f"`background: false` is not holding the run open:\n{paced.printed}"
        )
    # A held conversation is not silent: every hold long enough that a beat published at
    # the cadence `HEARTBEAT_BOUND_SECONDS` sets was due inside it contains one. Which
    # holds that is follows from the bound rather than from where the beats fell, so the
    # check holds at any phase between the cadence and the holds.
    heartbeats = _of(events, "member-heartbeat", MONITOR_MEMBER)
    long_enough = [
        hold
        for hold in holds
        if (hold.closed - hold.opened).total_seconds() > HEARTBEAT_GAP_SECONDS
    ]
    assert long_enough, (
        f"no hold outlasted the {HEARTBEAT_GAP_SECONDS}s between two heartbeats ({holds}), "
        f"so nothing here says whether a held conversation beats:\n{paced.printed}"
    )
    silent = [hold for hold in long_enough if not any(hold.covers(beat.at) for beat in heartbeats)]
    assert not silent, (
        f"no heartbeat of the `{MONITOR_MEMBER}` member landed inside the holds {silent} "
        f"(beats at {[beat.at.isoformat() for beat in heartbeats]}), each longer than the "
        f"{HEARTBEAT_GAP_SECONDS}s between beats, so a held conversation is silent to the "
        f"activity watchdog:\n{paced.printed}"
    )
    inside = [
        reading
        for reading in paced.readings
        if any(hold.covers(reading.began) and hold.covers(reading.ended) for hold in holds)
    ]
    assert inside, (
        f"no `just status` reading fell wholly inside a hold, so nothing here says what "
        f"the view reports while the monitor waits: {paced.readings}"
    )
    for reading in inside:
        assert reading.status == 0, (
            f"`just status` failed inside a hold, so its silence about the observer says "
            f"nothing:\n{reading.said}"
        )
        assert OBSERVER_DEAD not in reading.said and OBSERVER_NOT_RESTARTED not in reading.said, (
            "`just status` read the monitor's hold as the observer having died, which is "
            f"what a paced watch must never look like from outside:\n{reading.said}"
        )

    # The progress-report note's clock is the graph's one clock a read restarts, read back
    # off this launch: the surface read during it restarted that clock, naming the read as
    # its cause, and restarted no other — the monitor's hold included.
    resets = _note_resets(events)
    assert resets, (
        f"reading a surface restarted no `{PROGRESS_REPORT_NOTE}` clock, so the launched "
        f"document's `reset_on` does not follow the run's journal:\n{paced.printed}"
    )
    reset_others = [
        event
        for event in _of(events, "cron-reset")
        if event.payload.get("scheduled_note") != PROGRESS_REPORT_NOTE
    ]
    assert not reset_others, (
        f"reading a surface restarted {[e.payload for e in reset_others]}, a clock the "
        f"launched document gives no `reset_on` beside the {PROGRESS_REPORT_NOTE} note"
    )

    # The driver's closeout is on the run's record: it fired the run-end hook, which it
    # does only after cancelling the observer and announcing the settlement.
    assert paced.let_go is not None, (
        f"the run's detailed stream renders no `run-hook-fired`, so the driver's closeout "
        f"is not on the run's record:\n{paced.printed}"
    )
    # A turn in flight when the driver let go must be the schedule's: its hold opened
    # before the run settled, and the turn opened at that hold's own deadline, not later.
    # That turn may open after settlement, when the hold ran out during the closeout's
    # teardown before the cancel landed; the journal stamps no moment for the cancel
    # request, so that is permitted, and a driver that let the conversation go on is still
    # caught by the hold that would follow the turn.
    closes = _judge_closes(events, MONITOR_MEMBER)
    last_close = closes[-1].at
    assert paced.settled is not None
    # A hold the monitor opened once the run had settled is one the cancel had to end:
    # the schedule firing after such a hold opened is the conversation going on past the
    # settlement, a turn the cancel should have prevented. A hold opened before the
    # settlement may still run out in the closeout's teardown, which is the turn in flight
    # read below, and is not counted here.
    held_after = [close.at for close in closes if close.at > paced.settled]
    went_on = [
        firing.at.isoformat()
        for firing in monitor_fired
        if held_after and firing.at > held_after[0]
    ]
    assert not went_on, (
        f"the monitor's hold that opened at {held_after[0].isoformat() if held_after else ''}, "
        f"after the run settled at {paced.settled.isoformat()}, ran out and fired its "
        f"schedule at {went_on}: the driver's cancel at settlement is not ending the "
        f"monitor's hold, so the conversation goes on past a run that settled:\n{paced.printed}"
    )
    in_flight = [turn.at for turn in turns if turn.at > last_close]
    if in_flight:
        assert last_close < paced.settled, (
            f"the monitor's hold that opened at {last_close.isoformat()}, after the run "
            f"settled at {paced.settled.isoformat()}, ran out at {in_flight[0].isoformat()} "
            f"before the driver let go at {paced.let_go.isoformat()}: the driver's cancel at "
            f"settlement is not ending the monitor's hold:\n{paced.printed}"
        )
        opened_after = (in_flight[0] - last_close).total_seconds()
        assert opened_after <= PACED_HOLD_SECONDS + CLOCK_GRANULARITY_SECONDS, (
            f"the monitor's turn in flight when the driver let go opened {opened_after:.2f}s "
            f"after the hold before it began, later than the {PACED_HOLD_SECONDS}s hold "
            f"allows, so something other than the schedule held it back:\n{paced.printed}"
        )
        return
    # Otherwise the last hold was the one the cancel had to end, and the graph's own
    # record says the cancel ended it: a stop on the record, and the graph's process gone
    # once the launch returned.
    assert paced.observer.stop_requested, (
        f"the observer graph carries no `{STOP_SIGNAL}` once the launch returned, so the "
        f"driver never asked it to stop:\n{paced.printed}"
    )
    assert not paced.observer.owner_alive, (
        "the observer graph's process outlived the launch, so the driver let go of the "
        f"run with the monitor still holding:\n{paced.printed}"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[shell_test_tiers_stay_split]


def test_the_pinned_reader_refuses_an_observer_graph_nothing_holds_open(tmp_path: Path) -> None:
    """The shipped document with the monitor's foreground declaration removed does not load.

    Without `background: false` the monitor is a scheduled member and therefore
    background, so nothing holds the run open for a deferred first turn: the graph would
    take its initial waves and settle. The reader refuses that rather than running it,
    says why, and names the declaration this document uses as its answer — so removing
    the line is refused at the launch rather than discovered as a run nothing watched.
    """
    refused = _validated(_shipped_document_nothing_holds_open(tmp_path / "nothing-holds.yaml"))

    assert refused.status != 0, (
        "the pinned reader now accepts an observer graph whose members are all "
        "background with a deferred first turn, so the refusal this journey and "
        f"`{FINDING_DOCUMENT}` record has been lifted upstream and the write-up is due a "
        f"re-measurement:\n{refused.said}"
    )
    assert REFUSAL_NAMES_THE_CAUSE in refused.said, refused.said
    assert REFUSAL_NAMES_THE_DEFERRED in refused.said, refused.said
    assert REFUSAL_NAMES_THE_ANSWER in refused.said, (
        f"the refusal no longer names {REFUSAL_NAMES_THE_ANSWER} as a way out, so the "
        f"write-up's account of what it offers is out of date:\n{refused.said}"
    )
    assert MONITOR_MEMBER in refused.said, (
        "the refusal no longer names the member whose first turn never comes due, which "
        f"is what says the monitor is the one being refused:\n{refused.said}"
    )


def test_the_write_up_quotes_the_refusal_the_reader_actually_prints(tmp_path: Path) -> None:
    """The documented refusal is held to the tool, not to the memory of having run it.

    `docs/orchestration.md` quotes the reader's refusal as the evidence that liveness is a
    declaration and not a property of a member's kind. A quote is exactly the kind of
    claim that outlives the release it was taken from, so it is compared against what the
    reader says today — which makes a reword upstream a failing check rather than a
    paragraph quietly describing a message nothing produces.
    """
    refused = _validated(_shipped_document_nothing_holds_open(tmp_path / "nothing-holds.yaml"))
    written = (REPO_ROOT / FINDING_DOCUMENT).read_text(encoding="utf-8")

    quoted = REFUSAL_NAMES_THE_CAUSE
    assert quoted in " ".join(written.split()), (
        f"{FINDING_DOCUMENT} no longer quotes the refusal it records, so a reader has "
        "nothing to compare against the reader's own words"
    )
    assert quoted in refused.said, (
        f"{FINDING_DOCUMENT} quotes a refusal the pinned reader no longer prints:\n{refused.said}"
    )


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The task that moved the
# progress report onto the monitor's scheduled note requires these journeys to extend this
# module's launches rather than add a parallel launch suite, and they replace three launches
# the retired reporting member's journeys spent (its own prompt probe, its channel-access
# run and its placeholder probe). Which Nx project owns `tests/e2e` is a property of that
# tree, as the paced journey above records for the same rule, and re-homing the launch
# journeys into a project of their own is enforcement configuration this change may not
# move in order to pass.
#: The run a launch naming no override starts, and what it reads back.
UNPACED_RUN = "observer-shipped-cadence"


def _shipped_note(document: dict[str, object]) -> dict[str, object]:
    """The `progress-report` note of a graph document's monitor, as that document states it."""
    members = document.get("members")
    assert isinstance(members, dict), document
    monitor = members.get(MONITOR_MEMBER)
    assert isinstance(monitor, dict), members
    notes = monitor.get("scheduled_notes")
    assert isinstance(notes, list), (
        f"the `{MONITOR_MEMBER}` member declares no `scheduled_notes`, so nothing times the "
        f"run's progress report: {monitor}"
    )
    named = [
        note for note in notes if isinstance(note, dict) and note.get("id") == PROGRESS_REPORT_NOTE
    ]
    assert len(named) == 1, f"the monitor declares no one `{PROGRESS_REPORT_NOTE}` note: {notes}"
    return named[0]


#: A line of the shipped note's own text, which is how the stand-in monitor knows the turn
#: it is answering carries that note rather than an ordinary instruction.
NOTE_MARKER = str(
    _shipped_note(yaml.safe_load((REPO_ROOT / DAG_SCOPE_GRAPH).read_text("utf-8")))["text"]
).splitlines()[0]


@pytest.mark.xdist_group("observer-graph-liveness")
def test_a_launch_naming_no_override_gives_the_observer_the_shipped_note_cadence(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """What a real launch hands its observer graph is the monitor alone, on the shipped cadence.

    Read off what the run itself recorded rather than off the file in this tree: the
    engine's launch record names the graph document the observer was started on and the
    overrides it was given, and the observer's own record names the document it read and
    the members it declared. With no override recorded, that document's `progress-report`
    note is exactly the configuration the monitor's clock runs on — so a shipped cadence of
    anything but thirty minutes fails here even though every compressed journey passes.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _launched_environment(tmp_path, oneharness_bin)
    state = tmp_path / "graph-state"
    environment[GRAPH_STATE_ENV] = str(state)
    plan = tmp_path / "unpaced.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": UNPACED_RUN,
                "tasks": [_node(id="settles")],
            }
        ),
        encoding="utf-8",
    )
    try:
        launched = subprocess.run(
            ["just", "orchestrate", project_from_plan(plan)],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
            stdin=subprocess.DEVNULL,
        )
    finally:
        subprocess.run(
            ["just", "stop", UNPACED_RUN],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
    assert launched.returncode == 0, launched.stdout + launched.stderr

    # llmlint: ignore-block[tests_mirror_real_usage] No view renders the observer graph's
    # document or its overrides; the run's launch record and the graph's own run record
    # are the two places what the observer was given is kept.
    record = json.loads(
        (Path(environment["ONEPIPELINE_RUNS_DIR"]) / UNPACED_RUN / "launch.json").read_text("utf-8")
    )
    given = Path(str(record.get("graph")))
    assert given == REPO_ROOT / DAG_SCOPE_GRAPH, (
        f"the launch handed its observer {given}, not this checkout's {DAG_SCOPE_GRAPH}"
    )
    assert not record.get("dag_sets"), (
        f"a launch naming no override recorded observer overrides {record.get('dag_sets')}"
    )
    graphs = sorted(state.glob("dag-scope-*/record.json"))
    assert len(graphs) == 1, f"expected one observer graph under {state}: {graphs}"
    observed = json.loads(graphs[0].read_text("utf-8"))
    # llmlint: ignore-end[tests_mirror_real_usage]
    assert Path(str(observed.get("graph"))) == given, observed
    assert observed.get("declared_members") == [MONITOR_MEMBER], (
        f"the launched observer graph declared {observed.get('declared_members')}; the "
        f"`{MONITOR_MEMBER}` is its one member and writes the progress report itself"
    )
    started = _of(_graph_events(state), "graph-started")
    assert [Path(str(one.payload.get("graph"))) for one in started] == [given], started

    note = _shipped_note(yaml.safe_load(given.read_text("utf-8")))
    assert note.get("every") == PROGRESS_REPORT_EVERY_SECONDS, note
    assert note.get("start_after") == PROGRESS_REPORT_START_AFTER_SECONDS, note
    assert note.get("addressee") == PROGRESS_REPORT_ADDRESSEE, note
    reset_on = note.get("reset_on")
    assert isinstance(reset_on, dict) and PROGRESS_REPORT_RESET_ON in reset_on.get("include", []), (
        f"the note's clock is not restarted by a planner read: {reset_on}"
    )


#: The note's compressed cadence on the journeys below, overridden on the launch the
#: published way: short enough that it fires several times inside a minute's run, and
#: long enough that a stand-in turn answering it ends well inside one span.
NOTE_SPAN_SECONDS = 8

#: How far past its span a firing may land and still be the span's: the clock counts
#: whole seconds on a host also running dispatches, so a span is asserted near its value
#: rather than exactly at it.
NOTE_SPAN_SLACK_SECONDS = NOTE_SPAN_SECONDS / 2

#: How many notes a run nobody reads has to have been given before its worker is let go.
NOTES_BEFORE_RELEASE = 2


class Noted(NamedTuple):
    """One launch with the note's cadence compressed, and what it recorded."""

    events: list[Envelope]
    #: When each progress report reached the run's queue.
    reports: list[datetime]
    #: When the journey read a surface through the channel recipe, or `None` if it read none.
    read_at: datetime | None
    #: The monitor's agent turns, as the stand-in model was handed them.
    prompts: list[str]
    status: int
    printed: str


#: The line the planner's own view renders a queued progress report on: the event's stamp,
#: the run's column, then the surface's kind and the stand-in's text.
REPORT_QUEUED_LINE = re.compile(
    rf"^(?P<at>\S+)\s+graph:-\s+planner-surface-queued "
    rf'kind="{re.escape(PROGRESS_REPORT_KIND)}" message="{re.escape(PROGRESS_REPORT)}"',
    re.MULTILINE,
)


def _reports_raised(environment: dict[str, str], run: str) -> list[datetime]:
    """When each progress report reached the run's queue, off the planner's own view.

    Read through `just monitor`, which renders what was queued and claims nothing — the
    queue itself keeps only the latest waiting `check-in`, so it cannot count them — and
    which a manager reads to see the same thing.
    """
    rendered = subprocess.run(
        ["just", "monitor", run],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    if rendered.returncode != 0:
        return []
    return [_instant(line.group("at")) for line in REPORT_QUEUED_LINE.finditer(rendered.stdout)]


def _noted_evidence(events: list[Envelope], read_at: datetime | None, reports: int) -> bool:
    """Whether the live record already shows what the run is held open to show.

    Unread, that is the note given `NOTES_BEFORE_RELEASE` times and as many reports
    raised. Read, it is the clock restarted by the read, its next firing, and a note
    admitted after it — the whole window the journey asserts on, closed.
    """
    admitted = _note_events(events, "note-admitted")
    if read_at is None:
        return len(admitted) >= NOTES_BEFORE_RELEASE and reports >= NOTES_BEFORE_RELEASE
    resets = [reset for reset in _note_resets(events) if reset.at >= read_at]
    if not resets:
        return False
    fired = [one for one in _note_events(events, "cron-fired") if one.at > resets[0].at]
    return bool(fired) and any(note.at >= fired[0].at for note in admitted)


def _noted_launch(tmp_path: Path, oneharness_bin: str, run: str, *, reads: bool) -> Noted:
    """Launch a one-node run under the shipped observer graph, its note's cadence compressed.

    The worker is held so the run outlasts several spans; the monitor's own words are
    scripted quiet, and a turn carrying the note raises the progress report through the
    persona's own verb. `reads` has the journey read one surface through `just
    channel-next` as soon as the run can be read, as a manager would; otherwise nothing
    here reads the run's channel at all.
    """
    environment = _launched_environment(tmp_path, oneharness_bin)
    # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
    environment[OBSERVER_ANSWER_ENV] = SAID_ON_A_QUIET_TURN
    environment[OBSERVER_MEMBER_ENV] = MONITOR_MEMBER
    environment[OBSERVER_REPORTS_ON_ENV] = NOTE_MARKER
    gate = tmp_path / "turn-gate"
    gate.mkdir()
    environment[TURN_GATE_ENV] = str(gate)
    state = tmp_path / "graph-state"
    environment[GRAPH_STATE_ENV] = str(state)
    prompt_log = tmp_path / "prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)
    plan = tmp_path / "noted.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": run,
                "goal": {"text": "prove the progress report is timed by its note"},
                "tasks": [_node(id=HELD_NODE)],
            }
        ),
        encoding="utf-8",
    )
    note = f"members.{MONITOR_MEMBER}.scheduled_notes.{PROGRESS_REPORT_NOTE}"
    printed = tmp_path / "launch.log"
    read_at: datetime | None = None
    with printed.open("w", encoding="utf-8") as streaming:
        launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
            [
                "just",
                "orchestrate",
                project_from_plan(plan),
                "--set",
                f"{note}.every={NOTE_SPAN_SECONDS}",
                "--set",
                f"{note}.start_after={NOTE_SPAN_SECONDS}",
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdout=streaming,
            stderr=subprocess.STDOUT,
        )
        try:
            # Inside the stand-in's own gate ceiling, so a run that never shows what it is
            # held for is let go and settles, and fails on the assertion naming what is
            # missing rather than on a worker the gate gave up on.
            deadline = time.monotonic() + min(
                e2e_timeout(RELEASE_CEILING_SECONDS), TURN_GATE_CEILING_SECONDS / 2
            )
            while launch.poll() is None and time.monotonic() < deadline:
                if (
                    reads
                    and read_at is None
                    and _a_surface_raised_and_consumed_on(environment, run)
                ):
                    read_at = datetime.now(UTC)
                reports = len(_reports_raised(environment, run))
                if _noted_evidence(_graph_events(state, live=True), read_at, reports):
                    break
                time.sleep(0.5)
            (gate / TURN_GATE_RELEASED).touch()
            status = launch.wait(timeout=e2e_timeout(300))
            reports = _reports_raised(environment, run)
        finally:
            (gate / TURN_GATE_RELEASED).touch()
            launch.kill()
            launch.wait(timeout=e2e_timeout(60))
            subprocess.run(
                ["just", "stop", run],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=e2e_timeout(60),
                check=False,
            )
    # llmlint: ignore[tests_mirror_real_usage] No view carries the prompt a turn was handed.
    recorded = [json.loads(line) for line in prompt_log.read_text("utf-8").splitlines()]
    return Noted(
        events=_graph_events(state),
        reports=reports,
        read_at=read_at,
        prompts=[
            str(turn["prompt"])
            for turn in recorded
            if f"/members/{MONITOR_MEMBER}/" in str(turn.get("config") or "")
        ],
        status=status,
        printed=printed.read_text("utf-8"),
    )


def _a_surface_raised_and_consumed_on(environment: dict[str, str], run: str) -> bool:
    """Raise one surface on `run` and read it through `just channel-next`, as a manager does."""
    raised = subprocess.run(
        [str(REPO_ROOT / "node_modules" / ".bin" / "onepipeline"), "surface", "--kind", "finding"]
        + [run],
        cwd=REPO_ROOT,
        env=environment,
        input=CONSUMED_SURFACE,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    if raised.returncode != 0:
        return False
    read = subprocess.run(
        ["just", "channel-next", run],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert read.returncode == 0, read.stderr
    return True


@pytest.mark.xdist_group("observer-graph-liveness")
def test_a_run_nobody_reads_gets_one_progress_report_per_span(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The note reaches the monitor once per span, and each one raises exactly one report.

    Nothing here reads the run's channel, so nothing restarts the clock: the note is
    admitted into the monitor's conversation at its first span and once per span after,
    each copy naming the note, each taken by a turn, and each turn that carried it raising
    one `check-in` surface — the report a manager who had read nothing would be handed.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    noted = _noted_launch(tmp_path, oneharness_bin, "observer-progress-unread", reads=False)
    events = noted.events

    assert not _note_events(events, "cron-reset"), (
        f"the {PROGRESS_REPORT_NOTE} clock restarted in a run nobody read:\n{noted.printed}"
    )
    admitted = _note_events(events, "note-admitted")
    assert len(admitted) >= NOTES_BEFORE_RELEASE, (
        f"the {PROGRESS_REPORT_NOTE} note was admitted {len(admitted)} time(s) in a run held "
        f"for {NOTES_BEFORE_RELEASE} spans of {NOTE_SPAN_SECONDS}s:\n{noted.printed}"
    )
    assert all(one.member == MONITOR_MEMBER for one in admitted), admitted
    assert all(one.payload.get("addressee") == PROGRESS_REPORT_ADDRESSEE for one in admitted), (
        admitted
    )
    started = _of(events, "graph-started")[0].at
    first = (admitted[0].at - started).total_seconds()
    assert first >= NOTE_SPAN_SECONDS - CLOCK_GRANULARITY_SECONDS, (
        f"the first note was admitted {first:.2f}s after the graph started, inside its "
        f"{NOTE_SPAN_SECONDS}s `start_after`"
    )
    spans = [
        (later.at - earlier.at).total_seconds()
        for earlier, later in zip(admitted, admitted[1:], strict=False)
    ]
    assert all(
        NOTE_SPAN_SECONDS - CLOCK_GRANULARITY_SECONDS
        <= span
        <= NOTE_SPAN_SECONDS + NOTE_SPAN_SLACK_SECONDS
        for span in spans
    ), f"consecutive notes were admitted {spans}s apart against a {NOTE_SPAN_SECONDS}s span"

    # Every admitted copy reached a turn. The delivery is the `turn-interrupted` naming the
    # offer its `note-admitted` named — the offer is what ties a delivery to the note,
    # because the delivery itself names no note on the adopted release — taken `queued`
    # into the monitor's next turn. Only the last copy may still be untaken, admitted as
    # the run was ending.
    deliveries = {
        str(event.payload.get("offer")): event
        for event in _of(events, "turn-interrupted", MONITOR_MEMBER)
    }
    taken = [one for one in admitted if str(one.payload.get("offer")) in deliveries]
    assert [one.payload.get("offer") for one in taken] == [
        one.payload.get("offer") for one in admitted[: len(taken)]
    ] and len(taken) >= len(admitted) - 1, (
        f"of the copies {[one.payload.get('offer') for one in admitted]} only "
        f"{[one.payload.get('offer') for one in taken]} reached a turn"
    )
    for one in taken:
        delivery = deliveries[str(one.payload.get("offer"))]
        assert delivery.at >= one.at, (one, delivery)
        assert delivery.payload.get("delivered") is True, delivery
        assert delivery.payload.get("accepted") == "queued", delivery
        assert (
            delivery.payload.get("scheduled_note", PROGRESS_REPORT_NOTE) == PROGRESS_REPORT_NOTE
        ), delivery
    carried = [prompt for prompt in noted.prompts if NOTE_MARKER in prompt]
    assert len(carried) == len(taken), (
        f"{len(taken)} note(s) reached the monitor and {len(carried)} of its turns carried "
        "the note's text"
    )
    assert len(noted.reports) == len(carried), (
        f"{len(carried)} turn(s) carried the note and {len(noted.reports)} progress "
        f"report(s) reached the run's queue; each note is one report:\n{noted.printed}"
    )
    assert noted.status == 0, noted.printed


@pytest.mark.xdist_group("observer-graph-liveness")
def test_reading_a_surface_restarts_the_progress_report_clock(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A surface the manager reads restarts the note's clock, and no note comes before it fires.

    The read goes through `just channel-next`, which journals `planner-surfaced`; the
    observer graph follows the run's journal, and the note's `reset_on` names that kind.
    So the graph's stream carries the restart with the read as its cause, and from the
    read to the clock's next firing — a whole span later — the monitor is offered no
    note, which is a manager who has just read an update hearing nothing more until a
    span has passed with nothing read.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    noted = _noted_launch(tmp_path, oneharness_bin, "observer-progress-read", reads=True)
    assert noted.read_at is not None, f"the journey never read a surface:\n{noted.printed}"
    events = noted.events

    resets = _note_resets(events)
    after = [reset for reset in resets if reset.at >= noted.read_at - timedelta(seconds=1)]
    assert after, (
        f"no `cron-reset` of the {PROGRESS_REPORT_NOTE} clock names `planner-surfaced` as its "
        f"cause after the read at {noted.read_at.isoformat()}: "
        f"{[(one.at.isoformat(), one.payload) for one in _note_events(events, 'cron-reset')]}"
    )
    reset = after[0]
    fired = [one for one in _note_events(events, "cron-fired") if one.at > reset.at]
    assert fired, f"the {PROGRESS_REPORT_NOTE} clock never fired after the read:\n{noted.printed}"
    span = (fired[0].at - reset.at).total_seconds()
    assert span >= NOTE_SPAN_SECONDS - CLOCK_GRANULARITY_SECONDS, (
        f"the clock fired {span:.2f}s after the read restarted it, inside its "
        f"{NOTE_SPAN_SECONDS}s span"
    )
    admitted = _note_events(events, "note-admitted")
    early = [one.at.isoformat() for one in admitted if reset.at < one.at < fired[0].at]
    assert not early, (
        f"the {PROGRESS_REPORT_NOTE} note was admitted at {early}, between the read's restart "
        f"at {reset.at.isoformat()} and the clock's next firing at {fired[0].at.isoformat()}"
    )
    assert any(one.at >= fired[0].at for one in admitted), (
        f"no note was admitted once the clock fired again, so nothing here shows the clock "
        f"still runs after a read:\n{noted.printed}"
    )
    assert noted.status == 0, noted.printed


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
