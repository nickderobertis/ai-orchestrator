"""The supervisory tier, end to end: served spans, local capture, and CLI liveness.

The tier that drives every run was the one tier no view could see. Its sessions run
on the identity chain that puts codex first, whose history writes were failing, so the
orchestrator and its check-ins were absent from every history-derived view — while
being the tier whose deaths orphan runs.

This drives the real executor: a real `just orchestrate` launch, its real detached
onejudge process, its real per-round check-in dispatch, and the real read API and
planner CLIs over the run directory that launch produced. Only the paid model is
faked, through onejudge's own command provider.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
import yaml
from process_tree import is_running
from rendezvous import Rendezvous
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.server import create_app
from orchestrator.supervisory import CAPTURE_DIR

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"
FAKE_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "fake_oneharness.py"


@contextmanager
def _serve(app: object) -> Iterator[str]:
    """Run the read API on an ephemeral loopback port; yield its base URL."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        started = deadline(10)
        while not server.started and time.monotonic() < started:
            time.sleep(0.02)
        assert server.started, "the read API did not start"
        sock: socket.socket = server.servers[0].sockets[0]
        host, port = sock.getsockname()[:2]
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _oneharness_bin(tmp_path: Path, store: Path) -> Path:
    """A stand-in `oneharness history list` serving exactly the sessions ``store`` holds."""
    binary = tmp_path / "oneharness"
    binary.write_text(
        "#!/usr/bin/env python3\n" + FAKE_ONEHARNESS.read_text(encoding="utf-8"), encoding="utf-8"
    )
    binary.chmod(0o755)
    store.write_text(json.dumps({"sessions": []}), encoding="utf-8")
    return binary


def _launch(
    plan: Path,
    runs: Path,
    base: Path,
    onejudge_bin: str,
    heartbeat: str = "0.5",
    **env: str,
) -> str:
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            "--detach",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(base),
            "--onejudge-bin",
            onejudge_bin,
            "--heartbeat-interval",
            heartbeat,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=True,
    )
    return str(json.loads(launched.stdout)["run_id"])


