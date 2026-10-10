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
  `reopened: 0`. The journey then closes the card the way a person would, writing
  `cancelled` onto that record through the store's own CLI, and a second `retry` writes an
  open word back onto the same record — `onepipeline.supersedes` now naming both superseded
  ids in order — as a targeted update creating nothing. The run's landed baseline still
  knew the item as parked, so the attempt counts `reopened: 0`: a reopen is counted off the
  word the run knew (entry 80), and the card is reopened all the same;
* the first projection a driver makes after `just orchestrate --adopt <run>` is no whole
  copy: it reads and copies nothing, creates nothing, and carries the lineage at most
  once, under its root.

On onepipeline 0.37.0, the release before the landing, the attempt after the first retry
carries the replacement as an item of its own beside the root and reports `created: 1` —
the minted sibling — so the journey fails at its first read of that attempt: "a
replacement was carried by id". On onepipeline 0.51.0, before the landed baseline, the
first projection is a `whole` copy at schema version 3, and the journey fails at its
first read of the record.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from driven_run import (
    PATIENCE_SECONDS,
    TARGETED_CALLS,
    DestinationItem,
    DrivenRun,
    NodeId,
    Projection,
    apply,
    destination,
    just,
    landed,
    launched,
    projections,
    quiet_projections,
    store,
    waited_for,
)
from nx_workspace import WORKSPACE_INSTALL_MARKS
from waits import deadline

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

#: A reader of the toolchain this checkout provisioned: every step here runs its tool
#: through a `just` recipe or `uv run`, and no writer touches that toolchain, so
#: `tests/e2e/nx_workspace.py` asks no xdist group of it.
pytestmark = list(WORKSPACE_INSTALL_MARKS)


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
    # `oneharness` seam — the one boundary this repository's realistic-tests invariant
    # permits doubling here. The recipe, the engine, its driver and write-back worker, the
    # store it links and the store on disk are all real.
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


def _lineage(driven: DrivenRun, root: NodeId) -> list[DestinationItem]:
    """Every destination item carrying `root` as its `onepipeline.id`."""
    return [item for item in destination(driven) if item.metadata.get(ID_KEY) == root]


def _targeted_only(attempt: Projection) -> None:
    assert (attempt.scope, attempt.whole_because) == ("members", None), attempt
    assert set(attempt.calls) <= TARGETED_CALLS, f"a call beyond a targeted update: {attempt}"


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
        _targeted_only(attempt)
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
    _targeted_only(first)
    assert first.outcome == "projected" and ROOT in first.items, first
    recorded = quiet_projections(driven)
    (before,) = _lineage(driven, ROOT)
    assert SUPERSEDES_KEY not in before.metadata and SECOND_TASK not in before.body, before

    # A running node may be retried; the stand-in holds the replacement's turn too.
    _retry(driven, ROOT, SECOND, SECOND_TASK)
    waited_for(
        f"a projection carrying {ROOT}",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )
    retried = _carried(quiet_projections(driven)[len(recorded) :], ROOT, SECOND)
    assert sum(attempt.actions["reopened"] for attempt in retried if attempt.actions) == 0, retried
    minted = [item for item in destination(driven) if item.metadata.get(ID_KEY) == SECOND]
    assert not minted, f"the retry minted a destination item of its own: {minted}"
    (after_retry,) = _lineage(driven, ROOT)
    assert after_retry.identifier == before.identifier and after_retry.record == before.record, (
        before,
        after_retry,
    )
    assert after_retry.metadata.get(NODE_KEY) == SECOND, after_retry.metadata
    assert after_retry.metadata.get(SUPERSEDES_KEY) == [ROOT], after_retry.metadata
    assert SECOND_TASK in after_retry.body, after_retry.body
    recorded = quiet_projections(driven)

    _cancel_and_settle(
        driven, SECOND, "cancelled so the lineage's record can be closed and reopened"
    )
    parked = quiet_projections(driven)[len(recorded) :]
    (after_cancel,) = _lineage(driven, ROOT)
    assert after_cancel.identifier == before.identifier, after_cancel
    # The engine's contract: a cancelled running node's item reads the open park word, so
    # nothing is closed and nothing is reopened by the cancel itself.
    assert sum(attempt.actions["reopened"] for attempt in parked if attempt.actions) == 0, parked
    assert json.dumps(after_cancel.status) != json.dumps(before.status), after_cancel
    assert "cancelled" not in json.dumps(after_cancel.status), after_cancel
    assert after_cancel.metadata.get(NODE_KEY) == SECOND, after_cancel.metadata

    closed = store("task", "status", "set", before.identifier, CLOSED_BY_A_PERSON)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    (person_closed,) = _lineage(driven, ROOT)
    assert "cancelled" in json.dumps(person_closed.status), person_closed
    recorded = quiet_projections(driven)

    _retry(driven, SECOND, THIRD, THIRD_TASK)
    waited_for(
        f"a projection carrying {ROOT} after the second retry",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )
    reopened = _carried(quiet_projections(driven)[len(recorded) :], ROOT, THIRD)
    # The run's baseline knew the item as parked, not closed, so the update that wrote the
    # open word over the person's close counts no reopen — entry 80 counts off the word the
    # run knew — while the card below reads open all the same.
    assert landed(driven)[ROOT].status is not None, landed(driven)[ROOT]
    assert "cancelled" not in json.dumps(landed(driven)[ROOT].status), landed(driven)[ROOT]
    assert sum(attempt.actions["reopened"] for attempt in reopened if attempt.actions) == 0, (
        reopened
    )
    (after_reopen,) = _lineage(driven, ROOT)
    assert after_reopen.identifier == before.identifier, after_reopen
    assert after_reopen.metadata.get(NODE_KEY) == THIRD, after_reopen.metadata
    assert after_reopen.metadata.get(SUPERSEDES_KEY) == [ROOT, SECOND], after_reopen.metadata
    assert THIRD_TASK in after_reopen.body, after_reopen.body
    assert "cancelled" not in json.dumps(after_reopen.status), after_reopen
    assert len(destination(driven)) == 2, destination(driven)

    # Park everything still running, so the run settles and its driver lets go.
    _cancel_and_settle(driven, THIRD, "parked so the run settles for an adoption")
    _cancel_and_settle(driven, KEEPER, "parked so the run settles for an adoption")
    recorded = quiet_projections(driven)
    _adopt(driven)
    adopted = waited_for(
        "the adopted driver's first projection",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )[0]
    _targeted_only(adopted)
    assert adopted.outcome == "projected", adopted
    assert adopted.items.count(ROOT) <= 1 and set(adopted.items) <= {ROOT, KEEPER}, adopted
    assert adopted.actions is None or adopted.actions["created"] == 0, adopted
    (after_adopt,) = _lineage(driven, ROOT)
    assert after_adopt.identifier == before.identifier, after_adopt
    assert after_adopt.metadata.get(NODE_KEY) == THIRD, after_adopt.metadata
    assert len(destination(driven)) == 2, destination(driven)
