"""The spike stage of `just plan`: what `scripts/plan.sh` reads and writes between its launches.

The flow is **draft → spikes → finalize → review → design doc**, each launch its own run:
the draft planner as `<name>`, the plan's spikes as `<name>-spikes`, the finalize planner as
`<name>-finalize`, and the design document as `<name>-design` in `scripts/finish-plan.sh`.
Between them sit four questions only this host can answer, each a subcommand here:

* ``prepare <spikes project> <run>`` — whether the draft wrote spikes at all, and if it did,
  readying their project to launch: named for the run it launches as, since the engine mints
  a run id from a project's `onepipeline.name`, and stamped with the ``spikes`` plan kind
  naming exactly its nodes, which is what exempts it from design approval
  (:mod:`orchestrator.design_approval`). It prints the spike node ids, one per line, and
  nothing for a draft that wrote no spikes;
* ``settled <run> [--spikes] [--project <project>]`` — whether a run, launched from that
  project when one is named, settled with every node `done`, and for the
  spikes run every node's outcome `preserved`, read off the run's `result.json`: the ledger
  record the engine rewrites whenever a driver closes out. A node a retry superseded is
  answered for by its replacement, which `tests/run_end_hooks/test_run_end_hooks_e2e.py`
  reads off a ledger the installed engine wrote over a real retry;
* ``branches <run>`` — the branch each spike of that settled spikes run kept, read off
  `onevcs`'s own records of the run (`recoverable --label run=<run>`), never off its journal,
  and refused when one kept none;
* ``note <plan project> <run>`` — the finalize planner's instructions: every spike's report
  and branch, the spikes above each in its stacking chain, and what the plan's budgets
  document owes.

**A spike may build on a spike.** Where several spikes need one measurement harness, the
draft authors it as a harness spike the others depend on, and the engine starts each
dependent's session from the branch its **base dependency** kept: the same-repository spike
dependency whose own stacking chain holds every other. A spike's stacking chain is that
base dependency, then its base dependency, and so on; a dependency on another repository's
spike orders the dependent without carrying its harness, so it is never part of one. The
chains are read from the spikes project's own dependency edges and nowhere else, because a
second copy of the graph would need keeping in step with the first. A spike that failed
skips its dependents, and a retry of it re-points them onto its replacement's branch.

**A retried spike is two names.** The engine retries `spike-x` as `spike-x-2`, so the branch is
the one `onevcs` recorded for the node standing in the ledger, while the report is the one the
spike's task named, `spike-x-report`: each standing node is answered with the spike it is a
retry of, by walking the ledger's `superseded_by` links back to the first.

Each prints what it found on stdout and why it refused on stderr, exiting 1 for a stage the
flow must stop at and 2 for a question it could not ask.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, NewType

from orchestrator import (
    design_approval,
    plan_budgets,
    plan_store,
    project_store,
    spike_branches,
    spike_plan,
)
from orchestrator.plan_store import NodeId, QualifiedProjectId
from orchestrator.spike_plan import Branch

#: Where `onepipeline` keeps its ledger, the same default and override
#: `scripts/plan-brief.sh` reads, resolved against the working directory as it is.
RUNS_ROOT_ENV = "ONEPIPELINE_RUNS_DIR"
DEFAULT_RUNS_ROOT = "runs"

#: What a run id is, as `scripts/plan-brief.sh` holds every run name this flow uses to it: a
#: run id is a directory under the runs root, so one that is not a name is a path elsewhere.
RUN_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*\Z")

#: A run's id, once it is one: a directory name under the runs root.
RunId = NewType("RunId", str)

#: The ledger record a run's settlement is read from, and the words read out of it. A node a
#: retry replaced settles `cancelled` and names its replacement under `superseded_by`, which
#: is what marks it as answered for by that replacement.
RESULT = "result.json"
DONE = "done"
PRESERVED = "preserved"
FAILED = "failed"
SKIPPED = "skipped"
SUPERSEDED_BY = "superseded_by"

#: The project metadata key the engine mints a run id from.
NAME_KEY = "onepipeline.name"


class Stop(Exception):
    """A stage the flow stops at, with the reason it prints."""


class Standing(NamedTuple):
    """A node standing in a run's ledger, and the spike it answers for."""

    #: The node that settled: the spike itself, or the last retry of it.
    node: NodeId
    #: The spike its task was written as: the first of its retries.
    spike: NodeId


