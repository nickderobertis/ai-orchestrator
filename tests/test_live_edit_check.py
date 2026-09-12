"""Every live edit stating task prose is held to the criteria bar before it is sent.

Four ops put task prose in front of a dispatch: `amend` replaces the binding correction
composed onto a node's effective task, and `add`, `retry` and `requeue` each state a
whole task. All four reach a node over the live channel rather than through the plan
store, so `just check-plan` never sees one and `just review-plan` never records one —
which is why the text a judge actually reads could be text nothing had reviewed.

What is proven here is the reader in front of the bar, the bar's own answer for each
carrier, and the register that keeps what one reply cleared for the next one to find:
which of the bar's questions apply to an amendment, which do not and why not, that a
whole task is read past the block one heading opens, that the envelope shapes around
all four are passed over rather than judged, and that a text the free tier takes is
then put to one judged turn — spent once per text, framed for what a live edit is, and
answered with every finding or with nothing known.
`tests/ask_seam/test_channel_reply_e2e.py` drives the same refusals through the real
`just channel-reply` against a real run, with the real `oneharness` spending that turn
against a scripted provider, which is where a manager meets them.

The judged turn is stood in at the one boundary it crosses into this module — the
``judge`` :func:`orchestrator.live_edit_check.refusal` takes — and nowhere below it: a
scripted judge answers with a verdict, or fails the test when a turn is spent that the
journey says must not be.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from orchestrator import live_edit_check, plan_review, plan_store
from orchestrator.criteria_guard import (
    APPENDIX,
    CRITERIA_HEADING,
    Bar,
    CriteriaError,
    check,
    check_amendment,
)
from orchestrator.live_edit_check import (
    ADDITIONAL_INFO_HEADING,
    AMENDMENT_HEADING,
    AMENDMENT_PRECEDENCE,
    CHECKPOINT,
    JOURNAL,
    LAUNCH_PLAN,
    REFUSED,
    REGISTER,
    UNANSWERED,
    Judge,
    Nodes,
    Register,
    ReviewUnanswered,
    amended,
    main,
    reviewables,
)
from orchestrator.plan_review import Finding, Verdict
from orchestrator.root import REPO_ROOT

#: An operational-notes section in the shape every dispatched task carries one, naming
#: the invocations and the chained command this bar refuses **inside criteria**. Stated
#: here rather than read from the tracked appendix so that these journeys stay in the
#: code-keyed tier; what exempts the section is its heading, so a synthetic one is the
#: same evidence. `test_the_tracked_appendix_itself_is_left_alone` drives the real text.
APPENDIX_TEXT = (
    "## Additional info\n\n### Operational notes for this host\n\n"
    "Run `just test` over what you changed, and `git status` before you commit. Read the "
    "table with `ps -eo pid,args` rather than signalling by pattern, and repair a "
    'root-owned tree with `docker run --rm -v "$PWD":/w -w /w image chown -R x /w && '
    "just check`."
)


#: A verdict that passes, and one that refuses with two findings — the pair every judged
#: journey below is written from.
PASSES = Verdict(passes=True, findings=[])
REFUSES = Verdict(
    passes=False,
    findings=[
        Finding(criterion="- Done.", why="it reads as satisfied whatever the dispatch does"),
        Finding(
            criterion="- The newest release is pinned.", why="a floor a later release overtakes"
        ),
    ],
)


class ScriptedJudge:
    """The judged turn, answering what it was scripted to and keeping every prompt.

    Answers repeat the last one scripted once they run out, so a journey scripts only the
    turns whose answers differ. A judge scripted with nothing fails the test the moment
    it is asked, which is how a journey states that no turn may be spent.
    """

    def __init__(self, *answers: Verdict) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> Verdict:
        if not self.answers:
            pytest.fail(f"a judged turn was spent where none may be:\n{prompt}")
        self.prompts.append(prompt)
        if len(self.answers) > 1:
            return self.answers.pop(0)
        return self.answers[0]


def refusal(
    envelope: object,
    register: Register | None = None,
    judge: Judge | None = None,
    current: Nodes | None = None,
) -> str | None:
    """Why ``envelope`` is refused, against a register that keeps nothing by default.

    Most journeys here are about the bar's answer rather than about the register, and a
    register with nowhere to write is what the recipe hands over for a run reference it
    could not resolve — so it is the honest default as well as the convenient one. The
    judge passes everything by default, so a refusal a journey reads is the free tier's
    unless it scripted otherwise; and the run holds no nodes by default, so an `amend`
    is read alone unless a journey states the node it corrects.
    """
    return live_edit_check.refusal(
        envelope,
        Register(None) if register is None else register,
        ScriptedJudge(PASSES) if judge is None else judge,
        current,
    )


#: An amendment stating what the finished tree must carry, which is what every refusal
#: below asks its author for. It names a file and a behaviour and prescribes no route to
#: either, so nothing in the bar has anything to say about it.
SOUND = (
    "The finished tree carries an assertion whose subject is the behaviour this change "
    "adds, so removing that behaviour fails it."
)


def _envelope(*commands: dict[str, Any]) -> dict[str, Any]:
    """One reply envelope in the shape `just channel-reply` stages.

    `Any` because a reply envelope is `onepipeline reply`'s own open contract and its
    commands are heterogeneous by design — a `note` carries an addressee, a `retry` a
    whole replacement node — and the module under test reads it as the untyped JSON it
    arrives as. A typed model here would be a second declaration of somebody else's
    schema, and the shapes these journeys hand over on purpose are the ones no such
    model would let them write.
    """
    return {"version": 2, "author": "planner", "commands": list(commands)}


def _amend(text: str, node: str = "work") -> dict[str, Any]:
    """One `amend` command, as the untyped object the envelope above carries."""
    return {"op": "amend", "id": node, "text": text}


def test_an_amendment_resting_on_the_merge_paths_verdict_is_refused() -> None:
    """The amendment this whole check was written from, refused with its own reason.

    Written in the minute after a manager read a failure, it settled correct, committed,
    gate-green work as a task failure: the checks it names run on the host after
    publication, so the agent step it binds has ended before any of them start.
    """
    said = refusal(
        _envelope(
            _amend(
                "The finished branch merges cleanly into its base and its change "
                "request's required checks pass."
            )
        )
    )

    assert said is not None
    assert "the amendment for node 'work'" in said, said
    assert "required checks pass" in said, said
    assert "state that arrives after it is gone" in said, said


def test_an_amendment_prescribing_a_route_is_refused_and_offered_the_note() -> None:
    """The second shape that cost a node: a mechanism where a property belongs.

    "Do not re-research it" forbade the route that found the answer, and a judge cannot
    tell a mechanism its author preferred from a property the node owes. The escape a
    plan's criteria do not have is named in the refusal, because an observation belongs
    in a `note`, which touches no acceptance criterion at all.
    """
    said = refusal(_envelope(_amend("Do not re-research it: run `just gate` and stop there.")))

    assert said is not None
    assert "names a `just` invocation" in said, said
    assert "`note`" in said, said


def test_an_amendment_whose_backtick_run_never_closes_is_refused_before_the_rest() -> None:
    """The precondition for both questions, asked first and by name.

    Every pattern below it reads inline code by pairing backticks, so an unclosed run
    makes one read text its author never wrote as code — and the refusal then quotes a
    span crossing sentences. Refusing the imbalance is what keeps the two questions'
    quotes honest.
    """
    said = refusal(_envelope(_amend("Keep the `--json form, and just report what it says.")))

    assert said is not None
    assert "backtick run unclosed" in said, said


def test_a_sound_amendment_is_not_refused() -> None:
    """The half that makes the refusals worth having: a property passes untouched."""
    assert refusal(_envelope(_amend(SOUND))) is None


#: A bar making no demand of its own, so a refusal below is attributable to the criterion
#: rather than to something the bar imported into it.
NOTHING_DEMANDED = Bar("a bar that demands nothing", "Accept it when it is done.")


@pytest.mark.parametrize(
    ("text", "why"),
    (
        (
            "The pin moves to the release carrying that fix, at version 0.19.3 or past it.",
            "a criterion resting on somebody else's released artifact is a plan's author "
            "asking for a fact nobody in the dispatch can establish, where an amendment "
            "binds the next dispatch of a node its author is watching — and naming the "
            "release that has just landed is the correction most worth amending mid-run",
        ),
        (
            "The wheel this pin names exists on the registry.",
            "the other half of the same entry pair: the existence of a released artifact "
            "is not a property of any finished tree, while a manager amending mid-run is "
            "reading a registry the work has already reached",
        ),
        (
            "Give an honest justification of the shape described above.",
            "a deferral leaves a plan's judge reconstructing the criterion, where an "
            "amendment arrives composed onto the very task whose prose it points at",
        ),
        (
            "Use the exact phrase 'landed in part' in the subject.",
            "a demand for a particular string is a wording a plan cannot make a worker "
            "guess at, and a wording — a subject, a heading — is a legitimate thing for "
            "a correction to be about",
        ),
    ),
)
def test_a_question_a_whole_bar_asks_is_not_asked_of_an_amendment(text: str, why: str) -> None:
    """An amendment is a correction to criteria rather than the whole bar.

    Both halves are asserted of each text, because either alone says nothing: the plan
    check really does refuse it, and the amendment check really does not. Driven as a
    tuple because the decision is the set — a question quietly added here would start
    refusing a manager mid-run, and one quietly dropped from the plan check would stop
    refusing a plan, and only the pair catches either.

    The first text is the one to read twice, because what refuses it moved: the version
    literal in it is no longer refused anywhere deterministically, and what the plan check
    now names is `the release carrying` — an entry of
    :data:`~orchestrator.criteria_guard.OUT_OF_DISPATCH` whose ``of_an_amendment`` is
    false. So this tuple is still the same pair of facts about the same text, reached
    through a different rule, which is what keeps it evidence rather than a coincidence.
    """
    task = f"## What\n\nx\n\n## Why\n\ny\n\n{CRITERIA_HEADING}\n\n- {text}\n"
    with pytest.raises(CriteriaError):
        check(task, "probe", NOTHING_DEMANDED)

    assert refusal(_envelope(_amend(text))) is None, why


#: One criterion stating a property of the finished tree, so nothing in the bar has
#: anything to say about the criteria block of a whole task built around it.
SOUND_CRITERION = "- The finished tree carries an assertion whose subject is that behaviour."


def _task(*sections: str, criteria: str = SOUND_CRITERION) -> str:
    """One whole task in the template every plan here is written in.

    The appendix is carried because every dispatched task carries it, and because it is
    what makes a journey below evidence rather than an assertion: the text names
    `pkill`, `git status` and a `just` invocation, so a reader that examined its section
    would refuse every task this host requires.
    """
    return "\n\n".join(
        (
            "## What\n\nDo the thing.",
            "## Why\n\nThe user asked for it.",
            f"{CRITERIA_HEADING}\n\n{criteria}",
            *sections,
            f"{APPENDIX_TEXT}\n",
        )
    )


def _retry(task: str, node: str = "work-2", persona: str = "engineer") -> dict[str, Any]:
    """One `retry`, which states a whole replacement task rather than a correction."""
    return {"op": "retry", "id": "work", "node": {"id": node, "persona": persona, "task": task}}


#: The amendment section that cost a node, in the placement `AGENTS.md` instructs: under
#: a heading of its own, above the operational notes, opening by saying it outranks them.
#: Three readers passed the real one — the amendment check declined a `retry` by design,
#: the task-level bar stopped at the next heading, and nothing read what sat between.
AMENDMENT_SECTION = (
    "## Amendment\n\nWhere this and the notes below it disagree, this wins: the finished "
    "branch merges cleanly into its base and its change request's required checks pass."
)


def test_a_clause_under_a_heading_of_its_own_in_a_replacement_task_is_refused() -> None:
    """The region no reader examined, and the whole reason this reads past the block.

    The amendment rode inside a `retry`'s replacement task rather than arriving as an
    `amend`, so the amendment check declined it; it sat under a heading of its own rather
    than among the criteria, so the task-level bar stopped before it. The refusal names
    both the node and the section, because a manager holding a whole replacement task has
    to be told which part of it to correct.
    """
    said = refusal(_envelope(_retry(_task(AMENDMENT_SECTION))))

    assert said is not None
    assert "the replacement task for node 'work-2'" in said, said
    assert "under '## Amendment'" in said, said
    assert "required checks pass" in said, said


def test_a_replacement_task_carrying_the_appendix_and_sound_criteria_is_not_refused() -> None:
    """The half that makes the refusal worth having, and the exemption proven rather than
    asserted.

    The task carries the tracked appendix verbatim — `pkill`, `git status`, a `just`
    invocation and a chained shell command among them — so a reader that examined that
    section would refuse it. It is not refused, which is what says
    :data:`~orchestrator.criteria_guard.DESCRIBED_ELSEWHERE` really exempts the one
    section every dispatched task is required to carry.
    """
    assert refusal(_envelope(_retry(_task()))) is None


def test_a_replacement_task_is_held_to_the_criteria_bar_over_its_own_block() -> None:
    """A whole task's criteria are asked the whole bar, exactly as a plan's are.

    The obvious repair on its own — routing a `retry` through the task-level bar — is
    not enough, but it is still half of what a live edit was missing: before this, a
    replacement task could name an invocation in its criteria and reach a dispatch with
    nobody having read it.
    """
    said = refusal(_envelope(_retry(_task(criteria="- Run `just gate` and report it green."))))

    assert said is not None
    assert "names a `just` invocation" in said, said


def test_a_requeues_amended_task_is_read_under_the_node_it_returns() -> None:
    """A `requeue`'s overrides state a partial node, so the parked node's id names it.

    The engine refuses an `amend` that rewrites `id`, so the mapping carries none of its
    own — and a refusal naming `None`, or naming a positional fallback, is one a manager
    cannot act on.
    """
    said = refusal(
        _envelope({"op": "requeue", "id": "work", "amend": {"task": _task(AMENDMENT_SECTION)}})
    )

    assert said is not None
    assert "the amended task for node 'work'" in said, said


def test_an_added_nodes_task_is_read_as_the_node_it_adds() -> None:
    """`add` states a full node mapping, and its task reaches a dispatch like any other."""
    said = refusal(
        _envelope(
            {
                "op": "add",
                "node": {"id": "extra", "persona": "engineer", "task": _task(AMENDMENT_SECTION)},
            }
        )
    )

    assert said is not None
    assert "the task added as node 'extra'" in said, said


def test_each_step_of_a_lifecycle_replacement_is_read_on_its_own() -> None:
    """A stepped node states its prose once per step, so each step is a resulting task.

    Read through the same walker `just check-plan` uses, which is what makes the step's
    id in a refusal the one that command would print.
    """
    said = refusal(
        _envelope(
            {
                "op": "retry",
                "id": "work",
                "node": {
                    "id": "work-2",
                    "steps": [
                        {"id": "first", "persona": "engineer", "task": _task()},
                        {"id": "second", "persona": "engineer", "task": _task(AMENDMENT_SECTION)},
                    ],
                },
            }
        )
    )

    assert said is not None
    assert "the replacement task for node 'work-2/second'" in said, said


def test_a_note_and_an_op_carrying_no_task_prose_are_passed_over() -> None:
    """`note`, and every op that states no task, carry nothing for this bar to read.

    A `note` carries no criteria at all unless it states one, and its text is
    observational by construction — refusing one would refuse the very escape every
    refusal here offers. `cancel` and `drop` state no prose, and a `requeue` amending
    only a turn budget states no task either.
    """
    unjudged = _envelope(
        {"op": "note", "id": "work", "addressee": "both", "text": "run `just gate` first"},
        {"op": "cancel", "id": "work", "reason": "run `just gate` instead"},
        {"op": "requeue", "id": "work", "amend": {"max_turns": 40}},
    )

    assert reviewables(unjudged) == []
    assert refusal(unjudged) is None


def test_a_node_nothing_dispatches_from_is_passed_over() -> None:
    """A node declaring `expects_no_diff` settles without a worker, so no judge reads it.

    The ordinary way a manager records a follow-up mid-run is `{"task": "Report.",
    "expects_no_diff": true}` — a node with no persona and no acceptance criteria, which
    the engine settles `done (no-changes)` without a dispatch. Read as a whole task it
    was refused for the criteria it has no reader for, which refused every such `add`
    this repository's own journeys send. A refusal over prose no judge reads holds
    nobody to anything.
    """
    unjudged = _envelope(
        {"op": "add", "node": {"id": "follow-up", "task": "Report.", "expects_no_diff": True}},
        {
            "op": "retry",
            "id": "work",
            "node": {"id": "redo", "task": "No diff.", "expects_no_diff": True},
        },
    )

    assert reviewables(unjudged) == []
    assert refusal(unjudged) is None


def test_a_step_nothing_dispatches_from_is_passed_over_while_its_siblings_are_read() -> None:
    """A step may declare `expects_no_diff` of itself, and only that step is left out.

    Pruned per step rather than per node, because the steps beside it still dispatch:
    a lifecycle node recording one follow-up among its steps is still read for the
    steps that a judge reads.
    """
    said = refusal(
        _envelope(
            {
                "op": "retry",
                "id": "work",
                "node": {
                    "id": "work-2",
                    "steps": [
                        {"id": "record", "task": "Report.", "expects_no_diff": True},
                        {"id": "second", "persona": "engineer", "task": _task(AMENDMENT_SECTION)},
                    ],
                },
            }
        )
    )

    assert said is not None
    assert "the replacement task for node 'work-2/second'" in said, said


def _note(text: str, criterion: str | None = None, node: str | None = "work") -> dict[str, Any]:
    """One `note`, with the criterion it may carry beside its observational text."""
    command: dict[str, Any] = {"op": "note", "addressee": "both", "text": text}
    if node is not None:
        command["id"] = node
    if criterion is not None:
        command["criterion"] = criterion
    return command


def test_a_notes_criterion_is_read_as_a_correction_and_its_text_is_not() -> None:
    """A note's `criterion` enters the acceptance criteria its judge decides against.

    That is the whole reason the weaker op it replaced was removed, and it is what makes
    the criterion criteria: it binds a judge exactly as an amendment does, so it is asked
    an amendment's two questions and owes no judged turn, exactly as an amendment does.
    The text beside it names a route on purpose and is not read — holding an observation
    to a criteria bar would refuse exactly the corrections a manager most needs to send.
    The refusal names the field and the escape that fits it, which is the note's own
    `text` rather than "a note".
    """
    said = refusal(_envelope(_note("run `just gate` first", "The required checks pass.")))

    assert said is not None
    assert said.startswith("the criterion of the note for node 'work':"), said
    assert "required checks pass" in said, said
    assert "belongs in the note's `text`" in said, said

    (found,) = reviewables(_envelope(_note("run `just gate` first", SOUND)))
    assert found.text == SOUND
    assert not found.whole_task and not found.judged
    assert refusal(_envelope(_note("run `just gate` first", SOUND)), judge=ScriptedJudge()) is None


def test_a_note_naming_no_node_is_still_named_in_its_refusal() -> None:
    """A refusal must never read as though it were about no node at all."""
    said = refusal(_envelope(_note("x", "The required checks pass.", node=None)))

    assert said is not None
    assert said.startswith("the criterion of the note in commands[0]:"), said


@pytest.mark.parametrize("criterion", ("", "   ", 7))
def test_a_notes_criterion_that_is_blank_or_not_text_is_left_to_the_verb(
    criterion: object,
) -> None:
    """Blank and non-string criteria are the verb's to refuse, exactly as an amend's text is."""
    # `cast` because the wrong type is the subject: `_note` is typed to what a manager
    # sends, and the `7` here is what the verb refuses on its own, sent to prove that the
    # reader leaves it to the verb rather than refusing it under a name of its own.
    assert reviewables(_envelope(_note("x", cast(Any, criterion)))) == []


#: A run holding two agent nodes under different personas, as the run's own record
#: states them. `other` is a `researcher`, whose shipped bar forbids the dispatch changing
#: project files — which is what makes a requeue of it under an unchanged persona
#: answer differently from one under `engineer`.
def _nodes(*extra: dict[str, Any]) -> Nodes:
    held = [
        {"id": "work", "persona": "engineer", "deps": ["gate"], "task": _task()},
        {"id": "other", "persona": "researcher", "deps": ["gate"], "task": _task()},
        *extra,
    ]
    return Nodes({node["id"]: node for node in held})


def test_the_engine_renders_an_amendment_above_the_operational_notes() -> None:
    """The composition is the engine's, restated: the block goes immediately above
    `## Additional info` when the task states one on a line of its own, and at the end
    of a task that states none; a blank amendment renders nothing.

    `tests/test_engine_contracts.py` holds the three strings and the placement to the
    engine's own source at the pinned release.
    """
    block = f"{AMENDMENT_HEADING}\n{AMENDMENT_PRECEDENCE}\n\nLeave the comments.\n"
    with_notes = "## What\n\nDo it.\n\n## Additional info\n\nRun it.\n"
    assert amended(with_notes, "Leave the comments.") == (
        f"## What\n\nDo it.\n\n{block}\n{ADDITIONAL_INFO_HEADING}\n\nRun it.\n"
    )
    without = "## What\n\nDo it. Put it under `## Additional info`.\n"
    assert amended(without, "Leave the comments.") == (
        f"## What\n\nDo it. Put it under `## Additional info`.\n\n{block}"
    )
    assert amended(with_notes, "  ") == with_notes
    assert amended(with_notes, None) == with_notes


def test_an_amendment_is_composed_onto_the_nodes_own_task() -> None:
    """What an `amend` results in is the node's task with the amendment rendered into it.

    That is the text the node's judge reads, so it is the text the review is keyed on
    and read against: the whole bar over its criteria, the two questions over the
    `## Amendment` section the engine adds, under the persona whose bar that node's judge
    is given — and no judged turn, because it is a correction to a task a review already
    cleared.
    """
    (found,) = reviewables(_envelope(_amend(SOUND)), _nodes())

    assert found.text == amended(_task(), SOUND), found.text
    assert found.persona == "engineer"
    assert found.whole_task and not found.judged
    assert found.where == "the effective task of node 'work'"
    assert refusal(_envelope(_amend(SOUND)), judge=ScriptedJudge(), current=_nodes()) is None

    said = refusal(
        _envelope(_amend("The required checks pass.")), judge=ScriptedJudge(), current=_nodes()
    )
    assert said is not None
    assert "the effective task of node 'work', under '## Amendment'" in said, said
    assert "required checks pass" in said, said


def test_the_same_amendment_on_two_nodes_is_two_reviews(tmp_path: Path) -> None:
    """Identical amendment text on different underlying tasks is different effective tasks.

    Keyed on the text alone, a pass granted to the amendment of one node would be found
    by the same words sent to another node whose task — and whose judge — is not the
    same. Keyed on the effective task, the two are two records.
    """
    register = _register(tmp_path)
    current = _nodes({"id": "third", "persona": "engineer", "task": _task("## Scope\n\nAll.")})

    assert refusal(_envelope(_amend(SOUND, node="work")), register, current=current) is None
    assert refusal(_envelope(_amend(SOUND, node="third")), register, current=current) is None

    assert register.added == 2, "the same amendment on a different task reused the first's pass"
    assert sorted(held["where"] for held in register.reviews.values()) == [
        "the effective task of node 'third'",
        "the effective task of node 'work'",
    ]


def test_an_amendment_renders_into_every_step_of_a_lifecycle_node() -> None:
    """An amendment belongs to the node, so it renders into each agent step's task."""
    current = Nodes(
        {
            "work": {
                "id": "work",
                "steps": [
                    {"id": "first", "persona": "engineer", "task": _task()},
                    {"id": "second", "persona": "engineer", "task": _task()},
                ],
            }
        }
    )

    found = reviewables(_envelope(_amend(SOUND)), current)

    assert [one.where for one in found] == [
        "the effective task of node 'work/first'",
        "the effective task of node 'work/second'",
    ]
    assert all(one.text == amended(_task(), SOUND) for one in found)


