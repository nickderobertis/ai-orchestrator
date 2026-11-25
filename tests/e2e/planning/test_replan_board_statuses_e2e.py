"""A replan reads each task's status from its board copy, and the review and gate skip by it.

One bench on the loopback GitHub Projects board `tests/github_board.py` serves as the `plans`
source, the real installed store and engine driving it and only the paid model doubled. Each
plan is rendered from this host's templates and copied onto the board; then, on the board, a
person sets one task Done with no run settlement and closes another as not planned, and a
live `add` creates a board-only task depending on an existing one. Run-written keys stand on
the authoring copy, as a hand once put there.

* **`just review-plan`** refreshes first: every authoring task takes its board task's status,
  the board-only task is brought in with its edge on the authoring counterpart, every
  run-written key is stripped, and titles, bodies and template answers are untouched. The
  review judges the board-only task and neither the Done nor the cancelled one, though
  neither holds a review, and the launch gate holds neither.
* **`just finish-plan`**, over a second plan set up the same way, starts with the same
  refresh, and leaves the same authoring copy.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import engine_writeback_keys
import onetaskgraph_user_layer
import pytest
from drafting import DESIGN_MARKER, design_commands
from github_board import _serving_board
from planning_bench import (
    Bench,
    bench,
    judge_only_turns,
    just,
    mark,
    run,
    runs_of,
    store,
    store_json,
)

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("replan-board")]

PLANS = "plans"
STORE = ".venv/bin/onetaskgraph"
RESOLVE = "scripts/onepipeline.sh template resolve"
#: The authoring tasks: one a person marks Done, one they withdraw, two that stay open.
NODES = ("delivered", "withdrawn", "base", "kept")
#: Each task's body names it, so a judged prompt says which task it judged.
MARKER = "BODY-OF-{}"
PROBE = "BODY-OF-probe"
LOADED_PLAN_DUMP = """#!/usr/bin/env bash
cat > "$1"
printf '{"refusals": []}\\n'
"""


def _sh(bench_: Bench, script: str) -> str:
    done = run(bench_, "bash", "-c", f"set -euo pipefail; {script}", seconds=600)
    assert done.returncode == 0, f"{script[:400]}\n{done.stdout[-2000:]}{done.stderr[-2000:]}"
    return done.stdout


def _listed(bench_: Bench, source: str, project: str) -> list[dict[str, Any]]:
    items = store_json(bench_, "task", "list", "--source", source, "--project", project, "--all")[
        "items"
    ]
    assert isinstance(items, list), items
    return items


def _shown(bench_: Bench, kind: str, item: str) -> dict[str, Any]:
    items = store_json(bench_, kind, "show", item)["items"]
    assert isinstance(items, list) and len(items) == 1, items
    found = items[0]["item"]
    assert isinstance(found, dict), items
    return found


def _ok(done: subprocess.CompletedProcess[str]) -> None:
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]


def _set(bench_: Bench, kind: str, item: str, key: str, value: object) -> None:
    _ok(store(bench_, kind, "metadata", "set", item, key, json.dumps(value)))


def _bar(bench_: Bench) -> str:
    answered = run(
        bench_,
        ".venv/bin/oneagentgraph",
        *("run", "graphs/planner.yaml", "--judge-only", "--member", "worker", "--bar-fingerprint"),
    )
    _ok(answered)
    return answered.stdout.strip()


def _attest(bench_: Bench, kind: str, item: str, bar: str) -> None:
    _ok(
        store(
            bench_,
            *(kind, "attest", item, "--kind", "plan-review", "--by", "review-plan", "--bar", bar),
            *engine_writeback_keys.excludes(),
        )
    )


def _answers(bench_: Bench, task: str) -> str:
    answered = store(bench_, "task", "answers", task, "--json")
    _ok(answered)
    return answered.stdout


class Authored(NamedTuple):
    """What a person wrote of one task, which the refresh must leave as it found it."""

    title: str
    content: str
    answers: str


class Setup(NamedTuple):
    plan: str
    tasks: dict[str, str]
    board_task: str
    statuses: dict[str, str]
    authored: dict[str, Authored]


def _setup(bench_: Bench, native: str, bar: str) -> Setup:
    """A plan copied onto the board, then moved on the board by a person and a live `add`."""
    plan = f"authoring:{native}"
    goal = json.dumps({"text": "Give the service a health route operators can poll"})
    _sh(
        bench_,
        f"{RESOLVE} plan-description --json | {STORE} project create authoring --id {native} "
        f"--title {shlex.quote('Health route ' + native)} --template-loader - "
        "--var 'overview=Add a health route.' --metadata onepipeline.schema_version=3 "
        f"--metadata {shlex.quote('onepipeline.goal=' + goal)} --no-interactive >/dev/null",
    )
    tasks: dict[str, str] = {}
    for node in NODES:
        created = _sh(
            bench_,
            f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {plan} "
            f"--title {shlex.quote('feat: ' + node)} --template-loader - "
            f"--var 'what={MARKER.format(node)}: build the {node} part.' "
            "--var 'why=Operators need it.' "
            f"--var 'acceptance_criteria=[\"A request-level test drives {node}.\"]' "
            f"--metadata 'onepipeline.id={json.dumps(node)}' "
            "--metadata 'onepipeline.persona=\"engineer\"' "
            "--metadata 'orchestrator.unit=\"API\"' --no-interactive --json",
        )
        tasks[node] = str(json.loads(created)["items"][0]["id"])
    # The two open tasks and the project hold a passing review; the two the board moves hold
    # none, so only their status can keep them from being judged or held.
    for node in ("base", "kept"):
        _attest(bench_, "task", tasks[node], bar)
    _attest(bench_, "project", plan, bar)
    _ok(just(bench_, "copy-plan", plan))
    board = str(_shown(bench_, "project", plan)["metadata"]["onetaskgraph.copies"][PLANS])
    on_board = {
        node: str(_shown(bench_, "task", task)["metadata"]["onetaskgraph.copies"][PLANS])
        for node, task in tasks.items()
    }
    # A person marks one Done with no run settlement and withdraws another.
    _ok(store(bench_, "task", "update", on_board["delivered"], "--status", "done"))
    _ok(store(bench_, "task", "update", on_board["withdrawn"], "--status", "cancelled"))
    # A live `add` creates a board-only task depending on an existing one.
    added = _sh(
        bench_,
        f"printf '## What\\n\\n{PROBE}: probe the route.\\n\\n## Acceptance criteria\\n\\n"
        f"- It answers.\\n' > .added.md && {STORE} task create {PLANS} --project {board} "
        "--title 'feat: probe' --body-file .added.md --metadata 'onepipeline.id=\"probe\"' "
        "--metadata 'onepipeline.persona=\"engineer\"' "
        f"--metadata 'onepipeline.settlement={json.dumps({'status': 'failed'})}' "
        f"--depends-on {on_board['base']} --no-interactive --json; rm -f .added.md",
    )
    board_task = str(json.loads(added)["items"][0]["id"])
    # Run-written keys on the authoring copy, as a hand once put there.
    _set(bench_, "task", tasks["kept"], "onepipeline.change_url", "https://example.invalid/1")
    _set(bench_, "project", plan, "onepipeline.driver", {"session": "a-manager"})
    statuses = {
        node: str(_shown(bench_, "task", on_board[node])["status"]["category"]) for node in NODES
    }
    authored = {
        node: Authored(str(found["title"]), str(found["content"]), _answers(bench_, task))
        for node, task in tasks.items()
        for found in (_shown(bench_, "task", task),)
    }
    return Setup(plan, tasks, board_task, statuses, authored)


class TaskCopy(NamedTuple):
    """One authoring task as the plan holds it, by what a refresh may and may not move."""

    title: str
    content: str
    status: str
    depends_on: list[str]
    metadata: dict[str, Any]


class PlanCopy(NamedTuple):
    """The authoring copy: the project's metadata and each task by its node id."""

    project: dict[str, Any]
    tasks: dict[str, TaskCopy]


