from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from orchestrator.config import ConfigError
from orchestrator.edits import EDIT_OPERATION_KINDS
from orchestrator.journal import (
    AUDIT_EVENT_KINDS,
    AUTHORITATIVE_EVENT_KINDS,
    COMMITTED_EDIT_COMMAND_FIELD,
    COMMITTED_EDIT_COMMAND_SINCE,
    COMMITTED_EDIT_COMMAND_TYPE,
    COMMITTED_EDIT_OPERATIONS_FIELD,
    JOURNAL_NAME,
    OPTIONAL_EVENT_FIELDS,
    REQUIRED_EVENT_FIELDS,
    SCHEMA_VERSION,
    TERMINAL_NODE_EVENT_KINDS,
    TERMINAL_NODE_RESULT_FIELD,
    TERMINAL_NODE_RESULT_TYPE,
    Event,
    EventKind,
    NodeId,
    RunId,
    open_journal,
)
from orchestrator.projection import ProjectionError, project_round, project_run, read_strict_events
from orchestrator.runs import prepare_round


def test_static_event_contract_golden() -> None:
    """The checked-in contract and the code must move together, version included."""
    golden = json.loads(
        (
            Path(__file__).parent / "golden" / f"static-round-events-v{SCHEMA_VERSION}.json"
        ).read_text()
    )
    assert golden["version"] == SCHEMA_VERSION
    assert golden["terminal_node_kinds"] == list(TERMINAL_NODE_EVENT_KINDS)
    assert golden["terminal_detail"] == {
        "required": [TERMINAL_NODE_RESULT_FIELD],
        TERMINAL_NODE_RESULT_FIELD: TERMINAL_NODE_RESULT_TYPE,
    }
    assert golden["state_changing_kinds"] == list(AUTHORITATIVE_EVENT_KINDS)
    assert golden["audit_kinds"] == sorted(AUDIT_EVENT_KINDS)
    assert golden["required_envelope"] == list(REQUIRED_EVENT_FIELDS)
    assert golden["optional_envelope"] == list(OPTIONAL_EVENT_FIELDS)
    assert golden["committed_edit_detail"] == {
        "required": [COMMITTED_EDIT_OPERATIONS_FIELD],
        "optional": [COMMITTED_EDIT_COMMAND_FIELD],
        COMMITTED_EDIT_COMMAND_FIELD: COMMITTED_EDIT_COMMAND_TYPE,
        "command_since": COMMITTED_EDIT_COMMAND_SINCE,
    }
    # One golden per version, and only the current one: a stale file beside it would
    # be a second, unchecked source for the same contract.
    assert sorted(path.name for path in (Path(__file__).parent / "golden").glob("static-*")) == [
        f"static-round-events-v{SCHEMA_VERSION}.json"
    ]


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


def test_committed_reparent_is_atomic_and_replays_as_one_delta(tmp_path: Path) -> None:
    run_id = RunId("live-edit")
    journal = open_journal(tmp_path, run_id, 1)
    for node in ("a", "b", "c"):
        journal.append(
            "node-added",
            detail={"definition": {"id": node, "persona": "engineer", "task": node}},
        )
    journal.append("edge-added", detail={"from": "a", "to": "c"})
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 2}})
    journal.append(
        "edit-committed",
        detail={
            "command": {"op": "reparent", "id": "c", "deps": ["b"]},
            "operations": [
                {"kind": "edge-removed", "detail": {"from": "a", "to": "c"}},
                {"kind": "edge-added", "detail": {"from": "b", "to": "c"}},
                {"kind": "reparent", "node": "c", "detail": {"from": ["a"], "to": ["b"]}},
            ],
        },
    )

    projection = project_run(tmp_path / "events.jsonl", run_id, 1)
    by_id = {node["id"]: node for node in projection.plan["tasks"]}
    assert by_id["c"]["deps"] == ["b"]
    assert [event.kind for event in read_strict_events(tmp_path / "events.jsonl", run_id)].count(
        "edit-committed"
    ) == 1


