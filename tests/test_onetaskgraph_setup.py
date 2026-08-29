"""Exercise the release-archive installer without depending on GitHub availability."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT


def _release_fixture(
    root: Path,
    version: str,
    *,
    valid_checksum: bool = True,
    reported_version: str | None = None,
) -> Path:
    payload = root / "payload"
    payload.mkdir()
    binary = payload / "onetaskgraph"
    binary.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' 'onetaskgraph {reported_version or version}'\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    archive = root / f"onetaskgraph-v{version}-fixture-target.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(binary, arcname="onetaskgraph")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if not valid_checksum:
        digest = "0" * 64
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    return archive


def _run_installer(tmp_path: Path, archive: Path) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    command = r"""
source scripts/session-setup.sh
onetaskgraph_target() { printf '%s\n' fixture-target; }
curl() {
  local destination="${@: -1}" source
  if [[ "$destination" == *.sha256 ]]; then
    source="$ARCHIVE.sha256"
  else
    source="$ARCHIVE"
  fi
  cp "$source" "$destination"
}
install_onetaskgraph || exit $?
first="$(stat -c '%i:%Y:%s' "$HOME/.local/bin/onetaskgraph")"
install_onetaskgraph
second="$(stat -c '%i:%Y:%s' "$HOME/.local/bin/onetaskgraph")"
test "$first" = "$second"
test -d .plans/tasks
test -d .plans/projects
test -d .plans-local/tasks
test -d .plans-local/projects
"""
    return subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(home), "ARCHIVE": str(archive)},
        text=True,
        capture_output=True,
        check=False,
    )


def test_archive_install_is_checksum_verified_and_idempotent(tmp_path: Path) -> None:
    """A valid release installs once; the second call preserves the installed file."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    result = _run_installer(tmp_path, _release_fixture(tmp_path, version))
    assert result.returncode == 0, result.stderr


def test_archive_install_refuses_a_checksum_mismatch(tmp_path: Path) -> None:
    """Downloaded bytes cannot be installed unless the sibling checksum authenticates them."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    result = _run_installer(tmp_path, _release_fixture(tmp_path, version, valid_checksum=False))
    assert result.returncode != 0
    assert "checksum verification failed" in result.stderr
    assert not (tmp_path / "home/.local/bin/onetaskgraph").exists()


def test_archive_install_refuses_a_malformed_checksum_sidecar(tmp_path: Path) -> None:
    """A sidecar without one hexadecimal SHA-256 digest cannot authorize an install."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    archive = _release_fixture(tmp_path, version)
    archive.with_suffix(archive.suffix + ".sha256").write_text("not-a-checksum\n", encoding="utf-8")
    result = _run_installer(tmp_path, archive)
    assert result.returncode != 0
    assert "checksum verification failed" in result.stderr
    assert not (tmp_path / "home/.local/bin/onetaskgraph").exists()


