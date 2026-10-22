"""A launch journey that fails leaves nothing of its run behind (ai-orchestrator#1467).

A journey's launcher is the one process it holds, and the run under it — the driver, the
observer graph with its `onemessagebus` monitor, the dispatched worker — is a tree of
processes the journey never sees. Teardowns that killed the launcher alone left that tree
running past the test whenever the journey failed before its `just stop`, and an adoption
helper that ended left the engine it had exec'd: memory and CPU held for the life of the
host, a pool slot's worktree kept busy, and a gate refused for the processes it leaked.

So each journey here takes the failure path on purpose — a body that raises after the run
is dispatched, through the same teardown every launch journey in this directory uses
(`served_recipe.stop_launch`) — and then reads the host for what is left: nothing in the
launcher's own session, and nothing carrying this journey's temporary root.

The teardown's `just stop` is refused on purpose too, by asking it from another manager's
session, which the real recipe refuses by name. A stop that answers is enough on its own
to end the run's tree today, so a journey whose stop answered would pass over a teardown
that killed the launcher alone; what the group stop exists for is every stop that does
not — one refused, one timed out on a loaded host, one aimed at a driver already gone —
and a refused one is the case of those the real recipe can be made to give. Everything is
real — the recipes, the engine, its observer and the adoption — and only the paid model
is doubled, at the seam every launch journey here doubles it. What is left is **read**
off `/proc` and never signalled: picking processes to kill out of the process table is
what this host forbids, and what the teardown under test exists to make unnecessary.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Launches of the
installed engine, sharing the fixtures and stand-ins of the code-keyed tier every other
launch journey in this directory sits in; a project of its own would be keyed on the same
workspace twice.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys over the real
`just` recipes rather than a shell suite.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] No marker selects a tier
here; the journeys share their directory's project with the launches they reuse.

llmlint: ignore-file[tests_mirror_real_usage] What a failed journey leaves running reaches
no operator-facing view — the run's views report the run, not the processes outside it —
so the process table is the one place the claim can be read, and it is only read.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import NamedTuple

import pytest
import served_recipe
import test_orchestrate_launch_e2e as launch_journeys
from fake_backend import AGENT_DELAY_ENV
from project_fixtures import helper, project_from_plan
from served_recipe import Stream, serve, stop_launch
from test_adopted_engine_reads_an_adopted_run_e2e import _recorded, adopted_run
from test_orchestrate_launch_e2e import (
    GRAPH_STATE_ENV,
    CandidatePlan,
    LiveRun,
    RunId,
    _dispatched,
    _environment,
    _node,
)
from waits import timeout as e2e_timeout
from waits import until

pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: The stand-in for the paid model, which the dispatched worker's turn runs as.
FAKE_BACKEND = helper("fake_backend.py")

#: How long the stand-in holds the dispatched worker: far past the journey, so the worker
#: is still running when the journey fails and its teardown is what has to end it.
HELD_SECONDS = 600

#: The session the teardown's `just stop` is asked from: not the launching one, so the
#: recipe refuses it as another manager's run and leaves the group stop to end the tree.
ANOTHER_MANAGER = "e2e-another-manager"

#: The host directory issue #1467 identified as collecting leaked member scratch.
# The task explicitly requires checking this historical leak directory even if an upstream
# default moves. The positive control below separately proves scratch under the configured root.
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] Task-required historical leak path.
HOST_GRAPH_RUNS = Path.home() / ".local" / "state" / "oneagentgraph" / "runs"


class InjectedFailure(Exception):
    """The failure a journey body raises on purpose, after its run is dispatched."""


class Left(NamedTuple):
    """One live process this journey can see, read off `/proc` and never signalled."""

    pid: int
    session: int
    command: str
    #: Whether its environment or command line names the journey's temporary root.
    carries_root: bool


def _processes(root: Path) -> list[Left]:
    """Every live process this user can read, with its session and whether it names `root`.

    A zombie is left out: it has already exited, holds nothing, and is its new parent's to
    reap. A process that vanishes or is unreadable while the table is read is skipped,
    since it is either gone or another user's.
    """
    named = str(root)
    found: list[Left] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            environ = (entry / "environ").read_bytes().split(b"\0")
        except OSError:
            continue
        # The command name in `stat` is parenthesised and may itself hold spaces or
        # parentheses, so the fields are counted from the last closing one.
        fields = stat[stat.rindex(")") + 2 :].split()
        if fields[0] == "Z":
            continue
        values = [value.decode(errors="replace") for value in (*argv, *environ)]
        found.append(
            Left(
                pid=int(entry.name),
                session=int(fields[3]),
                command=" ".join(part.decode(errors="replace") for part in argv).strip(),
                carries_root=any(value.endswith(named) or f"{named}/" in value for value in values),
            )
        )
    return found


def _owned(session: int, root: Path) -> list[Left]:
    """What is left in the launcher's `session`, or carries the journey's `root`."""
    return [
        process
        for process in _processes(root)
        if process.session == session or process.carries_root
    ]


def _nothing_left(session: int, root: Path) -> None:
    """Wait for the teardown's last kill to land, then fail naming whatever outlived it."""
    until(
        f"every process of the launch in session {session} or carrying {root} to end",
        lambda: not _owned(session, root),
        seconds=30,
        state=lambda: (
            "still running:\n"
            + "\n".join(
                f"  {left.pid} (session {left.session}): {left.command}"
                for left in _owned(session, root)
            )
        ),
    )


