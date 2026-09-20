"""Every live edit stating task prose is held to the criteria bar before it is sent.

Four ops put task prose in front of a dispatch: `amend` replaces the binding correction
composed onto a node's effective task, and `add`, `retry` and `requeue` each state a
whole task; a `note` may carry a criterion beside its text. All of them reach a node over
the planner channel rather than through the plan store, so `just check-plan` never sees
one and `just review-plan` never records one. `config/onemessagebus.yaml` therefore
declares :mod:`orchestrator.envelope_review` as the command validator on the `replies`
queue, and the bus runs it — for `just channel-reply` and for the engine alike — before
anything is appended, caching only a pass.

What is proven here is what that validator decides on its own: which of the bar's
questions apply to each carrier, that a whole task is read past the block one heading
opens, that the envelope shapes around them are passed over rather than judged, that a
text the free tier takes is then put to one judged turn, that a node the envelope states
is held to the plan tier's structural rules over the graph the envelope's own nodes form,
and the exit protocol the bus reads — ``0`` pass, ``1`` refuse with stderr as the reason,
``2`` unjudged — with ``--bar-fingerprint`` printing what the bus keys its pass cache on.
The envelope is judged as it states itself: no run is read, so nothing here builds one.
`tests/ask_seam/channel_reply/test_channel_reply_e2e.py` drives the same validator through
the real `just channel-reply` and the real bus against a launched run, with the real `oneharness`
spending the judged turn against a scripted provider.

The judged turn is stood in at the one boundary it crosses into this module — the
``judge`` :func:`orchestrator.envelope_review.refusal` and :func:`main` take — and nowhere
below it: a scripted judge answers with a verdict, or fails the test when a turn is spent
that the journey says must not be.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from onevcs_stand_in import install_three_verb_stand_in, release_answer

from orchestrator import adoption_guard, criteria_guard, envelope_review, plan_review, plan_store
from orchestrator.criteria_guard import (
    APPENDIX,
    CRITERIA_HEADING,
    Bar,
    CriteriaError,
    check,
    check_amendment,
)
from orchestrator.envelope_review import (
    ADDITIONAL_INFO_HEADING,
    AMENDMENT_HEADING,
    AMENDMENT_PRECEDENCE,
    BAR_FINGERPRINT_FLAG,
    REFUSED,
    UNJUDGED,
    Judge,
    ReviewUnanswered,
    amended,
    bar_in_force,
    main,
    reviewables,
    stated_graph,
    structural_refusal,
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


def refusal(envelope: object, judge: Judge | None = None) -> str | None:
    """Why ``envelope`` is refused, with a judge that passes everything by default.

    So a refusal a journey reads is the free tier's unless it scripted otherwise.
    """
    return envelope_review.refusal(envelope, ScriptedJudge(PASSES) if judge is None else judge)


#: An amendment stating what the finished tree must carry, which is what every refusal
#: below asks its author for. It names a file and a behaviour and prescribes no route to
#: either, so nothing in the bar has anything to say about it.
SOUND = (
    "The finished tree carries an assertion whose subject is the behaviour this change "
    "adds, so removing that behaviour fails it."
)


def _envelope(*commands: dict[str, Any]) -> dict[str, Any]:
    """One reply envelope carrying ``commands``, as the bus hands the validator one.

    `Any` because a reply envelope is the planner channel's own open contract and its
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


def test_a_sound_amendment_is_not_refused_and_spends_no_judged_turn() -> None:
    """The half that makes the refusals worth having: a property passes untouched.

    And it passes on the free tier alone: an amendment is a correction written in the
    minute after a manager reads a failure, so the judge is scripted to fail if asked.
    """
    (found,) = reviewables(_envelope(_amend(SOUND)))

    assert found.text == SOUND and not found.whole_task and not found.judged
    assert found.where == "the amendment for node 'work'"
    assert refusal(_envelope(_amend(SOUND)), ScriptedJudge()) is None


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


# `Any` because a command is the channel's open JSON contract — its values are whatever JSON
# the op carries — and that openness is what `orchestrator.envelope_review` reads.
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
    """The half that makes the refusal worth having, and the exemption proven.

    The task carries an appendix naming `pkill`, `git status`, a `just` invocation and a
    chained shell command, so a reader that examined that section would refuse it. It is
    not refused, which is what says
    :data:`~orchestrator.criteria_guard.DESCRIBED_ELSEWHERE` really exempts the one
    section every dispatched task is required to carry.
    """
    assert refusal(_envelope(_retry(_task()))) is None


