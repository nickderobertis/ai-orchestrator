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

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import pytest
from nx_inputs import SELECTED_TARGETS, UNCONDITIONAL_TARGETS
from nx_workspace import copy_checkout, copy_working_tree, shares_workspace_install
from onetaskgraph_release import path_without
from waits import timeout as e2e_timeout

# Deliberately no module-level tier mark. Most of this file drives `just` recipes
# and shell scripts that never open this repository's prose, and a blanket
# declaration would charge every documentation edit for all of them. Each test
# declares what it actually reads, and `tests/conftest.py` fails one that declares
# wrong.
ROOT = Path(__file__).resolve().parents[2]
#: The two selections `just check` makes, spelled as Nx receives them. Which tiers
#: belong to each is declared once in `tests/nx_inputs.py`, and
#: `tests/test_nx_cache_scope.py` holds the recipe to that split; what these journeys
#: add is that the recipe really hands Nx two invocations rather than one.
CHECK_SELECTED = ",".join(SELECTED_TARGETS)
CHECK_UNCONDITIONAL = ",".join(UNCONDITIONAL_TARGETS)
#: The Nx target lists the other root quality recipes route through, restated here
#: rather than read from the `justfile` — this suite exists to catch one of them
#: drifting. `coverage` is last in each: it waits on the measuring tier and enforces
#: the floor on what that tier wrote.
TEST_TARGETS = "test,test-docs,test-recipes,test-checkouts,coverage"
UPGRADE_TARGETS = "build,lint,typecheck,test,test-docs,test-recipes,test-checkouts,coverage"


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
        "nx.sh run workspace:check-nx-cache",
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
@pytest.mark.reads_recipes
def test_root_recipe_routes_through_nx(recipe: str, target: str) -> None:
    result = _run("just", "--dry-run", recipe)

    assert result.returncode == 0, result.stderr
    assert f"./scripts/nx.sh {target}" in result.stderr


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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
"""
    uv = binaries / "uv"
    uv.write_text(command)
    uv.chmod(0o755)
    nx = scripts / "nx.sh"
    nx.write_text(command)
    nx.chmod(0o755)
    check_nx_cache = scripts / "check-nx-cache.sh"
    check_nx_cache.write_text(command)
    check_nx_cache.chmod(0o755)
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
# An install that takes long enough to overlap a concurrent caller, where a journey asks
# for one: a racing journey with an instant install would serialize by luck.
sleep "${BUN_INSTALL_SECONDS:-0}"
mkdir -p node_modules/.bin
cat >node_modules/.bin/nx <<'NX'
#!/usr/bin/env bash
printf 'nx %s\n' "$*" >>"$TRACE_FILE"
NX
chmod +x node_modules/.bin/nx
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


@pytest.mark.reads_recipes
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
        "nx.sh run-many -t bootstrap",
    ]


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
def test_check_recipe_preserves_captured_nx_failure(tmp_path: Path) -> None:
    checkout, trace = _recipe_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check", fail_command="nx.sh")

    assert result.returncode != 0
    assert "nx.sh: captured failure detail" in result.stderr
    assert "check: deterministic checks failed" in result.stderr
    assert trace.read_text().splitlines() == [f"nx.sh run-many -t {CHECK_SELECTED}"]


@pytest.mark.reads_recipes
def test_check_recipe_leaves_the_failing_run_readable_after_it_exits(tmp_path: Path) -> None:
    """The diagnosis outlives the process: `cat .logs/check.log` still answers."""
    checkout, trace = _recipe_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check", fail_command="nx.sh")

    assert result.returncode != 0
    log = checkout / ".logs/check.log"
    assert f"full output: {log}" in result.stderr
    assert "nx.sh: captured failure detail" in log.read_text()
    assert oct(log.stat().st_mode & 0o777) == "0o600"


@pytest.mark.reads_recipes
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


def _screens_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A recipe checkout `just dag-ui-screens` runs in, with the servers doubled.

    `scripts/dag-ui-screens.sh` and `scripts/dag-ui-server.js` are the real files,
    because what is under test is the gallery the recipe chooses, the surfaces and
    viewports it drives, and the path it reports — not what a browser renders once it
    has them. So Playwright, the published read API, the bundle server, and the
    readiness probe are doubled, and the ports and the gallery it derives are real.
    """
    checkout, trace = _recipe_checkout(tmp_path)
    # `telemetry-server.sh` too: the screens script starts the read API through it
    # rather than re-rendering `onepipeline-api serve`, so it is part of the path
    # under test here — the traced `--runs-root` below is what that wrapper renders.
    for name in ("dag-ui-screens.sh", "dag-ui-server.js", "telemetry-server.sh"):
        copied = checkout / "scripts" / name
        shutil.copy2(ROOT / "scripts" / name, copied)
        copied.chmod(0o755)
    # The address file both of those read; this script names its own host and port,
    # so what it supplies is the shape check, not the value.
    (checkout / "config").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "config/read-api.address", checkout / "config/read-api.address")
    for name in ("bunx", "bun"):
        double = checkout / "bin" / name
        double.write_text((checkout / "scripts/nx.sh").read_text())
        double.chmod(0o755)
    # The read API is doubled at the one place this script talks to it: the readiness
    # probe and the run-list query. Not traced — the probe polls, so tracing it would
    # put a variable number of lines between the ones that matter — and answering
    # with a real run list, because an API with no runs and an API that did not
    # answer are two things this script has to tell apart.
    body = checkout / "bin/runs-body.json"
    body.write_text('{"runs": []}\n', encoding="utf-8")
    curl = checkout / "bin/curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'case "${*: -1}" in\n'
        "  */api/v2/runs)\n"
        '    [ "${FAIL_RUNS_QUERY:-}" = "1" ] && exit 22\n'
        f'    cat "{body}"\n'
        "    ;;\n"
        "esac\n"
    )
    curl.chmod(0o755)
    # Provisioned already, so the real `workspace-install.sh` this script heals
    # through exits without reaching for Bun.
    _mark_nx_installed(checkout)
    return checkout, trace


class Capture(NamedTuple):
    """One Playwright capture the traced run made."""

    viewport: str
    url: str


def _captures(trace: Path) -> list[Capture]:
    """Every Playwright capture the traced run made, in the order it made them."""
    captures = []
    for line in trace.read_text().splitlines():
        match = re.fullmatch(
            r"bunx playwright screenshot --viewport-size=(\S+) --wait-for-timeout=\d+ ?"
            r"(?P<extra>.*?) ?(?P<url>http://\S+) \S+",
            line,
        )
        if match is not None:
            captures.append(Capture(viewport=match.group(1), url=match.group("url")))
    return captures


@pytest.mark.reads_recipes
def test_dag_ui_screens_recipe_gives_every_invocation_a_gallery_of_its_own(
    tmp_path: Path,
) -> None:
    """Two operators capturing at once must not photograph into one directory.

    The servers already get ports of this run's own; the gallery is the other thing
    two concurrent captures would collide on, so this recipe owns it.
    """
    checkout, trace = _screens_checkout(tmp_path)

    first = _recipe_run(checkout, trace, "dag-ui-screens")
    second = _recipe_run(checkout, trace, "dag-ui-screens", "--full-page")

    galleries = []
    for result in (first, second):
        assert result.returncode == 0, result.stderr
        reported = re.search(r"gallery at (\S+)/index\.html", result.stdout)
        assert reported is not None, result.stdout
        galleries.append(Path(reported.group(1)))
    assert galleries[0] != galleries[1]
    for gallery in galleries:
        assert (gallery / "index.html").is_file()
        assert gallery.parent == checkout / ".screenshots"
    # Every viewport in the matrix was photographed, against the bundle server this
    # run started rather than any address baked into the recipe.
    viewports = [capture.viewport for capture in _captures(trace)]
    assert viewports == ["1920,1080", "1440,900", "1280,800", "1024,768", "390,844"] * 2
    # And whatever the caller added reaches Playwright, so one capture can be varied.
    assert "--full-page" in trace.read_text()


@pytest.mark.reads_recipes
def test_dag_ui_screens_recipe_photographs_every_view_of_a_named_run(
    tmp_path: Path,
) -> None:
    """A run id names three more surfaces than the run list, at every viewport."""
    checkout, trace = _screens_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "dag-ui-screens", "--run", "run 1/2")

    assert result.returncode == 0, result.stderr
    surfaces = sorted({capture.url.partition("/?")[2] for capture in _captures(trace)})
    assert surfaces == [
        "",
        "run=run%201%2F2&view=graph",
        "run=run%201%2F2&view=overall",
        "run=run%201%2F2&view=timeline",
    ], surfaces
    assert len(_captures(trace)) == 20


