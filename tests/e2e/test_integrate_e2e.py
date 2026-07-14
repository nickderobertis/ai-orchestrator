"""E2E journeys for the integration train, using real git and a bare origin."""

# llmlint: ignore-file[e2e_not_mocked] one installed-console journey proves the CLI artifact;
# the remaining real-git journeys call its public core in-process so pytest-cov can enforce the
# repository's 95% threshold. Git, worktrees, conflicts, gates, merges, and pushes remain real.

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from orchestrator.integrate import IntegrateError, integrate, main


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def _branch(repo: Path, name: str, files: dict[str, str], *, start: str = "main") -> None:
    _git(repo, "checkout", "-b", name, start)
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"add {name}")
    _git(repo, "checkout", "main")


def _clone(tmp_path: Path, origin: Path) -> Path:
    repo = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True, capture_output=True)
    return repo


def _integrate(repo: Path, *branches: str, extra: tuple[str, ...] = ()) -> dict[str, object]:
    proc = subprocess.run(
        [
            "orchestrator-integrate",
            "--repo",
            str(repo),
            "--gate",
            "true",
            "--format",
            "json",
            *extra,
            *branches,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(proc.stdout)


def test_merge_train_pushes_all_branches_and_rerun_is_idempotent(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    repo = _clone(tmp_path, origin)
    _branch(repo, "claude/a", {"a.txt": "a\n"})
    _branch(repo, "claude/b", {"b.txt": "b\n"})

    result = integrate(repo, ["claude/a", "claude/b"], gate_command=["true"], push=True)
    assert [item.status for item in result.branches] == ["merged", "merged"]
    assert result.base_advanced and result.pushed
    assert _git(origin, "show", "main:a.txt") == "a"
    assert _git(origin, "show", "main:b.txt") == "b"

    rerun = integrate(repo, ["claude/a", "claude/b"], gate_command=["true"], push=True)
    assert [item.status for item in rerun.branches] == ["already-merged", "already-merged"]
    assert not rerun.base_advanced and not rerun.pushed


def test_conflict_is_skipped_and_train_continues(tmp_path, bare_origin) -> None:
    origin = bare_origin({"shared.txt": "old\n"})
    repo = _clone(tmp_path, origin)
    _branch(repo, "claude/conflict", {"shared.txt": "branch\n"})
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-m", "advance base")
    _branch(repo, "claude/good", {"good.txt": "good\n"})

    result = integrate(repo, ["claude/conflict", "claude/good"], gate_command=["true"])
    assert [(item.status, item.reason) for item in result.branches] == [
        ("skipped", "conflict"),
        ("merged", None),
    ]
    assert (repo / "shared.txt").read_text(encoding="utf-8") != "branch\n"
    assert (repo / "good.txt").is_file()


def test_gate_failure_is_skipped_and_green_branch_merges(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _branch(repo, "claude/red", {"FAIL": "red\n"})
    _branch(repo, "claude/green", {"green.txt": "green\n"})

    result = integrate(
        repo,
        ["claude/red", "claude/green"],
        gate_command=["sh", "-c", "test ! -f FAIL"],
    )
    assert [(item.status, item.reason) for item in result.branches] == [
        ("skipped", "gate-failed"),
        ("merged", None),
    ]
    assert not (repo / "FAIL").exists()
    assert (repo / "green.txt").is_file()


def test_refresh_updates_discovered_branch_without_merging(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _branch(repo, "claude/in-flight", {"feature.txt": "feature\n"})
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "advance base")
    base_tip = _git(repo, "rev-parse", "main")

    result = integrate(repo, refresh=True)
    assert [(item.branch, item.status) for item in result.branches] == [
        ("claude/in-flight", "updated")
    ]
    assert not result.base_advanced
    assert _git(repo, "merge-base", "--is-ancestor", base_tip, "claude/in-flight") == ""
    assert not (repo / "feature.txt").exists()


def test_dirty_candidate_worktree_is_rejected(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _branch(repo, "claude/dirty", {"feature.txt": "feature\n"})
    candidate = tmp_path / "candidate"
    _git(repo, "worktree", "add", str(candidate), "claude/dirty")
    (candidate / "untracked").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(IntegrateError, match="candidate worktree.*is dirty"):
        integrate(repo, ["claude/dirty"], gate_command=["true"])

    assert _git(repo, "status", "--porcelain") == ""


def test_base_merge_failure_is_aborted_and_skipped(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _branch(repo, "claude/racing", {"shared.txt": "candidate\n"})
    quoted_repo = shlex.quote(str(repo))
    gate = [
        "sh",
        "-c",
        (
            f"printf 'concurrent\\n' > {quoted_repo}/shared.txt && "
            f"git -C {quoted_repo} add shared.txt && "
            f"git -C {quoted_repo} commit -m 'advance base during gate'"
        ),
    ]

    result = integrate(repo, ["claude/racing"], gate_command=gate)

    assert [(item.status, item.reason) for item in result.branches] == [("skipped", "not-ready")]
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "concurrent\n"
    assert _git(repo, "status", "--porcelain") == ""


def test_installed_console_entry_runs_real_git_journey(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _branch(repo, "claude/cli", {"cli.txt": "cli\n"})

    result = _integrate(repo, "claude/cli")

    assert result["branches"] == [{"branch": "claude/cli", "status": "merged", "reason": None}]
    assert (repo / "cli.txt").read_text(encoding="utf-8") == "cli\n"


def test_cli_readable_success(tmp_path, bare_origin, capsys) -> None:
    repo = _clone(tmp_path, bare_origin())
    _branch(repo, "claude/readable", {"readable.txt": "readable\n"})

    assert (
        main(
            [
                "--repo",
                str(repo),
                "--gate",
                "true",
                "claude/readable",
            ]
        )
        == 0
    )
    assert "claude/readable: merged" in capsys.readouterr().out
