"""Guided continuation of a recorded tracked-graph run."""

from __future__ import annotations

import argparse
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import ConfigError
from .journal import open_journal
from .plan import PlanError
from .replan import next_round
from .runs import (
    NodeId,
    StepId,
    as_result_payload,
    human_actions,
    latest_round,
    launch_is_active,
    list_runs,
    load_completions,
    load_mapping,
    record_completions,
    status_summary,
    validate_run_id,
    write_next_plan,
    write_result,
)


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="Derive and run the next round from a tracked-graph run ledger.",
        usage="%(prog)s RUN [EDITS.json] [--complete-human REF] [run-plan options]",
    )
    parser.add_argument("run_id")
    parser.add_argument(
        "--complete-human",
        action="append",
        default=[],
        metavar="NODE[/STEP]",
        help="attest one human action recorded waiting in the latest round (repeatable)",
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--plan-only", action="store_true", help="write but do not run next plan")
    edits_path: Path | None = None
    # ``parse_known_args`` cannot know that a forwarded graph flag such as
    # ``--base`` consumes the following token; an optional positional therefore
    # stole that flag's value as the edits path.  The documented command shape is
    # ``next-round RUN [EDITS] [OPTIONS]``, so claim EDITS before parsing options
    # and leave every later unknown token intact for run-plan.
    if len(raw_args) > 1 and not raw_args[1].startswith("-"):
        edits_path = Path(raw_args.pop(1))
    args, repo_plan_args = parser.parse_known_args(raw_args)

    completed_refs: list[str] = []
    try:
        run_id = validate_run_id(args.run_id)
        run_dir = args.runs_dir / run_id
        latest = latest_round(run_dir)
        if latest is None:
            raise ConfigError(f"no recorded rounds for run {run_id!r}")
        number, round_dir = latest
        result_path = round_dir / "result.json"
        if not result_path.exists():
            raise ConfigError(f"latest round has no result yet: {round_dir}")
        result = load_mapping(result_path)
        edits = load_mapping(edits_path) if edits_path else {}
        completed_refs = _completion_refs(edits, args.complete_human)
        _validate_completions(run_dir, result, completed_refs)
        if completed_refs:
            edits = {**edits, "complete_human": completed_refs}
        previous_plan = load_mapping(round_dir / "plan.json")
        plan = next_round(previous_plan, result, edits)
    except (ConfigError, PlanError) as exc:
        print(f"next-round: {exc}", file=sys.stderr)
        return 2

    if completed_refs:
        try:
            record_completions(run_dir, completed_refs, round_number=number)
        except ConfigError as exc:
            print(f"next-round: {exc}", file=sys.stderr)
            return 2
        # Journal the attestation against the round that recorded the wait, so the
        # journal shows who released the block and when, not just that it lifted.
        journal = open_journal(run_dir, run_id, number)
        for ref in completed_refs:
            node, _, step = ref.partition("/")
            journal.append(
                "human-attested",
                node=NodeId(node),
                step=StepId(step) if step else None,
                detail={"ref": ref},
            )

    if not plan["tasks"]:
        if completed_refs:
            next_number, next_dir = write_next_plan(run_dir, plan)
            terminal = _terminal_completion(previous_plan, completed_refs)
            try:
                write_result(next_dir, terminal)
            except ConfigError as exc:
                print(f"next-round: {exc}", file=sys.stderr)
                return 2
            print(
                f"Round {next_number:02d} recorded -> {next_dir}/  "
                f"({status_summary(as_result_payload(terminal))})",
                file=sys.stderr,
            )
        print("All nodes are done or dropped; there is nothing to iterate.")
        return 0

    next_number, next_dir = write_next_plan(run_dir, plan)
    plan_path = next_dir / "plan.json"
    if args.plan_only:
        print(f"Round {next_number:02d} plan written -> {plan_path}")
        print(f"Run: just run-plan {plan_path} --run {run_id} --runs-dir {args.runs_dir}")
        return 0

    from .graph import main as main_graph

    return main_graph(
        [str(plan_path), "--run", run_id, "--runs-dir", str(args.runs_dir), *repo_plan_args]
    )


def _completion_refs(edits: dict[str, Any], cli_refs: list[str]) -> list[str]:
    raw = edits.get("complete_human") or []
    if not isinstance(raw, list) or not all(isinstance(ref, str) and ref for ref in raw):
        raise ConfigError("edits complete_human must be a list of human task refs")
    refs = [*raw, *cli_refs]
    if len(set(refs)) != len(refs):
        raise ConfigError("human task completion refs must be unique")
    return refs


def _terminal_completion(previous_plan: dict[str, Any], refs: list[str]) -> dict[str, Any]:
    """Record top-level humans as done when their attestation ends the graph."""
    completed = {ref for ref in refs if "/" not in ref}
    results = {
        task["id"]: {
            "kind": "human",
            "status": "done",
            "task": task["task"],
            "error": None,
        }
        for task in previous_plan.get("tasks") or []
        if isinstance(task, dict)
        and task.get("kind") == "human"
        and task.get("id") in completed
        and isinstance(task.get("task"), str)
    }
    return {"ok": True, "state": "complete", "started_order": [], "results": results}


def _validate_completions(run_dir: Path, result: dict[str, Any], refs: list[str]) -> None:
    if not refs:
        return
    completed = {item["ref"] for item in load_completions(run_dir)}
    repeated = [ref for ref in refs if ref in completed]
    if repeated:
        raise ConfigError(f"human task(s) already completed: {', '.join(sorted(repeated))}")
    payload = as_result_payload(result)
    waiting = {action["ref"] for action in human_actions(payload)}
    unknown = [ref for ref in refs if ref not in waiting]
    if unknown:
        raise ConfigError(
            "can only complete recorded waiting human task refs: " + ", ".join(sorted(unknown))
        )


def main_runs(argv: list[str] | None = None) -> int:
    from .channel import ChannelError, planner_wait_indicator

    parser = argparse.ArgumentParser(description="List recorded tracked-graph runs.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    rows = list_runs(args.runs_dir)
    active_launches = (
        {
            path.name
            for path in args.runs_dir.iterdir()
            if path.is_dir() and (path / "launch.json").is_file() and launch_is_active(path)
        }
        if args.runs_dir.is_dir()
        else set()
    )
    if not rows and not active_launches:
        print("No recorded runs.")
        return 0
    recorded = {row.run_id for row in rows}
    for run_id in sorted(active_launches - recorded):
        try:
            waiting = planner_wait_indicator(args.runs_dir / run_id / "channel")
        except (ChannelError, ConfigError, OSError):
            waiting = None
        detail = waiting or "orchestrator running"
        print(f"* {run_id}  ACTIVE  ({detail})")
    for run_id, number, summary in rows:
        marker = "* " if run_id in active_launches else "  "
        waiting = None
        if run_id in active_launches:
            with suppress(ChannelError, ConfigError, OSError):
                waiting = planner_wait_indicator(args.runs_dir / run_id / "channel")
        print(f"{marker}{run_id}  round-{number:02d}  ({waiting or summary})")
        print(f"    Results: just results {run_id} --runs-dir {args.runs_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
