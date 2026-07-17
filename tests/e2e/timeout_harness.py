#!/usr/bin/env python3
"""Npm-launcher-shaped fixture for the real oneharness timeout boundary.

The launcher starts a TERM-ignoring descendant which emits a partial OpenCode
JSONL transcript, then keeps inherited stdout/stderr pipes open while it ticks.
oneharness must terminate the whole process tree rather than only this launcher.
"""

from __future__ import annotations

import os
import subprocess
import sys

TRANSCRIPT = "".join(
    [
        '{"type":"text","sessionID":"ses-timeout","part":'
        '{"type":"text","text":"partial answer"}}\n',
        '{"type":"tool_use","sessionID":"ses-timeout","part":'
        '{"type":"tool","tool":"bash","state":'
        '{"input":{"command":"echo hi"},"output":"hi"}}}\n',
        '{"type":"step_finish","sessionID":"ses-timeout","part":'
        '{"cost":0.01,"tokens":{"input":12,"output":3,'
        '"cache":{"read":9,"write":4}}}}\n',
        '{"type":"task_complete","text":"emitted before exit"}\n',
        '{"type":"incomplete"',
    ]
)


def main() -> int:
    tick_file = os.environ.get("TIMEOUT_HARNESS_TICK_FILE")
    if not tick_file:
        print("TIMEOUT_HARNESS_TICK_FILE is required", file=sys.stderr)
        return 2
    child_env = {
        **os.environ,
        "TIMEOUT_HARNESS_STDOUT": TRANSCRIPT,
        "TIMEOUT_HARNESS_STDERR": "native child stderr\n",
    }
    script = r"""
trap '' TERM
printf '%s' "$TIMEOUT_HARNESS_STDOUT"
printf '%s' "$TIMEOUT_HARNESS_STDERR" >&2
i=0
while [ "$i" -lt 100 ]; do
    printf x >> "$TIMEOUT_HARNESS_TICK_FILE"
    i=$((i + 1))
    sleep 0.05
done
"""
    completed = subprocess.run(["sh", "-c", script], env=child_env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
