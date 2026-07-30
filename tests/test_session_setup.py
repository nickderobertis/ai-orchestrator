"""Shell-level tests for session setup behavior."""

from __future__ import annotations

import json
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


def _write_onejudge(path: Path, version: str) -> None:
    _write_executable(path, f"#!/bin/sh\nprintf 'onejudge {version}\\n'\n")


def _write_oneharness(path: Path, version: str) -> None:
    _write_executable(path, f"#!/bin/sh\nprintf 'oneharness {version}\\n'\n")


def _write_bun(path: Path, version: str = "1.2.3") -> None:
    _write_executable(path, f"#!/bin/sh\nprintf '{version}\\n'\n")


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
cp "$TEST_ONEHARNESS_BINARY" "$TEST_REPO/.venv/bin/oneharness"
cp "$TEST_SDK_PYTHON" "$TEST_REPO/.venv/bin/python"
chmod +x "$TEST_REPO/.venv/bin/onejudge" \
  "$TEST_REPO/.venv/bin/oneharness" \
  "$TEST_REPO/.venv/bin/python"
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


def test_alternate_claude_trust_is_idempotent_and_preserves_other_config(tmp_path: Path) -> None:
    config = tmp_path / "alternate" / ".claude.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "theme": "dark",
                "projects": {"/already": {"hasTrustDialogAccepted": False, "other": "kept"}},
            }
        ),
        encoding="utf-8",
    )
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    roots = (tmp_path / "checkout", tmp_path / "worktrees")
    command = 'source "$1"; mark_alternate_claude_trust "$2" "$3" "$4"'
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}

    first = subprocess.run(
        ["bash", "-c", command, "test-trust", str(script), str(config), *(map(str, roots))],
        text=True,
        capture_output=True,
        env=env,
    )
    assert first.returncode == 0, first.stderr
    first_bytes = config.read_bytes()
    second = subprocess.run(
        ["bash", "-c", command, "test-trust", str(script), str(config), *(map(str, roots))],
        text=True,
        capture_output=True,
        env=env,
    )

    assert second.returncode == 0, second.stderr
    assert config.read_bytes() == first_bytes
    data = json.loads(first_bytes)
    assert data["theme"] == "dark"
    assert data["projects"]["/already"] == {
        "hasTrustDialogAccepted": False,
        "other": "kept",
    }
    for root in roots:
        assert data["projects"][str(root)] == {"hasTrustDialogAccepted": True}


def _run_full_setup_without_bun(tmp_path: Path) -> subprocess.CompletedProcess[str]:
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
    (config / "onejudge.version").write_text(f"{ADOPTED_ONEJUDGE_VERSION}\n", encoding="utf-8")
    (config / "oneharness.version").write_text(f"{ADOPTED_ONEHARNESS_VERSION}\n", encoding="utf-8")
    _write_onejudge(test_repo / ".venv" / "bin" / "onejudge", ADOPTED_ONEJUDGE_VERSION)
    _write_sdk_python(test_repo / ".venv" / "bin" / "python", ADOPTED_ONEJUDGE_VERSION)
    _write_oneharness(test_repo / ".venv" / "bin" / "oneharness", ADOPTED_ONEHARNESS_VERSION)
    return subprocess.run(
        ["bash", str(session_setup)],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )


def _write_sdk_python(
    path: Path, onejudge_version: str, oneharness_version: str = ADOPTED_ONEHARNESS_VERSION
) -> None:
    _write_executable(
        path,
        "#!/bin/sh\n"
        f"case \"$*\" in *oneharness-cli*) printf '{oneharness_version}\\n' ;; "
        f"*) printf '{onejudge_version}\\n' ;; esac\n",
    )


def test_project_install_skips_sync_when_both_pinned_tools_are_compliant(tmp_path: Path) -> None:
    _fake_install_commands(tmp_path)
    _write_onejudge(tmp_path / "repo" / ".venv" / "bin" / "onejudge", ADOPTED_ONEJUDGE_VERSION)
    _write_sdk_python(tmp_path / "repo" / ".venv" / "bin" / "python", ADOPTED_ONEJUDGE_VERSION)
    _write_oneharness(
        tmp_path / "repo" / ".venv" / "bin" / "oneharness", ADOPTED_ONEHARNESS_VERSION
    )

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
    assert "required pinned onejudge and oneharness dependencies are unavailable" in proc.stderr


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
    assert "required pinned onejudge and oneharness dependencies" in proc.stderr


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
