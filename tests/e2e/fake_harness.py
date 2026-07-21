#!/usr/bin/env python3
"""Deterministic codex/claude-code executable for oneharness fallback tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    harness = Path(sys.argv[0]).name
    args = sys.argv[1:]
    try:
        model_index = args.index("--model")
        model = args[model_index + 1]
    except (ValueError, IndexError):
        print("fake_harness: --model requires a value", file=sys.stderr)
        return 2
    log_value = os.environ.get("FAKE_HARNESS_LOG")
    if not log_value:
        print("fake_harness: FAKE_HARNESS_LOG must name an output file", file=sys.stderr)
        return 2
    log_path = Path(log_value)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"harness": harness, "model": model}) + "\n")

    compatible = model.startswith("gpt-") if harness == "codex" else model.startswith("claude-")
    if not compatible:
        print(f"unknown model: {model}", file=sys.stderr)
        return 1
    if harness == "codex":
        if os.environ.get("FAKE_HARNESS_WITH_TELEMETRY") == "1":
            print(json.dumps({"type": "turn.started"}))
        print(json.dumps({"type": "thread.started", "thread_id": "fake-codex-thread"}))
        print(
            json.dumps(
                {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}
            )
        )
        if os.environ.get("FAKE_HARNESS_WITH_TELEMETRY") == "1":
            print(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 4,
                            "cached_input_tokens": 0,
                            "output_tokens": 1,
                        },
                    }
                )
            )
        return 0

    print(json.dumps({"result": "done", "session_id": "fake-claude-session"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
