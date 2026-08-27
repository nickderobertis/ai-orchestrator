"""A monitor that found nothing, driven through the real filter and the real channel.

Its persona tells `graphs/dag-scope.yaml`'s monitor to spend no planner surface on a turn
with no finding in it, and obeying that used to be fatal on the first quiet turn — see
`scripts/channel-serve.py`'s header for the incident. A monitor now *says* its silence,
and the three answers a turn can end in are each driven here at the interface
`oneagentgraph` spawns the filter through, against the pinned `onepipeline`'s own
published `channel serve`. No stand-in channel anywhere: all this journey provides is an
empty directory named after a run, so what was and was not queued is read out of
`runs/<run-id>/channel/queue.json` where a manager reads one.

Two things make the negative half mean something rather than pass vacuously. "No surface
was queued" is evidence only because the same channel, reached from the same environment,
really does queue one for the prose case — which is why the control is here beside it.
And the filter's contract and the member's *life* are two claims, only the second of
which is the incident, so the sentinel is also driven through a whole `just orchestrate`
launch whose monitor really answers it.
"""

from __future__ import annotations

import json
import os
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
from test_orchestrate_launch_e2e import MONITOR_MEMBER, SURFACE_KIND_OF_A_MONITOR
from test_orchestrate_launch_e2e import _environment as _launched_environment
from test_supervisory_prompt_discipline_e2e import FOUND_NOTHING_SENTINEL
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

#: What the monitor says when it looked and found nothing. Stated here rather than
#: imported from the filter: this is the word `personas/orchestrator.yaml` puts in front
#: of a paid model, and a journey that read the filter's own constant would keep passing
#: through a rename that left the persona telling the model something else.
FOUND_NOTHING = FOUND_NOTHING_SENTINEL

#: How long a quiet turn may take to answer before this journey calls it hung. A quiet
#: turn opens no channel at all, so what this really bounds is the regression: a filter
#: that raised a surface instead would sit inside `channel serve` waiting out its whole
#: reply window, and that wait is what the failure would look like.
QUIET_TURN_SECONDS = 30


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


class GraphEvent(TypedDict, total=False):
    """The additive graph event, narrowed to the fields this journey reads."""

    kind: str
    labels: EventLabels


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
            timeout=e2e_timeout(QUIET_TURN_SECONDS),
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
        ("quiet-turn-as-spelled", "the sentinel as the persona spells it", FOUND_NOTHING),
        ("quiet-turn-decorated", "the sentinel a model decorated", f"**{FOUND_NOTHING.title()}.**"),
    ],
)
def test_a_monitor_that_found_nothing_costs_the_planner_no_surface(
    tmp_path: Path, run: str, case: str, said: str
) -> None:
    """The sentinel is a report, so it is answered here and nothing is queued anywhere.

    Two things have to be true at once and neither is enough alone. Nothing may reach
    the planner's queue — raising "nothing to report" as a surface would cost exactly the
    update the persona's quiet-turn rule exists to spare them, and bury the blocking
    questions sharing that queue behind it. And onejudge has to be handed a ruling it can
    act on, because the alternative is the exit status that killed this host's monitors:
    a non-completion settles nothing, rules on no work, and leaves the member watching.

    The decorated spelling is driven rather than assumed. A model asked for one bare line
    of capitals writes it in bold with a full stop after it, and a match that forgave
    nothing would put that turn straight back into the refusal below.
    """
    queue = _create_empty_run(tmp_path, run)

    answered = _answered_without_the_channel(tmp_path, run, said)

    assert answered.returncode == 0, f"{case}: {answered.stderr}"
    assert _queued(queue) == [], (
        f"{case} put a surface on the planner's queue, which is the update the monitor's "
        f"quiet-turn rule exists to spare them: {_queued(queue)}"
    )
    ruling = json.loads(answered.stdout)
    assert ruling["completion"] is False, (
        f"{case} answered onejudge with a completion, which settles a watch nobody ruled "
        f"on: {ruling}"
    )
    assert FOUND_NOTHING in ruling["message"], (
        f"{case} was answered without quoting the sentinel back, so a monitor whose "
        f"message was nearly it cannot tell which reading it got: {ruling}"
    )
    assert ruling["reason"].strip(), (
        f"{case} answered with a bare non-completion, which reads in a transcript like a "
        f"planner who refused the watch: {ruling}"
    )


