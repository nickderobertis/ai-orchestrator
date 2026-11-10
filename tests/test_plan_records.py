"""A generated plan becomes records through the pinned store, and is read back through it.

Every write below runs the locked `onetaskgraph`'s own `project create` and `task create`,
and every assertion about what was written is read back through the same store — never off
a file this repository rendered — because what decides a record's shape is the store.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import plan_records, plan_store
from orchestrator.record_shapes import CROSS_DAG_DEPS

#: The `local-md` source each round trip below reads through, named into the store's own
#: environment layer the way `tests/test_plan_store_pages.py` does.
SOURCE = "recordsource"

#: A wait-only reference onto another run's node, in the one spelling the documented
#: shape admits.
CROSS_DAG_REFERENCE = "run:r-upstream-0001#adopt"


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A local Markdown root the store reads as :data:`SOURCE`."""
    root = tmp_path / "source"
    monkeypatch.setenv(
        f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__PLUGIN", plan_store.WRITABLE_PLUGIN
    )
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__CONFIG__ROOT", str(root))
    return root


def _tasks(project: str) -> dict[str, plan_store.StoreTask]:
    return {task.node_id: task for task in plan_store.read_tasks(f"{SOURCE}:{project}")}


def test_every_supported_node_shape_reaches_the_store_and_reads_back(source: Path) -> None:
    plan = {
        "name": "My Plan",
        "schema_version": 3,
        "goal": {"text": "ship it"},
        "tasks": [
            {
                "id": "first",
                "title": "feat: first",
                "task": "Do first.\n",
                "repo": "https://github.com/acme/service.git",
                "persona": "engineer",
            },
            {
                "id": "second",
                "task": "Do second.",
                "repo": "/tmp/service",
                "deps": ["first", CROSS_DAG_REFERENCE],
                "unit": "orchestrator",
            },
        ],
    }

    native = plan_records.write_plan_project(source, plan, native_id="Stored Plan")

    assert native == "stored-plan"
    tasks = _tasks(native)
    first, second = tasks["first"], tasks["second"]
    assert first.title == "feat: first"
    assert (first.content or "").strip() == "Do first."
    assert first.repositories == ["github.com/acme/service"]
    assert first.metadata["onepipeline.persona"] == "engineer"
    assert second.metadata["onepipeline.repo"] == "/tmp/service"
    assert second.deps == ("first",)
    assert second.metadata[CROSS_DAG_DEPS] == [CROSS_DAG_REFERENCE]
    assert plan_store.authored_deps(second) == ["first", CROSS_DAG_REFERENCE]
    assert CROSS_DAG_DEPS not in first.metadata, "a node waiting on no other run carries none"
    loaded, _ = plan_store.read_project(f"{SOURCE}:{native}")
    assert loaded["goal"] == {"text": "ship it"}
    (read_second,) = [task for task in loaded["tasks"] if task["id"] == "second"]
    assert read_second["deps"] == ["first", CROSS_DAG_REFERENCE]


def test_a_delivered_ticket_is_the_records_own_field(source: Path) -> None:
    """The record's own `delivers`, never `onepipeline.delivers`, which the engine refuses."""
    ticket = f"{SOURCE}:ticket"
    plan_records.write_plan_project(source, {"name": "tickets", "tasks": [{"id": "ticket"}]})
    plan_records.write_plan_project(
        source,
        {"name": "delivering", "tasks": [{"id": "third", "task": "x", "delivers": [ticket]}]},
    )

    third = _tasks("delivering")["third"]
    assert third.delivers == (ticket,)
    assert "onepipeline.delivers" not in third.metadata


def test_a_dependency_cycle_is_refused_before_anything_is_written(source: Path) -> None:
    """The store records no cycle, so a plan holding one never reaches it."""
    with pytest.raises(ValueError, match="'c', 'd'|c, d.*cycle"):
        plan_records.write_plan_project(
            source,
            {"name": "cycle", "tasks": [{"id": "c", "deps": ["d"]}, {"id": "d", "deps": ["c"]}]},
        )
    assert not source.exists()


def test_writing_a_project_again_retires_every_task_it_held(source: Path) -> None:
    """A re-written project holds the new plan's nodes and none of the previous pass's."""
    plan_records.write_plan_project(
        source, {"name": "replacement", "tasks": [{"id": "kept"}, {"id": "removed"}]}
    )
    plan_records.write_plan_project(source, {"name": "replacement", "tasks": [{"id": "kept"}]})

    assert sorted(_tasks("replacement")) == ["kept"]


