"""What `just host` and `just status` say about the machine a run is running in.

Both engine views report exhaustively on what is *running* and not at all on what it is
running *in*, and two supervision failures came out of that gap — a driver that died of a
full disk and reported only a dead driver, and a live rendezvous belonging to a passing
test that no manager-facing view could tell from one belonging to the run being
supervised. That rendezvous was an `onepipeline channel serve` then; on this host it is
now an `onemessagebus ask` or `onemessagebus serve` holding a run's channel open.
`AGENTS.md` and `docs/orchestration.md` carry what each cost.
`scripts/supervision-readings.py` is the reading; these journeys hold the two recipes to
giving it, to giving it where a supervisor can see it, and to still giving everything the
engine gave before it.

The engine is the real one and so are the recipes, the wrapper scripts and the shell: a
view reaches no model, and a doubled engine would prove nothing about a rendering. The run
roots are built rather than recorded for the reason `tests/e2e/probe_run_root.py` gives.

llmlint: ignore-file[tool_output_is_signal] the rendered view is these viewing
commands' whole product, so the assertions are on what they printed.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Each journey is behind
the edge that covers what it reads, and there are three. Those that drive a view carry
`reads_recipes` and are charged to `orchestrator:test-recipes`, keyed on the justfile,
`scripts/**` and the modules that collect them — exactly what they drive. The one that
spends a real `just orchestrate`, whose verdict depends on `graphs/`, `personas/`,
`config/` and the Markdown records under `examples/`, none of which that key covers,
carries `reads_docs` and is charged to the whole-workspace tier; putting it in the narrow
one would replay a green for a tree whose launch had changed. The one that drives the
filter alone with a mode word it does not know reads nothing outside the code-keyed tier,
and carries no marker.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys rather than a
shell test suite, and the split they are held to is the one above: each is charged to the
narrowest key that covers what it reads.

llmlint: ignore-file[modern_domain_modeling] `Probe` models what these journeys share;
its `run` stays a `str` for the reason `scripts/supervision-readings.py` gives about its
own — a `NewType` here is verified by nothing, since this repository's type checker reads
`orchestrator/` alone and the script it would have to agree with cannot be imported.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] The markers here move
journeys between three targets of the *same* project, which is the mechanism
`tests/conftest.py` documents and enforces rather than a way around a project boundary: it
refuses a marked journey that reads anything its tier's key does not cover, and
`tests/test_nx_cache_scope.py` holds the four selectors to a partition of the suite. A
project of this module's own is what `plan-tooling` and each ask-seam journey have, and
they exist because a whole tier of theirs costs what a host tool costs; one module is not
that, and a second project reading the same files would give `nx affected` two answers to
one question.

llmlint: ignore-file[e2e_not_mocked] One journey — the real-launch control — points the
launch at `tests/e2e/fake_backend.py` instead of a paid provider, which is the one double
this repository's own invariant allows. Everything the launch then does is real: the
recipe, the wrapper scripts, the engine, the plan store, the run root it writes. A journey
that reached a paid model would cost a quota per run of the suite and prove nothing
further about a view that reads a directory.

llmlint: ignore-file[tests_mirror_real_usage] Twenty journeys read a run root this
module builds, and one — `test_the_readings_answer_over_a_run_root_a_real_launch_wrote` —
renders both views over one `just orchestrate` wrote, which is the control that makes the
other twenty statements about a shape the engine really produces. The built roots are
not a saving: three of them need conditions no real launch can be asked for, listed in
`tests/e2e/probe_run_root.py`. Everything from the recipe down is real in every one of
them, the rendezvous included: a real `onemessagebus ask` or `serve`, except where a
journey needs an argv no real bus process can carry, and there the stand-in says so.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from example_records import isolated_examples
from probe_run_root import Probe, record_dispatch, run_name, run_root
from waits import timeout as e2e_timeout
from waits import until

#: This checkout, resolved from this file rather than imported: every journey but one
#: below is in the recipe-scoped tier, whose key is the justfile and `scripts/**`, and
#: importing `orchestrator` would be a read outside it.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where `just status`'s run-scoped lines stop and the host-wide provider report
#: begins. `AGENTS.md` tells a supervisor to cut a watch here before matching words in
#: what is above it, so a reading below this line is one no watch following this
#: repository's own guidance would ever see — which is what these journeys hold.
PROVIDERS = "  providers:"

#: What every reading this repository adds is prefixed with, so an operator can tell
#: the two documents apart and a watch has one anchor per reading.
DISK = "  disk"
RENDEZVOUS = "  rendezvous"

#: A directory on a filesystem of its own, which is what the two-reading half of the disk
#: journey needs and what no `tmp_path` can be: pytest's is under `/tmp`, which on this
#: host is the same device as everything else. `/dev/shm` is a tmpfs every Linux mounts
#: separately, and the journey below asserts the two-line answer only after proving the
#: one-line answer it is contrasted with.
SECOND_FILESYSTEM = "/dev/shm"

#: The installed bus every real rendezvous here is, and the configuration this host starts
#: each one with: `scripts/ask-manager.sh` execs `ask` and `graphs/dag-scope.yaml` execs
#: `serve`, both over `config/onemessagebus.yaml`, both naming the run's channel with
#: `--transport-dir <runs root>/<run>/channel`.
BUS = REPO_ROOT / ".venv" / "bin" / "onemessagebus"
BUS_CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"
QUEUE = "surfaces"
#: The binding the judge side serves, as `graphs/dag-scope.yaml` names it: the bus refuses
#: a `--codec` the configuration does not declare, at exit 2, before it holds anything open.
CODEC = "monitor"

#: One blocking question, as the ask shim encodes it — the whole of `ask`'s stdin.
QUESTION = '{"kind":"planner-question","message":"probe","source":"proposal"}\n'

#: Durations move between two invocations seconds apart, and the comparison journeys
#: below run each view twice — once through the engine and once through the recipe —
#: to prove nothing of the first was lost. Normalising them is what makes that
#: comparison about the *lines* rather than about how long the second one took.
DURATION = re.compile(r"\b\d+(?:h\d+m|m\d+s|[hms])\b")


def _environment(runs_root: Path) -> dict[str, str]:
    """The ambient values a supervisor's read reaches these recipes with."""
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs_root)
    # `scripts/onepipeline.sh` derives the reading session's identity from the harness
    # variable a real manager session carries, and ownership is a comparison — a view
    # that did not identify itself matches no run.
    environment["CLAUDE_CODE_SESSION_ID"] = "supervision-readings-e2e"
    return environment


