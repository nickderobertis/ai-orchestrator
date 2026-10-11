"""The half of `tests/test_observer_grounding.py` that reads this repository's prose.

It sits in the `manager-guidance` project.

These hold the grounding rule to the place an operator reads it. That project's key names
each file they read, and `tests/conftest.py` holds them to it; the tests of that module that
read no prose stay there, in `orchestrator:test`.
"""

from __future__ import annotations

from test_observer_grounding import DOCUMENTED, OBSERVER_PERSONAS, SECTION


def test_the_rule_is_explained_where_an_operator_reads_it() -> None:
    """Four instructed sides, one explanation, and the explanation names them all."""
    written = " ".join(DOCUMENTED.read_text(encoding="utf-8").split())

    assert " ".join(SECTION.split()) in written, (
        f"{DOCUMENTED.name} no longer carries {SECTION!r}; the personas would instruct a "
        "rule an operator reading a surfaced finding has nowhere to look up"
    )
    for persona in OBSERVER_PERSONAS:
        assert f"personas/{persona}" in written, (
            f"{DOCUMENTED.name} explains the grounding rule without naming personas/"
            f"{persona} as a place it is stated, so that side can be dropped unnoticed"
        )
