"""`orchestrator/run_reading.py`: what `just follow-ups` decides from the engine's reading.

The decisions are driven end to end over run roots the installed engine leaves by
`tests/plan_tooling/test_follow_ups_recipe_e2e.py`; this module holds every refusal the
reader makes to an answer the engine would not give, and — marked `reads_checkouts` —
holds the reader's restated vocabulary to the schema the pinned engine publishes.
"""

from __future__ import annotations

import io
import json
import re
from typing import NamedTuple

import jsonschema
import pytest
from test_engine_contracts import ONEPIPELINE_SCHEMAS, _source

from orchestrator import run_reading
from orchestrator.root import REPO_ROOT
from orchestrator.run_reading import RunId, Unreadable, decide

RUN = RunId("run-1")

#: A reading of `RUN` the engine would print for a run that ended `failed`.
ENDED = {
    "schema_version": 1,
    "run_id": RUN,
    "word": "ENDED failed",
    "liveness": "DRIVER DEAD",
    "driven": False,
    "ending": {"kind": "failed", "nodes": [{"id": "a", "status": "failed", "outcome": None}]},
    "paused": None,
}
#: The same run read while driven, paused on a human node and a question, and crashed.
DRIVEN = ENDED | {"word": "ACTIVE", "liveness": "ACTIVE", "driven": True, "ending": None}
PAUSED = ENDED | {
    "word": "PAUSED",
    "ending": None,
    "paused": {"human_actions": ["gate"], "blocking_surface": True},
}
CRASHED = ENDED | {"word": "DRIVER DEAD", "ending": None}


def _read(reading: object) -> run_reading.Decided:
    return decide(json.dumps(reading), RUN)


@pytest.mark.parametrize(
    ("reading", "line"),
    [
        (ENDED, "ended"),
        (ENDED | {"ending": {"kind": "complete", "nodes": []}, "word": "SETTLED"}, "ended"),
        (
            ENDED
            | {
                "ending": {
                    "kind": "stopped",
                    "nodes": [{"id": "a", "status": "waiting", "outcome": "x"}],
                }
            },
            "ended",
        ),
        (DRIVEN, "driven ACTIVE"),
        (PAUSED, 'paused human action "gate" is waiting; a blocking surface is waiting'),
        (
            PAUSED | {"paused": {"human_actions": [], "blocking_surface": True}},
            "paused a blocking surface is waiting",
        ),
        (CRASHED, "crashed DRIVER DEAD"),
    ],
    ids=["failed", "complete", "stopped", "driven", "paused", "surface-only", "crashed"],
)
def test_each_reading_the_engine_gives_is_decided(reading: object, line: str) -> None:
    assert _read(reading).line() == line


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        ("", "the answer is not one JSON document"),
        ("[]", "the reading does not carry exactly"),
        (json.dumps(ENDED | {"extra": 1}), "the reading does not carry exactly"),
        (json.dumps(ENDED | {"schema_version": 2}), "it is schema 2, not 1"),
        (json.dumps(ENDED | {"schema_version": True}), "it is schema true, not 1"),
        (json.dumps(ENDED | {"run_id": "run-2"}), 'it reads run "run-2", not run-1'),
        (json.dumps(ENDED | {"word": ["SETTLED"]}), 'its word is ["SETTLED"], which'),
        (json.dumps(ENDED | {"liveness": "GONE"}), 'its liveness is "GONE", which'),
        (json.dumps(ENDED | {"driven": 0}), "its driven field is not true or false"),
        (json.dumps(ENDED | {"ending": {"kind": "failed"}}), "its ending does not carry"),
        (
            json.dumps(ENDED | {"ending": {"kind": "crashed", "nodes": []}}),
            'its ending kind is "crashed", which',
        ),
        (json.dumps(ENDED | {"ending": {"kind": "failed", "nodes": {}}}), "nodes are not a list"),
        (
            json.dumps(ENDED | {"ending": {"kind": "failed", "nodes": [{"id": "a"}]}}),
            "a node of its ending does not carry exactly",
        ),
        (
            json.dumps(
                ENDED
                | {
                    "ending": {
                        "kind": "failed",
                        "nodes": [{"id": 1, "status": "failed", "outcome": None}],
                    }
                }
            ),
            "has an id that is not a string",
        ),
        (
            json.dumps(
                ENDED
                | {
                    "ending": {
                        "kind": "failed",
                        "nodes": [{"id": "a", "status": "done", "outcome": None}],
                    }
                }
            ),
            'the status of a node of its ending is "done", which',
        ),
        (
            json.dumps(
                ENDED
                | {
                    "ending": {
                        "kind": "failed",
                        "nodes": [{"id": "a", "status": "failed", "outcome": 3}],
                    }
                }
            ),
            "has an outcome that is not a string",
        ),
        (json.dumps(PAUSED | {"paused": {"human_actions": "gate"}}), "its pause does not carry"),
        (
            json.dumps(PAUSED | {"paused": {"human_actions": [1], "blocking_surface": False}}),
            "human actions are not a list of node ids",
        ),
        (
            json.dumps(PAUSED | {"paused": {"human_actions": [], "blocking_surface": "yes"}}),
            "blocking_surface is not true or false",
        ),
        (
            json.dumps(PAUSED | {"paused": {"human_actions": [], "blocking_surface": False}}),
            "its pause names nothing waiting",
        ),
        (json.dumps(ENDED | {"paused": PAUSED["paused"]}), "it reads both ended and paused"),
        (json.dumps(ENDED | {"driven": True}), "it reads driven and yet ended or paused"),
        (json.dumps(PAUSED | {"driven": True}), "it reads driven and yet ended or paused"),
    ],
)
def test_an_answer_that_is_not_the_engines_reading_is_unreadable(answer: str, said: str) -> None:
    with pytest.raises(Unreadable, match=None) as refused:
        decide(answer, RUN)

    assert said in str(refused.value)