def _stopped_by_another_manager(environment: dict[str, str]) -> None:
    """Make the teardown's `just stop` one asked from another session, which it refuses.

    The teardown reads this same environment, so the change reaches its stop and nothing
    the run already started.
    """
    environment["CLAUDE_CODE_SESSION_ID"] = ANOTHER_MANAGER


def _host_runs() -> set[str]:
    return (
        {entry.name for entry in HOST_GRAPH_RUNS.iterdir()} if HOST_GRAPH_RUNS.is_dir() else set()
    )


def _naming(directory: Path, token: str) -> bool:
    """Whether any file under `directory` holds `token`, read as bytes."""
    try:
        files = [path for path in directory.rglob("*") if path.is_file()]
    except OSError:
        return False
    for path in files:
        try:
            if token.encode() in path.read_bytes():
                return True
        except OSError:
            continue
    return False


def _held_launch(tmp_path: Path, oneharness_bin: str, token: str) -> Generator[LiveRun]:
    """An attached launch, its observer graph on, whose one worker is held far past the journey.

    The observer is left on — this journey does not pass `--dag-graph off` — because the
    monitor it runs is the process the defect left behind, and a run without one would
    prove the teardown against a smaller tree than an operator's.
    """
    run = RunId("teardown-reaps-e2e")
    environment = _environment(tmp_path, oneharness_bin)
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    plan = tmp_path / f"{run}.plan.json"
    held: CandidatePlan = {
        "schema_version": 2,
        "name": run,
        "tasks": [
            _node(
                id="held",
                task=(
                    f"## What\nReport {token}, taking your time.\n\n## Why\nSo the journey "
                    "fails while this dispatch runs.\n\n## Acceptance criteria\n- Reported."
                ),
            )
        ],
    }
    plan.write_text(json.dumps(held), encoding="utf-8")
    with (tmp_path / "launch.log").open("w", encoding="utf-8") as streaming:
        launch = serve(
            [
                "just",
                "orchestrate",
                project_from_plan(plan),
                "--success-hook=",
                "--failure-hook=",
            ],
            environment,
            stdout=streaming,
            stderr=subprocess.STDOUT,
        )
    try:
        _dispatched(LiveRun(environment, run, launch))
        yield LiveRun(environment, run, launch)
    finally:
        stop_launch(run, environment, launch)


