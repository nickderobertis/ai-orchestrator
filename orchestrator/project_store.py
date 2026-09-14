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


#: What every field of the plan itself is namespaced under in a record's metadata. A
#: caller's own project metadata is written beside those and may never name one: the two
#: are merged, the caller's would win, and a project would then state a plan field that
#: no plan declared.
ENGINE_PREFIX = "onepipeline."

#: The two directories of a local-md root a record sits in: `<root>/projects/<project>.md`
#: and `<root>/tasks/<project>/<task>.md`. Named once here, where the layout is written,
#: because `orchestrator/plan_store.py` resolves a record to a path and a path back to its
#: root through the same two names.
PROJECTS_DIRECTORY = "projects"
TASKS_DIRECTORY = "tasks"

#: The shape of a normalized origin — `host/owner/name` — which is the one value a task
#: record's top-level `repositories` list can hold. The scheme and a `.git` suffix are
#: what a clone URL carries beyond it, so both are dropped before the shape is asked.
#: `onevcs` files a hosted repository under exactly this key, and its `resolve` answers
#: an identity's `origin` in it, so what this recognizes is what that CLI would answer.
#: Reconciled against both owners rather than trusted:
#: `tests/plan_tooling/test_check_plan_recipe_e2e.py` registers a real hosted scratch
#: identity, writes a record through this shape, and reads the node's `repo` back out of
#: the installed engine's own loader equal to the origin the installed `onevcs` answers.
#: Each segment is held to the characters a host name, an owner and a repository name
#: are made of, because the value is promoted into a record other programs then read:
#: whitespace, a query string or a control character in a segment is not an origin.
_HOSTED_ORIGIN = re.compile(r"[A-Za-z0-9.-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_CLONE_SCHEME = re.compile(r"^https?://")


def hosted_origin(repo: str) -> str | None:
    """``repo`` as the normalized origin `repositories` can hold, or ``None``.

    ``None`` for every other spelling of a repository — a registered alias, an absolute
    path, an scp-like clone URL — because none of them is a value that list holds: the
    engine reads a node's ``repo`` from that list's first entry and reserves the
    ``onepipeline.repo`` key for an identity a normalized origin cannot name, which is a
    local checkout `onevcs` knows by its path. This is the one place that shape is
    decided, and :mod:`orchestrator.publication_guard` asks it of what `onevcs` answers
    so the record a plan writes and the record `just check-plan` refuses agree.
    """
    normalized = _CLONE_SCHEME.sub("", repo).removesuffix(".git")
    return normalized if _HOSTED_ORIGIN.fullmatch(normalized) else None


def _slug(value: str) -> str:
    rendered = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not rendered:
        raise ValueError("a project id must contain a lower-case letter or digit")
    return rendered


def metadata_entry(key: str, value: object) -> str:
    """One entry of a record's `metadata` block, in the one shape this repository writes.

    Public, and the **only** renderer of that line, because the shape read and the shape
    written have to be one answer. Two existed: this rendering, reached whenever
    :func:`frontmatter` writes a whole record, and a copy of it in
    `orchestrator/plan_store.py`, reached when a review record is edited into a record
    that already exists. They were identical, which is what made them a hazard rather than
    a bug — the reader beside them accounts for the shapes it meets one by one, and a
    writer that drifted from it would produce records only the other writer could read.
    """
    return f"  {json.dumps(key)}: {json.dumps(value)}"


def frontmatter(fields: Mapping[str, object], body: str) -> str:
    """One local Markdown record: its frontmatter fields, then its body.

    Public because it is the *shape* of a record this repository writes, and a second
    renderer of that shape would be a second answer to one question: `plan_store` stages
    a document through it on its way back into whichever store the plan is held in, and
    a staged record that read back differently from a written one would make the record
    depend on which of the two produced it.

    A mapping-valued field is written as a block of :func:`metadata_entry` lines — the
    one such field a record here carries is its `metadata`.
    """
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(metadata_entry(name, held) for name, held in value.items())
        else:
            lines.append(f"{key}: {json.dumps(value)}")
    return "\n".join([*lines, "---", "", body.rstrip(), ""])


# llmlint: ignore[suppressions_justified, changed_behavior_has_e2e] Plan metadata is open
# and the stable keys narrow below. The reserved-prefix refusal beside them is a guard over
# an argument this repository composes itself: the one production caller is
# `scripts/plan.sh`, which passes the literal `PLANNING_PROJECT_METADATA` and nothing else,
# so a journey could only reach it by calling this module rather than the recipe — and the
# recipe is what `tests/e2e/test_plan_recipe_e2e.py` already drives, asserting that the
# marker it does pass reaches the record.
def render_plan_project(
    plan: Mapping[str, Any],
    *,
    native_id: str | None = None,
    project_metadata: Mapping[str, object] | None = None,
) -> dict[Path, str]:
    """Map a plan document onto the project/task records onepipeline 0.16 reads.

    ``project_metadata`` is written onto the project record beside the plan's own
    `onepipeline.` entries and is never read back as part of the plan: `read_plan`
    keeps only the `onepipeline.`-prefixed keys, so what a caller says here is a fact
    about the *project* rather than a field the engine's loader would then meet. That
    is the whole reason it is a separate argument — a marker folded into the plan
    document would come back out of the store as a plan field nothing declared.
    """
    name = plan.get("name")
    tasks = plan.get("tasks")
    if not isinstance(name, str) or not isinstance(tasks, list):
        raise ValueError("a generated plan requires string `name` and list `tasks`")
    # The cast records the validated stable keys while retaining arbitrary
    # onepipeline metadata, which is deliberately copied through below.
    document = cast(PlanDocument, plan)
    reserved = sorted(key for key in (project_metadata or {}) if key.startswith(ENGINE_PREFIX))
    if reserved:
        raise ValueError(
            f"project metadata may not name {ENGINE_PREFIX}-prefixed keys ({', '.join(reserved)}): "
            f"those are the plan's own fields, and a caller writing one would overwrite what "
            f"the plan states with something the engine never saw"
        )
    project = _slug(native_id or name)
    # llmlint: ignore[boundary_inputs_validated] Every caller of this module decodes its
    # plan with `json.load`/`json.loads` first — `main` from stdin, the suite's fixtures
    # from their own documents — so a metadata value here is a decoded JSON value and is
    # serializable by construction. The stable keys this rendering depends on are the ones
    # validated above and per task below; the rest is onepipeline's open contract, which
    # this boundary is required to carry through untouched.
    metadata = {
        f"{ENGINE_PREFIX}{key}": value
        for key, value in document.items()
        if key not in {"name", "tasks"}
    } | dict(project_metadata or {})
    rendered: dict[Path, str] = {}
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
            # The contract's two shapes, decided by one reader: a normalized origin is
            # the record's own `repositories`, and anything else travels on the reserved
            # `onepipeline.repo` key, which is what the engine keeps for an identity
            # that list cannot hold.
            hosted = hosted_origin(repo)
            if hosted is not None:
                fields["repositories"] = [hosted]
            else:
                node["repo"] = repo
        if deps:
            fields["depends_on"] = [f"{project}/{task_names[dependency]}" for dependency in deps]
        # llmlint: ignore[boundary_inputs_validated] Decoded JSON, for the reason stated
        # where the project's own metadata is built above.
        fields["metadata"] = {"onepipeline.id": node_id} | {
            f"onepipeline.{key}": value for key, value in node.items()
        }
        rendered[Path(TASKS_DIRECTORY) / project / f"{node_slug}.md"] = frontmatter(fields, body)
    # The project document is rendered last, and `write_plan_project` writes in this
    # order, because a root is read concurrently with being written: a local Markdown
    # source that finds a project document opens the task directory below it, and one
    # that is not there yet is a hard refusal rather than an empty project. So a project
    # that was not published before becomes visible only once its tasks are on disk.
    # Replacing one published already is weaker and cannot be otherwise without a second
    # root to swap in: its document stays visible while its task files are rewritten one
    # at a time, so a reader can catch a mixed record. What the order still buys there is
    # that a replacement which fails leaves the reader the project it already had.
    rendered[Path(PROJECTS_DIRECTORY) / f"{project}.md"] = frontmatter(
        {"title": name, "status": "todo", "metadata": metadata},
        f"Execution plan {project}.",
    )
    return rendered


# llmlint: ignore[suppressions_justified] This boundary preserves open engine metadata.
def write_plan_project(
    root: Path,
    plan: Mapping[str, Any],
    *,
    native_id: str | None = None,
    project_metadata: Mapping[str, object] | None = None,
) -> str:
    """Write a rendered project under one local-md root and return its native id."""
    rendered = render_plan_project(plan, native_id=native_id, project_metadata=project_metadata)
    document = next(path for path in rendered if path.parent == Path(PROJECTS_DIRECTORY))
    project = document.stem
    for relative, content in rendered.items():
        if relative == document:
            continue
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    task_directory = root / TASKS_DIRECTORY / project
    current_tasks = {
        root / path for path in rendered if path.parent == Path(TASKS_DIRECTORY) / project
    }
    if task_directory.is_dir():
        for existing in task_directory.iterdir():
            if existing.is_file() and existing not in current_tasks:
                existing.unlink()
    # Last, for the reason `render_plan_project` states: a project not published before
    # becomes visible only here, with its tasks already written, and a replacement that
    # never reached this line left the previously published document untouched.
    destination = root / document
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered[document], encoding="utf-8")
    return project


