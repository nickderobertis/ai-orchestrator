"""Cross-process contention journeys over real git and persistent state."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from orchestrator import gitops
from orchestrator.config import ConfigError
from orchestrator.lifecycle import run_repo_task
from orchestrator.registry import Registry
from orchestrator.runs import prepare_round
from orchestrator.workspace import Workspace, normalize_repo

PLAN = {
    "name": "owned run",
    "tasks": [{"id": "change", "repo": "/unused", "persona": "backend", "task": "x"}],
}


def _worktree_process(
    canonical: str, root: str, branch: str, ready: multiprocessing.Queue[str]
) -> None:
    repo = normalize_repo(canonical)
    workspace = Workspace(root, resolver=lambda _spec: Path(canonical), workflow="local")
    worktree = workspace.worktree(repo, branch, base="origin/main")
    ready.put(str(worktree))
    workspace.remove_worktree(repo, worktree)


def _lifecycle_process(
    origin: str,
    canonical: str,
    root: str,
    base_config: str,
    persona_dir: str,
    results: multiprocessing.Queue[dict[str, object]],
) -> None:
    workspace = Workspace(root, resolver=lambda _spec: Path(canonical), workflow="local")
    result = run_repo_task(
        origin,
        "complete-now write-unique-change: the same lifecycle task",
        "backend-engineer",
        workspace=workspace,
        base_path=base_config,
        persona_dir=persona_dir,
        verify_cmd=["true"],
    )
    results.put(
        {
            "ok": result.ok,
            "outcome": result.outcome,
            "branch": result.branch,
            "detail": result.detail,
        }
    )


def _register_process(registry_path: str, checkout: str) -> None:
    Registry(registry_path).register(checkout, repo_type="single-owner")


def _own_round(run_dir: str, ready: Any, release: Any) -> None:
    prepare_round(Path(run_dir), PLAN)
    ready.set()
    release.wait(10)


def _join(process: multiprocessing.Process) -> None:
    process.join(15)
    assert not process.is_alive(), f"child {process.pid} did not finish"
    assert process.exitcode == 0


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def test_separate_processes_create_and_remove_distinct_worktrees(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    queue: multiprocessing.Queue[str] = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_worktree_process,
            args=(str(canonical), str(tmp_path / "worktrees"), f"feature/{index}", queue),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    paths = {queue.get(timeout=10) for _ in processes}
    for process in processes:
        _join(process)

    assert len(paths) == 2
    assert all(not Path(path).exists() for path in paths)
    assert gitops.worktrees(canonical) == {"main": canonical.resolve()}
    assert _git(canonical, "config", "--bool", "core.bare") == "false"


def test_identical_simultaneous_lifecycles_get_unique_branches_and_both_land(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    queue: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_lifecycle_process,
            args=(
                str(origin),
                str(canonical),
                str(tmp_path / "worktrees"),
                str(command_base()),
                str(personas_dir),
                queue,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=15) for _ in processes]
    for process in processes:
        _join(process)

    assert all(result["ok"] and result["outcome"] == "merged" for result in results), results
    assert len({result["branch"] for result in results}) == 2
    landed = _git(origin, "ls-tree", "--name-only", "main").splitlines()
    assert len([name for name in landed if name.startswith("CHANGE-")]) == 2
    assert _git(canonical, "config", "--bool", "core.bare") == "false"


def test_concurrent_registry_initialization_retains_both_entries(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    registry_path = tmp_path / "state" / "repos.json"
    checkouts = []
    for name in ("alpha", "beta"):
        checkout = gitops.clone(bare_origin(), tmp_path / name)
        checkouts.append(checkout)
    processes = [
        multiprocessing.Process(target=_register_process, args=(str(registry_path), str(checkout)))
        for checkout in checkouts
    ]
    for process in processes:
        process.start()
    for process in processes:
        _join(process)

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert payload["version"] == 3
    assert set(payload["checkouts"]) == {"local/alpha", "local/beta"}
    assert len(Registry(registry_path).entries) == 2


def test_explicit_run_owner_blocks_contention_and_only_dead_owner_can_be_recovered(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "explicit"
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    owner = multiprocessing.Process(target=_own_round, args=(str(run_dir), ready, release))
    owner.start()
    assert ready.wait(5), "owner never claimed the explicit run"

    with pytest.raises(ConfigError, match="already running"):
        prepare_round(run_dir, PLAN)
    with pytest.raises(ConfigError, match="owner is still alive; recovery refused"):
        prepare_round(run_dir, PLAN, recover=True)

    release.set()
    _join(owner)
    number, claimed = prepare_round(run_dir, PLAN, recover=True)
    assert number == 1
    status = json.loads((claimed / "status.json").read_text(encoding="utf-8"))
    assert status["pid"] == os.getpid()
