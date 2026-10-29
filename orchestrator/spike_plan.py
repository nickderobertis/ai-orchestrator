"""The spike convention of the planning flow, and what `just check-plan` refuses about it.

A plan whose budgets cannot be known without measuring, or whose approach is uncertain, is
drafted with **spikes**: dispatched nodes that measure real systems before the plan is
finalized. The convention is this module's to state, because three readers hold to it —
`scripts/plan.sh`, which launches the spikes; :mod:`orchestrator.plan_check`, which refuses
a plan breaking it; and `scripts/run-ended.sh`, which discards the spikes' branches once the
plan's main run succeeds:

* a plan's spikes live in their own project, ``<plan project>-spikes``, stamped with the
  ``spikes`` plan kind :mod:`orchestrator.design_approval` exempts from design approval for
  exactly the nodes the stamp names;
* every node there has an id ``spike-<topic>``, is a lifecycle node in the repository it
  measures, and declares ``onepipeline.publish: "preserve"``, so it settles ``done`` as
  ``preserved`` with its branch kept on its origin; and no node of any other project has a
  ``spike-`` id, because that prefix is what lists a spike branch from any machine;
* the spikes are launched with a branch template naming the plan, so each branch reads
  ``<host prefix>/<plan native id>/spike-<topic>`` — or ``…/spike-<topic>-<n>``, ``n`` of 2
  or more, where the engine retried the spike as the node ``spike-<topic>-<n>`` and that
  retry cut a branch of its own; such a branch is refused as ambiguous where the plan also
  has a spike named ``spike-<topic>-<n>``;
* each spike writes its report as the document ``<plan native id>-<spike id>-report`` of
  the **plan's** project (:func:`report_id`), rendered from the ``spike-report`` template —
  the plan's own native id in front because a flat document source such as ``authoring``
  keeps every project's documents in one directory, where two plans spiking one topic would
  otherwise write one document. The native id is the ``authoring`` project's, resolved for a
  copy through the store's own copy link (:func:`plan_native`). A report written before ids
  named their plan, under the spike's id alone (:func:`legacy_report_id`), is read only
  where the plan holds no report under the qualified id. On a copy of the plan, whose
  destination mints the document an id of its own, the report is the ``spike-report``
  document whose ``onetaskgraph.origin`` — the plan store's own record of what a copy was
  copied from — names that id, or, for a copy out of ``authoring`` or another default source
  declared private (:func:`drafting_sources`), on which the store records no origin, the
  document that source's ``onetaskgraph.copies`` link names. The store has no document lookup
  by origin, so the check reads the plan project's own documents, one project-scoped read, and
  for such a copy the drafted project's too;
* a spike may depend on other spikes — a shared harness spike the area spikes build on —
  and the engine starts it from the branch its base dependency kept, so same-repository
  spike dependencies lie on one stacking chain; the engine's ``plan check`` refuses a fan-in
  no chain resolves, a dependency on another repository's spike orders only, and those
  edges are the spikes project's own, which no report copies;
* a task building on spikes links each through the ``plan-task`` template's ``spikes``
  answer — ``{spike, report, branch}`` — which renders as a ``## Spike evidence`` section:
  each spike it builds on and every spike above it in that spike's stacking chain, never a
  dependency edge on a spike.
* a plan is **visual** — it changes what a user sees — exactly when its spikes project holds
  the initial node :data:`VISUAL_SPIKE`, whose canonical report :func:`visual_report_id`
  answers ``visual_changes`` with each expected change's paired or single images as
  assets of the report; the design document restates that answer, `scripts/finish-plan.sh`
  quotes it to the document's writer, and :mod:`orchestrator.design_approval` refuses to
  approve a document that differs from its current evidence, selected through
  :func:`current_visual_report`. A revision may select a separately named complete report
  or update the existing report in place. It is otherwise an ordinary
  spike.

That section is what the check reads, rather than the store's stored answers: a store keeps
a task's answers only where it was drafted and never copies them, while the rendered body
travels with every copy, and the engine's ``require_rendered`` holds that body to being the
template's rendering. :data:`EVIDENCE_ENTRY` is the one reading of the line the template
renders, and `tests/plan_tooling/check_and_review/test_check_plan_spikes_e2e.py` holds the
two together by checking bodies the pinned store rendered from that template.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import NamedTuple, NewType

from orchestrator import design_approval, plan_store
from orchestrator.plan_store import NodeId
from orchestrator.project_store import qualified_id

#: A spike's preserved branch, as `onevcs` names it with the host's prefix in front, and the
#: id of a spike's report document, as the plan's project holds it.
Branch = NewType("Branch", str)
ReportId = NewType("ReportId", str)

#: What every spike node's id starts with, and so what every spike branch's last segment does.
SPIKE_PREFIX = "spike-"

#: What a plan's spikes project is called: the plan project's own id with this appended.
SPIKES_SUFFIX = "-spikes"

#: What a spike's report document ends with, after the plan's native id and the spike's id.
REPORT_SUFFIX = "-report"

#: The reserved spike a plan that changes what a user sees authors; its canonical report is
#: :func:`visual_report_id`, the one statement every reader of a visual plan takes it from.
VISUAL_SPIKE = NodeId(f"{SPIKE_PREFIX}visual")

#: The one `publish` value a spike node declares, and the metadata key it is stored under.
PRESERVE = "preserve"
PUBLISH_KEY = "onepipeline.publish"

#: The heading the `plan-task` template renders a task's `spikes` answer under, and the line
#: it renders per spike. Anchored on the template's own words, so a body that does not carry
#: them links no spike rather than one this misreads.
EVIDENCE_HEADING = "## Spike evidence"
EVIDENCE_ENTRY = re.compile(
    r"^- `(?P<spike>[^`\n]+)`: report `(?P<report>[^`\n]+)`, a document of this plan's own "
    r"project; branch `(?P<branch>[^`\n]+)`$",
    re.MULTILINE,
)


class Refused(NamedTuple):
    """One refusal, in the fields `onepipeline plan check` renders."""

    node: NodeId | None
    field: str | None
    reason: str


class Evidence(NamedTuple):
    """One spike a task links through its `spikes` answer."""

    spike: NodeId
    report: ReportId
    branch: Branch


def spikes_project(plan_project: str) -> str:
    """The qualified id of the project ``plan_project``'s spikes live in."""
    return f"{plan_project}{SPIKES_SUFFIX}"


