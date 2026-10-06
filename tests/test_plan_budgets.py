"""A plan's budgets document, read and held to its rules by `orchestrator/plan_budgets.py`.

Every document here is rendered and stored the way a planner writes one: the pinned
engine's `template resolve plan-budgets` piped into the pinned store's `document create`,
in this test process's own `test-fixtures` source. The reader is then asked through the
store, so what is proven is what `just check-plan` and `just review-plan` read.
`tests/plan_tooling/test_check_plan_budgets_e2e.py` drives the same rules through the
recipe; what is here is every rule and every shape the reader refuses, one at a time.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] The
subject is `orchestrator/plan_budgets.py`, this tier's own module, and the boundary it reads
through is the plan store, so the store is what these tests drive rather than a stand-in for
it — the same choice `tests/test_design_doc_template.py` and `tests/test_design_approval.py`
make for the documents beside this one. Everything they read is in the key this tier is
memoized on: `templates/`, `config/budgets-migration.yaml` and the pinned installs `uv.lock`
names are all in `codeWorkspace`, and each store they write is this process's own fixture
root. The one test reading this repository's prose carries `reads_docs`.
"""

from __future__ import annotations

import copy
import dataclasses
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
from project_fixtures import budgeted, budgets_document, no_budgets
from published_tools import ONETASKGRAPH_BIN

from orchestrator import design_approval, plan_budgets, plan_store
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

_SEQUENCE = itertools.count()

# llmlint: ignore[suppressions_justified] A budgets record is the template's open JSON answer
# shape, which these tests build and then break one key at a time — a value of the wrong type
# among them — so its values are `Any` here by design; `orchestrator/plan_budgets.py`'s `parse`
# is the typed reading under test.
type Record = dict[str, Any]

#: The one repository the plans here change.
REPOSITORY = "github.com/acme/app"

#: A complete set of answers that breaks no rule, against the plan `_plan` writes.
ANSWERS: Record = {
    "workload": "2,000 nodes per plan, 3 runs at once.",
    "checklist": [
        {"concern": "latency", "budget": "listing-latency", "not_applicable": ""},
        {"concern": "spend", "budget": "", "not_applicable": "n/a because it calls no paid API"},
    ],
    "ten_x": "The listing slows first; `listing-latency` covers it.",
    "budgets": [
        {
            "id": "listing-latency",
            "repository": REPOSITORY,
            "file": "apps/web/budgets.yaml",
            "measure": "time to the first page in the browser",
            "inner_measure_reason": "",
            "unit": "ms",
            "direction": "max",
            "threshold": 800,
            "workload": "2,000 nodes",
            "evidence": "spike-listing: p50 420 ms",
            "command": "bun run measure:listing",
            "node": "listing",
            "file_change": "add",
        }
    ],
    "repo_wide_effects": [{"repository": REPOSITORY, "budget": "gate-time", "effect": "+20 s"}],
    "realistic_data": [
        {"data": "plan nodes", "choice": "generator", "reason": "seeded", "artifact": "gen.ts"}
    ],
    "spike_findings": [{"spike": "spike-listing", "finding": "pages at 100", "changed": "paging"}],
}

#: The plan those answers are read against: one node of the one repository.
PLAN: Record = {
    "schema_version": 3,
    "tasks": [
        {
            "id": "listing",
            "title": "feat: page the listing",
            "repo": f"https://{REPOSITORY}",
            "task": "Page it.",
        }
    ],
}


def _plan(budgets: Record | None = ANSWERS, plan: Record = PLAN) -> str:
    """Write ``plan`` into this process's fixture source, with ``budgets`` when given."""
    native = f"budgets-{os.getpid()}-{next(_SEQUENCE)}"
    write_plan_project(plan_fixture_source.root(), {**plan, "name": native}, native_id=native)
    if budgets is not None:
        budgeted(plan_fixture_source.SOURCE, native, budgets)
    return f"{plan_fixture_source.SOURCE}:{native}"


def _loaded(project: str) -> dict[str, object]:
    plan, _ = plan_store.read_project(project)
    return plan


def _broken(**changes: object) -> Record:
    """``ANSWERS`` with each named answer replaced, or its first entry's keys changed."""
    answers = copy.deepcopy(ANSWERS)
    for field, value in changes.items():
        if isinstance(value, dict) and isinstance(answers[field], list):
            answers[field][0].update(value)
        else:
            answers[field] = value
    return answers


