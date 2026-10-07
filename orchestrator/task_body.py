"""Measure each task's issue body against the limit the `plans` board's issues carry.

`just copy-plan` puts a plan on the `plans` GitHub Projects board, where every task
becomes an issue, and GitHub refuses an issue body over :data:`BODY_LIMIT` characters —
*"Body is too long (maximum is 65536 characters)"*. That refusal is the last step of the
flow, after the planning run, every review and the design-document launch have been
spent, so both of `just check-plan`'s paths read it first, the way
:mod:`orchestrator.adoption_guard` contributes its own.

**What is measured is the body the store sends, not the file on disk.** The
`github-projects` plugin of `nickderobertis/onetaskgraph` composes an issue body as the
task's content, :data:`METADATA_SEPARATOR`, and a metadata slot — :data:`METADATA_OPEN`,
the compact key-sorted JSON of the task's metadata map, :data:`METADATA_CLOSE` — with no
slot when the map is empty. :func:`compose` is that composition, held to the plugin's
source in the registered checkout by `tests/test_task_body.py`. The map measured is the
one this checkout's records carry — a task's `orchestrator.budgets` record among it, which
can run to as many characters as the `## Budgets` section it renders — because that is the
map the copy sends; what the copy
then adds or drops is small and is what the warning threshold covers, and
:data:`UNMEASURED` states it so every refusal and warning says what its figure is.

GitHub's refusal counts *characters*, so :func:`measure` counts Unicode code points —
``len()`` of the composed ``str`` — rather than bytes.

**A plan's design document becomes an issue on that board too, and is measured the same
way.** The plugin writes a document through the same `write_item` as a task — its content,
and its metadata map in the same slot `compose_body` appends — so :func:`compose` is its
composition as well, and `tests/test_task_body.py` holds the document write to that path.
It is measured by `just copy-plan` rather than by `just check-plan`, because in the flow
`just finish-plan` runs the check *before* the document-writing launch and the copy after
it: the copy's pre-flight is the one step that always has the document in front of it and
always precedes the write to the board. :func:`document_refusals` and
:func:`document_warnings` are its two faces, under the same :data:`BODY_LIMIT` and
:data:`WARN_FROM`.

**So does a plan's own description**, the project issue, whose metadata carries its
`orchestrator.plan-budgets` record beside the rendering of it: `just check-plan` measures it
with :func:`measure`, through `orchestrator/plan_budgets.py`, under the same limit.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from typing import NamedTuple

from orchestrator.plan_store import NodeId, QualifiedDocumentId, StoreDocument, StoreTask
from orchestrator.publication_guard import Refusal

#: The destination's refusal: an issue body over this many characters is one GitHub
#: refuses, and a task whose composed body measures over it is refused here.
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The limit is GitHub's, and
# GitHub publishes no machine-readable source for it: the one comparison that tests this
# number is `just copy-plan` onto the live `plans` board, whose refusal message states
# it, and no gate here spends a write to the live board.
BODY_LIMIT = 65_536

#: From this size a task is warned about rather than refused: the operational appendix
#: alone is about 23,000 characters, so a plan here is one ordinary edit from
#: unlaunchable, and the gap to the limit is also what covers the small keys the copy
#: adds that this does not measure.
WARN_FROM = 45_000

#: The two delimiters of the metadata slot and what the composer puts between a
#: non-empty visible body and the slot, spelled as the plugin's `METADATA_OPEN`,
#: `METADATA_CLOSE` and `METADATA_SEPARATOR` spell them.
METADATA_OPEN = "<!-- onetaskgraph.metadata\n"
METADATA_CLOSE = "\n-->"
METADATA_SEPARATOR = "\n\n"

#: The keys the copy itself adds to the slot or routes out of it, which this does not
#: measure: the origin goes to a project text field, the repositories are recorded only
#: when the list is not exactly the repository the issue lives in, the item-kind marker
#: is the board's own, and dependency edges between tasks of one board travel as native
#: relationships. Each is the plugin's `slot_metadata` doing, and `tests/test_task_body.py`
#: holds the four to its source.
UNMEASURED_KEYS = (
    "onetaskgraph.origin",
    "onetaskgraph.repositories",
    "onetaskgraph.item_kind",
    "onetaskgraph.depends_on",
)

#: What the figure leaves out, said once for the refusal and the warning alike.
UNMEASURED = (
    "measured as the record's content followed by the metadata slot the `github-projects` "
    "source appends — every `onepipeline.*` key, the review record and the "
    "`orchestrator.budgets` record, as compact key-sorted JSON — and leaving out what the "
    "copy itself adds or routes elsewhere: " + ", ".join(f"`{key}`" for key in UNMEASURED_KEYS)
)

#: The same account for a design document, whose map carries the template provenance
#: and the design approval rather than `onepipeline.*` keys and a review record; the keys
#: left out are the same, because the plugin's `slot_metadata` is the same call.
DOCUMENT_UNMEASURED = (
    "measured as the document's content followed by the metadata slot the "
    "`github-projects` source appends — its template provenance and its design approval, "
    "as compact key-sorted JSON — and leaving out what the copy itself adds or routes "
    "elsewhere: " + ", ".join(f"`{key}`" for key in UNMEASURED_KEYS)
)


class BodyError(ValueError):
    """A plan task whose composed issue body the destination board would refuse."""


class Body(NamedTuple):
    """One task's composed issue body, as its node and its size in characters."""

    node: NodeId
    size: int


