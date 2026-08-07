"""Real journey: a worker that goes quiet reaches the planner without being chased.

A dispatch that dies mid-turn used to sit invisible. The round records `node-started`
and nothing else, which is exactly what a healthy first turn looks like on this host —
so the only thing that ever noticed was a planner going and looking with a monitor of
its own, minutes or hours later.

This drives the real recipe: `just orchestrate` launches, the worker parks at a
rendezvous so it is genuinely in flight and recording nothing, and the assertion is
the surface a planner actually receives — non-blocking, naming the node, what was last
heard from it, and how long ago.
"""

# llmlint: ignore-file[e2e_not_mocked] Only the paid model is a double — the deterministic
# protocol backend this suite already dispatches through (tests/e2e/fake_backend.py). The
# recipe, onejudge, the channel, the round, and the run journal are real.

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest
import yaml
from rendezvous import Rendezvous
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.graph import STALL_AFTER_ENV

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, held: Rendezvous) -> Path:
    path = tmp_path / "stall-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "worker-stall",
                "tasks": [
                    {
                        "id": "quiet-worker",
                        "persona": "engineer",
                        "task": f"complete-now stall no-assessment{held.sentinels(0)}",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _channel(recipe: str, run_id: str, runs: Path, payload: str | None = None) -> str:
    result = subprocess.run(
        [
            "just",
            recipe,
            run_id,
            "--runs-dir",
            str(runs),
            *(["--timeout", "4"] if not payload else []),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        input=payload,
        timeout=e2e_timeout(120),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _wait_surface(run_id: str, runs: Path, *, wait_seconds: float) -> dict[str, object]:
    wait = deadline(wait_seconds)
    while time.monotonic() < wait:
        value = json.loads(_channel("channel-next", run_id, runs) or "{}")
        if value.get("surface") is not None:
            return value
    raise AssertionError(f"no surface arrived for {run_id}")


def _stop(run_dir: Path) -> None:
    status = run_dir / "orchestrator" / "status.json"
    wait = deadline(30)
    while not status.is_file() and time.monotonic() < wait:
        time.sleep(0.02)
    if not status.is_file():
        return
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(json.loads(status.read_text(encoding="utf-8"))["pid"], signal.SIGKILL)


def test_a_worker_that_records_nothing_surfaces_to_the_planner_mid_round(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    held = Rendezvous.at(tmp_path, "stall")
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            "--detach",
            str(_plan(tmp_path, held)),
            "--runs-dir",
            str(runs),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        # Exported for the whole run rather than passed as a flag, which is the
        # other half of the contract: the round the orchestrator drives is a
        # subprocess two levels down, and this is how a launch reaches it.
        env={**os.environ, STALL_AFTER_ENV: "1"},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )
    assert launched.returncode == 0, launched.stderr
    run_id = str(json.loads(launched.stdout)["run_id"])
    run_dir = runs / run_id
    try:
        held.wait(240)
        # The worker is parked and has recorded nothing. Nothing has failed, no
        # budget has been exceeded, and the planner has asked for nothing.
        stall = _wait_surface(run_id, runs, wait_seconds=240)
        surface = stall["surface"]
        assert isinstance(surface, dict)
        # Non-blocking: a stall is evidence for the planner to act on, not a verdict
        # that stops the round's other workers to ask a question.
        assert (surface["kind"], surface["blocking"]) == ("proposal", False)
        message = str(surface["message"])
        assert message.startswith("quiet-worker: "), message
        assert "nothing recorded since it was dispatched" in message, message
        age = re.search(r"no activity for (\d+)s \(threshold 1s\)", message)
        assert age is not None, message
        assert int(age.group(1)) >= 1, message

        _channel(
            "channel-reply",
            run_id,
            runs,
            json.dumps({"completion": False, "message": "watching it", "reason": "stall noted"}),
        )
        held.let_go()
        while True:
            boundary = _wait_surface(run_id, runs, wait_seconds=240)
            following = boundary["surface"]
            assert isinstance(following, dict)
            if following["kind"] != "proposal":
                break
            _channel(
                "channel-reply",
                run_id,
                runs,
                json.dumps({"completion": False, "message": "carry on", "reason": "noted"}),
            )
        result = json.loads((run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
        assert result["results"]["quiet-worker"]["status"] == "done", result
    finally:
        _stop(run_dir)


def _run_plan(
    tmp_path: Path, runs: Path, onejudge_bin: str, *extra: str
) -> subprocess.CompletedProcess[str]:
    """Drive one round through the real recipe, which is where --stall-after lives."""
    plan = tmp_path / "threshold-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "stall-threshold",
                "tasks": [{"id": "worker", "persona": "engineer", "task": "complete-now"}],
            }
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            "just",
            "run-plan",
            str(plan),
            "--run",
            f"stall-threshold-{len(extra)}-{'-'.join(extra).replace('-', '')[:12] or 'default'}",
            "--runs-dir",
            str(runs),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            *extra,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )


def test_the_round_takes_its_stall_threshold_from_the_command_line(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """`--stall-after` is what sets this per round, so the recipe has to take it."""
    accepted = _run_plan(tmp_path, tmp_path / "runs", onejudge_bin, "--stall-after", "30")

    assert accepted.returncode == 0, accepted.stderr


@pytest.mark.parametrize("threshold", ["0", "-5", "nan", "inf"])
def test_a_stall_threshold_the_round_cannot_watch_with_is_refused(
    tmp_path: Path, onejudge_bin: str, threshold: str
) -> None:
    """A round that accepted one would watch nothing and say it was watching.

    Zero and a negative threshold report every dispatch stalled the instant it
    starts; `nan` makes the comparison silently false forever, and `inf` never
    fires. All four are a watcher that is not one.
    """
    refused = _run_plan(tmp_path, tmp_path / "runs", onejudge_bin, "--stall-after", threshold)

    assert refused.returncode == 2, refused.stdout
    assert "--stall-after" in refused.stderr, refused.stderr
