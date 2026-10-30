"""Real local-md coverage for the synchronous SDK-backed plan-store helpers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import venv
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import boundary_home
import onetaskgraph_sdk
import pytest
from onetaskgraph_sdk import QueryResponseOfQualifiedTask
from project_fixtures import helper
from published_tools import ONETASKGRAPH_BIN

from orchestrator import design_approval, design_chain, follow_up_comments, plan_copy, plan_store
from orchestrator.project_store import PlanDocument, PlanNode, frontmatter, write_plan_project
from orchestrator.root import REPO_ROOT

#: A plan-store CLI of another release, the suite's own decoy: it reports a version no
#: checkout pins, delegates everything else to `REAL_PLAN_STORE`, and appends one line
#: per invocation it serves to `OLDER_PLAN_STORE_LOG`. Its header says why that log,
#: rather than a resolution rule, is what proves which program answered.
OLDER_PLAN_STORE = helper("older-plan-store")


def _source(
    monkeypatch: pytest.MonkeyPatch, name: str, root: Path, visibility: str | None = None
) -> None:
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__PLUGIN", "local-md")
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__CONFIG__ROOT", str(root))
    if visibility is not None:
        monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__VISIBILITY", visibility)


def _registered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    public: list[str],
    private: tuple[str, ...] = (),
) -> None:
    """Point this process's store at an `onevcs` root answering each identity's visibility.

    The write policy `boundary_home` switches the boundary on with asks `onevcs boundary
    inspect` for every repository an item names, and a repository it has no record of is
    private, so a fixture naming one has it registered with the visibility the test means
    it to have.
    """
    home = boundary_home.registered_home(tmp_path / "boundary", public=public, private=private)
    for name, value in boundary_home.environment(None, home).items():
        if os.environ.get(name) != value:
            monkeypatch.setenv(name, value)


#: The ticket a fixture task delivers, held by a local source the test configures.
DELIVERED = "sdktickets:board/ticket"


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
                delivers=[DELIVERED],
            ),
        ],
    }
    write_plan_project(source, plan)
    drafts = tmp_path / "drafts"
    # The draft source holds the project its document is filed under, because the store's
    # boundary refuses an item under a project its source cannot classify.
    write_plan_project(drafts, {"name": "demo", "tasks": []})
    documents = drafts / "documents"
    documents.mkdir(parents=True)
    # Provenance built here in the shape a rendering of this body records, so the approval
    # below reads a design document it can key rather than one it refuses. One value is
    # both hashed and written, which `frontmatter` stores with its trailing whitespace cut.
    body = "The design."
    rendering = {
        "template": design_approval.TEMPLATE_REFERENCE,
        "digest": "sha256:test-template",
        "body_digest": "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    (documents / "design.md").write_text(
        frontmatter(
            {
                "title": "Design",
                "project": "demo",
                "labels": ["design"],
                "metadata": {design_approval.PROVENANCE: rendering},
            },
            body,
        ),
        encoding="utf-8",
    )
    _registered(monkeypatch, tmp_path, public=["github.com/acme/service"])
    # The ticket a task delivers is one a configured source holds, because the store's
    # boundary refuses to write a reference whose item it cannot classify.
    tickets = tmp_path / "tickets"
    write_plan_project(
        tickets, {"name": "board", "tasks": [PlanNode(id="ticket", title="Ticket", task="Fix.")]}
    )
    _source(monkeypatch, "sdktickets", tickets)
    _source(monkeypatch, "sdksource", source)
    _source(monkeypatch, "sdkdestination", destination)
    _source(monkeypatch, "sdkdraft", drafts)
    plan_store.sdk(plan_store.client().document_copy(["sdkdraft:design"], to="sdksource"))

    read, records = plan_store.read_project("sdksource:demo")

    assert read["name"] == "demo"
    assert read["tasks"][1]["deps"] == ["first"]
    assert read["tasks"][1]["repo"] == "github.com/acme/service"
    assert read["tasks"][1]["delivers"] == [DELIVERED]
    assert plan_store.task_record("sdksource:demo/first")["title"] == "First"
    assert plan_store.read_projects("sdksource")[0].title == "demo"
    # No task there carries a comment, so the narrowed query keeps none of them.
    commented = plan_store.client().task_list(
        source=["sdksource"],
        commented_since=follow_up_comments.instant(datetime(2026, 1, 1, tzinfo=UTC)),
    )
    assert plan_store.sdk(commented).items == []
    assert plan_store.local_projects("sdksource") == ["sdksource:demo"]
    assert plan_store.task_document("sdksource", "demo/first").is_file()
    assert plan_store.read_documents("sdksource:demo")[0].labels == ["design"]
    assert plan_store.authored_deps(records[1]) == ["first"]

    monkeypatch.setattr(
        design_approval,
        "resolved_digest",
        lambda *_arguments: design_approval.ChainDigest("sha256:test-template"),
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
    # The store's own graph refuses a prerequisite its source does not hold at all.
    with pytest.raises(OSError, match="no task with the id unknown:demo/missing"):
        plan_store.read_tasks("unknown:demo")
    # One it holds outside the plan is drawn, and refused here as no task of the plan.
    write_plan_project(
        unknown,
        {"name": "other", "tasks": [PlanNode(id="missing", title="Missing", task="Body")]},
    )
    task.write_text(
        task.read_text(encoding="utf-8").replace("demo/missing", "other/missing"),
        encoding="utf-8",
    )
    with pytest.raises(OSError, match="unknown dependency targets for unknown:demo/task"):
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
        plan_store.every_page(
            "project 'sdksource:demo'",
            plan_store.client().task_list,
            source=["sdksource"],
            project="sdksource:demo",
            members=True,
        )

    assert asked == [
        {"source": ["sdksource"], "project": "sdksource:demo", "members": True, "page": None},
        {"source": ["sdksource"], "project": "sdksource:demo", "members": True, "page": "ab"},
    ]


#: Each listing the store reads through :func:`plan_store.every_page`, and the public
#: `QueryResponseOf…` name of the page it answers with.
LISTINGS = {
    "task_list": "QueryResponseOfQualifiedTask",
    "document_list": "QueryResponseOfQualifiedDocument",
    "project_list": "QueryResponseOfQualifiedProject",
    "task_deps": "QueryResponseOfQualifiedEdge",
    "label_list": "QueryResponseOfQualifiedLabel",
    "search": "QueryResponseOfSearchHit",
}

#: A consumer of the store written as one would be: the page types imported from the
#: SDK's public namespace, each listing read through the store, and every page held to
#: the public name. Under the repository's strict settings an alias the SDK does not
#: export is an `attr-defined` error at the import, and a page read as `Any` fails its
#: `assert_type`.
STRICT_CONSUMER = """\
from typing import assert_type

