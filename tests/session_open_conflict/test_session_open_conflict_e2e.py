"""A continued branch whose base moved to conflict with it is its worker's to merge.

A branch a node continues whose base moved to conflict with it is merged by the
dispatched worker, under its judge, and never by a person. `onevcs` 0.37.0 opens such a
session with the merge left in progress and reports it as the session's `conflict`.
onepipeline 0.57.3 dispatches the worker into it: the conflict goes under `## Planner context`, and
`### Merge resolution` closes the task's `## Acceptance criteria`, so the worker's judge
reviews the merge commit that concludes it. A worker that does not conclude it spends
the node's publication budget. Only then does the node settle `failed`/`sync-conflict`
and raise the blocking `session-conflict` finding, which asks for a `retry` and never
for a hand merge. `config/onepipeline.version` and `config/onevcs.version` put that in
force here, so this drives it the way a run on this host meets it: the real `just
orchestrate`, the real `scripts/onepipeline.sh`, the real driver and its linked
`onevcs`, and a real publication into a real origin.

Two cases, each with the same world: a stand-in worker that concludes the merge and one
that never does. The paid model is `tests/e2e/fake_backend.py` for both sides of the
conversation and `tests/e2e/fake_codex.py` for the single-sided members. The one
decision of the model's that is substituted is the worker's resolution: real git run
where the dispatch runs, by `FAKE_BACKEND_RUN_ON_MARKER`, keyed on the engine's own
`### Merge resolution` heading. So a worker that was never handed the criterion never
resolves anything.

The identity is a scratch one, a bare origin and two clones of it registered against a
scratch `ONEVCS_HOME`, because a test may not register or publish from this host's own
checkouts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, cast

import pytest
import short_state
from fake_backend import JUDGE_CONFIG_NAME, MEMBER_OF_CONFIG, PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from harness_indirections import established_indirections
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: Every step is a `just` recipe reaching its tool through `uv run`, which waits on this
#: checkout's `.venv` lock, so the journey joins the group that serialises it.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The paid model's stand-ins, and the guard covering the identities neither reaches.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
INDIRECTION_CALLER = "tests/session_open_conflict/test_session_open_conflict_e2e.py"

#: The registered alias of the checkout a session clones from.
EXECUTION_ALIAS = "execution"
BASE = "main"
#: Merged in the local checkout, naming no verifier: publication is reached for real, and
#: the scratch origin has no `pre-push` hook for the merge path to find.
RULES = """version: 3
trailer_prefix: Orchestrator-
rules:
  - match: {path: "*"}
    publication: local-direct
    approvals: none
default:
  publication: local-direct
  approvals: none
