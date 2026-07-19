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
  * "complete-now"  -> the agent completes on its first turn (exit 0).
  * otherwise       -> the unified supervisor completes on the second agent turn,
                       after one push, exercising the two-sided loop (exit 0).
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


def main() -> int:
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
    if "provider-errors" in task:
        sys.stderr.write("fake_backend: provider error\n")
        return 1
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
    fail = "should-fail" in task or (drafting and "drafting-fails" in task)

    match op:
        case "respond":
            if "slow-branch" in task:
                witness = Path(task.split("slow-branch", 1)[1].strip().split()[0])
                with witness.open("a", encoding="utf-8") as stream:
                    stream.write("tick\n")
                time.sleep(0.8)
                with witness.open("a", encoding="utf-8") as stream:
                    stream.write("tick\n")
            orchestrator_plan = _orchestrator_command(task)
            plan_text = ""
            if orchestrator_plan is not None:
                plan_path = orchestrator_plan.plan
                plan_text = plan_path.read_text(encoding="utf-8")
                if _assistant_turns(messages) == 0:
                    subprocess.run(
                        orchestrator_plan.argv,
                        check=True,
                        capture_output=True,
                        text=True,
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
                drafted = (
                    "nonempty malformed drafting output\n"
                    if "drafting-invalid" in task
                    else "## What\nAdds the completed behavior from the branch diff.\n\n"
                    "## Why\nMakes the requested capability available.\n"
                )
                Path(output).write_text(drafted, encoding="utf-8")
            if "capture-cache-env" in task:
                (Path.cwd() / "CACHE_ENV.txt").write_text(
                    os.environ["ORCHESTRATOR_CACHE_DIR"], encoding="utf-8"
                )
            if "write-change" in task:
                (Path.cwd() / "CHANGE.txt").write_text("change from fake agent\n", encoding="utf-8")
            if "write-unique-change" in task:
                identity = re.sub(r"[^A-Za-z0-9._-]+", "-", Path.cwd().name)
                (Path.cwd() / f"CHANGE-{identity}.txt").write_text(
                    "change from fake agent\n", encoding="utf-8"
                )
            # `complete-now` finishes on the first turn; otherwise the agent stays
            # "not done" and completion is decided by the unified supervisor
            # below, which only passes on the second turn — exercising the loop.
            done = (not fail) and ("complete-now" in task)
            guidance = _planner_guidance(messages)
            if orchestrator_plan is not None:
                turn = _assistant_turns(messages)
                if turn == 0 and "surface-blocker" in plan_text:
                    agent_message = json.dumps(
                        {"kind": "blocker", "message": "plan departure needs a decision"}
                    )
                elif turn == 0:
                    agent_message = json.dumps(
                        {"kind": "milestone", "message": "tracked round completed"}
                    )
                else:
                    suffix = f"; received {guidance}" if guidance else ""
                    agent_message = json.dumps(
                        {"kind": "closeout", "message": f"orchestration complete{suffix}"}
                    )
            else:
                agent_message = "done" if done else "working on it"
            resp = {
                "message": agent_message,
                "done": done,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "events": [
                    {
                        "kind": "tool_call",
                        "name": "bash",
                        "input": {"command": "just check"},
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
            complete = (not fail) and _assistant_turns(messages) >= 2
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
            value = (not fail) and ("complete-now" in task or _assistant_turns(messages) >= 2)
            resp = {"value": value, "reason": "fake judge verdict"}
        case "judge":
            resp = {"value": req.get("max", 5), "reason": "fake numeric verdict"}
        case "assess":
            resp = {
                "text": (
                    "None"
                    if "slow-branch" in task or "surface-" in task
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
