"""Cross-process contention journeys over real git and persistent state.

llmlint: ignore-file[tests_mirror_real_usage] A crashed run and a sibling's
destructive reach have no public entry point that produces them on demand.
llmlint: ignore-file[e2e_not_mocked] Real onejudge drives the dispatch journeys;
only its paid provider is replaced by the repository's command backend.
"""

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
from multiprocessing.synchronize import Event as MPEvent
from pathlib import Path

import pytest
from conftest import install_pre_push_hook
from rendezvous import Rendezvous
from waits import deadline as e2e_deadline
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
from orchestrator.provenance import incomplete_commits
from orchestrator.recover import recover_repo
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
    release: MPEvent,
) -> None:
    # The persistent multiprocessing forkserver retains the environment from the
    # first test that starts it. Pass this per-test fixture boundary explicitly so
    # owner and contender open the same process-shared lock file.
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    repo = normalize_repo(canonical)
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
    begin: MPEvent,
    teardown_started: MPEvent,
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


def _dirty_orphan_worktree_process(
    repo_spec: str,
    canonical: str,
    root: str,
    branch: str,
    ready: multiprocessing.Queue[str],
) -> None:
    repo = normalize_repo(repo_spec)
    workspace = Workspace(root, resolver=lambda _spec: Path(canonical), workflow="local")
    workspace.ensure_clone(repo)
    worktree = workspace.worktree(repo, branch, base="origin/main")
    (worktree / "interrupted.txt").write_text("survived the killed dispatch\n", encoding="utf-8")
    ready.put(str(worktree))


def _held_identity_worktree_process(
    repo_spec: str,
    canonical: str,
    root: str,
    branch: str,
    state_root: str,
    token: str,
    ready: multiprocessing.Queue[str],
    release: MPEvent,
) -> None:
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    repo = normalize_repo(repo_spec)
    workspace = Workspace(
        root, resolver=lambda _spec: Path(canonical), workflow="local", run_token=token
    )
    workspace.ensure_clone(repo)
    worktree = workspace.worktree(repo, branch, base="origin/main")
    (worktree / "live-incomplete.txt").write_text("still being edited\n", encoding="utf-8")
    ready.put(str(worktree))
    release.wait(e2e_timeout(30))
    workspace.remove_worktree(repo, worktree)


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
        recorded_gate=["true"],
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


def _own_round(run_dir: str, ready: MPEvent, release: MPEvent) -> None:
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


def test_dead_dispatch_worktree_is_adopted_at_the_same_path(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real dispatch path takes ownership of a killed worker's exact tree."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-adopt-dead")
    root = tmp_path / "worktrees-adopt-dead"
    branch = "feature/adopt-dead"
    alternate_configs = (tmp_path / "alternate", tmp_path / "alternate2")
    for config_dir in alternate_configs:
        config_dir.mkdir()
        (config_dir / ".claude.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR", str(alternate_configs[0]))
    monkeypatch.setenv("ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR", str(alternate_configs[1]))
    ready: multiprocessing.Queue[str] = MP.Queue()
    process = MP.Process(
        target=_dirty_orphan_worktree_process,
        args=(str(origin), str(canonical), str(root), branch, ready),
    )
    process.start()
    original = Path(ready.get(timeout=e2e_timeout(10)))
    _join(process)
    # Remove the creator's entries so only the retry's adoption path can restore
    # trust for the preserved clone and worktree.
    for config_dir in alternate_configs:
        (config_dir / ".claude.json").write_text("{}", encoding="utf-8")

    observed = tmp_path / "retry-cwd"
    result = run_repo_task(
        str(origin),
        f"complete-now continue interrupted work\nrecord-cwd={observed}",
        "engineer",
        workspace=Workspace(root, resolver=lambda _spec: canonical, workflow="local"),
        branch=branch,
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )

    assert Path(observed.read_text(encoding="utf-8")) == original
    assert result.ok and result.outcome == "merged", result.detail
    clone = original.parent / ".clone"
    for config_dir in alternate_configs:
        projects = json.loads((config_dir / ".claude.json").read_text(encoding="utf-8"))["projects"]
        assert projects[str(clone.resolve())] == {"hasTrustDialogAccepted": True}
        assert projects[str(original.resolve())] == {"hasTrustDialogAccepted": True}
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "show", "main:interrupted.txt"], capture_output=True
        ).returncode
        == 0
    )


