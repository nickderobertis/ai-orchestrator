"""A plan's budgets: where each answer lives, its one shape, and what `just check-plan` refuses.

Every plan states its measurable requirements, its **budgets**, in two homes, and this
module states the shape of both once. `docs/budgets.md` states the principles, and the
templates, `docs/budgets.md` and `personas/planner.yaml` restate this shape;
`tests/test_plan_budgets_template.py` fails the moment a restatement parts from it.

* **A task's budgets** are the budgets that task owns, because the task carrying a budget is
  the one that implements its command. They are the task's `orchestrator.budgets` metadata
  (:data:`TASK_RECORD`): a list of :class:`Budget`, which `templates/plan-task.md.j2` renders
  as the task's `## Budgets` section through the partial `templates/plan-task-budgets.md.j2`.
* **The plan-level answers** are the project's `orchestrator.plan-budgets` metadata
  (:data:`PLAN_RECORD`): one :class:`PlanAnswers`, the resolved answers of the
  `plan-description` template the project's own description is rendered from.

**The metadata records are authoritative**, because a copy carries metadata and never
answers, so a record is what every reader reads, on authoring and board copies alike. The
rendered bodies are held to them: :func:`refusals` renders each task's section and the
plan's description from their records through the pinned plan store and refuses a body that
does not carry its rendering.

**Which plans predate the requirement is one tracked list**, `config/budgets-migration.yaml`:
adding to it is a reviewed change to this repository, so a plan cannot exempt itself. A
project it names is exempt from every refusal about its plan-level answers. The plan kinds
`orchestrator/design_approval.py` exempts from design approval are exempt here on the same
bound: a project holding exactly the nodes its exempt launch dispatches.

Three readers use this: `just check-plan` (:func:`refusals`), `just review-plan`
(:func:`task_record` and :func:`plan_record`), and the design-document writer, whose answers
`python -m orchestrator.plan_budgets <project>` prints (:func:`writer_answers`) and against
which the finish-plan flow holds the written document (:func:`design_refusals`).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, NamedTuple, NewType, Protocol, TypedDict

import yaml

from orchestrator import design_approval, plan_store, task_body
from orchestrator.plan_store import QualifiedProjectId, StoreTask
from orchestrator.project_store import hosted_origin
from orchestrator.root import REPO_ROOT

#: The task metadata key holding the budgets a task owns, as a JSON list of :class:`Budget`.
TASK_RECORD = "orchestrator.budgets"

#: The project metadata key holding the plan-level answers, as one :class:`PlanAnswers`.
PLAN_RECORD = "orchestrator.plan-budgets"

#: The template a plan's description is rendered from, registered in
#: `templates/templates.yaml` with `role: project`, and the reference its rendering records.
DESCRIPTION_TEMPLATE = "plan-description"
DESCRIPTION_REFERENCE = f"onepipeline:{DESCRIPTION_TEMPLATE}"

#: This host's template root, and the partial a task's `## Budgets` section renders from.
TEMPLATE_ROOT = REPO_ROOT / "templates"
TASK_PARTIAL = TEMPLATE_ROOT / "plan-task-budgets.md.j2"

#: The heading a task's budgets render under, exactly, so its anchor is `budgets`.
SECTION_HEADING = "## Budgets"
SECTION = re.compile(r"^## Budgets[ \t]*$", re.MULTILINE)

#: The caps, in characters, on the answers a design document restates: a budget's name, a
#: one-line summary, the 10x summary and the sizing paragraph. The manager's rulings.
NAME_LIMIT = 60
SUMMARY_LIMIT = 160
TEN_X_SUMMARY_LIMIT = 240
SIZING_LIMIT = 600

#: The one value a repo-wide effect states when the plan has none on that repository.
NO_EFFECT = "none"


class Direction(StrEnum):
    """Which side of its threshold a budget holds a measurement to.

    This repository's copy of onebudgetspec's `direction` vocabulary, held to the installed
    library's `onebudgetspec schema` by `tests/test_budgets_files.py`.
    """

    MAX = "max"
    MIN = "min"


class FileChange(StrEnum):
    """What the task owning a budget does to that budget's entry in its budgets file."""

    ADD = "add"
    CHANGE = "change"
    NONE = "none"


class Basis(StrEnum):
    """How solid a budget's number is: what it rests on."""

    MEASURED = "measured"
    ARITHMETIC = "arithmetic"
    DESIGN = "design"
    PUBLISHED_DOCS = "published docs"
    ESTIMATE = "estimate"


#: The one file name a budgets file has: onebudgetspec's convention, held to the installed
#: library's `onebudgetspec schema` by `tests/test_budgets_files.py`.
BUDGETS_FILE = "budgets.yaml"


class Source(StrEnum):
    """Whether a command executes measured work or reads existing records."""

    DIRECT = "direct"
    TELEMETRY = "telemetry"


class DataChoice(StrEnum):
    """How the plan makes one kind of data realistic."""

    GENERATOR = "generator"
    FIXTURE = "fixture"
    HYBRID = "hybrid"
    REAL_SAMPLE = "real-sample"


#: The tracked list of plans that predate the requirement.
MIGRATION_LIST = REPO_ROOT / "config" / "budgets-migration.yaml"

#: A reason that says nothing: a placeholder written to get an entry past the shape.
PLACEHOLDER = re.compile(r"\A\s*(?:todo|tbd|fixme|xxx|n/?a|none|placeholder|\.+|-+)?\s*\Z", re.I)

