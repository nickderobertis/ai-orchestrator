"""The real `just bootstrap` in a fresh checkout: the one recipe that provisions from nothing.

Session setup is the only thing that installs into a worktree, and `just bootstrap` runs
it before its forced Bun reinstall, its Playwright browser and the Nx `bootstrap` targets.
The justfile exports `UV_NO_SYNC=1` to every recipe, so this journey is what shows that
export suppresses nothing bootstrap needs: the setup's `uv sync` and the
`orchestrator:bootstrap` target's `uv sync --locked` are syncs, which `UV_NO_SYNC` does
not reach, where a `uv run` would have provisioned nothing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_workspace import copy_working_tree
from offline_installs import NO_NETWORK
from waits import until

#: The variable session setup's last step reads its sibling checkout list from, pointed at
#: an empty list so the bootstrap never reaches this host's registered checkouts.
REGISTERED_CHECKOUTS_OVERRIDE = "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS"
#: Every way a caller points uv at an environment, dropped so the recipe provisions the
#: copy's own and the checks below read it.
PROVIDED_ENVIRONMENT = ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV")
#: The line session setup prints when it starts the host sweep as a detached job.
HOST_SWEEP_JOB = re.compile(r"^host-sweep: .*\bstarted (?:again as )?job (\d+)\b", re.MULTILINE)
#: How long that job may take over this journey's sandboxed state.
HOST_SWEEP_SECONDS = 120


@dataclass(frozen=True)
class Bootstrapped:
    """The copy the recipe ran in, what the recipe said, and the environment it ran under."""

    root: Path
    ran: subprocess.CompletedProcess[str]
    env: dict[str, str]

    def run(self, *command: str) -> subprocess.CompletedProcess[str]:
        """Run a read in the copy, under the environment the recipe ran under."""
        return subprocess.run(
            command, cwd=self.root, env=self.env, text=True, capture_output=True, check=False
        )


def _browsers_path() -> str:
    """Where this host keeps Playwright's browsers, stated so the copy reuses them.

    The journey moves `XDG_CACHE_HOME` to keep the Nx and sweep caches its own, and
    Playwright reads its default browser directory from that variable, so without this
    the bootstrap would download a browser this host already has.
    """
    named = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if named:
        return named
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return str(Path(cache) / "ms-playwright")


@pytest.fixture(scope="module")
def bootstrapped(tmp_path_factory: pytest.TempPathFactory) -> Bootstrapped:
    """A copy of this checkout's tracked files, with no toolchain, after a real `just bootstrap`.

    `UV_NO_SYNC=1` is in force for the whole run, exported as every session here has it
    exported; the copy keeps this host's home, so the Bun and uv caches it installs from
    are the host's, and names its own state, caches and an empty sibling list. It reaches
    no package index or browser host (`NO_NETWORK`): every install comes from those caches
    and this host's Playwright browsers, and one that would have to download fails.
    """
    base = tmp_path_factory.mktemp("bootstrap")
    root = base / "checkout"
    root.mkdir()
    copy_working_tree(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    assert not (root / ".venv").exists() and not (root / "node_modules").exists()
    listing = base / "checkouts"
    listing.write_text("", encoding="utf-8")
    for directory in ("oneagentgraph-state", "tmp"):
        (base / directory).mkdir()
    environment = {
        **{name: value for name, value in os.environ.items() if name not in PROVIDED_ENVIRONMENT},
        "UV_NO_SYNC": "1",
        **NO_NETWORK,
        REGISTERED_CHECKOUTS_OVERRIDE: str(listing),
        "PLAYWRIGHT_BROWSERS_PATH": _browsers_path(),
        "XDG_CACHE_HOME": str(base / "cache"),
        "ONEVCS_HOME": str(base / "onevcs-home"),
        "ONEAGENTGRAPH_STATE_DIR": str(base / "oneagentgraph-state"),
        "TMPDIR": str(base / "tmp"),
        "UV_TOOL_DIR": str(base / "uv-tools"),
        "UV_TOOL_BIN_DIR": str(base / "uv-tool-bin"),
        "NX_DAEMON": "false",
    }
    ran = subprocess.run(
        ["just", "bootstrap"],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    _await_host_sweeps([int(pid) for pid in HOST_SWEEP_JOB.findall(ran.stderr)])
    return Bootstrapped(root=root, ran=ran, env=environment)


def _await_host_sweeps(jobs: list[int]) -> None:
    """Wait out the host sweep the recipe's session setup started, which it never waits on.

    A read of the process table for exactly the pids the setup printed; nothing is signalled.
    """

    def alive() -> list[int]:
        return [pid for pid in jobs if Path(f"/proc/{pid}").exists()]

    until(
        "the host sweep the bootstrap started to exit",
        lambda: not alive(),
        seconds=HOST_SWEEP_SECONDS,
        state=lambda: f"still running: {alive()}",
    )


def test_bootstrap_provisions_a_locked_toolchain_in_a_fresh_checkout(
    bootstrapped: Bootstrapped,
) -> None:
    """From neither environment, with `UV_NO_SYNC` exported, bootstrap leaves both at the pins."""
    root = bootstrapped.root

    assert bootstrapped.ran.returncode == 0, bootstrapped.ran.stdout + bootstrapped.ran.stderr

    # uv's own answer that the environment is the one the lockfile describes.
    locked = bootstrapped.run("uv", "sync", "--locked", "--check")
    assert locked.returncode == 0, locked.stdout + locked.stderr
    assert "Would make no changes" in locked.stdout + locked.stderr

    # The copy's interpreter imports this repository's package from the copy, not from
    # whichever checkout is on PATH. Isolated, so the working directory is not on the
    # path and the import can only come through the environment's own editable install.
    imported = bootstrapped.run(
        str(root / ".venv" / "bin" / "python"),
        "-I",
        "-c",
        "import orchestrator.root as root; print(root.__file__)",
    )
    assert imported.returncode == 0, imported.stderr
    assert Path(imported.stdout.strip()).resolve().is_relative_to(root.resolve()), imported.stdout

    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    reported = bootstrapped.run(str(root / "node_modules" / ".bin" / "nx"), "--version")
    assert reported.returncode == 0, reported.stderr
    assert f"Local: v{manifest['devDependencies']['nx']}" in reported.stdout, reported.stdout
