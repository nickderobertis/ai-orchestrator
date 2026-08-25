"""What `AGENTS.md` tells a manager about steering a run, held to this checkout.

Three passages, each written from a defect somebody paid for while the release-adoption
work shipped on 2026-08-23/24, and each stating something a manager cannot discover by
reading a run:

* **which lever binds a node's judge.** A manager ruled a change out of scope mid-run,
  the worker complied and re-ran its complete gate green, and seven minutes later that
  node's own judge instructed it to restore what had been ruled out — because a `context`
  note reaches the worker and never the task the judge reviews against. `AGENTS.md` said
  both that an amended `task` is how a bar is amended and that `context` is the lever for
  steering a running dispatch, and said nothing about the gap between them.
* **that a mid-run amendment is criteria.** `adopt-oneharness-cli-2` settled
  `task-failed` on a *green* complete gate: the amendment named a mechanism — assert the
  wrapper *refuses* an invalid inherited label — where the implementation drops the
  offending pair and continues, which is what `orchestrator.labels.parse_labels` already
  does with an inherited value. The judge was right about the task and the task was
  wrong. The rule against writing criteria as procedure was stated for planners and
  nowhere for a manager writing an amendment under time pressure.
* **the bound on concurrent lifecycle dispatch.** `onevcs session open` reclaims a run
  root on a lease nothing holds while a dispatch works, and nothing else bounds how many
  dispatches one identity may safely have. The mechanism was recorded; the constraint
  that follows from it was not.

Each claim is enumerated here rather than summarized, and the ones this repository can
check against something other than its own prose are checked: the sentence it quotes
from the planner's own persona, the heading the operational notes really open at, the
document it points at instead of restating, and the op table a lever is picked from.
`tests/test_engine_contracts.py` holds the other end of the last two — the op table
against the engine's `Command` enum, and the `## Planner context` rendering against the
engine's own strings — so a release that gains a binding op, or rewords the sentence
that makes a note non-binding, fails there rather than leaving this passage describing a
lever this host does not have.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest

from orchestrator.criteria_guard import APPENDIX
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The manager's own document, and the two regions of it these claims live in. Regions
#: rather than the whole file, because each claim is made where a manager is doing that
#: thing — a phrase that survived somewhere else would satisfy a document-wide search
#: while the passage that has to carry it was gone.
MANAGER = "AGENTS.md"
LOOP_SECTION = "## Your loop as manager"
RECLAMATION_OPENER = "**What prunes that history is `onevcs session open`"

#: The planner's own persona, which is where the rule about how a criterion is written
#: lives, and this repository's one source for the operational notes a task carries.
PLANNER_PERSONA = "personas/planner.yaml"
#: The heading those notes open at, which is what "above the operational notes" means
#: when a manager places an amendment in a task.
APPENDIX_HEADING = "## Additional info"
#: Where the run-root mechanism is written in full, so the constraint may point at it.
RECLAMATION_DOCUMENT = "docs/run-root-reclamation.md"

#: The sentence `AGENTS.md` quotes from the planner's persona. One string, read from
#: here by both halves of the reconciliation below, so the quotation and the original
#: cannot come to be about two different rules.
CRITERIA_RULE = "State the outcome the node owes, never the procedure for reaching it"

#: The live-edit table a lever is picked from, and how a row names its op. The first
#: column only: every row also backticks the fields that op takes, and `amend` is one of
#: them — reading the whole row would report a field as though it were a lever.
OP_TABLE = re.compile(
    r"The accepted commands are:\n\n\|[^\n]*\n\|[^\n]*\n((?:\|[^\n]*\n)+)", re.DOTALL
)
OP_NAME = re.compile(r"^\| `([a-z]+)` \|", re.MULTILINE)

#: The levers the passage names, each of which has to be an op that table declares.
NAMED_LEVERS = ("retry", "cancel", "requeue", "context")

#: The op that does not exist here, and the reason it is worth naming: a binding
#: amendment op is a real proposal, and documenting one before this host's engine has it
#: would hand a manager a lever that fails at submission in the middle of a live run.
UNAVAILABLE_LEVER = "amend"


class Claim(NamedTuple):
    """One thing a passage has to say, and the phrase that says it."""

    #: What the claim is about, for the failure message and the test id.
    subject: str
    #: Which region of the document has to carry it.
    region: str
    phrase: str


#: Every claim these passages exist to make. Enumerated because each one is a thing a
#: manager would otherwise have to learn by losing a dispatch to it — which is how all
#: three of them were learned the first time.
REQUIRED_CLAIMS = (
    Claim(
        "the task is what changes the bar",
        LOOP_SECTION,
        'the only text that changes what "done" means for it is the\n   `task`',
    ),
    Claim(
        "which edits reach a task mid-run",
        LOOP_SECTION,
        "the only edits that\n   reach one mid-run are `retry`",
    ),
    Claim(
        "a requeue amendment is merged onto the node",
        LOOP_SECTION,
        "`cancel` plus a `requeue` whose `amend` is merged onto the node",
    ),
    Claim(
        "no lever amends a live dispatch's bar",
        LOOP_SECTION,
        "there is no lever\n   here that amends a node's bar while its worker keeps working",
    ),
    Claim("a note binds nothing", LOOP_SECTION, "the other lever and it binds nothing"),
    Claim("where a carried note is rendered", LOOP_SECTION, "under `## Planner context`"),
    Claim(
        "what that rendering declares the note to be",
        LOOP_SECTION,
        "This reports observed state and adds no acceptance criteria.",
    ),
    Claim(
        "a live note is stored nowhere",
        LOOP_SECTION,
        "an interrupt against the running turn that is stored\n   nowhere",
    ),
    Claim(
        "so it reaches neither judge nor next dispatch",
        LOOP_SECTION,
        "it reaches neither that node's judge nor its next dispatch",
    ),
    Claim(
        "when to amend",
        LOOP_SECTION,
        "Amend\n   whenever the correction changes what the finished tree must contain or what"
        ' "done"\n   means',
    ),
    Claim(
        "when to send a note instead",
        LOOP_SECTION,
        "send a note for anything the judge has no opinion\n   about",
    ),
    Claim("what getting it backwards cost", LOOP_SECTION, "15:50:23Z"),
    Claim("what the judge then instructed", LOOP_SECTION, "15:57:19Z"),
    Claim(
        "and what it took to resolve",
        LOOP_SECTION,
        "killing a live gate-green dispatch in order to\n   change its bar",
    ),
    Claim("an amendment is criteria", LOOP_SECTION, "**An amendment is criteria"),
    Claim("held to the planner's own rule", LOOP_SECTION, CRITERIA_RULE),
    Claim("the node that was failed for it", LOOP_SECTION, "`adopt-oneharness-cli-2`"),
    Claim(
        "the judge was right and the task was wrong",
        LOOP_SECTION,
        "The judge\n   was right about the task and the task was wrong",
    ),
    Claim(
        "where an amendment sits in the task",
        LOOP_SECTION,
        "Put the amendment **above** the\n   operational notes the task carries",
    ),
    Claim("which heading those notes open at", LOOP_SECTION, f"those open at `{APPENDIX_HEADING}`"),
    Claim(
        "which of the two wins",
        LOOP_SECTION,
        "where it and the notes below it disagree, the amendment wins",
    ),
    Claim(
        "the bound is one dispatch per identity",
        RECLAMATION_OPENER,
        "At most one lifecycle dispatch per repository identity is safe",
    ),
    Claim(
        "and it is a constraint",
        RECLAMATION_OPENER,
        "it is a constraint rather than a\npreference",
    ),
    Claim("the exposure is intra-run", RECLAMATION_OPENER, "The exposure is **intra-run**"),
    Claim("the run it was measured on", RECLAMATION_OPENER, "`adopt-engines-siblings`"),
    Claim(
        "one run's own concurrency reaches it",
        RECLAMATION_OPENER,
        "a plan whose `concurrency` is 2 reaches this with no\nsecond manager involved",
    ),
    Claim(
        "an uncommitted session is removed outright",
        RECLAMATION_OPENER,
        "a session that has opened and not yet committed is\nremoved outright",
    ),
    Claim(
        "which is when a node is most exposed",
        RECLAMATION_OPENER,
        "widest exactly when a\nnode has least to show for itself",
    ),
    Claim(
        "the mechanism is pointed at rather than restated",
        RECLAMATION_OPENER,
        f"[`{RECLAMATION_DOCUMENT}`]({RECLAMATION_DOCUMENT})",
    ),
)


def _text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a claim may be quoted as one line."""
    return " ".join(prose.split())