def test_invalid_pin_is_refused_before_session_provisioning(tmp_path: Path) -> None:
    """A malformed standalone-tool release declaration fails by file and value."""
    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
    (repo / "config").mkdir()
    for version_file in (REPO_ROOT / "config").glob("*.version"):
        shutil.copy2(version_file, repo / "config" / version_file.name)
    (repo / "config/onetaskgraph.version").write_text("next\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(repo / "scripts/session-setup.sh")],
        env={**os.environ, "CI": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "invalid adopted onetaskgraph version" in result.stderr
    assert "onetaskgraph.version: 'next'" in result.stderr


def test_unsupported_platform_is_refused_by_name(tmp_path: Path) -> None:
    """A host without a published target gets an attributable refusal."""
    command = r"""
source scripts/session-setup.sh
uname() { if [ "${1:-}" = -m ]; then echo mips; else echo Plan9; fi; }
onetaskgraph_target
"""
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "no release archive for Plan9 mips" in result.stderr


@pytest.mark.parametrize(
    ("system", "machine", "target"),
    (
        ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
        ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
        ("Darwin", "x86_64", "x86_64-apple-darwin"),
        ("Darwin", "arm64", "aarch64-apple-darwin"),
    ),
)
def test_supported_platform_selects_its_release_archive(
    tmp_path: Path, system: str, machine: str, target: str
) -> None:
    """Every platform advertised by the installer resolves through its real case table."""
    command = r"""
source scripts/session-setup.sh
uname() { if [ "${1:-}" = -m ]; then echo "$MACHINE"; else echo "$SYSTEM"; fi; }
onetaskgraph_target
"""
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(tmp_path), "SYSTEM": system, "MACHINE": machine},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == target


def test_archive_download_failure_is_required(tmp_path: Path) -> None:
    """A missing release asset fails installation with its adopted version."""
    command = r"""
source scripts/session-setup.sh
onetaskgraph_target() { echo fixture-target; }
curl() { return 1; }
install_onetaskgraph
"""
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "release archive download failed" in result.stderr


def test_temporary_directory_creation_failure_is_required(tmp_path: Path) -> None:
    """Installation stops before downloading when no private staging directory can be made."""
    command = r"""
source scripts/session-setup.sh
onetaskgraph_target() { echo fixture-target; }
mktemp() { return 1; }
install_onetaskgraph
"""
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert not (tmp_path / ".local/bin/onetaskgraph").exists()


def test_checksum_sidecar_download_failure_is_required(tmp_path: Path) -> None:
    """A downloaded archive is discarded when its checksum sidecar is unavailable."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    archive = _release_fixture(tmp_path, version)
    command = r"""
source scripts/session-setup.sh
onetaskgraph_target() { echo fixture-target; }
curl() {
  local destination="${@: -1}"
  if [[ "$destination" == *.sha256 ]]; then
    return 1
  fi
  cp "$ARCHIVE" "$destination"
}
install_onetaskgraph
"""
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(home), "ARCHIVE": str(archive)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "release archive download failed" in result.stderr
    assert not (home / ".local/bin/onetaskgraph").exists()


def test_shasum_fallback_verifies_and_installs_archive(tmp_path: Path) -> None:
    """macOS-style hosts without sha256sum authenticate the archive with shasum."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    archive = _release_fixture(tmp_path, version)
    home = tmp_path / "home"
    home.mkdir()
    called = tmp_path / "shasum-called"
    command = r"""
source scripts/session-setup.sh
onetaskgraph_target() { echo fixture-target; }
curl() {
  local destination="${@: -1}" source
  if [[ "$destination" == *.sha256 ]]; then source="$ARCHIVE.sha256"; else source="$ARCHIVE"; fi
  cp "$source" "$destination"
}
command() {
  if [[ "$1" == -v && "$2" == sha256sum ]]; then return 1; fi
  builtin command "$@"
}
shasum() {
  test "$1" = -a && test "$2" = 256 || return 2
  touch "$SHASUM_CALLED"
  /usr/bin/sha256sum "$3"
}
install_onetaskgraph
"""
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "HOME": str(home),
            "ARCHIVE": str(archive),
            "SHASUM_CALLED": str(called),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert called.is_file()
    assert (home / ".local/bin/onetaskgraph").is_file()


def test_destination_binary_directory_failure_is_required(tmp_path: Path) -> None:
    """Installation fails clearly when the destination directory cannot be created."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    archive = _release_fixture(tmp_path, version)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".local").write_text("not a directory", encoding="utf-8")
    result = _run_installer(tmp_path, archive)
    assert result.returncode != 0
    assert "release archive installation failed" in result.stderr
    assert not (home / ".local/bin/onetaskgraph").exists()


def test_installed_binary_version_verification_failure_is_required(tmp_path: Path) -> None:
    """A checksum-valid binary that reports the wrong version is not accepted."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    archive = _release_fixture(tmp_path, version, reported_version="0.0.0")
    result = _run_installer(tmp_path, archive)
    assert result.returncode != 0
    assert (tmp_path / "home/.local/bin/onetaskgraph").exists()


def test_archive_missing_expected_binary_is_not_installed(tmp_path: Path) -> None:
    """A valid tar archive without the named CLI payload fails installation."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    payload = tmp_path / "readme"
    payload.write_text("no binary here", encoding="utf-8")
    archive = tmp_path / f"onetaskgraph-v{version}-fixture-target.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="README")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    result = _run_installer(tmp_path, archive)
    assert result.returncode != 0
    assert "release archive installation failed" in result.stderr
    assert not (tmp_path / "home/.local/bin/onetaskgraph").exists()


def test_final_binary_install_failure_is_required(tmp_path: Path) -> None:
    """A destination copy failure is reported even after successful extraction."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    archive = _release_fixture(tmp_path, version)
    home = tmp_path / "home"
    home.mkdir()
    command = r"""
source scripts/session-setup.sh
onetaskgraph_target() { echo fixture-target; }
curl() {
  local destination="${@: -1}" source
  if [[ "$destination" == *.sha256 ]]; then source="$ARCHIVE.sha256"; else source="$ARCHIVE"; fi
  cp "$source" "$destination"
}
install() { return 1; }
install_onetaskgraph
"""
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(home), "ARCHIVE": str(archive)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "release archive installation failed" in result.stderr
    assert not (home / ".local/bin/onetaskgraph").exists()


@pytest.mark.parametrize("blocked", [".plans", ".plans-local"])
def test_plan_root_creation_failure_is_required(tmp_path: Path, blocked: str) -> None:
    """A verified installation still fails if its plan-store roots cannot be created.

    Both roots are created together, and a source refuses a root it cannot canonicalize
    for the whole read rather than for itself alone — so an installation that reported
    success with one of them missing would leave `just plans` exiting 4 on every source.
    """
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    archive = _release_fixture(tmp_path, version)
    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
    (repo / "config").mkdir()
    for version_file in (REPO_ROOT / "config").glob("*.version"):
        shutil.copy2(version_file, repo / "config" / version_file.name)
    (repo / blocked).write_text("not a directory", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    command = r"""
source scripts/session-setup.sh
onetaskgraph_target() { echo fixture-target; }
curl() {
  local destination="${@: -1}" source
  if [[ "$destination" == *.sha256 ]]; then
    source="$ARCHIVE.sha256"
  else
    source="$ARCHIVE"
  fi
  cp "$source" "$destination"
}
install_onetaskgraph
"""
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=repo,
        env={**os.environ, "HOME": str(home), "ARCHIVE": str(archive)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert (home / ".local/bin/onetaskgraph").exists()
    assert "Not a directory" in result.stderr


def test_invalid_archive_is_not_installed(tmp_path: Path) -> None:
    """Checksum-valid bytes must still contain the expected executable payload."""
    version = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    archive = tmp_path / f"onetaskgraph-v{version}-fixture-target.tar.gz"
    archive.write_bytes(b"not a tar archive")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    result = _run_installer(tmp_path, archive)
    assert result.returncode != 0
    assert "release archive installation failed" in result.stderr
