"""Shell-level tests for session setup behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT


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
    curl = tools / "curl"
    cargo = tools / "cargo"
    _write_executable(
        curl,
        """#!/bin/sh
printf '%s\n' "$*" >"$TEST_CURL_ARGS"
if [ "${TEST_CURL_FAIL:-0}" = 1 ]; then
  exit 1
fi
exec /bin/cat "$TEST_INSTALLER"
""",
    )
    _write_executable(
        cargo,
        """#!/bin/sh
printf '%s\n' "$*" >"$TEST_CARGO_ARGS"
if [ "${TEST_CARGO_FAIL:-0}" = 1 ]; then
  exit 1
fi
mkdir -p "$HOME/.local/bin"
cp "$TEST_CARGO_BINARY" "$HOME/.local/bin/onejudge"
chmod +x "$HOME/.local/bin/onejudge"
""",
    )


def _run_onejudge_install(tmp_path: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    tools = tmp_path / "tools"
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tools}:/usr/bin:/bin",
        "TEST_CURL_ARGS": str(tmp_path / "curl.args"),
        "TEST_CARGO_ARGS": str(tmp_path / "cargo.args"),
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


def _write_release_installer(path: Path) -> None:
    _write_executable(
        path,
        """#!/bin/sh
set -eu
printf '%s\n' "$ONEJUDGE_VERSION" >"$TEST_INSTALL_VERSION"
mkdir -p "$ONEJUDGE_INSTALL_DIR"
cp "$TEST_ARCHIVE_BINARY" "$ONEJUDGE_INSTALL_DIR/onejudge"
chmod +x "$ONEJUDGE_INSTALL_DIR/onejudge"
""",
    )


def test_install_onejudge_skips_compliant_binary(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    _write_onejudge(tmp_path / ".local" / "bin" / "onejudge", "0.3.0")

    proc = _run_onejudge_install(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "curl.args").exists()
    assert not (tmp_path / "cargo.args").exists()


def test_install_onejudge_upgrades_obsolete_binary_from_pinned_release(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    _write_onejudge(tmp_path / ".local" / "bin" / "onejudge", "0.2.0")
    replacement = tmp_path / "onejudge-0.3.0"
    _write_onejudge(replacement, "0.3.0")
    installer = tmp_path / "release-installer.sh"
    _write_release_installer(installer)

    proc = _run_onejudge_install(
        tmp_path,
        TEST_INSTALLER=str(installer),
        TEST_ARCHIVE_BINARY=str(replacement),
        TEST_INSTALL_VERSION=str(tmp_path / "install.version"),
    )

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / ".local" / "bin" / "onejudge").read_text(encoding="utf-8") == (
        replacement.read_text(encoding="utf-8")
    )
    assert (tmp_path / "install.version").read_text(encoding="utf-8").strip() == "v0.3.0"
    assert (tmp_path / "curl.args").read_text(encoding="utf-8").strip() == (
        "-fsSL https://raw.githubusercontent.com/nickderobertis/onejudge/v0.3.0/install.sh"
    )
    assert not (tmp_path / "cargo.args").exists()


def test_install_onejudge_fallback_pins_and_verifies_crates_io(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    _write_onejudge(tmp_path / ".local" / "bin" / "onejudge", "0.2.0")
    replacement = tmp_path / "cargo-onejudge-0.3.0"
    _write_onejudge(replacement, "0.3.0")

    proc = _run_onejudge_install(
        tmp_path,
        TEST_CURL_FAIL="1",
        TEST_CARGO_BINARY=str(replacement),
    )

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "cargo.args").read_text(encoding="utf-8").strip() == (
        f"install onejudge --version 0.3.0 --features cli --locked --force --root {tmp_path}/.local"
    )
    version = subprocess.run(
        [tmp_path / ".local" / "bin" / "onejudge", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip() == "onejudge 0.3.0"


def test_install_onejudge_rejects_wrong_versions_from_both_paths(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    _write_onejudge(tmp_path / ".local" / "bin" / "onejudge", "0.2.0")
    archive_binary = tmp_path / "archive-onejudge"
    cargo_binary = tmp_path / "cargo-onejudge"
    _write_onejudge(archive_binary, "0.4.0")
    _write_onejudge(cargo_binary, "0.2.0")
    installer = tmp_path / "release-installer.sh"
    _write_release_installer(installer)

    proc = _run_onejudge_install(
        tmp_path,
        TEST_INSTALLER=str(installer),
        TEST_ARCHIVE_BINARY=str(archive_binary),
        TEST_INSTALL_VERSION=str(tmp_path / "install.version"),
        TEST_CARGO_BINARY=str(cargo_binary),
    )

    assert proc.returncode == 1
    assert "expected 'onejudge 0.3.0', got 'onejudge 0.4.0'" in proc.stderr
    assert "expected 'onejudge 0.3.0', got 'onejudge 0.2.0'" in proc.stderr
    assert "required onejudge 0.3.0 is unavailable" in proc.stderr


def test_install_oneharness_skips_compliant_binary(tmp_path: Path) -> None:
    _write_oneharness(tmp_path / ".local" / "bin" / "oneharness", "0.4.0")

    proc = _run_oneharness_install(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "uv.args").exists()


def test_install_oneharness_upgrades_obsolete_binary_from_exact_pypi_release(
    tmp_path: Path,
) -> None:
    _write_oneharness(tmp_path / ".local" / "bin" / "oneharness", "0.3.24")
    replacement = tmp_path / "oneharness-0.4.0"
    _write_oneharness(replacement, "0.4.0")

    proc = _run_oneharness_install(tmp_path, TEST_ONEHARNESS_BINARY=str(replacement))

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "uv.args").read_text(encoding="utf-8").strip() == (
        "tool install --upgrade oneharness-cli==0.4.0"
    )
    version = subprocess.run(
        [tmp_path / ".local" / "bin" / "oneharness", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip() == "oneharness 0.4.0"


def test_install_oneharness_rejects_wrong_version_from_pypi(tmp_path: Path) -> None:
    _write_oneharness(tmp_path / ".local" / "bin" / "oneharness", "0.3.24")
    replacement = tmp_path / "wrong-oneharness"
    _write_oneharness(replacement, "0.4.1")

    proc = _run_oneharness_install(tmp_path, TEST_ONEHARNESS_BINARY=str(replacement))

    assert proc.returncode == 1
    assert "expected 'oneharness 0.4.0', got 'oneharness 0.4.1'" in proc.stderr
    assert "required oneharness 0.4.0 is unavailable" in proc.stderr


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