def report_id(plan: str, spike: NodeId) -> ReportId:
    """The document id ``spike``'s report is stored under in the project of the plan ``plan``.

    ``plan`` is the plan project's own native id (:func:`plan_native`), never its spikes
    project's: the one definition of the shape, which every statement of it is held to.
    """
    return ReportId(f"{plan}-{spike}{REPORT_SUFFIX}")


def legacy_report_id(spike: NodeId) -> ReportId:
    """The id ``spike``'s report was written under before report ids named their plan."""
    return ReportId(f"{spike}{REPORT_SUFFIX}")


#: The shape :func:`report_id` gives, in the placeholders the prose states it in.
REPORT_SHAPE = report_id("<plan native id>", NodeId("<spike id>"))


def visual_report_id(plan: str) -> ReportId:
    """The canonical report of the plan ``plan``'s :data:`VISUAL_SPIKE`."""
    return report_id(plan, VISUAL_SPIKE)


def plan_native(project: str, lookup: DraftedPlan | None = None) -> str:
    """The native id of the plan ``project`` was drafted as: its drafted project's.

    A copy's own native id is minted by its destination, so the drafted project whose
    `onetaskgraph.copies` link names ``project`` answers instead; a project no drafted one
    links, or a source that cannot be read, answers with its own native id. ``lookup`` is
    the invocation's own :class:`DraftedPlan` of ``project``, when the caller holds one.
    """
    native = project.partition(":")[2]
    drafted = (DraftedPlan(project) if lookup is None else lookup).project()
    return native if drafted is None else str(drafted.qualified_id).partition(":")[2]


