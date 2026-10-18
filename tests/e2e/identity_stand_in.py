#!/usr/bin/env python3
"""A `codex` and `claude` stand-in that records the identity each candidate was handed.

Named by `ONEHARNESS_BIN_CODEX` and `ONEHARNESS_BIN_CLAUDE_CODE`, it stands in for every
provider a oneharness chain spawns, variants included — by absolute path, because a
`PATH` entry is shadowed under an Nx task, whose `PATH` puts `node_modules/.bin` and Node's
own bin directory, where a host's npm-installed `codex` and `claude` live, ahead of the
caller's. Each launch appends one JSON line to the file
``IDENTITY_RECORDINGS`` names: the binary, ``CODEX_HOME`` and ``CLAUDE_CONFIG_DIR`` — the
indirection each variant maps into its child — and every ``ORCHESTRATOR_*`` identity
variable it inherited. Every launch then refuses the way a host that never logged that
identity in refuses, which oneharness classifies ``auth`` and falls through, except the
one whose ``CLAUDE_CONFIG_DIR`` is ``IDENTITY_ANSWERING_DIR``: that one answers an llmlint
judge batch in claude-code's result shape, every rule holding, through
``tests/e2e/fake_codex.py``'s own batch reader, which ``IDENTITY_FAKE_CODEX`` names. So one
judged run walks the whole chain and records each identity's directory reaching its child.
With neither variable set it records nothing and refuses every launch, which is how a
journey answered by another stand-in keeps every Claude candidate off a paid provider.

Keep this deterministic and stdlib-only — this file *is* the provider binary.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

#: What a version probe is spelled as, answered so a candidate is not skipped as absent.
VERSION_PROBE = frozenset({"--version", "-v"})


def record(binary: str) -> None:
    recordings = os.environ.get("IDENTITY_RECORDINGS")
    if not recordings:
        return
    seen = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("ORCHESTRATOR_") or key in {"CODEX_HOME", "CLAUDE_CONFIG_DIR"}
    }
    with open(recordings, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"binary": binary, "environment": seen}) + "\n")


def refuse(binary: str) -> int:
    """Refuse as an identity nobody logged in, which oneharness reads as `auth`."""
    if binary == "codex":
        print(json.dumps({"type": "error", "message": "401 Unauthorized: not logged in"}))
        print("ERROR: 401 Unauthorized: Not logged in", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "num_turns": 1,
                "result": "Not logged in · Please run /login",
                "session_id": "identity-stand-in-refusal",
                "total_cost_usd": 0,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        )
    )
    return 1


def answer(argv: list[str]) -> int:
    """Answer the llmlint batch the turn carries, every rule holding.

    claude-code takes the instruction as ``-p``'s value and the batch in another argument
    or on stdin, so every argument and stdin are read for it.
    """
    spec = importlib.util.spec_from_file_location("fake_codex", os.environ["IDENTITY_FAKE_CODEX"])
    assert spec is not None and spec.loader is not None
    fake_codex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake_codex)
    candidates = [*argv, sys.stdin.read()]
    batch = next(
        (found for found in map(fake_codex.llmlint_batch, candidates) if found is not None),
        None,
    )
    if batch is None:
        print("identity stand-in: the answering turn carried no llmlint batch", file=sys.stderr)
        return 1
    verdict = json.loads(fake_codex.llmlint_verdict(batch))
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": json.dumps(verdict),
                "structured_output": verdict,
                "session_id": "identity-stand-in",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )
    )
    return 0


def provider(argv: list[str], invoked: str) -> str:
    """Which provider this launch stands in for: its name on `PATH`, else its argv's shape.

    Named by `ONEHARNESS_BIN_CODEX` or `ONEHARNESS_BIN_CLAUDE_CODE`, it is invoked by this
    file's own name, and codex is the one launched as `codex exec`.
    """
    if invoked in {"codex", "claude"}:
        return invoked
    return "codex" if argv[:1] == ["exec"] else "claude"


def main(argv: list[str], invoked: str) -> int:
    binary = provider(argv, invoked)
    if VERSION_PROBE.intersection(argv):
        print("0.0.0 (identity stand-in)")
        return 0
    record(binary)
    answering = os.environ.get("IDENTITY_ANSWERING_DIR")
    if binary == "claude" and answering and os.environ.get("CLAUDE_CONFIG_DIR") == answering:
        return answer(argv)
    return refuse(binary)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], os.path.basename(sys.argv[0])))
