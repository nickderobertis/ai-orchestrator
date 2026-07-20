"""Cross-process contention journeys over real git and persistent state."""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

import pytest

from orchestrator import gitops
from orchestrator.config import ConfigError
from orchestrator.coordination import (
    advisory_lock,
    reset_harness_observer,
    set_harness_observer,
)
from orchestrator.journal import NodeJournal, open_journal
from orchestrator.lifecycle import run_repo_task
from orchestrator.registry import Registry
from orchestrator.runs import NodeId, RunId, prepare_round
from orchestrator.workspace import Workspace, normalize_repo

PLAN = {
    "name": "owned run",
    "tasks": [{"id": "change", "repo": "/unused", "persona": "backend", "task": "x"}],
}
MP = multiprocessing.get_context("spawn")


def _worktree_process(
    canonical: str, root: str, branch: str, ready: multiprocessing.Queue[str]
) -> None:
    repo = normalize_repo(canonical)
    workspace = Workspace(root, resolver=lambda _spec: Path(canonical), workflow="local")
    worktree = workspace.worktree(repo, branch, base="origin/main")
    ready.put(str(worktree))
    workspace.remove_worktree(repo, worktree)


def _held_worktree_process(
    canonical: str,
    root: str,
    branch: str,
    ready: multiprocessing.Queue[str],
    release: Any,
) -> None:
    repo = normalize_repo(canonical)
    workspace = Workspace(root, resolver=lambda _spec: Path(canonical), workflow="local")
    worktree = workspace.worktree(repo, branch, base="origin/main")
    ready.put(str(worktree))
    release.wait(10)
    workspace.remove_worktree(repo, worktree)


def _paused_teardown_process(
    canonical: str,
    root: str,
    branch: str,
    ready: multiprocessing.Queue[str],
    begin: Any,
    teardown_started: Any,
) -> None:
    class PausedTeardownWorkspace(Workspace):
        def remove_worktree(self, repo, path) -> None:
            teardown_started.set()
            super().remove_worktree(repo, path)

    repo = normalize_repo(canonical)
    workspace = PausedTeardownWorkspace(
        root, resolver=lambda _spec: Path(canonical), workflow="local"
    )
    worktree = workspace.worktree(repo, branch, base="origin/main")
    ready.put(str(worktree))
    begin.wait(10)
    workspace.remove_worktree(repo, worktree)


