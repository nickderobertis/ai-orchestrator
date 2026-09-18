"""Real local-md coverage for the synchronous SDK-backed plan-store helpers."""

from __future__ import annotations

import os
import shutil
import sys
import venv
from pathlib import Path
from types import SimpleNamespace

import pytest
from onetaskgraph_sdk import QueryResponseOfQualifiedTask
from project_fixtures import helper
from published_tools import ONETASKGRAPH_BIN

from orchestrator import design_approval, follow_up_comments, plan_copy, plan_store
from orchestrator.project_store import PlanDocument, PlanNode, frontmatter, write_plan_project

#: A plan-store CLI of another release, the suite's own decoy: it reports a version no
#: checkout pins, delegates everything else to `REAL_PLAN_STORE`, and appends one line
#: per invocation it serves to `OLDER_PLAN_STORE_LOG`. Its header says why that log,
#: rather than a resolution rule, is what proves which program answered.
OLDER_PLAN_STORE = helper("older-plan-store")


def _source(monkeypatch: pytest.MonkeyPatch, name: str, root: Path) -> None:
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__PLUGIN", "local-md")
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__CONFIG__ROOT", str(root))


def test_sdk_helpers_read_and_copy_a_real_local_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    destination.mkdir()
    plan: PlanDocument = {
        "name": "demo",
        "tasks": [
            PlanNode(id="first", title="First", task="Do first."),
            PlanNode(
                id="second",
                title="Second",
                task="Do second.",
                deps=["first"],
                repo="github.com/acme/service",
                delivers=["followups:I_1"],
            ),
        ],
    }
    write_plan_project(source, plan)
    drafts = tmp_path / "drafts"
    documents = drafts / "documents"
    documents.mkdir(parents=True)
    (documents / "design.md").write_text(
        frontmatter(
            {"title": "Design", "project": "demo", "labels": ["design"]},
            "The design.\n",
        ),
        encoding="utf-8",
    )
    _source(monkeypatch, "sdksource", source)
    _source(monkeypatch, "sdkdestination", destination)
    _source(monkeypatch, "sdkdraft", drafts)
    plan_store.sdk(plan_store.client().document_copy(["sdkdraft:design"], to="sdksource"))

    read, records = plan_store.read_project("sdksource:demo")

    assert read["name"] == "demo"
    assert read["tasks"][1]["deps"] == ["first"]
    assert read["tasks"][1]["repo"] == "github.com/acme/service"
    assert read["tasks"][1]["delivers"] == ["followups:I_1"]
    assert plan_store.task_record("sdksource:demo/first")["title"] == "First"
    assert plan_store.read_projects("sdksource")[0].title == "demo"
    assert follow_up_comments.board_issues("sdksource") == []
    assert plan_store.local_projects("sdksource") == ["sdksource:demo"]
    assert plan_store.task_document("sdksource", "demo/first").is_file()
    assert plan_store.read_documents("sdksource:demo")[0].labels == ["design"]
    assert plan_store.authored_deps(records[1]) == ["first"]

    monkeypatch.setattr(
        design_approval,
        "template_fingerprint",
        lambda: design_approval.TemplateFingerprint("test-template"),
    )
    assert design_approval.main(["sdksource:demo"]) == 0
    assert design_approval.approve("sdksource:demo").held
    assert design_approval.main(["sdksource:demo"]) == 0
    assert "sdksource" in design_approval.configured_sources()

    assert plan_copy.copy("sdksource:demo", "sdkdestination", []) == plan_copy.OK
    output = capsys.readouterr().out
    assert '"action":"created"' in output
    assert plan_store.project_record("sdkdestination:demo")["title"] == "demo"
    assert plan_copy._documents("sdksource:demo", "missing", []) == plan_copy.COPY_REFUSED
    empty = tmp_path / "empty"
    write_plan_project(empty, {"name": "empty", "tasks": []})
    _source(monkeypatch, "empty", empty)
    assert plan_copy._documents("empty:empty", "sdkdestination", []) == plan_copy.OK


