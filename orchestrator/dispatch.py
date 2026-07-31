"""Dispatch one subtask: merge base ⊕ persona, then run onejudge through its SDK.

`dispatch()` is the single unit of orchestrated work. It builds the effective
onejudge config for a persona, and passes it to the typed Python SDK, which drives
the real CLI and validates its versioned JSON report. The
orchestrator calls this for one-off subtasks; `plan.run_plan` calls it for each
node of a DAG.
"""

# llmlint: ignore-file[changed_behavior_has_e2e] the real just/console launch, detached split
# provider, nested run-plan, worker isolation, and launch failures run e2e; exhaustive malformed
# provider and binary string variants are deterministic pre-launch unit boundary tests.

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NotRequired, Protocol, TypedDict, cast

import yaml
from onejudge_sdk import (
    ContractError,
    OneJudge,
    OneJudgeProcessError,
    OneJudgeTimeoutError,
    RunConfig,
    RunResult,
)

from . import BASE_CONFIG, PERSONA_DIR, REPO_ROOT
from .channel import (
    CHANNEL_DIR_ENV,
    CHANNEL_RUN_ID_ENV,
    DEFAULT_HEARTBEAT_INTERVAL,
    ChannelError,
    create_channel,
)
from .cli_contract import DEFAULT_ONEHARNESS_MODE, ONEHARNESS_MODES, ROUND_BUDGET_OPTION
from .config import ConfigError, build_effective_config, load_yaml
from .coordination import atomic_json
from .goals import Goal, graph_identities, register_run, update_run_owner
from .labels import LABEL_ENV, LabelError, merge_labels, semantic_agent_labels
from .launch import (
    KNOWN_LAUNCHERS,
    LAUNCH_RECORD_NAME,
    LAUNCHER_KINDS,
    LaunchError,
    LaunchInfo,
    generate_launch_id,
    resolve_launcher_kind,
    select_launch,
    validate_session_id,
    write_provenance,
)
from .personas import persona_path
from .redaction import redact
from .runs import ArtifactPaths, resolve_run_dir, slugify
from .scratch import owned_scratch_directory
from .watchdog import (
    OWN_PROCESS_GROUP_FLAG,
    ProcessId,
    process_activity,
    terminate_process_group,
    terminate_processes,
    terminate_tree,
)

# onejudge's own exit codes (see docs/cli.md): 0 completed + boolean evals passed,
# 1 hit the turn cap / a boolean eval failed, 2 bad config or usage.
EXIT_COMPLETED = 0
ONEJUDGE_VERSION_FILE = REPO_ROOT / "config" / "onejudge.version"
EXIT_INCOMPLETE = 1
EXIT_CONFIG_ERROR = 2
# Temporary hard per-turn ceiling for legitimate long-running agents. Dispatch
# inactivity is bounded separately below; issue #6 tracks finer phase budgets.
DEFAULT_ONEHARNESS_TIMEOUT = "10800"
DEFAULT_DISPATCH_STALL_TIMEOUT = "600"
DEFAULT_WORKER_HEARTBEAT_TIMEOUT = "60"
ORCHESTRATOR_ONEHARNESS_TIMEOUT = "86400"
AGENT_ONEHARNESS_BIN = REPO_ROOT / "scripts" / "oneharness-agent.sh"
# The orchestrator role has its own harness order (codex first) and, like the
# worker wrapper, exports the alternate-Claude config indirection its fallback
# variant needs; a raw `oneharness` would discover the worker chain instead.
ORCHESTRATOR_ONEHARNESS_BIN = REPO_ROOT / "scripts" / "oneharness-orchestrator.sh"
#: A dispatch whose budget was spent without the agent producing anything. onejudge
#: counts every turn it attempts, and a provider that accepts a turn and answers with
#: nothing still spends one — so a failing provider drains a 30-turn cap in minutes,
#: at a rate no working agent produces. The accounting is onejudge's and not this
#: harness's to change; what the harness can stop doing is reporting the result as the
#: agent running out of room, because that reading is what earns an identical retry.
DispatchOutcome = Literal["worker-died", "no-agent-progress", "reported-blocker"]
#: Typed by the union rather than by a literal of its own, so a rename that misses one
#: of them stops being a spelling both places agree on and starts being a type error.
NO_AGENT_PROGRESS_OUTCOME: DispatchOutcome = "no-agent-progress"
REPORTED_BLOCKER_OUTCOME: DispatchOutcome = "reported-blocker"
REPORTED_BLOCKER_PREFIX = "terminal blocker reported:"
WatchdogReason = Literal["worker-died", "stalled"]
#: The status files `scripts/oneharness-agent.sh` writes and this module reads —
#: the whole IPC contract between the two. `tests/test_oneharness_agent_wrapper.py`
#: is its drift gate: the wrapper has to name every one of these.
AGENT_PID_NAME = "agent.pid"
AGENT_CHILD_PID_NAME = "agent.child.pid"
AGENT_HEARTBEAT_NAME = "agent.heartbeat"
AGENT_DONE_NAME = "agent.done"
AGENT_FAILED_NAME = "agent.failed"
AGENT_EXIT_CODE_NAME = "agent.exit_code"
AGENT_FAILURE_NAME = "agent.failure"
AGENT_STDERR_NAME = "agent.stderr"
# DRIFT-GATE: test_status_file_contract_has_one_source_the_wrapper_honors parses
# every agent.* path written by scripts/oneharness-agent.sh and rejects names
# absent from AGENT_STATUS_NAMES.
AGENT_STDOUT_NAME = "agent.stdout"
AGENT_STATUS_NAMES = (
    AGENT_PID_NAME,
    AGENT_CHILD_PID_NAME,
    AGENT_HEARTBEAT_NAME,
    AGENT_DONE_NAME,
    AGENT_FAILED_NAME,
    AGENT_EXIT_CODE_NAME,
    AGENT_FAILURE_NAME,
    AGENT_STDERR_NAME,
    AGENT_STDOUT_NAME,
)
#: How much of the dead child's stderr tail a death report carries. The tail is
#: the part that names the failure; the cap keeps one runaway harness from
#: filling a journal entry.
AGENT_STDERR_TAIL_CHARS = 1200
#: Read more raw bytes than the cap so collapsing whitespace still leaves a full
#: tail to trim, without pulling a multi-megabyte harness log into memory.
AGENT_STDERR_READ_BYTES = 8 * AGENT_STDERR_TAIL_CHARS
#: How long any one sentence of reported evidence may be — the wrapper's recorded
#: exit disposition, an unmet verdict's reason, a worker's assessment, a harness
#: stderr line. Each is short by nature, each arrives from somewhere this module
#: does not write, and each lands in a durable node result, so all are bounded and
#: redacted on the same terms.
REPORTED_NOTE_CHARS = 300


class DispatchError(Exception):
    """onejudge could not be run, or rejected the config (a loud failure)."""


class LaunchRecord(TypedDict):
    """Stable planner handoff persisted for one orchestrator launch.

    The ``launch`` link is the non-sensitive half of the provenance scheme: it names
    the ``launch_id`` the read API joins to the out-of-repo provenance record. The
    launcher session id never enters the run directory (see `orchestrator.launch`).
    """

    schema_version: int
    run_id: str
    channel_id: str
    plan_name: str
    commands: dict[str, str]
    goal: Goal | None
    launch: NotRequired[LaunchInfo]


