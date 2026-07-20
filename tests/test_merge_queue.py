"""Process-boundary tests for fair automated-merge serialization."""

from __future__ import annotations

import json
import multiprocessing
import os
import queue
import signal
import time
from pathlib import Path
from typing import Any

import pytest

from orchestrator.coordination import (
    GitLockIdentity,
    advisory_lock,
    atomic_json,
    reset_harness_observer,
    set_harness_observer,
)
from orchestrator.merge_queue import (
    _process_start_identity,
    _queue_path,
    _read_state,
    _state_identity,
    merge_queue_turn,
)

MP = multiprocessing.get_context("spawn")


def _take_turn(
    identity: GitLockIdentity,
    state_root: str,
    name: str,
    events: multiprocessing.Queue[str],
    release: Any,
) -> None:
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    with merge_queue_turn(identity):
        events.put(name)
        release.wait(10)


def _remove_ticket_process(
    identity: GitLockIdentity, state_root: str, pid: int, expected: int
) -> None:
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    _ticket_count(identity, expected)
    _remove_ticket(identity, pid=pid)


def _kill_after_ticket_count(
    identity: GitLockIdentity, state_root: str, pid: int, expected: int
) -> None:
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    _ticket_count(identity, expected)
    os.kill(pid, signal.SIGKILL)


def _ticket_count(identity: GitLockIdentity, expected: int) -> None:
    path = _queue_path(identity)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with advisory_lock(_state_identity(identity)):
            value = _read_state(path)
            if len(value["tickets"]) == expected:
                return
        time.sleep(0.01)
    raise AssertionError(f"merge queue never reached {expected} tickets")


def _join(process: multiprocessing.Process) -> None:
    process.join(10)
    assert not process.is_alive()
    assert process.exitcode == 0


def _remove_ticket(identity: GitLockIdentity, *, pid: int) -> None:
    path = _queue_path(identity)
    with advisory_lock(_state_identity(identity)):
        state = _read_state(path)
        state["tickets"] = [ticket for ticket in state["tickets"] if ticket["pid"] != pid]
        atomic_json(path, state)


