"""A review record covers exactly the authored content, and only a pass writes one.

Two plans shipped here whose criteria nothing had reviewed, and both produced finished,
gate-green work that a judge then rejected for satisfying the repository instead of the
criterion. What this module holds is the rule that catches both: a task carries a
digest of its own authored content, and a task whose content does not hash to its
record has not been reviewed — which covers the plan an operator wrote by hand and the
planner's plan an operator then tweaked, without anything having to detect who typed
either one.

Four properties are checked here because each of them is what makes the gate worth
having rather than an inconvenience: the key covers the authored fields and nothing a
settlement write-back owns, so a record survives its node being dispatched; it covers
the bar too, so a record does not outlive the bar it was granted under; a refusal
records nothing, so no failed review can be replayed as a pass; and there is no
argument, option, or environment variable that writes a record without a pass.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict, get_type_hints

import pytest

from orchestrator import criteria_guard, plan_review, plan_store
from orchestrator.plan_store import StoreTask
from orchestrator.root import REPO_ROOT

BAR = "bar-fingerprint"

#: The two verdicts a review turn can answer with, in the shape the response schema
#: declares and `orchestrator/plan_review.py` reads. The refusal carries two findings
#: rather than one, because reporting only the first is the defect this contract was
#: widened to end: a fixture carrying one would pass either way.
PASSES = plan_review.Verdict(passes=True, findings=[])
REFUSES = plan_review.Verdict(
    passes=False,
    findings=[
        plan_review.Finding(
            criterion="the lockfile resolves the sibling to 1.2.3",
            why="it names a release number rather than the property that number stands for",
        ),
        plan_review.Finding(
            criterion="the branch publishes",
            why="publication happens after the worker settles, so no dispatch can reach it",
        ),
    ],
)


def _task(
    *,
    qualified_id: str = "demo:plan/route",
    node_id: str = "route",
    title: str = "feat: add the route",
    content: str | None = "## What\n\nAdd the route.\n",
    metadata: Mapping[str, object] | None = None,
    repositories: list[object] | None = None,
    deps: tuple[str, ...] = (),
) -> StoreTask:
    """One store task, in the fields a review key is computed from."""
    return StoreTask(
        qualified_id=qualified_id,
        node_id=node_id,
        title=title,
        content=content,
        metadata=(
            {"onepipeline.id": "route", "onepipeline.persona": "engineer"}
            if metadata is None
            else metadata
        ),
        repositories=[] if repositories is None else repositories,
        deps=deps,
    )


def _recorded(task: StoreTask, key: str) -> StoreTask:
    return _task(
        qualified_id=task.qualified_id,
        node_id=task.node_id,
        title=task.title,
        content=task.content,
        metadata={**task.metadata, plan_review.RECORD_KEY: {"key": key, "by": "review-plan"}},
        repositories=list(task.repositories),
        deps=task.deps,
    )


def test_the_key_changes_with_every_authored_field() -> None:
    """The fields the record covers: title, prose, kind, persona, deps, whether the node
    expects no diff, and a step's own three."""
    base = plan_review.review_key(_task(), BAR)
    moved = {
        "title": _task(title="feat: add another route"),
        "content": _task(content="## What\n\nAdd a different route.\n"),
        "kind": _task(metadata={"onepipeline.id": "route", "onepipeline.kind": "human"}),
        "persona": _task(metadata={"onepipeline.id": "route", "onepipeline.persona": "reviewer"}),
        "deps": _task(deps=("design",)),
        "expects_no_diff": _task(
            metadata={
                "onepipeline.id": "route",
                "onepipeline.persona": "engineer",
                "onepipeline.expects_no_diff": True,
            }
        ),
    }
    for field, task in moved.items():
        assert plan_review.review_key(task, BAR) != base, field


#: One change to what a criterion demands, and four that alter no demand at all. The pair
#: is what the key is held to: a key surviving a changed demand is worse than the cost it
#: saves, and one that charges a judged turn for re-indenting prose costs something for
#: nothing. The re-wrap is deliberately on the *invalidating* side — lines are never
#: joined, because a bullet is how one criterion is separated from the next.
CRITERIA = "## Acceptance criteria\n\n- The route rejects an invalid request.\n- It is logged.\n"
MEANS_SOMETHING_ELSE = (
    CRITERIA.replace("rejects", "accepts"),
    CRITERIA.replace("- It is logged.\n", ""),
    CRITERIA.replace("invalid request.\n", "invalid\n  request.\n"),
)
MEANS_THE_SAME = (
    CRITERIA.replace("- The route", "   - The route"),
    CRITERIA.replace("an invalid", "an  invalid"),
    CRITERIA.replace("criteria\n\n", "criteria\n\n\n\n"),
    CRITERIA.replace("logged.\n", "logged.   \n"),
)


@pytest.mark.parametrize("content", MEANS_SOMETHING_ELSE, ids=range(len(MEANS_SOMETHING_ELSE)))
def test_a_change_to_what_a_criterion_demands_invalidates_the_record(content: str) -> None:
    """The half the normalization may never cost, stated first because it is the binding one.

    A word changed, a criterion dropped, and a paragraph re-wrapped are all outside what
    a reviewer could have ruled on without reading again. The re-wrap is here rather than
    below it on purpose: collapsing newlines as well as spaces would let one criterion and
    two hash alike, so lines are never joined and a re-wrap is read as a change.
    """
    assert plan_review.review_key(_task(content=content), BAR) != plan_review.review_key(
        _task(content=CRITERIA), BAR
    )


@pytest.mark.parametrize("content", MEANS_THE_SAME, ids=range(len(MEANS_THE_SAME)))
def test_a_change_that_alters_no_demand_leaves_the_record_standing(content: str) -> None:
    """Re-indenting, re-spacing, a blank-line run, trailing whitespace: no demand moved.

    The key is over what a task demands rather than over its bytes, and a judged turn
    charged for one of these is this gate costing something for nothing — which the two
    tiers that used to refuse each other's wording made routine.
    """
    assert plan_review.review_key(_task(content=content), BAR) == plan_review.review_key(
        _task(content=CRITERIA), BAR
    )


#: The same pair one level in, where the collapse had no business reaching. Whitespace is
#: cosmetic in prose and load-bearing in a literal, so each of these is a demand a
#: reviewer would have had to read again — and each hashed to the unedited key.
LITERAL = (
    "## Acceptance criteria\n\n"
    "- The parser accepts `a b` as one field.\n"
    "- The sample it is given reads:\n\n"
    "```yaml\n"
    "route:\n"
    "  method: GET\n"
    "```\n"
)
MEANS_SOMETHING_ELSE_IN_CODE = (
    LITERAL.replace("`a b`", "`a  b`"),
    LITERAL.replace("  method", "    method"),
    LITERAL.replace("accepts `a b`", "accepts`a b`"),
)


