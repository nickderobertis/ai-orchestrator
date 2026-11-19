"""Where a plan's run wrote cannot be read, the review judges nothing and the launch gate refuses.

`just review-plan` and the launch gate `scripts/plan-attestation-check.sh` each read what a run
wrote of a plan from the one board copy the project's `onetaskgraph.copies` names, or from the
plan's own tasks when it names none, and the review leaves every key the engine's write-back
owns out of what it attests. When any of that cannot be read — a
copy link that is not an object of ids, a link naming two destinations, a board copy the store
cannot list, a task's link naming no item the board holds, a settlement that is not an object
with a text status — reading the plan as if it had no run state would judge, or pass, a task
under the wrong settlement, so both stop with exit 2 and a diagnostic naming what to repair.
The review alone stops on an amendment that is not text, which only its judges read; its
refusal of an engine answering no run-written keys is
`tests/e2e/registry_and_recipes/test_delegated_recipes_e2e.py`'s, which doubles the engine.
The review does the same, carrying the store's structured failure, for a project the store
cannot show, and for a pass the store cannot record, naming the task or project and the
re-review.

One bench (`planning_bench`), one small plan per case built through this host's own templates
and `just copy-plan`, and the malformed value planted in the store's own Markdown front
matter: the store refuses to write its `onetaskgraph.` namespace through its verbs, which is
exactly why a damaged link is a hand edit's or another tool's to leave behind. Each case then
drives the real `just review-plan` and the engine's real `plan check` with the checks
`config/onepipeline.launch.yaml` registers, as a launch runs them. Only the paid model is
doubled, and no judge is reached.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml
from planning_bench import (
    BOARD,
    Bench,
    bench,
    judge_inputs,
    judge_only_turns,
    just,
    run,
    store_json,
)
from test_replan_review_e2e import _board_task, _copied, _project, _set, _task

from orchestrator.root import REPO_ROOT

pytestmark = [
    pytest.mark.usefixtures("module_tmpdir"),
    pytest.mark.xdist_group("replan-read-refusals"),
]

REFUSED = 2


def _front_matter(path: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    """Apply ``edit`` to the metadata of the local-md record at ``path``."""
    text = path.read_text(encoding="utf-8")
    _, head, body = text.split("---\n", 2)
    fields = yaml.safe_load(head)
    metadata = fields.setdefault("metadata", {})
    edit(metadata)
    dumped = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True)
    path.write_text(f"---\n{dumped}---\n{body}", encoding="utf-8")


def _record(bench_: Bench, kind: str, qualified: str) -> Path:
    """The file the plan store keeps ``qualified`` in, as it answers."""
    shown = store_json(bench_, kind, "show", qualified)
    items = shown["items"]
    assert isinstance(items, list) and len(items) == 1, shown
    return Path(items[0]["item"]["location"]["path"])


class Case(NamedTuple):
    """One unreadable plan: the review's and the gate's answers, and what the judges saw."""

    review: subprocess.CompletedProcess[str]
    gate: subprocess.CompletedProcess[str]
    judged: int


class Plan(NamedTuple):
    plan: str
    task: str


def _plan(bench_: Bench, native: str, *, boarded: bool) -> Plan:
    plan = _project(bench_, native)
    task = _task(bench_, plan, "only")
    if boarded:
        _copied(bench_, plan)
    return Plan(plan, task)


def _gate(bench_: Bench, plan: str) -> subprocess.CompletedProcess[str]:
    """The engine's `plan check` over ``plan`` with the launch's registered checks."""
    registered = yaml.safe_load(
        (REPO_ROOT / "config" / "onepipeline.launch.yaml").read_text("utf-8")
    )
    return run(
        bench_._replace(environment={**bench_.environment, "ORCHESTRATOR_PLAN": plan}),
        "scripts/onepipeline.sh",
        *("plan", "check", plan, "--json"),
        *[word for (check,) in registered["checks"] for word in ("--check", check)],
        seconds=300,
    )


def _judged(bench_: Bench) -> int:
    return len(judge_inputs(bench_)) + len(judge_only_turns(bench_))


def _case(bench_: Bench, plan: str) -> Case:
    before = _judged(bench_)
    review = just(bench_, "review-plan", plan)
    gate = _gate(bench_, plan)
    return Case(review, gate, _judged(bench_) - before)


#: Each case: the review's diagnostic, then the gate's (None where the gate reads no such thing).
EXPECTED: dict[str, tuple[str, str | None]] = {
    "project copies not an object": (
        "onetaskgraph.copies is not an object naming each destination's copy by a non-empty id",
        "whose onetaskgraph.copies names each destination's copy by a non-empty id",
    ),
    "board copy unlistable": (
        "reported errors or a further page, so nothing was judged",
        "could not list the board copy of",
    ),
    "two destinations": (
        "so no one board copy holds what its run wrote; review the plan",
        "so no one board copy holds what its run wrote; launch the plan",
    ),
    "task copies not an object": (
        "holds an onetaskgraph.copies that is not an object",
        "holds an onetaskgraph.copies that is not an object",
    ),
    "task copy absent from the board": (
        "which the board listing does not hold exactly once",
        "which the board listing does not hold exactly once",
    ),
    "amendment not text": ("holds an amendment that is not text", None),
    "settlement not an object": (
        "holds an onepipeline.settlement that is not an object with a text status",
        "holds an onepipeline.settlement that is not an object with a text status",
    ),
    "board settlement without status": (
        "holds an onepipeline.settlement that is not an object with a text status",
        "holds an onepipeline.settlement that is not an object with a text status",
    ),
    "project absent": (
        "exited 1 and did not answer one project without errors, so nothing was judged: "
        '{"class":"refused","kind":"no-such-item"',
        None,
    ),
}


@contextmanager
def _unwritable(record: Path) -> Iterator[None]:
    """Hold ``record`` and its directory read-only, so the store can read it and not write it."""
    modes = [(path, path.stat().st_mode) for path in (record, record.parent)]
    for path, mode in modes:
        path.chmod(mode & ~0o222)
    try:
        yield
    finally:
        for path, mode in modes:
            path.chmod(mode)


@pytest.fixture(scope="module")
def cases(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[dict[str, Case]]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("replan-read-refusals")
    bench_ = bench(tmp, oneharness_bin, "e2e-replan-read-refusals")
    tag = os.getpid()
    found: dict[str, Case] = {}
    found["project absent"] = _case(bench_, f"authoring:absent-{tag}")

    plan = _plan(bench_, f"proj-copies-{tag}", boarded=False).plan
    _front_matter(
        _record(bench_, "project", plan),
        lambda metadata: metadata.update({"onetaskgraph.copies": "board:somewhere"}),
    )
    found["project copies not an object"] = _case(bench_, plan)

    plan = _plan(bench_, f"unlistable-{tag}", boarded=False).plan
    _front_matter(
        _record(bench_, "project", plan),
        lambda metadata: metadata.update({"onetaskgraph.copies": {"nowhere": "nowhere:gone"}}),
    )
    found["board copy unlistable"] = _case(bench_, plan)

    plan = _plan(bench_, f"two-{tag}", boarded=True).plan
    _front_matter(
        _record(bench_, "project", plan),
        lambda metadata: metadata["onetaskgraph.copies"].update({"examples": "examples:other"}),
    )
    found["two destinations"] = _case(bench_, plan)

    boarded = _plan(bench_, f"task-copies-{tag}", boarded=True)
    _front_matter(
        _record(bench_, "task", boarded.task),
        lambda metadata: metadata.update({"onetaskgraph.copies": [f"{BOARD}:x"]}),
    )
    found["task copies not an object"] = _case(bench_, boarded.plan)

    boarded = _plan(bench_, f"absent-{tag}", boarded=True)
    _front_matter(
        _record(bench_, "task", boarded.task),
        lambda metadata: metadata["onetaskgraph.copies"].update({BOARD: f"{BOARD}:not-on-board"}),
    )
    found["task copy absent from the board"] = _case(bench_, boarded.plan)

    boarded = _plan(bench_, f"amendment-{tag}", boarded=True)
    _set(bench_, "task", _board_task(bench_, boarded.task), {"onepipeline.amendment": {"to": 1}})
    found["amendment not text"] = _case(bench_, boarded.plan)

    own = _plan(bench_, f"settlement-{tag}", boarded=False)
    _set(bench_, "task", own.task, {"onepipeline.settlement": "done"})
    found["settlement not an object"] = _case(bench_, own.plan)

    boarded = _plan(bench_, f"board-settlement-{tag}", boarded=True)
    _set(
        bench_,
        "task",
        _board_task(bench_, boarded.task),
        {"onepipeline.settlement": {"landing": "x"}},
    )
    found["board settlement without status"] = _case(bench_, boarded.plan)

    # A pass the store cannot record: the record is readable, so the judge is reached and
    # passes it, and the attestation write is what fails.
    unwritten = _plan(bench_, f"unwritten-task-{tag}", boarded=False)
    with _unwritable(_record(bench_, "task", unwritten.task)):
        found["task attestation unwritable"] = _case(bench_, unwritten.plan)
    unwritten = _plan(bench_, f"unwritten-project-{tag}", boarded=False)
    with _unwritable(_record(bench_, "project", unwritten.plan)):
        found["project attestation unwritable"] = _case(bench_, unwritten.plan)
    yield found


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_an_unreadable_run_record_stops_the_review_unjudged(
    cases: dict[str, Case], name: str
) -> None:
    case, (diagnostic, _) = cases[name], EXPECTED[name]
    assert case.review.returncode == REFUSED, case.review.stdout + case.review.stderr
    assert diagnostic in case.review.stderr, case.review.stderr
    assert case.judged == 0


@pytest.mark.parametrize("name", sorted(name for name, (_, gate) in EXPECTED.items() if gate))
def test_an_unreadable_run_record_stops_the_launch_gate(cases: dict[str, Case], name: str) -> None:
    case, (_, diagnostic) = cases[name], EXPECTED[name]
    assert diagnostic is not None
    answer = json.loads(case.gate.stdout)
    unrunnable = json.dumps(answer["unrunnable"])
    assert "plan-attestation-check" in unrunnable, case.gate.stdout + case.gate.stderr
    assert diagnostic in unrunnable, unrunnable


@pytest.mark.parametrize(
    ("name", "diagnostic"),
    [
        ("task attestation unwritable", "passed its review, but the plan store did not record"),
        (
            "project attestation unwritable",
            "description passed its review, but the plan store did not record",
        ),
    ],
)
def test_a_pass_the_store_cannot_record_stops_the_review_naming_the_retry(
    cases: dict[str, Case], name: str, diagnostic: str
) -> None:
    case = cases[name]
    assert case.review.returncode == REFUSED, case.review.stdout + case.review.stderr
    assert diagnostic in case.review.stderr, case.review.stderr
    assert "then re-run 'just review-plan authoring:unwritten-" in case.review.stderr, (
        case.review.stderr
    )
    assert case.judged > 0
