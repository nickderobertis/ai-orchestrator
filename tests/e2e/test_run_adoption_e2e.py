"""Real journey: an orphaned run is adopted and driven to completion on its own ledger.

Two runs were orphaned in one night when the orchestrator's post-round model request
died on provider quota, and an unattended run was unrecoverable by design — `just
orchestrate` refuses a run directory that exists, so the only way forward was a new
run id, which strands the journal, the ledger, and every publication anchor.

Everything here is the production path. `just orchestrate` launches, the run's driver
and its whole dispatch tree are then SIGKILLed mid-round exactly as a dead provider
turn leaves them, and `just orchestrate --adopt` attaches a fresh driver. The
assertions are what a planner gets back: the same run id, the same journal continuing
where it left off, the interrupted round re-claimed rather than replaced, and the dead
driver's own evidence preserved rather than buried.

Both refusals are driven against the same live run, because that is the only way they
can be real: a second planner session asking for a run it did not launch, and this
planner asking for one that is still being driven.
"""

# llmlint: ignore-file[e2e_not_mocked] Only the paid model is a double — the deterministic
# protocol backend this suite already dispatches through (tests/e2e/fake_backend.py). The
# recipes, onejudge, the channel, the ledger, the run journal, and process liveness are real.

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest
import yaml
from rendezvous import Rendezvous
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.launch import LAUNCHER_ENVIRONMENT_VARIABLES, read_launch_link
from orchestrator.stop import recorded_owners, run_tree
from orchestrator.watchdog import ProcessId

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"

#: Everything a harness exports to say which session it is, cleared before each launch
#: so the session under test is the one this journey named rather than the developer's.
_LAUNCHER_VARIABLES = (
    *sorted(LAUNCHER_ENVIRONMENT_VARIABLES),
    "CODEX_HOME",
    "ORCHESTRATOR_LAUNCHER",
    "ORCHESTRATOR_LAUNCHER_SESSION",
    "ONEHARNESS_HISTORY_LABELS",
)


