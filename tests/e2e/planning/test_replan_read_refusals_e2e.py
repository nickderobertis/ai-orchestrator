"""Where what a plan's review reads cannot be read, it judges nothing and stops at exit 2.

What a run wrote reaches the review through the store: `just review-plan` refreshes the plan
from its board copy first, and the store's filtered attestation check names the tasks to judge
by their own status, so neither the review nor the launch gate reads a settlement, a copy
listing or a destination of its own. What the review still reads itself, it refuses when it
cannot: a project the store fails to show for any reason but its absence, the project's copy
link the refresh reads its destination's copy from, a task's
amendment that is not text, a task's copy link that is not an object naming each destination's
copy by a qualified id, and a task's board copy the store cannot show when the task holds no
amendment of its own. Its refusal of an engine answering no run-written keys is
`tests/e2e/registry_and_recipes/test_delegated_recipes_e2e.py`'s, which doubles the engine.
The review does the same, carrying the store's structured failure, for a project the store
cannot show, and for a pass the store cannot record, naming the task or project and the
re-review.

One bench (`planning_bench`), one small plan per case built through this host's own templates
and `just copy-plan`, and the malformed value planted in the store's own Markdown front
matter: the store refuses to write its `onetaskgraph.` namespace through its verbs, which is
exactly why a damaged link is a hand edit's or another tool's to leave behind. Each case then
drives the real `just review-plan`. Only the paid model is doubled, and no judge is reached.

**A task copied to several destinations** is read, not refused: the bench's second local
source, `mirror`, takes a second real copy through `just copy-plan`, and the amendments are
set on the board copies through the store's own verbs. Reviewed for a destination, through
the real `just review-plan <plan> <destination>`, a task is judged with that destination's
copy's amendment and no other's, and refused at exit 2, unjudged, when its link holds no copy
under that key; reviewed for none, through the real script, it is judged with the amendment
its copies agree on, a copy holding none not disagreeing, and refused at exit 2, unjudged,
when two copies hold different ones. A task with one copy is judged with that copy's
amendment whatever destination is named, and every amendment-free shape is judged with none.
The planning and finishing templates, rendered for a destination, name it in their `review`
step's command.
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
from judging_codex import JudgeInput
from planning_bench import (
    BOARD,
    Bench,
    bench,
    judge_inputs,
    judge_only_turns,
    just,
    local_source,
    run,
    store_json,
)
from test_replan_review_e2e import (
    AMENDMENT_HEADING,
    _board_task,
    _copied,
    _metadata,
    _project,
    _project_state,
    _records,
    _set,
    _sh,
    _task,
)

pytestmark = [
    pytest.mark.usefixtures("module_tmpdir"),
    pytest.mark.xdist_group("replan-read-refusals"),
]

REFUSED = 2
#: The bench's second configured source, a plan's second destination.
MIRROR = "mirror"
#: A configured source of the bench that no plan here is copied to.
ELSEWHERE = "humans"
#: The amendments the board copies hold, each a text no other copy carries.
BOARD_TEXT = "- BOARD-AMENDMENT: the route answers within 50 ms."
MIRROR_TEXT = "- MIRROR-AMENDMENT: the route answers within 80 ms."
SAME_TEXT = "- SAME-AMENDMENT: the route answers with a JSON body."
ONE_SIDED_TEXT = "- ONE-SIDED-AMENDMENT: the route logs each poll."
SINGLE_TEXT = "- SINGLE-AMENDMENT: the route needs no credential."
EVERY_TEXT = (BOARD_TEXT, MIRROR_TEXT, SAME_TEXT, ONE_SIDED_TEXT, SINGLE_TEXT)


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
def planning(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Bench:
    """The module's one bench, with a second configured source a plan can be copied to."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("replan-read-refusals")
    (tmp / MIRROR).mkdir()
    planning_ = bench(tmp, oneharness_bin, "e2e-replan-read-refusals")
    planning_.environment.update(local_source(MIRROR, tmp / MIRROR))
    return planning_


