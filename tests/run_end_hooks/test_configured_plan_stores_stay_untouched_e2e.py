"""A launching journey that states no plan-store roots never reaches this checkout's.

Every other module here states its own runs root and follow-up store, as this project's
`AGENTS.md` says a journey that fires a hook must. This one deliberately does not: it
launches through the real `just orchestrate`, whose success hook runs `just follow-ups
<run-id>` — which *writes* the run's follow-up plan into the `follow-up-runs` source and
launches it, whose `onefollowup prepare` *reads* the run's drafts from the `drafts` source
and writes onefollowup's working files beside its store — and leaves every one of those to
whatever the suite gives it. What stands between such a launch and a developer's own
`.follow-ups`, `.follow-ups/runs` and `.onefollowup` is the autouse isolation in
`tests/conftest.py` and nothing else, so this is where that isolation is proven rather than
assumed.

The proof is a collision. A draft of the launched run's batch and a plan project named as
`just follow-ups` names the run's follow-up plan are seeded under **this checkout's
configured roots** — exactly the records an unisolated hook would read and overwrite — and
a second draft is seeded under the isolated drafts root. It then has to be true that the
follow-up plan landed under the isolated `follow-up-runs` root, that the batch onefollowup
prepared holds the isolated draft and not the configured one, that its working files are
under the isolated store and none under this checkout, and that both seeded records come
back byte for byte.

Everything between the recipe and the model is real: `just orchestrate`,
`scripts/orchestrate.sh`, `scripts/onepipeline.sh`, the installed driver,
`scripts/run-ended.sh`, `just follow-ups` and the detached follow-up run it launches, and
the pinned `onefollowup` both drafts are written with. **The paid model alone is doubled**:
`tests/e2e/fake_backend.py` at the `oneagentgraph` seam for the main run's worker and
`tests/e2e/fake_codex.py` at the provider binary for the follow-up agent's member.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import boundary_home
import follow_up_store
import follow_up_variables
import plan_store_client
import pytest
import short_state
from harness_indirections import established_indirections
from project_fixtures import helper, project_from_plan
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

# What the recipes these journeys run leave in the temporary directory, by design or
# from a published tool they run, is this module's own and goes with it
# (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: The paid model's stand-ins, and the guard covering the identities neither seam reaches.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The pinned onefollowup, which both drafts are written with.
ONEFOLLOWUP = REPO_ROOT / ".venv" / "bin" / "onefollowup"

#: The launching session this module states, and everything an enclosing dispatch would
#: otherwise decide for this launch. **No plan-store root and no follow-up store is on this
#: list**, and that is the whole point: this journey inherits whatever the suite put in its
#: environment, the way a journey that never thought about them does.
LAUNCHING_SESSION = "e2e-configured-plan-stores"
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_HOOK",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_RUN_ROOT",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ONEVCS_SESSION",
    "ORCHESTRATOR_ASK_MANAGER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "FAKE_CODEX_HOLD_SECONDS",
)

#: Who the indirection helpers attribute their diagnostics to.
INDIRECTION_CALLER = "tests/run_end_hooks/test_configured_plan_stores_stay_untouched_e2e.py"

#: The one node the launched plan holds, and what `just follow-ups` names the follow-up plan
#: and run for the run it follows up.
NODE = "report"
FOLLOW_UPS_SUFFIX = "-follow-ups"

#: The line the success hook prints once it has launched the follow-up run.
LAUNCHED = re.compile(
    r"^main work of run (?P<run>\S+) is complete; follow-ups are being verified in run "
    r"(?P<follow_up>\S+); watch it with: just watch (?P<watched>\S+)$",
    re.MULTILINE,
)

#: How long the detached follow-up run, whose one turn is a stand-in, is given to settle.
FOLLOW_UP_SETTLE_SECONDS = 300

#: The two seeded drafts, told apart by their titles: one under the root this checkout
#: configures, which nothing in this journey may touch, and one under the isolated root,
#: which is what the follow-up run should prepare.
CONFIGURED_TITLE = "A developer's own draft, which no test may read"
ISOLATED_TITLE = "The draft this journey's own launch leaves"
#: The repository both drafts are about, registered public in a registry of this module's
#: own: the store classifies a draft by its repository, so both seeds are recorded public, as
#: a draft about this repository is on any host.
REPOSITORY = boundary_home.THIS_REPOSITORY

#: The source the follow-up recipes create their plans in.
RUNS_SOURCE = "follow-up-runs"


class Configured(NamedTuple):
    """The two roots this checkout configures, and the records seeded under them."""

    drafts: Path
    runs: Path
    draft: Path
    project: Path


class Launched(NamedTuple):
    """The hooked launch, read back before anything is torn down."""

    run: str
    follow_up: str
    result: subprocess.CompletedProcess[str]
    log: str
    configured: Configured
    isolated_runs: Path
    store: Path
    draft_before: bytes
    project_before: bytes
    draft_after: bytes | None
    project_after: bytes | None


def _isolated() -> tuple[Path, Path, Path]:
    """The drafts root, follow-up-runs root and follow-up store the suite's isolation states."""
    named = (
        follow_up_variables.root_name(),
        follow_up_store.RUNS_ROOT,
        follow_up_variables.ONEFOLLOWUP_STORE,
    )
    missing = [name for name in named if not os.environ.get(name)]
    assert not missing, (
        f"this process was given no isolated {', '.join(missing)}, so this journey would be "
        "launching against what this checkout configures, which is the failure it exists to "
        "prevent"
    )
    drafts, runs, store = (Path(os.environ[name]) for name in named)
    return drafts, runs, store


