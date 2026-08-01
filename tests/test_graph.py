"""Unit tests for the canonical tracked graph executor."""

from __future__ import annotations

import json
import os
import shlex
import socket
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import NotRequired, cast, get_origin, get_type_hints

import pytest

from orchestrator.channel import QueuedCommand, create_channel
from orchestrator.config import ConfigError
from orchestrator.coordination import atomic_json
from orchestrator.dispatch import DispatchError, Report
from orchestrator.edits import EditCommand
from orchestrator.gitops import GitError
from orchestrator.goals import register_run
from orchestrator.graph import (
    GraphNode,
    HumanAction,
    _replay_node_run,
    first_line,
    graph_payload,
    infrastructure_failure_detail,
    load_graph,
    main,
    main_repo_plan,
    parse_graph,
    print_continuation,
    render_actions,
    run_graph,
)
from orchestrator.journal import JournalError, NodeSink, open_journal
from orchestrator.lifecycle import LifecycleResult, RepoPlanNode, Step, StepResult, result_payload
from orchestrator.plan import PLAN_SCHEMA_VERSION, NodeRun, PlanError, PlanNode
from orchestrator.runs import (
    RECORDED_RESULT_SCHEMA_VERSION,
    ArtifactPaths,
    GraphResultItem,
    NodeId,
    ResumePayload,
    RunId,
    StepResultPayload,
)
from orchestrator.verify import VerifyResult


def _report(persona: str, completed: bool = True, assessment: str | None = None) -> Report:
    return Report(
        persona=persona,
        exit_code=0 if completed else 1,
        completed=completed,
        stopped_early=False,
        assistant_turns=1,
        verdicts=[],
        usage={},
        raw={},
        stderr="",
        assessment=assessment,
    )


class _RecordingProposalPump:
    def __init__(self) -> None:
        self.proposals: list[tuple[str, str]] = []
        self.blocking_proposals: list[tuple[str, str]] = []
        self.drains = 0

    def propose(self, node: str, message: str) -> None:
        self.proposals.append((node, message))

    def propose_blocking(self, node: str, message: str) -> None:
        self.blocking_proposals.append((node, message))

    def defer_blocking(self, node: str, message: str) -> None:
        self.blocking_proposals.append((node, message))

    def persist_replies(self) -> None:
        self.drains += 1

    def close(self) -> None:
        self.persist_replies()


class _EditingPump(_RecordingProposalPump):
    def __init__(self, commands: list[EditCommand], *, wait_ticks: int = 0) -> None:
        super().__init__()
        self.commands = commands
        self.wait_ticks = wait_ticks
        self.outcomes: list[tuple[int, bool, str]] = []

    def drain_commands(self) -> tuple[QueuedCommand, ...]:
        if self.wait_ticks:
            self.wait_ticks -= 1
            return ()
        commands, self.commands = self.commands, []
        return tuple(
            QueuedCommand(seq, 1, command) for seq, command in enumerate(commands, start=1)
        )

    def record_outcome(self, seq: int, *, applied: bool, reason: str) -> None:
        self.outcomes.append((seq, applied, reason))


def test_run_graph_enqueues_worker_assessment_through_reconciler() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {"id": "discoverer", "persona": "engineer", "task": "Investigate"},
                {"id": "quiet", "persona": "engineer", "task": "No discovery"},
                {"id": "explicit-none", "persona": "engineer", "task": "No follow-up"},
            ]
        }
    )
    pump = _RecordingProposalPump()
    assessments = {"discoverer": "follow up", "quiet": None, "explicit-none": "None"}
    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona, assessment=assessments[node.id]),
        lifecycle_runner=lambda node, **_: _lifecycle(),
        proposal_pump=pump,  # type: ignore[arg-type] - narrow transport test double
    )
    assert result.state == "complete"
    assert pump.proposals == [("discoverer", "follow up")]
    assert pump.drains >= 2


def test_run_graph_records_and_surfaces_terminal_infrastructure_failure() -> None:
    graph = parse_graph(
        {"tasks": [{"id": "broken", "persona": "engineer", "task": "Dispatch work"}]}
    )
    pump = _RecordingProposalPump()
    detail = "provider error (respond): harness unavailable"

    def fail(_node: PlanNode, **_kwargs) -> Report:
        raise RuntimeError(detail)

    result = run_graph(
        graph,
        agent_runner=fail,
        lifecycle_runner=lambda node, **_: _lifecycle(),
        proposal_pump=pump,  # type: ignore[arg-type] - narrow transport test double
    )

    assert result.state == "failed"
    item = graph_payload(result)["results"]["broken"]
    assert item["status"] == "failed"
    assert item["outcome"] == "infrastructure-failure"
    assert item["error"] == detail
    assert pump.blocking_proposals == [
        ("broken", f"terminal infrastructure failure; dispatch cannot run: {detail}")
    ]


def test_round_budget_surfaces_and_cooperatively_cancels_wedged_dispatch() -> None:
    graph = parse_graph({"tasks": [{"id": "a", "persona": "engineer", "task": "wait"}]})
    pump = _RecordingProposalPump()

    def runner(node: PlanNode, *, cancel: threading.Event | None = None, **_kwargs) -> Report:
        assert cancel is not None
        assert cancel.wait(1)
        return _report(node.persona, completed=False)

    result = run_graph(
        graph,
        agent_runner=runner,
        lifecycle_runner=lambda node, **_: _lifecycle(),
        proposal_pump=pump,  # type: ignore[arg-type] - narrow transport test double
        round_budget=0.01,
    )

    assert result.state == "failed"
    assert pump.blocking_proposals == [
        (
            "round-budget",
            "round exceeded its 0.01s liveness budget; in-flight workers were cancelled "
            "and planner intervention is required",
        )
    ]


def test_reconciler_alone_applies_and_rejects_live_commands() -> None:
    graph = parse_graph(
        {
            "schema_version": 3,
            "tasks": [
                {"id": "approve", "kind": "human", "task": "Approve"},
                {
                    "id": "pending",
                    "task": "No diff",
                    "expects_no_diff": True,
                    "deps": ["approve"],
                },
            ],
        }
    )
    pump = _EditingPump([EditCommand("attest", {"op": "attest", "ref": "approve"})], wait_ticks=1)
    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
        proposal_pump=pump,  # type: ignore[arg-type] - focused in-memory command pump
    )
    assert result.results["approve"].status == "done"
    assert result.results["pending"].status == "done"

    rejected = _EditingPump(
        [EditCommand("reparent", {"op": "reparent", "id": "pending", "deps": ["pending"]})]
    )
    run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
        proposal_pump=rejected,  # type: ignore[arg-type] - focused in-memory command pump
    )
    assert rejected.proposals[0][0] == "reconciler"
    assert "depends on itself" in rejected.proposals[0][1]


