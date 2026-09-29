"""The host sweep session setup starts detached: never waited on, one at a time, hourly.

Every journey runs the real `scripts/session-setup.sh`, `scripts/host-sweep.sh`,
`justfile` and `scripts/repos-bootstrap.sh`, copied into a fixture repository, through
the real `just`, `uv`, `flock` and `setsid`. Only the published CLIs are doubled, at the
PATH boundary, and one journey runs the real `onevcs` and `oneagentgraph` instead. Each
points `XDG_CACHE_HOME` at its own directory, so no journey touches this host's lock,
and ends by releasing its doubles and waiting until no process or job it started is
alive; none signals anything.

What the sweep is for and when it starts is docs/host-setup.md, "The host sweep".
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from published_tools import PUBLISHED_TOOLS
from waits import timeout as e2e_timeout
from waits import until

from orchestrator.root import REPO_ROOT

ADOPTED_ONEJUDGE_VERSION = (
    (REPO_ROOT / "config" / "onejudge.version").read_text(encoding="utf-8").strip()
)
ADOPTED_ONEHARNESS_VERSION = (
    (REPO_ROOT / "config" / "oneharness.version").read_text(encoding="utf-8").strip()
)
#: What the doubled `python` answers each distribution query session setup makes with.
ADOPTED_DISTRIBUTION_VERSIONS = {
    "oneharness-cli": ADOPTED_ONEHARNESS_VERSION,
    **{tool.distribution: tool.adopted_version for tool in PUBLISHED_TOOLS},
}


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write_version_double(path: Path, answer: str) -> None:
    _write_executable(path, f"#!/bin/sh\nprintf '{answer}\\n'\n")


def _write_sdk_python(path: Path) -> None:
    """A `python` answering session setup's metadata queries and its `onejudge_sdk` import."""
    branches = "".join(
        f"*{distribution}*) printf '{version}\\n' ;; "
        for distribution, version in ADOPTED_DISTRIBUTION_VERSIONS.items()
    )
    _write_executable(
        path,
        f"#!/bin/sh\ncase \"$*\" in {branches}*) printf '{ADOPTED_ONEJUDGE_VERSION}\\n' ;; esac\n",
    )


#: The one place the interval is declared, read off the script.
HOST_SWEEP = REPO_ROOT / "scripts" / "host-sweep.sh"


def host_sweep_interval() -> int:
    found = re.search(
        r"(?m)^readonly HOST_SWEEP_INTERVAL_SECONDS=(\d+)$",
        HOST_SWEEP.read_text(encoding="utf-8"),
    )
    assert found is not None, "host-sweep.sh no longer declares its interval in one place"
    return int(found.group(1))


#: How long a session start with a sweep still running may take. Bounded in seconds,
#: against a doubled sweep that would run for ten minutes.
SESSION_START_SECONDS = 60

#: A doubled sweep verb: it records what it was asked, holds until the journey releases
#: it (for ten minutes at most, so a journey that died cannot leave it for good), and
#: exits as the journey says. Any other argument is the version query session setup makes.
SWEEP_VERB_DOUBLE = """#!/bin/sh
if [ "$1" = sweep ]; then
  printf '%s\\n' "{binary} $*" >>"$TEST_SWEEP_CALLS"
  waited=0
  while [ -n "${{TEST_SWEEP_RELEASE:-}}" ] && [ ! -e "$TEST_SWEEP_RELEASE" ]; do
    [ "$waited" -lt 6000 ] || break
    sleep 0.1
    waited=$((waited + 1))
  done
  echo "{binary} sweep double: exiting ${{TEST_SWEEP_EXIT:-0}}" >&2
  exit "${{TEST_SWEEP_EXIT:-0}}"
fi
printf '{binary} {version}\\n'
"""


