"""Shell-level tests for session setup behavior."""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import time
import tomllib
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from published_tools import PUBLISHED_TOOLS
from waits import timeout as e2e_timeout
from waits import until

from orchestrator.root import REPO_ROOT

ADOPTED_ONEJUDGE_VERSION = (
    (REPO_ROOT / "config" / "onejudge.version").read_text(encoding="utf-8").strip()
)
ADOPTED_ONEHARNESS_VERSION = (
    (REPO_ROOT / "config" / "oneharness.version").read_text(encoding="utf-8").strip()
)
#: What the fake `python` these fixtures install answers `importlib.metadata.version`
#: with, per distribution — the published tools alongside oneharness, since session
#: setup now verifies every one of them as a distribution as well as a CLI.
ADOPTED_DISTRIBUTION_VERSIONS = {
    "oneharness-cli": ADOPTED_ONEHARNESS_VERSION,
    **{tool.distribution: tool.adopted_version for tool in PUBLISHED_TOOLS},
}


def test_onejudge_dependency_pin_matches_authoritative_version() -> None:
    """DRIFT-GATE the executable SDK dependency against config/onejudge.version."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    onejudge_specs = [
        dependency for dependency in dependencies if dependency.startswith("onejudge")
    ]

    assert len(onejudge_specs) == 1, (
        "pyproject.toml must declare exactly one exact onejudge dependency; "
        f"found {onejudge_specs!r}"
    )
    package, separator, pinned_version = onejudge_specs[0].partition("==")
    assert package == "onejudge" and separator and pinned_version, (
        "pyproject.toml must pin the onejudge distribution exactly as onejudge==<version>; "
        f"found {onejudge_specs[0]!r}"
    )
    assert pinned_version == ADOPTED_ONEJUDGE_VERSION, (
        f"pyproject.toml pins onejudge=={pinned_version}, but config/onejudge.version "
        f"declares {ADOPTED_ONEJUDGE_VERSION}"
    )


def test_oneharness_dependency_pin_matches_authoritative_version() -> None:
    """DRIFT-GATE the worktree-local CLI against config/oneharness.version."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    specs = [item for item in dependencies if item.startswith("oneharness-cli")]

    assert specs == [f"oneharness-cli=={ADOPTED_ONEHARNESS_VERSION}"], (
        "pyproject.toml must pin oneharness-cli exactly to config/oneharness.version; "
        f"found {specs!r}"
    )


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write_adopted_version_files(config_dir: Path) -> None:
    """Give a copied session-setup the same adopted-release declarations the real one reads.

    Copied rather than enumerated, so adopting a further tool needs no fixture edit.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    for declared in (REPO_ROOT / "config").glob("*.version"):
        (config_dir / declared.name).write_bytes(declared.read_bytes())


def _write_onejudge(path: Path, version: str) -> None:
    _write_executable(path, f"#!/bin/sh\nprintf 'onejudge {version}\\n'\n")


def _write_oneharness(path: Path, version: str) -> None:
    _write_executable(path, f"#!/bin/sh\nprintf 'oneharness {version}\\n'\n")


def _write_bun(path: Path, version: str = "1.2.3") -> None:
    _write_executable(path, f"#!/bin/sh\nprintf '{version}\\n'\n")


def _write_published_tool_clis(bin_dir: Path) -> None:
    """Install a stand-in for every published tool session setup verifies as a CLI."""
    for tool in PUBLISHED_TOOLS:
        _write_executable(
            bin_dir / tool.binary,
            f"#!/bin/sh\nprintf '{tool.binary} {tool.adopted_version}\\n'\n",
        )


def _fake_install_commands(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    _write_published_tool_clis(tmp_path / "pinned-tools")
    uv = tools / "uv"
    _write_executable(
        uv,
        """#!/bin/sh
printf '%s\n' "$*" >"$TEST_UV_ARGS"
if [ "${TEST_UV_FAIL:-0}" = 1 ]; then
  exit 1
