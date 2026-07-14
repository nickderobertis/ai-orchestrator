"""E2E: drive the real repo lifecycle end to end against a real bare git origin.

Git is real throughout (clone → worktree → branch → commit → push → merge on a
local bare repo). Only the two external seams are faked: the paid harness
(`writing_dispatch`, which makes a real edit) and GitHub's PR/CI decisioning
(`FakeGitHub`, which performs the merge with real git). So these prove the whole
journey — local direct-merge and the GitHub PR+auto-merge path — for real.
"""

# llmlint: ignore-file[e2e_not_mocked] the repo-lifecycle e2e fakes ONLY the paid harness
# (the dispatch_fn seam) and GitHub's PR/CI decisioning while driving real git and the
# real merge, exactly as AGENTS.md prescribes for lifecycle tests; the real onejudge
# dispatch boundary is covered separately in test_dispatch_e2e.py.

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

from fakes import FakeGitHub, make_writing_dispatch

from orchestrator import gitops
from orchestrator.dispatch import Report
from orchestrator.github import PullRequest
from orchestrator.lifecycle import (
    RepoPlan,
    RepoPlanNode,
    Step,
    main_plan,
    run_repo_plan,
    run_repo_task,
)
from orchestrator.merge import GitHubMergeStrategy
from orchestrator.next_round import main as next_round_main
from orchestrator.workspace import Workspace, normalize_repo


def _workspace(tmp_path: Path, *origins: Path) -> Workspace:
    """Build a registry-like resolver over real canonical test checkouts."""
    checkouts = {
        str(origin.resolve()): gitops.clone(origin, tmp_path / f"canonical-{index}")
        for index, origin in enumerate(origins)
    }
    return Workspace(
        tmp_path / "worktrees",
        resolver=lambda spec: checkouts[str(Path(spec).resolve())],
    )


def _per_step_dispatch(fail_step: str | None = None):
    """A dispatch_fn that writes a file named after the step id (from the session)."""

    def dispatch_fn(
        persona: str, task: str, *, project_dir: str, session: str, **_: object
    ) -> Report:
        sid = session.rsplit(":", 1)[-1]
        completed = sid != fail_step
        if completed:
            (Path(project_dir) / f"{sid}.txt").write_text(f"{persona}\n", encoding="utf-8")
        return Report(persona, 0 if completed else 1, completed, False, 2, [], {}, {}, "")

    return dispatch_fn


def _has_file(origin: Path, ref: str, path: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(origin), "cat-file", "-e", f"{ref}:{path}"],
            capture_output=True,
        ).returncode
        == 0
    )


def _tip(origin: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "-C", str(origin), "rev-parse", ref], text=True, capture_output=True
    ).stdout.strip()


