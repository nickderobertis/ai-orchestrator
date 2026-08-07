from __future__ import annotations

import errno
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT
from orchestrator.channel import (
    HEARTBEAT_SURFACE_FILE,
    ChannelError,
    ChannelTimeout,
    ProposalPump,
    _finished,
    _reply,
    _surface,
    _validated_heartbeat_surface,
    apply_heartbeat_reply,
    await_command_outcomes,
    await_reply,
    await_reply_delivery,
    claim_commands,
    claim_heartbeat,
    claim_reply,
    command_outcomes,
    create_channel,
    due_indicator,
    finish_heartbeat_attempt,
    heartbeat_state,
    live_round,
    main_approve,
    main_continue,
    main_next,
    main_reject,
    main_relay,
    main_reply,
    main_surface,
    mark_heartbeat_due,
    next_surface,
    pending_commands,
    pending_replies,
    pending_surface_indicator,
    pending_surfaces,
    read_message,
    record_command_outcome,
    record_surface,
    relay_supervisor,
    release_heartbeat_claim,
    submit_commands,
    submit_reply,
    unanswered_commands,
    validate_commands,
    write_message,
)
from orchestrator.coordination import atomic_json
from orchestrator.edits import parse_commands
from orchestrator.supervisory import load_captures, open_capture


def _heartbeat(channel: Path) -> dict[str, object]:
    state = heartbeat_state(channel)
    assert state is not None
    return state


def test_heartbeat_surface_protocol_validates_every_external_field() -> None:
    valid: dict[str, object] = {
        "op": "supervisor",
        "run_id": "orch",
        "round": 1,
        "surface": {
            "kind": "heartbeat",
            "message": "work continues",
            "blocking": False,
        },
        "messages": [],
    }
    assert _validated_heartbeat_surface(valid, "orch", 1) == valid

    invalid = [
        {**valid, "op": "other"},
        {**valid, "run_id": "other"},
        {**valid, "round": True},
        {**valid, "surface": {"kind": "heartbeat", "message": 1, "blocking": False}},
        {**valid, "messages": [{"role": "assistant", "content": "status"}]},
        {**valid, "unexpected": True},
    ]
    for value in invalid:
        with pytest.raises(ChannelError, match="heartbeat surface"):
            _validated_heartbeat_surface(value, "orch", 1)


