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

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, get_type_hints

import jsonschema
import pytest
import yaml
from project_fixtures import budgeted
from published_tools import ONETASKGRAPH_BIN
from test_host_installs import RELEASE_RULE, RELEASES

from orchestrator import criteria_guard, host_installs, plan_budgets, plan_review, plan_store
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


def _metadata(**fields: object) -> dict[str, object]:
    """The default task's metadata with ``fields`` written over it, as `onepipeline.` keys."""
    return {
        "onepipeline.id": "route",
        "onepipeline.persona": "engineer",
        **{f"onepipeline.{key}": value for key, value in fields.items()},
    }


def test_the_key_changes_with_every_authored_field() -> None:
    """The fields the record covers: title, prose, kind, persona, deps, whether the node
    expects no diff, the repository it names, how it adopts a release and under which
    policy, and a step's own three."""
    base = plan_review.review_key(_task(), BAR)
    moved = {
        "title": _task(title="feat: add another route"),
        "content": _task(content="## What\n\nAdd a different route.\n"),
        "kind": _task(metadata={"onepipeline.id": "route", "onepipeline.kind": "human"}),
        "persona": _task(metadata={"onepipeline.id": "route", "onepipeline.persona": "reviewer"}),
        "deps": _task(deps=("design",)),
        "cross-DAG deps": _task(metadata=_metadata(deps=["run:r-upstream#adopt"])),
        "expects_no_diff": _task(metadata=_metadata(expects_no_diff=True)),
        "repo (hosted)": _task(repositories=["github.com/nickderobertis/elsewhere"]),
        "repo (local checkout)": _task(metadata=_metadata(repo="/home/nick/projects/org-apps")),
        "adoption": _task(metadata=_metadata(adoption="published")),
        "consumes": _task(metadata=_metadata(consumes={"engine": "crate"})),
        "merge_policy": _task(metadata=_metadata(merge_policy="change-auto")),
    }
    for field, task in moved.items():
        assert plan_review.review_key(task, BAR) != base, field
    # The two spellings of a repository are read in the engine's order — the record's
    # own list first — so a hosted origin and the same origin on the reserved key are one
    # value, and a local checkout beside a hosted origin is the hosted one.
    assert plan_review.repository_of(_task(repositories=["github.com/o/n"])) == "github.com/o/n"
    assert plan_review.repository_of(_task(metadata=_metadata(repo="github.com/o/n"))) == (
        "github.com/o/n"
    )
    assert plan_review.repository_of(_task(metadata=_metadata(repo=7))) is None


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

    `max_turns` is the shape of it: a dispatch control its author sets and no reviewer
    rules on, and so is `execution_checkout`, which says where a dispatch works rather
    than what it is asked. A task **retargeted at another repository** used to be the
    example here and is now on the other side — see the test above — because the
    pin-path question is answered differently for a node of this host's own repository.
    """
    base = plan_review.review_key(_task(), BAR)
    unkeyed = {
        "max_turns": _task(metadata=_metadata(max_turns=60)),
        "execution_checkout": _task(metadata=_metadata(execution_checkout="isolated")),
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
    budgeted = _recorded(_task(metadata=_metadata(max_turns=60)), key)
    assert plan_review.unreviewed([budgeted]) == []

    edited = _recorded(_task(content="## What\n\nSomething else entirely.\n"), key)
    assert plan_review.unreviewed([edited]) == [edited]
    # A task retargeted at another repository is re-reviewed, which reverses what this
    # gate once decided: the reviewer answers the pin-path question by the repository.
    retargeted = _recorded(_task(repositories=["github.com/nickderobertis/elsewhere"]), key)
    assert plan_review.unreviewed([retargeted]) == [retargeted]
    for moved in (
        _recorded(_task(metadata=_metadata(adoption="published")), key),
        _recorded(_task(metadata=_metadata(consumes={"engine": "crate"})), key),
        _recorded(_task(metadata=_metadata(merge_policy="change-auto")), key),
    ):
        assert plan_review.unreviewed([moved]) == [moved]


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
    meaning, never by phrase — and whether a lifecycle node whose criteria describe work
    that changes no file declares `expects_no_diff`. The first two are every task's and
    sit in `REVIEW_PROMPT`; the third is a lifecycle node's alone and sits in its own
    constant, which `REVIEW_PROMPT` no longer restates. `bar_fingerprint` covers both, so
    this is about what the wording *says* rather than about it having moved.
    """
    # Flattened, because the prompt is hard-wrapped prose: a phrase longer than one of
    # its lines would otherwise be absent from a prompt that says it.
    asked = " ".join(plan_review.REVIEW_PROMPT.split())
    lifecycle = " ".join(plan_review.LIFECYCLE_NO_DIFF_QUESTION.split())
    field = plan_review.EXPECTS_NO_DIFF.removeprefix("onepipeline.")

    for question in (
        "This turn is the only thing that asks about a version literal",
        "No deterministic check refuses one any more",
        "refuse criteria that leave a demand their own task or their own bar makes unanswered",
        "by **meaning rather than by wording**",
    ):
        assert question in asked, question
    assert field not in asked, "every task is asked the lifecycle question again"
    for question in (
        "This task is a **lifecycle** node",
        "changes no file in that repository",
        f"does not declare `{field}`, refuse the criterion that describes that work",
        "`failed` as `empty-branch`",
        "belongs on a **direct** node, one naming no repository",
        f"`{field}` fits only a node nobody needs to dispatch",
        f"*does* declare `{field}` but the criteria describe work a worker must perform, "
        "refuse that too",
        "Leave alone a node whose criteria do require a file of its repository to change",
    ):
        assert question in lifecycle, question


@pytest.mark.parametrize(
    ("task", "asked"),
    [
        pytest.param(_task(), False, id="direct"),
        pytest.param(
            _task(content="## What\n\nProvision the host; it changes no repository file.\n"),
            False,
            id="direct, describing work outside every repository",
        ),
        pytest.param(
            _task(repositories=["github.com/nickderobertis/elsewhere"]), True, id="hosted repo"
        ),
        pytest.param(
            _task(metadata=_metadata(repo="/home/nick/projects/org-apps")),
            True,
            id="local checkout",
        ),
        pytest.param(
            _task(metadata=_metadata(steps=[{"id": "a", "persona": "engineer", "task": "x"}])),
            True,
            id="steps",
        ),
        pytest.param(
            _task(
                metadata={"onepipeline.id": "route", "onepipeline.kind": "human"},
                repositories=["github.com/nickderobertis/elsewhere"],
            ),
            False,
            id="human, naming a repository",
        ),
        pytest.param(
            _task(content="## What\n\nWork on a branch of github.com/nickderobertis/x.\n"),
            False,
            id="direct, whose prose names a repository",
        ),
    ],
)
def test_only_a_lifecycle_node_is_asked_the_expects_no_diff_question(
    task: StoreTask, asked: bool
) -> None:
    """The question is asked by a node's fields, never by its prose.

    A direct node — no repository, no steps — runs in the launching checkout and commits
    nothing, so it can never settle `empty-branch`, and the engine refuses
    `expects_no_diff` beside the persona it is dispatched under: asking it the question
    refuses a shape no declaration can repair. A human node is dispatched nowhere.
    """
    composed = plan_review._prompt("P", task)
    assert (plan_review.LIFECYCLE_NO_DIFF_QUESTION in composed) is asked, composed
    assert plan_review.is_lifecycle(task) is (
        asked or task.metadata.get("onepipeline.kind") == "human"
    )


