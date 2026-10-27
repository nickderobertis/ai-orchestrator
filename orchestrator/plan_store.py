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
    QueryResponseOfQualifiedDocument,
    QueryResponseOfQualifiedEdge,
    QueryResponseOfQualifiedLabel,
    QueryResponseOfQualifiedProject,
    QueryResponseOfQualifiedTask,
    QueryResponseOfSearchHit,
)
from onetaskgraph_sdk._generated.query_response_of_qualified_task import QualifiedTask

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
MEMBERS_KEY = "onetaskgraph.members"
#: Where a copy records, on the item it copied, the counterpart it reached at each destination
#: source: an object mapping a source name to a qualified id there (onetaskgraph's
#: `docs/metadata.md`).
COPIES_KEY = "onetaskgraph.copies"
#: Where a source that serves a record's images at URLs records what it served, on the
#: record it wrote: an object keyed by asset name, each value `{"sha256": <lowercase hex of
#: the bytes>, "url": <string>}`, as onetaskgraph's plugin protocol states it. A `local-md`
#: record keeps its images as files beside it instead, which :func:`read_document_assets`
#: answers.
ASSETS_KEY = "onetaskgraph.assets"
RECORD_COMPONENT = re.compile(r"(?!\.+$)[\w.@+-]+")
QualifiedTaskId = NewType("QualifiedTaskId", str)
QualifiedDocumentId = NewType("QualifiedDocumentId", str)
QualifiedProjectId = NewType("QualifiedProjectId", str)
NodeId = NewType("NodeId", str)
#: A document's change token, as a listing of it reports it: opaque, spelled by its source
#: alone, and what a write guarded on the document as read expects to find it at.
DocumentVersion = NewType("DocumentVersion", str)


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
    #: The native id of the project the store files this task under, or `None` for a
    #: record not read from the store.
    project: str | None = None
    #: Where the store says this task's record is — the absolute path of a local
    #: Markdown record — or `None` for a record not read from the store or one the store
    #: reports no location for.
    location: str | None = None


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
    #: The change token the listing that read this document reported, or `None` where its
    #: source reports none: what a write guarded on the document as read expects.
    version: DocumentVersion | None = None


@dataclass(frozen=True)
class StoreAsset:
    """One image a document holds, as the store's `document show` answers it."""

    #: The bare file name the document's content references as `./<name>`.
    name: str
    #: The absolute path holding its bytes on this machine, or `None` for a source that
    #: serves them at a URL instead.
    path: str | None
    #: The lowercase hex SHA-256 the store states for its bytes.
    sha256: str


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


def members(project: str) -> frozenset[QualifiedProjectId]:
    """The member projects the home ``project`` names, by qualified id; none for any other.

    A plan whose tasks the store routed to several sources is one **home** project plus a
    member project in each other source, tied by the home's :data:`MEMBERS_KEY`. Read off
    the home on every call, as the store reads it, because nothing about a plan's members is
    kept anywhere else.
    """
    return _named_members(project, project_record(project).get("metadata"))


def _named_members(project: str, metadata: object) -> frozenset[QualifiedProjectId]:
    """The members ``project``'s record ``metadata`` names, held to being qualified ids."""
    named = metadata.get(MEMBERS_KEY) if isinstance(metadata, Mapping) else None
    if named is None:
        return frozenset()
    if not isinstance(named, list) or not all(
        isinstance(member, str) and qualified_id(member) is not None for member in named
    ):
        raise OSError(
            f"project {project!r} names its members as {named!r}, which is not a list of "
            f"qualified project ids"
        )
    return frozenset(QualifiedProjectId(member) for member in named)


