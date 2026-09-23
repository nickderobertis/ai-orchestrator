"""One half of the two-process proof that no two test processes share a fixture root.

Run by `tests/test_plan_fixture_isolation.py` and by nothing else: the isolation under
test is established by a *session fixture* of `tests/conftest.py`, so the only honest
peer is a real pytest process collecting under that conftest. Its filename does not match
`python_files`, so an ordinary run of this suite collects nothing here; pytest collects a
file named on the command line whatever its name, which is how the driver reaches it.

The two roles are one script because the halves have to agree on the rendezvous, and a
rendezvous split across two files is one edit away from two processes waiting on
different names. Each half writes the root it resolved, both write a record under the
*same* native id, and then the `remover` removes its own while the `keeper` reads its own
through the installed store CLI for as long as that removal is in flight. Distinct roots
are what make every one of the keeper's reads answer; one shared root is the failure this
is here to catch, and under it the keeper's read is the read that used to be refused.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import plan_fixture_source
from published_tools import ONETASKGRAPH_BIN

from orchestrator.project_store import TASKS_DIRECTORY, write_plan_project
from orchestrator.root import REPO_ROOT

#: The shared scratch directory the two peers rendezvous through, and which role this
#: process plays. Both are handed over by the driver; neither has a default, because a
#: peer that guessed one would rendezvous with nobody and pass.
SCRATCH_ENV = "PLAN_FIXTURE_ISOLATION_SCRATCH"
ROLE_ENV = "PLAN_FIXTURE_ISOLATION_ROLE"

KEEPER = "keeper"
REMOVER = "remover"

#: The native id both peers write, deliberately the same in both roots: what proves the
#: roots are distinct is that removing this id in one leaves it readable in the other, and
#: two different ids would prove that of two unrelated records instead.
SHARED_NATIVE = "test-shared-native-id"

#: The plan each peer stores: one record, complete enough for `write_plan_project`.
PLAN: dict[str, object] = {
    "name": SHARED_NATIVE,
    "tasks": [{"id": "only", "task": "Be readable."}],
}

#: How long either peer waits for the other to reach the next point of the rendezvous.
#: Generous, because both are paying for a pytest session start; an expiry here is a peer
#: that failed or never started, never a slow one.
RENDEZVOUS_SECONDS = 180.0
#: How often a wait looks again, and the bound on one store read.
POLL_SECONDS = 0.1
READ_SECONDS = 60.0


def _scratch() -> Path:
    return Path(os.environ[SCRATCH_ENV])


def _announce(name: str) -> None:
    (_scratch() / name).touch()


def _await(name: str) -> None:
    """Wait for the other half to reach ``name``, or fail saying what never arrived."""
    marker = _scratch() / name
    deadline = time.monotonic() + RENDEZVOUS_SECONDS
    while not marker.exists():
        if time.monotonic() > deadline:
            raise AssertionError(
                f"{marker} never appeared within {RENDEZVOUS_SECONDS:.0f}s; the peer "
                f"process that writes it failed or never started"
            )
        time.sleep(POLL_SECONDS)


def _stored_project() -> object:
    """The shared project read out of *this* process's store, through the installed CLI.

    The real binary over this process's own resolved configuration, because what is being
    proven is that the store answers — a `Path.exists()` here would pass over exactly the
    refused walk this isolation exists to prevent.
    """
    read = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            "project",
            "show",
            f"{plan_fixture_source.SOURCE}:{SHARED_NATIVE}",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=READ_SECONDS,
        check=False,
    )
    assert read.returncode == 0, (
        f"this process's own {plan_fixture_source.SOURCE!r} store stopped answering for "
        f"{SHARED_NATIVE} while the peer was removing its copy:\n{read.stdout}{read.stderr}"
    )
    payload = json.loads(read.stdout)
    assert isinstance(payload, dict) and payload.get("items"), read.stdout
    return payload["items"][0]


def _remove(root: Path) -> None:
    """Remove the shared record from this process's own root, record and tasks alike."""
    (root / "projects" / f"{SHARED_NATIVE}.md").unlink()
    tasks = root / TASKS_DIRECTORY / SHARED_NATIVE
    for child in tasks.iterdir():
        child.unlink()
    tasks.rmdir()


def test_a_peer_writes_a_record_and_plays_its_half_of_the_removal() -> None:
    """Report this process's fixture root, then keep or remove the record under it.

    Every assertion is this peer's own: the driver reads the exit status and the reported
    roots, so a peer that reached a wrong conclusion fails here rather than reporting a
    green the driver would have to second-guess.
    """
    role = os.environ[ROLE_ENV]
    assert role in (KEEPER, REMOVER), role
    root = plan_fixture_source.root()
    assert root != plan_fixture_source.tracked_root(), (
        f"this peer resolved the tracked root {root}, so its session fixture stated none"
    )
    (_scratch() / f"{role}.root").write_text(str(root), encoding="utf-8")

    write_plan_project(root, PLAN, native_id=SHARED_NATIVE)
    assert _stored_project(), "the record this peer just wrote is not in its own store"
    _announce(f"{role}.created")
    _await(f"{REMOVER if role == KEEPER else KEEPER}.created")

    if role == REMOVER:
        _remove(root)
        _announce("removed")
        return

    # The keeper reads its own store for the whole of the removal and once after it, so
    # the window a shared root would refuse a read in is covered rather than stepped over.
    removed = _scratch() / "removed"
    deadline = time.monotonic() + RENDEZVOUS_SECONDS
    while not removed.exists():
        assert time.monotonic() <= deadline, f"{removed} never appeared; the remover failed"
        _stored_project()
    _stored_project()
