"""The half of `tests/test_watch_surface_drift.py` that reads this repository's prose.

It sits in the `manager-guidance` project.

These hold the watch rule `AGENTS.md` states to the installed engine. That project's key
names each file they read, and `tests/conftest.py` holds them to it; the tests of that
module that read no prose stay there, in `orchestrator:test`, and those reading a registered
checkout in `orchestrator:test-checkouts`.
"""

from __future__ import annotations

from test_watch_surface_drift import Terminal, _restated_statuses, disagreements


def test_the_reconciliation_names_a_swapped_and_an_unknown_exit_status() -> None:
    """The comparison bites on every shape of drift, and is quiet on agreement.

    A swap is the shape that keeps every number and changes what two of them mean, and
    it is the one a check that only asked whether a status was known cannot see — so it
    is asserted first and by name.
    """
    restated = _restated_statuses()
    agreeing = {
        f"run-{status}": Terminal(condition=condition, exit=status)
        for status, condition in restated.items()
    }

    assert disagreements(restated, agreeing) == []

    moved = sorted(restated)[-2:]
    swapped = dict(agreeing) | {
        f"run-{moved[0]}": Terminal(condition=restated[moved[1]], exit=moved[0]),
        f"run-{moved[1]}": Terminal(condition=restated[moved[0]], exit=moved[1]),
    }
    named = disagreements(restated, swapped)
    assert len(named) == 2
    for status in moved:
        assert any(f"exit status {status}" in entry for entry in named)

    # One past every status the rule names, so the example stays unknown as the rule grows.
    unused = max(restated) + 1
    unknown = {f"run-{unused}": Terminal(condition="interrupted", exit=unused)}
    assert disagreements(restated, unknown) == [
        f"the verb answered `interrupted` at exit status {unused} on `run-{unused}`, and this "
        "repository branches on no such status"
    ]
