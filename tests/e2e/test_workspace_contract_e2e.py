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
import time
from pathlib import Path

import pytest

# The contracts checked here span the whole tree, and documentation is one of the
# layers they hold together — `docs/dag-ui.md` and `docs/dag-ui/design.md` are
# inputs, not commentary. The module belongs to the whole-workspace tier.
pytestmark = pytest.mark.reads_docs

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
        ("check", "run-many -t format-check,lint,typecheck,test,test-docs"),
        ("test", "run-many -t test,test-docs"),
        ("lint", "affected -t lint"),
        ("typecheck", "affected -t typecheck"),
        ("format", "affected -t format"),
        ("format-check", "run-many -t format-check"),
        ("upgrade", "run-many -t build,lint,typecheck,test,test-docs"),
        ("lint-llm-diff", "run workspace:lint-llm-diff"),
    ],
)
def test_root_recipe_routes_through_nx(recipe: str, target: str) -> None:
    result = _run("just", "--dry-run", recipe)

    assert result.returncode == 0, result.stderr
    assert f"./scripts/nx.sh {target}" in result.stderr


def test_test_recipe_forces_one_tier_to_re_run_through_the_command_surface() -> None:
    """The documented way to re-run a memoized tier is a flag on one invocation.

    A cached test verdict is a recorded answer. When an operator has reason to
    distrust one, the supported lever has to reach Nx from the `just` surface —
    otherwise the only way out is an exported global cache skip, which re-rolls
    every tier from every unrelated command and breaks the checks whose contract
    is cache replay.
    """
    result = _run("just", "--dry-run", "test", "--skip-nx-cache")

    assert result.returncode == 0, result.stderr
    assert "./scripts/nx.sh run-many -t test,test-docs --skip-nx-cache" in result.stderr


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
        "orchestrator/timeline.py",
        "orchestrator/server.py",
        "packages/dag-layout/src/index.ts",
        "packages/dag-model/src/index.ts",
        "apps/dag-ui/vite.config.ts",
        "docs/dag-ui.md",
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


