"""A plan is refused for the reasons its own nodes would otherwise be failed for.

`just check-plan` exists because a judge reads two things — the node's
`## Acceptance criteria` and the review bar its persona resolves to — and fails
finished work whenever the second demands something the first is silent about. So
what has to be proven here is not that a regex fires: it is that the bar a node
resolves to is the bar a dispatch is actually given, and that a demand made
anywhere a judge will read it is refused unless a criterion answers it.

The bar half is measured, never restated. `resolve_bar` reads the shipped roles out
of the `onepipeline` binary, because a plan node's `persona` is a *name* that
resolves to a role compiled into the `oneagentgraph` **that binary links** — not to
`personas/`, and not to the pinned `oneagentgraph` CLI. The journeys below drive
that resolution against the real binary this checkout installs, so a release that
moves a role moves what this guard demands, with nothing here to update.

`tests/plan_tooling/test_check_plan_recipe_e2e.py` drives the same guard through the real
`just check-plan` over the real tracked appendix; the tests here are the ones that
can state a synthetic bar and a synthetic appendix, which is what makes each
refusal attributable to one cause.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from criteria_examples import (
    PUBLICATION_COPULAS,
    PUBLICATION_IN_PROSE,
    PUBLISHED_WITH_A_WORD_IN_THE_WAY,
    RED_BEFORE_GREEN,
    RELEASED_ELSEWHERE,
    RELEASED_ELSEWHERE_IN_PROSE,
    STATES_THE_PROPERTY_INSTEAD,
)

from orchestrator import criteria_guard, plan_check, plan_review, plan_store, task_body
from orchestrator.criteria_guard import (
    APPENDIX_ENV,
    AUTHORIZATIONS,
    CRITERIA_HEADING,
    Authorization,
    Bar,
    CriteriaError,
    appendix_text,
    block_scalar,
    builtin_persona,
    builtin_persona_names,
    check,
    check_amendment,
    check_appendix,
    check_changes_allowed,
    check_directly,
    check_plan,
    criteria_block,
    criteria_items,
    dispatched_nodes,
    field,
    permitting_roles,
    resolve_bar,
    yaml_fragment,
)
from orchestrator.root import REPO_ROOT

#: The five names `oneagentgraph` compiles in, which `personas/README.md` and
#: `tests/e2e/test_shipped_persona_catalog_e2e.py` both state. Named here as the
#: input to the resolution under test rather than as the claim: what these journeys
#: assert is that each one resolves to a bar with content, and a name that stops
#: being shipped fails by resolving to a path instead.
SHIPPED_ROLES = ("docs-writer", "engineer", "planner", "researcher", "reviewer")

#: A synthetic appendix, so that a task's own prose can make a demand without
#: dragging the tracked appendix's wording into a test about the mechanism.
APPENDIX = "## Additional info\n\n### Operational notes\n\nWork the branch and report.\n"


def _task(criteria: str, additional: str = APPENDIX) -> str:
    """A task in the shape every dispatched node here carries."""
    return (
        "## What\n\nDo the thing.\n\n"
        "## Why\n\nThe user asked for it.\n\n"
        f"{CRITERIA_HEADING}\n\n{criteria}\n\n{additional}"
    )


#: Criteria that answer every demand the synthetic appendix and a bar can make. The
#: reporting one is stated as the property it now is — every claim about the finished
#: work true of the final tree — rather than as the withdrawn ordering of the dispatch's
#: own outputs, so the accepting path exercised here is the one a plan really writes.
COMPLETE = (
    "- The thing is done.\n"
    "- A journey proves the thing end to end against the real interface.\n"
    "- Every claim about the finished work is true of the tree as it finally stands, with "
    "the evidence named."
)

#: The same reporting demand answered the way the withdrawn wording answered it. Kept as
#: a case rather than replaced, because plans written before this change say it this way
#: and a checker that started refusing them would strand correct work.
REPORTS_BY_THE_WITHDRAWN_WORDING = (
    "- The thing is done.\n"
    "- A journey proves the thing end to end against the real interface.\n"
    "- The dispatch closes with a completion report naming the evidence."
)


@pytest.fixture
def appendix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the appendix check at a synthetic appendix this test controls.

    The tracked appendix is prose, and a test that read it would belong to the
    whole-workspace tier — where coverage is not measured. Its own wording is held
    by the journeys that are marked for that tier; what is under test here is the
    check, so the file it reads is this test's to write.
    """
    written = tmp_path / "appendix.md"
    written.write_text(APPENDIX, encoding="utf-8")
    monkeypatch.setattr(criteria_guard, "APPENDIX", written)
    return written


def _plan(**node: object) -> dict[str, object]:
    return {"schema_version": 3, "name": "probe", "tasks": [{"id": "probe", **node}]}


FRAGMENT = """name: probe
system_prompt: |
  A role.

  Two paragraphs of it.
user:
  persona: >-
    A folded
    contract.
  done_when: "one quoted line"
  max_turns: 8
"""


def test_a_literal_scalar_keeps_its_line_structure() -> None:
    """`|` reaches the model with its newlines, so the reader keeps them."""
    assert block_scalar(FRAGMENT, "system_prompt") == (
        "|",
        "A role.\n\nTwo paragraphs of it.",
    )


def test_a_folded_scalar_reaches_the_model_as_one_line() -> None:
    """`>` folds its newlines to spaces, so a value read otherwise is not the value."""
    assert block_scalar(FRAGMENT, "persona") == (">-", "A folded contract.")


def test_a_key_that_opens_no_block_scalar_is_not_found() -> None:
    assert block_scalar(FRAGMENT, "done_when") is None
    assert block_scalar(FRAGMENT, "nothing_here") is None


def test_a_block_scalar_with_no_block_under_it_reads_empty() -> None:
    """The degenerate shape still reads, rather than dividing by an empty block."""
    assert block_scalar("persona: |\nname: probe\n", "persona") == ("|", "")


def test_a_field_is_read_in_whichever_shape_it_was_written() -> None:
    """`researcher` and `reviewer` state `done_when` inline; the others open a block."""
    assert field(FRAGMENT, "done_when") == "one quoted line"
    assert field(FRAGMENT, "persona") == "A folded contract."
    assert field("user:\n  done_when: 'single quoted'\n", "done_when") == "single quoted"
    assert field("user:\n  max_turns: 8\n", "max_turns") == "8"
    assert field(FRAGMENT, "absent") is None


def test_a_fragment_ends_where_its_successor_opens_a_second_document() -> None:
    """A repeated column-zero key is the boundary when the next blob leads with comments."""
    successor = (
        "# A general implementation role.\n# Ported from elsewhere.\n"
        "name: next\nuser:\n  persona: |\n    Another.\n"
    )
    read = yaml_fragment(FRAGMENT + successor)

    assert "name: next" not in read
    assert read.endswith("max_turns: 8"), read
    assert field(read, "persona") == "A folded contract."


def test_a_fragment_ends_where_its_successor_is_not_a_continuation() -> None:
    """The other boundary: the next persona's name literal laid down adjacent."""
    read = yaml_fragment(FRAGMENT + "planner# A general decomposition role.\n")

    assert "planner#" not in read
    assert read.endswith("max_turns: 8"), read


def test_a_fragment_that_runs_to_the_end_of_its_text_is_read_whole() -> None:
    """Nothing follows the last shipped role, so exhausting the window is not an error."""
    assert yaml_fragment(FRAGMENT).strip("\n") == FRAGMENT.strip("\n")


def test_a_fragment_opening_on_no_key_at_all_is_still_read() -> None:
    """A blob that opens indented has no key to repeat, and must not crash on that."""
    assert yaml_fragment("  orphaned: value\nname: probe\n") == "  orphaned: value\nname: probe"


@pytest.mark.parametrize("role", SHIPPED_ROLES)
def test_each_shipped_role_resolves_to_a_bar_with_content(role: str) -> None:
    """The measurement this guard rests on, taken against the real binary.

    A bare `persona` name resolves to a role compiled into the `oneagentgraph`
    `onepipeline` links. If one of these stopped resolving, the guard would fall
    through to reading it as a path and refuse every plan that names it — which is
    what a dispatch does too, so the failure is the honest one.
    """
    bar = resolve_bar(role)

    assert role in bar.source and "onepipeline" in bar.source
    assert len(bar.text) > 100, bar.text


def test_a_role_carries_its_own_completion_bar_alongside_the_shared_one() -> None:
    """`reviewer` states `done_when` inline, and it is enforced beside the base's.

    `personas/README.md` records the composed criterion a real `reviewer` dispatch's
    supervisor was handed — "Both of these must hold: 1. every acceptance criterion
    … 2. the review reports verified, severity-ranked findings". A reader that saw
    only one of the two would resolve half the bar and demand half of what a judge
    will.
    """
    bar = resolve_bar("reviewer")

    assert "every acceptance criterion stated in the task is met" in bar.text
    assert "severity-ranked findings each tied to specific code" in bar.text


def test_no_persona_leaves_the_generic_contract_standing() -> None:
    """A node that names no role is judged by `config/onejudge.base.yaml` alone.

    What is left of that generic contract is the shared completion clause and nothing
    else: the base config states no `user.persona`, because every dispatch replaces that
    field rather than merging it. So this reads the clause that does survive — a bar
    resolving to the empty string here would be refused by `_stated` rather than passing
    quietly, and that refusal is what this asserts the absence of.
    """
    bar = resolve_bar(None)

    assert "onejudge.base.yaml" in bar.source
    assert "every acceptance criterion stated in the task is met" in bar.text


