"""The half of `tests/test_onemessagebus_config.py` that reads this repository's prose.

It sits in the `manager-guidance` project.

These hold the monitor's grants to the allowlist `AGENTS.md` states. That project's key
names each file they read, and `tests/conftest.py` holds them to it; the tests of that
module that read no prose stay there, in `orchestrator:test`.
"""

from __future__ import annotations

import re

from test_onemessagebus_config import _value

from orchestrator.root import REPO_ROOT


def test_the_monitors_grants_are_the_allowlist_agents_md_states() -> None:
    """Exactly the ops `AGENTS.md` names for the monitor's edit author, and no others."""
    agents = " ".join((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    stated = re.search(
        r"allowlist its edit author is bounded to — ((?:`\w+`(?:, | and )?)+)", agents
    )
    assert stated is not None, "AGENTS.md no longer names the monitor's allowlist"
    named = re.findall(r"`(\w+)`", stated.group(1))
    granted = _value(r"^authors:\n  monitor:\n    capabilities: \[([^\]]*)\]$")
    assert [op.strip() for op in granted.split(",")] == named
