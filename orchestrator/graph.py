"""Canonical tracked graph executor.

The graph is a mixed DAG of direct onejudge dispatches, repo lifecycle nodes, and
human actions. Human actions are never inferred by the harness: a ready human
node settles as ``waiting`` and records exactly what it unblocks; downstream work
settles as ``blocked`` until a later recorded round attests completion.
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import re
import shlex
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast

from . import REPO_ROOT
from .channel import (
    CHANNEL_DIR_ENV,
    CHANNEL_ENDPOINTS,
    CHANNEL_RUN_ID_ENV,
    ProposalPump,
    ProposalSink,
    heartbeat_state,
)
from .cli_contract import ROUND_BUDGET_OPTION
from .config import ConfigError, load_yaml
from .coordination import advisory_lock, reset_harness_observer, set_harness_observer
from .dispatch import Report, dispatch
from .edits import EditError, apply_edit
from .goals import Goal, find_active_run, finish_run, graph_identities, parse_goal, register_run
from .journal import (
    JOURNAL_NAME,
    TERMINAL_NODE_RESULT_FIELD,
    Event,
    EventKind,
    JournalOperation,
    JournalSink,
    NodeJournal,
    NullJournal,
    open_journal,
    read_events,
    reconcile,
)
from .labels import graph_labels
from .lifecycle import (
    LifecycleResult,
    LifecycleRunner,
    RepoAliasResolver,
    RepoPlanNode,
    StackBase,
    add_lifecycle_args,
    combine_stack_bases,
    emit,
    make_repo_runner,
    parse_repo_node,
    persist_report_artifacts,
    result_payload,
    validate_repo_aliases,
)
from .outcomes import (
    INFRASTRUCTURE_FAILURE_OUTCOME,
    LIFECYCLE_OUTCOMES,
    NODE_OUTCOMES,
)
from .plan import (
    NODE_KINDS,
    PLAN_SCHEMA_VERSION,
    AgentRunner,
    NodeRun,
    PlanError,
    PlanNode,
    _topological_order,
    make_dispatch_runner,
    parse_agent_node,
    parse_cross_dag_dependency,
    reconcile_dag,
)
from .registry import Registry
from .runs import (
    RECORDED_RESULT_SCHEMA_VERSION,
    GraphPayload,
    GraphResultItem,
    HumanActionPayload,
    NodeId,
    RunId,
    prepare_round,
    resolve_run_dir,
    status_summary,
    validate_run_id,
    write_result,
)
from .workspace import Workspace

NodeKind = Literal["agent", "human"]
EXIT_BY_STATE = {"complete": 0, "waiting": 1, "failed": 1}
DEFAULT_ROUND_BUDGET = 14_400.0

_INFRASTRUCTURE_FAILURE_PATTERNS = (
    re.compile(r"provider error.*\b(?:respond|supervisor)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"oneharness exited with signal:\s*9\b", re.IGNORECASE),
    re.compile(r"harness failed\s*\(\s*auth\s*\)", re.IGNORECASE),
    re.compile(r"cannot write v0\.3 history telemetry", re.IGNORECASE),
    re.compile(r"new history record lacks complete v0\.3 telemetry", re.IGNORECASE),
)


def infrastructure_failure_detail(exc: BaseException) -> str | None:
    """Return the durable underlying error only for known no-dispatch failures."""
    detail = str(exc).strip()
    if any(pattern.search(detail) for pattern in _INFRASTRUCTURE_FAILURE_PATTERNS):
        return detail
    return None


_AGENT_NODE_FIELDS = (
    "persona",
    "repo",
    "steps",
    "session",
    "project_dir",
    "max_turns",
    "done_when",
    "expects_no_diff",
    "base_branch",
    "branch",
    "title",
    "verify_cmd",
    "skip_verify",
    "verify_via_ci",
    "merge_policy",
    "workflow",
    "repo_type",
    "execution_checkout",
    "stack_bases",
    "resume",
)


@dataclass
class GraphNode:
    """One top-level tracked node."""

    id: str
    kind: NodeKind = "agent"
    task: str = ""
    deps: list[str] = field(default_factory=list)
    repo: str | None = None
    direct: PlanNode | None = None
    lifecycle: RepoPlanNode | None = None
    definition: dict[str, Any] = field(default_factory=dict)

    @property
    def human(self) -> bool:
        return self.kind == "human"


@dataclass
class Graph:
    tasks: list[GraphNode]
    concurrency: int = 4
    goal: Goal | None = None


@dataclass(frozen=True)
class HumanAction:
    """One ready human action and what completing it releases."""

    ref: str
    task: str
    unblocks: tuple[str, ...] = ()
    unblocks_publication: bool = False

    def downstream(self) -> str:
        if self.unblocks:
            return ", ".join(self.unblocks)
        if self.unblocks_publication:
            return "workstream publication"
        return "nothing downstream"


@dataclass
class NodeResult:
    id: str
    kind: NodeKind
    status: str
    task: str = ""
    report: Report | None = None
    lifecycle: LifecycleResult | None = None
    error: str | None = None
    unblocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    human_actions: list[HumanAction] = field(default_factory=list)
    outcome: str | None = None
    recorded: GraphResultItem | None = None


@dataclass
class GraphResult:
    results: dict[str, NodeResult]
    started_order: list[str]

    @property
    def ok(self) -> bool:
        return all(r.status == "done" for r in self.results.values())

    @property
    def state(self) -> str:
        statuses = {r.status for r in self.results.values()}
        if statuses & {"failed", "skipped", "cancelled"}:
            return "failed"
        if statuses & {"waiting", "blocked"}:
            return "waiting"
        return "complete"

    @property
    def human_actions(self) -> list[HumanAction]:
        return [action for r in self.results.values() for action in r.human_actions]

    def summary(self) -> str:
        return render_summary(self.results, self.state, self.human_actions)


def parse_graph(data: dict[str, Any]) -> Graph:
    """Validate an in-memory tracked graph mapping."""
    schema_version = data.get("schema_version", 1)
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in range(1, PLAN_SCHEMA_VERSION + 1)
    ):
        raise PlanError(
            f"'schema_version' must be between 1 and the current version {PLAN_SCHEMA_VERSION}"
        )
    try:
        goal = parse_goal(data, schema_version=schema_version)
    except ConfigError as exc:
        raise PlanError(str(exc)) from exc
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanError("plan must have a non-empty 'tasks' list")
    if schema_version < 2 and _contains_field(raw_tasks, "expects_no_diff", include_steps=True):
        raise PlanError(
            "'expects_no_diff' requires schema_version 2; legacy plans must omit the field"
        )
    if schema_version < 3 and _contains_field(raw_tasks, "verify_via_ci"):
        raise PlanError(
            "'verify_via_ci' requires schema_version 3; legacy plans must omit the field"
        )
    if schema_version < 5 and any(
        isinstance(task, Mapping)
        and isinstance(task.get("deps"), list)
        and any(isinstance(dep, str) and dep.startswith("run:") for dep in task["deps"])
        for task in raw_tasks
    ):
        raise PlanError("cross-DAG dependencies require schema_version 5")
    concurrency = data.get("concurrency", 4)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise PlanError("'concurrency' must be a positive integer")

    nodes: dict[str, GraphNode] = {}
    for i, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise PlanError(f"task #{i} must be a mapping")
        nid = raw.get("id")
        if not isinstance(nid, str) or not nid:
            raise PlanError(f"task #{i} needs a non-empty string 'id'")
        if nid in nodes:
            raise PlanError(f"duplicate task id: {nid!r}")
        nodes[nid] = _parse_node(nid, raw)

    for nid, node in nodes.items():
        for dep in node.deps:
            if parse_cross_dag_dependency(dep) is not None:
                continue
            if dep not in nodes:
                raise PlanError(f"task {nid!r} depends on unknown task {dep!r}")
            if dep == nid:
                raise PlanError(f"task {nid!r} depends on itself")
    _topological_order(
        {
            nid: PlanNode(id=nid, persona="_", task="_", deps=node.deps)
            for nid, node in nodes.items()
        }
    )
    return Graph(tasks=list(nodes.values()), concurrency=concurrency, goal=goal)


@dataclass
class CrossDagObserver:
    """Resolve and remember external edges through the shared active-runs index."""

    journal: JournalSink
    baselines: dict[str, int] = field(default_factory=dict)
    signalled: set[tuple[str, str]] = field(default_factory=set)

    def __post_init__(self) -> None:
        events: list[Event] = getattr(self.journal, "events", lambda: [])()
        for event in events:
            if event.kind != "cross-dag-satisfied":
                continue
            dependency = event.detail.get("dependency")
            last_seq = event.detail.get("last_seq")
            if isinstance(dependency, str) and isinstance(last_seq, int):
                self.baselines.setdefault(dependency, last_seq)

    def reconcile_edges(
        self, deps: Iterable[str], consumers: Mapping[str, list[str]]
    ) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for raw in deps:
            ref = parse_cross_dag_dependency(raw)
            if ref is None:
                continue
            active = find_active_run(ref.run_id)
            if active is None:
                statuses[raw] = "blocked"
                continue
            path = Path(active["run_dir"]) / JOURNAL_NAME
            with advisory_lock(f"journal:{path}"):
                state = reconcile(path, RunId(ref.run_id))
            terminal = [
                event
                for event in read_events(path)
                if event.run_id == ref.run_id
                and event.node == ref.node_id
                and event.kind in {"node-settled", "node-failed"}
            ]
            done = (
                bool(terminal)
                and terminal[-1].kind == "node-settled"
                and (terminal[-1].detail.get("status") == "done")
            )
            statuses[raw] = "done" if done else "blocked"
            if not done:
                continue
            baseline = self.baselines.get(raw)
            if baseline is None:
                baseline = state.last_seq
                self.baselines[raw] = baseline
                consumer = next(iter(consumers.get(raw, [])), None)
                if consumer is not None:
                    self.journal.append(
                        "cross-dag-satisfied",
                        node=NodeId(consumer),
                        detail={"dependency": raw, "last_seq": baseline},
                    )
            if state.last_seq <= baseline:
                continue
            for consumer in consumers.get(raw, []):
                key = (raw, consumer)
                if key in self.signalled:
                    continue
                self.journal.append(
                    "upstream-modified",
                    node=NodeId(consumer),
                    detail={
                        "dependency": raw,
                        "captured_last_seq": baseline,
                        "observed_last_seq": state.last_seq,
                    },
                )
                self.signalled.add(key)
        return statuses


def _contains_field(tasks: list[Any], field: str, *, include_steps: bool = False) -> bool:
    """Return whether a versioned field occurs at its supported node levels."""
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if field in task:
            return True
        steps = task.get("steps", [])
        if (
            include_steps
            and isinstance(steps, list)
            and any(isinstance(step, dict) and field in step for step in steps)
        ):
            return True
    return False


def _parse_node(nid: str, raw: dict[str, Any]) -> GraphNode:
    kind = raw.get("kind", "agent")
    if kind not in NODE_KINDS:
        raise PlanError(f"task {nid!r} 'kind' must be one of {NODE_KINDS}")
    deps = raw.get("deps", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise PlanError(f"task {nid!r} 'deps' must be a list of ids")
    if kind == "human":
        if "/" in nid:
            raise PlanError(
                f"human task {nid!r} cannot contain '/': that separator is reserved for "
                "NODE_ID/STEP_ID references"
            )
        task = raw.get("task")
        if not isinstance(task, str) or not task.strip():
            raise PlanError(f"human task {nid!r} needs a non-empty 'task'")
        present = [key for key in _AGENT_NODE_FIELDS if key in raw]
        if present:
            raise PlanError(
                f"human task {nid!r} cannot set {', '.join(map(repr, present))}: "
                "it names action for a person, not work the harness runs"
            )
        return GraphNode(id=nid, kind="human", task=task, deps=list(deps), definition=dict(raw))
    if raw.get("repo") is not None:
        node = parse_repo_node(nid, raw)
        return GraphNode(
            id=nid,
            kind="agent",
            task=node.task or "",
            deps=list(deps),
            repo=node.repo,
            lifecycle=node,
            definition=dict(raw),
        )
    direct = parse_agent_node(nid, raw)
    return GraphNode(
        id=nid,
        kind="agent",
        task=direct.task,
        deps=list(deps),
        direct=direct,
        definition=dict(raw),
    )


def load_graph(path: str | Path) -> Graph:
    """Load a tracked graph file (JSON or YAML)."""
    try:
        data = load_yaml(path)
    except ConfigError as exc:
        raise PlanError(str(exc)) from exc
    return parse_graph(data)


def validate_graph_repo_aliases(graph: Graph, registry: RepoAliasResolver | None = None) -> None:
    """Validate registry-backed lifecycle inputs before a graph is launched."""
    selected_registry = registry or Registry()
    for node in graph.tasks:
        if node.lifecycle is not None:
            validate_repo_aliases(node.lifecycle, selected_registry)


def run_graph(
    graph: Graph,
    *,
    agent_runner: AgentRunner,
    lifecycle_runner: LifecycleRunner,
    concurrency: int | None = None,
    journal: JournalSink | None = None,
    run_id: RunId | None = None,
    round_number: int | None = None,
    already_started: frozenset[str] = frozenset(),
    replayed_runs: Mapping[str, NodeRun] | None = None,
    replayed_order: list[str] | None = None,
    proposal_pump: ProposalSink | None = None,
    round_budget: float | None = DEFAULT_ROUND_BUDGET,
) -> GraphResult:
    """Schedule and run a mixed tracked graph, journaling each transition.

    Journaling is observation only: `NullJournal` is substituted when a round is
    not recorded, and nothing here reads the journal back, so an unrecorded round
    behaves identically to a recorded one.

    This is the last place that knows which node a piece of work belongs to, so it
    is where each node's scope is built. ``run_id``/``round_number`` name the round
    the journal already writes; they are taken separately because they must also
    reach a *dispatched subprocess* as history labels, and a subprocess cannot be
    handed a journal.
    """
    conc = concurrency if concurrency is not None else graph.concurrency
    log: JournalSink = journal if journal is not None else NullJournal()
    nodes = {n.id: n for n in graph.tasks}
    deps = {nid: nodes[nid].deps for nid in nodes}
    dependents: dict[str, list[str]] = {nid: [] for nid in nodes}
    for nid, node in nodes.items():
        for dep in node.deps:
            dependents.setdefault(dep, []).append(nid)
    external_deps = {
        dep for node in nodes.values() for dep in node.deps if parse_cross_dag_dependency(dep)
    }
    cross_dag = CrossDagObserver(log)
    completed: dict[str, LifecycleResult] = {
        nid: run.payload
        for nid, run in (replayed_runs or {}).items()
        if run.status == "done" and isinstance(run.payload, LifecycleResult)
    }
    guard = threading.Lock()
    cancellations = {node.id: threading.Event() for node in graph.tasks}
    frontier = {nid: run.status for nid, run in (replayed_runs or {}).items()} | {
        nid: "running" for nid in already_started
    }
    attestations: list[str] = []
    round_started = time.monotonic()
    budget_surfaced = False

    def settle(nid: str, node: GraphNode, node_log: NodeJournal) -> NodeRun:
        """Run one already-started node to its outcome, journaling how it settled."""
        expects_no_diff = bool(
            (node.direct and node.direct.expects_no_diff)
            or (node.lifecycle and node.lifecycle.expects_no_diff)
        )
        if expects_no_diff:
            run = NodeRun("done", None, "no-changes")
            node_log.append(
                "node-settled",
                detail={
                    "status": "done",
                    "outcome": "no-changes",
                    TERMINAL_NODE_RESULT_FIELD: cast(
                        Any, _run_payload(node, run, dependents.get(nid, []))
                    ),
                },
            )
            return run
        # llmlint: ignore[changed_behavior_has_e2e] this composition seam is split at its real
        # boundaries: test_running_direct_and_lifecycle_drops_cancel_cooperatively drives the live
        # reconciler transition, while test_cooperative_real_dispatch_cancellation_preserves_and_
        # recovers_branch proves the resulting cooperative dispatch, durable branch handoff, and
        # repo-recover journey against real git.
        if node.lifecycle is not None:
            with guard:
                anchors = combine_stack_bases(node.lifecycle, completed)
            lifecycle_args: dict[str, Any] = {"journal": node_log}
            if "cancel" in inspect.signature(lifecycle_runner).parameters:
                lifecycle_args["cancel"] = cancellations[nid]
            result = lifecycle_runner(
                replace(node.lifecycle, stack_bases=anchors), **lifecycle_args
            )
            if cancellations[nid].is_set() and not result.ok:
                run = NodeRun("cancelled", "cancelled cooperatively", result)
                node_log.append(
                    "node-settled",
                    detail={
                        "status": "cancelled",
                        "outcome": result.outcome,
                        TERMINAL_NODE_RESULT_FIELD: cast(
                            Any, _run_payload(node, run, dependents.get(nid, []))
                        ),
                    },
                )
                return run
            if result.waiting:
                run = NodeRun("waiting", result.detail, result)
                node_log.append(
                    "node-settled",
                    detail={
                        "status": "waiting",
                        "outcome": result.outcome,
                        TERMINAL_NODE_RESULT_FIELD: cast(
                            Any, _run_payload(node, run, dependents.get(nid, []))
                        ),
                    },
                )
                return run
            agent_steps = [step for step in node.lifecycle.steps or [] if not step.human]
            expected_step_no_diff = bool(agent_steps) and all(
                step.expects_no_diff for step in agent_steps
            )
            if result.outcome == "no-changes" and expected_step_no_diff:
                run = NodeRun("done", None, result)
                node_log.append(
                    "node-settled",
                    detail={
                        "status": "done",
                        "outcome": "no-changes",
                        TERMINAL_NODE_RESULT_FIELD: cast(
                            Any, _run_payload(node, run, dependents.get(nid, []))
                        ),
                    },
                )
                return run
            if not result.ok:
                run = NodeRun("failed", result.detail or result.outcome, result)
                node_log.append(
                    "node-failed",
                    detail={
                        "outcome": result.outcome,
                        "detail": result.detail,
                        TERMINAL_NODE_RESULT_FIELD: cast(
                            Any, _run_payload(node, run, dependents.get(nid, []))
                        ),
                    },
                )
                return run
            with guard:
                completed[nid] = result
            run = NodeRun("done", None, result)
            node_log.append(
                "node-settled",
                detail={
                    "status": "done",
                    "outcome": result.outcome,
                    TERMINAL_NODE_RESULT_FIELD: cast(
                        Any, _run_payload(node, run, dependents.get(nid, []))
                    ),
                },
            )
            return run
        agent_args: dict[str, Any] = {"labels": node_log.labels}
        if "cancel" in inspect.signature(agent_runner).parameters:
            agent_args["cancel"] = cancellations[nid]
        direct = cast(PlanNode, node.direct)
        report = agent_runner(direct, **agent_args)
        persist_report_artifacts(
            node_log,
            report,
            session=direct.session or f"dispatch-{direct.persona}",
        )
        if cancellations[nid].is_set():
            run = NodeRun("cancelled", "cancelled cooperatively", report)
            node_log.append(
                "node-settled",
                detail={
                    "status": "cancelled",
                    TERMINAL_NODE_RESULT_FIELD: cast(
                        Any, _run_payload(node, run, dependents.get(nid, []))
                    ),
                },
            )
            return run
        if report.completed:
            run = NodeRun("done", None, report)
            node_log.append(
                "node-settled",
                detail={
                    "status": "done",
                    "turns": report.assistant_turns,
                    TERMINAL_NODE_RESULT_FIELD: cast(
                        Any, _run_payload(node, run, dependents.get(nid, []))
                    ),
                },
            )
            return run
        detail = (
            "worker-died"
            if report.outcome == "worker-died"
            else "did not complete (hit the turn cap)"
        )
        run = NodeRun("failed", detail, report)
        node_log.append(
            "node-failed",
            detail={
                "detail": detail,
                **({"outcome": report.outcome} if report.outcome else {}),
                "turns": report.assistant_turns,
                TERMINAL_NODE_RESULT_FIELD: cast(
                    Any, _run_payload(node, run, dependents.get(nid, []))
                ),
            },
        )
        return run

    def run_one(nid: str) -> NodeRun:
        node = nodes[nid]
        frontier[nid] = "running"
        node_log = NodeJournal(sink=log, node=NodeId(nid), run_id=run_id, round=round_number)
        if node.human:
            run = NodeRun("waiting", "awaiting human action")
            node_log.append(
                "human-waiting",
                detail={
                    "task": first_line(node.task),
                    TERMINAL_NODE_RESULT_FIELD: cast(
                        Any, _run_payload(node, run, dependents.get(nid, []))
                    ),
                },
            )
            return run
        if nid not in already_started:
            node_log.append(
                "node-started",
                detail={"node_kind": "lifecycle" if node.lifecycle else "direct"},
            )

        def observe(kind: EventKind, detail: Mapping[str, str | float | bool]) -> None:
            node_log.append(kind, detail=detail)

        token = set_harness_observer(observe)
        try:
            return settle(nid, node, node_log)
        except Exception as exc:
            # `schedule_dag` settles a runner that raised as a failed node, so the
            # ledger records it either way. Journal it here as well: a `node-started`
            # with nothing to close it is how this journal says "still running", and
            # a node that raised is the one thing it is not.
            infrastructure_detail = infrastructure_failure_detail(exc)
            outcome = INFRASTRUCTURE_FAILURE_OUTCOME if infrastructure_detail is not None else None
            failed = NodeRun("failed", str(exc), outcome)
            node_log.append(
                "node-failed",
                detail={
                    "detail": str(exc),
                    "error": type(exc).__name__,
                    **({"outcome": outcome} if outcome is not None else {}),
                    TERMINAL_NODE_RESULT_FIELD: cast(
                        Any, _run_payload(node, failed, dependents.get(nid, []))
                    ),
                },
            )
            if infrastructure_detail is not None:
                if proposal_pump is not None:
                    message = (
                        "terminal infrastructure failure; dispatch cannot run: "
                        + infrastructure_detail
                    )
                    proposal_pump.defer_blocking(nid, message)
                return failed
            raise
        finally:
            reset_harness_observer(token)

    actual = dict(replayed_runs or {})
    actual.update({nid: NodeRun("running") for nid in already_started if nid not in actual})

    def on_settled(nid: str, run: NodeRun) -> None:
        frontier[nid] = run.status
        if proposal_pump is None or not isinstance(run.payload, Report):
            return
        if run.payload.assessment and run.payload.assessment.strip().lower() != "none":
            proposal_pump.propose(nid, run.payload.assessment)
        proposal_pump.persist_replies()

    def reconcile_commands(status: dict[str, str], actual: dict[str, NodeRun]) -> None:
        nonlocal graph
        if proposal_pump is None:
            return
        heartbeat_tick = getattr(proposal_pump, "heartbeat_tick", None)
        if heartbeat_tick is not None:
            heartbeat_tick()
        proposal_pump.persist_replies()
        drain = getattr(proposal_pump, "drain_commands", lambda: ())
        for command in drain():
            try:
                updated, operations = apply_edit(
                    graph, command, states=frontier, attestations=attestations
                )
            except EditError as exc:
                proposal_pump.propose("reconciler", f"rejected {command.op}: {exc}")
                continue
            # One event is the commit boundary: replay either sees every compiled
            # edge mutation or none of them.
            log.append("edit-committed", detail={"operations": cast(Any, operations)})
            graph = updated
            nodes.clear()
            nodes.update({node.id: node for node in graph.tasks})
            for node in graph.tasks:
                cancellations.setdefault(node.id, threading.Event())
            deps.clear()
            deps.update({node.id: node.deps for node in graph.tasks})
            external_deps.clear()
            external_deps.update(
                dependency
                for node in graph.tasks
                for dependency in node.deps
                if parse_cross_dag_dependency(dependency)
            )
            dependents.clear()
            dependents.update({node.id: [] for node in graph.tasks})
            for node in graph.tasks:
                for dependency in node.deps:
                    dependents.setdefault(dependency, []).append(node.id)
            # `completion-requested` is intentionally not dispatched below: completion is the
            # planner's closeout verdict to the onejudge supervisor (committed above for audit and
            # replay), not a scheduler transition. run_graph settles its own frontier; the verdict
            # that ends the run travels the channel, exercised by the real live-edit journey.
            # llmlint: ignore[changed_behavior_has_e2e] completion is a journaled closeout verdict,
            # not a scheduling change; its reconciler commit is unit-tested and its run-ending
            # effect is proven through the real channel journeys rather than this operation loop.
            for operation in operations:
                if operation["kind"] == "human-attested":
                    ref = cast(str, operation["detail"]["ref"])
                    attestations.append(ref)
                    status[ref] = "done"
                    actual[ref] = NodeRun("done", payload="human-attested")
                elif operation["kind"] == "retry-requested":
                    retried = operation["node"]
                    cancellations[retried].set()
                    reset = operation["detail"].get("reset", [])
                    if isinstance(reset, list):
                        for dependent in reset:
                            if isinstance(dependent, str) and status.get(dependent) in {
                                "skipped",
                                "blocked",
                            }:
                                status[dependent] = "pending"
                                actual.pop(dependent, None)
                elif operation["kind"] == "node-dropped":
                    dropped = operation["node"]
                    cancellations[dropped].set()
                    if status.get(dropped) != "running":
                        status.pop(dropped, None)
                        actual.pop(dropped, None)

    def observe_tick() -> None:
        nonlocal budget_surfaced
        cross_dag.reconcile_edges(external_deps, dependents)
        if proposal_pump is not None:
            proposal_pump.persist_replies()
        if (
            not budget_surfaced
            and round_budget is not None
            and time.monotonic() - round_started >= round_budget
        ):
            budget_surfaced = True
            for cancellation in cancellations.values():
                cancellation.set()
            if proposal_pump is not None:
                proposal_pump.propose_blocking(
                    "round-budget",
                    f"round exceeded its {round_budget:g}s liveness budget; "
                    "in-flight workers were cancelled and planner intervention is required",
                )

    runs, started_order = reconcile_dag(
        lambda: list(nodes),
        deps,
        run_one,
        concurrency=conc,
        actual=actual,
        started_order=replayed_order,
        on_settled=on_settled,
        on_tick=observe_tick,
        on_reconcile=reconcile_commands if proposal_pump is not None else None,
        external_status=lambda: cross_dag.reconcile_edges(external_deps, dependents),
    )
    return _collect(nodes, runs, [node for node in started_order if node in nodes])


def _run_payload(node: GraphNode, run: NodeRun, dependents: list[str]) -> GraphResultItem:
    """Serialize a settled run exactly as the eventual graph result will expose it."""
    actions = _waiting_actions(node, run, dependents) if run.status == "waiting" else []
    result = NodeResult(
        id=node.id,
        kind=node.kind,
        status=run.status,
        task=node.task,
        report=run.payload if isinstance(run.payload, Report) else None,
        lifecycle=run.payload if isinstance(run.payload, LifecycleResult) else None,
        error=run.error,
        unblocks=list(dependents) if run.status == "waiting" else [],
        human_actions=actions,
        outcome=(
            run.payload if isinstance(run.payload, str) and run.payload in NODE_OUTCOMES else None
        ),
    )
    return _node_payload(result)


def _replay_node_run(node: GraphNode, item: GraphResultItem) -> NodeRun:
    """Restore scheduler actual state while retaining the exact serialized result."""
    status = item["status"]
    error = item.get("error")
    recorded_outcome = item.get("outcome")
    payload: Any = (
        recorded_outcome
        if isinstance(recorded_outcome, str) and recorded_outcome in NODE_OUTCOMES
        else None
    )
    if node.lifecycle is not None:
        anchors = [
            StackBase(
                branch=anchor["branch"],
                repo=anchor.get("repo"),
                identity=cast(Any, anchor.get("identity")),
                base_branch=anchor.get("base_branch"),
                pr=anchor.get("pr"),
                pr_base=anchor.get("pr_base"),
            )
            for anchor in item.get("stack_bases", [])
        ]
        # llmlint: ignore[changed_behavior_has_e2e] lifecycle e2e covers production.
        raw_deferred_cleanup = item.get("deferred_cleanup", [])
        # llmlint: ignore[changed_behavior_has_e2e] unit test supplies malformed JSON.
        if not isinstance(raw_deferred_cleanup, list) or not all(
            isinstance(detail, str) for detail in raw_deferred_cleanup
        ):
            raise ConfigError("recorded lifecycle result has invalid deferred_cleanup")
        raw_outcome = item.get("outcome", "error")
        if not isinstance(raw_outcome, str) or raw_outcome not in LIFECYCLE_OUTCOMES:
            raise ConfigError(f"recorded lifecycle result has invalid outcome {raw_outcome!r}")
        payload = LifecycleResult(
            repo=item.get("repo", node.lifecycle.repo),
            task=node.task,
            persona=node.lifecycle.persona or "workstream",
            base_branch=item.get("base_branch", ""),
            branch=item.get("branch", ""),
            outcome=raw_outcome,
            publication_identity=cast(Any, item.get("publication_identity")),
            publication_workflow=cast(Any, item.get("publication_workflow")),
            repository_type=cast(Any, item.get("repository_type")),
            merge_policy=cast(Any, item.get("merge_policy")),
            pr_base=item.get("pr_base", ""),
            synthetic_stack_base=item.get("synthetic_stack_base"),
            stack_bases=anchors,
            detail=item.get("detail", ""),
            # llmlint: ignore[changed_behavior_has_e2e] unit round-trip covers replay.
            deferred_cleanup=raw_deferred_cleanup,
            waiting_steps=cast(list[str], list(item.get("waiting_steps", []))),
        )
    return NodeRun(status, error, payload, item)


def _collect(
    nodes: dict[str, GraphNode], runs: dict[str, NodeRun], started_order: list[str]
) -> GraphResult:
    dependents: dict[str, list[str]] = {nid: [] for nid in nodes}
    for nid, node in nodes.items():
        for dep in node.deps:
            dependents.setdefault(dep, []).append(nid)
    status = {nid: run.status for nid, run in runs.items()}
    actions = {
        nid: _waiting_actions(node, runs[nid], dependents[nid])
        for nid, node in nodes.items()
        if status.get(nid) == "waiting"
    }

    cache: dict[str, list[str]] = {}

    def blocking(nid: str) -> list[str]:
        if nid in cache:
            return cache[nid]
        refs: list[str] = []
        if status.get(nid) == "waiting":
            refs = [action.ref for action in actions.get(nid, [])]
        elif status.get(nid) == "blocked":
            for dep in nodes[nid].deps:
                if dep in nodes and status.get(dep) in ("waiting", "blocked"):
                    refs.extend(blocking(dep))
        cache[nid] = _dedupe(refs)
        return cache[nid]

    results: dict[str, NodeResult] = {}
    for nid, node in nodes.items():
        if nid not in runs:
            continue
        run = runs[nid]
        results[nid] = NodeResult(
            id=nid,
            kind=node.kind,
            status=run.status,
            task=node.task,
            report=run.payload if isinstance(run.payload, Report) else None,
            lifecycle=run.payload if isinstance(run.payload, LifecycleResult) else None,
            error=run.error,
            unblocks=list(dependents[nid]) if run.status == "waiting" else [],
            blocked_by=blocking(nid) if run.status == "blocked" else [],
            human_actions=actions.get(nid, []),
            outcome=(
                run.payload
                if isinstance(run.payload, str) and run.payload in NODE_OUTCOMES
                else None
            ),
            recorded=cast(GraphResultItem, run.recorded) if run.recorded is not None else None,
        )
    return GraphResult(results=results, started_order=started_order)


def _waiting_actions(node: GraphNode, run: NodeRun, dependents: list[str]) -> list[HumanAction]:
    if node.human:
        return [HumanAction(ref=node.id, task=node.task, unblocks=tuple(dependents))]
    result = run.payload
    if not isinstance(result, LifecycleResult) or node.lifecycle is None:
        return []
    steps = node.lifecycle.steps or []
    by_id = {step.id: step for step in steps}
    found: list[HumanAction] = []
    for sid in result.waiting_steps:
        step = by_id.get(sid)
        if step is None:
            continue
        downstream = tuple(f"{node.id}/{other.id}" for other in steps if sid in other.deps)
        found.append(
            HumanAction(
                ref=f"{node.id}/{sid}",
                task=step.task,
                unblocks=downstream,
                unblocks_publication=not downstream,
            )
        )
    return found


def _dedupe(refs: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(refs))


def action_payload(action: HumanAction) -> HumanActionPayload:
    return HumanActionPayload(
        ref=action.ref,
        task=action.task,
        unblocks=list(action.unblocks),
        unblocks_publication=action.unblocks_publication,
    )


def _node_payload(result: NodeResult) -> GraphResultItem:
    if result.recorded is not None:
        return result.recorded
    item: dict[str, Any] = {}
    if result.lifecycle is not None:
        item.update(result_payload(result.lifecycle))
    elif result.outcome is not None:
        item.update({"outcome": result.outcome, "completed": True})
    elif result.kind == "agent":
        report = result.report
        item.update(
            {
                "completed": report.completed if report else False,
                "exit_code": report.exit_code if report else None,
                "verdicts": report.verdicts if report else [],
                "usage": report.usage if report else {},
                **({"artifacts": report.artifacts} if report and report.artifacts else {}),
                # llmlint: ignore[changed_behavior_has_e2e] The pinned report-v4 producer cannot
                # emit this additive report-v5 field; the telemetry CLI E2E injects the exact
                # upstream payload at the persisted report boundary and exercises consumption.
                **({"telemetry": report.telemetry} if report and report.telemetry else {}),
            }
        )
    item.update({"kind": result.kind, "status": result.status, "task": result.task})
    if result.unblocks:
        item["unblocks"] = result.unblocks
    if result.blocked_by:
        item["blocked_by"] = result.blocked_by
    if result.human_actions:
        item["human_actions"] = [action_payload(action) for action in result.human_actions]
    item["error"] = result.error
    return cast(GraphResultItem, item)


def graph_payload(result: GraphResult, *, round_number: int | None = None) -> GraphPayload:
    payload = GraphPayload(
        schema_version=RECORDED_RESULT_SCHEMA_VERSION,
        ok=result.ok,
        state=result.state,
        started_order=result.started_order,
        results={nid: _node_payload(item) for nid, item in result.results.items()},
    )
    if round_number is not None:
        payload["round"] = round_number
    return payload


_HEADLINE = {
    "complete": "all nodes completed",
    "waiting": "awaiting human action",
    "failed": "some nodes did not complete",
}


def first_line(task: str) -> str:
    line = task.strip().splitlines()[0] if task.strip() else ""
    return line[:80] + ("..." if len(line) > 80 else "")


def render_counts(counts: Counter[str]) -> str:
    keys = ["done", "waiting", "blocked", "failed", "skipped"]
    keys.extend(sorted(set(counts) - set(keys)))
    return ", ".join(f"{counts[key]} {key}" for key in keys if counts[key])


def render_summary(results: dict[str, NodeResult], state: str, actions: list[HumanAction]) -> str:
    counts = Counter(r.status for r in results.values())
    lines = [f"plan: {_HEADLINE[state]} ({render_counts(counts)})"]
    for nid, result in results.items():
        label = f"  {nid} [human]" if result.kind == "human" else f"  {nid}"
        line = f"{label}: {result.status}"
        if result.lifecycle is not None:
            line += f" [{result.lifecycle.outcome}]"
        if result.error:
            line += f" ({result.error})"
        lines.append(line)
        if result.blocked_by:
            lines.append(f"    blocked by: {', '.join(result.blocked_by)}")
    lines.extend(render_actions(actions))
    return "\n".join(lines)


def render_actions(actions: list[HumanAction]) -> list[str]:
    if not actions:
        return []
    lines = ["", f"Awaiting {len(actions)} human action(s):"]
    for action in actions:
        lines.append(f"  {action.ref}: {first_line(action.task)}")
        lines.append(f"    unblocks: {action.downstream()}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a tracked graph of direct agents, repo lifecycle nodes, and human actions."
    )
    parser.add_argument("plan", type=Path, help="plan file (JSON or YAML)")
    parser.add_argument(
        "--concurrency", type=int, default=None, help="override the plan's concurrency"
    )
    parser.add_argument(
        "--run", default=None, help="record into this run id instead of deriving one from the plan"
    )
    parser.add_argument("--no-record", action="store_true", help="do not record this round")
    parser.add_argument(
        "--recover",
        action="store_true",
        help="claim a recorded running round only after confirming its owner is gone",
    )
    parser.add_argument(
        "--acknowledge-concurrent",
        action="store_true",
        help="proceed despite active DAGs targeting the same repository identities",
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"), help="run ledger root")
    parser.add_argument(
        "--project-dir", default=None, help="default target project dir for direct agent nodes"
    )
    parser.add_argument("--cwd", default=None, help="working dir for onejudge (default: repo root)")
    parser.add_argument("--onejudge-bin", default="onejudge")
    parser.add_argument("--provider", default=None, choices=["oneharness", "command", "split"])
    parser.add_argument(
        "--dispatch-timeout",
        type=float,
        default=None,
        help="wall-clock cap for one direct agent node's dispatch",
    )
    parser.add_argument(
        ROUND_BUDGET_OPTION,
        type=float,
        default=DEFAULT_ROUND_BUDGET,
        metavar="SECONDS",
        help=f"outer liveness budget for this round (default: {DEFAULT_ROUND_BUDGET:g})",
    )
    add_lifecycle_args(parser)
    args = parser.parse_args(argv)

    try:
        plan_mapping = load_yaml(args.plan)
        graph = parse_graph(plan_mapping)
        validate_graph_repo_aliases(graph)
        if args.concurrency is not None and args.concurrency < 1:
            raise PlanError("'--concurrency' must be a positive integer")
        if not math.isfinite(args.round_budget) or args.round_budget <= 0:
            raise PlanError(f"'{ROUND_BUDGET_OPTION}' must be a positive finite number")
        run_dir = (
            None
            if args.no_record
            else resolve_run_dir(args.runs_dir, plan_mapping, args.plan, args.run)
        )
        acknowledgements = []
        if run_dir is not None:
            acknowledgements = register_run(
                run_id=run_dir.name,
                run_dir=run_dir,
                goal=graph.goal,
                identities=graph_identities(graph),
                pid=os.getpid(),
                acknowledge_concurrent=args.acknowledge_concurrent,
            )
    except (ConfigError, PlanError) as exc:
        print(f"run-plan: {exc}", file=sys.stderr)
        return 2

    round_record: tuple[int, Path] | None = None
    if run_dir is not None:
        try:
            round_record = prepare_round(run_dir, plan_mapping, recover=args.recover)
        except ConfigError as exc:
            print(f"run-plan: could not claim run: {exc}", file=sys.stderr)
            return 2

    journal: JournalSink = NullJournal()
    run_id: RunId | None = None
    round_number: int | None = None
    already_started: frozenset[str] = frozenset()
    replayed_runs: dict[str, NodeRun] = {}
    replayed_order: list[str] = []
    if run_dir is not None and round_record is not None:
        run_id = RunId(run_dir.name)
        round_number = round_record[0]
        journal = open_journal(run_dir, run_id, round_number)
        for acknowledgement in acknowledgements:
            journal.append(
                "concurrent-acknowledged",
                detail={
                    "at": acknowledgement["at"],
                    "runs": acknowledgement["runs"],
                    "identities": acknowledgement["identities"],
                },
            )
        from .projection import ProjectionError, read_strict_events

        try:
            durable_events = read_strict_events(run_dir / "events.jsonl", run_id)
        except ProjectionError as exc:
            print(f"run-plan: cannot replay authoritative event log: {exc}", file=sys.stderr)
            return 2
        round_events = [event for event in durable_events if event.round == round_number]
        expected_definitions = [
            {key: value for key, value in raw_node.items() if key != "deps" or value == []}
            for raw_node in plan_mapping["tasks"]
        ]
        recorded_definitions: list[dict[str, Any]] = []
        for event in round_events:
            if event.kind != "node-added":
                continue
            definition = event.detail.get("definition")
            if not isinstance(definition, Mapping):
                print("run-plan: recorded node definition is malformed", file=sys.stderr)
                return 2
            recorded_definitions.append(dict(definition))
        if recorded_definitions != expected_definitions[: len(recorded_definitions)]:
            print(
                "run-plan: recorded node definitions do not match the recovery plan",
                file=sys.stderr,
            )
            return 2
        missing_definitions = expected_definitions[len(recorded_definitions) :]
        journal_batch_size = 20
        for start in range(0, len(missing_definitions), journal_batch_size):
            journal.append_batch(
                [
                    JournalOperation("node-added", {"definition": definition})
                    for definition in missing_definitions[start : start + journal_batch_size]
                ]
            )
        expected_edges = [
            {"from": dependency, "to": raw_node["id"]}
            for raw_node in plan_mapping["tasks"]
            for dependency in raw_node.get("deps", [])
        ]
        recorded_edges = [
            {"from": event.detail.get("from"), "to": event.detail.get("to")}
            for event in round_events
            if event.kind == "edge-added"
        ]
        if recorded_edges != expected_edges[: len(recorded_edges)]:
            print("run-plan: recorded graph edges do not match the recovery plan", file=sys.stderr)
            return 2
        missing_edges = expected_edges[len(recorded_edges) :]
        for start in range(0, len(missing_edges), journal_batch_size):
            journal.append_batch(
                [
                    JournalOperation("edge-added", edge)
                    for edge in missing_edges[start : start + journal_batch_size]
                ]
            )
        existing_kinds = {event.kind for event in round_events}
        if args.recover and "round-started" in existing_kinds:
            from .projection import project_run

            try:
                replayed = project_run(run_dir / "events.jsonl", run_id, round_number)
            except ProjectionError as exc:
                print(
                    f"run-plan: cannot replay authoritative event log: {exc}",
                    file=sys.stderr,
                )
                return 2
            settled = sorted(
                node for node, state in replayed.node_states.items() if state != "running"
            )
            missing_payloads = [node for node in settled if node not in replayed.node_results]
            if missing_payloads:
                print(
                    "run-plan: could not recover legacy settled nodes without terminal payloads: "
                    + ", ".join(missing_payloads),
                    file=sys.stderr,
                )
                return 2
            nodes_by_id = {node.id: node for node in graph.tasks}
            replayed_runs = {
                node: _replay_node_run(nodes_by_id[node], replayed.node_results[node])
                for node in settled
            }
            replayed_order = list(replayed.node_states)
            already_started = frozenset(
                node for node, state in replayed.node_states.items() if state == "running"
            )
        if "round-started" not in existing_kinds:
            journal.append(
                "round-started",
                detail={
                    "nodes": len(graph.tasks),
                    "concurrency": graph.concurrency,
                    "plan": {key: value for key, value in plan_mapping.items() if key != "tasks"},
                },
            )

    dispatch_timeout = args.dispatch_timeout if args.dispatch_timeout is not None else args.timeout
    proposal_pump: ProposalPump | None = None
    channel_path = os.environ.get(CHANNEL_DIR_ENV)
    channel_run_id = os.environ.get(CHANNEL_RUN_ID_ENV)
    configured_channel_values = (channel_path, channel_run_id)
    # llmlint: ignore[changed_behavior_has_e2e] internal env; malformed only in unit
    if any(configured_channel_values) and not all(configured_channel_values):
        print("run-plan: incomplete proposal channel environment", file=sys.stderr)
        return 2
    if channel_path and channel_run_id and round_number is not None:
        # llmlint: ignore-block[changed_behavior_has_e2e] internal env; malformed only in unit
        try:
            validated_run_id = str(validate_run_id(channel_run_id))
            resolved_channel = Path(channel_path).resolve(strict=True)
            if resolved_channel.parent.name != validated_run_id or not all(
                (resolved_channel / endpoint).is_fifo() for endpoint in CHANNEL_ENDPOINTS
            ):
                raise ValueError("channel identity or endpoints do not match")
        except (OSError, ValueError) as exc:
            print(f"run-plan: invalid proposal channel: {exc}", file=sys.stderr)
            return 2

        def run_check_in() -> bool:
            report = dispatch(
                "check-in",
                (
                    "Produce the dedicated planner pacemaker update for this run "
                    "(dedicated-check-in complete-now). Read durable run state under "
                    f"{resolved_channel.parent}, synthesize one concise non-blocking "
                    "per-workstream status update, send it with "
                    f"`just channel-surface {shlex.quote(validated_run_id)} MESSAGE "
                    f"--runs-dir {shlex.quote(str(resolved_channel.parent.parent))}`, "
                    "and exit. Do not wait for a reply."
                ),
                base_path=args.base_config,
                persona_dir=args.persona_dir,
                cwd=args.cwd or REPO_ROOT,
                onejudge_bin=args.onejudge_bin,
                provider=args.provider,
                oneharness_mode=args.oneharness_mode,
                use_llmlint_wrapper=False,
                session=f"check-in-{validated_run_id}-{round_number}",
                labels={
                    **graph_labels(
                        run_id=cast(RunId, validated_run_id),
                        round_number=round_number,
                    ),
                },
                timeout=dispatch_timeout,
            )
            state = heartbeat_state(resolved_channel)
            return report.completed and state is not None and not state["due"]

        proposal_pump = ProposalPump(resolved_channel, validated_run_id, round_number)
        configure_check_in = getattr(proposal_pump, "configure_check_in", None)
        if configure_check_in is not None:
            configure_check_in(run_check_in)
        # llmlint: ignore-end[changed_behavior_has_e2e]
    try:
        result = run_graph(
            graph,
            journal=journal,
            run_id=run_id,
            round_number=round_number,
            already_started=already_started,
            replayed_runs=replayed_runs,
            replayed_order=replayed_order,
            agent_runner=make_dispatch_runner(
                base_path=args.base_config,
                persona_dir=args.persona_dir,
                cwd=args.cwd or REPO_ROOT,
                project_dir=args.project_dir,
                onejudge_bin=args.onejudge_bin,
                provider=args.provider,
                oneharness_mode=args.oneharness_mode,
                timeout=dispatch_timeout,
            ),
            lifecycle_runner=make_repo_runner(
                workspace=Workspace(args.workspace),
                base_path=args.base_config,
                persona_dir=args.persona_dir,
                merge_policy=args.merge_policy,
                merge_method=args.merge_method,
                oneharness_mode=args.oneharness_mode,
                skip_verify=args.skip_verify,
                verify_via_ci=args.verify_via_ci,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                publication_attempts=args.publication_attempts,
                repo_type=args.repo_type,
            ),
            concurrency=args.concurrency,
            proposal_pump=proposal_pump,
            round_budget=args.round_budget,
        )
    finally:
        if proposal_pump is not None:
            proposal_pump.close()

    payload = graph_payload(result, round_number=round_number)
    journal.append(
        "round-finished",
        detail={"state": result.state, "ok": result.ok, "result": cast(Any, payload)},
    )
    rendered = json.dumps(payload, indent=2) if args.format == "json" else result.summary()
    emit(rendered, args.output)
    if run_dir is not None and round_record is not None:
        number, round_dir = round_record
        try:
            from .projection import project_run

            projected = project_run(run_dir / "events.jsonl", cast(RunId, run_id), number)
            if projected.result is None:  # round-finished above makes this an internal invariant
                raise ConfigError("event projection has no terminal result")
            write_result(round_dir, projected.result)
            # llmlint: ignore[changed_behavior_has_e2e] detached launch/report
            # lifecycle is channel-e2e-covered; goal reservation preservation is unit-proven.
            if not (run_dir / "launch.json").exists():
                finish_run(run_dir.name, run_dir)
        except ConfigError as exc:
            print(f"run-plan: could not record run: {exc}", file=sys.stderr)
            return 2
        print_continuation(run_dir.name, number, round_dir, payload, args.runs_dir)
    return EXIT_BY_STATE[result.state]


def print_continuation(
    run_id: str,
    number: int,
    round_dir: Path,
    payload: GraphPayload,
    runs_dir: Path,
) -> None:
    print(
        f"Round {number:02d} recorded -> {round_dir}/  ({status_summary(payload)})",
        file=sys.stderr,
    )
    suffix = "" if runs_dir == Path("runs") else f" --runs-dir {runs_dir}"
    if payload["state"] == "complete":
        print("All nodes are done; there is nothing to iterate.", file=sys.stderr)
        return
    attest = " ".join(
        f"--complete-human {action['ref']}"
        for item in payload["results"].values()
        for action in item.get("human_actions") or []
    )
    if attest:
        print("Complete the human action(s) above, then attest them:", file=sys.stderr)
        print(f"  just next-round {run_id} {attest}{suffix}", file=sys.stderr)
        return
    print("Iterate: write an edits.json (retry/split/add/drop), then", file=sys.stderr)
    print(f"  just next-round {run_id} [edits.json]{suffix}", file=sys.stderr)


def main_repo_plan(argv: list[str] | None = None) -> int:
    """Deprecated repo-plan alias routed through the canonical executor."""
    print(
        "repo-plan: deprecated; `just run-plan` is now the tracked graph executor and "
        "accepts this plan unchanged.",
        file=sys.stderr,
    )
    return main(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