def plan_listing(project: str, recorded: Mapping[str, object] | None = None) -> list[QualifiedTask]:
    """Every task of ``project`` and of each member project it names, each held to being one.

    Which read is decided by what the store states about the project, never by catching a
    refusal: its record names the members a routed copy tied to it, and only then is the
    store's own members read asked for (`task list --project <home> --members`) — so a plan
    whose tasks the store routed to another source is read as one plan wherever its home
    lives, and any failure of that read is the plan's refusal, never an empty or partial
    plan. A task in the home's own source is held to the home, and one anywhere else to a
    member its record names.

    The record is ``recorded`` when a caller has already read it, and otherwise the one
    project read by id (:func:`project_record`) — never a listing of its source, which on
    the `plans` board walked every project ever filed to find this one, and past GitHub's
    1,000-result search cap did not even see them all. So a project the store holds no
    record of is refused here, as every other read of it already was.
    """
    source, native = qualified(project)
    held_record = project_record(project) if recorded is None else recorded
    named = _named_members(project, held_record.get("metadata"))
    if named:
        listed = every_page(
            f"project {project!r}",
            client().task_list,
            source=[source],
            project=project,
            members=True,
        )
    else:
        listed = every_page(
            f"project {project!r}", client().task_list, source=[source], project=native
        )
    held = list(chain.from_iterable(page.items for page in listed))
    for task in held:
        held_id = task.id.model_dump()
        # Membership is the item's own `project`, never its id: a task created by the
        # store — from a template, say — is named by the source, and a `local-md` source
        # names it after its title rather than under a directory of its project.
        filed = None if task.item.project is None else task.item.project.model_dump()
        if held_id.startswith(f"{source}:") and filed == native:
            continue
        # llmlint: ignore-block[changed_behavior_has_e2e] A trust-boundary guard no conforming
        # store reaches: a members read answers only the home's tasks and its members', so
        # `tests/test_plan_store_sdk.py` reads a real store's template-created task through
        # it and substitutes only the listing to drive the refusal, as it does for the
        # source guard beside it.
        if filed is None or f"{held_id.partition(':')[0]}:{filed}" not in named:
            raise OSError(f"project {project!r} returned task {held_id!r} outside itself")
        # llmlint: ignore-end[changed_behavior_has_e2e]
    return held


def read_tasks(project: str, recorded: Mapping[str, object] | None = None) -> list[StoreTask]:
    """Every task of ``project``, as :func:`plan_listing` reads it, its dependencies resolved.

    ``recorded`` is the project's own record when the caller has read it already, so one
    invocation asks the store for it once. The dependencies are the store's own `project
    graph` of the home and of each member project its record names — one read per project
    rather than one `task deps` per task, the graph reading each task's edges exactly as that
    verb does — so a plan's tasks and their edges cost a fixed number of store invocations
    however many tasks it holds.
    """
    held_record = project_record(project) if recorded is None else recorded
    records = []
    for held in plan_listing(project, held_record):
        held_id = held.id.model_dump()
        filed = None if held.item.project is None else held.item.project.model_dump()
        item, metadata = held.item, held.item.metadata
        node_id = metadata.get("onepipeline.id")
        if not isinstance(node_id, str):
            raise OSError(f"task {held.id} has no string onepipeline.id")
        repositories = [repository.model_dump() for repository in item.repositories]
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
                tuple(x.model_dump() for x in item.delivers),
                filed,
                located(item.location.model_dump(mode="python"), held_id)
                if item.location
                else None,
            )
        )
    ids = {record.qualified_id: record.node_id for record in records}
    node_ids = {record.node_id for record in records}
    if len(ids) != len(records) or len(node_ids) != len(records):
        raise OSError("the store returned duplicate task or node identities")
    prerequisites: dict[QualifiedTaskId, list[QualifiedTaskId]] = {}
    for graphed in (project, *sorted(_named_members(project, held_record.get("metadata")))):
        for edge in sdk(client().project_graph(graphed, complete=True)).edges:
            dependent = QualifiedTaskId(edge.to.root)
            prerequisites.setdefault(dependent, []).append(QualifiedTaskId(edge.from_.root))
    resolved = []
    for record in records:
        targets = prerequisites.get(record.qualified_id, [])
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


def task_record(task: str, *, no_comments: bool = False) -> Mapping[str, object]:
    answer = complete(sdk(client().task_show(task, no_comments=no_comments)))
    if len(answer.items) != 1:
        raise OSError(f"task show for {task!r} returned {len(answer.items)} records")
    return dict(answer.items[0].item.model_dump(mode="python"))


