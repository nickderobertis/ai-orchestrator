"""Shell-level tests for session setup behavior."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from orchestrator import REPO_ROOT

ADOPTED_ONEJUDGE_VERSION = (
    (REPO_ROOT / "config" / "onejudge.version").read_text(encoding="utf-8").strip()
)
ADOPTED_ONEHARNESS_VERSION = (
    (REPO_ROOT / "config" / "oneharness.version").read_text(encoding="utf-8").strip()
)


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


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write_onejudge(path: Path, version: str) -> None:
    _write_executable(path, f"#!/bin/sh\nprintf 'onejudge {version}\\n'\n")


def _write_oneharness(path: Path, version: str) -> None:
    _write_executable(path, f"#!/bin/sh\nprintf 'oneharness {version}\\n'\n")


def _fake_install_commands(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
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
cp "$TEST_SDK_PYTHON" "$TEST_REPO/.venv/bin/python"
chmod +x "$TEST_REPO/.venv/bin/onejudge" "$TEST_REPO/.venv/bin/python"
""",
    )


def _run_onejudge_install(tmp_path: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    test_repo = tmp_path / "repo"
    (test_repo / "scripts").mkdir(parents=True)
    (test_repo / "config").mkdir()
    script = test_repo / "scripts" / "session-setup.sh"
    script.write_text(
        (REPO_ROOT / "scripts" / "session-setup.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (test_repo / "config" / "onejudge.version").write_text(
        f"{ADOPTED_ONEJUDGE_VERSION}\n", encoding="utf-8"
    )
    (test_repo / "config" / "oneharness.version").write_text(
        f"{ADOPTED_ONEHARNESS_VERSION}\n", encoding="utf-8"
    )
    tools = tmp_path / "tools"
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tools}:/usr/bin:/bin",
        "TEST_REPO": str(test_repo),
        "TEST_UV_ARGS": str(tmp_path / "uv.args"),
        **extra_env,
    }
    return subprocess.run(
        ["bash", "-c", 'source "$1"; install_onejudge', "test-install", str(script)],
        text=True,
        capture_output=True,
        env=env,
    )


def _run_oneharness_install(tmp_path: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    tools = tmp_path / "tools"
    uv = tools / "uv"
    _write_executable(
        uv,
        """#!/bin/sh
printf '%s\n' "$*" >"$TEST_UV_ARGS"
if [ "${TEST_UV_FAIL:-0}" = 1 ]; then
  exit 1
fi
mkdir -p "$HOME/.local/bin"
cp "$TEST_ONEHARNESS_BINARY" "$HOME/.local/bin/oneharness"
chmod +x "$HOME/.local/bin/oneharness"
""",
    )
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tools}:/usr/bin:/bin",
        "TEST_UV_ARGS": str(tmp_path / "uv.args"),
        **extra_env,
    }
    return subprocess.run(
        ["bash", "-c", 'source "$1"; install_oneharness', "test-install", str(script)],
        text=True,
        capture_output=True,
        env=env,
    )


def _write_sdk_python(path: Path, version: str) -> None:
    _write_executable(path, f"#!/bin/sh\nprintf '{version}\\n'\n")


