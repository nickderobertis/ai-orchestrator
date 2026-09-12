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
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from criteria_examples import (
    PUBLICATION_COPULAS,
    PUBLICATION_IN_PROSE,
    PUBLISHED_WITH_A_WORD_IN_THE_WAY,
    RED_BEFORE_GREEN,
    RELEASED_ELSEWHERE,
    RELEASED_ELSEWHERE_IN_PROSE,
    STATES_THE_PROPERTY_INSTEAD,
)
from project_fixtures import project_from_plan, reviewed
from scratch_identity import registered, seeded
from waits import timeout as e2e_timeout

from orchestrator import host_installs, plan_store
from orchestrator.criteria_guard import (
    APPENDIX,
    APPENDIX_ENV,
    AUTHORIZATIONS,
    OUT_OF_DISPATCH,
    Authorization,
    OutOfDispatch,
)
from orchestrator.root import REPO_ROOT

#: This suite is its own Nx project, `plan-tooling`, rather than a marker tier of the
#: orchestrator project: every journey here spawns the installed `onepipeline`, the
#: `just` recipes, the registered check script and — through `just review-plan` — a real
#: `oneharness run`, which is a different cost from the Python suite beside it and is
#: answered by a different set of files. `tests/plan_tooling/project.json` names that
#: set as `planToolingWorkspace`, and `tests/conftest.py` holds these tests to it.


def _task(criteria: str, own: str = "", appended: str = "") -> str:
    """A node's task in the shape every plan here writes, carrying the real appendix.

    ``own`` is what the task's author writes under `## Additional info` above the
    appendix — the appendix is copied in verbatim below it, its own heading included,
    which is the layout every task carrying author's notes has. ``appended`` is what the
    engine adds below the appendix at dispatch — a carried note under `## Planner
    context`, the cross-repository references — which no author wrote.
    """
    additional = f"## Additional info\n\n{own}\n\n" if own else ""
    return (
        "## What\n\nAdd the route and the test that drives it.\n\n"
        "## Why\n\nThe user cannot complete a purchase without it.\n\n"
        f"## Acceptance criteria\n\n{criteria}\n\n{additional}"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n{appended}"
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
#: and no longer says "end to end" at all.
#:
#: **It reaches a dispatch now**, and that is this tier's doing rather than the shape
#: having become sound: whether the criteria answer a demand their own bar makes is
#: judged by `just review-plan`'s turn, by meaning, because the matcher that asked it
#: here refused wordings the same review had just asked for.
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


#: The same node carrying a criterion about somebody else's released artifact, which is a
#: refusal this tier makes on its own account rather than one it hands to a judged turn.
#: Two journeys below need one: each drives a path where a refusal's *shape* is the
#: subject — one loader against another, and the registered check's own JSON answer — so
#: what the criteria did wrong has to be something those paths can still be refused for.
RESTS_ON_A_RELEASE = f"{STATES_ITS_BAR}\n{RELEASED_ELSEWHERE[0]}"


def _plan(root: Path, criteria: str, own: str = "", appended: str = "") -> Path:
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
                        "task": _task(criteria, own, appended),
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
    `tests/plan_tooling/test_plan_review_e2e.py`'s subject, which reviews nothing in advance.
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


def test_a_section_whose_heading_merely_starts_with_the_criteria_heading_is_accepted(
    tmp_path: Path,
) -> None:
    """A different heading that begins with the same words, driven through the recipe.

    A node documenting this tier writes `## Acceptance criteria examples`, and reading
    it as a second opening of the criteria heading refused the node for stating its
    criteria exactly once. It is at the recipe boundary because that is where a plan's
    author meets the refusal, and because the accepting half is the half a matcher
    reaching too far turns into a plan nobody can launch.
    """
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    node = document["tasks"][0]
    node["task"] = node["task"].replace(
        "## Acceptance criteria\n",
        "## Acceptance criteria examples\n\nThey are stated as properties of the tree.\n\n"
        "## Acceptance criteria\n",
        1,
    )
    plan.write_text(json.dumps(document), encoding="utf-8")

    checked = _check_plan(plan)

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
                "schema_version": 3,
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


@pytest.mark.parametrize(
    "criteria",
    (
        pytest.param(OMITS_A_DEMAND, id="the-appendixs-own-demand"),
        pytest.param(OMITS_A_DEMAND_THE_ROLE_MAKES, id="the-shipped-roles-demand"),
    ),
)
def test_a_node_silent_about_a_demand_reaches_a_dispatch_from_here(
    tmp_path: Path, criteria: str
) -> None:
    """Both carriers of a demand, and both now left to the judged turn, through the recipe.

    These were two refusals here: a demand the tracked appendix makes of every
    implementation dispatch, and one the role lifted out of the engine binary makes. Both
    were asked by matching a phrase, and matching a phrase is what made the two tiers
    refuse each other's required wording — a review refused a criterion for pinning a
    spelling while this tier refused the same task for lacking a literal phrase its
    criteria stated across three sentences of their own, and the review key being over the
    task's own content meant inserting words to satisfy the matcher bought another judged
    turn. So the demand is unchanged and the tier that reads it moved: `just review-plan`
    asks whether the criteria answer it, by meaning.

    Driven at the recipe rather than only in the unit tier because this is an
    *acceptance* now, and an acceptance is the half a regression hides in: a matcher
    quietly reintroduced here refuses a plan an operator has no way to correct, since the
    wording it would demand is the wording the review would refuse.
    """
    accepted = _check_plan(_plan(tmp_path, criteria))

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


@pytest.mark.parametrize("entry", OUT_OF_DISPATCH, ids=[one.example for one in OUT_OF_DISPATCH])
def test_a_criterion_resting_on_work_the_dispatch_cannot_do_is_refused(
    tmp_path: Path, entry: OutOfDispatch
) -> None:
    """Every entry naming state that only exists after the worker settles.

    Parametrized over the guard's own list rather than over a copy of it, so an entry
    added there is a case here — otherwise the one that goes unexercised is exactly
    the one nobody thought to write down twice. Each entry carries an example its own
    pattern matches whole, which is what keeps that true now the list is patterns: a
    pattern with no example to drive would be the unexercised case wearing a new shape.
    A node was failed against `The branch publishes.` with its branch finished and
    waiting: publication is the lifecycle's, and no worker can reach it from inside its
    own dispatch.
    """
    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n- {entry.example.capitalize()}."))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert entry.example in refused.stderr, refused.stderr
    assert "the dispatch cannot do" in refused.stderr, refused.stderr


def _sentence(fragment: str) -> str:
    """``fragment`` capitalized to open a sentence, as a criterion or a grant writes it."""
    return f"{fragment[0].upper()}{fragment[1:]}"


def _granting(*authorizations: Authorization) -> str:
    """A task's own `## Additional info` granting each carve-out in the appendix's words."""
    return " ".join(
        f"{_sentence(one.grant)}, under the carve-out the operational notes below name."
        for one in authorizations
    )


#: The wordings the admission must not reach, applied to the draft and to the demonstration
#: change request: a merge, a landing on a base, the merge path's own verdict, the node's
#: own publication, and a release. Each is refused with both grants written.
STILL_REFUSED_UNDER_EVERY_GRANT = (
    "- The draft is merged.",
    "- The demonstration PR lands on main.",
    "- The draft change request's required checks pass.",
    "- The branch publishes as a draft.",
    "- The demonstration pull request is merged.",
    "- The demonstration change request's wheel exists on the registry.",
)


@pytest.mark.parametrize("authorization", AUTHORIZATIONS, ids=lambda one: one.name)
def test_a_criterion_about_the_workers_own_draft_is_admitted_only_under_its_tasks_grant(
    tmp_path: Path, authorization: Authorization
) -> None:
    """Each carve-out's own example, through the recipe: admitted with the grant, refused without.

    A worker may open its session's change request as a draft, and a throwaway
    demonstration change request stacked on it, only when its task's own
    `## Additional info` says so in the words `config/dispatch-appendix.md` names. So the
    same criterion on the same node is admitted with that grant above the appendix and
    refused on an otherwise identical task without it — and the refusal names the grant
    to write rather than a precondition to state, which is the correction an author
    cannot derive.
    """
    criterion = f"- {_sentence(authorization.example)}."

    accepted = _check_plan(
        _plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}", _granting(authorization))
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout

    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}"))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "the dispatch cannot do" in refused.stderr, refused.stderr
    assert f"A criterion about {authorization.name} is admitted only" in refused.stderr, (
        refused.stderr
    )
    assert authorization.grant in refused.stderr, refused.stderr


def test_a_negated_grant_admits_nothing_through_the_recipe(tmp_path: Path) -> None:
    """A task that names a carve-out to withhold it has not granted it, at the surface.

    *"A throwaway demonstration change request is not authorized"* carries the words the
    grant is read in, and the recipe refuses the demonstration criterion under it exactly
    as it does under no grant at all — naming the grant to write, so an author who meant
    to withhold sees the refusal agree with them rather than a plan that launched.
    """
    demonstration, _ = AUTHORIZATIONS
    criterion = f"- {_sentence(demonstration.example)}."
    withheld = f"{_sentence(demonstration.grant).replace(' is authorized', ' is not authorized')}."
    assert "is not authorized" in withheld, withheld

    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}", withheld))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f"A criterion about {demonstration.name} is admitted only" in refused.stderr, (
        refused.stderr
    )


def test_a_grant_the_engine_appended_below_the_appendix_admits_nothing_through_the_recipe(
    tmp_path: Path,
) -> None:
    """A grant is the task's author's alone, at the surface: text below the appendix is none.

    The engine appends a carried note's text verbatim under `## Planner context`, so a
    note quoting a task spells `## Additional info` on a line of its own below the
    appendix. Read against the *last* opening of that heading, the section between it and
    the author's swallowed the note's sentence, and a task granting nothing was accepted
    on a grant nobody reviewed. So the recipe refuses the draft criterion under such a
    note exactly as under no grant at all, naming the grant to write — with the author's
    own section present and absent, since the appendix's heading is the first one in the
    latter.
    """
    _, early = AUTHORIZATIONS
    criterion = f"- {_sentence(early.example)}."
    carried = (
        f"\n## Planner context\n\n{_sentence(early.grant)}.\n\nQuoted from the task:\n\n"
        "## Additional info\n\nRun the checks that exercise the change.\n"
    )

    for own in ("", "The judge reads the route's test before anything else."):
        refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}", own, carried))

        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert f"A criterion about {early.name} is admitted only" in refused.stderr, refused.stderr
        assert early.grant in refused.stderr, refused.stderr

    accepted = _check_plan(
        _plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}", _granting(early), carried)
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_a_demonstration_criterion_is_refused_on_a_task_granting_only_early_publication(
    tmp_path: Path,
) -> None:
    """The two grants are two grants, at the surface an author meets the refusal.

    A task that lets its worker publish its draft early has not let it open a throwaway
    change request stacked on that draft, so a criterion about the demonstration change
    request is refused under early publication alone, naming the grant it needs.
    """
    demonstration, early = AUTHORIZATIONS
    criterion = f"- {_sentence(demonstration.example)}."

    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}", _granting(early)))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f"A criterion about {demonstration.name} is admitted only" in refused.stderr, (
        refused.stderr
    )