def test_the_lifecycle_question_is_hashed_into_both_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rewording the lifecycle question moves every task record and every plan record.

    It reaches the prompt by a route of its own rather than inside `REVIEW_PROMPT`, so a
    regression dropping it from the digest would leave every pass standing under a
    question nobody was asked.
    """
    task_bar = plan_review.bar_fingerprint()
    plan_bar = plan_review.plan_bar_fingerprint()
    monkeypatch.setattr(
        plan_review,
        "LIFECYCLE_NO_DIFF_QUESTION",
        f"{plan_review.LIFECYCLE_NO_DIFF_QUESTION}\nOne sentence on.\n",
    )
    assert plan_review.bar_fingerprint() != task_bar
    assert plan_review.plan_bar_fingerprint() != plan_bar


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


def test_the_bar_is_the_review_bar_the_verdict_schema_and_the_budgets_templates() -> None:
    """The reviewer reads the budget answers, so the templates they render through are bar."""
    assert (
        Path("personas") / "planner.yaml",
        Path("config") / "plan-review-verdict.schema.json",
        Path("templates") / "plan-task-budgets.md.j2",
        Path("templates") / "plan-description.md.j2",
    ) == plan_review.BAR_FILES


@pytest.mark.parametrize("edited", range(len(plan_review.BAR_FILES)), ids=lambda i: f"file-{i}")
def test_the_bar_fingerprint_reads_the_files_the_bar_is(tmp_path: Path, edited: int) -> None:
    """Editing any of the files, by one byte, invalidates every record made under it.

    Both levels of record: a task's pass found under the bar it was granted at is not found
    under the moved one, and nor is the plan's.
    """
    original = tmp_path / "original"
    moved = tmp_path / "moved"
    for root in (original, moved):
        for relative in plan_review.BAR_FILES:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((REPO_ROOT / relative).read_bytes())
    assert plan_review.bar_fingerprint(original) == plan_review.bar_fingerprint(REPO_ROOT)
    target = moved / plan_review.BAR_FILES[edited]
    target.write_bytes(target.read_bytes() + b"\n")
    assert plan_review.bar_fingerprint(moved) != plan_review.bar_fingerprint(original)

    granted = _recorded(
        _task(), plan_review.review_key(_task(), plan_review.bar_fingerprint(original))
    )
    assert plan_review.unreviewed([granted], plan_review.bar_fingerprint(original)) == []
    assert plan_review.unreviewed([granted], plan_review.bar_fingerprint(moved)) == [granted]
    plan = {"goal": {"text": "Deliver it"}, "tasks": []}
    record = {
        "metadata": {
            plan_review.RECORD_KEY: {
                "key": plan_review.plan_key(plan, plan_review.plan_bar_fingerprint(original))
            }
        }
    }
    assert not plan_review.plan_unreviewed(record, plan, plan_review.plan_bar_fingerprint(original))
    assert plan_review.plan_unreviewed(record, plan, plan_review.plan_bar_fingerprint(moved))


#: The canonical fixture of the two shapes a plan reaches a key in: what
#: `plan_store.read_plan` answers for the store path, and what the engine's `plan check`
#: hands `scripts/plan-check.sh` for the check path. They differ exactly where the two
#: readers differ — the loaded document carries the engine's resolved `repo` beside the
#: store's `metadata` map verbatim, drops the trailing newline off a task, and lists its
#: nodes in its own order — so a key equal across the pair is one both readers hash alike.
STORE_TASKS: list[dict[str, object]] = [
    {
        "id": "adopt",
        "title": "feat: adopt the engine release",
        "task": "## What\n\nMove the pin.\n",
        "persona": "engineer",
        "repo": "github.com/nickderobertis/ai-orchestrator",
        "deps": ["engine"],
        "adoption": "published",
        "execution_checkout": "ai-orchestrator-isolated",
    },
    {
        "id": "engine",
        "title": "feat: link the fix",
        "task": "## What\n\nLink it.\n",
        "persona": "engineer",
        "repo": "github.com/nickderobertis/onepipeline",
        "consumes": {"library": "crate"},
        "merge_policy": "change-auto",
        "deps": ["library"],
    },
    {
        "id": "library",
        "title": "feat: fix the library",
        "task": "## What\n\nFix it.\n",
        "persona": "engineer",
        "repo": "github.com/nickderobertis/onevcs",
    },
]
STORE_PLAN: dict[str, object] = {
    "name": "P",
    "schema_version": 3,
    "goal": {"text": "Put the fix in force here"},
    "tasks": STORE_TASKS,
}
LOADED_PLAN: dict[str, object] = {
    "schema_version": 3,
    "name": "P",
    "goal": {"text": "Put the fix in force here"},
    "concurrency": 4,
    "tasks": [
        {
            "id": "library",
            "task": "## What\n\nFix it.",
            "persona": "engineer",
            "repo": "github.com/nickderobertis/onevcs",
            "title": "feat: fix the library",
            "metadata": {"onepipeline.id": "library", "onepipeline.persona": "engineer"},
        },
        {
            "id": "engine",
            "task": "## What\n\nLink it.",
            "persona": "engineer",
            "deps": ["library"],
            "repo": "github.com/nickderobertis/onepipeline",
            "merge_policy": "change-auto",
            "title": "feat: link the fix",
            "consumes": {"library": "crate"},
            "metadata": {
                "onepipeline.consumes": {"library": "crate"},
                "onepipeline.id": "engine",
                "onepipeline.merge_policy": "change-auto",
                "onepipeline.persona": "engineer",
            },
        },
        {
            "id": "adopt",
            "task": "## What\n\nMove the pin.",
            "persona": "engineer",
            "deps": ["engine"],
            "repo": "github.com/nickderobertis/ai-orchestrator",
            "title": "feat: adopt the engine release",
            "execution_checkout": "ai-orchestrator-isolated",
            "adoption": "published",
            "metadata": {
                "onepipeline.adoption": "published",
                "onepipeline.execution_checkout": "ai-orchestrator-isolated",
                "onepipeline.id": "adopt",
                "onepipeline.persona": "engineer",
            },
        },
    ],
}


def _with_node(plan: Mapping[str, object], node_id: str, **fields: object) -> dict[str, object]:
    """``plan`` with ``fields`` written over the node ``node_id`` names."""
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    return {
        **plan,
        "tasks": [{**task, **fields} if task["id"] == node_id else task for task in tasks],
    }


def test_the_plan_key_is_one_key_over_the_stores_plan_and_the_loaded_document() -> None:
    """Both readers hash one thing, so the record one wrote is the record the other reads.

    `just review-plan` keys the plan `plan_store.read_plan` answers, and `just
    check-plan` keys the document the engine's loader hands the registered check; a
    key that told the two apart would refuse every plan the review had just recorded.
    """
    assert plan_review.plan_key(STORE_PLAN, BAR) == plan_review.plan_key(LOADED_PLAN, BAR)


def test_the_plan_key_changes_with_the_goal_and_every_node_field_it_renders() -> None:
    """Every field the plan-level prompt shows moves the key, and each of them alone."""
    base = plan_review.plan_key(STORE_PLAN, BAR)
    moved = {
        "goal": {**STORE_PLAN, "goal": {"text": "Something else"}},
        "a node added": {**STORE_PLAN, "tasks": [*STORE_TASKS, {"id": "docs"}]},
        "a node removed": {**STORE_PLAN, "tasks": STORE_TASKS[1:]},
        "id": _with_node(STORE_PLAN, "adopt", id="adopt-it"),
        "title": _with_node(STORE_PLAN, "adopt", title="feat: adopt something else"),
        "repo": _with_node(STORE_PLAN, "adopt", repo="github.com/nickderobertis/elsewhere"),
        "deps": _with_node(STORE_PLAN, "adopt", deps=["library"]),
        "adoption": _with_node(STORE_PLAN, "adopt", adoption="fast"),
        "consumes": _with_node(STORE_PLAN, "engine", consumes={"library": "wheel"}),
        "merge_policy": _with_node(STORE_PLAN, "engine", merge_policy="change-open"),
        "kind": _with_node(STORE_PLAN, "library", kind="human"),
        "expects_no_diff": _with_node(STORE_PLAN, "library", expects_no_diff=True),
        "persona": _with_node(STORE_PLAN, "library", persona="researcher"),
        "task": _with_node(STORE_PLAN, "adopt", task="## What\n\nMove a different pin.\n"),
        "a step's prose": _with_node(
            STORE_PLAN, "adopt", steps=[{"id": "move", "persona": "engineer", "task": "Move."}]
        ),
        "bar": None,
    }
    for field, plan in moved.items():
        key = (
            plan_review.plan_key(STORE_PLAN, "moved")
            if plan is None
            else plan_review.plan_key(plan, BAR)
        )
        assert key != base, field


def test_the_plan_key_survives_what_no_author_wrote_and_what_no_reviewer_read() -> None:
    """A settlement write-back, a dispatch control, the store's own order: none moves it."""
    base = plan_review.plan_key(STORE_PLAN, BAR)
    standing = {
        "status": _with_node(STORE_PLAN, "adopt", status="done", branch="onevcs/s-abc"),
        "max_turns": _with_node(STORE_PLAN, "adopt", max_turns=60),
        "execution_checkout": _with_node(STORE_PLAN, "adopt", execution_checkout="other"),
        "the store's order": {**STORE_PLAN, "tasks": list(reversed(STORE_TASKS))},
        "re-indented prose": _with_node(STORE_PLAN, "adopt", task="## What\n\n  Move  the pin."),
        "a step field no author wrote": _with_node(
            STORE_PLAN, "adopt", steps=[{"id": "m", "persona": "e", "task": "M", "branch": "b"}]
        ),
    }
    for field, plan in standing.items():
        if field.startswith("a step"):
            base_for_steps = plan_review.plan_key(
                _with_node(STORE_PLAN, "adopt", steps=[{"id": "m", "persona": "e", "task": "M"}]),
                BAR,
            )
            assert plan_review.plan_key(plan, BAR) == base_for_steps, field
            continue
        assert plan_review.plan_key(plan, BAR) == base, field
    # A plan whose `deps` say the same edges in another order is the same plan.
    assert plan_review.plan_key(
        _with_node(STORE_PLAN, "adopt", deps=["engine", "library"]), BAR
    ) == plan_review.plan_key(_with_node(STORE_PLAN, "adopt", deps=["library", "engine"]), BAR)


@pytest.mark.parametrize(
    "plan",
    ["not a plan", {"tasks": "route"}, {"tasks": ["route", 7]}, {}],
    ids=["not-an-object", "tasks-not-a-list", "tasks-not-objects", "no-tasks"],
)
def test_a_plan_this_cannot_walk_is_keyed_over_no_nodes_rather_than_raised(plan: object) -> None:
    """The safe direction: it earns a key no record will match, and the plan is refused."""
    assert plan_review.plan_nodes(plan) == []
    assert plan_review.plan_goal(plan) is None
    assert plan_review.plan_key(plan, BAR) != plan_review.plan_key(STORE_PLAN, BAR)
    # A goal that is not the `{"text": ...}` shape is keyed as whatever it is.
    assert plan_review.plan_goal({"goal": "bare"}) == "bare"
    # Steps this cannot narrow key as no steps, through the node's review key.
    assert plan_review.plan_nodes({"tasks": [{"id": "s", "steps": "not steps"}]}) == (
        plan_review.plan_nodes({"tasks": [{"id": "s"}]})
    )


def test_the_plan_reviewer_is_shown_exactly_what_the_plan_key_covers() -> None:
    """The same pairing as the task prompt's, one level up: shown, or proven read.

    Every keyed field of a node is rendered, except its `review_key` — which stands for
    the body, the steps and the budgets it owns — and that one is rendered as the
    per-task record it must equal, beside the path of the content it covers. The body
    itself never reaches the prompt beyond its summary, and nothing unkeyed does either.
    """
    assert set(plan_review.KeyedNode.__annotations__) == {
        *plan_review.SHOWN_FIELDS,
        "review_key",
    }, "a keyed node field is neither shown nor stood in for by the review key"
    sentinels = {
        "goal": "sentinel-goal",
        "id": "sentinel-id",
        "title": "sentinel-title",
        "repo": "github.com/nickderobertis/sentinel-repository",
        "deps": "sentinel-dependency",
        "adoption": "sentinel-adoption",
        "consumes": "sentinel-consumed-target",
        "merge_policy": "sentinel-merge-policy",
        "kind": "sentinel-kind",
        "expects_no_diff": "sentinel-expects-no-diff",
        "persona": "sentinel-persona",
        "summary": "sentinel-summary",
        "step id": "sentinel-step-id",
        "step persona": "sentinel-step-persona",
        "record": "sentinel-record-key",
        "path": "/sentinel/store/tasks/sentinel-task-file.md",
    }
    unread = {
        "criteria": "sentinel-criterion",
        "a later paragraph": "sentinel-later-paragraph",
        "step prose": "sentinel-step-task",
        "max_turns": "sentinel-max-turns",
        "step branch": "sentinel-step-branch",
    }
    plan = {
        "goal": {"text": sentinels["goal"]},
        "tasks": [
            {
                "id": sentinels["id"],
                "title": sentinels["title"],
                "repo": sentinels["repo"],
                "deps": [sentinels["deps"]],
                "adoption": sentinels["adoption"],
                "consumes": {"library": sentinels["consumes"]},
                "merge_policy": sentinels["merge_policy"],
                "kind": sentinels["kind"],
                "expects_no_diff": sentinels["expects_no_diff"],
                "persona": sentinels["persona"],
                "task": (
                    f"## What\n\nThe {sentinels['summary']} of the work.\n\n"
                    f"{unread['a later paragraph']}\n\n"
                    f"## Acceptance criteria\n\n- {unread['criteria']}\n"
                ),
                "max_turns": unread["max_turns"],
                "steps": [
                    {
                        "id": sentinels["step id"],
                        "persona": sentinels["step persona"],
                        "task": unread["step prose"],
                        "branch": unread["step branch"],
                    }
                ],
            }
        ],
    }
    view = plan_review.PlanView(
        tasks={sentinels["id"]: sentinels["path"]},
        records={sentinels["id"]: {"key": sentinels["record"], "by": "review-plan"}},
    )
    composed = plan_review._plan_prompt(plan, view=view)
    for field, sentinel in sentinels.items():
        assert sentinel in composed, f"{field} is keyed or stands for keyed content, never shown"
    for field, sentinel in unread.items():
        assert sentinel not in composed, f"{field} reached the compact view"
    # Beside the bar: the table with both rungs stated, the origin, and the question.
    assert composed.startswith(plan_review.PLAN_REVIEW_PROMPT)
    assert host_installs.rendered() in composed
    assert plan_review.RUNGS in composed
    assert f"`{plan_review.host_repository()}`" in composed
    assert (REPO_ROOT / plan_review.BAR_FILES[0]).read_text(encoding="utf-8") in composed
    # A node with no prose and no steps still renders, saying so.
    assert "states no body prose" in plan_review._plan_prompt({"tasks": [{"id": "bare"}]})


def test_the_operational_notes_file_named_is_the_one_the_criteria_guard_reads() -> None:
    """Restated rather than imported, because `criteria_guard` reads `plan_review`."""
    assert plan_review.APPENDIX == criteria_guard.APPENDIX
    composed = plan_review._plan_prompt({"tasks": [{"id": "bare"}]})
    assert str(REPO_ROOT / criteria_guard.APPENDIX) in composed


def test_the_plan_reviewer_is_asked_both_questions_and_told_the_pass_case() -> None:
    """What the plan-level turn owns, asked of the prompt that is the only place it is asked."""
    asked = " ".join(plan_review.PLAN_REVIEW_PROMPT.split())
    for question in (
        "does the goal need a change in one of the producers the table names to be "
        "**in force on this host**",
        "A plan naming no producer passes",
        "touches a producer for a reason this host does not consume",
        "your verdict says why you read it that way",
        "errs toward missing",
        "is there a node of this host's own repository that adopts that producer's release",
        "reachable from the producer's node through `deps`",
        "waits `published`",
        "names that row's `config/<pin>.version` in its acceptance criteria",
        "or, for a row naming no pin, the script that row says installs the wheel",
        "names no version, commit or branch of its own",
        "reaches a dispatch only through `config/onepipeline.version`, via an `onepipeline` "
        "node that links the crate",
        "never through that library's own CLI pin",
        f"a finding whose `criterion` is `{plan_review.THE_PLAN}`",
        "a finding whose `criterion` names the node's id",
    ):
        assert question in asked, question
    assert "waits `published` by default" in plan_review.RUNGS
    assert "every other repository's rung is `fast`" in plan_review.RUNGS


def test_the_rungs_the_plan_reviewer_is_told_are_the_overrides_own() -> None:
    """`RUNGS` restates `config/onevcs.releases.yml`, so it is held to that file.

    The override is read the way `tests/test_host_installs.py` reads it — as text, by
    the one-line `match` shape every rule is written in — for this repository's own rule
    and for the `default:` every other repository resolves to.
    """
    text = RELEASES.read_text(encoding="utf-8")
    (own,) = [
        rule for rule in RELEASE_RULE.finditer(text) if rule["name"].strip() == "ai-orchestrator"
    ]
    stated = re.search(r"^    adoption: (\S+)$", own["fields"], re.MULTILINE)
    assert stated is not None, own["fields"]
    assert stated.group(1) == "published", (
        "this repository's own rung moved in config/onevcs.releases.yml"
    )
    assert f"waits `{stated.group(1)}` by default" in plan_review.RUNGS
    default = re.search(r"^default:\n  adoption: (\S+)$", text, re.MULTILINE)
    assert default is not None, text
    assert f"every other repository's rung is `{default.group(1)}`" in plan_review.RUNGS


