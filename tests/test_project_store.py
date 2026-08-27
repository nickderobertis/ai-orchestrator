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