def test_an_amendment_to_a_node_this_run_cannot_read_is_read_alone() -> None:
    """With no node to compose onto, the correction is read as the correction it is.

    The engine refuses an `amend` naming no node of the graph for itself, so nothing is
    guessed at; what can be read is the amendment's own two questions.
    """
    (found,) = reviewables(_envelope(_amend(SOUND, node="nobody")), _nodes())

    assert found.text == SOUND and not found.whole_task and not found.judged
    assert found.where == "the amendment for node 'nobody'"


def test_a_requeue_keeps_the_parked_nodes_persona_and_its_bar() -> None:
    """A requeue's overrides merge onto the existing node, persona included.

    `other` is a `researcher`, whose bar forbids changing project files. Overrides that
    restate no persona are judged under that bar — so criteria requiring a file to change
    are refused naming the clause — where the same overrides restating `engineer` are
    taken. Read from the envelope alone, both would have been judged under the generic
    contract and the first would have passed.
    """
    changes = "- `docs/x.md` gains a row."
    requeue = {"op": "requeue", "id": "other", "amend": {"task": _task(criteria=changes)}}

    said = refusal(_envelope(requeue), judge=ScriptedJudge(PASSES), current=_nodes())

    assert said is not None
    assert "the amended task for node 'other'" in said, said
    assert "modified project files" in said, said

    restated = {**requeue, "amend": {**requeue["amend"], "persona": "engineer"}}
    judge = ScriptedJudge(PASSES)
    assert refusal(_envelope(restated), judge=judge, current=_nodes()) is None
    assert len(judge.prompts) == 1, "a requeued whole task did not spend its judged turn"
    assert '"persona": "engineer"' in judge.prompts[0]