@pytest.fixture(scope="module")
def cases(planning: Bench) -> Iterator[dict[str, Case]]:
    bench_ = planning
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


class Review(NamedTuple):
    """One review of a plan, with every reviewer turn and checklist batch it caused."""

    plan: str
    tasks: dict[str, str]
    ran: subprocess.CompletedProcess[str]
    turns: list[dict[str, Any]]
    batches: list[JudgeInput]
    records: dict[str, str]
    project: str
    copies: dict[str, dict[str, str]]

    def texts(self) -> list[str]:
        """Every text a judge of this review was handed."""
        return [str(turn.get("prompt")) for turn in self.turns] + [
            str(text) for batch in self.batches for text in dict(batch["read"]).values()
        ]

    def turn(self, node: str) -> str:
        """The one reviewer prompt that judged ``node``'s own content."""
        native = self.tasks[node].split(":", 1)[1]
        judged = [
            prompt
            for prompt in (str(turn.get("prompt")) for turn in self.turns)
            if f"WHAT-{node})" in prompt and f'"title":"Health route {native}' not in prompt
        ]
        assert len(judged) == 1, f"{len(judged)} reviewer turns judged {node}: {self.ran.stderr}"
        return judged[0]


def _review(bench_: Bench, plan: str, tasks: dict[str, str], *argv: str) -> Review:
    """``argv`` run in the bench over ``plan``: the recipe or the script, as the caller names."""
    turns, batches = len(judge_only_turns(bench_)), len(judge_inputs(bench_))
    ran = run(bench_, *argv, seconds=900)
    records = (
        {task: str(one["state"]) for task, one in _records(bench_, plan).items()}
        if ran.returncode == 0
        else {}
    )
    return Review(
        plan,
        tasks,
        ran,
        judge_only_turns(bench_)[turns:],
        [one for one in judge_inputs(bench_)[batches:] if one.get("llmlint")],
        records,
        _project_state(bench_, plan) if ran.returncode == 0 else "",
        {
            task: dict(_metadata(bench_, "task", task).get("onetaskgraph.copies", {}))
            for task in tasks.values()
        },
    )


def _recipe(bench_: Bench, plan: str, tasks: dict[str, str], destination: str) -> Review:
    """The real `just review-plan <plan> <destination>`."""
    return _review(bench_, plan, tasks, "just", "review-plan", plan, destination)


def _script(bench_: Bench, plan: str, tasks: dict[str, str], *destination: str) -> Review:
    """The real script, as a direct call makes it, naming ``destination`` or none."""
    return _review(
        bench_,
        plan,
        tasks,
        *("scripts/plan-store.sh", "scripts/review-plan-item.sh", plan, *destination),
    )


def _copy_of(bench_: Bench, task: str, destination: str) -> str:
    return str(_metadata(bench_, "task", task)["onetaskgraph.copies"][destination])


def _plan_of(
    bench_: Bench, native: str, nodes: tuple[str, ...], destinations: tuple[str, ...]
) -> tuple[str, dict[str, str]]:
    """A plan of ``nodes`` copied through `just copy-plan` to each of ``destinations``."""
    plan = _project(bench_, native)
    tasks = {node: _task(bench_, plan, node) for node in nodes}
    for destination in destinations:
        copied = just(bench_, "copy-plan", plan, destination)
        assert copied.returncode == 0, copied.stdout + copied.stderr
    for task in tasks.values():
        assert sorted(_metadata(bench_, "task", task).get("onetaskgraph.copies", {})) == sorted(
            destinations
        )
    return plan, tasks


def _amend(bench_: Bench, task: str, destination: str, text: str) -> None:
    """Set ``text`` as the amendment of ``task``'s copy on ``destination``, by the store's verb."""
    _set(bench_, "task", _copy_of(bench_, task, destination), {"onepipeline.amendment": text})


