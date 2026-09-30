"""The host sweep session setup starts detached: never waited on, one at a time, hourly.

Every journey runs the real `scripts/session-setup.sh`, `scripts/host-sweep.sh`,
`scripts/setup-llmlint.sh`, `justfile` and `scripts/repos-bootstrap.sh`, copied into a
fixture repository, through the real `just`, `uv`, `flock` and `setsid`. Only the
published CLIs are doubled, at the PATH boundary, and one journey runs the real `onevcs`
and `oneagentgraph` instead. Each points `XDG_CACHE_HOME` at its own directory, so no
journey touches this host's lock, and ends by releasing its doubles and waiting until no
process or job it started is alive; none signals anything.

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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ScratchRoots:
    """The state roots the sweep verbs judge, each inside the journey's own `tmp_path`."""

    onevcs_home: Path
    oneagentgraph_state: Path
    temp: Path

    @property
    def environment(self) -> dict[str, str]:
        return {
            "ONEVCS_HOME": str(self.onevcs_home),
            "ONEAGENTGRAPH_STATE_DIR": str(self.oneagentgraph_state),
            "TMPDIR": str(self.temp),
        }


#: The one place the interval is declared, read off the script.
HOST_SWEEP = REPO_ROOT / "scripts" / "host-sweep.sh"


def host_sweep_interval() -> int:
    found = re.search(
        r"(?m)^readonly HOST_SWEEP_INTERVAL_SECONDS=(\d+)$",
        HOST_SWEEP.read_text(encoding="utf-8"),
    )
    assert found is not None, "host-sweep.sh no longer declares its interval in one place"
    return int(found.group(1))


#: How far ahead of the real clock the stepped-clock journey seeds a completion stamp: a
#: whole number of seconds past any second boundary, and far enough ahead that the start
#: always reads the clock behind it, as it would after a backward step.
STAMP_AHEAD_SECONDS = 30


