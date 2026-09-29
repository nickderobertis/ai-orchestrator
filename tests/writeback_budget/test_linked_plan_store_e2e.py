"""The adopted engine reads and projects the plan store it links, and spawns none.

Since onepipeline 0.50.0 the engine reads a run's plan and projects its settlements through
the onetaskgraph crates it links rather than through a spawned `onetaskgraph`, so
`ONETASKGRAPH_BIN` reaches nothing. These journeys hold that on this host's own launch
path, against a local Markdown destination read back off disk:

* with `ONETASKGRAPH_BIN` naming a program that fails whenever it runs, the run still reads
  its plan and projects a settlement onto the destination, and the program never runs;
* a destination the store cannot read ends the projection and writes nothing: the fault
  is a real one on the real store — the carried task's own record made unreadable — so the
  attempt it fails is recorded `failed` / `refused`, and every record the destination
  keeps stays byte for byte what it was.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import plan_fixture_source
import pytest
from driven_run import (
    PATIENCE_SECONDS,
    DrivenRun,
    NodeId,
    Projection,
    apply,
    launched,
    projections,
    quiet_projections,
    waited_for,
)
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.project_store import PROJECTS_DIRECTORY, TASKS_DIRECTORY
from orchestrator.root import REPO_ROOT

#: The session these launches run under, stated rather than inherited: this suite runs
#: inside a dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-writeback-linked-plan-store"

#: The node whose dispatch keeps the run driven, and the node settled behind it.
HELD_NODE = NodeId("work")
SETTLED_NODE = NodeId("later")

#: `failed` rather than `done`, so no dependent's state moves with it.
SETTLED_OUTCOME = "failed"

#: `tests/e2e/nx_workspace.py`'s group: every step here is a `just` recipe blocking on
#: `uv run`, which waits on the lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)


class PoisonedStore(NamedTuple):
    """A program that fails whenever it runs, and the file it writes when it does."""

    program: Path
    ran: Path


@pytest.fixture
def poisoned_store(tmp_path: Path) -> PoisonedStore:
    poisoned = PoisonedStore(
        program=tmp_path / "poisoned-onetaskgraph", ran=tmp_path / "poisoned-onetaskgraph.ran"
    )
    poisoned.program.write_text(
        f'#!/bin/sh\necho "$*" >> {shlex.quote(str(poisoned.ran))}\nexit 97\n', "utf-8"
    )
    poisoned.program.chmod(0o755)
    return poisoned


@pytest.fixture
def driven(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    oneharness_bin: str,
    poisoned_store: PoisonedStore,
) -> Iterator[DrivenRun]:
    # llmlint: ignore-block[e2e_not_mocked] Only the paid model provider is faked, at the
    # `oneharness` seam, which this repository's realistic-tests invariant permits doubling.
    # The recipe, the engine, its driver and write-back worker, the plan store it links and
    # the store on disk are all real; the program `ONETASKGRAPH_BIN` names is what the
    # journey asserts nothing reaches.
    with launched(
        tmp_path,
        request,
        oneharness_bin,
        caller=__name__,
        session=LAUNCHING_SESSION,
        prefix="linked-store",
        goal="keep a run driven while its settlements are projected",
        held_node=HELD_NODE,
        waiting_nodes=(SETTLED_NODE,),
        extra_environment={"ONETASKGRAPH_BIN": str(poisoned_store.program)},
    ) as run:
        yield run
    # llmlint: ignore-end[e2e_not_mocked]


def _listed(driven: DrivenRun) -> dict[str, dict[str, Any]]:
    """Every task of the launched project by node id, as the real store CLI lists it.

    `Any` because this is the store's JSON as it printed it, indexed by the journeys along
    the paths its `task list --json` documents; a key that moved fails as a `KeyError`
    naming it rather than as a type this module would have to restate.
    """
    source, native = driven.project.split(":", 1)
    listed = subprocess.run(
        [str(ONETASKGRAPH_BIN), "task", "list", "--source", source, "--project", native]
        + ["--limit", "1000", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    items = [entry["item"] for entry in json.loads(listed.stdout)["items"]]
    return {item["metadata"]["onepipeline.id"]: item for item in items}


def _settlements(driven: DrivenRun) -> dict[str, object]:
    """Each task's projected settlement, by node id."""
    return {
        node: item["metadata"].get("onepipeline.settlement")
        for node, item in _listed(driven).items()
    }


