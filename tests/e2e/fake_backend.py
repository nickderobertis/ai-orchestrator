#!/usr/bin/env python3
"""A deterministic onejudge `command`-provider backend for the e2e suite.

onejudge spawns this once per protocol step, writes one JSON request to stdin,
and reads one JSON response from stdout (see onejudge's docs/protocol.md). It
stands in for the paid model/harness — the one genuinely external thing the gate
cannot run for free — so the e2e drives the *real* onejudge CLI (real config
parse, real loop, real subprocess boundary) with a fake only at that seam.

Outcome is steered by sentinels in the task (the first user message):
  * "should-fail"   -> the agent never declares done and the done_when judge
                       returns false, so the run hits the turn cap (exit 1).
  * "stop-short"    -> the supervisor releases the agent on its first turn but the
                       done_when judge returns false, so the run ends incomplete
                       *without* reaching the cap. onejudge's exit 1 covers both
                       this and the cap, which is why a settled result has to say
                       which one it was.
  * "complete-now"  -> the agent completes on its first turn (exit 0).
  * "terminal-blocker" -> the worker reports an uncontrollable blocker and the
                          supervisor promptly releases it with a failed verdict.
  * "repeat-productive" -> the worker repeats its progress phrase but continues
                           until completing on its third turn.
  * otherwise       -> the unified supervisor completes on the second agent turn,
                       after one push, exercising the two-sided loop (exit 0).

A journey that needs a node observably in flight names its own rendezvous rather
than timing a sleep: `hold-<turn>-ready=<path> hold-<turn>-release=<path>` makes
that zero-based agent turn announce it has arrived and then block until the test
creates the release path. One turn holds per named pair, so a journey that needs two
stops names two turns; a task that names none never delays. `tests/e2e/rendezvous.py`
renders the fragment and is the only convention there is — no journey needs a second.
"""

# llmlint: ignore-file[boundary_inputs_validated] this deterministic test backend validates the
# request mapping and every message field it consumes below; unsupported operations fail closed.

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict, cast

# onejudge spawns this file once per protocol step — six times for a default
# two-turn dispatch — so every import is paid on every step. Importing the three
# constants below from `orchestrator` transitively loaded onejudge_sdk (and
# jsonschema), asyncio, and yaml, which cost more than half of a fake dispatch's
# wall clock. They are restated here instead, and
# `tests/test_fake_backend_contract.py` fails when either side drifts.
REPORTED_BLOCKER_PREFIX = "terminal blocker reported:"  # orchestrator.dispatch
CAPACITY_ERROR_MARKER = "scratch-capacity-preflight:"  # orchestrator.scratch
DEFAULT_MIN_FREE_BYTES = 5 * 1024**3  # orchestrator.scratch


class SupervisorRequest(TypedDict):
    """Validated fields consumed from onejudge's protocol-v4 supervisor request."""

    task: str


class SupervisorCompleted(TypedDict):
    completion: Literal[True]
    reason: str


class SupervisorContinue(TypedDict):
    completion: Literal[False]
    message: str
    reason: str


def _task_text(messages: list[dict]) -> str:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


def _assistant_turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant")


class OrchestratorCommand(NamedTuple):
    plan: Path
    runs_dir: Path
    argv: list[str]


def _orchestrator_command(task: str) -> OrchestratorCommand | None:
    match = re.search(r"`(just run-plan .+?)`", task)
    if match is None:
        return None
    command = shlex.split(match.group(1))
    runs_index = command.index("--runs-dir")
    return OrchestratorCommand(Path(command[2]), Path(command[runs_index + 1]), command)


def _planner_guidance(messages: list[dict]) -> str | None:
    for message in reversed(messages):
        if message.get("role") == "user" and "retry " in str(message.get("content", "")):
            return str(message["content"])
    return None


def _hold_until_released(task: str, turn: int) -> bool:
    """Hold this agent turn at its rendezvous, reporting whether it held.

    The test names a ready path and a release path per turn in the task; this
    announces it has arrived and then blocks until the test releases it. Holding on
    the test's signal keeps the agent in flight exactly as long as the journey needs,
    where a fixed sleep both costs that time unconditionally and races the assertion
    it was meant to make observable. A turn the task does not name runs straight
    through, which is what makes one rendezvous enough for every journey.
    """
    ready = re.search(rf"hold-{turn}-ready=(\S+)", task)
    release = re.search(rf"hold-{turn}-release=(\S+)", task)
    if ready is None or release is None:
        return False
    Path(ready.group(1)).write_text("ready\n", encoding="utf-8")
    released = Path(release.group(1))
    while not released.exists():
        time.sleep(0.01)
    return True


