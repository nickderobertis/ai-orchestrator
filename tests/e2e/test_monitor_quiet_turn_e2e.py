"""A monitor's conversation, answered by the judge side `graphs/dag-scope.yaml` really spawns.

The monitor's judge side is `onemessagebus serve surfaces --codec monitor` over this host's
`config/onemessagebus.yaml`: the bus's generic binding interpreter, running the `monitor`
binding this host declares there as data. What the binding says is this host's policy, so
it is proven here, end to end: a turn the monitor took is answered with a non-completion and
raises nothing; a turn its agent side lost raises one bounded `monitor-failed` surface and
ends the member (`tests/e2e/test_lost_turn_wire_contract_e2e.py` loses that turn for real);
the completion bar is put to the planner as one non-blocking `monitor-completion` question
and scored from the ruling, never a pass nobody gave; and every frame the binding has no
entry for is refused.

Every frame here is onejudge's own. The installed `onejudge run` holds each conversation
(`tests/monitor_conversation.py`), with the argv read out of the graph as its judge side,
spawned where a launch spawns it, over a run directory the real engine made, and the
channel is read back through `onemessagebus status`. Only the paid model is doubled —
oneharness's own mock responder answers the monitor's turns — plus, for the one frame no
path of onejudge writes, the frame itself (see the refusal journey).

Why the rules matter is incidents on this host. While a turn's prose was raised
automatically, a monitor filing a finding had three moves and none was clean: of
`root-causes-94-plan`'s 54 surfaces, 19 are findings and 8 are prose restating the finding
immediately before them. And obeying an instruction to be quiet used to be fatal on the first
quiet turn — `spanish-language-tutor-upgrade` lost its observer five minutes into a two-hour
run, with nothing announcing the loss. So the two outcomes are also driven across a whole
`just orchestrate` launch whose monitor really takes its turns: a run of quiet turns, where
the member has to survive and the channel has to stay empty of its prose — beside the
periodic `check-in` member's own update, which is what makes "nothing was raised" a
statement about the monitor — and a run of lost turns, where the member has to die and say
so once each time.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, cast

import monitor_conversation
import onejudge_bundle
import pytest
from fake_backend import (
    AGENT_DELAY_ENV,
    OBSERVER_ANSWER_ENV,
    OBSERVER_LOSES_ENV,
    OBSERVER_MEMBER_ENV,
    PACEMAKER_REPORT,
    PROMPT_LOG_ENV,
)
from monitor_conversation import Conversation, Held, Taken
from planner_channel import BUS_CONFIG
from planner_channel import reply as channel_reply
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
    RUN_ID_ENV,
    SETTLES_UNWATCHED,
    _judge_command,
    _judge_side,
    _just,
    _settling_project,
)
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: What the scripted monitor says on every turn. Ordinary prose, deliberately: there is no
#: sentinel and no fixed string on this path, so what is under test is that *any* words at
#: all keep the member watching and queue nothing. It names no node, so a surface carrying
#: it could only have come from a judge side that raised prose.
SAID_ON_A_QUIET_TURN = "read the detailed stream; nothing needed raising this turn"

#: What the answer to a taken turn has to say about the turn after it. The graph paces
#: the monitor's conversation with a hold between turns, so an acknowledgement that told
#: the member to keep reading *now* would be an instruction to spend the turn it has just
#: finished.
NEXT_TURN_OPENS_AFTER_THE_HOLD = "next turn opens after the graph's hold"

#: The two kinds this host's binding raises: a lost turn, and the completion bar.
SURFACE_KIND_OF_A_LOST_TURN = "monitor-failed"
SURFACE_KIND_OF_A_COMPLETION_SCORE = "monitor-completion"

#: The ceiling a lost turn's surface may not exceed, for any cause, identity, and run id
#: these journeys drive through it. The raw transcript it replaced was 21,531 characters.
NAMED_FAILURE_LIMIT = 400

#: The judge command's verdict on a turn the agent side lost: the member failed. onejudge
#: reads any exit but 0 as its judge side failing, and `oneagentgraph` then ends the member.
MEMBER_FAILED = 1

#: The judge command's verdict on a frame the binding has no entry for.
REFUSED = 2

#: The one line of this host's configuration a copy changes, so the reply window elapses
#: in seconds rather than the fifty minutes a manager is given. Nothing else differs.
REPLY_WINDOW = "    reply_window_seconds: 3000"
SHORT_WINDOW_SECONDS = 3

#: The variable this host's binding bounds one serving session by.
SESSION_ENV = "ORCHESTRATOR_MONITOR_SESSION_SECONDS"

#: The asker the engine names an observer member's judge side with.
ASKER_ENV = "ONEPIPELINE_CHANNEL_ASKER"
ASKER = "dag-scope-monitor"

#: How long a journey waits for the binding to put its question on the channel.
ASKED_SECONDS = 120


class QueuedSurface(TypedDict, total=False):
    """One planner surface as the bus reports it, narrowed to what a manager reads."""

    id: int
    kind: str
    message: str
    blocking: bool
    source: str
    correlation: str


class ChannelState(TypedDict):
    """`onemessagebus status surfaces`, narrowed to what these journeys read."""

    records: int
    waiting: list[QueuedSurface]
    abandoned: list[QueuedSurface]


def _channel(environment: dict[str, str], run: str) -> ChannelState:
    """The run's surfaces queue, read through the bus rather than the file behind it."""
    channel = Path(environment["ONEPIPELINE_RUNS_DIR"]) / run / "channel"
    if not channel.is_dir():
        return {"records": 0, "waiting": [], "abandoned": []}
    read = subprocess.run(
        ["onemessagebus", "status", "surfaces", "--config", str(BUS_CONFIG)]
        + ["--transport-dir", str(channel)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert read.returncode == 0, f"`onemessagebus status surfaces` over {channel}:\n{read.stderr}"
    # The bus owns this schema; `ChannelState` states the fields read here, each checked.
    state: Any = json.loads(read.stdout)[0]
    for record in [*state["waiting"], *state["abandoned"]]:
        assert isinstance(record.get("kind"), str), record
        assert isinstance(record.get("message"), str), record
        assert isinstance(record.get("blocking"), bool), record
    return cast(ChannelState, state)


def _surfaces(environment: dict[str, str], run: str) -> list[QueuedSurface]:
    """Every surface the run's channel is holding."""
    return _channel(environment, run)["waiting"]


def _held(environment: dict[str, str], run: str) -> list[QueuedSurface]:
    """Every surface the run's channel holds, waiting or abandoned, once each.

    The bus lists an abandoned question under `abandoned` and still under `waiting`,
    because it is still unread; one record is one surface.
    """
    state = _channel(environment, run)
    return list({one["id"]: one for one in [*state["waiting"], *state["abandoned"]]}.values())


def _records(environment: dict[str, str], run: str) -> set[int]:
    """The ids of every surface the run's channel holds now, to read what is raised after."""
    return {one["id"] for one in _held(environment, run)}


def _raised_since(environment: dict[str, str], run: str, before: set[int]) -> list[QueuedSurface]:
    """The surfaces the run's channel came to hold after it held `before`."""
    return [one for one in _held(environment, run) if one["id"] not in before]


#: The run whose directory the judge-side journeys drive against. One per worker the
#: module lands on, and every such journey shares its xdist group, so each reads the
#: records it appended rather than another's.
JUDGED_RUN = "monitor-judge-side"


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] One real launch per
# worker, because what the judge-side journeys below prove is this host's wiring over a
# run directory the engine itself made rather than one this suite assembled. It sits in
# `tests/e2e` for the reason this file's block over the whole-run journeys states: which Nx
# project owns that tree is not this change's to move.
@pytest.fixture(scope="module")
def judged_run(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[dict[str, str]]:
    """The environment a judge side runs under, over a run root the real engine made.

    Launched and settled through the real recipe with nothing watching it, so the run
    directory is the engine's own and the channel inside it is empty until a judge side
    writes to it. The run and the asker are named by the variables the engine exports to
    an observer member's judge side.
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
        yield {**environment, RUN_ID_ENV: JUDGED_RUN, ASKER_ENV: ASKER}
    finally:
        _just("stop", JUDGED_RUN, environment=environment, seconds=60)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _short_window(tmp_path: Path) -> Path:
    """This host's configuration with only its reply window shortened."""
    copy = tmp_path / "onemessagebus.yaml"
    text = BUS_CONFIG.read_text(encoding="utf-8")
    assert text.count(REPLY_WINDOW) == 1, f"{BUS_CONFIG} no longer states {REPLY_WINDOW!r}"
    copy.write_text(
        text.replace(REPLY_WINDOW, f"    reply_window_seconds: {SHORT_WINDOW_SECONDS}"),
        encoding="utf-8",
    )
    return copy


def _question(environment: dict[str, str], before: set[int]) -> QueuedSurface | None:
    """The completion question the binding raised after `before`, once it has."""
    asked = [
        one
        for one in _raised_since(environment, JUDGED_RUN, before)
        if one["kind"] == SURFACE_KIND_OF_A_COMPLETION_SCORE
    ]
    return asked[0] if asked else None


def _asked(
    environment: dict[str, str], before: set[int], running: subprocess.Popen[str]
) -> QueuedSurface:
    """Wait for the binding to ask, failing with what onejudge said if it never does."""
    limit = deadline(ASKED_SECONDS)
    while (question := _question(environment, before)) is None:
        if running.poll() is not None or time.monotonic() >= limit:
            running.kill()
            stdout, stderr = running.communicate()
            pytest.fail(
                f"the binding never asked this run's channel to score the bar (onejudge "
                f"exited {running.returncode}):\n{stderr}\n{stdout[-2000:]}\n"
                f"{_channel(environment, JUDGED_RUN)}"
            )
        time.sleep(0.2)
    return question


#: How `onejudge run` exits on a conversation it held to its end: 0 when every criterion
#: was scored met, 1 when one was scored unmet.
SCORED_MET, SCORED_UNMET = 0, 1


def _finished(held: Held, *, met: bool) -> None:
    """The conversation ended without error, and exited as its score says it should."""
    assert held.report is not None and held.report.get("error") is None, _unfinished(held)
    assert held.completed.returncode == (SCORED_MET if met else SCORED_UNMET), _unfinished(held)


def _unfinished(held: Held) -> str:
    """Why a conversation that should have finished did not: its error, verdicts, and decisions."""
    report = held.report or {}
    shown = {key: report.get(key) for key in ("error", "verdicts", "judge_decisions")}
    return f"{json.dumps(shown)}\n{held.completed.stderr}"


def _said_by(held: Held, role: str) -> list[str]:
    """What one party said in the conversation onejudge recorded, in order."""
    transcript = (held.report or {}).get("transcript", {}).get("messages", [])
    return [str(message["content"]) for message in transcript if message.get("role") == role]


# llmlint: ignore-block[e2e_not_mocked] Only the paid model is doubled in these journeys:
# a `Taken` turn runs oneharness's own `--mock-harness` responder in place of the provider
# process, the seam `tests/e2e/fake_backend.py` substitutes at for every launched run, and
# everything above it — oneharness, onejudge, the graph's judge argv, the bus, the engine's
# run directory — is the real installed release.
@pytest.mark.xdist_group("monitor-judge-side")
def test_a_taken_turn_is_answered_with_the_hold_raises_nothing_and_keeps_the_member_watching(
    judged_run: dict[str, str], tmp_path: Path
) -> None:
    """Behaviour 1: content is content, and the member is answered, lives, and queues nothing.

    Three things have to be true at once and none is enough alone. Nothing may reach the
    planner's queue for the turn — an observation belongs on it as a `finding` op the
    monitor issues itself. onejudge has to be handed a ruling it can act on, because the
    alternative is the exit status that killed this host's monitors: a non-completion
    settles nothing, so the monitor takes its next turn with the binding's answer as its
    user turn. And what the monitor said may not come back inside that answer.

    The conversation is two turns long, and the only surface it may raise is the one
    question its completion bar is owed when it ends — the reply window of the copy it runs
    under is shortened so that question is not waited on for fifty minutes.
    """
    before = _records(judged_run, JUDGED_RUN)

    held = monitor_conversation.hold(
        Conversation(Taken(SAID_ON_A_QUIET_TURN), _judge_command(), max_turns=2),
        judged_run,
        tmp_path / "conversation",
        config=_short_window(tmp_path),
    )

    _finished(held, met=False)
    assert _said_by(held, "assistant") == [SAID_ON_A_QUIET_TURN] * 2, (
        "the monitor did not take a second turn, so its first was not answered with a ruling "
        f"onejudge could act on: {held.report}"
    )
    decisions = [turn["decisions"][0] for turn in (held.report or {})["judge_decisions"]]
    assert [decision["decision"] for decision in decisions] == ["continue"], decisions
    assert decisions[0]["reason"].strip(), f"a bare non-completion reads as a refusal: {decisions}"
    answered = _said_by(held, "user")[1]
    assert NEXT_TURN_OPENS_AFTER_THE_HOLD in answered, answered
    assert "finding" in answered, (
        f"the answer does not name the one route a report reaches the planner by: {answered}"
    )
    assert SAID_ON_A_QUIET_TURN not in answered, f"the turn came back inside its answer: {answered}"
    raised = _raised_since(judged_run, JUDGED_RUN, before)
    assert [one["kind"] for one in raised] == [SURFACE_KIND_OF_A_COMPLETION_SCORE], (
        f"a taken turn put a surface on the planner's queue beside the bar's one question: {raised}"
    )


class Ruling(NamedTuple):
    """One shape a planner rules in, and the score it has to be relayed as.

    `envelope` is the ruling as the planner sends it; `value` and `reason` are what the
    relayed score has to carry — the ruling's `completion`, and its own `reason` or,
    where it gives none, its `message`.
    """

    envelope: dict[str, object]
    value: bool
    reason: str


RULINGS = (
    Ruling(
        {"completion": True, "reason": "the watch reported every drift"},
        value=True,
        reason="the watch reported every drift",
    ),
    Ruling(
        {"completion": False, "message": "two drifts went unreported"},
        value=False,
        reason="two drifts went unreported",
    ),
)


@pytest.mark.xdist_group("monitor-judge-side")
@pytest.mark.parametrize("ruling", RULINGS, ids=["met", "unmet-by-message"])
def test_the_bar_is_asked_once_non_blocking_and_the_ruling_is_the_score(
    judged_run: dict[str, str], tmp_path: Path, ruling: Ruling
) -> None:
    """Behaviours 4 and 5: the criterion is one non-blocking question; the ruling is the score.

    A one-turn conversation's first frame is the `judge` frame for its bar, which onejudge
    always asks. It is put to the planner on this run's channel, quoting the criterion, and
    the planner answers it the way any question here is answered — `just channel-reply
    --correlation` — under this host's own configuration and reply window.
    """
    before = _records(judged_run, JUDGED_RUN)
    running = monitor_conversation.start(
        Conversation(Taken(SAID_ON_A_QUIET_TURN), _judge_command(), max_turns=1),
        judged_run,
        tmp_path / "conversation",
    )
    question = _asked(judged_run, before, running)

    assert question["blocking"] is False, f"the bar was asked as a BLOCKING question: {question}"
    assert question.get("source") == "proposal", question
    assert monitor_conversation.DONE_WHEN in question["message"], question
    assert "NOT BLOCKED" in question["message"], question
    sent = channel_reply(
        JUDGED_RUN,
        judged_run,
        json.dumps({"version": 3, **ruling.envelope}),
        question["correlation"],
    )
    assert sent.returncode == 0, sent.stderr + sent.stdout
    held = monitor_conversation.finish(running)

    _finished(held, met=ruling.value)
    assert (held.report or {})["verdicts"] == [
        {
            "criterion": monitor_conversation.DONE_WHEN,
            "kind": "boolean",
            "verdict": {"value": ruling.value, "reason": ruling.reason},
        }
    ]
    raised = _raised_since(judged_run, JUDGED_RUN, before)
    assert [one["kind"] for one in raised] == [SURFACE_KIND_OF_A_COMPLETION_SCORE], raised


@pytest.mark.xdist_group("monitor-judge-side")
def test_no_ruling_scores_unsatisfied_and_the_ended_stream_abandons_the_question(
    judged_run: dict[str, str], tmp_path: Path
) -> None:
    """Behaviours 6 and 8: a window nobody answered in is `false`, and the question is let go.

    onejudge closes the frame stream after each frame, so once the unanswered score is
    written the serving session reaches the end of its stream — which marks the question
    it still holds abandoned rather than leaving a manager a question nobody is waiting on.
    """
    before = _records(judged_run, JUDGED_RUN)

    held = monitor_conversation.hold(
        Conversation(Taken(SAID_ON_A_QUIET_TURN), _judge_command(), max_turns=1),
        judged_run,
        tmp_path / "conversation",
        config=_short_window(tmp_path),
    )

    _finished(held, met=False)
    (verdict,) = (held.report or {})["verdicts"]
    assert verdict["verdict"]["value"] is False, f"an unanswered bar scored a pass: {verdict}"
    assert "nobody ruled" in verdict["verdict"]["reason"], verdict
    abandoned = [
        one for one in _channel(judged_run, JUDGED_RUN)["abandoned"] if one["id"] not in before
    ]
    assert [one["kind"] for one in abandoned] == [SURFACE_KIND_OF_A_COMPLETION_SCORE], abandoned


@pytest.mark.xdist_group("monitor-judge-side")
def test_a_session_reaching_its_bound_with_the_stream_open_leaves_the_question_counted(
    judged_run: dict[str, str], tmp_path: Path
) -> None:
    """Behaviour 8's other ending: a bound, not the stream, ends the session, and asks survive.

    onejudge closes its stream after every frame, so a session that ends on its bound with
    the stream still open is one only a replay can reach: the `judge` frame a real
    onejudge wrote, fed to the graph's argv from a pipe this journey holds open, with the
    session bounded by this host's variable. The question is then still counted — not
    abandoned — and the ending says why.
    """
    frame = monitor_conversation.recorded_frame("judge", tmp_path / "recorded", judged_run)
    scratch = tmp_path / "bounded"
    (scratch / "config").mkdir(parents=True)
    (scratch / "config" / "onemessagebus.yaml").symlink_to(_short_window(tmp_path))
    before = _records(judged_run, JUDGED_RUN)

    serving = subprocess.Popen(  # noqa: S603 - the graph's own judge command
        _judge_command(),
        cwd=scratch,
        env={**judged_run, SESSION_ENV: "1"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert serving.stdin is not None
    # llmlint: ignore[tests_mirror_real_usage] onejudge closes its stream after every frame,
    # so the one ending this journey owns — a bound reached with the stream open — is one
    # no real onejudge caller produces; the frame is the one a real onejudge wrote, replayed,
    # which is the arrangement this node's acceptance criteria name for behaviour 8.
    serving.stdin.write(frame)
    serving.stdin.flush()
    try:
        # A wait rather than `communicate`, which closes stdin, and a session whose stream
        # closes ends for that reason whatever its bound.
        serving.wait(timeout=e2e_timeout(60))
    finally:
        serving.kill()
    stdout, stderr = serving.communicate()

    assert serving.returncode == 0, stderr
    assert json.loads(stdout)["value"] is False, stdout
    assert "reached its 1-second bound" in stderr, stderr
    state = _channel(judged_run, JUDGED_RUN)
    assert not [one for one in state["abandoned"] if one["id"] not in before], state
    asked = [one for one in state["waiting"] if one["id"] not in before]
    assert [one["kind"] for one in asked] == [SURFACE_KIND_OF_A_COMPLETION_SCORE], state


#: A `user` frame, composed. The one op of the four no path of the installed onejudge
#: writes: its `docs/protocol.md` keeps `user` "for API compatibility and explicit
#: role-play calls", and the engine's loop asks `supervisor` instead — so, on the
#: manager's ruling, this frame is validated against `agent.onejudge-frame.user@<pin>`
#: from the bundle derived from the installed onejudge before it is offered.
COMPOSED_USER_FRAME = {
    "op": "user",
    "persona": monitor_conversation.PERSONA,
    "messages": [{"role": "user", "content": monitor_conversation.TASK}],
    "session": "dag-scope-1-monitor-user",
}


def _refused_by_onejudges_own_frame(
    judged_run: dict[str, str], tmp_path: Path, conversation: Conversation
) -> Held:
    return monitor_conversation.hold(
        conversation, judged_run, tmp_path / "conversation", config=_short_window(tmp_path)
    )


@pytest.mark.xdist_group("monitor-judge-side")
@pytest.mark.parametrize("op", ["respond", "assess", "numeric judge", "user"])
def test_every_frame_the_binding_has_no_entry_for_is_refused(
    judged_run: dict[str, str], tmp_path: Path, op: str
) -> None:
    """Behaviour 7: `respond`, `user`, `assess` and a numeric `judge` end the side with exit 2.

    Three are written by a real onejudge: `respond` by making the graph's argv its agent
    side as well, and `assess` and a numeric `judge` by asking for an assessment and a
    numeric criterion after the bar (whose question elapses in the shortened window). The
    fourth, `user`, is composed — see `COMPOSED_USER_FRAME` — and checked against the
    schema the bus resolves through this host's own link before the binding sees it, with
    a malformed copy refused by that check as the control.
    """
    before = _records(judged_run, JUDGED_RUN)
    if op == "user":
        schema = f"agent.onejudge-frame.user@{onejudge_bundle.protocol()}"
        checks = [
            subprocess.run(
                ["onemessagebus", "schema", "check", schema, "--config", str(BUS_CONFIG)],
                cwd=REPO_ROOT,
                env=judged_run,
                input=json.dumps(frame),
                text=True,
                capture_output=True,
                timeout=e2e_timeout(60),
                check=False,
            ).returncode
            for frame in (
                COMPOSED_USER_FRAME,
                {k: v for k, v in COMPOSED_USER_FRAME.items() if k != "persona"},
            )
        ]
        assert checks == [0, 1], checks
        # llmlint: ignore[tests_mirror_real_usage] No path of the installed onejudge writes a
        # `user` frame (its docs/protocol.md keeps the op for API compatibility and role-play
        # calls), so on the manager's ruling this one is composed and checked against the
        # installed release's own schema above before the judge side is handed it.
        refused = _judge_side(json.dumps(COMPOSED_USER_FRAME), judged_run, JUDGED_RUN)
        assert refused.returncode == REFUSED, refused.stdout + refused.stderr
        assert refused.stdout == "", refused.stdout
    else:
        conversation = {
            "respond": Conversation(
                Taken(SAID_ON_A_QUIET_TURN), _judge_command(), judge_answers_the_agent=True
            ),
            "assess": Conversation(
                Taken(SAID_ON_A_QUIET_TURN), _judge_command(), max_turns=1, assessment="Say more."
            ),
            "numeric judge": Conversation(
                Taken(SAID_ON_A_QUIET_TURN),
                _judge_command(),
                max_turns=1,
                evals=[{"criterion": "how thorough", "kind": "numeric", "scale": [1, 5]}],
            ),
        }[op]
        held = _refused_by_onejudges_own_frame(judged_run, tmp_path, conversation)
        asked = "respond" if op == "respond" else "judge" if op == "numeric judge" else op
        assert f"provider error ({asked}): provider exited with exit status: {REFUSED}" in (
            held.error()
        ), _unfinished(held)
    raised = [one["kind"] for one in _raised_since(judged_run, JUDGED_RUN, before)]
    # What a refusal leaves behind is onejudge's to decide, not the binding's: a refused
    # `respond` is the agent side's turn failing, which onejudge reports to the judge side
    # as a lost turn — and that is raised. Anything else a refusal leaves is only the bar's
    # question, asked before the frame that was refused.
    expected = {
        "respond": [SURFACE_KIND_OF_A_LOST_TURN],
        "user": [],
    }.get(op, [SURFACE_KIND_OF_A_COMPLETION_SCORE])
    assert raised == expected, f"a refused {op} frame left {raised} on the channel"


# llmlint: ignore-end[e2e_not_mocked]


#: How `personas/planner.yaml` states what `onepipeline surface --kind` takes.
PLANNER_PERSONA = REPO_ROOT / "personas" / "planner.yaml"
PLANNER_KIND_STATEMENT = re.compile(
    r"`--kind` is an open word: any kind matching `(?P<form>[^`]+)`"
)


class KindCase(NamedTuple):
    """A kind on one side of that statement, and whether the engine has to relay it."""

    kind: str
    relayed: bool


KINDS_AND_WHETHER_RELAYED = (
    KindCase("check-in", relayed=True),
    KindCase("planner-exceptions", relayed=True),
    KindCase("x" * 64, relayed=True),
    KindCase("x" * 65, relayed=False),
    KindCase("Planner-Exceptions", relayed=False),
    KindCase("planner_exceptions", relayed=False),
)


@pytest.mark.xdist_group("monitor-judge-side")
def test_the_planners_statement_of_surface_kinds_is_what_the_engine_relays(
    judged_run: dict[str, str],
) -> None:
    """The kind vocabulary the planner is told is the one the adopted engine implements.

    Every kind the statement admits is raised through the installed engine's own verb and
    read back off the run's channel under that kind; every kind it refuses is refused by
    the verb, with nothing appended.
    """
    stated = PLANNER_KIND_STATEMENT.search(" ".join(PLANNER_PERSONA.read_text("utf-8").split()))
    assert stated is not None, f"{PLANNER_PERSONA.name} no longer states what `--kind` takes"
    form = re.compile(stated.group("form"))
    for kind, relayed in KINDS_AND_WHETHER_RELAYED:
        assert bool(form.fullmatch(kind)) is relayed, f"the stated form misreads {kind!r}"
        before = _records(judged_run, JUDGED_RUN)
        raised = _raised_by_the_pacemakers_own_verb(judged_run, JUDGED_RUN, kind)
        assert (raised.returncode == 0) is relayed, f"{kind!r}: {raised.stderr}"
        queued = _raised_since(judged_run, JUDGED_RUN, before)
        expected = [(kind, PACEMAKER_REPORT)] if relayed else []
        assert [(one["kind"], one["message"].strip()) for one in queued] == expected, queued


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
#: a turn answered with a next instruction.
DECIDED_CONTINUE = "continue"


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
    # bus binding at all: its task tells it to raise its update with `onepipeline surface`,
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

    The launch is scripted so that every monitor turn is lost: the monitor's provider
    process fails under oneharness's own mock responder, the real oneharness reports the
    candidate failed, and the real onejudge the engine links reports the turn to its judge
    side as lost, naming the cause and the harness. What the wiring owes is two things the
    binding cannot owe alone. The member really dies — its judge side's exit reaches
    onejudge unswallowed, so the run is not left reporting a watcher that is not there. And
    each death is announced exactly once, as the bounded non-blocking line a manager reads,
    on this run's own channel.

    The driver relaunches an observer graph whose monitor died, a bounded number of times,
    so a run of lost turns records several lives; the claim is one surface per death,
    however many there were.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _launch_environment(tmp_path, oneharness_bin, "")
    # llmlint: ignore[e2e_not_mocked] Only the paid model's provider process is scripted.
    environment[OBSERVER_LOSES_ENV] = "1"
    printed = _launch_to_settlement(environment, LOST_RUN, tmp_path)

    events = _graph_events(Path(environment[GRAPH_STATE_ENV]))
    # onejudge ends a member whose judge side failed a lost turn with the turn's own
    # classified failure — the judge side's exit preserves it rather than replacing it —
    # so a death of a lost turn is a provider failure, and the surface is what says so.
    lost = [
        death
        for death in _of_the_monitor(events, MEMBER_DIED)
        if death["payload"].get("rule") == "provider-failure"
    ]
    deaths = _of_the_monitor(events, MEMBER_DIED)
    assert lost, (
        f"no `{MONITOR_MEMBER}` member died of a lost turn, so its judge side's failure "
        f"never reached onejudge; the graph recorded {json.dumps(deaths)}:\n{printed}"
    )
    # onejudge records no judge decision for a lost turn's exchange, only for a turn taken,
    # so what shows a lost turn was never answered as one is that no turn was answered at all.
    decided = [event["payload"].get("decision") for event in _of_the_monitor(events, JUDGE_DECIDED)]
    assert DECIDED_CONTINUE not in decided, (
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
        assert re.match(r"monitor turn failed: \S+ on codex\b", surface["message"]), (
            f"the surface does not name the cause and harness onejudge reported: {surface}"
        )
        assert len(surface["message"]) <= NAMED_FAILURE_LIMIT, surface["message"]


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[shell_test_tiers_stay_split]
