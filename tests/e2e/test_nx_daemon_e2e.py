"""Running an Nx target must not leave a resident daemon or a private Nx install.

Nx keeps one daemon per *workspace root*, and the daemon primes an "are AI agents
configured?" cache the instant it starts by installing `nx@latest` into a fresh
system-temp directory — ~80 MB, logged as `[LATEST-NX]: Pulling latest Nx...` —
which it removes only on a graceful shutdown. Neither cost is visible from the
command line that pays it.

That made this suite the biggest producer of both. Every journey that copies this
checkout gets its own workspace root, so every journey started its own daemon, and
each daemon stamped another abandoned install into the platform temp root: one
worktree reached 49 resident daemons at 8.9 GB RSS, and the host reached ~110 GB
of abandoned installs in a day and 93% full, whose ENOSPC failures were first
diagnosed as code problems.

These journeys drive the real `scripts/nx.sh` against a real throwaway checkout
and assert on what is actually left behind: no process still rooted at the
workspace, and — for an operator who deliberately turns the daemon back on — no
private install anywhere.

llmlint: ignore-file[e2e_not_mocked] Nothing is faked here: the real wrapper runs
real Nx against a real copy of this checkout, and the assertions read the host's
own process table and temp root.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_workspace import copy_checkout, requires_workspace_install

from orchestrator.coordination import proc_root

# Copying the whole tree is this journey's premise, and the tree includes its
# prose: these belong to the whole-workspace tier by construction.
pytestmark = [requires_workspace_install, pytest.mark.reads_docs]

#: The daemon writes its own log here, under the workspace it is rooted at.
DAEMON_LOG = Path(".nx") / "workspace-data" / "d" / "daemon.log"
PULLED_A_PRIVATE_INSTALL = "[LATEST-NX]: Pulling latest Nx"
USED_THE_INSTALLED_NX = "Using local implementation (NX_USE_LOCAL=true)"
CHECKED_AI_AGENTS = "Agent configuration status computation completed"
#: Long enough for a real `nx@latest` install to land — it took ~70s when measured
#: — so waiting for the daemon's verdict cannot pass by outrunning the pull.
DAEMON_SETTLE_SECONDS = 90.0
#: Nx strips `TMPDIR` from the daemon's environment, so a daemon's private install
#: lands in the platform temp root whatever the caller's own temp directory is.
TEMP_ROOTS = (Path(tempfile.gettempdir()), Path("/tmp"))


def _processes_rooted_at(root: Path) -> list[int]:
    """Return the pids of every live process whose working directory is ``root``."""
    rooted: list[int] = []
    for entry in sorted(proc_root().iterdir()):
        if not entry.name.isdigit():
            continue
        try:
            cwd = (entry / "cwd").resolve()
        except OSError:
            # A process that exited mid-scan, or one owned by another user, proves
            # nothing about this workspace either way.
            continue
        if cwd == root:
            rooted.append(int(entry.name))
    return rooted


def _private_installs_stamped_by(pid: int) -> list[Path]:
    """Return the temp installs naming ``pid`` as their creator (`tmp-<pid>-<random>`)."""
    return sorted(
        {path for temp in TEMP_ROOTS for path in temp.glob(f"tmp-{pid}-*") if path.is_dir()}
    )


@dataclass(frozen=True)
class Checkout:
    """A throwaway copy of this repository driven through the real Nx wrapper."""

    root: Path
    cache: Path

    def run(self, *args: str, **overrides: str) -> None:
        # The session already exports NX_DAEMON=false so that no *test* leaves a
        # daemon behind (tests/conftest.py). Dropping it here is what makes these
        # journeys about the wrapper every `just` recipe goes through, rather than
        # about the belt this suite happens to be wearing.
        ambient = {key: value for key, value in os.environ.items() if key != "NX_DAEMON"}
        result = subprocess.run(
            ["./scripts/nx.sh", *args],
            cwd=self.root,
            env={**ambient, "XDG_CACHE_HOME": str(self.cache), **overrides},
            check=False,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def daemon_verdict(self) -> str:
        """Return the daemon's own log once it has finished its AI-agent check."""
        log = self.root / DAEMON_LOG
        deadline = time.monotonic() + DAEMON_SETTLE_SECONDS
        text = ""
        while time.monotonic() < deadline:
            text = log.read_text(encoding="utf-8") if log.exists() else ""
            if CHECKED_AI_AGENTS in text:
                return text
            time.sleep(0.5)
        raise AssertionError(f"the daemon never finished its AI-agent check; log so far:\n{text}")


@pytest.fixture
def checkout(tmp_path: Path) -> Iterator[Checkout]:
    root = tmp_path / "checkout"
    copy_checkout(root)
    # Nx resolves its workspace and its ignore rules from git, and scripts/nx.sh
    # derives the shared cache key from the repository identity.
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    yield Checkout(root=root, cache=tmp_path / "cache")
    # A daemon a test deliberately started outlives the workspace it watches, so
    # the test that asked for one is what has to take it back down.
    for pid in _processes_rooted_at(root):
        with suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)


def test_running_a_target_leaves_no_daemon_rooted_at_the_workspace(checkout: Checkout) -> None:
    """Repeated wrapper runs must not accumulate a resident daemon per workspace."""
    checkout.run("show", "projects")
    checkout.run("show", "projects")

    # The client waits for its daemon before it uses one, so a daemon this run was
    # going to leave behind is already rooted here by the time the wrapper exits.
    assert _processes_rooted_at(checkout.root) == [], (
        "scripts/nx.sh left a resident Nx daemon watching a throwaway workspace"
    )
    assert not (checkout.root / DAEMON_LOG).exists(), (
        "an Nx daemon started despite the wrapper defaulting it off"
    )


def test_an_explicitly_enabled_daemon_still_pulls_no_private_nx_install(
    checkout: Checkout,
) -> None:
    """Turning the daemon back on is supported; paying ~80 MB of abandoned temp is not."""
    checkout.run("show", "projects", NX_DAEMON="true")

    verdict = checkout.daemon_verdict()
    assert USED_THE_INSTALLED_NX in verdict, verdict
    assert PULLED_A_PRIVATE_INSTALL not in verdict, verdict

    daemons = _processes_rooted_at(checkout.root)
    assert daemons, "NX_DAEMON=true must still start a daemon; the wrapper only defaults it off"
    for pid in daemons:
        assert _private_installs_stamped_by(pid) == [], (
            f"the daemon at pid {pid} left a private nx install in the system temp root"
        )
