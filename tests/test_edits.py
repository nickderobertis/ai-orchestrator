from __future__ import annotations

import pytest

from orchestrator.edits import EditCommand, EditError, apply_edit, graph_mapping, parse_commands
from orchestrator.graph import parse_graph


def _graph():
    return parse_graph(
        {
            "schema_version": 3,
            "tasks": [
                {"id": "root", "persona": "engineer", "task": "Root"},
                {
                    "id": "leaf",
                    "persona": "engineer",
                    "task": "Leaf",
                    "deps": ["root"],
                },
                {"id": "approve", "kind": "human", "task": "Approve"},
            ],
        }
    )


def test_add_and_reparent_validate_against_the_live_graph() -> None:
    graph, events = apply_edit(
        _graph(),
        EditCommand(
            "add",
            {
                "op": "add",
                "node": {
                    "id": "followup",
                    "persona": "engineer",
                    "task": "Follow up",
                    "deps": ["leaf"],
                },
            },
        ),
        states={},
        attestations=(),
    )
    assert graph.tasks[-1].deps == ["leaf"]
    assert [event["kind"] for event in events] == ["node-added", "edge-added"]

    with pytest.raises(EditError, match="dependency cycle"):
        apply_edit(
            graph,
            EditCommand("reparent", {"op": "reparent", "id": "root", "deps": ["followup"]}),
            states={},
            attestations=(),
        )


def test_invalid_delta_does_not_mutate_the_input_graph() -> None:
    graph = _graph()
    with pytest.raises(EditError, match="define dependents"):
        apply_edit(
            graph,
            EditCommand("drop", {"op": "drop", "id": "root"}),
            states={},
            attestations=(),
        )
    assert [node.id for node in graph.tasks] == ["root", "leaf", "approve"]
    assert graph.tasks[1].deps == ["root"]


def test_retry_and_attestation_require_the_current_frontier() -> None:
    replacement = {
        "id": "root_retry",
        "persona": "engineer",
        "task": "Retry root",
    }
    _, running_retry = apply_edit(
        _graph(),
        EditCommand("retry", {"op": "retry", "id": "root", "node": replacement}),
        states={"root": "running"},
        attestations=(),
    )
    assert running_retry[0]["kind"] == "retry-requested"
    _, retry_events = apply_edit(
        _graph(),
        EditCommand("retry", {"op": "retry", "id": "root", "node": replacement}),
        states={"root": "failed"},
        attestations=(),
    )
    assert retry_events[0] == {
        "kind": "retry-requested",
        "node": "root",
        "detail": {"replacement": "root_retry", "reset": ["leaf"]},
    }
    assert retry_events[-2:] == [
        {"kind": "edge-removed", "detail": {"from": "root", "to": "leaf"}},
        {"kind": "edge-added", "detail": {"from": "root_retry", "to": "leaf"}},
    ]

    with pytest.raises(EditError, match="currently-ready"):
        apply_edit(
            _graph(),
            EditCommand("attest", {"op": "attest", "ref": "approve"}),
            states={"approve": "pending"},
            attestations=(),
        )


def test_drop_detach_cascade_anchor_and_complete_deltas() -> None:
    detached, events = apply_edit(
        _graph(),
        EditCommand("drop", {"op": "drop", "id": "root", "dependents": "detach"}),
        states={},
        attestations=(),
    )
    assert [node.id for node in detached.tasks] == ["leaf", "approve"]
    assert detached.tasks[0].deps == []
    assert [event["kind"] for event in events] == ["edge-removed", "node-dropped"]

    cascaded, events = apply_edit(
        _graph(),
        EditCommand("drop", {"op": "drop", "id": "root", "dependents": "drop"}),
        states={},
        attestations=(),
    )
    assert [node.id for node in cascaded.tasks] == ["approve"]
    assert [event["node"] for event in events] == ["leaf", "root"]

    same_repo = parse_graph(
        {
            "schema_version": 3,
            "tasks": [
                {"id": "anchor", "repo": "acme/widget", "task": "No diff", "expects_no_diff": True},
                {
                    "id": "stacked",
                    "repo": "acme/widget",
                    "task": "No diff",
                    "expects_no_diff": True,
                    "deps": ["anchor"],
                },
            ],
        }
    )
    with pytest.raises(EditError, match="last unresolved publication anchor"):
        apply_edit(
            same_repo,
            EditCommand("drop", {"op": "drop", "id": "anchor", "dependents": "detach"}),
            states={},
            attestations=(),
        )

    unchanged, complete = apply_edit(
        _graph(),
        EditCommand("complete", {"op": "complete", "reason": "published"}),
        states={},
        attestations=(),
    )
    assert unchanged.tasks[0].id == "root"
    assert complete == [{"kind": "completion-requested", "detail": {"reason": "published"}}]