@pytest.mark.parametrize(
    "content", MEANS_SOMETHING_ELSE_IN_CODE, ids=range(len(MEANS_SOMETHING_ELSE_IN_CODE))
)
def test_whitespace_a_literal_depends_on_invalidates_the_record(content: str) -> None:
    """The collapse stops at code, because a literal is where whitespace is the demand.

    `a b` and `a  b` are two different fields for the parser these criteria are about,
    and a YAML sample re-indented is a different sample. Collapsing either hashed a
    changed demand to its old key — a review record standing over content nobody read,
    which is the one failure this gate exists to prevent, arriving through the machinery
    that makes it cheap. The third case is the span's own edge: whether there was a
    space before a literal is part of it too, so the boundary is collapsed and never
    closed up.
    """
    assert plan_review.review_key(_task(content=content), BAR) != plan_review.review_key(
        _task(content=LITERAL), BAR
    )


def test_a_fence_closes_only_on_one_at_least_as_long_as_itself() -> None:
    """A task documenting a fenced block nests one, and the inner one must not end it.

    The nodes of this repository write exactly that — the appendix and the task template
    are markdown, so a criterion about them shows a fence inside a fence. Closing on the
    inner one would end the block early and hand every line after it back to the
    collapse, which is the same lost demand one level in.
    """
    nested = (
        "## Acceptance criteria\n\n"
        "- The task it is given reads:\n\n"
        "````markdown\n"
        "```yaml\n"
        "route:\n"
        "  method: GET\n"
        "```\n"
        "````\n"
    )

    assert plan_review.review_key(
        _task(content=nested.replace("  method", "    method")), BAR
    ) != plan_review.review_key(_task(content=nested), BAR)


def test_prose_around_a_literal_still_costs_no_judged_turn() -> None:
    """The other half of the pair: stopping at code buys back none of the cosmetic edits.

    Asserted beside the test above because a boundary that absorbed the prose too would
    pass it while charging for every re-indent, which is the cost this normalization
    exists to remove.
    """
    respaced = LITERAL.replace("- The parser  accepts", "- The parser accepts").replace(
        "- The parser accepts", "   - The parser   accepts"
    )

    assert plan_review.review_key(_task(content=respaced), BAR) == plan_review.review_key(
        _task(content=LITERAL), BAR
    )


def test_a_steps_own_prose_is_read_for_its_meaning_too() -> None:
    """A lifecycle node's criteria *are* its steps' prose, so the normalization reaches them.

    Asserted in both directions for the reason the pair above is: a step read byte-wise
    charges for a reflow, and a step not read at all leaves a record standing over
    criteria somebody rewrote.
    """
    reflowed = [{**STEPS[0], "task": "Add   the route.\n"}, STEPS[1]]
    reworded = [{**STEPS[0], "task": "Add the other route.\n"}, STEPS[1]]
    base = plan_review.review_key(_stepped(STEPS), BAR)

    assert plan_review.review_key(_stepped(reflowed), BAR) == base
    assert plan_review.review_key(_stepped(reworded), BAR) != base


#: One lifecycle node's steps, and the same steps with one authored field of one step
#: moved. A lifecycle node states its prose and its persona per step rather than in
#: `task` and `persona`, so for that node these are the whole of its authored content.
STEPS = [
    {"id": "implement", "persona": "engineer", "task": "Add the route.\n"},
    {"id": "document", "persona": "docs-writer", "task": "Write it up.\n"},
]


def _stepped(steps: object) -> StoreTask:
    return _task(content=None, metadata={"onepipeline.id": "route", "onepipeline.steps": steps})


def test_the_key_changes_with_every_authored_field_of_a_lifecycle_step() -> None:
    """A stepped node's criteria and personas are keyed, one step at a time.

    The whole of a lifecycle node's authored content can live in its `steps`, so a key
    that stopped at `task` and `persona` left a standing record over criteria nobody
    read — the hole this closes. Each of the three authored fields of each step is
    moved on its own, because a key covering the block but not the field would pass a
    test that moved the whole list.
    """
    base = plan_review.review_key(_stepped(STEPS), BAR)
    moved = {
        "the first step's prose": [{**STEPS[0], "task": "Add a different route.\n"}, STEPS[1]],
        "the second step's prose": [STEPS[0], {**STEPS[1], "task": "Write up something else."}],
        "the first step's persona": [{**STEPS[0], "persona": "researcher"}, STEPS[1]],
        "the second step's persona": [STEPS[0], {**STEPS[1], "persona": "reviewer"}],
        "a step's id": [{**STEPS[0], "id": "build"}, STEPS[1]],
        "the order they run in": [STEPS[1], STEPS[0]],
        "a step dropped": [STEPS[0]],
    }
    for field, steps in moved.items():
        assert plan_review.review_key(_stepped(steps), BAR) != base, field


def test_a_step_field_no_author_wrote_leaves_the_record_standing() -> None:
    """The narrowing is what stops the engine's own bookkeeping invalidating a review.

    A step is keyed on the three fields its author writes, so a field added to a step by
    something other than its author is outside the key for the same reason `status` is.
    """
    base = plan_review.review_key(_stepped(STEPS), BAR)
    annotated = [{**STEPS[0], "branch": "onevcs/s-0000"}, STEPS[1]]
    assert plan_review.review_key(_stepped(annotated), BAR) == base


@pytest.mark.parametrize("steps", ("not a list", {"id": "one"}, ["not a step"], 7), ids=str)
def test_steps_this_cannot_narrow_are_answered_as_no_steps(steps: object) -> None:
    """`just review-plan` reads a plan `just check-plan` may not have passed.

    So this meets whatever the store holds. Answering an unreadable `steps` as no steps
    costs nothing already lost: `check_plan` refuses `steps` that are not a list of
    mappings by name, and it runs before any record is consulted, so a plan this cannot
    narrow is one no launch reaches whatever its record says.
    """
    assert plan_review.authored_steps(_stepped(steps)) is None
    assert plan_review.review_key(_stepped(steps), BAR) == plan_review.review_key(
        _stepped(None), BAR
    )


