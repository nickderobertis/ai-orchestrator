"""`just smoke` over a real fallback chain whose first candidate refuses the turn.

The journey the smoke's judgment now rests on, driven end to end: the real recipe,
the real `scripts/oneharness-agent.sh`, the real `oneharness.toml` chain, the real
CLI, the real classifier, and the isolated history store the launch contract is
read back out of. What that chain writes — every candidate it attempted, in
priority order, in ONE session, with the selected one last — is exactly the shape
`orchestrator.smoke._selected` splits, so a journey that hand-wrote the history
would be asserting against its own assumption rather than against oneharness.

`tests/e2e/test_smoke_validation_e2e.py` covers the reading side over stored
records; this is the other half, and it is the half that would have caught the
original bug. While `claude-code:alternate`'s weekly quota was gone, the chain
correctly handed every turn to the next identity and the smoke reported the
refusal as launch breakage — blocking, through the pre-push hook, publication of
work that had already passed its gate.

llmlint: ignore-file[e2e_not_mocked] The paid agent harness is the one boundary
this repository fakes, and both candidates are replaced at its designated
`ONEHARNESS_BIN_*` seam; the recipe, the wrapper, oneharness, the fallback chain,
the classifier, the history store and the launch contract all run for real.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from fake_claude_code import chain_environment
from fake_codex import (
    WORKER_SIDE_SELECTION,
    provider_environment,
    uninstalled_provider_environment,
)
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT

#: The double that takes the turn over once the first candidate has refused it. It
#: reports token accounting of its own, which is how a real fall-through is told
#: from a chain that simply reported nothing.
FAKE_CODEX = REPO_ROOT / "tests" / "e2e" / "fake_codex.py"


def _run_smoke(tmp_path: Path, *, omit_usage: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the public recipe against a chain that must never reach a paid provider."""
    return subprocess.run(
        ["just", "smoke"],
        cwd=REPO_ROOT,
        env=chain_environment(
            codex_bin=FAKE_CODEX,
            attempt_log=tmp_path / "harness-attempts",
            omit_usage=omit_usage,
        ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )


def _launches(tmp_path: Path) -> int:
    """How many turns the candidate that took over was actually asked to run."""
    log = tmp_path / "harness-attempts"
    return len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0


@pytest.mark.parametrize(
    "build",
    [
        lambda path: chain_environment(codex_bin=FAKE_CODEX, attempt_log=path),
        lambda path: provider_environment(attempt_log=path),
        lambda _path: uninstalled_provider_environment(),
    ],
    ids=["refusing-chain", "single-candidate", "uninstalled-provider"],
)
def test_no_smoke_journey_inherits_the_dispatch_s_harness_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build: Callable[[Path], dict[str, str]]
) -> None:
    """No journey may run on the identity its dispatch pinned, because that one is paid.

    This repository runs its own suite from inside a dispatch, which exports
    `ORCHESTRATOR_WORKER_HARNESSES`; the wrapper applies it over the
    `ONEHARNESS_HARNESSES` a journey sets, and no `ONEHARNESS_BIN_*` spelling
    reaches the *variant* it names. So a journey that inherited it would spawn a
    live subscription with its double sitting unused — which is not a failure the
    journeys above can detect, since a green run and a billed one look identical
    from here. Asserted rather than observed, for that reason.

    The three builders parametrized here are every environment a smoke journey
    launches through, across this file, `test_smoke_validation_e2e.py`, and
    `test_smoke_contention_e2e.py`; a journey that spelled its own selection inline
    would be outside this guard, which is why none of them do.
    """
    monkeypatch.setenv(WORKER_SIDE_SELECTION, "claude-code:alternate2")

    environment = build(tmp_path / "harness-attempts")

    assert WORKER_SIDE_SELECTION not in environment
    assert environment["ONEHARNESS_HARNESSES"].split(",")[0] in ("claude-code", "codex")


def test_smoke_passes_when_the_chain_falls_through_a_refused_candidate(tmp_path: Path) -> None:
    """A refused subscription the chain moved past is the fallback working.

    The whole point of the change: the launch path is healthy precisely because the
    chain refused one identity and ran the next, so the record that decides the
    verdict is the selected one's.
    """
    result = _run_smoke(tmp_path)

    assert result.returncode == 0, result.stderr
    # Named on the pass, not swallowed: which subscription is gone is the operator's
    # business even when the verdict is green.
    assert "smoke: fell through claude-code (quota)" in result.stdout
    assert "the fallback chain handed the turn to the next identity" in result.stdout
    assert "smoke: passed via codex" in result.stdout
    # One turn, not one per candidate: the refusal cost nothing and was not retried.
    assert _launches(tmp_path) == 1


def test_smoke_still_fails_when_the_selected_candidate_breaks_the_contract(
    tmp_path: Path,
) -> None:
    """Falling through excuses the candidate that stepped aside, never the one that ran.

    The same real chain and the same real fall-through as above; only the record the
    turn actually produced is unaccounted for. A smoke that read the fall-through as
    permission to relax would report this launch as healthy.
    """
    result = _run_smoke(tmp_path, omit_usage=True)

    assert result.returncode == 1
    assert "smoke: real harness history reports no input_tokens" in result.stderr
    assert "rerun 'just smoke'" in result.stderr
    # A broken recorded contract is a regression, not weather, so it is not retried.
    assert _launches(tmp_path) == 1
