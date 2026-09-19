"""The adopted engine keeps one destination item for the life of a node's lineage.

Every retry used to mint a new destination item: the superseded node's shadow task stayed
in the snapshot as `cancelled` — which the store paired with closing its issue — and the
replacement's, with no origin, was created beside it. On the `plans` board one piece of
work reached seven issues, each a card a person had to reconcile and a dead sibling every
whole re-projection spent the board's allowance on. onepipeline 0.38.0 carries the landing
that ends it (`op-lineage-item`, its `contract-divergences.md` entry 80): one item per
lineage, keyed on the lineage **root**, saying what the lineage **head** says, edited on a
retry and reopened when a closed one is retried or requeued.
`tests/test_adopted_engine_carries_this_plan.py` holds that the adopted release's history
*contains* that landing and `tests/test_engine_contracts.py` holds the stored shape to its
source; this journey holds that the installed engine *does* it, on a real launch against a
local Markdown project, never the live `plans` or `followups` board.

The record is the engine's own account, so it is read beside the destination's own task
records, off disk and through the real store CLI:

* a `retry` of the running held node is projected onto the destination record that node
  already had — one record for the lineage, at the id it had before, its `onepipeline.id`
  the root, `onepipeline.node` the replacement, `onepipeline.supersedes` naming the root,
  its body the replacement's task — and the attempt's line names the root under `items`
  and reports `created: 0`;
* a `cancel` of the running replacement settles it `cancelled` in the run's own record
  while its destination item reads the run's open park word on that same record — the
  installed engine's contract, and the reason a retry after a plain cancel reports
  `reopened: 0`. The journey then closes the card the way a person or an older engine
  would, writing `cancelled` onto that record through the store's own CLI, and a second
  `retry` writes an open word back onto the same record — `onepipeline.supersedes` now
  naming both superseded ids in order — with the attempt reporting `reopened: 1` and
  `created: 0`;
* the whole projection a driver makes after `just orchestrate --adopt <run>` carries the
  lineage once, under its root, and creates nothing for it.

On onepipeline 0.37.0, the release before the landing, the attempt after the first retry
carries the replacement as an item of its own beside the root and reports `created: 1` —
the minted sibling — so the journey fails at its first read of that attempt: "a
replacement was carried by id".
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
    just,
    launched,
    projections,
    quiescent,
    waited_for,
)
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from published_tools import ONETASKGRAPH_BIN
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The session these launches run under, stated rather than inherited: this suite runs
#: inside a dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-writeback-lineage-item-reuse"

#: The lineage this journey retries twice: its root, and the two replacements in order.
ROOT = NodeId("work")
SECOND = NodeId("work-2")
THIRD = NodeId("work-3")
#: A root of its own, dispatched and held beside the lineage so a driver stays alive while
#: the lineage's running node is cancelled and its dispatch reaped.
KEEPER = NodeId("keeper")

#: The task each replacement states, which the lineage's one record has to come to carry.
SECOND_TASK = "Second attempt: the record is edited, not minted."
THIRD_TASK = "Third attempt: the closed card is reopened, not minted."

#: The run's own cancel grace. The stand-in takes no redirection, so a cancelled dispatch
#: lives this long before the teardown reaps it and the node settles; short enough to wait
#: out twice, long enough for a real teardown under this tier's load.
CANCEL_GRACE_SECONDS = 10

#: The reserved keys the engine writes on an item, as `src/taskgraph.rs` declares them.
ID_KEY = "onepipeline.id"
NODE_KEY = "onepipeline.node"
SUPERSEDES_KEY = "onepipeline.supersedes"

#: The store's closed categories, one of which a person closing a card leaves it in.
CLOSED_BY_A_PERSON = "cancelled"

#: What the plan reviewer answers for each replacement's task. A `retry` stating a novel
#: task spends one judged turn under the reviewer `just review-plan` uses before the bus
#: appends it, and that turn goes to the stand-in codex the bench already puts at the
#: `oneharness` seam, which answers `smoke-ok` — no verdict at all — unless a journey
#: scripts one. The verdict is scripted, not the review: the reviewer, its schema and the
#: bus's refusal of an unjudged envelope all run for real.
PASSING_REVIEW = json.dumps([json.dumps({"passes": True, "findings": []})])

#: `tests/e2e/nx_workspace.py`'s group: every step here is a `just` recipe blocking on
#: `uv run`, which waits on the lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)


class DestinationItem(NamedTuple):
    """One task of the launched project, as the destination store holds it."""

    #: The task's qualified id, which is what the store's own verbs take.
    identifier: str
    record: Path
    status: object
    body: str
    metadata: dict[str, object]


@pytest.fixture
def driven(
    tmp_path: Path, request: pytest.FixtureRequest, oneharness_bin: str
) -> Iterator[DrivenRun]:
    # llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `writeback-budget`
    # is already the Nx project edge this repository keeps for launch journeys over the
    # engine's write-back, and its one target is keyed on `writebackBudgetWorkspace`, the
    # `strace`-measured set of files a `just orchestrate` opens (`tests/nx_inputs.py`
    # says how it was taken): the harness configs, graphs, pins and run-ended hook chain
    # in that key are the launch's own machinery, which is what this journey exercises,
    # so an edit outside it does not pay for the launch and an edit inside it should.
    # llmlint: ignore-block[e2e_not_mocked] Only the paid model provider is faked, at the
    # `oneharness` seam, and the plan store is recorded by a wrapper that hands every call
    # to the real published `onetaskgraph` — the two boundaries this repository's
    # realistic-tests invariant permits doubling, and the shape this journey's task
    # requires. The recipe, the engine, its driver and write-back worker, and the store
    # on disk are all real.
    with launched(
        tmp_path,
        request,
        oneharness_bin,
        caller=__name__,
        session=LAUNCHING_SESSION,
        prefix="lineage",
        goal="keep a run driven while one node's lineage is retried, cancelled and retried",
        held_node=ROOT,
        waiting_nodes=(),
        independent_nodes=(KEEPER,),
        cancel_grace_seconds=CANCEL_GRACE_SECONDS,
    ) as run:
        run.environment["FAKE_CODEX_ANSWERS"] = PASSING_REVIEW
        yield run
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _store(*arguments: str) -> subprocess.CompletedProcess[str]:
    """One call of the real store CLI, as a person at the terminal makes it."""
    return subprocess.run(
        [str(ONETASKGRAPH_BIN), *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def _destination(driven: DrivenRun) -> list[DestinationItem]:
    """Every task of the launched project, read through the real store CLI.

    Every item rather than one per node, because the claim under test is a *count*: a
    lineage that minted a sibling shows up as two items sharing an `onepipeline.id`, which
    a read keyed by that id would silently collapse into one.
    """
    source, native = driven.project.split(":", 1)
    listed = _store(
        "task", "list", "--source", source, "--project", native, "--limit", "1000", "--json"
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    payload = json.loads(listed.stdout)
    assert isinstance(payload, dict) and isinstance(payload.get("items"), list), listed.stdout
    items: list[DestinationItem] = []
    for entry in payload["items"]:
        identifier, item = entry["id"], entry["item"]
        assert isinstance(identifier, str) and identifier.startswith(f"{source}:"), entry
        location = item.get("location") or {}
        record = Path(str(location.get("path", "")))
        assert record.is_file(), f"{identifier} is kept nowhere this journey can read: {entry}"
        metadata = item.get("metadata") or {}
        assert isinstance(metadata, dict), entry
        items.append(
            DestinationItem(
                identifier=identifier,
                record=record,
                status=item.get("status"),
                body=str(item.get("content") or ""),
                metadata=metadata,
            )
        )
    return items


def _lineage(driven: DrivenRun, root: NodeId) -> list[DestinationItem]:
    """Every destination item carrying `root` as its `onepipeline.id`."""
    return [item for item in _destination(driven) if item.metadata.get(ID_KEY) == root]


def _new_projections(driven: DrivenRun, seen: int) -> list[Projection] | None:
    recorded = projections(driven)
    return recorded[seen:] or None


def _carried(attempts: list[Projection], root: NodeId, *never: NodeId) -> list[Projection]:
    """The attempts among `attempts` that carried anything, each naming `root` and none of `never`.

    An attempt carrying nothing is a superseded node's own settlement landing — its lineage
    item is unchanged by it — and says nothing about how the lineage was projected.
    """
    carrying = [attempt for attempt in attempts if attempt.items]
    assert carrying, f"no attempt after the edit carried an item: {attempts}"
    for attempt in carrying:
        assert attempt.items.count(root) == 1, f"{root} is not carried once: {attempt}"
        assert not set(never) & set(attempt.items), f"a replacement was carried by id: {attempt}"
        assert attempt.actions is not None and attempt.actions["created"] == 0, attempt
    return carrying


def _retry(driven: DrivenRun, node: NodeId, replacement: NodeId, task: str) -> None:
    apply(
        driven,
        {
            "op": "retry",
            "id": node,
            "node": {
                "id": replacement,
                "persona": "engineer",
                "task": f"## What\n{task}\n\n## Why\nKeep the run driven.\n\n"
                "## Acceptance criteria\n- Done.",
            },
        },
    )


def _cancel_and_settle(driven: DrivenRun, node: NodeId, reason: str) -> None:
    """Cancel `node` and wait until the run's own record settles it `cancelled`.

    A cancel parks the node at once while its dispatch runs on; the settlement lands only
    once the grace has elapsed and the teardown has reaped the held turn, and a `retry` sent
    before then is refused for the dispatch still in flight. Read off the run's own journal
    stream rather than a view: the views render the park, which outranks the settlement.
    """
    apply(driven, {"op": "cancel", "id": node, "reason": reason})
    settlement = re.compile(rf"graph:{re.escape(node)}\s+node-settled cancelled\b")

    def settled() -> str | None:
        stream = just("monitor", driven.run, "--all", environment=driven.environment, seconds=60)
        return stream.stdout if settlement.search(stream.stdout) else None

    waited_for(
        f"{node} settling cancelled in the run's own record",
        settled,
        CANCEL_GRACE_SECONDS + PATIENCE_SECONDS,
    )


def _adopt(driven: DrivenRun) -> None:
    """Attach a fresh driver once the launch's own has let go of the settled run."""
    limit = deadline(CANCEL_GRACE_SECONDS + PATIENCE_SECONDS)
    refused = ""
    while time.monotonic() < limit:
        adopted = just(
            "orchestrate", "--adopt", driven.run, "--detach", environment=driven.environment
        )
        if adopted.returncode == 0:
            return
        refused = adopted.stdout + adopted.stderr
        assert "is still being driven" in refused, f"the adoption failed:\n{refused}"
        time.sleep(1.0)
    raise AssertionError(f"run {driven.run} was still being driven:\n{refused}")