#: A budget's id in its budgets file, and every reference to one.
BudgetId = NewType("BudgetId", str)


class BudgetsError(OSError):
    """A budgets record that is there but cannot be read as one."""


@dataclass(frozen=True)
class Budget:
    """One budget a task owns, as its `orchestrator.budgets` record states it."""

    id: BudgetId
    name: str
    # llmlint: ignore-block[modern_domain_modeling] `basis`, `file_change` and `direction` stay the text the record states, rather than a `Basis`, `FileChange` or `Direction` refused at the read, so that a value outside a vocabulary is refused by the plan check's own named rule against the task that holds it, the refusal `just check-plan` owes an author, instead of as an unreadable record.  # noqa: E501
    basis: str
    repository: str
    file: str
    file_change: str
    measure: str
    inner_measure_reason: str
    unit: str
    direction: str
    threshold: int | float
    workload: str
    evidence: str
    command: str
    # llmlint: ignore-end[modern_domain_modeling]


@dataclass(frozen=True)
class ModernBudget(Budget):
    """Version two adds explicit command mode and elapsed runtime."""

    schema_version: int
    source: Source
    check_runtime_seconds: int | float


@dataclass(frozen=True)
class ChecklistEntry:
    """One concern the plan was checked against, answered with a budget or a reason."""

    concern: str
    budget: BudgetId
    not_applicable: str
    summary: str


@dataclass(frozen=True)
class ModernChecklistEntry(ChecklistEntry):
    """Scope is author-declared, never inferred from a reason."""

    in_scope: bool


@dataclass(frozen=True)
class RepoWideEffect:
    """The plan's expected effect on one repository's repo-wide budgets."""

    repository: str
    budget: BudgetId
    effect: str
    summary: str


@dataclass(frozen=True)
class RealisticData:
    """How the plan makes one kind of data realistic, and why."""

    data: str
    choice: DataChoice
    reason: str
    artifact: str
    summary: str


@dataclass(frozen=True)
class SpikeFinding:
    """One spike finding that changed the plan."""

    spike: str
    finding: str
    changed: str
    summary: str


@dataclass(frozen=True)
class PlanAnswers:
    """The plan-level answers, the `plan-description` template's variables in its order."""

    overview: str
    sizing: str
    workload: str
    ten_x_summary: str
    ten_x: str
    checklist: tuple[ChecklistEntry, ...]
    repo_wide_effects: tuple[RepoWideEffect, ...]
    realistic_data: tuple[RealisticData, ...]
    spike_findings: tuple[SpikeFinding, ...]


@dataclass(frozen=True)
class ModernPlanAnswers(PlanAnswers):
    """The versioned project record; legacy records retain their exact shape."""

    schema_version: int


SCHEMA_VERSION = 2
CANONICAL_ROOT_CONCERNS = ("gate time", "change cycle time")
MODERN_BUDGET_KEYS = tuple(field.name for field in dataclasses.fields(ModernBudget))
MODERN_PLAN_VARIABLES = tuple(field.name for field in dataclasses.fields(ModernPlanAnswers))


#: The keys of a task's budget, and the plan description's variables, each in order.
BUDGET_KEYS = tuple(field.name for field in dataclasses.fields(Budget))
PLAN_VARIABLES = tuple(field.name for field in dataclasses.fields(PlanAnswers))

#: The plan description's text variables, and the caps two of them carry.
PLAN_TEXTS = ("overview", "sizing", "workload", "ten_x_summary", "ten_x")


class Refusal(NamedTuple):
    """One rule a plan's budgets break: the node it is about, the answer, and why."""

    node: str | None
    field: str
    reason: str


class MigrationEntry(NamedTuple):
    """One plan that predates the budgets requirement, and why it is exempt."""

    project: QualifiedProjectId
    reason: str


class TaskBudgets(NamedTuple):
    """One task as the budget checks read it: its node, its body and its budgets record.

    ``record`` is the task's :data:`TASK_RECORD` metadata as the store holds it, or ``None``
    when it carries none; it is read for its shape by :func:`parse_budgets`.
    """

    node: str
    content: str | None
    record: object


class WriterAnswers(TypedDict):
    """The design document's budget answers, exactly as the writer copies them."""

    #: The migration list's reason when it names the plan, and `""` otherwise.
    predates_budgets: str
    #: The plan's :data:`PLAN_RECORD`, or `{}` for a plan that predates budgets.
    plan_budgets: Mapping[str, object]
    #: Every budget of every task, each with its owning node and that task's location.
    budgets: list[dict[str, object]]


class _Kind(NamedTuple):
    """What one declared field type admits, how a refusal names it, and what it reads as."""

    holds: Callable[[object], bool]
    said: str
    read: Callable[[object], object]


#: The field types an entry model declares, by the annotation it is declared under.
_KINDS = {
    "int": _Kind(
        lambda v: type(v) is int and v == SCHEMA_VERSION, "integer literal 2", lambda v: v
    ),
    "bool": _Kind(lambda v: type(v) is bool, "a boolean", lambda v: v),
    "Source": _Kind(
        lambda v: isinstance(v, str) and v in tuple(Source),
        "direct or telemetry",
        lambda v: Source(str(v)),
    ),
    "str": _Kind(lambda value: isinstance(value, str), "a string", lambda value: value),
    "BudgetId": _Kind(
        lambda value: isinstance(value, str), "a string", lambda value: BudgetId(str(value))
    ),
    "int | float": _Kind(
        lambda value: (
            isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        ),
        "a finite number",
        lambda value: value,
    ),
    "DataChoice": _Kind(
        lambda value: isinstance(value, str) and value in tuple(DataChoice),
        f"one of {', '.join(DataChoice)}",
        lambda value: DataChoice(str(value)),
    ),
}


