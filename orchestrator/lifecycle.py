"""Drive one subtask through a target repo's full lifecycle — and many at once.

`run_repo_task` is the unit of real work against an external repo:

    clone/worktree → dispatch (bypass mode) → local gate → commit → push → PR
    → merge once the repo's blocking (required) checks are green → clean up

`run_repo_plan` layers the same DAG scheduler `run_plan` uses over that unit, so
one larger task becomes **multiple isolated PRs** — independent PRs open in
parallel; a dependent PR waits for the one it needs to merge, then branches off
the updated base. Each node gets its own worktree, so parallel agents never
collide.

Two seams are injected so the offline gate drives everything else for real:
`dispatch_fn` (the paid harness — defaults to the real `dispatch`) and the
`GitHubBackend` (GitHub's PR/CI decisioning). Git itself is always real.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from . import BASE_CONFIG, PERSONA_DIR, gitops
from .config import ConfigError, load_yaml
from .dispatch import Report, dispatch
from .github import CliGitHubBackend, GitHubBackend, GitHubError, PullRequest
from .gitops import GitError
from .merge import GitHubMergeStrategy, LocalMergeStrategy, MergeContext, MergeStrategy
from .plan import NodeRun, schedule_dag
from .registry import RegistryError
from .runs import (
    RepoPlanPayload,
    RepoPlanResultItem,
    prepare_round,
    resolve_run_dir,
    status_counts,
    status_summary,
    write_result,
)
from .verify import VerifyResult, detect_gate, run_gate
from .workspace import RepoRef, Workflow, Workspace, normalize_repo

# A merge only completes when the PR's blocking (required) checks are green. The
# default `auto` policy uses GitHub native auto-merge (which by construction
# gates on required checks and ignores optional ones); `direct` polls and merges
# ourselves on green required checks; `none` opens the PR and stops.
MERGE_POLICIES = ("auto", "direct", "none")
MERGE_METHODS = ("squash", "merge", "rebase")

# Outcomes that count as the subtask succeeding.
_SUCCESS_OUTCOMES = frozenset({"merged", "pr-open"})

DispatchFn = Callable[..., Report]


@dataclass
class Step:
    """One onejudge dispatch within a PR workstream (shares the workstream branch)."""

    id: str
    persona: str
    task: str
    deps: list[str] = field(default_factory=list)
    max_turns: int | None = None
    done_when: str | None = None


@dataclass
class StepResult:
    id: str
    persona: str
    status: str  # done | not-completed | skipped
    report: Report | None = None


@dataclass
class LifecycleResult:
    """The outcome of driving one subtask (or a step workstream) through a repo."""

    repo: str
    task: str
    persona: str
    base_branch: str
    branch: str
    outcome: str  # merged | pr-open | not-completed | gate-failed | no-changes
    #             | checks-failed | closed | timeout | error
    pr: PullRequest | None = None
    report: Report | None = None
    verify: VerifyResult | None = None
    detail: str = ""
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome in _SUCCESS_OUTCOMES

    def summary(self) -> str:
        head = f"{self.repo} [{self.persona}] {self.branch}: {self.outcome}"
        if self.pr is not None:
            head += f" (PR #{self.pr.number})"
        if len(self.steps) > 1:
            head += f" [{sum(s.status == 'done' for s in self.steps)}/{len(self.steps)} steps]"
        if self.detail:
            head += f"\n  - {self.detail}"
        if self.verify is not None and not self.verify.ok:
            head += f"\n  - gate: {' '.join(self.verify.command)} failed"
        return head


def _short_hash(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()[:8]


def _default_branch_name(persona: str, task: str) -> str:
    return f"ai-orchestrator/{persona}/{_short_hash(persona, task)}"


def _default_title(persona: str, task: str) -> str:
    line = task.strip().splitlines()[0] if task.strip() else "orchestrated change"
    return line[:68] + ("…" if len(line) > 68 else "")


def _default_body(persona: str, task: str, report: Report | None) -> str:
    turns = report.assistant_turns if report else 0
    return (
        "## What\n"
        f"{task.strip()}\n\n"
        "## Why\n"
        f"Dispatched by ai-orchestrator (persona: `{persona}`, {turns} agent turn(s)), "
        "verified locally and driven to green checks before merge.\n"
    )


def _step_commit_message(step: Step) -> str:
    return (
        f"{_default_title(step.persona, step.task)}\n\n"
        f"Workstream step {step.id} (persona: {step.persona}), dispatched by ai-orchestrator."
    )


def _incomplete_commit_message(step: Step) -> str:
    return (
        f"wip: {_default_title(step.persona, step.task)} (incomplete step)\n\n"
        f"Partial work from step {step.id} (persona: {step.persona}), preserved by "
        "ai-orchestrator after the dispatch did not complete."
    )


def _workstream_branch_name(steps: list[Step]) -> str:
    lead = steps[0]
    key = "\x00".join(f"{s.persona}:{s.task}" for s in steps)
    return f"ai-orchestrator/{lead.persona}/{_short_hash(key)}"


def _workstream_body(steps: list[Step], results: list[StepResult]) -> str:
    if len(steps) == 1:
        report = results[0].report if results else None
        return _default_body(steps[0].persona, steps[0].task, report)
    lines = ["## What", "This PR bundles an ordered workstream of subtasks:", ""]
    for s in steps:
        first = s.task.strip().splitlines()[0] if s.task.strip() else s.id
        lines.append(f"- **{s.id}** (`{s.persona}`): {first}")
    lines += [
        "",
        "## Why",
        "Dispatched by ai-orchestrator as one PR: each step ran in the same branch, "
        "committing in turn, and the change was verified locally before merge.",
        "",
    ]
    return "\n".join(lines)


def _run_steps(
    steps: list[Step],
    *,
    worktree: Path,
    branch: str,
    dispatch_fn: DispatchFn,
    oneharness_mode: str | None,
    base_path: str | Path,
    persona_dir: str | Path,
) -> tuple[bool, list[StepResult], str]:
    """Run a step sub-DAG in the shared worktree, committing per step.

    Execution is **serialized in topological order** (concurrency 1): the steps
    share one working tree, so running two dispatches into it at once would corrupt
    it — deps express ordering, and each step sees its predecessors' commits. A
    step that does not complete fails the workstream and skips its dependents.
    Returns ``(all_done, per-step results, detail)``.
    """
    by_id = {s.id: s for s in steps}
    deps = {s.id: s.deps for s in steps}
    reports: dict[str, Report] = {}

    def run_step(sid: str) -> NodeRun:
        step = by_id[sid]
        report = dispatch_fn(
            step.persona,
            step.task,
            project_dir=str(worktree),
            oneharness_mode=oneharness_mode,
            base_path=base_path,
            persona_dir=persona_dir,
            session=f"{branch}:{sid}",
            max_turns=step.max_turns,
            done_when=step.done_when,
        )
        reports[sid] = report
        if not report.completed:
            if gitops.is_dirty(worktree):
                gitops.add_all(worktree)
                gitops.commit(worktree, _incomplete_commit_message(step))
                return NodeRun(
                    "failed",
                    f"step {sid!r} hit the turn cap; partial work was committed to branch "
                    f"{branch!r}",
                    report,
                )
            return NodeRun("failed", f"step {sid!r} hit the turn cap", report)
        if gitops.is_dirty(worktree):
            gitops.add_all(worktree)
            gitops.commit(worktree, _step_commit_message(step))
        return NodeRun("done", None, report)

    runs, _order = schedule_dag(list(by_id), deps, run_step, concurrency=1)
    status_map = {"done": "done", "failed": "not-completed", "skipped": "skipped"}
    results = [
        StepResult(
            id=sid,
            persona=by_id[sid].persona,
            status=status_map.get(runs[sid].status, "skipped") if sid in runs else "skipped",
            report=reports.get(sid),
        )
        for sid in by_id
    ]
    all_done = all(r.status == "done" for r in results)
    detail = ""
    if not all_done:
        bad = next(r for r in results if r.status != "done")
        detail = runs[bad.id].error or f"step {bad.id!r} {bad.status}"
    return all_done, results, detail


def _select_merge_strategy(
    ref: RepoRef,
    merge: MergeStrategy | None,
    github: GitHubBackend | None,
    workflow: Workflow | None = None,
) -> MergeStrategy:
    if merge is not None:
        return merge
    if workflow == "local" or (workflow is None and ref.local):
        return LocalMergeStrategy()
    return GitHubMergeStrategy(github or CliGitHubBackend())


def run_repo_task(
    repo: str,
    task: str | None = None,
    persona: str | None = None,
    *,
    workspace: Workspace,
    steps: list[Step] | None = None,
    merge: MergeStrategy | None = None,
    workflow: Workflow | None = None,
    github: GitHubBackend | None = None,
    base_branch: str | None = None,
    branch: str | None = None,
    title: str | None = None,
    body: str | None = None,
    url: str | None = None,
    verify_cmd: list[str] | None = None,
    skip_verify: bool = False,
    merge_policy: str = "auto",
    merge_method: str = "squash",
    oneharness_mode: str | None = "bypass",
    dispatch_fn: DispatchFn = dispatch,
    base_path: str | Path = BASE_CONFIG,
    persona_dir: str | Path = PERSONA_DIR,
    max_turns: int | None = None,
    done_when: str | None = None,
    gate_timeout: float | None = None,
    poll_interval: float = 15.0,
    timeout: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    cleanup: bool = True,
) -> LifecycleResult:
    """Take one subtask from a fresh branch to a merged change on ``repo``.

    The merge step is a `MergeStrategy`: for a GitHub repo, open a PR and let
    GitHub auto-merge on green required checks; for a **local** repo (a path), a
    direct merge into the base branch after the checks pass. It is auto-selected
    from the repo (override with ``merge``); ``github`` supplies the backend for
    the GitHub path.

    ``oneharness_mode`` defaults to ``"bypass"`` (no approvals, no inner sandbox):
    the container is the sandbox, so this is the "complete tasks without approvals"
    mode. The agent runs in an isolated worktree; the change is verified with the
    repo's own gate before it is pushed, and never merges off a non-required check.

    Pass ``steps`` (a sub-DAG of `Step`s) to run **several onejudge on one PR**: they
    share the branch/worktree, run in dependency order committing in turn, and the
    result is verified and merged **once**. A single ``(persona, task)`` is the
    one-step case.
    """
    effective_steps = steps or (
        [Step("main", persona, task, max_turns=max_turns, done_when=done_when)]
        if persona and task
        else []
    )
    if not effective_steps:
        raise ConfigError("run_repo_task needs either (persona, task) or a non-empty steps list")
    lead = effective_steps[0]

    ref = normalize_repo(repo)
    result = LifecycleResult(
        repo=ref.slug,
        task=lead.task,
        persona=lead.persona,
        base_branch=base_branch or "",
        branch=branch or _workstream_branch_name(effective_steps),
        outcome="error",
    )
    worktree: Path | None = None
    try:
        clone = workspace.ensure_clone(ref, url=url)
        strategy = _select_merge_strategy(ref, merge, github, workflow or workspace.workflow(ref))
        base = base_branch or gitops.default_branch(clone)
        result.base_branch = base
        branch = result.branch
        worktree = workspace.worktree(ref, branch, base=f"origin/{base}")

        all_done, step_results, step_detail = _run_steps(
            effective_steps,
            worktree=worktree,
            branch=branch,
            dispatch_fn=dispatch_fn,
            oneharness_mode=oneharness_mode,
            base_path=base_path,
            persona_dir=persona_dir,
        )
        result.steps = step_results
        result.report = next(
            (r.report for r in reversed(step_results) if r.status == "done" and r.report), None
        )
        if not all_done:
            result.outcome = "not-completed"
            result.detail = f"workstream did not complete: {step_detail}"
            return result

        if not skip_verify:
            cmd = verify_cmd or detect_gate(worktree)
            if cmd is not None:
                verify = run_gate(worktree, cmd, timeout=gate_timeout)
                result.verify = verify
                if not verify.ok:
                    result.outcome = "gate-failed"
                    result.detail = f"local gate failed: {' '.join(cmd)}"
                    return result
            else:
                result.detail = "no local gate detected; relying on required CI checks"

        # Each step commits its own work in _run_steps, so the worktree is clean
        # here; if no step produced a commit, there is nothing to open a PR for.
        if not gitops.has_commits_ahead(worktree, f"origin/{base}"):
            result.outcome = "no-changes"
            result.detail = "agent completed but produced no commits to open a PR"
            return result

        gitops.push(worktree, branch)
        ctx = MergeContext(
            repo_slug=ref.slug,
            clone_dir=clone,
            base=base,
            branch=branch,
            title=title or _default_title(lead.persona, lead.task),
            body=body or _workstream_body(effective_steps, step_results),
            method=merge_method,
            policy=merge_policy,
            poll_interval=poll_interval,
            timeout=timeout,
            sleep=sleep,
            clock=clock,
        )
        merge_outcome = strategy.publish_and_merge(ctx)
        if merge_outcome.outcome == "merged":
            workspace.fast_forward(ref, base)
        result.pr = merge_outcome.pr
        result.outcome = merge_outcome.outcome
        result.detail = merge_outcome.detail
        return result
    except (GitError, GitHubError, ConfigError, RegistryError) as exc:
        result.outcome = "error"
        result.detail = str(exc)
        return result
    finally:
        if cleanup and worktree is not None:
            workspace.remove_worktree(ref, worktree)


# --- multi-PR plans --------------------------------------------------------


@dataclass
class RepoPlanNode:
    """One PR in a repo-plan. Either a single (persona, task) or a `steps` workstream."""

    id: str
    repo: str
    persona: str | None = None
    task: str | None = None
    deps: list[str] = field(default_factory=list)
    base_branch: str | None = None
    branch: str | None = None
    title: str | None = None
    verify_cmd: list[str] | None = None
    skip_verify: bool = False
    merge_policy: str | None = None
    workflow: Workflow | None = None
    max_turns: int | None = None
    done_when: str | None = None
    steps: list[Step] | None = None


@dataclass
class RepoPlan:
    tasks: list[RepoPlanNode]
    concurrency: int = 4


@dataclass
class RepoTaskResult:
    id: str
    status: str  # "done" | "failed" | "skipped"
    result: LifecycleResult | None = None
    error: str | None = None


@dataclass
class RepoPlanResult:
    results: dict[str, RepoTaskResult]
    started_order: list[str]

    @property
    def ok(self) -> bool:
        return all(r.status == "done" for r in self.results.values())

    def summary(self) -> str:
        lines = [f"repo-plan: {'all PRs merged' if self.ok else 'some subtasks did not complete'}"]
        for nid, r in self.results.items():
            outcome = r.result.outcome if r.result else r.status
            detail = f" ({r.error})" if r.error else ""
            lines.append(f"  {nid}: {r.status} [{outcome}]{detail}")
        return "\n".join(lines)


def load_repo_plan(path: str | Path) -> RepoPlan:
    """Load and validate a repo-plan file (JSON or YAML); raise PlanError on any issue."""
    from .plan import PlanError

    try:
        data = load_yaml(path)
    except ConfigError as exc:
        raise PlanError(str(exc)) from exc
    return parse_repo_plan(data)


def parse_repo_plan(data: dict[str, Any]) -> RepoPlan:
    """Validate an in-memory repo-plan mapping into a `RepoPlan` (raise PlanError)."""
    from .plan import PlanError, _topological_order

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanError("repo-plan must have a non-empty 'tasks' list")
    concurrency = data.get("concurrency", 4)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise PlanError("'concurrency' must be a positive integer")

    nodes: dict[str, RepoPlanNode] = {}
    for i, t in enumerate(raw_tasks):
        if not isinstance(t, dict):
            raise PlanError(f"task #{i} must be a mapping")
        nid = t.get("id")
        if not isinstance(nid, str) or not nid:
            raise PlanError(f"task #{i} needs a non-empty string 'id'")
        if nid in nodes:
            raise PlanError(f"duplicate task id: {nid!r}")
        if not isinstance(t.get("repo"), str) or not str(t.get("repo")).strip():
            raise PlanError(f"task {nid!r} needs a non-empty 'repo'")
        raw_steps = t.get("steps")
        if raw_steps is not None:
            node_steps = _parse_steps(nid, raw_steps)
            persona = task = None
        else:
            for key in ("persona", "task"):
                if not isinstance(t.get(key), str) or not str(t.get(key)).strip():
                    raise PlanError(f"task {nid!r} needs a non-empty {key!r} (or a 'steps' list)")
            node_steps = None
            persona, task = t["persona"], t["task"]
        deps = t.get("deps", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise PlanError(f"task {nid!r} 'deps' must be a list of ids")
        policy = t.get("merge_policy")
        if policy is not None and policy not in MERGE_POLICIES:
            raise PlanError(f"task {nid!r} 'merge_policy' must be one of {MERGE_POLICIES}")
        raw_workflow = t.get("workflow")
        if raw_workflow is not None and raw_workflow not in ("local", "remote"):
            raise PlanError(f"task {nid!r} 'workflow' must be 'local' or 'remote'")
        workflow = cast(Workflow | None, raw_workflow)
        nodes[nid] = RepoPlanNode(
            id=nid,
            repo=t["repo"],
            persona=persona,
            task=task,
            deps=list(deps),
            base_branch=t.get("base_branch"),
            branch=t.get("branch"),
            title=t.get("title"),
            verify_cmd=t.get("verify_cmd"),
            skip_verify=bool(t.get("skip_verify", False)),
            merge_policy=policy,
            workflow=workflow,
            max_turns=t.get("max_turns"),
            done_when=t.get("done_when"),
            steps=node_steps,
        )

    for nid, node in nodes.items():
        for dep in node.deps:
            if dep not in nodes:
                raise PlanError(f"task {nid!r} depends on unknown task {dep!r}")
            if dep == nid:
                raise PlanError(f"task {nid!r} depends on itself")
    _topological_order({nid: _to_plan_node(n) for nid, n in nodes.items()})  # cycle check

    return RepoPlan(tasks=list(nodes.values()), concurrency=concurrency)


def _parse_steps(nid: str, raw_steps: object) -> list[Step]:
    """Validate a node's `steps` sub-DAG and return `Step`s (raise PlanError)."""
    from .plan import PlanError, PlanNode, _topological_order

    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError(f"task {nid!r} 'steps' must be a non-empty list")
    steps: dict[str, Step] = {}
    for j, s in enumerate(raw_steps):
        if not isinstance(s, dict):
            raise PlanError(f"task {nid!r} step #{j} must be a mapping")
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            raise PlanError(f"task {nid!r} step #{j} needs a non-empty string 'id'")
        if sid in steps:
            raise PlanError(f"task {nid!r} has a duplicate step id: {sid!r}")
        for key in ("persona", "task"):
            if not isinstance(s.get(key), str) or not str(s.get(key)).strip():
                raise PlanError(f"task {nid!r} step {sid!r} needs a non-empty {key!r}")
        sdeps = s.get("deps", [])
        if not isinstance(sdeps, list) or not all(isinstance(d, str) for d in sdeps):
            raise PlanError(f"task {nid!r} step {sid!r} 'deps' must be a list of ids")
        steps[sid] = Step(
            id=sid,
            persona=s["persona"],
            task=s["task"],
            deps=list(sdeps),
            max_turns=s.get("max_turns"),
            done_when=s.get("done_when"),
        )
    for sid, step in steps.items():
        for dep in step.deps:
            if dep not in steps:
                raise PlanError(f"task {nid!r} step {sid!r} depends on unknown step {dep!r}")
            if dep == sid:
                raise PlanError(f"task {nid!r} step {sid!r} depends on itself")
    _topological_order(  # raises PlanError on a step cycle
        {sid: PlanNode(id=sid, persona="_", task="_", deps=st.deps) for sid, st in steps.items()}
    )
    return list(steps.values())


