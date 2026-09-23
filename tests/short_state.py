"""One short `XDG_STATE_HOME` per launched journey, so its controlled turns stay controlled.

oneharness opens a controlled turn's socket at `<session-dir>/control/<NAME>.sock`, and
`--session-dir` defaults to `<XDG_STATE_HOME>/oneharness/sessions`. Linux caps a Unix
socket address at 108 bytes, so what a journey spends on its state root comes straight
out of what is left for that layout and the session name under it. A journey that put its
root under pytest's own `tmp_path` —
`/tmp/pytest-of-<user>/pytest-<n>/popen-gw<k>/<name><i>/state` — spent about seventy of
those, and every controlled turn it took was refused for the address and re-taken without
control.

Nothing failed, which is why it went unnoticed: the turns ran, twice, so every launched
journey paid double the provider invocations and double the wall clock, and a journey
whose subject is *timing* got wrong arithmetic —
`tests/e2e/test_observer_graph_liveness_e2e.py` holds a worker for a measured number of
seconds, and a worker held twice put the settlement it measures against wherever the
second delay landed. So a journey minting a short root privately is a journey budgeting
this arithmetic on its own: there is one fixture, and it is this one.

So the root is minted directly under the system temporary directory, short enough that the
longest session name this host composes still fits with room over — `BUDGET` states that
arithmetic and `state_home` refuses a root past it, rather than leaving the next caller to
rediscover the limit from a refusal buried in a dispatch's log.
`tests/test_short_state_budget.py` holds each of its three terms to what owns it.

The session-scoped fixture below owns the whole of it: one base per test process, removed
with everything under it when that process ends. Each owner gets a directory of its own
under that base, because two journeys sharing one state root read each other's oneharness
sessions and history.
"""

from __future__ import annotations

import itertools
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

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
RESERVED_FOR_A_SESSION = 54

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
    try:
        yield _base
    finally:
        base, _base = _base, None
        _homes.clear()
        shutil.rmtree(base, ignore_errors=True)


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
