"""The supervisory tier's bounded local capture and its driver-state derivation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from orchestrator.supervisory import (
    CAPTURE_DIR,
    CAPTURE_SCHEMA_VERSION,
    MAX_CAPTURED_TURN_CHARS,
    MAX_CAPTURED_TURNS,
    CaptureError,
    capture_path,
    close_capture,
    driver_indicator,
    driver_state,
    history_write_failure,
    load_captures,
    open_capture,
    record_turn,
    validate_agent_role,
)


def _launch(run_dir: Path, *, pid: int | None = None, status: str = "running") -> None:
    """Record the orchestrator launch a driver observation reads, as dispatch writes it."""
    directory = run_dir / "orchestrator"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "status.json").write_text(
        json.dumps(
            {
                "status": status,
                "pid": os.getpid() if pid is None else pid,
                "host": __import__("socket").gethostname(),
                "started": "2026-08-06T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def _round(run_dir: Path, number: int, **files: str) -> Path:
    round_dir = run_dir / f"round-{number:02d}"
    round_dir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (round_dir / f"{name}.json").write_text(body, encoding="utf-8")
    return round_dir


def test_capture_round_trips_metadata_timing_and_a_bounded_transcript(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    open_capture(
        run_dir,
        session="check-in-run-1-2",
        agent_role="check-in",
        persona="check-in",
        round_number=2,
        started_at=1_000.0,
    )
    for index in range(MAX_CAPTURED_TURNS + 3):
        assert record_turn(run_dir, "check-in-run-1-2", f"turn {index} " + "x" * 4_000)
    assert close_capture(
        run_dir,
        "check-in-run-1-2",
        status="failed",
        harness="codex",
        history_failure="oneharness: new history run lacks complete v1.0 telemetry",
        finished_at=2_000.0,
    )

    captured = load_captures(run_dir)
    assert [item.session for item in captured] == ["check-in-run-1-2"]
    capture = captured[0]
    assert (capture.agent_role, capture.persona, capture.round) == ("check-in", "check-in", 2)
    assert capture.status == "failed"
    assert capture.harness == "codex"
    assert capture.history_failure is not None
    assert "lacks complete v1.0 telemetry" in capture.history_failure
    assert capture.started_at.startswith("1970-01-01T00:16:40")
    assert capture.finished_at is not None
    # Bounded on both axes: the newest few turns, each cut to a readable head.
    assert len(capture.turns) == MAX_CAPTURED_TURNS
    assert capture.turns[0]["text"].startswith("turn 3 ")
    assert all(len(turn["text"]) <= MAX_CAPTURED_TURN_CHARS for turn in capture.turns)


def test_retrying_a_round_reuses_its_capture_and_keeps_the_refusal_it_recorded(
    tmp_path: Path,
) -> None:
    """A file per attempt would grow without bound; a lost refusal would lie."""
    run_dir = tmp_path / "run-retry"
    open_capture(
        run_dir,
        session="check-in-run-retry-1",
        agent_role="check-in",
        round_number=1,
        started_at=1_000.0,
    )
    record_turn(run_dir, "check-in-run-retry-1", "first attempt", at=1_001.0)
    close_capture(
        run_dir,
        "check-in-run-retry-1",
        status="failed",
        history_failure="new history run lacks complete v1.0 telemetry",
        finished_at=1_002.0,
    )

    open_capture(
        run_dir,
        session="check-in-run-retry-1",
        agent_role="check-in",
        round_number=1,
        started_at=9_999.0,
    )
    retried = load_captures(run_dir)
    assert len(retried) == 1
    capture = retried[0]
    # The scope's own start stands: the round's check-in began when it began.
    assert capture.started_at.startswith("1970-01-01T00:16:40")
    assert capture.status == "running"
    assert capture.finished_at is None
    assert [turn["text"] for turn in capture.turns] == ["first attempt"]
    assert capture.history_failure is not None


def test_capture_writes_are_silent_when_there_is_nothing_to_write(tmp_path: Path) -> None:
    """A capture is an addition to a dispatch; a missing one must not fail the dispatch."""
    run_dir = tmp_path / "run-2"
    assert record_turn(run_dir, "orchestrator-run-2", "a turn nobody opened a capture for") is False
    assert close_capture(run_dir, "orchestrator-run-2", status="completed") is False
    open_capture(run_dir, session="orchestrator-run-2", agent_role="orchestrator")
    assert record_turn(run_dir, "orchestrator-run-2", "   ") is False


def test_a_capture_this_scheme_did_not_write_is_skipped_not_served(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-3"
    open_capture(run_dir, session="orchestrator-run-3", agent_role="orchestrator")
    directory = run_dir / CAPTURE_DIR
    (directory / "not-json.json").write_text("{", encoding="utf-8")
    (directory / "wrong-version.json").write_text(
        json.dumps(
            {
                "schema_version": CAPTURE_SCHEMA_VERSION + 1,
                "session": "future",
                "agent_role": "orchestrator",
                "started_at": "2026-08-06T10:00:00+00:00",
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    (directory / "foreign-role.json").write_text(
        json.dumps(
            {
                "schema_version": CAPTURE_SCHEMA_VERSION,
                "session": "foreign",
                "agent_role": "not-a-dispatched-role",
                "started_at": "2026-08-06T10:00:00+00:00",
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    (directory / "undatable.json").write_text(
        json.dumps(
            {
                "schema_version": CAPTURE_SCHEMA_VERSION,
                "session": "undatable",
                "agent_role": "check-in",
                "started_at": "whenever",
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    (directory / "no-role.json").write_text(
        json.dumps(
            {
                "schema_version": CAPTURE_SCHEMA_VERSION,
                "session": "roleless",
                "started_at": "2026-08-06T10:00:00+00:00",
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    assert [item.session for item in load_captures(run_dir)] == ["orchestrator-run-3"]
    assert load_captures(tmp_path / "never-launched") == []


def test_a_session_name_the_filesystem_cannot_carry_is_refused(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-4"
    for name in ("", "../escape", "with space", "-leading", "a" * 251):
        with pytest.raises(CaptureError):
            capture_path(run_dir, name)
    # A live turn is never failed by one, though: capture is an addition to a
    # dispatch, and the dispatch is the thing worth keeping.
    assert record_turn(run_dir, "a" * 251, "a turn") is False
    assert close_capture(run_dir, "a" * 251, status="failed") is False


def test_a_role_no_served_span_could_be_attributed_to_is_refused_on_the_way_in(
    tmp_path: Path,
) -> None:
    """Rejected where it is written, not only where it is read.

    A reader that skips an unknown role turns a bad write into a capture that is
    simply missing from the timeline — the invisibility this whole scheme exists to
    end — so the caller learns at the boundary instead.
    """
    run_dir = tmp_path / "run-role"
    for role in ("", "planner", "Orchestrator"):
        with pytest.raises(CaptureError):
            open_capture(run_dir, session="orchestrator-run-role", agent_role=role)
    assert load_captures(run_dir) == []
    # And a value no annotation constrains, because the record is JSON a later
    # release could hand this the wrong shape of.
    for value in (7, None, ["orchestrator"]):
        with pytest.raises(CaptureError):
            validate_agent_role(value)


@pytest.mark.parametrize(
    "text",
    [
        "oneharness: could not write history: new history run lacks complete v1.0 telemetry",
        "harness codex cannot write v1.0 history telemetry",
        "new history record lacks complete v0.3 telemetry",
    ],
)
def test_the_harness_history_write_failure_is_recognised_from_a_log_tail(text: str) -> None:
    found = history_write_failure(f"unrelated line\n{text}\n")
    assert found is not None
    assert found.endswith("telemetry")


def test_an_ordinary_harness_log_reports_no_history_write_failure() -> None:
    assert history_write_failure(None) is None
    assert history_write_failure("") is None
    assert history_write_failure("harness failed (auth): login required") is None


def test_a_run_that_was_never_launched_has_no_driver_to_report(tmp_path: Path) -> None:
    assert driver_state(tmp_path / "unlaunched") is None
    assert driver_indicator(tmp_path / "unlaunched") is None


def test_the_driver_phase_follows_what_the_run_itself_recorded(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-5"
    _launch(run_dir)
    starting = driver_state(run_dir)
    assert starting is not None
    assert (starting.phase, starting.round, starting.alive) == ("starting", None, True)

    _round(run_dir, 1, status=json.dumps({"status": "abandoned", "pid": os.getpid()}))
    driving = driver_state(run_dir)
    assert driving is not None
    # The round is not running and has recorded no result: the driver is between
    # commands, which is neither executing run-plan nor reviewing anything.
    assert (driving.phase, driving.round) == ("driving-round", 1)

    _round(
        run_dir,
        1,
        status=json.dumps(
            {
                "status": "running",
                "pid": os.getpid(),
                "host": __import__("socket").gethostname(),
            }
        ),
    )
    executing = driver_state(run_dir)
    assert executing is not None
    assert (executing.phase, executing.round) == ("executing-run-plan", 1)

    _round(run_dir, 1, result=json.dumps({"ok": True, "state": "complete", "results": {}}))
    reviewing = driver_state(run_dir)
    assert reviewing is not None
    assert (reviewing.phase, reviewing.round) == ("reviewing-results", 1)

    channel = run_dir / "channel"
    channel.mkdir(parents=True, exist_ok=True)
    (channel / "planner-pending.json").write_text(json.dumps({"kind": "milestone"}), "utf-8")
    surfacing = driver_state(run_dir)
    assert surfacing is not None
    assert (surfacing.phase, surfacing.round) == ("surfacing", 1)

    (run_dir / "orchestrator" / "report.json").write_text('{"completed": true}', encoding="utf-8")
    finished = driver_state(run_dir)
    assert finished is not None
    assert (finished.phase, finished.alive, finished.dead) == ("finished", False, False)
    # A finished driver says nothing in the planner views: the run's own row already
    # says how it ended, and a line per settled run buries the one that is dying.
    assert driver_indicator(run_dir) is None


def test_a_dead_driver_is_marked_explicitly_with_its_phase_and_activity_age(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-6"
    # A pid this host can prove is gone: pid 2**22 - 1 is above every default pid_max.
    _launch(run_dir, pid=4_194_303)
    _round(run_dir, 3, status=json.dumps({"status": "abandoned", "pid": 4_194_303}))
    open_capture(run_dir, session=f"orchestrator-{run_dir.name}", agent_role="orchestrator")
    record_turn(run_dir, f"orchestrator-{run_dir.name}", "driving round 3", at=1_000.0)

    state = driver_state(run_dir)
    assert state is not None
    assert state.alive is False
    assert state.dead is True
    assert state.phase == "driving-round"
    assert state.round == 3
    assert state.last_activity_at is not None
    line = state.describe(now=(state.last_activity_at or 0.0) + 125)
    assert "DRIVER DEAD" in line
    assert "pid 4194303 is gone" in line
    assert "phase driving-round (round 3)" in line
    assert "last observed activity 2m05 ago" in line
    assert "nothing is driving this run" in line


def test_the_newest_refusal_wins_across_captures_written_under_different_offsets(
    tmp_path: Path,
) -> None:
    """Ordered by instant: as text, `+00:00` and `-05:00` sort the opposite way."""
    run_dir = tmp_path / "run-10"
    _launch(run_dir)
    # Two instants an hour apart, written under offsets that reverse them as text:
    # 13:00+02:00 is 11:00Z and sorts *after* 12:00+00:00, which is 12:00Z.
    for session, started, reason in (
        ("check-in-run-10-1", "2026-08-06T13:00:00+02:00", "lacks complete v0.3 telemetry"),
        ("check-in-run-10-2", "2026-08-06T12:00:00+00:00", "lacks complete v1.0 telemetry"),
    ):
        open_capture(run_dir, session=session, agent_role="check-in")
        close_capture(
            run_dir, session, status="failed", history_failure=f"new history run {reason}"
        )
        path = capture_path(run_dir, session)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["started_at"] = started
        path.write_text(json.dumps(record), encoding="utf-8")

    line = driver_indicator(run_dir)
    assert line is not None
    assert "lacks complete v1.0 telemetry" in line


def test_a_harness_that_refused_the_history_write_is_named_in_the_driver_line(
    tmp_path: Path,
) -> None:
    """A refused write is a fact about the tier, so the tier's own line reports it."""
    run_dir = tmp_path / "run-7"
    _launch(run_dir)
    (run_dir / "orchestrator" / "stderr.log").write_text(
        "warming up\ncould not write history: new history run lacks complete v1.0 telemetry\n",
        encoding="utf-8",
    )
    line = driver_indicator(run_dir)
    assert line is not None
    assert "driver running" in line
    assert "harness history write failed" in line
    assert "lacks complete v1.0 telemetry" in line
    assert f"{run_dir.name}/{CAPTURE_DIR}/" in line