def host_sweep_clock_step() -> int:
    found = re.search(
        r"(?m)^readonly HOST_SWEEP_CLOCK_STEP_SECONDS=(\d+)$",
        HOST_SWEEP.read_text(encoding="utf-8"),
    )
    assert found is not None, "host-sweep.sh no longer declares its clock step in one place"
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
def _real_tool_path(directory: Path) -> str:
    """A directory linking the real `just`, `uv` and `uvx`, resolved past any shim.

    A journey's `HOME` is its own, which a shim reads its configuration from, so the
    binaries themselves are named: `uv run` exports its own path as `UV`. They are linked
    into a directory of the journey's own rather than their install directories being put
    on PATH, because those also hold this host's `llmlint`, which the real
    `setup-llmlint.sh` would otherwise find and run.
    """
    just = shutil.which("just")
    uv = shutil.which("uv")
    assert just is not None and uv is not None, "the journeys need `just` and `uv` on PATH"
    resolved = Path(
        subprocess.run(
            [uv, "run", "--no-project", "printenv", "UV"],
            cwd="/",
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    directory.mkdir()
    (directory / "just").symlink_to(Path(just).resolve())
    (directory / "uv").symlink_to(resolved)
    if (resolved.parent / "uvx").exists():
        (directory / "uvx").symlink_to(resolved.parent / "uvx")
    return str(directory)


def _refused(session_start: SessionStart, fault: str) -> str:
    """The line a start prints refusing the planted completion record for `fault`."""
    record = session_start.state / "completed"
    return f"refused the completion record {record}, which no sweep wrote: {fault}; read as none"


def _start_line(stderr: str) -> str:
    """The one line a start that went ahead prints, which carries whatever it also says."""
    lines = [line for line in stderr.splitlines() if line.startswith("host-sweep: ")]
    assert len(lines) == 1, stderr
    assert lines[0].startswith("host-sweep: started job "), stderr
    return lines[0]


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
            "setup-llmlint.sh",
        ):
            shutil.copy(REPO_ROOT / "scripts" / name, scripts / name)
        shutil.copyfile(REPO_ROOT / "justfile", self.repo / "justfile")
        config = self.repo / "config"
        config.mkdir()
        for declared in (REPO_ROOT / "config").glob("*.version"):
            shutil.copy(declared, config / declared.name)
        venv_bin = self.repo / ".venv" / "bin"
        if real_verbs:
            (self.repo / ".venv").symlink_to(REPO_ROOT / ".venv")
        else:
            # llmlint: ignore-block[e2e_not_mocked] Each double is a published CLI session
            # setup verifies or the recipe delegates to, at the PATH boundary
            # `tests/AGENTS.md` sanctions; the sweep verbs hold on a file so a journey can
            # watch the lock while a sweep runs. The scripts, recipe, `just`, `uv`, `flock`
            # and `setsid` stay real, and `real_verbs` runs the real verbs instead.
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
            # llmlint: ignore-end[e2e_not_mocked]
        # llmlint: ignore-block[e2e_not_mocked] `bun` is a published toolchain binary session
        # setup only verifies by version; installing it for real is outside the sweep's change.
        _write_version_double(tmp_path / ".local" / "node" / "bin" / "bun", "1.2.3")
        # llmlint: ignore-end[e2e_not_mocked]
        checkouts = tmp_path / "checkouts"
        checkouts.write_text("# no sibling checkout in this journey\n", encoding="utf-8")
        self.calls = tmp_path / "sweep-calls"
        self.release_file = tmp_path / "release"
        self.state = tmp_path / "cache" / "ai-orchestrator" / "sweep"
        self.scratch = ScratchRoots(
            onevcs_home=tmp_path / "onevcs-home",
            oneagentgraph_state=tmp_path / "oneagentgraph-state",
            temp=tmp_path / "tmp",
        )
        (self.scratch.onevcs_home / "workspaces").mkdir(parents=True)
        self.scratch.oneagentgraph_state.mkdir()
        self.scratch.temp.mkdir()
        self.environment = {
            "HOME": str(tmp_path),
            "PATH": f"{_real_tool_path(tmp_path / 'tool-bin')}:/usr/bin:/bin",
            # The real `setup-llmlint.sh` runs `uv tool install` into a tool directory of
            # the journey's own, resolving against an empty local index with no Python
            # download, so it reaches no network and ends in its logged no-install exit.
            "UV_TOOL_DIR": str(tmp_path / "uv-tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / "uv-tools" / "bin"),
            "UV_DEFAULT_INDEX": (tmp_path / "empty-index").as_uri(),
            "UV_PYTHON_DOWNLOADS": "never",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS": str(checkouts),
            "TEST_SWEEP_CALLS": str(self.calls),
            **self.scratch.environment,
        }
        (tmp_path / "empty-index").mkdir()
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

    def spawn_hand_sweep(self, *arguments: str, **extra_env: str) -> subprocess.Popen[str]:
        """`just sweep` run by hand in the fixture repository, its doubles on PATH."""
        environment = {**self.environment, **extra_env}
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

    def hand_sweep(self, *arguments: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        """A hand-run to completion, bounded well below any sweep a journey holds."""
        process = self.spawn_hand_sweep(*arguments, **extra_env)
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

    # llmlint: ignore-block[tests_mirror_real_usage] An hour passing has no interface a
    # journey can drive short of waiting it out, so the stamp the script reads is moved
    # back past the interval it declares; everything the start then does is the real
    # script's.
    def pass_the_interval(self) -> int:
        """Move the completion stamp back past the interval; returns the time it was moved at."""
        now = int(time.time())
        completed = self.state / "completed"
        completed.write_text(
            completed.read_text(encoding="utf-8").replace(
                f"finished {self.field('completed', 'finished')}",
                f"finished {now - host_sweep_interval() - 1}",
            ),
            encoding="utf-8",
        )
        return now

    # llmlint: ignore-end[tests_mirror_real_usage]

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
    assert "setup-llmlint: installing the highest llmlint-cli" in first.stderr, first.stderr
    assert "setup-llmlint: llmlint not installed" in first.stderr, first.stderr
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

    # llmlint: ignore-block[tests_mirror_real_usage] An hour passing has no interface
    # short of waiting it out; `pass_the_interval` says why the stamp is moved instead.
    now = session_start.pass_the_interval()
    # llmlint: ignore-end[tests_mirror_real_usage]
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
    # A spelling the verbs refuse is still a rehearsal, never a failed sweep to hold off.
    refused = session_start.hand_sweep("--dry-run=yes", TEST_SWEEP_EXIT="2")

    assert refused.returncode != 0, refused.stderr
    assert len(session_start.verb_calls()) == 4
    assert not (session_start.state / "completed").exists()

    start = session_start.start()

    assert "host-sweep: started job" in start.stderr, start.stderr
    session_start.await_completion()
    assert session_start.verb_calls()[4:] == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]


# llmlint: ignore-block[tests_mirror_real_usage] A completion record the sweep cannot
# read is a state only the host's disk can be in, so the journey plants one; it still
# drives the real recipe and reads only what it reports and leaves.
def test_a_dry_run_by_hand_sweeps_beside_a_completion_record_it_cannot_read(
    session_start: SessionStart,
) -> None:
    """A rehearsal never reads the stamp, so an unreadable one cannot refuse or change it."""
    session_start.state.mkdir(parents=True)
    completed = session_start.state / "completed"
    completed.mkdir()

    rehearsal = session_start.hand_sweep("--dry-run")

    assert rehearsal.returncode == 0, rehearsal.stderr
    assert "cannot read the completion record" not in rehearsal.stderr
    assert session_start.verb_calls() == [
        "onevcs sweep --dry-run --min-age-hours 4",
        "oneagentgraph sweep --dry-run --min-age-hours 4",
    ]
    assert completed.is_dir() and not any(completed.iterdir())
    completed.rmdir()


# llmlint: ignore-end[tests_mirror_real_usage]


def test_a_failed_sweep_is_named_with_its_log_by_the_next_start_and_never_fails_it(
    session_start: SessionStart,
) -> None:
    """The next start names the failure and its log; its exit is the toolchain's alone."""
    failing = session_start.start(TEST_SWEEP_EXIT="3")
    session_start.await_completion()
    assert session_start.field("completed", "status") == "failed"
    assert session_start.field("completed", "exit") == "3"
    log = session_start.state / "sweep.log"
    written = log.read_text(encoding="utf-8")
    assert "onevcs sweep double: exiting 3" in written
    assert re.search(r"host-sweep: job \d+ failed, exit 3;", written), written

    reported = session_start.start()

    assert f"the last sweep failed: exit 3 (log: {log})" in reported.stderr, reported.stderr
    assert "the host sweep reported a failure" in reported.stderr
    assert failing.returncode == reported.returncode == 0, failing.stderr + reported.stderr

    # Once the hour has passed the next start runs it again, and the line naming the
    # failure names the log kept apart from the one the new job writes.
    # llmlint: ignore-block[tests_mirror_real_usage] An hour passing has no interface
    # short of waiting it out; `pass_the_interval` says why the stamp is moved instead.
    session_start.pass_the_interval()
    # llmlint: ignore-end[tests_mirror_real_usage]
    again = session_start.start()

    kept = session_start.state / "sweep.failed.log"
    assert f"the last sweep failed: exit 3 (log: {kept}); started again as job" in again.stderr
    assert "onevcs sweep double: exiting 3" in kept.read_text(encoding="utf-8")
    assert again.returncode == 0, again.stderr


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] It is not expensive:
# about 1.4 s over a `tmp_path` state root (`pytest --durations`), spending no launch, no
# harness turn and no network.
# llmlint: ignore-block[shell_test_tiers_stay_split] It already has a project of its own:
# `host-sweep`, outside the offline script tests, and every journey there runs this one
# subject through real `just`, `flock` and `setsid`; the verbs this one runs for real are
# the ones its siblings double, so another project would split one subject in two to save
# about 1.4 s.
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
    temp = session_start.scratch.temp
    aged: dict[str, Path] = {}
    # llmlint: ignore-block[tests_mirror_real_usage] Scratch aged past the four-hour floor
    # has no interface short of waiting four hours, so the journey seeds oneagentgraph's
    # scratch shape with backdated times, as the task's own proof of this job asks; the
    # sweep that reclaims it is the real verb, run by the real detached job.
    for name, hours in (("old", 6), ("young", 2)):
        directory = temp / f"oneagentgraph-{name}"
        directory.mkdir()
        (directory / "owner.lock").write_text("999999 1\n", encoding="utf-8")
        stamp = time.time() - hours * 3600
        for path in (directory / "owner.lock", directory):
            os.utime(path, (stamp, stamp))
        aged[name] = directory
    # llmlint: ignore-end[tests_mirror_real_usage]
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


# llmlint: ignore-end[shell_test_tiers_stay_split]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


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
    """A relative, multi-line or unusable cache home is refused by name, and setup goes on."""
    relative = session_start.start(XDG_CACHE_HOME="relative/cache")

    assert "XDG_CACHE_HOME must be an absolute path, not 'relative/cache'" in relative.stderr
    assert "the host sweep reported a failure or could not start" in relative.stderr
    assert relative.returncode == 0, relative.stderr

    (tmp_path / "a-file").write_text("", encoding="utf-8")
    unusable = session_start.start(XDG_CACHE_HOME=str(tmp_path / "a-file" / "cache"))

    cache = tmp_path / "a-file" / "cache" / "ai-orchestrator" / "sweep"
    assert f"cannot create {cache}: Not a directory;" in unusable.stderr, unusable.stderr
    assert unusable.returncode == 0, unusable.stderr

    newline = _host_sweep(session_start, "--detach", XDG_CACHE_HOME=f"{tmp_path}/two\nlines")
    assert newline.returncode == 2
    assert "XDG_CACHE_HOME must not contain a newline" in newline.stderr, newline.stderr
    assert not (tmp_path / "two\nlines").exists()
    home = f"{tmp_path}/home\nlines"
    newline_home = _host_sweep(session_start, "--detach", HOME=home, XDG_CACHE_HOME="")
    assert newline_home.returncode == 2
    assert "nor HOME when it stands in" in newline_home.stderr, newline_home.stderr
    assert not Path(home).exists()

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


# llmlint: ignore-block[tests_mirror_real_usage] The refusal under test is exactly what
# `--job` does when run some way other than `--detach` starts it, so the journeys run it
# that way, one of them from a shell holding the lock file open on descriptor 9, the
# state a hand-run could inherit; no other interface reaches either.
def test_the_job_form_refuses_without_the_lock_it_is_handed(session_start: SessionStart) -> None:
    """`--job` is only what `--detach` starts, and sweeps nothing run any other way."""
    result = _host_sweep(session_start, "--job")

    assert result.returncode == 2
    assert "--job runs only under the lock --detach hands it" in result.stderr
    assert session_start.verb_calls() == []


def test_a_descriptor_on_the_lock_without_the_job_token_is_no_job(
    session_start: SessionStart,
) -> None:
    """Only the token `--detach` records makes a lock on descriptor 9 the job's.

    A caller that opened descriptor 9 on the lock file itself is refused as `--job`, and a
    hand-run from such a shell is a hand-run: it records its own completion.
    """
    session_start.state.mkdir(parents=True)
    opened = 'exec 9>>"$LOCK"; exec "$@"'
    lock = {"LOCK": str(session_start.state / "lock")}
    script = str(session_start.repo / "scripts" / "host-sweep.sh")

    job = _host_sweep_in(session_start, ["bash", "-c", opened, "-", "bash", script, "--job"], lock)

    assert job.returncode == 2, job.stderr
    assert "--job runs only under the lock --detach hands it" in job.stderr
    assert session_start.verb_calls() == []

    justfile = str(session_start.repo / "justfile")
    venv = session_start.repo / ".venv" / "bin"
    by_hand = _host_sweep_in(
        session_start,
        ["bash", "-c", opened, "-", "just", "--justfile", justfile, "sweep"],
        {**lock, "PATH": f"{venv}:{session_start.environment['PATH']}"},
    )

    assert by_hand.returncode == 0, by_hand.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]
    assert session_start.field("completed", "status") == "done"
    assert session_start.field("completed", "log") == "-"


