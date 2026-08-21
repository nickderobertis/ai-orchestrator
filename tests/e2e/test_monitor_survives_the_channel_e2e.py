"""The run's monitor lives through everything the planner channel hands it.

`graphs/dag-scope.yaml`'s monitor is the thing that notices a run going wrong, and it
had two ways of dying on its own judge side — both of which leave the run reporting
`ACTIVE` with nobody watching, because nothing announces the loss:

1. **A question the channel cannot answer.** `scripts/channel-serve.py` serves the
   `supervisor` op alone. onejudge asks its judge side other ops at the end of a
   conversation — `assess` for an `assessment`, `judge` for each `evals` criterion
   *and* for `user.done_when` — and each is refused by name, after which
   `oneagentgraph` kills the member with `provider-failure`/`protocol`. Which keys
   produce which op is a declaration, and `tests/test_observer_judge_ops.py` holds it.
   What a declaration cannot say is whether the merged configuration a **launch**
   composes still carries one, or whether the member actually lives to the end.
2. **An answer addressed to somebody else.** The channel is a durable queue with two
   readers, and through onepipeline 0.8.x it arbitrated between them by arrival order —
   so a manager's live graph edit, `commands` and no `completion`, reached the monitor's
   judge side whenever it got there first. Forty of this host's recorded dag-scope runs
   died on it, at the worst possible timing: it fired precisely while a manager was
   supervising, because the manager's own correction was what killed the watcher.

   **The adopted release routes a reply by the halves it carries**, so that envelope
   never reaches this reader at all. That is what the second half of this journey now
   measures: a manager who answers a real run with nothing but live edits for the whole
   life of that run, and a judge side that is handed none of them. The filter's own
   recognition of one is kept — it is what stands between a run and that death if a
   release regresses — and is driven directly, against a stand-in channel made to hand
   one back, at both of the boundaries a member has.

So this launches one real run and plays a manager who answers **only** with live
edits, which is the reproduction rather than a stand-in for it: the real recipe, the
real engine, the real graph, the real filter, and the real channel. Nothing is
substituted but the paid model, at the `oneharness` seam every other journey here
substitutes it at. The run is then driven to settlement and the whole of it is judged
at once — the effective config the member was launched with, what the judge side was
and was not handed, the edits the engine got, and the graph's own record of how each
member ended.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
from fake_backend import AGENT_DELAY_ENV, PROMPT_LOG_ENV

# The launch environment has one source and it is the module that owns the launch
# journeys. Copying its twenty lines here is how a journey comes to run against a
# seam the rest of the suite has moved off — the fake provider, the guarded PATH, the
# alternate-identity indirections — so it is imported rather than restated.
from test_orchestrate_launch_e2e import (
    DAG_SCOPE_STREAM,
    MONITOR_MEMBER,
    PACEMAKER_INTERVAL_SECONDS,
    SUPERVISOR_FRAME,
    _environment,
    _just,
)
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.xdist_group("monitor-survives-the-channel")

#: The run this journey launches, and the one node it holds open. The node is held so
#: the run stays alive long enough for the monitor to take several turns and for a
#: manager's edit to reach it; it settles on its own afterwards, which is what gives
#: this journey a settlement to watch the member survive to.
RUN = "monitor-survives-the-channel"
HELD_NODE = "held"
HELD_SECONDS = 90

#: How the filter opens the ruling it gives a monitor whose surface was answered with a
#: live edit. Read out of `scripts/channel-serve.py` rather than quoted, because this is
#: the one string that says the filter recognised the envelope instead of dying on it,
#: and a second copy of it here would keep passing after the first was reworded away.
FILTER = REPO_ROOT / "scripts" / "channel-serve.py"
ROUTED_RULING = "live graph edit addressed to the"

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

#: And the one it always carries however it is declared, which is why the filter serves
#: the op that scores it. Asserted PRESENT deliberately; see the test below.
UNDECLINABLE = "done_when:"

#: Where `oneagentgraph` writes each member's effective config and its own event log,
#: named by this journey so it reads this run's graph and never a concurrent dispatch's.
GRAPH_STATE_ENV = "ONEAGENTGRAPH_STATE_DIR"

#: The env var naming the run to a scoring frame, which carries no task to read one
#: out of. Stated by the cases below rather than inherited: this suite runs from inside
#: a dispatch that carries its own, and which run a frame belongs to is under test.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"

#: The seam that lets a case stand a chosen channel behind the real filter. The published
#: `channel serve` cannot be made to time out or to hand back a live edit on demand, and
#: those are the two degradations the score path has to survive.
ONEPIPELINE_BIN = "ONEPIPELINE_BIN"

#: The scoring frame onejudge writes once a conversation ends, measured on onejudge 0.4.0
#: with a `kind: command` judge that logged every op it was asked. No `task`, no `session`
#: — which is why the run is read from the environment.
SCORING_FRAME = {
    "op": "judge",
    "kind": "boolean",
    "criterion": "every acceptance criterion stated in the task is met",
    "messages": [{"role": "assistant", "content": "I watched the run."}],
}


#: The graph event that says a member did not survive, and the label naming which one.
#: Read from the graph's own record because the rendered event line does not carry the
#: member, and which member died is the whole question here.
MEMBER_DIED = "member-died"
MEMBER_LABEL = "member"


class ReplyAnswer(NamedTuple):
    """One thing `just channel-reply` answered a live edit with.

    The status and the body are read apart and mean different things — whether the
    send was refused, and what the verb claimed it did — so neither is positional.
    """

    #: The verb's exit status; non-zero is a refusal, which this manager ignores.
    returncode: int
    #: Its stdout verbatim, which is the JSON both documents quote when it is zero.
    stdout: str


class Watched(NamedTuple):
    """One settled run whose manager only ever sent live edits, and its evidence."""

    #: The effective onejudge config the launch handed the monitor member.
    monitor_config: Path
    #: Every prompt the monitor's agent side was given, oldest first.
    monitor_prompts: list[str]
    #: Whether a live edit reached the filter, which is what makes the rest meaningful.
    filter_answered_a_live_edit: bool
    #: What `just channel-reply` answered each edit with, oldest first. The premise the
    #: filter's inaction rests on is that the reply verb applies an envelope's commands
    #: itself, and this is that verb's own answer saying so.
    reply_answers: list[ReplyAnswer]
    #: `just monitor --filter monitor` over the settled run.
    stream: str
    #: The graph's own events, which name the member behind each one.
    graph_events: list[dict[str, Any]]
    #: How the attached launch ended, and what it printed on the way.
    settlement: str


def _monitor_prompts(prompt_log: Path) -> list[str]:
    """Every prompt the monitor's AGENT side has been given so far, oldest first.

    Tolerates the log not existing, which is the ordinary state for the first moments
    of a launch rather than a failure to report. The agent side is the turn pinned to
    the member's own harness config; its judge side is a command and takes no turn.
    """
    if not prompt_log.exists():
        return []
    # The member's NEXT PROMPT is the only place surviving an answer is observable. An
    # operator view reports that a member is alive, which a member killed one turn later
    # also is, and reports nothing about what it was given — so an event label would pass
    # for the death this asserts against. The prompt cannot: the filter's ruling is in it
    # only if the filter recognised the envelope, answered onejudge with something it
    # could act on, and the member then took another turn.
    # llmlint: ignore[tests_mirror_real_usage] No operator view carries a turn's prompt.
    recorded = [json.loads(line) for line in prompt_log.read_text("utf-8").splitlines()]
    return [
        turn["prompt"]
        for turn in recorded
        if f"/members/{MONITOR_MEMBER}/" in (turn.get("config") or "")
    ]


def _live_edit(sequence: int) -> str:
    """One reply envelope that is a graph edit and nothing else.

    Commands and no `completion`, which is what makes it the reconciler's rather than
    the monitor's judge side's — and what the filter has to recognise. `add` of a node
    that settles without a dispatch, so each one is genuinely applied and genuinely
    observable, and unique per send because the same id twice is refused the second
    time.
    """
    return json.dumps(
        {
            "version": 1,
            "author": "planner",
            "commands": [
                {
                    "op": "add",
                    "node": {
                        "id": f"noted-by-the-manager-{sequence}",
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
    of answering, so this one never sends a `completion` at all. Refusals are ignored
    on purpose — between two readers of one queue there are moments with no reader, and
    a send nobody is waiting for is refused rather than being a failing manager.
    """

    def __init__(self, environment: dict[str, str]) -> None:
        self._environment = environment
        self._stopping = threading.Event()
        self._sent = 0
        self._answers: list[ReplyAnswer] = []
        self._thread = threading.Thread(target=self._edit, daemon=True)
        self._thread.start()

    #: How long to leave between edits. Nothing at all while this raced a reader that
    #: no longer takes these envelopes means editing flat out for the whole run: one
    #: send added one node, and a three-minute run took over thirteen hundred of them
    #: before it settled. A monitor's turn lasts seconds, so this is still an edit
    #: inside every window there is to arrive in.
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

    `just monitor` renders these too, but its line carries the event and not the member
    it happened to, and which member died is exactly the question. The graph writes
    them into the scratch this journey named, so this is that run's record and no
    other's.
    """
    events = []
    for log in sorted(scratch.glob("dag-scope-*/events.jsonl")):
        # `oneagentgraph` owns this schema; only `kind` and `labels.member` are read.
        # llmlint: ignore[tests_mirror_real_usage] The rendered line omits the member.
        events.extend(
            cast(dict[str, Any], json.loads(line))
            for line in log.read_text("utf-8").splitlines()
            if line.strip()
        )
    return events


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
    plan = tmp_path / "watched.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": RUN,
                "goal": {"text": "prove the monitor survives its own judge side"},
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
                str(plan),
                "--heartbeat-interval",
                str(PACEMAKER_INTERVAL_SECONDS),
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdout=streaming,
            stderr=subprocess.STDOUT,
        )
        manager = EditingManager(environment)
        try:
            # Edits for the whole life of the run, and stops when the run does. The
            # window used to be a fixed one because reaching the filter was a race the
            # manager had to win; now it must never be won, and "for as long as there
            # was a run to edit" is both the stronger window and the one that does not
            # spend twenty minutes proving a negative after the run has settled.
            reached = False
            while launch.poll() is None and not reached:
                reached = any(ROUTED_RULING in prompt for prompt in _monitor_prompts(prompt_log))
                if not reached:
                    time.sleep(0.5)
            manager.stop()
            launch.wait(timeout=e2e_timeout(300))
            composed = sorted(scratch.glob(f"dag-scope-*/members/{MONITOR_MEMBER}/onejudge.yaml"))
            yield Watched(
                monitor_config=composed[0] if composed else scratch / "no-effective-config",
                monitor_prompts=_monitor_prompts(prompt_log),
                filter_answered_a_live_edit=reached,
                reply_answers=manager.answers,
                stream=_just(
                    "monitor", RUN, "--filter", "monitor", environment=environment, seconds=60
                ).stdout,
                graph_events=_graph_events(scratch),
                settlement=printed.read_text("utf-8"),
            )
        finally:
            manager.stop()
            launch.kill()
            launch.wait(timeout=e2e_timeout(60))
            _just("stop", RUN, environment=environment, seconds=60)


def test_the_merged_config_a_launch_hands_the_monitor_declines_what_it_can(
    watched: Watched,
) -> None:
    """The effective onejudge config asks for no assessment and no evals.

    `tests/test_observer_judge_ops.py` holds the two files this is merged from; neither
    of them is the answer, because inheritance is the whole hazard — the base declares an
    `assessment` for the dispatched workers it is shared with, and a persona that says
    nothing about it inherits it. `oneagentgraph` writes the merged result into the
    member's own scratch, and that file is the only place the answer exists.
    """
    assert watched.monitor_config.is_file(), (
        f"no effective config was written for the `{MONITOR_MEMBER}` member, so what it "
        f"was launched with cannot be read at all: {watched.monitor_config}"
    )
    # The merged base ⊕ persona result exists in exactly one place and no operator view
    # reports it: `status`, `results`, and `transcript` all carry what a member did, not
    # what it was configured with, and the two source files each state only half of the
    # answer. `test_a_nodes_turn_budget_reaches_the_dispatch_it_was_written_for` reads
    # the same file for the same reason.
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
            f"that question once the conversation ends and the member dies on the "
            f"refusal:\n{effective}"
        )


def test_the_bar_the_member_cannot_decline_is_still_there_to_be_served(
    watched: Watched,
) -> None:
    """The upstream gap this whole score path works around is still open, measurably.

    Asserting a `done_when` is PRESENT reads backwards until you have tried to remove
    one. `oneagentgraph` merges a persona's `user.done_when` as a second bar alongside
    the base's rather than over it, so a null adds nothing; and
    `user.done_when_replaces_base` is refused outright with nothing to replace it with.
    A `kind: onejudge` member therefore always carries a bar onejudge always asks its
    judge side to score, whether or not that judge side is a model — and the filter
    serving that op is a workaround for exactly that, not a feature.

    So this is the gate on the gap. The day a release lets a member decline the bar, this
    fails, and whoever reads it can retire the score path instead of maintaining a
    workaround for something that stopped being broken.
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
        "it, the completion-score path in scripts/channel-serve.py exists to work around a "
        f"gap that has closed and can go:\n{effective}"
    )


