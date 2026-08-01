"""E2E: a test session's process tree does not outlive the session.

A real ``pytest`` session runs as a subprocess with the real guard loaded as its
plugin; it starts a real process tree out of its own temp directory; and then the
session ends — normally in one journey, under the SIGKILL of a cancellation in the
others. Nothing about the guard is stubbed, and the only thing standing in for a
dispatch is the shape of the tree.

llmlint: ignore-file[tests_mirror_real_usage] The two tree-termination journeys call
`watchdog.process_activity` / `terminate_processes` because those *are* the production
actor: `just stop` and the dispatch watchdog end a run by walking its tree and
signalling what they found, and no operator command takes a bare pytest session as its
subject. Reaching for a command surface here would replace the real killer with a
weaker one and stop reproducing the escape these journeys exist for.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path

from leak_guard import REAPER_SCRIPT
from leak_reaper import SESSION_TOKEN_ENV
from process_tree import (
    await_orphaned,
    await_reaped,
    await_recorded_pid,
    await_recorded_pids,
    is_running,
    write_launching_tree,
    write_orphaning_tree,
)
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT
from orchestrator.watchdog import ProcessId, process_activity, terminate_processes

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
    # Held until the worker has been orphaned, so what teardown faces is a process
    # no walk can reach rather than one still hanging off the tree.
    worker = int(MARKER.read_text(encoding="utf-8").strip())
    while time.monotonic() < deadline:
        try:
            raw = Path(f"/proc/{{worker}}/stat").read_text(encoding="utf-8")
        except OSError:
            break
        if raw[raw.rfind(")") + 2 :].split()[1] == "1":
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


def _run_session(directory: Path, test_file: Path, *extra: str) -> subprocess.Popen[bytes]:
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
            *extra,
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


def _command_line(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(raw.decode("utf-8", errors="replace").split("\0"))


def _session_token(pid: int) -> str:
    """The leak-guard token ``pid`` inherited, or ``""`` if it carries none."""
    try:
        block = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return ""
    stamp = f"{SESSION_TOKEN_ENV}=".encode()
    for entry in block.split(b"\0"):
        if entry.startswith(stamp):
            return entry[len(stamp) :].decode("utf-8", errors="replace")
    return ""


def _sweep(*pids: int | None) -> None:
    """Leave nothing of this journey's own behind, whatever it was asserting.

    A journey about leaks is the last place to leak from: if an assertion fails, the
    processes it was watching are exactly the ones nothing else is going to end.
    """
    for pid in pids:
        if pid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)


def test_a_killed_session_leaves_nothing_running_from_its_temp_directory(tmp_path) -> None:
    """SIGKILL runs no teardown, so only something outside the session can clean up."""
    marker = tmp_path / "worker.pid"
    session = _run_session(tmp_path, _leaky_session(tmp_path, marker, hold_seconds=600))
    try:
        worker = await_recorded_pid(marker, timeout=e2e_timeout(60))
        assert is_running(worker)
        assert _working_directory(worker) == tmp_path
        # Killed only once the worker has been orphaned: what has to survive the
        # session's death is a process nothing inside it could still reach.
        assert await_orphaned(worker, timeout=e2e_timeout(30))

        os.kill(session.pid, signal.SIGKILL)
        session.wait(timeout=e2e_timeout(30))

        assert await_reaped(worker, timeout=e2e_timeout(20)), (
            f"a worker of the killed session is still running out of {tmp_path}"
        )
    finally:
        session.kill()
        session.wait(timeout=e2e_timeout(30))


_LAUNCHING_TEST = '''\
"""A test that launches a detached process which only then starts its worker."""

import subprocess
import sys
import time
from pathlib import Path

MARKER = Path({marker!r})
TREE = {tree!r}


def test_launches_a_detached_orchestrator():
    subprocess.Popen([sys.executable, TREE, str(MARKER)])
    # Held open until the launch has recorded both pids, so the session never ends
    # before the processes that have to outlive it exist.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        recorded = MARKER.read_text(encoding="utf-8").split() if MARKER.is_file() else []
        if len(recorded) == 2:
            break
        time.sleep(0.02)
    time.sleep({hold_seconds})
'''


def _launching_session(directory: Path, marker: Path, *, hold_seconds: float) -> Path:
    """Write the launch-shaped test into ``directory``; ``hold_seconds`` keeps it open."""
    path = directory / "test_launching_session.py"
    path.write_text(
        _LAUNCHING_TEST.format(
            marker=str(marker),
            tree=str(write_launching_tree(directory)),
            hold_seconds=hold_seconds,
        ),
        encoding="utf-8",
    )
    return path


def test_a_killed_session_reaps_what_it_launched_and_never_had_below_it(tmp_path) -> None:
    """The leak parentage cannot see at all: a launch, and what it starts afterwards.

    `dispatch.launch_orchestrator` — `just orchestrate` — starts its process in a
    session of its own and returns without waiting, so the launcher is gone within
    milliseconds and everything that process goes on to start was never below this
    session at any instant. That is not a sampling window to widen; the recorded
    orphans this closes were an `onejudge` and the `orchestrator.channel` it started
    31 seconds later, out of a session that had already died.

    The tree waits to be reparented to init *before* it starts its worker, so what is
    asserted cannot be a sample that got lucky.
    """
    marker = tmp_path / "launched.pid"
    session = _run_session(tmp_path, _launching_session(tmp_path, marker, hold_seconds=600))
    launched: int | None = None
    worker: int | None = None
    try:
        launched, worker = await_recorded_pids(marker, timeout=e2e_timeout(60))
        assert is_running(launched) and is_running(worker)
        assert _working_directory(worker) == tmp_path
        # Already reparented before the worker existed: nothing in the session was ever
        # an ancestor of it, which is what makes this distinct from the tree above.
        assert await_orphaned(launched, timeout=e2e_timeout(30))

        os.kill(session.pid, signal.SIGKILL)
        session.wait(timeout=e2e_timeout(30))

        assert await_reaped(worker, timeout=e2e_timeout(20)), (
            f"a launched worker of the killed session is still running out of {tmp_path}"
        )
        assert await_reaped(launched, timeout=e2e_timeout(20)), (
            f"the launched process of the killed session is still running out of {tmp_path}"
        )
    finally:
        session.kill()
        session.wait(timeout=e2e_timeout(30))
        _sweep(worker, launched)


def test_a_session_terminated_as_a_whole_tree_still_reaps_what_it_launched(tmp_path) -> None:
    """How the guard actually failed: the reaper was collected with the session.

    Nothing here kills the session directly. It is terminated the way a stalled
    dispatch is — `orchestrator.watchdog.terminate_processes` over the tree a walk
    of it found — because that is what ends a suite run under this harness: a
    supervisor cancelling a worker, `just stop`, a dispatch that ran out of turns.

    `start_new_session` never covered that. It takes the reaper out of the session's
    process *group*, and the walk goes by ancestry, so the one process posted to
    outlive the session was signalled along with it. The escape this proves closed
    was found live: an `onejudge` and the `orchestrator.channel` below it, still
    running out of a deleted `pytest-of-nick` temp directory eighteen hours after
    the session that started them, and still carrying that session's token.
    """
    marker = tmp_path / "launched.pid"
    session = _run_session(tmp_path, _launching_session(tmp_path, marker, hold_seconds=600))
    launched: int | None = None
    worker: int | None = None
    try:
        launched, worker = await_recorded_pids(marker, timeout=e2e_timeout(60))
        assert await_orphaned(launched, timeout=e2e_timeout(30))
        assert _working_directory(worker) == tmp_path

        observed = process_activity(ProcessId(session.pid)).pids
        # The claim this journey rests on: what production signals no longer names the
        # reaper. Asserting it here is what keeps a change that reattaches the reaper
        # from turning the reap below into a test of nothing — the launch would then be
        # reaped by the session's own teardown, and pass for the wrong reason.
        walked = {pid: _command_line(pid) for pid in observed}
        assert not [pid for pid, command in walked.items() if str(REAPER_SCRIPT) in command], (
            f"the reaper is still in the session's own tree: {walked}"
        )
        terminate_processes(observed)
        session.wait(timeout=e2e_timeout(30))

        assert await_reaped(worker, timeout=e2e_timeout(30)), (
            f"a launched worker of the terminated session is still running out of {tmp_path}"
        )
        assert await_reaped(launched, timeout=e2e_timeout(30)), (
            f"the launched process of the terminated session is still running out of {tmp_path}"
        )
    finally:
        session.kill()
        session.wait(timeout=e2e_timeout(30))
        _sweep(worker, launched)


#: Two launch-shaped tests in one file, so a two-worker run puts one on each. The suite
#: runs under xdist, and an xdist worker is a whole process of its own: it loads this
#: plugin itself, stamps its own token, and posts its own reaper. Nothing about that is
#: guaranteed by the design — it follows from where `install_session_guard` runs — so it
#: is asserted rather than assumed.
_PARALLEL_LAUNCHING_TESTS = '''\
"""Two tests, each launching a detached process that only then starts its worker."""

import subprocess
import sys
import time
from pathlib import Path

TREE = {tree!r}


def _launch(marker):
    subprocess.Popen([sys.executable, TREE, marker])
    # Held open until this worker's launch has recorded both pids, so the run never
    # ends before the processes that have to outlive it exist.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        path = Path(marker)
        recorded = path.read_text(encoding="utf-8").split() if path.is_file() else []
        if len(recorded) == 2:
            break
        time.sleep(0.02)
    time.sleep({hold_seconds})


def test_first_worker_launches_a_detached_orchestrator():
    _launch({first!r})


def test_second_worker_launches_a_detached_orchestrator():
    _launch({second!r})
'''


def test_each_xdist_worker_posts_its_own_token_and_its_own_reaper(tmp_path) -> None:
    """The suite runs in parallel, so every worker owes what one session owed.

    A worker is a separate process that imports the plugin for itself, which means it
    stamps a token of its own and posts a reaper of its own — and a reaper's claim is
    bounded by its token, so two workers sharing one would each be entitled to end the
    other's launches. Both halves are asserted here: that the tokens genuinely differ,
    and that killing the whole run still reaps what *both* workers launched.

    Terminated as a tree, as the journey above is, because that is how these runs end
    and because the controller and its workers are one tree — the reapers are not.
    """
    test_file = tmp_path / "test_parallel_launching_session.py"
    first, second = tmp_path / "first.pid", tmp_path / "second.pid"
    test_file.write_text(
        _PARALLEL_LAUNCHING_TESTS.format(
            tree=str(write_launching_tree(tmp_path)),
            first=str(first),
            second=str(second),
            hold_seconds=600,
        ),
        encoding="utf-8",
    )
    session = _run_session(tmp_path, test_file, "-n", "2", "--dist", "load")
    launched: list[int] = []
    started: list[int] = []
    try:
        for marker in (first, second):
            leader, worker = await_recorded_pids(marker, timeout=e2e_timeout(120))
            launched.append(leader)
            started.append(worker)
        assert all(await_orphaned(pid, timeout=e2e_timeout(30)) for pid in launched)

        tokens = [_session_token(pid) for pid in launched]
        assert all(tokens), f"a launched process carries no session token: {tokens}"
        assert tokens[0] != tokens[1], f"both workers stamped the same token {tokens[0]}"
        # The controller runs no tests, so it installs no guard and stamps nothing; a
        # token equal to its would mean one worker's reaper is claiming for both.
        assert _session_token(session.pid) not in tokens

        terminate_processes(process_activity(ProcessId(session.pid)).pids)
        session.wait(timeout=e2e_timeout(30))

        for pid in (*launched, *started):
            assert await_reaped(pid, timeout=e2e_timeout(30)), (
                f"a worker's launch survived the run that started it, out of {tmp_path}"
            )
    finally:
        session.kill()
        session.wait(timeout=e2e_timeout(30))
        _sweep(*launched, *started)


def test_reaping_one_session_leaves_another_live_sessions_launch_untouched(tmp_path) -> None:
    """The bound on all of this: a token is proof of descent from one session only.

    Two real sessions, each launching a detached tree the same way, and only one of
    them killed. The other is a stand-in for the live orchestrator this box always has
    running — its processes look exactly like the reaped ones by every signal except
    whose environment they inherited, and that is the signal the reaper uses.
    """
    doomed_dir = tmp_path / "doomed"
    live_dir = tmp_path / "live"
    for directory in (doomed_dir, live_dir):
        directory.mkdir()
    doomed_marker = doomed_dir / "launched.pid"
    live_marker = live_dir / "launched.pid"
    doomed = _run_session(
        doomed_dir, _launching_session(doomed_dir, doomed_marker, hold_seconds=600)
    )
    live = _run_session(live_dir, _launching_session(live_dir, live_marker, hold_seconds=600))
    doomed_launched = doomed_worker = live_launched = live_worker = None
    try:
        doomed_launched, doomed_worker = await_recorded_pids(doomed_marker, timeout=e2e_timeout(60))
        live_launched, live_worker = await_recorded_pids(live_marker, timeout=e2e_timeout(60))
        assert await_orphaned(doomed_launched, timeout=e2e_timeout(30))
        assert await_orphaned(live_launched, timeout=e2e_timeout(30))

        os.kill(doomed.pid, signal.SIGKILL)
        doomed.wait(timeout=e2e_timeout(30))

        assert await_reaped(doomed_worker, timeout=e2e_timeout(20))
        # The whole point: the surviving session's launch is the same shape, in the same
        # state, on the same host — and it is not this reaper's to end.
        assert is_running(live_launched), "another live session's launch was reaped"
        assert is_running(live_worker), "another live session's worker was reaped"
    finally:
        for session in (doomed, live):
            session.kill()
            session.wait(timeout=e2e_timeout(30))
        _sweep(live_worker, live_launched, doomed_worker, doomed_launched)


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
    # nobody fixes, and this suite is the only place it would ever be noticed. Naming
    # the orphaned worker specifically is what pins this to the watching layer — the
    # tree's root is still a descendant at teardown and would be found without it.
    assert session.returncode != 0, reported
    assert "live descendants" in reported, reported
    assert str(worker) in reported, reported
