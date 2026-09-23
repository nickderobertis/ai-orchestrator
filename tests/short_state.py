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
second delay landed. Its own private short root, and `tests/e2e/test_monitor_cursor_e2e.py`'s,
are what this replaces.

So the root is minted directly under the system temporary directory, short enough that the
longest session name this host composes still fits with room over — `BUDGET` states that
arithmetic and `state_home` refuses a root past it, rather than leaving the next caller to
rediscover the limit from a refusal buried in a dispatch's log. The session-scoped fixture
below owns the whole of it: one base per test process, removed with everything under it
when that process ends. Each owner gets a directory of its own under that base, because two
journeys sharing one state root read each other's oneharness sessions and history.
"""

from __future__ import annotations

import itertools
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

#: Linux's cap on a Unix socket address (`sun_path`), which oneharness reports by name
#: when it refuses one. One byte is left to the terminating NUL.
UNIX_SOCKET_ADDRESS_BYTES = 108

#: What oneharness puts between a state root and the socket, at its own defaults.
SOCKET_LAYOUT = "/oneharness/sessions/control/"

#: The longest session name this host composes, by its shape rather than by a measurement
#: that would go stale: `<graph>-<millis>-<pid>-<member>-<side>`, where the graph is at
#: most `node-scope`, the member at most `pr-author`, and the side `skill` or `user`.
LONGEST_SESSION_NAME = len("node-scope") + 1 + 13 + 1 + 7 + 1 + len("pr-author") + 1 + len("skill")

#: What a state root may spend, so that the longest address above still fits.
BUDGET = UNIX_SOCKET_ADDRESS_BYTES - 1 - len(SOCKET_LAYOUT) - LONGEST_SESSION_NAME - len(".sock")

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
            f"oneharness adds {SOCKET_LAYOUT!r} and up to {LONGEST_SESSION_NAME} bytes of "
            f"session name, and the {UNIX_SOCKET_ADDRESS_BYTES}-byte socket-address limit "
            f"turns the excess into a controlled turn refused and re-taken without control"
        )
    return known
