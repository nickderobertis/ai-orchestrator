"""E2E coverage for this repository's own command surface.

Two kinds of journey live here. The quality recipes — `bootstrap`, `check`, `test`,
`gate`, `upgrade` — are proven for the sequencing, capture, and stop behaviour they
own. The delegated recipes are proven for the one thing a wrapper is: that the
published CLI it names is reached, with the arguments the recipe promised.

llmlint: ignore-file[e2e_not_mocked,tests_mirror_real_usage] Recipe tests own shell
sequencing, capture, and stop behavior, so the uv/Nx/CLI subprocesses they wrap are
deterministic command doubles; the real Bun upgrade and the cross-worktree Nx cache
boundary run separately.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from nx_inputs import SELECTED_TARGETS, UNCONDITIONAL_TARGETS
from waits import timeout as e2e_timeout
from waits import until

# Deliberately no module-level tier mark. Most of this file drives `just` recipes
# and shell scripts that never open this repository's prose, and a blanket
# declaration would charge every documentation edit for all of them. Each test
# declares what it actually reads, and `tests/conftest.py` fails one that declares
# wrong.
ROOT = Path(__file__).resolve().parents[3]
#: The two selections `just check` makes, spelled as Nx receives them. Which tiers
#: belong to each is declared once in `tests/nx_inputs.py`, and these journeys hold the
#: recipe to that split by tracing the two invocations it really hands Nx.
CHECK_SELECTED = ",".join(SELECTED_TARGETS)
CHECK_UNCONDITIONAL = ",".join(UNCONDITIONAL_TARGETS)
#: The Nx target lists the other root quality recipes route through, restated here
#: rather than read from the `justfile` — this suite exists to catch one of them
#: drifting. `coverage` is last in each: it waits on the measuring tier and enforces
#: the floor on what that tier wrote.
TEST_TARGETS = "test,test-docs,test-recipes,test-checkouts,test-pypi,coverage"
UPGRADE_TARGETS = f"build,lint,typecheck,{TEST_TARGETS}"


#: The status `scripts/nx-selection.sh` reads as "nothing to narrow against", taken from
#: that script rather than restated, so the journey below reconciles the selector's own
#: constant against what `scripts/comparison-base.sh` really exits with.
NO_BASE_AVAILABLE = int(
    re.search(
        r"^readonly NO_BASE_AVAILABLE=(\d+)$",
        (ROOT / "scripts/nx-selection.sh").read_text(encoding="utf-8"),
        re.MULTILINE,
    ).group(1)
)


def _check_trace(selection: str) -> list[str]:
    """What `just check` hands Nx: the projects a diff chose, then the tiers it cannot."""
    return [
        f"nx.sh {selection} -t {CHECK_SELECTED}",
        f"nx.sh run-many -t {CHECK_UNCONDITIONAL}",
    ]


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
        ("test", f"run-many -t {TEST_TARGETS}"),
        ("lint", "affected -t lint"),
        ("typecheck", "affected -t typecheck"),
        ("format", "affected -t format"),
        ("format-check", "run-many -t format-check"),
        (
            "upgrade",
            f"run-many -t {UPGRADE_TARGETS}",
        ),
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
    forwarded = f"./scripts/nx.sh run-many -t {TEST_TARGETS} --skip-nx-cache"

    result = _run("just", "--dry-run", "test", "--skip-nx-cache")

    assert result.returncode == 0, result.stderr
    assert forwarded in result.stderr


def test_test_e2e_recipe_names_the_real_tier_without_recursing_into_itself() -> None:
    """The e2e tier cannot launch itself from inside itself, but its entry point is exact."""
    result = _run("just", "--dry-run", "test-e2e")

    assert result.returncode == 0, result.stderr
    assert (
        "uv run pytest tests/e2e tests/plan_tooling tests/ask_seam -n 4 --dist loadgroup"
        in result.stderr
    ), result.stderr


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
invocation="$(basename "$0") $*"
if [[ "${FAIL_COMMAND:-}" == "$(basename "$0")" || "${FAIL_INVOCATION:-}" == "$invocation" ]]; then
  echo "$(basename "$0"): captured failure detail" >&2
  exit 9
fi
if [[ -n "${FAIL_INVOCATIONS:-}" ]] && grep -Fxq -- "$invocation" <<<"$FAIL_INVOCATIONS"; then
  echo "$invocation: planted failure" >&2
  exit 9
fi
"""
    uv = binaries / "uv"
    uv.write_text(command)
    uv.chmod(0o755)
    nx = scripts / "nx.sh"
    nx.write_text(command)
    nx.chmod(0o755)
    session_setup = scripts / "session-setup.sh"
    session_setup.write_text(command)
    session_setup.chmod(0o755)
    # The log preservation, coverage readout, and workspace provisioning under test
    # are the real ones; only the checkers and package managers they wrap are doubled.
    for name in (
        "preserved-log.sh",
        "coverage-total.sh",
        "workspace-install.sh",
        # The lock that provisioning takes, which `workspace-install.sh` and its plan-store
        # sibling both source rather than each preparing `.logs` their own way.
        "install-lock.sh",
        # The two that decide which projects `just check` runs over: real, because
        # deciding that is the behaviour these journeys are about.
        "nx-selection.sh",
        "comparison-base.sh",
        # Beside the base a tier is judged against, whether that base's own origin ref
        # has moved past it: doubling either would let a recipe that stopped consulting
        # it pass here.
        "base-freshness.sh",
    ):
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
    # The recipe only checks that llmlint is installed before handing the tier to
    # Nx; the traced `scripts/nx.sh` double above is what stands in for the run.
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


def _add_bun_double(checkout: Path) -> None:
    """Trace Bun, and let it provision what the real one would.

    It answers a tree it has already installed the way the real one does — the
    measured `Checked 122 installs across 132 packages (no changes)` — because the
    installer no longer decides that itself. Which invocations *installed* is the
    whole question the traces below ask, so a double that installed on every call
    would answer it wrong.
    """
    bun = checkout / "bin/bun"
    bun.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ -x node_modules/.bin/nx ]]; then
  printf 'bun %s (no changes)\\n' "$*" >>"$TRACE_FILE"
  exit 0
fi
printf 'bun %s\\n' "$*" >>"$TRACE_FILE"
if [[ "${FAIL_COMMAND:-}" == "bun" ]]; then
  echo "bun: captured failure detail" >&2
  exit 9
fi
# An install held until the journey releases it, where a journey asks for one, so the
# callers it races provably arrive while this one holds the lock. Each held install records
# itself first, which is how a journey reads that one is in Bun and that no second joined
# it; the bound only ends a hold whose journey died.
if [[ -n "${BUN_HOLD_RELEASE:-}" ]]; then
  printf 'held\n' >>"$BUN_HOLD_RELEASE.holding"
  waited=0
  until [[ -e "$BUN_HOLD_RELEASE" ]] || ((waited >= 6000)); do
    sleep 0.1
    waited=$((waited + 1))
  done