@pytest.mark.parametrize(
    ("recorded", "carried", "with_lock"),
    [("nope", "nope", True), ("1-2-3", "1-2-4", True), ("1-2-3", "1-2-3", False)],
    ids=["malformed-record", "another-token", "token-without-the-lock"],
)
def test_the_job_form_refuses_a_token_or_lock_it_was_not_handed(
    session_start: SessionStart, recorded: str, carried: str, *, with_lock: bool
) -> None:
    """`--job` needs the token `--detach` recorded, well formed, and the lock beside it."""
    session_start.state.mkdir(parents=True)
    (session_start.state / "job").write_text(f"token {recorded}\n", encoding="utf-8")
    script = str(session_start.repo / "scripts" / "host-sweep.sh")
    argv = ["bash", script, "--job"]
    if with_lock:
        argv = ["bash", "-c", 'exec 9>>"$LOCK"; exec "$@"', "-", *argv]

    job = _host_sweep_in(
        session_start,
        argv,
        {"LOCK": str(session_start.state / "lock"), "HOST_SWEEP_JOB_TOKEN": carried},
    )

    assert job.returncode == 2, job.stderr
    assert "--job runs only under the lock --detach hands it" in job.stderr
    assert session_start.verb_calls() == []


def _host_sweep_in(
    session_start: SessionStart, argv: list[str], environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """`argv` run in the fixture repository, in the journey's environment plus `environment`."""
    return subprocess.run(
        argv,
        cwd=session_start.repo,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env={**session_start.environment, **environment},
        timeout=e2e_timeout(SESSION_START_SECONDS),
    )


# llmlint: ignore-end[tests_mirror_real_usage]


def test_an_unknown_or_extra_argument_is_refused_with_the_usage(
    session_start: SessionStart,
) -> None:
    for arguments in (("--now",), ("--detach", "--now")):
        result = _host_sweep(session_start, *arguments)

        assert result.returncode == 2, arguments
        assert f"refused arguments: {' '.join(arguments)}" in result.stderr
        assert "usage: host-sweep.sh --detach" in result.stderr
    assert session_start.verb_calls() == []


# llmlint: ignore-block[tests_mirror_real_usage] A lock or log path the script cannot
# open is a state only the host's disk can be in, so the journey plants one; it still
# drives the real script and recipe and reads only what they report.
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
        f"cannot write the sweep log {session_start.state / 'sweep.log'}: Is a directory; "
        "no sweep started"
    ) in unlogged.stderr
    assert session_start.verb_calls() == []


