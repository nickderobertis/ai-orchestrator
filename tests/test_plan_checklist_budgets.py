"""The plan-checklist budgets' command, driven over one planning flow's real records.

`tests/fixtures/plan-checklist-budgets/` is the workload the two budgets were approved at:
one planning flow over the real 9-node `crozier-fern-test-scenarios-2026-10-08` plan (10
documents, 651 KB), with 7 checklist runs — 3 decisions in the draft run, 3 in the finalize
run, and 1 `just review-plan` run. Its records are copied from spike-plan-checklist's three
recorded runs of that plan, reused in turn: each run's history pointer line verbatim, and
the oneharness history ``run`` record it names verbatim (the event lines before it in the
history file are left out, as nothing here reads them). The reviewer's per-decision times
are a real planner dispatch's onejudge report — the draft planner of this very plan, three
judge turns — with its transcript and its process table left out, for both planner runs.
The draft run's launch record is that same planner run's, its launch instant verbatim and
every other field left out, since the command reads nothing else of it.

A pointer names its history file by the absolute path it was recorded at on the host that
recorded it, so each journey copies the fixture and points every pointer at the copy's own
history directory, which is the one change made to a record. The command runs as
onebudgetspec runs it, its figure read back from the result file; no test compares either
figure with a threshold — onebudgetspec is the only judge of that.
"""

# This repository's orchestrator project keeps every test under tests/ and every fixture under
# tests/fixtures, and orchestrator/ holds the package alone; orchestrator/budgets.yaml's other
# budgets are measured from tests/ too, through tests/budget_telemetry.py.
# llmlint: ignore-file[budgets_scoped_to_minimal_tree] see above

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator import plan_checklist_budgets as budgets
from orchestrator import plan_review
from orchestrator.root import REPO_ROOT

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "plan-checklist-budgets"
FLOW = "crozier-fern-test-scenarios-2026-10-08"
PROJECT = f"authoring:{FLOW}"
#: The second of the three recorded runs, which the draft and the finalize each reuse as
#: their second decision.
SECOND_RUN = "evaluate-each-rule-against-the-target-20261010T011253Z-836551.jsonl"

#: Each recorded run's `duration_ms` and tokens, read off the three history records by hand,
#: and the reviewer's three judge turns off the onejudge report.
RUN_SECONDS = (58.906, 50.373, 60.924)
REVIEWER_SECONDS = (32.215, 16.366, 56.788)
RUN_TOKENS = (381268 + 2412, 306343 + 2348, 395495 + 2519)
RUN_CACHE_READS = (313088, 246144, 327808)

#: The flow's figures, summed by hand: each planner run waits on (58.906 − 32.215) +
#: (50.373 − 16.366) + (60.924 − 56.788) = 64.834 s past its reviewer, and the review on its
#: whole 58.906 s run; the tokens are each planner run's three calls and the review's one.
FLOW_SECONDS = 2 * (26.691 + 34.007 + 4.136) + 58.906
FLOW_TOKENS = 2 * (383680 + 308691 + 398014) + 383680


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    """A copy of the fixture whose pointers name the copy's history, as its runs root."""
    copied = tmp_path / "fixture"
    shutil.copytree(FIXTURE, copied)
    for pointers in copied.rglob(plan_review.POINTER_FILE):
        lines = []
        for line in pointers.read_text(encoding="utf-8").splitlines():
            pointer = json.loads(line)
            pointer["history_file"] = str(copied / "history" / Path(pointer["history_file"]).name)
            lines.append(json.dumps(pointer))
        pointers.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return copied / "runs"


class Measured(NamedTuple):
    """What one run of the command answered: its status, its stderr and its result."""

    returncode: int
    stderr: str
    written: dict[str, object] | None


def _measured(
    runs: Path,
    budget: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    **overrides: str,
) -> Measured:
    """Run the command's entry point as onebudgetspec runs it, reading back its result."""
    result = tmp_path / f"{budget}.json"
    result.unlink(missing_ok=True)
    environment = {
        budgets.BUDGET_ENV: budget,
        plan_review.RUNS_ROOT_ENV: str(runs),
        budgets.FLOW_ENV: FLOW,
        budgets.PROJECT_ENV: PROJECT,
        **overrides,
    }
    capsys.readouterr()
    with pytest.MonkeyPatch.context() as patched:
        # The SDK's reporter reads the result file onebudgetspec names from the process
        # environment, as it does under a real check.
        patched.setenv("ONEBUDGETSPEC_RESULT", str(result))
        status = budgets.main(environment)
    written = json.loads(result.read_text(encoding="utf-8")) if result.exists() else None
    return Measured(status, capsys.readouterr().err, written)


