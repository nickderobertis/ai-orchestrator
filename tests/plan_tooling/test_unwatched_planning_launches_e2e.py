"""The planning flow's two launches leave runs this session owns and the verb names.

`just plan` makes two launches — the planner's, and the design-document run its tail
launches — and AGENTS.md's watch rule singles them out: they are the launches most likely
to go unwatched, because each names `--dag-graph off` and so watches least of itself. That
is precisely where `onepipeline unwatched` has to answer, and it answers only about runs
whose launch record names the asked-about session. A launch that recorded nobody would be
passed over in silence, which reads exactly like a run nothing is wrong with.

Both are read off **one** flow, driven in the background while this journey polls: the two
runs are live one after the other and each is unsettled only while it is running, so a
flow run to completion and read afterwards would answer about two runs that had both
finished. That is also why nothing here asserts the flow's own output — what it produces
is `tests/plan_tooling/test_plan_flow_e2e.py`'s subject, and this journey deliberately
ends the flow as soon as it has seen what it came for.

Everything below the recipe is real: the real `just plan`, `scripts/plan.sh` and
`scripts/finish-plan.sh`, the real `just review-plan` and its `oneharness` turn, the real
`orchestrator-check-plan`, the real `onepipeline` driver for both launches, and the
installed engine answering every read. The paid provider is the one thing substituted.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This *is* the edge: a
journey that drives a whole planning flow is what `tests/plan_tooling/` is the Nx project
for, keyed on `planToolingWorkspace` and selected by directory rather than by marker, which
`tests/AGENTS.md` states as the settled design and `tests/conftest.py` enforces. A project
per journey inside it would give one key nothing enforces beside the one that is.

llmlint: ignore-file[tests_mirror_real_usage] Two readings here are of the flow's own
records rather than of a view. The plan is written through this repository's own record
renderer instead of authored by a dispatch, because what these journeys are about is the
two *launches* and not what either dispatch produces — a plan already in the store leaves
the planner run free to be exactly what it is here. And ownership is read off each run's
launch record, because no view answers it: a view reports about runs the reader already
owns, so it would confirm ownership out of the answer whose correctness is the question.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_backend import AGENT_DELAY_ENV, PROMPT_LOG_ENV
from plan_fixture_root import ROOT as FIXTURE_ROOT
from project_fixtures import helper
from scratch_identity import PLANNING_FLOW_ORIGIN, seeded
from waits import timeout as e2e_timeout

from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: This journey is its own Nx project's, `plan-tooling`; see
#: `tests/plan_tooling/project.json` and the guard in `tests/conftest.py`.

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The engine this checkout installs from its own pin, asked directly: the recipe is what
#: launches, and what has to answer is the binary a manager's hook would ask.
ONEPIPELINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"

#: The status the verb answers when a run the session owns has nothing watching it.
RUNS_UNWATCHED = 6

#: A launching session this journey states rather than inherits, and everything else an
#: enclosing dispatch would otherwise decide for it.
LAUNCHING_SESSION = "e2e-unwatched-planning-launches"
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The two aliases this repository's own identity registers, seeded against a scratch
#: registry: the nodes this flow writes are direct nodes and open no `onevcs` session,
#: and the scratch one is what a recipe that started opening sessions again would reach
#: rather than this host's.
PUBLICATION_ALIAS = "ai-orchestrator"
EXECUTION_ALIAS = "ai-orchestrator-isolated"

#: The store a plan is drafted in here, the source both launches read out of, and the
#: second local store the flow copies into.
FIXTURE_SOURCE = "test-fixtures"
AUTHORING_SOURCE = "authoring"
DESTINATION = "destination"

#: The run this flow launches under, and the run its tail derives from that name.
RUN = "unwatched-planning-flow"
DESIGN_RUN = f"{RUN}-design"

#: How long each stand-in turn takes. The two runs are unsettled only while their own
#: dispatch is working, so this is what gives a poll something to see — and it is a
#: property of the *journey* rather than of the flow: a real turn takes minutes.
TURN_SECONDS = 25

#: A review verdict that passes, in the shape `config/plan-review-verdict.schema.json`
#: admits, so the tail reaches its own launch. What `just review-plan` *decides* is
#: `tests/plan_tooling/test_plan_review_e2e.py`'s; here it is only the step between the
#: two launches this journey is about.
PASSING_VERDICT = json.dumps({"passes": True, "findings": []})

#: Criteria that answer every demand the tracked appendix and the shipped `engineer` bar
#: make, so `just check-plan` refuses this plan for nothing this journey is not about.
CRITERIA = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as it "
    "finally stands."
)


class Flow(NamedTuple):
    """One planning flow in flight: the environment it runs in, and its runs root."""

    environment: dict[str, str]
    runs: Path


def _plan_project(native: str) -> str:
    """One plan for the flow's tail to finish, already in the store the brief names.

    Written rather than authored by the planner's stand-in, because what this journey is
    about is the two *launches* and not what either dispatch produces: a plan already in
    the store leaves the planner run free to be exactly what it is here — a real launch,
    a real driver, and a dispatch that reports and stops.
    """
    write_plan_project(
        FIXTURE_ROOT,
        {
            "schema_version": 3,
            "goal": {"text": "Deliver the paginated listing"},
            "name": native,
            "tasks": [
                {
                    "id": "decide-the-cursor",
                    "persona": "engineer",
                    "title": "feat: page the node listing",
                    "task": (
                        "## What\n\nAdd the paginated listing and the test that drives it.\n\n"
                        "## Why\n\nAn operator cannot see past the first screen of nodes.\n\n"
                        f"## Acceptance criteria\n\n{CRITERIA}\n\n"
                        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
                    ),
                }
            ],
        },
        native_id=native,
    )
    return f"{FIXTURE_SOURCE}:{native}"


def _flow(tmp_path: Path, oneharness_bin: str) -> Flow:
    """The environment one planning flow runs in, against stores and a registry of its own."""
    identity = seeded(
        tmp_path,
        publication=PUBLICATION_ALIAS,
        execution=EXECUTION_ALIAS,
        origin=PLANNING_FLOW_ORIGIN,
    )
    destination = tmp_path / "board"
    # Created rather than left to the first write: a `local-md` source canonicalizes its
    # root when it is built, so an absent one is refused as a broken source.
    destination.mkdir(parents=True)
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEVCS_HOME"] = str(identity.home)
    environment.update(identity.environment)
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # The real CLI the stand-in delegates every turn to: without it the stand-in cannot
    # answer at all, and a dispatch that fails outright settles the run this journey has
    # to catch while it is unsettled.
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `tests/e2e/no_paid_provider.py`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([PASSING_VERDICT])
    environment[AGENT_DELAY_ENV] = str(TURN_SECONDS)
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment[f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__PLUGIN"] = "local-md"
    environment[f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__CONFIG__ROOT"] = str(destination)
    # `authoring` is in this list because both launches of the flow read the project each
    # writes for itself out of it.
    environment["ONETASKGRAPH_DEFAULT_SOURCES"] = (
        f"{AUTHORING_SOURCE},{FIXTURE_SOURCE},{DESTINATION}"
    )
    return Flow(environment=environment, runs=Path(environment["ONEPIPELINE_RUNS_DIR"]))


def _unwatched(flow: Flow) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the installed engine, named by absolute path
        [str(ONEPIPELINE), "unwatched", "--session", LAUNCHING_SESSION],
        cwd=REPO_ROOT,
        env=flow.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 300
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the real recipe, run as an operator runs it
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


class Observed(NamedTuple):
    """What one flow left behind: the runs the verb named, and the root holding them."""

    named: frozenset[str]
    runs: Path
    reported: str


@pytest.fixture(name="observed", scope="module")
def _observed(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Observed]:
    """Run one planning flow, and hand back every run the verb named while it ran.

    Module-scoped, because one flow answers for both launches and a fixture per launch
    would pay for two: the planner's run and the tail's are live one after the other in
    a single `just plan`, which is the whole reason they are read from one poll.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    if not ONEPIPELINE.is_file():
        pytest.skip(f"this checkout has no installed engine at {ONEPIPELINE}")
    tmp_path = tmp_path_factory.mktemp("unwatched-planning")
    flow = _flow(tmp_path, oneharness_bin)
    project = _plan_project(f"test-{os.getpid()}-unwatched-planning")
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(
        "## What\nDecide the cursor's shape.\n\n"
        f"Plan project: {project}\n\n"
        "## Why\nThe view cannot deep-link until it is settled.\n\n"
        "## Acceptance criteria\n- The cursor's shape and its type are stated.\n",
        encoding="utf-8",
    )
    streamed = tmp_path / "flow.log"
    named: set[str] = set()
    with streamed.open("w", encoding="utf-8") as sink:
        # A file rather than a pipe: this flow streams two attached runs, and a pipe
        # nobody drains fills and wedges the very launches being observed.
        running = subprocess.Popen(  # noqa: S603 - the real recipe, run as an operator runs it
            ["just", "plan", str(brief), "--name", RUN, "--to", DESTINATION],
            cwd=REPO_ROOT,
            env=flow.environment,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            limit = time.monotonic() + e2e_timeout(1200)
            while time.monotonic() < limit and not {RUN, DESIGN_RUN} <= named:
                answered = _unwatched(flow)
                if answered.returncode == RUNS_UNWATCHED:
                    named.update(run for run in (RUN, DESIGN_RUN) if run in answered.stdout)
                if running.poll() is not None:
                    # The flow is over. Whatever was seen is the answer: polling a
                    # finished flow for a run that is no longer live would only spend
                    # the bound before saying the same thing.
                    break
                time.sleep(0.5)
            yield Observed(
                named=frozenset(named),
                runs=flow.runs,
                reported=streamed.read_text(encoding="utf-8"),
            )
        finally:
            running.kill()
            running.wait(timeout=e2e_timeout(120))
            for ended in (RUN, DESIGN_RUN):
                _just("stop", ended, environment=flow.environment, seconds=120)
                (REPO_ROOT / ".plans" / "projects" / f"{ended}.md").unlink(missing_ok=True)


@pytest.mark.parametrize("launched", [RUN, DESIGN_RUN], ids=["planner", "design-document"])
def test_each_planning_launch_is_named_by_the_verb_while_nothing_watches_it(
    launched: str, observed: Observed
) -> None:
    """The planner's run and the tail's, each named while it was unsettled and unwatched.

    AGENTS.md's watch rule names these two together because a supervisor who armed a
    watch on the planner alone is blind for the whole of the second — and neither run has
    a monitor of its own, by design, so the verb is the only thing that can say so.
    """
    assert launched in observed.named, (
        f"the verb never named run {launched} while the planning flow was running, so a "
        "manager who left it unwatched would be told nothing about it. It named "
        f"{sorted(observed.named)}. The flow reported:\n{observed.reported}"
    )


@pytest.mark.parametrize("launched", [RUN, DESIGN_RUN], ids=["planner", "design-document"])
def test_each_planning_launch_records_the_session_that_made_it(
    launched: str, observed: Observed
) -> None:
    """Ownership, read off the launch record rather than off the verb's answer.

    The weaker claim is the verb's: one that had stopped comparing sessions would name
    these runs just the same. What has to be true is that each launch *recorded* the
    session it ran under — a run attributed to nobody is passed over in silence, which
    from a manager's seat is indistinguishable from a run nothing is wrong with.
    """
    record = observed.runs / launched / "launch.json"
    assert record.is_file(), (
        f"the flow left no launch record at {record}. It reported:\n{observed.reported}"
    )
    session = json.loads(record.read_text(encoding="utf-8")).get("session")
    assert session == LAUNCHING_SESSION, (
        f"run {launched} records session {session!r} where the flow ran as "
        f"{LAUNCHING_SESSION!r}, so nothing this session asks about would ever reach it"
    )