class DocumentBody(NamedTuple):
    """One design document's composed issue body, as its qualified id and its size."""

    document: QualifiedDocumentId
    size: int


def compose(content: str | None, metadata: Mapping[str, object]) -> str:
    """The issue body the `github-projects` source sends for ``content`` and ``metadata``.

    Exactly as the plugin's `compose_body` composes it, the empty cases included: no slot
    for an empty map, and no leading blank lines for empty content. `serde_json` writes a
    `BTreeMap` compact and key-sorted, which is what the `json.dumps` arguments here
    reproduce for the values a record carries; ``ensure_ascii=False`` because GitHub
    counts characters and an escaped code point would count as six.
    """
    visible = content or ""
    if not metadata:
        return visible
    try:
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        # Every map that reaches here was decoded from the store's or the verb's JSON, so
        # this names a caller handing over a map no record could carry rather than a plan.
        raise BodyError(f"the metadata map is not one the store could carry: {exc}") from exc
    slot = f"{METADATA_OPEN}{encoded}{METADATA_CLOSE}"
    return slot if not visible else f"{visible}{METADATA_SEPARATOR}{slot}"


def measure(content: str | None, metadata: Mapping[str, object]) -> int:
    """How many characters — Unicode code points — the composed body runs to."""
    return len(compose(content, metadata))


def bodies(plan: object) -> Iterator[Body]:
    """Every task of a loaded ``plan``'s body, in the order stated, read leniently.

    Top-level tasks only, because those are the records that become issues: a lifecycle
    node's steps travel inside its own `onepipeline.steps` metadata, so they are inside
    the parent's body rather than bodies of their own. A `kind: human` node is a record
    too, so it is measured like any other. A task with no `id` is skipped for the reason
    :func:`orchestrator.adoption_guard.nodes` skips one: the engine's loader has already
    refused it, and nothing here decides a plan's structure.
    """
    match plan:
        case {"tasks": [*tasks]}:
            pass
        case _:
            return
    for task in tasks:
        match task:
            case {"id": str(node_id)} if node_id:
                pass
            case _:
                continue
        content = task.get("task")
        metadata = task.get("metadata")
        yield Body(
            NodeId(node_id),
            measure(
                content if isinstance(content, str) else None,
                metadata if isinstance(metadata, Mapping) else {},
            ),
        )


def record_bodies(records: Iterable[StoreTask]) -> Iterator[Body]:
    """Every store record's body, for the wrapper that re-reads the project from the store."""
    for record in records:
        yield Body(record.node_id, measure(record.content, record.metadata))


def refusal(body: Body) -> Refusal | None:
    """The refusal ``body`` earns for being over the limit, or ``None`` within it."""
    if body.size <= BODY_LIMIT:
        return None
    return Refusal(
        node=body.node,
        field="task",
        reason=(
            f"its composed issue body measures {body.size:,} characters, over the "
            f"{BODY_LIMIT:,}-character limit GitHub puts on an issue body, so `just "
            f"copy-plan` onto the `plans` board would refuse this task after every review "
            f"had been spent. Shorten the task ({UNMEASURED})."
        ),
    )