def test_the_added_seconds_are_each_decisions_wait_past_its_reviewer_and_the_review(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    done = _measured(runs, budgets.ADDED_SECONDS, tmp_path, capsys)
    written = done.written

    assert done.returncode == 0, done.stderr
    assert written is not None
    assert written["value"] == pytest.approx(FLOW_SECONDS)
    detail = str(written["detail"])
    assert f"draft run {FLOW}: decision 1: checklist 58.9 s less reviewer 32.2 s" in detail
    assert "decision 3: checklist 60.9 s less reviewer 56.8 s = 4.1 s" in detail
    assert f"finalize run {FLOW}-finalize: decision 1:" in detail
    assert f"plan-review run 20261010T011151567000Z-1 of {PROJECT}: checklist 58.9 s" in detail


def test_the_tokens_are_input_plus_output_with_cache_reads_and_the_cache_excluded_apart(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    done = _measured(runs, budgets.TOKENS, tmp_path, capsys)
    written = done.written

    assert done.returncode == 0, done.stderr
    assert written is not None
    assert written["value"] == FLOW_TOKENS
    detail = str(written["detail"])
    for stage in (
        f"draft run {FLOW}",
        f"finalize run {FLOW}-finalize",
        f"plan-review run 20261010T011151567000Z-1 of {PROJECT}",
    ):
        assert f"{stage}, batch 1 (codex:primary): input 381268 + output 2412" in detail, detail
    assert f"= {RUN_TOKENS[0]}; cache reads {RUN_CACHE_READS[0]}" in detail
    cache_reads = 2 * sum(RUN_CACHE_READS) + RUN_CACHE_READS[0]
    assert f"cache reads, reported separately: {cache_reads} tokens" in detail
    # Codex counts its cache reads inside its input, so the cache-excluded figure is the
    # recorded input and output less those reads.
    assert f"cache-excluded figure, supplementary: {FLOW_TOKENS - cache_reads} tokens" in detail


@pytest.mark.parametrize("budget", [budgets.ADDED_SECONDS, budgets.TOKENS])
def test_a_run_whose_records_are_gone_is_named_and_not_counted(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], budget: str
) -> None:
    """The second recorded run's history removed: both planner runs lose that decision."""
    (runs.parent / "history" / SECOND_RUN).unlink()
    done = _measured(runs, budget, tmp_path, capsys)
    written = done.written

    assert done.returncode == 0, done.stderr
    assert written is not None
    lost = (
        2 * (RUN_SECONDS[1] - REVIEWER_SECONDS[1])
        if budget == budgets.ADDED_SECONDS
        else 2 * RUN_TOKENS[1]
    )
    full = FLOW_SECONDS if budget == budgets.ADDED_SECONDS else FLOW_TOKENS
    assert written["value"] == pytest.approx(full - lost)
    detail = str(written["detail"])
    for stage in (f"draft run {FLOW}", f"finalize run {FLOW}-finalize"):
        assert f"missing from {stage}: the call started 2026-10-10T01:12:53Z" in detail, detail
    assert SECOND_RUN in detail


@pytest.mark.parametrize("budget", [budgets.ADDED_SECONDS, budgets.TOKENS])
def test_a_review_of_the_plan_from_before_the_flow_launched_is_not_the_flows(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], budget: str
) -> None:
    """The same plan reviewed in an earlier flow: that review counts toward neither figure."""
    reviews = plan_review.reviews_of(PROJECT, runs)
    earlier = reviews / "20261008T040854000000Z-7"
    shutil.copytree(reviews / "20261010T011151567000Z-1", earlier)

    done = _measured(runs, budget, tmp_path, capsys)
    written = done.written

    assert done.returncode == 0, done.stderr
    assert written is not None
    assert written["value"] == pytest.approx(
        FLOW_SECONDS if budget == budgets.ADDED_SECONDS else FLOW_TOKENS
    )
    assert earlier.name not in str(written["detail"])