class _EntryModel(Protocol):
    """An entry model: one of the dataclasses above, whose fields an entry is held to."""

    __dataclass_fields__: ClassVar[dict[str, dataclasses.Field[object]]]


#: Each list variable of the plan description, and the entry model its objects are held to.
PLAN_LISTS: dict[str, type[_EntryModel]] = {
    "checklist": ChecklistEntry,
    "repo_wide_effects": RepoWideEffect,
    "realistic_data": RealisticData,
    "spike_findings": SpikeFinding,
}


def _entries[E: _EntryModel](held: object, field: str, model: type[E]) -> tuple[E, ...]:
    """``held`` as entries of ``model``, or :class:`BudgetsError` naming the fault.

    Each entry is an object holding exactly the model's keys, each a string — a threshold a
    number, a data choice one of :class:`DataChoice` — so an answer of any other shape is
    refused naming it rather than read as less than it says.
    """
    if not isinstance(held, list):
        raise BudgetsError(f"`{field}` is not a list of objects")
    declared = {one.name: one.type for one in dataclasses.fields(model)}
    entries: list[E] = []
    for position, entry in enumerate(held, start=1):
        if not isinstance(entry, dict) or set(entry) != set(declared):
            raise BudgetsError(
                f"`{field}` entry {position} does not hold exactly the keys "
                f"{', '.join(declared)}: {entry!r}"
            )
        for key, value in entry.items():
            kind = _KINDS.get(str(declared[key]), _KINDS["str"])
            if not kind.holds(value):
                raise BudgetsError(
                    f"`{field}` entry {position} holds {value!r} as its `{key}`, which is not "
                    f"{kind.said}"
                )
        entries.append(
            model(
                **{
                    key: _KINDS.get(str(declared[key]), _KINDS["str"]).read(value)
                    for key, value in entry.items()
                }
            )
        )
    return tuple(entries)


def parse_budgets(record: object) -> tuple[Budget, ...]:
    """A task's ``record`` as its budgets, or :class:`BudgetsError` naming what is malformed."""
    if not isinstance(record, list):
        raise BudgetsError(f"`{TASK_RECORD}` is not a list of objects")
    if all(not isinstance(entry, dict) or "schema_version" not in entry for entry in record):
        return _entries(record, TASK_RECORD, Budget)
    parsed: list[Budget] = []
    for position, entry in enumerate(record, 1):
        model = ModernBudget if isinstance(entry, dict) and "schema_version" in entry else Budget
        budget = _entries([entry], f"{TASK_RECORD}[{position}]", model)[0]
        if isinstance(budget, ModernBudget) and budget.check_runtime_seconds < 0:
            raise BudgetsError(f"`{TASK_RECORD}[{position}].check_runtime_seconds` must be >= 0")
        parsed.append(budget)
    return tuple(parsed)


def parse_plan(record: object) -> PlanAnswers:
    """A project's ``record`` as the plan-level answers, or :class:`BudgetsError`."""
    modern = isinstance(record, dict) and "schema_version" in record
    keys = MODERN_PLAN_VARIABLES if modern else PLAN_VARIABLES
    if not isinstance(record, dict) or set(record) != set(keys):
        raise BudgetsError(
            f"`{PLAN_RECORD}` does not hold exactly the {len(keys)} answers {', '.join(keys)}"
        )
    for text in PLAN_TEXTS:
        if not isinstance(record[text], str):
            raise BudgetsError(f"`{PLAN_RECORD}`'s `{text}` answer is not text")
    if modern and (
        type(record["schema_version"]) is not int or record["schema_version"] != SCHEMA_VERSION
    ):
        raise BudgetsError("`schema_version` must be integer literal 2")
    model = ModernPlanAnswers if modern else PlanAnswers
    return model(
        **({"schema_version": SCHEMA_VERSION} if modern else {}),
        overview=record["overview"],
        sizing=record["sizing"],
        workload=record["workload"],
        ten_x_summary=record["ten_x_summary"],
        ten_x=record["ten_x"],
        checklist=_entries(
            record["checklist"], "checklist", ModernChecklistEntry if modern else ChecklistEntry
        ),
        repo_wide_effects=_entries(
            record["repo_wide_effects"], "repo_wide_effects", RepoWideEffect
        ),
        realistic_data=_entries(record["realistic_data"], "realistic_data", RealisticData),
        spike_findings=_entries(record["spike_findings"], "spike_findings", SpikeFinding),
    )


def task_record(metadata: Mapping[str, object]) -> object:
    """The budgets record a task's ``metadata`` carries, or ``None`` when it carries none."""
    return metadata.get(TASK_RECORD)


def plan_record(project: str | None) -> object:
    """``project``'s plan-level record, or ``None`` for no project, none held, or no read.

    For the review keys, which cover the record as the store holds it. A project the store
    cannot answer for is keyed as carrying none rather than raised here, because the plan
    check is what refuses it, by name.
    """
    if project is None:
        return None
    try:
        metadata = plan_store.project_record(project).get("metadata")
    except OSError:
        return None
    return metadata.get(PLAN_RECORD) if isinstance(metadata, Mapping) else None


