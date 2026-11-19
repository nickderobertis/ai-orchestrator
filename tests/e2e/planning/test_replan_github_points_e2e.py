"""What one replan of a typical board-copied plan spends of GitHub's shared GraphQL points.

Budget `replan-github-points` (`tests/e2e/planning/budgets.yaml`). One replan of a plan shaped
like this host's print-stack plan, generated here and never copied from the real board: 27
authoring tasks (17 done, 9 todo, 1 cancelled) reviewed with whole-list records and copied
onto the `plans` board, whose copy then gains what a run writes — 4 newer statuses and 5
amendments — while 3 authoring tasks hold stale run-written keys. The board is the loopback
GitHub Projects board `tests/github_board.py` serves as the `plans` source, with no real
credential, under a onetaskgraph user layer of the journey's own so no host source is built;
the store driving it is the real installed one, the `plans` source's cache enabled as this
host's `onetaskgraph.yaml` enables it. Only the paid model is doubled, as on every
planning bench.

The workload has no task a live `add` created: the pinned store cannot bring one from a
GitHub Projects board into its authoring project (a members-only copy back finds no
counterpart project), so importing one is not measured until the store supports it (manager
ruling a1). The same board instead proves the refresh refuses such a task before writing
anything, naming it and what a person does next.

The replan's steps run through the real recipes and the launch gate, in order: `just
refresh-plan`, `just review-plan` after the replan edits 3 todo tasks, `just review-plan`
again (a retried review step), `just copy-plan`, and `scripts/plan-attestation-check.sh` over
the board copy, handed the plan the engine's loader loads as a launch hands it. Each step's
requests and points are what the board recorded while it ran, every document priced by
`REQUEST_PRICES`, an unpriced one failing the journey; the setup's requests are cleared
before each step and never counted. One `budget_telemetry` part per step is recorded, its
requests beside its points, for the planning project's `budgets` target to read.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

import budget_telemetry
import engine_writeback_keys
import onetaskgraph_user_layer
import pytest
from drafting import design_commands
from github_board import RequestCost, _GitHubFixture, _request_points, _serving_board
from planning_bench import Bench, bench, judge_only_turns, just, run, store, store_json

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("replan-points")]

BUDGET = "replan-github-points"
PLANS = "plans"
STORE = ".venv/bin/onetaskgraph"
RESOLVE = "scripts/onepipeline.sh template resolve"
#: The workload: the authoring tasks by status, and what the run wrote on the board since.
DONE, TODO, CANCELLED = 17, 9, 1
NEWER_STATUSES, AMENDMENTS, STALE, EDITED = 4, 5, 3, 3
#: Each measured step, in the order a replan takes them, as its telemetry part is labelled.
STEPS = ("refresh", "review", "review again", "copy", "launch gate")


def _sh(bench_: Bench, script: str, seconds: float = 600) -> str:
    done = run(bench_, "bash", "-c", f"set -euo pipefail; {script}", seconds=seconds)
    assert done.returncode == 0, f"{script[:400]}\n{done.stdout[-2000:]}{done.stderr[-2000:]}"
    return done.stdout


def _generated(bench_: Bench, native: str) -> tuple[str, list[str]]:
    """The workload's authoring plan, rendered from this host's templates; answers its tasks."""
    plan = f"authoring:{native}"
    goal = json.dumps({"text": "Print every run's improvements on one stack"})
    _sh(
        bench_,
        f"{RESOLVE} plan-description --json > .description.json; {RESOLVE} plan-task --json "
        f"> .task.json; {STORE} project create authoring --id {native} --title "
        f"{shlex.quote('Print-stack improvements ' + native)} --template-loader - "
        "--var 'overview=Print every run improvement on one stack.' "
        "--metadata onepipeline.schema_version=3 "
        f"--metadata {shlex.quote('onepipeline.goal=' + goal)} "
        "--no-interactive < .description.json > /dev/null; "
        f"for n in $(seq 1 {DONE + TODO + CANCELLED}); do {STORE} task create authoring "
        f'--project {plan} --title "feat: print improvement $n" --template-loader - '
        '--var "what=Print improvement $n on the stack." --var "why=Operators read the stack." '
        '--var "acceptance_criteria=[\\"A test reads improvement $n off the printed stack.\\"]" '
        '--metadata "onepipeline.id=\\"step-$n\\"" --metadata \'onepipeline.persona="engineer"\' '
        "--metadata 'orchestrator.unit=\"Print\"' --no-interactive < .task.json > /dev/null; done; "
        "rm -f .description.json .task.json",
        seconds=900,
    )
    tasks = sorted(
        (str(one["id"]) for one in _listed(bench_, "authoring", plan)),
        key=lambda task: int(task.rsplit("-", 1)[1]),
    )
    assert len(tasks) == DONE + TODO + CANCELLED, tasks
    return plan, tasks


def _listed(bench_: Bench, source: str, project: str) -> list[dict[str, Any]]:
    items = store_json(bench_, "task", "list", "--source", source, "--project", project, "--all")[
        "items"
    ]
    assert isinstance(items, list), items
    return items


def _update_many(bench_: Bench, source: str, updates: list[dict[str, Any]]) -> None:
    batch = bench_.tmp / "batch.json"
    batch.write_text(json.dumps({"schema_version": 1, "updates": updates}), encoding="utf-8")
    applied = store(bench_, "task", "update-many", source, "--input", str(batch))
    assert applied.returncode == 0, applied.stdout + applied.stderr


def _attested(bench_: Bench, items: list[tuple[str, str]], bar: str) -> None:
    """The review's whole-list `plan-review` record on each item, as a passing review leaves."""
    for kind, item in items:
        attested = store(
            bench_,
            *(kind, "attest", item, "--kind", "plan-review", "--by", "review-plan", "--bar", bar),
            *engine_writeback_keys.excludes(),
        )
        assert attested.returncode == 0, attested.stdout + attested.stderr


