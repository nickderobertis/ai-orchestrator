"""A real dispatch-shaped process tree for tests that prove nothing survives it.

Every leak this repository has had to reap by hand had the same shape: a root that
keeps running, an intermediate that starts the real worker and then exits, and a
worker that reparents away from the tree anyone was watching. Reproducing that
shape — rather than one sleeping child — is what makes a cleanup test mean
anything, because a walk from the root can no longer find the worker at all.

The intermediate lingers briefly before it goes, which is the one detail that has
to be right: a real dispatch's parent runs for minutes while its children work, and
that is the whole window in which anything can observe them. A parent that vanished
the instant it forked would be unattributable to anyone by any means short of
changing what the session means by "still alive", and it is not a shape this
harness has ever produced.
"""

from __future__ import annotations

import time
from pathlib import Path

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
# nothing that has ever leaked here.
LINGER = 2.0
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
    script.write_text(_SOURCE, encoding="utf-8")
    return script


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
    status. That distinction only became visible once the test session made itself a
    child subreaper, because an orphan now waits for *it* rather than vanishing into
    init, and a survivor check that counted zombies would report a reaped process as
    a leak forever.
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