"""

NODE_ID = "service"
#: The branch the node continues, which exists before the run and which the base then
#: moves to conflict with.
CONFLICTED = "feature/conflicted"
#: The file the branch and the base each take a different version of.
CONFLICTED_PATH = "service.md"
BRANCH_VERSION = "the branch wrote this\n"
BASE_VERSION = "the base wrote this instead\n"
BASE_SUBJECT = "feat: take the file another way"
#: What the stand-in worker resolves the conflict to, which is what proves its merge landed.
RESOLVED = "the branch and the base, reconciled\n"

#: The engine's own words, quoted from onepipeline's `plan::MERGE_RESOLUTION_HEADING`,
#: `plan::MERGE_RESOLUTION_PREAMBLE` and the planner context its conflict journeys hold, so
#: a paraphrase cannot pass here while the task says something else.
MERGE_RESOLUTION_HEADING = "### Merge resolution"
MERGE_RESOLUTION_PREAMBLE = (
    "The engine added this criterion because this dispatch's session opened with an "
    "unfinished merge of its base into its branch."
)
PLANNER_CONTEXT_HEADING = "## Planner context"
OPENED_CONFLICTED = "opened with a merge in progress"
#: The key the blocking finding is raised under, and the words it says how many times
#: the worker was dispatched into the conflict with, as onepipeline's `engine` renders it.
FINDING_KEY = "session-conflict"
DISPATCHED_INTO = "dispatched into the conflict with the base {count} times"


class Worker(StrEnum):
    """What the stand-in worker does with the merge it is handed."""

    CONCLUDES = "concludes"
    NEVER_CONCLUDES = "never-concludes"


#: The publication budget, low enough for the case that spends it to be quick.
ATTEMPTS = 2

LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)
GIT_IDENTITY = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")
#: Who the commits `onevcs` makes on a session's behalf are by: the driver it commits in is
#: started before the suite's per-test identity is.
AUTHOR = {
    "GIT_AUTHOR_NAME": "ai-orchestrator-test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "ai-orchestrator-test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(*arguments: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, f"git {' '.join(arguments)}: {done.stderr or done.stdout}"
    return done.stdout.strip()


class World(NamedTuple):
    """A scratch identity holding a branch its moved base conflicts with."""

    root: Path
    origin: Path
    publication: Path
    environment: dict[str, str]
    #: The base commit the branch conflicts with, and the branch's own tip.
    base: str
    tip: str


class Conflicted(NamedTuple):
    """A bare origin whose base has moved to conflict with `CONFLICTED`."""

    origin: Path
    #: The base commit the branch conflicts with.
    base: str
    #: The branch's own tip.
    tip: str


def _conflicted_branch(root: Path) -> Conflicted:
    """A bare origin whose `main` has moved to conflict with `CONFLICTED`.

    The branch is cut from the seed and published with one version of `CONFLICTED_PATH`,
    then the base takes another, which is the state a continued branch meets after
    somebody else's work landed.
    """
    seed = root / "seed"
    _git("init", "-q", "-b", BASE, str(seed), cwd=root)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git(*GIT_IDENTITY, "commit", "-qm", "feat: seed", cwd=seed)
    origin = root / "origin.git"
    _git("init", "-q", "--bare", "-b", BASE, str(origin), cwd=root)
    _git("remote", "add", "origin", str(origin), cwd=seed)
    _git("push", "-q", "origin", BASE, cwd=seed)

    _git("checkout", "-q", "-b", CONFLICTED, cwd=seed)
    (seed / CONFLICTED_PATH).write_text(BRANCH_VERSION, encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git(*GIT_IDENTITY, "commit", "-qm", "feat: take the file one way", cwd=seed)
    _git("push", "-q", "origin", CONFLICTED, cwd=seed)
    tip = _git("rev-parse", "HEAD", cwd=seed)

    _git("checkout", "-q", BASE, cwd=seed)
    (seed / CONFLICTED_PATH).write_text(BASE_VERSION, encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git(*GIT_IDENTITY, "commit", "-qm", BASE_SUBJECT, cwd=seed)
    _git("push", "-q", "origin", BASE, cwd=seed)
    base = _git("rev-parse", "HEAD", cwd=seed)
    return Conflicted(origin, base, tip)


def _world(root: Path, oneharness_bin: str, worker: Worker) -> World:
    home = root / "onevcs"
    home.mkdir()
    conflicted = _conflicted_branch(root)
    origin = conflicted.origin
    publication = root / "publication"
    execution = root / EXECUTION_ALIAS
    _git("clone", "-q", str(origin), str(publication), cwd=root)
    _git("clone", "-q", str(origin), str(execution), cwd=root)

    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = f"e2e-session-open-conflict-{worker}"
    environment.update(AUTHOR)
    environment["ONEVCS_HOME"] = str(home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["ONEPIPELINE_PUBLICATION_ATTEMPTS"] = str(ATTEMPTS)
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment[PROMPT_LOG_ENV] = str(root / "turns.jsonl")
    # The one decision of the model's substituted: a worker handed the merge-resolution
    # criterion resolves the path and commits the merge, and a worker that never
    # concludes it is handed the same criterion and does nothing.
    resolution: list[list[str]] = []
    if worker is Worker.CONCLUDES:
        resolution = [
            ["sh", "-c", f"printf '{RESOLVED.strip()}\\n' > {CONFLICTED_PATH}"],
            ["git", "add", "-A"],
            ["git", *GIT_IDENTITY, "commit", "-q", "--no-edit"],
        ]
    commands = root / "worker-commands.json"
    commands.write_text(json.dumps({MERGE_RESOLUTION_HEADING: resolution}), encoding="utf-8")
    environment[RUN_ON_MARKER_ENV] = str(commands)

    manifest = root / "onevcs.checkouts"
    manifest.write_text(f"{publication}\n{execution}\n", encoding="utf-8")
    rules = root / "onevcs.rules.yml"
    rules.write_text(RULES, encoding="utf-8")
    applied = subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert applied.returncode == 0, f"repos-apply failed:\n{applied.stdout}\n{applied.stderr}"
    return World(root, origin, publication, environment, conflicted.base, conflicted.tip)


def _plan(world: World, name: str) -> Path:
    """A one-node lifecycle plan continuing the branch its base conflicts with."""
    plan = world.root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Continue a branch whose base moved to conflict with it"},
                "name": name,
                "tasks": [
                    {
                        "id": NODE_ID,
                        "title": "feat: continue the conflicted branch",
                        "repo": str(world.publication),
                        "execution_checkout": EXECUTION_ALIAS,
                        "branch": CONFLICTED,
                        "persona": "engineer",
                        "task": (
                            "## What\n\nContinue the work on this branch.\n\n"
                            "## Why\n\nWhat a conflicted continuation is handed is the "
                            "subject.\n\n"
                            "## Acceptance criteria\n\n- The service is published.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


class Journey(NamedTuple):
    worker: Worker
    world: World
    run: str
    launch: subprocess.CompletedProcess[str]
    journal: list[dict[str, object]]
    #: Every task a worker of this node was handed, in the order its dispatches ran.
    worker_tasks: list[str]


def _worker_tasks(world: World) -> list[str]:
    """The opening prompt of each worker dispatch, read off the stand-in's turn log.

    A dispatch's first worker turn carries the task; the supervisor's turns carry the
    transcript, and a later worker turn in the same conversation carries the supervisor's
    reply, so both are left out.
    """
    log = Path(world.environment[PROMPT_LOG_ENV])
    turns = [
        json.loads(line)
        for line in (log.read_text(encoding="utf-8") if log.exists() else "").splitlines()
        if line.strip()
    ]
    tasks = []
    for turn in turns:
        config = turn.get("config") or ""
        member = MEMBER_OF_CONFIG.search(config)
        if member is None or member.group(1) != "worker":
            continue
        if Path(config).name == JUDGE_CONFIG_NAME or "## Acceptance criteria" not in turn["prompt"]:
            continue
        tasks.append(turn["prompt"])
    return tasks


def _journey(root: Path, oneharness_bin: str, worker: Worker) -> Iterator[Journey]:
    """Launch the one-node plan through `just orchestrate` and read what it recorded."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    world = _world(root, oneharness_bin, worker)
    run = f"session-open-conflict-{worker}"
    launch = subprocess.run(
        ["just", "orchestrate", project_from_plan(_plan(world, run), run)],
        cwd=REPO_ROOT,
        env=world.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    try:
        journal_file = world.root / "runs" / run / "events.jsonl"
        assert journal_file.is_file(), (
            f"the launch recorded no journal at {journal_file}:\n{launch.stdout}\n{launch.stderr}"
        )
        # `onepipeline` writes this file; its schema is the engine's, and the cast says so.
        journal = [
            cast(dict[str, object], json.loads(line))
            for line in journal_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        yield Journey(worker, world, run, launch, journal, _worker_tasks(world))
    finally:
        subprocess.run(
            ["just", "stop", run],
            cwd=REPO_ROOT,
            env=world.environment,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )


@pytest.fixture(scope="module")
def concluded(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Journey]:
    """The run whose stand-in worker concludes the merge it is handed."""
    yield from _journey(
        tmp_path_factory.mktemp("open-conflict-concludes"), oneharness_bin, Worker.CONCLUDES
    )


@pytest.fixture(scope="module")
def unconcluded(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Journey]:
    """The run whose stand-in worker is handed the merge every attempt and never concludes it."""
    yield from _journey(
        tmp_path_factory.mktemp("open-conflict-never"), oneharness_bin, Worker.NEVER_CONCLUDES
    )


def _payload(event: dict[str, object]) -> dict[str, object]:
    # Every journal event's `payload` is a JSON object in the engine's record schema.
    return cast(dict[str, object], event.get("payload", {}))


def _index_of(journey: Journey, kind: str) -> list[int]:
    return [index for index, event in enumerate(journey.journal) if event.get("kind") == kind]


def _why(journey: Journey) -> str:
    kinds = [str(event.get("kind")) for event in journey.journal]
    return f"{journey.launch.stdout}\n{journey.launch.stderr}\njournal: {kinds}"


def _section(task: str, heading: str) -> str:
    """`heading`'s section of `task`: from its line to the next level-two heading."""
    at = task.find(f"{heading}\n")
    assert at >= 0, f"the task has no {heading}:\n{task}"
    end = task.find("\n## ", at + len(heading))
    return task[at:] if end < 0 else task[at:end]


def _blocking_surfaces(journey: Journey) -> list[tuple[int, dict[str, object]]]:
    return [
        (index, _payload(event))
        for index, event in enumerate(journey.journal)
        if event.get("kind") == "planner-surface-queued" and _payload(event).get("blocking")
    ]


@pytest.mark.parametrize("case", ["concluded", "unconcluded"])
def test_the_node_is_dispatched_into_the_conflict_rather_than_refused(
    case: str, request: pytest.FixtureRequest
) -> None:
    """The session opens with the merge in progress and the worker is handed it.

    Every dispatch carries the unmerged path, the base commit and the branch tip under
    `## Planner context`, and the engine's merge-resolution criterion inside `## Acceptance
    criteria`, after the task's own. A session refused at open dispatches nothing, so this
    fails on the old behaviour before it reads anything else.
    """
    # `case` names one of the two module fixtures above, each of which yields a `Journey`.
    journey = cast(Journey, request.getfixturevalue(case))
    opened = [_payload(journey.journal[index]) for index in _index_of(journey, "session-opened")]
    conflicts = [payload["conflict"] for payload in opened if payload.get("conflict")]
    assert conflicts, f"no session opened over the conflict: {opened}\n{_why(journey)}"
    assert conflicts[0] == {
        "paths": [CONFLICTED_PATH],
        "base_commit": journey.world.base,
        "branch_tip": journey.world.tip,
    }, conflicts

    expected = 1 if journey.worker is Worker.CONCLUDES else ATTEMPTS
    dispatched = _index_of(journey, "node-dispatched")
    assert len(dispatched) == expected and len(journey.worker_tasks) == expected, (
        f"the node was dispatched {len(dispatched)} time(s) and its worker handed "
        f"{len(journey.worker_tasks)} task(s), not {expected}\n{_why(journey)}"
    )
    for task in journey.worker_tasks:
        context = _section(task, PLANNER_CONTEXT_HEADING)
        for names in (
            OPENED_CONFLICTED,
            f"`{CONFLICTED_PATH}`",
            journey.world.base,
            journey.world.tip,
            CONFLICTED,
            BASE_SUBJECT,
        ):
            assert names in context, f"the planner context does not name {names!r}:\n{context}"
        criteria = _section(task, "## Acceptance criteria")
        resolution = criteria.find(f"{MERGE_RESOLUTION_HEADING}\n{MERGE_RESOLUTION_PREAMBLE}")
        assert resolution >= 0, f"no merge-resolution criterion in the criteria:\n{criteria}"
        assert 0 <= criteria.find("- The service is published.") < resolution, (
            f"the merge-resolution criterion is not after the task's own:\n{criteria}"
        )
        assert journey.world.base in criteria[resolution:], criteria

    # Nothing asked a person to merge before the worker was handed the merge.
    first_dispatch = dispatched[0]
    early = [payload for index, payload in _blocking_surfaces(journey) if index < first_dispatch]
    assert not early, f"a decision was raised before any dispatch: {early}"


def test_a_worker_that_concludes_the_merge_lands_the_branch(concluded: Journey) -> None:
    """The merge the worker commits is what the node publishes, and nothing asks anyone."""
    journey = concluded
    settled = [_payload(journey.journal[index]) for index in _index_of(journey, "node-settled")]
    assert [(record.get("status"), record.get("outcome")) for record in settled] == [
        ("done", "merged")
    ], f"the node settled {settled}\n{_why(journey)}"
    origin = journey.world.origin
    # A `local-direct` publication squashes, so the branch tip is no ancestor of the base;
    # what landed is one commit on top of the moved base, carrying the worker's resolution.
    _git("merge-base", "--is-ancestor", journey.world.base, BASE, cwd=origin)
    landed = _git("show", f"{BASE}:{CONFLICTED_PATH}", cwd=origin)
    assert landed == RESOLVED.strip(), f"the base carries {landed!r}, not the resolution"
    assert not _blocking_surfaces(journey), (
        f"a conflict the worker concluded raised a decision\n{_why(journey)}"
    )


def test_a_worker_that_never_concludes_spends_the_budget_and_only_then_is_asked_about(
    unconcluded: Journey,
) -> None:
    """The budget is spent on the worker, and only then is the manager asked.

    No blocking `session-conflict` finding is journalled until the final attempt's
    publication has been refused, which the session that attempt worked in closing after
    it marks. The one finding is journalled in the same reconcile pass as the node's
    `failed`/`sync-conflict` settlement, with no other event of the node's between the
    two: the engine queues it a moment before the settlement rather than after, and the
    property that matters is that nobody is asked while the worker still has budget. It
    names how many dispatches the worker was given, and the run's channel holds it for
    the manager once the node has settled.
    """
    journey = unconcluded
    settled_at = _index_of(journey, "node-settled")
    settled = [_payload(journey.journal[index]) for index in settled_at]
    assert [(record.get("status"), record.get("outcome")) for record in settled] == [
        ("failed", "sync-conflict")
    ], f"the node settled {settled}\n{_why(journey)}"

    blocking = _blocking_surfaces(journey)
    assert len(blocking) == 1, f"one decision, not {blocking}\n{_why(journey)}"
    raised_at, finding = blocking[0]
    final_refusal = max(_index_of(journey, "session-closed"))
    assert raised_at > final_refusal > max(_index_of(journey, "node-dispatched")), (
        f"the decision was journalled at {raised_at}, before the final attempt's publication "
        f"was refused and its session closed at {final_refusal}\n{_why(journey)}"
    )
    between = journey.journal[min(raised_at, settled_at[0]) + 1 : max(raised_at, settled_at[0])]
    # Every journal event's `labels` is a JSON object of strings in the engine's schema.
    others = [
        event.get("kind")
        for event in between
        if cast(dict[str, str], event.get("labels", {})).get("node") == NODE_ID
    ]
    assert not others, (
        f"the decision and the settlement are not one pass: {others} came between them"
    )
    message = str(finding.get("message", ""))
    for names in (
        DISPATCHED_INTO.format(count=ATTEMPTS),
        "did not converge",
        f"`{CONFLICTED_PATH}`",
        CONFLICTED,
        f"`retry` of '{NODE_ID}'",
    ):
        assert names in message, f"the decision does not name {names!r}: {message}"
    assert "by hand" not in message, f"the decision asks for a hand merge: {message}"

    queue = subprocess.run(
        ["uv", "run", "onepipeline", "channel", "queue", journey.run],
        cwd=REPO_ROOT,
        env=journey.world.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert queue.returncode == 0, queue.stdout + queue.stderr
    surfaces = json.loads(queue.stdout)["surfaces"]
    keys = [str(surface.get("correlation")) for surface in surfaces if surface.get("blocking")]
    held = [key for key in keys if FINDING_KEY in key and NODE_ID in key]
    assert len(held) == 1, (
        f"the run's channel holds {len(held)} blocking {FINDING_KEY} surfaces for {NODE_ID}, "
        f"not one: {surfaces}"
    )
