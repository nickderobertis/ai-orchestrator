"""An amendment is held to the criteria bar before the reply carrying it is sent.

An `amend` replaces the binding text that becomes part of a node's effective task, and
that task is what the node's judge reads — so an amendment is criteria, written under
more pressure than a plan ever is. It reaches a node over the live channel rather than
through the plan store, so `just check-plan` never sees one, and until this ran it was
the only criteria on this host that nothing checked.

What is proven here is the reader in front of the bar and the bar's own answer for an
amendment: which of its questions apply, which do not and why not, and that the
envelope shapes around an `amend` are passed over rather than judged.
`tests/ask_seam/test_channel_reply_e2e.py` drives the same refusal through the real
`just channel-reply` against a real run, which is where a manager meets it.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from typing import Any

import pytest

from orchestrator import amendment_check
from orchestrator.amendment_check import REFUSED, amendments, main, refusal
from orchestrator.criteria_guard import (
    CRITERIA_HEADING,
    Bar,
    CriteriaError,
    check,
    check_amendment,
)
from orchestrator.root import REPO_ROOT

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


def test_only_the_amend_op_is_judged() -> None:
    """`note`, `retry` and `requeue` are passed over, each for its own reason.

    A `note` carries no criteria at all unless it states one, and its text is
    observational by construction — refusing one would refuse the very escape every
    refusal here offers. `retry` and `requeue` state a **whole task**, whose
    `## Additional info` carries the commands this bar refuses inside criteria; what
    reads a whole task is `just check-plan`, which reads its criteria block and stops at
    the next heading.
    """
    unjudged = _envelope(
        {"op": "note", "id": "work", "addressee": "both", "text": "run `just gate` first"},
        {"op": "retry", "id": "work", "node": {"id": "work-2", "task": "run `just gate`"}},
        {"op": "requeue", "id": "work", "amend": {"task": "run `just gate`"}},
    )

    assert amendments(unjudged) == []
    assert refusal(unjudged) is None


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
    ),
)
def test_a_shape_this_check_does_not_act_on_is_passed_to_the_verb(envelope: object) -> None:
    """Every shape but an amendment carrying text is the verb's to refuse.

    `onepipeline reply` refuses a malformed envelope naming what it actually received,
    and a second opinion here would replace that with a guess about what was meant. The
    blank text is that case too: the op refuses blank text itself.
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
    """Exit 0 sends the envelope; exit 1 refuses it with the reason and nothing else.

    Both statuses are read by `scripts/channel-reply.sh`, which treats a non-zero exit
    with an empty stdout as this check having failed to run rather than as a verdict —
    so a refusal that printed nothing would refuse a reply nothing had judged.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_envelope(_amend(SOUND)))))
    assert main() == 0
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(_envelope(_amend("The required checks pass."))))
    )
    assert main() == REFUSED
    refused = capsys.readouterr().out
    assert refused.startswith("the amendment for node 'work':"), refused


def test_an_envelope_that_is_not_json_is_left_to_the_verb(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reply this cannot parse is one the verb refuses, naming what it received."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))

    assert main() == 0
    assert capsys.readouterr().out == ""


def test_the_module_is_runnable_as_the_recipe_spawns_it() -> None:
    """`python -m orchestrator.amendment_check`, on the interpreter and path the recipe sets.

    Driven as the process it is rather than through `main`, because that spawn is the
    whole of the seam `scripts/channel-reply.sh` reaches: a module that imported only
    inside this suite's own `sys.path` would refuse nothing when the recipe ran it.
    """
    spawned = subprocess.run(
        [sys.executable, "-m", amendment_check.__name__],
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
    source = (REPO_ROOT / "orchestrator" / "amendment_check.py").read_text(encoding="utf-8")

    assert "import re" not in source, source
    assert "check_amendment" in source, source
    with pytest.raises(CriteriaError, match="required checks pass"):
        check_amendment("The required checks pass.", "the amendment for node 'work'")
