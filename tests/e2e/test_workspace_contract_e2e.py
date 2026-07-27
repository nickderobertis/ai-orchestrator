"""E2E coverage for the public Nx workspace command and contract surfaces.

llmlint: ignore-file[e2e_not_mocked,tests_mirror_real_usage] Recipe tests own shell
sequencing, capture, and stop behavior, so uv/Nx/checker subprocesses are deterministic
command doubles; real Bun upgrade, package-consumer layout, contract hashing, and
cross-worktree Nx cache boundaries run separately.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(
    *args: str, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("recipe", "target"),
    [
        ("bootstrap", "run-many -t bootstrap"),
        ("check", "run-many -t format-check,lint,typecheck,test"),
        ("test", "run-many -t test"),
        ("lint", "affected -t lint"),
        ("typecheck", "affected -t typecheck"),
        ("format", "affected -t format"),
        ("format-check", "run-many -t format-check"),
        ("upgrade", "run-many -t build,lint,typecheck,test"),
    ],
)
def test_root_recipe_routes_through_nx(recipe: str, target: str) -> None:
    result = _run("just", "--dry-run", recipe)

    assert result.returncode == 0, result.stderr
    assert f"./scripts/nx.sh {target}" in result.stderr


def test_orchestrator_lint_target_reports_missing_shellcheck(tmp_path: Path) -> None:
    command = json.loads((ROOT / "orchestrator/project.json").read_text())["targets"]["lint"][
        "command"
    ]
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text("#!/bin/bash\nexit 0\n")
    uv.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(binaries)

    result = _run("/bin/bash", "-c", command, env=env)

    assert result.returncode != 0
    assert "shellcheck" in result.stderr
    assert "just bootstrap" in result.stderr


def _contract_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    for relative in (
        "scripts/check-oneharness-ui-contract.sh",
        "config/oneharness-ui.commit",
        "config/oneharness-ui.types.sha256",
        "docs/dag-ui/design.md",
        "docs/dag-ui/oneharness-ui-contract.d.ts",
    ):
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    _run("git", "init", "-q", cwd=checkout)
    return checkout


def _contract_run(checkout: Path, source: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ONEHARNESS_UI_TYPES_URL"] = source.as_uri()
    return _run("bash", "scripts/check-oneharness-ui-contract.sh", cwd=checkout, env=env)


def test_contract_checker_accepts_the_exact_pinned_declaration(tmp_path: Path) -> None:
    checkout = _contract_checkout(tmp_path)
    source = checkout / "docs/dag-ui/oneharness-ui-contract.d.ts"

    result = _contract_run(checkout, source)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "oneharness-ui contract: pinned upstream source verified\n"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("commit", "repair invalid commit pin"),
        ("hash", "update reviewed fixture and hash together"),
        ("mirror", "regenerate the checked-in declaration"),
        ("design", "synchronize design pin"),
    ],
)
def test_contract_checker_reports_actionable_drift(
    tmp_path: Path, mutation: str, message: str
) -> None:
    checkout = _contract_checkout(tmp_path)
    source = tmp_path / "source.ts"
    shutil.copy2(checkout / "docs/dag-ui/oneharness-ui-contract.d.ts", source)
    match mutation:
        case "commit":
            (checkout / "config/oneharness-ui.commit").write_text("invalid\n")
        case "hash":
            (checkout / "config/oneharness-ui.types.sha256").write_text(f"{'0' * 64}\n")
        case "mirror":
            (checkout / "docs/dag-ui/oneharness-ui-contract.d.ts").write_text("drift\n")
        case "design":
            (checkout / "docs/dag-ui/design.md").write_text("missing pin\n")
        case _:
            raise AssertionError(f"unknown contract mutation {mutation!r}")

    result = _contract_run(checkout, source)

    assert result.returncode != 0
    assert message in result.stderr


def test_contract_checker_reports_fetch_failure(tmp_path: Path) -> None:
    checkout = _contract_checkout(tmp_path)

    result = _contract_run(checkout, tmp_path / "missing.ts")

    assert result.returncode != 0
    assert "fetch pinned source and retry" in result.stderr


def test_contract_checker_rejects_malformed_hash_pin(tmp_path: Path) -> None:
    checkout = _contract_checkout(tmp_path)
    source = tmp_path / "source.ts"
    shutil.copy2(checkout / "docs/dag-ui/oneharness-ui-contract.d.ts", source)
    (checkout / "config/oneharness-ui.types.sha256").write_text("invalid\n")

    result = _contract_run(checkout, source)

    assert result.returncode != 0
    assert "repair invalid SHA-256 pin" in result.stderr


def test_contract_checker_rejects_unsupported_source_scheme(tmp_path: Path) -> None:
    checkout = _contract_checkout(tmp_path)
    env = os.environ.copy()
    env["ONEHARNESS_UI_TYPES_URL"] = "ftp://example.invalid/types.ts"

    result = _run("bash", "scripts/check-oneharness-ui-contract.sh", cwd=checkout, env=env)

    assert result.returncode != 0
    assert "source URL must use https:// or file://" in result.stderr


def _dag_state_contract_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "dag-state-contract"
    for relative in (
        "scripts/check-dag-state-contract.py",
        "orchestrator/projection.py",
        "orchestrator/conversations.py",
        "orchestrator/labels.py",
        "orchestrator/launch.py",
        "orchestrator/read_model.py",
        "orchestrator/telemetry.py",
        "orchestrator/server.py",
        "packages/dag-layout/src/index.ts",
        "packages/dag-model/src/index.ts",
        "docs/dag-ui/design.md",
        "docs/dag-ui/oneharness-ui-contract.d.ts",
        "oneharness.judge.toml",
    ):
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        source = ROOT / relative
        if source.exists():
            shutil.copy2(source, target)
    _run("git", "init", "-q", cwd=checkout)
    return checkout


def _dag_state_contract_run(checkout: Path) -> subprocess.CompletedProcess[str]:
    return _run("python3", "scripts/check-dag-state-contract.py", cwd=checkout)


def test_dag_state_contract_checker_accepts_matching_public_states(
    tmp_path: Path,
) -> None:
    checkout = _dag_state_contract_checkout(tmp_path)

    result = _dag_state_contract_run(checkout)

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "dag state contract: Python, TypeScript, docs, and judge config agree\n"
    )


def test_dag_state_contract_checker_reports_typescript_drift(tmp_path: Path) -> None:
    checkout = _dag_state_contract_checkout(tmp_path)
    layout = checkout / "packages/dag-layout/src/index.ts"
    layout.write_text(layout.read_text().replace('"cancelled",', '"paused",'))

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "packages/dag-layout/src/index.ts DAG_NODE_STATES" in result.stderr
    assert "orchestrator/projection.py NodeState" in result.stderr
    assert "reconcile the TypeScript list with the Python projection states" in result.stderr


def test_dag_state_contract_checker_reports_usage_field_drift(tmp_path: Path) -> None:
    """Renaming a usage field in Python alone must fail, not silently break the UI."""
    checkout = _dag_state_contract_checkout(tmp_path)
    conversations = checkout / "orchestrator/conversations.py"
    conversations.write_text(
        conversations.read_text().replace('"cost_usd": "costUsd"', '"cost_usd": "costUSD"')
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "ConversationUsage rename targets" in result.stderr
    assert "costUSD" in result.stderr
    assert "reconcile them in one change" in result.stderr


def test_dag_state_contract_checker_reports_sse_event_drift(tmp_path: Path) -> None:
    """An SSE event renamed in the server alone must fail against the design contract."""
    checkout = _dag_state_contract_checkout(tmp_path)
    server = checkout / "orchestrator/server.py"
    server.write_text(
        server.read_text().replace('RUN_REMOVED = "run.removed"', 'RUN_REMOVED = "run.deleted"')
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "SSE event vocabulary" in result.stderr
    assert "run.deleted" in result.stderr
    assert "docs/dag-ui/design.md" in result.stderr


def _recipe_checkout(tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "recipes"
    scripts = checkout / "scripts"
    binaries = checkout / "bin"
    scripts.mkdir(parents=True)
    binaries.mkdir()
    shutil.copy2(ROOT / "justfile", checkout / "justfile")
    trace = checkout / "trace"
    command = """#!/usr/bin/env bash