@pytest.mark.reads_recipes
def test_dag_ui_screens_recipe_photographs_the_runs_root_it_is_given(
    tmp_path: Path,
) -> None:
    """An operator photographing another store must not silently get the default one."""
    checkout, trace = _screens_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "dag-ui-screens", "--runs-root", str(tmp_path / "store"))

    assert result.returncode == 0, result.stderr
    served = [line for line in trace.read_text().splitlines() if "onepipeline-api serve" in line]
    assert len(served) == 1, served
    assert f"--runs-root {tmp_path / 'store'}" in served[0]
    # And the gallery says which store it photographed, because an image of the
    # wrong runs root is indistinguishable from an image of the right one.
    gallery = re.search(r"gallery at (\S+)/index\.html", result.stdout)
    assert gallery is not None, result.stdout
    assert str(tmp_path / "store") in (Path(gallery.group(1)) / "index.html").read_text()


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("flag", "reason"),
    [("--runs-root", "--runs-root needs a directory"), ("--run", "--run needs a run id")],
    ids=("runs-root", "run"),
)
def test_dag_ui_screens_recipe_names_the_flag_it_was_given_nothing_for(
    tmp_path: Path, flag: str, reason: str
) -> None:
    """A flag with its value missing must not be passed on to Playwright as an argument."""
    checkout, trace = _screens_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "dag-ui-screens", flag)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists() or "playwright" not in trace.read_text()


@pytest.mark.reads_recipes
def test_dag_ui_screens_recipe_tells_a_silent_api_from_an_empty_store(
    tmp_path: Path,
) -> None:
    """A read API that did not answer must not be photographed as "no runs yet".

    Swallowing the failure captures the empty run list and reports it as the state
    of the runs root, which is a fuller claim than the run made.
    """
    checkout, trace = _screens_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "dag-ui-screens", FAIL_RUNS_QUERY="1")

    assert result.returncode != 0
    assert "the read API did not answer" in result.stderr
    assert "holds no runs" not in result.stderr
    assert "playwright" not in (trace.read_text() if trace.exists() else "")


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    "answered",
    [
        "<html>not json</html>",
        '["run-1"]',
        '{"runs": {"run-1": {}}}',
        '{"runs": [{}]}',
        '{"runs": [{"run_id": 7}]}',
        '{"runs": [{"run_id": null}]}',
        '{"runs": [{"run_id": "   "}]}',
    ],
    ids=("html", "bare-list", "runs-mapping", "no-id", "numeric-id", "null-id", "blank-id"),
)
def test_dag_ui_screens_recipe_says_when_the_run_list_is_not_one(
    tmp_path: Path, answered: str
) -> None:
    """An answer that is not a run list is its own diagnosis, not an empty store.

    Checked at every level, because the shapes that still index are the dangerous
    ones: a numeric or null `run_id` would be photographed as the run `7` or the run
    `None`, and a blank one would be reported as a runs root holding nothing.
    """
    checkout, trace = _screens_checkout(tmp_path)
    (checkout / "bin/runs-body.json").write_text(f"{answered}\n", encoding="utf-8")

    result = _recipe_run(checkout, trace, "dag-ui-screens")

    assert result.returncode != 0
    assert "with something that is not a run list" in result.stderr
    assert "holds no runs" not in result.stderr
    assert "playwright" not in (trace.read_text() if trace.exists() else "")


@pytest.mark.reads_recipes
def test_dag_ui_screens_recipe_names_the_gallery_a_failed_capture_left(
    tmp_path: Path,
) -> None:
    """A capture that dies part way through still says where its images are."""
    checkout, trace = _screens_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "dag-ui-screens", fail_command="bunx")

    assert result.returncode != 0
    partial = re.search(r"whatever it managed is at (\S+)", result.stderr)
    assert partial is not None, result.stderr
    assert Path(partial.group(1)).is_dir()
    # And it says what went wrong and what to do, rather than only that it failed: the
    # status Playwright exited with, and the recipe to rerun once that is addressed.
    assert "exited 9" in result.stderr
    assert "just dag-ui-screens" in result.stderr


@pytest.mark.reads_recipes
def test_dag_ui_screens_recipe_stops_when_provisioning_fails(
    tmp_path: Path,
) -> None:
    """A workspace that could not be provisioned must not be photographed anyway.

    Playwright and the published bundle both live in `node_modules`, so a failed
    install is the one thing that makes every capture below meaningless; the recipe
    owes the provisioner's own failure rather than a screenful of missing-module
    noise after it.
    """
    checkout, trace = _screens_checkout(tmp_path)
    install = checkout / "scripts/workspace-install.sh"
    install.write_text((checkout / "scripts/nx.sh").read_text())
    install.chmod(0o755)

    result = _recipe_run(checkout, trace, "dag-ui-screens", fail_command="workspace-install.sh")

    assert result.returncode != 0
    assert "workspace-install.sh: captured failure detail" in result.stderr
    # Nothing was captured, and no gallery was left behind to look at.
    assert "playwright" not in trace.read_text()
    assert not (checkout / ".screenshots").exists()


@pytest.mark.reads_recipes
def test_dag_ui_screens_recipe_reports_a_gallery_root_it_cannot_create(
    tmp_path: Path,
) -> None:
    """Nowhere to write the images is a diagnosis, not a Playwright failure.

    A checkout the invoking user cannot write into is what a read-only checkout, or
    one owned by another operator, actually looks like from here.
    """
    checkout, trace = _screens_checkout(tmp_path)
    # The doubles append to it, and a directory nothing may be created in is exactly
    # what this journey installs.
    trace.touch()
    # The recipe provisions before it captures anything, and provisioning reconciles
    # the installed tree against the lockfile through a log it writes here — so a
    # checkout that has ever run a recipe carries this directory, and one that has
    # not is refused at that path instead. What this journey is about is the *output*
    # root, which is the next thing the recipe cannot create.
    (checkout / ".logs").mkdir(exist_ok=True)
    checkout.chmod(0o500)
    try:
        result = _recipe_run(checkout, trace, "dag-ui-screens")
    finally:
        checkout.chmod(0o700)

    assert result.returncode != 0
    assert "cannot create the gallery root" in result.stderr
    # It named the repair rather than leaving the operator to infer one, and never
    # started a capture that had nowhere to land.
    assert "permissions" in result.stderr
    assert "playwright" not in trace.read_text()


def test_the_screenshot_gallery_root_is_ignored() -> None:
    """A recipe an operator runs while iterating must not be able to dirty the tree."""
    ignored = _run("git", "check-ignore", ".screenshots/gallery-aBcD1234/index.html")

    assert ignored.returncode == 0, ignored.stdout + ignored.stderr


def _nx_wrapper_checkout(tmp_path: Path, name: str) -> Path:
    """A checkout the *real* `scripts/nx.sh` runs in, with Nx doubled.

    Only Nx itself is replaced. `nx.sh`, `preserved-log.sh`, `workspace-install.sh`,
    `python-install.sh` and `onetaskgraph-install.sh` are the real files, because what
    they choose to do — which log to write, and whether to provision the workspace
    first — is what is under test. This checkout declares no adopted release, which is
    the case the plan-store heal is written to no-op in: `tests/fixtures/nx-cache` is
    the other one, and a wrapper that fetched here would put a network crossing in
    front of every Nx target in a tree that has no plan store to read.
    """
    checkout = tmp_path / name
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "bin").mkdir()
    for script in (
        "nx.sh",
        "preserved-log.sh",
        "install-lock.sh",
        "workspace-install.sh",
        "python-install.sh",
        "onetaskgraph-install.sh",
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
    """The wrapper checkout with its workspace already provisioned.

    Bun is doubled here even though this checkout is provisioned, because every
    `scripts/nx.sh` now asks Bun whether the tree still matches the lockfile — and
    a checkout with no `package.json` is one the real Bun refuses.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "nesting")
    _add_bun_double(checkout)
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


@pytest.mark.reads_recipes
def test_nx_wrapper_provisions_the_locked_workspace_when_nx_is_absent(tmp_path: Path) -> None:
    """A bare Nx invocation in a fresh worktree heals itself instead of failing.

    `just check` used to repair this inline, so the same missing install produced
    two different stories: one recipe named the provisioning and fixed it, and
    every other one failed with Nx's own "Could not find Nx modules" under advice
    to fix project findings it had never reached.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "provisioning")
    _add_nx_wrapper_doubles(checkout)
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "nx.sh"),
        "run-many",
        "-t",
        "test",
        cwd=checkout,
        env=_nx_wrapper_env(checkout, tmp_path, trace),
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "bun install --frozen-lockfile",
        "nx run-many -t test",
    ]


