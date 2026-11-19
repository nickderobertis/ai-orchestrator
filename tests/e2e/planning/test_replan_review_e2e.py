"""A plan partway through delivery is reviewed as a plan, and what its run settled is not reviewed.

One sequence on one bench (`planning_bench`), through the real `just review-plan`, `just
copy-plan` and the launch gate `scripts/plan-attestation-check.sh` as the engine's `plan
check` runs it, over plans the store renders from this host's own templates. Only the paid
model is doubled: `fake_backend.py` answers the planner's `reviewer` and records every turn
it is handed, refusing a turn carrying the defect text below together with the planner
persona's plan-review bar and the review's task framing, and a turn carrying an amendment
without the words presenting it as the run's applied record; `judging_codex.py` answers every
`plan-checklist` batch and records the files each read.

* **Reviewed as a plan.** Two plans whose descriptions state a delivery criterion their
  nodes have not met: one with no board copy whose own tasks and project carry what a run
  writes, and one whose clean authoring copy has a board copy holding an amendment. Each
  passes, every judge reads the bar and the framing — that a task's text need not restate its
  amendment, which binds where they differ — none reads a run-written key, each amendment
  reaches the reviewer verbatim after its task's body in a section presenting it as the run's
  applied record rather than a ruling of the plan, and the run's keys on the first plan are
  left as they were. New run-written values after a pass re-open nothing.
* **Settled tasks are not reviewed.** A board-copied plan whose settlements live on its board
  copy, and a plan with no board copy whose settlements sit on its own tasks. After the bar
  moves, a task settled `done` is neither judged nor refused, whether its record covers the
  whole run-written list, predates the list, its body was edited since, it was re-rendered
  under a changed `plan-task` template, it has no record, or it carries the defect. Every
  task not settled `done` is refused by the gate and judged — one whose authoring copy alone
  says `done`, and one settled `done` and since reopened, among them — and the one carrying
  the defect is refused, its project with it, until it is repaired; then the description is
  attested with the settled defect beside it.
* **Withdrawn tasks are not reviewed.** The same two plans each hold a task carrying the
  defect, withdrawn (`cancelled`, the status the store answers for a board issue closed as not
  planned) where its run reads it: on the board copy alone when it is first reviewed, and on
  the task itself. No judge reads it, nothing attests it, the launch gate never names it, and
  the description is judged and attested beside it, as the launch omits it.
* **A defect is still refused.** A task whose criterion does not prove its goal is refused,
  its project with it, while its sibling is attested, and a description carrying the defect
  is refused.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from typing import Any, NamedTuple

import engine_writeback_keys
import pytest
import yaml
from fake_backend import (
    JUDGE_REFUSAL,
    JUDGE_REFUSES_ENV,
    JUDGE_REFUSES_UNLESS_ENV,
    JUDGE_UNLESS_REFUSAL,
    RefusalCondition,
)
from planning_bench import (
    BOARD,
    Bench,
    bench,
    judge_inputs,
    judge_only_turns,
    just,
    run,
    store,
    store_json,
)

from orchestrator.root import REPO_ROOT

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("replan-review")]

STORE = ".venv/bin/onetaskgraph"
RESOLVE = "scripts/onepipeline.sh template resolve"

#: The planner persona's plan-review bar, as its first line states it.
BAR = "You are a principal engineer reviewing a plan before anyone builds from it."
#: The review's task framing, one phrase of each of its parts: what the plan document is, that
#: a task's text need not restate its amendment, which binds where the two differ, and that a
#: task is judged alone, its siblings context.
FRAMING = (
    "reviewed under the plan-review bar before or during its delivery",
    "Its acceptance criteria state what its nodes will deliver",
    "Its delivery state",
    "A task's own text is not expected to restate its amendment",
    "or to be rewritten to match it; where the two differ, the amendment binds.",
    "When the material under review is one task, judge that task alone",
)
#: What the description's judge is told besides: which tasks beside it are delivered work.
DELIVERED_FRAMING = "The tasks under delivered/ beside this description were settled done"
#: A criterion that does not prove its goal: the defect the doubled judges refuse.
DEFECT = "DEFECT-CRITERION: the docs mention that a health route exists."
#: A delivery criterion every plan's description states and none of its nodes has met.
UNMET = "The plan is done once every node has landed on main and the route answers in production."
#: Where the review renders an amendment into its task's judged file, and how it opens.
AMENDMENT_HEADING = "## Amendment: criteria the run added to this task by a live edit"
AMENDMENT_RECORD = (
    "This is the run's applied record, not a ruling this plan records: the run added these "
    "criteria to the task above by a live edit, and they are part of its bar as written. No "
    "ruling reference is expected of them, and their having none is no ground to refuse the task."
)
#: A line the changed `plan-task` template adds to every task it renders.
TEMPLATE_REVISION = "TEMPLATE-REVISION: the host's task template changed."


def _sh(bench_: Bench, script: str) -> str:
    done = run(bench_, "bash", "-c", f"set -euo pipefail; {script}", seconds=180)
    assert done.returncode == 0, f"{script}\n{done.stdout}{done.stderr}"
    return done.stdout


def _project(bench_: Bench, native: str, overview: str | None = None) -> str:
    """A plan project in `authoring`, its description rendered from `plan-description`."""
    goal = json.dumps({"text": "Give the service a health route operators can poll"})
    _sh(
        bench_,
        f"{RESOLVE} plan-description --json | {STORE} project create authoring --id {native} "
        f"--title {shlex.quote('Health route ' + native)} --template-loader - "
        f"--var {shlex.quote('overview=' + (overview or 'Add a health route. ' + UNMET))} "
        "--metadata onepipeline.schema_version=3 "
        f"--metadata {shlex.quote('onepipeline.goal=' + goal)} "
        "--classification private --no-interactive >/dev/null",
    )
    return f"authoring:{native}"


def _criteria(node: str, criterion: str | None = None) -> str:
    return json.dumps([criterion or f"A request-level test drives {node} end to end."])


def _task(bench_: Bench, plan: str, node: str, criterion: str | None = None) -> str:
    """One task of ``plan``, rendered from `plan-task`; answers its qualified id."""
    created = _sh(
        bench_,
        f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {plan} "
        f"--title {shlex.quote('feat: ' + node)} --template-loader - "
        f"--var {shlex.quote(f'what={_what(node)}')} --var 'why=Operators need it.' "
        f"--var {shlex.quote('acceptance_criteria=' + _criteria(node, criterion))} "
        f"--metadata 'onepipeline.id={json.dumps(node)}' "
        "--metadata 'onepipeline.persona=\"engineer\"' "
        "--metadata 'orchestrator.unit=\"API\"' --no-interactive --json",
    )
    return str(json.loads(created)["items"][0]["id"])


def _render(bench_: Bench, task: str, *variables: str) -> None:
    """Regenerate ``task`` from the `plan-task` template the bench resolves now, its stored
    answers laid under ``variables``."""
    words = " ".join(f"--var {shlex.quote(one)}" for one in variables)
    _sh(
        bench_,
        f"{RESOLVE} plan-task --json | {STORE} task render {task} --template-loader - "
        f"{words} --no-interactive >/dev/null",
    )


def _what(node: str) -> str:
    """The text only ``node``'s own content carries, so a judge's input names it."""
    return f"Build the {node} part of the route (WHAT-{node})."


def _set(bench_: Bench, kind: str, item: str, values: Mapping[str, object]) -> None:
    for key, value in values.items():
        answered = store(bench_, kind, "metadata", "set", item, key, json.dumps(value))
        assert answered.returncode == 0, answered.stdout + answered.stderr


def _withdraw(bench_: Bench, task: str) -> None:
    """Withdraw ``task`` as the store records a board issue closed as not planned."""
    withdrawn = store(bench_, "task", "update", task, "--status", "cancelled")
    assert withdrawn.returncode == 0, withdrawn.stdout + withdrawn.stderr


def _unset(bench_: Bench, task: str, key: str) -> None:
    removed = store(bench_, "task", "update", task, "--remove-metadata", key)
    assert removed.returncode == 0, removed.stdout + removed.stderr


def _metadata(bench_: Bench, kind: str, item: str) -> dict[str, Any]:
    shown = store_json(bench_, kind, "show", item)
    items = shown["items"]
    assert isinstance(items, list) and len(items) == 1, shown
    metadata = items[0]["item"]["metadata"]
    assert isinstance(metadata, dict), shown
    return metadata


def _run_written(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key in engine_writeback_keys.run_written_keys()
    }


def _bar(bench_: Bench) -> str:
    answered = run(
        bench_,
        ".venv/bin/oneagentgraph",
        *("run", "graphs/planner.yaml", "--judge-only", "--member", "worker", "--bar-fingerprint"),
    )
    assert answered.returncode == 0, answered.stdout + answered.stderr
    return answered.stdout.strip()


def _records(bench_: Bench, plan: str) -> dict[str, dict[str, Any]]:
    """Each task's `plan-review` answer under the current bar, by qualified id."""
    checked = store_json(
        bench_,
        *("project", "attestation", "check", plan, "--kind", "plan-review", "--tasks"),
        *("--bar", _bar(bench_)),
    )
    tasks = checked["tasks"]
    assert isinstance(tasks, list), checked
    return {str(one["id"]): one for one in tasks}