def test_a_requeue_keeps_the_parked_nodes_amendment_under_a_new_task() -> None:
    """The existing node's amendment renders into the task a requeue overrides."""
    current = _nodes({"id": "ruled", "persona": "engineer", "task": _task(), "amendment": SOUND})
    requeue = {"op": "requeue", "id": "ruled", "amend": {"task": _task("## Scope\n\nAll.")}}

    (found,) = reviewables(_envelope(requeue), current)

    assert found.text == amended(_task("## Scope\n\nAll."), SOUND)
    assert found.judged


def test_a_requeue_that_changes_no_task_reads_nothing() -> None:
    """A requeue moving a turn budget alone results in the task the node already had.

    That task is not novel — the plan reviewed it, or the live edit that last set it was
    recorded here — so nothing is put to the bar and no judged turn is spent.
    """
    budget = {"op": "requeue", "id": "work", "amend": {"max_turns": 40}}

    assert reviewables(_envelope(budget), _nodes()) == []
    assert refusal(_envelope(budget), judge=ScriptedJudge(), current=_nodes()) is None


def test_a_bare_amendment_spends_no_judged_turn_and_a_novel_task_does(tmp_path: Path) -> None:
    """The line is drawn at what the content is, not at which op carried it.

    An amendment is a correction to a task a review already cleared, written in the
    minute after a manager reads a failure; it is answered by the free tier alone. An
    added node, a retry's replacement and a requeued node's amended task are complete
    authored tasks nothing holds a pass for, and each would have been reviewed had it
    arrived in the plan.
    """
    register = _register(tmp_path)
    assert refusal(_envelope(_amend(SOUND)), register, ScriptedJudge(), _nodes()) is None
    assert register.added == 1

    judged = ScriptedJudge(PASSES)
    novel = _envelope(
        {"op": "add", "node": {"id": "extra", "persona": "engineer", "task": _task()}},
        _retry(_task("## Scope\n\nAll.")),
        {"op": "requeue", "id": "other", "amend": {"task": _task("## Scope\n\nNone.")}},
    )
    assert refusal(novel, register, judged, _nodes()) is None
    assert len(judged.prompts) == 3, "a novel whole task did not spend its judged turn"