def test_no_manager_live_edit_is_handed_to_the_monitors_judge_side(
    watched: Watched,
) -> None:
    """The failure this journey was written for is fixed at its source, and measured.

    The manager playing this run sends nothing but graph edits, for the whole life of
    the run — every one of them a reply that, under onepipeline 0.8.x, this reader
    would have claimed whenever it got to the queue first, and died on. The adopted
    release routes a reply by the halves it carries: a commands-only envelope belongs
    to the command path, and the verdict queue this reader claims from does not hold
    it.

    So the assertion is that the monitor was handed **none** of them, and it is only
    worth anything beside the two below it: that the edits really were sent and really
    did reach the graph. Without those this would pass for a manager that sent nothing.
    """
    assert ROUTED_RULING in FILTER.read_text("utf-8"), (
        f"{FILTER.name} no longer opens that ruling with {ROUTED_RULING!r}, so this journey "
        "is matching a string nothing produces; read the constant out of it again"
    )
    assert watched.reply_answers, (
        "this manager sent no live edit at all, so nothing here says anything about how "
        "one is routed"
    )
    answered = [prompt for prompt in watched.monitor_prompts if ROUTED_RULING in prompt]
    assert not watched.filter_answered_a_live_edit and not answered, (
        "a live edit was handed to the monitor's judge side, which the adopted release "
        "routes away from it. Either the routing regressed — in which case the filter "
        "below is the only thing keeping this member alive and every document describing "
        f"that routing is now wrong — or this run reached it another way:\n{answered}"
    )
    assert len(watched.monitor_prompts) > 1, (
        "the monitor took one turn or none, so this run never watched anything and the "
        f"absence above says nothing:\n{watched.monitor_prompts}"
    )