def _orphan_worktree_process(
    canonical: str, root: str, branch: str, ready: multiprocessing.Queue[str]
) -> None:
    repo = normalize_repo(canonical)
    workspace = Workspace(root, resolver=lambda _spec: Path(canonical), workflow="local")
    ready.put(str(workspace.worktree(repo, branch, base="origin/main")))


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
        "engineer",
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
    queue: multiprocessing.Queue[str] = MP.Queue()
    processes = [
        MP.Process(
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


def test_active_cross_process_worktree_is_never_reclaimed(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-active")
    root = tmp_path / "worktrees-active"
    # Python 3.14's shared forkserver captures the prior test's isolated
    # AI_ORCHESTRATOR_HOME. Spawn proves both contenders use this test's lock root.
    ready: multiprocessing.Queue[str] = MP.Queue()
    release = MP.Event()
    process = MP.Process(
        target=_held_worktree_process,
        args=(str(canonical), str(root), "feature/held", ready, release),
    )
    process.start()
    held_path = Path(ready.get(timeout=10))
    repo = normalize_repo(str(canonical))
    contender = Workspace(root, resolver=lambda _spec: canonical, workflow="local")
    run_id = RunId("worktree-lock-timeout")
    run_dir = tmp_path / "runs" / run_id
    prepare_round(run_dir, PLAN)
    journal = open_journal(run_dir, run_id, 1)
    node_journal = NodeJournal(journal, NodeId("change"), run_id, 1)
    observer = set_harness_observer(lambda kind, detail: node_journal.append(kind, detail=detail))
    try:
        with pytest.raises(RuntimeError, match="branch 'feature/held' is active"):
            contender.worktree(repo, "feature/held", base="origin/main")
        assert held_path.exists()
        assert gitops.worktrees(canonical)["feature/held"] == held_path
    finally:
        reset_harness_observer(observer)
        release.set()
        _join(process)

    timed_out = [
        event
        for event in journal.events()
        if event.kind == "lock-wait" and event.detail.get("acquired") is False
    ]
    assert len(timed_out) == 1
    assert str(timed_out[0].detail["identity"]).startswith("worktree:")
    assert float(timed_out[0].detail["seconds"]) >= 0


def test_same_branch_redispatch_cannot_overtake_paused_teardown(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-teardown-race")
    root = tmp_path / "worktrees-teardown-race"
    ready: multiprocessing.Queue[str] = MP.Queue()
    begin = MP.Event()
    teardown_started = MP.Event()
    process = MP.Process(
        target=_paused_teardown_process,
        args=(
            str(canonical),
            str(root),
            "feature/race",
            ready,
            begin,
            teardown_started,
        ),
    )
    process.start()
    original = Path(ready.get(timeout=10))
    repo = normalize_repo(str(canonical))
    contender = Workspace(root, resolver=lambda _spec: canonical, workflow="local")
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with advisory_lock(f"git:{gitops.common_dir(canonical)}"):
            begin.set()
            assert teardown_started.wait(10)
            redispatch = pool.submit(contender.worktree, repo, "feature/race", base="origin/main")
            with pytest.raises(FutureTimeout):
                redispatch.result(timeout=0.1)
        try:
            replacement = redispatch.result(timeout=10)
        except RuntimeError as exc:
            assert "branch 'feature/race' is active" in str(exc)
            replacement = None
    finally:
        pool.shutdown(wait=True)
        _join(process)

    if replacement is None:
        replacement = contender.worktree(repo, "feature/race", base="origin/main")
    assert replacement.exists()
    assert gitops.worktrees(canonical)["feature/race"] == replacement
    assert replacement == original
    contender.remove_worktree(repo, replacement)


def test_failed_abandoned_reclaim_releases_lease_for_retry(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-locked-orphan")
    root = tmp_path / "worktrees-locked-orphan"
    ready: multiprocessing.Queue[str] = MP.Queue()
    process = MP.Process(
        target=_orphan_worktree_process,
        args=(str(canonical), str(root), "feature/orphan", ready),
    )
    process.start()
    orphan = Path(ready.get(timeout=10))
    _join(process)
    subprocess.run(["git", "-C", str(canonical), "worktree", "lock", str(orphan)], check=True)

    repo = normalize_repo(str(canonical))
    workspace = Workspace(root, resolver=lambda _spec: canonical, workflow="local")
    with pytest.raises(gitops.GitError, match="locked working tree"):
        workspace.worktree(repo, "feature/orphan", base="origin/main")

    subprocess.run(["git", "-C", str(canonical), "worktree", "unlock", str(orphan)], check=True)
    replacement = workspace.worktree(repo, "feature/orphan", base="origin/main")
    assert replacement.exists()
    workspace.remove_worktree(repo, replacement)


def test_abandoned_branch_at_different_path_moves_to_new_owned_worktree(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-moved-orphan")
    ready: multiprocessing.Queue[str] = MP.Queue()
    process = MP.Process(
        target=_orphan_worktree_process,
        args=(str(canonical), str(tmp_path / "old-root"), "feature/moved", ready),
    )
    process.start()
    old_path = Path(ready.get(timeout=10))
    _join(process)

    repo = normalize_repo(str(canonical))
    workspace = Workspace(tmp_path / "new-root", resolver=lambda _spec: canonical, workflow="local")
    replacement = workspace.worktree(repo, "feature/moved", base="origin/main")

    assert replacement != old_path
    assert not old_path.exists()
    assert replacement.exists()
    assert gitops.worktrees(canonical)["feature/moved"] == replacement
    workspace.remove_worktree(repo, replacement)


def test_missing_abandoned_worktree_registration_is_pruned_and_recreated(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-missing-orphan")
    root = tmp_path / "worktrees-missing-orphan"
    ready: multiprocessing.Queue[str] = MP.Queue()
    process = MP.Process(
        target=_orphan_worktree_process,
        args=(str(canonical), str(root), "feature/missing", ready),
    )
    process.start()
    missing = Path(ready.get(timeout=10))
    _join(process)
    shutil.rmtree(missing)

    repo = normalize_repo(str(canonical))
    workspace = Workspace(root, resolver=lambda _spec: canonical, workflow="local")
    replacement = workspace.worktree(repo, "feature/missing", base="origin/main")

    assert replacement == missing
    assert replacement.exists()
    assert gitops.worktrees(canonical)["feature/missing"] == replacement
    workspace.remove_worktree(repo, replacement)


def test_identical_simultaneous_lifecycles_get_unique_branches_and_both_land(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    queue: multiprocessing.Queue[dict[str, object]] = MP.Queue()
    processes = [
        MP.Process(
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
        MP.Process(target=_register_process, args=(str(registry_path), str(checkout)))
        for checkout in checkouts
    ]
    for process in processes:
        process.start()
    for process in processes:
        _join(process)

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert payload["version"] == 4
    assert set(payload["checkouts"]) == {"local/alpha", "local/beta"}
    assert len(Registry(registry_path).entries) == 2


def test_explicit_run_owner_blocks_contention_and_only_dead_owner_can_be_recovered(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "explicit"
    ready = MP.Event()
    release = MP.Event()
    owner = MP.Process(target=_own_round, args=(str(run_dir), ready, release))
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
