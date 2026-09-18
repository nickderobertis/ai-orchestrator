"""The run's monitor lives through everything the planner channel hands it, and is scored by it.

`graphs/dag-scope.yaml`'s monitor is the thing that notices a run going wrong, and its judge
side is the planner channel: `onemessagebus serve surfaces --codec monitor`, the binding
this host declares in `config/onemessagebus.yaml`. It had two ways of dying on that side —
both of which leave the run reporting `ACTIVE` with nobody watching, because nothing
announces the loss:

1. **A question the channel cannot answer.** onejudge asks its judge side other ops at the
   end of a conversation — `assess` for an `assessment`, `judge` for each `evals` criterion
   *and* for `user.done_when` — and the binding serves only a boolean `judge`, refusing
   the rest, after which `oneagentgraph` kills the member. Which keys produce which op
   is a declaration, and `tests/test_planner_channel_personas.py` holds it. What a
   declaration cannot say is whether the merged configuration a **launch** composes still
   carries one, or whether the member actually lives to the end.
2. **An answer addressed to somebody else.** Through onepipeline 0.8.x a reply went to
   whichever reader of the channel arrived first, so a manager's live graph edit reached the
   monitor's judge side whenever it got there first. Forty of this host's recorded dag-scope
   runs died on it, at the worst possible timing: it fired precisely while a manager was
   supervising, because the manager's own correction was what killed the watcher. `just
   channel-reply` now sends a commands-only envelope to the engine's command path, and the
   bus routes a reply by the halves it carries.

So this launches one real run and plays a manager who answers **only** with live edits, which
is the reproduction rather than a stand-in for it: the real recipe, the real engine, the real
graph, the real judge side, and the real channel. Nothing is substituted but the paid model,
at the `oneharness` seam every other journey here substitutes it at. The run is then driven to
settlement and the whole of it is judged at once — the effective config the member was
launched with, what the monitor was and was not handed, the edits the engine got, and the
graph's own record of how each member ended.

And the bar the member cannot decline is served rather than survived: a second launch lets
the monitor's conversation end, and a manager scores it through `just channel-reply
--correlation`, the way one answers any question on this channel. The score the member is
settled with is read back off the graph's own record, so what is proven is that the ruling
the manager typed is the one onejudge recorded.
"""

# The finding these answer is about which Nx project owns this file, so it is the file
# that is suppressed and a project split that would resolve it.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-block[shell_test_tiers_stay_split] see above; and this is a pytest
# journey over the real recipe, not a shell test suite.

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
from fake_backend import AGENT_DELAY_ENV, PROMPT_LOG_ENV, RecordedTurn
from planner_channel import reply as channel_reply
from project_fixtures import project_from_plan
from shared_dispatch_bar import shared_completion_bar

# The launch environment has one source and it is the module that owns the launch
# journeys. Copying its twenty lines here is how a journey comes to run against a
# seam the rest of the suite has moved off — the fake provider, the guarded PATH, the
# alternate-identity indirections — so it is imported rather than restated.
from test_orchestrate_launch_e2e import (
    CONVERSATION_ENDS_AT_ITS_CAP,
    DAG_SCOPE_STREAM,
    MONITOR_MEMBER,
    PACEMAKER_INTERVAL_SECONDS,
    SURFACE_KIND_OF_A_COMPLETION_SCORE,
    _environment,
    _just,
    _queue_state,
)
from waits import deadline, until
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The run this journey launches, and the one node it holds open. The node is held so
#: the run stays alive long enough for the monitor to take several turns and for a
#: manager's edit to reach it; it settles on its own afterwards, which is what gives
#: this journey a settlement to watch the member survive to.
RUN = "monitor-survives-the-channel"
HELD_NODE = "held"
HELD_SECONDS = 90

#: The hang guard on each of this journey's waits, and never its stopping condition —
#: `SustainedEditing` below is that. Above what a healthy run costs, which is
#: `HELD_SECONDS` plus the seconds around it, and below what reads as a wedged tier.
SUPERVISED_SECONDS = 300

#: How long the launch tells the graph to hold the monitor between its turns. The
#: shipped `graphs/dag-scope.yaml` paces that conversation one turn per 300 seconds, and
#: the claim below is counted in monitor turns taken *while edits are being sent* — so
#: under the shipped period the twenty it needs would take longer than the run lives,
#: and a watcher that was never handed a draw would read as one that survived them.
#: `--set members.monitor.schedule.every` is the published override for a journey that
#: needs turns closer together than the shipped period; the shipped value stays what it
#: is. One second is the smallest hold the graph accepts, and this journey measures how
#: many turns were taken alongside the edits rather than how far apart they were.
MONITOR_HOLD_SECONDS = 1

