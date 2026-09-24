"""Drive two concurrent pytest sessions through fixture-root creation and removal."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import plan_fixture_isolation_peer as peer
import plan_fixture_source

from orchestrator.project_store import TASKS_DIRECTORY
from orchestrator.root import REPO_ROOT

#: The peer as pytest is given it. Named by path rather than collected: its filename does
#: not match `python_files`, which is what keeps an ordinary run of this suite from
#: collecting a half-rendezvous that would wait for a peer nobody started.
PEER = "tests/project_store_race/plan_fixture_isolation_peer.py"

#: What either peer session may take end to end. Each pays for a pytest session start and
#: then waits on the other, so this only ever catches a peer that died.
PEER_SECONDS = 600


def _started(scratch: Path, role: peer.Role) -> subprocess.Popen[str]:
    """One peer pytest session, in this process's own environment plus its role.

    The environment is inherited rather than scrubbed, this process's own fixture root
    included: that a peer states its own root *over* an inherited one is the isolation
    under test, and a scrubbed environment would prove it against a case no dispatch ever
    produces — every one of them runs this suite with a plan-store environment already set.
    """
    return subprocess.Popen(  # noqa: S603 - this suite's own peer, run as pytest runs it
        [
            sys.executable,
            "-m",
            "pytest",
            PEER,
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            peer.SCRATCH_ENV: str(scratch),
            peer.ROLE_ENV: role,
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def test_two_concurrent_test_processes_keep_their_own_fixture_roots(tmp_path: Path) -> None:
    """Distinct roots, and a removal in one that the other never sees.

    The keeper reads its own store for the whole of the removal and once after it, and
    those reads are its own assertions — so a shared root fails in the peer that met the
    half-removed record, and the roots below say which failure it was.
    """
    scratch = tmp_path / "rendezvous"
    scratch.mkdir()
    peers = {role: _started(scratch, role) for role in (peer.Role.KEEPER, peer.Role.REMOVER)}
    # Drained remover-first, because the keeper only ends once the remover has removed:
    # waiting on the keeper first would hold a full pipe against the peer it is waiting for.
    reported = {
        role: peers[role].communicate(timeout=PEER_SECONDS)[0]
        for role in (peer.Role.REMOVER, peer.Role.KEEPER)
    }
    for role, process in peers.items():
        assert process.returncode == 0, f"the {role} peer failed:\n{reported[role]}"

    roots = {
        role: Path((scratch / f"{role}.root").read_text(encoding="utf-8"))
        for role in (peer.Role.KEEPER, peer.Role.REMOVER)
    }
    assert roots[peer.Role.KEEPER] != roots[peer.Role.REMOVER], (
        f"both test processes resolved {roots[peer.Role.KEEPER]} for the "
        f"{plan_fixture_source.SOURCE!r} source, so one's removal is the other's missing "
        f"record — which is the sharing this isolation ended"
    )
    for role, root in roots.items():
        assert root != plan_fixture_source.tracked_root(), (
            f"the {role} peer used the root onetaskgraph.yaml states rather than its own"
        )

    kept = roots[peer.Role.KEEPER] / "projects" / f"{peer.SHARED_NATIVE}.md"
    gone = roots[peer.Role.REMOVER] / "projects" / f"{peer.SHARED_NATIVE}.md"
    assert kept.is_file(), f"the keeper's own record is gone from {kept}"
    assert not gone.exists(), f"the remover never removed {gone}"
    assert (roots[peer.Role.KEEPER] / TASKS_DIRECTORY / peer.SHARED_NATIVE).is_dir()
    assert not (roots[peer.Role.REMOVER] / TASKS_DIRECTORY / peer.SHARED_NATIVE).exists()
