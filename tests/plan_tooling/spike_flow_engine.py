"""The engine `just plan`'s spike journeys launch through: the real one, with `start` doubled.

`tests/plan_tooling/test_plan_spike_flow_e2e.py` installs this at a copied checkout's
`.venv/bin/onepipeline`, the path `scripts/onepipeline.sh` runs, with the real binary beside
it. Every verb but `start` is handed to the real binary unchanged — `template resolve`, `plan
check` and the rest are the engine's own. **`start` is the one boundary doubled**, because a
real one dispatches the paid model and a real lifecycle session for every node; this does,
with the real tools, what each of the flow's runs leaves behind when it settles:

* the **draft** planner's run writes the plan into the store — a project record, a task
  rendered from `plan-task`, the budgets document unless the plan predates budgets — and,
  when the scenario names spikes, the `<plan>-spikes` project of spike tasks;
* the **spikes** run takes the run id the engine would mint from the project's
  `onepipeline.name`, and for each spike renders the launch's `--branch-template`, opens a
  real `onevcs` session cutting that branch labelled with the run and the node, commits a
  harness, preserves the branch on its origin, closes the session, and writes the spike's
  report into the plan's project from the `spike-report` template. It places them as the
  adopted engine does: a spike depending on spikes waits for them, and its session is
  opened with its **base dependency**'s kept branch as its base — the dependency whose own
  chain holds every other, read off the spikes project's own edges — while a spike behind
  a failed or skipped one is `skipped`;
* ``retry`` — not an engine verb, but what a manager's `retry` of a failed spike leaves once
  the driver has settled it: the failed spike `cancelled` and superseded by `<spike>-2`, which
  keeps a branch of its own, and every spike skipped behind it run from that branch;
* the **finalize** planner's run reads its own task's list of spikes and regenerates the
  plan's task with the `spikes` answer linking each report and branch;
* the **design** run renders the design document into the plan's project.

Each settles by writing the run root the engine keeps — `launch.json` and the `result.json`
ledger record — and ending as an attached `start` does. Every call is appended to a log, so
a journey reads what was launched, in order. The scenario is a JSON document the journey
names in the environment; its controls are what each journey varies.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

#: What the journey names, under the `FAKE_` prefix a launch keeps: the real engine, the
#: pinned store and `onevcs`, the scenario and where each `start` is logged.
REAL = "FAKE_ENGINE_REAL"
STORE = "FAKE_ENGINE_STORE"
ONEVCS = "FAKE_ENGINE_ONEVCS"
SCENARIO = "FAKE_ENGINE_SCENARIO"
LOG = "FAKE_ENGINE_LOG"

#: `start`'s flags that take no value; every other flag takes the next argument.
SWITCHES = {"--attach", "--detach", "--acknowledge-concurrent"}

COMMITTER = ["-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test"]


class Scenario(TypedDict):
    """What one journey's flow writes, and how each of its runs settles."""

    #: The flow's own name, which the draft's run is.
    run: str
    #: The plan's qualified project, and the root its source keeps records under.
    plan: str
    root: str
    #: The repository every task changes and every spike measures.
    repository: str
    #: The answers the plan's task and each spike task are rendered from.
    task: dict[str, object]
    spike_task: dict[str, object]
    #: The spike node ids the draft writes, none for a draft with no spikes.
    spikes: list[str]
    #: The budgets document's answers, or none for a plan that writes none.
    budgets: dict[str, object] | None
    #: The design document's answers, beside the budget answers its writer's task quotes.
    design: dict[str, object]
    #: Whether the draft writes anything at all.
    write: NotRequired[bool]
    #: Each spike's dependencies on other spikes, written as the spikes project's own edges.
    spike_deps: NotRequired[dict[str, list[str]]]
    #: Spikes that measured no candidate budget themselves, as a harness spike often has not.
    unmeasured: NotRequired[list[str]]
    #: The one spike the finalized task builds on, where it does not build on every spike.
    builds_on: NotRequired[str]
    #: Whether each spike task declares the `preserve` its convention requires.
    spike_publish: NotRequired[bool]
    #: Spikes settling `failed`, and spikes settling `done` on another outcome.
    fail: NotRequired[list[str]]
    outcomes: NotRequired[dict[str, str]]
    #: Spikes retried once: the first attempt superseded by `<spike>-2`, which keeps the branch.
    retried: NotRequired[list[str]]
    #: A spikes run settling `preserved` with no branch `onevcs` recorded.
    unrecorded: NotRequired[bool]
    #: A spikes launch the engine refuses before a run exists.
    refuse_spikes: NotRequired[bool]
    #: A finalize run settling `failed`.
    fail_finalize: NotRequired[bool]


