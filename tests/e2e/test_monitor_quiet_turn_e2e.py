"""A monitor turn, driven through the real filter and the real published channel.

The monitor's report reaches the planner as a `finding` op it issues itself, and the
prose a turn ends in raises no planner surface at all. That is a deletion rather than a
filter: while prose was raised automatically, a monitor filing a finding had three moves
and none was clean, and it took the worst of them every time — of `root-causes-94-plan`'s
54 surfaces, 19 are findings and 8 are prose restating the finding immediately before
them, three to thirty-five seconds later. A planner may not filter the unread-surface
line, so 30% of that monitor's surfaces degraded the one indicator that discipline exists
to protect.

What the turn's content is still read for is liveness, and that has its own incident
behind it: obeying an instruction to be quiet used to be fatal on the first quiet turn —
see `scripts/channel-serve.py`'s header. So both answers a turn can end in are driven
here at the interface `oneagentgraph` spawns the filter through, against the pinned
`onepipeline`'s own published `channel serve`. No stand-in channel anywhere: all this
journey provides is an empty directory named after a run, so what was and was not queued
is read out of `runs/<run-id>/channel/queue.json` where a manager reads one.

Three things make the negative half mean something rather than pass vacuously. "No
surface was queued" is evidence only because the same channel, reached from the same
environment, really does queue one for a turn the agent side lost — which is why that
control is here beside it. The filter's contract and the member's *life* are two claims,
only the second of which is the incident, so an ordinary turn is also driven through a
whole `just orchestrate` launch whose monitor really takes them. And that same launch is
where the periodic `check-in` member's surfaces are proven unaffected rather than
asserted: its update reaches the queue in the same run whose monitor prose raises
nothing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, NotRequired, TypedDict

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

# The launch environment has one source and it is the module that owns the launch
# journeys: the fake provider, the guarded PATH, the alternate-identity indirections and
# the isolated state root. Copying its twenty lines here is how a journey comes to run
# against a seam the rest of the suite has moved off.
from test_orchestrate_launch_e2e import (
    LOST_TURN_CAUSE,
    MONITOR_MEMBER,
    PACEMAKER_MEMBER,
    SURFACE_KIND_OF_A_LOST_TURN,
    _lost_turn_transcript,
)
from test_orchestrate_launch_e2e import _environment as _launched_environment
from test_supervisory_prompt_discipline_e2e import CURSOR_FILE
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The filter this journey drives, at the interface `oneagentgraph` spawns it through.
CHANNEL_SERVE = REPO_ROOT / "scripts" / "channel-serve.py"

#: The seam that would let something other than the pinned `onepipeline` answer the
#: planner. Cleared rather than set: the published channel is the point of the journey.
ONEPIPELINE_BIN = "ONEPIPELINE_BIN"

#: The run id the environment names, cleared for the same reason. A `supervisor` frame
#: reads its run out of the composed task, and this suite runs from inside a dispatch
#: that carries a run id of its own — one this journey must not queue surfaces onto.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"

#: Where the published verb keeps every run's durable channel.
RUNS_DIR_ENV = "ONEPIPELINE_RUNS_DIR"

#: What the scripted monitor says on every turn of the launched run below. Ordinary
#: prose, deliberately: there is no sentinel and no fixed string on this path any more,
#: so what is under test is that *any* words at all keep the member watching and queue
#: nothing. It names no node and quotes no frame, so a surface carrying it could only
#: have come from the raise this change removed.
SAID_ON_A_QUIET_TURN = "read the detailed stream; nothing needed raising this turn"

#: What the answer to a taken turn has to say about the turn after it. The graph paces
#: the monitor's conversation with a hold between turns, so an acknowledgement that
#: told the member to keep reading *now* would be an instruction to spend the turn it
#: has just finished; what it says instead is that the next turn opens after the hold
#: and reads the stream from the cursor file the persona keeps — `CURSOR_FILE`, which
#: `tests/e2e/test_supervisory_prompt_discipline_e2e.py` reads off a real launch's
#: effective prompt, so the answer and the instruction it refers to are one name.
NEXT_TURN_OPENS_AFTER_THE_HOLD = "next turn opens after the graph's hold"

#: How long a turn that raises nothing may take to answer before this journey calls it
#: hung — every content-bearing turn, whatever the content is. Such a turn opens no
#: channel at all, so what this really bounds is the regression: a filter that raised a
#: surface instead would sit inside `channel serve` waiting out its whole reply window,
#: and that wait is what the failure would look like.
ANSWERED_WITHOUT_A_CHANNEL_SECONDS = 30


class QueuedSurface(TypedDict):
    """One planner surface as the run's own channel queue holds it.

    The three fields `scripts/channel-serve.py` composes and a manager reads. `channel
    serve` adds bookkeeping of its own — an id, a source — which is the published verb's
    to declare and no part of what is asserted here.
    """

    kind: str
    message: str
    blocking: bool
    id: NotRequired[str]
    source: NotRequired[str]


class EventLabels(TypedDict, total=False):
    """The additive labels record, narrowed to the member used by this journey."""

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


def _create_empty_run(tmp_path: Path, run: str) -> Path:
    """Create an empty runs root holding one empty run directory; return its queue path.

    Creating and nothing more, which is the whole point: the channel inside it is the
    published verb's to make, so a queue that exists afterwards was written by
    `onepipeline channel serve` having really been asked to raise something.
    """
    (tmp_path / "runs" / run).mkdir(parents=True)
    return tmp_path / "runs" / run / "channel" / "queue.json"


def _filter_environment(tmp_path: Path) -> dict[str, str]:
    """The environment the filter is spawned with, with this run's own channel in it."""
    environment = dict(os.environ)
    environment[RUNS_DIR_ENV] = str(tmp_path / "runs")
    environment.pop(ONEPIPELINE_BIN, None)
    environment.pop(RUN_ID_ENV, None)
    return environment


