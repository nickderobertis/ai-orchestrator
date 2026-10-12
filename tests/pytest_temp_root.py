"""A temporary root of its own for every pytest session this suite's targets start.

pytest's default root is shared: with no `--basetemp`, every session of one user on the
host numbers its base under `<tmp>/pytest-of-<user>/pytest-<N>` and prunes the numbered
directories past its retention count when it starts. The gate runs several of this
suite's targets at once, and other checkouts' gates and dispatches run theirs beside them,
so a long tier's live base sat in a directory every other session on the host considered
its own to clean, and its tests failed on `FileNotFoundError` for paths deleted under them.

`claim()` is the answer, run by `tests/conftest.py` before pytest's own temporary-path
factory reads its options: the controlling session mints a directory of its own with
`tempfile.mkdtemp` — a fresh, unpredictable name outside `pytest-of-<user>`, which no
session's numbered pruning walks — and takes its base inside it. An `xdist` worker is
handed its base under the controller's by `xdist` itself, so a worker claims nothing. The
root is also exported as `PYTEST_DEBUG_TEMPROOT`, the variable pytest reads for where its
per-user root lives, so a pytest a test starts that does not load this suite's conftest
numbers its base under this session's root rather than the host's; one that does load it
mints its own root inside this one. `release()` removes the root when the session ends.

A session given `--basetemp` explicitly keeps it: whoever named a base chose it.
`tests/test_pytest_temp_root.py` drives two concurrent sessions shaped like a target
through a pruning of the shared root and a session given a base through its end, and
`tests/tree_scans/test_nx_cache_scope.py` holds every target's command to reaching this.
"""

from __future__ import annotations

import getpass
import os
import shutil
import tempfile
from pathlib import Path

import pytest

#: The variable pytest reads for the directory its per-user root is made in.
TEMPROOT_ENV = "PYTEST_DEBUG_TEMPROOT"
#: How a session's root is named, so a leftover directory says whose it was.
ROOT_PREFIX = "aio-pytest-"
#: The base inside the root. Short, because a test's own paths under it bound socket
#: addresses (`tests/short_state.py`).
BASE_NAME = "t"

_claimed: Path | None = None


def shared_root() -> Path:
    """The per-user root a session with no base of its own would number under."""
    return Path(tempfile.gettempdir()).resolve() / f"pytest-of-{getpass.getuser()}"


def claim(config: pytest.Config) -> Path | None:
    """Give this session a root of its own and its base inside it; return the root.

    Nothing for an `xdist` worker, whose base the controller hands it, or for a session
    given `--basetemp`.
    """
    global _claimed  # noqa: PLW0603 - one root per process, removed by `release`
    if hasattr(config, "workerinput") or config.option.basetemp:
        return None
    root = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX, dir=os.environ.get(TEMPROOT_ENV)))
    config.option.basetemp = str(root / BASE_NAME)
    os.environ[TEMPROOT_ENV] = str(root)
    _claimed = root
    return root


def release() -> None:
    """Remove the root this process claimed, once the session it served has ended."""
    global _claimed  # noqa: PLW0603 - see `claim`
    if _claimed is not None:
        shutil.rmtree(_claimed, ignore_errors=True)
        _claimed = None
