"""Canonical tracked graph executor.

The graph is a mixed DAG of direct onejudge dispatches, repo lifecycle nodes, and
human actions. Human actions are never inferred by the harness: a ready human
node settles as ``waiting`` and records exactly what it unblocks; downstream work
settles as ``blocked`` until a later recorded round attests completion.
"""
# llmlint: ignore-file[changed_behavior_has_e2e] Crash-between-fsync topology prefixes cannot
# be deterministically induced through the public CLI; strict-prefix rejection is covered at the
# projection boundary, while real CLI e2e covers interruption after durable node start and replay.

from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast

from . import REPO_ROOT
from .config import ConfigError, load_yaml
from .dispatch import Report
from .journal import JournalSink, NodeJournal, NullJournal, open_journal
from .lifecycle import (
    LifecycleResult,
    LifecycleRunner,
    RepoPlanNode,
    add_lifecycle_args,
    combine_stack_bases,
    emit,
    make_repo_runner,
    parse_repo_node,
    result_payload,
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
    schedule_dag,
)
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
    write_result,
)
from .workspace import Workspace

NodeKind = Literal["agent", "human"]
EXIT_BY_STATE = {"complete": 0, "waiting": 1, "failed": 1}

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

    @property
    def human(self) -> bool:
        return self.kind == "human"


@dataclass
class Graph:
    tasks: list[GraphNode]
    concurrency: int = 4


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
        if statuses & {"failed", "skipped"}:
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
    return Graph(tasks=list(nodes.values()), concurrency=concurrency)


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
        return GraphNode(id=nid, kind="human", task=task, deps=list(deps))
    if raw.get("repo") is not None:
        node = parse_repo_node(nid, raw)
        return GraphNode(
            id=nid,
            kind="agent",
            task=node.task or "",
            deps=list(deps),
            repo=node.repo,
            lifecycle=node,
        )
    direct = parse_agent_node(nid, raw)
    return GraphNode(id=nid, kind="agent", task=direct.task, deps=list(deps), direct=direct)


def load_graph(path: str | Path) -> Graph:
    """Load a tracked graph file (JSON or YAML)."""
    try:
        data = load_yaml(path)
    except ConfigError as exc:
        raise PlanError(str(exc)) from exc
    return parse_graph(data)


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
    completed: dict[str, LifecycleResult] = {}
    guard = threading.Lock()

    def settle(nid: str, node: GraphNode, node_log: NodeJournal) -> NodeRun:
        """Run one already-started node to its outcome, journaling how it settled."""
        expects_no_diff = bool(
            (node.direct and node.direct.expects_no_diff)
            or (node.lifecycle and node.lifecycle.expects_no_diff)
        )
        if expects_no_diff:
            node_log.append("node-settled", detail={"status": "done", "outcome": "no-changes"})
            return NodeRun("done", None, "no-changes")
        if node.lifecycle is not None:
            with guard:
                anchors = combine_stack_bases(node.lifecycle, completed)
            result = lifecycle_runner(
                replace(node.lifecycle, stack_bases=anchors),
                journal=node_log,
            )
            if result.waiting:
                node_log.append(
                    "node-settled",
                    detail={"status": "waiting", "outcome": result.outcome},
                )
                return NodeRun("waiting", result.detail, result)
            agent_steps = [step for step in node.lifecycle.steps or [] if not step.human]
            expected_step_no_diff = bool(agent_steps) and all(
                step.expects_no_diff for step in agent_steps
            )
            if result.outcome == "no-changes" and expected_step_no_diff:
                node_log.append("node-settled", detail={"status": "done", "outcome": "no-changes"})
                return NodeRun("done", None, result)
            if not result.ok:
                node_log.append(
                    "node-failed",
                    detail={"outcome": result.outcome, "detail": result.detail},
                )
                return NodeRun("failed", result.detail or result.outcome, result)
            with guard:
                completed[nid] = result
            node_log.append("node-settled", detail={"status": "done", "outcome": result.outcome})
            return NodeRun("done", None, result)
        report = agent_runner(cast(PlanNode, node.direct), labels=node_log.labels)
        if report.completed:
            node_log.append(
                "node-settled",
                detail={"status": "done", "turns": report.assistant_turns},
            )
            return NodeRun("done", None, report)
        node_log.append(
            "node-failed",
            detail={"detail": "hit the turn cap", "turns": report.assistant_turns},
        )
        return NodeRun("failed", "did not complete (hit the turn cap)", report)

    def run_one(nid: str) -> NodeRun:
        node = nodes[nid]
        node_log = NodeJournal(sink=log, node=NodeId(nid), run_id=run_id, round=round_number)
        if node.human:
            node_log.append("human-waiting", detail={"task": first_line(node.task)})
            return NodeRun("waiting", "awaiting human action")
        if nid not in already_started:
            node_log.append(
                "node-started",
                detail={"node_kind": "lifecycle" if node.lifecycle else "direct"},
            )
        try:
            return settle(nid, node, node_log)
        except Exception as exc:
            # `schedule_dag` settles a runner that raised as a failed node, so the
            # ledger records it either way. Journal it here as well: a `node-started`
            # with nothing to close it is how this journal says "still running", and
            # a node that raised is the one thing it is not.
            node_log.append("node-failed", detail={"detail": str(exc), "error": type(exc).__name__})
            raise

    runs, started_order = schedule_dag(list(nodes), deps, run_one, concurrency=conc)
    return _collect(nodes, runs, started_order)


