"""Unified progress view over history, git worktrees, and the run ledger."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

from . import gitops, history, runs
from .activity import NodeActivity, live_activity
from .channel import (
    ChannelError,
    due_indicator,
    pending_surface_indicator,
    planner_wait_indicator,
)
from .config import ConfigError
from .goals import concurrent_indicator
from .journal import JOURNAL_NAME, EventKind, read_events
from .liveness import PARKED_AFTER_SECONDS, parked_indicator
from .monitor import RUN_LABEL
from .projection import TERMINAL_NODE_STATES, NodeState
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
    #: The terminal status the run journal recorded for the graph node this session
    #: was dispatched for, or ``None`` when the journal has recorded none — either
    #: because the node is genuinely still in flight or because the dispatch carries
    #: no graph labels at all.
    node_state: NodeState | None
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


def is_running(git: GitState | None, node_state: NodeState | None = None) -> bool:
    """True while a session's branch is still a checked-out worktree and unsettled.

    Under oneharness' 1.0 history every recorded turn is already terminal, so a
    live workstream can only be recognised by its still-present worktree — the
    orchestrator removes it once the branch integrates.

    The worktree alone is not enough, because it is *evidence* and the journal is
    the record. A node whose dispatch failed can keep its worktree — a direct agent
    never had one of its own to remove — and reporting that as running is the
    stale picture a supervisor then acts on. So a node the journal has already
    recorded as settled is never running here, whatever its checkout still looks
    like; the journal wins over the filesystem in every read-only view.
    """
    return bool(git and git.checked_out) and node_state is None


#: Journal kinds that settle a *dispatched* node, and the status each proves on its
#: own. `node-settled` carries the status it settled with, so it has none of its own.
#: Node-level `human-waiting` is deliberately absent: the executor emits it only for
#: a `kind: human` node, which runs no agent, so no history session this view reports
#: can ever carry that node's label. A lifecycle step awaiting a human is step-scoped
#: and excluded below, because its node is still working.
_SETTLING_KINDS: dict[EventKind, str | None] = {
    "node-failed": "failed",
    "node-settled": None,
}


def _settled_nodes(runs_dir: Path, run_id: str) -> dict[tuple[str, str], NodeState]:
    """The terminal status this run's journal recorded for each ``(round, node)``.

    Read straight from the journal rather than from `result.json`, because a round
    still in flight has written no result and that in-flight round is exactly when
    this view is asked what is happening.

    That makes the journal a trust boundary, so both values crossing it are checked
    against their own domain: ``run_id`` arrives as a history *label* a subprocess
    wrote and becomes a path component, and a recorded status is validated against
    the terminal states a node can settle in. This view degrades rather than raising
    on either — a status it cannot recognize falls back to what the event kind
    itself proves, which for `node-settled` is only that the node is no longer
    running.
    """
    try:
        events = read_events(runs_dir / runs.validate_run_id(run_id) / JOURNAL_NAME)
    except (ConfigError, OSError):
        return {}
    settled: dict[tuple[str, str], NodeState] = {}
    for event in events:
        # A step-scoped wait belongs to a node still working through its lifecycle;
        # only the node-level locator settles the node itself.
        if event.node is None or event.step is not None or event.kind not in _SETTLING_KINDS:
            continue
        recorded = _SETTLING_KINDS[event.kind] or event.detail.get("status")
        # `detail` is persisted JSON, so this can be a list or an object: the type
        # check comes first because membership alone would raise about hashability
        # rather than degrade, which is the one thing this view must not do.
        known = isinstance(recorded, str) and recorded in TERMINAL_NODE_STATES
        status = cast(NodeState, recorded) if known else "done"
        settled[(str(event.round), str(event.node))] = status
    return settled


@dataclass(frozen=True)
class InFlightDispatch:
    """One dispatch the run's journal started and never recorded settling.

    History records completed turns, and a first turn here can run for half an
    hour, so history alone cannot tell *no dispatch* from *no finished turn* —
    opposite conclusions for a supervising planner. The journal knows the start.

    That is the guarantee, and it stands on the journal alone. ``activity`` only
    ever *adds* to it: when the dispatch is streaming, it says what the node is
    doing right now instead of leaving the reader to infer it from elapsed time.
    A node with none is reported exactly as it was before streaming existed.
    """

    round: int
    node: str
    step: str | None
    persona: str | None
    started_at: float
    activity: NodeActivity | None = None

    def describe(self, *, now: float) -> str:
        where = f"{self.node}[{self.step}]" if self.step else self.node
        elapsed = max(0, int(now - self.started_at))
        persona = f" {self.persona}" if self.persona else ""
        doing = f"; {self.activity.describe(now=now)}" if self.activity else ""
        return (
            f"round-{self.round:02d} {where}{persona} — "
            f"in flight for {elapsed // 60}m{elapsed % 60:02d}s, no completed turn yet{doing}"
        )


def _persona(detail: Mapping[str, Any]) -> str | None:
    value = detail.get("persona")
    return value if isinstance(value, str) and value else None


def in_flight_dispatches(
    runs_dir: Path,
    run_id: str,
    *,
    activity: Mapping[tuple[str, str], NodeActivity] | None = None,
) -> list[InFlightDispatch]:
    """Every dispatch this run's journal shows started and not settled, in order.

    Node-level and step-level starts collapse onto one entry per node, because they
    describe the same running node at two depths: a lifecycle node reports the step
    it is on, and reverts to the node itself once that step settles. Reporting both
    would count one dispatch twice.

    ``activity`` is what the run's live dispatches are publishing, keyed the same way
    `_settled_nodes` keys its answer. It is supplied by the caller rather than read
    here so this stays a pure function of the journal — and so a view can be told to
    read no scratch at all without changing what it proves.
    """
    try:
        events = read_events(runs_dir / runs.validate_run_id(run_id) / JOURNAL_NAME)
    except (ConfigError, OSError):
        return []
    live = activity or {}
    started: dict[tuple[int, str], InFlightDispatch] = {}
    for event in events:
        if event.node is None:
            continue
        key = (event.round, str(event.node))
        match event.kind:
            case "node-started":
                started[key] = InFlightDispatch(
                    event.round, str(event.node), None, _persona(event.detail), event.at
                )
            case "step-started":
                started[key] = InFlightDispatch(
                    event.round,
                    str(event.node),
                    str(event.step) if event.step else None,
                    _persona(event.detail),
                    event.at,
                )
            case "step-settled" if (running := started.get(key)) is not None:
                # The step is over and the node is not: keep it listed, timed from
                # here, because the next step's dispatch is what it is now doing.
                started[key] = InFlightDispatch(running.round, running.node, None, None, event.at)
            case "node-settled" | "node-failed":
                started.pop(key, None)
            case _:
                continue
    return sorted(
        (
            replace(dispatch, activity=live.get((str(dispatch.round), dispatch.node)))
            for dispatch in started.values()
        ),
        key=lambda item: (item.round, item.node),
    )


def _labelled_locator(labels: Mapping[str, str]) -> tuple[str, str] | None:
    """The ``(round, node)`` a dispatch's history labels name, if they name one.

    These are values a subprocess wrote, so they are checked against the domain the
    journal records rather than used as a key on sight: `graph_labels` stamps a
    round counting from 1 and a non-empty node id, and `Event` admits nothing else.
    Anything outside that names no node this run journalled, and a run-scoped
    dispatch with no node — an orchestrator's own session — legitimately has none.
    """
    node = labels.get("node", "")
    recorded_round = labels.get("round", "")
    if not node or not recorded_round.isdigit() or int(recorded_round) < 1:
        return None
    # Normalized through `int` so a zero-padded label and the journal's own integer
    # round cannot spell the same round two ways.
    return str(int(recorded_round)), node


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
    # One journal read per run named by a session label, shared across that run's
    # sessions: a run with twenty dispatches must not re-read its journal twenty
    # times to answer the same question about it.
    settled: dict[str, dict[tuple[str, str], NodeState]] = {}
    for session in history.worker_sessions(oneharness_bin=oneharness_bin):
        if run_id is not None and session.labels.get(RUN_LABEL) != run_id:
            continue
        records = history.session_records(session)
        summary = history.digest(records, session.session_id)
        latest = records[-1] if records else {}
        git = _git_state(session.project)
        branch = git.branch if git else None
        publication = registry.identity_for_checkout(session.project)
        session_run = session.labels.get(RUN_LABEL)
        locator = _labelled_locator(session.labels)
        node_state: NodeState | None = None
        if session_run is not None and locator is not None:
            if session_run not in settled:
                settled[session_run] = _settled_nodes(runs_dir, session_run)
            node_state = settled[session_run].get(locator)
        result.append(
            TaskStatus(
                session_id=session.session_id,
                project=str(session.project),
                task=session.name,
                harness=str(latest.get("harness", "?")),
                model=str(latest.get("model", "?")),
                status=summary.status,
                node_state=node_state,
                running=is_running(git, node_state),
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


def _human(
    tasks: list[TaskStatus],
    *,
    run_id: runs.RunId | None = None,
    in_flight: Sequence[InFlightDispatch] = (),
    now: float | None = None,
) -> str:
    at = time.time() if now is None else now
    running = [dispatch.describe(now=at) for dispatch in in_flight]
    if not tasks:
        if run_id is not None and running:
            # The honest empty state: history has nothing *because* nothing has
            # finished a turn, which is the opposite of nothing running.
            return "\n".join(
                [
                    f"No completed harness turns recorded for run {run_id} yet, and "
                    f"{len(running)} dispatch(es) in flight — a history record is written "
                    "per finished turn:",
                    *(f"  {line}" for line in running),
                ]
            )
        if run_id is not None:
            return f"No dispatched tasks recorded for run {run_id}."
        return "No running tasks. Pass N or --all to include recent finished tasks."
    lines: list[str] = [f"In flight: {line}" for line in running]
    for task in tasks:
        # The journal's own word for the node, when it has one, rather than the
        # worktree's: "recent" would read as a finished task for a node that failed.
        state = "running" if task.running else task.node_state or "recent"
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
    # Only for a named run: the unscoped view would have to read every recorded
    # run's whole journal to answer the same question, and those journals reach tens
    # of thousands of events. `just status <run-id>` is the invocation that asked.
    running_dispatches = (
        in_flight_dispatches(args.runs_dir, run_id, activity=live_activity(run_id))
        if run_id is not None
        else []
    )
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
                parked = parked_indicator(run_dir, parked_after=args.parked_after)
                if parked is not None:
                    indicators.append(f"{run_dir.name}: {parked}")
                stopped = dead is not None or parked is not None
                # A second live orchestrator on a shared identity is the one piece of
                # machine state this view could not previously report: it belongs to
                # no run dir here, and its effects reach this one as a dirty
                # publication checkout or a lost push race.
                if (shared := concurrent_indicator(run_dir, args.parked_after)) is not None:
                    indicators.append(f"{run_dir.name}: {shared}")
                # Reported here as well as in `just runs`, because the two views are
                # read interchangeably: a planner who checks only this one must not
                # have to know that the other is where unread updates are named. Under
                # the same rule that view applies, though — a run reported abandoned or
                # parked keeps the line saying why it stopped, not an invitation to read
                # updates nothing will follow up on.
                if not stopped and (unread := pending_surface_indicator(run_dir)) is not None:
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
        print(
            "\n".join([*indicators, _human(selected, run_id=run_id, in_flight=running_dispatches)])
        )
    return 0
