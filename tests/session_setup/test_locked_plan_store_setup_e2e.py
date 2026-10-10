"""Real session-setup journeys kept behind their own host-tool target."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeIs, get_args

import pytest
from nx_workspace import TOOLCHAIN_WRITER_MARKS, WORKSPACE_INSTALL_MARKS, isolated_python_root
from waits import until

from orchestrator.root import REPO_ROOT

#: Every journey here runs the real session setup, which re-provisions the `.venv` of the
#: checkout it runs in and installs `uv` tools: a writer, run in a copy of this checkout
#: whose toolchain is its own, taking that copy's install from this checkout's.
pytestmark = [*WORKSPACE_INSTALL_MARKS, *TOOLCHAIN_WRITER_MARKS]

#: The variable session setup's last step reads its checkout list from, in place of the
#: tracked one. Named here so a run of the real setup provisions the stand-ins below and
#: never this host's registered siblings — the one thing a journey may not do to them.
REGISTERED_CHECKOUTS_OVERRIDE = "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS"
#: The file a held stand-in's bootstrap waits for before it finishes, named through this
#: variable, which the detached job inherits from the session setup that started it.
RELEASE_VARIABLE = "STAND_IN_RELEASE"
#: How long a held stand-in's bootstrap waits for its release: longer than the three
#: session starts it has to outlast, and bounded so a journey that never releases it
#: leaves nothing running for long.
HELD_BOOTSTRAP_SECONDS = 600
#: Every status a job's state records, as the script writes it;
#: `tests/test_repos_bootstrap_docs.py` holds this to the script's own vocabulary.
JobStatus = Literal["running", "done", "failed", "stale"]
JOB_ENDINGS: frozenset[JobStatus] = frozenset({"done", "failed", "stale"})


def _is_job_status(value: str) -> TypeIs[JobStatus]:
    return value in get_args(JobStatus)


@dataclass(frozen=True)
class JobState:
    """What one checkout's `state` file records: how its job stands, and its process."""

    status: JobStatus | None = None
    pid: int | None = None

    @classmethod
    def read(cls, memo_dir: Path) -> JobState:
        state = memo_dir / "state"
        if not state.exists():
            return cls()
        fields = dict(
            row.split(" ", 1) for row in state.read_text(encoding="utf-8").splitlines() if row
        )
        status = fields.get("status")
        assert status is None or _is_job_status(status), fields
        return cls(
            status=status,
            pid=int(fields["pid"]) if "pid" in fields else None,
        )

    @property
    def ended(self) -> bool:
        return self.status in JOB_ENDINGS

    @property
    def alive(self) -> bool:
        """Whether the process the job recorded of itself still exists: a read, not a signal."""
        return self.pid is not None and Path(f"/proc/{self.pid}").exists()

    @property
    def stopped(self) -> bool:
        """Whether nothing is left running for this state, however it ended.

        Every job records its process before it bootstraps, so a state with no `pid` is
        one whose job has not yet begun — unless it has ended, when no job wrote it.
        """
        if self.pid is None:
            return self.ended
        return not self.alive


def _stand_in(root: Path, *, marks: Path, body: str = "") -> Path:
    """A real checkout whose `just bootstrap` leaves a mark and then runs `body`."""
    root.mkdir()
    (root / "justfile").write_text(
        f'bootstrap:\n    @echo "{root.name}" >> "{marks}"\n{body}',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "-q", "--initial-branch", "main"], cwd=root, check=True, capture_output=True
    )
    _commit(root, "seed")
    return root


def _commit(root: Path, subject: str) -> None:
    for command in (
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.name=Journey",
            "-c",
            "user.email=j@example.invalid",
            "commit",
            "-q",
            "-m",
            subject,
        ],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)


def _held_body() -> str:
    return (
        f"    @for _ in $(seq 1 {HELD_BOOTSTRAP_SECONDS * 10}); do"
        f' [ -f "${RELEASE_VARIABLE}" ] && break; sleep 0.1; done\n'
    )


@dataclass(frozen=True)
class ReportLine:
    """One checkout's line of the recipe's report: what happened to it, and its log."""

    outcome: str
    text: str
    log: Path | None

    @classmethod
    def of(cls, stderr: str, checkout: Path) -> ReportLine:
        """The one line naming `checkout`, which has to be there exactly once."""
        lines = [line for line in stderr.splitlines() if str(checkout) in line.split()]
        assert len(lines) == 1, stderr
        text = lines[0].strip()
        _, _, log = text.partition("(log: ")
        return cls(outcome=text.split()[0], text=text, log=Path(log.rstrip(")")) if log else None)

    @property
    def memo_dir(self) -> Path:
        assert self.log is not None, self.text
        return self.log.parent


