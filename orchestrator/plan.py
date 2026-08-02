"""Execute a plan (a task DAG) by dispatching onejudge processes in parallel.

A plan is a set of subtasks with dependencies. `run_plan` topologically schedules
it: every subtask whose dependencies have completed runs concurrently (bounded by
`concurrency`), so independent branches go in parallel and a dependent waits only
for what it actually needs. A subtask that does not complete fails its dependents
(they are skipped, not run against a broken precondition).

The dispatch mechanism is injected as `runner`, so the scheduler is unit-tested
deterministically while the e2e path drives the real onejudge CLI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import BASE_CONFIG, PERSONA_DIR, REPO_ROOT
from .cli_contract import ONEHARNESS_MODES
from .config import ConfigError, load_yaml
from .dispatch import Report, dispatch, incomplete_detail


class PlanError(Exception):
    """The plan file is malformed, references unknown deps, or is cyclic."""


#: What a tracked-graph node (or workstream step) is. ``agent`` is the default
#: for backward-compatible plans; ``human`` names action the harness must never
#: infer or execute.
NODE_KINDS = ("agent", "human")
PLAN_SCHEMA_VERSION = 6

#: Heading the planner's carried context is rendered under inside a dispatched task.
NODE_CONTEXT_HEADING = "## Planner context"
#: What the worker is told the section is: observed state from the round that
#: attached it, never a replacement for the criteria the task already states.
NODE_CONTEXT_PREAMBLE = (
    "The planner attached this while an earlier dispatch of this node ran. It reports "
    "state that was observed then — work already finished, findings already open — so "
    "read it before redoing anything. It adds no acceptance criteria: where it and the "
    "task above disagree, the task decides."
)

_CROSS_DAG_DEP = re.compile(r"^run:([A-Za-z0-9][A-Za-z0-9._-]*)#([^#]+)$")


@dataclass(frozen=True)
class CrossDagDependency:
    """A validated wait-only edge into another tracked run."""

    run_id: str
    node_id: str


def parse_cross_dag_dependency(value: str) -> CrossDagDependency | None:
    """Parse an external dependency, rejecting malformed ``run:`` references."""
    match = _CROSS_DAG_DEP.fullmatch(value)
    if match is not None:
        return CrossDagDependency(run_id=match.group(1), node_id=match.group(2))
    if value.startswith("run:"):
        raise PlanError(
            f"malformed cross-DAG dependency {value!r}; expected 'run:<run_id>#<node_id>'"
        )
    return None


def is_cross_dag_dependency(value: str) -> bool:
    """Return whether a dependency is a validated external-run reference."""
    return parse_cross_dag_dependency(value) is not None


@dataclass
class PlanNode:
    id: str
    persona: str
    task: str
    deps: list[str] = field(default_factory=list)
    session: str | None = None
    project_dir: str | None = None
    max_turns: int | None = None
    done_when: str | None = None
    expects_no_diff: bool = False


@dataclass
class Plan:
    tasks: list[PlanNode]
    concurrency: int = 4


class AgentRunner(Protocol):
    """How a scheduler drives one direct agent node.

    ``labels`` locates the dispatch in the tracked graph for oneharness history.
    It is keyword-only with a default because `run_plan` — an untracked DAG with no
    run, round, or node to name — calls this with the node alone.
    """

    def __call__(
        self,
        node: PlanNode,
        *,
        labels: Mapping[str, str] | None = None,
        cancel: threading.Event | None = None,
    ) -> Report: ...


@dataclass
class TaskResult:
    id: str
    status: str  # "done" | "failed" | "skipped"
    report: Report | None = None
    error: str | None = None


@dataclass
class PlanResult:
    results: dict[str, TaskResult]
    started_order: list[str]

    @property
    def ok(self) -> bool:
        return all(r.status == "done" for r in self.results.values())

    def summary(self) -> str:
        lines = [
            f"plan: {'all subtasks completed' if self.ok else 'some subtasks did not complete'}"
        ]
        for nid, r in self.results.items():
            detail = f" ({r.error})" if r.error else ""
            lines.append(f"  {nid}: {r.status}{detail}")
        return "\n".join(lines)


@dataclass
class NodeRun:
    """One node's scheduling outcome: its status and the runner's payload."""

    status: str  # "done" | "failed" | "skipped" | "waiting" | "blocked"
    error: str | None = None
    payload: Any = None
    recorded: Mapping[str, Any] | None = None


_UNMET = ("failed", "skipped")
_GATED = ("waiting", "blocked")


def schedule_dag(
    node_ids: list[str],
    deps: dict[str, list[str]],
    run_one: Callable[[str], NodeRun],
    *,
    concurrency: int,
    actual: Mapping[str, NodeRun] | None = None,
    started_order: list[str] | None = None,
) -> tuple[dict[str, NodeRun], list[str]]:
    """Backward-compatible adapter over the long-lived reconciler."""
    return reconcile_dag(
        lambda: node_ids,
        deps,
        run_one,
        concurrency=concurrency,
        actual=actual,
        started_order=started_order,
    )


def reconcile_dag(
    desired_nodes: Callable[[], list[str]],
    deps: dict[str, list[str]],
    run_one: Callable[[str], NodeRun],
    *,
    concurrency: int,
    actual: Mapping[str, NodeRun] | None = None,
    started_order: list[str] | None = None,
    on_settled: Callable[[str, NodeRun], None] | None = None,
    on_tick: Callable[[], None] | None = None,
    on_reconcile: Callable[[dict[str, str], dict[str, NodeRun]], None] | None = None,
    external_status: Callable[[], Mapping[str, str]] | None = None,
) -> tuple[dict[str, NodeRun], list[str]]:
    """Converge desired nodes against replayed and in-flight actual state.

    A node runs once every dep is ``done``. If any dep failed or was skipped, the
    node is skipped. If a dep is waiting on a human action, or blocked behind one,
    the node is blocked. Failure takes precedence over waiting, so a node gated by
    both waits for every dep to settle and then becomes skipped.

    `run_one` returns the node's `NodeRun`; if it raises, the node is ``failed``
    with the exception text. All ready nodes are submitted to a `concurrency`
    worker pool, which bounds parallelism. Returns each node's `NodeRun` plus the
    order nodes were started.
    """
    desired = desired_nodes()
    results = dict(actual or {})
    status = {nid: results[nid].status if nid in results else "pending" for nid in desired}
    order = list(started_order or [])
    resumable = {nid for nid, state in status.items() if state == "running"}

    def resolve_gated() -> None:
        changed = True
        while changed:
            changed = False
            for nid in desired:
                if status[nid] != "pending":
                    continue
                settled = [status[d] for d in deps[nid]]
                if any(s in ("pending", "running") for s in settled):
                    continue
                if any(s in _UNMET for s in settled):
                    status[nid] = "skipped"
                    results[nid] = NodeRun("skipped", "a dependency did not complete")
                    changed = True
                elif any(s in _GATED for s in settled):
                    status[nid] = "blocked"
                    results[nid] = NodeRun("blocked", "a dependency is awaiting human action")
                    changed = True

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures: dict[Any, str] = {}
        while True:
            if external_status is not None:
                status.update(external_status())
            if on_reconcile is not None:
                on_reconcile(status, results)
            if external_status is not None:
                status.update(external_status())
            desired = desired_nodes()
            for nid in desired:
                status.setdefault(nid, "pending")
            resolve_gated()
            for nid in desired:
                ready = status[nid] == "pending" and all(status[d] == "done" for d in deps[nid])
                if nid in resumable or ready:
                    resumable.discard(nid)
                    status[nid] = "running"
                    if nid not in order:
                        order.append(nid)
                    futures[pool.submit(run_one, nid)] = nid
            if not futures:
                break
            done, _ = wait(futures, timeout=0.05, return_when=FIRST_COMPLETED)
            if on_tick is not None:
                on_tick()
            for fut in done:
                nid = futures.pop(fut)
                try:
                    run = fut.result()
                except Exception as exc:  # a runner failure is a node failure, not a crash
                    run = NodeRun("failed", str(exc))
                status[nid] = run.status
                results[nid] = run
                if on_settled is not None:
                    on_settled(nid, run)

    return results, order


def _topological_order(nodes: dict[str, PlanNode]) -> list[str]:
    """Return ids in dependency order; raise PlanError on a cycle."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in nodes}
    order: list[str] = []

    def visit(nid: str, stack: tuple[str, ...]) -> None:
        if color[nid] == BLACK:
            return
        if color[nid] == GREY:
            cycle = " -> ".join(stack[stack.index(nid) :] + (nid,))
            raise PlanError(f"dependency cycle: {cycle}")
        color[nid] = GREY
        for dep in nodes[nid].deps:
            if not is_cross_dag_dependency(dep):
                visit(dep, stack + (nid,))
        color[nid] = BLACK
        order.append(nid)

    for nid in nodes:
        visit(nid, ())
    return order


