"""A superseded branch is surfaced with its evidence and reclaimed with the command it prints.

The lifecycle retires a branch that provably holds no work beyond its base by itself, and
leaves a branch a landed retry replaced — one that still differs from its base — for the
manager to decide on. What a manager touches is this repository's recipes: `just
unpublished` lists the superseded branch with what superseded it, where that landed and
which paths differ, and offers `just reclaim-branch`; `just recoverable` renders onevcs's
`Reclaim:` line as that recipe; the recipe deletes the branch everywhere it is held and
refuses a branch still carrying unmerged work; and `just sweep` retires a lossless branch
the listing never showed. So these journeys drive those recipes as subprocesses and read
what came back, and the state they read is what the real `onevcs` recorded.

Everything is real — the recipes, the wrappers, `onevcs`, git and the sessions. Nothing is
doubled: the supersession is recorded through `onevcs supersede`, the verb the engine calls
when a retry lands, and the landing is a commit pushed to the base of a bare local origin.
What makes it safe is isolation: `ONEVCS_HOME` is a scratch registry over that throwaway
origin, `XDG_STATE_HOME` a scratch tree, `just sweep` takes its host-wide lock and writes
its completion stamp under a scratch `XDG_CACHE_HOME`, and its second verb runs under a
scratch `ONEAGENTGRAPH_STATE_DIR` and `TMPDIR`, so nothing this host holds is read, moved
or deleted.

The superseded branch is held in every place `onevcs reclaim` deletes from and one it must
not remove: it was cut on a **pool slot**, continued in a **run clone** under `runs/`,
reached the **registered checkout** when those sessions closed, and was put on the **bare
origin** by `onevcs preserve`.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from unpublished_registry import BASE, ORPHAN_BRANCH, Registry, commit, git, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT
from orchestrator.unpublished import COUNTED, NOTHING_COUNTED, SUPERSEDED_WITH_CHANGES

#: One worker, for the reason `test_unpublished_e2e.py` gives: every step reaches `onevcs`
#: through `uv run`, which waits on this checkout's exclusive environment lock.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The manager session the listing is read as; nothing here acknowledges anything.
MANAGER = "manager-session-superseded"

#: A workspaces file keeping one warm slot, so the first attempt is cut on a pool slot
#: that must survive the reclaim.
POOLED = "version: 1\ndefault:\n  pool: 1\n  overflow: unlimited\nrules: []\n"

#: The retry that replaced the first attempt, as the engine records it.
SUPERSEDING_BRANCH = "claude/second-try"
SUPERSEDING_NODE = "build-2"

#: What `onevcs reclaim` exits with when the class does not permit it: onevcs's
#: `docs/contract.md`, "A finished branch is retired once it provably holds no work".
RECLAIM_REFUSED = 4


class Superseded(NamedTuple):
    """The registry, the superseded branch, where it was held, and what replaced it."""

    registry: Registry
    state: Path
    #: The first attempt's session, which is the `--session` target that lists it.
    token: str
    branch: str
    #: The pool slot the first attempt was cut on.
    slot: Path
    #: The run root the continuation was cut under, clone and worktree together.
    run_root: Path
    #: The base commit the superseding retry landed as.
    landing: str
    #: A branch whose every changed path is identical on the base.
    lossless: str


def _open(registry: Registry, *arguments: str) -> dict[str, str]:
    """`onevcs session open` for real, answering its JSON report."""
    reported: dict[str, str] = json.loads(
        registry.onevcs("session", "open", str(registry.checkout), *arguments).strip()
    )
    return reported


def _land_on_base(registry: Registry, name: str, content: str) -> str:
    """Commit ``name`` on the base in the registered checkout, push it, answer the commit.

    Under a message of its own, so a base commit whose tree and parent match a branch's
    is still a different commit rather than the branch's own tip.
    """
    git("checkout", "-q", BASE, cwd=registry.checkout)
    (registry.checkout / name).write_text(content, encoding="utf-8")
    git("add", "-A", cwd=registry.checkout)
    git("commit", "-q", "-m", f"feat: land {name} on the base", cwd=registry.checkout)
    git("push", "-q", "origin", BASE, cwd=registry.checkout)
    return git("rev-parse", "HEAD", cwd=registry.checkout).strip()


@pytest.fixture(scope="module")
def seed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Superseded]:
    """One registry with a superseded branch, a lossless one and the seeded orphan."""
    root = tmp_path_factory.mktemp("superseded-e2e")
    registry = seeded(root)
    (registry.home / "workspaces.yml").write_text(POOLED, encoding="utf-8")

    first = _open(registry, "--label", "run=run-1", "--label", "node=build")
    slot = Path(first["worktree"]).parent
    commit(Path(first["worktree"]), "feature.txt", "first try\n")
    registry.onevcs("session", "close", first["token"])
    continued = _open(registry, "--branch", first["branch"], "--pool", "0")
    run_root = Path(continued["worktree"]).parent
    commit(Path(continued["worktree"]), "notes.txt", "what the first try also did\n")
    registry.onevcs("session", "close", continued["token"])
    registry.onevcs("preserve", "--repo", str(registry.checkout), first["branch"])

    lossless = _open(registry, "--pool", "0")
    commit(Path(lossless["worktree"]), "same.txt", "identical on the base\n")
    registry.onevcs("session", "close", lossless["token"])
    _land_on_base(registry, "same.txt", "identical on the base\n")

    landing = _land_on_base(registry, "feature.txt", "the retry's version\n")
    # llmlint: ignore-block[tests_mirror_real_usage] `onevcs supersede` is the published verb
    # the engine calls when a retry lands, and a real retry landing needs a driven run whose
    # dispatches this suite may not spend; the engine's half is held by the
    # `op-supersession-and-retirement-pass` row of
    # `tests/test_adopted_engine_carries_this_plan.py`, and this journey proves the half this
    # host owns — what the recipes do with the record once it exists.
    registry.onevcs(
        "supersede",
        first["branch"],
        "--repo",
        str(registry.checkout),
        "--by",
        SUPERSEDING_BRANCH,
        "--landing",
        landing,
        "--label",
        f"node={SUPERSEDING_NODE}",
    )
    # llmlint: ignore-end[tests_mirror_real_usage]
    yield Superseded(
        registry=registry,
        state=root / "state",
        token=first["token"],
        branch=first["branch"],
        slot=slot,
        run_root=run_root,
        landing=landing,
        lossless=lossless["branch"],
    )


def _recipe(seed: Superseded, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one `just` recipe from this checkout against the scratch state."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env={
            **seed.registry.environment,
            "XDG_STATE_HOME": str(seed.state),
            # `just sweep` holds the host sweep lock and stamps its completion under the
            # cache home: this journey's own, so it neither waits on a sweep this host is
            # running nor postpones the host's next one by an hour.
            "XDG_CACHE_HOME": str(seed.state / "cache"),
            "ONEPIPELINE_LAUNCHER": "claude-code",
            "ONEPIPELINE_LAUNCHER_SESSION": MANAGER,
            "ONEAGENTGRAPH_STATE_DIR": str(seed.state / "oneagentgraph"),
            "TMPDIR": str(seed.state.parent),
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )


# `Any` because a row is the view's own JSON, read back as parsed: its values are strings,
# booleans, nulls and nested objects by field, and the journeys index into them by the
# names `ROW_FIELDS` declares rather than through a second model of that shape.
def _rows(done: subprocess.CompletedProcess[str]) -> dict[str, dict[str, Any]]:
    """`just unpublished … --json`'s rows by branch."""
    parsed = json.loads(done.stdout)
    assert isinstance(parsed, list), f"--json did not emit an array: {done.stdout!r}"
    return {row["branch"]: row for row in parsed}


