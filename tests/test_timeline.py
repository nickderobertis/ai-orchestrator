"""The timeline fold, and the trust boundaries the served read model owns."""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from orchestrator.conversations import Attribution, DagConversation
from orchestrator.detail_snapshot import PrDetail
from orchestrator.journal import Detail, Event, EventKind, NodeId, RunId, StepId, open_journal
from orchestrator.monitor import DetailSnapshot, save_snapshot
from orchestrator.read_model import InvalidRunId, ProjectionFailed, RunNotFound
from orchestrator.runs import prepare_round
from orchestrator.supervisory import (
    DriverState,
    SupervisoryCapture,
    close_capture,
    open_capture,
    record_turn,
)
from orchestrator.timeline import (
    CONVERSATION_TURN_KIND,
    HISTORY_WRITE_FAILURE_KIND,
    TimelineSpan,
    assemble,
    run_timeline,
)

ABSENT = "definitely-not-a-real-oneharness-binary"
RUN = RunId("demo")
BASE = datetime(2026, 7, 19, tzinfo=UTC).timestamp()


def _event(
    seq: int,
    kind: EventKind,
    *,
    node: str | None = None,
    step: str | None = None,
    round_number: int = 1,
    detail: Detail | None = None,
    offset: float | None = None,
) -> Event:
    """One journal record, timestamped from its sequence unless told otherwise."""
    return Event(
        kind=kind,
        run_id=RUN,
        round=round_number,
        seq=seq,
        at=BASE + (seq if offset is None else offset),
        node=None if node is None else NodeId(node),
        step=None if step is None else StepId(step),
        detail=detail or {},
    )


def _conversation(
    session_id: str,
    *,
    started: str,
    turns: int = 1,
    state: str = "completed",
    transport_role: str = "agent",
    agent_role: str = "worker",
    node: str | None = None,
    step: str | None = None,
    round_number: int | None = None,
    finished: str | None = None,
) -> DagConversation:
    """One ``DagConversation`` shaped exactly as ``conversations.dag_conversation`` builds it."""
    attribution: Attribution = {
        "transportRole": transport_role,
        "agentRole": agent_role,
        "launcher": "unknown",
        "runId": str(RUN),
    }
    if node is not None:
        attribution["nodeId"] = node
    if step is not None:
        attribution["stepId"] = step
    if round_number is not None:
        attribution["round"] = round_number
    if finished is not None:
        attribution["finishedAt"] = finished
    return {
        "conversation": {
            "id": session_id,
            "name": f"{agent_role}-{session_id}",
            "project": "/tmp/project",
            "startedAt": started,
            "harnesses": ["codex"],
            "state": state,
            "canContinue": False,
            "turns": [
                {
                    "id": f"{session_id}-{index}",
                    "user": "go",
                    "assistant": "done",
                    "reasoning": None,
                    "harness": "codex",
                    "model": "gpt",
                    "timestamp": started,
                    "status": "completed",
                    "failureKind": None,
                    "usage": {},
                    "tools": [],
                    "unknown": {},
                }
                for index in range(turns)
            ],
        },
        "attribution": attribution,
    }


def _by_kind(spans: list[TimelineSpan], kind: str) -> list[TimelineSpan]:
    return [span for span in spans if span["kind"] == kind]


def _one(spans: list[TimelineSpan], kind: str) -> TimelineSpan:
    found = _by_kind(spans, kind)
    assert len(found) == 1, f"expected one {kind} span, got {[span['id'] for span in found]}"
    return found[0]


def _kinds(span: TimelineSpan) -> list[str]:
    return [event["kind"] for event in span["events"]]


