"""The zero-work subscription rejection the agent side's fallback chain exists for.

A Claude subscription that is out of session quota does not say so plainly. It
answers with a terminal record that reads as a success — `subtype: "success"`, no
`is_error`, an empty answer — and declares the rejection only through
`terminal_reason: "api_error"` and an embedded `api_error_status` of `429`. Nothing
was spent: no tokens, no cost, no `modelUsage`.

The adopted oneharness must read that as *this candidate could not run the task at
all* and hand the turn to the next identity in `oneharness.toml`. Under the previous
pin it read as a transient `rate_limit` and stopped the chain, which cost a tracked
run all three of its round-01 nodes ("did not complete after 1 turn") and left
`--worker-harness` as the hand-routed workaround. These journeys are the contract
that retires it, and its boundary: a rejection carrying *work* still stops the
chain, because falling through one that already spent tokens would buy the same
work twice.

A real 429 is an external event no dispatch controls, so the proof is the record's
shape driven through the real wrapper, the real `oneharness.toml` chain, and the
real CLI. No status directory is exported, so the wrapper takes its `--events`
branch: the branch a turn nobody is watching takes, and the one whose failing exit
returns rather than parking for a dispatcher that does not exist here.
"""

# llmlint: ignore-file[e2e_not_mocked] the repository requires faking the paid agent
# harness and does it here at its designated seam (oneharness's own shipped mock
# responder for the rejecting candidate, `tests/e2e/fake_codex.py` for the one that
# takes over); the wrapper, the agent config, the fallback chain, the classifier and
# the report are all real.

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from mock_oneharness import main as _mock_oneharness_main
from waits import timeout

from orchestrator.root import REPO_ROOT

MOCK_ONEHARNESS = Path(_mock_oneharness_main.__globals__["__file__"]).resolve()
#: The candidate that takes the turn over: a real codex-shaped stream, this
#: repository's one faked boundary, which reports token accounting of its own so a
#: fall-through can be told from a chain that simply reported nothing.
FAKE_CODEX = REPO_ROOT / "tests" / "e2e" / "fake_codex.py"
#: The answer `fake_codex.py` returns, and therefore the evidence that the second
#: candidate really ran rather than the first one's empty result being reported.
FALLBACK_ANSWER = "smoke-ok"


class Accounting(TypedDict):
    """What a Claude terminal record says the turn spent."""

    input_tokens: int
    output_tokens: int


class ModelAccounting(TypedDict):
    """One model's share of that spend.

    camelCase because these are the wire names Claude Code writes, and the map being
    non-empty is one of the three things the classifier reads as work done — a
    record spelled our way would not be the record it has to classify.
    """

    inputTokens: int
    outputTokens: int


class Rejection(TypedDict):
    """A Claude Code terminal record that refuses the turn without saying so.

    The classification rests on the accounting rather than on any of the prose, so
    these fields are the whole input: what the harness claims happened, and what it
    charged for.
    """

    type: str
    subtype: str
    terminal_reason: str
    api_error_status: int
    result: str
    usage: Accounting
    total_cost_usd: float
    modelUsage: dict[str, ModelAccounting]


#: A subscription limit rejected before any of the task's work: the accounting is
#: the discriminator, so every counter it carries is zero and `modelUsage` is empty.
ZERO_WORK_REJECTION: Rejection = {
    "type": "result",
    "subtype": "success",
    "terminal_reason": "api_error",
    "api_error_status": 429,
    "result": "",
    "usage": {"input_tokens": 0, "output_tokens": 0},
    "total_cost_usd": 0.0,
    "modelUsage": {},
}
#: The identical rejection from a turn that had already been paid for. This is the
#: boundary the fall-through must not cross: the record says exactly the same thing
#: about the failure and something entirely different about what it cost.
WORKED_REJECTION: Rejection = {
    **ZERO_WORK_REJECTION,
    "usage": {"input_tokens": 1200, "output_tokens": 340},
    "total_cost_usd": 0.21,
    "modelUsage": {"claude-opus-5": {"inputTokens": 1200, "outputTokens": 340}},
}


@dataclass(frozen=True)
class Candidate:
    """One attempted candidate's verdict, as this journey reads it."""

    status: str
    failure_kind: str | None
    text: str | None
    output_tokens: int


