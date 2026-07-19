from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.config import ConfigError
from orchestrator.journal import (
    AUTHORITATIVE_EVENT_KINDS,
    OPTIONAL_EVENT_FIELDS,
    REQUIRED_EVENT_FIELDS,
    Event,
    EventKind,
    NodeId,
    RunId,
    open_journal,
)
from orchestrator.projection import ProjectionError, project_round, project_run, read_strict_events
from orchestrator.runs import prepare_round


def test_static_event_contract_golden() -> None:
    golden = json.loads(
        (Path(__file__).parent / "golden" / "static-round-events-v2.json").read_text()
    )
    assert golden["version"] == 2
    assert golden["terminal_detail"] == {
        "required": ["result"],
        "result": "GraphResultItem",
    }
    assert golden["state_changing_kinds"] == list(AUTHORITATIVE_EVENT_KINDS)
    assert golden["required_envelope"] == list(REQUIRED_EVENT_FIELDS)
    assert golden["optional_envelope"] == list(OPTIONAL_EVENT_FIELDS)


def test_projection_reconstructs_plan_states_attestations_and_result(tmp_path: Path) -> None:
    run_id = RunId("projection")
    journal = open_journal(tmp_path, run_id, 1)
    journal.append(
        "node-added", detail={"definition": {"id": "approve", "kind": "human", "task": "Approve"}}
    )
    journal.append(
        "node-added", detail={"definition": {"id": "ship", "persona": "engineer", "task": "Ship"}}
    )
    journal.append("edge-added", detail={"from": "approve", "to": "ship"})
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 1}})
    journal.append(
        "human-waiting",
        node=NodeId("approve"),
        detail={"task": "Approve", "result": {"status": "waiting"}},
    )
    journal.append("human-attested", node=NodeId("approve"), detail={"ref": "approve"})
    result = {
        "ok": False,
        "state": "waiting",
        "started_order": ["approve"],
        "results": {"approve": {"status": "waiting"}},
    }
    journal.append("round-finished", detail={"result": result})

    projection = project_run(tmp_path / "events.jsonl", run_id, 1)
    assert projection.plan["tasks"][1]["deps"] == ["approve"]
    assert projection.attestations == ("approve",)
    assert projection.result == result


def test_strict_reader_rejects_unknown_state_changing_event(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            {"version": 1, "seq": 1, "at": 0, "kind": "node-dropped", "run_id": "r", "round": 1}
        )
        + "\n"
    )
    with pytest.raises(ProjectionError, match="unknown authoritative event"):
        read_strict_events(path, RunId("r"))


