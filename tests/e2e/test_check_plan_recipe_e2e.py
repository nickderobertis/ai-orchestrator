"""`just check-plan` refuses a plan a launch would spend dispatches failing.

This is the whole seam an operator touches, driven for real: the recipe, the console
script it delegates to, this checkout's own `config/onejudge.base.yaml`, the tracked
`config/dispatch-appendix.md`, and the real `onepipeline` binary whose linked
`oneagentgraph` ships the role a node's `persona` name resolves to. Nothing is
doubled — there is nothing here to double, because the guard spends no provider turn
and launches nothing.

Both directions are driven, because only the pair means anything. A plan whose nodes
state the bar they will be judged against passes and says how many it read; one whose
node omits a demand it will be held to is refused, with a non-zero exit and the reason
on stderr, before a single node is scheduled. The refused shape here is the one that
actually happened: criteria that were complete about the work and silent about proving
it end to end. A demand is refused **against whichever source really makes it**, and
both sources are driven — the appendix this repository tracks, and the role lifted out
of the engine binary — because a refusal naming the wrong one sends a plan's author to
edit something that decides nothing.

These journeys read the tracked appendix, so they belong to the tier keyed on this
repository's prose: editing that file changes what this recipe accepts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from project_fixtures import project_from_plan, reviewed
from waits import timeout as e2e_timeout

from orchestrator.criteria_guard import APPENDIX, OUT_OF_DISPATCH
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs


def _task(criteria: str) -> str:
    """A node's task in the shape every plan here writes, carrying the real appendix."""
    return (
        "## What\n\nAdd the route and the test that drives it.\n\n"
        "## Why\n\nThe user cannot complete a purchase without it.\n\n"
        f"## Acceptance criteria\n\n{criteria}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


#: Criteria that answer every demand the appendix and the built-in `engineer` bar
#: make, stated as properties of the finished tree rather than as commands.
STATES_ITS_BAR = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as "
    "it finally stands."
)

#: The same node whose reporting criterion says "account" rather than "claim". Every
#: word the demand accepts is exercised through the real recipe, because a wording a
#: plan's author may reasonably reach for and that only a unit test has ever accepted
#: is one this journey would not notice losing.
STATES_ITS_BAR_AS_AN_ACCOUNT = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths.\n"
    "- The dispatch's account of the finished work is true of the tree as it finally "
    "stands."
)

#: The same node with the end-to-end criterion dropped. This is the shape that was
#: dispatched, finished, gate-green, and failed anyway — the demand was made, the
#: criteria never said so, and the judge supplied its own reading. On the adopted
#: stack that demand comes from the appendix rather than from the role: oneagentgraph
#: 0.3.5 holds a dispatch to what it can prove from inside its own run, so the shipped
#: `engineer` bar asks for the change to be "proven at the level this run can reach"
#: and no longer says "end to end" at all. Which is the guard working as designed —
#: it enforces a demand only where it is really made.
OMITS_A_DEMAND = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as "
    "it finally stands."
)

#: And the same node with the *reporting* criterion dropped instead, which is the
#: demand the shipped role still makes. Both fixtures are here because the two are
#: refused through different halves of the guard, and only this one exercises the role
#: lifted out of the engine binary — the reading `AGENTS.md` records losing a dispatch
#: to. What that demand asks for is a property of the finished tree, not a final report
#: as an artifact: the ordering it used to ask for failed six nodes with complete,
#: committed, green work and no acceptance criterion unmet.
OMITS_A_DEMAND_THE_ROLE_MAKES = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths."
)


def _plan(root: Path, criteria: str) -> Path:
    written = root / "plan.json"
    written.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Deliver the checkout route"},
                "name": "check-plan-e2e",
                "tasks": [
                    {
                        "id": "route",
                        "persona": "engineer",
                        "repo": "https://github.com/nickderobertis/some-service",
                        "title": "feat: add the checkout route",
                        "task": _task(criteria),
                    },
                    {"id": "approve", "kind": "human", "task": "Approve the release."},
                ],
            }
        ),
        encoding="utf-8",
    )
    return written


