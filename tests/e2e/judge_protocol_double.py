#!/usr/bin/env python3
"""A provider command speaking onejudge's `docs/protocol.md`, standing in for a paid model.

`judge_protocol_double.py <name> <continue-for>` answers exactly one request per process,
which is the whole of that protocol: onejudge writes one JSON object to stdin, closes it,
and reads one JSON object back. Nothing above that boundary is doubled — the onejudge that
spawns this is the pinned CLI, composing its own panel.

- `respond` is the worker's turn, and names which turn it was.
- `supervisor` continues with an instruction naming `<name>` while the transcript holds no
  more than `<continue-for>` worker turns, then completes. The instruction is what a panel
  places under that judge's own header, so `<name>` is how a reader tells whose it was.
- `judge` and `assess` pass, so the ops a judge side is asked once the conversation ends
  are answered rather than refused.

A negative `<continue-for>` is a judge that cannot run: it exits non-zero on `supervisor`
with nothing on stdout, which onejudge's protocol classifies as a failed provider.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    name, continue_for = sys.argv[1], int(sys.argv[2])
    request = json.loads(sys.stdin.readline())
    op = request["op"]
    worker_turns = sum(
        1 for message in request.get("messages", ()) if message.get("role") == "assistant"
    )
    answer: dict[str, object]
    match op:
        case "respond":
            answer = {"message": f"Renamed the helper (turn {worker_turns + 1})."}
        case "supervisor" if continue_for < 0:
            print(f"{name}: this judge cannot run", file=sys.stderr)
            return 3
        case "supervisor" if worker_turns <= continue_for:
            answer = {
                "completion": False,
                "message": f"{name}: a caller under tests/ still uses the old name.",
                "reason": f"{name} found an unrenamed caller",
            }
        case "supervisor":
            answer = {"completion": True, "reason": f"{name} found every caller renamed"}
        case "judge":
            answer = {"value": True, "reason": f"{name} holds the bar met"}
        case "assess":
            answer = {"text": f"{name} has no follow-up."}
        case _:
            print(f"{name}: unsupported op {op!r}", file=sys.stderr)
            return 2
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
