"""Guided, non-streaming view of one tracked run's recorded results."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError
from .ids import GraphId
from .runs import as_result_payload, load_mapping, rounds, validate_run_id


def render(run: str, runs_dir: Path) -> str:
    """Render the latest completed round; node failures remain successful viewing."""
    run_id = validate_run_id(run)
    run_dir = runs_dir / run_id
    completed = [item for item in rounds(run_dir) if (item[1] / "result.json").is_file()]
    if not completed:
        raise ConfigError(f"no completed round for run {run!r} under {runs_dir}")
    number, round_dir = completed[-1]
    payload = as_result_payload(load_mapping(round_dir / "result.json"))
    lines = [f"Run {run_id} round-{number:02d} — {payload['state']}"]
    for node, item in payload["results"].items():
        outcome = (
            item.get("outcome")
            or item.get("error")
            or ("completed" if item.get("completed") else "-")
        )
        lines.append(f"{node}  {item['status']}  {outcome}")
        detail_id = GraphId(str(run_id), number, node)
        lines.append(f"  Detail: just history-show {detail_id} --runs-dir {runs_dir}")
        failed = item["status"] in {"failed", "blocked", "cancelled"} or item.get("ok") is False
        if failed:
            paths = list(item.get("artifacts", {}).values())
            for step in item.get("steps", []):
                paths.extend(step.get("artifacts", {}).values())
            if paths:
                lines.append("  Full logs:")
                lines.extend(f"    {path}" for path in dict.fromkeys(paths))
            else:
                lines.append("  Full logs: unavailable (node recorded no artifacts)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show node outcomes and full failure logs.")
    parser.add_argument("run")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    try:
        print(render(args.run, args.runs_dir))
    except (ConfigError, OSError) as exc:
        print(f"results: {exc}", file=sys.stderr)
        return 2
    return 0
