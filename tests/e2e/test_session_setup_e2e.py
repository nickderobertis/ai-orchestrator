"""E2E coverage for the worktree-local session toolchain bootstrap."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

ONEJUDGE_VERSION = (REPO_ROOT / "config" / "onejudge.version").read_text().strip()
ONEHARNESS_VERSION = (REPO_ROOT / "config" / "oneharness.version").read_text().strip()


def _setup_repo(
    tmp_path: Path,
    *,
    adopted_oneharness: str = ONEHARNESS_VERSION,
    dependency_oneharness: str = ONEHARNESS_VERSION,
) -> Path:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    config = repo / "config"
    package = repo / "orchestrator"
    scripts.mkdir(parents=True)
    config.mkdir()
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(REPO_ROOT / name, repo / name)
    if dependency_oneharness != ONEHARNESS_VERSION:
        pyproject = repo / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text(encoding="utf-8").replace(
                f"oneharness-cli=={ONEHARNESS_VERSION}",
                f"oneharness-cli=={dependency_oneharness}",
            ),
            encoding="utf-8",
        )
    shutil.copy2(REPO_ROOT / "scripts" / "session-setup.sh", scripts / "session-setup.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "setup-llmlint.sh", scripts / "setup-llmlint.sh")
    (config / "onejudge.version").write_text(f"{ONEJUDGE_VERSION}\n", encoding="utf-8")
    (config / "oneharness.version").write_text(f"{adopted_oneharness}\n", encoding="utf-8")
    return repo


def _run_setup(
    repo: Path, tmp_path: Path, *, path: str | None = None
) -> subprocess.CompletedProcess[str]:
    bun = shutil.which("bun")
    assert bun is not None
    bun_version = subprocess.run(
        [bun, "--version"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    return subprocess.run(
        ["bash", str(repo / "scripts" / "session-setup.sh")],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "ASDF_BUN_VERSION": bun_version,
            "ASDF_DATA_DIR": os.environ.get("ASDF_DATA_DIR", str(Path.home() / ".asdf")),
            "HOME": str(tmp_path),
            "PATH": path or os.environ["PATH"],
        },
    )


def _path_without_uv(tmp_path: Path) -> str:
    bun = shutil.which("bun")
    assert bun is not None
    tools = tmp_path / "real-tools"
    tools.mkdir(exist_ok=True)
    (tools / "bun").symlink_to(Path(bun).resolve())
    return f"{tools}:/usr/bin:/bin"


def test_session_setup_syncs_real_pinned_clis_and_then_needs_no_uv(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)

    installed = _run_setup(repo, tmp_path)

    assert installed.returncode == 0, installed.stderr
    assert (
        subprocess.run(
            [repo / ".venv" / "bin" / "oneharness", "--version"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        == f"oneharness {ONEHARNESS_VERSION}"
    )
    assert not (tmp_path / ".local" / "bin" / "oneharness").exists()

    without_uv = _run_setup(repo, tmp_path, path=_path_without_uv(tmp_path))
    assert without_uv.returncode == 0, without_uv.stderr
    assert "cannot install required project dependencies" not in without_uv.stderr


def test_session_setup_fails_when_synced_cli_misses_adopted_version(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, adopted_oneharness="99.99.99")

    result = _run_setup(repo, tmp_path)

    assert result.returncode == 1
    assert "oneharness verification failed" in result.stderr
    assert "required pinned onejudge and oneharness dependencies are unavailable" in result.stderr


def test_session_setup_surfaces_real_uv_resolution_failure(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, dependency_oneharness="99.99.99")

    result = _run_setup(repo, tmp_path)

    assert result.returncode == 1
    assert "project dependency sync failed" in result.stderr
    assert "required pinned onejudge and oneharness dependencies are unavailable" in result.stderr


def test_session_setup_reports_missing_uv_at_full_entry_point(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)

    result = _run_setup(repo, tmp_path, path=_path_without_uv(tmp_path))

    assert result.returncode == 1
    assert "cannot install required project dependencies: uv is not installed" in result.stderr


def test_session_setup_rejects_corrupt_distribution_metadata(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    installed = _run_setup(repo, tmp_path)
    assert installed.returncode == 0, installed.stderr
    metadata = next(
        (repo / ".venv").glob("lib/python*/site-packages/oneharness_cli-*.dist-info/METADATA")
    )
    metadata.write_text(
        metadata.read_text(encoding="utf-8").replace(
            f"Version: {ONEHARNESS_VERSION}", "Version: 99.99.99"
        ),
        encoding="utf-8",
    )

    result = _run_setup(repo, tmp_path, path=_path_without_uv(tmp_path))

    assert result.returncode == 1
    assert "oneharness distribution verification failed" in result.stderr
    assert f"expected '{ONEHARNESS_VERSION}', got '99.99.99'" in result.stderr