@functools.cache
def _real_tool_path() -> str:
    """The directories of the real `just` and `uv`, resolved past any version-manager shim.

    A journey's `HOME` is its own, which a shim reads its configuration from, so the
    binaries themselves are named: `uv run` exports its own path as `UV`.
    """
    just = shutil.which("just")
    uv = shutil.which("uv")
    assert just is not None and uv is not None, "the journeys need `just` and `uv` on PATH"
    resolved = subprocess.run(
        [uv, "run", "--no-project", "printenv", "UV"],
        cwd="/",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return f"{Path(just).resolve().parent}:{Path(resolved).parent}"


def _pid_alive(pid: int) -> bool:
    """A read of the process table, never a signal."""
    return Path(f"/proc/{pid}").exists()


class SessionStart:
    """A fixture repository whose real session setup starts the host sweep."""

    def __init__(self, tmp_path: Path, *, real_verbs: bool = False) -> None:
        self.root = tmp_path
        self.repo = tmp_path / "repo"
        scripts = self.repo / "scripts"
        scripts.mkdir(parents=True)
        for name in (
            "session-setup.sh",
            "host-sweep.sh",
            "repos-bootstrap.sh",
            "registered-checkouts.sh",
        ):
            shutil.copy(REPO_ROOT / "scripts" / name, scripts / name)
        shutil.copyfile(REPO_ROOT / "justfile", self.repo / "justfile")
        self.marks = tmp_path / "marks"
        self.marks.mkdir()
        _write_executable(
            scripts / "setup-llmlint.sh", f'#!/bin/sh\ntouch "{self.marks}/setup-llmlint"\n'
        )
        config = self.repo / "config"
        config.mkdir()
        for declared in (REPO_ROOT / "config").glob("*.version"):
            shutil.copy(declared, config / declared.name)
        venv_bin = self.repo / ".venv" / "bin"
        if real_verbs:
            (self.repo / ".venv").symlink_to(REPO_ROOT / ".venv")
        else:
            _write_version_double(venv_bin / "onejudge", f"onejudge {ADOPTED_ONEJUDGE_VERSION}")
            _write_version_double(
                venv_bin / "oneharness", f"oneharness {ADOPTED_ONEHARNESS_VERSION}"
            )
            _write_sdk_python(venv_bin / "python")
            for tool in PUBLISHED_TOOLS:
                if tool.binary in {"onevcs", "oneagentgraph"}:
                    _write_executable(
                        venv_bin / tool.binary,
                        SWEEP_VERB_DOUBLE.format(binary=tool.binary, version=tool.adopted_version),
                    )
                else:
                    _write_version_double(
                        venv_bin / tool.binary, f"{tool.binary} {tool.adopted_version}"
                    )
        _write_version_double(tmp_path / ".local" / "node" / "bin" / "bun", "1.2.3")
        checkouts = tmp_path / "checkouts"
        checkouts.write_text("# no sibling checkout in this journey\n", encoding="utf-8")
        self.calls = tmp_path / "sweep-calls"
        self.release_file = tmp_path / "release"
        self.state = tmp_path / "cache" / "ai-orchestrator" / "sweep"
        self.scratch = {
            "ONEVCS_HOME": tmp_path / "onevcs-home",
            "ONEAGENTGRAPH_STATE_DIR": tmp_path / "oneagentgraph-state",
            "TMPDIR": tmp_path / "tmp",
        }
        for directory in self.scratch.values():
            directory.mkdir()
        (self.scratch["ONEVCS_HOME"] / "workspaces").mkdir()
        self.environment = {
            "HOME": str(tmp_path),
            "PATH": f"{_real_tool_path()}:/usr/bin:/bin",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS": str(checkouts),
            "TEST_SWEEP_CALLS": str(self.calls),
            **{name: str(path) for name, path in self.scratch.items()},
        }
        self.processes: list[subprocess.Popen[str]] = []

    def hold_sweeps(self) -> None:
        """Every doubled sweep started from here on holds until `release`."""
        self.environment["TEST_SWEEP_RELEASE"] = str(self.release_file)

    def release(self) -> None:
        self.release_file.touch()

    def start(self, **extra_env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "session-setup.sh")],
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env={**self.environment, **extra_env},
            timeout=e2e_timeout(SESSION_START_SECONDS),
        )

    def spawn(self, *command: str) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            list(command),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=self.environment,
        )
        self.processes.append(process)
        return process

    def spawn_start(self) -> subprocess.Popen[str]:
        return self.spawn("bash", str(self.repo / "scripts" / "session-setup.sh"))

    def spawn_hand_sweep(self, *arguments: str) -> subprocess.Popen[str]:
        """`just sweep` run by hand in the fixture repository, its doubles on PATH."""
        environment = dict(self.environment)
        environment["PATH"] = f"{self.repo / '.venv' / 'bin'}:{environment['PATH']}"
        process = subprocess.Popen(
            ["just", "--justfile", str(self.repo / "justfile"), "sweep", *arguments],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=environment,
        )
        self.processes.append(process)
        return process

    def hand_sweep(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        """A hand-run to completion, bounded well below any sweep a journey holds."""
        process = self.spawn_hand_sweep(*arguments)
        stdout, stderr = process.communicate(timeout=e2e_timeout(30))
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def verb_calls(self) -> list[str]:
        if not self.calls.exists():
            return []
        return self.calls.read_text(encoding="utf-8").splitlines()

    def field(self, file: str, key: str) -> str | None:
        path = self.state / file
        if not path.exists():
            return None
        for row in path.read_text(encoding="utf-8").splitlines():
            name, _, value = row.partition(" ")
            if name == key:
                return value
        return None

    def holder_pid(self) -> int | None:
        pid = self.field("holder", "pid")
        return int(pid) if pid is not None else None

    def await_calls(self, count: int) -> None:
        until(
            f"{count} sweep verb call(s)",
            lambda: len(self.verb_calls()) >= count,
            seconds=60,
            state=lambda: f"calls {self.verb_calls()}, holder {self.holder_pid()}",
        )

    def await_completion(self, *, since: float = 0.0) -> None:
        """Until a sweep records an ending no earlier than `since` (epoch seconds)."""

        def recorded() -> bool:
            finished = self.field("completed", "finished")
            return finished is not None and int(finished) >= int(since)

        until(
            "the sweep to record its ending",
            recorded,
            seconds=120,
            state=lambda: f"calls {self.verb_calls()}, holder {self.holder_pid()}",
        )

    def settle(self) -> None:
        """Release every double, and wait until nothing this journey started is alive."""
        self.release()
        for process in self.processes:
            process.communicate(timeout=e2e_timeout(120))
        pid = self.holder_pid()
        if pid is not None:
            until(
                f"the sweep job {pid} to exit",
                lambda: not _pid_alive(pid),
                seconds=120,
                state=lambda: f"calls {self.verb_calls()}",
            )


@pytest.fixture
def session_start(tmp_path: Path) -> Iterator[SessionStart]:
    start = SessionStart(tmp_path)
    try:
        yield start
    finally:
        start.settle()


#: What the job's recipe asks each verb, with the default four-hour floor.
ONEVCS_SWEEP = "onevcs sweep --min-age-hours 4"
ONEAGENTGRAPH_SWEEP = "oneagentgraph sweep --min-age-hours 4"


def test_session_setup_returns_while_the_sweep_it_started_still_runs(
    session_start: SessionStart,
) -> None:
    """The hook's foreground never waits on the sweep, and every later step still runs.

    The doubled `onevcs sweep` holds for up to ten minutes; session setup returns in
    seconds with it still running, and exits exactly as the start after it does — one
    that starts no sweep at all, because this one completed inside the hour.
    """
    session_start.hold_sweeps()
    began = time.monotonic()

    first = session_start.start()

    elapsed = time.monotonic() - began
    assert elapsed < SESSION_START_SECONDS, first.stderr
    session_start.await_calls(1)
    job = session_start.holder_pid()
    assert job is not None and _pid_alive(job), first.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP]
    assert f"started job {job} (log: {session_start.state / 'sweep.log'})" in first.stderr
    for tool in PUBLISHED_TOOLS:
        assert f"ready ({tool.binary}: {tool.adopted_version} at " in first.stderr
    assert (session_start.marks / "setup-llmlint").exists(), first.stderr
    assert "repos-bootstrap: 0 started, 0 running" in first.stderr, first.stderr

    session_start.release()
    session_start.await_completion()
    assert session_start.field("completed", "status") == "done"
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]

    unswept = session_start.start()

    assert "none started" in unswept.stderr, unswept.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]
    assert first.returncode == unswept.returncode == 0, first.stderr + unswept.stderr


