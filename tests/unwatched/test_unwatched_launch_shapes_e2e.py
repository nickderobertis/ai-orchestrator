"""Every `just orchestrate` shape leaves a run this session owns and the verb names.

`onepipeline unwatched` reports the runs *the asked-about session owns*, so the whole
hook is worth nothing on a shape whose launch record names nobody: an unowned run is
passed over in silence, and a manager who forgot the watch on one would never be told.
Ownership is established in one place — `scripts/onepipeline.sh`, which every launching
verb here goes through — but "one place" is a claim about this repository's code and not
about what a launch leaves on disk, and it is exactly the claim that was false for the
ask seam until a journey went and looked.

So each shape is launched for real, through the real recipe and the real driver, and
read back through the installed engine: the run root it created, the session its launch
record names, and the verb's own answer while that run is unsettled and unwatched.

**What keeps each run unsettled is a dispatch that is still working**, and that is a
correction worth recording rather than a detail: a run parked on somebody's approval is
one the engine reports as *converged*, because nothing in it can advance until a person
acts — so it is rightly excluded from this verb, and a plan of human actions alone would
have measured that exclusion instead of what these journeys came for. The dispatch is
this suite's stand-in for the paid model, told to take its time, so a real node is really
running while the verb is asked. A human action does still open the adopted plan, because
converging is exactly what makes `--adopt` reachable: the detached driver has nothing left
to do and lets go, and attesting the action is what gives the fresh driver its work.

`--dag-graph off` for the same reason the `just plan` recipe names it: an observer watches
a run for its whole life and every turn it takes is another dispatch, which is real cost
for a claim about who owns the run and what the verb says of it.

llmlint: ignore-file[tests_mirror_real_usage] One reading here is of a record rather than
of a view, and it is the reading this module exists for: what decides whether the verb
ever reaches a run is the session its **launch record** names, and no view answers that —
a view reports about runs the reader already owns, so it would confirm ownership out of
the same answer whose correctness is the question. The launch, the driver, the dispatch
and the verb are all real, and the record read is the engine's own.

"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, NamedTuple, NewType, NotRequired, TypedDict

import pytest
from fake_backend import AGENT_DELAY_ENV
from harness_indirections import established_indirections
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: This module is its own Nx project's, `unwatched`; see `tests/unwatched/project.json`
#: and the guard in `tests/conftest.py`. No marker routes it: the directory decides.
#:
#: Every launch here reaches `onepipeline` through `uv run`, which waits on this
#: checkout's project-environment lock — and the module beside it holds that lock
#: outright to show the hook does not. `--dist loadgroup` co-locates the tests sharing
#: a group *name* and says nothing about two different ones, so both belong in the one
#: AGENTS.md's four-worker invariant names or a launch here waits out that journey.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The engine this checkout installs from its own pin, asked directly. The recipe is what
#: *launches*; what is read back has to be the engine, because the question is what a
#: manager's hook would be told.
ONEPIPELINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"

#: This repository's own entry point, for the one verb no recipe wraps: attesting the
#: human action an adopted run is parked on.
ONEPIPELINE_SH = REPO_ROOT / "scripts" / "onepipeline.sh"

#: The paid provider's stand-ins and the guard covering the identities `ONEHARNESS_BIN_*`
#: cannot reach. Reached through `helper` rather than composed from this module's own
#: directory, for the reason that function states: a stand-in named at a path this
#: checkout does not have is not a stand-in, and oneharness falls through to a real
#: identity rather than failing.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The session these launches run under, stated as the ambient harness variable a real
#: manager session carries rather than as the value `scripts/onepipeline.sh` derives from
#: it: the derivation is part of what is being held.
LAUNCHING_SESSION = "e2e-unwatched-launch-shapes"

#: The status the verb answers when a run the session owns has nothing watching it.
RUNS_UNWATCHED = 6

#: The engine's refusal, which is what `--adopt` answers while the outgoing driver
#: still holds the run.
REFUSED = 2

#: Every launcher variable an outer dispatch may have exported into this suite. A journey
#: that inherited one would be launching as whoever dispatched it, and its own session
#: would then own nothing.
LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The observer this host attaches by default, off. See the module docstring.
WITHOUT_OBSERVER = ("--dag-graph", "off")

#: How long each stand-in turn takes, which is what gives a poll something to see. A
#: property of the journey rather than of a launch: a real turn takes minutes here.
WORKING_SECONDS = 90


#: A run id and a plan node's id, each a type of its own for the reason
#: `tests/ask_seam/launch/test_launch_ask_seam_e2e.py` gives its own: these journeys pass
#: both beside file paths, session ids and project ids, and every one of them is a `str`.
RunId = NewType("RunId", str)
NodeId = NewType("NodeId", str)


class PlanNode(TypedDict):
    """One node of a plan these journeys launch, in the fields they give it.

    Stated rather than left a bare mapping because a plan has to be *shaped* right for
    any of this to happen at all: the engine refuses a plan it cannot load, and a refused
    plan launches no run for the verb to say anything about. `NotRequired` because a node
    is a small union here — a human action carries `kind` and no persona, an agent node
    carries a persona, and only some carry `deps`.
    """

    id: NodeId
    task: str
    kind: NotRequired[str]
    persona: NotRequired[str]
    deps: NotRequired[list[str]]


class Launch(NamedTuple):
    """One launched run: its id and the environment every read of it shares."""

    run: RunId
    environment: dict[str, str]


def _requirements() -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    if not ONEPIPELINE.is_file():
        pytest.skip(f"this checkout has no installed engine at {ONEPIPELINE}")


def _environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment a launch and every read of its run share."""
    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # The real CLI the stand-in delegates every turn to. Required rather than optional:
    # without it the stand-in cannot answer at all and the node fails `provider-failed`,
    # which settles the run — and a settled run is one this verb rightly says nothing
    # about, so the journey would report the absence as a defect in the verb.
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(__name__))
    # What keeps a run unsettled while the verb is asked: the stand-in for the paid model
    # takes its time, so a real node is really running rather than settling between the
    # launch and the first poll. See the module docstring for why a converged run — which
    # is what a plan of human actions alone produces — measures the wrong thing.
    environment[AGENT_DELAY_ENV] = str(WORKING_SECONDS)
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


