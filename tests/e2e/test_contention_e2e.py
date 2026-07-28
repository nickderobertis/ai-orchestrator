"""Cross-process contention journeys over real git and persistent state."""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

import pytest
from waits import timeout as e2e_timeout

from orchestrator import gitops
from orchestrator.config import ConfigError
from orchestrator.coordination import (
    LOCK_TIMEOUT_ENV,
    advisory_lock,
    git_lock_identity,
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


def _open_workspace(canonical: str, root: str, token: str | None = None) -> Workspace:
    """Resolve one run's workspace exactly as a dispatch does, in this process."""
    workspace = Workspace(
        root, resolver=lambda _spec: Path(canonical), workflow="local", run_token=token
    )
    workspace.ensure_clone(normalize_repo(canonical))
    return workspace


def _worktree_process(
    canonical: str, root: str, branch: str, ready: multiprocessing.Queue[str]
) -> None:
    repo = normalize_repo(canonical)
    workspace = _open_workspace(canonical, root)
    worktree = workspace.worktree(repo, branch, base="origin/main")
    ready.put(str(worktree))
    workspace.remove_worktree(repo, worktree)


def _held_worktree_process(
    canonical: str,
    root: str,
    branch: str,
    state_root: str,
    token: str,
    ready: multiprocessing.Queue[str],
    release: Any,
) -> None:
    # The persistent multiprocessing forkserver retains the environment from the
    # first test that starts it. Pass this per-test fixture boundary explicitly so
    # owner and contender open the same process-shared lock file.
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    repo = normalize_repo(canonical)
    # llmlint: ignore[tests_mirror_real_usage] This lease-level e2e must hold the
    # worktree between acquisition and teardown, a pause no public CLI exposes.
    workspace = _open_workspace(canonical, root, token)
    worktree = workspace.worktree(repo, branch, base="origin/main")
    ready.put(str(worktree))
    release.wait(e2e_timeout(10))
    workspace.remove_worktree(repo, worktree)


def _paused_teardown_process(
    canonical: str,
    root: str,
    branch: str,
    token: str,
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
        root, resolver=lambda _spec: Path(canonical), workflow="local", run_token=token
    )
    workspace.ensure_clone(repo)
    worktree = workspace.worktree(repo, branch, base="origin/main")
    ready.put(str(worktree))
    begin.wait(e2e_timeout(10))
    workspace.remove_worktree(repo, worktree)


def _orphan_worktree_process(
    canonical: str, root: str, branch: str, token: str, ready: multiprocessing.Queue[str]
) -> None:
    repo = normalize_repo(canonical)
    workspace = _open_workspace(canonical, root, token)
    ready.put(str(workspace.worktree(repo, branch, base="origin/main")))


def _committing_run_process(
    canonical: str, root: str, branch: str, publish: bool, ready: multiprocessing.Queue[str]
) -> None:
    """Leave one abandoned run root behind, with work either pushed or not."""
    repo = normalize_repo(canonical)
    workspace = _open_workspace(canonical, root)
    worktree = workspace.worktree(repo, branch, base="origin/main")
    (worktree / "work.txt").write_text(f"work only {branch} has\n", encoding="utf-8")
    gitops.add_all(worktree)
    gitops.commit(worktree, f"feat: work on {branch}")
    if publish:
        gitops.push(worktree, branch)
    ready.put(str(workspace.run_root(repo)))


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
    release.wait(e2e_timeout(10))


def _join(process: multiprocessing.Process) -> None:
    process.join(15)
    assert not process.is_alive(), f"child {process.pid} did not finish"
    assert process.exitcode == 0


def _has_file(repo: Path, ref: str, path: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{path}"], capture_output=True
        ).returncode
        == 0
    )


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
    paths = {queue.get(timeout=e2e_timeout(10)) for _ in processes}
    for process in processes:
        _join(process)

    assert len(paths) == 2
    assert all(not Path(path).exists() for path in paths)
    # Each run cut its worktree from its own clone, so the shared checkout never
    # carried a registration either of them could have pruned.
    assert gitops.worktrees(canonical) == {"main": canonical.resolve()}
    assert len({Path(path).parent for path in paths}) == 2
    assert _git(canonical, "config", "--bool", "core.bare") == "false"


