"""Local verification: run a target repo's *own* quality gate before pushing.

The lifecycle normally proves changes locally with the registered identity gate.
During onboarding, `detect_gate_candidates` ranks native commands and `run_gate`
runs the selected command in a worktree. The gate's own exit code is the verdict —
0 passes, anything else fails, with captured output kept for the report.
"""

# llmlint: ignore-file[changed_behavior_has_e2e] Bazel proves affected execution e2e;
# exact Nx/Turbo/pnpm/Lerna argv variants are deterministic detection units.
# llmlint: ignore-file[modern_domain_modeling] the sentinel is a serialized registry value.

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, TypeGuard

from .coordination import advisory_lock, atomic_json

NOOP_GATE = "<no-op>"

__all__ = [
    "GateAttestation",
    "NOOP_GATE",
    "VerifyResult",
    "detect_gate",
    "detect_gate_candidates",
    "resolve_gate_template",
    "run_gate",
]


@dataclass(frozen=True)
class GateAttestation:
    """Reusable complete-gate identity safe to surface in run telemetry."""

    commit: str
    comparison_remote: str
    comparison_base: str
    comparison_commit: str
    command: tuple[str, ...]
    environment_sha256: str

    @classmethod
    def from_value(cls, value: object) -> GateAttestation | None:
        """Validate the journal representation of the authoritative identity."""
        if not _is_attestation_record(value):
            return None
        return cls(
            commit=value["commit"],
            comparison_remote=value["comparison_remote"],
            comparison_base=value["comparison_base"],
            comparison_commit=value["comparison_commit"],
            command=tuple(value["command"]),
            environment_sha256=value["environment_sha256"],
        )

    def to_record(self) -> _AttestationRecord:
        return _AttestationRecord(
            commit=self.commit,
            comparison_remote=self.comparison_remote,
            comparison_base=self.comparison_base,
            comparison_commit=self.comparison_commit,
            command=list(self.command),
            environment_sha256=self.environment_sha256,
        )


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    command: list[str]
    output: str
    reused: bool = False
    attestation: GateAttestation | None = None

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
    candidates = detect_gate_candidates(project_dir)
    return shlex.split(candidates[0]) if candidates else None


def detect_gate_candidates(project_dir: str | Path) -> list[str]:
    """Return ranked onboarding gate templates, affected commands first."""
    d = Path(project_dir)
    candidates: list[str] = []
    if (d / "nx.json").is_file():
        candidates.append("npx nx affected --target=check --base={base}")
    if (d / "turbo.json").is_file():
        candidates.append('npx turbo run check --filter=..."[{base}]"')
    if (d / "WORKSPACE").is_file() or (d / "WORKSPACE.bazel").is_file():
        candidates.append(
            'sh -c \'targets=$(mktemp); trap "rm -f $targets" EXIT; '
            'bazel-diff --workspacePath . --startingRevision "$1" '
            '--finalRevision HEAD --output "$targets" && '
            "grep -E '^//[A-Za-z0-9_./:+@=-]+$' \"$targets\" | "
            "xargs -r bazel test --' -- {base}"
        )
    if (d / "pnpm-workspace.yaml").is_file():
        candidates.append('pnpm --filter ..."[{base}]" test')
    if (d / "lerna.json").is_file():
        candidates.append("npx lerna run test --since {base}")
    for name in ("justfile", "Justfile", ".justfile"):
        if _justfile_has_recipe(d / name, "check"):
            candidates.append("just check")
            return candidates
    makefile = d / "Makefile"
    if makefile.is_file() and _makefile_has_target(makefile, "check"):
        candidates.append("make check")
        return candidates
    if _npm_has_test(d / "package.json"):
        candidates.append("npm test")
        return candidates
    if (d / "Cargo.toml").is_file():
        candidates.append("cargo test")
        return candidates
    if (d / "pyproject.toml").is_file() or (d / "setup.py").is_file() or (d / "tox.ini").is_file():
        candidates.append("python -m pytest")
    return candidates


