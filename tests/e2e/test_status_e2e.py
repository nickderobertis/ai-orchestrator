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
import uuid
from pathlib import Path

from orchestrator import REPO_ROOT, gitops
from orchestrator.registry import Registry
from orchestrator.status import main as status_main
from orchestrator.workspace import Workspace, normalize_repo

_UUID_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _record(path: Path, *, project: Path, status: str = "ok") -> None:
    """Write one session in oneharness' 1.0 event-sourced line format.

    A ``type: "event"`` tool-call line plus a ``type: "run"`` line linked by the
    run's ``history_id``; every 1.0 run status is terminal, so ``status`` picks one
    of ``ok``/``nonzero`` rather than a live state (the running/recent split is now
    driven by whether the branch is still a checked-out worktree).
    """
    run_id = str(uuid.uuid5(_UUID_NS, f"{path.stem}-status"))
    event = {
        "type": "event",
        "schema_version": "1.0",
        "run_id": run_id,
        "harness": "codex",
        "event": {
            "kind": "tool_call",
            "name": "command_execution",
            "input": {"command": "just check"},
            "output": "",
            "index": 0,
            "tool_call_id": f"{run_id}-c0",
            "started_at": "2026-07-14T12:00:00.100Z",
            "finished_at": "2026-07-14T12:00:00.200Z",
            "duration_ms": 100,
            "status": "completed",
        },
    }
    run = {
        "type": "run",
        "schema_version": "1.0",
        "history_id": run_id,
        "session": path.stem,
        "name": "implement-status-view",
        "labels": {},
        "project": str(project),
        "timestamp": "2026-07-14T12:00:00Z",
        "harness": "codex",
        "model": "gpt-5",
        "prompt": "Implement the unified status view.",
        "permission_mode": "bypass",
        "status": status,
        "exit_code": 0,
        "duration_ms": 2300,
        "started_at": "2026-07-14T12:00:00.000Z",
        "finished_at": "2026-07-14T12:00:02.300Z",
        "model_ms": 2000,
        "tool_ms": 100,
        "time_to_first_token_ms": 40,
        "text": "Implemented the unified view.",
        "text_source": "json:codex-agent-message",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "cache_read_tokens": 0,
            "cache_write_tokens": None,
            "cost_usd": None,
        },
        "session_id": "codex-status-thread",
        "failure_kind": None,
    }
    path.write_text(json.dumps(event) + "\n" + json.dumps(run) + "\n", encoding="utf-8")


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

    The real journey — a killed executor surfacing in both views — is
    `tests/e2e/test_round_ownership_e2e.py::test_killed_executor_surfaces_as_abandoned_in_runs_and_status`.
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

    monkeypatch.setenv("ONEHARNESS_HISTORY_DIR", str(history_dir))
    assert status_main(["--runs-dir", str(runs_dir)]) == 0
    shown = capsys.readouterr().out
    assert "abandoned-run: round-01 ABANDONED" in shown
    assert "--recover" in shown
