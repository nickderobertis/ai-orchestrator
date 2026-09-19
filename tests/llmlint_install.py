"""Where `uv tool` puts `llmlint`, and what the installed release declares it needs.

Shared by the drift gate over this host's install (`tests/test_llmlint_release_pin.py`)
and the journey that drives the installer against a throwaway tool directory
(`tests/e2e/test_setup_llmlint_e2e.py`): both read the same thing — the installed
distribution's own `Requires-Dist` — from a tool directory `uv` names, so a reading
the gate takes on the host is the reading the journey proves the installer produces.
"""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

from orchestrator.root import REPO_ROOT

#: The distribution `scripts/setup-llmlint.sh` installs, and the one it constrains
#: to the pin. Named once here for both sides of the comparison.
LLMLINT_DISTRIBUTION = "llmlint-cli"
ONEHARNESS_DISTRIBUTION = "oneharness-cli"
#: The pin the installer derives its ceiling from.
ONEHARNESS_PIN_FILE = REPO_ROOT / "config" / "oneharness.version"
INSTALLER = REPO_ROOT / "scripts" / "setup-llmlint.sh"


def installer_floor() -> Version:
    """The floor `scripts/setup-llmlint.sh` declares, read off the script itself."""
    declared = re.search(
        r'^readonly LLMLINT_MIN="([^"]+)"$', INSTALLER.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert declared is not None, f"{INSTALLER} no longer declares LLMLINT_MIN"
    return Version(declared.group(1))


def pinned_oneharness() -> Version:
    """The `oneharness-cli` release `config/oneharness.version` adopts."""
    return Version(ONEHARNESS_PIN_FILE.read_text(encoding="utf-8").strip())


def installed_llmlint(tool_dir: Path) -> importlib.metadata.Distribution | None:
    """The `llmlint-cli` distribution installed under one `uv tool` directory, if any.

    Read off the tool venv's own `dist-info` rather than by importing anything: the
    tool venv is not this interpreter's, and `importlib.metadata.Distribution.at`
    reads the metadata a wheel installed without needing to be inside it.
    """
    environment = tool_dir / LLMLINT_DISTRIBUTION
    if not environment.is_dir():
        return None
    dist_infos = sorted(
        environment.glob(
            f"lib/python*/site-packages/{LLMLINT_DISTRIBUTION.replace('-', '_')}-*.dist-info"
        )
    )
    if len(dist_infos) != 1:
        return None
    return importlib.metadata.Distribution.at(dist_infos[0])


def declared_oneharness_requirement(llmlint: importlib.metadata.Distribution) -> Requirement:
    """The one `oneharness-cli` requirement an installed `llmlint-cli` declares."""
    declared = [
        Requirement(spec)
        for spec in llmlint.metadata.get_all("Requires-Dist") or ()
        if Requirement(spec).name == ONEHARNESS_DISTRIBUTION
    ]
    assert len(declared) == 1, (
        f"{LLMLINT_DISTRIBUTION} {llmlint.version} declares {len(declared)} "
        f"{ONEHARNESS_DISTRIBUTION} requirements, {[str(r) for r in declared]}; the "
        "installer's rule compares the pin against one"
    )
    return declared[0]