def _session_env(tmp_path: Path, session_id: str) -> dict[str, str]:
    environment = {**os.environ}
    for name in _LAUNCHER_VARIABLES:
        environment.pop(name, None)
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment["CLAUDECODE"] = "1"
    environment["CLAUDE_CODE_SESSION_ID"] = session_id
    return environment


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, held: Rendezvous) -> Path:
    path = tmp_path / "adoption-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "adoption",
                "tasks": [
                    {
                        "id": "worker",
                        "persona": "engineer",
                        "task": f"complete-now adoption no-assessment{held.sentinels(0)}",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def reaped() -> Iterator[list[Path]]:
    """Kill every launched orchestrator group a journey leaves behind."""
    directories: list[Path] = []
    yield directories
    for run_dir in directories:
        for owner in recorded_owners(run_dir):
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(owner.pid, signal.SIGKILL)


def _orchestrate(
    runs: Path, env: dict[str, str], onejudge_bin: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "just",
            "orchestrate",
            *arguments,
            "--detach",
            "--runs-dir",
            str(runs),
            "--onejudge-bin",
            onejudge_bin,
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )


def _kill_the_whole_run_tree(run_dir: Path) -> None:
    """End the driver and everything below it the way a provider death leaves them.

    SIGKILL, not `just stop`: a stop lets each owner record its own abandonment, and
    the state adoption exists for is the one nothing got to record — a `running`
    round behind a pid that is simply gone.
    """
    tracked: set[ProcessId] = run_tree(recorded_owners(run_dir))
    assert tracked, "the run recorded no owner to kill"
    for pid in sorted(tracked, reverse=True):
        with suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
    wait = deadline(120)
    while time.monotonic() < wait:
        if not any(_alive(pid) for pid in tracked):
            return
        time.sleep(0.05)
    raise AssertionError(f"processes outlived the kill: {sorted(tracked)}")


def _alive(pid: ProcessId) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return Path(f"/proc/{pid}/stat").is_file()


def _await_round_claimed(run_dir: Path, held: Rendezvous) -> None:
    held.wait(240)
    wait = deadline(240)
    while time.monotonic() < wait:
        if any(owner.source.startswith("round-") for owner in recorded_owners(run_dir)):
            return
        time.sleep(0.05)
    raise AssertionError("the run never claimed a round")


def _await_launched_run(runs: Path) -> Path:
    wait = deadline(120)
    while time.monotonic() < wait:
        found = [item for item in runs.iterdir()] if runs.is_dir() else []
        if found:
            return found[0]
        time.sleep(0.05)
    raise AssertionError(f"no run directory appeared under {runs}")


def _channel(recipe: str, run_id: str, runs: Path, payload: str | None = None) -> str:
    result = subprocess.run(
        ["just", recipe, run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        input=payload,
        timeout=e2e_timeout(180),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _answer_and_await_the_report(run_id: str, runs: Path) -> None:
    """Answer the adopted driver's supervisor question, and let the run finish."""
    wait = deadline(300)
    surface = None
    while surface is None:
        assert time.monotonic() < wait, f"{run_id} never asked the planner anything"
        surface = json.loads(_channel("channel-next", run_id, runs) or "{}").get("surface")
    _channel(
        "channel-reply",
        run_id,
        runs,
        json.dumps({"completion": True, "reason": "verified by the adoption journey"}),
    )
    report = runs / run_id / "orchestrator" / "report.json"
    while not (report.is_file() and report.stat().st_size):
        assert time.monotonic() < wait, f"the adopted orchestrator never finished {run_id}"
        time.sleep(0.05)


def test_an_orphaned_run_is_adopted_and_completed_on_its_original_ledger(
    tmp_path: Path, onejudge_bin: str, reaped: list[Path]
) -> None:
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-adoption-owner")
    stranger = _session_env(tmp_path, "session-another-planner")
    held = Rendezvous.at(tmp_path, "adoption")

    launched = _orchestrate(
        runs,
        planner,
        onejudge_bin,
        str(_plan(tmp_path, held)),
        "--base",
        str(_base(tmp_path)),
        "--skill-command",
        sys.executable,
        str(FAKE_BACKEND),
    )
    assert launched.returncode == 0, launched.stderr
    run_dir = _await_launched_run(runs)
    reaped.append(run_dir)
    run_id = run_dir.name
    _await_round_claimed(run_dir, held)

    # A run another planner launched is refused by name, with no `--force` to get
    # past it: unlike a stop, adopting takes over ongoing work rather than ending it.
    theirs = _orchestrate(runs, stranger, onejudge_bin, "--adopt", run_id)
    assert theirs.returncode == 2, theirs.stdout
    assert "another planner" in theirs.stderr, theirs.stderr

    # And this planner's own run is refused while something is still driving it.
    live = _orchestrate(runs, planner, onejudge_bin, "--adopt", run_id)
    assert live.returncode == 2, live.stdout
    assert "orchestrator process is still running" in live.stderr, live.stderr

    journal = run_dir / "events.jsonl"
    before = journal.read_text(encoding="utf-8").splitlines()
    link = read_launch_link(run_dir)
    assert link is not None
    _kill_the_whole_run_tree(run_dir)
    held.let_go()

    # A run launched before adoption existed has no parameters to replay, and is
    # refused rather than started on guessed ones. Moved aside rather than deleted,
    # so the adoption below is the same run in the same state.
    # llmlint: ignore[tests_mirror_real_usage] There is no command that produces this
    # state, and that is what it is: a run this build did not launch. Every run on
    # this host predating adoption has no relaunch record, and the refusal they get
    # is what this asserts — through the real `just orchestrate --adopt`, on a real
    # run, with only the record the older build would not have written withheld.
    record = run_dir / "orchestrator" / "relaunch.json"
    record.replace(record.with_suffix(".withheld"))
    unreplayable = _orchestrate(runs, planner, onejudge_bin, "--adopt", run_id)
    assert unreplayable.returncode == 2, unreplayable.stdout
    assert "no relaunch record" in unreplayable.stderr, unreplayable.stderr
    record.with_suffix(".withheld").replace(record)

    adopted = _orchestrate(runs, planner, onejudge_bin, "--adopt", run_id)
    assert adopted.returncode == 0, adopted.stderr
    assert json.loads(adopted.stdout)["run_id"] == run_id
    _answer_and_await_the_report(run_id, runs)

    # One run id, and no second run minted beside it.
    assert sorted(item.name for item in runs.iterdir()) == [run_id]
    # The interrupted round was reclaimed rather than replaced, and it settled.
    result = json.loads((run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
    assert result["state"] == "complete", result
    # The journal continued: every record written before the kill is still there, in
    # place, with the round the adopted driver ran appended after it. Only a torn
    # trailing line — the one the kill could have caught mid-write — may be repaired
    # away, which is the journal's own documented recovery.
    after = journal.read_text(encoding="utf-8").splitlines()
    assert after[: len(before) - 1] == before[: len(before) - 1]
    assert len(after) > len(before)
    # The run still names the session that launched it: adoption changes the driver,
    # never the provenance.
    assert read_launch_link(run_dir) == link
    # The adopted driver took a conversation of its own rather than resuming the dead
    # one, and its predecessor's report was preserved instead of truncated.
    effective = (run_dir / "orchestrator" / "effective.onejudge.yaml").read_text(encoding="utf-8")
    assert f"session: orchestrator-{run_id}-adopt1" in effective
    assert (run_dir / "orchestrator" / "report.pre-adopt-1.json").is_file()