def _configured(monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """The drafts and follow-up-runs roots `onetaskgraph.yaml` puts inside this checkout.

    Read with the isolation off and resolved through the store's own configuration, so what
    is seeded below is whatever this checkout really reads.
    """
    monkeypatch.delenv(follow_up_variables.root_name(), raising=False)
    monkeypatch.delenv(follow_up_store.RUNS_ROOT, raising=False)
    return (
        plan_store_client.ensure_writable_source_root(follow_up_variables.source()),
        plan_store_client.ensure_writable_source_root(RUNS_SOURCE),
    )


def _drafted(drafts: Path, run: str, title: str, registry: Path) -> Path:
    """One draft of ``run``'s batch under ``drafts``, through the pinned onefollowup.

    `onefollowup draft` itself rather than the drafting wrapper, because the wrapper reads the
    batch from the engine's record of the run, and this draft is seeded before the run exists.
    """
    drafted = subprocess.run(  # noqa: S603 - the pinned onefollowup, as the wrapper calls it
        [str(ONEFOLLOWUP), "draft", "--batch", run, "--author", "manager"]
        + ["--title", title, "--repository", REPOSITORY, "--path", "src/cursor.py"],
        cwd=REPO_ROOT,
        env={
            **boundary_home.environment(None, registry),
            follow_up_variables.root_name(): str(drafts),
            # The owner the review sink names, as the launching session's launch exports it.
            "ONEFOLLOWUP_HOOKS__OWNER__SESSION": LAUNCHING_SESSION,
        },
        input=(
            "## What happened\nThe cursor skipped a page.\n\n## Where\n`src/cursor.py`.\n\n"
            "## Why it is out of scope\nThe run was about something else.\n\n"
            "## Evidence\nPage 9 of 9 never rendered.\n"
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr
    return Path(json.loads(drafted.stdout)["path"])


def _seeded_project(runs: Path, native: str) -> Path:
    """A plan project under ``runs`` named as `just follow-ups` names the run's follow-up plan."""
    created = subprocess.run(  # noqa: S603 - the locked plan-store CLI
        [str(ONETASKGRAPH_BIN), "project", "create", RUNS_SOURCE, "--id", native]
        + ["--title", "A plan a developer authored, which no test may replace"],
        cwd=REPO_ROOT,
        env={**os.environ, follow_up_store.RUNS_ROOT: str(runs)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    project = runs / "projects" / f"{native}.md"
    assert project.is_file(), sorted(runs.rglob("*"))
    return project


def _just(
    environment: dict[str, str], *arguments: str, seconds: float = 600
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own recipes
        ["just", *arguments],  # noqa: S607 - `just` as an operator invokes it
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _environment(tmp: Path, oneharness_bin: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in INHERITED:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp))
    environment["ONEAGENTGRAPH_STATE_DIR"] = str(tmp / "graph-state")
    return environment


def _project(tmp: Path, name: str) -> str:
    """A launchable one-node project whose run id is `name`."""
    plan = tmp / f"{name}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Report once, so the run ends and its hook fires"},
                "name": name,
                "tasks": [
                    {
                        "id": NODE,
                        "persona": "engineer",
                        "task": (
                            "Report without changing files.\n\n## Acceptance criteria\n- Reported."
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project_from_plan(plan, name)


def _bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


@pytest.fixture(scope="module")
def launched(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Launched]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("configured-plan-stores")
    isolated_drafts, isolated_runs, store = _isolated()
    run = f"plan-store-isolation-{os.getpid()}"
    registry = boundary_home.registered_home(tmp)
    with pytest.MonkeyPatch.context() as patched:
        configured_drafts, configured_runs = _configured(patched)
    assert configured_drafts.is_relative_to(REPO_ROOT), configured_drafts
    assert configured_runs.is_relative_to(REPO_ROOT), configured_runs
    assert not isolated_drafts.is_relative_to(REPO_ROOT), isolated_drafts
    configured = Configured(
        drafts=configured_drafts,
        runs=configured_runs,
        draft=_drafted(configured_drafts, run, CONFIGURED_TITLE, registry),
        project=_seeded_project(configured_runs, f"{run}{FOLLOW_UPS_SUFFIX}"),
    )
    draft_before, project_before = configured.draft.read_bytes(), configured.project.read_bytes()
    _drafted(isolated_drafts, run, ISOLATED_TITLE, registry)

    environment = _environment(tmp, oneharness_bin)
    runs = Path(environment["ONEPIPELINE_RUNS_DIR"])
    follow_up = ""
    try:
        result = _just(environment, "orchestrate", _project(tmp, run), "--dag-graph", "off")
        log_path = runs / run / "hooks" / "success.log"
        log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
        matched = LAUNCHED.search(log)
        follow_up = matched["follow_up"] if matched else ""
        if follow_up:
            _just(
                environment,
                "watch",
                follow_up,
                "--until",
                "settled",
                "--timeout",
                str(FOLLOW_UP_SETTLE_SECONDS),
                seconds=FOLLOW_UP_SETTLE_SECONDS + 60,
            )
        yield Launched(
            run=run,
            follow_up=follow_up,
            result=result,
            log=log,
            configured=configured,
            isolated_runs=isolated_runs,
            store=store,
            draft_before=draft_before,
            project_before=project_before,
            draft_after=_bytes(configured.draft),
            project_after=_bytes(configured.project),
        )
    finally:
        for stopping in (run, follow_up):
            if stopping and (runs / stopping).exists():
                _just(environment, "stop", stopping, seconds=60)
        for seeded in (
            configured.draft,
            configured.project,
            configured_drafts / "projects" / f"{run}.md",
        ):
            seeded.unlink(missing_ok=True)


def test_the_follow_up_plan_lands_under_the_isolated_runs_root(launched: Launched) -> None:
    """The recipe's write: the follow-up plan is in the suite's root, not the checkout's."""
    assert launched.result.returncode == 0, launched.result.stdout + launched.result.stderr
    assert launched.follow_up == f"{launched.run}{FOLLOW_UPS_SUFFIX}", launched.log
    written = launched.isolated_runs / "projects" / f"{launched.follow_up}.md"
    assert written.is_file(), (
        f"no {launched.follow_up} project under {launched.isolated_runs}; the launch wrote "
        "its follow-up plan somewhere this journey did not state"
    )


def test_the_batch_is_prepared_from_the_isolated_drafts_into_the_isolated_store(
    launched: Launched,
) -> None:
    """onefollowup's read and its working files: the isolated draft, in the isolated store."""
    state_path = launched.store / ".onefollowup" / "state" / f"{launched.run}.json"
    assert state_path.is_file(), (state_path, launched.log)
    titles = [entry.get("title") for entry in json.loads(state_path.read_text("utf-8"))["inputs"]]
    assert ISOLATED_TITLE in titles, titles
    assert CONFIGURED_TITLE not in titles, (
        "the follow-up batch was prepared from the drafts root this checkout configures, "
        "which holds a developer's own drafts"
    )
    assert not (REPO_ROOT / ".onefollowup" / "state" / f"{launched.run}.json").exists()


def test_the_seeded_draft_and_plan_this_checkout_holds_come_back_unchanged(
    launched: Launched,
) -> None:
    """Neither seeded record was read, consumed or replaced by a launch with the same run id."""
    assert launched.draft_after == launched.draft_before, (
        "the draft under the root this checkout configures came back changed or gone"
    )
    assert launched.project_after == launched.project_before, (
        "the plan under the root this checkout configures came back changed or gone"
    )