def test_a_rejected_edit_is_journalled_with_its_command_and_reason(tmp_path: Path) -> None:
    """A rejection is evidence: the surfaced proposal is transient, the event is not."""
    graph = parse_graph(
        {
            "tasks": [
                {"id": "approve", "kind": "human", "task": "Approve"},
                {"id": "pending", "persona": "engineer", "task": "Ship", "deps": ["approve"]},
            ]
        }
    )
    journal = open_journal(tmp_path / "run-r", RunId("run-r"), 1)
    command = EditCommand("attest", {"op": "attest", "ref": "pending"})
    run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
        journal=journal,
        run_id=RunId("run-r"),
        round_number=1,
        proposal_pump=_EditingPump([command]),  # type: ignore[arg-type] - in-memory pump
    )
    events = [event for event in journal.events() if event.kind == "edit-rejected"]
    assert [event.detail["command"] for event in events] == [command.payload]
    assert "currently-ready human action" in str(events[0].detail["reason"])
    assert events[0].node is None


def test_running_direct_and_lifecycle_drops_cancel_cooperatively() -> None:
    direct = parse_graph(
        {
            "tasks": [
                {"id": "direct", "persona": "engineer", "task": "Wait"},
                {"id": "keep", "kind": "human", "task": "Keep run alive"},
            ]
        }
    )
    direct_pump = _EditingPump(
        [EditCommand("drop", {"op": "drop", "id": "direct", "dependents": "drop"})],
        wait_ticks=1,
    )

    def direct_runner(
        node: PlanNode,
        *,
        labels: Mapping[str, str] | None = None,
        cancel: threading.Event | None = None,
    ) -> Report:
        assert cancel is not None and cancel.wait(1)
        return _report(node.persona, completed=False)

    direct_result = run_graph(
        direct,
        agent_runner=direct_runner,
        lifecycle_runner=lambda node, **_: _lifecycle(),
        proposal_pump=direct_pump,  # type: ignore[arg-type] - focused in-memory command pump
    )
    assert set(direct_result.results) == {"keep"}

    lifecycle_graph = parse_graph(
        {
            "tasks": [
                {"id": "repo", "repo": "acme/widget", "persona": "engineer", "task": "Wait"},
                {"id": "keep", "kind": "human", "task": "Keep run alive"},
            ]
        }
    )
    lifecycle_pump = _EditingPump(
        [EditCommand("drop", {"op": "drop", "id": "repo", "dependents": "drop"})],
        wait_ticks=1,
    )

    def lifecycle_runner(
        node: RepoPlanNode,
        *,
        journal: NodeSink | None = None,
        cancel: threading.Event | None = None,
    ) -> LifecycleResult:
        assert cancel is not None and cancel.wait(1)
        return _lifecycle()

    lifecycle_result = run_graph(
        lifecycle_graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lifecycle_runner,
        proposal_pump=lifecycle_pump,  # type: ignore[arg-type] - focused in-memory command pump
    )
    assert set(lifecycle_result.results) == {"keep"}


def test_retry_of_a_dropped_node_is_rejected_and_reconciliation_continues() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {"id": "running", "persona": "engineer", "task": "Wait"},
                {"id": "keep", "kind": "human", "task": "Keep run alive"},
            ]
        }
    )
    pump = _EditingPump(
        [
            EditCommand("drop", {"op": "drop", "id": "running", "dependents": "drop"}),
            EditCommand(
                "retry",
                {
                    "op": "retry",
                    "id": "running",
                    "node": {"id": "replacement", "task": "No diff", "expects_no_diff": True},
                },
            ),
            EditCommand("attest", {"op": "attest", "ref": "keep"}),
        ],
        wait_ticks=1,
    )

    def runner(
        node: PlanNode,
        *,
        labels: Mapping[str, str] | None = None,
        cancel: threading.Event | None = None,
    ) -> Report:
        assert cancel is not None and cancel.wait(1)
        return _report(node.persona, completed=False)

    result = run_graph(
        graph,
        agent_runner=runner,
        lifecycle_runner=lambda node, **_: _lifecycle(),
        proposal_pump=pump,  # type: ignore[arg-type] - focused in-memory command pump
    )
    assert set(result.results) == {"keep"}
    assert result.results["keep"].status == "done"
    assert pump.proposals == [
        ("reconciler", "rejected retry: retry requires an existing graph node")
    ]


def test_reconciler_retries_failed_and_drops_unstarted_nodes() -> None:
    retry_graph = parse_graph(
        {
            "schema_version": 3,
            "tasks": [
                {"id": "failed", "persona": "engineer", "task": "Failed"},
                {
                    "id": "dependent",
                    "task": "No diff",
                    "expects_no_diff": True,
                    "deps": ["failed"],
                },
                {"id": "keep", "kind": "human", "task": "Keep"},
            ],
        }
    )
    retry_pump = _EditingPump(
        [
            EditCommand(
                "retry",
                {
                    "op": "retry",
                    "id": "failed",
                    "node": {"id": "replacement", "task": "No diff", "expects_no_diff": True},
                },
            )
        ]
    )
    retried = run_graph(
        retry_graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
        replayed_runs={
            "failed": NodeRun("failed", "failed earlier"),
            "dependent": NodeRun("skipped", "dependency failed"),
        },
        proposal_pump=retry_pump,  # type: ignore[arg-type] - focused in-memory command pump
    )
    assert retried.results["replacement"].status == "done"
    assert retried.results["dependent"].status == "done"

    drop_graph = parse_graph(
        {
            "schema_version": 3,
            "tasks": [
                {"id": "keep", "kind": "human", "task": "Keep"},
                {"id": "pending", "task": "No diff", "expects_no_diff": True, "deps": ["keep"]},
            ],
        }
    )
    drop_pump = _EditingPump(
        [EditCommand("drop", {"op": "drop", "id": "pending", "dependents": "drop"})]
    )
    dropped = run_graph(
        drop_graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
        proposal_pump=drop_pump,  # type: ignore[arg-type] - focused in-memory command pump
    )
    assert set(dropped.results) == {"keep"}


def test_main_validates_and_services_inherited_proposal_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "worker", "persona": "engineer", "task": "Work"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_ORCHESTRATOR_CHANNEL_DIR", str(tmp_path / "channel"))
    assert main([str(plan), "--no-record"]) == 2
    assert "incomplete proposal channel" in capsys.readouterr().err
    channel = create_channel(tmp_path / "outer")
    monkeypatch.setenv("AI_ORCHESTRATOR_CHANNEL_DIR", str(channel))
    monkeypatch.setenv("AI_ORCHESTRATOR_CHANNEL_RUN_ID", "outer")

    pumps: list[_RecordingProposalPump] = []
    dispatched: list[bool] = []
    runs_dir = tmp_path / "runs space;still-one-argument"

    def fake_check_in(persona: str, task: str, **kwargs: object) -> Report:
        assert persona == "check-in"
        assert "Journal:" in task and "Monitor details:" in task
        command = task.split("Check-in command: ", 1)[1].splitlines()[0]
        assert shlex.split(command) == [
            "just",
            "channel-surface",
            "outer",
            "MESSAGE",
            "--runs-dir",
            str(runs_dir.resolve()),
        ]
        atomic_json(
            channel / "heartbeat-surface.json",
            {
                "op": "supervisor",
                "run_id": "outer",
                "round": 1,
                "surface": {
                    "kind": "heartbeat",
                    "message": "worker: running; follow-ups: none",
                    "blocking": False,
                },
                "messages": [],
            },
        )
        return _report(persona)

    def make_pump(
        path: Path, run_id: str, round_number: int, **kwargs: object
    ) -> _RecordingProposalPump:
        assert (path, run_id, round_number) == (channel, "outer", 1)
        dispatch_check_in = kwargs.get("dispatch_check_in")
        assert callable(dispatch_check_in)
        dispatch_check_in()
        dispatched.append(True)
        pump = _RecordingProposalPump()
        pumps.append(pump)
        return pump

    monkeypatch.setattr("orchestrator.graph.ProposalPump", make_pump)
    monkeypatch.setattr("orchestrator.graph.dispatch", fake_check_in)
    monkeypatch.setattr(
        "orchestrator.graph.make_dispatch_runner",
        lambda **kwargs: lambda node, **labels: _report(node.persona),
    )
    assert main([str(plan), "--no-record"]) == 0
    assert not pumps
    assert main([str(plan), "--run", "recorded", "--runs-dir", str(runs_dir)]) == 0
    assert len(pumps) == 1 and pumps[0].drains >= 2
    assert dispatched == [True]
    assert (runs_dir / "recorded" / "round-01" / "result.json").is_file()

    for key in ("AI_ORCHESTRATOR_CHANNEL_DIR", "AI_ORCHESTRATOR_CHANNEL_RUN_ID"):
        monkeypatch.delenv(key)