fi
mkdir -p node_modules/.bin
cat >node_modules/.bin/nx <<'NX'
#!/usr/bin/env bash
printf 'nx %s\n' "$*" >>"$TRACE_FILE"
NX
chmod +x node_modules/.bin/nx
cat >node_modules/.bin/playwright <<'PLAYWRIGHT'
#!/usr/bin/env bash
printf 'playwright %s\n' "$*" >>"$TRACE_FILE"
PLAYWRIGHT
chmod +x node_modules/.bin/playwright
"""
    )
    bun.chmod(0o755)


def _mark_nx_installed(checkout: Path) -> None:
    nx = checkout / "node_modules/.bin/nx"
    nx.parent.mkdir(parents=True, exist_ok=True)
    nx.write_text(
        '#!/usr/bin/env bash\nprintf \'nx %s\\n\' "$*" >>"$TRACE_FILE"\n',
        encoding="utf-8",
    )
    nx.chmod(0o755)


def _init_repository(checkout: Path) -> None:
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.name", "test"),
        ("config", "user.email", "test.invalid"),
        ("remote", "add", "origin", "https://example.invalid/workspace-recipe.git"),
    ):
        subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True)


def test_bootstrap_recipe_reinstalls_the_locked_workspace_from_a_clean_clone(
    tmp_path: Path,
) -> None:
    """Bootstrap forces the install rather than heals it: the lockfile may have moved."""
    checkout, trace = _recipe_checkout(tmp_path)
    _add_bun_double(checkout)
    _mark_nx_installed(checkout)
    _init_repository(checkout)

    result = _recipe_run(checkout, trace, "bootstrap")

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "session-setup.sh ",
        "bun install --frozen-lockfile",
        "playwright install chromium",
        "nx.sh run-many -t bootstrap",
    ]


def test_bootstrap_recipe_leaves_the_failing_workspace_install_readable(
    tmp_path: Path,
) -> None:
    """A bootstrap that cannot install must not take the reason with it."""
    checkout, trace = _recipe_checkout(tmp_path)
    _add_bun_double(checkout)
    _init_repository(checkout)

    result = _recipe_run(checkout, trace, "bootstrap", fail_command="bun")

    assert result.returncode != 0
    log = checkout / ".logs/workspace-install.log"
    assert "bun: captured failure detail" in result.stderr
    assert "install locked workspace dependencies and retry" in result.stderr
    assert f"full output: {log}" in result.stderr
    assert "bun: captured failure detail" in log.read_text()
    assert oct(log.stat().st_mode & 0o777) == "0o600"
    assert trace.read_text().splitlines() == [
        "session-setup.sh ",
        "bun install --frozen-lockfile",
    ]


def test_check_recipe_runs_every_project_when_no_comparison_base_resolves(
    tmp_path: Path,
) -> None:
    """No base is not an empty diff, so a checkout with none narrows nothing.

    This is the state a fresh copy of the tree is in, and the direction a selector
    that cannot prove what changed has to fail in.
    """
    checkout, trace = _recipe_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "check: all deterministic checks passed; project selection: run-many\n"
    )
    assert trace.read_text().splitlines() == _check_trace("run-many")


def test_check_recipe_narrows_to_the_diff_the_gate_judges(tmp_path: Path) -> None:
    """The base the judged tier and the publishing push use is the base this narrows on.

    Nx's own default base is the local branch, which in a publication clone is the
    commit being pushed — an empty diff, and a gate that would select nothing at the
    one moment it decides whether work reaches a remote.
    """
    checkout, trace = _gate_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "check: all deterministic checks passed; project selection: affected --base origin/main\n"
    )
    assert trace.read_text().splitlines() == _check_trace("affected --base origin/main")


def test_check_recipe_says_what_failed_when_it_cannot_decide_the_selection(
    tmp_path: Path,
) -> None:
    """The selection is resolved before the log opens, so it needs its own diagnostic.

    A gate that died on the shell's own error here would say nothing about what it was
    doing, and would leave a reader looking for a failure among the checks — where
    nothing had run yet.
    """
    checkout, trace = _recipe_checkout(tmp_path)
    (checkout / "scripts/nx-selection.sh").chmod(0o644)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode != 0
    assert "check: could not decide which projects to run over" in result.stderr
    assert not trace.exists(), "nothing may reach Nx before the selection is decided"


def test_the_selector_falls_back_only_on_the_status_the_base_helper_refuses_with(
    tmp_path: Path,
) -> None:
    """The status that parts the two branches is a contract between two scripts.

    It parts them in both directions. A refusal for the state this fixture is in —
    nothing to narrow against — that started exiting with anything else would be read as
    the helper having failed to run, and `just check` would stop where it should run
    every project. A refusal about a base somebody named that collapsed back onto it
    would be answered by that same silent fallback, which is how a misconfigured
    comparison identity comes to look like a fresh copy of the tree.
    """
    checkout, _ = _recipe_checkout(tmp_path)
    named = _gate_checkout(tmp_path / "named")[0]

    refused = _run(str(checkout / "scripts/comparison-base.sh"), cwd=checkout)
    unusable = _run(str(named / "scripts/comparison-base.sh"), "origin", "absent-base", cwd=named)

    assert refused.returncode == NO_BASE_AVAILABLE
    assert "comparison-base:" in refused.stderr
    assert unusable.returncode not in (0, NO_BASE_AVAILABLE), (
        "a base that was named and is not there must not reach the fallback branch, "
        "which selects every project and says nothing"
    )
    assert "comparison-base:" in unusable.stderr


def test_the_selector_selects_every_project_without_explaining_itself(
    tmp_path: Path,
) -> None:
    """The tree having no base is the ordinary state, and a state needs no report.

    `just check` already prints the selection it made, so a line here would only add a
    second account of a run that went right — which is the noise that hides the one the
    journey below prints when something actually has to be repaired.
    """
    checkout, _ = _recipe_checkout(tmp_path)

    result = _run(str(checkout / "scripts/nx-selection.sh"), cwd=checkout)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "run-many\n"
    assert result.stderr == "", "a selection that went as designed says nothing of its own"


@pytest.mark.parametrize(
    ("base", "cause"),
    [
        (
            "absent-base",
            "'origin/absent-base' is missing; fetch 'origin' or choose an existing base",
        ),
        ("invalid..base", "'invalid..base' is not a valid branch name"),
    ],
)
def test_the_selector_refuses_a_base_that_was_named_and_cannot_be_used(
    tmp_path: Path, base: str, cause: str
) -> None:
    """A named base nothing can resolve is not the fresh-copy state and must not read as it.

    The comparison identity a lifecycle exports here is the one `just gate` resolves and
    the publishing push judges against, and the gate already refuses this value outright.
    Selecting every project instead would answer a misconfiguration with a green run over
    a base nobody has, with the helper's report — which names the repair — dropped.
    """
    checkout, _ = _gate_checkout(tmp_path)
    env = os.environ | {
        "ORCHESTRATOR_COMPARISON_REMOTE": "origin",
        "ORCHESTRATOR_COMPARISON_BASE": base,
    }

    result = _run(str(checkout / "scripts/nx-selection.sh"), cwd=checkout, env=env)

    assert result.returncode != 0
    assert result.stdout == "", "nothing may reach Nx as a selection here"
    assert f"comparison-base: {cause}" in result.stderr, (
        "the helper's own report is the only account of which base was refused and how to repair it"
    )
    assert (
        "nx-selection: scripts/comparison-base.sh named no base to narrow against" in result.stderr
    )


def test_check_recipe_parts_an_unresolvable_base_from_a_helper_that_could_not_run(
    tmp_path: Path,
) -> None:
    """A base nothing can resolve is ordinary; a helper that cannot run decides nothing.

    Collapsing the two would answer a broken checkout with a green gate over every
    project, which reads as the deliberate fallback rather than as the failure it is.
    """
    checkout, trace = _recipe_checkout(tmp_path)
    (checkout / "scripts/comparison-base.sh").unlink()

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode != 0
    assert "comparison-base.sh: No such file or directory" in result.stderr, (
        "the helper's own report is the only account of why nothing was decided"
    )
    assert (
        "nx-selection: scripts/comparison-base.sh named no base to narrow against" in result.stderr
    )
    assert "check: could not decide which projects to run over" in result.stderr
    assert not trace.exists(), "a selection nothing decided may not reach Nx"


def test_check_recipe_preserves_captured_nx_failure(tmp_path: Path) -> None:
    checkout, trace = _recipe_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check", fail_command="nx.sh")

    assert result.returncode != 0
    assert "nx.sh: captured failure detail" in result.stderr
    assert "check: deterministic checks failed in 2 of 2 phases" in result.stderr
    assert trace.read_text().splitlines() == _check_trace("run-many")


def test_one_check_run_reports_every_phase_that_failed(tmp_path: Path) -> None:
    """A failure in the diff selection does not hide one in the phase after it.

    Failures are planted in both phases — a target of the diff selection and a tier no
    diff selects. One run of the recipe has to run both, report both failures and name
    both phases, and exit non-zero; a recipe that stopped at the first failure would
    leave the second to be discovered by another full gate after the first was fixed.
    """
    checkout, trace = _recipe_checkout(tmp_path)
    selected, unconditional = _check_trace("run-many")
    planted = "\n".join((selected, unconditional))

    result = _run(
        "just",
        "check",
        cwd=checkout,
        env=_recipe_env(checkout, trace, FAIL_INVOCATIONS=planted),
    )

    assert result.returncode != 0
    assert trace.read_text().splitlines() == [selected, unconditional]
    assert f"{selected}: planted failure" in result.stderr
    assert f"{unconditional}: planted failure" in result.stderr
    assert (
        "check: deterministic checks failed in 2 of 2 phases "
        f"(the diff selection; {CHECK_UNCONDITIONAL})"
    ) in result.stderr
    assert "all deterministic checks passed" not in result.stdout
    log = (checkout / ".logs/check.log").read_text()
    assert f"{selected}: planted failure" in log and f"{unconditional}: planted failure" in log


def test_a_check_run_failing_only_in_an_unconditional_phase_still_fails(tmp_path: Path) -> None:
    """The phase no diff selects decides the verdict too: a green selection is not a pass."""
    checkout, trace = _recipe_checkout(tmp_path)
    _, unconditional = _check_trace("run-many")

    result = _run(
        "just",
        "check",
        cwd=checkout,
        env=_recipe_env(checkout, trace, FAIL_INVOCATIONS=unconditional),
    )

    assert result.returncode != 0
    assert trace.read_text().splitlines() == _check_trace("run-many")
    assert (
        f"check: deterministic checks failed in 1 of 2 phases ({','.join(UNCONDITIONAL_TARGETS)})"
        in result.stderr
    )


def test_check_recipe_leaves_the_failing_run_readable_after_it_exits(tmp_path: Path) -> None:
    """The diagnosis outlives the process: `cat .logs/check.log` still answers."""
    checkout, trace = _recipe_checkout(tmp_path)

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


def _nx_wrapper_checkout(tmp_path: Path, name: str) -> Path:
    """A checkout the *real* `scripts/nx.sh` and `scripts/workspace-install.sh` run in.

    Only the published tools they reach — Nx and Bun — are replaced. `nx.sh`,
    `preserved-log.sh`, `require-provisioned.sh`, `workspace-install.sh` and
    `install-lock.sh` are the real files, because what they choose to do — which log to
    write, which lock to take, what to refuse — is what is under test. It carries no
    `uv.lock`, so it has no project environment for the provisioning check to require.
    """
    checkout = tmp_path / name
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "bin").mkdir()
    for script in (
        "nx.sh",
        "preserved-log.sh",
        "install-lock.sh",
        "workspace-install.sh",
        "require-provisioned.sh",
    ):
        shutil.copy2(ROOT / "scripts" / script, checkout / "scripts" / script)
        (checkout / "scripts" / script).chmod(0o755)
    # `nx.sh` derives its shared cache key from the repository identity.
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", f"https://example.invalid/{name}.git"],
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    return checkout


def _nx_nesting_checkout(tmp_path: Path) -> Path:
    """The wrapper checkout with its workspace already provisioned."""
    checkout = _nx_wrapper_checkout(tmp_path, "nesting")
    _mark_nx_installed(checkout)
    return checkout


def _nx_wrapper_env(
    checkout: Path, tmp_path: Path, trace: Path, **overrides: str
) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{checkout / 'bin'}:{environment['PATH']}"
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    environment["TRACE_FILE"] = str(trace)
    # Claims inherited from an enclosing run belong to other checkouts and must not
    # divert the log this checkout is about to write.
    environment.pop("ORCHESTRATOR_PRESERVED_LOGS", None)
    environment.update(overrides)
    return environment


def _add_nx_wrapper_doubles(checkout: Path) -> None:
    """Trace Bun and Nx without installing or running either."""
    _add_bun_double(checkout)


#: The words both refusals of an unprovisioned checkout name as the remedy.
PROVISIONING_REMEDY = "just session-setup"


def _recording(path: Path, name: str) -> None:
    """A stand-in for ``name`` that records each call and the `UV_NO_SYNC` it was handed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'{name} %s UV_NO_SYNC=%s\\n\' "$*" "${{UV_NO_SYNC-unset}}" >>"$TRACE_FILE"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _python_project_wrapper_checkout(tmp_path: Path, name: str) -> Path:
    """The wrapper checkout as a Python project, its `uv` and `bun` recording every call.

    It carries a lockfile, so the provisioning check requires its environment, and a
    `.venv` whose interpreter is this suite's own — an environment session setup could
    have made — so that check passes and what the wrapper does next is what is read.
    """
    checkout = _nx_wrapper_checkout(tmp_path, name)
    shutil.copy2(ROOT / "uv.lock", checkout / "uv.lock")
    (checkout / ".venv" / "bin").mkdir(parents=True)
    (checkout / ".venv" / "bin" / "python").symlink_to(sys.executable)
    for tool in ("uv", "bun"):
        _recording(checkout / "bin" / tool, tool)
    return checkout


