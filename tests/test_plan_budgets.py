"""A plan's budgets, read and held to their rules by `orchestrator/plan_budgets.py`.

Every plan here is written the way a planner writes one: its tasks into this test process's
own `test-fixtures` source, each owning task's `## Budgets` section rendered by the pinned
store from the partial `plan-task` includes and its `orchestrator.budgets` record set through
the store's `task metadata set`, and its plan-level answers rendered as its description by the
pinned engine's `template resolve plan-description` piped into the pinned store's `project
create`, beside the same answers as `orchestrator.plan-budgets`. The checks are then asked
through the store, so what is proven is what `just check-plan`, `just review-plan` and the
finish-plan flow read. `tests/plan_tooling/test_check_plan_budgets_e2e.py` drives the same
rules through the recipe; what is here is every rule and every shape the reader refuses, one
at a time.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] The
subject is `orchestrator/plan_budgets.py`, this tier's own module, and the boundary it reads
through is the plan store, so the store is what these tests drive rather than a stand-in for
it — the same choice `tests/test_design_doc_template.py` and `tests/test_design_approval.py`
make for the documents beside this one. Everything they read is in the key this tier is
memoized on: `templates/`, `config/budgets-migration.yaml` and the pinned installs `uv.lock`
names are all in `codeWorkspace`, and each store they write is this process's own fixture
root.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import plan_fixture_source
import pytest
import yaml
from project_fixtures import budget_section, budgeted, designed, no_budgets, owning
from published_tools import ONETASKGRAPH_BIN

from orchestrator import design_approval, plan_budgets, plan_store, task_body
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

_SEQUENCE = itertools.count()

# llmlint: ignore[suppressions_justified] A budgets record is the templates' open JSON answer
# shape, which these tests build and then break one key at a time — a value of the wrong type
# among them — so its values are `Any` here by design; `orchestrator/plan_budgets.py`'s parsers
# are the typed reading under test.
type Record = dict[str, Any]

#: The one repository the plans here change, and the node that owns the budget.
REPOSITORY = "github.com/acme/app"
NODE = "listing"

#: The one budget the plan's node owns, breaking no rule.
BUDGET: Record = {
    "id": "listing-latency",
    "name": "Time to the first page",
    "basis": "measured",
    "repository": REPOSITORY,
    "file": "apps/web/budgets.yaml",
    "file_change": "add",
    "measure": "time to the first page in the browser",
    "inner_measure_reason": "",
    "unit": "ms",
    "direction": "max",
    "threshold": 800,
    "workload": "2,000 nodes",
    "evidence": "spike-listing: p50 420 ms",
    "command": "bun run measure:listing",
}

#: A complete set of plan-level answers that breaks no rule, against the plan `_plan` writes.
ANSWERS: Record = {
    "overview": "Page the node listing.",
    "sizing": "2,000 nodes per plan, 3 runs at once.",
    "workload": "2,000 nodes per plan, 3 runs at once, each node a 2 KB record.",
    "ten_x_summary": "The listing slows first; `listing-latency` covers it.",
    "ten_x": "At 20,000 nodes the first page slows first; `listing-latency` covers it.",
    "checklist": [
        {"concern": "latency", "budget": "listing-latency", "not_applicable": "", "summary": ""},
        {
            "concern": "spend",
            "budget": "",
            "not_applicable": "n/a because it calls no paid API",
            "summary": "It calls no paid API.",
        },
    ],
    "repo_wide_effects": [
        {
            "repository": REPOSITORY,
            "budget": "gate-time",
            "effect": "+20 s",
            "summary": "The gate grows by 20 s.",
        }
    ],
    "realistic_data": [
        {
            "data": "plan nodes",
            "choice": "generator",
            "reason": "seeded",
            "artifact": "gen.ts",
            "summary": "Seeded generated nodes.",
        }
    ],
    "spike_findings": [
        {
            "spike": "spike-listing",
            "finding": "pages at 100",
            "changed": "paging",
            "summary": "Pages of 100 hold.",
        }
    ],
}

#: The plan those answers are read against: one node of the one repository.
PLAN: Record = {
    "schema_version": 3,
    "tasks": [
        {
            "id": NODE,
            "title": "feat: page the listing",
            "repo": f"https://{REPOSITORY}",
            "task": "## What\n\nPage it.\n\n## Why\n\nIt is slow.\n\n",
        }
    ],
}


def _plan(
    answers: Record | None = ANSWERS,
    budgets: list[Record] | None = None,
    plan: Record = PLAN,
    *,
    body: str | None = None,
    record: object = None,
) -> str:
    """Write ``plan`` into this process's fixture source, its node owning ``budgets``.

    The node's body carries the section its budgets render, unless ``body`` is given, and
    its record is ``budgets`` unless ``record`` is given; the plan-level ``answers`` are
    stated as its description when given.
    """
    budgets = [BUDGET] if budgets is None else budgets
    native = f"budgets-{os.getpid()}-{next(_SEQUENCE)}"
    tasks = copy.deepcopy(plan["tasks"])
    tasks[0]["task"] = (
        body
        if body is not None
        else tasks[0]["task"] + (budget_section(budgets) if budgets else "")
    )
    write_plan_project(
        plan_fixture_source.root(), {**plan, "tasks": tasks, "name": native}, native_id=native
    )
    held = budgets if record is None else record
    if held:
        owning(plan_fixture_source.SOURCE, native, NODE, held)
    if answers is not None:
        budgeted(plan_fixture_source.SOURCE, native, answers)
    return f"{plan_fixture_source.SOURCE}:{native}"


def _checked(project: str) -> list[plan_budgets.Refusal]:
    """What the check refuses, over the document shape the engine's `plan check` hands it.

    Each task carries its metadata map verbatim, as the engine's loader hands it to the
    registered check; the store path is asked the same and must agree.
    """
    plan, records = plan_store.read_project(project)
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    by_node = {record.node_id: record for record in records}
    document = {
        **plan,
        "tasks": [
            {"id": node["id"], **node, "metadata": dict(by_node[node["id"]].metadata)}
            for node in tasks
        ],
    }
    found = plan_budgets.refusals(project, document)
    assert found == plan_budgets.refusals(project, plan, plan_budgets.tasks_of_records(records))
    return found


def _broken(**changes: object) -> Record:
    """``ANSWERS`` with each named answer replaced, or its first entry's keys changed."""
    answers = copy.deepcopy(ANSWERS)
    for field, value in changes.items():
        if isinstance(value, dict) and isinstance(answers[field], list):
            answers[field][0].update(value)
        else:
            answers[field] = value
    return answers