def test_active_worktree_of_a_rejoined_run_is_never_reclaimed(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """Two processes on one run share its clone, so its live worktree stays its own."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-active")
    root = tmp_path / "worktrees-active"
    token = "rejoined-run"
    # Python 3.14's shared forkserver captures the prior test's isolated
    # AI_ORCHESTRATOR_HOME. Spawn proves both contenders use this test's lock root.
    ready: multiprocessing.Queue[str] = MP.Queue()
    release = MP.Event()
    process = MP.Process(
        target=_held_worktree_process,
        args=(
            str(canonical),
            str(root),
            "feature/held",
            os.environ["AI_ORCHESTRATOR_HOME"],
            token,
            ready,
            release,
        ),
    )
    process.start()
    held_path = Path(ready.get(timeout=e2e_timeout(10)))
    repo = normalize_repo(str(canonical))
    contender = _open_workspace(str(canonical), str(root), token)
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
        assert gitops.worktrees(contender.clone_dir(repo))["feature/held"] == held_path
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


def test_sibling_run_can_neither_remove_a_live_worktree_nor_delete_its_branch(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """The failure that cost real work: a sibling reaching into a running dispatch."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-sibling")
    root = tmp_path / "worktrees-sibling"
    ready: multiprocessing.Queue[str] = MP.Queue()
    release = MP.Event()
    process = MP.Process(
        target=_held_worktree_process,
        args=(
            str(canonical),
            str(root),
            "feature/live",
            os.environ["AI_ORCHESTRATOR_HOME"],
            "owning-run",
            ready,
            release,
        ),
    )
    process.start()
    live = Path(ready.get(timeout=e2e_timeout(10)))
    repo = normalize_repo(str(canonical))
    sibling = _open_workspace(str(canonical), str(root))
    try:
        # The sibling's own clone is the only worktree registry and ref store it can
        # reach, and neither knows anything about the live run's tree or branch.
        with pytest.raises(gitops.GitError):
            sibling.remove_worktree(repo, live)
        with pytest.raises(gitops.GitError):
            sibling.delete_branch(repo, "feature/live")
        sibling.ensure_clone(repo)

        assert live.exists() and (live / ".git").exists()
        assert "feature/live" not in gitops.worktrees(sibling.clone_dir(repo))
        assert sibling.clone_dir(repo) != _open_workspace(
            str(canonical), str(root), "owning-run"
        ).clone_dir(repo)
    finally:
        release.set()
        _join(process)

    assert not live.exists()


