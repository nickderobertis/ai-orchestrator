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
from pathlib import Path
from typing import NamedTuple

from planning_bench import Bench, mark

#: Phrases only one planning task carries, so each doubled worker acts on its own task alone.
DRAFT_MARKER = "## Where the plan goes"
DESIGN_MARKER = "Write the design document a person reviews the plan"
SPIKE_BRIEF_MARKER = "spike brief the manager relays while the spikes run beside you"
SPIKE_MARKER = "SPIKE-WORK: measure the health route"
FINALIZE_MARKER = "**Which tasks to rework**"

STORE = ".venv/bin/onetaskgraph"
RESOLVE = "scripts/onepipeline.sh template resolve"


def plan_title(native: str) -> str:
    """The title of `authoring:<native>`: its brief's H1, which the draft titles it with."""
    return f"Health route {native}"


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
        f"--title {shlex.quote(plan_title(native))} --template-loader - "
        f"--var 'overview=Add a health route and document it.' "
        f"--metadata onepipeline.schema_version=3 "
        f"--metadata {shlex.quote('onepipeline.goal=' + goal)} "
        f"--no-interactive",
        _task(plan, "route", "feat: add the health route", "API"),
        _task(plan, "docs", "docs: document the health route", "Docs", after="route"),
        "rm -f .plan-route.id .plan-docs.id",
    ]


def design_commands(native: str) -> list[str]:
    """The design writer's commands: render the design document of `authoring:<native>`.

    When the plan holds a `spike-visual` report, the document restates its visual changes,
    answer for answer, with the report's own images, as the design-doc template asks.
    """
    plan = f"authoring:{native}"
    report = f"authoring:{native}-spike-visual-report"
    return [
        f"{STORE} project graph {plan} --format json --group-by orchestrator.unit "
        f"--no-interactive > .design-graph.json",
        f"answers=(--var what=W --var why=Y --var architecture=A --var 'units=[]' "
        f"--var 'acceptance_criteria=[\"Operators can poll the route.\"]' "
        f'--var "graph=$(cat .design-graph.json)"); '
        f"if {STORE} document show {report} --no-interactive >/dev/null 2>&1; then "
        f'answers+=(--var "visual_changes=$({STORE} document answers {report} --json '
        f'--no-interactive | jq -c .visual_changes)"); '
        f'while IFS= read -r image; do answers+=(--asset "$image"); done < <({STORE} document '
        f"show {report} --json --no-interactive | jq -r '.assets[].path'); fi; "
        f"if {STORE} document show authoring:{native}-design --no-interactive >/dev/null 2>&1; "
        f"then {RESOLVE} design-doc --json | {STORE} document render authoring:{native}-design "
        f'--template-loader - "${{answers[@]}}" --no-interactive; '
        f"else {RESOLVE} design-doc --json | {STORE} document create authoring --project {plan} "
        f"--id {native}-design --title 'Design: {native}' --template-loader - "
        f'"${{answers[@]}}" --no-interactive; fi',
        "rm -f .design-graph.json",
    ]


class Spike(NamedTuple):
    """One spike a drafted plan may carry."""

    #: The phrase only its task carries, so its doubled worker acts on it alone.
    marker: str
    #: What it measures, in the few words the brief's check-in gives it.
    measures: str


#: Each spike a drafted plan may carry, by node id.
SPIKES = {
    "spike-health": Spike(SPIKE_MARKER, "how fast the health route answers"),
    "spike-load": Spike(
        "SPIKE-WORK: measure the route under load", "how the route holds under load"
    ),
    "spike-visual": Spike(
        "SPIKE-WORK: capture the route's status page", "what the route's status page shows"
    ),
}


def spike_what(spike: str, definition: str = "") -> str:
    """A spike task's `what` answer: its marker, then what else its definition asks."""
    return f"{SPIKES[spike].marker}.{f' {definition}' if definition else ''}"


#: A spike task's `why` and `acceptance_criteria` answers, which say nothing about reports.
SPIKE_WHY = "The plan needs the figure."
SPIKE_CRITERIA = '["The measurement is committed."]'


def _spike_task(spikes: str, spike: str, repo: str, execution: str, definition: str = "") -> str:
    """A bash line creating one lifecycle spike task in ``spikes``."""
    publish = json.dumps("preserve")
    what = shlex.quote(f"what={spike_what(spike, definition)}")
    return (
        f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {spikes} "
        f"--title 'spike: {spike}' --template-loader - "
        f"--var {what} --var 'why={SPIKE_WHY}' "
        f"--var 'acceptance_criteria={SPIKE_CRITERIA}' "
        f"--metadata 'onepipeline.id=\"{spike}\"' "
        f"--metadata 'onepipeline.persona=\"engineer\"' "
        f"--metadata 'onepipeline.repo={json.dumps(repo)}' "
        f"--metadata 'onepipeline.execution_checkout={json.dumps(execution)}' "
        f"--metadata 'onepipeline.publish={publish}' --no-interactive --json >/dev/null"
    )


def spikes_check_record(bench: Bench) -> Path:
    """Where the draft records its `just check-plan` of the spikes project it drafted:
    the recipe's output, then an `exit <status>` line."""
    return bench.tmp / "spikes-check.txt"


