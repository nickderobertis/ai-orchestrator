"""A landing a manager starts by recipe is that manager's supervised work until it closes.

`just publish-branch`, `just repo-recover` and `just integrate` run the pinned engine's
landing verbs, which register each landing in the launching session's flow before any side
effect. This journey holds the manager's whole surface to that, for each recipe and each of
the three ways a landing can end — it finishes, the repository's merge path refuses it, or
its process is killed mid-operation:

- held at its push, the landing is live work: the declared `Stop` entry holds the session
  while no watch is armed, and `just unwatched --acknowledge` and `just unpublished
  --acknowledge` refuse the run it came from, its own flow and its branch — for both the
  manager who owns that run and the one landing it — writing nothing;
- the run and branch had been acknowledged *before* the landing began, and that earlier
  acknowledgement neither answers for the landing nor stops its owner's watch waking;
- the owner's default `just watch --flow` stays armed through the landing's progress, which
  the detailed reader is handed, and wakes on its milestone, its blocker or its death with
  the actual outcome; once a refused or dead landing's closure is acknowledged the session
  owes it nothing more, and a preserved branch's own obligation is answered separately.

The estate is two managers, two registered identities sharing a branch name, and a real run
under the first manager that left every branch these landings land.
"""

# The finding these answer is which Nx project owns this file: `publish-and-landing`, the
# directory-owned project whose key is the recipes, wrappers and configs every landing
# journey here drives, and which declares no marker tier.
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[shell_test_tiers_stay_split] see above

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple, get_args

import pytest
from conftest import git
from manager_landings import (
    ORIGINATOR,
    WATCH_ELAPSED,
    WATCH_FLOW_FAILED,
    Armed,
    Estate,
    Identity,
    Landing,
    Session,
    estate,
    fetched,
    held_identity,
    originating_run,
    preserved_by_session,
    sibling_identity,
    stop_hook,
    watch,
    woken_until_ended,
)
from waits import until

from orchestrator.root import REPO_ROOT

#: In one worker: the estate is one host's state.
pytestmark = [pytest.mark.xdist_group("manager-landing-supervision")]


def lander(verb: Verb, outcome: Outcome) -> Session:
    """The manager landing one journey's branch: its own, so one journey's closure is not
    another's obligation."""
    return Session(f"manager-lander-{verb}-{outcome}")


RUN = "left-branches"
#: The three landing recipes a manager starts.
Verb = Literal["publish-branch", "repo-recover", "integrate"]
#: How a landing ends: it finishes, its merge path refuses it, or it is killed mid-way.
Outcome = Literal["finished", "refused", "died"]
VERBS: tuple[Verb, ...] = get_args(Verb)
OUTCOMES: tuple[Outcome, ...] = get_args(Outcome)
#: The integration train: three branches, landed in this order.
TRAIN = 3


class Cell(NamedTuple):
    """One journey: the recipe it lands through, and how the landing ends."""

    verb: Verb
    outcome: Outcome


class Node(NamedTuple):
    """One node of the originating run: its id, and the commit subject its worker leaves."""

    id: str
    subject: str


@dataclass(frozen=True)
class World:
    """The estate, its two identities, and the branch(es) each journey lands."""

    estate: Estate
    service: Identity
    other: Identity
    branches: dict[Cell, list[str]]


