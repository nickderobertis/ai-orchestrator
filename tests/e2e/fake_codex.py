#!/usr/bin/env python3
"""A stand-in for the paid codex CLI: this repository's one faked boundary.

oneharness runs the selected harness's own binary, so pointing
``ONEHARNESS_BIN_CODEX`` here replaces exactly the paid provider and nothing else.
The real oneharness still selects it, spawns it, parses its stream, times and
prices the turn, and writes the history record the launch contract is read back
out of — which is what a smoke journey has to keep real to mean anything.

``FAKE_CODEX_ATTEMPT_LOG`` names a file this appends one line to per launch, which
is how a journey counts the paid turns a smoke actually spent. The two failure
modes it can be asked for are the two the smoke has to tell apart:

* ``FAKE_CODEX_UNAVAILABLE_ATTEMPTS`` — the first N launches die the way a
  contended host made the real provider die: started, and then failed. *Which*
  turn contention kills is not something generating load can decide, so the count
  is what makes "the first launch failed and a later one did not" deterministic
  while every other party stays real.
* ``FAKE_CODEX_OMIT_USAGE`` — the launch succeeds and returns a turn oneharness
  cannot fully account for, so no history record is persisted. That is a broken
  recorded contract rather than weather, and the smoke must stop on it after one
  paid turn instead of buying the same verdict twice more.

Keep this deterministic and stdlib-only — it is spawned as a subprocess.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: One complete codex-shaped turn. Token accounting is not decoration here:
#: without it oneharness declines to persist a history record at all.
TURN: tuple[dict[str, object], ...] = (
    {"type": "turn.started"},
    {"type": "thread.started", "thread_id": "fake-codex-thread"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "smoke-ok"}},
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 4, "cached_input_tokens": 0, "output_tokens": 1},
    },
)


def provider_environment(
    *,
    attempt_log: Path,
    unavailable_attempts: int = 0,
    omit_usage: bool = False,
) -> dict[str, str]:
    """Point a real `just smoke` at this double instead of at a paid provider.

    Defined here rather than in each journey so the variable names above have one
    source: a caller that misspells one would otherwise get a silently *real*
    smoke, which is exactly what these journeys must never spend.
    """
    return {
        **os.environ,
        # Selected rather than assumed: the fallback chain's first candidate is a
        # paid Claude subscription, and no journey may reach one.
        "ONEHARNESS_HARNESSES": "codex",
        "ONEHARNESS_BIN_CODEX": str(Path(__file__).resolve()),
        "FAKE_CODEX_ATTEMPT_LOG": str(attempt_log),
        "FAKE_CODEX_UNAVAILABLE_ATTEMPTS": str(unavailable_attempts),
        "FAKE_CODEX_OMIT_USAGE": "1" if omit_usage else "",
    }


def record_launch() -> int | None:
    """Append this launch to the attempt log, returning how many it now holds."""
    log = os.environ.get("FAKE_CODEX_ATTEMPT_LOG")
    if log is None:
        return None
    path = Path(log)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("launch\n")
    return len(path.read_text(encoding="utf-8").splitlines())


def turn() -> tuple[dict[str, object], ...]:
    """The stream this launch emits, with token accounting withheld on request."""
    if os.environ.get("FAKE_CODEX_OMIT_USAGE") != "1":
        return TURN
    return tuple({key: value for key, value in event.items() if key != "usage"} for event in TURN)


def main() -> int:
    launches = record_launch()
    unavailable = int(os.environ.get("FAKE_CODEX_UNAVAILABLE_ATTEMPTS") or "0")
    if launches is not None and launches <= unavailable:
        print("fake_codex: the provider started and then failed", file=sys.stderr)
        return 1
    for event in turn():
        print(json.dumps(event), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
