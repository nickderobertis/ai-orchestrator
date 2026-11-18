"""Every live correction carrying task prose is judged as authoring is; a note is not.

A reviewed, approved plan is launched through the orchestrate recipe on one bench
(`planning_bench`). Its first node's every worker turn is lost at the doubled provider, so it
fails and its dependents are skipped, and the run ends with nothing driving it — the state a
manager corrects. Then, through `just channel-reply` exactly as a manager sends them:

* an `amend` of one node's criteria,
* a `requeue` of a parked node carrying a new task,
* a `retry` of the failed node with a replacement task, and
* an `add` of a new node,

each sent first with a task the doubled judge refuses — the reply exits 2 and the run's
journal commits nothing — and then with one it passes, which the engine applies. The engine's
`plan_edit_hook`, `scripts/review-plan-item.sh`, is what judges each: the doubled judge
provider records receiving that op's own task text through the planner graph's judge-only
run. A `note` sent after them reaches no judge.

Human items, plans and follow-up drafts stay in the bench's own local sources.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import run_written_keys
from drafting import design_commands, plan_commands
from fake_backend import JUDGE_REFUSAL, JUDGE_REFUSES_ENV
from judging_codex import JudgeInput
from planning_bench import (
    Bench,
    assert_reviewed_on_codex,
    bench,
    judge_inputs,
    judge_only_turns,
    just,
    local_source,
    run,
    runs_of,
)

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("live-correction")]

#: The text the doubled judge refuses wherever a judged task carries it, and the rule it
#: refuses it under: a criterion naming a procedure.
REFUSED_TEXT = "Run the exact command `just gate` before reporting."
REFUSED_RULE = "plan_criteria_state_properties"
#: A criterion that does not prove its goal, which the doubled reviewer refuses only when it
#: reads it under the planner persona's plan-review bar and the review's task framing.
DEFECT = "DEFECT-CRITERION: the docs mention that a health route exists."
BAR = "You are a principal engineer reviewing a plan before anyone builds from it."
FRAMING = (
    "reviewed under the plan-review bar before or during its delivery",
    "Its acceptance criteria state what its nodes will deliver",
    "Its delivery state",
)
#: The node whose every turn is lost, and the two behind it.
FLAKY, ROUTE, DOCS = "flaky", "route", "docs"
#: What `just channel-reply` exits with for every refusal.
REFUSED = 2

STORE = ".venv/bin/onetaskgraph"
RESOLVE = "scripts/onepipeline.sh template resolve"


class Op(NamedTuple):
    """One correction, refused and then passed: what was sent and what came back."""

    name: str
    passing_text: str
    committed_before: int
    refused: subprocess.CompletedProcess[str]
    committed_after_refusal: int
    passed: subprocess.CompletedProcess[str]
    committed_after_pass: int
    judged_after_refusal: list[str]
    judged_after_pass: list[str]
    reviewer_after_pass: list[str]
    #: Every input the Codex double was handed while the passing reply was judged.
    codex_after_pass: list[JudgeInput]


class Sequence(NamedTuple):
    """The launch, every correction, and the note."""

    bench: Bench
    run: str
    undesigned: subprocess.CompletedProcess[str]
    retemplated: subprocess.CompletedProcess[str]
    launched: subprocess.CompletedProcess[str]
    ops: dict[str, Op]
    #: An amend carrying a plan-quality defect, refused by the reviewer, then the same amend
    #: without it, applied; the commits around each, and the reviewer turns each reached.
    defect: Op
    note: subprocess.CompletedProcess[str]
    note_text: str
    judged_after_note: list[str]
    reviewed_after_note: list[str]
    #: An amend sent while the judge cannot answer, the commits after it, then the same sent
    #: again once it answers.
    unjudged: subprocess.CompletedProcess[str]
    committed_before_unjudged: int
    committed_after_unjudged: int
    rejudged: subprocess.CompletedProcess[str]
    committed_after_rejudged: int
    #: `just review-plan` over the plan while its store listing reports a malformed record,
    #: and each task's review state before and after it.
    partial_review: subprocess.CompletedProcess[str]
    states_before_partial: dict[str, str]
    states_after_partial: dict[str, str]
    #: `just review-plan` over the plan while its graph names an edge from a private source
    #: that cannot answer; each task's review state and the project's attestation-check exit
    #: before and after it; and how many judge inputs and reviewer turns it added.
    unanswered_review: subprocess.CompletedProcess[str]
    states_before_unanswered: dict[str, str]
    states_after_unanswered: dict[str, str]
    project_before_unanswered: int
    project_after_unanswered: int
    judged_during_unanswered: int


def _task(what: str, criterion: str = "A request-level test drives the route.") -> str:
    return (
        f"## What\n\n{what}\n\n## Why\n\nOperators need it.\n\n"
        f"## Acceptance criteria\n\n- {criterion}\n"
    )


def _sh(bench: Bench, script: str) -> None:
    done = run(bench, "bash", "-c", f"set -euo pipefail; {script}")
    assert done.returncode == 0, f"{script}\n{done.stdout}{done.stderr}"


def _plan(bench: Bench, native: str) -> str:
    """A three-task plan, `flaky` <- `route` <- `docs`; its design document comes later."""
    plan = f"authoring:{native}"
    for line in plan_commands(native)[:-1]:
        _sh(bench, line)
    _sh(
        bench,
        f"{RESOLVE} plan-task --json | {STORE} task create authoring --project {plan} "
        "--title 'feat: probe the route' --template-loader - --var 'what=Probe the route.' "
        "--var 'why=Operators need it.' "
        "--var 'acceptance_criteria=[\"A request-level test drives the probe.\"]' "
        f"--metadata 'onepipeline.id=\"{FLAKY}\"' --metadata 'onepipeline.persona=\"engineer\"' "
        "--metadata 'orchestrator.unit=\"API\"' --no-interactive --json "
        "| jq -r '.items[0].id' > .plan-flaky.id; "
        f'{STORE} task update "$(cat .plan-route.id)" --depends-on "$(cat .plan-flaky.id)" '
        "--no-interactive >/dev/null; rm -f .plan-route.id .plan-docs.id .plan-flaky.id",
    )
    return plan


# `Any`: the store's JSON listing, which this journey reads by key and never types.
def _listed(bench: Bench, plan: str) -> list[dict[str, Any]]:
    answered = run(
        bench,
        STORE,
        "task",
        "list",
        "--source",
        "authoring",
        "--project",
        plan,
        "--all",
        "--json",
        "--no-interactive",
    )
    assert answered.returncode == 0, answered.stdout + answered.stderr
    items = json.loads(answered.stdout)["items"]
    assert isinstance(items, list)
    return items


def _states(bench: Bench, plan: str) -> dict[str, str]:
    """Each task's `plan-review` state under the bar the review attests under."""
    bar = run(
        bench,
        ".venv/bin/oneagentgraph",
        "run",
        "graphs/planner.yaml",
        "--judge-only",
        "--member",
        "worker",
        "--bar-fingerprint",
    )
    assert bar.returncode == 0, bar.stdout + bar.stderr
    checked = run(
        bench,
        STORE,
        "project",
        "attestation",
        "check",
        plan,
        "--kind",
        "plan-review",
        "--tasks",
        "--bar",
        bar.stdout.strip(),
        "--json",
        "--no-interactive",
    )
    assert checked.returncode in (0, 3), checked.stdout + checked.stderr
    return {str(one["id"]): str(one["state"]) for one in json.loads(checked.stdout)["tasks"]}


