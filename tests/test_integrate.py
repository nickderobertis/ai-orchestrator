"""Unit tests for integration planning and its CLI boundary."""

from __future__ import annotations

import json

import pytest

from orchestrator.integrate import IntegrateError, integrate, main, plan


def test_plan_preserves_order_and_marks_merged() -> None:
    result = plan(["claude/b", "claude/a"], "main", {"claude/a"})
    assert [(item.branch, item.status) for item in result] == [
        ("claude/b", "updated"),
        ("claude/a", "already-merged"),
    ]


@pytest.mark.parametrize("branches", [["main"], ["claude/a", "claude/a"]])
def test_plan_rejects_unsafe_candidates(branches: list[str]) -> None:
    with pytest.raises(IntegrateError):
        plan(branches, "main", set())


def test_cli_rejects_unknown_branch(tmp_path, capsys) -> None:
    assert main(["--repo", str(tmp_path), "missing"]) == 2
    assert "integrate:" in capsys.readouterr().err


def test_cli_rejects_empty_gate(tmp_path, bare_origin, capsys) -> None:
    from orchestrator import gitops

    repo = gitops.clone(str(bare_origin()), tmp_path / "clone")
    assert main(["--repo", str(repo), "--gate", ""]) == 2
    assert "gate command must not be empty" in capsys.readouterr().err


def test_cli_json_success(tmp_path, bare_origin, capsys) -> None:
    from orchestrator import gitops

    repo = gitops.clone(str(bare_origin()), tmp_path / "clone")
    assert main(["--repo", str(repo), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "base": "main",
        "branches": [],
        "base_advanced": False,
        "pushed": False,
    }


def test_push_rejects_unknown_remote(tmp_path, bare_origin) -> None:
    from orchestrator import gitops
    from orchestrator.integrate import integrate

    repo = gitops.clone(str(bare_origin()), tmp_path / "clone")
    with pytest.raises(IntegrateError, match="unknown git remote"):
        integrate(repo, refresh=True, push=True, remote="missing")


def test_integrate_requires_checked_out_base(tmp_path, bare_origin) -> None:
    from orchestrator import gitops

    repo = gitops.clone(str(bare_origin()), tmp_path / "clone")
    with pytest.raises(IntegrateError, match="must have base branch 'other' checked out"):
        integrate(repo, base="other", refresh=True)


def test_integrate_rejects_dirty_base(tmp_path, bare_origin) -> None:
    from orchestrator import gitops

    repo = gitops.clone(str(bare_origin()), tmp_path / "clone")
    (repo / "untracked").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(IntegrateError, match="base worktree is dirty"):
        integrate(repo, refresh=True)


def test_integrate_rejects_invalid_remote_name(tmp_path, bare_origin) -> None:
    from orchestrator import gitops

    repo = gitops.clone(str(bare_origin()), tmp_path / "clone")
    with pytest.raises(IntegrateError, match="invalid remote name"):
        integrate(repo, refresh=True, remote="--upload-pack=malicious")


def test_integrate_rejects_unknown_branch(tmp_path, bare_origin) -> None:
    from orchestrator import gitops

    repo = gitops.clone(str(bare_origin()), tmp_path / "clone")
    with pytest.raises(IntegrateError, match="unknown local branch: 'missing'"):
        integrate(repo, ["missing"], refresh=True)


def test_json_shape() -> None:
    # Keep the pure structured contract explicit without requiring git here.
    item = plan(["claude/a"], "main", set())[0]
    assert json.loads(json.dumps(item.__dict__)) == {
        "branch": "claude/a",
        "reason": None,
        "status": "updated",
    }