def held_report_id(
    plan: str,
    spike: NodeId,
    documents: Sequence[plan_store.StoreDocument],
    drafted: Drafted | None = None,
) -> ReportId:
    """The id ``spike``'s report goes by among ``documents``: :func:`report_id`'s, or the old one.

    The qualified id is ``plan``'s, or — on a copy whose ``plan`` is no drafted plan's, since
    the authoring side could not say which — the one a report's provenance names for
    ``spike`` (:func:`provenance_plans`). The old :func:`legacy_report_id` answers only where
    no spike report goes by a qualified id and one goes by the old, so a plan whose spikes
    reported before ids named their plan still reads its own reports.
    """
    reports = [document for document in documents if design_approval.is_spike_report(document)]
    held = {name for document in reports for name in names(document, drafted)}
    for one in (plan, *sorted(provenance_plans(reports, (spike,), drafted) - {plan})):
        if report_id(one, spike) in held:
            return report_id(one, spike)
    legacy = legacy_report_id(spike)
    return legacy if legacy in held else report_id(plan, spike)


def provenance_plans(
    documents: Sequence[plan_store.StoreDocument],
    spikes: Iterable[NodeId],
    drafted: Drafted | None = None,
) -> frozenset[str]:
    """Each plan id a document's provenance names as the plan of one of ``spikes``' reports.

    A copy's report records the id it was copied from — its `onetaskgraph.origin`, or the
    private source's own link (:func:`names`) — which is
    ``<plan native id>-<spike id>-report`` for the drafted plan, so the plan is read off it
    without reading the authoring source. Only that
    provenance is read, never a document's own id, which on the drafted plan must be
    :func:`report_id`'s exactly.
    """
    suffixes = [f"-{spike}{REPORT_SUFFIX}" for spike in spikes]
    return frozenset(
        name.removesuffix(suffix)
        for document in documents
        for name in _provenance(document, drafted)
        for suffix in suffixes
        if name.endswith(suffix) and len(name) > len(suffix)
    )


def report_spike(plan: str, name: str) -> NodeId | None:
    """The spike a report named ``name`` in the plan ``plan``'s project is of, if any."""
    if not name.endswith(REPORT_SUFFIX):
        return None
    stem = name.removesuffix(REPORT_SUFFIX)
    spike = stem.removeprefix(f"{plan}-")
    return NodeId(spike) if is_spike(spike) else None


def is_spike(node: str) -> bool:
    """Whether ``node`` is named as a spike."""
    return node.startswith(SPIKE_PREFIX) and len(node) > len(SPIKE_PREFIX)


def _evidence_section(body: str) -> str:
    _, heading, rest = body.partition(f"\n{EVIDENCE_HEADING}\n")
    return rest.split("\n## ", 1)[0] if heading else ""


def evidence(body: str) -> list[Evidence]:
    """The spikes ``body``'s `## Spike evidence` section links, in the order it lists them."""
    return [
        Evidence(NodeId(match["spike"]), ReportId(match["report"]), Branch(match["branch"]))
        for match in EVIDENCE_ENTRY.finditer(_evidence_section(body))
    ]


def malformed_evidence(body: str) -> list[str]:
    """Each entry of ``body``'s `## Spike evidence` section not in the template's words.

    Every entry the template renders is one line opening `- `, so a line opening that way
    and not reading as one links a spike this check would otherwise never see.
    """
    return [
        line
        for line in _evidence_section(body).splitlines()
        if line.startswith("- ") and EVIDENCE_ENTRY.fullmatch(line) is None
    ]


def _tasks(document: object) -> Iterator[Mapping[str, object]]:
    tasks = document.get("tasks") if isinstance(document, dict) else None
    for task in tasks if isinstance(tasks, list) else []:
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            yield task


def _publish(task: Mapping[str, object]) -> object:
    metadata = task.get("metadata")
    if "publish" in task:
        return task["publish"]
    return metadata.get(PUBLISH_KEY) if isinstance(metadata, dict) else None


def _is_lifecycle(task: Mapping[str, object]) -> bool:
    repo = task.get("repo")
    return isinstance(repo, str) and bool(repo) and task.get("kind") != "human"


