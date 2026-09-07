"""Read the onetaskgraph store this repository plans against, and write one record back.

`just check-plan` and `just review-plan` both read a plan out of the store, and the
review command writes its verdict back into the task it reviewed. Both directions live
here so that neither is reinvented beside the other: the reading half was
`orchestrator/criteria_guard.py`'s private helper until a second caller needed it, and
the writing half exists at all because onetaskgraph's CLI has no update verb — a
`local-md` source is a directory, and the record goes into the file.

**Every refusal here is driven in `tests/test_plan_store.py` and by no journey**, and
that is a property of what they refuse rather than a gap. They are guards over another
program's records: a source served by a plugin this may not write, a native id that is
not a local record's path, frontmatter this cannot edit narrowly. Reaching one through
`just review-plan` means putting a malformed record into a plan root the suite's other
tiers are concurrently walking — and a local Markdown source that meets one refuses the
*whole* walk rather than skipping the record, which is the hazard
`tests/plan_fixture_root.py` records having already failed a publication. So each is
driven against a root the test owns outright.

Writing is deliberately narrow. It sets **one** namespaced key of one task's metadata
map and touches nothing else, and it refuses frontmatter it cannot edit that way rather
than reformatting the file. A plan record is an operator's authored document; rewriting
one to normalize it would make the review gate the thing that most often changes the
content it reviews.

**A document is written the other way round, and the difference is what a board can
take.** A task's review record goes into the file because only a local Markdown source
has a file; a *document* carries the design approval, and a plan is approved wherever it
is held — so :func:`write_document_metadata` goes through `onetaskgraph document copy`,
which is the store's own write side and answers for a directory and a board alike. It
stages the document it already read into a source of its own, adds the one entry, and
copies that over the record it came from, matched by title because the destination's
recorded origin names whatever copy created it rather than this one. Two costs come with
that and neither is hidden: the store rewrites `onetaskgraph.origin` to name the staging
source, because that key is the store's own bookkeeping of the last copy; and the write
is a whole-record replacement, so what is staged is everything the store just reported
rather than the fields this repository happens to care about.
"""

# llmlint: ignore-file[changed_behavior_has_e2e] Every refusal below is a guard over
# another program's records, and the module docstring above states why no journey drives
# one: reaching it means leaving a malformed record in a plan root the suite's other
# tiers walk concurrently, which refuses the whole walk rather than that record. They are
# driven in tests/test_plan_store.py against a root that test owns.

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NewType

from orchestrator.project_store import frontmatter
from orchestrator.root import REPO_ROOT

#: The standalone plan-store CLI this host spawns. `config/onetaskgraph.version` pins
#: it; nothing here reads that pin, because the installed program is what answers and
#: a pin that disagreed with it would only mislead.
STORE = "onetaskgraph"

#: How many task records one listing page holds. Small on purpose: paging is a contract
#: this reader depends on, and a page size no plan ever exceeds would leave the second
#: page unread on every host until the first plan that needed it.
PAGE_SIZE = 2

#: What `onepipeline` names the plan-store CLI in, and what has to be **removed** from
#: the environment of every store command this module spawns. It is the engine's way of
#: pointing at a binary; `onetaskgraph`'s own configuration layer reads every
#: `ONETASKGRAPH_*` name as a *setting*, so a process that inherits it refuses `bin` as an
#: unknown field and answers nothing at all — for `config show`, for a listing, for
#: everything. The engine strips it before it spawns; so does this, or a launch made from
#: inside a run that set it would be refused for a plan store that is perfectly readable.
BIN_ENV = "ONETASKGRAPH_BIN"

# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The producer of this
# string is onepipeline 0.19.0, a release this host has never installed — it was held
# below it while the rewrite stood, and is now past the 0.20.0 that repaired it — so
# there is no installed artifact to generate it from or reconcile it against. It is a
# recogniser for records nothing here authors rather than a contract either side must
# hold to: a producer that changes its source or encoding makes this stop matching, and
# the reader falls back to the bare unresolvable-target refusal it gave before, which is
# a degraded diagnostic and never a wrong answer.
#: The source `onepipeline`'s settlement write-back stages a projection in, and the one
#: it must never leave behind in a record it wrote back. onepipeline 0.19.0 replaced a
#: settled record's own `onetaskgraph.origin` and every `depends_on` edge with an
#: identity under this source — which exists only as scratch beneath `runs/<run>/` — so
#: the plan could not be read back at all afterwards. It is
#: https://github.com/nickderobertis/onepipeline/issues/189, repaired by
#: https://github.com/nickderobertis/onepipeline/pull/191 in onepipeline 0.20.0.
#:
#: **The recogniser outlives the repair on purpose.** It is about *records*, not about
#: this host's pin: a plan carried here from a host that ran 0.19.0 still carries the
#: rewrite, and the id decodes to hex and reads like a corrupt store rather than like
#: the engine that wrote it.
WRITE_BACK_SOURCE = "onepipeline-writeback"