def test_fold_brackets_recorded_work_and_leaves_a_running_node_open() -> None:
    """The in-flight case: everything the stream never closed reads as still open."""
    events = [
        _event(1, "node-added", detail={"definition": {"id": "api"}}),
        _event(2, "round-started"),
        _event(3, "node-started", node="api", detail={"node_kind": "lifecycle"}),
        _event(4, "step-started", node="api", step="impl", detail={"step_kind": "agent"}),
        _event(
            5,
            "branch-discovered",
            node="api",
            step="impl",
            detail={"branch": "feature/api", "repo": "acme/app"},
        ),
        _event(
            6,
            "step-settled",
            node="api",
            step="impl",
            detail={"status": "done", "turns": 4},
        ),
        _event(7, "verification-started", node="api", detail={"label": "branch push feature/api"}),
        _event(
            8,
            "verification-finished",
            node="api",
            detail={"label": "branch push", "ok": True, "log_path": "/runs/demo/gate.log"},
        ),
        _event(
            9,
            "pr-created",
            node="api",
            detail={"repo": "acme/app", "pr": "https://x/pull/7", "number": 7},
        ),
        _event(
            10, "pr-checks-observed", node="api", detail={"pr": "https://x/pull/7", "state": "OPEN"}
        ),
    ]

    spans = assemble(events, [], DetailSnapshot())

    round_span = _one(spans, "round")
    node_span = _one(spans, "node")
    step_span = _one(spans, "step")
    verification = _one(spans, "verification")
    publication = _one(spans, "publication")

    # The round opens at its first record, not at `round-started`, so nothing is orphaned.
    assert round_span["started_at"] == datetime.fromtimestamp(BASE + 1, UTC).isoformat()
    assert _kinds(round_span) == ["node-added"]
    # Neither the run nor the node has settled: both spans stay open.
    assert round_span["ended_at"] is None
    assert node_span["ended_at"] is None
    assert node_span["parent_id"] == round_span["id"]

    # A settled step closes with its recorded status; its own events nest inside it.
    assert step_span["parent_id"] == node_span["id"]
    assert step_span["ended_at"] == datetime.fromtimestamp(BASE + 6, UTC).isoformat()
    assert step_span["status"] == "done"
    assert _kinds(step_span) == ["branch-discovered"]

    # Verification carries the preserved gate log by path, never its contents.
    assert verification["label"] == "branch push feature/api"
    assert verification["status"] == "ok"
    assert verification["reference"] == {
        "kind": "gate_log",
        "value": "/runs/demo/gate.log",
    }
    assert verification["parent_id"] == node_span["id"]

    # Publication has no recorded start, so the PR that opened it starts the span, and
    # its records stay events inside it rather than being consumed by the boundary.
    assert publication["started_at"] == datetime.fromtimestamp(BASE + 9, UTC).isoformat()
    assert publication["ended_at"] is None
    assert publication["reference"] == {"kind": "pr", "value": "https://x/pull/7"}
    assert _kinds(publication) == ["pr-created", "pr-checks-observed"]

    # Every timestamp is RFC 3339 UTC, never the epoch seconds the journal stores.
    for span in spans:
        assert span["started_at"].endswith("+00:00")
        assert all(event["at"].endswith("+00:00") for event in span["events"])


def test_settled_work_closes_its_spans_and_points_at_the_recorded_artifacts() -> None:
    events = [
        _event(1, "round-started"),
        _event(2, "node-started", node="api"),
        _event(3, "human-waiting", node="api", step="review", detail={"step_kind": "human"}),
        _event(4, "human-attested", node="api", detail={"ref": "api"}),
        _event(5, "publication-finished", node="api", detail={"pr": "https://x/pull/7"}),
        _event(
            6,
            "node-settled",
            node="api",
            detail={
                "status": "done",
                "result": {
                    "status": "done",
                    "artifacts": {
                        "worker_report": "/runs/demo/report.json",
                        "gate_log": "/runs/demo/gate.log",
                    },
                },
            },
        ),
        _event(7, "round-finished", detail={"result": {"state": "complete"}}),
    ]

    spans = assemble(events, [], DetailSnapshot())

    node_span = _one(spans, "node")
    assert node_span["ended_at"] is not None
    assert node_span["status"] == "done"
    # The worker's own report, by path: the timeline never inlines a report body.
    assert node_span["reference"] == {"kind": "worker_report", "value": "/runs/demo/report.json"}
    assert _one(spans, "round")["ended_at"] is not None
    assert _one(spans, "round")["status"] == "finished"

    human = _one(spans, "human-wait")
    assert human["status"] == "attested"
    assert human["ended_at"] == datetime.fromtimestamp(BASE + 4, UTC).isoformat()

    publication = _one(spans, "publication")
    assert publication["status"] == "finished"
    assert _kinds(publication) == ["publication-finished"]


