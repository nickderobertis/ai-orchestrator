"""The published tools this repository configures, and proof each pin is real.

`config/<tool>.version` is the single source of truth for each adopted release, the
same way `config/onejudge.version` is. A pin only means something if the named
release is actually published and installable, so these checks follow one pin from
the version file through the dependency declaration and the lockfile's resolved
source to the CLI the checkout installed — a git ref, a path dependency, or a vendored
copy fails at the lockfile step, and a version nobody published fails at the install
step, because neither `uv sync` nor a frozen `bun install` could have produced it. A
PyPI tool is followed through `pyproject.toml` and `uv.lock`; the engine and the read
API, which this host installs from npmjs.com, through `package.json` and `bun.lock`.
"""

from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
import tomllib

import pytest
from published_tools import (
    NPM_TOOLS,
    PUBLISHED_TOOLS,
    PYPI_TOOLS,
    SEPARATELY_GATED_VERSION_FILES,
    PublishedTool,
)

from orchestrator.root import REPO_ROOT

PUBLISHED_TOOL_IDS = [tool.distribution for tool in PUBLISHED_TOOLS]
PYPI_TOOL_IDS = [tool.distribution for tool in PYPI_TOOLS]
NPM_TOOL_IDS = [tool.distribution for tool in NPM_TOOLS]
#: What a PyPI-resolved lockfile entry names as its source. Anything else — a git
#: ref, a local path, a directory — is what this repository's pins exclude.
PYPI_REGISTRY = "https://pypi.org/simple"
#: The engine's and the read API's platform packages this host's own platform installs.
NPM_PLATFORM = "linux-x64"


def _bun_lock_packages() -> dict[str, list[object]]:
    """`bun.lock`'s `packages` table: Bun writes JSON with trailing commas."""
    text = (REPO_ROOT / "bun.lock").read_text(encoding="utf-8")
    document = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
    packages: dict[str, list[object]] = document["packages"]
    return packages