def test_both_reviewers_read_the_installer_a_pinless_row_names_in_place_of_a_pin() -> None:
    """The row the no-pin clause is about reaches both prompts naming its installer.

    Both reviewers are told a row naming no pin names the script that installs it; that
    is only answerable if the table they read says which script, and says no pin moves.
    """
    (pinless,) = [row for row in host_installs.INSTALLED if row.pin is None]
    stated = f"`{pinless.artifact}`, which `{pinless.installer}` installs with no `config/` pin"
    assert stated in plan_review._prompt("P", _task(content="Adopt it.")), stated
    assert stated in plan_review._plan_prompt({"tasks": [{"id": "bare"}]}), stated


def test_the_task_reviewer_is_asked_the_pin_path_question_in_both_halves() -> None:
    """The per-task half: name the pin, name no version — and read the repository directly."""
    asked = " ".join(plan_review.REVIEW_PROMPT.split())
    for sentence in (
        "**It names the pin it moves.**",
        "must name, in its `## Acceptance criteria`, the `config/<pin>.version` the table's "
        "row gives",
        "or, for a row naming no pin, the script that row says installs it",
        "adopts nothing a dispatch runs",
        "by comparing the header's `repo` with the origin stated below — never by inferring "
        "it from the prose",
        "consuming a producer's crate or package for its own manifest, is not asked this",
        "no particular phrase is required",
        "do not refuse a task here for naming the wrong one",
        "**It names no version of its own.**",
        "naming which version, commit or branch of the dependency to pin",
        "a release the node waits for is not yet an anchor",
    ):
        assert sentence in asked, sentence


def test_the_plan_bar_covers_the_task_bar_the_plan_question_and_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct from the task bar, and moved by exactly three things.

    Rewording the plan-level prompt moves every plan record and no task record; moving
    the task bar moves both; and a change to the table moves both, because both prompts
    render it.
    """
    before = plan_review.plan_bar_fingerprint()
    task_bar = plan_review.bar_fingerprint()
    assert before != task_bar

    monkeypatch.setattr(
        plan_review, "PLAN_REVIEW_PROMPT", f"{plan_review.PLAN_REVIEW_PROMPT}\nMore.\n"
    )
    assert plan_review.plan_bar_fingerprint() != before
    assert plan_review.bar_fingerprint() == task_bar, "the plan prompt moved the task bar"
    monkeypatch.undo()

    monkeypatch.setattr(plan_review, "REVIEW_PROMPT", f"{plan_review.REVIEW_PROMPT}\nMore.\n")
    assert plan_review.plan_bar_fingerprint() != before
    monkeypatch.undo()

    monkeypatch.setattr(host_installs, "rendered", lambda: "- one more wheel.\n")
    assert plan_review.plan_bar_fingerprint() != before
    assert plan_review.bar_fingerprint() != task_bar, "the table moved the plan bar alone"
    monkeypatch.undo()

    monkeypatch.setattr(plan_review, "host_repository", lambda: "github.com/elsewhere/host")
    assert plan_review.bar_fingerprint() != task_bar, "this host's own repository is not in the bar"


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        (
            "https://github.com/nickderobertis/ai-orchestrator.git",
            "github.com/nickderobertis/ai-orchestrator",
        ),
        (
            "https://github.com/nickderobertis/ai-orchestrator",
            "github.com/nickderobertis/ai-orchestrator",
        ),
        (
            "git@github.com:nickderobertis/ai-orchestrator.git",
            "github.com/nickderobertis/ai-orchestrator",
        ),
        (
            "ssh://git@github.com/nickderobertis/ai-orchestrator.git",
            "github.com/nickderobertis/ai-orchestrator",
        ),
    ],
    ids=["https-git", "https", "scp-like", "ssh"],
)
def test_this_hosts_own_repository_is_the_normalized_origin_of_its_remote(
    url: str, origin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every spelling a configured remote takes reads to the one shape a record names."""
    _git_answers(monkeypatch, 0, url)
    assert plan_review.host_repository() == origin


def test_this_checkouts_own_remote_is_what_the_host_repository_is_read_from() -> None:
    """Against the real remote: what git says this checkout's origin is, normalized."""
    asked = subprocess.run(
        [*plan_review.ORIGIN_COMMAND], cwd=REPO_ROOT, text=True, capture_output=True, check=True
    )
    assert plan_review.host_repository() == plan_review.hosted_origin(asked.stdout.strip())