def _run_id(run: str) -> RunId:
    if not RUN_ID.fullmatch(run):
        raise ValueError(f"{run!r} is not a run id: one word of letters, digits, '_' and '-'")
    return RunId(run)


def _qualified(project: str) -> QualifiedProjectId:
    """``project``, once it is a qualified id; one that is not names no source to ask."""
    if not design_approval.QUALIFIED.fullmatch(project):
        raise ValueError(f"{project!r} is not a qualified project id, `<source>:<project>`")
    return QualifiedProjectId(project)


def spike_nodes(project: QualifiedProjectId) -> list[NodeId]:
    """The node ids of ``project``'s tasks, or none when the source holds no such project.

    Refused unless ``project`` is a qualified id: one that is not names no source to ask, and
    answering it as absent would send a draft's spikes straight past their launch.
    """
    _qualified(project)
    source = project.partition(":")[0]
    if project not in {str(held.qualified_id) for held in plan_store.read_projects(source)}:
        return []
    return [task.node_id for task in plan_store.read_tasks(project)]


def _repository(task: plan_store.StoreTask) -> str:
    """The repository ``task`` measures, as a normalized origin where it names one."""
    named = task.repositories[0] if task.repositories else task.metadata.get("onepipeline.repo")
    if not isinstance(named, str):
        return ""
    return project_store.hosted_origin(named) or named


def stacking_chains(project: QualifiedProjectId) -> dict[NodeId, tuple[NodeId, ...]]:
    """Each spike of ``project`` that builds on another, with the spikes above it, nearest first.

    Read from the project's own dependency edges. A spike's chain is its base dependency —
    the same-repository dependency whose own chain holds every other same-repository one —
    then that spike's chain; a dependency on another repository's spike orders only and is
    never part of it. A spike whose same-repository dependencies lie on no one chain is one
    the engine refuses to launch, so meeting one here is refused rather than guessed at.
    """
    if project not in {
        str(held.qualified_id) for held in plan_store.read_projects(project.partition(":")[0])
    }:
        return {}
    tasks = {task.node_id: task for task in plan_store.read_tasks(project)}
    chains: dict[NodeId, tuple[NodeId, ...]] = {}

    def chain(node: NodeId, seen: frozenset[NodeId]) -> tuple[NodeId, ...]:
        if node in chains:
            return chains[node]
        if node in seen:
            raise ValueError(f"{project} records a dependency cycle through {node}")
        own = tasks[node]
        candidates = [
            dep for dep in own.deps if dep in tasks and _repository(tasks[dep]) == _repository(own)
        ]
        found: tuple[NodeId, ...] = ()
        for candidate in candidates:
            above = (candidate, *chain(candidate, seen | {node}))
            if set(candidates) <= set(above):
                found = above
                break
        if candidates and not found:
            raise ValueError(
                f"{project} records {node} depending on {', '.join(candidates)} in its own "
                f"repository, which lie on no one stacking chain"
            )
        chains[node] = found
        return found

    for node in tasks:
        chain(node, frozenset())
    return {node: above for node, above in chains.items() if above}


def prepare(project: QualifiedProjectId, run: RunId) -> list[NodeId]:
    """Name and stamp ``project`` for its launch as ``run``, answering its spike nodes.

    The plan's native id goes into the branch template the spikes launch with, so it is
    held, before anything is launched, to the grammar `orchestrator/spike_branches.py` lists
    and discards spike branches by: one a template or a branch pattern would read as syntax
    would cut branches nothing could later find as this plan's.
    """
    named = json.dumps(_run_id(run))
    native = project.partition(":")[2]
    if not native.endswith(spike_plan.SPIKES_SUFFIX):
        raise ValueError(
            f"{project} is not a plan's spikes project, `<plan>{spike_plan.SPIKES_SUFFIX}`"
        )
    spike_branches.plan_id(native.removesuffix(spike_plan.SPIKES_SUFFIX))
    nodes = spike_nodes(project)
    if not nodes:
        return []
    stamp = {design_approval.STAMP_KIND: design_approval.SPIKES, design_approval.STAMP_NODES: nodes}
    client = plan_store.client()
    plan_store.sdk(client.project_metadata_set(project, NAME_KEY, named))
    plan_store.sdk(
        client.project_metadata_set(project, design_approval.PLAN_KIND, json.dumps(stamp))
    )
    return nodes


