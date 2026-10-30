# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The task requires
# waiting through the documented oneharness 0.25.10 lifecycle (oneharness#1520), whose
# 60 s linger and 10 s stop deadline have no public query interface. This bounded wait
# is the consumer timeout, with margin; the session journey proves actual cleanup.
"""Own the shared Codex servers a pytest session starts, through their documented linger.

oneharness 0.25.10 ends a server after its last lease and a 60-second linger, within
its 10-second stop deadline (oneharness#1520). Consumers have no public shortening
knob. A private XDG state home keeps these servers apart from the host's dispatches;
release waits for the servers to exit, removes their entries, then waits for the
sweepers to observe removal and exit. It never signals a process.

A server is owned by the test that started it, so `snapshot` and `await_started` let a
test's own teardown wait for every server it started under a session base, and for each
process carrying that server's state home, before the next test or the session's own
release runs.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple


def _remaining(state: Path, *, servers_only: bool = False) -> list[int]:
    marker = f"XDG_STATE_HOME={state}/".encode()
    # oneharness 0.26 starts a shared server under a scrubbed environment that carries no
    # `XDG_STATE_HOME`, so a server is also known by the socket it listens on under `state`.
    listening = f"unix://{state}/".encode()
    remaining: list[int] = []
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            environment = (process / "environ").read_bytes().split(b"\0")
            status = (process / "stat").read_text().rsplit(")", 1)[1].split()[0]
            command = (process / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if servers_only and not (b"app-server" in command and b"--listen" in command):
            continue
        owned = any(value.startswith(marker) for value in environment) or any(
            word.startswith(listening) for word in command
        )
        if owned and status != "Z":
            remaining.append(int(process.name))
    return remaining


def _describe(pid: int) -> str:
    """Identify a survivor while it exists; reading it never changes its lifecycle."""
    process = Path("/proc") / str(pid)
    try:
        executable = (process / "exe").readlink()
        command = (process / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        stdin = (process / "fd/0").readlink()
        ancestors: list[str] = []
        parent = int((process / "stat").read_text().rsplit(")", 1)[1].split()[1])
        while parent and len(ancestors) < 8:
            ancestor = Path("/proc") / str(parent)
            ancestors.append(f"{parent}:{(ancestor / 'comm').read_text().strip()}")
            parent = int((ancestor / "stat").read_text().rsplit(")", 1)[1].split()[1])
    except OSError as error:
        return f"{pid}: unavailable ({error})"
    return (
        f"{pid}: executable={executable}; command={command}; "
        f"parent chain={ancestors}; stdin={stdin}; observed={datetime.now(UTC).isoformat()}"
    )


def _wait(state: Path, seconds: int, *, servers_only: bool) -> None:
    deadline = time.monotonic() + seconds
    while remaining := _remaining(state, servers_only=servers_only):
        if time.monotonic() >= deadline:
            details = "\n".join(_describe(pid) for pid in remaining)
            raise RuntimeError(
                f"processes still using test state {state} after {seconds}s:\n{details}"
            )
        time.sleep(0.2)


def release(state: Path) -> None:
    """Let servers stop, remove their entries, then let their sweepers observe removal."""
    _wait(state, 75, servers_only=True)  # 60 s linger + 10 s stop + scheduling margin.
    shutil.rmtree(state)
    # run_sweeper in oneharness v0.25.10 checks its entry every 2 s and returns
    # when the leases directory is gone. Entry removal is its supported shutdown.
    _wait(state, 10, servers_only=False)


#: Within a state home, where oneharness keeps a shared server's socket.
SOCKETS = "*/oneharness/servers/*/sock/app-server.sock"


class Owner(NamedTuple):
    """A live process under a session base, and the state home it belongs to."""

    pid: int
    home: Path
    server: bool


def _home_under(base: Path, path: str) -> Path | None:
    """The state home directly under `base` that `path` lies in, if any."""
    relative = Path(path).relative_to(base) if Path(path).is_relative_to(base) else None
    return base / relative.parts[0] if relative is not None and relative.parts else None


def snapshot(base: Path) -> set[Owner]:
    """Every live process under `base`: a server by its socket, any other by its state home.

    Free when no server socket exists under `base`, which is most tests' case, because
    only a test starting a server pays for reading the process table.
    """
    if not any(base.glob(SOCKETS)):
        return set()
    listening = f"unix://{base}/"
    marker = b"XDG_STATE_HOME="
    owners: set[Owner] = set()
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            command = (process / "cmdline").read_bytes().decode(errors="replace").split("\0")
            status = (process / "stat").read_text().rsplit(")", 1)[1].split()[0]
            environment = (process / "environ").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if status == "Z":
            continue
        socket = next((word for word in command if word.startswith(listening)), None)
        if socket is not None and "app-server" in command:
            home = _home_under(base, socket.removeprefix("unix://"))
            if home is not None:
                owners.add(Owner(int(process.name), home, server=True))
            continue
        for value in environment:
            if value.startswith(marker):
                home = _home_under(base, value.removeprefix(marker).decode(errors="replace"))
                if home is not None:
                    owners.add(Owner(int(process.name), home, server=False))
    return owners


def await_started(base: Path, before: set[Owner], seconds: int = 75) -> None:
    """Wait for each server started since `before`, and every process of its home, to exit.

    The bound is the documented linger and stop deadline with margin, as `release`'s is;
    a survivor past it is named, never signalled.
    """
    deadline = time.monotonic() + seconds
    homes: set[Path] = set()
    while True:
        started = snapshot(base) - before
        homes |= {owner.home for owner in started if owner.server}
        remaining = sorted(owner.pid for owner in started if owner.home in homes)
        if not remaining:
            return
        if time.monotonic() >= deadline:
            details = "\n".join(_describe(pid) for pid in remaining)
            raise RuntimeError(
                f"processes a test started under {sorted(map(str, homes))} still ran "
                f"{seconds}s after it ended:\n{details}"
            )
        time.sleep(0.2)


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