def _unprovided_env(
    checkout: Path, tmp_path: Path, trace: Path, **overrides: str
) -> dict[str, str]:
    """The wrapper environment with every way a caller points uv at an environment removed,
    so what a journey reads is what the wrapper or the recipe chose."""
    environment = _nx_wrapper_env(checkout, tmp_path, trace)
    for provided in ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        environment.pop(provided, None)
    environment.update(overrides)
    return environment


def test_nx_wrapper_runs_no_installer_and_hands_nx_uv_no_sync(tmp_path: Path) -> None:
    """The wrapper reaches Nx without asking Bun or uv anything, and keeps `uv run` unsynced.

    Every Nx call used to re-run both installers first, about 2.2 s even when in sync;
    session setup is now the only provisioning, so neither stand-in may be reached.
    """
    checkout = _python_project_wrapper_checkout(tmp_path, "no-installer")
    _recording(checkout / "node_modules" / ".bin" / "nx", "nx")
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "nx.sh"),
        "run-many",
        "-t",
        "test",
        cwd=checkout,
        env=_unprovided_env(checkout, tmp_path, trace),
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text(encoding="utf-8").splitlines() == ["nx run-many -t test UV_NO_SYNC=1"]


def test_nx_wrapper_refuses_a_checkout_without_nx_naming_session_setup(tmp_path: Path) -> None:
    """A checkout with no installed Nx is refused, never healed: nothing is installed."""
    checkout = _python_project_wrapper_checkout(tmp_path, "no-nx")
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "nx.sh"),
        "run",
        "anything",
        cwd=checkout,
        env=_unprovided_env(checkout, tmp_path, trace),
    )

    assert result.returncode != 0
    assert PROVISIONING_REMEDY in result.stderr, result.stderr
    assert not trace.exists(), trace.read_text(encoding="utf-8")
    assert not (checkout / "node_modules").exists()


def _unprovisioned_checkout(tmp_path: Path) -> Path:
    """A Python project with this checkout's lockfile and recipes, and no environment yet.

    `scripts/nx.sh`, the helper it sources and the justfile are the real files; Nx is
    doubled, and uv is the real one, because what uv would do in a checkout with no
    `.venv` — create an empty one and run whatever `PATH` offers — is what is refused.
    """
    checkout = tmp_path / "unprovisioned"
    (checkout / "scripts").mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock", "justfile"):
        shutil.copy2(ROOT / name, checkout / name)
    for script in ("nx.sh", "preserved-log.sh", "require-provisioned.sh"):
        shutil.copy2(ROOT / "scripts" / script, checkout / "scripts" / script)
    _recording(checkout / "node_modules" / ".bin" / "nx", "nx")
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/unprovisioned.git"],
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    return checkout


# llmlint: ignore-block[shell_test_tiers_stay_split] These two run the installed uv because
# uv's own behaviour is the subject: with no `.venv` under `UV_NO_SYNC` it creates an empty
# one, which the refusal exists to stop, and where a relative `UV_PROJECT_ENVIRONMENT`
# resolves, which the check restates. A double would assert the restatement against itself.
# Both run offline in about a second, in this project's narrow target keyed on
# `scripts/require-provisioned.sh` and `uv.lock`, as the gate journeys below do.
def test_an_unprovisioned_checkout_is_refused_by_nx_and_by_a_uv_running_recipe(
    tmp_path: Path,
) -> None:
    """Both entry points refuse a checkout with no environment, and create none.

    Under `UV_NO_SYNC`, uv with no `.venv` makes an empty one and silently runs another
    checkout's tools; the refusal is what stands between a fresh worktree and that.
    Pointed at a provisioned environment — this checkout's, read unsynced — both run.
    """
    checkout = _unprovisioned_checkout(tmp_path)
    trace = tmp_path / "trace"
    entry_points = (
        (str(checkout / "scripts" / "nx.sh"), "run", "anything"),
        ("just", "repo-policy", "--help"),
    )
    unprovided = _unprovided_env(checkout, tmp_path, trace)

    for command in entry_points:
        refused = _run(*command, cwd=checkout, env=unprovided)

        assert refused.returncode != 0, (command, refused.stdout)
        assert PROVISIONING_REMEDY in refused.stderr, (command, refused.stderr)
        assert not (checkout / ".venv").exists(), command
    assert not trace.exists(), "Nx ran in a checkout that was refused"

    provided = _unprovided_env(
        checkout, tmp_path, trace, UV_PROJECT_ENVIRONMENT=str(ROOT / ".venv"), UV_NO_SYNC="1"
    )
    for command in entry_points:
        ran = _run(*command, cwd=checkout, env=provided)

        assert ran.returncode == 0, (command, ran.stdout + ran.stderr)
    assert not (checkout / ".venv").exists()
    assert trace.read_text(encoding="utf-8").splitlines() == ["nx run anything UV_NO_SYNC=1"]