def _spike_node_refusals(task: Mapping[str, object], stamped: bool) -> Iterator[Refused]:
    node = NodeId(str(task["id"]))
    if not stamped:
        if node.startswith(SPIKE_PREFIX):
            yield Refused(
                node,
                "id",
                f"`{node}` is named as a spike, and a `{SPIKE_PREFIX}` id belongs only in a "
                f"project stamped `{design_approval.SPIKES}` — a plan's `<plan>{SPIKES_SUFFIX}` "
                f"project — because that prefix is what lists a spike branch from any machine; "
                f"rename the node, or move the spike into its plan's spikes project",
            )
        return
    if not is_spike(node):
        yield Refused(
            node,
            "id",
            f"`{node}` sits in a project stamped `{design_approval.SPIKES}`, where every node "
            f"is a spike and its id is `{SPIKE_PREFIX}<topic>`, so its branch is listed by "
            f"`git ls-remote --heads origin '*/{SPIKE_PREFIX}*'`; rename it "
            f"`{SPIKE_PREFIX}<topic>`",
        )
    if not _is_lifecycle(task):
        yield Refused(
            node,
            "repo",
            f"`{node}` is a spike, and a spike is a lifecycle node in the repository it "
            f"measures, so its harness is committed on a branch the plan's workers can reach; "
            f"give it that repository",
        )
    if _publish(task) != PRESERVE:
        yield Refused(
            node,
            "metadata",
            f'`{node}` is a spike, and a spike declares `{PUBLISH_KEY}: "{PRESERVE}"`, so its '
            f"branch is kept on its origin rather than landed; set it",
        )


#: The suffix the engine gives a retry of a node: `-2`, `-3`, … after the node's own id.
RETRY_SUFFIX = re.compile(r"-([2-9]|[1-9][0-9]+)\Z")


def branch_refusal(node: NodeId, linked: Evidence, spikes: frozenset[NodeId]) -> Refused | None:
    """Why ``linked``'s branch is not one its spike kept, or ``None`` when it is.

    A spike's branch ends `/<spike>`, or `/<spike>-<n>` for `n` of 2 or more where a retry of
    it cut a branch of its own: the engine retries `spike-x` as the node `spike-x-2`, which
    names its branch for itself. That second form is ambiguous where the plan also has a
    spike named `spike-x-2`, and is refused there, naming both, rather than guessed at.
    """
    spike, branch = linked.spike, linked.branch
    last = branch.rpartition("/")[2] if "/" in branch else ""
    if is_spike(spike) and last == spike:
        return None
    retry = RETRY_SUFFIX.search(last) if last.startswith(f"{spike}-") else None
    if is_spike(spike) and retry is not None and last[: retry.start()] == spike:
        if NodeId(last) not in spikes:
            return None
        return Refused(
            node,
            "task",
            f"`{node}`'s `spikes` answer names the branch `{branch}` for `{spike}`, which "
            f"could be a retry of `{spike}` or the branch of the plan's own spike `{last}`; "
            f"link each spike to a branch that names it alone",
        )
    return Refused(
        node,
        "task",
        f"`{node}`'s `spikes` answer names the branch `{branch}` for `{spike}`, and a "
        f"spike's branch ends in `/{SPIKE_PREFIX}<topic>` for that spike, or "
        f"`/{SPIKE_PREFIX}<topic>-<n>` for a retry of it; link the branch the spike settled "
        f"`preserved` on",
    )


def _evidence_refusals(
    task: Mapping[str, object],
    documents: Sequence[plan_store.StoreDocument],
    spikes: frozenset[NodeId],
    drafted: Drafted,
    plan: str,
) -> Iterator[Refused]:
    node = NodeId(str(task["id"]))
    body = task.get("task")
    for line in malformed_evidence(body if isinstance(body, str) else ""):
        yield Refused(
            node,
            "task",
            f"`{node}`'s `## Spike evidence` holds the entry {line!r}, which is not one the "
            f"`plan-task` template renders, so the spike it links goes unchecked; regenerate "
            f"the task from its `spikes` answer",
        )
    for linked in evidence(body if isinstance(body, str) else ""):
        named = held_report_id(plan, linked.spike, documents, drafted)
        resolved = report_for(linked, documents, drafted)
        if resolved is None or linked.report.rpartition(":")[2] != named:
            yield Refused(
                node,
                "task",
                f"`{node}`'s `spikes` answer links the report `{linked.report}` of "
                f"`{linked.spike}`, which is not that spike's report among this plan's "
                f"documents; a spike's report is the document `{named}` it writes there from "
                f"the `spike-report` template, so link the one it wrote",
            )
        refused = branch_refusal(node, linked, spikes)
        if refused is not None:
            yield refused


