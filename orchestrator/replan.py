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
from .lifecycle import parse_repo_plan

__all__ = ["next_round"]


def next_round(
    prev_plan: dict[str, Any],
    prev_result: dict[str, Any],
    edits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the next round's repo-plan mapping.

    ``prev_plan``: the prior repo-plan mapping. ``prev_result``: the ``--format
    json`` output of ``repo-plan`` (its ``results[id].status`` drives carry-over).
    ``edits``: ``{retry: {id: {overrides}}, split: {id: [nodes]}, add: [nodes],
    drop: [ids]}``. The result is validated via `parse_repo_plan`.
    """
    edits = edits or {}
    retry = edits.get("retry") or {}
    split = edits.get("split") or {}
    add = edits.get("add") or []
    drop = set(edits.get("drop") or [])

    results = prev_result.get("results") or {}
    done_ids = {
        nid for nid, r in results.items() if isinstance(r, dict) and r.get("status") == "done"
    }
    removed = drop | set(split)  # split replaces a node → its id goes away
    prior_tasks = {
        task.get("id"): task
        for task in prev_plan.get("tasks") or []
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }

    def _anchor(nid: str) -> dict[str, Any] | None:
        item = results.get(nid)
        source = prior_tasks.get(nid)
        if not isinstance(item, dict) or not isinstance(source, dict):
            return None
        outcome = item.get("outcome")
        branch = item.get("branch")
        root = item.get("base_branch")
        pr_base = item.get("pr_base")
        if outcome == "pr-open":
            landed = branch
        elif outcome == "merged" and isinstance(pr_base, str) and pr_base != root:
            landed = pr_base
        else:
            return None
        if not isinstance(landed, str) or not landed:
            return None
        return {
            "branch": landed,
            "repo": item.get("repo") or source.get("repo"),
            "identity": item.get("publication_identity"),
            "base_branch": root,
            "pr": item.get("pr"),
        }

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
                    anchors.append(anchor)
            if anchors:
                node["stack_bases"] = anchors
            node["deps"] = [d for d in previous_deps if d in kept_ids]

    plan: dict[str, Any] = {"concurrency": prev_plan.get("concurrency", 4), "tasks": next_tasks}
    if next_tasks:
        parse_repo_plan(plan)  # bad edits (duplicate ids, cycles, missing fields) fail loudly
    return plan


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
