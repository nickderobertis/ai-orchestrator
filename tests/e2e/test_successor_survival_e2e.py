"""A round launched from inside a dispatch, against the sweep that follows it.

`tests/e2e/test_round_ownership_e2e.py` proves a round survives the *signals* a turn's
teardown sends. That is only half of outliving the dispatch it was started from, and
the other half killed three publications here in one day: what a dispatch starts
inherits its ``ORCHESTRATOR_AGENT_STATUS_DIR``, and once that dispatch settles the
scratch sweep reads the stamp as proof of a leaked tree and terminates everything
carrying it. A round owner sixty-six seconds old, and two `repo-recover` runs whose
gate had already gone green, all died that way.

So this journey runs the whole sequence with nothing faked about it: a real `just
run-plan` launched carrying a real dispatch's stamp, a real teardown of the launching
turn's process group, that dispatch's ownership genuinely released, and then the real
`just sweep-scratch` recipe over the same scratch root. The round has to still be
there afterwards, still be driving its dispatch, and still settle.

The companion half runs in that same sweep, because a fix that made the round survive
by weakening the reaper would pass every assertion above: a genuinely leaked process —
one stamped for the finished dispatch that never re-attributed — must still be reaped
by the run that spares the round.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from process_tree import (
    await_reaped,
    is_running,
    spawn_reparented_leaving,
    write_reparented_leaving,
)
from rendezvous import Rendezvous
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT
from orchestrator.coordination import process_start_identity
from orchestrator.dispatches import live_dispatches
from orchestrator.scratch import (
    AGENT_STATUS_DIR_ENV,
    AGENT_STATUS_DIR_NAME,
    OWNER_LOCK_NAME,
    WATCHDOG_PATTERN,
)

ANNOUNCED_OWNER = re.compile(r"pid (\d+) leads its own session")
#: An owner record for a pid the kernel will not be handing out again, which is what a
#: settled dispatch leaves behind once it has released its scratch directory.
FINISHED_OWNER = "999999999 1"


def _live_owner_record(pid: int) -> str:
    """The record a dispatch writes while it still holds its scratch directory."""
    identity = process_start_identity(pid)
    assert identity is not None, f"procfs did not identify pid {pid}"
    return f"{pid} {identity}"


class LaunchedRound:
    """One `just run-plan` started the way a dispatched agent turn starts it."""

    def __init__(self, process: subprocess.Popen[bytes], streams: tuple[Path, Path]) -> None:
        self.process = process
        self.out, self.err = streams
        self.owner = self._await_announced_owner()

    def _await_announced_owner(self) -> int:
        guard = e2e_deadline(60)
        while time.monotonic() < guard:
            reported = self.err.read_text(encoding="utf-8", errors="replace")
            announced = ANNOUNCED_OWNER.search(reported)
            if announced is not None:
                return int(announced[1])
            time.sleep(0.02)
        pytest.fail(f"no round owner was announced in {self.err}")

    def teardown_launching_turn(self) -> None:
        """Kill the launcher's whole process group, as ending a harness turn does."""
        os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        self.process.wait(timeout=e2e_timeout(30))


