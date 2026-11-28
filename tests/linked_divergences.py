"""The pins this host deliberately holds away from the release the adopted engine links.

Its own module, rather than a constant of `tests/test_linked_libraries.py`, because a
journey that must know whether it drives what a dispatch runs needs this declaration and
nothing else that gate reaches. `tests/test_linked_libraries.py` is the gate that holds
every pin to the engine's SBOM or to an entry here.
"""

from __future__ import annotations

from typing import NamedTuple


class Divergence(NamedTuple):
    """One pin this host deliberately holds away from what the engine linked.

    Both versions are named, so the declaration goes stale the moment either moves:
    a divergence inherited across a bump is exactly the state these gates exist to
    end, and one that only named a reason would be inherited silently.
    """

    #: What the adopted engine resolved.
    linked: str
    #: What `config/<crate>.version` names instead.
    pinned: str
    #: Why the two cannot be the same release today.
    because: str


#: The pins that may not be reconciled today, each with the measured pair it was
#: declared against. A divergence is permitted where the linked release is not
#: installable from PyPI at all, or where this host's own verbs must run a later release
#: than any engine links — never as a convenience, and never as "not yet looked at",
#: because both read identically from here a release cycle later. A linked crate family
#: is declared under its pin's name, as `tests/test_linked_libraries.py`'s
#: `RECONCILED_FAMILIES` keys it.
DECLARED_DIVERGENCES: dict[str, Divergence] = {
    "onetaskgraph": Divergence(
        linked="0.16.0",
        pinned="0.17.3",
        because=(
            "this host's follow-up verbs run onefollowup 0.15.0, whose prepare counts and "
            "spends only its own duplicate-search points on the real board, and which links "
            "onetaskgraph 0.17.3, the release whose find asks the board for less; the host's "
            "own plan-store commands need that leaner find and 0.17.2's nested Linear "
            "sections, which carry a design document's collapsed Before block inside its "
            "visual change; the adopted engine 0.121.0 links 0.16.0, which already carries the "
            "in-review status and the Linear details-block conversion every dispatch needs; an "
            "onepipeline release linking onetaskgraph 0.17.3 or later, adopted between runs, "
            "retires it"
        ),
    ),
}
