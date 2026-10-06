"""A plan's budgets document: where it is, what it answers, and what `just check-plan` refuses.

Every plan states its measurable requirements — its **budgets** — in one project document,
`<project>-budgets`, rendered from this host's `plan-budgets` template
(`templates/plan-budgets.md.j2`). `docs/budgets.md` states the principles and the
convention; this module is the one reader of that document, for the three things that read
it: `just check-plan` (:func:`refusals`), `just review-plan` (:func:`read` and
:func:`owned`), and the design-document writer, whose task `scripts/finish-plan.sh` points
at it.

**The answers are read from the rendering itself, never from the store's stored answers.**
The store keeps a document's answers only where it was drafted and never copies them, so a
board copy holds none — while the rendered body travels with every copy and its digest is
recorded beside it. So the template closes on a `## Record` section carrying every answer
as one line of JSON, and :func:`read` takes the answers from there, once it has held the
body to being the rendering its provenance records: a body edited by hand is refused
rather than read.

**Which plans predate the requirement is one tracked list**, `config/budgets-migration.yaml`,
and nothing else: adding to it is a reviewed change to this repository, so a plan cannot
exempt itself. A project it names is exempt from every refusal about its budgets document
and held to everything else. The plan kinds `orchestrator/design_approval.py` exempts from
design approval are exempt here on the same bound: a project holding exactly the nodes its
exempt launch dispatches.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import ClassVar, NamedTuple, NewType, Protocol, TypedDict

import yaml

from orchestrator import design_approval, plan_store
from orchestrator.plan_store import QualifiedProjectId, StoreDocument
from orchestrator.project_store import hosted_origin
from orchestrator.root import REPO_ROOT

#: The name this host registers the budgets document's template under, the reference a
#: rendering of it records, and what the document is called — the plan's native id with
#: the suffix appended. `orchestrator/design_approval.py` states them, because it is what
#: tells a plan's design document apart from this one.
TEMPLATE_NAME = design_approval.BUDGETS_TEMPLATE
TEMPLATE_REFERENCE = design_approval.BUDGETS_REFERENCE
DOCUMENT_SUFFIX = design_approval.BUDGETS_SUFFIX

#: The template's seven variables, in the order it renders them. The design-document
#: template restates them, and `tests/test_design_doc_template.py` fails the moment the two
#: sets of keys part.
VARIABLES = (
    "workload",
    "checklist",
    "ten_x",
    "budgets",
    "repo_wide_effects",
    "realistic_data",
    "spike_findings",
)


class Direction(StrEnum):
    """Which side of its threshold a budget holds a measurement to.

    This repository's copy of onebudgetspec's `direction` vocabulary, held to the installed
    library's `onebudgetspec schema` by `tests/test_budgets_files.py`; the templates' and
    `docs/budgets.md`'s copies are held to this one by `tests/test_plan_budgets_template.py`.
    """

    MAX = "max"
    MIN = "min"


class FileChange(StrEnum):
    """What a plan does to the budgets file a budget goes in.

    This host's planning convention, stated once here; the templates' descriptions of it are
    held to this by `tests/test_plan_budgets_template.py`.
    """

    ADD = "add"
    CHANGE = "change"
    NONE = "none"


#: The one file name a budgets file has: onebudgetspec's convention, held to the installed
#: library's `onebudgetspec schema` by `tests/test_budgets_files.py`, as :class:`Direction`
#: is, with the templates' and `docs/budgets.md`'s copies held to this one.
BUDGETS_FILE = "budgets.yaml"


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

#: The closing record a rendering carries: one line of JSON in the last fenced block.
RECORD = re.compile(r"\n## Record\n\n```json\n(?P<record>[^\n]*)\n```\n*\Z")


#: A budget's id in its budgets file, and every reference to one: a checklist entry's
#: `budget`, a repo-wide effect's `budget`.
BudgetId = NewType("BudgetId", str)


class BudgetsError(OSError):
    """A budgets document that is there but cannot be read as one."""


@dataclass(frozen=True)
class ChecklistEntry:
    """One concern the plan was checked against, answered with a budget or a reason."""

    concern: str
    budget: BudgetId
    not_applicable: str


@dataclass(frozen=True)
class Budget:
    """One budget the plan proposes, as the budgets document states it."""

    id: BudgetId
    repository: str
    file: str
    measure: str
    inner_measure_reason: str
    unit: str
    # llmlint: ignore-block[modern_domain_modeling] `direction` and `file_change` stay the text the document states, rather than a `Direction` or a `FileChange` refused at the read, so that a value outside either vocabulary is refused by the plan check's own named rule against the plan — the refusal `just check-plan` owes an author — instead of as an unreadable document.  # noqa: E501
    direction: str
    threshold: int | float
    workload: str
    evidence: str
    command: str
    node: str
    file_change: str
    # llmlint: ignore-end[modern_domain_modeling]


@dataclass(frozen=True)
class RepoWideEffect:
    """The plan's expected effect on one repository's repo-wide budgets."""

    repository: str
    budget: BudgetId
    effect: str


@dataclass(frozen=True)
class RealisticData:
    """How the plan makes one kind of data realistic, and why."""

    data: str
    choice: DataChoice
    reason: str
    artifact: str


@dataclass(frozen=True)
class SpikeFinding:
    """One spike finding that changed the plan."""

    spike: str
    finding: str
    changed: str


@dataclass(frozen=True)
class BudgetAnswers:
    """The seven answers a budgets document renders, each held to its declared shape."""

    workload: str
    checklist: tuple[ChecklistEntry, ...]
    ten_x: str
    budgets: tuple[Budget, ...]
    repo_wide_effects: tuple[RepoWideEffect, ...]
    realistic_data: tuple[RealisticData, ...]
    spike_findings: tuple[SpikeFinding, ...]

    def as_record(self) -> dict[str, object]:
        """The answers as the template's record states them, the shape a review key covers."""
        return {name: _plain(getattr(self, name)) for name in VARIABLES}


