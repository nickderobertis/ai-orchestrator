"""Real-CLI journey: `just recoverable` inventories what the lifecycle really left.

The branches asserted on here are produced by the real repo lifecycle against a real
bare git origin — a workstream that hit its turn cap and preserved an incomplete
step, and one whose gate failed and preserved a complete branch. Nothing about the
git, the preservation, the provenance markers, or the registry is simulated; only the
paid harness is, at this repository's designated seam.

What the view then has to get right is the thing that cost twenty minutes of
forensics twice in one week: which of the two recovery verbs each branch takes, and
whether the publication checkout even has the branch to run it against.
"""

# llmlint: ignore-file[e2e_not_mocked] the repository requires faking the paid agent
# harness; the lifecycle, the git origin, the preservation and its provenance
# markers, the registry, and the `orchestrator-recoverable` CLI are all real.

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import install_pre_push_hook
from fakes import make_writing_dispatch
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT, gitops
from orchestrator.lifecycle import run_repo_task
from orchestrator.registry import Registry
from orchestrator.workspace import Workspace


def _register(home: Path, canonical: Path, *, gate: str) -> None:
    """Register the checkout through the production registry writer.

    The identity gate is what the merge path actually runs, so it is what decides
    whether a completed change publishes or is preserved as a gate-failed branch —
    which is the branch this view has to offer `integrate` for.
    """
    registry = Registry(home / "repos.json")
    registry.register(
        str(canonical), str(canonical), workflow="local", repo_type="single-owner", gate=gate
    )


def _refuse_publication(checkout: Path) -> None:
    """Make this checkout's own merge-path gate refuse, through its real pre-push hook.

    That hook is where publication is actually gated, so a completed change stopped by
    it is the real gate-failed branch: whole, unpublished, and carrying no incomplete
    provenance — the branch `integrate` exists to take through the gate again.
    """
    install_pre_push_hook(checkout, "printf 'pre-push: complete gate failed\\n' >&2\nexit 1")


def _workspace(tmp_path: Path, canonical: Path, name: str) -> Workspace:
    return Workspace(
        tmp_path / name,
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )


def _view(home: Path, runs_dir: Path, workspace_root: Path) -> list[dict[str, object]]:
    """Run the real view CLI against this journey's own orchestrator home."""
    completed = subprocess.run(
        [
            "uv",
            "run",
            "orchestrator-recoverable",
            "--runs-dir",
            str(runs_dir),
            "--workspace",
            str(workspace_root),
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        env={**os.environ, "AI_ORCHESTRATOR_HOME": str(home)},
    )
    assert completed.returncode == 0, completed.stderr
    rows = json.loads(completed.stdout)
    assert isinstance(rows, list)
    return rows


def _row(rows: list[dict[str, object]], branch: str) -> dict[str, object]:
    matched = [row for row in rows if row["branch"] == branch]
    assert len(matched) == 1, f"expected exactly one row for {branch!r}, got {rows}"
    return matched[0]


def test_recoverable_names_the_right_verb_for_each_preserved_branch(
    tmp_path, bare_origin, command_base, personas_dir, monkeypatch
) -> None:
    """An incomplete step offers `repo-recover`; a complete branch offers `integrate`.

    Both branches here are real lifecycle output. The distinction between them is the
    provenance marker the lifecycle writes when a step is left unfinished, and it is
    the only thing that decides which command can publish the work — the discovery
    that used to be made one error message at a time.
    """
    home = tmp_path / "orchestrator-home"
    home.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home))
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    _register(home, canonical, gate="true")
    _refuse_publication(canonical)

    # A workstream that ran out of turns: the lifecycle preserves what it has and
    # marks the step incomplete.
    interrupted = run_repo_task(
        str(origin),
        "write-change preserve interrupted work",
        "engineer",
        workspace=_workspace(tmp_path, canonical, "interrupted-worktrees"),
        branch="preserved/interrupted",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
        max_turns=1,
    )
    assert interrupted.outcome == "not-completed", interrupted.detail

    # A workstream whose change is complete and whose gate refused it: the branch is
    # preserved whole, with no incomplete provenance on it.
    refused = run_repo_task(
        str(origin),
        "write-change complete but unmergeable",
        "engineer",
        workspace=_workspace(tmp_path, canonical, "refused-worktrees"),
        branch="preserved/refused",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
        dispatch_fn=make_writing_dispatch(),
    )
    assert refused.outcome == "gate-failed", refused.detail

    rows = _view(home, tmp_path / "runs", tmp_path / "interrupted-worktrees")

    interrupted_row = _row(rows, "preserved/interrupted")
    assert interrupted_row["incomplete"] is True
    assert "just repo-recover preserved/interrupted" in str(interrupted_row["command"])

    refused_row = _row(rows, "preserved/refused")
    assert refused_row["incomplete"] is False
    assert "just integrate preserved/refused" in str(refused_row["command"])
    assert "repo-recover" not in str(refused_row["command"])

    # Both branches are in the publication checkout, so neither command needs a fetch.
    assert interrupted_row["in_publication_checkout"] is True
    assert "git -C" not in str(refused_row["command"])