def _check_project(
    project: str, *, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Drive the recipe with one already materialized qualified project.

    Every project here is reviewed first — through the real `just review-plan`, with
    only the paid provider scripted — because since the review gate landed a plan
    nothing has reviewed is refused before its criteria are read at all, and what these
    journeys are about is the criteria. The gate itself is
    `tests/e2e/test_plan_review_e2e.py`'s subject, which reviews nothing in advance.
    """
    if ":" in project and not project.endswith(":absent"):
        reviewed(project)
    return subprocess.run(
        ["just", "check-plan", project],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _check_plan(plan: Path) -> subprocess.CompletedProcess[str]:
    """Run the real recipe from this checkout."""
    project = project_from_plan(plan) if plan.is_file() else "authoring:absent"
    return _check_project(project)


def test_project_store_reports_malformed_stdin_at_its_command_boundary(tmp_path: Path) -> None:
    refused = subprocess.run(
        [sys.executable, "-m", "orchestrator.project_store", str(tmp_path / "store")],
        cwd=REPO_ROOT,
        input="{bad",
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "input is not valid JSON" in refused.stderr
    assert "Traceback" not in refused.stderr


@pytest.mark.parametrize(
    ("payload", "destination", "message"),
    (
        ('{"tasks":[]}', "store", "requires string"),
        ('{"name":"p","tasks":[{}]}', "store", "requires a string `id`"),
        (
            '{"name":"p","tasks":[{"id":"same id"},{"id":"same-id"}]}',
            "store",
            "slug-colliding",
        ),
        ('{"name":"p","tasks":[{"id":"a","task":7}]}', "store", "title and task"),
        ('{"name":"p","tasks":[{"id":"a","deps":[7]}]}', "store", "dependencies"),
        ('{"name":"p","tasks":[{"id":"a","repo":7}]}', "store", "repository"),
        ('{"name":"p","tasks":[{"id":"a","deps":["missing"]}]}', "store", "unknown"),
        ('{"name":"p","tasks":[]}', "blocked", "cannot write"),
    ),
)
def test_project_store_reports_semantic_and_write_failures(
    tmp_path: Path, payload: str, destination: str, message: str
) -> None:
    target = tmp_path / destination
    if destination == "blocked":
        target.write_text("not a directory", encoding="utf-8")
    refused = subprocess.run(
        [sys.executable, "-m", "orchestrator.project_store", str(target)],
        cwd=REPO_ROOT,
        input=payload,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert message in refused.stderr
    assert "Traceback" not in refused.stderr


def test_project_store_recovers_after_a_replacement_is_partially_written(tmp_path: Path) -> None:
    """Retrying the command completes a store whose second task blocked its first write.

    A root is read while it is being written, so what a half-written store leaves behind
    is the operator-visible property here: the project document is published last, and a
    write that failed before reaching it leaves no project at all rather than one whose
    tasks a source would then fail to open. The blocker is the *second* task, because a
    run that wrote nothing would exercise no recovery.
    """
    root = tmp_path / "store"
    blocked = root / "tasks" / "replacement" / "second.md"
    blocked.mkdir(parents=True)
    command = [sys.executable, "-m", "orchestrator.project_store", str(root)]
    payload = json.dumps({"name": "Replacement", "tasks": [{"id": "first"}, {"id": "second"}]})

    partial = subprocess.run(
        command,
        cwd=REPO_ROOT,
        input=payload,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )

    assert partial.returncode == 2, partial.stdout + partial.stderr
    assert (root / "tasks/replacement/first.md").is_file(), (
        "the failure happened before replacement began, so this does not exercise recovery"
    )
    assert not (root / "projects/replacement.md").exists(), (
        "a store that never finished its tasks must not publish the project a reader "
        "would then open those tasks from"
    )
    assert "cannot write generated plan" in partial.stderr

    blocked.rmdir()
    recovered = subprocess.run(
        command,
        cwd=REPO_ROOT,
        input=payload,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )

    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert (root / "tasks/replacement/second.md").is_file()
    assert (root / "projects/replacement.md").is_file()


def test_a_replacement_that_fails_leaves_the_published_project_where_it_was(
    tmp_path: Path,
) -> None:
    """Replacing an already published project, driven at the command boundary.

    The project document is written last, so a *replacement* is the case where one is
    already published while its tasks are being rewritten: a failure part-way leaves the
    reader the project it had, not a half-published new one, and the retry is what
    completes the replacement. The first write is the real command too, because a store
    assembled by hand would not be the store this recovers.
    """
    root = tmp_path / "store"
    command = [sys.executable, "-m", "orchestrator.project_store", str(root)]

    def store(payload: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=REPO_ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=e2e_timeout(30),
            check=False,
        )

    published = store({"name": "Replacement", "tasks": [{"id": "first", "task": "Original."}]})
    assert published.returncode == 0, published.stdout + published.stderr
    document = (root / "projects/replacement.md").read_text(encoding="utf-8")

    blocked = root / "tasks" / "replacement" / "second.md"
    blocked.mkdir()
    failed = store(
        {
            "name": "Replacement",
            "tasks": [{"id": "first", "task": "Replaced."}, {"id": "second"}],
        }
    )

    assert failed.returncode == 2, failed.stdout + failed.stderr
    assert "Replaced." in (root / "tasks/replacement/first.md").read_text(encoding="utf-8")
    assert (root / "projects/replacement.md").read_text(encoding="utf-8") == document

    blocked.rmdir()
    recovered = store(
        {
            "name": "Replacement",
            "tasks": [{"id": "first", "task": "Replaced."}, {"id": "second"}],
        }
    )

    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert (root / "tasks/replacement/second.md").is_file()


def test_project_store_slugs_dependency_targets_for_the_real_store(tmp_path: Path) -> None:
    root = tmp_path / "store"
    written = subprocess.run(
        [sys.executable, "-m", "orchestrator.project_store", str(root)],
        cwd=REPO_ROOT,
        input=json.dumps(
            {
                "name": "Slug dependency",
                "tasks": [
                    {"id": "Parent Node", "task": "Parent."},
                    {"id": "Child Node", "task": "Child.", "deps": ["Parent Node"]},
                ],
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    environment = os.environ | {"ONETASKGRAPH_SOURCES__TEST_FIXTURES__CONFIG__ROOT": str(root)}
    read = subprocess.run(
        [
            "just",
            "plans",
            "task",
            "deps",
            "test-fixtures:slug-dependency/child-node",
            "--json",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert read.returncode == 0, read.stderr
    edges = json.loads(read.stdout)["items"]
    assert edges[0]["to"]["id"] == "test-fixtures:slug-dependency/parent-node"


def test_project_store_replacement_removes_tasks_absent_from_the_new_plan(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    command = [sys.executable, "-m", "orchestrator.project_store", str(root)]
    first = subprocess.run(
        command,
        cwd=REPO_ROOT,
        input=json.dumps(
            {
                "name": "Replacement",
                "tasks": [{"id": "kept"}, {"id": "removed"}],
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    replacement = subprocess.run(
        command,
        cwd=REPO_ROOT,
        input=json.dumps({"name": "Replacement", "tasks": [{"id": "kept"}]}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr
    environment = os.environ | {"ONETASKGRAPH_SOURCES__TEST_FIXTURES__CONFIG__ROOT": str(root)}
    read = subprocess.run(
        [
            "just",
            "plans",
            "task",
            "list",
            "--source",
            "test-fixtures",
            "--project",
            "replacement",
            "--json",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert read.returncode == 0, read.stderr
    assert [record["id"] for record in json.loads(read.stdout)["items"]] == [
        "test-fixtures:replacement/kept"
    ]


@pytest.mark.parametrize(
    "criteria",
    [
        pytest.param(STATES_ITS_BAR, id="as-a-claim"),
        pytest.param(STATES_ITS_BAR_AS_AN_ACCOUNT, id="as-an-account"),
    ],
)
def test_a_plan_whose_node_states_its_bar_is_accepted(tmp_path: Path, criteria: str) -> None:
    """The accepting half, and the count that says the human node was not checked.

    A `kind: human` node carries an action a person performs rather than a task a
    judge reads, so counting it would be counting a dispatch that never happens.

    Both wordings of the reporting criterion run through the real recipe. That demand
    stopped asking for a final report as an artifact and started asking for a property
    of the finished tree — the ordering it replaced failed six nodes with complete,
    committed, green work — so a plan is free to state the property in its own words,
    and each word the demand accepts is proven at the boundary a plan's author uses.
    """
    checked = _check_plan(_plan(tmp_path, criteria))

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "1 dispatched node(s)" in checked.stdout, checked.stdout


def test_a_task_with_no_acceptance_criteria_at_all_is_refused(tmp_path: Path) -> None:
    """A node with no criteria has no bar of its own, so it is judged entirely on the
    role's — which is the whole failure this guard exists to catch, at its extreme."""
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    node = document["tasks"][0]
    node["task"] = node["task"].replace("## Acceptance criteria", "## Notes")
    plan.write_text(json.dumps(document), encoding="utf-8")

    refused = _check_plan(plan)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "Acceptance criteria" in refused.stderr, refused.stderr


def test_a_plan_of_human_actions_alone_is_accepted_and_says_it_checked_nothing(
    tmp_path: Path,
) -> None:
    """A plan can be all coordination, and the count is what says so.

    Reporting it as checked without saying how many nodes were read would let a plan
    whose nodes were all skipped for the wrong reason look exactly like a clean one.
    """
    plan = tmp_path / "human.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "check-plan-human-e2e",
                "tasks": [{"id": "approve", "kind": "human", "task": "Approve the release."}],
            }
        ),
        encoding="utf-8",
    )

    checked = _check_plan(plan)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "0 dispatched node(s)" in checked.stdout, checked.stdout