def test_live_dispatch_worktree_is_not_adopted_and_retry_uses_fresh_path(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
) -> None:
    """The real dispatch path falls back while the earlier owner remains live."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-adopt-live")
    root = tmp_path / "worktrees-adopt-live"
    branch = "feature/adopt-live"
    ready: multiprocessing.Queue[str] = MP.Queue()
    release = MP.Event()
    owner = MP.Process(
        target=_held_identity_worktree_process,
        args=(
            str(origin),
            str(canonical),
            str(root),
            branch,
            os.environ["AI_ORCHESTRATOR_HOME"],
            "live-owner",
            ready,
            release,
        ),
    )
    owner.start()
    held = Path(ready.get(timeout=e2e_timeout(10)))
    try:
        observed = tmp_path / "fresh-cwd"
        result = run_repo_task(
            str(origin),
            f"complete-now write-change\nrecord-cwd={observed}",
            "engineer",
            workspace=Workspace(root, resolver=lambda _spec: canonical, workflow="local"),
            branch=branch,
            base_path=command_base(),
            persona_dir=personas_dir,
            recorded_gate=["true"],
        )
        fresh = Path(observed.read_text(encoding="utf-8"))
        assert fresh != held
        assert result.ok, result.detail
        assert held.exists()
    finally:
        release.set()
        _join(owner)


def test_dirty_retained_recovery_is_marked_and_gate_rejection_cannot_advance_base(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """Recovery adopts the crash tree but publication still depends on its real hook."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-dirty-recovery")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    root = tmp_path / "worktrees-dirty-recovery"
    branch = "feature/dirty-recovery"
    ready: multiprocessing.Queue[str] = MP.Queue()
    process = MP.Process(
        target=_dirty_orphan_worktree_process,
        args=(str(canonical), str(canonical), str(root), branch, ready),
    )
    process.start()
    original = Path(ready.get(timeout=e2e_timeout(10)))
    _join(process)
    before = gitops.ref_sha(canonical, "main")
    gate_cwd = tmp_path / "recovery-gate-cwd"
    install_pre_push_hook(
        canonical,
        f"pwd > {gate_cwd}\nprintf 'complete gate failed deliberately\\n' >&2\nexit 1",
    )

    recovered = recover_repo(
        canonical,
        branch,
        workspace_root=root,
        recorded_gate=["true"],
    )

    assert recovered.outcome == "gate-failed", recovered.detail
    assert Path(gate_cwd.read_text(encoding="utf-8").strip()) == original
    assert gitops.ref_sha(canonical, "main") == before
    assert incomplete_commits(canonical, "origin/main", branch)
    assert "(incomplete step)" in gitops.log_messages(canonical, "origin/main", branch)[0].message