def _budget(**changes: object) -> Record:
    return {**BUDGET, **changes}


def test_a_complete_plan_is_read_back_and_breaks_no_rule() -> None:
    project = _plan()

    assert _checked(project) == []
    assert plan_budgets.plan_record(project) == ANSWERS
    records = plan_store.read_tasks(project)
    assert [plan_budgets.task_record(record.metadata) for record in records] == [[BUDGET]]
    assert plan_budgets.parse_plan(ANSWERS).checklist[0].budget == "listing-latency"
    assert plan_budgets.parse_budgets([BUDGET])[0].name == BUDGET["name"]


def test_a_plan_with_no_plan_level_answers_is_refused_naming_the_record_it_lacks() -> None:
    project = _plan(None)

    (refused,) = _checked(project)
    assert refused.node is None and refused.field == "budgets"
    assert f"`{plan_budgets.PLAN_RECORD}` metadata" in refused.reason
    assert "`plan-description` template" in refused.reason
    assert "config/budgets-migration.yaml" in refused.reason
    assert plan_budgets.plan_record(project) is None
    assert plan_budgets.plan_record(None) is None
    assert plan_budgets.plan_record("test-fixtures:no-such-project") is None


#: Every plan-level rule, as the answers that break it, the field refused and what it says.
PLAN_RULES = [
    (
        _broken(checklist={"budget": "", "not_applicable": ""}),
        "checklist",
        "checklist entry 1 (latency) answers neither of `budget` and `not_applicable`",
    ),
    (
        _broken(checklist={"not_applicable": "n/a because it is fast", "summary": "Fast."}),
        "checklist",
        "checklist entry 1 (latency) answers both of `budget` and `not_applicable`",
    ),
    (
        _broken(checklist={"budget": "nothing-by-this-id"}),
        "checklist",
        "names the budget 'nothing-by-this-id', which no task of the plan owns",
    ),
    (
        _broken(repo_wide_effects=[]),
        "repo_wide_effects",
        f"`repo_wide_effects` states no effect for {REPOSITORY}",
    ),
    (
        _broken(sizing="x" * (plan_budgets.SIZING_LIMIT + 1)),
        "sizing",
        f"over the {plan_budgets.SIZING_LIMIT} a design document's opening paragraph",
    ),
    (
        _broken(ten_x_summary="first line\nsecond line"),
        "ten_x_summary",
        f"is not one line of at most {plan_budgets.TEN_X_SUMMARY_LIMIT} characters",
    ),
    (
        _broken(ten_x_summary="x" * (plan_budgets.TEN_X_SUMMARY_LIMIT + 1)),
        "ten_x_summary",
        f"is not one line of at most {plan_budgets.TEN_X_SUMMARY_LIMIT} characters",
    ),
    (_broken(overview="  "), "overview", "the `overview` answer is empty"),
    (
        _broken(checklist=[{**ANSWERS["checklist"][1], "summary": ""}, ANSWERS["checklist"][0]]),
        "checklist",
        "checklist entry 1 (spend) has no `summary`, and its summary is non-empty exactly "
        "when its `not_applicable` is",
    ),
    (
        _broken(checklist={"summary": "Covered."}),
        "checklist",
        "checklist entry 1 (latency) has a `summary`, and its summary is non-empty exactly "
        "when its `not_applicable` is",
    ),
    (
        _broken(checklist=[{**ANSWERS["checklist"][1], "summary": "x" * 161}]),
        "checklist",
        f"checklist entry 1 (spend)'s `summary` is not one line of at most "
        f"{plan_budgets.SUMMARY_LIMIT} characters",
    ),
    (
        _broken(repo_wide_effects={"effect": ""}),
        "repo_wide_effects",
        "states an empty `effect`; it is `none` when there is none",
    ),
    (
        _broken(repo_wide_effects={"summary": ""}),
        "repo_wide_effects",
        f"repo-wide effect 1 ({REPOSITORY}) has no `summary`",
    ),
    (
        _broken(repo_wide_effects={"effect": "none"}),
        "repo_wide_effects",
        f"repo-wide effect 1 ({REPOSITORY}) has a `summary`",
    ),
    (
        _broken(realistic_data={"summary": ""}),
        "realistic_data",
        "`realistic_data` entry 1 has no `summary`, and its summary is never empty",
    ),
    (
        _broken(spike_findings={"summary": "two\nlines"}),
        "spike_findings",
        "`spike_findings` entry 1's `summary` is not one line",
    ),
]