def tasks_of_plan(plan: object) -> list[TaskBudgets]:
    """Every task of a loaded ``plan`` as the budget checks read it, read leniently.

    The document `onepipeline plan check` hands the registered check carries each task's
    metadata map verbatim, so the record is read from there; a task with no string `id` is
    skipped, because the engine's loader has already refused it.
    """
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else None
    found: list[TaskBudgets] = []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, Mapping) or not isinstance(task.get("id"), str):
            continue
        metadata = task.get("metadata")
        content = task.get("task")
        found.append(
            TaskBudgets(
                task["id"],
                content if isinstance(content, str) else None,
                task_record(metadata) if isinstance(metadata, Mapping) else None,
            )
        )
    return found


def tasks_of_records(records: Sequence[StoreTask]) -> list[TaskBudgets]:
    """Every store record as the budget checks read it, for the path that reads the store."""
    return [
        TaskBudgets(record.node_id, record.content, task_record(record.metadata))
        for record in records
    ]


def render_section(record: object) -> str:
    """The `## Budgets` section a task owning ``record`` renders, through the pinned store.

    The partial alone, from :data:`TASK_PARTIAL`, so the check renders exactly the text
    `plan-task` includes; an empty string for a task owning no budget.
    """
    rendered = plan_store.sdk(
        plan_store.client().template_render(
            str(TASK_PARTIAL),
            search_path=[str(TEMPLATE_ROOT)],
            answers={"budgets": _json(record)},
        )
    )
    return rendered.body


# llmlint: ignore[suppressions_justified] The store's typed answers take pydantic's `JsonValue`, which this package does not depend on directly, so a decoded JSON value is handed over as the `Any` `json.loads` answers; the round trip is what proves it JSON.  # noqa: E501
def _json(value: object) -> Any:
    """``value`` as the JSON value it is, for the store's typed answers.

    Every record here was decoded from the store's own JSON metadata, so it is JSON by
    construction; round-tripping it states that to the type checker and refuses, as a
    :class:`BudgetsError`, anything that is not.
    """
    try:
        decoded = json.loads(json.dumps(value))
    except (TypeError, ValueError) as exc:
        raise BudgetsError(f"a budgets record is not JSON: {exc}") from exc
    return decoded


def render_description(record: object) -> str:
    """The description `plan-description` renders from ``record``, through the pinned tools.

    The loader is the pinned engine's `template resolve`, the one a planner pipes into the
    store's `project create`, so the check renders through the chain the planner did.
    """
    loader = _resolved_loader(DESCRIPTION_TEMPLATE)
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as written:
        written.write(loader)
        written.flush()
        rendered = plan_store.sdk(
            plan_store.client().template_render(
                template_loader=written.name,
                answers=_answers(record),
            )
        )
    return rendered.body


def _answers(record: object) -> dict[str, Any]:
    """``record`` as the answers mapping the store renders, or :class:`BudgetsError`."""
    held = _json(record)
    if not isinstance(held, dict):
        raise BudgetsError(f"`{PLAN_RECORD}` is not an object of answers")
    return held


def _resolved_loader(name: str) -> str:
    """The loader document the pinned engine states for the host template ``name``."""
    command = [
        str(REPO_ROOT / ".venv" / "bin" / "onepipeline"),
        "template",
        "resolve",
        name,
        "--json",
        "--template-root",
        str(TEMPLATE_ROOT),
    ]
    try:
        done = subprocess.run(
            command, cwd=REPO_ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True
        )
    except OSError as exc:
        raise BudgetsError(
            f"the pinned engine could not be run to resolve the {name} template ({exc}); "
            f"provision this checkout with `just bootstrap`, then retry"
        ) from exc
    if done.returncode != 0:
        raise BudgetsError(f"`onepipeline template resolve {name}` refused: {done.stderr.strip()}")
    return done.stdout


def answers_digest(record: object) -> str:
    """The digest the plan store records for resolved answers ``record``.

    SHA-256 of the canonical JSON the store states for it — keys sorted at every depth, no
    insignificant whitespace, UTF-8 — so a copy is held to the store's own record.
    """
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _one_line(text: str, limit: int) -> bool:
    return "\n" not in text and "\r" not in text and len(text) <= limit


