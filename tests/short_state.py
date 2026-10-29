"""Allocate per-journey state homes within one short-lived base per test process."""

from __future__ import annotations

import itertools
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pooled_server_state
import pytest

from orchestrator.gate_impact import register_scratch

#: The platform's cap on a Unix socket address (`sun_path`, its terminating NUL included).
#: The kernel's rather than oneharness's, so it is held to the kernel:
#: `tests/test_short_state_budget.py` binds a real socket either side of it.
UNIX_SOCKET_ADDRESS_BYTES = 108

#: What oneharness puts between a state root and that socket at its own defaults —
#: `--session-dir` under `<XDG_STATE_HOME>/oneharness/sessions`, the socket in its
#: `control/` directory. Its one source is the installed CLI, which
#: `tests/test_short_state_budget.py` reads both halves back out of.
SOCKET_LAYOUT = "/oneharness/sessions/control/"

#: What this host reserves under that layout for a session name and its `.sock` suffix.
#: A decision rather than a copy of anybody's value — nothing published states a maximum
#: session name — and `tests/test_short_state_budget.py` is what keeps it a sufficient
#: one, against the longest name the graphs and members this repository ships compose.
RESERVED_FOR_A_SESSION = 55

#: What is left for a state root, which is what `state_home` refuses to spend past. The
#: byte taken off the top is the terminating NUL the limit above counts, so an address of
#: exactly `UNIX_SOCKET_ADDRESS_BYTES` characters is one byte too long rather than the
#: longest that fits.
BUDGET = UNIX_SOCKET_ADDRESS_BYTES - 1 - len(SOCKET_LAYOUT) - RESERVED_FOR_A_SESSION

#: How the base is named. Short on purpose — every byte here is a byte a session name
#: cannot have — and recognisable enough that a leftover directory says whose it was.
BASE_PREFIX = "aio-"

#: Where the base is minted. Stated rather than left to `tempfile`'s own choice, because
#: that one follows `TMPDIR`, which a caller of this suite may have pointed at a directory
#: inside a checkout — and a root the budget below then refuses would fail every launched
#: journey rather than shortening anything.
PARENT = Path("/tmp")

_base: Path | None = None
_homes: dict[Path, Path] = {}
_minted = itertools.count()


@pytest.fixture(scope="session", autouse=True)
def short_state_base() -> Iterator[Path]:
    """Create this test process's short state base, and remove it when the process ends.

    Autouse and session-scoped because the callers are of every scope: a module-scoped
    fixture that spends a launch needs it as much as a function-scoped test does, and a
    fixture narrower than the widest caller could not be requested by that caller at all.
    """
    global _base
    _base = Path(tempfile.mkdtemp(prefix=BASE_PREFIX, dir=PARENT))
    # Outside the temporary root a gate's recorder hands this process, since a socket
    # address leaves no room to nest it there, so it is registered with that recorder:
    # counted as the gate's scratch, and removed by it once counted.
    register_scratch(_base)
    previous = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = str(state_home(_base))
    try:
        yield _base
    finally:
        try:
            pooled_server_state.release(_base)
        finally:
            if previous is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = previous
            _homes.clear()
            _base = None


def state_home(owner: Path) -> Path:
    """The short state root belonging to ``owner``, created on first ask.

    ``owner`` is whatever directory the caller would otherwise have put a `state`
    directory under — its `tmp_path`, its world's root, its scratch — and one owner is
    answered with one directory however often it asks, because a journey that builds its
    environment twice is continuing one run and a second store would lose its sessions.
    """
    if _base is None:
        raise AssertionError(
            "short_state.state_home was called with no session base; `short_state_base` "
            "in tests/short_state.py is autouse for every test process and creates it"
        )
    resolved = Path(owner).resolve()
    known = _homes.get(resolved)
    if known is None:
        known = _base / str(next(_minted))
        known.mkdir()
        _homes[resolved] = known
    if len(str(known)) > BUDGET:
        raise AssertionError(
            f"{known} is {len(str(known))} bytes where a state root may spend {BUDGET}: "
            f"oneharness adds {SOCKET_LAYOUT!r} and this host reserves "
            f"{RESERVED_FOR_A_SESSION} bytes for the session name under it, so anything "
            f"over turns a controlled turn into one refused for its address and re-taken "
            f"without control"
        )
    return known


#: The variables a process reads for its temporary directory.
TEMP_VARIABLES = ("TMPDIR", "TMP", "TEMP")


@pytest.fixture(scope="module")
def module_tmpdir() -> Iterator[Path]:
    """A temporary directory of this test module's own for every process its tests start,
    removed when the module ends.

    For a module whose recipes leave files in the temporary directory by design — a
    `finish-plan` graph kept for its operator, a detached run's closeout hook its run has
    not reached yet — or because a published tool they run leaves its own there: what
    they leave is the module's, and goes with it. Short, under this process's base, so a
    socket a recipe opens under it still fits its address. Only the environment changes:
    this process's own `tempfile` keeps the directory it already resolved, which is read
    first so that it is never this one.
    """
    if _base is None:
        raise AssertionError(
            "module_tmpdir was requested with no session base; `short_state_base` in "
            "tests/short_state.py is autouse for every test process and creates it"
        )
    tempfile.gettempdir()
    own = _base / f"tmp{next(_minted)}"
    own.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        for name in TEMP_VARIABLES:
            patch.setenv(name, str(own))
        try:
            yield own
        finally:
            shutil.rmtree(own, ignore_errors=True)


@pytest.fixture(scope="module")
def module_node_scratch(module_tmpdir: Path) -> Iterator[Path]:
    """`ONEPIPELINE_NODE_SCRATCH_DIR` inside `module_tmpdir`, for the processes this
    module's tests start: where the engine's own dispatches put their scratch, which they
    otherwise put in the temporary directory or in the dispatch running this suite."""
    own = module_tmpdir / "node-scratch"
    own.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ONEPIPELINE_NODE_SCRATCH_DIR", str(own))
        yield own
