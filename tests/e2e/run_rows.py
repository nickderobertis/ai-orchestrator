"""Reading a `just runs` listing whose ownership column varies by session.

Every row names the session that launched the run — `[mine]`, `[claude-code:3f9a1c2e]`,
or `[unknown]` — and which of those it is depends on the session the suite itself is
running in. A test asserting one literal there would pass for one planner and fail for
the next, so tests whose subject is the *rest* of the row drop the column through here
first. What the column actually says is asserted, per session, in
tests/e2e/test_run_ownership_e2e.py.
"""

from __future__ import annotations

import re

#: The rendered ownership column, in each of the three shapes it can take.
OWNERSHIP_COLUMN = re.compile(r" {2}\[(?:mine|unknown|(?:claude-code|codex):[0-9a-f]{8})\]")


def without_ownership(listing: str) -> str:
    """The listing with each row's well-formed ownership column removed."""
    return OWNERSHIP_COLUMN.sub("", listing)
