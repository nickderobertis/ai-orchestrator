"""The engine's reading of one run, and whether `just follow-ups` may verify its drafts.

`onepipeline status <run> --json` answers one document — the run's word, whether it is
driven, and how it ended or what it is paused on — which the engine publishes as
`schemas/run-reading.schema.json`. `scripts/follow-ups.sh` hands that answer to this
module on stdin and acts on the one line it prints:

* ``ended`` — the reading carries an ending, of any kind, so the run's drafts are final;
* ``driven <word>`` — something still drives the run;
* ``paused <what waits>`` — nothing drives it, and it waits on a decision;
* ``crashed <word>`` — nothing drives it, it has not ended, and nothing is decided on;
* ``unreadable <why>`` — the answer is not the engine's document.

The answer crosses a process boundary, so every field is checked against the document
before anything is decided from it: an answer that half-matches is not one to launch on.
The vocabulary below restates the published schema, and
`tests/test_run_reading.py` holds it to that schema at the pinned engine's tag.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

__all__ = [
    "ENDING_KINDS",
    "FIELD_TYPES",
    "LIVENESS",
    "NODE_STATUSES",
    "READING_FIELDS",
    "SCHEMA_VERSION",
    "WORDS",
    "Decided",
    "Decision",
    "RunId",
    "Unreadable",
    "decide",
    "main",
]

#: The run a reading is asked of, which the reading has to name back.
RunId = NewType("RunId", str)

#: The version of the document this reader reads.
SCHEMA_VERSION = 1
#: The JSON type of every field of each object the document carries, keyed by the name
#: the schema gives the object (`RunReading` for the document itself). A type is a JSON
#: type name, another object's name, or `array[<item type>]`; every field is always
#: present, so each object's fields are exactly its keys here.
FIELD_TYPES: dict[str, dict[str, frozenset[str]]] = {
    "RunReading": {
        "schema_version": frozenset({"integer"}),
        "run_id": frozenset({"string"}),
        "word": frozenset({"string"}),
        "liveness": frozenset({"string"}),
        "driven": frozenset({"boolean"}),
        "ending": frozenset({"Ending", "null"}),
        "paused": frozenset({"Paused", "null"}),
    },
    "Ending": {"kind": frozenset({"EndingKind"}), "nodes": frozenset({"array[EndedNode]"})},
    "EndedNode": {
        "id": frozenset({"string"}),
        "status": frozenset({"string"}),
        "outcome": frozenset({"string", "null"}),
    },
    "Paused": {
        "human_actions": frozenset({"array[string]"}),
        "blocking_surface": frozenset({"boolean"}),
    },
}
#: Every field the document carries.
READING_FIELDS = frozenset(FIELD_TYPES["RunReading"])
#: The word `status` and `runs` print for a run.
WORDS = frozenset(
    {
        "SETTLED",
        "ENDED failed",
        "ENDED unfinished",
        "ENDED stopped",
        "PAUSED",
        "ACTIVE",
        "PARKED",
        "DRIVER DEAD",
        "UNDRIVEN",
    }
)
#: The driver-liveness word.
LIVENESS = frozenset({"ACTIVE", "PARKED", "DRIVER DEAD", "UNDRIVEN"})
#: The fields of an ending, and the ways a run ends.
ENDING_FIELDS = frozenset(FIELD_TYPES["Ending"])
ENDING_KINDS = frozenset({"complete", "failed", "unfinished", "stopped"})
#: The fields of a node an ending lists, and the status words such a node can carry:
#: every status but `done`, since an ending lists only the nodes that are not.
NODE_FIELDS = frozenset(FIELD_TYPES["EndedNode"])
NODE_STATUSES = frozenset(
    {
        "pending",
        "ready",
        "running",
        "waiting",
        "blocked",
        "parked",
        "cancelled",
        "complete-but-draft",
        "failed",
        "skipped",
    }
)
#: The fields of a pause.
PAUSE_FIELDS = frozenset(FIELD_TYPES["Paused"])


class Unreadable(ValueError):
    """The answer is not the engine's reading of the run, and says why."""


class Decision(StrEnum):
    """What the recipe does with a run, decided from its reading."""

    ENDED = "ended"
    DRIVEN = "driven"
    PAUSED = "paused"
    CRASHED = "crashed"


@dataclass(frozen=True)
class Decided:
    """One decision, and the detail the recipe's message names."""

    decision: Decision
    detail: str

    def line(self) -> str:
        return f"{self.decision} {self.detail}".rstrip()


