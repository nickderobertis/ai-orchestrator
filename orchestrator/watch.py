"""One blocking command that attaches a planner to a live run.

`just orchestrate` launches detached and exits — which is load-bearing, because a
planner supervises several runs at once and a launch that held the terminal could
only ever hold one. The cost is that nothing is attached afterwards unless the
planner composes `just channel-next` with the read-only views, and a planner who
forgets that reconstructs run state from process tables while written status
updates sit unread on the channel.

This is that composition, as one command. It consumes surfaces exactly the way
`channel-next` does — the same `next_surface`, so "attached" cannot come to mean two
different things — and interleaves them with the node transitions `just monitor`
already aggregates. It blocks until the run settles, and its exit code says which
kind of settling happened: 0 when the graph completed, 1 when the run stopped
without completing (an abandoned round, a dead orchestrator, a failed graph), which
is exactly when a planner has something to do.

Replying is still `just channel-reply`: this command reads, and a surface that wants
an answer says so and names that command. Watching must never be the thing that
answers a supervisor boundary on the planner's behalf.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .channel import ChannelError, next_surface
from .config import ConfigError
from .monitor import (
    DEFAULT_HEARTBEAT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RUNS_DIR,
    Heartbeat,
    Monitor,
    MonitorError,
    load_snapshot,
    resolve_run,
)
from .runs import abandoned_launch_indicator, abandoned_round_indicator

#: What a settled run is worth to the caller. A graph that completed is the only
#: clean end; every other way a run stops is one a planner acts on, so it exits
#: non-zero rather than reading as success in a script or a wrapper.
EXIT_COMPLETED = 0
EXIT_STOPPED = 1


def _stamp(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%H:%M:%S")


def _readable(value: str) -> list[str]:
    """The surface body as printable lines, with control characters neutralized.

    A surface message is agent-authored text that reaches an operator's terminal
    verbatim, so an escape sequence in it would be *acted on* rather than read. Line
    breaks are kept — unlike the monitor's one-line summaries, the whole point of
    this view is the update's own content — and every other control character
    becomes a space.
    """
    return [
        "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in line).rstrip()
        for line in (value.splitlines() or [""])
    ]


def surface_lines(value: Mapping[str, Any], *, run_id: str, runs_dir: Path, at: float) -> list[str]:
    """Render one consumed surface, or nothing when the frame carries none."""
    surface = value.get("surface")
    if not isinstance(surface, Mapping):
        return []
    kind = surface.get("kind")
    message = surface.get("message")
    if not isinstance(kind, str) or not isinstance(message, str):
        return []
    # Every surface except an explicitly non-blocking one holds its sender open until
    # the planner answers, so "no reply needed" is the exception that has to be
    # declared rather than the default that is assumed.
    wants_reply = surface.get("blocking", True) is not False
    lines = [
        f"{_stamp(at)}  surface  {kind} ({'reply required' if wants_reply else 'no reply needed'})"
    ]
    lines.extend(f"    {line}" for line in _readable(message))
    options = surface.get("options")
    if isinstance(options, list):
        lines.extend(f"    option: {option}" for option in options if isinstance(option, str))
    if wants_reply:
        lines.append(f"    Reply with: just channel-reply {run_id} --runs-dir {runs_dir}")
    return lines


def watch(
    monitor: Monitor,
    *,
    read_surface: Callable[[], Mapping[str, Any]],
    out: TextIO,
    runs_dir: Path,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    heartbeat: float = DEFAULT_HEARTBEAT,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Follow one run's surfaces and node transitions until it settles."""
    last_output = monitor.clock()

    def emit(*lines: str) -> None:
        nonlocal last_output
        for line in lines:
            print(line, file=out, flush=True)
        if lines:
            last_output = monitor.clock()

    emit(
        f"Watching {monitor.run_id}: surfaces and node transitions as they happen. "
        f"Reply with just channel-reply {monitor.run_id} --runs-dir {runs_dir}."
    )
    reported_failure: str | None = None
    while True:
        for event in monitor.poll():
            emit(event.text())
        settled = False
        try:
            value = read_surface()
        except (ChannelError, ConfigError, OSError) as exc:
            # A run whose channel is absent or unreadable is still worth following for
            # its node transitions, so this degrades to the monitor's own view rather
            # than ending the watch on a source that was never guaranteed. It is
            # reported once per distinct failure: a channel that stays broken would
            # otherwise repeat itself every poll and bury the transitions still
            # arriving underneath.
            failure = f"{type(exc).__name__}: {exc}"
            if failure != reported_failure:
                emit(f"{_stamp(time.time())}  channel  unavailable: {exc}")
                reported_failure = failure
            sleep(poll_interval)
        else:
            reported_failure = None
            emit(*surface_lines(value, run_id=monitor.run_id, runs_dir=runs_dir, at=time.time()))
            settled = value.get("status") == "finished"
        state = monitor.state()
        if state.finished:
            emit(f"{_stamp(time.time())}  --  {monitor.run_id} settled: graph complete")
            return EXIT_COMPLETED
        stopped = abandoned_round_indicator(monitor.run_dir) or abandoned_launch_indicator(
            monitor.run_dir
        )
        if stopped is not None or settled:
            emit(
                f"{_stamp(time.time())}  --  {monitor.run_id} settled without completing: "
                f"{stopped or f'{state.state}: {state.detail}'}"
            )
            return EXIT_STOPPED
        # A run can be legitimately quiet for a long time inside one agent step, and a
        # terminal that says nothing at all is the ergonomic failure this command
        # exists to fix. One state line per silent interval says which run, which
        # round, and — for a launch holding its pid while nothing progresses — that it
        # is parked rather than working.
        now = monitor.clock()
        if now - last_output >= heartbeat:
            emit(Heartbeat(now, state.run_id, state.round, state.state, state.detail).text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Attach to one tracked-graph run: print planner surfaces as they are "
            "queued and node transitions as they happen, and return when the run "
            "settles. Exits 0 only when the graph completed."
        )
    )
    parser.add_argument("run_id", nargs="?", metavar="RUN")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=DEFAULT_HEARTBEAT,
        metavar="SECONDS",
        help=f"seconds of silence before reporting run state (default: {DEFAULT_HEARTBEAT:g})",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        metavar="SECONDS",
        help=f"seconds to wait for each surface before polling again (default: "
        f"{DEFAULT_POLL_INTERVAL:g})",
    )
    args = parser.parse_args(argv)
    for name, value in (("--poll-interval", args.poll_interval), ("--heartbeat", args.heartbeat)):
        if not math.isfinite(value) or value <= 0:
            parser.error(f"{name} must be a positive number of seconds")
    try:
        run_id = resolve_run(args.runs_dir, args.run_id)
    except MonitorError as exc:
        print(f"watch: {exc}", file=sys.stderr)
        return 2
    run_dir = args.runs_dir / run_id
    monitor = Monitor(run_id=run_id, run_dir=run_dir, snapshot=load_snapshot(run_dir))
    try:
        return watch(
            monitor,
            read_surface=lambda: next_surface(run_dir, timeout=args.poll_interval),
            out=sys.stdout,
            runs_dir=args.runs_dir,
            poll_interval=args.poll_interval,
            heartbeat=args.heartbeat,
        )
    except KeyboardInterrupt:
        # Detaching is how a planner ends a watch that is working as designed; the run
        # keeps going, so this is a clean stop rather than a traceback.
        return EXIT_COMPLETED
