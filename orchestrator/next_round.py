"""Guided continuation of a recorded repo-plan run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError
from .lifecycle import main_plan
from .plan import PlanError
from .replan import next_round
from .runs import latest_round, list_runs, load_mapping, validate_run_id, write_next_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive and run the next round from a repo-plan run ledger."
    )
    parser.add_argument("run_id")
    parser.add_argument("edits", type=Path, nargs="?", default=None)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--plan-only", action="store_true")
    args, repo_plan_args = parser.parse_known_args(argv)

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
        plan = next_round(
            load_mapping(round_dir / "plan.json"),
            load_mapping(result_path),
            load_mapping(args.edits) if args.edits else {},
        )
    except (ConfigError, PlanError) as exc:
        print(f"next-round: {exc}", file=sys.stderr)
        return 2

    if not plan["tasks"]:
        print("All nodes are done or dropped; there is nothing to iterate.")
        return 0

    next_number, next_dir = write_next_plan(run_dir, plan)
    plan_path = next_dir / "plan.json"
    if args.plan_only:
        print(f"Round {next_number:02d} plan written -> {plan_path}")
        print(f"Run: just repo-plan {plan_path} --run {run_id} --runs-dir {args.runs_dir}")
        return 0

    return main_plan(
        [str(plan_path), "--run", run_id, "--runs-dir", str(args.runs_dir), *repo_plan_args]
    )


def main_runs(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List recorded repo-plan runs.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    rows = list_runs(args.runs_dir)
    if not rows:
        print("No recorded runs.")
        return 0
    for run_id, number, summary in rows:
        print(f"{run_id}  round-{number:02d}  ({summary})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
