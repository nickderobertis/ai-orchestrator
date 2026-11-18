"""A plan partway through delivery is reviewed as a plan, and what its run settled keeps its pass.

One sequence on one bench (`planning_bench`), through the real `just review-plan`, `just
copy-plan` and the launch gate `scripts/plan-attestation-check.sh` as the engine's `plan
check` runs it, over plans the store renders from this host's own templates. Only the paid
model is doubled: `fake_backend.py` answers the planner's `reviewer` and records every turn
it is handed, refusing only a turn carrying the defect text below together with the planner
persona's plan-review bar and the review's task framing; `judging_codex.py` answers every
`plan-checklist` batch and records the files each read.

* **Reviewed as a plan.** Two plans whose descriptions state a delivery criterion their
  nodes have not met: one with no board copy whose own tasks and project carry what a run
  writes, and one whose clean authoring copy has a board copy holding an amendment. Each
  passes, every judge reads the bar and the framing, none reads a run-written key, the
  amendment reaches the reviewer as a labelled section of its task, and the run's keys on the
  first plan are left as they were. New run-written values after the pass re-open nothing.
* **Settled tasks keep their pass.** A board-copied plan whose settlements live on its board
  copy, and a plan with no board copy whose settlements sit on its own tasks, each with a
  settled task under a whole-list record, one under a record predating the list, one edited
  since its record, and unsettled ones. After the bar moves the launch gate refuses, and the
  review judges, every one but the first; a second bar move re-opens only the unsettled.
* **A defect is still refused.** A task whose criterion does not prove its goal is refused,
  its project with it, while its sibling is attested.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from typing import Any, NamedTuple

import pytest
import run_written_keys
import yaml
from fake_backend import JUDGE_REFUSAL, JUDGE_REFUSES_ENV
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
#: The review's task framing, one phrase of each of its three parts.
FRAMING = (
    "reviewed under the plan-review bar before or during its delivery",
    "Its acceptance criteria state what its nodes will deliver",
    "Its delivery state",
)
#: A criterion that does not prove its goal: the defect the doubled reviewer refuses.
DEFECT = "DEFECT-CRITERION: the docs mention that a health route exists."
#: A delivery criterion every plan's description states and none of its nodes has met.
UNMET = "The plan is done once every node has landed on main and the route answers in production."
#: Where the review renders an amendment into its task's judged file.
AMENDMENT_HEADING = "## Amendment: the run's binding live ruling on this task"


def _sh(bench_: Bench, script: str) -> str:
    done = run(bench_, "bash", "-c", f"set -euo pipefail; {script}", seconds=180)
    assert done.returncode == 0, f"{script}\n{done.stdout}{done.stderr}"
    return done.stdout


def _project(bench_: Bench, native: str) -> str:
    """A plan project in `authoring`, its description rendered from `plan-description`."""
    goal = json.dumps({"text": "Give the service a health route operators can poll"})
    _sh(
        bench_,
        f"{RESOLVE} plan-description --json | {STORE} project create authoring --id {native} "
        f"--title {shlex.quote('Health route ' + native)} --template-loader - "
        f"--var {shlex.quote('overview=Add a health route. ' + UNMET)} "
        "--metadata onepipeline.schema_version=3 "
        f"--metadata {shlex.quote('onepipeline.goal=' + goal)} "
        "--classification private --no-interactive >/dev/null",
    )
    return f"authoring:{native}"


def _task(bench_: Bench, plan: str, node: str, criterion: str | None = None) -> str:
    """One task of ``plan``, rendered from `plan-task`; answers its qualified id."""
    criteria = json.dumps([criterion or f"A request-level test drives {node} end to end."])
    created = _sh(
        bench_,
        f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {plan} "
        f"--title {shlex.quote('feat: ' + node)} --template-loader - "
        f"--var {shlex.quote(f'what={_what(node)}')} --var 'why=Operators need it.' "
        f"--var {shlex.quote('acceptance_criteria=' + criteria)} "
        f"--metadata 'onepipeline.id={json.dumps(node)}' "
        "--metadata 'onepipeline.persona=\"engineer\"' "
        "--metadata 'orchestrator.unit=\"API\"' --no-interactive --json",
    )
    return str(json.loads(created)["items"][0]["id"])


def _what(node: str) -> str:
    """The text only ``node``'s own content carries, so a judge's input names it."""
    return f"Build the {node} part of the route (WHAT-{node})."


def _set(bench_: Bench, kind: str, item: str, values: Mapping[str, object]) -> None:
    for key, value in values.items():
        answered = store(bench_, kind, "metadata", "set", item, key, json.dumps(value))
        assert answered.returncode == 0, answered.stdout + answered.stderr


def _metadata(bench_: Bench, kind: str, item: str) -> dict[str, Any]:
    shown = store_json(bench_, kind, "show", item)
    items = shown["items"]
    assert isinstance(items, list) and len(items) == 1, shown
    metadata = items[0]["item"]["metadata"]
    assert isinstance(metadata, dict), shown
    return metadata


def _run_written(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in metadata.items() if key in run_written_keys.RUN_WRITTEN_KEYS
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
        """Whether a reviewer turn of this review judged ``node``'s own content."""
        return any(f"WHAT-{node})" in str(turn.get("prompt")) for turn in self.turns)

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


