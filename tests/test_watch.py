"""Rendering and exit contract for `just watch`.

The real journey — a launched orchestrator, a surface consumed off the live FIFO,
and a run settling under a watching process — runs end to end in
tests/e2e/test_channel_e2e.py. These drive the same functions in process, over the
real ledger and journal writers, for the branches a single live run cannot reach in
one pass: a channel that cannot be read, a run that stops without completing, and
the argument boundary.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from orchestrator.channel import ChannelError
from orchestrator.journal import open_journal
from orchestrator.monitor import Monitor
from orchestrator.runs import NodeId, RunId, prepare_round, write_result
from orchestrator.watch import EXIT_COMPLETED, EXIT_STOPPED, main, surface_lines, watch

RUN = RunId("watched-run")
PLAN: dict[str, Any] = {
    "concurrency": 1,
    "tasks": [{"id": "api", "persona": "engineer", "task": "ship it"}],
}


@pytest.fixture
def no_oneharness(tmp_path: Path) -> str:
    """A oneharness that is not installed — the history source degrades to silence."""
    return str(tmp_path / "absent" / "oneharness")


def _settle(run_dir: Path, *, ok: bool, state: str) -> None:
    _, round_dir = prepare_round(run_dir, PLAN)
    write_result(
        round_dir,
        {
            "ok": ok,
            "state": state,
            "started_order": ["api"],
            "results": {"api": {"status": "done" if ok else "failed"}},
        },
    )


def _reader(*values: Mapping[str, Any] | Exception) -> Callable[[], Mapping[str, Any]]:
    """One scripted channel: each call answers the next queued frame, then idles."""
    remaining = list(values)

    def read() -> Mapping[str, Any]:
        if not remaining:
            return {"status": "running", "surface": None}
        value = remaining.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    return read


def _watch(
    run_dir: Path, oneharness_bin: str, read: Callable[[], Mapping[str, Any]]
) -> tuple[int, str]:
    out = io.StringIO()
    code = watch(
        Monitor(run_id=RUN, run_dir=run_dir, oneharness_bin=oneharness_bin),
        read_surface=read,
        out=out,
        runs_dir=run_dir.parent,
        poll_interval=0.01,
        sleep=lambda _seconds: None,
    )
    return code, out.getvalue()


def test_a_watched_surface_names_the_reply_command_and_a_complete_graph_ends_the_watch(
    tmp_path: Path, no_oneharness: str
) -> None:
    run_dir = tmp_path / RUN
    open_journal(run_dir, RUN, 1).append(
        "node-settled", node=NodeId("api"), detail={"status": "done", "ok": True}
    )
    _settle(run_dir, ok=True, state="complete")
    code, shown = _watch(
        run_dir,
        no_oneharness,
        _reader(
            {
                "op": "supervisor",
                "run_id": RUN,
                "round": 1,
                "surface": {"kind": "milestone", "message": "round-01 complete: 1 done"},
            }
        ),
    )
    assert code == EXIT_COMPLETED
    assert f"Watching {RUN}" in shown
    assert "milestone (reply required)" in shown
    assert "    round-01 complete: 1 done" in shown
    assert f"    Reply with: just channel-reply {RUN} --runs-dir {tmp_path}" in shown
    # The node transition is the other half of what replaces composing two commands.
    assert f"graph:{RUN}/1/api" in shown
    assert f"{RUN} settled: graph complete" in shown


def test_an_unreadable_channel_degrades_to_transitions_and_a_failed_run_exits_nonzero(
    tmp_path: Path, no_oneharness: str
) -> None:
    """Neither half of the watch may take the other down with it."""
    run_dir = tmp_path / RUN
    _settle(run_dir, ok=False, state="failed")
    code, shown = _watch(
        run_dir,
        no_oneharness,
        _reader(ChannelError("up.fifo is missing"), {"status": "finished"}),
    )
    assert code == EXIT_STOPPED
    assert "channel  unavailable: up.fifo is missing" in shown
    assert f"{RUN} settled without completing: failed" in shown


def test_a_run_whose_round_was_abandoned_ends_the_watch_with_the_reclaiming_command(
    tmp_path: Path, no_oneharness: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / RUN
    prepare_round(run_dir, PLAN)
    monkeypatch.setattr("orchestrator.runs.process_may_be_live", lambda *_args: False)
    code, shown = _watch(run_dir, no_oneharness, _reader())
    assert code == EXIT_STOPPED
    assert "round-01 ABANDONED" in shown
    assert "--recover" in shown


class _Ticker:
    """A virtual clock whose reads advance time, then end the watch like Ctrl-C."""

    def __init__(self, *, stop_after: int, step: float) -> None:
        self.now = 1_000.0
        self.reads = 0
        self._stop_after = stop_after
        self._step = step

    def clock(self) -> float:
        return self.now

    def read(self) -> Mapping[str, Any]:
        self.reads += 1
        if self.reads > self._stop_after:
            raise KeyboardInterrupt
        self.now += self._step
        return {"status": "running", "surface": None}


def test_a_quiet_run_still_reports_its_state_once_per_silent_interval(
    tmp_path: Path, no_oneharness: str
) -> None:
    """Silence is what a planner misreads, so a working run says so periodically."""
    run_dir = tmp_path / RUN
    prepare_round(run_dir, PLAN)
    ticker = _Ticker(stop_after=8, step=25.0)
    out = io.StringIO()
    with pytest.raises(KeyboardInterrupt):
        watch(
            Monitor(run_id=RUN, run_dir=run_dir, oneharness_bin=no_oneharness, clock=ticker.clock),
            read_surface=ticker.read,
            out=out,
            runs_dir=tmp_path,
            poll_interval=0.01,
            heartbeat=60.0,
            sleep=lambda _seconds: None,
        )
    beats = [line for line in out.getvalue().splitlines() if " running: round in progress" in line]
    assert len(beats) == 2, out.getvalue()
    assert f"{RUN} round-01 running" in beats[0]


def test_a_frame_without_a_surface_renders_nothing_and_options_are_listed() -> None:
    assert (
        surface_lines(
            {"status": "running", "surface": None}, run_id="r", runs_dir=Path("runs"), at=0.0
        )
        == []
    )
    assert (
        surface_lines(
            {"surface": {"kind": 1, "message": "x"}}, run_id="r", runs_dir=Path("runs"), at=0.0
        )
        == []
    )
    lines = surface_lines(
        {
            "surface": {
                "kind": "choice",
                "message": "retry X?\x1b[31mor drop it",
                "blocking": True,
                "options": ["retry X", "drop X"],
            }
        },
        run_id="r",
        runs_dir=Path("runs"),
        at=0.0,
    )
    assert lines[0].endswith("surface  choice (reply required)")
    # An escape sequence in agent-authored text is shown, never acted on.
    assert lines[1] == "    retry X? [31mor drop it"
    assert lines[2:4] == ["    option: retry X", "    option: drop X"]
    assert lines[-1] == "    Reply with: just channel-reply r --runs-dir runs"
    heartbeat = surface_lines(
        {"surface": {"kind": "heartbeat", "message": "still verifying", "blocking": False}},
        run_id="r",
        runs_dir=Path("runs"),
        at=0.0,
    )
    assert heartbeat[0].endswith("surface  heartbeat (no reply needed)")
    assert not any("channel-reply" in line for line in heartbeat)


def test_the_command_boundary_rejects_bad_input_and_ends_cleanly_on_interrupt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit) as rejected:
        main(["--runs-dir", str(tmp_path), "--poll-interval", "0"])
    assert rejected.value.code == 2
    assert main(["missing-run", "--runs-dir", str(tmp_path)]) == 2
    assert "watch: no recorded run" in capsys.readouterr().err

    run_dir = tmp_path / RUN
    _settle(run_dir, ok=True, state="complete")
    assert main([str(RUN), "--runs-dir", str(tmp_path)]) == EXIT_COMPLETED
    assert f"{RUN} settled: graph complete" in capsys.readouterr().out

    def interrupted(*_args: object, **_kwargs: object) -> Mapping[str, Any]:
        raise KeyboardInterrupt

    monkeypatch.setattr("orchestrator.watch.next_surface", interrupted)
    prepare_round(run_dir, PLAN)
    assert main([str(RUN), "--runs-dir", str(tmp_path)]) == EXIT_COMPLETED
