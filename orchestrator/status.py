"""Unified progress view over history, git worktrees, and the run ledger."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import gitops, history, runs
from .channel import ChannelError, due_indicator, planner_wait_indicator
from .config import ConfigError
from .registry import Registry, RegistryError
from .workspace import IdentityKey, RepositoryType, Workflow

# A history record describes one completed harness invocation, not the whole
# onejudge task. oneharness' 1.0 history writes a record only when a turn finishes,
# always with a terminal status (`ok`/`nonzero`/`spawn-error`/`skipped`/`planned`),
# so a live status can never appear here. The signal that a workstream is still in
# flight is therefore its worktree: the orchestrator removes it once the branch
# integrates, so a session whose project is still a checked-out worktree is running.
# Everything else is a recent, integrated task available via N/--all.


@dataclass(frozen=True)
class GitState:
    branch: str
    base: str
    commits: list[gitops.Commit]
    checked_out: bool


@dataclass(frozen=True)
class LedgerState:
    run_id: runs.RunId
    round: int
    summary: str


@dataclass(frozen=True)
class TaskStatus:
    session_id: history.SessionId
    project: str
    task: str
    harness: str
    model: str
    status: str
    running: bool
    turns: int
    elapsed_ms: int
    output: str
    commands: list[str]
    execution_checkout: str
    publication_checkout: str | None
    publication_identity: IdentityKey | None
    repository_type: RepositoryType | None
    publication_workflow: Workflow | None
    branch: str | None
    base: str | None
    commits: list[gitops.Commit]
    ledger: LedgerState | None


def is_running(git: GitState | None) -> bool:
    """True while a session's branch is still a checked-out worktree.

    Under oneharness' 1.0 history every recorded turn is already terminal, so a
    live workstream can only be recognised by its still-present worktree — the
    orchestrator removes it once the branch integrates.
    """
    return bool(git and git.checked_out)


def _git_state(project: Path) -> GitState | None:
    if not project.is_dir():
        return None
    try:
        branch = gitops.current_branch(project)
        checked_out = gitops.worktrees(project).get(branch) == project.resolve()
        default = gitops.default_branch(project)
        remote_base = f"origin/{default}"
        base = remote_base if _ref_exists(project, remote_base) else default
        commits = gitops.log_delta(project, base, branch)
    except gitops.GitError:
        return None
    return GitState(branch, base, commits, checked_out)


def _ref_exists(project: Path, ref: str) -> bool:
    try:
        gitops.log_delta(project, ref, ref)
    except gitops.GitError:
        return False
    return True


def _ledger_for_branch(runs_dir: Path, branch: str | None) -> LedgerState | None:
    if branch is None or not runs_dir.is_dir():
        return None
    matches: list[LedgerState] = []
    for run_dir in runs_dir.iterdir():
        latest = runs.latest_round(run_dir) if run_dir.is_dir() else None
        if latest is None or not (latest[1] / "result.json").is_file():
            continue
        try:
            payload = runs._as_result_payload(runs.load_mapping(latest[1] / "result.json"))
        except (ConfigError, OSError):
            continue
        if any(item.get("branch") == branch for item in payload["results"].values()):
            matches.append(
                LedgerState(runs.RunId(run_dir.name), latest[0], runs.status_summary(payload))
            )
    return max(matches, default=None, key=lambda item: (item.round, item.run_id))


def collect(*, runs_dir: Path, oneharness_bin: str = "oneharness") -> list[TaskStatus]:
    """Join all validated worker history sessions to their git and ledger state."""
    result: list[TaskStatus] = []
    registry = Registry()
    for session in history.worker_sessions(oneharness_bin=oneharness_bin):
        records = history.session_records(session)
        summary = history.digest(records, session.session_id)
        latest = records[-1] if records else {}
        git = _git_state(session.project)
        branch = git.branch if git else None
        publication = registry.identity_for_checkout(session.project)
        result.append(
            TaskStatus(
                session_id=session.session_id,
                project=str(session.project),
                task=session.name,
                harness=str(latest.get("harness", "?")),
                model=str(latest.get("model", "?")),
                status=summary.status,
                running=is_running(git),
                turns=summary.turns,
                elapsed_ms=sum(
                    record.get("duration_ms", 0)
                    for record in records
                    if isinstance(record.get("duration_ms"), int)
                ),
                output=summary.text,
                commands=summary.commands,
                execution_checkout=str(session.project),
                publication_checkout=(
                    str(publication.publication_checkout) if publication else None
                ),
                publication_identity=publication.identity if publication else None,
                repository_type=publication.repo_type if publication else None,
                publication_workflow=publication.workflow if publication else None,
                branch=branch,
                base=git.base if git else None,
                commits=git.commits if git else [],
                ledger=_ledger_for_branch(runs_dir, branch),
            )
        )
    return result


def _human(tasks: list[TaskStatus]) -> str:
    if not tasks:
        return "No running tasks. Pass N or --all to include recent finished tasks."
    lines: list[str] = []
    for task in tasks:
        state = "running" if task.running else "recent"
        elapsed = task.elapsed_ms / 1000
        lines.append(
            f"{task.session_id[-14:]}  {Path(task.project).name or '?'}  {task.task}  "
            f"{task.harness}/{task.model}  {task.status} "
            f"({state}; {task.turns} turns, {elapsed:.1f}s)"
        )
        commands = "; ".join(f"$ {command}" for command in task.commands) or "(none)"
        lines.append(f"  Output: {task.output or '(none)'}")
        lines.append(f"  Commands: {commands}")
        lines.append(f"  Execution checkout: {task.execution_checkout}")
        if task.publication_identity:
            lines.append(
                f"  Publication: {task.publication_identity} via "
                f"type={task.repository_type} workflow={task.publication_workflow} "
                f"({task.publication_checkout})"
            )
        else:
            lines.append("  Publication: unknown identity; conservative workflow=remote")
        if task.branch:
            lines.append(
                f"  Branch: {task.branch} — {len(task.commits)} commit(s) over {task.base}"
            )
            lines.extend(f"    {commit.sha} {commit.subject}" for commit in task.commits[:5])
        else:
            lines.append("  Branch: unavailable (worktree/branch is gone)")
        if task.ledger:
            lines.append(
                f"  Round: {task.ledger.run_id} round-{task.ledger.round:02d} — "
                f"{task.ledger.summary}"
            )
            lines.append(
                f"  Results: just results {task.ledger.run_id}; per-node detail is listed there"
            )
        else:
            lines.append("  Round: none")
    return "\n".join(lines)


def _json_value(task: TaskStatus) -> dict[str, Any]:
    value = asdict(task)
    value["commits"] = [{"sha": commit.sha, "subject": commit.subject} for commit in task.commits]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show running and recent dispatched tasks.")
    parser.add_argument("limit", nargs="?", type=int, metavar="N")
    parser.add_argument("--all", action="store_true", help="include recent finished tasks")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("N must be a positive integer")
    try:
        tasks = collect(runs_dir=args.runs_dir)
    except (history.HistoryError, RegistryError) as exc:
        print(f"status: {exc}", file=sys.stderr)
        return 2
    include_recent = args.all or args.limit is not None
    selected = tasks if include_recent else [task for task in tasks if task.running]
    selected = selected[: args.limit or 15]
    if args.format == "json":
        print(json.dumps([_json_value(task) for task in selected]))
    else:
        indicators: list[str] = []
        if args.runs_dir.is_dir():
            for run_dir in sorted(path for path in args.runs_dir.iterdir() if path.is_dir()):
                # Reported before the channel indicators and independently of them: a
                # run that lost its round or its orchestrator leaves its last planner
                # surface in place, so it would otherwise still read as "waiting on me".
                dead = runs.abandoned_round_indicator(run_dir) or runs.abandoned_launch_indicator(
                    run_dir
                )
                if dead is not None:
                    indicators.append(f"{run_dir.name}: {dead}")
                try:
                    waiting = planner_wait_indicator(run_dir / "channel")
                    indicator = due_indicator(run_dir / "channel")
                except (ChannelError, ConfigError, OSError):
                    continue
                if waiting is not None:
                    indicators.append(f"{run_dir.name}: {waiting}")
                if indicator is not None:
                    indicators.append(f"{run_dir.name}: {indicator}")
        print("\n".join([*indicators, _human(selected)]))
    return 0
