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

* the shipped document validates under the pinned reader and declares the monitor
  paced and foreground — `every: 300`, `start_after: 0`, `background: false` — and the
  pacemaker a scheduled member that says nothing about liveness;
* a real launch under the shipped document, with both periods overridden small the
  published way, shows two consecutive monitor turns no closer together than the hold,
  the run watched between them, the pacemaker firing inside a hold, and the observer
  ending at settlement without waiting the hold out;
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
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_backend import (
    AGENT_DELAY_ENV,
    OBSERVER_ANSWER_ENV,
    OBSERVER_MEMBER_ENV,
    PROMPT_LOG_ENV,
)
from project_fixtures import project_from_plan
from test_orchestrate_launch_e2e import _environment as _launched_environment
from test_orchestrate_launch_e2e import _node
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The observer graph every `just orchestrate` attaches, and the two members it names.
DAG_SCOPE_GRAPH = "graphs/dag-scope.yaml"
MONITOR_MEMBER = "monitor"
PACEMAKER_MEMBER = "check-in"

#: Where the finding is written up for a manager, and what the last journey reads back.
FINDING_DOCUMENT = "docs/orchestration.md"

MONITOR_HOLD_SECONDS = 300
MONITOR_FIRST_TURN_SECONDS = 0
PACEMAKER_PERIOD_SECONDS = 1800

#: The schema the shipped document has to declare: `background` requires 8 and a
#: `schedule` on a two-party member 9, so 9 is the first that admits every field above.
SCHEMA_VERSION = 9

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

#: The hold the launch overrides in place of the shipped five minutes, and how long the
#: worker is held. Thirty-eight seconds outlasts three twelve-second holds: the second
#: turn is the pacing, the third puts the member's ~15-second heartbeat inside a hold,
#: and the last hold ends within a few seconds of the settlement it has to be cancelled
#: at — later, and a hold expiring during the driver's own closeout would read as one
#: waited out. That arithmetic holds only while the worker is held **once**: the
#: launched environment's session store sits under `tmp_path`, whose control-socket
#: address is past the 108 bytes Linux allows, so every controlled turn there is refused
#: and re-taken without control — a worker held twice, and a settlement landing wherever
#: in a hold the second delay puts it. `_paced_launch` gives the run a short store.
PACED_HOLD_SECONDS = 12
HELD_SECONDS = 38

#: How far short of the hold two consecutive turns may open and still be the hold's
#: doing. Measured on the linked oneagentgraph 0.3.19: a conversation held 12 seconds
#: opened its turns 11.93 to 12.11 seconds apart, and one held 2 seconds 1.90 to 2.11 —
#: the scheduler's clock is coarse by about a tenth of a second in either direction, and
#: an unpaced conversation opens its next turn within a tenth of a second of the judge
#: answering, so half a second tells the two apart with room to spare.
CLOCK_GRANULARITY_SECONDS = 0.5

#: The pacemaker's period for the same launch, overridden the same way. Short, so it
#: fires several times inside the monitor's holds — which is what shows a background
#: scheduled member firing while a foreground one holds the run open.
PACED_PACEMAKER_SECONDS = 5

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
    resettable: bool | None


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
    schedule = Schedule(every=None, start_after=None, resettable=None)
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
                resettable=fields["resettable"] == "true" if "resettable" in fields else None,
            )
        if backgrounded := BACKGROUND_LINE.match(line):
            background = backgrounded.group("value") == "true"
    return Declared(schedule=schedule, background=background)