def test_dirty_retained_recovery_passes_the_gate_and_publishes_one_squash(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """A green real merge-path gate can publish adopted crash work exactly once."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-dirty-recovery-green")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    root = tmp_path / "worktrees-dirty-recovery-green"
    branch = "feature/dirty-recovery-green"
    ready: multiprocessing.Queue[str] = MP.Queue()
    process = MP.Process(
        target=_dirty_orphan_worktree_process,
        args=(str(canonical), str(canonical), str(root), branch, ready),
    )
    process.start()
    original = Path(ready.get(timeout=e2e_timeout(10)))
    _join(process)
    before = gitops.ref_sha(canonical, "main")
    gate_cwd = tmp_path / "green-recovery-gate-cwd"
    install_pre_push_hook(canonical, f"pwd >> {gate_cwd}\nexit 0")

    recovered = recover_repo(
        canonical,
        branch,
        workspace_root=root,
        recorded_gate=["true"],
    )

    assert recovered.ok and recovered.outcome == "merged", recovered.detail
    gated_paths = [Path(line) for line in gate_cwd.read_text(encoding="utf-8").splitlines()]
    assert gated_paths[0] == original
    assert gitops.ref_sha(canonical, "main") != before
    assert (
        subprocess.run(
            ["git", "-C", str(canonical), "show", "main:interrupted.txt"], capture_output=True
        ).returncode
        == 0
    )
    assert len(gitops.log_messages(canonical, before, "main")) == 1


def test_recovery_default_root_finds_the_lifecycle_retained_worktree(
    tmp_path: Path, bare_origin: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public default joins lifecycle's root without an operator override."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-default-recovery-root")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    root = fake_home / ".ai-orchestrator" / "worktrees"
    branch = "feature/default-recovery-root"
    ready: multiprocessing.Queue[str] = MP.Queue()
    process = MP.Process(
        target=_dirty_orphan_worktree_process,
        args=(str(canonical), str(canonical), str(root), branch, ready),
    )
    process.start()
    original = Path(ready.get(timeout=e2e_timeout(10)))
    _join(process)
    gate_cwd = tmp_path / "default-recovery-gate-cwd"
    install_pre_push_hook(canonical, f"pwd >> {gate_cwd}\nexit 0")

    recovered = recover_repo(canonical, branch, recorded_gate=["true"])

    assert recovered.ok, recovered.detail
    assert Path(gate_cwd.read_text(encoding="utf-8").splitlines()[0]) == original


def test_recovery_falls_back_from_a_live_matching_tree_to_the_preserved_branch(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """A held crash-tree candidate never displaces the durable recovery branch."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-live-recovery-fallback")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    branch = "feature/live-recovery-fallback"
    repo = normalize_repo(str(canonical))
    seed = _open_workspace(str(canonical), str(tmp_path / "seed-recovery-worktrees"))
    seeded = seed.worktree(repo, branch, base="origin/main")
    (seeded / "preserved.txt").write_text("durable recovery work\n", encoding="utf-8")
    gitops.add_all(seeded)
    gitops.commit(
        seeded,
        "chore: durable recovery (incomplete step)\n\nOrchestrator-Status: incomplete",
    )
    seed.remove_worktree(repo, seeded)

    root = tmp_path / "live-recovery-worktrees"
    ready: multiprocessing.Queue[str] = MP.Queue()
    release = MP.Event()
    owner = MP.Process(
        target=_held_identity_worktree_process,
        args=(
            str(canonical),
            str(canonical),
            str(root),
            branch,
            os.environ["AI_ORCHESTRATOR_HOME"],
            "held-recovery-owner",
            ready,
            release,
        ),
    )
    owner.start()
    held = Path(ready.get(timeout=e2e_timeout(10)))
    gate_cwd = tmp_path / "fallback-recovery-gate-cwd"
    install_pre_push_hook(canonical, f"pwd >> {gate_cwd}\nexit 0")
    try:
        recovered = recover_repo(
            canonical,
            branch,
            workspace_root=root,
            recorded_gate=["true"],
        )
        first_gate = Path(gate_cwd.read_text(encoding="utf-8").splitlines()[0])
        assert recovered.ok, recovered.detail
        assert first_gate != held
        assert held.exists()
        assert (
            subprocess.run(
                ["git", "-C", str(canonical), "show", "main:preserved.txt"], capture_output=True
            ).returncode
            == 0
        )
        assert (
            subprocess.run(
                ["git", "-C", str(canonical), "show", "main:live-incomplete.txt"],
                capture_output=True,
            ).returncode
            != 0
        )
    finally:
        release.set()
        _join(owner)


