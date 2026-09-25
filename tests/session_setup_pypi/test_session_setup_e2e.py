"""E2E coverage for the worktree-local session toolchain bootstrap.

Every journey here runs the real `scripts/session-setup.sh` in a fixture repository
`tests/e2e/provisioning.py` builds, and that script's real `uv sync` installs the
adopted published tools from PyPI. That is a cost and an outside service no ordinary
edit should pay for, so this module belongs to the `session-setup-pypi` project, whose
one target no selection `just check` makes can reach: keyed on what the journeys copy
and run, and run by `just test` and `just upgrade`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import onejudge_bundle
import pytest
from onejudge_bundle import LoopbackOrigin
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
    # This checkout carries no `config/onemessagebus.yaml`, so the warm step's fetch fails
    # whole and reports no link at all — which setup says, and survives.
    assert "schema cache: no link warmed:" in installed.stderr, installed.stderr
    # Both sweep verbs reach a session, each printing its own report, and this fixture's
    # `HOME` and `TMPDIR` put every family they judge inside `tmp_path`.
    assert 'sweep: examined family "runs"' in installed.stderr, installed.stderr
    assert "This answers for the publication and recovery workspaces onevcs owns" in (
        installed.stderr
    ), installed.stderr
    # The last step reached the registered sibling checkouts through the recipe, and
    # under this fixture's `HOME` every listed `~/` path is absent, so each one was
    # skipped and no sibling's bootstrap ran — which is the report a host holding none
    # of them gets, and what keeps this journey off the real siblings.
    assert re.search(
        r"^repos-bootstrap: 0 ran, 0 unchanged, [1-9]\d* skipped, 0 refused, 0 failed$",
        installed.stderr,
        re.MULTILINE,
    ), installed.stderr
    assert "sibling checkout bootstrap unavailable" not in installed.stderr
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


# llmlint: ignore-block[tests_mirror_real_usage] Each journey here still drives the real
# `scripts/session-setup.sh` entry point end to end; what it plants is the state that
# script finds on disk — a workspace root it cannot sweep, installed metadata that is
# wrong or gone — because no command reaches those failure paths except a host in that
# state, and the assertions read only what the script itself reports and exits with.
def test_session_setup_continues_when_the_workspace_sweep_fails(tmp_path: Path) -> None:
    """A real failing sweep, produced by real state rather than by replacing the recipe.

    Substituting `false` for the recipe body proved only that session setup survives a
    non-zero exit; it proved nothing about the artifact that produces one, and it would
    have gone on passing if the recipe had stopped exiting non-zero at all. A
    `workspaces` that is a file is what a half-provisioned or hand-edited onevcs state
    root looks like, and the real verb refuses it — so this drives the real recipe and
    both real verbs, and reads the failure they actually produce.
    """
    repo = setup_repo(tmp_path)
    # `HOME` is `tmp_path`, so this is the state root the real `onevcs sweep` reads.
    (tmp_path / ".onevcs").mkdir()
    (tmp_path / ".onevcs" / "workspaces").write_text("not a directory\n", encoding="utf-8")

    result = run_setup(repo, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "cannot read the workspaces under" in result.stderr
    # One verb down never costs the other its reclamation — the property session
    # setup's sweep is worth running for at all.
    assert "sweep: examined family" in result.stderr
    assert "workspace sweep failed; continuing session setup" in result.stderr


# llmlint: ignore-end[tests_mirror_real_usage]


def test_session_setup_continues_when_the_workspace_sweep_is_unavailable(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    (repo / "justfile").unlink()

    result = run_setup(repo, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "workspace sweep unavailable; continuing session setup" in result.stderr
    # The sibling step is reached through the same justfile, and is the same kind of
    # absence: reported, and never this session's exit status.
    assert "sibling checkout bootstrap unavailable; continuing session setup" in result.stderr


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


# llmlint: ignore-block[tests_mirror_real_usage] Each journey here still drives the real
# `scripts/session-setup.sh` entry point end to end; what it plants is the state that
# script finds on disk — a workspace root it cannot sweep, installed metadata that is
# wrong or gone — because no command reaches those failure paths except a host in that
# state, and the assertions read only what the script itself reports and exits with.
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


# llmlint: ignore-end[tests_mirror_real_usage]


def test_session_setup_warms_every_schema_link_and_reports_each_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every link `config/onemessagebus.yaml` names is warmed, and each is reported by name.

    The configuration is this host's own with its one link pointed at a loopback origin
    the journey serves, beside a `file://` link to the same bundle and a third whose bundle
    this host's pin refuses. The first setup reports the version it stored for the served
    link (`fetched`), the file it read (`read`), and that it could not warm the refused one,
    and still succeeds although the warming fetch itself exited non-zero, as `onemessagebus
    schemas fetch` does when any link is refused. A second setup revalidates the stored
    entry against the origin (`confirmed`), and a third, with the origin gone, keeps it and
    says so (`reused`) — the cache is a speed-up, and a host that went offline still sets
    up. The cache is the journey's own, empty until the first setup, and nothing leaves the
    host.
    """
    repo = setup_repo(tmp_path)
    pin = onejudge_bundle.protocol()
    origin = LoopbackOrigin(json.dumps(onejudge_bundle.bundle()).encode())
    local = onejudge_bundle.write_bundle(tmp_path / "frames.json")
    refused = onejudge_bundle.write_bundle(tmp_path / "next.json", version=str(int(pin) + 1))
    served, read, cold = (
        f"{origin.url}@{pin}",
        f"file://{local}@{pin}",
        f"file://{refused}@{pin}",
    )
    config = onejudge_bundle.relinked_copy(repo / "config" / "onemessagebus.yaml", served)
    text = config.read_text(encoding="utf-8")
    linked = "".join(f'\n  - "{link}"' for link in (read, cold))
    config.write_text(text.replace(f'  - "{served}"', f'  - "{served}"{linked}'))
    cache = tmp_path / "schema-cache"
    monkeypatch.setenv(onejudge_bundle.CACHE_DIR_ENV, str(cache))
    try:
        first = run_setup(repo, tmp_path)
        second = run_setup(repo, tmp_path)
    finally:
        origin.server.shutdown()
    offline = run_setup(repo, tmp_path)

    for setup, outcome in ((first, "fetched"), (second, "confirmed"), (offline, "reused")):
        assert setup.returncode == 0, setup.stderr
        warmed = f"schema cache: {served} warmed, bundle version {pin} ({outcome})"
        assert warmed in setup.stderr, setup.stderr
        assert f"schema cache: {read} warmed, bundle version {pin} (read)" in setup.stderr
        assert f"schema cache: {cold} could not be warmed" in setup.stderr, setup.stderr
    listed = subprocess.run(
        [repo / ".venv" / "bin" / "onemessagebus", "schemas", "--format", "json"],
        env={**os.environ, onejudge_bundle.CACHE_DIR_ENV: str(cache)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert [(one["url"], one["version"]) for one in json.loads(listed.stdout)["entries"]] == [
        (origin.url, pin)
    ]