def _is_budgets_file(path: str) -> bool:
    if not path or "\\" in path:
        return False
    held = PurePosixPath(path)
    return (
        not held.is_absolute()
        and held.name == BUDGETS_FILE
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def budget_refusals(node: str, budgets: Sequence[Budget]) -> list[Refusal]:
    """Every rule the budgets one task owns break, each refusal naming that task."""
    found: list[Refusal] = []

    def refuse(reason: str) -> None:
        found.append(Refusal(node, "budgets", reason))

    for position, budget in enumerate(budgets, start=1):
        named = budget.id.strip() or f"budget {position}"
        if not budget.id.strip():
            refuse(f"{named} has no `id`, and every budget is named by one")
        if not budget.name.strip() or not _one_line(budget.name, NAME_LIMIT):
            refuse(
                f"budget {named!r} has the `name` {budget.name!r}, which is not a short "
                f"plain-English name on one line of at most {NAME_LIMIT} characters"
            )
        if budget.basis.strip() not in tuple(Basis):
            refuse(
                f"budget {named!r} has the `basis` {budget.basis.strip()!r}, which is not one "
                f"of {', '.join(Basis)}"
            )
        if budget.file_change.strip() not in tuple(FileChange):
            refuse(
                f"budget {named!r} has the `file_change` {budget.file_change.strip()!r}, "
                f"which is not one of {', '.join(FileChange)}"
            )
        if not _is_budgets_file(budget.file):
            refuse(
                f"budget {named!r} goes in the file {budget.file!r}, which is not a relative "
                f"path whose last component is `{BUDGETS_FILE}`"
            )
        if budget.direction.strip() not in tuple(Direction):
            refuse(
                f"budget {named!r} has the `direction` {budget.direction.strip()!r}, which is "
                f"not {' or '.join(Direction)}"
            )
        if not budget.command.strip():
            refuse(
                f"budget {named!r} names no `command`, and the command is what performs the "
                f"measurement"
            )
    return found


def section_refusals(task: TaskBudgets) -> list[Refusal]:
    """Every way ``task``'s body and its budgets record disagree, or its record is malformed.

    A body carrying a `## Budgets` section and no record is refused, because the record is
    authoritative and a section with none is one no reader of the record sees. A record is
    rendered through :data:`TASK_PARTIAL` and the body must carry that rendering whole.
    """
    has_section = task.content is not None and SECTION.search(task.content) is not None
    if task.record is None or task.record == []:
        if not has_section:
            return []
        return [
            Refusal(
                task.node,
                "budgets",
                f"its body carries a `{SECTION_HEADING}` section but its `{TASK_RECORD}` "
                f"metadata states no budget, and the record is what every reader reads; "
                f"record the budgets as `{TASK_RECORD}` and regenerate the task with them as "
                f"its `budgets` answer, or drop the section",
            )
        ]
    try:
        budgets = parse_budgets(task.record)
    except BudgetsError as exc:
        return [Refusal(task.node, "budgets", f"its `{TASK_RECORD}` metadata is malformed: {exc}")]
    found = budget_refusals(task.node, budgets)
    if not _carries(task.content or "", render_section(task.record).strip("\n")):
        found.append(
            Refusal(
                task.node,
                "budgets",
                f"its body does not carry the `{SECTION_HEADING}` section its `{TASK_RECORD}` "
                f"metadata renders, so the two state different budgets; regenerate the task "
                f"with its record as the `budgets` answer",
            )
        )
    return found


def _carries(content: str, section: str) -> bool:
    """Whether ``content`` holds ``section`` as whole lines, wherever it sits in the body.

    As whole lines rather than as bytes, because a store keeps a body's trailing newlines
    its own way: a section closing the body ends where the body does.
    """
    lines, block = content.splitlines(), section.splitlines()
    return any(
        lines[start : start + len(block)] == block for start in range(len(lines) - len(block) + 1)
    )


def _origin(repository: str) -> str:
    """``repository`` as the normalized origin it names, or as written when it names none."""
    return hosted_origin(repository) or repository


def _plan_repositories(plan: object) -> set[str]:
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else None
    named: set[str] = set()
    for task in tasks if isinstance(tasks, list) else []:
        repo = task.get("repo") if isinstance(task, Mapping) else None
        if isinstance(repo, str) and repo:
            named.add(_origin(repo))
    return named


def _plan_nodes(plan: object) -> set[str]:
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else None
    return {
        task["id"]
        for task in (tasks if isinstance(tasks, list) else [])
        if isinstance(task, Mapping) and isinstance(task.get("id"), str)
    }


def plan_rule_refusals(
    answers: PlanAnswers, owned: Mapping[str, Sequence[Budget]], plan: object
) -> list[Refusal]:
    """Every rule the plan-level ``answers`` break, read against every task's budgets."""
    found: list[Refusal] = []

    def refuse(field: str, reason: str) -> None:
        found.append(Refusal(None, field, reason))

    for text in PLAN_TEXTS:
        if not getattr(answers, text).strip():
            refuse(text, f"the `{text}` answer is empty, and every plan states it")
    if len(answers.sizing) > SIZING_LIMIT:
        refuse(
            "sizing",
            f"the `sizing` answer runs to {len(answers.sizing)} characters, over the "
            f"{SIZING_LIMIT} a design document's opening paragraph is held to",
        )
    if not _one_line(answers.ten_x_summary, TEN_X_SUMMARY_LIMIT):
        refuse(
            "ten_x_summary",
            f"the `ten_x_summary` answer is not one line of at most {TEN_X_SUMMARY_LIMIT} "
            f"characters",
        )
    ids = [budget.id.strip() for held in owned.values() for budget in held]
    for position, entry in enumerate(answers.checklist, start=1):
        concern = entry.concern.strip() or f"entry {position}"
        covered, not_applicable = entry.budget.strip(), entry.not_applicable.strip()
        if isinstance(entry, ModernChecklistEntry):
            if covered and not entry.in_scope:
                refuse("checklist", f"{concern}: a covered concern must have in_scope=true")
            if concern.casefold() in CANONICAL_ROOT_CONCERNS:
                refuse(
                    "checklist",
                    f"{concern}: existing root budgets govern this; "
                    "expected effects belong in repo_wide_effects",
                )
        if bool(covered) == bool(not_applicable):
            refuse(
                "checklist",
                f"checklist entry {position} ({concern}) answers "
                f"{'both' if covered else 'neither'} of `budget` and `not_applicable`; each "
                f"concern gets a budget or a one-line n/a, exactly one of the two",
            )
        elif covered and covered not in ids:
            refuse(
                "checklist",
                f"checklist entry {position} ({concern}) names the budget {covered!r}, which "
                f"no task of the plan owns",
            )
        _summary(
            refuse,
            "checklist",
            f"checklist entry {position} ({concern})",
            entry.summary,
            wanted=bool(not_applicable),
            rule="non-empty exactly when its `not_applicable` is",
        )
    repeated = sorted({one for one in ids if ids.count(one) > 1})
    if repeated:
        refuse(
            "budgets",
            f"the budget id(s) {', '.join(map(repr, repeated))} repeat across the plan's tasks",
        )
    modern = isinstance(answers, ModernPlanAnswers)
    pairs: set[tuple[str, str]] = set()
    for position, effect in enumerate(answers.repo_wide_effects, start=1):
        named = f"repo-wide effect {position} ({effect.repository.strip() or 'no repository'})"
        if modern:
            pair = (effect.repository, effect.budget)
            if pair in pairs:
                refuse(
                    "repo_wide_effects",
                    f"{named} repeats repository/budget {pair!r}; consolidate its effects",
                )
            pairs.add(pair)
            if (
                not effect.budget.strip()
                or not effect.effect.strip()
                or effect.effect.strip().casefold() == NO_EFFECT
            ):
                refuse(
                    "repo_wide_effects",
                    f"{named} must name a nonempty root budget and an actual effect, never none",
                )
            if effect.repository != _origin(
                effect.repository
            ) or effect.repository not in _plan_repositories(plan):
                refuse(
                    "repo_wide_effects",
                    f"{named} must name the normalized origin of a changed repository",
                )
        if not effect.effect.strip():
            refuse(
                "repo_wide_effects",
                f"{named} states an empty `effect`; it is `{NO_EFFECT}` when there is none",
            )
        _summary(
            refuse,
            "repo_wide_effects",
            named,
            effect.summary,
            wanted=effect.effect.strip() != NO_EFFECT,
            rule=f"non-empty exactly when its `effect` is not `{NO_EFFECT}`",
        )
    summarized: tuple[tuple[str, Sequence[RealisticData | SpikeFinding]], ...] = (
        ("realistic_data", answers.realistic_data),
        ("spike_findings", answers.spike_findings),
    )
    for name, entries in summarized:
        for position, item in enumerate(entries, start=1):
            _summary(
                refuse,
                name,
                f"`{name}` entry {position}",
                item.summary,
                wanted=True,
                rule="never empty",
            )
    # An entry stating no effect states nothing, so it covers no repository.
    stated = {
        _origin(entry.repository.strip())
        for entry in answers.repo_wide_effects
        if entry.effect.strip()
    }
    for repository in sorted(_plan_repositories(plan) - stated) if not modern else []:
        refuse(
            "repo_wide_effects",
            f"`repo_wide_effects` states no effect for {repository}, a repository the plan's "
            f"tasks change; each one gets an entry, with the effect `{NO_EFFECT}` where it has "
            f"none",
        )
    return found


def _summary(
    refuse: Callable[[str, str], None],
    field: str,
    named: str,
    summary: str,
    *,
    wanted: bool,
    rule: str,
) -> None:
    """Refuse ``summary`` unless it is present exactly when ``wanted``, on one capped line."""
    if bool(summary.strip()) != wanted:
        refuse(
            field,
            f"{named} {'has no' if wanted else 'has a'} `summary`, and its summary is {rule}",
        )
    elif wanted and not _one_line(summary, SUMMARY_LIMIT):
        refuse(field, f"{named}'s `summary` is not one line of at most {SUMMARY_LIMIT} characters")


def description_refusals(project: str, held: Mapping[str, object]) -> list[Refusal]:
    """Every way ``project``'s description is not the rendering of its plan-level record.

    ``held`` is the project record the store answers. Its content must be the
    `plan-description` rendering of its :data:`PLAN_RECORD` and its provenance must name that
    template; a copy whose references the store rewrote is held to the store's own rule for
    such a rendering instead — the body digest it re-recorded is its content's, and the
    answers digest is the record's — never by loosening the comparison for every plan. The
    body with its metadata slot is held to the issue-body limit too.
    """
    metadata = held.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    record = metadata.get(PLAN_RECORD)
    content = held.get("content")
    content = content if isinstance(content, str) else ""
    found: list[Refusal] = []
    provenance = metadata.get(design_approval.PROVENANCE)
    provenance = provenance if isinstance(provenance, Mapping) else {}
    if provenance.get("template") != DESCRIPTION_REFERENCE:
        found.append(
            Refusal(
                None,
                "description",
                f"{project}'s description records {provenance.get('template')!r} as the "
                f"template it was rendered from, not {DESCRIPTION_REFERENCE}; create it with "
                f"`onepipeline template resolve {DESCRIPTION_TEMPLATE} --json | onetaskgraph "
                f"project create <source> --id <project> ... --template-loader - --answers "
                f"<file>`",
            )
        )
    elif content != render_description(record):
        body = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        copied = isinstance(metadata.get(plan_store.ORIGIN_KEY), str)
        # llmlint: ignore[changed_behavior_has_e2e] The adopted store's `project copy` carries
        # a description verbatim, rewriting references only in a copied document, so no real
        # copy reaches this acceptance yet: `tests/plan_tooling/test_copy_plan_recipe_e2e.py`
        # copies a description naming a task's location, sees it land unrewritten and checked,
        # and sees an edit after the copy refused, and fails the day the store rewrites it.
        if not (
            copied
            and provenance.get("body_digest") == body
            and provenance.get("answers_digest") == answers_digest(record)
        ):
            found.append(
                Refusal(
                    None,
                    "description",
                    f"{project}'s description is not the `{DESCRIPTION_TEMPLATE}` rendering of "
                    f"its `{PLAN_RECORD}` metadata, so the two state different answers; "
                    f"recreate it from the record with `project create --id`",
                )
            )
    size = task_body.measure(content, metadata)
    if size > task_body.BODY_LIMIT:
        found.append(
            Refusal(
                None,
                "description",
                f"{project}'s description measures {size:,} characters with its metadata, over "
                f"the {task_body.BODY_LIMIT:,}-character limit GitHub puts on an issue body, "
                f"so the board would refuse the plan's project issue; shorten its answers "
                f"({task_body.UNMEASURED})",
            )
        )
    return found


def owned_budgets(tasks: Sequence[TaskBudgets]) -> dict[str, tuple[Budget, ...]]:
    """Every readable task's budgets, by node; a malformed record is refused elsewhere."""
    owned: dict[str, tuple[Budget, ...]] = {}
    for task in tasks:
        if task.record is None:
            continue
        try:
            owned[task.node] = parse_budgets(task.record)
        except BudgetsError:
            continue
    return owned


def migration_entries(path: Path | None = None) -> list[MigrationEntry]:
    """Every entry of the migration list at ``path``, the tracked one by default, held to its shape.

    Raises :class:`ValueError` naming the first entry that is not a qualified project id
    with a non-empty reason of its own, and any id the list names twice: a list that is not
    one of those is not one this repository reviewed.
    """
    path = MIGRATION_LIST if path is None else path
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} is not YAML: {exc}") from exc
    if not isinstance(loaded, list):
        raise ValueError(f"{path} holds no list of {{project, reason}} entries")
    entries: list[MigrationEntry] = []
    for position, entry in enumerate(loaded, start=1):
        project = entry.get("project") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not isinstance(entry, dict) or set(entry) != {"project", "reason"}:
            raise ValueError(
                f"{path} entry {position} is not exactly {{project, reason}}: {entry!r}"
            )
        if not isinstance(project, str) or not design_approval.QUALIFIED.fullmatch(project):
            raise ValueError(
                f"{path} entry {position} names {project!r}, not a qualified project id"
            )
        if not isinstance(reason, str) or PLACEHOLDER.fullmatch(reason):
            raise ValueError(f"{path} entry {position} ({project}) gives no reason: {reason!r}")
        entries.append(MigrationEntry(QualifiedProjectId(project), reason.strip()))
    named = [entry.project for entry in entries]
    repeated = sorted({project for project in named if named.count(project) > 1})
    if repeated:
        raise ValueError(f"{path} names {', '.join(repeated)} more than once")
    return entries