def _nodes() -> dict[Cell, list[Node]]:
    """Each journey's originating nodes."""
    nodes: dict[Cell, list[Node]] = {}
    for verb in VERBS:
        for outcome in OUTCOMES:
            cell, prefix = Cell(verb, outcome), f"{verb.split('-')[0]}-{outcome}"
            match cell:
                case Cell("integrate", "finished"):
                    nodes[cell] = [
                        Node(f"{prefix}-{index}", f"feat: add {prefix} {index}")
                        for index in range(TRAIN)
                    ]
                case Cell("repo-recover", _):
                    nodes[cell] = [Node(prefix, f"feat: add {prefix} (incomplete step)")]
                case _:
                    nodes[cell] = [Node(prefix, f"feat: add {prefix}")]
    return nodes


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[World]:
    """One run under the originator leaves every branch; it settles and is acknowledged."""
    the_estate = estate(tmp_path_factory.mktemp("manager-landings"), oneharness_bin)
    service = held_identity(the_estate, "service")
    other = sibling_identity(the_estate, service, "other")
    nodes = _nodes()
    left = originating_run(
        the_estate,
        service,
        RUN,
        {node.id: node.subject for members in nodes.values() for node in members},
    )
    branches = {cell: [left[node.id] for node in members] for cell, members in nodes.items()}
    # The originator closes the settled run before anybody lands what it left.
    closed = the_estate.recipe(
        ORIGINATOR,
        service,
        "unwatched",
        "--acknowledge",
        RUN,
        "--reason",
        "its work is landed by hand",
    )
    assert closed.returncode == 0, closed.stdout + closed.stderr
    yield World(estate=the_estate, service=service, other=other, branches=branches)
    the_estate.close()


def _arguments(verb: Verb, branches: list[str], service: Identity) -> list[str]:
    if verb == "integrate":
        return ["integrate", *branches, "--push"]
    return [verb, branches[0], "--repo", str(service.execution), "--no-draft"]


def _acknowledge_branches(world: World, branches: list[str]) -> None:
    """The originator acknowledges each branch before any landing of it begins."""
    for branch in branches:
        acknowledged = world.estate.recipe(
            ORIGINATOR,
            world.service,
            "unpublished",
            "--acknowledge",
            branch,
            "--reason",
            "kept until a manager lands it",
        )
        _acknowledged(acknowledged.stdout + acknowledged.stderr, branch)


def _acknowledged(said: str, branch: str) -> None:
    """The recipe's receipt for a recorded acknowledgement.

    Its exit status says whether anything is still owed afterwards — every other branch the
    run left is — so the receipt is what says this one was recorded.
    """
    assert said.startswith(f"acknowledged {branch} ["), said


def _refused_without_record(
    world: World, landing: Landing, session: Session, *arguments: str
) -> None:
    """Refused because the landing is live — the refusal names it — with nothing written."""
    before = world.estate.acknowledgement_records()
    refused = world.estate.recipe(session, world.service, *arguments)
    said = refused.stdout + refused.stderr
    assert refused.returncode != 0, (arguments, said)
    assert "nothing was recorded" in said and landing.flow in said, (arguments, said)
    assert world.estate.acknowledgement_records() == before, arguments


def _start(world: World, verb: Verb, outcome: Outcome) -> tuple[Landing, list[str]]:
    branches = world.branches[Cell(verb, outcome)]
    world.estate.hold.rearm()
    _acknowledge_branches(world, branches)
    cwd = REPO_ROOT
    if verb == "integrate":
        # A train runs inside the publication checkout, whose base the journeys before it
        # moved on its origin: fast-forwarded first, as `just sync` does for a person.
        synced = world.estate.recipe(
            ORIGINATOR, world.service, "sync", cwd=world.service.publication
        )
        assert synced.returncode == 0, synced.stdout + synced.stderr
        fetched(world.service, branches)
        cwd = world.service.publication
    landing = Landing(
        world.estate,
        lander(verb, outcome),
        world.service,
        _arguments(verb, branches, world.service),
        cwd=cwd,
    )
    until(
        "the landing's push to reach the repository's held pre-push hook",
        lambda: world.estate.hold.arrivals() >= 1,
        seconds=300,
        state=landing.said,
    )
    return landing, branches