PLAN_RULE_IDS = [
    "neither",
    "both",
    "unknown-budget",
    "repo-wide-effects",
    "sizing-cap",
    "ten-x-summary-lines",
    "ten-x-summary-cap",
    "empty-text",
    "missing-summary",
    "unwanted-summary",
    "summary-cap",
    "empty-effect",
    "missing-effect-summary",
    "unwanted-effect-summary",
    "missing-data-summary",
    "summary-lines",
]


@pytest.mark.parametrize(("answers", "field", "said"), PLAN_RULES, ids=PLAN_RULE_IDS)
def test_each_plan_level_rule_is_refused_naming_the_rule(
    answers: Record, field: str, said: str
) -> None:
    project = _plan(answers)

    found = _checked(project)

    assert {(refused.node, refused.field) for refused in found} == {(None, field)}, found
    assert any(said in refused.reason for refused in found), found


#: Every rule a task's own budgets keep, as the budget that breaks it and what is said.
BUDGET_RULES = [
    (_budget(file_change="replace"), "has the `file_change` 'replace', which is not one of add"),
    (_budget(file="apps/web/budgets.toml"), "goes in the file 'apps/web/budgets.toml', which"),
    (_budget(file="/repo/budgets.yaml"), "goes in the file '/repo/budgets.yaml', which is not"),
    (_budget(file="apps/../budgets.yaml"), "goes in the file 'apps/../budgets.yaml', which"),
    (_budget(direction="below"), "has the `direction` 'below', which is not max or min"),
    (_budget(command="  "), "names no `command`, and the command is what performs"),
    (_budget(basis="a hunch"), "has the `basis` 'a hunch', which is not one of measured, "),
    (_budget(name="x" * 61), f"on one line of at most {plan_budgets.NAME_LIMIT} characters"),
    (_budget(name="two\nlines"), "which is not a short plain-English name on one line"),
    (_budget(name=" "), "which is not a short plain-English name on one line"),
]


@pytest.mark.parametrize(
    ("budget", "said"),
    BUDGET_RULES,
    ids=[
        "file-change",
        "not-budgets-yaml",
        "absolute-file",
        "dot-dot-file",
        "direction",
        "empty-command",
        "basis",
        "name-cap",
        "name-lines",
        "name-empty",
    ],
)
def test_each_budget_rule_is_refused_naming_the_task_that_owns_it(
    budget: Record, said: str
) -> None:
    project = _plan(budgets=[budget])

    found = _checked(project)

    assert [(refused.node, refused.field) for refused in found] == [(NODE, "budgets")], found
    assert said in found[0].reason, found[0].reason


def test_a_budget_id_repeated_across_tasks_is_refused() -> None:
    second = {**PLAN["tasks"][0], "id": "paging", "title": "feat: page more"}
    plan = {**PLAN, "tasks": [*PLAN["tasks"], second]}
    project = _plan(plan=plan)
    native = plan_store.qualified(project).native
    owning(plan_fixture_source.SOURCE, native, "paging", [BUDGET])

    found = plan_budgets.refusals(
        project,
        plan_store.read_plan(project, records := plan_store.read_tasks(project)),
        plan_budgets.tasks_of_records(records),
    )

    # The second node records the budget without rendering it, and repeats its id.
    assert ("paging", "budgets") in [(refused.node, refused.field) for refused in found]
    (repeated,) = [refused for refused in found if refused.node is None]
    assert "the budget id(s) 'listing-latency' repeat across the plan's tasks" in repeated.reason


@pytest.mark.parametrize(
    "path",
    ["apps\\web\\budgets.yaml", "", "budgets.yaml/", "apps//budgets.yaml", "./budgets.yaml"],
    ids=["backslashes", "empty", "trailing-slash", "empty-component", "dot-component"],
)
def test_a_file_that_is_not_a_plain_relative_budgets_yaml_path_is_refused(path: str) -> None:
    (refused,) = plan_budgets.budget_refusals(
        NODE, plan_budgets.parse_budgets([_budget(file=path)])
    )

    assert f"goes in the file {path!r}, which is not a relative path" in refused.reason


def test_a_budget_with_no_id_is_refused_by_its_position() -> None:
    found = plan_budgets.budget_refusals(NODE, plan_budgets.parse_budgets([_budget(id="  ")]))

    assert [refused.reason for refused in found] == [
        "budget 1 has no `id`, and every budget is named by one"
    ]