def test_a_read_runs_the_locked_install_with_a_decoy_first_on_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `onetaskgraph` a shell puts first on `PATH` never answers a read of this package.

    The condition is the incident's: another release of the plan store ahead of the
    locked one on the search path. The read goes through the real SDK to the real
    installed CLI against a real `local-md` source, and three readings say which program
    answered — the read answered at all, the decoy's own log says it served nothing, and
    the client's binary is this checkout's locked install — because a resolution rule
    alone would read the test's own search path back to itself.
    """
    root = tmp_path / "source"
    write_plan_project(
        root, {"name": "demo", "tasks": [PlanNode(id="task", title="Task", task="Body")]}
    )
    _source(monkeypatch, "sdksource", root)
    served = tmp_path / "older-plan-store.served"
    monkeypatch.setenv("PATH", f"{OLDER_PLAN_STORE}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("REAL_PLAN_STORE", str(ONETASKGRAPH_BIN))
    monkeypatch.setenv("OLDER_PLAN_STORE_LOG", str(served))
    decoy = shutil.which("onetaskgraph")
    assert decoy is not None and Path(decoy).samefile(OLDER_PLAN_STORE / "onetaskgraph"), (
        f"the search path resolves `onetaskgraph` to {decoy}, so the condition this test "
        f"exists for — a decoy ahead of {ONETASKGRAPH_BIN} — was never set up"
    )

    assert plan_store.task_record("sdksource:demo/task")["title"] == "Task"
    assert plan_store.read_tasks("sdksource:demo")[0].node_id == "task"

    assert not served.exists() or served.read_text(encoding="utf-8") == "", (
        f"the decoy ahead on PATH served a read of this package: {served.read_text('utf-8')}"
    )
    assert Path(plan_store.client().binary).samefile(ONETASKGRAPH_BIN)
    assert Path(plan_store.client().binary) == ONETASKGRAPH_BIN.resolve()


def test_a_missing_locked_install_is_refused_by_name_with_the_bootstrap_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interpreter with no `onetaskgraph` beside it refuses, and never falls to `PATH`.

    The interpreter is a real one — a bare virtual environment built from this suite's
    own, with nothing installed into it — so what is driven is the resolution the package
    performs against a directory that really holds a Python and really holds no CLI,
    while the search path still offers one.
    """
    bare = tmp_path / "bare"
    venv.create(bare, with_pip=False)
    interpreter = bare / "bin" / "python3"
    assert interpreter.exists() and os.access(interpreter, os.X_OK), interpreter
    expected = bare / "bin" / "onetaskgraph"
    assert not expected.exists()
    assert shutil.which("onetaskgraph") is not None, (
        "the search path offers no plan store, so a refusal below says nothing about "
        "never falling through to it"
    )

    with pytest.raises(OSError, match="just bootstrap") as refused:
        plan_store.locked_binary(interpreter)
    assert str(expected) in str(refused.value)

    monkeypatch.setattr(sys, "executable", str(interpreter))
    with pytest.raises(OSError, match="just bootstrap") as refused:
        plan_store.client()
    assert str(expected) in str(refused.value)
    with pytest.raises(OSError, match="just bootstrap"):
        plan_store.task_record("sdksource:demo/task")

    # A file that is there but cannot be run — a half-finished provisioning — is the
    # same refusal, never handed to the SDK to fail on.
    expected.write_text("not a program\n", encoding="utf-8")
    expected.chmod(0o644)
    with pytest.raises(OSError, match="just bootstrap") as refused:
        plan_store.client()
    assert str(expected) in str(refused.value)


def test_the_variable_refused_is_the_one_the_installed_sdk_resolves_from() -> None:
    """`SDK_BINARY_VARIABLE` names the SDK's own environment lever, held to the real SDK.

    The name is the SDK's contract, restated in the package so a refusal can say which
    variable it refuses. It is held here to what the installed SDK does rather than to a
    literal: handed an environment naming the locked install under that variable and
    offering no search path, the SDK resolves exactly that file, and with the variable
    absent it finds nothing — so a release that renamed the lever fails here instead of
    leaving the package refusing a name nothing reads. The SDK stripping the variable
    from the child's environment is read off the same client, because the refusal's
    reasoning rests on nothing downstream reading it either way.
    """
    honoured = plan_store.Client(
        environment={plan_store.SDK_BINARY_VARIABLE: str(ONETASKGRAPH_BIN), "PATH": ""}
    )
    assert Path(honoured.binary).samefile(ONETASKGRAPH_BIN)
    assert plan_store.SDK_BINARY_VARIABLE not in honoured.environment
    with pytest.raises(FileNotFoundError, match=plan_store.SDK_BINARY_VARIABLE):
        plan_store.Client(environment={"PATH": ""})


