"""Script the paid model's choices; every store, git and template operation stays real."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from orchestrator import design_approval, plan_budgets, plan_store, planned_tasks, spike_flow
from orchestrator.root import REPO_ROOT


def run(*args: str, timeout: float = 120) -> str:
    result = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=True)
    return result.stdout


def loader(name: str, scratch: Path) -> str:
    path = scratch / (name + "-loader.json")
    path.write_text(
        run(
            str(REPO_ROOT / ".venv/bin/onepipeline"),
            "template",
            "resolve",
            name,
            "--template-root",
            str(REPO_ROOT / "templates"),
            "--json",
        )
    )
    return str(path)


def project(native: str, goal: str) -> None:
    plan_store.sdk(
        plan_store.client().project_create(
            "authoring",
            native,
            native,
            metadata=[
                "onepipeline.schema_version=3",
                "onepipeline.goal=" + json.dumps({"text": goal}),
            ],
        )
    )


def task(
    project_id: str,
    node: str,
    title: str,
    repository: str,
    words: int,
    scratch: Path,
    *,
    preserve: bool = False,
) -> None:
    metadata = [
        "onepipeline.id=" + json.dumps(node),
        'onepipeline.persona="engineer"',
        'orchestrator.unit="Interfaces"',
        "orchestrator.budgets=[]",
    ]
    if preserve:
        metadata.append('onepipeline.publish="preserve"')
    what = f"Measure and compare the {node} interface. "
    padding = "Read real request outcomes and record which supported interface preserves behavior. "
    what += padding * (words // len(padding.split()) + 1)
    what = " ".join(what.split()[:words])
    plan_store.sdk(
        plan_store.client().task_create(
            "authoring",
            project_id,
            title,
            repository=[repository],
            template_loader=loader("plan-task", scratch),
            metadata=metadata,
            answers={
                "what": what,
                "why": "Reliable interface evidence prevents unnecessary implementation.",
                "acceptance_criteria": [
                    "The public interface and a rejected input are exercised.",
                    "Every finished-work claim is true of the final tree.",
                ],
                "additional_info": "Checks owed: exercise the interface and record its result.",
                "budgets": [],
            },
        )
    )


def draft(project_id: str, repository: str, scratch: Path, budget_answers: Path) -> None:
    if (scratch / "draft-written").exists():
        return
    project(project_id, "Choose interfaces from measurements before implementation")
    project(project_id + "-spikes", "Measure six actual candidate interfaces")
    for index in range(20):
        task(
            project_id,
            f"implement-{index}",
            f"feat({project_id}): implement interface {index}",
            repository,
            1500,
            scratch,
        )
    for index in range(6):
        task(
            project_id + "-spikes",
            f"spike-{project_id}-agent-{index}",
            f"feat({project_id}): measure interface {index}",
            repository,
            1000,
            scratch,
            preserve=True,
        )
    answers = cast(dict[str, JsonValue], json.loads(budget_answers.read_text()))
    plan_store.sdk(
        plan_store.client().project_create(
            "authoring",
            project_id,
            project_id,
            template_loader=loader("plan-description", scratch),
            answers=answers,
            metadata=[plan_budgets.PLAN_RECORD + "=" + json.dumps(answers)],
        )
    )
    (scratch / "draft-written").touch()


def spike(project_id: str, node: str, scratch: Path) -> None:
    (scratch / (node + "-started")).touch()
    if node.endswith("agent-5") and (scratch / "hold-spikes").exists():
        if (scratch / "ask-spikes").exists():
            (scratch / "question-reached").touch()
            # The journey's load-scaled reply window; this script cannot import its helper.
            window = os.environ.get("FAKE_BRIEF_ASK_WINDOW", "300")
            answer = run(
                os.environ["ORCHESTRATOR_ASK_MANAGER"],
                "--timeout",
                window,
                "Continue this request measurement?",
                # The ask bounds itself; a shorter kill here would end it before the reply.
                timeout=float(window) + 60,
            )
            (scratch / "question-answered").write_text(answer)
        wait(scratch / "spikes-release")
    node_scratch = scratch / node
    node_scratch.mkdir(exist_ok=True)
    artifact = Path(node + ".txt")
    artifact.write_text("A valid request and a rejected request were measured.\n")
    run("git", "add", str(artifact))
    run("git", "commit", "-m", "test: record " + node + " measurement")
    branch = run("git", "branch", "--show-current").strip()
    answers: dict[str, JsonValue] = {
        "spike": node,
        "branch": branch,
        "harness": str(artifact) + "; read its valid and rejected request results",
        "method": "Write and read the fixture's valid and rejected result samples.",
        "candidates": [],
        "findings": [],
        "visual_changes": [],
    }
    plan_store.sdk(
        plan_store.client().document_create(
            "authoring",
            project_id,
            "Measurement " + project_id + " " + node,
            id=node + "-report",
            template_loader=loader("spike-report", node_scratch),
            answers=answers,
        )
    )
    (scratch / (node + "-measured")).touch()


def brief(project_id: str, parent: str, scratch: Path) -> None:
    (scratch / "writer-reached").touch()
    wait(scratch / "writer-release")
    tasks = plan_store.read_tasks("authoring:" + project_id)
    agents = spike_flow.spike_nodes(
        plan_store.QualifiedProjectId("authoring:" + project_id + "-spikes")
    )
    (scratch / "source-read.json").write_text(
        json.dumps(
            {
                "tasks": [task.node_id for task in tasks],
                "spikes": agents,
            }
        )
    )
    answers: dict[str, JsonValue] = {
        "overview": "During planning, compare the candidate request interfaces. "
        "Measurements will establish which choices preserve valid and rejected behavior.",
        "spikes": [
            {
                "spike": agent,
                "question": "Which candidate preserves the valid request and rejected input? " * 6,
                "use": "Select the supported implementation from the measured behavior.",
            }
            for agent in agents
        ],
    }
    plan_store.sdk(
        plan_store.client().document_create(
            "authoring",
            project_id,
            "Spike brief " + parent,
            id=parent + "-spike-brief",
            template_loader=loader("spike-brief", scratch),
            answers=answers,
        )
    )
    (scratch / "brief-written").touch()


def finalize(project_id: str, parent: str, scratch: Path) -> None:
    (scratch / "finalize-reached").touch()
    branches = spike_flow.branches(spike_flow.RunId(parent + "-spikes"))
    for held in plan_store.read_tasks("authoring:" + project_id):
        stored = plan_store.sdk(plan_store.client().task_answers(str(held.qualified_id)))
        answers = cast(dict[str, JsonValue], stored.model_dump())
        answers["spikes"] = [
            {"spike": node, "report": node + "-report", "branch": branch}
            for node, branch in branches.items()
        ]
        plan_store.sdk(
            plan_store.client().task_render(
                str(held.qualified_id),
                template_loader=loader("plan-task", scratch),
                answers=answers,
            )
        )


def design(project_id: str, scratch: Path) -> None:
    (scratch / "design-reached").touch()
    wait(scratch / "design-release")
    qualified = "authoring:" + project_id
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
    answers = cast(
        dict[str, JsonValue],
        json.loads(
            run(str(REPO_ROOT / ".venv/bin/python"), "-m", "orchestrator.plan_budgets", qualified)
        ),
    )
    answers.update(
        {
            "what": "Implement supported request interfaces.",
            "why": "Measured behavior prevents unnecessary implementation.",
            "architecture": "The Interfaces unit owns the request boundary.",
            "units": [
                {
                    "name": "Interfaces",
                    "repository": "fixture",
                    "part": "",
                    "summary": "Request validation and response handling.",
                    "reversible": [],
                    "decisions": [],
                }
            ],
            "acceptance_criteria": ["Valid and rejected requests preserve their behavior."],
            "visual_changes": [],
            "planned_tasks": [
                {
                    "task": row["task"],
                    "unit": row["unit"],
                    "depends_on": row["depends_on"],
                    "location": row["location"],
                    "delivers": "a supported interface",
                }
                for row in rows
            ],
        }
    )
    plan_store.sdk(
        plan_store.client().document_create(
            "authoring",
            project_id,
            "Design " + project_id,
            id=project_id + "-design",
            template_loader=loader("design-doc", scratch),
            answers=answers,
        )
    )


def writer_preparation(project_id: str, parent: str, scratch: Path, *, repair: bool) -> None:
    writer = parent + "-spike-brief"
    if repair:
        held = plan_store.read_tasks("authoring:" + writer)[0]
        plan_store.sdk(
            plan_store.client().task_render(
                str(held.qualified_id),
                template_loader=loader("plan-task", scratch),
                answers={
                    "what": "Read local authoring project authoring:" + project_id,
                    "why": "Make the measurements visible.",
                    "acceptance_criteria": ["Write the local brief with six agent sections."],
                    "additional_info": "Write only local authoring records outside tracked files.",
                    "budgets": [],
                },
            )
        )
        return
    from orchestrator.project_store import write_plan_project

    write_plan_project(
        plan_store.source_root("authoring"),
        {
            "schema_version": 3,
            "name": writer,
            "goal": {"text": "Write the local spike brief"},
            "tasks": [
                {
                    "id": "spike-brief",
                    "persona": "../personas/spike-brief.yaml",
                    "agent_graph": "graphs/spike-brief.yaml",
                    "max_turns": 3,
                    "task": "Interrupted preparation omitted the required criteria.",
                }
            ],
        },
        native_id=writer,
        project_metadata={
            design_approval.PLAN_KIND: {"kind": "planning", "nodes": ["spike-brief"]}
        },
    )


def wait(path: Path) -> None:
    # A hang guard: a held writer spans the post-spike stages, so the journey scales it
    # with load. A dispatch strips PYTHONPATH, so this script cannot import that helper.
    deadline = time.monotonic() + float(os.environ.get("FAKE_BRIEF_RELEASE_WINDOW", "300"))
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"test never released {path}")
        time.sleep(0.05)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "verb",
        choices=[
            "draft",
            "spike",
            "brief",
            "finalize",
            "design",
            "prepare-writer",
            "repair-writer",
        ],
    )
    parser.add_argument("project")
    parser.add_argument("parent")
    parser.add_argument("scratch", type=Path)
    parser.add_argument("--repository")
    parser.add_argument("--budget-answers", type=Path)
    parser.add_argument("--node")
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    match args.verb:
        case "draft":
            draft(args.project, args.repository, args.scratch, args.budget_answers)
        case "spike":
            spike(args.project, args.node, args.scratch)
        case "brief":
            brief(args.project, args.parent, args.scratch)
        case "finalize":
            finalize(args.project, args.parent, args.scratch)
        case "design":
            design(args.project, args.scratch)
        case "prepare-writer" | "repair-writer":
            writer_preparation(
                args.project, args.parent, args.scratch, repair=args.verb == "repair-writer"
            )


if __name__ == "__main__":
    main()