@pytest.mark.parametrize(
    ("code", "stdout", "stderr", "refusal"),
    [
        (2, "", "fatal: No such remote 'origin'", "could not be read"),
        (0, "/home/nick/projects/local-only", "", "not a hosted"),
        (0, "", "", "not a hosted"),
    ],
    ids=["no-remote", "local-path", "empty"],
)
def test_a_remote_that_names_no_hosted_origin_is_refused_rather_than_guessed(
    code: int, stdout: str, stderr: str, refusal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review against an unknown origin would be a pass over a question nobody could decide."""
    _git_answers(monkeypatch, code, stdout, stderr)
    with pytest.raises(OSError, match=refusal):
        plan_review.host_repository()
    with pytest.raises(OSError, match=refusal):
        plan_review.bar_fingerprint()


def test_a_host_that_cannot_run_git_is_refused_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plan_review.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError("git")),
    )
    with pytest.raises(OSError, match="git could not be run"):
        plan_review.host_repository()


def _git_answers(monkeypatch: pytest.MonkeyPatch, code: int, stdout: str, stderr: str = "") -> None:
    """Stand git in at the one boundary the origin crosses into this module."""

    def run(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert tuple(command) == plan_review.ORIGIN_COMMAND, command
        return subprocess.CompletedProcess(list(command), code, f"{stdout}\n", stderr)

    monkeypatch.setattr(plan_review.subprocess, "run", run)


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


#: Every JSON Schema keyword whose presence at a schema's top level is a combinator. The
#: Claude Code and Anthropic API structured-output validators refuse one there, so a
#: schema carrying one fails every Claude candidate a chain falls through to.
TOP_LEVEL_COMBINATORS = frozenset({"allOf", "oneOf", "anyOf", "not", "if", "then", "else"})

#: The one dialect every harness this repository routes to accepts: Claude Code's own
#: validator does not know the 2020-12 meta-schema and refuses the whole turn naming it.
DRAFT_07 = "http://json-schema.org/draft-07/schema#"


def _harness_schemas() -> dict[str, Path]:
    """Every repository-owned schema a harness config hands a turn, by config name.

    Read off the configs rather than listed, so a role that gains a `schema_file` is
    held to the subset below by being configured. Each path resolves against its own
    config's directory, which is where oneharness resolves it.
    """
    found: dict[str, Path] = {}
    for config in sorted(REPO_ROOT.glob("oneharness*.toml")):
        named = tomllib.loads(config.read_text(encoding="utf-8")).get("schema_file")
        if isinstance(named, str):
            found[config.name] = config.parent / named
    return found


def _untyped(schema: Mapping[str, Any], where: str) -> list[str]:
    """Every subschema reached through `properties` or an object-valued `items` that
    declares no `type`, by where it sits — the two locations these schemas nest at, which
    the meta-schema check and the combinator check beside this leave unread. `Any`
    because JSON Schema is recursive and each level is read for different keys, so a
    narrower type would restate the schema files this walks, as `_verdict_schema` says."""
    missing = [] if "type" in schema else [where]
    for name, child in schema.get("properties", {}).items():
        missing += _untyped(child, f"{where}.properties.{name}")
    if isinstance(schema.get("items"), Mapping):
        missing += _untyped(schema["items"], f"{where}.items")
    return missing


def test_every_schema_a_harness_is_handed_is_one_every_harness_accepts() -> None:
    """The verdict and the drafter's body are the two, in the subset Claude accepts.

    The verdict is handed by both reviewer roles, per task and whole plan. Both reach a
    chain that falls through to Claude Code, whose `--json-schema` refuses a dialect it
    does not know and whose API refuses a top-level combinator, so a schema outside that
    subset fails every Claude candidate rather than one answer. Each is checked against
    the draft-07 meta-schema, declares its dialect as draft-07 or not at all, carries a
    `type` on every subschema strict typing reads, and has no combinator at its top
    level.
    """
    schemas = _harness_schemas()
    assert {name: path.relative_to(REPO_ROOT) for name, path in schemas.items()} == {
        "oneharness.plan-review.toml": plan_review.BAR_FILES[1],
        "oneharness.plan-review-whole.toml": plan_review.BAR_FILES[1],
        "oneharness.pr-author.toml": Path("config") / "pr-author-body.schema.json",
    }, schemas
    for name, path in schemas.items():
        schema = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft7Validator.check_schema(schema)
        assert schema.get("$schema", DRAFT_07) == DRAFT_07, name
        assert TOP_LEVEL_COMBINATORS.isdisjoint(schema), name
        assert _untyped(schema, "$") == [], name


@pytest.mark.parametrize(
    "structured",
    [
        {"passes": False, "findings": []},
        {"passes": True, "findings": [{"criterion": "the route works", "why": "it is vague"}]},
    ],
    ids=["refuses-nothing", "passes-with-a-finding"],
)
def test_an_answer_the_schema_admits_is_no_verdict_when_its_outcome_and_findings_disagree(
    structured: dict[str, object],
) -> None:
    """A finding is a refused criterion, so the two halves are one statement — held here.

    The schema cannot say it: the top-level combinator that would is refused by the
    Claude validators, so both answers below validate against it and `_answered` is
    what refuses them. A refusal admitting no finding would let a reviewer stop this
    content with nothing naming what to correct, and a pass admitting one would clear a
    task whose own reviewer refused criteria of it.
    """
    jsonschema.Draft7Validator(_verdict_schema()).validate(structured)

    assert plan_review._answered(structured) is None


def test_the_schema_refuses_a_finding_that_names_nothing() -> None:
    """A finding naming nothing leaves its reader where a refusal naming none does.

    Both keywords are held, at the strength `_answered` reads them back at: `minLength`
    alone admits a run of spaces, which names nothing while satisfying a length, and a
    schema that admitted one would send a reviewer an answer oneharness validates and
    this package then discards with nothing said about why.
    """
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


def test_the_edit_bar_covers_the_plan_bar_and_the_frame_a_live_edit_is_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live edit's judged bar is the plan bar plus what a live edit is told about itself.

    Distinct from `bar_fingerprint` so that rewording the frame moves every live-edit
    record and no plan record — a plan task was never shown it — and covering it so that
    a reworded frame is a moved bar rather than a pass over a question nobody asked. All
    three halves are read off the digests: it differs from the plan bar, it moves when
    the plan bar moves, and it moves when the frame does.
    """
    before = plan_review.edit_bar_fingerprint()
    assert before != plan_review.bar_fingerprint()

    monkeypatch.setattr(plan_review, "REVIEW_PROMPT", f"{plan_review.REVIEW_PROMPT}\nMore.\n")
    assert plan_review.edit_bar_fingerprint() != before
    monkeypatch.undo()

    monkeypatch.setattr(plan_review, "LIVE_EDIT_FRAME", f"{plan_review.LIVE_EDIT_FRAME}\nMore.\n")
    assert plan_review.edit_bar_fingerprint() != before
    assert plan_review.bar_fingerprint() == plan_review.bar_fingerprint(REPO_ROOT), (
        "moving the live-edit frame moved the plan bar, so every plan record would fall"
    )


def test_a_live_edits_prompt_frames_the_task_as_a_live_edits_and_shows_its_persona() -> None:
    """The reviewer is shown the bar, the frame for what the text is, and the text.

    A whole task is framed as a live edit's and shown its persona, and named the way its
    refusal names it, so a finding and the refusal that carries it are about the same
    thing.
    """
    bar = (REPO_ROOT / plan_review.BAR_FILES[0]).read_text(encoding="utf-8")

    whole = plan_review.edit_prompt("## What\n\nDo it.", "engineer", "the task added as node 'x'")
    assert whole.startswith(plan_review.REVIEW_PROMPT), whole
    assert plan_review.LIVE_EDIT_FRAME in whole
    assert bar in whole
    # The two facts the pin-path question turns on sit beside the bar here too, because
    # the prompt says they do and the bar digests them; the frame says what a live edit
    # cannot show beside them.
    assert host_installs.rendered() in whole
    assert plan_review.host_repository() in whole
    assert "no repository and no adoption fields to show you" in whole
    # A live edit shows no repository and no steps, so no lifecycle question reaches it.
    assert plan_review.LIFECYCLE_NO_DIFF_QUESTION not in whole
    assert '"stated_as": "the task added as node \'x\'"' in whole, whole
    assert '"persona": "engineer"' in whole, whole
    assert whole.rstrip().endswith("## What\n\nDo it."), whole


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


#: What a review key hashed to for a task stating no cross-DAG dependency, taken before
#: :func:`orchestrator.plan_store.authored_deps` existed. Pinned rather than recomputed,
#: because a key covers the bar's fingerprint and that fingerprint deliberately does not
#: cover the module computing the key — so nothing else in this repository fails when
#: these digests move, and what moving them costs is every review record standing on the
#: board at once, each plan refused until somebody spends a turn re-reviewing it.
KEYS_BEFORE_CROSS_DAG_DEPS = {
    "no dependencies at all": (
        (),
        "6ac4e487bc3c04cc7b4401eb6c61185b24e1353c62ae4ebdfe49d39b466ed863",
    ),
    "this project's own edges": (
        ("design", "approve"),
        "a9b780b400a22f0de497619cc98753bfc59534d772b01c5efda56d6cc3083337",
    ),
}


@pytest.mark.parametrize(
    ("deps", "before"), KEYS_BEFORE_CROSS_DAG_DEPS.values(), ids=KEYS_BEFORE_CROSS_DAG_DEPS
)
def test_a_task_stating_no_cross_dag_dependency_keys_as_it_always_did(
    deps: tuple[str, ...], before: str
) -> None:
    """The blast radius of reading a second place for dependencies: none.

    A record found by its key is one nobody has to review again, so widening what the
    key covers is a change every recorded review on this host's board is exposed to.
    These two digests are what the key answered before it read
    :data:`~orchestrator.plan_store.CROSS_DAG_DEPS` at all, and they still answer —
    which is the whole of why the union is a union rather than a field beside `deps`.

    Driven through `unreviewed` as well as through the hash, because that is the reader
    `just check-plan` and `just copy-plan` both ask: a record written under the old
    digest has to be *found*, not merely to hash alike.
    """
    task = _task(deps=deps)

    assert plan_review.review_key(task, BAR) == before
    assert plan_review.unreviewed([_recorded(task, before)], BAR) == []


def test_one_task_stating_a_cross_dag_dependency_keys_the_same_from_either_reader() -> None:
    """The store's two halves and the loaded plan's one list are one key.

    `just review-plan` keys a record read out of the store, which keeps this project's
    own edges apart from the cross-DAG references beside them; `just check-plan` keys a
    task rebuilt from the loaded plan, whose `deps` the engine has already resolved into
    one list. The recipes are driven over a real store in
    `tests/plan_tooling/test_check_plan_recipe_e2e.py`; what is asserted here is the
    property that made them disagree, over each shape the two readers really hand in.
    """
    metadata = _metadata(deps=["run:r-upstream#adopt"])
    from_the_store = _task(deps=("design",), metadata=metadata)
    from_the_loaded_plan = _task(deps=("design", "run:r-upstream#adopt"), metadata=metadata)

    assert plan_review.review_key(from_the_store, BAR) == plan_review.review_key(
        from_the_loaded_plan, BAR
    )
    assert plan_store.authored_deps(from_the_store) == ["design", "run:r-upstream#adopt"]


@pytest.mark.parametrize(
    "stated",
    ["run:r-upstream#adopt", {"run": "adopt"}, [7], None],
    ids=["a string", "a mapping", "a non-string entry", "absent"],
)
def test_a_cross_dag_statement_this_cannot_read_leaves_the_key_where_it_was(
    stated: object,
) -> None:
    """A `deps` the engine's loader refuses keys nothing, rather than keying a shape.

    The loader rules on this long before either reader sees the plan, so what a record
    under any of these would key is a plan no launch accepts — and inventing a rendering
    for one would put a second answer to `deps` into the digest.
    """
    metadata = _metadata(deps=stated) if stated is not None else _metadata()

    assert plan_review.review_key(_task(metadata=metadata), BAR) == plan_review.review_key(
        _task(metadata=_metadata()), BAR
    )


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


#: The plan the store answers for every project here, in the engine's loaded shape:
#: a goal and no nodes, which is enough for a plan-level key to be computed and written.
PLAN = {"name": "P", "goal": {"text": "Deliver the route"}, "tasks": []}


class _Store:
    """A local Markdown source on disk, with the store answers a review reads it through.

    Every project a task names gets a project record beside `plan.md`, because the
    plan-level review is written into the project's own document and a project with no
    record is one the closeout passes over.
    """

    def __init__(self, root: Path, tasks: Sequence[StoreTask]) -> None:
        self.root = root
        self.tasks = list(tasks)
        (root / "projects").mkdir(parents=True, exist_ok=True)
        for project in {
            "plan",
            *(task.qualified_id.partition(":")[2].split("/")[0] for task in tasks),
        }:
            document = root / "projects" / f"{project}.md"
            if not document.exists():
                document.write_text('---\ntitle: "P"\n---\n', encoding="utf-8")
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
        monkeypatch.setenv("ONETASKGRAPH_SOURCES__DEMO__PLUGIN", "local-md")
        monkeypatch.setenv("ONETASKGRAPH_SOURCES__DEMO__CONFIG__ROOT", str(self.root))
        monkeypatch.setattr(plan_store, "read_tasks", self._read)
        monkeypatch.setattr(plan_store, "read_plan", lambda project, records: dict(PLAN))
        self._store_record = plan_store.project_record
        monkeypatch.setattr(plan_store, "project_record", self._project)

    def _project(self, project: str) -> dict[str, object]:
        """The project record as the store would report it: its metadata, read off disk.

        The plan-level budget answers are the store's own answer, for a project a test
        stated them on through the pinned store; the review record is read off the file.
        """
        _, _, native = project.partition(":")
        record = self.written(native, project=True)
        metadata: dict[str, object] = {} if record is None else {plan_review.RECORD_KEY: record}
        answers = self._store_record(project).get("metadata")
        if isinstance(answers, dict) and plan_budgets.PLAN_RECORD in answers:
            metadata[plan_budgets.PLAN_RECORD] = answers[plan_budgets.PLAN_RECORD]
        return {"metadata": metadata}

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

    def written(self, native: str, *, project: bool = False) -> object:
        """The review record one task — or, with ``project``, one project — carries."""
        if project:
            document = self.root / "projects" / f"{native}.md"
        else:
            document = self.root / "tasks" / native.split("/")[0] / f"{native.split('/')[1]}.md"
        for line in document.read_text(encoding="utf-8").splitlines():
            if f'"{plan_review.RECORD_KEY}"' in line:
                return json.loads(line.split(": ", 1)[1])
        return None


def _verdicts(monkeypatch: pytest.MonkeyPatch, *answers: plan_review.Verdict) -> list[str]:
    """Stand the judged turn in at the one boundary a verdict crosses into this module."""
    given = list(answers)
    seen: list[str] = []

    def verdict(prompt: str, *_: object) -> plan_review.Verdict:
        seen.append(prompt)
        return given.pop(0)

    monkeypatch.setattr(plan_review, "verdict", verdict)
    return seen


def test_a_passing_review_records_the_key_the_check_will_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    prompts = _verdicts(monkeypatch, PASSES, PASSES)

    assert plan_review.main(["demo:plan"]) == 0
    written = store.written("plan/route")
    assert isinstance(written, dict)
    assert written["key"] == plan_review.review_key(_task(), BAR)
    assert written["by"] == plan_review.BY_REVIEW
    assert "The review bar" in prompts[0]
    assert "Add the route." in prompts[0]
    # And the plan-level turn, spent once every task carried a record, on the project.
    whole = store.written("plan", project=True)
    assert isinstance(whole, dict)
    assert whole["key"] == plan_review.plan_key(PLAN, plan_review.plan_bar_fingerprint())
    assert whole["by"] == plan_review.BY_REVIEW
    assert prompts[1].startswith(plan_review.PLAN_REVIEW_PROMPT), prompts[1]
    assert "Deliver the route" in prompts[1]
    assert len(prompts) == 2


def test_a_plan_level_refusal_records_nothing_for_the_plan_and_shows_every_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The task's own record stands; the plan's is not written; both findings reach stderr.

    A finding names a node id or `the plan`, and the operator is told which reading
    each is — an omission, or a node declaring its adoption wrongly.
    """
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    refuses_the_plan = plan_review.Verdict(
        passes=False,
        findings=[
            plan_review.Finding(
                criterion=plan_review.THE_PLAN,
                why="no node of this repository adopts the engine release the fix lands in",
            ),
            plan_review.Finding(
                criterion="route", why="it names `config/onevcs.version` for a crate fix"
            ),
        ],
    )
    _verdicts(monkeypatch, PASSES, refuses_the_plan)

    assert plan_review.main(["demo:plan"]) == 1
    assert isinstance(store.written("plan/route"), dict), "the task's own pass was lost"
    assert store.written("plan", project=True) is None, "a refusal recorded a plan-level pass"
    reported = capsys.readouterr().err
    for finding in refuses_the_plan["findings"]:
        assert f"review-plan: {finding['criterion']} — {finding['why']}" in reported, reported
    assert "the plan as a whole was refused on 2 finding(s)" in reported, reported
    assert "no plan-level record was written" in reported, reported


def test_no_plan_level_turn_is_spent_while_a_task_is_refused_or_unreviewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The plan whole is read only once every task carries a record.

    A refused task is one its author is about to change, so a plan-level verdict over
    it would be a verdict over a plan that will not exist; and the operator is told the
    turn is owed rather than left to wonder whether it was spent.
    """
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    prompts = _verdicts(monkeypatch, REFUSES)

    assert plan_review.main(["demo:plan"]) == 1
    assert len(prompts) == 1, "a plan-level turn was spent beside a refused task"
    assert store.written("plan", project=True) is None
    assert "spent only once every task carries a record" in capsys.readouterr().err


def test_a_plan_level_turn_that_answers_nothing_leaves_the_task_records_standing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stop is reported as a stop, and the task passes already written are kept."""
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    answers = [PASSES]

    def verdict(prompt: str, *_: object) -> plan_review.Verdict:
        if answers:
            return answers.pop(0)
        raise OSError("the chain answered nothing")

    monkeypatch.setattr(plan_review, "verdict", verdict)

    assert plan_review.main(["demo:plan"]) == 2
    reported = capsys.readouterr().err
    assert "the chain answered nothing" in reported, reported
    assert "The plan-level review was left unrecorded" in reported, reported
    assert isinstance(store.written("plan/route"), dict)
    assert store.written("plan", project=True) is None


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
    """Neither a recorded task nor a recorded plan spends a turn, and each is authoritative
    on its own: a plan whose tasks all carry records still owes the one plan-level turn
    until the project carries its record too."""
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    current = _recorded(_task(), plan_review.review_key(_task(), BAR))
    store = _Store(tmp_path / "store", [current])
    store.install(monkeypatch)
    prompts = _verdicts(monkeypatch, PASSES)

    assert plan_review.main(["demo:plan"]) == 0
    assert len(prompts) == 1, "a recorded task was re-judged, or the plan whole was not"
    assert prompts[0].startswith(plan_review.PLAN_REVIEW_PROMPT)
    reported = capsys.readouterr().out
    assert "1 already carried one" in reported
    assert "reviewed and recorded on the project" in reported

    prompts = _verdicts(monkeypatch)
    assert plan_review.main(["demo:plan"]) == 0
    assert prompts == [], "a recorded plan-level pass was re-judged rather than replayed"
    assert "already carried a record for its current content" in capsys.readouterr().out


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

    def verdict(prompt: str, *_: object) -> plan_review.Verdict:
        if answers:
            return answers.pop(0)
        raise OSError("the chain answered nothing")

    monkeypatch.setattr(plan_review, "verdict", verdict)

    assert plan_review.main(["demo:plan"]) == 2
    reported = capsys.readouterr().err
    assert "the chain answered nothing" in reported, reported
    assert "1 task(s) were reviewed and recorded before that" in reported, reported
    assert "beginning at second" in reported, reported
    assert isinstance(store.written("plan/first"), dict)
    assert store.written("plan/second") is None


def _configured(monkeypatch: pytest.MonkeyPatch, plugin: str, root: str = "/tmp/store") -> None:
    """Answer `config show` for one source, so a writability read reaches no real store."""
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__DEMO__PLUGIN", plugin)
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__DEMO__CONFIG__ROOT", root)


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


def _harness(monkeypatch: pytest.MonkeyPatch, stdout: str, stderr: str = "") -> list[list[str]]:
    """Stand the `oneharness run` process in with one report and one diagnostic stream.

    Returns the argv of every spawn, so a test can read what the turn asked for.
    """
    completed = subprocess.CompletedProcess(["oneharness"], 0, stdout, stderr)
    spawned: list[list[str]] = []

    def run(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        spawned.append(list(command))
        return completed

    monkeypatch.setattr(plan_review.subprocess, "run", run)
    return spawned


def test_the_judged_turn_asks_the_harness_for_its_json_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spawn names `--format json`, because the CLI's stdout is prose without it.

    `verdict` reads the harness's stdout as one JSON document. Since the oneharness
    release that flipped the CLI's stdout default to text, that document is printed
    only when a reader asks — bare `oneharness run` prints a human-readable view — so
    an argv naming neither `--format json` nor `--compact` reads a text view and
    refuses every review as unreadable. Held on the argv the process is spawned with,
    immediately followed by the value, rather than on the constant it is assembled
    from; `tests/plan_tooling/test_plan_review_e2e.py` drives the same spawn through
    the real CLI.
    """
    answered = {"schema_valid": True, "structured": {"passes": True, "findings": []}}
    spawned = _harness(monkeypatch, json.dumps({"results": [answered]}))
    plan_review.verdict("prompt")
    [argv] = spawned
    assert argv[:2] == ["oneharness", "run"]
    assert argv[-2:] == ["--prompt-file", "-"], argv
    assert "--compact" not in argv, argv
    formats = [index for index, word in enumerate(argv) if word == "--format"]
    assert len(formats) == 1, argv
    assert argv[formats[0] + 1] == "json", argv


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
    assert plan_review.verdict("prompt") == sound


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
        plan_review.verdict("prompt")


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
        "cross-DAG deps": "run:r-sentinel#sentinel-cross-dag-dependency",
        "repo": "github.com/nickderobertis/sentinel-repository",
        "adoption": "sentinel-adoption",
        "consumes": "sentinel-consumed-target",
        "merge_policy": "sentinel-merge-policy",
        "step id": "sentinel-step-id",
        "step prose": "sentinel-step-task",
        "step persona": "sentinel-step-persona",
    }
    unkeyed = {
        "execution checkout": "sentinel-execution-checkout",
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
            "onepipeline.adoption": sentinels["adoption"],
            "onepipeline.consumes": {"engine": sentinels["consumes"]},
            "onepipeline.merge_policy": sentinels["merge_policy"],
            "onepipeline.deps": [sentinels["cross-DAG deps"]],
            "onepipeline.execution_checkout": unkeyed["execution checkout"],
            "onepipeline.steps": [
                {
                    "id": sentinels["step id"],
                    "persona": sentinels["step persona"],
                    "task": sentinels["step prose"],
                    "branch": unkeyed["a step field no author wrote"],
                }
            ],
        },
        repositories=[sentinels["repo"]],
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
    # And the two facts the pin-path question turns on sit beside the bar, because both
    # are hashed into it: the table, and this host's own repository as the origin the
    # header's `repo` is compared with.
    assert host_installs.rendered() in composed, composed
    assert f"`{plan_review.host_repository()}` — the origin the task's `repo`" in composed


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
    recorded = plan_review.record_projects_new_since(before)
    assert recorded.written == ["demo:authored/route"]
    written = store.written("authored/route")
    assert isinstance(written, dict)
    assert written["by"] == plan_review.BY_PLANNING
    assert written["key"] == plan_review.review_key(authored, BAR)
    assert store.written("plan/existing") is None
    # And the plan whole, beside its tasks, because the planner's own judge read it whole.
    assert recorded.plans == ["demo:authored"]
    whole = store.written("authored", project=True)
    assert isinstance(whole, dict)
    assert whole["by"] == plan_review.BY_PLANNING
    assert whole["key"] == plan_review.plan_key(PLAN, plan_review.plan_bar_fingerprint())
    assert store.written("plan", project=True) is None


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
    # A task the store created is named by the source, not under its project's directory,
    # so what it was read as belonging to is what decides — and it belongs to another.
    filed_elsewhere = replace(_task(qualified_id="demo:route"), project="other")
    with pytest.raises(OSError, match="is not one of"):
        plan_review.write_record("demo:plan", filed_elsewhere, "abc", plan_review.BY_REVIEW)
    assert store.written("plan/route") is None