def test_missing_process_start_token_fails_without_creating_queue(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AI_ORCHESTRATOR_PROC_ROOT", str(tmp_path / "empty-proc"))
    identity = GitLockIdentity("git:/missing-start/repository")

    with (
        pytest.raises(RuntimeError, match="cannot identify merge queue process"),
        merge_queue_turn(identity),
    ):
        pytest.fail("an unidentified process must not acquire a turn")
    assert not _queue_path(identity).exists()


def test_waiter_whose_ticket_disappears_fails_instead_of_hanging(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = str(tmp_path / "state")
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", state_root)
    identity = GitLockIdentity("git:/missing-ticket/repository")
    events: multiprocessing.Queue[str] = MP.Queue()
    holder_release = MP.Event()
    holder = MP.Process(
        target=_take_turn, args=(identity, state_root, "holder", events, holder_release)
    )
    holder.start()
    assert events.get(timeout=5) == "holder"
    remover = MP.Process(target=_remove_ticket_process, args=(identity, state_root, os.getpid(), 2))
    remover.start()
    path = _queue_path(identity)

    with (
        pytest.raises(RuntimeError, match="merge queue ticket disappeared while waiting"),
        merge_queue_turn(identity),
    ):
        pytest.fail("a removed waiter must not acquire a turn")
    _join(remover)
    holder_release.set()
    _join(holder)
    assert json.loads(path.read_text(encoding="utf-8"))["tickets"] == []


def test_acquired_turn_disappearing_is_detected_before_completion(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = str(tmp_path / "state")
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", state_root)
    identity = GitLockIdentity("git:/lost-turn/repository")
    path = _queue_path(identity)

    with (
        pytest.raises(RuntimeError, match="merge queue turn was lost before dequeue"),
        merge_queue_turn(identity),
    ):
        remover = MP.Process(
            target=_remove_ticket_process,
            args=(identity, state_root, os.getpid(), 1),
        )
        remover.start()
        _join(remover)
    assert json.loads(path.read_text(encoding="utf-8"))["tickets"] == []


def test_cross_process_turns_are_fifo(tmp_path: Path, monkeypatch) -> None:
    state_root = str(tmp_path / "state")
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", state_root)
    identity = GitLockIdentity("git:/shared/repository")
    events: multiprocessing.Queue[str] = MP.Queue()
    releases = [MP.Event() for _ in range(3)]
    processes: list[multiprocessing.Process] = []

    for index in range(3):
        process = MP.Process(
            target=_take_turn,
            args=(identity, state_root, str(index), events, releases[index]),
        )
        process.start()
        processes.append(process)
        _ticket_count(identity, index + 1)

    assert events.get(timeout=5) == "0"
    releases[0].set()
    assert events.get(timeout=5) == "1"
    releases[1].set()
    assert events.get(timeout=5) == "2"
    releases[2].set()
    for process in processes:
        _join(process)


def test_dead_turn_holder_is_reaped_and_next_process_proceeds(tmp_path: Path, monkeypatch) -> None:
    state_root = str(tmp_path / "state")
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", state_root)
    identity = GitLockIdentity("git:/crashed/repository")
    events: multiprocessing.Queue[str] = MP.Queue()
    never_release = MP.Event()
    holder = MP.Process(
        target=_take_turn,
        args=(identity, state_root, "holder", events, never_release),
    )
    holder.start()
    assert events.get(timeout=5) == "holder"
    killer = MP.Process(
        target=_kill_after_ticket_count,
        args=(identity, state_root, holder.pid, 2),
    )
    killer.start()

    with merge_queue_turn(identity):
        pass
    _join(killer)
    holder.join(5)
    assert holder.exitcode is not None and holder.exitcode < 0


def test_turn_reports_queue_wait_and_enqueue_position(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    observed: list[tuple[str, dict[str, str | float | bool]]] = []
    token = set_harness_observer(lambda kind, detail: observed.append((kind, dict(detail))))
    try:
        with merge_queue_turn(GitLockIdentity("git:/telemetry/repository")):
            pass
    finally:
        reset_harness_observer(token)

    queue_wait = [
        detail
        for kind, detail in observed
        if kind == "lock-wait" and detail["identity"] == "git:/telemetry/repository"
    ]
    assert len(queue_wait) == 1
    assert queue_wait[0]["acquired"] is True
    assert queue_wait[0]["position"] == 1
    assert float(queue_wait[0]["seconds"]) >= 0


def test_queue_state_rejects_boolean_pid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    identity = GitLockIdentity("git:/invalid/repository")
    path = _queue_path(identity)
    path.parent.mkdir(parents=True)
    path.write_text('{"version": 2, "tickets": [{"id": "bad", "pid": true, "process_start": 1}]}')

    with (
        pytest.raises(ValueError, match="invalid merge queue ticket"),
        merge_queue_turn(identity),
    ):
        pass


@pytest.mark.parametrize(
    "tickets",
    [
        {},
        [{"id": 1, "pid": 2, "process_start": 3}],
        [{"id": "bad", "pid": "2", "process_start": 3}],
        [{"id": "bad", "pid": 2, "process_start": True}],
        [{"id": "bad", "pid": 2, "process_start": "3"}],
    ],
)
def test_queue_state_rejects_other_malformed_tickets(
    tmp_path: Path, monkeypatch, tickets: object
) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    identity = GitLockIdentity("git:/invalid/tickets")
    path = _queue_path(identity)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 2, "tickets": tickets}), encoding="utf-8")

    with (
        pytest.raises(ValueError, match="invalid merge queue tickets?"),
        merge_queue_turn(identity),
    ):
        pass


def test_nonexistent_process_has_no_start_identity() -> None:
    assert _process_start_identity(-1) is None
    assert _process_start_identity(2**31 - 1) is None


def test_zombie_process_has_no_start_identity() -> None:
    process = MP.Process(target=os.getpid)
    process.start()
    deadline = time.monotonic() + 5
    stat_path = Path(f"/proc/{process.pid}/stat")
    while time.monotonic() < deadline:
        if (
            stat_path.exists()
            and stat_path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] == "Z"
        ):
            break
        time.sleep(0.01)
    else:
        pytest.fail("child process did not become a zombie")

    assert _process_start_identity(process.pid) is None
    process.join(5)


def test_queue_state_rejects_boolean_version(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    identity = GitLockIdentity("git:/invalid/version")
    path = _queue_path(identity)
    path.parent.mkdir(parents=True)
    path.write_text('{"version": true, "tickets": []}')

    with pytest.raises(ValueError, match="invalid merge queue state"), merge_queue_turn(identity):
        pass


def _current_process_start(pid: int) -> int:
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    return int(fields[19])


def test_recycled_pid_ticket_is_reaped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    identity = GitLockIdentity("git:/recycled-pid/repository")
    path = _queue_path(identity)
    path.parent.mkdir(parents=True)
    pid = os.getpid()
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "tickets": [
                    {"id": "stale", "pid": pid, "process_start": _current_process_start(pid) + 1}
                ],
            }
        )
    )

    with merge_queue_turn(identity):
        state = json.loads(path.read_text(encoding="utf-8"))
        assert len(state["tickets"]) == 1
        assert state["tickets"][0]["id"] != "stale"


def test_matching_pid_and_process_start_ticket_is_retained(tmp_path: Path, monkeypatch) -> None:
    state_root = str(tmp_path / "state")
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", state_root)
    identity = GitLockIdentity("git:/live-owner/repository")
    path = _queue_path(identity)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "tickets": [
                    {
                        "id": "live",
                        "pid": os.getpid(),
                        "process_start": _current_process_start(os.getpid()),
                    }
                ],
            }
        )
    )
    events: multiprocessing.Queue[str] = MP.Queue()
    release = MP.Event()
    waiter = MP.Process(target=_take_turn, args=(identity, state_root, "waiter", events, release))
    waiter.start()
    try:
        _ticket_count(identity, 2)
        with pytest.raises(queue.Empty):
            events.get(timeout=0.2)
    finally:
        waiter.kill()
        waiter.join(5)
