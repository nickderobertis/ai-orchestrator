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
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypedDict

import plan_fixture_root
import pytest

from orchestrator import plan_store, project_store

#: The source `onetaskgraph.yaml` roots at `plan_fixture_root.ROOT`, which
#: `tests/test_plan_source_roots.py` holds that configuration to. Spelled here so the
#: journey below reads through the store's own configured source rather than through a
#: root this module points at, which is the difference between exercising the real
#: reader and exercising a temporary directory.
FIXTURE_SOURCE = "test-fixtures"


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
    """`onetaskgraph.yaml` states `.plans`, and the record is below this checkout."""
    monkeypatch.setattr(
        plan_store,
        "store_json",
        _store(
            _settings(**{"sources.demo.plugin": "local-md", "sources.demo.config.root": ".plans"})
        ),
    )
    assert plan_store.source_root("demo") == plan_store.REPO_ROOT / ".plans"


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
        (
            _settings(**{"sources.demo.plugin": "local-md", "sources.demo.config.root": "pl\0ans"}),
            "NUL character",
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


def test_a_root_that_does_not_exist_yet_is_created_rather_than_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host that has never planned has no root, and its first launch makes one."""
    root = tmp_path / "never-planned"
    _rooted(monkeypatch, root)

    assert plan_store.ensure_writable_source_root("demo") == root
    assert root.is_dir(), "the root a launch is about to author into was not created"


def test_a_root_a_launch_can_already_write_into_is_answered_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary case: the directory is there, and nothing about it is disturbed."""
    root = tmp_path / "store"
    root.mkdir()
    (root / "projects").mkdir()
    _rooted(monkeypatch, root)

    assert plan_store.ensure_writable_source_root("demo") == root
    assert (root / "projects").is_dir(), "resolving the root disturbed what it already held"


def test_a_root_that_is_not_a_directory_is_refused_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path holding a file is refused before a launch writes a plan into it."""
    root = tmp_path / "store"
    root.write_text("not a plan store\n", encoding="utf-8")
    _rooted(monkeypatch, root)

    with pytest.raises(OSError, match=f"{root}, which is not a directory"):
        plan_store.ensure_writable_source_root("demo")


def test_a_root_that_cannot_be_created_is_refused_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing root below a parent nothing may write into is the refusal, not a crash."""
    parent = tmp_path / "sealed"
    parent.mkdir(mode=0o500)
    root = parent / "store"
    _rooted(monkeypatch, root)

    try:
        with pytest.raises(OSError, match=f"{root}, which could not be created"):
            plan_store.ensure_writable_source_root("demo")
    finally:
        parent.chmod(0o700)


def test_a_root_this_process_may_not_write_into_is_refused_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing but read-only root is the case a `mkdir` alone would pass over."""
    root = tmp_path / "store"
    root.mkdir(mode=0o500)
    _rooted(monkeypatch, root)

    try:
        with pytest.raises(OSError, match=f"{root}, which this process may not write into"):
            plan_store.ensure_writable_source_root("demo")
    finally:
        root.chmod(0o700)


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


def test_a_project_resolves_to_the_document_holding_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan-level review record is written into the project's own document."""
    _written(tmp_path, monkeypatch)
    document = tmp_path / "store" / "projects" / "demo.md"
    document.parent.mkdir(parents=True)
    document.write_text('---\ntitle: "demo"\n---\n', encoding="utf-8")
    assert plan_store.project_document("demo", "demo") == document


@pytest.mark.parametrize("native", ["..", "demo/route", "", "."])
def test_a_project_id_that_is_not_a_local_records_name_is_refused(
    native: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`..` is the value that would carry the write out of the root, so it is refused by shape."""
    _written(tmp_path, monkeypatch)
    with pytest.raises(OSError, match="not a local record's own name"):
        plan_store.project_document("demo", native)


def test_a_project_with_no_record_on_disk_is_named_rather_than_written_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _written(tmp_path, monkeypatch)
    with pytest.raises(OSError, match="has no record at"):
        plan_store.project_document("demo", "absent")


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


@pytest.mark.parametrize(
    "record", ["documents/demo.md", "demo.md", "tasks/demo.md", "projects/demo/route.md"]
)
def test_a_record_in_neither_layout_is_refused_rather_than_staged_by_guess(
    record: str, tmp_path: Path
) -> None:
    """Where a replacement is staged is decided from the layout, so no layout is no write."""
    document = tmp_path / "store" / record
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(RECORD, encoding="utf-8")
    with pytest.raises(OSError) as refused:
        plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    assert str(refused.value).startswith(f"{document} is in neither record layout")
    assert "`<root>/projects/<project>.md` or `<root>/tasks/<project>/<task>.md`" in str(
        refused.value
    )
    assert document.read_text(encoding="utf-8") == RECORD
    written = Path("store", record)
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == sorted(
        [*written.parents][:-1] + [written]
    ), "a refused write still left a staged file behind"


def test_a_replacement_whose_rename_is_refused_leaves_nothing_staged_at_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root admits the staged file where the record's directory refuses the rename.

    Staging beside the record failed before anything was created in a directory nothing
    may write into; staged at the root, the file exists before the rename is refused, and
    that root is shared by every writer of the store.
    """
    document = _written(tmp_path, monkeypatch)
    directory = document.parent
    before = directory.stat().st_mode
    directory.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    finally:
        directory.chmod(before)
    assert document.read_text(encoding="utf-8") == RECORD
    assert sorted(path.name for path in (tmp_path / "store").iterdir()) == ["tasks"], (
        "a refused rename left its staged file at the source root"
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
            '---\nmetadata:\n  "a": 1\n  - a list entry\n---\n\nbody\n',
            "cannot edit around",
        ),
        (
            '---\nmetadata:\n  "a": 1\n    stranded: 2\n---\n\nbody\n',
            "cannot edit around",
        ),
        (
            '---\nmetadata:\n  "a": 1\n\n  "b": 2\n---\n\nbody\n',
            "blank line no entry above it accounts for",
        ),
        (
            '---\nmetadata:\n    "a": 1\n  "b": 2\n---\n\nbody\n',
            "cannot edit around",
        ),
    ],
    ids=[
        "two blocks",
        "sequence",
        "deeper than a stated value",
        "unaccounted blank line",
        "shallower than the entries beside it",
    ],
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


def test_the_rendering_this_host_produces_is_one_this_writer_can_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconciled against the producer rather than a replica of it.

    `orchestrator/project_store.py` renders the records `just plan` writes, and this
    module edits them. Two modules independently describing one file format is how the
    two drift apart in silence, so the fixture here is that producer's own output rather
    than a hand-authored copy of what it is believed to emit: a rendering change that
    this writer could not edit fails here rather than at the next review record.
    """
    root = tmp_path / "store"
    project_store.write_plan_project(
        root,
        {
            "name": "drift",
            "tasks": [
                {"id": "route", "title": "feat: route", "task": "body", "persona": "engineer"}
            ],
        },
    )
    document = next((root / "tasks").rglob("*.md"))
    monkeypatch.chdir(tmp_path)
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written
    assert "onepipeline.id" in written


def test_one_writer_renders_the_entry_both_of_this_repositorys_writers_produce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two ways a record here gains a metadata entry write one line, not two alike.

    A record is written whole by `orchestrator/project_store.py` and edited in place by
    this module, and the second used to carry its own copy of the rendering. The copies
    were identical, which is what made them a hazard rather than a bug: the reader beside
    them accounts for the shapes it meets one at a time, so a writer that drifted would
    produce records only the other writer could read — and nothing would say so until a
    review record could not be edited into a plan.

    Reconciled against the producer rather than a replica of it, exactly as the test above
    is: what this compares is the line each writer actually emits for one key and one
    value, so a second rendering reintroduced anywhere fails here.
    """
    document = _written(tmp_path, monkeypatch, "---\ntitle: t\n---\n\nbody\n")
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    edited = document.read_text(encoding="utf-8").splitlines()
    whole = project_store.frontmatter(
        {"title": "t", "metadata": {"orchestrator.plan-review": {"key": "abc"}}}, "body"
    ).splitlines()
    entry = project_store.metadata_entry("orchestrator.plan-review", {"key": "abc"})
    assert entry in edited, edited
    assert entry in whole, whole


def test_a_block_the_plan_store_rendered_itself_is_editable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain YAML key is the rendering the store itself writes, so it is not foreign.

    The settlement write-back re-renders every task of the plan a run was launched from,
    and it renders each metadata key plain rather than JSON-quoted. A writer that refused
    that shape would leave every review record unwritable the moment a run settled.
    """
    document = _written(
        tmp_path,
        monkeypatch,
        "---\nmetadata:\n"
        "  onepipeline.id: contracts\n"
        "  onepipeline.persona: engineer\n"
        "---\n\nbody\n",
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert "  onepipeline.id: contracts" in written
    assert "  onepipeline.persona: engineer" in written
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written


def test_a_plainly_rendered_entry_of_the_same_key_is_replaced_rather_than_duplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the key of a plain entry is what keeps a re-review from duplicating it.

    Two entries for one key is a record a reader resolves silently and differently from
    the writer, so the key a plain entry states has to be read as readily as a quoted one.
    """
    document = _written(
        tmp_path,
        monkeypatch,
        "---\nmetadata:\n"
        "  onepipeline.id: contracts\n"
        '  orchestrator.plan-review: {"key": "old"}\n'
        "---\n\nbody\n",
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "new"})
    written = document.read_text(encoding="utf-8")
    assert written.count("orchestrator.plan-review") == 1
    assert '{"key": "new"}' in written
    assert "  onepipeline.id: contracts" in written


