"""Drive ``measure.py`` as a command over a fixture checkout: real plan store, real engine.

Run from the repository root:

    .venv/bin/pytest spikes/run-history-grouping/test_measure.py

Each test lays out a checkout of its own — an ``onetaskgraph.yaml`` naming the two local
sources, their Markdown projects and tasks, and a runs root of ``launch.json`` records — and
runs the harness against it with the locked ``onetaskgraph`` and ``onepipeline`` binaries,
exactly as it runs against this host.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HARNESS = Path(__file__).with_name("measure.py")

STORE_CONFIG = """\
default_sources: [authoring]
sources:
  authoring:
    plugin: local-md
    config:
      root: .plans
  follow-up-runs:
    plugin: local-md
    config:
      root: .follow-ups/runs
"""


def project(checkout: Path, name: str, metadata: dict[str, Any] | None = None) -> None:
    lines = ["---", f'title: "{name}"', "status: todo", "metadata:"]
    for key, value in (metadata or {"onepipeline.schema_version": 3}).items():
        lines.append(f'  "{key}": {json.dumps(value)}')
    path = checkout / ".plans" / "projects" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([*lines, "---", "", f"Execution plan {name}.", ""]))


def task(checkout: Path, proj: str, task_id: str, body: str) -> None:
    path = checkout / ".plans" / "tasks" / proj / f"{task_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\ntitle: "{task_id}"\nproject: "{proj}"\nstatus: done\n---\n\n{body}\n')


def run(checkout: Path, run_id: str, launch_project: str) -> None:
    path = checkout / "runs" / run_id / "launch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "project": launch_project,
                "launcher": "claude-code",
                "session": "fixture-session",
            }
        )
    )


def checkout_with_a_plan(tmp_path: Path) -> Path:
    """The plan ``authoring:widget`` and the brief project that plans it."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "onetaskgraph.yaml").write_text(STORE_CONFIG)
    (checkout / ".follow-ups" / "runs").mkdir(parents=True)
    project(checkout, "widget")
    return checkout


def measure(checkout: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS), "--runs-root", str(checkout / "runs"), *flags],
        capture_output=True,
        text=True,
        check=True,
    )


def rows(checkout: Path) -> dict[str, dict[str, Any]]:
    doc = json.loads(measure(checkout, "--json").stdout)
    return {row["run_id"]: row for row in doc["runs"]}


def block(text: str, run_id: str) -> str:
    """The text output's block for one run."""
    start = text.index(f"== {run_id} ")
    end = text.find("\n== ", start + 1)
    return text[start : end if end != -1 else text.index("\nsummary", start)]


def test_a_brief_run_whose_every_rule_agrees_is_placed_by_both_rule_sets(
    tmp_path: Path,
) -> None:
    checkout = checkout_with_a_plan(tmp_path)
    project(
        checkout,
        "brief-widget",
        {"orchestrator.plan-kind": {"kind": "planning", "nodes": ["plan"]}},
    )
    task(checkout, "brief-widget", "plan", "Plan project: authoring:widget")
    run(checkout, "brief-widget", "authoring:brief-widget")

    row = rows(checkout)["brief-widget"]
    for result in ("original", "backfill"):
        assert row[result]["outcome"] == "placed", row[result]
        assert row[result]["projects"] == ["authoring:widget"]
        assert row[result]["kinds"] == ["planning"]
        assert row[result]["reasons"] == []

    text = block(measure(checkout).stdout, "brief-widget")
    assert "original rules: PLACED" in text
    assert "backfill rules: PLACED" in text


def test_a_plan_run_whose_name_contradicts_its_brief_is_ambiguous_under_the_original_rules(
    tmp_path: Path,
) -> None:
    """``plan-widget`` names its own brief project; its brief names ``authoring:widget``."""
    checkout = checkout_with_a_plan(tmp_path)
    project(
        checkout,
        "plan-widget",
        {"orchestrator.plan-kind": {"kind": "planning", "nodes": ["plan"]}},
    )
    task(checkout, "plan-widget", "plan", "Plan project: authoring:widget")
    run(checkout, "plan-widget", "authoring:plan-widget")

    row = rows(checkout)["plan-widget"]
    original = row["original"]
    assert original["outcome"] == "ambiguous", original
    assert original["projects"] == ["authoring:plan-widget", "authoring:widget"]
    assert "projects disagree: authoring:plan-widget, authoring:widget" in original["reasons"]
    # The refined measurement stays its own result, and the refinement resolves the run.
    assert row["backfill"]["outcome"] == "placed", row["backfill"]
    assert row["backfill"]["projects"] == ["authoring:widget"]

    text = block(measure(checkout).stdout, "plan-widget")
    assert (
        "original rules: AMBIGUOUS: projects disagree: authoring:plan-widget, authoring:widget"
        in text
    )
    assert "backfill rules: PLACED" in text