def test_a_replacement_task_is_held_to_the_criteria_bar_over_its_own_block() -> None:
    """A whole task's criteria are asked the whole bar, exactly as a plan's are."""
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


def test_a_requeue_is_judged_under_the_persona_its_overrides_state_and_no_other() -> None:
    """The overrides are read as they are stated, because no run is read.

    A `requeue` stating a `researcher` persona beside criteria requiring a file to change
    is refused under that role's bar, naming the clause, before any turn is spent; the
    same overrides stating no persona are judged under the base config's generic contract,
    which the parked node's own persona is not folded into here. That is the composition
    this validator gave up so the bus's pass cache stays sound: the same bytes must pass
    or fail whatever the run holds.
    """
    changes = _task(criteria="- `docs/x.md` gains a row.")
    stated = {"op": "requeue", "id": "other", "amend": {"task": changes, "persona": "researcher"}}

    said = refusal(_envelope(stated), ScriptedJudge())

    assert said is not None
    assert "the amended task for node 'other'" in said, said
    assert "modified project files" in said, said

    unstated = {"op": "requeue", "id": "other", "amend": {"task": changes}}
    (found,) = reviewables(_envelope(unstated))
    assert found.persona is None and found.whole_task and found.judged


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
    refusal here offers. `cancel` states no prose, and a `requeue` amending only a turn
    budget states no task either.
    """
    unjudged = _envelope(
        {"op": "note", "id": "work", "addressee": "both", "text": "run `just gate` first"},
        {"op": "cancel", "id": "work", "reason": "run `just gate` instead"},
        {"op": "requeue", "id": "work", "amend": {"max_turns": 40}},
    )

    assert reviewables(unjudged) == []
    assert refusal(unjudged, ScriptedJudge()) is None


def test_a_node_nothing_dispatches_from_is_passed_over() -> None:
    """A node declaring `expects_no_diff` settles without a worker, so no judge reads it.

    The way a manager adds a journal bookmark mid-run is `{"task": "Report.",
    "expects_no_diff": true}` — a node with no persona and no acceptance criteria, which
    the engine settles `done (no-changes)` without a dispatch. A refusal over prose no
    judge reads holds nobody to anything.
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
    assert refusal(unjudged, ScriptedJudge()) is None


def test_a_step_nothing_dispatches_from_is_passed_over_while_its_siblings_are_read() -> None:
    """A step may declare `expects_no_diff` of itself, and only that step is left out."""
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


