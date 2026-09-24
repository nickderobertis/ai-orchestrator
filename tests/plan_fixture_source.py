"""Read this process's isolated `test-fixtures` source root and its tracked default."""

from __future__ import annotations

import functools
import os
from pathlib import Path

from plan_sources import read_plan_sources

from orchestrator.root import REPO_ROOT

#: The source `onetaskgraph.yaml` configures for this suite's own launchable projects.
#: Referenced nowhere outside `tests/` and that file, which is why no launch helper
#: composes the variable below and this module does.
SOURCE = "test-fixtures"

#: The document that configures it, read rather than restated by `tracked_root` below.
CONFIGURATION = REPO_ROOT / "onetaskgraph.yaml"

#: The plan store's environment layer for that source's root. Derived from the one name
#: above rather than spelled, because the store derives it from the source name the same
#: way: a hand-spelled second copy could name a source this checkout does not configure.
ROOT_VARIABLE = f"ONETASKGRAPH_SOURCES__{SOURCE.upper().replace('-', '_')}__CONFIG__ROOT"


def root() -> Path:
    """This process's own root for the `test-fixtures` source.

    Refused rather than defaulted when the variable names nothing: falling back to the
    tracked root would put this process's records where every other process on the host
    reads them, which is the sharing this module exists to end, and it would do it
    silently — a green suite over a store somebody else was mid-walk in.
    """
    stated = os.environ.get(ROOT_VARIABLE)
    if not stated:
        raise AssertionError(
            f"{ROOT_VARIABLE} names no root, so this process has no {SOURCE!r} store of "
            f"its own; `_isolated_plan_store_roots` in tests/conftest.py exports it for "
            f"every test process, and nothing here falls back to the tracked root"
        )
    return Path(stated)


@functools.cache
def tracked_root() -> Path:
    """The root `onetaskgraph.yaml` states for this source, read out of that file.

    Read rather than spelled here, because the file is where that root is decided and a
    copy of it is a copy that goes stale: what reads this is a check that a test process
    resolved a root of its *own*, and one comparing against a stale copy would pass
    against exactly the tracked root it was meant to catch.
    """
    configured = read_plan_sources(CONFIGURATION.read_text(encoding="utf-8"))[SOURCE]
    stated = configured.root
    if stated is None:
        raise AssertionError(
            f"{CONFIGURATION} configures the {SOURCE!r} source with no root, so this suite "
            f"has no store to isolate; it is a local-md source and needs one"
        )
    return REPO_ROOT / stated
