"""A run this suite launches walks no repository this host registers.

Since onepipeline 0.51.0 a run's idle driver passes over every identity the `onevcs`
registry it reads names: it maintains that identity's pool slots, fetches each of its
checkouts, and asks the identity's origin about every candidate branch with `git
ls-remote` — deleting the origin copy of one that holds no work beyond its base — and a
driver closing out waits for that pass. `tests/onevcs_state_snapshot.py` is why no
process this suite starts is handed the host's registry any more, and this journey is
what holds it: one real launch through `just orchestrate`, under exactly the environment
the suite composes for every launch journey, with git's own trace2 event stream switched
on for every git process the launch and its driver start.

What the processes read is taken from that stream rather than from any view of the run:
each git process records the repository it ran in and the command line it ran. The host's
registered checkouts and origins are read from the suite's registry copy, and none of
them may appear. A pass that walked nothing would pass that just as well as a pass that
never happened, so the launch also carries **one identity of the journey's own** — a
local checkout of a local bare origin, holding a branch the origin has not got — and the
same stream has to show the driver reaching that origin. That is the positive control:
it proves the trace reached the driver's pass, so the absence of every host identity is
an absence the pass would have shown.

The driver is waited out rather than assumed gone, because the leak this closes was
paid for there: a driver finishing a pass over this host's repositories held every
launch journey for 13 to 15 minutes after its run settled.
"""

# The registry copy is read as a file, once, for the checkout paths and origins the
# trace is compared against: the interface an operator reads them through is `onevcs
# repos`, whose first contact migrates a registry — `tests/onevcs_state_snapshot.py` is
# the measurement — and the git trace is a stream no view renders.
# llmlint: ignore-file[tests_mirror_real_usage] see above
# llmlint: ignore-file[new_code_lands_in_a_project] `tests/e2e/` has no project file of its
# own: every module in it is collected by the `orchestrator` project's `test` target, whose
# pytest command selects `tests/` outside the directories other projects own, and this
# module is placed and collected exactly as its siblings are.

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import onevcs_state_snapshot
import pytest
from fake_backend import AGENT_DELAY_ENV
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import project_from_plan
from test_orchestrate_launch_e2e import CandidatePlan, _node
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: This module spends a real launch, and every recipe it runs waits on the `uv` lock the
#: journeys re-provisioning this checkout take.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: git's own event stream: a directory names one file per git process, each carrying the
#: process's command line and the repository it ran in.
TRACE = "GIT_TRACE2_EVENT"
#: How long the node's one turn holds its slot, which is how long the driver has idle
#: passes to start its sweep on.
HELD_SECONDS = 10
#: A hang guard, not a pace: the node settles once its held turn ends, so reaching this
#: bound is a wedged launch, and `e2e_timeout` scales it like every guard in this tier.
SETTLE_SECONDS = 300
#: How long the driver is given to let go once the attached launch has returned. A
#: driver passing over this host's repositories held on for 13 to 15 minutes; one
#: passing over a single local identity lets go in seconds.
LET_GO_SECONDS = 60
#: The branch the journey's own checkout holds and its origin does not, which makes it a
#: candidate the driver's retirement pass asks the origin about.
UNPUBLISHED = "journey/unpublished"

_ONE_NODE: CandidatePlan = {
    "schema_version": 2,
    "concurrency": 2,
    "tasks": [_node(id="only")],
}


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Journey",
            "-c",
            "user.email=journey@example.invalid",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )


