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
import sys
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import BASE_CONFIG, PERSONA_DIR, REPO_ROOT
from .config import ConfigError, load_yaml
from .dispatch import Report, dispatch


class PlanError(Exception):
    """The plan file is malformed, references unknown deps, or is cyclic."""


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


@dataclass
class Plan:
    tasks: list[PlanNode]
    concurrency: int = 4


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

    status: str  # "done" | "failed" | "skipped"
    error: str | None = None
    payload: Any = None


def schedule_dag(
    node_ids: list[str],
    deps: dict[str, list[str]],
    run_one: Callable[[str], NodeRun],
    *,
    concurrency: int,
) -> tuple[dict[str, NodeRun], list[str]]:
    """Run a DAG of nodes concurrently, honoring deps and cascading failures.

    A node runs once every dep is ``done``; if any dep ``failed``/``skipped`` the
    node is ``skipped`` (the failure cascades). `run_one` returns the node's
    `NodeRun`; if it raises, the node is ``failed`` with the exception text. All
    ready nodes are submitted to a `concurrency`-worker pool, which is what bounds
    parallelism. Returns each node's `NodeRun` plus the order nodes were started.

    This is the shared engine under `run_plan` (onejudge dispatch) and
    `lifecycle.run_repo_plan` (full repo lifecycle) — one scheduler, two payloads.
    """
    status = {nid: "pending" for nid in node_ids}
    results: dict[str, NodeRun] = {}
    started_order: list[str] = []

    def resolve_skips() -> None:
        changed = True
        while changed:
            changed = False
            for nid in node_ids:
                if status[nid] == "pending" and any(
                    status[d] in ("failed", "skipped") for d in deps[nid]
                ):
                    status[nid] = "skipped"
                    results[nid] = NodeRun("skipped", "a dependency did not complete")
                    changed = True

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures: dict[Any, str] = {}
        while any(s in ("pending", "running") for s in status.values()):
            resolve_skips()
            for nid in node_ids:
                if status[nid] == "pending" and all(status[d] == "done" for d in deps[nid]):
                    status[nid] = "running"
                    started_order.append(nid)
                    futures[pool.submit(run_one, nid)] = nid
            if not futures:
                break
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for fut in done:
                nid = futures.pop(fut)
                try:
                    run = fut.result()
                except Exception as exc:  # a runner failure is a node failure, not a crash
                    run = NodeRun("failed", str(exc))
                status[nid] = run.status
                results[nid] = run

    return results, started_order


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
        persona = t.get("persona")
        if not isinstance(persona, str) or not persona:
            raise PlanError(f"task {nid!r} needs a 'persona'")
        task_text = t.get("task")
        if not isinstance(task_text, str) or not task_text.strip():
            raise PlanError(f"task {nid!r} needs a non-empty 'task'")
        deps = t.get("deps", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise PlanError(f"task {nid!r} 'deps' must be a list of ids")
        nodes[nid] = PlanNode(
            id=nid,
            persona=persona,
            task=task_text,
            deps=list(deps),
            session=t.get("session"),
            project_dir=t.get("project_dir"),
            max_turns=t.get("max_turns"),
            done_when=t.get("done_when"),
        )

    for nid, node in nodes.items():
        for dep in node.deps:
            if dep not in nodes:
                raise PlanError(f"task {nid!r} depends on unknown task {dep!r}")
            if dep == nid:
                raise PlanError(f"task {nid!r} depends on itself")
    _topological_order(nodes)  # raises on a cycle

    return Plan(tasks=list(nodes.values()), concurrency=concurrency)


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
        return NodeRun("failed", "did not complete (hit the turn cap)", report)

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
    timeout: float | None,
) -> Callable[[PlanNode], Report]:
    """Build the production runner that dispatches each node through onejudge."""

    def runner(node: PlanNode) -> Report:
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
            timeout=timeout,
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
        choices=["read-only", "plan", "default", "edit", "auto", "bypass"],
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