#: What has to have happened before this journey stops editing, and why these two.
#:
#: **One mis-routed envelope is all it takes.** The failure guarded here reaches the
#: monitor the first time its conversation is handed a manager's edit. So the question is
#: never how many draws make a sound sample — it is whether there were draws at all: an
#: edit sent while the monitor is a live member taking turns. An edit sent to a monitor
#: that had already stopped taking turns is not a draw, and monitor turns taken while
#: nothing was being sent are not draws either, which is why neither half stands alone.
#:
#: 40 accepted edits, spanning at least 20 further monitor turns, is roughly half a
#: minute of continuous editing against a demonstrably live watcher — two orders of
#: margin over the one mis-route it takes. Both are counts of **work done** rather than
#: of elapsed time, deliberately: a loaded host reaches them later rather than reaching
#: them with less of the claim tested.
EDITS_TO_FALSIFY = 40
MONITOR_TURNS_ALONGSIDE = 20

#: How every node this manager adds is named. Distinctive, because an edit that reached
#: the monitor's conversation carries it into the prompt the monitor is given next.
EDITED_NODE = "noted-by-the-manager-"

#: What the member's own effective onejudge config may not carry, against the written
#: value that says it is not carried. `oneagentgraph` composes it from
#: `config/onejudge.base.yaml` ⊕ `personas/orchestrator.yaml` and writes it into the
#: member's scratch, which is the only place the *merged* answer exists — the two files
#: it is merged from each say half of it.
#:
#: Per key, because the two are not unset by the same word: `assessment` is free text
#: and unsets as `null`, while `evals` is a sequence `oneagentgraph` refuses a null one
#: of, so the empty list unsets that one. `tests/test_planner_channel_personas.py`
#: reconciles these with the spellings that file advises a persona to write, so this
#: half cannot start refusing what the other half asks for.
DECLINED = {"assessment": ("null",), "evals": ("[]",)}

#: And the one it always carries however it is declared, which is why the binding serves
#: the op that scores it. Asserted PRESENT deliberately; see the test below.
UNDECLINABLE = "done_when:"

#: Where `oneagentgraph` writes each member's effective config and its own event log,
#: named by this journey so it reads this run's graph and never a concurrent dispatch's.
GRAPH_STATE_ENV = "ONEAGENTGRAPH_STATE_DIR"

#: The graph event that says a member did not survive, and the label naming which one.
#: Read from the graph's own record because the rendered event line does not carry the
#: member, and which member died is the whole question here.
MEMBER_DIED = "member-died"
MEMBER_SETTLED = "member-settled"
MEMBER_LABEL = "member"

#: The one death a settled run's teardown can record, and how it is told apart from a
#: death this journey guards against. Once the run settles, the driver writes the
#: observer graph's `signals/stop`; `oneagentgraph` reads that file as a cancellation
#: request, and a member it reaches **inside** a turn rather than in its hold between
#: turns is recorded `member-died` with this cause. Under this journey's one-second
#: hold a turn is in flight at that moment often enough to fail a healthy run. Both
#: deaths guarded here are provider or protocol failures before settlement, and neither
#: carries this cause.
TEARDOWN_CAUSE = "cancelled"
STOP_SIGNAL = "signals/stop"

#: The rendered launch line that settles a node: `<ts>  graph:<node>  node-settled …`.
#: The last of them is when the run settled, which a teardown stop has to follow.
NODE_SETTLED = "node-settled"


class ReplyAnswer(NamedTuple):
    """One thing `just channel-reply` answered a live edit with.

    The status and the body are read apart and mean different things — whether the
    send was refused, and what the verb claimed it did — so neither is positional.
    """

    #: The verb's exit status; non-zero is a refusal, which this manager ignores.
    returncode: int
    #: Its stdout verbatim, which is the bus's one-line answer when it is zero.
    stdout: str


class Watched(NamedTuple):
    """One settled run whose manager only ever sent live edits, and its evidence."""

    #: The effective onejudge config the launch handed the monitor member.
    monitor_config: Path
    #: Every prompt the monitor's agent side was given, oldest first.
    monitor_prompts: list[str]
    #: Whether a manager's edit reached the monitor's conversation, which is what makes
    #: the rest meaningful.
    an_edit_reached_the_monitor: bool
    #: What `just channel-reply` answered each edit with, oldest first.
    reply_answers: list[ReplyAnswer]
    #: `just monitor --filter detailed` over the settled run.
    stream: str
    #: The graph's own events, which name the member behind each one. `Any` because
    #: `oneagentgraph` owns this envelope and this journey reads two keys out of it —
    #: restating the rest here would be a second copy of somebody else's schema.
    graph_events: list[dict[str, Any]]
    #: When the driver asked each observer graph to stop, keyed by the graph's stream,
    #: which is the directory `oneagentgraph` wrote that graph's state under.
    stop_requests: dict[str, datetime]
    #: How the attached launch ended, and what it printed on the way.
    settlement: str