def spikes_commands(
    native: str,
    repo: str,
    execution: str,
    spikes: tuple[str, ...],
    definitions: dict[str, str] | None = None,
    *,
    checked: Path,
) -> list[str]:
    """The draft's extra commands: `authoring:<native>-spikes` from its template, its spikes,
    and the planner's `just check-plan` of that project, recorded at ``checked``.

    ``definitions`` adds to a spike's `what` what else its definition asks, by spike id. The
    check's refusal is recorded rather than failing the draft, so a journey reads it from
    ``checked`` beside everything else the run did.
    """
    plan = f"authoring:{native}"
    more = definitions or {}
    record = shlex.quote(str(checked))
    return [
        f"{STORE} project create authoring --id {native}-spikes "
        f"--title {shlex.quote(plan_title(native) + ' spikes')} "
        f"--template templates/planning/spikes.md.j2 --var plan={plan} --var native={native} "
        f"--metadata onepipeline.schema_version=3 --no-interactive",
        *(
            _spike_task(f"{plan}-spikes", spike, repo, execution, more.get(spike, ""))
            for spike in spikes
        ),
        f"just check-plan {plan}-spikes > {record} 2>&1 && echo 'exit 0' >> {record} "
        f'|| echo "exit $?" >> {record}',
    ]


def spike_brief_check_in(native: str, spikes: tuple[str, ...]) -> str:
    """The `check-in` text the spike-brief writer raises, in the shape its task states."""
    lines = [f"Spike brief {native}-spike-brief of authoring:{native}:"]
    lines += [f"- {spike}: {SPIKES[spike].measures}" for spike in spikes]
    return "\n".join(lines) + "\n"


def _released(hold: Path | None, name: str) -> list[str]:
    """A command waiting for ``hold``/``name`` to exist, or none when nothing holds."""
    if hold is None:
        return []
    return [f"until [ -e {shlex.quote(str(hold / name))} ]; do sleep 1; done"]


def spike_brief_commands(native: str, spikes: tuple[str, ...], hold: Path | None) -> list[str]:
    """The spike-brief writer's commands: the brief document, then its one check-in.

    Given ``hold``, the writer does not finish until `<hold>/spike-brief` exists.
    """
    entries = [{"spike": spike, "question": "How fast?", "use": "Sizing."} for spike in spikes]
    return [
        *_released(hold, "spike-brief"),
        f"{RESOLVE} spike-brief --json | {STORE} document create authoring "
        f"--project authoring:{native} --id {native}-spike-brief --title 'Spikes: {native}' "
        f"--template-loader - --var 'overview=One measurement before planning.' "
        f"--var {shlex.quote('spikes=' + json.dumps(entries))} --no-interactive",
        f"printf %s {shlex.quote(spike_brief_check_in(native, spikes))} | "
        'scripts/onepipeline.sh surface --kind check-in "$ONEPIPELINE_RUN_ID"',
    ]


def spike_work_commands(spike: str, hold: Path | None) -> list[str]:
    """A spike worker's commands: commit one measurement in its own worktree.

    Given ``hold``, every spike but the first does not finish until `<hold>/<spike>` exists.
    """
    return [
        *([] if spike == next(iter(SPIKES)) else _released(hold, spike)),
        f"mkdir -p spikes && echo 'p50 2ms' > spikes/{spike}.txt && git add spikes "
        "&& git -c user.email=t@example.com -c user.name=t commit -qm 'feat: measure the route'",
    ]


def drafting(
    bench: Bench,
    native: str,
    *,
    spikes: tuple[str, str] | None = None,
    spike_ids: tuple[str, ...] = ("spike-health",),
    hold: Path | None = None,
    definitions: dict[str, str] | None = None,
) -> None:
    """Script every planning worker of a run planning `authoring:<native>`.

    ``spikes`` names the repository and execution checkout of the spikes ``spike_ids`` the
    draft adds, ``definitions`` what else each one's definition asks; ``hold`` is a directory
    whose files release the held workers (see `spike_brief_commands` and
    `spike_work_commands`).
    """
    draft = plan_commands(native)
    if spikes is not None:
        draft += spikes_commands(
            native, *spikes, spike_ids, definitions, checked=spikes_check_record(bench)
        )
        mark(bench, SPIKE_BRIEF_MARKER, *spike_brief_commands(native, spike_ids, hold))
        for spike in spike_ids:
            mark(bench, SPIKES[spike].marker, *spike_work_commands(spike, hold))
    mark(bench, DRAFT_MARKER, *draft)
    mark(bench, DESIGN_MARKER, *design_commands(native))


def brief(bench: Bench, native: str) -> str:
    """A manager's brief naming the plan project, written into the bench; answers its path."""
    path = bench.tmp / f"{native}.brief.md"
    path.write_text(
        f"# {plan_title(native)}\n\n"
        "## What\n\nGive the service a health route operators can poll.\n\n"
        "## Why\n\nOperators cannot tell whether the service is up.\n\n"
        f"Plan project: authoring:{native}\n",
        encoding="utf-8",
    )
    return str(path)
