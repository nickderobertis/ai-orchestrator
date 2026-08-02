"""Deterministic boundaries of the parked-launch decision.

The real journey — `just runs` and `just status` against live processes with and
without a live child — is `tests/e2e/test_liveness_e2e.py`. These cover the
reader boundaries that journey cannot force: an unreadable ``/proc``, a corrupt
heartbeat or round record, a run that has recorded nothing at all, and the one
process state a journey can only reach through a whole wedged launch — a child
that has exited and been left uncollected.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from orchestrator.liveness import (
    PARKED_AFTER_SECONDS,
    has_live_descendant,
    observe_launch,
    parked_indicator,
)


def _launch(run_dir: Path, *, pid: int, idle_for: float = 600.0) -> None:
    (run_dir / "orchestrator").mkdir(parents=True)
    (run_dir / "launch.json").write_text("{}", encoding="utf-8")
    (run_dir / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": pid, "host": socket.gethostname()}),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")
    stale = time.time() - idle_for
    os.utime(run_dir / "events.jsonl", (stale, stale))
    os.utime(run_dir / "orchestrator" / "status.json", (stale, stale))


def test_a_run_without_an_active_launch_is_never_parked(tmp_path: Path) -> None:
    run_dir = tmp_path / "no-launch"
    run_dir.mkdir()
    liveness = observe_launch(run_dir)
    assert liveness == observe_launch(run_dir)
    assert not liveness.active and not liveness.parked
    assert liveness.threshold == PARKED_AFTER_SECONDS
    assert parked_indicator(run_dir) is None


def test_an_unreadable_proc_resolves_toward_still_working(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("orchestrator.liveness._PROC", tmp_path / "absent-proc")
    assert has_live_descendant(frozenset({os.getpid()})) is True
    assert has_live_descendant(frozenset()) is False


#: A process holding one grandchild in the state named on its command line, and
#: announcing only once that state is an established fact. ``WNOWAIT`` is what makes
#: the zombie one: it waits for the exit and deliberately does not collect it, which
#: is exactly what a wedged orchestrator does to the provider it was mid-turn with.
_TREE = (
    "import os, sys, time\n"
    "child = os.fork()\n"
    "if child == 0:\n"
    "    if sys.argv[1] == 'live':\n"
    "        time.sleep(300)\n"
    "    os._exit(0)\n"
    "if sys.argv[1] == 'zombie':\n"
    "    os.waitid(os.P_PID, child, os.WEXITED | os.WNOWAIT)\n"
    "open(sys.argv[2], 'w').write('ready\\n')\n"
    "time.sleep(300)\n"
)


def _parent_of(grandchild: str, marker: Path) -> subprocess.Popen[bytes]:
    """Start a real process whose one grandchild is left ``live`` or ``zombie``.

    Both sides are real processes rather than a ``/proc`` fixture, because the
    distinction under test is one only the kernel makes.
    """
    process = subprocess.Popen([sys.executable, "-c", _TREE, grandchild, str(marker)])
    guard = time.monotonic() + 30
    while not marker.is_file():
        assert time.monotonic() < guard, f"the {grandchild} tree never announced itself"
        time.sleep(0.01)
    return process


def test_an_unreaped_child_is_an_exit_rather_than_work_in_flight(tmp_path: Path) -> None:
    """A zombie under a launch is the parked launch itself, not evidence against it.

    A wedged orchestrator leaves exactly this: the provider it was mid-turn with has
    exited, and the process that would collect it never will. Counting that entry as
    a live descendant is what kept the parked decision from ever firing on a real
    launch — the live grandchild here is the control that says a working one still
    reads as working.
    """
    for grandchild, expected in (("live", True), ("zombie", False)):
        process = _parent_of(grandchild, tmp_path / f"{grandchild}.ready")
        try:
            assert has_live_descendant(frozenset({process.pid})) is expected
        finally:
            process.kill()
            process.wait(timeout=30)


def test_a_launch_with_no_timed_evidence_is_not_parked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("orchestrator.liveness._parent_map", lambda: {})
    run_dir = tmp_path / "silent"
    (run_dir / "orchestrator").mkdir(parents=True)
    (run_dir / "launch.json").write_text("{}", encoding="utf-8")
    (run_dir / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": os.getpid(), "host": socket.gethostname()}),
        encoding="utf-8",
    )
    monkeypatch.setattr("orchestrator.liveness._mtime", lambda path: None)
    liveness = observe_launch(run_dir, parked_after=1)
    assert liveness.active and liveness.idle_seconds is None and not liveness.parked


def test_corrupt_heartbeat_and_round_records_are_ignored_not_trusted(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("orchestrator.liveness._parent_map", lambda: {})
    run_dir = tmp_path / "corrupt"
    _launch(run_dir, pid=os.getpid())
    (run_dir / "channel").mkdir()
    (run_dir / "channel" / "heartbeat.json").write_text("{ not json", encoding="utf-8")
    round_dir = run_dir / "round-01"
    round_dir.mkdir()
    (round_dir / "status.json").write_text("{ not json", encoding="utf-8")
    stale = time.time() - 600
    os.utime(round_dir / "status.json", (stale, stale))
    indicator = parked_indicator(run_dir, parked_after=60)
    assert indicator is not None
    assert "no child process" in indicator and "10m00s" in indicator


def test_a_recent_surface_keeps_a_quiet_launch_reported_as_running(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("orchestrator.liveness._parent_map", lambda: {})
    run_dir = tmp_path / "surfaced"
    _launch(run_dir, pid=os.getpid())
    (run_dir / "channel").mkdir()
    (run_dir / "channel" / "heartbeat.json").write_text(
        json.dumps({"last_surface_at": time.time()}), encoding="utf-8"
    )
    assert parked_indicator(run_dir, parked_after=60) is None
    (run_dir / "channel" / "heartbeat.json").write_text(
        json.dumps({"last_surface_at": True}), encoding="utf-8"
    )
    assert parked_indicator(run_dir, parked_after=60) is not None


def test_a_running_round_owners_child_counts_as_progress(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "owner-child"
    _launch(run_dir, pid=41)
    round_dir = run_dir / "round-01"
    round_dir.mkdir()
    (round_dir / "status.json").write_text(
        json.dumps({"status": "running", "pid": 42, "host": socket.gethostname()}),
        encoding="utf-8",
    )
    stale = time.time() - 600
    os.utime(round_dir / "status.json", (stale, stale))
    monkeypatch.setattr("orchestrator.liveness._parent_map", lambda: {43: 42, 42: 1, 41: 1})
    assert parked_indicator(run_dir, parked_after=60) is None
    monkeypatch.setattr("orchestrator.liveness._parent_map", lambda: {42: 1, 41: 1})
    assert parked_indicator(run_dir, parked_after=60) is not None