def load_plan(path: str | Path) -> Plan:
    """Load and validate a plan file (JSON or YAML); raise PlanError on any issue."""
    try:
        data = load_yaml(path)  # load_yaml also parses JSON (JSON is a YAML subset)
    except ConfigError as exc:
        raise PlanError(str(exc)) from exc

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanError("plan must have a non-empty 'tasks' list")

    concurrency = data.get("concurrency", 4)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise PlanError("'concurrency' must be a positive integer")

    nodes: dict[str, PlanNode] = {}
    for i, t in enumerate(raw_tasks):
        if not isinstance(t, dict):
            raise PlanError(f"task #{i} must be a mapping")
        nid = t.get("id")
        if not isinstance(nid, str) or not nid:
            raise PlanError(f"task #{i} needs a non-empty string 'id'")
        if nid in nodes:
            raise PlanError(f"duplicate task id: {nid!r}")
        nodes[nid] = parse_agent_node(nid, t)

    for nid, node in nodes.items():
        for dep in node.deps:
            external = parse_cross_dag_dependency(dep)
            if external is not None:
                continue
            if dep not in nodes:
                raise PlanError(f"task {nid!r} depends on unknown task {dep!r}")
            if dep == nid:
                raise PlanError(f"task {nid!r} depends on itself")
    _topological_order(nodes)  # raises on a cycle

    return Plan(tasks=list(nodes.values()), concurrency=concurrency)


