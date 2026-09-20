"""A follow-up drafted from inside a real launch, and everything the launch stamped on it.

Every party that may draft a follow-up runs somewhere other than the launching checkout — a
dispatched worker in a session worktree, the monitor in a graph member's scratch — and
reaches the drafting seam only by inheritance: `scripts/onepipeline.sh` sources
`scripts/follow-up-env.sh`, and the variables it exports travel through `onepipeline`,
`oneagentgraph` and `oneharness`, none of which reports what it passed on. So this launches
for real — the real `just orchestrate` recipe, the real driver, this host's real
`graphs/dag-scope.yaml` with its monitor attached, and a real `onevcs` session for a
lifecycle node — and reads what each party was given out of its own turn.

Then the dispatched node *uses* it. `tests/e2e/fake_backend.py` stands in for the paid model
alone, and its worker turn runs `$ORCHESTRATOR_FOLLOW_UP_DRAFT` in the session worktree the
engine cut, with the environment that turn inherited — which is the only way to prove what a
draft written inside a dispatch is stamped with: the run, the dispatch's scratch, session,
working directory and git state, and the node, resolved from the run's own dispatch registry
by walking up from the drafting process to the dispatch the engine recorded.

The root is stated to the launch rather than left to resolve to this checkout's
`.follow-ups/`, for the reason `tests/plan_tooling/test_plan_root_env.py`'s neighbours state
theirs: a journey that wrote into the tree it runs in would leave records behind for every
other reader of that store. The helper still resolves and validates it through the store,
and `tests/plan_tooling/test_follow_up_drafts_e2e.py` holds the value it resolves on its own.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple, cast

import follow_up_variables
import pytest
from fake_backend import (
    ENVIRONMENT_KEYS_ENV,
    MEMBER_OF_CONFIG,
    PROMPT_LOG_ENV,
    RUN_ON_MARKER_ENV,
    RecordedTurn,
)
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from onetaskgraph_sdk import TaskDetail
from planner_channel import just
from project_fixtures import helper, project_from_plan
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import pooling, seeded
from waits import deadline

from orchestrator import follow_up_drafts as drafts
from orchestrator import plan_store
from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs, so it is scheduled
#: with every other journey that does.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The stand-in for the paid model, and the provider beneath a single-sided member.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: Everything an enclosing dispatch would otherwise decide for this launch. This suite runs
#: inside a launch of its own, so a journey that kept these would read the enclosing run's
#: seam back and call it this launch's doing.
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ONEVCS_SESSION",
    "ORCHESTRATOR_ASK_MANAGER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    *follow_up_variables.all_names(),
)
LAUNCHING_SESSION = "e2e-follow-up-draft-launch"

#: The node the plan dispatches, and the marker in its task that makes its turn draft.
NODE = "noticer"
MARKER = "DRAFT-A-FOLLOW-UP-FROM-THIS-DISPATCH"

TITLE = "The sweep trailer names a family it never examined"
REPOSITORY = "github.com/nickderobertis/ai-orchestrator"
DRAFTED_PATH = "scripts/sweep.sh"
BODY = (
    "## What happened\nThe sweep reported 0 B reclaimed beside an unexamined family.\n\n"
    "## Where\n`scripts/sweep.sh`, the trailer it writes.\n\n"
    "## Why it is out of scope\nThis node was dispatched to notice it, not to fix it.\n\n"
    "## Evidence\n```\n0 B reclaimed\n```\n"
)

#: The monitor member of `graphs/dag-scope.yaml`, whose turns are read beside the worker's.
MONITOR = "monitor"
WORKER = "worker"

#: How long a lifecycle launch has to dispatch its node and the monitor to take its turn.
LAUNCH_SECONDS = 300
#: How long a stopped run's processes may take to leave the worktree its session holds, and
#: the refusal `onevcs session close` gives while they have not.
TEARDOWN_SECONDS = 60
STILL_WORKING = "still working inside its run root"


class Launched(NamedTuple):
    """One launch, in what the journeys below read off it."""

    run: str
    root: Path
    runs: Path
    scratch: Path
    worker: list[RecordedTurn]
    monitor: list[RecordedTurn]
    draft: Path
    text: str
    environment: dict[str, str]


def _environment(
    tmp_path: Path, oneharness_bin: str, turns: Path, instruction: Path
) -> dict[str, str]:
    environment = dict(os.environ)
    for name in INHERITED:
        environment.pop(name, None)
    root, plugin, command = follow_up_variables.all_names()
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment[root] = str(tmp_path / "follow-ups")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment[PROMPT_LOG_ENV] = str(turns)
    environment[ENVIRONMENT_KEYS_ENV] = ",".join(
        [
            root,
            plugin,
            command,
            drafts.RUN_ID_ENV,
            drafts.NODE_SCRATCH_ENV,
            drafts.SESSION_ENV,
        ]
    )
    # The stand-in for the paid model runs the real drafting command in its own worktree,
    # exactly as a worker told to draft a follow-up does — the one action of the turn
    # anything downstream of it can observe.
    # llmlint: ignore[tests_mirror_real_usage] see the note above this line
    environment[RUN_ON_MARKER_ENV] = str(instruction)
    return environment


def _drafting_turn(instruction: Path) -> None:
    """The commands the dispatched worker runs: pipe a body into the exported command."""
    command = follow_up_variables.command_name()
    script = (
        f"cat <<'DRAFT' | \"${command}\" --title {shlex.quote(TITLE)} "
        f"--repository {REPOSITORY} --path {DRAFTED_PATH}\n{BODY}DRAFT\n"
    )
    instruction.write_text(json.dumps({MARKER: [["bash", "-c", script]]}), encoding="utf-8")


def _plan(tmp_path: Path, run: str, identity_publication: Path, execution: str) -> Path:
    written = tmp_path / f"{run}.plan.json"
    written.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "draft a follow-up from inside a dispatch"},
                "name": run,
                "tasks": [
                    {
                        "id": NODE,
                        "persona": "engineer",
                        "task": f"## What\n{MARKER}: draft the follow-up you noticed.\n\n"
                        "## Why\nWhat a draft is stamped with is the subject.\n\n"
                        "## Acceptance criteria\n- The follow-up is drafted.",
                        "repo": str(identity_publication),
                        "execution_checkout": execution,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return written


def _turns(turns: Path, member: str) -> list[RecordedTurn]:
    """Every recorded turn of ``member``, dropping a half-written last line."""
    if not turns.is_file():
        return []
    found: list[RecordedTurn] = []
    for line in turns.read_text(encoding="utf-8").splitlines():
        try:
            # Cast rather than validated: `tests/e2e/fake_backend.py` writes this record from
            # its own `RecordedTurn` declaration, so re-validating it would assert about the
            # recorder instead of about what the launch handed the turn.
            turn = cast(RecordedTurn, json.loads(line))
        except json.JSONDecodeError:
            continue
        named = MEMBER_OF_CONFIG.search(turn["config"] or "")
        if named is not None and named.group(1) == member:
            found.append(turn)
    return found


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This directory is
# already the Nx project edge this repository keeps for this real-launch journey over what
# a launch exports into its dispatches — `tests/ask_seam/launch/` beside it launches every
# launch shape the same way behind an edge of its own — and its target is keyed on
# `askSeamFollowUpDraftsLaunch`, which names file by file what this launch was measured
# reading, so an edit outside that key does not pay for it.
# The fixture is module-scoped and spends one launch, which ran in about three seconds
# here with the stand-in model; `LAUNCH_SECONDS` bounds a wedged launch rather than
# describing an ordinary one.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] Same site, same reason:
# this is already a project of its own, selected by directory rather than by a marker.
@pytest.fixture(scope="module")
def launched(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Launched:
    """Launch `just orchestrate --detach` on a lifecycle node that drafts a follow-up."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("follow-up-draft-launch")
    run = f"follow-up-draft-launch-{os.getpid()}"
    turns, instruction = tmp_path / "turns.jsonl", tmp_path / "run-on-marker.json"
    _drafting_turn(instruction)
    environment = _environment(tmp_path, oneharness_bin, turns, instruction)
    # Pooling off for this identity: the journey below proves the draft outlives the
    # dispatch worktree's *removal*, and the tracked workspaces file the recipe would
    # otherwise install pools one slot, whose close returns the directory rather than
    # removing it.
    identity = seeded(tmp_path, workspaces=pooling(0))
    environment["ONEVCS_HOME"] = str(identity.home)
    environment.update(identity.environment)
    plan = _plan(tmp_path, run, identity.publication, identity.execution.name)
    root = tmp_path / "follow-ups"
    written = root / "tasks" / run / drafts.DRAFTS

    launch = just("orchestrate", project_from_plan(plan), "--detach", environment=environment)
    try:
        assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}{launch.stderr}"
        limit = deadline(LAUNCH_SECONDS)
        while time.monotonic() < limit:
            drafted = sorted(written.glob("*.md")) if written.is_dir() else []
            if drafted and _turns(turns, MONITOR):
                break
            time.sleep(0.5)
        else:
            raise AssertionError(
                f"within {LAUNCH_SECONDS}s the launch drafted {sorted(written.glob('*.md'))} "
                f"and the monitor took {len(_turns(turns, MONITOR))} turns; the worker's "
                f"recorded turns were {_turns(turns, WORKER)}"
            )
        # The shortest name is the first draft written. A supervisor may send the stand-in
        # back for another turn, which runs the same command again; the second draft lands
        # beside the first with a `-2` suffix rather than over it, which is the collision
        # rule working, and the first is the one this reads.
        draft = min(drafted, key=lambda path: len(path.name))
        return Launched(
            run=run,
            root=root,
            runs=tmp_path / "runs",
            scratch=tmp_path,
            worker=_turns(turns, WORKER),
            monitor=_turns(turns, MONITOR),
            draft=draft,
            text=draft.read_text(encoding="utf-8"),
            environment=environment,
        )
    finally:
        just("stop", run, environment=environment, seconds=60)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def _given(turns: list[RecordedTurn], name: str, whose: str) -> str:
    """The one value every recorded turn of a party was given for ``name``."""
    assert turns, f"the launch recorded no turn of the {whose}, so nothing is measured"
    carried = {turn["environment"].get(name) for turn in turns}
    assert len(carried) == 1, f"the {whose} was given more than one {name}: {carried}"
    value = carried.pop()
    assert value is not None, f"{name} never reached the {whose}"
    return value