def _held_and_unhideable(world: World, landing: Landing, branches: list[str]) -> None:
    """While it is held: owed a watch, and nothing that would hide it is recorded."""
    reason = stop_hook(world.estate, landing.session, world.service)
    assert reason is not None and landing.flow in reason, reason
    # Its owner cannot close the run the work came from again, though it closed it before.
    _refused_without_record(
        world, landing, ORIGINATOR, "unwatched", "--acknowledge", RUN, "--reason", "x"
    )
    _refused_without_record(
        world,
        landing,
        landing.session,
        "unwatched",
        "--acknowledge-flow",
        landing.flow,
        "--reason",
        "x",
    )
    # Nor can either manager acknowledge a branch it is advancing.
    for branch in branches:
        for session in (ORIGINATOR, landing.session):
            _refused_without_record(
                world, landing, session, "unpublished", "--acknowledge", branch, "--reason", "x"
            )


def _progress_does_not_wake_the_owner(world: World, landing: Landing) -> Armed:
    """Every event recorded so far is progress: the detailed reader takes each, the default
    watch armed from the start stays armed."""
    armed = Armed(world.estate, landing.session, world.service, landing.flow)
    cursor: str | None = None
    taken = 0
    while True:
        at = ["--cursor", cursor] if cursor else []
        detailed = watch(
            world.estate,
            landing.session,
            world.service,
            landing.flow,
            "--filter",
            "detailed",
            "--timeout",
            "0",
            *at,
        )
        if detailed.exit == WATCH_ELAPSED:
            break
        assert detailed.condition == "landing-progress", detailed.line
        assert detailed.significance == "progress", detailed.line
        taken += 1
        cursor = detailed.cursor
    assert taken >= 2, "the detailed reader was handed none of the landing's progress"
    assert armed.waiting(), "the owner's default watch woke on progress"
    return armed


@pytest.mark.parametrize("verb", VERBS)
def test_a_finished_landing_wakes_its_owner_on_its_milestones_and_then_owes_nothing(
    world: World, verb: Verb
) -> None:
    landing, branches = _start(world, verb, "finished")
    _held_and_unhideable(world, landing, branches)
    if verb == "publish-branch":
        # The same branch name in the other identity is that identity's: acknowledging it
        # there is not refused by this landing.
        preserved_by_session(world.estate, world.other, branches[0], ORIGINATOR)
        elsewhere = world.estate.recipe(
            ORIGINATOR,
            world.other,
            "unpublished",
            "--acknowledge",
            branches[0],
            "--repo",
            str(world.other.publication),
            "--reason",
            "the other identity's own work",
        )
        _acknowledged(elsewhere.stdout + elsewhere.stderr, branches[0])
    armed = _progress_does_not_wake_the_owner(world, landing)

    world.estate.hold.release()
    first = armed.returned()
    assert first.condition == "landing-progress", first.line
    assert first.significance == "milestone", (first.line, landing.said())
    woken, ending = woken_until_ended(
        world.estate, landing.session, world.service, landing.flow, first.cursor
    )
    assert landing.ended() == 0, landing.said()
    assert ending.condition == "ended" and ending.status == 0, ending.line
    wakes = [first, *woken]
    assert {wake.significance for wake in wakes} == {"milestone"}, [w.line for w in wakes]
    # One notice of the work reaching its base, however many branches the train carried.
    arrived = [wake for wake in wakes if wake.kind in ("base-arrived", "change-merged")]
    assert len(arrived) == 1, [wake.kind for wake in wakes]

    assert stop_hook(world.estate, landing.session, world.service) is None
    # The run it came from was settled and is not revived; its journal holds the mirror.
    status = world.estate.recipe(ORIGINATOR, world.service, "status", RUN, "--no-providers")
    assert "SETTLED" in status.stdout, status.stdout + status.stderr
    mirrored = world.estate.recipe(
        ORIGINATOR, world.service, "monitor", RUN, "--filter", "detailed"
    )
    shown = [line for line in mirrored.stdout.splitlines() if landing.operation() in line]
    assert any("base-arrived" in line for line in shown), mirrored.stdout + mirrored.stderr


