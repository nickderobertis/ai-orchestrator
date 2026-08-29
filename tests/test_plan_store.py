"""The store reader and the one record writer this repository puts back into a plan.

Reading is exercised through `tests/test_criteria_guard.py`, which drives it as the
plan a check is made against. What is proven here is the half that has no other
caller: resolving a source to the directory it stores records in, naming the document
one task lives in, and setting exactly one metadata entry of that document while
leaving every other byte of it alone.

That last property is the one worth stating. A plan record is an operator's authored
document, and a writer that reformatted it would make the review gate the thing that
most often changes the content it reviews — so a frontmatter shape this cannot edit
that narrowly is refused rather than rewritten.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypedDict

import pytest

from orchestrator import plan_store


class Setting(TypedDict):
    """One entry of the store CLI's `config show` answer, in the fields read here."""

    key: object
    value: object


class Configuration(TypedDict):
    """The store CLI's `config show` answer, narrowed to what `source_root` reads.

    `settings` is deliberately `object` rather than `list[Setting]`: half the journeys
    below hand it something it is not, because that list is another program's open
    contract and what is under test is the reader refusing what it cannot account for.
    """

    settings: object


#: A record in the shape `orchestrator/project_store.py` writes and `examples/tasks/`
#: ships: JSON-quoted keys, JSON values, two spaces in, under a `metadata:` block.
RECORD = """---
title: "feat: add the route"
project: "demo"
status: "todo"
metadata:
  "onepipeline.id": "route"
  "onepipeline.persona": "engineer"
---

## What

Add the route.
"""


def _settings(**values: object) -> Configuration:
    return Configuration(settings=[Setting(key=key, value=value) for key, value in values.items()])


def _store(answer: Configuration) -> Callable[[Sequence[str]], Configuration]:
    def read(arguments: Sequence[str]) -> Configuration:
        assert list(arguments) == ["config", "show"]
        return answer

    return read


def _rooted(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(
        plan_store,
        "store_json",
        _store(
            _settings(
                **{
                    "sources.demo.plugin": "local-md",
                    "sources.demo.config.root": str(root),
                }
            )
        ),
    )


def test_a_source_resolves_to_the_directory_it_stores_records_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rooted(monkeypatch, tmp_path / "store")
    assert plan_store.source_root("demo") == tmp_path / "store"


def test_a_relative_root_resolves_against_this_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """`onetaskgraph.yaml` states `.plans-local`, and the record is below this checkout."""
    monkeypatch.setattr(
        plan_store,
        "store_json",
        _store(
            _settings(
                **{"sources.demo.plugin": "local-md", "sources.demo.config.root": ".plans-local"}
            )
        ),
    )
    assert plan_store.source_root("demo") == plan_store.REPO_ROOT / ".plans-local"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (Configuration(settings="not a list"), "settings list"),
        (_settings(**{"sources.demo.plugin": "github-projects"}), "'local-md'"),
        (_settings(**{"sources.demo.plugin": "local-md"}), "names no root"),
        (
            _settings(**{"sources.demo.plugin": "local-md", "sources.demo.config.root": ""}),
            "names no root",
        ),
    ],
)
def test_a_source_this_may_not_write_is_refused_by_name(
    answer: Configuration, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan_store, "store_json", _store(answer))
    with pytest.raises(OSError, match=expected):
        plan_store.source_root("demo")


def test_a_setting_without_a_string_key_is_passed_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings list is the CLI's open contract, so an entry this cannot read is skipped."""
    monkeypatch.setattr(
        plan_store,
        "store_json",
        _store(
            Configuration(
                settings=[
                    "not an object",
                    {"value": "keyless"},
                    Setting(key=7, value="not a string key"),
                    Setting(key="sources.demo.plugin", value="local-md"),
                    Setting(key="sources.demo.config.root", value="/tmp/demo"),
                ]
            )
        ),
    )
    assert plan_store.source_root("demo") == Path("/tmp/demo")


def _written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str = RECORD) -> Path:
    document = tmp_path / "store" / "tasks" / "demo" / "route.md"
    document.parent.mkdir(parents=True)
    document.write_text(content, encoding="utf-8")
    _rooted(monkeypatch, tmp_path / "store")
    return document


def test_a_task_resolves_to_the_document_holding_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _written(tmp_path, monkeypatch)
    assert plan_store.task_document("demo", "demo/route") == document


@pytest.mark.parametrize("native", ["route", "demo/", "/route", "demo/a/b"])
def test_a_task_id_that_is_not_a_local_records_path_is_refused(
    native: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _written(tmp_path, monkeypatch)
    with pytest.raises(OSError, match="`<project>/<task>`"):
        plan_store.task_document("demo", native)


def test_a_task_with_no_record_on_disk_is_named_rather_than_written_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _written(tmp_path, monkeypatch)
    with pytest.raises(OSError, match="has no record at"):
        plan_store.task_document("demo", "demo/absent")


def test_writing_a_record_sets_one_entry_and_leaves_every_other_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _written(tmp_path, monkeypatch)
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written
    assert '  "onepipeline.id": "route"' in written
    assert '  "onepipeline.persona": "engineer"' in written
    assert written.endswith("## What\n\nAdd the route.\n")


def test_writing_a_record_twice_replaces_it_rather_than_appending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _written(tmp_path, monkeypatch)
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "one"})
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "two"})
    written = document.read_text(encoding="utf-8")
    assert written.count('"orchestrator.plan-review"') == 1
    assert '{"key": "two"}' in written