def _await_ending(memo_dir: Path) -> JobState:
    """Wait on the sentinel a job writes — the ending in its state — and return it."""
    until(
        f"the job for {memo_dir.name} to record how it ended",
        lambda: JobState.read(memo_dir).ended,
        seconds=60,
        state=lambda: f"state {JobState.read(memo_dir)}",
    )
    return JobState.read(memo_dir)


def _await_stopped(memo_dir: Path) -> None:
    """Wait until the job for `memo_dir` has ended, or its recorded process is gone."""
    until(
        f"the job for {memo_dir.name} to stop",
        lambda: JobState.read(memo_dir).stopped,
        seconds=60,
        state=lambda: f"state {JobState.read(memo_dir)}",
    )


def _await_mark(marks: Path, name: str) -> None:
    """Wait until the stand-in `name`'s bootstrap has left its mark."""
    until(
        f"the job for {name} to be inside its bootstrap",
        lambda: name in _marks(marks),
        seconds=30,
        state=lambda: f"marks {_marks(marks)}",
    )


#: The line a session start that launched the host sweep prints, naming the job's own
#: pid: `started job <pid>`, or `started again as job <pid>` after a failed one.
HOST_SWEEP_JOB = re.compile(r"^host-sweep: .*\bstarted (?:again as )?job (\d+)\b", re.MULTILINE)
#: How long the host sweep a start launched may take over this journey's sandboxed state:
#: seconds of real work, bounded far above it.
HOST_SWEEP_SECONDS = 120


def _host_sweep_jobs(stderr: str) -> list[int]:
    """The pid of every host sweep job a session start's output says it launched."""
    return [int(pid) for pid in HOST_SWEEP_JOB.findall(stderr)]


def _await_host_sweeps(jobs: list[int]) -> None:
    """Wait until every host sweep job this journey's starts launched has exited.

    Session setup returns while its job still runs, by design, so the journey waits it
    out by reading the process table for exactly the pids those starts printed. Only a
    job that outlives the bound is signalled — that pid and no other — and the journey
    then fails naming it.
    """

    def alive() -> list[int]:
        return [pid for pid in jobs if Path(f"/proc/{pid}").exists()]

    try:
        until(
            "the host sweep jobs this journey started to exit",
            lambda: not alive(),
            seconds=HOST_SWEEP_SECONDS,
            state=lambda: f"still running: {alive()}",
        )
    finally:
        for pid in alive():
            os.kill(pid, signal.SIGTERM)


def _marks(marks: Path) -> list[str]:
    return sorted(marks.read_text(encoding="utf-8").split()) if marks.exists() else []


@pytest.fixture(scope="module")
def setup_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of this checkout, with a `.venv` and `node_modules` of its own, to set up.

    Session setup provisions the checkout it runs in — `uv sync` when a pin is not met,
    the plan root, the Bun install it verifies — so it runs here rather than in this
    checkout, whose toolchain every other worker's recipes read. One per worker, because
    the copy is the cost and the journeys on one worker run one after another.
    """
    return isolated_python_root(tmp_path_factory.mktemp("setup-root") / "checkout")


def _private_tool_directories(root: Path) -> dict[str, str]:
    """Where a `uv tool install` a journey's setup runs lands: under ``root``, its own."""
    return {
        "UV_TOOL_DIR": str(root / "uv-tools"),
        "UV_TOOL_BIN_DIR": str(root / "uv-tool-bin"),
    }