def _plain(value: object) -> object:
    """``value`` with every entry model turned back into the object the record holds."""
    if isinstance(value, tuple):
        return [
            dataclasses.asdict(entry)
            for entry in value
            if dataclasses.is_dataclass(entry) and not isinstance(entry, type)
        ]
    return value


class Budgets(NamedTuple):
    """One plan's budgets document: which document it is, and the answers it renders."""

    document: plan_store.QualifiedDocumentId
    answers: BudgetAnswers


class Refusal(NamedTuple):
    """One rule a plan's budgets document breaks: the answer it is about, and why."""

    field: str
    reason: str


class MigrationEntry(NamedTuple):
    """One plan that predates the budgets requirement, and why it is exempt."""

    project: QualifiedProjectId
    reason: str


class WriterContext(TypedDict):
    """What the design-document writer's task states about one plan's budgets."""

    #: The migration list's reason when it names the plan, and `""` otherwise.
    predates: str
    #: The plan's budgets document, or `""` for a plan that predates budgets.
    document: str
    #: That document's answers as its record states them, or `None` with no document.
    answers: dict[str, object] | None


document_id = design_approval.budgets_document_id
is_budgets_document = design_approval.is_budgets_document


def _provenance(document: StoreDocument) -> Mapping[str, object]:
    held = document.metadata.get(design_approval.PROVENANCE)
    return held if isinstance(held, Mapping) else {}


class _Kind(NamedTuple):
    """What one declared field type admits, how a refusal names it, and what it reads as."""

    holds: Callable[[object], bool]
    said: str
    read: Callable[[object], object]