from onetaskgraph_sdk import (
    Client,
    QueryResponseOfQualifiedDocument,
    QueryResponseOfQualifiedEdge,
    QueryResponseOfQualifiedLabel,
    QueryResponseOfQualifiedProject,
    QueryResponseOfQualifiedTask,
    QueryResponseOfSearchHit,
)

from orchestrator import plan_store


def read(client: Client) -> None:
{reads}
"""


def test_the_pages_a_listing_is_typed_over_are_the_sdks_public_names() -> None:
    """The store's page types are the ones the SDK publishes, imported from it by name.

    Every `QueryResponseOf…` the SDK publishes in `__all__` is the very class the store
    binds, and the store binds no other: a release that renamed, dropped or re-generated
    one fails here rather than at a dispatch.
    """
    published = {
        name: getattr(onetaskgraph_sdk, name)
        for name in dir(onetaskgraph_sdk)
        if name.startswith("QueryResponseOf")
    }
    bound = {
        name: getattr(plan_store, name)
        for name in dir(plan_store)
        if name.startswith("QueryResponseOf")
    }

    assert set(published) == set(LISTINGS.values()), published
    assert bound == published, (set(bound) ^ set(published), bound, published)
    assert all(bound[name] is published[name] for name in published)
    assert {"items", "errors", "next"} <= set(
        published["QueryResponseOfQualifiedTask"].model_fields
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] This test's subject is
# `orchestrator/plan_store.py` as a strict consumer reads it, and everything the mypy run
# reads — the package, `pyproject.toml`'s `[tool.mypy]`, the locked SDK — is inside the key
# `orchestrator:test` is memoized on, so a project of its own would carry the same key over
# the same inputs. That tier already drives real subprocesses (`just`, Nx, the plan-store
# CLI) by this repository's realistic-tests invariant; one mypy run over one module is that.
def test_the_public_page_names_type_check_under_the_repositorys_strict_settings(
    tmp_path: Path,
) -> None:
    """A strict consumer importing the public names reads every listing typed, not `Any`.

    The real mypy runs over a module importing the six names from `onetaskgraph_sdk`
    itself, under `pyproject.toml`'s own `[tool.mypy]`, and asserts each page
    :func:`plan_store.every_page` returns is the public name for its listing. The public
    names were once aliases a strict checker would not re-export, which is why the store
    reached into the SDK's generated modules; this is what says it no longer has to.
    """
    reads = "\n".join(
        f"    assert_type(plan_store.every_page({method!r}, client.{method}), list[{name}])"
        for method, name in LISTINGS.items()
    )
    consumer = tmp_path / "strict_consumer.py"
    consumer.write_text(STRICT_CONSUMER.format(reads=reads), encoding="utf-8")

    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            str(consumer),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "Success: no issues found in 1 source file" in checked.stdout, checked.stdout


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def test_a_page_cursor_already_followed_is_refused_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store answering a cursor the listing already followed is refused, not re-read.

    Followed again, the same page would be read as a listing twice its size — or, from a
    cursor that never advances, never end. The pages are the SDK's generated model over a
    real store answer, with the cursor the second page reports being the one that reached
    it; the refusal names that cursor and nothing of either page is returned.
    """
    root = tmp_path / "source"
    write_plan_project(
        root,
        {"name": "demo", "tasks": [PlanNode(id="task", title="Task", task="Body")]},
    )
    _source(monkeypatch, "sdksource", root)
    whole = plan_store.sdk(plan_store.client().task_list(source=["sdksource"], project="demo"))
    assert whole.next is None
    repeating = QueryResponseOfQualifiedTask.model_validate(
        {**whole.model_dump(mode="json", by_alias=True), "next": "ab"}
    )
    pages = [repeating, repeating.model_copy(deep=True), repeating.model_copy(deep=True)]
    asked: list[object] = []
    monkeypatch.setattr(
        plan_store,
        "client",
        lambda: SimpleNamespace(task_list=lambda **keywords: asked.append(keywords)),
    )
    monkeypatch.setattr(plan_store, "sdk", lambda _answer: pages.pop(0))

    with pytest.raises(OSError) as refused:
        plan_store.every_page(
            "project 'sdksource:demo'",
            plan_store.client().task_list,
            source=["sdksource"],
            project="sdksource:demo",
            members=True,
        )

    assert str(refused.value) == (
        "listing project 'sdksource:demo' answered the page cursor 'ab' again after it was "
        "already followed; refusing to read the same page twice"
    )
    assert asked == [
        {"source": ["sdksource"], "project": "sdksource:demo", "members": True, "page": None},
        {"source": ["sdksource"], "project": "sdksource:demo", "members": True, "page": "ab"},
    ]
    assert len(pages) == 1, "a third page was asked for after the cursor repeated"


