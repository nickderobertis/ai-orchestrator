"""`just finish-plan` carries a plan's budgets into its design document — or says it predates them.

Three journeys through the real recipe, with only the paid provider doubled:

* a plan that carries a budgets document, and that no plan kind and no migration entry
  exempts: the design-document writer's task, as `scripts/finish-plan.sh` composes it,
  quotes that document's entries, and the document the flow stores shows them in its
  budgets table once the doubled writer answers from that task;
* a new plan the migration list does not name, carrying no budgets document: the plan check
  refuses it naming the document it lacks, and nothing is launched;
* a plan the migration list names, with no budgets document and a design document approved
  under the template chain in force before budgets existed: once the requirement is in
  force it passes the review and the plan check, the flow launches its design document,
  which renders the predates-budgets line, the user's re-approval is recorded, and the
  launch gate then admits it.

The doubled writer acts as a real one would: it reads its own task out of the plan store,
answers the design document's budget answers from what that task quotes, and renders the
document through the pinned engine's resolve piped into the pinned store's `document create`.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] These journeys sit in
`plan-tooling` beside the check-plan and finish-plan journeys they extend, and drive the same
surface those do — the real recipes, the registered check, the installed engine and store —
under the inputs `planToolingWorkspace` names, which `tests/plan_tooling/AGENTS.md` states as
that project's split. A project of their own would be keyed on those same inputs and add only
a target.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import plan_fixture_source
import plan_root_variable
import pytest
from fake_backend import PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from nx_workspace import answering_this_checkouts_origin, copy_working_tree
from published_tools import ONETASKGRAPH_BIN
from test_finish_plan_recipe_e2e import (
    DESIGN_RUN_SUFFIX,
    DESIGN_TASK_MARKER,
    DESTINATION,
    ENGINE_BIN,
    OK,
    PASSES,
    PLAN_REFUSED,
    Bench,
    Drafted,
    RunId,
    _answers,
    _bench,
    _brief,
    _design_task,
    _draft,
    _just,
    _records,
    _stop,
)
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The source plans are drafted in here: this process's own fixture store.
FIXTURE_SOURCE = plan_fixture_source.SOURCE

#: The node `_draft` writes, which owns the one budget below.
NODE = "decide-the-cursor"

#: The budgets document of the budgeted plan: one budget, owned by its one node.
BUDGETS: dict[str, object] = {
    "workload": "2,000 nodes per plan, 3 runs at once.",
    "checklist": [
        {"concern": "latency", "budget": "listing-latency", "not_applicable": ""},
        {"concern": "spend", "budget": "", "not_applicable": "n/a because it calls no paid API"},
    ],
    "ten_x": "The listing slows first; listing-latency covers it.",
    "budgets": [
        {
            "id": "listing-latency",
            "repository": "github.com/nickderobertis/some-service",
            "file": "apps/web/budgets.yaml",
            "measure": "time to the first page of nodes in the browser",
            "inner_measure_reason": "",
            "unit": "ms",
            "direction": "max",
            "threshold": 800,
            "workload": "2,000 nodes behind the cursor",
            "evidence": "spike-listing measured 420 ms",
            "command": "bun run measure:listing",
            "node": NODE,
            "file_change": "add",
        }
    ],
    "repo_wide_effects": [],
    "realistic_data": [],
    "spike_findings": [],
}

#: The doubled writer: what a design-doc dispatch does, as one program. It reads its own
#: task out of the store, answers the budget answers from what the task quotes — the
#: budgets document's answers, or the reason the plan predates budgets — and renders the
#: document from those answers and the rest it composed.
WRITER = r"""
import json, os, re, subprocess, sys, tempfile

(engine, store, source, project, title, document, base, design_project) = sys.argv[1:9]
listed = subprocess.run(
    [store, "task", "list", "--source", "authoring", "--project", design_project, "--json"],
    capture_output=True, text=True, check=True,
)
(task,) = [held["item"]["content"] for held in json.loads(listed.stdout)["items"]]
answers = json.loads(open(base, encoding="utf-8").read())
quoted = re.search(r"which answers the following\..*?```json\n(.*?)\n```", task, re.S)
predates = re.search(r"The plan predates budgets:.*?\n\n> ([^\n]+)", task, re.S)
if quoted:
    answers.update(json.loads(quoted.group(1)))
elif predates:
    answers["predates_budgets"] = predates.group(1)
loader = subprocess.run(
    [engine, "template", "resolve", "design-doc", "--json"],
    capture_output=True, text=True, check=True,
).stdout
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as written:
    json.dump(answers, written)