def _collect(
    nodes: dict[str, GraphNode], runs: dict[str, NodeRun], started_order: list[str]
) -> GraphResult:
    dependents: dict[str, list[str]] = {nid: [] for nid in nodes}
    for nid, node in nodes.items():
        for dep in node.deps:
            dependents[dep].append(nid)
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
                if status.get(dep) in ("waiting", "blocked"):
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
            outcome=run.payload if run.payload == "no-changes" else None,
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
    add_lifecycle_args(parser)
    args = parser.parse_args(argv)

    try:
        plan_mapping = load_yaml(args.plan)
        graph = parse_graph(plan_mapping)
        if args.concurrency is not None and args.concurrency < 1:
            raise PlanError("'--concurrency' must be a positive integer")
        run_dir = (
            None
            if args.no_record
            else resolve_run_dir(args.runs_dir, plan_mapping, args.plan, args.run)
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
    if run_dir is not None and round_record is not None:
        run_id = RunId(run_dir.name)
        round_number = round_record[0]
        journal = open_journal(run_dir, run_id, round_number)
        existing_kinds = {event.kind for event in journal.events() if event.round == round_number}
        if "node-added" not in existing_kinds:
            for raw_node in plan_mapping["tasks"]:
                definition = {
                    key: value for key, value in raw_node.items() if key != "deps" or value == []
                }
                journal.append("node-added", detail={"definition": definition})
            for raw_node in plan_mapping["tasks"]:
                for dependency in raw_node.get("deps", []):
                    journal.append(
                        "edge-added",
                        detail={"from": dependency, "to": raw_node["id"]},
                    )
        elif args.recover:
            from .projection import project_run

            replayed = project_run(run_dir / "events.jsonl", run_id, round_number)
            already_started = frozenset(
                node for node, state in replayed.node_states.items() if state == "running"
            )
        journal.append(
            "round-started",
            detail={
                "nodes": len(graph.tasks),
                "concurrency": graph.concurrency,
                "plan": {key: value for key, value in plan_mapping.items() if key != "tasks"},
            },
        )

    dispatch_timeout = args.dispatch_timeout if args.dispatch_timeout is not None else args.timeout
    result = run_graph(
        graph,
        journal=journal,
        run_id=run_id,
        round_number=round_number,
        already_started=already_started,
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
    )

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
            write_result(round_dir, cast(GraphPayload, projected.result))
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