def test_the_provisioning_check_reads_a_relative_environment_where_uv_does(
    tmp_path: Path,
) -> None:
    """A relative `UV_PROJECT_ENVIRONMENT` names a directory under the project root.

    The check restates that much of uv's resolution, so the installed uv is asked the
    same question from a directory below the root: both must land on the one environment,
    and a relative name with no environment behind it is refused naming that directory.
    """
    checkout = _unprovisioned_checkout(tmp_path)
    created = _run(
        "uv", "venv", "--offline", "--python", sys.executable, "relative-env", cwd=checkout
    )
    assert created.returncode == 0, created.stderr
    below = checkout / "scripts"
    check = str(checkout / "scripts" / "require-provisioned.sh")
    trace = tmp_path / "trace"

    accepted = _run(
        check,
        cwd=below,
        env=_unprovided_env(checkout, tmp_path, trace, UV_PROJECT_ENVIRONMENT="relative-env"),
    )
    asked = _run(
        "uv",
        "run",
        "--offline",
        "--no-sync",
        "--project",
        str(checkout),
        "python",
        "-c",
        "import sys; print(sys.prefix)",
        cwd=below,
        env=_unprovided_env(checkout, tmp_path, trace, UV_PROJECT_ENVIRONMENT="relative-env"),
    )
    refused = _run(
        check,
        cwd=below,
        env=_unprovided_env(checkout, tmp_path, trace, UV_PROJECT_ENVIRONMENT="absent-env"),
    )

    assert accepted.returncode == 0, accepted.stderr
    assert asked.returncode == 0, asked.stderr
    assert Path(asked.stdout.strip()).resolve() == (checkout / "relative-env").resolve()
    assert refused.returncode != 0
    assert f"'{checkout / 'absent-env'}' holds no Python environment" in refused.stderr
    assert PROVISIONING_REMEDY in refused.stderr
    assert not (checkout / ".venv").exists()


# llmlint: ignore-end[shell_test_tiers_stay_split]


def test_a_uv_running_recipe_hands_its_uv_run_uv_no_sync(tmp_path: Path) -> None:
    """The justfile's own export reaches a recipe's `uv run`; the caller exports none."""
    checkout = tmp_path / "recipe"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "justfile", checkout / "justfile")
    shutil.copy2(ROOT / "uv.lock", checkout / "uv.lock")
    shutil.copy2(
        ROOT / "scripts" / "require-provisioned.sh",
        checkout / "scripts" / "require-provisioned.sh",
    )
    (checkout / ".venv" / "bin").mkdir(parents=True)
    (checkout / ".venv" / "bin" / "python").symlink_to(sys.executable)
    _recording(checkout / "bin" / "uv", "uv")
    trace = tmp_path / "trace"
    environment = _unprovided_env(checkout, tmp_path, trace)

    result = _run("just", "repo-policy", "github.com/example/repo", cwd=checkout, env=environment)

    assert result.returncode == 0, result.stderr
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "uv run onevcs rules check github.com/example/repo UV_NO_SYNC=1"
    ]


def test_a_uv_running_recipe_leaves_an_unsynced_project_unsynced(tmp_path: Path) -> None:
    """The real `uv` under the real `justfile` locks and syncs nothing, unasked.

    The project has never been locked, so any sync on the way into `uv run` writes its
    `uv.lock`. The same recipe under that `justfile` with its one `UV_NO_SYNC` export
    removed does write it, which is what says the first run's empty answer was the
    export's doing rather than a project `uv` would have left alone anyway.
    """
    checkout = tmp_path / "recipe"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "justfile", checkout / "justfile")
    shutil.copy2(
        ROOT / "scripts" / "require-provisioned.sh",
        checkout / "scripts" / "require-provisioned.sh",
    )
    (checkout / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0"\nrequires-python = ">=3.13"\ndependencies = []\n',
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(checkout / ".venv")],
        check=True,
        capture_output=True,
    )
    trace = tmp_path / "trace"
    # The published CLI the recipe delegates to, found in the project environment as the
    # real one would be; `uv` itself is the real one on this suite's search path.
    _recording(checkout / ".venv" / "bin" / "onevcs", "onevcs")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ("UV_NO_SYNC", "UV_FROZEN", "UV_LOCKED", "UV_PROJECT_ENVIRONMENT")
        and name != "VIRTUAL_ENV"
    }
    environment |= {"TRACE_FILE": str(trace), "UV_OFFLINE": "1"}

    unasked = _run("just", "repo-policy", "github.com/example/repo", cwd=checkout, env=environment)

    assert unasked.returncode == 0, unasked.stderr
    assert not (checkout / "uv.lock").exists(), "the recipe's `uv run` locked and synced"
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "onevcs rules check github.com/example/repo UV_NO_SYNC=1"
    ]

    recipes = (checkout / "justfile").read_text(encoding="utf-8")
    exported = 'export UV_NO_SYNC := "1"\n'
    assert exported in recipes
    (checkout / "justfile").write_text(recipes.replace(exported, ""), encoding="utf-8")
    synced = _run("just", "repo-policy", "github.com/example/repo", cwd=checkout, env=environment)

    assert synced.returncode == 0, synced.stderr
    assert (checkout / "uv.lock").is_file(), (
        "with the export gone the recipe's `uv run` still wrote no lock, so this journey "
        "cannot tell an unsynced run from one that had nothing to sync"
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--reinstall"], "unknown argument '--reinstall'"),
        (["--force", "unexpected"], "expected at most one argument"),
    ],
)
def test_workspace_install_rejects_arguments_it_does_not_define(
    tmp_path: Path, arguments: list[str], message: str
) -> None:
    """An installer that ran on a misread argument would install the wrong thing."""
    checkout = _nx_wrapper_checkout(tmp_path, "arguments")
    _add_nx_wrapper_doubles(checkout)
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "workspace-install.sh"),
        *arguments,
        cwd=checkout,
        env=_nx_wrapper_env(checkout, tmp_path, trace),
    )

    assert result.returncode == 2
    assert message in result.stderr
    # And what the one accepted flag *does*, which is no longer "reinstall an
    # already provisioned workspace": the ordinary run reinstalls whenever the tree
    # and the lockfile disagree, so a refusal that still advertised the old meaning
    # would send an operator to `--force` for a heal they already get.
    assert "discard the installed tree and reinstall it from the lockfile" in result.stderr
    assert not trace.exists(), "a rejected invocation must not have run Bun"


def _sabotage_installer_state(checkout: Path, mode: str) -> None:
    """Break one of the pieces of its own state the installer has to open."""
    logs = checkout / ".logs"
    match mode:
        case "lock-directory":
            # A regular file where the directory belongs: `mkdir` refuses it outright.
            logs.write_text("not a directory\n", encoding="utf-8")
        case "linked-lock-directory":
            # The property this installer did not have while it prepared `.logs` itself:
            # a link is followed by `chmod`, so a bare create-and-secure took permissions
            # away from whatever the link pointed at, outside the checkout entirely.
            # `mkdir` refuses an existing path — a link included rather than followed —
            # so what the shared helper meets here is the link, not its target.
            elsewhere = checkout.parent / "elsewhere-linked-lock-directory"
            elsewhere.mkdir(mode=0o755)
            logs.symlink_to(elsewhere)
        case "unreadable-lock" | "write-only-lock":
            logs.mkdir()
            lock = logs / "workspace-install.lock"
            lock.touch()
            lock.chmod(0o000 if mode == "unreadable-lock" else 0o200)
        case "unacquirable-lock":
            # The one refusal no permission can produce: `flock` itself failing.
            flock = checkout / "bin" / "flock"
            flock.write_text(
                '#!/usr/bin/env bash\necho "flock: cannot lock this file" >&2\nexit 1\n',
                encoding="utf-8",
            )
            flock.chmod(0o755)
        case "unopenable-log":
            logs.mkdir()
            (logs / "workspace-install.log").mkdir()
        case _:  # pragma: no cover - guards the parametrization above
            raise AssertionError(f"unknown installer sabotage {mode!r}")


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("lock-directory", "cannot prepare"),
        ("linked-lock-directory", "is a symbolic link"),
        ("unreadable-lock", "cannot open the install lock at"),
        ("write-only-lock", "cannot open the install lock at"),
        ("unacquirable-lock", "cannot serialize the locked install"),
        ("unopenable-log", "preserved-log: cannot open"),
    ],
)
def test_workspace_install_names_every_piece_of_its_own_state_that_refuses(
    tmp_path: Path, mode: str, message: str
) -> None:
    """Each way the installer's own state can refuse arrives as a diagnostic.

    The descriptor its lock is held on is opened with `exec`, whose redirection
    failures are exactly the kind a script dies on without a word — and the lock
    directory, the lock acquisition, and the preserved log can each refuse too.
    Every one of them has to name what could not be opened and stop before Bun,
    because an installer that ran anyway would be installing unserialized.
    """
    checkout = _nx_wrapper_checkout(tmp_path, f"refusing-{mode}")
    _add_nx_wrapper_doubles(checkout)
    _sabotage_installer_state(checkout, mode)
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "workspace-install.sh"),
        cwd=checkout,
        env=_nx_wrapper_env(checkout, tmp_path, trace),
    )

    assert result.returncode == 1
    assert message in result.stderr
    assert not trace.exists(), "an installer that never took its lock must not have run Bun"


