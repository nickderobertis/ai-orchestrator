"""What the plan checklist costs one planning flow: the two plan-checklist budgets.

The plan checklist is llmlint over a plan's documents. `just review-plan` runs it, and the
draft and finalize planners' panel (`graphs/planner.yaml`) runs it as their second judge
once a planning node names that graph; a dispatch that never ran it records no checklist
call, which the breakdown says. These budgets are about a planning flow the way
`cycle-time` is about a landed change, so neither push gate nor Nx target checks them: they
carry the `planning` label, and the flow's tail (`scripts/finish-plan.sh`) checks them over
the flow it has just finished, an overrun warning rather than refusing the plan.

This reads that flow's own records and re-runs nothing. Every judge call the checklist makes
is a oneharness history ``run`` record, reached through a pointer file: a planning
dispatch's `runs/<run>/oneharness-sessions.jsonl`, on its lines labelled ``role=llmlint`` —
a planner runs no lint of its own, so that role is the checklist — and the one a review
keeps under `runs/plan-reviews/<project>/<started>/` (:func:`orchestrator.plan_review.checklist`).
Neither the engine's ``judge-decided`` events nor the onejudge report carry a per-run figure
for an llmlint judge, so these records are the only place its time and tokens are.

* ``plan-checklist-tokens`` is input plus output as those records report them, over every
  checklist call of the flow. Cache reads are reported separately in the breakdown, and so
  is the cache-excluded figure, because Codex counts cache reads inside its input and
  Claude Code outside it.
* ``plan-checklist-added-seconds`` is how long the flow waited on the checklist beyond its
  reviewer. A panel's judges decide concurrently, so a decision waits on the checklist only
  for the time it took past the reviewer's own turn on that decision, floored at zero; the
  review's checklist run waits whole. A decision's checklist time is the span of its calls'
  recorded ``duration_ms`` (its batches run concurrently and its fallbacks in turn); the
  reviewer's is that decision's judge turn in the dispatch's onejudge report. llmlint's own
  startup is recorded nowhere, so it is left out.

A decision's calls are told apart by the label `scripts/plan-checklist.sh` stamps on every
record one of its runs writes, :data:`RUN_LABEL`, because llmlint's own labels do not reach
oneharness history; a record carrying none — one written before the script stamped them — is
a decision of its own. Decisions are taken in the order they started and paired in that
order with the reviewer's judge turns. A call's time is its record's own: its
``started_at`` and its ``duration_ms``.

The flow is named by the environment, as `finish-plan` names it: :data:`FLOW_ENV` is the
draft planner's run id, whose finalize run is ``<id>-finalize`` when the flow had spikes,
and :data:`PROJECT_ENV` the plan project its reviews judged. The flow's reviews are those of
that project started once its draft run launched, the instant the engine records in the
run's ``launch.json`` and every adoption keeps, so a project planned again counts no earlier
flow's review. A run whose records are gone
is named in the breakdown rather than counted, and a flow named by nothing fails naming why;
no figure is ever a default.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple, NewType

from onebudgetspec_sdk import report

from orchestrator import plan_review, spike_flow

#: The variable onebudgetspec sets to the id of the budget whose command is running.
BUDGET_ENV = "ONEBUDGETSPEC_BUDGET_ID"
#: The draft planner's run id, and the plan project the flow's reviews judged.
FLOW_ENV = "PLAN_CHECKLIST_FLOW"
PROJECT_ENV = "PLAN_CHECKLIST_PROJECT"
ADDED_SECONDS = "plan-checklist-added-seconds"
TOKENS = "plan-checklist-tokens"
#: The finalize planner's run, beside the draft's, as `scripts/plan-brief.sh` names it.
FINALIZE_SUFFIX = "-finalize"
#: The engine's record of a run's launch, and the instant in it the launch began.
LAUNCH_FILE = "launch.json"
LAUNCHED = "started_at"
#: What labels a checklist call in a planning dispatch's pointer file.
ROLE = "llmlint"
#: The label naming the checklist run a record belongs to, which the script stamps.
RUN_LABEL = "orchestrator.plan-checklist"
#: The label the planner's panel gives its harness judge, `graphs/planner.yaml`'s `reviewer`.
REVIEWER = "reviewer"
#: The harness whose recorded input counts its cache reads.
CODEX = "codex"
#: A qualified plan project, `<source>:<project>`.
QUALIFIED = re.compile(r"[A-Za-z0-9_.-]+:\S+\Z")

#: The checklist run a recorded call belongs to: the label the script stamps on every record
#: one of its runs writes, or the call's own history id when it carries none.
ChecklistRun = NewType("ChecklistRun", str)


class FlowError(Exception):
    """A flow these budgets cannot be read for, with the reason."""


class Call(NamedTuple):
    """One recorded checklist judge call: one llmlint batch on one harness candidate."""

    run: ChecklistRun
    started: datetime
    seconds: float
    harness: str
    input: int
    output: int
    cache_read: int
    #: A call whose history is gone: it holds its decision's place, so the decisions after it
    #: still pair with their own reviewer turns, and counts nothing.
    lost: bool = False

    @property
    def ended(self) -> datetime:
        return self.started + timedelta(seconds=self.seconds)

    @property
    def tokens(self) -> int:
        return self.input + self.output

    @property
    def cache_excluded(self) -> int:
        """Input less the cache reads a Codex record counts inside it, plus output."""
        uncached = self.input - self.cache_read if self.harness.startswith(CODEX) else self.input
        return uncached + self.output


class Stage(NamedTuple):
    """One run of the flow the checklist judged in, its decisions and what it lost."""

    label: str
    decisions: list[list[Call]]
    #: The reviewer's time on each of this dispatch's decisions, or ``None`` for a review,
    #: which has no reviewer beside its checklist and waits on it whole.
    reviewer: list[float] | None
    missing: list[str]


def _count(record: Mapping[str, object], key: str, where: object, *, required: bool) -> int:
    """A recorded count: a whole number not below zero, or zero for one never recorded.

    A count a record leaves unset — a failed candidate's usage, a cache it never wrote — is
    zero; one ``required`` must be there. Any other value is not a measurement, so it fails
    naming where it was read rather than counting as anything.
    """
    value = record.get(key)
    if value is None and not required:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FlowError(f"{where} records {key} as {value!r}, which is no count")
    return value


def _moment(value: object, where: object) -> datetime:
    """A recorded instant, which has to say its zone to be ordered against another."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise FlowError(f"{where} records no instant ({exc})") from exc
    if parsed.tzinfo is None:
        raise FlowError(f"{where} records {value!r}, an instant with no zone")
    return parsed


