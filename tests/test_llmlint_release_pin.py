"""The `llmlint` this host installs runs under the `oneharness` this host pins.

`config/oneharness.version` names the `oneharness` every judged run reaches, and
`scripts/setup-llmlint.sh` installs `llmlint-cli` host-wide on every session start:
two adoptions, and a `llmlint` requiring a newer `oneharness` refuses every run
before judging anything. The installer derives its ceiling from the pin; this gate
says when the two part anyway — a stop-gap by hand, an older installer, a pin moved
without re-running setup — naming both versions, off the installed distribution's
own metadata and nothing on the network. Its subject is the host's tool directory,
outside this workspace and every cache key, hence `reads_checkouts`.
`tests/e2e/test_setup_llmlint_e2e.py` drives the installer's selection itself.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from llmlint_install import (
    LLMLINT_DISTRIBUTION,
    ONEHARNESS_DISTRIBUTION,
    declared_oneharness_requirement,
    installed_llmlint,
    pinned_oneharness,
)

# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is
# this repository's tier mechanism rather than a shortcut around one: it routes a test
# to the uncached target `orchestrator:test-checkouts`, the tier that exists because no
# key over this workspace can describe state outside it, and the host's `uv tool`
# directory is such state. `tests/test_nx_cache_scope.py` holds the selectors to a
# partition of the suite.
# llmlint: ignore-block[shell_test_tiers_stay_split] Same site, same reason; and this is
# a pytest gate over an installed distribution's metadata, not a shell test suite.
pytestmark = pytest.mark.reads_checkouts


def _host_tool_dir() -> Path:
    """Where this host's `uv tool` installs live, as `uv` itself answers."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH, so nothing here can name where llmlint would be installed")
    located = subprocess.run([uv, "tool", "dir"], text=True, capture_output=True, check=False)
    assert located.returncode == 0, f"`uv tool dir` failed: {located.stderr}"
    return Path(located.stdout.strip())


def test_the_installed_llmlint_declares_a_oneharness_requirement_the_pin_satisfies() -> None:
    """Fail naming both versions; skip naming the path when nothing is installed.

    Read off `Requires-Dist` rather than by running `llmlint`, because the refusal
    this guards against is what running it produces, and the metadata is what the
    installer's rule is stated over.
    """
    tool_dir = _host_tool_dir()
    llmlint = installed_llmlint(tool_dir)
    if llmlint is None:
        pytest.skip(
            f"no {LLMLINT_DISTRIBUTION} is installed under {tool_dir / LLMLINT_DISTRIBUTION}; "
            "run 'just setup-llmlint' to install the release the pin admits"
        )
    pinned = pinned_oneharness()
    required = declared_oneharness_requirement(llmlint)

    assert required.specifier.contains(pinned, prereleases=True), (
        f"the installed {LLMLINT_DISTRIBUTION} {llmlint.version} requires "
        f"{required}, and config/oneharness.version pins {ONEHARNESS_DISTRIBUTION} "
        f"{pinned}; every judged run on this host "
        "would refuse on the version. Re-run 'just setup-llmlint', which installs the "
        "highest release the pin admits — or, to adopt the newer llmlint, move the pin "
        "in its own change"
    )


# llmlint: ignore-end[shell_test_tiers_stay_split]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
