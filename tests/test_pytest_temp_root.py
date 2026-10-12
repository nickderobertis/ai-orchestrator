"""Every session this suite's targets start keeps its temporary files where nobody prunes them.

`tests/pytest_temp_root.py` is the rule. These drive it the way the gate does: real
sessions, started as a target starts one — this checkout's conftest and configuration,
`xdist` workers and `--dist loadgroup` — two at once, with the host's shared per-user root
pruned out from under them while their tests run.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest_temp_root

from orchestrator.root import REPO_ROOT

#: Where a probe session reports to and waits on; unset in an ordinary run of this module.
PROBE_ENV = "AIO_TEMP_ROOT_PROBE"
PROBE = f"tests/{Path(__file__).name}::test_this_sessions_temporary_files_survive_its_neighbours"
#: How long either side waits for the other before calling the journey failed.
HANDSHAKE_SECONDS = 180


def _await(path: Path, what: str) -> None:
    deadline = time.monotonic() + HANDSHAKE_SECONDS
    while not path.exists():
        assert time.monotonic() < deadline, f"no {what} at {path} within {HANDSHAKE_SECONDS}s"
        time.sleep(0.2)


def test_this_sessions_temporary_files_survive_its_neighbours(tmp_path: Path) -> None:
    """This session's files are under a root of its own, not the host's shared one.

    Under `AIO_TEMP_ROOT_PROBE` it is also the probe the journey below starts: it says it
    is running, waits while the shared root is pruned, and only then writes and reports.
    """
    probe = os.environ.get(PROBE_ENV)
    if probe is not None:
        mark = Path(probe) / f"session-{os.getppid()}-{os.getpid()}"
        mark.with_suffix(".ready").write_text(
            json.dumps(
                {"tmp_path": str(tmp_path), "root": os.environ[pytest_temp_root.TEMPROOT_ENV]}
            ),
            encoding="utf-8",
        )
        _await(Path(probe) / "pruned", "pruning of the shared root")
    written = tmp_path / "written"
    written.write_text("still here", encoding="utf-8")
    assert written.read_text(encoding="utf-8") == "still here"
    assert not tmp_path.is_relative_to(pytest_temp_root.shared_root()), tmp_path
    own_root = Path(os.environ[pytest_temp_root.TEMPROOT_ENV])
    assert tmp_path.is_relative_to(own_root), (tmp_path, own_root)
    if probe is not None:
        mark.with_suffix(".json").write_text(
            json.dumps({"tmp_path": str(tmp_path), "root": str(own_root)}), encoding="utf-8"
        )


def test_two_target_shaped_sessions_survive_the_shared_root_being_pruned(tmp_path: Path) -> None:
    """Two sessions at once keep apart through a pruning of the shared root, then clean up.

    Each child is given a `TMPDIR` of this test's own, so the shared root it would default
    to is this test's rather than the host's. A third, ordinary pytest session
    retains only its newest three numbered bases while both children are waiting.
    """
    host_tmp = tmp_path / "host-tmp"
    probe = tmp_path / "probe"
    host_tmp.mkdir()
    probe.mkdir()
    shared = host_tmp / f"pytest-of-{getpass.getuser()}"
    shared.mkdir()
    for number in range(5):
        (shared / f"pytest-{number}").mkdir()
    neighbour = tmp_path / "neighbour"
    neighbour.mkdir()
    (neighbour / "test_default_root.py").write_text(
        "def test_default_root(tmp_path, pytestconfig):\n"
        "    assert int(pytestconfig.getini('tmp_path_retention_count')) == 3\n"
        "    assert pytestconfig.getini('tmp_path_retention_policy') == 'all'\n"
        "    (tmp_path / 'written').write_text('default root')\n",
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items() if key != pytest_temp_root.TEMPROOT_ENV
    }
    environment.update({"TMPDIR": str(host_tmp), PROBE_ENV: str(probe)})
    command = [
        sys.executable,
        "-m",
        "pytest",
        PROBE,
        "-n",
        "2",
        "--dist",
        "loadgroup",
        "--no-cov",
        "-p",
        "no:cacheprovider",
    ]
    sessions = [
        subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        deadline = time.monotonic() + HANDSHAKE_SECONDS
        while len(list(probe.glob("*.ready"))) < len(sessions):
            finished = [session for session in sessions if session.poll() is not None]
            assert not finished, finished[0].communicate()[0]
            assert time.monotonic() < deadline, "the probe sessions never started their tests"
            time.sleep(0.2)
        live = [json.loads(path.read_text(encoding="utf-8")) for path in probe.glob("*.ready")]
        live_roots = {Path(report["root"]) for report in live}
        assert len(live_roots) == len(sessions), live
        assert all(Path(report["tmp_path"]).is_dir() for report in live), live
        pruned = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "test_default_root.py",
                "--noconftest",
                "--no-cov",
                "-p",
                "no:xdist",
                "-p",
                "no:cacheprovider",
            ],
            cwd=neighbour,
            env={key: value for key, value in environment.items() if key != PROBE_ENV},
            capture_output=True,
            text=True,
            check=False,
        )
        assert pruned.returncode == 0, pruned.stdout + pruned.stderr
        retained = {path.name for path in shared.glob("pytest-*") if not path.is_symlink()}
        assert retained == {"pytest-3", "pytest-4", "pytest-5"}, retained
        assert all(session.poll() is None for session in sessions), "a probe ended before pruning"
        assert all(Path(report["tmp_path"]).is_dir() for report in live), live
        assert all(root.is_dir() for root in live_roots), live_roots
        (probe / "pruned").write_text("", encoding="utf-8")
        outputs = [session.communicate(timeout=HANDSHAKE_SECONDS)[0] for session in sessions]
    finally:
        for session in sessions:
            if session.poll() is None:
                session.kill()
                session.wait()
    for session, output in zip(sessions, outputs, strict=True):
        assert session.returncode == 0, output

    reports = [json.loads(path.read_text(encoding="utf-8")) for path in probe.glob("*.json")]
    assert len(reports) == len(sessions), reports
    roots = {Path(report["root"]) for report in reports}
    assert len(roots) == len(sessions), f"two sessions shared one root: {roots}"
    for report in reports:
        assert Path(report["tmp_path"]).is_relative_to(host_tmp), report
        assert not Path(report["tmp_path"]).is_relative_to(shared), report
    assert not [root for root in roots if root.exists()], "a session left its root behind"


def test_a_pytest_started_without_this_conftest_numbers_under_this_sessions_root(
    tmp_path: Path,
) -> None:
    """A nested pytest that never loads this suite's conftest still stays out of the shared root."""
    nested = tmp_path / "nested"
    nested.mkdir()
    report = tmp_path / "nested-tmp-path"
    (nested / "test_nested.py").write_text(
        "import os, pathlib\n"
        "def test_nested(tmp_path):\n"
        "    pathlib.Path(os.environ['NESTED_REPORT']).write_text(str(tmp_path))\n",
        encoding="utf-8",
    )
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_nested.py",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
        ],
        cwd=nested,
        env={**os.environ, "NESTED_REPORT": str(report)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    nested_tmp = Path(report.read_text(encoding="utf-8"))
    assert not nested_tmp.is_relative_to(pytest_temp_root.shared_root()), nested_tmp
    assert nested_tmp.is_relative_to(Path(os.environ[pytest_temp_root.TEMPROOT_ENV])), nested_tmp


def test_a_session_given_a_basetemp_keeps_it_after_it_ends(tmp_path: Path) -> None:
    """An explicit `--basetemp` is used as named, and the session's release leaves it standing."""
    host_tmp = tmp_path / "host-tmp"
    root = tmp_path / "named-root"
    base = root / "base"
    host_tmp.mkdir()
    root.mkdir()
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            PROBE,
            f"--basetemp={base}",
            "-p",
            "no:xdist",
            "--no-cov",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "TMPDIR": str(host_tmp), pytest_temp_root.TEMPROOT_ENV: str(root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert [path.name for path in base.iterdir()], f"nothing was written under {base}"
    minted = [
        path
        for parent in (root, host_tmp)
        for path in parent.glob(f"{pytest_temp_root.ROOT_PREFIX}*")
    ]
    assert not minted, f"a session given --basetemp minted a root of its own: {minted}"