def test_a_branch_only_in_an_execution_clone_carries_its_fetch(
    tmp_path, bare_origin, command_base, personas_dir, monkeypatch
) -> None:
    """A branch the publication checkout lost is reported with the fetch to bring it back.

    This is the invocation that failed: `just integrate` reads local branches only, so
    aiming it at a branch the canonical checkout does not have refuses for a reason
    that says nothing about the fix. The run clone the lifecycle really cut still has
    the branch, and the suggested command starts by fetching it from there.
    """
    home = tmp_path / "orchestrator-home"
    home.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home))
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    _register(home, canonical, gate="true")
    _refuse_publication(canonical)
    workspace_root = tmp_path / "worktrees"

    result = run_repo_task(
        str(origin),
        "write-change complete but unmergeable",
        "engineer",
        workspace=_workspace(tmp_path, canonical, "worktrees"),
        branch="preserved/clone-only",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
        dispatch_fn=make_writing_dispatch(),
    )
    assert result.outcome == "gate-failed", result.detail
    # Lose the branch from the publication checkout, which is the state a dispatch
    # killed before it could preserve its work leaves behind: the run clone the
    # lifecycle cut still has every commit, and nothing durable names it.
    gitops.delete_branch(canonical, "preserved/clone-only")

    row = _row(_view(home, tmp_path / "runs", workspace_root), "preserved/clone-only")

    assert row["in_publication_checkout"] is False
    command = str(row["command"])
    assert command.startswith("git -C ")
    assert "fetch" in command
    assert "refs/heads/preserved/clone-only:refs/heads/preserved/clone-only" in command
    assert " && just integrate preserved/clone-only" in command
    assert str(row["checkout"]).startswith(str(workspace_root))


def test_a_branch_its_base_already_carries_does_not_appear(
    tmp_path, bare_origin, command_base, personas_dir, monkeypatch
) -> None:
    """A published workstream is not recoverable work, and must not be offered as it.

    The one rule that keeps this view actionable: a branch whose commits reached
    origin has nothing to recover, so it drops out on the evidence rather than on a
    name-shaped guess.
    """
    home = tmp_path / "orchestrator-home"
    home.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home))
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    _register(home, canonical, gate="true")

    merged = run_repo_task(
        str(origin),
        "write-change publish it",
        "engineer",
        workspace=_workspace(tmp_path, canonical, "worktrees"),
        branch="published/change",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
        dispatch_fn=make_writing_dispatch(),
    )
    assert merged.outcome == "merged", merged.detail

    rows = _view(home, tmp_path / "runs", tmp_path / "worktrees")
    assert not [row for row in rows if row["branch"] == "published/change"], rows
    # The registry itself is intact and the view ran clean, so an empty answer is an
    # observation rather than a failure to look.
    assert Registry(home / "repos.json").entries