def test_a_retried_node_keeps_its_one_destination_item_across_a_cancel_and_a_reopen(
    driven: DrivenRun,
) -> None:
    """Retry edits the item, a closed card retried reopens it, an adopted driver creates nothing."""
    first = waited_for(
        "the run's first projection",
        lambda: next(iter(projections(driven)), None),
        PATIENCE_SECONDS,
    )
    assert (first.scope, first.whole_because, first.outcome) == ("whole", "first", "projected"), (
        first
    )
    recorded = quiescent(driven)
    (before,) = _lineage(driven, ROOT)
    assert SUPERSEDES_KEY not in before.metadata and SECOND_TASK not in before.body, before

    # A running node may be retried; the stand-in holds the replacement's turn too.
    _retry(driven, ROOT, SECOND, SECOND_TASK)
    waited_for(
        f"a projection carrying {ROOT}",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )
    retried = _carried(quiescent(driven)[len(recorded) :], ROOT, SECOND)
    assert sum(attempt.actions["reopened"] for attempt in retried if attempt.actions) == 0, retried
    minted = [item for item in _destination(driven) if item.metadata.get(ID_KEY) == SECOND]
    assert not minted, f"the retry minted a destination item of its own: {minted}"
    (after_retry,) = _lineage(driven, ROOT)
    assert after_retry.identifier == before.identifier and after_retry.record == before.record, (
        before,
        after_retry,
    )
    assert after_retry.metadata.get(NODE_KEY) == SECOND, after_retry.metadata
    assert after_retry.metadata.get(SUPERSEDES_KEY) == [ROOT], after_retry.metadata
    assert SECOND_TASK in after_retry.body, after_retry.body
    recorded = quiescent(driven)

    _cancel_and_settle(
        driven, SECOND, "cancelled so the lineage's record can be closed and reopened"
    )
    parked = quiescent(driven)[len(recorded) :]
    (after_cancel,) = _lineage(driven, ROOT)
    assert after_cancel.identifier == before.identifier, after_cancel
    # The engine's contract: a cancelled running node's item reads the open park word, so
    # nothing is closed and nothing is reopened by the cancel itself.
    assert sum(attempt.actions["reopened"] for attempt in parked if attempt.actions) == 0, parked
    assert json.dumps(after_cancel.status) != json.dumps(before.status), after_cancel
    assert "cancelled" not in json.dumps(after_cancel.status), after_cancel
    assert after_cancel.metadata.get(NODE_KEY) == SECOND, after_cancel.metadata

    closed = _store("task", "status", "set", before.identifier, CLOSED_BY_A_PERSON)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    (person_closed,) = _lineage(driven, ROOT)
    assert "cancelled" in json.dumps(person_closed.status), person_closed
    recorded = quiescent(driven)

    _retry(driven, SECOND, THIRD, THIRD_TASK)
    waited_for(
        f"a projection carrying {ROOT} after the second retry",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )
    reopened = _carried(quiescent(driven)[len(recorded) :], ROOT, THIRD)
    # The first attempt after the retry is the one that wrote the open word over the
    # closed card, and it alone counts the reopen.
    assert [attempt.actions["reopened"] for attempt in reopened if attempt.actions][0] == 1, (
        reopened
    )
    assert sum(attempt.actions["reopened"] for attempt in reopened if attempt.actions) == 1, (
        reopened
    )
    (after_reopen,) = _lineage(driven, ROOT)
    assert after_reopen.identifier == before.identifier, after_reopen
    assert after_reopen.metadata.get(NODE_KEY) == THIRD, after_reopen.metadata
    assert after_reopen.metadata.get(SUPERSEDES_KEY) == [ROOT, SECOND], after_reopen.metadata
    assert THIRD_TASK in after_reopen.body, after_reopen.body
    assert "cancelled" not in json.dumps(after_reopen.status), after_reopen
    assert len(_destination(driven)) == 2, _destination(driven)

    # Park everything still running, so the run settles and its driver lets go.
    _cancel_and_settle(driven, THIRD, "parked so the run settles for an adoption")
    _cancel_and_settle(driven, KEEPER, "parked so the run settles for an adoption")
    recorded = quiescent(driven)
    _adopt(driven)
    adopted = waited_for(
        "the adopted driver's first projection",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )[0]
    assert (adopted.scope, adopted.whole_because, adopted.outcome) == (
        "whole",
        "first",
        "projected",
    ), adopted
    assert adopted.items.count(ROOT) == 1 and set(adopted.items) == {ROOT, KEEPER}, adopted
    assert adopted.actions is not None and adopted.actions["created"] == 0, adopted
    (after_adopt,) = _lineage(driven, ROOT)
    assert after_adopt.identifier == before.identifier, after_adopt
    assert after_adopt.metadata.get(NODE_KEY) == THIRD, after_adopt.metadata
    assert len(_destination(driven)) == 2, _destination(driven)
