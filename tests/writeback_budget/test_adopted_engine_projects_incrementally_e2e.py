"""The adopted engine copies only what changed, and waits for the graph after a refusal.

The settlement write-back is what spent this host's GraphQL allowance. Every change a run
folded copied every node of the plan to the board, and a projection the store had refused
was asked again on a timer for as long as the run lasted. onepipeline 0.29.4 carries the
two landings that end both: a projection carries only the nodes whose projection changed,
naming each to the store's `project copy --member`; and a refusal is reported once and
attempted again only when the run's graph next changes. It records every attempt in the
run's `writeback-projections.jsonl`. `tests/test_adopted_engine_carries_this_plan.py`
holds that the adopted release's history *contains* those landings; this journey holds
that the installed engine *does* them, on a real launch against a local Markdown project.

The record is the engine's own account, so it is never the evidence alone.
`held_onetaskgraph.py` records every command the engine hands the store, and the
destination's own records are read before and after, through the real store CLI and
byte for byte off disk:

* the first projection is recorded `whole` and `first`, and its `project copy` names no
  `--member`;
* settling one waiting node from evidence is recorded as a `members` projection carrying
  that node alone. Exactly one `project copy` follows, naming that node's shadow task and
  no other, and that node's destination record gains the status and settlement the run
  projected. Every unnamed node's record stays byte for byte what it was, including one
  whose title the journey edited there first, the way a person edits a board;
* a node added by a live edit is created at the destination, and its destination record is
  then removed. The next change to that node is refused by the store, recorded once as
  `failed` / `refused`, and no further `project copy` is made across a window longer than
  the engine's one-minute retry ceiling while the graph stays unchanged. Exactly one more
  is made once the graph next changes.

On onepipeline 0.29.3, the release before both landings, the run writes no projection
record at all, so the journey fails at its first read of one: "the run's first projection
never arrived".
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
    copies_started,
    launched,
    member_id,
    projections,
    quiescent,
    reply,
    store_calls,
    waited_for,
)
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from plan_fixture_root import ROOT as FIXTURE_ROOT
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.project_store import TASKS_DIRECTORY
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
#: again is one the timer no longer reaches.
RETRY_CEILING_SECONDS = 60
REFUSAL_WINDOW_SECONDS = RETRY_CEILING_SECONDS + 20

#: What a person writes over a destination task's title, which no projection of another
#: node may carry away.
EDITED_TITLE = "title: retitled by a person on the board"

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
        prefix="incremental",
        goal="keep a run driven while its settlement projections are read",
        held_node=HELD_NODE,
        waiting_nodes=(SETTLED_NODE, EDITED_NODE),
    ) as run:
        yield run
    # llmlint: ignore-end[e2e_not_mocked]


def _destination(driven: DrivenRun) -> dict[str, DestinationTask]:
    """Every task of the launched project, by node id, read through the real store CLI.

    The real binary rather than the recorder, so reading the destination adds nothing to
    the log of what the engine asked. Each task's record path is where its local Markdown
    source keeps it, so a journey can compare it byte for byte.
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
        record = FIXTURE_ROOT / TASKS_DIRECTORY / f"{identifier.split(':', 1)[1]}.md"
        assert record.is_file(), f"{identifier} is kept nowhere this journey can read: {record}"
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
    reply(driven, {"op": "cancel", "id": node, "reason": reason})


def test_a_projection_carries_only_what_changed_and_a_refusal_waits_for_the_graph(
    driven: DrivenRun,
) -> None:
    """Whole first, then one node per change; a refusal is asked again only on a change."""
    first = waited_for(
        "the run's first projection",
        lambda: next(iter(projections(driven)), None),
        PATIENCE_SECONDS,
    )
    assert (first.scope, first.whole_because, first.outcome) == ("whole", "first", "projected"), (
        first
    )
    # Only the write-back copies, so the first copy the store was handed is that attempt's.
    first_copy = copies_started(store_calls(driven))[0]
    assert first_copy.members == (), first_copy
    assert "--member" not in first_copy.args, first_copy

    recorded = quiescent(driven)
    calls = store_calls(driven)
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

    reply(
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
    transition = quiescent(driven)[len(recorded) :]
    assert [
        (attempt.scope, attempt.whole_because, attempt.items, attempt.outcome)
        for attempt in transition
    ] == [("members", None, (SETTLED_NODE,), "projected")], transition

    handed = store_calls(driven)[len(calls) :]
    copied = copies_started(handed)
    assert len(copied) == 1, copied
    assert copied[0].members == (member_id(driven.project, SETTLED_NODE),), copied[0]
    assert not any(call.verb == "task list" for call in handed), handed

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
            f"{node} was not named by the copy and its destination record changed:\n"
            f"{after[node].record.read_text(encoding='utf-8')}"
        )
    assert EDITED_TITLE in after[EDITED_NODE].record.read_text(encoding="utf-8")

    reply(
        driven,
        {
            "op": "add",
            "node": {
                "id": ADDED_NODE,
                "task": "Record this follow-up without dispatching.",
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
    recorded = quiescent(driven)
    created = _destination(driven)[ADDED_NODE]
    # llmlint: ignore[tests_mirror_real_usage] A local Markdown destination is edited
    # through its own files, as the retitle above is, and the provisioned onetaskgraph
    # offers no verb that removes a task: removing its record is how a person removes
    # one, and a removed task is what makes the store refuse the next copy.
    created.record.unlink()
    calls = store_calls(driven)

    _cancel(driven, ADDED_NODE, "parked so the store refuses a task that is gone")
    refused = waited_for(
        "the store's refusal",
        lambda: _new_projections(driven, len(recorded)),
        PATIENCE_SECONDS,
    )
    assert [(attempt.items, attempt.outcome, attempt.failure_class) for attempt in refused] == [
        ((ADDED_NODE,), "failed", "refused")
    ], refused
    copies_at_refusal = len(copies_started(store_calls(driven)))

    watched_until = time.monotonic() + REFUSAL_WINDOW_SECONDS
    while time.monotonic() < watched_until:
        assert len(projections(driven)) == len(recorded) + 1, projections(driven)[len(recorded) :]
        assert len(copies_started(store_calls(driven))) == copies_at_refusal, (
            "the store was asked to copy again after refusing, with the graph unchanged: "
            f"{copies_started(store_calls(driven))[copies_at_refusal:]}"
        )
        time.sleep(1)

    _cancel(driven, EDITED_NODE, "parked so the graph changes after the refusal")
    waited_for(
        "the attempt after the graph changed",
        lambda: _new_projections(driven, len(recorded) + 1),
        PATIENCE_SECONDS,
    )
    # Past any retry the timer would still make of a failure it could retry.
    time.sleep(RETRY_CEILING_SECONDS // 4)
    assert len(copies_started(store_calls(driven))) == copies_at_refusal + 1, (
        "the graph changed once after the refusal and the store was asked to copy "
        f"{len(copies_started(store_calls(driven))) - copies_at_refusal} times: "
        f"{projections(driven)[len(recorded) :]}"
    )
    assert len(copies_started(store_calls(driven)[len(calls) :])) == 1

    # A local Markdown destination meters nothing, so every attempt's `spent` is null.
    assert all(attempt.spent is None for attempt in projections(driven)), projections(driven)