def _parent_of(pid: int) -> int | None:
    """The parent `pid` has now, read from the process table; None once it is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # The command name is parenthesized and may hold spaces, so the fields after it are
    # read from its closing parenthesis: state, then the parent.
    return int(stat.rpartition(")")[2].split()[1])


def _descends_from(pid: int, ancestor: int) -> bool:
    current: int | None = pid
    while current is not None and current > 1:
        if current == ancestor:
            return True
        current = _parent_of(current)
    return False


def _waits_on_flock(lock: Path, installer: int) -> bool:
    """Whether a process of `installer`'s tree is asleep in the kernel waiting on `lock`.

    The waiter's own evidence: its wait channel is the kernel's file-lock wait, and it holds
    a descriptor open on `lock`, so this is the wait itself rather than an inference from
    how long the installer has been running. `/proc/locks` would say it more directly, but
    the kernel leaves out a lock whose taker has exited — as `flock(1)` always has once
    it took one — and every waiter queued behind it with it.
    """
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or not _descends_from(int(entry.name), installer):
            continue
        try:
            asleep_on_a_lock = "lock" in (entry / "wchan").read_text(encoding="utf-8")
            if asleep_on_a_lock and any(
                os.readlink(descriptor) == os.path.realpath(lock)
                for descriptor in (entry / "fd").iterdir()
            ):
                return True
        except OSError:
            continue
    return False


def _naps_between_polls(naps: Path, installer: int) -> int:
    """How many naps the `flock`-less installer has started between tries at a held mutex.

    The fallback's wait is a loop of a failed `mkdir` and a `sleep`, and that nap is the
    only `sleep` it starts there, so a `sleep` whose parent is the installer is the loop
    waiting. Read from the record `_flockless_env`'s `sleep` writes as each nap starts
    rather than by finding the nap in `/proc`: a 0.2s nap is shorter than a scan of the
    process table takes on a loaded host, so a scan missed every one.
    """
    if not naps.exists():
        return 0
    return naps.read_text(encoding="utf-8").split().count(str(installer))


def _holding_installs(release: Path) -> int:
    """How many installs reached the Bun double held on `release`."""
    holding = release.with_name(f"{release.name}.holding")
    return len(holding.read_text(encoding="utf-8").splitlines()) if holding.exists() else 0


def _first_install_held(install: subprocess.Popen[str], release: Path, trace: Path) -> None:
    """Until the first caller holds the lock from inside the held Bun double."""
    until(
        "the first install to reach Bun",
        lambda: _holding_installs(release) >= 1,
        seconds=60,
        state=lambda: f"exit {install.poll()}, Bun ran {trace.exists()}",
    )


def _await_installer_waiting(
    process: subprocess.Popen[str], held: str, trace: Path, waiting: Callable[[], bool]
) -> None:
    """Until the installer is seen waiting on what this journey holds.

    An installer that does not wait on it runs the doubled Bun and exits, which fails
    this at once rather than at the hang guard.
    """

    def ready() -> bool:
        assert process.poll() is None, (
            f"the installer exited {process.returncode} while the {held} was held, so it "
            "never waited on it"
        )
        return waiting()

    until(
        f"the installer to wait on the {held}",
        ready,
        seconds=60,
        state=lambda: f"exit {process.poll()}, Bun ran {trace.exists()}",
    )


#: How many callers race one flock-less install below: enough that an unserialized
#: fallback would show as more than one install, and no more, because each is a process.
FLOCKLESS_RACERS = 3


def _path_without(root: Path, tool: str) -> Path:
    """A PATH directory containing the host's executables except ``tool``."""
    directory = root / "no-tool"
    directory.mkdir(parents=True, exist_ok=True)
    for entry in os.environ["PATH"].split(os.pathsep):
        source = Path(entry)
        if not source.is_dir():
            continue
        for candidate in sorted(source.iterdir()):
            destination = directory / candidate.name
            if candidate.name == tool or destination.is_symlink() or destination.exists():
                continue
            destination.symlink_to(candidate)
    return directory


def _nap_record(tmp_path: Path) -> Path:
    """Where `_flockless_env`'s `sleep` records the parent of each nap it starts."""
    return tmp_path / "naps"


def _flockless_env(checkout: Path, tmp_path: Path, trace: Path, **overrides: str) -> dict[str, str]:
    """The wrapper environment on a platform that ships no `flock`.

    Stated rather than assumed: every journey below passes under `flock` too, so
    without establishing it is gone each would prove the `flock` path a second time.

    Its `sleep` is the host's own, reached through a script that first appends its
    parent's pid to `_nap_record`, so a journey reads every nap the fallback's wait
    takes however short it is; the nap itself is the real one, for as long as asked.
    """
    naps = tmp_path / "recorded-sleep"
    naps.mkdir()
    sleep = naps / "sleep"
    real_sleep = shutil.which("sleep")
    assert real_sleep is not None, "this host has no sleep for the fallback to nap with"
    sleep.write_text(
        f'#!/bin/sh\necho "$PPID" >>"{_nap_record(tmp_path)}"\nexec "{real_sleep}" "$@"\n'
    )
    sleep.chmod(0o755)
    stripped = os.pathsep.join(
        (str(checkout / "bin"), str(naps), str(_path_without(tmp_path, "flock")))
    )
    resolves = subprocess.run(
        ["bash", "-c", "command -v flock"],
        env={"PATH": stripped},
        text=True,
        capture_output=True,
        check=False,
    )
    assert resolves.returncode != 0, (
        f"this journey's PATH still resolves flock at {resolves.stdout.strip()!r}, so it "
        "would prove the `flock` path a second time rather than the fallback"
    )
    return _nx_wrapper_env(checkout, tmp_path, trace, PATH=stripped, **overrides)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] What it spends is