def test_concurrent_session_starts_launch_one_sweep(session_start: SessionStart) -> None:
    """Several starts at once, and one after them, start one job between them."""
    session_start.hold_sweeps()

    starts = [session_start.spawn_start() for _ in range(4)]
    outputs = [start.communicate(timeout=e2e_timeout(SESSION_START_SECONDS)) for start in starts]
    session_start.await_calls(1)
    later = session_start.start()

    reported = "".join(stderr for _, stderr in outputs) + later.stderr
    assert reported.count("host-sweep: started job") == 1, reported
    # A start that met the lock in the instant between another caller taking it and
    # recording itself says so rather than naming a pid; either way it started nothing.
    assert reported.count("host-sweep: a sweep is running, ") == 4, reported
    assert all(start.returncode == 0 for start in starts), reported
    assert session_start.verb_calls() == [ONEVCS_SWEEP]

    session_start.release()
    session_start.await_completion()
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]


def test_a_hand_run_sweep_while_a_job_holds_the_lock_sweeps_nothing(
    session_start: SessionStart,
) -> None:
    """`just sweep` by hand names the job holding the host and its log, and sweeps nothing."""
    session_start.hold_sweeps()
    session_start.start()
    session_start.await_calls(1)
    job = session_start.holder_pid()

    by_hand = session_start.hand_sweep()

    assert by_hand.returncode != 0, by_hand.stdout + by_hand.stderr
    assert (
        f"another sweep holds the host lock, pid {job} "
        f"(log: {session_start.state / 'sweep.log'}); nothing swept"
    ) in by_hand.stderr, by_hand.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP]


