"""The monitored stream's own contracts: one capped line per event, dedup by durable
source identity, and an exit that only a successful graph earns.

Journals and ledgers here are written through the *real* `Journal` and `runs`
writers, so every test reads back exactly the bytes an executor would have left.
The history source is pointed at a oneharness that is not installed, which is both
the hermetic choice and the degradation the source promises: losing it costs detail,
never the transition itself.
"""

from __future__ import annotations

import io
import json
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import orchestrator.monitor as monitor_module
import orchestrator.runs as runs_module
from orchestrator.detail_snapshot import SNAPSHOT_VERSION, CommitDetail, PrDetail
from orchestrator.ids import GraphId
from orchestrator.journal import JOURNAL_NAME, open_journal
from orchestrator.monitor import (
    HEADER,
    SUMMARY_LIMIT,
    DetailSnapshot,
    Heartbeat,
    Monitor,
    MonitorError,
    MonitorEvent,
    Watchable,
    Writer,
    active_runs,
    follows,
    journal_events,
    load_snapshot,
    resolve_run,
    run_state,
    save_snapshot,
    snapshot_path,
    stream,
    summarize,
)
from orchestrator.monitor import (
    main as monitor_main,
)
from orchestrator.runs import NodeId, RunId, StepId, prepare_round, write_result

RUN = RunId("watch-me")
AT = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC).timestamp()
PLAN: dict[str, Any] = {
    "concurrency": 1,
    "tasks": [{"id": "api", "persona": "engineer", "task": "ship it"}],
}


def _settle(run_dir: Path, results: dict[str, Any], *, ok: bool, state: str) -> Path:
    """Record one finished round through the real ledger writer."""
    _, round_dir = prepare_round(run_dir, PLAN)
    write_result(
        round_dir,
        {"ok": ok, "state": state, "started_order": sorted(results), "results": results},
    )
    return round_dir


@pytest.fixture
def no_oneharness(tmp_path: Path) -> str:
    """A oneharness that is not installed — the history source degrades to silence."""
    return str(tmp_path / "absent" / "oneharness")


def _monitor(run_dir: Path, oneharness_bin: str, **overrides: Any) -> Monitor:
    fields: dict[str, Any] = {
        "run_id": RUN,
        "run_dir": run_dir,
        "oneharness_bin": oneharness_bin,
        "clock": lambda: AT,
    }
    return Monitor(**{**fields, **overrides})


def _event(**overrides: Any) -> MonitorEvent:
    fields: dict[str, Any] = {
        "at": AT,
        "source": "journal",
        "kind": "node-started",
        "stream_id": GraphId(run_id=RUN, round=1, node="api"),
        "summary": "node-started persona=engineer",
        "key": "journal:watch-me:1",
    }
    return MonitorEvent(**{**fields, **overrides})


class _Ticker:
    """A virtual clock whose sleep advances time, then ends the follow like Ctrl-C."""

    def __init__(self, *, stop_after: int) -> None:
        self.now = AT
        self.slept = 0
        self._stop_after = stop_after

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += 1
        self.now += seconds
        if self.slept >= self._stop_after:
            raise KeyboardInterrupt


# --- one event, one line -------------------------------------------------------


def test_a_summary_collapses_a_multi_line_value_onto_one_line() -> None:
    """A gate's stderr and an agent's own prose both reach here carrying newlines."""
    assert summarize("gate failed\nassert x == 1\r\n\tat line 3") == (
        "gate failed assert x == 1 at line 3"
    )
    assert summarize("  spaced   out  ") == "spaced out"


def test_a_separator_survives_as_a_separator_rather_than_welding_two_words() -> None:
    """Dropping the newline would yield `failedassert` — a token in neither line."""
    assert summarize("failed\nassert") == "failed assert"
    assert summarize("a\tb\rc\x0bd") == "a b c d"


def test_a_summary_strips_the_escape_sequences_a_coloured_log_carries() -> None:
    """The ESC is a Cc control character; what survives can no longer move a cursor."""
    assert summarize("plain \x1b[31mred\x1b[0m done") == "plain [31mred [0m done"
    assert "\x1b" not in summarize("\x00\x07\x1b[2Jcleared")


def test_a_long_summary_is_capped_to_one_scannable_line() -> None:
    """It sits beside a typed id that already leads to the full record."""
    capped = summarize("x" * 200)
    assert capped == "x" * (SUMMARY_LIMIT - 3) + "..."
    assert len(capped) == SUMMARY_LIMIT == 96


def test_a_summary_at_the_limit_is_left_exactly_as_it_is() -> None:
    assert summarize("y" * SUMMARY_LIMIT) == "y" * SUMMARY_LIMIT
    assert len(summarize("z" * (SUMMARY_LIMIT + 1))) == SUMMARY_LIMIT


def test_an_event_refuses_a_summary_no_source_should_have_built() -> None:
    """The cap is enforced at construction, so no source can bypass `summarize`."""
    with pytest.raises(MonitorError, match="exceeds 96 characters"):
        _event(summary="x" * (SUMMARY_LIMIT + 1))
    with pytest.raises(MonitorError, match="control character"):
        _event(summary="two\nlines")
    with pytest.raises(MonitorError, match="non-empty string"):
        _event(key="")


# --- the rendered stream -------------------------------------------------------