def test_attestation_and_command_boundary_rejections() -> None:
    _, events = apply_edit(
        _graph(),
        EditCommand("attest", {"op": "attest", "ref": "approve"}),
        states={"approve": "waiting"},
        attestations=(),
    )
    assert events[0]["kind"] == "human-attested"
    with pytest.raises(EditError, match="already attested"):
        apply_edit(
            _graph(),
            EditCommand("attest", {"op": "attest", "ref": "approve"}),
            states={"approve": "waiting"},
            attestations=("approve",),
        )

    assert parse_commands({}) == ()
    with pytest.raises(EditError, match="version 1"):
        parse_commands({"version": 2, "commands": []})
    with pytest.raises(EditError, match="must be a list"):
        parse_commands({"version": 1, "commands": {}})
    with pytest.raises(EditError, match="unknown op"):
        parse_commands({"version": 1, "commands": [{"op": "split"}]})


@pytest.mark.parametrize(
    ("command", "message"),
    [
        (EditCommand("add", {"op": "add", "node": "bad"}), "node mapping"),
        (EditCommand("reparent", {"op": "reparent", "id": "missing", "deps": []}), "existing"),
        (EditCommand("reparent", {"op": "reparent", "id": "leaf", "deps": "bad"}), "list"),
        (EditCommand("drop", {"op": "drop", "id": "missing", "dependents": "drop"}), "existing"),
        (
            EditCommand("retry", {"op": "retry", "id": "root", "node": {}}),
            "running, failed, or cancelled",
        ),
        (EditCommand("complete", {"op": "complete", "reason": 1}), "string reason"),
        (EditCommand("context", {"op": "context", "id": "missing", "note": "n"}), "existing"),
        (EditCommand("context", {"op": "context", "id": "leaf", "note": "  "}), "non-empty note"),
    ],
)
def test_malformed_delta_variants_are_rejected(command: EditCommand, message: str) -> None:
    with pytest.raises(EditError, match=message):
        apply_edit(_graph(), command, states={}, attestations=())


def test_graph_mapping_falls_back_for_programmatic_nodes() -> None:
    graph = _graph()
    graph.tasks[0].definition.clear()
    assert graph_mapping(graph)["tasks"][0] == {
        "id": "root",
        "kind": "agent",
        "task": "Root",
    }


def test_retry_replacement_shape_and_lineage_are_validated() -> None:
    graph = _graph()
    with pytest.raises(EditError, match="existing graph node"):
        apply_edit(
            graph,
            EditCommand(
                "retry",
                {"op": "retry", "id": "dropped", "node": {"id": "replacement"}},
            ),
            states={"dropped": "cancelled"},
            attestations=(),
        )
    assert [node.id for node in graph.tasks] == ["root", "leaf", "approve"]

    with pytest.raises(EditError, match="replacement node mapping"):
        apply_edit(
            _graph(),
            EditCommand("retry", {"op": "retry", "id": "root", "node": "bad"}),
            states={"root": "failed"},
            attestations=(),
        )
    with pytest.raises(EditError, match="must be new"):
        apply_edit(
            _graph(),
            EditCommand("retry", {"op": "retry", "id": "root", "node": {"id": "leaf"}}),
            states={"root": "failed"},
            attestations=(),
        )
    # A retry answering "which branch does this change live on?" twice is refused at
    # submission rather than resolved silently: the lifecycle honours the checkpoint's
    # branch and ignores the pin, so the planner would not get the branch it named.
    with pytest.raises(EditError, match="pins branch 'wanted' but resumes branch 'other'"):
        apply_edit(
            _graph(),
            EditCommand(
                "retry",
                {
                    "op": "retry",
                    "id": "root",
                    "node": {
                        "id": "replacement",
                        "repo": "acme/widget",
                        "task": "Continue",
                        "branch": "wanted",
                        "resume": {"branch": "other", "checkpoint": "abc"},
                    },
                },
            ),
            states={"root": "failed"},
            attestations=(),
        )
    _, events = apply_edit(
        _graph(),
        EditCommand(
            "retry",
            {
                "op": "retry",
                "id": "root",
                "node": {
                    "id": "replacement",
                    "task": "No diff",
                    "expects_no_diff": True,
                    "deps": ["approve"],
                },
            },
        ),
        states={"root": "cancelled"},
        attestations=(),
    )
    assert events[1] == {
        "kind": "node-added",
        "detail": {
            "definition": {
                "id": "replacement",
                "task": "No diff",
                "expects_no_diff": True,
            },
            "retry_of": "root",
        },
    }
    assert events[2] == {
        "kind": "edge-added",
        "detail": {"from": "approve", "to": "replacement"},
    }

    with pytest.raises(EditError, match="duplicate task id"):
        apply_edit(
            _graph(),
            EditCommand("add", {"op": "add", "node": {"id": "root", "task": "duplicate"}}),
            states={},
            attestations=(),
        )