def test_a_session_start_while_a_hand_run_sweep_holds_the_lock_launches_none(
    session_start: SessionStart,
) -> None:
    """A sweep run by hand holds the same lock, and a start reports it rather than joining."""
    session_start.hold_sweeps()
    by_hand = session_start.spawn_hand_sweep()
    session_start.await_calls(1)
    holder = session_start.holder_pid()
    assert holder is not None and _pid_alive(holder)

    start = session_start.start()

    assert (
        f"a sweep is running, pid {holder} (log: its caller's terminal, run by hand); none started"
    ) in start.stderr, start.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP]

    session_start.release()
    by_hand.communicate(timeout=e2e_timeout(120))
    assert by_hand.returncode == 0
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]
    # A sweep run by hand for real is the stamp a start reads, so this one starts nothing.
    assert session_start.field("completed", "status") == "done"
    assert "none started" in session_start.start().stderr
    assert len(session_start.verb_calls()) == 2


def test_a_start_inside_the_hour_after_a_sweep_launches_nothing_and_one_after_it_does(
    session_start: SessionStart,
) -> None:
    """The completion stamp gates starts for exactly the declared interval."""
    session_start.start()
    session_start.await_completion()
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]

    inside = session_start.start()

    assert "the next is due in" in inside.stderr and "none started" in inside.stderr
    assert len(session_start.verb_calls()) == 2

    # The hour passing, which a journey cannot wait out: the stamp moved back past it.
    now = int(time.time())
    completed = session_start.state / "completed"
    completed.write_text(
        completed.read_text(encoding="utf-8").replace(
            f"finished {session_start.field('completed', 'finished')}",
            f"finished {now - host_sweep_interval() - 1}",
        ),
        encoding="utf-8",
    )
    after = session_start.start()

    assert "host-sweep: started job" in after.stderr, after.stderr
    session_start.await_completion(since=now)
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP] * 2