def _gate(node_id: NodeId) -> PlanNode:
    """One human action: the node that lets a detached driver converge and let go."""
    return {
        "id": node_id,
        "kind": "human",
        "task": "## What\nApprove.\n\n## Why\nLet the driver converge, so there is an "
        "intact ledger to adopt.\n\n## Acceptance criteria\n- Approved.",
    }


def _work(node_id: NodeId, deps: list[NodeId] | None = None) -> PlanNode:
    """One agent node, which is what is *running* while these journeys ask the verb."""
    node: PlanNode = {
        "id": node_id,
        "persona": "engineer",
        "task": "## What\nReport, changing nothing.\n\n## Why\nA run is unsettled while "
        "one of its nodes is still working.\n\n## Acceptance criteria\n- Reported.",
    }
    if deps is not None:
        node["deps"] = deps
    return node


def _project(tmp_path: Path, run: RunId, tasks: list[PlanNode]) -> str:
    """One launchable project, with its design document approved as a launch requires."""
    written = tmp_path / f"{run}.plan.json"
    written.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "leave a run this session owns and nothing is watching"},
                "name": run,
                "concurrency": 1,
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    return project_from_plan(written, run)


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 300
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the real recipe, run as an operator runs it
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _unwatched(launch: Launch) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the installed engine, named by absolute path
        [str(ONEPIPELINE), "unwatched", "--session", LAUNCHING_SESSION],
        cwd=REPO_ROOT,
        env=launch.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _until_named(launch: Launch, *, seconds: float = 300) -> str:
    """Wait for the verb to name this run, and hand back what it wrote.

    Polled rather than asked once, because a launch and a read of its ledger are two
    processes: the run root, its launch record and the first records of its journal land
    over the launch's own first moments, and asking in that window would be a journey
    about how fast this host is.
    """
    limit = time.monotonic() + e2e_timeout(seconds)
    # Every distinct answer rather than the last one, because the last one is the least
    # informative: a run that has since settled answers with nothing at all, which reads
    # as a verb that said nothing throughout.
    said: list[str] = []
    while time.monotonic() < limit:
        answered = _unwatched(launch)
        if answered.returncode == RUNS_UNWATCHED and launch.run in answered.stdout:
            return answered.stdout
        seen = f"exit {answered.returncode}: {answered.stdout!r} {answered.stderr!r}"
        if seen not in said:
            said.append(seen)
        time.sleep(1)
    raise AssertionError(
        f"the verb never named run {launch.run} as unwatched. What it answered:\n"
        + "\n".join(f"  - {one}" for one in said)
    )