def read_plan(
    project: str, records: Sequence[StoreTask], recorded: Mapping[str, object] | None = None
) -> dict[str, object]:
    """The plan ``project`` is, in the engine's loaded shape, from its tasks ``records``.

    ``recorded`` is the project's own record when the caller has read it already.
    """
    held = project_record(project) if recorded is None else recorded
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
    # Refused as unqualified before the store is asked for the record, as the task listing
    # refused it when it was this function's first read.
    qualified(project)
    record = project_record(project)
    records = read_tasks(project, record)
    return read_plan(project, records, record), records


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
                [x.model_dump() for x in item.repositories],
                item.metadata,
                location,
                DocumentVersion(item.version) if item.version is not None else None,
            )
        )
    return result


def read_document_assets(document: str) -> list[StoreAsset]:
    """Every image asset the store holds for ``document``, in the order its content references them.

    A reference whose image the store does not hold is simply absent here — a `local-md`
    file deleted from beside its record is not an error to the store — so a reader that must
    see every image a document shows compares this against the content's own references.
    """
    shown = complete(sdk(client().document_show(document)))
    # `assets` is absent only beside a source failure, which `complete` has refused, or for
    # a document not found, which the SDK has raised for; reading it as none is the safe
    # direction even so, since a reader comparing against the content then refuses.
    return [StoreAsset(asset.name.root, asset.path, asset.sha256) for asset in shown.assets or []]


def read_projects(source: str) -> list[StoreProject]:
    return _listed_projects(f"the projects of {source!r}", source)


def project_copies(source: str, origin: str) -> list[StoreProject]:
    """Every project of ``source`` the store records as copied from ``origin``.

    The store's `project list --origin`, which a board answers with a query narrowed to that
    origin rather than by paging every project it holds, so the read is bounded by the
    copies it finds and never by the board's size. Zero, one or several may match.
    """
    return _listed_projects(
        f"the projects of {source!r} copied from {origin}", source, origin=origin
    )


def _listed_projects(what: str, source: str, **query: object) -> list[StoreProject]:
    """Every project of ``source`` the store's `project list` answers for ``query``."""
    projects = [
        StoreProject(
            QualifiedProjectId(x.id.model_dump()),
            x.item.title,
            x.item.metadata,
            x.item.location.model_dump(mode="python") if x.item.location else None,
        )
        for page in every_page(what, client().project_list, source=[source], **query)
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


def local_file(location: str | None) -> str | None:
    """``location`` when it is an absolute path to a file on this host, and ``None`` otherwise.

    A store's location is whatever it reports — a local record's path, a board item's URL,
    or, from :func:`located`, the record's own qualified id — and only the first is a file
    a reader on this host can open. So a location handed to anybody as *a path to read* is
    held to that here, rather than trusted: a URL named as a file is a read that fails.
    """
    if location is None:
        return None
    path = Path(location)
    return str(path) if path.is_absolute() and path.is_file() else None


def configured_settings() -> dict[str, object]:
    return {
        setting.key.model_dump(): setting.value for setting in sdk(client().config_show()).settings
    }


def declared_private(source: str) -> bool:
    """Whether the store's resolution of every layer declares ``source`` `visibility: private`.

    A copy out of such a source carries no :data:`ORIGIN_KEY`: the store keeps the
    correspondence on the private side alone, as :data:`COPIES_KEY`.
    """
    return configured_settings().get(f"sources.{source}.visibility") == "private"


def routed_sources(source: str) -> tuple[str, ...]:
    """The sources ``source``'s `routes` send an item to, in the order its entries name them.

    Read off the store's own resolution of every layer, which prints a document's list as
    one setting and an environment or flag layer's entries one setting per field: a plan
    copied into ``source`` may have its home in any of these, and in no other.
    """
    values = configured_settings()
    listed = values.get(f"sources.{source}.routes")
    entries = listed if isinstance(listed, list) else []
    named = [entry.get("to") for entry in entries if isinstance(entry, dict)]
    field = re.compile(rf"sources\.{re.escape(source)}\.routes\.(\d+)\.to")
    indexed = sorted(
        (int(matched.group(1)), value)
        for key, value in values.items()
        if (matched := field.fullmatch(key)) is not None
    )
    named.extend(value for _, value in indexed)
    return tuple(dict.fromkeys(to for to in named if isinstance(to, str) and to))


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