def _spikes_of(
    tasks: Sequence[Mapping[str, object]],
    documents: Sequence[plan_store.StoreDocument],
    drafted: Drafted,
    plan: str,
) -> frozenset[NodeId]:
    """Every spike the plan knows: each one a task links, and each one whose report it holds.

    A report's id is read against ``plan`` and every plan its linked spikes' provenance names
    (:func:`provenance_plans`), so a copy whose drafted plan could not be read knows them too.
    """
    bodies = [task.get("task") for task in tasks]
    linked = {one.spike for body in bodies if isinstance(body, str) for one in evidence(body)}
    reports = [held for held in documents if design_approval.is_spike_report(held)]
    plans = sorted({plan} | provenance_plans(reports, (*linked, VISUAL_SPIKE), drafted))
    reported = {
        spike
        for held in reports
        for name in names(held, drafted)
        if ":" not in name
        for one in plans
        if (spike := report_spike(one, name)) is not None
    }
    return frozenset(linked | reported)


#: The source a plan is drafted in. Once a host declares it private, its copies onto a board
#: carry no `onetaskgraph.origin`: the store keeps that correspondence on this side instead.
AUTHORING = "authoring"


def drafting_sources() -> tuple[str, ...]:
    """Where a plan copied without an origin may have been drafted, :data:`AUTHORING` first.

    Every default source declared private copies as :data:`AUTHORING` does, keeping the link
    on its own side — this checkout's `test-fixtures` among them — so each is a source a copy's
    drafted project may be in. A source read only by name is not one a plan is drafted in.
    """
    settings = plan_store.configured_settings()
    defaults = settings.get("default_sources")
    named = defaults if isinstance(defaults, list) else []
    private = (
        source
        for source in named
        if source != AUTHORING and settings.get(f"sources.{source}.visibility") == "private"
    )
    return (AUTHORING, *private)


def drafted_project(project: str) -> plan_store.StoreProject | None:
    """The project of a :func:`drafting_sources` source whose copy link names ``project``.

    ``None`` for a project of such a source itself, for one no drafted project links, and where
    the sources cannot be read — a reader then goes by the origin a copy carries alone. An id
    that is not qualified is linked by nothing, and is left for its caller to refuse.
    """
    # llmlint: ignore-block[changed_behavior_has_e2e] No plan-flow journey can reach this: a
    # store whose configuration does not resolve refuses every plan-store recipe before a
    # spike report is looked up. `tests/test_spike_plan.py` drives it through the real store
    # under a user layer that is not valid YAML, with nothing substituted.
    try:
        sources = drafting_sources()
    except OSError:
        return None
    # llmlint: ignore-end[changed_behavior_has_e2e]
    if project.partition(":")[0] in sources:
        return None
    for source in sources:
        try:
            drafted_projects = plan_store.read_projects(source)
        except OSError:
            continue
        for drafted in drafted_projects:
            copies = drafted.metadata.get(plan_store.COPIES_KEY)
            if isinstance(copies, Mapping) and project in copies.values():
                return drafted
    return None


#: A copy's document, by its qualified id, mapped to the qualified id it was copied from.
type Drafted = Mapping[str, str]


def drafted_ids(project: str) -> dict[str, str]:
    """Each document of ``project``, a copy of a privately drafted plan, by its source.

    A copy out of a source declared private records no `onetaskgraph.origin` on the items it
    creates, and keeps the correspondence on the private side as the store's own
    `onetaskgraph.copies` link instead. So the drafted project whose link names ``project`` is
    found (:func:`drafted_project`), and each of its documents' links names the copy it
    reached: the answer maps that copy's id to the drafted document's. Empty where no drafted
    project is found or its documents cannot be read — a reader without them then finds a
    report by the origin a copy carries alone, as before.
    """
    return dict(DraftedPlan(project).links())


