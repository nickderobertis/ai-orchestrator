"""Unified progress view over history, git worktrees, and the run ledger."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import gitops, history, runs
from .channel import (
    ChannelError,
    due_indicator,
    pending_surface_indicator,
    planner_wait_indicator,
)
from .config import ConfigError
from .goals import concurrent_indicator
from .liveness import PARKED_AFTER_SECONDS, parked_indicator
from .monitor import RUN_LABEL
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


def collect(
    *,
    runs_dir: Path,
    oneharness_bin: str = "oneharness",
    run_id: runs.RunId | None = None,
) -> list[TaskStatus]:
    """Join all validated worker history sessions to their git and ledger state.

    ``run_id`` narrows the join to the sessions that run's own scopes labelled,
    which is the same `run_id` label `just monitor` filters history on — so both
    planner views answer "what is this run doing" from one selection rule.
    """
    result: list[TaskStatus] = []
    registry = Registry()
    for session in history.worker_sessions(oneharness_bin=oneharness_bin):
        if run_id is not None and session.labels.get(RUN_LABEL) != run_id:
            continue
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


def _human(tasks: list[TaskStatus], *, run_id: runs.RunId | None = None) -> str:
    if not tasks:
        if run_id is not None:
            return f"No dispatched tasks recorded for run {run_id}."
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


def _positional(value: str | None) -> tuple[int | None, str | None]:
    """Split this view's one positional into its count and its run-id readings.

    The count came first and stays exactly as it was, so a plain integer is never
    read as a run id. Everything else is a run id, which is what `launch.json`
    advertises and what `just monitor` already accepts — the two planner views
    that could not be pointed at a run were the outlier, not this argument.

    A run id may itself be all digits (`_RUN_ID` admits one), so this split is a
    genuine ambiguity rather than a parsing convenience. It resolves toward the
    older meaning: reading `just status 5` as a run would silently change what a
    documented invocation shows, while a numeric run id is still reachable by its
    unambiguous `--runs-dir` path plus `just monitor`/`just results`.
    """
    if value is None:
        return None, None
    try:
        return int(value), None
    except ValueError:
        return None, value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show running and recent dispatched tasks.")
    parser.add_argument(
        "target",
        nargs="?",
        metavar="N|RUN_ID",
        help="a count of recent finished tasks to include, or the run id "
        "`launch.json` advertises (a plan name resolves when it names one active run)",
    )
    parser.add_argument("--all", action="store_true", help="include recent finished tasks")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument(
        "--parked-after",
        type=float,
        default=PARKED_AFTER_SECONDS,
        metavar="SECONDS",
        help="report a launch with no child process, planner surface, or ledger write for "
        f"this long as parked (default: {PARKED_AFTER_SECONDS:g})",
    )
    args = parser.parse_args(argv)
    limit, requested_run = _positional(args.target)
    if limit is not None and limit <= 0:
        parser.error("N must be a positive integer")
    if not math.isfinite(args.parked_after) or args.parked_after <= 0:
        parser.error("--parked-after must be a positive, finite number of seconds")
    run_id: runs.RunId | None = None
    if requested_run is not None:
        try:
            run_id = runs.resolve_supervision_run(args.runs_dir, requested_run)
        except ConfigError as exc:
            print(f"status: {exc}", file=sys.stderr)
            return 2
    try:
        tasks = collect(runs_dir=args.runs_dir, run_id=run_id)
    except (history.HistoryError, RegistryError) as exc:
        print(f"status: {exc}", file=sys.stderr)
        return 2
    # Naming a run asks for that run's picture, so its finished tasks are part of
    # the answer; the unscoped view keeps its running-only default.
    include_recent = args.all or limit is not None or run_id is not None
    selected = tasks if include_recent else [task for task in tasks if task.running]
    selected = selected[:limit] if limit is not None else selected[:15]
    if args.format == "json":
        print(json.dumps([_json_value(task) for task in selected]))
    else:
        indicators: list[str] = []
        if args.runs_dir.is_dir():
            for run_dir in sorted(
                path
                for path in args.runs_dir.iterdir()
                if path.is_dir() and (run_id is None or path.name == run_id)
            ):
                # Reported before the channel indicators and independently of them: a
                # run that lost its round or its orchestrator leaves its last planner
                # surface in place, so it would otherwise still read as "waiting on me".
                dead = runs.abandoned_round_indicator(run_dir) or runs.abandoned_launch_indicator(
                    run_dir
                )
                if dead is not None:
                    indicators.append(f"{run_dir.name}: {dead}")
                # Reported for the same reason, one layer up: a launch that keeps its
                # pid while nothing progresses is not running work, and its stale
                # planner surface would otherwise read as live supervision.
                if (
                    parked := parked_indicator(run_dir, parked_after=args.parked_after)
                ) is not None:
                    indicators.append(f"{run_dir.name}: {parked}")
                # A second live orchestrator on a shared identity is the one piece of
                # machine state this view could not previously report: it belongs to
                # no run dir here, and its effects reach this one as a dirty
                # publication checkout or a lost push race.
                if (shared := concurrent_indicator(run_dir, args.parked_after)) is not None:
                    indicators.append(f"{run_dir.name}: {shared}")
                # Reported here as well as in `just runs`, because the two views are
                # read interchangeably: a planner who checks only this one must not
                # have to know that the other is where unread updates are named.
                if (unread := pending_surface_indicator(run_dir)) is not None:
                    indicators.append(f"{run_dir.name}: {unread}")
                try:
                    waiting = planner_wait_indicator(run_dir / "channel")
                    indicator = due_indicator(run_dir / "channel")
                except (ChannelError, ConfigError, OSError):
                    continue
                if waiting is not None:
                    indicators.append(f"{run_dir.name}: {waiting}")
                if indicator is not None:
                    indicators.append(f"{run_dir.name}: {indicator}")
        print("\n".join([*indicators, _human(selected, run_id=run_id)]))
    return 0