def _record_of(driven: DrivenRun, node: NodeId) -> Path:
    """Where the destination keeps `node`'s task, as the store reports it."""
    return Path(str(_listed(driven)[node]["location"]["path"]))


def _destination_bytes(driven: DrivenRun) -> dict[Path, bytes]:
    """Every record the destination keeps for the launched project, byte for byte."""
    root, native = plan_fixture_source.root(), driven.project.split(":", 1)[1]
    kept = [root / PROJECTS_DIRECTORY / f"{native}.md"]
    kept += sorted((root / TASKS_DIRECTORY / native).rglob("*.md"))
    return {path: path.read_bytes() for path in kept}


def _attempts_after_settling(driven: DrivenRun) -> list[Projection]:
    """Settle the waiting node, and answer every projection attempt recorded after it."""
    seen = len(quiet_projections(driven))
    apply(
        driven,
        {
            "op": "settle",
            "id": SETTLED_NODE,
            "outcome": SETTLED_OUTCOME,
            "evidence": "settled so the run has a transition to project",
        },
    )
    waited_for(
        f"a projection attempt after {SETTLED_NODE} settled",
        lambda: projections(driven)[seen:] or None,
        PATIENCE_SECONDS,
    )
    return quiet_projections(driven)[seen:]


def test_the_engine_reads_and_projects_without_the_plan_store_executable(
    driven: DrivenRun, poisoned_store: PoisonedStore
) -> None:
    """The plan is read and a settlement projected with `ONETASKGRAPH_BIN` poisoned."""
    first = waited_for(
        "the run's first projection",
        lambda: next(iter(projections(driven)), None),
        PATIENCE_SECONDS,
    )
    assert (first.scope, first.whole_because, first.outcome) == ("members", None, "projected"), (
        first
    )
    assert set(first.items) == {HELD_NODE, SETTLED_NODE} and first.actions is not None, first
    assert first.actions["created"] == 0 and first.calls == {"task-update": len(first.items)}, first
    attempts = _attempts_after_settling(driven)
    assert [(a.items, a.outcome) for a in attempts] == [((SETTLED_NODE,), "projected")], attempts

    settlement = _settlements(driven)[SETTLED_NODE]
    assert isinstance(settlement, dict) and settlement.get("status") == SETTLED_OUTCOME, (
        f"the settlement never reached the destination on disk: {settlement!r}"
    )
    ran = poisoned_store.ran
    assert not ran.exists(), f"something ran the program ONETASKGRAPH_BIN names: {ran.read_text()}"


def test_a_refused_destination_read_stops_the_projection_and_writes_nothing(
    driven: DrivenRun,
) -> None:
    """An unreadable record fails the attempt that meets it, and leaves every record as it was.

    The write-back sends an existing item a targeted update, which the store makes by reading
    the item's record and writing back only the fields that changed. A read it cannot trust
    has to end the projection rather than fall back to a default — a read that defaulted
    would be a write over fields the run never owned. The fault is on the one record the
    attempt carries, because the adopted engine reads nothing else: the project is read only
    by an attempt that creates an item. Both halves are asserted, because either alone
    passes for the wrong reason: a run that never projected leaves the destination untouched
    too.
    """
    waited_for(
        "the run's first projection",
        lambda: next((a for a in projections(driven) if a.outcome == "projected"), None),
        PATIENCE_SECONDS,
    )
    quiet_projections(driven)
    before = _destination_bytes(driven)
    settled_record = _record_of(driven, SETTLED_NODE)
    assert settled_record in before, (settled_record, sorted(before))
    settled_record.chmod(0)
    try:
        after_fault = _attempts_after_settling(driven)
    finally:
        settled_record.chmod(0o644)

    assert after_fault and [(a.items, a.outcome, a.failure_class) for a in after_fault] == [
        ((SETTLED_NODE,), "failed", "refused")
    ] * len(after_fault), (
        f"a projection past an unreadable destination was not refused: {after_fault}"
    )
    assert _destination_bytes(driven) == before, (
        "the refused projection rewrote the destination anyway; a destination read it "
        "cannot trust has to leave the store exactly as it found it"
    )