@pytest.mark.reads_recipes
def test_nx_wrapper_installs_nothing_when_the_tree_already_matches_the_lockfile(
    tmp_path: Path,
) -> None:
    """The heal is a no-op on every ordinary invocation, which is most of them.

    A no-op decided by Bun rather than by the installer, though: the ordinary
    invocation asks, and what it must not do is spend an install on the answer.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "provisioned")
    _add_nx_wrapper_doubles(checkout)
    _mark_nx_installed(checkout)
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "nx.sh"),
        "run",
        "cached",
        cwd=checkout,
        env=_nx_wrapper_env(checkout, tmp_path, trace),
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "bun install --frozen-lockfile (no changes)",
        "nx run cached",
    ]


@pytest.mark.reads_recipes
def test_nx_wrapper_names_the_provisioning_it_could_not_complete(tmp_path: Path) -> None:
    """A failed install stops before Nx and leaves its own reason on disk."""
    checkout = _nx_wrapper_checkout(tmp_path, "unprovisionable")
    _add_nx_wrapper_doubles(checkout)
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "nx.sh"),
        "run",
        "anything",
        cwd=checkout,
        env=_nx_wrapper_env(checkout, tmp_path, trace, FAIL_COMMAND="bun"),
    )

    assert result.returncode != 0
    log = checkout / ".logs" / "workspace-install.log"
    assert "workspace-install: install locked workspace dependencies and retry" in result.stderr
    assert f"full output: {log}" in result.stderr
    assert "bun: captured failure detail" in log.read_text(encoding="utf-8")
    assert oct(log.stat().st_mode & 0o777) == "0o600"
    # Nx is never reached: running it without its modules is what produced the
    # misleading diagnosis this replaces.
    assert trace.read_text().splitlines() == ["bun install --frozen-lockfile"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--reinstall"], "unknown argument '--reinstall'"),
        (["--force", "unexpected"], "expected at most one argument"),
    ],
)
@pytest.mark.reads_recipes
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
        case "unloadable-helper":
            # Readable, and not loadable: the installer checked the first and assumed the
            # second, so a helper truncated mid-function died with bash's own syntax error
            # and no repair anybody could act on.
            (checkout / "scripts" / "install-lock.sh").write_text(
                "install_lock_take() {\n", encoding="utf-8"
            )
        case "helper-without-the-entry-point":
            # Loadable, and empty of the one thing it is sourced for. Left unguarded this
            # surfaces as `install_lock_take: command not found`, which names a function
            # rather than the file to restore.
            (checkout / "scripts" / "install-lock.sh").write_text(
                "# a helper that defines nothing\n", encoding="utf-8"
            )
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
        ("unloadable-helper", "could not be loaded"),
        ("helper-without-the-entry-point", "defines no install_lock_take"),
    ],
)
@pytest.mark.reads_recipes
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


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_recipes`
# deselects nothing and hides no cost: `orchestrator:test-recipes` runs `-m reads_recipes`
# as a target of this same project and `just check` runs it, so the marker chooses the
# `recipeWorkspace` key the verdict is memoized on — and `tests/conftest.py` fails a recipe
# journey that omits it. The installer under test is the doubled one every sibling here
# drives, and the journey takes seconds.
@pytest.mark.reads_recipes
def test_a_copy_sharing_an_install_takes_the_owning_checkouts_lock(tmp_path: Path) -> None:
    """Two callers over one tree serialize on one lock, wherever each runs from.

    Every journey `nx_workspace.copy_checkout` builds symlinks its `node_modules` at
    this checkout's own install, so an installer run in the copy writes the tree the
    owning checkout's installer writes — and a lock kept beside each caller serialized
    nothing between them. That is not harmless on a tree already in agreement with the
    lockfile: measured on Bun 1.3.14, four `bun install --frozen-lockfile` at once over
    one in-sync shared tree fail with `Failed to link <pkg>: EEXIST`, because a
    no-change run still re-links every package carrying a `bin`. Two Nx targets running
    two pytest processes is where the gate met it, past the reach of any xdist group.

    Proven by holding the owner's lock rather than by racing: a copy that takes the
    owner's lock waits, and one that takes its own would have run Bun long before the
    lock is released.
    """
    owner = _nx_wrapper_checkout(tmp_path, "owner")
    _add_nx_wrapper_doubles(owner)
    _mark_nx_installed(owner)
    copy = _nx_wrapper_checkout(tmp_path, "copy")
    _add_nx_wrapper_doubles(copy)
    (copy / "node_modules").symlink_to(owner / "node_modules", target_is_directory=True)
    trace = tmp_path / "trace"
    owner_lock = owner / ".logs" / "workspace-install.lock"
    owner_lock.parent.mkdir()
    owner_lock.touch()

    with owner_lock.open() as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        process = subprocess.Popen(
            [str(copy / "scripts" / "workspace-install.sh")],
            cwd=copy,
            env=_nx_wrapper_env(copy, tmp_path, trace),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # The window an installer taking its own lock would run Bun inside many
            # times over: the doubled install returns in milliseconds.
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=e2e_timeout(2))
            assert not trace.exists(), (
                "the copy's installer ran Bun while the owning checkout's lock was held"
            )
        except BaseException:
            process.kill()
            process.communicate()
            raise
        fcntl.flock(held, fcntl.LOCK_UN)
    _, stderr = process.communicate(timeout=e2e_timeout(30))

    assert process.returncode == 0, stderr
    # The `(no changes)` line: the tree the copy shares is the owner's provisioned one,
    # which is the in-sync case the race above fails in.
    assert trace.read_text().splitlines() == ["bun install --frozen-lockfile (no changes)"]
    assert not (copy / ".logs" / "workspace-install.lock").exists(), (
        "the copy took a lock of its own beside the owner's"
    )
    # The log stays the caller's: it records this run, and this run was the copy's.
    assert (copy / ".logs" / "workspace-install.log").is_file()
    assert not (owner / ".logs" / "workspace-install.log").exists()


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def _plant_unenterable_install_tree(checkout: Path, shape: str) -> None:
    """A `node_modules` that exists and cannot be entered, in one of two shapes."""
    modules = checkout / "node_modules"
    match shape:
        case "dangling-symlink":
            # What a copy sharing an install is left with once the install it shared
            # is gone: the link is there and the tree is not.
            modules.symlink_to(checkout / "nowhere" / "node_modules", target_is_directory=True)
        case "unreadable-directory":
            modules.mkdir()
            modules.chmod(0o000)
        case _:  # pragma: no cover - guards the parametrization below
            raise AssertionError(f"unknown install tree shape {shape!r}")


@pytest.mark.parametrize("shape", ["dangling-symlink", "unreadable-directory"])
@pytest.mark.reads_recipes
def test_an_install_tree_that_cannot_be_entered_is_refused_rather_than_read_as_absent(
    tmp_path: Path, shape: str
) -> None:
    """A tree that exists and cannot be entered is not a tree that does not exist.

    The lock is the tree's, found by entering `node_modules` and asking where it is.
    Read as absent, a dangling symlink or an unreadable directory would take this
    checkout's own lock while whatever it points at is still the tree Bun writes —
    losing the one property the lock exists for exactly where the tree is already
    broken. So the installer stops before Bun and says what to do about it.
    """
    checkout = _nx_wrapper_checkout(tmp_path, f"unenterable-{shape}")
    _add_nx_wrapper_doubles(checkout)
    _plant_unenterable_install_tree(checkout, shape)
    trace = tmp_path / "trace"

    try:
        result = _run(
            str(checkout / "scripts" / "workspace-install.sh"),
            cwd=checkout,
            env=_nx_wrapper_env(checkout, tmp_path, trace),
        )
    finally:
        if shape == "unreadable-directory":
            (checkout / "node_modules").chmod(0o700)

    assert result.returncode == 1
    assert f"'{checkout / 'node_modules'}' exists but cannot be entered" in result.stderr
    assert "just bootstrap" in result.stderr
    assert not trace.exists(), "an installer that could not find the tree's lock ran Bun anyway"
    assert not (checkout / ".logs" / "workspace-install.lock").exists(), (
        "the installer took this checkout's lock over a tree it could not enter"
    )


@pytest.mark.reads_recipes
def test_a_forced_install_discards_a_tree_it_cannot_enter(tmp_path: Path) -> None:
    """`--force` is the repair the refusal above names, so it must not meet the refusal.

    A forced run discards the tree before it installs, so the tree it then writes is
    this checkout's own, and this checkout's lock is the right one — a dangling
    symlink is removed like any other `node_modules` and the install lands beside it.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "forced-unenterable")
    _add_nx_wrapper_doubles(checkout)
    _plant_unenterable_install_tree(checkout, "dangling-symlink")
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "workspace-install.sh"),
        "--force",
        cwd=checkout,
        env=_nx_wrapper_env(checkout, tmp_path, trace),
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == ["bun install --frozen-lockfile"]
    modules = checkout / "node_modules"
    assert not modules.is_symlink(), "the forced run installed through the dangling link"
    assert (modules / ".bin" / "nx").is_file()
    assert (checkout / ".logs" / "workspace-install.lock").is_file()