def test_a_launch_whose_journey_raises_leaves_no_process_and_no_host_graph_state(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A journey that fails mid-run leaves nothing running and no scratch under the host's state.

    The failure is raised into the launch's own teardown, as pytest finalises a fixture
    whose test failed: the launcher is still attached, the worker still held, the observer
    still watching — and the teardown's stop is refused, so the run's own stop ends
    nothing. Two claims, read once the teardown has run. Nothing is left in the
    launcher's session or carrying this journey's temporary root. And `oneagentgraph` put
    the run's member scratch inside that root — where it went, as the control, beside the
    host's own runs directory gaining nothing that names this launch.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    token = f"teardown-{uuid.uuid4().hex}"
    before = _host_runs()
    launching = _held_launch(tmp_path, oneharness_bin, token)
    live = next(launching)
    session = live.launch.pid

    def in_session() -> list[Left]:
        return [process for process in _owned(session, tmp_path) if process.session == session]

    # The precondition: a tree for the teardown to reap, with the dispatched worker in it.
    until(
        "the dispatched worker to run in the launcher's session",
        lambda: any(FAKE_BACKEND.name in process.command for process in in_session()),
        seconds=120,
        state=lambda: f"the session held: {[process.command[:120] for process in in_session()]}",
    )

    _stopped_by_another_manager(live.environment)
    with pytest.raises(InjectedFailure):
        launching.throw(InjectedFailure("the journey failed after its run was dispatched"))

    _nothing_left(session, tmp_path)
    assert _recorded(live.run, live.environment, "run-stopped") == 0, (
        "the teardown's stop was not refused, so the run's own stop could have ended its tree"
    )
    added = sorted(_host_runs() - before)
    ours = [name for name in added if _naming(HOST_GRAPH_RUNS / name, token)]
    assert not ours, f"the launch left member scratch under {HOST_GRAPH_RUNS}: {ours}"
    graph_state = Path(live.environment[GRAPH_STATE_ENV])
    assert graph_state.is_relative_to(tmp_path), graph_state
    assert _naming(graph_state, token), (
        f"no member scratch under {graph_state} names the dispatched task, so the launch "
        "put it somewhere this journey did not name"
    )


def test_an_adoption_whose_journey_raises_leaves_no_process_of_the_adopted_engine(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The adoption helper's teardown ends the engine it adopted, with the run never stopped.

    The run is adopted and its adopting driver is dispatching again when the journey fails,
    and the teardown's stop is refused, so nothing but the group stop ends it: the escaped
    process this reads for is the adopted engine itself, which the old teardown — the
    `just` launcher killed alone — left running.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    adopting = adopted_run(tmp_path, oneharness_bin)
    adopted = next(adopting)
    session = adopted.adopting.pid
    assert adopted.adopting.poll() is None, "the adoption was not attached when the journey failed"

    stops = _recorded(adopted.run, adopted.environment, "run-stopped")
    _stopped_by_another_manager(adopted.environment)
    with pytest.raises(InjectedFailure):
        adopting.throw(InjectedFailure("the journey failed while the adoption drove the run"))

    _nothing_left(session, tmp_path)
    assert _recorded(adopted.run, adopted.environment, "run-stopped") == stops, (
        "the teardown's stop was not refused, so the run's own stop could have ended its tree"
    )


def test_group_stop_escalates_when_the_launcher_ignores_term(tmp_path: Path) -> None:
    """Even an unresponsive leader and its child are killed after TERM's grace period."""
    ready = tmp_path / "ready"
    process = serve(
        [
            sys.executable,
            "-c",
            "import pathlib, signal, subprocess, sys, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
            "pathlib.Path(sys.argv[1]).touch(); time.sleep(600)",
            str(ready),
        ]
    )
    try:
        until(
            "the leader to ignore TERM and start its child",
            ready.exists,
            seconds=30,
            state=lambda: f"leader status: {process.poll()}, marker: {ready}",
        )
    finally:
        served_recipe.stop(process)
    assert process.returncode == -9
    _nothing_left(process.pid, tmp_path)


def test_attached_timeout_reaps_the_group_and_preserves_output(tmp_path: Path) -> None:
    """The synchronous helper's timeout carries the output and leaves no child running."""
    marker = str(tmp_path / "attached")
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        served_recipe.attached(
            [
                sys.executable,
                "-c",
                "import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
                "print(sys.argv[1], flush=True); time.sleep(600)",
                marker,
            ],
            dict(os.environ, TEARDOWN_TEST_ROOT=str(tmp_path)),
            seconds=2,
        )
    assert marker in raised.value.output
    until(
        "the timed-out attached command and child to exit",
        lambda: not any(process.carries_root for process in _processes(tmp_path)),
        seconds=30,
        state=lambda: str([p for p in _processes(tmp_path) if p.carries_root]),
    )


def test_a_stop_recipe_timeout_still_reaps_the_launcher(
    tmp_path: Path, oneharness_bin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a stop recipe that cannot answer before its deadline leaves no launcher."""
    environment = _environment(tmp_path, oneharness_bin)
    process = serve([sys.executable, "-c", "import time; time.sleep(600)"], environment)
    original_timeout = e2e_timeout
    # Force the real stop recipe's deadline, without substituting the recipe or subprocess.
    monkeypatch.setattr(
        served_recipe,
        "e2e_timeout",
        lambda seconds: 0.000001 if seconds == 120 else original_timeout(seconds),
    )
    with pytest.raises(subprocess.TimeoutExpired):
        stop_launch("stop-timeout-e2e", environment, process)
    assert process.returncode is not None
    _nothing_left(process.pid, tmp_path)


@pytest.mark.parametrize("expires", [False, True])
def test_attached_launch_stops_run_before_signalling_or_reaping(
    tmp_path: Path, oneharness_bin: str, monkeypatch: pytest.MonkeyPatch, expires: bool
) -> None:
    """A returned or timed-out launch asks the real stop recipe before group cleanup."""
    environment = _environment(tmp_path, oneharness_bin)
    original_attached = served_recipe.attached
    original_stop = served_recipe.stop
    stopped: list[str] = []

    def observed_attached(
        command: list[str], environment: dict[str, str], *, seconds: float = 300
    ) -> subprocess.CompletedProcess[str]:
        result = original_attached(command, environment, seconds=seconds)
        if command[:2] == ["just", "stop"]:
            stopped.append(command[2])
        return result

    def observed_stop(*processes: subprocess.Popen[str]) -> None:
        # The stop recipe has its own group to clean up. Only the launcher's cleanup
        # owes the ordering assertion; all subprocesses still use the real cleanup.
        try:
            for process in processes:
                if isinstance(process.args, list) and process.args[1] == "orchestrate":
                    assert stopped == ["teardown-order-missing"], stopped
                    assert process.returncode is None, "launcher was reaped before stop"
        finally:
            original_stop(*processes)

    monkeypatch.setattr(served_recipe, "attached", observed_attached)
    monkeypatch.setattr(served_recipe, "stop", observed_stop)
    command = ["just", "orchestrate", "plans:teardown-order-missing"]
    if expires:
        with pytest.raises(subprocess.TimeoutExpired):
            served_recipe.attached(command, environment, seconds=0)
    else:
        assert served_recipe.attached(command, environment).returncode != 0
    assert stopped == ["teardown-order-missing"]
    _nothing_left(-1, tmp_path)


@pytest.mark.parametrize("helper_kind", ["adopted", "unwatched"])
@pytest.mark.parametrize("seconds", [0.01, 8])
def test_adoption_attempt_failure_reaps_every_attempt(
    tmp_path: Path,
    oneharness_bin: str,
    monkeypatch: pytest.MonkeyPatch,
    helper_kind: str,
    seconds: float,
) -> None:
    """A real adoption refused by ownership, or its deadline, leaves no attempted driver."""
    import test_adopted_engine_reads_an_adopted_run_e2e as adopted_helpers
    from project_fixtures import helper as fixture_helper

    # The unwatched tier is deliberately outside pytest's shared import path.
    source = fixture_helper("../unwatched/test_unwatched_launch_shapes_e2e.py")
    spec = importlib.util.spec_from_file_location("unwatched_teardown_journey", source)
    assert spec is not None and spec.loader is not None
    unwatched_helpers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = unwatched_helpers
    spec.loader.exec_module(unwatched_helpers)
    module = adopted_helpers if helper_kind == "adopted" else unwatched_helpers
    attempts: list[subprocess.Popen[str]] = []
    original_serve = served_recipe.serve

    def observed_serve(
        command: list[str],
        environment: dict[str, str] | None = None,
        *,
        stdin: Stream = None,
        stdout: Stream = subprocess.PIPE,
        stderr: Stream = subprocess.PIPE,
    ) -> subprocess.Popen[str]:
        process = original_serve(command, environment, stdin=stdin, stdout=stdout, stderr=stderr)
        attempts.append(process)
        return process

    launching = _held_launch(tmp_path, oneharness_bin, "adoption-attempt-cleanup")
    live = next(launching)
    try:
        _stopped_by_another_manager(live.environment)
        monkeypatch.setattr(module, "serve", observed_serve)
        with (
            (tmp_path / "attempt.log").open("w", encoding="utf-8") as sink,
            pytest.raises(AssertionError, match="could never be adopted|never recorded adopted"),
        ):
            if helper_kind == "adopted":
                monkeypatch.setattr(adopted_helpers, "PATIENCE_SECONDS", seconds)
                adopted_helpers._adopting(live.run, live.environment, sink)
            else:
                module._adopting(module.Launch(live.run, live.environment), sink, seconds=seconds)
        assert attempts, "the deadline expired before any adoption was attempted"
        assert all(attempt.returncode is not None for attempt in attempts)
        sessions = {attempt.pid for attempt in attempts}
        until(
            "every failed adoption attempt's session to empty",
            lambda: not any(p.session in sessions for p in _processes(tmp_path)),
            seconds=30,
            state=lambda: str([p for p in _processes(tmp_path) if p.session in sessions]),
        )
    finally:
        launching.close()
    _nothing_left(live.launch.pid, tmp_path)


def test_signal_terminated_launch_preserves_its_status(
    tmp_path: Path, oneharness_bin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A launcher killed by a signal reports that signal after the run-stop cleanup."""
    original_serve = served_recipe.serve

    def signalled_serve(
        command: list[str],
        environment: dict[str, str] | None = None,
        *,
        stdin: Stream = None,
        stdout: Stream = subprocess.PIPE,
        stderr: Stream = subprocess.PIPE,
    ) -> subprocess.Popen[str]:
        process = original_serve(command, environment, stdin=stdin, stdout=stdout, stderr=stderr)
        if command[:2] == ["just", "orchestrate"]:
            os.kill(process.pid, signal.SIGKILL)
        return process

    monkeypatch.setattr(served_recipe, "serve", signalled_serve)
    result = served_recipe.attached(
        ["just", "orchestrate", "plans:signal-status-missing"],
        _environment(tmp_path, oneharness_bin),
    )
    assert result.returncode == -signal.SIGKILL
    _nothing_left(-1, tmp_path)


@pytest.mark.parametrize("early_failure", [False, True])
def test_observer_fixture_stops_run_before_reaping_its_launcher(
    tmp_path: Path, oneharness_bin: str, monkeypatch: pytest.MonkeyPatch, early_failure: bool
) -> None:
    """The observer's returned and failed measurements keep the group leader until stop."""
    launchers: list[subprocess.Popen[str]] = []
    events: list[str] = []
    original_serve = served_recipe.serve
    original_observe = launch_journeys._observe
    original_attached = served_recipe.attached
    original_stop = served_recipe.stop

    def observed_serve(
        command: list[str],
        environment: dict[str, str] | None = None,
        *,
        stdin: Stream = None,
        stdout: Stream = subprocess.PIPE,
        stderr: Stream = subprocess.PIPE,
    ) -> subprocess.Popen[str]:
        process = original_serve(command, environment, stdin=stdin, stdout=stdout, stderr=stderr)
        launchers.append(process)
        return process

    def observed_measurement(
        launcher: subprocess.Popen[str], recorded: Path, stream: Path, environment: dict[str, str]
    ) -> launch_journeys.ObservedLaunch:
        if early_failure:
            os.kill(launcher.pid, signal.SIGKILL)
            until(
                "the observer launcher to exit before its judge runs",
                lambda: served_recipe.exit_code(launcher) is not None,
                seconds=30,
                state=lambda: str(launcher.pid),
            )
            assert not recorded.exists(), "the observer ran before the early failure was injected"
        result = original_observe(launcher, recorded, stream, environment)
        assert result.handed_back, result.reported
        return result

    def observed_attached(
        command: list[str], environment: dict[str, str], *, seconds: float = 300
    ) -> subprocess.CompletedProcess[str]:
        unreaped = launchers[0].returncode is None
        if command[:2] == ["just", "stop"]:
            events.append("stop-started")
        result = original_attached(command, environment, seconds=seconds)
        if command[:2] == ["just", "stop"]:
            events.append("stop-returned")
            assert unreaped, "observer launcher reaped before stop recipe"
        return result

    def observed_stop(*processes: subprocess.Popen[str]) -> None:
        try:
            if any(process is launchers[0] for process in processes):
                assert events == ["stop-started", "stop-returned"], events
                assert launchers[0].returncode is None, (
                    "observer launcher reaped before group cleanup"
                )
                events.append("group-cleanup")
        finally:
            original_stop(*processes)

    monkeypatch.setattr(launch_journeys, "serve", observed_serve)
    monkeypatch.setattr(launch_journeys, "_observe", observed_measurement)
    monkeypatch.setattr(served_recipe, "attached", observed_attached)
    monkeypatch.setattr(served_recipe, "stop", observed_stop)
    observing = launch_journeys._observed_launch(tmp_path, oneharness_bin)
    try:
        if early_failure:
            with pytest.raises(pytest.fail.Exception, match="launch ended before the observer"):
                next(observing)
        else:
            next(observing)
    finally:
        observing.close()
        # Keep the regression probe safe even when its ordering assertions fail.
        original_stop(*(launcher for launcher in launchers if launcher.returncode is None))
    assert events == ["stop-started", "stop-returned", "group-cleanup"]
    _nothing_left(launchers[0].pid, tmp_path)
