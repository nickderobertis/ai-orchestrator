"""Real journey: pointing the planner's read-only views at one run by its id.

`launch.json` advertises a run id as the handle for a run, and `just monitor`
already accepts it — but `just status` parsed that positional as an integer and
`just telemetry` accepted no positional at all, so for two days the only way to
inspect a named run was to read `events.jsonl` and `/proc` by hand. The same
launch proves the third defect the manual reading hid: telemetry rendered a node
the journal had recorded as `node-failed` as still `running`.

One real `just orchestrate` launch produces all three: it writes the `launch.json`
this test reads the identifier out of, its round fails one node immediately, and
its other worker parks at a barrier so the round is genuinely unsettled while the
views are read. A second, settled run recorded by the real `just run-plan` is what
makes the scoping assertions mean something — a view that ignored the identifier
would report it too.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from history_store import write_worker_session
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.labels import graph_labels
from orchestrator.runs import NodeId, RunId

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"


def _base(tmp_path: Path) -> Path:
    """The real base config with only the paid provider swapped for the backend."""
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _environment(tmp_path: Path) -> dict[str, str]:
    """The environment one planner session hands the commands it runs."""
    return {
        **os.environ,
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ONEHARNESS_HISTORY_DIR": str(tmp_path / "history"),
    }


@pytest.fixture
def launched(tmp_path: Path) -> Iterator[list[str]]:
    """Force-stop every run this test launched, however the test ended."""
    started: list[str] = []
    yield started
    for run_id in started:
        subprocess.run(
            ["just", "stop", run_id, "--runs-dir", str(tmp_path / "runs"), "--force"],
            cwd=REPO_ROOT,
            env=_environment(tmp_path),
            capture_output=True,
            text=True,
            check=False,
            timeout=e2e_timeout(120),
        )


def _view(command: str, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", command, *args, "--runs-dir", str(tmp_path / "runs")],
        cwd=REPO_ROOT,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )


def _await_unsettled_round_with_a_failed_node(run_dir: Path, ready: Path) -> None:
    """Wait until one node has durably failed while another still holds the round."""
    events = run_dir / "events.jsonl"
    wait = deadline(240)
    while time.monotonic() < wait:
        recorded = events.read_text(encoding="utf-8") if events.is_file() else ""
        if ready.is_file() and '"node-failed"' in recorded:
            assert not (run_dir / "round-01" / "result.json").exists()
            return
        time.sleep(0.05)
    stderr = run_dir / "orchestrator" / "stderr.log"
    detail = stderr.read_text(encoding="utf-8") if stderr.is_file() else "<no stderr>"
    raise AssertionError(f"the run never reached a failed node beside a held one: {detail}")


def test_status_and_telemetry_report_one_run_by_the_id_launch_json_advertises(
    tmp_path: Path, launched: list[str], onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    ready = tmp_path / "held.ready"
    release = tmp_path / "held.release"
    live_plan = tmp_path / "live-plan.json"
    live_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "views-by-id",
                "concurrency": 2,
                "tasks": [
                    {
                        "id": "held",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / 'held.ticks'} live-edit-slow "
                            f"live-edit-ready={ready} live-edit-release={release}"
                        ),
                    },
                    {
                        "id": "boom",
                        "persona": "engineer",
                        "task": "should-fail no-assessment",
                        "max_turns": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = subprocess.run(
        [
            "just",
            "orchestrate",
            str(live_plan),
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
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        check=True,
    )
    live = str(json.loads(launch.stdout)["run_id"])
    launched.append(live)

    # The identifier under test is read back out of the launch record rather than
    # from the launch's own stdout: `launch.json` is what advertises a run id to the
    # planner, so that is the exact string these views have to accept.
    record = json.loads((runs / live / "launch.json").read_text(encoding="utf-8"))
    advertised = str(record["run_id"])
    assert record["commands"]["monitor"] == f"just monitor {advertised}"

    settled_plan = tmp_path / "settled-plan.json"
    settled_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "neighbour",
                "tasks": [
                    {"id": "quick", "persona": "engineer", "task": "complete-now: neighbour"}
                ],
            }
        ),
        encoding="utf-8",
    )
    neighbour = subprocess.run(
        [
            "just",
            "run-plan",
            str(settled_plan),
            "--run",
            "neighbour",
            "--runs-dir",
            str(runs),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
        ],
        cwd=REPO_ROOT,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(240),
        check=False,
    )
    assert neighbour.returncode == 0, neighbour.stderr

    # `just status` reads dispatched work through oneharness' history store, and these
    # journeys drive onejudge's `command` provider — no oneharness runs, so nothing
    # populates it. The sessions are written here in oneharness' own format, carrying
    # exactly the run label a real dispatch stamps through `graph_labels`.
    store = tmp_path / "history" / "views-project"
    store.mkdir(parents=True)
    write_worker_session(
        store / "held-20260731T120000Z-1.jsonl",
        project=tmp_path / "held-worktree",
        name="held",
        prompt="should-fail no-assessment",
        labels=graph_labels(run_id=RunId(advertised), round_number=1, node=NodeId("boom")),
    )
    write_worker_session(
        store / "quick-20260731T120000Z-2.jsonl",
        project=tmp_path / "quick-worktree",
        name="quick",
        prompt="complete-now: neighbour",
        labels=graph_labels(run_id=RunId("neighbour"), round_number=1, node=NodeId("quick")),
    )

    try:
        _await_unsettled_round_with_a_failed_node(runs / live, ready)

        # Defect 2: this positional did not exist, so a run could not be named at all.
        scoped = _view("telemetry", tmp_path, advertised)
        assert scoped.returncode == 0, scoped.stderr
        reported = json.loads(scoped.stdout)
        assert [run["run_id"] for run in reported["runs"]] == [advertised]

        # Defect 3: the journal recorded `boom` as failed, so no read-only view may
        # still call it running. `held` has not settled in the journal, and reads as
        # what it is.
        states = {node["node"]: node["status"] for node in reported["runs"][0]["nodes"]}
        assert states == {"boom": "failed", "held": "running"}

        # Naming a run is the request, so the settled-run filter must not hide it —
        # while the unscoped index keeps its live-work default.
        named_settled = _view("telemetry", tmp_path, "neighbour")
        assert named_settled.returncode == 0, named_settled.stderr
        assert [run["run_id"] for run in json.loads(named_settled.stdout)["runs"]] == ["neighbour"]
        unscoped = json.loads(_view("telemetry", tmp_path).stdout)
        assert [run["run_id"] for run in unscoped["runs"]] == [advertised]
        every = json.loads(_view("telemetry", tmp_path, "--all").stdout)
        assert {run["run_id"] for run in every["runs"]} == {advertised, "neighbour"}

        # Defect 1: this positional parsed as an integer, so the run id never reached
        # the view. The unscoped view sees both runs' dispatched work; naming one
        # selects only the sessions that run labelled.
        every_task = _view("status", tmp_path, "--all")
        assert every_task.returncode == 0, every_task.stderr
        assert "held-worktree" in every_task.stdout
        assert "quick-worktree" in every_task.stdout

        run_status = _view("status", tmp_path, advertised)
        assert run_status.returncode == 0, run_status.stderr
        assert "held-worktree" in run_status.stdout
        assert "quick-worktree" not in run_status.stdout

        # The count positional this argument started as keeps its meaning, and an
        # identifier that names no run is refused rather than read as a count.
        counted = _view("status", tmp_path, "1")
        assert counted.returncode == 0, counted.stderr
        assert counted.stdout.count("Execution checkout:") == 1
        unknown = _view("status", tmp_path, "no-such-run")
        assert unknown.returncode == 2
        assert "no recorded run 'no-such-run'" in unknown.stderr
    finally:
        release.write_text("go\n", encoding="utf-8")