@pytest.mark.reads_recipes
def test_a_forced_install_in_a_copy_sharing_an_install_holds_both_trees_locks(
    tmp_path: Path,
) -> None:
    """`--force` in a copy sharing an install changes which tree its path names mid-run.

    Before it, `node_modules` is a link into the owning checkout's tree; after it, a
    tree of the copy's own. So one lock cannot cover it. Taking the owner's alone — the
    lock the ordinary run over that link takes — lets a caller arriving in the copy
    once the link is gone pick the copy's lock and install beside it; taking the copy's
    alone lets a caller already writing through the link collide with the link being
    pulled out from under it. The forced run holds the owner's first, so writers
    through the link finish, then its own, so later arrivals wait — and only then
    discards the link. The owner's tree itself is never written or removed.

    Proven the way the shared-lock journey above is: by holding each lock in turn and
    reading that the run neither ran Bun nor discarded the link while either was held.
    """
    owner = _nx_wrapper_checkout(tmp_path, "owner")
    _add_nx_wrapper_doubles(owner)
    _mark_nx_installed(owner)
    copy = _nx_wrapper_checkout(tmp_path, "copy")
    _add_nx_wrapper_doubles(copy)
    modules = copy / "node_modules"
    modules.symlink_to(owner / "node_modules", target_is_directory=True)
    trace = tmp_path / "trace"
    locks = []
    for checkout in (owner, copy):
        lock = checkout / ".logs" / "workspace-install.lock"
        lock.parent.mkdir()
        lock.touch()
        locks.append(lock.open())
    owner_held, copy_held = locks
    owner_lock = owner / ".logs" / "workspace-install.lock"

    try:
        for held in (owner_held, copy_held):
            fcntl.flock(held, fcntl.LOCK_EX)
        process = subprocess.Popen(
            [str(copy / "scripts" / "workspace-install.sh"), "--force"],
            cwd=copy,
            env=_nx_wrapper_env(copy, tmp_path, trace),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            for held, whose in ((owner_held, "owner's"), (copy_held, "copy's own")):
                # The window a run not waiting on this lock would have run Bun inside
                # many times over: the doubled install returns in milliseconds.
                with pytest.raises(subprocess.TimeoutExpired):
                    process.wait(timeout=e2e_timeout(2))
                assert not trace.exists(), f"the forced run ran Bun while the {whose} lock was held"
                assert modules.is_symlink(), (
                    f"the forced run discarded the link while the {whose} lock was held"
                )
                if held is copy_held:
                    # Waiting on its own lock, the run must still be holding the
                    # owner's: the two are held at once, and a helper that opened the
                    # second on the first one's descriptors would have released the
                    # first by closing them. `flock` conflicts across opens of one
                    # process, so a fresh open here answers who holds it.
                    with owner_lock.open() as probe, pytest.raises(BlockingIOError):
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(held, fcntl.LOCK_UN)
        except BaseException:
            process.kill()
            # Released before the pipes are drained: the `flock` the run forks waits
            # on the lock this journey holds, keeps the run's pipes open while it
            # does, and is not the process the kill above reached.
            for held in locks:
                fcntl.flock(held, fcntl.LOCK_UN)
            process.communicate()
            raise
    finally:
        for held in locks:
            held.close()
    _, stderr = process.communicate(timeout=e2e_timeout(30))

    assert process.returncode == 0, stderr
    # It installed rather than reconciling: the link is gone before Bun runs, so the
    # tree Bun meets is the copy's own and empty.
    assert trace.read_text().splitlines() == ["bun install --frozen-lockfile"]
    assert not modules.is_symlink() and (modules / ".bin" / "nx").is_file(), (
        "the forced run left the copy sharing the owner's tree"
    )
    assert (owner / "node_modules" / ".bin" / "nx").is_file(), (
        "the forced run in the copy reached the owning checkout's tree"
    )


#: How many callers race one flock-less install below: enough that an unserialized
#: fallback would show as more than one install, and no more, because each is a process.
FLOCKLESS_RACERS = 3

#: How long the raced install takes, so the callers overlap rather than serialize by
#: luck: with an instant install the first could finish before the second started.
BUN_INSTALL_SECONDS = "2"


def _flockless_env(checkout: Path, tmp_path: Path, trace: Path, **overrides: str) -> dict[str, str]:
    """The wrapper environment on a platform that ships no `flock`.

    Stated rather than assumed: every journey below passes under `flock` too, so
    without establishing it is gone each would prove the `flock` path a second time.
    """
    stripped = os.pathsep.join((str(checkout / "bin"), str(path_without(tmp_path, "flock"))))
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


# Both findings these answer are that the two fallback journeys below are selected by a
# marker inside the `orchestrator` project rather than owned by an Nx project of their own.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_recipes` is the
# marker `tests/conftest.py` requires of any test that opens a script, and the tier it
# selects, `orchestrator:test-recipes`, is keyed on `recipeWorkspace` — which covers
# `scripts/**/*`, so an edit to the installer or the lock helper these drive selects it
# already. That is the edge a project of its own would add, and moving the fifty-odd
# recipe journeys of this module into one is a change to the tier layout, not to these.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Same site, same
# reason: the seconds each spends are the raced install's, so three callers overlap
# rather than serialize by luck, and the two windows a forced run is watched not
# running Bun in; the key that selects them is the narrowest one that reads the scripts
# they exercise.
@pytest.mark.reads_recipes
def test_the_workspace_installer_serializes_without_flock_through_the_shared_fallback(
    tmp_path: Path,
) -> None:
    """The other installer through the same fallback, on a platform with no `flock`.

    `flock` is util-linux and a stock macOS has none, so `scripts/install-lock.sh`
    falls back to a mutex directory — and only the plan-store installer used to be
    driven through that path, while this one reached the fallback through the same
    helper unproved. Every caller succeeds and exactly one installs: the rest arrive
    at a tree the first already provisioned, and answer it as Bun does, `(no changes)`.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "flockless")
    _add_nx_wrapper_doubles(checkout)
    trace = tmp_path / "trace"
    environment = _flockless_env(checkout, tmp_path, trace, BUN_INSTALL_SECONDS=BUN_INSTALL_SECONDS)

    racing = [
        subprocess.Popen(
            [str(checkout / "scripts" / "workspace-install.sh")],
            cwd=checkout,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(FLOCKLESS_RACERS)
    ]
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


@pytest.mark.reads_recipes
def test_a_forced_install_in_a_copy_holds_both_trees_locks_without_flock_and_releases_both(
    tmp_path: Path,
) -> None:
    """The two-lock forced run through the fallback, which has to release two mutexes.

    Under `flock` the kernel releases a lock when its holder exits; under the fallback
    the sourcing shell's one EXIT trap does, and a forced run in a copy sharing an
    install takes two locks in that shell — the owning tree's, then its own. A trap
    re-armed for the second would have dropped the first, leaving a mutex behind that
    every later install of the owner waits the whole budget out on. So this holds each
    mutex in turn, the way the `flock` journey above holds each lock, reads that the run
    neither ran Bun nor discarded the link while either was held, and then reads that
    neither mutex outlived it.
    """
    owner = _nx_wrapper_checkout(tmp_path, "owner")
    _add_nx_wrapper_doubles(owner)
    _mark_nx_installed(owner)
    copy = _nx_wrapper_checkout(tmp_path, "copy")
    _add_nx_wrapper_doubles(copy)
    modules = copy / "node_modules"
    modules.symlink_to(owner / "node_modules", target_is_directory=True)
    trace = tmp_path / "trace"
    mutexes = []
    for checkout in (owner, copy):
        mutex = checkout / ".logs" / "workspace-install.lock.d"
        mutex.parent.mkdir(mode=0o700)
        mutex.mkdir()
        mutexes.append(mutex)
    owner_held, copy_held = mutexes

    process = subprocess.Popen(
        [str(copy / "scripts" / "workspace-install.sh"), "--force"],
        cwd=copy,
        env=_flockless_env(copy, tmp_path, trace),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for held, whose in ((owner_held, "owner's"), (copy_held, "copy's own")):
            # The window a run not waiting on this mutex would have run Bun inside
            # many times over: the doubled install returns in milliseconds.
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=e2e_timeout(2))
            assert not trace.exists(), f"the forced run ran Bun while the {whose} mutex was held"
            assert modules.is_symlink(), (
                f"the forced run discarded the link while the {whose} mutex was held"
            )
            if held is copy_held:
                # Waiting on its own mutex, the run must still be holding the owner's,
                # which under the fallback is the directory it re-made once this
                # journey released it.
                assert owner_held.is_dir(), (
                    "the forced run let go of the owner's mutex when it took its own"
                )
            held.rmdir()
    except BaseException:
        process.kill()
        process.communicate()
        raise
    _, stderr = process.communicate(timeout=e2e_timeout(30))

    assert process.returncode == 0, stderr
    assert trace.read_text().splitlines() == ["bun install --frozen-lockfile"]
    assert not modules.is_symlink() and (modules / ".bin" / "nx").is_file(), (
        "the forced run left the copy sharing the owner's tree"
    )
    assert (owner / "node_modules" / ".bin" / "nx").is_file(), (
        "the forced run in the copy reached the owning checkout's tree"
    )
    left = [str(mutex) for mutex in mutexes if mutex.exists()]
    assert not left, (
        f"{left} outlived the forced run that took both; the one EXIT trap has to "
        "release every mutex the shell took, or the next install of that checkout "
        "waits the whole budget out on a lock nothing holds"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _install_lock_limit() -> int:
    """How many locks `scripts/install-lock.sh` lets one shell hold, off its own declaration.

    Each is a spelled-out descriptor pair, so the bound is the number of pairs.
    """
    declared = re.search(
        r"^INSTALL_LOCK_LIMIT=(\d+)$",
        (ROOT / "scripts" / "install-lock.sh").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert declared is not None, "scripts/install-lock.sh no longer declares INSTALL_LOCK_LIMIT"
    return int(declared.group(1))


INSTALL_LOCK_LIMIT = _install_lock_limit()


@pytest.mark.reads_recipes
def test_the_lock_helper_refuses_a_third_lock_rather_than_reusing_a_pair(
    tmp_path: Path,
) -> None:
    """A lock past the helper's pairs is refused before it touches anything.

    Each lock is held on its own descriptor pair, because taking one on a pair already
    held would close it and release the `flock` on it. The pairs are spelled out, so a
    caller asking for one more than there are is a defect in the caller, and the helper
    says so and prepares nothing — the checkouts it already holds stay held, and the
    one it refused gains no lock directory.
    """
    checkouts = [tmp_path / f"held-{index}" for index in range(INSTALL_LOCK_LIMIT + 1)]
    for checkout in checkouts:
        checkout.mkdir()
    takes = " && ".join(
        f"install_lock_take probe '{checkout}' probe.lock" for checkout in checkouts
    )

    result = _run(
        "bash",
        "-c",
        f". '{ROOT / 'scripts' / 'install-lock.sh'}' && {takes}",
        cwd=tmp_path,
        env=os.environ.copy(),
    )

    assert result.returncode == 1, result.stderr
    assert f"holds at most {INSTALL_LOCK_LIMIT}" in result.stderr
    assert "defect in the caller" in result.stderr
    for checkout in checkouts[:INSTALL_LOCK_LIMIT]:
        assert (checkout / ".logs" / "probe.lock").is_file(), (
            f"the helper refused the lock past its bound and the one at {checkout} was never taken"
        )
    assert not (checkouts[INSTALL_LOCK_LIMIT] / ".logs").exists(), (
        "the helper prepared a lock directory for the lock it refused"
    )


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
def test_concurrent_workspace_installs_install_once_and_both_succeed(tmp_path: Path) -> None:
    """Two Nx invocations in one fresh worktree must not install over each other.

    The lock is what makes the self-heal safe to put in front of *every* Nx
    invocation: `just check` and the suite's own nested `just lint-llm-diff` reach
    it from the same checkout at once. The loser must wait, see the workspace the
    winner provisioned, and go on rather than reinstalling on top of it.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "concurrent")
    _add_nx_wrapper_doubles(checkout)
    # Slow enough that the second caller certainly arrives while the first holds
    # the lock, which is the interleaving under test.
    bun = checkout / "bin" / "bun"
    bun.write_text(
        bun.read_text().replace("mkdir -p node_modules/.bin", "sleep 2\nmkdir -p node_modules/.bin")
    )
    trace = tmp_path / "trace"
    environment = _nx_wrapper_env(checkout, tmp_path, trace)

    installs = [
        subprocess.Popen(
            [str(checkout / "scripts" / "workspace-install.sh")],
            cwd=checkout,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
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


#: One real journey carrying `shares_workspace_install`, run inside the fresh
#: worktree below. It reaches Nx through the real `just` recipes, so it is exactly
#: the shape that used to skip there — and a skip is what made a worker's own
#: `pytest` say something different from the gate's.
FRESH_WORKTREE_JOURNEY = (
    "tests/e2e/test_llmlint_cache_e2e.py::"
    "test_an_unresolvable_base_is_rejected_before_the_judge_is_paid"
)


@pytest.mark.reads_docs
def test_a_freshly_created_worktree_provisions_itself_for_nx_and_for_pytest(
    tmp_path: Path,
) -> None:
    """The situation every dispatched worker starts in, driven end to end.

    `node_modules` is ignored state that no checkout shares, so a new worktree has
    none. Both entry points into Nx are exercised here in a real linked worktree
    carrying this working tree's own changes: a bare `./scripts/nx.sh`, and a bare
    `pytest` on a journey that drives real Nx. Neither may ask an operator to run
    Bun by hand, which is what a worker had to do.
    """
    worktree = tmp_path / "fresh-worktree"
    # `--no-checkout`, so what lands here is this working tree exactly rather than
    # HEAD with the change laid over it — a file this change deletes would
    # otherwise survive into the tree that is supposed to be proving the change.
    _run("git", "worktree", "add", "--no-checkout", "--detach", str(worktree), "HEAD")
    try:
        copy_working_tree(worktree)
        assert not (worktree / "node_modules").exists()

        wrapper = _run("./scripts/nx.sh", "show", "projects", cwd=worktree)

        assert wrapper.returncode == 0, wrapper.stderr
        assert (worktree / "node_modules/.bin/nx").is_file()

        # Again from the other entry point, with the install withdrawn: the suite
        # provisions rather than skipping, so a green run here means what the gate
        # means. `uv run` builds this worktree's own environment on the way in.
        shutil.rmtree(worktree / "node_modules")
        suite = _run(
            "uv", "run", "pytest", FRESH_WORKTREE_JOURNEY, "-q", "-p", "no:randomly", cwd=worktree
        )

        assert suite.returncode == 0, suite.stdout + suite.stderr
        assert "skipped" not in suite.stdout
        assert (worktree / "node_modules/.bin/nx").is_file()
    finally:
        # Removed here rather than left to teardown: a linked worktree surviving a
        # test is a leak the guard reports, and rightly. `remove` deregisters this
        # one on its own; `prune` would reach across a registry other live
        # orchestrator runs share.
        _run("git", "worktree", "remove", "--force", str(worktree))


#: The dependency this journey moves the pin of, chosen because a stale
#: `node_modules` really did go on serving an older copy of it under a current pin.
STALENESS_WITNESS = "onepipeline-ui"


@pytest.mark.reads_docs
def test_a_worktree_provisioned_before_the_pin_moved_reinstalls_from_the_lockfile(
    tmp_path: Path,
) -> None:
    """A moved pin heals itself, the way a missing `node_modules` already did.

    Every checkout that already had a `node_modules` used to be exempt from the
    lockfile: the installer asked whether Nx was there, which answers for *an*
    install rather than *the locked* one. So the pin moved, nothing reinstalled,
    and an operator reading the served view had no way to tell a stale install
    from a broken feature.

    Driven against real Bun and a lockfile that really moved, because the whole
    defect was an install nobody could see: both entry points into the wrapper
    chain are exercised — the script every recipe routes through, and a `just`
    recipe on top of it.
    """
    worktree = tmp_path / "moved-pin-worktree"
    # `--no-checkout`, so this working tree's own change is what provisions here.
    _run("git", "worktree", "add", "--no-checkout", "--detach", str(worktree), "HEAD")
    try:
        copy_working_tree(worktree)
        manifest = worktree / "package.json"
        lockfile = worktree / "bun.lock"
        committed_manifest = manifest.read_bytes()
        committed_lock = lockfile.read_bytes()
        pinned = json.loads(committed_manifest)["dependencies"][STALENESS_WITNESS]

        # Provisioned from the lockfile as it was before the bump, for real: the
        # tree a checkout carries when the pin moves under it.
        before_the_bump = json.loads(committed_manifest)
        del before_the_bump["dependencies"][STALENESS_WITNESS]
        manifest.write_text(json.dumps(before_the_bump, indent=2) + "\n", encoding="utf-8")
        provision = _run("bun", "install", cwd=worktree)
        assert provision.returncode == 0, provision.stderr
        assert (worktree / "node_modules/.bin/nx").is_file()
        assert not (worktree / "node_modules" / STALENESS_WITNESS).exists()

        # The bump lands: the committed manifest and lockfile move, and nothing
        # else does.
        manifest.write_bytes(committed_manifest)
        lockfile.write_bytes(committed_lock)

        wrapper = _run("./scripts/nx.sh", "show", "projects", cwd=worktree)

        assert wrapper.returncode == 0, wrapper.stderr
        bundle = worktree / "node_modules" / STALENESS_WITNESS / "package.json"
        assert bundle.is_file(), (
            f"the wrapper left {STALENESS_WITNESS} uninstalled under a {pinned} pin: a tree "
            f"provisioned before the bump stayed exempt from the lockfile"
        )
        installed = json.loads(bundle.read_text(encoding="utf-8"))
        assert installed["version"] == pinned, (
            f"the wrapper left {STALENESS_WITNESS} {installed['version']} installed under a "
            f"{pinned} pin"
        )
        assert lockfile.read_bytes() == committed_lock, "the committed lockfile decides"

        # Again from the recipe an operator runs, against a tree that disagrees with
        # the lockfile the other way: a package the lockfile names, gone.
        shutil.rmtree(worktree / "node_modules" / STALENESS_WITNESS)
        recipe = _run("just", "format-check", cwd=worktree)

        assert recipe.returncode == 0, recipe.stdout + recipe.stderr
        assert (worktree / "node_modules" / STALENESS_WITNESS / "package.json").is_file()
    finally:
        _run("git", "worktree", "remove", "--force", str(worktree))


def _provisioning_copy(tmp_path: Path, name: str) -> Path:
    """A throwaway copy of this checkout the real `scripts/nx.sh` runs in.

    `node_modules` comes across as a symlink to this checkout's own install, so what
    a Python-provisioning journey pays for here is the Python half alone.
    """
    checkout = tmp_path / name
    checkout.mkdir()
    copy_checkout(checkout)
    # Nx resolves its workspace from git, and `scripts/nx.sh` derives its shared
    # cache key from the repository identity.
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True, capture_output=True)
    return checkout


def _uv_provisioning_env(**overrides: str) -> dict[str, str]:
    """The environment a Python-provisioning journey states for itself.

    Every one of these names decides where uv installs, and this suite runs from
    inside a dispatch that has already set some of them for its own checkout — so a
    journey about a fresh tree would otherwise be answering about the enclosing one.
    """
    environment = os.environ.copy()
    for provided in ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        environment.pop(provided, None)
    environment.update(overrides)
    return environment


@pytest.mark.reads_docs
def test_a_freshly_created_worktree_provisions_its_python_environment_from_the_lockfile(
    tmp_path: Path,
) -> None:
    """A worktree cut for a dispatch arrives without `.venv`, and must not stay that way.

    A missing environment does not fail loudly: every reader of `<root>/.venv/bin`
    falls through to whichever other checkout is on PATH.
    """
    worktree = tmp_path / "fresh-python-worktree"
    _run("git", "worktree", "add", "--no-checkout", "--detach", str(worktree), "HEAD")
    try:
        copy_working_tree(worktree)
        assert not (worktree / ".venv").exists()

        # `show projects` reaches no project target, so the wrapper's own provisioning
        # is the only thing that could have built an environment here. A target would
        # have made one through `uv run` on its way in and proved nothing.
        wrapper = _run(
            "./scripts/nx.sh", "show", "projects", cwd=worktree, env=_uv_provisioning_env()
        )

        assert wrapper.returncode == 0, wrapper.stderr
        # Installed, not merely created: an empty virtualenv satisfies a check for the
        # directory while every console script in it still resolves somewhere else.
        assert (worktree / ".venv/bin/oneharness").is_file()
        assert (worktree / "uv.lock").read_bytes() == (ROOT / "uv.lock").read_bytes()
    finally:
        _run("git", "worktree", "remove", "--force", str(worktree))


@shares_workspace_install
@pytest.mark.reads_docs
def test_the_gate_path_refuses_a_lockfile_that_would_have_to_move(tmp_path: Path) -> None:
    """A committed lockfile decides, and one that has to move stops the run.

    `uv run` would instead re-resolve and write the new answer back, so a branch
    whose subject *is* a pin would be verified against a tree it does not contain.
    """
    checkout = _provisioning_copy(tmp_path, "stale-lock")
    lockfile = checkout / "uv.lock"
    before = lockfile.read_bytes()
    pyproject = checkout / "pyproject.toml"
    # Stale by exactly one field the lockfile carries, with no dependency to resolve,
    # so what this journey drives is the refusal rather than a resolver's reachability.
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace('version = "0.1.0"', 'version = "0.1.1"', 1),
        encoding="utf-8",
    )

    wrapper = _run(
        "./scripts/nx.sh",
        "show",
        "projects",
        cwd=checkout,
        env=_uv_provisioning_env(XDG_CACHE_HOME=str(tmp_path / "cache")),
    )

    assert wrapper.returncode != 0
    assert "python-install: provision the locked Python environment" in wrapper.stderr
    assert "uv lock" in wrapper.stderr
    assert lockfile.read_bytes() == before


