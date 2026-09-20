"""Unit coverage for the generated-plan to local-md adapter."""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

from orchestrator import plan_store, project_store
from orchestrator.root import REPO_ROOT

#: The `local-md` source each round trip below reads through the installed store, named
#: into the store's own environment layer the way `tests/test_plan_store_pages.py` does.
SOURCE = "crossdagsource"

#: A wait-only reference onto another run's node, in the one spelling the documented
#: shape admits.
CROSS_DAG_REFERENCE = "run:r-upstream-0001#adopt"


def test_render_plan_project_covers_supported_node_shapes(tmp_path: Path) -> None:
    plan = {
        "name": "My Plan",
        "schema_version": 3,
        "tasks": [
            {
                "id": "first",
                "title": "feat: first",
                "task": "Do first.\n",
                "repo": "https://github.com/acme/service.git",
            },
            {
                "id": "second",
                "task": "Do second.",
                "repo": "/tmp/service",
                "deps": ["first"],
            },
            {"id": "third", "task": "Do third.", "delivers": ["followups:I_4"]},
        ],
    }

    native = project_store.write_plan_project(tmp_path, plan, native_id="Stored Plan")

    assert native == "stored-plan"
    first = (tmp_path / "tasks/stored-plan/first.md").read_text()
    second = (tmp_path / "tasks/stored-plan/second.md").read_text()
    third = (tmp_path / "tasks/stored-plan/third.md").read_text()
    assert 'repositories: ["github.com/acme/service"]' in first
    assert '"onepipeline.repo": "/tmp/service"' in second
    assert 'depends_on: ["stored-plan/first"]' in second
    # The record's own field and never `onepipeline.delivers`: the engine refuses the
    # namespaced key by name, and it is the store that moves what a node delivers.
    assert 'delivers: ["followups:I_4"]' in third
    assert "onepipeline.delivers" not in third
    assert "delivers" not in first, "a node delivering nothing carries no empty list"


def test_a_rendered_plan_lists_its_project_document_last() -> None:
    """A concurrent reader sees either no project or a whole one, never half of one.

    `write_plan_project` writes in the mapping's order, and a local Markdown source
    that finds a project document opens the task directory below it — so a document
    published before its tasks refuses the reader's whole walk rather than answering
    with an empty project. That ordering is the record's own, which is why it is
    stated by what `render_plan_project` returns rather than by how it is written.
    """
    rendered = list(
        project_store.render_plan_project(
            {"name": "plan", "tasks": [{"id": "first"}, {"id": "second"}]}
        )
    )

    assert rendered[-1] == Path("projects/plan.md"), rendered
    assert all(path.parent == Path("tasks/plan") for path in rendered[:-1]), rendered