def test_invalid_committed_cycle_rejects_the_entire_delta() -> None:
    events = [
        _event("node-added", 1, detail={"definition": {"id": "a", "persona": "p", "task": "a"}}),
        _event("node-added", 2, detail={"definition": {"id": "b", "persona": "p", "task": "b"}}),
        _event("edge-added", 3, detail={"from": "a", "to": "b"}),
        _event("round-started", 4, detail={"plan": {"schema_version": 3}}),
        _event(
            "edit-committed",
            5,
            detail={
                "operations": [
                    {"kind": "edge-removed", "detail": {"from": "a", "to": "b"}},
                    {"kind": "edge-added", "detail": {"from": "b", "to": "a"}},
                    {"kind": "edge-added", "detail": {"from": "a", "to": "b"}},
                ]
            },
        ),
    ]
    with pytest.raises(ProjectionError, match="dependency cycle"):
        project_round(events, RunId("r"), 1)


def test_committed_drop_and_attestation_fold_live_state() -> None:
    events = [
        _event(
            "node-added",
            1,
            detail={"definition": {"id": "approve", "kind": "human", "task": "Approve"}},
        ),
        _event(
            "node-added", 2, detail={"definition": {"id": "work", "persona": "p", "task": "Work"}}
        ),
        _event("round-started", 3, detail={"plan": {"schema_version": 3}}),
        _event("human-waiting", 4, node="approve"),
        _event(
            "edit-committed",
            5,
            detail={
                "operations": [
                    {"kind": "human-attested", "node": "approve", "detail": {"ref": "approve"}}
                ]
            },
        ),
        _event(
            "edit-committed",
            6,
            detail={
                "operations": [
                    {"kind": "node-dropped", "node": "work", "detail": {"dependents": "drop"}}
                ]
            },
        ),
        _event(
            "edit-committed",
            7,
            detail={"operations": [{"kind": "completion-requested", "detail": {"reason": "done"}}]},
        ),
    ]
    projection = project_round(events, RunId("r"), 1)
    assert projection.node_states == {"approve": "done"}
    assert projection.attestations == ("approve",)
    assert [node["id"] for node in projection.plan["tasks"]] == ["approve"]


def test_every_compiled_edit_operation_kind_has_a_replay_handler() -> None:
    operations = [
        {
            "kind": "node-added",
            "detail": {"definition": {"id": "c", "persona": "p", "task": "c"}},
        },
        {"kind": "edge-added", "detail": {"from": "a", "to": "c"}},
        {"kind": "edge-removed", "detail": {"from": "a", "to": "b"}},
        {"kind": "reparent", "node": "b", "detail": {"deps": []}},
        {"kind": "retry-requested", "node": "a", "detail": {"replacement": "c"}},
        {"kind": "human-attested", "node": "approve", "detail": {"ref": "approve"}},
        {"kind": "completion-requested", "detail": {"reason": "done"}},
        {"kind": "node-dropped", "node": "b", "detail": {"dependents": "drop"}},
    ]
    assert {operation["kind"] for operation in operations} == EDIT_OPERATION_KINDS
    events = [
        _event("node-added", 1, detail={"definition": {"id": "a", "persona": "p", "task": "a"}}),
        _event(
            "node-added",
            2,
            detail={"definition": {"id": "b", "persona": "p", "task": "b"}},
        ),
        _event("edge-added", 3, detail={"from": "a", "to": "b"}),
        _event(
            "node-added",
            4,
            detail={"definition": {"id": "approve", "kind": "human", "task": "Approve"}},
        ),
        _event("round-started", 5, detail={"plan": {"schema_version": 3}}),
        _event("human-waiting", 6, node="approve"),
        _event("edit-committed", 7, detail={"operations": operations}),
    ]
    projection = project_round(events, RunId("r"), 1)
    assert [node["id"] for node in projection.plan["tasks"]] == ["a", "approve", "c"]


