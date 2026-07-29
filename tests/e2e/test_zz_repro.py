"""Scratch reproduction: does a repeatedly redispatched node grow markers?"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fakes import make_writing_dispatch

from orchestrator import gitops
from orchestrator.dispatch import Report
from orchestrator.lifecycle import Resume, run_repo_task
from orchestrator.registry import Registry
from orchestrator.workspace import Workspace, normalize_repo


def _markers(repo: Path, branch: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s", f"origin/main..{branch}"],
        text=True, capture_output=True, check=True).stdout
    return [line for line in out.splitlines() if "(incomplete step)" in line]


def _idle_dispatch(persona, task, *, project_dir, **_):
    """A worker that burns its turn cap without producing anything."""
    return Report(persona, 1, False, True, 30, [], {}, {}, "")


def test_repeated_redispatch_marker_growth(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    workspace = Workspace(
        tmp_path / "wt",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )
    result = run_repo_task(
        str(origin), "Preserve partial work.", "engineer", workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"])
    assert isinstance(result.resume, Resume), result.detail
    branch = result.branch
    clone = workspace.clone_dir(normalize_repo(str(origin)))
    print("\nROUND 1 markers:", len(_markers(clone, branch)))

    for round_number in range(2, 5):
        result = run_repo_task(
            str(origin), "Preserve partial work.", "engineer", workspace=workspace,
            dispatch_fn=make_writing_dispatch(
                filename=f"partial-{round_number}.txt", completed=False),
            verify_cmd=["true"], resume=result.resume)
        marks = _markers(clone, branch)
        print(f"ROUND {round_number}: outcome={result.outcome} resume={result.resume is not None} "
              f"markers={len(marks)}")
        if result.resume is None:
            break