def _region(opener: str) -> str:
    """The passage a claim has to be made in, from its opener to the next section."""
    document = _text(MANAGER)
    assert opener in document, (
        f"{MANAGER} no longer carries {opener!r}, which is where a manager is told how to "
        "steer a run that is already going"
    )
    return document.split(opener, 1)[1].split("\n## ", 1)[0]


@pytest.mark.parametrize("claim", REQUIRED_CLAIMS, ids=lambda claim: claim.subject)
def test_the_manager_is_told_every_part_of_how_to_steer_a_running_node(claim: Claim) -> None:
    """A claim dropped from here is one a manager relearns by losing a dispatch.

    None of these is recoverable from a run: a note that failed to bind produces no
    error, an amendment that named a mechanism reads exactly like one that named a
    property until the judge rules, and a reclaimed run root reports as five identities
    refusing to spawn.
    """
    assert _flat(claim.phrase) in _flat(_region(claim.region)), (
        f"{MANAGER}'s {claim.region!r} passage no longer says {claim.subject}: the phrase "
        f"{claim.phrase!r} is gone"
    )


def test_the_rule_an_amendment_is_held_to_is_the_planners_own() -> None:
    """The quotation and the original, reconciled.

    `AGENTS.md` extends a rule it does not own — the persona is where criteria are
    written, and it travels into whatever repository is being planned against. Quoting a
    sentence that has since been reworded there would leave a manager holding an
    amendment to a rule no planner is given, which is the two-authorities failure this
    passage exists to close rather than repeat.
    """
    assert CRITERIA_RULE in _flat(_text(PLANNER_PERSONA)), (
        f"{PLANNER_PERSONA} no longer states {CRITERIA_RULE!r}, which {MANAGER} quotes as "
        "the rule a manager's mid-run amendment is held to. Re-read the persona and "
        "correct the quotation, or the two halves are about different rules"
    )