def _holds(repository: Path, branch: str) -> bool:
    """Whether the git repository at ``repository`` has a local ``branch``."""
    return bool(git("branch", "--list", branch, cwd=repository).strip())


def _on_origin(seed: Superseded, branch: str) -> bool:
    listed = git("ls-remote", "--heads", "origin", branch, cwd=seed.registry.checkout)
    return bool(listed.strip())


def _reclaim_line(listing: str) -> list[str]:
    """The argv of the `reclaim it:` line the listing printed."""
    lines = [line for line in listing.splitlines() if line.startswith("    reclaim it:")]
    assert len(lines) == 1, f"expected one `reclaim it:` line:\n{listing}"
    return shlex.split(lines[0].removeprefix("    reclaim it:"))


def test_a_superseded_branch_is_listed_with_its_evidence_and_reclaimed_everywhere(
    seed: Superseded,
) -> None:
    """Listed, counted, with its evidence and reclaim line; the printed command reclaims it."""
    checkout = str(seed.registry.checkout)
    as_json = _recipe(seed, "unpublished", "--session", seed.token, "--no-disk", "--json")
    listed = _recipe(seed, "unpublished", "--session", seed.token, "--no-disk")
    recoverable = _recipe(seed, "recoverable", "--repo", checkout)

    assert as_json.returncode == COUNTED, as_json.stderr
    row = _rows(as_json)[seed.branch]
    assert row["counted"] is True
    assert row["retirement"]["class"] == SUPERSEDED_WITH_CHANGES
    assert row["retirement"]["superseded_by"] == {
        "branch": SUPERSEDING_BRANCH,
        "landing": seed.landing,
        "labels": {"node": SUPERSEDING_NODE},
    }
    assert row["retirement"]["differing_paths"] == ["feature.txt", "notes.txt"]
    assert listed.returncode == COUNTED, listed.stderr
    assert (
        f"    superseded by:   {SUPERSEDING_BRANCH} (node {SUPERSEDING_NODE}), "
        f"landed at {seed.landing}\n"
        "    differs in:      feature.txt, notes.txt\n"
        f"    reclaim it:      just reclaim-branch {seed.branch} --repo {checkout}\n"
        f"    or acknowledge:  just unpublished --acknowledge {seed.branch} "
        '--reason "<why it is deliberately kept>"\n'
    ) in listed.stdout
    assert "land it:" not in listed.stdout
    assert recoverable.returncode == 0, recoverable.stderr
    assert f"Reclaim: just reclaim-branch {seed.branch} --repo {checkout}" in recoverable.stdout
    assert "onevcs reclaim" not in recoverable.stdout
    assert _holds(seed.registry.checkout, seed.branch)
    assert _holds(seed.run_root / "clone", seed.branch)
    assert _on_origin(seed, seed.branch)

    command = _reclaim_line(listed.stdout)
    assert command[:2] == ["just", "reclaim-branch"]
    reclaimed = _recipe(seed, *command[1:])

    assert reclaimed.returncode == 0, reclaimed.stdout + reclaimed.stderr
    assert not _holds(seed.registry.checkout, seed.branch)
    assert not (seed.run_root / "clone").exists() or not _holds(
        seed.run_root / "clone", seed.branch
    )
    assert not _on_origin(seed, seed.branch)
    assert (seed.slot / "slot.json").is_file(), "the pool slot was removed, not returned"
    pool = seed.registry.onevcs("pool", "status", checkout)
    assert "slot 1: idle" in pool, pool
    after = _recipe(seed, "unpublished", "--session", seed.token, "--no-disk", "--json")
    assert after.returncode == NOTHING_COUNTED, after.stdout + after.stderr
    assert seed.branch not in _rows(after)