def test_only_the_text_stream_carries_the_header() -> None:
    """A jsonl consumer parses every line; a prose banner would be the one that is not."""
    text, jsonl = io.StringIO(), io.StringIO()
    Writer("text", text).header()
    Writer("jsonl", jsonl).header()
    assert text.getvalue() == HEADER + "\n"
    assert jsonl.getvalue() == ""
    # The header is a contract, not a banner: it names the command that resolves ids.
    assert "just history-show" in HEADER


def test_a_text_line_leads_with_the_id_to_ask_history_show_for() -> None:
    out = io.StringIO()
    Writer("text", out).event(_event())
    assert out.getvalue() == ("12:00:00  graph:watch-me/1/api  node-started persona=engineer\n")


def test_a_jsonl_event_envelope_carries_exactly_one_typed_id() -> None:
    out = io.StringIO()
    Writer("jsonl", out).event(_event())
    assert json.loads(out.getvalue()) == {
        "type": "event",
        "at": AT,
        "source": "journal",
        "kind": "node-started",
        "id": "graph:watch-me/1/api",
        "summary": "node-started persona=engineer",
    }


def test_a_heartbeat_carries_run_state_and_deliberately_no_id() -> None:
    """A heartbeat is the absence of a graph event; an id for it would not resolve."""
    beat = Heartbeat(at=AT, run_id=RUN, round=1, state="waiting", detail="1 waiting")
    text, jsonl = io.StringIO(), io.StringIO()
    Writer("text", text).heartbeat(beat)
    Writer("jsonl", jsonl).heartbeat(beat)
    assert text.getvalue() == "12:00:00  --  watch-me round-01 waiting: 1 waiting\n"
    record = json.loads(jsonl.getvalue())
    assert record == {
        "type": "heartbeat",
        "at": AT,
        "run_id": "watch-me",
        "round": 1,
        "state": "waiting",
        "detail": "1 waiting",
    }
    assert "id" not in record


def test_a_heartbeat_before_any_round_says_so_rather_than_inventing_one() -> None:
    beat = Heartbeat(
        at=AT, run_id=RUN, round=None, state="unknown", detail="no recorded rounds yet"
    )
    assert beat.text() == "12:00:00  --  watch-me no round unknown: no recorded rounds yet"


# --- the journal source --------------------------------------------------------


def test_a_round_transition_reaches_the_reader_as_state_not_as_an_id(tmp_path: Path) -> None:
    """A round has no node, so it has no graph: id — and every line must resolve."""
    run_dir = tmp_path / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append("round-started")
    journal.append("node-started", node=NodeId("api"), detail={"persona": "engineer"})
    journal.append("round-finished")

    events = journal_events(RUN, run_dir)
    assert [str(event.stream_id) for event in events] == ["graph:watch-me/1/api"]
    assert events[0].summary == "node-started persona=engineer"


def test_a_summary_renders_an_allowlist_not_whatever_the_payload_holds(tmp_path: Path) -> None:
    """A detail is open at the leaves by design; rendering it whole would put an
    unbounded blob on a line that must stay scannable."""
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append(
        "node-settled",
        node=NodeId("api"),
        detail={
            "status": "done",
            "ok": True,
            "usage": {"output_tokens": 7},
            "verdicts": ["a long verdict"],
            "chatter": "x" * 200,
        },
    )
    assert journal_events(RUN, run_dir)[0].summary == "node-settled status=done ok=yes"