def test_the_operational_notes_really_open_at_the_heading_the_manager_is_told_to_sit_above() -> (
    None
):
    """ "Above the operational notes" has to name a position a task really has.

    A task carries `config/dispatch-appendix.md` verbatim — `just check-plan` refuses one
    that does not — so the heading that file opens with is the boundary an amendment sits
    above. If it moved, the placement instruction would send a manager to a heading no
    task has, and the precedence sentence would sit under text it does not govern.
    """
    appendix = _text(str(APPENDIX))
    assert appendix.lstrip().startswith(APPENDIX_HEADING), (
        f"{APPENDIX} no longer opens at {APPENDIX_HEADING!r}, so {MANAGER}'s instruction to "
        f"put an amendment above the operational notes names a boundary a task does not "
        f"have:\n{appendix[:120]}"
    )


def test_the_constraint_points_at_a_document_that_carries_the_mechanism() -> None:
    """The bound is stated here; the mechanism behind it is pointed at, so it must be there.

    The passage deliberately does not restate how `reclaim` decides — that is a page of
    measured reading, and two copies of it would drift. What it owes instead is a pointer
    that resolves, to a document that still explains the lease the proof rests on and
    still records that the fix is upstream.
    """
    document = _text(RECLAMATION_DOCUMENT)
    for grounding in ("occupancy lease", "## The upstream fix"):
        assert grounding in document, (
            f"{RECLAMATION_DOCUMENT} no longer carries {grounding!r}, and {MANAGER} states "
            "the concurrency bound by pointing at it rather than by restating the "
            "mechanism. Either the mechanism moved or the pointer did"
        )


def test_every_lever_the_manager_is_offered_is_an_op_the_engine_table_declares() -> None:
    """The passage may name levers this host has, and no others.

    A binding amendment op is a real proposal, and documenting one before the adopted
    engine has it would hand a manager a lever that fails at submission in the middle of
    a live run — the exact moment the passage is read. So the levers it names are read
    back against the live-edit table, and the op that does not exist here is held absent
    from both. `tests/test_engine_contracts.py` holds that table against the engine's own
    `Command` enum, so the day this host adopts a release that has one, this comes due.
    """
    table = OP_TABLE.search(_text("docs/orchestration.md"))
    assert table is not None, (
        "docs/orchestration.md no longer tabulates the accepted commands where this gate "
        "reads it, so nothing here can say which levers a manager really has"
    )
    declared = set(OP_NAME.findall(table.group(1)))
    passage = _flat(_region(LOOP_SECTION))
    for lever in NAMED_LEVERS:
        assert lever in declared, (
            f"{MANAGER} offers a manager `{lever}` and the live-edit table does not declare "
            f"it; the table has {sorted(declared)}"
        )
        assert f"`{lever}`" in passage, (
            f"{MANAGER}'s lever passage no longer names `{lever}`, which is one of the "
            "commands it tells a manager to choose between"
        )
    assert UNAVAILABLE_LEVER not in declared, (
        f"the live-edit table now declares a `{UNAVAILABLE_LEVER}` op. That is the lever "
        f"{MANAGER} says this host does not have, so the passage is now the stale claim: "
        "state the binding op beside `retry` and `requeue` instead"
    )
    assert f"`{UNAVAILABLE_LEVER}` op" not in passage, (
        f"{MANAGER} offers a manager an `{UNAVAILABLE_LEVER}` op the adopted engine does "
        "not accept; an edit naming it is refused at submission, mid-run"
    )