def test_high_frequency_records_roll_up_instead_of_one_item_each() -> None:
    """A thousand lock waits must not become a thousand timeline items."""
    events: list[Event] = [
        _event(1, "round-started"),
        _event(2, "node-started", node="api"),
        _event(3, "node-started", node="ui"),
    ]
    for seq in range(4, 1204):
        events.append(
            _event(
                seq,
                "lock-wait",
                node="api" if seq % 2 else "ui",
                detail={"identity": "merge", "seconds": 0.25, "acquired": True},
            )
        )

    spans = assemble(events, [], DetailSnapshot())

    rollups = _by_kind(spans, "rollup")
    assert len(rollups) == 2  # one per node, not one per record
    assert sum(span["count"] for span in rollups) == 1200
    assert {span["node_id"] for span in rollups} == {"api", "ui"}
    for span in rollups:
        assert span["label"] == "lock-wait"
        assert span["count"] == 600
        assert span["total_duration_ms"] == 150_000  # 600 * 0.25s, as telemetry totals them
        assert span["events"] == []
        assert span["ended_at"] is not None
    # The whole payload stays bounded by the graph rather than by contention.
    assert len(spans) == 5


def test_a_lint_run_nests_inside_the_dispatch_it_ran_under() -> None:
    """`transportRole: llmlint` is work *within* a worker dispatch, not beside it."""
    events = [
        _event(1, "round-started"),
        _event(2, "node-started", node="api", offset=0),
        _event(3, "node-settled", node="api", detail={"status": "done"}, offset=600),
    ]
    conversations = [
        # History lists the lint session first; the fold must still nest it correctly.
        _conversation(
            "lint-1",
            started="2026-07-19T00:05:00Z",
            transport_role="llmlint",
            agent_role="worker",
            node="api",
            round_number=1,
        ),
        _conversation(
            "worker-1",
            started="2026-07-19T00:01:00Z",
            turns=2,
            node="api",
            round_number=1,
            finished="2026-07-19T00:09:00Z",
        ),
        _conversation(
            "judge-1",
            started="2026-07-19T00:02:00Z",
            transport_role="judge",
            agent_role="judge",
            node="api",
            round_number=1,
        ),
    ]

    spans = assemble(events, conversations, DetailSnapshot())

    dispatches = {span["id"]: span for span in _by_kind(spans, "dispatch")}
    worker = dispatches["dispatch-worker-1"]
    lint = dispatches["dispatch-lint-1"]
    judge = dispatches["dispatch-judge-1"]
    node_span = _one(spans, "node")

    assert lint["parent_id"] == worker["id"]  # nested, not a sibling
    assert worker["parent_id"] == node_span["id"]
    assert judge["parent_id"] == node_span["id"]  # a judge is a sibling of the worker

    # Each dispatch points at its transcript rather than carrying it.
    assert worker["reference"] == {"kind": "conversation", "value": "worker-1"}
    assert worker["ended_at"] == "2026-07-19T00:09:00+00:00"
    assert [event["kind"] for event in worker["events"]] == [CONVERSATION_TURN_KIND] * 2
    assert all(
        event["reference"] == {"kind": "conversation", "value": "worker-1"}
        for event in worker["events"]
    )

    # Both roles travel on the span, so a reader can say what each of these three
    # dispatches was without opening a single transcript. The lint run is the case
    # that needs the pair: it is the worker's own verification, told apart from the
    # worker only by its transport role.
    assert [(span["agent_role"], span["transport_role"]) for span in (worker, judge, lint)] == [
        ("worker", "agent"),
        ("judge", "judge"),
        ("worker", "llmlint"),
    ]


def test_a_second_lint_run_nests_under_the_worker_not_under_the_first_lint() -> None:
    """A worker that lints twice must not hang its second lint off its first."""
    events = [
        _event(1, "round-started", offset=0),
        _event(2, "node-started", node="api", offset=0),
    ]
    conversations = [
        _conversation(
            "worker-1",
            started="2026-07-19T00:01:00Z",
            node="api",
            finished="2026-07-19T00:30:00Z",
        ),
        # Still open — a lint session whose transcript records no finish.
        _conversation(
            "lint-1",
            started="2026-07-19T00:05:00Z",
            transport_role="llmlint",
            node="api",
            state="running",
        ),
        _conversation(
            "lint-2",
            started="2026-07-19T00:20:00Z",
            transport_role="llmlint",
            node="api",
        ),
    ]

    spans = {
        span["id"]: span
        for span in _by_kind(assemble(events, conversations, DetailSnapshot()), "dispatch")
    }

    assert spans["dispatch-lint-1"]["parent_id"] == "dispatch-worker-1"
    assert spans["dispatch-lint-2"]["parent_id"] == "dispatch-worker-1"


