"""A live note waiting on a busy turn leaves the run recording everything else.

A note sent to a running dispatch waits for the conversation to take it, and a harness
that cannot be steered mid-turn takes it only when its current turn ends. The engine used
to wait on that answer on the run's single writer, so for as long as the turn ran the
run's journal took nothing: no heartbeat, no turn activity, no checkpoint — and a healthy
run read as an abandoned one, which invites exactly the wrong intervention
(https://github.com/nickderobertis/onepipeline/issues/447). The fix —
https://github.com/nickderobertis/onepipeline/pull/573 — delivers the note off that writer.
`config/onepipeline.version` is what puts it in force here, so this drives it the way a
manager meets it: a run launched by the real `just orchestrate`, a note sent by the real
`just channel-reply`, and the run read back through the real `just status` and its own
journal while the note waits.

Everything between the recipes and the model is real — the engine, `oneagentgraph`, the
onejudge conversation, the `oneharness` CLI and its stream. The paid model is
`tests/e2e/fake_backend.py`, whose worker turn here reports a tool call every
`ACTIVITY_INTERVAL_MS` for `ACTIVITY_CALLS` calls before answering, through the real
oneharness stream, and takes a note only when that turn ends; `tests/e2e/fake_codex.py`
stands in at the provider binary for the members that reach one there.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] One launch of the
installed engine, about forty seconds, sharing the fixtures and stand-ins of the
code-keyed tier every other launch journey in this directory sits in; what it reads —
the engine pin, the recipes, the wrapper scripts and the graphs — is what that tier's key
already covers, so a project of its own would be keyed on the same workspace twice.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] No marker selects a tier
here: the module carries only the xdist group that keeps its one launch together, and the
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
from pathlib import Path
from typing import NamedTuple, cast

import pytest
import short_state
from fake_backend import (
    AGENT_ACTIVITY_ENV,
    AGENT_ACTIVITY_INTERVAL_ENV,
    AGENT_ACTIVITY_MARKER_ENV,
)
from harness_indirections import established_indirections
from project_fixtures import project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"
INDIRECTION_CALLER = "tests/e2e/test_live_note_leaves_the_run_journalling_e2e.py"

RUN_NAME = "live-note-busy-turn"
NODE_ID = "build"
TASK_MARKER = "Keep working until the turn is done."
NOTE = "the fixture this node reads moved"

#: How long the worker's first turn is busy: this many tool calls, this far apart. Long
#: enough that a note sent once the turn has reported its first call waits through several
#: heartbeats and several more calls before the turn ends.
ACTIVITY_CALLS = 12
ACTIVITY_INTERVAL_MS = 2500
#: How long a member lets its supervision go unconfirmed before it says so, in seconds.
#: Short, so the member publishes `member-heartbeat` every few seconds and a journey can
#: watch the run keep recording one while the note waits. `oneagentgraph`'s own variable.
HEARTBEAT_BOUND_SECONDS = "8"

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


class Counted(NamedTuple):
    """How many of one record the journal held at two moments."""

    #: When the note was sent.
    when_sent: int
    #: Once the run had gone on recording past it with the note still undelivered.
    while_waiting: int


class WhileWaiting(NamedTuple):
    """What the run recorded and reported between the note's sending and its delivery."""

    heartbeats: Counted
    activity: Counted
    #: The first line of `just status` for the run, read with the note still waiting.
    status: str


class Journey(NamedTuple):
    sent: subprocess.CompletedProcess[str]
    waiting: WhileWaiting
    journal: list[Event]


