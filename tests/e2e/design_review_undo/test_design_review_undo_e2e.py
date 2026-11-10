"""The design review's undo over a real local plan store.

The `orchestrator-design-review undo` command `.onepipeline/config.yaml` declares, run as the
engine runs it, each case seeded in the store's own files so a launch signal or a refusal is
decided by what the store and the runs root really answer. The journey through the installed
engine is `tests/e2e/board_copy_approval/test_design_review_items_e2e.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import boundary_home
import pytest

from orchestrator import design_approval, plan_store
from orchestrator.plan_records import write_plan_project
from orchestrator.record_shapes import PlanNode
from orchestrator.root import REPO_ROOT


def _source(monkeypatch: pytest.MonkeyPatch, name: str, root: Path) -> None:
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__PLUGIN", "local-md")
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{name.upper()}__CONFIG__ROOT", str(root))


def _no_repositories_registered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point this process's store at an `onevcs` root holding no repository."""
    home = boundary_home.registered_home(tmp_path / "boundary", public=[], private=())
    for name, value in boundary_home.environment(None, home).items():
        if os.environ.get(name) != value:
            monkeypatch.setenv(name, value)


#: The answers the plan's design document is rendered from: one unit, and the plan's one task.
DESIGN = {
    "what": "One node that changes nothing.",
    "why": "Something has to be approved for an undo to remove it.",
    "architecture": "One node.",
    "units": [
        {
            "name": "First",
            "repository": "ai-orchestrator",
            "part": "",
            "summary": "Nothing outside this plan reads it.",
            "reversible": [],
            "decisions": [],
        }
    ],
    "acceptance_criteria": ["The approval is recorded."],
    "planned_tasks": [
        {
            "task": "first",
            "unit": "First",
            "delivers": "nothing",
            "depends_on": "none",
            "location": "the store's own location",
        }
    ],
}


class ApprovedPlan(NamedTuple):
    """A plan whose design approval is recorded, and the runs root the undo reads."""

    project: str
    key: design_approval.ApprovalKey
    runs: Path