# llmlint: ignore-end[tests_mirror_real_usage]


# llmlint: ignore-block[tests_mirror_real_usage] The interval is declared once in the
# script and tests read it from there, by the task that introduced it; this is the drift
# gate holding the documented hour to that declaration, while the journey above drives
# the interval through real session starts on either side of it.
def test_the_interval_is_the_hour_the_documentation_promises() -> None:
    """docs/host-setup.md and AGENTS.md say "at most once an hour"; this is that promise.

    Moving the interval is a change to what they tell an operator, so it fails here until
    the prose moves with it.
    """
    assert host_sweep_interval() == 3600


# llmlint: ignore-end[tests_mirror_real_usage]


# llmlint: ignore-block[tests_mirror_real_usage] Each journey below plants a state the
# script finds on disk — a record no sweep wrote, a path it cannot write, a failed log it
# cannot move — because only a host in that state reaches those paths; each still drives
# the real session setup or recipe and reads only what the script reports and records.
def test_a_stamp_just_ahead_of_the_clock_is_the_sweep_that_just_ran(
    session_start: SessionStart,
) -> None:
    """A completion stamp a clock step puts ahead of now still holds a start off.

    A host whose clock is corrected by stepping — NTP, or a VM agent setting the guest's
    time from its host's every few seconds — can move back past the second a job's stamp
    was written in, so the next start reads that stamp as ahead of its own clock. Read as
    a stamp no sweep wrote, that started a second sweep inside the hour. The state is
    seeded, and the start reads the real clock.
    """
    assert STAMP_AHEAD_SECONDS < host_sweep_clock_step() < host_sweep_interval()
    session_start.state.mkdir(parents=True)
    ahead = int(time.time()) + STAMP_AHEAD_SECONDS
    (session_start.state / "completed").write_text(
        f"status done\nexit 0\nfinished {ahead}\nlog -\n", encoding="utf-8"
    )

    start = session_start.start()

    assert "the next is due in" in start.stderr and "none started" in start.stderr, start.stderr
    assert session_start.verb_calls() == []