def _bar(bench_: Bench) -> str:
    answered = run(
        bench_,
        ".venv/bin/oneagentgraph",
        *("run", "graphs/planner.yaml", "--judge-only", "--member", "worker", "--bar-fingerprint"),
    )
    assert answered.returncode == 0, answered.stdout + answered.stderr
    return answered.stdout.strip()


def _shown(bench_: Bench, kind: str, item: str) -> dict[str, Any]:
    items = store_json(bench_, kind, "show", item)["items"]
    assert isinstance(items, list) and len(items) == 1, items
    found = items[0]["item"]
    assert isinstance(found, dict), items
    return found


def _metadata(bench_: Bench, kind: str, item: str) -> dict[str, Any]:
    found = _shown(bench_, kind, item)["metadata"]
    assert isinstance(found, dict), found
    return found


def _set(bench_: Bench, kind: str, item: str, key: str, value: object) -> None:
    answered = store(bench_, kind, "metadata", "set", item, key, json.dumps(value))
    assert answered.returncode == 0, answered.stdout + answered.stderr


class Step(NamedTuple):
    """One measured step: what ran and what the board recorded while it ran."""

    label: str
    ran: subprocess.CompletedProcess[str]
    cost: RequestCost


def _measured(label: str, step: Callable[[], subprocess.CompletedProcess[str]]) -> Step:
    """Run ``step`` with the board's record cleared first, and price what it recorded."""
    _GitHubFixture.requests.clear()
    ran = step()
    requests = list(_GitHubFixture.requests)
    cost = RequestCost(len(requests), sum(_request_points(request) for request in requests))
    _GitHubFixture.requests.clear()
    return Step(label, ran, cost)


#: A check the engine's loader hands the plan it loaded, kept for the measured gate to read:
#: what a launch hands `scripts/plan-attestation-check.sh` on stdin.
LOADED_PLAN_DUMP = """#!/usr/bin/env bash
cat > "$1"
printf '{"refusals": []}\\n'
"""