# the installer's own processes — three callers held behind one install — and the
# `workspace-copies` key that selects it is the narrowest one that reads the scripts it
# exercises.
def test_the_workspace_installer_serializes_without_flock_through_the_shared_fallback(
    tmp_path: Path,
) -> None:
    """The other installer through the same fallback, on a platform with no `flock`.

    `flock` is util-linux and a stock macOS has none, so `scripts/install-lock.sh`
    falls back to a mutex directory — and only the plan-store installer used to be
    driven through that path, while this one reached the fallback through the same
    helper unproved. Every caller succeeds and exactly one installs: the rest arrive
    at a tree the first already provisioned, and answer it as Bun does, `(no changes)`.
    The first is held in Bun until each of the rest is seen polling for the mutex it
    holds, so the callers overlap by construction rather than by how long Bun takes.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "flockless")
    _add_nx_wrapper_doubles(checkout)
    trace = tmp_path / "trace"
    release = tmp_path / "release-bun"
    environment = _flockless_env(checkout, tmp_path, trace, BUN_HOLD_RELEASE=str(release))

    def start_caller() -> subprocess.Popen[str]:
        return subprocess.Popen(
            [str(checkout / "scripts" / "workspace-install.sh")],
            cwd=checkout,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    racing = [start_caller()]
    try:
        _first_install_held(racing[0], release, trace)
        racing.extend(start_caller() for _ in range(FLOCKLESS_RACERS - 1))
        for waiter in racing[1:]:

            def polls(waiter: subprocess.Popen[str] = waiter) -> bool:
                assert _holding_installs(release) == 1, (
                    "a second install ran Bun beside the first while it held the first "
                    "install's mutex, so that caller never waited on it"
                )
                return _naps_between_polls(_nap_record(tmp_path), waiter.pid) > 0

            _await_installer_waiting(waiter, "first install's mutex", trace, polls)
    finally:
        release.touch()
    outcomes = [caller.communicate(timeout=e2e_timeout(180)) for caller in racing]

    assert [caller.returncode for caller in racing] == [0] * FLOCKLESS_RACERS, (
        f"a caller racing {FLOCKLESS_RACERS - 1} others with no `flock` on its PATH was "
        f"refused rather than waiting for the directory the fallback locks with:\n{outcomes}"
    )
    installs = trace.read_text(encoding="utf-8").splitlines()
    assert sorted(installs) == sorted(
        ["bun install --frozen-lockfile"]
        + ["bun install --frozen-lockfile (no changes)"] * (FLOCKLESS_RACERS - 1)
    ), (
        f"{FLOCKLESS_RACERS} concurrent callers with no `flock` recorded {installs}; one "
        f"install and the rest finding it is what a serialized fallback leaves"
    )
    assert not (checkout / ".logs" / "workspace-install.lock.d").exists(), (
        "the fallback's own lock directory outlived every caller that took it; nothing "
        "releases a directory but the shell that made it, so one left behind is what a "
        "later install inherits and waits out"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


#: The status the probe below exits with once every step it checks went as the helper says.
SECOND_LOCK_REFUSED = 3


@pytest.mark.skipif(shutil.which("flock") is None, reason="the held lock is read with flock")
def test_the_lock_helper_refuses_a_second_lock_and_keeps_the_first(tmp_path: Path) -> None:
    """One lock per shell: a second take is refused before it touches anything.

    The lock is held on two spelled-out descriptors, and taking another on them would
    close them and release the `flock` the first take holds. So the helper refuses the
    second, prepares no lock directory for it, and the first stays held — which a
    `flock -n` from another process, refused while the shell holds it, is what reads.
    """
    first, second = tmp_path / "held", tmp_path / "refused"
    first.mkdir()
    second.mkdir()
    probe = (
        f". '{ROOT / 'scripts' / 'install-lock.sh'}'\n"
        'install_lock_take probe "$1" probe.lock || exit 10\n'
        'if install_lock_take probe "$2" probe.lock; then exit 11; fi\n'
        'if flock -n "$1/.logs/probe.lock" true; then exit 12; fi\n'
        f"exit {SECOND_LOCK_REFUSED}\n"
    )

    result = _run(
        "bash", "-c", probe, "probe", str(first), str(second), cwd=tmp_path, env=os.environ.copy()
    )

    assert result.returncode == SECOND_LOCK_REFUSED, (
        f"exit {result.returncode}: 10 is the first take refused, 11 the second taken, 12 "
        f"the first lock released by it\n{result.stderr}"
    )
    assert "already holds the install lock" in result.stderr, result.stderr
    assert "defect in the caller" in result.stderr, result.stderr
    assert not (second / ".logs").exists(), (
        "the helper prepared a lock directory for the lock it refused"
    )


def test_a_forced_workspace_install_discards_the_installed_tree_first(tmp_path: Path) -> None:
    """`--force` is what distrusts the tree itself, now that the ordinary run reconciles.

    Reconciling against the lockfile leaves whatever the lockfile does not describe
    — measured: Bun keeps a package no committed dependency names. A clean-clone
    bootstrap is the caller that cannot afford that, so its forced run installs
    from nothing rather than on top of what is there.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "forced")
    _add_nx_wrapper_doubles(checkout)
    _mark_nx_installed(checkout)
    leftover = checkout / "node_modules" / "leftover-package"
    leftover.mkdir(parents=True)
    (leftover / "package.json").write_text('{"name": "leftover-package"}\n', encoding="utf-8")
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "workspace-install.sh"),
        "--force",
        cwd=checkout,
        env=_nx_wrapper_env(checkout, tmp_path, trace),
    )

    assert result.returncode == 0, result.stderr
    # The plain line, not the `(no changes)` one an ordinary run against this same
    # provisioned tree records: a forced run installs unconditionally.
    assert trace.read_text().splitlines() == ["bun install --frozen-lockfile"]
    assert not leftover.exists(), "a forced install left a package no lockfile describes"
    assert (checkout / "node_modules/.bin/nx").is_file()


def test_an_install_refuses_a_linked_tree_and_a_forced_one_replaces_the_link(
    tmp_path: Path,
) -> None:
    """A `node_modules` link is another tree, which this checkout's lock does not guard.

    An ordinary install refuses it before Bun runs, naming the link and the bootstrap
    that discards it; a forced one removes the link — never the tree behind it — and
    installs a tree of this checkout's own.
    """
    elsewhere = tmp_path / "elsewhere" / "node_modules"
    (elsewhere / ".bin").mkdir(parents=True)
    kept = elsewhere / "kept-package"
    kept.mkdir()
    checkout = _nx_wrapper_checkout(tmp_path, "linked")
    _add_nx_wrapper_doubles(checkout)
    (checkout / "node_modules").symlink_to(elsewhere, target_is_directory=True)
    trace = tmp_path / "trace"
    installer = str(checkout / "scripts" / "workspace-install.sh")
    environment = _nx_wrapper_env(checkout, tmp_path, trace)

    refused = _run(installer, cwd=checkout, env=environment)

    assert refused.returncode == 1, refused.stderr
    assert "is a symbolic link" in refused.stderr, refused.stderr
    assert "just bootstrap" in refused.stderr, refused.stderr
    assert not trace.exists(), f"Bun ran through the link: {trace.read_text()}"

    forced = _run(installer, "--force", cwd=checkout, env=environment)

    assert forced.returncode == 0, forced.stderr
    assert trace.read_text().splitlines() == ["bun install --frozen-lockfile"]
    assert not (checkout / "node_modules").is_symlink()
    assert (checkout / "node_modules/.bin/nx").is_file()
    assert kept.is_dir() and not (elsewhere / ".bin" / "nx").exists(), (
        "the forced install wrote the tree the link pointed at"
    )


def test_a_forced_install_that_cannot_discard_the_tree_stops_before_bun(tmp_path: Path) -> None:
    """Discarding is the whole of what `--force` adds, so failing at it is not a warning.

    A forced run that installed over a tree it could not remove would report success
    for the one thing the caller asked for and did not get — and `just bootstrap` is
    that caller, on a clone whose `node_modules` is exactly what it distrusts.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "unremovable")
    _add_nx_wrapper_doubles(checkout)
    _mark_nx_installed(checkout)
    modules = checkout / "node_modules"
    # Children that cannot be unlinked: `rm -rf` refuses, while the checkout itself
    # stays writable so the install lock and its log are not what refuses instead.
    modules.chmod(0o500)
    trace = tmp_path / "trace"

    try:
        result = _run(
            str(checkout / "scripts" / "workspace-install.sh"),
            "--force",
            cwd=checkout,
            env=_nx_wrapper_env(checkout, tmp_path, trace),
        )
    finally:
        modules.chmod(0o700)

    assert result.returncode == 1
    assert f"cannot remove '{modules}'" in result.stderr
    assert "permissions" in result.stderr
    assert not trace.exists(), "a forced install that kept the tree must not have run Bun"


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] What it spends is the
# doubled installer's own processes held on the lock, and the key that selects it is the
# `workspace-copies` project's narrow one, naming the scripts it exercises.
def test_concurrent_workspace_installs_install_once_and_both_succeed(tmp_path: Path) -> None:
    """Two Nx invocations in one fresh worktree must not install over each other.

    The lock is what makes the self-heal safe to put in front of *every* Nx
    invocation: `just check` and the suite's own nested `just lint-llm-diff` reach
    it from the same checkout at once. The loser must wait, see the workspace the
    winner provisioned, and go on rather than reinstalling on top of it.

    The interleaving is held rather than hoped for: the first caller's Bun is held until
    this journey releases it, and the second is read off the kernel's lock table as
    waiting on the lock — with no second caller in Bun — before it is.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "concurrent")
    _add_nx_wrapper_doubles(checkout)
    trace = tmp_path / "trace"
    release = tmp_path / "release-bun"
    environment = _nx_wrapper_env(checkout, tmp_path, trace, BUN_HOLD_RELEASE=str(release))
    lock = checkout / ".logs" / "workspace-install.lock"

    def start_install() -> subprocess.Popen[str]:
        return subprocess.Popen(
            [str(checkout / "scripts" / "workspace-install.sh")],
            cwd=checkout,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    installs = [start_install()]
    try:
        _first_install_held(installs[0], release, trace)
        installs.append(start_install())
        second = installs[1]

        def second_waits() -> bool:
            assert _holding_installs(release) == 1, "a second install ran Bun beside the first"
            return _waits_on_flock(lock, second.pid)

        _await_installer_waiting(second, "first install's lock", trace, second_waits)
    finally:
        release.touch()
    outcomes = [install.communicate(timeout=e2e_timeout(60)) for install in installs]

    assert [install.returncode for install in installs] == [0, 0], outcomes
    # Both asked; only the first installed. The second ran after the lock was
    # released, against the tree the first had just provisioned, and found nothing
    # left to do — which is what "serialized" has to mean now that the ordinary
    # path reconciles instead of checking for a binary.
    assert trace.read_text().splitlines() == [
        "bun install --frozen-lockfile",
        "bun install --frozen-lockfile (no changes)",
    ]
    assert (checkout / "node_modules/.bin/nx").is_file()


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def test_a_nested_nx_run_cannot_erase_the_running_one_s_log(tmp_path: Path) -> None:
    """The running check's log survives a nested Nx invocation in the same checkout.

    This is the exact shape that made the deterministic path unsafe in this
    repository: `just check` runs the suite, and the suite runs `just lint-llm-diff`
    against this same checkout, so a second `scripts/nx.sh` resolved the very
    `.logs/nx.log` the outer one was still writing and truncated it — leaving a
    running check uninspectable at the moment a reader needs it.

    So the doubled workspace Nx here does what pytest does to its parent: it invokes
    `scripts/nx.sh` again, in the same checkout, from inside the outer run's own
    process tree. The outer log has to still hold what it wrote *before* the nested
    run, and go on to hold what it writes after.
    """
    checkout = _nx_nesting_checkout(tmp_path)
    nested_started = tmp_path / "nested.started"
    release = tmp_path / "release"
    nx = checkout / "node_modules/.bin/nx"
    nx.write_text(
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
    nx.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{checkout / 'bin'}:{env['PATH']}"
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    env["TRACE_FILE"] = str(tmp_path / "trace")
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
    (checkout / ".coverage").write_text("")

    result = _recipe_run(checkout, trace, "check", FAKE_COVERAGE_TOTAL="96.42")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "check: all deterministic checks passed (line coverage 96.42%); "
        "project selection: run-many\n"
    )


def test_check_recipe_stays_green_when_no_coverage_artifact_exists(tmp_path: Path) -> None:
    """A missing artifact reports nothing; it must never turn a green tier red."""
    checkout, trace = _recipe_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "check: all deterministic checks passed; project selection: run-many\n"
    )
    assert not (checkout / ".coverage").exists()