def test_only_the_three_newest_incomplete_run_worktrees_are_retained(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """The crash window is useful but bounded rather than an unbounded archive."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-bounded-retention")
    root = tmp_path / "worktrees-bounded-retention"
    run_roots: list[Path] = []
    for number in range(5):
        ready: multiprocessing.Queue[str] = MP.Queue()
        if number % 2:
            process = MP.Process(
                target=_committing_run_process,
                args=(
                    str(canonical),
                    str(root),
                    f"feature/incomplete-{number}",
                    False,
                    ready,
                ),
            )
        else:
            process = MP.Process(
                target=_dirty_orphan_worktree_process,
                args=(
                    str(canonical),
                    str(canonical),
                    str(root),
                    f"feature/incomplete-{number}",
                    ready,
                ),
            )
        process.start()
        reported = Path(ready.get(timeout=e2e_timeout(10)))
        _join(process)
        run_roots.append(reported if number % 2 else reported.parent)
        # Retention order is the run-root mtime, so make the intended ordering
        # explicit instead of depending on filesystem timestamp resolution.
        stamp = time.time() + number
        os.utime(run_roots[-1], (stamp, stamp))

    releases: list[MPEvent] = []
    owners: list[multiprocessing.Process] = []
    live_paths: list[Path] = []
    for number in range(2):
        ready = MP.Queue()
        release = MP.Event()
        owner = MP.Process(
            target=_held_identity_worktree_process,
            args=(
                str(canonical),
                str(canonical),
                str(root),
                f"feature/live-incomplete-{number}",
                os.environ["AI_ORCHESTRATOR_HOME"],
                f"live-incomplete-{number}",
                ready,
                release,
            ),
        )
        owner.start()
        live_paths.append(Path(ready.get(timeout=e2e_timeout(10))))
        owners.append(owner)
        releases.append(release)
    try:
        _open_workspace(str(canonical), str(root))

        assert all(not path.exists() for path in run_roots[:2])
        assert all(path.exists() for path in run_roots[2:])
        assert all(path.exists() for path in live_paths)
    finally:
        for release in releases:
            release.set()
        for owner in owners:
            _join(owner)
    for path in run_roots[2:]:
        shutil.rmtree(path)


def _same_branch_lifecycle_process(
    origin: str,
    canonical: str,
    root: str,
    branch: str,
    state_root: str,
    base_config: str,
    persona_dir: str,
    hold: Rendezvous,
    results: multiprocessing.Queue[dict[str, object]],
) -> None:
    """Drive a real onejudge dispatch that halts at its rendezvous.

    The hold lives inside the faked paid model, so the dispatch itself — the
    onejudge subprocess, its worktree, its git — is entirely real. That is the only
    way to hold two runs inside their agents at one instant without standing in for
    the boundary under test.
    """
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    result = run_repo_task(
        origin,
        # The first line is what names the change when no commit does, so the
        # rendezvous paths go on their own line rather than overrunning a subject.
        f"complete-now write-unique-change: one branch name, two runs\n{hold.sentinels()}",
        "engineer",
        workspace=Workspace(root, resolver=lambda _spec: Path(canonical), workflow="local"),
        branch=branch,
        base_path=base_config,
        persona_dir=persona_dir,
        recorded_gate=["true"],
        repo_type="single-owner",
    )
    results.put({"ok": result.ok, "outcome": result.outcome, "detail": result.detail})


def test_concurrent_lifecycles_share_a_branch_name_without_colliding(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
) -> None:
    """One identity, two live dispatches, one branch name — and no registry to race."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-parallel")
    root = tmp_path / "worktrees-parallel"
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    # One release for both dispatches: the journey needs them held at one instant.
    release = barrier / "shared.release"
    holds = [Rendezvous(barrier / f"ready-{index}", release) for index in range(2)]
    results: multiprocessing.Queue[dict[str, object]] = MP.Queue()
    processes = [
        MP.Process(
            target=_same_branch_lifecycle_process,
            args=(
                str(origin),
                str(canonical),
                str(root),
                "feature/shared-name",
                os.environ["AI_ORCHESTRATOR_HOME"],
                str(command_base()),
                str(personas_dir),
                holds[index],
                results,
            ),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    try:
        # Both real dispatches are inside their agents at the same moment, on the
        # same branch name, before either can reach teardown.
        deadline = e2e_deadline(60)
        while not all(hold.arrived() for hold in holds) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert all(hold.arrived() for hold in holds), "both dispatches never ran at once"
        # The shared checkout is only an object store here: it registers no
        # worktree, so no run's cleanup can prune another run's tree out of it.
        assert gitops.worktrees(canonical) == {"main": canonical.resolve()}
        # A linked worktree records a `.git` file; two live ones mean two runs cut
        # the same branch name from clones that know nothing of each other.
        live = sorted(entry for entry in root.rglob(".git") if entry.is_file())
        assert len(live) == 2, live
    finally:
        holds[0].let_go()
        settled = [results.get(timeout=e2e_timeout(60)) for _ in processes]
        for process in processes:
            _join(process)

    # Both settled on their own terms. Two runs deliberately sharing one branch
    # name still race for the base, so the loser may find its content already
    # there — what must not happen is either run losing its tree or its work.
    assert all(item["ok"] for item in settled), settled
    assert {item["outcome"] for item in settled} <= {"merged", "already-integrated"}


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


def _fetching_lifecycle_process(
    origin: str,
    canonical: str,
    root: str,
    state_root: str,
    base_config: str,
    persona_dir: str,
    fetched: MPEvent,
) -> None:
    """Run a real dispatch, reporting the moment setup's origin fetch has finished.

    Nothing has to be held open here: setup fetches before it ever dispatches, so
    the observation lands while the parent still holds the shared checkout, and the
    onejudge subprocess that follows is entirely real.
    """
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root

    def observe(kind: str, detail: Mapping[str, str | float | bool]) -> None:
        if kind == "setup-finished" and detail.get("operation") == "fetch":
            fetched.set()

    set_harness_observer(observe)
    run_repo_task(
        origin,
        "complete-now write-change: prove setup fetches without the shared checkout",
        "engineer",
        workspace=Workspace(root, resolver=lambda _spec: Path(canonical), workflow="local"),
        base_path=base_config,
        persona_dir=persona_dir,
        recorded_gate=["true"],
        repo_type="single-owner",
    )


def test_execution_checkout_fetch_never_runs_inside_the_exclusive_section(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
) -> None:
    """A run's fetch must not be blocked by a sibling holding the shared checkout."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-fetch")
    fetched = MP.Event()
    process = MP.Process(
        target=_fetching_lifecycle_process,
        args=(
            str(origin),
            str(canonical),
            str(tmp_path / "worktrees-fetch"),
            os.environ["AI_ORCHESTRATOR_HOME"],
            str(command_base()),
            str(personas_dir),
            fetched,
        ),
    )
    try:
        with advisory_lock(git_lock_identity(gitops.common_dir(canonical))):
            process.start()
            assert fetched.wait(e2e_timeout(30)), (
                "the dispatch's fetch never completed while the shared checkout was held"
            )
    finally:
        _join(process)


def test_a_runs_branch_cleanup_never_withdraws_a_siblings_preserved_work(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """Two runs, one branch name, divergent work — neither may erase the other's."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-cleanup")
    root = tmp_path / "worktrees-cleanup"
    repo = normalize_repo(str(canonical))
    shared = "feature/contested-name"

    # This run cuts the name first, so it adopts nothing: its branch and the
    # sibling's are unrelated lines of work that happen to share a name.
    other = _open_workspace(str(canonical), str(root))
    other_tree = other.worktree(repo, shared, base="origin/main")

    # The sibling preserves work under that name. Its worktree teardown copies the
    # branch into the shared checkout, which becomes its only surviving record.
    sibling = _open_workspace(str(canonical), str(root))
    sibling_tree = sibling.worktree(repo, shared, base="origin/main")
    (sibling_tree / "sibling.txt").write_text("only the sibling has this\n", encoding="utf-8")
    gitops.add_all(sibling_tree)
    gitops.commit(sibling_tree, "feat: sibling work that never reached origin")
    sibling.remove_worktree(repo, sibling_tree)
    preserved = gitops.ref_sha(canonical, shared)

    # This run finishes its own divergent work and abandons the branch — the
    # synthetic-stack-base teardown path, which deletes what it is done with.
    (other_tree / "other.txt").write_text("a different line of work\n", encoding="utf-8")
    gitops.add_all(other_tree)
    gitops.commit(other_tree, "feat: work that must not overwrite the sibling")
    other.remove_worktree(repo, other_tree)
    other.delete_branch(repo, shared)

    # Its own clone dropped the branch; the sibling's record is untouched, neither
    # rewound by the teardown copy nor withdrawn by the delete.
    assert not gitops.branch_exists(other.clone_dir(repo), shared)
    assert gitops.ref_sha(canonical, shared) == preserved
    assert _has_file(canonical, shared, "sibling.txt")
    assert not _has_file(canonical, shared, "other.txt")

    # A run still withdraws the copy it made itself, so cleanup reclaims its own.
    owner = _open_workspace(str(canonical), str(root))
    owner_tree = owner.worktree(repo, "feature/owned-name", base="origin/main")
    (owner_tree / "owned.txt").write_text("mine\n", encoding="utf-8")
    gitops.add_all(owner_tree)
    gitops.commit(owner_tree, "feat: work this run owns")
    owner.remove_worktree(repo, owner_tree)
    assert gitops.branch_exists(canonical, "feature/owned-name")
    owner.delete_branch(repo, "feature/owned-name")

    assert not gitops.branch_exists(canonical, "feature/owned-name")


def test_a_sibling_advancing_a_mirrored_branch_keeps_it_from_the_delete(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """The window between one run's copy and its cleanup belongs to whoever moved it."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-advanced")
    root = tmp_path / "worktrees-advanced"
    repo = normalize_repo(str(canonical))
    shared = "feature/advanced-name"

    # This run preserves work and hands it to the shared checkout.
    owner = _open_workspace(str(canonical), str(root))
    owner_tree = owner.worktree(repo, shared, base="origin/main")
    (owner_tree / "first.txt").write_text("the first run's work\n", encoding="utf-8")
    gitops.add_all(owner_tree)
    gitops.commit(owner_tree, "feat: first run's preserved work")
    owner.remove_worktree(repo, owner_tree)

    # Before it cleans up, a sibling adopts that branch and preserves newer work
    # on top — the shared copy now carries commits the first run never had.
    sibling = _open_workspace(str(canonical), str(root))
    sibling_tree = sibling.worktree(repo, shared, base="origin/main")
    (sibling_tree / "second.txt").write_text("the sibling's newer work\n", encoding="utf-8")
    gitops.add_all(sibling_tree)
    gitops.commit(sibling_tree, "feat: sibling's newer preserved work")
    sibling.remove_worktree(repo, sibling_tree)
    advanced = gitops.ref_sha(canonical, shared)

    owner.delete_branch(repo, shared)

    # The delete declined: the branch is no longer where this run left it, and the
    # sibling's newer work is the only record of itself.
    assert gitops.ref_sha(canonical, shared) == advanced
    assert _has_file(canonical, shared, "second.txt")
    assert _has_file(canonical, shared, "first.txt")


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

    # Once the durable checkout carries the same-or-newer branch, the run root is
    # superseded and, after this rejoining owner ends, the next sweep may reclaim it.
    reclaim._run_leases[roots[False]].__exit__(None, None, None)
    (roots[False] / "owner.json").unlink()
    _open_workspace(str(canonical), str(root))
    assert not roots[False].exists()


def _rejoining_run_process(
    canonical: str, root: str, token: str, state_root: str, joined: MPEvent, release: MPEvent
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
            recorded_gate=["true"],
        )
