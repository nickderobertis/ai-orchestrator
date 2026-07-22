"""E2E coverage for the worktree-local session toolchain bootstrap."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

ONEJUDGE_VERSION = (REPO_ROOT / "config" / "onejudge.version").read_text().strip()
ONEHARNESS_VERSION = (REPO_ROOT / "config" / "oneharness.version").read_text().strip()


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _setup_repo(tmp_path: Path, *, sync_fails: bool) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    config = repo / "config"
    scripts.mkdir(parents=True)
    config.mkdir()
    (scripts / "session-setup.sh").write_text(
        (REPO_ROOT / "scripts" / "session-setup.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _executable(scripts / "setup-llmlint.sh", "#!/bin/sh\nexit 0\n")
    (config / "onejudge.version").write_text(f"{ONEJUDGE_VERSION}\n", encoding="utf-8")
    (config / "oneharness.version").write_text(f"{ONEHARNESS_VERSION}\n", encoding="utf-8")

    tools = tmp_path / "tools"
    installed = tmp_path / "installed"
    _executable(installed / "onejudge", f"#!/bin/sh\nprintf 'onejudge {ONEJUDGE_VERSION}\n'\n")
    _executable(
        installed / "oneharness", f"#!/bin/sh\nprintf 'oneharness {ONEHARNESS_VERSION}\n'\n"
    )
    _executable(
        installed / "python",
        "#!/bin/sh\n"
        f'case "$*" in *oneharness-cli*) printf "{ONEHARNESS_VERSION}\\n" ;; '
        f'*) printf "{ONEJUDGE_VERSION}\\n" ;; esac\n',
    )
    _executable(
        tools / "uv",
        f"""#!/bin/sh
printf '%s\n' "$*" >"$TEST_UV_ARGS"
if [ {int(sync_fails)} -eq 1 ]; then exit 1; fi
mkdir -p "$TEST_REPO/.venv/bin"
cp "$TEST_INSTALLED"/* "$TEST_REPO/.venv/bin/"
""",
    )
    _executable(tmp_path / ".local" / "node" / "bin" / "bun", "#!/bin/sh\nprintf '1.2.3\n'\n")
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": f"{tools}:/usr/bin:/bin",
        "TEST_REPO": str(repo),
        "TEST_INSTALLED": str(installed),
        "TEST_UV_ARGS": str(tmp_path / "uv.args"),
    }
    return repo, env


def test_session_setup_syncs_both_pinned_clis_into_its_worktree(tmp_path: Path) -> None:
    repo, env = _setup_repo(tmp_path, sync_fails=False)

    result = subprocess.run(
        ["bash", str(repo / "scripts" / "session-setup.sh")],
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "uv.args").read_text().strip() == f"sync --project {repo}"
    assert (repo / ".venv" / "bin" / "oneharness").is_file()
    assert not (tmp_path / ".local" / "bin" / "oneharness").exists()
    assert f"ready (onejudge: onejudge {ONEJUDGE_VERSION})" in result.stderr


def test_session_setup_surfaces_project_sync_failure(tmp_path: Path) -> None:
    repo, env = _setup_repo(tmp_path, sync_fails=True)

    result = subprocess.run(
        ["bash", str(repo / "scripts" / "session-setup.sh")],
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert (tmp_path / "uv.args").read_text().strip() == f"sync --project {repo}"
    assert "project dependency sync failed" in result.stderr
    assert "required pinned onejudge and oneharness dependencies are unavailable" in result.stderr