class SustainedEditing:
    """Whether this run has given "the monitor survived sustained editing" a chance to fail.

    This is the journey's stopping condition, and it is answerable from the claim rather
    than from a clock or from the run's own lifetime. That last one is not a stylistic
    preference: the manager adds a node every half second, so the graph stays exactly one
    node short of complete and a run edited "for as long as there is a run to edit" can
    never settle — which is why the loop that waited on that launch could only hang.

    Stopping is therefore also what lets the run settle: the held node finishes, nothing
    is adding more, and the graph completes. The settlement the last case asserts
    survival to exists because of this.

    Turns are counted from the first **accepted** edit rather than from the launch,
    because turns taken before anything was sent say nothing about what happens when
    something is.
    """

    def __init__(self, manager: EditingManager, prompt_log: Path) -> None:
        self._manager = manager
        self._prompt_log = prompt_log
        self._turns_when_editing_began: int | None = None

    def _accepted(self) -> int:
        """Edits the reply verb took. A refused send reached no reader and is no draw."""
        return sum(1 for answered in self._manager.answers if answered.returncode == 0)

    def observe(self) -> bool:
        """Take a reading, and answer whether both halves have happened yet.

        A reading rather than a predicate, and named for it: the first one that finds an
        accepted edit is what fixes the baseline the monitor turns are counted from, so
        calling this is what makes the second half measurable at all.
        """
        accepted, turns = self._accepted(), len(_monitor_prompts(self._prompt_log))
        if accepted and self._turns_when_editing_began is None:
            self._turns_when_editing_began = turns
        if self._turns_when_editing_began is None:
            return False
        return (
            accepted >= EDITS_TO_FALSIFY
            and turns - self._turns_when_editing_began >= MONITOR_TURNS_ALONGSIDE
        )

    def so_far(self) -> str:
        """Both counts against what they have to reach, for a wait that gave up."""
        turns = len(_monitor_prompts(self._prompt_log))
        alongside = (
            "none yet — nothing has been accepted"
            if self._turns_when_editing_began is None
            else f"{turns - self._turns_when_editing_began}/{MONITOR_TURNS_ALONGSIDE}"
        )
        return (
            f"{self._accepted()}/{EDITS_TO_FALSIFY} edit(s) accepted and {alongside} "
            f"monitor turn(s) taken alongside them"
        )


def _edit_reached_the_monitor(prompt_log: Path) -> bool:
    """Whether any prompt the monitor was given carries one of the manager's edits."""
    return any(EDITED_NODE in prompt for prompt in _monitor_prompts(prompt_log))


def _supervision_state(
    launch: subprocess.Popen[str],
    prompt_log: Path,
    manager: EditingManager,
    editing: SustainedEditing | None = None,
) -> str:
    """What the run had got to, for a wait that gave up on it.

    Every party of the round trip, because which one stopped is the whole diagnosis and
    from a bare timeout none of them is visible: the launch that should have ended, the
    member that should have kept taking turns, and the manager that should have been
    sending the edits.
    """
    ended = launch.poll()
    prompts = _monitor_prompts(prompt_log)
    answers = manager.answers
    last = f"exited {answers[-1].returncode}" if answers else "sent nothing"
    covered = "" if editing is None else f"; {editing.so_far()}"
    return (
        f"the launch is {'still running' if ended is None else f'over, exit {ended}'}; "
        f"the `{MONITOR_MEMBER}` member has been given {len(prompts)} prompt(s); the "
        f"manager sent {len(answers)} live edit(s) and the last {last}{covered}"
    )


def _recorded_turn(line: str) -> RecordedTurn | None:
    """One recorded turn, or `None` for a line the backend has not finished flushing.

    The backend appends while `until` polls, so a read can land mid-flush and see a
    record cut in half. Dropping that line beats raising on it: the next poll sees the
    same record whole.
    """
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    # `json.loads` answers `Any`, and the shape is the single `json.dumps` of the literal
    # in `fake_backend.py` that wrote the line.
    return cast("RecordedTurn", record)


