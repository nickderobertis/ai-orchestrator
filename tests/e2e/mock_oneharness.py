#!/usr/bin/env python3
"""Shared launcher for oneharness's shipped deterministic mock harness."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from waits import timeout as e2e_timeout

#: What this mock writes to stderr when it is killed at the provider barrier. It
#: stands in for the words a real harness leaves behind when it refuses to start,
#: and is what a dispatch's worker-death report has to carry through to its caller.
BARRIER_DEATH_NOTICE = "mock_oneharness: terminated at the provider barrier"


def mock_run_command(
    oneharness_bin: str, *args: str, harnesses: Sequence[str] = ("codex",)
) -> list[str]:
    """Build a ``oneharness run`` command using the shipped mock provider."""
    command = [oneharness_bin, "run"]
    for harness in harnesses:
        command.extend(("--mock-harness", harness))
    return [*command, *args]


def run_mock_oneharness(
    oneharness_bin: str,
    *args: str,
    harnesses: Sequence[str] = ("codex",),
    cwd: Path,
    env: Mapping[str, str],
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run oneharness at the deterministic paid-provider seam."""
    return subprocess.run(
        mock_run_command(oneharness_bin, *args, harnesses=harnesses),
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout or e2e_timeout(60),
    )


def main(argv: list[str]) -> int:
    """Act as a binary for callers that cannot add oneharness CLI arguments."""
    oneharness_bin = os.environ.get("REAL_ONEHARNESS_BIN")
    if not oneharness_bin:
        print("mock_oneharness: REAL_ONEHARNESS_BIN is not set", file=sys.stderr)
        return 2
    if not argv or argv[0] != "run":
        print(f"mock_oneharness: unsupported invocation {argv}", file=sys.stderr)
        return 2
    harnesses = tuple(filter(None, os.environ.get("MOCK_HARNESSES", "codex").split(",")))
    if "--print-command" in argv:
        # The wrapper's streaming-capability probe. The real CLI answers it by
        # validating the invocation and rendering what it *would* spawn — it starts
        # no harness, writes no history and does no work — so this must delegate
        # straight to it. Answering from the mock's own body instead would count as
        # a run wherever runs are counted, and would sit down at the barrier below
        # that only a real turn is meant to reach.
        probe = mock_run_command(oneharness_bin, *argv[1:], harnesses=harnesses)
        return subprocess.run(probe).returncode
    invocation_log = os.environ.get("MOCK_INVOCATION_LOG")
    if invocation_log:
        with Path(invocation_log).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(argv) + "\n")
    barrier = os.environ.get("MOCK_AGENT_BARRIER")
    if barrier:
        descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

        def stop_descendant(_signum: int, _frame: object) -> None:
            descendant.terminate()
            descendant.wait(timeout=2)
            # A real harness that cannot start says so on stderr and exits; that
            # line is the only account a worker dying before its first turn leaves
            # behind, so emit a recognizable one here for the dispatcher to carry.
            print(BARRIER_DEATH_NOTICE, file=sys.stderr, flush=True)
            raise SystemExit(143)

        signal.signal(signal.SIGTERM, stop_descendant)
        Path(barrier).write_text(str(descendant.pid), encoding="utf-8")
        while True:
            time.sleep(0.05)
    completed = subprocess.run(mock_run_command(oneharness_bin, *argv[1:], harnesses=harnesses))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
