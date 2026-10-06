"""`just plan` accepts a plan whose tasks a planner rendered from the `plan-task` template.

A planner here never writes a task's body. It pipes the pinned engine's `onepipeline
template resolve plan-task --json` into the pinned plan store's `onetaskgraph task create
--template-loader -`, answering the template's variables, and changes a task only by piping
the same `resolve` into `onetaskgraph task render`. So this journey has the doubled
planner do exactly that — the model's decision of which commands to run is the only thing
stood in for; the commands are the real ones, run where the dispatch runs, in the
environment the launch hands it — and then lets the rest of the flow run for real: the
review, `just check-plan`, the design-document launch and the copy onto a destination.

What is read back is what the template promises and what a board copy costs: each task
records `onepipeline:plan-task` as its provenance and keeps its answers beside it in the
`authoring` source, and the body a board would carry is the rendering plus a metadata slot
holding digests and no answer — the answers are never written into an issue twice.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import plan_root_variable
import pytest
from fake_backend import AUTHOR_PLAN_ENV, PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import PLANNING_FLOW_ORIGIN
from test_plan_flow_e2e import (
    DESIGN_RUN_SUFFIX,
    DESIGN_TASK_MARKER,
    DESTINATION,
    Stored,
    _environment,
    _just,
    _renders_the_document,
    _writes_the_budgets,
)

from orchestrator import plan_check, plan_store, task_body
from orchestrator.root import REPO_ROOT

#: The source a planning launch authors into, whose root the launch exports to the
#: dispatch and `tests/conftest.py` isolates for this process, under the name
#: `scripts/plan-root-env.sh` composes and `tests/plan_root_variable.py` reads.
AUTHORING = "authoring"
AUTHORING_ROOT = plan_root_variable.name()

#: The pinned engine, which states the template as a loader document.
ENGINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"

#: The provenance key the plan store records and the reference the engine's loader
#: document names for a registered task template.
PROVENANCE = "onetaskgraph.template"
REFERENCE = "onepipeline:plan-task"

#: A line only the planner's brief carries, which is how the doubled turn knows it is the
#: planner's and runs the planner's commands.
PLANNER_MARKER = "Render every task of this plan from the plan-task template."

RUN = "plan-task-template-e2e"
DESIGN_RUN = f"{RUN}{DESIGN_RUN_SUFFIX}"

#: The two tasks' answers. Their criteria answer every demand `just check-plan` reads,
#: because the flow checks this plan before it writes a document about it.
FIRST: dict[str, object] = {
    "what": "Add the paginated listing and the test that drives it.",
    "why": "An operator cannot see past the first screen of nodes.",
    "acceptance_criteria": [
        "The route accepts a valid request and rejects an invalid one.",
        "A request-level test drives the route end to end and covers both paths.",
        "Every claim the dispatch makes about the finished work is true of the tree as it "
        "finally stands.",
    ],
    "additional_info": "The listing's page size is the view's to choose.",
}
SECOND: dict[str, object] = {
    "what": "Follow the stated cursor from the browser view.",
    "why": "The shape is worth nothing until something reads it.",
    "acceptance_criteria": [
        "The view pages on the stated cursor and reports a rejected one.",
        "A browser-level test drives both of those paths end to end.",
        "Every claim the dispatch makes about the finished work is true of the tree as it "
        "finally stands.",
    ],
}
#: The answer the regenerate journey changes, and what it changes it to.
CHANGED = ("why", "An operator loses every node past the first screen of the listing.")


class Authored(NamedTuple):
    """One planning flow whose planner rendered its tasks, and what it left."""

    launch: subprocess.CompletedProcess[str]
    stored: Stored
    destination: Path
    #: Each task's node id and its qualified id in the `authoring` source.
    tasks: dict[str, str]
    #: The digest the pinned engine states for `plan-task` from this host's root.
    digest: str
    #: The runs root both of the flow's launches recorded themselves under.
    runs: Path


def _loader() -> dict[str, object]:
    resolved = subprocess.run(
        [str(ENGINE), "template", "resolve", "plan-task", "--json"],
        env={**os.environ, "ONEPIPELINE_TEMPLATE_ROOT": str(REPO_ROOT / "templates")},
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    document: dict[str, object] = json.loads(resolved.stdout)
    return document


def _planner_script(tmp_path: Path, stored: Stored) -> Path:
    """The commands the doubled planner runs: a project record, then two rendered tasks.

    The tasks are created the way the persona tells a planner to — the engine's `resolve`
    piped into the store's `task create` — by the bare command names the dispatch's own
    search path resolves, which is this checkout's locked installs.
    """
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "first.json").write_text(json.dumps(FIRST), encoding="utf-8")
    (answers / "second.json").write_text(json.dumps(SECOND), encoding="utf-8")
    project = (
        f'---\ntitle: "{stored.project}"\nstatus: "todo"\nmetadata:\n'
        '  "onepipeline.schema_version": 3\n'
        '  "onepipeline.goal": {"text": "Decide the cursor\'s shape"}\n---\n\n'
        f"Execution plan {stored.project}.\n"
    )
    script = tmp_path / "planner.sh"
    script.write_text(
        f"""set -euo pipefail