def test_a_path_named_persona_resolves_to_the_file_it_names() -> None:
    """The one shape in `personas/` that does dispatch: named as a path from `graphs/`."""
    bar = resolve_bar("../personas/orchestrator.yaml")

    assert bar.source.startswith("personas/orchestrator.yaml")
    assert bar.text


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway checkout with this repository's real base config and its own graphs.

    A persona ref is resolved from `graphs/` and refused when it lands outside the
    checkout, so a journey about a persona *file* needs a checkout to put one in.
    The base config is copied rather than invented: what composes onto a role's bar
    is the real shared contract, and a fake one would prove composition against
    nothing this host ships.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "onejudge.base.yaml").write_bytes(
        (REPO_ROOT / criteria_guard.BASE_CONFIG).read_bytes()
    )
    (tmp_path / "graphs").mkdir()
    monkeypatch.setattr(criteria_guard, "REPO_ROOT", tmp_path)
    return tmp_path


def test_a_role_that_replaces_the_base_bar_stands_in_for_it(checkout: Path) -> None:
    """`user.done_when_replaces_base` is the one way the shared clause is dropped."""
    (checkout / "graphs" / "standalone.yaml").write_text(
        "name: standalone\nuser:\n  persona: |\n    Only mine.\n"
        "  done_when: 'only my bar holds'\n  done_when_replaces_base: true\n",
        encoding="utf-8",
    )

    bar = resolve_bar("standalone.yaml")

    assert "only my bar holds" in bar.text
    assert "every acceptance criterion stated in the task is met" not in bar.text


def test_a_persona_file_that_states_no_review_bar_at_all_is_refused(checkout: Path) -> None:
    """A file naming neither review field is inert, and a plan's author cannot see that.

    The node is reviewed against the base config's generic contract instead, which is
    a bar — so nothing downstream reports that the file the plan named was never read.
    """
    (checkout / "graphs" / "inert.yaml").write_text(
        "name: inert\nsystem_prompt: |\n  A role and nothing else.\n", encoding="utf-8"
    )

    with pytest.raises(CriteriaError, match="states neither `user.persona`"):
        resolve_bar("inert.yaml")


def test_a_persona_ref_that_climbs_out_of_the_checkout_is_refused(checkout: Path) -> None:
    """A ref is read from `graphs/` in the launch directory, so an escape is unportable.

    Nothing stops `../../elsewhere.yaml` resolving on the host that wrote the plan —
    and nothing makes it resolve anywhere else, so the node dispatches once and then
    never again.
    """
    (checkout.parent / "elsewhere.yaml").write_text(
        "name: elsewhere\nuser:\n  persona: |\n    Outside.\n", encoding="utf-8"
    )

    with pytest.raises(CriteriaError, match="outside this checkout"):
        resolve_bar("../../elsewhere.yaml")


def test_a_bar_that_resolved_to_nothing_is_refused_rather_than_passing_everything(
    checkout: Path,
) -> None:
    """An empty bar makes every plan pass, for the one reason that proves nothing.

    The bar composes from files this module does not own, so a field renamed in
    either would empty it silently — and a guard that accepts everything looks
    exactly like a guard that found nothing wrong.
    """
    (checkout / "config" / "onejudge.base.yaml").write_text("session: probe\n", encoding="utf-8")

    with pytest.raises(CriteriaError, match="states neither a review contract"):
        resolve_bar(None)


@pytest.mark.parametrize("unshipped", ("orchestrator", "check-in", "pr-author", "no-such-role"))
def test_a_name_that_is_neither_shipped_nor_a_path_is_refused(unshipped: str) -> None:
    """The refusal a dispatch would make, made before the dispatch is spent.

    `oneagentgraph` reads any name no built-in claims as a path relative to
    `graphs/`, so `persona: orchestrator` settles `failed` on `cannot read
    graphs/orchestrator` rather than running the monitor role. Catching it here
    costs nothing; catching it there costs a scheduled node.
    """
    with pytest.raises(CriteriaError) as refused:
        resolve_bar(unshipped)

    assert "graphs/" in str(refused.value)
    assert unshipped in str(refused.value)


def test_a_binary_declaring_one_role_twice_is_refused_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two anchors mean the bar cannot be read from it, which is not a bar to guess at."""
    monkeypatch.setattr(
        criteria_guard, "_engine_bytes", lambda: b"\nname: twice\nuser:\n\nname: twice\nuser:\n"
    )

    with pytest.raises(CriteriaError, match="more than one persona"):
        builtin_persona("twice")


@pytest.mark.parametrize(
    "shipped",
    (
        pytest.param(b"\nname: bare\nmax_turns: 3\n", id="no-review-contract"),
        pytest.param("\nname: bare\nuser:\n  persona: |\n    Cut\ufffd\n".encode(), id="corrupt"),
    ),
)
def test_a_role_that_does_not_read_back_whole_is_refused(
    monkeypatch: pytest.MonkeyPatch, shipped: bytes
) -> None:
    """Finding the name is not finding the bar, and the difference must be loud.

    The window this is lifted from is cut at a byte offset, so its tail routinely
    lands mid-character and is decoded leniently. A replacement character inside the
    fragment itself is something else: the role's own text did not survive, and
    checking a plan against a corrupted bar is worse than not checking it.
    """
    monkeypatch.setattr(criteria_guard, "_engine_bytes", lambda: shipped)

    with pytest.raises(CriteriaError, match="does not read back as a whole persona"):
        builtin_persona("bare")


def test_a_name_no_role_claims_reads_as_absent_rather_than_as_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(criteria_guard, "_engine_bytes", lambda: b"\nname: other\nuser:\n")

    assert builtin_persona("absent") is None


def test_no_bar_can_be_resolved_without_the_engine_that_ships_the_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing `onepipeline` is named as the reason, not silently treated as no role."""
    criteria_guard._engine_bytes.cache_clear()
    monkeypatch.setattr(plan_store.shutil, "which", lambda _: None)
    try:
        with pytest.raises(CriteriaError, match="is not on PATH"):
            criteria_guard._engine_bytes()
    finally:
        criteria_guard._engine_bytes.cache_clear()


NOTHING_DEMANDED = Bar("a bar that demands nothing", "Accept it when it is done.")


def test_the_criteria_block_stops_at_the_next_heading_of_any_depth() -> None:
    """The appendix is spelled `### …`, and sweeping it in fired on commands that
    were never criteria."""
    assert criteria_block(_task("- One thing.")).strip() == "- One thing."


#: A task whose prose *names* the acceptance-criteria heading before opening it. The node
#: whose job is to document this tier writes exactly this, and so does any task quoting
#: the heading mid-sentence.
NAMES_THE_HEADING_IN_PROSE = (
    "## What\n\nEvery demand this node is held to is stated in its "
    f"`{CRITERIA_HEADING}`, never only in the prose around it.\n\n"
    "## Why\n\nThe user asked for it.\n\n"
    f"{CRITERIA_HEADING}\n\n- `just gate` is green.\n\n{APPENDIX}"
)


def test_prose_that_merely_names_the_heading_neither_opens_nor_closes_the_block() -> None:
    """The block is the one the heading *opens*, and both halves of getting that wrong fail.

    Located by the first occurrence of the heading's text anywhere, this task's block
    began at the mention inside `## What` and ended at `## Why` — so the criterion the
    node actually states sat in the half no rule read. The quiet half is the dangerous
    one: the refusal below is loud, while a task whose every criterion goes unexamined
    looks exactly like a sound plan.
    """
    assert criteria_block(NAMES_THE_HEADING_IN_PROSE).strip() == "- `just gate` is green."

    with pytest.raises(CriteriaError, match="`just` invocation"):
        check(NAMES_THE_HEADING_IN_PROSE, "probe", NOTHING_DEMANDED)


def test_a_task_that_opens_that_heading_twice_is_refused_by_name() -> None:
    """Which block states the node's bar cannot be decided from such a task.

    The judge is handed the whole task and reads both, so a reader here that picked
    either would be checking one while the dispatch is judged against the other. Naming
    the ambiguity is the same answer `orchestrator/plan_store.py` gives a record that
    opens `metadata` twice.
    """
    twice = f"{_task('- The thing is done.')}\n{CRITERIA_HEADING}\n\n- `just gate` is green.\n"

    with pytest.raises(CriteriaError) as refused:
        check(twice, "probe", NOTHING_DEMANDED)

    reported = str(refused.value)
    assert f"opens {CRITERIA_HEADING!r} 2 times" in reported, reported
    assert "Leave one block of criteria" in reported, reported


#: A heading that *starts with* the criteria heading's text and is a different heading:
#: the section a task documenting this tier writes to show what criteria look like.
LONGER_HEADING = f"{CRITERIA_HEADING} examples"


def test_a_longer_heading_that_starts_with_that_text_is_a_different_heading() -> None:
    """Opening the block is the whole heading, not a line that begins with one.

    Read as a prefix, `## Acceptance criteria examples` fails both of the ways prose
    naming the heading did, one step further out. Beside the real heading it made a
    sound task read as opening its criteria twice, and the node was refused for stating
    them once. Alone it was the quiet half again: the block silently became the one
    *that* heading opens, so every line the tier examined was something no criterion had
    ever claimed.
    """
    beside = (
        "## What\n\nDo the thing.\n\n"
        f"{LONGER_HEADING}\n\nThey are stated as properties.\n\n"
        "## Why\n\nThe user asked for it.\n\n"
        f"{CRITERIA_HEADING}\n\n- The thing is done.\n\n{APPENDIX}"
    )

    assert criteria_block(beside).strip() == "- The thing is done."
    check(beside, "probe", NOTHING_DEMANDED)

    alone = f"## What\n\nDo the thing.\n\n{LONGER_HEADING}\n\n- A criterion reads like this.\n"

    with pytest.raises(CriteriaError, match=f"no {CRITERIA_HEADING!r} section"):
        criteria_block(alone)


