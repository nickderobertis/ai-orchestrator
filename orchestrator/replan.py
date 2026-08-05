"""Across-round replanning: derive the next tracked graph from the last round.

The graph is static within a `run-plan` call; adaptivity lives between
rounds. The orchestrator (an agent following AGENTS.md) reads the structured
results of a round — which PRs merged, which failed, the judge verdicts — and
decides how to adjust: retry a failed node with more turns or a different persona,
split a node that proved too big, add follow-up work, or drop something no longer
needed. `next_round` turns those decisions (as data — `PlanEdits`) plus the prior
plan and results into the next round's plan.

Already-merged nodes are **carried out** (done, not re-run); a new/retried node's
dependency on a merged node is dropped as *satisfied* (its predecessor is on the
base branch now). Unresolved nodes are carried forward to retry unless dropped or
replaced by a split. The produced plan is validated, so a bad edit fails loudly.

A carried-forward node keeps its branch pin and resume checkpoint, and the planner
context attached to it while the round ran.

**The plan of record is the graph the round executed, not the file it was launched
with**, so `executed_plan` folds it from the run's journal rather than re-reading
`round-NN/plan.json`. The two guarantees above are why this replaced an earlier
round-scoped rule: a `retry` replacement's id exists only in the executed graph, so
the launch file can neither recognise the merged replacement as done nor keep the
branch pin it carried. docs/orchestration.md has the planner-facing account.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import ConfigError, load_yaml
from .lifecycle import MAX_AUTOMATIC_STEP_RESUMES
from .outcomes import INFRASTRUCTURE_FAILURE_OUTCOME
from .runs import NodeId, RunId, StackBasePayload

__all__ = ["executed_plan", "next_round", "round_context", "round_supersessions"]


def executed_plan(run_dir: Path, round_number: int, launch_plan: dict[str, Any]) -> dict[str, Any]:
    """The graph a round actually ran, folded from its authoritative journal.

    ``launch_plan`` is what the round was *asked* to do — `round-NN/plan.json`, which
    the reconciler never rewrites. It is returned unchanged when the journal cannot be
    folded strictly. That fallback loses nothing a reader would not already be told
    about: a round with no committed edit projects to the same tasks, and one whose
    edits cannot be replayed has a journal that already refuses `run-plan --recover`.
    """
    from .journal import JOURNAL_NAME
    from .projection import ProjectionError, project_run

    try:
        projected = project_run(run_dir / JOURNAL_NAME, RunId(run_dir.name), round_number)
    except (ProjectionError, ConfigError, OSError):
        return launch_plan
    return dict(projected.plan)


def round_supersessions(run_dir: Path, round_number: int) -> dict[NodeId, NodeId]:
    """Nodes a live ``retry`` replaced during one round, mapped to their replacement.

    A live retry does not edit the node in place the way a `next-round` retry does:
    it cancels the original and adds a differently-identified replacement, rewiring
    every dependent onto it. Both therefore appear in the executed graph, and without
    this the superseded original — cancelled, never done — would be carried forward
    and dispatched again beside the replacement that already did its work.

    Read tolerantly, like every other observer of the journal: `read_events` already
    skips junk lines and a torn tail, and an unreadable file reports no supersession
    — which is what a transition that carried none has always done.
    """
    from .journal import JOURNAL_NAME, read_events

    try:
        events = read_events(run_dir / JOURNAL_NAME)
    except OSError:
        return {}
    replaced: dict[NodeId, NodeId] = {}
    for event in events:
        if event.round != round_number or event.kind != "edit-committed":
            continue
        operations = event.detail.get("operations")
        if not isinstance(operations, list):
            continue
        for operation in operations:
            if not isinstance(operation, Mapping) or operation.get("kind") != "retry-requested":
                continue
            nid = operation.get("node")
            detail = operation.get("detail")
            replacement = detail.get("replacement") if isinstance(detail, Mapping) else None
            if isinstance(nid, str) and isinstance(replacement, str) and replacement:
                replaced[NodeId(nid)] = NodeId(replacement)
    return replaced


def round_context(run_dir: Path, round_number: int) -> dict[str, list[str]]:
    """Planner notes committed onto nodes during one round, in submission order.

    Read from the round's committed edits rather than from the live graph's nodes,
    and that is what bounds the accumulation: a note already carried into this
    round's plan was not attached *during* it, so it is not collected again. One
    round's notes therefore travel exactly one transition unless the planner
    attaches them again against what the next round actually shows.

    The journal is read tolerantly, the way every other observer reads it. A round
    that ran without any live edit — or a ledger written before this contract
    existed — simply reports no context, which is what a transition that has always
    carried none should keep doing.
    """
    from .journal import JOURNAL_NAME, read_events

    collected: dict[str, list[str]] = {}
    for event in read_events(run_dir / JOURNAL_NAME):
        if event.round != round_number or event.kind != "edit-committed":
            continue
        operations = event.detail.get("operations")
        if not isinstance(operations, list):
            continue
        for operation in operations:
            if not isinstance(operation, Mapping) or operation.get("kind") != "context-added":
                continue
            nid, detail = operation.get("node"), operation.get("detail")
            note = detail.get("note") if isinstance(detail, Mapping) else None
            if isinstance(nid, str) and isinstance(note, str) and note.strip():
                collected.setdefault(nid, []).append(note)
    return collected


def next_round(
    prev_plan: dict[str, Any],
    prev_result: dict[str, Any],
    edits: dict[str, Any] | None = None,
    *,
    carried_context: Mapping[str, list[str]] | None = None,
    superseded: Mapping[NodeId, NodeId] | None = None,
) -> dict[str, Any]:
    """Compute the next round's tracked-graph mapping.

    ``prev_plan``: the tracked-graph mapping the round *executed* — `executed_plan`,
    not the launch file. ``prev_result``: the ``--format json`` output of
    ``run-plan``. ``edits``: ``{retry: {id: {overrides}}, split: {id: [nodes]}, add:
    [nodes], drop: [ids], complete_human: [refs]}``. ``carried_context``: notes
    attached to nodes during the round that just ran, as `round_context` collects
    them. ``superseded``: nodes a live ``retry`` replaced, as `round_supersessions`
    collects them; one leaves the graph exactly as an explicit ``drop`` would, but only
    when its replacement is present to carry the work — see the note at the removal.
    The result is validated via the canonical graph parser.
    """
    from .graph import parse_graph
    from .plan import PlanError

    edits = edits or {}
    retry = _mapping_edit(edits, "retry")
    if not all(isinstance(nid, str) and isinstance(value, dict) for nid, value in retry.items()):
        raise PlanError("'retry' must map task ids to override mappings")
    split = _mapping_edit(edits, "split")
    if not all(
        isinstance(nid, str)
        and isinstance(nodes, list)
        and all(isinstance(node, dict) for node in nodes)
        for nid, nodes in split.items()
    ):
        raise PlanError("'split' must map task ids to lists of replacement nodes")
    add = edits.get("add")
    add = [] if add is None else add
    if not isinstance(add, list) or not all(isinstance(node, dict) for node in add):
        raise PlanError("'add' must be a list of task mappings")
    raw_drop = edits.get("drop")
    raw_drop = [] if raw_drop is None else raw_drop
    if (
        not isinstance(raw_drop, list)
        or not all(isinstance(nid, str) and nid for nid in raw_drop)
        or len(set(raw_drop)) != len(raw_drop)
    ):
        raise PlanError("'drop' must be a unique list of task ids")
    drop = set(raw_drop)
    completed_humans = _completed_humans(edits)

    results = prev_result.get("results")
    if not isinstance(results, dict):
        raise PlanError("previous result must contain a 'results' mapping")
    done_ids = {
        nid for nid, r in results.items() if isinstance(r, dict) and r.get("status") == "done"
    }
    done_ids.update(
        nid
        for nid, result in results.items()
        if isinstance(result, dict) and result.get("outcome") == INFRASTRUCTURE_FAILURE_OUTCOME
    )
    done_ids.update(ref for ref in completed_humans if "/" not in ref)
    prior_tasks: dict[str, Any] = {}
    for task in prev_plan.get("tasks") or []:
        if isinstance(task, dict) and isinstance((tid := task.get("id")), str):
            prior_tasks[tid] = task
    # A split replaces a node, and so does a live retry: in both the id goes away and
    # the replacement carries the work. A supersession is honoured only when that
    # replacement is actually here to carry it — normally it is, because ``prev_plan``
    # is the graph the round executed, but `executed_plan` falls back to the launch
    # record for a journal it cannot fold and that record predates the replacement.
    # Removing the original against it would drop the node with nothing carrying it.
    removed = (
        drop
        | set(split)
        | {nid for nid, replacement in (superseded or {}).items() if replacement in prior_tasks}
    )
    _validate_completed_humans(prior_tasks, results, completed_humans)

    def _anchor(nid: str) -> StackBasePayload | None:
        item = results.get(nid)
        source = prior_tasks.get(nid)
        if not isinstance(item, dict) or not isinstance(source, dict):
            return None
        outcome = item.get("outcome")
        branch = item.get("branch")
        root = item.get("base_branch")
        pr_base = item.get("pr_base")
        match outcome:
            case "pr-open":
                landed = branch
            case "merged" if isinstance(pr_base, str) and pr_base != root:
                landed = pr_base
            case _:
                return None
        if not isinstance(landed, str) or not landed:
            return None
        anchor: StackBasePayload = {
            "branch": landed,
            "repo": item.get("repo") or source.get("repo"),
            "identity": item.get("publication_identity"),
            "base_branch": root,
            "pr": item.get("pr"),
            "pr_base": pr_base,
        }
        return anchor

    def _anchors_through(nid: str, seen: frozenset[str] = frozenset()) -> list[StackBasePayload]:
        """Carry unresolved publication ancestry through removed non-repo gates."""
        if nid in seen:
            return []
        if anchor := _anchor(nid):
            return [anchor]
        item = results.get(nid)
        if (
            isinstance(item, dict)
            and item.get("status") == "done"
            and item.get("outcome") == "merged"
            and item.get("pr_base") == item.get("base_branch")
        ):
            return []
        source = prior_tasks.get(nid)
        if not isinstance(source, dict):
            return []
        deps = source.get("deps") or []
        if not isinstance(deps, list):
            return []
        found: list[StackBasePayload] = []
        for dep in deps:
            if isinstance(dep, str):
                found.extend(_anchors_through(dep, seen | {nid}))
        return found

    next_tasks: list[dict[str, Any]] = []
    kept_ids: set[str] = set()

    def _emit(node: dict[str, Any]) -> None:
        next_tasks.append(dict(node))
        nid = node.get("id")
        if isinstance(nid, str):
            kept_ids.add(nid)

    # Carry forward unresolved prior nodes (merged ones are done; dropped/split gone).
    for task in prev_plan.get("tasks") or []:
        tid = task.get("id")
        if tid in done_ids or tid in removed:
            continue
        node = dict(task)
        if tid in retry:
            node.update(retry[tid])
        _carry_context(
            node,
            tid,
            carried_context or {},
            stated=tid in retry and "context" in retry[tid],
        )
        if not _apply_lifecycle_resume(
            node,
            results,
            completed_humans,
            retry_requested=tid in retry,
            source_round=prev_result.get("round"),
        ):
            continue
        _emit(node)

    for subs in split.values():  # replacement subnodes for split nodes
        for sub in subs:
            _emit(sub)
    for node in add:  # brand-new work
        _emit(node)

    # A dep that is no longer in the round is satisfied (merged) or intentionally
    # gone — drop it so the node runs against the updated base.
    for node in next_tasks:
        if "deps" in node:
            previous_deps = node["deps"]
            anchors = list(node.get("stack_bases") or [])
            for dep in previous_deps:
                if dep in kept_ids:
                    continue
                for anchor in _anchors_through(dep):
                    anchors = [
                        existing
                        for existing in anchors
                        if existing.get("branch") != anchor["branch"]
                    ]
                    anchors.append(anchor)
            if anchors:
                node["stack_bases"] = anchors
            node["deps"] = [d for d in previous_deps if d in kept_ids]

    plan: dict[str, Any] = {"concurrency": prev_plan.get("concurrency", 4), "tasks": next_tasks}
    if "schema_version" in prev_plan:
        plan["schema_version"] = prev_plan["schema_version"]
    if next_tasks:
        parse_graph(plan)  # bad edits (duplicate ids, cycles, missing fields) fail loudly
    return plan


def _carry_context(
    node: dict[str, Any], nid: object, attached: Mapping[str, list[str]], *, stated: bool
) -> None:
    """Replace a carried node's planner context with what this round attached.

    Replace, never append. The notes report state the planner observed while a
    particular round ran — what was already finished, which finding was still open —
    and that reading is stale the moment the next attempt moves. Accumulating them
    would hand round five a stack of four obsolete descriptions of the same node and
    ask a worker to reconcile them; carrying only the newest set means a note the
    planner still means is a note the planner attached again.

    A ``retry`` override that states ``context`` wins, including an empty list: the
    planner writing the field at the boundary is a decision made after reading the
    result, and collection must not overrule it.
    """
    if stated:
        return
    notes = attached.get(nid) if isinstance(nid, str) else None
    if notes:
        node["context"] = list(notes)
    else:
        node.pop("context", None)


def _mapping_edit(edits: dict[str, Any], field: str) -> dict[Any, Any]:
    from .plan import PlanError

    value = edits.get(field)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PlanError(f"'{field}' must be a mapping")
    return value


def _completed_humans(edits: dict[str, Any]) -> set[str]:
    refs = edits.get("complete_human") or []
    if not isinstance(refs, list) or not all(isinstance(ref, str) and ref for ref in refs):
        from .plan import PlanError

        raise PlanError("'complete_human' must be a list of human task refs")
    if len(set(refs)) != len(refs):
        from .plan import PlanError

        raise PlanError("'complete_human' refs must be unique")
    return set(refs)


def _validate_completed_humans(
    prior_tasks: dict[str, Any], results: dict[str, Any], refs: set[str]
) -> None:
    if not refs:
        return
    from .plan import PlanError

    human_refs = {
        ref
        for nid, task in prior_tasks.items()
        if isinstance(task, dict)
        for ref in _node_human_refs(nid, task)
    }
    waiting_refs = {
        action["ref"]
        for nid, item in results.items()
        if isinstance(item, dict) and item.get("status") == "waiting"
        for action in item.get("human_actions") or []
        if isinstance(action, dict)
        and isinstance(action.get("ref"), str)
        and (action["ref"] == nid or action["ref"].startswith(f"{nid}/"))
        and action["ref"] in human_refs
    }
    unknown = sorted(ref for ref in refs if ref not in waiting_refs)
    if unknown:
        raise PlanError("can only complete recorded waiting human task refs: " + ", ".join(unknown))


def _node_human_refs(nid: str, task: dict[str, Any]) -> set[str]:
    if task.get("kind", "agent") == "human":
        return {nid}
    steps = task.get("steps") or []
    if not isinstance(steps, list):
        return set()
    return {
        f"{nid}/{step['id']}"
        for step in steps
        if isinstance(step, dict)
        and step.get("kind", "agent") == "human"
        and isinstance(step.get("id"), str)
    }


#: How many rounds the harness continues one preserved lifecycle branch on its own
#: before leaving the node to the planner. Continuation exists so a workstream that
#: ran out of turns picks up where it stopped, not as a policy for work that cannot
#: finish; unbounded it became the latter. It is the same budget the lifecycle gives a
#: step within one round, and taken from there: the two bound the same patience at
#: different scales, so a change of heart about one is a change of heart about both.
MAX_AUTOMATIC_ROUND_RESUMES = MAX_AUTOMATIC_STEP_RESUMES


def _spent_attempts(nid: str, node: dict[str, Any]) -> int:
    """Automatic continuations this node already carries; reject an unusable count.

    Read from the node as the *previous plan* recorded it, not from the round's
    result. A lifecycle builds a fresh continuation for whatever it preserved and
    has no reason to know how many rounds preceded it, so a count taken from the
    result would reset to zero every round and bound nothing. The prior plan is
    this function's own output from last round, which is exactly the tally.

    That plan is external input: `replan` reads a prior one straight off disk, and a
    planner may hand-write or hand-edit it. So a count that is present but is not a
    whole non-negative number is malformed input rather than an absent tally, and
    reading it as zero would restore a full continuation budget every round — the
    unbounded redispatch of one preserved branch that the budget exists to stop.
    Refuse it in the same terms the lifecycle plan parser refuses the same field.

    Called only where the tally is about to be trusted. The paths that instead
    discard it — an explicit branch, or a waiting round, which resets the count by
    design — need no verdict on a value nothing will read; a malformed count that
    survives into the emitted plan is refused by the plan parser on the way out.
    """
    resume = node.get("resume")
    if not isinstance(resume, dict) or "attempts" not in resume:
        return 0
    spent = resume["attempts"]
    if not isinstance(spent, int) or isinstance(spent, bool) or spent < 0:
        from .plan import PlanError

        raise PlanError(f"task {nid!r} resume 'attempts' must be a non-negative integer")
    return spent


def _apply_lifecycle_resume(
    node: dict[str, Any],
    results: dict[str, Any],
    completed_humans: set[str],
    *,
    retry_requested: bool = False,
    source_round: object = None,
) -> bool:
    """Attach continuation metadata; return whether the node runs again at all.

    ``False`` settles the node out of the next round the way an explicit ``drop``
    would — its dependents lose the dependency and its publication ancestry is
    carried through by `_anchors_through`, exactly as for any other removed node.
    That is reserved for one case: a preserved branch the harness has already
    continued to its budget. The failed result from the round that exhausted it
    stands for the planner to act on, its branch is still recoverable with
    ``just repo-recover``, and an explicit ``retry`` starts the budget over — so the
    decision to keep going stays the planner's rather than being taken by default.
    """
    nid = node.get("id")
    if not isinstance(nid, str):
        return True
    item = results.get(nid)
    if not isinstance(item, dict):
        return True
    status = item.get("status")
    # An explicit branch is an intentional routing decision for a preserved
    # attempt. It may pin a known branch or opt out by naming a fresh branch. A
    # waiting workstream is not choosing a branch, so it keeps the pause
    # metadata that carries its already completed steps.
    if "branch" in node and status != "waiting":
        node.pop("resume", None)
        return True
    resume = item.get("resume")
    waiting_steps = item.get("waiting_steps") or []
    prefix = f"{nid}/"
    completed_steps = sorted(
        ref.removeprefix(prefix) for ref in completed_humans if ref.startswith(prefix)
    )
    retrying_preserved = retry_requested and status == "failed"
    continuing_preserved = status == "failed" and resume is not None
    # A parked node is carried forward still parked, so nothing redispatches it here.
    # It carries its checkpoint anyway, because that is what a later `requeue` has to
    # adopt: the branch the cancelled dispatch preserved, not a fresh one. No
    # continuation budget is spent — parking is the planner's decision, not the
    # harness repeating itself.
    parked_preserved = status == "parked" and resume is not None
    if not (
        status == "waiting" or retrying_preserved or continuing_preserved or parked_preserved
    ) or (resume is None and not waiting_steps):
        return True
    if not isinstance(resume, dict):
        from .plan import PlanError

        raise PlanError(f"task {nid!r} has no valid resume metadata")
    next_resume = dict(resume)
    # The tally belongs to the plan, never to the result the lifecycle wrote.
    next_resume.pop("attempts", None)
    # An explicit retry starts over with a full budget: the bound exists to stop the
    # harness repeating itself, not to overrule a decision somebody made after
    # reading the result.
    if continuing_preserved and not retrying_preserved:
        spent = _spent_attempts(nid, node)
        if spent >= MAX_AUTOMATIC_ROUND_RESUMES:
            return False
        next_resume["attempts"] = spent + 1
    if isinstance(source_round, int) and not isinstance(source_round, bool) and source_round > 0:
        next_resume["source_round"] = source_round
    existing = next_resume.get("completed_steps") or []
    if not isinstance(existing, list):
        from .plan import PlanError

        raise PlanError(f"task {nid!r} resume 'completed_steps' must be a list")
    merged = list(dict.fromkeys([*existing, *completed_steps]))
    next_resume["completed_steps"] = merged
    node["resume"] = next_resume
    return True


def _read_mapping(path: Path) -> dict[str, Any]:
    data = load_yaml(path)  # parses JSON too (JSON is a YAML subset)
    return data


def main(argv: list[str] | None = None) -> int:
    from .plan import PlanError

    parser = argparse.ArgumentParser(
        description="Derive the next tracked-graph round from the last result plus edits."
    )
    parser.add_argument("prev_plan", type=Path, help="the prior tracked graph (JSON or YAML)")
    parser.add_argument("prev_result", type=Path, help="run-plan --format json output")
    parser.add_argument(
        "edits", type=Path, nargs="?", default=None, help="edits mapping (retry/split/add/drop)"
    )
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        prev_plan = _read_mapping(args.prev_plan)
        prev_result = _read_mapping(args.prev_result)
        edits = _read_mapping(args.edits) if args.edits else {}
        plan = next_round(prev_plan, prev_result, edits)
    except (ConfigError, PlanError) as exc:
        print(f"replan: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(plan, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