def test_a_conversation_that_names_no_node_lands_on_its_round_or_the_run() -> None:
    """Check-in is dispatched per round and the orchestrator per run; neither has a node."""
    events = [
        _event(1, "round-started", offset=0),
        _event(2, "node-started", node="api", offset=0),
    ]
    conversations = [
        _conversation(
            "check-in-1",
            started="2026-07-19T00:03:00Z",
            agent_role="check-in",
            round_number=1,
            state="running",
        ),
        _conversation("orchestrator-1", started="2026-07-19T00:00:30Z", agent_role="orchestrator"),
    ]

    spans = assemble(events, conversations, DetailSnapshot())
    dispatches = {span["id"]: span for span in _by_kind(spans, "dispatch")}

    # A round-scoped session attaches to its round rather than being forced onto a node.
    assert dispatches["dispatch-check-in-1"]["parent_id"] == _one(spans, "round")["id"]
    assert "node_id" not in dispatches["dispatch-check-in-1"]
    # A session that names neither is run-level, and is kept rather than discarded.
    assert "parent_id" not in dispatches["dispatch-orchestrator-1"]
    # Still speaking: no recorded finish and a non-terminal state leaves the span open.
    assert dispatches["dispatch-check-in-1"]["ended_at"] is None
    # No recorded finish, but a terminal state closes at the last turn.
    assert dispatches["dispatch-orchestrator-1"]["ended_at"] == "2026-07-19T00:00:30+00:00"


def test_an_unparseable_timestamp_drops_its_item_instead_of_inventing_one() -> None:
    unusable = _conversation("broken-1", started="whenever", node="api")
    spans = assemble(
        [_event(1, "round-started"), _event(2, "node-started", node="api")],
        [unusable],
        DetailSnapshot(),
    )

    assert _by_kind(spans, "dispatch") == []
    # A turn with no usable stamp still appears, anchored to the span it belongs to.
    live = _conversation("live-1", started="2026-07-19T00:01:00Z", node="api")
    live["conversation"]["turns"][0]["timestamp"] = ""
    dispatched = _by_kind(
        assemble(
            [_event(1, "round-started"), _event(2, "node-started", node="api")],
            [live],
            DetailSnapshot(),
        ),
        "dispatch",
    )
    assert dispatched[0]["events"][0]["at"] == dispatched[0]["started_at"]


def test_an_open_publication_shows_the_state_the_monitor_last_observed() -> None:
    """The snapshot has no timestamps, so it answers "where is this PR now" instead."""
    events = [
        _event(1, "round-started"),
        _event(2, "node-started", node="api"),
        _event(3, "pr-created", node="api", detail={"pr": "https://x/pull/7", "number": 7}),
    ]
    snapshot = DetailSnapshot(
        prs={
            "pr:acme/app#7": PrDetail(number=7, url="https://x/pull/7", state="MERGED").to_record()
        }
    )

    spans = assemble(events, [], snapshot)
    assert _one(spans, "publication")["status"] == "MERGED"

    # A closed publication keeps the verdict the journal recorded for it.
    closed = [
        *events,
        _event(4, "publication-failed", node="api", detail={"pr": "https://x/pull/7"}),
    ]
    assert _one(assemble(closed, [], snapshot), "publication")["status"] == "failed"


def _run(runs_dir: Path, run_id: str) -> Path:
    """A strict-valid run directory with one started, unsettled node."""
    run_dir = runs_dir / run_id
    prepare_round(run_dir, {"tasks": [{"id": "api", "task": "ship"}]})
    journal = open_journal(run_dir, RunId(run_id), 1)
    journal.append("node-added", detail={"definition": {"id": "api", "task": "ship"}})
    journal.append("round-started", detail={"plan": {"schema_version": 3}})
    journal.append("node-started", node=NodeId("api"), detail={"node_kind": "direct"})
    return run_dir


