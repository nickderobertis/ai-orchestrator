"""Shell-level tests for session setup behavior."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

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
    (test_repo / "scripts" / "alternate-claude-workspace-trust.sh").write_text(
        (REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh").read_text(encoding="utf-8"),
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


def test_alternate_claude_trust_rejects_invalid_json(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{broken", encoding="utf-8")
    script = REPO_ROOT / "scripts" / "session-setup.sh"

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(script),
            str(config),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "not valid JSON" in result.stderr
    assert config.read_text(encoding="utf-8") == "{broken"


@pytest.mark.parametrize("content", ["[]", '{"projects":[]}'])
def test_alternate_claude_trust_rejects_invalid_config_shape(tmp_path: Path, content: str) -> None:
    config = tmp_path / ".claude.json"
    config.write_text(content, encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            str(config),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "must contain a JSON object" in result.stderr
    assert config.read_text(encoding="utf-8") == content


def test_concurrent_alternate_claude_trust_updates_both_survive(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{}", encoding="utf-8")
    script = REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"
    command = 'source "$1"; mark_alternate_claude_trust "$2" "$3"'
    processes = [
        subprocess.Popen(
            ["bash", "-c", command, "test-trust", str(script), str(config), root],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        )
        for root in ("/first-concurrent-worktree", "/second-concurrent-worktree")
    ]

    results = [process.communicate(timeout=10) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    projects = json.loads(config.read_text(encoding="utf-8"))["projects"]
    assert projects["/first-concurrent-worktree"]["hasTrustDialogAccepted"] is True
    assert projects["/second-concurrent-worktree"]["hasTrustDialogAccepted"] is True


def test_alternate_claude_trust_releases_lock_while_sourcing_caller_remains_alive(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{}", encoding="utf-8")
    script = REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    caller = subprocess.Popen(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /first; echo READY; read -r',
            "test-trust",
            str(script),
            str(config),
        ],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert caller.stdout is not None
    assert caller.stdout.readline().strip() == "READY"

    contender = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /second',
            "test-trust",
            str(script),
            str(config),
        ],
        text=True,
        capture_output=True,
        timeout=5,
        env=env,
    )

    assert contender.returncode == 0, contender.stderr
    assert caller.poll() is None
    projects = json.loads(config.read_text(encoding="utf-8"))["projects"]
    assert projects["/first"]["hasTrustDialogAccepted"] is True
    assert projects["/second"]["hasTrustDialogAccepted"] is True
    assert caller.stdin is not None
    caller.stdin.write("done\n")
    caller.stdin.flush()
    assert caller.wait(timeout=5) == 0
    assert caller.stderr is not None
    assert caller.stderr.read() == ""


def test_alternate_claude_trust_reports_missing_jq(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{}", encoding="utf-8")
    script = REPO_ROOT / "scripts" / "session-setup.sh"

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; PATH=/missing; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(script),
            str(config),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "jq is unavailable" in result.stderr


def test_alternate_claude_trust_reports_missing_flock(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{}", encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "jq").symlink_to("/usr/bin/jq")

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; PATH="$3"; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            str(config),
            str(tools),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "flock is unavailable" in result.stderr
    assert "install util-linux" in result.stderr


@pytest.mark.parametrize(
    ("function", "message"),
    [
        (
            "mark_alternate_claude_trust",
            "configuration path and at least one workspace path are required",
        ),
        (
            "mark_alternate_claude_workspaces",
            "caller name and at least one workspace path are required",
        ),
    ],
)
def test_alternate_claude_trust_requires_function_arguments(
    tmp_path: Path, function: str, message: str
) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; "$2"',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            function,
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 2
    assert message in result.stderr


@pytest.mark.parametrize(
    ("function", "first_argument"),
    [
        ("mark_alternate_claude_trust", "/config"),
        ("mark_alternate_claude_workspaces", "test-caller"),
    ],
)
def test_alternate_claude_trust_requires_workspace_argument(
    tmp_path: Path, function: str, first_argument: str
) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; "$2" "$3"',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            function,
            first_argument,
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 2
    assert "at least one workspace path" in result.stderr


def test_alternate_claude_trust_standalone_resolves_configs_and_marks_roots(
    tmp_path: Path,
) -> None:
    configs = (tmp_path / ".claude-alt", tmp_path / ".claude-alt2")
    for config_dir in configs:
        config_dir.mkdir()
        (config_dir / ".claude.json").write_text("{}", encoding="utf-8")
    roots = (tmp_path / "clone", tmp_path / "worktree")

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            *(str(root) for root in roots),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 0, result.stderr
    for config_dir in configs:
        projects = json.loads((config_dir / ".claude.json").read_text(encoding="utf-8"))["projects"]
        for root in roots:
            assert projects[str(root)]["hasTrustDialogAccepted"] is True


def test_alternate_claude_trust_standalone_resolution_failure_is_nonfatal(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"), "/root"],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": "relative",
        },
    )

    assert result.returncode == 0
    assert "alternate Claude config resolution failed" in result.stderr
    assert "continuing" in result.stderr


def test_alternate_claude_trust_standalone_continues_to_second_config(
    tmp_path: Path,
) -> None:
    configs = (tmp_path / "alternate", tmp_path / "alternate2")
    for config_dir in configs:
        config_dir.mkdir()
    (configs[0] / ".claude.json").write_text("{broken", encoding="utf-8")
    (configs[1] / ".claude.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            "/worktree",
        ],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(configs[0]),
            "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(configs[1]),
        },
    )

    assert result.returncode == 0
    assert "not valid JSON" in result.stderr
    projects = json.loads((configs[1] / ".claude.json").read_text(encoding="utf-8"))["projects"]
    assert projects["/worktree"]["hasTrustDialogAccepted"] is True


def test_alternate_claude_trust_standalone_reports_missing_resolver(tmp_path: Path) -> None:
    script = tmp_path / "alternate-claude-workspace-trust.sh"
    script.write_bytes((REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh").read_bytes())

    result = subprocess.run(
        ["bash", str(script), "/worktree"],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode != 0
    assert "cannot load the alternate config resolver" in result.stderr
    assert "restore scripts/claude-alt-config-dir.sh" in result.stderr


@pytest.mark.parametrize(
    ("script_name", "message"),
    [
        (
            "alternate-claude-workspace-trust.sh",
            "alternate-claude-workspace-trust: cannot resolve its script directory",
        ),
        (
            "claude-workspace-trust.sh",
            "claude-workspace-trust: cannot resolve its script directory",
        ),
    ],
)
def test_claude_trust_entry_point_reports_script_directory_resolution_failure(
    tmp_path: Path, script_name: str, message: str
) -> None:
    tools = tmp_path / "tools"
    _write_executable(tools / "dirname", "#!/bin/sh\nprintf '/missing/script-directory\\n'\n")

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / script_name), "/worktree"],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": f"{tools}:/usr/bin:/bin"},
    )

    assert result.returncode != 0
    assert message in result.stderr
    assert "restore directory access, then retry" in result.stderr


@pytest.mark.parametrize("root", ["", "relative/worktree"])
def test_alternate_claude_trust_rejects_invalid_workspace_path(tmp_path: Path, root: str) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" "$3"',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            str(config),
            root,
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 2
    assert f"workspace path must be a nonempty absolute path: '{root}'" in result.stderr
    assert config.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("lock-open", "cannot open the lock"),
        ("lock-acquire", "cannot lock"),
        ("temporary-file", "cannot create a temporary file"),
    ],
)
def test_alternate_claude_trust_reports_filesystem_failures(
    tmp_path: Path, failure: str, message: str
) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{}", encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    match failure:
        case "lock-open":
            (tmp_path / ".claude.json.trust.lock").mkdir()
        case "lock-acquire":
            _write_executable(tools / "flock", "#!/bin/sh\nexit 23\n")
        case "temporary-file":
            _write_executable(tools / "mktemp", "#!/bin/sh\nexit 24\n")

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            str(config),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": f"{tools}:/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert message in result.stderr
    assert config.read_text(encoding="utf-8") == "{}"


def test_alternate_claude_trust_tolerates_config_removed_under_lock(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{}", encoding="utf-8")
    tools = tmp_path / "tools"
    _write_executable(tools / "flock", '#!/bin/sh\nrm -f "$TEST_CONFIG"\n')

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            str(config),
        ],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{tools}:/usr/bin:/bin",
            "TEST_CONFIG": str(config),
        },
    )

    assert result.returncode == 0, result.stderr
    assert not config.exists()


def test_alternate_claude_trust_reports_jq_update_failure(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{}", encoding="utf-8")
    tools = tmp_path / "tools"
    _write_executable(
        tools / "jq",
        '#!/bin/sh\n[ "$1" = --arg ] && exit 25\nexec /usr/bin/jq "$@"\n',
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            str(config),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": f"{tools}:/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "cannot add /checkout" in result.stderr


def test_alternate_claude_trust_reports_comparison_read_failure(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{}", encoding="utf-8")
    tools = tmp_path / "tools"
    _write_executable(tools / "cmp", "#!/bin/sh\nexit 2\n")

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            str(config),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": f"{tools}:/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert (
        result.stderr == f"alternate-claude-workspace-trust: cannot compare {config} with its "
        "updated configuration; verify both files are readable, then retry\n"
    )
    assert config.read_text(encoding="utf-8") == "{}"


def test_alternate_claude_trust_reports_cleanup_failure(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    config.write_text("{broken", encoding="utf-8")
    tools = tmp_path / "tools"
    _write_executable(tools / "rm", "#!/bin/sh\nexit 26\n")

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh"),
            str(config),
        ],
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": f"{tools}:/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert (
        f"cannot remove temporary files for {config}; fix directory permissions, then retry"
        in result.stderr
    )
    assert config.read_text(encoding="utf-8") == "{broken"


@pytest.mark.parametrize("failure", ["intermediate-mv", "chmod", "final-mv"])
def test_alternate_claude_trust_preserves_config_when_replacement_fails(
    tmp_path: Path, failure: str
) -> None:
    config = tmp_path / ".claude.json"
    original = b'{"theme":"dark"}'
    config.write_bytes(original)
    tools = tmp_path / "tools"
    tools.mkdir()
    _write_executable(
        tools / "mv",
        """#!/bin/sh
