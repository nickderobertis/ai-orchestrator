"""The published tools this repository configures, and proof each pin is real.

`config/<tool>.version` is the single source of truth for each adopted release, the
same way `config/onejudge.version` is. A pin only means something if the named
release is actually published and installable, so these checks follow one pin from
the version file through the dependency declaration and the lockfile's resolved
source to the CLI the project environment installed — a git ref, a path dependency,
or a vendored copy fails at the lockfile step, and a version nobody published fails
at the environment step, because `uv sync` could never have produced it.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
import tomllib

import pytest
from pinned_tools import LEGACY_VERSION_FILES, PINNED_TOOLS, PinnedTool

from orchestrator import REPO_ROOT

PINNED_TOOL_IDS = [tool.distribution for tool in PINNED_TOOLS]
#: What a PyPI-resolved lockfile entry names as its source. Anything else — a git
#: ref, a local path, a directory — is what this repository's pins exclude.
PYPI_REGISTRY = "https://pypi.org/simple"


@pytest.mark.parametrize("tool", PINNED_TOOLS, ids=PINNED_TOOL_IDS)
def test_pyproject_pins_the_distribution_to_the_adopted_version(tool: PinnedTool) -> None:
    """DRIFT-GATE the installed distribution against its config version file."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    specs = [item for item in dependencies if item.split("==")[0] == tool.distribution]

    assert specs == [f"{tool.distribution}=={tool.adopted_version}"], (
        f"pyproject.toml must pin {tool.distribution} exactly to config/{tool.version_file}; "
        f"found {specs!r}"
    )


@pytest.mark.parametrize("tool", PINNED_TOOLS, ids=PINNED_TOOL_IDS)
def test_the_lockfile_resolves_the_pin_from_pypi(tool: PinnedTool) -> None:
    """A pin is a published release only if the lockfile resolved it from PyPI."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [package for package in lock["package"] if package["name"] == tool.distribution]

    assert len(locked) == 1, (
        f"uv.lock must resolve {tool.distribution} exactly once; found {locked!r}"
    )
    assert locked[0]["version"] == tool.adopted_version
    assert locked[0]["source"] == {"registry": PYPI_REGISTRY}, (
        f"{tool.distribution} must resolve from {PYPI_REGISTRY}; a git ref, path dependency, "
        f"or vendored copy is not a published release (found {locked[0]['source']!r})"
    )
    assert locked[0]["wheels"], f"{tool.distribution} must publish an installable wheel"


@pytest.mark.parametrize("tool", PINNED_TOOLS, ids=PINNED_TOOL_IDS)
def test_the_adopted_release_is_installed_and_reports_its_version(tool: PinnedTool) -> None:
    """The pin is installable: `uv sync` put this exact release in the project venv.

    Both halves are checked because either can drift on its own — the wheel that
    supplies the distribution metadata, and the console script it installs.
    """
    adopted = tool.adopted_version
    try:
        installed = importlib.metadata.version(tool.distribution)
    except importlib.metadata.PackageNotFoundError:
        pytest.fail(f"{tool.distribution} is not installed here — run 'just bootstrap'")
    assert installed == adopted, (
        f"wrong {tool.distribution} in the project environment: expected {adopted!r}, "
        f"got {installed!r} — run 'just bootstrap'"
    )

    executable = REPO_ROOT / ".venv" / "bin" / tool.binary
    if not executable.is_file():
        pytest.fail(f"worktree-local {tool.binary} missing at {executable} — run 'just bootstrap'")
    reported = subprocess.run(
        [executable, "--version"], text=True, capture_output=True, check=False
    )

    assert reported.returncode == 0, reported.stderr
    assert reported.stdout.strip() == f"{tool.binary} {adopted}", (
        f"wrong {tool.binary} at {executable}: expected '{tool.binary} {adopted}', got "
        f"{reported.stdout.strip()!r} — run 'just bootstrap'"
    )


@pytest.mark.parametrize("tool", PINNED_TOOLS, ids=PINNED_TOOL_IDS)
def test_session_setup_installs_and_verifies_every_pinned_tool(tool: PinnedTool) -> None:
    """Session setup provisions the host, so a tool it does not name is not installed.

    Its table drives reading the version file, the `uv sync` decision, and the
    post-install verification alike, so a dropped entry would silently leave that
    tool unprovisioned on every session.
    """
    script = (REPO_ROOT / "scripts" / "session-setup.sh").read_text(encoding="utf-8")

    assert f'"{tool.binary}|{tool.distribution}|{tool.version_file}"' in script, (
        f"scripts/session-setup.sh must carry {tool.binary} in PINNED_TOOL_SPECS as "
        f"'{tool.binary}|{tool.distribution}|{tool.version_file}'"
    )


def test_every_adopted_version_file_is_covered_by_a_gate() -> None:
    """A new `config/<tool>.version` joins this gate rather than going unchecked."""
    declared = {tool.version_file for tool in PINNED_TOOLS} | set(LEGACY_VERSION_FILES)
    on_disk = {path.name for path in (REPO_ROOT / "config").glob("*.version")}

    assert on_disk == declared, (
        "every adopted published tool must be listed in PINNED_TOOLS; "
        f"unlisted: {sorted(on_disk - declared)}, absent from config/: {sorted(declared - on_disk)}"
    )


def test_the_version_file_format_matches_the_established_pins() -> None:
    """Each new file follows `config/onejudge.version` in shape as well as in name.

    Session setup reads all of them the same way, so a file carrying anything but one
    semantic version on one line is a pin the next reader has to special-case.
    """
    established = (REPO_ROOT / "config" / "onejudge.version").read_bytes()
    assert established.decode().strip().encode() + b"\n" == established

    for tool in PINNED_TOOLS:
        content = (REPO_ROOT / "config" / tool.version_file).read_bytes()
        assert content == f"{tool.adopted_version}\n".encode()
