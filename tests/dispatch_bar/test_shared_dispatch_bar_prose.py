"""The half of `tests/test_shared_dispatch_bar.py` that reads this repository's prose.

It sits in the `dispatch-bar` project.

These hold the shared dispatch bar to the operational notes every dispatch reads. That
project's key names each file they read, and `tests/conftest.py` holds them to it; the tests
of that module that read no prose stay there, in `orchestrator:test`.
"""

from __future__ import annotations

import pytest
from shared_dispatch_bar import (
    BASE_CONFIG,
    appendix_text,
    phrases_in_both,
    shared_agent_preamble,
    shared_completion_bar,
)
from test_dispatch_appendix import DOWNSTREAM_CHECKS_ARE_NOT_JUDGED, REPORT_RATHER_THAN_EDIT
from test_shared_dispatch_bar import PLANTED_SENTENCES

from orchestrator.criteria_guard import APPENDIX


def test_no_statement_of_dispatch_policy_stands_in_both_files() -> None:
    """The structural half: one policy, one document, and a gate rather than a rule.

    `AGENTS.md` has said since the suppression incident that this policy has one source
    and that it is `templates/dispatch-appendix.md`; nothing checked it. Compared as a worker
    meets them: this preamble as a system prompt, the appendix as its task's
    `## Additional info`.
    """
    shared = phrases_in_both(shared_agent_preamble(), appendix_text())
    assert not shared, (
        f"{BASE_CONFIG}'s `system_prompt` and {APPENDIX} both state:\n"
        + "\n".join(f"  {run!r}" for run in shared)
        + f"\nDispatch policy has one source and it is {APPENDIX}; delete the copy here "
        "or state it there alone"
    )


def test_what_a_retry_is_judged_on_is_stated_in_the_appendix_alone() -> None:
    """The statement a judge assesses a `checks-failed` retry by, held to its one source.

    It is dispatch policy — which checks a dispatch is held to — so it is the appendix's,
    which every judge reads beside the task. Stated in `user.done_when` instead it would
    be a clause about checks handed to every judge of a plan, a report and a diff alike,
    which the vocabulary above refuses; stated in both it would be two answers to the one
    question a repaired retry was failed on.
    """
    appendix = " ".join(appendix_text().split())
    bar = shared_completion_bar()
    preamble = " ".join(shared_agent_preamble().split())
    for stated, what in DOWNSTREAM_CHECKS_ARE_NOT_JUDGED:
        assert stated.search(appendix), f"{APPENDIX} no longer says {what}"
        for field_name, clause in (("user.done_when", bar), ("system_prompt", preamble)):
            found = stated.search(clause)
            assert found is None, (
                f"{BASE_CONFIG}'s `{field_name}` says {what} ({found.group(0)!r}), which "
                f"{APPENDIX} already states; dispatch policy has one source"
            )


def test_the_report_rather_than_edit_rule_is_stated_in_the_appendix_alone() -> None:
    """The one sentence the appendix keeps about a judged lint tier, held to its one source.

    It survived the removal of the behaviour beside it because it was never the conflict:
    a rule that looks wrong or misapplied is reported with evidence rather than edited.
    Stated here as well it would be two copies of the one judged-lint instruction left,
    and the appendix's own test holds that instruction to being the only one.
    """
    appendix = " ".join(appendix_text().split())
    assert REPORT_RATHER_THAN_EDIT.search(appendix), (
        f"{APPENDIX} no longer says that a rule which looks wrong or misapplied is "
        "reported with evidence rather than edited"
    )
    for field_name, clause in (
        ("user.done_when", shared_completion_bar()),
        ("system_prompt", " ".join(shared_agent_preamble().split())),
    ):
        found = REPORT_RATHER_THAN_EDIT.search(clause)
        assert found is None, (
            f"{BASE_CONFIG}'s `{field_name}` says a misapplied rule is reported rather "
            f"than edited ({found.group(0)!r}), which {APPENDIX} already states; dispatch "
            "policy has one source"
        )


@pytest.mark.parametrize("copied", PLANTED_SENTENCES)
def test_a_policy_sentence_standing_in_both_files_fails_that_gate(copied: str) -> None:
    """The gate proven to catch what it is for, on the real files rather than on fixtures.

    Two documents that happen to agree today pass whether the gate discriminates or not,
    so a real appendix sentence is planted into the real preamble and the gate asked
    again.
    """
    appendix = appendix_text()
    assert copied in " ".join(appendix.split()), (
        f"{APPENDIX} no longer states {copied!r}, so this gate is being proven against a "
        "sentence that is not in it; plant one this file really carries"
    )

    planted = f"{shared_agent_preamble()}\n  - Pick those checks from your change, and {copied}."
    caught = phrases_in_both(planted, appendix)
    assert any(copied in run for run in caught), (
        f"the gate over {BASE_CONFIG} and {APPENDIX} passed a preamble carrying "
        f"{copied!r} verbatim out of the appendix, so it would not have caught the "
        f"duplication it exists for; it reported {caught}"
    )
