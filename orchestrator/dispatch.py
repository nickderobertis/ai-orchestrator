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
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict, cast

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
from .cli_contract import ROUND_BUDGET_OPTION
from .config import ConfigError, build_effective_config, load_yaml
from .coordination import atomic_json
from .goals import Goal, graph_identities, register_run, update_run_owner
from .labels import LABEL_ENV, LabelError, merge_labels
from .personas import persona_path
from .runs import ArtifactPaths, resolve_run_dir, slugify
from .watchdog import (
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
DEFAULT_WORKER_HEARTBEAT_TIMEOUT = "5"
ORCHESTRATOR_ONEHARNESS_TIMEOUT = "86400"
AGENT_ONEHARNESS_BIN = REPO_ROOT / "scripts" / "oneharness-agent.sh"
DispatchOutcome = Literal["worker-died"]
WatchdogReason = Literal["worker-died", "stalled"]


class DispatchError(Exception):
    """onejudge could not be run, or rejected the config (a loud failure)."""


class LaunchRecord(TypedDict):
    """Stable planner handoff persisted for one orchestrator launch."""

    schema_version: int
    run_id: str
    channel_id: str
    plan_name: str
    commands: dict[str, str]
    goal: Goal | None


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


def _build_report(
    persona: str, result: RunResult, *, provenance: dict[str, object] | None = None
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
    resolved_onejudge = shutil.which(onejudge_bin, path=process_env.get("PATH"))
    if resolved_onejudge is None:
        raise DispatchError(f"onejudge binary not found: {onejudge_bin!r} — run 'just bootstrap'")
    resolved_onejudge = os.path.abspath(resolved_onejudge)
    adopted_version = ONEJUDGE_VERSION_FILE.read_text(encoding="utf-8").strip()
    version_result = subprocess.run(
        [resolved_onejudge, "--version"],
        text=True,
        capture_output=True,
        env=process_env,
        check=False,
    )
    expected_version = f"onejudge {adopted_version}"
    actual_version = version_result.stdout.strip()
    if version_result.returncode != 0 or actual_version != expected_version:
        actual = actual_version or version_result.stderr.strip() or "<no version output>"
        raise DispatchError(
            f"onejudge version mismatch: expected {expected_version!r}, got {actual!r} "
            f"from {resolved_onejudge}"
        )
    configured_provider = config.get("provider")
    provider_kind = provider
    if provider_kind is None and isinstance(configured_provider, dict):
        configured_kind = configured_provider.get("kind")
        provider_kind = configured_kind if isinstance(configured_kind, str) else None
    provenance: dict[str, object] = {
        "provider_kind": provider_kind or "oneharness",
        "onejudge": {"path": resolved_onejudge, "version": adopted_version},
    }
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
        with tempfile.TemporaryDirectory(prefix="orchestrator-watchdog-") as directory:
            pid_file = Path(directory) / "pid"
            agent_status_dir = Path(directory) / "agent"
            agent_status_dir.mkdir()
            process_env["ORCHESTRATOR_AGENT_STATUS_DIR"] = os.fspath(agent_status_dir)
            runner = OneJudge(
                executable=sys.executable,
                executable_args=(
                    "-m",
                    "orchestrator.watchdog",
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
                            "worker-died", pid, observed
                        )
                    agent_pid_file = agent_status_dir / "agent.pid"
                    if agent_pid_file.exists():
                        try:
                            current_agent = agent_pid_file.read_text(encoding="utf-8").strip()
                            agent_pid = ProcessId(int(current_agent))
                        except (OSError, ValueError):
                            current_agent = ""
                            agent_pid = ProcessId(0)
                        done_file = agent_status_dir / "agent.done"
                        done_agent = (
                            done_file.read_text(encoding="utf-8").strip()
                            if done_file.exists()
                            else None
                        )
                        failed_file = agent_status_dir / "agent.failed"
                        failed_agent = (
                            failed_file.read_text(encoding="utf-8").strip()
                            if failed_file.exists()
                            else None
                        )
                        if (  # pragma: no cover - real killed-agent e2e
                            current_agent and failed_agent == current_agent
                        ):
                            return WatchdogSignal("worker-died", pid, observed)
                        if current_agent and done_agent != current_agent:
                            if agent_pid not in activity.pids:
                                return WatchdogSignal("worker-died", pid, observed)
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
                                return WatchdogSignal("worker-died", pid, observed)
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
                    terminate_processes(signal.observed_pids)
                elif pid_file.exists():
                    terminate_processes(observed_tree)
                if pid_file.exists():
                    completed_pid = _read_watchdog_pid(pid_file)
                    terminate_process_group(completed_pid)
                    terminate_tree(completed_pid)
                return await run
            if watcher in done and (signal := await watcher):
                terminate_processes(signal.observed_pids)
                terminate_process_group(signal.root_pid)
                terminate_tree(signal.root_pid)
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run
                if signal.reason == "worker-died":
                    return Report(
                        persona,
                        EXIT_INCOMPLETE,
                        False,
                        True,
                        0,
                        [],
                        {},
                        None,
                        "worker-died: tracked worker exited or stopped heartbeating",
                        outcome="worker-died",
                    )
                raise DispatchError(
                    f"dispatch stalled for {stall_timeout:g}s with no process-tree CPU/I/O "
                    "or repository progress; terminated onejudge/provider worker tree"
                )
            watcher.cancel()
            run.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run
            terminate_processes(observed_tree)
            if pid_file.exists():
                cancelled_pid = _read_watchdog_pid(pid_file)
                terminate_process_group(cancelled_pid)
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
        if result.raw is not None:
            result.raw["provenance"] = provenance
        return result
    if result is None:
        return Report(persona, 1, False, True, 0, [], {}, None, "cancelled cooperatively")
    return _build_report(persona, result, provenance=provenance)


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
        session=session if session is not None else f"dispatch-{persona}",
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
    return run_onejudge(
        config,
        task,
        persona=persona,
        cwd=run_cwd,
        onejudge_bin=onejudge_bin,
        provider=provider,
        env=process_env or None,
        unset_llmlint_wrapper=not use_llmlint_wrapper,
        labels=labels,
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
) -> str:
    """Launch a detached live-supervised orchestrator and return its run id."""
    plan = Path(plan_path).resolve()
    if not plan.is_file():
        raise DispatchError(f"plan does not exist: {plan}")
    if not isinstance(onejudge_bin, str) or not onejudge_bin or "\x00" in onejudge_bin:
        raise DispatchError("onejudge binary must be a non-empty, non-NUL string")
    if round_budget is not None and (not math.isfinite(round_budget) or round_budget <= 0):
        raise DispatchError(f"'{ROUND_BUDGET_OPTION}' must be a positive finite number")
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
    command = [onejudge_bin, "run", str(effective), "--task", task, "--format", "json"]
    process_env = dict(os.environ)
    process_env["ONEHARNESS_TIMEOUT"] = str(turn_timeout)
    process_env[CHANNEL_DIR_ENV] = str(channel_dir)
    process_env[CHANNEL_RUN_ID_ENV] = run_dir.name
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
        "schema_version": 1,
        "run_id": run_dir.name,
        "channel_id": run_dir.name,
        "plan_name": plan_name,
        "goal": goal,
        "commands": {
            "channel_next": f"just channel-next {run_dir.name}",
            "monitor": f"just monitor {run_dir.name}",
        },
    }
    atomic_json(run_dir / "launch.json", launch)
    (run_dir / "planner.md").write_text(
        "# Planner launch\n\n"
        f"- Run id: `{run_dir.name}`\n"
        f"- Channel id: `{run_dir.name}`\n"
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
        "--skill-command",
        nargs="+",
        help="command-provider argv for the orchestrator agent (primarily for deterministic tests)",
    )
    args = parser.parse_args(argv)
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
        )
        print(
            (args.runs_dir.resolve() / launched / "launch.json").read_text(encoding="utf-8").strip()
        )
    except (DispatchError, ConfigError) as exc:
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
        choices=["read-only", "plan", "default", "edit", "auto", "bypass"],
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
