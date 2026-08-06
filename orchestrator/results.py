"""Guided, non-streaming view of one tracked run's recorded results."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError
from .ids import GraphId
from .runs import as_result_payload, load_mapping, rounds, validate_run_id


def _failure_line(value: object) -> str | None:
    """Name the refusing side and identity for a node the provider turned away.

    Only what the round recorded on the item itself: `judge_unrecorded` is derived
    from the history sessions rather than written here, so it belongs to the views
    that read those — `just status` and the read API — and claiming it from this
    one would be a guess.
    """
    if not isinstance(value, dict):
        return None
    side = value.get("side", "unknown")
    identity = value.get("identity", "unknown")
    cause = str(value.get("cause", "provider failure")).replace("_", " ")
    reset = f", resets {value['reset_time']}" if value.get("reset_time") else ""
    return f"Provider: {side}-side {identity} {cause}{reset}"


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
        if failure := _failure_line(item.get("failure_attribution")):
            lines.append(f"  {failure}")
        # A parked node is idle, not lost: the branch its cancelled dispatch preserved
        # is what a `requeue` resumes and what `just repo-recover` publishes, so the
        # view that reports the park has to name it rather than leave it to be dug out.
        if item["status"] == "parked":
            branch = item.get("branch")
            lines.append(f"  Preserved branch: {branch or 'none (parked before it started)'}")
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