def test_a_record_with_a_key_it_never_writes_or_a_key_twice_is_refused_by_name(
    session_start: SessionStart,
) -> None:
    """Only a record in the shape a sweep writes holds a start off; any other is named."""
    session_start.state.mkdir(parents=True)
    now = int(time.time())
    (session_start.state / "completed").write_text(
        f"status done\nexit 0\nfinished {now}\nfinished {now}\nlog -\n", encoding="utf-8"
    )

    duplicated = session_start.start()

    assert (_refused(session_start, "it carries 'finished' twice")) in _start_line(
        duplicated.stderr
    )
    session_start.await_completion(since=now)
    session_start.settle()
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]
    (session_start.state / "completed").write_text(
        f"status done\nexit 0\nfinished {int(time.time())}\nlog -\nsource elsewhere\n",
        encoding="utf-8",
    )

    unknown = session_start.start()

    assert (
        _refused(session_start, "it carries 'source', a key no sweep writes there")
    ) in _start_line(unknown.stderr)
    session_start.await_calls(4)


def test_a_record_line_padded_with_whitespace_is_refused_by_name(
    session_start: SessionStart,
) -> None:
    """A value is the rest of its line after one space, so padding no sweep writes is named."""
    session_start.state.mkdir(parents=True)
    now = int(time.time())
    (session_start.state / "completed").write_text(
        f"status done\nexit 0 \nfinished {now}\nlog -\n", encoding="utf-8"
    )

    start = session_start.start()

    assert (_refused(session_start, "its 'exit' is not a value a sweep writes")) in _start_line(
        start.stderr
    )
    session_start.await_calls(2)
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]


def test_a_stamp_further_ahead_than_a_clock_step_is_read_as_absent(
    session_start: SessionStart,
) -> None:
    """A completion no sweep could have written never holds starts off until its date."""
    session_start.state.mkdir(parents=True)
    future = int(time.time()) + 10 * host_sweep_interval()
    (session_start.state / "completed").write_text(
        f"status done\nexit 0\nfinished {future}\nlog -\n", encoding="utf-8"
    )

    start = session_start.start()

    assert "host-sweep: started job" in start.stderr, start.stderr
    session_start.await_calls(2)
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]


def test_a_completion_record_the_sweep_cannot_read_is_named_and_starts_nothing(
    session_start: SessionStart,
) -> None:
    """An unreadable stamp is not an absent one, so it never starts a sweep every session.

    A holder record that cannot be read is named as that to a hand-run meeting its lock.
    """
    session_start.state.mkdir(parents=True)
    completed = session_start.state / "completed"
    completed.mkdir()

    start = session_start.start()

    assert f"cannot read the completion record {completed}: Is a directory;" in start.stderr, (
        start.stderr
    )
    assert "none started" in start.stderr
    assert "the host sweep reported a failure or could not start" in start.stderr
    assert start.returncode == 0, start.stderr
    assert session_start.verb_calls() == []

    completed.rmdir()
    (session_start.state / "holder").mkdir()
    session_start.hold_sweeps()
    first = session_start.start()
    started = re.search(r"started job (\d+) ", first.stderr)
    assert started is not None, first.stderr
    job = int(started.group(1))
    session_start.await_calls(1)

    refused = session_start.hand_sweep()

    assert refused.returncode == 1, refused.stderr
    assert (
        f"a holder whose record {session_start.state / 'holder'} cannot be read"
    ) in refused.stderr
    assert len(session_start.verb_calls()) == 1

    session_start.release()
    until(
        f"the sweep job {job} to exit",
        lambda: not _pid_alive(job),
        seconds=120,
        state=lambda: f"calls {session_start.verb_calls()}",
    )
    # The fixture's teardown reads the holder record, which a file is again.
    shutil.rmtree(session_start.state / "holder")


def test_a_job_token_the_start_cannot_record_starts_no_sweep(
    session_start: SessionStart,
) -> None:
    """Without its token a job could not tell its lock was handed to it, so none starts."""
    session_start.state.mkdir(parents=True)
    (session_start.state / "job.new").mkdir()

    start = session_start.start()

    assert (
        f"cannot record the job token in {session_start.state} (Is a directory)" in start.stderr
    ), start.stderr
    assert "the host sweep reported a failure or could not start" in start.stderr
    assert start.returncode == 0, start.stderr
    assert session_start.verb_calls() == []


