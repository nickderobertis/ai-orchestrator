"""Process-shared locking and crash-safe state tests."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from orchestrator.coordination import LockTimeout, advisory_lock, atomic_json


def _hold(identity: str, ready: multiprocessing.Event, release: multiprocessing.Event) -> None:
    with advisory_lock(identity):
        ready.set()
        release.wait(5)


def test_advisory_lock_reports_live_owner(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path))
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    process = multiprocessing.Process(target=_hold, args=("shared", ready, release))
    process.start()
    assert ready.wait(2)
    try:
        with (
            pytest.raises(LockTimeout, match=rf"timed out.*pid={process.pid}.*host="),
            advisory_lock("shared", timeout=0.05),
        ):
            pytest.fail("concurrent process acquired an owned lock")
    finally:
        release.set()
    process.join(2)
    assert process.exitcode == 0


def test_atomic_json_interrupted_replace_preserves_old_file(monkeypatch, tmp_path) -> None:
    path = tmp_path / "state.json"
    atomic_json(path, {"generation": 1})

    def interrupted(_source: str, _destination: Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError, match="simulated interruption"):
        atomic_json(path, {"generation": 2})
    assert json.loads(path.read_text()) == {"generation": 1}
    assert not list(tmp_path.glob(".state.json.*"))
