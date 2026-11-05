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

A claimed session also empties each passed test's `tmp_path` when the test ends, through
`release_passed`, rather than holding every test's copies and installs until the session
does, which is what the `gate-peak-temp-disk` budget in `budgets.yaml` measures. It keeps
the emptied directory, because pytest's `tmp_path_retention_policy` would delete it and
then number the next test of that name into the same path, and `short_state`'s roots and
other state keyed on a test's path would answer that new test with the old one's. A
failed test's files stay until the session ends, for its failure to be read.
`tests/test_pytest_temp_root.py` drives two concurrent sessions shaped like a target
through a pruning of the shared root and a session given a base through its end, and
`tests/tree_scans/test_nx_cache_scope.py` holds every target's command to reaching this.
"""

from __future__ import annotations

import contextlib
import getpass
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

#: The variable pytest reads for the directory its per-user root is made in.
TEMPROOT_ENV = "PYTEST_DEBUG_TEMPROOT"
#: How a session's root is named, so a leftover directory says whose it was.
ROOT_PREFIX = "aio-pytest-"
#: The base whose tests are emptied as they pass, inherited by this session's `xdist`
#: workers; unset in a session given `--basetemp`, whose base is whoever named it's.
RELEASED_BASE_ENV = "AIO_PYTEST_RELEASED_BASE"
#: The base inside the root. Short, because a test's own paths under it bound socket
#: addresses (`tests/short_state.py`).
BASE_NAME = "t"

#: Set on a test that failed in its setup or call, whose files are left to be read.
FAILED = pytest.StashKey[bool]()

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
    if hasattr(config, "workerinput"):
        return None
    if config.option.basetemp:
        os.environ.pop(RELEASED_BASE_ENV, None)
        return None
    root = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX, dir=os.environ.get(TEMPROOT_ENV)))
    config.option.basetemp = str(root / BASE_NAME)
    os.environ[TEMPROOT_ENV] = str(root)
    os.environ[RELEASED_BASE_ENV] = config.option.basetemp
    _claimed = root
    return root


def release_passed(item: pytest.Item) -> None:
    """Empty the `tmp_path` of a test that has torn down without failing; keep the directory."""
    tmp_path = (getattr(item, "funcargs", None) or {}).get("tmp_path")
    base = os.environ.get(RELEASED_BASE_ENV)
    if (
        base is None
        or not isinstance(tmp_path, Path)
        or item.stash.get(FAILED, False)
        or not tmp_path.is_relative_to(base)
    ):
        return
    # What cannot be removed, or a directory the test removed itself, is the session's
    # `release` to clear; neither is a verdict on the test.
    with contextlib.suppress(OSError):
        for child in tmp_path.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


@contextlib.contextmanager
def held_until_scope_ends(session: pytest.Session, path: Path) -> Iterator[None]:
    """Remove a wider-scoped fixture's `path` when its scope ends, unless a test failed in it.

    For a fixture too large to hold through the rest of the session once its tests are done.
    """
    failed_before = session.testsfailed
    yield
    if session.testsfailed == failed_before:
        shutil.rmtree(path, ignore_errors=True)


def release() -> None:
    """Remove the root this process claimed, once the session it served has ended."""
    global _claimed  # noqa: PLW0603 - see `claim`
    if _claimed is not None:
        shutil.rmtree(_claimed, ignore_errors=True)
        _claimed = None