@pytest.mark.parametrize("status", ["cancelled", "done"])
def test_running_drop_replays_terminal_handoff_after_atomic_removal(status: str) -> None:
    events = [
        _event(
            "node-added", 1, detail={"definition": {"id": "work", "persona": "p", "task": "Work"}}
        ),
        _event(
            "node-added", 2, detail={"definition": {"id": "keep", "persona": "p", "task": "Keep"}}
        ),
        _event("round-started", 3, detail={"plan": {"schema_version": 3}}),
        _event("node-started", 4, node="work"),
        _event(
            "edit-committed",
            5,
            detail={
                "operations": [
                    {"kind": "node-dropped", "node": "work", "detail": {"dependents": "drop"}}
                ]
            },
        ),
        _event("node-settled", 6, node="work", detail={"status": status}),
    ]
    projection = project_round(events, RunId("r"), 1)
    assert [node["id"] for node in projection.plan["tasks"]] == ["keep"]
    assert projection.node_states == {}


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ({}, "contain a kind"),
        ({"kind": "node-added", "detail": "bad"}, "detail must be a mapping"),
        ({"kind": "node-added", "detail": {"definition": "bad"}}, "node definition"),
        ({"kind": "edge-added", "detail": {"from": 1, "to": "a"}}, "endpoints"),
        ({"kind": "edge-removed", "detail": {"from": "a", "to": "b"}}, "absent edge"),
        ({"kind": "node-dropped", "node": "missing"}, "unknown node"),
        ({"kind": "human-attested", "node": "a", "detail": {"ref": "a"}}, "currently waiting"),
        ({"kind": "future-edit"}, "unknown committed"),
    ],
)
def test_malformed_committed_operations_fail_atomically(operation, message: str) -> None:
    events = [
        _event("node-added", 1, detail={"definition": {"id": "a", "persona": "p", "task": "a"}}),
        _event("node-added", 2, detail={"definition": {"id": "b", "persona": "p", "task": "b"}}),
        _event("round-started", 3, detail={"plan": {"schema_version": 3}}),
        _event("edit-committed", 4, detail={"operations": [operation]}),
    ]
    with pytest.raises(ProjectionError, match=message):
        project_round(events, RunId("r"), 1)


def test_strict_reader_rejects_uncommitted_edit_vocabulary(tmp_path: Path) -> None:
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


