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
import time
from pathlib import Path

import pytest
from nx_workspace import copy_working_tree
from waits import timeout as e2e_timeout

# Deliberately no module-level tier mark. Most of this file drives `just` recipes
# and shell scripts that never open this repository's prose, and a blanket
# declaration would charge every documentation edit for all of them. Each test
# declares what it actually reads, and `tests/conftest.py` fails one that declares
# wrong.
ROOT = Path(__file__).resolve().parents[2]
#: The Nx target lists the root quality recipes route through, restated here
#: rather than read from the `justfile` — this suite exists to catch one of them
#: drifting. `coverage` is last in each: it waits on the measuring tier and enforces
#: the floor on what that tier wrote.
CHECK_TARGETS = "format-check,lint,typecheck,test,test-docs,test-recipes,coverage"
TEST_TARGETS = "test,test-docs,test-recipes,coverage"
UPGRADE_TARGETS = "build,lint,typecheck,test,test-docs,test-recipes,coverage"


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
        (
            "check",
            f"run-many -t {CHECK_TARGETS}",
        ),
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
    for name in ("preserved-log.sh", "coverage-total.sh", "workspace-install.sh"):
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
    shutil.copy2(ROOT / "scripts/comparison-base.sh", checkout / "scripts/comparison-base.sh")
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
    """Trace Bun, and let it provision what the real one would."""
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
    nx.parent.mkdir(parents=True, exist_ok=True)
    nx.touch()
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
def test_check_recipe_runs_the_combined_public_journey_with_concise_output(
    tmp_path: Path,
) -> None:
    checkout, trace = _recipe_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check: all deterministic checks passed\n"
    assert trace.read_text().splitlines() == [
        f"nx.sh run-many -t {CHECK_TARGETS}",
        "nx.sh run workspace:check-nx-cache",
    ]


@pytest.mark.reads_recipes
def test_check_recipe_preserves_captured_nx_failure(tmp_path: Path) -> None:
    checkout, trace = _recipe_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check", fail_command="nx.sh")

    assert result.returncode != 0
    assert "nx.sh: captured failure detail" in result.stderr
    assert "check: deterministic checks failed" in result.stderr
    assert trace.read_text().splitlines() == [f"nx.sh run-many -t {CHECK_TARGETS}"]


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


def _captures(trace: Path) -> list[str]:
    """Every Playwright capture the traced run made, as `<viewport> <url>`."""
    captures = []
    for line in trace.read_text().splitlines():
        match = re.fullmatch(
            r"bunx playwright screenshot --viewport-size=(\S+) --wait-for-timeout=\d+ ?"
            r"(?P<extra>.*?) ?(?P<url>http://\S+) \S+",
            line,
        )
        if match is not None:
            captures.append(f"{match.group(1)} {match.group('url')}")
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
    viewports = [capture.split(" ")[0] for capture in _captures(trace)]
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
    surfaces = sorted({capture.split(" ")[1].partition("/?")[2] for capture in _captures(trace)})
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
    """A checkout the *real* `scripts/nx.sh` runs in, with `bunx` doubled.

    Only Nx itself is replaced. `nx.sh`, `preserved-log.sh`, and
    `workspace-install.sh` are the real files, because what they choose to do —
    which log to write, and whether to provision the workspace first — is what is
    under test.
    """
    checkout = tmp_path / name
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "bin").mkdir()
    for script in ("nx.sh", "preserved-log.sh", "workspace-install.sh"):
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
    bunx = checkout / "bin" / "bunx"
    bunx.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'bunx %s\\n' "$*" >>"$TRACE_FILE"
""",
        encoding="utf-8",
    )
    bunx.chmod(0o755)


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
        "bunx nx run-many -t test",
    ]


@pytest.mark.reads_recipes
def test_nx_wrapper_skips_provisioning_once_the_workspace_is_installed(tmp_path: Path) -> None:
    """The heal is a no-op on every ordinary invocation, which is most of them."""
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
    assert trace.read_text().splitlines() == ["bunx nx run cached"]


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
    assert not trace.exists(), "a rejected invocation must not have run Bun"


def _sabotage_installer_state(checkout: Path, mode: str) -> None:
    """Break one of the pieces of its own state the installer has to open."""
    logs = checkout / ".logs"
    match mode:
        case "lock-directory":
            # A regular file where the directory belongs: `mkdir -p` refuses.
            logs.write_text("not a directory\n", encoding="utf-8")
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
        ("unreadable-lock", "cannot open the install lock at"),
        ("write-only-lock", "cannot open the install lock at"),
        ("unacquirable-lock", "cannot serialize the locked install"),
        ("unopenable-log", "preserved-log: cannot open"),
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
    assert trace.read_text().splitlines() == ["bun install --frozen-lockfile"]
    assert (checkout / "node_modules/.bin/nx").is_file()


#: One real journey carrying `requires_workspace_install`, run inside the fresh
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


@pytest.mark.reads_recipes
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
    assert result.stdout == "check: all deterministic checks passed (line coverage 96.42%)\n"


@pytest.mark.reads_recipes
def test_check_recipe_stays_green_when_no_coverage_artifact_exists(tmp_path: Path) -> None:
    """A missing artifact reports nothing; it must never turn a green tier red."""
    checkout, trace = _recipe_checkout(tmp_path)

    result = _recipe_run(checkout, trace, "check")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check: all deterministic checks passed\n"
    assert not (checkout / ".coverage").exists()


@pytest.mark.reads_recipes
def test_check_recipe_stays_green_when_the_coverage_total_is_unusable(tmp_path: Path) -> None:
    """An unavailable or malformed total is dropped, not reported and not fatal."""
    checkout, trace = _recipe_checkout(tmp_path)
    (checkout / ".coverage").write_text("")

    result = _recipe_run(checkout, trace, "check", FAKE_COVERAGE_TOTAL="No data to report.")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check: all deterministic checks passed\n"
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
    assert result.stdout == "check: all deterministic checks passed (line coverage 94.13%)\n"


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