@pytest.fixture
def scratch_root(tmp_path: Path) -> Path:
    """The one temporary root the launched round and everything under it writes into.

    Pointed at through ``TMPDIR``, which is what `tempfile` and so every scratch
    directory in this harness resolves — the round owner's own claim, and the watchdog
    tree each dispatch it makes takes out. Sweeping this root therefore sweeps the real
    thing rather than a stand-in for it.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    return root


def _plan(tmp_path: Path, run_id: str, hold: Rendezvous) -> Path:
    """A plan whose one agent node parks in the real provider until released."""
    path = tmp_path / f"{run_id}.json"
    path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "held",
                        "persona": "engineer",
                        "task": f"complete-now{hold.sentinels()}",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def launch(
    tmp_path: Path,
    scratch_root: Path,
    command_base: Callable[..., Path],
    onejudge_bin: str,
) -> Iterator[Callable[[Path, str], LaunchedRound]]:
    """Start rounds under a launching dispatch's stamp, and reap whatever survives."""
    started: list[LaunchedRound] = []

    def _start(status_dir: Path, run_id: str) -> LaunchedRound:
        hold = Rendezvous.at(tmp_path, run_id)
        streams = (tmp_path / f"{run_id}.out", tmp_path / f"{run_id}.err")
        arguments = [
            "just",
            "run-plan",
            str(_plan(tmp_path, run_id, hold)),
            "--run",
            run_id,
            "--runs-dir",
            str(tmp_path / "runs"),
            "--base",
            str(command_base()),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ]
        with streams[0].open("wb") as out, streams[1].open("wb") as err:
            process = subprocess.Popen(
                arguments,
                cwd=REPO_ROOT,
                stdout=out,
                stderr=err,
                # A harness turn puts what it starts in a group of its own and tears
                # that group down at the end; this is the group the teardown aims at.
                start_new_session=True,
                env={
                    **os.environ,
                    "TMPDIR": str(scratch_root),
                    AGENT_STATUS_DIR_ENV: os.fspath(status_dir),
                },
            )
        hold.wait(e2e_timeout(120))
        round_ = LaunchedRound(process, streams)
        started.append(round_)
        return round_

    yield _start
    for round_ in started:
        Rendezvous.at(tmp_path, "survives").let_go()
        for pid in (round_.owner, round_.process.pid):
            with contextlib.suppress(PermissionError, ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            round_.process.wait(timeout=e2e_timeout(15))


def _sweep(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "sweep-scratch", "--root", str(root)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=e2e_timeout(180),
    )


def _successor_directory(root: Path, owner: int) -> Path:
    """The watchdog directory the round owner re-attributed itself to.

    Found by the record it wrote rather than by name, because the name is a temporary
    one nothing outside the successor knows — and finding it by the owner it recorded
    is the same evidence the sweeper reads.
    """
    for candidate in sorted(root.glob(WATCHDOG_PATTERN)):
        record = candidate / OWNER_LOCK_NAME
        if record.is_file() and record.read_text(encoding="utf-8").split()[:1] == [str(owner)]:
            return candidate
    pytest.fail(f"the round owner {owner} recorded no scratch directory of its own under {root}")


def _await_result(path: Path) -> dict[str, object]:
    guard = e2e_deadline(180)
    while time.monotonic() < guard:
        if path.exists():
            parsed: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
            return parsed
        time.sleep(0.05)
    pytest.fail(f"the round never recorded a result at {path}")


@pytest.mark.load_sensitive
def test_a_launched_round_outlives_the_sweep_that_reaps_its_launchers_leavings(
    tmp_path: Path,
    scratch_root: Path,
    launch: Callable[[Path, str], LaunchedRound],
) -> None:
    """The round survives its launcher's whole ending; a real leak still does not."""
    launcher = scratch_root / "orchestrator-watchdog-launcher"
    status_dir = launcher / AGENT_STATUS_DIR_NAME
    status_dir.mkdir(parents=True)
    (launcher / OWNER_LOCK_NAME).write_text(_live_owner_record(os.getpid()), encoding="utf-8")

    round_ = launch(status_dir, "survives")
    # Started while the launching dispatch is still live and stamped for it, exactly as
    # anything else that dispatch starts is. Nothing re-attributes it, so when the
    # dispatch is released below this is a genuinely leaked tree.
    leaked = spawn_reparented_leaving(
        write_reparented_leaving(tmp_path),
        tmp_path / "leaked.pid",
        status_dir,
        timeout=e2e_timeout(60),
    )
    successor = _successor_directory(scratch_root, round_.owner)

    round_.teardown_launching_turn()
    assert is_running(round_.owner), "the round died with the turn that launched it"
    # The launching step settles: its dispatcher releases the scratch directory, which
    # is what turns every stamp naming it into evidence of something left behind.
    (launcher / OWNER_LOCK_NAME).write_text(FINISHED_OWNER, encoding="utf-8")

    swept = _sweep(scratch_root)

    assert is_running(round_.owner), (
        f"the sweep reaped the round owner as its launcher's leaving\n{swept.stdout}"
    )
    assert successor.is_dir(), "the sweep reclaimed the directory the round is attributed to"
    # The round is not merely alive: it is still driving the dispatch it started, which
    # is what the operator lost when this reaped a tree sixty-six seconds into a round.
    driving = live_dispatches(root=scratch_root)
    assert driving, f"no dispatch of the surviving round is running\n{swept.stdout}"
    assert {dispatch.run_id for dispatch in driving} == {"survives"}
    # The successor's own directory is deliberately not held with the lock a dispatcher
    # takes, so a round owner never appears in `just host` as a dispatch with no turn.
    assert all(item.status_dir != successor / AGENT_STATUS_DIR_NAME for item in driving)

    assert await_reaped(leaked, timeout=e2e_timeout(60)), (
        f"a genuinely leaked tree survived the sweep that spared the round\n{swept.stdout}"
    )
    assert str(leaked) in swept.stdout

    Rendezvous.at(tmp_path, "survives").let_go()
    settled = _await_result(tmp_path / "runs" / "survives" / "round-01" / "result.json")
    assert settled["state"] == "complete"
