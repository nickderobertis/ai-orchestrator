"""E2E: drive the real repo lifecycle end to end against a real bare git origin.

Git is real throughout (clone → worktree → branch → commit → push → merge on a
local bare repo). Only the two external seams are faked: the paid harness
(`writing_dispatch`, which makes a real edit) and GitHub's PR/CI decisioning
(`FakeGitHub`, which performs the merge with real git). So these prove the whole
journey — local direct-merge and the GitHub PR+auto-merge path — for real.
"""

# llmlint: ignore-file[e2e_not_mocked,tests_mirror_real_usage] these repo-lifecycle
# e2es fake ONLY the paid harness
# (the dispatch_fn seam) and GitHub's PR/CI decisioning while driving real git and the
# real merge, exactly as AGENTS.md prescribes for lifecycle tests; the real onejudge
# dispatch boundary is covered separately in test_dispatch_e2e.py.

from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from pathlib import Path
from typing import TypeVar, cast

import pytest
from conftest import git, install_pre_push_hook
from fakes import FakeGitHub, FakePRState, make_writing_dispatch
from git_http import serve_github_origin
from rendezvous import Rendezvous
from telemetry_contract import clipped_share_seconds
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

import orchestrator.graph as graph_module
import orchestrator.lifecycle as lifecycle_module
from orchestrator import gitops
from orchestrator.config import ConfigError
from orchestrator.coordination import LockTimeout, advisory_lock, git_lock_identity
from orchestrator.dispatch import (
    DispatchError,
    Report,
    classify_provider_failure,
    scoped_session,
)
from orchestrator.github import CliGitHubBackend, GitHubError, PullRequest
from orchestrator.graph import graph_payload, parse_graph, run_graph
from orchestrator.harnesses import JUDGE_HARNESS_ENV, WORKER_HARNESS_ENV
from orchestrator.journal import NodeJournal, NodeSink, open_journal
from orchestrator.labels import parse_labels
from orchestrator.lifecycle import (
    AI_ORCHESTRATOR_IDENTITY,
    MAX_AUTOMATIC_STEP_RESUMES,
    MAX_EMPTY_DEATH_RELAUNCHES,
    MAX_MERGE_CONFLICT_RESOLUTIONS,
    RELAUNCH_BACKOFF_SECONDS,
    RepoPlan,
    RepoPlanNode,
    Resume,
    StackBase,
    Step,
    load_repo_plan,
    main_plan,
    result_payload,
    run_repo_plan,
    run_repo_task,
)
from orchestrator.merge import GitHubMergeStrategy
from orchestrator.merge_queue import merge_queue_turn
from orchestrator.next_round import main as next_round_main
from orchestrator.provenance import (
    INCOMPLETE_TRAILER,
    PR_BASE_TRAILER,
    RECOVERY_TRAILER,
    format_preserved_step_metadata,
    incomplete_commits,
)
from orchestrator.provider_health import failure_rollups
from orchestrator.recover import recover_repo
from orchestrator.registry import Registry, RegistryEntry, RegistryError, Slug
from orchestrator.replan import MAX_AUTOMATIC_ROUND_RESUMES, next_round
from orchestrator.runs import NodeId, RunId, prepare_round, write_result
from orchestrator.workspace import IdentityKey, Workspace, normalize_repo

_T = TypeVar("_T")

# The cap a journey names when it needs a step that *exhausts* its budget.
#
# A `should-fail` step never completes, so it spends every turn it is given and is
# then automatically resumed `MAX_AUTOMATIC_STEP_RESUMES` more times. Each turn is
# two provider processes (respond, then supervisor) and each segment ends in one
# `assess`, so one such dispatch spawns `3 * (2 * cap + 1)` of them. At the
# lifecycle default of `DEFAULT_LIFECYCLE_STEP_MAX_TURNS` (24) that is 147
# processes and roughly twelve measured seconds — per dispatch, and these journeys
# drive up to four each.
#
# None of them is about how *high* the cap is; they need a step that reaches
# whatever cap it was given. The default's own height is pinned for free by
# `test_run_repo_task_journals_a_step_that_hit_the_turn_cap` in
# `tests/test_lifecycle_unit.py`, against an injected dispatch function. So name
# the smallest cap that still runs both sides of the loop — the agent takes a
# turn, the supervisor declines to release it, the agent takes its last — and the
# same 3-segment exhaustion costs 15 processes instead of 147.
EXHAUSTED_STEP_MAX_TURNS = 2


def _one_node_lifecycle_plan(
    tmp_path: Path, repo: Path, *, task: str, name: str = "one-node", **node: object
) -> Path:
    """Write the plan file one lifecycle dispatch is expressed as.

    The removed `just repo-task` took these as positionals and flags; the tracked
    graph is now the only executor, so a single workstream is a one-node plan.
    """
    plan = tmp_path / f"{name}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 6,
                "tasks": [
                    {"id": "solo", "repo": str(repo), "persona": "engineer", "task": task} | node
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


def _workspace(tmp_path: Path, *origins: Path, workflow: str = "local") -> Workspace:
    """Build a registry-like resolver over real canonical test checkouts."""
    checkouts = {
        str(origin.resolve()): gitops.clone(origin, tmp_path / f"canonical-{index}")
        for index, origin in enumerate(origins)
    }
    return Workspace(
        tmp_path / "worktrees",
        resolver=lambda spec: checkouts[str(Path(spec).resolve())],
        workflow=workflow,
    )


def _shared_checkout(root: Path, index: int = 0) -> Path:
    """The checkout `_workspace` resolves to, before any run clone exists.

    A run clone borrows its objects *and* its hooks path from this checkout, so a
    `pre-push` installed here is the one Git runs for every publishing push. It has
    to be addressed directly: `clone_dir` is the run's own clone and does not exist
    until the lifecycle resolves the repository.
    """
    return root / f"canonical-{index}"


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


def _directory_scoped_session_dispatch(store: dict[str, str]):
    """A dispatch_fn that enforces the harness's directory-scoped session store.

    oneharness records ``session name -> harness conversation token`` in a store
    shared by every run, and the harness itself files that conversation under the
    working directory that created it. Resuming a recorded name from a *different*
    directory therefore fails before the first turn — the agent process exits, its
    wrapper parks, and the dispatcher can only report ``worker-died``. That is the
    single rule this double enforces at the paid-harness seam; everything else here
    (git, the worktree, the branch, the merge) is real.
    """

    def dispatch_fn(
        persona: str, task: str, *, project_dir: str, session: str, **_: object
    ) -> Report:
        recorded = store.setdefault(session, project_dir)
        if recorded != project_dir:
            return Report(
                persona,
                1,
                False,
                True,
                0,
                [],
                {},
                None,
                (
                    f"worker-died: tracked worker exited or stopped heartbeating "
                    f"(watchdog pid 0, agent exit status 1): session {session!r} was "
                    f"recorded under {recorded} and cannot resume in {project_dir}"
                ),
                outcome="worker-died",
            )
        if persona == "pr-author":
            output = task.split(
                "Write the final body, and nothing else, to this absolute path:\n", 1
            )[1].splitlines()[0]
            Path(output).write_text(
                "## What\nContinues the pinned branch.\n\n## Why\nIt carries prior work.\n",
                encoding="utf-8",
            )
            return Report(persona, 0, True, False, 1, [], {}, {}, "")
        sid = session.rsplit(":", 1)[-1]
        (Path(project_dir) / f"{sid}.txt").write_text(f"{persona}\n", encoding="utf-8")
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    return dispatch_fn


def _preserve_branch_with_commits(canonical: Path, branch: str, marker: str) -> None:
    """Leave ``branch`` in the execution checkout carrying committed prior work.

    A merged workstream leaves its published branch behind on origin; a *preserved*
    one never reached it. Withdraw any published copy first so the branch this
    seeds is the never-published kind a later run is asked to pin.
    """
    subprocess.run(
        ["git", "-C", str(canonical), "push", "origin", "--delete", branch],
        capture_output=True,
        check=False,
    )
    git("fetch", "--prune", "origin", cwd=canonical)
    git("branch", "-f", branch, "origin/main", cwd=canonical)
    worktree = canonical.parent / f"preserve-{marker}"
    git("worktree", "add", "--quiet", str(worktree), branch, cwd=canonical)
    (worktree / f"PRIOR_{marker}.md").write_text(f"prior work {marker}\n", encoding="utf-8")
    git("add", "-A", cwd=worktree)
    git("commit", "-m", f"wip: prior partial work {marker}", cwd=worktree)
    git("worktree", "remove", "--force", str(worktree), cwd=canonical)


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


def _subject(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%s", ref],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _run_while_merge_turn_is_held(
    canonical: Path, operation: Callable[[], _T], *, should_wait: bool
) -> _T:
    acquired = threading.Event()
    release = threading.Event()

    def hold_turn() -> None:
        with merge_queue_turn(git_lock_identity(gitops.common_dir(canonical))):
            acquired.set()
            release.wait(e2e_timeout(10))

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_turn)
        assert acquired.wait(e2e_timeout(5))
        publication = pool.submit(operation)
        try:
            if should_wait:
                with pytest.raises(FutureTimeout):
                    publication.result(timeout=0.2)
                release.set()
            result = publication.result(timeout=e2e_timeout(10))
        finally:
            release.set()
            holder.result(timeout=e2e_timeout(10))
    return result


def test_pinned_branch_redispatch_reaches_its_agent_turn(
    tmp_path, bare_origin, personas_dir
) -> None:
    """Re-pinning a branch that already carries commits must still run its agent.

    Resuming preserved work and recovering it are both exactly this: a second run
    told to use a branch name a previous run already dispatched. The two runs cut
    that branch a worktree under their own run roots, so a session named only after
    the branch names a conversation the harness filed under a directory that is
    gone — and the worker dies before its first turn with nothing to show for it.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-pinned")
    root = tmp_path / "pinned-worktrees"
    branch = "ai-orchestrator/engineer/pinned-existing"
    sessions: dict[str, str] = {}

    def dispatch_pinned(marker: str) -> lifecycle_module.LifecycleResult:
        _preserve_branch_with_commits(canonical, branch, marker)
        return run_repo_task(
            str(origin),
            f"complete-now write-change {marker}",
            "engineer",
            workspace=Workspace(
                root,
                resolver=lambda _spec: canonical,
                workflow="local",
                repo_type="single-owner",
            ),
            branch=branch,
            persona_dir=personas_dir,
            recorded_gate=["true"],
            dispatch_fn=_directory_scoped_session_dispatch(sessions),
        )

    first = dispatch_pinned("first")
    assert first.outcome == "merged", first.detail
    second = dispatch_pinned("second")
    assert second.outcome == "merged", second.detail

    assert len(sessions) == 2, sessions
    assert {session.rsplit(":", 1)[-1] for session in sessions} == {"main"}
    assert all(session.startswith(branch) for session in sessions), sessions
    assert len(set(sessions.values())) == 2, sessions
    assert _has_file(origin, "main", "PRIOR_first.md")
    assert _has_file(origin, "main", "PRIOR_second.md")


def test_pinned_branch_redispatch_redrafts_its_pr_body(tmp_path, bare_origin, personas_dir) -> None:
    """The PR-author dispatch of a re-pinned branch reaches its turn too.

    Drafting runs in the same per-run worktree the worker did, under a session the
    lifecycle names for itself rather than for the branch — so a constant name is
    the same trap one directory later. Its failure is swallowed by the deliberate
    fallback body, which is exactly why it needs its own assertion.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-pinned-remote")
    root = tmp_path / "pinned-remote-worktrees"
    branch = "ai-orchestrator/engineer/pinned-remote"
    sessions: dict[str, str] = {}
    github = FakeGitHub(origin)

    def dispatch_pinned(marker: str) -> lifecycle_module.LifecycleResult:
        _preserve_branch_with_commits(canonical, branch, marker)
        return run_repo_task(
            str(origin),
            f"complete-now write-change {marker}",
            "engineer",
            workspace=Workspace(
                root,
                resolver=lambda _spec: canonical,
                workflow="remote",
                repo_type="single-owner",
            ),
            branch=branch,
            persona_dir=personas_dir,
            recorded_gate=["true"],
            workflow="remote",
            repo_type="single-owner",
            merge_policy="none",
            github=github,
            dispatch_fn=_directory_scoped_session_dispatch(sessions),
        )

    first = dispatch_pinned("first")
    assert first.outcome == "pr-open", first.detail
    second = dispatch_pinned("second")
    assert second.outcome == "pr-open", second.detail

    drafting = sorted(name for name in sessions if name.startswith("pr-author"))
    assert len(drafting) == 2, sessions
    assert len({sessions[name] for name in drafting}) == 2, sessions
    for result in (first, second):
        assert result.pr is not None
        assert "Continues the pinned branch." in github._prs[result.pr.number].body


def test_published_dispatch_survives_deferred_teardown_and_redispatch_reclaims_it(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """A real merged lifecycle self-heals after teardown leaves its worktree registered."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    root = tmp_path / "worktrees"

    class ContendedTeardownWorkspace(Workspace):
        def remove_worktree(self, repo, path) -> None:
            self._release_worktree_lease(path)
            raise LockTimeout("shared .git remains busy")

    contended = ContendedTeardownWorkspace(
        root, resolver=lambda _spec: canonical, workflow="local", repo_type="single-owner"
    )
    journal = open_journal(tmp_path / "run", RunId("teardown"), 1)
    scope = NodeJournal(journal, NodeId("publish"), RunId("teardown"), 1)
    first = run_repo_task(
        str(origin),
        "complete-now write-unique-change first",
        "engineer",
        workspace=contended,
        branch="teardown-retry",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
        journal=scope,
    )

    assert first.outcome == "merged", first.detail
    assert first.deferred_cleanup and "remove-worktree deferred" in first.deferred_cleanup[0]
    serialized = json.loads(json.dumps(result_payload(first)))
    assert serialized["outcome"] == "merged"
    assert serialized["deferred_cleanup"] == first.deferred_cleanup
    deferred = [event for event in journal.events() if event.kind == "cleanup-deferred"]
    assert deferred and deferred[0].detail["operation"] == "remove-worktree"
    orphan = gitops.worktrees(contended.clone_dir(normalize_repo(str(origin))))["teardown-retry"]
    assert orphan.exists()

    recovered = Workspace(
        root,
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
        run_token=contended.run_token,
    )
    second = run_repo_task(
        str(origin),
        "complete-now write-change second",
        "engineer",
        workspace=recovered,
        branch="teardown-retry",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert second.outcome == "merged", second.detail


def test_redispatch_reclaims_a_worktree_a_killed_worker_left_dirty(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """A re-dispatch clears the debris of a killed worker instead of refusing it.

    A worker that dies mid-run leaves a real worktree with a build cache in it and a
    ``.git`` pointer git will no longer honour, so ``git worktree remove`` answers
    "is not a working tree" and the plain directory stays. That used to end every
    later attempt at the same branch until an operator cleared the path by hand.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-killed")
    root = tmp_path / "killed-worktrees"
    ref = normalize_repo(str(origin))
    killed = Workspace(
        root, resolver=lambda _spec: canonical, workflow="local", repo_type="single-owner"
    )
    killed.ensure_clone(ref)
    abandoned = killed.worktree(ref, "killed-worker", base="origin/main")
    (abandoned / "node_modules" / ".cache").mkdir(parents=True)
    (abandoned / "node_modules" / ".cache" / "daemon.log").write_text("stale\n", encoding="utf-8")
    (abandoned / ".git").unlink()
    killed._release_worktree_lease(abandoned)

    resumed = Workspace(
        root,
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
        run_token=killed.run_token,
    )
    result = run_repo_task(
        str(origin),
        "complete-now write-change reclaimed after a killed worker",
        "engineer",
        workspace=resumed,
        branch="killed-worker",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )

    assert result.outcome == "merged", result.detail
    assert not (abandoned / "node_modules").exists()


def test_an_untracked_workstream_labels_the_sessions_its_dispatches_produce(
    tmp_path, bare_origin, command_base, personas_dir, monkeypatch
) -> None:
    """An untracked workstream's sessions still name the work they did.

    A `run_repo_task` reached outside a tracked round belongs to no graph, and for
    that reason used to hand its
    dispatches no history labels at all — so every session it produced joined to
    nothing, and the telemetry that counts a node's turns could see the sessions and
    attribute none of them. The labels are read where oneharness reads them: the
    environment of the provider process the dispatch actually ran.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-untracked")
    observed = tmp_path / "dispatch-labels.txt"
    monkeypatch.setenv("FAKE_BACKEND_LABELS", str(observed))

    result = run_repo_task(
        str(origin),
        "complete-now write-change from an untracked workstream",
        "engineer",
        workspace=Workspace(
            tmp_path / "untracked-worktrees",
            resolver=lambda _spec: canonical,
            workflow="local",
            repo_type="single-owner",
        ),
        branch="untracked-labels",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )

    assert result.outcome == "merged", result.detail
    labelled = [
        parse_labels(line)
        for line in observed.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert labelled, "the dispatch never reached a provider"
    # Every session of this workstream names one run and one node, and the same ones,
    # so a reader can gather them — and the synthetic run says plainly that it is not
    # a recorded run directory.
    runs = {labels.get("run_id") for labels in labelled}
    assert len(runs) == 1
    run_id = runs.pop()
    assert run_id is not None and run_id.startswith("repo-task-")
    assert {labels.get("node") for labels in labelled} == {"repo-task"}
    # And the semantic role rides along, so a judge session is not counted as worker
    # turns just because it inherited the worker's environment.
    assert {labels.get("persona") for labels in labelled} == {"engineer"}


def test_lifecycle_failure_survives_simultaneous_deferred_teardown(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-failed")

    class ContendedTeardownWorkspace(Workspace):
        def remove_worktree(self, repo, path) -> None:
            self._release_worktree_lease(path)
            raise LockTimeout("shared .git remains busy")

    workspace = ContendedTeardownWorkspace(
        tmp_path / "failed-worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )
    result = run_repo_task(
        str(origin),
        "should-fail write-change preserve original failure",
        "engineer",
        workspace=workspace,
        branch="failed-teardown",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
        max_turns=EXHAUSTED_STEP_MAX_TURNS,
    )

    assert result.outcome == "not-completed"
    assert "step 'main' hit the turn cap" in result.detail
    assert result.deferred_cleanup and "remove-worktree deferred" in result.deferred_cleanup[0]
    ref = normalize_repo(str(origin))
    cleanup = Workspace(
        tmp_path / "failed-worktrees",
        resolver=lambda _spec: canonical,
        run_token=workspace.run_token,
    )
    cleanup.ensure_clone(ref)
    cleanup.remove_worktree(ref, gitops.worktrees(cleanup.clone_dir(ref))["failed-teardown"])


def test_turn_cap_auto_resumes_preserved_branch_without_rerunning_completed_steps(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """A real onejudge cap continues its preserved workstream in place."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-auto-resume")
    prepare_runs = tmp_path / "prepare-runs.log"
    implement_runs = tmp_path / "implement-runs.log"

    result = run_repo_task(
        str(origin),
        workspace=Workspace(
            tmp_path / "auto-resume-worktrees",
            resolver=lambda _spec: canonical,
            workflow="local",
            repo_type="single-owner",
        ),
        steps=[
            Step(
                "prepare",
                "engineer",
                f"complete-after-13 write-change\nrecord-run={prepare_runs}",
            ),
            Step(
                "implement",
                "engineer",
                f"complete-now resume-after-cap write-change\nrecord-run={implement_runs}",
                deps=["prepare"],
                max_turns=1,
            ),
        ],
        branch="feature/automatic-turn-cap-resume",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )

    assert result.outcome == "merged", result.detail
    assert prepare_runs.read_text(encoding="utf-8").splitlines() == ["run"] * 13
    assert implement_runs.read_text(encoding="utf-8").splitlines() == ["run", "run"]
    assert _has_file(origin, "main", ".fake-turn-cap-preserved")
    assert result.retry_lineage is not None
    assert result.retry_lineage.disposition == "recovered"


def _launch_death(persona: str) -> Report:
    """The report a dispatch that died before its first turn actually produces.

    The wrapper parks the child's exit disposition and stderr, the watchdog sees an
    empty process tree, and `dispatch` reports `worker-died` with nothing else: no
    turns, no verdicts, no usage. Provider throttling, an out-of-quota harness, and
    an OOM kill all reach the lifecycle in exactly this shape.
    """
    return Report(
        persona,
        1,
        False,
        True,
        0,
        [],
        {},
        None,
        "worker-died (watchdog pid 0, agent exit status 1): tracked worker exited "
        "or stopped heartbeating: agent harness exited 1: HTTP 429 You have hit "
        "your session limit - resets 1pm",
        outcome="worker-died",
        # The harness exited of its own accord rather than being signalled, which is
        # what the wrapper records for a refusal to start.
        agent_exit_status=1,
    )


def test_a_dispatch_that_died_before_its_work_is_relaunched_and_publishes(
    tmp_path, bare_origin
) -> None:
    """A launch that never reached the task is retried, not counted against it.

    The old loop only continued when the branch already carried committed work, so
    a launch failure on a fresh branch failed the node on its first death — with
    nothing about the task having been attempted, let alone failed.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-relaunched")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    launches: list[str] = []

    def dying_then_working(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        launches.append(persona)
        if len(launches) == 1:
            return _launch_death(persona)
        worktree = Path(project_dir)
        (worktree / "cost-report.txt").write_text("measured\n", encoding="utf-8")
        gitops.add_all(worktree)
        gitops.commit(worktree, "feat: report lifecycle cost")
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    waits: list[float] = []
    result = run_repo_task(
        str(canonical),
        "## What\nReport lifecycle cost.\n\n## Why\nNobody can see what a run spends.\n",
        "engineer",
        workspace=Workspace(tmp_path / "relaunched-worktrees"),
        branch="feature/relaunched-after-launch-death",
        dispatch_fn=dying_then_working,
        recorded_gate=["true"],
        sleep=waits.append,
    )

    assert result.outcome == "merged", result.detail
    assert launches == ["engineer", "engineer"]
    # The relaunch waited rather than asking the same refusing provider at once.
    assert waits == [RELAUNCH_BACKOFF_SECONDS]
    assert _has_file(origin, "main", "cost-report.txt")


def test_an_empty_death_after_committed_work_relaunches_onto_the_preserved_branch(
    tmp_path, bare_origin
) -> None:
    """The death that lands mid-workstream, not on its first dispatch.

    By then the branch carries committed work, so the relaunch has something to
    orphan if it cuts a fresh branch — and something to finish if it does not. This
    is the shape that lost roughly ten minutes of committed work to a provider
    outage: the earlier attempt's commits must still be on the branch the next
    launch resumes, and must reach the base with it.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-death-mid-workstream")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    launches: list[str] = []
    waits: list[float] = []

    def working_then_dying_then_finishing(
        persona: str, task: str, *, project_dir: str, **_: object
    ) -> Report:
        launches.append(persona)
        worktree = Path(project_dir)
        if len(launches) == 1:
            (worktree / "partial.txt").write_text("measured so far\n", encoding="utf-8")
            gitops.add_all(worktree)
            gitops.commit(worktree, "chore: measure part of the lifecycle cost")
            return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)
        if len(launches) == 2:
            return _launch_death(persona)
        (worktree / "finished.txt").write_text("measured\n", encoding="utf-8")
        gitops.add_all(worktree)
        gitops.commit(worktree, "feat: finish measuring the lifecycle cost")
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    result = run_repo_task(
        str(canonical),
        "## What\nMeasure lifecycle cost.\n\n## Why\nA run's spend is invisible.\n",
        "engineer",
        workspace=Workspace(tmp_path / "death-mid-workstream-worktrees"),
        branch="feature/death-after-committed-work",
        dispatch_fn=working_then_dying_then_finishing,
        recorded_gate=["true"],
        sleep=waits.append,
    )

    assert result.outcome == "merged", result.detail
    assert launches == ["engineer"] * 3, launches
    # One relaunch, on the launch budget — the work stop before it took its own.
    assert waits == [RELAUNCH_BACKOFF_SECONDS]
    # The relaunch continued the preserved branch rather than cutting a fresh one, so
    # nothing the earlier attempt committed was orphaned by the death: both its work
    # and the work that finished afterwards reach the base together.
    assert _has_file(origin, "main", "partial.txt")
    assert _has_file(origin, "main", "finished.txt")


@pytest.mark.parametrize(
    ("exit_status", "relaunched"),
    [(1, True), (137, False), (None, False)],
    ids=["exited", "killed-by-signal", "unrecorded"],
)
def test_only_a_harness_that_exited_on_its_own_earns_a_relaunch(
    tmp_path, bare_origin, exit_status: int | None, relaunched: bool
) -> None:
    """A worker terminated after it was running is not a launch that never happened.

    The agent wrapper reads its child's wait status exactly this way — above 128 is
    "killed by signal N", at or below is "exited N" — so a watchdog kill, a round
    cancellation and an OOM are all signalled, while a harness refusing to start
    exits of its own accord. An unrecorded status earns nothing: the relaunch is
    positively earned, never assumed.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"canonical-disposition-{exit_status}")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    launches: list[str] = []

    def dying_with_recorded_disposition(persona: str, task: str, **_: object) -> Report:
        launches.append(persona)
        return replace(_launch_death(persona), agent_exit_status=exit_status)

    result = run_repo_task(
        str(canonical),
        "## What\nTier the workspace.\n\n## Why\nThe suite reruns work it proved.\n",
        "engineer",
        workspace=Workspace(tmp_path / f"disposition-worktrees-{exit_status}"),
        branch=f"feature/disposition-{exit_status}",
        dispatch_fn=dying_with_recorded_disposition,
        recorded_gate=["true"],
        sleep=lambda _seconds: None,
    )

    assert result.outcome == "not-completed"
    expected = 1 + MAX_EMPTY_DEATH_RELAUNCHES if relaunched else 1
    assert len(launches) == expected, launches
    assert ("just smoke" in result.detail) is relaunched, result.detail


def test_launch_deaths_do_not_spend_the_budget_that_carries_work_forward(
    tmp_path, bare_origin
) -> None:
    """The reported harm: an outage consumed the resume budget in under a minute.

    A launch death and a work stop are answered from separate budgets, so a node
    whose first dispatch never reached the provider still gets every automatic
    resume its *work* is entitled to — here one initial attempt plus
    `MAX_AUTOMATIC_STEP_RESUMES`, on top of the relaunch.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-separate-budgets")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    launches: list[str] = []

    def dying_then_failing_at_work(
        persona: str, task: str, *, project_dir: str, **_: object
    ) -> Report:
        launches.append(persona)
        if len(launches) == 1:
            return _launch_death(persona)
        worktree = Path(project_dir)
        (worktree / f"attempt-{len(launches)}.txt").write_text("partial\n", encoding="utf-8")
        gitops.add_all(worktree)
        gitops.commit(worktree, f"chore: partial attempt {len(launches)}")
        return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)

    result = run_repo_task(
        str(canonical),
        "## What\nMeasure lifecycle cost.\n\n## Why\nA run's spend is invisible.\n",
        "engineer",
        workspace=Workspace(tmp_path / "separate-budget-worktrees"),
        branch="feature/separate-launch-and-work-budgets",
        dispatch_fn=dying_then_failing_at_work,
        recorded_gate=["true"],
        sleep=lambda _seconds: None,
    )

    assert result.outcome == "not-completed", result.detail
    assert len(launches) == 1 + 1 + MAX_AUTOMATIC_STEP_RESUMES, launches
    # The work that did get committed is preserved for a retry, as it always was.
    assert result.resume is not None
    assert incomplete_commits(canonical, "origin/main", result.branch)
    # This one failed at its work, so it is reported as work — not as the launch.
    assert "just smoke" not in result.detail


def test_a_workstream_whose_relaunches_all_die_names_the_launch_path(tmp_path, bare_origin) -> None:
    """Relaunching is bounded, and what it reports sends the reader to the probe.

    "workstream did not complete" alone sends a planner looking for a fault in a
    task that was never attempted. The detail says so and names `just smoke`, which
    is the cheap probe for the other explanation.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-launch-outage")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    launches: list[str] = []

    waits: list[float] = []

    def always_dying(persona: str, task: str, **_: object) -> Report:
        launches.append(persona)
        return _launch_death(persona)

    result = run_repo_task(
        str(canonical),
        "## What\nTier the workspace.\n\n## Why\nThe suite reruns work it proved.\n",
        "engineer",
        workspace=Workspace(tmp_path / "launch-outage-worktrees"),
        branch="feature/launch-outage",
        dispatch_fn=always_dying,
        recorded_gate=["true"],
        sleep=waits.append,
    )

    assert result.outcome == "not-completed"
    assert len(launches) == 1 + MAX_EMPTY_DEATH_RELAUNCHES, launches
    # Each relaunch waits longer than the last, rather than at a flat cadence.
    assert waits == [RELAUNCH_BACKOFF_SECONDS * n for n in range(1, MAX_EMPTY_DEATH_RELAUNCHES + 1)]
    assert "died leaving no work behind" in result.detail
    assert "just smoke" in result.detail


class _CancelLandingDuringTheBackoff(threading.Event):
    """A round cancellation that arrives while the relaunch backoff is waiting.

    That window is the whole subject of the test below, and it used to be aimed at
    from outside with a short ``threading.Timer``: a race the timer thread loses
    whenever this host is busy, failing a green tree with no defect present. The
    backoff performs the one ``wait`` on this event, so setting it from inside that
    call puts the cancellation in the window by construction — no thread to starve
    and no wall clock to read. Each wait records the interval it was asked for and
    whether it returned woken (``True``) or expired (``False``).
    """

    def __init__(self) -> None:
        super().__init__()
        self.backoffs: list[float | None] = []
        self.wakes: list[bool] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.backoffs.append(timeout)
        self.set()
        woken = super().wait(timeout)
        self.wakes.append(woken)
        return woken


def test_a_cancelled_round_does_not_wait_out_a_relaunch_backoff(tmp_path, bare_origin) -> None:
    """The round is already closing; the backoff must not hold the branch hostage.

    Under a round the cancellation event *is* the sleep, so a cancel that lands
    mid-backoff wakes it. Waiting the whole interval out first would delay the one
    thing a cancelled workstream still owes: preserving what sits on the branch.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-cancelled-backoff")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    cancel = _CancelLandingDuringTheBackoff()
    launches: list[str] = []

    def dying_under_cancellation(persona: str, task: str, **_: object) -> Report:
        launches.append(persona)
        return _launch_death(persona)

    result = run_repo_task(
        str(canonical),
        "## What\nTier the workspace.\n\n## Why\nThe suite reruns work it proved.\n",
        "engineer",
        workspace=Workspace(tmp_path / "cancelled-backoff-worktrees"),
        branch="feature/cancelled-during-backoff",
        dispatch_fn=dying_under_cancellation,
        recorded_gate=["true"],
        cancel=cancel,
    )

    assert result.outcome == "not-completed", result.detail
    # The backoff was waited on the event rather than slept through, once, for the
    # first relaunch's interval.
    assert cancel.backoffs == [RELAUNCH_BACKOFF_SECONDS]
    # Woken, not expired: the wait returned on the cancellation, so the full
    # interval was never spent — whatever the host's speed.
    assert cancel.wakes == [True]
    # And the cancelled round did not launch one more dispatch on the way out.
    assert launches == ["engineer"]
    # The round decided this stop, so it is reported as the cancellation it was —
    # its relaunches were cut short, not spent, and the launch path is not accused.
    assert result.detail.startswith("cancelled cooperatively")
    assert "just smoke" not in result.detail