set -euo pipefail
printf '%s %s\\n' "$(basename "$0")" "$*" >>"$TRACE_FILE"
if [[ "${FAIL_COMMAND:-}" == "$(basename "$0")" ]]; then
  echo "$(basename "$0"): captured failure detail" >&2
  exit 9
fi
"""
    uv = binaries / "uv"
    uv.write_text(command)
    uv.chmod(0o755)
    nx = scripts / "nx.sh"
    nx.write_text(command)
    nx.chmod(0o755)
    for name in ("check-oneharness-ui-contract.sh", "check-nx-cache.sh"):
        path = scripts / name
        path.write_text(command)
        path.chmod(0o755)
    python = binaries / "python3"
    python.write_text(command)
    python.chmod(0o755)
    return checkout, trace


def _recipe_run(
    checkout: Path, trace: Path, recipe: str, *, fail_command: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{checkout / 'bin'}:{env['PATH']}"
    env["TRACE_FILE"] = str(trace)
    if fail_command is not None:
        env["FAIL_COMMAND"] = fail_command
    return _run("just", recipe, cwd=checkout, env=env)


def _add_bun_install_double(checkout: Path) -> None:
    bun = checkout / "bin/bun"
    bun.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'bun %s\\n' "$*" >>"$TRACE_FILE"
if [[ "${FAIL_COMMAND:-}" == "bun" ]]; then
  echo "bun: captured failure detail" >&2
  exit 9
fi
mkdir -p node_modules/.bin
touch node_modules/.bin/nx
chmod +x node_modules/.bin/nx
"""
    )
    bun.chmod(0o755)


