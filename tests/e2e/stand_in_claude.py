#!/usr/bin/env python3
"""A `claude` stand-in that records that it, and not a real binary, ran.

Each invocation appends one JSON line to the file `STAND_IN_CLAUDE_RECORD` names —
its argv and the `CLAUDE_CONFIG_DIR` it was handed, which is the variant's own
identity routing — then answers the prompt with the one `result` line claude-code's
output carries. It reads no credential and reaches no network, so a journey that
selected it spent nothing.
"""

from __future__ import annotations

import json
import os
import sys

with open(os.environ["STAND_IN_CLAUDE_RECORD"], "a", encoding="utf-8") as record:
    record.write(
        json.dumps({"argv": sys.argv, "claude_config_dir": os.environ.get("CLAUDE_CONFIG_DIR")})
        + "\n"
    )
sys.stdin.read()
print(
    json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "stand-in answered",
            "session_id": "stand-in-session",
        }
    )
)
