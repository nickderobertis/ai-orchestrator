"""The two ends of the plug-in this repository's plan checks reach a launch through.

`onepipeline plan check` runs the engine's own loader and then hands each registered
check the loaded plan on stdin. `orchestrator/plan_check.py` is both sides of that: the
check the verb spawns, and the wrapper `just check-plan` is that registers it.

`tests/plan_tooling/test_check_plan_recipe_e2e.py` drives both through the real recipe against
the real engine. What is here is what a journey cannot state: the answers a verb of some
*other* shape would give — one that grew a field, dropped one, exited on a code nothing
names, or answered nothing at all — because this checkout installs exactly one engine
and it answers exactly one way.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from criteria_examples import RELEASED_ELSEWHERE

from orchestrator import criteria_guard, plan_check, plan_review, plan_store, task_body

#: A synthetic appendix, for the reason `tests/test_criteria_guard.py` states: reading
#: the tracked one would put these in the whole-workspace tier, where coverage is not
#: measured, and its wording is the marked journeys' to hold. What is under test here is
#: the plug-in, so the file it reads is this test's to write.
APPENDIX = "## Additional info\n\n### Operational notes\n\nWork the branch and report.\n"

COMPLETE = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as "
    "it finally stands."
)


@pytest.fixture(autouse=True)
def appendix(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the appendix check at the synthetic one every task here carries."""
    written = tmp_path_factory.mktemp("appendix") / "appendix.md"
    written.write_text(APPENDIX, encoding="utf-8")
    monkeypatch.setattr(criteria_guard, "APPENDIX", written)