@pytest.mark.parametrize("verb", VERBS)
def test_a_refused_landing_wakes_its_owner_with_the_refusal_and_owes_closure(
    world: World, verb: Verb
) -> None:
    landing, branches = _start(world, verb, "refused")
    _held_and_unhideable(world, landing, branches)
    armed = _progress_does_not_wake_the_owner(world, landing)

    world.estate.hold.release(refuse=True)
    first = armed.returned()
    assert first.condition == "landing-progress", first.line
    assert first.significance == "blocker", (first.line, landing.said())
    woken, ending = woken_until_ended(
        world.estate, landing.session, world.service, landing.flow, first.cursor
    )
    assert landing.ended() != 0, landing.said()
    assert {wake.significance for wake in woken} <= {"blocker"}, [w.line for w in woken]
    assert ending.exit == WATCH_FLOW_FAILED, ending.line

    _closure_is_owed_then_released(world, landing)
    if verb == "integrate":
        # The refusal reached the push alone: the train's merge stays on the publication
        # checkout's base, unpushed, and nothing reached the origin. A person discards it
        # before the next train.
        publication = world.service.publication
        git("fetch", "-q", "origin", cwd=publication)
        for branch in branches:
            assert _carried(publication, "main", branch)
            assert not _carried(publication, "origin/main", branch)
        git("reset", "-q", "--hard", "origin/main", cwd=publication)


@pytest.mark.parametrize("verb", VERBS)
def test_a_landing_killed_mid_operation_wakes_its_owner_with_its_death(
    world: World, verb: Verb
) -> None:
    landing, branches = _start(world, verb, "died")
    _held_and_unhideable(world, landing, branches)
    armed = _progress_does_not_wake_the_owner(world, landing)

    landing.kill()
    world.estate.hold.release()
    # A flow registered for this landing alone reports the landing's ending as its own: the
    # owner's watch wakes on the flow failing, and what it names is the death.
    ending = armed.returned()
    assert (ending.exit, ending.condition) == (WATCH_FLOW_FAILED, "flow-failed"), ending.line
    assert ending.died is True, ending.line
    assert landing.recorded("landing-died"), landing.kinds()
    planner = world.estate.recipe(landing.session, world.service, "monitor", "--flow", landing.flow)
    assert any(
        "landing-died" in line and "blocker" in line for line in planner.stdout.splitlines()
    ), planner.stdout + planner.stderr

    _closure_is_owed_then_released(world, landing)
    # The branch it was landing is a separate obligation, answered on its own once nothing
    # live advances it.
    if verb == "integrate":
        # The train squashed it onto the publication checkout's base before the push it died
        # in, so that base carries its work and no row owes it any more.
        for branch in branches:
            assert _carried(world.service.publication, "main", branch)
        owed = world.estate.recipe(ORIGINATOR, world.service, "unpublished")
        assert not any(branch in owed.stdout for branch in branches), owed.stdout
        return
    for branch in branches:
        kept = world.estate.recipe(
            ORIGINATOR,
            world.service,
            "unpublished",
            "--acknowledge",
            branch,
            "--reason",
            "its landing died; landed again later",
        )
        _acknowledged(kept.stdout + kept.stderr, branch)


def _carried(checkout: Path, base: str, branch: str) -> bool:
    """Whether ``base`` carries the file the branch's worker left: a squash keeps no ancestry."""
    tree = git("ls-tree", "--name-only", base, cwd=checkout).split()
    return f"{branch.rsplit('/', 1)[-1]}.txt" in tree


def _closure_is_owed_then_released(world: World, landing: Landing) -> None:
    reason = stop_hook(world.estate, landing.session, world.service)
    assert reason is not None and landing.flow in reason, reason
    closed = world.estate.recipe(
        landing.session,
        world.service,
        "unwatched",
        "--acknowledge-flow",
        landing.flow,
        "--reason",
        "read the ending; the branch is handled on its own",
    )
    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert stop_hook(world.estate, landing.session, world.service) is None


def test_the_estate_is_two_identities_sharing_a_branch_name(world: World) -> None:
    """The fixture's own claim, so a regression in it names itself."""
    assert Path(world.other.home) == Path(world.service.home)
    assert world.other.publication != world.service.publication
