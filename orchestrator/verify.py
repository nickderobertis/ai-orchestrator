"""Local verification: run a target repo's *own* quality gate before pushing.

The lifecycle never pushes a change it hasn't first proven locally. Since target
repos differ, `detect_gate` reads the repo to pick its native gate command
(preferring an explicit ``just check``, then Make / npm / cargo / pytest), and
`run_gate` runs it in the worktree. A caller can always override with an explicit
command. The gate's own exit code is the verdict — 0 passes, anything else fails,
with the captured output kept for the report.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["VerifyResult", "detect_gate", "run_gate"]


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    command: list[str]
    output: str

    def tail(self, limit: int = 2000) -> str:
        """The trailing slice of output, for a compact failure report."""
        return self.output[-limit:]


def _justfile_has_recipe(path: Path, recipe: str) -> bool:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            # A recipe header starts at column 0 as ``name:`` / ``name arg:``.
            stripped = line.rstrip()
            if stripped and not line[0].isspace() and re.match(rf"{recipe}\b[^=]*:", stripped):
                return True
    except OSError:
        return False
    return False


def _npm_has_test(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else None
    return isinstance(scripts, dict) and "test" in scripts


def detect_gate(project_dir: str | Path) -> list[str] | None:
    """Best-effort detect the repo's own gate command; None if none is found.

    Order reflects preference: a curated ``just check`` / ``make check`` beats a
    generic per-ecosystem default, which beats nothing.
    """
    d = Path(project_dir)
    for name in ("justfile", "Justfile", ".justfile"):
        if _justfile_has_recipe(d / name, "check"):
            return ["just", "check"]
    makefile = d / "Makefile"
    if makefile.is_file() and _makefile_has_target(makefile, "check"):
        return ["make", "check"]
    if _npm_has_test(d / "package.json"):
        return ["npm", "test"]
    if (d / "Cargo.toml").is_file():
        return ["cargo", "test"]
    if (d / "pyproject.toml").is_file() or (d / "setup.py").is_file() or (d / "tox.ini").is_file():
        return ["python", "-m", "pytest"]
    return None


def _makefile_has_target(path: Path, target: str) -> bool:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(rf"{target}\s*:", line):
                return True
    except OSError:  # pragma: no cover - caller guards with is_file()
        return False
    return False


def run_gate(
    project_dir: str | Path,
    command: list[str],
    *,
    timeout: float | None = None,
) -> VerifyResult:
    """Run ``command`` in ``project_dir``; ok iff it exits 0."""
    try:
        proc = subprocess.run(
            command,
            cwd=str(project_dir),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return VerifyResult(
            ok=False,
            command=command,
            output=f"gate command not found: {command[0]!r}",
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - timing-dependent
        return VerifyResult(ok=False, command=command, output=f"gate timed out after {timeout}s")
    return VerifyResult(ok=proc.returncode == 0, command=command, output=proc.stdout + proc.stderr)
