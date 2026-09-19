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


def test_write_plan_project_removes_a_stale_task(tmp_path: Path) -> None:
    project_store.write_plan_project(
        tmp_path,
        {"name": "replacement", "tasks": [{"id": "kept"}, {"id": "removed"}]},
    )

    project_store.write_plan_project(tmp_path, {"name": "replacement", "tasks": [{"id": "kept"}]})

    assert (tmp_path / "tasks/replacement/kept.md").is_file()
    assert not (tmp_path / "tasks/replacement/removed.md").exists()


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