@pytest.mark.parametrize(
    "plan",
    (
        {},
        {"name": "plan", "tasks": [{}]},
        {"name": "plan", "tasks": [{"id": "node", "task": None}]},
        {"name": "plan", "tasks": [{"id": "node", "repo": 7}]},
        {"name": "plan", "tasks": [{"id": "node", "deps": [7]}]},
        {"name": "plan", "tasks": [{"id": "node", "delivers": "followups:I_4"}]},
        {"name": "plan", "tasks": [{"id": "node", "delivers": [7]}]},
        # A `delivers` entry names a task in another source, so an unqualified or empty one
        # is refused where it is written rather than by whichever reader cannot resolve it.
        {"name": "plan", "tasks": [{"id": "node", "delivers": ["I_4"]}]},
        {"name": "plan", "tasks": [{"id": "node", "delivers": [""]}]},
        {"name": "plan", "tasks": [{"id": "node", "delivers": ["followups:"]}]},
        {"name": "plan", "tasks": [{"id": "node", "deps": ["absent"]}]},
        {"name": "plan", "tasks": [{"id": "same id"}, {"id": "same-id"}]},
    ),
)
def test_render_plan_project_rejects_invalid_generated_fields(plan: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        project_store.render_plan_project(plan)


@pytest.mark.parametrize(
    ("value", "parts"),
    (
        ("followups:I_created_0", ("followups", "I_created_0")),
        ("drafts:a-run/tickets/a-cause", ("drafts", "a-run/tickets/a-cause")),
        # The native half is the source's own answer about its own records, so a colon in
        # it is the source's business: only the first one separates.
        ("followups:a:b", ("followups", "a:b")),
        ("I_created_0", None),
        ("", None),
        (":native", None),
        ("followups:", None),
    ),
)
def test_a_qualified_id_is_a_source_and_a_native_half(
    value: str, parts: tuple[str, str] | None
) -> None:
    """The one parser for the shape, which both `delivers` writers and `qualified` ask."""
    assert project_store.qualified_id(value) == parts


def test_a_cross_dag_reference_is_stored_beside_the_plans_own_edge_and_read_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One node waiting on a task of this plan and on another run's node, round-tripped.

    The two halves of `deps` are written to two places — the in-plan id as the record's
    own `depends_on`, the cross-DAG reference as its `onepipeline.deps` metadata, which
    is where the engine's loader reads one from — and both are read back through the
    installed plan store rather than off the file, because a record the renderer wrote
    and the store then refused or misread would have proven nothing. Before this the
    reference was refused as an unknown dependency, and a plan using the documented
    shape reached the store only by editing the record by hand after every render
    (https://github.com/nickderobertis/ai-orchestrator/issues/1139).
    """
    root = tmp_path / "source"
    project_store.write_plan_project(
        root,
        {
            "name": "demo",
            "tasks": [
                {"id": "first", "title": "feat: first", "task": "Do first."},
                {
                    "id": "second",
                    "title": "feat: second",
                    "task": "Do second.",
                    "deps": ["first", CROSS_DAG_REFERENCE],
                },
            ],
        },
    )
    monkeypatch.setenv(
        f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__PLUGIN", plan_store.WRITABLE_PLUGIN
    )
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__CONFIG__ROOT", str(root))

    records = {record.node_id: record for record in plan_store.read_tasks(f"{SOURCE}:demo")}

    second = records["second"]
    assert second.deps == ("first",)
    assert second.metadata[project_store.CROSS_DAG_DEPS] == [CROSS_DAG_REFERENCE]
    assert plan_store.authored_deps(second) == ["first", CROSS_DAG_REFERENCE]
    assert project_store.CROSS_DAG_DEPS not in records["first"].metadata, (
        "a node waiting on no other run carries no empty list"
    )
    plan, _ = plan_store.read_project(f"{SOURCE}:demo")
    (loaded,) = [task for task in plan["tasks"] if task["id"] == "second"]
    assert loaded["deps"] == ["first", CROSS_DAG_REFERENCE]


@pytest.mark.parametrize(
    "entry",
    (
        "absent",
        "run:#adopt",
        "run:r-upstream-0001#",
        "run:r-upstream 0001#adopt",
        "run:r-upstream-0001#adopt now",
        "run:r-upstream-0001#adopt#again",
        "run:r-upstream-0001",
        "run:",
    ),
)
def test_a_dependency_that_is_neither_a_task_nor_a_well_formed_reference_is_refused(
    entry: str,
) -> None:
    """Refused naming the node and the entry, and never written.

    A malformed reference — an empty run or node half, whitespace, a second `#`, no
    `#` at all — is refused exactly as an id no task of the plan carries is, rather
    than stored for whichever loader meets it first to refuse.
    """
    with pytest.raises(ValueError, match=r"'node'.*neither a task.*cross-DAG reference") as refused:
        project_store.render_plan_project(
            {"name": "plan", "tasks": [{"id": "node", "deps": ["node-2", entry]}, {"id": "node-2"}]}
        )
    assert str(refused.value).endswith(f": {entry}"), refused.value


@pytest.mark.reads_docs
def test_the_documented_reference_shape_is_the_one_the_renderer_reads() -> None:
    """`docs/orchestration.md`'s node-shapes section and the renderer state one shape.

    The section is the shape's one source and the renderer is the one place this
    repository parses it, so the spelling is read back out of the prose rather than
    restated here, and the reader is shown accepting exactly the form the prose states —
    both placeholders filled — and refusing the same form with either half emptied.
    """
    text = (REPO_ROOT / "docs" / "orchestration.md").read_text(encoding="utf-8")
    start = text.index("\n## Node shapes\n")
    section = text[start : text.index("\n## ", start + 1)]
    documented = re.search(
        r"cross-DAG references of the form\s+`([^`]+)`", " ".join(section.split())
    )
    assert documented is not None, "the node-shapes section no longer states the reference shape"
    assert documented.group(1) == project_store.CROSS_DAG_REFERENCE_SHAPE

    placeholders = re.findall(r"<([^<>]+)>", project_store.CROSS_DAG_REFERENCE_SHAPE)
    assert placeholders == ["run_id", "node_id"], placeholders
    filled = project_store.CROSS_DAG_REFERENCE_SHAPE
    for placeholder, value in zip(placeholders, ("r-upstream-0001", "adopt"), strict=True):
        filled = filled.replace(f"<{placeholder}>", value)
    assert project_store.cross_dag_reference(filled) == filled
    for placeholder in placeholders:
        emptied = project_store.CROSS_DAG_REFERENCE_SHAPE.replace(f"<{placeholder}>", "")
        for other in placeholders:
            emptied = emptied.replace(f"<{other}>", "x")
        assert project_store.cross_dag_reference(emptied) is None, emptied


def test_slug_rejects_a_value_with_no_identifier() -> None:
    with pytest.raises(ValueError, match="project id"):
        project_store.render_plan_project({"name": "!!!", "tasks": []})


def test_a_record_that_cannot_be_renamed_into_place_leaves_no_stage_behind(
    tmp_path: Path,
) -> None:
    """A write that fails at the rename reports it and leaves the root as it found it.

    The destination is occupied by a directory, which a rename cannot replace, so the
    staged record can never become the record. What the caller sees is the operating
    system's own refusal, and what the root keeps is nothing: a stage left beside its
    destination would be a hidden file the next sweep has to know about.
    """
    destination = tmp_path / "tasks" / "plan" / "node.md"
    destination.mkdir(parents=True)

    with pytest.raises(OSError, match="Is a directory"):
        project_store.publish_record(destination, "---\ntitle: T\n---\n")

    assert destination.is_dir()
    assert [path.name for path in destination.parent.iterdir()] == ["node.md"]


def test_a_record_that_cannot_be_written_to_its_stage_leaves_no_stage_behind(
    tmp_path: Path,
) -> None:
    """A write that fails while filling the stage reports it and removes the stage.

    JSON admits a lone surrogate, so a generated plan can carry task text UTF-8 cannot
    encode: the stage is created by the open and the write into it then fails, which is
    the one moment a stage exists that will never become a record. What the root keeps
    of it is nothing, and no record was replaced, because nothing was renamed.
    """
    plan = {"name": "unencodable", "tasks": [{"id": "node", "task": "lone \ud800 surrogate"}]}

    with pytest.raises(UnicodeEncodeError):
        project_store.write_plan_project(tmp_path, json.loads(json.dumps(plan)))

    assert not (tmp_path / "tasks/unencodable/node.md").exists()
    assert not (tmp_path / "projects/unencodable.md").exists()
    assert not [path for path in tmp_path.rglob("*") if path.name.startswith(".")], (
        "a stage whose write failed is removed"
    )


def test_write_plan_project_removes_a_stale_task_and_never_a_stage(tmp_path: Path) -> None:
    """A replacement drops the records it no longer renders, and nothing else.

    A hidden file in the task directory is a stage on its way to becoming a record —
    a peer writer's, or the store's own `.<name>.<pid>-<n>.onetaskgraph-staging` — and
    unlinking it fails that writer's rename, so the sweep leaves it where it is. And a
    reader listing that directory never opens it as a record: what it reads back is the
    project document and the task the replacement rendered, and nothing else.
    """
    project_store.write_plan_project(
        tmp_path,
        {"name": "replacement", "tasks": [{"id": "kept"}, {"id": "removed"}]},
    )
    stage = tmp_path / "tasks/replacement/.kept.md.4242-0.onetaskgraph-staging"
    stage.write_text("---\ntitle: kept\n---\n", encoding="utf-8")

    project_store.write_plan_project(tmp_path, {"name": "replacement", "tasks": [{"id": "kept"}]})

    assert (tmp_path / "tasks/replacement/kept.md").is_file()
    assert not (tmp_path / "tasks/replacement/removed.md").exists()
    assert stage.is_file(), "a peer's stage is not a stale record"
    assert project_store.read_records(tmp_path) == {
        str(relative): content
        for relative, content in project_store.render_plan_project(
            {"name": "replacement", "tasks": [{"id": "kept"}]}
        ).items()
    }, "a stage is listed by no reader, and a record is read back exactly as it was rendered"


def test_read_records_of_a_root_nothing_was_written_to_is_empty(tmp_path: Path) -> None:
    """A root with no records answers none, whether its directories exist or not.

    Both shapes are real: a fresh root has neither directory, and a root whose only
    project was removed keeps an empty `tasks/` beside a `projects/` holding a stage
    that never became a record.
    """
    assert project_store.read_records(tmp_path) == {}

    (tmp_path / "tasks" / "removed").mkdir(parents=True)
    (tmp_path / "tasks" / "notes.txt").write_text("not a project directory", encoding="utf-8")
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / ".removed.md.7-1.ai-orchestrator-staging").write_text(
        "---\n", encoding="utf-8"
    )

    assert project_store.read_records(tmp_path) == {}


def test_main_writes_a_project_and_reports_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["project-store"])
    assert project_store.main() == 2
    assert "usage:" in capsys.readouterr().err

    monkeypatch.setattr(sys, "argv", ["project-store", str(tmp_path)])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(["not an object"])))
    assert project_store.main() == 2

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"name": "written", "tasks": []})),
    )
    assert project_store.main() == 0
    assert (tmp_path / "projects/written.md").is_file()


def test_main_reports_malformed_input_and_write_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["project-store", str(tmp_path)])
    monkeypatch.setattr(sys, "stdin", io.StringIO("{bad"))
    assert project_store.main() == 2
    assert "input is not valid JSON" in capsys.readouterr().err

    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["project-store", str(blocked)])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"name":"p","tasks":[]}'))
    assert project_store.main() == 2
    assert "cannot write generated plan" in capsys.readouterr().err


def test_a_projects_own_metadata_is_written_beside_the_plans_and_never_read_back_as_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """How `scripts/plan.sh` marks the project a planning launch writes.

    Written unprefixed and beside the plan's own `onepipeline.` entries, because it is a
    fact about the project rather than a field the engine's loader would then meet: a
    marker folded into the plan document would come back out of the store as a plan field
    nothing declared, and the loader refuses one of those by name.
    """
    monkeypatch.setattr(sys, "argv", ["project-store", str(tmp_path), '{"a.kind": "planning"}'])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"name":"marked","tasks":[]}'))
    assert project_store.main() == 0
    record = (tmp_path / "projects/marked.md").read_text(encoding="utf-8")
    assert '"a.kind": "planning"' in record
    assert '"onepipeline.a.kind"' not in record

    monkeypatch.setattr(sys, "argv", ["project-store", str(tmp_path), '["not an object"]'])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"name":"marked","tasks":[]}'))
    assert project_store.main() == 2
    assert "project metadata is not an object" in capsys.readouterr().err


def test_project_metadata_may_not_overwrite_a_field_of_the_plan_itself() -> None:
    """The two maps are merged and the caller's would win, so the plan's half is reserved.

    A project that stated an `onepipeline.` field no plan declared would be read back as a
    plan carrying it, and the engine's loader would meet a field nobody wrote.
    """
    with pytest.raises(ValueError, match="may not name onepipeline"):
        project_store.render_plan_project(
            {"name": "marked", "tasks": []},
            project_metadata={"onepipeline.concurrency": 99},
        )


@pytest.mark.parametrize(
    ("repo", "origin"),
    (
        ("github.com/acme/service", "github.com/acme/service"),
        ("https://github.com/acme/service.git", "github.com/acme/service"),
        ("http://git.example.org/acme/service", "git.example.org/acme/service"),
        ("acme__service", None),
        ("/srv/checkouts/service", None),
        ("git@github.com:acme/service.git", None),
        ("github.com/acme", None),
        ("github.com/acme/service?ref=main", None),
        ("github.com/ac me/service", None),
        ("github.com/acme/service\x1b[0m", None),
    ),
)
def test_hosted_origin_is_the_one_shape_the_repositories_field_holds(
    repo: str, origin: str | None
) -> None:
    """A normalized `host/owner/name`, with the clone URL's scheme and suffix dropped.

    Everything else — an alias, a path, an scp-like URL — is answered `None`, because
    none of them is a value the record's `repositories` list holds: those travel on the
    reserved `onepipeline.repo` key, and the one reader deciding that is this function.
    """
    assert project_store.hosted_origin(repo) == origin