def test_a_nested_entry_the_write_back_projected_is_kept_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled plan holds entries whose value is a block, and they are somebody else's.

    A settlement projects entries whose value is a block onto the task it ran. What those
    entries are called and what they hold belongs to whoever writes them, so this asserts
    only what this writer owes: an entry it did not write survives it whole, contents and
    all. A writer that could not account for one refused every task of a settled plan.

    The fixture is authored here rather than obtained from the producer, and deliberately:
    what renders these is the engine's settlement write-back, which needs a launched run to
    reach and is not this tier's to drive. The producer-backed gate over the rendering this
    host can obtain — the store's own copy — is in the end-to-end tier beside it.
    """
    document = _written(
        tmp_path,
        monkeypatch,
        "---\nmetadata:\n"
        "  onepipeline.id: route\n"
        "  a.nested.entry:\n"
        "    inner: value\n"
        "  another.nested.entry:\n"
        "    first: one\n"
        "    second: two\n"
        "---\n\nbody\n",
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert "  a.nested.entry:\n    inner: value\n" in written
    assert "  another.nested.entry:\n    first: one\n    second: two\n" in written
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written


def test_an_entry_whose_value_is_a_block_sequence_is_kept_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third way an entry leaves its value below it, and the one that is not indented.

    YAML lets a block sequence stand at its own key's indent rather than beneath it, and
    the plan store's renderer takes that option — so a stepped node's `onepipeline.steps:`
    opens a block whose `- id: …` items sit *level* with the entries around them, where a
    writer placing a line by indentation alone cannot tell one from an entry.

    The fixture is authored here for the reason the nested-entry test above gives, and the
    producer-backed gate over it — the store's own copy verb, rendering a real stepped node
    — is `tests/plan_tooling/test_plan_store_record_shapes_e2e.py`.
    """
    document = _written(
        tmp_path,
        monkeypatch,
        "---\nmetadata:\n"
        "  onepipeline.id: landing\n"
        "  onepipeline.steps:\n"
        "  - id: build\n"
        "    persona: engineer\n"
        "    task: |\n"
        "      ## What\n"
        "\n"
        "      Add the route.\n"
        "  - id: prove\n"
        "    persona: engineer\n"
        "  onepipeline.persona: engineer\n"
        "---\n\nbody\n",
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert "  onepipeline.steps:\n  - id: build\n    persona: engineer\n" in written
    assert "      ## What\n\n      Add the route.\n  - id: prove\n" in written
    # The entry after the sequence is still seen as an entry rather than swallowed by it.
    assert "  onepipeline.persona: engineer" in written
    assert "  onepipeline.id: landing" in written
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written


def test_a_blank_line_before_a_keys_indentless_items_leaves_them_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank line says nothing about whose the lines below a key are.

    Which matters where it stands here: an indentless sequence is told from a mapping by
    the first line below the key, so a blank one taken for that line would refuse a
    record YAML accepts — and the record it would refuse is a stepped node, the shape
    that made a sequence readable at all.
    """
    document = _written(
        tmp_path,
        monkeypatch,
        "---\nmetadata:\n"
        "  onepipeline.steps:\n"
        "\n"
        "  - id: build\n"
        "    persona: engineer\n"
        "  onepipeline.persona: engineer\n"
        "---\n\nbody\n",
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert "  onepipeline.steps:\n\n  - id: build\n    persona: engineer\n" in written
    assert "  onepipeline.persona: engineer" in written
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("  onepipeline.id: landing\n  - id: build\n", id="after-a-valued-entry"),
        pytest.param("  - id: build\n", id="opening-the-block"),
    ],
)
def test_a_sequence_item_nothing_opened_a_block_for_is_still_refused(
    block: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading a sequence item is conditional on an entry above having opened a block.

    Which is the whole of what keeps the shape above from widening this writer: a `- `
    line is the one shape whose position says nothing about whose it is, so with nothing
    open it is the unplaceable line it always was. Editing around one would leave a second
    entry for a key already present, which a reader resolves differently from the writer.
    """
    document = _written(tmp_path, monkeypatch, f"---\nmetadata:\n{block}---\n\nbody\n")
    with pytest.raises(OSError, match="cannot edit around"):
        plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(
            "  prose: |\n    hello\n  - id: stray\n",
            id="beside-a-block-scalars-text",
        ),
        pytest.param(
            "  mapping:\n    child: value\n  - id: stray\n",
            id="beside-a-mappings-entries",
        ),
        pytest.param(
            "  mapping:\n\n    child: value\n  - id: stray\n",
            id="beside-a-mappings-entries-past-a-blank-line",
        ),
    ],
)
def test_a_sequence_item_beside_a_block_that_is_not_a_sequence_is_refused(
    block: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening a block is not on its own what makes a `- ` line level with the key its own.

    A key that states nothing after its colon and a key that states a block-scalar
    indicator both leave their value below, so both read as having opened a block — but a
    scalar's contents are text and a mapping's are its entries, and neither takes items.
    A `- ` line beside either is the unplaceable line it always was, and reading it as
    content of the block above would swallow a line this writer never accounted for.

    Each record is refused whole and byte-for-byte, which is the property this writer
    exists to hold: the alternative to refusing a shape it cannot read is editing around
    it, and what that leaves is a second entry for a key already present.
    """
    record = f"---\nmetadata:\n{block}---\n\nbody\n"
    document = _written(tmp_path, monkeypatch, record)
    with pytest.raises(OSError, match="cannot edit around"):
        plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    assert document.read_text(encoding="utf-8") == record


def test_an_entry_whose_value_is_a_block_scalar_keeps_its_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other way an entry leaves its value to the lines beneath it.

    A settlement records the detail of what a node reached as a block scalar, and the
    indicator sits where a value would — so the shape reads as an ordinary valued entry
    unless it is recognised, and the lines it owns are then refused as belonging to
    nothing. An entry after it must still be seen as an entry.
    """
    document = _written(
        tmp_path,
        monkeypatch,
        "---\nmetadata:\n"
        "  onepipeline.id: route\n"
        "  detail: |-\n"
        "    line one\n"
        "    line two\n"
        "  after: value\n"
        "---\n\nbody\n",
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert "  detail: |-\n    line one\n    line two\n" in written
    assert "  after: value" in written
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written


def test_a_block_scalar_whose_content_has_a_paragraph_break_is_kept_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The blank line inside a block scalar is content of it rather than the end of the block.

    A settlement records prose, and prose has paragraph breaks. Read as the end of the
    metadata block, one puts the review record between two halves of somebody's sentence
    and leaves the rest of the scalar stranded below it — a corrupted record rather than a
    refusal, which is the failure this writer exists to avoid.
    """
    document = _written(
        tmp_path,
        monkeypatch,
        "---\nmetadata:\n"
        "  onepipeline.id: route\n"
        "  detail: |-\n"
        "    line one\n"
        "\n"
        "    line three\n"
        "  after: value\n"
        "---\n\nbody\n",
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert "  detail: |-\n    line one\n\n    line three\n  after: value\n" in written
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written


def test_a_blank_line_the_block_merely_ends_on_is_left_where_it_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of scanning over blank lines: one after the last entry is not in it.

    Reading a trailing blank line as part of the block would write the record after it,
    putting the entry among whatever the blank line was separating the block from rather
    than at the end of the block it belongs to.
    """
    document = _written(
        tmp_path,
        monkeypatch,
        '---\nmetadata:\n  "a": 1\n\nstatus: "todo"\n---\n\nbody\n',
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "abc"})
    written = document.read_text(encoding="utf-8")
    assert '  "a": 1\n  "orchestrator.plan-review": {"key": "abc"}\n\nstatus: "todo"\n' in written


def test_a_standing_record_rendered_as_a_block_is_replaced_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing an entry takes the lines it owns with it, or its remains outlive it.

    A record the store re-rendered as a nested mapping is still one entry, so writing over
    it must remove every line beneath it too — otherwise the old value's contents are left
    stranded under the new one, which reads back as neither.
    """
    document = _written(
        tmp_path,
        monkeypatch,
        "---\nmetadata:\n"
        "  onepipeline.id: route\n"
        "  orchestrator.plan-review:\n"
        "    key: stale\n"
        "    by: review-plan\n"
        "---\n\nbody\n",
    )
    plan_store.write_metadata(document, "orchestrator.plan-review", {"key": "fresh"})
    written = document.read_text(encoding="utf-8")
    assert "stale" not in written
    assert "by: review-plan" not in written
    assert '  "orchestrator.plan-review": {"key": "fresh"}' in written
    assert "  onepipeline.id: route" in written


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


#: One document as the store reports it, in the fields the reader narrows to.
#:
#: **The labels are the store's canonical `{id, name, color}`**, which is the frozen
#: plugin contract every plugin constructs — not the `LabelInput` sugar an author types
#: into a record, which admits a bare string and is normalised away on read.
#: `tests/plan_tooling/test_plan_store_record_shapes_e2e.py` takes both from the installed
#: store, and the reason this fixture states the canonical one is that the reader was
#: written against the sugar: every labelled document was refused, and a fixture in the
#: sugar's shape is what let that stand.
DOCUMENT = {
    "id": "demo:demo-design",
    "item": {
        "id": "demo-design",
        "title": "Design: demo",
        "content": "## What\n\nA route.\n",
        "project": "demo",
        "labels": [{"id": "design", "name": "design", "color": "ff0000"}],
        "repositories": [],
        "metadata": {"onetaskgraph.origin": "drafted:demo-design"},
        "location": {"path": "/test/documents/demo-design.md"},
    },
}


def _listing(*pages: object) -> Callable[[Sequence[str]], dict[str, object]]:
    """Answer one `document list` page per call, in order."""
    remaining = list(pages)

    def read(arguments: Sequence[str]) -> dict[str, object]:
        assert list(arguments)[:2] == ["document", "list"]
        answer = remaining.pop(0)
        assert isinstance(answer, dict)
        return answer

    return read


def test_every_page_of_a_projects_documents_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paging is a contract this reader depends on, exactly as it is for tasks."""
    second = {"id": "demo:demo-notes", "item": dict(DOCUMENT["item"], id="demo-notes")}
    monkeypatch.setattr(
        plan_store,
        "store_json",
        _listing({"items": [DOCUMENT], "next": "page-2"}, {"items": [second], "next": None}),
    )
    documents = plan_store.read_documents("demo:demo")
    assert [document.qualified_id for document in documents] == [
        "demo:demo-design",
        "demo:demo-notes",
    ]
    assert documents[0].labels == ["design"]
    assert documents[0].location == {"path": "/test/documents/demo-design.md"}
    assert documents[0].metadata == {"onetaskgraph.origin": "drafted:demo-design"}


def test_a_label_is_read_by_name_whichever_shape_the_store_answers_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical mapping and the bare string, held to one answer.

    The store answers the mapping and only the mapping today, and the bare string is
    accepted because a reader that took only what it happens to see would be the same
    mistake pointing the other way — this repository stages a record's labels back as
    names, so it would refuse its own write.

    Only the name is kept, which is what makes the round trip a decision rather than a
    loss nobody stated: a colour read and not written back would be a field the write
    silently dropped.
    """
    mapped = dict(DOCUMENT["item"])
    bare = dict(DOCUMENT["item"], labels=["design", "second"])
    monkeypatch.setattr(
        plan_store,
        "store_json",
        _listing(
            {
                "items": [
                    {"id": "demo:mapped", "item": mapped},
                    {"id": "demo:bare", "item": bare},
                ],
                "next": None,
            }
        ),
    )
    assert [one.labels for one in plan_store.read_documents("demo:demo")] == [
        ["design"],
        ["design", "second"],
    ]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"items": "not a list"}, "not a list"),
        ({"items": [DOCUMENT], "next": ""}, "invalid next-page token"),
        ({"items": ["not an object"]}, "without a qualified id"),
        ({"items": [{"id": "demo:x"}]}, "without an object payload"),
        ({"items": [{"id": "demo:x", "item": {"title": 1}}]}, "no string title"),
        (
            {"items": [{"id": "demo:x", "item": {"id": "x", "title": "t", "content": 1}}]},
            "non-string content",
        ),
        (
            {"items": [{"id": "demo:x", "item": {"id": "x", "title": "t", "project": 1}}]},
            "non-string project",
        ),
        (
            {"items": [{"id": "demo:x", "item": {"id": "x", "title": "t", "labels": "no"}}]},
            "labels that are not a list",
        ),
        # A label is a name or an object naming one — the store's own canonical `{id,
        # name, color}` and the bare string its input sugar also admits. Anything else is
        # a label this reader cannot name, and a reader that fell back to `str()` would
        # carry a rendering of somebody else's payload into a record it writes back.
        (
            {"items": [{"id": "demo:x", "item": {"id": "x", "title": "t", "labels": [1]}}]},
            "neither a name nor an object naming one",
        ),
        (
            {
                "items": [
                    {"id": "demo:x", "item": {"id": "x", "title": "t", "labels": [{"id": "x"}]}}
                ]
            },
            "neither a name nor an object naming one",
        ),
        (
            {"items": [{"id": "demo:x", "item": {"id": "x", "title": "t", "repositories": [1]}}]},
            "repositories that are not",
        ),
        (
            {"items": [{"id": "demo:x", "item": {"id": "x", "title": "t", "metadata": []}}]},
            "metadata that is not an object",
        ),
        (
            {"items": [{"id": "demo:x", "item": {"id": "x", "title": "t", "location": "here"}}]},
            "location that is not an object",
        ),
        # The identity is held to a qualified `<source>:<native>` here rather than
        # wherever it is next used, because the write copies into the source half — a
        # type whose values are only sometimes qualified pushes that check onto every
        # caller, and the one that matters writes a record.
        ({"items": [{"id": "demo-design", "item": {"id": "x", "title": "t"}}]}, "not a qualified"),
    ],
)
def test_a_document_listing_this_cannot_account_for_is_refused(
    answer: dict[str, object], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another program's records, narrowed at the boundary rather than trusted past it."""
    monkeypatch.setattr(plan_store, "store_json", _listing(answer))
    with pytest.raises(OSError, match=expected):
        plan_store.read_documents("demo:demo")


def test_a_repeated_page_token_is_refused_rather_than_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store that answers its own token again would page for as long as it was asked."""
    page = {"items": [DOCUMENT], "next": "page-2"}
    monkeypatch.setattr(plan_store, "store_json", _listing(page, page))
    with pytest.raises(OSError, match="repeated next-page token"):
        plan_store.read_documents("demo:demo")


#: One project record as the store lists it, in the fields a reader locates a copy by.
#: Its own constant rather than a reuse of the document above: a project carries no
#: content and no labels, and what a reader asks it — where it is, and what it was copied
#: from — is a different set of fields.
PROJECT = {
    "id": "board:42",
    "item": {
        "id": "42",
        "title": "Deliver the checkout route",
        "location": {"url": "https://example.invalid/board/42"},
        "metadata": {"onetaskgraph.origin": "authoring:checkout-route"},
    },
}


def _projects(*pages: object) -> Callable[[Sequence[str]], dict[str, object]]:
    """Answer one `project list` page per call, in order."""
    remaining = list(pages)

    def read(arguments: Sequence[str]) -> dict[str, object]:
        assert list(arguments)[:4] == ["project", "list", "--source", "board"]
        answer = remaining.pop(0)
        assert isinstance(answer, dict)
        return answer

    return read


def test_every_page_of_a_sources_projects_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paging is a contract this reader depends on, exactly as it is for tasks.

    A destination holding more projects than one page is the ordinary case for the board
    this repository copies into, and the record a copy landed on may be any of them — so a
    reader that stopped at the first page would report a landed plan as one the
    destination does not hold.
    """
    second = {"id": "board:88", "item": dict(PROJECT["item"], id="88")}
    monkeypatch.setattr(
        plan_store,
        "store_json",
        _projects({"items": [PROJECT], "next": "page-2"}, {"items": [second], "next": None}),
    )
    projects = plan_store.read_projects("board")
    assert [project.qualified_id for project in projects] == ["board:42", "board:88"]
    assert projects[0].title == "Deliver the checkout route"
    assert projects[0].location == {"url": "https://example.invalid/board/42"}
    assert projects[0].metadata == {plan_store.ORIGIN_KEY: "authoring:checkout-route"}


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"items": ["not an object"]}, "without a qualified id"),
        ({"items": [{"id": "board:42"}]}, "without an object payload"),
        ({"items": [{"id": "board:42", "item": {"title": 1}}]}, "no string title"),
        (
            {"items": [{"id": "board:42", "item": {"title": "t", "metadata": []}}]},
            "metadata that is not an object",
        ),
        (
            {"items": [{"id": "board:42", "item": {"title": "t", "location": "here"}}]},
            "location that is not an object",
        ),
        # Held to a qualified id here rather than wherever it is next used, for the
        # reason a document's is: a caller reports this id as where a destination holds
        # a plan when the store says nothing about its location, and an unqualified one
        # addresses a project in no store.
        ({"items": [{"id": "42", "item": {"title": "t"}}]}, "not a qualified"),
    ],
)
def test_a_project_listing_this_cannot_account_for_is_refused(
    answer: dict[str, object], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another program's records, narrowed at the boundary rather than trusted past it.

    The metadata is the one that decides something: it is where the origin stamp lives,
    and a reader that took a non-object for it would answer that a destination holds no
    copy of a plan it is holding.
    """
    monkeypatch.setattr(plan_store, "store_json", _projects(answer))
    with pytest.raises(OSError, match=expected):
        plan_store.read_projects("board")


def test_a_listing_of_one_source_carrying_another_sources_record_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The narrowing is the whole of what this answer means, so it is checked.

    A caller reports one of these records as where a named destination holds a plan, and a
    record of some other source sends a reviewer to a project that destination does not
    hold. The query asked for one source; another program answering it is not a guarantee
    that it did.
    """
    strayed = {"id": "elsewhere:42", "item": dict(PROJECT["item"])}
    monkeypatch.setattr(plan_store, "store_json", _projects({"items": [PROJECT, strayed]}))
    with pytest.raises(OSError, match="record.s. of another source"):
        plan_store.read_projects("board")


def _document(
    *,
    project: str | None = "demo",
    labels: list[str] | None = None,
    repositories: list[str] | None = None,
) -> plan_store.StoreDocument:
    """One read document, in the shape the writer stages back.

    Keyword parameters rather than a merged mapping, so what a test may vary is stated
    and typed: the shape a `**overrides` helper takes is `object`, which types the call
    site as accepting anything and needs an escape at the constructor to get back out.
    """
    return plan_store.StoreDocument(
        qualified_id=plan_store.QualifiedDocumentId("demo:demo-design"),
        title="Design: demo",
        content="## What\n\nA route.\n",
        project=project,
        labels=["design"] if labels is None else labels,
        repositories=(
            ["github.com/nickderobertis/some-service"] if repositories is None else repositories
        ),
        metadata={"onetaskgraph.origin": "drafted:demo-design"},
        location={"path": "/test/documents/demo-design.md"},
    )


def test_a_document_record_is_staged_whole_and_copied_over_the_record_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write is a whole-record replacement, so what is staged is everything read.

    A field read and not staged is a field this write deletes, which is why the staged
    record is asserted in full rather than only for the entry being added.
    """
    staged: dict[str, str] = {}

    def read(arguments: Sequence[str]) -> dict[str, object]:
        given = list(arguments)
        root = next(
            value.split("=", 1)[1]
            for value in given
            if value.startswith("sources.") and "root" in value
        )
        staged["record"] = (
            Path(root) / "documents" / f"{plan_store.staged_name(_document().qualified_id)}.md"
        ).read_text(encoding="utf-8")
        staged["command"] = " ".join(given)
        return {"items": [{"source": "x", "action": "updated", "destination": "demo:demo-design"}]}

    monkeypatch.setattr(plan_store, "store_json", read)
    plan_store.write_document_metadata(_document(), "orchestrator.design-approval", {"key": "abc"})

    assert f"--match-by {plan_store.MATCH_BY}" in staged["command"]
    name = plan_store.staged_name(_document().qualified_id)
    assert f"{plan_store.STAGING_SOURCE}:{name} --to demo" in staged["command"]
    assert "demo-design" not in name, (
        "the staged record is named after the store's own answer, which is what reaches a "
        "path when that answer carries a separator or a traversal component"
    )
    record = staged["record"]
    assert 'title: "Design: demo"' in record
    assert 'project: "demo"' in record
    assert 'labels: ["design"]' in record
    assert 'repositories: ["github.com/nickderobertis/some-service"]' in record
    assert '"onetaskgraph.origin": "drafted:demo-design"' in record
    assert '"orchestrator.design-approval": {"key": "abc"}' in record
    assert "## What" in record


def test_a_record_written_onto_another_document_is_refused_rather_than_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The correspondence is matched by title, so the destination is checked.

    A second document of that title would be updated silently, and a record written over
    the wrong document reads as sound from every side afterwards.
    """
    monkeypatch.setattr(
        plan_store,
        "store_json",
        lambda _arguments: {"items": [{"destination": "demo:something-else"}]},
    )
    with pytest.raises(OSError, match="rather than onto 'demo:demo-design'"):
        plan_store.write_document_metadata(_document(), "orchestrator.design-approval", {})


def test_a_copy_that_reports_no_single_record_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the store wrote cannot be told from an answer naming none or several."""
    monkeypatch.setattr(plan_store, "store_json", lambda _arguments: {"items": []})
    with pytest.raises(OSError, match="reported 0 copied records"):
        plan_store.write_document_metadata(_document(), "orchestrator.design-approval", {})


def test_a_document_with_neither_project_nor_labels_stages_neither_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A field the store reports as absent is staged as absent rather than as empty."""
    staged: dict[str, str] = {}

    def read(arguments: Sequence[str]) -> dict[str, object]:
        root = next(
            value.split("=", 1)[1]
            for value in arguments
            if value.startswith("sources.") and "root" in value
        )
        staged["record"] = (
            Path(root) / "documents" / f"{plan_store.staged_name(_document().qualified_id)}.md"
        ).read_text(encoding="utf-8")
        return {"items": [{"destination": "demo:demo-design"}]}

    monkeypatch.setattr(plan_store, "store_json", read)
    plan_store.write_document_metadata(
        _document(project=None, labels=[], repositories=[]), "k", "v"
    )
    assert "project:" not in staged["record"]
    assert "labels:" not in staged["record"]
    assert "repositories:" not in staged["record"]


def test_a_projects_own_record_is_read_whole_rather_than_narrowed_to_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a project says about itself is not a plan field, so `read_plan` would drop it."""
    monkeypatch.setattr(
        plan_store,
        "store_json",
        lambda _arguments: {
            "items": [
                {"item": {"title": "demo", "metadata": {"orchestrator.plan-kind": "planning"}}}
            ]
        },
    )
    assert plan_store.project_record("demo:demo")["metadata"] == {
        "orchestrator.plan-kind": "planning"
    }


def test_the_engines_name_for_the_store_binary_is_kept_out_of_the_stores_own_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ONETASKGRAPH_BIN` is the engine's pointer and the store's unknown setting.

    Every `ONETASKGRAPH_*` name is read by that CLI's own configuration layer, so a
    process that inherits this one refuses `bin` as an unknown field and answers nothing
    at all — a listing and a `config show` alike. The engine strips it before it spawns;
    so does this, or a command run from inside a run that set it would report a plan
    store that is perfectly readable as unreadable.
    """
    monkeypatch.setenv(plan_store.BIN_ENV, "/test/stub-onetaskgraph")
    monkeypatch.setenv("ONETASKGRAPH_DEFAULT_SOURCES", "demo")
    environment = plan_store.store_environment()
    assert plan_store.BIN_ENV not in environment
    assert environment["ONETASKGRAPH_DEFAULT_SOURCES"] == "demo", (
        "stripping the engine's pointer took the store's own configuration with it"
    )


def test_two_documents_stage_under_two_names_and_one_document_always_under_its_own() -> None:
    """The name is per document and stable, and both halves are what the copy needs.

    Per document, because the write leaves its own origin on the destination: two
    documents staged under one name would carry one correspondence between them, and the
    second written would land on the first one's record. Stable, because a second write of
    the same document has to find the record the first one left.
    """
    first = plan_store.QualifiedDocumentId("demo:demo-design")
    second = plan_store.QualifiedDocumentId("demo:other-design")
    assert plan_store.staged_name(first) != plan_store.staged_name(second)
    assert plan_store.staged_name(first) == plan_store.staged_name(first)


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] This is a recorded
# observation of what a really rewritten record looks like, not a second statement of a
# contract: it was read off `onetaskgraph`'s own answer for a plan an onepipeline 0.19.0
# run had settled, and the release that produces it is one this host deliberately does
# not install. There is nothing installed to reconcile it against, and a producer that
# changed its spelling would leave the reader with the bare refusal it gave before.
#: How a task's own id is spelled inside the identity onepipeline 0.19.0's write-back
#: leaves behind: hex of the ASCII bytes, under a source that is a run's own scratch.
#: Written out rather than composed by the test, so the fixture below is the shape read
#: off a really corrupted record rather than the shape this module expects.
_REWRITTEN_EDGE = "onepipeline-writeback:6669727374"  # 66697273 74 -> "first"


