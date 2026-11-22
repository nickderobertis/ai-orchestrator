"""Where what a plan's review reads cannot be read, it judges nothing and stops at exit 2.

What a run wrote reaches the review through the store: `just review-plan` refreshes the plan
from its board copy first, and the store's filtered attestation check names the tasks to judge
by their own status, so neither the review nor the launch gate reads a settlement, a copy
listing or a destination of its own. What the review still reads itself, it refuses when it
cannot: a project the store fails to show for any reason but its absence, the project's copy
link the refresh reads its destination's copy from, a task's
amendment that is not text, a task's copy link that is not an object naming one destination's
copy, and a task's board copy the store cannot show when the task holds no amendment of its
own. Its refusal of an engine answering no run-written keys is
`tests/e2e/registry_and_recipes/test_delegated_recipes_e2e.py`'s, which doubles the engine.
The review does the same, carrying the store's structured failure, for a project the store
cannot show, and for a pass the store cannot record, naming the task or project and the
re-review.

One bench (`planning_bench`), one small plan per case built through this host's own templates
and `just copy-plan`, and the malformed value planted in the store's own Markdown front
matter: the store refuses to write its `onetaskgraph.` namespace through its verbs, which is
exactly why a damaged link is a hand edit's or another tool's to leave behind. Each case then
drives the real `just review-plan`. Only the paid model is doubled, and no judge is reached.
"""

from __future__ import annotations

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
    store_json,
)
from test_replan_review_e2e import _board_task, _copied, _project, _set, _task

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
    """One unreadable plan: the review's answer, and how many judgments it reached."""

    review: subprocess.CompletedProcess[str]
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


def _judged(bench_: Bench) -> int:
    return len(judge_inputs(bench_)) + len(judge_only_turns(bench_))


def _case(bench_: Bench, plan: str) -> Case:
    before = _judged(bench_)
    review = just(bench_, "review-plan", plan)
    return Case(review, _judged(bench_) - before)


#: Each case's diagnostic.
EXPECTED: dict[str, str] = {
    "project copies not an object": "refresh-plan: the plan store could not show",
    "project in no configured source": "refresh-plan: the plan store could not show",
    "task copy absent from the board": "as one record to read its amendment",
    "task copies two destinations": "so no one board copy holds its amendment",
    "task copies not an object": "so no one board copy holds its amendment",
    "amendment not text": "holds an amendment that is not text",
    "project absent": (
        "exited 1 and did not answer one project without errors, so nothing was judged: "
        '{"class":"refused","kind":"no-such-item"'
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
    # A failure the store reports other than an absent plan stops the refresh itself.
    found["project in no configured source"] = _case(bench_, f"nowhere:absent-{tag}")

    plan = _plan(bench_, f"proj-copies-{tag}", boarded=False).plan
    _front_matter(
        _record(bench_, "project", plan),
        lambda metadata: metadata.update({"onetaskgraph.copies": "board:somewhere"}),
    )
    found["project copies not an object"] = _case(bench_, plan)

    boarded = _plan(bench_, f"absent-{tag}", boarded=True)
    _front_matter(
        _record(bench_, "task", boarded.task),
        lambda metadata: metadata["onetaskgraph.copies"].update({BOARD: f"{BOARD}:not-on-board"}),
    )
    found["task copy absent from the board"] = _case(bench_, boarded.plan)

    boarded = _plan(bench_, f"two-copies-{tag}", boarded=True)
    _front_matter(
        _record(bench_, "task", boarded.task),
        lambda metadata: metadata["onetaskgraph.copies"].update({"examples": "examples:other"}),
    )
    found["task copies two destinations"] = _case(bench_, boarded.plan)

    boarded = _plan(bench_, f"task-copies-{tag}", boarded=True)
    _front_matter(
        _record(bench_, "task", boarded.task),
        lambda metadata: metadata.update({"onetaskgraph.copies": [f"{BOARD}:x"]}),
    )
    found["task copies not an object"] = _case(bench_, boarded.plan)

    boarded = _plan(bench_, f"amendment-{tag}", boarded=True)
    _set(bench_, "task", _board_task(bench_, boarded.task), {"onepipeline.amendment": {"to": 1}})
    found["amendment not text"] = _case(bench_, boarded.plan)

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
def test_an_unreadable_record_stops_the_review_unjudged(cases: dict[str, Case], name: str) -> None:
    case, diagnostic = cases[name], EXPECTED[name]
    assert case.review.returncode == REFUSED, case.review.stdout + case.review.stderr
    assert diagnostic in case.review.stderr, case.review.stderr
    assert case.judged == 0


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
