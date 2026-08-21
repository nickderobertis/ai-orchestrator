"""E2E coverage for the worktree-local session toolchain bootstrap."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from provisioning import (
    ONEHARNESS_VERSION,
    path_without_uv,
    run_setup,
    setup_repo,
)
from published_tools import PUBLISHED_TOOLS


def test_session_setup_syncs_real_pinned_clis_and_then_needs_no_uv(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)

    installed = run_setup(repo, tmp_path)

    assert installed.returncode == 0, installed.stderr
    assert f"at {repo / '.venv' / 'bin' / 'onejudge'}" in installed.stderr
    # Both halves of the composed sweep reach a session, and on a host where every
    # family was examined and nothing was left to act on that is the whole of what it
    # says — one line naming the four families it judged, rather than four sections a
    # reader learns to skim past.
    assert (
        "just sweep: nothing to act on — every family examined: "
        "oneagentgraph runs, temp; onevcs publications, recoveries." in installed.stderr
    )
    assert "=== just sweep — what this run looked at ===" not in installed.stderr
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

    without_uv = run_setup(repo, tmp_path, path=path_without_uv(tmp_path))
    assert without_uv.returncode == 0, without_uv.stderr
    assert "cannot install required project dependencies" not in without_uv.stderr


def test_session_setup_syncs_every_published_tool_from_pypi(tmp_path: Path) -> None:
    """The four adopted pins are real releases a real `uv sync` installs and runs.

    This is the only proof that matters for a pin: the sync resolves each
    distribution from PyPI into a fresh venv, and the CLI it lands reports the
    version `config/<tool>.version` adopted. A version nobody published, a git ref,
    or a vendored copy cannot survive it.
    """
    repo = setup_repo(tmp_path)

    installed = run_setup(repo, tmp_path)

    assert installed.returncode == 0, installed.stderr
    for tool in PUBLISHED_TOOLS:
        executable = repo / ".venv" / "bin" / tool.binary
        assert f"ready ({tool.binary}: {tool.adopted_version} at {executable})" in installed.stderr
        reported = subprocess.run(
            [executable, "--version"], text=True, capture_output=True, check=True
        )
        assert reported.stdout.strip() == f"{tool.binary} {tool.adopted_version}"


def test_session_setup_fails_when_a_published_tool_misses_its_adopted_version(
    tmp_path: Path,
) -> None:
    """A published tool is held to its adopted release exactly as oneharness is."""
    stale = PUBLISHED_TOOLS[0]
    repo = setup_repo(tmp_path, adopted_published={stale.version_file: "99.99.99"})

    result = run_setup(repo, tmp_path)

    assert result.returncode == 1
    assert (
        f"{stale.binary} verification failed: expected '{stale.binary} 99.99.99', "
        f"got '{stale.binary} {stale.adopted_version}'"
    ) in result.stderr
    assert (
        "required pinned onejudge, oneharness, and published-tool dependencies are unavailable"
        in result.stderr
    )


def test_session_setup_fails_when_synced_cli_misses_adopted_version(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path, adopted_oneharness="99.99.99")

    result = run_setup(repo, tmp_path)

    assert result.returncode == 1
    assert "oneharness verification failed" in result.stderr
    assert (
        "required pinned onejudge, oneharness, and published-tool dependencies are unavailable"
        in result.stderr
    )


def test_session_setup_continues_when_the_workspace_sweep_fails(tmp_path: Path) -> None:
    """A real failing sweep, produced by real state rather than by replacing the recipe.

    Substituting `false` for the recipe body proved only that session setup survives a
    non-zero exit; it proved nothing about the artifact that produces one, and it would
    have gone on passing if the wrapper had stopped exiting non-zero at all. A
    `workspaces` that is a file is what a half-provisioned or hand-edited onevcs state
    root looks like, and the real verb refuses it — so this drives the real recipe, the
    real wrapper, and both real verbs, and reads the failure they actually produce.
    """
    repo = setup_repo(tmp_path)
    # `HOME` is `tmp_path`, so this is the state root the real `onevcs sweep` reads.
    (tmp_path / ".onevcs").mkdir()
    (tmp_path / ".onevcs" / "workspaces").write_text("not a directory\n", encoding="utf-8")

    result = run_setup(repo, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "cannot read the workspaces under" in result.stderr
    # One verb down never costs the other its reclamation, and never leaves its own
    # families out of both lists — the property session setup's sweep is worth running
    # for at all.
    assert "sweep: examined family" in result.stderr
    assert "onevcs publications, recoveries — onevcs sweep exited 2" in result.stderr
    assert "workspace sweep failed; continuing session setup" in result.stderr


def test_session_setup_continues_when_the_workspace_sweep_is_unavailable(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    (repo / "justfile").unlink()

    result = run_setup(repo, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "workspace sweep unavailable; continuing session setup" in result.stderr


def test_session_setup_fails_when_synced_onejudge_misses_adopted_version(
    tmp_path: Path,
) -> None:
    repo = setup_repo(tmp_path, adopted_onejudge="99.99.99")

    result = run_setup(repo, tmp_path)

    assert result.returncode == 1
    assert "onejudge verification failed" in result.stderr
    assert (
        "required pinned onejudge, oneharness, and published-tool dependencies are unavailable"
        in result.stderr
    )


def test_session_setup_surfaces_real_uv_resolution_failure(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path, dependency_oneharness="99.99.99")

    result = run_setup(repo, tmp_path)

    assert result.returncode == 1
    assert "project dependency sync failed" in result.stderr
    assert (
        "required pinned onejudge, oneharness, and published-tool dependencies are unavailable"
        in result.stderr
    )


def test_sessionsetup_reports_missing_uv_at_full_entry_point(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)

    result = run_setup(repo, tmp_path, path=path_without_uv(tmp_path))

    assert result.returncode == 1
    assert "cannot install required project dependencies: uv is not installed" in result.stderr


def test_session_setup_rejects_corrupt_distribution_metadata(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    installed = run_setup(repo, tmp_path)
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

    result = run_setup(repo, tmp_path, path=path_without_uv(tmp_path))

    assert result.returncode == 1
    assert "oneharness distribution verification failed" in result.stderr
    assert f"expected '{ONEHARNESS_VERSION}', got '99.99.99'" in result.stderr


def test_session_setup_rejects_missing_distribution_metadata(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    installed = run_setup(repo, tmp_path)
    assert installed.returncode == 0, installed.stderr
    metadata = next(
        (repo / ".venv").glob("lib/python*/site-packages/oneharness_cli-*.dist-info/METADATA")
    )
    shutil.rmtree(metadata.parent)

    result = run_setup(repo, tmp_path, path=path_without_uv(tmp_path))

    assert result.returncode == 1
    assert "oneharness distribution verification failed" in result.stderr
    assert "oneharness-cli metadata is unavailable" in result.stderr
