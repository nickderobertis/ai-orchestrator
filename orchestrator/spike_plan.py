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
* each spike writes its report as the document ``<spike id>-report`` of the **plan's**
  project, rendered from the ``spike-report`` template; on a copy of the plan, whose
  destination mints the document an id of its own, the report is the ``spike-report``
  document whose ``onetaskgraph.origin`` — the plan store's own record of what a copy was
  copied from — names that id. The store has no document lookup by origin, so the check
  reads the plan project's own documents, one project-scoped read;
* a spike may depend on other spikes — a shared harness spike the area spikes build on —
  and the engine starts it from the branch its base dependency kept, so same-repository
  spike dependencies lie on one stacking chain; the engine's ``plan check`` refuses a fan-in
  no chain resolves, a dependency on another repository's spike orders only, and those
  edges are the spikes project's own, which no report copies;
* a task building on spikes links each through the ``plan-task`` template's ``spikes``
  answer — ``{spike, report, branch}`` — which renders as a ``## Spike evidence`` section:
  each spike it builds on and every spike above it in that spike's stacking chain, never a
  dependency edge on a spike.

That section is what the check reads, rather than the store's stored answers: a store keeps
a task's answers only where it was drafted and never copies them, while the rendered body
travels with every copy, and the engine's ``require_rendered`` holds that body to being the
template's rendering. :data:`EVIDENCE_ENTRY` is the one reading of the line the template
renders, and `tests/plan_tooling/test_check_plan_spikes_e2e.py` holds the two together by
checking bodies the pinned store rendered from that template.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import NamedTuple, NewType

from orchestrator import design_approval, plan_store
from orchestrator.plan_store import NodeId

#: A spike's preserved branch, as `onevcs` names it with the host's prefix in front, and the
#: id of a spike's report document, as the plan's project holds it.
Branch = NewType("Branch", str)
ReportId = NewType("ReportId", str)

#: What every spike node's id starts with, and so what every spike branch's last segment does.
SPIKE_PREFIX = "spike-"

#: What a plan's spikes project is called: the plan project's own id with this appended.
SPIKES_SUFFIX = "-spikes"

#: What a spike's report document is called: the spike's node id with this appended.
REPORT_SUFFIX = "-report"

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


def report_id(spike: NodeId) -> ReportId:
    """The document id ``spike``'s report is stored under in the plan's project."""
    return ReportId(f"{spike}{REPORT_SUFFIX}")


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


def _branch_refusal(node: NodeId, linked: Evidence, spikes: frozenset[NodeId]) -> Refused | None:
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
        named = report_id(linked.spike)
        if report_for(linked, documents) is None or linked.report.rpartition(":")[2] != named:
            yield Refused(
                node,
                "task",
                f"`{node}`'s `spikes` answer links the report `{linked.report}` of "
                f"`{linked.spike}`, which is not that spike's report among this plan's "
                f"documents; a spike's report is the document `{named}` it writes there from "
                f"the `spike-report` template, so link the one it wrote",
            )
        refused = _branch_refusal(node, linked, spikes)
        if refused is not None:
            yield refused


def _spikes_of(
    tasks: Sequence[Mapping[str, object]], documents: Sequence[plan_store.StoreDocument]
) -> frozenset[NodeId]:
    """Every spike the plan knows: each one a task links, and each one whose report it holds."""
    bodies = [task.get("task") for task in tasks]
    linked = {one.spike for body in bodies if isinstance(body, str) for one in evidence(body)}
    reported = {
        NodeId(name.removesuffix(REPORT_SUFFIX))
        for held in documents
        if design_approval.is_spike_report(held)
        for name in names(held)
        if name.endswith(REPORT_SUFFIX) and ":" not in name
    }
    return frozenset(linked | reported)


def names(report: plan_store.StoreDocument) -> frozenset[str]:
    """Every name a task's `spikes` answer may link ``report`` by, on any copy of the plan.

    Its own id, qualified and native, and — on a copy, where the destination mints an id of
    its own — the id of the document it was copied from, which the plan store records on the
    copy as `onetaskgraph.origin`. That record is the store's, and travels with every copy,
    so a task written where the plan was drafted names its report wherever the plan is read.
    """
    held = [str(report.qualified_id)]
    origin = report.metadata.get(plan_store.ORIGIN_KEY)
    if isinstance(origin, str) and origin:
        held.append(origin)
    return frozenset(name for one in held for name in (one, one.partition(":")[2]))


def report_for(
    linked: Evidence, documents: Sequence[plan_store.StoreDocument]
) -> plan_store.StoreDocument | None:
    """The spike-report document of ``documents`` a task's ``linked`` evidence names, if any."""
    return next(
        (
            held
            for held in documents
            if design_approval.is_spike_report(held) and linked.report in names(held)
        ),
        None,
    )


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
    spikes = _spikes_of(tasks, documents)
    for task in tasks:
        found.extend(_spike_node_refusals(task, is_spikes))
        found.extend(_evidence_refusals(task, documents, spikes))
    return found