def test_a_write_retires_only_the_records_filed_under_its_own_project(source: Path) -> None:
    """Every record naming the project goes, however it spells that; nothing else is read.

    A record the store cannot parse, one with no front matter, one whose front matter
    never closes and one naming another project stand beside the project's own — a quoted
    spelling of its name and one in a nested directory among them — and a write retires the
    project's and leaves every other one byte for byte, where a store listing would have
    refused the whole root over the unparseable one.
    """
    plan_records.write_plan_project(source, {"name": "kept", "tasks": [{"id": "kept"}]})
    tasks = source / "tasks"
    strangers = {
        tasks / "broken.md": "---\ntitle: [unclosed\nproject: mine\n---\nBody.\n",
        tasks / "bare.md": "No front matter, project: mine\n",
        tasks / "open.md": "---\nproject: mine\n",
        tasks / "listed.md": "---\n- project\n---\n",
    }
    for path, text in strangers.items():
        path.write_text(text, encoding="utf-8")
    nested = tasks / "nested" / "own.md"
    nested.parent.mkdir()
    nested.write_text("---\ntitle: own\nproject: 'mine'\n---\nOld.\n", encoding="utf-8")
    kept = {path: path.read_bytes() for path in tasks.rglob("*.md") if path != nested}

    plan_records.write_plan_project(source, {"name": "mine", "tasks": [{"id": "fresh"}]})

    assert not nested.exists(), "a record filed under the project was left beside its new tasks"
    assert all(path.read_bytes() == held for path, held in kept.items())
    assert (tasks / "fresh.md").is_file()


#: A node field the store refuses when it is handed as metadata: `--metadata` splits at the
#: first `=`, so the value it reads is not JSON. It stops a write at that node's create, after
#: every task ahead of it in dependency order was created.
REFUSED_FIELD = {"refused=field": 1}


def test_a_write_that_fails_part_way_takes_back_the_tasks_it_created(source: Path) -> None:
    """A failed write leaves nothing of its project behind, and the next write succeeds."""
    refused = {
        "name": "partial",
        "tasks": [{"id": "first"}, {"id": "second", "deps": ["first"]} | REFUSED_FIELD],
    }

    with pytest.raises(OSError, match="refused"):
        plan_records.write_plan_project(source, refused)
    assert not list((source / "tasks").glob("*.md")), list((source / "tasks").iterdir())
    assert not (source / "projects" / "partial.md").exists()

    plan = {"name": "partial", "tasks": [{"id": "first"}, {"id": "second", "deps": ["first"]}]}
    plan_records.write_plan_project(source, plan)
    assert sorted(_tasks("partial")) == ["first", "second"]


def test_a_write_whose_project_is_refused_takes_back_every_task_it_created(
    source: Path,
) -> None:
    """The project is created last: refused there, it leaves none of the tasks before it."""
    plan = {"name": "unfiled", "tasks": [{"id": "first"}, {"id": "second", "deps": ["first"]}]}

    with pytest.raises(OSError, match="refused"):
        plan_records.write_plan_project(source, plan, project_metadata=REFUSED_FIELD)
    assert not list((source / "tasks").glob("*.md")), list((source / "tasks").iterdir())
    assert not (source / "projects" / "unfiled.md").exists()

    plan_records.write_plan_project(source, plan)
    assert sorted(_tasks("unfiled")) == ["first", "second"]


def test_a_replacement_that_fails_part_way_leaves_no_task_of_either_pass(source: Path) -> None:
    """A failed replacement leaves nothing of the project, and a retry restores it whole."""
    first = {"name": "replaced", "tasks": [{"id": "first"}]}
    plan_records.write_plan_project(source, first)
    second = {"name": "replaced", "tasks": [{"id": "first"}, {"id": "second", "deps": ["first"]}]}

    with pytest.raises(OSError, match="refused"):
        plan_records.write_plan_project(
            source, {**second, "tasks": [{"id": "first"}, {"id": "second"} | REFUSED_FIELD]}
        )
    assert not (source / "projects" / "replaced.md").exists(), "it kept its previous record"
    assert not list((source / "tasks").glob("*.md")), "it kept tasks of one pass or the other"

    plan_records.write_plan_project(source, second)
    assert sorted(_tasks("replaced")) == ["first", "second"]