def test_check_recipe_stays_green_when_the_coverage_total_is_unusable(tmp_path: Path) -> None:
    """An unavailable or malformed total is dropped, not reported and not fatal."""
    checkout, trace = _recipe_checkout(tmp_path)
    (checkout / ".coverage").write_text("")

    result = _recipe_run(checkout, trace, "check", FAKE_COVERAGE_TOTAL="No data to report.")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "check: all deterministic checks passed; project selection: run-many\n"
    )
    assert "uv run coverage report --format=total" in trace.read_text()


def test_check_recipe_reports_a_total_that_coverage_exited_nonzero_to_report(
    tmp_path: Path,
) -> None:
    """`coverage report` exits 2 below the floor and still prints the number.

    Dropping it there would hide the total in exactly the situation an operator
    most wants it; the floor is the `coverage` target's to enforce, not this
    readout's.
    """
    checkout, trace = _recipe_checkout(tmp_path)
    (checkout / ".coverage").write_text("")

    result = _recipe_run(
        checkout, trace, "check", FAKE_COVERAGE_TOTAL="94.13", FAKE_COVERAGE_EXIT="2"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "check: all deterministic checks passed (line coverage 94.13%); "
        "project selection: run-many\n"
    )


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

    # The llmlint tier alone, not the deterministic stages `just check` runs
    # through the same Nx double: this asserts the judged stage's log, the stage
    # after `validate` and before the deterministic tier.
    result = _recipe_run(
        checkout,
        trace,
        "gate",
        "origin",
        "main",
        FAIL_INVOCATION="nx.sh run workspace:lint-llm-diff",
    )

    assert result.returncode != 0
    log = checkout / ".logs/gate-llmlint.log"
    assert f"full output: {log}" in result.stderr
    assert "nx.sh: captured failure detail" in log.read_text()


def test_gate_recipe_ends_on_a_judged_refusal_before_the_deterministic_tier(
    tmp_path: Path,
) -> None:
    """A judged refusal is learned in the judge's minute, not after the whole suite.

    The refusal names the one command to iterate with and the gate rerun against the
    same remote and base, and no deterministic-tier Nx invocation is ever started.
    """
    checkout, trace = _gate_checkout(tmp_path)

    result = _recipe_run(
        checkout,
        trace,
        "gate",
        "origin",
        "main",
        FAIL_INVOCATION="nx.sh run workspace:lint-llm-diff",
    )

    assert result.returncode != 0, result.stdout
    log = checkout / ".logs/gate-llmlint.log"
    assert (
        "gate: llmlint failed; clear the reported findings against "
        "'just lint-llm-diff origin/main' alone, then rerun 'just gate origin main' "
        f"once to confirm (full output: {log})"
    ) in result.stderr, result.stderr
    assert not [line for line in result.stdout.splitlines() if line.startswith("gate: ")]
    assert not (checkout / ".logs/gate-check.log").exists()
    ran = trace.read_text(encoding="utf-8").splitlines()
    assert ran == [
        "llmlint validate --diff-base origin/main",
        "nx.sh run workspace:lint-llm-diff",
    ], ran


def test_gate_recipe_reports_a_deterministic_failure_after_a_green_judge(
    tmp_path: Path,
) -> None:
    """The last stage's refusal keeps its own message and log, and claims no pass."""
    checkout, trace = _gate_checkout(tmp_path)

    result = _recipe_run(
        checkout,
        trace,
        "gate",
        "origin",
        "main",
        FAIL_INVOCATION=f"nx.sh run-many -t {CHECK_UNCONDITIONAL}",
    )

    assert result.returncode != 0, result.stdout
    log = checkout / ".logs/gate-check.log"
    assert "gate: deterministic checks failed; fix the reported findings against " in (
        result.stderr
    )
    assert f"rerun 'just gate origin main' (full output: {log})" in result.stderr
    assert not [line for line in result.stdout.splitlines() if line.startswith("gate: ")]
    ran = trace.read_text(encoding="utf-8").splitlines()
    assert ran.index("nx.sh run workspace:lint-llm-diff") < ran.index(
        f"nx.sh run-many -t {CHECK_UNCONDITIONAL}"
    ), ran


# llmlint: ignore-block[shell_test_tiers_stay_split] These two drive the gate recipe through the
# installed llmlint's `validate`, the published CLI whose refusal is the behaviour under test, and
# sit in this project's narrow `test` target beside this module's other gate journeys, keyed on
# what they drive.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] They run in about a second
# against a copied justfile and scripts, in the narrow target keyed on what they drive rather than
# in a project of their own.
#: The ignore-directive marker, spelled so this file never holds one itself.
DIRECTIVE = "llmlint" + ": ignore"


def _validating_gate_checkout(tmp_path: Path, directive_rule: str) -> tuple[Path, Path]:
    """A gate checkout whose `llmlint validate` is the real one, over a real config.

    Every other `llmlint` call stays the traced double, so a judged turn the gate reached
    would be counted rather than paid for; `validate` is traced and then handed to the
    installed llmlint, which reads this repository's own config over the copied
    `justfile`'s directives and the one planted in its scripts, naming ``directive_rule``.
    """
    checkout, trace = _gate_checkout(tmp_path)
    llmlint = checkout / "bin/llmlint"
    traced = llmlint.read_text(encoding="utf-8")
    llmlint.write_text(
        traced.replace(
            'invocation="$(basename "$0") $*"\n',
            'invocation="$(basename "$0") $*"\n'
            'if [[ ${1:-} == validate ]]; then exec "$REAL_LLMLINT" "$@"; fi\n',
            1,
        ),
        encoding="utf-8",
    )
    shutil.copy2(ROOT / "llmlint.yml", checkout / "llmlint.yml")
    (checkout / "scripts" / "planted.sh").write_text(
        # Composed, so this module's own source carries no directive for `validate` to read.
        f"#!/bin/sh\n# {DIRECTIVE}[{directive_rule}] planted by the journey\necho planted\n",
        encoding="utf-8",
    )
    # Committed and made the base, so the planted directive is part of the tree the
    # gate compares rather than an uncommitted edit.
    for args in (("add", "-A"), ("-c", "commit.gpgsign=false", "commit", "-qm", "planted")):
        subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    return checkout, trace


@pytest.mark.skipif(
    shutil.which("llmlint") is None, reason="the gate's first stage is the installed llmlint"
)
def test_gate_recipe_refuses_an_invalid_ignore_directive_before_any_judged_turn(
    tmp_path: Path,
) -> None:
    """A directive naming no configured rule stops the gate at `validate`, judging nothing.

    `validate` runs against the gate's own comparison base, and its refusal names the
    fix; neither the deterministic tier nor the judged diff — both reached through the
    traced Nx double — is started.
    """
    checkout, trace = _validating_gate_checkout(tmp_path, "no_such_rule")

    result = _recipe_run(
        checkout, trace, "gate", "origin", "main", REAL_LLMLINT=str(shutil.which("llmlint"))
    )

    assert result.returncode != 0, result.stdout + result.stderr
    log = checkout / ".logs/gate-llmlint-validate.log"
    assert 'unknown rule "no_such_rule"' in log.read_text(encoding="utf-8")
    assert (
        "gate: llmlint validate failed; fix the llmlint config or ignore directive it names"
        in result.stderr
    ), result.stderr
    assert "'just lint-llm-validate --diff-base origin/main' alone" in result.stderr
    assert f"full output: {log}" in result.stderr
    ran = trace.read_text(encoding="utf-8").splitlines()
    assert ran == ["llmlint validate --diff-base origin/main"], ran