def _is(value: object, types: frozenset[str]) -> bool:
    """Whether ``value`` is one of the scalar JSON ``types`` a field is declared as."""
    held = {
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    return any(held[each] for each in types)


def _item(declared: frozenset[str]) -> frozenset[str]:
    """The item types of an array field declared as ``array[<item>]``."""
    return frozenset(each.removeprefix("array[").removesuffix("]") for each in declared)


def _object(value: object, fields: frozenset[str], what: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise Unreadable(f"{what} does not carry exactly {', '.join(sorted(fields))}")
    return {str(key): held for key, held in value.items()}


def _word(value: object, allowed: frozenset[str], what: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise Unreadable(f"{what} is {json.dumps(value)}, which is not one the engine publishes")
    return value


def _ending(value: object) -> None:
    ending = _object(value, ENDING_FIELDS, "its ending")
    _word(ending["kind"], ENDING_KINDS, "its ending kind")
    nodes = ending["nodes"]
    if not isinstance(nodes, list):
        raise Unreadable("its ending's nodes are not a list")
    for listed in nodes:
        node = _object(listed, NODE_FIELDS, "a node of its ending")
        if not _is(node["id"], FIELD_TYPES["EndedNode"]["id"]):
            raise Unreadable("a node of its ending has an id that is not a string")
        _word(node["status"], NODE_STATUSES, "the status of a node of its ending")
        if not _is(node["outcome"], FIELD_TYPES["EndedNode"]["outcome"]):
            raise Unreadable("a node of its ending has an outcome that is not a string")


def _waiting(value: object) -> str:
    """What a pause names as waiting, as the recipe's message says it."""
    paused = _object(value, PAUSE_FIELDS, "its pause")
    actions = paused["human_actions"]
    items = _item(FIELD_TYPES["Paused"]["human_actions"])
    if not isinstance(actions, list) or not all(_is(each, items) for each in actions):
        raise Unreadable("its pause's human actions are not a list of node ids")
    if not _is(paused["blocking_surface"], FIELD_TYPES["Paused"]["blocking_surface"]):
        raise Unreadable("its pause's blocking_surface is not true or false")
    waiting = [f"human action {json.dumps(each)} is waiting" for each in actions]
    if paused["blocking_surface"]:
        waiting.append("a blocking surface is waiting")
    if not waiting:
        raise Unreadable("its pause names nothing waiting")
    return "; ".join(waiting)


def decide(answer: str, run: RunId) -> Decided:
    """Decide what the recipe does with ``run`` from the engine's ``answer`` about it."""
    try:
        parsed: object = json.loads(answer)
    except ValueError as error:
        raise Unreadable("the answer is not one JSON document") from error
    reading = _object(parsed, READING_FIELDS, "the reading")
    version = reading["schema_version"]
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise Unreadable(f"it is schema {json.dumps(version)}, not {SCHEMA_VERSION}")
    if reading["run_id"] != run:
        raise Unreadable(f"it reads run {json.dumps(reading['run_id'])}, not {run}")
    word = _word(reading["word"], WORDS, "its word")
    _word(reading["liveness"], LIVENESS, "its liveness")
    driven = reading["driven"]
    if not _is(driven, FIELD_TYPES["RunReading"]["driven"]):
        raise Unreadable("its driven field is not true or false")
    ending, paused = reading["ending"], reading["paused"]
    if ending is not None:
        _ending(ending)
    waiting = None if paused is None else _waiting(paused)
    if ending is not None and paused is not None:
        raise Unreadable("it reads both ended and paused")
    if driven and (ending is not None or paused is not None):
        raise Unreadable("it reads driven and yet ended or paused")
    if ending is not None:
        return Decided(Decision.ENDED, "")
    if driven:
        return Decided(Decision.DRIVEN, word)
    if waiting is not None:
        return Decided(Decision.PAUSED, waiting)
    return Decided(Decision.CRASHED, word)


def main(argv: list[str] | None = None) -> int:
    """Read the answer on stdin for the run ``argv`` names, and print the decision line."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m orchestrator.run_reading <run-id> < answer", file=sys.stderr)
        return 2
    try:
        print(decide(sys.stdin.read(), RunId(arguments[0])).line())
    except Unreadable as unreadable:
        print(f"unreadable {unreadable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