def _project_state(bench_: Bench, plan: str) -> str:
    checked = store_json(
        bench_,
        "project",
        "attestation",
        "check",
        plan,
        "--kind",
        "plan-review",
        "--bar",
        _bar(bench_),
    )
    return str(checked["state"])


def _move_bar(bench_: Bench, label: str, to: str) -> None:
    """Rename the planner graph's checklist judge in the bench's copy: the bar moves."""
    graph = bench_.checkout / "graphs" / "planner.yaml"
    text = graph.read_text(encoding="utf-8")
    assert f"label: {label}\n" in text, f"graphs/planner.yaml labels no judge {label}"
    graph.write_text(text.replace(f"label: {label}\n", f"label: {to}\n"), encoding="utf-8")


def _copied(bench_: Bench, plan: str) -> str:
    """``plan`` copied onto the bench's board through `just copy-plan`; answers the copy's id."""
    copied = just(bench_, "copy-plan", plan, BOARD)
    assert copied.returncode == 0, copied.stdout + copied.stderr
    copies = _metadata(bench_, "project", plan)["onetaskgraph.copies"]
    return str(copies[BOARD])


def _board_task(bench_: Bench, task: str) -> str:
    return str(_metadata(bench_, "task", task)["onetaskgraph.copies"][BOARD])


