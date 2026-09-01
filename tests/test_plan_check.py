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

from orchestrator import criteria_guard, plan_check, plan_review, plan_store

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


def test_a_reviewed_and_complete_plan_earns_no_refusal() -> None:
    document = _document(_reviewed(_node()))

    assert plan_check.refusals(document, "authoring:probe") == []
    assert plan_check.dispatched(document) == 1


def test_a_node_whose_criteria_omit_a_demand_is_refused_against_its_task() -> None:
    """The demand is the synthetic appendix's own, which is what makes it attributable."""
    silent = "\n".join(line for line in COMPLETE.splitlines() if "claim" not in line)
    (refusal,) = plan_check.refusals(_document(_reviewed(_node(task=_task(silent)))), "s:p")

    assert refusal["node"] == "route"
    assert refusal["field"] == "task"
    # The node id is the refusal's own field, so the reason does not open with it again.
    assert not refusal["reason"].startswith("route: ")
    assert "an account of this dispatch's own work" in refusal["reason"]


def test_a_node_whose_persona_cannot_resolve_is_refused_against_its_persona() -> None:
    node = _reviewed(_node(persona="../../elsewhere.yaml"))
    (refusal,) = plan_check.refusals(_document(node), "s:p")

    assert refusal["field"] == "persona"
    assert "outside this checkout" in refusal["reason"]


def test_a_task_nothing_has_reviewed_is_refused_and_names_the_command() -> None:
    (refusal,) = plan_check.refusals(_document(_node()), "authoring:probe")

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
    # The review record is still read: a plan this cannot walk is one whose criteria
    # nothing has read either, and reporting only the first would hide the second.
    assert [one["field"] for one in rest] == ["metadata"]
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


def test_the_check_answers_its_contract_on_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stdout carries the whole answer, and nothing else is written anywhere."""
    monkeypatch.setenv(plan_check.PROJECT_ENV, "authoring:probe")
    monkeypatch.setattr("sys.stdin", _Reader(json.dumps(_document(_reviewed(_node())))))

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
    """The value reaches a refusal's own prose, so it names a command that could work."""
    if named is None:
        monkeypatch.delenv(plan_check.PROJECT_ENV, raising=False)
    else:
        monkeypatch.setenv(plan_check.PROJECT_ENV, named)

    (refusal,) = plan_check.refusals(
        _document(_node()), plan_check._named_project(os.environ.get(plan_check.PROJECT_ENV))
    )

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
    monkeypatch.setattr(plan_check, "dispatched_in", lambda _: criteria_guard.Counted(3))

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

    assert plan_check.dispatched_in("s:p") == criteria_guard.Counted(1)


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