def _lifecycle(outcome: str = "merged", **kw) -> LifecycleResult:
    base = {
        "repo": "o/r",
        "task": "change",
        "persona": "engineer",
        "base_branch": "main",
        "branch": "feature",
        "outcome": outcome,
    }
    base.update(kw)
    return LifecycleResult(**base)


def test_run_graph_journals_what_the_round_actually_did(tmp_path: Path) -> None:
    """The journal is written by the real scheduler, not by a stand-in for it.

    Only the transitions `run_graph` itself owns are asserted here. A lifecycle's
    own events (branch-discovered, step-settled, verification-*) are journaled from
    inside the lifecycle against the scope it is handed, so they are proven there —
    against the real lifecycle — rather than against a runner double that could
    only ever restate what this test told it to say.
    """
    journal = open_journal(tmp_path / "run-j", RunId("run-j"), 1)
    graph = parse_graph(
        {
            "tasks": [
                {"id": "direct", "persona": "planner", "task": "Plan"},
                {"id": "repo", "repo": "o/r", "persona": "engineer", "task": "Patch"},
                {"id": "review", "kind": "human", "task": "Approve the change", "deps": ["repo"]},
            ]
        }
    )

    run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(outcome="merged", branch="feature"),
        journal=journal,
        run_id=RunId("run-j"),
        round_number=1,
    )

    events = journal.events()
    seen = {(e.kind, e.node, e.step) for e in events}
    assert ("node-started", "direct", None) in seen
    assert ("node-settled", "direct", None) in seen
    assert ("node-started", "repo", None) in seen
    assert ("node-settled", "repo", None) in seen
    assert ("human-waiting", "review", None) in seen
    # Sequence numbers are dense and monotonic even though nodes ran concurrently.
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    settled = next(e for e in events if e.kind == "node-settled" and e.node == "repo")
    assert settled.detail["status"] == "done"
    assert settled.detail["outcome"] == "merged"
    assert (
        settled.detail["result"]
        == graph_payload(
            run_graph(
                parse_graph(
                    {
                        "tasks": [
                            {"id": "repo", "repo": "o/r", "persona": "engineer", "task": "Patch"}
                        ]
                    }
                ),
                agent_runner=lambda node, **_: _report(node.persona),
                lifecycle_runner=lambda node, **_: _lifecycle(outcome="merged", branch="feature"),
            )
        )["results"]["repo"]
    )
    assert all(e.round == 1 and e.run_id == "run-j" for e in events)


def test_replayed_lifecycle_result_preserves_payload_and_unblocks_stack_dependency() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {"id": "parent", "repo": "o/r", "persona": "engineer", "task": "Parent"},
                {
                    "id": "child",
                    "repo": "o/r",
                    "persona": "engineer",
                    "task": "Child",
                    "deps": ["parent"],
                },
            ]
        }
    )
    parent_item = {
        "kind": "agent",
        "status": "done",
        "task": "Parent",
        "repo": "o/r",
        "branch": "feature/parent",
        "base_branch": "main",
        "pr_base": "main",
        "stack_bases": [
            {
                "branch": "feature/grandparent",
                "repo": "o/r",
                "identity": "github:o/r",
                "base_branch": "main",
                "pr": "https://example.test/1",
                "pr_base": "main",
            }
        ],
        "outcome": "pr-open",
        "detail": "published",
        "publication_identity": "github:o/r",
        "publication_workflow": "remote",
        "repository_type": "team",
        "merge_policy": "none",
        "synthetic_stack_base": "feature/stack",
        "waiting_steps": ["approval"],
        "error": None,
    }
    replayed = _replay_node_run(graph.tasks[0], parent_item)
    seen_bases = []

    def lifecycle(node, **_):
        seen_bases.extend(node.stack_bases)
        return _lifecycle(branch="feature/child")

    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lifecycle,
        replayed_runs={"parent": replayed},
        replayed_order=["parent"],
    )

    assert result.results["parent"].recorded == parent_item
    assert result.started_order == ["parent", "child"]
    assert [anchor.branch for anchor in seen_bases] == ["feature/parent"]


def test_lifecycle_expected_no_diff_terminal_payload_and_direct_replay() -> None:
    graph = parse_graph(
        {
            "schema_version": 2,
            "tasks": [
                {
                    "id": "repo",
                    "repo": "o/r",
                    "task": "Inspect",
                    "steps": [
                        {
                            "id": "inspect",
                            "task": "Confirm no change",
                            "expects_no_diff": True,
                        }
                    ],
                }
            ],
        }
    )
    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(outcome="no-changes"),
    )
    assert result.results["repo"].status == "done"

    direct = parse_graph(
        {
            "schema_version": 2,
            "tasks": [{"id": "empty", "task": "No change", "expects_no_diff": True}],
        }
    ).tasks[0]
    replayed = _replay_node_run(
        direct,
        {
            "kind": "agent",
            "status": "done",
            "task": "No change",
            "outcome": "no-changes",
            "completed": True,
        },
    )
    assert replayed.payload == "no-changes"


def test_main_replays_settled_v2_prefix_and_converges_without_dispatch(
    tmp_path: Path, capsys
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tasks": [{"id": "done", "task": "No change", "expects_no_diff": True}],
            }
        )
    )
    runs = tmp_path / "runs"
    args = [str(plan), "--run", "recover-v2", "--runs-dir", str(runs), "--format", "json"]
    assert main(args) == 0
    round_dir = runs / "recover-v2" / "round-01"
    original = json.loads((round_dir / "result.json").read_text())
    events_path = runs / "recover-v2" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    prefix = [event for event in events if event["kind"] != "round-finished"]
    events_path.write_text("".join(json.dumps(event) + "\n" for event in prefix))
    (round_dir / "result.json").unlink()
    (round_dir / "status.json").write_text(
        json.dumps({"status": "running", "pid": 999_999_999, "host": socket.gethostname()})
    )

    assert main([*args, "--recover"]) == 0
    assert json.loads((round_dir / "result.json").read_text()) == original
    recovered_events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert sum(event["kind"] == "node-settled" for event in recovered_events) == 1
    assert "complete" in capsys.readouterr().out

    legacy_args = [
        str(plan),
        "--run",
        "recover-v1",
        "--runs-dir",
        str(runs),
        "--format",
        "json",
    ]
    assert main(legacy_args) == 0
    legacy_round = runs / "recover-v1" / "round-01"
    legacy_events_path = runs / "recover-v1" / "events.jsonl"
    legacy_events = [json.loads(line) for line in legacy_events_path.read_text().splitlines()]
    legacy_prefix = []
    for event in legacy_events:
        if event["kind"] == "round-finished":
            continue
        event["version"] = 1
        if event["kind"] == "node-settled":
            event["detail"].pop("result")
        legacy_prefix.append(event)
    legacy_events_path.write_text("".join(json.dumps(event) + "\n" for event in legacy_prefix))
    (legacy_round / "result.json").unlink()
    (legacy_round / "status.json").write_text(
        json.dumps({"status": "running", "pid": 999_999_999, "host": socket.gethostname()})
    )
    assert main([*legacy_args, "--recover"]) == 2
    assert "legacy settled nodes without terminal payloads: done" in capsys.readouterr().err