def test_the_command_prints_one_decision_line_for_the_answer_on_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(CRASHED)))
    assert run_reading.main([RUN]) == 0
    assert capsys.readouterr().out == "crashed DRIVER DEAD\n"

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert run_reading.main([RUN]) == 0
    assert capsys.readouterr().out == "unreadable the answer is not one JSON document\n"


def test_the_command_refuses_a_call_that_names_no_single_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["run_reading"])
    assert run_reading.main() == 2
    assert "usage: python -m orchestrator.run_reading <run-id>" in capsys.readouterr().err


# llmlint: ignore-block[shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker] This
# repository runs one Nx project and splits its tiers by pytest marker over four `nx.json`
# keys. The subject of the two tests below is the engine's registered checkout, read at the
# pinned tag the way `tests/test_engine_contracts.py` reads it, which lives outside this
# workspace and so outside every one of those keys — hence `reads_checkouts`, the uncached
# tier, where a memo cannot replay a green across the pin bump these exist to catch.
#: The published document, at the release `config/onepipeline.version` pins.
def _schema() -> dict[str, object]:
    document: object = json.loads(_source(ONEPIPELINE_SCHEMAS, "run-reading.schema.json"))
    assert isinstance(document, dict)
    return document


def _definition(schema: dict[str, object], name: str) -> dict[str, object]:
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    found = definitions[name]
    assert isinstance(found, dict)
    return found


def _enum(definition: dict[str, object], field: str) -> frozenset[str]:
    properties = definition["properties"]
    assert isinstance(properties, dict)
    return frozenset(properties[field]["enum"])


