"""The adopted engine allows a settlement copy the deadline its items earn, on a real run.

The write-back is what keeps a plan's board in step with its run, and before
https://github.com/nickderobertis/onepipeline/pull/248 the store command it copies
through was killed at a fixed sixty seconds however many items it was writing — so
`root-causes-539-fixes` settled 18/18 with its board silently behind it, the 34-item copy
having outgrown the minute. That landing allows the copy `max(60, per-item budget × items)`
seconds, ten per item unless a launch says otherwise, and this host adopts the shipped
default. Since onepipeline 0.29.4 a copy carries only the nodes whose projection changed,
so *items* counts the nodes a copy carries: every node for a whole copy, and one for a
single node's transition. `tests/test_adopted_engine_carries_this_plan.py` holds that the
adopted release's history *contains* the landing; this journey holds that the installed
engine *does* it, and it fails on the engine before it, whose driver kills the first copy
at sixty seconds.

Nothing about a quick copy tells the engines apart, so `held_onetaskgraph.py` beside this
journey holds a `project copy` before handing it to the real store, and records when each
call started and whether it answered. `driven_run.py` keeps the run driven for the whole
journey, with nine nodes waiting behind the held one: ten items, and a deadline of a hundred
seconds for a copy carrying all of them. Three copies are measured, off the run's own
records:

* the first, whole copy held past the sixty-second floor and inside the computed deadline
  answers while the driver is alive, and the driver reports no failure — where the engine
  before the landing kills it at sixty and says so;
* a copy carrying one parked node, held past the floor, is killed at the floor, and the
  refusal the driver writes and the surface it raises both state that arithmetic;
* the attempt after that failure is whole again, carries all ten items, and, held past the
  computed deadline, is killed there, its projection record stating ten items at the
  launch record's budget.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from driven_run import (
    PATIENCE_SECONDS,
    DrivenRun,
    NodeId,
    StoreCall,
    driver_log,
    just,
    launched,
    member_id,
    projections,
    quiescent,
    reply,
    store_calls,
    waited_for,
)
from nx_workspace import SHARED_TOOLCHAIN_GROUP

#: The session these launches run under, stated rather than inherited: this suite runs
#: inside a dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-writeback-copy-deadline"

#: The node whose dispatch keeps the run driven, and the nodes waiting behind it. With it
#: they are the ten items every whole copy writes.
HELD_NODE = NodeId("work")
WAITING_NODES = tuple(NodeId(f"later-{index}") for index in range(1, 10))
ITEMS = 1 + len(WAITING_NODES)

#: The fixed backstop every store command ran under before the landing, and still the
#: floor under a copy's deadline.
FLOOR_SECONDS = 60
#: The per-item budget a launch naming none runs under, as the engine ships it.
SHIPPED_ITEM_BUDGET_SECONDS = 10
COMPUTED_DEADLINE_SECONDS = ITEMS * SHIPPED_ITEM_BUDGET_SECONDS

#: Past the floor by a margin no scheduling jitter closes, and inside the computed
#: deadline by more than the real copy of ten items takes to answer after its hold.
LANDING_HOLD_SECONDS = FLOOR_SECONDS + 15
#: Past the computed deadline, so the engine's kill arrives inside the hold.
REFUSED_HOLD_SECONDS = COMPUTED_DEADLINE_SECONDS + 30

#: `tests/e2e/nx_workspace.py`'s group: every step here is a `just` recipe blocking on
#: `uv run`, which waits on the lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)


class SurfaceLine(NamedTuple):
    """The two fields this journey reads off one line of the run's surface log."""

    kind: str
    message: str

    @classmethod
    def parse(cls, line: str) -> SurfaceLine:
        entry = json.loads(line)
        if not isinstance(entry, dict):
            raise ValueError(f"a surface log line is not an object: {line!r}")
        kind, message = entry.get("kind"), entry.get("message", "")
        if not isinstance(kind, str) or not isinstance(message, str):
            raise ValueError(f"a surface log line carries no string kind and message: {line!r}")
        return cls(kind=kind, message=message)


@pytest.fixture
def driven(
    tmp_path: Path, request: pytest.FixtureRequest, oneharness_bin: str
) -> Iterator[DrivenRun]:
    """Launch the plan with its first copy behind the hold, and stop the run however it ends."""
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
        prefix="copy-deadline",
        goal="keep a run driven while its settlement copies are timed",
        held_node=HELD_NODE,
        waiting_nodes=WAITING_NODES,
        first_hold_seconds=LANDING_HOLD_SECONDS,
    ) as run:
        yield run
    # llmlint: ignore-end[e2e_not_mocked]


def _copy_held_for(
    driven: DrivenRun, seconds: int, *, members: tuple[str, ...]
) -> StoreCall | None:
    """The first `project copy` that started under a hold of `seconds`, naming `members`."""
    return next(
        (
            call
            for call in store_calls(driven)
            if call.event == "started"
            and call.verb == "project copy"
            and call.held == seconds
            and call.members == members
        ),
        None,
    )


def _answer_to(driven: DrivenRun, started: StoreCall) -> StoreCall | None:
    """The answer the call that `started` records gave, once it gave one."""
    return next(
        (
            call
            for call in store_calls(driven)
            if call.event == "answered" and call.pid == started.pid
        ),
        None,
    )


