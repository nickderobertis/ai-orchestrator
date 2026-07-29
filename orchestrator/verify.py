"""Local verification: run a target repo's *own* quality gate.

The lifecycle no longer calls `run_gate`: a change is proven by the repository's
merge path — its `pre-push` hook, or its required PR status checks — which
dispatch refuses to start without. What remains here is onboarding
(`detect_gate_candidates` ranks native commands so an identity can record its
complete bar) and `just integrate`, whose per-candidate run has no later verifier
because each candidate fast-forwards the local base before the single push. The
gate's own exit code is the verdict — 0 passes, anything else fails, with captured
output kept for the report.

`record_merge_path_verification` is the other half of that move: the merge path
runs the gate, so its evidence has to be captured where it arrives — as `git push`
output — rather than where this module used to produce it.
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
from typing import TYPE_CHECKING, TypedDict, TypeGuard

from .coordination import advisory_lock, atomic_json
from .environment import CHANNEL_ENV_PREFIX
from .redaction import redact

if TYPE_CHECKING:
    # Annotation-and-duck-typing only, and load-bearing: the journal imports the run
    # ledger, which imports `merge`, which imports this module. Nothing here needs
    # the journal at runtime — evidence is appended through the sink the caller
    # injects.
    from .journal import NodeSink

NOOP_GATE = "<no-op>"

__all__ = [
    "GateAttestation",
    "NOOP_GATE",
    "VERIFICATION_TAIL_BYTES",
    "VerifyResult",
    "append_gate_log",
    "detect_gate",
    "detect_gate_candidates",
    "format_merge_path_failure",
    "format_merge_path_record",
    "record_merge_path_failure",
    "record_merge_path_verification",
    "resolve_gate_template",
    "run_gate",
]

#: How much of the merge path's own output travels in a node result and its journal
#: event. The whole run stays in the log the result points at; this is the slice a
#: planner reads without opening it.
VERIFICATION_TAIL_BYTES = 2000


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
    log_path: str | None = None

    def tail(self, limit: int = 2000) -> str:
        """The trailing slice of output, for a compact failure report."""
        return self.output[-limit:]


def format_merge_path_record(*, label: str, command: list[str], ok: bool, output: str) -> str:
    """Render one gated push's evidence, with any credential value stripped."""
    return redact(
        f"merge-path verification: {label}\n"
        f"repository gate: {shlex.join(command)}\n"
        f"verdict: {'passed' if ok else 'FAILED'}\n"
        "--- git push output ---\n" + (output if output.strip() else "<no output>\n")
    )


def append_gate_log(directory: Path, record: str) -> str:
    """Append one record to the node's merge-path log; return its path.

    One file per node, holding every record the merge path produced in order — a
    gated push's verdict, and a publication that failed before any gate ruled.
    Appending rather than overwriting is what keeps that complete: a branch push
    that passed and a publication that did not are both evidence, and the second
    must not erase the first.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / "gate.log").resolve()
    with path.open("a", encoding="utf-8") as stream:
        stream.write(record if record.endswith("\n") else record + "\n")
    return str(path)


def record_merge_path_verification(
    journal: NodeSink,
    *,
    label: str,
    command: list[str],
    ok: bool,
    output: str,
) -> VerifyResult | None:
    """Preserve what the merge path's gate actually did, and where to read it.

    Where a pre-push hook runs the repository's gate, its whole run arrives as
    ``git push`` output. Discarding it on success left a settled run with no
    evidence the gate ran at all; discarding it on failure left the operator
    re-deriving the cause from a one-line rejection.

    ``command`` is the gate that runs *at this push*, so an empty one means no
    gate runs here — the identity is covered by required PR checks, which decide
    later and are recorded by ``pr-checks-observed``. Returns ``None`` there
    rather than calling a bare accepted push a passed verification.
    """
    if not command:
        return None
    record = format_merge_path_record(label=label, command=command, ok=ok, output=output)
    directory = journal.artifact_dir
    log_path = append_gate_log(directory, record) if directory is not None else None
    journal.append(
        "verification-finished",
        detail={
            "label": label,
            "ok": ok,
            "command": list(command),
            "output_tail": record[-VERIFICATION_TAIL_BYTES:],
            **({"log_path": log_path} if log_path else {}),
        },
    )
    return VerifyResult(ok=ok, command=list(command), output=record, log_path=log_path)


def format_merge_path_failure(*, label: str, outcome: str, output: str) -> str:
    """Render a publication attempt that ended before any gate could rule on it."""
    return redact(
        f"merge-path failure: {label}\n"
        f"outcome: {outcome}\n"
        "--- git output ---\n" + (output if output.strip() else "<no output>\n")
    )


def record_merge_path_failure(
    journal: NodeSink, *, label: str, outcome: str, output: str
) -> str | None:
    """Preserve a publication that failed before any gate ruled; return its log path.

    A rejected push has a verdict to record. This is the other half, and the half
    that was silent: a rebuild that lost its base to a concurrent push, ran out of
    disk, or could not build its worktree settled with no output in any log and no
    tail on any event, leaving the operator only the fact that something failed.
    """
    record = format_merge_path_failure(label=label, outcome=outcome, output=output)
    directory = journal.artifact_dir
    log_path = append_gate_log(directory, record) if directory is not None else None
    journal.append(
        "publication-failed",
        detail={
            "label": label,
            "outcome": outcome,
            "output_tail": record[-VERIFICATION_TAIL_BYTES:],
            **({"log_path": log_path} if log_path else {}),
        },
    )
    return log_path


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
    gate_env = {
        key: value
        for key, value in {**os.environ, **(env or {})}.items()
        if not key.startswith(CHANNEL_ENV_PREFIX)
    }
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
            env=gate_env,
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
