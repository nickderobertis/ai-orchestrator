"""Synchronous plan-store helpers backed by :mod:`onetaskgraph_sdk`."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NewType

from onetaskgraph_sdk import (
    Client,
    OnetaskgraphError,
)

from orchestrator.project_store import (
    PROJECTS_DIRECTORY,
    TASKS_DIRECTORY,
    QualifiedId,
    qualified_id,
)
from orchestrator.root import REPO_ROOT

WRITE_BACK_SOURCE = "onepipeline-writeback"
WRITE_BACK_REWROTE = "the dependency points into onepipeline's old write-back scratch source"
WRITABLE_PLUGIN = "local-md"
ORIGIN_KEY = "onetaskgraph.origin"
CROSS_DAG_DEPS = "onepipeline.deps"
RECORD_COMPONENT = re.compile(r"(?!\.+$)[\w.@+-]+")
QualifiedTaskId = NewType("QualifiedTaskId", str)
QualifiedDocumentId = NewType("QualifiedDocumentId", str)
QualifiedProjectId = NewType("QualifiedProjectId", str)
NodeId = NewType("NodeId", str)


def sdk[T](awaitable: Coroutine[object, object, T]) -> T:
    """Bridge the SDK's async API once for synchronous repository commands."""
    try:
        return asyncio.run(awaitable)
    except OnetaskgraphError as exc:
        raise OSError(str(exc)) from exc


def client() -> Client:
    return Client(cwd=REPO_ROOT)


def complete[T](answer: T) -> T:
    """Refuse an SDK query whose source failures make its answer partial."""
    errors = getattr(answer, "errors", [])
    if errors:
        details = "; ".join(
            f"source {error.source.model_dump()} could not answer: "
            f"{error.error.model_dump_json(exclude_none=True)}"
            for error in errors
        )
        raise OSError(details)
    return answer


@dataclass(frozen=True)
class StoreTask:
    qualified_id: QualifiedTaskId
    node_id: NodeId
    title: str
    content: str | None
    metadata: Mapping[str, object]
    repositories: list[object]
    deps: tuple[NodeId, ...]
    delivers: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoreDocument:
    qualified_id: QualifiedDocumentId
    title: str
    content: str
    project: str | None
    labels: list[str]
    repositories: list[str]
    metadata: Mapping[str, object]
    location: Mapping[str, object] | None


@dataclass(frozen=True)
class StoreProject:
    qualified_id: QualifiedProjectId
    title: str
    metadata: Mapping[str, object]
    location: Mapping[str, object] | None


def qualified(project: str) -> QualifiedId:
    parts = qualified_id(project)
    if parts is None:
        raise OSError(f"a project id must be qualified as <source>:<native>, got {project!r}")
    return parts


def authored_deps(task: StoreTask) -> list[str]:
    own = list(task.deps)
    stated = task.metadata.get(CROSS_DAG_DEPS)
    listed = stated if isinstance(stated, list) else []
    return sorted([*own, *(x for x in listed if isinstance(x, str) and x not in own)])


def read_tasks(project: str) -> list[StoreTask]:
    source, native = qualified(project)
    answer = complete(sdk(client().task_list(source=[source], project=native)))
    records = []
    for held in answer.items:
        held_id = held.id.model_dump()
        if not held_id.startswith(f"{source}:{native}/"):
            raise OSError(f"project {project!r} returned task {held_id!r} outside itself")
        item, metadata = held.item, held.item.metadata or {}
        node_id = metadata.get("onepipeline.id")
        if not isinstance(node_id, str):
            raise OSError(f"task {held.id} has no string onepipeline.id")
        repositories = [repository.model_dump() for repository in item.repositories or []]
        if len(repositories) > 1:
            raise OSError(f"task {held.id} has more than one repository")
        records.append(
            StoreTask(
                QualifiedTaskId(held_id),
                NodeId(node_id),
                item.title,
                item.content,
                metadata,
                repositories,
                (),
                tuple(x.model_dump() for x in item.delivers or []),
            )
        )
    ids = {record.qualified_id: record.node_id for record in records}
    node_ids = {record.node_id for record in records}
    if len(ids) != len(records) or len(node_ids) != len(records):
        raise OSError("the store returned duplicate task or node identities")
    resolved = []
    for record in records:
        targets = [
            QualifiedTaskId(edge.to.id.model_dump())
            for edge in complete(sdk(client().task_deps(str(record.qualified_id)))).items
        ]
        unknown = [target for target in targets if target not in ids]
        if unknown:
            extra = (
                f" — {WRITE_BACK_REWROTE}"
                if any(x.startswith(f"{WRITE_BACK_SOURCE}:") for x in unknown)
                else ""
            )
            raise OSError(
                f"unknown dependency targets for {record.qualified_id}: {', '.join(unknown)}{extra}"
            )
        resolved.append(replace(record, deps=tuple(ids[target] for target in targets)))
    return resolved