def test_the_key_covers_the_authored_content_and_nothing_else_a_plan_carries() -> None:
    """Everything outside the authored content leaves a standing record standing.

    The one worth naming because a reader will meet it is a task **retargeted at another
    repository**, which keeps its record. `max_turns` is the shape of everything else: a
    dispatch control its author sets and no reviewer rules on.
    """
    base = plan_review.review_key(_task(), BAR)
    unkeyed = {
        "repositories": _task(repositories=["github.com/nickderobertis/elsewhere"]),
        "max_turns": _task(
            metadata={
                "onepipeline.id": "route",
                "onepipeline.persona": "engineer",
                "onepipeline.max_turns": 60,
            }
        ),
    }
    for field, task in unkeyed.items():
        assert plan_review.review_key(task, BAR) == base, field


def test_a_record_stands_across_a_change_to_a_field_the_key_does_not_cover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driven through the reader the check uses, not only through the hash.

    A key that ignored a field while `unreviewed` still refused over it would be the
    same defect wearing the opposite sign, so the pair is asserted rather than the hash
    alone.
    """
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    task = _task()
    key = plan_review.review_key(task, BAR)
    retargeted = _recorded(
        _task(repositories=["github.com/nickderobertis/elsewhere"]),
        key,
    )
    assert plan_review.unreviewed([retargeted]) == []

    edited = _recorded(_task(content="## What\n\nSomething else entirely.\n"), key)
    assert plan_review.unreviewed([edited]) == [edited]


def test_a_stepped_record_is_read_the_same_way_the_check_reads_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stepped halves of the same pair, driven through `unreviewed` rather than the
    hash: a step field no author wrote leaves the record standing, and a step's own
    prose or persona moving is what the check refuses over.
    """
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    key = plan_review.review_key(_stepped(STEPS), BAR)
    annotated = _recorded(_stepped([{**STEPS[0], "branch": "onevcs/s-0000"}, STEPS[1]]), key)
    assert plan_review.unreviewed([annotated]) == []

    for steps in (
        [{**STEPS[0], "task": "Add a different route.\n"}, STEPS[1]],
        [{**STEPS[0], "persona": "researcher"}, STEPS[1]],
    ):
        moved = _recorded(_stepped(steps), key)
        assert plan_review.unreviewed([moved]) == [moved]


def test_the_key_survives_the_fields_a_settlement_write_back_owns() -> None:
    """A node being dispatched must not invalidate the review of its own content.

    The engine projects each settlement back onto the plan it was launched from, so a
    key over the whole record would go stale the first time a node ran — and the gate
    would then refuse every plan that had ever been launched.
    """
    settled = _task(
        metadata={
            "onepipeline.id": "route",
            "onepipeline.persona": "engineer",
            "onepipeline.state": "done",
            "onepipeline.branch": "onevcs/s-abc",
        }
    )
    assert plan_review.review_key(settled, BAR) == plan_review.review_key(_task(), BAR)


def test_the_human_kind_is_the_one_the_criteria_guard_reads() -> None:
    """One vocabulary, restated on the side that cannot import the other.

    `orchestrator.criteria_guard` reads this repository's plans and imports this module,
    so this one cannot import it back — and the two would then be free to disagree about
    which value names the shape that dispatches nobody. They must not: this module tells
    the reviewer a human node carries an action rather than criteria, and that module is
    what skips it when the criteria are checked, so a plan whose node one of them called
    human and the other did not would be reviewed under one bar and checked under
    another. The metadata key is the same name under the `onepipeline.` prefix
    `orchestrator.plan_store` strips before the guard sees a node.
    """
    assert plan_review.HUMAN == criteria_guard.HUMAN
    assert plan_review.KIND == "onepipeline.kind"


def test_the_reviewer_is_asked_the_three_questions_that_moved_here() -> None:
    """What this tier now owns, asked of the prompt that is the only place it is asked.

    Each of the three needs judgment, and each was a deterministic proxy until the two
    tiers were found refusing each other's required wording: whether a number is the
    right number, whether the criteria answer a demand their own bar makes — judged by
    meaning, never by phrase — and whether a node whose criteria describe work that
    changes no file declares `expects_no_diff`. `bar_fingerprint` covers the prompt, so
    this is about what the wording *says* rather than about it having moved.
    """
    # Flattened, because the prompt is hard-wrapped prose: a phrase longer than one of
    # its lines would otherwise be absent from a prompt that says it.
    asked = " ".join(plan_review.REVIEW_PROMPT.split())
    field = plan_review.EXPECTS_NO_DIFF.removeprefix("onepipeline.")

    for question in (
        "This turn is the only thing that asks about a version literal",
        "No deterministic check refuses one any more",
        "refuse criteria that leave a demand their own task or their own bar makes unanswered",
        "by **meaning rather than by wording**",
        "criteria describe work that changes no file in the repository",
        f"unless the node declares `{field}`",
    ):
        assert question in asked, question


def test_the_reviewer_is_told_what_to_hold_a_human_node_to() -> None:
    """Showing the field without saying what it means would change no verdict.

    The bar the prompt carries is about acceptance criteria, which a human node states
    none of by construction, so a reviewer shown `kind` and nothing else has no reason
    to ask a different question of it — and refusing that shape for the property it may
    not have is a refusal its author can never correct.
    """
    assert 'A task whose `kind` is "human"' in plan_review.REVIEW_PROMPT
    assert "acceptance criteria" in plan_review.REVIEW_PROMPT


def test_the_key_changes_with_the_bar_in_force() -> None:
    """A record does not outlive the bar it was granted under."""
    assert plan_review.review_key(_task(), BAR) != plan_review.review_key(_task(), "moved")


def test_the_bar_fingerprint_reads_the_files_the_bar_is(tmp_path: Path) -> None:
    """Editing either file, by one byte, invalidates every record made under it."""
    for relative in plan_review.BAR_FILES:
        copy = tmp_path / relative.name
        copy.parent.mkdir(parents=True, exist_ok=True)
    original = tmp_path / "original"
    moved = tmp_path / "moved"
    for root in (original, moved):
        for relative in plan_review.BAR_FILES:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((REPO_ROOT / relative).read_bytes())
    assert plan_review.bar_fingerprint(original) == plan_review.bar_fingerprint(REPO_ROOT)
    edited = moved / plan_review.BAR_FILES[0]
    edited.write_bytes(edited.read_bytes() + b"\n# one more byte\n")
    assert plan_review.bar_fingerprint(moved) != plan_review.bar_fingerprint(original)