fi
mkdir -p "$TEST_REPO/.venv/bin"
cp "$TEST_ONEJUDGE_BINARY" "$TEST_REPO/.venv/bin/onejudge"
cp "$TEST_ONEHARNESS_BINARY" "$TEST_REPO/.venv/bin/oneharness"
cp "$TEST_SDK_PYTHON" "$TEST_REPO/.venv/bin/python"
cp "$TEST_PUBLISHED_TOOL_DIR"/* "$TEST_REPO/.venv/bin/"
chmod +x "$TEST_REPO/.venv/bin/"*
""",
    )


def _run_project_install(tmp_path: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    test_repo = tmp_path / "repo"
    (test_repo / "scripts").mkdir(parents=True)
    (test_repo / "config").mkdir()
    script = test_repo / "scripts" / "session-setup.sh"
    script.write_text(
        (REPO_ROOT / "scripts" / "session-setup.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write_adopted_version_files(test_repo / "config")
    tools = tmp_path / "tools"
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tools}:/usr/bin:/bin",
        "TEST_REPO": str(test_repo),
        "TEST_UV_ARGS": str(tmp_path / "uv.args"),
        "TEST_PUBLISHED_TOOL_DIR": str(tmp_path / "pinned-tools"),
        **extra_env,
    }
    return subprocess.run(
        ["bash", "-c", 'source "$1"; install_project_dependencies', "test-install", str(script)],
        text=True,
        capture_output=True,
        env=env,
    )


def _run_bun_install(
    tmp_path: Path, *, with_npm: bool = True, **extra_env: str
) -> subprocess.CompletedProcess[str]:
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    tools = tmp_path / "tools"
    npm = tools / "npm"
    if with_npm:
        _write_executable(
            npm,
            """#!/bin/sh
printf '%s\n' "$*" >"$TEST_NPM_ARGS"
if [ "${TEST_NPM_FAIL:-0}" = 1 ]; then
  exit 1
fi
mkdir -p "$HOME/.local/node/bin"
cp "$TEST_BUN_BINARY" "$HOME/.local/node/bin/bun"
chmod +x "$HOME/.local/node/bin/bun"
""",
        )
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tools}:/usr/bin:/bin",
        "TEST_NPM_ARGS": str(tmp_path / "npm.args"),
        **extra_env,
    }
    return subprocess.run(
        ["bash", "-c", 'source "$1"; install_bun', "test-install", str(script)],
        text=True,
        capture_output=True,
        env=env,
    )


#: The one place `scripts/session-setup.sh` pins the `cargo-sweep` it installs, read off
#: the script so these journeys drive the release the installer really asks for.
CARGO_SWEEP_PIN = re.compile(r'^readonly CARGO_SWEEP_VERSION="(?P<version>\d+\.\d+\.\d+)"$', re.M)


def pinned_cargo_sweep() -> str:
    found = CARGO_SWEEP_PIN.search(
        (REPO_ROOT / "scripts" / "session-setup.sh").read_text(encoding="utf-8")
    )
    assert found is not None, "session-setup.sh no longer pins cargo-sweep in one place"
    return found["version"]


def _write_cargo_sweep(path: Path, version: str) -> None:
    _write_executable(path, f"#!/bin/sh\nprintf 'cargo-sweep {version}\\n'\n")


def _run_cargo_sweep_install(
    tmp_path: Path, *, with_cargo: bool = True, **extra_env: str
) -> subprocess.CompletedProcess[str]:
    """Drive the script's own installer, with `cargo` — the published CLI it delegates
    to — the one thing doubled: it records the install request it was asked for and
    places the `cargo-sweep` the journey names into `$HOME/.cargo/bin`, where a real
    `cargo install` puts one."""
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    tools = tmp_path / "tools"
    if with_cargo:
        _write_executable(
            tools / "cargo",
            """#!/bin/sh
printf '%s\n' "$*" >>"$TEST_CARGO_ARGS"
if [ "${TEST_CARGO_FAIL:-0}" = 1 ]; then
  exit 1
fi
mkdir -p "$HOME/.cargo/bin"
cp "$TEST_CARGO_SWEEP_BINARY" "$HOME/.cargo/bin/cargo-sweep"
chmod +x "$HOME/.cargo/bin/cargo-sweep"
""",
        )
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tools}:/usr/bin:/bin",
        "TEST_CARGO_ARGS": str(tmp_path / "cargo.args"),
        **extra_env,
    }
    return subprocess.run(
        ["bash", "-c", 'source "$1"; install_cargo_sweep', "test-install", str(script)],
        text=True,
        capture_output=True,
        env=env,
    )


def _cargo_sweep_version(tmp_path: Path) -> str:
    """What the `cargo-sweep` on the journey's `PATH` answers, from where the installer put it."""
    answered = subprocess.run(
        [tmp_path / ".cargo" / "bin" / "cargo-sweep", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    return answered.stdout.strip()


def test_install_cargo_sweep_installs_the_pinned_release_from_an_absent_tool(
    tmp_path: Path,
) -> None:
    """With no `cargo-sweep` on `PATH`, the installer asks cargo for the pinned release.

    The request is read off what the double was asked — `install cargo-sweep --version
    <pinned>` — and the result off the binary it placed, which is the pinned release
    the pool's Rust slots will be maintained with.
    """
    replacement = tmp_path / "cargo-sweep-current"
    _write_cargo_sweep(replacement, pinned_cargo_sweep())

    proc = _run_cargo_sweep_install(tmp_path, TEST_CARGO_SWEEP_BINARY=str(replacement))

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "cargo.args").read_text(encoding="utf-8").splitlines() == [
        f"install cargo-sweep --version {pinned_cargo_sweep()}"
    ]
    assert _cargo_sweep_version(tmp_path) == f"cargo-sweep {pinned_cargo_sweep()}"
    assert f"cargo-sweep ready ({pinned_cargo_sweep()})" in proc.stderr


def test_install_cargo_sweep_asks_nothing_over_the_installed_pinned_release(
    tmp_path: Path,
) -> None:
    """A second run over the tool the first installed records no install request."""
    replacement = tmp_path / "cargo-sweep-current"
    _write_cargo_sweep(replacement, pinned_cargo_sweep())
    first = _run_cargo_sweep_install(tmp_path, TEST_CARGO_SWEEP_BINARY=str(replacement))
    assert first.returncode == 0, first.stderr
    (tmp_path / "cargo.args").unlink()

    again = _run_cargo_sweep_install(tmp_path, TEST_CARGO_SWEEP_BINARY=str(replacement))

    assert again.returncode == 0, again.stderr
    assert not (tmp_path / "cargo.args").exists(), "the installed pinned release was reinstalled"
    assert _cargo_sweep_version(tmp_path) == f"cargo-sweep {pinned_cargo_sweep()}"


def test_install_cargo_sweep_reinstalls_a_release_other_than_the_pinned_one(
    tmp_path: Path,
) -> None:
    """A `cargo-sweep` at another release is what the pin exists to replace."""
    _write_cargo_sweep(tmp_path / ".cargo" / "bin" / "cargo-sweep", "0.7.0")
    replacement = tmp_path / "cargo-sweep-current"
    _write_cargo_sweep(replacement, pinned_cargo_sweep())

    proc = _run_cargo_sweep_install(tmp_path, TEST_CARGO_SWEEP_BINARY=str(replacement))

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "cargo.args").read_text(encoding="utf-8").splitlines() == [
        f"install cargo-sweep --version {pinned_cargo_sweep()}"
    ]
    assert _cargo_sweep_version(tmp_path) == f"cargo-sweep {pinned_cargo_sweep()}"


def test_install_cargo_sweep_refuses_a_release_cargo_placed_that_is_not_the_pinned_one(
    tmp_path: Path,
) -> None:
    """A `cargo install` that succeeds and leaves another release is reported, not accepted.

    The verification after the install is the same one that decides whether to install
    at all, so a binary answering a release other than the pin — or nothing usable — is
    named as unavailable rather than left as the command every Rust slot is swept with.
    """
    replacement = tmp_path / "cargo-sweep-other"
    _write_cargo_sweep(replacement, "0.7.0")

    proc = _run_cargo_sweep_install(tmp_path, TEST_CARGO_SWEEP_BINARY=str(replacement))

    assert proc.returncode == 1
    assert (tmp_path / "cargo.args").read_text(encoding="utf-8").splitlines() == [
        f"install cargo-sweep --version {pinned_cargo_sweep()}"
    ]
    assert f"reports 'cargo-sweep 0.7.0', not the pinned {pinned_cargo_sweep()}" in proc.stderr
    assert "optional cargo-sweep is unavailable after cargo install" in proc.stderr


def test_install_cargo_sweep_is_optional_and_says_so_without_cargo(tmp_path: Path) -> None:
    """A host without `cargo` has no Rust slot to maintain, and is told rather than failed."""
    proc = _run_cargo_sweep_install(tmp_path, with_cargo=False)

    assert proc.returncode == 1
    assert "cannot install optional cargo-sweep: cargo is not installed" in proc.stderr
    assert not (tmp_path / "cargo.args").exists()


def test_install_cargo_sweep_reports_a_failed_cargo_install(tmp_path: Path) -> None:
    proc = _run_cargo_sweep_install(tmp_path, TEST_CARGO_FAIL="1")

    assert proc.returncode == 1
    assert (tmp_path / "cargo.args").read_text(encoding="utf-8").splitlines() == [
        f"install cargo-sweep --version {pinned_cargo_sweep()}"
    ]
    assert "cargo-sweep cargo install failed" in proc.stderr


def test_full_setup_continues_past_an_absent_cargo_and_reports_cargo_sweep_unavailable(
    tmp_path: Path,
) -> None:
    """The whole script, on a host with every tool but `cargo`, keeps its toolchain verdict.

    The installer is optional: the run says the pool's Rust slots go unmaintained and
    exits on the required tools alone, which the fixture host has.
    """
    result = _run_full_setup_without_bun(tmp_path)

    assert "cannot install optional cargo-sweep: cargo is not installed" in result.stderr
    assert "cargo-sweep unavailable" in result.stderr
    assert "bun is required" in result.stderr


def _run_full_setup_without_bun(
    tmp_path: Path, *, declared: Mapping[str, str | None] | None = None, **extra_env: str
) -> subprocess.CompletedProcess[str]:
    """Run the whole script against a fixture host that has every tool but bun.

    `declared` restates one `config/<tool>.version` after the real ones are copied in:
    a string replaces its contents, and `None` removes the file outright.
    """
    test_repo = tmp_path / "repo"
    scripts = test_repo / "scripts"
    config = test_repo / "config"
    scripts.mkdir(parents=True)
    config.mkdir()
    session_setup = scripts / "session-setup.sh"
    session_setup.write_text(
        (REPO_ROOT / "scripts" / "session-setup.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write_executable(scripts / "setup-llmlint.sh", "#!/bin/sh\nexit 0\n")
    _write_adopted_version_files(config)
    for name, adopted in (declared or {}).items():
        if adopted is None:
            (config / name).unlink()
        else:
            (config / name).write_text(adopted, encoding="utf-8")
    _write_onejudge(test_repo / ".venv" / "bin" / "onejudge", ADOPTED_ONEJUDGE_VERSION)
    _write_sdk_python(test_repo / ".venv" / "bin" / "python", ADOPTED_ONEJUDGE_VERSION)
    _write_oneharness(test_repo / ".venv" / "bin" / "oneharness", ADOPTED_ONEHARNESS_VERSION)
    _write_published_tool_clis(test_repo / ".venv" / "bin")
    return subprocess.run(
        ["bash", str(session_setup)],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", **extra_env},
    )


def _write_sdk_python(
    path: Path,
    onejudge_version: str,
    oneharness_version: str = ADOPTED_ONEHARNESS_VERSION,
    distribution_versions: Mapping[str, str] | None = None,
) -> None:
    """Install a stand-in `python` answering every metadata query session setup makes.

    One branch per distribution, so a test can move one version without moving the
    rest; the default branch answers the `onejudge_sdk` import instead of metadata.
    """
    answers = {
        **ADOPTED_DISTRIBUTION_VERSIONS,
        "oneharness-cli": oneharness_version,
        **(distribution_versions or {}),
    }
    branches = "".join(
        f"*{distribution}*) printf '{version}\\n' ;; " for distribution, version in answers.items()
    )
    _write_executable(
        path,
        f"#!/bin/sh\ncase \"$*\" in {branches}*) printf '{onejudge_version}\\n' ;; esac\n",
    )


def test_project_install_skips_sync_when_every_pinned_tool_is_compliant(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    _write_onejudge(tmp_path / "repo" / ".venv" / "bin" / "onejudge", ADOPTED_ONEJUDGE_VERSION)
    _write_sdk_python(tmp_path / "repo" / ".venv" / "bin" / "python", ADOPTED_ONEJUDGE_VERSION)
    _write_oneharness(
        tmp_path / "repo" / ".venv" / "bin" / "oneharness", ADOPTED_ONEHARNESS_VERSION
    )
    _write_published_tool_clis(tmp_path / "repo" / ".venv" / "bin")

    proc = _run_project_install(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "uv.args").exists()


def test_project_install_syncs_pinned_tools_into_worktree_venv(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    replacement = tmp_path / f"onejudge-{ADOPTED_ONEJUDGE_VERSION}"
    sdk_python = tmp_path / "sdk-python"
    harness = tmp_path / "oneharness"
    _write_onejudge(replacement, ADOPTED_ONEJUDGE_VERSION)
    _write_sdk_python(sdk_python, ADOPTED_ONEJUDGE_VERSION)
    _write_oneharness(harness, ADOPTED_ONEHARNESS_VERSION)

    proc = _run_project_install(
        tmp_path,
        TEST_ONEJUDGE_BINARY=str(replacement),
        TEST_ONEHARNESS_BINARY=str(harness),
        TEST_SDK_PYTHON=str(sdk_python),
    )

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "uv.args").read_text(encoding="utf-8").strip() == (
        f"sync --project {tmp_path}/repo"
    )


def test_project_install_fails_loudly_when_uv_is_unavailable(tmp_path: Path) -> None:
    proc = _run_project_install(tmp_path)

    assert proc.returncode == 1
    assert "cannot install required project dependencies: uv is not installed" in proc.stderr


def test_project_install_surfaces_uv_sync_failure(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)

    proc = _run_project_install(tmp_path, TEST_UV_FAIL="1")

    assert proc.returncode == 1
    assert (tmp_path / "uv.args").read_text(encoding="utf-8").strip() == (
        f"sync --project {tmp_path}/repo"
    )
    assert "project dependency sync failed" in proc.stderr
    assert (
        "required pinned onejudge, oneharness, and published-tool dependencies are unavailable"
        in proc.stderr
    )


def test_project_install_rejects_wrong_sdk_version(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    replacement = tmp_path / f"onejudge-{ADOPTED_ONEJUDGE_VERSION}"
    sdk_python = tmp_path / "sdk-python"
    harness = tmp_path / "oneharness"
    wrong_version = "99.99.99"  # never the adopted pin, so the mismatch is guaranteed
    _write_onejudge(replacement, ADOPTED_ONEJUDGE_VERSION)
    _write_sdk_python(sdk_python, wrong_version)
    _write_oneharness(harness, ADOPTED_ONEHARNESS_VERSION)

    proc = _run_project_install(
        tmp_path,
        TEST_ONEJUDGE_BINARY=str(replacement),
        TEST_ONEHARNESS_BINARY=str(harness),
        TEST_SDK_PYTHON=str(sdk_python),
    )

    assert proc.returncode == 1
    assert f"expected '{ADOPTED_ONEJUDGE_VERSION}', got '{wrong_version}'" in proc.stderr
    assert "required pinned onejudge, oneharness, and published-tool dependencies" in proc.stderr


def _run_project_install_with_real_pins(
    tmp_path: Path, **extra_env: str
) -> subprocess.CompletedProcess[str]:
    """Install with every adopted release compliant except what the caller staged."""
    onejudge = tmp_path / "onejudge"
    sdk_python = tmp_path / "sdk-python"
    harness = tmp_path / "oneharness"
    _write_onejudge(onejudge, ADOPTED_ONEJUDGE_VERSION)
    _write_oneharness(harness, ADOPTED_ONEHARNESS_VERSION)
    if not sdk_python.exists():
        _write_sdk_python(sdk_python, ADOPTED_ONEJUDGE_VERSION)
    return _run_project_install(
        tmp_path,
        TEST_ONEJUDGE_BINARY=str(onejudge),
        TEST_ONEHARNESS_BINARY=str(harness),
        TEST_SDK_PYTHON=str(sdk_python),
        **extra_env,
    )


def test_project_install_rejects_a_published_tool_left_at_a_stale_release(tmp_path: Path) -> None:
    """A CLI still on a previous release fails setup rather than passing unnoticed.

    These four tools are the published implementations this repository configures, so
    a venv holding a stale one is a working host quietly running different code —
    exactly the drift onejudge's own version check has always refused.
    """
    _fake_install_commands(tmp_path)
    stale = PUBLISHED_TOOLS[0]
    _write_executable(
        tmp_path / "pinned-tools" / stale.binary,
        f"#!/bin/sh\nprintf '{stale.binary} 99.99.99\\n'\n",
    )

    proc = _run_project_install_with_real_pins(tmp_path)

    assert proc.returncode == 1
    assert (
        f"{stale.binary} verification failed: expected '{stale.binary} {stale.adopted_version}', "
        f"got '{stale.binary} 99.99.99'"
    ) in proc.stderr
    assert "required pinned onejudge, oneharness, and published-tool dependencies" in proc.stderr


def test_project_install_rejects_a_published_tool_whose_distribution_drifted(
    tmp_path: Path,
) -> None:
    """The wheel and the console script it installs are checked separately.

    A distribution that no longer matches the CLI beside it means the venv holds two
    releases at once, which is a resolution failure rather than a stale binary.
    """
    _fake_install_commands(tmp_path)
    drifted = PUBLISHED_TOOLS[-1]
    sdk_python = tmp_path / "sdk-python"
    _write_sdk_python(
        sdk_python,
        ADOPTED_ONEJUDGE_VERSION,
        distribution_versions={drifted.distribution: "99.99.99"},
    )

    proc = _run_project_install_with_real_pins(tmp_path)

    assert proc.returncode == 1
    assert (
        f"{drifted.binary} distribution verification failed: "
        f"expected '{drifted.adopted_version}', got '99.99.99'"
    ) in proc.stderr


def test_full_setup_reports_every_published_tool_it_verified(tmp_path: Path) -> None:
    """The session log names each adopted release, so an operator can read the host."""
    result = _run_full_setup_without_bun(tmp_path)

    # Bun is deliberately absent from this fixture; the published tools still verified.
    assert result.returncode == 1
    for tool in PUBLISHED_TOOLS:
        assert f"ready ({tool.binary}: {tool.adopted_version} at " in result.stderr
    assert "releases are required" not in result.stderr
    assert (tmp_path / "repo" / ".plans" / "tasks").is_dir()
    assert (tmp_path / "repo" / ".plans" / "projects").is_dir()


@pytest.mark.parametrize(
    ("adopted", "reported"),
    [("0.2\n", "0.2"), ("v0.2.2\n", "v0.2.2"), (None, "")],
    ids=["not-semver", "tagged", "absent"],
)
def test_full_setup_refuses_a_published_tool_without_one_adopted_release(
    tmp_path: Path, adopted: str | None, reported: str
) -> None:
    """An undeclared or unreadable pin aborts setup by name before anything is installed.

    The version file is the pin, so a host that could not read one has no adopted
    release to hold its CLI to — provisioning on anyway would install whatever
    `uv sync` resolved and then verify it against nothing.
    """
    undeclared = PUBLISHED_TOOLS[0]

    result = _run_full_setup_without_bun(tmp_path, declared={undeclared.version_file: adopted})

    assert result.returncode == 1
    assert (
        f"invalid adopted {undeclared.binary} version in "
        f"{tmp_path / 'repo' / 'config' / undeclared.version_file}: '{reported}'"
    ) in result.stderr
    # Aborting means aborting: no tool is verified and no provisioning is attempted.
    assert "ready (" not in result.stderr


def test_install_bun_skips_invocable_binary(tmp_path: Path) -> None:
    _write_bun(tmp_path / ".local" / "node" / "bin" / "bun")

    proc = _run_bun_install(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "npm.args").exists()


def test_install_bun_installs_with_npm_and_verifies_binary(tmp_path: Path) -> None:
    replacement = tmp_path / "bun-current"
    _write_bun(replacement)

    proc = _run_bun_install(tmp_path, TEST_BUN_BINARY=str(replacement))

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "npm.args").read_text(encoding="utf-8").strip() == "install -g bun"
    version = subprocess.run(
        [tmp_path / ".local" / "node" / "bin" / "bun", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip() == "1.2.3"


def test_install_bun_failure_is_required(tmp_path: Path) -> None:
    proc = _run_bun_install(tmp_path, TEST_NPM_FAIL="1")

    assert proc.returncode == 1
    assert (tmp_path / "npm.args").read_text(encoding="utf-8").strip() == "install -g bun"
    assert "bun npm install failed" in proc.stderr


def test_install_bun_fails_loudly_when_npm_is_unavailable(tmp_path: Path) -> None:
    proc = _run_bun_install(tmp_path, with_npm=False)

    assert proc.returncode == 1
    assert "cannot install required bun: npm is not installed" in proc.stderr


def test_install_bun_rejects_unusable_binary_after_npm_succeeds(tmp_path: Path) -> None:
    replacement = tmp_path / "broken-bun"
    _write_executable(replacement, "#!/bin/sh\nexit 1\n")

    proc = _run_bun_install(tmp_path, TEST_BUN_BINARY=str(replacement))

    assert proc.returncode == 1
    assert "could not report its version" in proc.stderr
    assert "required bun is unavailable after npm install" in proc.stderr


def test_full_setup_reports_missing_bun_and_returns_failure(tmp_path: Path) -> None:
    proc = _run_full_setup_without_bun(tmp_path)

    assert proc.returncode == 1
    assert "cannot install required bun: npm is not installed" in proc.stderr
    assert "bun is required — the oneharness sdk-check gate will fail" in proc.stderr


def test_ensure_codex_exposes_asdf_install_on_stable_worker_path(tmp_path: Path) -> None:
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    asdf_bin = tmp_path / ".asdf" / "installs" / "nodejs" / "26.5.0" / "bin"
    codex = asdf_bin / "codex"
    _write_executable(codex, "#!/bin/sh\nprintf 'subscription codex\\n'\n")

    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; ensure_codex; PATH="$HOME/.local/bin:/usr/bin:/bin" codex',
            "test-ensure-codex",
            str(script),
        ],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{asdf_bin}:/usr/bin:/bin",
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "subscription codex\n"
    assert (tmp_path / ".local" / "bin" / "codex").resolve() == codex


def test_ensure_codex_exposes_new_npm_install_on_stable_worker_path(tmp_path: Path) -> None:
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    tools = tmp_path / "tools"
    npm = tools / "npm"
    installed_codex = tools / "codex"
    _write_executable(
        npm,
        """#!/bin/sh
 printf '#!/bin/sh\nprintf "npm subscription codex\\\\n"\n' >"$(dirname "$0")/codex"
 chmod +x "$(dirname "$0")/codex"
""",
    )

    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; ensure_codex; PATH="$HOME/.local/bin:/usr/bin:/bin" codex',
            "test-install-codex",
            str(script),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": f"{tools}:/usr/bin:/bin"},
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "npm subscription codex\n"
    assert (tmp_path / ".local" / "bin" / "codex").resolve() == installed_codex


def test_expose_codex_logs_stable_path_failure_without_blocking(tmp_path: Path) -> None:
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    local_path_blocker = tmp_path / ".local"
    local_path_blocker.write_text("not a directory", encoding="utf-8")

    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; expose_codex /asdf/bin/codex',
            "test-expose-codex-failure",
            str(script),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert proc.returncode == 0
    assert f"could not expose /asdf/bin/codex at {tmp_path}/.local/bin/codex" in proc.stderr


def test_persist_session_env_writes_worker_sandbox_environment_once(tmp_path: Path) -> None:
    env_file = tmp_path / "claude-env"
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    for live_path in ("/usr/bin:/bin", f"{tmp_path}/.local/node/bin:/usr/bin:/bin"):
        proc = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; persist_session_env',
                "test-persist-session-env",
                str(script),
            ],
            text=True,
            capture_output=True,
            env={
                "HOME": str(tmp_path),
                "PATH": live_path,
                "CLAUDE_ENV_FILE": str(env_file),
            },
        )
        assert proc.returncode == 0, proc.stderr

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("export PATH=")
    assert f"{tmp_path}/.local/node/bin" in lines[0]
    assert lines[1] == f"export LLMLINT_ONEHARNESS_BIN={REPO_ROOT}/scripts/llmlint-oneharness.sh"


def test_persist_session_env_creates_the_first_sessions_absent_parent(tmp_path: Path) -> None:
    """The state a freshly authenticated config directory is in on its first session.

    Claude Code names `<config-dir>/session-env/<session-id>/sessionstart-hook-0.sh`,
    and on that first session neither directory exists yet, so the appends used to
    fail with a raw shell redirect error and the session lost its toolchain PATH.
    """
    env_file = tmp_path / ".claude-alt2" / "session-env" / "a-session-id" / "sessionstart-hook-0.sh"

    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; persist_session_env',
            "test-persist",
            str(REPO_ROOT / "scripts" / "session-setup.sh"),
        ],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{tmp_path}/.local/node/bin:/usr/bin:/bin",
            "CLAUDE_ENV_FILE": str(env_file),
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", "the absent parent must not leak a raw shell redirect error"
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("export PATH=")
    assert f"{tmp_path}/.local/node/bin" in lines[0]
    assert lines[1] == f"export LLMLINT_ONEHARNESS_BIN={REPO_ROOT}/scripts/llmlint-oneharness.sh"


def test_persist_session_env_reports_an_unwritable_env_file_without_shell_noise(
    tmp_path: Path,
) -> None:
    """Persistence stays optional: an append it cannot make is this script's own log."""
    # A directory in the file's place is unwritable regardless of privilege, and
    # `mkdir -p` on its parent still succeeds, so this reaches the append itself.
    env_file = tmp_path / "session-env" / "a-session-id" / "sessionstart-hook-0.sh"
    env_file.mkdir(parents=True)

    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; persist_session_env; echo "rc=$?"',
            "test-persist",
            str(REPO_ROOT / "scripts" / "session-setup.sh"),
        ],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{tmp_path}/.local/node/bin:/usr/bin:/bin",
            "CLAUDE_ENV_FILE": str(env_file),
        },
    )

    assert proc.stdout == "rc=0\n", proc.stderr
    assert "No such file or directory" not in proc.stderr
    assert "Is a directory" not in proc.stderr
    assert (
        f"session-setup: cannot persist the session environment: {env_file} is not writable"
        in proc.stderr
    )
    assert list(env_file.iterdir()) == []


def test_persist_session_env_reports_a_parent_it_cannot_create(tmp_path: Path) -> None:
    """Creating the parent is best effort; failing to must not fail session setup."""
    # A regular file where `session-env` belongs makes `mkdir -p` fail for any uid.
    (tmp_path / "session-env").write_text("not a directory", encoding="utf-8")
    env_file = tmp_path / "session-env" / "a-session-id" / "sessionstart-hook-0.sh"

    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; persist_session_env; echo "rc=$?"',
            "test-persist",
            str(REPO_ROOT / "scripts" / "session-setup.sh"),
        ],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{tmp_path}/.local/node/bin:/usr/bin:/bin",
            "CLAUDE_ENV_FILE": str(env_file),
        },
    )

    assert proc.stdout == "rc=0\n", proc.stderr
    assert (
        "session-setup: cannot persist the session environment: "
        f"{env_file.parent} could not be created" in proc.stderr
    )
    assert not env_file.exists()


def test_persist_session_env_adds_wrapper_when_path_was_already_saved(tmp_path: Path) -> None:
    env_file = tmp_path / "claude-env"
    saved_path = f"{tmp_path}/.local/node/bin:/usr/bin:/bin"
    env_file.write_text(f"export PATH={saved_path}\n", encoding="utf-8")
    script = REPO_ROOT / "scripts" / "session-setup.sh"

    proc = subprocess.run(
        ["bash", "-c", 'source "$1"; persist_session_env', "test-persist", str(script)],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "CLAUDE_ENV_FILE": str(env_file),
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        f"export PATH={saved_path}",
        f"export LLMLINT_ONEHARNESS_BIN={REPO_ROOT}/scripts/llmlint-oneharness.sh",
    ]


# The host sweep session setup starts detached. Every journey below runs the real
# `scripts/session-setup.sh`, `scripts/host-sweep.sh`, `justfile` and
# `scripts/repos-bootstrap.sh`, copied into a fixture repository, through the real `just`,
# `uv`, `flock` and `setsid`; only the published CLIs are doubled, at the PATH boundary,
# except in the last journey, which runs the real ones. Each points `XDG_CACHE_HOME` at
# its own directory, so no journey touches this host's lock, and ends by releasing its
# doubles and waiting until no process it started, or a job it started, is alive.

#: The one place the interval is declared, read off the script.
HOST_SWEEP = REPO_ROOT / "scripts" / "host-sweep.sh"


def host_sweep_interval() -> int:
    found = re.search(
        r"(?m)^readonly HOST_SWEEP_INTERVAL_SECONDS=(\d+)$",
        HOST_SWEEP.read_text(encoding="utf-8"),
    )
    assert found is not None, "host-sweep.sh no longer declares its interval in one place"
    return int(found.group(1))


#: How long a session start with a sweep still running may take. Bounded in seconds,
#: against a doubled sweep that would run for ten minutes.
SESSION_START_SECONDS = 60

#: A doubled sweep verb: it records what it was asked, holds until the journey releases
#: it (for ten minutes at most, so a journey that died cannot leave it for good), and
#: exits as the journey says. Any other argument is the version query session setup makes.
SWEEP_VERB_DOUBLE = """#!/bin/sh
if [ "$1" = sweep ]; then
  printf '%s\\n' "{binary} $*" >>"$TEST_SWEEP_CALLS"
  waited=0
  while [ -n "${{TEST_SWEEP_RELEASE:-}}" ] && [ ! -e "$TEST_SWEEP_RELEASE" ]; do
    [ "$waited" -lt 6000 ] || break
    sleep 0.1
    waited=$((waited + 1))
  done
  echo "{binary} sweep double: exiting ${{TEST_SWEEP_EXIT:-0}}" >&2
  exit "${{TEST_SWEEP_EXIT:-0}}"
fi
printf '{binary} {version}\\n'
"""


@functools.cache
def _real_tool_path() -> str:
    """The directories of the real `just` and `uv`, resolved past any version-manager shim.

    A journey's `HOME` is its own, which a shim reads its configuration from, so the
    binaries themselves are named: `uv run` exports its own path as `UV`.
    """
    just = shutil.which("just")
    uv = shutil.which("uv")
    assert just is not None and uv is not None, "the journeys need `just` and `uv` on PATH"
    resolved = subprocess.run(
        [uv, "run", "--no-project", "printenv", "UV"],
        cwd="/",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return f"{Path(just).resolve().parent}:{Path(resolved).parent}"


def _pid_alive(pid: int) -> bool:
    """A read of the process table, never a signal."""
    return Path(f"/proc/{pid}").exists()


class SessionStart:
    """A fixture repository whose real session setup starts the host sweep."""

    def __init__(self, tmp_path: Path, *, real_verbs: bool = False) -> None:
        self.root = tmp_path
        self.repo = tmp_path / "repo"
        scripts = self.repo / "scripts"
        scripts.mkdir(parents=True)
        for name in (
            "session-setup.sh",
            "host-sweep.sh",
            "repos-bootstrap.sh",
            "registered-checkouts.sh",
        ):
            shutil.copy(REPO_ROOT / "scripts" / name, scripts / name)
        shutil.copyfile(REPO_ROOT / "justfile", self.repo / "justfile")
        self.marks = tmp_path / "marks"
        self.marks.mkdir()
        _write_executable(
            scripts / "setup-llmlint.sh", f'#!/bin/sh\ntouch "{self.marks}/setup-llmlint"\n'
        )
        _write_adopted_version_files(self.repo / "config")
        venv_bin = self.repo / ".venv" / "bin"
        if real_verbs:
            (self.repo / ".venv").symlink_to(REPO_ROOT / ".venv")
        else:
            _write_onejudge(venv_bin / "onejudge", ADOPTED_ONEJUDGE_VERSION)
            _write_sdk_python(venv_bin / "python", ADOPTED_ONEJUDGE_VERSION)
            _write_oneharness(venv_bin / "oneharness", ADOPTED_ONEHARNESS_VERSION)
            _write_published_tool_clis(venv_bin)
            for tool in PUBLISHED_TOOLS:
                if tool.binary in {"onevcs", "oneagentgraph"}:
                    _write_executable(
                        venv_bin / tool.binary,
                        SWEEP_VERB_DOUBLE.format(binary=tool.binary, version=tool.adopted_version),
                    )
        _write_bun(tmp_path / ".local" / "node" / "bin" / "bun")
        checkouts = tmp_path / "checkouts"
        checkouts.write_text("# no sibling checkout in this journey\n", encoding="utf-8")
        self.calls = tmp_path / "sweep-calls"
        self.release_file = tmp_path / "release"
        self.state = tmp_path / "cache" / "ai-orchestrator" / "sweep"
        self.scratch = {
            "ONEVCS_HOME": tmp_path / "onevcs-home",
            "ONEAGENTGRAPH_STATE_DIR": tmp_path / "oneagentgraph-state",
            "TMPDIR": tmp_path / "tmp",
        }
        for directory in self.scratch.values():
            directory.mkdir()
        (self.scratch["ONEVCS_HOME"] / "workspaces").mkdir()
        self.environment = {
            "HOME": str(tmp_path),
            "PATH": f"{_real_tool_path()}:/usr/bin:/bin",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS": str(checkouts),
            "TEST_SWEEP_CALLS": str(self.calls),
            **{name: str(path) for name, path in self.scratch.items()},
        }
        self.processes: list[subprocess.Popen[str]] = []

    def hold_sweeps(self) -> None:
        """Every doubled sweep started from here on holds until `release`."""
        self.environment["TEST_SWEEP_RELEASE"] = str(self.release_file)

    def release(self) -> None:
        self.release_file.touch()

    def start(self, **extra_env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "session-setup.sh")],
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env={**self.environment, **extra_env},
            timeout=e2e_timeout(SESSION_START_SECONDS),
        )

    def spawn(self, *command: str) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            list(command),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=self.environment,
        )
        self.processes.append(process)
        return process

    def spawn_start(self) -> subprocess.Popen[str]:
        return self.spawn("bash", str(self.repo / "scripts" / "session-setup.sh"))

    def spawn_hand_sweep(self, *arguments: str) -> subprocess.Popen[str]:
        """`just sweep` run by hand in the fixture repository, its doubles on PATH."""
        environment = dict(self.environment)
        environment["PATH"] = f"{self.repo / '.venv' / 'bin'}:{environment['PATH']}"
        process = subprocess.Popen(
            ["just", "--justfile", str(self.repo / "justfile"), "sweep", *arguments],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=environment,
        )
        self.processes.append(process)
        return process

    def hand_sweep(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        """A hand-run to completion, bounded well below any sweep a journey holds."""
        process = self.spawn_hand_sweep(*arguments)
        stdout, stderr = process.communicate(timeout=e2e_timeout(30))
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def verb_calls(self) -> list[str]:
        if not self.calls.exists():
            return []
        return self.calls.read_text(encoding="utf-8").splitlines()

    def field(self, file: str, key: str) -> str | None:
        path = self.state / file
        if not path.exists():
            return None
        for row in path.read_text(encoding="utf-8").splitlines():
            name, _, value = row.partition(" ")
            if name == key:
                return value
        return None

    def holder_pid(self) -> int | None:
        pid = self.field("holder", "pid")
        return int(pid) if pid is not None else None

    def await_calls(self, count: int) -> None:
        until(
            f"{count} sweep verb call(s)",
            lambda: len(self.verb_calls()) >= count,
            seconds=60,
            state=lambda: f"calls {self.verb_calls()}, holder {self.holder_pid()}",
        )

    def await_completion(self, *, since: float = 0.0) -> None:
        """Until a sweep records an ending no earlier than `since` (epoch seconds)."""

        def recorded() -> bool:
            finished = self.field("completed", "finished")
            return finished is not None and int(finished) >= int(since)

        until(
            "the sweep to record its ending",
            recorded,
            seconds=120,
            state=lambda: f"calls {self.verb_calls()}, holder {self.holder_pid()}",
        )

    def settle(self) -> None:
        """Release every double, and wait until nothing this journey started is alive."""
        self.release()
        for process in self.processes:
            process.communicate(timeout=e2e_timeout(120))
        pid = self.holder_pid()
        if pid is not None:
            until(
                f"the sweep job {pid} to exit",
                lambda: not _pid_alive(pid),
                seconds=120,
                state=lambda: f"calls {self.verb_calls()}",
            )


@pytest.fixture
def session_start(tmp_path: Path) -> Iterator[SessionStart]:
    start = SessionStart(tmp_path)
    try:
        yield start
    finally:
        start.settle()


#: What the job's recipe asks each verb, with the default four-hour floor.
ONEVCS_SWEEP = "onevcs sweep --min-age-hours 4"
ONEAGENTGRAPH_SWEEP = "oneagentgraph sweep --min-age-hours 4"


def test_session_setup_returns_while_the_sweep_it_started_still_runs(
    session_start: SessionStart,
) -> None:
    """The hook's foreground never waits on the sweep, and every later step still runs.

    The doubled `onevcs sweep` holds for up to ten minutes; session setup returns in
    seconds with it still running, and exits exactly as the start after it does — one
    that starts no sweep at all, because this one completed inside the hour.
    """
    session_start.hold_sweeps()
    began = time.monotonic()

    first = session_start.start()

    elapsed = time.monotonic() - began
    assert elapsed < SESSION_START_SECONDS, first.stderr
    session_start.await_calls(1)
    job = session_start.holder_pid()
    assert job is not None and _pid_alive(job), first.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP]
    assert f"started job {job} (log: {session_start.state / 'sweep.log'})" in first.stderr
    for tool in PUBLISHED_TOOLS:
        assert f"ready ({tool.binary}: {tool.adopted_version} at " in first.stderr
    assert (session_start.marks / "setup-llmlint").exists(), first.stderr
    assert "repos-bootstrap: 0 started, 0 running" in first.stderr, first.stderr

    session_start.release()
    session_start.await_completion()
    assert session_start.field("completed", "status") == "done"
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]

    unswept = session_start.start()

    assert "none started" in unswept.stderr, unswept.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]
    assert first.returncode == unswept.returncode == 0, first.stderr + unswept.stderr


def test_concurrent_session_starts_launch_one_sweep(session_start: SessionStart) -> None:
    """Several starts at once, and one after them, start one job between them."""
    session_start.hold_sweeps()

    starts = [session_start.spawn_start() for _ in range(4)]
    outputs = [start.communicate(timeout=e2e_timeout(SESSION_START_SECONDS)) for start in starts]
    session_start.await_calls(1)
    later = session_start.start()

    reported = "".join(stderr for _, stderr in outputs) + later.stderr
    assert reported.count("host-sweep: started job") == 1, reported
    # A start that met the lock in the instant between another caller taking it and
    # recording itself says so rather than naming a pid; either way it started nothing.
    assert reported.count("host-sweep: a sweep is running, ") == 4, reported
    assert all(start.returncode == 0 for start in starts), reported
    assert session_start.verb_calls() == [ONEVCS_SWEEP]

    session_start.release()
    session_start.await_completion()
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]


def test_a_hand_run_sweep_while_a_job_holds_the_lock_sweeps_nothing(
    session_start: SessionStart,
) -> None:
    """`just sweep` by hand names the job holding the host and its log, and sweeps nothing."""
    session_start.hold_sweeps()
    session_start.start()
    session_start.await_calls(1)
    job = session_start.holder_pid()

    by_hand = session_start.hand_sweep()

    assert by_hand.returncode != 0, by_hand.stdout + by_hand.stderr
    assert (
        f"another sweep holds the host lock, pid {job} "
        f"(log: {session_start.state / 'sweep.log'}); nothing swept"
    ) in by_hand.stderr, by_hand.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP]


def test_a_session_start_while_a_hand_run_sweep_holds_the_lock_launches_none(
    session_start: SessionStart,
) -> None:
    """A sweep run by hand holds the same lock, and a start reports it rather than joining."""
    session_start.hold_sweeps()
    by_hand = session_start.spawn_hand_sweep()
    session_start.await_calls(1)
    holder = session_start.holder_pid()
    assert holder is not None and _pid_alive(holder)

    start = session_start.start()

    assert (
        f"a sweep is running, pid {holder} (log: its caller's terminal, run by hand); none started"
    ) in start.stderr, start.stderr
    assert session_start.verb_calls() == [ONEVCS_SWEEP]

    session_start.release()
    by_hand.communicate(timeout=e2e_timeout(120))
    assert by_hand.returncode == 0
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]
    # A sweep run by hand for real is the stamp a start reads, so this one starts nothing.
    assert session_start.field("completed", "status") == "done"
    assert "none started" in session_start.start().stderr
    assert len(session_start.verb_calls()) == 2


def test_a_start_inside_the_hour_after_a_sweep_launches_nothing_and_one_after_it_does(
    session_start: SessionStart,
) -> None:
    """The completion stamp gates starts for exactly the declared interval."""
    session_start.start()
    session_start.await_completion()
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]

    inside = session_start.start()

    assert "the next is due in" in inside.stderr and "none started" in inside.stderr
    assert len(session_start.verb_calls()) == 2

    # The hour passing, which a journey cannot wait out: the stamp moved back past it.
    now = int(time.time())
    completed = session_start.state / "completed"
    completed.write_text(
        completed.read_text(encoding="utf-8").replace(
            f"finished {session_start.field('completed', 'finished')}",
            f"finished {now - host_sweep_interval() - 1}",
        ),
        encoding="utf-8",
    )
    after = session_start.start()

    assert "host-sweep: started job" in after.stderr, after.stderr
    session_start.await_completion(since=now)
    assert session_start.verb_calls() == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP] * 2


def test_a_dry_run_by_hand_neither_needs_nor_writes_the_stamp(
    session_start: SessionStart,
) -> None:
    """A rehearsal is no sweep: the next start still launches one."""
    rehearsal = session_start.hand_sweep("--dry-run")

    assert rehearsal.returncode == 0, rehearsal.stderr
    assert session_start.verb_calls() == [
        "onevcs sweep --dry-run --min-age-hours 4",
        "oneagentgraph sweep --dry-run --min-age-hours 4",
    ]
    assert not (session_start.state / "completed").exists()

    start = session_start.start()

    assert "host-sweep: started job" in start.stderr, start.stderr
    session_start.await_completion()
    assert session_start.verb_calls()[2:] == [ONEVCS_SWEEP, ONEAGENTGRAPH_SWEEP]


def test_a_failed_sweep_is_named_with_its_log_by_the_next_start_and_never_fails_it(
    session_start: SessionStart,
) -> None:
    """The next start names the failure and its log; its exit is the toolchain's alone."""
    failing = session_start.start(TEST_SWEEP_EXIT="3")
    session_start.await_completion()
    assert session_start.field("completed", "status") == "failed"
    log = session_start.state / "sweep.log"
    assert "onevcs sweep double: exiting 3" in log.read_text(encoding="utf-8")

    reported = session_start.start()

    assert f"the last sweep failed: exit 3 (log: {log})" in reported.stderr, reported.stderr
    assert "the host sweep reported a failure" in reported.stderr
    assert failing.returncode == reported.returncode == 0, failing.stderr + reported.stderr

    # Once the hour has passed the next start runs it again, and the line naming the
    # failure names the log kept apart from the one the new job writes.
    completed = session_start.state / "completed"
    completed.write_text(
        completed.read_text(encoding="utf-8").replace(
            f"finished {session_start.field('completed', 'finished')}",
            f"finished {int(time.time()) - host_sweep_interval() - 1}",
        ),
        encoding="utf-8",
    )
    again = session_start.start()

    kept = session_start.state / "sweep.failed.log"
    assert f"the last sweep failed: exit 3 (log: {kept}); started again as job" in again.stderr
    assert "onevcs sweep double: exiting 3" in kept.read_text(encoding="utf-8")
    assert again.returncode == 0, again.stderr


def test_a_session_starts_job_runs_both_real_verbs_with_the_default_floor(
    tmp_path: Path,
) -> None:
    """The detached job is the only automatic caller of all three reclamations.

    Run with the real `onevcs` and `oneagentgraph` over a sandboxed state root: scratch
    aged past the four-hour floor is gone and scratch inside it is kept, and onevcs's own
    report — its workspaces and its session records — is a real pass, not a rehearsal,
    at that floor.
    """
    session_start = SessionStart(tmp_path, real_verbs=True)
    temp = session_start.scratch["TMPDIR"]
    aged: dict[str, Path] = {}
    for name, hours in (("old", 6), ("young", 2)):
        directory = temp / f"oneagentgraph-{name}"
        directory.mkdir()
        (directory / "owner.lock").write_text("999999 1\n", encoding="utf-8")
        stamp = time.time() - hours * 3600
        for path in (directory / "owner.lock", directory):
            os.utime(path, (stamp, stamp))
        aged[name] = directory
    try:
        start = session_start.start()

        assert "host-sweep: started job" in start.stderr, start.stderr
        session_start.await_completion()
    finally:
        session_start.settle()

    log = (session_start.state / "sweep.log").read_text(encoding="utf-8")
    assert session_start.field("completed", "status") == "done", log
    assert not aged["old"].exists(), log
    assert aged["young"].exists(), log
    assert "onevcs sweep: reclaimed" in log, log
    assert "keeping anything written inside the last 4 hour(s)" in log, log
    assert "Session records with nothing left behind them:" in log, log
    assert "rehearsal" not in log, log