def _copy(bench_: Bench, plan: str) -> PlanCopy:
    """The authoring copy as the plan holds it, each record's metadata less the store's own
    keys, and each task's dependencies as the node ids it depends on."""
    listed = _listed(bench_, "authoring", plan)
    nodes = {str(one["id"]): str(one["item"]["metadata"].get("onepipeline.id")) for one in listed}
    graph = json.loads(
        _sh(bench_, f"{STORE} project graph {plan} --format json --complete --no-interactive")
    )

    def authored(metadata: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in metadata.items() if not k.startswith("onetaskgraph.")}

    return PlanCopy(
        authored(_shown(bench_, "project", plan)["metadata"]),
        {
            nodes[str(one["id"])]: TaskCopy(
                str(one["item"]["title"]),
                str(one["item"]["content"]),
                str(one["item"]["status"]["category"]),
                sorted(
                    nodes.get(str(edge["from"]), str(edge["from"]))
                    for edge in graph["edges"]
                    if edge["to"] == one["id"]
                ),
                authored(one["item"]["metadata"]),
            )
            for one in listed
        },
    )


def _gate(bench_: Bench, plan: str) -> list[str]:
    """The launch gate's refusals over the plan the engine's loader loads from ``plan``."""
    dump = bench_.tmp / "loaded-plan-dump.sh"
    loaded = bench_.tmp / "loaded-plan.json"
    dump.write_text(LOADED_PLAN_DUMP.replace('"$1"', shlex.quote(str(loaded))), encoding="utf-8")
    dump.chmod(0o755)
    checked = run(bench_, "scripts/onepipeline.sh", "plan", "check", plan, "--check", str(dump))
    assert loaded.is_file(), checked.stdout + checked.stderr
    gated = run(
        bench_, "scripts/plan-attestation-check.sh", input_text=loaded.read_text(encoding="utf-8")
    )
    _ok(gated)
    return [str(one["reason"]) for one in json.loads(gated.stdout)["refusals"]]