def test_strict_reader_handles_absence_torn_tail_and_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    assert read_strict_events(path, RunId("r")) == []
    path.write_bytes(b'{"torn":')
    assert read_strict_events(path, RunId("r")) == []
    path.write_bytes(b"\xff\n")
    with pytest.raises(ProjectionError, match="malformed"):
        read_strict_events(path, RunId("r"))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("\n", "blank"),
        ("{broken}\n", "malformed"),
        ("[]\n", "unknown"),
        ('{"kind":"node-added"}\n', "invalid"),
        (
            '{"version":1,"seq":1,"at":0,"kind":"round-started","run_id":"other","round":1}\n',
            "another run",
        ),
        (
            '{"version":1,"seq":2,"at":0,"kind":"round-started","run_id":"r","round":1}\n',
            "contiguous",
        ),
    ],
)
def test_strict_reader_rejects_every_invalid_durable_line(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(content)
    with pytest.raises(ProjectionError, match=message):
        read_strict_events(path, RunId("r"))


def _event(kind: EventKind, seq: int, *, node: str | None = None, detail=None) -> Event:
    return Event(
        kind=kind,
        run_id=RunId("r"),
        round=1,
        seq=seq,
        at=0,
        node=None if node is None else NodeId(node),
        detail=detail or {},
        version=1,
    )


@pytest.mark.parametrize(
    ("tail", "message"),
    [
        ([_event("node-added", 2, detail={"definition": "bad"})], "definition"),
        (
            [
                _event(
                    "node-added", 2, detail={"definition": {"id": "a", "persona": "p", "task": "x"}}
                )
            ],
            "duplicate node",
        ),
        ([_event("edge-added", 2, detail={"from": 1, "to": "a"})], "string"),
        ([_event("node-started", 2, node="missing")], "unknown node"),
        (
            [_event("node-started", 2, node="a"), _event("node-started", 3, node="a")],
            "more than once",
        ),
        ([_event("node-settled", 2, node="a", detail={"status": "done"})], "without one start"),
        (
            [_event("node-started", 2, node="a"), _event("node-settled", 3, node="a")],
            "terminal status",
        ),
        ([_event("human-attested", 2, node="a")], "non-empty ref"),
        (
            [
                _event("human-attested", 2, node="a", detail={"ref": "a"}),
                _event("human-attested", 3, node="a", detail={"ref": "a"}),
            ],
            "more than once",
        ),
        ([_event("round-finished", 2)], "projected result"),
        (
            [_event("round-finished", 2, detail={"result": {"ok": "yes"}})],
            "result is invalid",
        ),
        ([_event("edge-added", 2, detail={"from": "missing", "to": "a"})], "unknown node"),
        ([_event("edge-added", 2, detail={"from": "a", "to": "a"})], "depends on itself"),
        (
            [
                _event(
                    "node-added", 2, detail={"definition": {"id": "b", "persona": "p", "task": "x"}}
                ),
                _event("edge-added", 3, detail={"from": "a", "to": "b"}),
                _event("edge-added", 4, detail={"from": "a", "to": "b"}),
            ],
            "duplicate edge",
        ),
    ],
)
def test_fold_rejects_invalid_static_transitions(tail: list[Event], message: str) -> None:
    definition = (
        {"id": "a", "kind": "human", "task": "Approve"}
        if any(event.kind == "human-attested" for event in tail)
        else {"id": "a", "persona": "p", "task": "x"}
    )
    first = _event(
        "node-added",
        1,
        detail={"definition": definition},
    )
    topology_only = all(event.kind in {"node-added", "edge-added"} for event in tail)
    ordered = (
        [first, *tail, _event("round-started", 5, detail={"plan": {}})]
        if topology_only
        else [first, _event("round-started", 2, detail={"plan": {}}), *tail]
    )
    with pytest.raises(ProjectionError, match=message):
        project_round(ordered, RunId("r"), 1)


def test_fold_requires_a_graph_definition() -> None:
    with pytest.raises(ProjectionError, match="no node-added"):
        project_round([_event("round-started", 1, detail={"plan": {}})], RunId("r"), 1)


def test_fold_records_failed_state_and_ignores_another_round() -> None:
    events = [
        _event("node-added", 1, detail={"definition": {"id": "a", "persona": "p", "task": "x"}}),
        _event("round-started", 2, detail={"plan": {}}),
        _event("node-started", 3, node="a"),
        _event("node-failed", 4, node="a"),
        Event("round-started", RunId("r"), 2, 5, 0),
    ]
    assert project_round(events, RunId("r"), 1).node_states == {"a": "failed"}


def test_v2_terminal_event_requires_and_round_trips_node_result() -> None:
    prefix = [
        Event(
            "node-added",
            RunId("r"),
            1,
            1,
            0,
            detail={"definition": {"id": "a", "persona": "p", "task": "x"}},
        ),
        Event("round-started", RunId("r"), 1, 2, 0, detail={"plan": {}}),
        Event("node-started", RunId("r"), 1, 3, 0, node=NodeId("a")),
    ]
    with pytest.raises(ProjectionError, match="requires a serialized node result"):
        project_round(
            [
                *prefix,
                Event(
                    "node-settled",
                    RunId("r"),
                    1,
                    4,
                    0,
                    node=NodeId("a"),
                    detail={"status": "done"},
                ),
            ],
            RunId("r"),
            1,
        )

    item = {
        "kind": "agent",
        "status": "done",
        "task": "x",
        "completed": True,
        "exit_code": 0,
        "verdicts": [{"kind": "boolean", "criterion": "done"}],
        "usage": {"total_tokens": 12},
        "error": None,
    }
    projected = project_round(
        [
            *prefix,
            Event(
                "node-settled",
                RunId("r"),
                1,
                4,
                0,
                node=NodeId("a"),
                detail={"status": "done", "result": item},
            ),
        ],
        RunId("r"),
        1,
    )
    assert projected.node_results == {"a": item}

    for invalid in (
        "not-a-mapping",
        {"status": "bogus"},
        {"status": "done", "human_actions": "invalid"},
    ):
        with pytest.raises(ProjectionError, match="invalid serialized node result"):
            project_round(
                [
                    *prefix,
                    Event(
                        "node-settled",
                        RunId("r"),
                        1,
                        4,
                        0,
                        node=NodeId("a"),
                        detail={"status": "done", "result": invalid},
                    ),
                ],
                RunId("r"),
                1,
            )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "ok": True,
                "state": "complete",
                "started_order": ["a"],
                "results": {
                    "a": {"status": "done"},
                    "unknown": {"status": "done"},
                },
            },
            "result references unknown node.*unknown",
        ),
        (
            {
                "ok": True,
                "state": "complete",
                "started_order": ["unknown"],
                "results": {"a": {"status": "done"}},
            },
            "started_order references unknown node.*unknown",
        ),
        (
            {
                "ok": True,
                "state": "complete",
                "started_order": ["a"],
                "results": {"a": {"status": "done"}},
            },
            "without a start event.*a",
        ),
    ],
)
def test_fold_rejects_terminal_identifiers_outside_the_projected_execution(
    payload: dict[str, object], message: str
) -> None:
    events = [
        _event("node-added", 1, detail={"definition": {"id": "a", "persona": "p", "task": "x"}}),
        _event("round-started", 2, detail={"plan": {}}),
    ]
    if "without a start" not in message:
        events.extend(
            [
                _event("node-started", 3, node="a"),
                _event("node-settled", 4, node="a", detail={"status": "done"}),
            ]
        )
    events.append(_event("round-finished", 5, detail={"result": payload}))
    with pytest.raises(ProjectionError, match=message):
        project_round(events, RunId("r"), 1)