#: How each JSON Schema type the verdict declares is spelled in Python. A type appearing
#: in the schema and not here fails the gate below on the lookup rather than being
#: guessed at, which is what made the array this contract grew a failing check rather
#: than a silently unreconciled field.
SCHEMA_TYPES = {
    "boolean": bool,
    "string": str,
    "array": list[plan_review.Finding],
}


def _verdict_schema() -> dict[str, Any]:
    """The response schema, decoded. `Any` because JSON Schema is recursive and this
    reads a different depth of it per assertion — a narrower type here would be a second,
    weaker restatement of the very file these tests exist to reconcile against."""
    return json.loads((REPO_ROOT / plan_review.BAR_FILES[1]).read_text(encoding="utf-8"))


def test_the_verdict_type_matches_the_schema_it_is_validated_against() -> None:
    """`Verdict` and the response schema are one contract, so they are reconciled here.

    oneharness validates a review turn against `config/plan-review-verdict.schema.json`
    and this package reads what comes back through `plan_review.Verdict`. The two
    declare the same fields in different files and nothing compared them, so a field
    renamed or retyped on one side was caught only by whichever journey happened to trip
    over it — which is the drift this repository gates everywhere else it restates
    somebody's contract.

    Both directions are held, because they fail differently and both fail quietly: a
    field the schema requires and `Verdict` omits is read as absent, and one `Verdict`
    declares while the schema forbids it never arrives at all. `Finding` is reconciled
    against the array's own item schema for the same reason and in the same two
    directions — a finding is where everything an operator is shown now lives.
    """
    schema = _verdict_schema()
    declared = get_type_hints(plan_review.Verdict)

    assert schema["additionalProperties"] is False, schema
    assert set(schema["required"]) == set(schema["properties"]) == set(declared), schema
    for name, described in schema["properties"].items():
        assert SCHEMA_TYPES[described["type"]] == declared[name], name

    item = schema["properties"]["findings"]["items"]
    finding = get_type_hints(plan_review.Finding)
    assert item["additionalProperties"] is False, item
    assert set(item["required"]) == set(item["properties"]) == set(finding), item
    for name, described in item["properties"].items():
        assert SCHEMA_TYPES[described["type"]] == finding[name], name


def test_the_schema_admits_a_verdict_only_where_its_outcome_and_findings_agree() -> None:
    """A finding is a refused criterion, so the two halves are one statement.

    Read off the schema rather than driven, because oneharness is what enforces it and
    `tests/plan_tooling/test_plan_review_e2e.py` is where a real turn meets that
    validator. What this holds is that the file still *says* it: a refusal admitting no
    finding would let a reviewer stop this content with nothing naming what to correct,
    and a pass admitting one would clear a task whose own reviewer refused criteria of
    it — which is the failure that ends `_answered`'s narrowing too.
    """
    conditions = {
        found["if"]["properties"]["passes"]["const"]: found["then"]["properties"]["findings"]
        for found in _verdict_schema()["allOf"]
    }

    assert conditions[True] == {"maxItems": 0}, conditions
    assert conditions[False] == {"minItems": 1}, conditions

    # And a finding that names nothing is refused for the same reason a refusal naming
    # no finding is: it leaves its reader exactly where the other one does. Both
    # keywords are held, at the strength `_answered` reads them back at: `minLength`
    # alone admits a run of spaces, which names nothing while satisfying a length, and
    # a schema that admitted one would send a reviewer an answer oneharness validates
    # and this package then discards with nothing said about why.
    item = _verdict_schema()["properties"]["findings"]["items"]["properties"]
    assert {name: field["minLength"] for name, field in item.items()} == {
        "criterion": 1,
        "why": 1,
    }, item
    assert {name: field["pattern"] for name, field in item.items()} == {
        "criterion": r"\S",
        "why": r"\S",
    }, item


@pytest.mark.parametrize("blank", ["", " ", "\t", "   \n  "], ids=range(4))
@pytest.mark.parametrize("field", ["criterion", "why"], ids=["criterion", "why"])
def test_the_schema_and_the_fallback_reader_refuse_the_same_empty_finding(
    blank: str, field: str
) -> None:
    """One rule, restated in two files, so the restatement is what is gated.

    `config/plan-review-verdict.schema.json` is the authority oneharness validates
    against and `_answered` reads the same answer back, so a string one accepts and the
    other discards is a divergence that fails silently in the worst direction: the turn
    is validated, the verdict is thrown away, and the task stays unreviewed with the
    reviewer told nothing. Every string either refuses is required to be refused by
    both, checked against the schema's own keywords rather than against a copy of them.
    """
    item = _verdict_schema()["properties"]["findings"]["items"]["properties"][field]
    # `get` rather than indexing, so a keyword dropped from the schema fails on the
    # divergence it causes — an empty pattern admits everything — rather than on a
    # missing key, which reads like a broken test instead of a broken contract.
    admitted = len(blank) >= item["minLength"] and (
        re.search(item.get("pattern", ""), blank) is not None
    )

    finding = {"criterion": "the route works", "why": "it is vague"} | {field: blank}
    read = plan_review._answered({"passes": False, "findings": [finding]})

    assert admitted is False, item
    assert read is None, read


def test_the_bar_fingerprint_covers_the_question_it_asks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rewording `REVIEW_PROMPT` invalidates every record made under the old wording.

    The sibling above covers the half of the bar that is files, which `bar_fingerprint`
    reads from the root it is handed. The prompt is a constant of this module instead,
    so it reaches the digest by a different route and a regression dropping it is
    invisible to that test — the pass would go on standing under a question nobody
    asked. `tests/plan_tooling/test_plan_review_e2e.py` drives the same property through the
    real command surface; this is the tier that answers in milliseconds.
    """
    before = plan_review.bar_fingerprint()
    monkeypatch.setattr(
        plan_review, "REVIEW_PROMPT", f"{plan_review.REVIEW_PROMPT}\nOne sentence on.\n"
    )
    assert plan_review.bar_fingerprint() != before


@pytest.mark.parametrize(
    "record",
    [None, "a string", {"no key": 1}, {"key": 7}],
    ids=["absent", "string", "keyless", "unstring"],
)
def test_a_record_this_cannot_read_is_answered_as_no_record(record: object) -> None:
    """The safe direction: an unreadable record refuses the task rather than a whole plan."""
    metadata = {"onepipeline.id": "route"}
    if record is not None:
        metadata[plan_review.RECORD_KEY] = record
    assert plan_review.recorded(_task(metadata=metadata)) is None


def test_a_recorded_pass_is_authoritative_and_a_moved_one_is_not() -> None:
    task = _task()
    current = _recorded(task, plan_review.review_key(task, BAR))
    assert plan_review.unreviewed([current], BAR) == []
    assert plan_review.unreviewed([_recorded(task, "under an older bar")], BAR) == [
        _recorded(task, "under an older bar")
    ]


def test_the_bar_in_force_is_used_when_none_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    task = _task()
    assert plan_review.unreviewed([_recorded(task, plan_review.review_key(task, BAR))]) == []


class _Setting(TypedDict):
    """One entry of the store CLI's `config show` answer, in the two fields read here."""

    key: str
    value: str


