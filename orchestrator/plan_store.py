"""Synchronous plan-store helpers backed by :mod:`onetaskgraph_sdk`."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path
from typing import NewType

from onetaskgraph_sdk import (
    Client,
    OnetaskgraphError,
)

# The SDK publishes these as `onetaskgraph_sdk.QueryResponseOf…`, each an alias of the
# `QueryResponse` its generated module defines — and an alias whose name differs from the
# original is not a re-export to a strict type checker, so through the public name mypy
# sees nothing, and a `type: ignore` there would make every page `Any` and untype every
# reader. They are taken from the modules that define them instead;
# `tests/test_plan_store_sdk.py` holds each to its public alias, so an SDK that moved
# them fails there rather than at a dispatch.
from onetaskgraph_sdk._generated.query_response_of_qualified_document import (
    QueryResponse as QueryResponseOfQualifiedDocument,
)
from onetaskgraph_sdk._generated.query_response_of_qualified_edge import (
    QueryResponse as QueryResponseOfQualifiedEdge,
)
from onetaskgraph_sdk._generated.query_response_of_qualified_label import (
    QueryResponse as QueryResponseOfQualifiedLabel,
)
from onetaskgraph_sdk._generated.query_response_of_qualified_project import (
    QueryResponse as QueryResponseOfQualifiedProject,
)
from onetaskgraph_sdk._generated.query_response_of_qualified_task import (
    QueryResponse as QueryResponseOfQualifiedTask,
)
from onetaskgraph_sdk._generated.query_response_of_search_hit import (
    QueryResponse as QueryResponseOfSearchHit,
)

from orchestrator.project_store import (
    CROSS_DAG_DEPS,
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


#: The one environment name the SDK would otherwise resolve its binary from. Named here
#: so the refusal below can say which variable it is refusing.
SDK_BINARY_VARIABLE = "ONETASKGRAPH_SDK_BINARY"
#: The CLI the SDK drives, as the lock installs it beside the interpreter.
CLI_NAME = "onetaskgraph"
#: This checkout's provisioning recipe, the remedy every refusal below names.
BOOTSTRAP = "just bootstrap"


def locked_binary(interpreter: str | Path | None = None) -> Path:
    """The ``onetaskgraph`` this checkout's lock installed, beside the running interpreter.

    Every plan-store read in this package runs this file and nothing else. The SDK this
    module imports and the CLI it drives come from one ``uv sync`` of one lock —
    ``pyproject.toml`` pins ``onetaskgraph-sdk``, which brings the CLI, at the release
    ``config/onetaskgraph.version`` names — so the interpreter that imported the SDK is
    the one authority on where the matching CLI is: ``<its directory>/onetaskgraph``,
    which is ``.venv/bin/`` under ``uv run`` and under every wrapper here. The directory
    is taken from ``sys.executable`` *without* resolving symlinks, because
    ``.venv/bin/python`` is a symlink into uv's managed interpreter directory, where no
    ``onetaskgraph`` lives.

    Left to itself the SDK resolves its binary from ``ONETASKGRAPH_SDK_BINARY``, then from
    ``PATH``, and a host with any other ``onetaskgraph`` ahead on ``PATH`` turned every read
    here — a plan check, a design approval, a board listing, a ticket validation, a copy —
    into a read about the wrong release: a follow-up re-dispatch whose shell resolved
    ``~/.local/bin/onetaskgraph`` (0.2.12) had every ticket refused, because that
    release's ``task show`` reports no ``location``. So ``PATH`` is never consulted, and
    ``ONETASKGRAPH_SDK_BINARY`` is neither honoured nor ignored but **refused by name**:
    honouring it recreates that failure through a different door, and ignoring it
    silently leaves a lever connected to nothing, the shape ``AGENTS.md`` refuses under
    "Which pin governs a dispatch". ``config/onetaskgraph.version`` and the lock are the
    one answer to which release runs, and a second answer in the environment is the
    incident by another route. (The SDK strips the variable from the child's environment
    on its own, so nothing downstream reads it either way.)

    ``interpreter`` defaults to ``sys.executable`` and exists so a check can drive the
    resolution against a real interpreter beside which no CLI is installed. An absent or
    non-executable file is refused by name before the SDK's own generic "binary not
    found", with this checkout's provisioning recipe as the remedy — the refusal
    ``scripts/follow-ups.sh`` makes of an unprovisioned checkout, one layer down.
    """
    expected = Path(interpreter if interpreter is not None else sys.executable).parent / CLI_NAME
    if os.environ.get(SDK_BINARY_VARIABLE):
        raise OSError(
            f"{SDK_BINARY_VARIABLE} is set, and it is not a lever here: every plan-store "
            f"read of this package runs the locked install at {expected}, because "
            f"config/onetaskgraph.version and the lock are the one answer to which release "
            f"runs; unset it"
        )
    if not expected.is_file() or not os.access(expected, os.X_OK):
        raise OSError(
            f"this checkout has no plan-store CLI at {expected}, and a plan-store read may "
            f"run no other; provision this checkout with '{BOOTSTRAP}', then retry"
        )
    return expected


def client() -> Client:
    """The SDK client every plan-store read here goes through, on :func:`locked_binary`."""
    return Client(binary=locked_binary(), cwd=REPO_ROOT)


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


#: How many pages one listing may read before it is refused. The store answers a
#: ``next`` cursor page after page until every source is exhausted, so a store that
#: kept minting fresh cursors for ever — a bug — would spin the caller without this
#: bound; a cursor that never advances is refused by name the first time it repeats,
#: below, so this never reaches that case. It exists for termination, never as a
#: budget: at the store's default page of 50 it is fifty thousand items, far past any
#: board or plan project this host reads.
LISTING_PAGE_CEILING = 1000


#: One page of a plan-store listing: the SDK's generated ``QueryResponse`` models, one per
#: listing, which are the one statement of a page's shape — ``items``, ``errors``, and
#: ``next`` carrying the SDK's own ``PageToken`` — and share no base class to name instead.
type QueryResponse = (
    QueryResponseOfQualifiedTask
    | QueryResponseOfQualifiedDocument
    | QueryResponseOfQualifiedProject
    | QueryResponseOfQualifiedEdge
    | QueryResponseOfQualifiedLabel
    | QueryResponseOfSearchHit
)


def every_page[P: QueryResponse](
    what: str, query: Callable[..., Coroutine[object, object, P]], **keywords: object
) -> list[P]:
    """Read one paged SDK listing to exhaustion: every page, in listing order.

    A plan-store listing is a page, not an answer. ``task_list``, ``document_list``,
    ``project_list`` and ``task_deps`` each return one page of the store's ``page_size``
    (50 unless configured) and a ``next`` cursor when more remain, so a reader that takes
    the first answer as the whole listing silently reads a board as smaller than it is —
    which is how a run's follow-up comments went unseen once the ``followups`` board
    passed fifty items. Every listing in this package reads through here, so a reader
    added later cannot make that mistake again: ``query`` is the client's bound listing
    method, ``keywords`` its query, and ``what`` names the listing in a refusal. The pages
    come back as the generated model the method answers with, so a reader walks
    ``page.items`` across them and its item type is that model's own.

    The cursor is bound to the query that produced it, so each page repeats ``keywords``
    with ``page=`` added, until a page reports no ``next``. :func:`complete`'s rule holds
    on every page — a page any source failed to answer refuses the whole listing, and
    nothing partial is returned. A cursor this listing has already followed is refused
    by name rather than followed again, because a store answering the same page twice
    would otherwise be read as a listing twice its size or never end at all; and more
    pages than :data:`LISTING_PAGE_CEILING` refuses rather than spinning.
    """
    pages: list[P] = []
    cursor: str | None = None
    followed: set[str] = set()
    for _ in range(LISTING_PAGE_CEILING):
        answer = complete(sdk(query(**keywords, page=cursor)))
        pages.append(answer)
        if answer.next is None:
            return pages
        cursor = answer.next.root
        if cursor in followed:
            raise OSError(
                f"listing {what} answered the page cursor {cursor!r} again after it was "
                "already followed; refusing to read the same page twice"
            )
        followed.add(cursor)
    raise OSError(
        f"listing {what} read {LISTING_PAGE_CEILING} pages (the ceiling "
        "plan_store.LISTING_PAGE_CEILING sets) and the store still answered a next cursor; "
        "refusing to read on"
    )


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
    listed = every_page(f"project {project!r}", client().task_list, source=[source], project=native)
    records = []
    for held in chain.from_iterable(page.items for page in listed):
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
            for page in every_page(
                f"the dependencies of {record.qualified_id}",
                client().task_deps,
                id=str(record.qualified_id),
            )
            for edge in page.items
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
    listed = every_page(
        f"the documents of {project!r}", client().document_list, source=[source], project=native
    )
    for held in chain.from_iterable(page.items for page in listed):
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
        for page in every_page(
            f"the projects of {source!r}", client().project_list, source=[source]
        )
        for x in page.items
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
