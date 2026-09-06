"""The decomposition doctrine is split three ways on purpose.

`personas/planner.yaml` holds the judgment the **planner** applies, `AGENTS.md`
holds the judgment the **manager** applies, and `docs/orchestration.md` holds the
mechanics — each pointing at the others rather than restating them. The persona is
the half that has to be a file rather than a paragraph: `system_prompt`
becomes the dispatched planner's own system prompt, so it is what travels into a
repository whose `AGENTS.md` is that repository's and knows nothing about any of
this.

Both failure modes this gate has always guarded are silent. A half copied into
another document leaves two sources that drift apart — and copying the planner's
judgment back into `AGENTS.md` is exactly the drift the split was made to end,
because only one of the two copies reaches a planner working elsewhere. A pointer
to a section that was renamed still reads as a working reference.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT


class Owner(NamedTuple):
    """One document, and the section of it that owns its half of the doctrine."""

    document: str
    #: `None` when the whole file is the owning section. A persona is a onejudge
    #: config fragment rather than a document with headings, and `system_prompt` is
    #: its owning section in the only sense that matters.
    section: str | None


class Owned(NamedTuple):
    """One statement of the doctrine, and the document that owns it."""

    document: str
    statement: str


PLANNER = Owner("personas/planner.yaml", None)
MANAGER = Owner("AGENTS.md", "## Your loop as manager")
MECHANICS = Owner("docs/orchestration.md", "## Decomposition and scheduling")
OWNERS = (PLANNER, MANAGER, MECHANICS)
#: The two owners that may cross-reference the others. The persona is prose too, but
#: it is read outside this checkout, so it deliberately points at nothing — which is
#: its own test below rather than a link to resolve here.
CROSS_REFERENCING_OWNERS = (MANAGER, MECHANICS)

#: Every statement must appear in its own document and in neither of the other two.
OWNED_STATEMENTS = (
    # The planner's judgment: how big a node is, where to cut it, what one node
    # owns, how its task and its review bar are written, and how it reaches the
    # manager. All of it travels with the dispatch.
    Owned(PLANNER.document, "never merely to hand a capable agent a smaller slice"),
    Owned(PLANNER.document, "where to cut is a contract"),
    Owned(PLANNER.document, "a shared interface is never changed unilaterally"),
    Owned(PLANNER.document, "Never split implementation and those tests"),
    Owned(PLANNER.document, "the decision driver that a diff cannot recover"),
    Owned(PLANNER.document, "there is no second place to state one"),
    Owned(PLANNER.document, "satisfiable by that node's own worker inside its own dispatch"),
    Owned(PLANNER.document, "the criteria stop at *ready to publish*"),
    Owned(PLANNER.document, "$ORCHESTRATOR_ASK_MANAGER"),
    Owned(PLANNER.document, "PLANNER EXCEPTIONS"),
    # The manager's judgment: what to dispatch, what to brief, what to decide, what
    # to escalate — and the two rules a top-level session breaks most expensively.
    Owned(MANAGER.document, "Decide whether to dispatch a planner at all"),
    Owned(MANAGER.document, "the complete gate can prove it"),
    Owned(MANAGER.document, "their approval of it is what gates dispatch"),
    Owned(MANAGER.document, "high-value to put in front of the user"),
    Owned(MANAGER.document, "A watch is armed before you turn to anything else"),
    Owned(MANAGER.document, "Silence must never be indistinguishable from progress"),
    Owned(MANAGER.document, "A foreground attach alone is not an armed watch"),
    Owned(MANAGER.document, "planner update(s) waiting"),
    Owned(MANAGER.document, "confirm the `pending` surface is the one being answered"),
    Owned(MANAGER.document, "A blocking surface may have nobody waiting on it"),
    Owned(MANAGER.document, "The mark says nobody is listening *now*"),
    Owned(MANAGER.document, "A raw interrupt reaches the worker's turn alone and is"),
    # How a node says it depends on another repository's *release* rather than on the
    # work. The split is the same one and for the same reason: choosing a node's
    # adoption mode and writing its task around the references the framework appends
    # is the planner's, and it travels; what a release target is, how the two styles
    # are answered, and what a held run puts in front of a person is the manager's,
    # and it stays. A copy of either half in the other document is the drift that
    # ends with a task instructing a worker to pin what the framework already pinned.
    Owned(PLANNER.document, "whenever the default the node's repository resolves to is not"),
    Owned(PLANNER.document, "keyed by the dependency's node id"),
    Owned(PLANNER.document, "says who performs it and why waiting beats building against the work"),
    Owned(PLANNER.document, "Never write pinning instructions into a task that adopts fast"),
    Owned(PLANNER.document, "never a node in your plan"),
    Owned(MANAGER.document, "is one artifact a repository publishes"),
    Owned(MANAGER.document, "a member of the vocabulary nothing here uses"),
    Owned(MANAGER.document, "There is no fifth rung, no plan-level tier, and no run-only override"),
    Owned(MANAGER.document, "The adoption instruction a worker follows is the producer's"),
    Owned(MANAGER.document, "A probe is not a gate"),
    Owned(MANAGER.document, "the person who has to act, or find who will"),
    # What a node that changes code owes its judge, now that this host has stopped
    # dispatching a worker to run its repository's whole bar. The split is the same one:
    # writing that criterion is the planner's and it travels, while what a manager then
    # reads when the merge path refuses the branch is the manager's and it stays. A copy
    # of either half in the other document is how a plan comes to demand a gate again —
    # the demand this host removed, and the one a judge will happily import.
    Owned(PLANNER.document, "Never make a node's repository-wide gate one of those criteria"),
    Owned(MANAGER.document, "**Node criteria stopped naming a gate here**"),
    Owned(MANAGER.document, "`checks-failed` is the ordinary way that arrives"),
    # The mechanics: the shape a contract node takes in this engine's graph, and how
    # its dependents are wired to it.
    Owned(MECHANICS.document, "no-op or sample-data implementation"),
    Owned(MECHANICS.document, "new optional field"),
    Owned(MECHANICS.document, "`deps` of the real implementation"),
    # What counts as a contract at all, widened past a call surface. Split the usual
    # way: the criterion a planner applies travels, the approval a manager owes stays,
    # the per-kind first landing is the mechanics'. Stated twice, the breadth gets
    # narrowed in one copy only — which reads, to a planner working elsewhere, exactly
    # like the narrow list this widened.
    Owned(PLANNER.document, "an agreement two or more parties must both hold to"),
    Owned(PLANNER.document, "A persistence or storage schema, and the data model behind it"),
    Owned(PLANNER.document, "An interface between internal collections of code"),
    Owned(PLANNER.document, "read it against the criterion, not against the examples"),
    Owned(PLANNER.document, "a stored shape added beside the one already there"),
    Owned(PLANNER.document, "a stored shape binds hardest of the three"),
    # One statement rather than three: the source-or-drift-check demand holds for
    # every kind alike, and it is the planner's because it is written into a task.
    Owned(
        PLANNER.document,
        "one authoritative source the copies are derived from, or a check that goes red "
        "when they drift apart",
    ),
    Owned(MANAGER.document, "as often a stored shape or an internal boundary as it is a call"),
    Owned(MANAGER.document, "approve the seam rather than the list"),
    Owned(MECHANICS.document, "lands additively, reachable by the writers and readers that"),
    Owned(MECHANICS.document, "lands as the boundary declared with nothing moved across it"),
    # What a person is asked to accept, and when. The split is the same one again and
    # the reason is sharper here than anywhere else: the planner's half travels into a
    # repository where none of this harness exists, so it says where a contract is put
    # to the user and nothing about the command that records the answer; the manager's
    # half is the decision and the blocking condition on dispatch; the mechanics are the
    # record, what invalidates it, and what a launch refuses without it. A copy of the
    # manager's half in the persona is an instruction to run a command that is not
    # there, and a copy of the mechanics in either is the half that goes stale the first
    # time the record moves.
    Owned(PLANNER.document, "reach the user through the design document"),
    Owned(
        MECHANICS.document,
        "A launch is refused until the user has approved the document the plan is read as",
    ),
)

#: A markdown link, as `(target, anchor)`. An empty target is a link into the
#: linking document itself; an empty anchor is a link to a whole file.
LINK = re.compile(r"\]\(([^)#\s]*)(?:#([^)\s]+))?\)")
#: Any markdown link at all, by the two characters every one of them has.
ANY_LINK = "]("
#: A token shaped like a path. Deliberately loose: what makes a match a finding is
#: that the path exists in this checkout, not that it looked like one.
NAMED_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+|[A-Za-z0-9_-]+\.[A-Za-z]{1,5}\b")
#: Where the persona's role prose starts and ends. Read textually rather than as
#: YAML because this repository ships no YAML parser to its Python environment, and
#: `just validate-personas` is what proves the file's shape.
INSTRUCTIONS_OPEN = "\nsystem_prompt: |\n"
INSTRUCTIONS_CLOSE = "\nuser:\n"


def _slug(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _text(document: str) -> str:
    return (REPO_ROOT / document).read_text(encoding="utf-8")


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a statement may be quoted as one line.

    Both documents here are hard-wrapped and the persona is a YAML block scalar, so
    a statement long enough to be unambiguous is longer than the line it sits on.
    """
    return " ".join(prose.split())


