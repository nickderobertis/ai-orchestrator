"""The operational appendix may not contradict itself about the complete gate.

`config/dispatch-appendix.md` is the text every dispatched task carries, and it was
gitignored scratch propagated by copy-paste until this suite tracked it. That is not
incidental to what went wrong in it: it named a three-part chain as the complete gate
to be run *once*, and then, four paragraphs later, demonstrated waiting on a gate with
a sentinel that backgrounded only two of those three parts. A worker that obeyed both
ran the parts separately and was failed for it, in its judge's own words: *"The
required complete gate (...) was never run once end-to-end; its components were run
separately."*

Nothing could have caught that, because nothing read the file. What is asserted here
is the property the two passages have to share rather than either one's wording: the
complete gate is **named once**, and every worked example that runs or waits on a gate
runs that name and never a part of the chain behind it. A future edit is free to change
what the chain is; it is not free to leave two passages disagreeing about it again.

The cheap-iteration rule is held the same way — by position rather than by phrasing —
because the cost of burying it is measured: one node lost about 84 minutes looping on
the whole gate to learn its lint findings, after the rule had already been written down.

`tests/test_criteria_guard.py` proves the guard that reads this file; here the subject
is the file itself, which is why these belong to the tier keyed on this repository's
prose.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.criteria_guard import APPENDIX
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The one definition every other passage has to call instead of spelling out.
DEFINITION = re.compile(r"^ *complete_gate\(\) *\{(?P<chain>[^}]*)\}", re.MULTILINE)

#: A command put into the background, which is what a sentinel example does. The
#: chain is what has to be in here, and this is where the contradiction lived.
BACKGROUNDED = re.compile(r"\(\s*(?P<command>[^()]*?)\s*\)\s*&")

#: Any single step of the chain, named directly. Legitimate in the definition and in
#: the cheap loop, and nowhere near a sentinel: the whole failure was a sentinel that
#: named two of these instead of the gate they compose into.
CHAIN_STEP = re.compile(r"\bjust +(?:bootstrap|gate|lint-llm-diff)\b")


@pytest.fixture(scope="module")
def appendix() -> str:
    return (REPO_ROOT / APPENDIX).read_text(encoding="utf-8")


def test_the_complete_gate_is_defined_exactly_once(appendix: str) -> None:
    """One definition is what makes agreement between passages structural.

    Two spellings of the chain is the shape the contradiction had: each was locally
    reasonable, and nothing connected them. A reader following this file cannot end
    up running something other than what it calls complete if there is only one thing
    to run.
    """
    definitions = DEFINITION.findall(appendix)

    assert len(definitions) == 1, (
        f"{APPENDIX} defines the complete gate {len(definitions)} times; two spellings "
        "of the chain is exactly how its two passages came to disagree about what "
        "running it means"
    )


def test_the_definition_composes_every_step_of_the_chain(appendix: str) -> None:
    """A definition that dropped a step would be the same defect, moved."""
    chain = DEFINITION.search(appendix)
    assert chain is not None, f"{APPENDIX} no longer defines `complete_gate`"

    for step in ("just bootstrap", "just gate", "just lint-llm-diff"):
        assert step in chain["chain"], (
            f"{APPENDIX}'s complete gate no longer composes {step!r}, so a worker that "
            f"runs it has not run what this file calls complete:\n{chain['chain']}"
        )


def test_every_worked_example_waits_on_the_whole_chain(appendix: str) -> None:
    """The passage that failed a node, held to the passage that defines the gate.

    The sentinel example backgrounded `just bootstrap && just gate` — two of the
    three parts the prose four paragraphs above called complete. This asserts the
    class rather than the wording: whatever is put into the background has to be the
    gate's own name, and naming any step of the chain there is the defect returning
    under new spelling.
    """
    backgrounded = BACKGROUNDED.findall(appendix)

    assert backgrounded, f"{APPENDIX} no longer shows how to wait on a gate at all"
    for command in backgrounded:
        assert "complete_gate" in command, (
            f"{APPENDIX} backgrounds {command!r}, which is not the command it calls "
            "complete; a worker that waits on this has waited on something else"
        )
        named = CHAIN_STEP.search(command)
        assert named is None, (
            f"{APPENDIX} backgrounds {named.group(0)!r} rather than the whole gate "
            f"({command!r}) — which is the contradiction that failed a node for "
            "running the gate's components separately"
        )


def test_the_cheap_iteration_rule_is_the_first_thing_a_reader_meets(appendix: str) -> None:
    """Burying it has a measured cost, so its position is part of what it says."""
    first = re.search(r"\*\*(?P<rule>[^*]+)\*\*", appendix)
    assert first is not None, f"{APPENDIX} leads with no emphasized rule at all"

    rule = " ".join(first["rule"].split())
    assert "Iterate on the judged tier alone" in rule, (
        f"{APPENDIX} now leads with {rule!r}; the cheap-iteration rule is what this "
        "file exists to teach and it cost a node 84 minutes the last time it was not "
        "the first thing read"
    )


def test_the_rule_still_says_to_run_the_complete_gate_once_at_the_end(appendix: str) -> None:
    """The other half of the rule: the cheap loop is the judged tier, not the gate."""
    assert "just lint-llm-diff" in appendix
    assert re.search(r"exactly \*\*once\*\*, at the end", appendix), (
        f"{APPENDIX} no longer says the complete gate is run once at the end, which is "
        "the half of the rule that stops a worker looping on it"
    )


def test_the_appendix_asks_for_the_bar_to_be_stated_as_criteria(appendix: str) -> None:
    """The demands a judge imports when the criteria are silent, named here as criteria.

    Two branches were failed by demands nobody wrote down: end-to-end proof, which the
    built-in `engineer` bar makes of every implementation dispatch, and a final
    completion report, which is in neither the task nor the shared clause. Asking for
    both here is what makes `just check-plan` refuse a task that omits them — the guard
    enforces a demand where it is made, and this file is one of the two places it reads.
    """
    for demand in ("proven end to end", "completion report"):
        assert demand in appendix, (
            f"{APPENDIX} no longer asks for {demand!r} as an acceptance criterion, so a "
            "node that omits it is judged on the bar's own reading of it instead"
        )