def test_a_complete_document_is_read_back_and_breaks_no_rule() -> None:
    project = _plan()

    held = plan_budgets.read(project)

    assert held is not None
    assert held.document == f"{project}{plan_budgets.DOCUMENT_SUFFIX}"
    assert held.answers.as_record() == ANSWERS
    assert plan_budgets.refusals(project, _loaded(project)) == []
    owned = plan_budgets.owned(held)
    assert {
        node: [dataclasses.asdict(one) for one in budgets] for node, budgets in owned.items()
    } == {"listing": ANSWERS["budgets"]}


def test_a_plan_with_no_budgets_document_is_refused_naming_the_document_it_lacks() -> None:
    project = _plan(None)

    assert plan_budgets.read(project) is None
    (refused,) = plan_budgets.refusals(project, _loaded(project))
    assert refused.field == "budgets"
    assert f"`{project}-budgets`" in refused.reason
    assert "`plan-budgets` template" in refused.reason
    assert "config/budgets-migration.yaml" in refused.reason


@pytest.mark.parametrize(
    ("answers", "field", "said"),
    [
        (
            _broken(checklist={"budget": "", "not_applicable": ""}),
            "checklist",
            "checklist entry 1 (latency) answers neither of `budget` and `not_applicable`",
        ),
        (
            _broken(checklist={"not_applicable": "n/a because it is fast"}),
            "checklist",
            "checklist entry 1 (latency) answers both of `budget` and `not_applicable`",
        ),
        (
            _broken(checklist={"budget": "nothing-by-this-id"}),
            "checklist",
            "names the budget 'nothing-by-this-id', which no entry of `budgets` has",
        ),
        (
            _broken(budgets=ANSWERS["budgets"] * 2),
            "budgets",
            "the budget id(s) 'listing-latency' repeat",
        ),
        (
            _broken(budgets={"node": "no-such-node"}),
            "budgets",
            "owned by the node 'no-such-node', which is no node of the plan",
        ),
        (
            _broken(budgets={"file_change": "replace"}),
            "budgets",
            "has the `file_change` 'replace', which is not one of add, change, none",
        ),
        (
            _broken(budgets={"file": "apps/web/budgets.toml"}),
            "budgets",
            "goes in the file 'apps/web/budgets.toml', which is not a relative path",
        ),
        (
            _broken(budgets={"file": "/repo/budgets.yaml"}),
            "budgets",
            "goes in the file '/repo/budgets.yaml', which is not a relative path",
        ),
        (
            _broken(budgets={"file": "apps/../budgets.yaml"}),
            "budgets",
            "goes in the file 'apps/../budgets.yaml', which is not a relative path",
        ),
        (
            _broken(budgets={"direction": "below"}),
            "budgets",
            "has the `direction` 'below', which is not max or min",
        ),
        (
            _broken(budgets={"command": "  "}),
            "budgets",
            "names no `command`, and the command is what performs the measurement",
        ),
        (
            _broken(repo_wide_effects=[]),
            "repo_wide_effects",
            f"`repo_wide_effects` states no effect for {REPOSITORY}",
        ),
    ],
    ids=[
        "neither",
        "both",
        "unknown-budget",
        "repeated-id",
        "unknown-node",
        "file-change",
        "not-budgets-yaml",
        "absolute-file",
        "dot-dot-file",
        "direction",
        "empty-command",
        "repo-wide-effects",
    ],
)
def test_each_rule_is_refused_naming_the_document_and_the_rule(
    answers: Record, field: str, said: str
) -> None:
    project = _plan(answers)

    found = plan_budgets.refusals(project, _loaded(project))

    assert [refused.field for refused in found] == [field], found
    (refused,) = found
    assert refused.reason.startswith(f"{project}-budgets: "), refused.reason
    assert said in refused.reason, refused.reason


@pytest.mark.parametrize(
    "path",
    ["apps\\web\\budgets.yaml", "", "budgets.yaml/", "apps//budgets.yaml", "./budgets.yaml"],
    ids=["backslashes", "empty", "trailing-slash", "empty-component", "dot-component"],
)
def test_a_file_that_is_not_a_plain_relative_budgets_yaml_path_is_refused(path: str) -> None:
    (refused,) = plan_budgets.rule_refusals(
        plan_budgets.parse(_broken(budgets={"file": path})), PLAN
    )

    assert f"goes in the file {path!r}, which is not a relative path" in refused.reason


def test_a_budget_with_no_id_is_refused_by_its_position() -> None:
    answers = _broken(budgets={"id": "  "}, checklist={"budget": "", "not_applicable": "n/a"})

    found = plan_budgets.rule_refusals(plan_budgets.parse(answers), PLAN)

    assert [refused.reason for refused in found] == [
        "budget 1 has no `id`, and every budget is named by one"
    ]