def test_run_graph_journals_a_direct_node_whose_runner_raised(tmp_path: Path) -> None:
    """A runner that raises is a node failure, and the journal must say so.

    `schedule_dag` catches whatever a runner raises and settles the node as failed,
    so the ledger records it. If the journal records no matching transition, the
    round leaves a `node-started` with nothing to close it — which is exactly the
    reading a started-with-no-settled event is reserved for: work still in flight.
    """
    journal = open_journal(tmp_path / "run-x", RunId("run-x"), 1)
    graph = parse_graph({"tasks": [{"id": "api", "persona": "engineer", "task": "A"}]})

    def boom(node: PlanNode, **_: object) -> Report:
        raise DispatchError("onejudge could not start")

    result = run_graph(
        graph,
        agent_runner=boom,
        lifecycle_runner=lambda node, **_: _lifecycle(),
        journal=journal,
        run_id=RunId("run-x"),
        round_number=1,
    )

    assert result.results["api"].status == "failed"
    events = journal.events()
    assert [(e.kind, e.node) for e in events] == [("node-started", "api"), ("node-failed", "api")]
    assert "onejudge could not start" in str(events[1].detail["detail"])


def test_run_graph_journals_a_lifecycle_node_whose_runner_raised(tmp_path: Path) -> None:
    """The same holds for a lifecycle node: a raised error still settles the journal."""
    journal = open_journal(tmp_path / "run-y", RunId("run-y"), 1)
    graph = parse_graph(
        {"tasks": [{"id": "api", "repo": "o/r", "persona": "engineer", "task": "A"}]}
    )

    def boom(node: RepoPlanNode, *, journal: NodeSink | None = None) -> LifecycleResult:
        raise GitError("origin rejected the push")

    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=boom,
        journal=journal,
        run_id=RunId("run-y"),
        round_number=1,
    )

    assert result.results["api"].status == "failed"
    assert [(e.kind, e.node) for e in journal.events()] == [
        ("node-started", "api"),
        ("node-failed", "api"),
    ]


def test_run_graph_labels_each_dispatch_with_its_place_in_the_graph(tmp_path: Path) -> None:
    """A direct node's dispatch is labelled with the round and node it belongs to.

    These labels are the only thing tying a recorded oneharness session back to the
    node that produced it, so they are asserted at the seam the real dispatch is
    called through rather than off the journal, which a subprocess never sees.
    """
    journal = open_journal(tmp_path / "run-l", RunId("run-l"), 7)
    graph = parse_graph(
        {
            "tasks": [
                {"id": "api", "persona": "engineer", "task": "A"},
                {"id": "web", "persona": "engineer", "task": "B"},
            ]
        }
    )
    seen: dict[str, dict[str, str]] = {}

    def agent(node: PlanNode, *, labels: Mapping[str, str] | None = None) -> Report:
        seen[node.id] = dict(labels or {})
        return _report(node.persona)

    run_graph(
        graph,
        agent_runner=agent,
        lifecycle_runner=lambda node, **_: _lifecycle(),
        journal=journal,
        run_id=RunId("run-l"),
        round_number=7,
    )

    assert seen["api"] == {"run_id": "run-l", "round": "7", "node": "api"}
    assert seen["web"] == {"run_id": "run-l", "round": "7", "node": "web"}


def test_run_graph_scopes_each_lifecycle_to_its_own_node() -> None:
    """A lifecycle is handed a scope it cannot use to speak for a sibling node."""
    graph = parse_graph(
        {
            "tasks": [
                {"id": "api", "repo": "o/r", "persona": "engineer", "task": "A"},
                {"id": "web", "repo": "o/r", "persona": "engineer", "task": "B"},
            ]
        }
    )
    scopes: dict[str, NodeSink] = {}

    def lifecycle(node: RepoPlanNode, *, journal: NodeSink | None = None) -> LifecycleResult:
        assert journal is not None
        scopes[node.id] = journal
        return _lifecycle()

    run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lifecycle,
    )

    assert scopes["api"].labels == {"node": "api"}
    with pytest.raises(JournalError, match="cannot append an event for node 'web'"):
        scopes["api"].append("branch-discovered", node=NodeId("web"))


def test_run_graph_without_a_journal_is_unchanged(tmp_path: Path) -> None:
    graph = parse_graph({"tasks": [{"id": "a", "persona": "p", "task": "t"}]})
    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report("p"),
        lifecycle_runner=lambda node, **_: _lifecycle(),
    )
    assert result.state == "complete"


def test_parse_graph_accepts_old_direct_and_lifecycle_nodes() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {"id": "direct", "persona": "planner", "task": "Plan"},
                {
                    "id": "repo",
                    "repo": "o/r",
                    "persona": "engineer",
                    "task": "Patch",
                    "deps": ["direct"],
                },
            ]
        }
    )

    assert graph.tasks[0].direct is not None
    assert graph.tasks[1].lifecycle is not None
    assert graph.tasks[1].deps == ["direct"]


def test_expects_no_diff_rejects_agent_review_contract_before_dispatch() -> None:
    with pytest.raises(
        PlanError,
        match="expects_no_diff cannot set 'persona', 'done_when'.*no agent or review evidence",
    ):
        parse_graph(
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "certify",
                        "expects_no_diff": True,
                        "persona": "reviewer",
                        "task": "Certify the unchanged tree.",
                        "done_when": "report verified review findings and implement required fixes",
                    }
                ],
            }
        )


def test_expects_no_diff_step_settles_without_dispatch() -> None:
    graph = parse_graph(
        {
            "schema_version": 2,
            "tasks": [
                {
                    "id": "work",
                    "repo": "o/r",
                    "steps": [
                        {
                            "id": "ready",
                            "task": "Record that no repository change is expected.",
                            "expects_no_diff": True,
                        }
                    ],
                }
            ],
        }
    )

    lifecycle = graph.tasks[0].lifecycle
    assert lifecycle is not None
    assert lifecycle.steps is not None
    step = lifecycle.steps[0]
    assert step.expects_no_diff is True
    assert step.persona is None