def test_a_later_command_composes_onto_the_run_and_not_onto_an_earlier_one(
    tmp_path: Path,
) -> None:
    """One envelope's commands each compose onto the run as it stands, not onto each other.

    An `add` of `extra` and an `amend` for `extra` in the same envelope: the added node is
    a novel whole task and spends its judged turn, while the amendment names a node the
    run does not yet hold — the envelope's own add — and so is read alone, as the
    correction it is, under the free tier. Neither text goes unread, and the second
    envelope proves it: the same add, whose pass the register now holds, spends no second
    turn, and the amendment beside it carrying a clause resting on the merge path's
    verdict refuses the whole envelope.
    """
    register = _register(tmp_path)
    added = {"op": "add", "node": {"id": "extra", "persona": "engineer", "task": _task()}}
    judged = ScriptedJudge(PASSES)
    assert refusal(_envelope(added, _amend(SOUND, "extra")), register, judged, _nodes()) is None
    assert len(judged.prompts) == 1, "the added node did not spend exactly its own judged turn"
    assert "extra" in judged.prompts[0] and SOUND not in judged.prompts[0], (
        "the amendment was composed onto the node the same envelope adds"
    )
    assert register.added == 2, "the added task and the amendment were not each recorded"

    harmful = _amend(
        "The finished branch merges cleanly into its base and its change request's "
        "required checks pass.",
        "extra",
    )
    said = refusal(_envelope(added, harmful), register, ScriptedJudge(), _nodes())

    assert said is not None
    assert "the amendment for node 'extra'" in said, said
    assert "required checks pass" in said, said


def test_the_same_whole_task_from_two_ops_is_one_review(tmp_path: Path) -> None:
    """A later op resulting in the same effective task finds the pass the first recorded.

    A requeue that amends `work`'s task and an `add` of a new node stating that same
    task under the same persona are one effective task, so the second spends nothing.
    """
    register = _register(tmp_path)
    same = _task("## Scope\n\nAll.")
    first = ScriptedJudge(PASSES)
    requeue = {"op": "requeue", "id": "work", "amend": {"task": same}}
    assert refusal(_envelope(requeue), register, first, _nodes()) is None
    assert len(first.prompts) == 1 and register.flush() is None

    later = _register(tmp_path)
    added = {"op": "add", "node": {"id": "again", "persona": "engineer", "task": same}}
    assert refusal(_envelope(added), later, ScriptedJudge(), _nodes()) is None
    assert later.added == 0, "the same effective task from a second op was reviewed again"


def test_a_bare_amendments_free_pass_does_not_stand_in_for_a_novel_tasks_judged_turn(
    tmp_path: Path,
) -> None:
    """The one effective task two ops can share while owing different tiers.

    An `amend` composes onto `work`'s own task and clears the free tier alone; an `add`
    then states that same effective text outright — `work`'s task with the same
    amendment — as a novel whole task, which owes a judged turn. A key over the text and
    persona alone would find the amendment's record and hand the added node to its
    dispatch with the judged tier's questions unasked, so the key carries which tiers
    the text cleared, and the add spends its turn. The reverse costs nothing: the same
    text arriving again as an amendment, after the judged pass, is asked the free tier
    again — no provider turn — and recorded under its own shape.
    """
    register = _register(tmp_path)
    assert refusal(_envelope(_amend(SOUND)), register, ScriptedJudge(), _nodes()) is None
    assert register.flush() is None

    later = _register(tmp_path)
    judged = ScriptedJudge(PASSES)
    restated = {
        "op": "add",
        "node": {"id": "again", "persona": "engineer", "task": _task(), "amendment": SOUND},
    }
    assert refusal(_envelope(restated), later, judged, _nodes()) is None

    assert len(judged.prompts) == 1, (
        "an added node stating a bare amendment's effective text found that amendment's "
        "free pass and skipped the judged turn a novel whole task owes"
    )
    assert SOUND in judged.prompts[0], "the judged turn did not read the composed text"
    assert later.added == 1 and later.flush() is None

    again = _register(tmp_path)
    assert refusal(_envelope(_amend(SOUND)), again, ScriptedJudge(), _nodes()) is None
    assert again.added == 0, "the amendment's own record was not found after the judged pass"


