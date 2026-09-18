"""Every plan-store listing is read to its last page, against the installed `onetaskgraph`.

A listing the store answers is one page — its `page_size` setting, 50 unless configured —
with a `next` cursor when more remain, and `just follow-ups-handle-comments` once read the
`followups` board as its first page and answered "no new feedback" for a run whose every
issue sat on the second. What is proven here is `plan_store.every_page` and each reader
routed through it, over real `local-md` sources with nothing in the store's place: a
project past the default page with no page-size override, the shape of that incident, and
`ONETASKGRAPH_PAGE_SIZE` — the store's own environment layer for the setting — making a
small source span pages for the rest.
"""

from __future__ import annotations

from pathlib import Path

import follow_up_variables
import pytest
from test_follow_up_comments import BOARD, _filed

from orchestrator import follow_up_comments, plan_store
from orchestrator import follow_up_tickets as tickets
from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.project_store import PlanNode, frontmatter, write_plan_project

#: The store's default page, which the default-page case has to pass without naming it.
DEFAULT_PAGE = 50
SOURCE = "pagedsource"


def _source(monkeypatch: pytest.MonkeyPatch, name: str, root: Path) -> None:
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__PLUGIN", WRITABLE_PLUGIN)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__CONFIG__ROOT", str(root))


def _tasks(count: int, **extra: object) -> list[PlanNode]:
    return [
        PlanNode(id=f"t{i:02d}", title=f"Task {i}", task=f"Body {i}.") for i in range(1, count + 1)
    ]


def test_read_tasks_reads_a_project_past_the_stores_default_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """More tasks than one default page, no page-size override: every one is returned."""
    monkeypatch.delenv("ONETASKGRAPH_PAGE_SIZE", raising=False)
    root = tmp_path / "source"
    nodes = _tasks(DEFAULT_PAGE + 5)
    # A dependency from the last page onto the first, so the edge resolves across pages.
    nodes[-1] = PlanNode(id="t55", title="Task 55", task="Body 55.", deps=["t01"])
    write_plan_project(root, {"name": "demo", "tasks": nodes})
    _source(monkeypatch, SOURCE, root)

    # The premise, read off the installed store rather than assumed: the first page as the
    # SDK reports it stops short and names a cursor.
    first = plan_store.sdk(plan_store.client().task_list(source=[SOURCE], project="demo"))
    assert len(first.items) == DEFAULT_PAGE
    assert first.next is not None

    records = plan_store.read_tasks(f"{SOURCE}:demo")

    assert [record.node_id for record in records] == [node["id"] for node in nodes]
    assert records[-1].deps == ("t01",)
    plan, _ = plan_store.read_project(f"{SOURCE}:demo")
    assert len(plan["tasks"]) == DEFAULT_PAGE + 5


