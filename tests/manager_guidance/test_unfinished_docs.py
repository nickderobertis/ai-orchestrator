"""The prose describing `just unfinished`, reconciled to the module that declares it.

`docs/orchestration.md` states the recipe's flags and its status vocabulary because a
manager reads them there, and `AGENTS.md`'s watch rule names the answer a turn ends on.
`orchestrator/unfinished.py` is the one declaration of each, so every flag and status
the prose uses is read back against it: one the module does not have fails here rather
than reading as true.
"""

from __future__ import annotations

import argparse
import re

from orchestrator import unfinished
from orchestrator.root import REPO_ROOT

ORCHESTRATION = REPO_ROOT / "docs" / "orchestration.md"
MANAGER = REPO_ROOT / "AGENTS.md"
SECTION_HEADING = "### Asking what this session still owes"
STATUS_PARAGRAPH = re.compile(
    r"\*\*The\s+status\s+is\s+the\s+whole\s+of\s+what\s+a\s+caller\s+branches\s+on\*\*.*?\n\n", re.S
)
BACKTICKED_CODE = re.compile(r"`(\d+)`")
UNFINISHED_OPTION = re.compile(r"just unfinished((?: --[a-z-]+(?: [A-Z]+)?)*)")


def _section() -> str:
    text = ORCHESTRATION.read_text(encoding="utf-8")
    start = text.index(SECTION_HEADING)
    return text[start : text.index("\n### ", start + len(SECTION_HEADING))]


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {option for action in parser._actions for option in action.option_strings}


def test_the_statuses_the_section_states_are_the_ones_declared() -> None:
    paragraph = STATUS_PARAGRAPH.search(_section())
    assert paragraph is not None, "the section no longer states the status vocabulary"
    stated = {int(code) for code in BACKTICKED_CODE.findall(paragraph.group(0))}
    declared = {status.code for status in unfinished.EXIT_STATUSES}
    # The unanswered status is stated as "any other status", which is what a caller may
    # rely on; the number itself is the module's.
    assert stated == declared - {unfinished.UNANSWERED}


def test_every_flag_the_prose_names_is_one_the_recipe_accepts() -> None:
    accepted_unfinished = _options(unfinished._parser())
    for name, prose in (("orchestration", _section()), ("AGENTS.md", MANAGER.read_text("utf-8"))):
        for flags in UNFINISHED_OPTION.findall(prose):
            named = set(re.findall(r"--[a-z-]+", flags))
            assert named <= accepted_unfinished, f"{name} names {sorted(named)}"


def test_the_watch_rule_names_the_read_a_turn_ends_on_and_its_ways_out() -> None:
    rule = " ".join(MANAGER.read_text(encoding="utf-8").split())
    start = rule.index("1. **A watch is armed before you turn to anything else.**")
    first = rule[start : rule.index("2. **", start)]
    assert "`just unfinished`" in first
    assert f"answers `{unfinished.NOTHING_OWED}`" in first
    assert "just unpublished --acknowledge" in first and "--reason" in first
    # And the two the engine's listing prints beside it: the landing and the reclaim.
    assert "land the branch with the command its row prints" in first
    assert "reclaim a superseded one with the reclaim line its row prints" in first