def test_expects_no_diff_direct_node_records_no_changes_without_runner() -> None:
    graph = parse_graph(
        {
            "schema_version": 2,
            "tasks": [
                {
                    "id": "ready",
                    "task": "Certify the deterministic unchanged state.",
                    "expects_no_diff": True,
                }
            ],
        }
    )

    def unexpected_dispatch(*args: object, **kwargs: object) -> Report:
        raise AssertionError("expects_no_diff must not dispatch")

    result = run_graph(
        graph,
        agent_runner=unexpected_dispatch,
        lifecycle_runner=lambda node, **_: _lifecycle(),
    )

    assert graph_payload(result)["results"]["ready"]["outcome"] == "no-changes"
    assert result.results["ready"].status == "done"


@pytest.mark.reads_docs
def test_plan_schema_version_documentation_cannot_drift() -> None:
    root = Path(__file__).parents[1]
    docs = (root / "docs" / "orchestration.md").read_text(encoding="utf-8")
    assert f"schema version {PLAN_SCHEMA_VERSION}" in docs
    for example in ("plan.example.json", "repo-plan.example.json", "tracked-graph.example.json"):
        mapping = json.loads((root / "examples" / example).read_text(encoding="utf-8"))
        assert mapping["schema_version"] == PLAN_SCHEMA_VERSION


def test_cross_dag_dependency_resolves_done_and_surfaces_later_journal_advance(
    tmp_path: Path,
) -> None:
    upstream_dir = tmp_path / "runs" / "upstream"
    upstream = open_journal(upstream_dir, RunId("upstream"), 1)
    upstream.append("node-started", node=NodeId("produce"))
    upstream.append("node-settled", node=NodeId("produce"), detail={"status": "done"})
    register_run(
        run_id="upstream",
        run_dir=upstream_dir,
        goal=None,
        identities=[],
        pid=os.getpid(),
        acknowledge_concurrent=False,
    )
    downstream = open_journal(tmp_path / "runs" / "downstream", RunId("downstream"), 1)
    graph = parse_graph(
        {
            "schema_version": 5,
            "tasks": [
                {
                    "id": "consume",
                    "persona": "engineer",
                    "task": "Consume it",
                    "deps": ["run:upstream#produce"],
                }
            ],
        }
    )

    def consume(node: PlanNode, **_: object) -> Report:
        upstream.append("setup-finished", node=NodeId("produce"), detail={"changed": True})
        return _report(node.persona)

    result = run_graph(
        graph,
        agent_runner=consume,
        lifecycle_runner=lambda node, **_: _lifecycle(),
        journal=downstream,
        run_id=RunId("downstream"),
        round_number=1,
    )

    assert result.results["consume"].status == "done"
    satisfied = [event for event in downstream.events() if event.kind == "cross-dag-satisfied"]
    assert len(satisfied) == 1
    assert satisfied[0].detail["last_seq"] == 2
    signals = [event for event in downstream.events() if event.kind == "upstream-modified"]
    assert len(signals) == 1
    assert signals[0].node == "consume"
    assert signals[0].detail["dependency"] == "run:upstream#produce"


def test_cross_dag_unknown_or_failed_upstream_blocks_without_dispatch(tmp_path: Path) -> None:
    graph = parse_graph(
        {
            "schema_version": 5,
            "tasks": [
                {
                    "id": "consume",
                    "persona": "engineer",
                    "task": "Consume it",
                    "deps": ["run:unknown#produce"],
                }
            ],
        }
    )
    result = run_graph(
        graph,
        agent_runner=lambda node, **_: pytest.fail("blocked dependency dispatched"),
        lifecycle_runner=lambda node, **_: _lifecycle(),
    )
    assert result.results["consume"].status == "blocked"


def test_cross_dag_observer_replays_satisfaction_baseline_after_restart(tmp_path: Path) -> None:
    upstream_dir = tmp_path / "runs" / "upstream"
    upstream = open_journal(upstream_dir, RunId("upstream"), 1)
    upstream.append("node-started", node=NodeId("produce"))
    upstream.append("node-settled", node=NodeId("produce"), detail={"status": "done"})
    register_run(
        run_id="upstream",
        run_dir=upstream_dir,
        goal=None,
        identities=[],
        pid=os.getpid(),
        acknowledge_concurrent=False,
    )
    downstream = open_journal(tmp_path / "runs" / "downstream", RunId("downstream"), 1)
    graph = parse_graph(
        {
            "schema_version": 5,
            "tasks": [
                {
                    "id": "consume",
                    "task": "Record satisfaction.",
                    "expects_no_diff": True,
                    "deps": ["run:upstream#produce"],
                }
            ],
        }
    )
    run_graph(
        graph,
        agent_runner=lambda node, **_: pytest.fail("expects_no_diff dispatched"),
        lifecycle_runner=lambda node, **_: _lifecycle(),
        journal=downstream,
    )
    upstream.append("setup-finished", node=NodeId("produce"), detail={"changed": True})

    run_graph(
        graph,
        agent_runner=lambda node, **_: pytest.fail("expects_no_diff dispatched"),
        lifecycle_runner=lambda node, **_: _lifecycle(),
        journal=downstream,
    )

    assert [event.kind for event in downstream.events()].count("cross-dag-satisfied") == 1
    assert [event.kind for event in downstream.events()].count("upstream-modified") == 1


def test_cross_dag_dependency_requires_current_plan_schema() -> None:
    with pytest.raises(PlanError, match="require schema_version 5"):
        parse_graph(
            {
                "schema_version": 4,
                "tasks": [
                    {
                        "id": "consume",
                        "persona": "engineer",
                        "task": "Consume it",
                        "deps": ["run:upstream#produce"],
                    }
                ],
            }
        )


def test_goal_is_normalized_and_legacy_goal_less_plans_still_load() -> None:
    graph = parse_graph(
        {
            "schema_version": 4,
            "goal": {"text": "  Ship the safe release  "},
            "tasks": [{"id": "x", "persona": "p", "task": "x"}],
        }
    )
    legacy = parse_graph({"tasks": [{"id": "x", "persona": "p", "task": "x"}]})

    assert graph.goal == {"id": "Ship-the-safe-release", "text": "Ship the safe release"}
    assert legacy.goal is None


@pytest.mark.parametrize(
    ("goal", "message"),
    [
        ({"text": ""}, "goal.text"),
        ({"id": "", "text": "ship"}, "goal.id"),
        ({"text": "ship", "extra": True}, "unknown field"),
        ("ship", "must be a mapping"),
    ],
)
def test_goal_validation_rejects_malformed_contract(goal: object, message: str) -> None:
    with pytest.raises(PlanError, match=message):
        parse_graph(
            {
                "schema_version": 4,
                "goal": goal,
                "tasks": [{"id": "x", "persona": "p", "task": "x"}],
            }
        )

    with pytest.raises(PlanError, match="requires schema_version 4"):
        parse_graph(
            {
                "schema_version": 3,
                "goal": {"text": "ship"},
                "tasks": [{"id": "x", "persona": "p", "task": "x"}],
            }
        )


