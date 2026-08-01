"""`just smoke` while a realistic concurrent e2e load is running.

The failure this exists for cost a publication that had already passed its gate:
`just smoke` reported that the selected harness "ran but did not succeed" while a
full e2e coverage run was in flight spawning real onejudge subprocesses, and the
same command passed standalone immediately before and after. The pre-push hook
runs the smoke whenever the pushed diff touches `scripts/`, so the worker most
likely to be generating that load is the very one whose push it blocks — and the
failure's stated cause, a harness outage, was not the real one.

So the load here is generated rather than asserted: real `run_repo_task`
dispatches are held live inside their agents, at the fake provider's own barrier,
for the whole of the smoke. Everything the smoke touches is real — the recipe, the
`scripts/oneharness-agent.sh` heartbeat branch, `oneharness` itself, the isolated
history store, and the launch contract read back out of it. Only the paid provider
CLI is faked, through oneharness's own `ONEHARNESS_BIN_CODEX` seam, and it is made
to fail a launch the way the contended one did.

llmlint: ignore-file[e2e_not_mocked] The paid agent harness is the one boundary
this repository fakes; the smoke command, its wrapper, oneharness, the history
store, and the concurrent lifecycle dispatches all run for real here.
"""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fake_codex import provider_environment
from rendezvous import Rendezvous
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT, gitops
from orchestrator.lifecycle import run_repo_task
from orchestrator.smoke import LAUNCH_ATTEMPTS
from orchestrator.workspace import Workspace

MP = multiprocessing.get_context("spawn")


def _barrier_lifecycle_process(
    origin: str,
    canonical: str,
    root: str,
    state_root: str,
    base_config: str,
    persona_dir: str,
    hold: Rendezvous,
) -> None:
    """Run one real dispatch and park it inside its agent until released."""
    os.environ["AI_ORCHESTRATOR_HOME"] = state_root
    run_repo_task(
        origin,
        "complete-now write-unique-change: hold this dispatch live while the smoke runs"
        f"{hold.sentinels()}",
        "engineer",
        workspace=Workspace(root, resolver=lambda _spec: Path(canonical), workflow="local"),
        base_path=base_config,
        persona_dir=persona_dir,
        recorded_gate=["true"],
        repo_type="single-owner",
    )


class Load:
    """Real concurrent dispatches, held inside their agents for a whole journey."""

    def __init__(self, processes: list[multiprocessing.process.BaseProcess], release: Path) -> None:
        self.processes = processes
        self.release = release

    def alive(self) -> int:
        return sum(1 for process in self.processes if process.is_alive())

    def stop(self) -> None:
        self.release.write_text("release\n", encoding="utf-8")
        for process in self.processes:
            process.join(e2e_timeout(60))


@pytest.fixture
def concurrent_dispatch_load(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
) -> Iterator[Callable[[int], Load]]:
    """Start N real onejudge dispatches and hold every one of them live."""
    started: list[Load] = []

    def _start(count: int) -> Load:
        index = len(started)
        origin = bare_origin()
        canonical = gitops.clone(origin, tmp_path / f"canonical-load-{index}")
        barrier = tmp_path / f"barrier-{index}"
        barrier.mkdir()
        # One release for all of them: the journey needs every dispatch held at once.
        release = barrier / "shared.release"
        holds = [Rendezvous(barrier / f"ready-{number}", release) for number in range(count)]
        processes: list[multiprocessing.process.BaseProcess] = [
            MP.Process(
                target=_barrier_lifecycle_process,
                args=(
                    str(origin),
                    str(canonical),
                    str(tmp_path / f"worktrees-load-{index}"),
                    os.environ["AI_ORCHESTRATOR_HOME"],
                    str(command_base()),
                    str(personas_dir),
                    hold,
                ),
            )
            for hold in holds
        ]
        load = Load(processes, release)
        started.append(load)
        for process in processes:
            process.start()
        limit = e2e_deadline(90)
        while not all(hold.arrived() for hold in holds) and time.monotonic() < limit:
            time.sleep(0.05)
        assert all(hold.arrived() for hold in holds), (
            "the concurrent dispatches never all reached their agents"
        )
        return load

    yield _start
    for load in started:
        load.stop()


def _run_smoke(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "smoke"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )


def test_smoke_survives_a_harness_that_refuses_a_launch_under_a_live_load(
    tmp_path: Path, concurrent_dispatch_load: Callable[[int], Load]
) -> None:
    """A transient launch failure under load must not be reported as an outage."""
    attempts = tmp_path / "harness-attempts"
    load = concurrent_dispatch_load(3)
    assert load.alive() == 3

    result = _run_smoke(provider_environment(attempt_log=attempts, unavailable_attempts=1))

    # Live for the whole smoke, not merely started before it.
    assert load.alive() == 3, "the concurrent dispatches did not outlive the smoke"
    assert result.returncode == 0, result.stderr
    assert "smoke: passed via codex" in result.stdout
    # The retry is reported rather than smoothed over: the operator is the only one
    # who can act on a host that needed two launches to record one turn.
    assert "after 2 attempts" in result.stdout
    assert len(attempts.read_text(encoding="utf-8").splitlines()) == 2


def test_smoke_still_fails_when_every_launch_under_load_fails(
    tmp_path: Path, concurrent_dispatch_load: Callable[[int], Load]
) -> None:
    """Tolerating a transient failure must not tolerate a broken launch path."""
    attempts = tmp_path / "harness-attempts"
    load = concurrent_dispatch_load(2)

    result = _run_smoke(
        provider_environment(attempt_log=attempts, unavailable_attempts=LAUNCH_ATTEMPTS + 1)
    )

    assert load.alive() == 2
    assert result.returncode == 1
    assert "real harness smoke failed" in result.stderr
    assert f"after {LAUNCH_ATTEMPTS} attempts" in result.stderr
    assert "rerun 'just smoke'" in result.stderr
    assert len(attempts.read_text(encoding="utf-8").splitlines()) == LAUNCH_ATTEMPTS