def test_check_plan_reads_every_page_of_a_multi_page_project(tmp_path: Path) -> None:
    plan = tmp_path / "paged.json"
    plan.write_text(
        json.dumps(
            {
                "name": "check-plan-paged-e2e",
                "tasks": [
                    {"id": f"human-{index}", "kind": "human", "task": f"Approve {index}."}
                    for index in range(3)
                ],
            }
        ),
        encoding="utf-8",
    )

    checked = _check_plan(plan)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "0 dispatched node(s)" in checked.stdout


def test_a_plan_whose_node_omits_a_demand_it_will_be_held_to_is_refused(tmp_path: Path) -> None:
    """The refusal, at the seam and for the reason it exists.

    The message has to carry both halves for the plan's author to act on it: which
    demand went unanswered, and where it is made — here the appendix this repository
    tracks and every node's task carries, which is where the end-to-end demand lives
    on the adopted stack. The section it names moved with the complete gate: the
    operational notes used to demand that the gate chain run "end to end in one
    command", and with that instruction gone the demand a node is held to is the one
    the appendix's own closing section makes of every implementation dispatch.
    """
    refused = _check_plan(_plan(tmp_path, OMITS_A_DEMAND))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    reported = refused.stderr
    assert "route:" in reported, reported
    assert "proof end to end" in reported, reported
    assert "State the bar in" in reported, reported
    assert "State it as a criterion" in reported, reported