def test_a_repository_named_by_alias_is_compared_as_written() -> None:
    plan = {**PLAN, "tasks": [{**PLAN["tasks"][0], "repo": "ai-orchestrator-isolated"}]}
    effects = [{"repository": "ai-orchestrator-isolated", "budget": "", "effect": "none"}]

    stated = plan_budgets.rule_refusals(
        plan_budgets.parse(_broken(repo_wide_effects=effects)), plan
    )
    unstated = plan_budgets.rule_refusals(plan_budgets.parse(_broken(repo_wide_effects=[])), plan)
    unsaid = [{**effects[0], "effect": " "}]
    silent = plan_budgets.rule_refusals(plan_budgets.parse(_broken(repo_wide_effects=unsaid)), plan)

    assert stated == []
    assert [refused.reason for refused in silent] == [refused.reason for refused in unstated]
    assert [refused.reason for refused in unstated] == [
        "`repo_wide_effects` states no effect for ai-orchestrator-isolated, a repository the "
        "plan's tasks change; each one gets an entry, with the effect `none` where it has none"
    ]


def test_a_task_naming_no_repository_asks_for_no_repo_wide_effect() -> None:
    human = {"id": "sign-off", "kind": "human", "task": "Sign it off."}
    direct = {"id": "notes", "repo": "", "task": "Write the notes."}
    plan = {**PLAN, "tasks": [*PLAN["tasks"], human, direct]}

    assert plan_budgets.rule_refusals(plan_budgets.parse(ANSWERS), plan) == []


def test_a_plan_with_no_readable_tasks_is_judged_rather_than_crashed_on() -> None:
    answers = plan_budgets.parse(ANSWERS)

    assert [refused.field for refused in plan_budgets.rule_refusals(answers, {"tasks": None})] == [
        "budgets"
    ]
    assert len(plan_budgets.rule_refusals(answers, None)) == 1
    assert plan_budgets.owned(None) == {}