def _monitor_prompts(prompt_log: Path) -> list[str]:
    """Every prompt the monitor's AGENT side has been given so far, oldest first.

    Tolerates the log not existing, which is the ordinary state for the first moments
    of a launch rather than a failure to report. The agent side is the turn pinned to
    the member's own harness config; its judge side is a command and takes no turn.
    """
    if not prompt_log.exists():
        return []
    # The member's NEXT PROMPT is the only place what it was handed is observable. An
    # operator view reports that a member is alive, and reports nothing about what it was
    # given — so an event label would pass for a monitor that was handed a manager's edit.
    # llmlint: ignore[tests_mirror_real_usage] No operator view carries a turn's prompt.
    recorded = [
        record
        for line in prompt_log.read_text("utf-8").splitlines()
        if (record := _recorded_turn(line))
    ]
    return [
        turn["prompt"]
        for turn in recorded
        if f"/members/{MONITOR_MEMBER}/" in (turn.get("config") or "")
    ]


def _live_edit(sequence: int) -> str:
    """One reply envelope that is a graph edit and nothing else.

    Commands and no `completion`, which is what makes it the engine's command path's
    rather than any question's. `add` of a node that settles without a dispatch, so each
    one is genuinely applied and genuinely observable, and unique per send because the
    same id twice is refused the second time.
    """
    return json.dumps(
        {
            "version": 2,
            "author": "planner",
            "commands": [
                {
                    "op": "add",
                    "node": {
                        "id": f"{EDITED_NODE}{sequence}",
                        "task": "Report.",
                        "expects_no_diff": True,
                    },
                }
            ],
        }
    )


class EditingManager:
    """A manager who answers this run only with live graph edits, and never a verdict.

    Deliberately not `tests/e2e/planner_channel.py`'s manager, which answers questions:
    the failure under test is what happens when a manager *corrects the graph* instead
    of answering, so this one never sends a `completion` at all. Refusals are ignored on
    purpose, so a transient refusal is read as a send that was not a draw rather than as
    a failing manager.
    """

    def __init__(self, environment: dict[str, str]) -> None:
        self._environment = environment
        self._stopping = threading.Event()
        self._sent = 0
        self._answers: list[ReplyAnswer] = []
        self._thread = threading.Thread(target=self._edit, daemon=True)
        self._thread.start()

    #: How long to leave between edits. A monitor's turn lasts seconds, so this is still
    #: an edit inside every window there is to arrive in, without adding thousands of
    #: nodes to a three-minute run.
    INTERVAL_SECONDS = 0.5

    def _edit(self) -> None:
        while not self._stopping.is_set():
            self._sent += 1
            answered = subprocess.run(
                ["just", "channel-reply", RUN],
                cwd=REPO_ROOT,
                env=self._environment,
                input=_live_edit(self._sent),
                text=True,
                capture_output=True,
                timeout=e2e_timeout(120),
                check=False,
            )
            self._answers.append(
                ReplyAnswer(returncode=answered.returncode, stdout=answered.stdout)
            )
            self._stopping.wait(self.INTERVAL_SECONDS)

    @property
    def answers(self) -> list[ReplyAnswer]:
        """Every answer the reply verb gave, oldest first. Read after `stop`."""
        return list(self._answers)

    def stop(self) -> None:
        """Stop sending, after the send in flight. A blocked send is left to time out."""
        self._stopping.set()
        self._thread.join(timeout=e2e_timeout(60))


def _graph_events(scratch: Path) -> list[dict[str, Any]]:
    """The dag-scope graph's own record of this run, which names the member per event.

    The `Any` values are carried for the reason the field above states: the envelope is
    `oneagentgraph`'s own and only a few of its keys are read here.

    `just monitor` renders these too, but its line carries the event and not the member
    it happened to, and which member died is exactly the question. The graph writes
    them into the scratch this journey named, so this is that run's record and no
    other's.
    """
    events = []
    for log in sorted(scratch.glob("dag-scope-*/events.jsonl")):
        # `oneagentgraph` owns this schema; only `kind`, `labels.member` and the payload
        # of the events asserted on are read. The `cast` is that ownership at the type level.
        # llmlint: ignore[tests_mirror_real_usage] The rendered line omits the member.
        events.extend(
            cast(dict[str, Any], json.loads(line))
            for line in log.read_text("utf-8").splitlines()
            if line.strip()
        )
    return events


def _stop_requests(scratch: Path) -> dict[str, datetime]:
    """When the driver wrote each observer graph's stop request, to the millisecond.

    Read off the file itself because that file *is* the request `oneagentgraph` acts on,
    and it records no event of its own. Truncated to the millisecond the graph's own
    event timestamps carry, so a death in the same millisecond still reads as following
    it.
    """
    requests = {}
    # llmlint: ignore[tests_mirror_real_usage] No operator view records the stop request.
    for stop in scratch.glob(f"dag-scope-*/{STOP_SIGNAL}"):
        written = datetime.fromtimestamp(stop.stat().st_mtime, tz=UTC)
        requests[stop.parent.parent.name] = written.replace(
            microsecond=written.microsecond // 1000 * 1000
        )
    return requests


