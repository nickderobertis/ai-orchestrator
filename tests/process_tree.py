"""Real dispatch-shaped process trees for tests that prove nothing survives them.

Reproducing the shape of an actual leak — rather than one sleeping child — is what
makes a cleanup test mean anything. Both shapes this harness produces are here, and
they are written separately rather than as one parameterized tree because what
distinguishes them is not how wide the window is in which parentage can still
attribute the worker but whether there is a window at all.
"""

from __future__ import annotations

import time
from pathlib import Path

from leak_reaper import POLL_SECONDS

#: Long enough for the guard to take several samples of the tree before it orphans.
LINGER = POLL_SECONDS * 4

_SOURCE = '''\
"""A three-level process tree whose deepest worker outlives its own ancestry."""

import os
import subprocess
import sys
import time

LIFETIME = 600
# How long the intermediate stays before orphaning the worker. A real dispatch's
# parent lives for minutes while its children work, which is what lets a watcher
# see them at all; a parent that vanished the instant it forked would model
# nothing that has ever leaked here. Written in as a multiple of the guard's own
# sampling interval, because what the number has to buy is samples: too few and the
# tree is gone before the watcher ever sees it, and the e2e stops being about
# whether the guard reaps and starts being about whether it looked in time.
LINGER = LINGER_SECONDS
ROLE = "ORCHESTRATOR_TEST_TREE_ROLE"
marker = sys.argv[1]

if os.environ.get(ROLE) == "spawner":
    worker = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({LIFETIME})"],
        # A harness that daemonizes — an Nx daemon, a detached round owner — leaves
        # its supervisor's process group as well as its ancestry, so neither handle
        # the supervisor holds can reach it.
        start_new_session="--worker-detaches" in sys.argv[2:],
    )
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write(str(worker.pid))
    time.sleep(LINGER)
    # Leave without waiting, so the worker reparents and no walk from the root
    # can reach it again.
    os._exit(0)

subprocess.Popen(
    [sys.executable, *sys.argv],
    env={**os.environ, ROLE: "spawner"},
)
if "--root-exits" not in sys.argv[2:]:
    time.sleep(LIFETIME)
'''


def write_orphaning_tree(directory: Path) -> Path:
    """Write the tree script into ``directory`` and return its path."""
    script = directory / "orphaning_tree.py"
    script.write_text(_SOURCE.replace("LINGER_SECONDS", repr(LINGER)), encoding="utf-8")
    return script


#: The other shape, and the one no sampling interval can reach: a *launch*. This is
#: what `dispatch.launch_orchestrator` does for `just orchestrate` — start the process
#: in a session of its own and return without waiting for it — and it is the shape of
#: the `onejudge` and `orchestrator.channel` orphans this repository reaped by hand.
#:
#: The distinction from the tree above is not a shorter window; it is no window. There
#: the worker existed while its parent was still in the session, so a sample could see
#: it. Here the launched process waits until it has already been reparented to init
#: before it starts the worker at all, so the worker was never below the session at any
#: instant, at any sampling rate. Only something a process carries with it — its
#: inherited environment — can still attribute it.
_LAUNCH_SOURCE = '''\
"""A launch: detached, never waited for, and only then does it start a worker."""

import os
import subprocess
import sys
import time

LIFETIME = 600
ROLE = "ORCHESTRATOR_TEST_LAUNCH_ROLE"
marker = sys.argv[1]

if os.environ.get(ROLE) == "launched":
    # Wait to be reparented before starting anything, so the worker below is one no
    # walk from the launching session could ever have found.
    deadline = time.monotonic() + 60
    while os.getppid() != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    worker = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({LIFETIME})"])
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()} {worker.pid}")
    time.sleep(LIFETIME)

else:
    subprocess.Popen(
        [sys.executable, *sys.argv],
        env={**os.environ, ROLE: "launched"},
        # Exactly `launch_orchestrator`: its own session, and no wait for it.
        start_new_session=True,
    )
    os._exit(0)
'''


def write_launching_tree(directory: Path) -> Path:
    """Write the launch-shaped script into ``directory`` and return its path."""
    script = directory / "launching_tree.py"
    script.write_text(_LAUNCH_SOURCE, encoding="utf-8")
    return script


def await_recorded_pids(path: Path, *, timeout: float = 30.0) -> tuple[int, ...]:
    """Wait for a launch to record its own pid and its worker's, and return both."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            recorded = path.read_text(encoding="utf-8").split()
        except OSError:
            recorded = []
        if len(recorded) == 2:
            return tuple(int(value) for value in recorded)
        time.sleep(0.02)
    raise AssertionError(f"no launched pids were recorded at {path} within {timeout:g}s")


def await_recorded_pid(path: Path, *, timeout: float = 30.0) -> int:
    """Wait for the tree to record its deepest worker's pid, and return it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            recorded = path.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if recorded:
            return int(recorded)
        time.sleep(0.02)
    raise AssertionError(f"no worker pid was recorded at {path} within {timeout:g}s")


def is_running(pid: int) -> bool:
    """Whether ``pid`` names a process that is still executing.

    A zombie is deliberately not running: it has already exited and holds nothing —
    no working directory, no open files — while it waits for a parent to collect its
    status. Counting one as alive would report a process that has already been reaped
    as a leak, and keep reporting it until its parent got around to waiting on it.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    # The parenthesized comm field may itself contain spaces, so state is read
    # relative to its closing delimiter rather than by field index.
    return raw[raw.rfind(")") + 2 :].split(" ", 1)[0] != "Z"


def await_orphaned(pid: int, *, timeout: float = 30.0) -> bool:
    """Wait until ``pid`` has been reparented away from whatever started it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return False
        if raw[raw.rfind(")") + 2 :].split()[1] == "1":
            return True
        time.sleep(0.02)
    return False


def await_reaped(pid: int, *, timeout: float = 30.0) -> bool:
    """Wait until ``pid`` is gone, returning whether it went."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running(pid):
            return True
        time.sleep(0.02)
    return not is_running(pid)