@pytest.mark.parametrize(
    ("run", "case", "said"),
    [
        (
            "prose-turn-observation",
            "an observation",
            "issue: node api has drifted from its acceptance criteria",
        ),
        (
            "prose-turn-beside-the-sentinel",
            "a finding written beside the sentinel",
            f"{FOUND_NOTHING} — except node api drifted",
        ),
    ],
)
def test_a_monitor_that_found_something_still_reaches_the_planners_queue(
    tmp_path: Path, run: str, case: str, said: str
) -> None:
    """Prose is untouched, which is what makes the quiet answer above safe to ship.

    This is the control for that one: "nothing was queued" is only evidence because the
    same filter, the same environment, and the same published channel really do queue a
    surface here. It is also the boundary the sentinel is held to — a turn that raised a
    finding *and* wrote the sentinel has raised a finding, and swallowing it would lose
    the observation this member exists to produce.
    """
    _create_empty_run(tmp_path, run)

    raised = _raised_to_the_real_channel(tmp_path, run, said)

    assert raised["kind"] == SURFACE_KIND_OF_A_MONITOR, raised
    assert raised["blocking"] is False, (
        f"{case} was raised as a blocking surface, which parks the run at "
        f"`awaiting-planner` to ask about watching rather than about work: {raised}"
    )
    assert raised["message"] == said, (
        f"{case} did not reach the planner as the monitor's own words: {raised['message']!r}"
    )


