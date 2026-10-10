"""A branch a running publication holds is in flight, through the real recipe and stop verdict.

`publish-branch` and `recover` publish a branch no session holds, so before onevcs 0.40.0
a branch whose publication was running at that moment read as idle preserved work: `just
unpublished` counted it, and the `Stop` hook's verdict source refused a manager's turn
over a publication that was doing exactly what it should. onevcs now reports that branch
`held_by` with `holding: "publication-running"` and a `null` token — the one holding that
names no session — and this view reads any `held_by` as in flight.

So these journeys drive what a manager and the hook both touch — `just unpublished` and
`scripts/unpublished.sh --stop-verdict` — over a real registry, a real closed session's
preserved branch and the installed `onevcs`, and hold the real publication lease that
`onevcs`'s `publishing::Lease::take` holds while it runs. Nothing is doubled: the
`publication-running` row is the installed CLI's own answer, and the journey reads it
back from `onevcs recoverable --json` before believing the view, so a lease this fixture
spells wrongly fails as that rather than as a view that ignored it.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from nx_workspace import WORKSPACE_INSTALL_MARKS
from unpublished_registry import Registry, Session, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT
from orchestrator.unpublished import COUNTED, NOTHING_COUNTED

#: A reader of the toolchain this checkout provisioned: every step here runs its tool
#: through a `just` recipe or `uv run`, and no writer touches that toolchain, so
#: `tests/e2e/nx_workspace.py` asks no xdist group of it.
pytestmark = list(WORKSPACE_INSTALL_MARKS)

#: The manager session whose run left the branch, and the one the stop verdict is asked for.
MANAGER = "manager-session-held-publication"

#: The warning the view gives a `held_by` it could not read whole.
UNREADABLE_HOLDER = "its holder could not be read whole"


def _digest(value: str) -> str:
    """`onevcs`'s `ids::digest`: lower-case hex SHA-256 of the string."""
    return hashlib.sha256(value.encode()).hexdigest()


# llmlint: ignore-block[tests_mirror_real_usage] A running publication is a `publish-branch`
# mid-flight, and holding one open means running a real merge path until the test reads it,
# which no interface can pause. This takes the lease that publication takes — onevcs
# 0.40.2's `publishing::Lease::take`: a shared lock on `publication:<run root>` under
# `<ONEVCS_HOME>/locks`, and its record under `publishing/<digest(identity\nbranch)>` — and
# the journey reads the installed CLI's own `held_by` back before reading the view.
@contextmanager
def _running_publication(registry: Registry, branch: str, run_root: Path) -> Iterator[Path]:
    """Hold a real publication lease on ``branch`` until the block ends; yield its workspace."""
    worktree = run_root / "worktree"
    worktree.mkdir(parents=True)
    lease = f"publication:{run_root}"
    locks = registry.home / "locks"
    locks.mkdir(exist_ok=True)
    record_dir = registry.home / "publishing" / _digest(f"{registry.identity}\n{branch}")
    record_dir.mkdir(parents=True)
    record = record_dir / f"{_digest(lease)}.json"
    with (locks / f"{_digest(lease)}.lock").open("a+b") as held:
        fcntl.flock(held, fcntl.LOCK_SH)
        record.write_text(
            json.dumps(
                {
                    "identity": registry.identity,
                    "branch": branch,
                    "run_root": str(run_root),
                    "worktree": str(worktree),
                }
            ),
            encoding="utf-8",
        )
        try:
            yield worktree
        finally:
            record.unlink()
            fcntl.flock(held, fcntl.LOCK_UN)


# llmlint: ignore-end[tests_mirror_real_usage]


@pytest.fixture
def left(tmp_path: Path) -> tuple[Registry, Session]:
    """A registry holding one preserved branch the manager's run left: closed, labelled."""
    registry = seeded(tmp_path)
    session = registry.open_session(
        labels={"run": "run-held-publication", "node": "build", "launcher": MANAGER}
    )
    registry.close_session(session)
    return registry, session


def _environment(registry: Registry, state: Path) -> dict[str, str]:
    return {
        **registry.environment,
        "XDG_STATE_HOME": str(state),
        "ONEPIPELINE_LAUNCHER": "claude-code",
        "ONEPIPELINE_LAUNCHER_SESSION": MANAGER,
    }


def _unpublished(
    registry: Registry, state: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """The real `just unpublished`, as a manager runs it, against the scratch registry."""
    return subprocess.run(
        ["just", "unpublished", *arguments],
        cwd=REPO_ROOT,
        env=_environment(registry, state),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )


def _stop_verdict(registry: Registry, state: Path) -> dict[str, str]:
    """The `Stop` hook's verdict source, run as the hook runs it, for the manager's session."""
    answered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "unpublished.sh"), "--stop-verdict"],
        input=json.dumps({"session": MANAGER}),
        cwd=REPO_ROOT,
        env=_environment(registry, state),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )
    assert answered.returncode == 0, answered
    [line] = answered.stdout.splitlines()
    verdict: dict[str, str] = json.loads(line)
    assert UNREADABLE_HOLDER not in answered.stderr, answered.stderr
    return verdict


# `Any` because a row is the view's own JSON, read back as parsed.
def _row(done: subprocess.CompletedProcess[str], branch: str) -> dict[str, Any]:
    rows = {row["branch"]: row for row in json.loads(done.stdout)}
    assert branch in rows, done.stdout
    row: dict[str, Any] = rows[branch]
    return row


def test_a_branch_a_running_publication_holds_is_in_flight_and_owes_nothing(
    left: tuple[Registry, Session], tmp_path: Path
) -> None:
    registry, session = left
    state = tmp_path / "state"

    idle = _unpublished(registry, state, "--json", "--no-disk")
    assert idle.returncode == COUNTED, idle.stderr
    assert _row(idle, session.branch)["in_flight"] is False
    assert _stop_verdict(registry, state)["verdict"] == "block"

    with _running_publication(registry, session.branch, tmp_path / "publication-run") as work:
        reported = json.loads(registry.onevcs("recoverable", "--repo", registry.identity, "--json"))
        [record] = [row for row in reported if row["branch"]["branch"] == session.branch]
        assert record["held_by"] == {
            "holding": "publication-running",
            "token": None,
            "worktree": str(work),
        }, record

        held = _unpublished(registry, state, "--json", "--no-disk")
        assert held.returncode == NOTHING_COUNTED, held.stderr
        row = _row(held, session.branch)
        assert (row["in_flight"], row["counted"]) == (True, False), row
        assert UNREADABLE_HOLDER not in held.stderr, held.stderr

        listed = _unpublished(registry, state, "--no-disk")
        assert listed.returncode == NOTHING_COUNTED, listed.stderr
        [line] = [line for line in listed.stdout.splitlines() if session.branch in line]
        assert line.rstrip().endswith("in flight"), listed.stdout
        assert UNREADABLE_HOLDER not in listed.stderr, listed.stderr

        assert _stop_verdict(registry, state) == {"verdict": "none"}

    finished = _unpublished(registry, state, "--json", "--no-disk")
    assert finished.returncode == COUNTED, finished.stderr
    assert _row(finished, session.branch)["in_flight"] is False
    assert _stop_verdict(registry, state)["verdict"] == "block"
