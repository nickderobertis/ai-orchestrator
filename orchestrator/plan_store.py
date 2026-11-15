"""Synchronous plan-store helpers backed by :mod:`onetaskgraph_sdk`."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Callable, Coroutine, Mapping
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

# `task locate`'s per-task answer, unexported beside its `LocateReport` root for the reason
# `QualifiedTask` is, and retired by the same follow-up as that model.
from onetaskgraph_sdk._generated.locate_report import Located

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
#: Where a copy records, on the item it copies, the create it has in flight at each destination
#: source, removed with the write that records its link (onetaskgraph's README, *A create safe
#: to retry*). The store alone writes it; a caller rewriting the item carries it through as read.
CREATING_KEY = "onetaskgraph.creating"
RECORD_COMPONENT = re.compile(r"(?!\.+$)[\w.@+-]+")
QualifiedTaskId = NewType("QualifiedTaskId", str)
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


def task_record(task: str, *, no_comments: bool = False) -> Mapping[str, object]:
    answer = complete(sdk(client().task_show(task, no_comments=no_comments)))
    if len(answer.items) != 1:
        raise OSError(f"task show for {task!r} returned {len(answer.items)} records")
    return dict(answer.items[0].item.model_dump(mode="python"))


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


def reachable(source: str) -> tuple[str, ...]:
    """Every source an item written to ``source`` could land in: the store's own answer.

    `sources route`'s `reachable`, which reads configuration alone: ``source`` itself first,
    then each source its routes name as `to`, each once, whatever an item's repositories and
    classification — the set a routed copy reads ahead from, so a plan copied into ``source``
    may have its home in any of these and in no other, and a follow-up ticket filed through
    ``source`` lands on one of them.
    """
    return tuple(name.root for name in sdk(client().sources_route(source)).reachable)


def task_locate(task: str, to: str) -> Located:
    """Where `task copy --to <to>` would put ``task``: `task locate --no-counterpart`.

    The store reads the task, classifies it as a write does and places it through the copy's
    own placement path, answering its destination and the repository a create there would
    file it in — with `refused` where the destination's `creation_owners` refuses that create
    — and makes no lookup at the destination, so it sends the board nothing. :class:`OSError`
    for every refusal.
    """
    try:
        answer = asyncio.run(client().task_locate([task], to=to, no_counterpart=True))
    except OnetaskgraphError as exc:
        raise OSError(str(exc)) from exc
    # llmlint: ignore-block[changed_behavior_has_e2e] A trust-boundary guard no conforming store
    # reaches: `task locate` answers one entry per id named, in order, so a real store cannot be
    # made to answer another count or task; `tests/test_plan_store_sdk.py` drives the refusal
    # over the SDK's own typed `LocateReport`, substituting only the answer.
    if len(answer.items) != 1 or answer.items[0].source.root != task:
        raise OSError(f"task locate for {task!r} answered {len(answer.items)} other items")
    # llmlint: ignore-end[changed_behavior_has_e2e]
    return answer.items[0]


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