def test_recorded_result_schema_v5_field_golden_cannot_drift() -> None:
    golden = json.loads(
        (Path(__file__).parent / "golden" / "recorded-result-v5-fields.json").read_text(
            encoding="utf-8"
        )
    )

    def _split(payload: type) -> tuple[list[str], list[str]]:
        hints = get_type_hints(payload, include_extras=True)
        optional = sorted(
            key for key, annotation in hints.items() if get_origin(annotation) is NotRequired
        )
        return sorted(set(hints) - set(optional)), optional

    step_required, step_optional = _split(StepResultPayload)
    # `resume` crosses rounds rather than only being read back once, so a field
    # added to it silently is a field an older ledger will not carry — the drift
    # this golden exists to make loud.
    resume_required, resume_optional = _split(ResumePayload)
    assert golden == {
        "schema_version": RECORDED_RESULT_SCHEMA_VERSION,
        "artifact_paths": sorted(ArtifactPaths.__optional_keys__),
        "graph_result_item_optional": sorted(GraphResultItem.__optional_keys__),
        "step_result_required": step_required,
        "step_result_optional": step_optional,
        "resume_required": resume_required,
        "resume_optional": resume_optional,
    }


def test_recorded_artifacts_round_trip_and_empty_fields_are_omitted() -> None:
    populated = LifecycleResult(
        "o/r",
        "task",
        "engineer",
        "main",
        "branch",
        "merged",
        report=_report("engineer"),
        verify=VerifyResult(
            True,
            ["just", "gate"],
            "green\n",
            log_path="/runs/demo/round-01/work/gate.log",
        ),
        steps=[StepResult("work", "engineer", "done", report=_report("engineer"))],
    )
    populated.steps[0].report.artifacts.update(
        {
            "worker_report": "/runs/demo/round-01/work/worker-report.json",
            "oneharness_session": "/runs/demo/round-01/work/oneharness-session.json",
        }
    )
    serialized = json.loads(json.dumps(result_payload(populated)))

    assert serialized["artifacts"] == {
        "gate_log": "/runs/demo/round-01/work/gate.log",
        **populated.steps[0].report.artifacts,
    }
    assert serialized["steps"][0]["artifacts"] == populated.steps[0].report.artifacts

    empty = LifecycleResult(
        "o/r",
        "task",
        "engineer",
        "main",
        "branch",
        "merged",
        report=_report("engineer"),
        steps=[StepResult("work", "engineer", "done", report=_report("engineer"))],
    )
    empty_payload = result_payload(empty)

    assert "artifacts" not in empty_payload
    assert "artifacts" not in empty_payload["steps"][0]


def test_replay_rejects_invalid_deferred_cleanup() -> None:
    node = GraphNode(
        id="work",
        task="task",
        lifecycle=RepoPlanNode("work", "o/r", "engineer", "task"),
    )
    item = cast(GraphResultItem, {"status": "done", "deferred_cleanup": "not-a-list"})

    with pytest.raises(ConfigError, match="invalid deferred_cleanup"):
        _replay_node_run(node, item)


@pytest.mark.parametrize("invalid_outcome", ["unknown", ["merged"]])
def test_replay_rejects_invalid_lifecycle_outcome(invalid_outcome: object) -> None:
    node = parse_graph(
        {
            "tasks": [
                {
                    "id": "work",
                    "repo": "o/r",
                    "persona": "engineer",
                    "task": "Work",
                }
            ]
        }
    ).tasks[0]
    item = cast(GraphResultItem, {"status": "done", "outcome": invalid_outcome})

    with pytest.raises(ConfigError, match="invalid outcome"):
        _replay_node_run(node, item)


def test_deferred_cleanup_round_trips_through_recorded_result() -> None:
    node = GraphNode(
        id="work",
        task="task",
        lifecycle=RepoPlanNode("work", "o/r", "engineer", "task"),
    )
    lifecycle = LifecycleResult(
        "o/r",
        "task",
        "engineer",
        "main",
        "branch",
        "merged",
        deferred_cleanup=["remove-worktree deferred for /tmp/worktree: busy"],
    )
    item = cast(GraphResultItem, {"status": "done", **result_payload(lifecycle)})

    replayed = _replay_node_run(node, item)

    assert isinstance(replayed.payload, LifecycleResult)
    assert replayed.payload.deferred_cleanup == lifecycle.deferred_cleanup


def test_schema_v2_remains_compatible_but_verify_via_ci_requires_v3() -> None:
    legacy_v2 = {
        "schema_version": 2,
        "tasks": [
            {
                "id": "work",
                "repo": "o/r",
                "persona": "engineer",
                "task": "Patch",
            }
        ],
    }
    assert parse_graph(legacy_v2).tasks[0].lifecycle is not None

    legacy_v2["tasks"][0]["verify_via_ci"] = True
    with pytest.raises(PlanError, match="verify_via_ci.*requires schema_version 3"):
        parse_graph(legacy_v2)

    legacy_v2["schema_version"] = 3
    lifecycle = parse_graph(legacy_v2).tasks[0].lifecycle
    assert lifecycle is not None and lifecycle.verify_via_ci is True


@pytest.mark.parametrize("field, value", [("persona", "p"), ("persona", None), ("repo", None)])
def test_human_node_validation_is_strict(field, value) -> None:
    with pytest.raises(PlanError, match=f"cannot set '{field}'"):
        parse_graph({"tasks": [{"id": "review", "kind": "human", "task": "Review", field: value}]})


@pytest.mark.parametrize(
    "plan, match",
    [
        ({"tasks": []}, "non-empty 'tasks'"),
        (
            {"concurrency": 0, "tasks": [{"id": "a", "persona": "p", "task": "t"}]},
            "concurrency",
        ),
        ({"tasks": ["x"]}, "must be a mapping"),
        ({"tasks": [{"persona": "p", "task": "t"}]}, "non-empty string 'id'"),
        (
            {
                "tasks": [
                    {"id": "a", "persona": "p", "task": "t"},
                    {"id": "a", "persona": "p", "task": "t"},
                ]
            },
            "duplicate",
        ),
        ({"tasks": [{"id": "a", "kind": "robot", "task": "t"}]}, "kind"),
        ({"tasks": [{"id": "a", "kind": "human", "task": ""}]}, "non-empty 'task'"),
        (
            {"tasks": [{"id": "release/approval", "kind": "human", "task": "Approve"}]},
            "reserved for NODE_ID/STEP_ID",
        ),
        (
            {"tasks": [{"id": "a", "kind": "human", "task": "t", "deps": "b"}]},
            "deps",
        ),
        (
            {"tasks": [{"id": "a", "kind": "human", "task": "t", "deps": ["missing"]}]},
            "unknown task",
        ),
        (
            {"tasks": [{"id": "a", "kind": "human", "task": "t", "deps": ["a"]}]},
            "depends on itself",
        ),
    ],
)
def test_parse_graph_rejects_bad_inputs(plan, match) -> None:
    with pytest.raises(PlanError, match=match):
        parse_graph(plan)


def test_top_level_human_waits_and_blocks_transitively() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {"id": "review", "kind": "human", "task": "Approve the change"},
                {"id": "ship", "persona": "engineer", "task": "Ship", "deps": ["review"]},
                {"id": "announce", "persona": "docs-writer", "task": "Announce", "deps": ["ship"]},
            ]
        }
    )

    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
    )
    payload = graph_payload(result)

    assert result.state == "waiting"
    assert result.results["review"].status == "waiting"
    assert result.results["ship"].status == "blocked"
    assert result.results["announce"].blocked_by == ["review"]
    assert payload["results"]["review"]["human_actions"][0]["unblocks"] == ["ship"]
    summary = result.summary()
    assert "Awaiting 1 human action" in summary
    assert "blocked by: review" in summary