@pytest.mark.parametrize("tool", PYPI_TOOLS, ids=PYPI_TOOL_IDS)
def test_pyproject_pins_the_distribution_to_the_adopted_version(tool: PublishedTool) -> None:
    """`config/<tool>.version` is the pin, so the dependency spec may not drift from it."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    declared = "onetaskgraph-sdk" if tool.distribution == "onetaskgraph-cli" else tool.distribution
    specs = [item for item in dependencies if item.split("==")[0] == declared]

    assert specs == [f"{declared}=={tool.adopted_version}"], (
        f"pyproject.toml must pin {declared} exactly to config/{tool.version_file}; found {specs!r}"
    )


@pytest.mark.parametrize("tool", PYPI_TOOLS, ids=PYPI_TOOL_IDS)
def test_the_lockfile_resolves_the_pin_from_pypi(tool: PublishedTool) -> None:
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


@pytest.mark.parametrize("tool", NPM_TOOLS, ids=NPM_TOOL_IDS)
def test_an_npm_tool_is_pinned_and_locked_from_npm_and_absent_from_pypi(
    tool: PublishedTool,
) -> None:
    """`package.json` pins the npm package exactly, `bun.lock` resolves it and the platform
    package that carries its binary from the registry, and no Python lock names it.

    A `bun.lock` entry resolved from the registry carries an empty source and an
    integrity hash; a git ref, a path or a tarball URL is what this repository's pins
    exclude, exactly as a non-PyPI `uv.lock` source is.
    """
    manifest = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    assert manifest["dependencies"].get(tool.distribution) == tool.adopted_version, (
        f"package.json must pin {tool.distribution} exactly to config/{tool.version_file} "
        f"({tool.adopted_version}); found {manifest['dependencies'].get(tool.distribution)!r}"
    )
    packages = _bun_lock_packages()
    for name in (tool.distribution, f"{tool.distribution}-{NPM_PLATFORM}"):
        locked = packages.get(name)
        assert locked is not None, f"bun.lock resolves no {name}"
        assert locked[0] == f"{name}@{tool.adopted_version}", (
            f"bun.lock resolves {locked[0]}, not {name}@{tool.adopted_version}"
        )
        assert locked[1] == "" and str(locked[-1]).startswith("sha512-"), (
            f"{name} must resolve from the npm registry with an integrity hash; found {locked!r}"
        )
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert f'"{tool.distribution}==' not in pyproject, (
        f"pyproject.toml still requires {tool.distribution}, which this host installs from npm"
    )
    assert not [package for package in lock["package"] if package["name"] == tool.distribution]


@pytest.mark.parametrize("tool", PUBLISHED_TOOLS, ids=PUBLISHED_TOOL_IDS)
def test_the_adopted_release_is_installed_and_reports_its_version(tool: PublishedTool) -> None:
    """The pin is installable: provisioning put this exact release in this checkout.

    Both halves are checked because either can drift on its own — the package that
    supplies the metadata, and the CLI it installs.
    """
    adopted = tool.adopted_version
    if tool.registry == "npm":
        manifest = REPO_ROOT / "node_modules" / tool.distribution / "package.json"
        if not manifest.is_file():
            pytest.fail(f"{tool.distribution} is not installed here — run 'just bootstrap'")
        installed = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    else:
        try:
            installed = importlib.metadata.version(tool.distribution)
        except importlib.metadata.PackageNotFoundError:
            pytest.fail(f"{tool.distribution} is not installed here — run 'just bootstrap'")
    assert installed == adopted, (
        f"wrong {tool.distribution} in this checkout: expected {adopted!r}, "
        f"got {installed!r} — run 'just bootstrap'"
    )

    executable = tool.executable()
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


@pytest.mark.parametrize("tool", PUBLISHED_TOOLS, ids=PUBLISHED_TOOL_IDS)
def test_session_setup_declares_every_published_tool(tool: PublishedTool) -> None:
    """DRIFT-GATE `PUBLISHED_TOOLS` against the table session setup provisions from.

    Session setup's own table drives reading the version file, the `uv sync` decision,
    and the post-install verification alike, so an entry missing there would silently
    leave that tool unprovisioned on every session. That the declared tools are then
    really installed and verified is proven by
    `tests/session_setup_pypi/test_session_setup_e2e.py::test_session_setup_installs_every_published_tool_from_its_registry`.
    """
    script = (REPO_ROOT / "scripts" / "session-setup.sh").read_text(encoding="utf-8")
    table = "NPM_TOOL_SPECS" if tool.registry == "npm" else "PUBLISHED_TOOL_SPECS"
    declared = re.search(rf"readonly {table}=\((.*?)\n\)", script, re.DOTALL)
    assert declared is not None, f"scripts/session-setup.sh declares no {table}"

    assert f'"{tool.binary}|{tool.distribution}|{tool.version_file}"' in declared.group(1), (
        f"scripts/session-setup.sh must carry {tool.binary} in {table} as "
        f"'{tool.binary}|{tool.distribution}|{tool.version_file}'"
    )


@pytest.mark.parametrize(
    "tool",
    [tool for tool in PUBLISHED_TOOLS if tool.npm_package is not None],
    ids=[tool.npm_package for tool in PUBLISHED_TOOLS if tool.npm_package is not None],
)
def test_the_npm_half_of_a_release_is_pinned_to_the_same_version(tool: PublishedTool) -> None:
    """A tool published as two artifacts is installed here as one release.

    `just dag-ui` serves the npm bundle against the wheel's read API, so a bundle
    pinned to another release would be a view built for a contract this host does
    not serve — and nothing else in the workspace reads `package.json`'s pin.
    """
    assert tool.npm_package is not None
    manifest = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    declared = {
        **manifest.get("dependencies", {}),
        **manifest.get("devDependencies", {}),
    }

    assert declared.get(tool.npm_package) == tool.adopted_version, (
        f"package.json must pin {tool.npm_package} exactly to config/{tool.version_file} "
        f"({tool.adopted_version}); found {declared.get(tool.npm_package)!r}"
    )


def test_every_adopted_version_file_is_covered_by_a_gate() -> None:
    """A new `config/<tool>.version` joins this gate rather than going unchecked."""
    declared = {tool.version_file for tool in PUBLISHED_TOOLS} | SEPARATELY_GATED_VERSION_FILES
    on_disk = {path.name for path in (REPO_ROOT / "config").glob("*.version")}

    assert on_disk == declared, (
        "every adopted published tool must be listed in PUBLISHED_TOOLS; "
        f"unlisted: {sorted(on_disk - declared)}, absent from config/: {sorted(declared - on_disk)}"
    )


def test_the_version_file_format_matches_the_established_pins() -> None:
    """Each new file follows `config/onejudge.version` in shape as well as in name.

    Session setup reads all of them the same way, so a file carrying anything but one
    semantic version on one line is a pin the next reader has to special-case.
    """
    established = (REPO_ROOT / "config" / "onejudge.version").read_bytes()
    assert established.decode().strip().encode() + b"\n" == established

    for tool in PUBLISHED_TOOLS:
        content = (REPO_ROOT / "config" / tool.version_file).read_bytes()
        assert content == f"{tool.adopted_version}\n".encode()
