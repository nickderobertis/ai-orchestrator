"""The two documents that describe `just unpublished`, reconciled to the module that owns it.

`docs/orchestration.md` and `docs/repo-lifecycle.md` state the view's targets, flags,
exit statuses, build-output directories, label keys and acknowledgement location in
prose, because a manager reads them there. `orchestrator/unpublished.py` is the one
declaration of each, so every name the prose uses is read back against it here: a flag
the parser does not accept, a status it does not answer with, or a directory it does not
measure fails this rather than reading as true.
"""

from __future__ import annotations

import re

import pytest

from orchestrator import unpublished
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

ORCHESTRATION = REPO_ROOT / "docs" / "orchestration.md"
LIFECYCLE = REPO_ROOT / "docs" / "repo-lifecycle.md"
SECTION_HEADING = "### The preserved branches this host is holding onto"
LIFECYCLE_OPENING = "`just unpublished` asks the same question from the other end"

OPTION = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
EXIT_PARAGRAPH = re.compile(
    r"\*\*The exit status is the whole of what a consumer branches on\*\*.*?(?:\n\n|\Z)", re.S
)
BACKTICKED_CODE = re.compile(r"`(\d+)`")
ROW_KEYS = re.compile(r"A row's `--json` keys are (.*?) —", re.S)
BACKTICKED_NAME = re.compile(r"`([a-z_]+)`")
#: A parenthetical gloss on a key, which may itself quote a name that is not a key.
PARENTHETICAL = re.compile(r"\([^)]*\)")


def _orchestration_section() -> str:
    text = ORCHESTRATION.read_text(encoding="utf-8")
    start = text.index(SECTION_HEADING)
    end = text.index("\n### ", start + len(SECTION_HEADING))
    return text[start:end]


def _lifecycle_paragraph() -> str:
    text = LIFECYCLE.read_text(encoding="utf-8")
    start = text.index(LIFECYCLE_OPENING)
    return text[start : text.index("\n\n", start)]


def test_every_flag_the_documents_name_is_one_the_view_accepts() -> None:
    accepted = {
        option for action in unpublished._parser()._actions for option in action.option_strings
    }
    for name, prose in (
        ("orchestration", _orchestration_section()),
        ("lifecycle", _lifecycle_paragraph()),
    ):
        named = set(OPTION.findall(prose))
        assert named, f"the {name} prose names no flag to reconcile"
        assert named <= accepted, f"the {name} prose names {sorted(named - accepted)}"


def test_the_exit_statuses_the_document_states_are_the_ones_declared() -> None:
    paragraph = EXIT_PARAGRAPH.search(_orchestration_section())
    assert paragraph is not None
    stated = {int(code) for code in BACKTICKED_CODE.findall(paragraph.group(0))}
    declared = {status.code for status in unpublished.EXIT_STATUSES}
    # The unanswered status is stated as "any other non-zero", which is what a consumer
    # may rely on; the number itself is the module's.
    assert stated == declared - {unpublished.UNANSWERED}


def test_the_measured_directories_labels_and_acknowledgement_home_are_the_declared_ones() -> None:
    section = _orchestration_section()
    for directory in unpublished.BUILD_OUTPUT_DIRECTORIES:
        assert f"`{directory}`" in section, directory
    for label in unpublished.SESSION_LABELS:
        assert f"`{label}`" in section, label
    assert "/".join(unpublished.ACKNOWLEDGEMENTS_UNDER) in section


def test_the_row_keys_the_lifecycle_document_lists_are_the_emitted_ones_in_order() -> None:
    listed = ROW_KEYS.search(_lifecycle_paragraph())
    assert listed is not None, "the lifecycle paragraph no longer lists the row's keys"
    assert (
        tuple(BACKTICKED_NAME.findall(PARENTHETICAL.sub("", listed.group(1))))
        == unpublished.ROW_FIELDS
    )
