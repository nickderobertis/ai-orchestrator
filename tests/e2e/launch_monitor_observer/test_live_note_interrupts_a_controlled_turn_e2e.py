"""A live note interrupts a worker turn that can be interrupted, and a `turn-end` one waits.

`deliver: live`, a note's default, has the linked onejudge redirect the addressed party's
running turn through its turn-control socket: the turn is aborted, the work it did stands,
and its replacement opens on the note, which the run records as `turn: interrupted`.
`deliver: "turn-end"` never interrupts, so the note reaches the party once its turn ends
and is recorded `turn: turn-end`. `config/onepipeline.version` is what puts both in force
here (onepipeline#1048), so this drives them the way a manager meets them: a run launched
by the real `just orchestrate`, the note sent by the real `just channel-reply`, the run read
by the real `just status`, and each disposition read back off the run's own journal and the
receipt the reply recipe prints. The third delivery — a `live` note to a turn that cannot be
interrupted, which waits for its end — is
`tests/e2e/launch_monitor_observer/test_live_note_leaves_the_run_journalling_e2e.py`'s.

Everything between the recipes and the model is real — the engine, `oneagentgraph`, the
onejudge conversation and the `oneharness` CLI that opens the control socket and serves the
interrupt. The paid model is `tests/e2e/fake_backend.py`, whose marked worker turn here is
the pinned CLI's own mocked claude-code provider in its stdin control shape: held until an
interrupt ends it, or ending by itself a while after its prompt. Its turn log is the
double's record of what that turn was sent and how it ended; `tests/e2e/fake_codex.py`
stands in at the provider binary for the members that reach one there.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Two launches of the
installed engine, under a minute each, sharing the fixtures and stand-ins of the code-keyed
tier every other launch journey in this directory sits in; what they read — the engine pin,
the recipes, the wrapper scripts and the graphs — is what that tier's key already covers, so
a project of its own would be keyed on the same workspace twice.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] No marker selects a tier
here: the module carries only the xdist groups that keep each launch together, and the
split it declines is the project one, for the reason above.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, cast

import pytest
import short_state
from fake_backend import (
    AGENT_ACTIVITY_MARKER_ENV,
    AGENT_CONTROLLED_DELAY_ENV,
    AGENT_CONTROLLED_HOLD_ENV,
    AGENT_CONTROLLED_TURN_LOG_ENV,
    CONTROLLED_TURN_ENDED,
    CONTROLLED_TURN_INTERRUPTED,
    PROMPT_LOG_ENV,
)
from harness_indirections import established_indirections
from planner_channel import BUS_CONFIG
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

# What the recipes these journeys run leave in the temporary directory, by design or
# from a published tool they run, is this module's own and goes with it
# (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
INDIRECTION_CALLER = (
    "tests/e2e/launch_monitor_observer/test_live_note_interrupts_a_controlled_turn_e2e.py"
)

NODE_ID = "build"
TASK_MARKER = "Keep working until somebody redirects you."
NOTE = "the fixture this node reads moved"

#: How long the turn a `turn-end` note waits on runs before it ends by itself, after its
#: prompt arrives: long enough that the note is sent, and decided against interrupting,
#: well inside it.
SELF_ENDING_TURN_MS = 15000

LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: A journal record, in the fields these journeys read. `onepipeline` pins the whole record.
Event = dict[str, object]


class Delivery(NamedTuple):
    """One way of sending the note, and the turn it is held to reaching."""

    #: The run's name, which is also the xdist group its launch keeps together.
    run: str
    #: Whether the worker's turn waits for an interrupt rather than ending by itself.
    hold: bool
    #: The note's fields beyond its addressee and text.
    fields: dict[str, object]
    #: The `turn` its delivery is recorded with.
    turn: str


INTERRUPTING = Delivery(run="live-note-interrupts", hold=True, fields={}, turn="interrupted")
AT_TURN_END = Delivery(
    run="turn-end-note-waits",
    hold=False,
    # Bounded far past the turn, so the bound is accepted and never reached.
    fields={"deliver": "turn-end", "delivery_timeout_seconds": 600},
    turn="turn-end",
)


class Journey(NamedTuple):
    #: What `just channel-reply` returned for the note.
    sent: subprocess.CompletedProcess[str]
    #: The first line of `just status` for the run, read with the turn still in flight.
    status: str
    #: The run's journal once the node settled.
    journal: list[Event]
    #: The double's record of the controlled turn: each frame it was sent, and its ends.
    turn_log: list[str]
    #: Every harness turn the double served, as `fake_backend.RecordedTurn`s.
    turns: list[dict[str, object]]
    #: The engine's outcome for the note's envelope on `command-outcomes`.
    outcome: dict[str, object]


def _environment(root: Path, oneharness_bin: str, delivery: Delivery) -> dict[str, str]:
    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = f"e2e-{delivery.run}"
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment[AGENT_ACTIVITY_MARKER_ENV] = TASK_MARKER
    environment[AGENT_CONTROLLED_TURN_LOG_ENV] = str(root / "controlled-turn.log")
    environment[PROMPT_LOG_ENV] = str(root / "turns.jsonl")
    if delivery.hold:
        environment[AGENT_CONTROLLED_HOLD_ENV] = "1"
    else:
        environment[AGENT_CONTROLLED_DELAY_ENV] = str(SELF_ENDING_TURN_MS)
    return environment


def _plan(root: Path, delivery: Delivery) -> Path:
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Send a note to a worker turn that is still running"},
                "name": delivery.run,
                "tasks": [
                    {
                        "id": NODE_ID,
                        "persona": "engineer",
                        "task": (
                            f"## What\n\n{TASK_MARKER}\n\n"
                            "## Why\n\nHow a note reaches a running turn is the subject.\n\n"
                            "## Acceptance criteria\n\n- The dispatch reports.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


def _journal(root: Path, delivery: Delivery) -> list[Event]:
    written = root / "runs" / delivery.run / "events.jsonl"
    if not written.exists():
        return []
    # `onepipeline` writes this file; its schema is the engine's, and the cast says so.
    return [
        cast(Event, json.loads(line))
        for line in written.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _until(what: str, condition: Callable[[], bool], seconds: float) -> None:
    deadline = time.monotonic() + e2e_timeout(seconds)
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.25)


def _drive(root: Path, oneharness_bin: str, delivery: Delivery) -> Iterator[Journey]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _environment(root, oneharness_bin, delivery)
    turn_log = Path(environment[AGENT_CONTROLLED_TURN_LOG_ENV])

    def just(*arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["just", *arguments],
            cwd=REPO_ROOT,
            env=environment,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
        )

    launched = just(
        "orchestrate", project_from_plan(_plan(root, delivery), delivery.run), "--detach"
    )
    try:
        assert launched.returncode == 0, launched.stdout + launched.stderr
        # The turn is in flight once the double holds its prompt frame and has ended none.
        _until(
            "the worker's controlled turn to take its prompt",
            lambda: any(TASK_MARKER in line for line in _lines(turn_log)),
            180,
        )
        assert CONTROLLED_TURN_ENDED not in _lines(turn_log), _lines(turn_log)
        # Beside the read below rather than ahead of it: a `turn-end` note is decided only
        # once the turn ends, and the reply waits on that decision.
        sending = ThreadPoolExecutor(max_workers=1)
        sending_note = sending.submit(
            just,
            "channel-reply",
            delivery.run,
            stdin=json.dumps(
                {
                    "version": 3,
                    "author": "planner",
                    "commands": [
                        {
                            "op": "note",
                            "id": NODE_ID,
                            "addressee": "worker",
                            "text": NOTE,
                            **delivery.fields,
                        }
                    ],
                }
            ),
        )
        sending.shutdown(wait=False)
        status = just("status", delivery.run)
        sent = sending_note.result(timeout=e2e_timeout(300))
        assert sent.returncode == 0, sent.stdout + sent.stderr
        _until(
            "the node to settle",
            lambda: any(event.get("kind") == "node-settled" for event in _journal(root, delivery)),
            300,
        )
        reply = json.loads(sent.stdout)["reply"]
        streamed = subprocess.run(
            [
                "onemessagebus",
                "subscribe",
                "command-outcomes",
                "--until",
                json.dumps({"field": "id", "equals": reply}),
                "--timeout",
                "60",
                "--config",
                str(BUS_CONFIG),
                "--transport-dir",
                str(root / "runs" / delivery.run / "channel"),
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )
        assert streamed.returncode == 0, streamed.stderr
        # The stream ends on the first outcome for that id, which for a note nothing
        # withdrew is its only one.
        yield Journey(
            # The bus wraps each streamed record with its position in the queue.
            outcome=json.loads(streamed.stdout.splitlines()[-1])["record"],
            sent=sent,
            status=(status.stdout.splitlines() or [""])[0],
            journal=_journal(root, delivery),
            turn_log=_lines(turn_log),
            turns=[json.loads(line) for line in _lines(Path(environment[PROMPT_LOG_ENV]))],
        )
    finally:
        just("stop", delivery.run)


@pytest.fixture(scope="module")
def interrupting(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[Journey]:
    yield from _drive(tmp_path_factory.mktemp(INTERRUPTING.run), oneharness_bin, INTERRUPTING)


@pytest.fixture(scope="module")
def at_turn_end(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Journey]:
    yield from _drive(tmp_path_factory.mktemp(AT_TURN_END.run), oneharness_bin, AT_TURN_END)


def _delivered(journey: Journey) -> tuple[int, dict[str, object]]:
    """The note's committed delivery: where it sits in the journal, and its record."""
    for index, event in enumerate(journey.journal):
        if event.get("kind") != "edit-committed":
            continue
        payload = cast(dict[str, object], event["payload"])
        if cast(dict[str, object], payload.get("command", {})).get("op") != "note":
            continue
        operations = cast(list[dict[str, object]], payload["operations"])
        assert operations[0]["kind"] == "note-delivered", payload
        return index, operations[0]
    raise AssertionError([event.get("kind") for event in journey.journal])