# `Any` for the reason `_retry` gives: a note's criterion may be any JSON, and a journey
# below holds the review to one that is not text.
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

    So it is asked an amendment's two questions and owes no judged turn, exactly as an
    amendment does. The text beside it names a route on purpose and is not read. The
    refusal names the field and the escape that fits it, which is the note's own `text`
    rather than "a note".
    """
    said = refusal(_envelope(_note("run `just gate` first", "The required checks pass.")))

    assert said is not None
    assert said.startswith("the criterion of the note for node 'work':"), said
    assert "required checks pass" in said, said
    assert "belongs in the note's `text`" in said, said

    (found,) = reviewables(_envelope(_note("run `just gate` first", SOUND)))
    assert found.text == SOUND
    assert not found.whole_task and not found.judged
    assert refusal(_envelope(_note("run `just gate` first", SOUND)), ScriptedJudge()) is None


def test_a_note_naming_no_node_is_still_named_in_its_refusal() -> None:
    """A refusal must never read as though it were about no node at all."""
    said = refusal(_envelope(_note("x", "The required checks pass.", node=None)))

    assert said is not None
    assert said.startswith("the criterion of the note in commands[0]:"), said


@pytest.mark.parametrize("criterion", ("", "   ", 7))
def test_a_notes_criterion_that_is_blank_or_not_text_is_left_to_the_engine(
    criterion: object,
) -> None:
    """Blank and non-string criteria are the engine's to refuse, as an amend's text is."""
    # `cast` because the wrong type is the subject: `_note` is typed to what a manager
    # sends, and the `7` here is what the engine refuses on its own, sent to prove that
    # the reader leaves it rather than refusing it under a name of its own.
    assert reviewables(_envelope(_note("x", cast(Any, criterion)))) == []


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


def test_a_node_stating_its_own_amendment_is_read_with_it_rendered_in() -> None:
    """An `add` or `retry` stating an `amendment` is text its judge reads, so it is read.

    The amendment renders into the node's task exactly as the engine renders it, and the
    whole resulting task is asked the bar — so a sound task carrying an amendment that
    rests on the merge path is refused naming the `## Amendment` section, and a sound one
    is read as the novel whole task it is and spends its judged turn.
    """
    node = {"id": "again", "persona": "engineer", "task": _task(), "amendment": SOUND}
    (found,) = reviewables(_envelope({"op": "add", "node": node}))

    assert found.text == amended(_task(), SOUND)
    assert found.whole_task and found.judged
    assert found.where == "the task added as node 'again'"

    judge = ScriptedJudge(PASSES)
    assert refusal(_envelope({"op": "add", "node": node}), judge) is None
    assert len(judge.prompts) == 1 and SOUND in judge.prompts[0]

    harmful = {**node, "amendment": "The required checks pass."}
    said = refusal(_envelope({"op": "add", "node": harmful}), ScriptedJudge())
    assert said is not None
    assert "the task added as node 'again', under '## Amendment'" in said, said


def test_a_bare_amendment_spends_no_judged_turn_and_a_novel_task_does() -> None:
    """The line is drawn at what the content is, not at which op carried it.

    An amendment is a correction to a task a review already cleared; it is answered by
    the free tier alone. An added node, a retry's replacement and a requeued node's
    amended task are complete authored tasks nothing holds a pass for, and each would
    have been reviewed had it arrived in the plan.
    """
    assert refusal(_envelope(_amend(SOUND)), ScriptedJudge()) is None

    judged = ScriptedJudge(PASSES)
    novel = _envelope(
        {"op": "add", "node": {"id": "extra", "persona": "engineer", "task": _task()}},
        _retry(_task("## Scope\n\nAll.")),
        {"op": "requeue", "id": "other", "amend": {"task": _task("## Scope\n\nNone.")}},
    )
    assert refusal(novel, judged) is None
    assert len(judged.prompts) == 3, "a novel whole task did not spend its judged turn"


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
def test_a_shape_this_check_does_not_act_on_is_left_to_the_engine(envelope: object) -> None:
    """Every shape but a command stating task prose is the engine's to refuse.

    The engine refuses a malformed command naming what it actually received, and a second
    opinion here would replace that with a guess about what was meant. The blank text is
    that case too: the op refuses blank text itself. So is every node shape the plan
    walker refuses — a `retry` stating no node, one whose node is not a mapping, one that
    dispatches an agent and states no task, one whose id is not a string — and a
    `kind: human` node, which carries an action a person performs rather than a task a
    judge reads.
    """
    assert refusal(envelope, ScriptedJudge()) is None


def test_an_amendment_naming_no_node_is_still_named_in_its_refusal() -> None:
    """A refusal must never read as though it were about no node at all.

    An `amend` without an `id` is a shape the engine refuses on its own, but if this is
    reached first, the position in the envelope is what a manager can act on, where
    `None` would be a refusal naming nothing.
    """
    said = refusal({"commands": [{"op": "amend", "text": "the required checks pass"}]})

    assert said is not None
    assert said.startswith("the amendment in commands[0]:"), said


def test_the_first_refused_resulting_task_is_the_one_reported() -> None:
    """The bus refuses the whole envelope, so one reason is the whole answer."""
    said = refusal(
        _envelope(
            _amend(SOUND, node="first"),
            _amend("The required checks pass.", node="second"),
            _amend("Run `just gate`.", node="third"),
        )
    )

    assert said is not None and said.startswith("the amendment for node 'second':"), said


def test_the_bar_itself_is_the_plan_checks_and_is_not_restated_here() -> None:
    """One source for the criteria bar, so a change to it reaches a live edit too.

    `orchestrator/criteria_guard.py` is where the questions live and where the choice of
    which apply to an amendment is written down; this module is the envelope reader in
    front of it. So what is asserted is that the module carries no pattern of its own and
    that a whole task's refusal is exactly the plan tier's entry point's words.
    """
    source = Path(envelope_review.__file__).read_text(encoding="utf-8")
    assert "import re" not in source, source
    with pytest.raises(CriteriaError, match="required checks pass"):
        check_amendment("The required checks pass.", "the amendment for node 'work'")

    task = _task(criteria="- Run `just gate` and report it green.")
    with pytest.raises(CriteriaError) as plan_tier:
        criteria_guard.check_whole_task(
            task, "the task added as node 'x'", criteria_guard.resolve_bar("engineer")
        )
    said = refusal(
        _envelope({"op": "add", "node": {"id": "x", "persona": "engineer", "task": task}})
    )
    assert said == str(plan_tier.value)


def test_a_whole_task_the_free_tier_takes_is_put_to_one_judged_turn() -> None:
    """The second tier, and the one a deterministic-only reading of this check left out.

    The rules that moved out of the deterministic tier — whether a number is the right
    number, whether the criteria answer a demand their own bar makes, whether a criterion
    could be falsified — are asked by a judge. So a whole task the free tier takes is put
    to one turn of the same reviewer `just review-plan` spends one of, framed for what a
    live edit is, and every finding it names is reported, naming the text the way the
    refusal does.
    """
    judge = ScriptedJudge(REFUSES)

    said = refusal(_envelope(_retry(_task())), judge)

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
    said = refusal(_envelope(_retry(_task(AMENDMENT_SECTION))), ScriptedJudge())

    assert said is not None and "required checks pass" in said, said


def test_a_judged_turn_that_answers_nothing_is_unanswered_rather_than_refused() -> None:
    """No verdict is not a verdict either way.

    A refusal says the text is wrong and a manager corrects it; this says nothing is
    known, and the manager re-sends once the harness answers. Reading one as the other
    would have a sound edit rewritten. The task before it passed its turn and is not
    what the exception names.
    """
    answers: list[Verdict | OSError] = [PASSES, OSError("the harness printed nothing")]

    def judge(prompt: str) -> Verdict:
        answered = answers.pop(0)
        if isinstance(answered, OSError):
            raise answered
        return answered

    envelope = _envelope(_retry(_task(), node="sound"), _retry(_task("## Scope\n\nAll of it.")))

    with pytest.raises(ReviewUnanswered) as unanswered:
        envelope_review.refusal(envelope, judge)

    assert "the replacement task for node 'work-2' could not be reviewed" in str(unanswered.value)
    assert "the harness printed nothing" in str(unanswered.value)
    assert "'sound'" not in str(unanswered.value)


@pytest.mark.parametrize(
    ("module", "tier"),
    ((envelope_review, "criteria_fingerprint"), (plan_review, "edit_bar_fingerprint")),
    ids=("deterministic", "judged"),
)
def test_the_bar_fingerprint_moves_with_either_tier(
    module: object, tier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the bus keys every recorded pass on covers both tiers of the bar.

    Driven by moving each tier's own fingerprint where this module reads it — the free
    tier's out of `criteria_guard`, the judged tier's out of `plan_review` — and asking
    whether what `--bar-fingerprint` prints moved with it. A digest over one tier alone
    would let the bus's cached pass stand over a question the other tier stopped asking.
    """
    before = bar_in_force()
    assert bar_in_force() == before, "the fingerprint is not a function of the bar alone"

    monkeypatch.setattr(module, tier, lambda *_: "moved")

    assert bar_in_force() != before, f"moving the {tier} left the bar's fingerprint unchanged"