def _run(
    tmp_path: Path,
    *,
    plan: list[dict[str, Any]] | None = None,
    checkpoint: list[dict[str, Any]] | None = None,
    journal: list[list[dict[str, Any]]] = (),
    covered: object = None,
) -> Path:
    """One run root, as the engine leaves it: a launch plan, a journal, and a checkpoint.

    ``journal`` is one `edit-committed` record per entry, each carrying the compiled
    operations given; ``covered`` is the journal offset the checkpoint claims to have
    read to, defaulting to the whole journal so a caller states a stale one on purpose.
    """
    root = tmp_path / "run"
    root.mkdir(parents=True)
    lines = [
        json.dumps({"kind": "edit-committed", "payload": {"operations": operations}}) + "\n"
        for operations in journal
    ]
    (root / JOURNAL).write_text("".join(lines), encoding="utf-8")
    if plan is not None:
        (root / LAUNCH_PLAN).write_text(json.dumps({"tasks": plan}), encoding="utf-8")
    if checkpoint is not None:
        bytes_covered = (
            sum(len(line.encode("utf-8")) for line in lines) if covered is None else covered
        )
        (root / CHECKPOINT).write_text(
            json.dumps(
                {"coverage": {"bytes": bytes_covered}, "state": {"graph": {"nodes": checkpoint}}}
            ),
            encoding="utf-8",
        )
    return root


def test_the_run_is_read_from_its_checkpoint_and_the_journal_past_it(tmp_path: Path) -> None:
    """The checkpoint is the fold up to the offset it names; the journal past it is folded here.

    Proven from both ends: a record *before* the offset is not re-applied — it drops the
    node the checkpoint still holds — and a record *after* it is, amending that node.
    """
    before = [{"kind": "node-dropped", "node": "work", "dependents": "detach"}]
    after = [{"kind": "task-amended", "node": "work", "text": SOUND}]
    node = {"id": "work", "persona": "engineer", "task": _task()}
    root = _run(tmp_path, checkpoint=[node], journal=[before, after])
    line = len(json.dumps({"kind": "edit-committed", "payload": {"operations": before}}) + "\n")
    (root / CHECKPOINT).write_text(
        json.dumps({"coverage": {"bytes": line}, "state": {"graph": {"nodes": [node]}}}),
        encoding="utf-8",
    )

    current = Nodes.read(root)

    assert current.get("work") == {**node, "amendment": SOUND}


def test_a_run_with_no_usable_checkpoint_is_folded_from_its_launch_plan(tmp_path: Path) -> None:
    """A missing checkpoint, and one whose coverage cannot be trusted, both start from the plan."""
    plan = [{"id": "work", "persona": "engineer", "task": _task()}]
    ops = [
        [{"kind": "node-added", "node": {"id": "added", "persona": "engineer", "task": "x"}}],
        [{"kind": "node-requeued", "node": "work", "amend": {"persona": "docs-writer"}}],
        [{"kind": "node-dropped", "node": "added", "dependents": "detach"}],
    ]
    absent = Nodes.read(_run(tmp_path / "absent", plan=plan, journal=ops))
    assert absent.get("added") is None
    assert absent.get("work") == {"id": "work", "persona": "docs-writer", "task": _task()}

    stale = Nodes.read(
        _run(tmp_path / "stale", plan=plan, checkpoint=[], journal=ops, covered=10**9)
    )
    assert stale.get("work") == absent.get("work")

    assert Nodes.read(None).get("work") is None
    assert Nodes.read(tmp_path / "nowhere").get("work") is None


def test_a_requeue_folded_from_the_journal_drops_parked_and_keeps_the_rest(tmp_path: Path) -> None:
    """The fold is the engine's: `parked` is removed and every override written over its field."""
    plan = [
        {"id": "work", "persona": "engineer", "task": _task(), "parked": True, "deps": ["gate"]}
    ]
    ops = [[{"kind": "node-requeued", "node": "work", "amend": {"task": "changed"}}]]

    current = Nodes.read(_run(tmp_path, plan=plan, journal=ops))

    assert current.get("work") == {
        "id": "work",
        "persona": "engineer",
        "task": "changed",
        "deps": ["gate"],
    }


def test_both_kinds_the_engine_journals_a_command_under_are_folded(tmp_path: Path) -> None:
    """A `command-accepted` record's operations fold exactly as an `edit-committed`'s do.

    Which of the two a command is journalled under is the emitter's decision, and the
    engine's own replay folds both, so a reader that folded one alone would be right
    only until a later build put a node-moving operation under the other.
    """
    root = _run(tmp_path, plan=[{"id": "work", "persona": "engineer", "task": _task()}])
    added = {"id": "fresh", "persona": "engineer", "task": _task()}
    with (root / JOURNAL).open("a", encoding="utf-8") as journal:
        for kind in ("command-accepted", "edit-committed"):
            operations = [
                {"kind": "node-added", "node": {**added, "id": f"{kind}-added"}},
                {"kind": "task-amended", "node": "work", "text": kind},
            ]
            journal.write(json.dumps({"kind": kind, "payload": {"operations": operations}}) + "\n")
        settled = {
            "kind": "node-settled",
            "payload": {"operations": [{"kind": "node-added", "node": added}]},
        }
        journal.write(json.dumps(settled) + "\n")

    current = Nodes.read(root)

    assert set(current.nodes) == {"work", "command-accepted-added", "edit-committed-added"}
    assert current.nodes["work"]["amendment"] == "edit-committed"


def test_a_record_the_fold_cannot_read_moves_no_node(tmp_path: Path) -> None:
    """Every shape the fold does not understand is passed over rather than guessed at.

    An operation naming a node the run does not hold, overrides that are not a mapping,
    a journal line that is not JSON, an event that is not an edit, an operation that is
    not a mapping, a listed node stating no id, and a checkpoint whose offset does not
    land on a record boundary — each leaves the nodes exactly as they were, and the last
    is folded from the launch plan instead.
    """
    plan = [{"id": "work", "persona": "engineer", "task": _task()}, "not a node", {"task": "x"}]
    ops = [
        [{"kind": "task-amended", "node": "nobody", "text": SOUND}],
        [{"kind": "task-amended", "node": "work", "text": 7}],
        [{"kind": "node-added", "node": {"task": "no id"}}],
        [{"kind": "node-requeued", "node": "nobody", "amend": {"task": "x"}}],
        [{"kind": "node-requeued", "node": "work", "amend": "not overrides"}],
        ["not an operation", {"kind": "edge-added", "from": "a", "to": "work"}],
    ]
    root = _run(tmp_path, plan=plan, journal=ops)
    with (root / JOURNAL).open("a", encoding="utf-8") as journal:
        journal.write("{not json\n")
        journal.write(json.dumps({"kind": "node-settled", "payload": {}}) + "\n")

    assert Nodes.read(root).nodes == {"work": plan[0]}

    # A coverage that stops mid-record cannot be resumed from: the plan is folded whole.
    (root / CHECKPOINT).write_text(
        json.dumps({"coverage": {"bytes": 5}, "state": {"graph": {"nodes": [{"id": "stale"}]}}}),
        encoding="utf-8",
    )
    assert Nodes.read(root).nodes == {"work": plan[0]}
    for covered in ("5", -1):
        (root / CHECKPOINT).write_text(
            json.dumps(
                {"coverage": {"bytes": covered}, "state": {"graph": {"nodes": [{"id": "stale"}]}}}
            ),
            encoding="utf-8",
        )
        assert Nodes.read(root).nodes == {"work": plan[0]}

    # A checkpoint that read nothing holds what it holds, journal or no journal; one
    # that claims to have read a journal that is gone cannot be trusted at all.
    (root / JOURNAL).unlink()
    (root / CHECKPOINT).write_text(
        json.dumps({"coverage": {"bytes": 0}, "state": {"graph": {"nodes": [{"id": "held"}]}}}),
        encoding="utf-8",
    )
    assert Nodes.read(root).nodes == {"held": {"id": "held"}}
    (root / CHECKPOINT).write_text(
        json.dumps({"coverage": {"bytes": 9}, "state": {"graph": {"nodes": [{"id": "held"}]}}}),
        encoding="utf-8",
    )
    assert Nodes.read(root).nodes == {"work": plan[0]}


