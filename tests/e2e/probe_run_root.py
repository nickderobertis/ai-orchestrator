"""A run root the installed engine will read, built rather than recorded.

Two journeys here need a run whose driver process is gone while a process it dispatched
is still alive, and that is a pairing no recorded run can carry: a recorded pid is a pid
that exited long ago, and the whole question is what the views say while one of the two
is still there. So the record is written here, in the shapes the engine reads it in —
`launch.json`, `plan.json`, `events.jsonl`, and the dispatch registry beside them — and
the engine that reads it is the real one.

llmlint: ignore-file[tests_mirror_real_usage] Composing the record is this file's whole
purpose, and no real launch can be asked for the conditions it is composed for: a launch
whose process is gone beside a dispatch whose is not, a registry entry whose recorded
start time is not its live process's, one carrying no start time, and one that is not the
JSON the engine writes. Only the record is composed; the recipes and the engine that
read it are real. That it resembles what a launch writes is held rather
than assumed — `tests/host_views/test_status_and_host_views_e2e.py` renders both views over a run
root `just orchestrate` wrote, and `tests/test_engine_contracts.py` reconciles every
field of every record here against the installed engine's own declaration of it. The
second is what the first cannot give: a stale key is read past in silence, which is what
this builder was doing with a `plan` key that field had replaced.

llmlint: ignore-file[modern_domain_modeling] These dictionaries are the engine's own
on-disk records, composed once and handed straight to `json.dumps`. Typing them would put
a second statement of a shape this repository does not own beside the engine's, and it
would go stale silently — where a record the engine can no longer read fails the journeys
that drive it. What holds them to the real shape is the reconciliation named above rather
than a type.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from pathlib import Path
from typing import NamedTuple


class Probe(NamedTuple):
    """One journey's own runs root and the run in it.

    Both halves are needed at every use site and neither means anything without the
    other, so they travel as one value rather than as a positional pair.
    """

    root: Path
    run: str


def run_name() -> str:
    """A run id no other journey shares.

    The readings these journeys drive answer about every process on the host, and the
    suite runs four at a time on a machine that is also running live dispatches — so a
    shared run id would make one journey's rendezvous another's, and the assertions
    count them.
    """
    return f"probe-{uuid.uuid4().hex[:12]}"


#: The session a built run belongs to when a caller names none.
#:
#: A default rather than a required argument, because the two journeys this builder was
#: written for are about what the views say of the *machine* and never about who owns a
#: run: they read a root nobody else's session names, and naming one per journey would
#: put an ownership decision in front of every caller that has none to make.
DEFAULT_SESSION = "supervision-readings-e2e"


def run_root(
    root: Path,
    run: str,
    *,
    dispatch_pid: int | None = None,
    session: str = DEFAULT_SESSION,
    driver_pid: int | None = None,
) -> Path:
    """One run root the engine will read: a launch, and one node.

    The launch names a process that does not exist, which is what makes the run read
    `DRIVER DEAD`. When `dispatch_pid` is given, the dispatch registry names a process
    that *does*, which is the pairing the guidance about adopting a dead driver is
    written from and the one no recorded run can carry.

    `session` is who owns the run, which is what every ownership-scoped read compares
    against — `runs --mine`, `stop`'s refusal, and `unwatched`'s whole question of which
    runs to ask about. `driver_pid` inverts the paragraph above: the launch then names a
    live process **and its kernel start time**, so the run reads as one something is
    driving, which is the only state a blocking watch can be armed on. Both halves are
    required for that — a pid alone is a pid the kernel may have handed round again, and
    a stamp that is not the one this host reports is a positive statement that the
    process is somebody else's.
    """
    directory = root / run
    directory.mkdir(parents=True)
    plan = {
        "schema_version": 3,
        "goal": {"text": "prove what the views say about the machine"},
        "name": run,
        "concurrency": 1,
        "tasks": [{"id": "probe-node", "persona": "engineer", "task": "## What\n\nProbe.\n"}],
    }
    (directory / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (directory / "launch.json").write_text(
        json.dumps(
            {
                "run_id": run,
                # No `project`: the engine declares that field for the qualified store
                # project a launch came from, omits it when empty, and reads a record
                # carrying none as one written before a plan came from a store. This
                # run came from no store, so omitting it says exactly that. A `plan`
                # key naming the file beside it is what that field replaced, and the
                # engine reads past one in silence — see
                # `tests/test_engine_contracts.py`, which is what caught this builder
                # writing one.
                "dir": str(directory),
                "graph": "off",
                "launcher": "claude-code",
                "session": session,
                # No process has this id: the kernel's own ceiling is below it, so the
                # run reads as one nothing is driving without racing a real pid. A
                # caller that named a live driver gets that process instead, stamped
                # with the start time this host reports for it.
                "pid": driver_pid if driver_pid is not None else 2**31 - 1,
                "host": socket.gethostname(),
                "started": (
                    f"linux-proc-stat:{_started(driver_pid)}"
                    if driver_pid is not None
                    else time.ctime()
                ),
                "started_at": "2026-01-01T00:00:00.000Z",
                "heartbeat_interval": 1800,
                "adoptions": 0,
            }
        ),
        encoding="utf-8",
    )
    journal = [
        {
            "v": 2,
            "ts": "2026-01-01T00:00:00.000Z",
            "stream": "supervision-readings-e2e",
            "seq": 0,
            "source": "pipeline",
            "kind": "run-started",
            "labels": {"run_id": run},
            "payload": {"plan": plan},
        },
        {
            "v": 2,
            "ts": "2026-01-01T00:00:01.000Z",
            "stream": "supervision-readings-e2e",
            "seq": 1,
            "source": "pipeline",
            "kind": "node-dispatched",
            "labels": {"run_id": run, "node": "probe-node"},
            "payload": {"node": "probe-node"},
        },
    ]
    (directory / "events.jsonl").write_text(
        "".join(f"{json.dumps(event)}\n" for event in journal), encoding="utf-8"
    )
    if dispatch_pid is not None:
        record_dispatch(directory, dispatch_pid)
    return directory


def record_dispatch(
    directory: Path, pid: int, *, started: str | None = None, unstamped: bool = False
) -> Path:
    """Record one dispatch of that run, as the engine records one.

    `started` is the kernel's own start time for the process, which is what tells a live
    dispatch from a stale entry whose pid has since been handed to somebody else. It
    defaults to that process's real one; a journey about the stale case states its own,
    and one about a record carrying no start time at all asks for `unstamped`.
    """
    registry = directory / "dispatches"
    registry.mkdir(exist_ok=True)
    entry = registry / f"{pid}-1.json"
    recorded: dict[str, object] = {
        "node": "probe-node",
        "pid": pid,
        "host": socket.gethostname(),
        "dispatched_at": "2026-01-01T00:00:01.000Z",
    }
    if not unstamped:
        recorded["started"] = f"linux-proc-stat:{started if started is not None else _started(pid)}"
    entry.write_text(json.dumps(recorded), encoding="utf-8")
    return entry


def _started(pid: int) -> str:
    """The kernel's own start time for a process, which is how a pid is told from its reuse."""
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    # Everything after the comm field, which is the only one that can carry a bracket.
    return stat[stat.rindex(")") + 2 :].split()[19]