def test_a_record_that_states_no_metadata_block_gains_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _written(tmp_path, monkeypatch, '---\ntitle: "t"\nstatus: "todo"\n---\n\nbody\n')
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert 'metadata:\n  "orchestrator.plan-review": {"key": "abc"}\n---' in written
    assert written.endswith("\nbody\n")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("title: t\n", "does not open with"),
        ('---\ntitle: "t"\n', "never closed"),
        ('---\nmetadata: {"a": 1}\n---\n\nbody\n', "on one line"),
    ],
)
def test_frontmatter_this_cannot_edit_narrowly_is_refused_rather_than_rewritten(
    content: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _written(tmp_path, monkeypatch, content)
    before = document.read_text(encoding="utf-8")
    with pytest.raises(OSError, match=expected):
        plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    assert document.read_text(encoding="utf-8") == before


def test_a_record_whose_metadata_block_is_followed_by_more_frontmatter_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The block ends at the first line that is not an indented entry, not at the fence."""
    document = _written(
        tmp_path,
        monkeypatch,
        '---\nmetadata:\n  "onepipeline.id": "route"\nstatus: "todo"\n---\n\nbody\n',
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    assert document.read_text(encoding="utf-8") == (
        "---\nmetadata:\n"
        '  "onepipeline.id": "route"\n'
        '  "orchestrator.plan-review": {"key": "abc"}\n'
        'status: "todo"\n---\n\nbody\n'
    )


def test_a_source_holding_no_root_yet_lists_no_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host that has never planned is not an error; `just plan` creates the root."""
    _rooted(monkeypatch, tmp_path / "never-planned")
    assert plan_store.local_projects("demo") == []


def test_every_project_a_source_holds_is_listed_as_a_qualified_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmp_path / "store" / "projects"
    projects.mkdir(parents=True)
    (projects / "beta.md").write_text("---\ntitle: b\n---\n", encoding="utf-8")
    (projects / "alpha.md").write_text("---\ntitle: a\n---\n", encoding="utf-8")
    (projects / "notes.txt").write_text("ignored", encoding="utf-8")
    _rooted(monkeypatch, tmp_path / "store")
    assert plan_store.local_projects("demo") == ["demo:alpha", "demo:beta"]


def test_the_written_entry_reads_back_as_the_value_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rendering is JSON, so a record round-trips through the store unchanged."""
    document = _written(tmp_path, monkeypatch)
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "a", "by": "b"})
    line = next(
        one
        for one in document.read_text(encoding="utf-8").splitlines()
        if '"orchestrator.plan-review"' in one
    )
    assert json.loads(line.split(": ", 1)[1]) == {"key": "a", "by": "b"}


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        (
            '---\nmetadata:\n  "a": 1\nstatus: "todo"\nmetadata:\n  "b": 2\n---\n\nbody\n',
            "opens `metadata` 2 times",
        ),
        (
            '---\nmetadata:\n  "a": 1\n  unquoted: 2\n---\n\nbody\n',
            "cannot edit around",
        ),
        (
            '---\nmetadata:\n  "a": 1\n  - a list entry\n---\n\nbody\n',
            "cannot edit around",
        ),
    ],
    ids=["two blocks", "unquoted entry", "sequence"],
)
def test_a_metadata_block_this_cannot_account_for_is_refused(
    frontmatter: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guessing costs a duplicate YAML key rather than a visible failure, so it refuses.

    A block scanned only as far as its leading run of entries would leave a second entry
    for a key already further down it, and a record with two entries for one key is one
    a reader resolves silently and differently from the writer.
    """
    document = _written(tmp_path, monkeypatch, frontmatter)
    before = document.read_text(encoding="utf-8")
    with pytest.raises(OSError, match=expected):
        plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    assert document.read_text(encoding="utf-8") == before


def test_an_entry_further_down_the_block_is_replaced_rather_than_duplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole block is searched, so a standing record anywhere in it is the one edited."""
    document = _written(
        tmp_path,
        monkeypatch,
        "---\nmetadata:\n"
        '  "onepipeline.id": "route"\n'
        '  "orchestrator.plan-review": {"key": "old"}\n'
        '  "onepipeline.persona": "engineer"\n'
        "---\n\nbody\n",
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "new"})
    written = document.read_text(encoding="utf-8")
    assert written.count('"orchestrator.plan-review"') == 1
    assert '{"key": "new"}' in written
    assert '  "onepipeline.persona": "engineer"' in written
