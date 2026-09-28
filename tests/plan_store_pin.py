"""Which plan-store release this host runs, and the floor its journeys are written to.

`config/onetaskgraph.version` names the standalone CLI this checkout installs and
spawns — the program `just copy-plan`, `just plans` and the follow-up tooling run in. The
engine spawns no such program: `onepipeline` reads a plan and projects its settlements
through the `onetaskgraph-*` crates it links, and `tests/test_linked_libraries.py` holds
this pin to the one release those crates carry, so the two readers are one release.

Declared here so the pin decides what the journeys in
`tests/e2e/test_onetaskgraph_host_e2e.py` assert instead of a reader deciding it: at or
past :data:`PACING_FLOOR` they assert the behaviour, below it the burst and the misreport
that release ended. Neither answer is set anywhere, so a pin that moves in either
direction moves both readers with it.
"""

from __future__ import annotations

from orchestrator.root import REPO_ROOT

#: The plan-store release that carries both behaviours the journeys read this for:
#: reading GitHub's secondary rate limiter as one rather than as a credential problem,
#: and pacing a copy's content-creating mutations instead of sending them as one burst.
#: onetaskgraph https://github.com/nickderobertis/onetaskgraph/pull/173.
PACING_FLOOR = "0.2.18"


def adopted_release() -> str:
    """The plan-store release `config/onetaskgraph.version` names."""
    return (REPO_ROOT / "config" / "onetaskgraph.version").read_text(encoding="utf-8").strip()


def _ordered(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def held_below_the_pacing_floor() -> bool:
    """Whether this host is on a plan-store release below :data:`PACING_FLOOR`.

    A comparison rather than a flag somebody sets: a flag left behind after a bump
    would keep every reader of it asserting an absence that is no longer there, which
    is exactly the shape of staleness this repository gates prose against.
    """
    return _ordered(adopted_release()) < _ordered(PACING_FLOOR)