def _section(owner: Owner) -> str:
    """The owning section's raw prose, or the whole file when it owns no heading."""
    prose = _text(owner.document)
    if owner.section is None:
        return prose
    assert owner.section in prose, (
        f"{owner.document} no longer carries the section {owner.section!r}"
    )
    body = prose.split(f"\n{owner.section}\n", 1)[1]
    return body.split("\n## ", 1)[0]


def _planner_instructions() -> str:
    """The role prose the dispatched planner is actually given as its system prompt."""
    persona = _text(PLANNER.document)
    assert INSTRUCTIONS_OPEN in persona, (
        f"{PLANNER.document} no longer opens a block-scalar `system_prompt`"
    )
    role = persona.split(INSTRUCTIONS_OPEN, 1)[1]
    assert INSTRUCTIONS_CLOSE in role, f"{PLANNER.document} no longer carries a `user` block"
    return role.split(INSTRUCTIONS_CLOSE, 1)[0]


@pytest.mark.reads_docs
@pytest.mark.parametrize("owned", OWNED_STATEMENTS, ids=lambda owned: owned.statement)
def test_each_statement_of_the_doctrine_is_made_in_exactly_one_document(owned: Owned) -> None:
    wanted = _flat(owned.statement)
    assert wanted in _flat(_text(owned.document)), (
        f"{owned.document} no longer states {owned.statement!r}, which it owns; the "
        "three-way split only works while each half is stated somewhere"
    )
    for other in OWNERS:
        if other.document == owned.document:
            continue
        assert wanted not in _flat(_text(other.document)), (
            f"{other.document} restates {owned.statement!r}, which {owned.document} "
            "owns; cross-reference the owner instead of copying it, or the two copies "
            "drift apart"
        )


