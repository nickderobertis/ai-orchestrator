"""The adopted engine writes only what changed, as targeted updates, and never the whole plan.

The settlement write-back is what spent this host's GraphQL allowance. The engine before
this one copied the whole project on every fresh launch, every adopt and after every failed
attempt, and even a member copy rewrote each carried item's title, body, state and edges to
change one status. onepipeline 0.52.1 keeps a landed baseline beside the run —
`writeback-landed.json`, entry 93 of its `docs/contract-divergences.md` — and every attempt
carries only what differs from it, sending each existing item one targeted update naming
only the fields that differ (entry 73). This journey holds that the installed engine does
that, on a real `just orchestrate` launch against a local Markdown project.

The engine links the store, so there is no command of its to record: what it did is read
off what it left behind — the destination's own records, through this checkout's real store
CLI and byte for byte off disk, and the run's own `writeback-projections.jsonl` and
`writeback-landed.json`:

* the launch seeds the baseline, and the first projection is a `members` attempt carrying
  every lineage's claim as targeted updates — no copy, no project read, no page of tasks;
* settling one waiting node is one attempt carrying that node alone as one `task-update`
  writing its status and metadata, and that node's record changes only its status and its
  settlement keys, while every other record stays byte for byte what it was, including one
  whose title the journey edited there first, the way a person edits a board;
* an attempt whose item is gone fails, and once the item is back and the graph moves again
  the attempts that follow carry only the lineages that had not landed and the one that
  changed — never a whole copy, and never a lineage that had already landed — and a
  settlement on the person-edited node writes its status without touching its title;
* a node an `add` states is the one thing a copy is still made for: its attempt copies that
  lineage alone, reads the project once to restate it, creates one item, and leaves every
  other record byte for byte what it was;
* a `requeue` stating a new task for the parked node reaches its one item as one targeted
  update that writes the new body, and nothing else is rewritten.

On onepipeline 0.51.0, the release before this one, the first projection is `whole` /
`first` and every line is at schema version 3, so the journey fails at its first read of
the record: "a projection record is malformed".
"""

from __future__ import annotations

import json
import re
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
    landed,
    launched,
    projections,
    quiet_projections,
    waited_for,
)
from nx_workspace import SHARED_TOOLCHAIN_GROUP

#: The session these launches run under, stated rather than inherited: this suite runs
#: inside a dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-writeback-targeted-updates"

#: The node whose dispatch keeps the run driven, and the nodes waiting behind it.
HELD_NODE = NodeId("work")
SETTLED_NODE = NodeId("later-1")
EDITED_NODE = NodeId("later-2")
REMOVED_NODE = NodeId("later-3")
WAITING = (SETTLED_NODE, EDITED_NODE, REMOVED_NODE)
#: The node a live edit adds, which the run has no item for until a copy creates one.
ADDED_NODE = NodeId("follow-up")
#: What the parked node's requeue restates as its task, which its one item has to come to
#: carry.
REQUEUED_WHAT = "Requeued: the item's body is updated in place."

#: The outcome a settled node is put at. `failed` rather than `done`, so no dependent's
#: state moves with it and the transition is that one node's alone.
SETTLED_OUTCOME = "failed"
#: A manager-observed landing, deliberately unrelated to any dispatch branch so the
#: destination can only learn it from the stated settlement evidence.
STATED_LANDING = "https://github.com/octo-org/example/pull/17"

#: The keys a settlement writes onto an item, each engine-owned — the settlement key
#: `src/taskgraph.rs` names and the four landing keys `src/writeback.rs` declares beside
#: it; a targeted update of a settlement may set these and nothing else in the item's
#: metadata. `tests/test_engine_contracts.py`'s
#: `test_the_settlement_keys_a_targeted_update_may_set_are_exactly_the_engines` holds this
#: set equal to the pinned engine's, so a key added or removed on either side fails there.
SETTLEMENT_KEYS = frozenset(
    {
        "onepipeline.settlement",
        "onepipeline.landing",
        "onepipeline.landing_commit",
        "onepipeline.landing_evidence",
        "onepipeline.change_url",
    }
)