def test_real_git_teardown_refusal_is_deferred_after_publication(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-locked-cleanup")
    workspace = Workspace(
        tmp_path / "locked-cleanup-worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )

    def locking_dispatch(persona, task, *, project_dir, **kwargs):
        worktree = Path(project_dir)
        (worktree / "locked-cleanup.txt").write_text("published\n", encoding="utf-8")
        run_clone = workspace.clone_dir(normalize_repo(str(origin)))
        subprocess.run(["git", "-C", str(run_clone), "worktree", "lock", str(worktree)], check=True)
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(origin),
        "publish before real Git cleanup refusal",
        "engineer",
        workspace=workspace,
        branch="locked-cleanup",
        dispatch_fn=locking_dispatch,
        recorded_gate=["true"],
    )

    assert result.outcome == "merged", result.detail
    assert result.deferred_cleanup and "locked working tree" in result.deferred_cleanup[0]
    run_clone = workspace.clone_dir(normalize_repo(str(origin)))
    orphan = gitops.worktrees(run_clone)["locked-cleanup"]
    subprocess.run(["git", "-C", str(run_clone), "worktree", "unlock", str(orphan)], check=True)
    workspace.remove_worktree(normalize_repo(str(origin)), orphan)


def test_synthetic_stack_teardown_contention_is_deferred_with_real_git(
    tmp_path, bare_origin
) -> None:
    """The other lifecycle teardown site preserves its real synthetic push outcome."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-stack")

    class ContendedStackWorkspace(Workspace):
        def remove_worktree(self, repo, path) -> None:
            raise LockTimeout("shared .git remains busy")

    workspace = ContendedStackWorkspace(
        tmp_path / "stack-worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )
    ref = normalize_repo(str(origin))
    workspace.ensure_clone(ref)
    result = lifecycle_module.LifecycleResult(
        ref.slug, "stack", "engineer", "main", "stack", "error"
    )

    built = lifecycle_module._build_synthetic_stack_base(
        ref,
        workspace,
        "main",
        [StackBase("main")],
        result=result,
        journal=lifecycle_module.NullNodeJournal(),
    )

    assert isinstance(built, lifecycle_module.SyntheticStackBase)
    assert _tip(origin, f"refs/heads/{built.branch}")
    assert result.deferred_cleanup and "remove-worktree deferred" in result.deferred_cleanup[0]
    cleanup = Workspace(
        tmp_path / "stack-worktrees",
        resolver=lambda _spec: canonical,
        run_token=workspace.run_token,
    )
    cleanup.ensure_clone(ref)
    cleanup.remove_worktree(ref, gitops.worktrees(cleanup.clone_dir(ref))[built.branch])


def test_failed_synthetic_stack_defers_worktree_and_branch_cleanup_with_real_git(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    writer = gitops.clone(origin, tmp_path / "stack-writer")
    for branch, content in (("stack-left", "left\n"), ("stack-right", "right\n")):
        subprocess.run(["git", "checkout", "-B", branch, "origin/main"], cwd=writer, check=True)
        (writer / "conflict.txt").write_text(content, encoding="utf-8")
        gitops.add_all(writer)
        gitops.commit(writer, f"test: create {branch}")
        gitops.push(writer, branch, set_upstream=False)
    canonical = gitops.clone(origin, tmp_path / "canonical-conflict")

    class ContendedCleanupWorkspace(Workspace):
        def remove_worktree(self, repo, path) -> None:
            raise LockTimeout("worktree cleanup busy")

        def delete_branch(self, repo, branch) -> None:
            raise LockTimeout("branch cleanup busy")

    workspace = ContendedCleanupWorkspace(
        tmp_path / "conflict-worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )
    ref = normalize_repo(str(origin))
    workspace.ensure_clone(ref)
    result = lifecycle_module.LifecycleResult(
        ref.slug, "stack", "engineer", "main", "stack", "error"
    )

    built = lifecycle_module._build_synthetic_stack_base(
        ref,
        workspace,
        "main",
        [StackBase("stack-left"), StackBase("stack-right")],
        result=result,
        journal=lifecycle_module.NullNodeJournal(),
    )

    assert isinstance(built, lifecycle_module.StackConflict)
    assert [detail.split(" deferred", 1)[0] for detail in result.deferred_cleanup] == [
        "remove-worktree",
        "delete-branch",
    ]
    run_clone = workspace.clone_dir(ref)
    synthetic = next(
        branch
        for branch in gitops.worktrees(run_clone)
        if branch.startswith("ai-orchestrator/stack-base/")
    )
    cleanup = Workspace(
        tmp_path / "conflict-worktrees",
        resolver=lambda _spec: canonical,
        run_token=workspace.run_token,
    )
    cleanup.ensure_clone(ref)
    cleanup.remove_worktree(ref, gitops.worktrees(run_clone)[synthetic])
    cleanup.delete_branch(ref, synthetic)


def test_identity_cache_and_repo_post_checkout_hook_are_wired_across_dispatches(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """Real worktree creation runs repo hooks and reuses one identity cache."""
    origin = bare_origin()
    hook_author = gitops.clone(origin, tmp_path / "hook-author")
    hook_marker = tmp_path / "post-checkout-runs"
    hooks = hook_author / ".githooks"
    hooks.mkdir()
    repo_pre_push = hooks / "pre-push"
    repo_pre_push.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    repo_pre_push.chmod(0o755)
    hook = hooks / "post-checkout"
    hook.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$PWD\" >> {shlex.quote(str(hook_marker))}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    gitops.add_all(hook_author)
    gitops.commit(hook_author, "test: add target post-checkout hook")
    gitops.push(hook_author, "main", set_upstream=False)

    workspace = _workspace(tmp_path, origin)
    observed_caches: list[Path] = []

    for number in (1, 2):
        result = run_repo_task(
            str(origin),
            f"complete-now capture-cache-env write-unique-change {number}\n",
            "engineer",
            workspace=workspace,
            base_path=command_base(),
            persona_dir=personas_dir,
            repo_type="single-owner",
            workflow="local",
            branch=f"cache-hook-{number}",
            recorded_gate=[
                "sh",
                "-c",
                'test -d "$ORCHESTRATOR_CACHE_DIR" && '
                'case "$ORCHESTRATOR_CACHE_DIR" in /*) true;; *) false;; esac',
            ],
        )
        assert result.outcome == "merged", result.detail
        publication_checkout = Path(
            workspace.selection(normalize_repo(str(origin))).publication_checkout
        )
        observed_caches.append(
            Path((publication_checkout / "CACHE_ENV.txt").read_text(encoding="utf-8"))
        )

    assert observed_caches[0] == observed_caches[1]
    assert observed_caches[0].parent.name == "cache"
    assert observed_caches[0].parent.parent == Path(os.environ["AI_ORCHESTRATOR_HOME"])
    lifecycle_worktrees = [
        path
        for path in hook_marker.read_text(encoding="utf-8").splitlines()
        if Path(path).name.startswith("cache-hook-")
    ]
    assert len(lifecycle_worktrees) == 2


def test_real_lifecycle_dispatch_drafts_pr_bodies_and_preserves_fallbacks(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """The real onejudge boundary authors single/workstream bodies and safely falls back."""
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin, workflow="remote")
    github = FakeGitHub(origin)
    base = command_base(max_turns=2)

    single_task = (
        "## What\ncomplete-now write-change the single lifecycle behavior.\n\n"
        "## Why\nGive users the requested capability.\n\n"
        "## Acceptance criteria\n- The branch publishes through the real lifecycle.\n\n"
        "## Additional info\nderive-task-why"
    )
    single = run_repo_task(
        "acme/widget",
        single_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-single",
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert single.pr is not None, (single.outcome, single.detail)
    single_body = github._prs[single.pr.number].body
    assert single.outcome == "pr-open"
    assert single_body == (
        "## What\nAdds the completed behavior from the branch diff.\n\n"
        "## Why\nGive users the requested capability.\n"
    )
    assert single_task not in single_body

    workstream = run_repo_task(
        "acme/widget",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-workstream",
        steps=[
            Step("implement", "engineer", "complete-now write-change raw implementation handoff"),
            Step(
                "verify",
                "engineer",
                "complete-now capture-cache-env raw verification handoff",
                deps=["implement"],
            ),
        ],
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    workstream_body = github._prs[workstream.pr.number].body
    assert workstream.outcome == "pr-open"
    assert workstream_body.startswith("## What\nAdds the completed behavior from the branch diff.")
    assert "raw implementation handoff" not in workstream_body

    structured_draft = run_repo_task(
        "acme/widget",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-structured-workstream",
        steps=[
            Step(
                "implement",
                "engineer",
                "## What\ncomplete-now write-change the drafted API.\n\n"
                "## Why\nLet users call the drafted API.\n\n"
                "## Acceptance criteria\n- The drafted API is available.\n\n"
                "## Additional info\nderive-workstream-why",
            ),
            Step(
                "compatibility",
                "engineer",
                "complete-now write-unique-change preserve legacy callers",
                deps=["implement"],
            ),
            Step(
                "verify",
                "engineer",
                "## What\ncomplete-now capture-cache-env verify the drafted API.\n\n"
                "## Why\nKeep the drafted API reliable.\n\n"
                "## Acceptance criteria\n- The drafted API is verified.",
                deps=["compatibility"],
            ),
        ],
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert structured_draft.outcome == "pr-open"
    assert structured_draft.pr is not None
    assert github._prs[structured_draft.pr.number].body == (
        "## What\nAdds the completed behavior from the branch diff.\n\n"
        "## Why\nLet users call the drafted API while keeping it reliable.\n"
    )

    fallback_task = (
        "## What\ncomplete-now write-change with a deterministic fallback.\n\n"
        "## Why\nKeep the user's publication context when drafting-fails.\n\n"
        "## Acceptance criteria\n- The fallback is published.\n\n"
        "## Additional info\nThis must not appear in the PR body."
    )
    fallback = run_repo_task(
        "acme/widget",
        fallback_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-fallback",
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert fallback.outcome == "pr-open"
    assert github._prs[fallback.pr.number].body == (
        "## What\ncomplete-now write-change with a deterministic fallback.\n\n"
        "## Why\nKeep the user's publication context when drafting-fails.\n"
    )

    structured_workstream = run_repo_task(
        "acme/widget",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-structured-workstream-fallback",
        steps=[
            Step(
                "implement",
                "engineer",
                "## What\ncomplete-now write-change the API.\n\n"
                "## Why\nLet users call the API.\n\n"
                "## Acceptance criteria\n- The API is available.",
            ),
            Step(
                "legacy",
                "engineer",
                "complete-now write-unique-change legacy compatibility step",
                deps=["implement"],
            ),
            Step(
                "verify",
                "engineer",
                "## What\ncomplete-now capture-cache-env verify the API.\n\n"
                "## Why\nPrevent regressions when drafting-fails.\n\n"
                "## Acceptance criteria\n- The API is verified.",
                deps=["legacy"],
            ),
        ],
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert structured_workstream.outcome == "pr-open"
    assert github._prs[structured_workstream.pr.number].body == (
        "## What\n- complete-now write-change the API.\n"
        "- **legacy** (`engineer`): complete-now write-unique-change legacy compatibility step\n"
        "- complete-now capture-cache-env verify the API.\n\n"
        "## Why\n- Let users call the API.\n"
        "- **legacy** (`engineer`): complete-now write-unique-change legacy compatibility step\n"
        "- Prevent regressions when drafting-fails.\n"
    )

    empty_task = (
        "## What\ncomplete-now write-change drafting-empty empty fallback handoff.\n\n"
        "## Why\nPreserve structured context after empty drafting output.\n\n"
        "## Acceptance criteria\n- The deterministic fallback is published.\n\n"
        "## Additional info\nThis orchestration detail must not appear."
    )
    empty = run_repo_task(
        "acme/widget",
        empty_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-empty",
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert empty.outcome == "pr-open"
    assert github._prs[empty.pr.number].body == (
        "## What\ncomplete-now write-change drafting-empty empty fallback handoff.\n\n"
        "## Why\nPreserve structured context after empty drafting output.\n"
    )

    # The first line names the change when no commit does, so it fits a subject.
    invalid_task = "complete-now write-change drafting-invalid fallback"
    invalid = run_repo_task(
        "acme/widget",
        invalid_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-invalid",
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert invalid.outcome == "pr-open"
    assert invalid_task in github._prs[invalid.pr.number].body
    assert "nonempty malformed drafting output" not in github._prs[invalid.pr.number].body

    structured_invalid_task = (
        "## What\ncomplete-now write-change drafting-invalid structured fallback.\n\n"
        "## Why\nPreserve structured context after malformed drafting output.\n\n"
        "## Acceptance criteria\n- The deterministic fallback is published.\n\n"
        "## Additional info\nThis orchestration detail must not appear."
    )
    structured_invalid = run_repo_task(
        "acme/widget",
        structured_invalid_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-structured-invalid",
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert structured_invalid.outcome == "pr-open"
    assert github._prs[structured_invalid.pr.number].body == (
        "## What\ncomplete-now write-change drafting-invalid structured fallback.\n\n"
        "## Why\nPreserve structured context after malformed drafting output.\n"
    )

    error_task = "complete-now write-change drafting-errors error fallback handoff"
    error = run_repo_task(
        "acme/widget",
        error_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-error",
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert error.outcome == "pr-open"
    assert error_task in github._prs[error.pr.number].body

    structured_error_task = (
        "## What\ncomplete-now write-change drafting-errors structured fallback.\n\n"
        "## Why\nPreserve structured context after a drafting exception.\n\n"
        "## Acceptance criteria\n- The deterministic fallback is published.\n\n"
        "## Additional info\nThis orchestration detail must not appear."
    )
    structured_error = run_repo_task(
        "acme/widget",
        structured_error_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-structured-error",
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert structured_error.outcome == "pr-open"
    assert github._prs[structured_error.pr.number].body == (
        "## What\ncomplete-now write-change drafting-errors structured fallback.\n\n"
        "## Why\nPreserve structured context after a drafting exception.\n"
    )

    explicit = run_repo_task(
        "acme/widget",
        "complete-now write-change explicit body handoff",
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-explicit",
        body="## What\nSupplied body.\n\n## Why\nSupplied reason.\n",
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert explicit.outcome == "pr-open"
    assert github._prs[explicit.pr.number].body == (
        "## What\nSupplied body.\n\n## Why\nSupplied reason.\n"
    )

    title_task = "complete-now write-change explicit title handoff"
    titled = run_repo_task(
        "acme/widget",
        title_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-title",
        title="feat: use supplied title",
        base_path=base,
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )
    assert titled.outcome == "pr-open"
    assert github._prs[titled.pr.number].title == "feat: use supplied title"
    assert "Adds the completed behavior from the branch diff" in github._prs[titled.pr.number].body
    assert title_task not in github._prs[titled.pr.number].body


def test_empty_orchestrator_home_fails_at_lifecycle_cache_boundary(
    tmp_path, bare_origin, monkeypatch
) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", "")

    with pytest.raises(ValueError, match="AI_ORCHESTRATOR_HOME must not be empty"):
        run_repo_task(
            str(origin),
            "never dispatched",
            "engineer",
            workspace=workspace,
            dispatch_fn=_per_step_dispatch(),
            repo_type="single-owner",
            workflow="local",
            branch="empty-home",
        )


def _advance_origin(tmp_path: Path, origin: Path, filename: str, content: str) -> str:
    """Commit one concurrent base-branch change through a separate real clone."""
    checkout = gitops.clone(origin, tmp_path / f"advance-{filename.replace('/', '-')}")
    path = checkout / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    gitops.add_all(checkout)
    sha = gitops.commit(checkout, f"advance main with {filename}")
    gitops.push(checkout, "main", set_upstream=False)
    return sha


def test_repo_plan_ledger_and_guided_next_round(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """The real CLI + onejudge + git lifecycle records and retries an unresolved node."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-ledger")
    Registry().register(str(canonical), workflow="local")
    runs_dir = tmp_path / "runs"
    plan_path = tmp_path / "repo-plan.json"
    first_plan = {
        "name": "ledger e2e",
        "tasks": [
            {
                "id": "change",
                "repo": str(canonical),
                "persona": "engineer",
                "task": "should-fail write-change: preserve this partial attempt",
                "max_turns": EXHAUSTED_STEP_MAX_TURNS,
                "recorded_gate": ["true"],
                "workflow": "local",
                "repo_type": "single-owner",
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
        # The node override must beat this command-wide override and preserve
        # the registered local publication path.
        "--repo-type",
        "team",
    ]
    rc = main_plan([str(plan_path), "--run", "fixed-run", "--runs-dir", str(runs_dir), *common])
    captured = capsys.readouterr()
    assert rc == 1 and json.loads(captured.out)["results"]["change"]["status"] == "failed"
    first_result = json.loads((runs_dir / "fixed-run" / "round-01" / "result.json").read_text())
    assert first_result["schema_version"] == 6
    preserved_branch = first_result["results"]["change"]["branch"]
    preserved_checkpoint = first_result["results"]["change"]["resume"]["checkpoint"]
    assert first_result["results"]["change"]["resume"]["mode"] == "retry"
    first = runs_dir / "fixed-run" / "round-01"
    assert json.loads((first / "plan.json").read_text()) == first_plan
    assert (first / "result.json").is_file()
    follow_up = "- Add a regression test for the adjacent edge case."
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
    second_result = json.loads((second / "result.json").read_text())
    assert second_result["results"]["change"]["status"] == "done"
    assert second_result["results"]["change"]["follow_ups"] == follow_up
    publication = second_result["results"]["change"]
    # A settled publication points at the merge path's own gate run, for both
    # verdicts: without it a green round is indistinguishable from an unverified one.
    gate_log = Path(publication["artifacts"]["gate_log"])
    assert gate_log.read_text(encoding="utf-8").count("verdict: passed") == 2
    assert publication["branch"] == preserved_branch
    assert publication["retry_lineage"] == {
        "supersedes_branch": preserved_branch,
        "supersedes_checkpoint": preserved_checkpoint,
        "disposition": "recovered",
        "supersedes_round": 1,
    }
    assert publication["repository_type"] == publication["repo_type"] == "single-owner"
    assert publication["publication_workflow"] == publication["workflow"] == "local"
    assert publication["merge_policy"] == "direct"
    assert publication["base_branch"] == publication["pr_base"] == "main"
    assert publication["synthetic_stack_base"] is None and publication["stack_bases"] == []
    assert follow_up in captured.out
    indexed = subprocess.run(
        ["just", "telemetry", "--runs-dir", str(runs_dir), "--all"],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert indexed.returncode == 0, indexed.stderr
    telemetry = json.loads(indexed.stdout)
    assert telemetry["metrics"]["recovered_branches"] == 1

    # The merge path's gate is journaled again, so the interval between a green
    # gate and the publication it released is measurable rather than absent.
    (green_to_publication,) = telemetry["metrics"]["green_to_publication_seconds"]
    assert green_to_publication >= 0.0
    listed = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs_dir)],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        check=True,
    )
    runs_output = listed.stdout
    assert follow_up in runs_output
    assert f"just results fixed-run --runs-dir {runs_dir}" in runs_output
    assert "nothing to iterate" in captured.err

    unrecorded_plan = {
        **first_plan,
        "tasks": [
            {
                **first_plan["tasks"][0],
                "task": "complete-now write-unique-change: unrecorded schema output",
            }
        ],
    }
    plan_path.write_text(json.dumps(unrecorded_plan), encoding="utf-8")
    assert main_plan([str(plan_path), "--no-record", *common]) == 0
    unrecorded = json.loads(capsys.readouterr().out)
    assert unrecorded["schema_version"] == 6 and "round" not in unrecorded