def test_a_demand_the_shipped_role_makes_is_refused_against_that_role(tmp_path: Path) -> None:
    """The other half of the same seam: a demand that comes from the engine binary.

    This is the reading the guard exists to make and the one this repository has
    already lost a dispatch to getting wrong — the bar in force is the role compiled
    into the `oneagentgraph` the adopted `onepipeline` links, never a file in
    `personas/` and never the CLI pinned beside it. A refusal that named the wrong
    source would send a plan's author to edit something that decides nothing.
    """
    refused = _check_plan(_plan(tmp_path, OMITS_A_DEMAND_THE_ROLE_MAKES))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    reported = refused.stderr
    assert "route:" in reported, reported
    assert "an account of this dispatch's own work" in reported, reported
    assert "true of the tree as it finally stands" in reported, reported
    assert "engineer" in reported and "onepipeline" in reported, reported
    assert "State it as a criterion" in reported, reported


@pytest.mark.parametrize("phrase", OUT_OF_DISPATCH)
def test_a_criterion_resting_on_work_the_dispatch_cannot_do_is_refused(
    tmp_path: Path, phrase: str
) -> None:
    """Every phrase naming state that only exists after the worker settles.

    Parametrized over the guard's own list rather than over a copy of it, so a phrase
    added there is a case here — otherwise the one that goes unexercised is exactly
    the one nobody thought to write down twice. A node was failed against `The branch
    publishes.` with its branch finished and waiting: publication is the lifecycle's,
    and no worker can reach it from inside its own dispatch.
    """
    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n- {phrase.capitalize()}."))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert phrase in refused.stderr, refused.stderr
    assert "the dispatch cannot do" in refused.stderr, refused.stderr


@pytest.mark.parametrize(
    ("criterion", "reason"),
    (
        pytest.param("- The route behaves as described above.", "reconstruct", id="deferred"),
        pytest.param(
            "- The response repeats the exact wording of the spec.",
            "particular string",
            id="wording",
        ),
        pytest.param("- `just gate` is green.", "`just` invocation", id="just"),
        pytest.param("- The suite passes && the gate is green.", "chained shell", id="chained"),
        pytest.param("- `pytest tests/` passes.", "shell invocation", id="shell"),
    ),
)
def test_a_criterion_that_names_a_procedure_rather_than_a_property_is_refused(
    tmp_path: Path, criterion: str, reason: str
) -> None:
    """The other class of refusal, at the seam an operator meets it.

    A judge reads a criterion literally, so it cannot accept an equivalent route for a
    command string and it reconstructs a deferred one as a wording demand. Each
    criterion is added beside a complete set, so what is refused is the criterion
    itself and not an omission.
    """
    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}"))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert reason in refused.stderr, refused.stderr