@shares_workspace_install
@pytest.mark.reads_docs
def test_a_caller_that_provides_the_python_environment_is_not_synced_over(
    tmp_path: Path,
) -> None:
    """`UV_NO_SYNC` is honored, because the journeys that copy this checkout rely on it.

    The environment named is an empty path rather than this checkout's own: what
    fires this journey red is an install into whatever the caller provided, and it
    must not be able to land on the environment the rest of the suite runs in.
    """
    checkout = _provisioning_copy(tmp_path, "provided-environment")
    provided = tmp_path / "provided-venv"

    wrapper = _run(
        "./scripts/nx.sh",
        "show",
        "projects",
        cwd=checkout,
        env=_uv_provisioning_env(
            UV_NO_SYNC="1",
            UV_PROJECT_ENVIRONMENT=str(provided),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
        ),
    )

    assert wrapper.returncode == 0, wrapper.stderr
    assert not provided.exists(), "the provided environment was synced over"
    assert not (checkout / ".venv").exists()


@shares_workspace_install
@pytest.mark.reads_docs
def test_a_caller_that_asks_uv_to_sync_gets_the_environment_it_named(tmp_path: Path) -> None:
    """`UV_NO_SYNC=0` is how uv is asked to sync, so it must not skip provisioning.

    Read as a presence, the escape hatch fires on the spelling that means the
    opposite of firing — and the environment the caller named is left empty for
    every later reader of it to fall through.
    """
    checkout = _provisioning_copy(tmp_path, "requested-sync")
    named = tmp_path / "requested-venv"

    wrapper = _run(
        "./scripts/nx.sh",
        "show",
        "projects",
        cwd=checkout,
        env=_uv_provisioning_env(UV_NO_SYNC="0", UV_PROJECT_ENVIRONMENT=str(named)),
    )

    assert wrapper.returncode == 0, wrapper.stderr
    assert (named / "bin/oneharness").is_file()