def _view(
    recipe: str, *arguments: str, runs_root: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """One of the two views, through the real recipe."""
    return subprocess.run(
        ["just", recipe, *arguments],
        cwd=REPO_ROOT,
        env={**_environment(runs_root), **(env or {})},
        capture_output=True,
        text=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _engine(verb: str, *arguments: str, runs_root: Path) -> subprocess.CompletedProcess[str]:
    """The same view straight from the published verb, with nothing of this host's beside it."""
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs_root)
    return subprocess.run(
        ["uv", "run", "onepipeline", verb, *arguments],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _above_providers(rendered: str) -> list[str]:
    """The run-scoped half of a view: everything above the line a watch is cut at.

    The provider report below it is `oneagentgraph health`'s own, describes the host
    rather than the run, and carries an observation timestamp — so it is where a
    comparison of two invocations has to stop rather than something either view lost.
    """
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(PROVIDERS):
            return lines[:index]
    return lines


def _bus_processes(session: int, verb: str) -> list[int]:
    """Every live bus process running `verb` inside the process session `session`.

    Scoped to the session this journey started, so what it counts is its own rendezvous
    and never the bus processes other journeys on this shared host leave running.
    """
    found: list[int] = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            if os.getsid(int(name)) != session:
                continue
            argv = Path(f"/proc/{name}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if (
            len(argv) > 1
            and Path(os.fsdecode(argv[0])).name == BUS.name
            and os.fsdecode(argv[1]) == verb
        ):
            found.append(int(name))
    return found


def _question_waiting(channel: Path) -> bool:
    """Whether the channel's queue holds the question, read through the bus's own `status`."""
    looked = subprocess.run(
        [str(BUS), "status", QUEUE, "--transport-dir", str(channel), "--format", "json"],
        capture_output=True,
        text=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    return looked.returncode == 0 and any(
        queue.get("waiting") or queue.get("pending") is not None
        for queue in json.loads(looked.stdout or "[]")
    )


@contextlib.contextmanager
def _serving(
    run: str,
    runs_root: Path,
    *,
    verb: str = "ask",
    through_launcher: bool = False,
    relative: bool = False,
    in_environment: bool = False,
) -> Iterator[subprocess.Popen[str]]:
    """A real rendezvous: a bus process holding one run's channel open for a reply.

    `ask` is what a dispatched agent's blocking question is on this host — one question
    queued and nobody answering it — and `serve` is what an observer member's judge side
    is, reading frames for as long as its member runs. Both are started the way this host
    starts them, rather than imitated. `relative` names the channel relative to the
    process's working directory, which is how the judge side names it
    (`${ONEPIPELINE_RUNS_DIR:-runs}/<run>/channel` from the checkout); `in_environment`
    names it in `ONEMESSAGEBUS_TRANSPORT_DIR` instead of on the command line, which the
    bus accepts and which leaves argv naming no run at all.
    """
    bound = int(e2e_timeout(120))
    channel = runs_root / run / "channel"
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUN_ID"] = run
    cwd = runs_root.parent if relative else REPO_ROOT
    transport = str(Path(runs_root.name) / run / "channel") if relative else str(channel)
    named = ["--config", str(BUS_CONFIG)]
    if in_environment:
        environment["ONEMESSAGEBUS_TRANSPORT_DIR"] = transport
    else:
        named += ["--transport-dir", transport]
    if verb == "ask":
        command = [str(BUS), "ask", QUEUE, "--blocking", *named, "--timeout", str(bound)]
    else:
        command = [str(BUS), "serve", QUEUE, "--codec", CODEC, *named]
        command += ["--session-seconds", str(bound)]
    # A `timeout` in front is what somebody bounding a wait by hand types, and unlike
    # `uv run` — which execs, measured — it stays as a process of its own carrying the
    # same argv words as the bus below it.
    launcher = ["timeout", str(bound)] if through_launcher else []
    served = subprocess.Popen(
        [*launcher, *command],
        cwd=cwd,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        # Its own process session, so the cleanup below reaches every process this
        # rendezvous is made of rather than only the one this holds a handle on, and so
        # the wait below counts this journey's bus processes and nobody else's.
        start_new_session=True,
    )
    try:
        assert served.stdin is not None
        if verb == "ask":
            served.stdin.write(QUESTION)
            served.stdin.close()
        # A judge side's stdin stays open: it serves for as long as its member writes.
        until(
            f"the {verb} rendezvous for run {run} to be holding its channel open",
            lambda: (
                bool(_bus_processes(served.pid, verb))
                and (verb != "ask" or _question_waiting(channel))
            ),
            seconds=60,
            state=lambda: f"exit status {served.poll()}",
        )
        yield served
    finally:
        # Started here, so ending it here is this journey's own session to signal and
        # nobody else's — the one process on this host these tests may.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(served.pid, signal.SIGKILL)
        served.communicate(timeout=e2e_timeout(30))


def _stand_in(*argv: str) -> subprocess.Popen[bytes]:
    """A process carrying `argv` after a sleeper — the shape no real bus process can take.

    Used only where a journey needs a command line the bus itself would refuse, such as a
    channel directory that forges a line of the report: the reading matches argv words,
    so a sleeper carrying them is exactly what an arbitrary process on a shared host is.
    """
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({int(e2e_timeout(120))})", *argv]
    )


def _line_for(rendered: str, pid: int) -> list[str]:
    """The rendezvous lines naming one process."""
    return [
        line
        for line in rendered.splitlines()
        if line.startswith(RENDEZVOUS) and f"pid {pid}:" in line
    ]


@pytest.fixture
def probe(tmp_path: Path) -> Probe:
    """A runs root holding one run, private to the journey that asked for it."""
    root = tmp_path / "runs"
    run = run_name()
    run_root(root, run)
    return Probe(root=root, run=run)


def _rendezvous(rendered: str, run: str) -> list[str]:
    """The rendezvous lines this journey's own run is named in."""
    return [line for line in rendered.splitlines() if line.startswith(RENDEZVOUS) and run in line]


@pytest.mark.reads_recipes
def test_the_status_view_reports_free_space_above_the_line_a_watch_is_cut_at(
    probe: Probe,
) -> None:
    """The resource whose exhaustion stops everything, where a supervisor can see it."""
    rendered = _view("status", probe.run, runs_root=probe.root)

    assert rendered.returncode == 0, rendered.stderr
    disk = [line for line in rendered.stdout.splitlines() if line.startswith(DISK)]
    assert disk, (
        "the status view must report the free space of the filesystem the run's working "
        f"directories are on; it printed {rendered.stdout!r}"
    )
    assert any("GiB free of" in line for line in disk), (
        f"a free-space reading names what is free and of how much; it printed {disk}"
    )

    above = _above_providers(rendered.stdout)
    assert len(above) < len(rendered.stdout.splitlines()), (
        "this view's own provider report is the boundary a watch is told to cut at, and "
        f"it is not in what the view printed: {rendered.stdout!r}"
    )
    assert [line for line in above if line.startswith(DISK)] == disk, (
        "every disk reading has to sit above the provider block, which is where this "
        "repository's own guidance tells a supervisor to cut this view — below it, no "
        f"watch that follows that guidance would ever see it: {rendered.stdout!r}"
    )


@pytest.mark.reads_recipes
def test_the_status_view_still_gives_every_reading_the_engine_gave(probe: Probe) -> None:
    """What this host adds is additional; nothing the published view said is replaced."""
    engine = _engine("status", probe.run, runs_root=probe.root)
    recipe = _view("status", probe.run, runs_root=probe.root)

    assert engine.returncode == 0, engine.stderr
    assert recipe.returncode == 0, recipe.stderr

    published = [DURATION.sub("<age>", line) for line in _above_providers(engine.stdout)]
    kept = [
        DURATION.sub("<age>", line)
        for line in _above_providers(recipe.stdout)
        if not line.startswith(DISK)
    ]
    assert kept == published, (
        "the recipe must write back everything the published view printed, in order, and "
        f"add its own reading beside it; it printed {recipe.stdout!r}"
    )


@pytest.mark.reads_recipes
def test_the_host_view_reports_free_space_and_names_a_rendezvous_with_its_run(
    probe: Probe,
) -> None:
    """A live rendezvous is reported with the run whose question it is holding open."""
    with _serving(probe.run, probe.root) as served:
        rendered = _view("host", runs_root=probe.root)

    assert rendered.returncode == 0, rendered.stderr
    assert [line for line in rendered.stdout.splitlines() if line.startswith(DISK)], (
        f"the host view must report free space too; it printed {rendered.stdout!r}"
    )
    rendezvous = _rendezvous(rendered.stdout, probe.run)
    assert len(rendezvous) == 1, (
        "one live rendezvous is one line, and it is named with the run its question is "
        "bound to — which is what tells one belonging to a test from one belonging to "
        f"the run being supervised: {rendered.stdout!r}"
    )
    assert f"pid {served.pid}" in rendezvous[0], rendezvous[0]


@pytest.mark.reads_recipes
def test_a_rendezvous_bound_to_no_run_here_is_reported_rather_than_omitted(
    probe: Probe, tmp_path: Path
) -> None:
    """The case a manager most needs to see is the one a view cannot attribute.

    This is the incident exactly: a real rendezvous, live, holding a real question with a
    real correlation token — and bound to a run in somebody else's runs root, which is
    what a passing journey of this repository's own suite leaves on the host every time.
    """
    elsewhere = tmp_path / "elsewhere"
    foreign = run_name()
    run_root(elsewhere, foreign)

    with _serving(foreign, elsewhere) as served:
        rendered = _view("host", runs_root=probe.root)

    assert rendered.returncode == 0, rendered.stderr
    rendezvous = _rendezvous(rendered.stdout, foreign)
    assert len(rendezvous) == 1, rendered.stdout
    assert f"pid {served.pid}" in rendezvous[0], rendezvous[0]
    assert "no run this host is supervising" in rendezvous[0], (
        "a rendezvous this view cannot attribute has to be reported as that rather than "
        f"left out, because it is the one a manager most needs to see: {rendezvous[0]!r}"
    )


@pytest.mark.reads_recipes
def test_a_rendezvous_is_named_with_the_dispatch_it_sits_under(tmp_path: Path) -> None:
    """Beside the run it binds, the dispatch whose worker asked the question.

    The dispatch the registry records here is this test process, and the ancestry it is
    found through is real: a rendezvous is always a descendant of the dispatch whose
    worker ran `scripts/ask-manager.sh`, which execs the bus, and here the process that
    started the rendezvous is the one the registry names. Naming a process that merely
    *exists* — the shape tried first — proves nothing, because a rendezvous does not descend from
    an unrelated process and the reading correctly refused to attribute it.
    """
    runs_root = tmp_path / "runs"
    run = run_name()
    run_root(runs_root, run, dispatch_pid=os.getpid())

    with _serving(run, runs_root) as served:
        rendered = _view("host", runs_root=runs_root)

    assert rendered.returncode == 0, rendered.stderr
    rendezvous = _rendezvous(rendered.stdout, run)
    assert len(rendezvous) == 1, rendered.stdout
    assert f"pid {served.pid}" in rendezvous[0], rendezvous[0]
    assert f"{run}/probe-node" in rendezvous[0], (
        "a rendezvous descending from a dispatch this runs root records is reported "
        f"beside that dispatch: {rendezvous[0]!r}"
    )


@pytest.mark.reads_recipes
def test_a_stale_registry_entry_does_not_name_a_dispatch_that_ended(tmp_path: Path) -> None:
    """A pid the kernel has handed to somebody else is not the dispatch that recorded it.

    A registry entry records the process *and* the start time the kernel gave it, and a
    host up for weeks holds dozens of entries whose pid has since been reused — 26 on
    this one. Matching on the pid alone would name a dispatch that ended, which is the
    one answer worse than naming none when telling two rendezvous apart is the job.
    """
    runs_root = tmp_path / "runs"
    run = run_name()
    directory = run_root(runs_root, run, dispatch_pid=os.getpid())
    # Everything else held: the same live pid, an ancestor of the rendezvous below,
    # under a start time that is not this process's.
    record_dispatch(directory, os.getpid(), started="1")

    with _serving(run, runs_root) as served:
        rendered = _view("host", runs_root=runs_root)

    assert rendered.returncode == 0, rendered.stderr
    rendezvous = _rendezvous(rendered.stdout, run)
    assert len(rendezvous) == 1, rendered.stdout
    assert f"pid {served.pid}" in rendezvous[0], rendezvous[0]
    assert f"{run}/probe-node" not in rendezvous[0], (
        "a registry entry whose recorded start time is not the live process's names a "
        f"dispatch that ended, and must not be reported as this one's: {rendezvous[0]!r}"
    )


@pytest.mark.reads_docs
def test_the_readings_answer_over_a_run_root_a_real_launch_wrote(tmp_path: Path) -> None:
    """The same two readings, over a runs root nothing here composed.

    Every other journey in this module builds its run root, because the two the dead
    driver is about need a launch whose process is gone beside a dispatch whose is not.
    This one is the control for all of them: a real `just orchestrate` writes the run
    root, and both views answer over it exactly as they answer over the built ones — so
    a built root that stopped resembling what the engine writes fails here rather than
    quietly making every assertion above a statement about a shape nobody produces.

    Only the paid model is doubled, which is what makes a launch affordable in a test
    at all; everything from the recipe down is the real thing.

    `reads_docs` because the project this launches is a record of `examples/`, whose task
    documents are Markdown: copying them is reading them, and the code-only key this
    module's other journeys sit under deliberately drops this repository's prose. So the
    marker puts this one journey in the tier keyed on what it actually reads.
    """
    runs = tmp_path / "runs"
    # Launched from a copy, because the run writes its settlements back to its project;
    # the block's exit holds the tracked records unchanged.
    with isolated_examples(tmp_path) as examples:
        environment = {
            **_environment(runs),
            **examples.environment,
            "ONEAGENTGRAPH_ONEHARNESS_BIN": str(
                Path(__file__).resolve().parent / "fake_backend.py"
            ),
            "REAL_ONEHARNESS_BIN": shutil.which("oneharness") or "oneharness",
            "XDG_STATE_HOME": str(tmp_path / "state"),
        }
        launched = subprocess.run(
            ["just", "orchestrate", "examples:scheduler-research", "--detach"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
        )
        assert launched.returncode == 0, f"{launched.stdout}\n{launched.stderr}"
        written = [run.name for run in runs.iterdir() if (run / "launch.json").is_file()]
        assert written, f"the launch recorded no run under {runs}"
        try:
            status = _view("status", written[0], runs_root=runs)
            host = _view("host", runs_root=runs)

            assert status.returncode == 0, status.stderr
            assert host.returncode == 0, host.stderr
            assert [line for line in _above_providers(status.stdout) if line.startswith(DISK)], (
                f"the status view gave no reading over a run the engine wrote: {status.stdout!r}"
            )
            for label in (DISK, RENDEZVOUS):
                assert [line for line in host.stdout.splitlines() if line.startswith(label)], (
                    f"the host view gave no {label.strip()} reading over a run the engine "
                    f"wrote: {host.stdout!r}"
                )
        finally:
            # Detached, so nothing else ends it. With the launch's own environment, because
            # stopping writes the settlement back to whichever root that names.
            _view("stop", written[0], runs_root=runs, env=environment)


@pytest.mark.reads_recipes
def test_a_rendezvous_naming_something_odd_cannot_forge_a_line_of_the_report(
    probe: Probe,
) -> None:
    """A bus command line is any process's to carry, and its channel is nobody's to trust.

    This reading is the one thing in either view that renders a word taken off another
    process's command line, and it renders it into a supervisor's terminal beside the
    engine's own lines. A run name carrying a newline would put a line of its own author's
    writing into that report; one carrying an escape sequence would move the cursor. So a
    process is started here whose argv is exactly what an arbitrary process on a shared
    host may carry, and the report is held to naming it without becoming it.
    """
    # No `/` in it, because the run is read as a directory name: a slash would make the
    # forgery a different path rather than a different line.
    forged = f"{probe.run}\n  disk tmpfs: 0.0 GiB free of 0.0 GiB (0.0% free)\x1b[31m"
    arbitrary = _stand_in(
        "onemessagebus", "serve", QUEUE, "--transport-dir", str(probe.root / forged / "channel")
    )
    try:
        rendered = _view("host", runs_root=probe.root)
    finally:
        # Started here, so it is this journey's own to end.
        arbitrary.kill()
        arbitrary.wait(timeout=e2e_timeout(30))

    assert rendered.returncode == 0, rendered.stderr
    rendezvous = _rendezvous(rendered.stdout, probe.run)
    assert len(rendezvous) == 1, rendered.stdout
    assert "\\x0a" in rendezvous[0] and "\\x1b" in rendezvous[0], (
        "the operand has to be rendered escaped, so a supervisor sees what the process "
        f"really named rather than what it wrote: {rendezvous[0]!r}"
    )
    assert not [
        line
        for line in rendered.stdout.splitlines()
        if line.startswith(DISK) and "0.0 GiB free of 0.0 GiB" in line
    ], (
        "a run name carrying a newline forged a line of this report; every value taken "
        f"from outside has to be stripped of what would end a line: {rendered.stdout!r}"
    )


@pytest.mark.reads_recipes
def test_a_rendezvous_naming_something_enormous_cannot_push_the_line_off_the_terminal(
    probe: Probe,
) -> None:
    """The other half of what an untrusted operand can do: not forge a line, but bury one.

    Escaping answers a run name that would end a line or move the cursor; it does nothing
    about one that is simply enormous. A supervisor reads these beside the engine's own
    lines, and a single operand long enough to fill a screen hides the readings under it
    as effectively as deleting them — so every rendered value is bounded, and says how
    much it left out rather than trailing off.
    """
    enormous = f"{probe.run}-{'x' * 4000}"
    arbitrary = _stand_in(
        "onemessagebus", "serve", QUEUE, f"--transport-dir={probe.root / enormous / 'channel'}"
    )
    try:
        rendered = _view("host", runs_root=probe.root)
    finally:
        # Started here, so it is this journey's own to end.
        arbitrary.kill()
        arbitrary.wait(timeout=e2e_timeout(30))

    assert rendered.returncode == 0, rendered.stderr
    rendezvous = _rendezvous(rendered.stdout, probe.run)
    assert len(rendezvous) == 1, rendered.stdout
    assert len(rendezvous[0]) < len(enormous), (
        "an operand this long has to be cut rather than rendered whole, or it buries "
        f"every reading under it: {len(rendezvous[0])} characters"
    )
    assert f"[{len(enormous)} characters]" in rendezvous[0], (
        "what was cut is said rather than trailed off, so a supervisor can tell a long "
        f"name from a truncated report: {rendezvous[0]!r}"
    )


@pytest.mark.reads_recipes
def test_one_rendezvous_reached_through_a_launcher_is_still_one_line(probe: Probe) -> None:
    """The shape an operator produces by hand, which is the shape that reads as several.

    A wrapper that does not exec — `timeout`, which is what somebody bounding a wait by
    hand types — stays as a process of its own carrying the same argv words as the bus
    below it, so the process table holds this one rendezvous twice. Counting matches would
    report one open question as two, which is the opposite of what this reading is for.
    """
    with _serving(probe.run, probe.root, through_launcher=True) as served:
        rendered = _view("host", runs_root=probe.root)
        # Read while the rendezvous is still up: this is the premise the assertion below
        # rests on, and after the `with` there is nothing left to count.
        table = subprocess.run(
            ["ps", "-eo", "pid,args"], capture_output=True, text=True, check=True
        ).stdout

    assert rendered.returncode == 0, rendered.stderr
    matched = [
        line
        for line in table.splitlines()
        if f"onemessagebus ask {QUEUE}" in line and f"/{probe.run}/channel" in line
    ]
    assert len(matched) > 1, (
        "this journey is about a rendezvous the process table carries more than once, and "
        f"this launcher produced only one process: {matched}"
    )

    rendezvous = _rendezvous(rendered.stdout, probe.run)
    assert len(rendezvous) == 1, (
        f"{len(matched)} processes carry this one rendezvous and the view reported "
        f"{len(rendezvous)} of them: {rendered.stdout!r}"
    )
    assert f"pid {served.pid}" not in rendezvous[0], (
        "the process reported is the innermost one holding the rendezvous, not the "
        f"launcher that started it: {rendezvous[0]!r}"
    )


@pytest.mark.reads_recipes
def test_the_status_view_carries_its_reading_even_with_no_provider_block(
    tmp_path: Path,
) -> None:
    """A view with nothing to be below still gets the reading, at the end.

    The engine prints its provider report under a run's own lines and prints neither for
    a runs root holding no runs — so the line this reading is placed above is not always
    there. It is placed at the end then rather than dropped, because free space is the
    reading an operator most wants on a host they have just found empty.
    """
    empty = tmp_path / "runs"
    empty.mkdir()

    rendered = _view("status", runs_root=empty)

    assert rendered.returncode == 0, rendered.stderr
    lines = rendered.stdout.splitlines()
    assert not [line for line in lines if line.startswith(PROVIDERS)], (
        "this journey is about a view with no provider block, and this one has one: "
        f"{rendered.stdout!r}"
    )
    assert [line for line in lines if line.startswith(DISK)], (
        f"a view with no provider block still owes its reading: {rendered.stdout!r}"
    )


@pytest.mark.reads_recipes
def test_two_filesystems_are_two_readings_and_one_is_one(probe: Probe) -> None:
    """What a supervisor needs is the free space of each filesystem, once.

    The runs root and the lifecycle worktrees are on one device on this host and need not
    be on another, and the incident this reading exists for was the worktrees' filesystem
    filling. So both are asked about: two lines when they are two filesystems, and one
    line naming both when they are one — because two lines for one device would read as
    two answers about two resources.
    """
    # The one-filesystem half is arranged rather than assumed: a state root beside the
    # probe's own runs root is on that root's filesystem by construction, where the
    # host's default `~/.onevcs` need not be — pytest's temporary root and `$HOME` are
    # two devices on the host this was first taken on, and reading them as one made
    # this journey a claim about a host's mounts rather than about the reading.
    beside = probe.root.parent / "onevcs-home"
    beside.mkdir()
    shared = _view("host", runs_root=probe.root, env={"ONEVCS_HOME": str(beside)})
    assert shared.returncode == 0, shared.stderr
    one = [line for line in shared.stdout.splitlines() if line.startswith(f"{DISK} ")]
    assert len(one) == 1 and "and the lifecycle worktrees under" in one[0], (
        "the runs root and the worktrees are on one filesystem here, so they are one "
        f"reading naming both: {shared.stdout!r}"
    )

    elsewhere = Path(SECOND_FILESYSTEM) / f"supervision-readings-{os.getpid()}"
    elsewhere.mkdir(parents=True, exist_ok=True)
    try:
        parted = _view("host", runs_root=probe.root, env={"ONEVCS_HOME": str(elsewhere)})
    finally:
        elsewhere.rmdir()

    assert parted.returncode == 0, parted.stderr
    two = [line for line in parted.stdout.splitlines() if line.startswith(f"{DISK} ")]
    assert len(two) == 2, (
        f"{SECOND_FILESYSTEM} is a filesystem of its own on this host, so the worktrees "
        f"under it are a reading of their own: {parted.stdout!r}"
    )
    assert "the runs root" in two[0] and "the lifecycle worktrees under" in two[1], (
        f"each reading names which of the two roots it is about: {two}"
    )


@pytest.mark.reads_recipes
def test_a_registry_record_this_reading_cannot_parse_is_skipped(tmp_path: Path) -> None:
    """One unreadable record is one dispatch missed, never a view that fails.

    A view that raised where a supervisor is reading it would take the whole report with
    it, so a file in the registry that is not the JSON the engine writes is passed over
    and the records beside it go on answering.
    """
    runs_root = tmp_path / "runs"
    run = run_name()
    registry = run_root(runs_root, run, dispatch_pid=os.getpid()) / "dispatches"
    (registry / "not-a-record.json").write_text("{ this is not json", encoding="utf-8")

    with _serving(run, runs_root):
        rendered = _view("host", runs_root=runs_root)

    assert rendered.returncode == 0, rendered.stderr
    rendezvous = _rendezvous(rendered.stdout, run)
    assert len(rendezvous) == 1, rendered.stdout
    assert f"{run}/probe-node" in rendezvous[0], (
        "a record this reading could not parse has to be passed over, leaving the ones "
        f"beside it still answering: {rendezvous[0]!r}"
    )


@pytest.mark.reads_recipes
def test_a_registry_record_carrying_no_start_time_still_names_its_dispatch(
    tmp_path: Path,
) -> None:
    """Unverified is not refused, which is the direction a format change has to fail in.

    The start time is what tells a live dispatch from a stale entry whose pid has been
    reused, and a record that states one in a spelling this cannot check states nothing
    either way. Refusing it would lose a real answer to a format change rather than to a
    stale record, so it attributes and the check simply does not apply.
    """
    runs_root = tmp_path / "runs"
    run = run_name()
    record_dispatch(run_root(runs_root, run), os.getpid(), unstamped=True)

    with _serving(run, runs_root):
        rendered = _view("host", runs_root=runs_root)

    assert rendered.returncode == 0, rendered.stderr
    rendezvous = _rendezvous(rendered.stdout, run)
    assert len(rendezvous) == 1, rendered.stdout
    assert f"{run}/probe-node" in rendezvous[0], (
        "an entry carrying no checkable start time is unverified rather than refused, so "
        f"it still names the dispatch it records: {rendezvous[0]!r}"
    )


@pytest.mark.reads_recipes
def test_a_host_with_no_state_root_still_reports_the_runs_roots_filesystem(
    probe: Probe,
) -> None:
    """The worktrees are a reading this host may not have; the runs root always is.

    `onevcs` puts every lifecycle worktree under its state root, which it resolves from
    `ONEVCS_HOME` or from `HOME`. A process carrying neither has no worktree root to
    report on — and the runs root it does have is the reading that must survive that,
    rather than the whole line going with it.
    """
    rendered = _view("host", runs_root=probe.root, env={"ONEVCS_HOME": "", "HOME": ""})

    assert rendered.returncode == 0, rendered.stderr
    disk = [line for line in rendered.stdout.splitlines() if line.startswith(f"{DISK} ")]
    assert len(disk) == 1, (
        "with no state root there is one filesystem to report on, not two or none: "
        f"{rendered.stdout!r}"
    )
    assert "the runs root" in disk[0] and "lifecycle worktrees" not in disk[0], (
        f"the one reading left is the runs root's: {disk[0]!r}"
    )


@pytest.mark.reads_recipes
def test_a_runs_root_that_cannot_be_listed_still_reports_every_live_rendezvous(
    probe: Probe, tmp_path: Path
) -> None:
    """A host whose runs root does not exist yet still reports the questions on it.

    This is the fresh checkout, and a supervisor meets it before the first launch here:
    nothing can be listed under that root, so no dispatch can be recorded and every
    attribution has to come back empty. What must survive is the rendezvous itself — a
    live bus `ask` is somebody waiting on an answer whether or not *this* root
    knows anything about it, and a reading that dropped it would hide the one process
    these lines exist to name.

    The rendezvous is served over a root that does exist: the two are separate processes
    with separate environments, and it is the **view's** root that is absent.
    """
    absent = tmp_path / "never-written"

    with _serving(probe.run, probe.root):
        rendered = _view("host", runs_root=absent)

        assert rendered.returncode == 0, rendered.stderr
        named = _rendezvous(rendered.stdout, probe.run)
        assert len(named) == 1, (
            "a rendezvous is reported whether or not the runs root can be listed: "
            f"{rendered.stdout!r}"
        )
        assert "no dispatch this runs root records" in named[0], (
            f"with nothing to list, the line says so rather than inventing one: {named[0]!r}"
        )
        assert "bound to no run this host is supervising" in named[0], (
            f"a rendezvous this root holds no run for is reported as exactly that: {named[0]!r}"
        )
    assert not absent.exists(), (
        f"reading a runs root that does not exist must not create one: {absent}"
    )


@pytest.mark.reads_recipes
def test_a_judge_side_naming_its_channel_relative_to_where_it_runs_is_bound_to_its_run(
    probe: Probe,
) -> None:
    """The observer's judge side, named the way `graphs/dag-scope.yaml` names it.

    That member runs `onemessagebus serve` with `--transport-dir
    "${ONEPIPELINE_RUNS_DIR:-runs}/$ONEPIPELINE_RUN_ID/channel"`, which is relative
    whenever the runs root is — so the directory argv names means nothing until it is
    resolved against the process's own working directory. Read as written, it names a
    runs root called `runs` wherever the *view* happens to be, and every judge side on
    this host would report itself as bound to no run this host supervises.
    """
    with _serving(probe.run, probe.root, verb="serve", relative=True) as served:
        rendered = _view("host", runs_root=probe.root)

    assert rendered.returncode == 0, rendered.stderr
    named = _line_for(rendered.stdout, served.pid)
    assert len(named) == 1, rendered.stdout
    assert (
        f"onemessagebus serve is holding a channel open for a reply on run {probe.run}"
        in (named[0])
    ), named[0]
    assert "no run this host is supervising" not in named[0], (
        "a judge side whose relative channel resolves under this runs root is bound to its "
        f"run, not reported as belonging to somebody else: {named[0]!r}"
    )


@pytest.mark.reads_recipes
def test_a_channel_named_after_the_end_of_options_is_not_read_as_the_rendezvous_channel(
    probe: Probe,
) -> None:
    """A `--transport-dir` after `--` is an operand, as the bus reads it, and binds no run.

    Two processes carry the same words and the same channel, one with a `--` ahead of the
    flag: the one without is bound to this run, which is what makes the other's line
    saying it is unattributable the `--`'s doing rather than a channel this reading missed.
    """
    channel = str(probe.root / probe.run / "channel")
    flagged = _stand_in("onemessagebus", "ask", QUEUE, "--transport-dir", channel)
    ended = _stand_in("onemessagebus", "ask", QUEUE, "--", "--transport-dir", channel)
    try:
        rendered = _view("host", runs_root=probe.root)
    finally:
        # Started here, so both are this journey's own to end.
        for started in (flagged, ended):
            started.kill()
            started.wait(timeout=e2e_timeout(30))

    assert rendered.returncode == 0, rendered.stderr
    [bound] = _line_for(rendered.stdout, flagged.pid)
    assert f" on run {probe.run}" in bound, (
        f"the control naming its channel before any `--` was not bound to its run: {bound!r}"
    )
    [unbound] = _line_for(rendered.stdout, ended.pid)
    assert "unattributable" in unbound and " on run " not in unbound, (
        f"a channel named after `--` was read as the rendezvous's own: {unbound!r}"
    )


@pytest.mark.reads_recipes
def test_a_rendezvous_whose_command_line_names_no_run_is_reported_as_unattributable(
    probe: Probe,
) -> None:
    """A bus process whose argv names no channel is still a line, saying it cannot be read.

    The bus takes its channel directory from `ONEMESSAGEBUS_TRANSPORT_DIR` as readily as
    from `--transport-dir`, and a process's environment is not another user's to read. So
    a real judge side started that way holds a real channel open with nothing on its
    command line to say whose — and the report names it as unattributable rather than
    leaving out the one process it cannot place.
    """
    with _serving(probe.run, probe.root, verb="serve", in_environment=True) as served:
        rendered = _view("host", runs_root=probe.root)

    assert rendered.returncode == 0, rendered.stderr
    named = _line_for(rendered.stdout, served.pid)
    assert len(named) == 1, (
        f"a bus process naming no channel on its command line was left out: {rendered.stdout!r}"
    )
    assert "unattributable" in named[0] and " on run " not in named[0], (
        f"a rendezvous whose argv names no run is reported as exactly that: {named[0]!r}"
    )


@pytest.mark.reads_recipes
def test_a_blocking_ask_and_a_judge_side_are_each_reported_with_their_verb(
    probe: Probe,
) -> None:
    """Both shapes of rendezvous this host starts are read, each as what it is.

    A dispatched agent's question is an `ask`, and an observer member's judge side is a
    `serve`; a reading that watched for one would leave the other invisible, and the one
    it missed is as live a claim on a manager's answer.
    """
    with (
        _serving(probe.run, probe.root) as asked,
        _serving(probe.run, probe.root, verb="serve") as served,
    ):
        rendered = _view("host", runs_root=probe.root)

    assert rendered.returncode == 0, rendered.stderr
    for process, verb in ((asked, "ask"), (served, "serve")):
        named = _line_for(rendered.stdout, process.pid)
        assert len(named) == 1, rendered.stdout
        assert (
            f"onemessagebus {verb} is holding a channel open for a reply on run " in (named[0])
        ), named[0]


def test_the_filter_refuses_a_view_it_does_not_know_how_to_place_a_reading_in() -> None:
    """The two wrappers name a view; anything else is a mistake said out loud.

    Where a reading goes differs by view — above the provider block for `status`, after
    the report for `host` — so there is no sensible default for a third name. Driven at
    the filter directly because that is its whole interface: a mode word and the view on
    standard input.
    """
    refused = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "supervision-readings.py"), "bogus"],
        input="host x\n",
        capture_output=True,
        text=True,
        timeout=e2e_timeout(30),
        check=False,
    )

    assert refused.returncode == 2, refused
    assert refused.stdout == "", (
        f"a refusal writes no view, so nothing reads as a report: {refused.stdout!r}"
    )
    assert "status|host" in refused.stderr, (
        f"the refusal names the two views there are: {refused.stderr!r}"
    )