class DraftedPlan:
    """What the drafting sources record of the copy ``project``, read at most once, when asked.

    One invocation asks it of several readers — a design document carrying no origin, reports
    carrying none, the plan's drafted native id — so it is made once per invocation and handed
    to each, and each part is read the first time one of them needs it: the drafted project
    (:func:`drafted_project`), then that project's documents' links (:func:`drafted_ids`). It
    is never kept beyond the invocation that made it.
    """

    def __init__(self, project: str) -> None:
        self._copy = project
        self._drafted: list[plan_store.StoreProject | None] = []
        self._links: Drafted | None = None

    def project(self) -> plan_store.StoreProject | None:
        """:func:`drafted_project` of the copy, read the first time it is asked."""
        if not self._drafted:
            self._drafted.append(drafted_project(self._copy))
        return self._drafted[0]

    def links(self) -> Drafted:
        """:func:`drafted_ids` of the copy, read the first time it is asked."""
        if self._links is None:
            self._links = self._read_links()
        return self._links

    def _read_links(self) -> Drafted:
        drafted = self.project()
        if drafted is None:
            return {}
        try:
            documents = plan_store.read_documents(str(drafted.qualified_id))
        except OSError:
            return {}
        return {
            link: str(document.qualified_id) for document in documents for link in _links(document)
        }


def _links(document: plan_store.StoreDocument) -> list[str]:
    copies = document.metadata.get(plan_store.COPIES_KEY)
    if not isinstance(copies, Mapping):
        return []
    return [link for link in copies.values() if isinstance(link, str)]


def _drafted_for(
    project: str,
    documents: Sequence[plan_store.StoreDocument],
    lookup: DraftedPlan | None = None,
) -> Drafted:
    """:func:`drafted_ids` for ``project``, read only when it holds a report carrying no origin.

    Read through ``lookup``, the invocation's own :class:`DraftedPlan`, when given.
    """
    if any(
        design_approval.is_spike_report(held) and plan_store.ORIGIN_KEY not in held.metadata
        for held in documents
    ):
        return (DraftedPlan(project) if lookup is None else lookup).links()
    return {}


def names(report: plan_store.StoreDocument, drafted: Drafted | None = None) -> frozenset[str]:
    """Every name a task's `spikes` answer may link ``report`` by, on any copy of the plan.

    Its own id, qualified and native, and — on a copy, where the destination mints an id of
    its own — the id of the document it was copied from: the one the plan store records on the
    copy as `onetaskgraph.origin`, or, for a copy out of a private source, the one ``drafted``
    (:func:`drafted_ids`) reads off that source's own link. Either record is the store's, so a
    task written where the plan was drafted names its report wherever the plan is read.
    """
    held = [str(report.qualified_id), *_recorded(report, drafted)]
    return frozenset(name for one in held for name in (one, one.partition(":")[2]))


def _recorded(report: plan_store.StoreDocument, drafted: Drafted | None) -> list[str]:
    """The qualified ids the store records ``report`` was copied from, if any."""
    held: list[str] = []
    origin = report.metadata.get(plan_store.ORIGIN_KEY)
    if isinstance(origin, str) and origin:
        held.append(origin)
    source = (drafted or {}).get(str(report.qualified_id))
    if source:
        held.append(source)
    return held


def _provenance(report: plan_store.StoreDocument, drafted: Drafted | None) -> list[str]:
    """The native ids ``report`` was copied from, as :func:`_recorded` names them."""
    return [one.partition(":")[2] for one in _recorded(report, drafted)]


def report_for(
    linked: Evidence,
    documents: Sequence[plan_store.StoreDocument],
    drafted: Drafted | None = None,
) -> plan_store.StoreDocument | None:
    """The spike-report document of ``documents`` a task's ``linked`` evidence names, if any."""
    return next(
        (
            held
            for held in documents
            if design_approval.is_spike_report(held) and linked.report in names(held, drafted)
        ),
        None,
    )


def visual_report(
    plan: str, documents: Sequence[plan_store.StoreDocument], drafted: Drafted | None = None
) -> plan_store.StoreDocument | None:
    """The plan ``plan``'s visual report among ``documents``, or ``None`` for a plan with none.

    Found as a task's evidence finds a report: a spike-report document named by
    :func:`held_report_id` for :data:`VISUAL_SPIKE`, its own name or — on a copy — the one it
    was copied from (:func:`names`).
    """
    named = held_report_id(plan, VISUAL_SPIKE, documents, drafted)
    return next(
        (
            held
            for held in documents
            if design_approval.is_spike_report(held) and named in names(held, drafted)
        ),
        None,
    )


#: Optional project selection; absence retains the canonical report convention.
VISUAL_REPORT_KEY = "orchestrator.visual-report"