def _node_added(seq: int, node_id: str, **extra: object) -> Event:
    return _event(
        "node-added",
        seq,
        detail={"definition": {"id": node_id, "persona": "p", "task": node_id, **extra}},
    )


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (
            [
                _node_added(1, "a"),
                _event("round-started", 2, detail={"plan": {}}),
                _event("edit-committed", 3, detail={"operations": []}),
            ],
            "non-empty operations",
        ),
        (
            [
                _node_added(1, "work"),
                _node_added(2, "keep"),
                _event("round-started", 3, detail={"plan": {}}),
                _event("node-started", 4, node="work"),
                _event(
                    "edit-committed",
                    5,
                    detail={
                        "operations": [
                            {
                                "kind": "node-dropped",
                                "node": "work",
                                "detail": {"dependents": "drop"},
                            }
                        ]
                    },
                ),
                _event("node-settled", 6, node="work", detail={"status": "failed"}),
            ],
            "must settle cancelled or finish publication",
        ),
        (
            [
                _node_added(1, "a"),
                _event("round-started", 2, detail={"plan": {}}),
                _event(
                    "edit-committed",
                    3,
                    detail={
                        "operations": [
                            {
                                "kind": "node-added",
                                "detail": {"definition": {"id": "a", "task": "x"}},
                            }
                        ]
                    },
                ),
            ],
            "duplicate node-added for 'a'",
        ),
        (
            [
                _node_added(1, "a"),
                _node_added(2, "b"),
                _event("edge-added", 3, detail={"from": "a", "to": "b"}),
                _event("round-started", 4, detail={"plan": {}}),
                _event(
                    "edit-committed",
                    5,
                    detail={
                        "operations": [{"kind": "edge-added", "detail": {"from": "a", "to": "b"}}]
                    },
                ),
            ],
            "duplicate edge-added",
        ),
        (
            [
                _event(
                    "node-added",
                    1,
                    detail={"definition": {"id": "approve", "kind": "human", "task": "Approve"}},
                ),
                _event("round-started", 2, detail={"plan": {}}),
                _event("human-waiting", 3, node="approve"),
                _event("human-attested", 4, node="approve", detail={"ref": "approve"}),
                _event(
                    "edit-committed",
                    5,
                    detail={
                        "operations": [
                            {
                                "kind": "human-attested",
                                "node": "approve",
                                "detail": {"ref": "approve"},
                            }
                        ]
                    },
                ),
            ],
            "attested more than once",
        ),
    ],
)
def test_committed_replay_rejects_invalid_deltas(events: list[Event], message: str) -> None:
    with pytest.raises(ProjectionError, match=message):
        project_round(events, RunId("r"), 1)


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (
            [
                _node_added(1, "a"),
                _event("round-started", 2, detail={"plan": {}}),
                _event("node-started", 3, node="a"),
                _event("node-settled", 4, node="a", detail={"status": "done"}),
                _event(
                    "round-finished",
                    5,
                    detail={
                        "result": {
                            "ok": True,
                            "state": "complete",
                            "started_order": ["a"],
                            "results": {"a": {"status": "done"}},
                        }
                    },
                ),
                _event("node-started", 6, node="a"),
            ],
            "follows round-finished",
        ),
        (
            [_node_added(1, "a"), _event("node-started", 2, node="a")],
            "precedes round-started",
        ),
        (
            [
                _node_added(1, "a"),
                _event("round-started", 2, detail={"plan": {}}),
                _node_added(3, "b"),
            ],
            "follows round-started",
        ),
        (
            [
                _node_added(1, "a"),
                _event("round-started", 2, detail={"plan": {}}),
                _event("round-started", 3, detail={"plan": {}}),
            ],
            "may occur only once",
        ),
        (
            [_node_added(1, "a"), _event("round-started", 2, detail={"plan": "bad"})],
            "requires plan metadata",
        ),
        (
            [
                _node_added(1, "a"),
                _event("round-started", 2, detail={"plan": {}}),
                _event("human-waiting", 3, node="missing"),
            ],
            "human-waiting references unknown node",
        ),
        (
            [
                _node_added(1, "a"),
                _event("round-started", 2, detail={"plan": {}}),
                _event("node-started", 3, node="a"),
                _event("human-waiting", 4, node="a"),
            ],
            "started more than once",
        ),
        (
            [
                _node_added(1, "a"),
                _event("round-started", 2, detail={"plan": {}}),
                _event("node-started", 3, node="a"),
                _event(
                    "round-finished",
                    4,
                    detail={
                        "result": {
                            "ok": True,
                            "state": "complete",
                            "started_order": ["a"],
                            "results": {"a": {"status": "done"}},
                        }
                    },
                ),
            ],
            "disagrees with projected node",
        ),
        ([_node_added(1, "a")], "no round-started event"),
    ],
)
def test_projection_rejects_out_of_order_authoritative_events(
    events: list[Event], message: str
) -> None:
    with pytest.raises(ProjectionError, match=message):
        project_round(events, RunId("r"), 1)


def test_strict_reader_rejects_unknown_envelope_fields(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "seq": 1,
                "at": 0,
                "kind": "round-started",
                "run_id": "r",
                "round": 1,
                "bogus": True,
            }
        )
        + "\n"
    )
    with pytest.raises(ProjectionError, match="has unknown fields: bogus"):
        read_strict_events(path, RunId("r"))


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