class Review(NamedTuple):
    """One `just review-plan`, with every reviewer turn and checklist batch it caused."""

    ran: subprocess.CompletedProcess[str]
    turns: list[dict[str, Any]]
    batches: list[dict[str, Any]]

    def judged(self, node: str) -> bool:
        """Whether a judge of this review read ``node``'s own content as the material it
        judges: a reviewer turn naming it, or a checklist batch reading it under `tasks/`."""
        marker = f"WHAT-{node})"
        return any(marker in str(turn.get("prompt")) for turn in self.turns) or any(
            marker in str(text)
            for batch in self.batches
            for name, text in dict(batch["read"]).items()
            if "tasks/" in str(name)
        )

    def texts(self) -> list[str]:
        """Every text a judge of this review was handed: each reviewer turn's prompt and
        each file a checklist batch read."""
        texts = [str(turn.get("prompt")) for turn in self.turns]
        return texts + [
            str(text) for batch in self.batches for text in dict(batch["read"]).values()
        ]


def _reviewed(bench_: Bench, plan: str) -> Review:
    turns, batches = len(judge_only_turns(bench_)), len(judge_inputs(bench_))
    ran = just(bench_, "review-plan", plan)
    return Review(
        ran,
        judge_only_turns(bench_)[turns:],
        [one for one in judge_inputs(bench_)[batches:] if one.get("llmlint")],
    )


def _gate(bench_: Bench, plan: str) -> list[str]:
    """The launch gate's refusals for ``plan``, through the engine's `plan check` as a launch
    runs it with the checks `config/onepipeline.launch.yaml` registers."""
    registered = yaml.safe_load(
        (REPO_ROOT / "config" / "onepipeline.launch.yaml").read_text("utf-8")
    )
    checked = run(
        bench_._replace(environment={**bench_.environment, "ORCHESTRATOR_PLAN": plan}),
        "scripts/onepipeline.sh",
        *("plan", "check", plan, "--json"),
        *[word for (check,) in registered["checks"] for word in ("--check", check)],
        seconds=300,
    )
    answer = json.loads(checked.stdout)
    assert answer["unrunnable"] == [], checked.stdout + checked.stderr
    return [str(refusal["reason"]) for refusal in answer["refusals"]]