def current_visual_report(
    project: str,
    documents: Sequence[plan_store.StoreDocument],
    record: Mapping[str, object] | None = None,
    lookup: DraftedPlan | None = None,
) -> plan_store.StoreDocument | None:
    """Resolve current evidence by full id or copy origin, refusing explicit bad selections.

    Selections are opaque qualified document names, scoped to this project's documents.
    Null, non-string, blank, malformed, missing, wrong-kind, wrong-project and ambiguous
    selections fail closed; only an absent key delegates to :func:`visual_report`.
    ``record`` is the project's own record when the caller has already read it, and
    ``lookup`` the invocation's own :class:`DraftedPlan` of ``project`` when it holds one.
    """
    lookup = DraftedPlan(project) if lookup is None else lookup
    drafted = _drafted_for(project, documents, lookup)
    metadata = (plan_store.project_record(project) if record is None else record).get("metadata")
    if not isinstance(metadata, Mapping) or VISUAL_REPORT_KEY not in metadata:
        # A project holding no spike report has no visual one under any name, so the drafted
        # plan's native id — a listing of the authoring source — is not worth asking for.
        if not any(design_approval.is_spike_report(held) for held in documents):
            return None
        return visual_report(plan_native(project, lookup), documents, drafted)
    selected = metadata[VISUAL_REPORT_KEY]
    reason: str | None
    match selected:
        case None:
            reason = "null"
        case str() if not selected.strip():
            reason = "blank"
        case str() if qualified_id(selected) is None:
            reason = "malformed"
        case str():
            reason = None
        case _:
            reason = "non-string"
    if reason:
        raise OSError(f"{project}: {VISUAL_REPORT_KEY} {reason} selection {selected!r}")
    matches = [
        held
        for held in documents
        if selected
        in (
            str(held.qualified_id),
            held.metadata.get(plan_store.ORIGIN_KEY),
            drafted.get(str(held.qualified_id)),
        )
    ]
    match matches:
        case []:
            reason = "missing or wrong-project"
        case [held] if held.project not in (project, project.partition(":")[2]):
            reason = "wrong-project"
        case [held] if not design_approval.is_spike_report(held):
            reason = "wrong-kind"
        case [held]:
            return held
        case _:
            reason = "ambiguous"
    raise OSError(f"{project}: {VISUAL_REPORT_KEY} {reason} selection {selected!r}")


def main(argv: Sequence[str] | None = None) -> int:
    """Print the qualified id of a plan project's visual report, or nothing when it has none.

    `scripts/finish-plan.sh` asks this before it composes the design document's writer task,
    so the reserved ids are read from here rather than spelled in the script.
    """
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.spike_plan",
        description="Name the visual spike's report a plan project holds, if any.",
    )
    parser.add_argument("command", choices=["visual-report"])
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    args = parser.parse_args(argv)
    try:
        held = current_visual_report(args.project, plan_store.read_documents(args.project))
    except OSError as exc:
        print(
            f"spike_plan: the documents of {args.project} could not be read: {exc}", file=sys.stderr
        )
        return 2
    if held is not None:
        print(held.qualified_id)
    return 0


def refusals(project: str, document: object) -> list[Refused]:
    """Everything `just check-plan` refuses about ``project``'s spike convention.

    The stamp and the project's documents are read from the store by ``project``, because
    the loaded plan carries neither; one that cannot be read is refused rather than passed,
    since a rule this cannot check is not one the plan has met.
    """
    try:
        stamped = design_approval.stamped_launch(project)
        documents = plan_store.read_documents(project)
    except OSError as exc:
        return [Refused(None, None, f"{project}'s spike convention could not be checked: {exc}")]
    is_spikes = stamped is not None and stamped.kind == design_approval.SPIKES
    found: list[Refused] = []
    tasks = list(_tasks(document))
    drafted = _drafted_for(project, documents)
    plan = plan_native(project)
    spikes = _spikes_of(tasks, documents, drafted, plan)
    for task in tasks:
        found.extend(_spike_node_refusals(task, is_spikes))
        found.extend(_evidence_refusals(task, documents, spikes, drafted, plan))
    return found


if __name__ == "__main__":
    raise SystemExit(main())