def _project_state(bench: Bench, plan: str) -> int:
    """The exit of the project's own `plan-review` attestation check: 0 valid, 3 not."""
    bar = run(
        bench,
        ".venv/bin/oneagentgraph",
        "run",
        "graphs/planner.yaml",
        "--judge-only",
        "--member",
        "worker",
        "--bar-fingerprint",
    )
    assert bar.returncode == 0, bar.stdout + bar.stderr
    checked = run(
        bench,
        STORE,
        "project",
        "attestation",
        "check",
        plan,
        "--kind",
        "plan-review",
        "--bar",
        bar.stdout.strip(),
        "--json",
        "--no-interactive",
    )
    assert checked.returncode in (0, 3), checked.stdout + checked.stderr
    return checked.returncode


def _committed(bench: Bench, run_id: str) -> int:
    """How many edits the run's own journal records as committed."""
    journal = bench.runs / run_id / "events.jsonl"
    return sum(
        1
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("kind") == "edit-committed"
    )


def _judged(bench: Bench) -> list[str]:
    """Every llmlint batch the doubled judge provider received, in order: its prompt, then
    the target text it read."""
    return [
        str(entry.get("prompt"))
        + "\n"
        + "\n".join(str(text) for text in dict(entry.get("read") or {}).values())
        for entry in judge_inputs(bench)
        if entry.get("llmlint")
    ]


