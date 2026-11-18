"""The spikes step's watch endings, against the watch rule.

`scripts/spike-records.sh`'s `run` waits on its spikes run with `watch` and reads some of its
exit statuses as an ending of the wait rather than a failure: every one `AGENTS.md`'s watch
rule names, which `tests/test_watch_surface_drift.py` holds to the installed engine. Its
`case` arm names exactly those, so a status the engine adds or moves fails here before the
step reads it as the watch breaking.
"""

from __future__ import annotations

import re

import pytest
from watch_rule import rule

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

SCRIPT = "scripts/spike-records.sh"

#: The `case` arm of `run`'s wait that reads a watch's exit as an ending of the wait.
WATCH_ENDINGS = re.compile(r"\n\s*((?:\d+ \| )*\d+)\) ;;")


def test_the_spikes_wait_reads_exactly_the_watch_endings_as_endings() -> None:
    text = (REPO_ROOT / SCRIPT).read_text(encoding="utf-8")
    arms = WATCH_ENDINGS.findall(text)
    assert len(arms) == 1, f"{SCRIPT} no longer holds one case arm of watch endings: {arms}"
    script = frozenset(int(code) for code in arms[0].split(" | "))
    documented = frozenset(rule().statuses.values())
    assert script == documented, (
        f"{SCRIPT} reads watch exits {sorted(script)} as endings, while the watch rule "
        f"names {sorted(documented)}; make the case arm name exactly the rule's statuses"
    )
