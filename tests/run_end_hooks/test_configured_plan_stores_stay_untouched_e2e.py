"""A launching journey that states no plan-store roots never reaches this checkout's.

Every other module here states its own runs, drafts and authoring roots, as this
project's `AGENTS.md` says a journey that fires a hook must. This one deliberately does
not: it launches through the real `just orchestrate`, whose success hook runs
`just follow-ups <run-id>` — which *reads* that run's follow-up drafts and *writes* a
project named after it — and leaves both roots to whatever the suite gives it. What
stands between such a launch and a developer's own `.plans` and `.follow-ups` is the
autouse isolation in `tests/conftest.py` and nothing else, so this is where that
isolation is proven rather than assumed.

The proof is a collision. A draft and a plan project are seeded under **this checkout's
configured roots** carrying the launched run's own id — exactly the records an
unisolated hook would read and overwrite — and a second draft is seeded under the
isolated drafts root so the hook has something to find and carries on into the write. It
then has to be true that the follow-up run's composed task names the isolated drafts
root, that the project it wrote landed under the isolated authoring root, and that both
seeded records come back out of the store byte for byte.

Everything between the recipe and the model is real: `just orchestrate`,
`scripts/orchestrate.sh`, `scripts/onepipeline.sh` and its design-approval gate, the
installed driver, `scripts/run-ended.sh`, `just follow-ups` and the detached follow-up
run it launches, the real drafting command both seeds are written with, and the
installed `onetaskgraph` both seeded records are read back through. **The paid model
alone is doubled**: `tests/e2e/fake_backend.py` at the `oneagentgraph` seam for the main
run's worker and `tests/e2e/fake_codex.py` at the provider binary for the follow-up
agent's single-sided member.
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

import follow_up_variables
import plan_root_variable
import pytest
import short_state
from harness_indirections import established_indirections
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import helper, project_from_plan
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import follow_up_drafts as drafts
from orchestrator import plan_store
from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs, so this module is
#: scheduled with every other journey that does, which also keeps its one fixture on one
#: worker.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The paid model's stand-ins, and the guard covering the identities neither seam reaches.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The launching session this module states, and everything an enclosing dispatch would
#: otherwise decide for this launch. **Neither plan-store root is on this list**, and that
#: is the whole point: this journey inherits whatever roots the suite put in its
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

#: The one node the launched plan holds, and what `just follow-ups` names the run and the
#: project it writes for the run it follows up.
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

#: The two seeded drafts, told apart by their titles: one under the roots this checkout
#: configures, which nothing in this journey may touch, and one under the isolated root,
#: which is what gives the hook something to carry on from.
CONFIGURED_TITLE = "A developer's own draft, which no test may read"
ISOLATED_TITLE = "The draft this journey's own launch leaves"
REPOSITORY = "github.com/nickderobertis/some-service"

#: What the seeded plan project says, so that a project the hook overwrote could not read
#: back as the seeded one.
SEEDED_GOAL = "A plan a developer authored, which no test may replace"


class Configured(NamedTuple):
    """The two roots this checkout configures, and the records seeded under them."""

    authoring: Path
    drafts: Path
    environment: dict[str, str]
    draft: Path
    draft_id: str
    project: str
    task_id: str


class Isolated(NamedTuple):
    """The two roots the suite's isolation states for this process."""

    authoring: Path
    drafts: Path


class Launched(NamedTuple):
    """The hooked launch, read back before anything is torn down."""

    run: str
    follow_up: str
    result: subprocess.CompletedProcess[str]
    log: str
    task: str
    configured: Configured
    isolated: Isolated
    draft_before: dict[str, object] | None
    draft_after: dict[str, object] | None
    task_before: dict[str, object] | None
    task_after: dict[str, object] | None


def _variables() -> tuple[str, str]:
    """The two variables the roots are stated by, each read from the one place it is composed."""
    return plan_root_variable.name(), follow_up_variables.root_name()


def _isolated() -> Isolated:
    """What the suite's autouse isolation put in this process's environment."""
    authoring, drafts_root = (os.environ.get(name) for name in _variables())
    assert authoring and drafts_root, (
        "this process was given no isolated plan-store roots, so this journey would be "
        "launching against the roots this checkout configures, which is the failure it "
        "exists to prevent"
    )
    return Isolated(Path(authoring), Path(drafts_root))


