"""E2E coverage for the worktree-local session toolchain bootstrap."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

ONEJUDGE_VERSION = (REPO_ROOT / "config" / "onejudge.version").read_text().strip()
ONEHARNESS_VERSION = (REPO_ROOT / "config" / "oneharness.version").read_text().strip()


@functools.cache
def _shared_uv_cache() -> str | None:
    """This host's real uv download cache, or `None` when uv cannot name one.

    Every test here points `HOME` at its own `tmp_path`, which is what keeps the
    isolation honest — session setup writes into `$HOME` and must not touch the
    developer's. But uv derives its download cache from `HOME` too, so each of the
    nine tests re-downloaded every wheel of a locked environment it had just
    downloaded, and the file cost about seventy-five seconds an invocation for
    answers already on disk. The cache is content-addressed and safe to share, and
    `uv sync` stays entirely real: sharing it changes where the wheels come from,
    not whether the sync resolves, builds, and installs them.

    Resolved once, from the ambient PATH, before any test narrows it.
    """
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - the suite cannot run without uv on PATH
        return None
    located = subprocess.run([uv, "cache", "dir"], text=True, capture_output=True, check=False)
    return located.stdout.strip() or None if located.returncode == 0 else None


def _setup_repo(
    tmp_path: Path,
    *,
    adopted_onejudge: str = ONEJUDGE_VERSION,
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
    shutil.copy2(REPO_ROOT / "justfile", repo / "justfile")
    # The sweep is the only orchestrator code session setup runs, so the fixture
    # carries exactly its module and the helpers it imports: procfs identity from
    # `coordination`, and the termination the reap of a finished dispatch's leavings
    # performs from `watchdog`.
    for module in ("scratch.py", "coordination.py", "watchdog.py"):
        shutil.copy2(REPO_ROOT / "orchestrator" / module, package / module)
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
    (config / "onejudge.version").write_text(f"{adopted_onejudge}\n", encoding="utf-8")
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
    shared_cache = _shared_uv_cache()
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
            # A shared cache and a suite that corrupts what it installed cannot both
            # be hardlinks. uv installs by linking out of its cache, so the test
            # below that rewrites an installed `METADATA` to prove version drift is
            # detected rewrote the cache entry — and every other environment on this
            # host linked to the same inode — turning one deliberate corruption into
            # a real broken toolchain. Copying is the difference between sharing
            # downloads and sharing files; it costs a fraction of one download.
            "UV_LINK_MODE": "copy",
            **({"UV_CACHE_DIR": shared_cache} if shared_cache else {}),
        },
    )


def _path_without_uv(tmp_path: Path) -> str:
    """A real PATH with `uv` genuinely absent — nothing here is a double.

    Every executable this returns is the host's own: `bun` is a symlink to the
    real binary `which` just resolved, and `/usr/bin:/bin` are the real system
    directories. What the narrowing removes is `uv`, because the journeys below
    prove what `session-setup.sh` does when `uv` is not installed, and the only
    faithful way to test that is for `uv` to actually not be on PATH.

    So this substitutes no behaviour and stubs no interface: the script still
    crosses every real process boundary it would cross in a session, and still
    fails for the real reason rather than a simulated one.
    """
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
    assert f"at {repo / '.venv' / 'bin' / 'onejudge'}" in installed.stderr
    assert "sweep-scratch: removed" in installed.stderr
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


def test_session_setup_continues_when_scratch_sweep_fails(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    justfile = repo / "justfile"
    justfile.write_text(
        justfile.read_text(encoding="utf-8").replace(
            '@uv run orchestrator-sweep-scratch "$@"',
            "@false",
        ),
        encoding="utf-8",
    )

    result = _run_setup(repo, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "scratch sweep failed; continuing session setup" in result.stderr


def test_session_setup_continues_when_scratch_sweep_is_unavailable(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "justfile").unlink()

    result = _run_setup(repo, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "scratch sweep unavailable; continuing session setup" in result.stderr


def test_session_setup_fails_when_synced_onejudge_misses_adopted_version(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path, adopted_onejudge="99.99.99")

    result = _run_setup(repo, tmp_path)

    assert result.returncode == 1
    assert "onejudge verification failed" in result.stderr
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


def test_session_setup_rejects_missing_distribution_metadata(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    installed = _run_setup(repo, tmp_path)
    assert installed.returncode == 0, installed.stderr
    metadata = next(
        (repo / ".venv").glob("lib/python*/site-packages/oneharness_cli-*.dist-info/METADATA")
    )
    shutil.rmtree(metadata.parent)

    result = _run_setup(repo, tmp_path, path=_path_without_uv(tmp_path))

    assert result.returncode == 1
    assert "oneharness distribution verification failed" in result.stderr
    assert "oneharness-cli metadata is unavailable" in result.stderr
