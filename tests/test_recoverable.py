"""Focused tests for the recovery inventory, driven against real git repositories.

The e2e journey proves this view against branches the *lifecycle* preserved, through
the installed CLI. These prove the decisions that view makes per branch — which
recovery verb it offers, whether the publication checkout even has the branch, what
stopped the workstream, and how the rows are ordered — against repositories built
here with real git. Nothing is faked: the branches, their provenance markers, the run
clone, the registry, and the round results are all the production shapes, and the
entry point exercised is the command's own ``main``.

Building the branches directly rather than by running a lifecycle is what lets one
case be stated per test: a branch a dispatch died on carries no lifecycle record
beyond its own commits, so the corners worth pinning here are cheap to construct and
expensive to arrange through a whole workstream.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from orchestrator import gitops
from orchestrator.provenance import INCOMPLETE_TRAILER
from orchestrator.recoverable import collect, main
from orchestrator.registry import Registry
from orchestrator.runs import write_result
from orchestrator.workspace import CLONE_DIR_NAME, RUNS_DIR_NAME, normalize_repo

#: A commit carrying the provenance the lifecycle leaves where a step stopped.
_INTERRUPTED = f"chore: orchestrated change (incomplete step)\n\n{INCOMPLETE_TRAILER}"


@contextmanager
def _committed_at(day: str) -> Iterator[None]:
    """Commit on an explicit date, so ordering is not decided at second resolution.

    Git stamps commits to the second and this suite makes several within one, which
    would leave the newest-first ordering this view promises to the sort's tie-break
    rather than to the ages it is meant to rank.
    """
    stamp = f"{day}T12:00:00+00:00"
    previous = {key: os.environ.get(key) for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")}
    os.environ.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _branch(repo: Path, worktrees: Path, branch: str, message: str, *, when: str) -> None:
    """Leave ``branch`` behind in ``repo`` with one commit on it, as a dispatch does."""
    tree = gitops.worktree_add(
        repo, worktrees / branch.replace("/", "-"), branch, base="origin/main"
    )
    (tree / f"{branch.replace('/', '-')}.txt").write_text("work\n", encoding="utf-8")
    gitops.add_all(tree)
    with _committed_at(when):
        gitops.commit(tree, message)
    # The worktree goes; the branch and its commits stay, which is the state a killed
    # dispatch leaves behind and the only state this view ever reads.
    gitops.worktree_remove(repo, tree)


def _registered(tmp_path: Path, bare_origin: Callable[..., Path]) -> tuple[Path, Path]:
    """Register a real single-owner checkout, returning its origin and the checkout."""
    origin = bare_origin()
    canonical = gitops.clone(str(origin), tmp_path / "canonical")
    Registry().register(
        str(canonical),
        str(canonical),
        workflow="local",
        repo_type="single-owner",
        gate="just check",
    )
    return origin, canonical


def _run_clone(workspace: Path, origin: Path, canonical: Path, name: str) -> Path:
    """Clone ``canonical`` where the lifecycle puts a run's clone for this identity."""
    runs_root = workspace / normalize_repo(str(origin)).dir_key / RUNS_DIR_NAME
    runs_root.mkdir(parents=True, exist_ok=True)
    return gitops.clone(str(canonical), runs_root / name / CLONE_DIR_NAME)