def _launch_provenance(
    *,
    launcher: str | None,
    session_id: str | None,
    repository_identity: str,
) -> tuple[str, dict[str, str]]:
    """Mint a launch id, persist provenance for a known launcher, and build labels.

    Returns the ``launch_id`` and the history labels (``launch_id`` + ``launcher``)
    to stamp on every oneharness invocation this launch makes, so any nested
    worker/judge/orchestrator conversation joins back to its launching session. The
    sensitive session id goes only to the protected out-of-repo provenance record.
    """
    try:
        kind = resolve_launcher_kind(launcher)
        validated_session = validate_session_id(session_id)
    except LaunchError as exc:
        raise DispatchError(str(exc)) from exc
    launch_id = generate_launch_id()
    if kind in KNOWN_LAUNCHERS and validated_session is not None:
        write_provenance(
            launch_id=launch_id,
            launcher=kind,
            launcher_session_id=validated_session,
            repository_identity=repository_identity,
        )
    return launch_id, {"launch_id": launch_id, "launcher": kind}


class _TelemetryResult(Protocol):
    """The additive typed result interface introduced by onejudge 0.3.4."""

    @property
    def telemetry(self) -> dict[str, Any] | None: ...  # pragma: no cover - typing contract


@dataclass(frozen=True)
class FileProgress:
    """A filesystem entry whose changes demonstrate dispatch progress."""

    path: str
    modified_ns: int
    size: int


@dataclass
class Report:
    """The outcome of one dispatched subtask, parsed from onejudge's report."""

    persona: str
    exit_code: int
    completed: bool
    stopped_early: bool
    assistant_turns: int
    verdicts: list[dict[str, Any]]
    usage: dict[str, Any]
    raw: dict[str, Any] | None
    stderr: str
    assessment: str | None = None
    telemetry_data: dict[str, Any] | None = None
    artifacts: ArtifactPaths = field(default_factory=ArtifactPaths)
    outcome: DispatchOutcome | None = None
    #: Why the dispatch ended this way, when the outcome alone cannot say. Set for
    #: ``worker-died`` so a provider or harness failure reads differently from a
    #: worker that simply exited.
    outcome_detail: str | None = None
    #: The turn cap this dispatch asked for. onejudge exits 1 both when a worker
    #: exhausts its turns and when it stops for any other reason, so without the
    #: cap an incomplete run cannot say which of the two it was.
    max_turns: int | None = None

    @property
    def telemetry(self) -> dict[str, Any] | None:
        """Return SDK-validated report-v5 telemetry when the producer supplied it."""
        return dict(self.telemetry_data) if self.telemetry_data is not None else None

    def summary(self) -> str:
        state = "completed" if self.completed else "NOT completed"
        line = f"{self.persona}: {state} ({self.assistant_turns} assistant turn(s))"
        for v in self.verdicts:
            verdict = v.get("verdict", {})
            line += f"\n  - [{v.get('kind')}] {v.get('criterion')}: {verdict.get('value')}"
        if self.assessment:
            line += f"\n  follow-ups: {self.assessment}"
        return line


@dataclass(frozen=True)
class WatchdogSignal:
    """A typed liveness decision and the process identities needed for cleanup."""

    reason: WatchdogReason
    root_pid: ProcessId
    observed_pids: tuple[ProcessId, ...]
    #: What was observable at the point of death. Four different failures reach
    #: this watchdog as one dead tree, so the reason has to be carried explicitly.
    detail: str = ""


class OneJudgeProvenance(TypedDict):
    path: str
    version: str


class DispatchProvenance(TypedDict):
    provider_kind: str
    onejudge: OneJudgeProvenance


def _resolve_onejudge(onejudge_bin: str, env: Mapping[str, str]) -> OneJudgeProvenance:
    """Resolve and verify the executable against the repository's adopted version."""
    resolved = shutil.which(onejudge_bin, path=env.get("PATH"))
    if resolved is None:
        raise DispatchError(f"onejudge binary not found: {onejudge_bin!r} — run 'just bootstrap'")
    resolved = os.path.abspath(resolved)
    adopted = ONEJUDGE_VERSION_FILE.read_text(encoding="utf-8").strip()
    version_result = subprocess.run(
        [resolved, "--version"], text=True, capture_output=True, env=env, check=False
    )
    expected = f"onejudge {adopted}"
    actual = version_result.stdout.strip()
    if version_result.returncode != 0 or actual != expected:
        observed = actual or version_result.stderr.strip() or "<no version output>"
        raise DispatchError(
            f"onejudge version mismatch: expected {expected!r}, got {observed!r} from {resolved}"
        )
    return OneJudgeProvenance(path=resolved, version=adopted)


def _agent_produced_nothing(result: RunResult) -> bool:
    """Whether no assistant turn in this run carried any content.

    Content rather than turn count: onejudge records an empty answer as a turn, so
    the transcript of a failing provider is a full budget of blank turns rather than
    an empty one. Counting turns would see a busy run; reading them sees the truth.
    """
    transcript = result.raw.get("transcript")
    messages = transcript.get("messages") if isinstance(transcript, dict) else None
    if not isinstance(messages, list):
        return False
    return not any(
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and str(message.get("content") or "").strip()
        for message in messages
    )


def _configured_turn_cap(config: Mapping[str, Any]) -> int | None:
    """Read the turn cap out of an effective config, ignoring an unusable value.

    The cap is what tells an incomplete dispatch apart from one that ran out of
    turns, so it is read defensively: a config that does not state one leaves the
    report saying only how far the worker got, which is still true.
    """
    user = config.get("user")
    cap = user.get("max_turns") if isinstance(user, Mapping) else None
    return cap if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 else None


def incomplete_detail(report: Report) -> str:
    """Name why a dispatch ended incomplete, instead of assuming the turn cap.

    onejudge exits 1 both when a worker exhausts its turns and when it stops for
    any other reason, and every incomplete dispatch used to be reported as "hit the
    turn cap" — including runs that ended on turn 1. That sentence sends its reader
    to raise a cap that was never reached, so compare the turns actually taken with
    the cap the dispatch asked for, and when the cap was not reached carry whatever
    account of the stop the run did leave behind.

    Two stops answer ahead of that comparison, because each wants a response the turn
    count cannot suggest. A worker the watchdog saw die leaves the watchdog's account
    and nothing else worth saying. And a budget spent without the agent producing
    anything did reach the cap, but it is not a cap that was too small — retrying it
    unchanged spends the next budget exactly the same way.
    """
    match report.outcome:
        case "worker-died":
            # The watchdog's own account of how the worker died — which pid it was
            # watching, and what it last saw — is the whole diagnosis; the bare
            # outcome name only says that one happened.
            return report.stderr.strip() or "worker-died"
        case "no-agent-progress":
            return (
                "did not complete: the turn budget was spent without the agent "
                "producing anything, so retrying it unchanged will spend the next "
                "budget the same way"
            )
        case "reported-blocker":
            blocker = report.outcome_detail or "unspecified blocker"
            return f"stopped on a reported blocker: {blocker}"
    turns = report.assistant_turns
    plural = "" if turns == 1 else "s"
    cap = report.max_turns
    if cap is not None and turns >= cap:
        return f"hit the turn cap after {turns} turn{plural}"
    short_of = f", short of its {cap}-turn cap" if cap is not None else ""
    reason = _incomplete_reason(report)
    return f"did not complete after {turns} turn{plural}{short_of}" + (
        f": {reason}" if reason else ""
    )


