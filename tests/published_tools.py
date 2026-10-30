"""The published tools this repository pins, shared by every check that reads them.

`scripts/session-setup.sh` carries the same table as `PUBLISHED_TOOL_SPECS` and
`NPM_TOOL_SPECS`; the drift gate in `tests/test_published_tools.py` holds the two
together, so a tool added here without being wired into session setup fails rather than
going unprovisioned.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Literal, NamedTuple

import pytest

from orchestrator import npm_installs
from orchestrator.root import REPO_ROOT


class PublishedTool(NamedTuple):
    """One adopted published tool: where its release is declared, and what carries it.

    ``registry`` says which package registry this host installs ``distribution`` from:
    PyPI through `uv.lock` into `.venv/bin`, or npmjs.com through `bun.lock` into
    `node_modules/.bin`. The engine and the read API are the npm ones.

    ``npm_package`` is set only for a further npm artifact of the same release.
    `onepipeline-ui` ships the read API and its browser bundle as two packages of one
    release, and `just dag-ui` serves the second against the first, so a pin that moved
    on one side alone would serve a bundle against an API of another release.
    """

    version_file: str
    distribution: str
    binary: str
    npm_package: str | None = None
    registry: Literal["pypi", "npm"] = "pypi"

    def executable(self, checkout: Path = REPO_ROOT) -> Path:
        """Where `checkout`'s own provisioning installs this tool's CLI."""
        if self.registry == "npm":
            return checkout / "node_modules" / ".bin" / self.binary
        return checkout / ".venv" / "bin" / self.binary

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
#: `onepipeline-api-cli` the npm package carrying its read API.
PUBLISHED_TOOLS = (
    PublishedTool("oneagentgraph.version", "oneagentgraph-cli", "oneagentgraph"),
    PublishedTool("onevcs.version", "onevcs-cli", "onevcs"),
    PublishedTool("onepipeline.version", "onepipeline-cli", "onepipeline", registry="npm"),
    PublishedTool(
        "onepipeline-ui.version",
        "onepipeline-api-cli",
        "onepipeline-api",
        "onepipeline-ui",
        registry="npm",
    ),
    PublishedTool("onemessagebus.version", "onemessagebus-cli", "onemessagebus"),
    PublishedTool("onetaskgraph.version", "onetaskgraph-cli", "onetaskgraph"),
    PublishedTool("onebudgetspec.version", "onebudgetspec-cli", "onebudgetspec"),
)
#: onejudge and oneharness are pinned here too, but each is read and verified by its
#: own named function — onejudge's check also proves the `onejudge_sdk` import — so
#: they stay outside the uniform table. Named so a caller enumerating
#: `config/*.version` can say which files `PUBLISHED_TOOLS` is deliberately silent about.
SEPARATELY_GATED_VERSION_FILES = frozenset({"onejudge.version", "oneharness.version"})
#: The two halves of that table: what `uv.lock` installs, and what `bun.lock` does.
PYPI_TOOLS = tuple(tool for tool in PUBLISHED_TOOLS if tool.registry == "pypi")
NPM_TOOLS = tuple(tool for tool in PUBLISHED_TOOLS if tool.registry == "npm")
#: The engine every recipe runs, at the path this checkout's `bun.lock` installs it.
ENGINE_BIN = REPO_ROOT / "node_modules" / ".bin" / "onepipeline"

#: The search path this checkout's locked installs lead: `uv.lock`'s `.venv/bin`, then the
#: directory of the engine binary `bun.lock` installs, so a bare `onepipeline` or
#: `onetaskgraph` a command runs is the pinned release rather than one a caller's PATH
#: offers. Prepended to a caller's own PATH, never standing in for it.
LOCKED_INSTALLS_PATH = os.pathsep.join(
    (str(REPO_ROOT / ".venv" / "bin"), str(npm_installs.ENGINE.parent))
)


@cache
def node_interpreter() -> Path:
    """The `node` executable this suite's own search path resolves, symlinks and shims followed.

    Asked of node itself, so an asdf shim or a symlink answers with the interpreter it runs.
    """
    found = shutil.which("node")
    if found is None:
        pytest.fail(
            "no `node` is on this suite's PATH, and the engine and read API commands "
            "`bun.lock` installs are launchers node runs"
        )
    answered = subprocess.run(  # noqa: S603 - the node the suite's own PATH resolves
        [found, "-p", "process.execPath"], capture_output=True, text=True, check=True
    )
    return Path(answered.stdout.strip())


def interpreter_directory(root: Path) -> Path:
    """A directory under ``root`` holding the `node` interpreter and nothing else.

    `node_modules/.bin/onepipeline` and `onepipeline-api` name `node` on their
    `#!/usr/bin/env node` line, then replace themselves with the platform binary, so a
    journey that narrows PATH to leave a tool out keeps this directory on it — never the
    directory node is installed in, which holds other tools such a journey may be leaving
    out.
    """
    directory = root / "interpreter"
    directory.mkdir(parents=True, exist_ok=True)
    node = directory / "node"
    if not node.exists():
        node.symlink_to(node_interpreter())
    return directory


#: Where this checkout's own provisioning installs the standalone `onetaskgraph` CLI,
#: and the one place the suite reads it from. It is per checkout rather than a
#: directory the whole host shares — `scripts/session-setup.sh` says why — so a test
#: that read a shared path would be answering about whichever checkout provisioned
#: last rather than about this one.
ONETASKGRAPH_BIN = REPO_ROOT / ".venv" / "bin" / "onetaskgraph"