def _approved_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApprovedPlan:
    """A plan in a real local store, its design document approved, under an empty runs root."""
    source = tmp_path / "source"
    write_plan_project(
        source,
        {"name": "demo", "tasks": [PlanNode(id="first", title="First", task="Do first.")]},
    )
    _source(monkeypatch, "sdksource", source)
    _no_repositories_registered(monkeypatch, tmp_path)
    # The design document rendered as a writer renders one: the pinned engine's resolve piped
    # into the store's own `document create`, so its provenance is the store's and the
    # engine's template check holds it to being that rendering.
    answers = tmp_path / "design.answers.json"
    answers.write_text(json.dumps(DESIGN), encoding="utf-8")
    resolved = subprocess.run(
        [str(REPO_ROOT / "node_modules" / ".bin" / "onepipeline"), "template", "resolve"]
        + [design_approval.TEMPLATE_NAME, "--json"]
        + ["--template-root", str(design_approval.TEMPLATE_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    created = subprocess.run(
        [str(plan_store.locked_binary()), "document", "create", "sdksource", "--project", "demo"]
        + ["--title", "Design", "--id", "design", "--template-loader", "-"]
        + ["--answers", str(answers), "--no-interactive"],
        cwd=REPO_ROOT,
        input=resolved.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv(design_approval.RUNS_ROOT_ENV, str(runs))
    return ApprovedPlan("sdksource:demo", design_approval.approve("sdksource:demo").key, runs)


def _undo(project: str, key: str) -> subprocess.CompletedProcess[str]:
    """The undo command, from this checkout, with the adapter's own request on its stdin.

    Under `coverage run` because a child process is otherwise invisible to this project's
    measurement, which feeds the `orchestrator/` floor; the module's `__main__` is the same
    `main` the `orchestrator-design-review` script runs, and `--parallel-mode` writes data
    the pytest-cov run of this project combines into its own file.
    """
    return subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "coverage",
            "run",
            "--parallel-mode",
            "-m",
            "orchestrator.design_review",
            "undo",
        ],
        cwd=REPO_ROOT,
        input=json.dumps({"board_copy": project, "key": key}),
        capture_output=True,
        text=True,
        check=False,
    )


def _refused(project: str, key: str, why: str) -> None:
    """The undo exits non-zero, says why on standard error, and leaves the approval recorded."""
    done = _undo(project, key)
    assert done.returncode == 1, done.stdout + done.stderr
    assert why in done.stderr
    assert _record(project) == key


def _record(project: str) -> str | None:
    return design_approval.recorded(design_approval.design_document(project))


def test_an_undo_removes_only_the_version_it_names_and_repeating_it_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, key, runs = _approved_plan(tmp_path, monkeypatch)
    # What else a runs root holds: a store directory with no launch record, a stray file, and
    # a run of another plan.
    # llmlint: ignore-block[tests_mirror_real_usage] A run of another plan, in the shape the engine
    # writes it, is what the scan must pass over; a real launch here would be a second journey.
    (runs / "human-items").mkdir()
    (runs / "stray.txt").write_text("", encoding="utf-8")
    (runs / "other").mkdir()
    (runs / "other" / "launch.json").write_text('{"project": "plans:other"}', encoding="utf-8")
    # llmlint: ignore-end[tests_mirror_real_usage]

    other = _undo(project, "another-version")
    assert other.returncode == 0, other.stderr
    assert _record(project) == key
    assert "removed" not in other.stderr

    undone = _undo(project, key)
    assert undone.returncode == 0, undone.stderr
    assert json.loads(undone.stdout) == {"board_copy": project, "key": key}
    assert f"removed {project}'s design approval" in undone.stderr
    assert _record(project) is None

    repeated = _undo(project, key)
    assert repeated.returncode == 0, repeated.stderr
    assert "removed" not in repeated.stderr
    assert _record(project) is None
    assert design_approval.approve(project).key == key
    assert _record(project) == key


@pytest.mark.parametrize(
    ("record", "refusal"),
    [
        ('{"project": "sdksource:demo"}', "has already launched"),
        ("{not json", "could not be read"),
        ("[]", "names no project"),
    ],
    ids=["names-the-plan", "unreadable", "names-nothing"],
)
def test_a_launch_record_decides_or_refuses_the_undo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: str,
    refusal: str,
) -> None:
    project, key, runs = _approved_plan(tmp_path, monkeypatch)
    # llmlint: ignore-block[tests_mirror_real_usage] No interface writes an unreadable record,
    # and the refusal for one is the case under test.
    (runs / "a-run").mkdir()
    (runs / "a-run" / design_approval.LAUNCH_RECORD).write_text(record, encoding="utf-8")
    # llmlint: ignore-end[tests_mirror_real_usage]

    _refused(project, key, refusal)


def test_a_runs_root_that_cannot_be_listed_refuses_the_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, key, runs = _approved_plan(tmp_path, monkeypatch)
    monkeypatch.setenv(design_approval.RUNS_ROOT_ENV, str(runs / "absent"))

    _refused(project, key, "could not be listed")


def test_a_driver_claim_or_a_claimed_task_refuses_the_undo_and_a_released_claim_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, key, _ = _approved_plan(tmp_path, monkeypatch)
    client = plan_store.client()
    claim = design_approval.DRIVER_CLAIM
    # llmlint: ignore-block[tests_mirror_real_usage] The criteria need the driver claim as the only
    # launch signal; a real launch writes every signal at once, and the store verb here is the
    # one the engine writes it through.
    plan_store.sdk(client.project_metadata_set(project, claim, '{"run": "a-run"}'))
    # llmlint: ignore-end[tests_mirror_real_usage]
    _refused(project, key, "carries a driver claim")
    plan_store.sdk(client.project_metadata_set(project, claim, "null"))
    assert design_approval.launch_evidence(project) is None

    plan_store.sdk(client.task_status_set("sdksource:first", "queued"))
    _refused(project, key, "is at queued, past todo")


def test_a_status_the_store_cannot_place_refuses_the_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, key, _ = _approved_plan(tmp_path, monkeypatch)
    plan_store.sdk(plan_store.client().task_status_set("sdksource:first", "unknown"))

    _refused(project, key, "cannot place the status")