@pytest.mark.reads_checkouts
def test_the_vocabulary_the_reader_restates_is_the_published_schemas() -> None:
    """Each set the reader checks against is the schema's own, at the pinned engine's tag.

    The reader restates the document because the schema is not shipped in the wheel this
    host installs; this is what makes the restatement a copy with a drift gate rather than
    a second source.
    """
    schema = _schema()
    ending = _definition(schema, "Ending")
    node = _definition(schema, "EndedNode")
    paused = _definition(schema, "Paused")
    properties = schema["properties"]
    assert isinstance(properties, dict)

    assert frozenset(schema["required"]) == run_reading.READING_FIELDS == frozenset(properties)
    assert properties["schema_version"]["const"] == run_reading.SCHEMA_VERSION
    assert _enum(schema, "word") == run_reading.WORDS
    assert _enum(schema, "liveness") == run_reading.LIVENESS
    assert frozenset(ending["required"]) == run_reading.ENDING_FIELDS
    assert frozenset(node["required"]) == run_reading.NODE_FIELDS
    assert _enum(node, "status") == run_reading.NODE_STATUSES
    assert frozenset(paused["required"]) == run_reading.PAUSE_FIELDS
    kind = _definition(schema, "EndingKind")
    named = kind.get("enum") or [one["const"] for one in kind["oneOf"]]
    assert frozenset(named) == run_reading.ENDING_KINDS


def _types(field: dict[str, object]) -> frozenset[str]:
    """A schema field's types, spelled as `run_reading.FIELD_TYPES` spells them."""
    match field:
        case {"$ref": str(reference)}:
            return frozenset({reference.rsplit("/", 1)[-1]})
        case {"anyOf": list(alternatives)}:
            return frozenset().union(*(_types(each) for each in alternatives))
        case {"type": "array", "items": dict(items)}:
            (item,) = _types(items)
            return frozenset({f"array[{item}]"})
        case {"type": str(declared)}:
            return frozenset({declared})
        case {"type": list(declared)}:
            return frozenset(str(each) for each in declared)
        case _:
            raise AssertionError(f"a schema field of no shape the reader spells: {field}")


@pytest.mark.reads_checkouts
def test_the_field_types_the_reader_checks_are_the_published_schemas() -> None:
    """Each field the reader type-checks has the types the schema declares, and no other."""
    schema = _schema()
    objects = {"RunReading": schema} | {
        name: _definition(schema, name) for name in run_reading.FIELD_TYPES if name != "RunReading"
    }
    for name, fields in run_reading.FIELD_TYPES.items():
        properties = objects[name]["properties"]
        assert isinstance(properties, dict)
        published = {field: _types(declared) for field, declared in properties.items()}
        assert published == fields, name


@pytest.mark.reads_checkouts
@pytest.mark.parametrize("reading", [ENDED, DRIVEN, PAUSED, CRASHED])
def test_every_reading_the_reader_decides_on_is_one_the_schema_admits(reading: object) -> None:
    """Nothing the reader acts on is a document the engine's schema would refuse."""
    _read(reading)
    jsonschema.validate(reading, _schema())


# llmlint: ignore-end[shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]


class WordPassage(NamedTuple):
    """One passage of prose that enumerates the run's words."""

    #: The document, relative to the repository root.
    document: str
    #: The text the passage starts at.
    opens: str
    #: The text it stops before.
    closes: str


#: Each passage of this repository's prose that enumerates the run's words, and where it
#: starts and stops. Read so each restatement is held to the reader's words, which the gate
#: above holds to the engine's schema.
WORD_PASSAGES = (
    WordPassage("AGENTS.md", "**The word a view", "unrelated to a node you parked with `cancel`."),
    WordPassage(
        "docs/telemetry.md", "The word printed for the run puts its ending first", "(`just status"
    ),
)


@pytest.mark.reads_docs
@pytest.mark.parametrize(("document", "opens", "closes"), WORD_PASSAGES)
def test_the_prose_names_every_ending_word_and_only_the_engines_words(
    document: str, opens: str, closes: str
) -> None:
    """Each passage names every word a run that is not merely driven or not reads, and no other."""
    text = (REPO_ROOT / document).read_text(encoding="utf-8")
    start = text.index(opens)
    passage = " ".join(text[start : text.index(closes, start)].split())
    named = {
        word for word in re.findall(r"`([A-Z][A-Za-z ]*)`", passage) if word.split()[0].isupper()
    }

    assert named <= run_reading.WORDS, named - run_reading.WORDS
    assert named >= run_reading.WORDS - run_reading.LIVENESS, (
        f"{document} leaves out {sorted(run_reading.WORDS - run_reading.LIVENESS - named)}"
    )