def test_a_task_the_store_created_is_read_as_its_projects_whatever_the_source_named_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Membership is the item's own `project`, which a created task's id does not spell.

    `onetaskgraph task create` into a `local-md` source names the file after the task's
    title, so its id sits beside the project's other tasks rather than under a directory
    of the project. A reader holding it to `<project>/<task>` refused every task a planner
    created from a template; one filed under another project is still refused.
    """
    root = tmp_path / "source"
    write_plan_project(
        root,
        {"name": "demo", "tasks": [PlanNode(id="written", title="Written", task="Body")]},
    )
    _source(monkeypatch, "sdksource", root)
    body = tmp_path / "body.md"
    body.write_text("## What\n\nCreated by the store.\n", encoding="utf-8")
    created = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            "task",
            "create",
            "sdksource",
            "--project",
            "demo",
            "--title",
            "Created by the store",
            "--body-file",
            str(body),
            "--metadata",
            'onepipeline.id="created"',
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    minted = created.stdout.strip()
    assert not minted.startswith("sdksource:demo/"), minted

    read = {task.node_id: task for task in plan_store.read_tasks("sdksource:demo")}

    assert set(read) == {"written", "created"}, read
    assert read["created"].qualified_id == minted
    assert {task.project for task in read.values()} == {"demo"}

    listed = plan_store.sdk(plan_store.client().task_list(source=["sdksource"], project="demo"))
    (held,) = [item for item in listed.items if item.id.model_dump() == minted]
    elsewhere = held.model_copy(
        update={
            "item": held.item.model_copy(update={"project": held.item.project.__class__("other")})
        }
    )
    # The project's own record, read for real before the task listing is replaced, so the
    # replaced answer below is only ever read as the task listing it stands in for.
    record = plan_store.project_record("sdksource:demo")
    monkeypatch.setattr(plan_store, "project_record", lambda _project: record)
    monkeypatch.setattr(
        plan_store, "sdk", lambda _answer: listed.model_copy(update={"items": [elsewhere]})
    )
    with pytest.raises(OSError, match="outside itself"):
        plan_store.read_tasks("sdksource:demo")


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

    # The project listing is the store's own record and has its guard above; these are the
    # task listing's, so the project's record names no members and the plain listing answers.
    shown_record = plan_store.project_record
    monkeypatch.setattr(plan_store, "project_record", lambda _project: {"metadata": {}})
    duplicate = tasks.model_copy(update={"items": [tasks.items[0], tasks.items[0]]})
    monkeypatch.setattr(
        plan_store,
        "client",
        lambda: SimpleNamespace(
            task_list=lambda **_kwargs: object(), project_list=lambda **_kwargs: object()
        ),
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
    with pytest.raises(OSError, match="outside itself"):
        plan_store.read_tasks("sdksource:demo")

    monkeypatch.setattr(plan_store, "project_record", shown_record)
    monkeypatch.setattr(
        plan_store,
        "client",
        lambda: SimpleNamespace(
            project_show=lambda _id: object(), task_show=lambda _id, **_: object()
        ),
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


#: The published plan-store CLI, doubled at its boundary and nowhere above it: every
#: invocation is the locked install's, and only its JSON answer is rewritten on the way
#: out, so the SDK's decode and this package's reading path run as they do on a real read.
#: `REWRITTEN_FIELD` names a field of every task a listing answers, and `REWRITTEN_AS`
#: says what that task carries in its place: `absent`, an explicit JSON `null`, or —
#: for any other word — the store's own value, untouched.
REWRITING_PLAN_STORE = """\
#!/usr/bin/env bash
set -uo pipefail
answer=$("$REAL_PLAN_STORE" "$@")
status=$?
printf '%s' "$answer" | "$(dirname "$0")/python3" -c '
import json, os, sys
text = sys.stdin.read()
field, mode = os.environ["REWRITTEN_FIELD"], os.environ["REWRITTEN_AS"]
try:
    answer = json.loads(text)