class LedgerNode(NamedTuple):
    """One node of a run's ledger, in the fields a settlement is read off."""

    id: NodeId
    status: object
    outcome: object
    #: The node that replaced this one, when a retry did.
    superseded_by: NodeId | None


def _ledger(run: RunId) -> tuple[Path, dict[NodeId, LedgerNode]]:
    """``run``'s ledger nodes by id, or :class:`Stop` when it cannot be read node by node.

    A ledger this cannot read node by node is one whose settlement is unknown, and it stops
    the flow like a run that has not settled: passing over a malformed node would let a
    record holding nothing readable read as every node done.
    """
    path = Path(os.environ.get(RUNS_ROOT_ENV) or DEFAULT_RUNS_ROOT) / _run_id(run) / RESULT
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Stop(f"run {run} has not settled: {path} could not be read ({exc})") from exc
    nodes = result.get("nodes") if isinstance(result, dict) else None
    if not isinstance(nodes, list):
        raise Stop(f"run {run} has not settled: {path} records no list of nodes")
    ledger: dict[NodeId, LedgerNode] = {}
    for node in nodes:
        named = node.get("id") if isinstance(node, dict) else None
        if not isinstance(node, dict) or not isinstance(named, str) or not named:
            raise Stop(f"run {run} has not settled: {path} records a node with no id: {node!r}")
        if named in ledger:
            raise Stop(f"run {run} has not settled: {path} records {named} twice")
        replacement = node.get(SUPERSEDED_BY)
        if replacement is not None and (not isinstance(replacement, str) or not replacement):
            raise Stop(
                f"run {run} has not settled: {path} records {named} superseded by "
                f"{replacement!r}, which it holds no node for"
            )
        ledger[NodeId(named)] = LedgerNode(
            NodeId(named),
            node.get("status"),
            node.get("outcome"),
            None if replacement is None else NodeId(replacement),
        )
    return path, ledger


def _first_of(run: RunId, path: Path, ledger: Mapping[NodeId, LedgerNode]) -> dict[NodeId, NodeId]:
    """Each replacement in ``ledger``, by the node it replaced, once the links are one chain each.

    A retry replaces one node with one, so every link names a node the ledger holds, no node is
    named by two, and following them from any node reaches one that stands: a ledger whose
    links do anything else is not one a settlement can be read off.
    """
    first: dict[NodeId, NodeId] = {}
    for node in ledger.values():
        replacement = node.superseded_by
        if replacement is None:
            continue
        if replacement not in ledger:
            raise Stop(
                f"run {run} has not settled: {path} records {node.id} superseded by "
                f"{replacement!r}, which it holds no node for"
            )
        if replacement in first:
            raise Stop(
                f"run {run} has not settled: {path} records {replacement} replacing both "
                f"{first[replacement]} and {node.id}"
            )
        first[replacement] = node.id
    for node in ledger.values():
        seen = {node.id}
        following = node.superseded_by
        while following is not None:
            if following in seen:
                raise Stop(
                    f"run {run} has not settled: {path} records a retry of {node.id} replacing "
                    f"itself"
                )
            seen.add(following)
            following = ledger[following].superseded_by
    return first


#: The run's launch record, which names the project it was launched from.
LAUNCH_RECORD = "launch.json"


