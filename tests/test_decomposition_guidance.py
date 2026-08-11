"""The decomposition guidance is split across two documents on purpose.

`AGENTS.md` holds the judgment a planner applies and `docs/orchestration.md` holds
the mechanics, with each half pointing at the other. Both failure modes are silent:
a half copied into the other document leaves two sources that drift apart, and a
pointer to a section that was renamed still reads as a working reference.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.root import REPO_ROOT

_JUDGMENT = ("AGENTS.md", "## The granularity rule (the core judgment)")
_MECHANICS = ("docs/orchestration.md", "## Decomposition and scheduling")

#: A statement of the contract-first guidance, and the document that owns it. Each
#: must appear in its own document and in neither the other one's prose: the seam,
#: the approval, and the worker's rule are judgment; the shape of a contract node
#: and how it unblocks its dependents are mechanics.
OWNED_STATEMENTS = (
    (_JUDGMENT[0], "where to cut is a contract"),
    (_JUDGMENT[0], "explicit user approval on that contract"),
    (_JUDGMENT[0], "never unilaterally change a shared interface"),
    (_MECHANICS[0], "no-op or sample-data implementation"),
    (_MECHANICS[0], "new optional field"),
    (_MECHANICS[0], "`deps` of the real implementation"),
)


def _slug(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _section(document: str, heading: str) -> str:
    prose = (REPO_ROOT / document).read_text(encoding="utf-8")
    assert heading in prose, f"{document} no longer carries the section {heading!r}"
    body = prose.split(f"\n{heading}\n", 1)[1]
    return body.split("\n## ", 1)[0]


@pytest.mark.reads_docs
@pytest.mark.parametrize(("document", "statement"), OWNED_STATEMENTS)
def test_each_half_of_the_guidance_is_stated_in_exactly_one_document(
    document: str, statement: str
) -> None:
    other = _MECHANICS[0] if document == _JUDGMENT[0] else _JUDGMENT[0]

    assert statement in (REPO_ROOT / document).read_text(encoding="utf-8"), (
        f"{document} no longer states {statement!r}; it owns that half of the "
        "contract-first guidance"
    )
    assert statement not in (REPO_ROOT / other).read_text(encoding="utf-8"), (
        f"{other} restates {statement!r}, which {document} owns; cross-reference the "
        "owning section instead of copying it"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize(("document", "heading"), (_JUDGMENT, _MECHANICS))
def test_decomposition_cross_references_resolve_to_real_headings(
    document: str, heading: str
) -> None:
    section = _section(document, heading)
    links = re.findall(r"\]\(([^)#\s]*)#([^)\s]+)\)", section)
    assert links, f"{document}'s {heading!r} no longer points at the other half"

    for target, anchor in links:
        resolved = (
            document
            if target == ""
            else str(((REPO_ROOT / document).parent / target).resolve().relative_to(REPO_ROOT))
        )
        headings = {
            _slug(found)
            for found in re.findall(
                r"(?m)^#+\s+(.*)$", (REPO_ROOT / resolved).read_text(encoding="utf-8")
            )
        }
        assert anchor in headings, (
            f"{document}'s {heading!r} links to {resolved}#{anchor}, which names no "
            "heading there; update the link in the same change that renamed it"
        )