def test_a_node_that_cannot_finish_settles_instead_of_being_redispatched_forever(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """A preserved branch is continued to a budget, then left for the planner.

    The loop this closes: a node that never finishes was redispatched every round on
    the same branch and handed another `chore: ... (incomplete step)` marker commit
    each time, with nothing recording that the attempts were going nowhere. Driven
    through the real round CLIs against a real origin, so what is asserted is the
    ledger and the branch an operator would actually read.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-bounded-resume")
    Registry().register(str(canonical), workflow="local")
    runs_dir = tmp_path / "runs"
    plan_path = tmp_path / "bounded-resume.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        # Writes real work every round, so every attempt earns a
                        # marker: the growth is bounded by bounding the attempts.
                        "task": "should-fail write-unique-change: never finishes",
                        "max_turns": EXHAUSTED_STEP_MAX_TURNS,
                        "verify_cmd": ["true"],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    },
                    {
                        "id": "follow",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": "complete-now write-change: builds on the change",
                        "verify_cmd": ["true"],
                        "workflow": "local",
                        "repo_type": "single-owner",
                        "deps": ["change"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
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
    run = "bounded-resume"

    def _recorded(number: int, name: str) -> dict:
        recorded = runs_dir / run / f"round-{number:02d}" / name
        return json.loads(recorded.read_text(encoding="utf-8"))

    assert main_plan([str(plan_path), "--run", run, "--runs-dir", str(runs_dir), *common]) == 1
    capsys.readouterr()
    branch = _recorded(1, "result.json")["results"]["change"]["branch"]

    # Every automatic continuation the budget allows, and not one more. Each one
    # resumes the same preserved branch and spends one attempt from that budget,
    # which the round's own plan records.
    for attempt in range(1, MAX_AUTOMATIC_ROUND_RESUMES + 1):
        assert next_round_main([run, "--runs-dir", str(runs_dir), *common]) == 1
        capsys.readouterr()
        dispatched = _recorded(attempt + 1, "plan.json")["tasks"]
        resumed = next(task for task in dispatched if task["id"] == "change")
        assert resumed["resume"]["branch"] == branch
        assert resumed["resume"]["attempts"] == attempt
        assert _recorded(attempt + 1, "result.json")["results"]["change"]["branch"] == branch
        # Its dependent is still gated on it, and holds no anchor to work that has
        # not landed anywhere.
        follower = next(task for task in dispatched if task["id"] == "follow")
        assert follower["deps"] == ["change"]
        assert not follower.get("stack_bases")

    rounds_run = 1 + MAX_AUTOMATIC_ROUND_RESUMES
    # Exactly one marker, across every round that ran — and every one of those rounds
    # committed real work and then failed, so each was entitled to preserve something.
    # The marker states one fact about the branch, that it carries preserved incomplete
    # work, and a round that finds it already stated adds nothing by stating it again.
    # Bounding the attempts stopped the growth from being unbounded; this is what stops
    # it accruing one commit per round inside that bound, each of which recovery would
    # otherwise have to attest separately.
    markers = len(incomplete_commits(canonical, "origin/main", branch))
    assert markers == 1, markers
    assert rounds_run > 1, "a single round could not tell repetition from the first mark"

    # The exhausted node settles out, and its dependent is released to run against
    # the base rather than waiting on work nothing is going to finish — the same
    # release a `drop` gives, and with no publication anchor invented for a branch
    # that never landed.
    # Exit 0: with the exhausted node gone, the released dependent is all that runs
    # and it completes, so the round settles the graph.
    assert next_round_main([run, "--runs-dir", str(runs_dir), *common]) == 0
    capsys.readouterr()
    released = _recorded(rounds_run + 1, "plan.json")["tasks"]

    assert [task["id"] for task in released] == ["follow"]
    assert released[0]["deps"] == []
    assert not released[0].get("stack_bases")
    assert _recorded(rounds_run + 1, "result.json")["results"]["follow"]["status"] == "done"

    # The exhausted node was not dispatched again, so no further marker reached its
    # branch and the preserved work is still where `just repo-recover` expects it.
    assert "change" not in _recorded(rounds_run + 1, "result.json")["results"]
    assert len(incomplete_commits(canonical, "origin/main", branch)) == markers
    assert gitops.branch_exists(canonical, branch)


def test_an_explicit_retry_restores_an_exhausted_preserved_branchs_budget(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """The bound stops the harness repeating itself, never a planner decision.

    Same real round CLIs, driven to the same exhausted state, and then given the
    `retry` edit a planner writes after reading the result. The node runs again on
    the branch it preserved, with the budget started over rather than topped up.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-retry-budget")
    Registry().register(str(canonical), workflow="local")
    runs_dir = tmp_path / "runs"
    plan_path = tmp_path / "retry-budget.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": "should-fail write-unique-change: never finishes",
                        "max_turns": EXHAUSTED_STEP_MAX_TURNS,
                        "verify_cmd": ["true"],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
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
    run = "retry-budget"

    def _recorded(number: int, name: str) -> dict:
        return json.loads((runs_dir / run / f"round-{number:02d}" / name).read_text("utf-8"))

    assert main_plan([str(plan_path), "--run", run, "--runs-dir", str(runs_dir), *common]) == 1
    capsys.readouterr()
    branch = _recorded(1, "result.json")["results"]["change"]["branch"]
    for _ in range(MAX_AUTOMATIC_ROUND_RESUMES):
        assert next_round_main([run, "--runs-dir", str(runs_dir), *common]) == 1
        capsys.readouterr()
    spent = 1 + MAX_AUTOMATIC_ROUND_RESUMES
    assert _recorded(spent, "plan.json")["tasks"][0]["resume"]["attempts"] == (
        MAX_AUTOMATIC_ROUND_RESUMES
    )

    edits = tmp_path / "retry.json"
    edits.write_text(json.dumps({"retry": {"change": {}}}), encoding="utf-8")
    assert next_round_main([run, str(edits), "--runs-dir", str(runs_dir), *common]) == 1
    capsys.readouterr()
    retried = _recorded(spent + 1, "plan.json")["tasks"][0]

    assert retried["resume"]["branch"] == branch
    assert "attempts" not in retried["resume"]
    assert _recorded(spent + 1, "result.json")["results"]["change"]["branch"] == branch


def test_ordinary_next_round_resumes_committed_lifecycle_branch(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """An unchanged failed node carries its real committed branch into the next round."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-ordinary-resume")
    Registry().register(str(canonical), workflow="local")
    runs_dir = tmp_path / "runs"
    plan_path = tmp_path / "ordinary-resume.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": "should-fail write-change: preserve across ordinary rounds",
                        "max_turns": EXHAUSTED_STEP_MAX_TURNS,
                        "verify_cmd": ["true"],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workspace_root = tmp_path / "workspace"
    common = [
        "--base",
        str(command_base()),
        "--persona-dir",
        str(personas_dir),
        "--workspace",
        str(workspace_root),
        "--format",
        "json",
    ]

    assert (
        main_plan(
            [
                str(plan_path),
                "--run",
                "ordinary-resume",
                "--runs-dir",
                str(runs_dir),
                *common,
            ]
        )
        == 1
    )
    capsys.readouterr()
    first = json.loads(
        (runs_dir / "ordinary-resume" / "round-01" / "result.json").read_text(encoding="utf-8")
    )["results"]["change"]
    branch = first["branch"]
    checkpoint = first["resume"]["checkpoint"]

    assert next_round_main(["ordinary-resume", "--runs-dir", str(runs_dir), *common]) == 1
    capsys.readouterr()
    second = json.loads(
        (runs_dir / "ordinary-resume" / "round-02" / "result.json").read_text(encoding="utf-8")
    )["results"]["change"]
    assert second["branch"] == branch
    assert second["resume"]["checkpoint"] == checkpoint

    # Each round works in a clone of its own that it then discards, so the
    # registered checkout is where a preserved branch has to survive to be
    # resumable at all — and where round two just found this one.
    assert gitops.is_ancestor(canonical, checkpoint, branch)
    assert incomplete_commits(canonical, "origin/main", branch)
    events = [
        json.loads(line)
        for line in (runs_dir / "ordinary-resume" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    discovered = [
        event
        for event in events
        if event["round"] == 2
        and event["kind"] == "branch-discovered"
        and event["node"] == "change"
    ]
    assert len(discovered) == 1
    assert discovered[0]["detail"]["branch"] == branch
    assert discovered[0]["detail"]["resumed"] is True

    edits = tmp_path / "fresh-start.json"
    fresh_branch = "test/explicit-fresh-start"
    edits.write_text(
        json.dumps({"retry": {"change": {"branch": fresh_branch}}}),
        encoding="utf-8",
    )
    assert (
        next_round_main(["ordinary-resume", str(edits), "--runs-dir", str(runs_dir), *common]) == 1
    )
    capsys.readouterr()
    third = json.loads(
        (runs_dir / "ordinary-resume" / "round-03" / "result.json").read_text(encoding="utf-8")
    )["results"]["change"]
    assert third["branch"] == fresh_branch
    assert not gitops.is_ancestor(canonical, checkpoint, fresh_branch)
    events = [
        json.loads(line)
        for line in (runs_dir / "ordinary-resume" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    fresh_discovered = [
        event
        for event in events
        if event["round"] == 3
        and event["kind"] == "branch-discovered"
        and event["node"] == "change"
    ]
    assert len(fresh_discovered) == 1
    assert fresh_discovered[0]["detail"]["branch"] == fresh_branch
    assert fresh_discovered[0]["detail"]["resumed"] is False

    pin = tmp_path / "pin-existing.json"
    pin.write_text(
        json.dumps({"retry": {"change": {"branch": branch}}}),
        encoding="utf-8",
    )
    assert next_round_main(["ordinary-resume", str(pin), "--runs-dir", str(runs_dir), *common]) == 1
    capsys.readouterr()
    fourth = json.loads(
        (runs_dir / "ordinary-resume" / "round-04" / "result.json").read_text(encoding="utf-8")
    )["results"]["change"]
    assert fourth["branch"] == branch
    assert gitops.is_ancestor(canonical, checkpoint, branch)
    events = [
        json.loads(line)
        for line in (runs_dir / "ordinary-resume" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    pinned_discovered = [
        event
        for event in events
        if event["round"] == 4
        and event["kind"] == "branch-discovered"
        and event["node"] == "change"
    ]
    assert len(pinned_discovered) == 1
    assert pinned_discovered[0]["detail"]["branch"] == branch
    assert pinned_discovered[0]["detail"]["resumed"] is False


def test_lifecycle_records_verified_change_already_integrated_on_base(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """The real CLI, provider seam, and local publisher reconcile an early landing."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-already-integrated")
    Registry().register(str(canonical), workflow="local")
    plan_path = tmp_path / "already-integrated.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": (
                            "complete-now write-change publish-change-to-base: "
                            "land before lifecycle closeout"
                        ),
                        "recorded_gate": ["true"],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"

    rc = main_plan(
        [
            str(plan_path),
            "--run",
            "already-integrated",
            "--runs-dir",
            str(runs_dir),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--workspace",
            str(tmp_path / "workspace"),
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    result = payload["results"]["change"]
    assert rc == 0, json.dumps(result, indent=2)
    assert payload["ok"] is True
    assert result["status"] == "done" and result["outcome"] == "already-integrated"
    assert "already present on main" in result["detail"]
    assert (canonical / "CHANGE.txt").read_text(encoding="utf-8") == "change from fake agent\n"
    recorded = json.loads(
        (runs_dir / "already-integrated" / "round-01" / "result.json").read_text()
    )
    assert recorded["results"]["change"]["outcome"] == "already-integrated"


def test_lifecycle_accepts_merge_path_gated_change_already_integrated_on_base(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """An early landing is not subjected to a duplicate lifecycle-side gate."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-integrated-gate-failure")
    Registry().register(str(canonical), workflow="local")
    plan_path = tmp_path / "integrated-gate-failure.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": (
                            "complete-now write-change publish-change-to-base: "
                            "land invalid work before lifecycle closeout"
                        ),
                        "recorded_gate": ["false"],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"

    rc = main_plan(
        [
            str(plan_path),
            "--run",
            "integrated-gate-failure",
            "--runs-dir",
            str(runs_dir),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--workspace",
            str(tmp_path / "workspace"),
            "--format",
            "json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"]["change"]
    assert rc == 0
    assert payload["ok"] is True
    assert result["status"] == "done" and result["outcome"] == "already-integrated"
    assert (canonical / "CHANGE.txt").exists()
    assert _has_file(origin, "main", "CHANGE.txt")
    recorded = json.loads(
        (runs_dir / "integrated-gate-failure" / "round-01" / "result.json").read_text()
    )
    assert recorded["results"]["change"]["outcome"] == "already-integrated"


def test_lifecycle_ignores_legacy_verify_override_during_local_publication(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """The removed lifecycle gate cannot mutate publication as a side effect."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-publication-race")
    Registry().register(str(canonical), workflow="local")
    plan_path = tmp_path / "publication-race.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": "complete-now write-change: race local publication",
                        "recorded_gate": [
                            "sh",
                            "-c",
                            "git symbolic-ref -q HEAD >/dev/null && "
                            "git push origin HEAD:main || true",
                        ],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"

    rc = main_plan(
        [
            str(plan_path),
            "--run",
            "publication-race",
            "--runs-dir",
            str(runs_dir),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--workspace",
            str(tmp_path / "workspace"),
            "--format",
            "json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"]["change"]
    assert rc == 0, json.dumps(result, indent=2)
    assert payload["ok"] is True
    assert result["status"] == "done" and result["outcome"] == "merged"
    assert "local direct-merge" in result["detail"]
    assert (canonical / "CHANGE.txt").read_text(encoding="utf-8") == "change from fake agent\n"
    assert gitops.head_sha(canonical) == _tip(origin, "main")
    recorded = json.loads((runs_dir / "publication-race" / "round-01" / "result.json").read_text())
    assert recorded["results"]["change"]["outcome"] == "merged"


# --- local repo: direct merge into main after checks -----------------------


@pytest.mark.parametrize(
    ("identity", "expected_wrapper", "human_pause"),
    [
        (AI_ORCHESTRATOR_IDENTITY, True, False),
        (AI_ORCHESTRATOR_IDENTITY, True, True),
        ("https://github.com/nickderobertis/llmlint", False, False),
        ("https://github.com/nickderobertis/llmlint", False, True),
    ],
)
def test_lifecycle_scopes_llmlint_wrapper_from_resolved_repository_identity(
    tmp_path, bare_origin, identity, expected_wrapper, human_pause
) -> None:
    """Resolved identity reaches worker and PR-author dispatches over real git."""

    class IdentityWorkspace(Workspace):
        def selection(self, repo):
            return replace(super().selection(repo), publication_identity=IdentityKey(identity))

    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    seen: list[tuple[str, bool]] = []
    comparisons: list[tuple[str, str, str]] = []

    def writing_dispatch(persona, task, *, project_dir, use_llmlint_wrapper, env, **_):
        seen.append((persona, use_llmlint_wrapper))
        comparisons.append(
            (
                persona,
                env["ORCHESTRATOR_COMPARISON_REMOTE"],
                env["ORCHESTRATOR_COMPARISON_BASE"],
            )
        )
        if persona == "pr-author":
            output = task.split(
                "Write the final body, and nothing else, to this absolute path:\n", 1
            )[1].splitlines()[0]
            Path(output).write_text(
                "## What\nRoutes the wrapper.\n\n## Why\nKeeps target gates isolated.\n",
                encoding="utf-8",
            )
            return Report(persona, 0, True, False, 1, [], {}, {}, "")
        Path(project_dir, "change.txt").write_text("change\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        "acme/widget",
        "change wrapper routing" if not human_pause else None,
        "engineer" if not human_pause else None,
        workspace=IdentityWorkspace(
            tmp_path / "worktrees",
            resolver=lambda _spec: canonical,
            workflow="remote",
            repo_type="single-owner",
        ),
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        github=FakeGitHub(origin),
        steps=(
            [
                Step("prepare", "engineer", "change wrapper routing"),
                Step("approve", task="Approve routing.", kind="human", deps=["prepare"]),
            ]
            if human_pause
            else None
        ),
        recorded_gate=["true"],
        dispatch_fn=writing_dispatch,
    )

    assert result.outcome == ("waiting-human" if human_pause else "pr-open")
    assert seen == [("engineer", expected_wrapper), ("pr-author", expected_wrapper)]
    # Every dispatch of one workstream — the worker and the PR-author drafting that
    # follows it, on the ordinary and the human-paused publication path alike — is
    # handed the same comparison identity, so nothing it runs can resolve a
    # different base than the publication rebuild judges.
    assert comparisons == [("engineer", "origin", "main"), ("pr-author", "origin", "main")]


def test_every_dispatch_of_a_workstream_carries_the_side_selection_it_was_given(
    tmp_path, bare_origin
) -> None:
    """One lifecycle node's choice has to reach every dispatch the workstream makes.

    A worker whose judge moved between its own turn and the PR-author's would be
    supervised by a provider the operator never chose, which is the same failure as
    the two sides sharing one process-wide value.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    selections: list[tuple[str, str | None, str | None]] = []

    def writing_dispatch(persona, task, *, project_dir, env, **_):
        selections.append((persona, env.get(WORKER_HARNESS_ENV), env.get(JUDGE_HARNESS_ENV)))
        if persona == "pr-author":
            output = task.split(
                "Write the final body, and nothing else, to this absolute path:\n", 1
            )[1].splitlines()[0]
            Path(output).write_text(
                "## What\nPairs two providers.\n\n## Why\nOne authors, one reviews.\n",
                encoding="utf-8",
            )
            return Report(persona, 0, True, False, 1, [], {}, {}, "")
        Path(project_dir, "change.txt").write_text("change\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        "acme/widget",
        "pair a strong author with a strong reviewer",
        "engineer",
        workspace=Workspace(
            tmp_path / "worktrees",
            resolver=lambda _spec: canonical,
            workflow="remote",
            repo_type="single-owner",
        ),
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        github=FakeGitHub(origin),
        recorded_gate=["true"],
        dispatch_fn=writing_dispatch,
        worker_harness="codex",
        judge_harness="claude-code:alternate",
    )

    assert result.outcome == "pr-open", result.detail
    assert selections == [
        ("engineer", "codex", "claude-code:alternate"),
        ("pr-author", "codex", "claude-code:alternate"),
    ]


def test_a_workstream_given_no_selection_carries_neither_variable(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    selections: list[tuple[str | None, str | None]] = []

    def writing_dispatch(persona, task, *, project_dir, env, **_):
        selections.append((env.get(WORKER_HARNESS_ENV), env.get(JUDGE_HARNESS_ENV)))
        Path(project_dir, "change.txt").write_text("change\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(canonical),
        "leave both sides to their configured chains",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees", resolver=lambda _spec: canonical),
        workflow="local",
        repo_type="single-owner",
        recorded_gate=["true"],
        dispatch_fn=writing_dispatch,
    )

    assert result.ok, result.detail
    assert selections == [(None, None)]


def test_an_unconfigured_selection_refuses_the_workstream_before_it_cuts_a_worktree(
    tmp_path, bare_origin
) -> None:
    """The refusal has to come before any side effect, so nothing needs cleaning up."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    worktrees = tmp_path / "worktrees"

    def never_dispatch(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("a refused selection must not dispatch")

    with pytest.raises(ConfigError) as excinfo:
        run_repo_task(
            str(canonical),
            "must not start",
            "engineer",
            workspace=Workspace(worktrees, resolver=lambda _spec: canonical),
            workflow="local",
            recorded_gate=["true"],
            dispatch_fn=never_dispatch,
            judge_harness="opencode",
        )

    assert "--judge-harness" in str(excinfo.value)
    assert "oneharness.judge.toml" in str(excinfo.value)
    assert not worktrees.exists()


def test_one_node_plan_reports_an_unconfigured_selection_as_a_usage_error(
    tmp_path, bare_origin, capsys
) -> None:
    """Refused before the round is claimed, so no worktree and no ledger row exist."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    plan = _one_node_lifecycle_plan(tmp_path, canonical, task="task")

    rc = graph_module.main([str(plan), "--no-record", "--worker-harness", "opencode"])

    assert rc == 2
    stderr = capsys.readouterr().err
    assert "--worker-harness 'opencode'" in stderr
    assert "oneharness.toml" in stderr


def test_registered_aliases_drive_real_lifecycle_without_a_stray_clone(
    tmp_path, bare_origin, command_base, personas_dir, capsys, monkeypatch
) -> None:
    """Repo and execution aliases reach the real onejudge lifecycle boundary."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    safety = gitops.clone(origin, tmp_path / "safety")
    registry = Registry()
    registry.register(str(canonical), workflow="local")
    registry.register(str(safety))

    result = run_repo_task(
        "local/canonical",
        "complete-now write-change alias lifecycle",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        execution_checkout="local/safety",
        base_path=command_base(),
        persona_dir=personas_dir,
        recorded_gate=["true"],
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert Path(result.publication_checkout) == canonical
    assert Path(result.execution_checkout) == safety
    assert not (Path(os.environ["AI_ORCHESTRATOR_HOME"]) / "repos").exists()

    plan = tmp_path / "alias-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "alias-node",
                        "repo": "local/canonical",
                        "persona": "engineer",
                        "task": "complete-now write-unique-change alias plan lifecycle",
                        "execution_checkout": "local/safety",
                        "recorded_gate": ["true"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    planned_rc = graph_module.main(
        [
            str(plan),
            "--no-record",
            "--workspace",
            str(tmp_path / "plan-worktrees"),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    planned = json.loads(capsys.readouterr().out)

    assert planned_rc == 0
    planned_result = planned["results"]["alias-node"]
    assert planned_result["outcome"] == "merged"
    assert Path(planned_result["publication_checkout"]) == canonical
    assert Path(planned_result["execution_checkout"]) == safety
    assert not (Path(os.environ["AI_ORCHESTRATOR_HOME"]) / "repos").exists()

    remote_origin = bare_origin()
    remote_checkout = gitops.clone(remote_origin, tmp_path / "crozier")
    remote_registry = Registry()
    remote_registry.entries[Slug("nickderobertis/crozier")] = RegistryEntry(
        str(remote_checkout.resolve()),
        str(remote_origin),
        "remote",
        "single-owner",
    )
    remote_registry.save()
    monkeypatch.setattr(lifecycle_module, "CliGitHubBackend", lambda: FakeGitHub(remote_origin))
    remote_plan = tmp_path / "remote-alias-plan.json"
    remote_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "remote-alias",
                        "repo": "nickderobertis/crozier",
                        "persona": "engineer",
                        "task": "complete-now write-change registered remote alias",
                        "recorded_gate": ["true"],
                        "merge_policy": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    remote_rc = graph_module.main(
        [
            str(remote_plan),
            "--no-record",
            "--workspace",
            str(tmp_path / "remote-plan-worktrees"),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    remote = json.loads(capsys.readouterr().out)["results"]["remote-alias"]

    assert remote_rc == 0
    assert remote["outcome"] == "pr-open"
    assert Path(remote["publication_checkout"]) == remote_checkout
    assert remote["workflow"] == "remote" and remote["repo_type"] == "single-owner"


def test_local_identity_executes_in_safety_clone_and_publishes_without_pr(
    tmp_path, bare_origin
) -> None:
    """The self-dispatch safety clone does not alter the identity's local workflow."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "ai-orchestrator")
    safety = gitops.clone(origin, tmp_path / "ai-orchestrator-isolated")
    registry = Registry()
    registry.register(str(canonical), workflow="local")
    registry.register(str(safety))
    github = FakeGitHub(origin)

    result = run_repo_task(
        str(canonical),
        "Change the git subsystem from an isolated safety clone.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        execution_checkout=safety,
        github=github,
        dispatch_fn=make_writing_dispatch(filename="git-subsystem-change.txt"),
        recorded_gate=["true"],
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert github._n == 0
    assert Path(result.execution_checkout) == safety
    assert Path(result.publication_checkout) == canonical
    assert result.publication_workflow == "local"
    assert result.publication_identity == str(origin).removesuffix(".git")
    assert _has_file(origin, "main", "git-subsystem-change.txt")
    assert gitops.head_sha(canonical) == _tip(origin, "main")
    rendered = result.summary()
    assert f"execution checkout: {safety}" in rendered
    assert "publication workflow: local" in rendered


def test_safety_clone_refuses_publication_checkout_on_nonroot_branch(tmp_path, bare_origin) -> None:
    """A safety-clone run must not fast-forward whichever canonical branch is active."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-nonroot")
    safety = gitops.clone(origin, tmp_path / "safety-nonroot")
    Registry().register(str(canonical), workflow="local")
    Registry().register(str(safety))
    subprocess.run(
        ["git", "-C", str(canonical), "switch", "-c", "operator/wip"],
        check=True,
        capture_output=True,
    )
    dispatched: list[str] = []

    def dispatch_fn(persona: str, task: str, **_: object) -> Report:
        dispatched.append(task)
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(canonical),
        "Do not mutate the operator branch.",
        "engineer",
        workspace=Workspace(tmp_path / "nonroot-worktrees"),
        execution_checkout=safety,
        dispatch_fn=dispatch_fn,
        recorded_gate=["true"],
    )

    assert result.outcome == "error"
    assert "publication checkout" in result.detail and "root branch 'main'" in result.detail
    assert dispatched == []
    assert gitops.current_branch(canonical) == "operator/wip"
    assert _tip(origin, "main") == gitops.head_sha(canonical)


def test_registered_remote_identity_keeps_pr_flow(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-remote")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    github = FakeGitHub(origin)

    result = run_repo_task(
        str(canonical),
        "Publish this identity through review.",
        "engineer",
        workspace=Workspace(tmp_path / "remote-worktrees"),
        github=github,
        dispatch_fn=make_writing_dispatch(filename="reviewed.txt"),
        recorded_gate=["true"],
        sleep=lambda _: None,
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert result.publication_workflow == "remote"
    assert result.pr is not None and result.pr.number == 1
    assert github._n == 1
    assert _has_file(origin, "main", "reviewed.txt")
    assert result.repository_type == "single-owner"
    assert result.merge_policy == "auto"
    assert result.pr_base == "main"


def test_cli_github_adopts_only_exact_merged_head_via_all_state_lookup(
    tmp_path, monkeypatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "gh-calls"
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        "printf '%s\\n' "
        '\'[{"number":17,"url":"https://github.test/o/r/pull/17",'
        '"state":"MERGED","headRefOid":"published-head"}]\'\n',
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    backend = CliGitHubBackend()
    adopted = backend.adoptable_pr("o/r", head="feature", base="main", head_sha="published-head")
    stale = backend.adoptable_pr("o/r", head="feature", base="main", head_sha="new-head")

    assert adopted is not None and adopted.number == 17
    assert stale is None
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "pr list --repo o/r --head feature --base main --state all "
        "--json number,url,state,headRefOid",
        "pr list --repo o/r --head feature --base main --state all "
        "--json number,url,state,headRefOid",
    ]


def test_cli_github_rejects_malformed_check_link_from_real_cli_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' "
        '\'{"number":17,"state":"OPEN","isDraft":false,'
        '"mergeStateStatus":"CLEAN","statusCheckRollup":[{'
        '"__typename":"CheckRun","name":"ci",'
        '"status":"COMPLETED","conclusion":"SUCCESS",'
        '"detailsUrl":"file:///tmp/check"}]}\'\n',
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(GitHubError, match="invalid detailsUrl"):
        CliGitHubBackend().status(
            PullRequest("o/r", 17, "https://github.test/o/r/pull/17", "feature", "main")
        )


@pytest.mark.parametrize("existing_state", ["open", "merged", "stale-merged"])
def test_remote_closeout_adopts_adoptable_pr_without_duplicate(
    tmp_path, bare_origin, existing_state
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"canonical-{existing_state}-existing-pr")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")

    class ExistingPRGitHub(FakeGitHub):
        def adoptable_pr(self, repo, *, head, base, head_sha):
            if not self._prs:
                self._n = 1
                self._prs[1] = FakePRState(head, base, "existing", "existing")
                seeded = PullRequest(1, f"https://github.com/{repo}/pull/1", repo, head, base)
                if existing_state in {"merged", "stale-merged"}:
                    self._do_merge(seeded)
                if existing_state == "stale-merged":
                    self._prs[1].merged_head_sha = "stale-head"
            return super().adoptable_pr(repo, head=head, base=base, head_sha=head_sha)

    github = ExistingPRGitHub(origin)
    result = run_repo_task(
        str(canonical),
        "complete-now publish preserved work",
        "engineer",
        workspace=Workspace(tmp_path / f"{existing_state}-existing-pr-worktrees"),
        github=github,
        merge_policy="none" if existing_state == "open" else "auto",
        branch=f"preserved-{existing_state}-pr",
        recorded_gate=["true"],
        dispatch_fn=make_writing_dispatch(),
        body="## What\nPublish preserved work.\n\n## Why\nAvoid duplicate PRs.\n",
        sleep=lambda _: None,
    )

    expected_number = 2 if existing_state == "stale-merged" else 1
    assert result.pr is not None and result.pr.number == expected_number
    assert github._n == expected_number
    assert result.outcome == ("pr-open" if existing_state == "open" else "merged")
    if existing_state != "open":
        assert _has_file(origin, "main", "CHANGE.txt")


@pytest.mark.parametrize("failure_mode", ["incomplete", "invalid", "exception"])
def test_pr_author_failure_retries_once_and_surfaces_underlying_error(
    tmp_path, bare_origin, failure_mode
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-drafting-retry")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    github = FakeGitHub(origin)
    attempts = 0
    writing = make_writing_dispatch()

    def failing_author(persona, task, *, project_dir, **kwargs):
        nonlocal attempts
        if persona != "pr-author":
            return writing(persona, task, project_dir=project_dir, **kwargs)
        attempts += 1
        if failure_mode == "exception":
            raise DispatchError("harness exited 17: authentication rejected")
        if failure_mode == "invalid":
            output = task.split(
                "Write the final body, and nothing else, to this absolute path:\n", 1
            )[1].splitlines()[0]
            Path(output).write_text("not a template", encoding="utf-8")
            return Report(persona, 0, True, False, 1, [], {}, {}, "")
        return Report(
            persona=persona,
            exit_code=2,
            completed=False,
            stopped_early=False,
            assistant_turns=0,
            verdicts=[],
            usage={},
            raw={},
            stderr="provider launch failed: subscription unavailable",
            outcome_detail="harness exited 17: authentication rejected",
            max_turns=2,
        )

    journal = open_journal(tmp_path / "drafting-run", RunId("drafting-retry"), 1)
    result = run_repo_task(
        str(canonical),
        "## What\ncomplete-now write-change.\n\n"
        "## Why\nKeep publication reliable.\n\n"
        "## Acceptance criteria\n- The change is published.",
        "engineer",
        workspace=Workspace(tmp_path / "drafting-retry-worktrees"),
        github=github,
        merge_policy="none",
        branch="drafting-retry",
        recorded_gate=["true"],
        dispatch_fn=failing_author,
        journal=NodeJournal(journal, NodeId("publish"), RunId("drafting-retry"), 1),
    )

    assert result.outcome == "pr-open" and attempts == 2
    assert result.pr is not None
    assert github._prs[result.pr.number].body == (
        "## What\ncomplete-now write-change.\n\n## Why\nKeep publication reliable.\n"
    )
    assert result.follow_ups is not None
    expected = (
        "invalid or empty body"
        if failure_mode == "invalid"
        else (
            "drafting error: harness exited 17: authentication rejected"
            if failure_mode == "exception"
            else "harness exited 17: authentication rejected"
        )
    )
    assert f"attempt 1: {expected}" in result.follow_ups
    assert f"attempt 2: {expected}" in result.follow_ups
    assert result_payload(result)["follow_ups"] == result.follow_ups
    assert result.follow_ups in result.summary()
    fallback = next(event for event in journal.events() if event.kind == "pr-drafting-fallback")
    assert fallback.detail["attempts"] == 2
    assert expected in fallback.detail["reason"]


def test_pr_author_failure_does_not_block_human_draft_checkpoint(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-human-drafting-retry")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    attempts = 0
    writing = make_writing_dispatch()

    def failing_author(persona, task, *, project_dir, **kwargs):
        nonlocal attempts
        if persona == "pr-author":
            attempts += 1
            raise DispatchError("drafting provider unavailable")
        return writing(persona, task, project_dir=project_dir, **kwargs)

    result = run_repo_task(
        str(canonical),
        workspace=Workspace(tmp_path / "human-drafting-retry-worktrees"),
        github=FakeGitHub(origin),
        merge_policy="none",
        branch="human-drafting-retry",
        recorded_gate=["true"],
        dispatch_fn=failing_author,
        steps=[
            Step("implement", "engineer", "complete-now write-change"),
            Step("approve", task="Approve publication.", kind="human", deps=["implement"]),
        ],
    )

    assert result.outcome == "waiting-human" and result.pr is not None
    assert attempts == 2
    assert result.follow_ups is not None
    assert "drafting provider unavailable" in result.follow_ups


def test_pr_author_failure_preserves_worker_assessment_in_surfaced_output(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-drafting-assessment")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    writing = make_writing_dispatch()

    def assessed_worker_with_failing_author(persona, task, *, project_dir, **kwargs):
        if persona == "pr-author":
            raise DispatchError("drafting provider unavailable")
        report = writing(persona, task, project_dir=project_dir, **kwargs)
        report.assessment = "Worker assessment: inspect the migration edge case."
        return report

    result = run_repo_task(
        str(canonical),
        "complete-now write-change",
        "engineer",
        workspace=Workspace(tmp_path / "drafting-assessment-worktrees"),
        github=FakeGitHub(origin),
        merge_policy="none",
        branch="drafting-assessment",
        recorded_gate=["true"],
        dispatch_fn=assessed_worker_with_failing_author,
    )

    assert result.outcome == "pr-open" and result.pr is not None
    surfaced = result_payload(result)["follow_ups"]
    assert surfaced == (
        "Worker assessment: inspect the migration edge case.\n"
        "pr-author drafting failed: attempt 1: drafting error: drafting provider unavailable; "
        "attempt 2: drafting error: drafting provider unavailable"
    )


def test_pr_author_successful_retry_publishes_drafted_body(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-drafting-recovery")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    attempts = 0
    writing = make_writing_dispatch()

    def recovering_author(persona, task, *, project_dir, **kwargs):
        nonlocal attempts
        if persona != "pr-author":
            return writing(persona, task, project_dir=project_dir, **kwargs)
        attempts += 1
        if attempts == 1:
            return Report(persona, 1, False, False, 2, [], {}, {}, "transient harness error")
        output = task.split("Write the final body, and nothing else, to this absolute path:\n", 1)[
            1
        ].splitlines()[0]
        Path(output).write_text(
            "## What\nRecovered drafted body.\n\n## Why\nThe retry succeeded.\n",
            encoding="utf-8",
        )
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    github = FakeGitHub(origin)
    result = run_repo_task(
        str(canonical),
        "complete-now write-change",
        "engineer",
        workspace=Workspace(tmp_path / "drafting-recovery-worktrees"),
        github=github,
        merge_policy="none",
        branch="drafting-recovery",
        recorded_gate=["true"],
        dispatch_fn=recovering_author,
    )

    assert result.outcome == "pr-open" and attempts == 2
    assert result.pr is not None
    assert github._prs[result.pr.number].body == (
        "## What\nRecovered drafted body.\n\n## Why\nThe retry succeeded.\n"
    )
    assert result.follow_ups is None


def test_verify_via_ci_iterates_real_dispatch_then_requires_green_branch_ci(
    tmp_path, bare_origin, command_base, personas_dir, monkeypatch, capsys
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-ci")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    push_log = tmp_path / "ci-pushes.log"
    hook = origin / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        f'  git show "$new:CI_STATE.txt" 2>/dev/null >> {shlex.quote(str(push_log))} || true\n'
        "done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    github = FakeGitHub(origin)
    monkeypatch.setattr(lifecycle_module, "CliGitHubBackend", lambda: github)

    green_rc = graph_module.main(
        [
            str(
                _one_node_lifecycle_plan(
                    tmp_path,
                    canonical,
                    task="ci-iterate exercise authoritative CI",
                    name="ci-green",
                )
            ),
            "--no-record",
            "--verify-via-ci",
            "--workspace",
            str(tmp_path / "ci-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--poll-interval",
            "0",
            "--format",
            "json",
        ]
    )
    green = json.loads(capsys.readouterr().out)["results"]["solo"]

    assert green_rc == 0 and green["outcome"] == "merged", green["detail"]
    assert push_log.read_text(encoding="utf-8").splitlines()[:2] == ["RED", "GREEN"]
    assert _has_file(origin, "main", "CI_STATE.txt")
    assert github._n == 1  # CI pre-verification and publication reuse one PR

    red_github = FakeGitHub(origin, fail_checks=True)
    red = run_repo_task(
        str(canonical),
        "complete-now write-change leave CI red",
        "engineer",
        workspace=Workspace(tmp_path / "red-worktrees"),
        github=red_github,
        verify_via_ci=True,
        base_path=command_base(max_turns=2),
        persona_dir=personas_dir,
        sleep=lambda _: None,
    )

    assert red.outcome == "not-completed"
    assert "ci=FAILURE" in red.detail
    assert red.pr is not None


def test_verify_via_ci_real_cli_rejects_local_and_tracked_node_can_opt_out(
    tmp_path, bare_origin, command_base, personas_dir, monkeypatch, capsys
) -> None:
    local_origin = bare_origin()
    local_checkout = gitops.clone(local_origin, tmp_path / "local-ci-checkout")
    Registry().register(str(local_checkout), workflow="local", repo_type="single-owner")

    rejected = graph_module.main(
        [
            str(
                _one_node_lifecycle_plan(
                    tmp_path,
                    local_checkout,
                    task="complete-now write-change",
                    name="local-ci-rejection",
                )
            ),
            "--no-record",
            "--verify-via-ci",
            "--workspace",
            str(tmp_path / "local-cli-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    rejected_payload = json.loads(capsys.readouterr().out)["results"]["solo"]
    assert rejected == 1
    assert "remote GitHub/PR workflow" in rejected_payload["detail"]

    remote_origin = bare_origin()
    remote_checkout = gitops.clone(remote_origin, tmp_path / "remote-ci-checkout")
    Registry().register(str(remote_checkout), workflow="remote", repo_type="single-owner")
    github = FakeGitHub(remote_origin, fail_checks=True)
    monkeypatch.setattr(lifecycle_module, "CliGitHubBackend", lambda: github)
    plan = tmp_path / "verify-via-ci-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "opt-out",
                        "repo": str(remote_checkout),
                        "persona": "engineer",
                        "task": "complete-now write-change",
                        "verify_via_ci": False,
                        "merge_policy": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    graph_rc = graph_module.main(
        [
            str(plan),
            "--verify-via-ci",
            "--no-record",
            "--workspace",
            str(tmp_path / "graph-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    graph_payload_result = json.loads(capsys.readouterr().out)
    assert graph_rc == 0
    assert graph_payload_result["results"]["opt-out"]["outcome"] == "pr-open"

    inherited_github = FakeGitHub(remote_origin)
    monkeypatch.setattr(lifecycle_module, "CliGitHubBackend", lambda: inherited_github)
    inherited_plan = tmp_path / "inherited-verify-via-ci-plan.json"
    inherited_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "ci-default",
                        "repo": str(remote_checkout),
                        "persona": "engineer",
                        "task": "complete-now write-change",
                        "recorded_gate": ["false"],
                        "merge_policy": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    inherited_rc = graph_module.main(
        [
            str(inherited_plan),
            "--verify-via-ci",
            "--no-record",
            "--workspace",
            str(tmp_path / "inherited-graph-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    inherited_payload = json.loads(capsys.readouterr().out)
    assert inherited_rc == 0
    assert inherited_payload["results"]["ci-default"]["outcome"] == "pr-open"

    node_opt_in_plan = tmp_path / "node-verify-via-ci-plan.json"
    node_opt_in_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "ci-node-opt-in",
                        "repo": str(remote_checkout),
                        "persona": "engineer",
                        "task": "complete-now write-unique-change node CI opt-in",
                        "verify_via_ci": True,
                        "recorded_gate": ["false"],
                        "merge_policy": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    node_opt_in_rc = graph_module.main(
        [
            str(node_opt_in_plan),
            "--no-record",
            "--workspace",
            str(tmp_path / "node-opt-in-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    node_opt_in_payload = json.loads(capsys.readouterr().out)
    assert node_opt_in_rc == 0
    assert node_opt_in_payload["results"]["ci-node-opt-in"]["outcome"] == "pr-open"


@pytest.mark.parametrize(
    ("check_states", "expected_detail"),
    [
        (("PENDING",), "ci=PENDING"),
        ((None,), "required checks: []"),
    ],
)
def test_verify_via_ci_rejects_unsettled_or_absent_required_checks(
    tmp_path, bare_origin, check_states, expected_detail
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-unsettled-ci")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")

    result = run_repo_task(
        str(canonical),
        "Leave authoritative CI incomplete.",
        "engineer",
        workspace=Workspace(tmp_path / "unsettled-ci-worktrees"),
        github=FakeGitHub(origin, check_states=check_states),
        verify_via_ci=True,
        dispatch_fn=make_writing_dispatch(filename="unsettled.txt"),
        sleep=lambda _: None,
    )

    assert result.outcome == "not-completed"
    assert expected_detail in result.detail


def test_team_default_opens_ready_for_review_pr_without_polling(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-team")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    github = FakeGitHub(origin, fail_checks=True)

    result = run_repo_task(
        str(canonical),
        "Open a team-owned change for review.",
        "engineer",
        workspace=Workspace(tmp_path / "team-worktrees"),
        github=github,
        dispatch_fn=make_writing_dispatch(filename="team.txt"),
        recorded_gate=["true"],
    )

    assert result.ok and result.outcome == "pr-open", result.detail
    assert result.repository_type == "team"
    assert result.publication_workflow == "remote" and result.merge_policy == "none"
    assert result.pr is not None and result.pr.base == "main"
    assert not _has_file(origin, "main", "team.txt")
    assert _has_file(origin, result.branch, "team.txt")


def test_team_explicit_auto_merges_remote_pr(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-team-auto")
    Registry().register(str(canonical), workflow="remote", repo_type="team")

    result = run_repo_task(
        str(canonical),
        "Merge an explicitly automated team change.",
        "engineer",
        workspace=Workspace(tmp_path / "team-auto-worktrees"),
        github=FakeGitHub(origin),
        dispatch_fn=make_writing_dispatch(filename="team-auto.txt"),
        recorded_gate=["true"],
        merge_policy="auto",
        sleep=lambda _: None,
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert result.merge_policy == "auto" and result.publication_workflow == "remote"
    assert _has_file(origin, "main", "team-auto.txt")


@pytest.mark.parametrize(("repo_type", "should_wait"), [("single-owner", True), ("team", False)])
def test_remote_lifecycle_routes_only_single_owner_auto_merge_through_queue(
    tmp_path, bare_origin, repo_type, should_wait
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"canonical-routing-{repo_type}")
    Registry().register(str(canonical), workflow="remote", repo_type=repo_type)

    result = _run_while_merge_turn_is_held(
        canonical,
        lambda: run_repo_task(
            str(canonical),
            "Publish an automated remote change.",
            "engineer",
            workspace=Workspace(tmp_path / f"routing-{repo_type}-worktrees"),
            github=FakeGitHub(origin),
            dispatch_fn=make_writing_dispatch(filename=f"routing-{repo_type}.txt"),
            recorded_gate=["true"],
            merge_policy="auto",
            sleep=lambda _: None,
        ),
        should_wait=should_wait,
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert result.repository_type == repo_type and result.merge_policy == "auto"
    assert _has_file(origin, "main", f"routing-{repo_type}.txt")


@pytest.mark.parametrize(("repo_type", "should_wait"), [("single-owner", True), ("team", False)])
def test_remote_recovery_routes_only_single_owner_auto_merge_through_queue(
    tmp_path, bare_origin, repo_type, should_wait
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"canonical-recovery-routing-{repo_type}")
    Registry().register(str(canonical), workflow="remote", repo_type=repo_type, gate="true")
    branch = f"feature/recovery-routing-{repo_type}"
    preserved = run_repo_task(
        str(canonical),
        "Preserve an incomplete remote change.",
        "engineer",
        workspace=Workspace(tmp_path / f"preserve-routing-{repo_type}-worktrees"),
        branch=branch,
        dispatch_fn=make_writing_dispatch(
            filename=f"recovery-routing-{repo_type}.txt", completed=False
        ),
        recorded_gate=["true"],
    )
    assert preserved.outcome == "not-completed" and preserved.resume is not None

    recovered = _run_while_merge_turn_is_held(
        canonical,
        lambda: recover_repo(
            canonical,
            branch,
            workspace_root=tmp_path / f"recovery-routing-{repo_type}-worktrees",
            github=FakeGitHub(origin),
            recorded_gate=["true"],
            merge_policy="auto",
        ),
        should_wait=should_wait,
    )

    assert recovered.ok and recovered.outcome == "merged", recovered.detail
    assert recovered.repo_type == repo_type and recovered.merge_policy == "auto"
    assert _has_file(origin, "main", f"recovery-routing-{repo_type}.txt")


def test_local_single_owner_none_opens_pr_without_mutating_stored_workflow(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-local-none")
    registry = Registry()
    registry.register(str(canonical), workflow="local", repo_type="single-owner")

    result = run_repo_task(
        str(canonical),
        "Temporarily publish local work for review.",
        "engineer",
        workspace=Workspace(tmp_path / "local-none-worktrees"),
        github=FakeGitHub(origin),
        dispatch_fn=make_writing_dispatch(filename="local-none.txt"),
        recorded_gate=["true"],
        merge_policy="none",
    )

    assert result.ok and result.outcome == "pr-open", result.detail
    assert result.publication_workflow == "remote" and result.merge_policy == "none"
    stored = Registry().identity_for_checkout(canonical)
    assert stored is not None and stored.workflow == "local"
    assert not _has_file(origin, "main", "local-none.txt")


@pytest.mark.parametrize(
    (
        "repo_type",
        "identity_workflow",
        "merge_policy",
        "expected_workflow",
        "expected_policy",
        "expected_outcome",
    ),
    [
        ("single-owner", "local", None, "local", "direct", "merged"),
        ("single-owner", "local", "auto", "local", "direct", "merged"),
        ("single-owner", "local", "direct", "local", "direct", "merged"),
        ("single-owner", "local", "none", "remote", "none", "pr-open"),
        ("single-owner", "remote", None, "remote", "auto", "merged"),
        ("single-owner", "remote", "auto", "remote", "auto", "merged"),
        ("single-owner", "remote", "direct", "remote", "direct", "merged"),
        ("single-owner", "remote", "none", "remote", "none", "pr-open"),
        ("team", "local", None, "remote", "none", "pr-open"),
        ("team", "local", "auto", "remote", "auto", "merged"),
        ("team", "local", "direct", "remote", "direct", "merged"),
        ("team", "local", "none", "remote", "none", "pr-open"),
        ("team", "remote", None, "remote", "none", "pr-open"),
        ("team", "remote", "auto", "remote", "auto", "merged"),
        ("team", "remote", "direct", "remote", "direct", "merged"),
        ("team", "remote", "none", "remote", "none", "pr-open"),
    ],
)
def test_public_lifecycle_type_workflow_policy_matrix(
    tmp_path,
    bare_origin,
    repo_type,
    identity_workflow,
    merge_policy,
    expected_workflow,
    expected_policy,
    expected_outcome,
) -> None:
    """Every supported decision reaches the expected real Git publication boundary."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-policy-matrix")
    stored_type = "single-owner" if identity_workflow == "local" else repo_type
    registry = Registry()
    registry.register(str(canonical), workflow=identity_workflow, repo_type=stored_type)
    github = FakeGitHub(origin, fail_checks=expected_outcome == "pr-open")

    result = run_repo_task(
        str(canonical),
        "Exercise one effective publication decision.",
        "engineer",
        workspace=Workspace(tmp_path / "policy-matrix-worktrees"),
        github=github,
        repo_type=repo_type,
        merge_policy=merge_policy,
        dispatch_fn=make_writing_dispatch(filename="matrix.txt"),
        recorded_gate=["true"],
        sleep=lambda _: None,
    )

    assert result.ok and result.outcome == expected_outcome, result.detail
    assert result.repository_type == repo_type
    assert result.publication_workflow == expected_workflow
    assert result.merge_policy == expected_policy
    assert (github._n == 0) is (expected_workflow == "local")
    assert _has_file(origin, "main", "matrix.txt") is (expected_outcome == "merged")
    rendered = result.summary()
    assert f"repository type: {repo_type}" in rendered
    assert f"publication workflow: {expected_workflow}" in rendered
    assert f"merge policy: {expected_policy}" in rendered
    if repo_type == "team" and identity_workflow == "local":
        stored = Registry().identity_for_checkout(canonical)
        assert stored is not None
        assert stored.repo_type == "single-owner" and stored.workflow == "local"


def test_local_repo_direct_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    prior_base = _tip(origin, "main")
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),  # a local path → local direct-merge strategy is auto-selected
        "Add a change file.",
        "engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],  # the bar this run records; the pre-push hook is what runs
    )
    assert result.ok, result.detail
    assert result.outcome == "merged"
    assert result.base_branch == "main"
    assert _has_file(origin, "main", "feature.txt")  # the change really landed on origin main
    canonical = ws.execution_checkout(normalize_repo(str(origin)))
    assert gitops.current_branch(canonical) == "main"
    assert gitops.head_sha(canonical) == _tip(origin, "main")
    landed = _tip(origin, "main")
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "rev-list", "--count", f"{prior_base}..{landed}"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        == "1"
    )
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "show", "-s", "--format=%P", landed],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        == prior_base
    )
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "show", "-s", "--format=%s", landed],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        == "chore: Add a change file."
    )
    branch_commits = {
        commit.sha for commit in gitops.log_delta(canonical, prior_base, result.branch)
    }
    base_commits = set(
        subprocess.run(
            ["git", "-C", str(origin), "rev-list", "main"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
    )
    assert landed not in branch_commits
    assert branch_commits.isdisjoint(base_commits)


def _multi_commit_dispatch(*messages: str):
    """A dispatch that lands one real commit per message in the worktree."""

    def dispatch_fn(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        worktree = Path(project_dir)
        for index, message in enumerate(messages):
            (worktree / f"change{index}.txt").write_text(f"{index}\n", encoding="utf-8")
            gitops.add_all(worktree)
            gitops.commit(worktree, message)
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    return dispatch_fn


def test_multi_commit_branch_publishes_a_subject_that_names_the_change(
    tmp_path, bare_origin
) -> None:
    """One squash commit reaches the base, so its subject names the change it made.

    Concatenating every description is what published `feat: ...; read the r…` — the
    branch's steps do not fit in one subject, and its own history already records them.
    """
    origin = bare_origin()
    named = "feat: expose the captured session output through a public reader"
    result = run_repo_task(
        str(origin),
        "## What\nExpose captured session output.\n\n## Why\nOperators cannot read it.\n",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=_multi_commit_dispatch(
            "docs: explain how a failed session's captured output is retained across a later retry",
            named,
            "fix: retain the captured output of a session that failed before its very first turn",
        ),
        recorded_gate=["true"],
    )

    assert result.ok, result.detail
    assert result.outcome == "merged"
    subject = _subject(origin, "main")
    assert subject == named
    assert "…" not in subject and "; " not in subject
    assert "explain how" not in subject and "retain the captured" not in subject


_OVERLONG_COMMITS = (
    "feat: expose every captured session's output through a public reader API "
    "that operators can call directly",
    "fix: retain the captured output of a session that failed before it produced "
    "its very first turn of work",
)
_FITTING_TASK = "## What\nExpose captured session output.\n\n## Why\nOperators cannot read it.\n"
_OVERLONG_TASK = (
    "## What\nExpose every captured session's output through a public reader API that "
    "operators can call directly.\n\n## Why\nOperators cannot read it.\n"
)


@pytest.mark.parametrize(
    "messages, task, expected",
    [
        # The type still carries the branch's release semantics; only the description moved.
        (_OVERLONG_COMMITS, _FITTING_TASK, "feat: Expose captured session output."),
        # A scope is a Conventional Commit guarantee, so it survives the fall-through
        # rather than being traded for room the description would have fit into.
        (
            (f"fix(capture): {_OVERLONG_COMMITS[1].partition(': ')[2]}",),
            _FITTING_TASK,
            "fix(capture): Expose captured session output.",
        ),
        # The release-facing marker describes the branch, so it outlives the description.
        (
            (f"feat!: {_OVERLONG_COMMITS[0].partition(': ')[2]}",),
            _FITTING_TASK,
            "feat!: Expose captured session output.",
        ),
        # No usable commit subject at all: the non-releasing fallback names the task.
        (("Update the reader",), _FITTING_TASK, "chore: Expose captured session output."),
        # The same, with the branch's only breaking signal in a footer.
        (
            ("Update the reader\n\nBREAKING CHANGE: the old reader is gone",),
            _FITTING_TASK,
            "chore!: Expose captured session output.",
        ),
    ],
    ids=[
        "task-name",
        "scope-preserved",
        "breaking-preserved",
        "no-usable-commit",
        "breaking-footer-only",
    ],
)
def test_descriptions_over_the_limit_publish_a_whole_name_not_an_elision(
    tmp_path, bare_origin, messages: tuple[str, ...], task: str, expected: str
) -> None:
    """What reaches the base branch when a description cannot fit: never a cut one.

    The task's own name for the change publishes instead, behind the type, scope, and
    breaking marker the branch's commits carry — none of which is dropped to make room.
    """
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        task,
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=_multi_commit_dispatch(*messages),
        recorded_gate=["true"],
    )

    assert result.ok, result.detail
    subject = _subject(origin, "main")
    assert subject == expected
    assert "…" not in subject and len(subject) <= lifecycle_module._SUBJECT_LIMIT


@pytest.mark.parametrize(
    "messages",
    [
        _OVERLONG_COMMITS,
        ("Update the reader",),
        ("Update the reader\n\nBREAKING CHANGE: the old reader is gone",),
        # The description would fit without its scope — which is not room to take.
        (f"fix({'session-capture-' * 2}reader): retain a failed session's output",),
    ],
    ids=["all-commits", "no-usable-commit", "breaking-footer-only", "scope-crowds-description"],
)
def test_a_branch_with_no_publishable_subject_refuses_instead_of_publishing_one(
    tmp_path, bare_origin, messages: tuple[str, ...]
) -> None:
    """Nothing on the branch or in the prose names the change within the limit.

    Publishing `chore: orchestrated change` here would be the same defect as a cut
    subject in a different costume, so the run settles as an error naming the limit and
    the ways out, and the base branch is left exactly as it was.
    """
    origin = bare_origin()
    before = _tip(origin, "main")
    result = run_repo_task(
        str(origin),
        _OVERLONG_TASK,
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=_multi_commit_dispatch(*messages),
        recorded_gate=["true"],
    )

    assert not result.ok and result.outcome == "error"
    assert f"at most {lifecycle_module._SUBJECT_LIMIT} characters" in result.detail
    assert "shorten a commit subject" in result.detail and "explicit title" in result.detail
    assert _tip(origin, "main") == before
    assert not _has_file(origin, "main", "change0.txt")


def test_a_refused_publication_still_commits_the_agents_work_to_the_branch(
    tmp_path, bare_origin
) -> None:
    """Refusal is publication's answer, never a branch commit's: that would lose work.

    The agent leaves its change uncommitted and nothing on the branch or in the prose can
    name it, so the step commit says only that a change happened. Publication declines to
    borrow that filler as a subject and refuses, with the work safe on the branch.
    """
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    before = _tip(origin, "main")

    result = run_repo_task(
        str(origin),
        _OVERLONG_TASK,
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="unnamed.txt"),
        recorded_gate=["true"],
    )

    assert not result.ok and result.outcome == "error"
    assert f"at most {lifecycle_module._SUBJECT_LIMIT} characters" in result.detail
    assert _tip(origin, "main") == before
    clone = workspace.clone_dir(normalize_repo(str(origin)))
    assert _has_file(clone, result.branch, "unnamed.txt")
    assert _subject(clone, result.branch) == "chore: orchestrated change"


def test_covered_lifecycle_uses_push_gate_without_orchestrator_gate_run(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    hook_log = tmp_path / "pre-push-gates.log"
    # Record which ref each gated push targets, so the journey says what the merge
    # path actually covers rather than asserting an opaque count.
    install_pre_push_hook(
        canonical,
        f"""while read -r _local _lsha remote _rsha; do
  printf '%s\\n' "$remote" >> {shlex.quote(str(hook_log))}
done""",
    )

    result = run_repo_task(
        str(origin),
        "Publish through the repository merge-path gate.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="covered.txt"),
        # This legacy override would fail if lifecycle still invoked run_gate.
        recorded_gate=["false"],
    )

    assert result.ok and result.outcome == "merged", result.detail
    # The orchestrator ran no gate of its own — `recorded_gate=["false"]` would have
    # failed if it had. What it recorded is the merge path's own run.
    assert result.verify is not None and result.verify.ok
    assert result.verify.command == ["false"]
    gated = hook_log.read_text(encoding="utf-8").splitlines()
    # The feature branch and the squashed publication onto base are gated. Mirroring
    # the branch into the registered execution checkout is a local durability copy,
    # not publication, so it deliberately does not invoke the pre-push hook.
    assert gated == [
        f"refs/heads/{result.branch}",
        "refs/heads/main",
    ], gated
    assert _has_file(origin, "main", "covered.txt")


def test_pre_push_gate_failure_preserves_completed_work_in_execution_checkout(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-gate-rejection")
    safety = gitops.clone(origin, tmp_path / "safety-gate-rejection")
    registry = Registry()
    registry.register(str(canonical), workflow="local")
    registry.register(str(safety))
    install_pre_push_hook(
        safety,
        "printf 'pre-push: complete gate failed\\n' >&2\nexit 1",
    )
    before = _tip(origin, "main")
    branch = "feature/recover-rejected-complete-work"

    result = run_repo_task(
        str(canonical),
        "Publish work rejected by the merge-path gate.",
        "engineer",
        workspace=Workspace(tmp_path / "gate-rejection-worktrees"),
        execution_checkout=safety,
        branch=branch,
        dispatch_fn=make_writing_dispatch(filename="rejected.txt"),
        recorded_gate=["true"],
    )

    assert result.outcome == "gate-failed"
    assert "repository pre-push gate rejected publication" in result.detail
    assert "complete gate failed" in result.detail
    assert str(safety) in result.detail and branch in result.detail
    assert gitops.branch_exists(safety, branch)
    preserved = subprocess.run(
        ["git", "-C", str(safety), "show", f"{branch}:rejected.txt"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert preserved.stdout == "change by engineer\n"
    assert not incomplete_commits(safety, "origin/main", branch)
    assert not gitops.branch_exists(origin, branch)
    assert _tip(origin, "main") == before

    # Preserved has to mean recoverable, not merely present: a later run of the same
    # branch, once the gate is repaired, must continue the rejected commits rather
    # than re-cut the branch off a stale base and silently rebuild without them.
    install_pre_push_hook(safety, "exit 0")
    resumed = run_repo_task(
        str(canonical),
        "Add the follow-up the repaired gate accepts.",
        "engineer",
        workspace=Workspace(tmp_path / "gate-repaired-worktrees"),
        execution_checkout=safety,
        branch=branch,
        dispatch_fn=make_writing_dispatch(filename="follow-up.txt"),
        recorded_gate=["true"],
    )

    assert resumed.ok and resumed.outcome == "merged", resumed.detail
    assert _has_file(origin, "main", "rejected.txt")
    assert _has_file(origin, "main", "follow-up.txt")


def test_gate_failure_says_so_when_rejected_work_could_not_be_preserved(
    tmp_path, bare_origin
) -> None:
    """A copy the checkout refuses is reported, not silently treated as preserved.

    The copy is deliberately fast-forward only, because a concurrent run told to use
    the same branch name would otherwise have its own only record overwritten. When
    it refuses for that reason the work really is still only in run scratch, which is
    the loss this preservation exists to prevent — so the result has to say the
    preservation did not happen rather than name a branch that does not carry it.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-unpreservable")
    safety = gitops.clone(origin, tmp_path / "safety-unpreservable")
    registry = Registry()
    registry.register(str(canonical), workflow="local")
    registry.register(str(safety))
    install_pre_push_hook(safety, "printf 'pre-push: complete gate failed\\n' >&2\nexit 1")
    branch = "feature/unpreservable-rejected-work"
    writes = make_writing_dispatch(filename="rejected.txt")

    def writes_while_another_run_claims_the_branch(
        persona: str, task: str, *, project_dir: str, **kwargs: object
    ) -> Report:
        report = writes(persona, task, project_dir=project_dir, **kwargs)
        # Another run reached the registered checkout first and put its own commit on
        # this branch name. This run's tip is not a fast-forward of it, so the copy
        # below has to refuse rather than discard that run's record.
        tree = git("rev-parse", "origin/main^{tree}", cwd=safety).strip()
        other = git("commit-tree", tree, "-p", "origin/main", "-m", "other run", cwd=safety).strip()
        git("branch", branch, other, cwd=safety)
        return report

    result = run_repo_task(
        str(canonical),
        "Publish work the registered checkout cannot take back.",
        "engineer",
        workspace=Workspace(tmp_path / "unpreservable-worktrees"),
        execution_checkout=safety,
        branch=branch,
        dispatch_fn=writes_while_another_run_claims_the_branch,
        recorded_gate=["true"],
    )

    assert result.outcome == "gate-failed"
    assert "could not preserve rejected work" in result.detail
    assert branch in result.detail and str(safety) in result.detail
    # The other run's commit is still the one the checkout carries.
    assert not _has_file(safety, branch, "rejected.txt")
    assert not _has_file(origin, "main", "rejected.txt")


# A hook that lets the feature branch through and rejects the direct base push, so
# the *second* gated push — the rebuilt squash publication tree — is the one that
# fails. Git feeds `<local-ref> <local-sha> <remote-ref> <remote-sha>` on stdin.
_REJECT_BASE_PUSH = """while read -r _local _lsha remote _rsha; do
  case "$remote" in
    refs/heads/main)
      printf 'pre-push gate: publication tree rejected\\n' >&2
      exit 1
      ;;
  esac
done"""


def test_publication_push_gate_does_not_hold_the_shared_git_lock(tmp_path, bare_origin) -> None:
    """A concurrent lifecycle can still reach the shared `.git` while the gate runs.

    The gate moved from an orchestrator-side `run_gate` into the `pre-push` hook,
    but it is the same ten-minute command: holding the shared git lock across it
    would serialize every other dispatch against this checkout for its duration.
    The hook blocks on the base push here, and the assertion is that the lock is
    acquirable meanwhile.
    """
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    started = tmp_path / "publication-gate-started"
    release = tmp_path / "publication-gate-release"
    install_pre_push_hook(
        canonical,
        f"""while read -r _local _lsha remote _rsha; do
  case "$remote" in
    refs/heads/main)
      : > {shlex.quote(str(started))}
      while test ! -f {shlex.quote(str(release))}; do sleep 0.01; done
      ;;
  esac
done""",
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_repo_task,
            str(origin),
            "Add a change while the publication gate blocks.",
            "engineer",
            workspace=workspace,
            dispatch_fn=make_writing_dispatch(filename="feature.txt"),
            recorded_gate=["true"],
        )
        deadline = e2e_deadline(15)
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists(), "the publication push never reached the pre-push gate"
        try:
            with advisory_lock(git_lock_identity(gitops.common_dir(canonical)), timeout=0.5):
                gitops.fetch(canonical)
        finally:
            release.touch()
        result = future.result(timeout=e2e_timeout(30))

    assert result.ok and result.outcome == "merged", result.detail
    assert _has_file(origin, "main", "feature.txt")


def test_publication_push_gate_failure_leaves_the_branch_and_base_intact(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    install_pre_push_hook(canonical, _REJECT_BASE_PUSH)
    before = _tip(origin, "main")

    result = run_repo_task(
        str(origin),
        "Publish a tree the base push rejects.",
        "engineer",
        workspace=workspace,
        branch="feature/publication-gate-failure",
        dispatch_fn=make_writing_dispatch(filename="publication.txt"),
        recorded_gate=["true"],
    )

    assert result.outcome == "gate-failed"
    assert "rebuilt local publication" in result.detail
    assert "publication tree rejected" in result.detail
    # The branch push passed the same hook, so the agent's work survives for recovery.
    assert _has_file(origin, "feature/publication-gate-failure", "publication.txt")
    assert _tip(origin, "main") == before


def test_recovery_publication_push_gate_failure_preserves_the_branch(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-recovery-publication-gate")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")

    preserved = run_repo_task(
        str(canonical),
        "Preserve work whose recovery publication push is rejected.",
        "engineer",
        workspace=Workspace(tmp_path / "recovery-publication-source-worktrees"),
        branch="feature/recovery-publication-gate",
        dispatch_fn=make_writing_dispatch(filename="preserved.txt", completed=False),
        recorded_gate=["true"],
    )
    assert preserved.outcome == "not-completed"
    before = _tip(origin, "main")
    install_pre_push_hook(canonical, _REJECT_BASE_PUSH)

    recovered = recover_repo(
        canonical,
        preserved.branch,
        workspace_root=tmp_path / "recovery-publication-gate-worktrees",
        recorded_gate=["true"],
    )

    assert recovered.outcome == "gate-failed"
    assert "rebuilt local publication" in recovered.detail
    assert gitops.branch_exists(canonical, preserved.branch)
    assert _tip(origin, "main") == before
    assert not _has_file(origin, "main", "preserved.txt")


def _preserve_in_execution_checkout(
    tmp_path: Path, origin: Path, name: str
) -> tuple[Path, Path, str]:
    """Leave interrupted work in the execution checkout, where the lifecycle puts it."""
    canonical = gitops.clone(origin, tmp_path / f"canonical-{name}")
    safety = gitops.clone(origin, tmp_path / f"safety-{name}")
    registry = Registry()
    registry.register(str(canonical), workflow="local", repo_type="single-owner")
    registry.register(str(safety))

    preserved = run_repo_task(
        f"local/canonical-{name}",
        "Preserve interrupted work in the isolated execution checkout.",
        "engineer",
        workspace=Workspace(tmp_path / f"{name}-worktrees"),
        execution_checkout=f"local/safety-{name}",
        branch=f"feature/{name}",
        dispatch_fn=make_writing_dispatch(filename="preserved.txt", completed=False),
        recorded_gate=["true"],
    )

    assert preserved.outcome == "not-completed"
    # The defect this covers: a branch that reaches publication on its first try has
    # never been pushed, so the publication checkout has never heard of it.
    assert not gitops.branch_exists(canonical, preserved.branch)
    assert gitops.branch_exists(safety, preserved.branch)
    return canonical, safety, preserved.branch


def test_recovery_publishes_a_branch_only_the_execution_checkout_has(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical, safety, branch = _preserve_in_execution_checkout(tmp_path, origin, "execution-only")

    recovered = recover_repo(
        canonical,
        branch,
        workspace_root=tmp_path / "execution-only-recovery",
        recorded_gate=["true"],
    )

    assert recovered.ok and recovered.outcome == "merged", recovered.detail
    assert _has_file(origin, "main", "preserved.txt")
    # Fetching the ref in is allowed; working in the publication checkout is not.
    assert gitops.current_branch(canonical) == "main"
    assert not gitops.is_dirty(canonical)


def test_preserved_work_survives_task_prose_too_long_for_a_subject(tmp_path, bare_origin) -> None:
    """A marker names the work from task prose, but must never fail on prose that overruns.

    The marker keeps its `(incomplete step)` text through that fallback — a marker whose
    trailer is missing is still recognized by it — and the recovery still publishes.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-long-prose")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")

    preserved = run_repo_task(
        str(canonical),
        _OVERLONG_TASK,
        "engineer",
        workspace=Workspace(tmp_path / "long-prose-worktrees"),
        branch="feature/long-prose",
        dispatch_fn=make_writing_dispatch(filename="preserved.txt", completed=False),
        recorded_gate=["true"],
    )
    assert preserved.outcome == "not-completed"
    assert _subject(canonical, preserved.branch) == "chore: orchestrated change (incomplete step)"
    assert incomplete_commits(canonical, "main", preserved.branch)

    recovered = recover_repo(
        canonical,
        preserved.branch,
        workspace_root=tmp_path / "long-prose-recovery",
        recorded_gate=["true"],
    )

    assert recovered.ok and recovered.outcome == "merged", recovered.detail
    assert _has_file(origin, "main", "preserved.txt")
    published = subprocess.run(
        ["git", "-C", str(origin), "log", "-1", "--format=%B", "main"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "…" not in published.splitlines()[0]
    assert RECOVERY_TRAILER in published  # the incomplete step is still on the record


def test_recovery_accepts_an_execution_checkout_the_identity_does_not_know(
    tmp_path, bare_origin
) -> None:
    """The branch can live somewhere the registry never recorded; say so explicitly."""
    origin = bare_origin()
    canonical, safety, branch = _preserve_in_execution_checkout(tmp_path, origin, "explicit-exec")
    unregistered = tmp_path / "moved-execution-checkout"
    safety.rename(unregistered)

    recovered = recover_repo(
        canonical,
        branch,
        workspace_root=tmp_path / "explicit-exec-recovery",
        execution_checkout=unregistered,
        recorded_gate=["true"],
    )

    assert recovered.ok and recovered.outcome == "merged", recovered.detail
    assert _has_file(origin, "main", "preserved.txt")


def test_recovery_rejects_an_execution_checkout_of_another_repository(
    tmp_path, bare_origin
) -> None:
    """Reading a branch out of the wrong tree would publish the wrong work."""
    origin = bare_origin()
    canonical, _safety, branch = _preserve_in_execution_checkout(tmp_path, origin, "foreign-exec")
    stranger = gitops.clone(bare_origin(), tmp_path / "a-different-repository")

    with pytest.raises(RegistryError) as failure:
        recover_repo(
            canonical,
            branch,
            workspace_root=tmp_path / "foreign-exec-recovery",
            execution_checkout=stranger,
            recorded_gate=["true"],
        )

    assert "is not a git checkout of the repository identity" in str(failure.value)


def test_recovery_of_a_missing_branch_names_every_checkout_it_searched(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    canonical, safety, _branch = _preserve_in_execution_checkout(tmp_path, origin, "missing-branch")

    with pytest.raises(RegistryError) as failure:
        recover_repo(
            canonical,
            "feature/never-existed",
            workspace_root=tmp_path / "missing-branch-recovery",
            recorded_gate=["true"],
        )

    detail = str(failure.value)
    assert "does not exist in any registered checkout" in detail
    assert str(canonical) in detail and str(safety) in detail
    assert "--execution-checkout" in detail


def test_recovery_of_a_completed_branch_hands_over_to_integrate(tmp_path, bare_origin) -> None:
    """Picking the wrong verb has to name the right one, not just refuse."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-completed-branch")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    tree = gitops.worktree_add(
        canonical,
        tmp_path / "completed-branch-tree",
        "feature/already-complete",
        base="origin/main",
    )
    (tree / "complete.txt").write_text("complete\n", encoding="utf-8")
    gitops.add_all(tree)
    gitops.commit(tree, "feat: complete work that never needed recovery")
    gitops.worktree_remove(canonical, tree)

    with pytest.raises(RegistryError) as failure:
        recover_repo(
            canonical,
            "feature/already-complete",
            workspace_root=tmp_path / "completed-branch-recovery",
            recorded_gate=["true"],
        )

    detail = str(failure.value)
    assert "carries no lifecycle-preserved incomplete provenance" in detail
    assert f"just integrate feature/already-complete --repo {canonical}" in detail


def test_three_failing_recoveries_of_one_branch_report_three_distinct_causes(
    tmp_path, bare_origin
) -> None:
    """The defect this closes: one opaque sentence for three unrelated failures."""
    origin = bare_origin()
    canonical, _safety, branch = _preserve_in_execution_checkout(tmp_path, origin, "three-causes")
    workspace_root = tmp_path / "three-causes-recovery"

    with pytest.raises(RegistryError) as missing:
        recover_repo(
            canonical, "feature/typo", workspace_root=workspace_root, recorded_gate=["true"]
        )

    install_pre_push_hook(canonical, "printf 'pre-push: llmlint stale finding\\n' >&2\nexit 1")
    stale = recover_repo(
        canonical, branch, workspace_root=workspace_root, recorded_gate=["just gate"]
    )

    install_pre_push_hook(
        canonical, "printf 'pre-push: ruff found a dead CLI option\\n' >&2\nexit 1"
    )
    dead_option = recover_repo(
        canonical, branch, workspace_root=workspace_root, recorded_gate=["just gate"]
    )

    causes = [str(missing.value), stale.detail, dead_option.detail]
    assert len(set(causes)) == 3, causes
    assert "does not exist in any registered checkout" in causes[0]
    assert stale.outcome == dead_option.outcome == "gate-failed"
    # Each rejection keeps its own output, so "the same failure again" is checkable
    # rather than assumed.
    assert stale.gate_log is not None and stale.gate_log == dead_option.gate_log
    preserved = Path(stale.gate_log).read_text(encoding="utf-8")
    assert "pre-push: llmlint stale finding" in preserved
    assert "pre-push: ruff found a dead CLI option" in preserved
    assert preserved.count("verdict: FAILED") == 2
    assert stale.gate_log in stale.detail


def test_a_rejecting_hook_that_echoes_a_credential_records_only_its_name(
    tmp_path, bare_origin, monkeypatch
) -> None:
    """Preserved evidence outlives its terminal, so it must never carry a token.

    The gate log and the recorded detail are both durable records of whatever the
    pre-push hook wrote, so this drives a real hook that echoes an environment
    credential and checks both records.
    """
    token = "sk-ant-oat01-not-a-real-credential"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", token)
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    install_pre_push_hook(
        canonical,
        "printf 'pre-push: gate failed while authenticating with %s\\n' "
        '"$CLAUDE_CODE_OAUTH_TOKEN" >&2\nexit 1',
    )
    run_dir = tmp_path / "redaction-run"
    journal = open_journal(run_dir, RunId("redaction"), 1)
    scope = NodeJournal(journal, NodeId("publish"), RunId("redaction"), 1)

    result = run_repo_task(
        str(origin),
        "Publish work the gate rejects while printing a credential.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="rejected.txt"),
        recorded_gate=["just", "gate"],
        journal=scope,
    )

    assert result.outcome == "gate-failed"
    assert result.verify is not None and result.verify.log_path
    preserved = Path(result.verify.log_path).read_text(encoding="utf-8")
    assert token not in preserved and token not in result.detail
    assert "<redacted:CLAUDE_CODE_OAUTH_TOKEN>" in preserved
    assert "<redacted:CLAUDE_CODE_OAUTH_TOKEN>" in result.detail
    assert "gate failed while authenticating with" in preserved


def test_a_publication_that_fails_before_any_gate_preserves_its_error(
    tmp_path, bare_origin
) -> None:
    """A publication can fail with no gate to rule on it and no verdict to record.

    The settled result must still carry the error rather than only the fact of
    failure. A real `post-receive` hook removes the base ref out from under the
    rebuild, so `origin/main` is genuinely gone when it is next resolved.
    """
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    install_pre_push_hook(canonical)
    receive = origin / "hooks" / "post-receive"
    receive.write_text(
        "#!/bin/sh\n"
        "while read -r _old _new ref; do\n"
        '  case "$ref" in\n'
        "    refs/heads/main) ;;\n"
        "    *) git update-ref -d refs/heads/main ;;\n"
        "  esac\n"
        "done\n",
        encoding="utf-8",
    )
    receive.chmod(0o755)
    run_dir = tmp_path / "publication-failure-run"
    journal = open_journal(run_dir, RunId("publication-failure"), 1)
    scope = NodeJournal(journal, NodeId("publish"), RunId("publication-failure"), 1)

    result = run_repo_task(
        str(origin),
        "Publish into a base that disappears mid-rebuild.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="doomed.txt"),
        recorded_gate=["just", "gate"],
        journal=scope,
    )

    assert result.outcome == "error", result.detail
    # The branch push was gated and passed; the failure came after, and used to
    # leave the settled run with that stale verdict as its only evidence.
    assert result.verify is not None and result.verify.ok
    (failure,) = [event for event in journal.events() if event.kind == "publication-failed"]
    assert failure.detail["outcome"] == "GitError"
    assert str(failure.detail["output_tail"]).strip()
    preserved = Path(str(failure.detail["log_path"])).read_text(encoding="utf-8")
    assert "merge-path failure: publication of" in preserved
    assert "verdict: passed" in preserved  # the earlier gate run is still there
    assert str(failure.detail["log_path"]) in result.detail


def test_lifecycle_refuses_uncovered_identity_before_dispatch(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "uncovered")
    gitops.hooks_dir(canonical).joinpath("pre-push").unlink()
    dispatched = False

    def unexpected_dispatch(*_args: object, **_kwargs: object) -> Report:
        nonlocal dispatched
        dispatched = True
        raise AssertionError("uncovered lifecycle must not dispatch")

    result = run_repo_task(
        str(canonical),
        "This work must not start without merge-path coverage.",
        "engineer",
        workspace=Workspace(
            tmp_path / "uncovered-worktrees",
            resolver=lambda _spec: canonical,
            workflow="local",
        ),
        dispatch_fn=unexpected_dispatch,
        recorded_gate=["true"],
    )

    assert result.outcome == "error"
    assert "lifecycle dispatch refused" in result.detail
    assert "no executable pre-push hook" in result.detail
    assert "just repos --audit-gate-coverage" in result.detail
    assert not dispatched


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("absent", "no required PR status checks exist"),
        ("unknown", "required PR status checks are unknown"),
    ],
)
def test_lifecycle_refuses_remote_identity_without_known_required_checks(
    tmp_path, bare_origin, status: str, expected: str
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"remote-uncovered-{status}")
    gitops.hooks_dir(canonical).joinpath("pre-push").unlink()
    workspace = Workspace(
        tmp_path / f"remote-uncovered-{status}-worktrees",
        resolver=lambda _spec: canonical,
        workflow="remote",
        repo_type="team",
    )

    class CoverageGitHub(FakeGitHub):
        def required_status_checks(self, repo: str, branch: str) -> tuple[str, ...]:
            if status == "unknown":
                raise GitHubError("branch protection unavailable")
            return ()

    result = run_repo_task(
        "acme/widget",
        "Do not dispatch without known remote merge-path coverage.",
        "engineer",
        workspace=workspace,
        github=CoverageGitHub(origin),
        dispatch_fn=lambda *_args, **_kwargs: pytest.fail("must not dispatch"),
    )

    assert result.outcome == "error"
    assert expected in result.detail


def test_remote_lifecycle_publishes_on_required_checks_without_a_pre_push_hook(
    tmp_path, bare_origin
) -> None:
    """Required PR status checks alone are sufficient coverage to dispatch.

    Every other lifecycle journey is covered by the hook the clone fixture
    installs, so this is the one that proves the other half of the criterion
    actually admits work rather than merely being spelled in the condition.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "remote-required-checks-only")
    gitops.hooks_dir(canonical).joinpath("pre-push").unlink()
    workspace = Workspace(
        tmp_path / "remote-required-checks-only-worktrees",
        resolver=lambda _spec: canonical,
        workflow="remote",
        repo_type="single-owner",
    )

    result = run_repo_task(
        "acme/widget",
        "Publish a change covered only by required PR status checks.",
        "engineer",
        workspace=workspace,
        github=FakeGitHub(origin, required=("complete-gate",)),
        dispatch_fn=make_writing_dispatch(filename="required-checks-only.txt"),
        recorded_gate=["true"],
        merge_policy="auto",
        sleep=lambda _: None,
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert result.publication_workflow == "remote"
    assert _has_file(origin, "main", "required-checks-only.txt")


def test_local_workflow_cannot_qualify_on_required_checks_alone(tmp_path, bare_origin) -> None:
    """Branch protection cannot cover a workflow that never opens a PR.

    A local publication pushes straight to base, so however many required status
    checks the GitHub identity declares, none of them ever run against the change.
    Only the pre-push hook stands on that path, and it is absent here.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "local-required-only")
    gitops.hooks_dir(canonical).joinpath("pre-push").unlink()
    workspace = Workspace(
        tmp_path / "local-required-only-worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )
    before = _tip(origin, "main")

    result = run_repo_task(
        "acme/widget",
        "This local publication must not start on remote checks alone.",
        "engineer",
        workspace=workspace,
        github=FakeGitHub(origin, required=("complete-gate",)),
        dispatch_fn=make_writing_dispatch(filename="never-dispatched.txt"),
        recorded_gate=["true"],
    )

    assert result.outcome == "error"
    assert "lifecycle dispatch refused" in result.detail
    assert "publishes without a PR for required status checks to gate" in result.detail
    # The refusal precedes the agent, so its change exists nowhere: no branch was
    # cut in the execution checkout and the base never moved.
    assert not gitops.branch_exists(canonical, result.branch)
    assert _tip(origin, "main") == before
    assert not _has_file(origin, "main", "never-dispatched.txt")


def test_local_workflow_recovery_cannot_qualify_on_required_checks_alone(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "local-recovery-required-only")
    gitops.hooks_dir(canonical).joinpath("pre-push").unlink()
    subprocess.run(
        ["git", "remote", "set-url", "origin", "https://github.com/acme/widget.git"],
        cwd=canonical,
        check=True,
    )
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")

    with pytest.raises(RegistryError, match="publishes without a PR"):
        recover_repo(
            canonical,
            "feature/never-recovered",
            workspace_root=tmp_path / "local-recovery-required-only-worktrees",
            github=FakeGitHub(origin, required=("complete-gate",)),
            recorded_gate=["true"],
        )


@pytest.mark.parametrize(
    ("rejection", "expected_outcome"),
    [("gate", "gate-failed"), ("transport", "error")],
)
def test_remote_human_checkpoint_records_push_failure(
    tmp_path, bare_origin, rejection: str, expected_outcome: str
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"human-checkpoint-{rejection}")
    workspace = Workspace(
        tmp_path / f"human-checkpoint-{rejection}-worktrees",
        resolver=lambda _spec: canonical,
        workflow="remote",
        repo_type="team",
    )
    if rejection == "gate":
        install_pre_push_hook(canonical, "printf 'pre-push gate failed\\n' >&2\nexit 1")
    else:
        receive = origin / "hooks" / "pre-receive"
        receive.write_text("#!/bin/sh\nprintf 'remote denied\\n' >&2\nexit 1\n", encoding="utf-8")
        receive.chmod(0o755)

    result = run_repo_task(
        "acme/widget",
        workspace=workspace,
        github=FakeGitHub(origin),
        steps=[
            Step("prepare", "engineer", "Prepare work for external approval."),
            Step("approve", task="Approve the work.", kind="human", deps=["prepare"]),
        ],
        dispatch_fn=_per_step_dispatch(),
        recorded_gate=["true"],
    )

    assert result.outcome == expected_outcome
    assert "push" in result.detail
    assert result.pr is None


@pytest.mark.parametrize(
    ("rejection", "expected_outcome"),
    [("gate", "gate-failed"), ("transport", "error")],
)
def test_remote_lifecycle_branch_push_failure_opens_no_pr(
    tmp_path, bare_origin, rejection: str, expected_outcome: str
) -> None:
    """The ordinary remote path stops at its own push, before any PR exists.

    A remote identity is normally covered by required checks, but a qualifying
    pre-push hook covers it too — and then it, not GitHub, is what rejects the
    branch that would have carried the PR.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"remote-branch-push-{rejection}")
    workspace = Workspace(
        tmp_path / f"remote-branch-push-{rejection}-worktrees",
        resolver=lambda _spec: canonical,
        workflow="remote",
        repo_type="single-owner",
    )
    if rejection == "gate":
        install_pre_push_hook(canonical, "printf 'pre-push gate failed\\n' >&2\nexit 1")
    else:
        receive = origin / "hooks" / "pre-receive"
        receive.write_text("#!/bin/sh\nprintf 'remote denied\\n' >&2\nexit 1\n", encoding="utf-8")
        receive.chmod(0o755)
    github = FakeGitHub(origin)

    result = run_repo_task(
        "acme/widget",
        "Publish an ordinary remote change the push rejects.",
        "engineer",
        workspace=workspace,
        github=github,
        dispatch_fn=make_writing_dispatch(filename="remote.txt"),
        recorded_gate=["true"],
    )

    assert result.outcome == expected_outcome
    # No PR was opened, and the branch that would have carried one never reached
    # the remote, so the rejection really did land before publication.
    assert result.pr is None
    assert not gitops.branch_exists(origin, result.branch)
    assert not _has_file(origin, "main", "remote.txt")


def test_local_repo_publishes_to_a_non_main_default_branch(tmp_path, bare_origin) -> None:
    origin = bare_origin(branch="master")
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),
        "Add a portable change.",
        "engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="portable.txt"),
        recorded_gate=["true"],
    )
    assert result.ok, result.detail
    assert result.base_branch == "master"
    assert _has_file(origin, "master", "portable.txt")


def test_local_repo_gate_failure_blocks_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    workspace = _workspace(tmp_path, origin)
    install_pre_push_hook(
        _shared_checkout(tmp_path),
        "printf 'pre-push gate: lint tier: bad import\\n' >&2\nexit 1",
    )
    result = run_repo_task(
        str(origin),
        "Add a change that fails the gate.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["sh", "-c", "printf 'lint tier: bad import\\n'; exit 1"],
    )
    assert not result.ok
    assert result.outcome == "gate-failed"
    assert result.pr is None
    assert "lint tier: bad import" in result.detail
    assert _tip(origin, "main") == before  # origin main untouched


def test_local_repo_syncs_advanced_base_before_the_gated_push(tmp_path, bare_origin) -> None:
    """The pre-handoff sync lands before the hook sees the tree it will gate.

    The hook asserts the advanced base file is present in whatever tree is being
    pushed, so it fails if the sync ever moves after publication rather than
    before it — which is the ordering the repository's gate depends on to judge
    the branch against the current base.
    """
    origin = bare_origin()
    ws = _workspace(tmp_path, origin)
    install_pre_push_hook(
        _shared_checkout(tmp_path),
        "test -f base.txt || { printf 'pre-push gate: base sync missing\\n' >&2; exit 1; }",
    )
    writing_dispatch = make_writing_dispatch(filename="feature.txt")
    advanced_sha = ""

    def dispatch_after_base_advances(
        persona: str, task: str, *, project_dir: str, **kwargs: object
    ) -> Report:
        nonlocal advanced_sha
        report = writing_dispatch(persona, task, project_dir=project_dir, **kwargs)
        advanced_sha = _advance_origin(tmp_path, origin, "base.txt", "new base\n")
        return report

    result = run_repo_task(
        str(origin),
        "Add a feature while main advances.",
        "engineer",
        workspace=ws,
        dispatch_fn=dispatch_after_base_advances,
        recorded_gate=["true"],
    )

    assert result.ok and result.outcome == "merged"
    assert result.verify is not None and result.verify.ok
    assert _has_file(origin, "main", "base.txt")
    assert _has_file(origin, "main", "feature.txt")
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "merge-base", "--is-ancestor", advanced_sha, "main"]
        ).returncode
        == 0
    )


def test_local_conflict_resolves_outside_queue_then_requeues_and_merges(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    workspace = _workspace(tmp_path, origin)
    dispatched = threading.Barrier(2)
    resolution_calls: list[tuple[str, str]] = []

    def concurrent_dispatch(
        persona: str, task: str, *, project_dir: str, env: dict[str, str], **kwargs: object
    ) -> Report:
        path = Path(project_dir) / "shared.txt"
        if "Resolve the content conflict" in task:
            assert "<<<<<<<" in path.read_text(encoding="utf-8")
            # The resolver works in the same worktree and proves its resolution with
            # the same gate, so it must resolve the same comparison base.
            assert env["ORCHESTRATOR_COMPARISON_REMOTE"] == "origin"
            assert env["ORCHESTRATOR_COMPARISON_BASE"] == "main"
            resolution_calls.append((str(kwargs["session"]), project_dir))
            path.write_text("first branch\nsecond branch\n", encoding="utf-8")
            gitops.add_all(project_dir)
        else:
            content = "first branch\n" if "first" in task else "second branch\n"
            path.write_text(content, encoding="utf-8")
            dispatched.wait(timeout=e2e_timeout(10))
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    journal = open_journal(tmp_path / "conflict-run", RunId("local-conflict"), 1)

    def run(name: str):
        return run_repo_task(
            str(origin),
            f"Edit the shared file from the {name} local run.",
            "engineer",
            branch=f"feature/{name}-local-conflict",
            workspace=workspace,
            dispatch_fn=concurrent_dispatch,
            recorded_gate=["git", "diff", "--check", "origin/main...HEAD"],
            journal=NodeJournal(journal, NodeId(name), RunId("local-conflict"), 1),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(run, "first")
        second_future = pool.submit(run, "second")
        results = [
            first_future.result(timeout=e2e_timeout(30)),
            second_future.result(timeout=e2e_timeout(30)),
        ]

    assert [result.outcome for result in results] == ["merged", "merged"]
    assert len(resolution_calls) == 1
    # The resolver runs against a branch this run already dispatched, in this run's
    # own worktree, so its conversation is named for the branch *and* that directory.
    # A bare branch name would resume a conversation the harness filed under some
    # other run's worktree, and the resolver would die before its first turn.
    resolution_session, resolver_dir = resolution_calls[0]
    resolved_branch = resolution_session.split("@", 1)[0]
    assert resolved_branch in {"feature/first-local-conflict", "feature/second-local-conflict"}
    assert resolution_session == f"{scoped_session(resolved_branch, resolver_dir)}:main"
    # The publication-path conflict resolution is a dispatch, and the ledger records
    # its start and finish like any other rather than rendering 90 minutes of real
    # work as one lock-wait.
    recorded = journal.events()
    started = [event for event in recorded if event.kind == "conflict-resolution-started"]
    finished = [event for event in recorded if event.kind == "conflict-resolution-finished"]
    assert len(started) == 1 and len(finished) == 1
    assert started[0].detail["attempt"] == 1
    assert started[0].detail["persona"] == "engineer"
    assert finished[0].detail == {
        "branch": started[0].detail["branch"],
        "attempt": 1,
        "completed": True,
        "resolved": True,
        "unresolved_paths": [],
    }
    assert recorded.index(started[0]) < recorded.index(finished[0])
    final = subprocess.run(
        ["git", "-C", str(origin), "show", "main:shared.txt"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert final == "first branch\nsecond branch\n"


def test_a_conflict_resolution_dispatch_that_raises_still_closes_its_ledger_events(
    tmp_path, bare_origin
) -> None:
    """A started event with nothing to close it is the reading these events prevent."""
    origin = bare_origin({"shared.txt": "original\n"})
    initial = make_writing_dispatch(filename="shared.txt", content="preserved")

    def dispatch_fn(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        if "Resolve the content conflict" not in task:
            report = initial(persona, task, project_dir=project_dir, **kwargs)
            _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")
            return report
        raise DispatchError("onejudge binary not found: 'onejudge'")

    journal = open_journal(tmp_path / "raising-run", RunId("raising-conflict"), 1)
    with pytest.raises(DispatchError):
        run_repo_task(
            str(origin),
            "Create a conflicting local edit.",
            "engineer",
            workspace=_workspace(tmp_path, origin),
            dispatch_fn=dispatch_fn,
            recorded_gate=["true"],
            journal=NodeJournal(journal, NodeId("ship"), RunId("raising-conflict"), 1),
        )

    recorded = [
        event
        for event in journal.events()
        if event.kind in {"conflict-resolution-started", "conflict-resolution-finished"}
    ]
    assert [event.kind for event in recorded] == [
        "conflict-resolution-started",
        "conflict-resolution-finished",
    ]
    assert recorded[1].detail["completed"] is False
    assert "DispatchError" in str(recorded[1].detail["error"])


def test_a_conflict_resolution_whose_artifact_write_fails_still_closes_its_events(
    tmp_path, bare_origin
) -> None:
    """The close cannot depend on *where* after the start the failure lands.

    Here the resolver itself succeeds and commits; what fails is persisting its
    report afterwards, against a real read-only artifact directory — the shape a
    full or permission-denied disk takes in production. The ledger cannot see which
    step raised, so a start it never closed reads as a hang either way.
    """
    origin = bare_origin({"shared.txt": "original\n"})
    initial = make_writing_dispatch(filename="shared.txt", content="preserved")
    journal = open_journal(tmp_path / "unwritable-run", RunId("unwritable-conflict"), 1)
    node_journal = NodeJournal(journal, NodeId("ship"), RunId("unwritable-conflict"), 1)
    artifacts = node_journal.artifact_dir
    assert artifacts is not None

    def dispatch_fn(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        if "Resolve the content conflict" not in task:
            report = initial(persona, task, project_dir=project_dir, **kwargs)
            _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")
            return report
        path = Path(project_dir) / "shared.txt"
        path.write_text("advanced\npreserved by engineer\n", encoding="utf-8")
        gitops.add_all(project_dir)
        artifacts.mkdir(parents=True, exist_ok=True)
        artifacts.chmod(0o500)
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    try:
        with pytest.raises(OSError):
            run_repo_task(
                str(origin),
                "Create a conflicting local edit.",
                "engineer",
                workspace=_workspace(tmp_path, origin),
                dispatch_fn=dispatch_fn,
                recorded_gate=["true"],
                journal=node_journal,
            )
    finally:
        artifacts.chmod(0o700)

    recorded = [
        event
        for event in journal.events()
        if event.kind in {"conflict-resolution-started", "conflict-resolution-finished"}
    ]
    assert [event.kind for event in recorded] == [
        "conflict-resolution-started",
        "conflict-resolution-finished",
    ]
    assert recorded[1].detail["completed"] is False
    assert "Error" in str(recorded[1].detail["error"])


@pytest.mark.parametrize("resolver_commits", [False, True])
def test_local_conflict_incomplete_resolver_preserves_branch(
    tmp_path, bare_origin, resolver_commits: bool
) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    initial = make_writing_dispatch(filename="shared.txt", content="preserved")

    def dispatch_fn(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        if "Resolve the content conflict" not in task:
            report = initial(persona, task, project_dir=project_dir, **kwargs)
            _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")
            return report
        if resolver_commits:
            path = Path(project_dir) / "shared.txt"
            path.write_text("advanced\npreserved by engineer\n", encoding="utf-8")
            gitops.add_all(project_dir)
        return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)

    journal = open_journal(tmp_path / "incomplete-run", RunId("incomplete-conflict"), 1)
    result = run_repo_task(
        str(origin),
        "Create a conflicting local edit.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=dispatch_fn,
        recorded_gate=["true"],
        journal=NodeJournal(journal, NodeId("ship"), RunId("incomplete-conflict"), 1),
    )

    assert result.outcome == "sync-conflict"
    assert "did not complete" in result.detail
    assert not _has_file(origin, result.branch, "shared.txt")
    # The unresolved and incomplete outcomes are recorded too: the ledger says the
    # conflict dispatch ran and how it ended, not merely that publication stopped.
    finished = [event for event in journal.events() if event.kind == "conflict-resolution-finished"]
    assert len(finished) == 1
    assert finished[0].detail["completed"] is False
    assert finished[0].detail["resolved"] is resolver_commits
    assert finished[0].detail["unresolved_paths"] == ([] if resolver_commits else ["shared.txt"])


def test_local_conflict_retry_resumes_committed_branch(tmp_path, bare_origin) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    initial = make_writing_dispatch(filename="shared.txt", content="preserved")
    resolutions = 0
    initial_runs = 0
    finish_resolution = False

    def dispatch_fn(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        nonlocal initial_runs, resolutions
        if "Resolve the content conflict" not in task:
            initial_runs += 1
            report = initial(persona, task, project_dir=project_dir, **kwargs)
            _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")
            return report
        resolutions += 1
        if finish_resolution:
            (Path(project_dir) / "shared.txt").write_text("advanced\npreserved\n", encoding="utf-8")
            gitops.add_all(project_dir)
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    workspace = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),
        "Create a persistently conflicting local edit.",
        "engineer",
        workspace=workspace,
        dispatch_fn=dispatch_fn,
        recorded_gate=["true"],
    )

    assert result.outcome == "sync-conflict"
    assert resolutions == MAX_MERGE_CONFLICT_RESOLUTIONS
    assert f"after {MAX_MERGE_CONFLICT_RESOLUTIONS} resolve-and-requeue cycles" in result.detail
    assert result.resume is not None and result.resume.mode == "retry"
    preserved_branch = result.branch
    preserved_checkpoint = result.resume.checkpoint
    prior_plan = {
        "tasks": [
            {
                "id": "change",
                "repo": str(origin),
                "persona": "engineer",
                "task": "Create a persistently conflicting local edit.",
                "recorded_gate": ["true"],
            }
        ]
    }
    retry_plan = next_round(
        prior_plan,
        {"round": 1, "results": {"change": {"status": "failed", **result_payload(result)}}},
        {"retry": {"change": {}}},
    )
    assert retry_plan["tasks"][0]["resume"]["checkpoint"] == preserved_checkpoint
    retry_plan_path = tmp_path / "retry-plan.json"
    retry_plan_path.write_text(json.dumps(retry_plan), encoding="utf-8")
    parsed_retry = load_repo_plan(retry_plan_path).tasks[0]
    assert parsed_retry.resume is not None
    assert parsed_retry.resume.completed_steps == ("main",)

    finish_resolution = True
    retried = run_repo_task(
        str(origin),
        "Create a persistently conflicting local edit.",
        "engineer",
        workspace=workspace,
        dispatch_fn=dispatch_fn,
        recorded_gate=["true"],
        resume=parsed_retry.resume,
    )

    assert retried.outcome == "merged", retried.detail
    assert retried.branch == preserved_branch
    assert initial_runs == 1
    assert gitops.is_ancestor(
        workspace.clone_dir(normalize_repo(str(origin))), preserved_checkpoint, preserved_branch
    )
    assert _has_file(origin, "main", "shared.txt")


def test_failed_lifecycle_without_commits_retries_fresh(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)

    def no_work(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        return Report(persona, 1, False, False, 1, [], {}, {}, "")

    failed = run_repo_task(
        str(origin),
        "Fail before producing work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=no_work,
        recorded_gate=["true"],
    )

    assert failed.outcome == "not-completed"
    assert failed.resume is None
    retry_plan = next_round(
        {
            "tasks": [
                {
                    "id": "change",
                    "repo": str(origin),
                    "persona": "engineer",
                    "task": "Fail before producing work.",
                    "recorded_gate": ["true"],
                }
            ]
        },
        {"round": 1, "results": {"change": {"status": "failed", **result_payload(failed)}}},
        {"retry": {"change": {}}},
    )
    assert "resume" not in retry_plan["tasks"][0]

    retried = run_repo_task(
        str(origin),
        "Produce work on the fresh retry.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="fresh.txt", content="fresh"),
        recorded_gate=["true"],
    )

    assert retried.outcome == "merged", retried.detail
    assert retried.branch != failed.branch
    assert _has_file(origin, "main", "fresh.txt")


def test_local_repo_pre_push_hook_verifies_the_real_worktree(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    marker = tmp_path / "gate-ran"
    install_pre_push_hook(
        canonical,
        f"test -f feature.txt && touch {shlex.quote(str(marker))}",
    )
    Registry().register(
        str(canonical),
        workflow="local",
        repo_type="single-owner",
        gate=(
            f'sh -c \'test "$1" = origin/main && test -f feature.txt '
            f"&& touch {marker}' -- {{base}}"
        ),
    )
    result = run_repo_task(
        str(canonical),
        "Add a change verified by the merge-path hook.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )
    assert result.ok and result.outcome == "merged"
    assert result.verify is not None and result.verify.ok
    assert marker.exists()
    assert _has_file(origin, "main", "feature.txt")


def test_local_repo_pre_push_hook_failure_stops_publication(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    install_pre_push_hook(canonical, "printf 'pre-push gate failed\\n' >&2\nexit 1")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner", gate="false")
    result = run_repo_task(
        str(canonical),
        "Add a change rejected by the merge-path hook.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )
    assert result.outcome == "gate-failed"
    assert result.verify is not None and not result.verify.ok
    assert "pre-push" in result.verify.output
    assert not _has_file(origin, "main", "feature.txt")


def test_pre_push_complete_gate_catches_a_strict_tier_then_allows_clean_work(
    tmp_path, bare_origin
) -> None:
    gate_log = tmp_path / "complete-gate.log"
    origin = bare_origin(
        {
            "Makefile": (
                ".PHONY: check strict-tier gate\n"
                "check:\n"
                f"\t@printf 'check\\n' >> {shlex.quote(str(gate_log))}\n"
                "\t@test -f README.md\n"
                "strict-tier:\n"
                f"\t@printf 'strict-tier\\n' >> {shlex.quote(str(gate_log))}\n"
                "\t@! grep -R -F complete-gate-finding -- feature.txt 2>/dev/null\n"
                "gate: check strict-tier\n"
            )
        }
    )
    base_before_failure = _tip(origin, "main")
    canonical = gitops.clone(origin, tmp_path / "registered-complete-gate")
    install_pre_push_hook(
        canonical,
        "make gate || { printf 'pre-push gate failed\\n' >&2; exit 1; }",
    )
    Registry().register(
        str(canonical), workflow="local", repo_type="single-owner", gate="make gate"
    )
    workspace = Workspace(tmp_path / "complete-gate-worktrees")

    rejected = run_repo_task(
        str(canonical),
        "Add a change rejected only by the strict gate tier.",
        "engineer",
        workspace=workspace,
        branch="strict-tier-failure",
        dispatch_fn=make_writing_dispatch(filename="feature.txt", content="complete-gate-finding"),
    )

    assert rejected.outcome == "gate-failed"
    assert rejected.verify is not None and not rejected.verify.ok
    assert "strict-tier" in rejected.verify.output
    assert gate_log.read_text(encoding="utf-8").splitlines() == ["check", "strict-tier"]
    assert _tip(origin, "main") == base_before_failure
    assert not _has_file(origin, "main", "feature.txt")

    published = run_repo_task(
        str(canonical),
        "Add a change accepted by the complete gate.",
        "engineer",
        workspace=workspace,
        branch="complete-gate-success",
        dispatch_fn=make_writing_dispatch(filename="feature.txt", content="clean change"),
    )

    assert published.ok and published.outcome == "merged", published.detail
    assert published.verify is not None and published.verify.ok
    assert gate_log.read_text(encoding="utf-8").splitlines() == [
        "check",
        "strict-tier",
        "check",
        "strict-tier",
        "check",
        "strict-tier",
    ]
    assert _tip(origin, "main") != base_before_failure
    assert _has_file(origin, "main", "feature.txt")


def test_bazel_affected_candidate_runs_in_lifecycle_worktree(
    tmp_path, bare_origin, monkeypatch
) -> None:
    origin = bare_origin({"WORKSPACE.bazel": ""})
    canonical = gitops.clone(origin, tmp_path / "registered")
    tools = tmp_path / "bin"
    tools.mkdir()
    bazel_diff = tools / "bazel-diff"
    bazel_diff.write_text(
        "#!/usr/bin/env python3\nimport pathlib, sys\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output') + 1]).write_text('//:affected\\n')\n",
        encoding="utf-8",
    )
    bazel = tools / "bazel"
    bazel.write_text(
        '#!/bin/sh\ntest "$1" = test && test "$2" = -- && test "$3" = //:affected\n',
        encoding="utf-8",
    )
    bazel_diff.chmod(0o755)
    bazel.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    result = run_repo_task(
        str(canonical),
        "Add a Bazel-affected change.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )
    assert result.ok and result.verify is not None and result.verify.ok


def test_local_repo_noop_registry_gate_relies_on_covered_merge_path(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")

    result = run_repo_task(
        str(canonical),
        "Add a change with an explicit no-op identity gate.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )

    assert result.ok and result.outcome == "merged"
    assert "pushed unproven" not in result.detail
    # A `<no-op>` identity gate names no bar, so there is nothing for a record to
    # claim was run: the hook still gates the push, but the run cannot say what it
    # ran, and inventing a verdict would be worse than recording none.
    assert result.verify is None


# llmlint: ignore[e2e_not_mocked] GitHub decisioning is the suite's documented external seam.
def test_remote_human_checkpoint_noop_gate_relies_on_required_checks(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    result = run_repo_task(
        str(canonical),
        workspace=Workspace(tmp_path / "worktrees"),
        github=FakeGitHub(origin),
        steps=[
            Step("prepare", "engineer", "prepare a draft checkpoint"),
            Step("approve", task="Approve the checkpoint.", kind="human", deps=["prepare"]),
        ],
        body="## What\nPrepare a checkpoint.\n\n## Why\nAwait approval.\n",
        dispatch_fn=_per_step_dispatch(),
    )
    assert result.outcome == "waiting-human" and result.pr is not None
    assert "pushed unproven" not in result.detail


# llmlint: ignore[e2e_not_mocked] GitHub decisioning is the suite's documented external seam.
def test_remote_human_checkpoint_adopts_existing_pr_without_duplicate(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered-existing-human-pr")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")

    class ExistingPRGitHub(FakeGitHub):
        def adoptable_pr(self, repo, *, head, base, head_sha):
            if not self._prs:
                self._n = 1
                self._prs[1] = FakePRState(head, base, "existing", "existing", draft=True)
            return super().adoptable_pr(repo, head=head, base=base, head_sha=head_sha)

    github = ExistingPRGitHub(origin)
    result = run_repo_task(
        str(canonical),
        workspace=Workspace(tmp_path / "existing-human-pr-worktrees"),
        github=github,
        steps=[
            Step("prepare", "engineer", "prepare a draft checkpoint"),
            Step("approve", task="Approve the checkpoint.", kind="human", deps=["prepare"]),
        ],
        body="## What\nPrepare a checkpoint.\n\n## Why\nAvoid duplicate PRs.\n",
        dispatch_fn=_per_step_dispatch(),
    )

    assert result.outcome == "waiting-human"
    assert result.pr is not None and result.pr.number == 1
    assert result.resume is not None and result.resume.pr == result.pr.url
    assert github._n == 1


# llmlint: ignore[e2e_not_mocked] GitHub decisioning is the suite's documented external seam.
def test_remote_human_checkpoint_drafts_without_a_lifecycle_gate_run(tmp_path, bare_origin) -> None:
    """A red *recorded* identity gate cannot block a draft: nothing runs it."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    Registry().register(
        str(canonical),
        workflow="remote",
        repo_type="single-owner",
        gate="sh -c 'printf human-pause-gate-failed; exit 1'",
    )
    github = FakeGitHub(origin)
    result = run_repo_task(
        str(canonical),
        workspace=Workspace(tmp_path / "worktrees"),
        github=github,
        steps=[
            Step("prepare", "engineer", "prepare a draft checkpoint"),
            Step("approve", task="Approve the checkpoint.", kind="human", deps=["prepare"]),
        ],
        body="## What\nPrepare a checkpoint.\n\n## Why\nAwait approval.\n",
        dispatch_fn=_per_step_dispatch(),
    )
    assert result.outcome == "waiting-human" and result.pr is not None
    assert result.verify is None


def test_lifecycle_without_explicit_or_registry_gate_errors(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "Attempt an unconfigured lifecycle.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )
    assert result.outcome == "error"
    assert "no verification gate is configured" in result.detail


def test_recorded_gate_override_cannot_bypass_the_merge_path_gate(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    install_pre_push_hook(canonical, "printf 'pre-push gate failed\\n' >&2\nexit 1")
    before = _tip(origin, "main")

    result = run_repo_task(
        str(origin),
        "Add a change whose recorded gate command would have passed.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        # The recorded identity-gate override says the change is fine; only the
        # repository's own merge path decides, and it rejects.
        recorded_gate=["true"],
    )

    assert result.outcome == "gate-failed"
    assert "repository pre-push gate rejected publication" in result.detail
    assert _tip(origin, "main") == before
    assert not _has_file(origin, "main", "feature.txt")


def test_agent_not_completed_stops_early(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),
        "Task the agent will not finish.",
        "engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
    )
    assert result.outcome == "not-completed"
    assert not result.ok
    assert "hit the turn cap" in result.detail
    assert result.resume is not None

    clone = ws.clone_dir(normalize_repo(str(origin)))
    assert _has_file(clone, result.branch, "partial.txt")
    subject = subprocess.run(
        ["git", "-C", str(clone), "log", "-1", "--format=%s", result.branch],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert subject.startswith("chore:") and "incomplete step" in subject
    assert not _has_file(origin, "main", "partial.txt")
    assert not _has_file(origin, result.branch, "partial.txt")  # incomplete work is not pushed


def test_completed_automatic_resume_drops_its_provisional_incomplete_marker(
    tmp_path, bare_origin
) -> None:
    """A later green completion supersedes the marker from its first bounded attempt."""
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    before = _tip(origin, "main")
    attempts = 0

    def completes_after_resume(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            change = Path(project_dir) / "completed-after-resume.txt"
            change.write_text("finished work\n", encoding="utf-8")
            gitops.add_all(project_dir)
            gitops.commit(project_dir, "fix: finish work before the supervisor settles")
            return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)
        verification = Path(project_dir) / "verified-after-resume.txt"
        verification.write_text("gate is green\n", encoding="utf-8")
        gitops.add_all(project_dir)
        gitops.commit(project_dir, "test: record the completed continuation")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(origin),
        "Finish and verify work across one automatic continuation.",
        "engineer",
        workspace=workspace,
        dispatch_fn=completes_after_resume,
        recorded_gate=["true"],
    )

    assert attempts == 2
    assert result.ok and result.outcome == "merged", result.detail
    assert _has_file(origin, "main", "completed-after-resume.txt")
    assert _has_file(origin, "main", "verified-after-resume.txt")
    assert not incomplete_commits(canonical, before, result.branch)


def test_completed_automatic_resume_drops_a_provisional_marker_left_at_the_tip(
    tmp_path, bare_origin
) -> None:
    """The superseded marker is removed even when nothing was committed after it.

    A continuation that finishes by verifying rather than by writing leaves the
    provisional marker as the branch tip, so removing it is a reset rather than a
    replay of later commits. Both are the same contract — the marker does not
    survive its own supersession — and only the replay half had been driven.
    """
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    before = _tip(origin, "main")
    attempts = 0

    def completes_without_committing_again(
        persona: str, task: str, *, project_dir: str, **_: object
    ) -> Report:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            (Path(project_dir) / "tip-marker-work.txt").write_text("done\n", encoding="utf-8")
            gitops.add_all(project_dir)
            gitops.commit(project_dir, "fix: do the work before the supervisor settles")
            return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)
        # Verified the existing commit and wrote nothing, so the marker is the tip.
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(origin),
        "Verify work already committed by the first bounded attempt.",
        "engineer",
        workspace=workspace,
        dispatch_fn=completes_without_committing_again,
        recorded_gate=["true"],
    )

    assert attempts == 2
    assert result.ok and result.outcome == "merged", result.detail
    assert _has_file(origin, "main", "tip-marker-work.txt")
    assert not incomplete_commits(canonical, before, result.branch)


def test_completion_after_two_resumes_clears_the_one_marker_and_keeps_every_part(
    tmp_path, bare_origin
) -> None:
    """Two bounded attempts still leave one marker, and completion clears it.

    A branch carries at most one empty marker however many attempts stop on it: the
    second stop sees the first one base-relative and adds none. So the removal this
    completion performs stays a single-marker operation no matter how many resumes
    preceded it, and the work each bounded attempt committed underneath that marker
    survives the rewrite removing it costs. The one-resume cases above cannot show
    that, because they never stop twice.
    """
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    before = _tip(origin, "main")
    attempts = 0

    def stops_twice_then_completes(
        persona: str, task: str, *, project_dir: str, **_: object
    ) -> Report:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            # A clean tree at the stop, so the lifecycle preserves nothing and the
            # marker it writes is empty — a removal candidate rather than the work.
            change = Path(project_dir) / f"resumed-part-{attempts}.txt"
            change.write_text(f"part {attempts}\n", encoding="utf-8")
            gitops.add_all(project_dir)
            gitops.commit(project_dir, f"fix: land part {attempts} before the supervisor settles")
            return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(origin),
        "Finish work that needed both automatic continuations.",
        "engineer",
        workspace=workspace,
        dispatch_fn=stops_twice_then_completes,
        recorded_gate=["true"],
    )

    assert attempts == 3
    assert result.ok and result.outcome == "merged", result.detail
    # Both bounded attempts' commits survive the rewrite that removing the marker costs.
    assert _has_file(origin, "main", "resumed-part-1.txt")
    assert _has_file(origin, "main", "resumed-part-2.txt")
    assert not incomplete_commits(canonical, before, result.branch)
    # The stops are no longer part of this result's story: the lineage the first one
    # recorded is retracted along with the marker, so the run reads as the single
    # completed workstream the branch now is.
    assert result.retry_lineage is None


def test_a_completed_continuation_keeps_an_inherited_incomplete_marker(
    tmp_path, bare_origin
) -> None:
    """Only this lifecycle's own provisional markers are superseded by its completion.

    A marker an earlier run left on the branch is load-bearing `repo-recover`
    provenance: it says work on this branch was never carried through the gate by the
    run that wrote it. Completing a *later* dispatch says nothing about that claim, so
    removing it would silently drop the recovery contract for the earlier work.
    """
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    remote_base = "origin/main"

    def commits_then_stops(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        (Path(project_dir) / "inherited-partial.txt").write_text("partial\n", encoding="utf-8")
        gitops.add_all(project_dir)
        gitops.commit(project_dir, "fix: commit partial work before stopping")
        return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)

    stopped = run_repo_task(
        str(origin),
        "Work an earlier run never finished.",
        "engineer",
        workspace=workspace,
        dispatch_fn=commits_then_stops,
        recorded_gate=["true"],
    )
    assert stopped.outcome == "not-completed"
    clone = workspace.clone_dir(normalize_repo(str(origin)))
    inherited = incomplete_commits(clone, remote_base, stopped.branch)
    # Empty, because that run committed its own work and stopped with a clean tree.
    # Only an empty marker is a removal candidate at all, so this is the one whose
    # survival actually turns on it having been inherited rather than provisional.
    assert len(inherited) == 1
    assert gitops.is_empty_commit(clone, next(iter(inherited)))

    attempts = 0

    def stops_once_then_completes(
        persona: str, task: str, *, project_dir: str, **_: object
    ) -> Report:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            (Path(project_dir) / "resumed-work.txt").write_text("more\n", encoding="utf-8")
            gitops.add_all(project_dir)
            gitops.commit(project_dir, "fix: continue the inherited branch")
            return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    resumed = run_repo_task(
        str(origin),
        "Continue the branch the earlier run left incomplete.",
        "engineer",
        workspace=workspace,
        branch=stopped.branch,
        dispatch_fn=stops_once_then_completes,
        recorded_gate=["true"],
    )

    assert attempts == 2
    surviving = incomplete_commits(clone, remote_base, resumed.branch)
    assert inherited <= surviving, "the earlier run's recovery provenance was dropped"


def test_completed_automatic_resume_keeps_the_marker_that_carries_its_work(
    tmp_path, bare_origin
) -> None:
    """A marker commit that *is* the preserved work is kept, and the run still merges.

    A bounded attempt that stops with a dirty tree has its partial work committed
    under the incomplete-marker message, so that commit and the preservation are the
    same object. Dropping it as a superseded marker would destroy exactly what the
    preservation exists to save — and the refusal to drop it escaped as an `error`,
    losing the publication of work a later attempt had already carried through the
    gate.
    """
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    canonical = _shared_checkout(tmp_path)
    attempts = 0

    def stops_dirty_then_completes(
        persona: str, task: str, *, project_dir: str, **_: object
    ) -> Report:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Uncommitted: the lifecycle is what commits this, under the marker message.
            (Path(project_dir) / "preserved-dirty.txt").write_text("partial\n", encoding="utf-8")
            return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)
        (Path(project_dir) / "finished.txt").write_text("gate is green\n", encoding="utf-8")
        gitops.add_all(project_dir)
        gitops.commit(project_dir, "test: finish the continuation")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(origin),
        "Continue work whose first attempt stopped with an uncommitted tree.",
        "engineer",
        workspace=workspace,
        dispatch_fn=stops_dirty_then_completes,
        recorded_gate=["true"],
    )

    assert attempts == 2
    assert result.ok and result.outcome == "merged", result.detail
    assert _has_file(origin, "main", "preserved-dirty.txt")
    assert _has_file(origin, "main", "finished.txt")
    marker = next(
        commit
        for commit in gitops.log_messages(canonical, "origin/main", result.branch)
        if INCOMPLETE_TRAILER in commit.message
    )
    assert not gitops.is_empty_commit(canonical, marker.sha)
    # The mirror of the cleared case: the marker stayed, so the lineage that records
    # the stop stays with it. Retracting it here would claim a branch still carrying
    # incomplete provenance had never been resumed.
    assert result.retry_lineage is not None


def test_a_stop_short_of_the_cap_is_not_reported_as_hitting_it(tmp_path, bare_origin) -> None:
    """One turn is not twelve, and the settled result has to say so.

    onejudge exits 1 for a worker that exhausted its turns *and* for one that
    stopped for any other reason, and this harness reported both as "hit the turn
    cap". A real run of this very node settled as `step 'main' hit the turn cap` at
    `turns: 1`, which sent its reader to raise a cap that was never approached. The
    honest line names how far the worker got and what account it left.
    """
    origin = bare_origin()
    ws = _workspace(tmp_path, origin)

    def stops_early(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        (Path(project_dir) / "partial.txt").write_text("one turn in\n", encoding="utf-8")
        return Report(
            persona,
            1,
            False,
            True,
            1,
            [
                {
                    "kind": "done_when",
                    "criterion": "the gate is green",
                    "verdict": {"value": False, "reason": "the supervisor ended the workstream"},
                }
            ],
            {},
            {},
            "",
            max_turns=12,
        )

    result = run_repo_task(
        str(origin),
        "Task the agent will abandon on its first turn.",
        "engineer",
        workspace=ws,
        dispatch_fn=stops_early,
        recorded_gate=["true"],
    )

    assert result.outcome == "not-completed"
    assert "hit the turn cap" not in result.detail
    assert "did not complete after 1 turn, short of its 12-turn cap" in result.detail
    # And the reason the run did leave behind travels with it, so the settled node
    # says why rather than only how far.
    assert "the supervisor ended the workstream" in result.detail


def test_retry_with_invalid_incomplete_provenance_records_fresh_branch_fallback(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    first = run_repo_task(
        str(origin),
        "Preserve partial work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
    )
    assert first.resume is not None and first.resume.mode == "retry"
    recovery_worktree = workspace.worktree(
        normalize_repo(str(origin)), first.branch, base="origin/main"
    )
    gitops.commit_empty(
        recovery_worktree,
        "test: invalidate retry provenance\n\n"
        f"Orchestrator-Recovered-Incomplete: {first.resume.checkpoint}",
    )
    workspace.remove_worktree(normalize_repo(str(origin)), recovery_worktree)

    second = run_repo_task(
        str(origin),
        "Complete on a safe branch.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="complete.txt"),
        recorded_gate=["true"],
        resume=first.resume,
    )

    assert second.ok and second.branch != first.branch
    assert second.retry_lineage is not None
    assert second.retry_lineage.disposition == "abandoned"
    assert "does not carry valid unattested incomplete provenance" in (
        second.retry_lineage.reason or ""
    )


def test_a_pinned_retry_resolves_to_the_pinned_branch_or_is_refused(tmp_path, bare_origin) -> None:
    """A retry that names its branch gets that branch every time, or a stated refusal.

    One change carried two branch names once: the planner submitted a retry pinned to
    a preserved branch, it resumed, and the *identical* envelope submitted afterwards
    silently landed on a freshly generated branch instead — because the preserved work
    was no longer unattested-incomplete by then. Which branch a retry produces has to
    be a function of the envelope, not of what the repository did in between.
    """
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    first = run_repo_task(
        str(origin),
        "Preserve partial work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
    )
    assert first.resume is not None and first.resume.mode == "retry"
    # The planner's envelope, submitted verbatim below: this change lives on this
    # branch, continued from this checkpoint.
    envelope = {"branch": first.branch, "resume": first.resume}

    honoured = run_repo_task(
        str(origin),
        "Continue the preserved work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="more.txt", completed=False),
        recorded_gate=["true"],
        **envelope,
    )
    assert honoured.branch == first.branch
    assert honoured.retry_lineage is not None
    assert honoured.retry_lineage.disposition == "reused"

    # Now the preserved work stops being unattested-incomplete, exactly as it does
    # once a recovery or an attestation lands on every preserved commit. The envelope
    # has not changed, and that is the whole point.
    recovery_worktree = workspace.worktree(
        normalize_repo(str(origin)), first.branch, base="origin/main"
    )
    attested = "\n".join(
        f"{RECOVERY_TRAILER} {sha}"
        for sha in sorted(incomplete_commits(recovery_worktree, "origin/main", "HEAD"))
    )
    gitops.commit_empty(recovery_worktree, f"test: invalidate retry provenance\n\n{attested}")
    workspace.remove_worktree(normalize_repo(str(origin)), recovery_worktree)

    refusals = [
        run_repo_task(
            str(origin),
            "Continue the preserved work.",
            "engineer",
            workspace=workspace,
            dispatch_fn=make_writing_dispatch(filename="more.txt", completed=False),
            recorded_gate=["true"],
            **envelope,
        )
        for _ in range(2)
    ]

    # Two identical submissions, one branch — the pinned one — and a reason naming
    # the pin. A generated branch name is random, so before this the two submissions
    # would not even have agreed with each other.
    assert [result.branch for result in refusals] == [first.branch, first.branch]
    for refused in refusals:
        assert refused.outcome == "resume-failed"
        assert "does not carry valid unattested incomplete provenance" in refused.detail
        assert f"pins branch {first.branch!r}" in refused.detail
        assert refused.retry_lineage is None


def test_preserved_retry_is_recovered_through_the_merge_path_gate(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    first = run_repo_task(
        str(origin),
        "Preserve partial work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
    )
    assert isinstance(first.resume, Resume)

    retried = run_repo_task(
        str(origin),
        "Finish the preserved workstream.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="complete.txt"),
        recorded_gate=["true"],
        resume=first.resume,
    )

    assert retried.outcome == "merged" and retried.ok
    assert _has_file(origin, "main", "complete.txt")


def test_preserved_retry_publishes_despite_a_red_recorded_gate(tmp_path, bare_origin) -> None:
    """The recorded gate is metadata: only the merge path can stop a retry."""
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    first = run_repo_task(
        str(origin),
        "Preserve partial work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
    )
    assert isinstance(first.resume, Resume)

    retried = run_repo_task(
        str(origin),
        "Finish with a red recorded gate.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="complete.txt"),
        recorded_gate=["false"],
        resume=first.resume,
    )

    assert retried.outcome == "merged" and retried.ok
    assert retried.verify is not None and retried.verify.ok
    assert _has_file(origin, "main", "complete.txt")


def test_clean_committed_partial_work_is_marked_and_recoverable(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-clean-partial")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    workspace = Workspace(tmp_path / "clean-partial-worktrees")
    partial_shas: list[str] = []

    def committing_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        worktree = Path(project_dir)
        partial = worktree / "partial.txt"
        if not partial.exists():
            partial.write_text("agent-owned partial work\n", encoding="utf-8")
            gitops.add_all(worktree)
            partial_shas.append(gitops.commit(worktree, "wip: agent commits partial work"))
        assert not gitops.is_dirty(worktree)
        return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)

    result = run_repo_task(
        str(canonical),
        "Commit partial work, then hit the turn cap.",
        "engineer",
        workspace=workspace,
        branch="feature/clean-committed-partial",
        dispatch_fn=committing_dispatch,
        recorded_gate=["true"],
    )

    assert result.outcome == "not-completed" and not result.ok
    assert "hit the turn cap" in result.detail
    assert result.resume is not None and result.retry_lineage is not None
    assert result.branch not in gitops.worktrees(canonical)
    assert partial_shas and gitops.is_ancestor(canonical, partial_shas[0], result.branch)
    marker_sha = gitops.ref_sha(canonical, result.branch)
    marker_message = gitops.log_messages(canonical, partial_shas[0], result.branch)[0].message
    assert INCOMPLETE_TRAILER in marker_message
    assert f"{PR_BASE_TRAILER} main" in marker_message
    assert (
        subprocess.run(
            ["git", "-C", str(canonical), "diff-tree", "--quiet", f"{marker_sha}^", marker_sha]
        ).returncode
        == 0
    )
    assert not _has_file(origin, "main", "partial.txt")
    assert not _has_file(origin, result.branch, "partial.txt")

    gate_log = tmp_path / "clean-partial-recovery-gates.log"
    gate = [
        "sh",
        "-c",
        f"echo gate >> {shlex.quote(str(gate_log))}; test -f partial.txt",
    ]
    recovered = recover_repo(
        canonical,
        result.branch,
        workspace_root=tmp_path / "clean-partial-recovery-worktrees",
        recorded_gate=gate,
    )

    assert recovered.ok and recovered.outcome == "merged"
    assert recovered.workflow == "local" and recovered.pr_base == "main"
    assert result.branch not in gitops.worktrees(canonical)
    assert not gate_log.exists()
    attestation = gitops.log_messages(canonical, marker_sha, result.branch)
    assert len(attestation) == 1
    assert f"Orchestrator-Recovered-Incomplete: {marker_sha}" in attestation[0].message
    assert not gitops.is_ancestor(canonical, attestation[0].sha, "origin/main")
    assert _has_file(origin, "main", "partial.txt")


def test_publication_subject_describes_the_change_and_its_body_keeps_the_attestation(
    tmp_path, bare_origin
) -> None:
    """What a recovered incomplete step leaves on the base branch.

    Observed on this repository's own `main`: subjects like
    `feat: adopt @oneharness/ui as the app's design system; ## What (incomple…`. The
    marker commit's subject is a valid Conventional Commit, so the subject synthesizer
    folded it in alongside the real work, and because task prose opens with a `## What`
    heading the fragment it contributed named a section rather than any change.

    The base branch stays squash-merged — the marker and its attestation are branch
    state, not `main` history — so the publication commit's trailers are what carry
    "a step was left incomplete here, and a green gate recovered it" forward.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-subject")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")

    def committing_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        worktree = Path(project_dir)
        (worktree / "design-system.txt").write_text("adopted\n", encoding="utf-8")
        gitops.add_all(worktree)
        gitops.commit(worktree, "feat: adopt the shared design system")
        assert not gitops.is_dirty(worktree)
        return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)

    result = run_repo_task(
        str(canonical),
        # The structured prose every dispatched task carries, headings included.
        "## What\nAdopt the shared design system.\n\n## Why\nThe app has no one source"
        " of visual truth.\n",
        "engineer",
        workspace=Workspace(tmp_path / "subject-worktrees"),
        branch="feature/published-subject",
        dispatch_fn=committing_dispatch,
        recorded_gate=["true"],
    )
    assert result.outcome == "not-completed" and result.resume is not None
    base_before = _tip(origin, "main")
    markers = incomplete_commits(canonical, "origin/main", result.branch)
    assert len(markers) == 1, sorted(markers)
    marker_sha = next(iter(markers))
    marker_subject = gitops.log_messages(canonical, f"{marker_sha}~1", marker_sha)[0].message
    # The marker names the work it preserved, not the heading above it.
    assert marker_subject.splitlines()[0] == (
        "chore: Adopt the shared design system. (incomplete step)"
    )

    recovered = recover_repo(
        canonical,
        result.branch,
        workspace_root=tmp_path / "subject-recovery-worktrees",
        recorded_gate=["true"],
    )
    assert recovered.ok and recovered.outcome == "merged", recovered.detail

    published = gitops.log_messages(canonical, base_before, "origin/main")
    # Squash-merged: one publication commit, not the branch's provenance history.
    assert len(published) == 1, [commit.message.splitlines()[0] for commit in published]
    subject, _, body = published[0].message.partition("\n")
    assert subject == "feat: adopt the shared design system"
    assert "incomplete" not in subject and "##" not in subject and "attest" not in subject
    # The attestation is preserved: the fact reaches `main`, the commits do not.
    assert f"{RECOVERY_TRAILER} {marker_sha}" in body
    assert not gitops.is_ancestor(canonical, marker_sha, "origin/main")
    assert _has_file(origin, "main", "design-system.txt")


def test_a_remote_retry_publishes_its_attestation_in_the_pr_body(tmp_path, bare_origin) -> None:
    """The remote half of the same contract: GitHub squashes, so the body carries it.

    A remote workstream that resumes a preserved branch attests the marker on the
    branch, but GitHub collapses that branch into one commit built from the PR title
    and body. Without the trailers there, `main` would keep no record that a step was
    left incomplete.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-remote-attested")
    workspace = Workspace(
        tmp_path / "remote-attested-worktrees",
        resolver=lambda _spec: canonical,
        workflow="remote",
        repo_type="single-owner",
    )
    github = FakeGitHub(origin)

    partial = run_repo_task(
        str(origin),
        "## What\nPreserve work for a remote retry.\n\n## Why\nIt proves the trailer.\n",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
        workflow="remote",
        repo_type="single-owner",
        github=github,
    )
    assert partial.outcome == "not-completed" and isinstance(partial.resume, Resume)
    markers = incomplete_commits(canonical, "origin/main", partial.branch)
    assert len(markers) == 1, sorted(markers)
    marker_sha = next(iter(markers))

    resumed = run_repo_task(
        str(origin),
        "## What\nFinish the preserved remote work.\n\n## Why\nIt proves the trailer.\n",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="finished.txt"),
        recorded_gate=["true"],
        workflow="remote",
        repo_type="single-owner",
        github=github,
        resume=partial.resume,
    )
    assert resumed.outcome == "merged", resumed.detail
    assert resumed.retry_lineage is not None
    assert resumed.retry_lineage.disposition == "recovered"
    assert resumed.pr is not None
    body = github.published(resumed.pr).body
    assert f"{RECOVERY_TRAILER} {marker_sha}" in body
    assert body.startswith("## What\n")


def test_a_redispatched_branch_is_not_handed_a_second_incomplete_marker(
    tmp_path, bare_origin
) -> None:
    """The marker states one fact about the branch; a redispatch does not restate it.

    Observed on this repository's own workstreams: every redispatch of a branch that
    committed real work and then ran out of turns appended one more empty
    `chore: ... (incomplete step)` commit, because the "is it already marked?" question
    was asked from that dispatch's own head — where the answer is always no. The cost is
    not only a noisy branch: recovery attests every unattested marker it finds, so the
    accumulation reaches the published history too.

    Two real dispatches against a real origin, each committing its own work and leaving
    a clean tree before it runs out of turns, then the real recovery that publishes it.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-remarked")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    workspace = Workspace(tmp_path / "remarked-worktrees")

    def committing_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        """Commit this round's own work, leave the tree clean, and run out of turns."""
        worktree = Path(project_dir)
        round_file = f"{task.split()[0]}.txt"
        (worktree / round_file).write_text(f"partial work: {task}\n", encoding="utf-8")
        gitops.add_all(worktree)
        gitops.commit(worktree, f"wip: {task}")
        assert not gitops.is_dirty(worktree)
        return Report(persona, 1, False, False, 2, [], {}, {}, "")

    first = run_repo_task(
        str(canonical),
        "first round of partial work",
        "engineer",
        workspace=workspace,
        branch="feature/remarked",
        dispatch_fn=committing_dispatch,
        recorded_gate=["true"],
    )

    assert first.outcome == "not-completed" and isinstance(first.resume, Resume)
    first_markers = incomplete_commits(canonical, "origin/main", first.branch)
    assert len(first_markers) == 1, sorted(first_markers)

    second = run_repo_task(
        str(canonical),
        "second round of partial work",
        "engineer",
        workspace=workspace,
        dispatch_fn=committing_dispatch,
        recorded_gate=["true"],
        resume=first.resume,
    )

    # The second round resumed the same branch, committed to it, and is still
    # not-completed with work worth recovering. It simply did not mark it again.
    assert second.outcome == "not-completed" and second.branch == first.branch
    assert second.resume is not None
    assert _has_file(canonical, second.branch, "second.txt")
    assert incomplete_commits(canonical, "origin/main", second.branch) == first_markers

    recovered = recover_repo(
        canonical,
        second.branch,
        workspace_root=tmp_path / "remarked-recovery-worktrees",
        recorded_gate=["true"],
    )

    # One marker, so one attestation: what recovery publishes accounts for this
    # branch's incomplete provenance exactly once, and carries both rounds' work.
    assert recovered.ok and recovered.outcome == "merged", recovered.detail
    attested = [
        line
        for commit in gitops.log_messages(canonical, next(iter(first_markers)), second.branch)
        for line in commit.message.splitlines()
        if line.startswith(RECOVERY_TRAILER)
    ]
    assert attested == [f"{RECOVERY_TRAILER} {next(iter(first_markers))}"], attested
    assert _has_file(origin, "main", "first.txt") and _has_file(origin, "main", "second.txt")


@pytest.mark.parametrize(
    ("rejection", "expected_outcome", "expected_detail"),
    [
        ("gate", "gate-failed", "repository pre-push gate rejected recovery"),
        ("transport", "error", "recovery push"),
    ],
)
def test_recovery_push_failure_is_recorded_and_preserves_branch(
    tmp_path, bare_origin, rejection: str, expected_outcome: str, expected_detail: str
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-recovery-gate-failure")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")

    preserved = run_repo_task(
        str(canonical),
        "Preserve work for a recovery rejected by the merge-path gate.",
        "engineer",
        workspace=Workspace(tmp_path / "recovery-gate-source-worktrees"),
        branch="feature/recovery-gate-failure",
        dispatch_fn=make_writing_dispatch(filename="preserved.txt", completed=False),
        recorded_gate=["true"],
    )
    assert preserved.outcome == "not-completed" and preserved.resume is not None
    checkpoint = gitops.ref_sha(canonical, preserved.branch)
    if rejection == "gate":
        install_pre_push_hook(
            canonical,
            "printf 'pre-push: complete gate failed during recovery\\n' >&2\nexit 1",
        )
    else:
        receive = origin / "hooks" / "pre-receive"
        receive.write_text("#!/bin/sh\nprintf 'remote denied\\n' >&2\nexit 1\n", encoding="utf-8")
        receive.chmod(0o755)

    recovered = recover_repo(
        canonical,
        preserved.branch,
        workspace_root=tmp_path / "recovery-gate-failure-worktrees",
        recorded_gate=["true"],
    )

    assert recovered.outcome == expected_outcome
    assert expected_detail in recovered.detail
    assert gitops.is_ancestor(canonical, checkpoint, preserved.branch)
    assert not _has_file(origin, "main", "preserved.txt")


@pytest.mark.parametrize(
    ("rejection", "expected_outcome", "expected_detail"),
    [
        ("gate", "gate-failed", "repository pre-push gate rejected recovery"),
        ("transport", "error", "recovery push"),
    ],
)
def test_remote_recovery_push_failure_is_classified_and_opens_no_pr(
    tmp_path, bare_origin, rejection: str, expected_outcome: str, expected_detail: str
) -> None:
    """A remote recovery's branch push is gated before any PR exists.

    The remote path pushes the branch from `attest_and_push` and only then asks
    GitHub for a PR, so a rejection here has to settle the node itself. A hook
    rejection reads as the repository's gate; a transport rejection, which Git
    reports indistinguishably apart from its text, stays an `error` carrying the
    remote's own diagnostic.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"canonical-remote-recovery-{rejection}")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner", gate="true")
    github = FakeGitHub(origin)

    preserved = run_repo_task(
        str(canonical),
        "Preserve remote work whose recovery push is rejected.",
        "engineer",
        workspace=Workspace(tmp_path / f"remote-recovery-source-{rejection}"),
        branch=f"feature/remote-recovery-{rejection}",
        github=github,
        dispatch_fn=make_writing_dispatch(filename="preserved.txt", completed=False),
        recorded_gate=["true"],
    )
    assert preserved.outcome == "not-completed" and preserved.resume is not None
    checkpoint = gitops.ref_sha(canonical, preserved.branch)
    open_prs = len(github._prs)
    if rejection == "gate":
        install_pre_push_hook(
            canonical,
            "printf 'pre-push: complete gate failed during remote recovery\\n' >&2\nexit 1",
        )
    else:
        receive = origin / "hooks" / "pre-receive"
        receive.write_text("#!/bin/sh\nprintf 'remote denied\\n' >&2\nexit 1\n", encoding="utf-8")
        receive.chmod(0o755)

    recovered = recover_repo(
        canonical,
        preserved.branch,
        workspace_root=tmp_path / f"remote-recovery-{rejection}-worktrees",
        github=github,
        recorded_gate=["true"],
    )

    assert recovered.outcome == expected_outcome
    assert expected_detail in recovered.detail
    assert gitops.is_ancestor(canonical, checkpoint, preserved.branch)
    # The push precedes PR creation, so a rejected recovery leaves no PR behind.
    assert len(github._prs) == open_prs
    assert not _has_file(origin, "main", "preserved.txt")


def test_remote_recovery_publishes_on_required_checks_without_a_pre_push_hook(
    tmp_path, bare_origin
) -> None:
    """Recovery admits required PR status checks as its only merge-path gate.

    This is the journey that carries the design's risk: with the orchestrator's
    own gate run gone, an identity covered *only* by branch protection has to
    still recover and merge. It needs a genuine GitHub identity, because required
    checks count as coverage only for one — so the bare origin is served over real
    TLS at its GitHub clone URL and every fetch and push here crosses the network.
    """
    origin = bare_origin()
    with serve_github_origin(origin, tmp_path, slug="acme/recovered") as remote:
        canonical = gitops.clone(origin, tmp_path / "canonical-required-checks-recovery")
        gitops.hooks_dir(canonical).joinpath("pre-push").unlink()
        remote.attach(canonical)
        Registry().register(
            "acme/recovered",
            str(canonical),
            workflow="remote",
            repo_type="single-owner",
            gate="true",
        )
        github = FakeGitHub(origin, required=("complete-gate",))

        preserved = run_repo_task(
            "acme/recovered",
            "Preserve work recovered through required PR status checks alone.",
            "engineer",
            workspace=Workspace(tmp_path / "required-checks-recovery-source"),
            branch="feature/required-checks-recovery",
            github=github,
            dispatch_fn=make_writing_dispatch(filename="recovered.txt", completed=False),
            recorded_gate=["true"],
        )
        assert preserved.outcome == "not-completed", preserved.detail
        assert gitops.hooks_dir(canonical).joinpath("pre-push").exists() is False

        recovered = recover_repo(
            "acme/recovered",
            preserved.branch,
            workspace_root=tmp_path / "required-checks-recovery-worktrees",
            github=github,
            recorded_gate=["true"],
            merge_policy="auto",
        )

    assert recovered.ok and recovered.outcome == "merged", recovered.detail
    assert _has_file(origin, "main", "recovered.txt")


def test_stacked_lifecycle_publishes_on_required_checks_without_a_pre_push_hook(
    tmp_path, bare_origin
) -> None:
    """The admission rule holds for a stacked PR base, not just the root.

    Coverage is judged from required checks on the repository's *default* branch,
    while a stacked node publishes onto its parent's branch instead. That makes
    the stacked case a distinct admission, so it gets its own journey: with no
    pre-push hook anywhere, the child still dispatches and opens its PR against
    the parent branch rather than the root.
    """
    origin = bare_origin()
    with serve_github_origin(origin, tmp_path, slug="acme/stacked") as remote:
        canonical = gitops.clone(origin, tmp_path / "canonical-stacked-required-checks")
        gitops.hooks_dir(canonical).joinpath("pre-push").unlink()
        remote.attach(canonical)
        Registry().register(
            "acme/stacked", str(canonical), workflow="remote", repo_type="team", gate="true"
        )
        github = FakeGitHub(origin, required=("complete-gate",))
        workspace = Workspace(tmp_path / "stacked-required-checks-worktrees")

        parent = run_repo_task(
            "acme/stacked",
            "Open the stack's parent PR.",
            "engineer",
            workspace=workspace,
            github=github,
            branch="feature/stacked-required-parent",
            dispatch_fn=make_writing_dispatch(filename="parent.txt"),
            recorded_gate=["true"],
        )
        assert parent.outcome == "pr-open", parent.detail

        child = run_repo_task(
            "acme/stacked",
            "Stack a child on the unmerged parent.",
            "engineer",
            workspace=workspace,
            github=github,
            branch="feature/stacked-required-child",
            dispatch_fn=make_writing_dispatch(filename="child.txt"),
            recorded_gate=["true"],
            stack_bases=[StackBase(parent.branch, repo=parent.repo)],
        )
        assert gitops.hooks_dir(canonical).joinpath("pre-push").exists() is False

    assert child.outcome == "pr-open", child.detail
    # The stacked base, not the root: this is the admission the root journey misses.
    assert child.pr_base == parent.branch
    assert _has_file(origin, child.branch, "child.txt")
    assert _has_file(origin, child.branch, "parent.txt")


def test_stacked_recovery_publishes_on_required_checks_without_a_pre_push_hook(
    tmp_path, bare_origin
) -> None:
    """Stacked recovery is admitted by required checks and targets the stack.

    Recovery re-derives the preserved branch's recorded PR base, so an identity
    covered only by branch protection has to be admitted *and* land its PR on the
    parent branch. Both are proven here with no pre-push hook in the checkout.
    """
    origin = bare_origin()
    with serve_github_origin(origin, tmp_path, slug="acme/stacked-recovery") as remote:
        canonical = gitops.clone(origin, tmp_path / "canonical-stacked-recovery")
        gitops.hooks_dir(canonical).joinpath("pre-push").unlink()
        remote.attach(canonical)
        Registry().register(
            "acme/stacked-recovery",
            str(canonical),
            workflow="remote",
            repo_type="team",
            gate="true",
        )
        github = FakeGitHub(origin, required=("complete-gate",))
        workspace = Workspace(tmp_path / "stacked-recovery-worktrees")

        parent = run_repo_task(
            "acme/stacked-recovery",
            "Open the parent the preserved child stacks on.",
            "engineer",
            workspace=workspace,
            github=github,
            branch="feature/stacked-recovery-parent",
            dispatch_fn=make_writing_dispatch(filename="parent.txt"),
            recorded_gate=["true"],
        )
        assert parent.outcome == "pr-open", parent.detail

        preserved = run_repo_task(
            "acme/stacked-recovery",
            "Preserve a stacked child for recovery through required checks.",
            "engineer",
            workspace=workspace,
            github=github,
            branch="feature/stacked-recovery-child",
            dispatch_fn=make_writing_dispatch(filename="child.txt", completed=False),
            recorded_gate=["true"],
            stack_bases=[StackBase(parent.branch, repo=parent.repo)],
        )
        assert preserved.outcome == "not-completed", preserved.detail

        recovered = recover_repo(
            "acme/stacked-recovery",
            preserved.branch,
            workspace_root=tmp_path / "stacked-recovery-recover-worktrees",
            github=github,
            recorded_gate=["true"],
        )
        assert gitops.hooks_dir(canonical).joinpath("pre-push").exists() is False

    assert recovered.ok and recovered.outcome == "pr-open", recovered.detail
    assert recovered.pr_base == parent.branch
    assert _has_file(origin, preserved.branch, "child.txt")


def test_recovery_refuses_uncovered_identity_and_preserves_branch(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-uncovered-recovery")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")

    preserved = run_repo_task(
        str(canonical),
        "Preserve work whose identity later loses merge-path coverage.",
        "engineer",
        workspace=Workspace(tmp_path / "uncovered-recovery-source-worktrees"),
        branch="feature/uncovered-recovery",
        dispatch_fn=make_writing_dispatch(filename="preserved.txt", completed=False),
        recorded_gate=["true"],
    )
    assert preserved.outcome == "not-completed"
    checkpoint = gitops.ref_sha(canonical, preserved.branch)
    gitops.hooks_dir(canonical).joinpath("pre-push").unlink()

    with pytest.raises(RegistryError, match="recovery refused for identity"):
        recover_repo(
            canonical,
            preserved.branch,
            workspace_root=tmp_path / "uncovered-recovery-worktrees",
            recorded_gate=["true"],
        )

    assert gitops.ref_sha(canonical, preserved.branch) == checkpoint
    assert not _has_file(origin, "main", "preserved.txt")


@pytest.mark.parametrize(
    ("failure", "expected_outcome"),
    [("conflict", "sync-conflict"), ("gate", "pr-open")],
)
def test_remote_stacked_recovery_failures_preserve_synthetic_base(
    tmp_path, bare_origin, failure: str, expected_outcome: str
) -> None:
    origin = bare_origin({"shared.txt": "root\n"})
    canonical = gitops.clone(origin, tmp_path / f"canonical-stacked-recovery-{failure}")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    synthetic = f"ai-orchestrator/stack-base/recovery-{failure}"
    branch = f"feature/stacked-recovery-{failure}"
    subprocess.run(["git", "branch", synthetic, "main"], cwd=canonical, check=True)
    gitops.push(canonical, synthetic, set_upstream=False)
    subprocess.run(["git", "checkout", "-b", branch, synthetic], cwd=canonical, check=True)
    (canonical / "shared.txt").write_text("preserved\n", encoding="utf-8")
    gitops.add_all(canonical)
    metadata = format_preserved_step_metadata("main", "engineer")
    gitops.commit(
        canonical,
        "chore: preserve stacked recovery (incomplete step)\n\n"
        f"{metadata}, preserved by ai-orchestrator after the dispatch did not complete.\n\n"
        f"{INCOMPLETE_TRAILER}\n{PR_BASE_TRAILER} {synthetic}",
    )
    gitops.push(canonical, branch, set_upstream=False)
    subprocess.run(["git", "checkout", synthetic], cwd=canonical, check=True)
    if failure == "conflict":
        (canonical / "shared.txt").write_text("advanced\n", encoding="utf-8")
        gitops.add_all(canonical)
        gitops.commit(canonical, "advance synthetic stack base")
        gitops.push(canonical, synthetic, set_upstream=False)
    subprocess.run(["git", "checkout", "main"], cwd=canonical, check=True)

    recovered = recover_repo(
        canonical,
        branch,
        workspace_root=tmp_path / f"stacked-recovery-{failure}-worktrees",
        github=FakeGitHub(origin),
        recorded_gate=["false" if failure == "gate" else "true"],
    )

    assert recovered.outcome == expected_outcome
    assert recovered.pr_base == synthetic
    assert recovered.synthetic_stack_base == synthetic


def test_local_recovery_conflict_resumes_worker_then_requeues(tmp_path, bare_origin) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    canonical = gitops.clone(origin, tmp_path / "canonical-recovery-conflict")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    partial = run_repo_task(
        str(canonical),
        "Preserve a conflicting edit.",
        "engineer",
        workspace=Workspace(tmp_path / "recovery-conflict-initial"),
        branch="feature/recovery-conflict",
        dispatch_fn=make_writing_dispatch(
            filename="shared.txt", content="preserved branch", completed=False
        ),
        recorded_gate=["true"],
    )
    assert partial.outcome == "not-completed"
    _advance_origin(tmp_path, origin, "shared.txt", "advanced base\n")
    sessions: list[tuple[str, str]] = []

    def resolving_dispatch(
        persona: str,
        task: str,
        *,
        project_dir: str,
        session: str,
        env: dict[str, str],
        **_: object,
    ) -> Report:
        path = Path(project_dir) / "shared.txt"
        assert "Resolve the content conflict" in task
        assert "<<<<<<<" in path.read_text(encoding="utf-8")
        # The resolver proves its resolution with the same gate the recovery push
        # will run, so it must resolve the base that push publishes onto.
        assert env["ORCHESTRATOR_COMPARISON_REMOTE"] == "origin"
        assert env["ORCHESTRATOR_COMPARISON_BASE"] == "main"
        sessions.append((session, project_dir))
        path.write_text("advanced base\npreserved branch by engineer\n", encoding="utf-8")
        gitops.add_all(project_dir)
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    recovered = recover_repo(
        canonical,
        partial.branch,
        workspace_root=tmp_path / "recovery-conflict-worktrees",
        recorded_gate=["git", "diff", "--check", "origin/main...HEAD"],
        dispatch_fn=resolving_dispatch,
    )

    assert recovered.outcome == "merged"
    # A recovery cuts its own worktree, so the resolver's conversation is named for
    # the branch *and* that directory — a bare branch name would resume a session
    # the harness recorded somewhere the recovery does not run.
    assert sessions == [(f"{scoped_session(partial.branch, sessions[0][1])}:main", sessions[0][1])]
    assert sessions[0][0].startswith(partial.branch)
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "show", "main:shared.txt"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        == "advanced base\npreserved branch by engineer\n"
    )


@pytest.mark.parametrize("resolver_commits", [False, True])
def test_local_recovery_incomplete_resolver_preserves_branch(
    tmp_path, bare_origin, resolver_commits: bool
) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    canonical = gitops.clone(origin, tmp_path / "canonical-incomplete-recovery")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    partial = run_repo_task(
        str(canonical),
        "Preserve a conflicting recovery edit.",
        "engineer",
        workspace=Workspace(tmp_path / "incomplete-recovery-initial"),
        dispatch_fn=make_writing_dispatch(
            filename="shared.txt", content="preserved", completed=False
        ),
        recorded_gate=["true"],
    )
    _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")

    def incomplete_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        if resolver_commits:
            path = Path(project_dir) / "shared.txt"
            path.write_text("advanced\npreserved by engineer\n", encoding="utf-8")
            gitops.add_all(project_dir)
        return Report(persona, 1, False, False, 2, [], {}, {}, "", max_turns=2)

    recovered = recover_repo(
        canonical,
        partial.branch,
        workspace_root=tmp_path / "incomplete-recovery-worktrees",
        recorded_gate=["true"],
        dispatch_fn=incomplete_dispatch,
    )

    assert recovered.outcome == "sync-conflict"
    assert "did not complete" in recovered.detail
    assert gitops.branch_exists(canonical, partial.branch)


def test_local_recovery_conflict_resolution_exhaustion_is_bounded(tmp_path, bare_origin) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    canonical = gitops.clone(origin, tmp_path / "canonical-exhausted-recovery")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    partial = run_repo_task(
        str(canonical),
        "Preserve a persistently conflicting recovery edit.",
        "engineer",
        workspace=Workspace(tmp_path / "exhausted-recovery-initial"),
        dispatch_fn=make_writing_dispatch(
            filename="shared.txt", content="preserved", completed=False
        ),
        recorded_gate=["true"],
    )
    _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")
    resolutions = 0

    def unresolved_dispatch(persona: str, task: str, **_: object) -> Report:
        nonlocal resolutions
        resolutions += 1
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    recovered = recover_repo(
        canonical,
        partial.branch,
        workspace_root=tmp_path / "exhausted-recovery-worktrees",
        recorded_gate=["true"],
        dispatch_fn=unresolved_dispatch,
    )

    assert recovered.outcome == "sync-conflict"
    assert resolutions == MAX_MERGE_CONFLICT_RESOLUTIONS
    assert f"after {MAX_MERGE_CONFLICT_RESOLUTIONS} resolve-and-requeue cycles" in recovered.detail


@pytest.mark.parametrize(
    ("step_id", "persona", "expected"),
    [("bad/step", "engineer", "step metadata"), ("main", "Engineer!", "persona metadata")],
)
def test_local_recovery_rejects_invalid_worker_metadata(
    tmp_path, bare_origin, step_id: str, persona: str, expected: str
) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    label = expected.split()[0]
    canonical = gitops.clone(origin, tmp_path / f"canonical-invalid-{label}")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    partial = run_repo_task(
        str(canonical),
        "Preserve work with metadata that will be corrupted.",
        "engineer",
        workspace=Workspace(tmp_path / f"invalid-{label}-initial"),
        dispatch_fn=make_writing_dispatch(
            filename="shared.txt", content="preserved", completed=False
        ),
        recorded_gate=["true"],
    )
    subprocess.run(["git", "checkout", partial.branch], cwd=canonical, check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "--amend",
            "-m",
            "chore: corrupted preserved metadata\n\n"
            f"Partial work from step {step_id} (persona: {persona}), preserved by "
            "ai-orchestrator after the dispatch did not complete.\n\n"
            f"{INCOMPLETE_TRAILER}\n{PR_BASE_TRAILER} main",
        ],
        cwd=canonical,
        check=True,
    )
    subprocess.run(["git", "checkout", "main"], cwd=canonical, check=True)
    _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")

    recovered = recover_repo(
        canonical,
        partial.branch,
        workspace_root=tmp_path / f"invalid-{label}-recovery",
        recorded_gate=["true"],
    )

    assert recovered.outcome == "sync-conflict"
    assert expected in recovered.detail


def test_cooperative_real_dispatch_cancellation_preserves_and_recovers_branch(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-cancelled")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    workspace = Workspace(tmp_path / "cancelled-worktrees")
    cancel = threading.Event()
    witness = tmp_path / "cancelled.ticks"
    # The second agent turn holds instead of sleeping, so cancellation always lands
    # on a dispatch that is genuinely mid-work with partial work already committed.
    held = Rendezvous.at(tmp_path, "cancelled")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_repo_task,
            str(canonical),
            f"slow-branch {witness} write-change{held.sentinels(1)}",
            "engineer",
            workspace=workspace,
            base_path=command_base(),
            persona_dir=personas_dir,
            branch="feature/cooperative-cancel",
            recorded_gate=["test", "-f", "CHANGE.txt"],
            cancel=cancel,
        )
        deadline = e2e_deadline(15)
        while time.monotonic() < deadline:
            changes = list((tmp_path / "cancelled-worktrees").rglob("CHANGE.txt"))
            # The second turn parks before it writes anything, so its arrival is what
            # says the first turn's partial work is already on disk.
            if changes and held.arrived():
                break
            time.sleep(0.02)
        else:
            pytest.fail("real dispatch did not produce partial work before cancellation")
        cancel.set()
        result = future.result(timeout=e2e_timeout(15))

    assert result.outcome == "not-completed"
    assert isinstance(result.resume, Resume)
    assert result.resume.checkpoint == gitops.ref_sha(canonical, result.branch)
    assert result.branch not in gitops.worktrees(canonical)
    assert incomplete_commits(canonical, "origin/main", result.branch)

    recovered = recover_repo(
        canonical,
        result.branch,
        workspace_root=tmp_path / "cancelled-recovery-worktrees",
        recorded_gate=["test", "-f", "CHANGE.txt"],
    )
    assert recovered.ok and recovered.outcome == "merged"
    assert _has_file(origin, "main", "CHANGE.txt")


def test_cancellation_after_publication_starts_finishes_authoritatively(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-publication-cancel")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    push_started = tmp_path / "publication.started"
    hook = origin / "hooks" / "pre-receive"
    hook.write_text(
        f"#!/bin/sh\ntouch {shlex.quote(str(push_started))}\nsleep 1\nexit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    cancel = threading.Event()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_repo_task,
            str(canonical),
            "complete-now write-change",
            "engineer",
            workspace=Workspace(tmp_path / "publication-cancel-worktrees"),
            base_path=command_base(),
            persona_dir=personas_dir,
            branch="feature/publication-cancel",
            recorded_gate=["test", "-f", "CHANGE.txt"],
            cancel=cancel,
        )
        deadline = e2e_deadline(15)
        while time.monotonic() < deadline and not push_started.exists():
            time.sleep(0.02)
        assert push_started.exists()
        cancel.set()
        result = future.result(timeout=e2e_timeout(20))

    assert cancel.is_set()
    assert result.ok and result.outcome == "merged"
    assert result.resume is None
    assert _has_file(origin, "main", "CHANGE.txt")


def test_no_changes_produces_no_pr(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "A task the agent completes without editing anything.",
        "reviewer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename=None),
        recorded_gate=["true"],
    )
    assert result.outcome == "no-changes"
    assert not result.ok


def test_resumed_branch_setup_round_trips_through_telemetry_cli(tmp_path, bare_origin) -> None:
    """A tracked retry journals and surfaces setup for its existing branch worktree."""
    origin = bare_origin()
    workspace = _workspace(tmp_path / "workspace", origin)
    partial = run_repo_task(
        str(origin),
        "Preserve work for a real retry.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
    )
    assert isinstance(partial.resume, Resume)

    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "existing-branch"
    _, round_dir = prepare_round(
        run_dir,
        {"tasks": [{"id": "retry", "task": "Continue the preserved branch."}]},
    )
    journal = open_journal(run_dir, RunId("existing-branch"), 1)

    def lifecycle_runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            dispatch_fn=make_writing_dispatch(filename="completed.txt"),
            recorded_gate=["true"],
            resume=partial.resume,
            journal=journal,
        )

    result = run_graph(
        parse_graph(
            {
                "tasks": [
                    {
                        "id": "retry",
                        "repo": str(origin),
                        "persona": "engineer",
                        "task": "Continue the preserved branch.",
                    }
                ]
            }
        ),
        agent_runner=lambda _node: (_ for _ in ()).throw(
            AssertionError("this graph contains no direct agent")
        ),
        lifecycle_runner=lifecycle_runner,
        journal=journal,
        run_id=RunId("existing-branch"),
        round_number=1,
    )
    assert result.ok
    write_result(round_dir, graph_payload(result))

    setup_events = [event for event in journal.events() if event.kind == "setup-finished"]
    assert any(event.detail["operation"] == "worktree" for event in setup_events)

    indexed = subprocess.run(
        ["just", "telemetry", "--runs-dir", str(runs_dir), "--all"],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert indexed.returncode == 0, indexed.stderr
    observed = json.loads(indexed.stdout)["runs"][0]["timing"]
    # `setup_seconds` is a millisecond-rounded share of the run's wall clock handed
    # out after the categories ahead of it, so a positive value is a property of a
    # fast box, and the whole journalled total is a property of one that had the room
    # left. The journal-to-CLI round trip is the contract, and holds either way.
    journalled = sum(event.detail["seconds"] for event in setup_events)
    assert observed["setup_seconds"] == clipped_share_seconds(observed, "setup_seconds", journalled)


def test_real_lifecycle_outcomes_round_trip_through_telemetry_cli(tmp_path, bare_origin) -> None:
    """Produce every classification and metric from real git lifecycle journeys."""
    runs_dir = tmp_path / "runs"

    def run_recorded(
        run_id: str,
        *,
        origin: Path,
        github: FakeGitHub | None = None,
        dispatch_fn=None,
        recorded_gate: list[str] | None = None,
        timeout: float = 3600.0,
        clock=None,
        resume: Resume | None = None,
        workspace: Workspace | None = None,
    ):
        run_dir = runs_dir / run_id
        _, round_dir = prepare_round(run_dir, {"tasks": [{"id": "ship", "task": run_id}]})
        journal = open_journal(run_dir, RunId(run_id), 1)
        node = NodeJournal(journal, NodeId("ship"), RunId(run_id), 1)
        result = run_repo_task(
            "acme/widget" if github is not None else str(origin),
            run_id,
            "engineer",
            workspace=workspace or _workspace(tmp_path / run_id, origin),
            merge=GitHubMergeStrategy(github) if github is not None else None,
            url=str(origin),
            dispatch_fn=dispatch_fn or make_writing_dispatch(filename="change.txt"),
            recorded_gate=recorded_gate or ["true"],
            timeout=timeout,
            clock=clock or __import__("time").monotonic,
            sleep=lambda _seconds: None,
            journal=node,
            resume=resume,
        )
        item = result_payload(result)
        item.update(
            {
                "kind": "agent",
                "status": "done" if result.ok or result.outcome == "no-changes" else "failed",
            }
        )
        write_result(
            round_dir,
            {
                "ok": result.ok,
                "state": "complete" if result.ok else "failed",
                "started_order": ["ship"],
                "results": {"ship": item},
            },
        )
        return result

    gate_origin = bare_origin()
    gate_workspace = _workspace(tmp_path / "gate-workspace", gate_origin)
    install_pre_push_hook(
        _shared_checkout(tmp_path / "gate-workspace"),
        "printf 'pre-push gate failed\\n' >&2\nexit 1",
    )
    run_recorded("gate", origin=gate_origin, workspace=gate_workspace)
    checks_origin = bare_origin()
    run_recorded(
        "checks",
        origin=checks_origin,
        github=FakeGitHub(checks_origin, fail_checks=True),
    )
    timeout_origin = bare_origin()
    ticks = iter((0.0, 100.0))
    run_recorded(
        "timeout",
        origin=timeout_origin,
        github=FakeGitHub(timeout_origin, auto_completes=False, check_states=("PENDING",)),
        timeout=1.0,
        clock=lambda: next(ticks),
    )

    class ClosedGitHub(FakeGitHub):
        def status(self, pr):
            self._prs[pr.number].closed = True
            return super().status(pr)

    closed_origin = bare_origin()
    run_recorded(
        "publication",
        origin=closed_origin,
        github=ClosedGitHub(closed_origin),
    )
    run_recorded(
        "no-diff",
        origin=bare_origin(),
        dispatch_fn=make_writing_dispatch(filename=None),
    )

    reused_origin = bare_origin()
    reused_workspace = _workspace(tmp_path / "reused-workspace", reused_origin)
    partial = run_repo_task(
        str(reused_origin),
        "Preserve work for a real retry.",
        "engineer",
        workspace=reused_workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
    )
    assert isinstance(partial.resume, Resume)
    reused = run_recorded(
        "reused",
        origin=reused_origin,
        workspace=reused_workspace,
        resume=partial.resume,
        recorded_gate=["false"],
    )
    assert reused.retry_lineage is not None
    assert reused.retry_lineage.disposition == "recovered"

    abandoned_origin = bare_origin()
    abandoned_workspace = _workspace(tmp_path / "abandoned-workspace", abandoned_origin)
    abandoned_partial = run_repo_task(
        str(abandoned_origin),
        "Preserve work before invalidating its provenance.",
        "engineer",
        workspace=abandoned_workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
    )
    assert isinstance(abandoned_partial.resume, Resume)
    recovery_worktree = abandoned_workspace.worktree(
        normalize_repo(str(abandoned_origin)), abandoned_partial.branch, base="origin/main"
    )
    gitops.commit_empty(
        recovery_worktree,
        "test: invalidate retry provenance\n\n"
        f"Orchestrator-Recovered-Incomplete: {abandoned_partial.resume.checkpoint}",
    )
    abandoned_workspace.remove_worktree(normalize_repo(str(abandoned_origin)), recovery_worktree)
    abandoned = run_recorded(
        "abandoned",
        origin=abandoned_origin,
        workspace=abandoned_workspace,
        resume=abandoned_partial.resume,
    )
    assert abandoned.retry_lineage is not None
    assert abandoned.retry_lineage.disposition == "abandoned"

    indexed = subprocess.run(
        ["just", "telemetry", "--runs-dir", str(runs_dir), "--all"],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert indexed.returncode == 0, indexed.stderr
    payload = json.loads(indexed.stdout)
    failures = {
        run["run_id"]: run["failure"]["class"] for run in payload["runs"] if "failure" in run
    }
    assert failures == {
        "gate": "gate",
        "checks": "checks",
        "timeout": "timeout",
        "publication": "publication",
    }
    assert payload["metrics"]["no_diff_dispatches"] == 1
    assert payload["metrics"]["retry_branch_reuses"] == 0
    assert payload["metrics"]["recovered_branches"] == 1
    assert payload["metrics"]["abandoned_branches"] == 1


# --- GitHub repo: PR + auto-merge on required checks -----------------------


def test_github_auto_merge_on_required_checks(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(origin, required=("ci",))
    workspace = _workspace(tmp_path, origin)
    result = run_repo_task(
        "acme/widget",  # a GitHub-style slug → GitHub strategy
        "Add a feature file.",
        "engineer",
        workspace=workspace,
        merge=GitHubMergeStrategy(github),
        url=str(origin),  # but clone/push the real bare repo
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        sleep=lambda _: None,
    )
    assert result.ok
    assert result.outcome == "merged"
    assert result.pr is not None and result.pr.number == 1
    assert _has_file(origin, "main", "feature.txt")
    canonical = workspace.execution_checkout(normalize_repo("acme/widget"))
    assert gitops.head_sha(canonical) == _tip(origin, "main")


def test_github_pr_title_comes_from_agent_commit_subject(tmp_path, bare_origin) -> None:
    """A real branch commit, not task prose, supplies the release-facing PR title."""
    origin = bare_origin()
    github = FakeGitHub(origin, required=("ci",))

    def committing_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        worktree = Path(project_dir)
        (worktree / "capture.txt").write_text("preserved output\n", encoding="utf-8")
        gitops.add_all(worktree)
        gitops.commit(worktree, "fix(capture): preserve failed session output")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        "acme/widget",
        "Fix a silent session-capture failure in oneharness, and the unfaithful behavior.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=committing_dispatch,
        recorded_gate=["true"],
        sleep=lambda _: None,
    )

    assert result.ok and result.pr is not None
    assert github._prs[result.pr.number].title == "fix(capture): preserve failed session output"
    merged_subject = subprocess.run(
        ["git", "-C", str(origin), "log", "-1", "--format=%s", "main"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert merged_subject == "fix(capture): preserve failed session output"


def test_github_second_run_reuses_open_pr_and_merges(tmp_path, bare_origin) -> None:
    class FindOrCreateFakeGitHub(FakeGitHub):
        def create_pr(
            self, repo: str, *, head: str, base: str, title: str, body: str, draft: bool = False
        ) -> PullRequest:
            for number, state in self._prs.items():
                if state.head == head and not state.merged:
                    return PullRequest(
                        number=number,
                        url=f"https://github.com/{repo}/pull/{number}",
                        repo=repo,
                        head=head,
                        base=base,
                    )
            return super().create_pr(
                repo, head=head, base=base, title=title, body=body, draft=draft
            )

    origin = bare_origin()
    github = FindOrCreateFakeGitHub(origin)
    workspace = _workspace(tmp_path, origin)
    branch = "orchestrator/continue-pr"
    first = run_repo_task(
        "acme/widget",
        "Start a feature.",
        "engineer",
        workspace=workspace,
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        branch=branch,
        dispatch_fn=make_writing_dispatch(filename="first.txt"),
        recorded_gate=["true"],
        merge_policy="none",
    )
    assert first.outcome == "pr-open"
    assert first.pr is not None

    continue_dispatch = make_writing_dispatch(filename="second.txt")

    def resume_open_pr(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        if persona == "pr-author":
            return cast(Report, continue_dispatch(persona, task, project_dir=project_dir, **kwargs))
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
        "engineer",
        workspace=workspace,
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        branch=branch,
        dispatch_fn=resume_open_pr,
        recorded_gate=["true"],
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
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        sleep=lambda _: None,
    )
    assert result.ok and result.outcome == "merged"
    assert _has_file(origin, "main", "feature.txt")


def test_github_direct_fallback_fails_fast_when_merge_returns_unmerged(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, auto_available=False, direct_completes=False)
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose direct remote merge stalls.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert not result.ok and result.outcome == "error"
    assert "ci=SUCCESS" in result.detail
    assert sleeps == []
    assert _tip(origin, "main") == before


def test_github_direct_fallback_polls_only_while_merge_is_in_progress(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(
        origin,
        auto_available=False,
        direct_completes=False,
        merge_progress_states=(False, False, True, False),
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose queued direct merge stops progressing.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert not result.ok and result.outcome == "error"
    assert "ci=SUCCESS" in result.detail
    assert sleeps == [15.0]
    assert _tip(origin, "main") == before


def test_github_direct_policy_polls_an_in_progress_merge_until_completion(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    github = FakeGitHub(
        origin,
        direct_completes=False,
        direct_merge_status_poll=4,
        merge_in_progress=True,
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose direct merge completes asynchronously.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        merge_policy="direct",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0]
    assert _has_file(origin, "main", "feature.txt")


def test_github_required_check_failure_blocks_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, fail_checks=True)
    sleeps: list[float] = []
    result = run_repo_task(
        "acme/widget",
        "Add a feature file.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )
    assert not result.ok
    assert result.outcome == "checks-failed"
    assert sleeps == []
    assert github.status_polls == 2
    assert _tip(origin, "main") == before  # required check failed → nothing merged


def test_github_pending_required_check_keeps_polling_then_merges(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(origin, check_states=("PENDING", "PENDING", "SUCCESS"))
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature after CI settles.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        sleep=sleeps.append,
        timeout=60.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0]
    assert github.status_polls >= 3
    assert _has_file(origin, "main", "feature.txt")


def test_github_settled_checks_fail_when_native_auto_merge_stalls(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, required=("ci",), auto_completes=False)
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose remote merge stalls.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert result.outcome == "error", result.detail
    assert not result.ok
    assert "[ci=SUCCESS]" in result.detail
    assert sleeps == []
    assert _tip(origin, "main") == before


def test_github_waits_for_required_checks_to_be_reported_then_merges(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    # The first status read checks whether the new PR is a draft; the merge poll
    # then observes no reported checks, pending CI, and finally green CI.
    github = FakeGitHub(
        origin,
        check_states=(None, None, "PENDING", "PENDING", "PENDING", "PENDING", "SUCCESS"),
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature after GitHub reports and settles CI.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=60.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0, 30.0, 60.0, 120.0, 120.0]
    assert github.status_polls == 7
    assert _has_file(origin, "main", "feature.txt")


def test_github_unreported_required_checks_wait_until_timeout(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, check_states=(None,), auto_completes=False)
    sleeps: list[float] = []
    ticks = iter([0.0, 0.0, 100.0])

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose required checks are never reported.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        clock=lambda: next(ticks),
        timeout=10.0,
    )

    assert not result.ok and result.outcome == "timeout"
    assert sleeps == [15.0]
    assert github.status_polls == 3
    assert _tip(origin, "main") == before


def test_github_direct_merge_waits_when_post_merge_checks_disappear(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(
        origin,
        auto_available=False,
        direct_completes=False,
        direct_merge_status_poll=4,
        check_states=("SUCCESS", "SUCCESS", None, "SUCCESS"),
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature despite an empty post-merge check response.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=60.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0]
    assert github.status_polls == 4
    assert _has_file(origin, "main", "feature.txt")


def test_github_auto_policy_polls_an_in_progress_merge_until_completion(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    github = FakeGitHub(
        origin,
        auto_completes=False,
        auto_merge_status_poll=3,
        merge_progress_states=(False, True, True),
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose native auto-merge completes asynchronously.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0]
    assert _has_file(origin, "main", "feature.txt")


def test_github_in_progress_merge_uses_timeout_as_backstop(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, direct_completes=False, merge_in_progress=True)
    sleeps: list[float] = []
    ticks = iter([0.0, 0.0, 100.0])

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose direct merge never finishes processing.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
        merge_policy="direct",
        sleep=sleeps.append,
        clock=lambda: next(ticks),
        timeout=10.0,
    )

    assert not result.ok and result.outcome == "timeout"
    assert sleeps == [15.0]
    assert _tip(origin, "main") == before


def test_remote_human_workstream_draft_checkpoint_and_safe_resume(tmp_path, bare_origin) -> None:
    """Remote pauses sync, gate, reuse one draft, and reject unsafe resumes."""

    class TrackingGitHub(FakeGitHub):
        def __init__(self, origin: Path) -> None:
            super().__init__(origin)
            self.created: list[int] = []
            self.reused: list[int] = []
            self.readied: list[int] = []

        def create_pr(
            self,
            repo: str,
            *,
            head: str,
            base: str,
            title: str,
            body: str,
            draft: bool = False,
        ) -> PullRequest:
            for number, state in self._prs.items():
                if (
                    state.head == head
                    and state.base == base
                    and not state.closed
                    and not state.merged
                ):
                    self.reused.append(number)
                    return PullRequest(
                        number=number,
                        url=f"https://github.com/{repo}/pull/{number}",
                        repo=repo,
                        head=head,
                        base=base,
                    )
            pr = super().create_pr(repo, head=head, base=base, title=title, body=body, draft=draft)
            self.created.append(pr.number)
            return pr

        def mark_ready(self, pr: PullRequest) -> None:
            self.readied.append(pr.number)
            super().mark_ready(pr)

    origin = bare_origin()
    subprocess.run(
        ["git", "-C", str(origin), "config", "receive.denyNonFastForwards", "true"],
        check=True,
    )
    canonical = gitops.clone(origin, tmp_path / "remote-human-canonical")
    workspace = Workspace(
        tmp_path / "remote-human-worktrees",
        resolver=lambda _: canonical,
        workflow="remote",
        repo_type="single-owner",
    )
    github = TrackingGitHub(origin)

    empty = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=[Step("approve", task="Approve before work starts.", kind="human")],
        branch="feature/empty-human-pause",
        recorded_gate=["true"],
    )
    assert empty.outcome == "waiting-human" and empty.resume is not None
    assert empty.pr is None and empty.resume.pr is None and github.created == []
    assert not _has_file(origin, empty.branch, "README.md")

    dispatched: list[str] = []
    advanced_sha: str | None = None

    def writing_step(
        persona: str, task: str, *, project_dir: str, session: str, **_: object
    ) -> Report:
        nonlocal advanced_sha
        if persona == "pr-author":
            output = task.split(
                "Write the final body, and nothing else, to this absolute path:\n", 1
            )[1].splitlines()[0]
            Path(output).write_text(
                "## What\nPrepares and publishes the remote work.\n\n"
                "## Why\nSupports the gated workstream.\n",
                encoding="utf-8",
            )
            return Report(persona, 0, True, False, 1, [], {}, {}, "")
        sid = session.rsplit(":", 1)[-1]
        dispatched.append(sid)
        (Path(project_dir) / f"{sid}.txt").write_text(task + "\n", encoding="utf-8")
        if sid == "prepare" and advanced_sha is None:
            advanced_sha = _advance_origin(tmp_path, origin, "base.txt", "advanced base\n")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    gate_log = tmp_path / "remote-human-gates.log"
    gate = [
        "sh",
        "-c",
        f"echo gate >> {shlex.quote(str(gate_log))}; test -f base.txt && "
        'test -f prepare.txt && test -d "$ORCHESTRATOR_CACHE_DIR"',
    ]
    steps = [
        Step("prepare", "engineer", "prepare remote work"),
        Step("approve", task="Approve the checkpoint.", kind="human", deps=["prepare"]),
        Step("implement", "engineer", "implement after approval", deps=["approve"]),
        Step("release", task="Release the implementation.", kind="human", deps=["implement"]),
        Step("finalize", "engineer", "finalize publication", deps=["release"]),
    ]
    paused = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        branch="feature/remote-human-resume",
        dispatch_fn=writing_step,
        recorded_gate=gate,
        sleep=lambda _: None,
    )

    assert paused.outcome == "waiting-human" and paused.resume is not None
    assert advanced_sha is not None
    assert paused.pr is not None and paused.resume.pr == paused.pr.url
    assert github.created == [paused.pr.number] and github._prs[paused.pr.number].draft
    assert dispatched == ["prepare"]
    assert not gate_log.exists()
    assert paused.resume.checkpoint == _tip(origin, paused.branch)
    assert gitops.is_ancestor(canonical, advanced_sha, paused.resume.checkpoint)
    assert _tip(origin, "main") == advanced_sha

    second = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        recorded_gate=gate,
        sleep=lambda _: None,
        resume=replace(
            paused.resume,
            completed_steps=(*paused.resume.completed_steps, "approve"),
        ),
    )

    assert second.outcome == "waiting-human" and second.resume is not None
    assert second.pr is not None and second.pr.number == paused.pr.number
    assert github.created == [paused.pr.number] and github.reused == []
    assert github._prs[paused.pr.number].draft
    assert dispatched == ["prepare", "implement"]
    assert not gate_log.exists()
    assert gitops.is_ancestor(canonical, paused.resume.checkpoint, second.resume.checkpoint)
    assert second.resume.checkpoint == _tip(origin, second.branch)

    final_resume = replace(
        second.resume,
        completed_steps=(*second.resume.completed_steps, "release"),
    )
    github._prs[paused.pr.number].closed = True
    closed = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        recorded_gate=gate,
        resume=final_resume,
    )
    assert closed.outcome == "resume-failed" and "closed without merging" in closed.detail
    assert dispatched == ["prepare", "implement"]
    github._prs[paused.pr.number].closed = False

    github._prs[paused.pr.number].draft = False
    prematurely_ready = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        recorded_gate=gate,
        resume=final_resume,
    )
    assert prematurely_ready.outcome == "resume-failed" and "ready for review" in (
        prematurely_ready.detail
    )
    assert dispatched == ["prepare", "implement"]
    github._prs[paused.pr.number].draft = True

    saved_tip = _tip(origin, second.branch)
    subprocess.run(
        ["git", "-C", str(origin), "update-ref", f"refs/heads/{second.branch}", advanced_sha],
        check=True,
    )
    rewritten = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        recorded_gate=gate,
        resume=final_resume,
    )
    assert rewritten.outcome == "resume-failed" and "rewritten" in rewritten.detail
    assert dispatched == ["prepare", "implement"]
    subprocess.run(
        ["git", "-C", str(origin), "update-ref", f"refs/heads/{second.branch}", saved_tip],
        check=True,
    )

    rogue = workspace.worktree(
        normalize_repo("acme/widget"), second.branch, base=f"origin/{second.branch}"
    )
    (rogue / "unpublished.txt").write_text("must not enter the resumed draft\n", encoding="utf-8")
    gitops.add_all(rogue)
    gitops.commit(rogue, "unpublished local branch mutation")
    workspace.remove_worktree(normalize_repo("acme/widget"), rogue)
    local_ahead = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        recorded_gate=gate,
        resume=final_resume,
    )
    assert local_ahead.outcome == "resume-failed" and "unpublished or divergent" in (
        local_ahead.detail
    )
    assert dispatched == ["prepare", "implement"]
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace.clone_dir(normalize_repo("acme/widget"))),
            "update-ref",
            f"refs/heads/{second.branch}",
            saved_tip,
        ],
        check=True,
    )

    completed = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        recorded_gate=gate,
        sleep=lambda _: None,
        resume=final_resume,
    )

    assert completed.ok and completed.outcome == "merged", completed.detail
    assert completed.pr is not None and completed.pr.number == paused.pr.number
    assert github.created == [paused.pr.number]
    assert github.reused == []
    assert github.readied == [paused.pr.number]
    assert not github._prs[paused.pr.number].draft
    assert dispatched == ["prepare", "implement", "finalize"]
    assert not gate_log.exists()
    assert gitops.is_ancestor(canonical, second.resume.checkpoint, _tip(origin, completed.branch))
    assert _has_file(origin, "main", "prepare.txt")
    assert _has_file(origin, "main", "implement.txt")
    assert _has_file(origin, "main", "finalize.txt")


# --- multi-PR: one larger task across coordinated PRs ----------------------


def test_multi_pr_dag_across_repos(tmp_path, bare_origin) -> None:
    repo_x = bare_origin()
    repo_y = bare_origin()
    ws = _workspace(tmp_path, repo_x, repo_y)

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=ws,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            recorded_gate=["true"],
            journal=journal,
        )

    plan = RepoPlan(
        tasks=[
            RepoPlanNode("a", str(repo_x), "engineer", "part A on repo X"),
            RepoPlanNode("b", str(repo_y), "engineer", "part B on repo Y"),
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


def test_linear_team_stack_targets_open_dependency_and_includes_pr_link(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-linear-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "linear-stack-worktrees")
    github = FakeGitHub(origin)

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            recorded_gate=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "parent", str(canonical), "engineer", "parent", branch="feature/parent"
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "engineer",
                    "child",
                    deps=["parent"],
                    branch="feature/child",
                ),
            ]
        ),
        runner,
    )

    assert result.ok
    parent = result.results["parent"].result
    child = result.results["child"].result
    assert parent is not None and child is not None
    assert parent.outcome == child.outcome == "pr-open"
    assert child.pr_base == parent.branch
    assert child.pr is not None and child.pr.base == parent.branch
    assert parent.pr is not None and parent.pr.url in github._prs[2].body
    assert _has_file(origin, child.branch, "parent.txt")
    assert _has_file(origin, child.branch, "child.txt")
    assert not _has_file(origin, "main", "parent.txt")


def test_cross_round_human_gate_preserves_open_same_repo_stack(tmp_path, bare_origin) -> None:
    """Attesting a human between two repo nodes retains the parent's real PR base."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-human-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "human-stack-worktrees")
    github = FakeGitHub(origin)

    plan_mapping = {
        "tasks": [
            {
                "id": "parent",
                "repo": str(canonical),
                "persona": "engineer",
                "task": "parent",
                "branch": "feature/human-stack-parent",
            },
            {
                "id": "approval",
                "kind": "human",
                "task": "Approve the parent before the child starts.",
                "deps": ["parent"],
            },
            {
                "id": "child",
                "repo": str(canonical),
                "persona": "engineer",
                "task": "child",
                "branch": "feature/human-stack-child",
                "deps": ["approval"],
            },
        ]
    }

    def lifecycle_runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            recorded_gate=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    def no_direct_agent(_node):
        raise AssertionError("this graph contains no direct agent")

    first = run_graph(
        parse_graph(plan_mapping),
        agent_runner=no_direct_agent,
        lifecycle_runner=lifecycle_runner,
    )
    first_payload = graph_payload(first)
    parent = first.results["parent"].lifecycle
    assert first.state == "waiting" and parent is not None and parent.outcome == "pr-open"
    assert first.results["approval"].status == "waiting"
    assert first.results["child"].status == "blocked"

    continued_mapping = next_round(
        plan_mapping,
        first_payload,
        {"complete_human": ["approval"]},
    )
    child_mapping = continued_mapping["tasks"][0]
    assert child_mapping["id"] == "child" and child_mapping["deps"] == []
    assert child_mapping["stack_bases"][0]["branch"] == parent.branch

    second = run_graph(
        parse_graph(continued_mapping),
        agent_runner=no_direct_agent,
        lifecycle_runner=lifecycle_runner,
    )
    child = second.results["child"].lifecycle
    assert second.ok and child is not None and child.outcome == "pr-open"
    assert child.pr_base == parent.branch
    assert child.pr is not None and child.pr.base == parent.branch
    assert _has_file(origin, child.branch, "parent.txt")
    assert _has_file(origin, child.branch, "child.txt")
    assert not _has_file(origin, "main", "parent.txt")


def test_cross_round_root_merged_pr_anchor_is_dropped_after_squash(tmp_path, bare_origin) -> None:
    """A root-landed squash is detected from PR state even without commit ancestry."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-squash-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "squash-stack-worktrees")
    github = FakeGitHub(origin)
    parent = run_repo_task(
        str(canonical),
        "parent",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/squashed-parent",
        dispatch_fn=make_writing_dispatch(filename="parent.txt"),
        recorded_gate=["true"],
    )
    assert parent.outcome == "pr-open" and parent.pr is not None

    merger = gitops.clone(origin, tmp_path / "squash-merger")
    subprocess.run(
        ["git", "-C", str(merger), "merge", "--squash", f"origin/{parent.branch}"],
        check=True,
        capture_output=True,
    )
    gitops.commit(merger, "squash parent")
    gitops.push(merger, "main", set_upstream=False)
    github._prs[parent.pr.number].merged = True
    gitops.fetch(canonical)
    assert not gitops.is_ancestor(canonical, f"origin/{parent.branch}", "origin/main")

    child = run_repo_task(
        str(canonical),
        "child",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/post-squash-child",
        dispatch_fn=make_writing_dispatch(filename="child.txt"),
        recorded_gate=["true"],
        stack_bases=[
            StackBase(
                parent.branch,
                repo=parent.repo,
                identity=parent.publication_identity,
                base_branch=parent.base_branch,
                pr=parent.pr.url,
                pr_base=parent.pr_base,
            )
        ],
    )

    assert child.outcome == "pr-open" and child.pr_base == "main"
    assert child.stack_bases == []
    assert child.pr is not None and child.pr.base == "main"
    assert _has_file(origin, child.branch, "parent.txt")


def test_legacy_untyped_anchor_from_other_repo_is_scheduling_only(tmp_path, bare_origin) -> None:
    left_origin = bare_origin()
    right_origin = bare_origin()
    left = gitops.clone(left_origin, tmp_path / "legacy-anchor-left")
    right = gitops.clone(right_origin, tmp_path / "legacy-anchor-right")
    registry = Registry()
    registry.register(str(left), workflow="remote", repo_type="team")
    registry.register(str(right), workflow="remote", repo_type="team")
    left_result = run_repo_task(
        str(left),
        "left",
        "engineer",
        workspace=Workspace(tmp_path / "legacy-left-worktrees"),
        github=FakeGitHub(left_origin),
        branch="feature/legacy-left",
        dispatch_fn=make_writing_dispatch(filename="left.txt"),
        recorded_gate=["true"],
    )
    assert left_result.outcome == "pr-open"

    right_result = run_repo_task(
        str(right),
        "right",
        "engineer",
        workspace=Workspace(tmp_path / "legacy-right-worktrees"),
        github=FakeGitHub(right_origin),
        branch="feature/legacy-right",
        dispatch_fn=make_writing_dispatch(filename="right.txt"),
        recorded_gate=["true"],
        stack_bases=[StackBase(left_result.branch, repo=left_result.repo)],
    )

    assert right_result.outcome == "pr-open" and right_result.pr_base == "main"
    assert right_result.stack_bases == []
    assert _has_file(right_origin, right_result.branch, "right.txt")


def test_stack_anchor_for_different_root_fails_before_dispatch(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-root-mismatch")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "root-mismatch-worktrees")
    github = FakeGitHub(origin)
    parent = run_repo_task(
        str(canonical),
        "parent",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/root-mismatch-parent",
        dispatch_fn=make_writing_dispatch(filename="parent.txt"),
        recorded_gate=["true"],
    )
    dispatched: list[str] = []

    def dispatch_fn(persona: str, task: str, **_: object) -> Report:
        dispatched.append(task)
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    child = run_repo_task(
        str(canonical),
        "child",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/root-mismatch-child",
        dispatch_fn=dispatch_fn,
        recorded_gate=["true"],
        stack_bases=[
            StackBase(
                parent.branch,
                repo=parent.repo,
                identity=parent.publication_identity,
                base_branch="release",
            )
        ],
    )

    assert child.outcome == "stack-conflict" and child.pr_base == "main"
    assert "belongs to root 'release'" in child.detail
    assert dispatched == []


def test_closed_and_missing_stack_anchors_fail_before_dispatch(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-invalid-anchors")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "invalid-anchor-worktrees")
    github = FakeGitHub(origin)
    parent = run_repo_task(
        str(canonical),
        "parent",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/closed-parent",
        dispatch_fn=make_writing_dispatch(filename="parent.txt"),
        recorded_gate=["true"],
    )
    assert parent.pr is not None
    github._prs[parent.pr.number].closed = True
    anchor = StackBase(
        parent.branch,
        repo=parent.repo,
        identity=parent.publication_identity,
        base_branch=parent.base_branch,
        pr=parent.pr.url,
        pr_base=parent.pr_base,
    )

    closed = run_repo_task(
        str(canonical),
        "closed child",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/closed-child",
        dispatch_fn=make_writing_dispatch(filename="closed.txt"),
        recorded_gate=["true"],
        stack_bases=[anchor],
    )
    missing = run_repo_task(
        str(canonical),
        "missing child",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/missing-child",
        dispatch_fn=make_writing_dispatch(filename="missing.txt"),
        recorded_gate=["true"],
        stack_bases=[
            StackBase(
                "feature/deleted-parent",
                repo=parent.repo,
                identity=parent.publication_identity,
                base_branch=parent.base_branch,
            )
        ],
    )

    assert closed.outcome == "stack-conflict" and "closed without merging" in closed.detail
    assert missing.outcome == "stack-conflict" and "missing from origin" in missing.detail


def test_multi_parent_stack_uses_synthetic_base_and_child_only_diff(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-multi-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "multi-stack-worktrees")
    github = FakeGitHub(origin)

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            recorded_gate=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode("left", str(canonical), "engineer", "left", branch="feature/left"),
                RepoPlanNode("right", str(canonical), "engineer", "right", branch="feature/right"),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "engineer",
                    "child",
                    deps=["left", "right"],
                    branch="feature/combined-child",
                ),
            ],
            concurrency=2,
        ),
        runner,
    )

    child = result.results["child"].result
    assert result.ok and child is not None and child.outcome == "pr-open"
    synthetic = child.synthetic_stack_base
    assert synthetic is not None and synthetic.startswith("ai-orchestrator/stack-base/")
    assert child.pr_base == synthetic and child.pr is not None and child.pr.base == synthetic
    assert _has_file(origin, synthetic, "left.txt")
    assert _has_file(origin, synthetic, "right.txt")
    changed = subprocess.run(
        ["git", "-C", str(origin), "diff", "--name-only", synthetic, child.branch],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    assert changed == ["child.txt"]
    assert all(state.head != synthetic for state in github._prs.values())


def test_multi_parent_stack_deduplicates_ancestor_prerequisites(tmp_path, bare_origin) -> None:
    """A declared ancestor after its descendant is a safe no-op in stack assembly."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-ancestry-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "ancestry-stack-worktrees")
    github = FakeGitHub(origin)

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            recorded_gate=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "ancestor",
                    str(canonical),
                    "engineer",
                    "ancestor",
                    branch="feature/ancestor",
                ),
                RepoPlanNode(
                    "descendant",
                    str(canonical),
                    "engineer",
                    "descendant",
                    deps=["ancestor"],
                    branch="feature/descendant",
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "engineer",
                    "child",
                    deps=["descendant", "ancestor"],
                    branch="feature/ancestry-child",
                ),
            ]
        ),
        runner,
    )

    ancestor = result.results["ancestor"].result
    descendant = result.results["descendant"].result
    child = result.results["child"].result
    assert result.ok and ancestor is not None and descendant is not None and child is not None
    assert child.synthetic_stack_base is None
    assert child.pr is not None and child.pr.base == descendant.branch
    assert ancestor.pr is not None and descendant.pr is not None
    assert ancestor.pr.url not in github._prs[3].body
    assert descendant.pr.url in github._prs[3].body


def test_stack_conflict_aborts_before_child_dispatch_and_skips_descendant(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin({"shared.txt": "root\n"})
    canonical = gitops.clone(origin, tmp_path / "canonical-conflict-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "conflict-stack-worktrees")
    github = FakeGitHub(origin)
    dispatched: list[str] = []

    def writing_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        dispatched.append(task)
        path = Path(project_dir) / "shared.txt"
        path.write_text(f"{task}\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=writing_dispatch,
            recorded_gate=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "left",
                    str(canonical),
                    "engineer",
                    "left",
                    branch="feature/conflict-left",
                ),
                RepoPlanNode(
                    "right",
                    str(canonical),
                    "engineer",
                    "right",
                    branch="feature/conflict-right",
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "engineer",
                    "child",
                    deps=["left", "right"],
                    branch="feature/conflict-child",
                ),
                RepoPlanNode(
                    "descendant",
                    str(canonical),
                    "engineer",
                    "descendant",
                    deps=["child"],
                ),
            ],
            concurrency=2,
        ),
        runner,
    )

    child = result.results["child"].result
    assert child is not None and child.outcome == "stack-conflict"
    assert result.results["child"].status == "failed"
    assert result.results["descendant"].status == "skipped"
    assert "child" not in dispatched and "descendant" not in dispatched
    refs = subprocess.run(
        [
            "git",
            "-C",
            str(origin),
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads/ai-orchestrator/stack-base",
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert not refs.strip()
    assert not any(
        branch.startswith("ai-orchestrator/stack-base/") for branch in gitops.branches(canonical)
    )


# --- workstream: several onejudge on ONE PR -------------------------------


def test_local_human_workstream_removes_worktree_and_resumes_same_branch(
    tmp_path, bare_origin
) -> None:
    """A local agent → human → agent workstream publishes only after resume."""
    origin = bare_origin()
    before = _tip(origin, "main")
    workspace = _workspace(tmp_path, origin)
    dispatched: list[str] = []

    def writing_step(
        persona: str, task: str, *, project_dir: str, session: str, **_: object
    ) -> Report:
        sid = session.rsplit(":", 1)[-1]
        dispatched.append(sid)
        (Path(project_dir) / f"{sid}.txt").write_text(f"{task} by {persona}\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    gate_log = tmp_path / "local-human-gates.log"
    gate = [
        "sh",
        "-c",
        f"echo gate >> {shlex.quote(str(gate_log))}; test -f prepare.txt && test -f finalize.txt",
    ]
    steps = [
        Step("prepare", "engineer", "prepare the change"),
        Step("approve", task="Approve the prepared change.", kind="human", deps=["prepare"]),
        Step("finalize", "engineer", "finalize the change", deps=["approve"]),
    ]

    paused = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=steps,
        branch="feature/local-human-resume",
        dispatch_fn=writing_step,
        recorded_gate=gate,
    )

    assert paused.outcome == "waiting-human" and paused.resume is not None
    assert dispatched == ["prepare"]
    assert [step.status for step in paused.steps] == ["done", "waiting", "blocked"]
    assert paused.waiting_steps == ["approve"]
    assert paused.resume.branch == paused.branch == "feature/local-human-resume"
    assert paused.resume.base_branch == paused.resume.pr_base == "main"
    assert paused.resume.completed_steps == ("prepare",)
    clone = workspace.clone_dir(normalize_repo(str(origin)))
    assert paused.resume.checkpoint == gitops.ref_sha(clone, paused.branch)
    assert gitops.branch_exists(clone, paused.branch)
    assert not _has_file(origin, paused.branch, "prepare.txt")
    assert _tip(origin, "main") == before
    assert not gate_log.exists()
    worktrees = subprocess.run(
        ["git", "-C", str(clone), "worktree", "list", "--porcelain"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "local-human-resume" not in worktrees

    resume = replace(
        paused.resume,
        completed_steps=(*paused.resume.completed_steps, "approve"),
    )
    completed = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=steps,
        dispatch_fn=writing_step,
        recorded_gate=gate,
        resume=resume,
    )

    assert completed.ok and completed.outcome == "merged", completed.detail
    assert completed.branch == paused.branch
    assert dispatched == ["prepare", "finalize"]
    assert [step.status for step in completed.steps] == ["done", "done", "done"]
    assert _has_file(origin, "main", "prepare.txt")
    assert _has_file(origin, "main", "finalize.txt")
    assert not gate_log.exists()
    final_worktrees = subprocess.run(
        ["git", "-C", str(clone), "worktree", "list", "--porcelain"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "local-human-resume" not in final_worktrees


def test_workstream_multiple_onejudge_one_pr(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    steps = [
        Step("impl", "engineer", "implement the feature"),
        Step("test", "engineer", "add tests", deps=["impl"]),
        Step("docs", "docs-writer", "document it", deps=["impl"]),
    ]
    result = run_repo_task(
        str(origin),
        workspace=_workspace(tmp_path, origin),
        steps=steps,
        dispatch_fn=_per_step_dispatch(),
        recorded_gate=["true"],
    )
    assert result.ok and result.outcome == "merged"
    assert len(result.steps) == 3 and all(s.status == "done" for s in result.steps)
    step_subjects = [
        commit.message.splitlines()[0]
        for commit in gitops.log_messages(origin, before, result.branch)
    ]
    assert step_subjects == [
        "chore: implement the feature",
        "chore: add tests",
        "chore: document it",
    ]
    # All three steps' changes landed on origin main via ONE merge of ONE branch.
    for sid in ("impl", "test", "docs"):
        assert _has_file(origin, "main", f"{sid}.txt")


def test_step_commit_subject_comes_from_agent_commits(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")

    def partly_committing_dispatch(
        persona: str, task: str, *, project_dir: str, **_: object
    ) -> Report:
        worktree = Path(project_dir)
        (worktree / "committed.txt").write_text("agent commit\n", encoding="utf-8")
        gitops.add_all(worktree)
        gitops.commit(worktree, "fix(capture): retain failed output")
        (worktree / "remaining.txt").write_text("remaining step work\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(origin),
        "Repair capture using task prose that is not a commit subject.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=partly_committing_dispatch,
        recorded_gate=["true"],
    )

    assert result.ok
    subjects = [
        commit.message.splitlines()[0]
        for commit in gitops.log_messages(origin, before, result.branch)
    ]
    assert subjects == [
        "fix(capture): retain failed output",
        "fix(capture): retain failed output",
    ]


def test_workstream_step_failure_stops_and_skips_dependents(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    workspace = _workspace(tmp_path, origin)
    steps = [
        Step("impl", "engineer", "implement"),
        Step("test", "engineer", "add tests", deps=["impl"]),
    ]
    result = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=steps,
        dispatch_fn=_per_step_dispatch(fail_step="impl"),  # first step hits the turn cap
        recorded_gate=["true"],
    )
    assert not result.ok and result.outcome == "not-completed"
    by_id = {s.id: s.status for s in result.steps}
    assert by_id["impl"] == "not-completed" and by_id["test"] == "skipped"
    assert _tip(origin, "main") == before  # nothing pushed or merged
    clone = workspace.clone_dir(normalize_repo(str(origin)))
    assert gitops.ref_sha(clone, result.branch) == gitops.ref_sha(clone, "origin/main")
    assert incomplete_commits(clone, "origin/main", result.branch) == set()


def test_multi_pr_failure_skips_dependents(tmp_path, bare_origin) -> None:
    repo_x = bare_origin()
    ws = _workspace(tmp_path, repo_x)
    install_pre_push_hook(
        _shared_checkout(tmp_path),
        "if test -f a.txt; then printf 'pre-push gate failed\\n' >&2; exit 1; fi",
    )

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        # node 'a' fails its gate; 'b' depends on it and must be skipped.
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=ws,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            recorded_gate=["false"] if node.id == "a" else ["true"],
            journal=journal,
        )

    plan = RepoPlan(
        tasks=[
            RepoPlanNode("a", str(repo_x), "engineer", "failing part"),
            RepoPlanNode("b", str(repo_x), "reviewer", "dependent part", deps=["a"]),
        ],
        concurrency=2,
    )
    result = run_repo_plan(plan, runner)
    assert not result.ok
    assert result.results["a"].status == "failed"
    assert result.results["b"].status == "skipped"


def test_a_workstream_step_refused_by_the_provider_records_which_identity_refused(
    tmp_path, bare_origin
) -> None:
    """The lifecycle half of failure attribution, end to end on a real workstream.

    A direct agent node and a lifecycle step reach the provider by different paths,
    and only the direct one was covered. The night this work exists for lost
    lifecycle workstreams too, so a refused step has to settle as the refusal it
    was — naming the side and the identity in its journal, in the recorded result,
    and in the rolled-up line a planner reads — rather than propagating as a bare
    dispatch error that names neither.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-refused-step")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    refusal = (
        "provider error (supervisor): harness failed (quota) — judge-side codex "
        "quota exhausted mid-conversation; resets Aug 8"
    )

    def refused_by_the_provider(persona: str, task: str, **_: object) -> Report:
        raise DispatchError(refusal, failure_attribution=classify_provider_failure(refusal))

    journal = open_journal(tmp_path / "refused-run", RunId("refused"), 1)
    result = run_repo_task(
        str(canonical),
        "## What\nMeasure lifecycle cost.\n\n## Why\nA refused run must say who refused.\n",
        "engineer",
        workspace=Workspace(tmp_path / "refused-worktrees"),
        branch="feature/refused-by-provider",
        dispatch_fn=refused_by_the_provider,
        recorded_gate=["true"],
        journal=NodeJournal(journal, NodeId("work"), RunId("refused"), 1),
    )

    # Settled as a refusal rather than propagating: the workstream is not completed,
    # and the step it stopped on is recorded rather than lost to a raised error.
    assert result.outcome == "not-completed", result.detail
    attribution = result_payload(result)["failure_attribution"]
    assert (attribution["side"], attribution["identity"], attribution["cause"]) == (
        "judge",
        "codex",
        "quota_mid_conversation",
    )
    assert attribution["reset_time"] == "Aug 8"

    # The same fact reaches the journal, which is what the planner views fold.
    settled = [
        json.loads(line)
        for line in (tmp_path / "refused-run" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line)["kind"] == "step-settled"
    ]
    assert settled, "the refused step recorded no step-settled event"
    assert settled[-1]["detail"]["failure_attribution"] == attribution

    # And it reads as the one rolled-up line `just status` / `just runs` print.
    assert failure_rollups(tmp_path / "refused-run") == [
        "1 node failed on judge-side codex quota mid conversation, resets Aug 8"
    ]


def test_a_refused_step_preserves_the_work_its_worker_had_already_written(
    tmp_path, bare_origin
) -> None:
    """A refusal is a stop, and a stop preserves the branch like every other one.

    `quota_mid_conversation` is by definition a worker that was already working, so
    the worktree it is holding can carry real authored work. Settling that refusal
    as a recorded failure must go through the same preservation every other
    incomplete step takes; recording the attribution and dropping the work would
    trade one silent loss for another.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-refused-preserve")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    refusal = (
        "provider error (respond): harness failed (quota) — claude-code:alternate "
        "quota exhausted mid-conversation"
    )

    def refused_after_writing(persona: str, task: str, **kwargs: object) -> Report:
        (Path(cast(str, kwargs["project_dir"])) / "tiering.md").write_text(
            "the work the refusal interrupted\n", encoding="utf-8"
        )
        raise DispatchError(refusal, failure_attribution=classify_provider_failure(refusal))

    result = run_repo_task(
        str(canonical),
        "## What\nTier the workspace.\n\n## Why\nA refusal must not cost the work.\n",
        "engineer",
        workspace=Workspace(tmp_path / "refused-preserve-worktrees"),
        branch="feature/refused-mid-work",
        dispatch_fn=refused_after_writing,
        recorded_gate=["true"],
    )

    assert result.outcome == "not-completed", result.detail
    # Resumable, because there is work on the branch worth resuming onto.
    assert result.resume is not None and result.resume.mode == "retry"
    # The branch really carries it, under the marker recovery recognises.
    assert _subject(canonical, result.branch) == "chore: Tier the workspace. (incomplete step)"
    assert incomplete_commits(canonical, "main", result.branch)
    assert _has_file(canonical, result.branch, "tiering.md")
    # And the refusal is still attributed, not traded away for the preservation.
    attribution = result_payload(result)["failure_attribution"]
    assert (attribution["side"], attribution["identity"], attribution["cause"]) == (
        "agent",
        "claude-code:alternate",
        "quota_mid_conversation",
    )