def test_reparent_rejects_an_already_started_node() -> None:
    with pytest.raises(EditError, match="unstarted node"):
        apply_edit(
            _graph(),
            EditCommand("reparent", {"op": "reparent", "id": "leaf", "deps": []}),
            states={"leaf": "running"},
            attestations=(),
        )


def test_drop_cascade_visits_each_reachable_dependent_once() -> None:
    diamond = parse_graph(
        {
            "schema_version": 3,
            "tasks": [
                {"id": "keep", "persona": "engineer", "task": "Keep"},
                {"id": "root", "persona": "engineer", "task": "Root"},
                {"id": "left", "persona": "engineer", "task": "Left", "deps": ["root"]},
                {"id": "right", "persona": "engineer", "task": "Right", "deps": ["root"]},
                {
                    "id": "sink",
                    "persona": "engineer",
                    "task": "Sink",
                    "deps": ["left", "right"],
                },
            ],
        }
    )
    graph, events = apply_edit(
        diamond,
        EditCommand("drop", {"op": "drop", "id": "root", "dependents": "drop"}),
        states={},
        attestations=(),
    )
    assert [node.id for node in graph.tasks] == ["keep"]
    # ``sink`` is reachable through both ``left`` and ``right``; the cascade removes
    # it exactly once rather than emitting a duplicate node-dropped event.
    dropped = [event["node"] for event in events if event["kind"] == "node-dropped"]
    assert sorted(dropped) == ["left", "right", "root", "sink"]


def test_context_reaches_the_live_node_and_accumulates_within_the_round() -> None:
    """A note is data on the node and prose in the task the next dispatch receives."""
    graph, events = apply_edit(
        _graph(),
        EditCommand("context", {"op": "context", "id": "leaf", "note": "the gate is green"}),
        states={"leaf": "failed"},
        attestations=(),
    )
    assert events == [
        {"kind": "context-added", "node": "leaf", "detail": {"note": "the gate is green"}}
    ]
    leaf = next(node for node in graph.tasks if node.id == "leaf")
    assert leaf.definition["context"] == ["the gate is green"]
    assert leaf.direct is not None
    assert leaf.direct.task.startswith("Leaf\n\n## Planner context")
    assert "the gate is green" in leaf.direct.task

    graph, _ = apply_edit(
        graph,
        EditCommand("context", {"op": "context", "id": "leaf", "note": "one finding is open"}),
        states={"leaf": "failed"},
        attestations=(),
    )
    leaf = next(node for node in graph.tasks if node.id == "leaf")
    assert leaf.definition["context"] == ["the gate is green", "one finding is open"]
    # Composing a second time renders both notes once: the stored notes are the
    # source, never the already-rendered task text.
    assert leaf.direct is not None
    assert leaf.direct.task.count("## Planner context") == 1


def test_context_is_refused_where_no_dispatch_can_ever_read_it() -> None:
    """A settled-done node leaves the round, and a human node has no dispatch."""
    with pytest.raises(EditError, match="can still be dispatched"):
        apply_edit(
            _graph(),
            EditCommand("context", {"op": "context", "id": "leaf", "note": "too late"}),
            states={"leaf": "done"},
            attestations=(),
        )
    with pytest.raises(EditError, match="cannot set 'context'"):
        apply_edit(
            _graph(),
            EditCommand("context", {"op": "context", "id": "approve", "note": "for a person"}),
            states={"approve": "waiting"},
            attestations=(),
        )
