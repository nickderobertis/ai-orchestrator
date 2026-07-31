#!/usr/bin/env python3
"""A stand-in for the paid codex CLI: this repository's one faked boundary.

oneharness runs the selected harness's own binary, so pointing
``ONEHARNESS_BIN_CODEX`` here replaces exactly the paid provider and nothing else.
The real oneharness still selects it, spawns it, parses its stream, times and
prices the turn, and writes the history record the launch contract is read back
out of — which is what a smoke journey has to keep real to mean anything.

``FAKE_CODEX_ATTEMPT_LOG`` names a file this appends one line to per launch, and
the first ``FAKE_CODEX_UNAVAILABLE_ATTEMPTS`` launches then die the way a contended
host made the real provider die: started, and then failed. *Which* turn contention
kills is not something generating load can decide, so the count is what makes "the
first launch failed and a later one did not" a deterministic journey while every
other party in it stays real.

Keep this deterministic and stdlib-only — it is spawned as a subprocess.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: One complete codex-shaped turn. Token accounting is not decoration here:
#: without it oneharness declines to persist a history record at all, and the
#: smoke would then fail for something it is not about.
TURN = (
    {"type": "turn.started"},
    {"type": "thread.started", "thread_id": "fake-codex-thread"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "smoke-ok"}},
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 4, "cached_input_tokens": 0, "output_tokens": 1},
    },
)


def _refuses_this_launch() -> bool:
    """Record this launch, and report whether it is one of the failing ones."""
    log = os.environ.get("FAKE_CODEX_ATTEMPT_LOG")
    if log is None:
        return False
    path = Path(log)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("launch\n")
    launches = len(path.read_text(encoding="utf-8").splitlines())
    return launches <= int(os.environ.get("FAKE_CODEX_UNAVAILABLE_ATTEMPTS", "0"))


def main() -> int:
    if _refuses_this_launch():
        print("fake_codex: the provider started and then failed", file=sys.stderr)
        return 1
    for event in TURN:
        print(json.dumps(event), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
