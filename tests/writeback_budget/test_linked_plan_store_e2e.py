"""The adopted engine reads and projects the plan store it links, and spawns none.

Since onepipeline 0.50.0 the engine reads a run's plan and projects its settlements through
the onetaskgraph crates it links rather than through a spawned `onetaskgraph`, so
`ONETASKGRAPH_BIN` reaches nothing. These journeys hold that on this host's own launch
path, against a local Markdown destination read back off disk:

* with `ONETASKGRAPH_BIN` naming a program that fails whenever it runs, the run still reads
  its plan and projects a settlement onto the destination, and the program never runs;
* a destination item the store cannot read ends the projection and writes nothing: the
  fault is a real one on the real store — the settled node's own task record made
  unreadable, the one record its targeted update reads — so every attempt it meets is
  recorded `failed`, and every record the destination keeps stays byte for byte what it
  was.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import plan_fixture_source
import pytest
from driven_run import (
    PATIENCE_SECONDS,
    DrivenRun,
    NodeId,
    Projection,
    apply,
    destination,
    launched,
    projections,
    quiet_projections,
    waited_for,
)
from nx_workspace import WORKSPACE_INSTALL_MARKS
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

#: A reader of the toolchain this checkout provisioned: every step here runs its tool
#: through a `just` recipe or `uv run`, and no writer touches that toolchain, so
#: `tests/e2e/nx_workspace.py` asks no xdist group of it.
pytestmark = list(WORKSPACE_INSTALL_MARKS)


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


def _settlements(driven: DrivenRun) -> dict[str, object]:
    """Each task's projected settlement, by node id, read through the real store CLI."""
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
    items = json.loads(listed.stdout)["items"]
    return {
        entry["item"]["metadata"]["onepipeline.id"]: entry["item"]["metadata"].get(
            "onepipeline.settlement"
        )
        for entry in items
    }


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
    attempts = _attempts_after_settling(driven)
    assert [(a.items, a.outcome) for a in attempts] == [((SETTLED_NODE,), "projected")], attempts

    settlement = _settlements(driven)[SETTLED_NODE]
    assert isinstance(settlement, dict) and settlement.get("status") == SETTLED_OUTCOME, (
        f"the settlement never reached the destination on disk: {settlement!r}"
    )
    ran = poisoned_store.ran
    assert not ran.exists(), f"something ran the program ONETASKGRAPH_BIN names: {ran.read_text()}"


def test_an_unreadable_destination_item_stops_the_projection_and_writes_nothing(
    driven: DrivenRun,
) -> None:
    """An unreadable item fails the attempt it meets, and leaves every record as it was.

    A targeted update reads the one item it names and nothing else — not the project's
    record, not a page of tasks — so the fault is put on that item. A read it cannot trust
    has to end the projection rather than fall back to a default, since a write from a
    defaulted read would replace what a person kept there. Both halves are asserted,
    because either alone passes for the wrong reason: a run that never projected leaves
    the destination untouched too.
    """
    waited_for(
        "the run's first projection",
        lambda: next((a for a in projections(driven) if a.outcome == "projected"), None),
        PATIENCE_SECONDS,
    )
    quiet_projections(driven)
    before = _destination_bytes(driven)
    settled_record = next(
        item.record
        for item in destination(driven)
        if item.metadata.get("onepipeline.id") == SETTLED_NODE
    )
    assert settled_record in before, (settled_record, sorted(before))
    settled_record.chmod(0)
    try:
        after_fault = _attempts_after_settling(driven)
    finally:
        settled_record.chmod(0o644)

    assert after_fault and all(a.outcome == "failed" for a in after_fault), (
        f"a projection past an unreadable item was not failed: {after_fault}"
    )
    assert all(a.items == (SETTLED_NODE,) for a in after_fault), after_fault
    assert _destination_bytes(driven) == before, (
        "the failed projection rewrote the destination anyway; a destination read it "
        "cannot trust has to leave the store exactly as it found it"
    )