def test_fifo_round_trip_and_reattach(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")

    def send(value: dict[str, object]) -> None:
        write_message(channel / "up.fifo", value, timeout=1)

    first = threading.Thread(target=send, args=({"sequence": 1},))
    first.start()
    assert read_message(channel / "up.fifo", timeout=1) == {"sequence": 1}
    first.join()

    second = threading.Thread(target=send, args=({"sequence": 2},))
    second.start()
    assert read_message(channel / "up.fifo", timeout=1) == {"sequence": 2}
    second.join()


def test_fifo_round_trips_frame_larger_than_pipe_buffer(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    value = {"summary": "x" * 100_000}
    sender = threading.Thread(
        target=write_message,
        args=(channel / "up.fifo", value),
        kwargs={"timeout": 2},
    )
    sender.start()
    assert read_message(channel / "up.fifo", timeout=2) == value
    sender.join()


def test_fifo_round_trips_large_versioned_edit_frame(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    value = {
        "version": 1,
        "commands": [
            {
                "op": "add",
                "node": {
                    "id": "large-followup",
                    "persona": "engineer",
                    "task": "x" * 100_000,
                },
            }
        ],
    }
    sender = threading.Thread(
        target=write_message,
        args=(channel / "down.fifo", value),
        kwargs={"timeout": 2},
    )
    sender.start()
    received = read_message(channel / "down.fifo", timeout=2)
    sender.join()
    reply = _reply(received)
    assert reply["commands"] == value["commands"]


def test_concurrent_large_writers_do_not_interleave_frames(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    values = [{"writer": index, "body": str(index) * 100_000} for index in range(4)]
    writers = [
        threading.Thread(
            target=write_message,
            args=(channel / "up.fifo", value),
            kwargs={"timeout": 5},
        )
        for value in values
    ]
    for writer in writers:
        writer.start()
    received = [read_message(channel / "up.fifo", timeout=5) for _ in writers]
    for writer in writers:
        writer.join()
    assert sorted(received, key=lambda item: item["writer"]) == values


def test_writer_retries_partial_writes_and_backpressure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = create_channel(tmp_path / "run")
    value = {"summary": "partial" * 20_000}
    real_write = os.write
    calls = 0

    def pressured_write(fd: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls % 3 == 0:
            raise BlockingIOError(errno.EAGAIN, "pipe full")
        return real_write(fd, data[:997])

    monkeypatch.setattr("orchestrator.channel.os.write", pressured_write)
    sender = threading.Thread(
        target=write_message,
        args=(channel / "up.fifo", value),
        kwargs={"timeout": 5},
    )
    sender.start()
    assert read_message(channel / "up.fifo", timeout=5) == value
    sender.join()
    assert calls > 100


def test_proposal_pump_round_trips_and_persists_on_reconciler_drain(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")

    def dispatch_check_in() -> None:
        atomic_json(
            channel / "heartbeat-surface.json",
            {
                "op": "supervisor",
                "run_id": "live",
                "round": 3,
                "surface": {
                    "kind": "heartbeat",
                    "message": "worker: implementing transport; follow-ups: none",
                    "blocking": False,
                },
                "messages": [],
            },
        )

    pump = ProposalPump(
        channel,
        "live",
        3,
        dispatch_check_in=dispatch_check_in,
    )
    pump.propose_blocking("worker", "found adjacent work")
    assert read_message(channel / "up.fifo", timeout=1) == {
        "op": "supervisor",
        "run_id": "live",
        "round": 3,
        "surface": {
            "kind": "proposal",
            "message": "worker: found adjacent work",
            "blocking": True,
        },
        "messages": [],
        "proposal_id": "worker:found adjacent work",
    }
    reply = {"completion": False, "message": "defer", "reason": "next round"}
    # Submitted the way `channel-reply` submits one: durably, with nobody holding an
    # endpoint open. The pump's receiver claims it on its own poll.
    assert submit_reply(channel, reply) == 1
    deadline = time.monotonic() + 1
    verdict = channel / "planner-verdict.json"
    while time.monotonic() < deadline and not verdict.is_file():
        pump.persist_replies()
        time.sleep(0.01)
    assert json.loads(verdict.read_text(encoding="utf-8")) == reply
    assert pending_replies(channel) == ()
    state = heartbeat_state(channel)
    assert state is not None
    state.update({"last_surface_at": 0, "interval_s": 1})
    atomic_json(channel / "heartbeat.json", state)
    pump.heartbeat_tick()
    assert _heartbeat(channel)["due"] is True
    assert pump.drain_commands() == ()
    pump.propose_blocking("worker", "found adjacent work")
    deadline = time.monotonic() + 1
    heartbeat_surface = channel / "heartbeat-surface.json"
    while not heartbeat_surface.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert json.loads(heartbeat_surface.read_text(encoding="utf-8"))["surface"] == {
        "kind": "heartbeat",
        "message": "worker: implementing transport; follow-ups: none",
        "blocking": False,
    }
    assert _heartbeat(channel)["last_surface_at"] == 0
    with pytest.raises(ChannelTimeout):
        read_message(channel / "up.fifo", timeout=0.1)
    pump.close()


def test_proposal_pump_defers_terminal_blocker_until_supervisor_relay(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "deferred-run")
    pump = ProposalPump(channel, "live", 1)

    pump.defer_blocking("worker", "provider unavailable")

    assert json.loads((channel / "deferred-blocker.json").read_text()) == {
        "kind": "proposal",
        "message": "worker: provider unavailable",
        "blocking": True,
    }
    assert not (channel / "planner-pending.json").exists()
    pump.close()


def test_new_proposal_pump_continues_persisted_heartbeat_countdown(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run", heartbeat_interval=0.1)
    record_surface(channel, now=time.time() - 0.09)

    def dispatch_check_in() -> None:
        atomic_json(
            channel / "heartbeat-surface.json",
            {
                "op": "supervisor",
                "run_id": "continued",
                "round": 2,
                "surface": {"kind": "heartbeat", "message": "still active", "blocking": False},
                "messages": [],
            },
        )

    pump = ProposalPump(
        channel,
        "continued",
        2,
        dispatch_check_in=dispatch_check_in,
    )
    queued = channel / "heartbeat-surface.json"
    # The full suite can leave the pacemaker thread briefly CPU-starved under
    # coverage; keep the assertion bounded without tying it to scheduler speed.
    wait_until = time.monotonic() + 2
    while not queued.is_file() and time.monotonic() < wait_until:
        time.sleep(0.01)
    pump.close()
    assert queued.is_file()
    assert json.loads(queued.read_text(encoding="utf-8"))["round"] == 2


@pytest.mark.parametrize(
    ("entrypoint", "arguments", "expected"),
    [
        (main_approve, [], {"completion": True, "reason": "approved"}),
        (
            main_reject,
            ["verification failed"],
            {
                "completion": False,
                "message": "verification failed",
                "reason": "verification failed",
            },
        ),
        (
            main_continue,
            ["keep going"],
            {"completion": False, "message": "keep going", "reason": "keep going"},
        ),
    ],
)
def test_convenience_recipe_queues_its_reply_with_no_reader_present(
    tmp_path: Path,
    entrypoint: Callable[[list[str] | None], int],
    arguments: list[str],
    expected: dict[str, object],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every convenience shape is accepted with nothing listening, and says so.

    No reader is started deliberately: this is the state a planner replies into
    while the orchestrator agent is mid-turn, and it used to be the one that failed.
    """
    runs = tmp_path / "runs"
    channel = create_channel(runs / "orch")

    assert entrypoint(["orch", *arguments, "--runs-dir", str(runs)]) == 0

    assert json.loads(capsys.readouterr().out) == {"reply": 1, "state": "queued"}
    assert [item.reply for item in pending_replies(channel)] == [expected]
    claimed = claim_reply(channel)
    assert claimed is not None and claimed.reply == expected
    # Exactly once: the cursor moved with the claim, so a second reader gets nothing.
    assert claim_reply(channel) is None


def test_channel_run_resolution_accepts_unique_active_plan_name_and_lists_ambiguity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runs = tmp_path / "runs"
    for run_id in ("launch-1", "launch-2"):
        channel = create_channel(runs / run_id)
        atomic_json(channel.parent / "launch.json", {"plan_name": "friendly"})
        (channel.parent / "orchestrator").mkdir()
        atomic_json(
            channel.parent / "orchestrator" / "status.json",
            {"status": "running", "pid": os.getpid(), "host": socket.gethostname()},
        )
    report = runs / "launch-2" / "orchestrator" / "report.json"
    report.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("orchestrator.channel._finished", lambda path: False)
    monkeypatch.setattr(
        "orchestrator.channel.read_message",
        lambda path, timeout: {"resolved": path.parent.parent.name},
    )
    assert main_next(["friendly", "--runs-dir", str(runs)]) == 0
    assert json.loads(capsys.readouterr().out) == {"resolved": "launch-1"}

    report.unlink()
    assert main_next(["friendly", "--runs-dir", str(runs)]) == 2
    error = capsys.readouterr().err
    assert "ambiguous" in error
    assert "launch-1, launch-2" in error


def test_a_reply_sent_after_the_pump_closes_waits_for_the_boundary_relay(
    tmp_path: Path,
) -> None:
    """The window the incident happened in: no reader between round and relay.

    The pump has closed and the relay has not opened, which is exactly where a reply
    used to time out. It is accepted, survives with nobody holding anything open, and
    the next reader — the relay, here standing in for it — gets it.
    """
    channel = create_channel(tmp_path / "run")
    pump = ProposalPump(channel, "live", 1)
    pump.propose("worker", "discovery")
    assert read_message(channel / "up.fifo", timeout=1)["surface"]["kind"] == "proposal"
    pump.close()
    assert not pump._thread.is_alive()

    boundary_reply = {"completion": True, "reason": "closeout verified"}
    assert submit_reply(channel, boundary_reply) == 1
    assert await_reply(channel, timeout=1) == boundary_reply
    with pytest.raises(ChannelTimeout, match="no planner reply arrived"):
        await_reply(channel, timeout=0.05)


@pytest.mark.parametrize("failure", ["write", "write_timeout", "claim_error", "claim_os_error"])
def test_proposal_pump_stops_on_a_broken_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    channel = create_channel(tmp_path / "run")
    calls = 0

    def fail_after_timeout(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ChannelTimeout
        raise OSError("closed")

    match failure:
        case "write":
            monkeypatch.setattr(
                "orchestrator.channel.write_message",
                lambda *args, **kwargs: (_ for _ in ()).throw(OSError("closed")),
            )
        case "write_timeout":
            monkeypatch.setattr("orchestrator.channel.write_message", fail_after_timeout)
        case "claim_error" | "claim_os_error":
            monkeypatch.setattr("orchestrator.channel.write_message", lambda *args, **kwargs: None)
            broken: Exception = (
                ChannelError("broken")
                if failure == "claim_error"
                else OSError("reply queue is unreadable")
            )
            monkeypatch.setattr(
                "orchestrator.channel.claim_reply",
                lambda *args, **kwargs: (_ for _ in ()).throw(broken),
            )
    pump = ProposalPump(channel, "live", 1)
    pump.propose("worker", "discovery")
    target = pump._receiver if failure.startswith("claim") else pump._thread
    target.join(timeout=1)
    assert not target.is_alive()
    pump.close()


def test_fifo_timeout_is_bounded(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    with pytest.raises(ChannelTimeout):
        read_message(channel / "up.fifo", timeout=0.01)
    with pytest.raises(ChannelTimeout):
        write_message(channel / "down.fifo", {"value": 1}, timeout=0.01)


def test_writer_reports_writability_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = create_channel(tmp_path / "run")
    reader = os.open(channel / "down.fifo", os.O_RDONLY | os.O_NONBLOCK)
    try:
        monkeypatch.setattr(
            "orchestrator.channel.select.select",
            lambda reads, writes, errors, timeout: ([], [], []),
        )
        with pytest.raises(ChannelTimeout, match="write timed out"):
            write_message(channel / "down.fifo", {"value": 1}, timeout=1)
    finally:
        os.close(reader)


def test_frame_and_supervisor_shapes_are_validated(tmp_path: Path) -> None:
    request = {"kind": "milestone", "message": "round ran", "messages": []}
    assert _surface(request, "live", 2) == {
        "op": "supervisor",
        "run_id": "live",
        "round": 2,
        "surface": {"kind": "milestone", "message": "round ran"},
        "messages": [],
    }
    assert _reply({"completion": False, "message": "retry X", "reason": "failed"}) == {
        "completion": False,
        "message": "retry X",
        "reason": "failed",
    }
    assert _reply({"completion": True, "reason": "landed"}) == {
        "completion": True,
        "reason": "landed",
    }
    with pytest.raises(ChannelError):
        _reply({"completion": False, "reason": "missing guidance"})


def test_versioned_edits_are_independent_of_completion() -> None:
    edit = _reply(
        {
            "version": 1,
            "commands": [
                {
                    "op": "add",
                    "node": {"id": "followup", "persona": "engineer", "task": "Follow up"},
                }
            ],
        }
    )
    assert edit["completion"] is False
    assert edit["commands"][0]["op"] == "add"

    complete = _reply({"version": 1, "commands": [{"op": "complete", "reason": "published"}]})
    assert complete == {
        "completion": True,
        "reason": "published",
        "version": 1,
        "commands": [{"op": "complete", "reason": "published"}],
    }
    with pytest.raises(ChannelError, match="version 1"):
        _reply({"version": 2, "commands": [{"op": "complete", "reason": "done"}]})
    with pytest.raises(ChannelError, match="string reason"):
        _reply({"version": 1, "commands": [{"op": "complete", "reason": 1}]})


def test_channel_metadata_is_durable(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    assert json.loads((channel / "channel.json").read_text()) == {"schema_version": 1}
    assert create_channel(tmp_path / "run") == channel


def test_heartbeat_is_sticky_durable_and_reset_by_surface(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run", heartbeat_interval=10)
    initial = heartbeat_state(channel)
    assert initial is not None
    mark_heartbeat_due(channel, now=float(initial["last_surface_at"]) + 5)
    assert _heartbeat(channel)["due"] is False
    mark_heartbeat_due(channel, now=float(initial["last_surface_at"]) + 11)
    assert due_indicator(channel, now=float(initial["last_surface_at"]) + 125) == (
        "planner update due (2m since last update)"
    )
    assert create_channel(tmp_path / "run", heartbeat_interval=99) == channel
    assert _heartbeat(channel)["interval_s"] == 10
    record_surface(channel, now=float(initial["last_surface_at"]) + 126)
    assert due_indicator(channel, now=float(initial["last_surface_at"]) + 200) is None


def test_heartbeat_claim_deduplicates_and_failure_retries_next_interval(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run", heartbeat_interval=10)
    initial = _heartbeat(channel)
    due_at = float(initial["last_surface_at"]) + 11
    mark_heartbeat_due(channel, now=due_at)

    assert claim_heartbeat(channel) is True
    assert claim_heartbeat(channel) is False
    assert _heartbeat(channel)["in_flight"] is True

    finish_heartbeat_attempt(channel, now=due_at)
    failed = _heartbeat(channel)
    assert failed["in_flight"] is False
    assert failed["due"] is False
    mark_heartbeat_due(channel, now=due_at + 9)
    assert claim_heartbeat(channel) is False
    mark_heartbeat_due(channel, now=due_at + 10)
    assert claim_heartbeat(channel) is True

    record_surface(channel, now=due_at + 11)
    settled = _heartbeat(channel)
    assert settled["in_flight"] is False
    assert settled["due"] is False


def test_releasing_a_claim_keeps_the_clock_so_an_ignored_pacemaker_stays_due(
    tmp_path: Path,
) -> None:
    """A discarded update must not take the pacemaker down with it.

    The claim exists to keep exactly one check-in pending. Dropping the update it was
    held for therefore has to drop the claim too — while leaving the clock where it
    was, because nobody has been updated and the staleness the views report is
    measured from the last update a planner actually saw.
    """
    channel = create_channel(tmp_path / "run", heartbeat_interval=10)
    initial = _heartbeat(channel)
    due_at = float(initial["last_surface_at"]) + 11
    mark_heartbeat_due(channel, now=due_at)
    assert claim_heartbeat(channel) is True

    release_heartbeat_claim(channel)
    released = _heartbeat(channel)
    assert released["in_flight"] is False
    assert released["due"] is True
    assert released["last_surface_at"] == initial["last_surface_at"]
    # Eligible again, exactly once: the next tick may queue one replacement update.
    assert claim_heartbeat(channel) is True
    assert claim_heartbeat(channel) is False
    # Idempotent, and silent for a run that has no pacemaker at all.
    release_heartbeat_claim(channel)
    release_heartbeat_claim(channel)
    assert _heartbeat(channel)["in_flight"] is False
    release_heartbeat_claim(tmp_path / "no-channel")


def test_queued_surfaces_are_reported_with_a_growing_age_and_the_reading_command(
    tmp_path: Path,
) -> None:
    """The state a planner who never attached is otherwise blind to."""
    run_dir = tmp_path / "runs" / "unattached"
    channel = create_channel(run_dir)
    assert pending_surfaces(channel) == ()
    assert pending_surface_indicator(run_dir) is None

    atomic_json(
        channel / HEARTBEAT_SURFACE_FILE,
        {
            "op": "supervisor",
            "run_id": "unattached",
            "round": 1,
            "surface": {
                "kind": "heartbeat",
                "message": "worker still verifying",
                "blocking": False,
            },
            "messages": [],
        },
    )
    queued = pending_surfaces(channel)
    assert [(item.kind, item.message) for item in queued] == [
        ("heartbeat", "worker still verifying")
    ]
    indicator = pending_surface_indicator(run_dir, now=queued[0].queued_at + 3 * 3600)
    assert indicator == (
        "1 planner update waiting, unread for 3h; read it with: "
        f"just channel-next unattached --runs-dir {run_dir.parent}"
    )
    # The age is what escalates, so it is reported at the granularity a reader can act
    # on rather than rounded away.
    assert "unread for 0s" in str(pending_surface_indicator(run_dir))
    assert "unread for 12m" in str(
        pending_surface_indicator(run_dir, now=queued[0].queued_at + 12 * 60)
    )

    # A blocker preserved for the next reply-ready relay is queued in the same sense.
    atomic_json(
        channel / "deferred-blocker.json",
        {"kind": "proposal", "message": "worker: dispatch died", "blocking": True},
    )
    both = pending_surfaces(channel)
    assert [item.kind for item in both] == ["heartbeat", "proposal"]
    assert both[0].queued_at <= both[1].queued_at
    assert "2 planner updates waiting" in str(pending_surface_indicator(run_dir))
    assert "read them with:" in str(pending_surface_indicator(run_dir))


def test_a_queued_surface_that_cannot_be_read_is_still_reported(tmp_path: Path) -> None:
    """Damaged state must not restore the silence this reporting exists to break."""
    run_dir = tmp_path / "runs" / "damaged"
    channel = create_channel(run_dir)
    (channel / HEARTBEAT_SURFACE_FILE).write_text("{not json", encoding="utf-8")
    queued = pending_surfaces(channel)
    assert [(item.kind, item.message) for item in queued] == [
        ("unknown", "unreadable queued surface")
    ]
    assert "1 planner update waiting" in str(pending_surface_indicator(run_dir))


def test_heartbeat_reply_adjusts_or_disables_without_changing_verdict(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    initial = heartbeat_state(channel)
    assert initial is not None
    mark_heartbeat_due(channel, now=float(initial["last_surface_at"]) + 1801)
    adjusted = _reply(
        {
            "completion": False,
            "message": "continue",
            "reason": "cadence",
            "heartbeat_interval": 12.5,
        }
    )
    apply_heartbeat_reply(channel, adjusted)
    assert _heartbeat(channel)["interval_s"] == 12.5
    assert _heartbeat(channel)["due"] is True
    disabled = _reply(
        {
            "completion": False,
            "message": "continue",
            "reason": "quiet",
            "heartbeat_interval": False,
        }
    )
    apply_heartbeat_reply(channel, disabled)
    assert _heartbeat(channel)["enabled"] is False
    with pytest.raises(ChannelError, match="positive, finite"):
        _reply({"completion": True, "reason": "bad", "heartbeat_interval": 0})


def test_nonblocking_surface_cli_queues_claimed_update_until_consumed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    run_dir = runs / "orch"
    channel = create_channel(run_dir, heartbeat_interval=1)
    (run_dir / "round-01").mkdir()
    initial = heartbeat_state(channel)
    assert initial is not None
    mark_heartbeat_due(channel, now=float(initial["last_surface_at"]) + 2)
    assert main_surface(["orch", "unclaimed", "--runs-dir", str(runs)]) == 2
    assert "no active check-in claim" in capsys.readouterr().err
    assert claim_heartbeat(channel)
    assert (
        main_surface(
            ["orch", "worker active; no follow-ups", "--runs-dir", str(runs), "--timeout", "1"]
        )
        == 0
    )
    assert json.loads((channel / "heartbeat-surface.json").read_text()) == {
        "op": "supervisor",
        "run_id": "orch",
        "round": 1,
        "surface": {
            "kind": "heartbeat",
            "message": "worker active; no follow-ups",
            "blocking": False,
        },
        "messages": [],
    }
    assert _heartbeat(channel)["due"] is True
    assert _heartbeat(channel)["in_flight"] is True
    # The next interval's check-in replaces a snapshot nobody read rather than being
    # refused by it. Exactly one update stays queued, and it is the current one.
    assert main_surface(["orch", "worker still verifying", "--runs-dir", str(runs)]) == 0
    queued = json.loads((channel / "heartbeat-surface.json").read_text())
    assert queued["surface"]["message"] == "worker still verifying"
    assert main_surface(["orch", " ", "--runs-dir", str(runs)]) == 2
    assert "non-empty" in capsys.readouterr().err
    assert main_surface(["orch", "update", "--runs-dir", str(runs), "--timeout", "0"]) == 2
    assert "timeout must be a positive" in capsys.readouterr().err


def test_nonblocking_surface_cli_rejects_missing_round_and_pending_planner_surface(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    run_dir = runs / "orch"
    channel = create_channel(run_dir, heartbeat_interval=1)
    initial = heartbeat_state(channel)
    assert initial is not None
    mark_heartbeat_due(channel, now=float(initial["last_surface_at"]) + 2)
    assert claim_heartbeat(channel)

    assert main_surface(["orch", "status", "--runs-dir", str(runs)]) == 2
    assert "requires an active round" in capsys.readouterr().err

    (run_dir / "round-01").mkdir()
    atomic_json(channel / "planner-pending.json", {"completion": False})
    assert main_surface(["orch", "status", "--runs-dir", str(runs)]) == 2
    assert "planner surface is already pending" in capsys.readouterr().err
    assert not (channel / "heartbeat-surface.json").exists()


def test_heartbeat_legacy_and_corrupt_state_boundaries(tmp_path: Path) -> None:
    channel = tmp_path / "legacy-channel"
    channel.mkdir()
    assert heartbeat_state(channel) is None
    mark_heartbeat_due(channel)
    record_surface(channel)
    with pytest.raises(ChannelError, match="unavailable"):
        apply_heartbeat_reply(channel, {"heartbeat_interval": 10})
    atomic_json(
        channel / "heartbeat.json",
        {"last_surface_at": -1, "interval_s": 10, "due": False, "enabled": True},
    )
    with pytest.raises(ChannelError, match="state is invalid"):
        heartbeat_state(channel)


def test_relay_shapes_supervisor_and_persists_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    channel = create_channel(tmp_path / "run")
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "orchestrator.channel.write_message", lambda path, value, timeout: sent.append(value)
    )
    submit_reply(channel, {"completion": False, "message": "retry X", "reason": "gap"})
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"op": "supervisor", "task": "work", "messages": []})),
    )
    assert relay_supervisor(channel, "orch", 3, timeout=1) == 0
    assert sent[0]["run_id"] == "orch"
    assert json.loads(capsys.readouterr().out)["message"] == "retry X"
    assert json.loads((channel / "planner-verdict.json").read_text())["completion"] is False


def test_relay_mirrors_completed_verdict_for_boolean_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    channel = create_channel(tmp_path / "run")
    atomic_json(channel / "planner-verdict.json", {"completion": True, "reason": "done"})
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"op": "judge", "kind": "boolean"})))
    assert relay_supervisor(channel, "orch", 1, timeout=1) == 0
    assert json.loads(capsys.readouterr().out)["value"] is True


def test_relay_preserves_an_existing_terminal_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    channel = create_channel(tmp_path / "run-blocker")
    blocker = {
        "kind": "proposal",
        "message": "worker: terminal infrastructure failure",
        "blocking": True,
    }
    atomic_json(channel / "planner-pending.json", blocker)
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "orchestrator.channel.write_message",
        lambda path, value, timeout: sent.append(dict(value)),
    )
    submit_reply(channel, {"completion": True, "reason": "acknowledged"})
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"op": "supervisor", "task": "round complete", "messages": []})),
    )

    assert relay_supervisor(channel, "orch", 1, timeout=1) == 0
    assert sent[0]["surface"] == blocker
    assert json.loads(capsys.readouterr().out)["completion"] is True


def test_relay_records_the_driver_turn_it_served_after_answering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The capture is written once the reply is in, not before the surface.

    Where this write sits is load-bearing. Ahead of `write_message` it delays a
    surface planners read under seconds-long budgets; between the surface and the
    reply it delays this relay reaching the reply queue, which is the window a
    planner replying immediately lands in. So it happens after the answer.
    """
    run_dir = tmp_path / "run-recorded"
    channel = create_channel(run_dir)
    open_capture(run_dir, session="orchestrator-orch", agent_role="orchestrator")
    monkeypatch.setattr("orchestrator.channel.write_message", lambda path, value, timeout: None)
    submit_reply(channel, {"completion": True, "reason": "done"})
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"op": "supervisor", "task": "round complete", "messages": []})),
    )

    assert relay_supervisor(channel, "orch", 1, timeout=1) == 0
    assert json.loads(capsys.readouterr().out)["completion"] is True
    captured = load_captures(run_dir)
    assert [turn["text"] for turn in captured[0].turns] == ["round complete"]


def test_relay_records_the_driver_turn_a_planner_never_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A planner who never replies must not cost the turn its only record.

    That guarantee is why the write cannot simply move to the end of the happy
    path: this relay's whole reason for existing is a supervisor that can be left
    waiting, and the turn it already served is exactly what a stranded run needs
    recorded.
    """
    run_dir = tmp_path / "run-unanswered"
    channel = create_channel(run_dir)
    open_capture(run_dir, session="orchestrator-orch", agent_role="orchestrator")
    monkeypatch.setattr("orchestrator.channel.write_message", lambda path, value, timeout: None)
    # Nothing is submitted, so the relay waits out its bound with no answer.
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"op": "supervisor", "task": "stranded turn", "messages": []})),
    )

    assert relay_supervisor(channel, "orch", 1, timeout=0.05) == 1
    captured = load_captures(run_dir)
    assert [turn["text"] for turn in captured[0].turns] == ["stranded turn"]


def test_relay_replaces_stale_nonblocking_pending_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    channel = create_channel(tmp_path / "run-stale")
    atomic_json(
        channel / "planner-pending.json",
        {"kind": "heartbeat", "message": "stale", "blocking": False},
    )
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "orchestrator.channel.write_message",
        lambda path, value, timeout: sent.append(dict(value)),
    )
    submit_reply(channel, {"completion": True, "reason": "done"})
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"op": "supervisor", "task": "fresh milestone", "messages": []})),
    )

    assert relay_supervisor(channel, "orch", 1, timeout=1) == 0
    assert sent[0]["surface"]["message"] == "fresh milestone"
    assert json.loads(capsys.readouterr().out)["completion"] is True


@pytest.mark.parametrize(
    "surface",
    [
        {"kind": "proposal", "message": 1, "blocking": True},
        {"kind": "unknown", "message": "blocked", "blocking": True},
        {
            "kind": "proposal",
            "message": "blocked",
            "blocking": True,
            "options": "invalid",
        },
    ],
)
def test_relay_rejects_invalid_persisted_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    surface: dict[str, object],
) -> None:
    channel = create_channel(tmp_path / "invalid-blocker")
    atomic_json(channel / "deferred-blocker.json", surface)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"op": "supervisor", "task": "round complete", "messages": []})),
    )

    assert relay_supervisor(channel, "orch", 1, timeout=0.01) == 1
    assert "persisted planner surface" in capsys.readouterr().err


def test_bridge_mains_render_bounded_states_and_validate_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "runs" / "orch"
    create_channel(run_dir)
    monkeypatch.setattr("orchestrator.channel._finished", lambda path: False)

    def timeout_read(path: Path, *, timeout: float) -> dict[str, object]:
        raise ChannelTimeout

    monkeypatch.setattr("orchestrator.channel.read_message", timeout_read)
    assert main_next(["orch", "--runs-dir", str(tmp_path / "runs"), "--timeout", "0"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "running", "surface": None}

    reply = tmp_path / "reply.json"
    reply.write_text('{"completion":true,"reason":"verified"}', encoding="utf-8")
    assert main_reply(["orch", str(reply), "--runs-dir", str(tmp_path / "runs")]) == 0
    assert json.loads(capsys.readouterr().out) == {"reply": 1, "state": "queued"}
    assert [item.reply for item in pending_replies(run_dir / "channel")] == [
        {"completion": True, "reason": "verified"}
    ]


def test_finished_uses_report_and_owner_liveness(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "orchestrator").mkdir(parents=True)
    report = run / "orchestrator" / "report.json"
    report.write_text("", encoding="utf-8")
    assert _finished(run) is False
    report.write_text("{}", encoding="utf-8")
    assert _finished(run) is True
    report.unlink()
    round_dir = run / "round-01"
    round_dir.mkdir()
    atomic_json(
        round_dir / "status.json",
        {"status": "running", "pid": os.getpid(), "host": "ignored"},
    )
    assert _finished(run) is False
    atomic_json(round_dir / "status.json", {"status": "completed"})
    assert _finished(run) is True


def test_surface_accepts_agent_emitted_shape_and_options() -> None:
    request = {
        "op": "supervisor",
        "task": "fallback",
        "messages": [
            {
                "role": "assistant",
                "content": '{"kind":"choice","message":"pick","options":["a","b"]}',
            }
        ],
    }
    value = _surface(request, "orch", 1)
    assert value["surface"] == {"kind": "choice", "message": "pick", "options": ["a", "b"]}
    with pytest.raises(ChannelError, match="options"):
        _surface({"task": "x", "messages": [], "options": "bad"}, "orch", 1)
    with pytest.raises(ChannelError, match="messages"):
        _surface({"task": "x", "messages": "bad"}, "orch", 1)


def test_bridge_main_success_finished_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    create_channel(runs / "orch")
    monkeypatch.setattr("orchestrator.channel._finished", lambda path: True)
    assert main_next(["orch", "--runs-dir", str(runs)]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "finished"}

    monkeypatch.setattr("orchestrator.channel._finished", lambda path: False)
    heartbeat_surface = runs / "orch" / "channel" / "heartbeat-surface.json"
    valid_heartbeat = {
        "op": "supervisor",
        "run_id": "orch",
        "round": 1,
        "surface": {"kind": "heartbeat", "message": "status", "blocking": False},
        "messages": [],
    }
    (runs / "orch" / "round-01").mkdir()
    atomic_json(heartbeat_surface, valid_heartbeat)
    assert main_next(["orch", "--runs-dir", str(runs)]) == 0
    assert json.loads(capsys.readouterr().out) == valid_heartbeat
    assert not heartbeat_surface.exists()

    atomic_json(
        heartbeat_surface,
        {
            "op": "supervisor",
            "run_id": "wrong",
            "round": 1,
            "surface": {"kind": "heartbeat", "message": "status", "blocking": False},
            "messages": [],
        },
    )
    assert main_next(["orch", "--runs-dir", str(runs)]) == 2
    assert "run id" in capsys.readouterr().err
    heartbeat_surface.unlink()
    monkeypatch.setattr(
        "orchestrator.channel.read_message",
        lambda path, timeout: {
            "op": "supervisor",
            "run_id": "orch",
            "round": 1,
            "surface": {"kind": "milestone", "message": "done"},
            "messages": [],
        },
    )
    assert main_next(["orch", "--runs-dir", str(runs)]) == 0
    assert json.loads(capsys.readouterr().out)["op"] == "supervisor"

    monkeypatch.setattr(
        "orchestrator.channel.read_message",
        lambda path, timeout: (_ for _ in ()).throw(ChannelError("bad frame")),
    )
    assert main_next(["orch", "--runs-dir", str(runs)]) == 2
    assert "bad frame" in capsys.readouterr().err

    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    assert main_reply(["orch", str(bad), "--runs-dir", str(runs)]) == 2
    assert "JSON object" in capsys.readouterr().err


def test_main_relay_and_relay_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: list[tuple[Path, str, int, float]] = []

    def fake_relay(path: Path, run_id: str, round_number: int, *, timeout: float) -> int:
        called.append((path, run_id, round_number, timeout))
        return 7

    monkeypatch.setattr("orchestrator.channel.relay_supervisor", fake_relay)
    channel = create_channel(tmp_path / "orch")
    assert main_relay([str(channel), "orch", "4", "--timeout", "2"]) == 7
    assert called == [(channel, "orch", 4, 2.0)]

    monkeypatch.setattr("orchestrator.channel.relay_supervisor", relay_supervisor)
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
    assert relay_supervisor(tmp_path, "orch", 1, timeout=1) == 1
    assert "JSON object" in capsys.readouterr().err

    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"op": "judge", "kind": "score", "max": 7}))
    )
    assert relay_supervisor(tmp_path, "orch", 1, timeout=1) == 0
    assert json.loads(capsys.readouterr().out)["value"] == 7


@pytest.mark.parametrize("payload", [b"not-json\n", b"[]\n", b"{}\ntrailing"])
def test_reader_rejects_malformed_frames(tmp_path: Path, payload: bytes) -> None:
    channel = create_channel(tmp_path / "run")

    def raw_send() -> None:
        fd = os.open(channel / "up.fifo", os.O_WRONLY)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)

    sender = threading.Thread(target=raw_send)
    sender.start()
    with pytest.raises(ChannelError):
        read_message(channel / "up.fifo", timeout=1)
    sender.join()


def test_channel_and_surface_reject_invalid_boundaries(tmp_path: Path) -> None:
    endpoint = tmp_path / "run" / "channel" / "up.fifo"
    endpoint.parent.mkdir(parents=True)
    endpoint.write_text("not a fifo", encoding="utf-8")
    with pytest.raises(ChannelError, match="not a FIFO"):
        create_channel(tmp_path / "run")
    with pytest.raises(ChannelError, match="kind/message"):
        _surface({"messages": []}, "orch", 1)
    with pytest.raises(ChannelError, match="timeout"):
        read_message(endpoint, timeout=-1)
    with pytest.raises(ChannelError, match="timeout"):
        write_message(endpoint, {}, timeout=-1)
    with pytest.raises(FileNotFoundError):
        write_message(tmp_path / "missing.fifo", {}, timeout=0.1)
    with pytest.raises(ChannelError, match="boolean completion"):
        _reply({"reason": "missing completion"})


def test_finished_handles_missing_status_and_dead_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    round_dir = run / "round-01"
    round_dir.mkdir(parents=True)
    assert _finished(run) is False
    atomic_json(
        round_dir / "status.json",
        {"status": "running", "pid": 123, "host": socket.gethostname()},
    )

    def dead(pid: int, signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("orchestrator.channel.os.kill", dead)
    assert _finished(run) is True

    def inaccessible(pid: int, signal: int) -> None:
        raise PermissionError

    monkeypatch.setattr("orchestrator.channel.os.kill", inaccessible)
    assert _finished(run) is False


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"op": "unknown"}, "request op"),
        ({"op": "judge", "kind": "unknown"}, "judge kind"),
        ({"op": "judge", "kind": "score", "max": -1}, "numeric judge max"),
    ],
)
def test_relay_rejects_invalid_protocol_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: dict[str, object],
    error: str,
) -> None:
    channel = create_channel(tmp_path / "run")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert relay_supervisor(channel, "run", 1, timeout=1) == 1
    assert error in capsys.readouterr().err


def test_relay_rejects_malformed_persisted_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    channel = create_channel(tmp_path / "run")
    atomic_json(channel / "planner-verdict.json", {"completion": "yes"})
    monkeypatch.setattr("sys.stdin", io.StringIO('{"op":"judge","kind":"boolean"}'))
    assert relay_supervisor(channel, "run", 1, timeout=1) == 1
    assert "persisted planner completion" in capsys.readouterr().err


def test_relay_boolean_defaults_incomplete_without_persisted_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    channel = create_channel(tmp_path / "run")
    monkeypatch.setattr("sys.stdin", io.StringIO('{"op":"judge","kind":"boolean"}'))
    assert relay_supervisor(channel, "run", 1, timeout=1) == 0
    assert json.loads(capsys.readouterr().out)["value"] is False


@pytest.mark.parametrize(
    "argv,error",
    [
        (["0", "--timeout", "1"], "round must be"),
        (["1", "--timeout", "1", "wrong-run"], "unrecognized arguments"),
    ],
)
def test_relay_cli_rejects_invalid_arguments(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    error: str,
) -> None:
    channel = create_channel(tmp_path / "run")
    args = [str(channel), "run", *argv]
    with pytest.raises(SystemExit):
        main_relay(args)
    assert error in capsys.readouterr().err


def test_relay_cli_rejects_wrong_channel_and_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wrong = create_channel(tmp_path / "other")
    with pytest.raises(SystemExit):
        main_relay([str(wrong), "run", "1", "--timeout", "1"])
    assert "must belong" in capsys.readouterr().err

    channel = create_channel(tmp_path / "run")
    (channel / "channel.json").write_text('{"schema_version":2}', encoding="utf-8")
    with pytest.raises(SystemExit):
        main_relay([str(channel), "run", "1", "--timeout", "1"])
    assert "invalid metadata" in capsys.readouterr().err


def test_finished_tolerates_partial_report_and_missing_status(tmp_path: Path) -> None:
    run = tmp_path / "run"
    report = run / "orchestrator" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text("{", encoding="utf-8")
    assert _finished(run) is False
    (run / "round-01").mkdir()
    assert _finished(run) is False


def test_surface_ignores_non_json_assistant_content() -> None:
    value = _surface(
        {"task": "fallback", "messages": [{"role": "assistant", "content": "plain text"}]},
        "run",
        1,
    )
    assert value["surface"] == {"kind": "supervisor", "message": "fallback"}


def test_surface_accepts_emitted_shape_without_options() -> None:
    value = _surface(
        {
            "task": "fallback",
            "messages": [{"role": "assistant", "content": '{"kind":"closeout","message":"done"}'}],
        },
        "run",
        1,
    )
    assert value["surface"] == {"kind": "closeout", "message": "done"}


def test_finished_reports_live_local_owner_as_running(tmp_path: Path) -> None:
    run = tmp_path / "run"
    round_dir = run / "round-01"
    round_dir.mkdir(parents=True)
    atomic_json(
        round_dir / "status.json",
        {"status": "running", "pid": os.getpid(), "host": socket.gethostname()},
    )
    assert _finished(run) is False


def test_reader_waits_through_fifo_rendezvous_before_writer_arrives(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")

    def delayed_send() -> None:
        time.sleep(0.02)
        write_message(channel / "up.fifo", {"ready": True}, timeout=1)

    sender = threading.Thread(target=delayed_send)
    sender.start()
    assert read_message(channel / "up.fifo", timeout=1) == {"ready": True}
    sender.join()


def test_reader_times_out_after_writer_closes_without_frame(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")

    def close_without_frame() -> None:
        fd = os.open(channel / "up.fifo", os.O_WRONLY)
        os.close(fd)

    sender = threading.Thread(target=close_without_frame)
    sender.start()
    with pytest.raises(ChannelTimeout):
        read_message(channel / "up.fifo", timeout=0.02)
    sender.join()


@pytest.mark.parametrize("content", ["[]", "{}"])
def test_surface_ignores_json_without_emitted_surface_shape(content: str) -> None:
    value = _surface(
        {"task": "fallback", "messages": [{"role": "assistant", "content": content}]},
        "run",
        1,
    )
    assert value["surface"] == {"kind": "supervisor", "message": "fallback"}


def _round(run_dir: Path, *, running: bool = True) -> None:
    """Record one claimed round owned by this live process."""
    round_dir = run_dir / "round-01"
    round_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(round_dir / "plan.json", {"tasks": []})
    atomic_json(
        round_dir / "status.json",
        {
            "status": "running" if running else "completed",
            "pid": os.getpid(),
            "host": socket.gethostname(),
        },
    )


def test_accepted_commands_survive_the_frame_and_are_claimed_once(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "queue-run")
    commands = parse_commands(
        {
            "version": 1,
            "commands": [
                {"op": "attest", "ref": "approve"},
                {"op": "complete", "reason": "verified"},
            ],
        }
    )
    assert submit_commands(channel, commands, round_number=1) == (1, 2)
    assert [item.seq for item in pending_commands(channel)] == [1, 2]
    claimed = claim_commands(channel)
    assert [item.command.payload for item in claimed] == [
        {"op": "attest", "ref": "approve"},
        {"op": "complete", "reason": "verified"},
    ]
    assert claim_commands(channel) == ()
    assert pending_commands(channel) == ()
    assert submit_commands(channel, (), round_number=1) == ()


def test_queue_skips_unreadable_records_and_rejects_a_broken_cursor(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "queue-junk")
    (channel / "commands.jsonl").write_text(
        "\n".join(
            [
                "not json",
                json.dumps({"seq": "x", "round": 1, "command": {"op": "attest", "ref": "a"}}),
                json.dumps({"seq": 4, "round": 1, "command": {"op": "nonsense"}}),
                json.dumps([1, 2]),
                json.dumps({"seq": 7, "round": 2, "command": {"op": "attest", "ref": "a"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert [item.seq for item in pending_commands(channel)] == [7]
    atomic_json(channel / "commands-cursor.json", {"consumed": -1})
    with pytest.raises(ChannelError, match="command cursor is invalid"):
        claim_commands(channel)


def test_live_round_names_why_an_edit_cannot_be_applied(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "orch"
    run_dir.mkdir(parents=True)
    with pytest.raises(ChannelError, match="no recorded round"):
        live_round(run_dir)
    _round(run_dir, running=False)
    with pytest.raises(ChannelError, match="is not executing"):
        live_round(run_dir)
    _round(run_dir)
    assert live_round(run_dir) == 1
    atomic_json(run_dir / "round-01" / "result.json", {"ok": True})
    with pytest.raises(ChannelError, match="is not executing"):
        live_round(run_dir)


def test_validate_commands_rejects_an_unreadable_graph(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "orch"
    run_dir.mkdir(parents=True)
    commands = parse_commands({"version": 1, "commands": [{"op": "attest", "ref": "approve"}]})
    with pytest.raises(ChannelError, match="cannot validate against the live graph"):
        validate_commands(run_dir, 1, commands)


def test_main_reply_refuses_an_edit_with_no_live_round(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    run_dir = runs / "orch"
    create_channel(run_dir)
    reply = tmp_path / "edit.json"
    reply.write_text(
        json.dumps({"version": 1, "commands": [{"op": "attest", "ref": "approve"}]}),
        encoding="utf-8",
    )
    assert main_reply(["orch", str(reply), "--runs-dir", str(runs)]) == 2
    assert "no graph edit can be applied" in capsys.readouterr().err
    assert not (run_dir / "channel" / "commands.jsonl").exists()


def test_main_reply_reports_accepted_edits_when_queuing_the_reply_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Edits are durable before the reply is, so a later failure must say so.

    Nothing about the reply can fail for want of a reader any more, but the write
    itself still can — a full or unwritable run directory — and the accepted edits
    are already applied-or-answered by then. Reporting that as "nothing happened"
    is what would get them resubmitted, and applied twice.
    """
    runs = tmp_path / "runs"
    run_dir = runs / "orch"
    create_channel(run_dir)
    _round(run_dir)
    monkeypatch.setattr("orchestrator.channel.validate_commands", lambda *args: None)

    def refuse(channel_dir: Path, response: object) -> int:
        raise OSError("no space left on device")

    monkeypatch.setattr("orchestrator.channel.submit_reply", refuse)
    reply = tmp_path / "edit.json"
    reply.write_text(
        json.dumps({"version": 1, "commands": [{"op": "attest", "ref": "approve"}]}),
        encoding="utf-8",
    )
    assert main_reply(["orch", str(reply), "--runs-dir", str(runs)]) == 2
    assert "edit(s) #1 were accepted" in capsys.readouterr().err
    assert [item.seq for item in pending_commands(run_dir / "channel")] == [1]


@pytest.mark.parametrize(
    "record",
    [
        "not json at all",
        json.dumps(["not", "a", "mapping"]),
        json.dumps({"seq": 0, "reply": {"completion": True, "reason": "seq below one"}}),
        json.dumps({"seq": True, "reply": {"completion": True, "reason": "bool is not a seq"}}),
        json.dumps({"seq": 1, "reply": "not a mapping"}),
        json.dumps({"seq": 1, "reply": {"completion": "not a boolean"}}),
    ],
)
def test_an_unreadable_queued_reply_is_skipped_rather_than_served(
    tmp_path: Path, record: str
) -> None:
    """The queue is a file on disk, so a damaged record must not become a verdict.

    Each of these would otherwise be handed to the orchestrator as the planner's
    answer. They are skipped instead, and the well-formed reply behind them is still
    claimed — a reader that stopped at the first bad line would strand it.
    """
    channel = create_channel(tmp_path / "damaged-run")
    good = {"completion": False, "message": "keep going", "reason": "keep going"}
    (channel / "replies.jsonl").write_text(
        f"{record}\n" + json.dumps({"seq": 2, "reply": good}) + "\n", encoding="utf-8"
    )

    assert [item.seq for item in pending_replies(channel)] == [2]
    assert await_reply(channel, timeout=0.1) == good
    assert claim_reply(channel) is None


def test_a_damaged_record_still_holds_the_sequence_it_was_written_with(tmp_path: Path) -> None:
    """Allocating over a record this reader cannot parse would break exactly-once.

    The unreadable record is skipped when replies are *served*, but it still occupies
    a sequence: reusing it would give two replies one number, and the single cursor
    advance that answers one would answer the other too.
    """
    channel = create_channel(tmp_path / "reused-run")
    assert submit_reply(channel, {"completion": True, "reason": "first"}) == 1
    with (channel / "replies.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "reply": {"completion": "torn"}}\n')

    assert submit_reply(channel, {"completion": True, "reason": "third"}) == 3
    assert [item.seq for item in pending_replies(channel)] == [1, 3]


def test_a_repeated_or_reordered_sequence_never_rides_one_cursor_advance(
    tmp_path: Path,
) -> None:
    """The cursor is one high-water mark, so the queue's own ordering is a boundary.

    A duplicate or backwards sequence would be consumed by the advance that answered
    the record before it — one claim, two replies, and the second lost with no reader
    ever seeing it. Both are dropped, and the next well-ordered reply still serves.
    """
    channel = create_channel(tmp_path / "disordered-run")
    good = {"completion": False, "message": "keep going", "reason": "keep going"}
    (channel / "replies.jsonl").write_text(
        "\n".join(
            json.dumps({"seq": seq, "reply": {**good, "reason": reason}})
            for seq, reason in ((2, "first"), (2, "duplicate"), (1, "backwards"), (3, "next"))
        )
        + "\n",
        encoding="utf-8",
    )

    assert [item.seq for item in pending_replies(channel)] == [2, 3]
    claimed = claim_reply(channel)
    assert claimed is not None and claimed.reply["reason"] == "first"
    survivor = claim_reply(channel)
    assert survivor is not None and survivor.reply["reason"] == "next"
    assert claim_reply(channel) is None


def test_reply_queue_reads_refuse_an_invalid_cursor_and_an_unbounded_wait(
    tmp_path: Path,
) -> None:
    """Both bounds this queue stands on are checked rather than assumed.

    A cursor that is not a count would silently re-serve or silently swallow every
    queued reply, and a non-finite deadline never compares true — the wait it bounds
    would never end.
    """
    channel = create_channel(tmp_path / "invalid-run")
    submit_reply(channel, {"completion": True, "reason": "verified"})
    for unbounded in (float("nan"), float("inf"), -1.0):
        with pytest.raises(ChannelError, match="finite and non-negative"):
            await_reply(channel, timeout=unbounded)
        with pytest.raises(ChannelError, match="finite and non-negative"):
            await_reply_delivery(channel, 1, timeout=unbounded)

    atomic_json(channel / "replies-cursor.json", {"consumed": "all of them"})
    with pytest.raises(ChannelError, match="reply cursor is invalid"):
        claim_reply(channel)


def test_pump_rejects_a_command_left_over_from_another_round(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "stale-run")
    commands = parse_commands({"version": 1, "commands": [{"op": "attest", "ref": "approve"}]})
    submit_commands(channel, commands, round_number=1)
    pump = ProposalPump(channel, "stale-run", 2)
    try:
        assert pump.drain_commands() == ()
        pending = channel / "planner-pending.json"
        deadline = time.monotonic() + 2
        while not pending.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        surface = json.loads(pending.read_text(encoding="utf-8"))
    finally:
        pump.close()
    assert "rejected attest: submitted for round 1" in surface["message"]
    assert pending_commands(channel) == ()


def _live_graph_run(tmp_path: Path) -> Path:
    """One recorded, executing round whose human node is waiting."""
    from orchestrator.journal import open_journal
    from orchestrator.runs import NodeId, RunId

    runs = tmp_path / "runs"
    run_dir = runs / "orch"
    create_channel(run_dir)
    _round(run_dir)
    journal = open_journal(run_dir, RunId("orch"), 1)
    journal.append(
        "node-added", detail={"definition": {"id": "approve", "kind": "human", "task": "Approve"}}
    )
    journal.append(
        "node-added", detail={"definition": {"id": "ship", "persona": "engineer", "task": "Ship"}}
    )
    journal.append("edge-added", detail={"from": "approve", "to": "ship"})
    journal.append("round-started", detail={"plan": {"schema_version": 5, "concurrency": 2}})
    journal.append(
        "human-waiting",
        node=NodeId("approve"),
        detail={"task": "Approve", "result": {"status": "waiting", "kind": "human"}},
    )
    return runs


def test_validate_commands_accepts_an_attest_and_refuses_a_repeat(tmp_path: Path) -> None:
    runs = _live_graph_run(tmp_path)
    once = parse_commands({"version": 1, "commands": [{"op": "attest", "ref": "approve"}]})
    validate_commands(runs / "orch", 1, once)
    twice = parse_commands(
        {
            "version": 1,
            "commands": [{"op": "attest", "ref": "approve"}, {"op": "attest", "ref": "approve"}],
        }
    )
    with pytest.raises(ChannelError, match="command #1 \\(attest\\)"):
        validate_commands(runs / "orch", 1, twice)


def test_main_reply_queues_a_validated_edit_beside_the_reply_that_carried_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _live_graph_run(tmp_path)
    channel = runs / "orch" / "channel"
    reply = tmp_path / "edit.json"
    reply.write_text(
        json.dumps({"version": 1, "commands": [{"op": "attest", "ref": "approve"}]}),
        encoding="utf-8",
    )
    # No reconciler is draining, so the edit is durably accepted but unanswered: that
    # is neither success nor a rejection, and the caller is told not to resubmit.
    assert main_reply(["orch", str(reply), "--runs-dir", str(runs), "--timeout", "0.2"]) == 1
    shown = capsys.readouterr()
    assert "were accepted but not reconciled" in shown.err
    assert json.loads(shown.out) == {"reply": 1, "state": "queued"}
    carried = pending_replies(channel)
    assert [item.reply["commands"] for item in carried] == [[{"op": "attest", "ref": "approve"}]]
    queued = pending_commands(channel)
    assert [(item.seq, item.round, item.command.payload) for item in queued] == [
        (1, 1, {"op": "attest", "ref": "approve"})
    ]

    # Once the reconciler answers it, the same wait reports success.
    record_command_outcome(channel, 1, applied=True, reason="applied attest")
    reply.write_text(
        json.dumps({"version": 1, "commands": [{"op": "attest", "ref": "approve"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("orchestrator.channel.validate_commands", lambda *args: None)
    threading.Thread(
        target=lambda: (
            time.sleep(0.05),
            record_command_outcome(channel, 2, applied=True, reason="applied attest"),
        ),
        daemon=True,
    ).start()
    assert main_reply(["orch", str(reply), "--runs-dir", str(runs), "--timeout", "5"]) == 0


def test_main_reply_refuses_an_inapplicable_edit_without_queueing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _live_graph_run(tmp_path)
    monkeypatch.setattr(
        "orchestrator.channel.write_message",
        lambda path, value, timeout: pytest.fail("an inapplicable edit must not be sent"),
    )
    reply = tmp_path / "edit.json"
    reply.write_text(
        json.dumps({"version": 1, "commands": [{"op": "attest", "ref": "ship"}]}),
        encoding="utf-8",
    )
    assert main_reply(["orch", str(reply), "--runs-dir", str(runs)]) == 2
    assert "attest requires a currently-ready human action" in capsys.readouterr().err
    assert pending_commands(runs / "orch" / "channel") == ()


def test_main_reply_keeps_a_bare_completion_legal_at_a_round_boundary(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    channel = create_channel(runs / "orch")
    reply = tmp_path / "complete.json"
    reply.write_text(
        json.dumps({"version": 1, "commands": [{"op": "complete", "reason": "verified"}]}),
        encoding="utf-8",
    )
    assert main_reply(["orch", str(reply), "--runs-dir", str(runs)]) == 0
    assert [item.reply["completion"] for item in pending_replies(channel)] == [True]
    assert not (channel / "commands.jsonl").exists()


def test_a_command_that_loses_the_applicability_race_is_rejected_to_its_submitter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Apply-or-reject, end to end, against the real reconciler.

    Submission validates against the authoritative log; the reconciler validates
    against the live frontier. When those disagree — which is what losing a race to
    a concurrently settling node looks like — the command is not quietly downgraded
    to a proposal: `channel-reply` itself exits non-zero with the reconciler's
    reason. The divergence is set up directly here (the log records a waiting human
    action the running graph no longer has) because a genuine race window is
    microseconds wide and cannot be held open through the CLI.
    """
    from orchestrator.graph import parse_graph, run_graph

    runs = _live_graph_run(tmp_path)
    run_dir = runs / "orch"
    channel = run_dir / "channel"
    release = threading.Event()
    graph = parse_graph({"tasks": [{"id": "ship", "persona": "engineer", "task": "Ship"}]})

    def blocking_runner(node: object, **_: object) -> object:
        from orchestrator.dispatch import Report

        release.wait(timeout=30)
        return Report("engineer", 0, True, False, 1, [], {}, {}, "")

    pump = ProposalPump(channel, "orch", 1)
    reconciler = threading.Thread(
        target=run_graph,
        args=(graph,),
        kwargs={
            "agent_runner": blocking_runner,
            "lifecycle_runner": lambda node, **_: None,
            "proposal_pump": pump,
        },
        daemon=True,
    )
    reconciler.start()
    try:
        reply = tmp_path / "raced.json"
        reply.write_text(
            json.dumps({"version": 1, "commands": [{"op": "attest", "ref": "approve"}]}),
            encoding="utf-8",
        )
        assert main_reply(["orch", str(reply), "--runs-dir", str(runs), "--timeout", "10"]) == 2
    finally:
        release.set()
        reconciler.join(timeout=30)
        pump.close()
    error = capsys.readouterr().err
    assert "was rejected by the reconciler" in error
    assert "attest requires a currently-ready human action" in error
    outcomes = command_outcomes(channel)
    assert [(seq, outcome.applied) for seq, outcome in outcomes.items()] == [(1, False)]
    # And the durable queue is answered, not merely consumed: nothing is left pending.
    assert pending_commands(channel) == ()


def test_a_claimed_command_the_reconciler_never_answered_is_rejected_at_teardown(
    tmp_path: Path,
) -> None:
    """Claiming advances the cursor before a verdict exists, so teardown must sweep it.

    A reconciler that stops between claiming a command and deciding it would
    otherwise leave that command consumed and unanswered — indistinguishable, to a
    submitter waiting on its verdict, from one still queued.
    """
    channel = create_channel(tmp_path / "unanswered-run")
    commands = parse_commands(
        {
            "version": 1,
            "commands": [{"op": "attest", "ref": "approve"}, {"op": "attest", "ref": "release"}],
        }
    )
    submit_commands(channel, commands, round_number=1)
    pump = ProposalPump(channel, "unanswered-run", 1)
    claimed = pump.drain_commands()
    assert [item.seq for item in claimed] == [1, 2]
    # Only the first is decided; the round then ends.
    pump.record_outcome(1, applied=True, reason="applied attest")
    assert pending_commands(channel) == ()  # the cursor already consumed both
    assert [item.seq for item in unanswered_commands(channel)] == [2]

    pump.close()
    outcomes = command_outcomes(channel)
    assert outcomes[1].applied is True
    assert outcomes[2].applied is False
    assert "finished before the reconciler answered it" in outcomes[2].reason
    assert unanswered_commands(channel) == ()


def test_a_reply_timeout_that_cannot_bound_the_wait_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The wait for a verdict is only a contract if its bound is a real one."""
    runs = _live_graph_run(tmp_path)
    reply = tmp_path / "edit.json"
    reply.write_text(json.dumps({"completion": True, "reason": "done"}), encoding="utf-8")
    for bad in ("nan", "0", "-1"):
        assert main_reply(["orch", str(reply), "--runs-dir", str(runs), "--timeout", bad]) == 2
        assert "timeout must be a positive, finite number" in capsys.readouterr().err
    with pytest.raises(ChannelError, match="finite and non-negative"):
        await_command_outcomes(runs / "orch" / "channel", (1,), timeout=float("nan"))


def test_a_claim_whose_holder_died_is_reclaimed_by_the_next_tick(tmp_path: Path) -> None:
    """A lease outliving its holder must not silence the run.

    The claimant here is a real second process that takes the lease through the real
    `claim_heartbeat` and then exits without ever reaching `finish_heartbeat_attempt`
    — exactly what a check-in dispatch killed mid-flight leaves behind. Nothing else
    in this repository reclaims a claim, and the pacemaker's only release ran on the
    failure path, so a holder that simply went away held it for the life of the run.
    The probe is `process_may_be_live`, the same one the abandoned-round path uses.
    """
    channel = create_channel(tmp_path / "run", heartbeat_interval=10)
    initial = _heartbeat(channel)
    mark_heartbeat_due(channel, now=float(initial["last_surface_at"]) + 11)

    claimant = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys;from pathlib import Path;"
            "from orchestrator.channel import claim_heartbeat;"
            "sys.exit(0 if claim_heartbeat(Path(sys.argv[1])) else 1)",
            str(channel),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert claimant.returncode == 0, claimant.stderr
    held = _heartbeat(channel)
    assert held["in_flight"] is True
    assert held["claim"] is not None
    assert held["claim"]["pid"] > 0 and held["claim"]["pid"] != os.getpid()
    assert held["claim"]["host"] == socket.gethostname()
    assert held["due"] is True

    # That process is gone, so the next tick takes the lease rather than deferring to
    # a holder that can never hand it back.
    assert claim_heartbeat(channel) is True
    reclaimed = _heartbeat(channel)
    assert reclaimed["claim"]["pid"] == os.getpid()
    # And this holder is alive, so nothing steals it out from under the dispatch.
    assert claim_heartbeat(channel) is False


@pytest.mark.parametrize(
    "host",
    [None, 42, "", {"name": "somewhere"}, ["somewhere"]],
    ids=["absent", "number", "empty", "mapping", "list"],
)
def test_a_claim_with_unreadable_host_metadata_does_not_hold_the_lease(
    tmp_path: Path, host: object
) -> None:
    """The wedge reachable through the claim's *other* field.

    ``process_may_be_live`` answers "may be live" for anything that is not this
    host — correctly, since a claimant on another machine cannot be probed from
    here. That makes a malformed host indistinguishable from a foreign one, so a
    claim carrying one would be deferred to forever and silence the run, which is
    precisely what a lease exists to prevent. A claimant writes
    ``socket.gethostname()``; anything else is metadata nobody can read, and the
    documented policy for unreadable owner metadata is "not a live holder".
    """
    channel = create_channel(tmp_path / "run", heartbeat_interval=10)
    initial = _heartbeat(channel)
    mark_heartbeat_due(channel, now=float(initial["last_surface_at"]) + 11)
    assert claim_heartbeat(channel)

    # A live pid this process could genuinely probe, so only the host is in question.
    state = _heartbeat(channel)
    claim = dict(state["claim"])
    if host is None:
        claim.pop("host")
    else:
        claim["host"] = host
    state["claim"] = claim
    (channel / "heartbeat.json").write_text(json.dumps(state), encoding="utf-8")

    assert claim_heartbeat(channel) is True
    assert _heartbeat(channel)["claim"]["host"] == socket.gethostname()


def test_a_surface_that_outlives_its_round_is_discarded_and_frees_the_pacemaker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one wedge with no operator remedy: unconsumable, and still the pending one.

    `channel-next` validates a queued frame against the *active* round, so a surface
    still queued when the round transitions could never be read — and while it sat
    there it was the run's one pending update, so no later check-in could replace it.
    Discarded rather than kept consumable:
    it describes a round that has finished, and the check-in that replaces it
    describes the round actually running.
    """
    runs = tmp_path / "runs"
    run_dir = runs / "outlived"
    channel = create_channel(run_dir, heartbeat_interval=10)
    (run_dir / "round-01").mkdir()
    initial = _heartbeat(channel)
    mark_heartbeat_due(channel, now=float(initial["last_surface_at"]) + 11)
    assert claim_heartbeat(channel)
    assert main_surface(["outlived", "round one is verifying", "--runs-dir", str(runs)]) == 0
    finish_heartbeat_attempt(channel)
    assert (channel / HEARTBEAT_SURFACE_FILE).is_file()

    # The transition the planner's retry caused: round 2 opens with round 1's update
    # still queued and unread.
    (run_dir / "round-02").mkdir()
    assert pending_surface_indicator(run_dir) is not None
    assert next_surface(run_dir, timeout=0.01) == {"status": "running", "surface": None}
    assert not (channel / HEARTBEAT_SURFACE_FILE).is_file()
    assert pending_surface_indicator(run_dir) is None

    cleared = _heartbeat(channel)
    assert cleared["in_flight"] is False
    assert cleared["claim"] is None
    # The clock is untouched by the discard, so the pacemaker is due again on the
    # interval measured from the last update a planner actually read.
    mark_heartbeat_due(channel, now=float(cleared["last_attempt_at"]) + 11)
    assert claim_heartbeat(channel) is True
    assert main_surface(["outlived", "round two is dispatching", "--runs-dir", str(runs)]) == 0
    queued = json.loads((channel / HEARTBEAT_SURFACE_FILE).read_text(encoding="utf-8"))
    assert queued["round"] == 2
    assert next_surface(run_dir, timeout=0.01)["surface"]["message"] == "round two is dispatching"
    capsys.readouterr()


def test_the_pacemaker_keeps_firing_while_its_update_sits_unread(tmp_path: Path) -> None:
    """The pacemaker keeps firing behind an update nobody reads.

    Driven through the real `ProposalPump`; only the paid check-in agent is stood in
    for, and its stand-in queues through the same `channel-surface` entry point the
    agent invokes. The regression it guards: the claim was released only on
    consumption, so a run whose planner never read the channel was told least of all.
    """
    runs = tmp_path / "runs"
    run_dir = runs / "unread"
    channel = create_channel(run_dir, heartbeat_interval=0.05)
    (run_dir / "round-01").mkdir()
    queued: list[str] = []

    def dispatch_check_in() -> None:
        message = f"update {len(queued) + 1}"
        assert main_surface(["unread", message, "--runs-dir", str(runs)]) == 0
        queued.append(message)

    pump = ProposalPump(channel, "unread", 1, dispatch_check_in=dispatch_check_in)
    surface = channel / HEARTBEAT_SURFACE_FILE
    try:
        wait_until = time.monotonic() + 10
        while len(queued) < 3 and time.monotonic() < wait_until:
            # Reading the pending surface the way `just monitor` renders it never
            # consumes it, and must not be what stops the run reporting.
            assert pending_surface_indicator(run_dir) is None or surface.is_file()
            time.sleep(0.01)
    finally:
        pump.close()

    assert len(queued) >= 3, queued
    # Exactly one update is ever pending, and it is the newest: the check-in
    # replaces the snapshot nobody read rather than piling a second one beside it.
    assert pending_surfaces(channel) == pending_surfaces(channel)[:1]
    assert json.loads(surface.read_text(encoding="utf-8"))["surface"]["message"] == queued[-1]
    # The staleness the views report is measured from the last update a planner
    # actually read, so refreshing the queue entry cannot reset it.
    state = _heartbeat(channel)
    assert state["last_surface_at"] < state["last_attempt_at"]