def parse_agent_node(nid: str, t: dict[str, Any]) -> PlanNode:
    """Validate one direct-agent node into a `PlanNode`."""
    expects_no_diff = _expects_no_diff(nid, t)
    persona = t.get("persona")
    if not expects_no_diff and (not isinstance(persona, str) or not persona):
        raise PlanError(f"task {nid!r} needs a 'persona'")
    task_text = t.get("task")
    if not isinstance(task_text, str) or not task_text.strip():
        raise PlanError(f"task {nid!r} needs a non-empty 'task'")
    deps = t.get("deps", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise PlanError(f"task {nid!r} 'deps' must be a list of ids")
    for dep in deps:
        parse_cross_dag_dependency(dep)
    return PlanNode(
        id=nid,
        persona=persona or "",
        task=task_text,
        deps=list(deps),
        session=t.get("session"),
        project_dir=t.get("project_dir"),
        max_turns=t.get("max_turns"),
        done_when=t.get("done_when"),
        expects_no_diff=expects_no_diff,
    )


def parse_node_context(nid: str, item: Mapping[str, Any]) -> list[str]:
    """Validate a node's planner-supplied context notes.

    The field is the planner's, but it is normally written by the harness rather
    than by hand: a live ``context`` edit appends one note to the running graph, and
    the round transition carries the notes attached during that round onto the
    carried-forward node. It is deliberately *not* gated on the plan's declared
    schema version — a note is attached to a graph mid-round, so refusing it against
    the version that graph was launched with would make a committed edit unreplayable
    rather than protect an old plan from a field it never uses.
    """
    value = item.get("context")
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(note, str) and note.strip() for note in value
    ):
        raise PlanError(f"task {nid!r} 'context' must be a list of non-empty planner notes")
    return list(value)


def compose_task_context(task: str, notes: Sequence[str]) -> str:
    """Render planner context as a trailing section of the task a worker receives.

    Rendering happens where a node is parsed rather than where it is dispatched, so
    every dispatch shape — a direct node, a lifecycle node, each agent step of a
    workstream — delivers the same section without a second rendering path. The
    stored node keeps the notes as data, so composing twice cannot double them.
    """
    if not notes:
        return task
    body = "\n\n".join(note.strip() for note in notes)
    return f"{task.rstrip()}\n\n{NODE_CONTEXT_HEADING}\n\n{NODE_CONTEXT_PREAMBLE}\n\n{body}\n"


def _expects_no_diff(nid: str, item: dict[str, Any], *, step: str | None = None) -> bool:
    """Validate the explicit zero-yield declaration shared by nodes and steps."""
    value = item.get("expects_no_diff", False)
    location = f"task {nid!r}" + (f" step {step!r}" if step is not None else "")
    if not isinstance(value, bool):
        raise PlanError(f"{location} 'expects_no_diff' must be a boolean")
    if not value:
        return False
    incompatible = [field for field in ("persona", "done_when") if field in item]
    if incompatible:
        raise PlanError(
            f"{location} with expects_no_diff cannot set "
            f"{', '.join(map(repr, incompatible))}: no agent or review evidence is dispatched"
        )
    return True