def test_a_review_is_recorded_on_a_task_the_store_created_and_refused_for_another_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A created task's id is the source's, so its own project decides where it is recorded.

    Run against a real `local-md` source through the real plan store: the task is created
    by `onetaskgraph task create`, which files it flat under `tasks/`, and read back the
    way `just review-plan` reads a project.
    """
    root = tmp_path / "source"
    for project in ("plan", "other"):
        (root / "projects").mkdir(parents=True, exist_ok=True)
        (root / "projects" / f"{project}.md").write_text(
            f'---\ntitle: "{project}"\nstatus: "todo"\n---\n', encoding="utf-8"
        )
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__REVIEWED__PLUGIN", "local-md")
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__REVIEWED__CONFIG__ROOT", str(root))
    body = tmp_path / "body.md"
    body.write_text("## What\n\nAdd the route.\n", encoding="utf-8")
    created = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            "task",
            "create",
            "reviewed",
            "--project",
            "other",
            "--title",
            "feat: add the route",
            "--body-file",
            str(body),
            "--metadata",
            'onepipeline.id="route"',
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    (task,) = plan_store.read_tasks("reviewed:other")

    with pytest.raises(OSError, match="is not one of"):
        plan_review.write_record("reviewed:plan", task, "abc", plan_review.BY_REVIEW)
    written = plan_review.write_record("reviewed:other", task, "abc", plan_review.BY_REVIEW)

    (reread,) = plan_store.read_tasks("reviewed:other")
    record = reread.metadata[plan_review.RECORD_KEY]
    assert isinstance(record, dict) and record["key"] == "abc", record
    assert written.is_file()


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
    assert "1 task(s) and for 1 plan(s) whole" in capsys.readouterr().err
    assert isinstance(store.written("authored/route"), dict)
    assert isinstance(store.written("authored", project=True), dict)


def test_a_project_a_closeout_cannot_record_is_left_alone_rather_than_failing_the_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A neighbour's plan in the window may not kill this planning run.

    A closeout cannot tell its own run's output from a concurrent one's, so every plan
    that appeared in its window is one it may meet. Raising on an unreadable neighbour
    would exit a launch non-zero over an unrelated plan, so the project is passed over
    and named, and the run this closeout belongs to still records what it authored.
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
    # the plan store's own rendering, which the SDK's metadata verb preserves.
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
    assert "source returned data this interface cannot represent" in reported, reported
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


#: One budget a node of the plan owns, and the plan-level answers naming it.
OWNED_BUDGET: dict[str, object] = {
    "id": "route-latency",
    "name": "Route response time",
    "basis": "measured",
    "repository": "github.com/acme/app",
    "file": "apps/api/budgets.yaml",
    "file_change": "add",
    "measure": "time to the route's response at the client",
    "inner_measure_reason": "",
    "unit": "ms",
    "direction": "max",
    "threshold": 250,
    "workload": "sentinel-workload: 500 requests a minute",
    "evidence": "spike-route measured 90 ms",
    "command": "bun run measure:route",
}
BUDGET_ANSWERS: dict[str, object] = {
    "overview": "Deliver the route.",
    "sizing": "500 requests a minute.",
    "workload": "sentinel-plan-workload: 500 requests a minute at peak.",
    "ten_x_summary": "The route slows first.",
    "ten_x": "sentinel-ten-x: the route slows first, and route-latency covers it.",
    "checklist": [
        {"concern": "latency", "budget": "route-latency", "not_applicable": "", "summary": ""}
    ],
    "repo_wide_effects": [
        {"repository": "github.com/acme/app", "budget": "", "effect": "none", "summary": ""}
    ],
    "realistic_data": [],
    "spike_findings": [],
}

#: One moved value per answer, each still of its declared shape.
MOVED_ANSWERS: dict[str, object] = {
    "overview": "Deliver another route.",
    "sizing": "10 requests a minute.",
    "workload": "10 requests a minute.",
    "ten_x_summary": "Nothing slows.",
    "ten_x": "Nothing slows.",
    "checklist": [
        {"concern": "spend", "budget": "", "not_applicable": "n/a because free", "summary": "Free."}
    ],
    "repo_wide_effects": [
        {"repository": "github.com/acme/app", "budget": "", "effect": "+1 s", "summary": "1 s."}
    ],
    "realistic_data": [
        {"data": "d", "choice": "fixture", "reason": "r", "artifact": "a", "summary": "s"}
    ],
    "spike_findings": [{"spike": "s", "finding": "f", "changed": "c", "summary": "s"}],
}


def _owning(task: StoreTask, budgets: object = None) -> StoreTask:
    """``task`` carrying ``budgets`` — the one budget above by default — as its record."""
    held = [OWNED_BUDGET] if budgets is None else budgets
    return dataclasses.replace(task, metadata={**task.metadata, plan_budgets.TASK_RECORD: held})


def test_the_task_reviewer_is_asked_that_a_node_owning_a_budget_names_it_and_its_workload() -> None:
    asked = " ".join(plan_review.REVIEW_PROMPT.split())
    for sentence in (
        "One question is asked of a node that owns a budget: one whose task carries a "
        "`## Budgets` section, its own budgets",
        "Such a node owns the budget's command",
        "its `## Acceptance criteria` name each budget it owns by its id and the realistic "
        "workload the budget holds at",
        "a criterion asking the worker to have met a repo-wide budget's threshold is refused",
        "A node owning no budget is not asked this.",
    ):
        assert sentence in asked, sentence
    composed = plan_review._prompt("P", _owning(_task()))
    assert composed.startswith(plan_review.REVIEW_PROMPT)
    heading = "## The budgets this node owns, as its `orchestrator.budgets` record states them"
    assert heading in composed
    assert "route-latency" in composed and "sentinel-workload" in composed
    assert plan_review._prompt("P", _task()).endswith(f"{heading}\n\n(none)\n")


def test_a_task_key_covers_the_budgets_it_owns_and_nothing_when_it_owns_none() -> None:
    task = _task()
    unowned = plan_review.review_key(task, BAR)

    owned = plan_review.review_key(_owning(task), BAR)
    assert owned != unowned
    for key, value in (("workload", "10 a minute"), ("basis", "estimate"), ("name", "Other")):
        moved = plan_review.review_key(_owning(task, [{**OWNED_BUDGET, key: value}]), BAR)
        assert moved != owned, key
    granted = _recorded(_owning(task), owned)
    assert plan_review.unreviewed([granted], BAR) == []
    relieved = dataclasses.replace(granted, metadata=_recorded(task, owned).metadata)
    assert plan_review.unreviewed([relieved], BAR) == [relieved], "a dropped budget is new"


def test_the_plan_reviewer_is_asked_what_the_plan_is_missing_from_its_budgets() -> None:
    asked = " ".join(plan_review.PLAN_REVIEW_PROMPT.split())
    for question in (
        "On the budgets, ask what the plan is **missing**, not only whether each answer was "
        "filled in",
        "a concern the stated workload makes likely that the checklist dismissed or never lists",
        "a 10x answer no budget covers",
        "a budget with no command to check it",
        "a basis the evidence does not support",
        "a measure taken further inward than where the product owner feels the impact with no "
        "reason given",
        "a repo-wide budget — one in a repository's root `budgets.yaml` — written into a task's "
        "criteria",
        "a change to a budgets file the budgets imply that no budget states",
        "a target the evidence shows is infeasible, quietly loosened rather than escalated",
        "A plan that carries no plan-level answers is not asked this.",
    ):
        assert question in asked, question


def test_the_plan_reviewer_is_shown_the_plans_answers_and_every_tasks_budgets_and_keys_them() -> (
    None
):
    """Shown iff keyed, one level up: the plan's own answers and each task's budgets."""
    plan = {"goal": {"text": "Deliver the route"}, "tasks": [{"id": "route"}]}
    owned = {"route": [OWNED_BUDGET]}
    composed = plan_review._plan_prompt(plan, BUDGET_ANSWERS, owned)

    assert "## The plan's budgets" in composed
    assert "rendered whole as its description at `(the store reported no path for it)`" in (
        composed
    )
    for sentinel in ("sentinel-plan-workload", "sentinel-ten-x", "sentinel-workload"):
        assert sentinel in composed, sentinel
    assert "(this plan carries no plan-level budget answers)" in plan_review._plan_prompt(plan)
    assert "(no task owns a budget)" in plan_review._plan_prompt(plan)
    located = plan_review._plan_prompt(
        plan, BUDGET_ANSWERS, owned, plan_review.PlanView(description="/p.md")
    )
    assert "rendered whole as its description at `/p.md`" in located
    # Read off the plan's own metadata when no records are handed in, as the check path does.
    carried = {
        "goal": plan["goal"],
        "tasks": [{"id": "route", "metadata": {plan_budgets.TASK_RECORD: [OWNED_BUDGET]}}],
    }
    assert "sentinel-workload" in plan_review._plan_prompt(carried, BUDGET_ANSWERS)

    keyed = plan_review.plan_key(plan, BAR, BUDGET_ANSWERS, owned)
    assert keyed != plan_review.plan_key(plan, BAR, None, owned)
    assert keyed != plan_review.plan_key(plan, BAR, BUDGET_ANSWERS)
    for answer in BUDGET_ANSWERS:
        moved = {**BUDGET_ANSWERS, answer: MOVED_ANSWERS[answer]}
        assert plan_review.plan_key(plan, BAR, moved, owned) != keyed, answer
    for key in OWNED_BUDGET:
        changed = {"route": [{**OWNED_BUDGET, key: "moved"}]}
        assert plan_review.plan_key(plan, BAR, BUDGET_ANSWERS, changed) != keyed, key
    record = {"metadata": {plan_budgets.PLAN_RECORD: BUDGET_ANSWERS}}
    assert plan_review.plan_answers(record) == BUDGET_ANSWERS
    assert plan_review.plan_answers({"metadata": "not a map"}) is None


def test_the_review_bar_holds_the_budget_question() -> None:
    bar = " ".join(
        yaml.safe_load((REPO_ROOT / plan_review.BAR_FILES[0]).read_text(encoding="utf-8"))["user"][
            "persona"
        ].split()
    )
    for demand in (
        "Hold it to six things",
        "**Its budgets are what its work needs.**",
        "The plan's own answers state a realistic workload with numbers",
        "answers every concern of the checklist with a budget or a one-line reason it needs none",
        "Look for what is missing rather than whether each line was filled in",
        "a basis the evidence does not support",
        "a repo-wide budget written into a task's criteria",
        "a requested target quietly loosened rather than escalated",
        "A node that owns a budget names that budget and its workload in its criteria.",
    ):
        assert demand in bar, demand


# llmlint: ignore-block[shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker] The
# review reads the plan's answers and each task's budgets through the plan store, so the store
# is what this drives: the `_Store` every review test here already writes into, the
# description rendered by the pinned tools themselves. It is not a host tool: the store is the
# pinned install `uv.lock` names and `templates/` is in `codeWorkspace`, so both are in the key
# this tier is memoized on, and the module under test is this project's. Only the judged turn
# is stood in for.
def test_a_review_reads_the_budgets_from_the_store_and_records_keys_over_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real store: the records are read back, the judge doubled.

    Each node's prompt shows the budgets it owns and the plan's shows the plan's answers and
    every task's budgets, and the records written are the keys `just check-plan` then finds
    — so a plan reviewed with its budgets is reviewed, and an answer moving after is a plan
    nobody reviewed.
    """
    store = _Store(tmp_path / "store", [_owning(_task())])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    budgeted("demo", "plan", BUDGET_ANSWERS)
    prompts = _verdicts(monkeypatch, PASSES, PASSES)

    assert plan_review.main(["demo:plan"]) == 0

    assert plan_budgets.plan_record("demo:plan") == BUDGET_ANSWERS
    assert "sentinel-workload" in prompts[0], prompts[0]
    assert "sentinel-plan-workload" in prompts[1] and "sentinel-ten-x" in prompts[1]
    assert "sentinel-workload" in prompts[1]
    written = store.written("plan/route")
    assert isinstance(written, dict)
    assert written["key"] == plan_review.review_key(_owning(_task()), BAR)
    whole = store.written("plan", project=True)
    assert isinstance(whole, dict)
    owned = {"route": [OWNED_BUDGET]}
    assert whole["key"] == plan_review.plan_key(
        PLAN, plan_review.plan_bar_fingerprint(), BUDGET_ANSWERS, owned
    )
    assert not plan_review.plan_unreviewed(
        plan_store.project_record("demo:plan"), PLAN, owned=owned
    )

    moved = {**BUDGET_ANSWERS, "ten_x": "Something else slows first."}
    budgeted("demo", "plan", moved)
    assert plan_review.plan_unreviewed(plan_store.project_record("demo:plan"), PLAN, owned=owned), (
        "a plan-level answer changed after the review still read as reviewed"
    )