def _add_uv_double(checkout: Path) -> None:
    """Trace what the wrapper asks of uv, and fail on request, provisioning nothing.

    uv is the tool this wrapper delegates to; what the wrapper decides — whether to
    call it at all, and what it leaves readable when the call fails — is under test.
    """
    uv = checkout / "bin" / "uv"
    uv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
if [[ -n ${UV_DOUBLE_FAILURE:-} ]]; then
    echo "$UV_DOUBLE_FAILURE" >&2
    exit 1
fi
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)


def _python_install_checkout(tmp_path: Path, name: str) -> Path:
    """A wrapper checkout with a lockfile to provision and uv doubled."""
    checkout = _nx_wrapper_checkout(tmp_path, name)
    (checkout / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    _add_uv_double(checkout)
    return checkout


PYTHON_INSTALL = ROOT / "scripts" / "python-install.sh"

#: One `UV_NO_SYNC` value per way of being unreadable, for the wrapper and for uv.
UNREADABLE_NO_SYNC = (("empty", ""), ("blank", " "), ("numeric", "2"), ("word", "banana"))

_NO_SYNC_ARM = re.compile(r"^\s*(?P<spellings>[a-z0-9]+(?: \| [a-z0-9]+)*)\)(?P<body>.*);;\s*$")


def _declared_no_sync_grammar() -> dict[str, bool]:
    """Each `UV_NO_SYNC` spelling the wrapper accepts, mapped to whether it skips.

    Read out of the script rather than restated, so the journeys below and the gate
    that measures uv both speak about the one list that decides.
    """
    grammar = {
        spelling: "exit 0" in arm["body"]
        for line in PYTHON_INSTALL.read_text(encoding="utf-8").splitlines()
        if (arm := _NO_SYNC_ARM.match(line)) is not None
        for spelling in arm["spellings"].split(" | ")
    }
    if not grammar:
        raise AssertionError("scripts/python-install.sh no longer names its UV_NO_SYNC grammar")
    return grammar


DECLARED_NO_SYNC = _declared_no_sync_grammar()


@pytest.mark.parametrize(("spelling", "skips"), sorted(DECLARED_NO_SYNC.items()))
@pytest.mark.reads_recipes
def test_uv_no_sync_skips_or_provisions_by_the_spelling_it_carries(
    tmp_path: Path, spelling: str, skips: bool
) -> None:
    """Read as a presence, every one of these skipped — uv's false spellings included.

    Each runs in both cases, because an operator's `UV_NO_SYNC=FALSE` has to mean
    what uv means by `false`.
    """
    checkout = _python_install_checkout(tmp_path, f"no-sync-{spelling}")
    trace = tmp_path / "trace"

    for value in (spelling, spelling.upper()):
        result = _run(
            str(checkout / "scripts" / "python-install.sh"),
            cwd=checkout,
            env=_nx_wrapper_env(checkout, tmp_path, trace, UV_NO_SYNC=value),
        )
        assert result.returncode == 0, result.stderr

    assert trace.exists() is not skips
    if not skips:
        assert trace.read_text(encoding="utf-8").splitlines() == ["uv sync --locked"] * 2


def _uv_no_sync_probe(root: Path) -> Path:
    """A project the installed uv can decide about without reaching an index.

    Its one dependency is on no index at all, and the probe runs offline: a uv that
    chose to sync fails to, and a uv that skipped runs the command instead.
    """
    project = root / "no-sync-probe"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "no-sync-probe"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["a-distribution-no-index-supplies-0000"]\n',
        encoding="utf-8",
    )
    return project