def _refused_tasks(reasons: list[str], tasks: dict[str, str]) -> list[str]:
    """The nodes of ``tasks`` (node -> id) whose plan review the gate refused, sorted."""
    return sorted(
        node
        for node, task in tasks.items()
        if any(reason.startswith(f"{task}: plan-review") for reason in reasons)
    )


def _refused_project(reasons: list[str], plan: str) -> bool:
    return any(reason.startswith(f"{plan}: plan-review") for reason in reasons)


#: The run-written values the first plan's own records carry, as a run would have written them.
OWN_TASK_KEYS: dict[str, dict[str, object]] = {
    "landed": {
        "onepipeline.settlement": {"status": "done", "branch": "me/landed", "landing": "landed"},
        "onepipeline.projected_status": "done",
        "onepipeline.landing": "landed",
        "onepipeline.change_url": "https://example.invalid/change/1",
    },
    "broke": {"onepipeline.settlement": {"status": "failed", "branch": "me/broke"}},
    "running": {"onepipeline.dispatch": {"branch": "me/running", "base": "main"}},
    "amended": {"onepipeline.amendment": "- OWN-AMENDMENT: the route answers within 50 ms."},
}
OWN_PROJECT_KEYS: dict[str, object] = {"onepipeline.driver": {"session": "a-manager", "pid": 1}}
BOARD_AMENDMENT = "- BOARD-AMENDMENT: the route reports its build commit."
AMENDMENTS = (str(OWN_TASK_KEYS["amended"]["onepipeline.amendment"]), BOARD_AMENDMENT)
DONE: dict[str, object] = {"status": "done", "branch": "me/x", "landing": "landed"}
TWO_KEY = ("--exclude", "onepipeline.projected_status", "--exclude", "onepipeline.settlement")
#: The settled-task plans' nodes settled `done`: under a whole-list record, under a record
#: predating the list, edited since its record, re-rendered from unchanged answers under a
#: changed template, with no record, and carrying the defect.
SETTLED = ("kept", "predates", "edited", "rerendered", "unrecorded", "flawed")
#: Those not settled `done`: never settled, settled only on its authoring copy (on the board-
#: copied plan; the other plan holds no such copy), and settled `done` once and since reopened.
UNSETTLED = ("open", "stale", "reopened")


class Settled(NamedTuple):
    """One settled-task plan, the gate's refusals and the reviews after the bar moved: the
    first with `open` carrying the defect, the second with it repaired."""

    plan: str
    board: str | None
    tasks: dict[str, str]
    gate_before: list[str]
    gate_before_project: bool
    refused: Review
    gate_refused: list[str]
    gate_refused_project: bool
    repaired: Review
    project_state: str
    records: dict[str, dict[str, Any]]
    gate_after: list[str]
    gate_after_project: bool


class Sequence(NamedTuple):
    bench: Bench
    own: str
    own_tasks: dict[str, str]
    own_before: dict[str, dict[str, Any]]
    own_after: dict[str, dict[str, Any]]
    boarded: str
    boarded_tasks: dict[str, str]
    reviews: dict[str, Review]
    records: dict[str, dict[str, dict[str, Any]]]
    project_states: dict[str, str]
    rereview: Review
    boarded_settled: Settled
    own_settled: Settled
    defect: Review
    defect_tasks: dict[str, str]
    defect_records: dict[str, dict[str, Any]]
    defect_project: str
    described: Review
    described_records: dict[str, dict[str, Any]]
    described_project: str


class SettledPlan(NamedTuple):
    """A reviewed plan holding the settled-task cases: its id, its board copy and its tasks."""

    plan: str
    board: str | None
    tasks: dict[str, str]