def test_a_turn_carrying_no_assistant_content_still_fails_and_names_the_sentinel(
    tmp_path: Path,
) -> None:
    """The third answer, kept a failure — and made readable as a different one.

    A turn with no assistant content at all is a real provider defect worth reporting,
    and it is precisely the case the sentinel exists to stop being mistaken for. So it is
    still refused, still without a fabricated verdict onejudge could act on — and its
    refusal names the sentinel, so an operator meeting it can tell the two apart at the
    point of refusal instead of by going and reading the filter.
    """
    run = "empty-turn"
    queue = _create_empty_run(tmp_path, run)

    refused = _answered_without_the_channel(tmp_path, run, None)

    assert refused.returncode != 0, f"an empty turn was served anyway: {refused.stdout}"
    assert "said nothing" in refused.stderr, refused.stderr
    assert FOUND_NOTHING in refused.stderr, (
        "the refusal does not name the sentinel, so a turn that found nothing and a turn "
        f"the harness dropped read as one failure: {refused.stderr}"
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

#: Where `oneagentgraph` writes its own event log, named by this journey so it reads
#: this run's graph and never a concurrent dispatch's. The rendered event line does not
#: carry the member, and which member died is the whole question.
GRAPH_STATE_ENV = "ONEAGENTGRAPH_STATE_DIR"
MEMBER_DIED = "member-died"

#: How many agent turns the monitor has to have taken for its survival to mean anything.
#: Two, and the second is the whole point: a turn only follows a turn its judge side
#: answered with a ruling onejudge could act on, so a member that took another one is a
#: member the sentinel kept alive rather than one that never got going.
TURNS_PROVING_IT_KEPT_WATCHING = 2


def _launch_environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment one real launch runs under, with its monitor scripted quiet.

    The launch half of this journey needs the one thing the filter half cannot reach:
    a monitor whose *own words* are the sentinel. Those words are the paid model's, and
    the paid model is the only thing doubled here — so the stand-in is told what to
    answer for that member alone, and every other turn of the run stays what it was.
    """
    environment = _launched_environment(tmp_path, oneharness_bin)
    # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
    environment[OBSERVER_ANSWER_ENV] = FOUND_NOTHING
    environment[OBSERVER_MEMBER_ENV] = MONITOR_MEMBER
    # Holds the dispatched worker's turn so the run outlives the monitor's first few.
    # The stand-in delays that side alone, so the watch is not slowed with it.
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    environment[GRAPH_STATE_ENV] = str(tmp_path / "graph-state")
    environment[PROMPT_LOG_ENV] = str(tmp_path / "prompts.jsonl")
    return environment


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


@pytest.mark.xdist_group("monitor-quiet-turn")
def test_a_monitor_that_answers_the_sentinel_survives_a_whole_real_run(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The incident, reproduced and then not reproduced, at the scale it happened.

    `spanish-language-tutor-upgrade` lost its observer five minutes into a two-hour run
    because its monitor did what its persona told it to. Nothing announced that: a run
    with no observer reports plain `ACTIVE`, so the only place the loss is visible is
    `oneagentgraph`'s own `member-died` record — which is what this reads, out of the
    graph state this launch names, after driving a real run to settlement with a monitor
    that answers the sentinel on every turn it takes.

    The channel is read too, and for the reason the whole change exists: surviving while
    queueing "nothing to report" onto the planner's queue would keep the member alive and
    still spend the update its silence is supposed to spare them.
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
        f"the `{MONITOR_MEMBER}` member did not survive a run it answered the sentinel "
        f"on; the graph recorded {json.dumps(died)}. The run goes on being driven and "
        "reporting `ACTIVE` after that, with nothing watching it and nothing announcing "
        "the loss."
    )
    # llmlint: ignore[tests_mirror_real_usage] A second paid-model turn is the observable
    # proof that onejudge acted on the first ruling; no run view exposes member turns.
    turns = _monitor_prompts(Path(environment[PROMPT_LOG_ENV]))
    assert len(turns) >= TURNS_PROVING_IT_KEPT_WATCHING, (
        f"the `{MONITOR_MEMBER}` member took {len(turns)} agent turn(s), so this run "
        "cannot show that a sentinel turn was answered with a ruling onejudge could act "
        f"on — a member that never took a second turn is not one that kept watching:\n"
        f"{printed.read_text('utf-8')}"
    )
    # llmlint: ignore[tests_mirror_real_usage] This records only the substituted paid
    # model answer, proving the survival assertion exercised the sentinel rather than
    # the fake provider's default prose; the filter and channel remain real.
    scripted = [
        json.loads(line)
        for line in Path(environment[PROMPT_LOG_ENV]).read_text("utf-8").splitlines()
        if line.strip() and json.loads(line).get("scripted_answer") is not None
    ]
    assert len(scripted) >= TURNS_PROVING_IT_KEPT_WATCHING, (
        f"the fake provider scripted only {len(scripted)} monitor turn(s), so survival "
        f"could have been measured against its default prose instead: {scripted}"
    )
    assert all(
        entry["scripted_answer"] == FOUND_NOTHING
        and f"/members/{MONITOR_MEMBER}/" in entry["config"]
        for entry in scripted
    ), scripted
    queued = _queued(tmp_path / "runs" / LAUNCHED_RUN / "channel" / "queue.json")
    # By the monitor's own WORDS rather than by kind: `monitor` is also what the engine
    # queues a single-sided member's proposal under, and this run's pacemaker raises one
    # — so a kind filter would fail on a surface the monitor had nothing to do with.
    # What must never be here is the sentinel itself, raised as though it were a finding.
    said = [one for one in queued if str(one.get("message", "")).strip() == FOUND_NOTHING]
    assert not said, (
        "a quiet monitor still put its own silence on the planner's queue, which is the "
        f"update that silence is meant to spare them: {queued}"
    )
