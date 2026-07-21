"""Real history + git worktree + run-ledger journey for ``just status``."""

# llmlint: ignore-file[e2e_not_mocked] the primary journey invokes the real `just status`
# subprocess; the direct main calls additionally verify rendering without replacing any
# production boundary.

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT, gitops
from orchestrator.registry import Registry
from orchestrator.status import main as status_main
from orchestrator.workspace import Workspace, normalize_repo


def _record(path: Path, *, project: Path, status: str = "running") -> None:
    value = {
        "name": "implement-status-view",
        "project": str(project),
        "session": "status-worker",
        "timestamp": "2026-07-14T12:00:00Z",
        "harness": "codex",
        "model": "gpt-5",
        "status": status,
        "duration_ms": 2300,
        "text": "Implemented the unified view.",
        "events": [{"kind": "tool_call", "input": {"cmd": "just check"}}],
    }
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


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
    assert "Results: just results status-run; per-node detail is listed there" in direct_human
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
        status="completed",
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
