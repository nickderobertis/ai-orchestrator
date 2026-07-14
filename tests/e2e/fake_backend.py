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
  * otherwise       -> the agent completes on its second turn, after one
                       supervisor push, exercising the two-sided loop (exit 0).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _task_text(messages: list[dict]) -> str:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


def _assistant_turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant")


def main() -> int:
    req = json.loads(sys.stdin.read())
    if not isinstance(req, dict):
        sys.stderr.write("fake_backend: request must be a JSON object\n")
        return 1
    op = req.get("op")
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
    fail = "should-fail" in task

    match op:
        case "respond":
            if "write-change" in task:
                (Path.cwd() / "CHANGE.txt").write_text("change from fake agent\n", encoding="utf-8")
            # `complete-now` finishes on the first turn; otherwise the agent stays
            # "not done" and completion is decided by the supervisor's done_when
            # judge below, which only passes on the second turn — exercising the
            # two-sided loop.
            done = (not fail) and ("complete-now" in task)
            resp = {
                "message": "done" if done else "working on it",
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
        case "judge" if req.get("kind") == "boolean":
            # onejudge re-judges done_when both mid-run (ending the loop early when
            # satisfied) and at the end. Withhold satisfaction until two assistant
            # turns have happened so the normal path runs a real supervisor round.
            value = (not fail) and ("complete-now" in task or _assistant_turns(messages) >= 2)
            resp = {"value": value, "reason": "fake judge verdict"}
        case "judge":
            resp = {"value": req.get("max", 5), "reason": "fake numeric verdict"}
        case "assess":
            resp = {"text": "- Add a regression test for the adjacent edge case."}
        case _:
            sys.stderr.write(f"fake_backend: unknown op {op!r}\n")
            return 1

    sys.stdout.write(json.dumps(resp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