def _loaded_plan(bench_: Bench, board: str) -> str:
    """The plan the engine's loader loads from ``board``, as a launch hands its checks."""
    dump = bench_.tmp / "loaded-plan-dump.sh"
    loaded = bench_.tmp / "loaded-plan.json"
    dump.write_text(LOADED_PLAN_DUMP.replace('"$1"', shlex.quote(str(loaded))), encoding="utf-8")
    dump.chmod(0o755)
    checked = run(
        bench_, "scripts/onepipeline.sh", "plan", "check", board, "--json", "--check", str(dump)
    )
    assert loaded.is_file(), checked.stdout + checked.stderr
    return loaded.read_text(encoding="utf-8")


def _files(bench_: Bench, plan: str) -> dict[str, str]:
    """The digest of every file the plan store keeps ``plan`` and its tasks in."""
    paths = [str(_shown(bench_, "project", plan)["location"]["path"])]
    paths += [str(one["item"]["location"]["path"]) for one in _listed(bench_, "authoring", plan)]
    return {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}


class RefusedImport(NamedTuple):
    """A refresh over a plan whose board copy holds a task a live `add` created."""

    ran: subprocess.CompletedProcess[str]
    files_before: dict[str, str]
    files_after: dict[str, str]
    board_id: str
    title: str


def _refused_import(bench_: Bench, native: str) -> RefusedImport:
    """A two-task plan copied to the board, a task a live `add` created there, and a refresh."""
    plan = f"authoring:{native}"
    _sh(
        bench_,
        f"printf '## What\\n\\nPrint.\\n\\n## Acceptance criteria\\n\\n- It prints.\\n' "
        f"> .live.md && {STORE} project create authoring --id {native} --title {native} "
        "--body-file .live.md --no-interactive > /dev/null && for n in 1 2; do "
        f'{STORE} task create authoring --project {plan} --title "feat: step $n" '
        '--body-file .live.md --metadata "onepipeline.id=\\"step-$n\\"" --no-interactive '
        "> /dev/null; done",
    )
    copied = just(bench_, "copy-plan", plan)
    assert copied.returncode == 0, copied.stdout + copied.stderr
    board = str(_metadata(bench_, "project", plan)["onetaskgraph.copies"][PLANS])
    title = "feat: print the run id"
    added = _sh(
        bench_,
        f"{STORE} task create {PLANS} --project {board} --title {shlex.quote(title)} "
        "--body-file .live.md --metadata 'onepipeline.id=\"added\"' --no-interactive --json; "
        "rm -f .live.md",
    )
    board_only = str(json.loads(added)["items"][0]["id"])
    before = _files(bench_, plan)
    refused = just(bench_, "refresh-plan", plan)
    return RefusedImport(refused, before, _files(bench_, plan), board_only, title)


class Replan(NamedTuple):
    steps: list[Step]
    judged: list[str]
    board_items_before: int
    board_items_after: int
    refusals: list[str]
    refused: RefusedImport