def _mark_nx_installed(checkout: Path) -> None:
    nx = checkout / "node_modules/.bin/nx"
    nx.parent.mkdir(parents=True)
    nx.touch()
    nx.chmod(0o755)


def test_check_recipe_installs_locked_dependencies_when_nx_is_absent(
    tmp_path: Path,
) -> None:
    checkout, trace = _recipe_checkout(tmp_path)
    _add_bun_install_double(checkout)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines()[0] == "bun install --frozen-lockfile"


def test_check_recipe_preserves_locked_dependency_install_failure(
    tmp_path: Path,
) -> None:
    checkout, trace = _recipe_checkout(tmp_path)
    _add_bun_install_double(checkout)

    result = _recipe_run(checkout, trace, "check", fail_command="bun")

    assert result.returncode != 0
    assert "bun: captured failure detail" in result.stderr
    assert "check: install locked workspace dependencies" in result.stderr
    assert trace.read_text().splitlines() == ["bun install --frozen-lockfile"]


def test_check_recipe_runs_the_combined_public_journey_with_concise_output(
    tmp_path: Path,
) -> None:
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check: all deterministic checks passed\n"
    assert trace.read_text().splitlines() == [
        "nx.sh run-many -t format-check,lint,typecheck,test",
        "check-oneharness-ui-contract.sh ",
        "python3 ./scripts/check-dag-state-contract.py",
        "check-nx-cache.sh ",
    ]


def test_check_recipe_preserves_captured_nx_failure(tmp_path: Path) -> None:
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)

    result = _recipe_run(checkout, trace, "check", fail_command="nx.sh")

    assert result.returncode != 0
    assert "nx.sh: captured failure detail" in result.stderr
    assert "check: deterministic checks failed" in result.stderr
    assert trace.read_text().splitlines() == ["nx.sh run-many -t format-check,lint,typecheck,test"]


def test_upgrade_recipe_runs_bun_and_reports_one_success_line(tmp_path: Path) -> None:
    checkout, trace = _recipe_checkout(tmp_path)
    shutil.copy2(ROOT / "package.json", checkout / "package.json")
    shutil.copy2(ROOT / "bun.lock", checkout / "bun.lock")

    result = _recipe_run(checkout, trace, "upgrade")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "upgrade: dependencies refreshed and targets passed\n"
    assert trace.read_text().splitlines() == [
        "uv lock --upgrade",
        "uv sync",
        "nx.sh run-many -t build,lint,typecheck,test",
    ]


def test_upgrade_recipe_preserves_bun_failure_and_stops(tmp_path: Path) -> None:
    checkout, trace = _recipe_checkout(tmp_path)
    (checkout / "package.json").write_text("{invalid")

    result = _recipe_run(checkout, trace, "upgrade")

    assert result.returncode != 0
    assert "package.json" in result.stderr
    assert "upgrade: repair dependency constraints or target findings" in result.stderr
    assert trace.read_text().splitlines() == [
        "uv lock --upgrade",
        "uv sync",
    ]