except ValueError:
    sys.stdout.write(text)
    raise SystemExit
for held in answer.get("items", []) if isinstance(answer, dict) else []:
    task = held.get("item") if isinstance(held, dict) else None
    if isinstance(task, dict) and "delivers" in task:
        if mode == "absent":
            task.pop(field, None)
        elif mode == "null":
            task[field] = None
json.dump(answer, sys.stdout)
'
exit "$status"
"""


@pytest.mark.parametrize("field", ["repositories", "delivers"])
def test_a_defaulted_task_field_decodes_to_its_default_and_an_explicit_null_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """The adopted SDK's decode, through `read_tasks` over a real local-md store.

    A task's `repositories` and `delivers` are defaulted and never null: a store answer
    that omits one reads as its default, the same answer carrying `null` is refused as
    outside the store's schema rather than read as `None`, and a value the store answers
    reads unchanged. The interpreter is a real bare one with the stand-in beside it,
    because the package runs the CLI beside its interpreter and no other.
    """
    root = tmp_path / "source"
    write_plan_project(
        root,
        {
            "name": "demo",
            "tasks": [
                PlanNode(
                    id="task",
                    title="Task",
                    task="Body",
                    repo="github.com/acme/service",
                    delivers=["followups:I_1"],
                )
            ],
        },
    )
    _source(monkeypatch, "sdksource", root)
    bare = tmp_path / "bare"
    venv.create(bare, with_pip=False)
    stand_in = bare / "bin" / "onetaskgraph"
    stand_in.write_text(REWRITING_PLAN_STORE, encoding="utf-8")
    stand_in.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(bare / "bin" / "python3"))
    monkeypatch.setenv("REAL_PLAN_STORE", str(ONETASKGRAPH_BIN))
    monkeypatch.setenv("REWRITTEN_FIELD", field)

    monkeypatch.setenv("REWRITTEN_AS", "absent")
    [defaulted] = plan_store.read_tasks("sdksource:demo")
    assert getattr(defaulted, field) in ([], ()), defaulted

    monkeypatch.setenv("REWRITTEN_AS", "null")
    with pytest.raises(OSError, match="outside its emitted schema") as refused:
        plan_store.read_tasks("sdksource:demo")
    assert field in str(refused.value)

    monkeypatch.setenv("REWRITTEN_AS", "untouched")
    [held] = plan_store.read_tasks("sdksource:demo")
    assert held.repositories == ["github.com/acme/service"]
    assert held.delivers == ("followups:I_1",)


#: The repository a routed source takes, and the one that stays in the plan's own source.
ROUTED_REPOSITORY = "github.com/hiddenco/qh-api"
HOME_REPOSITORY = "github.com/nickderobertis/onevcs"


def _routed_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, home: str | None = None
) -> None:
    """A draft source, a home source routing hiddenco repositories, and the member source.

    The hiddenco repository is registered private and the member source declared private,
    as a host's organization tracker is, and the other repository registered public; the
    home is declared ``home``, undeclared unless the test names it.
    """
    _registered(monkeypatch, tmp_path, public=[HOME_REPOSITORY], private=(ROUTED_REPOSITORY,))
    visibility = {"draft": None, "home": home, "member": "private"}
    for name, declared in visibility.items():
        (tmp_path / name).mkdir()
        _source(monkeypatch, name, tmp_path / name, declared)
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__HOME__ROUTES__0__TO", "member")
    monkeypatch.setenv(
        "ONETASKGRAPH_SOURCES__HOME__ROUTES__0__REPOSITORIES", "github.com/hiddenco/*"
    )


def _copied_home(name: str, tasks: list[PlanNode], tmp_path: Path) -> str:
    """Draft ``tasks`` as one plan and copy it to the routing source, answering its home.

    The copy is the real store's, in a directory whose own document declares the three
    sources and no public boundary. These reader journeys exercise existing routed records;
    `test_a_public_home_sends_a_plan_holding_a_private_task_to_the_private_source`
    separately drives the screened copy and an explicitly private project's refusal.
    """
    write_plan_project(tmp_path / "draft", {"name": name, "tasks": tasks})
    unbounded = tmp_path / "unbounded"
    unbounded.mkdir(exist_ok=True)
    (unbounded / "onetaskgraph.yaml").write_text(
        "sources:\n"
        + "".join(
            f"  {source}:\n    plugin: local-md\n    config:\n      root: {tmp_path / source}\n"
            for source in ("draft", "member")
        )
        + f"  home:\n    plugin: local-md\n    config:\n      root: {tmp_path / 'home'}\n"
        + '    routes:\n      - repositories: ["github.com/hiddenco/*"]\n        to: member\n',
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ONETASKGRAPH_")
    }
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "no-user-layer")
    copied = subprocess.run(
        [str(ONETASKGRAPH_BIN), "--json", "project", "copy", f"draft:{name}", "--to", "home"],
        cwd=unbounded,
        env=environment,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert copied.returncode == 0, copied.stderr
    (home,) = [
        str(item["destination"])
        for item in json.loads(copied.stdout)["items"]
        if item["source"] == f"draft:{name}" and item.get("destination")
    ]
    return home


def test_a_plan_the_store_routed_across_sources_is_read_as_one_with_its_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The home's tasks and its member's are one plan, with edges both ways between them.

    The real store routes the hiddenco task into a member project of the second source; the
    read names every node once, each under its own source and project, and resolves the
    dependency edges that cross sources in either direction.
    """
    _routed_sources(monkeypatch, tmp_path)
    home = _copied_home(
        "mixed",
        [
            PlanNode(id="library", title="Library", task="Do it.", repo=HOME_REPOSITORY),
            PlanNode(
                id="consumer",
                title="Consumer",
                task="Do it.",
                deps=["library"],
                repo=ROUTED_REPOSITORY,
            ),
            PlanNode(
                id="rollout",
                title="Rollout",
                task="Do it.",
                deps=["consumer"],
                repo=HOME_REPOSITORY,
            ),
        ],
        tmp_path,
    )

    read = {task.node_id: task for task in plan_store.read_tasks(home)}

    assert home.startswith("home:"), home
    assert plan_store.reachable("home") == ("home", "member")
    assert plan_store.members(home) == {f"member:{home.partition(':')[2]}"}
    assert {node: task.qualified_id.partition(":")[0] for node, task in read.items()} == {
        "library": "home",
        "consumer": "member",
        "rollout": "home",
    }
    assert {node: task.deps for node, task in read.items()} == {
        "library": (),
        "consumer": ("library",),
        "rollout": ("consumer",),
    }
    assert design_chain.plan_repository(home) is None


