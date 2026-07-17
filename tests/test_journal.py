"""Journal tests: real files, real fcntl locks, real torn-tail reconciliation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.journal import (
    EVENT_KINDS,
    SCHEMA_VERSION,
    Event,
    JournalError,
    NullJournal,
    open_journal,
    parse_event,
    read_events,
    reconcile,
)


def test_append_writes_one_durable_record_per_event(tmp_path: Path) -> None:
    journal = open_journal(tmp_path / "run-1", "run-1", 3)
    journal.append("node-started", node="api")
    journal.append("node-settled", node="api", status="done")

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
    first = open_journal(run_dir, "run-2", 1)
    first.append("round-started")
    first.append("node-started", node="a")

    # A later round reopens the same journal; the sequence must continue, not reset.
    second = open_journal(run_dir, "run-2", 2)
    second.append("round-started")

    assert [e.seq for e in read_events(run_dir / "events.jsonl")] == [1, 2, 3]
    assert [e.round for e in read_events(run_dir / "events.jsonl")] == [1, 1, 2]


def test_open_journal_truncates_a_torn_trailing_record(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-3"
    journal = open_journal(run_dir, "run-3", 1)
    journal.append("node-started", node="a")
    path = run_dir / "events.jsonl"

    # Simulate a crash mid-append: a partial line with no terminating newline.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"version": 1, "seq": 2, "kind": "node-set')

    assert reconcile(path) == 1
    assert path.read_bytes().endswith(b"\n")
    events = read_events(path)
    assert [e.kind for e in events] == ["node-started"]

    # And the reopened journal appends *after* the intact record, not onto the torn one.
    reopened = open_journal(run_dir, "run-3", 2)
    reopened.append("node-settled", node="a")
    assert [e.kind for e in read_events(path)] == ["node-started", "node-settled"]
    assert [e.seq for e in read_events(path)] == [1, 2]


def test_read_events_skips_a_torn_tail_without_mutating_the_file(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"version": 1, "seq": 1, "at": 0, "kind": "round-started", '
                    '"run_id": "r", "round": 1}\n{"partial": ', encoding="utf-8")
    before = path.read_bytes()

    assert [e.kind for e in read_events(path)] == ["round-started"]
    assert path.read_bytes() == before  # reading is not a repair


def test_reconcile_is_a_noop_on_a_missing_or_intact_journal(tmp_path: Path) -> None:
    missing = tmp_path / "nope.jsonl"
    assert reconcile(missing) == 0
    assert read_events(missing) == []

    intact = tmp_path / "events.jsonl"
    intact.write_text('{"version": 1, "seq": 1, "at": 0, "kind": "round-started", '
                      '"run_id": "r", "round": 1}\n', encoding="utf-8")
    assert reconcile(intact) == 1
    assert reconcile(intact) == 1  # idempotent


@pytest.mark.parametrize(
    "record",
    [
        "not a mapping",
        {"version": SCHEMA_VERSION + 1, "seq": 1, "at": 0, "kind": "round-started",
         "run_id": "r", "round": 1},
        {"version": SCHEMA_VERSION, "seq": 1, "at": 0, "kind": "invented",
         "run_id": "r", "round": 1},
        {"version": SCHEMA_VERSION, "seq": 1, "at": 0, "kind": "round-started",
         "run_id": "", "round": 1},
        {"version": SCHEMA_VERSION, "seq": 1, "at": 0, "kind": "round-started",
         "run_id": "r", "round": True},
        {"version": SCHEMA_VERSION, "seq": 1, "at": 0, "kind": "round-started",
         "run_id": "r", "round": 1, "node": 7},
        {"version": SCHEMA_VERSION, "seq": 1, "at": 0, "kind": "round-started",
         "run_id": "r", "round": 1, "detail": "nope"},
    ],
)
def test_parse_event_skips_unrecognized_records(record) -> None:
    assert parse_event(record) is None


def test_read_events_skips_junk_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    good = json.dumps(
        Event(kind="round-started", run_id="r", round=1, seq=1, at=1.0).to_record(),
        sort_keys=True,
    )
    path.write_text(f"not json\n\n{good}\n", encoding="utf-8")
    assert [e.kind for e in read_events(path)] == ["round-started"]


def test_append_rejects_an_unknown_kind(tmp_path: Path) -> None:
    journal = open_journal(tmp_path / "run-4", "run-4", 1)
    with pytest.raises(JournalError, match="unknown journal event kind"):
        journal.append("not-a-kind")  # type: ignore[arg-type]


def test_null_journal_accepts_every_kind_and_writes_nothing(tmp_path: Path) -> None:
    journal = NullJournal()
    for kind in sorted(EVENT_KINDS):
        assert journal.append(kind, node="a", step="b", extra=1) is None  # type: ignore[arg-type]
    assert list(tmp_path.iterdir()) == []


def test_concurrent_processes_never_interleave_a_record(tmp_path: Path) -> None:
    """Two real processes appending at once must produce whole, parseable lines."""
    run_dir = tmp_path / "run-5"
    open_journal(run_dir, "run-5", 1)
    script = (
        "import sys; from pathlib import Path; from orchestrator.journal import open_journal;"
        "j = open_journal(Path(sys.argv[1]), 'run-5', 1);"
        "[j.append('node-started', node=sys.argv[2], filler='x' * 200) for _ in range(25)]"
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(run_dir), name],
            env={**__import__("os").environ, "AI_ORCHESTRATOR_HOME": str(tmp_path / "home")},
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