def _commit_and_push_ci_iteration(state: str) -> None:
    """Act like the paid agent iterating a branch against authoritative CI."""
    Path("CI_STATE.txt").write_text(state + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "CI_STATE.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"test: CI is {state.lower()}"], check=True, capture_output=True
    )
    branch = subprocess.run(
        ["git", "branch", "--show-current"], check=True, text=True, capture_output=True
    ).stdout.strip()
    subprocess.run(["git", "push", "-u", "origin", branch], check=True, capture_output=True)


def _onejudge_report_proxy(argv: list[str]) -> int:
    """Run the adopted CLI and supply deterministic report-v5 producer fields."""
    executable = os.environ.get("REAL_ONEJUDGE")
    if not executable:
        sys.stderr.write("fake_backend: REAL_ONEJUDGE is required for report proxy mode\n")
        return 2
    process = subprocess.run([executable, *argv], text=True, capture_output=True, check=False)
    sys.stderr.write(process.stderr)
    if not process.stdout.strip():
        return process.returncode
    report = json.loads(process.stdout)
    telemetry_mode = os.environ.get("FAKE_REPORT_TELEMETRY", "legacy")
    if telemetry_mode != "legacy":
        telemetry = {
            "wall_ms": 25,
            "agent": {
                "model_ms": 12,
                "tool_ms": 5,
                "time_to_first_token_ms": 3,
                "session_ids": ["agent-history"],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "cache_read_tokens": 1,
                    "cache_write_tokens": 0,
                    "cost_usd": 0.02,
                },
            },
            "judge": {
                "model_ms": 8,
                "tool_ms": 0,
                "time_to_first_token_ms": 2,
                "session_ids": ["judge-history"],
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "cache_read_tokens": 1,
                    "cache_write_tokens": 0,
                    "cost_usd": 0.01,
                },
            },
            "orchestration_ms": 0,
            "sessions": [
                {
                    "session_id": "agent-history",
                    "history_id": "agent-record",
                    "role": "agent",
                    "turn_index": 0,
                    "started_at": "2026-07-19T00:00:00+00:00",
                    "finished_at": "2026-07-19T00:00:00.017000+00:00",
                },
                {
                    "session_id": "judge-history",
                    "history_id": "judge-record",
                    "role": "judge",
                    "turn_index": 1,
                    "started_at": "2026-07-19T00:00:00.017000+00:00",
                    "finished_at": "2026-07-19T00:00:00.025000+00:00",
                },
            ],
        }
        if telemetry_mode == "invalid":
            telemetry["judge"]["usage"].pop("cache_write_tokens")
            telemetry["judge"]["usage"]["cost_usd"] = True
        report["telemetry"] = telemetry
    sys.stdout.write(json.dumps(report))
    return process.returncode


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "onejudge-report-proxy":
        return _onejudge_report_proxy(sys.argv[2:])
    req = json.loads(sys.stdin.read())
    if not isinstance(req, dict):
        sys.stderr.write("fake_backend: request must be a JSON object\n")
        return 1
    op = req.get("op")
    if not isinstance(op, str):
        sys.stderr.write("fake_backend: op must be a string\n")
        return 1
    messages = req.get("messages") or []
    if not isinstance(messages, list) or not all(
        isinstance(item, dict)
        and ("role" not in item or isinstance(item["role"], str))
        and ("content" not in item or isinstance(item["content"], str))
        for item in messages
    ):
        sys.stderr.write("fake_backend: messages must be a list of objects\n")
        return 1
    task = _task_text(messages)
    if "configuration-errors" in task:
        sys.stderr.write("fake_backend: bad config\n")
        return 1
    if "unknown-errors" in task:
        sys.stderr.write("fake_backend: unexpected runtime failure\n")
        return 1
    drafting = "Write the final body, and nothing else, to this absolute path:" in task
    if drafting and "drafting-errors" in task:
        sys.stderr.write("fake_backend: forced drafting provider failure\n")
        return 1
    resume_marker = Path(".fake-turn-cap-preserved")
    resume_segments = (
        int(resume_marker.read_text(encoding="utf-8").strip())
        if "resume-after-cap" in task and resume_marker.exists()
        else 0
    )
    capped_resume_segment = "resume-after-cap" in task and resume_segments <= 1
    fail = "should-fail" in task or capped_resume_segment or (drafting and "drafting-fails" in task)

    match op:
        case "respond":
            # A deterministic real-provider boundary: whatever this turn is about to
            # do, the journey holding it here decides when it happens.
            _hold_until_released(task, _assistant_turns(messages))
            if "Check-in command: " in task and "agent-synthesized planner update" in task:
                channel_dir = Path(task.split("Channel directory: ", 1)[1].splitlines()[0])
                attempts = channel_dir / "check-in-dispatches.txt"
                fail_once_value = os.environ.get("FAKE_CHECK_IN_FAIL_ONCE")
                fail_once = Path(fail_once_value) if fail_once_value else None
                skip_surface_value = os.environ.get("FAKE_CHECK_IN_SKIP_SURFACE_ONCE")
                skip_surface = Path(skip_surface_value) if skip_surface_value else None
                if fail_once is not None and not fail_once.exists():
                    fail_once.write_text("failed\n", encoding="utf-8")
                    with attempts.open("a", encoding="utf-8") as stream:
                        stream.write("failed\n")
                    sys.stderr.write("fake_backend: forced first check-in failure\n")
                    return 1
                else:
                    with attempts.open("a", encoding="utf-8") as stream:
                        stream.write(
                            "missing-surface\n"
                            if skip_surface is not None and not skip_surface.exists()
                            else "success\n"
                        )
                    (channel_dir / "check-in-labels.txt").write_text(
                        os.environ["ONEHARNESS_HISTORY_LABELS"],
                        encoding="utf-8",
                    )
                    message = (
                        "active-worker: executing the slow agent step; "
                        "evidence: node-started is recorded and node-settled is absent; "
                        "follow-ups: none"
                    )
                    if skip_surface is not None and not skip_surface.exists():
                        skip_surface.write_text("skipped\n", encoding="utf-8")
                    else:
                        command = shlex.split(
                            task.split("Check-in command: ", 1)[1].splitlines()[0]
                        )
                        command[command.index("MESSAGE")] = message
                        surfaced = subprocess.run(
                            command, text=True, capture_output=True, check=False
                        )
                        if surfaced.returncode != 0:
                            sys.stderr.write(surfaced.stderr)
                            return 1
            guidance = _planner_guidance(messages)
            run_log = re.search(r"record-run=(\S+)", task)
            if run_log is not None:
                with Path(run_log.group(1)).open("a", encoding="utf-8") as stream:
                    stream.write("run\n")
            # One JSON line per delivered prompt, so a journey can assert on exactly
            # what the agent side received across several dispatches of one node.
            task_log = re.search(r"record-task=(\S+)", task)
            if task_log is not None:
                with Path(task_log.group(1)).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(task) + "\n")
            if "resume-after-cap" in task:
                resume_marker.write_text(str(resume_segments + 1), encoding="utf-8")
            if "slow-branch" in task:
                witness = Path(task.split("slow-branch", 1)[1].strip().split()[0])
                with witness.open("a", encoding="utf-8") as stream:
                    stream.write("tick\ntick\n")
            orchestrator_plan = _orchestrator_command(task)
            infrastructure_failures = {
                "provider-errors": "fake_backend: provider error",
                "infrastructure-sigkill": "oneharness exited with signal: 9 (SIGKILL)",
                "infrastructure-auth": "harness failed (auth): login required",
                "infrastructure-v03-write": (
                    "harness claude-code cannot write v0.3 history telemetry"
                ),
                "infrastructure-v03-incomplete": (
                    "new history record lacks complete v0.3 telemetry"
                ),
                "infrastructure-v10-write": ("harness codex cannot write v1.0 history telemetry"),
                "infrastructure-v10-incomplete": ("new history run lacks complete v1.0 telemetry"),
                "infrastructure-enospc": "[Errno 28] No space left on device",
                "infrastructure-oom": "worker was OOMKilled",
                "infrastructure-preflight": (
                    f"{CAPACITY_ERROR_MARKER} scratch filesystem at /tmp has "
                    "1 bytes free, below the "
                    f"{DEFAULT_MIN_FREE_BYTES}-byte dispatch threshold"
                ),
            }
            infrastructure_error = next(
                (
                    detail
                    for sentinel, detail in infrastructure_failures.items()
                    if sentinel in task
                ),
                None,
            )
            if infrastructure_error is not None and orchestrator_plan is None:
                sys.stderr.write(f"{infrastructure_error}\n")
                return 1
            plan_text = ""
            if orchestrator_plan is not None:
                plan_path = orchestrator_plan.plan
                plan_text = plan_path.read_text(encoding="utf-8")
                orchestrator_turn = _assistant_turns(messages)
                if "pre-round-pause " in plan_text:
                    release = Path(plan_text.split("pre-round-pause ", 1)[1].split()[0])
                    while not release.exists():
                        time.sleep(0.02)
                if orchestrator_turn == 0:
                    run_argv = list(orchestrator_plan.argv)
                    run_env = None
                    if "lifecycle-worker-death-retry" in plan_text:
                        provider_index = run_argv.index("--provider")
                        del run_argv[provider_index : provider_index + 2]
                        run_argv[:2] = [
                            str(Path(sys.executable).with_name("orchestrator-run-plan"))
                        ]
                        run_env = dict(os.environ)
                        mock_barrier = Path(os.environ["MOCK_AGENT_BARRIER"])
                        run_env["PATH"] = (
                            f"{mock_barrier.parent / 'bin'}{os.pathsep}{os.environ['PATH']}"
                        )
                    subprocess.run(
                        run_argv,
                        check=not any(
                            sentinel in plan_text
                            for sentinel in (
                                "continuation-channel",
                                '"name": "live-edit"',
                                # A round whose carried node fails again by design;
                                # its non-zero status is the journey's subject.
                                '"name": "planner-context"',
                                '"name": "carried-edits"',
                                # Live-edit journeys whose round legitimately settles
                                # waiting or failed; run-plan's non-zero status is the
                                # expected outcome, not an orchestrator failure.
                                '"name": "eligibility"',
                                '"name": "rejection"',
                                "lifecycle-worker-death-retry",
                                "provider-errors",
                                "infrastructure-",
                            )
                        ),
                        capture_output=True,
                        text=True,
                        env=run_env,
                    )
                elif orchestrator_turn == 1 and "continuation-channel" in plan_text:
                    run_id = orchestrator_plan.argv[orchestrator_plan.argv.index("--run") + 1]
                    settled_run = orchestrator_plan.runs_dir / run_id
                    if not (settled_run / "round-01" / "result.json").is_file():
                        raise RuntimeError("expected one settled continuation-channel run")
                    forwarded = orchestrator_plan.argv[orchestrator_plan.argv.index("--runs-dir") :]
                    subprocess.run(
                        [
                            "just",
                            "next-round",
                            run_id,
                            "--complete-human",
                            "gate",
                            *forwarded,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                elif orchestrator_turn == 1 and any(
                    name in plan_text
                    for name in ('"name": "planner-context"', '"name": "carried-edits"')
                ):
                    # The transition an orchestrator drives after the planner's
                    # continuing verdict: no attestation, no edits file, just the
                    # next round derived from the round that settled.
                    run_id = orchestrator_plan.argv[orchestrator_plan.argv.index("--run") + 1]
                    forwarded = orchestrator_plan.argv[orchestrator_plan.argv.index("--runs-dir") :]
                    subprocess.run(
                        ["just", "next-round", run_id, *forwarded],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                elif (
                    orchestrator_turn == 1
                    and "lifecycle-worker-death-retry" in plan_text
                    and guidance is not None
                ):
                    run_id = orchestrator_plan.argv[orchestrator_plan.argv.index("--run") + 1]
                    edits = orchestrator_plan.runs_dir / run_id / "worker-death-retry.json"
                    edits.write_text(
                        json.dumps({"retry": {"change": {}}}),
                        encoding="utf-8",
                    )
                    forwarded = orchestrator_plan.argv[orchestrator_plan.argv.index("--runs-dir") :]
                    provider_index = forwarded.index("--provider")
                    del forwarded[provider_index : provider_index + 2]
                    subprocess.run(
                        [
                            str(Path(sys.executable).with_name("orchestrator-next-round")),
                            run_id,
                            str(edits),
                            *forwarded,
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        env={
                            **os.environ,
                            "PATH": (
                                f"{Path(os.environ['MOCK_AGENT_BARRIER']).parent / 'bin'}"
                                f"{os.pathsep}{os.environ['PATH']}"
                            ),
                        },
                    )
            if "ci-iterate" in task:
                state = "RED" if _assistant_turns(messages) == 0 else "GREEN"
                _commit_and_push_ci_iteration(state)
            if (
                "Write the final body, and nothing else, to this absolute path:" in task
                and "drafting-empty" not in task
            ):
                output = task.split(
                    "Write the final body, and nothing else, to this absolute path:\n", 1
                )[1].splitlines()[0]
                task_why = None
                if "derive-workstream-why" in task:
                    expected_contexts = (
                        "## Why\nLet users call the drafted API.",
                        "Legacy task without structured What/Why",
                        "## Why\nKeep the drafted API reliable.",
                    )
                    if not all(context in task for context in expected_contexts):
                        raise AssertionError(
                            "drafting task did not pass every structured workstream context"
                        )
                    task_why = "Let users call the drafted API while keeping it reliable."
                elif "derive-task-why" in task:
                    why_match = re.search(r"## Why\n(.+?)\nOriginal task context:", task, re.DOTALL)
                    if why_match is None:
                        raise AssertionError("drafting task did not pass structured Why context")
                    task_why = why_match.group(1).strip()
                drafted = (
                    "nonempty malformed drafting output\n"
                    if "drafting-invalid" in task
                    else (
                        "## What\nAdds the completed behavior from the branch diff.\n\n"
                        f"## Why\n{task_why}\n"
                        if task_why is not None
                        else "## What\nAdds the completed behavior from the branch diff.\n\n"
                        "## Why\nMakes the requested capability available.\n"
                    )
                )
                Path(output).write_text(drafted, encoding="utf-8")
            if "capture-cache-env" in task:
                (Path.cwd() / "CACHE_ENV.txt").write_text(
                    os.environ["ORCHESTRATOR_CACHE_DIR"], encoding="utf-8"
                )
            if "capture-llmlint-env" in task:
                (Path.cwd() / "LLMLINT_ENV.txt").write_text(
                    os.environ.get("LLMLINT_ONEHARNESS_BIN", "<absent>"), encoding="utf-8"
                )
            if "write-change" in task:
                (Path.cwd() / "CHANGE.txt").write_text("change from fake agent\n", encoding="utf-8")
            if "run-worker-gate" in task:
                # A real worker proves its own change with the repository's own gate
                # before it settles, and iterates on a red one rather than accepting
                # it. This worker has nothing left to change, so it runs the gate
                # again and settles only once retrying stops helping — the turn-cap
                # exhaustion the lifecycle's own gate-failed outcome exists for.
                # Output goes to the shared cache directory, never into the worktree:
                # a file written here would change the very content the publication
                # rebuild is asked to judge.
                log = Path(os.environ["ORCHESTRATOR_CACHE_DIR"]) / "worker-gate.log"
                for _attempt in range(2):
                    gate = subprocess.run(
                        ["bash", "gate.sh"], capture_output=True, text=True, check=False
                    )
                    with log.open("a", encoding="utf-8") as handle:
                        handle.write(f"exit={gate.returncode}\n{gate.stdout}{gate.stderr}")
                    if gate.returncode == 0:
                        break
            if "publish-change-to-base" in task:
                subprocess.run(["git", "add", "CHANGE.txt"], check=True, capture_output=True)
                subprocess.run(
                    ["git", "commit", "-m", "test: publish change early"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "push", "origin", "HEAD:main"],
                    check=True,
                    capture_output=True,
                )
            if "write-unique-change" in task:
                identity = re.sub(r"[^A-Za-z0-9._-]+", "-", Path.cwd().name)
                (Path.cwd() / f"CHANGE-{identity}.txt").write_text(
                    "change from fake agent\n", encoding="utf-8"
                )
            # `complete-now` finishes on the first turn; otherwise the agent stays
            # "not done" and completion is decided by the unified supervisor
            # below, which only passes on the second turn — exercising the loop.
            done = (not fail) and (
                "complete-now" in task or "agent-synthesized planner update" in task
            )
            if orchestrator_plan is not None:
                turn = _assistant_turns(messages)
                if turn == 0 and "surface-blocker" in plan_text:
                    agent_message = json.dumps(
                        {"kind": "blocker", "message": "plan departure needs a decision"}
                    )
                elif turn == 1 and "lifecycle-worker-death-retry" in plan_text:
                    agent_message = json.dumps(
                        {
                            "kind": "blocker",
                            "message": (
                                "lifecycle worker died after its bounded retry; "
                                "planner intervention is required"
                            ),
                        }
                    )
                elif turn == 0:
                    summary = (
                        "tracked round completed " + "x" * 100_000
                        if "surface-large-summary" in plan_text
                        else "tracked round completed"
                    )
                    agent_message = json.dumps({"kind": "milestone", "message": summary})
                else:
                    suffix = f"; received {guidance}" if guidance else ""
                    agent_message = json.dumps(
                        {"kind": "closeout", "message": f"orchestration complete{suffix}"}
                    )
            elif "silent-agent" in task:
                # A provider that accepts the turn and answers with nothing. onejudge
                # counts the turn it attempted, so the budget drains at a rate no
                # working agent produces — the shape a failing provider leaves.
                agent_message = ""
            elif "terminal-blocker" in task:
                agent_message = "Terminal blocker: required external service is unavailable."
            elif "repeat-productive" in task:
                agent_message = "continuing verified migration work"
            else:
                agent_message = "done" if done else "working on it"
            resp = {
                "message": agent_message,
                "done": done,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_tokens": 4,
                    "cache_write_tokens": 1,
                    "cost_usd": 0.002,
                },
                "events": [
                    {
                        "kind": "tool_call",
                        "name": "bash",
                        "input": {"command": "just check"},
                        "duration_ms": 7,
                        "index": 0,
                    }
                ],
            }
        case "user":
            resp = {"message": "verify it before you call it done", "stop": False}
        case "supervisor":
            original_task = req.get("task")
            if not isinstance(original_task, str):
                sys.stderr.write("fake_backend: supervisor task must be a string\n")
                return 1
            supervisor = cast(SupervisorRequest, req)
            completion_turn = (
                13 if "complete-after-13" in task else 3 if "repeat-productive" in task else 2
            )
            complete = (not fail) and (
                "stop-short" in task
                or "terminal-blocker" in task
                or "agent-synthesized planner update" in task
                or _assistant_turns(messages) >= completion_turn
                or "resume-after-cap" in task
                and resume_segments >= 2
            )
            if complete:
                supervisor_resp: SupervisorCompleted | SupervisorContinue = {
                    "completion": True,
                    "reason": "fake supervisor verified completion",
                }
            else:
                supervisor_resp = {
                    "completion": False,
                    "message": "verify it before you call it done",
                    "reason": f"fake supervisor requires another turn for {supervisor['task']}",
                }
            resp = supervisor_resp
        case "judge" if req.get("kind") == "boolean":
            # Final evals still use the standalone judge operation. The loop's
            # The adopted version routes the completion decision through `supervisor` above.
            completion_turn = (
                13 if "complete-after-13" in task else 3 if "repeat-productive" in task else 2
            )
            value = (
                (not fail)
                and "stop-short" not in task
                and (
                    "complete-now" in task
                    or "agent-synthesized planner update" in task
                    or _assistant_turns(messages) >= completion_turn
                )
            )
            resp = {
                "value": value,
                "reason": (
                    f"{REPORTED_BLOCKER_PREFIX} required external service is unavailable"
                    if "terminal-blocker" in task
                    else "the supervisor released it before its criteria were met"
                    if "stop-short" in task
                    else "fake judge verdict"
                ),
            }
        case "judge":
            resp = {"value": req.get("max", 5), "reason": "fake numeric verdict"}
        case "assess":
            resp = {
                "text": (
                    "None"
                    if "slow-branch" in task or "surface-" in task or "no-assessment" in task
                    else "- Add a regression test for the adjacent edge case."
                )
            }
        case _:
            sys.stderr.write(f"fake_backend: unknown op {op!r}\n")
            return 1

    sys.stdout.write(json.dumps(resp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