def _environment(root: Path, oneharness_bin: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = "e2e-live-note-busy-turn"
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    environment["ONEAGENTGRAPH_HEARTBEAT_TIMEOUT"] = HEARTBEAT_BOUND_SECONDS
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment[AGENT_ACTIVITY_ENV] = str(ACTIVITY_CALLS)
    environment[AGENT_ACTIVITY_INTERVAL_ENV] = str(ACTIVITY_INTERVAL_MS)
    environment[AGENT_ACTIVITY_MARKER_ENV] = TASK_MARKER
    return environment


def _plan(root: Path) -> Path:
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Send a live note to a turn that is still working"},
                "name": RUN_NAME,
                "tasks": [
                    {
                        "id": NODE_ID,
                        "persona": "engineer",
                        "task": (
                            f"## What\n\n{TASK_MARKER}\n\n"
                            "## Why\n\nThe run's record while a note waits is the subject.\n\n"
                            "## Acceptance criteria\n\n- The dispatch reports.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


def _journal(root: Path) -> list[Event]:
    written = root / "runs" / RUN_NAME / "events.jsonl"
    if not written.exists():
        return []
    # `onepipeline` writes this file; its schema is the engine's, and the cast says so.
    return [
        cast(Event, json.loads(line))
        for line in written.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _count(journal: list[Event], kind: str) -> int:
    return sum(
        1
        for event in journal
        if event.get("kind") == kind
        and cast(dict[str, object], event.get("labels", {})).get("node") == NODE_ID
    )


def _note_recorded(journal: list[Event]) -> bool:
    return any(
        event.get("kind") == "edit-committed"
        and cast(
            dict[str, object], cast(dict[str, object], event["payload"]).get("command", {})
        ).get("op")
        == "note"
        for event in journal
    )


def _until(what: str, condition: Callable[[], bool], seconds: float) -> None:
    deadline = time.monotonic() + e2e_timeout(seconds)
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.5)


@pytest.fixture(scope="module")
def journey(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Journey]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("live-note-busy-turn")
    environment = _environment(root, oneharness_bin)

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

    launched = just("orchestrate", project_from_plan(_plan(root), RUN_NAME), "--detach")
    try:
        assert launched.returncode == 0, launched.stdout + launched.stderr
        _until(
            "the worker's turn to report its first tool call",
            lambda: _count(_journal(root), "turn-activity") >= 1,
            180,
        )
        before = _journal(root)
        # Sent beside the reads below rather than ahead of them: the reply waits on the
        # driver's decision about the note for up to its reply timeout, and the note is
        # only decided once the busy turn ends — the window those reads are about.
        sending = ThreadPoolExecutor(max_workers=1)
        sending_note = sending.submit(
            just,
            "channel-reply",
            RUN_NAME,
            stdin=json.dumps(
                {
                    "version": 3,
                    "author": "planner",
                    "commands": [
                        {"op": "note", "id": NODE_ID, "addressee": "worker", "text": NOTE}
                    ],
                }
            ),
        )
        sending.shutdown(wait=False)
        heartbeats = _count(before, "member-heartbeat")
        activity = _count(before, "turn-activity")

        def recorded_while_waiting() -> bool:
            journal = _journal(root)
            # The defect this exists for reads exactly here: a writer waiting on the note
            # appends nothing until the turn ends, and then the held heartbeats and tool
            # calls arrive in one flush behind the note's own record.
            assert not _note_recorded(journal), (
                "the note was recorded before the run journalled two more heartbeats and two "
                "more tool calls: the run's journal took nothing while the note waited on the "
                f"busy turn\n{[event.get('kind') for event in journal]}"
            )
            return (
                _count(journal, "member-heartbeat") >= heartbeats + 2
                and _count(journal, "turn-activity") >= activity + 2
            )

        _until(
            "the run to go on recording heartbeats and turn activity", recorded_while_waiting, 60
        )
        status = just("status", RUN_NAME)
        during = _journal(root)
        waiting = WhileWaiting(
            heartbeats=Counted(heartbeats, _count(during, "member-heartbeat")),
            activity=Counted(activity, _count(during, "turn-activity")),
            status=(status.stdout.splitlines() or [""])[0],
        )
        assert not _note_recorded(during), (
            "the busy turn ended before the run was read while the note waited; lengthen the turn"
        )
        sent = sending_note.result(timeout=e2e_timeout(300))
        assert sent.returncode == 0, sent.stdout + sent.stderr
        _until(
            "the node to settle",
            lambda: any(event.get("kind") == "node-settled" for event in _journal(root)),
            300,
        )
        yield Journey(sent=sent, waiting=waiting, journal=_journal(root))
    finally:
        just("stop", RUN_NAME)


@pytest.mark.xdist_group("live-note-busy-turn")
def test_the_run_goes_on_journalling_heartbeats_and_turn_activity_while_the_note_waits(
    journey: Journey,
) -> None:
    """Both records the frozen writer used to hold back keep arriving during the wait."""
    for counted in (journey.waiting.heartbeats, journey.waiting.activity):
        assert counted.while_waiting >= counted.when_sent + 2, journey.waiting


@pytest.mark.xdist_group("live-note-busy-turn")
def test_the_status_view_reports_the_run_live_while_the_note_waits(journey: Journey) -> None:
    """A manager reading the run while the note waits sees it driven, not abandoned."""
    assert RUN_NAME in journey.waiting.status, journey.waiting.status
    assert "ACTIVE" in journey.waiting.status.split(), journey.waiting.status


@pytest.mark.xdist_group("live-note-busy-turn")
def test_the_note_is_delivered_when_the_turn_ends_and_then_shown(journey: Journey) -> None:
    """Once the busy turn ends, the delivery is recorded, and then its presentation."""
    kinds = [event.get("kind") for event in journey.journal]
    committed = next(
        (
            index
            for index, event in enumerate(journey.journal)
            if event.get("kind") == "edit-committed" and _note_recorded([event])
        ),
        None,
    )
    assert committed is not None, kinds
    payload = cast(dict[str, object], journey.journal[committed]["payload"])
    operations = cast(list[dict[str, object]], payload["operations"])
    assert operations[0]["kind"] == "note-delivered", payload
    assert operations[0]["text"] == NOTE, payload
    assert operations[0]["reached"] == "worker", payload

    shown = next(
        (
            index
            for index, event in enumerate(journey.journal)
            if event.get("kind") == "note-shown"
            and cast(dict[str, object], event["payload"]).get("text") == NOTE
        ),
        None,
    )
    assert shown is not None and committed < shown, kinds
    # And the delivery waited for the busy turn to end, rather than overtaking it.
    first_turn_ended = next(
        index
        for index, event in enumerate(journey.journal)
        if event.get("kind") == "turn-completed"
        and cast(dict[str, object], event.get("labels", {})).get("node") == NODE_ID
    )
    assert first_turn_ended < committed, kinds
