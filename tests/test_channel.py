from __future__ import annotations

import errno
import io
import json
import os
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from orchestrator.channel import (
    ChannelError,
    ChannelTimeout,
    ProposalPump,
    _finished,
    _reply,
    _surface,
    _validated_heartbeat_surface,
    apply_heartbeat_reply,
    create_channel,
    due_indicator,
    heartbeat_state,
    main_approve,
    main_continue,
    main_next,
    main_reject,
    main_relay,
    main_reply,
    main_surface,
    mark_heartbeat_due,
    read_message,
    record_surface,
    relay_supervisor,
    write_message,
)
from orchestrator.coordination import atomic_json


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
    assert _validated_heartbeat_surface(valid, "orch") == valid

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
            _validated_heartbeat_surface(value, "orch")


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
    pump = ProposalPump(
        channel,
        "live",
        3,
        synthesize_heartbeat=lambda: "worker: implementing transport; follow-ups: none",
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
    sender = threading.Thread(
        target=write_message,
        args=(channel / "down.fifo", reply),
        kwargs={"timeout": 1},
    )
    sender.start()
    deadline = time.monotonic() + 1
    verdict = channel / "planner-verdict.json"
    while time.monotonic() < deadline and not verdict.is_file():
        pump.persist_replies()
        time.sleep(0.01)
    sender.join()
    assert json.loads(verdict.read_text(encoding="utf-8")) == reply
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
    pump = ProposalPump(
        channel,
        "continued",
        2,
        synthesize_heartbeat=lambda: "worker: still active; follow-ups: none",
    )
    queued = channel / "heartbeat-surface.json"
    wait_until = time.monotonic() + 0.5
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
def test_convenience_recipe_builds_reply_over_real_fifo(
    tmp_path: Path,
    entrypoint: Callable[[list[str] | None], int],
    arguments: list[str],
    expected: dict[str, object],
) -> None:
    runs = tmp_path / "runs"
    channel = create_channel(runs / "orch")
    received: list[dict[str, object]] = []

    def receive() -> None:
        received.append(read_message(channel / "down.fifo", timeout=1))

    reader = threading.Thread(target=receive)
    reader.start()
    assert entrypoint(["orch", *arguments, "--runs-dir", str(runs)]) == 0
    reader.join()
    assert received == [expected]


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


def test_unanswered_proposal_releases_down_fifo_before_boundary(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    pump = ProposalPump(channel, "live", 1)
    pump.propose("worker", "discovery")
    assert read_message(channel / "up.fifo", timeout=1)["surface"]["kind"] == "proposal"
    pump.close()
    assert not pump._thread.is_alive()

    boundary_reply = {"completion": True, "reason": "closeout verified"}
    sender = threading.Thread(
        target=write_message,
        args=(channel / "down.fifo", boundary_reply),
        kwargs={"timeout": 1},
    )
    sender.start()
    assert read_message(channel / "down.fifo", timeout=1) == boundary_reply
    sender.join()


@pytest.mark.parametrize("failure", ["write", "read", "write_timeout", "read_timeout"])
def test_proposal_pump_stops_on_broken_fifo(
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
        case "read" | "read_timeout":
            monkeypatch.setattr("orchestrator.channel.write_message", lambda *args, **kwargs: None)
            monkeypatch.setattr(
                "orchestrator.channel.read_message",
                (
                    fail_after_timeout
                    if failure == "read_timeout"
                    else lambda *args, **kwargs: (_ for _ in ()).throw(ChannelError("broken"))
                ),
            )
    pump = ProposalPump(channel, "live", 1)
    pump.propose("worker", "discovery")
    target = pump._receiver if failure.startswith("read") else pump._thread
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


def test_nonblocking_surface_cli_writes_real_fifo_and_resets_clock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    run_dir = runs / "orch"
    channel = create_channel(run_dir, heartbeat_interval=1)
    initial = heartbeat_state(channel)
    assert initial is not None
    mark_heartbeat_due(channel, now=float(initial["last_surface_at"]) + 2)
    received: list[dict[str, object]] = []

    def receive() -> None:
        received.append(read_message(channel / "up.fifo", timeout=1))

    reader = threading.Thread(target=receive)
    reader.start()
    assert (
        main_surface(
            ["orch", "worker active; no follow-ups", "--runs-dir", str(runs), "--timeout", "1"]
        )
        == 0
    )
    reader.join()
    assert received == [
        {
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
    ]
    assert _heartbeat(channel)["due"] is False
    assert main_surface(["orch", " ", "--runs-dir", str(runs)]) == 2
    assert "non-empty" in capsys.readouterr().err
    assert main_surface(["orch", "update", "--runs-dir", str(runs), "--timeout", "0"]) == 2
    assert "timeout must be a positive" in capsys.readouterr().err


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
    monkeypatch.setattr(
        "orchestrator.channel.read_message",
        lambda path, timeout: {"completion": False, "message": "retry X", "reason": "gap"},
    )
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
    monkeypatch.setattr(
        "orchestrator.channel.read_message",
        lambda path, timeout: {"completion": True, "reason": "acknowledged"},
    )
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"op": "supervisor", "task": "round complete", "messages": []})),
    )

    assert relay_supervisor(channel, "orch", 1, timeout=1) == 0
    assert sent[0]["surface"] == blocker
    assert json.loads(capsys.readouterr().out)["completion"] is True


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
    monkeypatch.setattr(
        "orchestrator.channel.read_message",
        lambda path, timeout: {"completion": True, "reason": "done"},
    )
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

    received: list[dict[str, object]] = []
    monkeypatch.setattr(
        "orchestrator.channel.write_message",
        lambda path, value, timeout: received.append(dict(value)),
    )
    reply = tmp_path / "reply.json"
    reply.write_text('{"completion":true,"reason":"verified"}', encoding="utf-8")
    assert main_reply(["orch", str(reply), "--runs-dir", str(tmp_path / "runs")]) == 0
    assert received == [{"completion": True, "reason": "verified"}]


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