def _settled_plan(bench_: Bench, native: str, *, boarded: bool) -> SettledPlan:
    """A reviewed plan holding the settled-task cases, its board copy written as a run would."""
    plan = _project(bench_, native)
    tasks = {node: _task(bench_, plan, node) for node in (*SETTLED, *UNSETTLED) if node not in LATE}
    reviewed = just(bench_, "review-plan", plan)
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    # A record of the two keys the review once excluded, voided since by landing keys a hand
    # or a run wrote onto the task the record covers.
    attested = store(
        bench_,
        *("task", "attest", tasks["predates"], "--kind", "plan-review", "--by", "review-plan"),
        *("--bar", _bar(bench_), *TWO_KEY),
    )
    assert attested.returncode == 0, attested.stdout + attested.stderr
    _set(
        bench_,
        "task",
        tasks["predates"],
        {"onepipeline.landing": "landed", "onepipeline.landing_commit": "abc123"},
    )
    _render(
        bench_,
        tasks["edited"],
        "acceptance_criteria="
        + _criteria("edited", "A request-level test drives edited end to end, edited since."),
    )
    # The host's shared task template changes, and the task is re-rendered from the answers
    # it already holds; the template is put back for every task rendered after.
    template = bench_.checkout / "templates" / "plan-task.md.j2"
    original = template.read_text(encoding="utf-8")
    include = '{% include "dispatch-appendix.md" %}'
    assert original.count(include) == 1, "templates/plan-task.md.j2 includes no appendix once"
    template.write_text(original.replace(include, f"{TEMPLATE_REVISION}\n\n{include}"), "utf-8")
    try:
        _render(bench_, tasks["rerendered"])
    finally:
        template.write_text(original, encoding="utf-8")
    # Tasks added after the review, so they hold no record at all.
    tasks["unrecorded"] = _task(bench_, plan, "unrecorded")
    tasks["flawed"] = _task(bench_, plan, "flawed", DEFECT)
    tasks[WITHDRAWN] = _task(bench_, plan, WITHDRAWN, DEFECT)
    if not boarded:
        _withdraw(bench_, tasks[WITHDRAWN])
        for node in (*SETTLED, "reopened"):
            _set(bench_, "task", tasks[node], {"onepipeline.settlement": DONE})
        # Reopened for more work: its settlement is no longer `done`.
        _set(bench_, "task", tasks["reopened"], {"onepipeline.settlement": {"status": "failed"}})
        return SettledPlan(plan, None, tasks)
    board = _copied(bench_, plan)
    # Withdrawn on its board copy alone, where the run reads it.
    _withdraw(bench_, _board_task(bench_, tasks[WITHDRAWN]))
    for node in (*SETTLED, "reopened"):
        _set(bench_, "task", _board_task(bench_, tasks[node]), {"onepipeline.settlement": DONE})
    # Reopened for more work: its board copy, where the run writes, holds no settlement now.
    _unset(bench_, _board_task(bench_, tasks["reopened"]), "onepipeline.settlement")
    # A stale settlement on the authoring copy, which the board copy, where the run writes,
    # does not hold.
    _set(bench_, "task", tasks["stale"], {"onepipeline.settlement": DONE})
    return SettledPlan(plan, board, tasks)


#: The node carrying the defect that the settled-task plans withdraw where their run reads it.
WITHDRAWN = "withdrawn"
#: The settled-task nodes created after the plan's first review.
LATE = ("unrecorded", "flawed", WITHDRAWN)


def _settled_journey(bench_: Bench, settled: SettledPlan) -> Settled:
    """After the bar moved: gate; plant the defect on `open` and review; copy, gate; repair it
    and review; copy, gate again."""
    plan, board, tasks = settled
    gated = board or plan
    gated_tasks = (
        {node: _board_task(bench_, task) for node, task in tasks.items()} if board else tasks
    )
    gate_before = _gate(bench_, gated)
    _render(bench_, tasks["open"], "acceptance_criteria=" + _criteria("open", DEFECT))
    refused = _reviewed(bench_, plan)
    if board:
        # As the refresh starting a replan does, so the copy writes no stale settlement over
        # the board copy, where the run writes.
        _unset(bench_, tasks["stale"], "onepipeline.settlement")
        # The withdrawal mirrored onto the authoring copy as that refresh does, so the copy
        # keeps the board copy withdrawn.
        _withdraw(bench_, tasks[WITHDRAWN])
        _copied(bench_, plan)
    gate_refused = _gate(bench_, gated)
    _render(bench_, tasks["open"], "acceptance_criteria=" + _criteria("open"))
    repaired = _reviewed(bench_, plan)
    project_state = _project_state(bench_, plan)
    records = _records(bench_, plan)
    if board:
        _copied(bench_, plan)
    gate_after = _gate(bench_, gated)
    return Settled(
        plan,
        board,
        tasks,
        _refused_tasks(gate_before, gated_tasks),
        _refused_project(gate_before, gated),
        refused,
        _refused_tasks(gate_refused, gated_tasks),
        _refused_project(gate_refused, gated),
        repaired,
        project_state,
        records,
        _refused_tasks(gate_after, gated_tasks),
        _refused_project(gate_after, gated),
    )


