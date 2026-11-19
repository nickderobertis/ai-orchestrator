"""The planning run's `finalize` guard, run exactly as the planning template renders it.

The engine runs a node's `onepipeline.guard` before dispatching it: exit 3 settles the node
`done (not-needed)` with no dispatch, exit 0 dispatches it, and any other exit fails it
`guard-failed` (onepipeline's `docs/contract.md`, Guards). So each verdict here is read off
the guard's own exit, the template rendered by the locked plan store into the isolated
`authoring` root and the guard run from this checkout, as a launch runs it, against that
store: exit 3 for a plan with no spikes project, 0 for one whose spikes project holds a
task, and neither for a lookup the store refuses.

Two failures the locked store cannot be made to produce — an answer that is not a whole
listing, and a store failing with the very status that means "not needed" — are reached by
running the same rendered guard from a scratch directory whose `.venv/bin/onetaskgraph` is a
stand-in answering exactly that, since the guard names the store by that relative path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

STORE = REPO_ROOT / ".venv" / "bin" / "onetaskgraph"
TEMPLATE = REPO_ROOT / "templates" / "planning" / "planning.md.j2"


def _store(*args: str) -> dict[str, object]:
    done = subprocess.run(
        [str(STORE), *args, "--json", "--no-interactive"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    answer = json.loads(done.stdout)
    assert isinstance(answer, dict), answer
    return answer


def _rendered_guard(plan: str, native: str) -> list[str]:
    """The `onepipeline.guard` the planning template renders onto `finalize` for `plan`."""
    project = f"{native}-planning"
    _store(
        "project",
        "create",
        "authoring",
        "--id",
        project,
        "--title",
        f"{native} planning",
        "--template",
        str(TEMPLATE),
        "--var",
        f"plan={plan}",
        "--var",
        f"native={native}",
        "--var",
        f"plan_title={native}",
        "--var",
        "brief=brief.md",
        "--var",
        "brief_text=# Brief",
        "--var",
        "checklist=none",
    )
    listed = _store("task", "list", "--source", "authoring", "--project", f"authoring:{project}")
    items = listed["items"]
    assert isinstance(items, list), listed
    (finalize,) = [
        one["item"]["metadata"]
        for one in items
        if one["item"]["metadata"].get("onepipeline.id") == "finalize"
    ]
    guard = finalize["onepipeline.guard"]
    assert isinstance(guard, list) and all(isinstance(part, str) for part in guard), guard
    assert "onepipeline.guard_timeout_seconds" not in finalize, finalize
    return guard


def _run(guard: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        guard, cwd=REPO_ROOT, env=os.environ, capture_output=True, text=True, check=False
    )


@pytest.fixture
def native(request: pytest.FixtureRequest) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"guard-{os.getpid()}-{request.node.name}".lower()).strip("-")


def test_a_plan_with_no_spikes_project_needs_no_finalize(native: str) -> None:
    plan = f"authoring:{native}"
    ran = _run(_rendered_guard(plan, native))
    assert ran.returncode == 3, ran.stdout + ran.stderr
    assert ran.stderr.strip().splitlines() == [
        f"{plan} has no spikes project, so there is nothing to finalize"
    ], ran.stderr


def test_a_plan_whose_spikes_project_holds_a_task_is_finalized(native: str, tmp_path: Path) -> None:
    plan = f"authoring:{native}"
    guard = _rendered_guard(plan, native)
    _store("project", "create", "authoring", "--id", f"{native}-spikes", "--title", "spikes")
    body = tmp_path / "spike.md"
    body.write_text("Measure it.\n", encoding="utf-8")
    _store(
        "task",
        "create",
        "authoring",
        "--project",
        f"{plan}-spikes",
        "--title",
        "Measure the health route",
        "--body-file",
        str(body),
    )
    ran = _run(guard)
    assert ran.returncode == 0, ran.stdout + ran.stderr


class StandIn(NamedTuple):
    """One answer the locked store cannot be made to give, and what the guard says of it."""

    #: The stand-in store's bash body.
    script: str
    #: A phrase the guard's stderr carries for it.
    said: str


STAND_INS = {
    # A listing the store marks incomplete, which must not count as an empty spikes project.
    "an-incomplete-listing": StandIn(
        """printf '%s\\n' '{"items": [], "next": null, "errors": [{"source": "authoring"}]}'""",
        "could not be counted",
    ),
    # A listing with no items array, which `length` would otherwise read as zero.
    "no-items-array": StandIn(
        """printf '%s\\n' '{"next": null, "errors": []}'""", "could not be counted"
    ),
    # A store failing with 3 itself: the guard maps it to its own failure, never passes it on.
    "a-store-exiting-3": StandIn("echo 'store broke' >&2; exit 3", "could not list"),
}


@pytest.mark.parametrize("case", sorted(STAND_INS))
def test_a_listing_that_cannot_be_counted_fails_rather_than_skips(
    case: str, native: str, tmp_path: Path
) -> None:
    stand_in = STAND_INS[case]
    guard = _rendered_guard(f"authoring:{native}", native)
    # llmlint: ignore-block[e2e_not_mocked, tests_mirror_real_usage] The layer under test is the
    # rendered guard, run unmodified; only the published plan-store CLI it delegates to is doubled,
    # at that boundary, which AGENTS.md's realistic-tests invariant permits, because the locked
    # store cannot be made to answer an incomplete listing, one with no items array, or a failure
    # exiting 3 — the three answers the guard must refuse to read as "not needed". Every answer the
    # real store can give is driven against it above.
    store = tmp_path / ".venv" / "bin" / "onetaskgraph"
    store.parent.mkdir(parents=True)
    store.write_text(f"#!/usr/bin/env bash\n{stand_in.script}\n", encoding="utf-8")
    store.chmod(0o755)
    ran = subprocess.run(
        guard, cwd=tmp_path, env=os.environ, capture_output=True, text=True, check=False
    )
    # llmlint: ignore-end[e2e_not_mocked, tests_mirror_real_usage]
    assert ran.returncode not in (0, 3), (ran.returncode, ran.stderr)
    assert stand_in.said in ran.stderr, ran.stderr


def test_a_lookup_the_store_refuses_fails_rather_than_skips(native: str) -> None:
    ran = _run(_rendered_guard(f"no-such-source:{native}", native))
    assert ran.returncode not in (0, 3), (ran.returncode, ran.stderr)
    assert "could not list" in ran.stderr, ran.stderr