@pytest.mark.parametrize(
    ("launch", "reason"),
    [
        (None, "records no launch"),
        ("[]", "is not a launch record"),
        (json.dumps({"run_id": FLOW}), "records no instant"),
    ],
)
def test_a_draft_run_whose_launch_cannot_be_read_fails_naming_it(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], launch: str | None, reason: str
) -> None:
    """Without its launch the flow's reviews cannot be told from an earlier flow's."""
    record = runs / FLOW / budgets.LAUNCH_FILE
    if launch is None:
        record.unlink()
    else:
        record.write_text(launch, encoding="utf-8")

    done = _measured(runs, budgets.TOKENS, tmp_path, capsys)

    assert done.returncode == 1
    assert done.written is None
    assert str(record) in done.stderr and reason in done.stderr, done.stderr


def test_a_review_named_for_no_start_fails_naming_it(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stray = plan_review.reviews_of(PROJECT, runs) / "not-a-review"
    stray.mkdir()

    done = _measured(runs, budgets.TOKENS, tmp_path, capsys)

    assert done.returncode == 1
    assert done.written is None
    assert f"{stray} is named for no instant a review started" in done.stderr


def test_a_flow_named_by_nothing_fails_naming_why(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    done = _measured(runs, budgets.TOKENS, tmp_path, capsys, PLAN_CHECKLIST_FLOW="")
    written = done.written

    assert done.returncode == 1
    assert written is None
    assert "no planning flow is named" in done.stderr


def test_a_draft_run_that_was_never_recorded_fails_naming_it(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    done = _measured(runs, budgets.TOKENS, tmp_path, capsys, PLAN_CHECKLIST_FLOW="elsewhere")
    written = done.written

    assert done.returncode == 1
    assert written is None
    assert f"no planning run elsewhere is recorded under {runs}" in done.stderr


def test_a_budget_it_does_not_measure_is_refused(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    done = _measured(runs, "cycle-time", tmp_path, capsys)
    written = done.written

    assert done.returncode == 1
    assert written is None
    assert "names no plan-checklist budget: 'cycle-time'" in done.stderr


def test_the_check_reports_each_figure_through_onebudgetspec(runs: Path) -> None:
    """The registered command, run by the pinned onebudgetspec over the fixture's flow."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ONEBUDGETSPEC_", "PLAN_CHECKLIST_", "ONEPIPELINE_"))
    }
    environment |= {
        plan_review.RUNS_ROOT_ENV: str(runs),
        budgets.FLOW_ENV: FLOW,
        budgets.PROJECT_ENV: PROJECT,
    }
    checked = subprocess.run(
        [
            str(Path(sys.executable).with_name("onebudgetspec")),
            "check",
            str(REPO_ROOT / "orchestrator" / "budgets.yaml"),
            "--label",
            "planning",
            "--json",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )

    reported = (
        {result["id"]: result for result in json.loads(checked.stdout)["results"]}
        if checked.stdout.strip()
        else {}
    )
    assert set(reported) == {budgets.ADDED_SECONDS, budgets.TOKENS}, checked.stdout + checked.stderr
    assert reported[budgets.ADDED_SECONDS]["actual"] == pytest.approx(FLOW_SECONDS)
    assert reported[budgets.TOKENS]["actual"] == FLOW_TOKENS


def _pointer(history: Path, labels: dict[str, str], identifier: str = "h") -> str:
    return json.dumps(
        {
            "history_id": identifier,
            "history_file": str(history),
            "harness_id": "claude-code:alternate",
            "started": "2026-10-10T00:00:00Z",
            "labels": labels,
        }
    )


def _record(started: str, milliseconds: int, harness: str = "claude-code:alternate") -> str:
    return json.dumps(
        {
            "type": "run",
            "harness_id": harness,
            "started_at": started,
            "duration_ms": milliseconds,
            "usage": {"input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 7},
        }
    )


def test_one_checklist_runs_calls_are_one_decision_and_other_roles_are_not_its(
    tmp_path: Path,
) -> None:
    """A run's fallback and concurrent batch are one decision; the worker's call is not."""
    history = tmp_path / "history.jsonl"
    history.write_text(
        "\n".join(
            [
                json.dumps({"type": "event"}),
                _record("2026-10-10T00:00:00.000Z", 1000),
                _record("2026-10-10T00:00:00.500Z", 3000),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    later = tmp_path / "later.jsonl"
    later.write_text(_record("2026-10-10T00:05:00.000Z", 2000, "codex:primary") + "\n")
    pointers = tmp_path / "oneharness-sessions.jsonl"
    pointers.write_text(
        "\n".join(
            [
                _pointer(history, {"role": "llmlint", budgets.RUN_LABEL: "first"}),
                _pointer(history, {"role": "worker"}),
                _pointer(later, {"role": "llmlint"}, identifier="unlabelled"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    found, missing = budgets.calls(pointers, by_role=True)
    grouped = budgets.decisions(found)

    assert missing == []
    assert [len(decision) for decision in grouped] == [2, 1]
    assert budgets.span(grouped[0]) == pytest.approx(3.5)
    # A Claude record's input already excludes its cache reads; a Codex record's does not.
    assert [call.cache_excluded for call in found] == [12, 12, 5]


def test_a_dispatch_with_more_decisions_than_reviewer_turns_waits_on_the_rest_whole() -> None:
    call = budgets.Call(
        "r", budgets.datetime.fromisoformat("2026-10-10T00:00:00Z"), 5.0, "codex:primary", 1, 1, 0
    )
    stage = budgets.Stage("draft run x", [[call], [call._replace(run="s")]], [7.0], [])
    seconds, detail = budgets.added_seconds([stage])

    assert seconds == pytest.approx(5.0)
    assert "decision 1: checklist 5.0 s less reviewer 7.0 s = 0.0 s" in detail
    assert "decision 2: checklist 5.0 s less reviewer 0.0 s = 5.0 s" in detail


def _launch(runs: Path, run: str, started_at: object) -> None:
    """The launch record the engine writes for ``run``, carrying the one field read here."""
    (runs / run).mkdir(parents=True, exist_ok=True)
    (runs / run / budgets.LAUNCH_FILE).write_text(
        json.dumps({"run_id": run, budgets.LAUNCHED: started_at}), encoding="utf-8"
    )


def test_a_dispatch_or_review_that_recorded_nothing_is_said_so(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _launch(runs, FLOW, "2026-10-09T23:00:00.000Z")
    (plan_review.reviews_of(PROJECT, runs) / "20261010T000000000000Z-1").mkdir(parents=True)

    stages = budgets.flow(runs, FLOW, PROJECT)
    seconds, detail = budgets.added_seconds(stages)
    tokens, token_detail = budgets.tokens(stages)

    assert (seconds, tokens) == (0.0, 0.0)
    assert f"draft run {FLOW}: no checklist call recorded" in detail
    assert "recorded no history pointer file" in detail
    assert f"plan-review run 20261010T000000000000Z-1 of {PROJECT}: no checklist call" in (
        token_detail
    )
    assert "recorded no history" in token_detail


@pytest.mark.parametrize(
    ("pointer", "record", "reason"),
    [
        ("not json", None, "is not a history pointer"),
        (json.dumps(["a list"]), None, "carries no labels"),
        (json.dumps({"labels": {"role": "llmlint"}}), None, "names no history_id"),
        (
            json.dumps({"history_id": "h", "history_file": "/gone", "labels": {"role": "llmlint"}}),
            None,
            "records no instant",
        ),
        (None, "not a record", "holds a line that is no record"),
        (None, json.dumps({"type": "run", "duration_ms": 1}), "records no instant"),
        (
            None,
            json.dumps({"type": "run", "duration_ms": 1, "started_at": "2026-10-10T00:00:00"}),
            "an instant with no zone",
        ),
        (
            None,
            json.dumps({"type": "run", "duration_ms": -5, "started_at": "2026-10-10T00:00:00Z"}),
            "records duration_ms as -5, which is no count",
        ),
        (
            None,
            json.dumps({"type": "run", "started_at": "2026-10-10T00:00:00Z"}),
            "records duration_ms as None, which is no count",
        ),
        (
            None,
            json.dumps(
                {"type": "run", "duration_ms": 1, "started_at": "2026-10-10T00:00:00Z", "usage": 3}
            ),
            "records usage as 3, which is no usage",
        ),
        (
            None,
            json.dumps({"type": "run", "duration_ms": 1, "started_at": "2026-10-10T00:00:00Z"}),
            "names no harness_id",
        ),
    ],
)
def test_a_record_that_cannot_be_read_fails_naming_it(
    tmp_path: Path, pointer: str | None, record: str | None, reason: str
) -> None:
    history = tmp_path / "history.jsonl"
    history.write_text(f"{record}\n" if record else "", encoding="utf-8")
    pointers = tmp_path / "oneharness-sessions.jsonl"
    pointers.write_text(
        f"{pointer if pointer is not None else _pointer(history, {'role': 'llmlint'})}\n",
        encoding="utf-8",
    )

    with pytest.raises(budgets.FlowError, match=reason):
        budgets.calls(pointers, by_role=True)


@pytest.mark.parametrize(
    ("report", "reason"),
    [
        ({}, "is not a onejudge report"),
        ({"telemetry": "none"}, "its attribution is no list"),
        ({"telemetry": {"attribution": {"role": "judge"}}}, "its attribution is no list"),
        (
            {"telemetry": {"attribution": [{"role": "judge", "candidates": "one"}]}},
            "whose candidates are no list",
        ),
    ],
)
def test_a_report_that_is_not_onejudges_fails_naming_it(
    tmp_path: Path, report: dict[str, object], reason: str
) -> None:
    (tmp_path / "planner-1.json").write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(budgets.FlowError, match=reason):
        budgets.reviewer_seconds(tmp_path)


@pytest.mark.parametrize(
    "named",
    [{"PLAN_CHECKLIST_FLOW": "../elsewhere"}, {"PLAN_CHECKLIST_PROJECT": "no-source"}],
)
def test_a_flow_named_by_no_run_id_is_refused_before_anything_is_read(
    runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], named: dict[str, str]
) -> None:
    done = _measured(runs, budgets.TOKENS, tmp_path, capsys, **named)

    assert done.returncode == 1
    assert done.written is None
    assert "neither names a flow under the runs root" in done.stderr


def test_a_call_that_recorded_no_usage_counts_no_tokens(tmp_path: Path) -> None:
    """A candidate that failed before any work records no usage, and spends no tokens."""
    history = tmp_path / "history.jsonl"
    history.write_text(
        json.dumps(
            {
                "type": "run",
                "duration_ms": 900,
                "started_at": "2026-10-10T00:00:00Z",
                "harness_id": "codex:alternate",
                "usage": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    pointers = tmp_path / "oneharness-sessions.jsonl"
    pointers.write_text(_pointer(history, {"role": "llmlint"}) + "\n", encoding="utf-8")

    (call,), missing = budgets.calls(pointers, by_role=True)

    assert missing == []
    assert (call.seconds, call.tokens, call.cache_read) == (0.9, 0, 0)


def test_the_finalize_run_is_named_as_the_planning_flow_names_it() -> None:
    """The finalize run these budgets read beside the draft is the one `just plan` launches."""
    helper = (REPO_ROOT / "scripts" / "plan-brief.sh").read_text(encoding="utf-8")

    assert f'PLAN_FINALIZE_RUN_SUFFIX="{budgets.FINALIZE_SUFFIX}"' in helper


def test_only_the_reviewers_judge_turns_are_counted(tmp_path: Path) -> None:
    """A panel's report stamps each judge's turns with its label; a lone judge's carry none."""
    candidate = {"duration_ms": 2000}
    (tmp_path / "planner-1.json").write_text(
        json.dumps(
            {
                "telemetry": {
                    "attribution": [
                        {"role": "agent", "candidates": [candidate]},
                        {"role": "judge", "judge": "reviewer", "candidates": [candidate]},
                        {"role": "judge", "judge": "another", "candidates": [candidate]},
                        {"role": "judge", "candidates": [candidate, candidate]},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "planner-2.json").write_text(json.dumps({"telemetry": {}}), encoding="utf-8")

    assert budgets.reviewer_seconds(tmp_path) == [2.0, 4.0]