def test_repo_plan_ledger_and_guided_next_round(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """The real CLI + onejudge + git lifecycle records and retries an unresolved node."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-ledger")
    runs_dir = tmp_path / "runs"
    plan_path = tmp_path / "repo-plan.json"
    first_plan = {
        "name": "ledger e2e",
        "tasks": [
            {
                "id": "change",
                "repo": str(canonical),
                "persona": "backend-engineer",
                "task": "should-fail write-change: preserve this partial attempt",
                "skip_verify": True,
            }
        ],
    }
    plan_path.write_text(json.dumps(first_plan), encoding="utf-8")
    common = [
        "--base",
        str(command_base()),
        "--persona-dir",
        str(personas_dir),
        "--workspace",
        str(tmp_path / "workspace"),
        "--format",
        "json",
    ]
    rc = main_plan([str(plan_path), "--run", "fixed-run", "--runs-dir", str(runs_dir), *common])
    captured = capsys.readouterr()
    assert rc == 1 and json.loads(captured.out)["results"]["change"]["status"] == "failed"
    first = runs_dir / "fixed-run" / "round-01"
    assert json.loads((first / "plan.json").read_text()) == first_plan
    assert (first / "result.json").is_file()
    assert "just next-round fixed-run [edits.json]" in captured.err

    edits = tmp_path / "edits.json"
    edits.write_text(
        json.dumps({"retry": {"change": {"task": "complete-now write-change: finish"}}}),
        encoding="utf-8",
    )
    rc = next_round_main(["fixed-run", str(edits), "--runs-dir", str(runs_dir), *common])
    captured = capsys.readouterr()
    second = runs_dir / "fixed-run" / "round-02"
    assert rc == 0 and (second / "plan.json").is_file() and (second / "result.json").is_file()
    assert json.loads((second / "result.json").read_text())["results"]["change"]["status"] == "done"
    assert "nothing to iterate" in captured.err


# --- local repo: direct merge into main after checks -----------------------


def test_local_repo_direct_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),  # a local path → local direct-merge strategy is auto-selected
        "Add a change file.",
        "backend-engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],  # explicit gate so the test never depends on make/just
    )
    assert result.ok
    assert result.outcome == "merged"
    assert result.base_branch == "main"
    assert _has_file(origin, "main", "feature.txt")  # the change really landed on origin main


def test_local_repo_gate_failure_blocks_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    result = run_repo_task(
        str(origin),
        "Add a change that fails the gate.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["false"],  # gate fails → never pushes or merges
    )
    assert not result.ok
    assert result.outcome == "gate-failed"
    assert result.pr is None
    assert _tip(origin, "main") == before  # origin main untouched


def test_local_repo_no_gate_detected_proceeds(tmp_path, bare_origin) -> None:
    # The seed repo has no recognized gate; with no explicit verify_cmd the
    # lifecycle notes that and relies on downstream checks, still merging.
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "Add a change with no detectable local gate.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        # no verify_cmd → detect_gate finds nothing (only README in the repo)
    )
    assert result.ok and result.outcome == "merged"
    assert "no local gate" in result.detail or result.verify is None
    assert _has_file(origin, "main", "feature.txt")


def test_skip_verify_bypasses_the_gate(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "Add a change with the gate skipped.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        skip_verify=True,  # no local gate runs at all
        verify_cmd=["false"],  # would fail if it ran — proving it is skipped
    )
    assert result.ok and result.outcome == "merged"
    assert result.verify is None
    assert _has_file(origin, "main", "feature.txt")


def test_agent_not_completed_stops_early(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),
        "Task the agent will not finish.",
        "backend-engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"],
    )
    assert result.outcome == "not-completed"
    assert not result.ok
    assert f"committed to branch '{result.branch}'" in result.detail

    clone = ws.clone_dir(normalize_repo(str(origin)))
    assert _has_file(clone, result.branch, "partial.txt")
    subject = subprocess.run(
        ["git", "-C", str(clone), "log", "-1", "--format=%s", result.branch],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert subject.startswith("wip:") and "incomplete step" in subject
    assert not _has_file(origin, "main", "partial.txt")
    assert not _has_file(origin, result.branch, "partial.txt")  # incomplete work is not pushed


def test_no_changes_produces_no_pr(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "A task the agent completes without editing anything.",
        "reviewer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename=None),
        verify_cmd=["true"],
    )
    assert result.outcome == "no-changes"
    assert not result.ok


# --- GitHub repo: PR + auto-merge on required checks -----------------------


def test_github_auto_merge_on_required_checks(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(origin, required=("ci",))
    result = run_repo_task(
        "acme/widget",  # a GitHub-style slug → GitHub strategy
        "Add a feature file.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),  # but clone/push the real bare repo
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        sleep=lambda _: None,
    )
    assert result.ok
    assert result.outcome == "merged"
    assert result.pr is not None and result.pr.number == 1
    assert _has_file(origin, "main", "feature.txt")


def test_github_second_run_reuses_open_pr_and_merges(tmp_path, bare_origin) -> None:
    class FindOrCreateFakeGitHub(FakeGitHub):
        def create_pr(
            self, repo: str, *, head: str, base: str, title: str, body: str
        ) -> PullRequest:
            for number, state in self._prs.items():
                if state["head"] == head and not state["merged"]:
                    return PullRequest(
                        number=number,
                        url=f"https://github.com/{repo}/pull/{number}",
                        repo=repo,
                        head=head,
                        base=base,
                    )
            return super().create_pr(repo, head=head, base=base, title=title, body=body)

    origin = bare_origin()
    github = FindOrCreateFakeGitHub(origin)
    workspace = _workspace(tmp_path, origin)
    branch = "orchestrator/continue-pr"
    first = run_repo_task(
        "acme/widget",
        "Start a feature.",
        "backend-engineer",
        workspace=workspace,
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        branch=branch,
        dispatch_fn=make_writing_dispatch(filename="first.txt"),
        verify_cmd=["true"],
        merge_policy="none",
    )
    assert first.outcome == "pr-open"
    assert first.pr is not None

    continue_dispatch = make_writing_dispatch(filename="second.txt")

    def resume_open_pr(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        subprocess.run(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=project_dir,
            check=True,
            capture_output=True,
        )
        return cast(Report, continue_dispatch(persona, task, project_dir=project_dir, **kwargs))

    second = run_repo_task(
        "acme/widget",
        "Continue the feature.",
        "backend-engineer",
        workspace=workspace,
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        branch=branch,
        dispatch_fn=resume_open_pr,
        verify_cmd=["true"],
        sleep=lambda _: None,
    )
    assert second.ok and second.outcome == "merged"
    assert second.pr is not None and second.pr.number == first.pr.number
    assert github._n == 1
    assert _has_file(origin, "main", "first.txt")
    assert _has_file(origin, "main", "second.txt")


def test_github_auto_merge_unavailable_falls_back_to_direct(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(origin, auto_available=False)  # repo forbids native auto-merge
    result = run_repo_task(
        "acme/widget",
        "Add a feature file.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        sleep=lambda _: None,
    )
    assert result.ok and result.outcome == "merged"
    assert _has_file(origin, "main", "feature.txt")


def test_github_required_check_failure_blocks_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, fail_checks=True)
    result = run_repo_task(
        "acme/widget",
        "Add a feature file.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        sleep=lambda _: None,
        timeout=5.0,
    )
    assert not result.ok
    assert result.outcome == "checks-failed"
    assert _tip(origin, "main") == before  # required check failed → nothing merged


# --- multi-PR: one larger task across coordinated PRs ----------------------


def test_multi_pr_dag_across_repos(tmp_path, bare_origin) -> None:
    repo_x = bare_origin()
    repo_y = bare_origin()
    ws = _workspace(tmp_path, repo_x, repo_y)

    def runner(node: RepoPlanNode):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=ws,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["true"],
        )

    plan = RepoPlan(
        tasks=[
            RepoPlanNode("a", str(repo_x), "backend-engineer", "part A on repo X"),
            RepoPlanNode("b", str(repo_y), "backend-engineer", "part B on repo Y"),
            RepoPlanNode("c", str(repo_x), "reviewer", "part C on repo X", deps=["a"]),
        ],
        concurrency=3,
    )
    result = run_repo_plan(plan, runner)

    assert result.ok
    assert all(r.status == "done" for r in result.results.values())
    order = result.started_order
    assert order.index("a") < order.index("c")  # dependent waits for its dep
    # Each PR's change landed on its repo's main; c branched off a's merged base.
    assert _has_file(repo_x, "main", "a.txt")
    assert _has_file(repo_x, "main", "c.txt")
    assert _has_file(repo_y, "main", "b.txt")


# --- workstream: several onejudge on ONE PR -------------------------------


def test_workstream_multiple_onejudge_one_pr(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    steps = [
        Step("impl", "backend-engineer", "implement the feature"),
        Step("test", "test-engineer", "add tests", deps=["impl"]),
        Step("docs", "docs-writer", "document it", deps=["impl"]),
    ]
    result = run_repo_task(
        str(origin),
        workspace=_workspace(tmp_path, origin),
        steps=steps,
        dispatch_fn=_per_step_dispatch(),
        verify_cmd=["true"],
    )
    assert result.ok and result.outcome == "merged"
    assert len(result.steps) == 3 and all(s.status == "done" for s in result.steps)
    # All three steps' changes landed on origin main via ONE merge of ONE branch.
    for sid in ("impl", "test", "docs"):
        assert _has_file(origin, "main", f"{sid}.txt")


def test_workstream_step_failure_stops_and_skips_dependents(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    steps = [
        Step("impl", "backend-engineer", "implement"),
        Step("test", "test-engineer", "add tests", deps=["impl"]),
    ]
    result = run_repo_task(
        str(origin),
        workspace=_workspace(tmp_path, origin),
        steps=steps,
        dispatch_fn=_per_step_dispatch(fail_step="impl"),  # first step hits the turn cap
        verify_cmd=["true"],
    )
    assert not result.ok and result.outcome == "not-completed"
    by_id = {s.id: s.status for s in result.steps}
    assert by_id["impl"] == "not-completed" and by_id["test"] == "skipped"
    assert _tip(origin, "main") == before  # nothing pushed or merged


def test_multi_pr_failure_skips_dependents(tmp_path, bare_origin) -> None:
    repo_x = bare_origin()
    ws = _workspace(tmp_path, repo_x)

    def runner(node: RepoPlanNode):
        # node 'a' fails its gate; 'b' depends on it and must be skipped.
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=ws,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["false"] if node.id == "a" else ["true"],
        )

    plan = RepoPlan(
        tasks=[
            RepoPlanNode("a", str(repo_x), "backend-engineer", "failing part"),
            RepoPlanNode("b", str(repo_x), "reviewer", "dependent part", deps=["a"]),
        ],
        concurrency=2,
    )
    result = run_repo_plan(plan, runner)
    assert not result.ok
    assert result.results["a"].status == "failed"
    assert result.results["b"].status == "skipped"
