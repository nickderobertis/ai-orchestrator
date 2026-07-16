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
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import ConfigError, load_yaml
from .runs import StackBasePayload

__all__ = ["next_round"]


def next_round(
    prev_plan: dict[str, Any],
    prev_result: dict[str, Any],
    edits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the next round's tracked-graph mapping.

    ``prev_plan``: the prior tracked-graph mapping. ``prev_result``: the ``--format
    json`` output of ``run-plan``. ``edits``: ``{retry: {id: {overrides}},
    split: {id: [nodes]}, add: [nodes], drop: [ids], complete_human: [refs]}``.
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
    done_ids.update(ref for ref in completed_humans if "/" not in ref)
    removed = drop | set(split)  # split replaces a node → its id goes away
    prior_tasks: dict[str, Any] = {}
    for task in prev_plan.get("tasks") or []:
        if isinstance(task, dict) and isinstance((tid := task.get("id")), str):
            prior_tasks[tid] = task
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
        _apply_lifecycle_resume(node, results, completed_humans)
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
    if next_tasks:
        parse_graph(plan)  # bad edits (duplicate ids, cycles, missing fields) fail loudly
    return plan


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


def _apply_lifecycle_resume(
    node: dict[str, Any], results: dict[str, Any], completed_humans: set[str]
) -> None:
    nid = node.get("id")
    if not isinstance(nid, str):
        return
    item = results.get(nid)
    if not isinstance(item, dict):
        return
    resume = item.get("resume")
    waiting_steps = item.get("waiting_steps") or []
    completed_steps = sorted(
        ref.split("/", 1)[1] for ref in completed_humans if ref.startswith(f"{nid}/") and "/" in ref
    )
    if item.get("status") != "waiting" or (resume is None and not waiting_steps):
        return
    if not isinstance(resume, dict):
        from .plan import PlanError

        raise PlanError(f"task {nid!r} is waiting but has no valid resume metadata")
    next_resume = dict(resume)
    existing = next_resume.get("completed_steps") or []
    if not isinstance(existing, list):
        from .plan import PlanError

        raise PlanError(f"task {nid!r} resume 'completed_steps' must be a list")
    merged = list(dict.fromkeys([*existing, *completed_steps]))
    next_resume["completed_steps"] = merged
    node["resume"] = next_resume


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