def run_plan(
    plan: Plan,
    runner: Callable[[PlanNode], Report],
    *,
    concurrency: int | None = None,
) -> PlanResult:
    """Schedule the DAG, running ready subtasks concurrently via `runner`.

    A node runs once every dep has status ``done``; if any dep ``failed`` or was
    ``skipped``, the node is skipped (the failure cascades). A node is ``done``
    when its `Report.completed` is true, else ``failed``; a `runner` that raises
    marks the node ``failed`` with the error.
    """
    conc = concurrency if concurrency is not None else plan.concurrency
    nodes = {n.id: n for n in plan.tasks}
    deps = {nid: nodes[nid].deps for nid in nodes}

    def run_one(nid: str) -> NodeRun:
        report = runner(nodes[nid])
        if report.completed:
            return NodeRun("done", None, report)
        return NodeRun("failed", incomplete_detail(report), report)

    runs, started_order = schedule_dag(list(nodes), deps, run_one, concurrency=conc)
    results = {
        nid: TaskResult(
            nid,
            runs[nid].status,
            report=runs[nid].payload if isinstance(runs[nid].payload, Report) else None,
            error=runs[nid].error,
        )
        for nid in nodes
        if nid in runs
    }
    return PlanResult(results=results, started_order=started_order)


def make_dispatch_runner(
    *,
    base_path: str | Path,
    persona_dir: str | Path,
    cwd: str | Path,
    project_dir: str | None,
    onejudge_bin: str,
    provider: str | None,
    oneharness_mode: str | None,
    worker_harness: str | None = None,
    judge_harness: str | None = None,
    timeout: float | None,
) -> AgentRunner:
    """Build the production runner that dispatches each node through onejudge."""

    def runner(
        node: PlanNode,
        *,
        labels: Mapping[str, str] | None = None,
        cancel: threading.Event | None = None,
    ) -> Report:
        return dispatch(
            node.persona,
            node.task,
            base_path=base_path,
            persona_dir=persona_dir,
            session=node.session or node.id,
            project_dir=node.project_dir if node.project_dir is not None else project_dir,
            max_turns=node.max_turns,
            done_when=node.done_when,
            cwd=cwd,
            onejudge_bin=onejudge_bin,
            provider=provider,
            oneharness_mode=oneharness_mode,
            worker_harness=worker_harness,
            judge_harness=judge_harness,
            labels=labels,
            timeout=timeout,
            cancel=cancel,
        )

    return runner


def _render(result: PlanResult, fmt: str) -> str:
    if fmt == "human":
        return result.summary()
    payload = {
        "ok": result.ok,
        "started_order": result.started_order,
        "results": {
            nid: {
                "status": r.status,
                "completed": r.report.completed if r.report else False,
                "exit_code": r.report.exit_code if r.report else None,
                "verdicts": r.report.verdicts if r.report else [],
                "usage": r.report.usage if r.report else {},
                "error": r.error,
            }
            for nid, r in result.results.items()
        },
    }
    return json.dumps(payload, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a plan (task DAG) of onejudge dispatches.")
    parser.add_argument("plan", type=Path, help="plan file (JSON or YAML)")
    parser.add_argument("--base", type=Path, default=BASE_CONFIG)
    parser.add_argument("--persona-dir", type=Path, default=PERSONA_DIR)
    parser.add_argument("--project-dir", default=None, help="default target project dir for nodes")
    parser.add_argument(
        "--concurrency", type=int, default=None, help="override the plan's concurrency"
    )
    parser.add_argument("--cwd", default=None, help="working dir for onejudge (default: repo root)")
    parser.add_argument("--onejudge-bin", default="onejudge")
    parser.add_argument("--provider", default=None, choices=["oneharness", "command", "split"])
    parser.add_argument(
        "--oneharness-mode",
        default=None,
        choices=list(ONEHARNESS_MODES),
        help="approval/sandbox mode for the harness (via ONEHARNESS_MODE); "
        "use 'bypass' where codex's OS sandbox can't run",
    )
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--format", choices=["human", "json"], default="human")
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"run-plan: {exc}", file=sys.stderr)
        return 2

    runner = make_dispatch_runner(
        base_path=args.base,
        persona_dir=args.persona_dir,
        cwd=args.cwd or REPO_ROOT,
        project_dir=args.project_dir,
        onejudge_bin=args.onejudge_bin,
        provider=args.provider,
        oneharness_mode=args.oneharness_mode,
        timeout=args.timeout,
    )
    # run_plan turns a per-node dispatch failure into that node's `failed` status
    # (it never propagates), so there is no DispatchError to catch here.
    result = run_plan(plan, runner, concurrency=args.concurrency)

    rendered = _render(result, args.format)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if result.ok else 1
