"""Render onepipeline's plan model as a local-md project and its tasks."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast


class PlanNode(TypedDict):
    """One generated node before it is serialized as a local-md task."""

    id: str
    task: NotRequired[str]
    title: NotRequired[str]
    deps: NotRequired[list[str]]
    repo: NotRequired[str]


class PlanDocument(TypedDict):
    """The stable portion of onepipeline's generated plan contract."""

    name: str
    tasks: list[PlanNode]


def _slug(value: str) -> str:
    rendered = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not rendered:
        raise ValueError("a project id must contain a lower-case letter or digit")
    return rendered


def _frontmatter(fields: Mapping[str, object], body: str) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(
                f"  {json.dumps(name)}: {json.dumps(held)}" for name, held in value.items()
            )
        else:
            lines.append(f"{key}: {json.dumps(value)}")
    return "\n".join([*lines, "---", "", body.rstrip(), ""])


# llmlint: ignore[suppressions_justified] Plan metadata is open; stable keys narrow below.
def render_plan_project(
    plan: Mapping[str, Any], *, native_id: str | None = None
) -> dict[Path, str]:
    """Map a plan document onto the project/task records onepipeline 0.16 reads."""
    name = plan.get("name")
    tasks = plan.get("tasks")
    if not isinstance(name, str) or not isinstance(tasks, list):
        raise ValueError("a generated plan requires string `name` and list `tasks`")
    # The cast records the validated stable keys while retaining arbitrary
    # onepipeline metadata, which is deliberately copied through below.
    document = cast(PlanDocument, plan)
    project = _slug(native_id or name)
    project_metadata = {
        f"onepipeline.{key}": value
        for key, value in document.items()
        if key not in {"name", "tasks"}
    }
    rendered = {
        Path("projects") / f"{project}.md": _frontmatter(
            {"title": name, "status": "todo", "metadata": project_metadata},
            f"Execution plan {project}.",
        )
    }
    validated: list[tuple[PlanNode, str]] = []
    task_ids: set[str] = set()
    task_slugs: set[str] = set()
    task_names: dict[str, str] = {}
    for raw in document["tasks"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise ValueError("every generated plan task requires a string `id`")
        node_id = raw["id"]
        node_slug = _slug(node_id)
        if node_id in task_ids or node_slug in task_slugs:
            raise ValueError(f"generated task id {node_id!r} is duplicate or slug-colliding")
        task_ids.add(node_id)
        task_slugs.add(node_slug)
        task_names[node_id] = node_slug
        validated.append((raw, node_slug))
    for raw, node_slug in validated:
        node = dict(raw)
        node_id = raw["id"]
        node.pop("id")
        body = node.pop("task", "")
        title = node.pop("title", node_id)
        deps = node.pop("deps", [])
        repo = node.pop("repo", None)
        if not isinstance(body, str) or not isinstance(title, str):
            raise ValueError(f"generated task {node_id!r} requires string title and task")
        if not isinstance(deps, list) or not all(
            isinstance(dependency, str) for dependency in deps
        ):
            raise ValueError(f"generated task {node_id!r} requires a list of string dependencies")
        unknown = [dependency for dependency in deps if dependency not in task_ids]
        if unknown:
            raise ValueError(
                f"generated task {node_id!r} names unknown dependencies: {', '.join(unknown)}"
            )
        fields: dict[str, object] = {
            "title": title,
            "project": project,
            "status": "todo",
        }
        if repo is not None:
            if not isinstance(repo, str):
                raise ValueError(f"generated task {node_id!r} has a non-string repository")
            normalized = re.sub(r"^https?://", "", repo).removesuffix(".git")
            if re.fullmatch(r"[A-Za-z0-9.-]+/[^/]+/[^/]+", normalized):
                fields["repositories"] = [normalized]
            else:
                node["repo"] = repo
        if deps:
            fields["depends_on"] = [f"{project}/{task_names[dependency]}" for dependency in deps]
        fields["metadata"] = {"onepipeline.id": node_id} | {
            f"onepipeline.{key}": value for key, value in node.items()
        }
        rendered[Path("tasks") / project / f"{node_slug}.md"] = _frontmatter(fields, body)
    return rendered


# llmlint: ignore[suppressions_justified] This boundary preserves open engine metadata.
def write_plan_project(root: Path, plan: Mapping[str, Any], *, native_id: str | None = None) -> str:
    """Write a rendered project under one local-md root and return its native id."""
    rendered = render_plan_project(plan, native_id=native_id)
    project = next(path.stem for path in rendered if path.parent == Path("projects"))
    for relative, content in rendered.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    task_directory = root / "tasks" / project
    current_tasks = {root / path for path in rendered if path.parent == Path("tasks") / project}
    if task_directory.is_dir():
        for existing in task_directory.iterdir():
            if existing.is_file() and existing not in current_tasks:
                existing.unlink()
    return project


def main() -> int:
    """Read one generated plan on stdin and write its local-md project below ROOT."""
    if len(sys.argv) != 2:
        print("usage: python -m orchestrator.project_store ROOT", file=sys.stderr)
        return 2
    try:
        document = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"project-store: input is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(document, dict):
        print("project-store: generated plan is not an object", file=sys.stderr)
        return 2
    try:
        project = write_plan_project(Path(sys.argv[1]), document)
    except (OSError, ValueError) as exc:
        print(f"project-store: cannot write generated plan: {exc}", file=sys.stderr)
        return 2
    print(project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