root="${AUTHORING_ROOT}"
# A supervisor sends a turn back for more, and this is run on every turn: the plan is
# authored once, as a planner authors it once.
[ -e "$root/projects/{stored.project}.md" ] && exit 0
mkdir -p "$root/projects"
cat >"$root/projects/{stored.project}.md" <<'PROJECT'
{project}PROJECT
first=$(onepipeline template resolve plan-task --json \\
  | onetaskgraph task create {AUTHORING} --template-loader - --no-interactive \\
      --project {stored.project} --title '{stored.task_title}' \\
      --answers {answers / "first.json"} --repository {stored.repository} \\
      --metadata 'onepipeline.id="decide-the-cursor"' \\
      --metadata 'onepipeline.persona="engineer"')
onepipeline template resolve plan-task --json \\
  | onetaskgraph task create {AUTHORING} --template-loader - --no-interactive \\
      --project {stored.project} --title '{stored.second_task_title}' \\
      --answers {answers / "second.json"} --repository {stored.second_repository} \\
      --depends-on "$first" \\
      --metadata 'onepipeline.id="read-the-cursor"' \\
      --metadata 'onepipeline.persona="engineer"'
""",
        encoding="utf-8",
    )
    return script


# llmlint: ignore[expensive_tests_stay_behind_their_own_edge] `plan-tooling:test` is the edge every whole planning flow here already sits behind (`tests/plan_tooling/AGENTS.md`), and this journey drives `just plan`, which reads the scripts and the package that key names; a project of its own would be keyed on the same files.  # noqa: E501
@pytest.fixture(scope="module")
def authored(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Authored:
    """Drive one whole `just plan` flow whose planner renders its tasks from the template."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("plan-task-template")
    unique = f"test-{os.getpid()}-plan-task-template"
    stored = Stored(
        project=unique,
        qualified=f"{AUTHORING}:{unique}",
        task_title="feat: page the node listing",
        second_task_title="feat: follow the cursor from the view",
        # The one repository this flow's scratch identity registers, for both tasks: under
        # `require_rendered` the engine resolves each task's template through the repository
        # it names, and a repository this host has no checkout of cannot be asked.
        repository=PLANNING_FLOW_ORIGIN,
        second_repository=PLANNING_FLOW_ORIGIN,
        document=f"{unique}-document",
        document_qualified=f"{AUTHORING}:{unique}-document",
        document_path=Path(os.environ[AUTHORING_ROOT]) / "documents" / f"{unique}-document.md",
    )
    destination = tmp_path / "board"
    destination.mkdir()
    environment = _environment(tmp_path, stored, destination=DESTINATION, declared_at=destination)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")
    # This flow's tasks are renderings, so it runs without the suite's session-wide opt-out
    # for hand-written fixture plans: every launch and check below takes the
    # `require_rendered` answer the recipes name, which is what the journey reads back.
    environment.pop(plan_check.REQUIRE_RENDERED_ENV, None)
    # The planner authors by running commands rather than by having files written for it.
    environment.pop(AUTHOR_PLAN_ENV, None)
    keyed = tmp_path / "planner-commands.json"
    keyed.write_text(
        json.dumps(
            {
                # Each turn is the paid model's, scripted as this node's doubled planner and
                # drafter; what each runs is a pinned, real verb (`resolve` | `task create`,
                # then `resolve` | `document create`), judged by the real `check-plan` and flow
                # after it.
                # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] Paid turn only.
                PLANNER_MARKER: [
                    ["bash", str(_planner_script(tmp_path, stored))],
                    _writes_the_budgets(tmp_path / "budgets", stored, AUTHORING),
                ],
                # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] Paid turn only.
                DESIGN_TASK_MARKER: [_renders_the_document(tmp_path / "design", stored, AUTHORING)],
            }
        ),
        encoding="utf-8",
    )
    environment[RUN_ON_MARKER_ENV] = str(keyed)
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(
        "## What\nDecide the cursor's shape.\n\n"
        f"Plan project: {stored.qualified}\n\n{PLANNER_MARKER}\n\n"
        "## Why\nThe view cannot deep-link until it is settled.\n\n"
        "## Acceptance criteria\n- The cursor's shape and its type are stated.\n",
        encoding="utf-8",
    )
    try:
        launch = _just(
            "plan", str(brief), "--name", RUN, "--to", DESTINATION, environment=environment
        )
        tasks = {
            task.node_id: task.qualified_id for task in plan_store.read_tasks(stored.qualified)
        }
        return Authored(
            launch=launch,
            stored=stored,
            destination=destination,
            tasks=tasks,
            digest=str(_loader()["digest"]),
            runs=Path(environment["ONEPIPELINE_RUNS_DIR"]),
        )
    finally:
        for ended in (RUN, DESIGN_RUN):
            _just("stop", ended, environment=environment, seconds=60)