def _outcome_turn(journey: Journey) -> object:
    """The `turn` the channel's outcome for the reply records on the note's command.

    The receipt `just channel-reply` prints says the envelope was applied and names the id
    it went in under; the per-command result, `turn` among it, is the engine's outcome for
    that id on the run's `command-outcomes` queue, read through the bus's own `subscribe`.
    """
    printed = journey.sent.stdout.splitlines()
    assert len(printed) == 1, journey.sent.stdout
    receipt = json.loads(printed[0])
    assert (receipt["state"], receipt["commands"]) == ("applied", "applied"), receipt
    assert journey.outcome["id"] == receipt["reply"], (journey.outcome, receipt)
    results = cast(list[dict[str, object]], journey.outcome["results"])
    assert [(result["op"], result["outcome"]) for result in results] == [("note", "applied")], (
        journey.outcome
    )
    return results[0].get("turn")


def _frames(journey: Journey) -> list[str]:
    """The `user` frames the double's controlled turn was sent, in order."""
    return [line for line in journey.turn_log if line.startswith("{") and '"user"' in line]


@pytest.mark.xdist_group(INTERRUPTING.run)
def test_a_live_note_is_recorded_as_interrupting_the_turn(interrupting: Journey) -> None:
    """The journal and the reply's receipt both say the note interrupted the worker's turn."""
    _, delivered = _delivered(interrupting)
    assert delivered["text"] == NOTE, delivered
    assert delivered["reached"] == "worker", delivered
    assert delivered["turn"] == INTERRUPTING.turn, delivered
    assert _outcome_turn(interrupting) == INTERRUPTING.turn, interrupting.sent.stdout


