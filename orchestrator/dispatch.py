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
import json
import os
import socket
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
from .channel import create_channel
from .config import ConfigError, build_effective_config, load_yaml
from .coordination import atomic_json
from .labels import LABEL_ENV, LabelError, merge_labels
from .personas import persona_path
from .runs import resolve_run_dir

# onejudge's own exit codes (see docs/cli.md): 0 completed + boolean evals passed,
# 1 hit the turn cap / a boolean eval failed, 2 bad config or usage.
EXIT_COMPLETED = 0
EXIT_INCOMPLETE = 1
EXIT_CONFIG_ERROR = 2
# Temporary hard per-turn ceiling for legitimate long-running agents. Issue #6
# will replace this coarse bound with separate inactivity and phase budgets.
DEFAULT_ONEHARNESS_TIMEOUT = "10800"
ORCHESTRATOR_ONEHARNESS_TIMEOUT = "86400"
AGENT_ONEHARNESS_BIN = REPO_ROOT / "scripts" / "oneharness-agent.sh"


class DispatchError(Exception):
    """onejudge could not be run, or rejected the config (a loud failure)."""


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

    def summary(self) -> str:
        state = "completed" if self.completed else "NOT completed"
        line = f"{self.persona}: {state} ({self.assistant_turns} assistant turn(s))"
        for v in self.verdicts:
            verdict = v.get("verdict", {})
            line += f"\n  - [{v.get('kind')}] {v.get('criterion')}: {verdict.get('value')}"
        if self.assessment:
            line += f"\n  follow-ups: {self.assessment}"
        return line