def _launched_from(run: RunId, project: QualifiedProjectId) -> None:
    """Stop unless ``run`` was launched from ``project``, as its launch record names it.

    A resume reads a stage's run by its name, and a run of that name launched from any other
    project is not this flow's stage: reading it would let another plan's work stand in.
    """
    path = Path(os.environ.get(RUNS_ROOT_ENV) or DEFAULT_RUNS_ROOT) / _run_id(run) / LAUNCH_RECORD
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Stop(f"run {run} has not settled: {path} could not be read ({exc})") from exc
    launched = record.get("project") if isinstance(record, dict) else None
    if launched != project:
        raise Stop(
            f"run {run} was launched from {launched!r}, not from {project}, so it is not this "
            f"flow's stage; name the flow something no other run has taken"
        )


def settled(
    run: RunId, *, spikes: bool = False, project: QualifiedProjectId | None = None
) -> list[Standing]:
    """``run``'s standing nodes once each settled as the stage needs, or :class:`Stop`.

    With ``project``, ``run`` must also have been launched from that project.
    """
    if project is not None:
        _launched_from(run, project)
    path, ledger = _ledger(run)
    first = _first_of(run, path, ledger)
    answered: list[Standing] = []
    unsettled: list[str] = []
    for node in ledger.values():
        if node.superseded_by is not None:
            continue
        if node.status != DONE:
            unsettled.append(f"{node.id} {node.status}")
        elif spikes and node.outcome != PRESERVED:
            unsettled.append(f"{node.id} done with the outcome {node.outcome}, not {PRESERVED}")
        spike = node.id
        while spike in first:
            spike = first[spike]
        answered.append(Standing(node.id, spike))
    if unsettled:
        raise Stop(
            f"run {run} did not settle with every node {DONE}: {', '.join(unsettled)}"
            + _skipped_behind(run, ledger)
        )
    if not answered:
        raise Stop(f"run {run} has not settled: {path} records no node standing")
    return answered


def _skipped_behind(run: RunId, ledger: Mapping[NodeId, LedgerNode]) -> str:
    """What to do about spikes the engine skipped behind a failed one, or ``""`` for none."""
    standing = [node for node in ledger.values() if node.superseded_by is None]
    failed = [node.id for node in standing if node.status == FAILED]
    skipped = [node.id for node in standing if node.status == SKIPPED]
    if not failed or not skipped:
        return ""
    behind, spike = ("it", "the failed spike") if len(failed) == 1 else ("them", "a failed spike")
    return (
        f". {', '.join(failed)} failed, and the engine skipped {', '.join(skipped)} behind "
        f"{behind} as dependents: retrying {spike} re-runs its dependents on its replacement's "
        f"branch, and once run {run} settles, the flow's `--resume spikes` carries it on"
    )


#: What a name in `onevcs`'s answer may not hold: each is printed in one tab-separated line.
_SPLITS_A_RECORD = re.compile(r"[\t\r\n]")


def branches(run: RunId) -> dict[NodeId, Branch]:
    """The branch each spike of the settled spikes run ``run`` kept, by the spike it is."""
    standing = settled(run, spikes=True)
    listed = json.loads(
        spike_branches.answer(
            [spike_branches.installed_onevcs(), "recoverable"]
            + ["--label", f"run={_run_id(run)}", "--json"]
        )
    )
    if not isinstance(listed, list):
        raise ValueError(f"`onevcs recoverable` answered no list of branches: {listed!r}")
    kept: dict[NodeId, Branch] = {}
    for entry in listed:
        match entry:
            case {"labels": {"node": str() as node}, "branch": {"branch": str() as branch}} if (
                node and branch and not _SPLITS_A_RECORD.search(node + branch)
            ):
                if kept.get(NodeId(node), branch) != branch:
                    raise ValueError(
                        f"`onevcs recoverable` answered two branches for {node}: "
                        f"{kept[NodeId(node)]} and {branch}"
                    )
                kept[NodeId(node)] = Branch(branch)
            case {"labels": dict() as labels, "branch": dict()} if "node" not in labels:
                continue
            case _:
                raise ValueError(
                    f"`onevcs recoverable` answered an entry of no known shape: {entry!r}"
                )
    missing = [one.node for one in standing if one.node not in kept]
    if missing:
        raise Stop(
            f"onevcs records no kept branch of run {run} for {', '.join(missing)}, so there is "
            f"no harness to link; read it with `just recoverable` before finalizing"
        )
    return {one.spike: kept[one.node] for one in standing}