@pytest.mark.xdist_group(INTERRUPTING.run)
def test_the_held_turn_was_aborted_and_never_completed(interrupting: Journey) -> None:
    """The double's only turn end before the note is the abort the interrupt sent it."""
    log = interrupting.turn_log
    assert CONTROLLED_TURN_INTERRUPTED in log, log
    first_end = log.index(CONTROLLED_TURN_ENDED)
    assert log[first_end - 1] == CONTROLLED_TURN_INTERRUPTED, log
    aborts = [line for line in log[:first_end] if '"control_request"' in line]
    assert len(aborts) == 1 and '"interrupt"' in aborts[0], log


@pytest.mark.xdist_group(INTERRUPTING.run)
def test_the_replacement_turn_opens_on_the_note_once(interrupting: Journey) -> None:
    """The worker's next turn is handed the note, exactly once, after the abort."""
    frames = _frames(interrupting)
    assert len(frames) == 2, interrupting.turn_log
    replacement = frames[1]
    assert interrupting.turn_log.index(replacement) > interrupting.turn_log.index(
        CONTROLLED_TURN_INTERRUPTED
    ), interrupting.turn_log
    text = "".join(
        cast(str, block["text"]) for block in json.loads(replacement)["message"]["content"]
    )
    assert text.count(NOTE) == 1, text