@pytest.mark.parametrize(
    "criterion", STILL_REFUSED_UNDER_EVERY_GRANT, ids=range(len(STILL_REFUSED_UNDER_EVERY_GRANT))
)
def test_the_wordings_the_carve_out_does_not_reach_stay_refused_under_every_grant(
    tmp_path: Path, criterion: str
) -> None:
    """The bound the admission is bought under, paid at the same surface.

    Every one of these names the draft or the demonstration change request and rests on
    something the worker still cannot reach — a merge, a landing, a required check, the
    branch's own publication, a release. Each is refused with both grants written, by the
    existing refusals: the subject of a clause admits nothing on its own.
    """
    refused = _check_plan(
        _plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}", _granting(*AUTHORIZATIONS))
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "the dispatch cannot do" in refused.stderr, refused.stderr
    assert "is admitted only" not in refused.stderr, refused.stderr


@pytest.mark.parametrize("criterion", RED_BEFORE_GREEN, ids=range(len(RED_BEFORE_GREEN)))
def test_every_red_before_green_form_is_refused_through_the_recipe(
    tmp_path: Path, criterion: str
) -> None:
    """The widening's own evidence, through the command surface an operator meets it at.

    Parametrized over `tests/criteria_examples.py` rather than over a selection from it,
    for the reason the out-of-dispatch journey above is parametrized over the guard's own
    list: a branch driven only by a unit test is the one that goes unexercised where it is
    actually reached, and it is reached here. Every entry is taken from
    `docs/plan-review-refusals.json`, where it cost a real judged review turn to refuse.
    """
    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}"))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "red-before-green" in refused.stderr, refused.stderr


@pytest.mark.parametrize(
    "criterion",
    (*PUBLISHED_WITH_A_WORD_IN_THE_WAY, *PUBLICATION_COPULAS),
    ids=range(len(PUBLISHED_WITH_A_WORD_IN_THE_WAY) + len(PUBLICATION_COPULAS)),
)
def test_every_publication_through_a_word_is_refused_through_the_recipe(
    tmp_path: Path, criterion: str
) -> None:
    """Every copula the matcher carries and every gap it steps over, at the surface.

    A publication is a publication whatever copula carries it and whatever stands
    between that copula and the participle, so each branch is driven where a refusal is
    actually paid rather than only where the pattern is read.
    """
    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}"))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "the dispatch cannot do" in refused.stderr, refused.stderr


@pytest.mark.parametrize(
    "criterion",
    (*STATES_THE_PROPERTY_INSTEAD, *PUBLICATION_IN_PROSE),
    ids=range(len(STATES_THE_PROPERTY_INSTEAD) + len(PUBLICATION_IN_PROSE)),
)
def test_the_sound_criteria_of_both_widened_shapes_still_reach_a_dispatch(
    tmp_path: Path, criterion: str
) -> None:
    """The bound each widening is bought under, at the surface the refusal is paid at.

    A widening refuses a plan outright, so what it costs is measured by the criteria of
    its own shape that must keep passing: the property a red-before-green demand stood in
    for, and a publication named in prose rather than rested on. Both are refused by
    nothing here, and a widening that started refusing either would be refusing the very
    correction its own refusal recommends.
    """
    accepted = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}"))

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


@pytest.mark.parametrize("criterion", RELEASED_ELSEWHERE, ids=range(len(RELEASED_ELSEWHERE)))
def test_a_criterion_about_somebody_elses_released_artifact_is_refused_through_the_recipe(
    tmp_path: Path, criterion: str
) -> None:
    """The shape that moved *into* this tier, at the surface an operator meets a refusal.

    Whether a release exists, or carries a named change, is not a fact about the finished
    tree under any wording, and establishing it means going and reading another
    repository. One such criterion required a pin to name a plan-store release carrying
    two fixes no release archive can carry; the worker correctly determined it could not
    be satisfied, and the node was killed and settled by hand with the rest of its work
    complete and landed.

    The refusal has to carry the correction as well as the complaint, because the
    correction is the one an author cannot derive: a release is not a property of the
    tree, so there is no worker-side precondition to state and what is left is the
    corresponding-content shape `personas/planner.yaml` admits.
    """
    refused = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}"))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "the dispatch cannot do" in refused.stderr, refused.stderr
    assert "state what this node's own committed content must carry" in refused.stderr, (
        refused.stderr
    )
    assert "not this node's bar" in refused.stderr, refused.stderr


@pytest.mark.parametrize(
    "criterion", RELEASED_ELSEWHERE_IN_PROSE, ids=range(len(RELEASED_ELSEWHERE_IN_PROSE))
)
def test_a_criterion_naming_a_release_while_resting_on_the_tree_reaches_a_dispatch(
    tmp_path: Path, criterion: str
) -> None:
    """The bound that widening is bought under, paid at the same surface.

    Every one of these names a release, a version, or a package and rests on the finished
    tree alone — and one of them is the corresponding-content correction the refusal above
    tells its author to write, which a widening that refused it would be refusing its own
    remedy for.
    """
    accepted = _check_plan(_plan(tmp_path, f"{STATES_ITS_BAR}\n{criterion}"))

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


def test_the_criteria_a_node_is_read_on_are_the_ones_its_own_heading_opens(
    tmp_path: Path,
) -> None:
    """Prose that merely names the heading neither begins the block nor ends it.

    Located by the first occurrence of that text anywhere, this plan's block began at the
    mention inside `## What` and ended at `## Why`, which left the criteria the node
    actually states in the half no rule read. Both halves of that fail and the quiet one
    is the dangerous one, so both are driven here: the `just gate` criterion the node
    really states is refused, and — the half a green return cannot show on its own — the
    refusal is about *that* criterion rather than about a span lifted out of the prose.
    """
    unclosed = "- A plan carrying `onepipeline.deps for an in-plan edge is refused."
    plan = _plan(tmp_path, unclosed)
    document = json.loads(plan.read_text(encoding="utf-8"))
    document["tasks"][0]["task"] = document["tasks"][0]["task"].replace(
        "## What\n\nAdd the route and the test that drives it.",
        "## What\n\nEvery demand this node is held to is stated in its "
        "`## Acceptance criteria`, never only in the prose around it.",
    )
    plan.write_text(json.dumps(document), encoding="utf-8")

    refused = _check_plan(plan)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "backtick run unclosed" in refused.stderr, refused.stderr
    # The criterion the refusal quotes is the one the heading opens, which is the half a
    # non-zero exit cannot show: read from the mention in `## What` instead, the block
    # would have been the prose between it and `## Why` — where this criterion is not,
    # and where nothing is refusable at all.
    assert unclosed.removeprefix("- ") in refused.stderr, refused.stderr


def test_a_task_that_opens_the_criteria_heading_twice_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """Which block states the node's bar cannot be decided from such a task.

    The judge is handed the whole task and reads both, so a reader here that picked either
    would be checking one while the dispatch is judged against the other — and naming the
    ambiguity is the same answer this repository's plan store gives a record that opens
    `metadata` twice.
    """
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    document["tasks"][0]["task"] += "\n## Acceptance criteria\n\n- `just gate` is green.\n"
    plan.write_text(json.dumps(document), encoding="utf-8")

    refused = _check_plan(plan)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "opens '## Acceptance criteria' 2 times" in refused.stderr, refused.stderr
    assert "Leave one block of criteria" in refused.stderr, refused.stderr


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


def test_a_node_that_never_says_what_it_reports_reaches_a_dispatch_from_here(
    tmp_path: Path,
) -> None:
    """The second demand nobody wrote down, and the tier that reads it now.

    A branch was failed for never having "provided a final verified completion report" —
    a demand in neither its task nor the shared clause. The bar and the appendix both ask
    a worker to report, and a node whose criteria say nothing about what it claims of the
    finished work leaves the judge to decide what that meant. This tier answered that by
    matching a phrase, which is what made it and the judged turn refuse each other's
    required wording, so the question moved rather than the demand: `just review-plan`
    reads whether the criteria answer it, by meaning.

    Driven as an acceptance because an acceptance is where a regression hides — a matcher
    quietly put back here refuses a plan whose only correction is a wording the review
    would then refuse.
    """
    silent = "\n".join(line for line in STATES_ITS_BAR.splitlines() if "claim" not in line)

    accepted = _check_plan(_plan(tmp_path, silent))

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


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


PERSONA_BY_PATH = "../personas/orchestrator.yaml"


def test_a_persona_named_by_path_resolves_through_the_recipe(tmp_path: Path) -> None:
    """The one resolution besides a shipped name that a real project can carry.

    A `persona` naming a path relative to `graphs/` is the only shape in `personas/` a
    dispatch reads, and it must neither make the checks fall over nor stop them
    refusing: a plan that omits a demand is still refused under it, and which source
    the demand came from is what the shipped-name journey above asserts.

    The third resolution — a node with **no** persona at all, left with the base
    config's generic contract — has no journey because no plan can reach it: the
    engine's own loader refuses an agent node that names none, which the field journey
    below drives. `tests/test_criteria_guard.py` is what covers that resolution, and
    the reason it is worth keeping there is that this repository does not own the rule
    that makes it unreachable.

    The refusing half is driven over a criterion this tier still refuses on its own
    account, rather than over one its bar decides: the demand matching that used to
    answer here is the judged turn's now, and a refusal about the criterion alone would
    say nothing about which persona resolved. What it shows instead is that a path
    persona neither makes the checks fall over nor stops them refusing — which is the
    whole of what a plan carrying this shape needs from them.
    """
    accepted = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), persona=PERSONA_BY_PATH))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout

    outside = f"{STATES_ITS_BAR}\n{RELEASED_ELSEWHERE[0]}"
    refused = _check_plan(_with_node(_plan(tmp_path, outside), persona=PERSONA_BY_PATH))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "the dispatch cannot do" in refused.stderr, refused.stderr


