"""What the doubled planner and design writer do: the store and engine commands a real one runs.

A planner's deliverable is plan-store records, and a design writer's a document; no program
turns a model's prose into either, so each doubled worker runs, in its own dispatch's
directory, exactly the commands the planning templates tell it to — `onepipeline template
resolve` piped into `onetaskgraph project create` / `task create` / `document create`. The
records the journeys read back are the store's own.
"""

from __future__ import annotations

import json
import shlex

from planning_bench import Bench, mark

#: Phrases only one planning task carries, so each doubled worker acts on its own task alone.
DRAFT_MARKER = "## Where the plan goes"
DESIGN_MARKER = "Write the design document a person reviews the plan"
SPIKE_BRIEF_MARKER = "spike brief a person approves before any spike runs"
SPIKE_MARKER = "SPIKE-WORK: measure the health route"

STORE = ".venv/bin/onetaskgraph"
RESOLVE = "scripts/onepipeline.sh template resolve"


def _task(plan: str, node: str, title: str, unit: str, *, after: str | None = None) -> str:
    """A bash line creating one plan-task rendering in ``plan``, echoing its id to a file."""
    depends = f' --depends-on "$(cat .plan-{after}.id)"' if after else ""
    return (
        f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {plan} "
        f"--title {shlex.quote(title)} --template-loader - --var 'what=Do {node}.' "
        f"--var 'why=Operators need it.' "
        f"--var 'acceptance_criteria=[\"A request-level test drives {node} end to end.\"]' "
        f"--metadata 'onepipeline.id=\"{node}\"' --metadata 'onepipeline.persona=\"engineer\"' "
        f"--metadata 'orchestrator.unit=\"{unit}\"'{depends} --no-interactive --json "
        f"| jq -r '.items[0].id' > .plan-{node}.id"
    )


def plan_commands(native: str) -> list[str]:
    """The draft's commands for a two-task plan `authoring:<native>`, as `just plan` asks."""
    plan = f"authoring:{native}"
    goal = json.dumps({"text": "Give the service a health route operators can poll"})
    return [
        f"{RESOLVE} plan-description --json | {STORE} project create authoring --id {native} "
        f"--title {shlex.quote('Health route ' + native)} --template-loader - "
        f"--var 'overview=Add a health route and document it.' "
        f"--metadata onepipeline.schema_version=3 "
        f"--metadata {shlex.quote('onepipeline.goal=' + goal)} "
        f"--no-interactive",
        _task(plan, "route", "feat: add the health route", "API"),
        _task(plan, "docs", "docs: document the health route", "Docs", after="route"),
        "rm -f .plan-route.id .plan-docs.id",
    ]


def design_commands(native: str) -> list[str]:
    """The design writer's commands: render the design document of `authoring:<native>`."""
    plan = f"authoring:{native}"
    return [
        f"{STORE} project graph {plan} --format json --group-by orchestrator.unit "
        f"--no-interactive > .design-graph.json",
        f"answers=(--var what=W --var why=Y --var architecture=A --var 'units=[]' "
        f"--var 'acceptance_criteria=[\"Operators can poll the route.\"]' "
        f'--var "graph=$(cat .design-graph.json)"); '
        f"if {STORE} document show authoring:{native}-design --no-interactive >/dev/null 2>&1; "
        f"then {RESOLVE} design-doc --json | {STORE} document render authoring:{native}-design "
        f'--template-loader - "${{answers[@]}}" --no-interactive; '
        f"else {RESOLVE} design-doc --json | {STORE} document create authoring --project {plan} "
        f"--id {native}-design --title 'Design: {native}' --template-loader - "
        f'"${{answers[@]}}" --no-interactive; fi',
        "rm -f .design-graph.json",
    ]


def spikes_commands(native: str, repo: str, execution: str) -> list[str]:
    """The draft's extra commands: `authoring:<native>-spikes` from its template, one spike."""
    plan = f"authoring:{native}"
    spikes = f"{plan}-spikes"
    publish = json.dumps("preserve")
    return [
        f"{STORE} project create authoring --id {native}-spikes --title {native}-spikes "
        f"--template templates/planning/spikes.md.j2 --var plan={plan} --var native={native} "
        f"--metadata onepipeline.schema_version=3 --no-interactive",
        f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {spikes} "
        f"--title 'spike: measure the health route' --template-loader - "
        f"--var 'what={SPIKE_MARKER}.' --var 'why=The plan needs the figure.' "
        f"--var 'acceptance_criteria=[\"The measurement is committed.\"]' "
        f"--metadata 'onepipeline.id=\"spike-health\"' "
        f"--metadata 'onepipeline.persona=\"engineer\"' "
        f"--metadata 'onepipeline.repo={json.dumps(repo)}' "
        f"--metadata 'onepipeline.execution_checkout={json.dumps(execution)}' "
        f"--metadata 'onepipeline.publish={publish}' "
        f"--depends-on authoring:{native}-spikes-approve --no-interactive --json "
        f"| jq -r '.items[0].id' > .spike.id",
        "rm -f .spike.id",
    ]


def spike_brief_commands(native: str) -> list[str]:
    """The spike-brief writer's command: the brief document of `authoring:<native>`."""
    spikes = json.dumps([{"spike": "spike-health", "question": "How fast?", "use": "Sizing."}])
    return [
        f"{RESOLVE} spike-brief --json | {STORE} document create authoring "
        f"--project authoring:{native} --id {native}-spike-brief --title 'Spikes: {native}' "
        f"--template-loader - --var 'overview=One measurement before planning.' "
        f"--var {shlex.quote('spikes=' + spikes)} --no-interactive",
    ]


def spike_work_commands() -> list[str]:
    """The spike worker's commands: commit one measurement in its own worktree."""
    return [
        "mkdir -p spikes && echo 'p50 2ms' > spikes/health.txt && git add spikes "
        "&& git -c user.email=t@example.com -c user.name=t commit -qm 'feat: measure the route'",
    ]


def drafting(bench: Bench, native: str, *, spikes: tuple[str, str] | None = None) -> None:
    """Script every planning worker of a run planning `authoring:<native>`."""
    draft = plan_commands(native)
    if spikes is not None:
        draft += spikes_commands(native, *spikes)
        mark(bench, SPIKE_BRIEF_MARKER, *spike_brief_commands(native))
        mark(bench, SPIKE_MARKER, *spike_work_commands())
    mark(bench, DRAFT_MARKER, *draft)
    mark(bench, DESIGN_MARKER, *design_commands(native))


def brief(bench: Bench, native: str) -> str:
    """A manager's brief naming the plan project, written into the bench; answers its path."""
    path = bench.tmp / f"{native}.brief.md"
    path.write_text(
        "## What\n\nGive the service a health route operators can poll.\n\n"
        "## Why\n\nOperators cannot tell whether the service is up.\n\n"
        f"Plan project: authoring:{native}\n",
        encoding="utf-8",
    )
    return str(path)