def test_run_timeline_serves_a_recorded_run_and_rejects_what_it_cannot_read(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    run_dir = _run(runs, "demo")

    served = run_timeline(runs, "demo", oneharness_bin=ABSENT)
    assert served["api_version"] == 2
    assert served["run_id"] == "demo"
    assert [span["kind"] for span in served["spans"]] == ["round", "node"]
    # A missing history store and an absent snapshot degrade to nothing, not a failure.
    assert served["spans"][1]["ended_at"] is None
    node_only = run_timeline(runs, "demo", node_id="api", oneharness_bin=ABSENT)
    assert {span.get("node_id") for span in node_only["spans"]} == {"api"}
    run_only = run_timeline(runs, "demo", scope="run", oneharness_bin=ABSENT)
    assert [span["kind"] for span in run_only["spans"]] == ["round", "node"]
    assert run_only["spans"][1]["node_id"] == "api"

    with pytest.raises(InvalidRunId):
        run_timeline(runs, "bad!id", oneharness_bin=ABSENT)
    with pytest.raises(RunNotFound):
        run_timeline(runs, "absent", oneharness_bin=ABSENT)

    journal = run_dir / "events.jsonl"
    journal.write_text(journal.read_text(encoding="utf-8") + '{"kind":"bogus"}\n', encoding="utf-8")
    with pytest.raises(ProjectionFailed):
        run_timeline(runs, "demo", oneharness_bin=ABSENT)


def test_run_timeline_reads_the_persisted_snapshot_and_survives_a_corrupt_one(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    run_dir = _run(runs, "demo")
    journal = open_journal(run_dir, RunId("demo"), 1)
    journal.append("pr-created", node=NodeId("api"), detail={"pr": "https://x/pull/9", "number": 9})
    save_snapshot(
        run_dir,
        DetailSnapshot(
            prs={
                "pr:acme/app#9": PrDetail(
                    number=9, url="https://x/pull/9", state="OPEN"
                ).to_record()
            }
        ),
    )

    spans = run_timeline(runs, "demo", oneharness_bin=ABSENT)["spans"]
    assert _one(spans, "publication")["status"] == "OPEN"

    # A snapshot this build cannot read is an optimization over observation, never
    # evidence: the timeline still serves the journal it can read.
    snapshot = run_dir / "monitor" / "details.json"
    snapshot.write_text(json.dumps({"version": 999, "prs": "not a mapping"}), encoding="utf-8")
    degraded = run_timeline(runs, "demo", oneharness_bin=ABSENT)["spans"]
    assert "status" not in _one(degraded, "publication")


def test_pr_drafting_and_conflict_resolution_bracket_their_own_work() -> None:
    """The two remaining recorded pairs, including the fallback that stays an event."""
    events = [
        _event(1, "round-started"),
        _event(2, "node-started", node="api"),
        _event(3, "conflict-resolution-started", node="api", detail={"base": "main"}),
        _event(4, "conflict-resolution-finished", node="api", detail={"ok": True}),
        _event(5, "pr-drafting-started", node="api", detail={"base": "main"}),
        _event(6, "pr-drafting-fallback", node="api", detail={"reason": "drafting failed"}),
        _event(7, "pr-drafting-finished", node="api", detail={"completed": False}),
    ]

    spans = assemble(events, [], DetailSnapshot())

    conflict = _one(spans, "conflict-resolution")
    assert conflict["status"] == "ok"
    assert conflict["ended_at"] is not None

    drafting = _one(spans, "pr-drafting")
    # Drafting failure must never block publication, so it settles as not-completed.
    assert drafting["status"] == "not-completed"
    assert _kinds(drafting) == ["pr-drafting-fallback"]


def test_a_partial_stream_and_an_unusable_timestamp_are_survivable() -> None:
    """A journal whose start this build never saw still folds into a timeline."""
    events = [
        # A resumed run: the round finishes with no recorded start in this file.
        _event(1, "round-finished", detail={"result": {}}),
        _event(2, "human-attested", node="api", detail={"ref": "api"}),
        _event(3, "node-started", node="api"),
        # Far outside the representable range: the record is storable but unplaceable.
        _event(4, "node-settled", node="api", detail={"status": "done"}, offset=1e30),
    ]

    spans = assemble(events, [], DetailSnapshot())

    # Nothing was invented: the unplaceable settle is dropped, so the node stays open.
    assert _one(spans, "node")["ended_at"] is None
    # And the unpaired finish and attestation closed nothing rather than raising.
    assert _by_kind(spans, "human-wait") == []


def test_naive_and_offset_timestamps_are_normalized_to_utc() -> None:
    naive = _conversation("naive-1", started="2026-07-19T00:01:00", node="api")
    offset = _conversation("offset-1", started="2026-07-18T21:01:00-03:00", node="api")
    spans = assemble(
        [_event(1, "round-started"), _event(2, "node-started", node="api")],
        [naive, offset],
        DetailSnapshot(),
    )

    starts = {span["id"]: span["started_at"] for span in _by_kind(spans, "dispatch")}
    # A stamp with no zone is read as UTC; one with an offset is converted to it.
    assert starts["dispatch-naive-1"] == "2026-07-19T00:01:00+00:00"
    assert starts["dispatch-offset-1"] == "2026-07-19T00:01:00+00:00"


def test_observations_and_artifacts_that_say_nothing_are_ignored() -> None:
    """Defensive reads: a malformed record must not become a reference or a status."""
    events = [
        _event(1, "round-started"),
        _event(2, "node-started", node="api"),
        # A lock wait with no recorded seconds still counts, but adds no duration.
        _event(3, "lock-wait", node="api", detail={"identity": "merge"}),
        # A publication whose records name no PR cannot be joined to an observation.
        _event(5, "pr-checks-observed", node="api", detail={"repo": "acme/app"}),
        # A settle whose result records only unknown artifact keys stays unreferenced.
        _event(
            6,
            "node-settled",
            node="api",
            detail={"status": "done", "result": {"artifacts": {"scratch": "/tmp/x"}}},
        ),
    ]
    snapshot = DetailSnapshot(
        prs={
            # No url: nothing to key an observation on.
            "pr:acme/app#7": PrDetail(number=7, state="OPEN").to_record(),
            "pr:acme/app#8": PrDetail(number=8, url="https://x/pull/8", state="OPEN").to_record(),
        }
    )

    spans = assemble(events, [], snapshot)

    rollup = _one(spans, "rollup")
    assert (rollup["count"], rollup["total_duration_ms"]) == (1, 0)
    assert "reference" not in _one(spans, "node")
    publication = _one(spans, "publication")
    assert "reference" not in publication
    # An unjoinable observation must not be attached to an arbitrary publication.
    assert "status" not in publication


def test_a_history_store_that_errors_degrades_to_no_conversations(tmp_path: Path) -> None:
    """A store that exists but fails must not fail the graph the journal can serve."""
    runs = tmp_path / "runs"
    _run(runs, "demo")
    broken = tmp_path / "oneharness"
    broken.write_text("#!/bin/sh\necho 'history backend exploded' >&2\nexit 3\n", encoding="utf-8")
    broken.chmod(0o755)

    served = run_timeline(runs, "demo", oneharness_bin=str(broken))

    assert [span["kind"] for span in served["spans"]] == ["round", "node"]


def _capture(
    run_dir: Path,
    *,
    session: str,
    agent_role: str,
    round_number: int | None = None,
    started: float,
    finished: float | None = None,
    turns: tuple[str, ...] = (),
    history_failure: str | None = None,
) -> None:
    """One supervisory capture, written exactly as the dispatch layer writes it."""
    open_capture(
        run_dir,
        session=session,
        agent_role=agent_role,
        persona=agent_role,
        round_number=round_number,
        started_at=started,
    )
    for index, text in enumerate(turns):
        record_turn(run_dir, session, text, at=started + index + 1)
    if finished is not None:
        close_capture(
            run_dir,
            session,
            status="failed" if history_failure else "completed",
            history_failure=history_failure,
            finished_at=finished,
        )


def test_a_supervisory_session_history_lost_is_served_from_its_local_capture() -> None:
    """The tier whose harness refused the write is still a span, with the refusal on it."""
    events = [_event(1, "round-started", offset=0), _event(2, "node-started", node="api", offset=0)]
    captures = [
        SupervisoryCapture(
            session="orchestrator-demo",
            agent_role="orchestrator",
            started_at="2026-07-19T00:00:10+00:00",
            status="running",
            turns=({"at": "2026-07-19T00:00:40+00:00", "text": "driving round 1"},),
        ),
        SupervisoryCapture(
            session="check-in-demo-1",
            agent_role="check-in",
            started_at="2026-07-19T00:01:00+00:00",
            finished_at="2026-07-19T00:01:30+00:00",
            status="failed",
            round=1,
            history_failure="new history run lacks complete v1.0 telemetry",
        ),
    ]

    spans = assemble(events, [], DetailSnapshot(), captures)
    dispatches = {span["id"]: span for span in _by_kind(spans, "dispatch")}
    driver = dispatches["capture-orchestrator-demo"]
    check_in = dispatches["capture-check-in-demo-1"]

    assert driver["agent_role"] == "orchestrator"
    assert driver["transport_role"] == "agent"
    # Still speaking: a capture nothing closed is open-ended, exactly like a live
    # transcript, rather than being given an invented end.
    assert driver["ended_at"] is None
    assert "reference" not in driver, "a capture has no transcript to point at"
    assert _kinds(driver) == [CONVERSATION_TURN_KIND]
    assert driver["events"][0]["status"] == "captured"
    assert driver["detail"] == {"output_tail": "2026-07-19T00:00:40+00:00 driving round 1"}

    # A round-scoped capture attaches to its round, as a recorded check-in would.
    assert check_in["parent_id"] == _one(spans, "round")["id"]
    assert check_in["ended_at"] == "2026-07-19T00:01:30+00:00"
    failure = next(event for event in check_in["events"] if event["kind"] != CONVERSATION_TURN_KIND)
    assert failure["kind"] == HISTORY_WRITE_FAILURE_KIND
    assert failure["status"] == "new history run lacks complete v1.0 telemetry"


def test_a_capture_stands_down_when_history_did_record_that_session() -> None:
    """Serving both would draw one session twice; the recorded transcript wins."""
    events = [_event(1, "round-started", offset=0)]
    conversations = [
        _conversation("orchestrator-1", started="2026-07-19T00:00:30Z", agent_role="orchestrator"),
        _conversation(
            "check-in-1",
            started="2026-07-19T00:01:00Z",
            agent_role="check-in",
            round_number=1,
        ),
    ]
    captures = [
        SupervisoryCapture(
            session="orchestrator-demo",
            agent_role="orchestrator",
            started_at="2026-07-19T00:00:10+00:00",
            status="running",
        ),
        SupervisoryCapture(
            session="check-in-demo-1",
            agent_role="check-in",
            started_at="2026-07-19T00:01:00+00:00",
            status="completed",
            round=1,
        ),
        # A second round's check-in that history never recorded still needs its span.
        SupervisoryCapture(
            session="check-in-demo-2",
            agent_role="check-in",
            started_at="2026-07-19T00:02:00+00:00",
            status="completed",
            round=2,
        ),
    ]

    spans = assemble(events, conversations, DetailSnapshot(), captures)
    served = sorted(span["id"] for span in _by_kind(spans, "dispatch"))

    assert served == ["capture-check-in-demo-2", "dispatch-check-in-1", "dispatch-orchestrator-1"]
    driver = next(span for span in spans if span["id"] == "dispatch-orchestrator-1")
    assert driver["reference"] == {"kind": "conversation", "value": "orchestrator-1"}


def test_an_undatable_capture_is_dropped_rather_than_placed_by_invention() -> None:
    spans = assemble(
        [_event(1, "round-started", offset=0)],
        [],
        DetailSnapshot(),
        [
            SupervisoryCapture(
                session="orchestrator-demo",
                agent_role="orchestrator",
                started_at="whenever",
                status="running",
            )
        ],
    )
    assert _by_kind(spans, "dispatch") == []


def test_the_phase_lands_on_the_newest_driver_span_and_nothing_else() -> None:
    """A resumed run has more than one driver session; the phase describes now."""
    driver = DriverState(
        session="orchestrator-demo",
        pid=4242,
        alive=True,
        phase="executing-run-plan",
        round=1,
        last_activity_at=None,
    )
    conversations = [
        _conversation(
            "orchestrator-old", started="2026-07-19T00:00:10Z", agent_role="orchestrator"
        ),
        _conversation(
            "orchestrator-new", started="2026-07-19T00:05:00Z", agent_role="orchestrator"
        ),
        _conversation("check-in-1", started="2026-07-19T00:06:00Z", agent_role="check-in"),
    ]

    spans = assemble(
        [_event(1, "round-started", offset=0)], conversations, DetailSnapshot(), (), driver
    )
    by_id = {span["id"]: span for span in spans}

    assert by_id["dispatch-orchestrator-new"]["phase"] == "executing-run-plan"
    assert "phase" not in by_id["dispatch-orchestrator-old"]
    assert "phase" not in by_id["dispatch-check-in-1"]


def test_a_run_the_orchestrator_drives_serves_its_driver_and_check_in_at_run_scope(
    tmp_path: Path,
) -> None:
    """Both supervisory spans survive `scope=run`, which is where the run row reads them."""
    runs = tmp_path / "runs"
    run_dir = _run(runs, "demo")
    _capture(
        run_dir,
        session="orchestrator-demo",
        agent_role="orchestrator",
        started=BASE + 1,
        turns=("driving round 1",),
    )
    _capture(
        run_dir,
        session="check-in-demo-1",
        agent_role="check-in",
        round_number=1,
        started=BASE + 2,
        finished=BASE + 3,
        history_failure="new history run lacks complete v1.0 telemetry",
    )
    (run_dir / "orchestrator").mkdir(parents=True, exist_ok=True)
    (run_dir / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": os.getpid(), "host": socket.gethostname()}),
        encoding="utf-8",
    )

    served = run_timeline(runs, "demo", oneharness_bin=ABSENT, scope="run")
    dispatches = {span["id"]: span for span in served["spans"] if span["kind"] == "dispatch"}

    assert sorted(dispatches) == ["capture-check-in-demo-1", "capture-orchestrator-demo"]
    assert dispatches["capture-orchestrator-demo"]["phase"] == "executing-run-plan"
    assert dispatches["capture-orchestrator-demo"]["ended_at"] is None
    assert dispatches["capture-check-in-demo-1"]["agent_role"] == "check-in"


def test_run_scope_keeps_a_waiting_node_and_the_measured_cost_of_an_aggregate(
    tmp_path: Path,
) -> None:
    """Two things the graph-level reading of a run is built on, at `scope=run`.

    A node waiting on a person is journalled without ever being started, and lock
    contention is journalled per acquisition rather than per graph transition. Both
    are ordinary, and a summary that dropped the first and re-measured the second
    told an operator that a graph blocked on a human had done nothing for an hour
    and that four seconds of contention had cost two minutes.
    """
    runs = tmp_path / "runs"
    run_dir = runs / "demo"
    tasks = [
        {"id": "api", "task": "ship"},
        {"id": "signoff", "kind": "human", "task": "approve the release"},
    ]
    prepare_round(run_dir, {"tasks": tasks})
    journal = open_journal(run_dir, RunId("demo"), 1)
    for task in tasks:
        journal.append("node-added", detail={"definition": task})
    journal.append("round-started", detail={"plan": {"schema_version": 3}})
    journal.append("node-started", node=NodeId("api"), detail={"node_kind": "direct"})
    for _ in range(4):
        journal.append("lock-wait", node=NodeId("api"), detail={"seconds": 1.5})
    # No `node-started`: the scheduler journals the wait itself and nothing before it.
    journal.append("human-waiting", node=NodeId("signoff"), detail={"status": "waiting"})

    served = run_timeline(runs, "demo", oneharness_bin=ABSENT, scope="run")
    by_node: dict[str | None, list[TimelineSpan]] = {}
    for span in served["spans"]:
        by_node.setdefault(span.get("node_id"), []).append(span)

    # The waiting node is present with the wait that is the only thing it recorded,
    # even though nothing ever opened a span to hold it.
    waiting = by_node["signoff"]
    assert [span["kind"] for span in waiting] == ["rollup"]
    assert waiting[0]["label"] == "human-wait"
    assert "parent_id" not in waiting[0]
    assert waiting[0]["round"] == 1

    # And the aggregate reports the six seconds it measured, not the window it fell in.
    aggregate = next(span for span in by_node["api"] if span["label"] == "rollup")
    assert aggregate["count"] == 1
    assert aggregate["total_duration_ms"] == 6000