def migrated(project: str, path: Path | None = None) -> str | None:
    """The reason the migration list gives for ``project``, or ``None`` when it names none."""
    return next(
        (entry.reason for entry in migration_entries(path) if entry.project == project), None
    )


def exempt_kind(project: str, nodes: set[str]) -> str | None:
    """The exempt plan kind ``project`` is, holding exactly ``nodes``, or ``None``.

    The kinds and the stamp are `orchestrator/design_approval.py`'s, and so is the bound: a
    project holding any node beyond the launch its stamp describes, or missing one, is a
    plan like any other.
    """
    stamped = design_approval.stamped_launch(project)
    if stamped is None or set(stamped.nodes) != nodes:
        return None
    return stamped.kind


def refusals(
    project: str, plan: object, tasks: Sequence[TaskBudgets] | None = None
) -> list[Refusal]:
    """Everything `just check-plan` refuses about ``project``'s budgets, read against ``plan``.

    ``tasks`` is every task with its budgets record, read from ``plan``'s own metadata maps
    when not given — the document the engine's `plan check` hands the registered check
    carries them, and the path reading the store hands its records instead. Every task's
    `## Budgets` section is held to its record whatever the plan; the plan-level answers are
    required of every plan but one the migration list names or an exempt launch's project.
    """
    tasks = tasks_of_plan(plan) if tasks is None else tasks
    found: list[Refusal] = []
    try:
        for task in tasks:
            found.extend(section_refusals(task))
        if exempt_kind(project, _plan_nodes(plan)) is not None or migrated(project) is not None:
            return found
        held = plan_store.project_record(project)
    except (OSError, ValueError) as exc:
        return [*found, Refusal(None, "budgets", f"{project}'s budgets could not be read: {exc}")]
    metadata = held.get("metadata")
    record = metadata.get(PLAN_RECORD) if isinstance(metadata, Mapping) else None
    if record is None:
        return [
            *found,
            Refusal(
                None,
                "budgets",
                f"{project} carries no `{PLAN_RECORD}` metadata holding its plan-level budget "
                f"answers, and every plan states them there and renders them as its "
                f"description from the `{DESCRIPTION_TEMPLATE}` template (`docs/budgets.md`); "
                f"a plan that predates the requirement is one `config/budgets-migration.yaml` "
                f"names",
            ),
        ]
    try:
        answers = parse_plan(record)
    except BudgetsError as exc:
        return [
            *found,
            Refusal(None, "budgets", f"{project}'s plan-level answers are malformed: {exc}"),
        ]
    found.extend(plan_rule_refusals(answers, owned_budgets(tasks), plan))
    try:
        found.extend(description_refusals(project, held))
    except OSError as exc:
        found.append(
            Refusal(None, "description", f"{project}'s description could not be rendered: {exc}")
        )
    return found