def test_the_recipe_hands_the_run_root_over_and_the_check_reads_its_nodes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Through `main`, as the recipe spawns it: the node is read from the run root named.

    The amendment alone is sound; composed onto the node's task it sits under a heading
    of its own, and the whole effective task is what is refused — which is the region
    that cost a node, reached through the same argument the recipe already passes.
    """
    plan = [{"id": "work", "persona": "engineer", "task": _task()}]
    root = _run(tmp_path, plan=plan)
    monkeyed = json.dumps(_envelope(_amend("The required checks pass.")))
    stdin, sys.stdin = sys.stdin, io.StringIO(monkeyed)
    try:
        assert main(argv=[str(root)], judge=ScriptedJudge()) == REFUSED
    finally:
        sys.stdin = stdin

    said = capsys.readouterr().out
    assert said.startswith("the effective task of node 'work', under '## Amendment'"), said


@pytest.mark.parametrize(
    "envelope",
    (
        "not an envelope at all",
        {"version": 2},
        {"version": 2, "commands": "amend it"},
        {"version": 2, "commands": ["amend it"]},
        {"version": 2, "commands": [{"op": "amend", "id": "work"}]},
        {"version": 2, "commands": [{"op": "amend", "id": "work", "text": 7}]},
        {"version": 2, "commands": [{"op": "amend", "id": "work", "text": "   "}]},
        {"version": 2, "commands": [{"op": "retry", "id": "work"}]},
        {"version": 2, "commands": [{"op": "retry", "id": "work", "node": "work-2"}]},
        {"version": 2, "commands": [{"op": "retry", "id": "work", "node": {"id": "work-2"}}]},
        {"version": 2, "commands": [{"op": "retry", "id": "work", "node": {"id": 7, "task": "x"}}]},
        {"version": 2, "commands": [{"op": "retry", "node": {"kind": "human", "action": "merge"}}]},
        {"version": 2, "commands": [{"op": 7, "node": {"id": "work-2", "task": "x"}}]},
    ),
)
def test_a_shape_this_check_does_not_act_on_is_passed_to_the_verb(envelope: object) -> None:
    """Every shape but a command stating task prose is the verb's to refuse.

    `onepipeline reply` refuses a malformed envelope naming what it actually received,
    and a second opinion here would replace that with a guess about what was meant. The
    blank text is that case too: the op refuses blank text itself. So is every node shape
    the plan walker refuses — a `retry` stating no node, one whose node is not a mapping,
    one that dispatches an agent and states no task, one whose id is not a string — and a
    `kind: human` node, which carries an action a person performs rather than a task a
    judge reads.
    """
    assert refusal(envelope) is None


def test_an_amendment_naming_no_node_is_still_named_in_its_refusal() -> None:
    """A refusal must never read as though it were about no node at all.

    An `amend` without an `id` is a shape the verb refuses on its own, so this check
    never decides it — but if it is reached first, the position in the envelope is what
    a manager can act on, where `None` would be a refusal naming nothing.
    """
    said = refusal({"commands": [{"op": "amend", "text": "the required checks pass"}]})

    assert said is not None
    assert said.startswith("the amendment in commands[0]:"), said


def test_the_first_refused_amendment_is_the_one_reported() -> None:
    """The recipe refuses the whole envelope, so one reason is the whole answer."""
    said = refusal(
        _envelope(
            _amend(SOUND, node="first"),
            _amend("The required checks pass.", node="second"),
            _amend("Run `just gate`.", node="third"),
        )
    )

    assert said is not None and said.startswith("the amendment for node 'second':"), said


def test_the_check_exits_on_the_protocol_the_recipe_reads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 0 sends the envelope; 1 refuses it; 3 says its judged turn answered nothing.

    Each carries its reason on stdout and nothing else, and all three are read by
    `scripts/channel-reply.sh`, which treats a non-zero exit with an empty stdout as this
    check having failed to run rather than as a verdict — so a refusal that printed
    nothing would refuse a reply nothing had judged. The third is not a verdict either
    way, and the recipe tells its reader to re-send rather than to correct.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_envelope(_amend(SOUND)))))
    assert main(judge=ScriptedJudge(PASSES)) == 0
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(_envelope(_amend("The required checks pass."))))
    )
    assert main(judge=ScriptedJudge()) == REFUSED
    refused = capsys.readouterr().out
    assert refused.startswith("the amendment for node 'work':"), refused

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_envelope(_retry(_task())))))
    assert main(judge=ScriptedJudge(REFUSES)) == REFUSED
    judged = capsys.readouterr().out
    assert judged.startswith(
        "the replacement task for node 'work-2' was refused by its judged review"
    ), judged

    def unanswered(prompt: str) -> Verdict:
        raise OSError("no candidate answered the review; run `oneharness doctor`")

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_envelope(_retry(_task())))))
    assert main(judge=unanswered) == UNANSWERED
    unknown = capsys.readouterr().out
    assert unknown.startswith("the replacement task for node 'work-2' could not be reviewed"), (
        unknown
    )
    assert "oneharness doctor" in unknown, unknown


def test_an_envelope_that_is_not_json_is_left_to_the_verb(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reply this cannot parse is one the verb refuses, naming what it received."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))

    assert main(judge=ScriptedJudge()) == 0
    assert capsys.readouterr().out == ""


def test_the_module_is_runnable_as_the_recipe_spawns_it() -> None:
    """`python -m orchestrator.live_edit_check`, on the interpreter and path the recipe sets.

    Driven as the process it is rather than through `main`, because that spawn is the
    whole of the seam `scripts/channel-reply.sh` reaches: a module that imported only
    inside this suite's own `sys.path` would refuse nothing when the recipe ran it.
    """
    spawned = subprocess.run(
        [sys.executable, "-m", live_edit_check.__name__],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
        input=json.dumps(_envelope(_amend("The required checks pass."))),
        text=True,
        capture_output=True,
        check=False,
    )

    assert spawned.returncode == REFUSED, spawned.stderr
    assert spawned.stdout.startswith("the amendment for node 'work':"), spawned.stdout


def test_the_bar_itself_is_the_plan_checks_and_is_not_restated_here() -> None:
    """One source for the criteria bar, so a change to it reaches an amendment too.

    `orchestrator/criteria_guard.py` is where the questions live and where the choice of
    which apply to an amendment is written down; this module is the envelope reader in
    front of it. A second copy of any of those patterns would be a second answer to the
    question a manager is refused on, and the two would part company the first time
    either moved — so what is asserted is that the module carries no pattern of its own
    and that the shared entry point is what it calls.
    """
    source = (REPO_ROOT / "orchestrator" / "live_edit_check.py").read_text(encoding="utf-8")

    assert "import re" not in source, source
    assert "check_amendment" in source, source
    with pytest.raises(CriteriaError, match="required checks pass"):
        check_amendment("The required checks pass.", "the amendment for node 'work'")


def _register(tmp_path: Path) -> Register:
    """A register kept in a run root of its own, as the recipe hands one over."""
    return Register(tmp_path / REGISTER)


def test_a_whole_task_the_free_tier_takes_is_put_to_one_judged_turn() -> None:
    """The second tier, and the one a deterministic-only reading of this check left out.

    The rules that moved out of the deterministic tier — whether a number is the right
    number, whether the criteria answer a demand their own bar makes, whether a criterion
    could be falsified — are asked by a judge, and a live edit that cleared the free tier
    alone reached its dispatch with exactly those unasked. So a whole task the free tier
    takes is put to one turn of the same reviewer `just review-plan` spends one of, and
    every finding it names is reported, naming the text the way the refusal does.
    """
    judge = ScriptedJudge(REFUSES)

    said = refusal(_envelope(_retry(_task())), judge=judge)

    assert said is not None
    assert said.startswith(
        "the replacement task for node 'work-2' was refused by its judged review"
    ), said
    for finding in REFUSES["findings"]:
        assert (
            f"the replacement task for node 'work-2': {finding['criterion']} — {finding['why']}"
            in said
        ), said
    (prompt,) = judge.prompts
    assert plan_review.LIVE_EDIT_FRAME in prompt, prompt
    assert (REPO_ROOT / plan_review.BAR_FILES[0]).read_text(encoding="utf-8") in prompt
    assert '"persona": "engineer"' in prompt, prompt
    assert _task() in prompt, prompt


def test_a_text_the_free_tier_refuses_spends_no_judged_turn() -> None:
    """The tiers run in cost order, so the commonest refusal costs no provider turn."""
    said = refusal(_envelope(_retry(_task(AMENDMENT_SECTION))), judge=ScriptedJudge())

    assert said is not None and "required checks pass" in said, said


def test_a_judged_pass_is_recorded_so_the_same_text_spends_no_second_turn(
    tmp_path: Path,
) -> None:
    """What the register is for: one judged turn per resulting task, not per reply.

    The second reply's judge is scripted to fail the test if asked, which is the whole
    assertion — a record the second reply found is a turn it did not spend.
    """
    task = _task()
    first = _register(tmp_path)
    judged = ScriptedJudge(PASSES)
    assert refusal(_envelope(_retry(task)), first, judged) is None
    assert len(judged.prompts) == 1, judged.prompts
    assert first.flush() is None

    second = _register(tmp_path)
    assert refusal(_envelope(_retry(task, node="work-3")), second, ScriptedJudge()) is None
    assert second.added == 0, "the same resulting task was reviewed a second time"


def test_a_judged_refusal_records_nothing(tmp_path: Path) -> None:
    """Only a pass is recorded, so a refusal leaves nothing behind to replay.

    The same property this repository defends for its judged lint tier and for a plan's
    review: a refused text sent again is judged again, rather than found refused.
    """
    register = _register(tmp_path)

    assert refusal(_envelope(_retry(_task())), register, ScriptedJudge(REFUSES)) is not None
    assert register.added == 0, "a judged refusal was recorded as though it were a pass"

    again = ScriptedJudge(PASSES)
    assert refusal(_envelope(_retry(_task())), register, again) is None
    assert len(again.prompts) == 1, "the refused text was not put to the judge again"


def test_a_judged_turn_that_answers_nothing_is_unanswered_rather_than_refused(
    tmp_path: Path,
) -> None:
    """No verdict is not a verdict either way, and what cleared before it is kept.

    A refusal says the text is wrong and a manager corrects it; this says nothing is
    known, and the manager re-sends once the harness answers. Reading one as the other
    would have a sound edit rewritten. The pass granted to the task before it is still
    recorded, so the re-send pays for the unanswered one alone.
    """
    answers: list[Verdict | OSError] = [PASSES, OSError("the harness printed nothing")]

    def judge(prompt: str) -> Verdict:
        answered = answers.pop(0)
        if isinstance(answered, OSError):
            raise answered
        return answered

    register = _register(tmp_path)
    envelope = _envelope(_retry(_task(), node="sound"), _retry(_task("## Scope\n\nAll of it.")))

    with pytest.raises(ReviewUnanswered) as unanswered:
        refusal(envelope, register, judge)

    assert "the replacement task for node 'work-2' could not be reviewed" in str(unanswered.value)
    assert "the harness printed nothing" in str(unanswered.value)
    assert [held["where"] for held in register.reviews.values()] == [
        "the replacement task for node 'sound'"
    ]


@pytest.mark.parametrize(
    "tier",
    ("criteria_fingerprint", "edit_bar_fingerprint"),
)
def test_the_key_covers_both_tiers(
    tmp_path: Path, tier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record is a claim about text under both bars, so moving either invalidates it.

    Driven by moving each fingerprint in turn where this module reads it — the free
    tier's out of `criteria_guard`, the judged tier's out of `plan_review` — and asking
    whether a recorded task is judged again. A key under one tier alone would let a pass
    stand over a question the other tier stopped asking.
    """
    register = _register(tmp_path)
    assert refusal(_envelope(_retry(_task())), register, ScriptedJudge(PASSES)) is None
    assert register.flush() is None

    module = live_edit_check if tier == "criteria_fingerprint" else plan_review
    monkeypatch.setattr(module, tier, lambda *_: "moved")
    again = ScriptedJudge(PASSES)

    assert refusal(_envelope(_retry(_task())), _register(tmp_path), again) is None
    assert len(again.prompts) == 1, f"a record granted under the old {tier} was reused"