def test_trailing_whitespace_is_not_part_of_the_heading_a_reader_sees() -> None:
    """It opens the block, because whitespace nobody can see decides nothing here."""
    padded = _task("- The thing is done.").replace(CRITERIA_HEADING, f"{CRITERIA_HEADING}  ")

    assert criteria_block(padded).strip() == "- The thing is done."


#: This repository's own plan-reading recipes, named as the nouns a criterion about the
#: plan tooling has to be able to name. A `just` invocation is exempt only where the
#: recipe it names reads, reviews, or launches a plan: none of those is a check a worker
#: runs over its own change, so naming one is never the "run this exact invocation"
#: demand the procedure check exists to refuse.
PLAN_TOOLING_CRITERIA = (
    "- `just check-plan` refuses a plan whose node names its repository twice.\n"
    "- `just review-plan` records a pass and never a refusal.\n"
    "- `just orchestrate` launches the plan it is given.\n"
    "- `just plans` lists the project the plan was written into."
)


def test_a_criterion_naming_this_repositorys_plan_recipes_is_not_a_procedure() -> None:
    """The exemption, and the refusal it makes room for, in one place.

    Scanning continues past an exempt match rather than stopping at it, so a block that
    names the plan tooling *and* a gate is still refused for the gate — an exemption
    that stopped the scan would be the hole rather than the fix.
    """
    check(_task(f"{PLAN_TOOLING_CRITERIA}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)

    with pytest.raises(CriteriaError, match="`just` invocation"):
        check(
            _task(f"{PLAN_TOOLING_CRITERIA}\n- `just gate` is green.\n{COMPLETE}"),
            "probe",
            NOTHING_DEMANDED,
        )


def test_a_criterion_leaving_a_backtick_run_unclosed_is_refused_by_name() -> None:
    """The imbalance is named, and the quote stays inside the criterion that has it.

    An unpaired backtick pairs with the next criterion's, so every later pattern reads
    a span its author never wrote — which is how one block was refused for "naming a
    shell invocation", quoting a match that ended at the word `git` in "real git
    repositories" three criteria later.
    """
    block = (
        "- A plan carrying `onepipeline.deps for an in-plan edge is refused.\n"
        "- The journeys drive real `git` repositories rather than fixtures of them.\n"
        f"{COMPLETE}"
    )

    with pytest.raises(CriteriaError) as refused:
        check(_task(block), "probe", NOTHING_DEMANDED)

    reported = str(refused.value)
    assert "backtick run unclosed" in reported
    assert "onepipeline.deps for an in-plan edge" in reported
    assert "real `git` repositories" not in reported
    assert "shell invocation" not in reported


def test_a_task_with_no_criteria_at_all_is_refused() -> None:
    with pytest.raises(CriteriaError, match="Acceptance criteria"):
        check("## What\n\nNo bar at all.\n", "probe", NOTHING_DEMANDED)


@pytest.mark.parametrize(
    ("criterion", "reason"),
    (
        ("- The branch publishes.", "cannot"),
        ("- The pull request is merged.", "cannot"),
        ("- The behavior is documented as described above.", "reconstruct"),
        ("- The note repeats the exact phrase from the brief.", "particular string"),
        ("- `just gate` is green.", "properties"),
        ("- The suite passes && the gate is green.", "properties"),
        ("- `pytest tests/` passes.", "properties"),
    ),
)
def test_a_criterion_naming_a_procedure_or_an_absent_event_is_refused(
    criterion: str, reason: str
) -> None:
    """Each of these has failed finished work by being read literally.

    A judge cannot accept an equivalent route for a command string, and cannot reach
    state that only exists after the dispatch has settled — so the more precisely
    either is written as a criterion, the more certainly correct work fails it.
    """
    with pytest.raises(CriteriaError, match=reason):
        check(_task(criterion), "probe", NOTHING_DEMANDED)


#: A bar and a task that each demand end-to-end proof of a node whose criteria never
#: mention it. Both carriers, because a demand reaches a judge from either and this tier
#: used to answer both: the bar a persona resolves to, and the operational notes the
#: task's own `## Additional info` carries.
SILENT_ABOUT_A_DEMAND = (
    pytest.param(
        Bar("the built-in role", "Do not accept done until it is proven end to end."),
        APPENDIX,
        id="the-bars-demand",
    ),
    pytest.param(
        NOTHING_DEMANDED,
        "## Additional info\n\nProve the change end to end before you settle.\n",
        id="the-tasks-own-demand",
    ),
)


@pytest.mark.parametrize(("bar", "additional"), SILENT_ABOUT_A_DEMAND)
def test_a_demand_the_criteria_are_silent_about_is_left_to_the_judged_tier(
    bar: Bar, additional: str
) -> None:
    """This tier stopped matching phrases, and both carriers of a demand go with it.

    Refusing criteria for *not saying* something is a question about meaning, and asking
    it here by pattern made the two tiers refuse each other's required wording: a review
    refused a criterion for pinning a spelling while this check refused the same task for
    lacking a literal phrase its criteria stated across three sentences of their own. The
    demand itself is unchanged — `personas/planner.yaml` still asks for it and
    `orchestrator/plan_review.py`'s judged turn is now what reads whether the criteria
    answer it, by meaning.
    """
    check(_task("- The thing is done.\n- A report names the evidence.", additional), "probe", bar)


def test_a_criterion_answering_a_demand_in_its_own_words_is_accepted_too(appendix: Path) -> None:
    """The other side of that: nothing here rules on the wording either way.

    Read through `check_plan`, whose count is the observable answer — a check that only
    declined to raise would pass just as well if it had stopped looking at all.
    """
    quiet = _task("- The thing is done.", additional="## Additional info\n\nNothing is asked.\n")
    appendix.write_text("## Additional info\n\nNothing is asked.\n", encoding="utf-8")

    assert check_plan(_plan(task=quiet)) == 1


def test_criteria_that_answer_every_demand_are_accepted(appendix: Path) -> None:
    """The whole accepting path under the real `engineer` bar, counted rather than assumed."""
    assert check_plan(_plan(persona="engineer", task=_task(COMPLETE))) == 1


@pytest.mark.parametrize(
    "criteria",
    [
        pytest.param(COMPLETE, id="the-property"),
        pytest.param(REPORTS_BY_THE_WITHDRAWN_WORDING, id="the-withdrawn-ordering"),
    ],
)
def test_a_node_under_the_real_role_is_accepted_whichever_wording_it_reports_in(
    appendix: Path, criteria: str
) -> None:
    """Both wordings reach a dispatch, under the bar the engine binary really ships.

    Kept as a pair after the phrase matching moved to the judged tier, because the pair
    is what says this tier rules on neither: the property this host asks for — every
    claim about the finished work true of the tree as it finally stands — and the older
    wording that names a report, which plans written before that narrowing still carry.
    """
    assert check_plan(_plan(persona="engineer", task=_task(criteria))) == 1


def test_a_task_rebuilt_from_a_stale_appendix_is_refused(appendix: Path) -> None:
    """A builder cloned before an appendix fix reintroduces the wording it removed.

    The refusal names two places the appendix can be had, and both are openable from
    where the party that has to act stands: the variable every planning launch hands its
    dispatch, and an absolute path on this host. It named a path relative to this
    checkout, which a planner working in another repository's worktree cannot open at
    all — so the planner that met this refusal was told to read a file that does not
    exist from there, and a manager appended the text by hand instead.
    """
    appendix.write_text(APPENDIX + "\nA rule that was added since.\n", encoding="utf-8")

    with pytest.raises(CriteriaError) as refused:
        check_appendix(_task(COMPLETE), "probe")

    reported = str(refused.value)
    assert "current operational appendix" in reported, reported
    assert f"${APPENDIX_ENV}" in reported, reported
    assert str(appendix) in reported, reported


def test_the_text_a_task_must_carry_is_the_text_the_launch_hands_over(appendix: Path) -> None:
    """One reader for both ends of that requirement, which is what makes it answerable.

    `scripts/dispatch-appendix-env.sh` exports what this function answers, so what a
    planning dispatch is handed is byte-for-byte what the check demands as a substring
    rather than a second rendering of the same file — which is how the two come to differ
    by a trailing newline nobody can see.
    """
    appendix.write_text(f"\n\n{APPENDIX}\n\n", encoding="utf-8")

    assert appendix_text() == APPENDIX.strip()
    check_appendix(_task(COMPLETE, additional=appendix_text()), "probe")


def test_only_the_nodes_that_dispatch_an_agent_are_checked() -> None:
    """A human node carries an action rather than a task, and a lifecycle node's
    steps are the dispatches rather than the node above them."""
    plan = {
        "tasks": [
            {"id": "approve", "kind": "human", "task": "Merge it."},
            {"id": "direct", "task": "prose"},
            {
                "id": "lifecycle",
                "repo": "somewhere",
                "steps": [{"id": "one", "task": "prose"}, {"id": "two", "task": "prose"}],
            },
        ]
    }

    assert [node.id for node in dispatched_nodes(plan)] == [
        "direct",
        "lifecycle/one",
        "lifecycle/two",
    ]


@pytest.mark.parametrize(
    ("plan", "reason"),
    (
        ("not a plan at all", "the plan is str, not an object"),
        ({"name": "probe"}, "the plan states no `tasks`"),
        ({"tasks": {"probe": {}}}, "the plan's `tasks` is dict, not a list"),
        ({"tasks": ["probe"]}, "`tasks[0]` is str, not an object"),
        ({"tasks": [{"id": "life", "steps": 3}]}, "life's `steps` is int, not a list"),
        ({"tasks": [{"id": "life", "steps": ["one"]}]}, "life's `steps[0]` is str, not an object"),
        ({"tasks": [{"id": "probe", "task": "prose", "persona": 7}]}, "`persona` is int"),
        ({"tasks": [{"id": 7, "task": "prose"}]}, "states `id` as int"),
        ({"tasks": [{"id": "probe", "kind": "review", "task": "prose"}]}, "`kind` is 'review'"),
        (
            {"tasks": [{"id": "life", "steps": [{"id": [], "task": "prose"}]}]},
            "life's `steps[0]` states `id` as list",
        ),
    ),
)
def test_a_plan_whose_shape_is_wrong_is_refused_by_the_field_that_is_wrong(
    plan: object, reason: str
) -> None:
    """A plan is a JSON document some other tool wrote, so its shape is untrusted.

    Reaching into it and hoping raises whatever the shape happens to produce, at a
    frame the plan's author cannot act on. Each refusal here names the field instead,
    which is the whole difference between a diagnostic and a traceback.
    """
    with pytest.raises(CriteriaError) as refused:
        check_plan(plan)

    assert reason in str(refused.value)


def test_a_node_that_dispatches_but_states_no_task_is_refused(appendix: Path) -> None:
    with pytest.raises(CriteriaError, match="states no `task` string"):
        check_plan(_plan(persona="engineer"))


@pytest.fixture
def project_record(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The project record the store answers the direct path for every project here.

    Stood in at the store CLI, the one boundary the plan-level record crosses into
    `check_directly`, the way `tests/test_plan_check.py` stands it in for the spawned
    check: it starts carrying no record, and `_planned` writes the key for a plan into it
    the way `just review-plan` would.
    """
    record: dict[str, object] = {"metadata": {}}
    monkeypatch.setattr(plan_store, "project_record", lambda _project: record)
    return record


def _planned(record: dict[str, object], plan: dict[str, object]) -> dict[str, object]:
    """``plan``, once ``record`` carries a plan-level record for exactly it."""
    key = plan_review.plan_key(plan, plan_review.plan_bar_fingerprint())
    record["metadata"] = {plan_review.RECORD_KEY: {"key": key, "by": plan_review.BY_REVIEW}}
    return plan


def test_the_command_accepts_a_plan_that_states_its_bar(
    appendix: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_record: dict[str, object],
) -> None:
    plan = _planned(project_record, _plan(persona="engineer", task=_task(COMPLETE)))
    monkeypatch.setattr(plan_store, "read_project", lambda _: (plan, []))

    assert check_directly("authoring:complete") == 0
    assert "1 dispatched node(s)" in capsys.readouterr().out


def test_the_command_refuses_a_plan_that_does_not(
    appendix: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = _task(f"{RELEASED_ELSEWHERE[0]}\n{COMPLETE}")
    monkeypatch.setattr(
        plan_store, "read_project", lambda _: (_plan(persona="engineer", task=refused), [])
    )

    assert check_directly("authoring:incomplete") == 1
    assert "check-plan:" in capsys.readouterr().err


def test_the_command_refuses_a_task_whose_issue_body_the_board_would_refuse(
    appendix: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The direct path refuses the size the spawned check refuses, naming the same three things.

    Over the store's records rather than the assembled plan, because the plan
    `read_plan` assembles carries no metadata map and the map is part of the body: the
    node's own task here is sound, and the record beside it is what is over the limit.
    """
    padded = _task(COMPLETE) + "\n## Planner context\n\n" + "Carried context. " * 4_000
    plan = _plan(persona="engineer", task=_task(COMPLETE))
    record = dataclasses.replace(_reviewable(), content=padded)
    monkeypatch.setattr(plan_store, "read_project", lambda _: (plan, [record]))

    assert check_directly("authoring:padded") == 1
    reported = capsys.readouterr().err
    assert reported.startswith("check-plan: probe: task: its composed issue body"), reported
    assert f"{task_body.BODY_LIMIT:,}-character limit" in reported, reported


def test_the_command_warns_about_a_task_between_the_thresholds_and_accepts_it(
    appendix: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_record: dict[str, object],
) -> None:
    """Warned over the records, whose map is the one the copy sends, and still accepted."""
    plan = _planned(project_record, _plan(persona="engineer", task=_task(COMPLETE)))
    large = dataclasses.replace(_reviewable(), content="x" * task_body.WARN_FROM)
    key = plan_review.review_key(large, plan_review.bar_fingerprint())
    warned = dataclasses.replace(
        large, metadata={**large.metadata, plan_review.RECORD_KEY: {"key": key}}
    )
    monkeypatch.setattr(plan_store, "read_project", lambda _: (plan, [warned]))

    assert check_directly("authoring:probe") == 0
    captured = capsys.readouterr()
    assert captured.err.startswith("check-plan: warning: probe: "), captured.err
    assert f"{task_body.BODY_LIMIT:,}-character limit" in captured.err, captured.err
    assert "carries a review record" in captured.out, captured.out


def test_the_command_reports_a_checkout_that_cannot_answer_what_the_bar_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A checkout missing its own base config says so, rather than raising through.

    The plan was readable and nothing about it was refused — what failed is this
    checkout — so it exits 2 like an unreadable plan and names the recipe that
    repairs it.
    """
    monkeypatch.setattr(criteria_guard, "REPO_ROOT", tmp_path / "no-such-checkout")
    monkeypatch.setattr(
        plan_store, "read_project", lambda _: (_plan(persona="engineer", task=_task(COMPLETE)), [])
    )

    assert check_directly("authoring:complete") == 2
    reported = capsys.readouterr().err
    assert "review configuration" in reported, reported
    assert "just bootstrap" in reported, reported


def test_the_command_separates_an_unreadable_plan_from_a_refused_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 rather than 1: an unreadable project leaves nothing to judge."""
    monkeypatch.setattr(
        plan_store, "read_project", lambda _: (_ for _ in ()).throw(OSError("missing"))
    )
    assert check_directly("authoring:absent") == 2
    assert "cannot read" in capsys.readouterr().err


#: A bar in the shape a role that may not touch the tree states one. Synthetic, so
#: that what these journeys attribute a refusal to is the pairing rather than any one
#: release's wording; `test_the_shipped_researcher_is_the_role_this_refuses` is where
#: the same check meets the real `researcher` bar the engine ships.
FORBIDS = Bar("a read-only role", "Accept it when no project files were changed.")


def test_each_criterion_is_read_on_its_own_with_the_lines_it_wraps_to() -> None:
    """The conflict is a property of one criterion, so the block is split into them.

    Read whole, a path named in a read-only criterion would pair with a verb from the
    criterion after it, and a sound plan would be refused for a sentence nobody wrote.
    """
    block = (
        "\n- The answer cites `docs/x.md`\n  and the line it rests on.\n"
        "- Confirmed facts are separated from inferences.\n\n"
        "- The report names its sources.\n\n"
    )

    assert list(criteria_items(block)) == [
        "- The answer cites `docs/x.md`\n  and the line it rests on.",
        "- Confirmed facts are separated from inferences.",
        "- The report names its sources.",
    ]


def test_a_bar_that_forbids_changes_under_criteria_that_require_one_is_refused() -> None:
    """The plan that could not have succeeded, refused for the reason it could not.

    Both halves have to reach the author: which demand the persona's bar makes, and
    which criterion contradicts it. Naming only the persona leaves them guessing at
    which of several criteria to move.
    """
    with pytest.raises(CriteriaError) as refused:
        check_changes_allowed(
            "- `docs/fern-limitations.md` gains a row for each limitation.", "probe", FORBIDS
        )

    reported = str(refused.value)
    assert "a read-only role forbids this dispatch changing project files" in reported
    assert "no project files were changed" in reported
    assert "require `docs/fern-limitations.md` to change" in reported


def test_the_refusal_names_a_persona_whose_bar_would_permit_the_work() -> None:
    """A refusal that does not say what to name instead is a refusal to work around.

    The names are resolved out of the engine binary rather than listed here, so what
    is offered is a role that would really dispatch and really permit the edit.
    """
    with pytest.raises(CriteriaError) as refused:
        check_changes_allowed("- `AGENTS.md` gains a paragraph.", "probe", FORBIDS)

    assert "`docs-writer`" in str(refused.value)
    assert set(permitting_roles()) == set(SHIPPED_ROLES) - {"researcher"}


def test_the_shipped_researcher_is_the_role_this_refuses() -> None:
    """The measurement the check rests on, taken against the binary a dispatch runs.

    `researcher` is not named anywhere in the check — what refuses it is its own bar,
    read out of the `oneagentgraph` `onepipeline` links. A release that dropped the
    file-modification clause would stop the refusal here with nothing to edit, which
    is the point: the clause is invisible from `personas/`, so a list of names here
    would be a copy of one release's roles.
    """
    with pytest.raises(CriteriaError, match="modified project files"):
        check_changes_allowed("- `docs/x.md` gains a row.", "probe", resolve_bar("researcher"))

    for permitted in permitting_roles():
        check_changes_allowed("- `docs/x.md` gains a row.", "probe", resolve_bar(permitted))


@pytest.mark.parametrize(
    "criterion",
    (
        pytest.param("- The answer cites `docs/x.md` for every claim it makes.", id="cited"),
        pytest.param("- The report names `orchestrator/labels.py` and its line.", id="named"),
        pytest.param("- A new section of the answer separates fact from inference.", id="no-path"),
        pytest.param("- The finding covers docs/x.md and is added to the answer.", id="unquoted"),
    ),
)
def test_a_read_only_node_that_merely_names_a_file_is_left_alone(
    criterion: str, appendix: Path
) -> None:
    """The false refusal this check is written to avoid, in the shapes it would take.

    Naming a file is what a role forbidden to touch the tree is *for*, and an unquoted
    path is deliberately missed: `and/or` is a path by any looser reading, and a plan
    refused for a sound criterion is one that gets worked around.

    Read through `check_plan` under the real shipped `researcher`, whose count is the
    observable answer: a check that only declined to raise would pass just as well if
    it had stopped looking, and a synthetic bar would prove it against nothing.
    """
    node = _plan(persona="researcher", task=_task(f"{COMPLETE}\n{criterion}"))

    assert check_plan(node) == 1


def test_a_bar_that_permits_changes_never_reads_the_criteria_at_all(appendix: Path) -> None:
    """Neither half is a fault alone: an editing criterion is right for an editing bar."""
    editing = _task(f"{COMPLETE}\n- `docs/x.md` gains a row.")

    assert check_plan(_plan(persona="engineer", task=editing)) == 1


#: An engine declaring one role that may not touch the tree and one that may, so the
#: correction the refusal offers has exactly one name to give.
ONE_ROLE_PERMITS = (
    b"\nname: reader\nuser:\n  persona: 'no project files were changed'\n"
    b"\nname: writer\nuser:\n  persona: 'edit whatever the task needs'\n"
)


def test_a_role_the_engine_declares_twice_is_passed_over_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name that cannot be resolved is not a name to offer, and not a second failure.

    The author is being told which persona to name; turning the engine's packaging
    into the refusal they read would send them somewhere they cannot act.
    """
    monkeypatch.setattr(
        criteria_guard,
        "_engine_bytes",
        lambda: (
            b"\nname: twice\nuser:\n  persona: 'x'\n\nname: twice\nuser:\n  persona: 'x'\n"
            + ONE_ROLE_PERMITS
        ),
    )

    assert builtin_persona_names() == ("twice", "reader", "writer")
    assert permitting_roles() == ("writer",)


def test_the_remedy_offers_the_roles_this_engine_really_ships(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The correction is the engine's own answer, not a list written here."""
    monkeypatch.setattr(criteria_guard, "_engine_bytes", lambda: ONE_ROLE_PERMITS)

    with pytest.raises(CriteriaError) as refused:
        check_changes_allowed("- `docs/x.md` gains a row.", "probe", FORBIDS)

    reported = str(refused.value)
    assert "name a persona whose bar permits the edit (`writer`)" in reported
    assert "`reader`" not in reported


def test_an_engine_whose_every_role_forbids_changes_says_so_rather_than_offering_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal still has to be actionable when there is no persona to point at."""
    monkeypatch.setattr(
        criteria_guard,
        "_engine_bytes",
        lambda: b"\nname: reader\nuser:\n  persona: 'no project files were changed'\n",
    )

    with pytest.raises(CriteriaError) as refused:
        check_changes_allowed("- `docs/x.md` gains a row.", "probe", FORBIDS)

    assert "none of the shipped roles does" in str(refused.value)
    assert "move the editing into a node of its own" in str(refused.value)


def test_the_tracked_appendix_is_where_the_guard_reads_it_from() -> None:
    """The promotion this module is half of: the appendix is tracked, not scratch."""
    assert (REPO_ROOT / criteria_guard.APPENDIX).is_file()


def _reviewable(**metadata: object) -> plan_store.StoreTask:
    return plan_store.StoreTask(
        qualified_id="authoring:probe/probe",
        node_id="probe",
        title="feat: probe",
        content="## What\n\nProbe.\n",
        metadata={"onepipeline.id": "probe", **metadata},
        repositories=[],
        deps=(),
    )


def test_the_command_refuses_a_task_nothing_has_reviewed(
    appendix: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_record: dict[str, object],
) -> None:
    """Content nothing has reviewed is what reaches a dispatch when nobody is watching.

    Both plans this gate exists to catch were single-node plans an operator wrote and
    launched with no planner, so `personas/planner.yaml`'s judge — the thing that exists
    to catch exactly this — never saw their criteria.
    """
    plan = _planned(project_record, _plan(persona="engineer", task=_task(COMPLETE)))
    monkeypatch.setattr(plan_store, "read_project", lambda _: (plan, [_reviewable()]))

    assert check_directly("authoring:probe") == 1
    reported = capsys.readouterr().err
    assert "no review record" in reported, reported
    assert "probe" in reported, reported
    assert "just review-plan authoring:probe" in reported, reported


def _recorded() -> plan_store.StoreTask:
    """A task carrying a record for exactly its own content."""
    task = _reviewable()
    key = plan_review.review_key(task, plan_review.bar_fingerprint())
    return _reviewable(**{plan_review.RECORD_KEY: {"key": key}})


def test_the_command_accepts_a_recorded_pass_without_spending_a_judged_turn(
    appendix: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_record: dict[str, object],
) -> None:
    """A recorded pass is authoritative: a second opinion is how one tree gets two verdicts."""
    plan = _planned(project_record, _plan(persona="engineer", task=_task(COMPLETE)))
    monkeypatch.setattr(plan_store, "read_project", lambda _: (plan, [_recorded()]))
    monkeypatch.setattr(
        plan_review, "verdict", lambda _: pytest.fail("a recorded pass spent a judged turn")
    )

    assert check_directly("authoring:probe") == 0
    assert "carries a review record" in capsys.readouterr().out


def test_the_command_refuses_a_plan_every_task_of_which_is_recorded_and_no_plan_level_record(
    appendix: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_record: dict[str, object],
) -> None:
    """The direct path reads the plan-level record the spawned check reads, and refuses
    without it rather than falling through to `accepted`.

    Every task carries a record and the project carries none: nothing has read the plan
    whole for the adoption its goal needs, which no task's own record can say. The
    refusal is the one text both paths emit, naming `just review-plan` as the remedy,
    and a record for a *different* plan is no record for this one.
    """
    plan = _plan(persona="engineer", task=_task(COMPLETE))
    monkeypatch.setattr(plan_store, "read_project", lambda _: (plan, [_recorded()]))
    assert project_record == {"metadata": {}}

    assert check_directly("authoring:probe") == 1
    captured = capsys.readouterr()
    assert captured.out == "", captured.out
    assert "no plan-level review record" in captured.err, captured.err
    assert "just review-plan authoring:probe" in captured.err, captured.err
    # The spawned check's refusal about the plan whole is the same text, word for word.
    (spawned,) = [
        one for one in plan_check.refusals(plan, "authoring:probe") if one["node"] is None
    ]
    assert captured.err == f"check-plan: {spawned['reason']}\n"

    _planned(project_record, {**plan, "goal": {"text": "Something else"}})
    assert check_directly("authoring:probe") == 1
    assert "no plan-level review record" in capsys.readouterr().err

    _planned(project_record, plan)
    assert check_directly("authoring:probe") == 0
    assert "carries a review record" in capsys.readouterr().out


def test_the_command_reports_a_checkout_that_cannot_answer_what_the_review_bar_is(
    appendix: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bar is tracked files, so a checkout missing one says so rather than passing."""
    monkeypatch.setattr(
        plan_store,
        "read_project",
        lambda _: (_plan(persona="engineer", task=_task(COMPLETE)), [_reviewable()]),
    )
    monkeypatch.setattr(
        plan_review,
        "bar_fingerprint",
        lambda *_: (_ for _ in ()).throw(OSError("no such file")),
    )

    assert check_directly("authoring:probe") == 2
    reported = capsys.readouterr().err
    assert "fingerprint the review bar" in reported, reported
    assert "just bootstrap" in reported, reported


@pytest.mark.parametrize(
    "criterion",
    [
        "- `Cargo.lock` resolves onevcs to 0.15.4.",
        "- The pin reads v0.16.3.",
        "- The manifest requires >= 1.2.",
        "- The adopted release is 2.0.0-rc.1.",
        "- The lockfile resolves the sibling to the newest release its requirement admits.",
        "- Line coverage stays at 100%.",
    ],
    ids=["three-part", "prefixed", "operator", "prerelease", "property", "percentage"],
)
def test_whether_a_number_is_the_right_number_is_left_to_the_judged_tier(criterion: str) -> None:
    """A version literal is no longer refused here, and the correction is not either.

    Both sides of that are one case now, which is the point: this tier could tell a
    number from a percentage and never which number was right, so it refused the shape
    while the judged turn refused the judgement — and one review prescribed pinning an
    immutable version, which this rule then refused outright. What replaced it is one
    verdict holding both considerations at once, in `orchestrator/plan_review.py`'s
    prompt, where a release already published is admitted as an anchor and a floor a
    later publication overtakes is not.
    """
    check(_task(f"{criterion}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)


@pytest.mark.parametrize("criterion", RELEASED_ELSEWHERE, ids=range(len(RELEASED_ELSEWHERE)))
def test_a_criterion_about_somebody_elses_released_artifact_is_refused(criterion: str) -> None:
    """What stays deterministic is whether the criterion's truth is outside the tree at all.

    Whether a release exists, or carries a named change, is not a fact about the finished
    tree in any wording, and establishing it means going and reading another repository.
    One such criterion required a pin to name a plan-store release carrying two fixes no
    release archive can carry; the worker correctly determined it could not be satisfied,
    and the node was killed and settled by hand with the rest of its work landed.
    """
    with pytest.raises(CriteriaError) as refused:
        check(_task(f"{criterion}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)

    reported = str(refused.value)
    assert "the dispatch cannot do" in reported, reported
    assert "state what this node's own committed content must carry" in reported, reported
    assert "not this node's bar" in reported, reported


@pytest.mark.parametrize(
    "criterion", RELEASED_ELSEWHERE_IN_PROSE, ids=range(len(RELEASED_ELSEWHERE_IN_PROSE))
)
def test_a_criterion_naming_a_release_while_resting_on_the_tree_is_accepted(
    criterion: str,
) -> None:
    """The bound that widening is bought under, including its own recommended correction.

    Every one of these names a release, a version, or a package and rests on the finished
    tree alone — and one of them is the corresponding-content shape the refusal above
    tells its author to write instead, which a detector that refused it would be refusing
    its own remedy for.
    """
    check(_task(f"{criterion}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)


@pytest.mark.parametrize("criterion", RED_BEFORE_GREEN, ids=range(len(RED_BEFORE_GREEN)))
def test_a_criterion_prescribing_red_before_green_is_refused(criterion: str) -> None:
    """The step is good practice and a bad criterion.

    What a criterion can ask for instead is the property the step produces, which the
    finished tree carries; what it asks for as written is a development step nothing in
    the tree records. `docs/plan-review-refusals.md` is where that trade is argued.
    """
    with pytest.raises(CriteriaError, match="red-before-green"):
        check(_task(f"{criterion}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)


@pytest.mark.parametrize(
    "criterion", STATES_THE_PROPERTY_INSTEAD, ids=range(len(STATES_THE_PROPERTY_INSTEAD))
)
def test_the_property_a_red_before_green_demand_stood_in_for_is_accepted(criterion: str) -> None:
    """Including the two that name the demand in prose rather than making it.

    This check refuses a plan outright, so it is written to miss a criterion that names
    its subject rather than to refuse a sound one: the node whose job is to document
    that demand has to be able to say the words.
    """
    check(_task(f"{criterion}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)


@pytest.mark.parametrize(
    "criterion",
    (*PUBLISHED_WITH_A_WORD_IN_THE_WAY, *PUBLICATION_COPULAS),
    ids=range(len(PUBLISHED_WITH_A_WORD_IN_THE_WAY) + len(PUBLICATION_COPULAS)),
)
def test_a_publication_the_dispatch_cannot_reach_is_refused_through_a_word_in_the_way(
    criterion: str,
) -> None:
    """A publication is a publication whatever copula carries it and whatever stands
    between that copula and the participle."""
    with pytest.raises(CriteriaError, match="the dispatch cannot do"):
        check(_task(f"{criterion}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)


@pytest.mark.parametrize("criterion", PUBLICATION_IN_PROSE, ids=range(len(PUBLICATION_IN_PROSE)))
def test_a_criterion_naming_a_publication_without_resting_on_one_is_accepted(
    criterion: str,
) -> None:
    check(_task(f"{criterion}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)


def test_store_json_validates_the_cli_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    def completed(
        code: int, stdout: str = "", stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], code, stdout, stderr)

    monkeypatch.setattr(plan_store.shutil, "which", lambda _: "/test/onetaskgraph")

    monkeypatch.setattr(
        plan_store.subprocess, "run", lambda *args, **kwargs: completed(2, stderr="no")
    )
    with pytest.raises(OSError, match="no"):
        plan_store.store_json(["project", "show", "x:y"])

    monkeypatch.setattr(
        plan_store.subprocess, "run", lambda *args, **kwargs: completed(0, json.dumps([]))
    )
    with pytest.raises(OSError, match="non-object"):
        plan_store.store_json(["project", "show", "x:y"])

    monkeypatch.setattr(plan_store.subprocess, "run", lambda *args, **kwargs: completed(0, "{bad"))
    with pytest.raises(OSError, match="invalid JSON"):
        plan_store.store_json(["project", "show", "x:y"])

    monkeypatch.setattr(
        plan_store.subprocess,
        "run",
        lambda *args, **kwargs: completed(0, json.dumps({"items": []})),
    )
    assert plan_store.store_json(["project", "show", "x:y"]) == {"items": []}


def test_store_json_requires_the_installed_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_store.shutil, "which", lambda _: None)
    with pytest.raises(OSError, match="not installed"):
        plan_store.store_json(["project", "show", "x:y"])


def _store_process_double(tmp_path: Path, mode: str) -> dict[str, str]:
    """Provide malformed external responses through the real subprocess boundary."""
    binary = tmp_path / "onetaskgraph"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "if os.environ['STORE_DOUBLE_MODE'] == 'invalid-json': print('{bad')\n"
        "elif sys.argv[1:3] == ['project', 'show']: "
        "print(json.dumps({'items': [{'item': {'title': 'p', 'metadata': {}}}]}))\n"
        "elif sys.argv[1:3] == ['task', 'list']: "
        "print(json.dumps({'items': [{'id': 'fake:p/a', 'item': "
        "{'title': 'A', 'content': 'A.', 'metadata': "
        "{'onepipeline.id': 'a'}, 'repositories': []}}]}))\n"
        "else: print(json.dumps({'items': [{'to': 7}]}))\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "STORE_DOUBLE_MODE": mode,
        # The store is this command's own to read only on the direct path; through the
        # engine's `plan check` the store is the engine's to read and its refusals are
        # the engine's to report. So this drives the path whose diagnostics it is about.
        plan_check.ENGINE_ENV: str(_engine_without_plan_check(tmp_path)),
    }


def _engine_without_plan_check(tmp_path: Path) -> Path:
    """An engine of the shape this command's direct path exists for.

    Deliberately outside `PATH`: the roles a node's persona resolves to are still read
    out of the installed binary, so shadowing that would answer a different question
    from the one the caller is asking.
    """
    directory = tmp_path / "older-engine"
    directory.mkdir(exist_ok=True)
    binary = directory / "onepipeline"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print(\"error: unrecognized subcommand 'check'\", file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


@pytest.mark.parametrize("mode,message", (("invalid-json", "invalid JSON"), ("edge", "edge")))
def test_the_direct_path_reports_malformed_store_responses(
    tmp_path: Path, mode: str, message: str
) -> None:
    read = subprocess.run(
        [str(Path(sys.executable).with_name("orchestrator-check-plan")), "fake:p"],
        cwd=REPO_ROOT,
        env=_store_process_double(tmp_path, mode),
        text=True,
        capture_output=True,
        check=False,
    )

    assert read.returncode == 2
    assert message in read.stderr
    assert "Traceback" not in read.stderr


@pytest.mark.parametrize(
    "payload,message",
    (({}, "0 project records"), ({"items": [{}]}, "without an object payload")),
)
def test_one_item_rejects_incomplete_store_records(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(OSError, match=message):
        plan_store.one_item(payload, "project")


def test_project_plan_reconstructs_metadata_repositories_and_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {
        "project": {
            "items": [
                {
                    "item": {
                        "title": "probe",
                        "metadata": {"onepipeline.schema_version": 3},
                    }
                }
            ]
        },
        "list": {
            "items": [
                {
                    "id": "source:probe/first",
                    "item": {
                        "title": "First",
                        "content": "Do first.",
                        "metadata": {"onepipeline.id": "first"},
                        "repositories": ["github.com/acme/service"],
                    },
                },
                {
                    "id": "source:probe/second",
                    "item": {
                        "title": "Second",
                        "content": "Do second.",
                        "metadata": {"onepipeline.id": "second"},
                        "repositories": [],
                        "delivers": ["followups:I_4"],
                    },
                },
            ]
        },
        "first": {"items": []},
        "second": {
            "items": [{"to": {"id": "source:probe/first"}}],
        },
    }

    def store(arguments: list[str]) -> dict[str, object]:
        if arguments[:2] == ["project", "show"]:
            return answers["project"]
        if arguments[:2] == ["task", "list"]:
            return answers["list"]
        return answers["second" if arguments[-1].endswith("second") else "first"]

    monkeypatch.setattr(plan_store, "store_json", store)
    plan = plan_store.read_project("source:probe")[0]

    assert plan["schema_version"] == 3
    assert plan["tasks"][0]["repo"] == "github.com/acme/service"
    assert plan["tasks"][1]["deps"] == ["first"]
    # The record's own `delivers` round-trips as the node field the engine reads, and a
    # task delivering nothing carries none — so an older plan reads back exactly as before.
    assert plan["tasks"][1]["delivers"] == ["followups:I_4"]
    assert "delivers" not in plan["tasks"][0]


def test_project_plan_reads_every_task_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A store cursor is sent back to the public task-list boundary unchanged."""
    pages: list[str | None] = []

    def store(arguments: list[str]) -> dict[str, object]:
        if arguments[:2] == ["project", "show"]:
            return {"items": [{"item": {"title": "p"}}]}
        if arguments[:2] == ["task", "list"]:
            page = arguments[arguments.index("--page") + 1] if "--page" in arguments else None
            pages.append(page)
            if page is None:
                return {"items": [], "next": "second-page"}
            return {"items": [], "next": None}
        raise AssertionError(f"an empty project has no dependency query: {arguments}")

    monkeypatch.setattr(plan_store, "store_json", store)

    assert plan_store.read_project("s:p")[0]["tasks"] == []
    assert pages == [None, "second-page"]


@pytest.mark.parametrize(
    ("next_pages", "message"),
    (([7], "invalid next-page token"), (["again", "again"], "repeated next-page token")),
)
def test_project_plan_rejects_invalid_task_page_cursors(
    monkeypatch: pytest.MonkeyPatch, next_pages: list[object], message: str
) -> None:
    calls = 0

    def store(arguments: list[str]) -> dict[str, object]:
        nonlocal calls
        if arguments[:2] == ["project", "show"]:
            return {"items": [{"item": {"title": "p"}}]}
        answer = {"items": [], "next": next_pages[min(calls, len(next_pages) - 1)]}
        calls += 1
        return answer

    monkeypatch.setattr(plan_store, "store_json", store)
    with pytest.raises(OSError, match=message):
        plan_store.read_project("s:p")


@pytest.mark.parametrize("project", ("unqualified", ":native", "source:"))
def test_project_plan_requires_a_qualified_id(project: str) -> None:
    with pytest.raises(OSError, match="qualified"):
        plan_store.read_project(project)


@pytest.mark.parametrize(
    "project_item,listing,deps,message",
    (
        ({"title": 7}, [], [], "string title"),
        ({"title": "p", "metadata": []}, [], [], "metadata"),
        ({"title": "p"}, None, [], "task listing"),
        ({"title": "p"}, [None], [], "qualified id"),
        ({"title": "p"}, [{"id": "s:p/a"}], [], "object payload"),
        (
            {"title": "p"},
            [{"id": "s:p/a", "item": {"metadata": {}, "title": "a", "content": "a"}}],
            [],
            "onepipeline.id",
        ),
        (
            {"title": "p"},
            [
                {
                    "id": "s:p/a",
                    "item": {
                        "metadata": {"onepipeline.id": "a"},
                        "repositories": "not-a-list",
                    },
                }
            ],
            [],
            "non-list repositories",
        ),
        (
            {"title": "p"},
            [
                {
                    "id": "s:p/a",
                    "item": {
                        "metadata": {"onepipeline.id": "a"},
                        "repositories": [7],
                    },
                }
            ],
            [],
            "non-string repository",
        ),
        (
            {"title": "p"},
            [
                {
                    "id": "s:p/a",
                    "item": {
                        "metadata": {"onepipeline.id": "a"},
                        "repositories": ["github.com/a/a", "github.com/b/b"],
                    },
                }
            ],
            [],
            "more than one repository",
        ),
        (
            {"title": "p"},
            [
                {
                    "id": "s:p/a",
                    "item": {
                        "title": "a",
                        "content": "body",
                        "metadata": {"onepipeline.id": "a"},
                        "repositories": [],
                        "delivers": ["I_created_0"],
                    },
                }
            ],
            [],
            "unqualified entry in delivers",
        ),
        (
            {"title": "p"},
            [
                {
                    "id": "s:p/a",
                    "item": {
                        "title": 7,
                        "content": "body",
                        "metadata": {"onepipeline.id": "a"},
                        "repositories": [],
                    },
                }
            ],
            [],
            "invalid title or content",
        ),
        (
            {"title": "p"},
            [
                {
                    "id": "s:p/a",
                    "item": {
                        "metadata": {"onepipeline.id": "a"},
                        "title": "a",
                        "content": "a",
                    },
                }
            ],
            None,
            "non-list dependencies",
        ),
        (
            {"title": "p"},
            [
                {
                    "id": "s:p/a",
                    "item": {
                        "title": "a",
                        "content": "a",
                        "metadata": {"onepipeline.id": "a"},
                    },
                }
            ],
            [{"to": 7}],
            "dependency edge",
        ),
    ),
)
def test_project_plan_rejects_malformed_store_answers(
    monkeypatch: pytest.MonkeyPatch,
    project_item: object,
    listing: object,
    deps: object,
    message: str,
) -> None:
    def store(arguments: list[str]) -> dict[str, object]:
        if arguments[:2] == ["project", "show"]:
            return {"items": [{"item": project_item}]}
        if arguments[:2] == ["task", "list"]:
            return {"items": listing}
        return {"items": deps}

    monkeypatch.setattr(plan_store, "store_json", store)
    with pytest.raises(OSError, match=message):
        plan_store.read_project("s:p")


@pytest.mark.parametrize("second_id,second_node", (("s:p/a", "b"), ("s:p/b", "a")))
def test_project_plan_rejects_duplicate_store_identities(
    monkeypatch: pytest.MonkeyPatch, second_id: str, second_node: str
) -> None:
    listing = [
        {
            "id": "s:p/a",
            "item": {
                "title": "A",
                "content": "A.",
                "metadata": {"onepipeline.id": "a"},
                "repositories": [],
            },
        },
        {
            "id": second_id,
            "item": {
                "title": "B",
                "content": "B.",
                "metadata": {"onepipeline.id": second_node},
                "repositories": [],
            },
        },
    ]

    def store(arguments: list[str]) -> dict[str, object]:
        if arguments[:2] == ["project", "show"]:
            return {"items": [{"item": {"title": "p"}}]}
        return {"items": listing}

    monkeypatch.setattr(plan_store, "store_json", store)
    with pytest.raises(OSError, match="duplicate task identity"):
        plan_store.read_project("s:p")


def test_project_plan_rejects_a_dependency_on_an_unlisted_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def store(arguments: list[str]) -> dict[str, object]:
        if arguments[:2] == ["project", "show"]:
            return {"items": [{"item": {"title": "p"}}]}
        if arguments[:2] == ["task", "list"]:
            return {
                "items": [
                    {
                        "id": "s:p/a",
                        "item": {
                            "title": "A",
                            "content": "A.",
                            "metadata": {"onepipeline.id": "a"},
                            "repositories": [],
                        },
                    }
                ]
            }
        return {"items": [{"to": {"id": "s:p/missing"}}]}

    monkeypatch.setattr(plan_store, "store_json", store)
    with pytest.raises(OSError, match="unknown dependency targets") as refused:
        plan_store.read_project("s:p")

    # An ordinary unresolvable target is a plan somebody wrote wrong, so it gets the
    # bare refusal. The settlement write-back's own rewrite is the one that also names
    # the engine and the issue, and holding this apart is what says that sentence is
    # about the rewrite rather than about every missing edge.
    assert plan_store.WRITE_BACK_SOURCE not in str(refused.value)


#: A grant in the words the appendix carve-out names, per authorization, as a task's own
#: `## Additional info` writes it above the operational appendix. Composed from the
#: guard's own `grant` rather than spelled here, so the sentence a planner is told to
#: write and the one this check reads stay one sentence.
def _sentence(fragment: str) -> str:
    """``fragment`` capitalized to open a sentence, as a criterion or a grant writes it."""
    return f"{fragment[0].upper()}{fragment[1:]}"


def _granting(*names: str) -> str:
    grants = [one.grant for one in AUTHORIZATIONS if one.name in names]
    sentences = " ".join(
        f"{grant[0].upper()}{grant[1:]}, under the carve-out below." for grant in grants
    )
    return f"## Additional info\n\n{sentences}\n\n{APPENDIX}"


#: The wordings the admission must not reach, under every grant at once: a merge, a
#: landing on a base, the merge path's own verdict, and the node's own publication, each
#: applied to the draft or to the demonstration change request. The word `draft` is not
#: a licence; the subject of an admitted clause is the draft *and* the predicate is the
#: one thing the carve-out lets a worker do.
STILL_REFUSED_UNDER_EVERY_GRANT = (
    "- The draft is merged.",
    "- The demonstration PR lands on main.",
    "- The draft change request's required checks pass.",
    "- The branch publishes as a draft.",
    "- The demonstration pull request is merged.",
    "- The demonstration change request's wheel exists on the registry.",
)


@pytest.mark.parametrize("authorization", AUTHORIZATIONS, ids=lambda one: one.name)
def test_a_criterion_about_what_the_carve_out_lets_the_worker_do_is_admitted_under_its_grant(
    authorization: criteria_guard.Authorization,
) -> None:
    """Each authorization's own example: admitted with the grant, refused without it.

    The same criterion on the same task, differing only in whether the task's own
    `## Additional info` grants the carve-out in the appendix's words. Without the grant
    the worker may not do the thing the criterion rests on, so it is refused exactly as
    every other publication is — and the refusal names the grant to write rather than a
    precondition to state, because that is the correction an author cannot derive.
    """
    criterion = f"- {authorization.example[0].upper()}{authorization.example[1:]}."

    check(
        _task(f"{criterion}\n{COMPLETE}", _granting(authorization.name)), "probe", NOTHING_DEMANDED
    )

    with pytest.raises(CriteriaError) as refused:
        check(_task(f"{criterion}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)
    reported = str(refused.value)
    assert "the dispatch cannot do" in reported, reported
    assert f"A criterion about {authorization.name} is admitted only" in reported, reported
    assert authorization.grant in reported, reported


def test_a_demonstration_criterion_is_refused_on_a_task_granting_only_early_publication() -> None:
    """The two grants are two grants, and the more specific subject decides which is needed.

    A task that lets its worker publish its draft early has not let it open a throwaway
    change request stacked on that draft, so a criterion whose subject is the
    demonstration change request is refused under early publication alone — including
    one that also says `draft`, since "the demonstration draft" is about the demonstration.
    """
    demonstration, early = AUTHORIZATIONS
    assert demonstration.name == "a demonstration change request"
    for criterion in (
        f"- {demonstration.example[0].upper()}{demonstration.example[1:]}.",
        "- The demonstration draft is published.",
    ):
        with pytest.raises(CriteriaError) as refused:
            check(
                _task(f"{criterion}\n{COMPLETE}", _granting(early.name)), "probe", NOTHING_DEMANDED
            )
        assert f"A criterion about {demonstration.name} is admitted only" in str(refused.value), (
            str(refused.value)
        )


@pytest.mark.parametrize(
    "criterion", STILL_REFUSED_UNDER_EVERY_GRANT, ids=range(len(STILL_REFUSED_UNDER_EVERY_GRANT))
)
def test_the_wordings_the_carve_out_does_not_reach_stay_refused_under_every_grant(
    criterion: str,
) -> None:
    """What the admission is bounded by: the subject alone admits nothing.

    Every one of these names the draft or the demonstration change request and rests on
    something the worker still cannot reach — a merge, a landing, a required check, the
    branch's own publication, a release — and each is refused with both grants written,
    by the existing refusals rather than by a new one.
    """
    every = _granting(*(one.name for one in AUTHORIZATIONS))
    with pytest.raises(CriteriaError) as refused:
        check(_task(f"{criterion}\n{COMPLETE}", every), "probe", NOTHING_DEMANDED)
    reported = str(refused.value)
    assert "the dispatch cannot do" in reported, reported
    assert "is admitted only" not in reported, reported


#: What the engine appends below the appendix, each carrying a grant in the carve-out's
#: own words and each spelling `## Additional info` on a line of its own after it: a
#: carried note quoting a task, rendered under `## Planner context`; the same with the
#: quoted heading opening the context; and a references block. Under a reader that took
#: the *last* opening of the heading as the appendix's, every one of these pulled the
#: grant sentence into the task's own section, and a task granting nothing was accepted
#: on the strength of a sentence nobody reviewed as a grant.
def _appended_after_the_appendix(grant: str) -> tuple[str, ...]:
    return (
        f"## Planner context\n\n{grant}.\n\nQuoted from the task:\n\n## Additional info\n\nx\n",
        f"## Planner context\n\n## Additional info\n\n{grant}.\n",
        f"## Cross-repository references\n\n{grant}.\n\n## Additional info\n\nx\n",
    )


def test_a_grant_is_read_from_the_tasks_own_section_and_nowhere_else() -> None:
    """Where the grant is read from, held in both directions.

    A task's own `## Additional info` is the block the first opening of that heading
    holds, read only when the heading closing that block is the second opening — the
    appendix's, directly below it. So a task opening it once has no section of its own,
    and prose the engine appends after the appendix, such as a manager's carried
    `## Planner context`, is never a grant however it is worded — even when it spells
    the heading itself on a line of its own. A grant a planner context could confer would
    be a criterion nobody reviewed admitting a publication.
    """
    _, early = AUTHORIZATIONS
    criterion = f"- {_sentence(early.example)}."
    assert criteria_guard.own_additional_info(_task(COMPLETE)) == ""
    ungranted = _task(f"{criterion}\n{COMPLETE}")
    appended = f"{ungranted}\n\n## Planner context\n\n{_sentence(early.grant)}.\n"
    own = criteria_guard.own_additional_info(appended)
    assert criteria_guard.authorizations(own) == frozenset(), own

    with pytest.raises(CriteriaError, match="is admitted only"):
        check(appended, "probe", NOTHING_DEMANDED)

    criteria = f"{criterion}\n{COMPLETE}"
    granting_nothing = "## Additional info\n\nNothing is granted here.\n\n"
    for trailing in _appended_after_the_appendix(_sentence(early.grant)):
        for additional in (APPENDIX, f"{granting_nothing}{APPENDIX}"):
            appended = f"{_task(criteria, additional)}\n{trailing}"
            own = criteria_guard.own_additional_info(appended)
            assert criteria_guard.authorizations(own) == frozenset(), (trailing, own)
            with pytest.raises(CriteriaError, match="is admitted only"):
                check(appended, "probe", NOTHING_DEMANDED)

    granted = f"{_task(criteria, _granting(early.name))}\n## Planner context\n"
    assert criteria_guard.authorizations(criteria_guard.own_additional_info(granted)) == {
        early.name
    }
    check(granted, "probe", NOTHING_DEMANDED)


#: A carve-out named in the appendix's own words and then negated. Each is what a planner
#: writes to *withhold* one, and each carries the very phrase the grant matcher reads.
NEGATED_GRANTS = (
    "A throwaway demonstration change request is not authorized.",
    "This task does not authorize a throwaway demonstration change request.",
    "No demonstration change request is authorized.",
    "Never authorize a throwaway demonstration change request.",
    "A throwaway demonstration change request isn't authorized.",
)


@pytest.mark.parametrize("withheld", NEGATED_GRANTS)
def test_a_negated_grant_grants_nothing(withheld: str) -> None:
    """A sentence that names a carve-out to withhold it is not a grant of it.

    The grant is read in the appendix's own words, and every one of these carries them:
    *"demonstration change request … authorized"* is inside *"is not authorized"* as much
    as inside the grant. Read by phrase alone, a task that had just forbidden the
    demonstration admitted a criterion resting on it — so the grant is read by the
    sentence, and a negated one is refused exactly as a task saying nothing is, naming
    the grant to write. Held at every reader: the parser, a task, and an amendment.
    """
    demonstration, _ = AUTHORIZATIONS
    assert criteria_guard.authorizations(withheld) == frozenset(), withheld
    criterion = f"- {_sentence(demonstration.example)}."
    additional = f"## Additional info\n\n{withheld}\n\n{APPENDIX}"
    with pytest.raises(CriteriaError, match="is admitted only"):
        check(_task(f"{criterion}\n{COMPLETE}", additional), "probe", NOTHING_DEMANDED)
    with pytest.raises(CriteriaError, match="is admitted only"):
        check_amendment(f"{withheld} {demonstration.example}.", "the amendment for node 'work'")


@pytest.mark.parametrize("authorization", AUTHORIZATIONS, ids=lambda one: one.name)
def test_the_words_that_grant_a_carve_out_are_not_themselves_a_publication_criterion(
    authorization: Authorization,
) -> None:
    """A grant names a publication — "may be published early" — and is not one.

    That sentence reaches the criteria reader only in an amendment, which carries its
    grant in its own text; a task's grant lives outside the criteria block. So the
    publication matcher passes over a match inside a grant's own span, and an amendment
    carrying the grant beside a criterion about the draft is admitted whole. Held from
    both sides: the same criterion with no grant beside it is refused as ungranted, which
    is what says the grant — and not a matcher that never fired — is what admits it.
    """
    where = "the amendment for node 'work'"
    with pytest.raises(CriteriaError) as refused:
        check_amendment(f"{authorization.example}.", where)
    assert f"A criterion about {authorization.name} is admitted only" in str(refused.value), str(
        refused.value
    )
    assert check_amendment(f"{authorization.grant}. {authorization.example}.", where) is None


def test_the_criteria_fingerprint_reads_the_files_the_deterministic_bar_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deterministic half of a live edit's key is a digest of this bar's two sources.

    A record of a pass is a claim about content under a bar, and this is the fingerprint
    that says which bar: `criteria_guard.py` itself, because its patterns are the bar,
    and the base config, because the shared review contract composes into every resolved
    bar. Three things are read off it rather than assumed. It names exactly those two
    files; it is the digest of their bytes, recomputed here independently; and editing
    either by one byte moves it — which is what invalidates every live-edit record made
    under the previous bar, the property `AGENTS.md` promises of it. The judged half has
    the same proof in `tests/test_plan_review.py`; a fingerprint only ever patched to a
    constant would leave stale passes standing with nothing red.
    """
    sources = (Path(criteria_guard.__file__), REPO_ROOT / "config" / "onejudge.base.yaml")
    assert sources == criteria_guard.BAR_SOURCES
    expected = hashlib.sha256()
    for source in criteria_guard.BAR_SOURCES:
        expected.update(source.read_bytes())
        expected.update(b"\0")
    before = criteria_guard.criteria_fingerprint()
    assert before == expected.hexdigest()

    copies = tuple(tmp_path / source.name for source in criteria_guard.BAR_SOURCES)
    for source, copy in zip(criteria_guard.BAR_SOURCES, copies, strict=True):
        copy.write_bytes(source.read_bytes())
    monkeypatch.setattr(criteria_guard, "BAR_SOURCES", copies)
    assert criteria_guard.criteria_fingerprint() == before
    for copy in copies:
        copy.write_bytes(copy.read_bytes() + b"\n# one more byte\n")
        moved = criteria_guard.criteria_fingerprint()
        assert moved != before, f"editing {copy.name} left the deterministic bar's digest standing"
        before = moved
