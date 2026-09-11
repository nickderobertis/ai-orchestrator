"""Every section of `AGENTS.md` that says what the engine — or a library it links — does.

Moving `config/onepipeline.version` moves what a dispatch runs, and this document
describes that behaviour at length: the landing tiers, the phases, the channel receipts,
the scheduler's outcomes, the watch verb, the write-back. A bump therefore brings every
one of those passages due at once, and the failure mode is the quiet one — a paragraph
that was measured, was true, and now describes a build nobody runs, read by a manager
mid-run who has no reason to doubt it.

`tests/test_dated_claims.py` catches the passages that carry a **date**. This one closes
the other half: a section can state what a release does without stamping a date on it,
and there was no list saying which sections those are. So the population is enumerated
here — every heading in the document is classified, in both directions, so a section
that grows an engine claim and is not added to the list is visible to a reader comparing
the two, and a section deleted or renamed fails here rather than leaving a gate pointing
at prose that is gone.

Two things are held of each classified section, and neither is a judgment of prose,
which no check can make:

- **A section that states engine behaviour names the checks that re-take it.** Those
  are the modules that ask the *installed* artifacts rather than the document, so an
  engine that moved out from under a claim fails them rather than being caught by a
  reader. Each named path is required to exist, so a renamed module is a failure here
  instead of a pointer to a check nobody runs.
- **A section that names an adopted release names the one this checkout pins.** That is
  the exact staleness a bump produces, and this module was written across one: *the
  adopted onepipeline 0.23.0* was a true sentence at every one of its sites until the
  pin moved off it, and a false one at every one of them the moment after, with nothing
  in a run to contradict any of them. Every such phrase in the document is read and
  reconciled against `config/`.

What this cannot do is judge whether a named check really re-takes the claim beside it,
which is the same gap `tests/test_dated_claims.py` records. The reviewer who reads the
two together is the judge of that.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest
from published_tools import PUBLISHED_TOOLS

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The document under this rule, and the one a manager reads mid-run: `CLAUDE.md` is a
#: symlink to it, so an agent session reads it too.
GUIDANCE_DOCUMENT = "AGENTS.md"

#: Every heading this classification covers. The document's title is not one of them:
#: what sits under it is a two-line frame naming the file's own purpose, and there is
#: no section boundary between it and the first `##`.
HEADING = re.compile(r"^(#{2,3}) (.+)$", re.MULTILINE)
TITLE = "# AGENTS.md"


class Section(NamedTuple):
    """One section that states what the engine, or a library it links, does."""

    #: The heading, exactly as the document spells it.
    heading: str
    #: What the section says about a published implementation, for a reader of a
    #: failure — so that somebody arriving at this list knows what is at stake in it
    #: without reading the whole section first.
    states: str
    #: The checks that re-take it against the installed artifacts. At least one, and
    #: each is required to exist.
    held_by: tuple[str, ...]


#: Every section that states what a published implementation does. Ordered as the
#: document orders them, so the two can be read side by side.
ENGINE_BEHAVIOUR_SECTIONS = (
    Section(
        heading="## What this repo is",
        states=(
            "how `onevcs` decides a branch landed — the four tiers, which of them a "
            "workflow can reach, and what `onepipeline` renders a landing from"
        ),
        held_by=(
            "tests/test_phase_and_landing_guidance.py",
            "tests/e2e/test_work_status_and_import_e2e.py",
            "tests/e2e/test_worker_start_directory_e2e.py",
        ),
    ),
    Section(
        heading="## Which pin governs a dispatch",
        states=(
            "which pin decides what a dispatched node runs, and which releases of "
            "`oneagentgraph`, `onevcs`, `onejudge` and `oneharness-core` the adopted "
            "engine links"
        ),
        held_by=(
            "tests/test_linked_libraries.py",
            "tests/test_adopted_engine_carries_this_plan.py",
            "tests/e2e/test_linked_engine_reconciliation_e2e.py",
        ),
    ),
    Section(
        heading="### Why the read API's linked engine is behind the engine pin",
        states=(
            "that the read API carries its own copy of the engine, which release "
            "repaired the write-back and the attached launch, and that neither is "
            "read off a pin"
        ),
        held_by=(
            "tests/e2e/test_onetaskgraph_host_e2e.py",
            "tests/e2e/test_orchestrate_launch_e2e.py",
            "tests/dag_ui/test_dag_ui_serving_e2e.py",
        ),
    ),
    Section(
        heading="## Sequencing a node behind a release",
        states=(
            "the `onevcs release` surface, the adoption modes the engine's loader "
            "reads, and what each answers on this host"
        ),
        held_by=(
            "tests/test_release_adoption_guidance.py",
            "tests/e2e/test_release_adoption_in_force_e2e.py",
        ),
    ),
    Section(
        heading="## What phase a change's events belong to",
        states=(
            "the phase every `onevcs` event carries, how a filter naming one is "
            "answered, and how a session's releases reach a run"
        ),
        held_by=(
            "tests/test_phase_and_landing_guidance.py",
            "tests/e2e/test_release_adoption_in_force_e2e.py",
        ),
    ),
    Section(
        heading="## Where a plan of this repository lives",
        states=(
            "what `onetaskgraph` answers a scoped read with, what the engine's "
            "settlement write-back projects, and how a refused copy is reported"
        ),
        held_by=(
            "tests/test_plan_store_guidance.py",
            "tests/e2e/test_onetaskgraph_host_e2e.py",
        ),
    ),
    Section(
        heading="## Your loop as manager",
        states=(
            "which live edits the engine accepts, what each does to a node, and which "
            "lever binds a node's judge"
        ),
        held_by=(
            "tests/test_manager_lever_guidance.py",
            "tests/test_engine_contracts.py",
            "tests/e2e/test_driver_death_is_recoverable_e2e.py",
        ),
    ),
    Section(
        heading="### Never let dispatched work run unwatched",
        states=(
            "what the engine's blocking watch verb returns on, the conditions it "
            "accepts, the cursor it hands back, and what its `unwatched` verb reports, "
            "excludes and leaves undecided"
        ),
        held_by=(
            "tests/test_watch_and_release_reading_guidance.py",
            "tests/test_watch_surface_drift.py",
            "tests/e2e/test_watch_selector_e2e.py",
            "tests/unwatched/test_unwatched_and_stop_hook_e2e.py",
            "tests/unwatched/test_unwatched_launch_shapes_e2e.py",
            "tests/plan_tooling/test_unwatched_planning_launches_e2e.py",
        ),
    ),
    Section(
        heading="### Answering on the channel",
        states=(
            "what the engine's reply answers with per carried half, how a surface is "
            "handed out, and what an abandoned one means"
        ),
        held_by=(
            "tests/ask_seam/test_channel_reply_e2e.py",
            "tests/ask_seam/test_ask_manager_e2e.py",
            "tests/test_planner_seam_contracts.py",
        ),
    ),
    Section(
        heading="## Personas and the base config",
        states=(
            "which roles `oneagentgraph` builds in, and which of its fields a persona "
            "replaces rather than merges"
        ),
        held_by=(
            "tests/e2e/test_shipped_persona_catalog_e2e.py",
            "tests/e2e/test_persona_review_bar_e2e.py",
            "tests/test_shared_dispatch_bar.py",
        ),
    ),
    Section(
        heading="## The two sides of the conversation",
        states=(
            "how `oneharness` routes each side, what streaming and turn control cost "
            "together, and which member runs its turn as a library"
        ),
        held_by=(
            "tests/e2e/test_agent_wrapper_sides_e2e.py",
            "tests/e2e/test_oneharness_control_e2e.py",
            "tests/e2e/test_quota_fallthrough_e2e.py",
        ),
    ),
    Section(
        heading="## Command surface",
        states=(
            "what every delegated verb reports — the views, the transcript, the sweep, "
            "the recoverable listing, and the drafting a publication opens under"
        ),
        held_by=(
            "tests/test_cli_surface_drift.py",
            "tests/e2e/test_delegated_recipes_e2e.py",
            "tests/e2e/test_transcript_recipe_e2e.py",
            "tests/e2e/test_recoverable_resume_commands_e2e.py",
        ),
    ),
    Section(
        heading="## Dogfooding rule",
        states=(
            "that a lifecycle node clones its target, works in a worktree of its own, "
            "and that a one-node run gets the same journal and surfaces as any other"
        ),
        held_by=(
            "tests/e2e/test_worker_start_directory_e2e.py",
            "tests/e2e/test_orchestrate_launch_e2e.py",
        ),
    ),
    Section(
        heading="## Commits and merging",
        states=(
            "that `onevcs` puts a publication's composed subject to the destination's "
            "own hook, and what a landed recovery leaves on the base"
        ),
        held_by=(
            "tests/e2e/test_commit_msg_hook_e2e.py",
            "tests/e2e/test_publish_branch_e2e.py",
        ),
    ),
)

#: Every other heading, and why it states no published implementation's behaviour.
#: Enumerated rather than derived by subtraction: what makes the list above meaningful
#: is that a heading has to be classified one way or the other, so a section that grows
#: an engine claim fails here until somebody decides which side it is on.
SECTIONS_STATING_NO_ENGINE_BEHAVIOUR = {
    '## What "agent" means here': "defines a word this repository uses",
    "## Stack and composition": "records how this workspace was composed",
    "## Invariants (non-negotiable)": "this repository's own gate and its floors",
    "## Tests are context engineering": "how this repository's own suite is split",
    "## After the main task": "what to do beyond the ask",
}

#: How the document names an adopted release: the phrase, and the tool it is about.
#: Each is reconciled against `config/`, because each is a sentence that is true until
#: the pin moves and false the moment after, with nothing in a run to contradict it.
#: `onepipeline-ui` is excluded from the bare-`onepipeline` shapes by the negative
#: lookahead, since its own releases are numbered separately.
ADOPTED_RELEASE_PHRASES = re.compile(
    r"\b(?:the )?(?:adopted|pinned) (?P<tool>onepipeline-ui|onepipeline|onevcs|"
    r"oneagentgraph|onejudge|onetaskgraph)(?!-)[  ]v?(?P<version>\d+\.\d+\.\d+)"
)

#: Which `config/<file>` names each tool's adopted release. Every tool the pattern above
#: recognises has a row, so a phrase this document grows cannot go unreconciled.
VERSION_FILES = {
    "onepipeline": "onepipeline.version",
    "onepipeline-ui": "onepipeline-ui.version",
    "onevcs": "onevcs.version",
    "oneagentgraph": "oneagentgraph.version",
    "onejudge": "onejudge.version",
    "onetaskgraph": "onetaskgraph.version",
}


def _document() -> str:
    return (REPO_ROOT / GUIDANCE_DOCUMENT).read_text(encoding="utf-8")


def _headings() -> tuple[str, ...]:
    """Every `##` and `###` heading in the document, as it spells them."""
    return tuple(f"{level} {text}" for level, text in HEADING.findall(_document()))


def _adopted(tool: str) -> str:
    """The release `config/` declares for one tool."""
    version_file = VERSION_FILES[tool]
    for published in PUBLISHED_TOOLS:
        if published.version_file == version_file:
            return published.adopted_version
    declared = (REPO_ROOT / "config" / version_file).read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", declared), (
        f"config/{version_file} must contain one semantic version, got {declared!r}"
    )
    return declared


def test_every_heading_is_classified_one_way_or_the_other() -> None:
    """The population is closed, so a section missing from the list is visible.

    Both directions. A heading in neither list is a section nobody decided about — the
    state this module exists to end — and a listed heading the document no longer
    carries is a gate pointing at prose that is gone, which reads as coverage and is
    none.
    """
    document = set(_headings())
    classified = {section.heading for section in ENGINE_BEHAVIOUR_SECTIONS} | set(
        SECTIONS_STATING_NO_ENGINE_BEHAVIOUR
    )

    assert TITLE in _document(), f"{GUIDANCE_DOCUMENT} no longer opens with {TITLE!r}"
    assert document == classified, (
        f"{GUIDANCE_DOCUMENT}'s sections and this module's classification of them "
        f"disagree. Unclassified: {sorted(document - classified)}. Classified but not "
        f"in the document: {sorted(classified - document)}. Every section either states "
        "what a published implementation does — and names the checks that re-take it — "
        "or says why it does not"
    )


@pytest.mark.parametrize("section", ENGINE_BEHAVIOUR_SECTIONS, ids=lambda section: section.heading)
def test_each_engine_behaviour_section_is_in_the_document_and_names_its_checks(
    section: Section,
) -> None:
    """A listed section is really there, and the checks it names really exist.

    The second half is what stops this list decaying into an inventory of intentions: a
    module renamed or deleted under a section leaves the claim held by nothing, and that
    is exactly as unheld as never having named a check at all — but it reads like
    coverage, which is worse.
    """
    assert f"\n{section.heading}\n" in _document(), (
        f"{GUIDANCE_DOCUMENT} no longer carries {section.heading!r}, which this module "
        f"lists as stating {section.states}"
    )
    assert section.held_by, (
        f"{section.heading!r} states {section.states} and names no check that re-takes "
        "it against the installed artifacts"
    )
    for path in section.held_by:
        assert (REPO_ROOT / path).is_file(), (
            f"{section.heading!r} names {path}, which does not exist. A section held by "
            "a check nobody runs reads as covered and is not"
        )


def test_every_adopted_release_the_document_names_is_the_one_this_checkout_pins() -> None:
    """A bump that left a sentence behind fails here rather than misleading a manager.

    This is the staleness the enumeration above exists for, in the one shape a check can
    decide: *the adopted onepipeline X* is true until `config/onepipeline.version` moves
    off X and false immediately afterwards, and no run contradicts it. Reported whole
    rather than at the first difference, because an adopter re-reading the document
    after a bump wants every site at once.
    """
    stale: list[str] = []
    for match in ADOPTED_RELEASE_PHRASES.finditer(_document()):
        tool, named = match.group("tool"), match.group("version")
        adopted = _adopted(tool)
        if named != adopted:
            stale.append(f"{match.group(0)!r} — config/{VERSION_FILES[tool]} reads {adopted}")

    assert not stale, (
        f"{GUIDANCE_DOCUMENT} names an adopted release this checkout does not pin:\n"
        + "\n".join(f"  - {entry}" for entry in stale)
        + "\nRe-read each passage against the release now installed and correct it in "
        "the same change that moved the pin, or it goes on describing a build nobody runs"
    )