def test_content_a_reply_cleared_is_not_read_again_by_a_later_reply(tmp_path: Path) -> None:
    """The register is what makes a second op over the same text cost nothing.

    Driven as two `Register` objects over one run root, because that is what two replies
    of one run are: the first writes what it cleared and the second reads it back off
    disk, having been constructed with nothing in memory from the first.
    """
    task = _task()
    first = _register(tmp_path)
    assert refusal(_envelope(_retry(task)), first) is None
    assert first.flush() is None

    kept = json.loads((tmp_path / REGISTER).read_text(encoding="utf-8"))
    assert len(kept["reviews"]) == 1, kept
    (cleared,) = kept["reviews"].values()
    assert cleared["where"] == "the replacement task for node 'work-2'", kept

    second = _register(tmp_path)
    assert second.holds(next(iter(kept["reviews"]))), kept
    assert refusal(_envelope(_retry(task, node="work-3")), second) is None
    assert second.added == 0, "the same resulting task was reviewed a second time"


def test_a_task_whose_demand_moved_is_read_again(tmp_path: Path) -> None:
    """The key is over the resulting task, so changed text is changed content.

    The pair is what means anything: a register that never invalidated would be a rubber
    stamp, and one that never held would be an outage. The change here is a whole section
    added, which is exactly the edit that reaches a dispatch unread without this.
    """
    register = _register(tmp_path)
    assert refusal(_envelope(_retry(_task())), register) is None

    said = refusal(_envelope(_retry(_task(AMENDMENT_SECTION))), register)

    assert said is not None and "required checks pass" in said, said


def test_re_indenting_a_task_costs_no_second_review(tmp_path: Path) -> None:
    """The key is over what a task demands rather than over its bytes.

    Whitespace a reviewer could not have ruled on is taken out before the digest, so a
    block re-indented or a run of blank lines closed up is the same content — which is
    what stops this gate costing something for nothing.
    """
    register = _register(tmp_path)
    assert refusal(_envelope(_retry(_task())), register) is None
    assert register.flush() is None

    respaced = _task().replace("\n\n## Why", "\n\n\n\n## Why")

    assert refusal(_envelope(_retry(respaced)), register) is None
    assert register.added == 1, "a re-spaced task was reviewed a second time"


def test_what_cleared_before_a_refusal_is_still_recorded(tmp_path: Path) -> None:
    """A record says the text cleared the bar, not that the envelope went out.

    Nothing in a refused envelope is sent, and a manager corrects one command and sends
    the whole thing again — so recording only what was applied would re-read every other
    command in it, which is the case that most needs to be cheap.
    """
    register = _register(tmp_path)
    envelope = _envelope(_retry(_task(), node="sound"), _retry(_task(AMENDMENT_SECTION)))

    assert refusal(envelope, register) is not None
    assert register.flush() is None
    kept = json.loads((tmp_path / REGISTER).read_text(encoding="utf-8"))
    assert [held["where"] for held in kept["reviews"].values()] == [
        "the replacement task for node 'sound'"
    ], kept


def test_a_register_that_cannot_be_kept_is_named_rather_than_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A saving that stopped happening must not be silent, and must not refuse a reply.

    The envelope was judged and its answer stands; all that is lost is the saving. Driven
    through `main`, which is where the reason is printed, with the register's own path
    held by a directory so the rename cannot land.
    """
    (tmp_path / REGISTER).mkdir()
    monkeyed = json.dumps(_envelope(_retry(_task())))
    stdin, sys.stdin = sys.stdin, io.StringIO(monkeyed)
    try:
        assert main(argv=[str(tmp_path)], judge=ScriptedJudge(PASSES)) == 0
    finally:
        sys.stdin = stdin

    said = capsys.readouterr()
    assert said.out == "", said.out
    assert "could not be kept" in said.err, said.err
    assert "nothing about what was sent is changed" in said.err, said.err


def test_a_run_root_the_ledger_does_not_hold_keeps_nothing_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A root that is not a directory is no run root, and this module invents none.

    The recipe composes the argument from a run reference it has already matched and
    the ledger root the engine configures, so the one thing left to check is that the
    directory exists: writing a register beneath a path nothing holds would be this
    check inventing a run. The reply is still judged and still sent; only the saving is
    lost, and that is said rather than left silent.
    """
    absent = tmp_path / "no-such-run"
    monkeyed = json.dumps(_envelope(_retry(_task())))
    stdin, sys.stdin = sys.stdin, io.StringIO(monkeyed)
    try:
        assert main(argv=[str(absent)], judge=ScriptedJudge(PASSES)) == 0
    finally:
        sys.stdin = stdin

    said = capsys.readouterr()
    assert said.out == "", said.out
    assert f"{absent} is not a run root this ledger holds" in said.err, said.err
    assert not absent.exists(), "a register was written beneath a run root nothing holds"