def _parse_report(stdout: str) -> dict[str, Any] | None:
    stdout = stdout.strip()
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _build_report(persona: str, exit_code: int, stdout: str, stderr: str) -> Report:
    data = _parse_report(stdout)
    messages = ((data or {}).get("transcript") or {}).get("messages") or []
    turns = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant")
    raw_assessment = (data or {}).get("assessment")
    assessment = (
        raw_assessment.strip()
        if isinstance(raw_assessment, str) and raw_assessment.strip()
        else None
    )
    return Report(
        persona=persona,
        exit_code=exit_code,
        completed=exit_code == EXIT_COMPLETED,
        stopped_early=bool((data or {}).get("stopped_early", False)),
        assistant_turns=turns,
        verdicts=list((data or {}).get("verdicts") or []),
        usage=dict((data or {}).get("usage") or {}),
        raw=data,
        stderr=stderr,
        assessment=assessment,
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
    labels: Mapping[str, str] | None = None,
    timeout: float | None = None,
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
    process_env.setdefault("ONEHARNESS_TIMEOUT", DEFAULT_ONEHARNESS_TIMEOUT)
    _validate_oneharness_timeout(process_env["ONEHARNESS_TIMEOUT"])
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
    try:
        result = asyncio.run(
            OneJudge(executable=onejudge_bin).run(
                cast(RunConfig, config),
                task,
                provider=provider,
                cwd=str(cwd),
                env=process_env,
                timeout=timeout,
            )
        )
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
    return _build_sdk_report(persona, result)


def _build_sdk_report(persona: str, result: RunResult) -> Report:
    """Adapt the SDK's validated report without changing our public contract."""
    raw_assessment = result.raw.get("assessment")
    assessment = (
        raw_assessment.strip()
        if isinstance(raw_assessment, str) and raw_assessment.strip()
        else None
    )
    return Report(
        persona=persona,
        exit_code=result.exit_code,
        completed=result.completed,
        stopped_early=bool(result.raw.get("stopped_early", False)),
        assistant_turns=result.assistant_turns,
        verdicts=list(result.verdicts),
        usage=dict(result.usage),
        raw=dict(result.raw),
        stderr=result.stderr,
        assessment=assessment,
    )


def _agent_run_context(
    config: dict[str, Any],
    *,
    cwd: str | Path,
    project_dir: str | None,
    oneharness_mode: str | None,
) -> tuple[str | Path, dict[str, str]]:
    """Compute the (cwd, env) for the onejudge run, mutating `config` as needed.

    onejudge runs the agent in its OWN cwd, so when `project_dir` is set the agent
    is put there and the repo's oneharness configs are made resolvable from that
    cwd: the agent provider uses a wrapper that passes oneharness `--config`, and
    the judge side gets an absolute `provider.judge_config`. `oneharness_mode` is forwarded as
    `ONEHARNESS_MODE` (e.g. "bypass" where codex's OS sandbox can't initialize).
    """
    run_cwd: str | Path = cwd
    env: dict[str, str] = {}
    if oneharness_mode is not None:
        env["ONEHARNESS_MODE"] = oneharness_mode
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
    labels: Mapping[str, str] | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
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
        config, cwd=cwd, project_dir=project_dir, oneharness_mode=oneharness_mode
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
        labels=labels,
        timeout=timeout,
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
    cwd: str | Path = REPO_ROOT,
) -> str:
    """Launch a detached live-supervised orchestrator and return its run id."""
    plan = Path(plan_path).resolve()
    if not plan.is_file():
        raise DispatchError(f"plan does not exist: {plan}")
    if not isinstance(onejudge_bin, str) or not onejudge_bin or "\x00" in onejudge_bin:
        raise DispatchError("onejudge binary must be a non-empty, non-NUL string")
    plan_mapping = load_yaml(plan)
    # Import locally because graph's direct-agent runner imports this module.
    from .graph import parse_graph

    parse_graph(plan_mapping)
    root = Path(runs_dir).resolve()
    run_dir = resolve_run_dir(root, plan_mapping, plan, run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    channel_dir = create_channel(run_dir)
    round_dir = run_dir / "round-01"
    round_dir.mkdir()
    config = build_effective_config(load_yaml(base_path), {}, max_turns=max_turns)
    # The live planner's supervisor verdict is the completion authority. Standalone
    # simulated-model eval/assessment calls do not belong on this command relay.
    config.pop("evals", None)
    config.pop("assessment", None)
    skill = dict(skill_provider or config.get("provider", {}))
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
    worker_base["provider"] = skill
    worker_base_path = effective.parent / "worker-base.yaml"
    worker_base_path.write_text(yaml.safe_dump(worker_base, sort_keys=False), encoding="utf-8")
    report_path = effective.parent / "report.json"
    stderr_path = effective.parent / "stderr.log"
    task = (
        "Drive this tracked orchestration plan one round at a time. Execute the real command "
        f"`just run-plan {plan} --runs-dir {root} --base {worker_base_path} "
        f"--provider {provider_kind}` for each required round, review its recorded "
        "result, and surface milestones, blockers, departures, and closeout to your supervisor."
    )
    command = [onejudge_bin, "run", str(effective), "--task", task, "--format", "json"]
    process_env = dict(os.environ)
    process_env["ONEHARNESS_TIMEOUT"] = str(turn_timeout)
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
        round_dir / "status.json",
        {
            "status": "running",
            "pid": proc.pid,
            "host": socket.gethostname(),
            "started": datetime.now(UTC).isoformat(),
        },
    )
    atomic_json(round_dir / "plan.json", {"name": run_dir.name, "nodes": []})
    return run_dir.name


def main_orchestrate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch a live-supervised orchestrator")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--run-id")
    parser.add_argument("--base", type=Path, default=BASE_CONFIG)
    parser.add_argument("--onejudge-bin", default="onejudge")
    parser.add_argument(
        "--skill-command",
        nargs="+",
        help="command-provider argv for the orchestrator agent (primarily for deterministic tests)",
    )
    args = parser.parse_args(argv)
    try:
        skill = {"kind": "command", "command": args.skill_command} if args.skill_command else None
        print(
            launch_orchestrator(
                args.plan,
                runs_dir=args.runs_dir,
                run_id=args.run_id,
                base_path=args.base,
                onejudge_bin=args.onejudge_bin,
                skill_provider=skill,
            )
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
