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
  report into the plan's project from the `spike-report` template;
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
    project: str, title: str, answers: dict[str, object], *meta: str, repository: str
) -> None:
    source, _, native = project.partition(":")
    _store(
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
        stdin=_resolved("plan-task"),
    )


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
    for spike in scenario["spikes"]:
        _create_task(
            f"{plan}-spikes",
            f"feat(spikes): measure {spike}",
            scenario["spike_task"],
            f'onepipeline.id="{spike}"',
            'onepipeline.persona="engineer"',
            *publish,
            repository=scenario["repository"],
        )
    return [{"id": "plan", "status": "done"}]


def _kept(scenario: Scenario, template: str, run: str, node: str) -> Node:
    """Leave ``node``'s branch as a spike's session does, and answer its ledger entry."""
    onevcs, repository = os.environ[ONEVCS], scenario["repository"]
    branch_name = template.replace("{{ node.id }}", node)
    if scenario.get("unrecorded"):
        return {"id": node, "status": "done", "outcome": "preserved", "branch": branch_name}
    opened = json.loads(
        _run(
            [onevcs, "session", "open", repository, "--branch-name", branch_name]
            + ["--label", f"run={run}", "--label", f"node={node}"]
        )
    )
    worktree = Path(opened["worktree"])
    (worktree / "harness.sh").write_text(f"echo measuring {node}\n", encoding="utf-8")
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


def spikes(scenario: Scenario, project: str, template: str, run: str) -> list[Node]:
    settled: list[Node] = []
    for task in _tasks(project):
        node = str(task["item"]["metadata"]["onepipeline.id"])
        if node in scenario.get("fail", []):
            settled.append({"id": node, "status": "failed", "outcome": "task-failed"})
            continue
        standing = node
        if node in scenario.get("retried", []):
            # The engine's retry: the first attempt settles `cancelled`, superseded by its
            # replacement, which runs as a node of its own and cuts a branch named for it.
            standing = f"{node}-2"
            settled.append(
                {
                    "id": node,
                    "status": "cancelled",
                    "outcome": "task-failed",
                    "superseded_by": standing,
                }
            )
        kept = _kept(scenario, template, run, standing)
        # The report is the one the spike's task names, whichever attempt wrote it.
        _document(
            scenario["plan"],
            f"{node}-report",
            "spike-report",
            {
                "spike": node,
                "branch": kept.get("branch", ""),
                "harness": "`harness.sh` at the branch's root; run `sh harness.sh`.",
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
                ],
                "findings": [],
            },
        )
        settled.append(kept)
    return settled


#: How the finalize planner's task lists each spike, in `orchestrator/spike_flow.py`'s words.
LISTED = re.compile(
    r"^- `(?P<spike>[^`]+)`: report `(?P<report>[^`]+)`; branch `(?P<branch>[^`]+)`$", re.M
)


def finalize(scenario: Scenario, project: str) -> list[Node]:
    if scenario.get("fail_finalize"):
        return [{"id": "finalize", "status": "failed", "outcome": "task-failed"}]
    (own,) = _tasks(project)
    listed = [match.groupdict() for match in LISTED.finditer(str(own["item"]["content"]))]
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
    quoted = re.search(r"which answers the following\..*?```json\n(.*?)\n```", content, re.S)
    predates = re.search(r"The plan predates budgets:.*?\n\n> ([^\n]+)", content, re.S)
    if quoted:
        answers.update(json.loads(quoted.group(1)))
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
    arguments = sys.argv[1:]
    if arguments[:1] != ["start"]:
        os.execv(os.environ[REAL], [os.environ[REAL], *arguments])
    return start(arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
