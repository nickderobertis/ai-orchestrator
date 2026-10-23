"""Shell-level tests for session setup behavior."""

from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from published_tools import PUBLISHED_TOOLS

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


def _setup_script(tmp_path: Path) -> Path:
    """A copy of `scripts/session-setup.sh`, in a checkout of the journey's own.

    The script provisions the checkout it sits in — a `uv sync` against it, its plan root —
    so a journey sourcing one of its steps sources this copy, never this checkout's own,
    whose toolchain every other worker's recipes read. It reads the adopted versions as
    it loads, so the copy carries them.
    """
    checkout = tmp_path / "checkout"
    script = checkout / "scripts" / "session-setup.sh"
    if not script.exists():
        script.parent.mkdir(parents=True)
        (checkout / "config").mkdir()
        _write_adopted_version_files(checkout / "config")
        script.write_text(
            (REPO_ROOT / "scripts" / "session-setup.sh").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return script


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
    script = _setup_script(tmp_path)
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


#: Where the stand-in for `scripts/workspace-install.sh` records each call's arguments.
WORKSPACE_INSTALL_CALLS = "workspace-install.calls"
#: Where the stand-in for `scripts/setup-llmlint.sh` records each call.
SETUP_LLMLINT_CALLS = "setup-llmlint.calls"


def _write_workspace_install_double(scripts: Path, tmp_path: Path) -> None:
    """Stand in for `scripts/workspace-install.sh`: record each call, fail on request."""
    _write_executable(
        scripts / "workspace-install.sh",
        f'#!/bin/sh\nprintf \'[%s]\\n\' "$*" >>"{tmp_path / WORKSPACE_INSTALL_CALLS}"\n'
        'if [ "${TEST_WORKSPACE_INSTALL_FAIL:-0}" = 1 ]; then\n'
        '  echo "workspace-install: install locked workspace dependencies and retry" >&2\n'
        "  exit 1\n"
        "fi\n",
    )


def _run_full_setup_without_bun(
    tmp_path: Path,
    *,
    declared: Mapping[str, str | None] | None = None,
    real_workspace_install: bool = False,
    args: Sequence[str] = (),
    **extra_env: str,
) -> subprocess.CompletedProcess[str]:
    """Run the whole script against a fixture host that has every tool but bun.

    `declared` restates one `config/<tool>.version` after the real ones are copied in:
    a string replaces its contents, and `None` removes the file outright. The locked
    workspace install is the repository's own script, which reaches the real Bun, so it
    is doubled like `uv` and `bun`: it records each call and fails on request.
    `real_workspace_install` copies in the real script and the two it sources instead,
    so it runs against whichever `bun` the fixture host puts first.
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
    _write_executable(
        scripts / "setup-llmlint.sh",
        f'#!/bin/sh\necho called >>"{tmp_path / SETUP_LLMLINT_CALLS}"\nexit 0\n',
    )
    if real_workspace_install:
        for script in ("workspace-install.sh", "preserved-log.sh", "install-lock.sh"):
            copied = scripts / script
            copied.write_text(
                (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8"), encoding="utf-8"
            )
            copied.chmod(0o755)
    else:
        _write_workspace_install_double(scripts, tmp_path)
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
        ["bash", str(session_setup), *args],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            # Where a `uv tool install` the whole setup reaches would land: the journey's own.
            "UV_TOOL_DIR": str(tmp_path / "uv-tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / "uv-tool-bin"),
            **extra_env,
        },
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
    # With no Bun there is nothing to install the workspace with, so it is not attempted.
    assert not (tmp_path / WORKSPACE_INSTALL_CALLS).exists()


def test_full_setup_installs_the_locked_workspace_once_bun_is_ready(tmp_path: Path) -> None:
    """Setup is what provisions `node_modules`: one plain, non-forced locked install."""
    _write_bun(tmp_path / ".local" / "node" / "bin" / "bun")

    proc = _run_full_setup_without_bun(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / WORKSPACE_INSTALL_CALLS).read_text(encoding="utf-8").splitlines() == ["[]"]


def test_full_setup_fails_when_the_locked_workspace_install_fails(tmp_path: Path) -> None:
    """No Nx target runs without `node_modules`, so a failed install fails the session's setup."""
    _write_bun(tmp_path / ".local" / "node" / "bin" / "bun")

    proc = _run_full_setup_without_bun(tmp_path, TEST_WORKSPACE_INSTALL_FAIL="1")

    assert proc.returncode == 1
    assert "workspace-install: install locked workspace dependencies and retry" in proc.stderr
    assert "the locked workspace install failed — no Nx target can run" in proc.stderr
    # The steps after it still ran: a failed install is reported, not an abort.
    assert "ready (onejudge: " in proc.stderr


def test_full_setup_reports_the_real_installers_failure_and_carries_on(tmp_path: Path) -> None:
    """The real `scripts/workspace-install.sh`, over a Bun whose install fails, fails setup
    with the installer's own diagnostic and Bun's output, and the later steps still run."""
    _write_executable(
        tmp_path / ".local" / "node" / "bin" / "bun",
        "#!/bin/sh\n"
        'if [ "$1" = install ]; then\n'
        '  echo "error: lockfile had changes, but lockfile is frozen" >&2\n'
        "  exit 1\n"
        "fi\n"
        "printf '1.2.3\\n'\n",
    )

    proc = _run_full_setup_without_bun(tmp_path, real_workspace_install=True)

    assert proc.returncode == 1, proc.stderr
    assert "error: lockfile had changes, but lockfile is frozen" in proc.stderr
    assert "workspace-install: install locked workspace dependencies and retry" in proc.stderr
    assert "the locked workspace install failed — no Nx target can run" in proc.stderr
    assert "ready (onejudge: " in proc.stderr
    assert not (tmp_path / "repo" / "node_modules").exists()


def _write_plan_store_document(repo: Path, absolute_root: Path) -> None:
    """A `onetaskgraph.yaml` whose `local-md` roots take each spelling the setup must read:
    relative, nested, quoted, absolute and commented, beside a source that has no root and
    a top-level key after the `sources:` block whose `root` is no source's."""
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "onetaskgraph.yaml").write_text(
        "# A header comment.\n"
        "default_sources: [authoring]\n"
        "sources:\n"
        "  authoring:\n"
        "    plugin: local-md\n"
        "    config:\n"
        "      root: .plans\n"
        "  runs:\n"
        "    plugin: local-md\n"
        "    config:\n"
        "      # A comment inside the source.\n"
        '      root: ".follow-ups/runs"  # trailing comment\n'
        "  elsewhere:\n"
        "    plugin: 'local-md'\n"
        "    config:\n"
        f"      root: {absolute_root}\n"
        "  board:\n"
        "    plugin: github-projects\n"
        "    config:\n"
        "      owner: someone\n"
        "cache:\n"
        "  root: not-a-plan-root\n",
        encoding="utf-8",
    )


