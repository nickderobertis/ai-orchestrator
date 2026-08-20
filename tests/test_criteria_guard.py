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
from pathlib import Path

import pytest

from orchestrator import criteria_guard
from orchestrator.criteria_guard import (
    CRITERIA_HEADING,
    Bar,
    CriteriaError,
    block_scalar,
    builtin_persona,
    check,
    check_appendix,
    check_demands,
    check_plan,
    criteria_block,
    dispatched_nodes,
    field,
    main,
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
    monkeypatch.setattr(criteria_guard.shutil, "which", lambda _: None)
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
    appendix: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(_plan(persona="engineer", task=_task(COMPLETE))), encoding="utf-8")

    assert main([str(plan)]) == 0
    assert "1 dispatched node(s)" in capsys.readouterr().out


def test_the_command_refuses_a_plan_that_does_not(
    appendix: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(_plan(persona="engineer", task=_task("- The thing is done."))), encoding="utf-8"
    )

    assert main([str(plan)]) == 1
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
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(_plan(persona="engineer", task=_task(COMPLETE))), encoding="utf-8")

    assert main([str(plan)]) == 2
    reported = capsys.readouterr().err
    assert "review configuration" in reported, reported
    assert "just bootstrap" in reported, reported


def test_the_command_separates_an_unreadable_plan_from_a_refused_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 rather than 1: nothing was judged, so nothing was refused."""
    missing = tmp_path / "absent.json"

    assert main([str(missing)]) == 2
    assert "cannot read" in capsys.readouterr().err

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")

    assert main([str(malformed)]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_the_tracked_appendix_is_where_the_guard_reads_it_from() -> None:
    """The promotion this module is half of: the appendix is tracked, not scratch."""
    assert (REPO_ROOT / criteria_guard.APPENDIX).is_file()