def _refused_tasks(reasons: list[str], tasks: dict[str, str]) -> set[str]:
    """The nodes of ``tasks`` (node -> id) whose plan review the gate refused."""
    return {
        node
        for node, task in tasks.items()
        for reason in reasons
        if reason.startswith(f"{task}: plan-review")
    }


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
DONE: dict[str, object] = {"status": "done", "branch": "me/x", "landing": "landed"}
TWO_KEY = ("--exclude", "onepipeline.projected_status", "--exclude", "onepipeline.settlement")
#: The settled-task plans' nodes: settled under a whole-list record, under a record predating
#: the list, edited since its record, unsettled, and unsettled with a stale authoring settlement.
SETTLED_NODES = ("kept", "predates", "edited", "open", "stale")


class Settled(NamedTuple):
    """One settled-task plan: its ids, the gate's refusals and the reviews around the bar moves."""

    plan: str
    board: str | None
    tasks: dict[str, str]
    gate_before: list[str]
    review: Review
    predates_record: dict[str, Any]
    gate_after: list[str]
    again: Review


class Legacy(NamedTuple):
    """A settled-done task whose two-key record stays valid under the current bar: the gate's
    refusals and the review's turns around the one review that upgrades its exclusions."""

    tasks: dict[str, str]
    record_state: str
    gate_before: list[str]
    review: Review
    excludes_after: list[str]
    gate_after: list[str]


def _legacy(bench_: Bench, native: str, *, boarded: bool) -> Legacy:
    """A reviewed plan of two tasks, `legacy` settled done under a record of the two keys the
    review once excluded, its authored content unmoved and the bar unchanged since."""
    plan = _project(bench_, native)
    tasks = {node: _task(bench_, plan, node) for node in ("legacy", "plain")}
    reviewed = just(bench_, "review-plan", plan)
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    attested = store(
        bench_,
        *("task", "attest", tasks["legacy"], "--kind", "plan-review", "--by", "review-plan"),
        *("--bar", _bar(bench_), *TWO_KEY),
    )
    assert attested.returncode == 0, attested.stdout + attested.stderr
    gated, gated_tasks = plan, tasks
    if boarded:
        gated = _copied(bench_, plan)
        gated_tasks = {node: _board_task(bench_, task) for node, task in tasks.items()}
    # The run's settlement, written where the run writes and excluded by the two-key record.
    _set(bench_, "task", gated_tasks["legacy"], {"onepipeline.settlement": DONE})
    record_state = str(_records(bench_, gated)[gated_tasks["legacy"]]["state"])
    gate_before = sorted(_refused_tasks(_gate(bench_, gated), gated_tasks))
    review = _reviewed(bench_, plan)
    excludes_after = list(_records(bench_, plan)[tasks["legacy"]]["record"]["excludes"])
    if boarded:
        _copied(bench_, plan)
    gate_after = sorted(_refused_tasks(_gate(bench_, gated), gated_tasks))
    return Legacy(tasks, record_state, gate_before, review, excludes_after, gate_after)


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
    boarded_legacy: Legacy
    own_legacy: Legacy


class SettledPlan(NamedTuple):
    """A reviewed plan holding the settled-task cases: its id, its board copy and its tasks."""

    plan: str
    board: str | None
    tasks: dict[str, str]