def _last_node_settled(settlement: str) -> datetime | None:
    """When the launch rendered its last node settling, which is when the run settled."""
    settled = [
        datetime.fromisoformat(fields[0])
        for fields in (line.split() for line in settlement.splitlines())
        if len(fields) > 2 and fields[1].startswith("graph:") and fields[2] == NODE_SETTLED
    ]
    return max(settled, default=None)


def _settlement_teardown(event: dict[str, Any], watched: Watched) -> bool:
    """Whether a death is the settled run's own teardown rather than a lost watcher.

    All three have to hold: the cause is the cancellation a stop request produces, the
    death came at or after that graph's stop request, and the request came after the
    run's last node settled. A stop requested while nodes were still settling is a
    watcher lost before the end, and fails exactly as any other death does.
    """
    stop = watched.stop_requests.get(str(event.get("stream")))
    settled = _last_node_settled(watched.settlement)
    if event.get("payload", {}).get("cause") != TEARDOWN_CAUSE or stop is None or settled is None:
        return False
    return settled <= stop <= datetime.fromisoformat(str(event.get("ts")))


def _held_plan(tmp_path: Path, run: str, goal: str) -> str:
    """A one-node project whose node is a dispatched worker the stand-in holds open."""
    plan = tmp_path / f"{run}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": run,
                "goal": {"text": goal},
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
    return project_from_plan(plan)


