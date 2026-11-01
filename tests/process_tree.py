"""A stand-in program that forks a child and a grandchild, and ends both at teardown.

`orchestrator/publication_guard.py` promises that a subprocess it gives up on leaves
nothing it started running. Proving that needs a stand-in whose tree is deeper than the
one process `subprocess.run`'s own timeout kills: this one starts a background child,
which starts a background grandchild, each a real process recording its pid to a file
and then sleeping, while the stand-in itself waits on them and never returns.

The fixture keeps the identity of everything it started — the pid files the stand-in
writes — and at teardown signals each of those pids that is still running and waits for
it to be gone, waiting too for a leader the test started through :meth:`ProcessTree.start`.
Teardown runs whether the test passed or failed, so a guard that leaks a tree fails its
test and still leaves nothing behind on a host several managers share; and a recorded
process still present once that bounded cleanup is over fails the teardown by name rather
than being dropped, since a leak nobody hears about is the one this module exists to stop.
Only a pid this fixture's own stand-in recorded is ever signalled, and only while that
process's command line still names this tree's directory, so a pid the kernel reused
for somebody else's process is left alone.
"""

from __future__ import annotations

import contextlib
import os
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import pytest

#: Every process the stand-in records, in the order it starts them.
MEMBERS = ("leader", "child", "grandchild")

#: The bound on every wait here: long enough for a loaded host to start or end a process,
#: and short of a hang. A guard on a wait, never a claim about how fast anything is.
HANG_GUARD_SECONDS = 20.0

_POLL_SECONDS = 0.05


def _sleeper(root: Path, member: str) -> str:
    """The line that records ``member``'s pid and turns it into a sleep naming the tree."""
    return (
        f'echo $$ > "{root}/{member}.pid.tmp" && mv "{root}/{member}.pid.tmp" '
        f'"{root}/{member}.pid"\n'
        f'exec -a "process-tree:{root}" sleep 300\n'
    )


class LeftRunning(AssertionError):
    """Recorded processes a tree's bounded cleanup could not end."""


class ProcessTree:
    """One forking stand-in, written under ``root``, and the processes it recorded."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._started: list[subprocess.Popen[bytes]] = []
        root.mkdir(parents=True, exist_ok=True)
        grandchild = root / "grandchild.sh"
        grandchild.write_text("#!/usr/bin/env bash\n" + _sleeper(root, "grandchild"))
        child = root / "child.sh"
        child.write_text(
            f'#!/usr/bin/env bash\n"{grandchild}" < /dev/null &\n' + _sleeper(root, "child")
            # The child sleeps rather than waits, so it is a separate process from its
            # background job's shell and both are live members of the tree.
        )
        self.program = (
            "#!/usr/bin/env bash\n"
            # Re-exec under a name carrying the tree's directory, wherever the stand-in
            # is installed, so teardown can tell the leader is still this tree's too.
            'if [ "${PROCESS_TREE_NAMED:-}" != 1 ]; then\n'
            f'    PROCESS_TREE_NAMED=1 exec -a "process-tree:{root}" bash "$0" "$@"\n'
            "fi\n"
            f'echo $$ > "{root}/leader.pid"\n'
            f'"{child}" < /dev/null &\n'
            f'until [ -s "{root}/child.pid" ] && [ -s "{root}/grandchild.pid" ]; do\n'
            "    sleep 0.05\n"
            "done\n"
            "wait\n"
        )
        for script in (grandchild, child):
            script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def install(self, path: Path) -> Path:
        """Write the stand-in at ``path``, executable, and return it."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.program, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def start(self) -> subprocess.Popen[bytes]:
        """Run the stand-in directly, as this test's own child, which :meth:`end` waits for."""
        leader = subprocess.Popen([str(self.install(self.root / "stand-in"))])
        self._started.append(leader)
        return leader

    def wait_recorded(self) -> dict[str, int]:
        """Every member's pid, polling within the hang guard; raises if one never appears."""
        deadline = time.monotonic() + HANG_GUARD_SECONDS
        while len(found := self.recorded()) < len(MEMBERS):
            if time.monotonic() > deadline:
                raise AssertionError(f"the stand-in under {self.root} recorded only {found}")
            time.sleep(_POLL_SECONDS)
        return found

    def recorded(self) -> dict[str, int]:
        """Every pid the stand-in has recorded so far, by member."""
        found: dict[str, int] = {}
        for member in MEMBERS:
            written = self.root / f"{member}.pid"
            if written.is_file() and written.read_text().strip():
                found[member] = int(written.read_text().strip())
        return found

    def descendants(self) -> dict[str, int]:
        """The child's and grandchild's pids, which the stand-in records before it waits."""
        return {name: pid for name, pid in self.recorded().items() if name != "leader"}

    def running(self) -> dict[str, int]:
        """Each recorded process that is still running, waiting out the hang guard first.

        A process is gone once the kernel no longer knows its pid — exited *and* reaped —
        so one left as a zombie, unreaped by the test or by anyone, still counts as here.
        """
        deadline = time.monotonic() + HANG_GUARD_SECONDS
        while True:
            alive = {name: pid for name, pid in self.recorded().items() if _exists(pid)}
            if not alive or time.monotonic() > deadline:
                return alive
            _reap(alive.values())
            time.sleep(_POLL_SECONDS)

    def end(self) -> None:
        """Signal every process this tree started, wait it out, and raise on any survivor.

        Raises :class:`LeftRunning` naming each recorded process still present once the
        bounded wait is over, so a cleanup that could not finish is reported, not dropped.
        """
        for pid in self.recorded().values():
            if _ours(pid, self.root):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        for leader in self._started:
            leader.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                leader.wait(timeout=HANG_GUARD_SECONDS)
        left = self.running()
        if left:
            raise LeftRunning(f"still running after cleanup of {self.root}: {left}")


def _exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap(pids: Iterable[int]) -> None:
    """Reap any of ``pids`` that is this process's own exited child."""
    for pid in pids:
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


def _ours(pid: int, root: Path) -> bool:
    """Whether ``pid`` is still a process this tree started, read off its command line.

    Every member runs under a name carrying the tree's directory — the leader re-execs
    itself under one and the two sleepers ``exec -a`` into one — so each names ``root``.
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return str(root).encode() in cmdline


@pytest.fixture(name="process_tree")
def process_tree_fixture() -> Iterator[Callable[[Path], ProcessTree]]:
    """Make forking stand-ins under the directories asked for, and end them all at teardown."""
    made: list[ProcessTree] = []

    def make(root: Path) -> ProcessTree:
        tree = ProcessTree(root)
        made.append(tree)
        return tree

    try:
        yield make
    finally:
        failures: list[LeftRunning] = []
        for tree in made:
            try:
                tree.end()
            except LeftRunning as left:
                failures.append(left)
        if failures:
            raise LeftRunning("; ".join(str(one) for one in failures))