def test_a_dry_run_by_hand_neither_needs_nor_writes_the_stamp(
    session_start: SessionStart,
) -> None:
    """A rehearsal is no sweep: the next start still launches one."""
    rehearsal = session_start.hand_sweep("--dry-run")

    assert rehearsal.returncode == 0, rehearsal.stderr
    assert session_start.verb_calls() == [
        "onevcs sweep --dry-run --min-age-hours 4",
        "oneagentgraph sweep --dry-run --min-age-hours 4",
    ]
    assert not (session_start.state / "completed").exists()

    start = session_start.start()

    assert "host-sweep: started job" in start.stderr, start.stderr
    session_start.await_completion()
    assert session_start.verb_calls()[2:] == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]


def test_a_failed_sweep_is_named_with_its_log_by_the_next_start_and_never_fails_it(
    session_start: SessionStart,
) -> None:
    """The next start names the failure and its log; its exit is the toolchain's alone."""
    failing = session_start.start(TEST_SWEEP_EXIT="3")
    session_start.await_completion()
    assert session_start.field("completed", "status") == "failed"
    log = session_start.state / "sweep.log"
    assert "onevcs sweep double: exiting 3" in log.read_text(encoding="utf-8")

    reported = session_start.start()

    assert f"the last sweep failed: exit 3 (log: {log})" in reported.stderr, reported.stderr
    assert "the host sweep reported a failure" in reported.stderr
    assert failing.returncode == reported.returncode == 0, failing.stderr + reported.stderr

    # Once the hour has passed the next start runs it again, and the line naming the
    # failure names the log kept apart from the one the new job writes.
    completed = session_start.state / "completed"
    completed.write_text(
        completed.read_text(encoding="utf-8").replace(
            f"finished {session_start.field('completed', 'finished')}",
            f"finished {int(time.time()) - host_sweep_interval() - 1}",
        ),
        encoding="utf-8",
    )
    again = session_start.start()

    kept = session_start.state / "sweep.failed.log"
    assert f"the last sweep failed: exit 3 (log: {kept}); started again as job" in again.stderr
    assert "onevcs sweep double: exiting 3" in kept.read_text(encoding="utf-8")
    assert again.returncode == 0, again.stderr


def test_a_session_starts_job_runs_both_real_verbs_with_the_default_floor(
    tmp_path: Path,
) -> None:
    """The detached job is the only automatic caller of all three reclamations.

    Run with the real `onevcs` and `oneagentgraph` over a sandboxed state root: scratch
    aged past the four-hour floor is gone and scratch inside it is kept, and onevcs's own
    report — its workspaces and its session records — is a real pass, not a rehearsal,
    at that floor.
    """
    session_start = SessionStart(tmp_path, real_verbs=True)
    temp = session_start.scratch["TMPDIR"]
    aged: dict[str, Path] = {}
    for name, hours in (("old", 6), ("young", 2)):
        directory = temp / f"oneagentgraph-{name}"
        directory.mkdir()
        (directory / "owner.lock").write_text("999999 1\n", encoding="utf-8")
        stamp = time.time() - hours * 3600
        for path in (directory / "owner.lock", directory):
            os.utime(path, (stamp, stamp))
        aged[name] = directory
    try:
        start = session_start.start()

        assert "host-sweep: started job" in start.stderr, start.stderr
        session_start.await_completion()
    finally:
        session_start.settle()

    log = (session_start.state / "sweep.log").read_text(encoding="utf-8")
    assert session_start.field("completed", "status") == "done", log
    assert not aged["old"].exists(), log
    assert aged["young"].exists(), log
    assert "onevcs sweep: reclaimed" in log, log
    assert "keeping anything written inside the last 4 hour(s)" in log, log
    assert "Session records with nothing left behind them:" in log, log
    assert "rehearsal" not in log, log


