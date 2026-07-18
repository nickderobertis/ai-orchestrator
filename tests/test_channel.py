from __future__ import annotations

import io
import json
import os
import socket
import threading
from pathlib import Path

import pytest

from orchestrator.channel import (
    ChannelError,
    ChannelTimeout,
    _finished,
    _reply,
    _surface,
    create_channel,
    main_next,
    main_relay,
    main_reply,
    read_message,
    relay_supervisor,
    write_message,
)
from orchestrator.coordination import atomic_json


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


def test_fifo_timeout_is_bounded(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    with pytest.raises(ChannelTimeout):
        read_message(channel / "up.fifo", timeout=0.01)
    with pytest.raises(ChannelTimeout):
        write_message(channel / "down.fifo", {"value": 1}, timeout=0.01)


def test_frame_and_supervisor_shapes_are_validated(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    too_large = {"value": "x" * 5000}
    with pytest.raises(ChannelError, match="atomic FIFO limit"):
        write_message(channel / "up.fifo", too_large, timeout=0.01)

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


def test_channel_metadata_is_durable(tmp_path: Path) -> None:
    channel = create_channel(tmp_path / "run")
    assert json.loads((channel / "channel.json").read_text()) == {"schema_version": 1}
    assert create_channel(tmp_path / "run") == channel


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
    monkeypatch.setattr(
        "orchestrator.channel.read_message",
        lambda path, timeout: {"op": "supervisor", "surface": {}},
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