def test_a_plan_every_task_of_which_routes_away_lands_whole_in_the_routed_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An all-hiddenco plan's home is the routed source's, resolving through that repository."""
    _routed_sources(monkeypatch, tmp_path)
    home = _copied_home(
        "routed",
        [
            PlanNode(id="first", title="First", task="Do it.", repo=ROUTED_REPOSITORY),
            PlanNode(
                id="second",
                title="Second",
                task="Do it.",
                deps=["first"],
                repo=ROUTED_REPOSITORY,
            ),
        ],
        tmp_path,
    )

    assert home.startswith("member:"), home
    assert plan_store.members(home) == frozenset()
    assert {task.node_id for task in plan_store.read_tasks(home)} == {"first", "second"}
    assert design_chain.plan_repository(home) == ROUTED_REPOSITORY


def test_a_public_home_sends_a_plan_holding_a_private_task_to_the_private_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Onto a public home a plan naming only private repositories is homed where they route.

    The boundary is on, so a plan every task of which routes to the source declared private
    lands there whole, home and tasks. A mixed plan lands there whole too, and its public
    task is seen at the public home only as a neutral view naming no private work. An
    explicitly private project is refused at that public home before anything is written. A
    single task copy is routed, which is how a follow-up ticket of the organization reaches
    its own tracker.
    """
    _routed_sources(monkeypatch, tmp_path, home="public")
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__DRAFT__VISIBILITY", "private")

    def drafted(name: str, repositories: list[str]) -> str:
        write_plan_project(
            tmp_path / "draft",
            {
                "name": name,
                "tasks": [
                    PlanNode(id=f"task-{index}", title=f"Task {index}", task="Do it.", repo=repo)
                    for index, repo in enumerate(repositories)
                ],
            },
        )
        return f"draft:{name}"

    report = plan_store.sdk(
        plan_store.client().project_copy(
            drafted("routed", [ROUTED_REPOSITORY, ROUTED_REPOSITORY]), to="home"
        )
    )
    assert {
        str(item.root.source.root): str(item.root.destination.root).partition(":")[0]
        for item in report.items
        if item.root.destination
    } == {
        "draft:routed": "member",
        "draft:routed/task-0": "member",
        "draft:routed/task-1": "member",
    }

    mixed = drafted("mixed", [HOME_REPOSITORY, ROUTED_REPOSITORY])
    report = plan_store.sdk(plan_store.client().project_copy(mixed, to="home"))
    destinations = {
        str(item.root.source.root): str(item.root.destination.root).partition(":")[0]
        for item in report.items
        if item.root.destination
    }
    # Every task of a plan holding a private one stays canonical beside its private home; the
    # public task is seen at the public home only as the store's neutral view of it.
    assert destinations == {
        "draft:mixed": "member",
        "draft:mixed/task-0": "member",
        "draft:mixed/task-1": "member",
    }, destinations
    public_records = {
        path: path.read_text(encoding="utf-8") for path in (tmp_path / "home").rglob("*.md")
    }
    views = [content for content in public_records.values() if "title: Task 0" in content]
    assert len(views) == 1, public_records
    assert "github.com/nickderobertis/onevcs" in views[0], views
    assert any("title: Public work" in content for content in public_records.values())
    for path, content in public_records.items():
        assert "hiddenco" not in content and "qh-api" not in content, (path, content)
        assert "mixed" not in content and "Task 1" not in content, (path, content)

    # Public repositories leave no route to a private home: only the project's explicit
    # classification makes this copy private, so it must refuse at the requested home.
    private = drafted("classified", [HOME_REPOSITORY, HOME_REPOSITORY])
    classified = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            "project",
            "create",
            "draft",
            "--id",
            "classified",
            "--title",
            "Classified plan",
            "--classification",
            "private",
            "--no-interactive",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert classified.returncode == 0, classified.stdout + classified.stderr
    before_refusal = {
        path: path.read_bytes()
        for source in ("home", "member")
        for path in (tmp_path / source).rglob("*.md")
    }
    with pytest.raises(OSError, match="is private, and source home is not declared private"):
        plan_store.sdk(plan_store.client().project_copy(private, to="home"))
    assert before_refusal == {
        path: path.read_bytes()
        for source in ("home", "member")
        for path in (tmp_path / source).rglob("*.md")
    }

    report = plan_store.sdk(plan_store.client().task_copy(["draft:routed/task-0"], to="home"))
    assert [str(item.root.destination.root) for item in report.items if item.root.destination] == [
        "member:routed/task-0"
    ]


