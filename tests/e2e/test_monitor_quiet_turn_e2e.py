"""A monitor turn, answered by the judge side `graphs/dag-scope.yaml` really spawns.

The monitor's judge side is `onemessagebus serve surfaces --codec onejudge` over this
host's `config/onemessagebus.yaml`, and the codec's own rules — onemessagebus's
`docs/codecs.md` — decide what a turn costs: a turn that produced content is answered with a
non-completion and raises nothing, and a turn the agent side lost raises one bounded
`monitor-failed` surface and ends the member. Those rules are the bus's, proven in its own
suite. What is this repository's is the wiring, and it is what these journeys prove: that the
argv the graph declares, spawned from the directory a launch spawns it from, with the
environment a launch gives it, reaches those rules on a real run's channel.

Why the rules matter here is incidents on this host. While a turn's prose was raised
automatically, a monitor filing a finding had three moves and none was clean: of
`root-causes-94-plan`'s 54 surfaces, 19 are findings and 8 are prose restating the finding
immediately before them. And obeying an instruction to be quiet used to be fatal on the first
quiet turn — `spanish-language-tutor-upgrade` lost its observer five minutes into a two-hour
run, with nothing announcing the loss.

So each outcome is driven twice. At the judge command, against a run directory the real
engine made and read back through `onemessagebus status`, where every shape a turn arrives in
can be put to it in turn. And across a whole `just orchestrate` launch whose monitor really
takes its turns, with only the paid model's words scripted: a run of quiet turns, where the
member has to survive and the channel has to stay empty of its prose — beside the periodic
`check-in` member's own update, which is what makes "nothing was raised" a statement about the
monitor — and a run of lost turns, where the member has to die and say so once each time.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TypedDict, cast

import pytest
from fake_backend import (
    AGENT_DELAY_ENV,
    OBSERVER_ANSWER_ENV,
    OBSERVER_MEMBER_ENV,
    PACEMAKER_REPORT,
    PROMPT_LOG_ENV,
)
from project_fixtures import project_from_plan

# How many turns the monitor's AGENT side took, read the one way a turn is observable —
# out of the prompts it was given. Imported rather than restated: its sibling journey
# owns that reader, and two copies would drift into two ideas of whose turn a prompt is.
from test_monitor_survives_the_channel_e2e import _monitor_prompts

# The launch environment and the judge side's own seams have one source, the module that
# owns the launch journeys: the fake provider, the guarded PATH, the alternate-identity
# indirections, the isolated state root, and the argv read out of the graph.
from test_orchestrate_launch_e2e import (
    MONITOR_MEMBER,
    PACEMAKER_MEMBER,
    SETTLES_UNWATCHED,
    _judge_side,
    _just,
    _queue_state,
    _settling_project,
    _supervisor_frame,
)
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: What the scripted monitor says on every turn of the launched run below. Ordinary
#: prose, deliberately: there is no sentinel and no fixed string on this path, so what is
#: under test is that *any* words at all keep the member watching and queue nothing. It
#: names no node and quotes no frame, so a surface carrying it could only have come from
#: a judge side that raised prose.
SAID_ON_A_QUIET_TURN = "read the detailed stream; nothing needed raising this turn"

#: What the answer to a taken turn has to say about the turn after it. The graph paces
#: the monitor's conversation with a hold between turns, so an acknowledgement that told
#: the member to keep reading *now* would be an instruction to spend the turn it has just
#: finished.
NEXT_TURN_OPENS_AFTER_THE_HOLD = "next turn opens after the graph's hold"

#: How long a turn that raises nothing may take to answer before this journey calls it
#: hung. Such a turn asks the planner nothing, so what this really bounds is the
#: regression: a judge side that asked instead would sit out its whole reply window.
ANSWERED_WITHOUT_ASKING_SECONDS = 30

#: The kind the codec raises a turn its agent side lost under.
SURFACE_KIND_OF_A_LOST_TURN = "monitor-failed"

#: The ceiling a lost turn's surface may not exceed, for any transcript, identity, and
#: run id these journeys drive through it. The raw transcript it replaces was 21,531
#: characters.
NAMED_FAILURE_LIMIT = 400

#: How the recorded transcript's harness classified the refusal it ended on.
LOST_TURN_CAUSE = "usageLimitExceeded"

#: Text that occurs only inside the transcript, asserted absent from the surface: a
#: frame name, a key, and the URL out of the refusal's own prose.
ONLY_IN_THE_TRANSCRIPT = ("turn/completed", "codexErrorInfo", "chatgpt.com")

#: The prompt the harness echoes back at itself, which is most of a lost turn's weight.
ECHOED_PROMPT = (
    "Actively monitor one executing tracked graph and report what drifts from it.\n" * 100
)

#: The judge command's verdict on a turn the agent side lost: the member failed. onejudge
#: reads any exit but 0 as its judge side failing, and `oneagentgraph` then ends the member.
MEMBER_FAILED = 1


def _lost_turn_transcript(codex_home: str) -> str:
    """What a monitor turn its agent side lost leaves as the last thing it "said".

    The shape measured off this host's own `runs/rc-fixes-brief` channel queue: the
    harness's own JSON-RPC stream, one frame per line, opening with the home the
    identity it ran as is credentialed from, echoing the whole prompt back, and ending
    in an error frame and a `turn/completed` whose status is `failed`. Twenty of these
    queued unread on one run, each one 21,531 characters that had to be opened to find
    out it said nothing.

    Thread and session identifiers are this fixture's own and the echoed prompt is the
    monitor's role rather than the recorded run's; the frames, their order, and the two
    that carry the refusal are as recorded.
    """
    refusal = (
        "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
        "to purchase more credits or try again at Aug 20th, 2026 3:30 AM."
    )
    thread = "01a01a5d-8df8-77b0-aace-730d932eefe4"
    turn = "01a01a5d-8f08-7642-947f-d6103de39e42"
    echoed = {
        "type": "userMessage",
        "id": "01a01a5d-9351-77a1-b947-d7aa89759a9c",
        "content": [{"type": "text", "text": ECHOED_PROMPT}],
    }
    error = {"message": refusal, "codexErrorInfo": LOST_TURN_CAUSE, "additionalDetails": None}
    frames: list[dict[str, object]] = [
        {
            "id": 1,
            "result": {
                "userAgent": "oneharness/0.145.0 (Ubuntu 24.4.0; x86_64)",
                "codexHome": codex_home,
                "platformFamily": "unix",
                "platformOs": "linux",
            },
        },
        {"method": "thread/started", "params": {"thread": {"id": thread}}},
        {
            "method": "turn/started",
            "params": {"threadId": thread, "turn": {"id": turn, "status": "inProgress"}},
        },
        {"method": "item/started", "params": {"item": echoed}},
        {"method": "item/completed", "params": {"item": echoed}},
        {
            "method": "account/rateLimits/updated",
            "params": {"rateLimits": {"credits": {"hasCredits": False, "balance": "0"}}},
        },
        {
            "method": "thread/status/changed",
            "params": {"threadId": thread, "status": {"type": "systemError"}},
        },
        {"method": "error", "params": {"error": error, "willRetry": False, "threadId": thread}},
        {
            "method": "turn/completed",
            "params": {
                "threadId": thread,
                "turn": {"id": turn, "items": [], "status": "failed", "error": error},
            },
        },
    ]
    return "\n".join(json.dumps(frame) for frame in frames)


class QueuedSurface(TypedDict, total=False):
    """One planner surface as the bus reports it, narrowed to what a manager reads."""

    id: int
    kind: str
    message: str
    blocking: bool
    source: str


def _surfaces(environment: dict[str, str], run: str) -> list[QueuedSurface]:
    """Every surface the run's channel is holding, read through `onemessagebus status`."""
    waiting = _queue_state(environment, run, "surfaces")["waiting"]
    surfaces: list[QueuedSurface] = []
    for record in waiting:
        assert isinstance(record.get("kind"), str), record
        assert isinstance(record.get("message"), str), record
        assert isinstance(record.get("blocking"), bool), record
        # Each field above is checked before it is read as the narrowed shape.
        surfaces.append(cast(QueuedSurface, record))
    return surfaces