def _frame(run: str, said: str | None) -> str:
    """One supervisor frame ending in what the monitor said, or in nothing at all.

    `None` is the third case rather than an absence to tolerate: a conversation the
    monitor has not spoken in is what a turn its agent side lost leaves behind, and
    telling that from a turn that found nothing is the whole subject here.
    """
    spoken = [] if said is None else [{"role": "assistant", "content": said}]
    return json.dumps(
        {
            "op": "supervisor",
            "task": f"onepipeline run `{run}`.\n\nGoal: prove a quiet turn costs no surface",
            "messages": [{"role": "user", "content": f"onepipeline run `{run}`."}, *spoken],
        }
    )


def _answered_without_the_channel(
    tmp_path: Path, run: str, said: str | None
) -> subprocess.CompletedProcess[str]:
    """Put one frame to the real filter and wait for it to answer on its own.

    For the two cases that raise nothing: the filter is expected to decide and exit
    without ever opening the channel. A `TimeoutExpired` here is therefore a finding
    rather than a flake — it says the filter went to `channel serve` and is waiting out
    a reply window nobody will answer — so it is re-raised as that.
    """
    try:
        return subprocess.run(
            [str(CHANNEL_SERVE)],
            cwd=REPO_ROOT,
            env=_filter_environment(tmp_path),
            input=_frame(run, said),
            text=True,
            capture_output=True,
            timeout=e2e_timeout(ANSWERED_WITHOUT_A_CHANNEL_SECONDS),
            check=False,
        )
    except subprocess.TimeoutExpired as waited:
        raise AssertionError(
            f"the filter did not answer run {run} on its own within the wait, which is "
            "what raising a surface looks like from here: `channel serve` queues it and "
            "then blocks for a planner who is not coming"
        ) from waited


def _queued(queue: Path) -> list[QueuedSurface]:
    """Whatever the run's own channel is holding, and `[]` when it is holding nothing.

    A missing file and an empty `waiting` list are the same answer here — no surface was
    raised — and that is exactly what the two quiet cases assert.
    """
    try:
        # The queue is the published verb's own schema; only `waiting` is read, and each
        # entry is checked field by field by the assertions that consume it.
        decoded: Any = json.loads(queue.read_text(encoding="utf-8"))["waiting"]
    except (OSError, json.JSONDecodeError, KeyError):
        return []
    assert isinstance(decoded, list), decoded
    waiting: list[QueuedSurface] = []
    for entry in decoded:
        assert isinstance(entry, dict), entry
        assert isinstance(entry.get("kind"), str), entry
        assert isinstance(entry.get("message"), str), entry
        assert isinstance(entry.get("blocking"), bool), entry
        waiting.append(entry)
    return waiting