#: The field types an entry model declares, by the annotation it is declared under.
_KINDS = {
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


def _entries[E: _EntryModel](
    record: Mapping[str, object], field: str, model: type[E]
) -> tuple[E, ...]:
    """The answer ``field`` as entries of ``model``, or :class:`BudgetsError` naming the fault.

    Each entry is an object holding exactly the model's keys, each a string — a threshold a
    number, a data choice one of :class:`DataChoice` — so an answer of any other shape is
    refused naming it rather than read as less than it says.
    """
    held = record.get(field)
    if not isinstance(held, list):
        raise BudgetsError(f"the `{field}` answer is not a list of objects")
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


def parse(record: object) -> BudgetAnswers:
    """``record`` as the seven answers, or :class:`BudgetsError` naming what is malformed."""
    if not isinstance(record, dict) or set(record) != set(VARIABLES):
        raise BudgetsError(f"the record does not hold exactly the {len(VARIABLES)} answers")
    for text in ("workload", "ten_x"):
        if not isinstance(record[text], str):
            raise BudgetsError(f"the `{text}` answer is not text")
    return BudgetAnswers(
        workload=record["workload"],
        checklist=_entries(record, "checklist", ChecklistEntry),
        ten_x=record["ten_x"],
        budgets=_entries(record, "budgets", Budget),
        repo_wide_effects=_entries(record, "repo_wide_effects", RepoWideEffect),
        realistic_data=_entries(record, "realistic_data", RealisticData),
        spike_findings=_entries(record, "spike_findings", SpikeFinding),
    )


def answers_of(document: StoreDocument) -> BudgetAnswers:
    """The answers ``document`` renders, once it is a rendering of `plan-budgets` as it stands.

    Refused as :class:`BudgetsError` when it records no rendering of that template, when its
    content is not the body its provenance records — edited after it was rendered — or when
    its record does not hold the seven answers in their declared shapes.
    """
    provenance = _provenance(document)
    if provenance.get("template") != TEMPLATE_REFERENCE:
        raise BudgetsError(
            f"{document.qualified_id} records no rendering of the `{TEMPLATE_NAME}` template, "
            f"so its answers are not ones the template stated; create it with `onepipeline "
            f"template resolve {TEMPLATE_NAME} --json | onetaskgraph document create ... "
            f"--template-loader - --answers <file>`"
        )
    held = "sha256:" + hashlib.sha256(document.content.encode("utf-8")).hexdigest()
    if provenance.get("body_digest") != held:
        raise BudgetsError(
            f"{document.qualified_id}'s content is not the rendering its provenance records, "
            f"so it was edited after it was rendered; change its answers and regenerate it "
            f"with `onetaskgraph document render`"
        )
    found = RECORD.search(document.content)
    try:
        # llmlint: ignore[boundary_inputs_validated] Decoded here and held to its shape whole by `parse` on the next line, which refuses every answer that is not the template's declared shape.  # noqa: E501
        return parse(json.loads(found.group("record")) if found else None)
    except (ValueError, BudgetsError) as exc:
        raise BudgetsError(
            f"{document.qualified_id} carries no `## Record` holding the {len(VARIABLES)} "
            f"answers of the `{TEMPLATE_NAME}` template in their declared shapes ({exc}), so "
            f"it was rendered from a version of that template that is no longer in force; "
            f"regenerate it with `onetaskgraph document render`"
        ) from exc


def find(project: str, documents: Sequence[StoreDocument]) -> StoreDocument | None:
    """The one of ``documents`` that is ``project``'s budgets document, or ``None``."""
    held = [document for document in documents if is_budgets_document(project, document)]
    if len(held) > 1:
        named = ", ".join(sorted(str(document.qualified_id) for document in held))
        raise BudgetsError(
            f"{project} holds {len(held)} budgets documents ({named}), so which of them states "
            f"its budgets cannot be decided; leave it the one, `{document_id(project)}`"
        )
    return held[0] if held else None


def read(project: str) -> Budgets | None:
    """``project``'s budgets document and its answers, or ``None`` when it holds none."""
    document = find(project, plan_store.read_documents(project))
    if document is None:
        return None
    return Budgets(document.qualified_id, answers_of(document))


def readable(project: str | None) -> Budgets | None:
    """:func:`read`, answered as ``None`` for no project and for a document it cannot read.

    For the review keys, which cover the answers when there are any. An unreadable document
    is keyed as no document rather than raised here, because it is :func:`refusals`' to
    refuse, by name, and a plan carrying one is refused whatever its review records say.
    """
    if project is None:
        return None
    try:
        return read(project)
    except OSError:
        return None


def owned(budgets: Budgets | None) -> dict[str, list[Budget]]:
    """Every budget of ``budgets``, grouped by the id of the node that owns its command."""
    grouped: dict[str, list[Budget]] = {}
    for budget in budgets.answers.budgets if budgets else ():
        grouped.setdefault(budget.node.strip(), []).append(budget)
    return grouped


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


def _is_budgets_file(path: str) -> bool:
    if not path or "\\" in path:
        return False
    held = PurePosixPath(path)
    return (
        not held.is_absolute()
        and held.name == BUDGETS_FILE
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def rule_refusals(answers: BudgetAnswers, plan: object) -> list[Refusal]:
    """Every rule of a plan's budgets document ``answers`` breaks, read against ``plan``."""
    found: list[Refusal] = []
    ids = [budget.id.strip() for budget in answers.budgets]
    for position, entry in enumerate(answers.checklist, start=1):
        concern = entry.concern.strip() or f"entry {position}"
        covered, not_applicable = entry.budget.strip(), entry.not_applicable.strip()
        if bool(covered) == bool(not_applicable):
            found.append(
                Refusal(
                    "checklist",
                    f"checklist entry {position} ({concern}) answers "
                    f"{'both' if covered else 'neither'} of `budget` and `not_applicable`; "
                    f"each concern gets a budget or a one-line n/a, exactly one of the two",
                )
            )
        elif covered and covered not in ids:
            found.append(
                Refusal(
                    "checklist",
                    f"checklist entry {position} ({concern}) names the budget {covered!r}, "
                    f"which no entry of `budgets` has as its `id`",
                )
            )
    repeated = sorted({one for one in ids if ids.count(one) > 1})
    if repeated:
        found.append(
            Refusal("budgets", f"the budget id(s) {', '.join(map(repr, repeated))} repeat")
        )
    nodes = _plan_nodes(plan)
    for position, budget in enumerate(answers.budgets, start=1):
        named = budget.id.strip() or f"budget {position}"
        if not budget.id.strip():
            found.append(
                Refusal("budgets", f"{named} has no `id`, and every budget is named by one")
            )
        if budget.node.strip() not in nodes:
            found.append(
                Refusal(
                    "budgets",
                    f"budget {named!r} is owned by the node {budget.node.strip()!r}, which is no "
                    f"node of the plan; the node that implements a budget owns its command",
                )
            )
        if budget.file_change.strip() not in tuple(FileChange):
            found.append(
                Refusal(
                    "budgets",
                    f"budget {named!r} has the `file_change` {budget.file_change.strip()!r}, "
                    f"which is not one of {', '.join(FileChange)}",
                )
            )
        if not _is_budgets_file(budget.file):
            found.append(
                Refusal(
                    "budgets",
                    f"budget {named!r} goes in the file {budget.file!r}, which is not a relative "
                    f"path whose last component is `{BUDGETS_FILE}`",
                )
            )
        if budget.direction.strip() not in tuple(Direction):
            found.append(
                Refusal(
                    "budgets",
                    f"budget {named!r} has the `direction` {budget.direction.strip()!r}, which "
                    f"is not {' or '.join(Direction)}",
                )
            )
        if not budget.command.strip():
            found.append(
                Refusal(
                    "budgets",
                    f"budget {named!r} names no `command`, and the command is what performs "
                    f"the measurement",
                )
            )
    # An entry stating no effect states nothing, so it covers no repository.
    stated = {
        _origin(entry.repository.strip())
        for entry in answers.repo_wide_effects
        if entry.effect.strip()
    }
    for repository in sorted(_plan_repositories(plan) - stated):
        found.append(
            Refusal(
                "repo_wide_effects",
                f"`repo_wide_effects` states no effect for {repository}, a repository the "
                f"plan's tasks change; each one gets an entry, with the effect `none` where it "
                f"has none",
            )
        )
    return found


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


def refusals(project: str, plan: object) -> list[Refusal]:
    """Everything `just check-plan` refuses about ``project``'s budgets, read against ``plan``."""
    try:
        if exempt_kind(project, _plan_nodes(plan)) is not None or migrated(project) is not None:
            return []
        budgets = read(project)
    except (OSError, ValueError) as exc:
        return [Refusal("budgets", f"{project}'s budgets could not be read: {exc}")]
    if budgets is None:
        return [
            Refusal(
                "budgets",
                f"{project} carries no `{document_id(project)}` document rendered from the "
                f"`{TEMPLATE_NAME}` template, and every plan states its budgets there "
                f"(`docs/budgets.md`); a plan that predates the requirement is one "
                f"`config/budgets-migration.yaml` names",
            )
        ]
    return [
        Refusal(refused.field, f"{budgets.document}: {refused.reason}")
        for refused in rule_refusals(budgets.answers, plan)
    ]


def context(project: str) -> WriterContext:
    """What the design-document writer's task states about ``project``'s budgets.

    The migration list's reason when it names the plan, and otherwise the plan's budgets
    document and the answers it renders, which the design document's budget answers
    restate. Raises :class:`BudgetsError` for a plan with neither, which `just check-plan`
    has refused before any writer is launched.
    """
    reason = migrated(project)
    if reason is not None:
        return WriterContext(predates=reason, document="", answers=None)
    budgets = read(project)
    if budgets is None:
        raise BudgetsError(
            f"{project} carries no `{document_id(project)}` document and "
            f"`config/budgets-migration.yaml` does not name it, so there is nothing for its "
            f"design document's budget sections to restate; `just check-plan {project}` "
            f"refuses it for the same reason"
        )
    return WriterContext(
        predates="", document=str(budgets.document), answers=budgets.answers.as_record()
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Print what the design-document writer's task states about one plan's budgets.

    For `scripts/finish-plan.sh`: one JSON object, :func:`context`'s answer, at exit 0, and
    a diagnostic at exit 2 when the plan has neither a budgets document nor an entry in the
    migration list, or either cannot be read.
    """
    parser = argparse.ArgumentParser(
        prog="plan-budgets",
        description="Print what a plan's design document restates about its budgets.",
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    args = parser.parse_args(argv)
    try:
        stated = context(args.project)
    except (OSError, ValueError) as exc:
        print(f"plan-budgets: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(stated, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
