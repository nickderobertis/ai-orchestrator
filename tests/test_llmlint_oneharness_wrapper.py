"""Command-boundary tests for llmlint's oneharness mode adapter."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

WRAPPER = REPO_ROOT / "scripts" / "llmlint-oneharness.sh"


def _run(tmp_path: Path, *args: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    output = tmp_path / "args"
    oneharness = bin_dir / "oneharness"
    oneharness.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >"$WRAPPER_ARGS"\n', encoding="utf-8")
    oneharness.chmod(0o755)
    proc = subprocess.run(
        [WRAPPER, *args],
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WRAPPER_ARGS": str(output)},
    )
    assert proc.returncode == 0, proc.stderr
    return output.read_text(encoding="utf-8").splitlines()


def test_read_only_judge_uses_container_bypass(tmp_path: Path) -> None:
    assert _run(tmp_path, "run", "--mode", "read-only", "--compact") == [
        "run",
        "--mode",
        "bypass",
        "--compact",
    ]


def test_other_modes_and_arguments_are_unchanged(tmp_path: Path) -> None:
    assert _run(tmp_path, "run", "--mode", "auto", "--prompt", "read-only") == [
        "run",
        "--mode",
        "auto",
        "--prompt",
        "read-only",
    ]