def _main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stdin: str,
    judge: Judge,
    argv: list[str] | None = None,
) -> tuple[int, str, str]:
    """``main``'s exit status, stdout and stderr for ``stdin``, as the bus runs it."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    status = main([] if argv is None else argv, judge=judge)
    said = capsys.readouterr()
    return status, said.out, said.err


def test_the_validator_answers_on_the_exit_protocol_the_bus_reads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 0 passes; 1 refuses with stderr as the reason; 2 is unjudged.

    The bus discards a validator's stdout, so every word a manager is told is on stderr,
    and a pass says nothing at all. A turn that answered nothing is neither verdict: it
    exits with the status the bus reads as unjudged, and its repair is to send the same
    envelope again rather than to correct it.
    """
    sound = json.dumps(_envelope(_amend(SOUND)))
    assert _main(monkeypatch, capsys, sound, ScriptedJudge()) == (0, "", "")

    harmful = json.dumps(_envelope(_amend("The required checks pass.")))
    status, out, err = _main(monkeypatch, capsys, harmful, ScriptedJudge())
    assert (status, out) == (REFUSED, "")
    assert err.startswith(
        "this reply states task prose a judge would hold its worker to as work — "
        "the amendment for node 'work':"
    ), err
    assert "nothing was sent" in err and "correct it" in err, err

    whole = json.dumps(_envelope(_retry(_task())))
    status, out, err = _main(monkeypatch, capsys, whole, ScriptedJudge(REFUSES))
    assert (status, out) == (REFUSED, "")
    assert "the replacement task for node 'work-2' was refused by its judged review" in err, err

    def unanswered(prompt: str) -> Verdict:
        raise OSError("no candidate answered the review; run `oneharness doctor`")

    status, out, err = _main(monkeypatch, capsys, whole, unanswered)
    assert (status, out) == (UNJUDGED, "")
    assert "could not be judged" in err, err
    assert "the replacement task for node 'work-2' could not be reviewed" in err, err
    assert "send the whole envelope again unchanged" in err, err
    assert "correct it" not in err, err