def test_failure_precedence_over_waiting_dependency() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {"id": "bad", "persona": "engineer", "task": "Fail"},
                {"id": "review", "kind": "human", "task": "Review"},
                {
                    "id": "after",
                    "persona": "engineer",
                    "task": "After",
                    "deps": ["bad", "review"],
                },
            ]
        }
    )

    def agent(node: PlanNode, **_: object) -> Report:
        return _report(node.persona, completed=node.id != "bad")

    result = run_graph(graph, agent_runner=agent, lifecycle_runner=lambda node, **_: _lifecycle())

    assert result.state == "failed"
    assert result.results["review"].status == "waiting"
    assert result.results["after"].status == "skipped"
    assert result.results["after"].blocked_by == []


def test_lifecycle_failure_sets_failed_state() -> None:
    graph = parse_graph(
        {"tasks": [{"id": "work", "repo": "o/r", "persona": "engineer", "task": "Patch"}]}
    )

    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle("gate-failed", detail="gate failed"),
    )

    assert result.state == "failed"
    assert result.results["work"].status == "failed"
    assert result.results["work"].error == "gate failed"


def test_lifecycle_human_step_waiting_action_uses_nested_ref() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {
                    "id": "work",
                    "repo": "o/r",
                    "steps": [
                        {"id": "agent", "persona": "engineer", "task": "Prepare"},
                        {"id": "approve", "kind": "human", "task": "Approve", "deps": ["agent"]},
                    ],
                },
                {"id": "after", "persona": "engineer", "task": "After", "deps": ["work"]},
            ]
        }
    )

    def lifecycle(node, **_: object) -> LifecycleResult:
        return _lifecycle(
            "waiting-human",
            detail="paused",
            steps=[
                StepResult("agent", "engineer", "done", "agent", _report("engineer")),
                StepResult("approve", None, "waiting", "human", None),
            ],
            waiting_steps=["approve"],
        )

    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lifecycle,
    )
    action = graph_payload(result)["results"]["work"]["human_actions"][0]

    assert action["ref"] == "work/approve"
    assert action["unblocks_publication"]
    assert result.results["after"].blocked_by == ["work/approve"]


def test_lifecycle_human_step_waiting_action_with_internal_downstream() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {
                    "id": "work",
                    "repo": "o/r",
                    "steps": [
                        {"id": "approve", "kind": "human", "task": "Approve"},
                        {
                            "id": "finish",
                            "persona": "engineer",
                            "task": "Finish",
                            "deps": ["approve"],
                        },
                    ],
                }
            ]
        }
    )

    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(
            "waiting-human",
            detail="paused",
            steps=[StepResult("approve", None, "waiting", "human", None)],
            waiting_steps=["approve"],
        ),
    )
    action = graph_payload(result)["results"]["work"]["human_actions"][0]
    assert action["unblocks"] == ["work/finish"]
    assert not action["unblocks_publication"]


def test_parse_graph_accepts_human_steps_and_rejects_agent_fields() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {
                    "id": "work",
                    "repo": "o/r",
                    "steps": [{"id": "approve", "kind": "human", "task": "Approve"}],
                }
            ]
        }
    )

    assert graph.tasks[0].lifecycle is not None
    assert graph.tasks[0].lifecycle.steps == [Step("approve", None, "Approve", "human")]
    with pytest.raises(PlanError, match="reserved for NODE_ID/STEP_ID"):
        parse_graph(
            {
                "tasks": [
                    {
                        "id": "work",
                        "repo": "o/r",
                        "steps": [
                            {
                                "id": "security/approval",
                                "kind": "human",
                                "task": "Approve",
                            }
                        ],
                    }
                ]
            }
        )
    with pytest.raises(PlanError, match="human step 'approve' cannot set 'persona'"):
        parse_graph(
            {
                "tasks": [
                    {
                        "id": "work",
                        "repo": "o/r",
                        "steps": [
                            {
                                "id": "approve",
                                "kind": "human",
                                "task": "Approve",
                                "persona": "engineer",
                            }
                        ],
                    }
                ]
            }
        )
    with pytest.raises(PlanError, match="human step 'approve' cannot set 'persona'"):
        parse_graph(
            {
                "tasks": [
                    {
                        "id": "work",
                        "repo": "o/r",
                        "steps": [
                            {
                                "id": "approve",
                                "kind": "human",
                                "task": "Approve",
                                "persona": None,
                            }
                        ],
                    }
                ]
            }
        )


def test_graph_payload_is_json_serializable() -> None:
    graph = parse_graph({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]})
    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
    )

    json.dumps(graph_payload(result))


def test_run_graph_direct_success_and_lifecycle_payload() -> None:
    direct = parse_graph({"tasks": [{"id": "a", "persona": "engineer", "task": "A"}]})
    direct_result = run_graph(
        direct,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
    )
    assert direct_result.ok
    assert graph_payload(direct_result)["results"]["a"]["completed"] is True

    lifecycle = parse_graph(
        {"tasks": [{"id": "life", "repo": "o/r", "persona": "engineer", "task": "L"}]}
    )
    lifecycle_result = run_graph(
        lifecycle,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle("pr-open", detail="opened"),
    )
    payload = graph_payload(lifecycle_result)
    assert payload["results"]["life"]["outcome"] == "pr-open"


def test_render_action_helpers_cover_downstream_variants() -> None:
    assert HumanAction("a", "Task", ("b",)).downstream() == "b"
    assert HumanAction("a", "Task", (), True).downstream() == "workstream publication"
    assert HumanAction("a", "Task").downstream() == "nothing downstream"
    assert render_actions([]) == []
    long = "x" * 100
    assert first_line(long).endswith("...")


def test_run_plan_cli_records_by_default_for_human_plan(tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )

    rc = main(
        [
            str(plan),
            "--run",
            "demo",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--format",
            "json",
        ]
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "waiting"
    recorded = json.loads((tmp_path / "runs" / "demo" / "round-01" / "result.json").read_text())
    assert recorded["results"]["review"]["human_actions"][0]["ref"] == "review"


def test_load_graph_wraps_config_errors(tmp_path) -> None:
    with pytest.raises(PlanError):
        load_graph(tmp_path / "missing.json")


def test_load_graph_success(tmp_path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )
    assert load_graph(plan).tasks[0].id == "review"


def test_run_plan_cli_invalid_input_exits_2(tmp_path, capsys) -> None:
    plan = tmp_path / "bad.json"
    plan.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    assert main([str(plan)]) == 2
    assert "run-plan:" in capsys.readouterr().err


def test_run_plan_cli_invalid_concurrency_override_exits_2(tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )

    assert main([str(plan), "--concurrency", "0", "--no-record"]) == 2
    assert "positive integer" in capsys.readouterr().err


def test_run_plan_cli_rejects_invalid_round_budget(tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "tasks": [{"id": "a", "task": "none", "expects_no_diff": True}],
            }
        ),
        encoding="utf-8",
    )

    assert main([str(plan), "--no-record", "--round-budget", "nan"]) == 2
    assert "--round-budget" in capsys.readouterr().err