def project_record(project: str) -> Mapping[str, object]:
    answer = complete(sdk(client().project_show(project)))
    if len(answer.items) != 1:
        raise OSError(f"project show for {project!r} returned {len(answer.items)} records")
    return dict(answer.items[0].item.model_dump(mode="python"))


def task_record(task: str) -> Mapping[str, object]:
    answer = complete(sdk(client().task_show(task)))
    if len(answer.items) != 1:
        raise OSError(f"task show for {task!r} returned {len(answer.items)} records")
    return dict(answer.items[0].item.model_dump(mode="python"))


def read_plan(project: str, records: Sequence[StoreTask]) -> dict[str, object]:
    held = project_record(project)
    raw_metadata = held.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    plan = {
        k.removeprefix("onepipeline."): v
        for k, v in metadata.items()
        if k.startswith("onepipeline.")
    }
    plan.setdefault("name", held["title"])
    nodes = []
    for record in records:
        node = {
            k.removeprefix("onepipeline."): v
            for k, v in record.metadata.items()
            if k.startswith("onepipeline.")
        }
        node.update(title=record.title, task=record.content)
        if record.repositories:
            node["repo"] = record.repositories[0]
        if deps := authored_deps(record):
            node["deps"] = deps
        if record.delivers:
            node["delivers"] = list(record.delivers)
        nodes.append(node)
    plan["tasks"] = nodes
    return plan


def read_project(project: str) -> tuple[dict[str, object], list[StoreTask]]:
    records = read_tasks(project)
    return read_plan(project, records), records


def read_documents(project: str) -> list[StoreDocument]:
    source, native = qualified(project)
    result = []
    for held in complete(sdk(client().document_list(source=[source], project=native))).items:
        held_id = held.id.model_dump()
        item = held.item
        item_project = item.project.model_dump() if item.project else None
        if not held_id.startswith(f"{source}:") or item_project != native:
            raise OSError(f"project {project!r} returned document {held_id!r} outside itself")
        location = item.location.model_dump(mode="python") if item.location else None
        result.append(
            StoreDocument(
                QualifiedDocumentId(held_id),
                item.title,
                item.content or "",
                item_project,
                [label.name for label in item.labels],
                [x.model_dump() for x in item.repositories or []],
                item.metadata or {},
                location,
            )
        )
    return result


def read_projects(source: str) -> list[StoreProject]:
    projects = [
        StoreProject(
            QualifiedProjectId(x.id.model_dump()),
            x.item.title,
            x.item.metadata or {},
            x.item.location.model_dump(mode="python") if x.item.location else None,
        )
        for x in complete(sdk(client().project_list(source=[source]))).items
    ]
    if any(not str(project.qualified_id).startswith(f"{source}:") for project in projects):
        raise OSError(f"source {source!r} returned a project outside its own ids")
    return projects


def located(location: Mapping[str, object] | None, fallback: str) -> str:
    held = location or {}
    for key in ("url", "path"):
        value = held.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def configured_settings() -> dict[str, object]:
    return {
        setting.key.model_dump(): setting.value for setting in sdk(client().config_show()).settings
    }


def source_root(source: str) -> Path:
    values = configured_settings()
    plugin, root = (
        values.get(f"sources.{source}.plugin"),
        values.get(f"sources.{source}.config.root"),
    )
    if plugin != WRITABLE_PLUGIN:
        raise OSError(f"source {source!r} is a {plugin!r} source")
    if not isinstance(root, str) or not root or "\0" in root:
        raise OSError(f"source {source!r} names no valid root")
    path = Path(root)
    return path if path.is_absolute() else REPO_ROOT / path


def ensure_writable_source_root(source: str) -> Path:
    root = source_root(source)
    if root.exists() and not root.is_dir():
        raise OSError(f"source {source!r} root {root} is not a directory")
    root.mkdir(parents=True, exist_ok=True)
    if not os.access(root, os.W_OK | os.X_OK):
        raise OSError(f"source {source!r} root {root} is not writable")
    return root


def local_projects(source: str) -> list[str]:
    root = source_root(source) / PROJECTS_DIRECTORY
    return (
        [] if not root.exists() else [f"{source}:{path.stem}" for path in sorted(root.glob("*.md"))]
    )


def task_document(source: str, native_task_id: str) -> Path:
    """Return the existing local task path for read-only journey assertions."""
    project, separator, task = native_task_id.partition("/")
    if (
        not separator
        or not RECORD_COMPONENT.fullmatch(project)
        or not RECORD_COMPONENT.fullmatch(task)
    ):
        raise OSError(f"task id {native_task_id!r} is not <project>/<task>")
    document = source_root(source) / TASKS_DIRECTORY / project / f"{task}.md"
    if not document.is_file():
        raise OSError(f"task {source}:{native_task_id} has no record at {document}")
    return document
