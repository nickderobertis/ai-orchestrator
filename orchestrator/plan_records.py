"""Create a generated plan's project and tasks through the pinned store's own verbs.

A plan this repository generates — a planning launch's one planner node, a follow-up
run's project, a suite fixture — becomes records by the store's `project create` and
`task create`, run through the locked SDK (`orchestrator.plan_store.client`), and never by
a file this repository writes: the store decides the record's layout, its id and its
provenance, so a record here is one the store recognises by construction.

The plan is onepipeline's plan document. Its fields map onto the records as the engine's
loader reads them back: the project carries every plan field under :data:`ENGINE_PREFIX`
beside the caller's own project metadata; each task carries its node id and every other
node field under the same prefix, its title, its body, its `repositories` for a normalized
origin (`onepipeline.repo` for any other spelling), its `delivers`, its own-plan
dependencies as store `depends_on` edges and its cross-DAG ones on :data:`CROSS_DAG_DEPS`.

The store names a created task from its title, so a task's id is the store's answer rather
than the node's; the node id travels on `onepipeline.id`, which is what the engine keys a
node by. Every write first retires each task record that names the project — the store
has no task removal verb — so a project written again never holds the previous pass's
nodes beside the new ones, and a task left by a write that failed before its project was
created is not adopted by the next one.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict

import yaml
from onetaskgraph_sdk._generated.global_id import GlobalId

from orchestrator import plan_store, plan_units
from orchestrator.record_shapes import (
    CROSS_DAG_DEPS,
    CROSS_DAG_REFERENCE_SHAPE,
    ENGINE_PREFIX,
    PROJECTS_DIRECTORY,
    TASKS_DIRECTORY,
    cross_dag_reference,
    hosted_origin,
    qualified_id,
)

#: The source a write names: a local Markdown source declared over the root the caller
#: names, for that one invocation, and private so nothing it writes is screened as a public
#: write. It is a name the configuration does not otherwise declare, so stating it never
#: changes a configured source.
SOURCE = "plan-records"


def _slug(value: str) -> str:
    rendered = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not rendered:
        raise ValueError("a project id must contain a lower-case letter or digit")
    return rendered


def _settings(root: Path) -> list[str]:
    return [
        f"sources.{SOURCE}.plugin=local-md",
        f"sources.{SOURCE}.config.root={root.resolve()}",
        f"sources.{SOURCE}.visibility=private",
    ]


class ValidatedPlan(NamedTuple):
    """A generated plan's name and its nodes, once their stable keys are held."""

    name: str
    nodes: list[dict[str, Any]]


class TaskCreate(TypedDict):
    """The fields one `task create` is asked for, as the SDK names them."""

    title: str
    body: str
    status: Literal["todo"]
    metadata: list[str]
    repository: NotRequired[list[str]]
    delivers: NotRequired[list[GlobalId | str]]


class PreparedTask(NamedTuple):
    """One node as `task create` is asked for it, and the ids of this plan it waits on."""

    keywords: TaskCreate
    deps: list[str]


def _metadata(fields: Mapping[str, object]) -> list[str]:
    return [f"{key}={json.dumps(value)}" for key, value in fields.items()]


# llmlint: ignore[suppressions_justified] A generated plan is onepipeline's open plan shape:
# the stable keys are validated below, and every other field is carried through untouched.
def _validated(
    plan: Mapping[str, Any], project_metadata: Mapping[str, object] | None
) -> ValidatedPlan:
    name = plan.get("name")
    tasks = plan.get("tasks")
    if not isinstance(name, str) or not isinstance(tasks, list):
        raise ValueError("a generated plan requires string `name` and list `tasks`")
    reserved = sorted(key for key in (project_metadata or {}) if key.startswith(ENGINE_PREFIX))
    if reserved:
        raise ValueError(
            f"project metadata may not name {ENGINE_PREFIX}-prefixed keys ({', '.join(reserved)}): "
            f"those are the plan's own fields, and a caller writing one would overwrite what "
            f"the plan states with something the engine never saw"
        )
    seen: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for raw in tasks:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise ValueError("every generated plan task requires a string `id`")
        node_id = raw["id"]
        if not node_id or node_id != node_id.strip() or not node_id.isprintable():
            raise ValueError(
                f"generated task id {node_id!r} is empty, carries surrounding whitespace or a "
                "control character; a node id is the engine's name for the node, so give it "
                "printable text with no leading or trailing space"
            )
        if raw["id"] in seen:
            raise ValueError(f"generated task id {raw['id']!r} is duplicate")
        seen.add(raw["id"])
        nodes.append(dict(raw))
    return ValidatedPlan(name, nodes)


