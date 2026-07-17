"""Journal tests: real files, real fcntl locks, real torn-tail reconciliation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from orchestrator.journal import (
    EVENT_KINDS,
    SCHEMA_VERSION,
    Event,
    EventKind,
    JournalError,
    NullJournal,
    Reconciliation,
    open_journal,
    parse_event,
    read_events,
    reconcile,
)
from orchestrator.runs import NodeId, RunId, StepId


def _record(**overrides: object) -> dict[str, object]:
    """A well-formed stored record, before a single field is made invalid."""
    return {
        "version": SCHEMA_VERSION,
        "seq": 1,
        "at": 0,
        "kind": "round-started",
        "run_id": "r",
        "round": 1,
        **overrides,
    }


def _line(**overrides: object) -> str:
    return json.dumps(_record(**overrides), sort_keys=True) + "\n"


def test_append_writes_one_durable_record_per_event(tmp_path: Path) -> None:
    journal = open_journal(tmp_path / "run-1", RunId("run-1"), 3)
    journal.append("node-started", node=NodeId("api"))
    journal.append("node-settled", node=NodeId("api"), detail={"status": "done"})

    lines = (tmp_path / "run-1" / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "node-started"
    assert first["run_id"] == "run-1"
    assert first["round"] == 3
    assert first["node"] == "api"
    assert first["version"] == SCHEMA_VERSION
    assert json.loads(lines[1])["detail"] == {"status": "done"}


def test_sequence_numbers_are_monotonic_across_reopen(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-2"
    first = open_journal(run_dir, RunId("run-2"), 1)
    first.append("round-started")
    first.append("node-started", node=NodeId("a"))

    # A later round reopens the same journal; the sequence must continue, not reset.
    second = open_journal(run_dir, RunId("run-2"), 2)
    second.append("round-started")

    assert [e.seq for e in read_events(run_dir / "events.jsonl")] == [1, 2, 3]
    assert [e.round for e in read_events(run_dir / "events.jsonl")] == [1, 1, 2]


def test_open_journal_truncates_a_torn_trailing_record(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-3"
    journal = open_journal(run_dir, RunId("run-3"), 1)
    journal.append("node-started", node=NodeId("a"))
    path = run_dir / "events.jsonl"

    # Simulate a crash mid-append: a partial line with no terminating newline.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"version": 1, "seq": 2, "kind": "node-set')

    state = reconcile(path, RunId("run-3"))
    assert (state.records, state.last_seq) == (1, 1)
    assert path.read_bytes().endswith(b"\n")
    events = read_events(path)
    assert [e.kind for e in events] == ["node-started"]

    # And the reopened journal appends *after* the intact record, not onto the torn one.
    reopened = open_journal(run_dir, RunId("run-3"), 2)
    reopened.append("node-settled", node=NodeId("a"))
    assert [e.kind for e in read_events(path)] == ["node-started", "node-settled"]
    assert [e.seq for e in read_events(path)] == [1, 2]


def test_reopening_past_unreadable_lines_does_not_reissue_a_stored_sequence(
    tmp_path: Path,
) -> None:
    """The resume point is read from the stored records, never counted off the lines.

    A journal accumulates lines this build cannot read — junk from a partial disk
    failure, a blank, a record from a newer schema. Counting lines to pick the next
    sequence hands out a number that is already on disk; reading the sequences does
    not.
    """
    run_dir = tmp_path / "run-c"
    journal = open_journal(run_dir, RunId("run-c"), 1)
    journal.append("round-started")
    path = run_dir / "events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("garbage that is not json\n")
        handle.write(_line(run_id="run-c", version=SCHEMA_VERSION + 1, seq=99))
        handle.write("\n")

    reopened = open_journal(run_dir, RunId("run-c"), 2)
    appended = reopened.append("round-finished")

    # Four lines on disk, but only one readable record: the next sequence is 2, not 5.
    assert appended.seq == 2
    assert [e.seq for e in read_events(path)] == [1, 2]


def test_reopening_resumes_above_tied_and_out_of_order_sequences(tmp_path: Path) -> None:
    """Concurrent appenders tie and reorder; the resume point must clear all of them."""
    run_dir = tmp_path / "run-d"
    run_dir.mkdir()
    path = run_dir / "events.jsonl"
    path.write_text(_line(seq=3) + _line(seq=1) + _line(seq=3), encoding="utf-8")

    journal = open_journal(run_dir, RunId("r"), 2)

    assert journal.append("round-finished").seq == 4


def test_reconcile_ignores_a_stored_record_that_breaks_the_value_contract(tmp_path: Path) -> None:
    """A corrupt sequence must not be trusted as the high-water mark."""
    path = tmp_path / "events.jsonl"
    path.write_text(
        _line(seq=2) + _line(seq=0) + _line(seq=-5) + _line(at=float("nan")) + _line(round=0),
        encoding="utf-8",
    )

    assert reconcile(path, RunId("r")) == Reconciliation(records=1, last_seq=2)
    assert [e.seq for e in read_events(path)] == [2]


def test_reconcile_ignores_another_runs_records_without_deleting_them(tmp_path: Path) -> None:
    """A foreign id means the file was corrupted, and guessing is worse than skipping."""
    path = tmp_path / "events.jsonl"
    path.write_text(_line(seq=1) + _line(run_id="someone-else", seq=50), encoding="utf-8")
    before = path.read_bytes()

    assert reconcile(path, RunId("r")) == Reconciliation(records=1, last_seq=1)
    assert path.read_bytes() == before


def test_journal_events_excludes_another_runs_records(tmp_path: Path) -> None:
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    path = run_dir / "events.jsonl"
    path.write_text(_line(seq=1) + _line(run_id="someone-else", seq=2), encoding="utf-8")

    journal = open_journal(run_dir, RunId("r"), 1)

    assert [event.run_id for event in journal.events()] == ["r"]


def test_read_events_skips_a_torn_tail_without_mutating_the_file(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"version": 1, "seq": 1, "at": 0, "kind": "round-started", '
        '"run_id": "r", "round": 1}\n{"partial": ',
        encoding="utf-8",
    )
    before = path.read_bytes()

    assert [e.kind for e in read_events(path)] == ["round-started"]
    assert path.read_bytes() == before  # reading is not a repair


def test_reconcile_is_a_noop_on_a_missing_or_intact_journal(tmp_path: Path) -> None:
    missing = tmp_path / "nope.jsonl"
    assert reconcile(missing, RunId("r")) == Reconciliation(records=0, last_seq=0)
    assert read_events(missing) == []

    intact = tmp_path / "events.jsonl"
    intact.write_text(_line(), encoding="utf-8")
    before = intact.read_bytes()

    assert reconcile(intact, RunId("r")) == Reconciliation(records=1, last_seq=1)
    assert reconcile(intact, RunId("r")) == Reconciliation(records=1, last_seq=1)  # idempotent
    assert intact.read_bytes() == before  # an intact journal is not rewritten


@pytest.mark.parametrize(
    "record",
    [
        pytest.param("not a mapping", id="not-a-mapping"),
        pytest.param(_record(version=SCHEMA_VERSION + 1), id="newer-schema"),
        pytest.param(_record(kind="invented"), id="unknown-kind"),
        pytest.param(_record(run_id=""), id="empty-run-id"),
        pytest.param(_record(run_id=7), id="non-string-run-id"),
        pytest.param(_record(round=True), id="boolean-round"),
        pytest.param(_record(seq=True), id="boolean-seq"),
        pytest.param(_record(node=7), id="non-string-node"),
        pytest.param(_record(node=""), id="empty-node"),
        pytest.param(_record(step=7), id="non-string-step"),
        pytest.param(_record(step=""), id="empty-step"),
        pytest.param(_record(detail="nope"), id="non-mapping-detail"),
        # The value contract: rounds and sequences count from 1, so a stored 0 is not
        # a low sequence but a corrupt one — honouring it would hand the next append
        # a number already on disk.
        pytest.param(_record(round=0), id="zero-round"),
        pytest.param(_record(round=-1), id="negative-round"),
        pytest.param(_record(seq=0), id="zero-seq"),
        pytest.param(_record(seq=-3), id="negative-seq"),
        # A timestamp has to be a real point in time. NaN/Infinity reach the file
        # because json.loads accepts them even though they are not valid JSON.
        pytest.param(_record(at=float("nan")), id="nan-timestamp"),
        pytest.param(_record(at=float("inf")), id="infinite-timestamp"),
        pytest.param(_record(at=float("-inf")), id="negative-infinite-timestamp"),
        pytest.param(_record(at=-1.0), id="pre-epoch-timestamp"),
        pytest.param(_record(at=True), id="boolean-timestamp"),
        pytest.param(_record(at="soon"), id="non-numeric-timestamp"),
    ],
)
def test_parse_event_skips_records_that_are_unrecognized_or_out_of_contract(
    record: object,
) -> None:
    assert parse_event(record) is None


def test_parse_event_round_trips_a_well_formed_record() -> None:
    event = Event(
        kind="step-settled",
        run_id=RunId("r"),
        round=2,
        seq=5,
        at=1.5,
        node=NodeId("api"),
        step=StepId("impl"),
        detail={"status": "done"},
    )
    assert parse_event(event.to_record()) == event


def _event(*, round_number: int = 1, seq: int = 1, at: float = 1.0) -> Event:
    return Event(kind="round-started", run_id=RunId("r"), round=round_number, seq=seq, at=at)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"round_number": 0}, "round must be a positive integer"),
        ({"round_number": -1}, "round must be a positive integer"),
        ({"seq": 0}, "seq must be a positive integer"),
        ({"seq": -1}, "seq must be a positive integer"),
        ({"at": float("nan")}, "timestamp must be a finite"),
        ({"at": float("inf")}, "timestamp must be a finite"),
        ({"at": -1.0}, "timestamp must be a finite"),
    ],
)
def test_event_rejects_an_out_of_contract_value_at_construction(
    kwargs: dict[str, float], message: str
) -> None:
    """The contract binds every constructor, not just the on-disk parser."""
    with pytest.raises(JournalError, match=message):
        _event(**kwargs)


def test_read_events_skips_junk_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    good = json.dumps(
        Event(kind="round-started", run_id=RunId("r"), round=1, seq=1, at=1.0).to_record(),
        sort_keys=True,
    )
    path.write_text(f"not json\n\n{good}\n", encoding="utf-8")
    assert [e.kind for e in read_events(path)] == ["round-started"]


def test_append_rejects_an_unknown_kind(tmp_path: Path) -> None:
    journal = open_journal(tmp_path / "run-4", RunId("run-4"), 1)
    # `cast` rather than a suppression: this is the value an untyped caller (a plan
    # file, a JSON payload) actually delivers, and rejecting it is the point.
    with pytest.raises(JournalError, match="unknown journal event kind"):
        journal.append(cast(EventKind, "not-a-kind"))


def test_open_journal_rejects_a_non_positive_round(tmp_path: Path) -> None:
    for round_number in (0, -1):
        with pytest.raises(JournalError, match="round must be a positive integer"):
            open_journal(tmp_path / "run-r", RunId("run-r"), round_number)


def test_open_journal_rejects_an_empty_run_id(tmp_path: Path) -> None:
    with pytest.raises(JournalError, match="run_id must be a non-empty string"):
        open_journal(tmp_path / "run-e", RunId(""), 1)


def test_null_journal_accepts_every_kind_and_writes_nothing(tmp_path: Path) -> None:
    journal = NullJournal()
    for kind in sorted(EVENT_KINDS):
        assert journal.append(kind, node=NodeId("a"), step=StepId("b"), detail={"x": 1}) is None
    assert list(tmp_path.iterdir()) == []


def test_detail_may_carry_keys_that_shadow_the_record_locators(tmp_path: Path) -> None:
    """A step's own 'kind'/'node' must not collide with the event's locators."""
    journal = open_journal(tmp_path / "run-6", RunId("run-6"), 1)
    journal.append(
        "step-settled",
        node=NodeId("api"),
        step=StepId("impl"),
        detail={"step_kind": "human", "node": "shadow"},
    )
    event = journal.events()[0]
    assert event.kind == "step-settled"
    assert event.node == "api"
    assert event.step == "impl"
    assert event.detail == {"step_kind": "human", "node": "shadow"}


def test_concurrent_processes_never_interleave_a_record(tmp_path: Path) -> None:
    """Two real processes appending at once must produce whole, parseable lines."""
    run_dir = tmp_path / "run-5"
    open_journal(run_dir, RunId("run-5"), 1)
    script = (
        "import sys; from pathlib import Path; from orchestrator.journal import open_journal;"
        "j = open_journal(Path(sys.argv[1]), 'run-5', 1);"
        "[j.append('node-started', node=sys.argv[2], detail={'filler': 'x' * 200})"
        " for _ in range(25)]"
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(run_dir), name],
            env={**os.environ, "AI_ORCHESTRATOR_HOME": str(tmp_path / "home")},
        )
        for name in ("a", "b")
    ]
    for worker in workers:
        assert worker.wait(timeout=60) == 0

    path = run_dir / "events.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == 50
    # Every line is individually whole: no append tore another's record.
    assert all(json.loads(line)["kind"] == "node-started" for line in lines)
    assert len(read_events(path)) == 50