def _incomplete_reason(report: Report) -> str | None:
    """The most specific account of an incomplete stop the report actually carries."""
    for entry in reversed(report.verdicts):
        verdict = entry.get("verdict")
        if not isinstance(verdict, dict) or verdict.get("value"):
            continue
        reason = verdict.get("reason")
        if isinstance(reason, str) and reason.strip():
            return _bounded_note(f"unmet {entry.get('kind')} verdict: {reason}")
    return _bounded_note(report.assessment) or _bounded_note(report.stderr)


def _reported_blocker(result: RunResult) -> str | None:
    """Return the supervisor-confirmed terminal blocker from a failed final verdict.

    The simulated user owns the judgment that a worker's report is both terminal
    and outside the dispatch's control. Requiring its durable prefix avoids treating
    repeated prose, ordinary failures, or a worker's unilateral claim as this
    outcome. The final boolean verdict remains false, so releasing the conversation
    promptly cannot turn blocked work into successful work.
    """
    if result.completed:
        return None
    for entry in reversed(result.verdicts):
        match entry:
            case {"verdict": {"value": False, "reason": str(reason)}}:
                prefix, separator, blocker = reason.strip().partition(":")
                if separator and f"{prefix.lower()}:" == REPORTED_BLOCKER_PREFIX:
                    return _bounded_note(blocker)
    return None


def _build_report(
    persona: str,
    result: RunResult,
    *,
    provenance: DispatchProvenance | None = None,
    max_turns: int | None = None,
) -> Report:
    """Adapt the SDK's validated report without changing our public contract."""
    raw_assessment = result.raw.get("assessment")
    assessment = (
        raw_assessment.strip()
        if isinstance(raw_assessment, str) and raw_assessment.strip()
        else None
    )
    # Current SDKs expose telemetry as a typed property; validated raw reports
    # keep records from older SDKs readable after an upgrade.
    typed_result = cast(_TelemetryResult, result)
    raw_telemetry = (
        typed_result.telemetry if hasattr(result, "telemetry") else result.raw.get("telemetry")
    )
    raw = dict(result.raw)
    if provenance is not None:
        raw["provenance"] = provenance
    reported_blocker = _reported_blocker(result)
    return Report(
        persona=persona,
        exit_code=result.exit_code,
        completed=result.completed,
        stopped_early=bool(result.raw.get("stopped_early", False)),
        assistant_turns=result.assistant_turns,
        verdicts=cast(list[dict[str, Any]], list(result.verdicts)),
        usage=dict(result.usage),
        raw=raw,
        stderr=result.stderr,
        assessment=assessment,
        telemetry_data=dict(raw_telemetry) if isinstance(raw_telemetry, dict) else None,
        outcome=(
            REPORTED_BLOCKER_OUTCOME
            if reported_blocker
            else (
                NO_AGENT_PROGRESS_OUTCOME
                if not result.completed and _agent_produced_nothing(result)
                else None
            )
        ),
        outcome_detail=reported_blocker,
        max_turns=max_turns,
    )


def _validate_oneharness_timeout(value: str) -> None:
    """Reject a non-positive-integer ``ONEHARNESS_TIMEOUT`` (seconds) at the boundary.

    The value crosses in from the process environment; validate it here so a typo
    fails loudly rather than reaching oneharness as an opaque per-turn timeout error
    mid-dispatch.
    """
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise DispatchError(
            f"ONEHARNESS_TIMEOUT must be a positive integer number of seconds, got {value!r}"
        ) from None
    if seconds <= 0:
        raise DispatchError(
            f"ONEHARNESS_TIMEOUT must be a positive integer number of seconds, got {value!r}"
        )


def _stall_timeout(env: Mapping[str, str]) -> float:
    value = env.get("ORCHESTRATOR_DISPATCH_STALL_TIMEOUT", DEFAULT_DISPATCH_STALL_TIMEOUT)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise DispatchError(
            "ORCHESTRATOR_DISPATCH_STALL_TIMEOUT must be a positive number of seconds, "
            f"got {value!r}"
        ) from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise DispatchError(
            "ORCHESTRATOR_DISPATCH_STALL_TIMEOUT must be a positive number of seconds, "
            f"got {value!r}"
        )
    return seconds


def _worker_heartbeat_timeout(env: Mapping[str, str]) -> float:
    value = env.get("ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT", DEFAULT_WORKER_HEARTBEAT_TIMEOUT)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise DispatchError(
            "ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT must be a positive number of seconds, "
            f"got {value!r}"
        ) from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise DispatchError(
            "ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT must be a positive number of seconds, "
            f"got {value!r}"
        )
    return seconds