def test_a_lifecycle_node_is_read_through_its_steps_rather_than_as_one_node(
    tmp_path: Path,
) -> None:
    """The other plan shape an operator writes, and the count that proves it was walked.

    A lifecycle node runs several steps in sequence on one branch, so each step is a
    dispatch and the node above them is not. A reader that checked the node instead
    would find no `task` on it and refuse a plan that is fine.
    """
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    node = document["tasks"][0]
    document["tasks"][0] = {
        "id": "service",
        "repo": node["repo"],
        "title": node["title"],
        "steps": [
            {"id": "implement", "persona": "engineer", "task": node["task"]},
            {"id": "document", "persona": "docs-writer", "task": node["task"]},
        ],
    }
    plan.write_text(json.dumps(document), encoding="utf-8")

    checked = _check_plan(plan)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "2 dispatched node(s)" in checked.stdout, checked.stdout


def test_a_node_that_never_says_what_it_reports_is_refused(tmp_path: Path) -> None:
    """The second demand nobody wrote down, refused at the seam.

    A branch was failed for never having "provided a final verified completion
    report" — a demand in neither its task nor the shared clause. The bar and the
    appendix both ask a worker to report; a node whose criteria never say anything about
    what it claims of the finished work leaves the judge to decide what that meant. What
    the refusal asks for is the property rather than that artifact: the ordering demand
    that once answered this failed six nodes with complete, committed, green work.
    """
    silent = "\n".join(line for line in STATES_ITS_BAR.splitlines() if "claim" not in line)

    refused = _check_plan(_plan(tmp_path, silent))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "an account of this dispatch's own work" in refused.stderr, refused.stderr
    assert "true of the tree as it finally stands" in refused.stderr, refused.stderr
    for withdrawn in ("last thing", "closes with", "final report", "reported afresh"):
        assert withdrawn not in refused.stderr, refused.stderr


def test_a_node_naming_a_persona_no_dispatch_could_resolve_is_refused(tmp_path: Path) -> None:
    """`persona: orchestrator` is read as `graphs/orchestrator`, and settles `failed`.

    A dispatch discovers that in `oneagentgraph`'s config validation, after the node
    has been scheduled. The recipe discovers it for free, before the launch.
    """
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    document["tasks"][0]["persona"] = "orchestrator"
    plan.write_text(json.dumps(document), encoding="utf-8")

    refused = _check_plan(plan)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "graphs/orchestrator" in refused.stderr, refused.stderr


def _with_node(plan: Path, **fields: object) -> Path:
    """The same plan with its dispatched node's fields replaced or removed."""
    document = json.loads(plan.read_text(encoding="utf-8"))
    for key, value in fields.items():
        if value is None:
            document["tasks"][0].pop(key, None)
        else:
            document["tasks"][0][key] = value
    plan.write_text(json.dumps(document), encoding="utf-8")
    return plan


@pytest.mark.parametrize(
    "persona",
    (
        pytest.param(None, id="no-persona"),
        pytest.param("../personas/orchestrator.yaml", id="by-path"),
    ),
)
def test_every_way_a_node_names_its_bar_resolves_through_the_recipe(
    tmp_path: Path, persona: str | None
) -> None:
    """The two resolutions besides a shipped name, driven where an operator uses them.

    A node with no `persona` is left with the base config's generic contract, and one
    naming a path relative to `graphs/` gets that file's — the only shape in
    `personas/` a dispatch reads. Neither resolution may make the guard fall over or
    stop refusing: a plan that omits a demand is still refused under both, and which
    source the demand came from is what the shipped-name journey above asserts.
    """
    accepted = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), persona=persona))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout

    refused = _check_plan(_with_node(_plan(tmp_path, OMITS_A_DEMAND), persona=persona))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "proof end to end" in refused.stderr, refused.stderr