def _raised_to_the_real_channel(tmp_path: Path, run: str, said: str) -> QueuedSurface:
    """The one surface the real filter queued on this run's real channel.

    Spawned rather than run to completion, because a raised surface is non-blocking and
    `channel serve` then keeps running to wait for a planner: the surface is read off
    the queue while it waits. A half-written queue is read as no queue, since the
    published verb is the one writing this file and a partial read is a race.
    """
    serving = subprocess.Popen(
        [str(CHANNEL_SERVE)],
        cwd=REPO_ROOT,
        env=_filter_environment(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    queue = tmp_path / "runs" / run / "channel" / "queue.json"
    waiting: list[QueuedSurface] = []
    try:
        assert serving.stdin is not None
        serving.stdin.write(_frame(run, said))
        serving.stdin.close()
        limit = deadline(60)
        while True:
            assert serving.poll() is None, (
                f"the filter exited before raising a surface: {serving.communicate()}"
            )
            assert time.monotonic() < limit, f"no surface reached {queue} within the wait"
            waiting = _queued(queue)
            if waiting:
                break
            time.sleep(0.05)
    finally:
        # Nobody here is the planner, so `channel serve` is now waiting out its own
        # reply window. This journey has what it came to read.
        serving.terminate()
        serving.wait(timeout=e2e_timeout(30))

    assert len(waiting) == 1, waiting
    return waiting[0]


@pytest.mark.parametrize(
    ("run", "case", "said"),
    [
        ("turn-with-nothing-to-report", "a turn that found nothing", SAID_ON_A_QUIET_TURN),
        (
            "turn-with-an-observation",
            "an observation written as prose",
            "issue: node api has drifted from its acceptance criteria",
        ),
        (
            "turn-with-an-unprovable-transcript",
            "a machine transcript no failure can be proven inside",
            '{"result": {"codexHome": "/home/nick/.codex"}}\n'
            '{"method": "turn/completed", "params": {"turn": {"status": "completed"}}}',
        ),
    ],
)
def test_a_monitor_turn_that_produced_content_costs_the_planner_no_surface(
    tmp_path: Path, run: str, case: str, said: str
) -> None:
    """Content is content: the member is answered, it lives, and nothing is queued.

    Three things have to be true at once and none is enough alone. Nothing may reach the
    planner's queue — an observation belongs on it as a `finding` op the monitor issues
    itself, and raising the prose beside one is what made 30% of a run's monitor surfaces
    duplicates of a finding seconds older. onejudge has to be handed a ruling it can act
    on, because the alternative is the exit status that killed this host's monitors: a
    non-completion settles nothing, rules on no work, and leaves the member watching. And
    what the monitor said may not come back inside that ruling, because a message
    composed from the monitor's own words is a republication by another route.

    The three cases are the three shapes a turn's content arrives in, and the point of
    driving all of them is that the filter no longer branches between them at all: a
    quiet turn, an observation, and an unprovable machine transcript are one answer now.
    The last one used to earn a bounded surface of its own, which existed only to keep
    republished prose readable and went with the republication.
    """
    queue = _create_empty_run(tmp_path, run)

    answered = _answered_without_the_channel(tmp_path, run, said)

    assert answered.returncode == 0, f"{case}: {answered.stderr}"
    assert _queued(queue) == [], (
        f"{case} put a surface on the planner's queue, which is the update a monitor's "
        f"report is supposed to arrive as exactly once: {_queued(queue)}"
    )
    ruling = json.loads(answered.stdout)
    assert ruling["completion"] is False, (
        f"{case} answered onejudge with a completion, which settles a watch nobody ruled "
        f"on: {ruling}"
    )
    assert said not in ruling["message"], (
        f"{case} came back to the monitor inside its own ruling: {ruling}"
    )
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
    assert f"`{CURSOR_FILE}`" in ruling["message"], (
        f"{case} was answered without telling the monitor to read its next turn from "
        f"`{CURSOR_FILE}`, the cursor the persona keeps: {ruling}"
    )


def test_a_monitor_turn_its_agent_side_lost_still_reaches_the_planners_queue(
    tmp_path: Path,
) -> None:
    """The one surface left on this path, and the control for every assertion above.

    "Nothing was queued" is evidence only because the same filter, the same environment
    and the same published channel really do queue a surface here. This is also the half
    of the transcript machinery that stays: a turn the agent side lost writes the
    harness's own stream where its words go, so reading that as content would let a
    monitor whose agent side is failing look healthy for the rest of the run. It is the
    filter reporting on the member rather than the monitor reporting on the run, which is
    why it survives a change that took away every other raise.
    """
    run = "lost-turn"
    _create_empty_run(tmp_path, run)

    raised = _raised_to_the_real_channel(tmp_path, run, _lost_turn_transcript("/home/nick/.codex"))

    assert raised["kind"] == SURFACE_KIND_OF_A_LOST_TURN, raised
    assert raised["blocking"] is False, (
        f"a lost turn was raised as a blocking surface, which parks the run at "
        f"`awaiting-planner` to ask about watching rather than about work: {raised}"
    )
    assert raised["message"].startswith(f"monitor turn failed: {LOST_TURN_CAUSE} on codex."), (
        f"a lost turn did not reach the planner as a named failure: {raised['message']!r}"
    )


def test_a_turn_carrying_no_assistant_content_is_reported_as_a_failure(
    tmp_path: Path,
) -> None:
    """The other answer, kept a failure, and readable as the provider defect it is.

    A turn with no assistant content at all is a real provider defect worth reporting,
    and it is precisely the case a turn that merely found nothing must not be mistaken
    for. So it is still refused, still without a fabricated verdict onejudge could act
    on — and its refusal says what the other answer is, so an operator meeting it can
    tell the two apart at the point of refusal instead of by going and reading the
    filter.
    """
    run = "empty-turn"
    queue = _create_empty_run(tmp_path, run)

    refused = _answered_without_the_channel(tmp_path, run, None)

    assert refused.returncode != 0, f"an empty turn was served anyway: {refused.stdout}"
    assert "said nothing" in refused.stderr, refused.stderr
    assert "still takes its turn and says something" in refused.stderr, (
        "the refusal does not say what a monitor with no finding does instead, so a turn "
        f"that found nothing and a turn the harness dropped read as one failure: "
        f"{refused.stderr}"
    )
    assert "completion" not in refused.stdout, (
        f"an empty turn produced a verdict onejudge would act on: {refused.stdout}"
    )
    assert _queued(queue) == [], f"an empty turn still reached the planner: {_queued(queue)}"


#: The plan the launch below runs, and the run id `onepipeline` mints from its name. One
#: node, held open just long enough for the monitor to take turns against it: what is
#: under test is the observer graph every launch attaches, not anything the node does.
LAUNCHED_RUN = "monitor-quiet-turn"
HELD_NODE = "held"
HELD_SECONDS = 25

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

#: How many agent turns the monitor has to have taken for its survival to mean anything.
#: Two, and the second is the whole point: a turn only follows a turn its judge side
#: answered with a ruling onejudge could act on, so a member that took another one is a
#: member a quiet turn kept alive rather than one that never got going.
TURNS_PROVING_IT_KEPT_WATCHING = 2


def _launch_environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment one real launch runs under, with its monitor scripted quiet.

    The launch half of this journey needs the one thing the filter half cannot reach: a
    monitor whose *own words* are an ordinary turn with no finding in it. Those words are
    the paid model's, and the paid model is the only thing doubled here — so the stand-in
    is told what to answer for that member alone, and every other turn of the run stays
    what it was.
    """
    environment = _launched_environment(tmp_path, oneharness_bin)
    # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
    environment[OBSERVER_ANSWER_ENV] = SAID_ON_A_QUIET_TURN
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

#: The `onepipeline` this checkout pins, resolved the way `scripts/channel-serve.py`
#: resolves it: a judge command runs with whatever PATH the graph inherited, so a bare
#: name would let the ambient environment decide which release answers.
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
        # `oneagentgraph` owns this schema; only `kind` and `labels.member` are read.
        # llmlint: ignore[tests_mirror_real_usage] The rendered line omits the member.
        for line in log.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            # `Any` because this is `oneagentgraph`'s wire format rather than this
            # repository's: `json.loads` answers `Any`, `GraphEvent` is a `TypedDict` no
            # runtime check can establish, and the producer owns every field. So each
            # one is narrowed by the assertions below before it is read.
            decoded: Any = json.loads(line)
            assert isinstance(decoded, dict), decoded
            kind = decoded.get("kind")
            labels = decoded.get("labels", {})
            assert isinstance(kind, str), decoded
            assert isinstance(labels, dict), decoded
            member = labels.get("member")
            assert member is None or isinstance(member, str), decoded
            events.append(decoded)
    return events


#: The event a member's own words arrive on, and the role that makes them the member's
#: rather than its supervisor's.
TURN_MESSAGE = "turn-message"
SPOKEN_BY_THE_MEMBER = "assistant"


def _what_the_monitor_said(events: list[GraphEvent]) -> list[str]:
    """Every word the monitor's agent side actually produced, from the graph's own record.

    Read here rather than off the stand-in's prompt log, and the difference is not
    academic: that log records the answer the stand-in was *told* to give, and a branch
    that answered before the script was consulted left every such entry recording a
    script the model never said. `turn-message` is the graph's own record of what came
    back, so it is the only witness that the scripted turn is the turn that happened.
    """
    return [
        payload["text"]
        for event in events
        if event.get("kind") == TURN_MESSAGE
        and event.get("labels", {}).get("member") == MONITOR_MEMBER
        and isinstance(payload := event.get("payload"), dict)
        and payload.get("role") == SPOKEN_BY_THE_MEMBER
        and isinstance(payload.get("text"), str)
    ]


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

    The channel is read too, and for the reason the whole change exists: surviving while
    queueing that prose onto the planner's queue would keep the member alive and still
    spend the update the `finding` op is supposed to be the only thing spending. And the
    periodic member's own route is driven against the same run's channel afterwards,
    which is what makes "nothing was queued" a statement about the monitor rather than
    about a run whose channel nothing could reach.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _launch_environment(tmp_path, oneharness_bin)
    scratch = Path(environment[GRAPH_STATE_ENV])
    plan = tmp_path / "quiet.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": LAUNCHED_RUN,
                "goal": {"text": "prove a quiet monitor outlives the run it watches"},
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
    printed = tmp_path / "launch.log"
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
            subprocess.run(
                ["just", "stop", LAUNCHED_RUN],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=e2e_timeout(60),
                check=False,
            )

    events = _graph_events(scratch)
    assert events, (
        f"the dag-scope graph recorded nothing for this run, so the absence of a "
        f"`{MEMBER_DIED}` below proves nothing:\n{printed.read_text('utf-8')}"
    )
    died = [
        event
        for event in events
        if event.get("kind") == MEMBER_DIED
        and event.get("labels", {}).get("member") == MONITOR_MEMBER
    ]
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
        f"{printed.read_text('utf-8')}"
    )
    # What the monitor SAID, out of the graph's own record rather than out of the
    # stand-in's prompt log — the log records what the stand-in was told to answer, and
    # a branch answering before the script was consulted once left every entry recording
    # a script the model never said. So survival is measured against a run whose monitor
    # really took quiet turns rather than against the stand-in's default prose.
    said_by_the_monitor = _what_the_monitor_said(events)
    assert len(said_by_the_monitor) >= TURNS_PROVING_IT_KEPT_WATCHING, (
        f"the monitor produced {len(said_by_the_monitor)} message(s), so this run cannot "
        f"show that a quiet turn was answered at all: {said_by_the_monitor}"
    )
    assert all(spoken == SAID_ON_A_QUIET_TURN for spoken in said_by_the_monitor), (
        "the monitor said something other than the quiet turn this run scripted, so "
        f"survival was measured against another answer: {said_by_the_monitor}"
    )
    queued = _queued(tmp_path / "runs" / LAUNCHED_RUN / "channel" / "queue.json")
    # By the monitor's own WORDS rather than by kind: `monitor` is also what the engine
    # queues a single-sided member's proposal under, and this run's pacemaker raises one
    # — so a kind filter would fail on a surface the monitor had nothing to do with.
    said = [one for one in queued if str(one.get("message", "")).strip() == SAID_ON_A_QUIET_TURN]
    assert not said, (
        "a monitor's own prose still reached the planner's queue, which is the raise "
        f"this change removed and the route a finding is supposed to own: {queued}"
    )
    # And the periodic member's own route still reaches that queue, driven in the SAME
    # run rather than asserted. `graphs/dag-scope.yaml`'s `check-in` member is
    # single-sided — `kind: oneharness`, with no judge side — so it never reaches
    # `scripts/channel-serve.py` at all: its task tells it to raise its update with
    # `onepipeline surface`, and that verb is what is run here, under the kind read out
    # of the shipped task rather than retyped. Its schedule is half an hour and this run
    # is under a minute, so the member does not come due inside it; what is under test
    # here is the route, and `tests/e2e/test_supervisory_prompt_discipline_e2e.py` is
    # what reads the turn that takes it.
    kind = _pacemaker_surface_kind()
    raised = _raised_by_the_pacemakers_own_verb(environment, LAUNCHED_RUN, kind)
    assert raised.returncode == 0, (
        f"the `{PACEMAKER_MEMBER}` member's own verb was refused on a run whose monitor "
        f"prose raised nothing: {raised.stderr}"
    )
    paced = _queued(tmp_path / "runs" / LAUNCHED_RUN / "channel" / "queue.json")
    assert [one for one in paced if one.get("message", "").strip() == PACEMAKER_REPORT] == [
        one for one in paced
    ], (
        f"the `{PACEMAKER_MEMBER}` member's update did not reach the planner's queue, or "
        f"did not reach it alone, in a run whose monitor prose raised nothing: {paced}"
    )
    assert paced[0]["kind"] == kind and paced[0].get("source") == kind, (
        f"the `{PACEMAKER_MEMBER}` member's update no longer carries its own kind and "
        f"source, which is what keeps it clear of every kind the filter raises: {paced}"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[shell_test_tiers_stay_split]