def _landed_or_refused(driven: DrivenRun, started: StoreCall) -> StoreCall | None:
    """The held copy's answer, or a failure quoting the driver the moment it refused it.

    Failing on the driver's own report rather than waiting out the whole deadline, so a
    copy killed at a fixed floor is named as that: the driver's line states the deadline it
    applied, which is the evidence the refusal is about.
    """
    reported = driver_log(driven)
    assert "write-back failed" not in reported, (
        f"the copy held {LANDING_HOLD_SECONDS} seconds past the {FLOOR_SECONDS} second floor "
        f"was killed under what should be a deadline of {ITEMS} items × "
        f"{SHIPPED_ITEM_BUDGET_SECONDS} seconds:\n{reported}"
    )
    return _answer_to(driven, started)


def _refusal_surface(driven: DrivenRun, refusal: str) -> str | None:
    """The finding the run raised that names `refusal`, off the run's own surface log."""
    log = driven.root / "channel" / "surfaces.jsonl"
    if not log.is_file():
        return None
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = SurfaceLine.parse(line)
        if entry.kind == "finding" and refusal in entry.message:
            return entry.message
    return None


def test_a_copy_is_allowed_the_deadline_its_items_earn_and_refused_only_past_it(
    driven: DrivenRun,
) -> None:
    """Held past the floor it lands; held past items × budget it is killed, saying so.

    Under the engine before the landing the first copy is killed at sixty seconds and the
    driver writes `project-copy exceeded 60 seconds`, so the journey fails there, quoting
    that line.
    """
    landing = waited_for(
        "the driver's first copy",
        lambda: _copy_held_for(driven, LANDING_HOLD_SECONDS, members=()),
        PATIENCE_SECONDS,
    )
    driven.hold.unlink()
    answered = waited_for(
        f"an answer from the copy held {LANDING_HOLD_SECONDS} seconds",
        lambda: _landed_or_refused(driven, landing),
        COMPUTED_DEADLINE_SECONDS + PATIENCE_SECONDS,
    )
    assert answered.exit == 0, answered
    assert answered.at - landing.at > FLOOR_SECONDS, (landing, answered)
    # The driver that could have killed the copy was alive to do it: the run is still
    # driven, its held node still in flight.
    status = just("status", driven.run, environment=driven.environment, seconds=60)
    assert status.returncode == 0, status.stdout + status.stderr
    assert "DRIVER DEAD" not in status.stdout, status.stdout
    assert "write-back failed" not in driver_log(driven), driver_log(driven)

    launch_record = json.loads((driven.root / "launch.json").read_text(encoding="utf-8"))
    assert launch_record["writeback_item_budget"] == SHIPPED_ITEM_BUDGET_SECONDS, (
        "the launch did not record the shipped per-item budget, so the deadlines below are "
        f"not the ones this host adopts: {launch_record.get('writeback_item_budget')!r}"
    )

    # A copy carrying one node is bounded by the floor, and killed there. Parking a waiting
    # node is what gives the driver a transition to project, and it carries that node alone.
    recorded = len(quiescent(driven))
    parked = WAITING_NODES[0]
    driven.hold.write_text(str(REFUSED_HOLD_SECONDS), encoding="utf-8")
    reply(
        driven,
        {"op": "cancel", "id": parked, "reason": "parked so the run has a new snapshot to project"},
    )
    member_copy = waited_for(
        f"a copy carrying {parked} held {REFUSED_HOLD_SECONDS} seconds",
        lambda: _copy_held_for(
            driven, REFUSED_HOLD_SECONDS, members=(member_id(driven.project, parked),)
        ),
        PATIENCE_SECONDS,
    )
    floor_refusal = (
        f"project-copy exceeded {FLOOR_SECONDS} seconds (the {FLOOR_SECONDS} second floor; "
        f"1 item × {SHIPPED_ITEM_BUDGET_SECONDS} seconds per item is less)"
    )
    waited_for(
        "the driver's report that the copy carrying one node was killed",
        lambda: floor_refusal in driver_log(driven) or None,
        FLOOR_SECONDS + PATIENCE_SECONDS,
    )
    surface = waited_for(
        "the finding the run raised about the killed copy",
        lambda: _refusal_surface(driven, floor_refusal),
        PATIENCE_SECONDS,
    )
    assert parked in surface, surface
    assert _answer_to(driven, member_copy) is None, (
        "the copy held past the floor answered, so the engine never killed it: "
        f"{store_calls(driven)}"
    )

    # The attempt after a failure is whole: ten items, and the deadline they earn.
    whole_copy = waited_for(
        f"a whole copy held {REFUSED_HOLD_SECONDS} seconds",
        lambda: _copy_held_for(driven, REFUSED_HOLD_SECONDS, members=()),
        PATIENCE_SECONDS,
    )
    whole_refusal = (
        f"project-copy exceeded {COMPUTED_DEADLINE_SECONDS} seconds "
        f"({ITEMS} items × {SHIPPED_ITEM_BUDGET_SECONDS} seconds per item)"
    )
    killed = waited_for(
        "the projection record of the whole copy killed at its deadline",
        lambda: next(
            (
                attempt
                for attempt in projections(driven)[recorded:]
                if attempt.reason is not None and whole_refusal in attempt.reason
            ),
            None,
        ),
        COMPUTED_DEADLINE_SECONDS + PATIENCE_SECONDS,
    )
    driven.hold.unlink(missing_ok=True)
    assert (killed.scope, killed.whole_because, killed.outcome) == (
        "whole",
        "after-failure",
        "failed",
    ), killed
    assert len(killed.items) == ITEMS, killed
    assert _answer_to(driven, whole_copy) is None, (
        "the copy held past the computed deadline answered, so the engine never killed it: "
        f"{store_calls(driven)}"
    )