def _owned(launch: Launch, *, seconds: float = 300) -> None:
    """The run's own launch record names the launching session.

    Read off the record rather than inferred from the verb's answer, because the two are
    different claims and the verb's is the weaker one: a verb that ignored ownership
    altogether would name this run just the same.

    Waited for rather than read at once. A launch and this journey are two processes: an
    attached launch is a process this fixture has only just started, and the record is
    written a moment into it — so reading it immediately measures how fast this host is
    rather than what the launch recorded.
    """
    record = Path(launch.environment["ONEPIPELINE_RUNS_DIR"]) / launch.run / "launch.json"
    limit = time.monotonic() + e2e_timeout(seconds)
    while time.monotonic() < limit and not record.is_file():
        time.sleep(0.5)
    assert record.is_file(), f"the launch left no record at {record}"
    named = json.loads(record.read_text(encoding="utf-8")).get("session")
    assert named == LAUNCHING_SESSION, (
        f"run {launch.run} records session {named!r} where this launch ran as "
        f"{LAUNCHING_SESSION!r}: it is owned by nobody this session can be told about, so "
        "the hook would pass over it in silence"
    )


def _attested(launch: Launch, reference: NodeId, *, seconds: float = 180) -> None:
    """Complete the human action the run is parked on, once it is one that can be."""
    limit = time.monotonic() + e2e_timeout(seconds)
    refusal = ""
    while time.monotonic() < limit:
        attested = subprocess.run(  # noqa: S603 - this repository's own onepipeline entry point
            ["bash", str(ONEPIPELINE_SH), "attest", launch.run, reference],
            cwd=REPO_ROOT,
            env=launch.environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )
        if attested.returncode == 0:
            return
        refusal = f"{attested.stdout}{attested.stderr}"
        time.sleep(1)
    raise AssertionError(f"the human action {reference} was never attested: {refusal}")


def _launched(tmp_path: Path, oneharness_bin: str, run: RunId) -> Iterator[Launch]:
    """Set a launch up and stop its run afterwards, whatever the journey did with it."""
    environment = _environment(tmp_path, oneharness_bin)
    launch = Launch(run=run, environment=environment)
    try:
        yield launch
    finally:
        _just("stop", run, environment=environment, seconds=120)


@pytest.fixture(name="attached")
def _attached(tmp_path: Path, oneharness_bin: str) -> Iterator[Launch]:
    """`just orchestrate`, attached, as a manager launches one and stays with it.

    A subprocess rather than a blocking call, because an attached launch returns only
    when the run settles and the whole point of this one is that it has not: the journey
    asks the verb while the launch is still there, which is where a manager who forgot
    the watch is.
    """
    _requirements()
    for launch in _launched(tmp_path, oneharness_bin, RunId("unwatched-orchestrate-attached")):
        project = _project(tmp_path, launch.run, [_work(NodeId("only"))])
        streamed = tmp_path / "attached.log"
        with streamed.open("w", encoding="utf-8") as sink:
            # A file rather than a pipe: an attached launch streams the run's whole merged
            # feed, and a pipe nobody drains fills and wedges the launch it is measuring.
            running = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
                ["just", "orchestrate", project, *WITHOUT_OBSERVER],
                cwd=REPO_ROOT,
                env=launch.environment,
                text=True,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
            )
            try:
                yield launch
            finally:
                running.kill()
                running.wait(timeout=e2e_timeout(120))


@pytest.fixture(name="detached")
def _detached(tmp_path: Path, oneharness_bin: str) -> Iterator[Launch]:
    """`just orchestrate --detach`, the shape a manager supervising several runs uses.

    The one shape whose dispatch is made by a driver the launcher has already returned
    away from, so what that driver recorded is what the launching process exported and
    nothing an attach could add afterwards.
    """
    _requirements()
    for launch in _launched(tmp_path, oneharness_bin, RunId("unwatched-orchestrate-detached")):
        project = _project(tmp_path, launch.run, [_work(NodeId("only"))])
        started = _just(
            "orchestrate", project, "--detach", *WITHOUT_OBSERVER, environment=launch.environment
        )
        assert started.returncode == 0, f"the launch failed:\n{started.stdout}{started.stderr}"
        yield launch