class Node(TypedDict):
    """One node of a run's ledger record, as the engine writes it."""

    id: str
    status: str
    outcome: NotRequired[str]
    branch: NotRequired[str]
    head: NotRequired[str]
    remote: NotRequired[str]
    superseded_by: NotRequired[str]


# `Any` rather than `object`: the store's answer is an open JSON document this stand-in
# indexes into field by field, and a field it reads that is missing fails the journey.
def _object(value: object) -> dict[str, Any]:
    """``value`` as the JSON object the store answers with, or an assertion naming it."""
    assert isinstance(value, dict), f"the store answered {value!r} where it answers an object"
    return value


def _run(command: list[str], *, stdin: str | None = None, cwd: Path | None = None) -> str:
    done = subprocess.run(
        command, input=stdin, cwd=cwd, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise SystemExit(f"stand-in engine: {' '.join(command)} failed:\n{done.stderr}")
    return done.stdout


def _store(*arguments: str, stdin: str | None = None) -> str:
    return _run([os.environ[STORE], *arguments], stdin=stdin)


def _resolved(template: str) -> str:
    return _run([os.environ[REAL], "template", "resolve", template, "--json"])


def _with_answers(answers: dict[str, object]) -> str:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as written:
        json.dump(answers, written)
    return written.name


def _project_record(project: str) -> dict[str, object]:
    (held,) = json.loads(_store("project", "show", project, "--json"))["items"]
    return _object(held["item"])


def _run_id(project: str) -> str:
    """The run id the engine mints: the project's `onepipeline.name`, else its title."""
    record = _project_record(project)
    metadata = _object(record.get("metadata") or {})
    return str(metadata.get("onepipeline.name") or record["title"])


# `Any` for the reason `_object` gives: each listed task is the store's open JSON object.
def _tasks(project: str) -> list[dict[str, Any]]:
    source, _, native = project.partition(":")
    listed = json.loads(_store("task", "list", "--source", source, "--project", native, "--json"))
    return [_object(task) for task in _object(listed)["items"]]


def _write_project(root: Path, native: str, goal: str) -> None:
    (root / "projects").mkdir(parents=True, exist_ok=True)
    (root / "projects" / f"{native}.md").write_text(
        f'---\ntitle: "{native}"\nstatus: "todo"\nmetadata:\n'
        '  "onepipeline.schema_version": 3\n'
        f'  "onepipeline.goal": {json.dumps({"text": goal})}\n---\n\n{goal}\n',
        encoding="utf-8",
    )


def _create_task(
    project: str,
    title: str,
    answers: dict[str, object],
    *meta: str,
    repository: str,
    depends_on: tuple[str, ...] = (),
) -> str:
    """Create one task rendered from `plan-task`, answering its qualified id."""
    source, _, native = project.partition(":")
    created = _store(
        "task",
        "create",
        source,
        "--template-loader",
        "-",
        "--no-interactive",
        "--project",
        native,
        "--title",
        title,
        "--answers",
        _with_answers(answers),
        "--repository",
        repository,
        *(flag for one in meta for flag in ("--metadata", one)),
        *(flag for one in depends_on for flag in ("--depends-on", one)),
        "--json",
        stdin=_resolved("plan-task"),
    )
    (held,) = _object(json.loads(created))["items"]
    return str(held["id"])


def _document(plan: str, document: str, template: str, answers: dict[str, object]) -> None:
    source, _, native = plan.partition(":")
    _store(
        "document",
        "create",
        source,
        "--project",
        native,
        "--title",
        document,
        "--id",
        document,
        "--template-loader",
        "-",
        "--answers",
        _with_answers(answers),
        "--no-interactive",
        stdin=_resolved(template),
    )


def draft(scenario: Scenario) -> list[Node]:
    plan, root = scenario["plan"], Path(scenario["root"])
    if not scenario.get("write", True):
        return [{"id": "plan", "status": "done"}]
    native = plan.partition(":")[2]
    _write_project(root, native, "Page the node listing")
    _create_task(
        plan,
        "feat: page the node listing",
        scenario["task"],
        'onepipeline.id="build-listing"',
        'onepipeline.persona="engineer"',
        repository=scenario["repository"],
    )
    if scenario["budgets"] is not None:
        _document(plan, f"{native}-budgets", "plan-budgets", scenario["budgets"])
    if scenario["spikes"]:
        _write_project(root, f"{native}-spikes", "Measure the listing before the plan is final")
    publish = ['onepipeline.publish="preserve"'] if scenario.get("spike_publish", True) else []
    created: dict[str, str] = {}
    for spike in scenario["spikes"]:
        created[spike] = _create_task(
            f"{plan}-spikes",
            f"feat(spikes): measure {spike}",
            scenario["spike_task"],
            f'onepipeline.id="{spike}"',
            'onepipeline.persona="engineer"',
            *publish,
            repository=scenario["repository"],
            depends_on=tuple(created[dep] for dep in scenario.get("spike_deps", {}).get(spike, [])),
        )
    return [{"id": "plan", "status": "done"}]


def _kept(scenario: Scenario, template: str, run: str, node: str, base: str | None = None) -> Node:
    """Leave ``node``'s branch as a spike's session does, and answer its ledger entry.

    ``base`` is the kept branch of the spike it builds on, which its session starts from.
    """
    onevcs, repository = os.environ[ONEVCS], scenario["repository"]
    branch_name = template.replace("{{ node.id }}", node)
    if scenario.get("unrecorded"):
        return {"id": node, "status": "done", "outcome": "preserved", "branch": branch_name}
    opened = json.loads(
        _run(
            [onevcs, "session", "open", repository, "--branch-name", branch_name]
            + ([] if base is None else ["--base", base])
            + ["--label", f"run={run}", "--label", f"node={node}"]
        )
    )
    worktree = Path(opened["worktree"])
    (worktree / f"{node}.sh").write_text(f"echo measuring {node}\n", encoding="utf-8")
    _run(["git", "add", "-A"], cwd=worktree)
    _run(["git", *COMMITTER, "commit", "-qm", f"feat: {node} harness"], cwd=worktree)
    head = _run(["git", "rev-parse", "HEAD"], cwd=worktree).strip()
    _run([onevcs, "preserve", "--repo", repository, opened["branch"]])
    _run([onevcs, "session", "close", opened["token"]])
    outcome = scenario.get("outcomes", {}).get(node, "preserved")
    return {
        "id": node,
        "status": "done",
        "outcome": outcome,
        "branch": opened["branch"],
        "head": head,
        "remote": "pushed",
    }


def _spike_tasks(project: str) -> dict[str, list[str]]:
    """Each spike of ``project`` and the spikes it depends on, off the project's own edges."""
    tasks = _tasks(project)
    node_of = {str(task["id"]): str(task["item"]["metadata"]["onepipeline.id"]) for task in tasks}
    return {
        node_of[str(task["id"])]: [
            node_of[str(edge["to"]["id"])]
            for edge in _object(json.loads(_store("task", "deps", str(task["id"]), "--json")))[
                "items"
            ]
        ]
        for task in tasks
    }


def _base(deps: list[str], graph: dict[str, list[str]]) -> str | None:
    """The dependency whose own chain holds every other, as the engine selects it."""

    def above(node: str) -> set[str]:
        return {node}.union(*(above(dep) for dep in graph[node]))

    based = [dep for dep in deps if set(deps) <= above(dep)]
    assert len(based) == 1 or not deps, f"the engine refuses the fan-in {deps}"
    return based[0] if based else None


def _report(scenario: Scenario, node: str, kept: Node) -> None:
    """Write ``node``'s report, the one its task names, whichever attempt kept the branch."""
    measured = node not in scenario.get("unmeasured", [])
    _document(
        scenario["plan"],
        f"{node}-report",
        "spike-report",
        {
            "spike": node,
            "branch": kept.get("branch", ""),
            "harness": f"`{node}.sh` at the branch's root; run `sh {node}.sh`.",
            "method": "Twenty pages of the real listing at 2,000 nodes.",
            "candidates": [
                {
                    "budget": "listing-latency",
                    "measure": "time to the first page",
                    "workload": "2,000 nodes",
                    "achievable": "420 ms",
                    "limits": "5,000 calls an hour",
                    "consumed": "20 calls",
                }
            ]
            if measured
            else [],
            "findings": [],
        },
    )


def _settle(
    scenario: Scenario,
    graph: dict[str, list[str]],
    template: str,
    run: str,
    settled: dict[str, list[Node]],
    *,
    failing: list[str],
    retried: list[str],
) -> None:
    """Settle every spike of ``graph`` not yet in ``settled``, each once its dependencies have."""
    while len(settled) < len(graph):
        node = next(
            one for one in graph if one not in settled and all(dep in settled for dep in graph[one])
        )
        standing = {dep: settled[dep][-1] for dep in graph[node]}
        if any(entry["status"] != "done" for entry in standing.values()):
            settled[node] = [{"id": node, "status": "skipped"}]
            continue
        if node in failing:
            settled[node] = [{"id": node, "status": "failed", "outcome": "task-failed"}]
            continue
        entries: list[Node] = []
        attempt = node
        if node in retried:
            # The engine's retry: the first attempt settles `cancelled`, superseded by its
            # replacement, which runs as a node of its own and cuts a branch named for it.
            attempt = f"{node}-2"
            entries.append(
                {
                    "id": node,
                    "status": "cancelled",
                    "outcome": "task-failed",
                    "superseded_by": attempt,
                }
            )
        base = _base(graph[node], graph)
        kept = _kept(
            scenario, template, run, attempt, None if base is None else standing[base]["branch"]
        )
        _report(scenario, node, kept)
        settled[node] = [*entries, kept]


def spikes(scenario: Scenario, project: str, template: str, run: str) -> list[Node]:
    graph = _spike_tasks(project)
    settled: dict[str, list[Node]] = {}
    _settle(
        scenario,
        graph,
        template,
        run,
        settled,
        failing=scenario.get("fail", []),
        retried=scenario.get("retried", []),
    )
    return [entry for node in graph for entry in settled[node]]


def retry(scenario: Scenario, project: str, template: str) -> int:
    """Settle a manager's `retry` of every failed spike of the spikes run of ``project``.

    The failed spike is superseded by `<spike>-2`, which keeps a branch of its own, and every
    spike skipped behind it runs from that branch, as the engine re-points them onto it.
    """
    run = _run_id(project)
    record = Path(os.environ.get("ONEPIPELINE_RUNS_DIR", "runs")) / run / "result.json"
    ledger = _object(json.loads(record.read_text(encoding="utf-8")))
    graph = _spike_tasks(project)
    held: dict[str, Node] = {str(entry["id"]): cast(Node, entry) for entry in ledger["nodes"]}
    failed = [node for node in graph if held[node]["status"] == "failed"]
    settled = {
        node: [held[node]] for node in graph if held[node]["status"] not in ("failed", "skipped")
    }
    _settle(scenario, graph, template, run, settled, failing=[], retried=failed)
    nodes = [entry for node in graph for entry in settled[node]]
    done = all(entry["status"] in ("done", "cancelled") for entry in nodes)
    ledger |= {"state": "complete" if done else "failed", "ok": done, "nodes": nodes}
    record.write_text(json.dumps(ledger), encoding="utf-8")
    return 0


#: How the finalize planner's task lists each spike, in `orchestrator/spike_flow.py`'s words.
LISTED = re.compile(
    r"^- `(?P<spike>[^`]+)`: report `(?P<report>[^`]+)`; branch `(?P<branch>[^`]+)`$", re.M
)


def finalize(scenario: Scenario, project: str) -> list[Node]:
    if scenario.get("fail_finalize"):
        return [{"id": "finalize", "status": "failed", "outcome": "task-failed"}]
    (own,) = _tasks(project)
    content = str(own["item"]["content"])
    listed = [match.groupdict() for match in LISTED.finditer(content)]
    built_on = scenario.get("builds_on")
    if built_on is not None:
        # The task builds on one spike: it links that one and every spike the note names
        # above it in its stacking chain, which is where that spike's harness is.
        line = re.search(rf"^- `{re.escape(built_on)}`: above it, (.+)$", content, re.M)
        above = re.findall(r"`([^`]+)`", line.group(1)) if line else []
        listed = [one for one in listed if one["spike"] in {built_on, *above}]
    for task in _tasks(scenario["plan"]):
        answered = json.loads(_store("task", "answers", str(task["id"]), "--json"))
        answers = _object(_object(answered).get("answers", answered))
        answers["spikes"] = listed
        _store(
            "task",
            "render",
            str(task["id"]),
            "--template-loader",
            "-",
            "--answers",
            _with_answers(answers),
            "--no-interactive",
            stdin=_resolved("plan-task"),
        )
    return [{"id": "finalize", "status": "done"}]


def design(scenario: Scenario, project: str) -> list[Node]:
    (own,) = _tasks(project)
    content = str(own["item"]["content"])
    answers = dict(scenario["design"])
    # The task names the budgets document's file; its answers are the record it renders.
    named = re.search(r"budgets document `[^`]+`, the file `([^`]+)`", content)
    predates = re.search(r"The plan predates budgets:.*?\n\n> ([^\n]+)", content, re.S)
    if named:
        recorded = re.search(
            r"\n## Record\n\n```json\n([^\n]*)\n```",
            Path(named.group(1)).read_text(encoding="utf-8"),
        )
        assert recorded is not None, named.group(1)
        answers.update(json.loads(recorded.group(1)))
    elif predates:
        answers["predates_budgets"] = predates.group(1)
    plan = scenario["plan"]
    _document(plan, f"{plan.partition(':')[2]}-design", "design-doc", answers)
    return [{"id": "design-doc", "status": "done"}]


def start(arguments: list[str]) -> int:
    project, template, detached, rest = "", "", False, list(arguments)
    while rest:
        match rest.pop(0):
            case "--detach":
                detached = True
            case word if word in SWITCHES:
                pass
            case "--branch-template":
                template = rest.pop(0) if rest else ""
            case word if word.startswith("--") and "=" not in word:
                rest = rest[1:]
            case word if not word.startswith("--") and not project:
                project = word
            case _:
                pass
    # The journey writes this document itself, in exactly the shape `Scenario` states.
    scenario = cast(Scenario, json.loads(os.environ[SCENARIO]))
    run = _run_id(project)
    with Path(os.environ[LOG]).open("a", encoding="utf-8") as log:
        log.write(json.dumps({"run": run, "project": project, "arguments": arguments}) + "\n")
    flow = scenario["run"]
    match run:
        case _ if run == flow:
            nodes = draft(scenario)
        case _ if run == f"{flow}-spikes" and scenario.get("refuse_spikes"):
            # A launch the engine refuses before any run exists: nothing is written.
            print(f"onepipeline: refused: {project}", file=sys.stderr)
            return 2
        case _ if run == f"{flow}-spikes":
            nodes = spikes(scenario, project, template, run)
        case _ if run == f"{flow}-finalize":
            nodes = finalize(scenario, project)
        case _:
            nodes = design(scenario, project)
    root = Path(os.environ.get("ONEPIPELINE_RUNS_DIR", "runs")) / run
    root.mkdir(parents=True, exist_ok=True)
    (root / "launch.json").write_text(json.dumps({"run_id": run, "project": project}), "utf-8")
    done = all(node["status"] == "done" for node in nodes)
    ledger = {
        "schema_version": 5,
        "run_id": run,
        "state": "complete" if done else "failed",
        "ok": done,
        "nodes": nodes,
    }
    (root / "result.json").write_text(json.dumps(ledger), encoding="utf-8")
    if detached:
        print(json.dumps({"run_id": run, "pid": os.getpid()}))
        return 0
    print(json.dumps({"run_id": run, "settlement": "complete" if done else "failed"}))
    return 0 if done else 1


def main() -> int:
    match sys.argv[1:]:
        case ["retry", project, template]:
            scenario = cast(Scenario, json.loads(os.environ[SCENARIO]))
            return retry(scenario, project, template)
        case ["start", *arguments]:
            return start(arguments)
        case arguments:
            os.execv(os.environ[REAL], [os.environ[REAL], *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