@pytest.mark.xdist_group(INTERRUPTING.run)
def test_the_status_view_reports_the_run_live_while_the_turn_is_held(
    interrupting: Journey,
) -> None:
    """A manager reading the run with the worker's turn in flight sees it driven."""
    assert INTERRUPTING.run in interrupting.status, interrupting.status
    assert "ACTIVE" in interrupting.status.split(), interrupting.status


@pytest.mark.xdist_group(AT_TURN_END.run)
def test_a_turn_end_note_is_recorded_as_reaching_the_turns_end(at_turn_end: Journey) -> None:
    """The journal and the reply's receipt both say `turn-end`, never `interrupted`."""
    _, delivered = _delivered(at_turn_end)
    assert delivered["text"] == NOTE, delivered
    assert delivered["reached"] == "worker", delivered
    assert delivered["turn"] == AT_TURN_END.turn, delivered
    assert _outcome_turn(at_turn_end) == AT_TURN_END.turn, at_turn_end.sent.stdout


@pytest.mark.xdist_group(AT_TURN_END.run)
def test_a_turn_end_note_never_interrupts_the_turn_it_waits_on(at_turn_end: Journey) -> None:
    """The turn that could have been interrupted ran to its own end, and took no abort."""
    log = at_turn_end.turn_log
    assert CONTROLLED_TURN_INTERRUPTED not in log, log
    assert not any('"control_request"' in line for line in log), log
    assert len(_frames(at_turn_end)) == 1, log
    assert log.count(CONTROLLED_TURN_ENDED) == 1, log


@pytest.mark.xdist_group(AT_TURN_END.run)
def test_a_turn_end_note_reaches_the_worker_after_its_turn_completes(
    at_turn_end: Journey,
) -> None:
    """The delivery follows the held turn's end, and the worker's next turn carries it."""
    committed, _ = _delivered(at_turn_end)
    first_turn = next(
        cast(dict[str, object], event["payload"])
        for event in at_turn_end.journal
        if event.get("kind") == "turn-completed"
        and cast(dict[str, object], event.get("labels", {})).get("node") == NODE_ID
    )
    assert _instant(cast(str, first_turn["finished_at"])) <= _instant(
        cast(str, at_turn_end.journal[committed]["ts"])
    ), (first_turn, at_turn_end.journal[committed])
    worker_prompts = [
        cast(str, turn["prompt"])
        for turn in at_turn_end.turns
        if "/members/worker/" in cast(str, turn["config"] or "")
        and not cast(str, turn["config"]).endswith(".judge.toml")
    ]
    assert len(worker_prompts) >= 2, worker_prompts
    assert TASK_MARKER in worker_prompts[0], worker_prompts
    assert worker_prompts[1].count(NOTE) == 1, worker_prompts[1]


def _instant(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)