if { [ "$TEST_FAILURE" = intermediate-mv ] && case "$1" in *.next) true;; *) false;; esac; } ||
   { [ "$TEST_FAILURE" = final-mv ] && [ "$2" = "$TEST_CONFIG" ]; }; then
  exit 23
fi
exec /usr/bin/mv "$@"
""",
    )
    _write_executable(
        tools / "chmod",
        """#!/bin/sh
[ "$TEST_FAILURE" = chmod ] && exit 24
exec /usr/bin/chmod "$@"
""",
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mark_alternate_claude_trust "$2" /checkout',
            "test-trust",
            str(REPO_ROOT / "scripts" / "session-setup.sh"),
            str(config),
        ],
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{tools}:/usr/bin:/bin",
            "TEST_FAILURE": failure,
            "TEST_CONFIG": str(config),
        },
    )

    assert result.returncode == 1
    assert config.read_bytes() == original
    expected_diagnostic = {
        "intermediate-mv": "cannot advance the temporary config",
        "chmod": "cannot preserve permissions",
        "final-mv": "cannot atomically replace",
    }[failure]
    assert expected_diagnostic in result.stderr
    assert [path for path in tmp_path.glob(".claude.json.trust.*") if path.suffix != ".lock"] == []


def test_legacy_claude_trust_source_loads_helper_and_reports_when_missing(
    tmp_path: Path,
) -> None:
    legacy = REPO_ROOT / "scripts" / "claude-workspace-trust.sh"
    success = subprocess.run(
        ["bash", "-c", 'source "$1"; type mark_alternate_claude_trust', "test", str(legacy)],
        text=True,
        capture_output=True,
    )
    assert success.returncode == 0, success.stderr

    isolated = tmp_path / "scripts"
    isolated.mkdir()
    copied = isolated / legacy.name
    copied.write_bytes(legacy.read_bytes())
    failure = subprocess.run(["bash", str(copied)], text=True, capture_output=True)
    assert failure.returncode != 0
    assert "restore scripts/alternate-claude-workspace-trust.sh" in failure.stderr


def test_full_setup_trusts_its_dispatch_checkout_and_keeps_failure_nonfatal(
    tmp_path: Path,
) -> None:
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    config = alternate / ".claude.json"
    config.write_text('{"theme":"dark"}', encoding="utf-8")

    result = _run_full_setup_without_bun(
        tmp_path, ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR=str(alternate)
    )

    # Bun is deliberately absent from this fixture; trust setup still ran and
    # did not replace the required-tool failure with its own.
    assert result.returncode == 1
    data = json.loads(config.read_text(encoding="utf-8"))
    repo = str(tmp_path / "repo")
    assert data["theme"] == "dark"
    assert data["projects"][repo] == {"hasTrustDialogAccepted": True}


def test_full_setup_trusts_dispatch_checkout_in_default_alternate_config(
    tmp_path: Path,
) -> None:
    alternate = tmp_path / ".claude-alt"
    alternate.mkdir()
    config = alternate / ".claude.json"
    config.write_text('{"theme":"dark"}', encoding="utf-8")

    result = _run_full_setup_without_bun(tmp_path)

    assert result.returncode == 1
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert data["projects"][str(tmp_path / "repo")] == {"hasTrustDialogAccepted": True}


def test_full_setup_accepts_absent_alternate_config(tmp_path: Path) -> None:
    alternate = tmp_path / "alternate"
    alternate.mkdir()

    result = _run_full_setup_without_bun(
        tmp_path, ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR=str(alternate)
    )

    assert result.returncode == 1
    assert not (alternate / ".claude.json").exists()
    assert "workspace trust setup failed" not in result.stderr


def test_full_setup_trusts_its_dispatch_checkout_in_both_alternate_configs(
    tmp_path: Path,
) -> None:
    """Both alternate subscriptions dispatch here, so both must trust this checkout.

    claude-code prompts for workspace trust on first use in a directory, which a
    non-interactive dispatch cannot answer — so an untrusted second account would
    fail exactly when the first one's quota ran out.
    """
    first = tmp_path / "alternate"
    second = tmp_path / "alternate2"
    first.mkdir()
    second.mkdir()
    for alternate in (first, second):
        (alternate / ".claude.json").write_text('{"theme":"dark"}', encoding="utf-8")

    result = _run_full_setup_without_bun(
        tmp_path,
        ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR=str(first),
        ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR=str(second),
    )

    assert result.returncode == 1
    repo = str(tmp_path / "repo")
    for alternate in (first, second):
        data = json.loads((alternate / ".claude.json").read_text(encoding="utf-8"))
        assert data["theme"] == "dark"
        assert data["projects"][repo] == {"hasTrustDialogAccepted": True}


def test_full_setup_trusts_the_default_second_alternate_config(tmp_path: Path) -> None:
    # Nothing exports the indirection on a fresh shell, so the $HOME-derived
    # default is the path that actually gets used.
    second = tmp_path / ".claude-alt2"
    second.mkdir()
    config = second / ".claude.json"
    config.write_text("{}", encoding="utf-8")

    result = _run_full_setup_without_bun(tmp_path)

    assert result.returncode == 1
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["projects"][str(tmp_path / "repo")] == {"hasTrustDialogAccepted": True}


def test_full_setup_accepts_an_absent_second_alternate_config(tmp_path: Path) -> None:
    """The second plan is not authenticated yet, so its config file is simply gone.

    Trust marking must skip it silently rather than report a failure the operator
    cannot act on until they log in.
    """
    first = tmp_path / "alternate"
    second = tmp_path / "alternate2"
    first.mkdir()
    (first / ".claude.json").write_text("{}", encoding="utf-8")

    result = _run_full_setup_without_bun(
        tmp_path,
        ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR=str(first),
        ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR=str(second),
    )

    assert result.returncode == 1
    assert not second.exists()
    assert "workspace trust setup failed" not in result.stderr
    data = json.loads((first / ".claude.json").read_text(encoding="utf-8"))
    assert data["projects"][str(tmp_path / "repo")] == {"hasTrustDialogAccepted": True}


def test_full_setup_continues_after_alternate_trust_failure(tmp_path: Path) -> None:
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    config = alternate / ".claude.json"
    config.write_text("{broken", encoding="utf-8")

    result = _run_full_setup_without_bun(
        tmp_path, ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR=str(alternate)
    )

    assert result.returncode == 1
    assert "alternate Claude workspace trust setup failed; continuing" in result.stderr
    assert "bun is required" in result.stderr


def test_full_setup_continues_after_alternate_config_resolution_failure(
    tmp_path: Path,
) -> None:
    result = _run_full_setup_without_bun(
        tmp_path, ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR="relative/config"
    )

    assert result.returncode == 1
    assert "alternate Claude config path must be absolute" in result.stderr
    assert "alternate Claude config resolution failed; continuing" in result.stderr
    assert "bun is required" in result.stderr


def test_full_setup_trusts_distinct_managed_and_worktree_roots(tmp_path: Path) -> None:
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    config = alternate / ".claude.json"
    config.write_text("{}", encoding="utf-8")
    managed = tmp_path / "managed"
    tools = tmp_path / "git-tools"
    fake_git = tools / "git"
    _write_executable(
        fake_git,
        f"#!/bin/sh\nprintf '%s\\n' '{managed}/.git'\n",
    )

    result = _run_full_setup_without_bun(
        tmp_path,
        ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR=str(alternate),
        PATH=f"{tools}:/usr/bin:/bin",
    )

    assert result.returncode == 1
    projects = json.loads(config.read_text(encoding="utf-8"))["projects"]
    assert projects[str(managed)] == {"hasTrustDialogAccepted": True}
    assert projects[str(tmp_path / "repo")] == {"hasTrustDialogAccepted": True}


def _run_full_setup_without_bun(
    tmp_path: Path, **extra_env: str
) -> subprocess.CompletedProcess[str]:
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
    (scripts / "claude-alt-config-dir.sh").write_text(
        (REPO_ROOT / "scripts" / "claude-alt-config-dir.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (scripts / "alternate-claude-workspace-trust.sh").write_text(
        (REPO_ROOT / "scripts" / "alternate-claude-workspace-trust.sh").read_text(encoding="utf-8"),
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
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", **extra_env},
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
            str(REPO_ROOT / "scripts" / "session-setup.sh"),
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
    assert len(lines) == 2
    assert lines[0].startswith("export PATH=")
    assert f"{tmp_path}/.local/node/bin" in lines[0]
    assert lines[1] == f"export LLMLINT_ONEHARNESS_BIN={REPO_ROOT}/scripts/llmlint-oneharness.sh"


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
            str(REPO_ROOT / "scripts" / "session-setup.sh"),
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
            str(REPO_ROOT / "scripts" / "session-setup.sh"),
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
