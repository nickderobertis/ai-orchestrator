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
from drafting import design_commands, plan_commands
from planning_bench import Bench, bench, judge_inputs, judge_only_turns, just, run, runs_of

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("live-correction")]

#: The text the doubled judge refuses wherever a judged task carries it, and the rule it
#: refuses it under: a criterion naming a procedure.
REFUSED_TEXT = "Run the exact command `just gate` before reporting."
REFUSED_RULE = "plan_criteria_state_properties"
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


class Sequence(NamedTuple):
    """The launch, every correction, and the note."""

    bench: Bench
    run: str
    undesigned: subprocess.CompletedProcess[str]
    retemplated: subprocess.CompletedProcess[str]
    launched: subprocess.CompletedProcess[str]
    ops: dict[str, Op]
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
    )
    native = f"plan-lc-{os.getpid()}"
    plan = _plan(bench_, native)
    reviewed = just(bench_, "review-plan", plan)
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
        node = {"id": f"probe-{suffix}", "persona": "engineer", "task": _task(text)}
        return [{"op": "add", "node": node}]

    ops = {"amend": _correct(bench_, run_id, "amend", amend)}
    parked = _reply(bench_, run_id, {"op": "cancel", "id": ROUTE, "reason": "re-scoped below"})
    assert parked.returncode == 0, parked.stdout + parked.stderr
    ops["requeue"] = _correct(bench_, run_id, "requeue", requeue)
    ops["retry"] = _correct(bench_, run_id, "retry", retry)
    ops["add"] = _correct(bench_, run_id, "add", add)

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
    yield Sequence(
        bench_,
        run_id,
        undesigned,
        retemplated,
        launched,
        ops,
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