def _uv_reads_no_sync(project: Path, environment: Path, value: str) -> bool | None:
    """Whether the installed uv skips the sync on one value, syncs on it, or refuses it."""
    probe = _run(
        "uv",
        "run",
        "--offline",
        "--python",
        sys.executable,
        "--",
        "python",
        "-c",
        "pass",
        cwd=project,
        env=_uv_provisioning_env(
            UV_NO_SYNC=value,
            UV_PYTHON_DOWNLOADS="never",
            UV_PROJECT_ENVIRONMENT=str(environment),
        ),
    )
    if "expected a boolish value" in probe.stderr:
        return None
    return probe.returncode == 0


@pytest.mark.reads_recipes
def test_the_no_sync_grammar_the_wrapper_declares_is_the_one_uv_reads(tmp_path: Path) -> None:
    """uv owns this grammar, so the wrapper's copy is measured against it, not asserted.

    A spelling uv stops accepting, or starts reading the other way round, would leave
    the wrapper deciding the opposite of what the caller's own `uv run` decides.
    """
    project = _uv_no_sync_probe(tmp_path)

    measured = {
        spelling: _uv_reads_no_sync(project, tmp_path / f"env-{spelling}", spelling)
        for spelling in DECLARED_NO_SYNC
    }
    refused = {
        name: _uv_reads_no_sync(project, tmp_path / f"env-refused-{name}", value)
        for name, value in UNREADABLE_NO_SYNC
    }

    assert measured == DECLARED_NO_SYNC
    assert refused == dict.fromkeys(refused)


@pytest.mark.parametrize(("name", "value"), UNREADABLE_NO_SYNC)
@pytest.mark.reads_recipes
def test_uv_no_sync_refuses_a_value_uv_would_refuse(tmp_path: Path, name: str, value: str) -> None:
    """A value neither side can read is named here, not left to surface as uv's error.

    Guessing either way is the defect: skip and provisioning silently does not
    happen, sync and the caller's stated intent is silently overridden.
    """
    checkout = _python_install_checkout(tmp_path, f"no-sync-invalid-{name}")
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "python-install.sh"),
        cwd=checkout,
        env=_nx_wrapper_env(checkout, tmp_path, trace, UV_NO_SYNC=value),
    )

    assert result.returncode == 2
    assert f"UV_NO_SYNC='{value}' is not a value uv reads" in result.stderr
    assert "or leave it unset" in result.stderr
    assert not trace.exists(), "a refused value must not have reached uv"


@pytest.mark.reads_recipes
def test_python_install_leaves_the_failing_provision_readable(tmp_path: Path) -> None:
    """A provision that failed on the way into the gate keeps its own reason on disk."""
    checkout = _python_install_checkout(tmp_path, "unprovisionable-python")
    trace = tmp_path / "trace"

    result = _run(
        str(checkout / "scripts" / "python-install.sh"),
        cwd=checkout,
        env=_nx_wrapper_env(
            checkout, tmp_path, trace, UV_DOUBLE_FAILURE="uv: captured failure detail"
        ),
    )

    assert result.returncode == 1
    log = checkout / ".logs" / "python-install.log"
    assert "python-install: provision the locked Python environment" in result.stderr
    assert f"full output: {log}" in result.stderr
    assert "uv: captured failure detail" in log.read_text(encoding="utf-8")
    assert oct(log.stat().st_mode & 0o777) == "0o600"


@pytest.mark.reads_recipes
def test_python_install_log_records_the_credential_name_not_its_value(tmp_path: Path) -> None:
    """A preserved log outlives its terminal, so it must never durably hold a token."""
    checkout = _python_install_checkout(tmp_path, "python-install-credentials")
    trace = tmp_path / "trace"
    token = "sk-ant-oat01-not-a-real-credential"

    result = _run(
        str(checkout / "scripts" / "python-install.sh"),
        cwd=checkout,
        env=_nx_wrapper_env(
            checkout,
            tmp_path,
            trace,
            CLAUDE_CODE_OAUTH_TOKEN=token,
            UV_DOUBLE_FAILURE=f"uv: refused with CLAUDE_CODE_OAUTH_TOKEN={token}",
        ),
    )

    assert result.returncode == 1
    log = (checkout / ".logs" / "python-install.log").read_text(encoding="utf-8")
    assert token not in log
    assert "CLAUDE_CODE_OAUTH_TOKEN=<redacted:CLAUDE_CODE_OAUTH_TOKEN>" in log
    assert token not in result.stderr


