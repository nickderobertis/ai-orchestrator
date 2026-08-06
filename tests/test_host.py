"""Focused tests for the whole-host view, driven against real stamped processes.

Every row this view prints has to be *provable*: a live process carrying a dispatch's
ownership stamp, under a scratch directory a dispatcher still holds. So the fixtures
here are the production ones — `owned_scratch_directory` takes the real owner lock,
the observed process is a real child of this one carrying the real stamp and the real
history labels, and the entry point exercised is the command's own ``main``.

What is deliberately not arranged is a paid harness: the child is a plain process, so
the turn it is serving is named by the dispatch's own ``agent_role`` label and its
harness identity is unknown, exactly as it is for any dispatch whose provider this
host cannot identify. Which *credential directory* names which identity is the real
oneharness journey's claim (`tests/e2e/test_live_dispatch_views_e2e.py`); what this
covers is the view built on top of it, including the two answers it must never
confuse — nothing is running, and nothing is knowable.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from orchestrator.coordination import PROC_ROOT_ENV
from orchestrator.dispatches import live_dispatches
from orchestrator.host import main
from orchestrator.launch import (
    LAUNCH_RECORD_NAME,
    LaunchSession,
    generate_launch_id,
    launch_info,
    session_key,
)
from orchestrator.scratch import AGENT_STATUS_DIR_NAME, owned_scratch_directory

#: A child that simply stays alive: everything this view reads about it comes from
#: the kernel (its environment, its state, its start time), so it has nothing to do.
_IDLE = "import time; time.sleep(600)"


@pytest.fixture
def scratch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point this process's `tempfile` at a private root, as a dispatcher's TMPDIR is.

    A dispatch and the view reading it have to agree on one scratch root. Giving this
    test its own is what keeps it from seeing — or being seen by — the real dispatches
    running beside it on this host.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture
def dispatched() -> Iterator[ExitStack]:
    """Own every scratch directory and child process one test starts, and release them."""
    with ExitStack() as stack:
        yield stack


def _dispatch(stack: ExitStack, *, labels: str) -> Path:
    """Start one stamped process under an owned scratch directory, as a dispatch does."""
    status_dir = stack.enter_context(owned_scratch_directory()) / AGENT_STATUS_DIR_NAME
    status_dir.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-c", _IDLE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin",
            "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
            "ONEHARNESS_HISTORY_LABELS": labels,
        },
        start_new_session=True,
    )
    # Pushed after the directory it is stamped for, so the process is killed before the
    # ownership lock is released: a released directory with a live child still carrying
    # its stamp is the one state this view is not meant to see.
    stack.callback(_end, process)
    return status_dir


def _end(process: subprocess.Popen[bytes]) -> None:
    process.kill()
    process.wait(timeout=30)


def _await_live(scratch_root: Path, node: str) -> None:
    """Wait until the ownership registry can see this dispatch, or fail saying so."""
    limit = time.monotonic() + 60
    while time.monotonic() < limit:
        if any(dispatch.node == node for dispatch in live_dispatches(root=scratch_root) or []):
            return
        time.sleep(0.05)
    raise AssertionError(f"no live dispatch for node {node!r} ever became observable")


def test_a_live_dispatch_is_named_by_its_place_in_a_graph_and_its_owning_session(
    scratch_root: Path,
    tmp_path: Path,
    dispatched: ExitStack,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One row per dispatch: where it sits, whose it is, and the load it contributes.

    The session is read from the *run's* own launch record rather than from anything
    the dispatch stamped, because that is the record `just runs` attributes ownership
    by — a view that answered differently would tell two planners sharing this host
    two different stories about the same run.
    """
    runs = tmp_path / "runs"
    run_dir = runs / "run-42"
    run_dir.mkdir(parents=True)
    (run_dir / LAUNCH_RECORD_NAME).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "run_id": run_dir.name,
                "launch": launch_info(
                    launch_id=generate_launch_id(), launcher="claude-code", session_id="s-host"
                ),
            }
        ),
        encoding="utf-8",
    )
    _dispatch(
        dispatched,
        labels="run_id=run-42,round=1,node=api,step=implement,persona=engineer,agent_role=worker",
    )
    _await_live(scratch_root, "api")

    assert (
        main(
            [
                "--format",
                "json",
                "--runs-dir",
                str(runs),
                "--scratch-root",
                str(scratch_root),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["observable"] is True
    (row,) = payload["dispatches"]
    assert row["run_id"] == "run-42"
    assert row["round"] == "1"
    assert row["node"] == "api"
    assert row["step"] == "implement"
    assert row["persona"] == "engineer"
    assert row["role"] == "worker"
    assert row["session"] == LaunchSession("claude-code", session_key("s-host")).label
    assert row["processes"] == 1
    assert row["turn_age_seconds"] is not None
    # A turn that just started is ordinary, and nothing about it is flagged.
    assert row["turn_is_outlier"] is False


def test_the_human_rendering_names_the_turn_the_load_and_the_status_directory(
    scratch_root: Path,
    tmp_path: Path,
    dispatched: ExitStack,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What an operator reads: the graph locator, the turn, and the load it is causing.

    The status directory is on the row because it is the *proof* — it is what makes
    this dispatch attributable at all, and it is what a planner hands to the scratch
    tooling when a dispatch has to be investigated.
    """
    status_dir = _dispatch(dispatched, labels="run_id=run-9,round=2,node=web,agent_role=judge")
    _await_live(scratch_root, "web")

    assert main(["--runs-dir", str(tmp_path / "runs"), "--scratch-root", str(scratch_root)]) == 0
    rendered = capsys.readouterr().out

    assert "Load: " in rendered
    assert "Live dispatches: 1, contributing" in rendered
    assert "run-9 round-02 web  [unknown]" in rendered
    assert "judge on harness unknown, turn running " in rendered
    assert f"Status directory: {status_dir}" in rendered


def test_a_turn_past_its_role_s_scale_is_flagged_at_the_threshold_it_was_given(
    scratch_root: Path,
    tmp_path: Path,
    dispatched: ExitStack,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The anomaly threshold is the operator's, and it reaches every rendering of the turn.

    A judge wedged for nearly two hours is what this flag exists to surface, and no
    test may wait for one — so the threshold is collapsed instead, which is the same
    path with the same arithmetic.
    """
    _dispatch(dispatched, labels="run_id=run-3,round=1,node=slow,agent_role=judge")
    _await_live(scratch_root, "slow")

    assert (
        main(
            [
                "--runs-dir",
                str(tmp_path / "runs"),
                "--scratch-root",
                str(scratch_root),
                "--outlier-multiple",
                "0",
            ]
        )
        == 0
    )

    assert "ANOMALOUS, past 0x the 300s typical for it" in capsys.readouterr().out


def test_a_threshold_it_cannot_judge_against_is_refused_rather_than_used(
    tmp_path: Path, scratch_root: Path
) -> None:
    """A negative or non-finite multiple would silently flag everything, or nothing."""
    for value in ("-1", "nan", "inf"):
        with pytest.raises(SystemExit) as refused:
            main(["--scratch-root", str(scratch_root), "--outlier-multiple", value])
        assert refused.value.code == 2


def test_nothing_running_is_reported_as_none_and_never_as_unknown(
    scratch_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty registry means nothing is running, which is a claim this view may make."""
    assert main(["--runs-dir", str(tmp_path / "runs"), "--scratch-root", str(scratch_root)]) == 0

    rendered = capsys.readouterr().out
    assert "Live dispatches: none." in rendered
    assert "ownership stamp" in rendered


def test_a_procfs_it_cannot_read_is_reported_as_unknown_and_never_as_nothing(
    scratch_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The two answers this view must never confuse.

    "Nothing is running" is a claim; "nothing is knowable" is the absence of one. A
    planner acting on the first when the second is true starts a duplicate path beside
    live work — which is the mistake the ownership registry exists to retire.
    """
    monkeypatch.setenv(PROC_ROOT_ENV, str(tmp_path / "no-procfs"))

    assert main(["--runs-dir", str(tmp_path / "runs"), "--scratch-root", str(scratch_root)]) == 0
    rendered = capsys.readouterr().out
    assert "Live dispatches: unknown" in rendered
    assert "nothing may be claimed" in rendered

    assert (
        main(
            [
                "--format",
                "json",
                "--runs-dir",
                str(tmp_path / "runs"),
                "--scratch-root",
                str(scratch_root),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["observable"] is False
    assert payload["dispatches"] == []


def test_a_run_id_no_run_could_be_named_by_is_reported_as_an_unknown_session(
    scratch_root: Path,
    tmp_path: Path,
    dispatched: ExitStack,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The run id is a label a subprocess put in its own environment, not a ledger read.

    It becomes a path component when the owning session is looked up, so a value
    outside the run-id domain is refused there and reported as an unknown session —
    never resolved against whatever that path would have reached.
    """
    _dispatch(dispatched, labels="run_id=../../etc,round=1,node=sneaky,agent_role=worker")
    _await_live(scratch_root, "sneaky")

    assert (
        main(
            [
                "--format",
                "json",
                "--runs-dir",
                str(tmp_path / "runs"),
                "--scratch-root",
                str(scratch_root),
            ]
        )
        == 0
    )

    (row,) = json.loads(capsys.readouterr().out)["dispatches"]
    assert row["session"] == "unknown"


def test_a_dispatch_with_no_graph_locator_is_still_a_row(
    scratch_root: Path,
    tmp_path: Path,
    dispatched: ExitStack,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An untracked lifecycle task belongs to no run, and is still consuming this host.

    Dropping it would understate the load, and understating the load is what the `ps`
    pattern this view replaced already did.
    """
    _dispatch(dispatched, labels="persona=engineer,agent_role=worker")
    limit = time.monotonic() + 60
    while time.monotonic() < limit and not (live_dispatches(root=scratch_root) or []):
        time.sleep(0.05)

    assert main(["--runs-dir", str(tmp_path / "runs"), "--scratch-root", str(scratch_root)]) == 0

    rendered = capsys.readouterr().out
    assert "engineer (no graph locator)  [unknown]" in rendered