def test_a_repository_named_by_alias_is_compared_as_written() -> None:
    plan = {**PLAN, "tasks": [{**PLAN["tasks"][0], "repo": "ai-orchestrator-isolated"}]}
    effects = [
        {"repository": "ai-orchestrator-isolated", "budget": "", "effect": "none", "summary": ""}
    ]
    owned = {NODE: plan_budgets.parse_budgets([BUDGET])}

    def judged(answers: Record) -> list[str]:
        return [
            refused.reason
            for refused in plan_budgets.plan_rule_refusals(
                plan_budgets.parse_plan(answers), owned, plan
            )
        ]

    assert judged(_broken(repo_wide_effects=effects)) == []
    assert judged(_broken(repo_wide_effects=[])) == [
        "`repo_wide_effects` states no effect for ai-orchestrator-isolated, a repository the "
        "plan's tasks change; each one gets an entry, with the effect `none` where it has none"
    ]


def test_a_task_naming_no_repository_asks_for_no_repo_wide_effect() -> None:
    human = {"id": "sign-off", "kind": "human", "task": "Sign it off."}
    direct = {"id": "notes", "repo": "", "task": "Write the notes."}
    plan = {**PLAN, "tasks": [*PLAN["tasks"], human, direct]}
    owned = {NODE: plan_budgets.parse_budgets([BUDGET])}

    assert plan_budgets.plan_rule_refusals(plan_budgets.parse_plan(ANSWERS), owned, plan) == []


def test_a_plan_with_no_readable_tasks_is_judged_rather_than_crashed_on() -> None:
    answers = plan_budgets.parse_plan(ANSWERS)

    assert [
        refused.field for refused in plan_budgets.plan_rule_refusals(answers, {}, {"tasks": None})
    ] == ["checklist"]
    assert plan_budgets.tasks_of_plan(None) == []
    assert plan_budgets.tasks_of_plan({"tasks": [{"no": "id"}, "text"]}) == []


@pytest.mark.parametrize(
    ("record", "said"),
    [
        (None, "does not hold exactly the 9 answers"),
        ({**ANSWERS, "extra": "x"}, "does not hold exactly the 9 answers"),
        ({**ANSWERS, "workload": 2000}, "`orchestrator.plan-budgets`'s `workload` answer is not"),
        ({**ANSWERS, "checklist": "latency"}, "`checklist` is not a list of objects"),
        ({**ANSWERS, "checklist": ["latency"]}, "`checklist` entry 1 does not hold exactly"),
        (
            _broken(checklist={"owner": "me"}),
            "`checklist` entry 1 does not hold exactly the keys concern, budget, "
            "not_applicable, summary",
        ),
        (
            _broken(repo_wide_effects={"effect": None}),
            "`repo_wide_effects` entry 1 holds None as its `effect`, which is not a string",
        ),
        (
            _broken(realistic_data={"choice": "guess"}),
            "`realistic_data` entry 1 holds 'guess' as its `choice`, which is not one of "
            "generator, fixture, hybrid, real-sample",
        ),
    ],
    ids=[
        "not-an-object",
        "an-extra-answer",
        "workload-not-text",
        "list-not-a-list",
        "entry-not-an-object",
        "entry-extra-key",
        "effect-not-text",
        "unknown-data-choice",
    ],
)
def test_a_plan_record_of_any_other_shape_is_refused_naming_what_is_malformed(
    record: object, said: str
) -> None:
    with pytest.raises(plan_budgets.BudgetsError, match=re.escape(said)):
        plan_budgets.parse_plan(record)


@pytest.mark.parametrize(
    ("record", "said"),
    [
        ({"id": "x"}, "`orchestrator.budgets` is not a list of objects"),
        ([{"id": "x"}], "`orchestrator.budgets` entry 1 does not hold exactly the keys id, name"),
        ([_budget(threshold="800")], "holds '800' as its `threshold`, which is not a finite"),
        ([_budget(threshold=True)], "holds True as its `threshold`, which is not a finite"),
        ([_budget(threshold=float("nan"))], "holds nan as its `threshold`, which is not a"),
    ],
    ids=["not-a-list", "missing-keys", "threshold-text", "threshold-bool", "threshold-nan"],
)
def test_a_task_record_of_any_other_shape_is_refused_naming_what_is_malformed(
    record: object, said: str
) -> None:
    with pytest.raises(plan_budgets.BudgetsError, match=re.escape(said)):
        plan_budgets.parse_budgets(record)


def test_a_malformed_task_record_is_refused_by_the_check_naming_its_task() -> None:
    project = _plan(budgets=[], record=[{"id": "listing-latency"}])

    # Its plan-level checklist also names a budget no readable record owns.
    (refused,) = [refused for refused in _checked(project) if refused.node is not None]

    assert refused.node == NODE
    assert "its `orchestrator.budgets` metadata is malformed" in refused.reason


def test_a_malformed_plan_record_is_refused_by_the_check() -> None:
    project = _plan(_broken(checklist={"owner": "the listing team"}))

    (refused,) = _checked(project)

    assert refused.node is None
    assert "plan-level answers are malformed" in refused.reason
    assert "`checklist` entry 1 does not hold exactly the keys" in refused.reason


def test_a_task_carrying_a_budgets_section_and_no_record_is_refused() -> None:
    project = _plan(budgets=[], body=PLAN["tasks"][0]["task"] + budget_section([BUDGET]))

    found = _checked(project)

    # Its plan-level checklist also names a budget no task now owns.
    sections = [refused for refused in found if refused.node == NODE]
    assert len(sections) == 1, found
    assert "carries a `## Budgets` section but its `orchestrator.budgets` metadata states no " in (
        sections[0].reason
    )