def _shipped_document_nothing_holds_open(written_to: Path) -> Path:
    """The shipped document with its monitor's `background: false` removed and deferred.

    Taken from the shipped file rather than retyped, so what is refused below is this
    repository's own observer graph with the one declaration that holds it open removed
    — the pacemaker, the schema version and every comment travel verbatim. The monitor's
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
    declaration, and a pacemaker that declares nothing and is therefore background.
    """
    validated = _validated(REPO_ROOT / DAG_SCOPE_GRAPH)
    assert validated.status == 0, (
        f"{DAG_SCOPE_GRAPH} is not a document the pinned reader will run, so every "
        f"launch from this checkout attaches no observer:\n{validated.said}"
    )

    declared = VERSION_LINE.search((REPO_ROOT / DAG_SCOPE_GRAPH).read_text("utf-8"))
    version = int(declared.group("version")) if declared is not None else None
    assert version == SCHEMA_VERSION, (
        f"{DAG_SCOPE_GRAPH} declares schema {version}, and `background` needs 8 and a "
        f"two-party `schedule` 9: {SCHEMA_VERSION} is the first that admits both"
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
    assert monitor.schedule.resettable is None, (
        f"the `{MONITOR_MEMBER}` member's schedule states `resettable`, so a planner "
        f"surface would restart the monitor's hold rather than the pacemaker's: "
        f"{monitor.schedule}"
    )
    assert monitor.background is False, (
        f"the `{MONITOR_MEMBER}` member no longer declares `background: false`, which is "
        "the one declaration that keeps the observer graph alive between its turns"
    )

    pacemaker = _declared(PACEMAKER_MEMBER)
    assert pacemaker.schedule.every == PACEMAKER_PERIOD_SECONDS, (
        f"the `{PACEMAKER_MEMBER}` member's period moved: {pacemaker.schedule}"
    )
    assert pacemaker.schedule.resettable is True, (
        f"the `{PACEMAKER_MEMBER}` member is no longer resettable, so a run already "
        f"reporting also gets a pacemaker surface: {pacemaker.schedule}"
    )
    assert pacemaker.background is None, (
        f"the `{PACEMAKER_MEMBER}` member states `background: {pacemaker.background}`; a "
        "scheduled member that states nothing is background, which is what a pacemaker "
        "is, and `false` would make a member that exits after each firing hold the run "
        "open forever"
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


def _graph_events(scratch: Path) -> list[Envelope]:
    """The dag-scope graph's own record of this run, which names the member per event.

    The graph writes these into the scratch this journey named, so this is that run's
    record and no other's. Read here rather than off the run's journal because the
    member and the turn's role are exactly what the pacing is measured on.
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
            decoded = json.loads(line)
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


def _let_go(environment: dict[str, str]) -> datetime | None:
    """When the driver fired the run-end hook, read through the run's detailed stream.

    `just orchestrate` names a hook for both endings, and the driver fires it last: after
    the engine loop has finished, the observer has been cancelled and the settlement
    announced, and before the hook's own command runs. So its `run-hook-fired` event is
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
    fired = LET_GO_LINE.search(rendered.stdout + rendered.stderr)
    return _instant(fired.group("at")) if fired else None


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
    launch: subprocess.Popen[str], environment: dict[str, str]
) -> list[StatusReading]:
    """Read the run's status through the real recipe until the launch returns.

    Cut at the `providers:` boundary the way `AGENTS.md` tells a watch to cut it, because
    everything below it is `oneagentgraph health`'s report about the host. Each reading
    keeps both instants, so a reading is credited to a hold only when the whole of it
    fell inside one.
    """
    readings: list[StatusReading] = []
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
    return readings


#: The text of the one surface a paced launch raises and reads, as a planner would.
CONSUMED_SURFACE = "a planner-visible update, raised so that reading it resets a clock"


def _a_surface_raised_and_consumed(environment: dict[str, str]) -> bool:
    """Raise one surface on the live run and read it through the planner's own recipe.

    Reading a surface is what restarts a resettable member's clock — the engine resets
    every member the run's observer graph declares `resettable` — so this is what makes
    the graph record which of its members the launched document declared resettable.
    `False` until the run exists to raise on.
    """
    raised = subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "onepipeline"), "surface", "--kind", "check-in"]
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


class Paced(NamedTuple):
    """One real launch under the shipped document, paced small, and what it recorded."""

    events: list[Envelope]
    readings: list[StatusReading]
    #: When the driver let go of the run, by the engine's own clock: the instant it
    #: fired the run-end hook, which it does after cancelling the observer and announcing
    #: the settlement and before the hook's command runs. `None` when the stream renders
    #: no such firing.
    let_go: datetime | None
    #: When the launch process returned, by this journey's own clock, and how. Later than
    #: `let_go` by however long the hook's recipe took, which is said beside a failure.
    returned: datetime
    status: int
    printed: str