def test_install_onejudge_skips_compliant_sdk_and_cli(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    _write_onejudge(tmp_path / "repo" / ".venv" / "bin" / "onejudge", ADOPTED_ONEJUDGE_VERSION)
    _write_sdk_python(tmp_path / "repo" / ".venv" / "bin" / "python", ADOPTED_ONEJUDGE_VERSION)

    proc = _run_onejudge_install(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "uv.args").exists()


def test_install_onejudge_installs_pinned_sdk_and_cli_from_pypi(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    replacement = tmp_path / f"onejudge-{ADOPTED_ONEJUDGE_VERSION}"
    sdk_python = tmp_path / "sdk-python"
    _write_onejudge(replacement, ADOPTED_ONEJUDGE_VERSION)
    _write_sdk_python(sdk_python, ADOPTED_ONEJUDGE_VERSION)

    proc = _run_onejudge_install(
        tmp_path,
        TEST_ONEJUDGE_BINARY=str(replacement),
        TEST_SDK_PYTHON=str(sdk_python),
    )

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "uv.args").read_text(encoding="utf-8").strip() == (
        f"sync --project {tmp_path}/repo"
    )


def test_install_onejudge_fails_loudly_when_uv_is_unavailable(tmp_path: Path) -> None:
    proc = _run_onejudge_install(tmp_path)

    assert proc.returncode == 1
    assert (
        f"cannot install required onejudge {ADOPTED_ONEJUDGE_VERSION}: uv is not installed"
        in proc.stderr
    )


def test_install_onejudge_surfaces_uv_sync_failure(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)

    proc = _run_onejudge_install(tmp_path, TEST_UV_FAIL="1")

    assert proc.returncode == 1
    assert (tmp_path / "uv.args").read_text(encoding="utf-8").strip() == (
        f"sync --project {tmp_path}/repo"
    )
    assert f"onejudge {ADOPTED_ONEJUDGE_VERSION} PyPI install failed" in proc.stderr
    assert (
        f"required onejudge SDK and CLI {ADOPTED_ONEJUDGE_VERSION} are unavailable" in proc.stderr
    )


def test_install_onejudge_rejects_wrong_sdk_version(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    replacement = tmp_path / f"onejudge-{ADOPTED_ONEJUDGE_VERSION}"
    sdk_python = tmp_path / "sdk-python"
    wrong_version = "99.99.99"  # never the adopted pin, so the mismatch is guaranteed
    _write_onejudge(replacement, ADOPTED_ONEJUDGE_VERSION)
    _write_sdk_python(sdk_python, wrong_version)

    proc = _run_onejudge_install(
        tmp_path,
        TEST_ONEJUDGE_BINARY=str(replacement),
        TEST_SDK_PYTHON=str(sdk_python),
    )

    assert proc.returncode == 1
    assert f"expected '{ADOPTED_ONEJUDGE_VERSION}', got '{wrong_version}'" in proc.stderr
    assert f"required onejudge SDK and CLI {ADOPTED_ONEJUDGE_VERSION}" in proc.stderr


def test_install_oneharness_skips_compliant_binary(tmp_path: Path) -> None:
    _write_oneharness(tmp_path / ".local" / "bin" / "oneharness", ADOPTED_ONEHARNESS_VERSION)

    proc = _run_oneharness_install(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "uv.args").exists()


def test_install_oneharness_upgrades_obsolete_binary_from_exact_pypi_release(
    tmp_path: Path,
) -> None:
    _write_oneharness(tmp_path / ".local" / "bin" / "oneharness", "0.0.1")
    replacement = tmp_path / "oneharness-current"
    _write_oneharness(replacement, ADOPTED_ONEHARNESS_VERSION)

    proc = _run_oneharness_install(tmp_path, TEST_ONEHARNESS_BINARY=str(replacement))

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "uv.args").read_text(encoding="utf-8").strip() == (
        f"tool install --upgrade oneharness-cli=={ADOPTED_ONEHARNESS_VERSION}"
    )
    version = subprocess.run(
        [tmp_path / ".local" / "bin" / "oneharness", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip() == f"oneharness {ADOPTED_ONEHARNESS_VERSION}"


def test_install_oneharness_rejects_wrong_version_from_pypi(tmp_path: Path) -> None:
    _write_oneharness(tmp_path / ".local" / "bin" / "oneharness", "0.0.1")
    wrong_version = "99.99.99"  # never the adopted pin, so the mismatch is guaranteed
    replacement = tmp_path / "wrong-oneharness"
    _write_oneharness(replacement, wrong_version)

    proc = _run_oneharness_install(tmp_path, TEST_ONEHARNESS_BINARY=str(replacement))

    assert proc.returncode == 1
    assert (
        f"expected 'oneharness {ADOPTED_ONEHARNESS_VERSION}', got 'oneharness {wrong_version}'"
        in proc.stderr
    )
    assert f"required oneharness {ADOPTED_ONEHARNESS_VERSION} is unavailable" in proc.stderr


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
    installed_codex = tmp_path / ".local" / "node" / "bin" / "codex"
    _write_executable(
        npm,
        """#!/bin/sh
mkdir -p "$HOME/.local/node/bin"
printf '#!/bin/sh\nprintf "npm subscription codex\\\\n"\n' >"$HOME/.local/node/bin/codex"
chmod +x "$HOME/.local/node/bin/codex"
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


def test_persist_session_env_writes_path_once(tmp_path: Path) -> None:
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
    assert len(lines) == 1
    assert lines[0].startswith("export PATH=")
    assert f"{tmp_path}/.local/node/bin" in lines[0]