@dataclass(frozen=True)
class Turn:
    """One agent-side turn: how it exited, and the single report it printed."""

    exit_code: int
    stdout: str
    stderr: str
    #: The candidate that executed, or `None` when none of them could.
    ran: str | None
    #: Every candidate the chain moved past, in priority order, with its reason.
    fell_through: tuple[tuple[str, str], ...]
    #: The candidates actually attempted, by harness id. A candidate the chain
    #: never reached is absent — which is how "its quota was never touched" is read.
    attempted: Mapping[str, Candidate]


def _read_turn(completed: subprocess.CompletedProcess[str]) -> Turn:
    """Model the one report the turn printed, refusing anything that is not one.

    onejudge parses this process's stdout as exactly one report, whichever candidate
    produced it, so a chain that fell through must not have added a line.
    """
    lines = completed.stdout.strip().splitlines()
    assert len(lines) == 1, completed.stdout
    report = json.loads(lines[0])
    fallback = report["fallback"]
    return Turn(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        ran=fallback["ran"],
        fell_through=tuple(
            (entry["harness"], entry["reason"]) for entry in fallback["fell_through"]
        ),
        attempted={
            result["harness_id"]: Candidate(
                status=result["status"],
                failure_kind=result["failure_kind"],
                text=result["text"],
                output_tokens=result["usage"]["output_tokens"],
            )
            for result in report["results"]
        },
    )


def _agent_turn(tmp_path: Path, oneharness_bin: str, rejection: Rejection) -> Turn:
    """Run one agent-side turn whose first candidate answers with `rejection`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    oneharness = bin_dir / "oneharness"
    if not oneharness.exists():
        oneharness.symlink_to(MOCK_ONEHARNESS)
    return _read_turn(
        subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "scripts" / "oneharness-agent.sh"),
                "run",
                "--compact",
                "--prompt",
                "answer this turn",
            ],
            text=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env={
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "HOME": str(tmp_path / "home"),
                "PYTHONPATH": str(REPO_ROOT / "tests" / "e2e"),
                "REAL_ONEHARNESS_BIN": oneharness_bin,
                # Narrowed to plain ids so the shipped responder can name the
                # candidate it replaces, exactly as the other real-CLI journeys
                # narrow it. The order, the `fallback` run mode and the classifier
                # all stay the agent config's own.
                "MOCK_HARNESSES": "claude-code",
                "ONEHARNESS_HARNESSES": "claude-code,codex",
                "MOCK_STDOUT": json.dumps(rejection),
                "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
            },
            timeout=timeout(60),
        )
    )


def test_a_zero_work_subscription_429_hands_the_turn_to_the_next_identity(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The adopted release classifies the rejection as `quota` and falls through."""
    turn = _agent_turn(tmp_path, oneharness_bin, ZERO_WORK_REJECTION)

    assert turn.exit_code == 0, turn.stderr
    assert turn.ran == "codex"
    assert turn.fell_through == (("claude-code", "quota"),)
    # `quota`, not the transient `rate_limit` that stopped the chain: the record's
    # own accounting says the candidate never got to the task.
    assert turn.attempted["claude-code"].failure_kind == "quota"
    # And the turn has a real answer, from the identity the chain moved on to.
    assert turn.attempted["codex"].status == "ok"
    assert turn.attempted["codex"].text == FALLBACK_ANSWER


def test_the_same_429_after_billed_work_still_stops_the_chain(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Work already paid for is never re-run on the next candidate's quota.

    The failure this fall-through is for and the one it must refuse are the same
    record apart from what the harness billed, so the classifier reading tokens
    rather than error text is the whole guarantee — and a fall-through that widened
    to cover this case would silently double every rejected turn's cost.
    """
    turn = _agent_turn(tmp_path, oneharness_bin, WORKED_REJECTION)

    assert turn.exit_code == 1
    assert turn.ran == "claude-code"
    assert turn.fell_through == ()
    assert turn.attempted["claude-code"].failure_kind == "rate_limit"
    assert turn.attempted["claude-code"].output_tokens == 340
    # The next identity was never reached, so its quota was never touched.
    assert "codex" not in turn.attempted
    assert FALLBACK_ANSWER not in turn.stdout