def test_a_task_whose_body_is_not_the_rendering_of_its_record_is_refused() -> None:
    """A target changed in the record alone, or in the body alone, is caught either way."""
    changed = _budget(threshold=900)
    in_the_record = _plan(record=[changed])
    in_the_body = _plan(body=PLAN["tasks"][0]["task"] + budget_section([changed]))

    for project in (in_the_record, in_the_body):
        (refused,) = _checked(project)
        assert refused.node == NODE
        assert "its body does not carry the `## Budgets` section its `orchestrator.budgets` " in (
            refused.reason
        )


def test_a_task_owning_no_budget_and_carrying_no_section_is_held_to_nothing() -> None:
    no_budget = _broken(
        checklist=[ANSWERS["checklist"][1]],
        repo_wide_effects=[
            {"repository": REPOSITORY, "budget": "", "effect": "none", "summary": ""}
        ],
    )
    project = _plan(no_budget, budgets=[], record=[])

    assert _checked(project) == []
    assert plan_budgets.section_refusals(plan_budgets.TaskBudgets(NODE, None, None)) == []


def test_a_plan_adding_no_budget_is_still_held_to_its_plan_level_answers() -> None:
    """A budget left as it is renders no summary, and its plan's answers are still checked."""
    project = _plan(
        _broken(checklist={"not_applicable": "n/a too", "summary": "Too."}),
        budgets=[_budget(file_change="none")],
    )

    (refused,) = _checked(project)

    assert "answers both of `budget` and `not_applicable`" in refused.reason


def _description(project: str) -> Path:
    return plan_fixture_source.root() / "projects" / f"{plan_store.qualified(project).native}.md"


def test_a_description_edited_after_it_was_rendered_is_refused() -> None:
    project = _plan()
    stored = _description(project)
    held = stored.read_text(encoding="utf-8")
    edited = held.replace("Page the node listing.\n\n## Budgets", "Page all.\n\n## Budgets")
    assert edited != held
    stored.write_text(edited, encoding="utf-8")

    (refused,) = _checked(project)

    assert refused.field == "description"
    assert "is not the `plan-description` rendering of its `orchestrator.plan-budgets`" in (
        refused.reason
    )