def test_the_sdk_binary_variable_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ONETASKGRAPH_SDK_BINARY` is neither honoured nor ignored: it is refused, naming both.

    It is set to the locked install itself, so the refusal is for the variable being set
    at all rather than for where it points — the pin and the lock are the one answer to
    which release runs, and a second answer in the environment is refused as such.
    """
    root = tmp_path / "source"
    write_plan_project(
        root, {"name": "demo", "tasks": [PlanNode(id="task", title="Task", task="Body")]}
    )
    _source(monkeypatch, "sdksource", root)
    assert plan_store.task_record("sdksource:demo/task")["title"] == "Task"

    monkeypatch.setenv(plan_store.SDK_BINARY_VARIABLE, str(ONETASKGRAPH_BIN))
    with pytest.raises(OSError, match="ONETASKGRAPH_SDK_BINARY") as refused:
        plan_store.client()
    assert str(ONETASKGRAPH_BIN) in str(refused.value)
    with pytest.raises(OSError, match="ONETASKGRAPH_SDK_BINARY"):
        plan_store.task_record("sdksource:demo/task")


def test_sdk_helpers_report_real_local_source_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    write_plan_project(source, {"name": "demo", "tasks": []})
    _source(monkeypatch, "sdksource", source)

    assert plan_copy.copy("sdksource:demo", "missing", []) == plan_copy.COPY_REFUSED
    assert "refused copying" in capsys.readouterr().err
    with pytest.raises(OSError, match="no project"):
        plan_store.project_record("sdksource:missing")
    with pytest.raises(OSError, match="no task"):
        plan_store.task_record("sdksource:demo/missing")
    with pytest.raises(OSError, match="not <project>/<task>"):
        plan_store.task_document("sdksource", "missing")
    with pytest.raises(OSError, match="has no record"):
        plan_store.task_document("sdksource", "demo/missing")


def test_source_root_helpers_use_the_sdk_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "new" / "plans"
    _source(monkeypatch, "sdksource", root)
    assert plan_store.ensure_writable_source_root("sdksource") == root
    assert root.is_dir()

    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")
    _source(monkeypatch, "occupied", occupied)
    with pytest.raises(OSError, match="not a directory"):
        plan_store.ensure_writable_source_root("occupied")

    with pytest.raises(OSError, match="qualified"):
        plan_store.read_project("unqualified")

    monkeypatch.setattr(
        plan_store,
        "configured_settings",
        lambda: {"sources.invalid.plugin": "local-md", "sources.invalid.config.root": ""},
    )
    with pytest.raises(OSError, match="no valid root"):
        plan_store.source_root("invalid")
    monkeypatch.setattr(
        plan_store,
        "configured_settings",
        lambda: {"sources.invalid.plugin": "github-projects"},
    )
    with pytest.raises(OSError, match="github-projects"):
        plan_store.source_root("invalid")

    monkeypatch.setattr(plan_store, "source_root", lambda _source: root)
    monkeypatch.setattr(plan_store.os, "access", lambda *_arguments: False)
    with pytest.raises(OSError, match="not writable"):
        plan_store.ensure_writable_source_root("sdksource")


def test_plan_semantic_refusals_are_applied_after_sdk_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_id = tmp_path / "missing-id"
    write_plan_project(
        missing_id,
        {"name": "demo", "tasks": [PlanNode(id="task", title="Task", task="Body")]},
    )
    task = missing_id / "tasks" / "demo" / "task.md"
    task.write_text(
        task.read_text(encoding="utf-8").replace('  "onepipeline.id": "task"\n', ""),
        encoding="utf-8",
    )
    _source(monkeypatch, "missingid", missing_id)
    with pytest.raises(OSError, match="no string onepipeline.id"):
        plan_store.read_tasks("missingid:demo")

    repositories = tmp_path / "repositories"
    write_plan_project(
        repositories,
        {
            "name": "demo",
            "tasks": [
                PlanNode(
                    id="task",
                    title="Task",
                    task="Body",
                    repo="github.com/acme/one",
                )
            ],
        },
    )
    task = repositories / "tasks" / "demo" / "task.md"
    task.write_text(
        task.read_text(encoding="utf-8").replace(
            '["github.com/acme/one"]', '["github.com/acme/one", "github.com/acme/two"]'
        ),
        encoding="utf-8",
    )
    _source(monkeypatch, "repositories", repositories)
    with pytest.raises(OSError, match="more than one repository"):
        plan_store.read_tasks("repositories:demo")

    unknown = tmp_path / "unknown"
    write_plan_project(
        unknown,
        {"name": "demo", "tasks": [PlanNode(id="task", title="Task", task="Body")]},
    )
    task = unknown / "tasks" / "demo" / "task.md"
    task.write_text(
        task.read_text(encoding="utf-8").replace(
            'status: "todo"\n', 'status: "todo"\ndepends_on: ["demo/missing"]\n'
        ),
        encoding="utf-8",
    )
    _source(monkeypatch, "unknown", unknown)
    with pytest.raises(OSError, match="unknown dependency targets"):
        plan_store.read_tasks("unknown:demo")

    duplicate = tmp_path / "duplicate"
    write_plan_project(
        duplicate,
        {
            "name": "demo",
            "tasks": [
                PlanNode(id="first", title="First", task="Body"),
                PlanNode(id="second", title="Second", task="Body"),
            ],
        },
    )
    second = duplicate / "tasks" / "demo" / "second.md"
    second.write_text(
        second.read_text(encoding="utf-8").replace(
            '"onepipeline.id": "second"', '"onepipeline.id": "first"'
        ),
        encoding="utf-8",
    )
    _source(monkeypatch, "duplicate", duplicate)
    with pytest.raises(OSError, match="duplicate task or node identities"):
        plan_store.read_tasks("duplicate:demo")

    with pytest.raises(OSError, match="not <project>/<task>"):
        plan_store.task_document("duplicate", "../first")


def test_partial_sdk_answers_are_refused_through_the_generated_model() -> None:
    answer = QueryResponseOfQualifiedTask.model_validate(
        {
            "errors": [
                {
                    "class": "refused",
                    "source": "board",
                    "error": {"kind": "auth", "message": "credential missing"},
                }
            ],
            "items": [],
            "plan": {"per_source": []},
        }
    )
    with pytest.raises(OSError, match="source board could not answer"):
        plan_store.complete(answer)


def test_a_later_page_a_source_failed_to_answer_refuses_the_whole_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`complete`'s rule holds on every page: nothing from a partial listing is returned."""
    root = tmp_path / "source"
    write_plan_project(
        root,
        {"name": "demo", "tasks": [PlanNode(id="task", title="Task", task="Body")]},
    )
    _source(monkeypatch, "sdksource", root)
    whole = plan_store.sdk(plan_store.client().task_list(source=["sdksource"], project="demo"))
    assert whole.next is None
    first = QueryResponseOfQualifiedTask.model_validate(
        {**whole.model_dump(mode="json", by_alias=True), "next": "ab"}
    )
    second = QueryResponseOfQualifiedTask.model_validate(
        {
            "errors": [
                {
                    "class": "refused",
                    "source": "sdksource",
                    "error": {"kind": "auth", "message": "credential missing"},
                }
            ],
            "items": [],
            "plan": {"per_source": []},
        }
    )
    pages = [first, second]
    asked: list[object] = []
    monkeypatch.setattr(
        plan_store,
        "client",
        lambda: SimpleNamespace(task_list=lambda **keywords: asked.append(keywords)),
    )
    monkeypatch.setattr(plan_store, "sdk", lambda _answer: pages.pop(0))

    with pytest.raises(OSError, match="source sdksource could not answer"):
        plan_store.read_tasks("sdksource:demo")

    assert asked == [
        {"source": ["sdksource"], "project": "demo", "page": None},
        {"source": ["sdksource"], "project": "demo", "page": "ab"},
    ]


def test_typed_listings_are_still_held_to_the_requested_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    write_plan_project(
        root,
        {"name": "demo", "tasks": [PlanNode(id="task", title="Task", task="Body")]},
    )
    documents = root / "documents"
    documents.mkdir()
    (documents / "design.md").write_text(
        frontmatter({"title": "Design", "project": "demo", "labels": ["design"]}, "Body.\n"),
        encoding="utf-8",
    )
    _source(monkeypatch, "sdksource", root)
    real_client = plan_store.client()
    projects = plan_store.sdk(real_client.project_list(source=["sdksource"]))
    tasks = plan_store.sdk(real_client.task_list(source=["sdksource"], project="demo"))
    shown_project = plan_store.sdk(real_client.project_show("sdksource:demo"))
    shown_task = plan_store.sdk(real_client.task_show("sdksource:demo/task"))
    held_documents = plan_store.sdk(real_client.document_list(source=["sdksource"], project="demo"))

    bad_project = projects.items[0].model_copy(
        update={"id": projects.items[0].id.__class__("elsewhere:demo")}
    )
    monkeypatch.setattr(
        plan_store,
        "client",
        lambda: SimpleNamespace(project_list=lambda **_kwargs: object()),
    )
    monkeypatch.setattr(
        plan_store, "sdk", lambda _answer: projects.model_copy(update={"items": [bad_project]})
    )
    with pytest.raises(OSError, match="outside its own ids"):
        plan_store.read_projects("sdksource")

    duplicate = tasks.model_copy(update={"items": [tasks.items[0], tasks.items[0]]})
    monkeypatch.setattr(
        plan_store,
        "client",
        lambda: SimpleNamespace(task_list=lambda **_kwargs: object()),
    )
    monkeypatch.setattr(plan_store, "sdk", lambda _answer: duplicate)
    with pytest.raises(OSError, match="duplicate task or node identities"):
        plan_store.read_tasks("sdksource:demo")

    outside = tasks.items[0].model_copy(
        update={"id": tasks.items[0].id.__class__("elsewhere:demo/task")}
    )
    monkeypatch.setattr(
        plan_store, "sdk", lambda _answer: tasks.model_copy(update={"items": [outside]})
    )
    with pytest.raises(OSError, match="not one of its ids"):
        follow_up_comments.board_issues("sdksource")
    with pytest.raises(OSError, match="outside itself"):
        plan_store.read_tasks("sdksource:demo")

    monkeypatch.setattr(
        plan_store,
        "client",
        lambda: SimpleNamespace(project_show=lambda _id: object(), task_show=lambda _id: object()),
    )
    monkeypatch.setattr(
        plan_store,
        "sdk",
        lambda _answer: shown_project.model_copy(
            update={"items": [shown_project.items[0], shown_project.items[0]]}
        ),
    )
    with pytest.raises(OSError, match="returned 2 records"):
        plan_store.project_record("sdksource:demo")
    monkeypatch.setattr(
        plan_store, "sdk", lambda _answer: shown_task.model_copy(update={"items": []})
    )
    with pytest.raises(OSError, match="returned 0 records"):
        plan_store.task_record("sdksource:demo/task")

    foreign = held_documents.items[0].model_copy(
        update={"id": held_documents.items[0].id.__class__("elsewhere:design")}
    )
    monkeypatch.setattr(
        plan_store,
        "client",
        lambda: SimpleNamespace(document_list=lambda **_kwargs: object()),
    )
    monkeypatch.setattr(
        plan_store,
        "sdk",
        lambda _answer: held_documents.model_copy(update={"items": [foreign]}),
    )
    with pytest.raises(OSError, match="outside itself"):
        plan_store.read_documents("sdksource:demo")