def test_the_same_words_as_an_amendment_and_as_a_task_are_two_reviews(
    tmp_path: Path,
) -> None:
    """A pass granted to a correction says nothing about the same words stated as a task.

    The two are framed differently and asked different questions, so the key covers
    what the text *is* as well as what it says: an amendment's record cannot be found by
    a `requeue` restating the identical words as a whole task, or the other way round.
    """
    words = "## Acceptance criteria\n\n- The finished tree carries the assertion."
    register = _register(tmp_path)
    assert refusal(_envelope(_amend(words)), register, ScriptedJudge(PASSES)) is None
    assert register.flush() is None

    again = ScriptedJudge(PASSES)
    assert (
        refusal(
            _envelope({"op": "requeue", "id": "work", "amend": {"task": words}}),
            _register(tmp_path),
            again,
        )
        is None
    )
    assert len(again.prompts) == 1, "a whole task reused the review granted to an amendment"


@pytest.mark.parametrize(
    "entry",
    (
        '"cleared"',
        '{"cleared_at": "2026-09-11T00:00:00+00:00"}',
        '{"cleared_at": 7, "where": "the task added as node \'x\'"}',
    ),
    ids=["string", "no-where", "unstring-instant"],
)
def test_a_register_entry_that_is_not_one_this_wrote_is_left_out(
    tmp_path: Path, entry: str
) -> None:
    """Each entry is read only in the shape this module writes, and only under a digest.

    An entry keyed by something that is not a digest, or holding something that is not
    the two fields a record has, is one nothing here wrote — and reading it as a pass
    would let text through that nothing examined. Both are dropped on read, so the text
    they claimed is judged again, while the sound entry beside them is kept.
    """
    task = _task()
    sound = _register(tmp_path)
    assert refusal(_envelope(_retry(task)), sound, ScriptedJudge(PASSES)) is None
    assert sound.flush() is None
    kept = json.loads((tmp_path / REGISTER).read_text(encoding="utf-8"))
    (digest,) = kept["reviews"]
    kept["reviews"]["not-a-digest"] = {"cleared_at": "2026-09-11T00:00:00+00:00", "where": "x"}
    kept["reviews"]["f" * 64] = json.loads(entry)
    (tmp_path / REGISTER).write_text(json.dumps(kept), encoding="utf-8")

    register = _register(tmp_path)

    assert list(register.reviews) == [digest], register.reviews
    assert refusal(_envelope(_retry(task)), register, ScriptedJudge()) is None


@pytest.mark.parametrize(
    "held",
    ("{not json", '"a register"', '{"version": 2, "reviews": {}}', '{"reviews": {}}'),
)
def test_a_register_this_cannot_read_holds_nothing(tmp_path: Path, held: str) -> None:
    """Every unreadable shape answers as an empty register, which is the safe direction.

    Reading a shape it does not understand as a pass would let text through that nothing
    examined, where re-checking costs a repeated run of a deterministic bar.
    """
    (tmp_path / REGISTER).write_text(held, encoding="utf-8")

    said = refusal(_envelope(_retry(_task(AMENDMENT_SECTION))), _register(tmp_path))

    assert said is not None and "required checks pass" in said, said


def test_a_reply_with_no_run_root_keeps_nothing_and_still_judges(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unresolvable run reference is one the recipe forwards, so it hands over none.

    The bar is the same one, so a register with nowhere to write costs a repeated answer
    — and, for a text the free tier takes, a repeated judged turn — rather than a
    different answer; the refusal is unchanged.
    """
    stdin, sys.stdin = (
        sys.stdin,
        io.StringIO(json.dumps(_envelope(_retry(_task(AMENDMENT_SECTION))))),
    )
    try:
        assert main(argv=[""], judge=ScriptedJudge()) == REFUSED
    finally:
        sys.stdin = stdin

    said = capsys.readouterr()
    assert "required checks pass" in said.out, said.out
    assert said.err == "", said.err


#: Where the engine's live-edit table is stated on this host, and the heading under which
#: it opens. The table is the one source of which op carries what, so what this module
#: restates of it is held to it here rather than remembered.
LIVE_EDIT_TABLE = REPO_ROOT / "docs" / "orchestration.md"
LIVE_EDIT_TABLE_OPENS = "The accepted commands are:"


def _live_edit_rows() -> dict[str, str]:
    """Each `op` of the live-edit table, with the required-fields cell it states."""
    text = LIVE_EDIT_TABLE.read_text(encoding="utf-8")
    body = text[text.index(LIVE_EDIT_TABLE_OPENS) :]
    rows: dict[str, str] = {}
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].startswith("`"):
            continue
        rows[cells[0].strip("`")] = cells[1]
        if cells[0] == "`finding`":
            break
    return rows


@pytest.mark.reads_docs
def test_the_task_bearing_ops_are_the_ones_the_live_edit_table_says_carry_a_task() -> None:
    """Which ops put task prose in front of a dispatch is the engine's answer, gated here.

    The module restates three things of the live-edit table — which ops state a whole
    task, in which field each states it, and which op states an amendment — and this is
    what holds the restatement to the table rather than to memory: an op the table
    started giving a node mapping to, or stopped, is a failing check here rather than
    task prose reaching a dispatch unread.
    """
    rows = _live_edit_rows()
    assert rows, f"no live-edit table under {LIVE_EDIT_TABLE_OPENS!r} in {LIVE_EDIT_TABLE}"

    stated_in = {
        op: "node" if "`node`: full" in fields else "amend"
        for op, fields in rows.items()
        if "`node`: full" in fields or "`amend`: partial node overrides" in fields
    }
    assert stated_in == live_edit_check.STATED_IN, (rows, live_edit_check.STATED_IN)
    assert set(stated_in) == set(live_edit_check.WHOLE_TASK_OPS)

    amending = [op for op, fields in rows.items() if fields == "`id`; `text`"]
    assert amending == [live_edit_check.AMEND_OP], rows

    stated = LIVE_EDIT_TABLE.read_text(encoding="utf-8")
    assert f"`{live_edit_check.NO_DISPATCH}: true`" in stated, (
        f"the field a node declares it dispatches nobody by is not the one {LIVE_EDIT_TABLE} "
        f"documents"
    )
    assert "without dispatching onejudge" in stated


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_docs` routes a
# test between two targets of the project that already owns it, rather than standing in
# for a project: this one reads `config/dispatch-appendix.md`, which collection refuses
# without the marker, and so runs under `orchestrator:test-docs`'s whole-workspace key.
@pytest.mark.reads_docs
def test_the_tracked_appendix_itself_is_left_alone() -> None:
    """The exemption proven against the text this host actually requires.

    Every dispatched task carries `config/dispatch-appendix.md` verbatim, and that text
    names `pkill`, `git status`, a `just` invocation and a chained shell command — so a
    reader that examined its section would refuse every task this repository demands.
    Marked `reads_docs` because it opens that tracked prose, which the code-only test key
    does not cover.
    """
    tracked = (REPO_ROOT / APPENDIX).read_text(encoding="utf-8").strip()

    assert (
        refusal(_envelope(_retry(_task(criteria=SOUND_CRITERION).replace(APPENDIX_TEXT, tracked))))
        is None
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def test_a_live_edit_spends_no_plan_level_turn_and_reads_no_plan_level_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-task tiers alone, as `orchestrator/plan_review.py`'s docstring states.

    A novel whole task — a retry's replacement here — owes the judged turn a plan task
    owes and nothing more: the one judged turn it spends is the per-task prompt, never the
    plan-level one, and the project's plan-level record is neither read nor written. A
    model call over the whole plan here would stall every mid-run correction; the
    manager's own review is what covers adoption on a mid-run add.
    """
    monkeypatch.setattr(
        plan_store,
        "project_record",
        lambda _project: pytest.fail("a live edit read the plan-level record"),
    )
    monkeypatch.setattr(
        plan_review,
        "write_plan_record",
        lambda *_: pytest.fail("a live edit wrote a plan-level record"),
    )
    judge = ScriptedJudge(PASSES)

    assert refusal(_envelope(_retry(_task())), judge=judge) is None
    (prompt,) = judge.prompts
    assert prompt.startswith(plan_review.REVIEW_PROMPT), prompt
    assert plan_review.PLAN_REVIEW_PROMPT not in prompt
