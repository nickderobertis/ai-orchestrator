"""E2E: a test session's process tree does not outlive the session.

The suite is the only QA loop this repository has, so a leaking session is not a
tidiness problem: twenty-two onejudge, channel, and run-plan processes were once
found still running out of temp directories that had been deleted a day earlier,
and the load they put on an eight-core host is what made the timing-sensitive e2es
fail for reasons that had nothing to do with the code under test.

Both ways a session ends are driven for real. A real ``pytest`` session runs as a
subprocess with the real guard loaded as its plugin; it starts a real three-level
process tree out of its own temp directory, whose deepest worker leaves both its
process group and its ancestry; and then the session ends — normally in one journey,
under the SIGKILL of a cancellation in the other. Nothing about the guard is
stubbed. The only thing standing in for a dispatch is the shape of the tree.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from leak_reaper import POLL_SECONDS
from process_tree import await_reaped, await_recorded_pid, is_running, write_orphaning_tree
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT

TESTS_DIR = REPO_ROOT / "tests"

# The tree is started through the guard's own registered `Popen` on purpose — that
# is what a dispatch does — and its deepest worker still detaches into a session of
# its own, so signalling the registered group cannot reach it. The test waits for
# that worker's pid, so the session never ends before there is something to leak.
_LEAKY_TEST = '''\
"""A test that starts a dispatch-shaped tree the guard's Popen hook never sees."""

import subprocess
import sys
import time
from pathlib import Path

MARKER = Path({marker!r})
TREE = {tree!r}
HOLD_SECONDS = {hold_seconds}


def test_starts_a_dispatch_shaped_tree():
    subprocess.Popen([sys.executable, TREE, str(MARKER), "--worker-detaches"])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if MARKER.is_file() and MARKER.read_text(encoding="utf-8").strip():
            break
        time.sleep(0.02)
    time.sleep(HOLD_SECONDS)
'''


def _leaky_session(directory: Path, marker: Path, *, hold_seconds: float) -> Path:
    """Write the leaking test into ``directory``; ``hold_seconds`` keeps it open."""
    path = directory / "test_leaky_session.py"
    path.write_text(
        _LEAKY_TEST.format(
            marker=str(marker),
            tree=str(write_orphaning_tree(directory)),
            hold_seconds=hold_seconds,
        ),
        encoding="utf-8",
    )
    return path


def _run_session(directory: Path, test_file: Path) -> subprocess.Popen[bytes]:
    """Start a real pytest session, in ``directory``, with the real guard plugin."""
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "leak_guard",
            "-p",
            "no:cacheprovider",
            "-q",
            str(test_file),
        ],
        # The session's own working directory, so everything it starts inherits it
        # and "survived its temp directory" is a claim about a real path.
        cwd=directory,
        env={**os.environ, "PYTHONPATH": str(TESTS_DIR)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _working_directory(pid: int) -> Path:
    return Path(os.readlink(f"/proc/{pid}/cwd"))


def test_a_killed_session_leaves_nothing_running_from_its_temp_directory(tmp_path) -> None:
    """SIGKILL runs no teardown, so only something outside the session can clean up."""
    marker = tmp_path / "worker.pid"
    session = _run_session(tmp_path, _leaky_session(tmp_path, marker, hold_seconds=600))
    try:
        worker = await_recorded_pid(marker, timeout=e2e_timeout(60))
        assert is_running(worker)
        assert _working_directory(worker) == tmp_path
        # The reaper claims a process within one sampling interval of its appearing,
        # and that interval is its whole blind spot. Waiting it out here is what makes
        # the journey deterministic rather than a race against the sampler; the leaks
        # this closes ran for minutes, not milliseconds.
        time.sleep(2 * POLL_SECONDS)

        os.kill(session.pid, signal.SIGKILL)
        session.wait(timeout=e2e_timeout(30))

        assert await_reaped(worker, timeout=e2e_timeout(20)), (
            f"a worker of the killed session is still running out of {tmp_path}"
        )
    finally:
        session.kill()
        session.wait(timeout=e2e_timeout(30))


def test_a_finished_test_leaves_nothing_running_from_its_temp_directory(tmp_path) -> None:
    """The ordinary ending: teardown must reap what registration never saw."""
    marker = tmp_path / "worker.pid"
    session = _run_session(tmp_path, _leaky_session(tmp_path, marker, hold_seconds=0))
    worker = await_recorded_pid(marker, timeout=e2e_timeout(60))
    reported = session.communicate(timeout=e2e_timeout(120))[0].decode()

    assert await_reaped(worker, timeout=e2e_timeout(20)), (
        f"a worker the finished test started is still running out of {tmp_path}"
    )
    # Reported as a failure rather than reaped in silence: an unreported leak is one
    # nobody fixes, and this suite is the only place it would ever be noticed.
    assert session.returncode != 0, reported
    assert "live descendants" in reported, reported
