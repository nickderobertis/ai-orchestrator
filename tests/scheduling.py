"""The suite's own scheduling constraints, as a self-contained pytest plugin.

`single_threaded` is honoured by selecting it out of the parallel tier, which a
command line can express. The constraint here cannot be: a journey that starts
several real processes and waits for a readiness handshake between them fails when
*another test of the same kind* is in flight, and nothing inside either one can say
so. The declaration is a marker and the enforcement is the distribution.

Kept out of `conftest.py` so a session can load exactly this with `-p scheduling`
— which is how its own e2e drives a real distributed session — rather than the
whole fixture stack.
"""

from __future__ import annotations

import pytest

#: The marker a journey carries when co-scheduling it with another of its kind
#: measures the host instead of the code. Registered in `pyproject.toml` beside
#: `single_threaded`.
LOAD_SENSITIVE_MARKER = "load_sensitive"
#: One group for the whole family. xdist runs every test of a group on a single
#: worker, so declaring one group is what makes "never two of these at once" a
#: property of the distribution rather than of how busy the box happened to be.
LOAD_SENSITIVE_GROUP = "load-sensitive"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Hand this repository's scheduling declaration to the scheduler.

    The translation lives here rather than at each call site so the suite declares
    *why* a journey is constrained and xdist is told *how*, and neither restates the
    other.

    `tryfirst` is load-bearing, not defensive: xdist reads `xdist_group` in this same
    hook and encodes it into the node id the controller schedules on, so a mark added
    after its implementation ran is a mark the distribution never sees — and the
    family silently spreads back across the workers with everything still green.
    """
    for item in items:
        if item.get_closest_marker(LOAD_SENSITIVE_MARKER) is not None:
            item.add_marker(pytest.mark.xdist_group(LOAD_SENSITIVE_GROUP))