def test_concurrent_runs_hold_the_same_branch_name_without_colliding(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """One identity, two live runs, one branch name — and no shared registry to race."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-parallel")
    root = tmp_path / "worktrees-parallel"
    releases = [MP.Event(), MP.Event()]
    readies: list[multiprocessing.Queue[str]] = [MP.Queue(), MP.Queue()]
    processes = [
        MP.Process(
            target=_held_worktree_process,
            args=(
                str(canonical),
                str(root),
                "feature/shared-name",
                os.environ["AI_ORCHESTRATOR_HOME"],
                f"parallel-{index}",
                readies[index],
                releases[index],
            ),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    paths = [Path(queue.get(timeout=e2e_timeout(10))) for queue in readies]
    try:
        assert len({str(path) for path in paths}) == 2
        assert all(path.exists() for path in paths)
        # The shared checkout is only an object store here: it never registers a
        # worktree, so no run's cleanup can prune another run's tree out of it.
        assert gitops.worktrees(canonical) == {"main": canonical.resolve()}
    finally:
        for release in releases:
            release.set()
        for process in processes:
            _join(process)


def test_same_branch_redispatch_cannot_overtake_paused_teardown(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-teardown-race")
    root = tmp_path / "worktrees-teardown-race"
    ready: multiprocessing.Queue[str] = MP.Queue()
    begin = MP.Event()
    teardown_started = MP.Event()
    token = "teardown-race-run"
    process = MP.Process(
        target=_paused_teardown_process,
        args=(
            str(canonical),
            str(root),
            "feature/race",
            token,
            ready,
            begin,
            teardown_started,
        ),
    )
    process.start()
    original = Path(ready.get(timeout=e2e_timeout(10)))
    repo = normalize_repo(str(canonical))
    contender = _open_workspace(str(canonical), str(root), token)
    clone = contender.clone_dir(repo)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with advisory_lock(git_lock_identity(gitops.common_dir(clone))):
            begin.set()
            assert teardown_started.wait(e2e_timeout(10))
            redispatch = pool.submit(contender.worktree, repo, "feature/race", base="origin/main")
            with pytest.raises(FutureTimeout):
                redispatch.result(timeout=0.1)
        try:
            replacement = redispatch.result(timeout=e2e_timeout(10))
        except RuntimeError as exc:
            assert "branch 'feature/race' is active" in str(exc)
            replacement = None
    finally:
        pool.shutdown(wait=True)
        _join(process)

    if replacement is None:
        replacement = contender.worktree(repo, "feature/race", base="origin/main")
    assert replacement.exists()
    assert gitops.worktrees(clone)["feature/race"] == replacement
    assert replacement == original
    contender.remove_worktree(repo, replacement)


def test_failed_abandoned_reclaim_releases_lease_for_retry(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-locked-orphan")
    root = tmp_path / "worktrees-locked-orphan"
    ready: multiprocessing.Queue[str] = MP.Queue()
    token = "locked-orphan-run"
    process = MP.Process(
        target=_orphan_worktree_process,
        args=(str(canonical), str(root), "feature/orphan", token, ready),
    )
    process.start()
    orphan = Path(ready.get(timeout=e2e_timeout(10)))
    _join(process)
    repo = normalize_repo(str(canonical))
    workspace = _open_workspace(str(canonical), str(root), token)
    clone = workspace.clone_dir(repo)
    subprocess.run(["git", "-C", str(clone), "worktree", "lock", str(orphan)], check=True)

    with pytest.raises(gitops.GitError, match="locked working tree"):
        workspace.worktree(repo, "feature/orphan", base="origin/main")

    subprocess.run(["git", "-C", str(clone), "worktree", "unlock", str(orphan)], check=True)
    replacement = workspace.worktree(repo, "feature/orphan", base="origin/main")
    assert replacement.exists()
    workspace.remove_worktree(repo, replacement)


def test_abandoned_branch_at_different_path_moves_to_new_owned_worktree(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """An earlier attempt in this run parked the branch somewhere else and died."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-moved-orphan")
    repo = normalize_repo(str(canonical))
    workspace = _open_workspace(str(canonical), str(tmp_path / "moved-root"))
    clone = workspace.clone_dir(repo)
    old_path = workspace.run_root(repo) / "earlier-attempt"
    gitops.worktree_add(clone, old_path, "feature/moved", base="origin/main")

    replacement = workspace.worktree(repo, "feature/moved", base="origin/main")

    assert replacement != old_path
    assert not old_path.exists()
    assert replacement.exists()
    assert gitops.worktrees(clone)["feature/moved"] == replacement
    workspace.remove_worktree(repo, replacement)