@pytest.fixture(autouse=True)
def project_record(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The project record the store answers for every project here.

    Stood in at the store CLI, which is the one boundary the plan-level record crosses
    into this module: it starts carrying no record, and `_planned` writes the key for a
    document into it the way `just review-plan` would. A test about a task's own refusal
    plans its document first, so the one refusal it reads is the one it is about.
    """
    record: dict[str, object] = {"metadata": {}}
    monkeypatch.setattr(plan_store, "project_record", lambda _project: record)
    return record


def _planned(record: dict[str, object], document: dict[str, object]) -> dict[str, object]:
    """``document``, once ``record`` carries a plan-level record for exactly it."""
    key = plan_review.plan_key(document, plan_review.plan_bar_fingerprint())
    record["metadata"] = {plan_review.RECORD_KEY: {"key": key, "by": plan_review.BY_REVIEW}}
    return document


def _task(criteria: str = COMPLETE) -> str:
    return f"## What\n\nAdd it.\n\n## Acceptance criteria\n\n{criteria}\n\n{APPENDIX}"


def _node(**fields: object) -> dict[str, object]:
    node: dict[str, object] = {
        "id": "route",
        "persona": "engineer",
        "title": "feat: add the route",
        "task": _task(),
        "metadata": {"onepipeline.id": "route", "onepipeline.persona": "engineer"},
    }
    node.update(fields)
    return node


def _reviewed(node: dict[str, object]) -> dict[str, object]:
    """``node`` carrying a record for exactly the content it states."""
    record = plan_check._task_record(node)
    key = plan_review.review_key(record, plan_review.bar_fingerprint())
    metadata = dict(record.metadata)
    metadata[plan_review.RECORD_KEY] = {"key": key}
    return _node(**{**node, "metadata": metadata})


def _document(*tasks: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 3, "name": "probe", "tasks": list(tasks)}


def test_a_reviewed_and_complete_plan_earns_no_refusal(project_record: dict[str, object]) -> None:
    document = _planned(project_record, _document(_reviewed(_node())))

    assert plan_check.refusals(document, "authoring:probe") == []
    assert plan_check.dispatched(document) == 1


def test_a_plan_every_task_of_which_is_recorded_is_refused_for_want_of_the_plan_level_record(
    project_record: dict[str, object],
) -> None:
    """The one refusal no task's own record answers, against the plan rather than a node.

    Every task carries a record and the project carries none: nothing has read the plan
    whole for the adoption its goal needs, so the refusal names the plan, the `metadata`
    field the record lives in, and the command that records one.
    """
    document = _document(_reviewed(_node()))
    assert project_record == {"metadata": {}}

    (refusal,) = plan_check.refusals(document, "authoring:probe")

    assert refusal["node"] is None
    assert refusal["field"] == "metadata"
    assert "no plan-level review record" in refusal["reason"]
    assert "just review-plan authoring:probe" in refusal["reason"]

    # And a record for a *different* plan is no record for this one: the goal moved.
    _planned(project_record, {**document, "goal": {"text": "Something else"}})
    (still,) = plan_check.refusals(document, "authoring:probe")
    assert "no plan-level review record" in still["reason"]

    _planned(project_record, document)
    assert plan_check.refusals(document, "authoring:probe") == []


def test_a_check_handed_no_project_id_refuses_rather_than_passes(
    monkeypatch: pytest.MonkeyPatch, project_record: dict[str, object]
) -> None:
    """The record lives on the project, which the loaded plan does not carry.

    So a check that cannot read it — because nothing named the project — refuses and
    says why, rather than accepting a plan whose plan-level review it could not see; an
    accept for want of the check's own environment would be the escape hatch this gate
    has none of. The store is not asked at all, because there is nothing to ask it by.
    """
    document = _planned(project_record, _document(_reviewed(_node())))
    monkeypatch.setattr(
        plan_store,
        "project_record",
        lambda _project: pytest.fail("the store was asked for a project nothing named"),
    )

    (refusal,) = plan_check.refusals(document, plan_check.UNNAMED_PROJECT)

    assert refusal["node"] is None
    assert refusal["field"] == "metadata"
    assert "for want of a project id" in refusal["reason"]
    assert plan_check.PROJECT_ENV in refusal["reason"]
    assert f"just review-plan {plan_check.UNNAMED_PROJECT}" in refusal["reason"]


def test_a_project_record_the_store_cannot_answer_is_refused_naming_what_it_said(
    monkeypatch: pytest.MonkeyPatch, project_record: dict[str, object]
) -> None:
    """Refused rather than raised: the store just loaded this plan, so its silence about
    the project is a fact about the plan-level record and never an accept."""
    document = _planned(project_record, _document(_reviewed(_node())))
    monkeypatch.setattr(
        plan_store,
        "project_record",
        lambda _project: (_ for _ in ()).throw(OSError("the store answered nothing")),
    )

    (refusal,) = plan_check.refusals(document, "authoring:probe")

    assert refusal["node"] is None
    assert refusal["field"] == "metadata"
    assert "the store answered nothing" in refusal["reason"]
    assert "just review-plan authoring:probe" in refusal["reason"]


def test_a_node_whose_criteria_rest_outside_its_dispatch_is_refused_against_its_task(
    project_record: dict[str, object],
) -> None:
    """A criteria refusal arrives on the `task` field, whatever the criteria did wrong.

    Driven over a criterion about somebody else's released artifact because that is a
    refusal this tier makes rather than one it hands on: a version literal and criteria
    silent about a demand their bar makes are the judged turn's now, so a plug-in journey
    written over either would be measuring a refusal nothing here can make.
    """
    outside = f"{COMPLETE}\n{RELEASED_ELSEWHERE[0]}"
    document = _planned(project_record, _document(_reviewed(_node(task=_task(outside)))))
    (refusal,) = plan_check.refusals(document, "s:p")

    assert refusal["node"] == "route"
    assert refusal["field"] == "task"
    # The node id is the refusal's own field, so the reason does not open with it again.
    assert not refusal["reason"].startswith("route: ")
    assert "the dispatch cannot do" in refusal["reason"]


def _with_own_notes(heading: str) -> str:
    """A task whose author wrote notes of their own under ``heading``, above the appendix."""
    return (
        f"## What\n\nAdd it.\n\n## Acceptance criteria\n\n{COMPLETE}\n\n"
        f"{heading}\n\nThe change request may be published early.\n\n{APPENDIX}"
    )


@pytest.mark.parametrize(
    "heading",
    (
        "## Additional info for this node",
        "## Additional information",
        "## additional info",
        "##Additional info",
    ),
)
def test_a_task_opening_its_notes_under_a_longer_heading_is_refused_naming_the_one_to_write(
    project_record: dict[str, object], heading: str
) -> None:
    """The live-edit check reads a task's own grants under exactly one heading line.

    So a task written under any other spelling of it launches with grants a retry
    restating it would lose; both plan-tier paths refuse it, naming the heading as
    written, the heading to write, and what a retry would lose.
    """
    document = _planned(project_record, _document(_reviewed(_node(task=_with_own_notes(heading)))))

    (refusal,) = plan_check.refusals(document, "s:p")

    assert refusal["node"] == "route"
    assert refusal["field"] == "task"
    assert repr(heading) in refusal["reason"], refusal["reason"]
    assert "exactly `## Additional info`" in refusal["reason"], refusal["reason"]
    assert "a `retry` or `requeue` restating this task loses every grant" in refusal["reason"]
    with pytest.raises(criteria_guard.CriteriaError, match="exactly `## Additional info`"):
        criteria_guard.check_plan(document)


@pytest.mark.parametrize(
    "heading", ("## Additional info", "## Additional info  ", "### Additional info for this node")
)
def test_a_task_opening_its_notes_under_the_exact_heading_is_not_refused_for_it(
    project_record: dict[str, object], heading: str
) -> None:
    """The live tier reads trailing blanks as the heading, and a level-3 heading is not one."""
    document = _planned(project_record, _document(_reviewed(_node(task=_with_own_notes(heading)))))

    assert plan_check.refusals(document, "s:p") == []
    assert criteria_guard.check_plan(document) == 1


def test_a_task_whose_issue_body_would_exceed_the_boards_limit_is_refused_against_its_task(
    project_record: dict[str, object],
) -> None:
    """The size refusal reaches the verb the way every other guard's does.

    Measured over the document's own `task` and `metadata`, which is the body the copy
    would send: the padding sits below the appendix, so the criteria are sound and the
    one refusal is about the size.
    """
    padded = _task() + "\n## Planner context\n\n" + "Carried context. " * 4_000
    document = _planned(project_record, _document(_reviewed(_node(task=padded))))

    (refusal,) = plan_check.refusals(document, "s:p")

    assert refusal["node"] == "route"
    assert refusal["field"] == "task"
    assert f"{task_body.BODY_LIMIT:,}-character limit" in refusal["reason"]
    assert task_body.UNMEASURED in refusal["reason"]


def test_a_node_whose_persona_cannot_resolve_is_refused_against_its_persona(
    project_record: dict[str, object],
) -> None:
    node = _reviewed(_node(persona="../../elsewhere.yaml"))
    (refusal,) = plan_check.refusals(_planned(project_record, _document(node)), "s:p")

    assert refusal["field"] == "persona"
    assert "outside this checkout" in refusal["reason"]


def test_a_task_nothing_has_reviewed_is_refused_and_names_the_command(
    project_record: dict[str, object],
) -> None:
    document = _planned(project_record, _document(_node()))
    (refusal,) = plan_check.refusals(document, "authoring:probe")

    assert refusal["node"] == "route"
    assert refusal["field"] == "metadata"
    assert "no review record" in refusal["reason"]
    assert "just review-plan authoring:probe" in refusal["reason"]


def test_a_plan_shape_this_check_cannot_walk_is_one_refusal_about_the_plan() -> None:
    """The engine's loader refuses these first, so reaching one means the two disagree.

    Reported as a refusal about the plan rather than raised, because a traceback would
    be read as a broken check — which is a different thing from a plan to fix.
    """
    malformed = {"tasks": [{"id": "route", "kind": "review"}]}
    refusal, *rest = plan_check.refusals(malformed, "s:p")

    assert refusal["node"] is None
    assert refusal["field"] is None
    assert "`kind` is 'review'" in refusal["reason"]
    # The review records are still read, at both levels: a plan this cannot walk is one
    # whose criteria nothing has read either, and reporting only the first would hide
    # the second.
    assert [(one["node"], one["field"]) for one in rest] == [
        ("route", "metadata"),
        (None, "metadata"),
    ]
    assert plan_check.dispatched(malformed) == 0


@pytest.mark.parametrize(
    "document",
    (
        pytest.param("not a plan", id="not-an-object"),
        pytest.param({"tasks": "route"}, id="tasks-not-a-list"),
    ),
)
def test_a_document_carrying_no_tasks_at_all_reviews_nothing(document: object) -> None:
    assert list(plan_check._review_refusals(document, "s:p")) == []


def test_a_task_record_narrows_every_field_the_document_may_be_missing() -> None:
    """A review key is computed over fields the loaded plan need not carry."""
    empty = plan_check._task_record("not a task")

    assert empty.title == ""
    assert empty.content is None
    assert empty.metadata == {}
    assert empty.deps == ()

    # An id that is not a string is left empty rather than rendered into one: the
    # engine's loader refuses a node without one, so a record addressed as `"None"`
    # would name a node no plan contains.
    assert plan_check._task_record({"id": 7}).node_id == ""

    read = plan_check._task_record({"id": "a", "deps": ["b", 7], "task": "x", "title": "T"})
    assert read.deps == ("b",)
    assert read.content == "x"
    assert read.repositories == []

    # The document's resolved `repo` is carried as the record's one repository, so the
    # key's `repo` reads the same value the store path reads off `repositories`.
    hosted = plan_check._task_record({"id": "a", "repo": "github.com/o/n", "task": "x"})
    assert plan_review.repository_of(hosted) == "github.com/o/n"
    assert plan_review.repository_of(plan_check._task_record({"id": "a", "repo": 7})) is None


def test_the_check_answers_its_contract_on_stdin(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_record: dict[str, object],
) -> None:
    """Stdout carries the whole answer, and nothing else is written anywhere."""
    monkeypatch.setenv(plan_check.PROJECT_ENV, "authoring:probe")
    document = _planned(project_record, _document(_reviewed(_node())))
    monkeypatch.setattr("sys.stdin", _Reader(json.dumps(document)))

    assert plan_check.answer_on_stdin() == 0
    read = capsys.readouterr()
    assert json.loads(read.out) == {"refusals": []}
    assert read.err == ""


def test_a_plan_that_is_not_json_could_not_be_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", _Reader("{bad"))

    assert plan_check.answer_on_stdin() == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_a_checkout_that_cannot_answer_what_the_bar_is_could_not_be_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit non-zero rather than accept: "this repository cannot say" is not "fine"."""
    document = json.dumps(_document(_reviewed(_node())))
    monkeypatch.setattr(
        plan_review, "bar_fingerprint", lambda *_: (_ for _ in ()).throw(OSError("no such file"))
    )
    monkeypatch.setattr("sys.stdin", _Reader(document))

    assert plan_check.answer_on_stdin() == 2
    assert "own review configuration" in capsys.readouterr().err


class _Reader:
    """The one attribute `json.load` uses, so stdin can be a string in a unit test."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self, *_: object) -> str:
        return self._text


@pytest.mark.parametrize(
    ("refusal", "rendered"),
    (
        pytest.param({"reason": 7}, None, id="no-reason"),
        pytest.param("engine refused", None, id="not-an-object"),
        pytest.param({"reason": "why"}, "check-plan: why", id="reason-alone"),
        pytest.param(
            {"source": "engine", "node": "a", "field": "task", "reason": "why"},
            "check-plan: engine: a: task: why",
            id="whole",
        ),
        pytest.param(
            {"source": "engine", "node": None, "field": "", "reason": "why"},
            "check-plan: engine: why",
            id="plan-wide",
        ),
    ),
)
def test_a_refusal_of_the_verbs_answer_renders_what_it_carries(
    refusal: object, rendered: str | None
) -> None:
    assert plan_check.rendered_refusal(refusal) == rendered


@pytest.mark.parametrize(
    ("entry", "rendered"),
    (
        pytest.param("broken", None, id="not-an-object"),
        pytest.param(
            {"check": "c.sh", "exit_code": 3, "stderr": "boom"},
            "check-plan: c.sh could not be run (exit 3): boom",
            id="exited",
        ),
        pytest.param(
            {"check": "c.sh", "exit_code": None, "stderr": ""},
            "check-plan: c.sh could not be run: it reported nothing",
            id="never-started",
        ),
        pytest.param(
            {"exit_code": 1, "stderr": "boom"},
            "check-plan: a registered check could not be run (exit 1): boom",
            id="unnamed",
        ),
        pytest.param(
            {"check": "c.sh", "exit_code": "killed", "stderr": "boom"},
            "check-plan: c.sh could not be run: boom",
            id="code-is-not-a-number",
        ),
        pytest.param(
            {"check": "c.sh", "exit_code": True, "stderr": "boom"},
            "check-plan: c.sh could not be run: boom",
            id="code-is-a-boolean",
        ),
    ),
)
def test_an_unrunnable_check_renders_what_it_carries(entry: object, rendered: str | None) -> None:
    """Every field of another program's answer is narrowed at the read.

    The last two are why the exit code is narrowed rather than interpolated: this is a
    JSON document some other build of the verb wrote, so a code that is not a whole
    number is a value this sentence cannot describe. Dropping it keeps the check's name
    and what it reported — the two things an operator acts on — where `(exit killed)`
    and `(exit True)` would each read as a status somebody could look up.
    """
    assert plan_check.rendered_unrunnable(entry) == rendered


@pytest.mark.parametrize(
    ("named", "runnable"),
    (
        pytest.param("onepipeline", True, id="bare-name-on-the-path"),
        pytest.param("nothing-this-host-has", False, id="bare-name-nothing-has"),
        pytest.param("/nowhere/onepipeline", False, id="path-to-nothing"),
        # A directory carries the execute bit for traversal, so a permission check
        # alone calls one runnable and the spawn then fails as though the engine had
        # refused the verb.
        pytest.param("/tmp", False, id="path-to-a-directory"),
    ),
)
def test_an_engine_named_in_the_environment_is_checked_before_it_is_spawned(
    named: str, runnable: bool
) -> None:
    """A value this cannot spawn must not read as an engine that refused the verb.

    Caught before the probe rather than after it: a spawn failure and a refusal are the
    same exit from outside, and reading one as the other takes the narrower path over a
    plan the operator meant to have checked whole.
    """
    assert plan_check._runnable(named) is runnable


def test_the_command_refuses_an_engine_the_environment_names_but_cannot_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(plan_check.ENGINE_ENV, "/nowhere/onepipeline")

    assert plan_check.main(["s:p"]) == 2
    reported = capsys.readouterr().err
    assert plan_check.ENGINE_ENV in reported
    assert "not an executable" in reported


@pytest.mark.parametrize(
    "named",
    (pytest.param(None, id="unset"), pytest.param("not-qualified", id="not-a-project-id")),
)
def test_a_project_that_is_not_a_qualified_id_is_not_put_into_a_command_to_run(
    named: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The value reaches a refusal's own prose, so it names a command that could work.

    Both refusals — the task's, and the plan-level one a check handed no project id
    makes — name the placeholder, and neither carries the value itself.
    """
    if named is None:
        monkeypatch.delenv(plan_check.PROJECT_ENV, raising=False)
    else:
        monkeypatch.setenv(plan_check.PROJECT_ENV, named)

    task, whole = plan_check.refusals(
        _document(_node()), plan_check._named_project(os.environ.get(plan_check.PROJECT_ENV))
    )

    assert task["node"] == "route"
    assert whole["node"] is None
    for refusal in (task, whole):
        assert plan_check.UNNAMED_PROJECT in refusal["reason"]
        assert "not-qualified" not in refusal["reason"]


def test_the_engine_is_taken_from_the_environment_before_the_search_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(plan_check.ENGINE_ENV, "/named/onepipeline")
    assert plan_check.plan_check_engine() == "/named/onepipeline"

    monkeypatch.delenv(plan_check.ENGINE_ENV)
    monkeypatch.setattr(plan_check.shutil, "which", lambda _: "/found/onepipeline")
    assert plan_check.plan_check_engine() == "/found/onepipeline"

    monkeypatch.setattr(plan_check.shutil, "which", lambda _: None)
    assert plan_check.plan_check_engine() is None


def _completed(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_an_engine_is_asked_whether_it_carries_the_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asked of the binary rather than of a pin, which describes a release not a host."""
    asked: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        asked.append(command)
        return _completed(0 if command[0] == "/new" else 2)

    monkeypatch.setattr(plan_check.subprocess, "run", run)

    assert plan_check.carries_plan_check("/new") is True
    assert plan_check.carries_plan_check("/old") is False
    assert asked[0][1:] == ["plan", "check", "--help"]


ACCEPTED = {"project": "s:p", "accepted": True, "refusals": [], "unrunnable": []}


def _verb(monkeypatch: pytest.MonkeyPatch, code: int, answer: object, stderr: str = "") -> None:
    monkeypatch.setattr(
        plan_check.subprocess,
        "run",
        lambda *_, **__: _completed(code, json.dumps(answer), stderr),
    )


def test_an_accepted_plan_reports_the_path_that_read_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _verb(monkeypatch, 0, ACCEPTED)
    monkeypatch.setattr(
        plan_check, "dispatched_in_with_records", lambda _: (criteria_guard.Counted(3), [])
    )

    assert plan_check.check_through_engine("s:p", "/new/onepipeline") == 0
    read = capsys.readouterr().out
    assert criteria_guard.THROUGH_ENGINE in read
    assert "3 dispatched node(s)" in read


def test_a_plan_the_engine_accepted_is_counted_by_re_reading_its_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count comes from the store, because the check has no channel back to here.

    The only one a spawned check would have is a file, and a file whose path this
    command hands over in the environment is a write any symlink on the way to it can
    redirect — so there is no side channel, and this reads the project for itself.
    """
    monkeypatch.setattr(
        plan_store,
        "read_project",
        lambda project: (
            {"tasks": [_node(), {"id": "approve", "kind": "human", "task": "Approve."}]},
            [],
        ),
    )

    assert plan_check.dispatched_in_with_records("s:p") == (criteria_guard.Counted(1), [])


def test_a_task_between_the_thresholds_is_warned_about_on_stderr_and_still_accepted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The warning is the wrapper's, because the verb swallows an accepting check's stderr.

    Read off the records the wrapper re-reads for its count, so the map measured is the
    store's own — the review record included — and printed before the accepted line.
    """
    _verb(monkeypatch, 0, ACCEPTED)
    warned = plan_store.StoreTask(
        qualified_id=plan_store.QualifiedTaskId("s:p/route"),
        node_id=plan_store.NodeId("route"),
        title="feat: add the route",
        content="x" * task_body.WARN_FROM,
        metadata={"onepipeline.id": "route"},
        repositories=[],
        deps=(),
    )
    monkeypatch.setattr(plan_store, "read_project", lambda _: ({"tasks": [_node()]}, [warned]))

    assert plan_check.check_through_engine("s:p", "/new/onepipeline") == 0
    captured = capsys.readouterr()
    assert captured.err.startswith("check-plan: warning: route: "), captured.err
    assert f"{task_body.BODY_LIMIT:,}-character limit" in captured.err, captured.err
    assert "1 dispatched node(s)" in captured.out, captured.out


def test_a_project_this_command_cannot_re_read_leaves_the_count_unknown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An accepted plan stays accepted: this reader's limits are not a verdict.

    A plan the engine reads and this one cannot is a real state — a task carrying two
    repositories is one — and turning that into a refusal would be the second
    implementation deciding structure, which is what the plug-in retired.
    """
    _verb(monkeypatch, 0, ACCEPTED)
    monkeypatch.setattr(
        plan_store,
        "read_project",
        lambda _: (_ for _ in ()).throw(OSError("has more than one repository")),
    )

    assert plan_check.check_through_engine("s:p", "/new/onepipeline") == 0
    read = capsys.readouterr().out
    assert "how many there are is unknown here" in read
    assert "has more than one repository" in read
    assert "dispatched node(s)" not in read


def test_every_refusal_the_verb_returns_is_reported_before_anything_of_its_own(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _verb(
        monkeypatch,
        1,
        {
            "refusals": [
                {"source": "engine", "node": "a", "field": "repo", "reason": "two places"},
                {"source": "scripts/plan-check.sh", "node": "b", "reason": "no record"},
                "not a refusal",
            ],
            "unrunnable": [
                {"check": "scripts/plan-check.sh", "exit_code": None, "stderr": "no"},
                "not an entry",
            ],
        },
        stderr="onepipeline: the loader refused",
    )

    assert plan_check.check_through_engine("s:p", "/new/onepipeline") == 1
    reported = capsys.readouterr().err.splitlines()
    assert reported[0] == "check-plan: engine: a: repo: two places"
    assert reported[1] == "check-plan: scripts/plan-check.sh: b: no record"
    assert reported[2].startswith("check-plan: scripts/plan-check.sh could not be run")
    assert reported[3] == "onepipeline: the loader refused"


def test_a_project_that_could_not_be_read_names_the_next_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _verb(monkeypatch, 2, {"refusals": [], "unrunnable": []}, stderr="no project with that id")

    assert plan_check.check_through_engine("s:absent", "/new/onepipeline") == 2
    reported = capsys.readouterr().err
    assert "s:absent could not be read" in reported
    assert "just orchestrate" in reported


def test_a_check_that_could_not_be_run_is_not_reported_as_an_unreadable_project(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _verb(
        monkeypatch,
        2,
        {"refusals": [], "unrunnable": [{"check": "c.sh", "exit_code": 3, "stderr": "boom"}]},
    )

    assert plan_check.check_through_engine("s:p", "/new/onepipeline") == 2
    reported = capsys.readouterr().err
    assert "c.sh could not be run (exit 3): boom" in reported
    assert "could not be read" not in reported


def test_a_verb_exiting_on_a_code_this_command_does_not_name_is_read_as_unreadable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Never as a refusal: a code nothing here names is a verb this cannot interpret."""
    _verb(monkeypatch, 7, {"refusals": [], "unrunnable": []})

    assert plan_check.check_through_engine("s:p", "/new/onepipeline") == 2
    capsys.readouterr()


def test_a_verb_answering_nothing_readable_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        plan_check.subprocess, "run", lambda *_, **__: _completed(0, "not json", "")
    )

    assert plan_check.check_through_engine("s:p", "/new/onepipeline") == 2
    assert "answered nothing this command could read" in capsys.readouterr().err


def test_a_verb_answering_nothing_at_all_names_the_command_it_ran(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(plan_check.subprocess, "run", lambda *_, **__: _completed(0, "", ""))

    assert plan_check.check_through_engine("s:p", "/new/onepipeline") == 2
    assert "it reported nothing" in capsys.readouterr().err


def test_the_command_takes_the_engines_verb_where_there_is_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(plan_check, "plan_check_engine", lambda: "/new/onepipeline")
    monkeypatch.setattr(plan_check, "carries_plan_check", lambda _: True)
    monkeypatch.setattr(plan_check, "check_through_engine", lambda project, engine: 0)
    monkeypatch.setattr(
        criteria_guard, "check_directly", lambda _: pytest.fail("the direct path was taken")
    )

    assert plan_check.main(["s:p"]) == 0
    capsys.readouterr()


@pytest.mark.parametrize(
    ("engine", "carries"),
    (pytest.param("/old/onepipeline", False, id="no-verb"), pytest.param(None, True, id="absent")),
)
def test_the_command_falls_back_to_this_repositorys_checks_alone(
    monkeypatch: pytest.MonkeyPatch, engine: str | None, carries: bool
) -> None:
    monkeypatch.setattr(plan_check, "plan_check_engine", lambda: engine)
    monkeypatch.setattr(plan_check, "carries_plan_check", lambda _: carries)
    monkeypatch.setattr(
        plan_check,
        "check_through_engine",
        lambda *_: pytest.fail("the engine path was taken"),
    )
    monkeypatch.setattr(criteria_guard, "check_directly", lambda project: 5)

    assert plan_check.main(["s:p"]) == 5