def test_an_envelope_that_is_not_json_is_unjudged_rather_than_passed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing is known about bytes this cannot read, and the bus sends nothing unjudged.

    The bus parses an envelope before it runs a validator, so this is a broken hand-over
    rather than a manager's typo — and a pass here would send an envelope nothing read.
    """
    status, out, err = _main(monkeypatch, capsys, "{not json", ScriptedJudge())

    assert (status, out) == (UNJUDGED, "")
    assert "is not JSON" in err and "nothing was sent" in err, err


def test_an_argument_the_validator_does_not_take_is_unjudged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A configuration naming the validator with another argument judges nothing.

    Read as a pass it would wave every envelope through; read as a refusal it would tell
    a manager their sound edit is wrong. So it is unjudged, naming what it takes.
    """
    sound = json.dumps(_envelope(_amend(SOUND)))
    status, out, err = _main(monkeypatch, capsys, sound, ScriptedJudge(), ["run-1"])

    assert (status, out) == (UNJUDGED, "")
    assert "['run-1'] is not what this validator takes" in err, err
    assert BAR_FINGERPRINT_FLAG in err, err


def test_the_bar_fingerprint_flag_prints_the_bar_in_force(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--bar-fingerprint` prints :func:`bar_in_force` alone, reading no envelope."""
    status, out, err = _main(monkeypatch, capsys, "", ScriptedJudge(), [BAR_FINGERPRINT_FLAG])

    assert (status, err) == (0, "")
    assert out == f"{bar_in_force()}\n"


def test_main_reads_its_arguments_from_the_process_when_handed_none(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bus runs the module with its own argv, which is what `main()` reads by default."""
    monkeypatch.setattr(sys, "argv", ["envelope_review", BAR_FINGERPRINT_FLAG])

    assert main(judge=ScriptedJudge()) == 0
    assert capsys.readouterr().out == f"{bar_in_force()}\n"


def test_the_module_is_runnable_as_the_validator_script_spawns_it() -> None:
    """`python -m orchestrator.envelope_review` from the checkout root, as a process.

    `scripts/envelope-review.sh` changes to the checkout root and runs exactly this module,
    and the bus's pass cache runs it again with `--bar-fingerprint`. Driven as the process
    it is rather than through `main`, because a module that imported only inside this
    suite's own `sys.path` would refuse nothing when the bus ran it.
    """
    environment = {"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}
    spawned = subprocess.run(
        [sys.executable, "-m", envelope_review.__name__],
        cwd=REPO_ROOT,
        env=environment,
        input=json.dumps(_envelope(_amend("The required checks pass."))),
        text=True,
        capture_output=True,
        check=False,
    )

    assert spawned.returncode == REFUSED, spawned.stderr
    assert spawned.stdout == "", spawned.stdout
    assert "the amendment for node 'work':" in spawned.stderr, spawned.stderr

    printed = subprocess.run(
        [sys.executable, "-m", envelope_review.__name__, BAR_FINGERPRINT_FLAG],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert printed.returncode == 0, printed.stderr
    assert printed.stdout.strip() == bar_in_force()


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
    what holds the restatement to the table rather than to memory.
    """
    rows = _live_edit_rows()
    assert rows, f"no live-edit table under {LIVE_EDIT_TABLE_OPENS!r} in {LIVE_EDIT_TABLE}"

    stated_in = {
        op: "node" if "`node`: full" in fields else "amend"
        for op, fields in rows.items()
        if "`node`: full" in fields or "`amend`: partial node overrides" in fields
    }
    assert stated_in == envelope_review.STATED_IN, (rows, envelope_review.STATED_IN)
    assert set(stated_in) == set(envelope_review.WHOLE_TASK_OPS)

    amending = [op for op, fields in rows.items() if fields == "`id`; `text`"]
    assert amending == [envelope_review.AMEND_OP], rows

    stated = LIVE_EDIT_TABLE.read_text(encoding="utf-8")
    assert f"`{envelope_review.NO_DISPATCH}: true`" in stated, (
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
    plan-level one, and the project's plan-level record is neither read nor written.
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

    assert refusal(_envelope(_retry(_task())), judge) is None
    (prompt,) = judge.prompts
    assert prompt.startswith(plan_review.REVIEW_PROMPT), prompt
    assert plan_review.PLAN_REVIEW_PROMPT not in prompt


#: A producer declaring the wheel this host installs, as `onevcs release targets` answers
#: for its repository — which is what makes a dependency landing there one that releases.
RELEASING = release_answer("id/library", ("pypi", "pypi:onepipeline-cli"))

#: The node whose repository releases, as an envelope states it.
PRODUCER_NODE: dict[str, object] = {
    "id": "producer",
    "title": "feat: release the library",
    "repo": "library",
    "expects_no_diff": True,
    "task": "Release.",
}


def _consumer_node(**fields: object) -> dict[str, object]:
    """A node landing in `service` behind the producer, adopting `fast`."""
    return {
        "id": "consumer",
        "title": "feat: adopt the release",
        "repo": "service",
        "deps": ["producer"],
        "adoption": "fast",
        "expects_no_diff": True,
        "task": "Adopt.",
        **fields,
    }


def _releasing_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`onevcs` answering that both repositories publish `local-direct` and `library` releases."""
    return install_three_verb_stand_in(
        tmp_path / "onevcs",
        monkeypatch,
        releases={"library": RELEASING, "service": release_answer("id/service")},
        resolved={
            "library": ("/checkouts/library", "local-direct"),
            "service": ("/checkouts/service", "local-direct"),
        },
    )


def test_a_stated_node_behind_a_stated_release_is_refused_in_the_plan_rules_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An envelope adding a releasing producer and a `fast` consumer behind it, refused.

    The consumer publishes `local-direct` behind a dependency whose repository releases —
    the shape `orchestrator/adoption_guard.py` refuses at the plan tier — so it is refused
    here in that rule's own words, naming the node and the field, before any judged turn:
    the judge is scripted to fail if asked.
    """
    _releasing_host(tmp_path, monkeypatch)
    envelope = _envelope(
        {"op": "add", "node": PRODUCER_NODE}, {"op": "add", "node": _consumer_node()}
    )

    status, out, err = _main(monkeypatch, capsys, json.dumps(envelope), ScriptedJudge())

    assert (status, out) == (REFUSED, "")
    (plan_tier,) = adoption_guard.refusals({"tasks": [PRODUCER_NODE, _consumer_node()]})
    assert err.startswith(
        "this reply states a node this host's structural plan rules refuse — "
        f"node 'consumer', as this reply states it: adoption: {plan_tier.reason}\n"
    ), err
    assert "adopts `fast`" in err and "'local-direct'" in err, err
    assert "correct the field each refusal names" in err, err


def test_the_correction_the_refusal_names_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A policy opening a change request is accepted, after the rules really asked `onevcs`."""
    asked = _releasing_host(tmp_path, monkeypatch)
    envelope = _envelope(
        {"op": "add", "node": PRODUCER_NODE},
        {"op": "add", "node": _consumer_node(merge_policy="change-open")},
    )

    assert structural_refusal(envelope) is None
    assert "release targets library --json" in asked.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "remedy",
    (
        {"op": "reparent", "id": "consumer", "deps": ["gate"]},
        {"op": "drop", "id": "producer", "dependents": "detach"},
    ),
    ids=("reparent", "drop"),
)
def test_a_release_edge_a_later_command_removes_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remedy: dict[str, object]
) -> None:
    """The rules are asked over the graph the whole envelope forms, folded in order.

    "Drop the edge if the work does not need the release" is the refusal's third remedy,
    and a `reparent` of the consumer, or a detaching `drop` of the producer, later in the
    same envelope takes it.
    """
    _releasing_host(tmp_path, monkeypatch)
    envelope = _envelope(
        {"op": "add", "node": PRODUCER_NODE}, {"op": "add", "node": _consumer_node()}, remedy
    )

    assert structural_refusal(envelope) is None


def test_a_retrys_replacement_is_held_to_the_same_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement node is a node like any an `add` states, so it is asked too."""
    _releasing_host(tmp_path, monkeypatch)
    envelope = _envelope(
        {"op": "add", "node": PRODUCER_NODE},
        {"op": "retry", "id": "consumer", "node": _consumer_node(id="again")},
    )

    refused = structural_refusal(envelope)

    assert refused is not None
    assert refused.startswith("node 'again', as this reply states it: adoption: "), refused


def test_an_envelope_stating_no_node_asks_onevcs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A note, an amendment, a cancel and a requeue state no node, so nothing is asked.

    The requeue is the case to read twice: it states overrides of a node the run holds,
    and this validator reads no run, so it moves nothing here and the engine and the merge
    path are what refuse the node it returns.
    """
    asked = _releasing_host(tmp_path, monkeypatch)
    envelope = _envelope(
        _amend(SOUND, "consumer"),
        _note("Keep going.", node="consumer"),
        {"op": "cancel", "id": "consumer", "reason": "park it"},
        {"op": "requeue", "id": "consumer", "amend": {"adoption": "fast"}},
    )

    assert structural_refusal(envelope) is None
    assert structural_refusal("not an envelope") is None
    assert not asked.exists(), asked.read_text(encoding="utf-8")


def test_the_graph_an_envelope_forms_folds_every_command_in_order() -> None:
    """Each command reads what the one before it did, as the engine applies them.

    An added node is retried and its dependent redirected; an added node is reparented; a
    cascading drop takes its dependents with it and a detaching one takes only their
    edges; a node added and dropped in one envelope is not one it results in; and every
    command naming a node the envelope has not stated, or stating no shape the engine
    accepts, moves nothing.
    """
    envelope = _envelope(
        {"op": "add", "node": {"id": "gate", "kind": "human"}},
        {"op": "add", "node": {"id": "work", "deps": ["gate"]}},
        {"op": "add", "node": {"id": "after", "deps": ["work"]}},
        {"op": "add", "node": {"id": "tail", "deps": ["after", "gate"]}},
        {"op": "add", "node": {"id": "loose", "deps": ["gate"]}},
        {"op": "requeue", "id": "work", "amend": {"adoption": "fast"}},
        {"op": "retry", "id": "work", "node": {"id": "work-2", "deps": ["gate"]}},
        {"op": "add", "node": {"id": "fresh", "deps": ["work-2"]}},
        {"op": "reparent", "id": "fresh", "deps": ["gate"]},
        {"op": "drop", "id": "after", "dependents": "drop"},
        {"op": "drop", "id": "gate", "dependents": "detach"},
        {"op": "add", "node": {"id": "doomed"}},
        {"op": "drop", "id": "doomed", "dependents": "detach"},
        {"op": "add", "node": {"task": "no id"}},
        {"op": "reparent", "id": "absent", "deps": []},
        # `cast` because this entry is deliberately not a command at all: an envelope is
        # the channel's open contract, and the fold is held to passing over what it
        # cannot read as one.
        cast(Any, "not a command"),
    )

    graph, resulting = stated_graph(envelope)

    assert graph == {
        "loose": {"id": "loose", "deps": []},
        "work-2": {"id": "work-2", "deps": []},
        "fresh": {"id": "fresh", "deps": []},
    }
    assert resulting == ["loose", "work-2", "fresh"]
    assert stated_graph({"version": 2}) == ({}, [])


def test_a_command_the_engine_refuses_for_its_target_moves_nothing() -> None:
    """A retry onto an id the envelope holds, an add of one, and every shape the engine refuses.

    Each is refused by the engine for its target or its shape, so none results in a node
    the rules are asked about — a node that will never be committed would otherwise be
    refused here for fields no graph will ever carry. A retry of a node the envelope did
    not state is the ordinary retry of a run's node, and puts its replacement in.
    """
    held = _envelope(
        {"op": "add", "node": {"id": "gate", "kind": "human"}},
        {"op": "add", "node": {"id": "work", "deps": ["gate"]}},
    )
    envelope = _envelope(
        *held["commands"],
        {"op": "retry", "id": "work", "node": {"id": "gate"}},
        {"op": "add", "node": {"id": "work", "deps": []}},
        {"op": "add", "node": {"id": "unknown", "surprise": True}},
        {"op": "add", "node": {"id": "extra"}, "surprise": True},
        {"op": "retry", "id": "work", "node": {"id": "work-3"}, "surprise": True},
        {"op": "requeue", "id": "gate", "amend": {"id": "rewritten"}},
        {"op": "reparent", "id": "work", "deps": ["gate", 7]},
        {"op": "reparent", "id": "work", "deps": [], "surprise": True},
        {"op": "drop", "id": "gate", "dependents": "unknown"},
        {"op": "drop", "id": "gate", "dependents": "detach", "surprise": True},
    )

    assert stated_graph(envelope) == stated_graph(held)
    graph, resulting = stated_graph(
        _envelope({"op": "retry", "id": "run-node", "node": {"id": "replacement", "deps": ["x"]}})
    )
    assert graph == {"replacement": {"id": "replacement", "deps": ["x"]}}
    assert resulting == ["replacement"]


def test_a_stated_node_sizing_its_own_workspace_placement_is_read_as_one() -> None:
    """`pool` and `overflow` are fields the engine's `Node` accepts, so a node stating them is read.

    They arrived with onepipeline 0.40.0 and are copied onto the session request a
    lifecycle step opens with; a preflight that did not know them would pass over a
    node the engine commits, and its task would reach a dispatch unjudged.
    """
    graph, resulting = stated_graph(
        _envelope(
            {"op": "add", "node": {"id": "placed", "pool": 0, "overflow": "unlimited"}},
            {"op": "add", "node": {"id": "unplaced", "slot": 1}},
        )
    )

    assert graph == {"placed": {"id": "placed", "pool": 0, "overflow": "unlimited"}}
    assert resulting == ["placed"]


def test_the_graph_an_envelope_forms_carries_deps_and_consumes_as_the_engine_does() -> None:
    """The fields the adoption rules read move with the edges, as `edits.rs` moves them.

    A retry whose replacement states no deps inherits the superseded node's `deps` and
    `consumes`, and one stating no `delivers` inherits those on that condition alone — a
    replacement restating its deps still takes the tickets over, one stating its own keeps
    them, and one whose predecessor stated tickets in no shape the engine gives the field
    inherits none — while a dependent redirected onto it takes its `consumes` entry along; a
    reparent keeps only the `consumes` keyed on a dependency it still names; and a node
    that leaves the graph is consumed by nothing — its detached dependents lose the edge
    and the entry, and a node that consumed it without the edge loses the entry too. The
    envelope's own commands are left untouched.
    """
    envelope = _envelope(
        {"op": "add", "node": {"id": "producer", "repo": "library"}},
        {"op": "add", "node": {"id": "gate", "kind": "human"}},
        {
            "op": "add",
            "node": {"id": "work", "deps": ["producer"], "consumes": {"producer": "pypi"}},
        },
        {"op": "add", "node": {"id": "after", "deps": ["work"], "consumes": {"work": "pypi"}}},
        {
            "op": "add",
            "node": {
                "id": "keep",
                "deps": ["producer", "gate"],
                "consumes": {"producer": "pypi"},
            },
        },
        {"op": "add", "node": {"id": "stale", "consumes": {"producer": "pypi"}}},
        {"op": "add", "node": {"id": "bare", "deps": "gate"}},
        {
            "op": "add",
            "node": {"id": "ticketed", "deps": ["gate"], "delivers": ["followups:issue-7"]},
        },
        {"op": "add", "node": {"id": "reticketed", "delivers": ["followups:issue-8"]}},
        {"op": "add", "node": {"id": "misticketed", "delivers": "followups:issue-10"}},
        {"op": "retry", "id": "bare", "node": {"id": "bare-2"}},
        {"op": "retry", "id": "work", "node": {"id": "work-2"}},
        {"op": "retry", "id": "ticketed", "node": {"id": "ticketed-2", "deps": ["gate"]}},
        {
            "op": "retry",
            "id": "reticketed",
            "node": {"id": "reticketed-2", "delivers": ["followups:issue-9"]},
        },
        {"op": "retry", "id": "misticketed", "node": {"id": "misticketed-2"}},
        {"op": "add", "node": {"id": "emptied", "delivers": ["followups:issue-11"]}},
        {"op": "retry", "id": "emptied", "node": {"id": "emptied-2", "delivers": []}},
        {"op": "reparent", "id": "keep", "deps": ["gate"]},
        {"op": "drop", "id": "producer", "dependents": "detach"},
    )
    sent = json.loads(json.dumps(envelope))

    graph, resulting = stated_graph(envelope)

    assert graph == {
        "gate": {"id": "gate", "kind": "human"},
        "after": {"id": "after", "deps": ["work-2"], "consumes": {"work-2": "pypi"}},
        "keep": {"id": "keep", "deps": ["gate"], "consumes": {}},
        "stale": {"id": "stale", "consumes": {}},
        "bare-2": {"id": "bare-2", "deps": []},
        "work-2": {"id": "work-2", "deps": [], "consumes": {}},
        "ticketed-2": {"id": "ticketed-2", "deps": ["gate"], "delivers": ["followups:issue-7"]},
        "reticketed-2": {"id": "reticketed-2", "deps": [], "delivers": ["followups:issue-9"]},
        # A `delivers` not in the engine's shape is the engine's to refuse, not to inherit.
        "misticketed-2": {"id": "misticketed-2", "deps": []},
        # `[]` states none, as an absent field does: the engine's `is_empty()`.
        "emptied-2": {"id": "emptied-2", "deps": [], "delivers": ["followups:issue-11"]},
    }
    assert resulting == [
        "gate",
        "after",
        "keep",
        "stale",
        "bare-2",
        "work-2",
        "ticketed-2",
        "reticketed-2",
        "misticketed-2",
        "emptied-2",
    ]
    assert envelope == sent, "folding the graph rewrote the envelope's own commands"