def resolve_gate_template(template: str, base: str) -> list[str]:
    """Parse a validated template, then substitute base without shell re-parsing."""
    return [part.replace("{base}", base) for part in shlex.split(template)]


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
    env: dict[str, str] | None = None,
) -> VerifyResult:
    """Run ``command`` in ``project_dir``; reuse an exact successful certification.

    Reuse is deliberately unavailable outside a Git checkout or without the
    resolved comparison remote/base.  The durable record is repository-local
    and binds the verdict to HEAD, comparison identity, and the gate command.
    """
    directory = Path(project_dir)
    context = _attestation_context(directory, command, env)
    if context is not None and _has_attestation(context):
        return VerifyResult(
            ok=True,
            command=command,
            output="gate attestation reused\n",
            reused=True,
            attestation=_public_attestation(context.record),
        )
    try:
        proc = subprocess.run(
            command,
            cwd=str(project_dir),
            text=True,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, **(env or {})},
        )
    except FileNotFoundError:
        return VerifyResult(
            ok=False,
            command=command,
            output=f"gate command not found: {command[0]!r}",
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - timing-dependent
        return VerifyResult(ok=False, command=command, output=f"gate timed out after {timeout}s")
    result = VerifyResult(
        ok=proc.returncode == 0, command=command, output=proc.stdout + proc.stderr
    )
    if result.ok and context is not None:
        _record_attestation(context)
        result = VerifyResult(
            ok=result.ok,
            command=result.command,
            output=result.output,
            attestation=_public_attestation(context.record),
        )
    return result


_ATTESTATION_SCHEMA_VERSION = 1


class _AttestationRecord(TypedDict):
    """Exact inputs covered by one successful complete-gate verdict."""

    commit: str
    comparison_remote: str
    comparison_base: str
    comparison_commit: str
    command: list[str]
    environment_sha256: str


class _AttestationStore(TypedDict):
    """Versioned repository-local collection of reusable gate verdicts."""

    schema_version: int
    attestations: dict[str, _AttestationRecord]


@dataclass(frozen=True)
class _AttestationContext:
    path: Path
    key: str
    record: _AttestationRecord


def _public_attestation(record: _AttestationRecord) -> GateAttestation:
    return GateAttestation(
        commit=record["commit"],
        comparison_remote=record["comparison_remote"],
        comparison_base=record["comparison_base"],
        comparison_commit=record["comparison_commit"],
        command=tuple(record["command"]),
        environment_sha256=record["environment_sha256"],
    )


def _git_value(directory: Path, *args: str) -> str | None:
    proc = subprocess.run(["git", "-C", str(directory), *args], text=True, capture_output=True)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _attestation_context(
    directory: Path, command: list[str], env: dict[str, str] | None
) -> _AttestationContext | None:
    remote = (env or {}).get("ORCHESTRATOR_COMPARISON_REMOTE")
    base = (env or {}).get("ORCHESTRATOR_COMPARISON_BASE")
    head = _git_value(directory, "rev-parse", "HEAD")
    common = _git_value(directory, "rev-parse", "--path-format=absolute", "--git-common-dir")
    valid_comparison = False
    if remote and base:
        checked = subprocess.run(
            ["git", "check-ref-format", f"refs/remotes/{remote}/{base}"],
            text=True,
            capture_output=True,
        )
        valid_comparison = checked.returncode == 0
    comparison = (
        _git_value(directory, "rev-parse", "--verify", f"refs/remotes/{remote}/{base}^{{commit}}")
        if valid_comparison
        else None
    )
    status = subprocess.run(
        ["git", "-C", str(directory), "status", "--porcelain"],
        text=True,
        capture_output=True,
    )
    clean = status.returncode == 0 and not status.stdout
    if (
        not remote
        or not base
        or not valid_comparison
        or head is None
        or common is None
        or comparison is None
        or not clean
    ):
        return None
    environment = json.dumps(sorted((env or {}).items()), separators=(",", ":"))
    record = _AttestationRecord(
        commit=head,
        comparison_remote=remote,
        comparison_base=base,
        comparison_commit=comparison,
        command=list(command),
        environment_sha256=hashlib.sha256(environment.encode()).hexdigest(),
    )
    key = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return _AttestationContext(
        Path(common) / "ai-orchestrator" / "gate-attestations.json", key, record
    )


def _has_attestation(context: _AttestationContext) -> bool:
    try:
        payload = json.loads(context.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    store = _parse_attestation_store(payload)
    return store is not None and store["attestations"].get(context.key) == context.record


def _is_attestation_record(value: object) -> TypeGuard[_AttestationRecord]:
    if not isinstance(value, dict) or set(value) != _AttestationRecord.__required_keys__:
        return False
    return (
        all(
            isinstance(value[field], str)
            for field in _AttestationRecord.__required_keys__ - {"command"}
        )
        and isinstance(value["command"], list)
        and all(isinstance(part, str) for part in value["command"])
    )


def _parse_attestation_store(value: object) -> _AttestationStore | None:
    """Validate persisted JSON before it can authorize gate reuse."""
    if not isinstance(value, dict) or value.get("schema_version") != _ATTESTATION_SCHEMA_VERSION:
        return None
    raw_attestations = value.get("attestations")
    if not isinstance(raw_attestations, dict) or not all(
        isinstance(key, str) and _is_attestation_record(record)
        for key, record in raw_attestations.items()
    ):
        return None
    return _AttestationStore(
        schema_version=_ATTESTATION_SCHEMA_VERSION,
        attestations=raw_attestations,
    )


def _record_attestation(context: _AttestationContext) -> None:
    context.path.parent.mkdir(parents=True, exist_ok=True)
    with advisory_lock(f"gate-attestations:{context.path}"):
        attestations: dict[str, _AttestationRecord] = {}
        try:
            payload = json.loads(context.path.read_text(encoding="utf-8"))
            store = _parse_attestation_store(payload)
            if store is not None:
                attestations = dict(store["attestations"])
        except (OSError, json.JSONDecodeError):
            pass
        attestations[context.key] = context.record
        store = _AttestationStore(
            schema_version=_ATTESTATION_SCHEMA_VERSION,
            attestations=attestations,
        )
        atomic_json(context.path, store)