def _text(record: Mapping[str, object], key: str, where: object) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise FlowError(f"{where} names no {key}")
    return value


def calls(pointers: Path, *, by_role: bool) -> tuple[list[Call], list[str]]:
    """Every checklist call ``pointers`` names, and each one whose history is gone.

    ``by_role`` keeps only the lines labelled ``role=llmlint``, which is how a planning
    dispatch's checklist calls are told from its worker's and reviewer's in one file.
    """
    found: list[Call] = []
    missing: list[str] = []
    for number, line in enumerate(pointers.read_text(encoding="utf-8").splitlines(), 1):
        where = f"{pointers}:{number}"
        try:
            pointer = json.loads(line)
        except ValueError as exc:
            raise FlowError(f"{where} is not a history pointer ({exc})") from exc
        labels = pointer.get("labels") if isinstance(pointer, dict) else None
        if not isinstance(labels, dict):
            raise FlowError(f"{where} is not a history pointer: it carries no labels")
        if by_role and labels.get("role") != ROLE:
            continue
        stamped = labels.get(RUN_LABEL)
        run = ChecklistRun(
            stamped if isinstance(stamped, str) and stamped else _text(pointer, "history_id", where)
        )
        history = Path(_text(pointer, "history_file", where))
        if not history.is_file():
            missing.append(f"the call started {pointer.get('started')}: {history} is gone")
            when = _moment(pointer.get("started"), where)
            found.append(Call(run, when, 0.0, "", 0, 0, 0, lost=True))
            continue
        for record_line in history.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(record_line)
            except ValueError as exc:
                raise FlowError(f"{history} holds a line that is no record ({exc})") from exc
            if not isinstance(record, dict) or record.get("type") != "run":
                continue
            held = record.get("usage")
            if held is not None and not isinstance(held, dict):
                raise FlowError(f"{history} records usage as {held!r}, which is no usage")
            usage: Mapping[str, object] = held or {}
            found.append(
                Call(
                    run,
                    _moment(record.get("started_at"), history),
                    _count(record, "duration_ms", history, required=True) / 1000,
                    _text(record, "harness_id", history),
                    _count(usage, "input_tokens", history, required=False),
                    _count(usage, "output_tokens", history, required=False),
                    _count(usage, "cache_read_tokens", history, required=False),
                )
            )
    return found, missing


