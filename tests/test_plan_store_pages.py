"""Every plan-store listing is read to its last page, against the installed `onetaskgraph`.

A listing the store answers is one page — its `page_size` setting, 50 unless configured —
with a `next` cursor when more remain, and the follow-up comment gathering once read the
`followups` board as its first page and answered "no new feedback" for a run whose every
issue sat on the second. What is proven here is `plan_store.every_page` and each reader
routed through it, over real `local-md` sources with nothing in the store's place, with
`ONETASKGRAPH_PAGE_SIZE` — the store's own environment layer for the setting — making a
small source span pages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import follow_up_variables
import pytest
from test_follow_up_comments import BOARD, _commented, _filed

from orchestrator import follow_up_comments, plan_store
from orchestrator.plan_store import WRITABLE_PLUGIN

SOURCE = "pagedsource"


def _source(monkeypatch: pytest.MonkeyPatch, name: str, root: Path) -> None:
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__PLUGIN", WRITABLE_PLUGIN)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__CONFIG__ROOT", str(root))


def test_the_narrowed_comment_listing_returns_an_issue_past_the_first_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The comment gathering's one copy, `commented_since` and all, read to its last page."""
    drafts = tmp_path / "follow-ups"
    drafts.mkdir()
    monkeypatch.setenv(follow_up_variables.root_name(), str(drafts))
    monkeypatch.setenv(follow_up_variables.plugin_name(), WRITABLE_PLUGIN)
    _source(monkeypatch, BOARD, tmp_path / "board")
    (tmp_path / "board").mkdir()
    filed = [_filed(drafts, f"run-{i}", f"cause-{i}") for i in range(5)]
    since = datetime.now(UTC).replace(microsecond=0)
    for issue in filed:
        _commented(issue, "Still happening.\n")
    monkeypatch.setenv("ONETASKGRAPH_PAGE_SIZE", "2")

    first = plan_store.sdk(
        plan_store.client().task_list(
            source=[BOARD], commented_since=follow_up_comments.instant(since)
        )
    )
    listed_first = [held.id.model_dump() for held in first.items]
    assert first.next is not None
    assert filed[-1] not in listed_first

    snapshot = follow_up_comments.gather(drafts, BOARD, since=since, say=lambda _: None)

    assert snapshot is not None
    assert sorted(one.id for one in snapshot.items) == sorted(filed)
    assert sorted(str(one.owner) for one in snapshot.items) == [f"run-{i}" for i in range(5)]


def test_a_listing_past_the_ceiling_is_refused_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONETASKGRAPH_PAGE_SIZE", "1")
    root = tmp_path / "source"
    _source(monkeypatch, SOURCE, root)
    # Three tasks of one project, written as the `local-md` plugin stores them.
    (root / "projects").mkdir(parents=True)
    (root / "projects" / "demo.md").write_text("---\ntitle: demo\nstatus: todo\n---\n")
    (root / "tasks").mkdir()
    for i in range(3):
        (root / "tasks" / f"t{i}.md").write_text(
            f"---\ntitle: Task {i}\nstatus: todo\nproject: demo\n---\nBody {i}.\n"
        )
    monkeypatch.setattr(plan_store, "LISTING_PAGE_CEILING", 2)

    def listing() -> list[object]:
        pages = plan_store.every_page(
            f"project '{SOURCE}:demo'",
            plan_store.client().task_list,
            source=[SOURCE],
            project="demo",
        )
        return [item for page in pages for item in page.items]

    with pytest.raises(OSError) as refused:
        listing()

    message = str(refused.value)
    assert message.startswith(f"listing project '{SOURCE}:demo' read 2 pages")
    assert "LISTING_PAGE_CEILING" in message

    # A listing that ends exactly at the ceiling is whole, not refused.
    monkeypatch.setattr(plan_store, "LISTING_PAGE_CEILING", 3)
    assert len(listing()) == 3