@pytest.mark.parametrize(
    ("fields", "reason", "source"),
    (
        pytest.param(
            {"persona": "../../elsewhere.yaml"},
            "outside this checkout",
            "scripts/plan-check.sh",
            id="escaped",
        ),
        pytest.param({"persona": 7}, "persona: invalid type", "engine", id="persona-not-a-name"),
        pytest.param({"persona": None}, "needs a persona", "engine", id="no-persona"),
        pytest.param({"task": None}, "needs task prose", "engine", id="no-task"),
        pytest.param({"kind": "review"}, "unknown variant `review`", "engine", id="unknown-kind"),
        pytest.param({"steps": 3}, "steps: invalid type", "engine", id="steps-not-a-list"),
        pytest.param(
            {"steps": ["one"]}, "steps[0]: invalid type", "engine", id="step-not-an-object"
        ),
    ),
)
def test_a_node_whose_own_fields_are_wrong_is_refused_by_the_field(
    tmp_path: Path, fields: dict[str, object], reason: str, source: str
) -> None:
    """A plan is a document some other tool wrote, so its shape is untrusted here too.

    Each of these reaches the operator as a diagnostic naming the field, rather than as
    whatever exception reaching into the wrong shape happened to produce. **Which
    loader says so is asserted beside the reason**, because that is the whole of what
    registering these checks with `onepipeline plan check` bought: every shape below but
    the first is the engine's own to refuse now, so a refusal here is the refusal a
    launch would make rather than one this repository reconstructed — and the
    reconstruction is what passed three of these and false-refused twice.
    """
    refused = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), **fields))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert reason in refused.stderr, refused.stderr
    assert f"check-plan: {source}: " in refused.stderr, refused.stderr


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


def test_a_persona_that_replaces_the_shared_bar_is_read_as_the_bar_in_force(
    tmp_path: Path, drafted_persona: Drafted
) -> None:
    """A file declaring `user.done_when_replaces_base` is composed and read through the recipe.

    What a real project can show is the *source* a refusal names: the clause comes from
    this drafted file, reached as a path relative to `graphs/`, so the bar in force is
    that file's own rather than anything in the base config. Whether the shared clause
    was dropped or merged is no longer observable at the recipe — the only reader of a
    composed bar left here is the file-modification conflict, and the base config forbids
    no change — so `tests/test_criteria_guard.py::`
    `test_a_role_that_replaces_the_base_bar_stands_in_for_it` is what isolates the
    replacement itself, over a base config it can write.
    """
    drafted_persona.file.write_text(
        "name: standalone\nuser:\n  persona: |\n    Only mine.\n"
        "  done_when: 'the answer is given and no project files were changed'\n"
        "  done_when_replaces_base: true\n",
        encoding="utf-8",
    )
    editing = f"{STATES_ITS_BAR}\n- `docs/routes.md` gains the route's own section."

    refused = _check_plan(_with_node(_plan(tmp_path, editing), persona=drafted_persona.ref))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "scratch/personas" in refused.stderr, refused.stderr
    assert "no project files were changed" in refused.stderr, refused.stderr
    assert "docs/routes.md" in refused.stderr, refused.stderr


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
    # And where it sends the party that has to act, which is the planner rather than
    # whoever is reading this: the variable every `just plan` launch hands its dispatch,
    # and an absolute path on this host. It named a path relative to this checkout, which
    # a planner working in another repository's worktree cannot open at all.
    assert f"${APPENDIX_ENV}" in refused.stderr, refused.stderr
    assert str(REPO_ROOT / APPENDIX) in refused.stderr, refused.stderr


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
    #: What the loader's refusal says about it, naming the field and the value.
    reason: str
    #: How this case is named in the parametrization.
    named: str


#: Both refusals a real qualified project can reach: a store record's `onepipeline.`
#: metadata is arbitrary JSON — nothing between the plan's author and the loader
#: narrows it — so a `steps` that is not a list, and one whose entries are not
#: objects, are the two shapes that arrive malformed.
MALFORMED_STEPS = (
    Malformed(3, "steps: invalid type: integer `3`", "steps-not-a-list"),
    Malformed(["one"], 'steps[0]: invalid type: string "one"', "step-not-an-object"),
)


@pytest.mark.parametrize("malformed", MALFORMED_STEPS, ids=lambda item: item.named)
def test_a_malformed_step_is_refused_against_the_plan_input_the_recipe_reads(
    tmp_path: Path, malformed: Malformed
) -> None:
    """The refusal names the node and the field, and says which loader made it.

    A stepped node reaches the engine's own loader as `onepipeline.steps` metadata, so
    what refuses a malformed one is the loader a launch runs — and the refusal names
    where the value came from, which is the reserved metadata key on the task record its
    author edits.
    """
    refused = _check_plan(_with_node(_plan(tmp_path, STATES_ITS_BAR), steps=malformed.steps))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    reported = refused.stderr
    assert malformed.reason in reported, reported
    assert "check-plan: engine: " in reported, reported
    assert "route" in reported, reported
    assert "`onepipeline.<field>` metadata keys on its task" in reported, reported


#: The engine's own loader is what decides a plan's structure now, and the accepted
#: line says so. An operator reading "accepted" has to know which loader read the plan,
#: because the direct path leaves every structural refusal for the launch to make.
THROUGH_THE_ENGINE = "read through `onepipeline plan check`"


def _task_document(project: str) -> Path:
    """The stored record of this plan's one dispatched node, for an edit no plan states.

    Two of the three structural errors below cannot be written as plan JSON at all —
    `orchestrator/project_store.py` renders a node's repository into exactly one of the
    two places, and turns `deps` into store dependency edges — so the shape that
    actually reached a launch is reached the way it arose, by editing the record.
    """
    source, _ = plan_store.qualified(project)
    (task,) = [one for one in plan_store.read_tasks(project) if one.node_id == "route"]
    _, _, native = task.qualified_id.partition(":")
    return plan_store.task_document(source, native)


def _with_metadata(project: str, entry: str) -> str:
    """``project`` with one more `onepipeline.` metadata entry on its dispatched node."""
    document = _task_document(project)
    written = document.read_text(encoding="utf-8")
    anchor = '  "onepipeline.id": "route"'
    assert anchor in written, written
    document.write_text(written.replace(anchor, f"{anchor}\n{entry}", 1), encoding="utf-8")
    return project


class Structural(NamedTuple):
    """One structural error that reached a launch, and what the refusal must name."""

    #: How the stored record is made to carry it.
    entry: str
    #: The field the refusal names.
    field: str
    #: A phrase of the loader's own reason, so the refusal is this error's and not
    #: some other one the same edit happens to produce.
    reason: str


#: The three that were reported sound by this repository's own re-implementation of the
#: loader and then refused by the launch — five wasted attempts on one plan. Each is now
#: the engine's to refuse, which is the whole of why they are driven here: a refusal
#: this recipe makes is a refusal the launch makes, by construction.
STRUCTURAL = (
    Structural(
        '  "onepipeline.repo": "https://github.com/nickderobertis/some-service"',
        "repo",
        "names a repository in both",
    ),
    Structural('  "onepipeline.deps": ["approve"]', "deps", "cross-DAG"),
)


@pytest.mark.parametrize("structural", STRUCTURAL, ids=lambda item: item.field)
def test_a_structural_error_that_reached_a_launch_is_refused_by_the_recipe(
    tmp_path: Path, structural: Structural
) -> None:
    """A shape the launch refuses is refused here, naming the node and the field."""
    project = _with_metadata(project_from_plan(_plan(tmp_path, STATES_ITS_BAR)), structural.entry)

    refused = _check_project(project)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "check-plan: engine: " in refused.stderr, refused.stderr
    assert "route" in refused.stderr, refused.stderr
    assert f": {structural.field}: " in refused.stderr, refused.stderr
    assert structural.reason in refused.stderr, refused.stderr


def test_a_stepped_node_that_also_carries_a_task_is_refused(tmp_path: Path) -> None:
    """The third of the three, which a plan can state directly.

    A node with `steps` takes its persona and its prose from them, so one carrying a
    `task` as well states two answers to the same question — and which one a dispatch
    would be judged against is exactly what nobody can say.
    """
    plan = _plan(tmp_path, STATES_ITS_BAR)
    document = json.loads(plan.read_text(encoding="utf-8"))
    document["tasks"][0]["steps"] = [
        {"id": "build", "persona": "engineer", "task": _task(STATES_ITS_BAR)}
    ]
    plan.write_text(json.dumps(document), encoding="utf-8")

    refused = _check_plan(plan)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "check-plan: engine: " in refused.stderr, refused.stderr
    # The loader names whichever of the three fields a stepped node may not restate it
    # reached first; which one that is belongs to the engine, and the node and the
    # reason are what the plan's author acts on.
    assert re.search(r"check-plan: engine: route: (task|persona|max_turns): ", refused.stderr), (
        refused.stderr
    )
    assert "takes its persona, task, and turn budget from them" in refused.stderr, refused.stderr


#: Seventeen backticks: eight pairs and one stray, spread over criteria their author
#: wrote apart. It is the block that was refused for "naming a shell invocation",
#: quoting a span that began in one criterion and ended at the word `git` in "real git
#: repositories" three criteria later.
UNBALANCED_BACKTICKS = (
    "- `orchestrator/plan_check.py` answers on `stdout` and exits `0` whether or not it "
    "refused.\n"
    "- The refusal names the `node`, the `field`, and the `reason` its loader gave.\n"
    "- A plan carrying `onepipeline.deps for an in-plan edge is refused.\n"
    "- The journeys drive real `git` repositories rather than fixtures of them.\n"
    "- `scripts/plan-check.sh` reads the plan from standard input.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as "
    "it finally stands.\n"
    "- The behavior is proven end to end by a journey driving the real recipe."
)


def test_a_criteria_block_with_an_unbalanced_backtick_run_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """The imbalance is named, and the quote stays inside the criterion that has it.

    Seventeen backticks is an odd number, so inline-code pairing runs on past the
    criterion the stray one is in and every later pattern reads a span its author never
    wrote. Refusing that by name is what turns an unactionable refusal about a shell
    invocation into one sentence naming the stray backtick.
    """
    assert UNBALANCED_BACKTICKS.count("`") == 17, "this fixture no longer holds the imbalance"

    refused = _check_plan(_plan(tmp_path, UNBALANCED_BACKTICKS))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "backtick run unclosed" in refused.stderr, refused.stderr
    # The quote is the one criterion that carries the stray backtick, and nothing from
    # the criteria on either side of it.
    assert "onepipeline.deps for an in-plan edge" in refused.stderr, refused.stderr
    assert "real `git` repositories" not in refused.stderr, refused.stderr
    assert "answers on stdout" not in refused.stderr, refused.stderr
    assert "shell invocation" not in refused.stderr, refused.stderr