def _own_identity(root: Path, home: Path) -> Path:
    """Register a checkout of a local origin under ``home``, with a branch it has not pushed."""
    origin = root / "journey-origin.git"
    checkout = root / "journey"
    _git("init", "-q", "--bare", "-b", "main", str(origin))
    _git("clone", "-q", str(origin), str(checkout))
    (checkout / "README.md").write_text("journey\n", encoding="utf-8")
    _git("add", "-A", cwd=checkout)
    _git("commit", "-qm", "chore: seed", cwd=checkout)
    _git("push", "-q", "origin", "main", cwd=checkout)
    _git("switch", "-qc", UNPUBLISHED, cwd=checkout)
    (checkout / "work.txt").write_text("unpublished\n", encoding="utf-8")
    _git("add", "-A", cwd=checkout)
    _git("commit", "-qm", "feat: unpublished", cwd=checkout)
    _git("switch", "-q", "main", cwd=checkout)
    registered = subprocess.run(
        ["onevcs", "register", str(checkout)],
        env={**os.environ, onevcs_state_snapshot.ONEVCS_HOME: str(home)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert registered.returncode == 0, registered.stderr
    return checkout.resolve()


class HostRegistrations(NamedTuple):
    """What the host's registry names that a process walking it would reach."""

    #: Every registered checkout, resolved.
    checkouts: frozenset[Path]
    #: Every registered identity's origin, as the registry spells it.
    origins: frozenset[str]


def _host_registrations() -> HostRegistrations:
    """Every checkout path and origin the host's registry names, from the suite's copy."""
    registry = onevcs_state_snapshot.host_registry() / onevcs_state_snapshot.REGISTRY
    if not registry.is_file():
        return HostRegistrations(frozenset(), frozenset())
    document = json.loads(registry.read_text(encoding="utf-8"))
    return HostRegistrations(
        checkouts=frozenset(
            Path(entry["path"]).resolve() for entry in document["checkouts"].values()
        ),
        origins=frozenset(str(entry["origin"]) for entry in document["identities"].values()),
    )


class GitProcess:
    """One git process the launch started, as its own trace2 events describe it."""

    def __init__(self, events: list[dict[str, object]]) -> None:
        self.argv: list[str] = []
        self.worktree: Path | None = None
        for event in events:
            match event:
                case {"event": "start", "argv": list(argv)}:
                    self.argv = [str(word) for word in argv]
                case {"event": "def_repo", "worktree": str(worktree)}:
                    self.worktree = Path(worktree).resolve()

    def ran_in(self, checkout: Path) -> bool:
        return self.worktree is not None and (
            self.worktree == checkout or checkout in self.worktree.parents
        )

    def names(self, origin: str) -> bool:
        return any(origin in word for word in self.argv)


def _traced(trace: Path) -> list[GitProcess]:
    processes = []
    for record in sorted(trace.iterdir()):
        events = [
            json.loads(line)
            for line in record.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        processes.append(GitProcess(events))
    return processes


def _driving(run: str, environment: dict[str, str]) -> bool:
    """Whether the driver `launch.json` records for ``run`` is still in the process table.

    Read with `ps`, which only reads, and matched on the executable so a reused pid is
    not taken for the driver.
    """
    launch = Path(environment["ONEPIPELINE_RUNS_DIR"]) / run / "launch.json"
    pid = json.loads(launch.read_text(encoding="utf-8"))["pid"]
    listed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "comm="],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    return "onepipeline" in listed.stdout


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The subject is the
# environment `tests/conftest.py` hands every process of every tier, which every tier's
# key already covers, so a project of its own would sit behind the same inputs as the
# tier it left; the cost is one launch that settles in about fifteen seconds.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `xdist_group` above
# selects an xdist worker under `--dist loadgroup`, not a test tier.
def test_a_launched_driver_walks_only_the_identity_its_journey_registered(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Launch, let the driver finish its idle pass, and read every git process it started.

    Two nodes' worth of concurrency and one node held for a few seconds, because that is
    what an idle pass needs: a pass that dispatched nothing on a run below its ceiling.
    A run whose one node filled its one slot until it settled would never be idle, walk
    nothing, and prove nothing either way.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _launched_environment(tmp_path, oneharness_bin)
    host = _host_registrations()

    # A copy of the export rather than the export itself: registering the control identity
    # in the export would hand it to every later test on this worker, and a copy still
    # carries whatever the export does, so an identity the export leaked shows up below.
    home = tmp_path / "onevcs"
    shutil.copytree(environment[onevcs_state_snapshot.ONEVCS_HOME], home)
    own = _own_identity(tmp_path, home)
    environment[onevcs_state_snapshot.ONEVCS_HOME] = str(home)
    trace = tmp_path / "trace"
    trace.mkdir()
    environment[TRACE] = str(trace)
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)

    plan = tmp_path / "walk.plan.json"
    plan.write_text(json.dumps(_ONE_NODE), encoding="utf-8")
    project = project_from_plan(plan)
    run = project.split(":", 1)[1]
    launched = subprocess.run(
        ["just", "orchestrate", project, "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(SETTLE_SECONDS),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert '"settlement":"complete"' in launched.stdout, launched.stdout + launched.stderr
    limit = deadline(LET_GO_SECONDS)
    while _driving(run, environment):
        assert time.monotonic() < limit, (
            f"run {run}'s driver still held on {LET_GO_SECONDS}s after its launch returned; "
            "a driver passing over a registry of one local identity lets go in seconds"
        )
        time.sleep(1.0)

    processes = _traced(trace)
    walked = [process.argv for process in processes if process.ran_in(own)]
    assert any("ls-remote" in argv for argv in walked), (
        "the driver never asked the journey's own origin about its unpublished branch, so "
        "the trace did not reach an idle pass and an absence below would prove nothing; "
        f"git ran in the journey's checkout as: {walked}"
    )
    reached = [
        (process.worktree, process.argv)
        for process in processes
        if any(process.ran_in(checkout) for checkout in host.checkouts)
        or any(process.names(origin) for origin in host.origins)
    ]
    assert not reached, (
        "a process this suite launched walked a repository this host registers, which is "
        "a pass over its real origin that can retire real branches:\n"
        + "\n".join(f"{worktree}: {' '.join(argv)}" for worktree, argv in reached)
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
