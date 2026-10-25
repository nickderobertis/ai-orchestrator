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

With ``--public-id`` it prints instead the run's public id — the reading's `public_id`, what
the run is named by wherever a destination is not private — which every follow-up record of
the run is keyed by. The engine decides it; nothing here derives one. With ``--group`` it
prints the run's effective project group — the reading's `group`, its launch group, project
origin or project, as any label overlay leaves it — or an empty line where the run has none,
which the follow-up run it launches is grouped under.

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
    "OPTIONAL_FIELDS",
    "READING_FIELDS",
    "SCHEMA_VERSION",
    "WORDS",
    "Decided",
    "Decision",
    "RunId",
    "Unreadable",
    "decide",
    "group",
    "main",
    "public_id",
]

#: The run a reading is asked of, which the reading has to name back.
RunId = NewType("RunId", str)

#: The version of the document this reader reads.
SCHEMA_VERSION = 2
#: The JSON type of every field of each object the document carries, keyed by the name
#: the schema gives the object (`RunReading` for the document itself). A type is a JSON
#: type name, another object's name, or `array[<item type>]`. Every field is present
#: unless :data:`OPTIONAL_FIELDS` names it, which the engine omits when it is empty.
FIELD_TYPES: dict[str, dict[str, frozenset[str]]] = {
    "RunReading": {
        "labels": frozenset({"object"}),
        "group": frozenset({"string", "null"}),
        "schema_version": frozenset({"integer"}),
        "run_id": frozenset({"string"}),
        "public_id": frozenset({"string"}),
        "word": frozenset({"string"}),
        "liveness": frozenset({"string"}),
        "driven": frozenset({"boolean"}),
        "ending": frozenset({"Ending", "null"}),
        "paused": frozenset({"Paused", "null"}),
        "unanswered": frozenset({"array[UnansweredEnvelope]"}),
        "inbox": frozenset({"array[InboxNote]"}),
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
    "UnansweredEnvelope": {
        "envelope": frozenset({"string"}),
        "delivery": frozenset({"Delivery", "null"}),
    },
    "InboxNote": {
        "envelope": frozenset({"string"}),
        "delivery": frozenset({"InboxDelivery"}),
    },
}
#: The fields of each object the schema leaves out of its `required`.
OPTIONAL_FIELDS: dict[str, frozenset[str]] = {
    "RunReading": frozenset({"unanswered", "inbox"}),
    "UnansweredEnvelope": frozenset({"delivery"}),
    "InboxNote": frozenset(),
}
#: Every field the document always carries.
READING_FIELDS = frozenset(FIELD_TYPES["RunReading"]) - OPTIONAL_FIELDS["RunReading"]
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
#: The fields every envelope the run has claimed and not answered carries.
UNANSWERED_FIELDS = (
    frozenset(FIELD_TYPES["UnansweredEnvelope"]) - OPTIONAL_FIELDS["UnansweredEnvelope"]
)
#: The fields every live note a receiver's inbox holds carries.
INBOX_FIELDS = frozenset(FIELD_TYPES["InboxNote"])
#: The one state a note in an inbox has, and the fields its delivery carries: the receiving
#: node and since when, always, and a deadline where the note carries one.
INBOX_STATE = "queued"
INBOX_DELIVERY_FIELDS = frozenset({"state", "node", "since"})
INBOX_DELIVERY_OPTIONAL = frozenset({"deadline"})
#: The JSON types of that delivery's fields besides its one `state`, as the schema declares
#: them in its `InboxDelivery`, which states its one shape as a `oneOf` alternative rather than
#: as the plain object :data:`FIELD_TYPES` spells.
INBOX_DELIVERY_TYPES: dict[str, frozenset[str]] = {
    "node": frozenset({"string"}),
    "since": frozenset({"string"}),
    "deadline": frozenset({"string", "null"}),
}


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
        "object": isinstance(value, dict),
    }
    return any(held[each] for each in types)


def _item(declared: frozenset[str]) -> frozenset[str]:
    """The item types of an array field declared as ``array[<item>]``."""
    return frozenset(each.removeprefix("array[").removesuffix("]") for each in declared)


def _object(
    value: object, fields: frozenset[str], what: str, optional: frozenset[str] = frozenset()
) -> dict[str, object]:
    if not isinstance(value, dict) or not fields <= set(value) <= fields | optional:
        carried = ", ".join(sorted(fields))
        also = f" and at most {', '.join(sorted(optional))}" if optional else ""
        raise Unreadable(f"{what} does not carry exactly {carried}{also}")
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


def _unanswered(value: object) -> None:
    """Check the envelopes the run claimed and has not answered, which decide nothing."""
    if not isinstance(value, list):
        raise Unreadable("its unanswered envelopes are not a list")
    for listed in value:
        envelope = _object(
            listed,
            UNANSWERED_FIELDS,
            "an unanswered envelope",
            OPTIONAL_FIELDS["UnansweredEnvelope"],
        )
        if not _is(envelope["envelope"], FIELD_TYPES["UnansweredEnvelope"]["envelope"]):
            raise Unreadable("an unanswered envelope has an id that is not a string")
        delivery = envelope.get("delivery")
        if delivery is not None and not isinstance(delivery, dict):
            raise Unreadable("an unanswered envelope's delivery is not an object")