class _Configuration(TypedDict):
    """The store CLI's `config show` answer, narrowed to what `source_root` reads."""

    settings: list[_Setting]


class _Store:
    """A local Markdown source on disk, with the store answers a review reads it through."""

    def __init__(self, root: Path, tasks: Sequence[StoreTask]) -> None:
        self.root = root
        self.tasks = list(tasks)
        (root / "projects").mkdir(parents=True, exist_ok=True)
        (root / "projects" / "plan.md").write_text('---\ntitle: "P"\n---\n', encoding="utf-8")
        for task in self.tasks:
            _, _, native = task.qualified_id.partition(":")
            document = root / "tasks" / native.split("/")[0] / f"{native.split('/')[1]}.md"
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(
                "---\n"
                f"title: {json.dumps(task.title)}\n"
                "metadata:\n"
                + "".join(
                    f"  {json.dumps(key)}: {json.dumps(value)}\n"
                    for key, value in task.metadata.items()
                )
                + "---\n\n"
                + (task.content or "")
                + "\n",
                encoding="utf-8",
            )

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(plan_store, "store_json", self._answer)
        monkeypatch.setattr(plan_store, "read_tasks", self._read)
        monkeypatch.setattr(
            plan_store, "read_plan", lambda project, records: {"name": "P", "tasks": []}
        )

    def _read(self, project: str) -> list[StoreTask]:
        assert project == "demo:plan"
        return list(self.tasks)

    def _answer(self, arguments: Sequence[str]) -> _Configuration:
        assert list(arguments) == ["config", "show"]
        return _Configuration(
            settings=[
                _Setting(key="sources.demo.plugin", value="local-md"),
                _Setting(key="sources.demo.config.root", value=str(self.root)),
            ]
        )

    def written(self, native: str) -> object:
        document = self.root / "tasks" / native.split("/")[0] / f"{native.split('/')[1]}.md"
        for line in document.read_text(encoding="utf-8").splitlines():
            if f'"{plan_review.RECORD_KEY}"' in line:
                return json.loads(line.split(": ", 1)[1])
        return None


def _verdicts(monkeypatch: pytest.MonkeyPatch, *answers: plan_review.Verdict) -> list[str]:
    """Stand the judged turn in at the one boundary a verdict crosses into this module."""
    given = list(answers)
    seen: list[str] = []

    def verdict(prompt: str) -> plan_review.Verdict:
        seen.append(prompt)
        return given.pop(0)

    monkeypatch.setattr(plan_review, "_verdict", verdict)
    return seen


def test_a_passing_review_records_the_key_the_check_will_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    prompts = _verdicts(monkeypatch, PASSES)

    assert plan_review.main(["demo:plan"]) == 0
    written = store.written("plan/route")
    assert isinstance(written, dict)
    assert written["key"] == plan_review.review_key(_task(), BAR)
    assert written["by"] == plan_review.BY_REVIEW
    assert "The review bar" in prompts[0]
    assert "Add the route." in prompts[0]


def test_a_refused_review_records_nothing_and_shows_every_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed review leaves nothing behind, and reports all of what it found.

    Both findings reach stderr on their own lines, and the summary counts criteria and
    tasks separately, because a reader acts on the first number and schedules on the
    second.
    """
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    _verdicts(monkeypatch, REFUSES)

    assert plan_review.main(["demo:plan"]) == 1
    assert store.written("plan/route") is None
    reported = capsys.readouterr().err
    for finding in REFUSES["findings"]:
        assert f"route: {finding['criterion']} — {finding['why']}" in reported, reported
    assert "2 criterion(s) across 1 task(s)" in reported, reported
    assert "nothing was recorded" in reported, reported


@pytest.mark.parametrize(
    "structured",
    [
        {"passes": False, "findings": []},
        {"passes": True, "findings": [{"criterion": "c", "why": "w"}]},
        {"passes": False, "findings": ["not an object"]},
        {"passes": False, "findings": [{"criterion": "", "why": "w"}]},
        {"passes": False, "findings": [{"criterion": "c", "why": "   "}]},
        {"passes": False, "findings": [{"criterion": "c"}]},
        {"passes": False, "findings": [{"criterion": "c", "why": 7}]},
        {"passes": False, "findings": "not a list"},
        {"passes": "no", "findings": []},
        {"findings": []},
        {"passes": True, "findings": [], "reason": "and some commentary besides"},
        {"passes": False, "findings": [{"criterion": "c", "why": "w", "severity": "high"}]},
    ],
    ids=[
        "refuses-nothing",
        "passes-with-a-finding",
        "unobject-finding",
        "nameless-criterion",
        "blank-why",
        "finding-without-why",
        "unstring-why",
        "unlist-findings",
        "unbool-passes",
        "no-outcome",
        "undeclared-verdict-field",
        "undeclared-finding-field",
    ],
)
def test_an_answer_the_schema_would_not_admit_is_not_a_verdict(structured: object) -> None:
    """The failure direction stays "not reviewed", for both halves of the agreement.

    oneharness validates the schema and re-prompts, so this narrowing is the second
    reading of the same rule — the one that holds when the validator was skipped,
    misconfigured, or stood in for. The two that matter are the first pair: a refusal
    naming no criterion stops the content while saying nothing an author can correct,
    and a pass carrying a finding would record a pass over criteria its own reviewer
    refused. The last pair is the other half of the schema, `additionalProperties:
    false` at each of its two levels: an answer carrying more than the declared shape
    was written to a contract this does not have, and the field it carries is one
    nothing here would read.
    """
    assert plan_review._answered(structured) is None


@pytest.mark.parametrize(
    "structured",
    [
        {"passes": True, "findings": []},
        {"passes": False, "findings": [{"criterion": "c", "why": "w"}]},
        {
            "passes": False,
            "findings": [{"criterion": "c", "why": "w"}, {"criterion": "d", "why": "x"}],
        },
    ],
    ids=["passes", "one-finding", "several-findings"],
)
def test_an_answer_whose_outcome_and_findings_agree_is_the_verdict_it_states(
    # `Any` because the subject is what the harness *might* answer: a typed parameter
    # would be a shape this narrowing had already accepted, which is not what it decides.
    structured: dict[str, Any],
) -> None:
    """And it arrives whole: nothing between the harness report and the operator drops one."""
    answered = plan_review._answered(structured)

    assert answered == structured, answered


def test_the_prompt_asks_for_every_criterion_the_reviewer_would_refuse() -> None:
    """The question and the schema are one contract, so the question has to ask for it.

    A schema that admits several findings under a prompt asking for one sentence buys
    nothing: the reviewer answers the question it was asked. `bar_fingerprint` covers
    both, so this is about what the wording *says* rather than about it having moved.
    """
    asked = plan_review.REVIEW_PROMPT

    assert "every criterion you would refuse" in asked, asked
    assert "not the first one, and not the worst one" in asked, asked
    assert "no findings at all" in asked, asked


def test_a_task_already_carrying_a_record_spends_no_judged_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    current = _recorded(_task(), plan_review.review_key(_task(), BAR))
    store = _Store(tmp_path / "store", [current])
    store.install(monkeypatch)
    prompts = _verdicts(monkeypatch)

    assert plan_review.main(["demo:plan"]) == 0
    assert prompts == []
    assert "1 already carried one" in capsys.readouterr().out


def test_a_review_that_stops_partway_keeps_and_reports_the_passes_it_granted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each pass is written as it is granted, so a later failure cannot unsay it.

    A diagnostic claiming nothing was recorded would send its reader looking for state
    that is there, and — worse — reading a plan as wholly unreviewed when half of it is.
    """
    first = _task(qualified_id="demo:plan/first", node_id="first")
    second = _task(qualified_id="demo:plan/second", node_id="second")
    store = _Store(tmp_path / "store", [first, second])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    answers = [PASSES]

    def verdict(prompt: str) -> plan_review.Verdict:
        if answers:
            return answers.pop(0)
        raise OSError("the chain answered nothing")

    monkeypatch.setattr(plan_review, "_verdict", verdict)

    assert plan_review.main(["demo:plan"]) == 2
    reported = capsys.readouterr().err
    assert "the chain answered nothing" in reported, reported
    assert "1 task(s) were reviewed and recorded before that" in reported, reported
    assert "beginning at second" in reported, reported
    assert isinstance(store.written("plan/first"), dict)
    assert store.written("plan/second") is None


