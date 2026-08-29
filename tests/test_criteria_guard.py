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

`tests/e2e/test_check_plan_recipe_e2e.py` drives the same guard through the real
`just check-plan` over the real tracked appendix; the tests here are the ones that
can state a synthetic bar and a synthetic appendix, which is what makes each
refusal attributable to one cause.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator import criteria_guard, plan_review, plan_store
from orchestrator.criteria_guard import (
    CRITERIA_HEADING,
    Bar,
    CriteriaError,
    block_scalar,
    builtin_persona,
    builtin_persona_names,
    check,
    check_appendix,
    check_changes_allowed,
    check_demands,
    check_plan,
    criteria_block,
    criteria_items,
    dispatched_nodes,
    field,
    main,
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


#: Criteria that answer every demand the synthetic appendix and a bar can make.
COMPLETE = (
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
    """A node that names no role is judged by `config/onejudge.base.yaml` alone."""
    bar = resolve_bar(None)

    assert "onejudge.base.yaml" in bar.source
    assert "Verify the requested task against the acceptance criteria it states" in bar.text


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


def test_a_demand_the_resolved_bar_makes_must_be_stated_as_a_criterion() -> None:
    """The failure this guard was added for, at its own boundary.

    A node whose criteria never mention end-to-end proof is still judged by a bar
    that demands it, so the judge imports the demand and applies its own reading —
    and a branch that was correct and gate-green was failed exactly that way.
    """
    bar = Bar("the built-in role", "Do not accept done until it is proven end to end.")

    with pytest.raises(CriteriaError) as refused:
        check(_task("- The thing is done.\n- A report names the evidence."), "probe", bar)

    assert "the built-in role demands proof end to end" in str(refused.value)
    assert "State it as a criterion" in str(refused.value)


def test_a_demand_made_outside_the_criteria_block_is_refused_the_same_way() -> None:
    """The guard's old blind spot: it read the criteria against nothing but themselves.

    The demand that failed a node was in `## Additional info`, where the operational
    appendix lands — so a check that only ever looked inside `## Acceptance criteria`
    could not see the thing the judge was about to hold the worker to.
    """
    with pytest.raises(CriteriaError) as refused:
        check(
            _task(
                "- The thing is done.\n- A report names the evidence.",
                additional="## Additional info\n\nProve the change end to end before you settle.\n",
            ),
            "probe",
            NOTHING_DEMANDED,
        )

    assert "this task's own `Additional info`" in str(refused.value)
    assert "proof end to end" in str(refused.value)


def test_a_demand_made_in_prose_under_no_heading_is_still_located() -> None:
    """The message says where the demand was made, including when nothing titles it."""
    with pytest.raises(CriteriaError, match="its opening prose"):
        check_demands(
            "Prove it end to end.", "- The thing is done.\n- Report it.", "probe", NOTHING_DEMANDED
        )


def test_a_bar_that_demands_nothing_leaves_the_criteria_alone(appendix: Path) -> None:
    """The guard adds no demand of its own; it only refuses one that goes unanswered.

    Read through `check_plan`, whose count is the observable answer: a check that only
    declined to raise would pass just as well if it had stopped looking.
    """
    quiet = _task("- The thing is done.", additional="## Additional info\n\nNothing is asked.\n")
    appendix.write_text("## Additional info\n\nNothing is asked.\n", encoding="utf-8")

    assert check_plan(_plan(task=quiet)) == 1


def test_criteria_that_answer_every_demand_are_accepted(appendix: Path) -> None:
    """The whole accepting path under the real `engineer` bar, counted rather than assumed."""
    assert check_plan(_plan(persona="engineer", task=_task(COMPLETE))) == 1


def test_a_task_rebuilt_from_a_stale_appendix_is_refused(appendix: Path) -> None:
    """A builder cloned before an appendix fix reintroduces the wording it removed."""
    appendix.write_text(APPENDIX + "\nA rule that was added since.\n", encoding="utf-8")

    with pytest.raises(CriteriaError, match="current operational appendix"):
        check_appendix(_task(COMPLETE), "probe")


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


def test_the_command_accepts_a_plan_that_states_its_bar(
    appendix: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        plan_store, "read_project", lambda _: (_plan(persona="engineer", task=_task(COMPLETE)), [])
    )

    assert main(["authoring:complete"]) == 0
    assert "1 dispatched node(s)" in capsys.readouterr().out


def test_the_command_refuses_a_plan_that_does_not(
    appendix: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        plan_store,
        "read_project",
        lambda _: (_plan(persona="engineer", task=_task("- The thing is done.")), []),
    )

    assert main(["authoring:incomplete"]) == 1
    assert "check-plan:" in capsys.readouterr().err


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

    assert main(["authoring:complete"]) == 2
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
    assert main(["authoring:absent"]) == 2
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
    appendix: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Content nothing has reviewed is what reaches a dispatch when nobody is watching.

    Both plans this gate exists to catch were single-node plans an operator wrote and
    launched with no planner, so `personas/planner.yaml`'s judge — the thing that exists
    to catch exactly this — never saw their criteria.
    """
    monkeypatch.setattr(
        plan_store,
        "read_project",
        lambda _: (
            _plan(persona="engineer", task=_task(COMPLETE)),
            [_reviewable()],
        ),
    )

    assert main(["authoring:probe"]) == 1
    reported = capsys.readouterr().err
    assert "no review record" in reported, reported
    assert "probe" in reported, reported
    assert "just review-plan authoring:probe" in reported, reported


def test_the_command_accepts_a_recorded_pass_without_spending_a_judged_turn(
    appendix: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A recorded pass is authoritative: a second opinion is how one tree gets two verdicts."""
    task = _reviewable()
    key = plan_review.review_key(task, plan_review.bar_fingerprint())
    monkeypatch.setattr(
        plan_store,
        "read_project",
        lambda _: (
            _plan(persona="engineer", task=_task(COMPLETE)),
            [_reviewable(**{plan_review.RECORD_KEY: {"key": key}})],
        ),
    )
    monkeypatch.setattr(
        plan_review, "_verdict", lambda _: pytest.fail("a recorded pass spent a judged turn")
    )

    assert main(["authoring:probe"]) == 0
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

    assert main(["authoring:probe"]) == 2
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
    ],
    ids=["three-part", "prefixed", "operator", "prerelease"],
)
def test_criteria_may_not_carry_a_version_literal(criterion: str) -> None:
    """The number is well-formed and it perishes; the property it stands in for does not.

    This node shipped: a criterion required a lockfile to resolve a sibling to an exact
    version, the sibling published a newer one between the task being written and the
    node being dispatched, the worker resolved the newest as that repository's own
    manifest demands, and the judge failed finished, gate-green work for doing the right
    thing. A judge reading the number would most likely have passed it.
    """
    with pytest.raises(CriteriaError, match="version literal"):
        check(_task(f"{criterion}\n{COMPLETE}"), "probe", NOTHING_DEMANDED)


@pytest.mark.parametrize(
    "criterion",
    [
        "- The lockfile resolves the sibling to the newest release its requirement admits.",
        "- Line coverage stays at 100%.",
        "- The suite runs across 4 xdist workers.",
        "- The plan declares schema version 3.",
    ],
    ids=["property", "percentage", "count", "schema"],
)
def test_a_criterion_naming_the_property_instead_is_accepted(criterion: str) -> None:
    """The refusal is written to miss rather than to over-refuse: no bare `<n>.<n>`.

    An unprefixed two-component number is a duration, a percentage, or a schema version
    far more often than it is a release, and a false refusal here blocks correct work
    and gets worked around — which is worse than the gap.
    """
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
    }


@pytest.mark.parametrize("mode,message", (("invalid-json", "invalid JSON"), ("edge", "edge")))
def test_check_plan_process_reports_malformed_store_responses(
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
    with pytest.raises(OSError, match="unknown dependency targets"):
        plan_store.read_project("s:p")