@pytest.fixture(scope="module")
def sequence(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Sequence]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("replan-review")
    unless = [
        RefusalCondition(when=[text], unless=[AMENDMENT_HEADING, AMENDMENT_RECORD, *FRAMING])
        for text in AMENDMENTS
    ]
    # llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This journey copies
    # the checkout and runs the review, copy and launch-gate scripts in the copy, which read
    # config/, graphs/, personas/, templates/ and scripts/ whole; planning:test keys on those
    # trees for that reason, which its project.json states beside the same rule.
    bench_ = bench(
        tmp,
        oneharness_bin,
        "e2e-replan-review",
        **{
            JUDGE_REFUSES_ENV: json.dumps([DEFECT, BAR, *FRAMING]),
            JUDGE_REFUSES_UNLESS_ENV: json.dumps(unless),
        },
    )
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
    tag = os.getpid()

    own = _project(bench_, f"own-{tag}")
    own_tasks = {node: _task(bench_, own, node) for node in OWN_TASK_KEYS}
    for node, values in OWN_TASK_KEYS.items():
        _set(bench_, "task", own_tasks[node], values)
    _set(bench_, "project", own, OWN_PROJECT_KEYS)
    own_before = {
        item: _run_written(_metadata(bench_, kind, item))
        for kind, item in (("project", own), *(("task", one) for one in own_tasks.values()))
    }

    boarded = _project(bench_, f"boarded-{tag}")
    boarded_tasks = {node: _task(bench_, boarded, node) for node in ("first", "amended")}
    _copied(bench_, boarded)
    _set(
        bench_,
        "task",
        _board_task(bench_, boarded_tasks["amended"]),
        {"onepipeline.amendment": BOARD_AMENDMENT},
    )

    reviews = {plan: _reviewed(bench_, plan) for plan in (own, boarded)}
    records = {plan: _records(bench_, plan) for plan in (own, boarded)}
    project_states = {plan: _project_state(bench_, plan) for plan in (own, boarded)}
    own_after = {
        item: _run_written(_metadata(bench_, kind, item))
        for kind, item in (("project", own), *(("task", one) for one in own_tasks.values()))
    }

    _set(
        bench_,
        "task",
        own_tasks["landed"],
        {"onepipeline.landing_commit": "def456", "onepipeline.rework": {"count": 1}},
    )
    _set(bench_, "project", own, {"onepipeline.publishing": {"session": "a-manager"}})
    rereview = _reviewed(bench_, own)

    boarded_settled = _settled_plan(bench_, f"settled-board-{tag}", boarded=True)
    own_settled = _settled_plan(bench_, f"settled-own-{tag}", boarded=False)
    _move_bar(bench_, "plan-checklist", "checklist")
    first = _settled_journey(bench_, boarded_settled)
    second = _settled_journey(bench_, own_settled)

    defect_plan = _project(bench_, f"defect-{tag}")
    defect_tasks = {
        "flawed": _task(bench_, defect_plan, "flawed", DEFECT),
        "sound": _task(bench_, defect_plan, "sound"),
    }
    defect = _reviewed(bench_, defect_plan)
    described_plan = _project(bench_, f"described-{tag}", f"Add a health route. {DEFECT}")
    described_tasks = {"sound": _task(bench_, described_plan, "sound")}
    described = _reviewed(bench_, described_plan)
    defect_records, defect_project = (
        _records(bench_, defect_plan),
        _project_state(bench_, defect_plan),
    )
    described_records = _records(bench_, described_plan)
    described_project = _project_state(bench_, described_plan)
    _move_bar(bench_, "checklist", "plan-checklist")
    yield Sequence(
        bench_,
        own,
        own_tasks,
        own_before,
        own_after,
        boarded,
        boarded_tasks,
        reviews,
        records,
        project_states,
        rereview,
        first,
        second,
        defect,
        defect_tasks,
        defect_records,
        defect_project,
        described,
        {described_tasks["sound"]: described_records[described_tasks["sound"]]},
        described_project,
    )


def _carries_run_written_key(text: str) -> list[str]:
    return [key for key in engine_writeback_keys.run_written_keys() if key in text]


def _turn_of(review: Review, node: str) -> dict[str, Any]:
    turns = [turn for turn in review.turns if f"WHAT-{node})" in str(turn.get("prompt"))]
    assert turns, f"no reviewer turn judged {node}"
    return turns[0]