def _raised_since(environment: dict[str, str], run: str, before: int) -> list[QueuedSurface]:
    """The surfaces appended to the run's channel after it held `before` records."""
    return [surface for surface in _surfaces(environment, run) if surface.get("id", -1) >= before]


def _records(environment: dict[str, str], run: str) -> int:
    return _queue_state(environment, run, "surfaces")["records"]


#: The run whose directory the judge-command journeys drive against. One per worker the
#: module lands on, and every such journey shares its xdist group, so each reads the
#: records it appended rather than another's.
JUDGED_RUN = "monitor-judge-side"


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] One real launch per
# worker, because what the judge-command journeys below prove is this host's wiring over a
# run directory the engine itself made rather than one this suite assembled. It sits in
# `tests/e2e` for the reason this file's block over the whole-run journeys states: which Nx
# project owns that tree is not this change's to move.
@pytest.fixture(scope="module")
def judged_run(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[dict[str, str]]:
    """A run root the real engine made, and the environment that names it.

    Launched and settled through the real recipe with nothing watching it, so the run
    directory is the engine's own and the channel inside it is empty until a judge side
    writes to it.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("judge-side")
    environment = _launched_environment(tmp_path, oneharness_bin)
    launch = _just(
        "orchestrate",
        _settling_project(tmp_path, JUDGED_RUN),
        *SETTLES_UNWATCHED,
        environment=environment,
    )
    try:
        assert launch.returncode == 0, launch.stdout + launch.stderr
        run_root = Path(environment["ONEPIPELINE_RUNS_DIR"]) / JUDGED_RUN
        assert (run_root / "launch.json").is_file(), f"the launch made no run root at {run_root}"
        yield environment
    finally:
        _just("stop", JUDGED_RUN, environment=environment, seconds=60)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _answered_without_asking(
    environment: dict[str, str], said: str
) -> subprocess.CompletedProcess[str]:
    """Put one supervisor frame to the judge side and wait for it to answer on its own."""
    try:
        return _judge_side(
            _supervisor_frame(JUDGED_RUN, said),
            environment,
            JUDGED_RUN,
            seconds=ANSWERED_WITHOUT_ASKING_SECONDS,
        )
    except subprocess.TimeoutExpired as waited:
        raise AssertionError(
            f"the judge side did not answer run {JUDGED_RUN} on its own within the wait, "
            "which is what asking the planner looks like from here: a question queued and "
            "a reply window nobody is coming to answer"
        ) from waited


def test_the_recorded_supervisor_frame_is_one_the_bus_codec_reads() -> None:
    """The frame these journeys feed the judge side is onejudge's protocol v6, as the bus has it.

    The bus registers the five onejudge frames as schemas, and a fixture this suite wrote
    is a second copy of somebody else's wire format. So it is checked against the
    registered one, beside a copy missing a required field that the same check refuses —
    without that control, a check that accepted anything would pass too.
    """
    schema = "agent.onejudge-frame.supervisor@6"

    def checked(frame: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["onemessagebus", "schema", "check", schema],
            cwd=REPO_ROOT,
            input=frame,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )

    frame = _supervisor_frame(JUDGED_RUN, SAID_ON_A_QUIET_TURN)
    accepted = checked(frame)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    without_persona = {key: value for key, value in json.loads(frame).items() if key != "persona"}
    refused = checked(json.dumps(without_persona))
    assert refused.returncode == 1, refused.stdout + refused.stderr


@pytest.mark.xdist_group("monitor-judge-side")
@pytest.mark.parametrize(
    ("case", "said"),
    [
        ("a turn that found nothing", SAID_ON_A_QUIET_TURN),
        ("an observation written as prose", "issue: node api has drifted from its criteria"),
        (
            "a machine transcript no failure can be proven inside",
            '{"result": {"codexHome": "/home/nick/.codex"}}\n'
            '{"method": "turn/completed", "params": {"turn": {"status": "completed"}}}',
        ),
    ],
)
def test_a_monitor_turn_that_produced_content_costs_the_planner_no_surface(
    judged_run: dict[str, str], case: str, said: str
) -> None:
    """Content is content: the member is answered, it lives, and nothing is queued.

    Three things have to be true at once and none is enough alone. Nothing may reach the
    planner's queue — an observation belongs on it as a `finding` op the monitor issues
    itself. onejudge has to be handed a ruling it can act on, because the alternative is
    the exit status that killed this host's monitors: a non-completion settles nothing and
    leaves the member watching. And what the monitor said may not come back inside that
    ruling, because a message composed from the monitor's own words is a republication by
    another route.

    The three cases are the three shapes a turn's content arrives in, and the point of
    driving all of them is that the judge side does not branch between them at all.
    """
    before = _records(judged_run, JUDGED_RUN)

    answered = _answered_without_asking(judged_run, said)

    assert answered.returncode == 0, f"{case}: {answered.stderr}"
    raised = _raised_since(judged_run, JUDGED_RUN, before)
    assert raised == [], (
        f"{case} put a surface on the planner's queue, which is the update a monitor's "
        f"report is supposed to arrive as exactly once: {raised}"
    )
    ruling = json.loads(answered.stdout)
    assert ruling["completion"] is False, (
        f"{case} answered onejudge with a completion, which settles a watch nobody ruled "
        f"on: {ruling}"
    )
    assert said not in ruling["message"], f"{case} came back inside its own ruling: {ruling}"
    assert "finding" in ruling["message"], (
        f"{case} was answered without naming the one route a report reaches the planner "
        f"by, so a monitor writing prose is never told it reached nobody: {ruling}"
    )
    assert ruling["reason"].strip(), (
        f"{case} answered with a bare non-completion, which reads in a transcript like a "
        f"planner who refused the watch: {ruling}"
    )
    assert NEXT_TURN_OPENS_AFTER_THE_HOLD in ruling["message"], (
        f"{case} was answered as if the monitor's next turn were now, and the graph holds "
        f"that conversation between turns: {ruling}"
    )


@pytest.mark.xdist_group("monitor-judge-side")
@pytest.mark.parametrize(
    ("case", "said", "named"),
    [
        (
            "a turn whose transcript proves it was lost",
            _lost_turn_transcript("/home/nick/.codex"),
            f"monitor turn failed: {LOST_TURN_CAUSE} on codex.",
        ),
        ("a turn carrying no assistant content at all", None, "monitor turn failed:"),
    ],
)
def test_a_monitor_turn_its_agent_side_lost_raises_one_bounded_surface_and_fails_the_member(
    judged_run: dict[str, str], case: str, said: str | None, named: str
) -> None:
    """The one surface left on this path, and the control for every assertion above.

    "Nothing was queued" is evidence only because the same judge side, the same
    environment and the same channel really do queue a surface here. A turn the agent side
    lost writes the harness's own stream where its words go — or nothing at all — and
    reading that as content would let a monitor whose agent side is failing look healthy
    for the rest of the run. So it is raised once, non-blocking, named rather than
    transcribed, and the member is failed rather than handed a verdict it would act on.
    """
    before = _records(judged_run, JUDGED_RUN)

    failed = _judge_side(_supervisor_frame(JUDGED_RUN, said), judged_run, JUDGED_RUN)

    assert failed.returncode == MEMBER_FAILED, f"{case}: {failed.stdout}{failed.stderr}"
    assert "completion" not in failed.stdout, (
        f"{case} produced a verdict onejudge would act on: {failed.stdout}"
    )
    raised = _raised_since(judged_run, JUDGED_RUN, before)
    assert len(raised) == 1, f"{case} raised {len(raised)} surface(s): {raised}"
    surface = raised[0]
    assert surface["kind"] == SURFACE_KIND_OF_A_LOST_TURN, surface
    assert surface["blocking"] is False, (
        f"{case} was raised as a blocking surface, which holds a manager on a question "
        f"about watching rather than about work: {surface}"
    )
    assert surface["message"].startswith(named), surface["message"]
    assert len(surface["message"]) <= NAMED_FAILURE_LIMIT, surface["message"]
    for buried in ONLY_IN_THE_TRANSCRIPT:
        assert buried not in surface["message"], f"the transcript reached the surface: {surface}"
    assert ECHOED_PROMPT.splitlines()[0] not in surface["message"], surface


#: The plan the quiet launch below runs, and the run id `onepipeline` mints from its name.
#: One node, held open just long enough for the monitor to take turns against it: what is
#: under test is the observer graph every launch attaches, not anything the node does.
LAUNCHED_RUN = "monitor-quiet-turn"
HELD_NODE = "held"
HELD_SECONDS = 25

#: The run a monitor whose every turn is lost watches, held the same way.
LOST_RUN = "monitor-lost-turn"

#: How often the launch tells the pacemaker to come due. Short, so the run has a second
#: supervisory member producing surfaces — which is what makes "no MONITOR surface" a
#: statement about the monitor rather than about a run nothing surfaced on.
PACEMAKER_INTERVAL_SECONDS = 1

#: How long the launch tells the graph to hold the monitor between its turns. The
#: shipped `graphs/dag-scope.yaml` paces that conversation one turn per 300 seconds, and
#: this run lasts about as long as `HELD_SECONDS` — so under the shipped period the
#: second turn this journey needs would never open, and a run that took one turn read
#: exactly like the incident. `--set members.monitor.schedule.every` is the published
#: override for a journey that needs turns closer together than the shipped period, and
#: the shipped value stays what it is. Two seconds rather than one, so the second turn
#: is visibly a paced turn and not the graph's floor.
MONITOR_HOLD_SECONDS = 2

#: Where `oneagentgraph` writes its own event log, named by this journey so it reads
#: this run's graph and never a concurrent dispatch's. The rendered event line does not
#: carry the member, and which member died is the whole question.
GRAPH_STATE_ENV = "ONEAGENTGRAPH_STATE_DIR"
MEMBER_DIED = "member-died"
JUDGE_DECIDED = "judge-decided"

#: How many agent turns the monitor has to have taken for its survival to mean anything.
#: Two, and the second is the whole point: a turn only follows a turn its judge side
#: answered with a ruling onejudge could act on, so a member that took another one is a
#: member a quiet turn kept alive rather than one that never got going.
TURNS_PROVING_IT_KEPT_WATCHING = 2

#: How onejudge spells the decision a judge side's answer came to, in `judge-decided`:
#: a turn answered with a next instruction, and a judge side that failed.
DECIDED_CONTINUE = "continue"
DECIDED_ERROR = "error"


def _launch_environment(tmp_path: Path, oneharness_bin: str, answer: str) -> dict[str, str]:
    """The environment one real launch runs under, with its monitor scripted to `answer`.

    The monitor's words are the paid model's, and the paid model is the only thing doubled
    here — so the stand-in is told what to answer for that member alone, and every other
    turn of the run stays what it was.
    """
    environment = _launched_environment(tmp_path, oneharness_bin)
    # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
    environment[OBSERVER_ANSWER_ENV] = answer
    environment[OBSERVER_MEMBER_ENV] = MONITOR_MEMBER
    # Holds the dispatched worker's turn so the run outlives the monitor's first few.
    # The stand-in delays that side alone, so the watch is not slowed with it.
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    environment[GRAPH_STATE_ENV] = str(tmp_path / "graph-state")
    environment[PROMPT_LOG_ENV] = str(tmp_path / "prompts.jsonl")
    return environment


#: How the shipped `check-in` member's own task tells it to raise its update. Read out
#: of `graphs/dag-scope.yaml` rather than retyped, so the journey below drives the verb
#: and the kind that member is really given and fails if either moves.
PACEMAKER_SURFACE_FORM = re.compile(r"onepipeline surface --kind (?P<kind>\S+) <run-id>")

#: The `onepipeline` this checkout pins. A bare name would let the ambient environment
#: decide which release answers.
PINNED_ONEPIPELINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"


def _pacemaker_surface_kind() -> str:
    """The surface kind the shipped pacemaker's own task names, off the shipped graph."""
    declared = (REPO_ROOT / "graphs" / "dag-scope.yaml").read_text(encoding="utf-8")
    named = PACEMAKER_SURFACE_FORM.search(declared)
    assert named is not None, (
        "graphs/dag-scope.yaml no longer tells its pacemaker to raise its update with "
        "`onepipeline surface --kind <kind> <run-id>`, so this journey is driving a "
        "route that member has moved off"
    )
    return named.group("kind")


def _raised_by_the_pacemakers_own_verb(
    environment: dict[str, str], run: str, kind: str
) -> subprocess.CompletedProcess[str]:
    """Raise one update the way the pacemaker's task tells it to: bytes on the verb's stdin.

    The published verb, against the run's own durable channel, with the text handed over
    as bytes rather than as a command-line word — which is the form `graphs/dag-scope.yaml`
    prescribes and `tests/e2e/test_supervisory_prompt_discipline_e2e.py` holds it to.
    """
    return subprocess.run(  # noqa: S603 - the published verb, as the pacemaker runs it
        [str(PINNED_ONEPIPELINE), "surface", "--kind", kind, run],
        cwd=REPO_ROOT,
        env=environment,
        input=PACEMAKER_REPORT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


class EventLabels(TypedDict, total=False):
    """A graph event's labels, narrowed to the one that names the member it happened to."""

    member: str


class TurnMessage(TypedDict, total=False):
    """The payload of a `turn-message`, narrowed to what a member actually said."""

    role: str
    text: str


class GraphEvent(TypedDict, total=False):
    """The additive graph event, narrowed to the fields this journey reads."""

    kind: str
    labels: EventLabels
    payload: TurnMessage


def _graph_events(scratch: Path) -> list[GraphEvent]:
    """The dag-scope graph's own record of this run, which names the member per event.

    `just monitor` renders these too, but its line carries the event and not the member
    it happened to, and which member died is exactly the question. The graph writes them
    into the scratch this journey named, so this is that run's record and no other's.
    """
    # llmlint: ignore[tests_mirror_real_usage] No operator view identifies which member
    # died; that missing signal is why the incident stayed hidden behind plain ACTIVE.
    events: list[GraphEvent] = []
    for log in sorted(scratch.glob("dag-scope-*/events.jsonl")):
        # llmlint: ignore[tests_mirror_real_usage] The rendered line omits the member.
        for line in log.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            # `Any` because this is `oneagentgraph`'s wire format: `json.loads` answers
            # `Any`, and `GraphEvent` is a `TypedDict` no runtime check can establish. So
            # each field this journey reads is checked here, and the `cast` records that.
            decoded: Any = json.loads(line)
            assert isinstance(decoded, dict), decoded
            assert isinstance(decoded.get("kind"), str), decoded
            assert isinstance(decoded.get("labels", {}), dict), decoded
            events.append(cast(GraphEvent, decoded))
    return events


def _of_the_monitor(events: list[GraphEvent], kind: str) -> list[GraphEvent]:
    return [
        event
        for event in events
        if event.get("kind") == kind and event.get("labels", {}).get("member") == MONITOR_MEMBER
    ]


#: The event a member's own words arrive on, and the role that makes them the member's
#: rather than its supervisor's.
TURN_MESSAGE = "turn-message"
SPOKEN_BY_THE_MEMBER = "assistant"


def _what_the_monitor_said(events: list[GraphEvent]) -> list[str]:
    """Every word the monitor's agent side actually produced, from the graph's own record.

    Read here rather than off the stand-in's prompt log: that log records the answer the
    stand-in was *told* to give, and `turn-message` is the graph's own record of what came
    back, so it is the only witness that the scripted turn is the turn that happened.
    """
    return [
        payload["text"]
        for event in _of_the_monitor(events, TURN_MESSAGE)
        if isinstance(payload := event.get("payload"), dict)
        and payload.get("role") == SPOKEN_BY_THE_MEMBER
        and isinstance(payload.get("text"), str)
    ]


def _launch_to_settlement(environment: dict[str, str], run: str, tmp_path: Path) -> str:
    """Drive one held plan through the real recipe to settlement, and return what it printed."""
    plan = tmp_path / f"{run}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": run,
                "goal": {"text": "prove what a monitor's turn costs the run it watches"},
                "tasks": [
                    {
                        "id": HELD_NODE,
                        "persona": "engineer",
                        "task": "## What\nReport.\n\n## Why\nBecause.\n\n"
                        "## Acceptance criteria\n- Reported.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    # Streamed to a file rather than a pipe nobody drains: an attached launch prints the
    # whole merged event stream, and a full pipe buffer stops the driver mid-run — which
    # reads exactly like a run that died.
    printed = tmp_path / f"{run}.launch.log"
    with printed.open("w", encoding="utf-8") as streaming:
        launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
            [
                "just",
                "orchestrate",
                project_from_plan(plan),
                "--heartbeat-interval",
                str(PACEMAKER_INTERVAL_SECONDS),
                "--set",
                f"members.{MONITOR_MEMBER}.schedule.every={MONITOR_HOLD_SECONDS}",
                # No run-end hooks: they are not this journey's subject, and an attached
                # launch awaits the one the run fires, which queues its completion surface
                # beside the updates these journeys count. A blank value is the recipe's
                # spelling of none.
                "--success-hook=",
                "--failure-hook=",
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdout=streaming,
            stderr=subprocess.STDOUT,
        )
        try:
            launch.wait(timeout=e2e_timeout(300))
        finally:
            launch.kill()
            launch.wait(timeout=e2e_timeout(60))
            _just("stop", run, environment=environment, seconds=60)
    return printed.read_text("utf-8")


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `xdist_group` selects
# an xdist worker under this suite's `--dist loadgroup`, not a test tier; the tiers here
# split by what a test reads, which is what each one's Nx cache key has to cover.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Same site, same
# follow-up: this journey and the `tests/e2e` tree it sits in both pre-date this change,
# and which Nx project owns that tree is a property of the tree rather than of anything
# here — `AGENTS.md` records the tier split as a deliberate decision, and re-homing forty
# launch journeys into a new project is enforcement configuration this change may not
# move in order to pass.
# llmlint: ignore-block[shell_test_tiers_stay_split] Same site, same reason; and this is
# a pytest journey over the real recipe, not a shell test suite.
@pytest.mark.xdist_group("monitor-quiet-turn")
def test_a_monitor_taking_quiet_turns_survives_a_whole_real_run(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The incident, reproduced and then not reproduced, at the scale it happened.

    `spanish-language-tutor-upgrade` lost its observer five minutes into a two-hour run
    because its monitor did what its persona told it to. Nothing announced that: a run
    with no observer reports plain `ACTIVE`, so the only place the loss is visible is
    `oneagentgraph`'s own `member-died` record — which is what this reads, out of the
    graph state this launch names, after driving a real run to settlement with a monitor
    whose every turn is a quiet one.

    The channel is read too, through the bus: surviving while queueing that prose onto the
    planner's queue would keep the member alive and still spend the update the `finding`
    op is supposed to be the only thing spending. And the periodic member's own route is
    driven against the same run's channel afterwards, which is what makes "nothing was
    queued" a statement about the monitor rather than about a run whose channel nothing
    could reach.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _launch_environment(tmp_path, oneharness_bin, SAID_ON_A_QUIET_TURN)
    printed = _launch_to_settlement(environment, LAUNCHED_RUN, tmp_path)

    events = _graph_events(Path(environment[GRAPH_STATE_ENV]))
    assert events, (
        f"the dag-scope graph recorded nothing for this run, so the absence of a "
        f"`{MEMBER_DIED}` below proves nothing:\n{printed}"
    )
    died = _of_the_monitor(events, MEMBER_DIED)
    assert not died, (
        f"the `{MONITOR_MEMBER}` member did not survive a run of quiet turns; the graph "
        f"recorded {json.dumps(died)}. The run goes on being driven and reporting "
        "`ACTIVE` after that, with nothing watching it and nothing announcing the loss."
    )
    # llmlint: ignore[tests_mirror_real_usage] A second paid-model turn is the observable
    # proof that onejudge acted on the first ruling; no run view exposes member turns.
    turns = _monitor_prompts(Path(environment[PROMPT_LOG_ENV]))
    assert len(turns) >= TURNS_PROVING_IT_KEPT_WATCHING, (
        f"the `{MONITOR_MEMBER}` member took {len(turns)} agent turn(s), so this run "
        "cannot show that a quiet turn was answered with a ruling onejudge could act "
        f"on — a member that never took a second turn is not one that kept watching:\n"
        f"{printed}"
    )
    said_by_the_monitor = _what_the_monitor_said(events)
    assert len(said_by_the_monitor) >= TURNS_PROVING_IT_KEPT_WATCHING, (
        f"the monitor produced {len(said_by_the_monitor)} message(s), so this run cannot "
        f"show that a quiet turn was answered at all: {said_by_the_monitor}"
    )
    assert all(spoken == SAID_ON_A_QUIET_TURN for spoken in said_by_the_monitor), (
        "the monitor said something other than the quiet turn this run scripted, so "
        f"survival was measured against another answer: {said_by_the_monitor}"
    )
    decided = [event["payload"].get("decision") for event in _of_the_monitor(events, JUDGE_DECIDED)]
    assert decided and set(decided) == {DECIDED_CONTINUE}, (
        "the monitor's judge side did not answer every quiet turn with a next instruction, "
        f"which is the non-completion that keeps a member watching: {decided}"
    )
    queued = _surfaces(environment, LAUNCHED_RUN)
    # By the monitor's own WORDS rather than by kind: `monitor` is also what the engine
    # queues a single-sided member's proposal under, so a kind filter would fail on a
    # surface the monitor had nothing to do with.
    said = [one for one in queued if one.get("message", "").strip() == SAID_ON_A_QUIET_TURN]
    assert not said, (
        "a monitor's own prose reached the planner's queue, which is the route a finding "
        f"is supposed to own: {queued}"
    )
    # And the periodic member's own route still reaches that queue, driven in the SAME
    # run rather than asserted. `graphs/dag-scope.yaml`'s `check-in` member is
    # single-sided — `kind: oneharness`, with no judge side — so it never reaches the
    # bus codec at all: its task tells it to raise its update with `onepipeline surface`,
    # and that verb is what is run here, under the kind read out of the shipped task
    # rather than retyped. Its schedule is half an hour and this run is under a minute, so
    # the member does not come due inside it; what is under test here is the route, and
    # `tests/e2e/test_supervisory_prompt_discipline_e2e.py` is what reads the turn.
    kind = _pacemaker_surface_kind()
    raised = _raised_by_the_pacemakers_own_verb(environment, LAUNCHED_RUN, kind)
    assert raised.returncode == 0, (
        f"the `{PACEMAKER_MEMBER}` member's own verb was refused on a run whose monitor "
        f"prose raised nothing: {raised.stderr}"
    )
    paced = _surfaces(environment, LAUNCHED_RUN)
    assert [one for one in paced if one.get("message", "").strip() == PACEMAKER_REPORT] == paced, (
        f"the `{PACEMAKER_MEMBER}` member's update did not reach the planner's queue, or "
        f"did not reach it alone, in a run whose monitor prose raised nothing: {paced}"
    )
    assert paced and paced[0]["kind"] == kind and paced[0].get("source") == kind, (
        f"the `{PACEMAKER_MEMBER}` member's update no longer carries its own kind and "
        f"source, which is what keeps it clear of every kind the judge side raises: {paced}"
    )


@pytest.mark.xdist_group("monitor-lost-turn")
def test_a_monitor_whose_turns_are_lost_dies_saying_so_once_each_time(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A lost turn ends the member, and the planner is told once per member life, by name.

    The launch is scripted so that every monitor turn is lost the way this host's were:
    the harness's own transcript where the monitor's words go, credentialed from the
    alternate codex home. What the wiring owes is three things the codec cannot owe
    alone. The member really dies — its judge side's exit reaches onejudge unswallowed,
    so the run is not left reporting a watcher that is not there. Each death is announced
    exactly once, as the bounded non-blocking line a manager reads, on this run's own
    channel. And the line names the identity whose quota to look at as `codex:alternate`,
    which the codec can only do if the launch handed the judge side the same
    `ORCHESTRATOR_CODEX_ALT_HOME` the monitor's agent side ran under.

    The driver relaunches an observer graph whose monitor died, a bounded number of times,
    so a run of lost turns records several lives; the claim is one surface per death,
    however many there were.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _launch_environment(tmp_path, oneharness_bin, "")
    # The alternate home this launch establishes for its identities, which is what the
    # monitor's agent side would have been credentialed from on that identity.
    # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
    environment[OBSERVER_ANSWER_ENV] = _lost_turn_transcript(
        environment["ORCHESTRATOR_CODEX_ALT_HOME"]
    )
    printed = _launch_to_settlement(environment, LOST_RUN, tmp_path)

    events = _graph_events(Path(environment[GRAPH_STATE_ENV]))
    deaths = _of_the_monitor(events, MEMBER_DIED)
    lost = [death for death in deaths if "was lost" in str(death["payload"].get("detail"))]
    assert lost, (
        f"no `{MONITOR_MEMBER}` member died of a lost turn, so its judge side's failure "
        f"never reached onejudge; the graph recorded {json.dumps(deaths)}:\n{printed}"
    )
    decided = [event["payload"].get("decision") for event in _of_the_monitor(events, JUDGE_DECIDED)]
    assert decided and set(decided) == {DECIDED_ERROR}, (
        "a lost monitor turn was answered with a ruling onejudge could act on, so the member "
        f"would have gone on looking alive: {decided}"
    )
    announced = [
        surface
        for surface in _surfaces(environment, LOST_RUN)
        if surface["kind"] == SURFACE_KIND_OF_A_LOST_TURN
    ]
    assert len(announced) == len(lost), (
        f"{len(lost)} monitor(s) died of a lost turn and {len(announced)} `"
        f"{SURFACE_KIND_OF_A_LOST_TURN}` surface(s) reached the planner, where each death "
        f"is owed exactly one: {announced}"
    )
    for surface in announced:
        assert surface["blocking"] is False, surface
        assert surface["message"].startswith(
            f"monitor turn failed: {LOST_TURN_CAUSE} on codex:alternate."
        ), (
            "the surface did not name the alternate identity the turn was lost on, so a "
            f"manager is sent to a quota that is fine: {surface['message']!r}"
        )
        assert len(surface["message"]) <= NAMED_FAILURE_LIMIT, surface["message"]
        for buried in ONLY_IN_THE_TRANSCRIPT:
            assert buried not in surface["message"], surface


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[shell_test_tiers_stay_split]