@pytest.mark.reads_recipes
def test_python_install_refuses_an_argument_and_names_the_call_that_works(
    tmp_path: Path,
) -> None:
    """A flag this script has no meaning for is refused rather than dropped.

    Its sibling takes `--force`, so reaching for one here is the natural mistake —
    and silently ignoring it would leave an operator believing they had forced
    something. There is nothing to force: the locked sync is already idempotent.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "python-install-arguments")

    result = _run(str(checkout / "scripts" / "python-install.sh"), "--force", cwd=checkout)

    assert result.returncode == 2
    assert "expected no arguments, got '--force'" in result.stderr
    assert "rerun it with none" in result.stderr


@pytest.mark.reads_recipes
def test_python_install_reports_a_host_that_cannot_provision_at_all(tmp_path: Path) -> None:
    """Missing uv is named where the repair is, not deep inside a target.

    This runs in front of every Nx invocation, so on a host that never bootstrapped
    it is the first thing to notice — and what it says is the whole diagnosis. Left
    to fall through, the same host fails much later with a bare `uv: command not
    found` from whichever target happened to run first.
    """
    checkout = _nx_wrapper_checkout(tmp_path, "python-install-without-uv")
    # A lockfile, because a workspace without one has no environment to provision
    # and would exit before reaching the check under test.
    (checkout / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    # A PATH holding what this script runs and nothing else, so what is withdrawn
    # here is uv alone rather than whatever this host keeps beside it.
    binaries = tmp_path / "path-without-uv"
    binaries.mkdir()
    for tool in ("bash", "dirname", "mkdir", "chmod", "cat", "rm", "sort"):
        located = shutil.which(tool)
        assert located is not None, f"this host has no {tool} for the journey to keep"
        (binaries / tool).symlink_to(located)

    result = _run(
        str(checkout / "scripts" / "python-install.sh"),
        cwd=checkout,
        env={**os.environ, "PATH": str(binaries)},
    )

    assert result.returncode == 1
    assert "'uv' is not installed" in result.stderr
    assert "just bootstrap" in result.stderr


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
def test_check_recipe_reports_the_coverage_total_it_measured(tmp_path: Path) -> None:
    checkout, trace = _recipe_checkout(tmp_path)
    (checkout / ".coverage").write_text("")

    result = _recipe_run(checkout, trace, "check", FAKE_COVERAGE_TOTAL="96.42")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "check: all deterministic checks passed (line coverage 96.42%); "
        "project selection: run-many\n"
    )


@pytest.mark.reads_recipes
def test_check_recipe_stays_green_when_no_coverage_artifact_exists(tmp_path: Path) -> None:
    """A missing artifact reports nothing; it must never turn a green tier red."""
    checkout, trace = _recipe_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "check: all deterministic checks passed; project selection: run-many\n"
    )
    assert not (checkout / ".coverage").exists()


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
def test_gate_recipe_leaves_the_failing_llmlint_run_readable(tmp_path: Path) -> None:
    checkout, trace = _gate_checkout(tmp_path)

    # The llmlint tier alone, not the deterministic stages `just check` runs
    # through the same Nx double: this asserts the *second* gate stage's log.
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


UPGRADE_MANIFEST = ("package.json", "bun.lock")


def _upgrade_manifest_cache() -> Path:
    """Where this host keeps the resolution `bun update --latest` last produced.

    Alongside the shared fixture lockfile `scripts/check-nx-cache.sh` publishes,
    and for the same reason: what costs real time against this registry is
    resolving a dependency tree, not installing one.
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


@pytest.mark.reads_recipes
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


def _shared_resolution_entry() -> tuple[str, str]:
    """The cache entry name this host's check would use, and the manifest behind it.

    Deliberately restated from `scripts/check-nx-cache.sh` rather than imported:
    the association between a cached resolution and the manifest it came from is
    the contract under test, so a test that asked the script for the answer could
    not detect the script agreeing with itself. If the derivation there changes,
    the accepted case below stops being accepted and says so.
    """
    root = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    fixture = json.loads(
        (ROOT / "tests/fixtures/nx-cache/package.json").read_text(encoding="utf-8")
    )
    fixture["devDependencies"] = {
        "nx": root["devDependencies"]["nx"],
        "typescript": root["devDependencies"]["typescript"],
    }
    manifest = json.dumps(fixture, indent=2) + "\n"
    bun_version = _run("bun", "--version").stdout.strip()
    key = hashlib.sha256(manifest.encode() + bun_version.encode()).hexdigest()[:16]
    return key, manifest


#: A real, complete Bun lockfile. This repository's own is the honest fixture: it
#: is a different tree, but the check's question is whether the file parses whole
#: and carries what Bun needs, not which packages it resolved.
COMPLETE_LOCKFILE = (ROOT / "bun.lock").read_text(encoding="utf-8")
#: The same file cut off mid-write, which is what a publish interrupted by a kill
#: or a full disk leaves behind. It keeps its opening bytes, so it is exactly the
#: entry a prefix check would wave through.
TRUNCATED_LOCKFILE = COMPLETE_LOCKFILE[:400]
RESOLUTION_REFUSED = "resolve the fixture dependency tree"


@pytest.mark.parametrize(
    ("stored_lockfile", "stored_manifest", "resolves_again"),
    [
        pytest.param(COMPLETE_LOCKFILE, None, True, id="no-manifest-beside-it"),
        pytest.param(
            COMPLETE_LOCKFILE, '{"name": "something-else"}\n', True, id="manifest-of-another-tree"
        ),
        pytest.param(TRUNCATED_LOCKFILE, "", True, id="lockfile-cut-off-mid-write"),
        pytest.param(COMPLETE_LOCKFILE, "", False, id="complete-and-vouched-for"),
    ],
)
@pytest.mark.reads_recipes
def test_cache_check_seeds_only_a_resolution_it_can_vouch_for(
    tmp_path: Path, stored_lockfile: str, stored_manifest: str | None, resolves_again: bool
) -> None:
    """A shared resolution is trusted for its provenance, not for being readable.

    The cache lives outside the repository under a key any process can write to,
    so an entry in it is an untrusted input however it got there — a truncated
    publish, a restored backup, an entry left by an older layout. Seeding one
    unchecked would not fail here; it would surface much later as an unrelated
    `--frozen-lockfile` error inside a worktree the check builds.

    The registry is pointed somewhere nothing is listening, which makes the
    decision observable in a second rather than the two minutes a real resolution
    costs: re-resolving at all is the proof the entry was refused, and getting
    past resolution is the proof it was accepted. Everything here is real — the
    script, `bun`, and a connection that genuinely cannot be made.
    """
    key, manifest = _shared_resolution_entry()
    entries = tmp_path / "cache" / "ai-orchestrator" / "nx-cache-fixture"
    entries.mkdir(parents=True)
    (entries / f"{key}.lock").write_text(stored_lockfile, encoding="utf-8")
    if stored_manifest is not None:
        (entries / f"{key}.manifest").write_text(stored_manifest or manifest, encoding="utf-8")

    env = os.environ.copy()
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    env["npm_config_registry"] = "http://127.0.0.1:1/"

    result = _run("bash", "scripts/check-nx-cache.sh", env=env)

    assert result.returncode != 0, "an unreachable registry must not produce a passing check"
    assert (RESOLUTION_REFUSED in result.stderr) is resolves_again, result.stderr


@pytest.mark.parametrize(
    "cache_home", ["", "relative/cache", "."], ids=["empty", "relative", "current-directory"]
)
@pytest.mark.reads_recipes
def test_cache_check_refuses_a_cache_directory_it_cannot_place(cache_home: str) -> None:
    """Where the shared cache lives is an input too, and a relative one is not usable.

    This is the failure that would not announce itself: a relative or empty value
    resolves against whatever directory the check happens to run in, so entries
    land somewhere no later run looks. Nothing errors — every commit just quietly
    pays a full resolution again while the cache appears to be working.
    """
    env = os.environ.copy()
    env["XDG_CACHE_HOME"] = cache_home
    env.pop("HOME", None)

    result = _run("bash", "scripts/check-nx-cache.sh", env=env)

    assert result.returncode != 0
    assert "must name an absolute directory" in result.stderr