@pytest.mark.parametrize(
    ("definition", "node", "ref", "message"),
    [
        (
            {"id": "approval", "kind": "human", "task": "Approve"},
            "unknown",
            "unknown",
            "unknown graph node",
        ),
        (
            {"id": "approval", "persona": "p", "task": "Work"},
            "approval",
            "approval",
            "not a projected human action",
        ),
        (
            {"id": "approval", "kind": "human", "task": "Approve"},
            "approval",
            "different",
            "does not match locator 'approval'",
        ),
    ],
)
def test_fold_rejects_invalid_human_attestation_targets(
    definition: dict[str, object], node: str, ref: str, message: str
) -> None:
    events = [
        _event("node-added", 1, detail={"definition": definition}),
        _event("round-started", 2, detail={"plan": {}}),
        _event("human-attested", 3, node=node, detail={"ref": ref}),
    ]
    with pytest.raises(ProjectionError, match=message):
        project_round(events, RunId("r"), 1)


def test_prepare_round_rebuilds_deleted_derived_plan(tmp_path: Path) -> None:
    run_dir = tmp_path / "replay"
    plan = {"schema_version": 3, "tasks": [{"id": "a", "persona": "p", "task": "x"}]}
    number, round_dir = prepare_round(run_dir, plan)
    journal = open_journal(run_dir, RunId("replay"), number)
    journal.append("node-added", detail={"definition": plan["tasks"][0]})
    journal.append("round-started", detail={"plan": {"schema_version": 3}})
    (round_dir / "plan.json").unlink()
    (round_dir / "status.json").unlink()

    assert prepare_round(run_dir, plan, recover=True) == (number, round_dir)
    assert json.loads((round_dir / "plan.json").read_text()) == plan


def test_prepare_round_rejects_an_invalid_authoritative_replay(tmp_path: Path) -> None:
    run_dir = tmp_path / "bad"
    _, round_dir = prepare_round(run_dir, {"tasks": [{"id": "a", "task": "x"}]})
    (round_dir / "plan.json").unlink()
    (run_dir / "events.jsonl").write_text("{}\n")
    with pytest.raises(ConfigError, match="cannot replay.*unknown authoritative event"):
        prepare_round(run_dir, {"tasks": [{"id": "a", "task": "x"}]}, recover=True)


def test_prepare_round_regenerates_a_deleted_terminal_result_then_advances(tmp_path: Path) -> None:
    run_dir = tmp_path / "terminal"
    plan = {"tasks": [{"id": "a", "persona": "p", "task": "x"}]}
    _, round_dir = prepare_round(run_dir, plan)
    journal = open_journal(run_dir, RunId("terminal"), 1)
    journal.append("node-added", detail={"definition": plan["tasks"][0]})
    journal.append("round-started", detail={"plan": {}})
    result = {"ok": True, "state": "complete", "started_order": [], "results": {}}
    journal.append("round-finished", detail={"result": result})
    (round_dir / "plan.json").unlink()

    number, next_dir = prepare_round(run_dir, plan, recover=True)
    assert number == 2
    assert json.loads((round_dir / "result.json").read_text()) == result
    assert json.loads((next_dir / "plan.json").read_text()) == plan
