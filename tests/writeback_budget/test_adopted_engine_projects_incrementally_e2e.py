"""The adopted engine copies only what changed, and waits for the graph after a refusal.

The settlement write-back is what spent this host's GraphQL allowance: every change a run
folded copied every node to the board, and a refused projection was asked again on a timer
for as long as the run lasted. The engine now carries only the nodes whose projection
changed, and attempts a refused projection again only when the run's graph next changes.
Nothing stands between the engine and the store it links, so this journey reads the run's
own `writeback-projections.jsonl` beside the destination's records, through the real store
CLI and byte for byte off disk, and never takes the record as the evidence alone.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

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
    task,
    waited_for,
)
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The session these launches run under, stated rather than inherited: this suite runs
#: inside a dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-writeback-incremental-projection"

#: The node whose dispatch keeps the run driven, and the nodes waiting behind it.
HELD_NODE = NodeId("work")
SETTLED_NODE = NodeId("later-1")
EDITED_NODE = NodeId("later-2")
#: The node a live edit adds, which is therefore a destination task the run itself created.
ADDED_NODE = NodeId("follow-up")

#: The outcome the settled node is put at. `failed` rather than `done`, so no dependent's
#: state moves with it and the transition is that one node's alone.
SETTLED_OUTCOME = "failed"
#: A manager-observed landing, deliberately unrelated to the failed dispatch branch so
#: the destination can only learn it from the stated settlement evidence.
STATED_LANDING = "https://github.com/octo-org/example/pull/17"

#: The engine's `writeback::RETRY_CEILING`: the longest a failing projection waits before
#: it is attempted again on a timer. A refusal watched for longer than this and not asked
#: again is one the timer no longer reaches. `tests/test_engine_contracts.py` holds it to
#: the engine's own declaration.
RETRY_CEILING_SECONDS = 60
REFUSAL_WINDOW_SECONDS = RETRY_CEILING_SECONDS + 20

#: What a person writes over a destination task's title, which no projection of another
#: node may carry away.
EDITED_TITLE = "title: retitled by a person on the board"

#: What the plan reviewer answers for the added node's task. An `add` stating a novel task
#: spends one judged turn under the reviewer `just review-plan` uses before the bus appends
#: it, and that turn goes to the stand-in codex the bench already puts at the `oneharness`
#: seam, which answers no verdict unless a journey scripts one.
PASSING_REVIEW = json.dumps([json.dumps({"passes": True, "findings": []})])

#: `tests/e2e/nx_workspace.py`'s group: every step here is a `just` recipe blocking on
#: `uv run`, which waits on the lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)


class DestinationTask(NamedTuple):
    """One task of the launched project, as the destination store holds it."""

    record: Path
    status: str
    settlement: object
    landing: object
    landing_evidence: object
    change_url: object


@pytest.fixture
def driven(
    tmp_path: Path, request: pytest.FixtureRequest, oneharness_bin: str
) -> Iterator[DrivenRun]:
    # llmlint: ignore-block[e2e_not_mocked] Only the paid model provider is faked, at the
    # `oneharness` seam, which this repository's realistic-tests invariant permits doubling.
    # The recipe, the engine, its driver and write-back worker, the plan store it links and
    # the store on disk are all real.
    with launched(
        tmp_path,
        request,
        oneharness_bin,
        caller=__name__,
        session=LAUNCHING_SESSION,
        prefix="incremental",
        goal="keep a run driven while its settlement projections are read",
        held_node=HELD_NODE,
        waiting_nodes=(SETTLED_NODE, EDITED_NODE),
    ) as run:
        run.environment["FAKE_CODEX_ANSWERS"] = PASSING_REVIEW
        yield run
    # llmlint: ignore-end[e2e_not_mocked]


def _destination(driven: DrivenRun) -> dict[str, DestinationTask]:
    """Every task of the launched project, by node id, read through the real store CLI.

    Each task's record path is where its local Markdown source keeps it, so a journey can
    compare it byte for byte.
    """
    source, native = driven.project.split(":", 1)
    listed = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            "task",
            "list",
            "--source",
            source,
            "--project",
            native,
            "--limit",
            "1000",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    payload = json.loads(listed.stdout)
    assert isinstance(payload, dict) and isinstance(payload.get("items"), list), listed.stdout
    tasks: dict[str, DestinationTask] = {}
    for entry in payload["items"]:
        identifier, item = entry["id"], entry["item"]
        assert isinstance(identifier, str) and identifier.startswith(f"{source}:"), entry
        location = item.get("location") or {}
        record = Path(str(location.get("path", "")))
        assert record.is_file(), f"{identifier} is kept nowhere this journey can read: {entry}"
        metadata = item.get("metadata") or {}
        tasks[metadata["onepipeline.id"]] = DestinationTask(
            record=record,
            status=json.dumps(item.get("status"), sort_keys=True),
            settlement=metadata.get("onepipeline.settlement"),
            landing=metadata.get("onepipeline.landing"),
            landing_evidence=metadata.get("onepipeline.landing_evidence"),
            change_url=metadata.get("onepipeline.change_url"),
        )
    return tasks


def _new_projections(driven: DrivenRun, seen: int) -> list[Projection] | None:
    """The attempts recorded after the first `seen`, once there is at least one."""
    recorded = projections(driven)
    return recorded[seen:] or None


def _cancel(driven: DrivenRun, node: str, reason: str) -> None:
    apply(driven, {"op": "cancel", "id": node, "reason": reason})


def test_a_projection_carries_only_what_changed_and_a_refusal_waits_for_the_graph(
    driven: DrivenRun,
) -> None:
    """Each node once by targeted update, then one node per change; a refusal waits for one."""
    first = waited_for(
        "the run's first projection",
        lambda: next(iter(projections(driven)), None),
        PATIENCE_SECONDS,
    )
    assert (first.scope, first.whole_because, first.outcome) == ("members", None, "projected"), (
        first
    )
    assert (
        set(first.items) == {HELD_NODE, SETTLED_NODE, EDITED_NODE} and first.actions is not None
    ), first
    assert first.actions["created"] == 0 and first.calls == {"task-update": len(first.items)}, first

    recorded = quiet_projections(driven)
    before = _destination(driven)
    assert {HELD_NODE, SETTLED_NODE, EDITED_NODE} <= set(before), sorted(before)
    assert before[SETTLED_NODE].settlement is None, before[SETTLED_NODE]

    edited = before[EDITED_NODE].record
    retitled, count = re.subn(
        r"^title: .*$", EDITED_TITLE, edited.read_text(encoding="utf-8"), count=1, flags=re.M
    )
    assert count == 1, edited.read_text(encoding="utf-8")
    edited.write_text(retitled, encoding="utf-8")
    unnamed = {
        node: task.record.read_bytes() for node, task in before.items() if node != SETTLED_NODE
    }

    apply(
        driven,
        {
            "op": "settle",
            "id": SETTLED_NODE,
            "outcome": SETTLED_OUTCOME,
            "evidence": "settled so one node's transition is projected alone",
            "landing": STATED_LANDING,
        },
    )
    waited_for(
        f"a projection carrying {SETTLED_NODE}",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )
    transition = quiet_projections(driven)[len(recorded) :]
    assert [
        (attempt.scope, attempt.whole_because, attempt.items, attempt.outcome)
        for attempt in transition
    ] == [("members", None, (SETTLED_NODE,), "projected")], transition

    after = _destination(driven)
    settlement = after[SETTLED_NODE].settlement
    assert isinstance(settlement, dict) and settlement.get("status") == SETTLED_OUTCOME, after[
        SETTLED_NODE
    ]
    assert (
        after[SETTLED_NODE].landing,
        after[SETTLED_NODE].landing_evidence,
        after[SETTLED_NODE].change_url,
    ) == ("landed", "stated-change-request", STATED_LANDING), after[SETTLED_NODE]
    assert after[SETTLED_NODE].status != before[SETTLED_NODE].status, (
        before[SETTLED_NODE],
        after[SETTLED_NODE],
    )
    for node, written in unnamed.items():
        assert after[node].record.read_bytes() == written, (
            f"{node} was not carried by the projection and its destination record changed:\n"
            f"{after[node].record.read_text(encoding='utf-8')}"
        )
    assert EDITED_TITLE in after[EDITED_NODE].record.read_text(encoding="utf-8")

    apply(
        driven,
        {
            "op": "add",
            "node": {
                "id": ADDED_NODE,
                "task": task("Record this follow-up without dispatching."),
                "expects_no_diff": True,
                "deps": [HELD_NODE],
            },
        },
    )
    waited_for(
        f"{ADDED_NODE} created at the destination",
        lambda: _destination(driven).get(ADDED_NODE),
        PATIENCE_SECONDS,
    )
    recorded = quiet_projections(driven)
    created = _destination(driven)[ADDED_NODE]
    # llmlint: ignore[tests_mirror_real_usage] A local Markdown destination is edited
    # through its own files, as the retitle above is, and the provisioned onetaskgraph
    # offers no verb that removes a task: removing its record is how a person removes
    # one, and a removed task is what makes the store refuse the next copy.
    created.record.unlink()

    _cancel(driven, ADDED_NODE, "parked so the store refuses a task that is gone")
    refused = waited_for(
        "the store's refusal",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )
    assert [(attempt.items, attempt.outcome, attempt.failure_class) for attempt in refused] == [
        ((ADDED_NODE,), "failed", "refused")
    ], refused

    watched_until = time.monotonic() + REFUSAL_WINDOW_SECONDS
    while time.monotonic() < watched_until:
        assert len(projections(driven)) == len(recorded) + 1, (
            "the store was asked again after refusing, with the graph unchanged: "
            f"{projections(driven)[len(recorded) :]}"
        )
        time.sleep(1)

    _cancel(driven, EDITED_NODE, "parked so the graph changes after the refusal")
    waited_for(
        "the attempt after the graph changed",
        lambda: _new_projections(driven, len(recorded) + 1),
        PATIENCE_SECONDS,
    )
    # Long enough for a second attempt made straight after the first to be recorded.
    time.sleep(RETRY_CEILING_SECONDS // 4)
    assert len(projections(driven)) == len(recorded) + 2, (
        "the graph changed once after the refusal and the store was asked "
        f"{len(projections(driven)) - len(recorded) - 1} times: "
        f"{projections(driven)[len(recorded) :]}"
    )

    # A local Markdown destination meters nothing, so every attempt's `spent` is null.
    assert all(attempt.spent is None for attempt in projections(driven)), projections(driven)