def test_a_review_excerpts_long_budget_prose_only_in_the_plan_level_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entrypoint keeps owned detail whole while bounding the plan overview."""
    prose = {
        field: f"{field}: " + "measured workload and evidence " * 20
        for field in plan_review.EXCERPTED_BUDGET_FIELDS
    }
    budget = {**OWNED_BUDGET, **prose}
    store = _Store(tmp_path / "store", [_owning(_task(), [budget])])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    budgeted("demo", "plan", BUDGET_ANSWERS)
    prompts = _verdicts(monkeypatch, PASSES, PASSES)

    assert plan_review.main(["demo:plan"]) == 0

    for field, value in prose.items():
        assert value in prompts[0], field
        assert value not in prompts[1], field
        excerpt = value[: plan_review.EXCERPT_LIMIT] + plan_review.EXCERPT_MARKER
        assert json.dumps(excerpt, ensure_ascii=False) in prompts[1], field
    whole = store.written("plan", project=True)
    assert isinstance(whole, dict)
    assert whole["key"] == plan_review.plan_key(
        PLAN, plan_review.plan_bar_fingerprint(), BUDGET_ANSWERS, {"route": [budget]}
    )


# llmlint: ignore-end[shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]


def test_the_planner_is_told_how_to_state_its_budgets_in_general_terms() -> None:
    """The planner's own prompt carries every budgets rule, naming no repository of this host.

    It travels into every repository planned against, so the section holds the general
    concepts — and the review bar above holds a plan to them.
    """
    prompt = yaml.safe_load((REPO_ROOT / plan_review.BAR_FILES[0]).read_text(encoding="utf-8"))[
        "system_prompt"
    ]
    section = prompt.split("## Budgets: the measurable requirements a plan states", 1)[1].split(
        "\n## ", 1
    )[0]
    stated = " ".join(section.split())
    for rule in (
        "**Workload first.**",
        "Those numbers reach the acceptance criteria",
        "latency; quota and rate-limit headroom; how the work scales with data; spend; resource "
        "use",
        'Every concern gets a budget or a full "n/a because …"',
        "*at 10× realistic usage, what does the product owner notice getting worse first?*",
        "a set of general problem classes, never a closed list",
        "grows a new class each time a real miss",
        "**Use the measure of record**",
        "Take a measure further inward only when the outer one cannot be checked, and say why",
        "the one at its root and every project's own",
        "Propose a change to any of them only through a budget of the task that owns it",
        "**A budget's command analyses telemetry the gate's tests already record**",
        "**A budget is a product-owner-level outcome**",
        "**Detailed figures are telemetry, not budgets**",
        "The node that implements a budget owns that command, the recording in the tests it "
        "reads, and the budget's registration",
        "its acceptance criteria name the budget and the realistic workload it holds at",
        "A feature budget goes in the budgets file of the project that owns what it measures",
        "Only a budget that must always be checked goes in the root file",
        "**Repo-wide budgets are enforced on the merge path** and never written into a task's "
        "criteria",
        "weigh realism (can real data be had at all), generation cost, fixture cost, and upkeep",
        "**A target the user asked for that the evidence says is infeasible is an escalated "
        "exception**",
        "never a quietly loosened number",
        "**Every plan states its budgets in two homes**",
        "`orchestrator.budgets`",
        "`orchestrator.plan-budgets`",
    ):
        assert rule in stated, rule
    for host_name in ("nickderobertis", "ai-orchestrator", "petsinc", "hellopatient"):
        assert host_name not in section, host_name


# The realistic plans the plan-level prompt is measured over.
#
# Both are 44 nodes and 52 budgets, the size of `authoring:create-repo-baseline-audit`,
# the plan whose 2,005,821-character prompt no candidate could answer. Measured on that
# plan: agent task bodies of about 23.5k authored characters each, then the operational
# notes; `kind: human` nodes of a few hundred; each budget's `measure` near 120
# characters, its `workload` near 1,040, its `evidence` from 260 to 780 and its
# `inner_measure_reason` near 360; and the document's own `workload` near 4,500. The
# budgets these two tests measure are registered in `orchestrator/budgets.yaml`, which is
# the one place their thresholds are stated.

#: The budgets file the two prompt-size budgets are registered in, and read from.
ORCHESTRATOR_BUDGETS = REPO_ROOT / "orchestrator" / "budgets.yaml"
#: Where a store of this shape keeps its task files, as the baseline plan's store does.
FIXTURE_STORE = "/home/operator/ai-orchestrator/.plans/tasks"
#: How many authored characters a realistic agent task body carries before its notes.
AUTHORED_CHARACTERS = 23_500
#: The words realistic prose is drawn from: this host's own planning vocabulary, so the
#: text reads as a task does rather than as filler a reviewer would skim differently.
VOCABULARY_TEXT = (
    "the repository's gate runs its targets through the project graph so a change to one "
    "package re-runs only what that package reaches; the worker adds the missing tier, "
    "keeps the cache key covering every file the tier reads, and proves the change with a "
    "journey that drives the real recipe rather than a stand-in for it; the published "
    "release carries the fix and the pin moves only once the registry reports it; a "
    "budget measured where the product owner feels the impact holds the change to its "
    "realistic workload"
)
VOCABULARY = tuple(VOCABULARY_TEXT.split())


def _prose(seed: str, characters: int) -> str:
    """Deterministic sentences of about ``characters`` characters, distinct per ``seed``."""
    words: list[str] = []
    length = 0
    position = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    while length < characters:
        word = VOCABULARY[position % len(VOCABULARY)]
        position = position // 7 + 1_000_003 * (len(words) + 1)
        words.append(word)
        length += len(word) + 1
    sentences = [" ".join(words[at : at + 18]) for at in range(0, len(words), 18)]
    return " ".join(sentence[0].upper() + sentence[1:] + "." for sentence in sentences)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _agent_body(node_id: str, lead: int = 420, grown: int = 1) -> str:
    """One agent task as the plan-task template renders it: authored sections, then notes.

    ``grown`` multiplies the authored text past the lead — later `## What` paragraphs,
    criteria and the task's own `## Additional info` — which is what a longer task grows.
    """
    later = AUTHORED_CHARACTERS * grown - lead
    paragraphs = "\n\n".join(
        _prose(f"{node_id}-what-{index}", later // 4 // 3) for index in range(3)
    )
    criteria = "\n".join(
        f"- {_prose(f'{node_id}-criterion-{index}', later // 2 // 12)}" for index in range(12)
    )
    notes = _prose(f"{node_id}-notes", later // 4)
    return (
        f"## What\n\n{_prose(f'{node_id}-lead', lead)}\n\n"
        f"{paragraphs} SENTINEL-LATER-{node_id}.\n\n"
        f"## Why\n\n{_prose(f'{node_id}-why', 400)}\n\n"
        f"## Acceptance criteria\n\n{criteria}\n- SENTINEL-CRITERION-{node_id}.\n\n"
        f"## Additional info\n\n{notes} SENTINEL-NOTES-{node_id}.\n\n"
        f"{(REPO_ROOT / plan_review.APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


def _human_body(node_id: str) -> str:
    return (
        f"{_prose(f'{node_id}-action', 340)}\n\n"
        f"{_prose(f'{node_id}-detail', 1_600)} SENTINEL-LATER-{node_id}.\n"
    )


#: The repositories the baseline plan spans: this host's own and twenty-six others.
REPOSITORIES = (
    "github.com/nickderobertis/ai-orchestrator",
    *(f"github.com/nickderobertis/repository-{index:02d}" for index in range(26)),
)


def _budget(index: int, repository: str) -> dict[str, object]:
    """One budget at the measured sizes of the baseline plan's prose fields."""
    return {
        "id": f"budget-{index:02d}",
        "name": f"Measured duration {index:02d}",
        "basis": "measured",
        "repository": repository,
        "file": "budgets.yaml",
        "file_change": "add",
        "measure": _prose(f"measure-{index}", 117),
        "inner_measure_reason": _prose(f"inner-{index}", 357),
        "unit": "seconds",
        "direction": "max",
        "threshold": 600 + index,
        "workload": _prose(f"workload-{index}", 1_037),
        "evidence": _prose(f"evidence-{index}", 258 + (index * 41) % 524),
        "command": f"just measure-{index:02d} --against main --report seconds",
    }