def decisions(found: Sequence[Call]) -> list[list[Call]]:
    """The calls grouped into the checklist runs they belong to, in the order each began."""
    grouped: dict[ChecklistRun, list[Call]] = {}
    for call in found:
        grouped.setdefault(call.run, []).append(call)
    return sorted(grouped.values(), key=lambda decision: min(call.started for call in decision))


def span(decision: Sequence[Call]) -> float:
    """How long a decision's recorded calls took, first start to last end."""
    held = [call for call in decision if not call.lost]
    if not held:
        return 0.0
    return (max(call.ended for call in held) - min(call.started for call in held)).total_seconds()


def reviewer_seconds(reports: Path) -> list[float]:
    """The reviewer's time on each judge turn of the onejudge reports under ``reports``.

    A judge turn's time is every candidate the turn tried, in the order the reports list
    them, across the reports in the order the dispatches wrote them. onejudge omits the
    attribution list when it is empty, and stamps each entry of a panel with its judge's
    label, so only the reviewer's are counted: an unlabelled entry is a lone judge's, and
    the plan checklist's llmlint judge records none.
    """
    turns: list[float] = []
    for path in sorted(reports.glob("*.json")) if reports.is_dir() else ():
        try:
            telemetry = json.loads(path.read_text(encoding="utf-8"))["telemetry"]
        except (ValueError, KeyError, TypeError) as exc:
            raise FlowError(f"{path} is not a onejudge report ({exc})") from exc
        attribution = telemetry.get("attribution", []) if isinstance(telemetry, dict) else None
        if not isinstance(attribution, list):
            raise FlowError(f"{path} is not a onejudge report: its attribution is no list")
        for turn in attribution:
            if not isinstance(turn, dict) or turn.get("role") != "judge":
                continue
            if turn.get("judge", REVIEWER) != REVIEWER:
                continue
            candidates = turn.get("candidates")
            if not isinstance(candidates, list) or not all(
                isinstance(candidate, dict) for candidate in candidates
            ):
                raise FlowError(f"{path} records a judge turn whose candidates are no list")
            turns.append(
                sum(
                    _count(candidate, "duration_ms", path, required=False) / 1000
                    for candidate in candidates
                )
            )
    return turns


def dispatch(runs: Path, run: str, label: str) -> Stage:
    """One planner dispatch's checklist decisions, beside its reviewer's judge turns."""
    pointers = runs / run / plan_review.POINTER_FILE
    if not pointers.is_file():
        return Stage(label, [], [], [f"{label} recorded no history pointer file at {pointers}"])
    found, missing = calls(pointers, by_role=True)
    return Stage(label, decisions(found), reviewer_seconds(runs / run / "reports"), missing)


def launched(runs: Path, run: str) -> datetime:
    """When ``run`` launched, as the engine recorded it in the run's launch record."""
    path = runs / run / LAUNCH_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FlowError(
            f"{path} records no launch ({exc}), so this flow's reviews of its plan cannot be "
            f"told from an earlier flow's"
        ) from exc
    if not isinstance(record, dict):
        raise FlowError(f"{path} is not a launch record")
    return _moment(record.get(LAUNCHED), path)


def review_started(review: Path) -> datetime:
    """When a review's checklist run started, from the name :func:`plan_review.checklist`
    gives its directory."""
    stamp = review.name.split("-", 1)[0]
    try:
        return datetime.strptime(stamp, plan_review.REVIEW_STARTED).replace(tzinfo=UTC)
    except ValueError as exc:
        raise FlowError(f"{review} is named for no instant a review started ({exc})") from exc


def flow(runs: Path, draft: str, project: str) -> list[Stage]:
    """The draft, the finalize when there was one, and each review of the plan since."""
    if not (runs / draft).is_dir():
        raise FlowError(f"no planning run {draft} is recorded under {runs}")
    since = launched(runs, draft)
    stages = [dispatch(runs, draft, f"draft run {draft}")]
    finalize = f"{draft}{FINALIZE_SUFFIX}"
    if (runs / finalize).is_dir():
        stages.append(dispatch(runs, finalize, f"finalize run {finalize}"))
    reviews = plan_review.reviews_of(project, runs)
    for review in sorted(reviews.iterdir()) if reviews.is_dir() else ():
        if review_started(review) < since:
            continue
        pointers = review / plan_review.POINTER_FILE
        label = f"plan-review run {review.name} of {project}"
        if not pointers.is_file():
            stages.append(Stage(label, [], None, [f"{label} recorded no history"]))
            continue
        found, missing = calls(pointers, by_role=False)
        stages.append(Stage(label, decisions(found), None, missing))
    return stages