def _configured(monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """The roots `onetaskgraph.yaml` puts inside this checkout, read with the isolation off.

    Resolved through the store's own configuration, so what is seeded below is whatever
    this checkout really reads rather than a directory name joined onto its path.
    """
    for name in _variables():
        monkeypatch.delenv(name, raising=False)
    return (
        plan_store.ensure_writable_source_root("authoring"),
        plan_store.ensure_writable_source_root(drafts.SOURCE),
    )


def _drafted(root: Path, run: str, title: str) -> Path:
    """One follow-up drafted against ``run`` under ``root``, through the real command."""
    _, drafts_variable = _variables()
    drafted = subprocess.run(  # noqa: S603 - the drafting command every party drafts with
        [str(REPO_ROOT / "scripts" / "follow-up-draft.sh"), "--as", "manager", "--run", run]
        + ["--title", title, "--repository", REPOSITORY, "--path", "src/cursor.py"],
        cwd=REPO_ROOT,
        env={**os.environ, drafts_variable: str(root)},
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
    matched = re.fullmatch(r"drafted (?P<id>\S+) at (?P<path>\S+)\n", drafted.stdout)
    assert matched is not None, drafted.stdout
    return Path(matched["path"])


def _seeded_project(root: Path, native: str) -> None:
    """A plan project written under ``root`` through this repository's own writer.

    Out of process, because it is another program's records this is putting in place and
    because the writer is the one `just follow-ups` itself spawns — a project written any
    other way would be a shape this journey invented.
    """
    plan = {
        "schema_version": 3,
        "goal": {"text": SEEDED_GOAL},
        "name": native,
        "tasks": [
            {
                "id": "authored",
                "persona": "engineer",
                "task": f"## What\n{SEEDED_GOAL}\n\n## Why\nA developer wrote it.\n\n"
                "## Acceptance criteria\n- It is still here afterwards.",
            }
        ],
    }
    # llmlint: ignore-block[tests_mirror_real_usage] `python -m orchestrator.project_store <root>`
    # *is* how a plan reaches the authoring source here: `scripts/plan.sh:384` and
    # `scripts/finish-plan.sh:572` each pipe a planner's plan into that exact command line,
    # and it is the only way an authored project is written. Reaching for `just plan`
    # instead would spend a real planner dispatch to produce the one record this seed needs.
    written = subprocess.run(  # noqa: S603 - this repository's own project writer
        [
            str(REPO_ROOT / ".venv" / "bin" / "python3"),
            "-m",
            "orchestrator.project_store",
            str(root),
        ],
        cwd=REPO_ROOT,
        input=json.dumps(plan),
        text=True,
        capture_output=True,
        check=False,
    )
    # llmlint: ignore-end[tests_mirror_real_usage]
    assert written.returncode == 0, written.stdout + written.stderr
    assert written.stdout.strip() == native, written.stdout


def _record(
    environment: dict[str, str], qualified: str, *, required: bool = True
) -> dict[str, object] | None:
    """One record as the installed store reports it, read under the configured roots.

    ``required`` is what tells a seed that never landed from a record something removed:
    the first read of each seed demands one, and the read *after* the launch answers
    `None` for a record the store no longer holds, so the comparison below reports a
    record that was replaced rather than erroring in a fixture with the store's own
    "no task with that id".
    """
    shown = subprocess.run(  # noqa: S603 - the installed plan-store CLI, reading a seeded record
        [str(ONETASKGRAPH_BIN), "task", "show", qualified, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    if shown.returncode != 0 and not required:
        return None
    assert shown.returncode == 0, shown.stdout + shown.stderr
    payload: object = json.loads(shown.stdout)
    assert isinstance(payload, dict), payload
    items = payload["items"]
    assert isinstance(items, list) and len(items) == 1, items
    held = items[0]
    assert isinstance(held, dict), held
    item = held["item"]
    assert isinstance(item, dict), item
    return item


def _just(
    environment: dict[str, str], *arguments: str, seconds: float = 600
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own recipes
        # noqa: S607 - `just` from the search path, as an operator invokes it: this
        # journey's subject is the recipe reached the way the command surface reaches it,
        # and an absolute path would exercise a binary no caller here resolves.
        ["just", *arguments],  # noqa: S607
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
                        "task": "Report without changing files.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project_from_plan(plan, name)


def _launched_task(runs: Path, follow_up: str) -> str:
    """The task the engine recorded dispatching for the follow-up run."""
    plan: object = json.loads((runs / follow_up / "plan.json").read_text(encoding="utf-8"))
    assert isinstance(plan, dict), plan
    tasks = plan["tasks"]
    assert isinstance(tasks, list) and len(tasks) == 1, tasks
    node = tasks[0]
    assert isinstance(node, dict), node
    return str(node["task"])


def _remove(*paths: Path) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def launched(  # noqa: PLR0915 - one journey, seeded and read back in order
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[Launched]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("configured-plan-stores")
    isolated = _isolated()
    run = f"plan-store-isolation-{os.getpid()}"
    follow_up_native = f"{run}{FOLLOW_UPS_SUFFIX}"
    authoring_variable, drafts_variable = _variables()
    with pytest.MonkeyPatch.context() as patched:
        configured_authoring, configured_drafts = _configured(patched)
    assert configured_authoring.is_relative_to(REPO_ROOT), configured_authoring
    assert configured_drafts.is_relative_to(REPO_ROOT), configured_drafts
    # Read back under the configured roots, whatever the environment says elsewhere: this
    # is the reading a developer would make of their own records.
    reading = {
        **os.environ,
        authoring_variable: str(configured_authoring),
        drafts_variable: str(configured_drafts),
    }
    seeded_draft = _drafted(configured_drafts, run, CONFIGURED_TITLE)
    _seeded_project(configured_authoring, follow_up_native)
    configured = Configured(
        authoring=configured_authoring,
        drafts=configured_drafts,
        environment=reading,
        draft=seeded_draft,
        draft_id=f"{drafts.SOURCE}:{run}/{drafts.DRAFTS}/{seeded_draft.stem}",
        project=follow_up_native,
        task_id=f"authoring:{follow_up_native}/authored",
    )
    draft_before = _record(reading, configured.draft_id)
    task_before = _record(reading, configured.task_id)
    # What the hook finds instead, so it carries on past its no-drafts ending and into the
    # write a collision would land on the seeded project.
    _drafted(isolated.drafts, run, ISOLATED_TITLE)

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
                "--timeout",
                str(FOLLOW_UP_SETTLE_SECONDS),
                seconds=FOLLOW_UP_SETTLE_SECONDS + 60,
            )
        yield Launched(
            run=run,
            follow_up=follow_up,
            result=result,
            log=log,
            task=_launched_task(runs, follow_up) if follow_up else "",
            configured=configured,
            isolated=isolated,
            draft_before=draft_before,
            draft_after=_record(reading, configured.draft_id, required=False),
            task_before=task_before,
            task_after=_record(reading, configured.task_id, required=False),
        )
    finally:
        for stopping in (run, follow_up):
            if stopping and (runs / stopping).exists():
                _just(environment, "stop", stopping, seconds=60)
        _remove(
            configured_drafts / "tasks" / run,
            configured_drafts / "projects" / f"{run}.md",
            configured_authoring / "tasks" / follow_up_native,
            configured_authoring / "projects" / f"{follow_up_native}.md",
        )


def test_the_hook_reads_the_isolated_drafts_root_and_not_the_one_this_checkout_configures(
    launched: Launched,
) -> None:
    """The follow-up run was composed against the isolated root, over the isolated draft.

    The composed task is where the root the agent is sent to actually appears, so this is
    the read half of the claim: a hook that had resolved the configured root would have
    named it here and told its agent to verify a draft nobody asked it to read.
    """
    assert launched.result.returncode == 0, launched.result.stdout + launched.result.stderr
    assert launched.follow_up == f"{launched.run}{FOLLOW_UPS_SUFFIX}", launched.log

    assert str(launched.isolated.drafts) in launched.task, launched.task
    assert str(launched.configured.drafts) not in launched.task, (
        "the follow-up agent was sent to the drafts root this checkout configures, which "
        "holds a developer's own drafts"
    )
    assert CONFIGURED_TITLE not in launched.task, launched.task


def test_the_follow_up_project_lands_under_the_isolated_authoring_root(
    launched: Launched,
) -> None:
    """The write half: the project the recipe wrote is in the suite's root, not the checkout's."""
    native = f"{launched.run}{FOLLOW_UPS_SUFFIX}"

    assert (launched.isolated.authoring / "projects" / f"{native}.md").is_file(), (
        f"no {native} project under {launched.isolated.authoring}; the launch wrote its "
        "follow-up project somewhere this journey did not state"
    )


def test_the_seeded_draft_and_plan_this_checkout_holds_come_back_unchanged(
    launched: Launched,
) -> None:
    """Neither seeded record was read, consumed or replaced by a launch with the same run id."""
    assert launched.configured.draft.is_file(), (
        f"{launched.configured.draft} is gone; a launch consumed a draft under the root "
        "this checkout configures"
    )
    # Whole records rather than chosen fields: what a collision does to one of these is
    # not this journey's to anticipate, and a comparison of the fields it happened to
    # think of would pass over the rest.
    assert CONFIGURED_TITLE in json.dumps(launched.draft_before), launched.draft_before
    assert launched.draft_after == launched.draft_before, (
        "the draft under the root this checkout configures came back changed"
    )
    assert SEEDED_GOAL in json.dumps(launched.task_before), launched.task_before
    assert launched.task_after == launched.task_before, (
        "the plan under the root this checkout configures came back changed"
    )
