"""Materialize test-owned local Markdown projects for real onepipeline launches."""

from __future__ import annotations

import atexit
import itertools
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

_WRITTEN: list[Path] = []
_PROJECT_SEQUENCE = itertools.count()
_PROJECT_ROOT = Path("/tmp/ai-orchestrator-test-projects")


def local_project(content: str, name: str) -> str:
    """Write one unique authoring project and return its qualified id."""
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    native = f"test-{os.getpid()}-{next(_PROJECT_SEQUENCE)}-{slug}"
    plan = json.loads(content)
    if not isinstance(plan, dict):
        raise ValueError("a plan fixture must be a JSON object")
    plan.setdefault("name", native)
    write_plan_project(_PROJECT_ROOT, plan, native_id=native)
    written = [_PROJECT_ROOT / "projects" / f"{native}.md", _PROJECT_ROOT / "tasks" / native]
    _WRITTEN.extend(written)
    return f"test-fixtures:{native}"


def project_from_plan(plan: Path, name: str | None = None) -> str:
    """Store a JSON plan as a local project through the committed source layout."""
    return local_project(plan.read_text(encoding="utf-8"), name or plan.stem)


# llmlint: ignore[suppressions_justified] The fixture reconstructs open plan metadata.
def read_project_plan(project: str) -> dict[str, Any]:
    """Read a stored project through onetaskgraph's public JSON command surface."""
    source, native = project.split(":", 1)

    # llmlint: ignore[suppressions_justified] The CLI owns this open JSON schema.
    def read(*arguments: str) -> dict[str, Any]:
        completed = subprocess.run(
            [str(Path.home() / ".local/bin/onetaskgraph"), *arguments, "--json"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(completed.stdout)
        assert isinstance(payload, dict)
        return payload

    project_item = read("project", "show", project)["items"][0]["item"]
    plan = {
        key.removeprefix("onepipeline."): value
        for key, value in project_item.get("metadata", {}).items()
        if key.startswith("onepipeline.")
    }
    plan.setdefault("name", project_item["title"])
    listed = read("task", "list", "--source", source, "--project", native, "--limit", "1000")[
        "items"
    ]
    ids = {record["id"]: record["item"]["metadata"]["onepipeline.id"] for record in listed}
    # llmlint: ignore[suppressions_justified] Tasks include open round-tripped metadata.
    tasks: list[dict[str, Any]] = []
    for record in listed:
        item = record["item"]
        node = {
            key.removeprefix("onepipeline."): value
            for key, value in item.get("metadata", {}).items()
            if key.startswith("onepipeline.")
        }
        node.update(title=item.get("title"), task=item.get("content"))
        if item.get("repositories"):
            node["repo"] = item["repositories"][0]
        dependencies = [
            ids[edge["to"]["id"]] for edge in read("task", "deps", record["id"])["items"]
        ]
        if dependencies:
            node["deps"] = dependencies
        tasks.append(node)
    plan["tasks"] = tasks
    return plan


@atexit.register
def _remove_projects() -> None:
    for written in reversed(_WRITTEN):
        if written.is_dir():
            for child in written.iterdir():
                child.unlink(missing_ok=True)
            written.rmdir()
        else:
            written.unlink(missing_ok=True)
