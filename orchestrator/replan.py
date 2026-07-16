"""Across-round replanning: derive the next repo-plan from the last round's results.

The plan is static *within* a `run_repo_plan` call; adaptivity lives *between*
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
    """Compute the next round's repo-plan mapping.

    ``prev_plan``: the prior tracked-graph mapping. ``prev_result``: the ``--format
    json`` output of ``run-plan``. ``edits``: ``{retry: {id: {overrides}},
    split: {id: [nodes]}, add: [nodes], drop: [ids], complete_human: [refs]}``.
    The result is validated via the canonical graph parser.
    """
    from .graph import parse_graph
    edits = edits or {}
    retry = edits.get("retry") or {}
    split = edits.get("split") or {}
    add = edits.get("add") or []
    drop = set(edits.get("drop") or [])
    completed_humans = _completed_humans(edits)

    results = prev_result.get("results") or {}
    done_ids = {
        nid for nid, r in results.items() if isinstance(r, dict) and r.get("status") == "done"
    }
    done_ids.update(ref for ref in completed_humans if "/" not in ref)
    removed = drop | set(split)  # split replaces a node → its id goes away
    prior_tasks = {
        task.get("id"): task
        for task in prev_plan.get("tasks") or []
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    _validate_completed_humans(results, completed_humans)

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
                if dep in done_ids and (anchor := _anchor(dep)) is not None:
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


def _completed_humans(edits: dict[str, Any]) -> set[str]:
    refs = edits.get("complete_human") or []
    if not isinstance(refs, list) or not all(isinstance(ref, str) and ref for ref in refs):
        from .plan import PlanError

        raise PlanError("'complete_human' must be a list of human task refs")
    if len(set(refs)) != len(refs):
        from .plan import PlanError

        raise PlanError("'complete_human' refs must be unique")
    return set(refs)


def _validate_completed_humans(results: dict[str, Any], refs: set[str]) -> None:
    if not refs:
        return
    from .plan import PlanError

    waiting_refs = {
        action["ref"]
        for item in results.values()
        if isinstance(item, dict)
        for action in item.get("human_actions") or []
        if isinstance(action, dict) and isinstance(action.get("ref"), str)
    }
    unknown = sorted(ref for ref in refs if ref not in waiting_refs)
    if unknown:
        raise PlanError("can only complete recorded waiting human task refs: " + ", ".join(unknown))


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
    if resume is None and not waiting_steps:
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
        description="Derive the next round's repo-plan from the last round's results + edits."
    )
    parser.add_argument("prev_plan", type=Path, help="the prior repo-plan (JSON or YAML)")
    parser.add_argument("prev_result", type=Path, help="repo-plan --format json output")
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