def writer_answers(project: str) -> WriterAnswers:
    """The design document's budget answers for ``project``: what its writer copies.

    The migration list's reason when it names the plan; otherwise the plan's
    :data:`PLAN_RECORD`, and every budget of every task, each with its owning node id and the
    location the plan store reports for that task, sorted by node. Raises
    :class:`BudgetsError` for a plan with no plan-level record, which `just check-plan` has
    refused before any writer is launched.
    """
    reason = migrated(project)
    if reason is not None:
        return WriterAnswers(predates_budgets=reason, plan_budgets={}, budgets=[])
    record = plan_store.project_record(project).get("metadata")
    held = record.get(PLAN_RECORD) if isinstance(record, Mapping) else None
    if held is None:
        raise BudgetsError(
            f"{project} carries no `{PLAN_RECORD}` metadata and `config/budgets-migration.yaml` "
            f"does not name it, so there is nothing for its design document's budget summary "
            f"to restate; `just check-plan {project}` refuses it for the same reason"
        )
    parse_plan(held)
    budgets: list[dict[str, object]] = []
    for task in sorted(plan_store.read_tasks(project), key=lambda task: task.node_id):
        record = task_record(task.metadata)
        if record is None:
            continue
        location = task.location or str(task.qualified_id)
        budgets.extend(
            {"node": task.node_id, "location": location, **dataclasses.asdict(budget)}
            for budget in parse_budgets(record)
        )
    return WriterAnswers(predates_budgets="", plan_budgets=held, budgets=budgets)


