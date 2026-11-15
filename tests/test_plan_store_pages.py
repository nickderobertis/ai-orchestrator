"""Every plan-store listing is read to its last page, against the installed `onetaskgraph`.

A listing the store answers is one page — its `page_size` setting, 50 unless configured —
with a `next` cursor when more remain, and a reader that took the first page for the whole
answered "nothing more" for every item on the second. What is proven here is
`plan_store.every_page` over a real `local-md` source with nothing in the store's place, with
`ONETASKGRAPH_PAGE_SIZE` — the store's own environment layer for the setting — making a
small source span pages.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import plan_store
from orchestrator.plan_store import WRITABLE_PLUGIN

SOURCE = "pagedsource"


def _source(monkeypatch: pytest.MonkeyPatch, name: str, root: Path) -> None:
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__PLUGIN", WRITABLE_PLUGIN)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__CONFIG__ROOT", str(root))


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