subprocess.run(
    [store, "document", "create", source, "--project", project, "--title", title,
     "--id", document, "--template-loader", "-", "--answers", written.name,
     "--no-interactive"],
    input=loader, text=True, check=True,
)
os.unlink(written.name)
"""


def _writes_from_its_task(bench: Bench, drafted: Drafted, run: RunId) -> None:
    """Script the design-doc dispatch as the program above, run where the dispatch runs."""
    writer = bench.tmp_path / "writer.py"
    writer.write_text(WRITER, encoding="utf-8")
    base = bench.tmp_path / f"{drafted.document}.base.json"
    base.write_text(json.dumps(_answers(drafted)), encoding="utf-8")
    keyed = bench.tmp_path / f"commands-{drafted.project}.json"
    source, _, project = drafted.qualified.partition(":")
    keyed.write_text(
        json.dumps(
            {
                DESIGN_TASK_MARKER: [
                    [
                        sys.executable,
                        str(writer),
                        str(ENGINE_BIN),
                        str(ONETASKGRAPH_BIN),
                        source,
                        project,
                        f"Design: {drafted.project}",
                        drafted.document,
                        str(base),
                        f"{run}{DESIGN_RUN_SUFFIX}",
                    ]
                ]
            }
        ),
        encoding="utf-8",
    )
    bench.environment[RUN_ON_MARKER_ENV] = str(keyed)
    bench.environment[PROMPT_LOG_ENV] = str(bench.tmp_path / f"turns-{drafted.project}.jsonl")


@pytest.mark.xdist_group("finish-plan")
def test_a_budgeted_plans_writer_is_handed_its_budgets_and_its_document_shows_them(
    tmp_path: Path, oneharness_bin: str
) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-budgeted", budgets=BUDGETS)
    run = RunId("finish-plan-e2e-budgeted")
    _writes_from_its_task(bench, drafted, run)
    try:
        finished = _just(
            "finish-plan",
            str(_brief(tmp_path, drafted)),
            "--name",
            run,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
        assert finished.returncode == OK, f"{finished.stdout}\n{finished.stderr}"
        task = _design_task(bench, run)["content"]
    finally:
        _stop(bench, f"{run}{DESIGN_RUN_SUFFIX}")

    # The writer's task quotes the budgets document whole, and says what to do with it.
    assert f"`{drafted.qualified}-budgets`" in task, task
    quoted = task.split("which answers the following.", 1)[1].split("```json\n", 1)[1]
    assert json.loads(quoted.split("\n```", 1)[0]) == BUDGETS, task
    assert "`predates_budgets` is empty" in task, task

    # And the document the flow stored shows them, in the budgets table.
    stored = drafted.document_path.read_text(encoding="utf-8")
    assert "## Budgets\n" in stored, stored
    assert (
        "| time to the first page of nodes in the browser | 2,000 nodes behind the cursor "
        "| at most 800 ms | spike-listing measured 420 ms "
        "| `bun run measure:listing` in `apps/web/budgets.yaml` |"
    ) in stored, stored
    assert "2,000 nodes per plan, 3 runs at once." in stored, stored
    assert "- **spend.** n/a because it calls no paid API" in stored, stored
    assert "predates budgets" not in stored, stored


@pytest.mark.xdist_group("finish-plan")
def test_a_new_plan_with_no_budgets_document_is_refused_and_nothing_is_launched(
    tmp_path: Path, oneharness_bin: str
) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-unbudgeted", budgets=False)
    run = RunId("finish-plan-e2e-unbudgeted")
    try:
        refused = _just(
            "finish-plan",
            str(_brief(tmp_path, drafted)),
            "--name",
            run,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
    finally:
        _stop(bench, f"{run}{DESIGN_RUN_SUFFIX}")

    assert refused.returncode == PLAN_REFUSED, f"{refused.stdout}\n{refused.stderr}"
    assert (
        f"{drafted.qualified} carries no `{drafted.qualified}-budgets` document" in refused.stderr
    ), refused.stderr
    assert "no design document was launched and nothing was copied" in refused.stderr
    assert not (bench.runs / f"{run}{DESIGN_RUN_SUFFIX}").exists(), _records(bench.runs)
    assert _records(bench.destination) == [], _records(bench.destination)


#: The design-doc template as it stood before budgets existed: the chain a plan approved
#: before this requirement was approved under.
PRE_BUDGETS_TEMPLATE = (
    REPO_ROOT / "tests" / "fixtures" / "design_doc" / "pre-budgets-design-doc.md.j2"
)

#: The reason the migration journey's plan is listed for.
REASON = "It was approved before plans carried a budgets document, in the migration journey."


def _provisioned_copy(tmp_path: Path) -> Path:
    """A copy of this checkout, provisioned from the locked installs as a session would be.

    The migration list is a tracked file and nothing but a reviewed change adds to it, so a
    journey whose plan the list names writes that entry into a copy; and the template
    chain a document was approved under before budgets existed is the copy's, too.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    answering_this_checkouts_origin(checkout)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV")
    }
    synced = subprocess.run(
        ["uv", "sync", "--locked"],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    assert synced.returncode == 0, f"the copied checkout could not be provisioned:\n{synced.stderr}"
    return checkout


#: What `uv run` would otherwise take for the environment to run in rather than the copy's.
_FOREIGN_ENVIRONMENT = ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV")


def _in(checkout: Path, bench: Bench, *argv: str) -> subprocess.CompletedProcess[str]:
    """Run one of the copy's own commands, from the copy, on the bench."""
    return subprocess.run(
        list(argv),
        cwd=checkout,
        env={k: v for k, v in bench.environment.items() if k not in _FOREIGN_ENVIRONMENT},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# tests/plan_tooling/AGENTS.md states this project's split: `reads_docs` routes a journey that
# builds a copy of this checkout to `plan-tooling:test-docs`, keyed on the whole workspace because
# copying the tracked tree reads all of it, and never out of this project.
@pytest.mark.reads_docs
@pytest.mark.xdist_group("finish-plan")
def test_a_listed_plan_predating_budgets_is_documented_as_such_and_re_approved(
    tmp_path: Path, oneharness_bin: str
) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    checkout = _provisioned_copy(tmp_path)
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    bench.environment[plan_root_variable.name()] = str(authoring)
    drafted = _draft("finish-plan-predates", budgets=False)
    template = checkout / "templates" / "design-doc.md.j2"

    # Before budgets: the design document rendered and approved under the chain then in force.
    template.write_text(PRE_BUDGETS_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    earlier = bench.tmp_path / "earlier-answers.json"
    earlier.write_text(json.dumps(_answers(drafted)), encoding="utf-8")
    resolved = _in(
        checkout,
        bench,
        str(checkout / ".venv" / "bin" / "onepipeline"),
        "template",
        "resolve",
        "design-doc",
        "--json",
        "--template-root",
        str(checkout / "templates"),
    )
    assert resolved.returncode == 0, resolved.stderr
    created = subprocess.run(
        [str(ONETASKGRAPH_BIN), "document", "create", FIXTURE_SOURCE, "--project", drafted.project]
        + ["--title", f"Design: {drafted.project}", "--id", drafted.document]
        + ["--template-loader", "-", "--answers", str(earlier), "--no-interactive"],
        cwd=checkout,
        env=bench.environment,
        input=resolved.stdout,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    approved = _in(checkout, bench, "just", "approve-design", drafted.qualified)
    assert approved.returncode == 0, approved.stdout + approved.stderr

    # The requirement comes into force: the template with its budget sections, and the
    # migration list naming this plan, as the reviewed change adding it would.
    shutil.copy(REPO_ROOT / "templates" / "design-doc.md.j2", template)
    listed = checkout / "config" / "budgets-migration.yaml"
    listed.write_text(
        listed.read_text(encoding="utf-8")
        + f"- project: {json.dumps(drafted.qualified)}\n  reason: {json.dumps(REASON)}\n",
        encoding="utf-8",
    )
    gated = _in(checkout, bench, "uv", "run", "orchestrator-launch-gate", drafted.qualified)
    assert gated.returncode == 1, "an approval granted under the earlier chain still admitted it"
    assert "now resolves to" in gated.stderr, gated.stderr

    reviewed = _in(checkout, bench, "just", "review-plan", drafted.qualified)
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    checked = _in(checkout, bench, "just", "check-plan", drafted.qualified)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "budgets" not in checked.stderr, checked.stderr

    run = RunId("finish-plan-e2e-predates")
    _writes_from_its_task(bench, drafted, run)
    try:
        finished = _in(
            checkout,
            bench,
            "just",
            "finish-plan",
            str(_brief(tmp_path, drafted)),
            "--name",
            run,
            "--to",
            DESTINATION,
        )
        assert finished.returncode == OK, f"{finished.stdout}\n{finished.stderr}"
    finally:
        _stop(bench, f"{run}{DESIGN_RUN_SUFFIX}")
    stored = drafted.document_path.read_text(encoding="utf-8")
    assert f"## Budgets\n\nThis plan predates budgets: {REASON}\n" in stored, stored
    assert "## Budgets-file changes" not in stored, stored

    # Re-approving the document the new chain rendered is the user's act, and it admits it.
    still = _in(checkout, bench, "uv", "run", "orchestrator-launch-gate", drafted.qualified)
    assert still.returncode == 1, "the regenerated document was admitted with nobody approving it"
    reapproved = _in(checkout, bench, "just", "approve-design", drafted.qualified)
    assert reapproved.returncode == 0, reapproved.stdout + reapproved.stderr
    assert "recorded the approval" in reapproved.stdout, reapproved.stdout
    admitted = _in(checkout, bench, "uv", "run", "orchestrator-launch-gate", drafted.qualified)
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