def test_provision_only_installs_the_checkouts_toolchain_and_nothing_about_the_host(
    tmp_path: Path,
) -> None:
    """The mode the pre-push hook runs in a publication worktree: this checkout's `.venv`
    and `node_modules`, the plan store's configured roots its gate budgets read, and none
    of the session's host steps."""
    _write_bun(tmp_path / ".local" / "node" / "bin" / "bun")
    repo = tmp_path / "repo"
    _write_plan_store_document(repo, tmp_path / "absolute-root")

    proc = _run_full_setup_without_bun(tmp_path, args=("--provision-only",))

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / WORKSPACE_INSTALL_CALLS).read_text(encoding="utf-8").splitlines() == ["[]"]
    assert not (tmp_path / SETUP_LLMLINT_CALLS).exists()
    assert "host sweep" not in proc.stderr
    assert "sibling checkout bootstrap" not in proc.stderr
    assert (repo / ".plans" / "tasks").is_dir()
    assert (repo / ".follow-ups" / "runs").is_dir()
    assert (tmp_path / "absolute-root").is_dir()
    assert not (repo / "not-a-plan-root").exists()


def test_setup_creates_the_tracked_private_local_roots_in_a_fresh_checkout(tmp_path: Path) -> None:
    """The actual tracked document provisions both private drafting roots before any read."""
    _write_bun(tmp_path / ".local" / "node" / "bin" / "bun")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "onetaskgraph.yaml").write_text(
        (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert not (repo / ".plans").exists()
    assert not (repo / ".follow-ups" / "runs").exists()

    proc = _run_full_setup_without_bun(tmp_path, args=("--provision-only",))

    assert proc.returncode == 0, proc.stderr
    assert (repo / ".plans").is_dir()
    assert (repo / ".follow-ups" / "runs").is_dir()


def test_setup_fails_naming_a_configured_plan_root_it_cannot_create(tmp_path: Path) -> None:
    """A root the store will refuse every read for is a provisioning failure, said by name."""
    _write_bun(tmp_path / ".local" / "node" / "bin" / "bun")
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory must go\n", encoding="utf-8")
    _write_plan_store_document(tmp_path / "repo", blocker / "root")

    proc = _run_full_setup_without_bun(tmp_path, args=("--provision-only",))

    assert proc.returncode == 1
    assert (
        f"plan roots: {blocker / 'root'}, a local-md root onetaskgraph.yaml configures,"
        " could not be created"
    ) in proc.stderr


def test_provision_only_fails_when_the_locked_workspace_install_fails(tmp_path: Path) -> None:
    """A publication gate with no `node_modules` cannot run, so the hook is told so."""
    _write_bun(tmp_path / ".local" / "node" / "bin" / "bun")

    proc = _run_full_setup_without_bun(
        tmp_path, args=("--provision-only",), TEST_WORKSPACE_INSTALL_FAIL="1"
    )

    assert proc.returncode == 1
    assert "the locked workspace install failed — no Nx target can run" in proc.stderr


def test_setup_refuses_an_argument_it_does_not_take_and_installs_nothing(tmp_path: Path) -> None:
    _write_bun(tmp_path / ".local" / "node" / "bin" / "bun")

    proc = _run_full_setup_without_bun(tmp_path, args=("--provision",))

    assert proc.returncode == 2
    assert "unknown argument '--provision'" in proc.stderr
    assert "--provision-only" in proc.stderr
    assert not (tmp_path / WORKSPACE_INSTALL_CALLS).exists()
    assert not (tmp_path / SETUP_LLMLINT_CALLS).exists()


def test_ensure_codex_exposes_asdf_install_on_stable_worker_path(tmp_path: Path) -> None:
    script = _setup_script(tmp_path)
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
    script = _setup_script(tmp_path)
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
    script = _setup_script(tmp_path)
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
    script = _setup_script(tmp_path)
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
    assert len(lines) == 1
    assert lines[0].startswith("export PATH=")
    assert f"{tmp_path}/.local/node/bin" in lines[0]


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
            str(_setup_script(tmp_path)),
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
    assert len(lines) == 1
    assert lines[0].startswith("export PATH=")
    assert f"{tmp_path}/.local/node/bin" in lines[0]


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
            str(_setup_script(tmp_path)),
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
            str(_setup_script(tmp_path)),
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


def test_persist_session_env_adds_nothing_when_path_was_already_saved(tmp_path: Path) -> None:
    env_file = tmp_path / "claude-env"
    saved_path = f"{tmp_path}/.local/node/bin:/usr/bin:/bin"
    env_file.write_text(f"export PATH={saved_path}\n", encoding="utf-8")
    script = _setup_script(tmp_path)

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
    assert env_file.read_text(encoding="utf-8").splitlines() == [f"export PATH={saved_path}"]