def _to_plan_node(node: RepoPlanNode) -> Any:
    from .plan import PlanNode

    return PlanNode(id=node.id, persona=node.persona or "_", task=node.task or "_", deps=node.deps)


def run_repo_plan(
    plan: RepoPlan,
    runner: Callable[[RepoPlanNode], LifecycleResult],
    *,
    concurrency: int | None = None,
) -> RepoPlanResult:
    """Schedule the DAG, driving each node through its repo lifecycle via `runner`.

    A node is ``done`` when its `LifecycleResult.ok`, else ``failed``; a failed or
    skipped dependency skips the node (its PR is never opened against a broken
    precondition). `runner` is injected so the scheduler is unit-tested without
    git/GitHub while the e2e drives the real lifecycle.
    """
    conc = concurrency if concurrency is not None else plan.concurrency
    nodes = {n.id: n for n in plan.tasks}
    deps = {nid: nodes[nid].deps for nid in nodes}

    def run_one(nid: str) -> NodeRun:
        result = runner(nodes[nid])
        if result.ok:
            return NodeRun("done", None, result)
        return NodeRun("failed", result.detail or result.outcome, result)

    runs, started_order = schedule_dag(list(nodes), deps, run_one, concurrency=conc)
    results = {
        nid: RepoTaskResult(
            nid,
            runs[nid].status,
            result=runs[nid].payload if isinstance(runs[nid].payload, LifecycleResult) else None,
            error=runs[nid].error,
        )
        for nid in nodes
        if nid in runs
    }
    return RepoPlanResult(results=results, started_order=started_order)