#: The design document's three budget answers, whose values :func:`writer_answers` states.
DESIGN_ANSWERS = tuple(WriterAnswers.__annotations__)


def design_refusals(project: str) -> tuple[str, list[str]]:
    """``project``'s design document, and every budget answer of it that differs from the records.

    The design document is the one `orchestrator/design_approval.py` reads, the project's
    one document recording the `design-doc` template. Its answers are read from where the
    store keeps them, where the document was drafted, and compared with
    :func:`writer_answers` answer for answer, so a changed target, an omitted budget or a
    wrong owner is named before anything is copied.
    """
    stated = writer_answers(project)
    document = str(design_approval.design_document(project).qualified_id)
    answered = plan_store.sdk(plan_store.client().document_answers(document)).model_dump()
    design_approval.validate_content(answered)
    found = [
        f"its `{name}` answer is not what `python -m orchestrator.plan_budgets {project}` "
        f"prints for it"
        for name, expected in (
            ("predates_budgets", stated["predates_budgets"]),
            ("plan_budgets", stated["plan_budgets"]),
        )
        if answered.get(name) != expected
    ]
    # A rendering of the design-doc chain in force always holds the list, `[]` by default, so
    # a document holding none was rendered under another chain and is refused as such.
    held = answered.get("budgets")
    if not isinstance(held, list):
        found.append(
            f"it holds no `budgets` list, so it was rendered from a design-doc template that "
            f"takes none; regenerate it with the answers `python -m orchestrator.plan_budgets "
            f"{project}` prints"
        )
    elif held != stated["budgets"]:
        found.extend(_budget_differences(held, stated["budgets"]))
    return document, found


def _budget_differences(held: list[object], expected: list[dict[str, object]]) -> list[str]:
    """What separates the document's `budgets` answer from the records, budget by budget."""
    by_id = {str(entry.get("id")): entry for entry in held if isinstance(entry, dict)}
    found: list[str] = []
    for entry in expected:
        named = str(entry["id"])
        written = by_id.pop(named, None)
        if written is None:
            found.append(f"it omits the budget {named!r}, which `{entry['node']}` owns")
            continue
        for key, value in entry.items():
            if written.get(key) != value:
                found.append(
                    f"its budget {named!r} states the `{key}` {written.get(key)!r}, where the "
                    f"records state {value!r}"
                )
        for key in sorted(set(written) - set(entry)):
            found.append(f"its budget {named!r} carries the key `{key}`, which no record states")
    for named in sorted(by_id):
        found.append(f"it states the budget {named!r}, which no task of the plan owns")
    if not found:
        found.append("its `budgets` answer lists the plan's budgets in another order")
    return found


def main(argv: Sequence[str] | None = None) -> int:
    """Print a plan's design-document budget answers, or hold a written document to them.

    For `scripts/finish-plan.sh`: with no flag, one JSON object, :func:`writer_answers`'s
    answer, at exit 0; with `--check-design-document`, nothing at exit 0 when the plan's
    design document's budget answers are the records', and each difference on stderr at
    exit 1. Exit 2 when the plan's budgets or its design document cannot be read.
    """
    parser = argparse.ArgumentParser(
        prog="plan-budgets",
        description="Print, or check, what a plan's design document restates about its budgets.",
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    parser.add_argument("--check-design-document", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.check_design_document:
            print(json.dumps(writer_answers(args.project), ensure_ascii=False))
            return 0
        document, differences = design_refusals(args.project)
    except (OSError, ValueError) as exc:
        print(f"plan-budgets: {exc}", file=sys.stderr)
        return 2
    for difference in differences:
        print(f"plan-budgets: {document}: {difference}", file=sys.stderr)
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
