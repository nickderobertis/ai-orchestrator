"""Reading a `just runs` listing whose ownership column varies by session.

Every row names the session that launched the run — `[mine]`, `[claude-code:3f9a1c2e]`,
or `[unknown]` — and which of those it is depends on the session the suite itself is
running in. A test asserting one literal there would pass for one planner and fail for
the next, so tests whose subject is the *rest* of the row drop the column through here
first. What the column actually says is asserted, per session, in
tests/e2e/test_run_ownership_e2e.py.

The shapes it can take are derived from `orchestrator.launch` rather than restated:
the launcher vocabulary and the fingerprint's width are that module's to define, and a
copy here would keep matching a column production had stopped rendering.
"""

from __future__ import annotations

import re

from orchestrator.launch import KNOWN_LAUNCHERS, session_fingerprint

#: One rendered label: the caller's own run, a named session, or an unattributed run.
_LAUNCHERS = "|".join(sorted(re.escape(launcher) for launcher in KNOWN_LAUNCHERS))
_FINGERPRINT = f"[0-9a-f]{{{len(session_fingerprint('sample'))}}}"

#: The rendered ownership column, in each of the three shapes it can take.
OWNERSHIP_COLUMN = re.compile(rf" {{2}}\[(?:mine|unknown|(?:{_LAUNCHERS}):{_FINGERPRINT})\]")


def without_ownership(listing: str) -> str:
    """The listing with each row's well-formed ownership column removed."""
    return OWNERSHIP_COLUMN.sub("", listing)
