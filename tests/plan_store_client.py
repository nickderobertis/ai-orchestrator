"""The suite's synchronous plan-store client: the SDK over this checkout's locked CLI.

No production code reads the plan store through Python; the host's readers run the store's
CLI from their scripts. What is left here serves the journeys that set up or read back a
store of their own — always through the `onetaskgraph` the lock installed beside the
interpreter (`published_tools.ONETASKGRAPH_BIN`), never one from `PATH` — together with the
shapes those journeys write a plan and find its records by.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import NotRequired, TypedDict

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
from published_tools import ONETASKGRAPH_BIN

from orchestrator.root import REPO_ROOT

#: The writable plugin a journey's scratch source is declared with.
WRITABLE_PLUGIN = "local-md"
#: The directories of a `local-md` root a record sits in: `<root>/projects/<project>.md`
#: and `<root>/tasks/…`. The store owns the layout; these are what a journey finds them by.
PROJECTS_DIRECTORY = "projects"
TASKS_DIRECTORY = "tasks"
#: How many pages one listing may read before it is refused, so a store that kept minting
#: fresh cursors cannot spin a journey for ever.
LISTING_PAGE_CEILING = 1000


class PlanNode(TypedDict):
    """One node of a plan a journey writes."""

    id: str
    task: NotRequired[str]
    title: NotRequired[str]
    deps: NotRequired[list[str]]
    repo: NotRequired[str]
    delivers: NotRequired[list[str]]
    unit: NotRequired[str]


class PlanDocument(TypedDict):
    """A plan a journey writes: its name and its nodes."""

    name: str
    tasks: list[PlanNode]


def sdk[T](awaitable: Coroutine[object, object, T]) -> T:
    """Run one SDK call to its answer, a store refusal raised as :class:`OSError`."""
    try:
        return asyncio.run(awaitable)
    except OnetaskgraphError as exc:
        raise OSError(str(exc)) from exc


def client() -> Client:
    """The SDK client on this checkout's locked plan-store CLI."""
    return Client(binary=ONETASKGRAPH_BIN, cwd=REPO_ROOT)


def complete[T](answer: T) -> T:
    """``answer``, refused when any source failed to answer it, so nothing partial is read."""
    errors = getattr(answer, "errors", [])
    if errors:
        raise OSError(
            "; ".join(
                f"source {error.source.model_dump()} could not answer: "
                f"{error.error.model_dump_json(exclude_none=True)}"
                for error in errors
            )
        )
    return answer


#: One page of a plan-store listing, as the SDK's generated models type it.
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
    """Every page of one listing, in order: a listing is a page, never the whole answer.

    ``query`` is the client's bound listing method and ``keywords`` its query, repeated on
    each page with its cursor. A page any source failed, a cursor answered twice, or more
    than :data:`LISTING_PAGE_CEILING` pages is refused, naming ``what``.
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
            raise OSError(f"listing {what} answered the page cursor {cursor!r} again")
        followed.add(cursor)
    raise OSError(f"listing {what} read {LISTING_PAGE_CEILING} pages and still had more")


def configured_settings() -> dict[str, object]:
    """Every setting the store resolves for this checkout, by its dotted key."""
    return {
        setting.key.model_dump(): setting.value for setting in sdk(client().config_show()).settings
    }


def reachable(source: str) -> tuple[str, ...]:
    """`sources route`'s `reachable` for ``source``: itself, then each source it routes to."""
    return tuple(name.root for name in sdk(client().sources_route(source)).reachable)


def ensure_writable_source_root(source: str) -> Path:
    """The writable `local-md` root the store resolves for ``source``, created if absent."""
    values = configured_settings()
    plugin = values.get(f"sources.{source}.plugin")
    configured = values.get(f"sources.{source}.config.root")
    if plugin != WRITABLE_PLUGIN:
        raise OSError(f"source {source!r} is a {plugin!r} source")
    if not isinstance(configured, str) or not configured or "\0" in configured:
        raise OSError(f"source {source!r} names no valid root")
    root = Path(configured) if Path(configured).is_absolute() else REPO_ROOT / configured
    if root.exists() and not root.is_dir():
        raise OSError(f"source {source!r} root {root} is not a directory")
    root.mkdir(parents=True, exist_ok=True)
    if not os.access(root, os.W_OK | os.X_OK):
        raise OSError(f"source {source!r} root {root} is not writable")
    return root