def _answers(task: str) -> dict[str, object]:
    shown = subprocess.run(
        [str(ONETASKGRAPH_BIN), "task", "answers", task, "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    answers: dict[str, object] = json.loads(shown.stdout)
    return answers


def _task(project: str, qualified: str) -> plan_store.StoreTask:
    """One task of ``project``, read through the store: a created task's id is the source's."""
    (task,) = [one for one in plan_store.read_tasks(project) if one.qualified_id == qualified]
    return task


@pytest.mark.xdist_group("plan-task-template")
def test_the_flow_accepts_a_plan_whose_planner_rendered_every_task(authored: Authored) -> None:
    """Review, `check-plan`, the document launch and the copy all take the rendered plan."""
    reported = authored.launch.stdout + authored.launch.stderr
    assert authored.launch.returncode == 0, f"the flow refused the plan:\n{reported}"
    assert set(authored.tasks) == {"decide-the-cursor", "read-the-cursor"}, authored.tasks
    project = authored.stored.project
    assert (authored.destination / "projects" / f"{project}.md").is_file()
    # The destination names each copied task as the store does, so what makes a file one
    # of this plan's tasks is the project its record is filed under.
    filed = [
        one
        for one in (authored.destination / "tasks").rglob("*.md")
        if re.search(rf"^project: \"?{re.escape(project)}\"?$", one.read_text("utf-8"), re.M)
    ]
    assert len(filed) == 2, f"the destination holds {filed} of this plan's tasks"


@pytest.mark.xdist_group("plan-task-template")
def test_the_planning_launch_alone_runs_without_require_rendered(authored: Authored) -> None:
    """The planner's one node is the manager's hand-written brief; the document's is rendered.

    Read off each launch's own record, because the flag is named on the flow's behalf —
    `false` by `scripts/plan.sh` for the brief, `true` by `scripts/onepipeline.sh` for every
    other launch — and a plan check the flow ran in between took the same `true`.
    """
    assert authored.launch.returncode == 0, authored.launch.stdout + authored.launch.stderr
    for run, expected in ((RUN, False), (DESIGN_RUN, True)):
        record = json.loads((authored.runs / run / "launch.json").read_text("utf-8"))
        assert record.get("require_rendered", False) is expected, (run, record)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] An existing journey of `plan-tooling`, updated to the planning flow's new handover; where it runs is that project's split, which `tests/plan_tooling/AGENTS.md` states and this change leaves as it was.  # noqa: E501
@pytest.mark.xdist_group("plan-task-template")
def test_each_task_records_the_engines_template_and_keeps_its_answers_beside_it(
    authored: Authored,
) -> None:
    for node, answers in (("decide-the-cursor", FIRST), ("read-the-cursor", SECOND)):
        task = _task(authored.stored.qualified, authored.tasks[node])
        provenance = task.metadata.get(PROVENANCE)
        assert isinstance(provenance, dict), task.metadata
        assert provenance["template"] == REFERENCE, provenance
        assert provenance["digest"] == authored.digest, (
            f"{node} was rendered with {provenance['digest']}, not the {authored.digest} the "
            "pinned engine states for this host's plan-task"
        )
        stored = _answers(authored.tasks[node])
        assert stored == {"additional_info": "", "spikes": [], **answers}, stored
        assert task.content is not None and task.content.startswith(f"## What\n\n{answers['what']}")
    second = _task(authored.stored.qualified, authored.tasks["read-the-cursor"])
    assert second.deps == ("decide-the-cursor",), second.deps


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


@pytest.mark.xdist_group("plan-task-template")
def test_the_board_body_check_plan_measures_is_the_rendering_and_a_slot_holding_no_answer(
    authored: Authored,
) -> None:
    """The answers live in the authoring file alone, so a board issue never carries them twice.

    The body is the one `just check-plan` measures a task by: the stored record's content
    and metadata as the `plans` board's plugin composes an issue from them.
    """
    for node, answers in (("decide-the-cursor", FIRST), ("read-the-cursor", SECOND)):
        task = _task(authored.stored.qualified, authored.tasks[node])
        # llmlint: ignore[tests_mirror_real_usage] The only destination that composes this body is the live `plans` board, which no journey here writes to; `task_body.compose` is the composer `just check-plan` measures with, held byte for byte to the `github-projects` plugin's own by `tests/test_task_body.py`, and it is fed the record this flow really stored.  # noqa: E501
        body = task_body.compose(task.content, task.metadata)
        assert task.content is not None
        assert body.startswith(task.content + task_body.METADATA_SEPARATOR), node
        slot = body.removeprefix(task.content + task_body.METADATA_SEPARATOR)
        assert slot.startswith(task_body.METADATA_OPEN), slot[:80]
        criteria = answers["acceptance_criteria"]
        assert isinstance(criteria, list)
        texts = [answers["what"], answers["why"], *criteria]
        if answers.get("additional_info"):
            texts.append(answers["additional_info"])
        carried = [text for text in texts if str(text) in slot]
        assert not carried, f"{node}'s metadata slot carries answer text: {carried}"


class Regenerated(NamedTuple):
    before: plan_store.StoreTask
    after: plan_store.StoreTask
    rendered: subprocess.CompletedProcess[str]


@pytest.fixture(scope="module")
def regenerated(authored: Authored) -> Regenerated:
    """Change one answer the way the persona says a task is changed, and nothing else."""
    qualified = authored.tasks["read-the-cursor"]
    before = _task(authored.stored.qualified, qualified)
    name, value = CHANGED
    rendered = subprocess.run(
        [
            "bash",
            "-c",
            'onepipeline template resolve plan-task --json | onetaskgraph task render "$1" '
            '--template-loader - --var "$2" --no-interactive',
            "regenerate",
            qualified,
            f"{name}={value}",
        ],
        env={
            **os.environ,
            "PATH": f"{REPO_ROOT / '.venv' / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "ONEPIPELINE_TEMPLATE_ROOT": str(REPO_ROOT / "templates"),
        },
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return Regenerated(before, _task(authored.stored.qualified, qualified), rendered)


@pytest.mark.xdist_group("plan-task-template")
def test_a_regenerated_task_shows_its_changed_answer_and_keeps_everything_else(
    authored: Authored, regenerated: Regenerated
) -> None:
    assert regenerated.rendered.returncode == 0, regenerated.rendered.stderr
    before, after = regenerated.before, regenerated.after
    name, value = CHANGED
    assert after.content is not None and f"## Why\n\n{value}\n" in after.content
    assert str(SECOND["why"]) not in after.content
    assert (after.qualified_id, after.node_id, after.deps) == (
        before.qualified_id,
        before.node_id,
        before.deps,
    )
    kept = {key: val for key, val in before.metadata.items() if key != PROVENANCE}
    assert {key: val for key, val in after.metadata.items() if key != PROVENANCE} == kept
    provenance = after.metadata[PROVENANCE]
    assert isinstance(provenance, dict) and provenance["template"] == REFERENCE, provenance
    assert _answers(after.qualified_id)[name] == value