def test_a_home_naming_its_members_in_any_other_shape_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `onetaskgraph.members` that is not a list of qualified ids names nothing to read."""
    _source(monkeypatch, "home", tmp_path)
    write_plan_project(tmp_path, {"name": "odd", "tasks": []})
    record = tmp_path / "projects" / "odd.md"
    text = record.read_text(encoding="utf-8")
    record.write_text(
        text.replace("metadata:\n", 'metadata:\n  onetaskgraph.members: ["not qualified"]\n', 1),
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="not a list of qualified project ids"):
        plan_store.members("home:odd")


def test_a_sources_reachable_family_is_the_stores_own_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sources route`'s `reachable`: the source first, then each route's target once, in order."""
    for name in ("home", "member", "other"):
        (tmp_path / name).mkdir()
        _source(monkeypatch, name, tmp_path / name, "private" if name != "home" else None)
    for index, (to, matched) in enumerate(
        (("member", "github.com/a/*"), ("other", "github.com/b/*"), ("member", "github.com/c/*"))
    ):
        monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__HOME__ROUTES__{index}__TO", to)
        monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__HOME__ROUTES__{index}__REPOSITORIES", matched)

    assert plan_store.reachable("home") == ("home", "member", "other")
    assert plan_store.reachable("other") == ("other",)
    with pytest.raises(OSError, match="nowhere"):
        plan_store.reachable("nowhere")