class Several(NamedTuple):
    named: Review
    agreed: Review
    single: Review
    bare_named: Review
    uncopied: Review
    uncopied_named: Review
    disagree: Review
    unkeyed: Review
    rendered: dict[str, list[str]]


#: The answers the planning template declares and the finishing one does not.
PLANNING_ONLY = "--var brief=brief.md --var checklist=C"


def _rendered_review_command(bench_: Bench, template: str, native: str) -> list[str]:
    """The `review` step's command of ``template`` rendered for the `mirror` destination."""
    project = f"{native}-{template}"
    _sh(
        bench_,
        f".venv/bin/onetaskgraph project create authoring --id {project} --title 'Rendered' "
        f"--template templates/planning/{template}.md.j2 "
        "--metadata onepipeline.schema_version=3 "
        f"--var plan=authoring:{native} --var native={native} --var plan_title=Rendered "
        f"--var brief_text=B {PLANNING_ONLY if template == 'planning' else ''} "
        f"--var destination={MIRROR} --no-interactive > /dev/null",
    )
    members = store_json(
        bench_, "task", "list", "--source", "authoring", "--project", f"authoring:{project}"
    )
    items = members["items"]
    assert isinstance(items, list), members
    (command,) = [
        one["item"]["metadata"]["onepipeline.command"]
        for one in items
        if one["item"]["metadata"].get("onepipeline.id") == "review"
    ]
    return [str(word) for word in command]


@pytest.fixture(scope="module")
def several(planning: Bench) -> Several:
    bench_, tag = planning, os.getpid()
    both = (BOARD, MIRROR)

    plan, tasks = _plan_of(bench_, f"named-{tag}", ("differ", "bare"), both)
    _amend(bench_, tasks["differ"], BOARD, BOARD_TEXT)
    _amend(bench_, tasks["differ"], MIRROR, MIRROR_TEXT)
    named = _recipe(bench_, plan, tasks, MIRROR)

    plan, tasks = _plan_of(bench_, f"agreed-{tag}", ("same", "onesided", "bare"), both)
    for destination in both:
        _amend(bench_, tasks["same"], destination, SAME_TEXT)
    _amend(bench_, tasks["onesided"], MIRROR, ONE_SIDED_TEXT)
    agreed = _script(bench_, plan, tasks)

    plan, tasks = _plan_of(bench_, f"single-{tag}", ("amended", "bare"), (BOARD,))
    _amend(bench_, tasks["amended"], BOARD, SINGLE_TEXT)
    single = _recipe(bench_, plan, tasks, MIRROR)

    plan, tasks = _plan_of(bench_, f"bare-named-{tag}", ("bare",), both)
    bare_named = _script(bench_, plan, tasks, BOARD)

    plan, tasks = _plan_of(bench_, f"uncopied-{tag}", ("bare",), ())
    uncopied = _script(bench_, plan, tasks)
    plan, tasks = _plan_of(bench_, f"uncopied-named-{tag}", ("bare",), ())
    uncopied_named = _script(bench_, plan, tasks, BOARD)

    plan, tasks = _plan_of(bench_, f"disagree-{tag}", ("differ",), both)
    _amend(bench_, tasks["differ"], BOARD, BOARD_TEXT)
    _amend(bench_, tasks["differ"], MIRROR, MIRROR_TEXT)
    disagree = _script(bench_, plan, tasks)

    plan, tasks = _plan_of(bench_, f"unkeyed-{tag}", ("bare",), both)
    unkeyed = _recipe(bench_, plan, tasks, ELSEWHERE)

    rendered = {
        template: _rendered_review_command(bench_, template, f"rendered-{tag}")
        for template in ("planning", "finishing")
    }
    return Several(
        named, agreed, single, bare_named, uncopied, uncopied_named, disagree, unkeyed, rendered
    )


def _assert_attested(review: Review) -> None:
    assert review.ran.returncode == 0, review.ran.stdout + review.ran.stderr
    assert review.records == dict.fromkeys(review.tasks.values(), "valid"), review.records
    assert review.project == "valid", review.project