def _uv_tool_dir(environment: dict[str, str], *flags: str) -> Path:
    """Where `uv` itself says a tool lands, asked in ``environment``."""
    answered = subprocess.run(
        ["uv", "tool", "dir", *flags],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    return Path(answered.stdout.strip())


def test_session_setup_keeps_the_locked_plan_store_and_plan_root_in_force(
    tmp_path: Path, setup_root: Path
) -> None:
    """The real setup verifies the locked CLI, ensures the plan root, and detaches the siblings.

    Its own exit status stays the one its toolchain earned whatever the sibling jobs do,
    and the `llmlint` it installs lands in the tool directories the journey handed it,
    never this host's shared ones, which every dispatch's pre-push gate runs.
    """
    plans = setup_root / ".plans"
    marks = tmp_path / "marks"
    release = tmp_path / "release"
    held = _stand_in(tmp_path / "held-sibling", marks=marks, body=_held_body())
    moving = _stand_in(tmp_path / "moving-sibling", marks=marks, body=_held_body())
    failing = _stand_in(
        tmp_path / "failing-sibling",
        marks=marks,
        body='    @echo "no cargo-deny here" >&2\n    @exit 3\n',
    )
    listing = tmp_path / "checkouts"
    listing.write_text(
        f"{held}\n{moving}\n{failing}\n{tmp_path / 'absent-sibling'}\n", encoding="utf-8"
    )
    cache = tmp_path / "cache"
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    graph_state = tmp_path / "oneagentgraph-state"
    graph_state.mkdir()
    sweeps: list[int] = []
    tools = _private_tool_directories(tmp_path)
    environment = {
        **os.environ,
        REGISTERED_CHECKOUTS_OVERRIDE: str(listing),
        RELEASE_VARIABLE: str(release),
        "XDG_CACHE_HOME": str(cache),
        # A registry of the journey's own: it registers its stand-ins through the
        # checkout list above and nothing in onevcs, so setup's workspace sweep has
        # no identity to walk. The suite's copy of this host's registry names every
        # real checkout, and sweeping their origins outlasts the held job's release.
        "ONEVCS_HOME": str(tmp_path / "onevcs-home"),
        # The families the sweep's second verb judges, the journey's own for the
        # same reason: its detached job would otherwise reclaim this host's real
        # oneagentgraph runs and scratch.
        "ONEAGENTGRAPH_STATE_DIR": str(graph_state),
        "TMPDIR": str(scratch),
        # This journey keeps the real home, so without these setup's `llmlint` install
        # would replace the one this host's other dispatches run.
        **tools,
    }

    def session_start() -> subprocess.CompletedProcess[str]:
        started = subprocess.run(
            ["bash", str(setup_root / "scripts" / "session-setup.sh")],
            cwd=setup_root,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        sweeps.extend(_host_sweep_jobs(started.stderr))
        return started

    try:
        first = session_start()

        assert first.returncode == 0, first.stdout + first.stderr
        adopted = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
        located = setup_root / ".venv" / "bin" / "onetaskgraph"
        assert f"ready (onetaskgraph: {adopted} at {located})" in first.stderr
        assert (plans / "tasks").is_dir()
        assert (plans / "projects").is_dir()

        # The install landed where the journey said, by uv's own account of where that is.
        tool_dir, tool_bin = (Path(tools[name]) for name in ("UV_TOOL_DIR", "UV_TOOL_BIN_DIR"))
        assert _uv_tool_dir(environment) == tool_dir
        assert _uv_tool_dir(environment, "--bin") == tool_bin
        assert (tool_dir / "llmlint-cli" / "uv-receipt.toml").is_file(), first.stderr
        assert (tool_bin / "llmlint").is_file(), first.stderr

        # Each stand-in's job was started and the absent one skipped the way the
        # registration recipe skips it; the setup returned with the held jobs still
        # inside their bootstraps, holding no memo that claims they finished.
        memo = {}
        for stand_in in (held, moving, failing):
            line = ReportLine.of(first.stderr, stand_in)
            assert line.outcome == "started", line
            memo[stand_in] = line.memo_dir
        assert ReportLine.of(first.stderr, tmp_path / "absent-sibling").outcome == "skip"
        assert "3 started, 0 running, 0 stale, 0 unchanged, 1 skipped, 0 refused, 0 failed" in (
            first.stderr
        )
        for stand_in in (held, moving):
            _await_mark(marks, stand_in.name)
            state = JobState.read(memo[stand_in])
            assert state.status == "running", state
            assert state.alive, state
            assert not (memo[stand_in] / "stamp").exists()
        assert _await_ending(memo[failing]).status == "failed"

        (moving / "README").write_text("moved under a running job\n", encoding="utf-8")
        _commit(moving, "move HEAD under the job")

        second = session_start()

        assert second.returncode == 0, second.stdout + second.stderr
        assert ReportLine.of(second.stderr, held).outcome == "running"
        stale = ReportLine.of(second.stderr, moving)
        assert stale.outcome == "stale" and "still running" in stale.text, stale
        failed = ReportLine.of(second.stderr, failing)
        assert failed.outcome == "failed" and "exit 3; started again" in failed.text, failed
        assert failed.log is not None
        assert "no cargo-deny here" in failed.log.read_text(encoding="utf-8")
        assert (
            "session-setup: a registered sibling checkout's bootstrap failed or was refused"
            in second.stderr
        )
        # The failed one was started again; neither held checkout was, while its lock
        # was held.
        assert _await_ending(memo[failing]).status == "failed"
        assert _marks(marks) == sorted([held.name, moving.name, failing.name, failing.name])

        release.write_text("", encoding="utf-8")
        assert _await_ending(memo[held]).status == "done"
        assert (memo[held] / "stamp").exists()
        # The moved checkout's job succeeded at a tree it no longer is: never trusted.
        assert _await_ending(memo[moving]).status == "stale"
        assert not (memo[moving] / "stamp").exists()

        third = session_start()

        assert third.returncode == 0, third.stdout + third.stderr
        complete = ReportLine.of(third.stderr, held)
        assert complete.outcome == "unchanged" and "completed" in complete.text, complete
        assert ReportLine.of(third.stderr, moving).outcome == "stale"
        assert _marks(marks).count(held.name) == 1

        # The host sweep the first start launched ran inside this journey's sandbox: its
        # second verb judged the journey's own state root and never this host's.
        assert sweeps, first.stderr
        _await_host_sweeps(sweeps)
        swept = (cache / "ai-orchestrator" / "sweep" / "sweep.log").read_text(encoding="utf-8")
        assert f'examined family "runs" at {graph_state} ' in swept, swept
        assert f'examined family "temp" at {scratch} ' in swept, swept
        assert str(Path.home() / ".local" / "state" / "oneagentgraph") not in swept, swept
    finally:
        release.write_text("", encoding="utf-8")
        for memo_dir in (state.parent for state in cache.rglob("state")):
            _await_stopped(memo_dir)
        _await_host_sweeps(sweeps)


def _path_lacking(shims: Path, *missing: str) -> str:
    """This process's PATH with no ``missing`` tool on it and every other tool kept.

    A directory holding one of them is dropped, and every other executable it held is
    linked into ``shims``, searched last, so the session keeps it: on this host `uv`
    shares a directory with `codex`, whose absence setup would answer with a real npm
    install.
    """
    shims.mkdir()
    kept: list[str] = []
    for directory in filter(None, os.environ["PATH"].split(os.pathsep)):
        held = Path(directory)
        if not any((held / tool).exists() for tool in missing):
            kept.append(directory)
            continue
        for entry in sorted(held.iterdir()) if held.is_dir() else ():
            if entry.name not in missing and not (shims / entry.name).exists():
                (shims / entry.name).symlink_to(entry)
    # Last, so a tool a kept directory provides is still found there first, as it was.
    return os.pathsep.join([*kept, str(shims)])


@dataclass(frozen=True)
class Session:
    """A session start's environment, its persisted environment file, its home, and the
    copy of this checkout it sets up."""

    env: dict[str, str]
    env_file: Path
    home: Path
    root: Path

    def start(self, sweeps: list[int]) -> subprocess.CompletedProcess[str]:
        started = subprocess.run(
            ["bash", str(self.root / "scripts" / "session-setup.sh")],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )
        sweeps.extend(_host_sweep_jobs(started.stderr))
        return started

    def resolve_just(self) -> subprocess.CompletedProcess[str]:
        """What a later command of the session finds, reading what setup persisted."""
        return subprocess.run(
            ["bash", "-c", f'source "{self.env_file}" && command -v just && just --version'],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )


def _session(root: Path, tmp_path: Path, *missing: str, **overrides: str) -> Session:
    """A session setting up ``root``, with none of ``missing`` on its PATH, a home and `uv`
    tool directories of its own, and this host's uv cache and interpreters unless
    ``overrides`` names others."""
    home = tmp_path / "home"
    home.mkdir()
    listing = tmp_path / "checkouts"
    listing.write_text("", encoding="utf-8")
    for directory in ("oneagentgraph-state", "tmp"):
        (tmp_path / directory).mkdir()
    uv = shutil.which("uv")
    assert uv is not None
    uv_dirs = {
        variable: subprocess.run(
            [uv, *command], check=True, text=True, capture_output=True
        ).stdout.strip()
        for variable, command in (
            ("UV_CACHE_DIR", ["cache", "dir"]),
            ("UV_PYTHON_INSTALL_DIR", ["python", "dir"]),
        )
    }
    env = {
        **os.environ,
        **uv_dirs,
        "HOME": str(home),
        "PATH": _path_lacking(tmp_path / "shims", *missing),
        "CLAUDE_ENV_FILE": str(tmp_path / "session.env"),
        REGISTERED_CHECKOUTS_OVERRIDE: str(listing),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "ONEVCS_HOME": str(tmp_path / "onevcs-home"),
        "ONEAGENTGRAPH_STATE_DIR": str(tmp_path / "oneagentgraph-state"),
        "TMPDIR": str(tmp_path / "tmp"),
        # Where uv would put them under this home anyway, stated so that no tool directory
        # this process inherited can send a session's installs anywhere else.
        "UV_TOOL_DIR": str(home / ".local" / "share" / "uv" / "tools"),
        "UV_TOOL_BIN_DIR": str(home / ".local" / "bin"),
    }
    env.pop("UV_OFFLINE", None)
    env.update(overrides)
    for tool in missing:
        assert (
            subprocess.run(["bash", "-c", f"command -v {tool}"], env=env, check=False).returncode
            != 0
        ), f"the session under test must start without {tool}"
    return Session(env=env, env_file=tmp_path / "session.env", home=home, root=root)


def test_session_setup_provisions_just_into_a_session_that_lacks_it(
    tmp_path: Path, setup_root: Path
) -> None:
    """A session with no `just` anywhere ends the real setup with one on its persisted PATH.

    The session's home is the journey's own, so neither the `~/.cargo/bin` nor the
    `~/.local/bin` setup prepends holds this host's `just`; uv keeps this host's cache
    and interpreters so the install reads the index rather than rebuilding them. The
    second start, with `just` now present, stays quiet about it.
    """
    session = _session(setup_root, tmp_path, "just")
    sweeps: list[int] = []
    try:
        first = session.start(sweeps)

        assert first.returncode == 0, first.stdout + first.stderr
        assert "session-setup: installing rust-just >= " in first.stderr, first.stderr
        resolved = session.resolve_just()
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        located, version = resolved.stdout.splitlines()
        assert Path(located) == session.home / ".local" / "bin" / "just", resolved.stdout
        assert version.startswith("just "), resolved.stdout

        second = session.start(sweeps)

        assert second.returncode == 0, second.stdout + second.stderr
        assert "rust-just" not in second.stderr, second.stderr
    finally:
        _await_host_sweeps(sweeps)


def test_session_setup_puts_a_moved_uv_tool_bin_on_the_persisted_path(
    tmp_path: Path, setup_root: Path
) -> None:
    """With `UV_TOOL_BIN_DIR` pointing elsewhere, the `just` installed there is the one found."""
    tool_bin = tmp_path / "tool-bin"
    session = _session(setup_root, tmp_path, "just", UV_TOOL_BIN_DIR=str(tool_bin))
    sweeps: list[int] = []
    try:
        started = session.start(sweeps)

        assert started.returncode == 0, started.stdout + started.stderr
        resolved = session.resolve_just()
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        assert Path(resolved.stdout.splitlines()[0]) == tool_bin / "just", resolved.stdout
    finally:
        _await_host_sweeps(sweeps)


def test_session_setup_continues_when_just_cannot_be_installed(
    tmp_path: Path, setup_root: Path
) -> None:
    """An install uv cannot complete — offline, over an empty cache — is logged, and the rest
    of setup still runs to the exit status its required tools earn."""
    cache = tmp_path / "uv-cache"
    cache.mkdir()
    session = _session(setup_root, tmp_path, "just", UV_CACHE_DIR=str(cache), UV_OFFLINE="1")
    sweeps: list[int] = []
    try:
        started = session.start(sweeps)

        assert started.returncode == 0, started.stdout + started.stderr
        assert "session-setup: rust-just install failed (continuing)" in started.stderr
        assert "session-setup: host sweep unavailable; continuing session setup" in started.stderr
        assert session.resolve_just().returncode != 0
    finally:
        _await_host_sweeps(sweeps)


def test_session_setup_names_uv_when_it_cannot_install_just(
    tmp_path: Path, setup_root: Path
) -> None:
    """With neither `just` nor `uv` on PATH, setup says which is missing and goes on."""
    session = _session(setup_root, tmp_path, "just", "uv")
    sweeps: list[int] = []
    try:
        started = session.start(sweeps)

        assert started.returncode == 0, started.stdout + started.stderr
        assert "session-setup: cannot install just: uv is not installed" in started.stderr
        assert session.resolve_just().returncode != 0
    finally:
        _await_host_sweeps(sweeps)