def _fixture_budgets(
    owners: Sequence[tuple[str, str]],
) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
    """The plan's own answers, and 52 budgets spread over ``owners`` at measured sizes.

    ``owners`` are (node, repository) pairs; the budgets are answered by owning node.
    """
    owned: dict[str, list[dict[str, object]]] = {}
    for index in range(52):
        node, repository = owners[index % len(owners)]
        owned.setdefault(node, []).append(_budget(index, repository))
    answers: dict[str, object] = {
        "overview": _prose("overview", 600),
        "sizing": _prose("sizing", 500),
        "workload": _prose("plan-workload", 4_500),
        "ten_x_summary": _prose("ten-x-summary", 200),
        "ten_x": _prose("ten-x", 780),
        "checklist": [
            {
                "concern": concern,
                "budget": f"budget-{index:02d}",
                "not_applicable": "",
                "summary": "",
            }
            for index, concern in enumerate(
                ("latency", "spend", "resource use", "gate time", "change cycle time")
            )
        ]
        + [
            {
                "concern": "quota and rate-limit headroom",
                "budget": "",
                "not_applicable": _prose("quota", 300),
                "summary": _prose("quota-summary", 140),
            }
        ],
        "repo_wide_effects": [
            {
                "repository": repository,
                "budget": "gate-time",
                "effect": _prose(repository, 80),
                "summary": _prose(f"{repository}-summary", 80),
            }
            for repository in REPOSITORIES
        ],
        "realistic_data": [],
        "spike_findings": [
            {
                "spike": f"spike-{index}",
                "finding": _prose(f"finding-{index}", 200),
                "changed": "x",
                "summary": _prose(f"finding-summary-{index}", 140),
            }
            for index in range(3)
        ],
    }
    return answers, owned


class RealisticPlan(NamedTuple):
    """One realistic plan, as the review reads it: the plan, its budgets, where things are."""

    plan: dict[str, object]
    answers: dict[str, object]
    owned: dict[str, list[dict[str, object]]]
    view: plan_review.PlanView


def _realistic(nodes: list[dict[str, object]]) -> RealisticPlan:
    owners = [(str(node["id"]), str(node["repo"])) for node in nodes if node.get("kind") != "human"]
    view = plan_review.PlanView(
        tasks={
            str(node["id"]): f"{FIXTURE_STORE}/{_slug(str(node['title']))}.md" for node in nodes
        },
        records={
            str(node["id"]): {
                "key": hashlib.sha256(str(node["id"]).encode()).hexdigest(),
                "by": "review-plan",
                "reviewed_at": "2026-10-06T09:26:10.363120+00:00",
            }
            for node in nodes
        },
        description="/home/operator/ai-orchestrator/.plans/projects/fixture.md",
    )
    plan = {"name": "fixture", "goal": {"text": _prose("goal", 900)}, "tasks": nodes}
    answers, owned = _fixture_budgets(owners)
    return RealisticPlan(plan, answers, owned, view)


def baseline_shaped(grown: int = 1) -> RealisticPlan:
    """The baseline plan's shape: 36 agent nodes, one of this host, and 8 human ones.

    One agent node's `## What` lead is longer than the summary's bound.
    """
    nodes: list[dict[str, object]] = []
    for index in range(36):
        node_id = f"repository-{index:02d}-baseline"
        nodes.append(
            {
                "id": node_id,
                "title": f"ci: bring repository {index:02d} up to the create-repo baseline",
                "repo": REPOSITORIES[0] if index == 0 else REPOSITORIES[1 + index % 26],
                "persona": "engineer",
                "deps": [] if index % 5 else [f"repository-{(index + 1) % 36:02d}-baseline"],
                "task": _agent_body(node_id, lead=900 if index == 3 else 420, grown=grown),
            }
        )
    for index in range(8):
        node_id = f"governance-{index}"
        nodes.append(
            {
                "id": node_id,
                "title": f"Turn on the required checks of repository {index:02d}'s main branch",
                "repo": REPOSITORIES[index + 1],
                "kind": "human",
                "deps": [f"repository-{index + 1:02d}-baseline"],
                "task": _human_body(node_id),
            }
        )
    return _realistic(nodes)


def host_shaped(grown: int = 1) -> RealisticPlan:
    """Every node of this host's repository: half `published`, a quarter `consumes`.

    Two nodes are stepped, one of them three steps long, and one carries a `## What` lead
    longer than the summary's bound.
    """
    nodes: list[dict[str, object]] = []
    for index in range(44):
        node_id = f"adopt-{index:02d}"
        node: dict[str, object] = {
            "id": node_id,
            "title": f"feat: adopt producer {index:02d}'s release and move the pin it governs",
            "repo": REPOSITORIES[0],
            "persona": "engineer",
            "deps": [f"adopt-{index - 1:02d}"] if index else [],
            "task": _agent_body(node_id, lead=1_100 if index == 7 else 420, grown=grown),
        }
        if index < 22:
            node["adoption"] = "published"
        elif index < 33:
            node["consumes"] = {f"producer-{index:02d}": "wheel"}
        if index in (40, 41):
            node["steps"] = [
                {
                    "id": f"step-{step}",
                    "persona": "engineer",
                    "task": _agent_body(f"{node_id}-{step}"),
                }
                for step in range(3 if index == 40 else 2)
            ]
        nodes.append(node)
    return _realistic(nodes)


REALISTIC = {"baseline-shaped": baseline_shaped, "host-shaped": host_shaped}


def _prompt_of(realistic: RealisticPlan) -> str:
    return plan_review._plan_prompt(
        realistic.plan, realistic.answers, realistic.owned, realistic.view
    )


def _node_costs(prompt: str) -> dict[str, int]:
    """What each node costs the prompt: its section and the separator before it."""
    body = prompt.split("## Every node of the plan, compactly\n\n", 1)[1]
    body = body.split("\n\n## What you may open", 1)[0]
    sections = body.split("\n\n### Node `")
    return {
        section.removeprefix("### Node `").split("`", 1)[0]: len(section) + len("\n\n### Node `")
        for section in sections
    }


def _threshold(identifier: str) -> float:
    """A budget's threshold, read from `orchestrator/budgets.yaml` — its one statement."""
    document = yaml.safe_load(ORCHESTRATOR_BUDGETS.read_text(encoding="utf-8"))
    (budget,) = [entry for entry in document["budgets"] if entry["id"] == identifier]
    threshold = budget["threshold"]
    assert isinstance(threshold, int | float)
    return threshold


def _report(value: int) -> None:
    """Hand onebudgetspec the measured value, when this test is a budget's command."""
    if destination := os.environ.get("ONEBUDGETSPEC_RESULT"):
        Path(destination).write_text(json.dumps({"value": value}), encoding="utf-8")


@pytest.mark.reads_docs
def test_the_plan_level_prompt_is_the_compact_view_of_a_realistic_plan() -> None:
    """Contract 2 over the baseline-shaped plan: every node alike, nothing past a summary."""
    realistic = baseline_shaped()
    prompt = _prompt_of(realistic)
    appendix = (REPO_ROOT / plan_review.APPENDIX).read_text(encoding="utf-8").strip()
    tasks = realistic.plan["tasks"]
    assert isinstance(tasks, list) and len(tasks) == 44
    assert sum(1 for node in tasks if node.get("kind") == "human") == 8
    assert sum(1 for node in tasks if node["repo"] == plan_review.host_repository()) == 1
    budgets = [budget for held in realistic.owned.values() for budget in held]
    assert len(budgets) == 52

    for node in tasks:
        node_id = node["id"]
        section = prompt.split(f"### Node `{node_id}`", 1)[1].split("\n\n### Node `", 1)[0]
        for field in plan_review.SHOWN_FIELDS:
            assert f'"{field}":' in section, (node_id, field)
        stored = realistic.view.records[node_id]
        assert isinstance(stored, dict) and stored["key"] in section, node_id
        assert f"task file: {realistic.view.tasks[node_id]}" in section, node_id
        summary = plan_review.summary(node["task"])
        assert f"summary: {summary}" in section, node_id
        assert len(summary.removesuffix(plan_review.SUMMARY_MARKER)) <= plan_review.SUMMARY_LIMIT
        for sentinel in ("LATER", "CRITERION", "NOTES"):
            assert f"SENTINEL-{sentinel}-{node_id}" not in prompt, (node_id, sentinel)
    # A lead past the bound is cut verbatim, and says so.
    assert "repository-03-baseline" in prompt
    assert plan_review.SUMMARY_MARKER in prompt
    # The operational notes are named once, never rendered.
    assert appendix.splitlines()[4] not in prompt, "a line of the notes' body was rendered"
    assert prompt.count(str(REPO_ROOT / plan_review.APPENDIX)) == 1
    # Each budget's long prose is a marked excerpt; every other answer is whole.
    for budget in budgets:
        for field in plan_review.EXCERPTED_BUDGET_FIELDS:
            value = budget[field]
            if len(value) > plan_review.EXCERPT_LIMIT:
                assert value not in prompt, (budget["id"], field)
                excerpt = value[: plan_review.EXCERPT_LIMIT] + plan_review.EXCERPT_MARKER
                assert json.dumps(excerpt, ensure_ascii=False) in prompt, (budget["id"], field)
            else:
                assert json.dumps(value, ensure_ascii=False) in prompt, (budget["id"], field)
        for field in ("id", "name", "basis", "repository", "file", "unit", "command"):
            assert json.dumps(budget[field], ensure_ascii=False) in prompt
    # And the plan's own: its three longest prose answers excerpted, every other whole.
    for answer in plan_review.EXCERPTED_PLAN_FIELDS:
        value = realistic.answers[answer]
        assert isinstance(value, str) and value not in prompt, answer
    for answer in ("sizing", "ten_x_summary", "checklist", "repo_wide_effects", "spike_findings"):
        assert (
            json.dumps(realistic.answers[answer], ensure_ascii=False, separators=(",", ":"))
            in prompt
        ), answer


@pytest.mark.reads_docs
def test_plan_level_prompt_size_holds_both_realistic_plans_under_its_budget() -> None:
    """Budget `plan-level-prompt-chars`: the larger realistic prompt, against its threshold.

    And the property that makes the budget hold for any plan this size: growing every
    task body of the host-shaped plan fourfold leaves its prompt exactly as long.
    """
    sizes = {name: len(_prompt_of(build())) for name, build in REALISTIC.items()}
    grown = len(_prompt_of(host_shaped(grown=4)))
    tasks = host_shaped(grown=4).plan["tasks"]
    assert isinstance(tasks, list)
    authored = min(len(node["task"]) for node in tasks)
    print(f"plan-level prompt characters: {sizes}; host-shaped grown fourfold: {grown}")
    assert authored > 4 * AUTHORED_CHARACTERS, authored
    assert grown == sizes["host-shaped"], f"a longer body changed the prompt: {grown} vs {sizes}"
    largest = max(sizes.values())
    _report(largest)
    threshold = _threshold("plan-level-prompt-chars")
    assert largest <= threshold, f"the plan-level prompt sizes {sizes} exceed {threshold}"


@pytest.mark.reads_docs
def test_plan_level_prompt_per_node_holds_every_realistic_node_under_its_budget() -> None:
    """Budget `plan-level-prompt-chars-per-node`: the costliest node of both plans.

    Including a node whose `## What` lead is past the summary's bound and a stepped node
    of three steps, which are the two shapes that cost the most.
    """
    costs = {
        f"{name}:{node}": cost
        for name, build in REALISTIC.items()
        for node, cost in _node_costs(_prompt_of(build())).items()
    }
    assert len(costs) == 88, len(costs)
    for heavy in ("baseline-shaped:repository-03-baseline", "host-shaped:adopt-07"):
        assert costs[heavy] > plan_review.SUMMARY_LIMIT, heavy
    stepped = host_shaped().plan["tasks"]
    assert isinstance(stepped, list) and len(stepped[40]["steps"]) == 3
    assert '"id":"step-2"' in _prompt_of(host_shaped())
    costliest = max(costs, key=costs.__getitem__)
    print(f"costliest node: {costliest} at {costs[costliest]} characters")
    _report(costs[costliest])
    threshold = _threshold("plan-level-prompt-chars-per-node")
    assert costs[costliest] <= threshold, f"{costliest} costs {costs[costliest]} > {threshold}"