@pytest.fixture(name="adopted")
def _adopted(tmp_path: Path, oneharness_bin: str) -> Iterator[Launch]:
    """`just orchestrate --adopt`, a fresh driver attached to a run whose own has gone.

    Reachable only from the state the gate produces: the detached launch converges on a
    human action nobody has attested and its driver lets go, which is a run nothing is
    driving with an intact ledger. Attesting the action is what gives the fresh driver
    something to do, and the agent node behind it is what is still working while the verb
    is asked — an adopted run whose graph had converged again would rightly be excluded.

    **The order of those two is the whole of this fixture, and getting it wrong measures
    nothing.** The attestation has to come after the original driver is gone: attested
    while it is still alive, that driver picks the work up itself, never converges, and
    `--adopt` is then refused for as long as the work runs — so what finally adopts the
    run is a driver that arrives to find it complete.

    The adoption runs beside the journey for the same reason the attached launch does: it
    stays attached to the run it just took over.
    """
    _requirements()
    for launch in _launched(tmp_path, oneharness_bin, RunId("unwatched-orchestrate-adopted")):
        project = _project(
            tmp_path,
            launch.run,
            [_gate(NodeId("approve")), _work(NodeId("work"), deps=[NodeId("approve")])],
        )
        started = _just(
            "orchestrate", project, "--detach", *WITHOUT_OBSERVER, environment=launch.environment
        )
        assert started.returncode == 0, f"the launch failed:\n{started.stdout}{started.stderr}"
        _until_the_driver_lets_go(launch)
        _attested(launch, NodeId("approve"))
        streamed = tmp_path / "adopted.log"
        with streamed.open("w", encoding="utf-8") as sink:
            adopting = _adopting(launch, sink)
            try:
                yield launch
            finally:
                adopting.kill()
                adopting.wait(timeout=e2e_timeout(120))


def _until_the_driver_lets_go(launch: Launch, *, seconds: float = 300) -> None:
    """Wait for the launch's own driver to be gone, which is what `--adopt` is for.

    Asked of the process the launch recorded rather than of a view over the run, because
    that is exactly the fact `--adopt` refuses on and the two are answered differently: a
    view reports what the run's records say, and this is about whether a process is still
    there. A pid the kernel hands round again inside this window would read as a driver
    that never let go, which is a fixture racing the host rather than a claim about the
    engine — and it would fail this wait rather than pass it wrongly. That is why only
    `ProcessLookupError` ends it: a `PermissionError` is a pid that exists under another
    user, which on this shared host is exactly a reused pid, and returning on it would be
    the wrong pass the sentence before rules out.
    """
    record = Path(launch.environment["ONEPIPELINE_RUNS_DIR"]) / launch.run / "launch.json"
    driver = int(json.loads(record.read_text(encoding="utf-8"))["pid"])
    limit = time.monotonic() + e2e_timeout(seconds)
    while time.monotonic() < limit:
        try:
            os.kill(driver, 0)
        except ProcessLookupError:
            return
        time.sleep(0.5)
    raise AssertionError(
        f"the driver {driver} that launched run {launch.run} never let go of it, so there "
        "is nothing for an adoption to take over"
    )


def _adopting(launch: Launch, sink: IO[str], *, seconds: float = 300) -> subprocess.Popen[str]:
    """Attach a fresh driver, once the outgoing one has let go of the run.

    Retried rather than waited out with a sleep: `--adopt` is for a run nothing is
    driving and refuses one that still is, and how long a converged driver takes to
    finish letting go is a property of a loaded host. The refusal it gives before then is
    what this polls on, exactly as the ask-seam journeys poll their own.
    """
    limit = time.monotonic() + e2e_timeout(seconds)
    refusal = ""
    while time.monotonic() < limit:
        adopting = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
            ["just", "orchestrate", "--adopt", launch.run],
            cwd=REPO_ROOT,
            env=launch.environment,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        # Only the *refusal* is retried, and it is told apart by its own status rather
        # than by how quickly the process ended: an adoption that took the run and then
        # handed back because the run settled is not a refusal, and re-adopting it would
        # be this journey driving the run in circles rather than reading it.
        try:
            adopting.wait(timeout=e2e_timeout(20))
        except subprocess.TimeoutExpired:
            return adopting
        if adopting.returncode != REFUSED:
            return adopting
        refusal = f"exit {adopting.returncode}"
        time.sleep(2)
    raise AssertionError(f"run {launch.run} could never be adopted: {refusal}")


@pytest.mark.parametrize("shape", ["attached", "detached", "adopted"])
def test_every_orchestrate_shape_leaves_a_run_this_session_owns_and_the_verb_names(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """Owned by the launching session, and named by the verb while nothing watches it.

    Both halves, because either alone would pass over the defect the other catches. A run
    the verb names is not necessarily one the session owns — a verb that had stopped
    comparing sessions would name every run on the host — and a run the record attributes
    correctly is not necessarily one the verb reports, which is what a settled document or
    a watcher record left standing would each cost.
    """
    launch = request.getfixturevalue(shape)
    _owned(launch)
    named = _until_named(launch)
    assert "watch it with" in named, (
        f"the verb named run {launch.run} without saying what to do about it:\n{named}"
    )