@pytest.mark.skipif(
    shutil.which("llmlint") is None, reason="the gate's first stage is the installed llmlint"
)
@pytest.mark.parametrize("cache_skip", ["", "NX_SKIP_NX_CACHE", "NX_DISABLE_NX_CACHE"])
def test_gate_recipe_validates_first_then_judges_then_checks(
    tmp_path: Path, cache_skip: str
) -> None:
    """A tree `validate` accepts goes on to the judge, and only then the deterministic tier.

    The one summary line comes after every stage, carrying the coverage the last stage
    measured and the provenance the judged stage printed, including its warning when
    it ignores either ambient global cache skip.
    """
    checkout, trace = _validating_gate_checkout(tmp_path, "robust_shell")
    (checkout / ".coverage").write_text("")
    base = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=checkout,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()

    result = _recipe_run(
        checkout,
        trace,
        "gate",
        "origin",
        "main",
        REAL_LLMLINT=str(shutil.which("llmlint")),
        FAKE_COVERAGE_TOTAL="96.41",
        NX_SKIP_NX_CACHE="true" if cache_skip == "NX_SKIP_NX_CACHE" else "",
        NX_DISABLE_NX_CACHE="true" if cache_skip == "NX_DISABLE_NX_CACHE" else "",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    ran = trace.read_text(encoding="utf-8").splitlines()
    assert ran[0] == "llmlint validate --diff-base origin/main", ran
    assert ran[1] == "nx.sh run workspace:lint-llm-diff", ran
    assert any(line.startswith("nx.sh ") and "-t format-check" in line for line in ran[2:]), ran
    assert not any("-t format-check" in line for line in ran[:2]), ran
    note = " [ignored an ambient global Nx cache skip]" if cache_skip else ""
    assert [line for line in result.stdout.splitlines() if line.startswith("gate: ")] == [
        "gate: complete gate passed (line coverage 96.41%); "
        f"judged this diff against base {base} (Nx cache miss){note}"
    ], result.stdout


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[shell_test_tiers_stay_split]


#: How `just lint-llm-diff` tells a replayed verdict from a judged one: the pattern it
#: matches Nx's own report against, lifted from the recipe rather than restated.
REPLAY_PATTERN = re.compile(
    r"grep -qE '\^(?P<summary>[^|]+)\|\^> nx run [^(]+\((?P<annotations>[^)]+)\)'"
)


def test_the_lint_recipe_matches_the_cache_wording_the_installed_nx_prints() -> None:
    """The recipe's replay provenance is read off Nx's wording, so that wording is Nx's.

    `just lint-llm-diff` reports a verdict as replayed only when Nx's report carries
    its summary line or one of its cache annotations, and as freshly judged otherwise.
    An Nx release that renamed either would make every replay read as a fresh
    judgement without failing anything, so each phrase the recipe matches has to be one
    the installed Nx prints. Read statically from Nx's own distribution: which change
    replays the target is Nx's behaviour, and no Nx runs here.
    """
    recipe = (ROOT / "justfile").read_text(encoding="utf-8")
    matched = REPLAY_PATTERN.search(recipe)
    assert matched is not None, (
        "the lint-llm-diff recipe no longer matches Nx's report as `^<summary>|^> nx run "
        "<target> +[(<annotations>)`; lift its new form here"
    )
    dist = ROOT / "node_modules/nx/dist/src"
    printed = "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in dist.rglob("*.js")
    )
    phrases = [matched["summary"], *(f"[{name}" for name in matched["annotations"].split("|"))]
    missing = [phrase for phrase in phrases if phrase not in printed]
    assert not missing, (
        f"the installed Nx under {dist} prints none of {missing}, which the lint-llm-diff "
        "recipe reads as a replayed verdict; match the wording this Nx release prints"
    )


UPGRADE_MANIFEST = ("package.json", "bun.lock")
UPGRADE_INSTALL_CONFIG = ".puppeteerrc.json"


def _upgrade_manifest_cache() -> Path:
    """Where this host keeps the resolution `bun update --latest` last produced.

    What costs real time against this registry is resolving a dependency tree, not
    installing one.
    """
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    seed = b"".join((ROOT / name).read_bytes() for name in UPGRADE_MANIFEST)
    key = hashlib.sha256(seed).hexdigest()[:16]
    return Path(root) / "ai-orchestrator" / "upgrade-manifest" / key


def _seed_upgrade_manifest(checkout: Path, cache: Path) -> None:
    """Give the checkout a real manifest, already resolved if this host has one.

    `bun update --latest` re-resolves the whole tree from the registry whenever it
    finds something to move, and ~380 serialized manifest round-trips is where the
    140 seconds this journey charged every commit went — the install itself
    hardlinks out of a warm package cache in about two seconds. Seeding the
    resolution the last successful run produced leaves Bun with nothing to move and
    nothing to ask, so the real recipe still runs the real `bun update --latest`
    into a real `node_modules`, against a manifest that is this repository's own.
    """
    for name in UPGRADE_MANIFEST:
        source = cache / name if (cache / name).is_file() else ROOT / name
        shutil.copy2(source, checkout / name)
    # What a clone carries beside the manifest that Bun's install reads: mermaid-cli's
    # Puppeteer downloads a browser in its postinstall unless this tells it not to.
    shutil.copy2(ROOT / UPGRADE_INSTALL_CONFIG, checkout / UPGRADE_INSTALL_CONFIG)


def _publish_upgrade_manifest(checkout: Path, cache: Path) -> None:
    """Record what Bun just resolved, so the next run has nothing left to resolve.

    Published per file by rename rather than as a pair, because the pair only ever
    costs time: a reader that catches a new manifest beside an older lockfile hands
    Bun something to move and pays one resolution, which is exactly what it would
    have paid without this cache at all. That is what keeps the cache self-healing
    once upstream publishes a release the recorded resolution predates.
    """
    cache.mkdir(parents=True, exist_ok=True)
    for name in UPGRADE_MANIFEST:
        resolved = (checkout / name).read_bytes()
        if (cache / name).is_file() and (cache / name).read_bytes() == resolved:
            continue
        staged = cache / f"{name}.{os.getpid()}"
        staged.write_bytes(resolved)
        staged.replace(cache / name)


def test_upgrade_recipe_runs_bun_and_reports_one_success_line(tmp_path: Path) -> None:
    checkout, trace = _recipe_checkout(tmp_path)
    cache = _upgrade_manifest_cache()
    _seed_upgrade_manifest(checkout, cache)

    result = _recipe_run(checkout, trace, "upgrade")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "upgrade: dependencies refreshed and targets passed\n"
    assert trace.read_text().splitlines() == [
        "uv lock --upgrade",
        "uv sync",
        f"nx.sh run-many -t {UPGRADE_TARGETS}",
    ]
    # Bun really ran: it is the only thing in this recipe that is not a double, and
    # a `node_modules` it linked is the evidence the doubles cannot manufacture.
    assert (checkout / "node_modules" / ".bin" / "nx").exists()
    _publish_upgrade_manifest(checkout, cache)


def test_upgrade_recipe_preserves_bun_failure_and_stops(tmp_path: Path) -> None:
    """A real Bun failure, and the log that outlives the process which reported it.

    `upgrade` swallows the whole of uv's and Bun's output, so that log is the only
    account of which constraint could not be solved — and it used to be a `mktemp`
    file an EXIT trap removed, leaving a failed upgrade with nothing to read.
    """
    checkout, trace = _recipe_checkout(tmp_path)
    (checkout / "package.json").write_text("{invalid")

    result = _recipe_run(checkout, trace, "upgrade")

    assert result.returncode != 0
    log = checkout / ".logs/upgrade.log"
    assert "package.json" in result.stderr
    assert "upgrade: repair dependency constraints or target findings" in result.stderr
    assert f"full output: {log}" in result.stderr
    assert "package.json" in log.read_text()
    assert oct(log.stat().st_mode & 0o777) == "0o600"
    assert trace.read_text().splitlines() == [
        "uv lock --upgrade",
        "uv sync",
    ]