def _board_bench(tmp: Path, oneharness_bin: str, session: str, remote: dict[str, str]) -> Bench:
    """A planning bench in ``tmp`` whose `plans` source is the loopback board ``remote``."""
    home = onetaskgraph_user_layer.installed(tmp / "config-home", {"sources": {}})
    return bench(
        tmp,
        oneharness_bin,
        session,
        **remote,
        XDG_CONFIG_HOME=str(home),
        ONETASKGRAPH_SOURCES__PLANS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS="0",
    )


class Journey(NamedTuple):
    reviewed: Setup
    review: subprocess.CompletedProcess[str]
    judged: list[str]
    after_review: PlanCopy
    answers_after: dict[str, str]
    refusals: list[str]
    finished: Setup
    finish: subprocess.CompletedProcess[str]
    after_finish: PlanCopy


@pytest.fixture(scope="module")
def journey(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Journey]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("replan-board")
    with _serving_board() as remote:
        bench_ = _board_bench(tmp, oneharness_bin, "e2e-replan-board", remote)
        bar = _bar(bench_)
        tag = os.getpid()
        reviewed = _setup(bench_, f"board-reviewed-{tag}", bar)
        judged_before = len(judge_only_turns(bench_))
        review = just(bench_, "review-plan", reviewed.plan)
        judged = [str(turn.get("prompt")) for turn in judge_only_turns(bench_)[judged_before:]]
        after_review = _copy(bench_, reviewed.plan)
        answers_after = {node: _answers(bench_, task) for node, task in reviewed.tasks.items()}
        refusals = _gate(bench_, reviewed.plan)
        native = f"board-finished-{tag}"
        finished = _setup(bench_, native, bar)
        mark(bench_, DESIGN_MARKER, *design_commands(native))
        finish = just(bench_, "finish-plan", finished.plan)
        after_finish = _copy(bench_, finished.plan)
        yield Journey(
            reviewed,
            review,
            judged,
            after_review,
            answers_after,
            refusals,
            finished,
            finish,
            after_finish,
        )
        for one in runs_of(bench_):
            if not one.startswith("."):
                just(bench_, "stop", one, seconds=120)


def test_the_refresh_brings_every_board_status_and_the_board_only_task_in(
    journey: Journey,
) -> None:
    _ok(journey.review)
    tasks = journey.after_review.tasks
    assert sorted(tasks) == sorted((*NODES, "probe")), tasks
    for node in NODES:
        assert tasks[node].status == journey.reviewed.statuses[node], node
    assert (tasks["delivered"].status, tasks["withdrawn"].status) == ("done", "cancelled")
    assert tasks["probe"].depends_on == ["base"], tasks["probe"]
    assert PROBE in tasks["probe"].content


def test_the_refresh_strips_every_run_written_key_and_leaves_the_authored_text(
    journey: Journey,
) -> None:
    keys = set(engine_writeback_keys.run_written_keys())
    copy = journey.after_review
    assert keys.isdisjoint(copy.project), copy.project
    for node, task in copy.tasks.items():
        assert keys.isdisjoint(task.metadata), (node, task.metadata)
    for node, authored in journey.reviewed.authored.items():
        task = copy.tasks[node]
        assert (task.title, task.content) == (authored.title, authored.content), node
        assert journey.answers_after[node] == authored.answers, node


def test_the_review_judges_neither_the_done_nor_the_cancelled_task(journey: Journey) -> None:
    flat = " ".join(journey.judged)
    assert PROBE in flat, "the board-only task, which holds no review, was not judged"
    for node in ("delivered", "withdrawn"):
        assert MARKER.format(node) not in flat, node