#: This repository's own plan-reading recipes, named as the nouns a criterion about the
#: plan tooling has to be able to name. The node whose job is to change what
#: `just check-plan` refuses cannot state its criteria without naming it, and none of
#: these is a check a worker runs over its own change.
NAMES_THE_PLAN_RECIPES = (
    "- `just check-plan` refuses a plan whose node names its repository twice.\n"
    "- `just review-plan` records a pass and never a refusal.\n"
    "- `just orchestrate` launches the plan it is given without reading it again.\n"
    "- `just plans` lists the project the plan was written into.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as "
    "it finally stands.\n"
    "- The behavior is proven end to end by a journey driving the real recipe."
)


def test_a_criterion_naming_this_repositorys_plan_recipes_is_accepted(tmp_path: Path) -> None:
    """The exemption, and its bound: `just gate` in the same block is still refused.

    Both halves in one journey, because the exemption is only sound while the refusal
    it makes room for still fires — an exemption that swallowed every `just` invocation
    would be the hole rather than the fix.
    """
    accepted = _check_plan(_plan(tmp_path, NAMES_THE_PLAN_RECIPES))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout

    refused = _check_plan(_plan(tmp_path, f"{NAMES_THE_PLAN_RECIPES}\n- `just gate` is green."))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "`just` invocation" in refused.stderr, refused.stderr


def test_the_recipe_says_the_engines_own_loader_read_the_plan(tmp_path: Path) -> None:
    """An accepted plan names which loader read it, because they accept different amounts."""
    accepted = _check_plan(_plan(tmp_path, STATES_ITS_BAR))

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert THROUGH_THE_ENGINE in accepted.stdout, accepted.stdout
    assert "scripts/plan-check.sh" in accepted.stdout, accepted.stdout


def _older_engine(root: Path) -> Path:
    """An engine of the shape this recipe's direct path exists for.

    A real binary refusing `plan check` the way a release before that verb does, rather
    than a flag on the recipe: the path is chosen by asking the engine what it carries,
    so the only honest way to drive the other answer is to give it an engine that
    answers differently. Kept outside `PATH` deliberately — the roles a node's persona
    resolves to are still read out of the installed binary, so shadowing that would
    change what is being compared.
    """
    directory = root / "older-engine"
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "onepipeline"
    # llmlint: ignore[e2e_not_mocked] An engine carrying no `plan check` is not an
    # artifact this repository can install — it is a release older than the verb — so
    # the substitute is the absence of that one verb and nothing else. Everything the
    # journey then drives is real: the recipe, the wrapper, this repository's own
    # checks, the review bar lifted out of the *installed* engine, and the store.
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print(\"error: unrecognized subcommand 'check'\", file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


@pytest.mark.parametrize(
    ("criteria", "code"),
    (
        pytest.param(STATES_ITS_BAR, 0, id="accepted"),
        pytest.param(RESTS_ON_A_RELEASE, 1, id="refused"),
    ),
)
def test_both_paths_reach_the_same_verdict_on_one_plan(
    tmp_path: Path, criteria: str, code: int
) -> None:
    """The recipe works against an engine with no `plan check`, and agrees with itself.

    The two paths refuse different amounts — only the engine's own loader makes the
    structural refusals — but over a plan whose structure is sound they are the same
    checks over the same nodes, so they must reach the same verdict. What differs is
    the sentence naming which one read it, because an operator on the narrower path is
    owed the knowledge that a launch may still refuse this plan's structure.
    """
    project = project_from_plan(_plan(tmp_path, criteria))
    direct = os.environ | {"ORCHESTRATOR_PLAN_CHECK_ENGINE": str(_older_engine(tmp_path))}

    through = _check_project(project)
    directly = _check_project(project, environment=direct)

    assert through.returncode == code, through.stdout + through.stderr
    assert directly.returncode == code, directly.stdout + directly.stderr
    if code == 0:
        assert THROUGH_THE_ENGINE in through.stdout, through.stdout
        assert "carries no `plan check`" in directly.stdout, directly.stdout
        assert "1 dispatched node(s)" in through.stdout, through.stdout
        assert "1 dispatched node(s)" in directly.stdout, directly.stdout
    else:
        assert "the dispatch cannot do" in through.stderr, through.stderr
        assert "the dispatch cannot do" in directly.stderr, directly.stderr
        # Through the verb the refusal names the check that made it; directly there is
        # only one loader, so there is no source to name.
        assert "check-plan: scripts/plan-check.sh: route: task: " in through.stderr, through.stderr
        assert "check-plan: route: " in directly.stderr, directly.stderr


def _loaded_plan(project: str, root: Path) -> str:
    """The plan document the engine's own `plan check` hands a registered check.

    Captured from the verb rather than rebuilt here, so what the script below is driven
    with is the document it actually receives — including the store's whole `metadata`
    map, which is where this repository's review record travels.
    """
    recorder = root / "record-check.sh"
    captured = root / "captured.json"
    recorder.write_text(
        f'#!/usr/bin/env sh\ncat > "{captured}"\necho \'{{"refusals": []}}\'\n',
        encoding="utf-8",
    )
    recorder.chmod(0o755)
    read = subprocess.run(
        ["uv", "run", "onepipeline", "plan", "check", project, "--check", str(recorder)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert captured.is_file(), read.stdout + read.stderr
    return captured.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("criteria", "refuses"),
    (
        pytest.param(STATES_ITS_BAR, False, id="accepting"),
        pytest.param(RESTS_ON_A_RELEASE, True, id="refusing"),
    ),
)
def test_the_registered_check_answers_a_plan_document_on_its_own_stdin(
    tmp_path: Path, criteria: str, refuses: bool
) -> None:
    """The contract the verb reads this repository's checks through, driven directly.

    Exit 0 either way is the half worth driving: a non-zero exit means the check could
    not be run, and the verb reports that separately rather than as an accept — so a
    refusal that exited non-zero would be read as a broken check and a plan nobody
    judged.
    """
    project = reviewed(project_from_plan(_plan(tmp_path, criteria)))

    answered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "plan-check.sh")],
        cwd=REPO_ROOT,
        input=_loaded_plan(project, tmp_path),
        env=os.environ | {"ORCHESTRATOR_PLAN_CHECK_PROJECT": project},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert answered.returncode == 0, answered.stdout + answered.stderr
    answer = json.loads(answered.stdout)
    if not refuses:
        assert answer == {"refusals": []}, answer
    else:
        (refusal,) = answer["refusals"]
        assert refusal["node"] == "route", refusal
        assert refusal["field"] == "task", refusal
        assert "the dispatch cannot do" in refusal["reason"], refusal


def test_the_registered_check_does_not_import_from_an_inherited_module_path(
    tmp_path: Path,
) -> None:
    """A `PYTHONPATH` the verb was spawned with may not reach this check's interpreter.

    The verb spawns a registered check with the environment it was handed, so that
    variable is whatever ran `just check-plan` — or whatever ran the thing that ran it.
    Every entry on it is a directory the interpreter imports from *before* the standard
    library, so a `json.py` left there answers this check's own reads, and an empty entry
    means the working directory the verb was run in. Prepending this checkout's root does
    not help: the root only wins for names it actually holds.

    Driven with a hostile entry that would stop the interpreter outright, because a
    subtler one would be indistinguishable from the check working.
    """
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "json.py").write_text(
        "raise SystemExit('an inherited module path answered this check')\n", encoding="utf-8"
    )
    project = reviewed(project_from_plan(_plan(tmp_path, STATES_ITS_BAR)))

    answered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "plan-check.sh")],
        cwd=REPO_ROOT,
        input=_loaded_plan(project, tmp_path),
        env=os.environ
        | {
            "ORCHESTRATOR_PLAN_CHECK_PROJECT": project,
            "PYTHONPATH": f"{hostile}:",
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert answered.returncode == 0, answered.stdout + answered.stderr
    assert json.loads(answered.stdout) == {"refusals": []}, answered.stdout


def _unreadable_engine(root: Path) -> Path:
    """An engine whose `plan check` answers something no reader could act on.

    The verb's answer is another program's output, and a build that changed its shape —
    or a wrapper between the two that wrote to the same stream — would otherwise reach
    an operator as a plan silently accepted. This is what that looks like from outside.
    """
    directory = root / "unreadable-engine"
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "onepipeline"
    # llmlint: ignore[e2e_not_mocked] The engine this checkout installs answers exactly
    # one way, so an unreadable answer is a shape no real artifact here can produce.
    # Only that one answer is substituted; the recipe and the wrapper reading it are real.
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:] == ['plan', 'check', '--help']:\n"
        "    raise SystemExit(0)\n"
        "print('not a json answer')\n"
        "print('onepipeline: something went wrong', file=sys.stderr)\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def test_an_engine_answering_nothing_readable_is_not_reported_as_an_accepted_plan(
    tmp_path: Path,
) -> None:
    """Exit 2 and say so: an answer nobody can read is not a plan that passed."""
    project = project_from_plan(_plan(tmp_path, STATES_ITS_BAR))

    refused = _check_project(
        project,
        environment=os.environ
        | {"ORCHESTRATOR_PLAN_CHECK_ENGINE": str(_unreadable_engine(tmp_path))},
    )

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "answered nothing this command could read" in refused.stderr, refused.stderr
    assert "onepipeline: something went wrong" in refused.stderr, refused.stderr
    assert "dispatched node(s)" not in refused.stdout, refused.stdout