def _amendment_of(prompt: str) -> str:
    """The amendment section a judged task's prompt carries, or "" for none."""
    assert prompt.count(AMENDMENT_HEADING) <= 1, prompt[-900:]
    return prompt.split(AMENDMENT_HEADING, 1)[1] if AMENDMENT_HEADING in prompt else ""


def test_a_review_for_a_destination_reads_that_copys_amendment_alone(several: Several) -> None:
    review = several.named
    _assert_attested(review)
    assert f"\n\n{MIRROR_TEXT}\n" in _amendment_of(review.turn("differ"))
    assert not any("BOARD-AMENDMENT" in text for text in review.texts()), review.texts()
    assert _amendment_of(review.turn("bare")) == ""


def test_a_review_for_no_destination_reads_the_amendment_the_copies_agree_on(
    several: Several,
) -> None:
    review = several.agreed
    _assert_attested(review)
    assert f"\n\n{SAME_TEXT}\n" in _amendment_of(review.turn("same"))
    assert f"\n\n{ONE_SIDED_TEXT}\n" in _amendment_of(review.turn("onesided"))
    assert _amendment_of(review.turn("bare")) == ""


def test_a_single_copy_is_read_whatever_destination_is_named(several: Several) -> None:
    review = several.single
    _assert_attested(review)
    assert f"\n\n{SINGLE_TEXT}\n" in _amendment_of(review.turn("amended"))
    assert _amendment_of(review.turn("bare")) == ""


@pytest.mark.parametrize(
    "shape",
    ["agreed", "named", "single", "bare_named", "uncopied", "uncopied_named"],
)
def test_every_amendment_free_task_is_judged_and_attested_with_none(
    several: Several, shape: str
) -> None:
    """Two copies holding none (for no destination and for one of its keys), one copy holding
    none, and no copy link (for no destination and for one)."""
    review: Review = getattr(several, shape)
    _assert_attested(review)
    prompt = review.turn("bare")
    assert AMENDMENT_HEADING not in prompt, prompt[-900:]
    assert not any(text in prompt for text in EVERY_TEXT), prompt[-900:]


def test_copies_holding_different_amendments_refuse_a_review_for_no_destination(
    several: Several,
) -> None:
    review = several.disagree
    assert review.ran.returncode == REFUSED, review.ran.stdout + review.ran.stderr
    assert review.turns == [] and review.batches == []
    (task,) = review.tasks.values()
    stderr = review.ran.stderr
    assert f"{task}'s board copies " in stderr, stderr
    assert "hold different amendments" in stderr, stderr
    remedy = f"run the review for the destination whose run binds ('just review-plan {review.plan}"
    assert remedy in stderr, stderr
    copies = review.copies[task]
    assert sorted(copies) == [BOARD, MIRROR], copies
    assert all(copy in stderr for copy in copies.values()), (copies, stderr)


def test_a_destination_the_copy_link_lacks_refuses_the_review(several: Several) -> None:
    review = several.unkeyed
    assert review.ran.returncode == REFUSED, review.ran.stdout + review.ran.stderr
    assert review.turns == [] and review.batches == []
    (task,) = review.tasks.values()
    stderr = review.ran.stderr
    assert f"{task} is copied to the destinations [" in stderr, stderr
    listed = json.loads("[" + stderr.split("the destinations [", 1)[1].split("]", 1)[0] + "]")
    assert sorted(listed) == sorted(review.copies[task]) == [BOARD, MIRROR], stderr
    assert f"holds no copy under '{ELSEWHERE}'" in stderr, stderr
    assert "run the review for one of those destinations" in stderr, stderr


@pytest.mark.parametrize("template", ["planning", "finishing"])
def test_the_review_step_names_the_destination_it_was_rendered_for(
    several: Several, template: str
) -> None:
    command = several.rendered[template]
    assert command[0] == "scripts/review-plan-item.sh", command
    assert command[-1] == MIRROR, command