def test_the_launch_gate_holds_neither_the_done_nor_the_cancelled_task(journey: Journey) -> None:
    for node in ("delivered", "withdrawn"):
        task = journey.reviewed.tasks[node]
        assert not [reason for reason in journey.refusals if task in reason], journey.refusals
    assert not [r for r in journey.refusals if "plan-review" in r], journey.refusals


def test_finish_plans_refresh_step_leaves_the_same_authoring_copy(journey: Journey) -> None:
    def normalized(copy: PlanCopy, native: str) -> str:
        return json.dumps(copy, sort_keys=True).replace(native, "<plan>")

    assert normalized(journey.after_finish, journey.finished.plan.split(":", 1)[1]) == (
        normalized(journey.after_review, journey.reviewed.plan.split(":", 1)[1])
    ), journey.finish.stdout[-2000:] + journey.finish.stderr[-2000:]


class Successive(NamedTuple):
    """What a board-status journey saw after an earlier journey in its process planned `route`."""

    route_copies: dict[str, str]
    base_copies: dict[str, str]
    refresh: subprocess.CompletedProcess[str]
    tasks: dict[str, TaskCopy]
    refusals: list[str]
    earlier_plan_shown: subprocess.CompletedProcess[str]


@pytest.fixture(scope="module")
def successive(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Successive:
    """An earlier journey copies a `route` plan onto its board; a later one then refreshes.

    The loopback board numbers its issues afresh for every journey, so the earlier journey's
    `route` task and the later journey's `base` task are copied onto their boards under the
    same issue id. Both journeys run in this one process, through the benches every journey
    here uses.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("replan-board-successive")
    tag = os.getpid()
    route = f"authoring:earlier-{tag}"
    with _serving_board() as remote:
        earlier = _board_bench(tmp / "earlier", oneharness_bin, "e2e-earlier", remote)
        bar = _bar(earlier)
        _sh(
            earlier,
            f"{RESOLVE} plan-description --json | {STORE} project create authoring "
            f"--id earlier-{tag} --title 'Health route' --template-loader - "
            "--var 'overview=Add a health route.' --metadata onepipeline.schema_version=3 "
            f"--no-interactive >/dev/null; {RESOLVE} plan-task --json | {STORE} task create "
            f"authoring --project {route} --title 'feat: add the health route' "
            "--template-loader - --var 'what=Do route.' --var 'why=Operators need it.' "
            "--var 'acceptance_criteria=[\"A request-level test drives the route.\"]' "
            "--metadata 'onepipeline.id=\"route\"' "
            "--metadata 'onepipeline.persona=\"engineer\"' "
            "--metadata 'orchestrator.unit=\"API\"' --no-interactive >/dev/null",
        )
        (routed,) = _listed(earlier, "authoring", route)
        _attest(earlier, "task", str(routed["id"]), bar)
        _attest(earlier, "project", route, bar)
        _ok(just(earlier, "copy-plan", route))
        route_copies = _shown(earlier, "task", str(routed["id"]))["metadata"]
    with _serving_board() as remote:
        later = _board_bench(tmp / "later", oneharness_bin, "e2e-later", remote)
        setup = _setup(later, f"later-{tag}", bar)
        base_copies = _shown(later, "task", setup.tasks["base"])["metadata"]
        refresh = just(later, "refresh-plan", setup.plan)
        tasks = _copy(later, setup.plan).tasks
        refusals = _gate(later, setup.plan)
        shown = store(later, "project", "show", route, "--json")
    return Successive(
        route_copies["onetaskgraph.copies"],
        base_copies["onetaskgraph.copies"],
        refresh,
        tasks,
        refusals,
        shown,
    )


def test_a_journey_reads_no_plan_an_earlier_journey_in_its_process_left(
    successive: Successive,
) -> None:
    """Had both journeys' authoring copies shared one root, the refresh would resolve each
    board id to whichever authoring record copied there first — the earlier journey's: its
    project, filing `probe` in the earlier plan, or its `route` task, so the launch gate
    refuses `probe` as depending on a `route` its plan does not hold."""
    # The precondition that makes a shared root leak: one board id on both journeys' tasks.
    assert successive.base_copies == successive.route_copies, successive
    unseen = successive.earlier_plan_shown
    assert unseen.returncode != 0, "the later journey's store holds the earlier one's plan"
    assert json.loads(unseen.stdout)["failure"]["kind"] == "no-such-item", unseen.stdout
    _ok(successive.refresh)
    tasks = successive.tasks
    assert sorted(tasks) == sorted((*NODES, "probe")), tasks
    assert tasks["probe"].depends_on == ["base"], tasks["probe"]
    assert not [r for r in successive.refusals if "route" in r], successive.refusals