def test_a_lossless_branch_is_never_listed_and_the_sweep_retires_it(seed: Superseded) -> None:
    """A branch whose every changed path is on the base: no row, then gone after a sweep."""
    before = _recipe(seed, "unpublished", "--host", "--no-disk", "--json")

    assert before.returncode in (COUNTED, NOTHING_COUNTED), before.stderr
    assert seed.lossless not in _rows(before)
    assert _holds(seed.registry.checkout, seed.lossless)

    swept = _recipe(seed, "sweep")

    assert swept.returncode == 0, swept.stdout + swept.stderr
    assert not _holds(seed.registry.checkout, seed.lossless), swept.stdout


def test_a_branch_holding_unmerged_work_keeps_its_landing_line_and_is_not_reclaimed(
    seed: Superseded,
) -> None:
    """Listed with today's `land it:` line, and refused by the reclaim recipe."""
    checkout = str(seed.registry.checkout)
    listed = _recipe(seed, "unpublished", "--host", "--no-disk")

    assert listed.returncode == COUNTED, listed.stderr
    assert (
        f"    land it:         just publish-branch {ORPHAN_BRANCH} --repo {checkout}"
    ) in listed.stdout

    refused = _recipe(seed, "reclaim-branch", ORPHAN_BRANCH, "--repo", checkout)

    assert refused.returncode == RECLAIM_REFUSED, refused.stdout + refused.stderr
    assert "unmerged-unique-commits" in refused.stdout + refused.stderr
    assert _holds(seed.registry.checkout, ORPHAN_BRANCH)