def _task(node: dict[str, Any], ids: set[str]) -> PreparedTask:
    """The `task create` keywords for ``node``, and the ids of this plan it depends on."""
    node_id = node.pop("id")
    body = node.pop("task", "")
    title = node.pop("title", node_id)
    deps = node.pop("deps", [])
    repo = node.pop("repo", None)
    delivers = node.pop("delivers", [])
    unit = node.pop("unit", plan_units.ABSENT)
    if not isinstance(body, str) or not isinstance(title, str):
        raise ValueError(f"generated task {node_id!r} requires string title and task")
    if not isinstance(deps, list) or not all(isinstance(dependency, str) for dependency in deps):
        raise ValueError(f"generated task {node_id!r} requires a list of string dependencies")
    own = [dependency for dependency in deps if dependency in ids]
    cross_dag = [
        dependency
        for dependency in deps
        if dependency not in ids and cross_dag_reference(dependency) is not None
    ]
    unknown = [dependency for dependency in deps if dependency not in {*own, *cross_dag}]
    if unknown:
        raise ValueError(
            f"generated task {node_id!r} names dependencies that are neither a task of "
            f"this plan nor a cross-DAG reference of the form {CROSS_DAG_REFERENCE_SHAPE}: "
            f"{', '.join(unknown)}"
        )
    if not isinstance(delivers, list) or not all(
        isinstance(ticket, str) and qualified_id(ticket) for ticket in delivers
    ):
        raise ValueError(
            f"generated task {node_id!r} requires a list of qualified <source>:<native> "
            f"task ids in delivers, and has {delivers!r}"
        )
    # A record's body ends where its text does, whatever line breaks the plan's text
    # trailed: the engine reads a trailing break off a record's content where the store
    # keeps it, so only a body ending in its text is read alike by both.
    keywords: TaskCreate = {
        "title": title,
        "body": body.rstrip(),
        "status": "todo",
        "metadata": [],
    }
    if repo is not None:
        if not isinstance(repo, str):
            raise ValueError(f"generated task {node_id!r} has a non-string repository")
        # A normalized origin is the record's own `repositories`; any other spelling of a
        # repository travels on the reserved `onepipeline.repo` key the engine keeps for it.
        hosted = hosted_origin(repo)
        if hosted is not None:
            keywords["repository"] = [hosted]
        else:
            node["repo"] = repo
    if delivers:
        keywords["delivers"] = delivers
    metadata: dict[str, object] = {f"{ENGINE_PREFIX}id": node_id} | {
        f"{ENGINE_PREFIX}{key}": value for key, value in node.items()
    }
    if cross_dag:
        metadata[CROSS_DAG_DEPS] = cross_dag
    # The unit the task changes is this repository's own record under its own key, as a
    # planner's `task create --metadata` writes it; `orchestrator/plan_units.py` holds it.
    if unit is not plan_units.ABSENT:
        wrong = plan_units.problem(unit)
        if wrong is not None:
            raise ValueError(f"generated task {node_id!r} {wrong}")
        metadata[plan_units.UNIT_KEY] = unit
    keywords["metadata"] = _metadata(metadata)
    return PreparedTask(keywords, own)


def _dependency_order(prepared: Mapping[str, PreparedTask]) -> list[str]:
    """The node ids of ``prepared`` with every in-plan dependency ahead of its dependent."""
    order: list[str] = []
    waiting = list(prepared)
    while waiting:
        ready = [node for node in waiting if all(dep in order for dep in prepared[node].deps)]
        if not ready:
            raise ValueError(
                f"the generated tasks {', '.join(sorted(waiting))} depend on each other in a "
                f"cycle, which the store refuses to record"
            )
        order.extend(ready)
        waiting = [node for node in waiting if node not in ready]
    return order


def _filed_under(record: Path) -> object:
    """The `project` ``record``'s front matter names, or `None` where it states none.

    A record whose front matter does not parse, or that has none, names no project: it is
    not one this writer created, and it is left where it is.
    """
    text = record.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    front, closed, _ = text[4:].partition("\n---")
    try:
        fields = yaml.safe_load(front) if closed else None
    except yaml.YAMLError:
        return None
    return fields.get("project") if isinstance(fields, dict) else None


def _retire(root: Path, project: str) -> None:
    """Remove every task record under ``root`` whose front matter files it under ``project``.

    Read off each record's own front matter rather than through a store listing, because a
    listing parses every record in the root and refuses on one it cannot read — and a root
    a planner, a peer or a person also writes into is not this writer's to have whole.
    """
    tasks = root / TASKS_DIRECTORY
    for record in sorted(tasks.rglob("*.md")) if tasks.is_dir() else []:
        if record.is_file() and _filed_under(record) == project:
            record.unlink(missing_ok=True)