# llmlint: ignore[changed_behavior_has_e2e] Every refusal below is over input
# `scripts/plan.sh` cannot produce — it pipes a plan it generated and passes one literal
# metadata constant — so reaching one means driving this module instead of the recipe.
# `tests/test_project_store.py` drives them there, and the recipe's own path is driven end
# to end in `tests/e2e/test_plan_recipe_e2e.py`.
def main() -> int:
    """Read one generated plan on stdin and write its local-md project below ROOT.

    The optional second argument is a JSON object of project metadata, which is how
    `scripts/plan.sh` marks the project a planning launch writes as the planning
    project it is. It is an argument rather than part of the plan on stdin for the
    reason `render_plan_project` gives.
    """
    if len(sys.argv) not in (2, 3):
        print("usage: python -m orchestrator.project_store ROOT [METADATA_JSON]", file=sys.stderr)
        return 2
    try:
        document = json.load(sys.stdin)
        metadata = json.loads(sys.argv[2]) if len(sys.argv) == 3 else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"project-store: input is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(document, dict):
        print("project-store: generated plan is not an object", file=sys.stderr)
        return 2
    if not isinstance(metadata, dict):
        print("project-store: project metadata is not an object", file=sys.stderr)
        return 2
    try:
        project = write_plan_project(Path(sys.argv[1]), document, project_metadata=metadata)
    except (OSError, ValueError) as exc:
        print(f"project-store: cannot write generated plan: {exc}", file=sys.stderr)
        return 2
    print(project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