def test_a_record_the_sweep_cannot_write_is_named_and_sweeps_anyway(
    session_start: SessionStart,
) -> None:
    """An unwritable holder or completion record costs the report, never the sweep.

    With no stamp recorded, the next start sweeps again, which is the safe side of a
    completion that could not be written. A holder that never recorded itself is named
    as such to a start that meets its lock.
    """
    session_start.state.mkdir(parents=True)
    for record in ("holder", "completed"):
        (session_start.state / f"{record}.new").mkdir()

    by_hand = session_start.hand_sweep()

    assert by_hand.returncode == 0, by_hand.stderr
    assert f"holder not recorded in {session_start.state} (Is a directory)" in by_hand.stderr
    assert f"completion not recorded in {session_start.state} (Is a directory)" in by_hand.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]

    session_start.hold_sweeps()
    first = session_start.start()
    started = re.search(r"started job (\d+) ", first.stderr)
    assert started is not None, first.stderr
    job = int(started.group(1))
    assert f"holder not recorded in {session_start.state} (Is a directory)" in _start_line(
        first.stderr
    )
    session_start.await_calls(3)

    second = session_start.start()

    assert "a sweep is running, a holder that has not yet recorded itself" in second.stderr
    assert len(session_start.verb_calls()) == 3

    session_start.release()
    until(
        f"the sweep job {job} to exit",
        lambda: not _pid_alive(job),
        seconds=120,
        state=lambda: f"calls {session_start.verb_calls()}",
    )
    log = (session_start.state / "sweep.log").read_text(encoding="utf-8")
    assert f"completion not recorded in {session_start.state} (Is a directory)" in log, log
    assert not (session_start.state / "completed").exists()


def test_a_failed_log_that_cannot_be_kept_apart_is_appended_to_not_lost(
    session_start: SessionStart,
) -> None:
    """The failure a log holds survives the next job even when it cannot be moved aside."""
    session_start.start(TEST_SWEEP_EXIT="3")
    session_start.await_completion()
    log = session_start.state / "sweep.log"
    blocked = session_start.state / "sweep.failed.log"
    blocked.mkdir()
    (blocked / "occupied").touch()
    since = session_start.pass_the_interval()

    again = session_start.start()

    assert f"the last sweep failed: exit 3 (log: {log}); started again as job" in again.stderr
    session_start.await_completion(since=since)
    kept = log.read_text(encoding="utf-8")
    assert kept.count("onevcs sweep double: exiting 3") == 1, kept
    assert kept.count("onevcs sweep double: exiting 0") == 1, kept


def test_a_log_outside_the_sweep_directory_is_never_reported(
    session_start: SessionStart,
) -> None:
    """A record naming a log the script never writes is refused by name, its log never shown."""
    foreign = "/etc/hostname"
    session_start.state.mkdir(parents=True)
    (session_start.state / "completed").write_text(
        f"status failed\nexit 3\nfinished {int(time.time())}\nlog {foreign}\n",
        encoding="utf-8",
    )
    session_start.hold_sweeps()

    start = session_start.start()

    started = re.search(r"started job (\d+) ", start.stderr)
    assert started is not None, start.stderr
    job = int(started.group(1))
    assert "the last sweep failed" not in start.stderr
    assert (_refused(session_start, "its 'log' is not a value a sweep writes")) in start.stderr
    assert foreign not in start.stderr
    session_start.await_calls(1)
    holder = session_start.state / "holder"
    holder.write_text(f"pid {job}\nlog {foreign}\n", encoding="utf-8")

    refused = session_start.hand_sweep()

    assert (
        f"another sweep holds the host lock, a holder whose record {holder} no sweep wrote: "
        "its 'log' is not a value a sweep writes"
    ) in refused.stderr
    assert foreign not in refused.stderr
    session_start.release()
    until(
        f"the sweep job {job} to exit",
        lambda: not _pid_alive(job),
        seconds=120,
        state=lambda: f"calls {session_start.verb_calls()}",
    )


def test_a_record_in_a_shape_the_script_never_writes_is_refused_by_name(
    session_start: SessionStart,
) -> None:
    """A malformed stamp is refused by name and starts a sweep; a malformed holder is named."""
    session_start.state.mkdir(parents=True)
    # A leading zero is octal to shell arithmetic: this is the present moment written in
    # octal, which a reader that trusted it would take as a sweep completed just now.
    octal_now = "0" + format(int(time.time()), "o")
    (session_start.state / "completed").write_text(
        f"status weird\nexit 003\nfinished {octal_now}\nlog relative\n", encoding="utf-8"
    )
    session_start.hold_sweeps()

    start = session_start.start()

    started = re.search(r"started job (\d+) ", start.stderr)
    assert started is not None, start.stderr
    job = int(started.group(1))
    assert "the last sweep failed" not in start.stderr
    assert (_refused(session_start, "its 'status' is not a value a sweep writes")) in start.stderr
    session_start.await_calls(1)
    holder = session_start.state / "holder"
    holder.write_text("pid 0\nlog relative\n", encoding="utf-8")

    held = session_start.start()

    assert (
        f"a sweep is running, a holder whose record {holder} no sweep wrote: "
        "its 'pid' is not a value a sweep writes"
    ) in held.stderr
    session_start.release()
    until(
        f"the sweep job {job} to exit",
        lambda: not _pid_alive(job),
        seconds=120,
        state=lambda: f"calls {session_start.verb_calls()}",
    )


