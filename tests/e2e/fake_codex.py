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
* ``FAKE_CODEX_OMIT_USAGE`` — the launch succeeds and returns a turn carrying no
  token accounting. oneharness persists that record with its usage counters
  unset, and the smoke's launch contract rejects it for reporting no
  ``input_tokens``. That is a broken recorded contract rather than weather, so
  the smoke must stop on it after one paid turn instead of buying the same
  verdict twice more.

Keep the spawned path deterministic and stdlib-only — this file *is* the provider
binary. The environment helpers below run only in the test process, so the one that
must name a product constant imports it there rather than respelling it here.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

#: One complete codex-shaped turn. Token accounting is not decoration here: a
#: record persisted without it fails the smoke's launch contract.
TURN: tuple[dict[str, object], ...] = (
    {"type": "turn.started"},
    {"type": "thread.started", "thread_id": "fake-codex-thread"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "smoke-ok"}},
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 4, "cached_input_tokens": 0, "output_tokens": 1},
    },
)


def unpinned_worker_side(environment: Mapping[str, str]) -> dict[str, str]:
    """Copy ``environment`` with the agent side's harness pin removed.

    This repository runs its own suite from inside a dispatch, and a dispatch
    exports the per-side worker selection to pin the identity its worker runs on.
    `scripts/oneharness-agent.sh` applies that pin over any `ONEHARNESS_HARNESSES` a
    journey sets — which is correct for a real dispatch and catastrophic here,
    because the pinned identity is a *variant*, no `ONEHARNESS_BIN_*` spelling
    reaches a variant, and the journey therefore spends a real paid turn while its
    double sits unused. Observed, not theorized: two `just smoke` runs billed a live
    subscription this way before the pin was found.

    The variable is named by `orchestrator.harnesses`, which declares it, rather
    than respelled here: a rename that moved the product constant while a copy in
    this file went on stripping the old name would strip nothing, and every journey
    would go back to spending that turn — with a green run and a billed one looking
    identical from the assertions. The import is deferred because this file is also
    *spawned* as the provider binary, and that path stays stdlib-only; only the test
    process ever calls this helper.
    """
    from orchestrator.harnesses import WORKER_HARNESS_ENV

    return {key: value for key, value in environment.items() if key != WORKER_HARNESS_ENV}


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
        **unpinned_worker_side(os.environ),
        # Selected rather than assumed: the fallback chain's first candidate is a
        # paid Claude subscription, and no journey may reach one.
        "ONEHARNESS_HARNESSES": "codex",
        "ONEHARNESS_BIN_CODEX": str(Path(__file__).resolve()),
        "FAKE_CODEX_ATTEMPT_LOG": str(attempt_log),
        "FAKE_CODEX_UNAVAILABLE_ATTEMPTS": str(unavailable_attempts),
        "FAKE_CODEX_OMIT_USAGE": "1" if omit_usage else "",
    }


def uninstalled_provider_environment() -> dict[str, str]:
    """Point a real `just smoke` at a codex binary that is not installed.

    The same narrowing and the same dropped pin as `provider_environment`, for the
    same reason: what makes a journey about a launch that cannot start cost nothing
    is the selection alone. Keeping the dispatch's pin would resolve a paid variant
    that starts perfectly well, and the journey would buy a turn to prove it.
    """
    return {
        **unpinned_worker_side(os.environ),
        "ONEHARNESS_HARNESSES": "codex",
        "ONEHARNESS_BIN_CODEX": "/does/not/exist/codex",
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