@pytest.mark.parametrize(
    ("fields", "reason"),
    (
        pytest.param({"persona": "../../elsewhere.yaml"}, "outside this checkout", id="escaped"),
        pytest.param({"persona": 7}, "`persona` is int", id="persona-not-a-name"),
        pytest.param({"task": None}, "states no `task` string", id="no-task"),
        pytest.param({"kind": "review"}, "`kind` is 'review'", id="unknown-kind"),
        pytest.param({"steps": 3}, "`steps` is int", id="steps-not-a-list"),
        pytest.param({"steps": ["one"]}, "`steps[0]` is str", id="step-not-an-object"),
    ),
)
def test_a_node_whose_own_fields_are_wrong_is_refused_by_the_field(
    tmp_path: Path, fields: dict[str, object], reason: str
) -> None:
    """A plan is a document some other tool wrote, so its shape is untrusted here too.

    Each of these reaches the operator as a diagnostic naming the field, rather than
    as whatever exception reaching into the wrong shape happened to produce.
    """
    refused = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), **fields))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert reason in refused.stderr, refused.stderr


class Drafted(NamedTuple):
    """A persona file in this checkout, and the ref a plan node names it by."""

    file: Path
    #: Relative to `graphs/`, which is where `oneagentgraph` reads a ref from.
    ref: str


@pytest.fixture
def drafted_persona(request: pytest.FixtureRequest) -> Iterator[Drafted]:
    """A persona file inside this checkout, named the way a node would name one.

    A ref is read relative to `graphs/` in the launch directory and refused when it
    lands outside the checkout, so a journey about a persona *file* has to put one
    here. `scratch/personas/` is where `personas/README.md` says a draft persona
    lives while it is being proven, and it is gitignored, so the tree stays clean.
    """
    drafts = REPO_ROOT / "scratch" / "personas"
    drafts.mkdir(parents=True, exist_ok=True)
    written = drafts / f"{request.node.name}.yaml"
    try:
        yield Drafted(written, str(Path("..") / written.relative_to(REPO_ROOT)))
    finally:
        written.unlink(missing_ok=True)


def test_a_persona_file_stating_no_review_bar_is_refused(
    tmp_path: Path, drafted_persona: Drafted
) -> None:
    """A file naming neither review field is inert, and a plan's author cannot see that.

    The node is reviewed against the base config's generic contract instead, so
    nothing downstream would report that the file the plan named was never read.
    """
    drafted_persona.file.write_text(
        "name: inert\nsystem_prompt: |\n  A role and nothing else.\n", encoding="utf-8"
    )

    refused = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), persona=drafted_persona.ref))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "states neither `user.persona` nor `user.done_when`" in refused.stderr, refused.stderr


def test_a_persona_that_replaces_the_shared_bar_is_the_only_bar_in_force(
    tmp_path: Path, drafted_persona: Drafted
) -> None:
    """`user.done_when_replaces_base` drops the shared clause, and the guard follows it.

    Proven by what the refusal names: the demand comes from this file, and the node's
    criteria answer everything the shared clause would have asked for — so a guard
    still composing the base bar would have accepted.
    """
    drafted_persona.file.write_text(
        "name: standalone\nuser:\n  persona: |\n    Only mine.\n"
        "  done_when: 'the change is proven end to end by a soak run'\n"
        "  done_when_replaces_base: true\n",
        encoding="utf-8",
    )
    silent = "\n".join(line for line in STATES_ITS_BAR.splitlines() if "end to end" not in line)

    refused = _check_plan(_with_node(_plan(tmp_path, silent), persona=drafted_persona.ref))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "scratch/personas" in refused.stderr, refused.stderr
    assert "proof end to end" in refused.stderr, refused.stderr


def test_a_task_rebuilt_from_a_stale_appendix_is_refused(tmp_path: Path) -> None:
    """The copy-paste failure this promotion exists to end, driven through the recipe.

    Every task carries the appendix by copy, so a builder cloned before an appendix
    fix silently reintroduces the wording that fix removed — which is exactly how the
    complete-gate contradiction outlived being noticed, and exactly what an older
    builder would do with the complete-gate instruction this host has since removed.
    The edit below is that regression in miniature: the leading rule about which checks
    a dispatch owes, replaced by the chained gate invocation it superseded.
    """
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    stale = document["tasks"][0]["task"].replace(
        "Run only the checks that exercise what you changed",
        "Run `just bootstrap && just gate` once at closeout",
    )
    assert stale != document["tasks"][0]["task"], (
        "the appendix no longer carries the leading rule this journey ages out, so "
        "nothing here proves a task rebuilt from an older copy is refused"
    )
    document["tasks"][0]["task"] = stale
    plan.write_text(json.dumps(document), encoding="utf-8")

    refused = _check_plan(plan)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "current operational appendix" in refused.stderr, refused.stderr