def test_dag_state_contract_checker_reports_timeline_span_drift(tmp_path: Path) -> None:
    """A timeline span kind added in Python alone must fail, not ship unparseable."""
    checkout = _dag_state_contract_checkout(tmp_path)
    timeline = checkout / "orchestrator/timeline.py"
    timeline.write_text(
        timeline.read_text().replace('    "rollup",\n]', '    "rollup",\n    "recovery",\n]')
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "TimelineSpanKind vocabulary" in result.stderr
    assert "recovery" in result.stderr
    assert "packages/dag-model/src/index.ts timelineSpanKindSchema" in result.stderr


def test_dag_state_contract_checker_reports_timeline_payload_drift(tmp_path: Path) -> None:
    """A timeline field invented server-side must fail against the design contract."""
    checkout = _dag_state_contract_checkout(tmp_path)
    timeline = checkout / "orchestrator/timeline.py"
    timeline.write_text(
        timeline.read_text().replace(
            "    kind: TimelineReferenceKind\n    value: str",
            "    kind: TimelineReferenceKind\n    value: str\n    body: str",
        )
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "TimelineReference declares ['body']" in result.stderr
    assert "design.md does not" in result.stderr


def test_dag_state_contract_checker_reports_dag_ui_proxy_drift(tmp_path: Path) -> None:
    """A UI proxying somewhere the server does not bind must fail before it ships."""
    checkout = _dag_state_contract_checkout(tmp_path)
    config = checkout / "apps/dag-ui/vite.config.ts"
    config.write_text(config.read_text().replace("127.0.0.1:8787", "127.0.0.1:9999"))

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "default port" in result.stderr
    assert "apps/dag-ui/vite.config.ts proxy default says 9999" in result.stderr


def test_dag_state_contract_checker_reports_dag_ui_documented_port_drift(
    tmp_path: Path,
) -> None:
    """Operator documentation that names a port the app does not serve must fail."""
    checkout = _dag_state_contract_checkout(tmp_path)
    doc = checkout / "docs/dag-ui.md"
    doc.write_text(doc.read_text().replace("`http://127.0.0.1:4173`", "`http://127.0.0.1:4999`"))

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "DAG UI development port" in result.stderr
    assert "docs/dag-ui.md says 4999" in result.stderr


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
if [[ "$*" == "run coverage report --format=total" ]]; then
  printf '%s\\n' "${FAKE_COVERAGE_TOTAL:-}"
  exit "${FAKE_COVERAGE_EXIT:-0}"
fi
if [[ "${ECHO_COMMAND:-}" == "$(basename "$0")" ]]; then echo "$ECHO_LINE"; fi
if [[ "${BLOCK_COMMAND:-}" == "$(basename "$0")" ]]; then
  while [[ ! -e "$BLOCK_UNTIL" ]]; do sleep 0.05; done
fi
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
    # The log preservation and coverage readout under test are the real ones; only
    # the checkers and package managers they wrap are doubled.
    for name in ("preserved-log.sh", "coverage-total.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    return checkout, trace


def _recipe_env(checkout: Path, trace: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{checkout / 'bin'}:{env['PATH']}"
    env["TRACE_FILE"] = str(trace)
    env.update(overrides)
    return env


def _recipe_run(
    checkout: Path,
    trace: Path,
    recipe: str,
    *args: str,
    fail_command: str | None = None,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    if fail_command is not None:
        overrides["FAIL_COMMAND"] = fail_command
    return _run("just", recipe, *args, cwd=checkout, env=_recipe_env(checkout, trace, **overrides))


def _gate_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A recipe checkout `just gate` can run in: a real repo with `origin/main`."""
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)
    shutil.copy2(ROOT / "scripts/comparison-base.sh", checkout / "scripts/comparison-base.sh")
    verdict = checkout / "scripts/llmlint-verdict.sh"
    verdict.write_text((checkout / "scripts/nx.sh").read_text())
    verdict.chmod(0o755)
    llmlint = checkout / "bin/llmlint"
    llmlint.write_text((checkout / "scripts/nx.sh").read_text())
    llmlint.chmod(0o755)
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.name", "test"),
        ("config", "user.email", "test.invalid"),
        ("remote", "add", "origin", "https://example.invalid/gate-recipe.git"),
        ("add", "-A"),
        ("-c", "commit.gpgsign=false", "commit", "-qm", "fixture"),
    ):
        subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout, check=True, text=True, capture_output=True
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", head],
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    return checkout, trace


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
        "nx.sh run-many -t format-check,lint,typecheck,test,test-docs",
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
    assert trace.read_text().splitlines() == [
        "nx.sh run-many -t format-check,lint,typecheck,test,test-docs"
    ]


def test_check_recipe_leaves_the_failing_run_readable_after_it_exits(tmp_path: Path) -> None:
    """The diagnosis outlives the process: `cat .logs/check.log` still answers."""
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)

    result = _recipe_run(checkout, trace, "check", fail_command="nx.sh")

    assert result.returncode != 0
    log = checkout / ".logs/check.log"
    assert f"full output: {log}" in result.stderr
    assert "nx.sh: captured failure detail" in log.read_text()
    assert oct(log.stat().st_mode & 0o777) == "0o600"


def test_check_recipe_log_is_readable_while_the_recipe_is_still_running(
    tmp_path: Path,
) -> None:
    """A stalled run is diagnosable by reading its log, not its file descriptors."""
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)
    release = tmp_path / "release"
    env = _recipe_env(
        checkout,
        trace,
        ECHO_COMMAND="nx.sh",
        ECHO_LINE="running the deterministic tier",
        BLOCK_COMMAND="nx.sh",
        BLOCK_UNTIL=str(release),
    )
    log = checkout / ".logs/check.log"

    process = subprocess.Popen(
        ["just", "check"], cwd=checkout, env=env, text=True, stdout=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if log.exists() and "running the deterministic tier" in log.read_text():
                break
            time.sleep(0.05)
        else:  # pragma: no cover - only reached when the log never materializes
            pytest.fail("the running recipe's log never became readable")
        assert process.poll() is None
    finally:
        release.touch()
        process.communicate(timeout=60)

    assert process.returncode == 0


def _nx_nesting_checkout(tmp_path: Path) -> Path:
    """A checkout the *real* `scripts/nx.sh` runs in, with `bunx` doubled.

    Only Nx itself is replaced. `nx.sh` and `preserved-log.sh` are the real files,
    because the destination they choose is what is under test.
    """
    checkout = tmp_path / "nesting"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "bin").mkdir()
    for name in ("nx.sh", "preserved-log.sh"):
        shutil.copy2(ROOT / "scripts" / name, checkout / "scripts" / name)
        (checkout / "scripts" / name).chmod(0o755)
    # `nx.sh` derives its shared cache key from the repository identity.
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/nx-nesting.git"],
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    return checkout


def test_a_nested_nx_run_cannot_erase_the_running_one_s_log(tmp_path: Path) -> None:
    """The running check's log survives a nested Nx invocation in the same checkout.

    This is the exact shape that made the deterministic path unsafe in this
    repository: `just check` runs the suite, and the suite runs `just lint-llm-diff`
    against this same checkout, so a second `scripts/nx.sh` resolved the very
    `.logs/nx.log` the outer one was still writing and truncated it — leaving a
    running check uninspectable at the moment a reader needs it.

    So the doubled `bunx` here does what pytest does to its parent: it invokes
    `scripts/nx.sh` again, in the same checkout, from inside the outer run's own
    process tree. The outer log has to still hold what it wrote *before* the nested
    run, and go on to hold what it writes after.
    """
    checkout = _nx_nesting_checkout(tmp_path)
    nested_started = tmp_path / "nested.started"
    release = tmp_path / "release"
    bunx = checkout / "bin" / "bunx"
    bunx.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        # The nested run fails, so it also has to *name* where its own evidence
        # went — a diverted log nobody can find would be no better than a lost one.
        'if [[ -n "${NX_NESTING_INNER:-}" ]]; then\n'
        '  echo "inner nx ran" >&2\n'
        "  exit 1\n"
        "fi\n"
        'echo "outer line before the nested run"\n'
        # The nested invocation, inheriting this process tree's environment
        # exactly as the suite's own `just lint-llm-diff` does.
        f'NX_NESTING_INNER=1 "{checkout}/scripts/nx.sh" run inner >"{tmp_path}/inner.out" 2>&1'
        " || true\n"
        f'touch "{nested_started}"\n'
        f'while [[ ! -e "{release}" ]]; do sleep 0.05; done\n'
        'echo "outer line after the nested run"\n',
        encoding="utf-8",
    )
    bunx.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{checkout / 'bin'}:{env['PATH']}"
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    # Whatever claims this process already inherited belong to other checkouts and
    # must not divert anything here; the outer run below is the first claim on it.
    env.pop("ORCHESTRATOR_PRESERVED_LOGS", None)
    outer_log = checkout / ".logs" / "nx.log"

    process = subprocess.Popen(
        [str(checkout / "scripts" / "nx.sh"), "run", "outer"],
        cwd=checkout,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not nested_started.exists():
            assert process.poll() is None, "the outer run exited before nesting"
            time.sleep(0.05)
        assert nested_started.exists(), "the nested run never completed"
        # The moment that used to lose the evidence: the nested run has been and
        # gone while the outer one is still going.
        assert "outer line before the nested run" in outer_log.read_text(encoding="utf-8")
    finally:
        release.touch()
        process.communicate(timeout=60)

    assert process.returncode == 0
    preserved = outer_log.read_text(encoding="utf-8")
    assert "outer line before the nested run" in preserved
    assert "outer line after the nested run" in preserved
    # The nested run is not silenced to achieve that — it gets a log of its own,
    # owner-only like every other, and its failure names where that log went.
    (diverted,) = [path for path in (checkout / ".logs").glob("nx.*.log") if path.name != "nx.log"]
    assert "inner nx ran" in diverted.read_text(encoding="utf-8")
    assert oct(diverted.stat().st_mode & 0o777) == "0o600"
    assert f"full output: {diverted}" in (tmp_path / "inner.out").read_text(encoding="utf-8")
    # And the outer run's evidence never held the nested run's.
    assert "inner nx ran" not in preserved


def test_check_recipe_log_records_the_credential_name_not_its_value(tmp_path: Path) -> None:
    """A preserved log outlives its terminal, so it must never durably hold a token."""
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)
    token = "sk-ant-oat01-not-a-real-credential"

    result = _recipe_run(
        checkout,
        trace,
        "check",
        fail_command="nx.sh",
        CLAUDE_CODE_OAUTH_TOKEN=token,
        ECHO_COMMAND="nx.sh",
        ECHO_LINE=f"CLAUDE_CODE_OAUTH_TOKEN={token}",
    )

    assert result.returncode != 0
    log = (checkout / ".logs/check.log").read_text()
    assert token not in log
    assert "CLAUDE_CODE_OAUTH_TOKEN=<redacted:CLAUDE_CODE_OAUTH_TOKEN>" in log
    assert token not in result.stderr


def test_check_recipe_reports_the_coverage_total_it_measured(tmp_path: Path) -> None:
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)
    (checkout / ".coverage").write_text("")

    result = _recipe_run(checkout, trace, "check", FAKE_COVERAGE_TOTAL="96.42")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check: all deterministic checks passed (line coverage 96.42%)\n"


def test_check_recipe_stays_green_when_no_coverage_artifact_exists(tmp_path: Path) -> None:
    """A missing artifact reports nothing; it must never turn a green tier red."""
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check: all deterministic checks passed\n"
    assert not (checkout / ".coverage").exists()


def test_check_recipe_stays_green_when_the_coverage_total_is_unusable(tmp_path: Path) -> None:
    """An unavailable or malformed total is dropped, not reported and not fatal."""
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)
    (checkout / ".coverage").write_text("")

    result = _recipe_run(checkout, trace, "check", FAKE_COVERAGE_TOTAL="No data to report.")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check: all deterministic checks passed\n"
    assert "uv run coverage report --format=total" in trace.read_text()


def test_check_recipe_reports_a_total_that_coverage_exited_nonzero_to_report(
    tmp_path: Path,
) -> None:
    """`coverage report` exits 2 below the floor and still prints the number.

    Dropping it there would hide the total in exactly the situation an operator
    most wants it; the floor is the `test` target's to enforce, not this readout's.
    """
    checkout, trace = _recipe_checkout(tmp_path)
    _mark_nx_installed(checkout)
    (checkout / ".coverage").write_text("")

    result = _recipe_run(
        checkout, trace, "check", FAKE_COVERAGE_TOTAL="94.13", FAKE_COVERAGE_EXIT="2"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check: all deterministic checks passed (line coverage 94.13%)\n"


def test_gate_recipe_reports_the_coverage_total_it_measured(tmp_path: Path) -> None:
    checkout, trace = _gate_checkout(tmp_path)
    (checkout / ".coverage").write_text("")

    result = _recipe_run(checkout, trace, "gate", "origin", "main", FAKE_COVERAGE_TOTAL="95.07")

    assert result.returncode == 0, result.stderr + result.stdout
    # One success line, carrying both things a passing gate measured: the coverage
    # total, and which llmlint verdict the "green" is a claim about.
    (success,) = [line for line in result.stdout.splitlines() if line.startswith("gate: ")]
    assert success.startswith("gate: complete gate passed (line coverage 95.07%); ")
    assert not [line for line in result.stderr.splitlines() if line.startswith("gate: ")]


def test_gate_recipe_leaves_the_failing_llmlint_run_readable(tmp_path: Path) -> None:
    checkout, trace = _gate_checkout(tmp_path)

    result = _recipe_run(
        checkout, trace, "gate", "origin", "main", fail_command="llmlint-verdict.sh"
    )

    assert result.returncode != 0
    log = checkout / ".logs/gate-llmlint.log"
    assert f"full output: {log}" in result.stderr
    assert "llmlint-verdict.sh: captured failure detail" in log.read_text()


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
        "nx.sh run-many -t build,lint,typecheck,test,test-docs",
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
    """Two real worktrees, a real cache hit and miss, and the failing run's own log.

    The miss half of this check makes the real `scripts/nx.sh` fail, which is the
    only place a genuine `nx.sh` failure happens under the gate — so it is also
    where the preserved log is asserted. That log used to be a `mktemp` file an
    EXIT trap removed.
    """
    result = _run("bash", "scripts/check-nx-cache.sh")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "nx cache check: cross-worktree hit, broken-input miss, "
        "and preserved failure log verified\n"
    )


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
    assert "agentRoleSchema must contain unique string members" in result.stderr


def test_dag_state_contract_checker_reports_telemetry_schema_drift(tmp_path: Path) -> None:
    """The base bumped this 7 -> 8 while the contract still said 7; gate it."""
    checkout = _dag_state_contract_checkout(tmp_path)
    telemetry = checkout / "orchestrator/telemetry.py"
    telemetry.write_text(
        telemetry.read_text().replace(
            "TELEMETRY_SCHEMA_VERSION = 8", "TELEMETRY_SCHEMA_VERSION = 9"
        )
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "telemetry schema version" in result.stderr
    assert "is 9 but" in result.stderr
    assert "reconcile them in one change" in result.stderr


def test_dag_state_contract_checker_reports_provenance_record_drift(tmp_path: Path) -> None:
    """The out-of-repo record's shape is documented; renaming a field alone must fail."""
    checkout = _dag_state_contract_checkout(tmp_path)
    launch = checkout / "orchestrator/launch.py"
    launch.write_text(
        launch.read_text().replace("    launcher_session_id: str", "    renamed_session: str", 1)
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "LaunchProvenance declares ['renamed_session']" in result.stderr


def test_dag_state_contract_checker_reports_provenance_version_drift(tmp_path: Path) -> None:
    """Bumping the record's schema version without the contract must fail."""
    checkout = _dag_state_contract_checkout(tmp_path)
    launch = checkout / "orchestrator/launch.py"
    launch.write_text(
        launch.read_text().replace(
            "PROVENANCE_SCHEMA_VERSION = 1", "PROVENANCE_SCHEMA_VERSION = 2", 1
        )
    )

    result = _dag_state_contract_run(checkout)

    assert result.returncode != 0
    assert "provenance schema version" in result.stderr
    assert "reconcile them in one change" in result.stderr