def _created(answer: object, title: str) -> str:
    """The native id of the one task ``answer`` reports created in :data:`SOURCE`."""
    items = getattr(answer, "items", [])
    qualified = [str(item.id.root) for item in items]
    if len(qualified) != 1 or not qualified[0].startswith(f"{SOURCE}:"):
        raise OSError(
            f"the store answered creating the task {title!r} with {qualified!r} rather than "
            f"the one task it created in {SOURCE}"
        )
    return qualified[0].removeprefix(f"{SOURCE}:")


# llmlint: ignore[suppressions_justified] This boundary preserves open engine metadata.
def write_plan_project(
    root: Path,
    plan: Mapping[str, Any],
    *,
    native_id: str | None = None,
    project_metadata: Mapping[str, object] | None = None,
) -> str:
    """Create ``plan`` under the local Markdown ``root`` through the store; answer its id.

    ``project_metadata`` is written onto the project beside the plan's own
    `onepipeline.` entries and is never read back as part of the plan: the engine keeps
    only the `onepipeline.`-prefixed keys, so it is a fact about the *project*.

    The tasks are created before the project, in dependency order so each names the ids
    the store gave the tasks it waits on: a reader that lists the project finds its tasks
    already there; a write that fails part-way takes back every task it created. A plan
    whose tasks depend on each other in a cycle has no such order, and the store refuses a
    cycle, so it is refused before anything is written.
    """
    name, nodes = _validated(plan, project_metadata)
    project = _slug(native_id or name)
    settings = _settings(root)
    client = plan_store.client()
    ids = {node["id"] for node in nodes}
    # Every node is validated before anything is retired or created, so a plan this refuses
    # changes nothing in the root.
    prepared = {node["id"]: _task(dict(node), ids) for node in nodes}
    order = _dependency_order(prepared)
    root.mkdir(parents=True, exist_ok=True)
    # Replaced whole: the store's `project create` keeps every metadata key of a project it
    # replaces that it is not handed, so a record a previous pass or its review left would
    # otherwise stand over a plan nobody reviewed.
    (root / PROJECTS_DIRECTORY / f"{project}.md").unlink(missing_ok=True)
    _retire(root, project)
    metadata = {
        f"{ENGINE_PREFIX}{key}": value
        for key, value in plan.items()
        if key not in {"name", "tasks"}
    } | dict(project_metadata or {})
    try:
        created: dict[str, str] = {}
        for node_id in order:
            task = prepared[node_id]
            answer = plan_store.complete(
                plan_store.sdk(
                    client.task_create(
                        SOURCE,
                        project,
                        depends_on=[f"{SOURCE}:{created[dep]}" for dep in task.deps],
                        set=settings,
                        **task.keywords,
                    )
                )
            )
            created[node_id] = _created(answer, task.keywords["title"])
        plan_store.complete(
            plan_store.sdk(
                client.project_create(
                    SOURCE,
                    project,
                    name,
                    body=f"Execution plan {project}.",
                    status="todo",
                    metadata=_metadata(metadata),
                    set=settings,
                )
            )
        )
    except BaseException:
        # A write that stopped part-way takes back the tasks it created, so no later reader
        # meets a project's tasks without the project that the write never finished.
        _retire(root, project)
        raise
    return project


# llmlint: ignore[changed_behavior_has_e2e] Every refusal below is over input
# `scripts/plan.sh` and `scripts/finish-plan.sh` cannot produce — each pipes a plan it
# generated and passes one literal metadata constant — so reaching one means driving this
# module rather than the recipe; `tests/test_plan_records.py` drives them there.
def main() -> int:
    """Read one generated plan on stdin and create its project below ROOT; print its id.

    The optional second argument is a JSON object of project metadata, which is how the
    planning recipes mark the project a planning launch writes as the planning project it
    is.
    """
    if len(sys.argv) not in (2, 3):
        print("usage: python -m orchestrator.plan_records ROOT [METADATA_JSON]", file=sys.stderr)
        return 2
    try:
        document = json.load(sys.stdin)
        metadata = json.loads(sys.argv[2]) if len(sys.argv) == 3 else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"plan-records: input is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(document, dict):
        print("plan-records: generated plan is not an object", file=sys.stderr)
        return 2
    if not isinstance(metadata, dict):
        print("plan-records: project metadata is not an object", file=sys.stderr)
        return 2
    try:
        project = write_plan_project(Path(sys.argv[1]), document, project_metadata=metadata)
    except (OSError, ValueError, RuntimeError) as exc:
        print(
            f"plan-records: the store could not create the generated plan: {exc}", file=sys.stderr
        )
        return 2
    print(project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