@pytest.mark.parametrize("whose", [WORKER, MONITOR], ids=["dispatched-node", "dag-scope-member"])
def test_a_launch_hands_the_drafting_seam_to_a_dispatch_and_to_a_dag_scope_member(
    launched: Launched, whose: str
) -> None:
    """Both parties get the root the launch resolved, its plugin, and a command they can run."""
    root, plugin, command = follow_up_variables.all_names()
    turns = launched.worker if whose == WORKER else launched.monitor

    assert _given(turns, root, whose) == str(launched.root)
    assert Path(_given(turns, root, whose)).is_absolute()
    assert _given(turns, plugin, whose) == plan_store.WRITABLE_PLUGIN
    given = _given(turns, command, whose)
    assert given == str(REPO_ROOT / "scripts" / "follow-up-draft.sh")
    assert os.access(given, os.X_OK), f"the {whose} was given {given}, which is not runnable"


def test_a_draft_from_inside_the_dispatch_lands_in_the_record_shape_under_that_root(
    launched: Launched,
) -> None:
    """The file is where the contract says, holds exactly the shape it says, beside its project."""
    draft = drafts.parse(launched.text)

    assert launched.draft == launched.root / "tasks" / launched.run / "drafts" / f"{draft.stem}.md"
    assert drafts.render(draft) == launched.text, "the draft is not in the shape the module renders"
    assert (draft.title, draft.repository, draft.paths, draft.body) == (
        TITLE,
        REPOSITORY,
        (DRAFTED_PATH,),
        BODY.strip(),
    )
    project = launched.root / "projects" / f"{launched.run}.md"
    assert project.read_text(encoding="utf-8") == drafts.render_project(launched.run)


