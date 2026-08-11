"""The published tools this repository pins, shared by every check that reads them.

`scripts/session-setup.sh` carries the same table as `PINNED_TOOL_SPECS`; the drift
gate in `tests/test_pinned_tools.py` holds the two together, so a tool added here
without being wired into session setup fails rather than going unprovisioned.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest

from orchestrator import REPO_ROOT


class PinnedTool(NamedTuple):
    """One adopted published tool: where its release is declared, and what carries it."""

    version_file: str
    distribution: str
    binary: str

    @property
    def adopted_version(self) -> str:
        """Read the adopted release, rejecting anything but one semantic version."""
        path = REPO_ROOT / "config" / self.version_file
        if not path.is_file():
            pytest.fail(f"config/{self.version_file} must declare the adopted release")
        adopted = path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", adopted) is None:
            pytest.fail(
                f"config/{self.version_file} must contain one semantic version, got {adopted!r}"
            )
        return adopted


#: Every published tool this repository adopts beyond onejudge and oneharness, which
#: predate this table and keep their own drift gates. `onepipeline-ui` is the tool's
#: name and `onepipeline-api-cli` the distribution PyPI carries it under.
PINNED_TOOLS = (
    PinnedTool("oneagentgraph.version", "oneagentgraph-cli", "oneagentgraph"),
    PinnedTool("onevcs.version", "onevcs-cli", "onevcs"),
    PinnedTool("onepipeline.version", "onepipeline-cli", "onepipeline"),
    PinnedTool("onepipeline-ui.version", "onepipeline-api-cli", "onepipeline-api"),
)
#: The two adopted releases that predate `PINNED_TOOLS`, so a caller enumerating
#: `config/*.version` can say which files this table is deliberately silent about.
LEGACY_VERSION_FILES = frozenset({"onejudge.version", "oneharness.version"})