@pytest.fixture(scope="module")
def replan(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Replan]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("replan-points")
    home = onetaskgraph_user_layer.installed(tmp / "config-home", {"sources": {}})
    with _serving_board() as remote:
        bench_ = bench(
            tmp,
            oneharness_bin,
            "e2e-replan-points",
            **remote,
            XDG_CONFIG_HOME=str(home),
            # How fast a copy writes is the pacing journey's subject, not this one's.
            ONETASKGRAPH_SOURCES__PLANS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS="0",
        )
        native = f"print-run-{os.getpid()}"
        plan, tasks = _generated(bench_, native)
        done, todo, cancelled = tasks[:DONE], tasks[DONE : DONE + TODO], tasks[DONE + TODO :]
        _update_many(
            bench_,
            "authoring",
            [{"item": task, "status": "done"} for task in done]
            + [{"item": task, "status": "cancelled"} for task in cancelled],
        )
        for line in design_commands(native):
            _sh(bench_, line)
        bar = _bar(bench_)
        _attested(bench_, [("task", task) for task in tasks] + [("project", plan)], bar)
        copied = just(bench_, "copy-plan", plan)
        assert copied.returncode == 0, copied.stdout + copied.stderr
        board = str(_metadata(bench_, "project", plan)["onetaskgraph.copies"][PLANS])
        on_board = [
            str(_metadata(bench_, "task", task)["onetaskgraph.copies"][PLANS]) for task in tasks
        ]
        boarded_todo = on_board[DONE : DONE + TODO]
        for task in boarded_todo[:NEWER_STATUSES]:
            moved = store(bench_, "task", "update", task, "--status", "in-progress")
            assert moved.returncode == 0, moved.stdout + moved.stderr
        for task in boarded_todo[NEWER_STATUSES : NEWER_STATUSES + AMENDMENTS]:
            _set(bench_, "task", task, "onepipeline.amendment", "- The stack prints its run id.")
        for task in todo[-STALE:]:
            _set(bench_, "task", task, "onepipeline.settlement", {"status": "done"})
        board_items_before = len(_listed(bench_, PLANS, board))

        steps = [_measured("refresh", lambda: just(bench_, "refresh-plan", plan))]
        for task in todo[:EDITED]:
            _sh(
                bench_,
                f"{RESOLVE} plan-task --json | {STORE} task render {task} --template-loader - "
                "--var 'what=Print the improvement, replanned.' "
                "--var 'why=Operators read the stack.' "
                "--var 'acceptance_criteria=[\"A test reads the replanned improvement.\"]' "
                "--no-interactive > /dev/null",
            )
        judged_before = len(judge_only_turns(bench_))
        steps.append(_measured("review", lambda: just(bench_, "review-plan", plan)))
        judged = [str(turn.get("prompt")) for turn in judge_only_turns(bench_)[judged_before:]]
        steps.append(_measured("review again", lambda: just(bench_, "review-plan", plan)))
        steps.append(_measured("copy", lambda: just(bench_, "copy-plan", plan)))
        loaded = _loaded_plan(bench_, board)
        gated = bench_._replace(environment={**bench_.environment, "ORCHESTRATOR_PLAN": board})
        steps.append(
            _measured(
                "launch gate",
                lambda: run(gated, "scripts/plan-attestation-check.sh", input_text=loaded),
            )
        )
        board_items_after = len(_listed(bench_, PLANS, board))
        refused = _refused_import(bench_, f"live-add-{os.getpid()}")
    gate = steps[-1].ran
    refusals = (
        [str(one["reason"]) for one in json.loads(gate.stdout)["refusals"]]
        if gate.returncode == 0
        else []
    )
    yield Replan(
        steps,
        judged,
        board_items_before,
        board_items_after,
        refusals,
        refused,
    )


def test_every_step_of_the_replan_runs_through_the_real_recipes(replan: Replan) -> None:
    for step in replan.steps:
        assert step.ran.returncode == 0, (
            step.label,
            step.ran.stdout[-1500:],
            step.ran.stderr[-3000:],
        )
    assert [step.label for step in replan.steps] == list(STEPS)
    assert replan.board_items_after == replan.board_items_before
    # Only the design approval, which this journey never records, is refused at the gate.
    assert [reason for reason in replan.refusals if "plan-review" in reason] == [], replan.refusals


def test_the_review_judges_the_edited_tasks(replan: Replan) -> None:
    replanned = [
        prompt for prompt in replan.judged if "Print the improvement, replanned." in prompt
    ]
    assert len(replanned) == EDITED, len(replanned)


def test_a_board_only_task_the_store_cannot_import_is_refused_before_any_write(
    replan: Replan,
) -> None:
    refused = replan.refused
    stderr = refused.ran.stderr
    assert refused.ran.returncode == 1, refused.ran.stdout + stderr
    assert f"{refused.board_id} ({refused.title})" in stderr, stderr
    assert "has no counterpart in authoring" in stderr, stderr
    assert "add each to" in stderr and "drop it from the run" in stderr, stderr
    assert refused.files_after == refused.files_before


def test_each_step_records_its_points_and_requests_for_the_budget(replan: Replan) -> None:
    """One counted part per step, its requests beside its points, for the budgets target."""
    for step in replan.steps:
        print(f"{step.label}: requests={step.cost.requests}, points={step.cost.points}")
        budget_telemetry.record(
            BUDGET, step.label, step.cost.points, unit="points", requests=step.cost.requests
        )
    assert all(step.cost.requests for step in replan.steps), replan.steps