def _reviewer(bench: Bench) -> list[str]:
    return [str(turn.get("prompt")) for turn in judge_only_turns(bench)]


def _reply(
    bench: Bench, run_id: str, *commands: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    envelope = {"version": 4, "author": "planner", "commands": list(commands)}
    return run(bench, "just", "channel-reply", run_id, input_text=json.dumps(envelope), seconds=600)


def _correct(bench: Bench, run_id: str, name: str, build: object) -> Op:
    """Send ``name``'s command refused, then passing; read what each reached."""
    assert callable(build)
    refused_text = f"{name.upper()}-REFUSED: {REFUSED_TEXT}"
    passing_text = f"{name.upper()}-PASSING: the route answers its health JSON."
    judged, reviewer = len(_judged(bench)), len(_reviewer(bench))
    before = _committed(bench, run_id)
    refused = _reply(bench, run_id, *build(refused_text, "a"))
    after_refusal = _committed(bench, run_id)
    judged_refused = _judged(bench)[judged:]
    judged = len(_judged(bench))
    codex = len(judge_inputs(bench))
    passed = _reply(bench, run_id, *build(passing_text, "b"))
    return Op(
        name,
        passing_text,
        before,
        refused,
        after_refusal,
        passed,
        _committed(bench, run_id),
        judged_refused,
        _judged(bench)[judged:],
        _reviewer(bench)[reviewer:],
        judge_inputs(bench)[codex:],
    )


def _defect(bench: Bench, run_id: str) -> Op:
    """An amend whose criterion does not prove its goal, then the same amend without it."""
    judged, reviewer = len(_judged(bench)), len(_reviewer(bench))
    before = _committed(bench, run_id)
    refused = _reply(bench, run_id, {"op": "amend", "id": DOCS, "text": f"- {DEFECT}"})
    after_refusal = _committed(bench, run_id)
    refused_turns = _reviewer(bench)[reviewer:]
    judged, reviewer = len(_judged(bench)), len(_reviewer(bench))
    codex = len(judge_inputs(bench))
    passing = "DEFECT-FIXED: a request-level test reads the route's JSON body."
    passed = _reply(bench, run_id, {"op": "amend", "id": DOCS, "text": f"- {passing}"})
    return Op(
        "defect",
        passing,
        before,
        refused,
        after_refusal,
        passed,
        _committed(bench, run_id),
        refused_turns,
        _judged(bench)[judged:],
        _reviewer(bench)[reviewer:],
        judge_inputs(bench)[codex:],
    )


@pytest.fixture(scope="module")
def sequence(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Sequence]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("live-correction")
    bench_ = bench(
        tmp,
        oneharness_bin,
        "e2e-live-correction",
        FAKE_PLANNING_JUDGE_REFUSES=json.dumps({REFUSED_RULE: REFUSED_TEXT}),
        FAKE_PLANNING_JUDGE_DOWN=str(tmp / "judge-down"),
        **{JUDGE_REFUSES_ENV: json.dumps([DEFECT, BAR, *FRAMING])},
    )
    native = f"plan-lc-{os.getpid()}"
    plan = _plan(bench_, native)
    reviewed = just(bench_, "review-plan", plan)
    assert_reviewed_on_codex(judge_inputs(bench_), reviewed)
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    # Reviewed, but with no design document yet: the launch check refuses it as absent.
    undesigned = just(bench_, "orchestrate", plan, "--dag-graph", "off")
    for line in design_commands(native):
        _sh(bench_, line)
    approved = just(bench_, "approve-design", plan)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    # The design-doc template the approval was recorded under changes: the approval is void
    # until it is recorded again, under the template as it now resolves.
    template = bench_.checkout / "templates" / "design-doc.md.j2"
    template.write_text(
        template.read_text(encoding="utf-8") + "{# an edit to the design-doc template #}\n",
        encoding="utf-8",
    )
    retemplated = just(bench_, "orchestrate", plan, "--dag-graph", "off")
    reapproved = just(bench_, "approve-design", plan)
    assert reapproved.returncode == 0, reapproved.stdout + reapproved.stderr

    before = set(runs_of(bench_))
    losing = {
        **bench_.environment,
        "FAKE_BACKEND_OBSERVER_MEMBER": "worker",
        "FAKE_BACKEND_OBSERVER_LOSES": "1",
    }
    launched = just(
        bench_._replace(environment=losing), "orchestrate", plan, "--dag-graph", "off", seconds=1500
    )
    started = [
        one
        for one in runs_of(bench_)
        if one not in before and one != "human-items" and not one.endswith("-follow-ups")
    ]
    assert len(started) == 1, (started, launched.stdout, launched.stderr)
    run_id = started[0]

    def amend(text: str, _: str) -> list[dict[str, object]]:
        return [{"op": "amend", "id": DOCS, "text": f"- {text}"}]

    def requeue(text: str, _: str) -> list[dict[str, object]]:
        return [{"op": "requeue", "id": ROUTE, "amend": {"task": _task(text)}}]

    def retry(text: str, suffix: str) -> list[dict[str, object]]:
        replacement = {"id": f"{FLAKY}-{suffix}", "persona": "engineer", "task": _task(text)}
        return [{"op": "retry", "id": FLAKY, "node": replacement}]

    def add(text: str, suffix: str) -> list[dict[str, object]]:
        node = {
            "id": f"probe-{suffix}",
            "persona": "engineer",
            "task": _task(text),
            "deps": [ROUTE],
        }
        return [{"op": "add", "node": node}]

    ops = {"amend": _correct(bench_, run_id, "amend", amend)}
    parked = _reply(bench_, run_id, {"op": "cancel", "id": ROUTE, "reason": "re-scoped below"})
    assert parked.returncode == 0, parked.stdout + parked.stderr
    ops["requeue"] = _correct(bench_, run_id, "requeue", requeue)
    ops["retry"] = _correct(bench_, run_id, "retry", retry)
    ops["add"] = _correct(bench_, run_id, "add", add)
    defect = _defect(bench_, run_id)

    note_text = f"NOTE-{os.getpid()}: {REFUSED_TEXT}"
    judged = len(_judged(bench_))
    # To the node the add created, so no later edit judged here composes the carried note
    # into a task it judges.
    noted = {
        "op": "note",
        "id": "probe-b",
        "addressee": "both",
        "text": note_text,
        "deliver": "next",
    }
    reviewer = len(_reviewer(bench_))
    note = _reply(bench_, run_id, noted)
    judged_after_note = _judged(bench_)[judged:]
    reviewed_after_note = _reviewer(bench_)[reviewer:]

    down = tmp / "judge-down"
    down.write_text("", encoding="utf-8")
    amended = {"op": "amend", "id": DOCS, "text": "- UNJUDGED: the route answers its JSON."}
    committed_before_unjudged = _committed(bench_, run_id)
    try:
        unjudged = _reply(bench_, run_id, amended)
        committed_after_unjudged = _committed(bench_, run_id)
    finally:
        down.unlink()
    rejudged = _reply(bench_, run_id, amended)
    committed_after_rejudged = _committed(bench_, run_id)

    # A task re-rendered, so the review has something to judge, beside a record the store
    # cannot read, so its listing of the plan reports an error.
    held = next(
        one
        for one in _listed(bench_, plan)
        if one["item"]["metadata"].get("onepipeline.id") == ROUTE
    )
    task = str(held["id"])
    _sh(
        bench_,
        f"{RESOLVE} plan-task --json | {STORE} task render {task} --template-loader - "
        "--var 'what=Do route, rerendered.' --var 'why=Operators need it.' "
        "--var 'acceptance_criteria=[\"A request-level test drives route end to end.\"]' "
        "--no-interactive",
    )
    states_before_partial = _states(bench_, plan)
    # Beside the plan's own task records, where the store reports keeping them.
    broken = Path(str(held["item"]["location"]["path"])).with_name(f"{native}-unreadable.md")
    broken.write_text(f"---\ntitle: [unclosed\nproject: {native}\n---\nbody\n", encoding="utf-8")
    try:
        partial_review = just(bench_, "review-plan", plan)
    finally:
        broken.unlink()
    states_after_partial = _states(bench_, plan)

    # The route task also depends on a task of another private source, which then cannot
    # answer: the listing of the plan's own source is whole, its graph is not.
    elsewhere_root = tmp / "elsewhere"
    elsewhere_root.mkdir()
    elsewhere = bench_._replace(
        environment={**bench_.environment, **local_source("elsewhere", elsewhere_root)}
    )
    _sh(
        elsewhere,
        f"{STORE} project create elsewhere --id outside --title outside --no-interactive "
        f">/dev/null; {STORE} task create elsewhere --project elsewhere:outside "
        "--title 'feat: outside' --no-interactive --json | jq -r '.items[0].id' > .outside.id",
    )
    flaky = next(
        str(one["id"])
        for one in _listed(bench_, plan)
        if one["item"]["metadata"].get("onepipeline.id") == FLAKY
    )
    _sh(
        elsewhere,
        f'{STORE} task update {task} --depends-on {flaky} --depends-on "$(cat .outside.id)" '
        "--no-interactive >/dev/null; rm -f .outside.id",
    )
    states_before_unanswered = _states(bench_, plan)
    project_before_unanswered = _project_state(bench_, plan)
    inputs_before = len(judge_inputs(bench_)) + len(judge_only_turns(bench_))
    gone = elsewhere_root.with_name("elsewhere-gone")
    elsewhere_root.rename(gone)
    try:
        unanswered_review = just(elsewhere, "review-plan", plan)
    finally:
        gone.rename(elsewhere_root)
    judged_during_unanswered = (
        len(judge_inputs(bench_)) + len(judge_only_turns(bench_)) - inputs_before
    )
    states_after_unanswered = _states(bench_, plan)
    project_after_unanswered = _project_state(bench_, plan)
    yield Sequence(
        bench_,
        run_id,
        undesigned,
        retemplated,
        launched,
        ops,
        defect,
        note,
        note_text,
        judged_after_note,
        reviewed_after_note,
        unjudged,
        committed_before_unjudged,
        committed_after_unjudged,
        rejudged,
        committed_after_rejudged,
        partial_review,
        states_before_partial,
        states_after_partial,
        unanswered_review,
        states_before_unanswered,
        states_after_unanswered,
        project_before_unanswered,
        project_after_unanswered,
        judged_during_unanswered,
    )
    for one in runs_of(bench_):
        if not one.startswith("."):
            just(bench_, "stop", one, seconds=120)


def test_a_plan_with_no_design_document_is_refused_as_absent(sequence: Sequence) -> None:
    refused = sequence.undesigned
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "design-approval is absent" in refused.stderr, refused.stderr
    assert "plan-review" not in refused.stderr, refused.stderr


def test_a_changed_design_template_voids_the_approval_until_it_is_recorded_again(
    sequence: Sequence,
) -> None:
    refused = sequence.retemplated
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "design-approval is void" in refused.stderr, refused.stderr
    assert "plan-review" not in refused.stderr, refused.stderr


def test_the_reviewed_plan_launched_and_left_a_node_to_correct(sequence: Sequence) -> None:
    assert "plan-attestation-check" not in sequence.launched.stderr, sequence.launched.stderr
    settled = json.loads(sequence.launched.stdout.strip().splitlines()[-1])
    assert settled["settlement"] != "complete", settled


@pytest.mark.parametrize("name", ["amend", "requeue", "retry", "add"])
def test_a_judged_rejection_refuses_the_reply_and_applies_nothing(
    sequence: Sequence, name: str
) -> None:
    op = sequence.ops[name]
    assert op.refused.returncode == REFUSED, op.refused.stdout + op.refused.stderr
    assert "the plan-edit hook `scripts/review-plan-item.sh` refused node" in op.refused.stderr
    # The engine shortens the hook's findings, so which rule refused is read off the judge's
    # own record: the batch that received this op's task carried the rule that refuses it.
    assert any(
        f"{name.upper()}-REFUSED" in text and f"### {REFUSED_RULE}" in text
        for text in op.judged_after_refusal
    ), [text[-400:] for text in op.judged_after_refusal]


@pytest.mark.parametrize("name", ["amend", "requeue", "retry", "add"])
def test_a_passing_correction_is_judged_on_its_own_task_and_applied(
    sequence: Sequence, name: str
) -> None:
    op = sequence.ops[name]
    assert op.passed.returncode == 0, op.passed.stdout + op.passed.stderr
    assert op.committed_after_pass > op.committed_after_refusal
    assert any(op.passing_text in text for text in op.judged_after_pass), op.judged_after_pass
    assert any(op.passing_text in prompt for prompt in op.reviewer_after_pass), [
        prompt[-300:] for prompt in op.reviewer_after_pass
    ]


def test_the_hook_judges_a_live_edit_as_a_plan_and_reads_no_run_written_key(
    sequence: Sequence,
) -> None:
    """Each passing edit's reviewer turn carries the plan-review bar and the task framing, and
    no file a judge of an edit received holds a run-written key."""
    for op in sequence.ops.values():
        framed = [
            prompt
            for prompt in op.reviewer_after_pass
            if op.passing_text in prompt and BAR in prompt and all(one in prompt for one in FRAMING)
        ]
        assert framed, [prompt[:400] for prompt in op.reviewer_after_pass]
        read = [*op.judged_after_refusal, *op.judged_after_pass, *op.reviewer_after_pass]
        carried = [key for text in read for key in run_written_keys.RUN_WRITTEN_KEYS if key in text]
        assert not carried, carried


def test_a_live_edit_carrying_a_plan_quality_defect_is_refused_and_applied_without_it(
    sequence: Sequence,
) -> None:
    defect = sequence.defect
    assert defect.refused.returncode == REFUSED, defect.refused.stdout + defect.refused.stderr
    assert "the plan-edit hook `scripts/review-plan-item.sh` refused node" in defect.refused.stderr
    assert defect.committed_after_refusal == defect.committed_before
    # Refused by the reviewer, which read the defect under the bar and the framing.
    refusing = [prompt for prompt in defect.judged_after_refusal if DEFECT in prompt]
    assert refusing and all(BAR in prompt for prompt in refusing), refusing
    assert defect.passed.returncode == 0, defect.passed.stdout + defect.passed.stderr
    assert defect.committed_after_pass > defect.committed_after_refusal
    assert JUDGE_REFUSAL not in defect.passed.stderr


@pytest.mark.parametrize("name", ["amend", "requeue", "retry", "add"])
def test_the_plan_edit_hook_runs_its_reviewer_on_codex_in_its_non_repository_scratch(
    sequence: Sequence, name: str
) -> None:
    """Hook mode: the reviewer judging a live correction runs its Codex turn where the hook
    laid the task out, which is no repository, and the Codex double answers it."""
    op = sequence.ops[name]
    assert_reviewed_on_codex(op.codex_after_pass, op.passed)


def test_nothing_of_a_refused_reply_is_committed(sequence: Sequence) -> None:
    for op in sequence.ops.values():
        assert op.committed_after_refusal == op.committed_before, op


def test_a_note_reaches_no_judge(sequence: Sequence) -> None:
    assert sequence.note.returncode == 0, sequence.note.stdout + sequence.note.stderr
    assert not [text for text in sequence.judged_after_note if sequence.note_text in text]
    assert not [prompt for prompt in sequence.reviewed_after_note if sequence.note_text in prompt]


def test_an_edit_the_judge_cannot_judge_applies_nothing_and_applies_once_it_can(
    sequence: Sequence,
) -> None:
    unjudged = sequence.unjudged
    assert unjudged.returncode == REFUSED, unjudged.stdout + unjudged.stderr
    assert "scripts/review-plan-item.sh" in unjudged.stderr, unjudged.stderr
    assert sequence.committed_after_unjudged == sequence.committed_before_unjudged
    rejudged = sequence.rejudged
    assert rejudged.returncode == 0, rejudged.stdout + rejudged.stderr
    assert sequence.committed_after_rejudged > sequence.committed_after_unjudged


def test_a_listing_reporting_errors_is_refused_and_attests_nothing(sequence: Sequence) -> None:
    review = sequence.partial_review
    assert review.returncode == 2, review.stdout + review.stderr
    assert "reported errors or a further page, so nothing was judged" in review.stderr, (
        review.stderr
    )
    assert "void" in sequence.states_before_partial.values(), sequence.states_before_partial
    assert sequence.states_after_partial == sequence.states_before_partial


def _front_matter_ending(depends_on: list[str]) -> str:
    """How the hook's judged task file ends its front matter: `depends_on` last."""
    return '"depends_on":' + json.dumps(depends_on, separators=(",", ":")) + "}\n---\n"


def test_the_hook_shows_the_judge_the_dependencies_an_edited_node_names(
    sequence: Sequence,
) -> None:
    """An added node naming `route` is judged with it as `depends_on`; a retry's replacement
    naming none is judged with `depends_on: []`."""
    for name, deps in (("add", [ROUTE]), ("retry", [])):
        op = sequence.ops[name]
        assert op.passed.returncode == 0, op.passed.stdout + op.passed.stderr
        ending = _front_matter_ending(deps)
        assert any(op.passing_text in text and ending in text for text in op.judged_after_pass), (
            name,
            ending,
            [text[-600:] for text in op.judged_after_pass],
        )


def test_a_graph_the_store_cannot_answer_whole_stops_the_review_unjudged(
    sequence: Sequence,
) -> None:
    review = sequence.unanswered_review
    assert review.returncode == 2, review.stdout + review.stderr
    assert "graph of" in review.stderr and "was not answered whole" in review.stderr, review.stderr
    # What the store reported, naming the source that could not answer.
    assert "source elsewhere could not" in review.stderr, review.stderr
    assert sequence.judged_during_unanswered == 0
    assert "void" in sequence.states_before_unanswered.values(), sequence.states_before_unanswered
    assert sequence.states_after_unanswered == sequence.states_before_unanswered
    # The project's pass excludes every run-written key, so the run's own writes to the
    # project since its review leave it valid; the unanswered review leaves it as it was.
    assert sequence.project_before_unanswered == 0
    assert sequence.project_after_unanswered == 0


@pytest.mark.parametrize("deps", ["route", [3], [""], False])
def test_a_hook_document_naming_dependencies_that_are_not_ids_is_not_judged(
    sequence: Sequence, deps: object
) -> None:
    """The hook's own trust boundary: dependencies that are not a list of ids stop it."""
    document = {"node": {"id": "probe-x", "deps": deps}, "task": {"title": "t", "body": "b"}}
    before = len(judge_inputs(sequence.bench)) + len(judge_only_turns(sequence.bench))
    hooked = run(
        sequence.bench,
        "scripts/review-plan-item.sh",
        input_text=json.dumps(document),
        seconds=120,
    )
    assert hooked.returncode == REFUSED, hooked.stdout + hooked.stderr
    assert "something other than a list of ids, so nothing was judged" in hooked.stderr
    after = len(judge_inputs(sequence.bench)) + len(judge_only_turns(sequence.bench))
    assert after == before