@pytest.mark.reads_recipes
def test_the_host_view_still_gives_every_reading_the_engine_gave(probe: Probe) -> None:
    """The whole-host inventory is added to rather than replaced."""
    engine = _engine("host", runs_root=probe.root)
    recipe = _view("host", runs_root=probe.root)

    assert engine.returncode == 0, engine.stderr
    assert recipe.returncode == 0, recipe.stderr

    published = [DURATION.sub("<age>", line) for line in engine.stdout.splitlines()]
    kept = [
        DURATION.sub("<age>", line)
        for line in recipe.stdout.splitlines()
        if not line.startswith((DISK, RENDEZVOUS))
    ]
    assert kept == published, (
        "the recipe must write back everything the published view printed, in order, and "
        f"add its own readings beside it; it printed {recipe.stdout!r}"
    )


@pytest.mark.reads_recipes
def test_a_refused_view_is_still_a_refusal_with_no_reading_beside_it(probe: Probe) -> None:
    """A reading printed beside a refusal would read as an answer to it.

    The published verb refuses a run it does not have on standard error and leaves this
    stream empty, and the recipe carries that refusal out whole: the status a caller
    branches on, and nothing added to a view there was none of.
    """
    refused = _view("status", "no-such-run", runs_root=probe.root)

    assert refused.returncode != 0, refused.stdout
    assert "no such run" in refused.stderr, refused.stderr
    assert refused.stdout.strip() == "", (
        "a reading printed beside a refusal reads as an answer to it; the recipe "
        f"printed {refused.stdout!r}"
    )