def _settled_plan(bench_: Bench, native: str, *, boarded: bool) -> SettledPlan:
    """A reviewed plan holding the settled-task cases, its board copy written as a run would."""
    plan = _project(bench_, native)
    tasks = {node: _task(bench_, plan, node) for node in SETTLED_NODES}
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
    criteria = json.dumps(["A request-level test drives edited end to end, edited since."])
    _sh(
        bench_,
        f"{RESOLVE} plan-task --json | {STORE} task render {tasks['edited']} --template-loader - "
        f"--var {shlex.quote('what=' + _what('edited'))} --var 'why=Operators need it.' "
        f"--var {shlex.quote('acceptance_criteria=' + criteria)} --no-interactive >/dev/null",
    )
    settled = ("kept", "predates", "edited")
    if not boarded:
        for node in settled:
            _set(bench_, "task", tasks[node], {"onepipeline.settlement": DONE})
        return SettledPlan(plan, None, tasks)
    board = _copied(bench_, plan)
    for node in settled:
        _set(bench_, "task", _board_task(bench_, tasks[node]), {"onepipeline.settlement": DONE})
    # A stale settlement on the authoring copy, which the board copy, where the run writes,
    # does not hold.
    _set(bench_, "task", tasks["stale"], {"onepipeline.settlement": DONE})
    return SettledPlan(plan, board, tasks)


def _settled_journey(bench_: Bench, settled: SettledPlan) -> Settled:
    """After the bar moved: gate, review, copy, gate again; the second review comes later."""
    plan, board, tasks = settled
    gated = board or plan
    gated_tasks = (
        {node: _board_task(bench_, task) for node, task in tasks.items()} if board else tasks
    )
    gate_before = _gate(bench_, gated)
    review = _reviewed(bench_, plan)
    predates = _records(bench_, plan)[tasks["predates"]]["record"]
    if board:
        # As the refresh starting a replan does, so the copy writes no stale settlement over
        # the board copy, where the run writes.
        stripped = store(
            bench_, "task", "update", tasks["stale"], "--remove-metadata", "onepipeline.settlement"
        )
        assert stripped.returncode == 0, stripped.stdout + stripped.stderr
        _copied(bench_, plan)
    gate_after = _gate(bench_, gated)
    return Settled(
        plan,
        board,
        tasks,
        sorted(_refused_tasks(gate_before, gated_tasks)),
        review,
        predates,
        sorted(_refused_tasks(gate_after, gated_tasks)),
        Review(subprocess.CompletedProcess([], 0), [], []),
    )


@pytest.fixture(scope="module")
def sequence(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Sequence]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("replan-review")
    bench_ = bench(
        tmp,
        oneharness_bin,
        "e2e-replan-review",
        **{JUDGE_REFUSES_ENV: json.dumps([DEFECT, BAR, *FRAMING])},
    )
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
    _move_bar(bench_, "checklist", "plan-checklist")
    first = first._replace(again=_reviewed(bench_, first.plan))
    second = second._replace(again=_reviewed(bench_, second.plan))

    defect_plan = _project(bench_, f"defect-{tag}")
    defect_tasks = {
        "flawed": _task(bench_, defect_plan, "flawed", DEFECT),
        "sound": _task(bench_, defect_plan, "sound"),
    }
    defect = _reviewed(bench_, defect_plan)
    boarded_legacy = _legacy(bench_, f"legacy-board-{tag}", boarded=True)
    own_legacy = _legacy(bench_, f"legacy-own-{tag}", boarded=False)
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
        _records(bench_, defect_plan),
        _project_state(bench_, defect_plan),
        boarded_legacy,
        own_legacy,
    )


def _carries_run_written_key(text: str) -> list[str]:
    return [key for key in run_written_keys.RUN_WRITTEN_KEYS if key in text]


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


def _framed(turn: dict[str, Any]) -> bool:
    composed = f"{turn.get('system')}\n{turn.get('prompt')}"
    return BAR in composed and all(phrase in composed for phrase in FRAMING)


def test_a_plan_carrying_run_state_and_unmet_delivery_criteria_passes_review(
    sequence: Sequence,
) -> None:
    for plan in (sequence.own, sequence.boarded):
        review = sequence.reviews[plan]
        assert review.ran.returncode == 0, review.ran.stdout + review.ran.stderr
        assert {one["state"] for one in sequence.records[plan].values()} == {"valid"}, (
            sequence.records[plan]
        )
        assert sequence.project_states[plan] == "valid", sequence.project_states
        assert UNMET in str(_project_turn(review, plan).get("prompt"))


def test_every_judge_reads_the_plan_review_bar_and_the_framing(sequence: Sequence) -> None:
    for plan, node in ((sequence.own, "landed"), (sequence.boarded, "first")):
        review = sequence.reviews[plan]
        assert _framed(_project_turn(review, plan)), "the project was not judged as a plan"
        assert _framed(_turn_of(review, node)), f"{node} was not judged as a plan"


def test_no_judge_reads_a_run_written_key(sequence: Sequence) -> None:
    for review in sequence.reviews.values():
        carrying = {text[:120]: _carries_run_written_key(text) for text in review.texts()}
        assert not {text: keys for text, keys in carrying.items() if keys}, carrying
        assert review.batches, "no checklist batch read the judged files"


