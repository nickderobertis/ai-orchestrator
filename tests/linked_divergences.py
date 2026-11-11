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
#: because both read identically from here a release cycle later. The adopted engine
#: 0.106.3 links onejudge 0.23.0, which was tagged but never published to PyPI, so no
#: pin can name it; the one published release past it, 0.23.1, builds on onemessagebus
#: 0.11 while that engine links onemessagebus 0.10.0 and `config/onemessagebus.version`
#: names 0.10.0, so this host's verbs keep 0.22.5, the last published release on 0.10.
DECLARED_DIVERGENCES: dict[str, Divergence] = {
    "onejudge": Divergence(
        linked="0.23.0",
        pinned="0.22.5",
        because=(
            "onejudge 0.23.0 is not on PyPI, and 0.23.1 builds on onemessagebus 0.11 while "
            "the engine and config/onemessagebus.version stay on 0.10"
        ),
    ),
}