#: What a person writes over a destination task's title, which no projection may carry away.
EDITED_TITLE = "retitled by a person on the board"

#: What the plan reviewer answers for the added node's task. An `add` stating a novel task
#: spends one judged turn under the reviewer `just review-plan` uses before the bus appends
#: it, on the stand-in codex the bench puts at the `oneharness` seam; the verdict is
#: scripted, and the reviewer, its schema and the engine's own criteria check run for real.
PASSING_REVIEW = json.dumps([json.dumps({"passes": True, "findings": []})])

#: `tests/e2e/nx_workspace.py`'s group: every step here is a `just` recipe blocking on
#: `uv run`, which waits on the lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)


@pytest.fixture
def driven(
    tmp_path: Path, request: pytest.FixtureRequest, oneharness_bin: str
) -> Iterator[DrivenRun]:
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
        prefix="targeted",
        goal="keep a run driven while its settlement projections are read",
        held_node=HELD_NODE,
        waiting_nodes=WAITING,
    ) as run:
        run.environment["FAKE_CODEX_ANSWERS"] = PASSING_REVIEW
        yield run
    # llmlint: ignore-end[e2e_not_mocked]


def _by_node(driven: DrivenRun) -> dict[str, DestinationItem]:
    items: dict[str, DestinationItem] = {}
    for item in destination(driven):
        root = item.metadata.get("onepipeline.id")
        assert isinstance(root, str) and root not in items, f"a lineage holds two items: {item}"
        items[root] = item
    return items


def _new_projections(driven: DrivenRun, seen: int) -> list[Projection] | None:
    recorded = projections(driven)
    return recorded[seen:] or None


def _targeted_only(attempts: list[Projection]) -> None:
    for attempt in attempts:
        assert (attempt.scope, attempt.whole_because) == ("members", None), attempt
        assert set(attempt.calls) <= TARGETED_CALLS, f"a call beyond a targeted update: {attempt}"


def _settle(driven: DrivenRun, node: NodeId, evidence: str) -> None:
    apply(
        driven,
        {
            "op": "settle",
            "id": node,
            "outcome": SETTLED_OUTCOME,
            "evidence": evidence,
            "landing": STATED_LANDING,
        },
    )