#: The one thing a *reading* verb may now leave behind: the fold checkpoint the engine
#: resumes a read from instead of replaying a run's whole journal. It is derived, it is
#: the run's own and goes when the run does, and deleting it costs nothing but the next
#: read's time — so a view that writes one has not changed the run's record of itself,
#: which is the property this journey is about and the reason these views are safe beside
#: live work.
#:
#: Named rather than the assertion loosened: everything else under a run root is a
#: record, and a view that touched one of those would still fail here. That distinction
#: is what the engine's own arrival made necessary — before the checkpoint existed, "read
#: nothing, write nothing" and "leave the record alone" were the same sentence.
DERIVED_BY_A_READER = frozenset({"checkpoint.json"})


@pytest.mark.reads_recipes
def test_neither_view_changes_the_run_s_own_record_of_itself(probe: Probe) -> None:
    """Both views leave the run's record alone, which is what makes them safe beside live work."""
    before = _fingerprint(probe.root)

    assert _view("status", probe.run, runs_root=probe.root).returncode == 0
    assert _view("host", runs_root=probe.root).returncode == 0

    assert _fingerprint(probe.root) == before, (
        "these views are run beside live work and must leave the run's own record of "
        "itself exactly as they found it; something under the runs root that is not a "
        f"derived {'/'.join(sorted(DERIVED_BY_A_READER))} changed while they reported on it"
    )
    # The other half, and what keeps the exclusion above from being vacuous: the engine
    # really does write that record when a reader folds a run's state, so a build that
    # stopped writing it — or renamed it — fails here rather than leaving this journey
    # quietly comparing everything again and proving a property nobody had to hold. What
    # the *name* is reconciled against is the engine's own declaration, in
    # `tests/test_engine_contracts.py`, which reads it off `RunPaths`.
    left = {path.name for path in probe.root.rglob("*") if path.is_file()}
    assert left & DERIVED_BY_A_READER, (
        f"reading this run left no {'/'.join(sorted(DERIVED_BY_A_READER))} behind, so "
        "the exclusion above is excluding nothing and this journey is no longer about "
        "the distinction it was written for"
    )


def _fingerprint(root: Path) -> dict[str, bytes]:
    """Every recorded file under a runs root, by path and by content.

    The derived records a reader may write are left out by name, so what this compares
    is the run's own record of itself rather than the caches built over it.
    """
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in DERIVED_BY_A_READER
    }