def _plan(tmp_path: Path, witness: Path, hold: Rendezvous) -> Path:
    path = tmp_path / "supervisory-visibility.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "supervisory-visibility",
                "tasks": [
                    {
                        "id": "active-worker",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {witness}{hold.sentinels()} "
                            "complete-now supervisory-visibility"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _status(run_id: str, runs: Path, history: Path) -> str:
    history.mkdir(exist_ok=True)
    viewed = subprocess.run(
        ["just", "status", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEHARNESS_HISTORY_DIR": str(history)},
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    assert viewed.returncode == 0, viewed.stderr
    return viewed.stdout


def _runs(runs: Path, history: Path) -> str:
    history.mkdir(exist_ok=True)
    listed = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEHARNESS_HISTORY_DIR": str(history)},
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    return listed.stdout


def _wait_for(predicate: object, *, seconds: float, what: str) -> None:
    assert callable(predicate)
    waited = deadline(seconds)
    while time.monotonic() < waited:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"never observed: {what}")


def _drain(run_id: str, runs: Path) -> dict[str, object]:
    """Consume surfaces until a non-heartbeat one arrives, and return it."""
    waited = deadline(120)
    while time.monotonic() < waited:
        value = json.loads(
            subprocess.run(
                [
                    "just",
                    "channel-next",
                    run_id,
                    "--runs-dir",
                    str(runs),
                    "--timeout",
                    str(e2e_timeout(2)),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=True,
            ).stdout
        )
        surface = value.get("surface")
        if isinstance(surface, dict) and surface.get("kind") != "heartbeat":
            return value
    raise AssertionError(f"no boundary surface arrived for {run_id}")


# The whole family of journeys that race several real processes through a readiness
# handshake shares one xdist group, so the distribution never has two in flight.
@pytest.mark.load_sensitive
@pytest.mark.xdist_group("load_sensitive")
def test_a_driven_run_serves_its_supervisory_tier_and_names_a_dead_driver(
    tmp_path: Path, onejudge_bin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    witness = tmp_path / "slow-witness"
    # The node stays in flight until every assertion about the live run has been made,
    # so the driver is observed while it is genuinely driving rather than after a sleep.
    hold = Rendezvous.at(tmp_path, "active-worker")
    refused = tmp_path / "check-in-history-write-refused"
    run_id = _launch(
        _plan(tmp_path, witness, hold),
        runs,
        _base(tmp_path),
        onejudge_bin,
        FAKE_CHECK_IN_HISTORY_WRITE_FAILS=str(refused),
        XDG_STATE_HOME=str(tmp_path / "state"),
    )
    run_dir = runs / run_id
    captures = run_dir / CAPTURE_DIR
    driver_capture = captures / f"orchestrator-{run_id}.json"

    # The driver's own capture exists from the moment it was launched — before it has
    # finished a turn, which is exactly the window in which it used to be invisible.
    _wait_for(driver_capture.is_file, seconds=60, what="the driver's capture")
    opened = json.loads(driver_capture.read_text(encoding="utf-8"))
    assert opened["agent_role"] == "orchestrator"
    assert opened["finished_at"] is None
    # The capture lands at launch, before `run-plan` has dispatched anything, so this
    # waits for the node rather than sampling whether it has started yet.
    hold.wait(180)

    # A check-in whose harness refused the history write: the session oneharness never
    # recorded is still captured, and the refusal itself is recorded with it.
    _wait_for(refused.is_file, seconds=120, what="the refused check-in history write")
    check_in_capture = next(
        path for path in captures.iterdir() if path.name.startswith("check-in-")
    )
    _wait_for(
        lambda: (
            json.loads(check_in_capture.read_text(encoding="utf-8")).get("history_failure")
            is not None
        ),
        seconds=60,
        what="the recorded history-write failure",
    )
    recorded = json.loads(check_in_capture.read_text(encoding="utf-8"))
    assert recorded["agent_role"] == "check-in"
    assert recorded["round"] == 1
    # Recorded from the raised dispatch error, not from a returned report: a harness
    # that refuses the write fails the dispatch outright, so the capture has to be
    # closed on the way out rather than after a result nobody gets.
    assert "onejudge failed" in recorded["history_failure"], recorded
    # The pacemaker retries this round's check-in, and the retry reuses this capture;
    # the refusal is what stays true once it has happened, so it is what is asserted.
    assert "lacks complete v1.0 telemetry" in recorded["history_failure"]

    # Served: run-scope spans for both supervisory roles, over the real read API. The
    # store starts empty, which is exactly what a run whose harness refused every
    # history write leaves behind.
    store = tmp_path / "history-store.json"
    binary = _oneharness_bin(tmp_path, store)
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(store))
    app = create_app(runs, oneharness_bin=str(binary))
    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=30)

        # `Any` because these are decoded JSON payloads whose shape is owned by the
        # `dag-model` timeline schemas rather than by this test; restating a span here
        # would be a second contract to drift, which is what that gate exists to stop.
        def _by_role() -> dict[str, Any]:
            spans = client.get(f"/api/v2/runs/{run_id}/timeline", params={"scope": "run"}).json()[
                "spans"
            ]
            return {
                str(span["agent_role"]): span
                for span in spans
                if span["kind"] == "dispatch" and "agent_role" in span
            }

        by_role = _by_role()
        assert sorted(by_role) == ["check-in", "orchestrator"]
        driver_span = by_role["orchestrator"]
        # Still driving: an open-ended span is what a live supervisory session is.
        assert driver_span["ended_at"] is None
        # Exactly the phase the run's own state places it in: `run-plan` owns the
        # round, and the node it dispatched is held at its rendezvous. (`starting` is
        # the sub-second window before `run-plan` writes round-01, which no journey can
        # observe without racing the launch; `tests/test_supervisory.py` walks it.)
        assert driver_span["phase"] == "executing-run-plan", driver_span
        check_in_span = by_role["check-in"]
        assert check_in_span["round"] == 1
        failure = next(
            event for event in check_in_span["events"] if event["kind"] == "history-write-failed"
        )
        assert "lacks complete v1.0 telemetry" in failure["status"]
        # No transcript exists for either, so neither invents a reference to one.
        assert all("reference" not in span for span in by_role.values())

        # `just status` and `just runs` both name the live driver: pid liveness, phase,
        # and how long it has been silent — while it is still driving, which is the
        # window a planner is in when they wonder why nothing is settling.
        owner = json.loads((run_dir / "orchestrator" / "status.json").read_text(encoding="utf-8"))[
            "pid"
        ]
        live_view = _status(run_id, runs, tmp_path / "history")
        assert f"{run_id}: driver running (pid {owner})" in live_view, live_view
        assert "phase " in live_view and "last observed activity " in live_view, live_view
        # This run's check-in had a session the harness refused to record, so the
        # driver's line says so and points at the bounded captures standing in for the
        # transcripts that were never written.
        assert "harness history write failed" in live_view, live_view
        assert "lacks complete v1.0 telemetry" in live_view, live_view
        assert f"{run_id}/{CAPTURE_DIR}/" in live_view, live_view
        live_rows = _runs(runs, tmp_path / "history")
        assert f"    driver running (pid {owner})" in live_rows, live_rows

        # The node was held so far, so the driver's first turn had not ended. Let it
        # finish one: the relay records a bounded turn as each orchestrator turn ends,
        # which is what gives a capture-backed span its transcript.
        hold.let_go()
        _drain(run_id, runs)
        _wait_for(
            lambda: bool(json.loads(driver_capture.read_text(encoding="utf-8"))["turns"]),
            seconds=60,
            what="a captured orchestrator turn",
        )
        driver_span = _by_role()["orchestrator"]
        assert driver_span["detail"]["output_tail"]
        # And the phase moved with the run: that surface is unanswered, so what the
        # driver is doing now is waiting on the planner rather than driving a round.
        assert driver_span["phase"] == "surfacing", driver_span

    # And with the driver's session in history, the recorded transcript wins. The store
    # is the one this run's `oneharness history list` reads, written before the server
    # that reads it starts — the same way every history journey here seeds one.
    recorded_store = tmp_path / "recorded-store.json"
    recorded_store.write_text(
        json.dumps({"sessions": [_recorded_driver(tmp_path, run_id)]}), encoding="utf-8"
    )
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(recorded_store))
    with _serve(create_app(runs, oneharness_bin=str(binary))) as base:
        client = httpx.Client(base_url=base, timeout=30)
        served = client.get(f"/api/v2/runs/{run_id}/timeline", params={"scope": "run"}).json()[
            "spans"
        ]
        drivers = [
            span
            for span in served
            if span["kind"] == "dispatch" and span.get("agent_role") == "orchestrator"
        ]
        # The capture stood down rather than drawing the same session twice.
        assert len(drivers) == 1
        assert drivers[0]["reference"]["kind"] == "conversation"
        conversation_id = drivers[0]["reference"]["value"]
        resolved = client.get(f"/api/v2/runs/{run_id}/conversations/{conversation_id}")
        assert resolved.status_code == 200
        assert resolved.json()["conversation"]["id"] == conversation_id
        assert resolved.json()["attribution"]["agentRole"] == "orchestrator"

        # A capture torn by a partial write costs only itself: the run still serves the
        # spans its other sources carry, which is the whole point of an optional source.
        check_in_capture.write_text("{", encoding="utf-8")
        after = client.get(f"/api/v2/runs/{run_id}/timeline", params={"scope": "run"}).json()
        roles = {
            span.get("agent_role")
            for span in after["spans"]
            if span["kind"] == "dispatch" and "agent_role" in span
        }
        assert roles == {"orchestrator"}, after

    # And marks it explicitly once it is gone: the state four stranded runs were in.
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(owner, signal.SIGKILL)
    _wait_for(lambda: not is_running(owner), seconds=30, what="the driver exiting")
    dead_view = _status(run_id, runs, tmp_path / "history")
    assert f"{run_id}: DRIVER DEAD (pid {owner} is gone)" in dead_view, dead_view
    assert "nothing is driving this run" in dead_view, dead_view
    listed = _runs(runs, tmp_path / "history")
    assert "DRIVER DEAD" in listed, listed


def _recorded_driver(tmp_path: Path, run_id: str) -> dict[str, object]:
    """One oneharness history session for the driver, as the store would list it."""
    record = tmp_path / "orchestrator-session.jsonl"
    stamp = "2026-08-06T10:00:00+00:00"
    record.write_text(
        json.dumps(
            {
                "session": f"orchestrator-{run_id}",
                "name": f"orchestrator-{run_id}",
                "project": str(tmp_path),
                "harness": "codex",
                "model": "gpt",
                "timestamp": stamp,
                "prompt": "drive the plan",
                "text": "round 1 dispatched",
                "status": "ok",
                "session_id": f"orchestrator-{run_id}",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "id": f"orchestrator-{run_id}",
        "name": f"orchestrator-{run_id}",
        "project": str(tmp_path),
        "started": stamp,
        "path": str(record),
        "labels": {
            "run_id": run_id,
            "role": "agent",
            "agent_role": "orchestrator",
            "persona": "orchestrator",
        },
    }


@pytest.mark.load_sensitive
@pytest.mark.xdist_group("load_sensitive")
def test_a_driver_that_finished_its_loop_stops_reporting_itself(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """The other end of the same line: a run that ended says so on its own row.

    A finished driver is deliberately silent. Its run already says how it ended, and a
    line per settled run is exactly what would bury the one run whose driver is dying —
    which is the line this whole surface exists for.
    """
    runs = tmp_path / "runs"
    plan = tmp_path / "settled-loop.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "settled-loop",
                "tasks": [{"id": "worker", "persona": "engineer", "task": "complete-now finished"}],
            }
        ),
        encoding="utf-8",
    )
    # No pacemaker here: this journey is about the boundary surface and the reply that
    # ends the loop, and a check-in queueing between the two would answer a surface the
    # planner never read.
    run_id = _launch(
        plan,
        runs,
        _base(tmp_path),
        onejudge_bin,
        heartbeat="600",
        XDG_STATE_HOME=str(tmp_path / "st"),
    )

    _drain(run_id, runs)
    subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps({"completion": True, "reason": "verified"}),
        text=True,
        capture_output=True,
        check=True,
    )
    report = runs / run_id / "orchestrator" / "report.json"
    _wait_for(
        lambda: report.is_file() and report.stat().st_size > 0,
        seconds=120,
        what="the driver's own report",
    )

    reported = _status(run_id, runs, tmp_path / "history")
    assert "driver running" not in reported, reported
    assert "DRIVER DEAD" not in reported, reported
    listed = _runs(runs, tmp_path / "history")
    assert "driver running" not in listed, listed
    assert "DRIVER DEAD" not in listed, listed