def _file_progress(root: Path) -> tuple[FileProgress, ...]:
    if not root.exists():
        return ()
    records: list[FileProgress] = []
    for path in root.rglob("*"):
        try:
            stat = path.stat()
        except (FileNotFoundError, PermissionError):
            continue
        if path.is_file():
            records.append(FileProgress(os.fspath(path), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(records, key=lambda record: record.path))


def _read_watchdog_pid(pid_file: Path) -> ProcessId:
    try:
        pid = int(pid_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DispatchError(f"watchdog pid file is invalid: {pid_file}") from None
    if pid <= 0:
        raise DispatchError(f"watchdog pid file is invalid: {pid_file}")
    return ProcessId(pid)


def _agent_status(status_dir: Path, name: str) -> str | None:
    """Read one agent status marker, treating an unreadable marker as absent."""
    try:
        return (status_dir / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def agent_failure_reason(status_dir: Path) -> str | None:
    """Explain a harness-side death from what the agent wrapper recorded.

    Provider throttling, quota exhaustion, an OOM kill, and a genuine crash all
    reach the dispatcher as the same dead process tree. The wrapper records the
    child's exit disposition and parks its stderr, so this turns that into one
    sentence the planner can act on instead of a bare ``worker-died``.
    """
    recorded = _bounded_note(_agent_status(status_dir, AGENT_FAILURE_NAME))
    tail = _agent_stderr_tail(status_dir)
    if recorded and tail:
        return f"{recorded}: {tail}"
    return recorded or tail or None


def _bounded_note(raw: str | None) -> str | None:
    """Bound and redact one harness-authored line before it becomes evidence."""
    if raw is None:
        return None
    return " ".join(redact(raw[:REPORTED_NOTE_CHARS]).split()) or None


def _agent_stderr_tail(status_dir: Path) -> str:
    """Return a collapsed, redacted, length-capped tail of the child's stderr.

    Read from the end: a verbose harness leaves pages of startup chatter before it
    fails, and the part that names the failure is the last of it.
    """
    try:
        with (status_dir / AGENT_STDERR_NAME).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - AGENT_STDERR_READ_BYTES))
            raw = stream.read()
    except OSError:
        return ""
    collapsed = " ".join(redact(raw.decode("utf-8", errors="replace")).split())
    if len(collapsed) <= AGENT_STDERR_TAIL_CHARS:
        return collapsed
    return f"...{collapsed[-AGENT_STDERR_TAIL_CHARS:]}"


def _worker_death_detail(status_dir: Path, root_pid: ProcessId, condition: str) -> str:
    """Say why a worker died, carrying whatever its wrapper managed to record.

    A worker that dies before its first turn produces no report, no transcript and
    no verdict, so the bare outcome name is the whole of what a reader gets — and
    it is the same string whether the harness refused to start, the provider was
    throttled, or the turn simply stopped heartbeating. ``condition`` names the
    liveness rule that fired; the agent wrapper records the child's exit status and
    stderr for the rest. All of it is best-effort, so an absent marker degrades the
    sentence rather than hiding the death.
    """
    # The wrapper is the only writer, but this file crosses a process boundary, so
    # only a plausible wait status is repeated back; anything else is unknown.
    recorded = _agent_status(status_dir, AGENT_EXIT_CODE_NAME) or ""
    plausible = recorded.isascii() and recorded.isdigit() and len(recorded) <= 3
    exit_status = recorded if plausible and int(recorded) <= 255 else "unknown"
    return f"worker-died (watchdog pid {root_pid}, agent exit status {exit_status}): {condition}"


def scoped_session(name: str, project_dir: str | Path) -> str:
    """Bind a conversation name to the directory the agent will actually run in.

    A harness stores its resumable conversations per working directory, so a
    recorded session name is only resolvable from the directory that created it.
    The lifecycle names a session after the branch, and every run cuts that branch
    a worktree under its own run root — so re-dispatching a branch (pinning one,
    resuming one, recovering one) would otherwise hand the harness a name whose
    conversation lives under a directory that no longer exists, and the resume
    fails before the first turn. Folding the directory into the name keeps a
    re-dispatch resolvable, while steps and retries *within* one run, which share
    the worktree, still share one conversation.
    """
    digest = hashlib.sha256(os.fspath(Path(project_dir).resolve()).encode("utf-8")).hexdigest()
    return f"{name}@{digest[:10]}"


def _validate_environment(env: Mapping[str, str]) -> None:
    """Validate caller-provided values before they reach the process boundary."""
    for key, value in env.items():
        if not isinstance(key, str) or not key or "\x00" in key or "=" in key:
            raise DispatchError(f"environment variable name is invalid: {key!r}")
        if not isinstance(value, str) or "\x00" in value:
            raise DispatchError(f"environment variable {key!r} must be a non-NUL string")


def run_onejudge(
    config: dict[str, Any],
    task: str,
    *,
    persona: str = "agent",
    cwd: str | Path = REPO_ROOT,
    onejudge_bin: str = "onejudge",
    provider: str | None = None,
    env: dict[str, str] | None = None,
    unset_llmlint_wrapper: bool = False,
    labels: Mapping[str, str] | None = None,
    timeout: float | None = None,
    cancel: threading.Event | None = None,
) -> Report:
    """Run an already-merged effective config through onejudge; return a Report.

    The SDK passes the task to the CLI over stdin, so arbitrarily long, multi-line
    tasks need no shell quoting. A config/provider error (exit 2) is raised as a
    DispatchError rather than returned as a normal outcome.

    ``labels`` locate this dispatch in the tracked graph (run/round/node/step) and
    are layered over any ``ONEHARNESS_HISTORY_LABELS`` we inherited, so a nested
    dispatch keeps the outer run's labels as well as its own.
    """
    _validate_environment(env or {})
    process_env = {**os.environ, **(env or {})}
    if unset_llmlint_wrapper:
        process_env.pop("LLMLINT_ONEHARNESS_BIN", None)
        process_env["ORCHESTRATOR_WATCHDOG_UNSET_LLMLINT"] = "1"
    process_env.setdefault("ONEHARNESS_TIMEOUT", DEFAULT_ONEHARNESS_TIMEOUT)
    _validate_oneharness_timeout(process_env["ONEHARNESS_TIMEOUT"])
    stall_timeout = _stall_timeout(process_env)
    heartbeat_timeout = _worker_heartbeat_timeout(process_env)
    turn_cap = _configured_turn_cap(config)
    onejudge_provenance = _resolve_onejudge(onejudge_bin, process_env)
    resolved_onejudge = onejudge_provenance["path"]
    configured_provider = config.get("provider")
    provider_kind = provider
    if provider_kind is None and isinstance(configured_provider, dict):
        configured_kind = configured_provider.get("kind")
        provider_kind = configured_kind if isinstance(configured_kind, str) else None
    provenance = DispatchProvenance(
        provider_kind=provider_kind or "oneharness",
        onejudge=onejudge_provenance,
    )
    inherited_labels = process_env.get(LABEL_ENV)
    if labels or inherited_labels is not None:
        try:
            normalized_labels = merge_labels(inherited_labels, labels or {})
        except LabelError as exc:
            raise DispatchError(f"invalid history label: {exc}") from exc
        if normalized_labels:
            process_env[LABEL_ENV] = normalized_labels
        else:
            process_env.pop(LABEL_ENV, None)

    async def execute() -> RunResult | Report | None:
        with owned_scratch_directory() as directory:
            pid_file = directory / "pid"
            agent_status_dir = directory / "agent"
            agent_status_dir.mkdir()
            process_env["ORCHESTRATOR_AGENT_STATUS_DIR"] = os.fspath(agent_status_dir)
            runner = OneJudge(
                executable=sys.executable,
                executable_args=(
                    "-m",
                    "orchestrator.watchdog",
                    OWN_PROCESS_GROUP_FLAG,
                    os.fspath(pid_file),
                    resolved_onejudge,
                ),
            )
            run = asyncio.create_task(
                runner.run(
                    cast(RunConfig, config),
                    task,
                    provider=provider,
                    cwd=str(cwd),
                    env=process_env,
                    timeout=timeout,
                )
            )
            observed_tree: tuple[ProcessId, ...] = ()

            async def watch_liveness() -> WatchdogSignal | None:
                nonlocal observed_tree
                pid_wait_started = time.monotonic()
                while True:
                    if run.done():
                        return None
                    if pid_file.exists():
                        try:
                            pid = _read_watchdog_pid(pid_file)
                        except DispatchError:
                            # The watchdog creates then fills this file. Under load,
                            # existence can become visible during that short write.
                            if time.monotonic() - pid_wait_started >= min(5.0, heartbeat_timeout):
                                _read_watchdog_pid(pid_file)
                        else:
                            break
                    await asyncio.sleep(min(0.05, stall_timeout / 4))
                activity = process_activity(pid)
                observed = activity.pids
                observed_tree = observed
                previous = (activity, _file_progress(Path(cwd) / ".git"))
                last_progress = time.monotonic()
                agent_identity: str | None = None
                missing_agent_identity: str | None = None
                last_agent_heartbeat_ns: int | None = None
                last_agent_heartbeat = time.monotonic()
                while not run.done():
                    await asyncio.sleep(min(0.25, stall_timeout / 4, heartbeat_timeout / 4))
                    activity = process_activity(pid)
                    if activity.pids:
                        observed = tuple(dict.fromkeys((*observed, *activity.pids)))
                        observed_tree = observed
                    else:
                        # Normal process exit precedes SDK report parsing by a tiny
                        # interval. Give that handoff one bounded grace period;
                        # leaked pipe holders keep the awaitable pending beyond it.
                        await asyncio.sleep(min(5.0, heartbeat_timeout))
                        if run.done():  # pragma: no cover - real subprocess race
                            return None
                        return WatchdogSignal(  # pragma: no cover - real killed-worker e2e
                            "worker-died",
                            pid,
                            observed,
                            detail=(
                                agent_failure_reason(agent_status_dir)
                                or "the tracked worker process tree exited without a report"
                            ),
                        )
                    agent_pid_file = agent_status_dir / "agent.pid"
                    if agent_pid_file.exists():
                        try:
                            current_agent = agent_pid_file.read_text(encoding="utf-8").strip()
                            agent_pid = ProcessId(int(current_agent))
                        except (OSError, ValueError):
                            current_agent = ""
                            agent_pid = ProcessId(0)
                        done_agent = _agent_status(agent_status_dir, "agent.done")
                        failed_agent = _agent_status(agent_status_dir, "agent.failed")
                        if (  # pragma: no cover - real killed-agent e2e
                            current_agent and failed_agent == current_agent
                        ):
                            return WatchdogSignal(
                                "worker-died",
                                pid,
                                observed,
                                detail=(
                                    agent_failure_reason(agent_status_dir)
                                    or "the agent harness reported a failed turn"
                                ),
                            )
                        if current_agent and done_agent != current_agent:
                            if agent_pid not in activity.pids:
                                # This tree was sampled before the pid was read, and an
                                # agent turn can both start and finish inside that gap.
                                # Re-sample, then re-read the marker the wrapper writes
                                # before it exits: only a pid missing from the newer
                                # tree and still unmarked has actually died.
                                activity = process_activity(pid)
                                if activity.pids:
                                    observed = tuple(dict.fromkeys((*observed, *activity.pids)))
                                    observed_tree = observed
                                latest_agent = _agent_status(agent_status_dir, "agent.pid")
                                if (
                                    agent_pid not in activity.pids
                                    and latest_agent == current_agent
                                    and _agent_status(agent_status_dir, "agent.done")
                                    != current_agent
                                ):
                                    # The wrapper records agent.done after wait(2)
                                    # observes the child exit. A loaded host can
                                    # schedule this watcher between those operations
                                    # for longer than any chosen sleep. Confirm the
                                    # same unfinished identity against a second,
                                    # independently sampled tree instead of turning
                                    # scheduler latency into a death diagnosis.
                                    if missing_agent_identity == current_agent:
                                        return WatchdogSignal(
                                            "worker-died",
                                            pid,
                                            observed,
                                            detail=(
                                                agent_failure_reason(agent_status_dir)
                                                or "the agent harness process vanished mid-turn "
                                                "without recording an exit"
                                            ),
                                        )
                                    missing_agent_identity = current_agent
                                else:
                                    missing_agent_identity = None
                            else:
                                missing_agent_identity = None
                            child_pid_file = agent_status_dir / "agent.child.pid"
                            if child_pid_file.exists():
                                try:
                                    child_pid = ProcessId(
                                        int(child_pid_file.read_text(encoding="utf-8"))
                                    )
                                except (OSError, ValueError):
                                    pass
                                else:
                                    if child_pid in activity.pids:
                                        observed = tuple(
                                            dict.fromkeys((*observed, agent_pid, child_pid))
                                        )
                                        observed_tree = observed
                            if agent_identity != current_agent:
                                agent_identity = current_agent
                                last_agent_heartbeat_ns = None
                                last_agent_heartbeat = time.monotonic()
                            try:
                                observed_agent_heartbeat_ns: int | None = (
                                    (agent_status_dir / "agent.heartbeat").stat().st_mtime_ns
                                )
                            except FileNotFoundError:
                                observed_agent_heartbeat_ns = last_agent_heartbeat_ns
                            if observed_agent_heartbeat_ns != last_agent_heartbeat_ns:
                                last_agent_heartbeat_ns = observed_agent_heartbeat_ns
                                last_agent_heartbeat = time.monotonic()
                            elif time.monotonic() - last_agent_heartbeat >= heartbeat_timeout:
                                return WatchdogSignal(
                                    "worker-died",
                                    pid,
                                    observed,
                                    detail=(
                                        agent_failure_reason(agent_status_dir)
                                        or "the agent harness stopped heartbeating for "
                                        f"{heartbeat_timeout:g}s"
                                    ),
                                )
                        else:
                            missing_agent_identity = None
                    current = (activity, _file_progress(Path(cwd) / ".git"))
                    if current != previous:
                        previous = current
                        last_progress = time.monotonic()
                    elif time.monotonic() - last_progress >= stall_timeout:
                        return WatchdogSignal("stalled", pid, observed)
                return None  # pragma: no cover - watcher/run completion scheduling race

            watcher = asyncio.create_task(watch_liveness())
            cancellation: asyncio.Task[None] | None = None
            if cancel is not None:

                async def cancellation_requested() -> None:
                    while not cancel.is_set():
                        await asyncio.sleep(0.05)

                cancellation = asyncio.create_task(cancellation_requested())
            waiting: set[asyncio.Task[Any]] = {run, watcher}
            if cancellation is not None:
                waiting.add(cancellation)
            done, pending = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if run in done:
                signal = await watcher if watcher in done else None
                for pending_task in pending:
                    pending_task.cancel()
                if signal is not None:
                    terminate_processes(signal.observed_pids, externally_waited=(signal.root_pid,))
                elif pid_file.exists():
                    terminate_processes(
                        observed_tree,
                        externally_waited=(_read_watchdog_pid(pid_file),),
                    )
                if pid_file.exists():
                    completed_pid = _read_watchdog_pid(pid_file)
                    terminate_process_group(completed_pid, externally_waited=(completed_pid,))
                    terminate_tree(completed_pid)
                return await run
            if watcher in done and (signal := await watcher):
                terminate_processes(signal.observed_pids, externally_waited=(signal.root_pid,))
                terminate_process_group(signal.root_pid, externally_waited=(signal.root_pid,))
                terminate_tree(signal.root_pid)
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run
                if signal.reason == "worker-died":
                    worker_detail = _worker_death_detail(
                        agent_status_dir, signal.root_pid, signal.detail or "worker-died"
                    )
                    if "dispatch failure:" in worker_detail:
                        raise DispatchError(worker_detail)
                    return Report(
                        persona,
                        EXIT_INCOMPLETE,
                        False,
                        True,
                        0,
                        [],
                        {},
                        None,
                        worker_detail,
                        outcome="worker-died",
                        outcome_detail=signal.detail,
                        max_turns=turn_cap,
                    )
                raise DispatchError(
                    f"dispatch stalled for {stall_timeout:g}s with no process-tree CPU/I/O "
                    "or repository progress; terminated onejudge/provider worker tree"
                )
            watcher.cancel()
            run.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run
            terminate_processes(
                observed_tree,
                externally_waited=(_read_watchdog_pid(pid_file),) if pid_file.exists() else (),
            )
            if pid_file.exists():
                cancelled_pid = _read_watchdog_pid(pid_file)
                terminate_process_group(cancelled_pid, externally_waited=(cancelled_pid,))
                terminate_tree(cancelled_pid)
            return None

    try:
        result = asyncio.run(execute())
    except FileNotFoundError as exc:
        raise DispatchError(
            f"onejudge binary not found: {onejudge_bin!r} — run 'just bootstrap'"
        ) from exc
    except OneJudgeTimeoutError as exc:  # pragma: no cover - timing-dependent
        raise DispatchError(f"onejudge timed out after {timeout}s") from exc
    except OneJudgeProcessError as exc:
        # Exit 2 covers both a rejected config AND a provider/runtime failure (e.g.
        # the harness process dying → "provider error ... Broken pipe"). Don't
        # assume "bad config" — surface onejudge's own stderr, which says which.
        detail = exc.stderr.strip() or "<no stderr>"
        raise DispatchError(
            f"onejudge failed (exit {exc.returncode} — bad config or provider/runtime error): "
            f"{detail}"
        ) from exc
    except ContractError as exc:
        raise DispatchError(
            f"onejudge failed (exit 2 — bad config or provider/runtime error): {exc}"
        ) from exc
    if isinstance(result, Report):
        result.raw = dict(result.raw or {})
        result.raw["provenance"] = provenance
        return result
    if result is None:
        return Report(
            persona,
            1,
            False,
            True,
            0,
            [],
            {},
            {"provenance": provenance},
            "cancelled cooperatively",
            max_turns=turn_cap,
        )
    return _build_report(persona, result, provenance=provenance, max_turns=turn_cap)


def _agent_run_context(
    config: dict[str, Any],
    *,
    cwd: str | Path,
    project_dir: str | None,
    oneharness_mode: str | None,
    use_llmlint_wrapper: bool = True,
) -> tuple[str | Path, dict[str, str]]:
    """Compute the (cwd, env) for the onejudge run, mutating `config` as needed.

    onejudge runs the agent in its OWN cwd, so when `project_dir` is set the agent
    is put there and the repo's oneharness configs are made resolvable from that
    cwd: the agent provider uses a wrapper that passes oneharness `--config`, and
    the judge side gets an absolute `provider.judge_config`. `oneharness_mode` is forwarded as
    `ONEHARNESS_MODE` (e.g. "bypass" where codex's OS sandbox can't initialize).
    Bypass also tells llmlint to use the container boundary instead of asking its
    nested read-only judge to create a network namespace unavailable on this host.
    """
    run_cwd: str | Path = cwd
    env: dict[str, str] = {}
    if oneharness_mode is not None:
        env["ONEHARNESS_MODE"] = oneharness_mode
        if oneharness_mode == "bypass" and use_llmlint_wrapper:
            env["LLMLINT_ONEHARNESS_BIN"] = str(REPO_ROOT / "scripts/llmlint-oneharness.sh")
    if project_dir is not None:
        run_cwd = project_dir
        prov = config.get("provider", {})
        match prov:
            case {"kind": "oneharness"}:
                prov["bin"] = str(AGENT_ONEHARNESS_BIN)
            case {"kind": "split", "skill": {"kind": "oneharness"} as skill}:
                skill["bin"] = str(AGENT_ONEHARNESS_BIN)
        judge_config = prov.get("judge_config")
        if isinstance(judge_config, str) and not Path(judge_config).is_absolute():
            prov["judge_config"] = str((REPO_ROOT / judge_config).resolve())
    return run_cwd, env


def dispatch(
    persona: str,
    task: str,
    *,
    base_path: str | Path = BASE_CONFIG,
    persona_dir: str | Path = PERSONA_DIR,
    session: str | None = None,
    project_dir: str | None = None,
    max_turns: int | None = None,
    done_when: str | None = None,
    extra_instructions: str | None = None,
    cwd: str | Path = REPO_ROOT,
    onejudge_bin: str = "onejudge",
    provider: str | None = None,
    oneharness_mode: str | None = None,
    use_llmlint_wrapper: bool = True,
    labels: Mapping[str, str] | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    cancel: threading.Event | None = None,
) -> Report:
    """Merge base ⊕ persona and drive the subtask to completion via onejudge.

    `oneharness_mode` (e.g. "bypass") is forwarded to oneharness via
    `ONEHARNESS_MODE` — needed to let codex write where its OS sandbox can't
    initialize (see docs/onejudge-integration.md). When `project_dir` is set the
    agent runs there (onejudge runs the agent in its own cwd), and the repo's
    oneharness configs are made resolvable from that cwd.
    """
    base = load_yaml(base_path)
    try:
        resolved_persona = persona_path(persona, Path(persona_dir))
    except ValueError as exc:
        raise DispatchError(str(exc)) from exc
    if not resolved_persona.is_file():
        raise DispatchError(
            f"unknown persona {persona!r}: no {resolved_persona} "
            f"(create one with 'just new-persona {persona}')"
        )
    persona_data = load_yaml(resolved_persona)
    config = build_effective_config(
        base,
        persona_data,
        session=session if session is not None else f"dispatch-{persona}-{uuid.uuid4().hex}",
        max_turns=max_turns,
        done_when=done_when,
        extra_instructions=extra_instructions,
    )

    run_cwd, context_env = _agent_run_context(
        config,
        cwd=cwd,
        project_dir=project_dir,
        oneharness_mode=oneharness_mode,
        use_llmlint_wrapper=use_llmlint_wrapper,
    )
    process_env = {**context_env, **(env or {})}
    _validate_environment(process_env)
    semantic_labels = {
        **(labels or {}),
        **semantic_agent_labels(persona),
    }
    return run_onejudge(
        config,
        task,
        persona=persona,
        cwd=run_cwd,
        onejudge_bin=onejudge_bin,
        provider=provider,
        env=process_env or None,
        unset_llmlint_wrapper=not use_llmlint_wrapper,
        labels=semantic_labels,
        timeout=timeout,
        cancel=cancel,
    )


# llmlint: ignore[changed_behavior_has_e2e] tests/e2e/test_channel_e2e.py drives the real CLI
# for missing-plan, unsupported-provider, and missing-onejudge launch failures as well as the
# successful detached split-provider journey; provider payload variants are deterministic
# pre-launch validation branches covered exhaustively in tests/test_orchestrator_launch.py.
# llmlint: ignore[structural_pattern_matching] provider_kind is first validated as the
# discriminator, then each open-ended provider mapping receives variant-specific checks.
def launch_orchestrator(
    plan_path: str | Path,
    *,
    runs_dir: str | Path = "runs",
    run_id: str | None = None,
    base_path: str | Path = BASE_CONFIG,
    onejudge_bin: str = "onejudge",
    skill_provider: Mapping[str, Any] | None = None,
    max_turns: int = 100,
    turn_timeout: int = int(ORCHESTRATOR_ONEHARNESS_TIMEOUT),
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    cwd: str | Path = REPO_ROOT,
    acknowledge_concurrent: bool = False,
    round_budget: float | None = None,
    oneharness_mode: str = DEFAULT_ONEHARNESS_MODE,
    launcher: str | None = None,
    launcher_session_id: str | None = None,
) -> str:
    """Launch a detached live-supervised orchestrator and return its run id.

    ``oneharness_mode`` is forwarded to the launched process as ``ONEHARNESS_MODE``
    and defaults to ``bypass`` for the same reason `just repo-task` does: the
    container is the sandbox, and claude-code's non-interactive default denies —
    without prompting — every command outside `.claude/settings.json`, which would
    leave the orchestrator unable to run the very commands its persona mandates.
    """
    # Validate launcher provenance up front so a bad value fails before any side effect.
    try:
        resolve_launcher_kind(launcher)
        validate_session_id(launcher_session_id)
    except LaunchError as exc:
        raise DispatchError(str(exc)) from exc
    plan = Path(plan_path).resolve()
    if not plan.is_file():
        raise DispatchError(f"plan does not exist: {plan}")
    if not isinstance(onejudge_bin, str) or not onejudge_bin or "\x00" in onejudge_bin:
        raise DispatchError("onejudge binary must be a non-empty, non-NUL string")
    if round_budget is not None and (not math.isfinite(round_budget) or round_budget <= 0):
        raise DispatchError(f"'{ROUND_BUDGET_OPTION}' must be a positive finite number")
    if oneharness_mode not in ONEHARNESS_MODES:
        raise DispatchError(
            f"oneharness mode must be one of {', '.join(ONEHARNESS_MODES)}, got {oneharness_mode!r}"
        )
    plan_mapping = load_yaml(plan)
    # Import locally because graph's direct-agent runner imports this module.
    from .graph import parse_graph, validate_graph_repo_aliases
    from .plan import PlanError

    try:
        graph = parse_graph(plan_mapping)
        validate_graph_repo_aliases(graph)
    except PlanError as exc:
        raise DispatchError(f"invalid plan: {exc}") from exc
    root = Path(runs_dir).resolve()
    run_dir = resolve_run_dir(root, plan_mapping, plan, run_id)
    if run_dir.exists():
        raise DispatchError(f"run already exists: {run_dir}")
    goal = graph.goal
    register_run(
        run_id=run_dir.name,
        run_dir=run_dir,
        goal=goal,
        identities=graph_identities(graph),
        pid=os.getpid(),
        acknowledge_concurrent=acknowledge_concurrent,
        # stderr, not the launch record: stdout is the record `just orchestrate`
        # prints for a caller to parse, and a notice is for the planner reading along.
        report=lambda notice: print(f"orchestrate: {notice}", file=sys.stderr),
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        channel_dir = create_channel(run_dir, heartbeat_interval=heartbeat_interval)
    except ChannelError as exc:
        raise DispatchError(str(exc)) from exc
    config = build_effective_config(load_yaml(base_path), {}, max_turns=max_turns)
    # The live planner's supervisor verdict is the completion authority. Standalone
    # simulated-model eval/assessment calls do not belong on this command relay.
    config.pop("evals", None)
    config.pop("assessment", None)
    worker_skill = dict(config.get("provider", {}))
    skill = dict(skill_provider or worker_skill)
    provider_kind = skill.get("kind")
    if provider_kind not in {"command", "oneharness"}:
        raise DispatchError("orchestrator skill provider kind must be 'command' or 'oneharness'")
    if provider_kind == "command":
        provider_command = skill.get("command")
        if not (
            isinstance(provider_command, list)
            and provider_command
            and all(
                isinstance(item, str) and item and "\x00" not in item for item in provider_command
            )
        ):
            raise DispatchError(
                "orchestrator command provider requires a non-empty command list of strings"
            )
    else:
        provider_bin = skill.get("bin", "oneharness")
        if not isinstance(provider_bin, str) or not provider_bin or "\x00" in provider_bin:
            raise DispatchError("orchestrator oneharness provider bin must be a non-empty string")
        # Pin the role's own wrapper, exactly as project dispatch pins the worker's
        # (`_agent_run_context`): it forces oneharness.orchestrator.toml and exports
        # the alternate-Claude config indirection that config's fallback names.
        skill["bin"] = str(ORCHESTRATOR_ONEHARNESS_BIN)
    config["provider"] = {
        "kind": "split",
        "skill": skill,
        "judge": {
            "kind": "command",
            "command": [
                sys.executable,
                "-m",
                "orchestrator.channel",
                str(channel_dir),
                run_dir.name,
                "1",
                "--timeout",
                str(turn_timeout),
            ],
        },
    }
    config["session"] = f"orchestrator-{run_dir.name}"
    effective = run_dir / "orchestrator" / "effective.onejudge.yaml"
    effective.parent.mkdir()
    effective.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    worker_base = load_yaml(base_path)
    worker_provider_kind = worker_skill.get("kind")
    if worker_provider_kind not in {"command", "oneharness"}:
        raise DispatchError("worker provider kind must be 'command' or 'oneharness'")
    worker_base_path = effective.parent / "worker-base.yaml"
    worker_base_path.write_text(yaml.safe_dump(worker_base, sort_keys=False), encoding="utf-8")
    report_path = effective.parent / "report.json"
    stderr_path = effective.parent / "stderr.log"
    round_budget_arg = "" if round_budget is None else f" {ROUND_BUDGET_OPTION} {round_budget:g}"
    task = (
        "Drive this tracked orchestration plan one round at a time. Execute the real command "
        f"`just run-plan {plan} --run {run_dir.name} --runs-dir {root} --base {worker_base_path} "
        f"--provider {worker_provider_kind}"
        f"{' --acknowledge-concurrent' if acknowledge_concurrent else ''}"
        f"{round_budget_arg}` for each required "
        "round, review its recorded "
        "result, and surface milestones, blockers, departures, and closeout to your supervisor."
    )
    resolved_onejudge = _resolve_onejudge(onejudge_bin, os.environ)["path"]
    command = [resolved_onejudge, "run", str(effective), "--task", task, "--format", "json"]
    # Stamped once, here: nested dispatches inherit ONEHARNESS_HISTORY_LABELS and layer
    # their node labels over it (run_onejudge -> merge_labels), so every worker, judge,
    # and check-in conversation carries the launch join without stamping each one.
    repository_identity = next(iter(sorted(str(item) for item in graph_identities(graph))), "")
    launch_id, launch_labels = _launch_provenance(
        launcher=launcher,
        session_id=launcher_session_id,
        repository_identity=repository_identity,
    )
    process_env = dict(os.environ)
    process_env["ONEHARNESS_TIMEOUT"] = str(turn_timeout)
    process_env["ONEHARNESS_MODE"] = oneharness_mode
    process_env[CHANNEL_DIR_ENV] = str(channel_dir)
    process_env[CHANNEL_RUN_ID_ENV] = run_dir.name
    try:
        process_env[LABEL_ENV] = merge_labels(
            process_env.get(LABEL_ENV),
            {
                **launch_labels,
                "run_id": run_dir.name,
                **semantic_agent_labels("orchestrator"),
            },
        )
    except LabelError as exc:
        raise DispatchError(f"invalid launch label: {exc}") from exc
    _validate_oneharness_timeout(process_env["ONEHARNESS_TIMEOUT"])
    try:
        with (
            report_path.open("w", encoding="utf-8") as stdout,
            stderr_path.open("w", encoding="utf-8") as stderr,
        ):
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                text=True,
                stdout=stdout,
                stderr=stderr,
                env=process_env,
                start_new_session=True,
            )
    except FileNotFoundError as exc:
        raise DispatchError(f"onejudge binary not found: {onejudge_bin!r}") from exc
    atomic_json(
        effective.parent / "status.json",
        {
            "status": "running",
            "pid": proc.pid,
            "host": socket.gethostname(),
            "started": datetime.now(UTC).isoformat(),
        },
    )
    update_run_owner(run_dir.name, run_dir, proc.pid)
    raw_plan_name = plan_mapping.get("name")
    plan_name = slugify(
        raw_plan_name if isinstance(raw_plan_name, str) and raw_plan_name.strip() else plan.stem
    )
    launch: LaunchRecord = {
        "schema_version": 2,
        "run_id": run_dir.name,
        "channel_id": run_dir.name,
        "plan_name": plan_name,
        "goal": goal,
        "commands": {
            # `watch` leads: this launch is detached by design, so the record has to
            # name the one command that attaches to it. A planner who reads no
            # further than this line is still attached rather than reconstructing
            # the run from process tables while queued updates sit unread.
            "watch": f"just watch {run_dir.name}",
            "channel_next": f"just channel-next {run_dir.name}",
            "monitor": f"just monitor {run_dir.name}",
        },
        # The key is a literal because `LaunchRecord` types it; `launch.read_launch_info`
        # is the only reader, and tests/test_orchestrator_launch.py round-trips the two.
        "launch": {"launch_id": launch_id},
    }
    atomic_json(run_dir / LAUNCH_RECORD_NAME, launch)
    (run_dir / "planner.md").write_text(
        "# Planner launch\n\n"
        f"- Run id: `{run_dir.name}`\n"
        f"- Channel id: `{run_dir.name}`\n"
        f"- Attach and stay attached: `just watch {run_dir.name}`\n"
        f"- Next surface: `just channel-next {run_dir.name}`\n"
        f"- Monitor: `just monitor {run_dir.name}`\n",
        encoding="utf-8",
    )
    return run_dir.name


def main_orchestrate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch a live-supervised orchestrator")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--run-id")
    parser.add_argument("--base", type=Path, default=BASE_CONFIG)
    parser.add_argument("--onejudge-bin", default="onejudge")
    parser.add_argument("--acknowledge-concurrent", action="store_true")
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=DEFAULT_HEARTBEAT_INTERVAL,
        metavar="SECONDS",
        help=f"planner status-update interval (default: {DEFAULT_HEARTBEAT_INTERVAL:g})",
    )
    parser.add_argument(ROUND_BUDGET_OPTION, type=float, metavar="SECONDS")
    parser.add_argument(
        "--oneharness-mode",
        default=DEFAULT_ONEHARNESS_MODE,
        choices=list(ONEHARNESS_MODES),
        help="approval/sandbox mode for the orchestrator's harness (via ONEHARNESS_MODE; "
        f"default: {DEFAULT_ONEHARNESS_MODE} — the no-approval mode; the container is "
        "the sandbox)",
    )
    parser.add_argument(
        "--skill-command",
        nargs="+",
        help="command-provider argv for the orchestrator agent (primarily for deterministic tests)",
    )
    parser.add_argument(
        "--launcher",
        choices=sorted(LAUNCHER_KINDS),
        default=os.environ.get("ORCHESTRATOR_LAUNCHER"),
        help="top-level harness running orchestrate (default: $ORCHESTRATOR_LAUNCHER, "
        "else the harness detected from this session's environment)",
    )
    parser.add_argument(
        "--launcher-session",
        default=os.environ.get("ORCHESTRATOR_LAUNCHER_SESSION"),
        metavar="SESSION_ID",
        help="launching session id to group runs by (default: $ORCHESTRATOR_LAUNCHER_SESSION, "
        "else this session's own id)",
    )
    args = parser.parse_args(argv)
    # Detected from the ambient session so the ordinary launch is attributable without
    # the planner remembering two flags: an unattributable run is one no planner can
    # tell from another planner's. Explicit values still win; see `select_launch`.
    selected = select_launch(launcher=args.launcher, session_id=args.launcher_session)
    try:
        skill = {"kind": "command", "command": args.skill_command} if args.skill_command else None
        launched = launch_orchestrator(
            args.plan,
            runs_dir=args.runs_dir,
            run_id=args.run_id,
            base_path=args.base,
            onejudge_bin=args.onejudge_bin,
            skill_provider=skill,
            heartbeat_interval=args.heartbeat_interval,
            acknowledge_concurrent=args.acknowledge_concurrent,
            round_budget=args.round_budget,
            oneharness_mode=args.oneharness_mode,
            launcher=selected.launcher,
            launcher_session_id=selected.session_id,
        )
        print(
            (args.runs_dir.resolve() / launched / LAUNCH_RECORD_NAME)
            .read_text(encoding="utf-8")
            .strip()
        )
    except (DispatchError, ConfigError, LaunchError) as exc:
        # LaunchError reaches here from provenance validation that happens before
        # anything is spawned; it is a launch-boundary refusal like the others, not
        # a crash, so it exits 2 with a message rather than a traceback.
        print(f"orchestrate: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    return 0


def _read_task(value: str | None) -> str:
    """Resolve the task from the CLI arg, reading stdin when omitted or ``-``."""
    if value is None or value == "-":
        return sys.stdin.read()
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dispatch one subtask to onejudge with a persona.")
    parser.add_argument("persona", help="persona name (see personas/)")
    parser.add_argument(
        "task", nargs="?", default=None, help="the task ('-' or omitted reads stdin)"
    )
    parser.add_argument("--base", type=Path, default=BASE_CONFIG)
    parser.add_argument("--persona-dir", type=Path, default=PERSONA_DIR)
    parser.add_argument(
        "--project-dir", default=None, help="the target project dir (onejudge run cwd)"
    )
    parser.add_argument("--session", default=None)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--done-when", default=None)
    parser.add_argument("--cwd", default=None, help="working dir for onejudge (default: repo root)")
    parser.add_argument("--onejudge-bin", default="onejudge")
    parser.add_argument("--provider", default=None, choices=["oneharness", "command", "split"])
    parser.add_argument(
        "--oneharness-mode",
        default=None,
        choices=list(ONEHARNESS_MODES),
        help="approval/sandbox mode for the harness (via ONEHARNESS_MODE); "
        "use 'bypass' where codex's OS sandbox can't run",
    )
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--format", choices=["human", "json"], default="human")
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        report = dispatch(
            args.persona,
            _read_task(args.task),
            base_path=args.base,
            persona_dir=args.persona_dir,
            session=args.session,
            project_dir=args.project_dir,
            max_turns=args.max_turns,
            done_when=args.done_when,
            cwd=args.cwd or REPO_ROOT,
            onejudge_bin=args.onejudge_bin,
            provider=args.provider,
            oneharness_mode=args.oneharness_mode,
            timeout=args.timeout,
        )
    except (DispatchError, ConfigError) as exc:
        print(f"dispatch: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    rendered = json.dumps(report.raw, indent=2) if args.format == "json" else report.summary()
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return report.exit_code