def _paced_launch(tmp_path: Path, oneharness_bin: str) -> Paced:
    """Launch a one-node run under the shipped observer graph, with both periods small.

    Everything between `just orchestrate` and the model is real: the driver, the observer
    graph, `oneagentgraph`, the onejudge conversation and its channel-served judge side.
    The worker is held so the run outlasts several holds; the monitor's own words are
    scripted quiet, so the pacing measured is the graph's and not the model's.
    """
    environment = _launched_environment(tmp_path, oneharness_bin)
    # A session store of its own, and a deliberately short one: `HELD_SECONDS` explains
    # why, and `test_monitor_cursor_e2e.py`'s `session_state` is the same remedy.
    session_store = Path(tempfile.mkdtemp(prefix="ogl-"))
    environment["XDG_STATE_HOME"] = str(session_store)
    # llmlint: ignore-block[live_tier_compiles_and_requires_credential] The boundary under
    # test is the graph's pacing of a conversation, and a credentialed turn would prove
    # nothing more about it; the paid provider is the one thing this suite doubles.
    # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
    environment[OBSERVER_ANSWER_ENV] = SAID_ON_A_QUIET_TURN
    # llmlint: ignore-end[live_tier_compiles_and_requires_credential]
    environment[OBSERVER_MEMBER_ENV] = MONITOR_MEMBER
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    environment[GRAPH_STATE_ENV] = str(tmp_path / "graph-state")
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
                "--set",
                f"members.{PACEMAKER_MEMBER}.schedule.every={PACED_PACEMAKER_SECONDS}",
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdout=streaming,
            stderr=subprocess.STDOUT,
        )
        try:
            readings = _status_readings(launch, environment)
            status = launch.wait(timeout=e2e_timeout(300))
            returned = datetime.now(UTC)
        finally:
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
            shutil.rmtree(session_store, ignore_errors=True)
    return Paced(
        events=_graph_events(tmp_path / "graph-state"),
        readings=readings,
        let_go=_let_go(environment),
        returned=returned,
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
      heartbeat lands inside a hold, and `just status` read inside one reports neither
      `OBSERVER DEAD` nor `OBSERVER NOT RESTARTED`;
    * the pacemaker fires inside a hold and the graph survives it, taking another monitor
      turn afterwards;
    * reading a planner surface restarts the pacemaker's clock and no other member's,
      because the pacemaker is the one member the launched document declares
      `resettable`;
    * the observer ends when the run settles rather than when the hold would have: the
      driver lets go of the run before the next turn was due.

    Reverting the document's `schedule` fails before any of it: `--set
    members.monitor.schedule.every` on a member with no schedule is refused by the reader
    (`this graph has no schedule`), the launch attaches no observer, and the graph records
    nothing for the run. Reverting `background: false` fails at the reader too, because
    the pacemaker's deferred first turn then has nothing to hold the run open for it.
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
    died = _of(events, "member-died")
    assert not died, (
        f"a member of the observer graph died during a paced run: {died}\n{paced.printed}"
    )

    turns = _agent_turns(events, MONITOR_MEMBER)
    assert len(turns) >= 3, (
        f"the `{MONITOR_MEMBER}` member took {len(turns)} agent turn(s) in a run held for "
        f"{HELD_SECONDS}s at a {PACED_HOLD_SECONDS}s hold, so it is not being paced through "
        f"the run — or not held open between its turns at all:\n{paced.printed}"
    )
    opened_with_the_wave = (turns[0].at - started[0].at).total_seconds()
    assert opened_with_the_wave < PACED_HOLD_SECONDS, (
        f"the monitor's first turn opened {opened_with_the_wave:.1f}s after the graph "
        "started, which is a deferred first turn: `start_after: 0` is what opens the "
        f"conversation with the wave, and the document no longer says it:\n{paced.printed}"
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
    heartbeats = _of(events, "member-heartbeat", MONITOR_MEMBER)
    assert any(hold.covers(beat.at) for hold in holds for beat in heartbeats), (
        f"no heartbeat of the `{MONITOR_MEMBER}` member landed inside a hold "
        f"({[beat.at.isoformat() for beat in heartbeats]} against {holds}), so a held "
        f"conversation is silent to the activity watchdog:\n{paced.printed}"
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

    fired = _of(events, "cron-fired", PACEMAKER_MEMBER)
    fired_inside = [firing for firing in fired if any(hold.covers(firing.at) for hold in holds)]
    assert fired_inside, (
        f"the `{PACEMAKER_MEMBER}` member never fired inside a monitor hold "
        f"({[firing.at.isoformat() for firing in fired]} against {holds}), so nothing here "
        f"shows a background member firing while a foreground one holds the run open:\n"
        f"{paced.printed}"
    )
    assert any(turn.at > fired_inside[0].at for turn in turns), (
        f"the monitor took no turn after the pacemaker fired at {fired_inside[0].at}, so "
        f"the firing may have ended the graph:\n{paced.printed}"
    )

    # The pacemaker is the graph's one resettable member, read back off this launch: the
    # surface read during it restarted the pacemaker's clock and nobody else's.
    assert _of(events, "cron-reset", PACEMAKER_MEMBER), (
        f"reading a surface restarted no clock of the `{PACEMAKER_MEMBER}` member, so the "
        f"launched document did not declare it resettable:\n{paced.printed}"
    )
    reset_others = [
        event for event in _of(events, "cron-reset") if event.member != PACEMAKER_MEMBER
    ]
    assert not reset_others, (
        f"reading a surface restarted the clock of {[e.member for e in reset_others]}, "
        f"which the launched document declares resettable beside the pacemaker"
    )

    # Read at the driver's own stamp for letting go of the run rather than at the
    # launch's return: the run-end hook fires between the two, and its recipe's runtime
    # on a loaded host is not the hold's — see `_let_go`.
    assert paced.let_go is not None, (
        f"the run's detailed stream renders no `run-hook-fired`, so when the driver let go "
        f"of the run cannot be read off it:\n{paced.printed}"
    )
    last_close = _judge_closes(events, MONITOR_MEMBER)[-1].at
    next_turn_was_due = last_close.timestamp() + PACED_HOLD_SECONDS
    assert paced.let_go.timestamp() < next_turn_was_due, (
        f"the driver let go of the run at {paced.let_go.isoformat()}, after the next "
        f"monitor turn was due at {datetime.fromtimestamp(next_turn_was_due, UTC).isoformat()}: "
        "the driver's cancel at settlement is not ending the monitor's last hold, so a run "
        f"that settles in seconds waits the hold out (the launch itself returned at "
        f"{paced.returned.isoformat()}, once the run-end hook had):\n{paced.printed}"
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


@pytest.mark.reads_docs
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