def warning(body: Body) -> str | None:
    """The warning ``body`` earns from :data:`WARN_FROM` up to the limit, or ``None``."""
    if body.size < WARN_FROM or body.size > BODY_LIMIT:
        return None
    return (
        f"check-plan: warning: {body.node}: its composed issue body measures {body.size:,} "
        f"characters, {100 * body.size // BODY_LIMIT}% of the {BODY_LIMIT:,}-character limit "
        f"the `plans` board's issues carry; a plan there is one ordinary edit from "
        f"unlaunchable ({UNMEASURED})"
    )


def refusals(plan: object) -> list[Refusal]:
    """Every task of a loaded ``plan`` whose composed body the destination board would refuse.

    The registered check's face, over the document `onepipeline plan check` hands it —
    which carries each task's metadata map verbatim, the review record included.
    """
    return [refused for body in bodies(plan) if (refused := refusal(body)) is not None]


def warnings(records: Iterable[StoreTask]) -> list[str]:
    """One warning per record of ``records`` measuring from :data:`WARN_FROM` up to the limit.

    Printed by the `just check-plan` wrapper rather than answered by the registered
    check, because `onepipeline plan check` swallows a registered check's stderr when the
    check exits 0 — and a warned plan is an accepted one.
    """
    return [warned for body in record_bodies(records) if (warned := warning(body)) is not None]


def check_records(records: Iterable[StoreTask]) -> None:
    """Raise :class:`BodyError` for the first record of ``records`` this board would refuse.

    The raising face of :func:`refusals`, for the path that reports one refusal at a
    time; `orchestrator/plan_check.py` reports them all through the verb. Over the
    store's records rather than a plan because that path's plan carries no metadata map
    — :func:`orchestrator.plan_store.read_plan` lifts the `onepipeline.*` keys out of it
    and drops the rest — and the map is part of the body. The message names the field
    beside the node, so the two paths report the same three things.
    """
    for body in record_bodies(records):
        refused = refusal(body)
        if refused is not None:
            raise BodyError(f"{refused.node}: {refused.field}: {refused.reason}")


def document_bodies(documents: Iterable[StoreDocument]) -> Iterator[DocumentBody]:
    """Every document's body, measured from the content and the map the store reads back.

    The content is what the source answers as the document's own — a `local-md` record's
    stored template answers are not part of it, and the board keeps none either — and the
    map is the record's whole metadata map, which is what the store's `document copy`
    hands the destination.
    """
    for document in documents:
        yield DocumentBody(document.qualified_id, measure(document.content, document.metadata))


def document_refusals(documents: Iterable[StoreDocument]) -> list[str]:
    """One refusal per document of ``documents`` whose composed body is over the limit.

    Inclusive at the limit, as :func:`refusal` is. Each line names the document, its size,
    the limit and what the figure leaves out, prefixed for the `just copy-plan` that prints
    it.
    """
    return [
        f"copy-plan: {body.document}: its composed issue body measures {body.size:,} "
        f"characters, over the {BODY_LIMIT:,}-character limit GitHub puts on an issue "
        f"body, so the board would refuse this design document after the plan had landed "
        f"without it. Shorten the document ({DOCUMENT_UNMEASURED})."
        for body in document_bodies(documents)
        if body.size > BODY_LIMIT
    ]


def document_warnings(documents: Iterable[StoreDocument]) -> list[str]:
    """One warning per document measuring from :data:`WARN_FROM` up to the limit."""
    return [
        f"copy-plan: warning: {body.document}: its composed issue body measures "
        f"{body.size:,} characters, {100 * body.size // BODY_LIMIT}% of the "
        f"{BODY_LIMIT:,}-character limit the `plans` board's issues carry; a design "
        f"document there is one ordinary edit from uncopyable ({DOCUMENT_UNMEASURED})"
        for body in document_bodies(documents)
        if WARN_FROM <= body.size <= BODY_LIMIT
    ]