def test_real_cache_check_drives_both_linked_worktrees() -> None:
    result = _run("bash", "scripts/check-nx-cache.sh")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ("nx cache check: cross-worktree hit and broken-input miss verified\n")


def test_dag_state_contract_checker_reports_an_invented_payload_field(tmp_path: Path) -> None:
    """A Python field no contract declares would be served to a client expecting none."""
    checkout = _dag_state_contract_checkout(tmp_path)
    read_model = checkout / "orchestrator/read_model.py"
    read_model.write_text(
        read_model.read_text().replace(
            "    attestations: list[str]", "    attestations: list[str]\n    invented: str"
        )
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "read_model.py Round declares ['invented']" in result.stderr
    assert "add it to the contract or drop it" in result.stderr


def test_dag_state_contract_checker_reports_a_dropped_required_field(tmp_path: Path) -> None:
    """A required contract field the server stops serving leaves a documented gap."""
    checkout = _dag_state_contract_checkout(tmp_path)
    conversations = checkout / "orchestrator/conversations.py"
    conversations.write_text(conversations.read_text().replace("    canContinue: bool\n", "", 1))

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "omits required" in result.stderr
    assert "canContinue" in result.stderr


def test_dag_state_contract_checker_reports_network_default_drift(tmp_path: Path) -> None:
    """A default changed in the server alone leaves the documented one wrong."""
    checkout = _dag_state_contract_checkout(tmp_path)
    server = checkout / "orchestrator/server.py"
    server.write_text(server.read_text().replace("DEFAULT_PORT = 8787", "DEFAULT_PORT = 9999"))

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "default port" in result.stderr
    assert "is 9999 but" in result.stderr
    assert "design.md says 8787" in result.stderr


def test_dag_state_contract_checker_reports_heartbeat_drift(tmp_path: Path) -> None:
    """The documented 15-second SSE heartbeat and the server constant stay together."""
    checkout = _dag_state_contract_checkout(tmp_path)
    server = checkout / "orchestrator/server.py"
    server.write_text(
        server.read_text().replace(
            "DEFAULT_HEARTBEAT_INTERVAL = 15.0", "DEFAULT_HEARTBEAT_INTERVAL = 30.0"
        )
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "SSE heartbeat interval" in result.stderr
    assert "reconcile them in one change" in result.stderr


def test_dag_state_contract_checker_reports_agent_role_drift(tmp_path: Path) -> None:
    checkout = _dag_state_contract_checkout(tmp_path)
    model = checkout / "packages/dag-model/src/index.ts"
    model.write_text(model.read_text().replace('  "check-in",\n', ""))

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "semantic agent roles disagree" in result.stderr


def test_dag_state_contract_checker_reports_judge_config_role_drift(tmp_path: Path) -> None:
    checkout = _dag_state_contract_checkout(tmp_path)
    judge_config = checkout / "oneharness.judge.toml"
    judge_config.write_text(
        judge_config.read_text().replace('agent_role = "judge"', 'agent_role = "worker"')
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "oneharness.judge.toml history_labels.agent_role 'worker' disagrees" in result.stderr
    assert 'restore `agent_role = "judge"`' in result.stderr


def test_dag_state_contract_checker_rejects_duplicate_agent_roles(tmp_path: Path) -> None:
    checkout = _dag_state_contract_checkout(tmp_path)
    model = checkout / "packages/dag-model/src/index.ts"
    model.write_text(model.read_text().replace('  "check-in",\n', '  "check-in",\n  "check-in",\n'))

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "agentRoleSchema must contain unique string roles" in result.stderr


def test_dag_state_contract_checker_reports_telemetry_schema_drift(tmp_path: Path) -> None:
    """The base bumped this 6 -> 7 while the contract still said 6; gate it."""
    checkout = _dag_state_contract_checkout(tmp_path)
    telemetry = checkout / "orchestrator/telemetry.py"
    telemetry.write_text(
        telemetry.read_text().replace(
            "TELEMETRY_SCHEMA_VERSION = 7", "TELEMETRY_SCHEMA_VERSION = 8"
        )
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "telemetry schema version" in result.stderr
    assert "is 8 but" in result.stderr
    assert "reconcile them in one change" in result.stderr