@pytest.mark.parametrize(
    ("record", "said"),
    [
        (None, "does not hold exactly the 7 answers"),
        ({**ANSWERS, "extra": "x"}, "does not hold exactly the 7 answers"),
        ({**ANSWERS, "workload": 2000}, "the `workload` answer is not text"),
        ({**ANSWERS, "checklist": "latency"}, "the `checklist` answer is not a list of objects"),
        ({**ANSWERS, "checklist": ["latency"]}, "`checklist` entry 1 does not hold exactly"),
        (
            _broken(checklist={"owner": "me"}),
            "`checklist` entry 1 does not hold exactly the keys concern, budget, not_applicable",
        ),
        (
            _broken(budgets={"threshold": "800"}),
            "`budgets` entry 1 holds '800' as its `threshold`, which is not a finite number",
        ),
        (
            _broken(budgets={"threshold": True}),
            "`budgets` entry 1 holds True as its `threshold`, which is not a finite number",
        ),
        (
            {**ANSWERS, "budgets": [{**ANSWERS["budgets"][0], "threshold": float("nan")}]},
            "`budgets` entry 1 holds nan as its `threshold`, which is not a finite number",
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
        "threshold-text",
        "threshold-bool",
        "threshold-nan",
        "effect-not-text",
        "unknown-data-choice",
    ],
)
def test_a_record_of_any_other_shape_is_refused_naming_what_is_malformed(
    record: object, said: str
) -> None:
    with pytest.raises(plan_budgets.BudgetsError, match=re.escape(said)):
        plan_budgets.parse(record)


def _store_document(project: str, answers: Record = ANSWERS) -> plan_store.StoreDocument:
    return budgets_document(project, answers)


def test_a_document_recording_no_rendering_of_the_template_is_refused() -> None:
    held = _store_document("test-fixtures:probe")
    unrendered = plan_store.StoreDocument(**{**held.__dict__, "metadata": {}})

    with pytest.raises(
        plan_budgets.BudgetsError, match="records no rendering of the `plan-budgets`"
    ):
        plan_budgets.answers_of(unrendered)


def test_a_document_edited_after_it_was_rendered_is_refused() -> None:
    held = _store_document("test-fixtures:probe")
    edited = plan_store.StoreDocument(**{**held.__dict__, "content": held.content + "\nmore"})

    with pytest.raises(plan_budgets.BudgetsError, match="edited after it was rendered"):
        plan_budgets.answers_of(edited)


@pytest.mark.parametrize(
    "record",
    ["{not json", json.dumps({"workload": "only one answer"}), json.dumps(["a", "list"])],
    ids=["not-json", "missing-answers", "not-an-object"],
)
def test_a_document_whose_record_is_not_the_seven_answers_is_refused(record: str) -> None:
    content = f"## Workload\n\nx\n\n## Record\n\n```json\n{record}\n```\n"
    digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    held = _store_document("test-fixtures:probe")
    stale = plan_store.StoreDocument(
        **{
            **held.__dict__,
            "content": content,
            "metadata": {
                "onetaskgraph.template": {
                    "template": plan_budgets.TEMPLATE_REFERENCE,
                    "body_digest": digest,
                }
            },
        }
    )

    with pytest.raises(plan_budgets.BudgetsError, match="carries no `## Record` holding the 7"):
        plan_budgets.answers_of(stale)


def test_a_project_holding_two_budgets_documents_is_refused_naming_both() -> None:
    project = "test-fixtures:probe"
    first = _store_document(project)
    other = plan_store.StoreDocument(
        **{**first.__dict__, "qualified_id": plan_store.QualifiedDocumentId(f"{project}-more")}
    )

    with pytest.raises(plan_budgets.BudgetsError, match="holds 2 budgets documents"):
        plan_budgets.find(project, [first, other])
    assert plan_budgets.find(project, []) is None


def test_the_budgets_document_is_never_taken_for_the_design_document() -> None:
    """A project holds both, and the approval reads the one that is not the budgets one."""
    project = _plan()
    native = plan_store.qualified(project).native
    designed = _design_document(native)

    held = plan_store.read_documents(project)

    assert len(held) == 2
    assert design_approval.one_document(project, held).qualified_id == designed


def _design_document(native: str) -> str:
    from project_fixtures import designed

    designed(plan_fixture_source.SOURCE, native, [])
    return f"{plan_fixture_source.SOURCE}:{native}-design"


def test_an_unreadable_document_is_refused_by_the_check_and_keyed_as_none(tmp_path: Path) -> None:
    project = _plan(None)
    native = plan_store.qualified(project).native
    # A hand-written document under the budgets document's id is not a rendering of it.
    body = tmp_path / "hand-written.md"
    body.write_text("Every budget is fine.\n", encoding="utf-8")
    created = subprocess.run(
        [str(ONETASKGRAPH_BIN), "document", "create", plan_fixture_source.SOURCE]
        + ["--project", native, "--title", f"Budgets: {native}"]
        + ["--id", f"{native}{plan_budgets.DOCUMENT_SUFFIX}", "--body-file", str(body)]
        + ["--no-interactive"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr

    (refused,) = plan_budgets.refusals(project, _loaded(project))
    assert "records no rendering of the `plan-budgets` template" in refused.reason
    assert plan_budgets.readable(project) is None
    assert plan_budgets.readable(None) is None


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


def test_the_plan_kinds_design_approval_exempts_are_exempt_here_on_the_same_bound() -> None:
    project = _plan(None)
    stamp = {"kind": design_approval.PLANNING, "nodes": ["listing"]}
    written = subprocess.run(
        [str(ONETASKGRAPH_BIN), "project", "metadata", "set", project]
        + [design_approval.PLAN_KIND, json.dumps(stamp)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    loaded = _loaded(project)

    assert plan_budgets.exempt_kind(project, {"listing"}) == design_approval.PLANNING
    assert plan_budgets.refusals(project, loaded) == []
    # A project holding a node its stamp does not claim is a plan like any other.
    assert plan_budgets.exempt_kind(project, {"listing", "more"}) is None
    held = loaded["tasks"]
    assert isinstance(held, list)
    grown = {**loaded, "tasks": [*held, {"id": "more"}]}
    assert [refused.field for refused in plan_budgets.refusals(project, grown)] == ["budgets"]


def test_a_migration_list_the_check_cannot_read_is_a_refusal_rather_than_an_exemption(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _plan(None)
    monkeypatch.setattr(plan_budgets, "MIGRATION_LIST", tmp_path / "absent.yaml")

    (refused,) = plan_budgets.refusals(project, _loaded(project))
    assert "budgets could not be read" in refused.reason
    assert "absent.yaml" in refused.reason


def test_the_writers_context_is_the_documents_path_or_the_lists_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _plan()

    assert plan_budgets.main([project]) == 0
    stated = json.loads(capsys.readouterr().out)
    path = stated.pop("path")
    assert stated == {"predates": "", "document": f"{project}-budgets"}
    # The writer is pointed at the document's file rather than handed its answers, and that
    # file records exactly the answers the document renders.
    recorded = re.search(
        r"\n## Record\n\n```json\n([^\n]*)\n```", Path(path).read_text(encoding="utf-8")
    )
    assert Path(path).is_absolute() and recorded is not None
    assert json.loads(recorded.group(1)) == ANSWERS

    assert plan_budgets.main(["authoring:approved-budgets"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["document"] == "" and listed["path"] == ""
    assert listed["predates"] == plan_budgets.migrated("authoring:approved-budgets")

    bare = _plan(None)
    assert plan_budgets.main([bare]) == 2
    assert f"{bare} carries no `{bare}-budgets` document" in capsys.readouterr().err


def test_a_budgets_document_that_is_no_file_here_is_refused_for_the_writer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The writer is pointed at a file it can read, or at nothing: a board copy is refused.

    The store answers the document as a board does — rendered, with no local location —
    so its location falls back to the document's id, which no reader can open.
    """
    project = "plans:board-plan"
    monkeypatch.setattr(
        plan_store, "read_documents", lambda named: [budgets_document(project, ANSWERS)]
    )
    assert plan_budgets.main([project]) == 2
    refused = capsys.readouterr().err
    assert f"{project}-budgets is not a file on this host" in refused, refused
    assert "local Markdown source" in refused, refused


def test_the_fixture_answers_for_a_plan_needing_no_budget_break_no_rule() -> None:
    project = _plan(no_budgets([REPOSITORY]))

    assert plan_budgets.refusals(project, _loaded(project)) == []


@pytest.mark.reads_docs
def test_the_budgets_doc_names_the_vocabularies_the_check_holds_answers_to() -> None:
    """`docs/budgets.md`'s copy of the file name and the directions is held to the check's."""
    text = " ".join((REPO_ROOT / "docs" / "budgets.md").read_text(encoding="utf-8").split())

    assert f"named `{plan_budgets.BUDGETS_FILE}`" in text
    assert " or ".join(f"`{direction}`" for direction in plan_budgets.Direction) in text


def test_a_budgets_document_leaves_a_planning_exemption_standing_where_a_design_one_ends_it() -> (
    None
):
    """The design-approval gate counts design documents, and a budgets document is not one.

    A planning launch's project is exempt from design approval while it holds exactly the
    nodes its stamp names and no design document. A budgets document beside it is no design
    document, so the exemption stands; the design document arriving is what ends it.
    """
    project = _plan()
    stamp = {"kind": design_approval.PLANNING, "nodes": ["listing"]}
    written = subprocess.run(
        [str(ONETASKGRAPH_BIN), "project", "metadata", "set", project]
        + [design_approval.PLAN_KIND, json.dumps(stamp)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    assert plan_budgets.read(project) is not None

    held = design_approval.assess(project)
    assert held.refusal is None, held.refusal
    assert held.exemption is not None and "and no design document yet" in held.exemption

    _design_document(plan_store.qualified(project).native)
    ended = design_approval.assess(project)
    assert ended.exemption is None
    assert ended.refusal is not None
    assert "it holds 1 design document(s), so there is something to read" in ended.refusal


def test_a_stored_budgets_document_edited_after_rendering_is_refused_by_the_check() -> None:
    """Through the store and the check: a body edited on disk is no rendering of its answers."""
    project = _plan()
    native = plan_store.qualified(project).native
    stored = plan_fixture_source.root() / "documents" / f"{native}-budgets.md"
    stored.write_text(stored.read_text(encoding="utf-8") + "\nAnd one more budget.\n")

    (refused,) = plan_budgets.refusals(project, _loaded(project))

    assert "edited after it was rendered" in refused.reason, refused.reason


def test_a_stored_rendering_whose_record_breaks_the_shape_is_refused_by_the_check() -> None:
    """The store holds an object answer to no key list, so the check is what refuses one."""
    project = _plan(_broken(budgets={"owner": "the listing team"}))

    (refused,) = plan_budgets.refusals(project, _loaded(project))

    assert "`budgets` entry 1 does not hold exactly the keys" in refused.reason, refused.reason


def test_a_project_storing_two_budgets_renderings_is_refused_by_the_check() -> None:
    project = _plan()
    native = plan_store.qualified(project).native
    budgeted(plan_fixture_source.SOURCE, f"{native}-again", ANSWERS)
    again = plan_fixture_source.root() / "documents" / f"{native}-again-budgets.md"
    # Filed under this plan's project, as a second rendering of the template beside the first.
    again.write_text(
        again.read_text(encoding="utf-8").replace(
            f"project: {native}-again\n", f"project: {native}\n"
        )
    )

    (refused,) = plan_budgets.refusals(project, _loaded(project))

    assert "holds 2 budgets documents" in refused.reason, refused.reason
