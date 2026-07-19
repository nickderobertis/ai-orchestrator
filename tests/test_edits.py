from __future__ import annotations

import pytest

from orchestrator.edits import EditCommand, EditError, apply_edit
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
        "detail": {"replacement": "root_retry"},
    }

    with pytest.raises(EditError, match="currently-ready"):
        apply_edit(
            _graph(),
            EditCommand("attest", {"op": "attest", "ref": "approve"}),
            states={"approve": "pending"},
            attestations=(),
        )