def test_the_registered_check_reports_a_plan_it_cannot_read_as_unrunnable(tmp_path: Path) -> None:
    """A check that answers nothing usable exits non-zero, and the verb says so.

    Both halves in one journey: the script's own refusal, and what the verb does with a
    check that exits non-zero — reported as a check that could not be run rather than as
    a plan that passed, which is the one reading that would let unreviewed criteria
    through.
    """
    answered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "plan-check.sh")],
        cwd=REPO_ROOT,
        input="{not json",
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert answered.returncode == 2, answered.stdout + answered.stderr
    assert "not valid JSON" in answered.stderr, answered.stderr
    assert answered.stdout == "", answered.stdout

    refusing = tmp_path / "refusing-check.sh"
    refusing.write_text("#!/usr/bin/env sh\ncat > /dev/null\necho boom >&2\nexit 3\n", "utf-8")
    refusing.chmod(0o755)
    project = reviewed(project_from_plan(_plan(tmp_path, STATES_ITS_BAR)))

    read = subprocess.run(
        ["uv", "run", "onepipeline", "plan", "check", project, "--check", str(refusing), "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert read.returncode == 2, read.stdout + read.stderr
    answer = json.loads(read.stdout)
    assert answer["accepted"] is False, answer
    (unrunnable,) = answer["unrunnable"]
    assert unrunnable["exit_code"] == 3, unrunnable
    assert "boom" in unrunnable["stderr"], unrunnable


def test_an_engine_named_but_not_runnable_is_refused_rather_than_read_as_a_refusal(
    tmp_path: Path,
) -> None:
    """A value naming nothing runnable must not read as an engine that lacks the verb.

    The two exits are the same from outside a spawn, and reading one as the other takes
    the narrower direct path over a plan the operator meant to have checked whole — so
    the recipe says which it was and judges nothing.
    """
    project = project_from_plan(_plan(tmp_path, STATES_ITS_BAR))

    refused = _check_project(
        project,
        environment=os.environ
        | {"ORCHESTRATOR_PLAN_CHECK_ENGINE": str(tmp_path / "no-such-engine")},
    )

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "ORCHESTRATOR_PLAN_CHECK_ENGINE" in refused.stderr, refused.stderr
    assert "not an executable" in refused.stderr, refused.stderr
    assert "dispatched node(s)" not in refused.stdout, refused.stdout


@pytest.mark.parametrize(
    "interpreter",
    (
        pytest.param("/nowhere/python3", id="path-to-nothing"),
        # A directory carries the execute bit for traversal, so a permission check alone
        # calls one an interpreter and the failure then arrives from `exec`.
        pytest.param("/tmp", id="path-to-a-directory"),
        pytest.param("no-such-interpreter", id="bare-name-nothing-has"),
    ),
)
def test_the_registered_check_refuses_an_interpreter_it_cannot_run(interpreter: str) -> None:
    """The script says which interpreter it could not run and what provisions one.

    The check is spawned by the engine with no shell around it, so a failure here
    reaches an operator as whatever the adapter says — and `exec: not found` names
    neither the value that was wrong nor the command that fixes it.
    """
    refused = subprocess.run(
        [str(REPO_ROOT / "scripts" / "plan-check.sh")],
        cwd=REPO_ROOT,
        input='{"tasks": []}',
        env=os.environ | {"ORCHESTRATOR_PYTHON": interpreter},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert interpreter in refused.stderr, refused.stderr
    assert "not an executable interpreter" in refused.stderr, refused.stderr
    assert "just bootstrap" in refused.stderr, refused.stderr
    assert refused.stdout == "", refused.stdout


def test_a_plan_the_engine_accepts_and_this_reader_cannot_says_so_rather_than_refusing(
    tmp_path: Path,
) -> None:
    """An accepted plan stays accepted when only the count is out of reach.

    The count is re-read from the store rather than reported back by the registered
    check, because the only channel a spawned check has to its wrapper is a file and a
    file whose path that wrapper hands over in the environment is a write any symlink on
    the way to it can redirect. That read can fail where the engine's did not, and a
    task carrying two repositories is a real project where it does — the engine takes
    the first and accepts, `orchestrator/plan_store.py` refuses the record. Turning that
    into a refusal would be this repository's reader deciding a plan's structure again,
    which is exactly what registering the check retired.
    """
    # Reviewed while the record is still one this repository can read, then retargeted:
    # a task's repositories are outside the review key by design, so the record stands.
    project = reviewed(project_from_plan(_plan(tmp_path, STATES_ITS_BAR)))
    document = _task_document(project)
    written = document.read_text(encoding="utf-8")
    named = '["github.com/nickderobertis/some-service"]'
    assert named in written, written
    document.write_text(
        written.replace(named, '["github.com/nickderobertis/some-service", "github.com/x/y"]'),
        encoding="utf-8",
    )

    accepted = subprocess.run(
        ["just", "check-plan", project],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "how many there are is unknown here" in accepted.stdout, accepted.stdout
    assert "more than one repository" in accepted.stdout, accepted.stdout
    assert "carries a review record" in accepted.stdout, accepted.stdout
    assert "dispatched node(s)" not in accepted.stdout, accepted.stdout


def test_the_registered_check_run_with_no_project_names_the_command_shape(
    tmp_path: Path,
) -> None:
    """A check driven by hand still says what to run, without inventing a project id.

    The project reaches this file only through the wrapper's environment, so a check
    run any other way has none — and a refusal about a missing review record whose
    remedy named nothing would send its reader to a command that cannot work.
    """
    project = project_from_plan(_plan(tmp_path, STATES_ITS_BAR))
    environment = {
        key: value for key, value in os.environ.items() if key != "ORCHESTRATOR_PLAN_CHECK_PROJECT"
    }

    answered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "plan-check.sh")],
        cwd=REPO_ROOT,
        input=_loaded_plan(project, tmp_path),
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert answered.returncode == 0, answered.stdout + answered.stderr
    refusals = json.loads(answered.stdout)["refusals"]
    assert {one["node"] for one in refusals} == {"route", "approve"}, refusals
    for refusal in refusals:
        assert refusal["field"] == "metadata", refusal
        assert "just review-plan <source>:<project>" in refusal["reason"], refusal


#: A `commit-msg` hook of the shape a repository this host publishes to really carries:
#: it reads the subject and nothing else, because `onevcs` runs one against a subject it
#: is about to publish where no index, diff or branch exists. This one refuses a type its
#: repository cuts no release from, which is the rule that cost a node here.
COMMIT_MSG_HOOK = """#!/usr/bin/env bash
set -euo pipefail
subject=$(head -n 1 "$1")
case "$subject" in
    feat:*|fix:*|perf:*) exit 0 ;;
esac
echo "commit-msg: this repository does not release from '${subject%%:*}:'" >&2
exit 1
"""

#: A destination this host has never registered, which is what most plans checked here
#: name. Nothing about its title or its release targets is knowable, so nothing about it
#: is refused — the property that keeps every plan launching today launching.
UNRESOLVABLE = "https://github.com/nickderobertis/some-service"


def _hooked(checkout: Path) -> Path:
    """Install a real `commit-msg` hook in a real checkout, the way this repository does.

    Through `core.hooksPath` and a tracked directory rather than `.git/hooks`, because
    that is the arrangement a publication inherits: the disposable clone a publication
    works in is given the lender's hooks path when it is cut.
    """
    hooks = checkout / ".githooks"
    hooks.mkdir()
    hook = hooks / "commit-msg"
    hook.write_text(COMMIT_MSG_HOOK, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "core.hooksPath", ".githooks"],
        check=True,
        timeout=e2e_timeout(30),
    )
    return checkout


def _publishing_plan(root: Path, *nodes: dict[str, object]) -> Path:
    """A plan whose nodes land in repositories, in the shape the loader accepts."""
    written = root / "publishing-plan.json"
    written.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Land the work"},
                "name": "publication-guard-e2e",
                "tasks": [
                    {"persona": "engineer", "task": _task(STATES_ITS_BAR), **node} for node in nodes
                ],
            }
        ),
        encoding="utf-8",
    )
    return written


# llmlint: ignore-block[shell_test_tiers_stay_split] `plan-tooling` **is** this
# repository's host-tool project: every journey in it already spawns the installed
# `onepipeline`, the `just` recipes, the registered check script and a real
# `oneharness run`, which is why it was cut out of the orchestrator project at all. Its
# `planToolingWorkspace` key covers what decides these answers — `uv.lock`, which pins
# the `onevcs` they ask, and `scripts/**` and `config/**`, which are the recipes and
# rules they drive — so a third project would split one cost across two edges rather
# than putting a host-tool cost behind its own.
def _in_registry(home: Path) -> dict[str, str]:
    """The environment a launch would make this check in, pointed at a scratch registry.

    Never this host's own: the identities a plan is checked against decide what this
    recipe refuses, and a journey pointed at the real registry would both depend on
    another repository's registration and answer differently the day somebody
    re-registers one.
    """
    return {**os.environ, "ONEVCS_HOME": str(home)}


