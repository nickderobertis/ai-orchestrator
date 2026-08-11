"""The published tools this repository pins, shared by every check that reads them.

`scripts/session-setup.sh` carries the same table as `PUBLISHED_TOOL_SPECS`; the drift
gate in `tests/test_published_tools.py` holds the two together, so a tool added here
without being wired into session setup fails rather than going unprovisioned.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest

from orchestrator import REPO_ROOT


class PublishedTool(NamedTuple):
    """One adopted published tool: where its release is declared, and what carries it.

    ``npm_package`` is set only for a tool this repository also installs from npm.
    `onepipeline-ui` ships as two artifacts of one release — the read API as a wheel
    and the browser bundle as an npm package — and `just dag-ui` serves the second
    against the first, so a pin that moved on one side alone would serve a bundle
    against an API of another release.
    """

    version_file: str
    distribution: str
    binary: str
    npm_package: str | None = None

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


#: The published implementations this repository is a configuration layer over, each
#: verified uniformly from this one table. `onepipeline-ui` is the tool's name and
#: `onepipeline-api-cli` the distribution PyPI carries it under.
PUBLISHED_TOOLS = (
    PublishedTool("oneagentgraph.version", "oneagentgraph-cli", "oneagentgraph"),
    PublishedTool("onevcs.version", "onevcs-cli", "onevcs"),
    PublishedTool("onepipeline.version", "onepipeline-cli", "onepipeline"),
    PublishedTool(
        "onepipeline-ui.version", "onepipeline-api-cli", "onepipeline-api", "onepipeline-ui"
    ),
)
#: onejudge and oneharness are pinned here too, but each is read and verified by its
#: own named function — onejudge's check also proves the `onejudge_sdk` import — so
#: they stay outside the uniform table. Named so a caller enumerating
#: `config/*.version` can say which files `PUBLISHED_TOOLS` is deliberately silent about.
SEPARATELY_GATED_VERSION_FILES = frozenset({"onejudge.version", "oneharness.version"})