def test_a_committed_edit_carries_the_command_that_produced_it() -> None:
    """Replay reads the submitted command, not an inference from the mutations."""
    submitted = {"op": "drop", "id": "work", "dependents": "drop"}
    events = [
        _event(
            "node-added", 1, detail={"definition": {"id": "keep", "persona": "p", "task": "Keep"}}
        ),
        _event(
            "node-added", 2, detail={"definition": {"id": "work", "persona": "p", "task": "Work"}}
        ),
        _event("round-started", 3, detail={"plan": {"schema_version": 3}}),
        _event(
            "edit-committed",
            4,
            detail={
                "command": submitted,
                "operations": [
                    {"kind": "node-dropped", "node": "work", "detail": {"dependents": "drop"}}
                ],
            },
        ),
    ]
    projection = project_round(events, RunId("r"), 1)
    assert [node["id"] for node in projection.plan["tasks"]] == ["keep"]
    committed = next(event for event in events if event.kind == "edit-committed")
    assert committed.detail["command"] == submitted

    for broken in ({"op": "nonsense"}, ["drop"]):
        corrupt = [
            *events[:3],
            _event("edit-committed", 4, detail={**committed.detail, "command": broken}),
        ]
        with pytest.raises(ProjectionError, match="known edit payload"):
            project_round(corrupt, RunId("r"), 1)


def test_a_v5_log_still_replays_without_the_command_a_v6_record_must_carry() -> None:
    """The bump is additive, and both directions of that claim are checked here."""
    operations = [{"kind": "node-dropped", "node": "work", "detail": {"dependents": "drop"}}]
    prelude = [
        _event(
            "node-added", 1, detail={"definition": {"id": "keep", "persona": "p", "task": "Keep"}}
        ),
        _event(
            "node-added", 2, detail={"definition": {"id": "work", "persona": "p", "task": "Work"}}
        ),
        _event("round-started", 3, detail={"plan": {"schema_version": 3}}),
    ]
    legacy = replace(_event("edit-committed", 4, detail={"operations": operations}), version=5)
    projection = project_round([*prelude, legacy], RunId("r"), 1)
    assert [node["id"] for node in projection.plan["tasks"]] == ["keep"]

    # The same record at the current version is incomplete: a reader would have to
    # infer the disposition the reconciler was actually given.
    current = replace(legacy, version=SCHEMA_VERSION)
    with pytest.raises(ProjectionError, match="requires the submitted command"):
        project_round([*prelude, current], RunId("r"), 1)


def test_a_committed_edit_round_trips_its_command_through_the_journal(tmp_path: Path) -> None:
    """Written by the real journal, read back by the strict reader, unchanged."""
    run_id = RunId("round-trip")
    journal = open_journal(tmp_path, run_id, 1)
    journal.append(
        "node-added", detail={"definition": {"id": "keep", "persona": "p", "task": "Keep"}}
    )
    journal.append(
        "node-added", detail={"definition": {"id": "work", "persona": "p", "task": "Work"}}
    )
    journal.append("round-started", detail={"plan": {"schema_version": 3}})
    command = {"op": "drop", "id": "work", "dependents": "drop"}
    written = journal.append(
        "edit-committed",
        detail={
            "command": command,
            "operations": [
                {"kind": "node-dropped", "node": "work", "detail": {"dependents": "drop"}}
            ],
        },
    )
    assert written.version == SCHEMA_VERSION

    events = read_strict_events(tmp_path / JOURNAL_NAME, run_id)
    committed = next(event for event in events if event.kind == "edit-committed")
    assert committed.version == SCHEMA_VERSION
    assert committed.detail["command"] == command
    assert [node["id"] for node in project_round(events, run_id, 1).plan["tasks"]] == ["keep"]