def _inbox(value: object) -> None:
    """Check the live notes waiting in receivers' inboxes, which decide nothing."""
    if not isinstance(value, list):
        raise Unreadable("its inbox is not a list")
    for listed in value:
        note = _object(listed, INBOX_FIELDS, "a note in its inbox")
        if not _is(note["envelope"], FIELD_TYPES["InboxNote"]["envelope"]):
            raise Unreadable("a note in its inbox has an envelope id that is not a string")
        delivery = _object(
            note["delivery"],
            INBOX_DELIVERY_FIELDS,
            "a note in its inbox's delivery",
            INBOX_DELIVERY_OPTIONAL,
        )
        if delivery["state"] != INBOX_STATE:
            raise Unreadable(
                f"a note in its inbox is {json.dumps(delivery['state'])}, not {INBOX_STATE}"
            )
        if not all(
            _is(delivery[field], INBOX_DELIVERY_TYPES[field]) for field in ("node", "since")
        ):
            raise Unreadable("a note in its inbox names its node or its time as no string")
        if not _is(delivery.get("deadline"), INBOX_DELIVERY_TYPES["deadline"]):
            raise Unreadable("a note in its inbox has a deadline that is not a string")


def _labelled(reading: dict[str, object]) -> None:
    """Check the run's labels, its group and its public id, which decide nothing."""
    labels = reading["labels"]
    if not isinstance(labels, dict) or not all(isinstance(each, str) for each in labels.values()):
        raise Unreadable("its labels are not an object of strings")
    if not _is(reading["group"], FIELD_TYPES["RunReading"]["group"]):
        raise Unreadable("its group is neither a string nor null")
    if not _is(reading["public_id"], FIELD_TYPES["RunReading"]["public_id"]):
        raise Unreadable("its public id is not a string")


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


def _checked(answer: str, run: RunId) -> dict[str, object]:
    """The engine's ``answer`` about ``run``, every field it carries held to the document."""
    try:
        parsed: object = json.loads(answer)
    except ValueError as error:
        raise Unreadable("the answer is not one JSON document") from error
    reading = _object(parsed, READING_FIELDS, "the reading", OPTIONAL_FIELDS["RunReading"])
    version = reading["schema_version"]
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise Unreadable(f"it is schema {json.dumps(version)}, not {SCHEMA_VERSION}")
    if reading["run_id"] != run:
        raise Unreadable(f"it reads run {json.dumps(reading['run_id'])}, not {run}")
    _labelled(reading)
    if not reading["public_id"]:
        raise Unreadable("its public id is empty")
    _word(reading["word"], WORDS, "its word")
    _word(reading["liveness"], LIVENESS, "its liveness")
    if not _is(reading["driven"], FIELD_TYPES["RunReading"]["driven"]):
        raise Unreadable("its driven field is not true or false")
    if "unanswered" in reading:
        _unanswered(reading["unanswered"])
    if "inbox" in reading:
        _inbox(reading["inbox"])
    ending, paused = reading["ending"], reading["paused"]
    if ending is not None:
        _ending(ending)
    if paused is not None:
        _waiting(paused)
    if ending is not None and paused is not None:
        raise Unreadable("it reads both ended and paused")
    if reading["driven"] and (ending is not None or paused is not None):
        raise Unreadable("it reads driven and yet ended or paused")
    return reading


def public_id(answer: str, run: RunId) -> str:
    """The public id the engine's ``answer`` names ``run`` by, from a reading held whole."""
    return str(_checked(answer, run)["public_id"])


def group(answer: str, run: RunId) -> str | None:
    """The effective project group the engine's ``answer`` puts ``run`` in, if any."""
    grouped = _checked(answer, run)["group"]
    return None if grouped is None else str(grouped)


def decide(answer: str, run: RunId) -> Decided:
    """Decide what the recipe does with ``run`` from the engine's ``answer`` about it."""
    reading = _checked(answer, run)
    word = str(reading["word"])
    driven = reading["driven"]
    ending, paused = reading["ending"], reading["paused"]
    waiting = None if paused is None else _waiting(paused)
    if ending is not None:
        return Decided(Decision.ENDED, "")
    if driven:
        return Decided(Decision.DRIVEN, word)
    if waiting is not None:
        return Decided(Decision.PAUSED, waiting)
    return Decided(Decision.CRASHED, word)


def main(argv: list[str] | None = None) -> int:
    """Read the answer on stdin for the run ``argv`` names, and print the decision line."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    field = arguments[0] if arguments[:1] in (["--public-id"], ["--group"]) else None
    if field is not None:
        arguments = arguments[1:]
    if len(arguments) != 1:
        print(
            "usage: python -m orchestrator.run_reading [--public-id | --group] <run-id> < answer",
            file=sys.stderr,
        )
        return 2
    try:
        answer, run = sys.stdin.read(), RunId(arguments[0])
        match field:
            case "--public-id":
                print(public_id(answer, run))
            case "--group":
                print(group(answer, run) or "")
            case _:
                print(decide(answer, run).line())
    except Unreadable as unreadable:
        if field is not None:
            print(f"run_reading: unreadable {unreadable}", file=sys.stderr)
            return 1
        print(f"unreadable {unreadable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