def _rewritten_project(root: Path, native: str) -> None:
    """A local Markdown project whose one dependency edge names the write-back's scratch.

    The records are written by hand because no supported path produces them: they are
    what a settled plan looks like *after* an engine this host does not pin has rewritten
    it, and the point of the journey is that the reader recognises records nothing here
    would ever author.
    """
    (root / "projects").mkdir(parents=True, exist_ok=True)
    (root / "projects" / f"{native}.md").write_text(
        f'---\ntitle: "{native}"\nstatus: "todo"\nmetadata:\n'
        '  "onepipeline.schema_version": 3\n---\n\nA settled plan.\n',
        encoding="utf-8",
    )
    tasks = root / "tasks" / native
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / "first.md").write_text(
        f'---\ntitle: "first"\nstatus: "done"\nproject: "{native}"\nmetadata:\n'
        '  "onepipeline.id": "first"\n  "onepipeline.persona": "engineer"\n'
        "---\n\n## What\n\nFirst.\n",
        encoding="utf-8",
    )
    (tasks / "second.md").write_text(
        f'---\ntitle: "second"\nstatus: "done"\nproject: "{native}"\ndepends_on:\n'
        f"- id: {_REWRITTEN_EDGE}\n  kind: blocks\n  item: task\nmetadata:\n"
        '  "onepipeline.id": "second"\n  "onepipeline.persona": "engineer"\n'
        "---\n\n## What\n\nSecond.\n",
        encoding="utf-8",
    )


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def test_a_plan_the_write_back_rewrote_is_refused_by_naming_the_engine_that_rewrote_it() -> None:
    """A rewritten plan is refused by naming the engine that rewrote it.

    Driven through the real `onetaskgraph` rather than a doubled `store_json`, because
    what is in question is that the store reports these edges as dependency targets at
    all: a reader written against an assumed spelling would pass against a double and
    miss the real records.
    """
    root = plan_fixture_root.ROOT
    native = f"test-{os.getpid()}-writeback-rewrite"
    _rewritten_project(root, native)

    with pytest.raises(OSError) as refused:
        plan_store.read_project(f"{FIXTURE_SOURCE}:{native}")

    reported = str(refused.value)
    assert _REWRITTEN_EDGE in reported, reported
    assert "onepipeline 0.19.0" in reported, reported
    assert "https://github.com/nickderobertis/onepipeline/issues/189" in reported, reported
    assert "repaired in onepipeline 0.20.0" in reported, reported
    # The refusal says the records came from that one release rather than from this
    # host's pin, because the recogniser outlives the repair: a plan carried here from a
    # host that ran 0.19.0 still carries the rewrite, and a reader told to move a pin
    # that has already moved would be sent to fix the wrong thing.
    assert "nothing this host installs writes them" in reported, reported