def _seconds(value: float) -> str:
    return f"{value:.1f} s"


def added_seconds(stages: Sequence[Stage]) -> tuple[float, str]:
    """The seconds the flow waited on the checklist, and each decision's wait."""
    total = 0.0
    lines: list[str] = []
    for stage in stages:
        waits: list[str] = []
        for index, decision in enumerate(stage.decisions):
            checklist = span(decision)
            if stage.reviewer is None:
                wait = checklist
                waits.append(f"checklist {_seconds(checklist)}")
            else:
                reviewer = stage.reviewer[index] if index < len(stage.reviewer) else 0.0
                wait = max(0.0, checklist - reviewer)
                waits.append(
                    f"decision {index + 1}: checklist {_seconds(checklist)} less reviewer "
                    f"{_seconds(reviewer)} = {_seconds(wait)}"
                )
            total += wait
        lines.append(f"{stage.label}: " + ("; ".join(waits) or "no checklist call recorded"))
        lines += [f"missing from {stage.label}: {lost}" for lost in stage.missing]
    return total, "\n".join(lines)


def tokens(stages: Sequence[Stage]) -> tuple[float, str]:
    """The flow's checklist tokens, input plus output, and each call's figures."""
    every = [call for stage in stages for decision in stage.decisions for call in decision]
    lines: list[str] = []
    for stage in stages:
        number = 0
        for decision in stage.decisions:
            for call in (call for call in decision if not call.lost):
                number += 1
                lines.append(
                    f"{stage.label}, batch {number} ({call.harness}): input {call.input} + "
                    f"output {call.output} = {call.tokens}; cache reads {call.cache_read}"
                )
        if not number:
            lines.append(f"{stage.label}: no checklist call recorded")
        lines += [f"missing from {stage.label}: {lost}" for lost in stage.missing]
    lines.append(
        f"cache reads, reported separately: {sum(call.cache_read for call in every)} tokens"
    )
    lines.append(
        f"cache-excluded figure, supplementary: {sum(call.cache_excluded for call in every)} tokens"
    )
    return float(sum(call.tokens for call in every)), "\n".join(lines)


# llmlint: ignore-block[budget_commands_measure_directly] This module is the measurement
# itself, not a wrapper around another one: both budgets are registered under the one
# command their plan records, so the running budget's id is what chooses which of the two
# figures it computes, and an id naming neither is refused rather than measured as one.
def main(environment: Mapping[str, str] | None = None) -> int:
    """Report the running budget's figure and breakdown; 1 with the reason when it cannot."""
    environment = os.environ if environment is None else environment
    budget = environment.get(BUDGET_ENV, "")
    try:
        if budget not in (ADDED_SECONDS, TOKENS):
            raise FlowError(f"{BUDGET_ENV} names no plan-checklist budget: {budget!r}")
        draft = environment.get(FLOW_ENV, "")
        project = environment.get(PROJECT_ENV, "")
        if not draft or not project:
            raise FlowError(
                f"no planning flow is named: {FLOW_ENV} names its draft run and {PROJECT_ENV} "
                f"its plan project, as `just finish-plan` sets them when a flow ends"
            )
        if not spike_flow.RUN_ID.match(draft) or not QUALIFIED.match(project):
            raise FlowError(
                f"{FLOW_ENV}={draft!r} is not a run id or {PROJECT_ENV}={project!r} is not a "
                f"qualified plan project, so neither names a flow under the runs root"
            )
        runs = Path(
            environment.get(plan_review.RUNS_ROOT_ENV) or plan_review.DEFAULT_RUNS_ROOT
        ).absolute()
        stages = flow(runs, draft, project)
        value, detail = (added_seconds if budget == ADDED_SECONDS else tokens)(stages)
    except (FlowError, OSError, ValueError) as error:
        print(f"budget {budget or '(unnamed)'}: {error}", file=sys.stderr)
        return 1
    report(value, detail)
    return 0


# llmlint: ignore-end[budget_commands_measure_directly]


if __name__ == "__main__":
    raise SystemExit(main())
