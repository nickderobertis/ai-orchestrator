"""Report where a destination holds the plan a copy landed there, and its design document.

`just finish-plan` copies a cleared plan into the source a person reviews it in, and the
two things they then have to open are the project and the one design document beside it.
Neither address is something this repository can compose: a destination decides its own
native id — a board mints a number where a directory keeps the name — and it decides
where a record lives, so a path or a URL assembled from a project name is one that names
nothing the moment the destination is a board.

So both are **read back out of the destination**. The landed project is found by the
origin stamp the store itself writes onto every record it creates by copying, which is
the only thing that survives a copy to tie the two records together; its location and its
one document's location are then the store's own answers, rendered by the one reading of
them :func:`~orchestrator.plan_store.located` owns.

It is a command of its own rather than a line inside the copy because the copy's product
is the store's per-record report, which reaches the operator's streams whole. This asks a
second question of the destination afterwards — where the records it just wrote are —
and a reader that could not answer it must not turn a landed copy into a failed one.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from orchestrator import design_approval, plan_store

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
    # Every way this cannot answer is driven in `tests/test_plan_locations.py`, and the
    # answer it gives is read back off a real destination in both flow journeys. What no
    # journey can reach is the pairing itself: this runs after a copy the same command
    # just made, so a destination holding no copy of the plan, holding two, or refusing to
    # be read at all is a store that answered that copy and then stopped answering — which
    # a journey would produce by breaking a store between two commands of one script.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    except OSError as exc:
        print(f"plan-locations: {exc}", file=sys.stderr)
        print(
            f"plan-locations: the copy's own per-record report says what landed in "
            f"{args.destination!r}; this could not then say where it holds them",
            file=sys.stderr,
        )
        return UNREADABLE
    print(
        f"plan-locations: {args.destination} holds the plan at "
        f"{plan_store.located(landed.location, str(landed.qualified_id))}"
    )
    print(
        f"plan-locations: {args.destination} holds its design document at "
        f"{design_approval.located(document)}"
    )
    return OK


def copied(project: str, destination: str) -> plan_store.StoreProject:
    """The record in ``destination`` that ``project`` was copied onto.

    Found by :data:`~orchestrator.plan_store.ORIGIN_KEY` rather than by name, because a
    destination names its own records: the store stamps what it copied from, and that
    stamp is the whole of the correspondence. Several matches are refused rather than
    picked between — two copies of one plan into one destination is a state somebody has
    to resolve, and reporting one of them would send a reviewer to whichever this walked
    into first.
    """
    plan_store.qualified(project)
    matched = [
        held
        for held in plan_store.read_projects(destination)
        if held.metadata.get(plan_store.ORIGIN_KEY) == project
    ]
    if not matched:
        raise OSError(
            f"source {destination!r} holds no project the store records as copied from "
            f"{project}, so there is nothing there to point a reviewer at"
        )
    if len(matched) > 1:
        named = ", ".join(str(held.qualified_id) for held in matched)
        raise OSError(
            f"source {destination!r} holds {len(matched)} projects the store records as "
            f"copied from {project} ({named}), so which one a reviewer should open is not "
            f"this command's to guess"
        )
    return matched[0]


if __name__ == "__main__":
    raise SystemExit(main())
