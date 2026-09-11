"""Unit coverage for the generated-plan to local-md adapter."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from orchestrator import project_store


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
            {"id": "third", "task": "Do third."},
        ],
    }

    native = project_store.write_plan_project(tmp_path, plan, native_id="Stored Plan")

    assert native == "stored-plan"
    first = (tmp_path / "tasks/stored-plan/first.md").read_text()
    second = (tmp_path / "tasks/stored-plan/second.md").read_text()
    assert 'repositories: ["github.com/acme/service"]' in first
    assert '"onepipeline.repo": "/tmp/service"' in second
    assert 'depends_on: ["stored-plan/first"]' in second


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
        {"name": "plan", "tasks": [{"id": "node", "deps": ["absent"]}]},
        {"name": "plan", "tasks": [{"id": "same id"}, {"id": "same-id"}]},
    ),
)
def test_render_plan_project_rejects_invalid_generated_fields(plan: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        project_store.render_plan_project(plan)


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