def _project_turn(review: Review, plan: str) -> dict[str, Any]:
    native = plan.split(":", 1)[1]
    turns = [
        turn
        for turn in review.turns
        if f'"title":"Health route {native}"' in str(turn.get("prompt"))
    ]
    assert len(turns) == 1, [str(turn.get("prompt"))[:300] for turn in review.turns]
    return turns[0]


def _composed(turn: dict[str, Any]) -> str:
    return f"{turn.get('system')}\n{turn.get('prompt')}"


def _framed(turn: dict[str, Any]) -> bool:
    composed = _composed(turn)
    return BAR in composed and all(phrase in composed for phrase in FRAMING)


def test_a_plan_carrying_run_state_and_unmet_delivery_criteria_passes_review(
    sequence: Sequence,
) -> None:
    """Both plans pass, the amended task and its project attested with them."""
    for plan in (sequence.own, sequence.boarded):
        review = sequence.reviews[plan]
        assert review.ran.returncode == 0, review.ran.stdout + review.ran.stderr
        assert JUDGE_UNLESS_REFUSAL not in review.ran.stderr, review.ran.stderr
        # The first plan's `landed` is settled `done` on its own record, so it is never judged.
        landed = {sequence.own_tasks["landed"]} if plan == sequence.own else set()
        states = {task: one["state"] for task, one in sequence.records[plan].items()}
        assert {
            task: state for task, state in states.items() if task not in landed
        } == dict.fromkeys(set(states) - landed, "valid"), states
        assert all(states[task] == "absent" for task in landed), states
        assert not review.judged("landed")
        assert sequence.project_states[plan] == "valid", sequence.project_states
        assert UNMET in str(_project_turn(review, plan).get("prompt"))


def test_every_judge_reads_the_plan_review_bar_and_the_framing(sequence: Sequence) -> None:
    for plan, node in ((sequence.own, "broke"), (sequence.boarded, "first")):
        review = sequence.reviews[plan]
        assert _framed(_project_turn(review, plan)), "the project was not judged as a plan"
        assert _framed(_turn_of(review, node)), f"{node} was not judged as a plan"
        assert _framed(_turn_of(review, "amended")), "the amended task was not judged as a plan"


def test_no_judge_reads_a_run_written_key(sequence: Sequence) -> None:
    for review in sequence.reviews.values():
        carrying = {text[:120]: _carries_run_written_key(text) for text in review.texts()}
        assert not {text: keys for text, keys in carrying.items() if keys}, carrying
        assert review.batches, "no checklist batch read the judged files"


def test_each_amendment_reaches_the_reviewer_verbatim_as_the_runs_applied_record(
    sequence: Sequence,
) -> None:
    """After the task's body, under a heading and opening presenting it as criteria the run
    added by a live edit, part of the task's bar and not a ruling the plan records."""
    for plan, text in zip((sequence.own, sequence.boarded), AMENDMENTS, strict=True):
        prompt = str(_turn_of(sequence.reviews[plan], "amended").get("prompt"))
        assert prompt.count(AMENDMENT_HEADING) == 1, prompt[-900:]
        body, section = prompt.split(AMENDMENT_HEADING, 1)
        assert "WHAT-amended)" in body, prompt[-900:]
        assert section.lstrip().startswith(AMENDMENT_RECORD), section[:600]
        assert f"\n\n{text}\n" in section, section[:600]


def test_the_review_leaves_the_runs_own_keys_where_it_found_them(sequence: Sequence) -> None:
    assert sequence.own_after == sequence.own_before
    assert all(sequence.own_before.values()), sequence.own_before


def test_new_run_written_values_after_a_pass_re_open_nothing(sequence: Sequence) -> None:
    again = sequence.rereview
    assert again.ran.returncode == 0, again.ran.stdout + again.ran.stderr
    assert again.turns == [] and again.batches == [], [
        str(turn.get("prompt"))[:200] for turn in again.turns
    ]


SETTLED_PLANS = ["boarded_settled", "own_settled"]


@pytest.mark.parametrize("which", SETTLED_PLANS)
def test_after_the_bar_moves_the_gate_refuses_every_unsettled_task_and_no_settled_one(
    sequence: Sequence, which: str
) -> None:
    """On the board-copied plan `stale` is among them, its authoring copy's settlement being
    stale, and on both `reopened`, settled `done` once and no longer."""
    settled: Settled = getattr(sequence, which)
    assert settled.gate_before == sorted(UNSETTLED), settled.gate_before
    assert settled.gate_before_project