def test_a_pass_the_store_will_not_accept_is_reported_as_a_review_that_did_not_happen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Granting a pass and writing it down are one step, so they fail as one."""
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    _verdicts(monkeypatch, PASSES)
    monkeypatch.setattr(
        plan_store,
        "write_metadata",
        lambda *_: (_ for _ in ()).throw(OSError("the record is read-only")),
    )

    assert plan_review.main(["demo:plan"]) == 2
    reported = capsys.readouterr().err
    assert "the record is read-only" in reported, reported
    assert "0 task(s) were reviewed and recorded before that" in reported, reported
    assert store.written("plan/route") is None


def _configured(monkeypatch: pytest.MonkeyPatch, plugin: str, root: str = "/tmp/store") -> None:
    """Answer `config show` for one source, so a writability read reaches no real store."""
    monkeypatch.setattr(
        plan_store,
        "store_json",
        lambda arguments: _Configuration(
            settings=[
                _Setting(key="sources.demo.plugin", value=plugin),
                _Setting(key="sources.demo.config.root", value=root),
            ]
        ),
    )


def test_a_project_that_cannot_be_read_records_nothing_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configured(monkeypatch, "local-md")
    monkeypatch.setattr(
        plan_store, "read_tasks", lambda _: (_ for _ in ()).throw(OSError("no such project"))
    )
    assert plan_review.main(["demo:absent"]) == 2
    assert "no such project" in capsys.readouterr().err


def test_a_store_no_record_can_be_written_into_is_refused_before_a_turn_is_spent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The board this repository plans against is read-only to this gate, so say so first.

    Reviewing it would otherwise pay a provider for a verdict, reach the write, find
    there is nowhere to put it, and report a plan no further run of this command can
    advance — which reads as a transient failure and is not one.
    """
    _configured(monkeypatch, "github-projects")
    prompts = _verdicts(monkeypatch)
    read: list[str] = []
    monkeypatch.setattr(plan_store, "read_tasks", lambda project: read.append(project) or [])

    assert plan_review.main(["demo:plan"]) == 2
    assert prompts == [], "a turn was spent on a review that could never be recorded"
    assert read == [], "the store was read for a plan that could never be cleared"
    reported = capsys.readouterr().err
    assert "github-projects" in reported, reported
    assert "no run of this command can record one" in reported, reported