#: The review keys the dispatch base's own `plan_review.py` (bab2b6ae, which carries the
#: corrected verdict schema) computed for these tasks under the bar below — taken by
#: running that tree's module, not this one. A record written then must read as current
#: now: this change moved the plan-level key and nothing a per-task record is keyed on.
BASE_BAR = plan_review.BarFingerprint("bar-fingerprint")
BASE_KEYS = {
    "plain": "5a189abfd3877086124ceb40552c81b513359e62159e2fb5829f15f17b7d7ae8",
    "adopting": "0cdc66b36242b3b7d59a6449c1b9277659e79ad9176045921dfb74e294db4f57",
    "stepped": "d92752dcc6ea622936b77b0463e0ff12230e565047448e496b89897e9b83417a",
}
BASE_TASKS = {
    "plain": StoreTask(
        "demo:plan/route",
        "route",
        "feat: add the route",
        "## What\n\nAdd the route.\n\n## Acceptance criteria\n\n- It rejects an invalid request.\n",
        {"onepipeline.id": "route", "onepipeline.persona": "engineer"},
        [],
        (),
    ),
    "adopting": StoreTask(
        "demo:plan/adopt",
        "adopt",
        "feat: adopt the release",
        "## What\n\nMove the pin.\n",
        {
            "onepipeline.id": "adopt",
            "onepipeline.persona": "engineer",
            "onepipeline.adoption": "published",
            "onepipeline.deps": ["run:r-1#x"],
        },
        ["github.com/nickderobertis/ai-orchestrator"],
        ("engine",),
    ),
    "stepped": StoreTask(
        "demo:plan/s",
        "s",
        "feat: stepped",
        "",
        {
            "onepipeline.id": "s",
            "onepipeline.kind": None,
            "onepipeline.steps": [
                {"id": "a", "persona": "engineer", "task": "Do a."},
                {"id": "b", "persona": "engineer", "task": "Do b."},
            ],
        },
        [],
        (),
    ),
}


def test_a_task_record_written_under_the_dispatch_base_still_reads_as_current() -> None:
    """A task owning no budget keys as it did: every record the base wrote stands."""
    recorded = [_recorded(task, BASE_KEYS[name]) for name, task in BASE_TASKS.items()]
    assert plan_review.unreviewed(recorded, BASE_BAR) == []


def test_the_plan_key_moves_with_a_budget_a_node_owns_through_its_review_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A node's owned budget reaches the plan key through the node's review key.

    Whether the node's record arrives in its own metadata — the check path — or is handed
    in by node id — the store path — the key is one. And a plan read twice keys twice alike.
    The task bar is varied through the seam the other tests here use for it,
    `bar_fingerprint`, since `plan_key` derives each review key from it.
    """
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BASE_BAR)
    plan = {"goal": {"text": "Deliver"}, "tasks": [{"id": "route", "task": "## What\n\nIt."}]}
    owned = {"route": [OWNED_BUDGET]}
    keyed = plan_review.plan_key(plan, BAR, BUDGET_ANSWERS, owned)
    assert keyed == plan_review.plan_key(json.loads(json.dumps(plan)), BAR, BUDGET_ANSWERS, owned)
    carried = {
        **plan,
        "tasks": [
            {
                "id": "route",
                "task": "## What\n\nIt.",
                "metadata": {
                    "onepipeline.id": "route",
                    "onepipeline.task": "## What\n\nIt.",
                    plan_budgets.TASK_RECORD: [OWNED_BUDGET],
                },
            }
        ],
    }
    (node,) = plan_review.plan_nodes(plan, owned)
    (in_metadata,) = plan_review.plan_nodes(carried)
    record = plan_review.task_record_of({"id": "route", "task": "## What\n\nIt."})
    assert node["review_key"] == plan_review.review_key(_owning(record), BASE_BAR)
    assert in_metadata["review_key"] == node["review_key"]
    moved = {"route": [{**OWNED_BUDGET, "evidence": "measured again"}]}
    assert plan_review.plan_key(plan, BAR, BUDGET_ANSWERS, moved) != keyed
    # A node the records say owns nothing keys as one owning nothing, whatever it carried.
    (bare,) = plan_review.plan_nodes(carried, {"route": None})
    assert bare["review_key"] == plan_review.review_key(record, BASE_BAR)
    # The task bar the review keys are computed under is part of the plan key too.
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: plan_review.BarFingerprint("x"))
    assert plan_review.plan_key(plan, BAR, BUDGET_ANSWERS, owned) != keyed


def test_the_size_guard_never_sits_below_the_budget_it_guards() -> None:
    """`PLAN_PROMPT_LIMIT` admits every plan the prompt-size budget admits.

    The budget's threshold is read from `orchestrator/budgets.yaml`, its one statement.
    """
    assert _threshold("plan-level-prompt-chars") <= plan_review.PLAN_PROMPT_LIMIT


def test_the_plan_level_turn_spawns_under_its_own_role_file_and_the_task_turns_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which config each turn is handed, at the one seam a turn crosses into a harness."""
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    handed: list[tuple[str, ...]] = []

    def verdict(prompt: str, command: Sequence[str] = plan_review.HARNESS_COMMAND, *_: object):
        handed.append(tuple(command))
        return PASSES

    monkeypatch.setattr(plan_review, "verdict", verdict)
    assert plan_review.main(["demo:plan"]) == 0
    assert handed == [plan_review.HARNESS_COMMAND, plan_review.HARNESS_WHOLE_COMMAND]
    assert str(REPO_ROOT / "oneharness.plan-review-whole.toml") in plan_review.HARNESS_WHOLE_COMMAND
    assert str(REPO_ROOT / "oneharness.plan-review.toml") in plan_review.HARNESS_COMMAND


def test_a_plan_level_prompt_over_the_limit_spends_no_turn_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The guard stops before the turn, through the stop path, naming both numbers."""
    store = _Store(tmp_path / "store", [_task()])
    store.install(monkeypatch)
    monkeypatch.setattr(plan_review, "bar_fingerprint", lambda *_: BAR)
    monkeypatch.setattr(plan_review, "PLAN_PROMPT_LIMIT", 100)
    prompts = _verdicts(monkeypatch, PASSES)

    assert plan_review.main(["demo:plan"]) == 2
    assert len(prompts) == 1, "a plan-level turn was spent over the limit"
    assert isinstance(store.written("plan/route"), dict), "the task's own pass was lost"
    assert store.written("plan", project=True) is None
    reported = capsys.readouterr().err
    assert "characters, over the 100-character limit" in reported, reported
    assert "no plan-level turn was spent" in reported, reported


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (None, "(this node states no body prose)"),
        ("  \n", "(this node states no body prose)"),
        ("## What\n\nThe lead.\n\nThe second.\n", "The lead."),
        ("Merge it once green.\n\nThen tell the user.", "Merge it once green."),
        ("## What\n\n## Why\n\nBecause.\n", "Because."),
        ("# Title\n\n## Why\n\n", "(this node states no paragraph outside its headings)"),
    ],
    ids=["none", "blank", "what", "human", "empty-what", "only-headings"],
)
def test_a_summary_is_the_first_authored_paragraph(body: object, expected: str) -> None:
    assert plan_review.summary(body) == expected


def test_a_summary_past_its_bound_is_cut_verbatim_and_marked() -> None:
    lead = "x" * (plan_review.SUMMARY_LIMIT + 1)
    assert plan_review.summary(f"## What\n\n{lead}\n") == (
        lead[: plan_review.SUMMARY_LIMIT] + plan_review.SUMMARY_MARKER
    )


def test_the_view_names_where_each_task_and_document_is(tmp_path: Path) -> None:
    """Paths read off the store's answer, and only those that are files here.

    A location that is a URL — a board item's — is not named as a file the reviewer may
    open; the design document is the one recording the design-doc template, beside any
    other rendering the project holds; and the plan's description is its project's file.
    """
    store = tmp_path / "store"
    for relative in (
        "tasks/route.md",
        "documents/plan-report.md",
        "documents/plan-design.md",
        "projects/plan.md",
    ):
        (store / relative).parent.mkdir(parents=True, exist_ok=True)
        (store / relative).write_text("held\n", encoding="utf-8")
    task = dataclasses.replace(_recorded(_task(), "k"), location=str(store / "tasks/route.md"))
    unlocated = dataclasses.replace(_task(node_id="other", qualified_id="demo:plan/other"))
    on_a_board = dataclasses.replace(
        _task(node_id="board", qualified_id="demo:plan/board"),
        location="https://github.com/orgs/acme/projects/2/views/1?pane=issue&itemId=1",
    )

    def document(
        identifier: str, path: str, template: str | None = None
    ) -> plan_store.StoreDocument:
        return plan_store.StoreDocument(
            qualified_id=plan_store.QualifiedDocumentId(identifier),
            title=identifier,
            content="",
            project="plan",
            labels=[],
            repositories=[],
            metadata={} if template is None else {"onetaskgraph.template": {"template": template}},
            location={"path": path},
        )

    report = document(
        "demo:plan-report", str(store / "documents/plan-report.md"), "onepipeline:spike-report"
    )
    design = document(
        "demo:plan-design", str(store / "documents/plan-design.md"), "onepipeline:design-doc"
    )
    held = {"location": {"path": str(store / "projects/plan.md")}}
    view = plan_review.plan_view("demo:plan", [task, unlocated, on_a_board], [report, design], held)
    assert view.tasks == {"route": str(store / "tasks/route.md")}
    assert view.records["route"] == {"key": "k", "by": "review-plan"}
    assert view.records["other"] is None
    assert view.design == str(store / "documents/plan-design.md")
    assert view.description == str(store / "projects/plan.md")
    composed = plan_review._plan_prompt({"tasks": [{"id": "route"}]}, view=view)
    assert f"the design document at `{store / 'documents/plan-design.md'}`" in composed

    twice = document("demo:other-design", "/store/documents/b.md", "onepipeline:design-doc")
    undecided = plan_review.plan_view("demo:plan", [task], [design, twice])
    assert undecided.design is None and undecided.description is None


@pytest.mark.parametrize(
    "location",
    [
        None,
        "https://github.com/orgs/acme/projects/2/views/1?pane=issue&itemId=1",
        "authoring:plan/route",
        "tasks/route.md",
        "/no/such/file.md",
    ],
    ids=["none", "url", "qualified-id", "relative", "missing"],
)
def test_a_location_that_is_no_file_here_is_named_as_no_path(location: str | None) -> None:
    assert plan_store.local_file(location) is None


def test_a_location_that_is_a_file_here_is_its_path(tmp_path: Path) -> None:
    held = tmp_path / "route.md"
    held.write_text("held\n", encoding="utf-8")
    assert plan_store.local_file(str(held)) == str(held)
    assert plan_store.local_file(str(tmp_path)) is None, "a directory is not a file to read"


def test_the_reviewer_reads_budget_detail_by_the_heading_the_task_template_renders() -> None:
    """The prompt sends the reviewer to a budget's own section in the task that owns it.

    The heading it names is the one `templates/plan-task-budgets.md.j2` renders each budget
    under, so a search for it in that task's file finds that budget and no other.
    """
    template = plan_budgets.TASK_PARTIAL.read_text(encoding="utf-8")
    assert plan_review.BUDGET_HEADING.replace("<name>", "{{ budget.name }}") in template
    composed = " ".join(plan_review._plan_prompt({"tasks": [{"id": "bare"}]}).split())
    assert f"under its `{plan_review.BUDGET_HEADING}` heading, inside that task's `## Budgets`" in (
        composed
    )
    asked = " ".join(plan_review.PLAN_REVIEW_PROMPT.split())
    assert "where a question below turns on its criteria or on a budget's detail" in asked


@pytest.mark.reads_docs
def test_the_reviewer_reads_a_task_file_only_up_to_the_notes_every_agent_task_shares() -> None:
    """The heading the prompt stops a task-file read at is the appendix's own."""
    appendix = (REPO_ROOT / plan_review.APPENDIX).read_text(encoding="utf-8")
    assert f"\n{plan_review.NOTES_HEADING}\n" in appendix
    composed = " ".join(plan_review._plan_prompt({"tasks": [{"id": "bare"}]}).split())
    assert f"read it only as far as its `{plan_review.NOTES_HEADING}` heading" in composed
    asked = " ".join(plan_review.PLAN_REVIEW_PROMPT.split())
    assert "a node's task file, up to the operational notes it ends with" in asked
