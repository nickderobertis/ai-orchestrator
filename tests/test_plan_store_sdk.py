"""Real local-md coverage for the synchronous SDK-backed plan-store helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace

import boundary_home
import onetaskgraph_sdk
import pytest
import yaml
from onetaskgraph_sdk import QueryResponseOfQualifiedTask
from project_fixtures import helper
from published_tools import ONETASKGRAPH_BIN

from orchestrator import plan_store
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


def _local_task(root: Path, native: str, title: str, body: str, **front: object) -> None:
    """One task of project `demo`, written as the `local-md` plugin stores it."""
    (root / "projects").mkdir(parents=True, exist_ok=True)
    (root / "projects" / "demo.md").write_text("---\ntitle: demo\nstatus: todo\n---\n")
    (root / "tasks").mkdir(exist_ok=True)
    fields = {"title": title, "status": "todo", "project": "demo", **front}
    (root / "tasks" / f"{native}.md").write_text(
        f"---\n{yaml.safe_dump(fields, sort_keys=False)}---\n{body}\n", encoding="utf-8"
    )


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
    _local_task(root, "task", "Task", "Body")
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

    assert plan_store.task_record("sdksource:task")["title"] == "Task"

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
        plan_store.task_record("sdksource:task")

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
    _local_task(root, "task", "Task", "Body")
    _source(monkeypatch, "sdksource", root)
    assert plan_store.task_record("sdksource:task")["title"] == "Task"

    monkeypatch.setenv(plan_store.SDK_BINARY_VARIABLE, str(ONETASKGRAPH_BIN))
    with pytest.raises(OSError, match="ONETASKGRAPH_SDK_BINARY") as refused:
        plan_store.client()
    assert str(ONETASKGRAPH_BIN) in str(refused.value)
    with pytest.raises(OSError, match="ONETASKGRAPH_SDK_BINARY"):
        plan_store.task_record("sdksource:task")


def test_sdk_helpers_report_real_local_source_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    _local_task(source, "task", "Task", "Body")
    _source(monkeypatch, "sdksource", source)

    with pytest.raises(OSError, match="no task"):
        plan_store.task_record("sdksource:missing")


def test_a_task_show_answering_other_than_one_record_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trust-boundary guard: `task show` for one id answering 0 or 2 records is refused.

    A conforming store answers one, so the real answer is read and only its item count is
    changed before the guard reads it.
    """
    source = tmp_path / "source"
    _local_task(source, "task", "Task", "Body")
    _source(monkeypatch, "sdksource", source)
    real = plan_store.sdk
    for count in (0, 2):

        def answered(
            awaitable: Coroutine[object, object, QueryResponseOfQualifiedTask], count: int = count
        ) -> QueryResponseOfQualifiedTask:
            shown = real(awaitable)
            return shown.model_copy(update={"items": [shown.items[0]] * count})

        monkeypatch.setattr(plan_store, "sdk", answered)
        with pytest.raises(OSError, match=f"returned {count} records"):
            plan_store.task_record("sdksource:task")


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
    _local_task(root, "task", "Task", "Body")
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
    _local_task(root, "task", "Task", "Body")
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


def test_task_locate_answers_the_copys_placement_and_looks_nothing_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store's `task locate --no-counterpart`, writing nothing and looking nothing up.

    It answers the destination the copy places a task at, with no counterpart and no carriers
    even where the destination holds the task's copy, and every refusal is an :class:`OSError`.
    """
    _routed_sources(monkeypatch, tmp_path, home="public")
    _local_task(tmp_path / "draft", "one", "one", "One task.", repositories=[HOME_REPOSITORY])
    source = "draft:one"
    before = sorted((tmp_path / "home").rglob("*.md"))

    found = plan_store.task_locate(source, "home")
    assert (found.destination.root, found.counterpart, found.carriers) == ("home", None, None)
    assert sorted((tmp_path / "home").rglob("*.md")) == before, "locating wrote to the board"
    with pytest.raises(OSError):
        plan_store.task_locate(source, "nowhere")


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


def test_the_tracked_configuration_declares_its_drafting_sources_private_and_its_boards_not() -> (
    None
):
    """The store's own resolution, read through `config show`, answers each source's visibility.

    The sources a plan or a follow-up is drafted or launched from are declared private, and
    the boards public.
    """
    assert plan_store.declared_private("authoring")
    assert plan_store.declared_private("follow-up-runs")
    assert plan_store.declared_private("test-fixtures")
    assert not plan_store.declared_private("plans")
    assert not plan_store.declared_private("followups")
    assert plan_store.declared_private("drafts")
