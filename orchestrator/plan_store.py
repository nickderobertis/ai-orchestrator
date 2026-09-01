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
"""

# llmlint: ignore-file[changed_behavior_has_e2e] Every refusal below is a guard over
# another program's records, and the module docstring above states why no journey drives
# one: reaching it means leaving a malformed record in a plan root the suite's other
# tiers walk concurrently, which refuses the whole walk rather than that record. They are
# driven in tests/test_plan_store.py against a root that test owns.

from __future__ import annotations

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

from orchestrator.root import REPO_ROOT

#: The standalone plan-store CLI this host spawns. `config/onetaskgraph.version` pins
#: it; nothing here reads that pin, because the installed program is what answers and
#: a pin that disagreed with it would only mislead.
STORE = "onetaskgraph"

#: How many task records one listing page holds. Small on purpose: paging is a contract
#: this reader depends on, and a page size no plan ever exceeds would leave the second
#: page unread on every host until the first plan that needed it.
PAGE_SIZE = 2

#: The plugin whose records this module may write. Every other source is read-only
#: here — a GitHub Projects board is not a directory, and a record written into one
#: would go through an API this repository deliberately does not call.
WRITABLE_PLUGIN = "local-md"


#: A task's address in the store, `<source>:<native-id>` — what a store command is
#: given. Distinct from :data:`NodeId`, which is what a *plan* calls the same task and
#: what a dependency edge resolves to: the two are both strings, they travel together
#: through every function here, and mixing them addresses the wrong record.
QualifiedTaskId = NewType("QualifiedTaskId", str)

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


# llmlint: ignore[suppressions_justified] Open CLI JSON; consumed fields narrow at each caller.
def store_json(arguments: Sequence[str]) -> dict[str, Any]:
    """Read one JSON answer from the installed store CLI."""
    binary = store_binary()
    read = subprocess.run(
        [binary, *arguments, "--json"],
        cwd=REPO_ROOT,
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


def read_tasks(project: str) -> list[StoreTask]:
    """Every task of ``project``, validated, and each carrying the node ids it depends on."""
    source, native = qualified(project)
    listed: list[Any] = []
    page: str | None = None
    seen_pages: set[str] = set()
    while True:
        arguments = ["task", "list", "--source", source, "--project", native]
        arguments += ["--limit", str(PAGE_SIZE)]
        if page is not None:
            arguments.extend(["--page", page])
        answer = store_json(arguments)
        items = answer.get("items")
        if not isinstance(items, list):
            raise OSError(f"{STORE} returned a task listing that is not a list")
        listed.extend(items)
        following = answer.get("next")
        if following is None:
            break
        if not isinstance(following, str) or not following:
            raise OSError(f"{STORE} returned an invalid next-page token")
        if following in seen_pages:
            raise OSError(f"{STORE} returned a repeated next-page token")
        seen_pages.add(following)
        page = following
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
            raise OSError(
                f"{STORE} returned unknown dependency targets for "
                f"{record.qualified_id}: {', '.join(unknown)}"
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
    held = Path(root)
    return held if held.is_absolute() else REPO_ROOT / held


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
_METADATA_ENTRY = re.compile(r'^\s+"(?P<key>[^"]*)": \S')
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
    return matched["key"]


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