def test_a_plan_that_cannot_be_read_is_not_reported_as_a_refusal(tmp_path: Path) -> None:
    """Exit 2, because no project was read and therefore nothing was judged."""
    unreadable = _check_plan(tmp_path / "absent.json")

    assert unreadable.returncode == 2, unreadable.stdout + unreadable.stderr
    assert "authoring:absent" in unreadable.stderr, unreadable.stderr
    assert "just orchestrate" in unreadable.stderr, unreadable.stderr


def _research_task(criteria: str) -> str:
    """A node whose job is measurement, carrying the real appendix like any other."""
    return (
        "## What\n\nMeasure which limitations the corpus actually demonstrates.\n\n"
        "## Why\n\nThe user cannot tell which limitations are real.\n\n"
        f"## Acceptance criteria\n\n{criteria}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


#: The pairing that could not have succeeded, in the shape it was launched in:
#: `persona: researcher`, whose shipped bar refuses the work outright if the agent
#: changed any project file, under criteria that require a tracked document to gain a
#: row. The judge was required to fail the work the task was required to produce, and
#: it settled `task-failed` citing a file that does not exist.
RESEARCH_REQUIRES_AN_EDIT = (
    "- `docs/fern-limitations.md` gains a row for every limitation the corpus names.\n"
    "- A journey reads that document back end to end.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)

#: The same node written as the question that bar is for. Same persona, same file
#: named in the same criterion — what differs is that nothing here requires the tree
#: to change, and refusing this would be the false refusal that gets worked around.
RESEARCH_ANSWERS_A_QUESTION = (
    "- The answer cites `docs/fern-limitations.md` and the line each claim rests on.\n"
    "- Confirmed facts are separated from inferences, traced end to end.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)

#: The criterion whose real refusal forced a manager to make a sound plan vaguer.
#: Its later "changes" describes the branch; the action governing the named template
#: is "read", and this process journey keeps that distinction at the public recipe.
RESEARCH_READS_A_TEMPLATE_ABOUT_CHANGES = (
    "- The report states a body for change request #127 following that repository's "
    "own pull request template — read the template from "
    "`.github/pull_request_template.md` in the publication checkout rather than "
    "assuming its shape — describing what the branch changes and why.\n"
    "- Confirmed facts are traced end to end against the sources.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)

#: Both actions concern the same named file, with the edit nearer the path. This is
#: still impossible under the researcher bar even though the criterion also reads it.
RESEARCH_READS_THEN_EDITS = (
    "- After reading the source material, update `docs/fern-limitations.md` with the "
    "confirmed findings.\n"
    "- The result is traced end to end against the sources.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)

#: An intentionally terse but valid criterion puts the two actions equally close to
#: the path. Ambiguity must retain the refusal rather than license an edit.
RESEARCH_HAS_EQUIDISTANT_ACTIONS = (
    "- read `docs/fern-limitations.md` edit\n"
    "- The result is traced end to end against the sources.\n"
    "- The dispatch closes with a completion report naming the evidence it verified."
)

#: The same read-only node with the two halves of the conflict scattered across
#: *different* criteria: the path is named by one that only reads it, and the word
#: asserting something changed belongs to the next one, about the report. Read as one
#: block this pairs, and a plan nobody wrote a fault into would be refused; the
#: refusal is a property of a single criterion, which is what keeps this accepted.
RESEARCH_NAMES_A_PATH_AND_A_CHANGE_IN_DIFFERENT_CRITERIA = (
    "- The answer cites `docs/fern-limitations.md` and the line each claim rests on.\n"
    "- A new section of the report separates confirmed facts from inferences.\n"
    "- That report is traced end to end against the corpus it was read from."
)


def test_a_node_whose_bar_forbids_the_edit_its_criteria_require_is_refused(
    tmp_path: Path,
) -> None:
    """The contradiction refused at the seam, for the cost of a `check-plan` run.

    Every part of the message is load-bearing, because the fix is only obvious once
    all of it is there: which node, which persona's bar refuses, the clause it
    refuses under, the criterion that contradicts it, and a persona that would not.
    """
    refused = _check_plan(
        _with_node(
            _plan(tmp_path, STATES_ITS_BAR),
            persona="researcher",
            task=_research_task(RESEARCH_REQUIRES_AN_EDIT),
        )
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    reported = refused.stderr
    assert "route:" in reported, reported
    assert "researcher" in reported, reported
    assert "modified project files" in reported, reported
    assert "docs/fern-limitations.md" in reported, reported
    assert "`docs-writer`" in reported, reported


def test_the_same_read_only_node_asking_a_question_is_accepted(tmp_path: Path) -> None:
    """The other half, and the half that keeps the check worth having.

    A read-only role naming the file it read is what that role is for, so the pairing
    — not the persona, and not the path — has to be what decides.
    """
    accepted = _check_plan(
        _with_node(
            _plan(tmp_path, STATES_ITS_BAR),
            persona="researcher",
            task=_research_task(RESEARCH_ANSWERS_A_QUESTION),
        )
    )

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


def test_a_read_only_node_may_read_a_named_template_about_branch_changes(
    tmp_path: Path,
) -> None:
    """The paid-out false refusal is accepted through the real recipe and plan file."""
    accepted = _check_plan(
        _with_node(
            _plan(tmp_path, STATES_ITS_BAR),
            persona="researcher",
            task=_research_task(RESEARCH_READS_A_TEMPLATE_ABOUT_CHANGES),
        )
    )

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


@pytest.mark.parametrize("criteria", (RESEARCH_READS_THEN_EDITS, RESEARCH_HAS_EQUIDISTANT_ACTIONS))
def test_a_read_action_does_not_hide_an_edit_governing_the_same_path(
    tmp_path: Path, criteria: str
) -> None:
    """Competing actions retain the full refusal through the real recipe."""
    refused = _check_plan(
        _with_node(
            _plan(tmp_path, STATES_ITS_BAR),
            persona="researcher",
            task=_research_task(criteria),
        )
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    reported = refused.stderr
    assert "route:" in reported, reported
    assert "modified project files" in reported, reported
    assert "docs/fern-limitations.md" in reported, reported
    assert "`docs-writer`" in reported, reported


def test_an_editing_node_under_a_bar_that_permits_it_is_accepted(tmp_path: Path) -> None:
    """The pairing's other permitted combination, driven through the real recipe.

    Criteria that require a tracked file to change are what most nodes of most plans
    state, and the `engineer` bar this checkout's engine ships makes no demand against
    them — so the check has to be silent here or it refuses ordinary implementation
    work. Same criteria as the refused journey above; only the bar differs.
    """
    accepted = _check_plan(
        _with_node(
            _plan(tmp_path, STATES_ITS_BAR),
            persona="engineer",
            task=_research_task(RESEARCH_REQUIRES_AN_EDIT),
        )
    )

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


def test_a_path_read_in_one_criterion_is_not_paired_with_a_change_in_the_next(
    tmp_path: Path,
) -> None:
    """The false refusal the split prevents, refused or accepted by the real recipe.

    This is the shape a sound read-only plan most often has — it names the document
    it read, and it says the report gains a section — so a check that paired those
    two across criteria would refuse ordinary research and be worked around. Nothing
    but the recipe's own exit status can say which way this lands.
    """
    accepted = _check_plan(
        _with_node(
            _plan(tmp_path, STATES_ITS_BAR),
            persona="researcher",
            task=_research_task(RESEARCH_NAMES_A_PATH_AND_A_CHANGE_IN_DIFFERENT_CRITERIA),
        )
    )

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


class Malformed(NamedTuple):
    """One lifecycle `steps` value a store record can carry, and what it must be told."""

    #: The value the plan node states, which reaches the task record as
    #: `onepipeline.steps` and comes back out of `_project_plan` unchanged.
    steps: object
    #: The refusal's opening, naming the field the plan's author has to fix.
    field: str
    #: The record path the refusal sends them to, which must exist in this checkout.
    example: str


#: Both refusals a real qualified project can reach: a store record's `onepipeline.`
#: metadata is arbitrary JSON — nothing between the plan's author and this guard
#: narrows it — so a `steps` that is not a list, and one whose entries are not
#: objects, are the two shapes that arrive here malformed.
MALFORMED_STEPS = (
    Malformed(3, "route's `steps` is int, not a list", "examples/tasks/tracked-release/service.md"),
    Malformed(
        ["one"],
        "route's `steps[0]` is str, not an object",
        "examples/tasks/tracked-release/",
    ),
)


@pytest.mark.parametrize("malformed", MALFORMED_STEPS, ids=lambda item: item.field)
def test_a_malformed_step_is_refused_against_the_plan_input_the_recipe_reads(
    tmp_path: Path, malformed: Malformed
) -> None:
    """The refusal names the store record to fix and an example this checkout has."""
    refused = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), steps=malformed.steps))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    reported = refused.stderr
    assert malformed.field in reported, reported
    assert "task record" in reported, reported
    assert "JSON" not in reported, reported
    assert malformed.example in reported, reported
    assert (REPO_ROOT / malformed.example).exists(), reported