def test_run_plan_cli_reports_pending_round_claim_failure(tmp_path, capsys) -> None:
    from orchestrator.runs import prepare_round

    plan = tmp_path / "plan.json"
    payload = {"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}
    plan.write_text(json.dumps(payload), encoding="utf-8")
    prepare_round(tmp_path / "runs" / "demo", payload)

    assert main([str(plan), "--run", "demo", "--runs-dir", str(tmp_path / "runs")]) == 2
    assert "could not claim run" in capsys.readouterr().err


def test_run_plan_cli_reports_recording_failure(monkeypatch, tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )

    def fail_write(round_dir, payload):
        raise ConfigError("ledger full")

    from orchestrator.config import ConfigError

    monkeypatch.setattr("orchestrator.graph.write_result", fail_write)
    assert main([str(plan), "--run", "demo", "--runs-dir", str(tmp_path / "runs")]) == 2
    assert "could not record run" in capsys.readouterr().err


def test_run_plan_cli_no_record_does_not_create_runs(tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )
    runs = tmp_path / "runs"

    assert main([str(plan), "--runs-dir", str(runs), "--no-record", "--format", "json"]) == 1
    assert not runs.exists()
    assert json.loads(capsys.readouterr().out)["state"] == "waiting"


def test_print_continuation_complete_and_failed_without_humans(tmp_path, capsys) -> None:
    complete = {"ok": True, "state": "complete", "started_order": [], "results": {}}
    print_continuation("demo", 1, tmp_path, complete, Path("runs"))
    assert "nothing to iterate" in capsys.readouterr().err

    failed = {
        "ok": False,
        "state": "failed",
        "started_order": ["a"],
        "results": {"a": {"status": "failed"}},
    }
    print_continuation("demo", 1, tmp_path, failed, tmp_path / "runs")
    assert "edits.json" in capsys.readouterr().err


def test_repo_plan_alias_warns(monkeypatch, capsys) -> None:
    monkeypatch.setattr("orchestrator.graph.main", lambda argv=None: 0)
    assert main_repo_plan(["plan.json"]) == 0
    assert "deprecated" in capsys.readouterr().err


@pytest.mark.parametrize(
    "detail",
    [
        "onejudge failed (exit 2 — bad config or provider/runtime error): "
        "provider error (respond): connection closed",
        "onejudge failed (exit 2): provider error (supervisor): Broken pipe",
        "oneharness exited with signal: 9 (SIGKILL)",
        "harness failed (auth): login required",
        "harness claude-code cannot write v0.3 history telemetry",
        "harness codex cannot write v1.0 history telemetry",
        "could not write history: new history record lacks complete v0.3 telemetry",
        "could not write history: new history run lacks complete v1.0 telemetry",
        "new history run lacks complete v12.34.5 telemetry",
        "[Errno 28] No space left on device",
        "worker was OOMKilled",
    ],
)
def test_infrastructure_failure_classifier_recognizes_no_dispatch_errors(detail: str) -> None:
    assert infrastructure_failure_detail(RuntimeError(detail)) == detail


@pytest.mark.parametrize(
    "detail",
    [
        "onejudge failed (exit 2 — bad config or provider/runtime error): bad config",
        "unexpected runtime failure",
        "agent failed its requested task",
    ],
)
def test_infrastructure_failure_classifier_keeps_ambiguous_errors_retryable(
    detail: str,
) -> None:
    assert infrastructure_failure_detail(RuntimeError(detail)) is None


def test_infrastructure_failure_classifier_uses_capacity_exception_contract() -> None:
    from orchestrator.scratch import CAPACITY_ERROR_MARKER, ScratchCapacityError

    detail = "capacity threshold reached"
    assert infrastructure_failure_detail(ScratchCapacityError(detail)) == detail
    marked = f"{CAPACITY_ERROR_MARKER} {detail}"
    assert infrastructure_failure_detail(RuntimeError(marked)) == marked


def test_recorded_round_translates_scratch_sweep_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": PLAN_SCHEMA_VERSION,
                "name": "sweep-failure",
                "tasks": [
                    {
                        "id": "no-diff",
                        "task": "No dispatch.",
                        "expects_no_diff": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "orchestrator.graph.sweep_scratch",
        lambda: (_ for _ in ()).throw(PermissionError("denied")),
    )

    assert main([str(plan), "--run", "demo", "--runs-dir", str(tmp_path / "runs")]) == 2
    error = capsys.readouterr().err
    assert "scratch sweep failed before claiming the round" in error
    assert "just sweep-scratch --dry-run" in error


def test_planner_context_reaches_every_dispatch_the_node_makes() -> None:
    """One node-level note, rendered into each task the node hands to a worker."""
    graph = parse_graph(
        {
            "schema_version": PLAN_SCHEMA_VERSION,
            "tasks": [
                {
                    "id": "direct",
                    "persona": "engineer",
                    "task": "## What\nSweep the harness",
                    "context": ["41 commits are on the branch", "one llmlint finding is open"],
                },
                {
                    "id": "workstream",
                    "repo": "o/r",
                    "context": ["the gate is green"],
                    "steps": [
                        {"id": "fix", "persona": "engineer", "task": "Fix"},
                        {"id": "approve", "kind": "human", "task": "Approve", "deps": ["fix"]},
                    ],
                },
                {
                    "id": "single",
                    "repo": "o/r",
                    "persona": "engineer",
                    "task": "Publish",
                    "context": ["the branch is already pushed"],
                },
            ],
        }
    )
    direct = graph.tasks[0].direct
    assert direct is not None
    assert direct.task.startswith("## What\nSweep the harness\n\n## Planner context")
    assert "41 commits are on the branch\n\none llmlint finding is open" in direct.task
    # The node keeps the notes as data, so the next render composes from them
    # rather than from prose that already contains the section.
    assert graph.tasks[0].definition["context"] == [
        "41 commits are on the branch",
        "one llmlint finding is open",
    ]

    workstream = graph.tasks[1].lifecycle
    assert workstream is not None and workstream.steps is not None
    agent_step, human_step = workstream.steps
    assert "## Planner context" in agent_step.task and "the gate is green" in agent_step.task
    # A human step is prose for a person, left exactly as the planner wrote it.
    assert human_step.task == "Approve"

    single = graph.tasks[2].lifecycle
    assert single is not None and single.task is not None
    assert single.task.startswith("Publish\n\n## Planner context")


def test_planner_context_is_validated_and_refused_where_no_worker_reads_it() -> None:
    with pytest.raises(PlanError, match="'context' must be a list of non-empty planner notes"):
        parse_graph({"tasks": [{"id": "a", "persona": "p", "task": "t", "context": "a note"}]})
    with pytest.raises(PlanError, match="'context' must be a list of non-empty planner notes"):
        parse_graph({"tasks": [{"id": "a", "persona": "p", "task": "t", "context": [" "]}]})
    with pytest.raises(PlanError, match="cannot set 'context'"):
        parse_graph({"tasks": [{"id": "h", "kind": "human", "task": "Approve", "context": ["n"]}]})
    # An omitted list changes nothing about the task a worker receives.
    unchanged = parse_graph({"tasks": [{"id": "a", "persona": "p", "task": "t"}]}).tasks[0]
    assert unchanged.direct is not None and unchanged.direct.task == "t"