@pytest.mark.reads_docs
@pytest.mark.parametrize("owner", CROSS_REFERENCING_OWNERS, ids=lambda owner: owner.document)
def test_every_cross_reference_in_an_owning_section_resolves(owner: Owner) -> None:
    section = _section(owner)
    links = LINK.findall(section)
    assert links, f"{owner.document}'s {owner.section!r} no longer points at the other halves"

    for target, anchor in links:
        resolved = (
            owner.document
            if target == ""
            else str(
                ((REPO_ROOT / owner.document).parent / target).resolve().relative_to(REPO_ROOT)
            )
        )
        assert (REPO_ROOT / resolved).is_file(), (
            f"{owner.document}'s owning section links to {resolved}, which is not a file "
            "here; update the link in the same change that moved it"
        )
        if not anchor:
            continue
        headings = {_slug(found) for found in re.findall(r"(?m)^#+\s+(.*)$", _text(resolved))}
        assert anchor in headings, (
            f"{owner.document}'s owning section links to {resolved}#{anchor}, which names "
            "no heading there; update the link in the same change that renamed it"
        )


@pytest.mark.reads_docs
def test_the_planner_persona_points_at_nothing_in_this_repository() -> None:
    """What travels cannot cite what stays.

    `system_prompt` is the dispatched planner's system prompt in whatever
    repository it is planning against, where every path in this checkout resolves to
    nothing. So this half of the doctrine carries no pointer at all: a reference out
    of it would be the same dead end as the copy the split removed.
    """
    role = _planner_instructions()
    # Prose punctuation rides along on a loose match: `docs/orchestration.md.` ends a
    # sentence and names a file, and only one of those is visible to `exists`.
    candidates = {path.strip("`.,;:()") for path in NAMED_PATH.findall(role)}
    named = sorted(path for path in candidates if path and (REPO_ROOT / path).exists())
    assert not named, (
        f"{PLANNER.document}'s role prose names {named}, which exists in this checkout "
        "and nowhere the persona is dispatched against; state what the planner needs "
        "rather than pointing at it"
    )
    assert ANY_LINK not in role, (
        f"{PLANNER.document}'s role prose carries a markdown link; it is read outside "
        "this checkout, where the link resolves to nothing"
    )


@pytest.mark.reads_docs
def test_the_manager_names_the_persona_that_owns_the_rest_of_the_doctrine() -> None:
    """The one pointer that cannot be an anchored link, because a persona has none."""
    assert (REPO_ROOT / PLANNER.document).is_file(), (
        f"{PLANNER.document} is the planner's own source"
    )
    assert f"({PLANNER.document})" in _section(MANAGER), (
        f"{MANAGER.document}'s {MANAGER.section!r} no longer points at {PLANNER.document}; "
        "a reader who cannot find the planner's judgment will restate it here instead"
    )
