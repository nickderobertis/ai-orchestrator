"""A session warms one schema cache, shares it with everything it starts, and removes it.

`tests/onejudge_bundle.py` is the rule. The session journeys drive it the way a target
does: a real session over this checkout's conftest with several `xdist` workers, each of
whose tests also starts a pytest of its own, all given a temporary directory of the
journey's, which is read while the session runs and again after it ended — once passing
and once failing. The process journeys drive the two exits a session never takes: a warm
that fails, and a forked child of the warming process exiting first.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import onejudge_bundle
import pytest

from orchestrator.root import REPO_ROOT

#: Where a probe reports to; unset in an ordinary run of this module.
PROBE_ENV = "AIO_SCHEMA_CACHE_PROBE"
#: Set for the pytest a probe starts, so it reports rather than starting another.
NESTED_ENV = "AIO_SCHEMA_CACHE_PROBE_NESTED"
#: Set to make every probe fail once it has reported.
FAIL_ENV = "AIO_SCHEMA_CACHE_PROBE_FAIL"
PROBE = (
    f"tests/schema_cache/{Path(__file__).name}"
    "::test_this_process_resolves_the_bus_from_the_shared_cache"
)
#: How many probes a session runs, spread over `WORKERS` workers.
PROBES = 4
WORKERS = 2
SESSION_SECONDS = 600
#: A proxy nothing listens on: a verb that left the cache for the network would fail here.
DEAD_PROXY = "http://127.0.0.1:1"


@dataclass(frozen=True)
class ProbeReport:
    """What one process of a probe session resolved the bus through, and what it saw."""

    #: The `xdist` worker it ran in, or `None` for a pytest a probe started.
    worker: str | None
    cache: str
    #: Whether the bus resolved the configuration's links with the network unreachable.
    resolved: bool
    output: str
    #: Every schema cache directory under the session's temporary directory at the time.
    present: list[str]

    @classmethod
    def read(cls, path: Path) -> ProbeReport:
        return cls(**json.loads(path.read_text(encoding="utf-8")))


class Session(NamedTuple):
    """A finished probe session, and the temporary directory it was given."""

    ran: subprocess.CompletedProcess[str]
    temporary: Path
    probe: Path


def _report(probe: Path, kind: str, probe_number: int) -> None:
    """Record what this process resolved the bus through, after proving it offline."""
    channel = Path(os.environ[onejudge_bundle.CACHE_DIR_ENV]).parent / f"channel-{os.getpid()}"
    resolved = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "onemessagebus"),
            "status",
            "--config",
            str(onejudge_bundle.CONFIG),
            "--transport-dir",
            str(channel),
        ],
        env={**os.environ, "HTTPS_PROXY": DEAD_PROXY, "https_proxy": DEAD_PROXY},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=REPO_ROOT,
    )
    temporary = Path(os.environ["TMPDIR"])
    report = ProbeReport(
        worker=os.environ.get("PYTEST_XDIST_WORKER"),
        cache=os.environ[onejudge_bundle.CACHE_DIR_ENV],
        resolved=resolved.returncode == 0,
        output=resolved.stdout + resolved.stderr,
        present=sorted(str(path) for path in temporary.glob(f"{onejudge_bundle.CACHE_PREFIX}*")),
    )
    (probe / f"{kind}-{probe_number}.json").write_text(
        json.dumps(report.__dict__), encoding="utf-8"
    )


@pytest.mark.parametrize("probe_number", range(PROBES))
def test_this_process_resolves_the_bus_from_the_shared_cache(probe_number: int) -> None:
    """This process was handed a warmed cache.

    Under `AIO_SCHEMA_CACHE_PROBE` it is also the probe the session journeys start: it
    reports, starts a pytest of its own that reports too, and fails if told to.
    """
    probe = os.environ.get(PROBE_ENV)
    if probe is None:
        cache = Path(os.environ[onejudge_bundle.CACHE_DIR_ENV])
        assert (cache.parent / onejudge_bundle.WARMED_MARKER).is_file(), cache
        return
    if os.environ.get(NESTED_ENV):
        _report(Path(probe), "nested", probe_number)
        return
    _report(Path(probe), "worker", probe_number)
    nested = subprocess.run(
        [sys.executable, "-m", "pytest", f"{PROBE}[{probe_number}]", "-p", "no:xdist"]
        + ["--no-cov", "-p", "no:cacheprovider"],
        env={**os.environ, NESTED_ENV: "1"},
        capture_output=True,
        text=True,
        timeout=SESSION_SECONDS,
        check=False,
        cwd=REPO_ROOT,
    )
    assert nested.returncode == 0, nested.stdout + nested.stderr
    assert not os.environ.get(FAIL_ENV), "told to fail"


def _environment(temporary: Path, **extra: str) -> dict[str, str]:
    """This process's environment with `temporary` as the temporary directory and no cache."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {onejudge_bundle.SUITE_CACHE_ENV, onejudge_bundle.CACHE_DIR_ENV}
    }
    environment.update({"TMPDIR": str(temporary), **extra})
    return environment


