"""Read-only view of this whole host: its load, and every dispatch producing it.

`just runs` and `just status` are run-scoped — they answer "what is *this* run
doing". Several planners share this host, and the question that had no view at all
is the one across all of them: what is running right now, whose it is, and which of
it is consuming the machine. A planner needing that has had `ps` and a pattern, which
is how six live dispatches were counted where there were two.

Every row here comes from the dispatch ownership registry
(`orchestrator.dispatches`), so a process appears only when a live dispatcher still
holds the scratch directory whose stamp that process carries. Nothing is recognised
by the shape of its command line, nothing is signalled, and nothing is written.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from pathlib import Path

from .config import ConfigError
from .dispatches import LiveDispatch, live_dispatches, load_averages
from .launch import caller_identity, read_run_owner
from .runs import validate_run_id


def _session(dispatch: LiveDispatch, runs_dir: Path) -> str:
    """Which planner session owns this dispatch's run, in `just runs`' own words.

    The run directory is the authority, exactly as it is for the ownership column of
    `just runs`: the labels a dispatch carries name the launcher kind, and only the
    run's own record names the session. A dispatch with no run — an untracked
    lifecycle task — legitimately has neither.

    The id is validated before it becomes a path component, because it is a *label* a
    dispatched subprocess put in its own environment rather than a value this harness
    read back from the ledger. `orchestrator.status` and `orchestrator.next_round`
    already hold every run id they join on to that domain; one that does not belong to
    it names no run whose owner could be read, so it reports as unknown.
    """
    if dispatch.run_id is None:
        return dispatch.launcher or "unknown"
    try:
        run_id = validate_run_id(dispatch.run_id)
    except ConfigError:
        return "unknown"
    return read_run_owner(runs_dir / run_id).label(caller_identity())


def render(dispatches: Sequence[LiveDispatch] | None, runs_dir: Path, *, now: float) -> str:
    """Render the whole-host view, saying plainly when it could observe nothing."""
    averages = load_averages()
    header = (
        "Load: unavailable on this platform"
        if averages is None
        else f"Load: {averages[0]:.2f} {averages[1]:.2f} {averages[2]:.2f} (1m 5m 15m)"
    )
    if dispatches is None:
        return (
            f"{header}\nLive dispatches: unknown — this host's procfs could not be read, so "
            "no ownership proof is available and nothing may be claimed about what is running."
        )
    if not dispatches:
        return (
            f"{header}\nLive dispatches: none. No process on this host carries a dispatch "
            "ownership stamp whose scratch directory a dispatcher still holds."
        )
    runnable = sum(dispatch.runnable for dispatch in dispatches)
    lines = [
        header,
        f"Live dispatches: {len(dispatches)}, contributing {runnable} runnable process(es)",
    ]
    for dispatch in dispatches:
        lines.append(f"  {dispatch.where()}  [{_session(dispatch, runs_dir)}]")
        lines.append(f"    {dispatch.turn.describe(now=now)}")
        lines.append(
            f"    Load: {dispatch.runnable} runnable of {dispatch.processes} process(es), "
            f"{dispatch.cpu_seconds:.0f}s CPU consumed"
        )
        lines.append(f"    Status directory: {dispatch.status_dir}")
    return "\n".join(lines)


def _json_value(dispatch: LiveDispatch, runs_dir: Path, *, now: float) -> dict[str, object]:
    return {
        "status_dir": str(dispatch.status_dir),
        "run_id": dispatch.run_id,
        "round": dispatch.round,
        "node": dispatch.node,
        "step": dispatch.step,
        "persona": dispatch.persona,
        "session": _session(dispatch, runs_dir),
        "role": dispatch.turn.role,
        "harness": dispatch.turn.harness,
        "turn_age_seconds": dispatch.turn.age(now=now),
        "turn_is_outlier": dispatch.turn.is_outlier(now=now),
        "runnable": dispatch.runnable,
        "processes": dispatch.processes,
        "cpu_seconds": dispatch.cpu_seconds,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show this host's load and every live dispatch producing it."
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument(
        "--scratch-root",
        type=Path,
        default=None,
        help="the scratch root dispatches write into (default: this process's TMPDIR)",
    )
    args = parser.parse_args(argv)
    now = time.time()
    dispatches = live_dispatches(root=args.scratch_root)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "load": load_averages(),
                    "observable": dispatches is not None,
                    "dispatches": [
                        _json_value(dispatch, args.runs_dir, now=now)
                        for dispatch in dispatches or ()
                    ],
                }
            )
        )
        return 0
    print(render(dispatches, args.runs_dir, now=now))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
