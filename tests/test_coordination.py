"""Process-shared locking and crash-safe state tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.coordination import (
    LockTimeout,
    advisory_lock,
    atomic_json,
    reset_harness_observer,
    set_harness_observer,
)


def test_advisory_lock_reports_live_owner(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path))
    script = (
        "from orchestrator.coordination import advisory_lock; import sys; "
        "lock = advisory_lock('shared'); lock.__enter__(); "
        "print('locked', flush=True); sys.stdin.readline(); lock.__exit__(None, None, None)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline() == "locked\n"
    try:
        with (
            pytest.raises(LockTimeout, match=rf"timed out.*pid={process.pid}.*host="),
            advisory_lock("shared", timeout=0.05),
        ):
            pytest.fail("concurrent process acquired an owned lock")
    finally:
        assert process.stdin is not None
        process.stdin.write("release\n")
        process.stdin.flush()
    assert process.wait(timeout=2) == 0


def test_advisory_lock_observes_success_and_timeout_waits(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path))
    observed: list[tuple[str, dict[str, str | float | bool]]] = []
    token = set_harness_observer(lambda kind, detail: observed.append((kind, dict(detail))))
    try:
        with advisory_lock("available"):
            pass
        held = advisory_lock("held")
        held.__enter__()
        try:
            with pytest.raises(LockTimeout), advisory_lock("held", timeout=0.02):
                pass
        finally:
            held.__exit__(None, None, None)
    finally:
        reset_harness_observer(token)

    assert [(kind, detail["acquired"]) for kind, detail in observed] == [
        ("lock-wait", True),
        ("lock-wait", True),
        ("lock-wait", False),
    ]
    assert all(float(detail["seconds"]) >= 0 for _, detail in observed)


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