def test_a_refusal_only_a_check_in_capture_recorded_still_reaches_the_driver_line(
    tmp_path: Path,
) -> None:
    """A detached driver's own log is one source; the run's captures are the other.

    onejudge keeps its provider's stderr, so a dispatch whose harness refused the write
    records that refusal in its capture and nowhere the driver's log can see.
    """
    run_dir = tmp_path / "run-9"
    _launch(run_dir)
    open_capture(run_dir, session="check-in-run-9-1", agent_role="check-in", round_number=1)
    close_capture(
        run_dir,
        "check-in-run-9-1",
        status="failed",
        history_failure="could not write history: new history run lacks complete v1.0 telemetry",
    )

    line = driver_indicator(run_dir)
    assert line is not None
    assert "harness history write failed" in line
    assert "lacks complete v1.0 telemetry" in line


def test_records_damaged_after_they_were_written_degrade_rather_than_raise(
    tmp_path: Path,
) -> None:
    """Every input here is a file another process — or a disk — can have replaced."""
    run_dir = tmp_path / "run-damaged"
    _launch(run_dir)
    _round(run_dir, 2, status="{ not json")
    corrupt_round = driver_state(run_dir)
    assert corrupt_round is not None
    assert (corrupt_round.phase, corrupt_round.round) == ("driving-round", 2)

    session = f"orchestrator-{run_dir.name}"
    open_capture(run_dir, session=session, agent_role="orchestrator")
    # Turns recorded in a shape this scheme never wrote are dropped, and so is one
    # nothing can place in time: a served turn becomes an event's `at` on a rendered
    # timeline, where an unparseable stamp is a lie rather than a gap.
    capture_path(run_dir, session).write_text(
        json.dumps(
            {
                "schema_version": CAPTURE_SCHEMA_VERSION,
                "session": session,
                "agent_role": "orchestrator",
                "started_at": "2026-08-06T10:00:00+00:00",
                "status": "running",
                "round": True,
                "finished_at": "whenever",
                "turns": [
                    "not a turn",
                    {"at": "whenever", "text": "unplaceable"},
                    {"at": "2026-08-06T10:01:00+00:00", "text": "placeable"},
                ],
            }
        ),
        encoding="utf-8",
    )
    captured = load_captures(run_dir)[0]
    assert captured.round is None
    # An end nothing can parse reads as "not closed", which is the honest state.
    assert captured.finished_at is None
    assert [turn["text"] for turn in captured.turns] == ["placeable"]
    assert driver_state(run_dir) is not None

    # A capture replaced by something unreadable stops accepting writes, silently.
    capture_path(run_dir, session).write_text("{", encoding="utf-8")
    assert record_turn(run_dir, session, "a turn nothing can hold") is False
    assert load_captures(run_dir) == []


def test_a_driver_with_nothing_timeable_still_reports_its_phase(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-8"
    _launch(run_dir)
    state = driver_state(run_dir)
    assert state is not None
    # The launch record itself is timeable, so drop it to reach the unrecorded case.
    bare = type(state)(
        session=state.session,
        pid=state.pid,
        alive=state.alive,
        phase=state.phase,
        round=state.round,
        last_activity_at=None,
    )
    assert bare.last_activity_age() is None
    assert "no observed activity" in bare.describe()