def test_a_description_whose_record_moved_is_refused() -> None:
    project = _plan()
    native = plan_store.qualified(project).native
    moved = _broken(sizing="20,000 nodes per plan.")
    written = subprocess.run(
        [str(ONETASKGRAPH_BIN), "project", "metadata", "set", project]
        + [plan_budgets.PLAN_RECORD, json.dumps(moved)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr

    (refused,) = _checked(project)

    assert refused.field == "description", native
    assert "is not the `plan-description` rendering" in refused.reason


def test_a_description_matching_its_record_but_recording_another_template_is_refused(
    tmp_path: Path,
) -> None:
    project = _plan()
    native = plan_store.qualified(project).native
    body = tmp_path / "description.md"
    body.write_text(plan_budgets.render_description(ANSWERS), encoding="utf-8")
    created = subprocess.run(
        [str(ONETASKGRAPH_BIN), "project", "create", plan_fixture_source.SOURCE, "--id", native]
        + ["--title", native, "--body-file", str(body), "--no-interactive"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    assert plan_store.project_record(project)["content"] == body.read_text(encoding="utf-8")

    (refused,) = _checked(project)

    assert refused.field == "description"
    assert "records None as the template it was rendered from, not onepipeline:plan-" in (
        refused.reason
    )


def _held(content: str, **metadata: object) -> dict[str, object]:
    """A project record as the store answers one: its content and its metadata."""
    return {"content": content, "metadata": {plan_budgets.PLAN_RECORD: ANSWERS, **metadata}}


def _provenance(content: str, record: object = ANSWERS) -> dict[str, object]:
    """The provenance the store records for ``content`` rendered from ``record``."""
    return {
        "template": plan_budgets.DESCRIPTION_REFERENCE,
        "digest": "sha256:" + "0" * 64,
        "body_digest": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "answers_digest": plan_budgets.answers_digest(record),
    }


def test_a_copy_whose_references_the_store_rewrote_is_held_to_the_stores_own_rule() -> None:
    """A copy re-records the digest of the body it rewrote: accepted only on that record."""
    rewritten = plan_budgets.render_description(ANSWERS).replace("Page the node", "Page the")
    copied = {"onetaskgraph.origin": "authoring:plan"}
    sound = _held(rewritten, **copied, **{"onetaskgraph.template": _provenance(rewritten)})
    not_a_copy = _held(rewritten, **{"onetaskgraph.template": _provenance(rewritten)})
    other_answers = _held(
        rewritten, **copied, **{"onetaskgraph.template": _provenance(rewritten, {"other": 1})}
    )

    assert plan_budgets.description_refusals("plans:x", sound) == []
    assert [one.field for one in plan_budgets.description_refusals("plans:x", not_a_copy)] == [
        "description"
    ]
    assert [one.field for one in plan_budgets.description_refusals("plans:x", other_answers)] == [
        "description"
    ]


def test_the_answers_digest_is_the_one_the_store_records() -> None:
    project = _plan()

    provenance = plan_store.project_record(project)["metadata"]["onetaskgraph.template"]

    assert provenance["answers_digest"] == plan_budgets.answers_digest(ANSWERS)


def test_a_description_over_the_issue_body_limit_is_refused() -> None:
    answers = _broken(workload="w" * task_body.BODY_LIMIT)
    content = plan_budgets.render_description(answers)
    held = {
        "content": content,
        "metadata": {
            plan_budgets.PLAN_RECORD: answers,
            "onetaskgraph.template": _provenance(content, answers),
        },
    }

    (refused,) = plan_budgets.description_refusals("plans:x", held)

    assert refused.field == "description"
    assert f"over the {task_body.BODY_LIMIT:,}-character limit GitHub puts on an issue body" in (
        refused.reason
    )


def _migration(tmp_path: Path, entries: object) -> Path:
    listed = tmp_path / "budgets-migration.yaml"
    listed.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return listed


def test_a_listed_plan_is_exempt_and_every_other_is_not(tmp_path: Path) -> None:
    listed = _migration(tmp_path, [{"project": "authoring:old", "reason": "It came first."}])

    assert plan_budgets.migration_entries(listed) == [
        plan_budgets.MigrationEntry("authoring:old", "It came first.")
    ]
    assert plan_budgets.migrated("authoring:old", listed) == "It came first."
    assert plan_budgets.migrated("authoring:new", listed) is None


@pytest.mark.parametrize(
    ("entries", "said"),
    [
        ({"project": "authoring:old"}, "holds no list"),
        (["authoring:old"], "is not exactly {project, reason}"),
        ([{"project": "authoring:old", "reason": "x", "by": "me"}], "is not exactly"),
        ([{"project": "old", "reason": "It came first."}], "not a qualified project id"),
        ([{"project": "authoring:old", "reason": "TODO"}], "gives no reason"),
        ([{"project": "authoring:old", "reason": "  "}], "gives no reason"),
        ([{"project": "authoring:old", "reason": 7}], "gives no reason"),
        (
            [{"project": "authoring:old", "reason": "One."}] * 2,
            "names authoring:old more than once",
        ),
    ],
    ids=[
        "not-a-list",
        "not-a-mapping",
        "extra-key",
        "unqualified",
        "todo",
        "blank",
        "not-text",
        "twice",
    ],
)
def test_a_migration_list_of_the_wrong_shape_is_refused(
    tmp_path: Path, entries: object, said: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(said)):
        plan_budgets.migration_entries(_migration(tmp_path, entries))


def test_a_migration_list_that_is_not_yaml_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "budgets-migration.yaml"
    broken.write_text("- project: [unclosed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="is not YAML"):
        plan_budgets.migration_entries(broken)


def test_the_tracked_list_names_this_plan_and_holds_one_reason_of_its_own_per_entry() -> None:
    """The list as committed: well formed, this plan's two copies named, no reason shared."""
    entries = plan_budgets.migration_entries()
    named = {entry.project for entry in entries}

    assert {"authoring:approved-budgets", "plans:I_kwDOTXCWqs8AAAABU8UKWg"} <= named
    reasons = [entry.reason for entry in entries]
    assert len(set(reasons)) == len(reasons), (
        "a reason shared by two entries says nothing of either"
    )


def _stamp(project: str) -> None:
    stamp = {"kind": design_approval.PLANNING, "nodes": [NODE]}
    written = subprocess.run(
        [str(ONETASKGRAPH_BIN), "project", "metadata", "set", project]
        + [design_approval.PLAN_KIND, json.dumps(stamp)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr


def test_the_plan_kinds_design_approval_exempts_are_exempt_here_on_the_same_bound() -> None:
    project = _plan(None, budgets=[])
    _stamp(project)
    loaded = plan_store.read_project(project)[0]

    assert plan_budgets.exempt_kind(project, {NODE}) == design_approval.PLANNING
    assert plan_budgets.refusals(project, loaded) == []
    # A project holding a node its stamp does not claim is a plan like any other.
    assert plan_budgets.exempt_kind(project, {NODE, "more"}) is None
    held = loaded["tasks"]
    assert isinstance(held, list)
    grown = {**loaded, "tasks": [*held, {"id": "more"}]}
    assert [refused.field for refused in plan_budgets.refusals(project, grown)] == ["budgets"]


def test_a_migration_list_the_check_cannot_read_is_a_refusal_rather_than_an_exemption(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _plan(None)
    monkeypatch.setattr(plan_budgets, "MIGRATION_LIST", tmp_path / "absent.yaml")

    (refused,) = _checked(project)
    assert "budgets could not be read" in refused.reason
    assert "absent.yaml" in refused.reason


def test_a_description_that_cannot_be_rendered_is_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _plan()

    def unrenderable(record: object) -> str:
        raise plan_budgets.BudgetsError("the engine is gone")

    monkeypatch.setattr(plan_budgets, "render_description", unrenderable)

    (refused,) = _checked(project)
    assert refused.field == "description"
    assert "could not be rendered: the engine is gone" in refused.reason


# llmlint: ignore[suppressions_justified] What the command printed, the open JSON answer shape the writer copies, read by key and nested index below, so its values are `Any` as the decoded document they are; the parsers are its typed reading.  # noqa: E501
def _writer(project: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert plan_budgets.main([project]) == 0
    stated: dict[str, Any] = json.loads(capsys.readouterr().out)
    return stated


def test_the_writers_answers_are_the_records_with_each_budgets_owner_and_its_location(
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _plan()
    (task,) = plan_store.read_tasks(project)

    stated = _writer(project, capsys)

    assert stated == {
        "predates_budgets": "",
        "plan_budgets": ANSWERS,
        "budgets": [{"node": NODE, "location": task.location, **BUDGET}],
    }
    assert task.location is not None and Path(task.location).is_absolute()

    predated = _writer("authoring:approved-budgets", capsys)
    assert predated == {
        "predates_budgets": plan_budgets.migrated("authoring:approved-budgets"),
        "plan_budgets": {},
        "budgets": [],
    }

    bare = _plan(None)
    assert plan_budgets.main([bare]) == 2
    assert f"{bare} carries no `orchestrator.plan-budgets` metadata" in capsys.readouterr().err


def _design(project: str, capsys: pytest.CaptureFixture[str], **changed: object) -> str:
    """Write ``project``'s design document from its writer answers, ``changed`` applied."""
    answers = _writer(project, capsys)
    answers.update(changed)
    native = plan_store.qualified(project).native
    designed(plan_fixture_source.SOURCE, native, [], budget_answers=answers)
    return f"{plan_fixture_source.SOURCE}:{native}-design"


def test_a_design_document_copying_the_writers_answers_is_held_to_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _plan()
    _design(project, capsys)

    assert plan_budgets.main([project, "--check-design-document"]) == 0
    assert capsys.readouterr().err == ""


def test_a_design_document_whose_budget_answers_differ_is_refused_naming_each_difference(
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _plan()
    (stated,) = _writer(project, capsys)["budgets"]
    second = {**BUDGET, "id": "listing-memory", "name": "Memory", "unit": "MB"}
    cases = {
        "changed target": (
            [{**stated, "threshold": 900}],
            "its budget 'listing-latency' states the `threshold` 900, where the records state 800",
        ),
        "wrong owner": (
            [{**stated, "node": "paging"}],
            "its budget 'listing-latency' states the `node` 'paging', where the records state "
            "'listing'",
        ),
        "omitted budget": ([], "it omits the budget 'listing-latency', which `listing` owns"),
        "invented budget": (
            [stated, {**stated, **second}],
            "it states the budget 'listing-memory', which no task of the plan owns",
        ),
        "extra key": (
            [{**stated, "note": "x"}],
            "its budget 'listing-latency' carries the key `note`, which no record states",
        ),
    }
    for case, (budgets, said) in cases.items():
        _design(project, capsys, budgets=budgets)
        assert plan_budgets.main([project, "--check-design-document"]) == 1, case
        assert said in capsys.readouterr().err, case

    _design(project, capsys, plan_budgets={**ANSWERS, "sizing": "Another."})
    assert plan_budgets.main([project, "--check-design-document"]) == 1
    assert "its `plan_budgets` answer is not what `python -m orchestrator.plan_budgets" in (
        capsys.readouterr().err
    )


def test_a_design_document_listing_the_budgets_in_another_order_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every budget right but out of the order the command prints them in is still a refusal."""
    second = _budget(id="listing-memory", name="Memory on the first page", unit="MB")
    answers = _broken(
        checklist=[*ANSWERS["checklist"], {**ANSWERS["checklist"][0], "budget": "listing-memory"}]
    )
    project = _plan(answers, budgets=[BUDGET, second])
    printed = _writer(project, capsys)["budgets"]
    _design(project, capsys, budgets=list(reversed(printed)))

    assert plan_budgets.main([project, "--check-design-document"]) == 1
    assert "its `budgets` answer lists the plan's budgets in another order" in (
        capsys.readouterr().err
    )


def test_a_design_document_the_store_cannot_answer_for_is_unrunnable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _plan()

    assert plan_budgets.main([project, "--check-design-document"]) == 2
    assert capsys.readouterr().err.startswith("plan-budgets: ")


def test_the_fixture_answers_for_a_plan_needing_no_budget_break_no_rule() -> None:
    project = _plan(no_budgets([REPOSITORY]), budgets=[])

    assert _checked(project) == []


def test_the_design_document_is_the_one_recording_the_design_doc_template() -> None:
    """Beside another rendered document, the approval reads the design-doc rendering alone."""
    project = _plan()
    native = plan_store.qualified(project).native
    designed(plan_fixture_source.SOURCE, native, [])
    created = subprocess.run(
        [str(ONETASKGRAPH_BIN), "document", "create", plan_fixture_source.SOURCE]
        + ["--project", native, "--title", f"Notes: {native}", "--id", f"{native}-notes"]
        + ["--template", str(plan_budgets.TASK_PARTIAL)]
        + ["--search-path", str(plan_budgets.TEMPLATE_ROOT)]
        + ["--var", f"budgets={json.dumps([BUDGET])}", "--no-interactive"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr

    held = plan_store.read_documents(project)

    assert len(held) == 2
    assert all("onetaskgraph.template" in document.metadata for document in held)
    chosen = design_approval.one_document(project, held)
    assert chosen.qualified_id == f"{plan_fixture_source.SOURCE}:{native}-design"
    assert design_approval.is_design_document(chosen)
    assert [str(one.qualified_id) for one in design_approval.design_documents(project, held)] == [
        str(chosen.qualified_id)
    ]


def test_a_task_owning_no_budget_adds_nothing_to_the_writers_answers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    second = {**PLAN["tasks"][0], "id": "notes", "title": "docs: write the notes"}
    project = _plan(plan={**PLAN, "tasks": [*PLAN["tasks"], second]})

    stated = _writer(project, capsys)

    assert [entry["node"] for entry in stated["budgets"]] == [NODE]


def test_a_description_template_the_engine_cannot_resolve_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Neither an engine that refuses the template nor one that is not there is read as one."""
    monkeypatch.setattr(plan_budgets, "TEMPLATE_ROOT", tmp_path)
    with pytest.raises(plan_budgets.BudgetsError, match="template resolve plan-description` re"):
        plan_budgets.render_description(ANSWERS)

    monkeypatch.setattr(plan_budgets, "REPO_ROOT", tmp_path)
    with pytest.raises(plan_budgets.BudgetsError, match="the pinned engine could not be run"):
        plan_budgets.render_description(ANSWERS)


#: #1568's real budget answers in this shape, which the design document's own budget is
#: measured over too.
FIXTURE_1568 = REPO_ROOT / "tests" / "fixtures" / "budgets" / "issue-1568.json"

#: About what this host's operational notes add to every task body, which a measured body
#: carries whole: the size `orchestrator/task_body.py` states for the appendix.
NOTES = "## Additional info\n\n" + "Work the branch, commit as you go, and report. " * 500


def test_a_task_owning_six_realistic_budgets_fits_the_issue_body_limit() -> None:
    """Its section and its record both travel in the issue body, and still fit under it."""
    fixture = json.loads(FIXTURE_1568.read_text(encoding="utf-8"))
    six = [
        {key: entry[key] for key in plan_budgets.BUDGET_KEYS} for entry in fixture["budgets"][:6]
    ]
    content = (
        "## What\n\nCarry image assets across copies.\n\n## Why\n\nScreenshots.\n\n"
        + budget_section(six)
        + "## Acceptance criteria\n\n- The images arrive.\n\n"
        + NOTES
    )
    metadata = {
        "onepipeline.id": "otg-assets-github",
        "onepipeline.persona": "engineer",
        plan_budgets.TASK_RECORD: six,
    }
    plan = {"tasks": [{"id": "otg-assets-github", "task": content, "metadata": metadata}]}

    size = task_body.measure(content, metadata)

    assert len(json.dumps(six)) > 10_000, "the six budgets are not realistic in size"
    assert size <= task_body.BODY_LIMIT, size
    assert task_body.refusals(plan) == []
    # And the record is what is measured beside the body: without it the body is smaller.
    assert task_body.measure(content, {**metadata, plan_budgets.TASK_RECORD: []}) < size


def test_a_description_of_1568s_plan_level_answers_fits_the_issue_body_limit() -> None:
    answers = json.loads(FIXTURE_1568.read_text(encoding="utf-8"))["plan_budgets"]
    content = plan_budgets.render_description(answers)
    metadata = {
        plan_budgets.PLAN_RECORD: answers,
        "onetaskgraph.template": _provenance(content, answers),
    }

    assert (
        plan_budgets.description_refusals("plans:x", {"content": content, "metadata": metadata})
        == []
    )
    assert task_body.measure(content, metadata) <= task_body.BODY_LIMIT


def test_a_design_document_rendered_under_a_template_taking_no_budgets_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Its stored answers hold no `budgets` list, which is refused rather than read as none."""
    project = _plan()
    native = plan_store.qualified(project).native
    root = tmp_path / "templates"
    root.mkdir()
    (root / "templates.yaml").write_text(
        "onepipeline_templates: 1\ntemplates:\n  design-doc:\n    role: document\n"
        "    description: The design document, as it was before budgets.\n",
        encoding="utf-8",
    )
    pre_budgets = REPO_ROOT / "tests" / "fixtures" / "design_doc" / "pre-budgets-design-doc.md.j2"
    (root / "design-doc.md.j2").write_bytes(pre_budgets.read_bytes())
    resolved = subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "onepipeline"), "template", "resolve", "design-doc"]
        + ["--json", "--template-root", str(root)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "what": "W",
                "why": "Y",
                "architecture": "A",
                "units": [],
                "acceptance_criteria": ["C"],
                "planned_tasks": [],
            }
        ),
        encoding="utf-8",
    )
    created = subprocess.run(
        [str(ONETASKGRAPH_BIN), "document", "create", plan_fixture_source.SOURCE]
        + ["--project", native, "--title", f"Design: {native}", "--id", f"{native}-design"]
        + ["--template-loader", "-", "--answers", str(answers), "--no-interactive"],
        cwd=REPO_ROOT,
        input=resolved.stdout,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr

    assert plan_budgets.main([project, "--check-design-document"]) == 1
    refused = capsys.readouterr().err
    assert "it holds no `budgets` list, so it was rendered from a design-doc template" in refused


def test_a_record_that_is_not_json_or_not_an_object_of_answers_is_refused() -> None:
    """The store's answers are JSON, and a description's are an object of them."""
    with pytest.raises(plan_budgets.BudgetsError, match="a budgets record is not JSON"):
        plan_budgets.render_section({"not": object()})
    with pytest.raises(plan_budgets.BudgetsError, match="is not an object of answers"):
        plan_budgets.render_description(["not", "an", "object"])