def test_task_locate_answers_the_copys_placement_and_refuses_a_target_by_its_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store's `task locate`, writing nothing: its counterpart, and `--target-native`.

    A named item the store verifies is answered `target-task`; one gone, or one carrying
    another task's origin, is :class:`plan_store.TargetRefused`, the store's own kinds for
    them, while every other refusal stays a plain :class:`OSError`.
    """
    _routed_sources(monkeypatch, tmp_path, home="public")
    home = _copied_home(
        "located",
        [
            PlanNode(id="one", task="One task.", repo=HOME_REPOSITORY),
            PlanNode(id="two", task="Another task.", repo=HOME_REPOSITORY),
        ],
        tmp_path,
    )
    copies = {task.node_id: str(task.qualified_id) for task in plan_store.read_tasks(home)}
    drafted = {
        task.node_id: str(task.qualified_id) for task in plan_store.read_tasks("draft:located")
    }
    source = drafted["one"]
    before = sorted((tmp_path / "home").rglob("*.md"))

    found = plan_store.task_locate(source, "home")
    assert found.destination.root == "home"
    assert found.counterpart is not None and found.counterpart.id.root == copies["one"]
    bare = plan_store.task_locate(source, "home", counterpart=False)
    assert (bare.destination.root, bare.counterpart, bare.carriers) == ("home", None, None)
    named = plan_store.task_locate(source, "home", target_native=copies["one"].partition(":")[2])
    assert named.counterpart is not None
    assert (named.counterpart.id.root, str(named.counterpart.via)) == (
        copies["one"],
        "target-task",
    )
    with pytest.raises(plan_store.TargetRefused, match="does not exist"):
        plan_store.task_locate(source, "home", target_native="not-on-the-board")
    with pytest.raises(plan_store.TargetRefused, match="has no origin naming"):
        plan_store.task_locate(source, "home", target_native=copies["two"].partition(":")[2])
    assert sorted((tmp_path / "home").rglob("*.md")) == before, "locating wrote to the board"
    with pytest.raises(OSError) as unconfigured:
        plan_store.task_locate(source, "nowhere")
    assert not isinstance(unconfigured.value, plan_store.TargetRefused)


def test_task_locate_refuses_an_answer_about_another_task_or_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trust-boundary guard: an answer of another count, or about another task, is refused.

    A conforming store answers one entry per id named, in order, so no real store reaches
    this; the answer is the SDK's own typed `LocateReport`, and only it is substituted.
    """
    located = {
        "source": "drafts:run/tickets/cause",
        "classification": "public",
        "destination": "board",
        "counterpart": None,
        "carriers": [],
        "creates_in": None,
    }
    for items, refused in (
        ([], "answered 0 other items"),
        ([located, located], "answered 2 other items"),
        ([located | {"source": "drafts:run/tickets/another"}], "answered 1 other items"),
    ):

        async def answered(*_ids: object, items: object = items, **_options: object) -> object:
            return onetaskgraph_sdk.LocateReport.model_validate({"items": items})

        monkeypatch.setattr(plan_store, "client", lambda: SimpleNamespace(task_locate=answered))
        with pytest.raises(OSError, match=refused):
            plan_store.task_locate("drafts:run/tickets/cause", "board")