def test_the_draft_is_stamped_with_the_run_the_dispatch_and_the_node_it_came_from(
    launched: Launched,
) -> None:
    """Nothing the worker supplied says where the draft came from; the launch stamped all of it."""
    draft = drafts.parse(launched.text)
    session = _given(launched.worker, drafts.SESSION_ENV, WORKER)

    assert draft.run == launched.run == _given(launched.worker, drafts.RUN_ID_ENV, WORKER)
    registry = sorted(path.name for path in (launched.runs / launched.run / "dispatches").iterdir())
    assert draft.author == drafts.Author("node", NODE, None), (
        f"the draft names {draft.author}, where the dispatch it was written in is node {NODE}; "
        f"the run's dispatch registry held {registry}"
    )
    assert draft.dispatch.scratch == _given(launched.worker, drafts.NODE_SCRATCH_ENV, WORKER)
    assert draft.dispatch.session == session and re.fullmatch(r"s-[0-9a-f]{12}", session)
    worktree = {turn["cwd"] for turn in launched.worker}
    assert draft.dispatch.cwd in worktree, (draft.dispatch.cwd, worktree)
    assert Path(draft.dispatch.cwd).is_relative_to(launched.scratch), draft.dispatch.cwd
    assert draft.dispatch.branch == f"onevcs/{session}"
    assert draft.dispatch.head is not None and re.fullmatch(r"[0-9a-f]{40}", draft.dispatch.head)
    assert draft.transcript == f"onepipeline transcript {launched.run} {NODE}"


