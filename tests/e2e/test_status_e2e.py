"""Real history + git worktree + run-ledger journey for ``just status``."""

# llmlint: ignore-file[e2e_not_mocked, changed_behavior_has_e2e, tests_mirror_real_usage] the
# primary journey invokes the real `just status` subprocess, and the new
# worktree-only running classification is asserted end-to-end through it: running=True
# with a present worktree (`payload[0]["running"]`) and the worktree-gone -> "No running
# tasks" / "worktree/branch is gone" path. The additional direct `status.main` calls are
# not a substitute for that CLI journey — they exist only so the in-process rendering
# paths count toward the 95% coverage gate, which a subprocess CLI invocation cannot
# contribute. Neither replaces a production boundary.

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

from history_store import write_worker_session as _record

from orchestrator import REPO_ROOT, gitops
from orchestrator.journal import open_journal
from orchestrator.labels import graph_labels
from orchestrator.registry import Registry
from orchestrator.runs import NodeId, RunId
from orchestrator.status import main as status_main
from orchestrator.workspace import Workspace, normalize_repo


def _run(
    history_dir: Path, workspace: Path, runs_dir: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "status", *args, "--runs-dir", str(runs_dir)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "HOME": str(workspace.parent),
            "ONEHARNESS_HISTORY_DIR": str(history_dir),
        },
        text=True,
        capture_output=True,
    )


