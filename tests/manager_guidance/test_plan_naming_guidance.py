"""The manager's brief guidance states how a plan's title and slug are written.

The brief's H1 becomes the plan's title, which `just plan` and `just finish-plan` turn into
the planning and finishing projects' titles and the engine into every run id the navigation
lists (`tests/e2e/planning/test_plan_names_e2e.py` drives that). Only the manager writes the
H1 and the `Plan project:` slug, so the rule for both lives in the one step of `AGENTS.md`
that tells the manager how to write a brief, and nowhere else in that file.
"""

from __future__ import annotations

import pytest

from orchestrator.root import REPO_ROOT

AGENTS = REPO_ROOT / "AGENTS.md"
STEP = "2. **Write the planner's brief.**"
NEXT_STEP = "3. **Answer its questions while it plans.**"

#: The rule's own words, one per property it states.
RULE = (
    "**The brief's H1 is the plan's title**, naming its runs",
    "natural language opening with the subject — what the work changes —",
    "in words and spaces only, as the run id turns each colon, comma or spaced dash into more `-`",
    "the slug opens with the same words, any date last",
)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _brief_step() -> str:
    text = AGENTS.read_text(encoding="utf-8")
    start = text.index(STEP)
    return _flat(text[start : text.index(NEXT_STEP, start)])


@pytest.mark.parametrize("statement", RULE)
def test_the_brief_step_states_the_naming_rule(statement: str) -> None:
    assert statement in _brief_step(), (
        f"AGENTS.md's 'Write the planner's brief' step no longer says {statement!r}; the "
        "manager writes the brief's H1 and slug, which every name the navigation lists comes "
        "from, so the rule has to stay where a manager writes a brief"
    )


def test_the_naming_rule_is_stated_once() -> None:
    whole = _flat(AGENTS.read_text(encoding="utf-8"))
    assert whole.count(RULE[0]) == 1