def test_a_step_event_names_the_step_it_happened_in(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append(
        "step-settled",
        node=NodeId("api"),
        step=StepId("gate"),
        detail={"status": "failed", "command": "just gate"},
    )
    assert journal_events(RUN, run_dir)[0].summary == (
        "step-settled [gate] status=failed command=just gate"
    )


def test_a_node_the_id_grammar_cannot_render_is_left_out_of_the_stream(tmp_path: Path) -> None:
    """A line for it could not honour the header's promise that every id resolves."""
    run_dir = tmp_path / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append("node-started", node=NodeId("api service"))
    journal.append("node-started", node=NodeId("api"))
    assert [str(event.stream_id) for event in journal_events(RUN, run_dir)] == [
        "graph:watch-me/1/api"
    ]


def test_a_foreign_run_id_in_the_file_is_not_reported_as_this_run(tmp_path: Path) -> None:
    """It cannot arrive through `open_journal`, so the file was corrupted or
    hand-edited — and skipping those records is safer than trusting them."""
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append("node-started", node=NodeId("api"))
    open_journal(run_dir, RunId("another-run"), 1).append("node-started", node=NodeId("intruder"))
    assert [str(event.stream_id) for event in journal_events(RUN, run_dir)] == [
        "graph:watch-me/1/api"
    ]


# --- dedup by durable source identity ------------------------------------------


def test_polling_again_reports_only_what_is_new(tmp_path: Path, no_oneharness: str) -> None:
    """Every source is polled, so each pass re-reads what it already reported."""
    run_dir = tmp_path / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append("node-started", node=NodeId("api"))

    monitor = _monitor(run_dir, no_oneharness)
    assert [event.kind for event in monitor.poll()] == ["node-started"]
    assert monitor.poll() == []

    journal.append("node-settled", node=NodeId("api"), detail={"status": "done"})
    assert [event.kind for event in monitor.poll()] == ["node-settled"]
    assert monitor.poll() == []


def test_a_restarted_monitor_replays_the_run_then_follows_it(
    tmp_path: Path, no_oneharness: str
) -> None:
    """The keys are the sources' own stable names, never a counter of what this
    process has seen — so a restart continues instead of double-reporting."""
    run_dir = tmp_path / RUN
    first_round = open_journal(run_dir, RUN, 1)
    first_round.append("node-started", node=NodeId("api"))
    first_round.append("node-settled", node=NodeId("api"), detail={"status": "done"})
    _settle(run_dir, {"api": {"status": "done"}}, ok=True, state="complete")

    second_round = open_journal(run_dir, RUN, 2)
    second_round.append("node-started", node=NodeId("web"))

    original = _monitor(run_dir, no_oneharness).poll()
    assert [str(event.stream_id) for event in original] == [
        "graph:watch-me/1/api",
        "graph:watch-me/1/api",
        "graph:watch-me/2/web",
    ]

    # A monitor started fresh replays the same stream, across both rounds...
    restarted = _monitor(run_dir, no_oneharness)
    assert [event.key for event in restarted.poll()] == [event.key for event in original]

    # ...and then follows the run forward without re-reporting the replay.
    second_round.append("node-settled", node=NodeId("web"), detail={"status": "done"})
    assert [str(event.stream_id) for event in restarted.poll()] == ["graph:watch-me/2/web"]


def test_a_torn_tail_is_skipped_and_a_resumed_run_keeps_streaming(
    tmp_path: Path, no_oneharness: str
) -> None:
    """A crash mid-append leaves a half-written line. Reading never repairs, so the
    monitor skips it; the resumed writer reconciles it away and carries on."""
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append("node-started", node=NodeId("api"))
    path = run_dir / JOURNAL_NAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"version": 1, "seq": 2, "at": 1.0, "kind": "node-settl')

    monitor = _monitor(run_dir, no_oneharness)
    assert [event.kind for event in monitor.poll()] == ["node-started"]

    resumed = open_journal(run_dir, RUN, 1)  # reconciles the torn tail away
    resumed.append("node-settled", node=NodeId("api"), detail={"status": "done"})
    assert [event.kind for event in monitor.poll()] == ["node-settled"]
    assert path.read_text(encoding="utf-8").count("\n") == 2


# --- the persisted detail snapshot ---------------------------------------------


def test_a_saved_snapshot_round_trips_through_the_run_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN
    snapshot = DetailSnapshot(
        commits={
            "git:local/app@abc1234": CommitDetail(sha="abc1234", subject="feat: ship").to_record()
        },
        prs={"pr:acme/app#1": PrDetail(number=1, state="OPEN").to_record()},
    )
    save_snapshot(run_dir, snapshot)
    assert snapshot_path(run_dir) == run_dir / "monitor" / "details.json"
    assert load_snapshot(run_dir) == snapshot


@pytest.mark.parametrize(
    "written",
    [
        "{ not json at all",
        json.dumps({"version": 99, "commits": {"git:local/app@abc1234": {"sha": "abc1234"}}}),
        json.dumps({"commits": {}}),
    ],
)
def test_an_unreadable_or_future_snapshot_degrades_to_nothing_observed(
    tmp_path: Path, written: str
) -> None:
    """A snapshot is an optimization over observation, never evidence a decision
    rests on — so a corrupt one must not fail a monitor of a live run."""
    run_dir = tmp_path / RUN
    path = snapshot_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(written, encoding="utf-8")
    assert load_snapshot(run_dir) == DetailSnapshot()


def test_a_missing_snapshot_reads_as_empty(tmp_path: Path) -> None:
    assert load_snapshot(tmp_path / RUN) == DetailSnapshot()


def test_a_malformed_entry_is_dropped_without_taking_the_section_with_it(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / RUN
    path = snapshot_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": SNAPSHOT_VERSION,
                "commits": {"": {"sha": "x"}, "git:local/app@abc1234": {"sha": "abc1234"}},
                "prs": ["not a mapping"],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_snapshot(run_dir)
    assert loaded.commits == {"git:local/app@abc1234": CommitDetail(sha="abc1234").to_record()}
    assert loaded.prs == {}


# --- which run to watch --------------------------------------------------------


def test_the_newest_active_run_is_watched_when_none_is_named(tmp_path: Path) -> None:
    """ "Active" is a property of the run, not of a live process: a round waiting on a
    human has no executor at all and is the single most important thing to watch."""
    runs_dir = tmp_path / "runs"
    _settle(runs_dir / "done-run", {"api": {"status": "done"}}, ok=True, state="complete")
    older = _settle(
        runs_dir / "old-wait", {"api": {"status": "waiting"}}, ok=False, state="waiting"
    )
    newer = _settle(
        runs_dir / "new-wait", {"api": {"status": "waiting"}}, ok=False, state="waiting"
    )
    os.utime(older, (AT, AT))
    os.utime(newer, (AT + 60, AT + 60))

    assert active_runs(runs_dir) == ["new-wait", "old-wait"]
    assert resolve_run(runs_dir, None) == "new-wait"


def test_with_nothing_active_the_newest_run_of_any_state_is_replayed(tmp_path: Path) -> None:
    """A finished run is still worth replaying; only "no runs at all" is an error."""
    runs_dir = tmp_path / "runs"
    _settle(runs_dir / "done-run", {"api": {"status": "done"}}, ok=True, state="complete")
    assert active_runs(runs_dir) == []
    assert resolve_run(runs_dir, None) == "done-run"


def test_an_explicitly_named_run_beats_recency(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _settle(runs_dir / "done-run", {"api": {"status": "done"}}, ok=True, state="complete")
    _settle(runs_dir / "new-wait", {"api": {"status": "waiting"}}, ok=False, state="waiting")
    assert resolve_run(runs_dir, "done-run") == "done-run"


def test_a_run_that_cannot_be_watched_is_reported_actionably(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _settle(runs_dir / "done-run", {"api": {"status": "done"}}, ok=True, state="complete")
    with pytest.raises(MonitorError, match="no recorded run 'never-ran'"):
        resolve_run(runs_dir, "never-ran")
    with pytest.raises(MonitorError, match="letters, numbers"):
        resolve_run(runs_dir, "../escape")
    with pytest.raises(MonitorError, match="pass --runs-dir"):
        resolve_run(tmp_path / "elsewhere", None)


def test_a_run_directory_that_is_not_a_run_is_ignored_rather_than_fatal(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _settle(runs_dir / "done-run", {"api": {"status": "done"}}, ok=True, state="complete")
    (runs_dir / "not-a-run").mkdir()
    (runs_dir / "stray-file.txt").write_text("ignore me", encoding="utf-8")
    assert active_runs(runs_dir) == []
    assert resolve_run(runs_dir, None) == "done-run"


# --- what the run is doing -----------------------------------------------------


def test_a_round_in_progress_reports_running_until_its_executor_is_gone(tmp_path: Path) -> None:
    """Conservative in the direction that keeps the stream open: wrongly reporting
    "the executor stopped" is worse than heartbeating at a run that is merely quiet."""
    run_dir = tmp_path / RUN
    _, round_dir = prepare_round(run_dir, PLAN)  # records this live process as the owner

    live = run_state(run_dir, RUN)
    assert (live.state, live.executor_live, live.finished) == ("running", True, False)
    assert live.detail == "round in progress"

    (round_dir / "status.json").write_text(
        json.dumps(
            {"status": "running", "pid": os.getpid() + 10_000_000, "host": socket.gethostname()}
        ),
        encoding="utf-8",
    )
    gone = run_state(run_dir, RUN)
    assert (gone.state, gone.executor_live, gone.finished) == ("stopped", False, False)
    assert gone.detail == "executor stopped without recording a result"


def test_executor_status_corruption_is_interpreted_conservatively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / RUN
    _, round_dir = prepare_round(run_dir, PLAN)
    status = round_dir / "status.json"

    status.unlink()
    assert run_state(run_dir, RUN).state == "stopped"

    status.write_text("{", encoding="utf-8")
    assert run_state(run_dir, RUN).state == "running"

    status.write_text(json.dumps({"status": "complete"}), encoding="utf-8")
    assert run_state(run_dir, RUN).state == "stopped"

    status.write_text(json.dumps({"status": "running", "pid": False}), encoding="utf-8")
    assert run_state(run_dir, RUN).state == "running"

    status.write_text(
        json.dumps({"status": "running", "pid": os.getpid(), "host": "another-host"}),
        encoding="utf-8",
    )
    assert run_state(run_dir, RUN).state == "running"

    status.write_text(
        json.dumps({"status": "running", "pid": os.getpid(), "host": socket.gethostname()}),
        encoding="utf-8",
    )

    def permission_denied(_pid: int, _signal: int) -> None:
        raise PermissionError

    # The OS liveness probe itself lives in `runs`: the monitor, the run views, and
    # the recovery gate all ask that one function whether an owner still exists.
    monkeypatch.setattr(runs_module.os, "kill", permission_denied)
    assert run_state(run_dir, RUN).state == "running"


def test_a_parked_launch_is_reported_before_anything_it_last_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live pid is ownership, not progress: a parked launch outranks the stale
    surface and the stale `running` round status it left behind."""
    run_dir = tmp_path / RUN
    _, round_dir = prepare_round(run_dir, PLAN)  # records this live process as the owner
    (run_dir / "orchestrator").mkdir()
    (run_dir / "launch.json").write_text("{}", encoding="utf-8")
    (run_dir / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": os.getpid(), "host": socket.gethostname()}),
        encoding="utf-8",
    )
    (run_dir / "channel").mkdir()
    (run_dir / "channel" / "planner-pending.json").write_text(
        json.dumps({"kind": "blocker", "message": "answer me", "blocking": True}),
        encoding="utf-8",
    )
    stale = time.time() - 600
    for path in (
        run_dir / "orchestrator" / "status.json",
        round_dir / "status.json",
        round_dir / "plan.json",
    ):
        os.utime(path, (stale, stale))
    monkeypatch.setattr("orchestrator.monitor.parked_indicator", lambda run, *, parked_after: None)
    assert run_state(run_dir, RUN).state == "blocked"
    monkeypatch.undo()
    monkeypatch.setattr("orchestrator.liveness._parent_map", lambda: {})
    parked = run_state(run_dir, RUN, parked_after=60)
    assert (parked.state, parked.executor_live, parked.round) == ("parked", False, 1)
    assert "no child process" in parked.detail
    assert run_state(run_dir, RUN, parked_after=100_000).state == "blocked"


def test_a_run_with_no_recorded_rounds_has_nothing_to_report_yet(tmp_path: Path) -> None:
    state = run_state(tmp_path / RUN, RUN)
    assert (state.round, state.state, state.finished) == (None, "unknown", False)


def test_non_boolean_persisted_pending_state_is_ignored(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN
    prepare_round(run_dir, PLAN)
    pending = run_dir / "channel" / "planner-pending.json"
    pending.parent.mkdir()
    pending.write_text(
        json.dumps({"kind": "proposal", "message": "review", "blocking": "false"}),
        encoding="utf-8",
    )

    state = run_state(run_dir, RUN)
    assert state.state == "running"
    assert state.detail == "round in progress"


def test_the_state_line_counts_a_park_among_the_statuses_still_to_act_on(
    tmp_path: Path,
) -> None:
    """A park is tallied with the held statuses, ahead of the ones that are over.

    The state line renders over one declared order, and a status missing from it is
    still counted — but at the end, beside the failures. That reads a park as
    something that went wrong, when it is a node the planner idled whose work is
    preserved and whose release is its own `requeue`. The order is the claim here,
    so the round holds a done, a parked, and a failed node at once.
    """
    run_dir = tmp_path / RUN
    _settle(
        run_dir,
        {
            "hold": {"status": "done"},
            "sweep": {"status": "parked", "branch": "feature/sweep"},
            "probe": {"status": "failed"},
        },
        ok=False,
        state="waiting",
    )

    state = run_state(run_dir, RUN)

    assert state.state == "waiting"
    assert state.detail == "1 done, 1 parked, 1 failed"
    # And a round holding a park is not a run anything may stop watching.
    assert not state.finished


def test_only_a_complete_and_ok_result_counts_as_finished(tmp_path: Path) -> None:
    """`ok` and the state are recorded separately, and both must agree."""
    run_dir = tmp_path / RUN
    _settle(run_dir, {"api": {"status": "done"}}, ok=False, state="complete")
    assert not run_state(run_dir, RUN).finished


# --- the exit contract ---------------------------------------------------------


def test_only_a_successful_graph_ends_the_stream(tmp_path: Path, no_oneharness: str) -> None:
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append(
        "node-settled", node=NodeId("api"), detail={"status": "done", "ok": True}
    )
    _settle(run_dir, {"api": {"status": "done"}}, ok=True, state="complete")

    ticker = _Ticker(stop_after=1)
    out = io.StringIO()
    code = stream(
        _monitor(run_dir, no_oneharness, clock=ticker.clock),
        Writer("jsonl", out),
        heartbeat=10.0,
        poll_interval=10.0,
        sleep=ticker.sleep,
    )
    assert code == 0
    assert ticker.slept == 0  # it never waited: the graph was already complete
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines[0]["summary"] == "node-settled status=done ok=yes"
    assert lines[-1] == {
        "type": "heartbeat",
        "at": AT,
        "run_id": "watch-me",
        "round": 1,
        "state": "complete",
        "detail": "graph complete",
        "next_poll_seconds": 10.0,
    }


def test_unchanged_monitor_polls_back_off_to_the_bounded_maximum(
    tmp_path: Path, no_oneharness: str
) -> None:
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append("node-started", node=NodeId("api"))
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")
    ticker = _Ticker(stop_after=4)
    intervals: list[float] = []

    def sleep(seconds: float) -> None:
        intervals.append(seconds)
        ticker.sleep(seconds)

    with pytest.raises(KeyboardInterrupt):
        stream(
            _monitor(run_dir, no_oneharness, clock=ticker.clock),
            Writer("jsonl", io.StringIO()),
            heartbeat=100.0,
            poll_interval=2.0,
            max_poll_interval=5.0,
            sleep=sleep,
        )
    assert intervals == [2.0, 4.0, 5.0, 5.0]


@pytest.mark.parametrize(
    ("results", "state", "expected_detail"),
    [
        (
            {"signoff": {"status": "waiting"}, "api": {"status": "blocked"}},
            "waiting",
            "1 waiting, 1 blocked",
        ),
        (
            {"api": {"status": "failed"}, "web": {"status": "skipped"}},
            "failed",
            "1 failed, 1 skipped",
        ),
    ],
)
def test_a_state_a_person_must_act_on_keeps_heartbeating(
    tmp_path: Path,
    no_oneharness: str,
    results: dict[str, Any],
    state: str,
    expected_detail: str,
) -> None:
    """Each of these continues through next-round, whose new round directory the next
    poll picks up. Exiting would report "finished" for a run that is merely stuck."""
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append("node-started", node=NodeId("api"))
    _settle(run_dir, results, ok=False, state=state)

    ticker = _Ticker(stop_after=3)
    out = io.StringIO()
    with pytest.raises(KeyboardInterrupt):
        stream(
            _monitor(run_dir, no_oneharness, clock=ticker.clock),
            Writer("jsonl", out),
            heartbeat=10.0,
            poll_interval=10.0,
            sleep=ticker.sleep,
        )
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["type"] for line in lines] == ["event", "heartbeat", "heartbeat"]
    assert [line["state"] for line in lines if line["type"] == "heartbeat"] == [state, state]
    assert lines[-1]["detail"] == expected_detail


def test_an_executor_that_died_without_a_result_keeps_heartbeating(
    tmp_path: Path, no_oneharness: str
) -> None:
    """Someone must notice and recover it; the run then continues."""
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append("node-started", node=NodeId("api"))
    _, round_dir = prepare_round(run_dir, PLAN)
    (round_dir / "status.json").write_text(
        json.dumps(
            {"status": "running", "pid": os.getpid() + 10_000_000, "host": socket.gethostname()}
        ),
        encoding="utf-8",
    )

    ticker = _Ticker(stop_after=3)
    out = io.StringIO()
    with pytest.raises(KeyboardInterrupt):
        stream(
            _monitor(run_dir, no_oneharness, clock=ticker.clock),
            Writer("jsonl", out),
            heartbeat=10.0,
            poll_interval=10.0,
            sleep=ticker.sleep,
        )
    beats = [json.loads(line) for line in out.getvalue().splitlines() if '"heartbeat"' in line]
    assert [beat["state"] for beat in beats] == ["stopped", "stopped"]
    assert beats[-1]["detail"] == "executor stopped without recording a result"


def test_a_fresh_event_resets_the_silence_the_heartbeat_measures(
    tmp_path: Path, no_oneharness: str
) -> None:
    """The heartbeat reports *silence*, so a run producing events does not need one."""
    run_dir = tmp_path / RUN
    journal = open_journal(run_dir, RUN, 1)
    journal.append("node-started", node=NodeId("api"))
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")

    class _ChattyTicker(_Ticker):
        """An executor that appends a transition between every one of the poller's passes."""

        def sleep(self, seconds: float) -> None:
            journal.append("node-started", node=NodeId(f"node{self.slept}"))
            super().sleep(seconds)

    chatty = _ChattyTicker(stop_after=2)
    out = io.StringIO()
    with pytest.raises(KeyboardInterrupt):
        stream(
            _monitor(run_dir, no_oneharness, clock=chatty.clock),
            Writer("jsonl", out),
            heartbeat=10.0,
            poll_interval=10.0,
            sleep=chatty.sleep,
        )
    # Two full heartbeat intervals of wall clock passed, and neither earned a beat.
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["type"] for line in lines] == ["event", "event"]
    assert chatty.now == AT + 20.0


def test_once_replays_reports_state_and_exits_even_for_a_run_still_going(
    tmp_path: Path, no_oneharness: str
) -> None:
    """--once is the replay-and-report mode: exit 0 says "here is where it stands",
    where the follow mode's exit 0 would claim the graph had completed."""
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append("node-started", node=NodeId("api"))
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")

    ticker = _Ticker(stop_after=1)
    out = io.StringIO()
    code = stream(
        _monitor(run_dir, no_oneharness, clock=ticker.clock),
        Writer("text", out),
        once=True,
        sleep=ticker.sleep,
    )
    assert code == 0
    assert ticker.slept == 0
    lines = out.getvalue().splitlines()
    assert lines[0] == HEADER
    # The event is stamped when the journal recorded it; the heartbeat, now.
    assert lines[1].endswith("  graph:watch-me/1/api  node-started")
    assert lines[-1] == "12:00:00  --  watch-me round-01 waiting: 1 waiting"


def test_an_unreadable_result_is_reported_rather_than_crashing_the_stream(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / RUN
    _, round_dir = prepare_round(run_dir, PLAN)
    (round_dir / "result.json").write_text(json.dumps({"ok": "not-a-bool"}), encoding="utf-8")
    state = run_state(run_dir, RUN)
    assert state.state == "unknown"
    assert "unreadable result" in state.detail
    assert not state.finished


def test_invalid_run_directories_are_skipped_after_their_ledgers_are_found(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    prepare_round(runs_dir / "bad run name", PLAN)
    assert active_runs(runs_dir) == []
    assert monitor_module.newest_run(runs_dir) is None


def test_a_corrupt_registry_degrades_monitor_checkout_discovery_to_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_oneharness: str
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "repos.json").write_text("{", encoding="utf-8")
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(state_dir))

    monitor = _monitor(tmp_path / RUN, no_oneharness)
    assert monitor.checkouts() == {}
    assert monitor.checkouts() == {}  # the degraded result is cached for later polls


@pytest.mark.parametrize("output_format", ["text", "jsonl"])
def test_the_public_monitor_entrypoint_replays_a_complete_run_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    output_format: str,
) -> None:
    """Exercise the installed command's Python boundary without losing coverage to
    the subprocess used by the e2e acceptance test."""
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / RUN
    open_journal(run_dir, RUN, 1).append(
        "node-settled", node=NodeId("api"), detail={"status": "done", "ok": True}
    )
    _settle(run_dir, {"api": {"status": "done"}}, ok=True, state="complete")
    monkeypatch.setenv("PATH", str(tmp_path / "no-tools"))

    assert (
        monitor_main(["--once", "--runs-dir", str(runs_dir), "--format", output_format, str(RUN)])
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    if output_format == "text":
        assert lines[0] == HEADER
        assert lines[1].endswith("graph:watch-me/1/api  node-settled status=done ok=yes")
        # A completed graph reads the same whichever mode read it: the detail is
        # about the run, not about how it was being watched.
        assert lines[-1].endswith("watch-me round-01 complete: graph complete")
    else:
        records = [json.loads(line) for line in lines]
        assert records[0]["id"] == "graph:watch-me/1/api"
        assert records[-1]["type"] == "heartbeat"
        assert records[-1]["state"] == "complete"
    assert snapshot_path(run_dir).is_file()


def test_the_public_monitor_entrypoint_reports_resolution_and_argument_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    assert monitor_main(["--once", "--runs-dir", str(runs_dir), "missing"]) == 2
    missing = capsys.readouterr()
    assert "no recorded run 'missing'" in missing.err
    assert "Traceback" not in missing.err

    with pytest.raises(SystemExit) as invalid:
        monitor_main(["--heartbeat", "0", "--runs-dir", str(runs_dir)])
    assert invalid.value.code == 2
    assert "--heartbeat must be a positive number of seconds" in capsys.readouterr().err

    with pytest.raises(SystemExit) as invalid_timeout:
        monitor_main(["--source-timeout", "0", "--runs-dir", str(runs_dir)])
    assert invalid_timeout.value.code == 2
    assert "--source-timeout must be a positive number of seconds" in capsys.readouterr().err

    with pytest.raises(SystemExit) as contradictory:
        monitor_main(["--once", "--follow", "--runs-dir", str(runs_dir)])
    assert contradictory.value.code == 2
    assert "--once and --follow ask for opposite things" in capsys.readouterr().err


class _Watched:
    """A stream that reports whether a person is watching it."""

    def __init__(self, terminal: bool) -> None:
        self._terminal = terminal

    def isatty(self) -> bool:
        return self._terminal


def _closed() -> io.StringIO:
    """A stream already closed: `isatty` raises rather than answering."""
    stream = io.StringIO()
    stream.close()
    return stream


@pytest.mark.parametrize(
    ("out", "follow", "expected"),
    [
        (_Watched(True), False, True),
        (_Watched(False), False, False),
        (_Watched(False), True, True),
        (io.StringIO(), False, False),
        (_closed(), False, False),
    ],
)
def test_only_a_watcher_that_can_see_the_stream_follows_it(
    out: Watchable, follow: bool, expected: bool
) -> None:
    """Following is a terminal affordance. Off one — a pipe, a file, a captured
    planner invocation — the caller sees nothing until the process exits, and the
    exit contract says only a successful graph ever exits. So the command
    `launch.json` advertises makes one bounded pass instead, unless asked otherwise."""
    assert follows(out, follow=follow) is expected


def test_the_public_monitor_entrypoint_treats_ctrl_c_as_a_clean_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir = tmp_path / "runs"
    _settle(runs_dir / RUN, {"api": {"status": "waiting"}}, ok=False, state="waiting")

    def interrupted(*_args: Any, **_kwargs: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(monitor_module, "stream", interrupted)
    assert monitor_main(["--runs-dir", str(runs_dir), str(RUN)]) == 0


def _launched(run_dir: Path, pid: int, *, reported: bool = False) -> None:
    """Record a launch the way `just orchestrate` leaves one, owned by ``pid``."""
    (run_dir / "orchestrator").mkdir(parents=True, exist_ok=True)
    (run_dir / "launch.json").write_text(
        json.dumps({"schema_version": 2, "run_id": run_dir.name}), encoding="utf-8"
    )
    (run_dir / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": pid, "host": socket.gethostname()}),
        encoding="utf-8",
    )
    (run_dir / "orchestrator" / "report.json").write_text(
        '{"ok": true}' if reported else "", encoding="utf-8"
    )


def _pending(run_dir: Path, *, blocking: bool) -> None:
    channel = run_dir / "channel"
    channel.mkdir(parents=True, exist_ok=True)
    (channel / "planner-pending.json").write_text(
        json.dumps({"kind": "blocker", "message": "decide", "blocking": blocking}),
        encoding="utf-8",
    )


def test_a_launch_that_still_claims_a_live_owner_is_never_settled(tmp_path: Path) -> None:
    """Between rounds is the case a "the round finished" reading would return on.

    The round has a result, nothing is pending, and the orchestrator is alive and
    about to open round two. Anything that called this settled would hand the
    planner back a graph that is still executing.
    """
    run_dir = tmp_path / RUN
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")
    _launched(run_dir, os.getpid())

    assert monitor_module.nothing_is_driving(run_dir) is False
    assert run_state(run_dir, RUN).settlement is None
    assert run_state(run_dir, RUN).settled is False


def test_a_launch_that_has_written_its_report_is_settled_unattended(tmp_path: Path) -> None:
    """A report is the launch saying it is done; nothing will record another round."""
    run_dir = tmp_path / RUN
    _settle(run_dir, {"api": {"status": "failed"}}, ok=False, state="failed")
    _launched(run_dir, os.getpid(), reported=True)

    state = run_state(run_dir, RUN)
    assert state.settlement == monitor_module.SETTLED_UNATTENDED
    assert monitor_module.settled_detail(state).startswith("settled, nothing is driving this run")


def test_a_run_nothing_ever_launched_is_not_reported_as_abandoned(tmp_path: Path) -> None:
    """No launch record and no round is a run that has not started, not one that died."""
    run_dir = tmp_path / RUN
    run_dir.mkdir(parents=True)
    assert monitor_module.nothing_is_driving(run_dir) is False
    assert run_state(run_dir, RUN).settlement is None


def test_a_launch_that_died_before_its_first_round_is_settled_unattended(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN
    run_dir.mkdir(parents=True)
    _launched(run_dir, os.getpid(), reported=True)
    assert monitor_module.nothing_is_driving(run_dir) is True
    assert run_state(run_dir, RUN).settlement == monitor_module.SETTLED_UNATTENDED


def test_only_a_blocking_surface_settles_a_run_on_the_planner(tmp_path: Path) -> None:
    run_dir = tmp_path / RUN
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")
    _launched(run_dir, os.getpid())

    _pending(run_dir, blocking=False)
    informational = run_state(run_dir, RUN)
    assert informational.state == "blocked"
    assert informational.settlement is None

    _pending(run_dir, blocking=True)
    waiting = run_state(run_dir, RUN)
    assert waiting.settlement == monitor_module.SETTLED_AWAITING_PLANNER
    assert monitor_module.settled_detail(waiting) == (
        "settled, awaiting the planner: ACK REQUIRED: blocker: decide"
    )


def test_a_settle_terminating_follow_returns_with_the_settlements_own_status(
    tmp_path: Path, no_oneharness: str
) -> None:
    """The status is the contract for a caller that is not reading the stream."""
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append("node-started", node=NodeId("api"))
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")
    _launched(run_dir, os.getpid())
    _pending(run_dir, blocking=True)

    out = io.StringIO()
    ticker = _Ticker(stop_after=1)
    code = stream(
        _monitor(run_dir, no_oneharness, clock=ticker.clock),
        Writer("jsonl", out),
        until_settled=True,
        sleep=ticker.sleep,
    )
    assert code == 0
    assert ticker.slept == 0
    beat = json.loads(out.getvalue().splitlines()[-1])
    assert beat["settlement"] == "awaiting-planner"

    # The same run with nothing driving it: same command, non-zero status.
    _launched(run_dir, os.getpid(), reported=True)
    (run_dir / "channel" / "planner-pending.json").unlink()
    gone = io.StringIO()
    assert (
        stream(
            _monitor(run_dir, no_oneharness, clock=ticker.clock),
            Writer("jsonl", gone),
            until_settled=True,
            sleep=ticker.sleep,
        )
        == 3
    )
    assert json.loads(gone.getvalue().splitlines()[-1])["settlement"] == "unattended"


def test_a_follow_without_the_flag_keeps_every_ending_it_had(
    tmp_path: Path, no_oneharness: str
) -> None:
    """The settle mode widens the ending; it must not change the one that was there."""
    run_dir = tmp_path / RUN
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")
    _launched(run_dir, os.getpid(), reported=True)  # settled, but nobody asked

    ticker = _Ticker(stop_after=2)
    with pytest.raises(KeyboardInterrupt):
        stream(
            _monitor(run_dir, no_oneharness, clock=ticker.clock),
            Writer("jsonl", io.StringIO()),
            heartbeat=100.0,
            poll_interval=1.0,
            sleep=ticker.sleep,
        )


def test_the_settle_terminating_entrypoint_follows_off_a_terminal_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Off a terminal the default is one pass, because such a caller could neither
    observe nor end a follow. `--until-settled` ends itself, so it follows anyway —
    and that is the mode the foreground `just orchestrate` runs in."""
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / RUN
    _settle(run_dir, {"api": {"status": "done"}}, ok=True, state="complete")

    assert monitor_main(["--runs-dir", str(runs_dir), "--until-settled", str(RUN)]) == 0
    assert "graph complete" in capsys.readouterr().out

    with pytest.raises(SystemExit) as contradictory:
        monitor_main(["--once", "--until-settled", "--runs-dir", str(runs_dir)])
    assert contradictory.value.code == 2
    assert "--once and --until-settled ask for opposite things" in capsys.readouterr().err

    with pytest.raises(SystemExit) as unusable:
        monitor_main(["--parked-after", "nan", "--runs-dir", str(runs_dir)])
    assert unusable.value.code == 2
    assert "--parked-after must be a positive, finite number of seconds" in capsys.readouterr().err


def test_the_attach_entrypoint_is_the_one_both_commands_stream_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_oneharness: str
) -> None:
    """`just monitor` and the foreground `just orchestrate` share this, deliberately.

    A second renderer would be a second answer to "what is happening right now", and
    the two would drift on the first source that changed — so the launch path calls
    exactly this, with `until_settled` set.
    """
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / RUN
    open_journal(run_dir, RUN, 1).append("node-settled", node=NodeId("api"))
    _settle(run_dir, {"api": {"status": "done"}}, ok=True, state="complete")

    assert (
        monitor_module.attach(
            RUN, runs_dir=runs_dir, until_settled=True, oneharness_bin=no_oneharness
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert printed.splitlines()[0] == HEADER
    assert "graph complete" in printed


def test_an_attach_a_person_ends_is_a_clean_stop_not_a_stopped_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_oneharness: str
) -> None:
    """Ctrl-C ends the attachment; the run leads its own session and keeps working."""
    runs_dir = tmp_path / "runs"
    _settle(runs_dir / RUN, {"api": {"status": "waiting"}}, ok=False, state="waiting")

    def interrupted(*_args: Any, **_kwargs: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(monitor_module, "stream", interrupted)
    assert (
        monitor_module.attach(
            RUN, runs_dir=runs_dir, until_settled=True, oneharness_bin=no_oneharness
        )
        == 0
    )


def test_an_unsettled_state_renders_its_own_detail(tmp_path: Path) -> None:
    """`settled_detail` describes a settlement; without one it changes nothing."""
    run_dir = tmp_path / RUN
    _settle(run_dir, {"api": {"status": "waiting"}}, ok=False, state="waiting")
    _launched(run_dir, os.getpid())
    state = run_state(run_dir, RUN)
    assert state.settlement is None
    assert monitor_module.settled_detail(state) == state.detail == "1 waiting"


def test_every_settlement_and_the_status_it_reports_are_pinned_here() -> None:
    """The one place these values are asserted, and the gate against them drifting.

    A settlement's exit status is a contract a scripted launch reads without
    parsing the stream: zero for the two endings a planner expects to reach, and
    non-zero for the one that means intervene. `tests/e2e/test_attach_settles_e2e.py`
    reads the mapping rather than restating a number, so this assertion is what
    would fail if one moved.
    """
    assert monitor_module.SETTLEMENTS == {
        "complete": 0,
        "awaiting-planner": 0,
        "unattended": 3,
    }
    assert monitor_module.SETTLED_COMPLETE == "complete"
    assert monitor_module.SETTLED_AWAITING_PLANNER == "awaiting-planner"
    assert monitor_module.SETTLED_UNATTENDED == "unattended"
