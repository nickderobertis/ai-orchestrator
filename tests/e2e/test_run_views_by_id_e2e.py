"""Real journey: pointing the planner's read-only views at one run by its id.

`launch.json` advertises a run id as the handle for a run, but `just status`
parsed that positional as an integer, `just telemetry` accepted no positional at
all, and `just monitor` — which did accept it — never returned when its output
was captured, which is every automated planner invocation. So for two days the
only way to inspect a named run was to read `events.jsonl` and `/proc` by hand.
The same launch proves the third defect the manual reading hid: a node the journal
had recorded as `node-failed` still rendered as `running`, in telemetry from the
in-flight journal and in `status` from the worktree that outlived it.

One real `just orchestrate` launch produces all three: it writes the `launch.json`
this test reads the identifier out of, its round fails one node immediately, and
its other worker parks at a barrier so the round is genuinely unsettled while the
views are read. A second, settled run recorded by the real `just run-plan` is what
makes the scoping assertions mean something — a view that ignored the identifier
would report it too.
"""

# llmlint: ignore-file[e2e_not_mocked, tests_mirror_real_usage] the run directories, the
# journal, the launch record, and every view under test are real, produced and read
# through the real recipes. Only the dispatched *history* sessions are written directly,
# and only because oneharness is not in this journey at all: the fake seam is onejudge's
# `command` provider, so no oneharness process runs to populate the store `just status`
# reads. That is the same seam and the same reason as tests/e2e/test_status_e2e.py, and
# the real oneharness producer boundary is covered by
# tests/e2e/test_monitor_run_plan_e2e.py::test_history_labels_and_cursor_watch.

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
from orchestrator.monitor import RETURN_BOUND_SECONDS
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
        # `just monitor` promises to return within its own stated bound off a
        # terminal, so that budget is this view's guard rather than a hang guard:
        # exceeding it is the defect, not a slow host.
        timeout=RETURN_BOUND_SECONDS if command == "monitor" else e2e_timeout(180),
    )


def _checked_out_worktree(tmp_path: Path) -> Path:
    """A real checkout whose branch is checked out — evidence of a live dispatch.

    `just status` recognises a still-running workstream by exactly this: a session
    whose project is still a checked-out worktree. A failed node keeps one (a direct
    agent works in the checkout itself and has none of its own to remove), so this is
    the state in which the filesystem says "running" and the journal says "failed".
    """
    worktree = tmp_path / "boom-worktree"
    worktree.mkdir()
    for argv in (
        ["git", "init", "-b", "main", "."],
        ["git", "config", "user.email", "views@example.com"],
        ["git", "config", "user.name", "Views"],
        ["git", "commit", "--allow-empty", "-m", "seed"],
    ):
        subprocess.run(argv, cwd=worktree, check=True, capture_output=True, text=True)
    return worktree


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


def test_every_read_only_view_reports_one_run_by_the_id_launch_json_advertises(
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
            # A run id deliberately unequal to the plan name: both are advertised, and
            # a view that only ever matched a directory would pass on an id that
            # happens to equal the plan's name.
            "--run-id",
            "views-by-id-run",
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
    plan_name = str(record["plan_name"])
    assert record["commands"]["monitor"] == f"just monitor {advertised}"
    assert (advertised, plan_name) == ("views-by-id-run", "views-by-id")

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
        project=_checked_out_worktree(tmp_path),
        name="held",
        prompt="should-fail no-assessment",
        labels=graph_labels(run_id=RunId(advertised), round_number=1, node=NodeId("boom")),
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
        # the view. The unscoped view sees this run's dispatched work; naming a
        # different run selects only the sessions that run labelled, which here is none.
        every_task = _view("status", tmp_path, "--all")
        assert every_task.returncode == 0, every_task.stderr
        assert "boom-worktree" in every_task.stdout

        run_status = _view("status", tmp_path, advertised)
        assert run_status.returncode == 0, run_status.stderr
        assert "boom-worktree" in run_status.stdout
        encoded = _view("status", tmp_path, advertised, "--format", "json")
        assert encoded.returncode == 0, encoded.stderr
        assert [task["task"] for task in json.loads(encoded.stdout)] == ["held"]

        # Defect 3 again, in the view whose evidence is the filesystem: this
        # session's worktree is still checked out, which is the whole basis on which
        # `status` calls a workstream running. The journal has already recorded its
        # node as failed, and the journal wins.
        [reported_task] = json.loads(encoded.stdout)
        assert (reported_task["running"], reported_task["node_state"]) == (False, "failed")
        assert "(failed;" in run_status.stdout
        assert "(running;" not in run_status.stdout

        # Defect 2: `just monitor <run-id>` is what `launch.json` advertises, and a
        # captured invocation of it never returned — so the planner read the journal
        # by hand instead. It returns here, within its own stated bound, and the
        # `_view` timeout above is what enforces that.
        watched = _view("monitor", tmp_path, advertised)
        assert watched.returncode == 0, watched.stderr
        assert f"graph:{advertised}/1/boom  node-failed" in watched.stdout
        assert f"graph:{advertised}/1/held  node-failed" not in watched.stdout
        watched_by_plan_name = _view("monitor", tmp_path, plan_name)
        assert watched_by_plan_name.returncode == 0, watched_by_plan_name.stderr
        assert f"graph:{advertised}/1/boom  node-failed" in watched_by_plan_name.stdout
        unwatchable = _view("monitor", tmp_path, "no-such-run")
        assert unwatchable.returncode == 2
        assert "no recorded run 'no-such-run'" in unwatchable.stderr

        other_run = _view("status", tmp_path, "neighbour")
        assert other_run.returncode == 0, other_run.stderr
        assert other_run.stdout.strip().endswith("No dispatched tasks recorded for run neighbour.")
        assert "boom-worktree" not in other_run.stdout

        # Each view also resolves the plan name the same launch advertises, and
        # lands on the run id rather than on the name.
        by_plan_name = _view("telemetry", tmp_path, plan_name)
        assert by_plan_name.returncode == 0, by_plan_name.stderr
        assert [run["run_id"] for run in json.loads(by_plan_name.stdout)["runs"]] == [advertised]
        status_by_plan_name = _view("status", tmp_path, plan_name)
        assert status_by_plan_name.returncode == 0, status_by_plan_name.stderr
        assert "boom-worktree" in status_by_plan_name.stdout

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
