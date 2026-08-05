#!/usr/bin/env python3
"""A stand-in for the paid Claude Code CLI, refusing the turn without running it.

The companion to `fake_codex.py` at the same designated boundary: pointing
``ONEHARNESS_BIN_CLAUDE_CODE`` here replaces exactly the paid provider, and the
real oneharness still selects it, spawns it, classifies its refusal, moves on to
the next identity, and writes both records into one history session. That chain
is what the smoke's judgment now rests on, so a journey that invented the history
instead would prove nothing about it.

A subscription that is out of quota does not say so plainly — it answers with a
terminal record that reads as a success and declares the rejection only through
`terminal_reason` and an embedded `api_error_status`, spending nothing. The
accounting is what oneharness classifies on, which is why every counter here is
zero; `tests/e2e/test_quota_fallthrough_e2e.py` is the journey that proves that
classification, and this file exists to put a real one in front of the smoke.

Keep this deterministic — it is spawned as a subprocess, so its only import beyond
the standard library is the sibling double it shares one safety helper with, which
sits in this same directory and is therefore always importable from it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from fake_codex import unpinned_worker_side

#: The three ways a chain's first candidate can step aside, as a closed set: a
#: misspelled one is a type error rather than a silent fall back to the quota shape
#: here and to the installed-binary branch in `chain_environment`.
Refusal = Literal["quota", "auth", "skipped"]

#: The zero-work subscription rejection, in Claude Code's own wire shape. Spelled
#: the way the provider spells it (`camelCase` `modelUsage`, an empty `result`)
#: because oneharness classifies the record the provider actually writes.
ZERO_WORK_REJECTION: dict[str, object] = {
    "type": "result",
    "subtype": "success",
    "terminal_reason": "api_error",
    "api_error_status": 429,
    "result": "",
    "usage": {"input_tokens": 0, "output_tokens": 0},
    "total_cost_usd": 0.0,
    "modelUsage": {},
}
#: The exit a real refusing subscription leaves behind, captured from this host's
#: own history: the turn failed, so the record is `nonzero` with exit code 1 — the
#: very shape the smoke used to read as launch breakage.
REFUSAL_EXIT_CODE = 1
#: What an identity nobody authenticated says instead, on stderr and nowhere else.
#: oneharness classifies this as `auth`, which is the second kind a chain steps
#: past — and the one whose record carries a null for every counter rather than the
#: zeros the quota shape reports.
UNAUTHENTICATED_REJECTION = "401 Unauthorized: no credentials"
#: Which rejection this double answers with, read from the environment because it is
#: spawned as the provider binary and has no other way to be told.
REFUSAL_ENV = "FAKE_CLAUDE_CODE_REFUSAL"
#: The candidate the chain never starts at all: not a rejection this double can
#: write, because a binary that is absent is what oneharness records as `skipped`.
UNINSTALLED_BIN = Path("/does/not/exist/claude")


def chain_environment(
    *,
    codex_bin: Path,
    attempt_log: Path,
    omit_usage: bool = False,
    refusal: Refusal = "quota",
) -> dict[str, str]:
    """Point a real `just smoke` at a chain whose first candidate refuses the turn.

    Named here for the same reason `fake_codex.provider_environment` is named
    there: a journey that misspelled one of these would get a silently *paid*
    smoke, and no journey may spend a real subscription's turn.

    The identities are deliberately the bare, variant-less ones. `ONEHARNESS_BIN_*`
    keys on a harness id, and there is no spelling of it that reaches a *variant*
    — `ONEHARNESS_BIN_CLAUDE_CODE` leaves `claude-code:alternate` resolving to the
    real `claude` — so a chain naming variants here would spawn the paid provider
    with the double sitting unused beside it.

    ``refusal`` picks which way the first candidate steps aside: ``quota`` and
    ``auth`` are answered by this double, and ``skipped`` points the candidate at a
    binary that is not there, because a chain only records that status for one it
    never started.
    """
    return {
        **unpinned_worker_side(os.environ),
        # Both candidates are replaced, so the chain can reach no paid provider by
        # any path — including the fall-through this journey is about.
        "ONEHARNESS_HARNESSES": "claude-code,codex",
        "ONEHARNESS_BIN_CLAUDE_CODE": str(
            UNINSTALLED_BIN if refusal == "skipped" else Path(__file__).resolve()
        ),
        REFUSAL_ENV: refusal,
        "ONEHARNESS_BIN_CODEX": str(codex_bin),
        "FAKE_CODEX_ATTEMPT_LOG": str(attempt_log),
        "FAKE_CODEX_UNAVAILABLE_ATTEMPTS": "0",
        "FAKE_CODEX_OMIT_USAGE": "1" if omit_usage else "",
    }


def main() -> int:
    """Answer the turn with the rejection this launch was asked for.

    The quota shape is written to stdout in whichever format oneharness asked for:
    `stream-json` and `json` differ here only in that the streamed form is one JSON
    document per line, and the terminal record oneharness classifies is the same
    object either way. An unauthenticated identity never gets that far — it fails
    before it can answer, and says so on stderr alone.
    """
    if os.environ.get(REFUSAL_ENV) == "auth":
        print(UNAUTHENTICATED_REJECTION, file=sys.stderr)
        return REFUSAL_EXIT_CODE
    json.dump(ZERO_WORK_REJECTION, sys.stdout)
    if "stream-json" in sys.argv:
        sys.stdout.write("\n")
    sys.stdout.flush()
    return REFUSAL_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