def test_project_copies_lists_only_projects_stamped_with_the_requested_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real store finds a copied project by origin and excludes unrelated projects."""
    draft = tmp_path / "draft"
    board = tmp_path / "board"
    write_plan_project(draft, {"name": "copied", "tasks": []})
    write_plan_project(board, {"name": "unrelated", "tasks": []})
    _source(monkeypatch, "draft", draft)
    _source(monkeypatch, "board", board)

    assert plan_store.project_copies("board", "draft:copied") == []
    plan_store.sdk(plan_store.client().project_copy("draft:copied", to="board"))

    (copied,) = plan_store.project_copies("board", "draft:copied")
    assert str(copied.qualified_id) == "board:copied"
    assert copied.title == "copied"
    assert copied.metadata[plan_store.ORIGIN_KEY] == "draft:copied"
    assert plan_store.project_copies("board", "draft:unrelated") == []


def test_a_project_the_store_holds_no_record_of_is_refused_rather_than_read_as_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its record is read by id, so a project nothing holds is the store's refusal.

    Which members a plan has is read off its own record, and that record is asked for by
    id rather than found in a listing of every project its source holds; a project the
    store has no record of is then the store's own refusal, never an empty plan.
    """
    _source(monkeypatch, "home", tmp_path)

    with pytest.raises(OSError, match="no project with that id"):
        plan_store.read_tasks("home:nothing-here")


def test_a_home_with_no_tasks_of_its_own_refuses_when_its_member_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A routed task the store cannot read is a refusal, never an empty or partial plan.

    The real routed copy files the hiddenco task in the member source and records the member
    on the home. The home's own task is then removed, so its own listing is empty, and the
    member source is pointed at a directory that is not there: the read of the home asks for
    its members because its record names them, and reports the member's refusal.
    """
    _routed_sources(monkeypatch, tmp_path)
    home = _copied_home(
        "unreadable",
        [
            PlanNode(id="library", title="Library", task="Do it.", repo=HOME_REPOSITORY),
            PlanNode(id="consumer", title="Consumer", task="Do it.", repo=ROUTED_REPOSITORY),
        ],
        tmp_path,
    )
    native = home.partition(":")[2]
    for task in (tmp_path / "home" / "tasks").rglob("*.md"):
        task.unlink()
    listed = plan_store.sdk(plan_store.client().task_list(source=["home"], project=native))
    assert listed.items == [], "the home still lists a task of its own"
    _source(monkeypatch, "member", tmp_path / "gone")

    with pytest.raises(OSError, match="source member could not answer"):
        plan_store.read_tasks(home)


def test_the_tracked_configuration_declares_its_drafting_sources_private_and_its_boards_not() -> (
    None
):
    """The store's own resolution, read through `config show`, answers each source's visibility.

    The sources a plan is drafted or launched from are declared private, the boards public, and
    `drafts` declares nothing, which is not private.
    """
    assert plan_store.declared_private("authoring")
    assert plan_store.declared_private("follow-up-runs")
    assert plan_store.declared_private("test-fixtures")
    assert not plan_store.declared_private("plans")
    assert not plan_store.declared_private("followups")
    assert not plan_store.declared_private("drafts")
