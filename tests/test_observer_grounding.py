"""The grounding rule is stated to every observer side, and documented exactly once.

An observer reports what it sees, and a report that calls something a **rule
violation** has to quote the file and line that rule comes from; what it cannot point
at is an observation. That rule is not enforced by any engine — it is prose given to a
model — so the only thing keeping it in force is that all four sides of the two
observer members still carry it and that an operator can still read what it means.

Four sides, because each member is a conversation: the agent side is told to ground a
violation, and the supervisor side is told to reject one that is not grounded. Dropping
either half silently un-enforces the rule for that member — an agent told nothing keeps
inventing rules, and a supervisor told nothing accepts whatever it is handed — while the
other member goes on obeying it, which is the drift this gate exists to refuse.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.root import REPO_ROOT

#: The two observer members of `graphs/dag-scope.yaml`: the monitor that watches every
#: turn, and the pacemaker that reports when its schedule comes due. Neither authors
#: target-project content and both raise planner surfaces, so both can allege a breach.
OBSERVER_PERSONAS = ("orchestrator.yaml", "check-in.yaml")

#: The section an operator reads to learn what the rule means, and the one place it is
#: explained rather than instructed. Each persona is prose handed to one model; a reader
#: deciding whether a surfaced finding is a violation or an observation reads this.
DOCUMENTED = REPO_ROOT / "docs" / "orchestration.md"
SECTION = "### A finding names the rule it is grounded in"

#: What grounding a violation requires, as each side has to still say it. Matched
#: against flattened prose, because these are hard-wrapped YAML block scalars and every
#: phrase here is longer than the line it sits on. Deliberately the *demand* rather than
#: any one wording of the rationale: a persona may be reworded, and this gate should
#: fail only when a side stops making the demand.
GROUNDS_A_VIOLATION = re.compile(r"[Gg]round (?:any|a) (?:claimed )?\*{0,2}rule violation")
QUOTES_THE_SOURCE = re.compile(r"[Qq]uote the file and the line")
REJECTS_THE_UNGROUNDED = re.compile(
    r"Reject a claimed rule violation that does not quote the file and the line"
)
DEMOTES_TO_OBSERVATION = re.compile(r"\bobservation\b")


#: The top-level key that starts the supervisor half of a persona delta. Split on
#: rather than parsed, because the suite carries no YAML reader and this gate needs the
#: two halves apart, not a document: a rule stated only to the agent is advice, and one
#: stated only to the supervisor is enforced against an agent that was never told.
SIDES = "\nuser:\n"


def _flattened(persona: str, side: str) -> str:
    """One side of an observer persona, as one line of prose."""
    written = (REPO_ROOT / "personas" / persona).read_text(encoding="utf-8")
    agent, separator, user = written.partition(SIDES)
    assert separator, f"personas/{persona} has no `user:` block, so it names no review bar"
    return " ".join((agent if side == "agent" else user).split())


@pytest.mark.parametrize("persona", OBSERVER_PERSONAS)
def test_an_observer_is_told_to_ground_a_violation_it_alleges(persona: str) -> None:
    """The agent side names the demand, the source to quote, and the fallback."""
    instructions = _flattened(persona, "agent")

    assert GROUNDS_A_VIOLATION.search(instructions), (
        f"personas/{persona} no longer tells its observer to ground a rule violation, "
        "so nothing stops it reporting a rule it inferred as a breach"
    )
    assert QUOTES_THE_SOURCE.search(instructions), (
        f"personas/{persona} demands grounding without saying what grounds it; the rule "
        "is a quoted file and line, not a recollection"
    )
    assert DEMOTES_TO_OBSERVATION.search(instructions), (
        f"personas/{persona} gives an ungrounded finding nowhere to go, so the observer "
        "must either allege a breach or stay silent about what it saw"
    )


@pytest.mark.parametrize("persona", OBSERVER_PERSONAS)
def test_an_observers_supervisor_sends_back_an_ungrounded_violation(persona: str) -> None:
    """The judge side enforces on the turn, which is what makes the rule more than advice."""
    bar = _flattened(persona, "user")

    assert REJECTS_THE_UNGROUNDED.search(bar), (
        f"personas/{persona}'s supervisor no longer rejects an ungrounded rule violation, "
        "so the agent-side instruction is advice the turn never has to follow"
    )
    assert DEMOTES_TO_OBSERVATION.search(bar), (
        f"personas/{persona}'s supervisor rejects without naming the repair, so a turn it "
        "sends back has no stated way to come back"
    )


@pytest.mark.reads_docs
def test_the_rule_is_explained_where_an_operator_reads_it() -> None:
    """Four instructed sides, one explanation, and the explanation names them all."""
    written = " ".join(DOCUMENTED.read_text(encoding="utf-8").split())

    assert " ".join(SECTION.split()) in written, (
        f"{DOCUMENTED.name} no longer carries {SECTION!r}; the personas would instruct a "
        "rule an operator reading a surfaced finding has nowhere to look up"
    )
    for persona in OBSERVER_PERSONAS:
        assert f"personas/{persona}" in written, (
            f"{DOCUMENTED.name} explains the grounding rule without naming personas/"
            f"{persona} as a place it is stated, so that side can be dropped unnoticed"
        )