def test_each_amendment_reaches_the_reviewer_in_its_labelled_section(sequence: Sequence) -> None:
    for plan, text in (
        (sequence.own, str(OWN_TASK_KEYS["amended"]["onepipeline.amendment"])),
        (sequence.boarded, BOARD_AMENDMENT),
    ):
        prompt = str(_turn_of(sequence.reviews[plan], "amended").get("prompt"))
        assert AMENDMENT_HEADING in prompt, prompt[-600:]
        assert text in prompt.split(AMENDMENT_HEADING, 1)[1], prompt[-600:]


def test_the_review_leaves_the_runs_own_keys_where_it_found_them(sequence: Sequence) -> None:
    assert sequence.own_after == sequence.own_before
    assert all(sequence.own_before.values()), sequence.own_before


def test_new_run_written_values_after_a_pass_re_open_nothing(sequence: Sequence) -> None:
    again = sequence.rereview
    assert again.ran.returncode == 0, again.ran.stdout + again.ran.stderr
    assert again.turns == [] and again.batches == [], [
        str(turn.get("prompt"))[:200] for turn in again.turns
    ]


@pytest.mark.parametrize("which", ["boarded_settled", "own_settled"])
def test_a_settled_tasks_pass_survives_a_bar_change_in_the_gate_and_the_review(
    sequence: Sequence, which: str
) -> None:
    """Every task but the one settled under a whole-list record is refused and judged; on the
    board-copied plan `stale` is among them, its authoring copy's settlement being stale."""
    settled: Settled = getattr(sequence, which)
    reopened = ["predates", "edited", "open", "stale"]
    assert settled.gate_before == sorted(reopened), settled.gate_before
    review = settled.review
    assert review.ran.returncode == 0, review.ran.stdout + review.ran.stderr
    assert [node for node in SETTLED_NODES if review.judged(node)] == reopened


def test_a_record_predating_the_list_is_judged_without_run_keys_and_reattested_whole(
    sequence: Sequence,
) -> None:
    for settled in (sequence.boarded_settled, sequence.own_settled):
        prompt = str(_turn_of(settled.review, "predates").get("prompt"))
        carried = _carries_run_written_key(prompt)
        assert not carried, [prompt[max(0, prompt.index(key) - 400) :][:600] for key in carried]
        assert set(settled.predates_record["excludes"]) == set(run_written_keys.RUN_WRITTEN_KEYS)


def test_after_the_review_and_a_copy_the_gate_refuses_none(sequence: Sequence) -> None:
    for settled in (sequence.boarded_settled, sequence.own_settled):
        assert settled.gate_after == [], settled.gate_after


def test_a_further_bar_change_re_opens_only_the_unsettled(sequence: Sequence) -> None:
    for settled in (sequence.boarded_settled, sequence.own_settled):
        again = settled.again
        assert again.ran.returncode == 0, again.ran.stdout + again.ran.stderr
        assert [node for node in SETTLED_NODES if again.judged(node)] == ["open", "stale"]


def test_a_plan_quality_defect_is_still_refused(sequence: Sequence) -> None:
    defect = sequence.defect
    assert defect.ran.returncode == 1, defect.ran.stdout + defect.ran.stderr
    assert JUDGE_REFUSAL in defect.ran.stderr, defect.ran.stderr
    flawed, sound = sequence.defect_tasks["flawed"], sequence.defect_tasks["sound"]
    assert sequence.defect_records[flawed]["state"] != "valid", sequence.defect_records
    assert sequence.defect_records[sound]["state"] == "valid", sequence.defect_records
    assert sequence.defect_project != "valid"
    assert _framed(_turn_of(defect, "flawed"))


@pytest.mark.parametrize("which", ["boarded_legacy", "own_legacy"])
def test_a_settled_tasks_valid_record_predating_the_list_is_refused_until_reviewed(
    sequence: Sequence, which: str
) -> None:
    """The store answers the two-key record valid, yet the gate refuses the task until the one
    review judges it and attests it with the whole list; its sibling is never touched."""
    legacy: Legacy = getattr(sequence, which)
    assert legacy.record_state == "valid"
    assert legacy.gate_before == ["legacy"], legacy.gate_before
    review = legacy.review
    assert review.ran.returncode == 0, review.ran.stdout + review.ran.stderr
    assert review.judged("legacy") and not review.judged("plain")
    assert set(legacy.excludes_after) == set(run_written_keys.RUN_WRITTEN_KEYS)
    assert legacy.gate_after == [], legacy.gate_after