def test_a_malformed_stamp_alone_is_refused_by_name_and_sweeps(
    session_start: SessionStart,
) -> None:
    """A record whose only fault is its stamp, in octal, is refused rather than holding off."""
    session_start.state.mkdir(parents=True)
    octal_now = "0" + format(int(time.time()), "o")
    (session_start.state / "completed").write_text(
        f"status done\nexit 0\nfinished {octal_now}\nlog -\n", encoding="utf-8"
    )

    start = session_start.start()

    line = _start_line(start.stderr)
    assert _refused(session_start, "its 'finished' is not a value a sweep writes") in line, line
    started = re.search(r"started job (\d+) ", line)
    assert started is not None, line
    job = int(started.group(1))
    until(
        f"the sweep job {job} to exit",
        lambda: not _pid_alive(job),
        seconds=120,
        state=lambda: f"calls {session_start.verb_calls()}",
    )
    assert session_start.field("completed", "status") == "done"
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]


@pytest.mark.parametrize(
    ("tail", "fault"),
    [
        ("", "it lacks 'log'"),
        ("log -\npid 12345\n", "it carries 'pid', a key no sweep writes there"),
        ("log -\ntoken 1-2-3", "it carries 'token', a key no sweep writes there"),
    ],
    ids=["missing-key", "holder-key", "unterminated-last-line"],
)
def test_a_completion_record_with_other_keys_than_a_sweep_writes_is_refused_by_name(
    session_start: SessionStart, tail: str, fault: str
) -> None:
    """A completion carries exactly its own four keys, the last line read with or without a
    newline; one missing, or one another record's writer writes, is named and read as none.
    """
    session_start.state.mkdir(parents=True)
    (session_start.state / "completed").write_text(
        f"status done\nexit 0\nfinished {int(time.time())}\n{tail}", encoding="utf-8"
    )

    start = session_start.start()

    assert _refused(session_start, fault) in start.stderr, start.stderr
    assert "host-sweep: started job" in start.stderr, start.stderr
    session_start.await_calls(2)


CONTRADICTED = "its status contradicts its exit"


@pytest.mark.parametrize(
    ("status", "exit_status", "fault"),
    [
        ("weird", "0", "its 'status' is not a value a sweep writes"),
        ("done", "3", CONTRADICTED),
        ("failed", "0", CONTRADICTED),
        # No process returns more than 255, so a larger exit is one no sweep recorded.
        ("failed", "256", "its 'exit' is not a value a sweep writes"),
    ],
)
def test_a_fresh_stamp_in_a_record_no_sweep_wrote_is_refused_by_name(
    session_start: SessionStart, status: str, exit_status: str, fault: str
) -> None:
    """The record is read whole: a stamp from just now defers nothing beside a bad status."""
    session_start.state.mkdir(parents=True)
    (session_start.state / "completed").write_text(
        f"status {status}\nexit {exit_status}\nfinished {int(time.time())}\nlog -\n",
        encoding="utf-8",
    )

    start = session_start.start()

    assert "host-sweep: started job" in start.stderr, start.stderr
    assert _refused(session_start, fault) in start.stderr, start.stderr
    # Only the job records its log's path; the planted record names none.
    job_log = str(session_start.state / "sweep.log")
    until(
        "the sweep to record its ending",
        lambda: session_start.field("completed", "log") == job_log,
        seconds=120,
        state=lambda: f"calls {session_start.verb_calls()}",
    )
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]


# llmlint: ignore-end[tests_mirror_real_usage]


def test_a_failed_hand_run_is_named_by_the_next_session_start(
    session_start: SessionStart,
) -> None:
    """A sweep run by hand for real is the stamp, failed or not; its output was its caller's."""
    by_hand = session_start.hand_sweep(TEST_SWEEP_EXIT="4")

    assert by_hand.returncode != 0
    assert session_start.field("completed", "status") == "failed"

    start = session_start.start()

    assert (
        "the last sweep failed: exit 4 (log: its caller's terminal, run by hand); "
        "the next is due in"
    ) in start.stderr, start.stderr
    assert start.returncode == 0, start.stderr
    assert len(session_start.verb_calls()) == 2


def _path_without(directory: Path, missing: str) -> str:
    """A directory linking every real binary of `/usr/bin` but `missing`: a host lacking it."""
    directory.mkdir()
    for binary in Path("/usr/bin").iterdir():
        if binary.name != missing:
            (directory / binary.name).symlink_to(binary)
    return str(directory)


