"""Report where a destination holds the plan a copy landed there, and its design document.

`just finish-plan` copies a cleared plan into the source a person reviews it in, and the
two things they then have to open are the project and the one design document beside it.
Neither address is something this repository can compose: a destination decides its own
native id — a board mints a number where a directory keeps the name — and it decides
where a record lives, so a path or a URL assembled from a project name is one that names
nothing the moment the destination is a board.

So both are **read back out of the destination**. The landed project is found by the link the store
records on the plan when it copies it, and held to the origin stamp the store writes onto every
record it creates by copying — the two records the store keeps to tie a copy to its plan, the stamp
left off a copy out of a private source — so it is read by id rather than by listing every project
the destination holds; its location and its one document's location are then the store's own
answers, rendered by the one reading of them :func:`~orchestrator.plan_store.located` owns.

It is a command of its own rather than a line inside the copy because the copy's product
is the store's per-record report, which reaches the operator's streams whole. This asks a
second question of the destination afterwards — where the records it just wrote are —
and a reader that could not answer it must not turn a landed copy into a failed one.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence

from orchestrator import design_approval, plan_store
from orchestrator.project_store import qualified_id

#: Both locations were read and reported.
OK = 0

#: The destination could not be read, holds no project copied from this plan, holds
#: several, or does not hold exactly one design document for it — so nothing was
#: reported. There is no second status, because every one of those is the same thing to
#: an operator: the records landed and this could not say where.
UNREADABLE = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Report where ``destination`` holds one copied plan and its design document."""
    parser = argparse.ArgumentParser(
        prog="plan-locations",
        description=(
            "Report where a destination source holds the plan copied into it, and the "
            "design document a person reviews that plan as."
        ),
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    parser.add_argument(
        "--in",
        dest="destination",
        required=True,
        metavar="SOURCE",
        help="the configured source the plan was copied into",
    )
    args = parser.parse_args(argv)
    try:
        landed = copied(args.project, args.destination)
        document = design_approval.design_document(str(landed.qualified_id))
        members = plan_store.members(str(landed.qualified_id))
    # Every way this cannot answer is driven in `tests/test_plan_locations.py`, and the
    # answer it gives is read back off a real destination in both flow journeys. A plan with
    # no copy, a link naming a project not stamped as its copy, a link naming no project at
    # all, and two copies are each driven through this command against the board double in
    # `tests/e2e/board_copy_approval/test_board_copy_store_reads_e2e.py`. What no journey
    # can reach is a destination refusing to be read at all: this runs after a copy the
    # same command just made, so that is a store that answered that copy and then stopped
    # answering — which a journey would produce by breaking a store between two commands
    # of one script.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    except OSError as exc:
        print(f"plan-locations: {exc}", file=sys.stderr)
        print(
            f"plan-locations: the copy's own per-record report says what landed in "
            f"{args.destination!r}; this could not then say where it holds them",
            file=sys.stderr,
        )
        return UNREADABLE
    home = str(landed.qualified_id)
    held_in = home.partition(":")[0]
    print(
        f"plan-locations: {held_in} holds the plan at {plan_store.located(landed.location, home)}"
    )
    document_at = design_approval.located(document)
    print(f"plan-locations: {held_in} holds its design document at {document_at}")
    for member in sorted(members):
        print(f"plan-locations: its tasks routed to {member.partition(':')[0]} are in {member}")
    print(
        f"plan-locations: its home is {home}; approve it with `just approve-design {home}` "
        f"and launch it with `just orchestrate {home}`"
    )
    return OK


def copied(project: str, destination: str) -> plan_store.StoreProject:
    """The home project ``project`` was copied onto, in ``destination`` or a source it routes to.

    Found by the link the store records on ``project`` when it copies it —
    :data:`~orchestrator.plan_store.COPIES_KEY`, the counterpart it reached at each
    destination source — read with ``project show`` rather than by listing every project
    the destination holds, which on the `plans` board walked every plan ever filed. The
    link is looked up under ``destination`` and then under every source its routes name,
    because the store puts a plan's home in a routed source when every task routes there.
    What the link names is then held to the stamp that makes it a copy of this plan —
    :data:`~orchestrator.plan_store.ORIGIN_KEY` — so a link left pointing at somebody
    else's project is refused rather than reported. A copy out of a source declared private
    carries no stamp, because the store keeps the correspondence on the private side alone,
    so there the link is the record and what it names is refused only when it carries a
    stamp naming another plan.

    Two copies of one plan are refused rather than picked between, by
    :func:`stamped_copies` — a state somebody has to resolve, and reporting one of them
    would send a reviewer to whichever the link happened to name.
    """
    plan_store.qualified(project)
    sources = (destination, *plan_store.routed_sources(destination))
    links = plan_store.project_record(project).get("metadata")
    recorded = links.get(plan_store.COPIES_KEY) if isinstance(links, Mapping) else None
    keyed = next(
        (
            (source, link)
            for source in sources
            if isinstance(recorded, Mapping)
            and isinstance(link := recorded.get(source), str)
            and link
        ),
        None,
    )
    if keyed is None:
        raise OSError(
            f"source {destination!r}, and every source it routes to, holds no project the "
            f"store records as copied from {project}, so there is nothing there to point a "
            f"reviewer at"
        )
    source, linked = keyed
    # The link is the store's record of where the copy reached *in that source*, so one
    # that is no qualified id of that source is not a copy this step was asked to look for.
    parts = qualified_id(linked)
    if parts is None or parts.source != source:
        raise OSError(
            f"{project}'s copy link for source {source!r} names {linked!r}, which is no "
            f"project of that source, so it is not this plan's copy there and there is "
            f"nothing to point a reviewer at"
        )
    try:
        landed = _project(linked)
    except OSError as exc:
        raise OSError(
            f"{project}'s copy link names {linked}, which the store could not read ({exc}), "
            f"so there is nothing there to point a reviewer at"
        ) from exc
    origin = landed.metadata.get(plan_store.ORIGIN_KEY)
    unstamped_private = origin is None and plan_store.declared_private(
        plan_store.qualified(project)[0]
    )
    if origin != project and not unstamped_private:
        raise OSError(
            f"{project}'s copy link names {linked}, which the store does not record as "
            f"copied from {project}, so it is not this plan's copy and there is nothing "
            f"there to point a reviewer at"
        )
    matched = stamped_copies(project, sources)
    if len(matched) > 1:
        named = ", ".join(sorted(str(held.qualified_id) for held in matched))
        raise OSError(
            f"source {destination!r} and the sources it routes to hold {len(matched)} "
            f"projects the store records as copied from {project} ({named}), so which one a "
            f"reviewer should open is not this command's to guess"
        )
    return landed


def stamped_copies(project: str, sources: Sequence[str]) -> list[plan_store.StoreProject]:
    """Every project of ``sources`` the store records as copied from ``project``.

    The two-copy check's one read, and the one listing left in this command: the store
    this host adopts has no narrower way to find a second copy no link names than listing
    each source and reading every project's origin stamp. When the adopted onetaskgraph
    answers `project list --origin <SOURCE:ID>`, this becomes that one call per source.
    """
    return [
        held
        for source in sources
        for held in plan_store.read_projects(source)
        if held.metadata.get(plan_store.ORIGIN_KEY) == project
    ]


def _project(qualified: str) -> plan_store.StoreProject:
    """The project ``qualified`` as the store's ``project show`` answers it."""
    held = plan_store.project_record(qualified)
    metadata = held.get("metadata")
    location = held.get("location")
    return plan_store.StoreProject(
        plan_store.QualifiedProjectId(qualified),
        str(held.get("title", "")),
        metadata if isinstance(metadata, Mapping) else {},
        location if isinstance(location, Mapping) else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
