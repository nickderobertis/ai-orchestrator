"""Fair, process-shared serialization for automated repository merges."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NewType, TypedDict, cast

from .coordination import (
    GitLockIdentity,
    ProcessStart,
    advisory_lock,
    atomic_json,
    lock_path,
    observe_harness,
    process_start_identity,
)

_STATE_VERSION = 2
TicketId = NewType("TicketId", str)


class _Ticket(TypedDict):
    id: TicketId
    pid: int
    process_start: ProcessStart


class _QueueState(TypedDict):
    version: int
    tickets: list[_Ticket]


def _queue_path(identity: str) -> Path:
    return lock_path(f"merge-queue:{identity}").with_suffix(".json")


def _state_identity(identity: str) -> str:
    return f"merge-queue-state:{identity}"


def _read_state(path: Path) -> _QueueState:
    if not path.exists():
        return {"version": _STATE_VERSION, "tickets": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or isinstance(value.get("version"), bool)
        or value.get("version") != _STATE_VERSION
    ):
        raise ValueError(f"invalid merge queue state at {path}")
    raw_tickets = value.get("tickets")
    if not isinstance(raw_tickets, list):
        raise ValueError(f"invalid merge queue tickets at {path}")
    tickets: list[_Ticket] = []
    for item in raw_tickets:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or isinstance(item.get("pid"), bool)
            or not isinstance(item.get("pid"), int)
            or isinstance(item.get("process_start"), bool)
            or not isinstance(item.get("process_start"), int)
        ):
            raise ValueError(f"invalid merge queue ticket at {path}")
        tickets.append(
            {
                "id": TicketId(item["id"]),
                "pid": item["pid"],
                "process_start": ProcessStart(item["process_start"]),
            }
        )
    return {"version": _STATE_VERSION, "tickets": tickets}


def _write_state(path: Path, state: _QueueState) -> None:
    atomic_json(path, cast(dict[str, object], state))


def _reap_dead(tickets: list[_Ticket]) -> list[_Ticket]:
    return [
        ticket
        for ticket in tickets
        if process_start_identity(ticket["pid"]) == ticket["process_start"]
    ]


@contextmanager
def merge_queue_turn(identity: GitLockIdentity) -> Iterator[None]:
    """Wait for and hold one FIFO merge turn for a git-lock identity.

    The queue has no resident coordinator. Every same-host contender briefly
    coordinates queue-file updates, reaps tickets whose PID and Linux process
    start token no longer identify a live process, and the process at the head
    executes its own merge context. Queue recovery is not cross-host.
    """
    path = _queue_path(identity)
    pid = os.getpid()
    process_start = process_start_identity(pid)
    if process_start is None:
        raise RuntimeError(f"cannot identify merge queue process {pid}")
    ticket: _Ticket = {
        "id": TicketId(uuid.uuid4().hex),
        "pid": pid,
        "process_start": process_start,
    }
    started = time.monotonic()
    with advisory_lock(_state_identity(identity)):
        state = _read_state(path)
        state["tickets"] = _reap_dead(state["tickets"])
        state["tickets"].append(ticket)
        initial_position = len(state["tickets"])
        _write_state(path, state)

    acquired = False
    try:
        while True:
            with advisory_lock(_state_identity(identity)):
                state = _read_state(path)
                live = _reap_dead(state["tickets"])
                if live != state["tickets"]:
                    state["tickets"] = live
                    _write_state(path, state)
                if live and live[0]["id"] == ticket["id"]:
                    acquired = True
                    break
                if not any(item["id"] == ticket["id"] for item in live):
                    raise RuntimeError("merge queue ticket disappeared while waiting")
            time.sleep(0.05)
        observe_harness(
            "lock-wait",
            {
                "identity": identity,
                "seconds": max(0.0, time.monotonic() - started),
                "acquired": True,
                "position": initial_position,
            },
        )
        yield
    finally:
        with advisory_lock(_state_identity(identity)):
            state = _read_state(path)
            remaining = [item for item in state["tickets"] if item["id"] != ticket["id"]]
            if acquired and len(remaining) == len(state["tickets"]):
                raise RuntimeError("merge queue turn was lost before dequeue")
            state["tickets"] = _reap_dead(remaining)
            _write_state(path, state)