def test_a_project_written_again_keeps_nothing_its_previous_record_carried(
    source: Path,
) -> None:
    """A replacement is the plan it states: no key a previous pass or its review left stays."""
    plan_records.write_plan_project(
        source, {"name": "again", "tasks": []}, project_metadata={"orchestrator.left": 1}
    )
    plan_records.write_plan_project(source, {"name": "again", "tasks": []})

    metadata = plan_store.project_record(f"{SOURCE}:again").get("metadata") or {}
    assert isinstance(metadata, dict)
    assert "orchestrator.left" not in metadata, metadata


@pytest.mark.parametrize(
    "items",
    ([], [f"{SOURCE}:a", f"{SOURCE}:b"], ["elsewhere:a"]),
    ids=["none", "two", "another-source"],
)
def test_a_create_answer_naming_other_than_one_task_of_its_source_is_refused(
    items: list[str],
) -> None:
    """The id a dependency is built on is the one task the create made, or nothing is."""
    answer = SimpleNamespace(
        items=[SimpleNamespace(id=SimpleNamespace(root=item)) for item in items]
    )
    with pytest.raises(OSError, match="rather than the one task it created"):
        plan_records._created(answer, "title")


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
        {"name": "plan", "tasks": [{"id": "same"}, {"id": "same"}]},
        # A unit is held to its one rule where it is written, never stored broken.
        {"name": "plan", "tasks": [{"id": "node", "unit": ""}]},
        {"name": "plan", "tasks": [{"id": "node", "unit": 7}]},
        {"name": "plan", "tasks": [{"id": "node", "unit": None}]},
        {"name": "!!!", "tasks": []},
        # A node id is the engine's name for the node, never stored blank or unprintable.
        {"name": "plan", "tasks": [{"id": ""}]},
        {"name": "plan", "tasks": [{"id": "   "}]},
        {"name": "plan", "tasks": [{"id": " node"}]},
        {"name": "plan", "tasks": [{"id": "no\nde"}]},
        {"name": "plan", "tasks": [{"id": "no\x07de"}]},
    ),
)
def test_an_invalid_generated_plan_is_refused_and_nothing_is_written(
    source: Path, plan: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        plan_records.write_plan_project(source, plan)
    assert not source.exists() or not any(source.rglob("*.md"))


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
    source: Path, entry: str
) -> None:
    with pytest.raises(ValueError, match=r"'node'.*neither a task.*cross-DAG reference") as refused:
        plan_records.write_plan_project(
            source,
            {
                "name": "plan",
                "tasks": [{"id": "node", "deps": ["node-2", entry]}, {"id": "node-2"}],
            },
        )
    assert str(refused.value).endswith(f": {entry}"), refused.value


def test_project_metadata_may_not_overwrite_a_field_of_the_plan_itself(source: Path) -> None:
    with pytest.raises(ValueError, match="may not name onepipeline"):
        plan_records.write_plan_project(
            source,
            {"name": "marked", "tasks": []},
            project_metadata={"onepipeline.concurrency": 99},
        )


def test_main_creates_a_project_carrying_its_own_metadata_and_reports_usage(
    source: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """How the planning recipes mark the project a planning launch writes.

    The marker is written unprefixed beside the plan's own `onepipeline.` entries, because
    it is a fact about the project rather than a field the engine's loader would then meet.
    """
    monkeypatch.setattr(sys, "argv", ["plan-records"])
    assert plan_records.main() == 2
    assert "usage:" in capsys.readouterr().err

    monkeypatch.setattr(sys, "argv", ["plan-records", str(source), '{"a.kind": "planning"}'])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"name":"marked","tasks":[]}'))
    assert plan_records.main() == 0
    assert capsys.readouterr().out.strip() == "marked"
    metadata = plan_store.project_record(f"{SOURCE}:marked")["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["a.kind"] == "planning"
    assert "onepipeline.a.kind" not in metadata


@pytest.mark.parametrize(
    ("argv_tail", "stdin", "said"),
    (
        ([], "{bad", "input is not valid JSON"),
        ([], json.dumps(["not an object"]), "generated plan is not an object"),
        (['["not an object"]'], '{"name":"m","tasks":[]}', "project metadata is not an object"),
        ([], '{"name":"m","tasks":[{"id":"n","deps":["x"]}]}', "could not create"),
    ),
)
def test_main_refuses_what_it_cannot_create(
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv_tail: list[str],
    stdin: str,
    said: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["plan-records", str(source), *argv_tail])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    assert plan_records.main() == 2
    assert said in capsys.readouterr().err