def test_identity_checks_hold_over_the_union_of_every_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicate node id and a second repository past the first page are still refused."""
    monkeypatch.delenv("ONETASKGRAPH_PAGE_SIZE", raising=False)
    duplicate = tmp_path / "duplicate"
    write_plan_project(duplicate, {"name": "demo", "tasks": _tasks(DEFAULT_PAGE + 1)})
    last = duplicate / "tasks" / "demo" / "t51.md"
    last.write_text(
        last.read_text(encoding="utf-8").replace(
            '"onepipeline.id": "t51"', '"onepipeline.id": "t01"'
        ),
        encoding="utf-8",
    )
    _source(monkeypatch, "duplicate", duplicate)
    with pytest.raises(OSError, match="duplicate task or node identities"):
        plan_store.read_tasks("duplicate:demo")

    repositories = tmp_path / "repositories"
    nodes = _tasks(DEFAULT_PAGE + 1)
    nodes[-1] = PlanNode(id="t51", title="Task 51", task="Body 51.", repo="github.com/acme/one")
    write_plan_project(repositories, {"name": "demo", "tasks": nodes})
    last = repositories / "tasks" / "demo" / "t51.md"
    last.write_text(
        last.read_text(encoding="utf-8").replace(
            '["github.com/acme/one"]', '["github.com/acme/one", "github.com/acme/two"]'
        ),
        encoding="utf-8",
    )
    _source(monkeypatch, "repositories", repositories)
    with pytest.raises(OSError, match="demo/t51' has more than one repository"):
        plan_store.read_tasks("repositories:demo")


def test_task_dependencies_are_read_past_one_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONETASKGRAPH_PAGE_SIZE", "1")
    root = tmp_path / "source"
    write_plan_project(
        root,
        {
            "name": "demo",
            "tasks": [
                PlanNode(id="a", title="A", task="Body."),
                PlanNode(id="b", title="B", task="Body."),
                PlanNode(id="c", title="C", task="Body.", deps=["a", "b"]),
            ],
        },
    )
    _source(monkeypatch, SOURCE, root)

    edges = plan_store.sdk(plan_store.client().task_deps(f"{SOURCE}:demo/c"))
    assert len(edges.items) == 1 and edges.next is not None

    records = {record.node_id: record for record in plan_store.read_tasks(f"{SOURCE}:demo")}
    assert records["c"].deps == ("a", "b")


def test_projects_and_documents_are_read_past_one_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONETASKGRAPH_PAGE_SIZE", "2")
    root = tmp_path / "source"
    names = ["alpha", "beta", "gamma", "delta", "epsilon"]
    for name in names:
        write_plan_project(root, {"name": name, "tasks": []})
    documents = root / "documents"
    documents.mkdir()
    for index in range(5):
        (documents / f"note-{index}.md").write_text(
            frontmatter({"title": f"Note {index}", "project": "alpha", "labels": ["x"]}, "Body.\n"),
            encoding="utf-8",
        )
    _source(monkeypatch, SOURCE, root)

    first = plan_store.sdk(plan_store.client().project_list(source=[SOURCE]))
    assert len(first.items) == 2 and first.next is not None

    assert sorted(project.title for project in plan_store.read_projects(SOURCE)) == sorted(names)
    read = plan_store.read_documents(f"{SOURCE}:alpha")
    assert sorted(document.title for document in read) == [f"Note {i}" for i in range(5)]
    assert {document.project for document in read} == {"alpha"}


def test_board_issues_returns_an_issue_past_the_first_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drafts = tmp_path / "follow-ups"
    drafts.mkdir()
    monkeypatch.setenv(follow_up_variables.root_name(), str(drafts))
    monkeypatch.setenv(follow_up_variables.plugin_name(), WRITABLE_PLUGIN)
    _source(monkeypatch, BOARD, tmp_path / "board")
    (tmp_path / "board").mkdir()
    filed = [_filed(drafts, f"run-{i}", f"cause-{i}") for i in range(5)]
    monkeypatch.setenv("ONETASKGRAPH_PAGE_SIZE", "2")

    first = plan_store.sdk(plan_store.client().task_list(source=[BOARD]))
    listed_first = [held.id.model_dump() for held in first.items]
    assert first.next is not None
    assert filed[-1] not in listed_first

    issues = follow_up_comments.board_issues(BOARD)

    assert [issue.id for issue in issues] == filed
    assert [issue.owner for issue in issues] == [tickets.RunId(f"run-{i}") for i in range(5)]


def test_a_listing_past_the_ceiling_is_refused_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONETASKGRAPH_PAGE_SIZE", "1")
    root = tmp_path / "source"
    write_plan_project(root, {"name": "demo", "tasks": _tasks(3)})
    _source(monkeypatch, SOURCE, root)
    monkeypatch.setattr(plan_store, "LISTING_PAGE_CEILING", 2)

    with pytest.raises(OSError) as refused:
        plan_store.read_tasks(f"{SOURCE}:demo")

    message = str(refused.value)
    assert message.startswith(f"listing project '{SOURCE}:demo' read 2 pages")
    assert "LISTING_PAGE_CEILING" in message

    # A listing that ends exactly at the ceiling is whole, not refused.
    monkeypatch.setattr(plan_store, "LISTING_PAGE_CEILING", 3)
    assert len(plan_store.read_tasks(f"{SOURCE}:demo")) == 3