def make_repo_runner(
    *,
    workspace: Workspace,
    github: GitHubBackend | None = None,
    base_path: str | Path,
    persona_dir: str | Path,
    merge_policy: str,
    merge_method: str,
    oneharness_mode: str | None,
    skip_verify: bool,
    poll_interval: float,
    timeout: float,
) -> Callable[[RepoPlanNode], LifecycleResult]:
    """Build the production runner that drives each node through `run_repo_task`."""

    def runner(node: RepoPlanNode) -> LifecycleResult:
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            steps=node.steps,
            github=github,
            workflow=node.workflow,
            base_branch=node.base_branch,
            branch=node.branch,
            title=node.title,
            verify_cmd=node.verify_cmd,
            skip_verify=node.skip_verify or skip_verify,
            merge_policy=node.merge_policy or merge_policy,
            merge_method=merge_method,
            oneharness_mode=oneharness_mode,
            base_path=base_path,
            persona_dir=persona_dir,
            max_turns=node.max_turns,
            done_when=node.done_when,
            poll_interval=poll_interval,
            timeout=timeout,
        )

    return runner


# --- CLI -------------------------------------------------------------------


def _read_task(value: str | None) -> str:
    if value is None or value == "-":
        return sys.stdin.read()
    return value


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base", type=Path, default=BASE_CONFIG, dest="base_config")
    parser.add_argument("--persona-dir", type=Path, default=PERSONA_DIR)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.home() / ".ai-orchestrator" / "worktrees",
        help="root directory for isolated task worktrees",
    )
    parser.add_argument("--merge-policy", choices=MERGE_POLICIES, default="auto")
    parser.add_argument("--merge-method", choices=MERGE_METHODS, default="squash")
    parser.add_argument(
        "--oneharness-mode",
        default="bypass",
        choices=["read-only", "plan", "default", "edit", "auto", "bypass"],
        help="approval/sandbox mode for the harness (default: bypass — the "
        "no-approval mode; the container is the sandbox)",
    )
    parser.add_argument("--skip-verify", action="store_true", help="skip the local gate")
    parser.add_argument("--poll-interval", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--format", choices=["human", "json"], default="human")
    parser.add_argument("-o", "--output", type=Path, default=None)


def _emit(rendered: str, output: Path | None) -> None:
    if output:
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def _result_payload(result: LifecycleResult) -> dict[str, Any]:
    return {
        "repo": result.repo,
        "branch": result.branch,
        "base_branch": result.base_branch,
        "outcome": result.outcome,
        "ok": result.ok,
        "pr": result.pr.url if result.pr else None,
        "detail": result.detail,
    }


def main_task(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive one subtask through a repo's lifecycle (clone→gate→PR/merge)."
    )
    parser.add_argument("repo", help="repo (name, owner/name, GitHub URL, or LOCAL path)")
    parser.add_argument("persona", help="persona name (see personas/)")
    parser.add_argument("task", nargs="?", default=None, help="the task ('-'/omitted reads stdin)")
    parser.add_argument("--base-branch", default=None, help="branch to target (default: repo HEAD)")
    parser.add_argument("--branch", default=None, help="feature branch name (default: derived)")
    parser.add_argument("--title", default=None)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--done-when", default=None)
    _add_common_args(parser)
    args = parser.parse_args(argv)

    result = run_repo_task(
        args.repo,
        _read_task(args.task),
        args.persona,
        workspace=Workspace(args.workspace),
        base_branch=args.base_branch,
        branch=args.branch,
        title=args.title,
        verify_cmd=None,
        skip_verify=args.skip_verify,
        merge_policy=args.merge_policy,
        merge_method=args.merge_method,
        oneharness_mode=args.oneharness_mode,
        base_path=args.base_config,
        persona_dir=args.persona_dir,
        max_turns=args.max_turns,
        done_when=args.done_when,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )
    rendered = (
        json.dumps(_result_payload(result), indent=2) if args.format == "json" else result.summary()
    )
    _emit(rendered, args.output)
    return 0 if result.ok else 1


def main_plan(argv: list[str] | None = None) -> int:
    from .plan import PlanError

    parser = argparse.ArgumentParser(
        description="Run a repo-plan: many isolated PRs across repos, coordinated by a DAG."
    )
    parser.add_argument("plan", type=Path, help="repo-plan file (JSON or YAML)")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--run", default=None, help="record into this validated run id")
    parser.add_argument("--no-record", action="store_true", help="do not record this round")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"), help="run ledger root")
    _add_common_args(parser)
    args = parser.parse_args(argv)

    try:
        plan_mapping = load_yaml(args.plan)
        plan = parse_repo_plan(plan_mapping)
        run_dir = (
            resolve_run_dir(args.runs_dir, plan_mapping, args.plan, args.run)
            if not args.no_record
            else None
        )
    except (ConfigError, PlanError) as exc:
        print(f"repo-plan: {exc}", file=sys.stderr)
        return 2

    runner = make_repo_runner(
        workspace=Workspace(args.workspace),
        base_path=args.base_config,
        persona_dir=args.persona_dir,
        merge_policy=args.merge_policy,
        merge_method=args.merge_method,
        oneharness_mode=args.oneharness_mode,
        skip_verify=args.skip_verify,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )
    result = run_repo_plan(plan, runner, concurrency=args.concurrency)

    payload: RepoPlanPayload = {
        "ok": result.ok,
        "started_order": result.started_order,
        "results": {
            nid: cast(
                RepoPlanResultItem,
                {
                    "status": r.status,
                    **({} if r.result is None else _result_payload(r.result)),
                    "error": r.error,
                },
            )
            for nid, r in result.results.items()
        },
    }
    rendered = json.dumps(payload, indent=2) if args.format == "json" else result.summary()
    _emit(rendered, args.output)
    if run_dir is not None:
        try:
            number, round_dir = prepare_round(run_dir, plan_mapping)
            write_result(round_dir, payload)
        except ConfigError as exc:
            print(f"repo-plan: could not record run: {exc}", file=sys.stderr)
            return 2
        _print_continuation(run_dir.name, number, round_dir, payload, args.runs_dir)
    return 0 if result.ok else 1


def _print_continuation(
    run_id: str,
    number: int,
    round_dir: Path,
    payload: RepoPlanPayload,
    runs_dir: Path,
) -> None:
    """Print the repo-plan continuation guidance without contaminating stdout."""
    print(
        f"Round {number:02d} recorded -> {round_dir}/  ({status_summary(payload)})",
        file=sys.stderr,
    )
    counts = status_counts(payload)
    if counts and counts["done"] == sum(counts.values()):
        print("All nodes are done; there is nothing to iterate.", file=sys.stderr)
        return
    print("Iterate: write an edits.json (retry/split/add/drop), then", file=sys.stderr)
    suffix = "" if runs_dir == Path("runs") else f" --runs-dir {runs_dir}"
    print(f"  just next-round {run_id} [edits.json]{suffix}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main_task())