def test_missing_abandoned_worktree_registration_is_pruned_and_recreated(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-missing-orphan")
    root = tmp_path / "worktrees-missing-orphan"
    ready: multiprocessing.Queue[str] = MP.Queue()
    token = "missing-orphan-run"
    process = MP.Process(
        target=_orphan_worktree_process,
        args=(str(canonical), str(root), "feature/missing", token, ready),
    )
    process.start()
    missing = Path(ready.get(timeout=e2e_timeout(10)))
    _join(process)
    shutil.rmtree(missing)

    repo = normalize_repo(str(canonical))
    workspace = _open_workspace(str(canonical), str(root), token)
    replacement = workspace.worktree(repo, "feature/missing", base="origin/main")

    assert replacement == missing
    assert replacement.exists()
    assert gitops.worktrees(workspace.clone_dir(repo))["feature/missing"] == replacement
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
    results = [queue.get(timeout=e2e_timeout(15)) for _ in processes]
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
    assert ready.wait(e2e_timeout(5)), "owner never claimed the explicit run"

    with pytest.raises(ConfigError, match="already running"):
        prepare_round(run_dir, PLAN)
    with pytest.raises(ConfigError, match="owner may still be alive; recovery refused"):
        prepare_round(run_dir, PLAN, recover=True)

    release.set()
    _join(owner)
    number, claimed = prepare_round(run_dir, PLAN, recover=True)
    assert number == 1
    status = json.loads((claimed / "status.json").read_text(encoding="utf-8"))
    assert status["pid"] == os.getpid()


def _queued_lock_process(
    state_root: str, identity: str, hold: float, done: multiprocessing.Queue[float]
) -> None:
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    started = time.monotonic()
    with advisory_lock(identity):
        waited = time.monotonic() - started
        time.sleep(hold)
    done.put(waited)


def _dying_lock_holder(state_root: str, identity: str, holding: Any) -> None:
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    lock = advisory_lock(identity)
    lock.__enter__()
    holding.set()
    time.sleep(e2e_timeout(60))


def _fetching_run_process(
    canonical: str, root: str, state_root: str, fetched: Any, release: Any
) -> None:
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root

    def observe(kind: str, detail: Mapping[str, str | float | bool]) -> None:
        if kind == "setup-finished" and detail.get("operation") == "fetch":
            fetched.set()
            release.wait(e2e_timeout(20))

    set_harness_observer(observe)
    _open_workspace(canonical, root)


def test_every_contender_is_queued_and_served_instead_of_timing_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four processes want one identity; the shortest turn must not fail the rest."""
    monkeypatch.setenv(LOCK_TIMEOUT_ENV, str(e2e_timeout(60)))
    identity = "git:/queued/repository"
    done: multiprocessing.Queue[float] = MP.Queue()
    processes = [
        MP.Process(
            target=_queued_lock_process,
            args=(os.environ["AI_ORCHESTRATOR_HOME"], identity, 0.3, done),
        )
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    waits = sorted(done.get(timeout=e2e_timeout(30)) for _ in processes)
    for process in processes:
        _join(process)

    # Serialization is the point, so the last contender must genuinely have waited
    # out the ones before it rather than been told the resource was unavailable.
    assert len(waits) == 4
    assert waits[-1] >= 0.6


def test_killed_lock_holder_hands_the_queue_to_the_next_waiter(tmp_path: Path) -> None:
    """`flock` releases on death, so a crashed holder cannot wedge the queue."""
    identity = "git:/crashed/repository"
    holding = MP.Event()
    holder = MP.Process(
        target=_dying_lock_holder,
        args=(os.environ["AI_ORCHESTRATOR_HOME"], identity, holding),
    )
    holder.start()
    assert holding.wait(e2e_timeout(10)), "holder never took the lock"

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        waiter = pool.submit(_hold_briefly, identity)
        with pytest.raises(FutureTimeout):
            waiter.result(timeout=0.2)
        holder.kill()
        holder.join(e2e_timeout(10))
        assert waiter.result(timeout=e2e_timeout(20)) is True
    finally:
        pool.shutdown(wait=True)


def _hold_briefly(identity: str) -> bool:
    with advisory_lock(identity):
        return True


def test_execution_checkout_fetch_never_runs_inside_the_exclusive_section(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """A run's fetch must not be blocked by a sibling holding the shared checkout."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-fetch")
    root = tmp_path / "worktrees-fetch"
    fetched = MP.Event()
    release = MP.Event()
    process = MP.Process(
        target=_fetching_run_process,
        args=(
            str(canonical),
            str(root),
            os.environ["AI_ORCHESTRATOR_HOME"],
            fetched,
            release,
        ),
    )
    identity = git_lock_identity(gitops.common_dir(canonical))
    try:
        with advisory_lock(identity):
            process.start()
            assert fetched.wait(e2e_timeout(15)), (
                "the run's fetch never completed while the shared checkout was held"
            )
    finally:
        release.set()
        _join(process)


def test_abandoned_run_is_reclaimed_only_once_all_its_work_reached_origin(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """Reclaiming disk must never be able to discard a dead run's unpushed commits."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-reap")
    root = tmp_path / "worktrees-reap"
    roots: dict[bool, Path] = {}
    for publish in (True, False):
        ready: multiprocessing.Queue[str] = MP.Queue()
        process = MP.Process(
            target=_committing_run_process,
            args=(str(canonical), str(root), f"feature/reap-{publish}", publish, ready),
        )
        process.start()
        roots[publish] = Path(ready.get(timeout=e2e_timeout(10)))
        _join(process)
    assert roots[True] != roots[False]

    # A later run on this identity is the only thing that sweeps; it must keep the
    # one holding work that exists nowhere else.
    later = _open_workspace(str(canonical), str(root))

    repo = normalize_repo(str(canonical))
    assert not roots[True].exists()
    assert roots[False].exists()
    assert later.run_root(repo).exists()

    # Rejoining the abandoned run is how that work gets out: its clone still has
    # the branch, and tearing the tree down hands it to the shared checkout.
    reclaim = _open_workspace(str(canonical), str(root), roots[False].name)
    retained = gitops.worktrees(reclaim.clone_dir(repo))["feature/reap-False"]
    reclaim.remove_worktree(repo, retained)

    assert gitops.branch_exists(canonical, "feature/reap-False")
    assert not _has_file(origin, "main", "work.txt")


def _rejoining_run_process(
    canonical: str, root: str, token: str, state_root: str, joined: Any, release: Any
) -> None:
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    _open_workspace(canonical, root, token)
    joined.set()
    release.wait(e2e_timeout(20))


def test_a_run_rejoined_after_its_first_process_died_is_still_never_reaped(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """The second process in a run must be as protected from the sweep as the first."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-rejoin")
    root = tmp_path / "worktrees-rejoin"
    token = "rejoined-after-death"
    ready: multiprocessing.Queue[str] = MP.Queue()
    first = MP.Process(
        target=_orphan_worktree_process,
        args=(str(canonical), str(root), "feature/rejoin", token, ready),
    )
    first.start()
    worktree = Path(ready.get(timeout=e2e_timeout(10)))
    _join(first)

    joined, release = MP.Event(), MP.Event()
    rejoiner = MP.Process(
        target=_rejoining_run_process,
        args=(
            str(canonical),
            str(root),
            token,
            os.environ["AI_ORCHESTRATOR_HOME"],
            joined,
            release,
        ),
    )
    rejoiner.start()
    try:
        assert joined.wait(e2e_timeout(15)), "the rejoining run never opened its workspace"
        # The creating process is gone and its work is unpublished, but a live
        # occupant is reason enough on its own to leave the tree alone.
        sweeper = _open_workspace(str(canonical), str(root))
        repo = normalize_repo(str(canonical))

        assert worktree.exists()
        assert (Path(root) / repo.dir_key / "runs" / token / ".clone").is_dir()
        assert sweeper.run_root(repo).exists()
    finally:
        release.set()
        _join(rejoiner)

    rejoin = _open_workspace(str(canonical), str(root), token)
    rejoin.remove_worktree(normalize_repo(str(canonical)), worktree)


def test_an_unusable_lock_timeout_stops_a_dispatch_by_name(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misconfigured watchdog must name its variable, not fail somewhere obscure."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-bad-timeout")
    monkeypatch.setenv(LOCK_TIMEOUT_ENV, "forever")

    with pytest.raises(ValueError, match=LOCK_TIMEOUT_ENV):
        run_repo_task(
            str(origin),
            "never dispatched",
            "engineer",
            workspace=Workspace(
                tmp_path / "worktrees-bad-timeout",
                resolver=lambda _spec: canonical,
                workflow="local",
            ),
            verify_cmd=["true"],
        )
