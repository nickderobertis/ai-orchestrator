"""How a relaunch names its conversation, and what it is given instead of resuming one.

The seed itself is read back through the real `oneharness history` CLI, so its
rendering and bounding are proven against a real store in
tests/e2e/test_relaunch_seed_e2e.py, and the whole journey — a workstream that dies
to a provider and is relaunched — in tests/e2e/test_lifecycle_e2e.py. What is here
is the naming rule and the degradation that keeps a missing store from holding up a
relaunch at all.
"""

from __future__ import annotations

import pytest

from orchestrator.history import HistoryError
from orchestrator.relaunch import (
    RELAUNCH_MARK,
    relaunch_session,
    seeded_task,
    transcript_seed,
)

SESSION = "feature/branch@0123456789:main"


def test_attempt_zero_keeps_the_conversation_it_always_had() -> None:
    """The turn-cap resume is not a relaunch and must continue its own session."""
    assert relaunch_session(SESSION, 0) == SESSION
    assert relaunch_session(SESSION, -1) == SESSION


def test_each_relaunch_names_a_conversation_of_its_own() -> None:
    assert relaunch_session(SESSION, 1) == f"{SESSION}{RELAUNCH_MARK}1"
    assert relaunch_session(SESSION, 2) != relaunch_session(SESSION, 1)


def test_a_history_store_this_host_cannot_read_seeds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seed improves a relaunch; it is never a precondition for one.

    The one branch a real store cannot produce on demand: oneharness absent from the
    path entirely. Reaching it must answer "no seed" rather than raise, because the
    caller is already recovering from a provider that refused.
    """

    def refuse(**_kwargs: object) -> list[object]:
        raise HistoryError("oneharness not found — run 'just bootstrap'")

    monkeypatch.setattr("orchestrator.relaunch.all_sessions", refuse)

    assert transcript_seed(SESSION) is None


def test_the_seeded_task_says_the_prior_session_is_gone_and_keeps_the_task_last() -> None:
    """Said plainly, because being handed a session that does not exist is the bug."""
    seeded = seeded_task("## What\nShip it.\n", dead_session=SESSION, seed="asked: do the thing")

    assert SESSION in seeded
    assert "NOT being resumed" in seeded
    assert "asked: do the thing" in seeded
    assert seeded.endswith("## What\nShip it.\n")


def test_no_seed_leaves_the_task_exactly_as_it_was() -> None:
    """A relaunch with nothing to carry behaves exactly as it did before this existed."""
    assert seeded_task("do the work", dead_session=SESSION, seed=None) == "do the work"
    assert seeded_task("do the work", dead_session=SESSION, seed="") == "do the work"