def test_a_settlement_is_one_targeted_update_and_a_failure_carries_only_what_had_not_landed(
    driven: DrivenRun,
) -> None:
    """No whole copy anywhere; one field-scoped update per change; a person's edit stands."""
    first = waited_for(
        "the run's first projection",
        lambda: next(iter(projections(driven)), None),
        PATIENCE_SECONDS,
    )
    _targeted_only([first])
    assert first.outcome == "projected", first
    assert set(first.items) == {HELD_NODE, *WAITING}, first
    assert first.calls == {"task-update": len(first.items)}, first
    assert first.actions is not None and first.actions["created"] == 0, first

    recorded = quiet_projections(driven)
    _targeted_only(recorded)
    baseline = landed(driven)
    before = _by_node(driven)
    assert set(baseline) == set(before) == {HELD_NODE, *WAITING}, (sorted(baseline), before)
    for node, item in before.items():
        assert baseline[node].destination == item.identifier, (baseline[node], item)
        assert baseline[node].status is not None, f"{node}'s claim never landed: {baseline[node]}"
    assert not SETTLEMENT_KEYS & set(before[SETTLED_NODE].metadata), before[SETTLED_NODE]

    edited = before[EDITED_NODE].record
    retitled, count = re.subn(
        r"^title: .*$",
        f"title: {EDITED_TITLE}",
        edited.read_text(encoding="utf-8"),
        count=1,
        flags=re.M,
    )
    assert count == 1, edited.read_text(encoding="utf-8")
    edited.write_text(retitled, encoding="utf-8")
    untouched = {
        node: item.record.read_bytes() for node, item in before.items() if node != SETTLED_NODE
    }

    _settle(driven, SETTLED_NODE, "settled so one node's transition is projected alone")
    waited_for(
        f"a projection carrying {SETTLED_NODE}",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )
    transition = quiet_projections(driven)[len(recorded) :]
    _targeted_only(transition)
    assert [(attempt.items, attempt.outcome, attempt.calls) for attempt in transition] == [
        ((SETTLED_NODE,), "projected", {"task-update": 1})
    ], transition
    assert set(transition[0].updated_fields) <= {"status", "metadata"}, transition[0]
    assert transition[0].updated_fields.get("metadata") == 1, transition[0]

    after = _by_node(driven)
    landed_bytes = {node: after[node].record.read_bytes() for node in (HELD_NODE, SETTLED_NODE)}
    settled_before, settled_after = before[SETTLED_NODE], after[SETTLED_NODE]
    assert (settled_after.identifier, settled_after.title, settled_after.body) == (
        settled_before.identifier,
        settled_before.title,
        settled_before.body,
    ), (settled_before, settled_after)
    assert settled_after.status != settled_before.status, (settled_before, settled_after)
    moved = {
        key
        for key in set(settled_before.metadata) | set(settled_after.metadata)
        if settled_before.metadata.get(key) != settled_after.metadata.get(key)
    }
    assert "onepipeline.settlement" in moved and moved <= SETTLEMENT_KEYS, moved
    assert settled_after.metadata.get("onepipeline.change_url") == STATED_LANDING, settled_after
    for node, written in untouched.items():
        assert after[node].record.read_bytes() == written, (
            f"{node} was not carried and its destination record changed:\n"
            f"{after[node].record.read_text(encoding='utf-8')}"
        )
    assert after[EDITED_NODE].title == EDITED_TITLE, after[EDITED_NODE]
    advanced = landed(driven)
    assert advanced[SETTLED_NODE].status != baseline[SETTLED_NODE].status, advanced
    assert {node: advanced[node] for node in untouched} == {
        node: baseline[node] for node in untouched
    }, "the baseline moved for a lineage no write landed on"

    recorded = quiet_projections(driven)
    removed = after[REMOVED_NODE].record
    kept = removed.read_bytes()
    # llmlint: ignore[tests_mirror_real_usage] A local Markdown destination is edited
    # through its own files, as the retitle above is, and the store CLI offers no verb that
    # removes a task: removing its record is how a person removes one, and a missing item
    # is what makes the store fail the targeted update the next change sends it.
    removed.unlink()
    apply(driven, {"op": "cancel", "id": REMOVED_NODE, "reason": "parked while its item is gone"})
    failed = waited_for(
        f"the failed attempt carrying {REMOVED_NODE}",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )
    assert [(attempt.items, attempt.outcome) for attempt in failed[:1]] == [
        ((REMOVED_NODE,), "failed")
    ], failed
    assert landed(driven)[REMOVED_NODE] == advanced[REMOVED_NODE], "a failed write advanced"

    removed.write_bytes(kept)
    seen = len(projections(driven))
    _settle(driven, EDITED_NODE, "settled so the graph moves after the failure")

    def both_landed() -> dict[str, DestinationItem] | None:
        now = _by_node(driven)
        settled = SETTLEMENT_KEYS & set(now[EDITED_NODE].metadata)
        parked = now[REMOVED_NODE].status != after[REMOVED_NODE].status
        return now if settled and parked else None

    final = waited_for(
        f"{REMOVED_NODE} and {EDITED_NODE} both landing", both_landed, PATIENCE_SECONDS
    )
    after_failure = [
        attempt for attempt in quiet_projections(driven)[len(recorded) :] if attempt.items
    ]
    _targeted_only(after_failure)
    for attempt in after_failure:
        assert set(attempt.items) <= {REMOVED_NODE, EDITED_NODE}, (
            f"an attempt after the failure carried a lineage that had landed: {attempt}"
        )
        assert set(attempt.calls) <= {"task-update"}, attempt
    succeeded = [attempt for attempt in after_failure if attempt.outcome == "projected"]
    assert succeeded and any(REMOVED_NODE in attempt.items for attempt in succeeded), (
        f"what the failure lost never landed: {after_failure}"
    )
    assert any(EDITED_NODE in attempt.items for attempt in projections(driven)[seen:]), projections(
        driven
    )[seen:]

    assert final[EDITED_NODE].title == EDITED_TITLE, final[EDITED_NODE]
    for node, written in landed_bytes.items():
        assert final[node].record.read_bytes() == written, (
            f"{node} had landed and its record changed after the failure:\n"
            f"{final[node].record.read_text(encoding='utf-8')}"
        )
    recorded = quiet_projections(driven)
    existing = {node: item.record.read_bytes() for node, item in _by_node(driven).items()}
    apply(
        driven,
        {
            "op": "add",
            "node": {
                "id": ADDED_NODE,
                "task": "## What\nRecord this follow-up without dispatching.\n\n"
                "## Why\nA node the run has no item for yet.\n\n"
                "## Acceptance criteria\n- The follow-up is recorded.",
                "expects_no_diff": True,
                "deps": [HELD_NODE],
            },
        },
    )
    created = waited_for(
        f"{ADDED_NODE} created at the destination",
        lambda: _by_node(driven).get(ADDED_NODE),
        PATIENCE_SECONDS,
    )
    creating = [
        attempt
        for attempt in quiet_projections(driven)[len(recorded) :]
        if ADDED_NODE in attempt.items
    ]
    assert [(attempt.items, attempt.outcome) for attempt in creating] == [
        ((ADDED_NODE,), "projected")
    ], creating
    (creation,) = creating
    assert (creation.scope, creation.whole_because) == ("members", None), creation
    assert creation.calls.get("project-copy") == 1 and creation.calls.get("project-show") == 1, (
        creation
    )
    assert "task-list" not in creation.calls, creation
    assert creation.actions is not None and creation.actions["created"] == 1, creation
    assert landed(driven)[ADDED_NODE].destination == created.identifier, landed(driven)
    now = _by_node(driven)
    for node, written in existing.items():
        assert now[node].record.read_bytes() == written, (
            f"creating {ADDED_NODE} rewrote {node}'s record:\n"
            f"{now[node].record.read_text(encoding='utf-8')}"
        )

    recorded = quiet_projections(driven)
    existing = {node: item.record.read_bytes() for node, item in _by_node(driven).items()}
    apply(
        driven,
        {
            "op": "requeue",
            "id": REMOVED_NODE,
            "amend": {
                "task": f"## What\n{REQUEUED_WHAT}\n\n## Why\nKeep the run driven.\n\n"
                "## Acceptance criteria\n- Done."
            },
        },
    )
    requeued = waited_for(
        f"{REMOVED_NODE}'s requeued task landing",
        lambda: next(
            (
                item
                for node, item in _by_node(driven).items()
                if node == REMOVED_NODE and REQUEUED_WHAT in item.body
            ),
            None,
        ),
        PATIENCE_SECONDS,
    )
    assert requeued.identifier == final[REMOVED_NODE].identifier, requeued
    rewritten = quiet_projections(driven)[len(recorded) :]
    _targeted_only(rewritten)
    assert [(attempt.items, attempt.outcome, attempt.calls) for attempt in rewritten] == [
        ((REMOVED_NODE,), "projected", {"task-update": 1})
    ], rewritten
    assert "content" in rewritten[0].updated_fields, rewritten[0]
    now = _by_node(driven)
    for node, written in existing.items():
        if node != REMOVED_NODE:
            assert now[node].record.read_bytes() == written, (
                f"requeueing {REMOVED_NODE} rewrote {node}'s record:\n"
                f"{now[node].record.read_text(encoding='utf-8')}"
            )

    # A local Markdown destination meters nothing, so every attempt's `spent` is null.
    assert all(attempt.spent is None for attempt in projections(driven)), projections(driven)
