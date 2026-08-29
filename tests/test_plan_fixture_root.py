"""The reader lock that keeps one test tier from breaking another's store read.

Every tier of this suite runs as its own process over one configured `test-fixtures`
root, so the property under test is a property *between* processes: these drive real
ones holding real locks rather than asserting on this module's own bookkeeping.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import plan_fixture_root
import pytest

from orchestrator.project_store import write_plan_project

#: A plan small enough to be one record and complete enough for `write_plan_project`.
PLAN: dict[str, object] = {"name": "held", "tasks": [{"id": "only", "task": "Do it."}]}


def _store(root: Path, native: str) -> str:
    write_plan_project(root, PLAN, native_id=native)
    return native


def _reaped_pid() -> int:
    """A pid that has certainly exited, taken from a process this test waited on."""
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    return ended.pid


def _peer_holding(root: Path) -> subprocess.Popen[str]:
    """A live peer process holding the shared reader lock, as another tier does."""
    peer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(f"""
                import sys
                from pathlib import Path
                sys.path.insert(0, {str(Path(__file__).parent)!r})
                import plan_fixture_root
                plan_fixture_root.hold_shared(Path({str(root)!r}))
                print("held", flush=True)
                sys.stdin.readline()
            """),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert peer.stdout is not None
    assert peer.stdout.readline().strip() == "held"
    return peer


def _release(peer: subprocess.Popen[str]) -> None:
    assert peer.stdin is not None
    peer.stdin.write("\n")
    peer.stdin.close()
    peer.wait(timeout=30)


def test_a_sweep_reclaims_a_record_whose_writer_is_gone(tmp_path: Path) -> None:
    """The record an exited or killed tier left behind, and only that one."""
    stale = _store(tmp_path, f"test-{_reaped_pid()}-0-stale")
    mine = _store(tmp_path, f"test-{os.getpid()}-0-live")

    reclaimed = plan_fixture_root.sweep_dead_owners(tmp_path)

    assert reclaimed == [stale], reclaimed
    assert not (tmp_path / "projects" / f"{stale}.md").exists()
    assert not (tmp_path / "tasks" / stale).exists()
    assert (tmp_path / "projects" / f"{mine}.md").is_file()
    assert (tmp_path / "tasks" / mine).is_dir()


def test_a_record_no_test_process_named_is_never_reclaimed(tmp_path: Path) -> None:
    """A root is shared, so anything not named `test-<pid>-` belongs to somebody else."""
    _store(tmp_path, "somebody-elses-plan")

    assert plan_fixture_root.sweep_dead_owners(tmp_path) == []
    assert (tmp_path / "projects" / "somebody-elses-plan.md").is_file()


def test_a_sweep_removes_nothing_while_a_peer_process_holds_the_reader_lock(
    tmp_path: Path,
) -> None:
    """The whole point: a live tier's walk is never taken out from under it."""
    stale = _store(tmp_path, f"test-{_reaped_pid()}-0-stale")
    peer = _peer_holding(tmp_path)
    try:
        assert plan_fixture_root.sweep_dead_owners(tmp_path) == []
        assert (tmp_path / "projects" / f"{stale}.md").is_file()
        assert (tmp_path / "tasks" / stale).is_dir()
    finally:
        _release(peer)

    assert plan_fixture_root.sweep_dead_owners(tmp_path) == [stale]
    assert not (tmp_path / "projects" / f"{stale}.md").exists()


def test_this_process_holds_the_reader_lock_on_the_configured_root() -> None:
    """`tests/conftest.py` takes it in every process before it runs anything."""
    assert plan_fixture_root.holds_shared()


def test_holding_the_reader_lock_twice_keeps_the_first_descriptor(tmp_path: Path) -> None:
    """A second call is the same lock, so a sweep from elsewhere still sees one holder."""
    plan_fixture_root.hold_shared(tmp_path)
    plan_fixture_root.hold_shared(tmp_path)

    assert plan_fixture_root.holds_shared(tmp_path)


@pytest.mark.parametrize("pid", (1, 2**62))
def test_liveness_is_answered_conservatively_for_a_pid_it_cannot_signal(pid: int) -> None:
    """Anything but a definite no keeps the record, so neither of these is reclaimed."""
    assert plan_fixture_root.running(pid) is True