def test_a_job_that_never_ran_is_named_with_its_log_by_the_next_start(
    session_start: SessionStart,
) -> None:
    """A start on a host without `setsid` returns at once; the next start names what happened.

    Session setup never waits on its job, not even for it to start, so the start that
    launched it reports it started. The launch fails into the log and leaves the job's
    record behind, and the next start, finding it with the lock free, names that job as
    never having recorded an ending, with its log kept apart, and sweeps again; neither
    start changes session setup's exit status.
    """
    with_setsid = session_start.environment["PATH"]
    tools = with_setsid.split(":", 1)[0]
    session_start.environment["PATH"] = (
        f"{tools}:{_path_without(session_start.root / 'no-setsid-bin', 'setsid')}"
    )

    launched = session_start.start()

    line = _start_line(launched.stderr)
    started = re.search(r"started job (\d+) ", line)
    assert started is not None, line
    job = int(started.group(1))
    until(
        f"the launch {job} to exit",
        lambda: not _pid_alive(job),
        seconds=60,
        state=lambda: f"calls {session_start.verb_calls()}",
    )
    assert session_start.verb_calls() == []
    assert not (session_start.state / "completed").exists()

    session_start.environment["PATH"] = with_setsid
    named = session_start.start()

    kept = session_start.state / "sweep.failed.log"
    assert (
        "host-sweep: the last sweep job never finished cleanly: it could not start, was "
        f"killed, or could not clear its record (log: {kept}); started again as job"
    ) in named.stderr, named.stderr
    assert "the host sweep reported a failure" in named.stderr, named.stderr
    assert "setsid" in kept.read_text(encoding="utf-8")
    again = re.search(r"started again as job (\d+) ", named.stderr)
    assert again is not None, named.stderr
    rerun = int(again.group(1))
    until(
        f"the sweep job {rerun} to exit",
        lambda: not _pid_alive(rerun),
        seconds=120,
        state=lambda: f"calls {session_start.verb_calls()}",
    )
    assert session_start.field("completed", "status") == "done"
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]
    assert not (session_start.state / "job").exists()
    assert launched.returncode == named.returncode, launched.stderr + named.stderr


def test_a_job_that_never_ran_is_named_inside_the_hour_a_hand_run_opened(
    session_start: SessionStart,
) -> None:
    """A hand-run after a job that never ran holds starts off for the hour, not the report.

    The start inside that hour launches nothing, and still names the job that never
    recorded an ending, with its log, until a job runs.
    """
    with_setsid = session_start.environment["PATH"]
    tools = with_setsid.split(":", 1)[0]
    session_start.environment["PATH"] = (
        f"{tools}:{_path_without(session_start.root / 'no-setsid-bin', 'setsid')}"
    )
    launched = session_start.start()
    started = re.search(r"started job (\d+) ", _start_line(launched.stderr))
    assert started is not None, launched.stderr
    job = int(started.group(1))
    until(
        f"the launch {job} to exit",
        lambda: not _pid_alive(job),
        seconds=60,
        state=lambda: f"calls {session_start.verb_calls()}",
    )
    session_start.environment["PATH"] = with_setsid
    by_hand = session_start.hand_sweep()
    assert by_hand.returncode == 0, by_hand.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]

    inside = session_start.start()

    log = session_start.state / "sweep.log"
    assert (
        "host-sweep: the last sweep job never finished cleanly: it could not start, was "
        f"killed, or could not clear its record (log: {log}); the next is due in"
    ) in inside.stderr, inside.stderr
    assert "none started" in inside.stderr, inside.stderr
    assert "setsid" in log.read_text(encoding="utf-8")
    assert len(session_start.verb_calls()) == 2
    assert inside.returncode == launched.returncode, launched.stderr + inside.stderr


# llmlint: ignore-block[tests_mirror_real_usage] A job record the job cannot remove is a
# state only the host's disk can be in, so the journey puts a directory where the record
# is while the real job sweeps; it drives the real session setup and reads only what the
# job and the next starts report.
def test_a_job_record_the_job_cannot_remove_is_named_until_it_is_gone(
    session_start: SessionStart,
) -> None:
    """A job that swept but could not clear its record says why, and each start names it.

    The job's log carries the operating system's reason and the remedy; the next start,
    inside the hour the job opened, names that log and launches nothing; once the record
    is removed by hand, as the log says, the start after it reports the sweep completed.
    """
    session_start.hold_sweeps()
    first = session_start.start()
    started = re.search(r"started job (\d+) ", _start_line(first.stderr))
    assert started is not None, first.stderr
    job = int(started.group(1))
    session_start.await_calls(1)
    record = session_start.state / "job"
    record.unlink()
    record.mkdir()
    (record / "kept").touch()
    session_start.release()
    until(
        f"the sweep job {job} to exit",
        lambda: not _pid_alive(job),
        seconds=120,
        state=lambda: f"calls {session_start.verb_calls()}",
    )
    log = session_start.state / "sweep.log"
    assert (
        f"host-sweep: job {job} recorded its ending but could not remove {record}: "
        "Is a directory; every session start names this log until it is gone"
    ) in log.read_text(encoding="utf-8")
    assert session_start.field("completed", "status") == "done"

    named = session_start.start()

    assert (
        "host-sweep: the last sweep job never finished cleanly: it could not start, was "
        f"killed, or could not clear its record (log: {log}); the next is due in"
    ) in named.stderr, named.stderr
    assert "none started" in named.stderr, named.stderr
    assert len(session_start.verb_calls()) == 2
    assert named.returncode == first.returncode, first.stderr + named.stderr

    shutil.rmtree(record)
    cleared = session_start.start()

    assert "host-sweep: the last sweep completed 0 min ago" in cleared.stderr, cleared.stderr
    assert len(session_start.verb_calls()) == 2


# llmlint: ignore-end[tests_mirror_real_usage]