def test_a_title_the_destinations_own_hook_refuses_is_refused_before_the_launch(
    tmp_path: Path,
) -> None:
    """The two refusals a whole dispatch used to be paid for, through the real recipe.

    A node's `title` becomes the subject `onevcs` publishes under and is never
    re-derived, so a hook that refuses it refuses the branch after the work is finished;
    one node here lost judge-passed work to a `refactor:` subject and its dependent was
    skipped. And a node consuming a release target on an identity that opens no change
    request is waiting on a publication that identity never makes.

    The hook is **run** rather than restated, which is what keeps this refusal and the
    destination's own rule from being two statements of one policy: nothing here knows
    which types that repository releases from.

    **The loader is what refuses it now, and the plan carries one defect rather than
    two.** These used to be one plan carrying both, on the ground that both were refused
    in one read by this repository's own registered check. The adopted engine's loader
    makes both refusals itself, and a loader refusal leaves no loaded plan to hand the
    registered checks — which then do not run at all. So the source is asserted as well
    as the sentence: a refusal to correct in the engine and one to correct here are
    corrected in different places, and a check that did not run is not one that passed.
    """
    (checkout,) = registered(tmp_path / "registry", ["service"])
    _hooked(checkout)
    plan = _publishing_plan(
        tmp_path,
        {"id": "landing", "title": "refactor: rename the reader", "repo": "service"},
    )

    refused = _check_project(
        project_from_plan(plan), environment=_in_registry(tmp_path / "registry" / "onevcs")
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "landing: title:" in refused.stderr, refused.stderr
    assert "refactor: rename the reader" in refused.stderr, refused.stderr
    # The hook's own words, relayed rather than restated: what this refuses is whatever
    # that repository's hook refuses, and nothing here knows which types it releases from.
    assert "does not release from 'refactor:'" in refused.stderr, refused.stderr
    assert "check-plan: engine:" in refused.stderr, refused.stderr
    assert "it did not run" in refused.stderr, refused.stderr


def test_a_node_consuming_a_release_its_identity_never_publishes_is_refused_at_the_loader(
    tmp_path: Path,
) -> None:
    """The other refusal a whole dispatch was paid for, and which side now makes it.

    A node consuming a release target on an identity that opens no change request is
    waiting on a publication that identity never makes — one ran an hour and thirty-six
    minutes before anything said so. This repository refused it before the engine did;
    the adopted engine's loader refuses it itself, which is strictly better, because a
    refusal the loader makes is one `onepipeline start` makes too rather than one a
    pre-launch read has to remember.

    Driven beside the title journey above rather than folded into it: each is refused on
    its own, and a plan carrying both would prove only that whichever the loader reached
    first still fires.
    """
    (checkout,) = registered(tmp_path / "registry", ["service"])
    _hooked(checkout)
    plan = _publishing_plan(
        tmp_path,
        {"id": "landing", "title": "feat: add the reader", "repo": "service"},
        {
            "id": "consumer",
            "title": "feat: adopt the release",
            "repo": "service",
            "deps": ["landing"],
            "consumes": {"landing": "pypi"},
        },
    )

    refused = _check_project(
        project_from_plan(plan), environment=_in_registry(tmp_path / "registry" / "onevcs")
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "consumer: consumes:" in refused.stderr, refused.stderr
    assert "local-direct" in refused.stderr, refused.stderr
    assert "opens no change request" in refused.stderr, refused.stderr
    # Which side refused it, and what that left undone. Both are what a reader of this
    # output has to be able to tell: an engine refusal and a registered check's refusal
    # are corrected in different places, and a check that did not run is not a check
    # that passed.
    assert "check-plan: engine:" in refused.stderr, refused.stderr
    assert "it did not run" in refused.stderr, refused.stderr


def test_a_stated_change_request_policy_lets_a_local_direct_node_consume_a_release(
    tmp_path: Path,
) -> None:
    """The node's own `merge_policy` decides what its `consumes` can hold.

    The shape this host's own observer-pacing adoption takes: a node of a `local-direct`
    repository that states `change-open` for itself, so its run opens the change request
    the release hold is a state of. The engine's loader reads the stated policy over the
    repository's; this repository's registered check read only the repository's and
    refused a plan the launch would have accepted — which stopped `just finish-plan` at
    its check step, before any design document could be written or approved.
    """
    (checkout,) = registered(
        tmp_path / "registry", ["service"], declarations={"service": _declaring(WHEEL)}
    )
    _hooked(checkout)
    plan = _publishing_plan(
        tmp_path,
        {"id": "landing", "title": "feat: add the reader", "repo": "service"},
        {
            "id": "consumer",
            "title": "feat: adopt the release",
            "repo": "service",
            "deps": ["landing"],
            "consumes": {"landing": "pypi"},
            "merge_policy": "change-open",
        },
    )

    accepted = _check_project(
        project_from_plan(plan), environment=_in_registry(tmp_path / "registry" / "onevcs")
    )

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "consumes" not in accepted.stderr, accepted.stderr


def test_a_stated_local_direct_policy_refuses_consumes_whatever_the_repository_opens(
    tmp_path: Path,
) -> None:
    """The converse: a node that says it opens no change request, on a repository that does.

    `change-auto` opens one, so the repository's own answer would let this node through;
    the node's stated `local-direct` is what decides, whichever way it points. Refused
    through the same recipe, so what an operator is told is what is asserted — whichever
    side of the recipe makes the refusal, it names the stated policy and the field.
    """
    (checkout,) = registered(tmp_path / "registry", ["service"], publication="change-auto")
    _hooked(checkout)
    plan = _publishing_plan(
        tmp_path,
        {"id": "landing", "title": "feat: add the reader", "repo": "service"},
        {
            "id": "consumer",
            "title": "feat: adopt the release",
            "repo": "service",
            "deps": ["landing"],
            "consumes": {"landing": "pypi"},
            "merge_policy": "local-direct",
        },
    )

    refused = _check_project(
        project_from_plan(plan), environment=_in_registry(tmp_path / "registry" / "onevcs")
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "consumer: consumes:" in refused.stderr, refused.stderr
    assert "local-direct" in refused.stderr, refused.stderr


def test_a_plan_this_host_would_publish_is_not_refused_for_where_it_lands(
    tmp_path: Path,
) -> None:
    """Three passes in one read, which is the half that makes the refusals worth having.

    A title the destination's own hook accepts; the same refused title against a
    destination that declares no hook at all; and both of those against a destination
    this host has never registered. The last two are the same answer — this host cannot
    say — and refusing either would refuse plans that launch correctly today.
    """
    hooked, bare = registered(
        tmp_path / "registry", ["hooked", "bare"], declarations={"hooked": _declaring(WHEEL)}
    )
    _hooked(hooked)
    plan = _publishing_plan(
        tmp_path,
        {"id": "accepted", "title": "feat: add the reader", "repo": "hooked"},
        {"id": "unhooked", "title": "refactor: rename the reader", "repo": "bare"},
        {
            "id": "unregistered",
            "title": "refactor: rename it elsewhere",
            "repo": UNRESOLVABLE,
            "deps": ["accepted"],
            "consumes": {"accepted": "pypi"},
        },
    )

    accepted = _check_project(
        project_from_plan(plan), environment=_in_registry(tmp_path / "registry" / "onevcs")
    )

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "3 dispatched node(s)" in accepted.stdout, accepted.stdout


#: A hosted scratch identity, as `onevcs` files it: the value the record's own
#: `repositories` holds for it, and the alias `just repos` would list it under.
HOSTED_SERVICE = "github.com/scratchowner/service"
HOSTED_ALIAS = "service"


def _hosted_registry(root: Path) -> dict[str, str]:
    """A scratch registry holding one hosted identity, as the environment a check runs in.

    Seeded under a hosted origin rather than a path, because the question these journeys
    ask is which of the two `onevcs resolve` answers for an alias — and every identity
    `registered` seeds resolves to a path, which is the case the reserved key exists for.
    """
    identity = seeded(
        root, publication=HOSTED_ALIAS, execution=f"{HOSTED_ALIAS}-isolated", origin=HOSTED_SERVICE
    )
    return {**_in_registry(identity.home), **identity.environment}


def test_a_hosted_repository_named_on_the_reserved_key_is_refused_naming_the_origin(
    tmp_path: Path,
) -> None:
    """The record shape that filed every task issue of a plan in this repository.

    A node's repository belongs in the task record's own `repositories`, as one
    normalized origin; `onepipeline.repo` is reserved for an identity that list cannot
    hold. Every plan this host's planners wrote put a checkout alias on the key, so every
    record reached the board with `repositories` empty and every issue landed in the
    orchestrator's own repository. The refusal names the node, what it wrote, and the
    origin to write instead — and it is this repository's registered check that makes it,
    not the engine, because which of the two an alias names is a fact of this host's
    registry rather than of the plan's structure.
    """
    plan = _publishing_plan(
        tmp_path, {"id": "landing", "title": "feat: land the reader", "repo": HOSTED_ALIAS}
    )

    refused = _check_project(project_from_plan(plan), environment=_hosted_registry(tmp_path))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "check-plan: scripts/plan-check.sh: landing: repo:" in refused.stderr, refused.stderr
    assert f"'{HOSTED_ALIAS}'" in refused.stderr, refused.stderr
    assert f'repositories: ["{HOSTED_SERVICE}"]' in refused.stderr, refused.stderr
    assert "onepipeline.repo" in refused.stderr, refused.stderr


def test_the_same_node_rewritten_with_the_origin_in_repositories_is_accepted(
    tmp_path: Path,
) -> None:
    """The correction the refusal above asks for, driven to acceptance.

    The record renderer puts a `host/owner/name` `repo` into the record's `repositories`
    and writes no `onepipeline.repo`, and the engine's own loader reads the node's `repo`
    back out of that list — which is asserted from the plan document the verb hands this
    repository's check, so what is read is what the loader loaded rather than what the
    record says. A record naming the repository both ways is the engine's own refusal,
    which the structural journey above holds.
    """
    plan = _publishing_plan(
        tmp_path, {"id": "landing", "title": "feat: land the reader", "repo": HOSTED_SERVICE}
    )
    project = project_from_plan(plan)
    registry = _hosted_registry(tmp_path)

    accepted = _check_project(project, environment=registry)

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout
    (loaded,) = json.loads(_loaded_plan(project, tmp_path))["tasks"]
    assert loaded["repo"] == HOSTED_SERVICE, loaded
    assert "onepipeline.repo" not in loaded["metadata"], loaded


def test_a_local_identity_on_the_reserved_key_is_the_case_the_key_exists_for(
    tmp_path: Path,
) -> None:
    """An identity whose origin is a path is not a value `repositories` can hold.

    Every scratch identity this repository's own journeys register resolves this way, so
    a check that refused it would refuse every one of them — and a real local checkout
    `onevcs` knows by its absolute path has nowhere else to be named.
    """
    (checkout,) = registered(tmp_path / "registry", ["service"])
    plan = _publishing_plan(
        tmp_path, {"id": "landing", "title": "feat: land the reader", "repo": "service"}
    )

    accepted = _check_project(
        project_from_plan(plan), environment=_in_registry(tmp_path / "registry" / "onevcs")
    )

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout
    assert checkout.is_dir()


def test_a_value_this_host_cannot_resolve_earns_no_refusal_about_the_key(
    tmp_path: Path,
) -> None:
    """Nothing about an alias this registry does not hold is knowable, so nothing is refused.

    Written to miss rather than to over-refuse, for the reason the title check passes
    over a destination this host cannot see: a plan is checked against repositories this
    checkout may never have registered, and refusing one for that would refuse plans that
    launch correctly today.
    """
    plan = _publishing_plan(
        tmp_path, {"id": "landing", "title": "feat: land the reader", "repo": "nobody__service"}
    )

    accepted = _check_project(project_from_plan(plan), environment=_hosted_registry(tmp_path))

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "1 dispatched node(s)" in accepted.stdout, accepted.stdout


def test_both_paths_refuse_a_node_this_host_would_not_publish(tmp_path: Path) -> None:
    """The publication refusals reach an engine with no `plan check` too, and agree.

    The two paths refuse different amounts — only the engine's own loader makes the
    structural refusals — but these are this repository's own, so they are the same
    answer over the same plan whichever loader read it. What differs is how much of that
    answer each can print: the verb renders every refusal, and the direct path raises the
    first, which is why the assertion below is about the one they share rather than about
    the count.
    """
    (checkout,) = registered(tmp_path / "registry", ["service"])
    _hooked(checkout)
    plan = _publishing_plan(
        tmp_path, {"id": "landing", "title": "refactor: rename it", "repo": "service"}
    )
    project = project_from_plan(plan)
    registry = _in_registry(tmp_path / "registry" / "onevcs")

    through = _check_project(project, environment=registry)
    # llmlint: ignore-block[e2e_not_mocked] The direct path is reached only against an
    # engine carrying no `plan check`, which is a release older than that verb and so not
    # an artifact this repository can install; `_older_engine` is the absence of that one
    # verb and nothing else, for the reason stated where it is built. Everything else here
    # is real — the recipe, the registered identity, and the destination's own hook.
    directly = _check_project(
        project,
        environment=registry | {"ORCHESTRATOR_PLAN_CHECK_ENGINE": str(_older_engine(tmp_path))},
    )
    # llmlint: ignore-end[e2e_not_mocked]

    assert through.returncode == 1, through.stdout + through.stderr
    assert directly.returncode == 1, directly.stdout + directly.stderr
    for refused in (through, directly):
        assert "does not release from 'refactor:'" in refused.stderr, refused.stderr
        assert "landing" in refused.stderr, refused.stderr


def test_a_node_consuming_a_release_its_identity_really_publishes_is_not_refused(
    tmp_path: Path,
) -> None:
    """The other side of the workflow question, against a real resolved policy.

    `change-auto` opens a change request, so a node awaiting a release target on it is
    waiting on a publication that identity really makes. The workflow is taken from
    `onevcs rules check` rather than from `onevcs resolve`'s own `workflow` field, and
    this registry is what proves the difference matters: the same identity registers as
    `local` and resolves `change-auto`, so a reader of the wrong one would refuse this.
    """
    (checkout,) = registered(
        tmp_path / "registry",
        ["service"],
        publication="change-auto",
        declarations={"service": _declaring(WHEEL)},
    )
    _hooked(checkout)
    plan = _publishing_plan(
        tmp_path,
        {"id": "landing", "title": "feat: land the library", "repo": "service"},
        {
            "id": "consumer",
            "title": "feat: adopt the release",
            "repo": "service",
            "deps": ["landing"],
            "consumes": {"landing": "pypi"},
        },
    )

    accepted = _check_project(
        project_from_plan(plan), environment=_in_registry(tmp_path / "registry" / "onevcs")
    )

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "2 dispatched node(s)" in accepted.stdout, accepted.stdout


#: A wheel this host installs, as `host_installs` names it, and two artifacts it does
#: not: what a scratch producer declares so a node can wait on either kind.
WHEEL = ("pypi", "pypi:onepipeline-cli")
CRATE = ("crate", "crate:onepipeline")
NPM = ("npm", "npm:onepipeline-cli")

#: This host's own repository, as `onevcs` files it and as a node's `repo` names it. The
#: journeys about a node *of this repository* name it unregistered in the scratch
#: registry, which is fine: those rules compare origins and read the dependency's
#: targets, and every other rule passes over what it cannot resolve.
THIS_REPOSITORY = "github.com/nickderobertis/ai-orchestrator"


def _declaring(*declared: tuple[str, str]) -> str:
    """A `release-targets.toml` declaring each ``(name, id)`` target and nothing a probe runs."""
    rows = "".join(
        f'\n[[target]]\nid = "{artifact}"\nname = "{name}"\n'
        f'what = "The {name} target, as a scratch producer declares it."\n'
        f"published_by = \"Nothing: a journey's stand-in for a producer's declaration.\"\n"
        for name, artifact in declared
    )
    return f'schema_version = 3\nprobe = "scripts/release-probe.sh"\n{rows}'


def _releases(root: Path, **rules: dict[str, str]) -> str:
    """A `releases.yml` for the scratch registry under ``root``, one rule per checkout name.

    Matched by checkout path — `root / name`, which is where `registered` puts each
    identity's one checkout — because that is what `onevcs`'s `path` matcher reads, so
    two identities in one registry can sit on different rungs.
    """
    written = "".join(
        f'  - match: {{path: "{root / name}"}}\n'
        + "".join(f"    {key}: {value}\n" for key, value in rule.items())
        for name, rule in rules.items()
    )
    return f"version: 1\ndefault:\n  adoption: fast\nrepositories:\n{written}"


def _consumer(**fields: object) -> dict[str, object]:
    """A node landing in `service` behind the `library` producer, with ``fields`` on top."""
    return {
        "id": "consumer",
        "title": "feat: adopt the release",
        "repo": "service",
        "deps": ["producer"],
        **fields,
    }


PRODUCER = {"id": "producer", "title": "feat: release the library", "repo": "library"}


def _adoption_check(
    tmp_path: Path, *nodes: dict[str, object], environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Check a plan of ``nodes`` through the real recipe against the scratch registry."""
    return _check_project(
        project_from_plan(_publishing_plan(tmp_path, *nodes)),
        environment=environment or _in_registry(tmp_path / "registry" / "onevcs"),
    )


def test_a_consumed_target_the_producer_does_not_resolve_is_refused_listing_what_it_does(
    tmp_path: Path,
) -> None:
    """R2, through the real recipe: a wait on a target the producer does not release.

    The producer really declares a wheel, in its origin's first commit where `onevcs`
    reads a declaration from, so what the refusal lists is what the real verb resolved
    rather than what the plan claimed.
    """
    registered(
        tmp_path / "registry",
        ["library", "service"],
        publication="change-auto",
        declarations={"library": _declaring(WHEEL)},
    )

    refused = _adoption_check(tmp_path, PRODUCER, _consumer(consumes={"producer": "npm"}))
    accepted = _adoption_check(tmp_path, PRODUCER, _consumer(consumes={"producer": "pypi"}))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "check-plan: scripts/plan-check.sh: consumer: consumes:" in refused.stderr, (
        refused.stderr
    )
    assert "'npm'" in refused.stderr, refused.stderr
    assert "resolves: pypi" in refused.stderr, refused.stderr
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "2 dispatched node(s)" in accepted.stdout, accepted.stdout


def test_a_published_node_with_no_target_to_wait_on_is_refused_as_held_for_ever(
    tmp_path: Path,
) -> None:
    """R3: the hold nothing can end, with the node's rung read out of the real override.

    The node states no `adoption` of its own; `published` is what `onevcs` answers for
    its repository out of the `releases.yml` seeded into the scratch registry — so the
    rung the engine would hold it on is the one this refusal read.
    """
    root = tmp_path / "registry"
    registered(
        root,
        ["library", "service"],
        declarations={"library": _declaring(WHEEL)},
        releases=_releases(root, service={"adoption": "published"}),
    )

    refused = _adoption_check(tmp_path, PRODUCER, _consumer())
    accepted = _adoption_check(
        tmp_path,
        PRODUCER,
        _consumer(consumes={"producer": "pypi"}, merge_policy="change-open"),
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "check-plan: scripts/plan-check.sh: consumer: adoption:" in refused.stderr, (
        refused.stderr
    )
    assert "held for ever" in refused.stderr, refused.stderr
    assert "`consumes: {producer: <target>}`" in refused.stderr, refused.stderr
    assert "`default_target` in config/onevcs.releases.yml" in refused.stderr, refused.stderr
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_a_fast_node_behind_a_release_publishing_without_a_change_request_is_refused(
    tmp_path: Path,
) -> None:
    """R4: the node that fails at its last step after all of its work.

    `local-direct` is what the scratch rules resolve for every identity here, so the
    policy this refusal names is the one `onevcs rules check` answered; stating a
    change-request policy on the node is the correction the refusal names, and it is
    accepted.
    """
    registered(
        tmp_path / "registry", ["library", "service"], declarations={"library": _declaring(WHEEL)}
    )

    refused = _adoption_check(tmp_path, PRODUCER, _consumer())
    accepted = _adoption_check(tmp_path, PRODUCER, _consumer(merge_policy="change-open"))

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "check-plan: scripts/plan-check.sh: consumer: adoption:" in refused.stderr, (
        refused.stderr
    )
    assert "adopts `fast`" in refused.stderr, refused.stderr
    assert "'local-direct'" in refused.stderr, refused.stderr
    assert "`adoption: published`" in refused.stderr, refused.stderr
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_a_node_of_this_repository_resolving_a_crate_is_refused_naming_the_judged_tier(
    tmp_path: Path,
) -> None:
    """R5: a crate reaches nothing a dispatch here runs, and the pin question is the judge's.

    The scratch producer declares what the engine's real producer declares — a crate,
    the wheel `host_installs` names, and an npm launcher — so the artifact the refusal
    names is one the real verb read off a real declaration.
    """
    registered(
        tmp_path / "registry", ["library"], declarations={"library": _declaring(CRATE, WHEEL, NPM)}
    )
    consumer = _consumer(repo=THIS_REPOSITORY, adoption="published", merge_policy="change-open")

    refused = _adoption_check(tmp_path, PRODUCER, consumer | {"consumes": {"producer": "crate"}})

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "check-plan: scripts/plan-check.sh: consumer: consumes:" in refused.stderr, (
        refused.stderr
    )
    assert "`crate:onepipeline`" in refused.stderr, refused.stderr
    assert "engine wheel" in refused.stderr, refused.stderr
    assert "`just review-plan`'s question" in refused.stderr, refused.stderr
    assert host_installs.by_artifact(WHEEL[1]) is not None, "the pass case below names no wheel"


@pytest.mark.parametrize(
    "criteria",
    (
        pytest.param(STATES_ITS_BAR, id="silent about the pin"),
        pytest.param(
            f"{STATES_ITS_BAR}\n- `config/onepipeline.version` names the release this node adopts.",
            id="naming the pin",
        ),
    ),
)
def test_a_node_of_this_repository_adopting_a_wheel_is_accepted_whatever_its_criteria_say(
    tmp_path: Path, criteria: str
) -> None:
    """The correction R5 names, and the line this tier does not cross.

    Two fixtures differing only in whether the criteria name the `config/<pin>.version`
    the wheel governs, and both pass: no rule of this module reads a task's prose, so
    whether the pin named is the right one for the fix is `just review-plan`'s question
    and never this recipe's refusal.
    """
    registered(
        tmp_path / "registry", ["library"], declarations={"library": _declaring(CRATE, WHEEL, NPM)}
    )
    consumer = _consumer(
        repo=THIS_REPOSITORY,
        adoption="published",
        merge_policy="change-open",
        consumes={"producer": "pypi"},
        task=_task(criteria),
    )

    accepted = _adoption_check(tmp_path, PRODUCER, consumer)

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "2 dispatched node(s)" in accepted.stdout, accepted.stdout


def test_a_node_elsewhere_taking_the_hosts_default_target_is_told_what_to_write(
    tmp_path: Path,
) -> None:
    """R7: the shape every planner outside this repository meets first.

    The override gives the producer a `default_target`, exactly as this host's tracked
    one does for every producer it installs, and a `published` node of another
    repository naming no `consumes` would take it — this host's own wheel. The refusal
    says what to write and lists the producer's targets; writing it is accepted.
    """
    root = tmp_path / "registry"
    registered(
        root,
        ["library", "service"],
        publication="change-auto",
        declarations={"library": _declaring(WHEEL, CRATE)},
        releases=_releases(root, library={"default_target": "pypi"}),
    )

    refused = _adoption_check(tmp_path, PRODUCER, _consumer(adoption="published"))
    accepted = _adoption_check(
        tmp_path, PRODUCER, _consumer(adoption="published", consumes={"producer": "crate"})
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "check-plan: scripts/plan-check.sh: consumer: consumes:" in refused.stderr, (
        refused.stderr
    )
    assert "`default_target` ('pypi')" in refused.stderr, refused.stderr
    assert "`consumes: {producer: <target>}`" in refused.stderr, refused.stderr
    assert "resolves: pypi, crate" in refused.stderr, refused.stderr
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_a_plan_whose_repositories_the_registry_does_not_know_earns_no_adoption_refusal(
    tmp_path: Path,
) -> None:
    """Every rule passes over what this host cannot answer for.

    A node carrying every field the rules read — `published`, a `consumes` naming a
    target nobody resolves, a dependency elsewhere — against a registry holding neither
    repository. Nothing about any of it is knowable here, so nothing is refused.
    """
    registered(tmp_path / "registry", ["unrelated"])
    consumer = _consumer(repo="nobody__service", adoption="published", consumes={"producer": "npm"})

    accepted = _adoption_check(tmp_path, PRODUCER | {"repo": "nobody__library"}, consumer)

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "2 dispatched node(s)" in accepted.stdout, accepted.stdout


def test_the_two_paths_report_the_same_adoption_refusal(tmp_path: Path) -> None:
    """The direct path raises the first refusal the rules make; the verb collects them all.

    Two violations in one plan, one per node, so the two amounts differ: the through-engine
    path prints both, and the direct path prints exactly one — which has to be one of the
    two, naming the same node, field and rule. On a plan carrying one violation the two
    outputs name the same three things.
    """
    root = tmp_path / "registry"
    registered(
        root,
        ["library", "service"],
        declarations={"library": _declaring(WHEEL)},
        releases=_releases(root, service={"adoption": "published"}),
    )
    held = _consumer(id="held")
    drafted = _consumer(id="drafted", adoption="fast")
    two = project_from_plan(_publishing_plan(tmp_path, PRODUCER, held, drafted))
    (tmp_path / "one").mkdir()
    one = project_from_plan(_publishing_plan(tmp_path / "one", PRODUCER, held))
    registry = _in_registry(root / "onevcs")
    # llmlint: ignore-block[e2e_not_mocked] The direct path is reached only against an
    # engine carrying no `plan check`, for the reason `_older_engine` states.
    direct = registry | {"ORCHESTRATOR_PLAN_CHECK_ENGINE": str(_older_engine(tmp_path))}
    # llmlint: ignore-end[e2e_not_mocked]

    through = _check_project(two, environment=registry)
    directly = _check_project(two, environment=direct)
    through_one = _check_project(one, environment=registry)
    directly_one = _check_project(one, environment=direct)

    assert through.returncode == 1, through.stdout + through.stderr
    assert directly.returncode == 1, directly.stdout + directly.stderr
    collected = [
        line.removeprefix("check-plan: scripts/plan-check.sh: ")
        for line in through.stderr.splitlines()
        if line.startswith("check-plan: scripts/plan-check.sh: ")
    ]
    assert {line.split(": ")[0] for line in collected} == {"held", "drafted"}, collected
    (raised,) = [line for line in directly.stderr.splitlines() if line.startswith("check-plan: ")]
    assert raised.removeprefix("check-plan: ") in collected, (raised, collected)
    for reported in (through_one, directly_one):
        assert reported.returncode == 1, reported.stdout + reported.stderr
        assert "held: adoption: this node adopts `published`" in reported.stderr, reported.stderr


# llmlint: ignore-end[shell_test_tiers_stay_split]


#: Every way this host can have nothing to say about where a node publishes, as the
#: `onevcs` that answers each one. Three different silences, and each has to end the same
#: way: a verb that does not return, an answer this command cannot read, and a policy
#: outside the four published `merge_policy` names.
UNANSWERABLE = (
    ("a verb that never returns", "import time\n\ntime.sleep(30)\n"),
    ("an answer this host cannot read", "print('not the object this reads')\n"),
    (
        "a policy outside the published vocabulary",
        # The checkout it names is this stand-in's own directory, which declares no hook:
        # an unknown policy silences the release-target question and nothing else, so a
        # destination whose hook *could* be read would still be ruled on for its title.
        "import json, pathlib, sys\n"
        "resolved = {\n"
        "    'identity': 'local/service',\n"
        "    'publication_checkout': str(pathlib.Path(__file__).parent),\n"
        "}\n"
        "print(json.dumps(resolved) if sys.argv[1] == 'resolve' else 'publication: brand-new')\n",
    ),
)


@pytest.mark.parametrize("what,program", UNANSWERABLE, ids=lambda row: row)
def test_a_destination_this_host_cannot_answer_for_refuses_nothing(
    tmp_path: Path, what: str, program: str
) -> None:
    """A plan is checked against repositories this checkout may never have seen.

    Each shape here is this host having nothing to say, and refusing a plan for what it
    cannot see would refuse plans that launch correctly today — which is the failure this
    whole check has to avoid more than it has to catch. The node it is driven with has a
    title *and* a `consumes` that would both be refused if the destination could be read,
    so an empty answer is the silence rather than an accident of the fixture.

    Driven through `scripts/plan-check.sh` rather than through `just check-plan`, which is
    the seam these shapes are reachable at: the recipe reaches the engine through
    `uv run`, which puts this checkout's own `.venv/bin` at the front of the child's PATH,
    so a stand-in `onevcs` never answers and a journey written against the recipe would
    pass for the unregistered-repository reason instead of the one it names. This script
    is what `onepipeline plan check` spawns, given the document that verb really hands it.
    """
    # llmlint: ignore-block[e2e_not_mocked] `onevcs` is the published CLI this check
    # delegates its one question to, doubled at that boundary and nothing above it: a verb
    # that hangs, an unreadable answer and a policy name no release has published cannot
    # be produced by the real one on demand. The script, the module, the subprocess
    # boundary between them and the plan document the verb writes are all real.
    binary = tmp_path / "bin"
    binary.mkdir(parents=True)
    stand_in = binary / "onevcs"
    stand_in.write_text(f"#!{sys.executable}\n{program}", encoding="utf-8")
    stand_in.chmod(stand_in.stat().st_mode | stat.S_IXUSR)
    # llmlint: ignore-end[e2e_not_mocked]
    plan = _publishing_plan(
        tmp_path,
        {"id": "landing", "title": "feat: land it", "repo": "service"},
        {
            "id": "consumer",
            "title": "refactor: rename it",
            "repo": "service",
            "deps": ["landing"],
            "consumes": {"landing": "pypi"},
        },
    )
    project = reviewed(project_from_plan(plan))

    # llmlint: ignore-block[tests_mirror_real_usage] This script *is* the interface these
    # shapes are reachable through: `just check-plan` reaches the engine via `uv run`,
    # which puts this checkout's `.venv/bin` at the front of the child's PATH, so a
    # stand-in `onevcs` never answers it. What is spawned here is what `onepipeline plan
    # check` spawns, on the document that verb really hands it.
    answered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "plan-check.sh")],
        cwd=REPO_ROOT,
        input=_loaded_plan(project, tmp_path),
        env=os.environ | {"PATH": f"{binary}{os.pathsep}{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )

    assert answered.returncode == 0, answered.stdout + answered.stderr
    assert json.loads(answered.stdout)["refusals"] == [], f"{what} refused: {answered.stdout}"
    # llmlint: ignore-end[tests_mirror_real_usage]


#: Every way this host can have nothing to say about a release: no `onevcs` at all, one
#: that refuses the question, and one whose answer is not the shape the rules read.
#: ``None`` is a PATH holding the system's tools and the installed engine — which the
#: check needs, to read the review bar out of — and no `onevcs`, so the CLI's absence is
#: the one thing tested.
UNANSWERABLE_ADOPTION = (
    ("no onevcs on PATH", None),
    ("a verb that exits non-zero", "import sys\n\nsys.exit(3)\n"),
    ("an answer that is not the JSON shape the rules read", "print('adoption: published')\n"),
)


@pytest.mark.parametrize("what,program", UNANSWERABLE_ADOPTION, ids=lambda row: row)
def test_a_release_this_host_cannot_ask_about_refuses_nothing(
    tmp_path: Path, what: str, program: str | None
) -> None:
    """The adoption rules are written to miss wherever `onevcs` cannot answer.

    The node carries every field the rules read — `published`, a `consumes` naming a
    target nobody resolves, a dependency in another repository — so that an empty answer
    is the silence rather than an accident of the fixture. Driven through
    `scripts/plan-check.sh` for the reason the publication journey above gives: the
    recipe reaches the engine through `uv run`, which puts this checkout's `.venv/bin`
    ahead of anything a journey could put on PATH.
    """
    binary = tmp_path / "bin"
    binary.mkdir(parents=True)
    if program is not None:
        # llmlint: ignore-block[e2e_not_mocked] `onevcs` is the published CLI this check
        # delegates its questions to, doubled at that boundary and nothing above it: a
        # verb that refuses and an answer of another shape cannot be produced by the real
        # one on demand. The script, the module, the subprocess boundary between them and
        # the plan document the verb writes are all real.
        stand_in = binary / "onevcs"
        stand_in.write_text(f"#!{sys.executable}\n{program}", encoding="utf-8")
        stand_in.chmod(stand_in.stat().st_mode | stat.S_IXUSR)
        # llmlint: ignore-end[e2e_not_mocked]
        path = f"{binary}{os.pathsep}{os.environ['PATH']}"
    else:
        engine = shutil.which("onepipeline")
        assert engine is not None, "the installed engine is what the check reads the bar from"
        (binary / "onepipeline").symlink_to(engine)
        path = f"{binary}{os.pathsep}/usr/bin:/bin"
    plan = _publishing_plan(
        tmp_path,
        PRODUCER,
        _consumer(adoption="published", consumes={"producer": "npm"}, merge_policy="change-open"),
    )
    project = reviewed(project_from_plan(plan))

    # llmlint: ignore-block[tests_mirror_real_usage] This script *is* the interface these
    # shapes are reachable through, for the reason the publication journey above states.
    answered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "plan-check.sh")],
        cwd=REPO_ROOT,
        input=_loaded_plan(project, tmp_path),
        env=os.environ | {"PATH": path},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )

    assert answered.returncode == 0, answered.stdout + answered.stderr
    assert json.loads(answered.stdout)["refusals"] == [], f"{what} refused: {answered.stdout}"
    # llmlint: ignore-end[tests_mirror_real_usage]
