#!/usr/bin/env python3
"""Shared launcher for oneharness's shipped deterministic mock harness."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from waits import timeout as e2e_timeout


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
    completed = subprocess.run(mock_run_command(oneharness_bin, *argv[1:], harnesses=harnesses))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