#: What a reader is told when a dependency edge points into that source. The engine and
#: the issue are both named: a plan whose edges were rewritten is unreadable for a
#: reason nothing in the store can report.
WRITE_BACK_REWROTE = (
    f"the plan's dependency edges point into `{WRITE_BACK_SOURCE}:`, which is the "
    "scratch source onepipeline 0.19.0's settlement write-back rewrote a settled "
    "record's own origin and edges into, so this plan can no longer be read back — "
    "https://github.com/nickderobertis/onepipeline/issues/189, repaired in onepipeline "
    "0.20.0. These records were written by an engine at that one release; nothing this "
    "host installs writes them"
)

# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]

#: The plugin whose records this module may write. Every other source is read-only
#: here — a GitHub Projects board is not a directory, and a record written into one
#: would go through an API this repository deliberately does not call.
WRITABLE_PLUGIN = "local-md"


#: A task's address in the store, `<source>:<native-id>` — what a store command is
#: given. Distinct from :data:`NodeId`, which is what a *plan* calls the same task and
#: what a dependency edge resolves to: the two are both strings, they travel together
#: through every function here, and mixing them addresses the wrong record.
QualifiedTaskId = NewType("QualifiedTaskId", str)

#: A document's address in the store, `<source>:<native-id>`. Its own namespace: a
#: document and a task of one project may wear the same native id and address different
#: records, so the two are never interchanged even though both are strings.
QualifiedDocumentId = NewType("QualifiedDocumentId", str)

#: A project's address in the store, `<source>:<native-id>`. Its own namespace for the
#: reason the two above are: a project, a task and a document of one plan may wear the
#: same native id, and a copy gives the destination's project an id of the destination's
#: own choosing — a board mints a number where a directory keeps the name.
QualifiedProjectId = NewType("QualifiedProjectId", str)

#: What the store stamps on a record it created by copying, naming what it was copied
#: from. It is the store's own bookkeeping rather than anything this repository writes,
#: and it is the only thing that ties a landed record back to the one it came from: a
#: destination decides its own native id, so nothing about the source's name survives
#: the copy for a reader to compose an address out of.
ORIGIN_KEY = "onetaskgraph.origin"

#: The source name the write below stages a document under, which exists only for the
#: length of one copy. Deliberately unlike anything `onetaskgraph.yaml` configures: it is
#: added to the configuration of that one invocation, and a name a real source already
#: holds would repoint that source for the command doing the writing.
STAGING_SOURCE = "orchestrator-record-staging"

#: How the copy is told which destination record it is updating. A document a plan store
#: already holds was created by some earlier copy, so its recorded origin names *that*
#: source rather than the staging one below — the correspondence a bare copy would
#: follow is one this write can never satisfy, and following it would duplicate the
#: document instead of updating it. The title is what both records share.
MATCH_BY = "title"

#: A task's id within its plan — `onepipeline.id`, the name a run's journal, its branch,
#: and every refusal use.
NodeId = NewType("NodeId", str)


@dataclass(frozen=True)
class StoreTask:
    """A validated task record returned by onetaskgraph."""

    qualified_id: QualifiedTaskId
    node_id: NodeId
    title: str
    content: str | None
    metadata: Mapping[str, object]
    repositories: list[object]
    deps: tuple[NodeId, ...]


def store_binary() -> str:
    """The plan-store CLI this checkout spawns, refused by name when it has none.

    One source for every command here that runs it, so a read and the write beside it
    cannot resolve two different binaries — which on this host is not a theoretical
    difference: `config/onetaskgraph.version` is per checkout, and a copy of this
    program provisioned by another checkout answers about a different release.

    Resolved from `PATH` rather than from a path spelled here, because every recipe
    that reaches this runs under `uv run`, which puts this checkout's own `.venv/bin`
    first — the destination `scripts/session-setup.sh` installs the pinned release into
    and `scripts/onetaskgraph-install.sh` heals.
    """
    binary = shutil.which(STORE)
    if binary is None:
        raise OSError(f"{STORE} is not installed on PATH")
    return binary


