"""The `test-fixtures` plan source, and this process's own root for it.

Every journey here that launches a real run needs a stored project to launch, and
`tests/e2e/project_fixtures.py` writes one into the `test-fixtures` source
`onetaskgraph.yaml` configures. That source was rooted at one fixed host-wide directory,
which every concurrent test process of this repository wrote to and walked at once — Nx
runs `test`, `test-docs`, `test-recipes` and `test-checkouts` as separate processes at
the same time, each with its own xdist workers. A record one of them removed while
another was mid-walk was not a missing project to the source: on the release this host
ran then it refused the read outright, and a publication's own pre-push gate failed
exactly that way, in `test-docs`, on a record the `test-checkouts` process was removing.
The adopted release answers a smaller tree instead, with nothing to say so, which is no
better a thing for a journey to read. A reader/sweeper lock held that to records whose
writing process was gone, and so could not cover the removal that caused it: a *live*
test removing a record it had just written.

So nothing is shared. `tests/conftest.py` gives every test process a temporary root of
its own by exporting the plan store's own environment layer for this source, the way it
already does for `authoring` and `drafts`, and this module is where that variable is
composed and read back. The root the tracked configuration states is a relative,
gitignored directory beside those two — what a checkout resolves when nothing states a
root — and no test writes there, because `root()` refuses rather than falling back to it.
"""

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