def _store_item(launched: Launched) -> dict[str, object]:
    root, plugin, _ = follow_up_variables.all_names()
    draft = drafts.parse(launched.text)
    stem = launched.draft.stem
    shown = subprocess.run(  # noqa: S603 - the installed plan-store CLI, reading a draft back
        [str(ONETASKGRAPH_BIN), "task", "show", f"drafts:{draft.run}/drafts/{stem}", "--json"],
        cwd=launched.scratch,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(Path.home()),
            root: _given(launched.worker, root, WORKER),
            plugin: _given(launched.worker, plugin, WORKER),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    answer = TaskDetail.model_validate_json(shown.stdout)
    assert len(answer.items) == 1, answer
    return answer.items[0].item.model_dump(mode="json")


def test_the_installed_store_reads_the_draft_back_with_every_value_and_type_intact(
    launched: Launched,
) -> None:
    item = _store_item(launched)

    metadata = item["metadata"]
    assert isinstance(metadata, dict)
    assert metadata[drafts.DRAFT_KEY] == drafts.record(drafts.parse(launched.text))
    assert drafts.from_store_item(item) == drafts.parse(launched.text)


def test_the_draft_survives_the_dispatch_worktree_being_removed(launched: Launched) -> None:
    """A draft is written under the launch's root, so reaping the worktree loses nothing."""
    worktree = Path(drafts.parse(launched.text).dispatch.cwd)
    assert worktree.is_relative_to(launched.scratch), (
        f"{worktree} is not under this journey's own directory, so it is not this journey's "
        "to remove"
    )
    session = drafts.parse(launched.text).dispatch.session
    assert session is not None
    if worktree.exists():
        # The run was stopped; releasing its session is what reaps the worktree it was cut.
        # A process the stopped run started can still be exiting inside that worktree when
        # the close runs — two were, re-parented to init, on one run of this journey — and
        # `onevcs` refuses to take the directory out from under a live process, saying to
        # let it finish. So that one refusal is retried until the teardown deadline, and
        # any other fails at once.
        limit = deadline(TEARDOWN_SECONDS)
        while True:
            closed = subprocess.run(  # noqa: S603 - the lifecycle verb that releases a session
                ["onevcs", "session", "close", session],  # noqa: S607 - the onevcs the launch ran
                cwd=REPO_ROOT,
                env=launched.environment,
                text=True,
                capture_output=True,
                check=False,
            )
            if (
                closed.returncode == 0
                or STILL_WORKING not in closed.stderr
                or time.monotonic() >= limit
            ):
                break
            time.sleep(0.5)
        assert closed.returncode == 0, closed.stdout + closed.stderr
    assert not worktree.exists(), f"releasing session {session} left its worktree {worktree}"

    assert launched.draft.read_text(encoding="utf-8") == launched.text
    assert drafts.from_store_item(_store_item(launched)) == drafts.parse(launched.text)
