"""Script the paid model's choices in a screened export; every record and commit stays real.

Each verb is what one turn of the journey's model would have done, run where that turn runs:
the draft planner writes the one private coordinating plan into `authoring` through the store
and the engine's templates, the design-document writer renders the plan's document, the private
producer commits its generalized example, and the public consumer commits what it builds on it.
Nothing here writes a record by hand: the store, the templates and git do.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import cast

from export_plan import PlanSpec, TaskSpec
from pydantic import JsonValue

from orchestrator import npm_installs, plan_budgets, plan_store, planned_tasks
from orchestrator.root import REPO_ROOT

#: The unit every task of the plan is grouped under, which the design document's units name.
UNIT = "Listing"
#: The committer the scripted workers commit as: neutral, as any worker's identity is.
GIT_IDENTITY = ("-c", "user.email=worker@example.invalid", "-c", "user.name=Example Worker")


def run(*args: str, timeout: float = 120) -> str:
    result = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=True)
    return result.stdout


def loader(name: str, scratch: Path) -> str:
    """The engine's own resolution of template ``name``, as a file the store reads."""
    path = scratch / (name + "-loader.json")
    path.write_text(
        run(
            str(npm_installs.ENGINE),
            "template",
            "resolve",
            name,
            "--template-root",
            str(REPO_ROOT / "templates"),
            "--json",
        )
    )
    return str(path)


def _task(project: str, spec: TaskSpec, depends_on: list[str], scratch: Path) -> str:
    """Render one task of the plan through the `plan-task` template; answer its qualified id."""
    budgets = [asdict(budget) for budget in spec.budgets]
    metadata = [
        "onepipeline.id=" + json.dumps(spec.id),
        'onepipeline.persona="engineer"',
        "onepipeline.execution_checkout=" + json.dumps(spec.execution_checkout),
        "orchestrator.unit=" + json.dumps(UNIT),
        plan_budgets.TASK_RECORD + "=" + json.dumps(budgets),
    ]
    if spec.publish:
        metadata.append("onepipeline.publish=" + json.dumps(spec.publish))
    created = plan_store.sdk(
        plan_store.client().task_create(
            "authoring",
            project,
            spec.title,
            repository=[spec.repository],
            depends_on=depends_on,
            template_loader=loader("plan-task", scratch),
            metadata=metadata,
            answers={
                "what": spec.what,
                "why": spec.why,
                "acceptance_criteria": spec.criteria,
                "additional_info": "Checks owed: the tests over what this task changes.",
                "budgets": budgets,
                "boundary": {"mixed": True, "clearance": spec.clearance},
            },
        )
    )
    (held,) = created.items
    return str(held.id.root if hasattr(held.id, "root") else held.id)


def draft(plan_path: Path, scratch: Path) -> None:
    """Write the plan the journey hands the draft planner, as one project in `authoring`."""
    if (scratch / "draft-written").exists():
        return
    # The journey's own `plan-spec.json`, which its `_plan_spec` writes from this same model,
    # so a drift fails here, at the journey's first step, rather than passing unseen.
    plan = PlanSpec.model_validate_json(plan_path.read_text())
    edges = [edge.model_dump(by_alias=True) for edge in plan.export_edges]
    plan_store.sdk(
        plan_store.client().project_create(
            "authoring",
            plan.project,
            plan.title,
            # Classified as the private home it is, since a private task is never filed under
            # a project the store holds public; each task's own visibility is its own.
            classification="private",
            template_loader=loader("plan-description", scratch),
            answers=plan.budgets,
            metadata=[
                "onepipeline.schema_version=3",
                "onepipeline.goal=" + json.dumps({"text": plan.goal}),
                "onepipeline.export_edges=" + json.dumps(edges),
                plan_budgets.PLAN_RECORD + "=" + json.dumps(plan.budgets),
            ],
        )
    )
    created: dict[str, str] = {}
    for spec in plan.tasks:
        deps = [created[dep] for dep in spec.deps]
        created[spec.id] = _task(plan.project, spec, deps, scratch)
    (scratch / "draft-written").touch()


def design(project: str, scratch: Path, graph_root: Path) -> None:
    """Render the plan's design document, from the plan's own graph and budget answers,
    holding the graph image the flow rendered under ``graph_root`` as its task says to."""
    (image,) = sorted(graph_root.glob("finish-plan-graph.*/" + planned_tasks.IMAGE_NAME))
    qualified = "authoring:" + project
    graph = planned_tasks.parse_graph(
        json.loads(
            run(
                str(REPO_ROOT / ".venv/bin/onetaskgraph"),
                "project",
                "graph",
                qualified,
                "--group-by",
                "orchestrator.unit",
                "--format",
                "json",
            )
        )
    )
    rows = planned_tasks.rows(graph, planned_tasks.locations(qualified))
    # `orchestrator.plan_budgets` prints the design document's budget answers as one JSON
    # object, the shape its own `main` documents; the template render refuses anything else.
    answers = cast(
        dict[str, JsonValue],
        json.loads(
            run(str(REPO_ROOT / ".venv/bin/python"), "-m", "orchestrator.plan_budgets", qualified)
        ),
    )
    answers.update(
        {
            "what": "Page the listing by an opaque cursor, built on a generalized example.",
            "why": "Callers page long listings without skipping or repeating a record.",
            "architecture": "The Listing unit owns the cursor a caller pages by.",
            "units": [
                {
                    "name": UNIT,
                    "repository": "listing",
                    "part": "",
                    "summary": "The cursor a listing is paged by.",
                    "reversible": [],
                    "decisions": [],
                }
            ],
            "acceptance_criteria": ["A caller pages the whole listing by its opaque cursor."],
            "visual_changes": [],
            "dag_image": planned_tasks.IMAGE_NAME,
            "planned_tasks": [
                {
                    "task": row["task"],
                    "unit": row["unit"],
                    "depends_on": row["depends_on"],
                    "location": row["location"],
                    "delivers": "a cursor-paged listing",
                }
                for row in rows
            ],
        }
    )
    plan_store.sdk(
        plan_store.client().document_create(
            "authoring",
            project,
            "Design " + project,
            id=project + "-design",
            asset=[str(image)],
            template_loader=loader("design-doc", scratch),
            answers=answers,
        )
    )


def commit(path: str, content: str, message: str) -> None:
    """Write ``content`` at ``path`` in the turn's worktree and commit it under ``message``."""
    written = Path(path)
    if written.exists():
        return
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(content)
    run("git", "add", path)
    run("git", *GIT_IDENTITY, "commit", "-q", "-m", message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", choices=["draft", "design", "commit"])
    parser.add_argument("argument")
    parser.add_argument("scratch", type=Path)
    parser.add_argument("--content", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--graph-root", type=Path)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    try:
        _dispatch(args)
    except Exception:
        # The turn's own output is the stand-in's to discard, so a failure is kept where the
        # journey reads it back when it explains what never arrived.
        with (args.scratch / "failures.log").open("a") as failures:
            failures.write(f"{args.verb}:\n{traceback.format_exc()}\n")
        raise


def _dispatch(args: argparse.Namespace) -> None:
    match args.verb:
        case "draft":
            draft(Path(args.argument), args.scratch)
        case "design":
            design(args.argument, args.scratch, args.graph_root)
        case "commit":
            commit(args.argument, args.content, args.message)


if __name__ == "__main__":
    main()