def test_the_manager_live_edit_still_reached_the_engine(watched: Watched) -> None:
    """The edit is accounted for rather than dropped, and by the engine rather than here.

    This is the premise the filter's own inaction rests on, so it is measured rather
    than assumed: `onepipeline reply` applies an envelope's commands *itself*, before
    the envelope is queued for any reader, so an edit that then arrives at the monitor's
    judge side has already landed and there is nothing left there to route. If a release
    ever stopped doing that, ignoring one would start losing it — and this is the check
    that would fail rather than a paragraph that would quietly go stale.
    """
    assert "edit-committed" in watched.stream, (
        "the manager's live edits never reached the graph, so either the reply verb "
        "stopped applying an envelope's commands or nothing was ever sent:\n"
        f"{watched.stream}"
    )


def test_the_reply_verb_says_itself_that_it_applied_the_edit(watched: Watched) -> None:
    """And it says so in its own answer, which is the literal both documents quote.

    `scripts/channel-serve.py` and `docs/orchestration.md` each date this measurement to
    a release and quote what the verb answered — `{"reply":0,"state":"applied"}` — because
    it is the whole reason ignoring a claimed edit here is safe rather than lossy. The
    event above proves the edit landed; this proves the verb reported landing it, which
    is the half a reader checks the quoted literal against. Both move in the same change
    as the dated literal, so a release that reworded the answer re-dates the paragraphs
    rather than leaving them quoting a shape nothing emits.

    Only that at least one send was answered this way, deliberately: this manager races
    the engine's own reader for the queue, so a send nobody was waiting for is refused
    rather than being a failing manager, exactly as `EditingManager` says. The `state` is
    pinned and the `reply` count is only required to be a number, because the count is how
    many surfaces that send also answered — a property of what happened to be pending, not
    of the release — while `applied` is the claim the paragraphs rest on.
    """
    applied = [
        json.loads(answered.stdout)
        for answered in watched.reply_answers
        if answered.returncode == 0 and answered.stdout.strip().startswith("{")
    ]

    assert any(answer.get("state") == "applied" for answer in applied), (
        "no live edit was answered `state: applied` by the reply verb, so the premise "
        "the filter's own inaction rests on no longer holds; re-measure the answer and "
        "re-date every document quoting it, in this change. What it answered instead:\n"
        f"{watched.reply_answers}"
    )
    assert all(isinstance(answer.get("reply"), int) for answer in applied), (
        "an answer no longer carries the `reply` count both documents quote beside the "
        f"state:\n{watched.reply_answers}"
    )


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
    died = [
        event
        for event in watched.graph_events
        if event.get("kind") == MEMBER_DIED
        and event.get("labels", {}).get(MEMBER_LABEL) == MONITOR_MEMBER
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


def _channel_answering(tmp_path: Path, answer: str) -> Path:
    """A stand-in `channel serve` that keeps the surface it was handed, and answers.

    Only the channel, and only for the cases whose subject is an answer the published
    one cannot be made to give on demand — a reply window that ran out, and a live edit
    claimed at this boundary. The filter, its argv, the frame on its stdin, and the
    response onejudge reads back are all real.
    """
    channel = tmp_path / "stand-in-channel"
    captured = tmp_path / "surface.json"
    # llmlint: ignore[e2e_not_mocked] The published channel cannot make these answers.
    channel.write_text(f"#!/usr/bin/env bash\ncat > {captured}\ncat <<'JSON'\n{answer}\nJSON\n")
    channel.chmod(0o755)
    return channel


def _scored(
    tmp_path: Path,
    oneharness_bin: str,
    answer: str,
    *,
    frame: dict[str, object] | None = None,
    run: str | None = RUN,
) -> subprocess.CompletedProcess[str]:
    """Put one scoring frame to the real filter, against a channel answering `answer`.

    `run` is what the environment names, and `None` is a launch that named nothing —
    which is a state to drive rather than one to assume away, since this suite runs from
    inside a dispatch that carries a run id of its own.
    """
    environment = _environment(tmp_path, oneharness_bin)
    environment.pop(RUN_ID_ENV, None)
    if run is not None:
        environment[RUN_ID_ENV] = run
    # llmlint: ignore[e2e_not_mocked] The published channel cannot make these answers.
    environment[ONEPIPELINE_BIN] = str(_channel_answering(tmp_path, answer))
    return subprocess.run(
        [str(REPO_ROOT / "scripts" / "channel-serve.py")],
        cwd=REPO_ROOT,
        env=environment,
        input=json.dumps(frame if frame is not None else SCORING_FRAME),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def test_the_planner_scores_the_completion_bar_they_are_the_judge_side_for(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The `judge` op is put to the planner and their ruling comes back as the score.

    A `kind: onejudge` member always carries a `done_when` — `oneagentgraph` refuses a
    persona that replaces the base's bar with nothing — and onejudge always asks its
    judge side to score it once the conversation ends. There is no configuration escape,
    so this op has to be *served*, and the only answer that is not an invention is the
    planner's: they are this member's judge side, and they rule with a `completion`
    boolean that relays onto a boolean score exactly.

    The surface it raises is checked as well as the score it returns, because a surface
    that read like a blocking question would move the stall this whole change is about
    from the observer onto the run.
    """
    ruled = _scored(
        tmp_path,
        oneharness_bin,
        '{"completion": true, "reason": "the watch was continuous"}',
    )

    assert ruled.returncode == 0, ruled.stderr
    scored = json.loads(ruled.stdout)
    assert scored == {"value": True, "rationale": "the watch was continuous"}, scored
    raised = json.loads((tmp_path / "surface.json").read_text("utf-8"))
    assert raised["blocking"] is False, (
        f"the completion score was raised as a BLOCKING surface, which parks the run at "
        f"`awaiting-planner` until somebody answers it: {raised}"
    )
    assert SCORING_FRAME["criterion"] in raised["message"], raised["message"]
    assert "NOT BLOCKED" in raised["message"], (
        "the surface does not say that nothing is waiting on it, so a manager meeting it "
        f"for the first time reads it as a run held up on them: {raised['message']}"
    )


def test_a_completion_bar_nobody_answers_scores_false_rather_than_killing_the_member(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The degradation is conservative and silent, which is what makes it safe to ship.

    A planner who never answers is the ordinary case, not the exception — the surface is
    non-blocking and the run settles without it. `channel serve` times out with its own
    non-completion, and that reads straight through as `unsatisfied`: no invention, no
    fabricated success, and above all no exit status, because an exit here is the death
    this whole journey exists to prevent.
    """
    timed_out = _scored(
        tmp_path,
        oneharness_bin,
        '{"completion": false, "message": "no planner reply within the timeout; continue",'
        ' "reason": "the channel timed out waiting for a verdict"}',
    )

    assert timed_out.returncode == 0, timed_out.stderr
    assert json.loads(timed_out.stdout)["value"] is False, timed_out.stdout


def test_a_live_edit_claimed_at_the_score_boundary_does_not_end_the_member_either(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The other reader's envelope arrives at this op too, and is survived here as well.

    The queue does not know which op this filter is serving when it hands over a reply,
    so a manager's live edit reaches the score boundary exactly as it reaches a turn
    boundary. It is recognised in one place for both, which is the point of serving them
    through one round trip.
    """
    edited = _scored(
        tmp_path,
        oneharness_bin,
        '{"version":1,"commands":[{"op":"context","id":"held","note":"the fixture moved"}]}',
    )

    assert edited.returncode == 0, edited.stderr
    assert json.loads(edited.stdout)["value"] is False, edited.stdout


def test_a_score_the_planner_cannot_rule_on_is_still_refused(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A numeric criterion has no planner answer, so it is named rather than invented.

    A planner rules with a boolean and nothing else, so a criterion asking for a score on
    a scale would have to be made up here. It can only come from `evals`, which
    `tests/test_observer_judge_ops.py` forbids any channel-served persona from carrying —
    so this is the boundary holding rather than a branch anybody takes.
    """
    refused = _scored(
        tmp_path,
        oneharness_bin,
        '{"completion": true, "reason": "sure"}',
        frame={**SCORING_FRAME, "kind": "numeric", "criterion": "how readable the watch was"},
    )

    assert refused.returncode != 0, refused.stdout
    assert "scores a `boolean` criterion" in refused.stderr, refused.stderr
    assert "value" not in refused.stdout, refused.stdout


def test_a_score_that_names_no_run_is_reported_rather_than_guessed_at(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The scoring frame's only source for the run is the environment, so a gap is named.

    Every other op is held to the composed task, which this filter already validates. A
    scoring frame carries no task at all, so `ONEPIPELINE_RUN_ID` is the whole of what
    it has — and a release that stopped exporting it would leave this reader with no
    channel to serve and no way to say so unless it checks. Reported rather than
    answered around: a score printed here without a planner behind it is the invention
    this file exists not to make.
    """
    unnamed = _scored(tmp_path, oneharness_bin, '{"completion": true, "reason": "sure"}', run=None)

    assert unnamed.returncode != 0, unnamed.stdout
    assert f"{RUN_ID_ENV} names no run" in unnamed.stderr, unnamed.stderr
    assert "value" not in unnamed.stdout, unnamed.stdout


@pytest.mark.parametrize(
    ("case", "run"),
    [
        ("a run argv would read as a flag", "--all"),
        ("a run that reaches outside the runs directory", "../../etc/passwd"),
    ],
)
def test_a_score_naming_a_run_the_environment_cannot_spend_is_refused(
    tmp_path: Path, oneharness_bin: str, case: str, run: str
) -> None:
    """The environment is a source like any other, so what it names is checked like one.

    Every other op reads its run out of a composed task, and that source is already held
    to this grammar. A scoring frame's source is `ONEPIPELINE_RUN_ID` instead — exported
    by whatever launched the member — and the value is spent the same two ways, as an
    argv word to `onepipeline channel serve` and as a `runs/<run-id>/` path. Trusting it
    because it arrived through the environment is how a filter that refuses a hostile
    task hands the same string to a subprocess one op over.
    """
    refused = _scored(tmp_path, oneharness_bin, '{"completion": true, "reason": "sure"}', run=run)

    assert refused.returncode != 0, f"{case} was scored anyway: {refused.stdout}"
    assert "will not pass to" in refused.stderr, f"{case}: {refused.stderr}"
    assert RUN_ID_ENV in refused.stderr, (
        f"{case} was refused without naming the environment variable an operator has to "
        f"fix, which is the only place this run came from: {refused.stderr}"
    )
    assert "value" not in refused.stdout, f"{case}: {refused.stdout}"


@pytest.mark.parametrize(
    ("case", "criterion"),
    [("a criterion that is missing", None), ("a criterion that is blank", "   ")],
)
def test_a_score_with_no_criterion_to_rule_on_is_reported_rather_than_guessed_at(
    tmp_path: Path, oneharness_bin: str, case: str, criterion: str | None
) -> None:
    """There is nothing to put to the planner, so nothing is put to them.

    A surface asking a manager to rule on an empty bar is worse than no surface: they
    cannot answer it, and answering it wrongly is what decides the member's reported
    completion. Both shapes are read defensively at the one place the criterion is used,
    because this is onejudge's wire format and not this repository's.
    """
    frame = dict(SCORING_FRAME)
    if criterion is None:
        del frame["criterion"]
    else:
        frame["criterion"] = criterion

    refused = _scored(
        tmp_path, oneharness_bin, '{"completion": true, "reason": "sure"}', frame=frame
    )

    assert refused.returncode != 0, f"{case} was scored anyway: {refused.stdout}"
    assert "scores a `boolean` criterion" in refused.stderr, f"{case}: {refused.stderr}"
    assert "value" not in refused.stdout, f"{case}: {refused.stdout}"


def _supervised(
    tmp_path: Path, oneharness_bin: str, answer: str
) -> subprocess.CompletedProcess[str]:
    """Put one supervisor frame to the real filter, against a channel answering `answer`.

    The turn boundary rather than the score boundary, because what a claimed reply is
    turned into is decided there and only *reported* here — and every branch below is
    about an answer the published channel cannot be made to give on demand.
    """
    environment = _environment(tmp_path, oneharness_bin)
    # llmlint: ignore[e2e_not_mocked] The published channel cannot make these answers.
    environment[ONEPIPELINE_BIN] = str(_channel_answering(tmp_path, answer))
    return subprocess.run(
        [str(REPO_ROOT / "scripts" / "channel-serve.py")],
        cwd=REPO_ROOT,
        env=environment,
        input=json.dumps(SUPERVISOR_FRAME),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def test_a_reply_carrying_both_a_verdict_and_edits_is_still_relayed_as_a_ruling(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The discriminator is the verdict, not the presence of commands.

    This is the other side of the recognition, and getting it wrong is a worse bug than
    the one it guards: a manager who rules AND corrects in one envelope has answered the
    monitor, and swallowing that into a non-completion would silently discard a planner
    verdict — the run would go on being watched by a member nobody could ever finish.
    onejudge acts on `completion` and on nothing else, so an envelope carrying one is a
    ruling however many edits ride with it.
    """
    both = _supervised(
        tmp_path,
        oneharness_bin,
        '{"version":1,"completion":true,"reason":"the watch is finished",'
        '"commands":[{"op":"context","id":"held","note":"n"}]}',
    )

    assert both.returncode == 0, both.stderr
    relayed = json.loads(both.stdout)
    assert relayed["completion"] is True, relayed
    assert relayed["reason"] == "the watch is finished", relayed


def test_a_claimed_edit_is_named_back_to_the_monitor_with_whatever_the_planner_said(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """What the monitor is told is the whole of what this reader does with an edit.

    So it is asserted rather than assumed. Three things have to survive into that one
    sentence: which edits arrived and what each one targets, so a manager reading the
    monitor's next turn can recognise their own correction; a command the envelope
    shaped badly, named as unnamed rather than as `None`; and any prose the planner sent
    beside their edits, since this reader is the last thing holding it — dropping it
    would lose a manager's words with no trace that there were any.
    """
    claimed = _supervised(
        tmp_path,
        oneharness_bin,
        '{"version":1,"commands":[{"op":"context","id":"held","note":"n"},'
        '{"op":"cancel","id":"stale"},{"note":"no op at all"}],'
        '"message":"stop working on the stale node"}',
    )

    assert claimed.returncode == 0, claimed.stderr
    told = json.loads(claimed.stdout)
    assert told["completion"] is False, told
    assert "context held" in told["message"], told["message"]
    assert "cancel stale" in told["message"], told["message"]
    assert "an unnamed edit" in told["message"], told["message"]
    assert "stop working on the stale node" in told["message"], told["message"]


def test_a_score_the_planner_gave_no_reason_for_still_says_who_decided_it(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A bare boolean in a transcript says which way it went and nothing about who ruled.

    `rationale` is optional to onejudge and sent anyway, because the interesting half of
    this score is that a person decided it rather than a model. A planner is entitled to
    rule with neither `reason` nor `message` — `scripts/planner-verdict.sh` renders an
    approve with no message at all — so the fallback is a real path, not a defensive one.
    """
    bare = _scored(tmp_path, oneharness_bin, '{"completion": true}')

    assert bare.returncode == 0, bare.stderr
    scored = json.loads(bare.stdout)
    assert scored["value"] is True, scored
    assert scored["rationale"].strip(), (
        f"the score carries no rationale at all, so its transcript says nothing about who "
        f"decided it: {scored}"
    )