def test_a_local_markdown_store_is_writable_and_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other answer, so a green above means the plugin decided rather than the read."""
    _configured(monkeypatch, "local-md")
    assert plan_review.unwritable("demo:plan") is None


def _harness(monkeypatch: pytest.MonkeyPatch, stdout: str, stderr: str = "") -> None:
    """Stand the `oneharness run` process in with one report and one diagnostic stream."""
    completed = subprocess.CompletedProcess(["oneharness"], 0, stdout, stderr)
    monkeypatch.setattr(
        plan_review.subprocess,
        "run",
        lambda *arguments, **keywords: completed,
    )


def test_the_judged_turn_reads_its_verdict_out_of_the_harness_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a candidate whose answer the schema validated is read as the verdict."""
    sound = {"passes": False, "findings": [{"criterion": "criterion 2", "why": "it perishes"}]}
    _harness(
        monkeypatch,
        json.dumps(
            {
                "results": [
                    "not an object",
                    {"schema_valid": False, "structured": {"passes": True, "findings": []}},
                    {"schema_valid": True, "structured": None},
                    {"schema_valid": True, "structured": {"passes": "yes", "findings": []}},
                    {"schema_valid": True, "structured": {"passes": False, "findings": []}},
                    {"schema_valid": True, "structured": sound},
                ]
            }
        ),
    )
    assert plan_review._verdict("prompt") == sound


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        ("not json", "", "no readable report"),
        ("not json", "the chain stopped", "the chain stopped"),
        (json.dumps({"results": []}), "", "no candidate answered"),
        (json.dumps({"results": "not a list"}), "", "no candidate answered"),
        (json.dumps(["not an object"]), "", "no candidate answered"),
    ],
)
def test_a_turn_that_answered_no_verdict_is_refused_rather_than_assumed(
    stdout: str, stderr: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure direction is always "not reviewed", never "reviewed and passed"."""
    _harness(monkeypatch, stdout, stderr)
    with pytest.raises(OSError, match=expected):
        plan_review._verdict("prompt")


def test_the_reviewer_is_shown_exactly_what_the_key_covers() -> None:
    """The prompt and the key are held to each other in both directions.

    A field whose change invalidates the record but which nobody was shown is one nobody
    reviewed. A field shown but not covered is the same defect wearing the opposite sign:
    the reviewer passes content that can then change under its own record. So each field
    carries a sentinel and is asserted present or absent according to which side of the
    four it is on, and a field added to one and forgotten in the other fails here.
    """
    sentinels = {
        "title": "sentinel-title",
        "content": "sentinel-body-prose",
        "kind": "sentinel-kind",
        "persona": "sentinel-persona",
        "expects_no_diff": "sentinel-expects-no-diff",
        "deps": "sentinel-dependency",
        "step id": "sentinel-step-id",
        "step prose": "sentinel-step-task",
        "step persona": "sentinel-step-persona",
    }
    unkeyed = {
        "repository": "github.com/nickderobertis/sentinel-repository",
        "a step field no author wrote": "sentinel-step-branch",
    }
    task = _task(
        title=sentinels["title"],
        content=sentinels["content"],
        metadata={
            "onepipeline.id": "route",
            "onepipeline.kind": sentinels["kind"],
            "onepipeline.persona": sentinels["persona"],
            "onepipeline.expects_no_diff": sentinels["expects_no_diff"],
            "onepipeline.steps": [
                {
                    "id": sentinels["step id"],
                    "persona": sentinels["step persona"],
                    "task": sentinels["step prose"],
                    "branch": unkeyed["a step field no author wrote"],
                }
            ],
        },
        repositories=[unkeyed["repository"]],
        deps=(sentinels["deps"],),
    )
    composed = plan_review._prompt("P", task)
    for field, sentinel in sentinels.items():
        assert sentinel in composed, f"{field} is hashed into the key and never shown: {composed}"
    for field, sentinel in unkeyed.items():
        assert sentinel not in composed, (
            f"{field} is shown to the reviewer and not covered by the key, so a pass "
            f"would stand over content the reviewer read and nothing protects: {composed}"
        )


def test_a_task_with_no_body_prose_still_composes_a_prompt() -> None:
    composed = plan_review._prompt("P", _task(content=None))
    assert "states no body prose" in composed


def test_a_planning_closeout_records_only_what_the_run_authored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan already on disk when the planner launched is not that planner's output."""
    existing = _task(qualified_id="demo:plan/existing", node_id="existing")
    store = _Store(tmp_path / "store", [existing])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    monkeypatch.setattr(plan_review, "PLAN_SOURCES", ("demo",))
    projects = ["demo:plan"]
    monkeypatch.setattr(plan_store, "local_projects", lambda source: list(projects))

    before = plan_review.plan_projects()
    assert plan_review.record_projects_new_since(before).written == []
    assert store.written("plan/existing") is None

    authored = _task(qualified_id="demo:authored/route", node_id="route")
    _Store(tmp_path / "store", [authored])
    projects.append("demo:authored")
    monkeypatch.setattr(
        plan_store,
        "read_tasks",
        lambda project: [authored] if project == "demo:authored" else [existing],
    )
    assert plan_review.record_projects_new_since(before).written == ["demo:authored/route"]
    written = store.written("authored/route")
    assert isinstance(written, dict)
    assert written["by"] == plan_review.BY_PLANNING
    assert written["key"] == plan_review.review_key(authored, BAR)
    assert store.written("plan/existing") is None


def test_a_plan_edited_beside_a_planning_run_is_left_unrecorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a project that *appeared* is recorded, so an edit beside the run is not.

    A closeout that recorded whatever moved would bless an operator's own hand edit to
    an existing plan made while their planner worked — which is the case this gate
    exists to catch, reached from the other side. The cost of the narrower rule is that
    a planner *revising* a project from an earlier run costs a `just review-plan`, and
    that is the direction this has to fail in.
    """
    existing = _task(qualified_id="demo:plan/existing", node_id="existing")
    store = _Store(tmp_path / "store", [existing])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    monkeypatch.setattr(plan_review, "PLAN_SOURCES", ("demo",))
    monkeypatch.setattr(plan_store, "local_projects", lambda source: ["demo:plan"])

    before = plan_review.plan_projects()
    edited = _task(
        qualified_id="demo:plan/existing",
        node_id="existing",
        content="## What\n\nAn operator changed this while the planner ran.\n",
    )
    store.tasks[:] = [edited]
    _Store(tmp_path / "store", [edited])

    assert plan_review.record_projects_new_since(before).written == []
    assert store.written("plan/existing") is None


def test_a_second_planning_runs_project_is_recorded_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window that is left, recorded here so it is known rather than found.

    A closeout records every plan project that appeared while its run was in flight, and
    a *second* planning run creating its own project in that window is indistinguishable
    from its own planner's output: nothing tells this host which project a dispatched
    planner authored, because the plan is its deliverable rather than its argument.

    No journey drives this, and that is deliberate rather than a gap: reaching it means
    two overlapping real planning runs, which is the very thing
    `tests/plan_tooling/test_plan_review_e2e.py`'s closeout journeys give each launch a plan
    store of its own to avoid — a peer's window spanning one of theirs is what failed a
    publication gate before they did.
    """
    store = _Store(tmp_path / "store", [])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    monkeypatch.setattr(plan_review, "PLAN_SOURCES", ("demo",))
    projects: list[str] = []
    monkeypatch.setattr(plan_store, "local_projects", lambda source: list(projects))

    before = plan_review.plan_projects()

    mine = _task(qualified_id="demo:mine/route", node_id="route")
    theirs = _task(qualified_id="demo:theirs/route", node_id="route")
    _Store(tmp_path / "store", [mine])
    _Store(tmp_path / "store", [theirs])
    projects.extend(["demo:mine", "demo:theirs"])
    monkeypatch.setattr(
        plan_store,
        "read_tasks",
        lambda project: [mine] if project == "demo:mine" else [theirs],
    )

    assert plan_review.record_projects_new_since(before).written == [
        "demo:mine/route",
        "demo:theirs/route",
    ]


def test_a_task_of_another_project_is_never_recorded_against_this_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pair decides a file that is then edited, so a mismatched pair is refused.

    A record written into a task it does not describe would read as sound afterwards:
    nothing downstream can tell a key computed for another project's task from a stale
    one, so the refusal has to be here.
    """
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    elsewhere = _task(qualified_id="demo:other/route")
    with pytest.raises(OSError, match="is not one of"):
        plan_review.write_record("demo:plan", elsewhere, "abc", plan_review.BY_REVIEW)
    with pytest.raises(OSError, match="is not one of"):
        plan_review.write_record("other:plan", _task(), "abc", plan_review.BY_REVIEW)
    assert store.written("plan/route") is None


def test_the_planning_verbs_round_trip_their_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    authored = _task(qualified_id="demo:authored/route", node_id="route")
    store = _Store(tmp_path / "store", [])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    monkeypatch.setattr(plan_review, "PLAN_SOURCES", ("demo",))
    projects: list[str] = []
    monkeypatch.setattr(plan_store, "local_projects", lambda source: list(projects))

    snapshot = tmp_path / "snapshot.json"
    assert plan_review.planning_main(["snapshot", str(snapshot)]) == 0

    _Store(tmp_path / "store", [authored])
    projects.append("demo:authored")
    monkeypatch.setattr(plan_store, "read_tasks", lambda project: [authored])
    assert plan_review.planning_main(["closeout", str(snapshot)]) == 0
    assert "1 task(s)" in capsys.readouterr().err
    assert isinstance(store.written("authored/route"), dict)


def test_a_project_a_closeout_cannot_record_is_left_alone_rather_than_failing_the_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A neighbour's plan in the window may not kill this planning run.

    A closeout cannot tell its own run's output from a concurrent one's, so every plan
    that appeared in its window is one it may meet — and one already on this host cannot
    take a record at all: a project `onepipeline`'s settlement write-back has re-rendered
    holds a `metadata` block `plan_store.write_metadata` refuses to edit around. Raising
    there would exit a launch non-zero over an unrelated plan, so the project is passed
    over and named, and the run this closeout belongs to still records what it authored.
    """
    authored = _task(qualified_id="demo:authored/route", node_id="route")
    neighbour = _task(qualified_id="demo:neighbour/route", node_id="route")
    store = _Store(tmp_path / "store", [])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    monkeypatch.setattr(plan_review, "PLAN_SOURCES", ("demo",))
    projects: list[str] = []
    monkeypatch.setattr(plan_store, "local_projects", lambda source: list(projects))

    snapshot = tmp_path / "snapshot.json"
    assert plan_review.planning_main(["snapshot", str(snapshot)]) == 0

    _Store(tmp_path / "store", [authored, neighbour])
    projects.extend(["demo:authored", "demo:neighbour"])
    monkeypatch.setattr(
        plan_store,
        "read_tasks",
        lambda project: [authored] if project == "demo:authored" else [neighbour],
    )
    # A line no rendering of this host produces, so no writer can account for it: a
    # sequence entry inside the mapping. A plain YAML key is *not* such a line — that is
    # the plan store's own rendering, and `plan_store.write_metadata` edits around it.
    document = tmp_path / "store" / "tasks" / "neighbour" / "route.md"
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            '  "onepipeline.id": "route"', '  "onepipeline.id": "route"\n  - a list entry'
        ),
        encoding="utf-8",
    )

    assert plan_review.planning_main(["closeout", str(snapshot)]) == 0
    reported = capsys.readouterr().err
    assert "demo:neighbour" in reported, reported
    assert "cannot edit around" in reported, reported
    assert "1 task(s)" in reported, reported
    assert isinstance(store.written("authored/route"), dict)
    assert store.written("neighbour/route") is None