def _host_sweep(
    session_start: SessionStart, *arguments: str, **environment: str
) -> subprocess.CompletedProcess[str]:
    """The fixture's `scripts/host-sweep.sh` as session setup runs it, in the given environment."""
    return subprocess.run(
        ["bash", str(session_start.repo / "scripts" / "host-sweep.sh"), *arguments],
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env={**session_start.environment, **environment},
        timeout=e2e_timeout(SESSION_START_SECONDS),
    )


def test_a_cache_home_the_sweep_cannot_use_starts_nothing_and_never_fails_setup(
    session_start: SessionStart, tmp_path: Path
) -> None:
    """A relative or unusable cache home is refused by name, and session setup goes on."""
    relative = session_start.start(XDG_CACHE_HOME="relative/cache")

    assert "XDG_CACHE_HOME must be an absolute path, not 'relative/cache'" in relative.stderr
    assert "the host sweep reported a failure or could not start" in relative.stderr
    assert relative.returncode == 0, relative.stderr

    (tmp_path / "a-file").write_text("", encoding="utf-8")
    unusable = session_start.start(XDG_CACHE_HOME=str(tmp_path / "a-file" / "cache"))

    assert "cannot create" in unusable.stderr and "a-file" in unusable.stderr, unusable.stderr
    assert unusable.returncode == 0, unusable.stderr

    homeless = _host_sweep(session_start, "--detach", HOME="relative", XDG_CACHE_HOME="")
    assert homeless.returncode == 2
    assert "HOME must name an absolute directory" in homeless.stderr
    assert session_start.verb_calls() == []


def test_a_host_without_just_starts_no_sweep(session_start: SessionStart) -> None:
    """The job is the recipe, so a PATH without `just` refuses before anything starts."""
    result = _host_sweep(session_start, "--detach", PATH="/usr/bin:/bin")

    assert result.returncode == 2
    assert "'just' is not on PATH, so no sweep can be started" in result.stderr
    assert session_start.verb_calls() == []


def test_the_job_form_refuses_without_the_lock_it_is_handed(session_start: SessionStart) -> None:
    """`--job` is only what `--detach` starts, and sweeps nothing run any other way."""
    result = _host_sweep(session_start, "--job")

    assert result.returncode == 2
    assert "--job runs only under the lock --detach hands it" in result.stderr
    assert session_start.verb_calls() == []


def test_an_unknown_argument_is_refused_with_the_usage(session_start: SessionStart) -> None:
    result = _host_sweep(session_start, "--now")

    assert result.returncode == 2
    assert "usage: host-sweep.sh --detach" in result.stderr


def test_a_lock_or_log_the_sweep_cannot_open_starts_nothing(session_start: SessionStart) -> None:
    """Each refusal names the path it could not use; no verb runs beside either."""
    session_start.state.mkdir(parents=True)
    (session_start.state / "lock").mkdir()

    unlocked = _host_sweep(session_start, "--detach")
    by_hand = session_start.hand_sweep()

    assert unlocked.returncode == 2
    assert f"cannot open the host sweep lock {session_start.state / 'lock'}" in unlocked.stderr
    assert by_hand.returncode != 0
    assert f"cannot open the host sweep lock {session_start.state / 'lock'}" in by_hand.stderr

    (session_start.state / "lock").rmdir()
    (session_start.state / "sweep.log").mkdir()
    unlogged = _host_sweep(session_start, "--detach")

    assert unlogged.returncode == 2
    assert (
        f"cannot write the sweep log {session_start.state / 'sweep.log'}; no sweep started"
    ) in unlogged.stderr
    assert session_start.verb_calls() == []