def _rows(runs: Path, workspace: Path, capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    """Run the view's own CLI in JSON form and return the rows it printed."""
    assert main(["--runs-dir", str(runs), "--workspace", str(workspace), "--format", "json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert isinstance(rows, list)
    return rows


def _row(rows: list[dict[str, Any]], branch: str) -> dict[str, Any]:
    matched = [row for row in rows if row["branch"] == branch]
    assert len(matched) == 1, f"expected one row for {branch!r}, got {rows}"
    return matched[0]


def test_the_verb_offered_is_decided_by_provenance_and_never_by_the_branch_name(
    tmp_path: Path, bare_origin: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """A marker means `repo-recover`; its absence means `integrate`.

    Offering the wrong one is the mistake this view exists to stop: a branch carrying
    an incomplete-step marker may only be published through the recovery path, and a
    complete one is `integrate`'s. Both branches here sit in the same checkout, so the
    only thing separating the two commands is the provenance on their commits.
    """
    _, canonical = _registered(tmp_path, bare_origin)
    _branch(canonical, tmp_path / "wt", "feature/complete", "feat: finished", when="2026-01-02")
    _branch(canonical, tmp_path / "wt", "feature/interrupted", _INTERRUPTED, when="2026-01-01")

    rows = _rows(tmp_path / "runs", tmp_path / "workspace", capsys)

    complete = _row(rows, "feature/complete")
    assert complete["incomplete"] is False
    assert complete["command"] == f"just integrate feature/complete --repo {canonical}"
    interrupted = _row(rows, "feature/interrupted")
    assert interrupted["incomplete"] is True
    assert interrupted["command"] == f"just repo-recover feature/interrupted --repo {canonical}"
    # Both are in the publication checkout, so neither command has to bring it there.
    assert all(row["in_publication_checkout"] for row in rows)
    # Newest work first: the branch a dispatch just lost is the one being looked for.
    assert [row["branch"] for row in rows] == ["feature/complete", "feature/interrupted"]


def test_a_later_commit_does_not_make_preserved_work_complete(
    tmp_path: Path, bare_origin: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The marker is asked of the whole base-relative history, not of the tip.

    Work that continued past the marker leaves it underneath an ordinary commit.
    Reading only the tip would offer `integrate`, which refuses to publish interrupted
    provenance — the same twenty-minute detour, one commit later.
    """
    _, canonical = _registered(tmp_path, bare_origin)
    tree = gitops.worktree_add(canonical, tmp_path / "wt", "feature/resumed", base="origin/main")
    (tree / "partial.txt").write_text("partial\n", encoding="utf-8")
    gitops.add_all(tree)
    gitops.commit(tree, _INTERRUPTED)
    (tree / "more.txt").write_text("more\n", encoding="utf-8")
    gitops.add_all(tree)
    gitops.commit(tree, "feat: carry on from the preserved work")
    gitops.worktree_remove(canonical, tree)

    row = _row(_rows(tmp_path / "runs", tmp_path / "workspace", capsys), "feature/resumed")

    assert row["incomplete"] is True
    assert row["tip_subject"] == "feat: carry on from the preserved work"
    assert row["command"].startswith("just repo-recover")


def test_a_branch_only_a_run_clone_has_carries_the_fetch_that_lands_it(
    tmp_path: Path, bare_origin: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The publication checkout does not have it, so the command brings it there first.

    A dispatch cut its branch in the run clone and died there. Aiming `integrate` at a
    checkout that has never heard of the branch is the failure this module's own
    docstring records, so the row has to carry the import that precedes it.
    """
    origin, canonical = _registered(tmp_path, bare_origin)
    workspace = tmp_path / "workspace"
    clone = _run_clone(workspace, origin, canonical, "run-live")
    # Ordinary leftovers beside a real run: a run root with no clone in it, and one
    # whose clone directory is not a repository. Neither may be read as a run clone.
    (clone.parent.parent / "run-empty").mkdir()
    (clone.parent.parent / "run-bogus" / CLONE_DIR_NAME).mkdir(parents=True)
    _branch(clone, tmp_path / "clone-wt", "feature/killed", "feat: work lost", when="2026-01-03")

    row = _row(_rows(tmp_path / "runs", workspace, capsys), "feature/killed")

    assert row["in_publication_checkout"] is False
    assert row["checkout"] == str(clone)
    assert row["command"] == (
        f"git -C {canonical} fetch {clone} "
        "refs/heads/feature/killed:refs/heads/feature/killed && "
        f"just integrate feature/killed --repo {canonical}"
    )


def test_an_incomplete_branch_outside_the_publication_checkout_names_where_it_is(
    tmp_path: Path, bare_origin: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """`repo-recover` fetches for itself, so it is given the checkout, not a git line."""
    origin, canonical = _registered(tmp_path, bare_origin)
    workspace = tmp_path / "workspace"
    clone = _run_clone(workspace, origin, canonical, "run-1")
    _branch(clone, tmp_path / "clone-wt", "feature/stopped", _INTERRUPTED, when="2026-01-04")

    row = _row(_rows(tmp_path / "runs", workspace, capsys), "feature/stopped")

    assert row["command"] == (
        f"just repo-recover feature/stopped --repo {canonical} --execution-checkout {clone}"
    )


def test_the_round_result_that_named_the_branch_is_why_it_stopped(
    tmp_path: Path, bare_origin: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The lifecycle already named the reason; a branch with no record says so plainly."""
    _, canonical = _registered(tmp_path, bare_origin)
    _branch(canonical, tmp_path / "wt", "feature/gated", "feat: refused", when="2026-01-05")
    _branch(canonical, tmp_path / "wt", "feature/silent", "feat: unrecorded", when="2026-01-06")
    runs = tmp_path / "runs"
    round_dir = runs / "run-7" / "round-01"
    round_dir.mkdir(parents=True)
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "failed",
            "started_order": ["api"],
            "results": {
                "api": {
                    "kind": "agent",
                    "status": "failed",
                    "ok": False,
                    "branch": "feature/gated",
                    "outcome": "gate-failed",
                }
            },
        },
    )

    rows = _rows(runs, tmp_path / "workspace", capsys)

    assert _row(rows, "feature/gated")["stopped_because"] == "run-7 node api: failed (gate-failed)"
    assert "died before recording one" in _row(rows, "feature/silent")["stopped_because"]


def test_a_branch_its_base_already_carries_is_not_offered_at_all(
    tmp_path: Path, bare_origin: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Published work drops out by the unpublished rule, never by a name-shaped guess."""
    _, canonical = _registered(tmp_path, bare_origin)
    _branch(canonical, tmp_path / "wt", "feature/landed", "feat: published", when="2026-01-07")
    gitops.push(canonical, "feature/landed")
    gitops.fetch(canonical)

    assert _rows(tmp_path / "runs", tmp_path / "workspace", capsys) == []


def test_the_default_rendering_reads_as_an_operator_inventory(
    tmp_path: Path, bare_origin: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The text a planner actually reads names the age, the place, and the command."""
    _, canonical = _registered(tmp_path, bare_origin)
    _branch(canonical, tmp_path / "wt", "feature/held", "feat: preserved", when="2026-01-08")

    assert main(["--runs-dir", str(tmp_path / "r"), "--workspace", str(tmp_path / "w")]) == 0
    rendered = capsys.readouterr().out

    assert "1 preserved unpublished branch(es):" in rendered
    assert "feature/held" in rendered
    assert f"Found in: {canonical}" in rendered
    assert "NOT in the publication checkout" not in rendered
    assert f"Resume: just integrate feature/held --repo {canonical}" in rendered
    assert "m old)" in rendered


def test_nothing_preserved_is_reported_as_nothing_rather_than_as_an_empty_list(
    tmp_path: Path, bare_origin: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _registered(tmp_path, bare_origin)

    assert main(["--runs-dir", str(tmp_path / "r"), "--workspace", str(tmp_path / "w")]) == 0

    assert "No preserved unpublished branches." in capsys.readouterr().out


def test_a_registry_it_cannot_read_is_a_usage_error_and_never_an_empty_inventory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreadable registry must not render as "nothing to recover".

    That is the one claim this view may never make wrongly: a planner reading it
    concludes there is nothing left to land and moves on from work still sitting in a
    clone, which is the forensics this whole view exists to retire.
    """
    home = Path(os.environ["AI_ORCHESTRATOR_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "repos.json").write_text("{[", encoding="utf-8")

    assert main(["--runs-dir", str(tmp_path / "r"), "--workspace", str(tmp_path / "w")]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("recoverable: ")


def test_a_registered_path_that_is_no_longer_a_checkout_contributes_no_rows(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """An entry outliving the repository at its path is skipped, not raised on.

    This view runs beside live work, so one identity whose checkout has been emptied
    out may not take the whole inventory down with it — the other identities' branches
    are exactly what the planner opened it for.
    """
    _, canonical = _registered(tmp_path, bare_origin)
    _branch(canonical, tmp_path / "wt", "feature/orphan", "feat: preserved", when="2026-01-09")
    assert [branch.branch for branch in collect(workspace_root=tmp_path / "workspace")] == [
        "feature/orphan"
    ]

    for path in sorted((canonical / ".git").rglob("*"), reverse=True):
        path.unlink() if path.is_file() or path.is_symlink() else path.rmdir()
    (canonical / ".git").rmdir()

    assert collect(workspace_root=tmp_path / "workspace") == []