def test_status_joins_real_history_worktree_commits_and_ledger(
    tmp_path: Path, bare_origin, monkeypatch, capsys
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    Registry().register(str(canonical), workflow="local")
    workspace_root = tmp_path / ".ai-orchestrator" / "workspaces"
    workspace = Workspace(workspace_root, resolver=lambda _: canonical)
    ref = normalize_repo(str(origin))
    workspace.ensure_clone(ref)
    branch = "agent/status-view"
    worktree = workspace.worktree(ref, branch, base="origin/main")
    (worktree / "status.txt").write_text("done\n", encoding="utf-8")
    gitops.add_all(worktree)
    gitops.commit(worktree, "feat: add status view")

    history_dir = tmp_path / "history"
    store = history_dir / "status-project"
    store.mkdir(parents=True)
    _record(store / "implement-status-view-20260714T120000Z-123.jsonl", project=worktree)

    runs_dir = tmp_path / "runs"
    round_dir = runs_dir / "status-run" / "round-01"
    round_dir.mkdir(parents=True)
    (round_dir / "plan.json").write_text("{}\n", encoding="utf-8")
    result = {
        "ok": True,
        "started_order": ["status"],
        "results": {"status": {"status": "done", "branch": branch}},
    }
    (round_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")

    shown = _run(history_dir, workspace_root, runs_dir)
    assert shown.returncode == 0, shown.stderr
    for expected in (
        "implement-status-view",
        "codex/gpt-5",
        "Implemented the unified view.",
        "$ just check",
        "agent/status-view",
        "feat: add status view",
        "status-run round-01",
        "1 done",
        f"Execution checkout: {worktree}",
        "type=single-owner",
        "workflow=local",
        "Results: just results status-run; per-node detail is listed there",
    ):
        assert expected in shown.stdout

    encoded = _run(history_dir, workspace_root, runs_dir, "--format", "json")
    assert encoded.returncode == 0, encoded.stderr
    payload = json.loads(encoded.stdout)
    assert payload[0]["running"] is True
    assert payload[0]["commits"][0]["subject"] == "feat: add status view"
    assert payload[0]["ledger"]["run_id"] == "status-run"
    assert payload[0]["execution_checkout"] == str(worktree)
    assert payload[0]["repository_type"] == "single-owner"
    assert payload[0]["publication_workflow"] == "local"

    monkeypatch.setenv("ONEHARNESS_HISTORY_DIR", str(history_dir))
    assert status_main(["--runs-dir", str(runs_dir), "--format", "json"]) == 0
    direct = json.loads(capsys.readouterr().out)
    assert direct[0]["branch"] == branch
    assert status_main(["--runs-dir", str(runs_dir)]) == 0
    direct_human = capsys.readouterr().out
    assert "feat: add status view" in direct_human
    assert "status-run round-01" in direct_human
    workspace.remove_worktree(ref, worktree)


def test_status_recent_handles_gone_worktree_and_no_ledger(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    history_dir = tmp_path / "history"
    store = history_dir / "gone-project"
    store.mkdir(parents=True)
    (tmp_path / "gone").mkdir()
    _record(
        store / "implement-status-view-20260714T120000Z-123.jsonl",
        project=tmp_path / "gone",
    )
    result = _run(
        history_dir,
        tmp_path / ".ai-orchestrator" / "workspaces",
        tmp_path / "no-runs",
        "5",
    )
    assert result.returncode == 0, result.stderr
    assert "worktree/branch is gone" in result.stdout
    assert "Round: none" in result.stdout

    default = _run(
        history_dir,
        tmp_path / ".ai-orchestrator" / "workspaces",
        tmp_path / "no-runs",
    )
    assert "No running tasks" in default.stdout

    monkeypatch.setenv("ONEHARNESS_HISTORY_DIR", str(history_dir))
    assert status_main(["5", "--runs-dir", str(tmp_path / "no-runs")]) == 0
    assert "worktree/branch is gone" in capsys.readouterr().out
    assert status_main(["--runs-dir", str(tmp_path / "no-runs")]) == 0
    assert "No running tasks" in capsys.readouterr().out


def test_status_reports_a_run_whose_round_owner_is_gone(tmp_path, monkeypatch, capsys) -> None:
    """An abandoned round must be visible here, not only in `just runs`.

    The real journeys are in `tests/e2e/test_round_ownership_e2e.py`: a killed
    executor surfacing in both views
    (`test_killed_executor_surfaces_as_abandoned_in_runs_and_status`), and this
    combination — the abandonment reported beside the planner surface the dead round
    left queued (`test_status_reports_a_dead_round_beside_the_surface_it_left_pending`).
    This direct call exists so the in-process rendering counts toward the coverage
    gate, which a subprocess CLI invocation cannot contribute.
    """
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    runs_dir = tmp_path / "runs"
    round_dir = runs_dir / "abandoned-run" / "round-01"
    round_dir.mkdir(parents=True)
    (round_dir / "plan.json").write_text("{}\n", encoding="utf-8")
    (round_dir / "status.json").write_text(
        json.dumps(
            {"status": "running", "pid": os.getpid() + 10_000_000, "host": socket.gethostname()}
        ),
        encoding="utf-8",
    )

    # The reported failure's worst symptom: the surface a run last queued outlives the
    # round, so a run that died hours ago keeps reading as "waiting on me". The
    # abandonment is reported beside that stale surface, not instead of it.
    pending = runs_dir / "abandoned-run" / "channel" / "planner-pending.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(
        json.dumps({"kind": "blocker", "message": "round 1 dispatched", "blocking": True}),
        encoding="utf-8",
    )

    monkeypatch.setenv("ONEHARNESS_HISTORY_DIR", str(history_dir))
    assert status_main(["--runs-dir", str(runs_dir)]) == 0
    shown = capsys.readouterr().out
    assert "abandoned-run: round-01 ABANDONED" in shown
    assert "--recover" in shown
    assert "abandoned-run: waiting for planner decision: blocker: round 1 dispatched" in shown


def test_status_reports_a_queued_surface_no_planner_has_read(tmp_path, monkeypatch, capsys) -> None:
    """The same unread-surface fact `just runs` reports, in the other view.

    The real journey — a pacemaker queuing this through a live check-in agent, and
    the report clearing once consumed — is
    ``tests/e2e/test_channel_e2e.py::test_a_queued_update_nobody_read_is_reported_until_it_is_consumed``.
    This direct call exists so the rendering counts toward the coverage gate, which a
    subprocess CLI invocation cannot contribute.
    """
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    runs_dir = tmp_path / "runs"
    channel = runs_dir / "unattended" / "channel"
    channel.mkdir(parents=True)
    (channel / "heartbeat-surface.json").write_text(
        json.dumps(
            {
                "op": "supervisor",
                "run_id": "unattended",
                "round": 1,
                "surface": {
                    "kind": "heartbeat",
                    "message": "worker: still verifying",
                    "blocking": False,
                },
                "messages": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ONEHARNESS_HISTORY_DIR", str(history_dir))
    assert status_main(["--runs-dir", str(runs_dir)]) == 0
    shown = capsys.readouterr().out
    assert "unattended: 1 planner update waiting, unread for 0s" in shown
    assert f"just channel-next unattended --runs-dir {runs_dir}" in shown


def _settled_run(runs_dir: Path, run_id: str, outcomes: dict[str, str]) -> None:
    """Journal one in-flight round in which every named node has already settled.

    Written through the real `Journal`, and deliberately with no `result.json`: a
    round still in flight has recorded none, and that is exactly when this view is
    asked what is happening — so the journal is the only record of these outcomes.
    """
    journal = open_journal(runs_dir / run_id, RunId(run_id), 1)
    for node, status in outcomes.items():
        journal.append("node-started", node=NodeId(node))
        if status == "failed":
            journal.append("node-failed", node=NodeId(node), detail={"status": "failed"})
        else:
            journal.append("node-settled", node=NodeId(node), detail={"status": status})


def test_status_reports_every_settled_node_as_the_journal_recorded_it(
    tmp_path: Path, bare_origin
) -> None:
    """The journal outranks the worktree for every outcome, not only for a failure.

    `just status` recognises a running workstream by its still-present worktree, and
    every session here has one — a real checkout with its branch checked out, which
    is the state the filesystem calls "running". The run's own journal has already
    settled each of these nodes, so none of them may read as running, and each reads
    as what was recorded. A status outside the domain a node can settle in still
    settles the node, because the event kind proves that much on its own; and a
    dispatch whose labels name no node the journal recorded keeps the worktree's
    answer, because nothing overrides it.
    """
    origin = bare_origin()
    workspace_root = tmp_path / ".ai-orchestrator" / "workspaces"
    workspace = Workspace(workspace_root, resolver=lambda _: gitops.clone(origin, tmp_path / "c"))
    ref = normalize_repo(str(origin))
    workspace.ensure_clone(ref)

    runs_dir = tmp_path / "runs"
    outcomes = {"win": "done", "lose": "failed", "held": "waiting", "dropped": "cancelled"}
    _settled_run(runs_dir, "settled-run", {**outcomes, "odd": "in-orbit"})

    history_dir = tmp_path / "history"
    store = history_dir / "settled-project"
    store.mkdir(parents=True)
    labelled = {**outcomes, "odd": "done"}
    worktrees: list[Path] = []
    for index, node in enumerate([*labelled, "unjournalled"]):
        worktrees.append(workspace.worktree(ref, f"agent/{node}", base="origin/main"))
        _record(
            store / f"{node}-20260714T12000{index}Z-{index}.jsonl",
            project=worktrees[-1],
            name=node,
            labels=graph_labels(run_id=RunId("settled-run"), round_number=1, node=NodeId(node)),
        )
    # A dispatch whose round label is not a round the journal could have recorded
    # names no node, so it is never matched against one.
    worktrees.append(workspace.worktree(ref, "agent/mislabelled", base="origin/main"))
    _record(
        store / "mislabelled-20260714T120009Z-9.jsonl",
        project=worktrees[-1],
        name="mislabelled",
        labels={"run_id": "settled-run", "round": "not-a-round", "node": "win"},
    )

    try:
        shown = _run(history_dir, workspace_root, runs_dir, "settled-run", "--format", "json")
        assert shown.returncode == 0, shown.stderr
        reported = {task["task"]: task for task in json.loads(shown.stdout)}
        assert {node: reported[node]["node_state"] for node in labelled} == labelled
        assert not any(reported[node]["running"] for node in labelled)
        # Neither names a node the journal settled, so the worktree still answers.
        for unmatched in ("unjournalled", "mislabelled"):
            assert reported[unmatched]["node_state"] is None
            assert reported[unmatched]["running"] is True

        human = _run(history_dir, workspace_root, runs_dir, "settled-run")
        assert human.returncode == 0, human.stderr
        for node, status in labelled.items():
            assert f"  {node}  " in human.stdout
            assert f"({status};" in human.stdout
    finally:
        for worktree in worktrees:
            workspace.remove_worktree(ref, worktree)
