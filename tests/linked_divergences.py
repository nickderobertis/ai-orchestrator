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
        linked="0.15.0",
        pinned="0.15.1",
        because=(
            "this host's follow-up verbs run onefollowup 0.13.2, whose quota governor needs "
            "onetaskgraph#4080's request-ended signal, first released in 0.15.1, and the "
            "adopted engine 0.118.0 links 0.15.0, which no dispatch needs past; an onepipeline "
            "release linking onetaskgraph 0.15.1 or later, adopted between runs, retires it"
        ),
    ),
}
