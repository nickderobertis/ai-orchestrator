"""Process-shared locking and crash-safe state tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from orchestrator.coordination import (
    DEFAULT_LOCK_TIMEOUT,
    LOCK_TIMEOUT_ENV,
    LockTimeout,
    advisory_lock,
    atomic_json,
    lock_timeout_seconds,
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


def _lock_holder(seconds: float) -> subprocess.Popen[str]:
    """Start a process that takes the shared identity and holds it for ``seconds``."""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from orchestrator.coordination import advisory_lock; import sys, time; "
            "seconds = float(sys.argv[1]); "
            "lock = advisory_lock('contended'); lock.__enter__(); "
            "print('locked', flush=True); time.sleep(seconds)",
            str(seconds),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline() == "locked\n"
    return process


def test_contenders_queue_for_an_owned_lock_instead_of_failing(monkeypatch, tmp_path) -> None:
    """A busy resource must make a caller wait its turn, not give up on it."""
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path))
    holder = _lock_holder(0.4)
    try:
        started = time.monotonic()
        with advisory_lock("contended", timeout=30):
            waited = time.monotonic() - started
    finally:
        assert holder.wait(timeout=5) == 0

    assert waited >= 0.3, "the contender was served before the owner released"


def test_a_killed_owner_hands_the_lock_to_the_waiter_already_in_line(monkeypatch, tmp_path) -> None:
    """`flock` releases on death, so a crashed owner cannot wedge the queue."""
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path))
    holder = _lock_holder(60)
    acquired = threading.Event()

    def wait_for_the_lock() -> None:
        with advisory_lock("contended", timeout=30):
            acquired.set()

    waiter = threading.Thread(target=wait_for_the_lock, daemon=True)
    waiter.start()
    try:
        assert not acquired.wait(0.3), "the lock was handed out while its owner held it"
        holder.kill()
        assert acquired.wait(10), "a killed owner left its queue wedged"
    finally:
        holder.wait(timeout=5)
        waiter.join(timeout=10)
        assert not waiter.is_alive()


def test_lock_timeout_defaults_to_minutes_and_is_configurable(monkeypatch) -> None:
    assert lock_timeout_seconds({}) == DEFAULT_LOCK_TIMEOUT
    assert DEFAULT_LOCK_TIMEOUT >= 300, "a queued wait must be bounded in minutes, not seconds"
    assert lock_timeout_seconds({LOCK_TIMEOUT_ENV: "45.5"}) == 45.5

    monkeypatch.setenv(LOCK_TIMEOUT_ENV, "120")
    assert lock_timeout_seconds() == 120


@pytest.mark.parametrize("value", ["", "soon", "0", "-1", "nan", "inf"])
def test_lock_timeout_rejects_an_unusable_bound(value: str) -> None:
    with pytest.raises(ValueError, match=LOCK_TIMEOUT_ENV):
        lock_timeout_seconds({LOCK_TIMEOUT_ENV: value})


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
