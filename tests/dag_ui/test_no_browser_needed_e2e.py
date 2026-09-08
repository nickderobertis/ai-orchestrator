"""This tier needs no browser, and that is proven rather than left to a host that has none.

Every target in `just check` is inside `just gate`, which is inside this repository's
`pre-push` hook — so anything this tier needs is something the merge path needs, on
every host that publishes from here. A browser is not a thing this repository
provisions: no script, recipe or target installs one, and a driver's managed browser
revision arrives only when somebody downloads it by hand. While one journey here drove
a real browser, the default branch failed its own gate on any host without that
revision and every branch publishing from there was refused for it.

Asserting that the published bundle *renders* belongs to the repository that builds it;
`tests/dag_ui/AGENTS.md` says so, and what this tier covers instead is this
repository's own composition of the view.

**A green tier is only evidence while the block bites**, which is why the two halves
are one test. On a host that still has a browser, a block that did nothing would let
the tier pass exactly as it does now and this module would attest nothing — so a real
launch is required to fail first, and only then is the tier driven.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

from nx_workspace import WORKSPACE_INSTALL_MARKS
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [*WORKSPACE_INSTALL_MARKS]

#: The tier's own declaration, which is where the command it runs comes from. Read
#: rather than restated: a copy here would go on driving the tier's old command the
#: day the declaration moved, and a probe that drives something else than the tier is
#: exactly as good as no probe.
TIER = Path(__file__).parent / "project.json"
#: Where a driver's managed browser is looked for. Pointed at an empty directory, this
#: is what makes Playwright's own browser unreachable without touching the install.
MANAGED_BROWSERS = "PLAYWRIGHT_BROWSERS_PATH"
#: The names a process reaching for a browser by bare name would spawn. Shadowed on
#: `PATH` so "no browser is reachable" covers more than the managed one.
BROWSER_EXECUTABLES = (
    "chrome",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "firefox",
    "msedge",
)
#: The workspace's own browser driver, which `scripts/dag-ui-screens.sh` still uses and
#: `package.json` still pins. Its presence is what makes the refusal below evidence: a
#: launch that failed because the driver is not installed would say nothing about
#: whether a browser is reachable.
DRIVER = REPO_ROOT / "node_modules" / "playwright"
#: The smallest thing that needs a browser: resolve the driver and start one. It closes
#: what it started, because the case this probe is written to catch is the one where the
#: launch *succeeds* — a browser left running there would hold this subprocess open to
#: its hang guard and report an ineffective block as a timeout minutes later.
LAUNCH_A_BROWSER = """
const [root] = process.argv.slice(2);
const { chromium } = require(root + '/node_modules/playwright');
const browser = await chromium.launch({ args: ['--no-sandbox'] });
await browser.close();
"""
#: What Playwright says when it has a driver and no browser under `MANAGED_BROWSERS`.
#: Matched so that a launch failing for some unrelated reason — a driver that will not
#: load, a `bun` that is not there — cannot stand in for a browser being unreachable.
NO_EXECUTABLE = "Executable doesn't exist"


def _tier_command() -> list[str]:
    """The command `dag-ui:test` runs, from the declaration that runs it."""
    declared = json.loads(TIER.read_text(encoding="utf-8"))["targets"]["test"]["command"]
    return shlex.split(declared)


class RoutesBlocked(NamedTuple):
    """An environment with both routes blocked, and the `PATH` shadow that blocks one."""

    environment: dict[str, str]
    shadowed: Path


def _block_both_routes_to_a_browser(tmp_path: Path) -> RoutesBlocked:
    """Shut the two routes anything in this workspace takes to a browser.

    Two, because there are two: a driver resolves its managed browser under
    `MANAGED_BROWSERS`, and anything else spawns one by name off `PATH`. Neither
    touches the install, so this is reversible and says nothing about the host.

    **What it does not shut is a browser named by an absolute path**, or one under a
    name `BROWSER_EXECUTABLES` does not carry — so the claim is bounded to the routes,
    and the launch the test makes next is what shows the route the only browser client
    in this workspace takes is really shut.
    """
    empty = tmp_path / "no-managed-browsers"
    empty.mkdir()
    shadowed = tmp_path / "shadowed"
    shadowed.mkdir()
    for name in BROWSER_EXECUTABLES:
        stub = shadowed / name
        stub.write_text(
            '#!/bin/sh\necho "no browser is reachable here" >&2\nexit 127\n', encoding="utf-8"
        )
        stub.chmod(0o755)
    # A child pytest inheriting this run's own xdist and session variables reads them as
    # its own, so the sub-run is handed none of them.
    inherited = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    return RoutesBlocked(
        environment={
            **inherited,
            MANAGED_BROWSERS: str(empty),
            "PATH": f"{shadowed}{os.pathsep}{inherited['PATH']}",
        },
        shadowed=shadowed,
    )


def test_this_tier_passes_with_both_routes_to_a_browser_blocked(tmp_path: Path) -> None:
    """The tier's own command, driven where neither route reaches a browser."""
    environment, shadowed = _block_both_routes_to_a_browser(tmp_path)

    assert DRIVER.is_dir(), (
        f"{DRIVER} is not installed, so a refused browser launch would prove nothing "
        "about whether a browser is reachable — run 'just bootstrap'"
    )
    for name in BROWSER_EXECUTABLES:
        resolved = shutil.which(name, path=environment["PATH"])
        assert resolved is not None and Path(resolved).parent == shadowed, (
            f"{name} resolves to {resolved}, not to the shadow under {shadowed}, so a "
            "process spawning a browser by name would still reach one"
        )

    probe = tmp_path / "launch.js"
    probe.write_text(LAUNCH_A_BROWSER, encoding="utf-8")
    launched = subprocess.run(
        ["bun", str(probe), str(REPO_ROOT)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert launched.returncode != 0, (
        "a browser started under this environment, so the block did nothing and "
        f"a green tier under it would be evidence of nothing: {launched.stdout}"
    )
    assert NO_EXECUTABLE in launched.stderr, (
        "the driver refused for a reason other than a missing browser, so neither "
        f"route has been shown to be shut: {launched.stderr}"
    )

    # The whole tier but this probe: driving the directory would drive this test inside
    # itself, and `--ignore` rather than a named module so a journey added beside the
    # ones here is covered by being there.
    tier = subprocess.run(
        [*_tier_command(), f"--ignore={Path(__file__).relative_to(REPO_ROOT)}"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )

    assert tier.returncode == 0, (
        "this tier is inside 'just check', which is inside the pre-push hook, so a "
        "journey here that needs a browser refuses every publication from a host "
        f"without one:\n{tier.stdout}\n{tier.stderr}"
    )