def staged_name(qualified_id: QualifiedDocumentId) -> str:
    """What one document's staged copy is called inside the staging source.

    A digest of the identity rather than the identity itself, and both halves of that
    matter. It is **derived** because the store's own id is another program's answer about
    another program's records, and interpolating one into a path is how a separator or a
    `..` in it reaches the filesystem. It is **per document** because the copy that writes
    the record leaves its own origin on the destination, and every document staged under
    one name would then carry one correspondence between them — so the next document
    written would match the first one's record and land on it. It is **stable** because a
    second write of the same document should find the record the first one left.
    """
    return hashlib.sha256(qualified_id.encode("utf-8")).hexdigest()


def store_environment() -> dict[str, str]:
    """The environment a store command runs under: this process's, less :data:`BIN_ENV`."""
    environment = dict(os.environ)
    environment.pop(BIN_ENV, None)
    return environment


# llmlint: ignore[suppressions_justified] Open CLI JSON; consumed fields narrow at each caller.
def store_json(arguments: Sequence[str]) -> dict[str, Any]:
    """Read one JSON answer from the installed store CLI."""
    binary = store_binary()
    read = subprocess.run(
        [binary, *arguments, "--json"],
        cwd=REPO_ROOT,
        env=store_environment(),
        text=True,
        capture_output=True,
        check=False,
    )
    if read.returncode != 0:
        raise OSError(read.stderr.strip() or f"{STORE} exited {read.returncode}")
    try:
        payload = json.loads(read.stdout)
    except json.JSONDecodeError as exc:
        raise OSError(f"{STORE} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise OSError(f"{STORE} returned a non-object response")
    return payload


# llmlint: ignore[suppressions_justified] Store values stay open until validated here.
def one_item(payload: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    """Require one store item and return its typed payload mapping."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or len(items) != 1:
        raise OSError(
            f"{STORE} returned {len(items) if isinstance(items, list) else 0} {kind} records"
        )
    record = items[0]
    item = record.get("item") if isinstance(record, dict) else None
    if not isinstance(item, dict):
        raise OSError(f"{STORE} returned a {kind} without an object payload")
    return item


def qualified(project: str) -> tuple[str, str]:
    """``project`` split into its source and native id, refused when it is neither."""
    source, separator, native = project.partition(":")
    if not separator:
        raise OSError("a project id must be qualified as <source>:<native>")
    if not source or not native:
        raise OSError("a qualified project id must contain both <source> and <native> components")
    return source, native


def paged(arguments: Sequence[str], kind: str) -> list[Any]:
    """Every item the store lists for ``arguments``, following its own paging to the end.

    One walk for every listing this module makes rather than one per record kind: the
    paging contract — a `next` token that is a non-empty string, is not one already
    followed, and is absent on the last page — is the store's, and a second copy of it
    would be a second reading of somebody else's protocol. ``kind`` names the records for
    the refusals, which is the only thing that differs between callers.

    A repeated token is refused rather than followed, because a source that hands back a
    cursor that does not advance is one this would otherwise walk forever.
    """
    listed: list[Any] = []
    page: str | None = None
    seen_pages: set[str] = set()
    while True:
        asked = [*arguments, "--limit", str(PAGE_SIZE)]
        if page is not None:
            asked.extend(["--page", page])
        answer = store_json(asked)
        items = answer.get("items")
        if not isinstance(items, list):
            raise OSError(f"{STORE} returned a {kind} listing that is not a list")
        listed.extend(items)
        following = answer.get("next")
        if following is None:
            return listed
        if not isinstance(following, str) or not following:
            raise OSError(f"{STORE} returned an invalid next-page token")
        if following in seen_pages:
            raise OSError(f"{STORE} returned a repeated next-page token")
        seen_pages.add(following)
        page = following


def read_tasks(project: str) -> list[StoreTask]:
    """Every task of ``project``, validated, and each carrying the node ids it depends on."""
    source, native = qualified(project)
    listed = paged(["task", "list", "--source", source, "--project", native], "task")
    return _with_dependencies([_record(item) for item in listed])


# llmlint: ignore[suppressions_justified] The item payload is open; every field read is checked.
def _record(item: object) -> StoreTask:
    """One listed task, validated down to the fields a plan and a review key read."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        raise OSError(f"{STORE} returned a task without a qualified id")
    payload = item.get("item")
    if not isinstance(payload, dict):
        raise OSError(f"{STORE} returned a task without an object payload")
    metadata = payload.get("metadata", {})
    node_id = metadata.get("onepipeline.id") if isinstance(metadata, dict) else None
    if not isinstance(node_id, str):
        raise OSError(f"task {item['id']} has no string onepipeline.id")
    repositories = payload.get("repositories", [])
    if not isinstance(repositories, list):
        raise OSError(f"task {item['id']} has non-list repositories")
    if not all(isinstance(repository, str) for repository in repositories):
        raise OSError(f"task {item['id']} has a non-string repository")
    if len(repositories) > 1:
        raise OSError(f"task {item['id']} has more than one repository")
    title = payload.get("title")
    content = payload.get("content")
    if not isinstance(title, str) or (content is not None and not isinstance(content, str)):
        raise OSError(f"task {item['id']} has invalid title or content")
    return StoreTask(
        qualified_id=QualifiedTaskId(item["id"]),
        node_id=NodeId(node_id),
        title=title,
        content=content,
        metadata=metadata,
        repositories=repositories,
        deps=(),
    )


def _with_dependencies(records: list[StoreTask]) -> list[StoreTask]:
    """``records`` with each one's dependency edges resolved to node ids."""
    ids: dict[QualifiedTaskId, NodeId] = {}
    node_ids: set[NodeId] = set()
    for record in records:
        if record.qualified_id in ids or record.node_id in node_ids:
            raise OSError(
                f"{STORE} returned duplicate task identity {record.qualified_id!r} or "
                f"onepipeline.id {record.node_id!r}"
            )
        ids[record.qualified_id] = record.node_id
        node_ids.add(record.node_id)
    resolved: list[StoreTask] = []
    for record in records:
        edges = store_json(["task", "deps", record.qualified_id]).get("items")
        if not isinstance(edges, list):
            raise OSError(f"{STORE} returned non-list dependencies for {record.qualified_id}")
        targets: list[QualifiedTaskId] = []
        for edge in edges:
            match edge:
                case {"to": {"id": str(target_id)}}:
                    targets.append(QualifiedTaskId(target_id))
                case _:
                    raise OSError(
                        "onetaskgraph returned a dependency edge without a string target id "
                        f"for {record.qualified_id}"
                    )
        unknown = [target for target in targets if target not in ids]
        if unknown:
            rewritten = any(target.startswith(f"{WRITE_BACK_SOURCE}:") for target in unknown)
            raise OSError(
                f"{STORE} returned unknown dependency targets for "
                f"{record.qualified_id}: {', '.join(unknown)}"
                + (f" — {WRITE_BACK_REWROTE}" if rewritten else "")
            )
        resolved.append(
            StoreTask(
                qualified_id=record.qualified_id,
                node_id=record.node_id,
                title=record.title,
                content=record.content,
                metadata=record.metadata,
                repositories=record.repositories,
                deps=tuple(ids[target] for target in targets),
            )
        )
    return resolved


# llmlint: ignore[suppressions_justified] Engine plan metadata is an open contract.
def read_plan(project: str, records: Sequence[StoreTask]) -> dict[str, Any]:
    """Map a qualified store project and its already-read tasks onto the engine's plan."""
    held = one_item(store_json(["project", "show", project]), "project")
    metadata = held.get("metadata", {})
    if not isinstance(metadata, dict):
        raise OSError(f"{STORE} returned project metadata that is not an object")
    project_title = held.get("title")
    if not isinstance(project_title, str):
        raise OSError(f"{STORE} returned a project without a string title")
    plan = {
        key.removeprefix("onepipeline."): value
        for key, value in metadata.items()
        if isinstance(key, str) and key.startswith("onepipeline.")
    }
    plan.setdefault("name", project_title)
    # llmlint: ignore[suppressions_justified] Nodes include open validated metadata.
    nodes: list[dict[str, Any]] = []
    for record in records:
        node = {
            key.removeprefix("onepipeline."): value
            for key, value in record.metadata.items()
            if isinstance(key, str) and key.startswith("onepipeline.")
        }
        node["title"] = record.title
        node["task"] = record.content
        if record.repositories:
            node["repo"] = record.repositories[0]
        if record.deps:
            node["deps"] = list(record.deps)
        nodes.append(node)
    plan["tasks"] = nodes
    return plan


def read_project(project: str) -> tuple[dict[str, Any], list[StoreTask]]:
    """One qualified project as the plan the engine reads, beside the records it came from.

    Both halves in one call because both callers need both and each half costs its own
    walk of the store: `just check-plan` reads the plan to check its nodes and the
    records to check what has reviewed them, and asking twice would double every
    listing and every dependency query a plan makes.
    """
    records = read_tasks(project)
    return read_plan(project, records), records


@dataclass(frozen=True)
class StoreDocument:
    """A validated document record returned by onetaskgraph.

    Every field the store reports that a write may carry back, because
    :func:`write_document_metadata` replaces the record whole: a field read and not
    staged is a field the write deletes.
    """

    #: `<source>:<native>`, held to that shape where the store's answer is read. The
    #: whole identity and nothing beside it: the store also reports the native half on
    #: its own, and two identities that can disagree is one a record could be staged
    #: under while being addressed by the other.
    qualified_id: QualifiedDocumentId
    title: str
    content: str
    project: str | None
    labels: list[str]
    repositories: list[str]
    metadata: Mapping[str, object]
    #: Where the store says this record is — a path for a directory, a link for a
    #: board — reported back rather than composed. `None` when the store reports none.
    location: Mapping[str, Any] | None


def read_documents(project: str) -> list[StoreDocument]:
    """Every document of ``project``, validated, in the order the store lists them.

    Addressed by the qualified project id alone, which narrows the query to that
    project's own source: a bare native id is asked of every configured source, and a
    second source holding a project of the same name would answer for it.
    """
    qualified(project)
    listed = paged(["document", "list", "--project", project], "document")
    return [_document(item) for item in listed]


# llmlint: ignore[suppressions_justified] The item payload is open; every field read is checked.
def _document(item: object) -> StoreDocument:
    """One listed document, validated down to the fields a record is keyed and staged from.

    The identity is held to a **qualified** `<source>:<native>` here rather than wherever
    it is next used, because that is what makes :data:`QualifiedDocumentId` mean what its
    name says: the write below takes one and has to name the source it copies into, and a
    type whose values are only sometimes qualified pushes that check onto every caller.
    """
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        raise OSError(f"{STORE} returned a document without a qualified id")
    source, separator, native = item["id"].partition(":")
    if not separator or not source or not native:
        raise OSError(
            f"{STORE} addressed a document as {item['id']!r}, which is not a qualified "
            f"`<source>:<native>` id, so there is no source to write a record back into"
        )
    payload = item.get("item")
    if not isinstance(payload, dict):
        raise OSError(f"{STORE} returned a document without an object payload")
    title = payload.get("title")
    content = payload.get("content")
    project = payload.get("project")
    labels = payload.get("labels", [])
    repositories = payload.get("repositories", [])
    metadata = payload.get("metadata", {})
    location = payload.get("location")
    if not isinstance(title, str):
        raise OSError(f"document {item['id']} has no string title")
    if content is not None and not isinstance(content, str):
        raise OSError(f"document {item['id']} has non-string content")
    if project is not None and not isinstance(project, str):
        raise OSError(f"document {item['id']} has a non-string project")
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise OSError(f"document {item['id']} has labels that are not a list of strings")
    if not isinstance(repositories, list) or not all(
        isinstance(repository, str) for repository in repositories
    ):
        raise OSError(f"document {item['id']} has repositories that are not a list of strings")
    if not isinstance(metadata, dict):
        raise OSError(f"document {item['id']} has metadata that is not an object")
    if location is not None and not isinstance(location, dict):
        raise OSError(f"document {item['id']} has a location that is not an object")
    return StoreDocument(
        qualified_id=QualifiedDocumentId(item["id"]),
        title=title,
        content=content or "",
        project=project,
        labels=labels,
        repositories=repositories,
        metadata=metadata,
        location=location,
    )


def located(location: Mapping[str, Any] | None, fallback: str) -> str:
    """Where the store says a record is, in the form the store reports it.

    A link where the store puts it on a website, a path where it puts it in a file on
    this machine, and ``fallback`` — the record's own qualified id — when the store
    reports neither. **Never a location composed here**, which is the same rule the
    design document's own planned-tasks table follows: a destination decides where its
    records live, and a path or a URL assembled from a project name is one that names
    nothing the moment the destination is a board rather than a directory.

    One renderer for every record kind, because the question is the store's answer
    rather than the record's: a project, a task and a document are all reported with the
    same `location` object, and a second reading of it would answer differently the day
    the store grows a third form.
    """
    held = location or {}
    for form in ("url", "path"):
        answered = held.get(form)
        if isinstance(answered, str) and answered:
            return answered
    return fallback


@dataclass(frozen=True)
class StoreProject:
    """A validated project record returned by onetaskgraph.

    Narrower than :class:`StoreDocument` deliberately: nothing writes a project record
    back, so this carries only what a reader asks a project — where it is, and what it
    was copied from.
    """

    #: `<source>:<native>`, held to that shape where the store's answer is read.
    qualified_id: QualifiedProjectId
    title: str
    metadata: Mapping[str, object]
    #: Where the store says this record is, reported back rather than composed. `None`
    #: when the store reports none.
    location: Mapping[str, Any] | None


def read_projects(source: str) -> list[StoreProject]:
    """Every project ``source`` holds, validated, in the order the store lists them.

    Narrowed to one source rather than asked of every configured one, because the caller
    is asking which record a copy landed on in a named destination — and a second source
    holding a project copied from the same origin would answer for it.
    """
    return [_project(item) for item in paged(["project", "list", "--source", source], "project")]


# llmlint: ignore[suppressions_justified] The item payload is open; every field read is checked.
def _project(item: object) -> StoreProject:
    """One listed project, validated down to the fields a reader locates a copy by.

    The identity is held to a **qualified** `<source>:<native>` here rather than wherever
    it is next used, for the reason :func:`_document` gives: that is what makes
    :data:`QualifiedProjectId` mean what its name says. It is not only naming here — a
    caller reports this id as where the destination holds a plan when the store says
    nothing about its location, and an unqualified one addresses a project in no store.
    """
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        raise OSError(f"{STORE} returned a project without a qualified id")
    source, separator, native = item["id"].partition(":")
    if not separator or not source or not native:
        raise OSError(
            f"{STORE} addressed a project as {item['id']!r}, which is not a qualified "
            f"`<source>:<native>` id, so nothing can be asked of the store about it"
        )
    payload = item.get("item")
    if not isinstance(payload, dict):
        raise OSError(f"{STORE} returned a project without an object payload")
    title = payload.get("title")
    metadata = payload.get("metadata", {})
    location = payload.get("location")
    if not isinstance(title, str):
        raise OSError(f"project {item['id']} has no string title")
    if not isinstance(metadata, dict):
        raise OSError(f"project {item['id']} has metadata that is not an object")
    if location is not None and not isinstance(location, dict):
        raise OSError(f"project {item['id']} has a location that is not an object")
    return StoreProject(
        qualified_id=QualifiedProjectId(item["id"]),
        title=title,
        metadata=metadata,
        location=location,
    )


def write_document_metadata(document: StoreDocument, key: str, value: object) -> None:
    """Set one namespaced metadata entry of ``document``, wherever its store keeps it.

    The record is staged whole — every field :class:`StoreDocument` carries — into a
    local Markdown source of this call's own, and copied over the record it was read
    from. The copy is the store's own write verb, so a board takes this write exactly as
    a directory does; the module docstring states the two costs that come with it.

    The destination the store reports is checked against the document this was asked
    about, because the correspondence is matched by title: a second document of that
    title would be updated silently, and a record written over the wrong document reads
    as sound from every side afterwards.

    ``document`` is one :func:`read_documents` returned, and that is where its identity
    was held to a qualified `<source>:<native>` — the source half is what this copies
    into, and it is the only half that reaches anything here. The native half is the
    store's own answer about its own records and is never interpolated into a path; see
    :func:`staged_name`.
    """
    # Qualified by construction: `_document` refuses an identity that is not, which is
    # what makes this partition a read of the source half rather than a second check.
    source = document.qualified_id.partition(":")[0]
    fields: dict[str, object] = {"title": document.title}
    if document.project is not None:
        fields["project"] = document.project
    if document.labels:
        fields["labels"] = document.labels
    if document.repositories:
        fields["repositories"] = document.repositories
    fields["metadata"] = dict(document.metadata) | {key: value}
    name = staged_name(document.qualified_id)
    with tempfile.TemporaryDirectory(prefix="ai-orchestrator-record-") as staging:
        staged = Path(staging) / "documents"
        staged.mkdir(parents=True)
        (staged / f"{name}.md").write_text(frontmatter(fields, document.content), encoding="utf-8")
        answer = store_json(
            [
                "--set",
                f"sources.{STAGING_SOURCE}.plugin={WRITABLE_PLUGIN}",
                "--set",
                f"sources.{STAGING_SOURCE}.config.root={staging}",
                "document",
                "copy",
                f"{STAGING_SOURCE}:{name}",
                "--to",
                source,
                "--match-by",
                MATCH_BY,
            ]
        )
    written = answer.get("items")
    if not isinstance(written, list) or len(written) != 1 or not isinstance(written[0], dict):
        raise OSError(
            f"{STORE} reported {len(written) if isinstance(written, list) else 0} copied "
            f"records for {document.qualified_id}, so what it wrote cannot be told"
        )
    landed = written[0].get("destination")
    if landed != document.qualified_id:
        raise OSError(
            f"{STORE} wrote the record onto {landed!r} rather than onto "
            f"{document.qualified_id!r}; the two share a title and the write was matched "
            f"by {MATCH_BY}, so leave one of them a title of its own and run this again"
        )


def project_record(project: str) -> Mapping[str, Any]:
    """One qualified project's own record, as the store reports it.

    Distinct from :func:`read_plan`, which keeps the `onepipeline.`-prefixed metadata
    and drops everything else: what a *project* says about itself — that it is the plan
    a planning launch is writing, say — is not a plan field and would be dropped there.
    """
    return one_item(store_json(["project", "show", project]), "project")


def source_root(source: str) -> Path:
    """The directory ``source`` stores its records in, when it is one this may write.

    Resolved through the CLI's own `config show` rather than by reading
    `onetaskgraph.yaml` here: the configuration layers a file under environment
    variables under flags, and a second reader of the file alone would answer for a
    layer nothing runs at.
    """
    settings = store_json(["config", "show"]).get("settings")
    if not isinstance(settings, list):
        raise OSError(f"{STORE} returned a configuration without a settings list")
    values = {
        setting["key"]: setting.get("value")
        for setting in settings
        if isinstance(setting, dict) and isinstance(setting.get("key"), str)
    }
    plugin = values.get(f"sources.{source}.plugin")
    root = values.get(f"sources.{source}.config.root")
    if plugin != WRITABLE_PLUGIN:
        raise OSError(
            f"source {source!r} is a {plugin!r} source, and a review record is only ever "
            f"written into a {WRITABLE_PLUGIN!r} one"
        )
    if not isinstance(root, str) or not root:
        raise OSError(f"source {source!r} names no root directory")
    # Refused here rather than left to the first syscall that touches it: a NUL is the
    # one character `Path` accepts and every filesystem call then rejects with
    # `ValueError`, which is outside the `OSError` every caller of this reads — so a
    # launcher would report a traceback where it promised a sentence.
    if "\0" in root:
        raise OSError(
            f"source {source!r} names a root containing a NUL character, which no "
            f"filesystem path can hold"
        )
    held = Path(root)
    return held if held.is_absolute() else REPO_ROOT / held


def ensure_writable_source_root(source: str) -> Path:
    """:func:`source_root`, made into a directory records can actually be written into.

    `source_root` answers what the configuration *says*; this answers whether a plan
    could be authored there, and is what a planning launch resolves before it writes
    anything. A root that does not exist yet is the ordinary state of a host that has
    never planned — the first launch makes one — so it is created rather than refused,
    and what is refused is a path that is not a directory, one that cannot be made, and
    one this process may not write into.

    Every refusal names the source and the path, because the caller is a launcher whose
    operator has to repair one of the two.
    """
    root = source_root(source)
    if root.exists() and not root.is_dir():
        raise OSError(
            f"source {source!r} is rooted at {root}, which is not a directory, so no plan "
            f"can be stored there"
        )
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"source {source!r} is rooted at {root}, which could not be created: {exc}"
        ) from exc
    if not os.access(root, os.W_OK | os.X_OK):
        raise OSError(
            f"source {source!r} is rooted at {root}, which this process may not write into"
        )
    return root


def task_document(source: str, native_task_id: str) -> Path:
    """The Markdown file holding one task of a local Markdown source.

    A local task's native id is `<project>/<task>`, which is also its path below the
    root's `tasks/` directory — the layout `orchestrator/project_store.py` writes and
    `examples/tasks/` ships.

    Both components are held to :data:`RECORD_COMPONENT` rather than merely to "not
    empty and not nested". The id arrives from the store, which is a plugin this
    repository does not own answering about a directory this one writes into, so a
    component of `..` would name a record outside the project — and the caller is about
    to edit whatever this returns.
    """
    project, separator, task = native_task_id.partition("/")
    if (
        not separator
        or not RECORD_COMPONENT.fullmatch(project)
        or not RECORD_COMPONENT.fullmatch(task)
    ):
        raise OSError(
            f"task id {native_task_id!r} is not a local record's `<project>/<task>`, so the "
            f"document holding it cannot be named"
        )
    document = source_root(source) / "tasks" / project / f"{task}.md"
    if not document.is_file():
        raise OSError(f"task {source}:{native_task_id} has no record at {document}")
    return document


#: What a project or task component of a local record's id may be. Deliberately an
#: allowlist: this decides a path a caller then writes to, and `.` and `..` are the two
#: values that would carry that write out of the project it names.
RECORD_COMPONENT = re.compile(r"(?!\.+$)[\w.@+-]+")


_FENCE = "---"
_METADATA_OPEN = re.compile(r"^metadata:\s*$")
#: One entry of a `metadata` block, in either rendering this host produces. This
#: module and `orchestrator/project_store.py` write a JSON-quoted key; the plan
#: store's own renderer, and the settlement write-back that goes through it, write
#: a plain YAML key. Both parse to the same key, so a record written by one has to
#: be editable by the other — a writer that refused the store's own rendering left
#: every review record unwritable after a run settled onto the plan it launched from.
_METADATA_ENTRY = re.compile(r'^\s+(?:"(?P<quoted>[^"]*)"|(?P<plain>[A-Za-z_][A-Za-z0-9_.-]*)): \S')
_INDENTED = re.compile(r"^\s+\S")


def _closing_fence(lines: Sequence[str]) -> int:
    """The index of the fence closing this document's frontmatter."""
    if not lines or lines[0].strip() != _FENCE:
        raise OSError("the record does not open with a `---` frontmatter fence")
    for index in range(1, len(lines)):
        if lines[index].strip() == _FENCE:
            return index
    raise OSError("the record's frontmatter fence is never closed")


def write_metadata(document: Path, key: str, value: object) -> None:
    """Set one namespaced metadata entry of ``document``, leaving everything else alone.

    The entry is rendered the way `orchestrator/project_store.py` renders every other
    one — a JSON-quoted key and a JSON value, two spaces in — so a record this writes
    and a record that module wrote read back identically.
    """
    lines = document.read_text(encoding="utf-8").split("\n")
    closing = _closing_fence(lines)
    entry = f"  {json.dumps(key)}: {json.dumps(value)}"
    opened = [index for index in range(1, closing) if _METADATA_OPEN.match(lines[index])]
    if len(opened) > 1:
        raise OSError(
            f"the record opens `metadata` {len(opened)} times, so which block a review "
            f"record belongs in cannot be decided; leave it one block"
        )
    if not opened:
        if any(line.startswith("metadata:") for line in lines[1:closing]):
            raise OSError(
                "the record states `metadata` on one line; a review record is written as an "
                "indented entry, so re-render the record with its metadata as a block"
            )
        updated = [*lines[:closing], "metadata:", entry, *lines[closing:]]
    else:
        start = opened[0] + 1
        end = start
        while end < closing and _INDENTED.match(lines[end]):
            end += 1
        # The whole block is held to the one entry shape rather than only its leading
        # run, because anything else in it is a line this cannot account for — and the
        # cost of guessing is a second entry for a key already there, which is a
        # duplicate YAML key rather than a visible failure.
        unreadable = [line for line in lines[start:end] if not _METADATA_ENTRY.match(line)]
        if unreadable:
            raise OSError(
                f"the record's `metadata` block holds a line this cannot edit around: "
                f"{unreadable[0].strip()!r}; a review record is written beside entries of "
                f'the form `"<key>": <json>`, so re-render the record'
            )
        held = [line for line in lines[start:end] if _entry_key(line) != key]
        updated = [*lines[:start], *held, entry, *lines[end:]]
    _replace(document, "\n".join(updated))


def _entry_key(line: str) -> str:
    """The metadata key ``line`` states, which the caller has already matched."""
    matched = _METADATA_ENTRY.match(line)
    assert matched is not None
    quoted = matched["quoted"]
    return quoted if quoted is not None else matched["plain"]


def _replace(document: Path, content: str) -> None:
    """Write ``content`` over ``document`` without ever leaving a half-written record.

    A local Markdown source opens a project's task directory and reads every file in
    it, so a reader that arrives mid-write does not see a shorter file — it sees a
    record whose frontmatter is truncated, and refuses the whole walk.
    """
    handle, temporary = tempfile.mkstemp(dir=str(document.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as opened:
            opened.write(content)
    except OSError:  # pragma: no cover - the filesystem failing mid-write
        Path(temporary).unlink(missing_ok=True)
        raise
    os.replace(temporary, document)


def local_projects(source: str) -> list[str]:
    """Every project ``source`` holds, as qualified ids; none when it holds no root."""
    root = source_root(source) / "projects"
    if not root.is_dir():
        return []
    return sorted(f"{source}:{document.stem}" for document in root.glob("*.md"))