def note(
    project: QualifiedProjectId,
    kept: Mapping[NodeId, Branch],
    above: Mapping[NodeId, Sequence[NodeId]] | None = None,
) -> str:
    """The finalize planner's instructions for ``project``, whose spikes kept ``kept``.

    ``above`` is each spike's stacking chain, as :func:`stacking_chains` reads it; a task
    building on a spike links every spike above it too, because its harness is there.
    """
    if plan_budgets.migrated(_qualified(project)) is not None:
        budgets = (
            "The plan predates budgets: the host's budgets migration list names it, so it "
            "carries no budgets document and this dispatch writes none."
        )
    else:
        budgets = (
            f"Keep the plan's budgets document `{plan_budgets.document_id(project)}` current "
            "with what the spikes found, and state the workload each spike measured at there "
            "and in the criteria of the task that builds on it, never in a budgets file."
        )
    listed = "\n".join(
        f"- `{spike}`: report `{spike_plan.report_id(spike)}`; branch `{branch}`"
        for spike, branch in kept.items()
    )
    stacked = "\n".join(
        f"- `{spike}`: above it, {', '.join(f'`{one}`' for one in chain)}"
        for spike, chain in (above or {}).items()
        if chain
    )
    stacking = (
        "\n\nSome spikes build on others, each starting from the branch the spike above it "
        "kept. A task that builds on such a spike also links, through the same `spikes` "
        "answer, every spike above it in its stacking chain, since that is where its harness "
        f"is. Each spike's stacking chain, nearest first:\n\n{stacked}"
        if stacked
        else ""
    )
    return (
        "## Finalize this plan from its spikes\n\n"
        f"This dispatch is the planning flow's finalize stage. The plan `{project}` was "
        f"drafted from the brief above, and its spikes in "
        f"`{spike_plan.spikes_project(project)}` have run. Read every spike's report — the "
        "document of the plan's own project each one names below — and rework the plan from "
        "all of it: its targets, its decomposition and its contracts. Link each task that "
        "builds on a spike to that spike's report and branch through the task's `spikes` "
        f"answer, regenerating the task rather than editing its body. {budgets} A requested "
        "target the evidence calls infeasible is an escalated exception, never a quietly "
        f"loosened number.\n\nThe spikes, and the branches they kept:\n\n{listed}{stacking}\n"
    )


def main(argv: list[str] | None = None) -> int:
    """The four subcommands `scripts/plan.sh` runs, named in the module docstring."""
    parser = argparse.ArgumentParser(prog="python -m orchestrator.spike_flow")
    verbs = parser.add_subparsers(dest="verb", required=True)
    prepared = verbs.add_parser("prepare")
    prepared.add_argument("project")
    prepared.add_argument("run")
    reading = verbs.add_parser("settled")
    reading.add_argument("run")
    reading.add_argument("--spikes", action="store_true")
    reading.add_argument("--project")
    verbs.add_parser("branches").add_argument("run")
    noted = verbs.add_parser("note")
    noted.add_argument("project")
    noted.add_argument("run")
    args = parser.parse_args(argv)
    try:
        run = _run_id(args.run)
        match args.verb:
            case "prepare":
                print("\n".join(prepare(_qualified(args.project), run)))
            case "settled":
                project = None if args.project is None else _qualified(args.project)
                answered = settled(run, spikes=args.spikes, project=project)
                print("\n".join(one.node for one in answered))
            case "branches":
                for spike, branch in branches(run).items():
                    print(f"{spike}\t{branch}")
            case _:
                plan = _qualified(args.project)
                kept = branches(run)
                chains = stacking_chains(QualifiedProjectId(spike_plan.spikes_project(plan)))
                sys.stdout.write(note(plan, kept, chains))
    except Stop as exc:
        print(f"plan: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, spike_branches.Unlisted) as exc:
        print(f"plan: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