def _session(tmp_path: Path, *, failing: bool, inherited: Path | None = None) -> Session:
    """Run a probe session over `WORKERS` workers in a temporary directory of its own.

    `inherited` is a cache directory the session's environment names as already warmed.
    """
    temporary, probe = tmp_path / "tmp", tmp_path / "probe"
    temporary.mkdir()
    probe.mkdir()
    environment = _environment(temporary, **{PROBE_ENV: str(probe)})
    if failing:
        environment[FAIL_ENV] = "1"
    if inherited is not None:
        environment[onejudge_bundle.SUITE_CACHE_ENV] = str(inherited)
        environment[onejudge_bundle.CACHE_DIR_ENV] = str(inherited / "cache")
    ran = subprocess.run(
        [sys.executable, "-m", "pytest", PROBE, "-n", str(WORKERS)]
        + ["--no-cov", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=SESSION_SECONDS,
        check=False,
    )
    return Session(ran, temporary, probe)


def _assert_one_cache_shared_then_removed(session: Session) -> str:
    """Every process of `session` used one cache it created, now gone; return that cache."""
    output = session.ran.stdout + session.ran.stderr
    workers = [ProbeReport.read(path) for path in session.probe.glob("worker-*.json")]
    nested = [ProbeReport.read(path) for path in session.probe.glob("nested-*.json")]
    assert len(workers) == PROBES, output
    assert len(nested) == PROBES, output
    assert len({report.worker for report in workers}) == WORKERS, workers
    reports = workers + nested
    assert all(report.resolved for report in reports), [report.output for report in reports]
    caches = {report.cache for report in reports}
    assert len(caches) == 1, f"the session's processes used several caches: {caches}"
    cache = caches.pop()
    present = {path for report in reports for path in report.present}
    assert present == {str(Path(cache).parent)}, present
    left = sorted(session.temporary.glob(f"{onejudge_bundle.CACHE_PREFIX}*"))
    assert not left, f"the session left its schema cache behind: {left}"
    return cache


def test_a_passing_session_shares_one_cache_and_removes_it(tmp_path: Path) -> None:
    """Every worker and every pytest it starts reuses one cache, gone once the session ends.

    The session is started from an environment still naming a cache whose warming never
    finished — a directory with no warmed mark — which it warms a cache of its own beside
    rather than trusting, and leaves alone.
    """
    unwarmed = tmp_path / "unwarmed"
    unwarmed.mkdir()
    session = _session(tmp_path, failing=False, inherited=unwarmed)
    assert session.ran.returncode == 0, session.ran.stdout + session.ran.stderr
    cache = _assert_one_cache_shared_then_removed(session)
    assert not Path(cache).is_relative_to(unwarmed), cache
    assert unwarmed.is_dir() and not any(unwarmed.iterdir()), sorted(unwarmed.iterdir())


def test_a_failing_session_removes_its_cache_too(tmp_path: Path) -> None:
    """A session whose tests fail still removes the one cache it warmed."""
    session = _session(tmp_path, failing=True)
    assert session.ran.returncode == 1, session.ran.stdout + session.ran.stderr
    assert "told to fail" in session.ran.stdout, session.ran.stdout
    _assert_one_cache_shared_then_removed(session)


def _seeding_process(temporary: Path, body: str) -> subprocess.CompletedProcess[str]:
    """Run `body` in a fresh interpreter that has imported the suite's bundle module."""
    script = (
        "import os, sys\n"
        f"sys.path[:0] = [{str(REPO_ROOT / 'tests')!r}, {str(REPO_ROOT)!r}]\n"
        "import onejudge_bundle\n" + body
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=_environment(temporary),
        capture_output=True,
        text=True,
        timeout=SESSION_SECONDS,
        check=False,
    )


def test_a_warm_that_fails_removes_the_directory_it_made(tmp_path: Path) -> None:
    """A fetch the loopback origin cannot answer fails the seed, and its exit cleans up.

    The configuration is this host's with its frames link renamed to a document the
    origin does not hold, on the same host, so the warm reaches the origin and is refused.
    """
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    location, pin = onejudge_bundle.frames_link().rsplit("@", 1)
    missing = f"{location.rsplit('/', 1)[0]}/no-such-frames.json@{pin}"
    broken = onejudge_bundle.relinked_copy(tmp_path / "onemessagebus.yaml", missing)
    ran = _seeding_process(
        temporary,
        f"onejudge_bundle.seed_process_cache(__import__('pathlib').Path({str(broken)!r}))\n",
    )
    assert ran.returncode != 0, ran.stdout + ran.stderr
    assert "could not warm its schema cache" in ran.stderr, ran.stderr
    left = sorted(temporary.glob(f"{onejudge_bundle.CACHE_PREFIX}*"))
    assert not left, f"a failed warm left its directory behind: {left}"


def test_a_forked_child_exiting_leaves_its_parents_cache(tmp_path: Path) -> None:
    """A child forked from the warming process runs its exit handlers without removing
    the cache; the parent's own exit does."""
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    ran = _seeding_process(
        temporary,
        "cache = onejudge_bundle.seed_process_cache()\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    sys.exit(0)\n"
        "os.waitpid(child, 0)\n"
        "print(cache, (cache.parent / onejudge_bundle.WARMED_MARKER).is_file())\n",
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    cache, after_child = ran.stdout.split()
    assert after_child == "True", f"the forked child's exit removed {cache}"
    assert Path(cache).is_relative_to(temporary), cache
    assert not Path(cache).parent.exists(), f"the warming process left {cache} behind"
