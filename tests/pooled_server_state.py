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
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path


def _remaining(state: Path, *, servers_only: bool = False) -> list[int]:
    marker = f"XDG_STATE_HOME={state}/".encode()
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
        if any(value.startswith(marker) for value in environment) and status != "Z":
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


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