@pytest.fixture(scope="module")
def watched(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Watched]:
    """Launch one run, supervise it with edits alone, and settle it.

    Failures are collected rather than raised: every assertion below is about a
    different way the member could have died, and a fixture that gave up at the first
    missing signal would report one of them as all of them.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("monitor-survives-the-channel")
    environment = _environment(tmp_path, oneharness_bin)
    prompt_log = tmp_path / "prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)
    # Held so the run outlives the handful of monitor turns this needs. The stand-in
    # backend delays the dispatched worker alone — the monitor's own turns answer
    # before it reaches that delay — so this buys the window without slowing the watch.
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    scratch = tmp_path / "graph-state"
    environment[GRAPH_STATE_ENV] = str(scratch)
    project = _held_plan(tmp_path, RUN, "prove the monitor survives its own judge side")

    # Streamed to a file rather than a pipe nobody drains: an attached launch prints the
    # whole merged event stream, and a full pipe buffer stops the driver mid-run — which
    # reads exactly like a run that died.
    printed = tmp_path / "launch.log"
    with printed.open("w", encoding="utf-8") as streaming:
        launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
            [
                "just",
                "orchestrate",
                project,
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
        # Not one edit before the launch has made its run root. A send names the run by its
        # id alone, and `just channel-reply` makes that run's channel directory when it does
        # not exist yet — so an edit that beat the launch would leave a directory the engine
        # then mints a different run id beside, and every read of this run below would read
        # a run that was never launched.
        run_root = Path(environment["ONEPIPELINE_RUNS_DIR"]) / RUN
        until(
            "the launch to write its run's own launch record",
            lambda: (run_root / "launch.json").is_file() or launch.poll() is not None,
            seconds=SUPERVISED_SECONDS,
            state=lambda: printed.read_text("utf-8"),
        )
        assert (run_root / "launch.json").is_file(), printed.read_text("utf-8")
        manager = EditingManager(environment)
        editing = SustainedEditing(manager, prompt_log)
        try:
            # Edit until the claim has been tested rather than until the run ends:
            # this manager's own edits keep the graph one node short of complete, so a
            # window measured by the run never closes. The other two exits are the claim
            # failing and the run ending underneath, and neither is swallowed — each
            # leaves `reached` for the cases below to judge.
            until(
                "the monitor to keep taking turns through sustained live editing",
                lambda: (
                    editing.observe()
                    or _edit_reached_the_monitor(prompt_log)
                    or launch.poll() is not None
                ),
                seconds=SUPERVISED_SECONDS,
                state=lambda: _supervision_state(launch, prompt_log, manager, editing),
            )
            reached = _edit_reached_the_monitor(prompt_log)
            # Stopping is what lets the graph complete: nothing else here ever stops
            # adding nodes to it, and the settlement the last case asserts survival to
            # does not exist until this happens.
            manager.stop()
            until(
                "the run to settle once nothing was adding nodes to its graph",
                lambda: launch.poll() is not None,
                seconds=SUPERVISED_SECONDS,
                state=lambda: _supervision_state(launch, prompt_log, manager, editing),
            )
            launch.wait(timeout=e2e_timeout(60))
            composed = sorted(scratch.glob(f"dag-scope-*/members/{MONITOR_MEMBER}/onejudge.yaml"))
            yield Watched(
                monitor_config=composed[0] if composed else scratch / "no-effective-config",
                monitor_prompts=_monitor_prompts(prompt_log),
                an_edit_reached_the_monitor=reached,
                reply_answers=manager.answers,
                stream=_just(
                    "monitor", RUN, "--filter", "detailed", environment=environment, seconds=60
                ).stdout,
                graph_events=_graph_events(scratch),
                stop_requests=_stop_requests(scratch),
                settlement=printed.read_text("utf-8"),
            )
        finally:
            manager.stop()
            launch.kill()
            launch.wait(timeout=e2e_timeout(60))
            _just("stop", RUN, environment=environment, seconds=60)


@pytest.mark.xdist_group("monitor-survives-the-channel")
def test_the_merged_config_a_launch_hands_the_monitor_declines_what_it_can(
    watched: Watched,
) -> None:
    """The effective onejudge config asks for no assessment and no evals.

    `tests/test_planner_channel_personas.py` holds the two files this is merged from;
    neither of them is the answer, because inheritance is the whole hazard — the base
    declares an `assessment` for the dispatched workers it is shared with, and a persona
    that says nothing about it inherits it. `oneagentgraph` writes the merged result into
    the member's own scratch, and that file is the only place the answer exists.
    """
    assert watched.monitor_config.is_file(), (
        f"no effective config was written for the `{MONITOR_MEMBER}` member, so what it "
        f"was launched with cannot be read at all: {watched.monitor_config}"
    )
    # The merged base ⊕ persona result exists in exactly one place and no operator view
    # reports it: `status`, `results`, and `transcript` all carry what a member did, not
    # what it was configured with, and the two source files each state only half of the
    # answer.
    # llmlint: ignore[tests_mirror_real_usage] No operator view carries a merged config.
    effective = watched.monitor_config.read_text("utf-8")
    for question, unset in DECLINED.items():
        asked = [
            line
            for line in effective.splitlines()
            if line.strip().startswith(f"{question}:")
            and line.split(":", 1)[1].strip() not in unset
        ]
        assert not asked, (
            f"the launch handed the `{MONITOR_MEMBER}` member a `{question}` to "
            f"answer ({asked}), and its judge side is the planner channel. onejudge asks "
            f"that question once the conversation ends, the binding refuses it, and the "
            f"member dies on the refusal:\n{effective}"
        )


@pytest.mark.xdist_group("monitor-survives-the-channel")
def test_the_bar_the_member_cannot_decline_is_still_there_to_be_served(
    watched: Watched,
) -> None:
    """The upstream gap the score path works around is still open, measurably.

    Asserting a `done_when` is PRESENT reads backwards until you have tried to remove
    one. `oneagentgraph` merges a persona's `user.done_when` as a second bar alongside
    the base's rather than over it, so a null adds nothing; and
    `user.done_when_replaces_base` is refused outright with nothing to replace it with.
    A `kind: onejudge` member therefore always carries a bar onejudge always asks its
    judge side to score, whether or not that judge side is a model — and the binding
    serving that op, which the journey below drives, is a workaround for exactly that.

    So this is the gate on the gap. The day a release lets a member decline the bar, this
    fails, and whoever reads it can stop relying on the score path.
    """
    # Same file and same reason as above, and here it is the subject rather than a
    # convenience: the claim is about what `oneagentgraph` composed, which is observable
    # nowhere else at all.
    # llmlint: ignore[tests_mirror_real_usage] No operator view carries a merged config.
    effective = watched.monitor_config.read_text("utf-8")
    carried = [line for line in effective.splitlines() if line.strip().startswith(UNDECLINABLE)]
    assert carried, (
        f"the `{MONITOR_MEMBER}` member was launched with no `{UNDECLINABLE.rstrip(':')}` at "
        "all, which the adopted oneagentgraph refuses to compose. If a release now allows "
        "it, the completion score the binding puts to the planner exists to work around a "
        f"gap that has closed:\n{effective}"
    )


@pytest.mark.xdist_group("monitor-survives-the-channel")
def test_no_manager_live_edit_is_handed_to_the_monitor(watched: Watched) -> None:
    """The failure this journey was written for does not reach the member, and it is measured.

    The manager playing this run sends nothing but graph edits, for the whole life of the
    run — every one of them a reply that, under onepipeline 0.8.x, the monitor's judge
    side would have claimed whenever it got to the queue first, and died on. Now a
    commands-only envelope goes to the engine's command path, and the monitor's judge side
    asks no question at a turn boundary for one to be mistaken for an answer.

    So the assertion is that no prompt the monitor was given carries an edit, and it is
    only worth anything beside the two below it: that the edits really were sent and
    really did reach the graph. Without those this would pass for a manager that sent
    nothing.
    """
    assert watched.reply_answers, (
        "this manager sent no live edit at all, so nothing here says anything about how "
        "one is routed"
    )
    handed = [prompt for prompt in watched.monitor_prompts if EDITED_NODE in prompt]
    assert not watched.an_edit_reached_the_monitor and not handed, (
        "a manager's live edit reached the monitor's own conversation, which the command "
        f"path is meant to keep it out of:\n{handed}"
    )
    assert len(watched.monitor_prompts) > 1, (
        "the monitor took one turn or none, so this run never watched anything and the "
        f"absence above says nothing:\n{watched.monitor_prompts}"
    )


@pytest.mark.xdist_group("monitor-survives-the-channel")
def test_the_manager_live_edit_still_reached_the_engine(watched: Watched) -> None:
    """The edit is accounted for rather than dropped, and by the engine rather than here.

    An edit routed away from the monitor is only safe if it arrives where it was routed:
    the engine's command path applies it. If the routing ever sent edits nowhere, keeping
    them away from the monitor would start losing them — and this is the check that would
    fail rather than a paragraph that would quietly go stale.
    """
    assert "edit-committed" in watched.stream, (
        "the manager's live edits never reached the graph, so either the command path "
        f"stopped applying an envelope's commands or nothing was ever sent:\n{watched.stream}"
    )


@pytest.mark.xdist_group("monitor-survives-the-channel")
def test_the_reply_verb_says_itself_that_it_sent_the_edit_to_the_command_path(
    watched: Watched,
) -> None:
    """And `just channel-reply` says where the edit went, in the bus's own answer.

    A commands-only envelope is `onemessagebus send replies`, which the planner-channel
    layout routes to the engine's `commands` queue and answers with one `{queue, position,
    id}` line. The event above proves the edit landed; this proves the recipe routed it
    there rather than to the queue a question is answered on, which is the half a reader
    checks the recipe against. Every accepted send is held to it.
    """
    accepted = [
        json.loads(answered.stdout)
        for answered in watched.reply_answers
        if answered.returncode == 0 and answered.stdout.strip().startswith("{")
    ]

    assert accepted, f"no live edit was accepted at all:\n{watched.reply_answers}"
    assert all(answer.get("queue") == "commands" for answer in accepted), (
        "a live edit was sent somewhere other than the engine's command path, where "
        f"nothing applies it:\n{accepted}"
    )
    assert all(
        isinstance(answer.get("position"), int) and isinstance(answer.get("id"), int)
        for answer in accepted
    ), f"an answer no longer says where on that queue the edit was appended:\n{accepted}"


@pytest.mark.xdist_group("monitor-survives-the-channel")
def test_the_monitor_lives_to_the_graphs_settlement(watched: Watched) -> None:
    """The member is still there when the graph ends, which is the whole point of it.

    Deliberately settlement and not "it started" or "it survived a turn". Both of the
    deaths this journey is about land at a boundary rather than at launch — one at the
    end of the conversation, one at whichever turn a manager happens to correct the run
    on — so a member that started, watched, and was killed before the run finished
    would satisfy every weaker assertion while leaving exactly the gap that let
    `nds-decentralize-20` run seventeen hours unobserved.
    """
    assert "SETTLED" in watched.settlement or "settlement" in watched.settlement, (
        f"the launch did not report the run settling, so there is no settlement to have "
        f"survived to:\n{watched.settlement}"
    )
    # Every death counts but the settled run's own teardown (`_settlement_teardown`): a
    # stop the driver wrote after the last node settled that reached the member inside a
    # turn. That one is the settlement this case asserts survival to, not a loss before it.
    died = [
        event
        for event in watched.graph_events
        if event.get("kind") == MEMBER_DIED
        and event.get("labels", {}).get(MEMBER_LABEL) == MONITOR_MEMBER
        and not _settlement_teardown(event, watched)
    ]
    assert not died, (
        f"the `{MONITOR_MEMBER}` member did not survive this run; the graph recorded "
        f"{json.dumps(died)}. The run goes on being driven and reporting `ACTIVE` after "
        "that, with nothing watching it and nothing announcing the loss."
    )
    assert any(DAG_SCOPE_STREAM in line for line in watched.stream.splitlines()), (
        "no dag-scope event reached the planner's stream at all, so the absence of a "
        f"`{MEMBER_DIED}` above proves nothing:\n{watched.stream}"
    )


#: The run whose monitor a manager scores, and the ruling they score it with. The reason
#: is distinctive, because a score arriving with it is the manager's and no default's; and
#: the value is `true`, because a wait nobody answers is scored `false`, so only `true`
#: tells a relayed ruling from a question that went unanswered.
SCORED_RUN = "monitor-scored-by-the-planner"
PLANNER_RULED = "the planner read the watch and ruled it met the bar"

#: How long the manager below waits for the monitor's conversation to end and ask.
SCORE_ASKED_SECONDS = 180


def _completion_question(environment: dict[str, str]) -> dict[str, object] | None:
    """The completion score the monitor's judge side is asking for, if it is asking yet."""
    asked = [
        record
        for record in _queue_state(environment, SCORED_RUN, "surfaces")["waiting"]
        if record.get("kind") == SURFACE_KIND_OF_A_COMPLETION_SCORE
    ]
    return asked[0] if asked else None


@pytest.mark.xdist_group("monitor-scored-by-the-planner")
def test_the_planner_scores_the_completion_bar_through_the_reply_recipe(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The `judge` op is put to the manager, and their ruling is the score onejudge records.

    A `kind: onejudge` member always carries a `done_when`, and onejudge always asks its
    judge side to score it once the conversation ends. There is no configuration escape,
    so this op has to be *served*, and the only answer that is not an invention is the
    manager's: they are this member's judge side. So a real launch lets the monitor's
    conversation end at its turn cap, and a manager answers the question the judge side
    raised with `just channel-reply --correlation`, the way any question on this channel
    is answered.

    What is held is the whole round trip the wiring owns: the question reaches this run's
    own channel non-blocking and quoting the bar, the recipe binds the ruling to it, and
    the member is settled with exactly the value and reason the manager typed — read off
    the graph's own `member-settled` record rather than off anything the recipe said.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _environment(tmp_path, oneharness_bin)
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    scratch = tmp_path / "graph-state"
    environment[GRAPH_STATE_ENV] = str(scratch)
    project = _held_plan(tmp_path, SCORED_RUN, "prove the planner scores the monitor")

    with (tmp_path / "launch.log").open("w", encoding="utf-8") as streaming:
        launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
            [
                "just",
                "orchestrate",
                project,
                *CONVERSATION_ENDS_AT_ITS_CAP,
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
        limit = deadline(SCORE_ASKED_SECONDS)
        question = _completion_question(environment)
        while question is None:
            if launch.poll() is not None or time.monotonic() >= limit:
                pytest.fail(
                    "the monitor's judge side never asked this run's channel for a completion "
                    f"score:\n{(tmp_path / 'launch.log').read_text('utf-8')}"
                )
            time.sleep(0.2)
            question = _completion_question(environment)

        assert question.get("blocking") is False, (
            f"the completion score was raised as a BLOCKING question, which holds a manager "
            f"on the watch rather than on the work: {question}"
        )
        assert " ".join(shared_completion_bar().split()) in " ".join(
            str(question.get("message")).split()
        ), f"the question does not quote the bar it asks the manager to score: {question}"
        correlation = str(question.get("correlation"))

        sent = channel_reply(
            SCORED_RUN,
            environment,
            json.dumps({"version": 3, "completion": True, "reason": PLANNER_RULED}),
            correlation,
        )
        assert sent.returncode == 0, sent.stderr + sent.stdout
        answered = json.loads(sent.stdout)
        assert answered["correlation"] == correlation and answered["answered"], answered

        def scored() -> list[dict[str, Any]]:
            # `Any` because each verdict is the engine's own settlement record, read off the
            # graph's event log unvalidated; the journey asserts the members it is about.
            return [
                verdict["verdict"]
                for event in _graph_events(scratch)
                if event.get("kind") == MEMBER_SETTLED
                and event.get("labels", {}).get(MEMBER_LABEL) == MONITOR_MEMBER
                for verdict in event.get("payload", {}).get("verdict", [])
            ]

        until(
            "the monitor to be settled with the score its judge side relayed",
            lambda: bool(scored()),
            seconds=120,
            state=lambda: f"the graph recorded no settled monitor yet: {_graph_events(scratch)}",
        )
        assert scored()[0] == {"value": True, "reason": PLANNER_RULED}, (
            "the monitor was settled with a score other than the ruling the manager sent, so "
            f"the question and the reply did not meet on one channel: {scored()}"
        )
    finally:
        launch.kill()
        launch.wait(timeout=e2e_timeout(60))
        _just("stop", SCORED_RUN, environment=environment, seconds=60)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[shell_test_tiers_stay_split]