@pytest.mark.parametrize("which", SETTLED_PLANS)
def test_the_review_judges_every_unsettled_task_and_refuses_the_defect_with_its_project(
    sequence: Sequence, which: str
) -> None:
    settled: Settled = getattr(sequence, which)
    review = settled.refused
    assert review.ran.returncode == 1, review.ran.stdout + review.ran.stderr
    assert JUDGE_REFUSAL in review.ran.stderr, review.ran.stderr
    assert [node for node in (*SETTLED, *UNSETTLED) if review.judged(node)] == list(UNSETTLED)
    assert settled.gate_refused == ["open"], settled.gate_refused
    assert settled.gate_refused_project


@pytest.mark.parametrize("which", SETTLED_PLANS)
def test_no_task_settled_done_is_judged_whatever_its_record_and_text(
    sequence: Sequence, which: str
) -> None:
    settled: Settled = getattr(sequence, which)
    for review in (settled.refused, settled.repaired):
        assert not [node for node in SETTLED if review.judged(node)], [
            str(turn.get("prompt"))[:300] for turn in review.turns
        ]


@pytest.mark.parametrize("which", SETTLED_PLANS)
def test_a_description_is_attested_beside_a_settled_task_carrying_the_defect(
    sequence: Sequence, which: str
) -> None:
    settled: Settled = getattr(sequence, which)
    review = settled.repaired
    assert review.ran.returncode == 0, review.ran.stdout + review.ran.stderr
    assert [node for node in (*SETTLED, *UNSETTLED) if review.judged(node)] == ["open"]
    turn = _project_turn(review, settled.plan)
    assert _framed(turn) and DELIVERED_FRAMING in _composed(turn)
    assert settled.project_state == "valid"
    assert settled.gate_after == [] and not settled.gate_after_project, settled.gate_after


def test_a_plan_quality_defect_is_still_refused(sequence: Sequence) -> None:
    defect = sequence.defect
    assert defect.ran.returncode == 1, defect.ran.stdout + defect.ran.stderr
    assert JUDGE_REFUSAL in defect.ran.stderr, defect.ran.stderr
    flawed, sound = sequence.defect_tasks["flawed"], sequence.defect_tasks["sound"]
    assert sequence.defect_records[flawed]["state"] != "valid", sequence.defect_records
    assert sequence.defect_records[sound]["state"] == "valid", sequence.defect_records
    assert sequence.defect_project != "valid"
    assert _framed(_turn_of(defect, "flawed"))


def test_a_description_carrying_the_defect_is_still_refused(sequence: Sequence) -> None:
    described = sequence.described
    assert described.ran.returncode == 1, described.ran.stdout + described.ran.stderr
    assert {one["state"] for one in sequence.described_records.values()} == {"valid"}
    assert sequence.described_project != "valid"


@pytest.mark.parametrize("which", SETTLED_PLANS)
def test_a_withdrawn_task_is_never_judged_or_attested_and_the_gate_never_names_it(
    sequence: Sequence, which: str
) -> None:
    """Carrying the defect, it is read by no judge, in its own file or in the description's
    `tasks`, while `open`, which carries it too and is not withdrawn, is refused with the
    description unjudged; once `open` is repaired the description is judged and attested."""
    settled: Settled = getattr(sequence, which)
    listed = f'"title":"feat: {WITHDRAWN}"'
    for review in (settled.refused, settled.repaired):
        assert not review.judged(WITHDRAWN), [
            str(turn.get("prompt"))[:300] for turn in review.turns
        ]
        assert not [text[:300] for text in review.texts() if listed in text]
    native = settled.plan.split(":", 1)[1]
    assert not [
        turn
        for turn in settled.refused.turns
        if f'"title":"Health route {native}"' in str(turn.get("prompt"))
    ], "the description was judged beside a refused task"
    assert settled.refused.judged("open")
    assert settled.repaired.ran.returncode == 0, settled.repaired.ran.stderr
    assert _framed(_project_turn(settled.repaired, settled.plan))
    assert settled.project_state == "valid"
    assert settled.records[settled.tasks[WITHDRAWN]]["state"] == "absent", settled.records
    assert WITHDRAWN not in settled.gate_before + settled.gate_refused + settled.gate_after
