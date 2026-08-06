"""Real journey: a boundary request the provider refuses is asked again, and recorded.

The failure this is for orphaned two runs in one night: a request made at a round
boundary, with the quota that round had just spent, refused once — and the process
that owned the whole run was gone. The heartbeat check-in launch is the same shape
and is covered by the same policy, and it is the half a journey can drive end to end
through the real channel.

Everything here is production: `just orchestrate`, the real onejudge split provider,
the real pacemaker, and the run's own journal. Only the paid model is a double, and
the refusal it writes is the wording a real out-of-quota harness produced.
"""

# llmlint: ignore-file[e2e_not_mocked] Only the paid model is a double — the deterministic
# protocol backend this suite already dispatches through (tests/e2e/fake_backend.py). The
# recipe, onejudge, the channel, the pacemaker, and the run journal are real.

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import yaml
from rendezvous import Rendezvous
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.boundary import ATTEMPTS_ENV, BACKOFF_ENV

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, held: Rendezvous) -> Path:
    """A one-worker plan whose worker is held so the pacemaker fires mid-round."""
    path = tmp_path / "boundary-retry-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "boundary-retry",
                "tasks": [
                    {
                        "id": "worker",
                        "persona": "engineer",
                        "task": f"complete-now boundary-retry no-assessment{held.sentinels(0)}",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _stop(run_dir: Path) -> None:
    status = run_dir / "orchestrator" / "status.json"
    wait = deadline(30)
    while not status.is_file() and time.monotonic() < wait:
        time.sleep(0.02)
    if not status.is_file():
        return
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(json.loads(status.read_text(encoding="utf-8"))["pid"], signal.SIGKILL)


def _journalled_retries(run_dir: Path) -> list[dict[str, object]]:
    journal = run_dir / "events.jsonl"
    if not journal.is_file():
        return []
    return [
        record["detail"]
        for line in journal.read_text(encoding="utf-8").splitlines()
        if (record := json.loads(line)).get("kind") == "boundary-retried"
    ]


def test_a_refused_check_in_launch_is_retried_and_the_journal_counts_the_attempts(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    held = Rendezvous.at(tmp_path, "boundary-retry")
    refused = tmp_path / "provider-refused-once"
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
            "--heartbeat-interval",
            "0.5",
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "FAKE_CHECK_IN_PROVIDER_REFUSE_ONCE": str(refused),
            ATTEMPTS_ENV: "3",
            # Nothing here waits for real seconds: what is under test is that the
            # request is asked again inside one claim, not how long apart.
            BACKOFF_ENV: "0.05",
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )
    assert launched.returncode == 0, launched.stderr
    run_id = str(json.loads(launched.stdout)["run_id"])
    run_dir = runs / run_id
    try:
        held.wait(240)
        # The first check-in launch is refused with an out-of-quota diagnostic, the
        # retry answers, and the update the planner reads is the retry's.
        # Short on purpose: with a half-second pacemaker and a 0.05s backoff the
        # retry lands within seconds, so a run that never retried fails here rather
        # than hanging out a generic guard.
        wait = deadline(90)
        retries: list[dict[str, object]] = []
        while time.monotonic() < wait:
            retries = _journalled_retries(run_dir)
            if retries and (run_dir / "channel" / "heartbeat-surface.json").is_file():
                break
            time.sleep(0.05)
        assert refused.is_file(), "the provider refusal never happened"
        assert retries, "the retried boundary request left no trace in the journal"
        # Attempt counts, so a reader can tell one retry from a run that spent its
        # whole budget riding out an outage.
        assert retries[0]["role"] == "check-in"
        assert retries[0]["attempt"] == 1
        assert retries[0]["attempts"] == 3
        assert (run_dir / "channel" / "heartbeat-surface.json").is_file()
        # And the refusal was not left to the pacemaker's next interval: the
        # dispatch that was refused is the one that then succeeded.
        dispatches = (
            (run_dir / "channel" / "check-in-dispatches.txt")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert dispatches[:2] == ["provider-refused", "success"], dispatches
    finally:
        held.let_go()
        _stop(run_dir)