@pytest.mark.parametrize("held", ['{"a": 1}', "[7]", "{bad"], ids=["object", "values", "malformed"])
def test_a_snapshot_a_closeout_cannot_read_records_nothing(
    held: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(plan_review, "PLAN_SOURCES", ("demo",))
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(held, encoding="utf-8")
    assert plan_review.planning_main(["closeout", str(snapshot)]) == 2
    assert "plan-review:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("shape", "refusal"),
    (
        ("symlink", "is a symlink"),
        ("directory", "not a regular file"),
        ("foreign", "does not hold a review snapshot"),
    ),
    ids=["symlink", "directory", "foreign"],
)
def test_a_snapshot_is_never_written_through_a_destination_it_did_not_write(
    shape: str, refusal: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verb takes its destination from the command line, so it validates it.

    `scripts/plan.sh` hands over its own `mktemp` file and nothing else, so an argument
    that is anything but that shape is a slip — and the two slips that cost something
    are a symlink, whose target this would truncate on somebody else's behalf, and a
    path already holding content of its own. Both are refused before the write.
    """
    monkeypatch.setattr(plan_review, "PLAN_SOURCES", ("demo",))
    monkeypatch.setattr(plan_store, "local_projects", lambda source: [])
    destination = tmp_path / "destination"
    owned = tmp_path / "owned.json"
    owned.write_text("its owner's content", encoding="utf-8")
    match shape:
        case "symlink":
            destination.symlink_to(owned)
        case "directory":
            destination.mkdir()
        case _:
            destination.write_text("its owner's content", encoding="utf-8")

    with pytest.raises(OSError, match=refusal):
        plan_review.snapshot_file(destination)
    assert plan_review.planning_main(["snapshot", str(destination)]) == 2
    assert owned.read_text(encoding="utf-8") == "its owner's content"


def test_a_snapshot_destination_that_is_absent_or_already_a_snapshot_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two shapes `scripts/plan.sh` actually produces: a fresh file, and a rewrite.

    `mktemp` creates an empty file, and a second launch reusing a path finds the
    previous launch's snapshot there — so a validation that refused either would refuse
    the only caller this verb has.
    """
    monkeypatch.setattr(plan_review, "PLAN_SOURCES", ("demo",))
    monkeypatch.setattr(plan_store, "local_projects", lambda source: ["demo:already"])
    for destination in (tmp_path / "absent", tmp_path / "empty", tmp_path / "prior"):
        if destination.name == "empty":
            destination.touch()
        if destination.name == "prior":
            destination.write_text('["demo:earlier"]', encoding="utf-8")
        assert plan_review.planning_main(["snapshot", str(destination)]) == 0
        assert json.loads(destination.read_text(encoding="utf-8")) == ["demo:already"]
