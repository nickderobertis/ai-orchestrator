"""Record a person's approval of a plan's design document, and refuse a launch without one.

A plan is not what a person can usefully review. What they can judge is the one short
document `config/design-doc-template.md` states — what is being built and why, the
architecture, the contracts, the acceptance criteria, and the planned work as a table of
links — written for a reader who has no depth in the domain. So the thing put in front of
the user is that document, and **their approval of it is what gates dispatch**: `just
approve-design` records the approval, and a launch against a project carrying none is
refused before anything is dispatched.

Four properties are deliberate, and each is the one `orchestrator/plan_review.py` already
defends for the plan-review gate beside this one:

* **Only an approval is ever recorded.** There is no record of a rejection, so there is
  nothing to replay and nothing that could read as a decision nobody made.
* **A record is authoritative.** Reading it is the whole check; nothing re-asks, because a
  second opinion on identical content is how one plan comes to carry two verdicts.
* **There is no escape hatch** — no flag, no option, no environment variable. An escape
  here is reached under exactly the time pressure that makes skipping this a mistake.
* **The key covers the bar as well as the content, and nothing else.** It is a digest of
  the document's own authored content *and* of the tracked template that says what a design
  document is, so editing the document loses its approval and moving the template
  invalidates every approval granted under the previous one — while nothing the store the
  document sits in owns is in it at all.

**The record goes onto the document itself**, in the open metadata map every store carries,
rather than into a file beside the plan. That is what makes it readable from whichever
store the plan is held in: it travels with the document through `just copy-plan` exactly as
a review record travels with a task, so a plan cleared where it was drafted is still cleared
once it reaches the board. `orchestrator/plan_store.py` owns the write and states what it
costs. **Carrying the record is only half of travelling**, and the other half is that
last clause of the bullet above: a copy is defined to change where a record sits, so a key
covering any part of that would arrive intact and no longer match — see :func:`approval_key`
for the identifier that did exactly that.

**One exemption exists, and what it is scoped to is the half worth reading.** A planning
*launch* is exempt, because that run's output is the plan and the document it will be read
as does not exist until the run has written one. The one-node project `just follow-ups`
writes is exempt on the same terms and under the same bound, stamped as its own kind: its
one node verifies a finished run's drafted follow-ups, and there is no plan for a person to
read it as. That is a statement about a launch with
nothing to approve yet, and it stops being true of the project the moment either half of it
does — so the exemption is bounded by both halves rather than by the stamp alone:

* **the project holds exactly the nodes that launch dispatches**, which
  `scripts/plan.sh` names on the stamp itself — and *exactly* is meant in both
  directions. A plan a planner writes into the planning project is executable work in a
  project that is no longer only that launch, and it is gated like any other; a stamp
  claiming a node the project does not hold is bounded to a launch nothing here can see,
  and the project grows into that claim silently, so the exemption ends there too;
* **and it holds no design document**, because once one exists there is something a person
  can read and the exemption has nothing left to stand on.

It was scoped to the *project* until this, and the stamp alone decided it — so a project
that had ever been a planning project was exempt for the rest of its life, executable nodes
and all. What made that invisible is that being exempt and being approved were the same
silence from the launch's side, which is why :class:`Assessment` carries both and
:func:`gate_main` reports the exemption it acted on rather than passing over it.

The stamp is still what says a project is a planning project at all, and still a fact the
project states rather than a shape recognised from outside — a hand-written two-node project
is not quietly exempt. What has changed is that stating it is no longer sufficient.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple, NewType

from orchestrator import plan_store
from orchestrator.plan_store import NodeId, StoreDocument
from orchestrator.root import REPO_ROOT

#: A digest of the bar a design document is written and read against — the tracked
#: template. Distinct from :data:`ApprovalKey`, which is a digest of one document's
#: content *under* that bar: both are hex strings of the same length and each is
#: meaningless in the other's place.
TemplateFingerprint = NewType("TemplateFingerprint", str)

#: The digest one document's authored content approved under one template hashes to —
#: what a record holds and what the launch gate compares against.
ApprovalKey = NewType("ApprovalKey", str)

#: Where one document's approval record lives: a namespaced entry of the open metadata
#: map the document already carries, so it travels with the document into whichever store
#: the plan is copied to and stays visible to anybody reading it.
RECORD_KEY = "orchestrator.design-approval"

#: What a design document is, and the only statement of it. Hashed into every key, so an
#: approval granted under one shape does not stand once that shape has moved.
TEMPLATE = Path("config") / "design-doc-template.md"

#: Where a project says what kind of plan it is, and — for a planning project — what the
#: launch that wrote it dispatches. A key of this repository's own rather than an
#: `onepipeline.` one, because it is a fact about the project and not a plan field: the
#: engine never sees it, and `orchestrator/plan_store.py`'s `read_plan` drops it.
PLAN_KIND = "orchestrator.plan-kind"

#: The kinds :data:`PLAN_KIND` names that are exempt. Every other project — and one that
#: says nothing — is gated. `scripts/plan.sh` and `scripts/finish-plan.sh` stamp the first;
#: `scripts/follow-ups.sh` stamps the second, whose launch dispatches exactly one node, so a
#: stamp of that kind naming more than one bounds nothing and exempts nothing.
PLANNING = "planning"
FOLLOW_UPS = "follow-ups"
EXEMPT_KINDS = (PLANNING, FOLLOW_UPS)

#: The field of that stamp naming the kind, and the field naming the node ids the launch
#: wrote. The second is what bounds the exemption to the launch it was written for, and
#: the bound is an agreement rather than a covering: a project holding any task beyond
#: those is not that launch any more, and a stamp claiming any node the project does not
#: hold is not a claim about this project's launch. A stamp naming **no** node bounds
#: nothing and so exempts nothing — which covers a bare :data:`PLANNING` string, the shape
#: the stamp had while the exemption was scoped to the project, and is the safe direction
#: for a claim this cannot read.
STAMP_KIND = "kind"
STAMP_NODES = "nodes"

#: Those same four names again, as the dotted forms a pattern can read them by. A mapping
#: pattern's key is a literal or a dotted name and a bare constant is neither, so matching
#: the stamp's shape directly — which is what :func:`planning_launch` does — needs this
#: rather than a second copy of the spellings beside the constants above.
_STAMP = SimpleNamespace(key=PLAN_KIND, kind=STAMP_KIND, nodes=STAMP_NODES, planning=PLANNING)

#: What each exempt kind's launch is, in the words a reader gets.
_LAUNCHES = {
    PLANNING: (
        "the plan a planning launch is writing",
        "that run's output is the plan, and the document it will be read as does not exist "
        "until it has written one",
        "the plan a planning launch writes",
    ),
    FOLLOW_UPS: (
        "the project a follow-ups launch writes",
        "its one node verifies a finished run's drafted follow-ups, and there is no plan for "
        "a person to read it as",
        "the project a follow-ups launch writes",
    ),
}

#: The recipe that records an approval, named in every refusal that wants one.
RECIPE = "just approve-design"

#: What a qualified id looks like before anything is asked of the store, read by both
#: entry points here and meaning something different to each. The launch gate reads a
#: command line it deliberately does not parse — see :func:`gate_main` — so there this is
#: what tells a project id from a flag value; :func:`qualified` refuses what does not match
#: it, because `just approve-design` is handed one argument and it is a project id or it is
#: nothing. It is narrow on the source half because a source is a configured name rather
#: than arbitrary text.
QUALIFIED = re.compile(r"[A-Za-z0-9_.-]+:[^\s]+\Z")


class Assessment(NamedTuple):
    """What the gate found about one project, and at most one of these is ever set.

    Both are carried because "exempt" and "approved" are two different answers, and the
    launch reported them as one silence for as long as the exemption was scoped to a
    project: a plan whose nodes sat in a project that had once been a planning project
    was let through exactly as an approved one was, with nothing said either way.
    """

    #: Why the project may not be launched, or ``None`` when it may.
    refusal: str | None = None
    #: Why it is launched with no approved design document, when it is. The exemption in
    #: the words a reader gets, so a launch that acted on one says so.
    exemption: str | None = None


class StampedLaunch(NamedTuple):
    """The exempt launch a project's stamp describes: its kind, and the nodes it dispatches."""

    kind: str
    nodes: frozenset[NodeId]


class Approved(NamedTuple):
    """What one `just approve-design` did, named rather than positional."""

    #: The document the approval was recorded against.
    document: plan_store.QualifiedDocumentId
    #: Where the store says that document is, reported back rather than composed.
    location: str
    #: True when the document already carried an approval for this exact content, so
    #: nothing was written. Repeating the command is a no-op rather than a second record.
    held: bool


# llmlint: ignore[changed_behavior_has_e2e] What a moved template does is a key that no
# longer matches, and that mechanism is driven end to end by
# `tests/plan_tooling/test_approve_design_recipe_e2e.py`, which edits a real document and
# has a real launch refused for it. The only half left is *which* input moved, and driving
# that means moving a tracked file of this checkout — which every other tier of this suite
# reads concurrently — or installing a second copy of it to move the file in. What does
# catch a moved template is deterministic rather than absent: the shipped-examples test in
# `tests/test_design_approval.py` goes red until every example this repository documents as
# launchable has been read against the new shape and approved again.
def template_fingerprint(root: Path = REPO_ROOT) -> TemplateFingerprint:
    """A digest of the design-document template in force, over ``root``'s copy of it."""
    digest = hashlib.sha256()
    digest.update(TEMPLATE.as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update((root / TEMPLATE).read_bytes())
    return TemplateFingerprint(digest.hexdigest())


def approval_key(document: StoreDocument, template: TemplateFingerprint) -> ApprovalKey:
    """The digest ``document``'s content hashes to, approved under ``template``.

    **Exactly the authored content, and the bar.** The title, the prose, and the template
    that says what a design document is — the two things a person read when they approved
    it, and the shape they read them under. **Nothing the destination store owns is here**,
    and that is the whole of what makes the record travel: a copy is *defined* to change
    where a record sits, so a key covering any of that would invalidate the approval in the
    act of moving it. `project` in particular is not: it is the store's own local
    identifier for the plan the document belongs to, and the same document is a document of
    `some-plan` where it was drafted and of an opaque board identifier once it is copied —
    so a key over it refused every copied plan for want of the approval it was carrying,
    and both ways past that were wrong: recording an approval against a copy nobody had
    read, or launching from the drafting store and projecting every settlement into a
    gitignored local directory. `onetaskgraph.origin` and the record itself are outside it
    for the same reason one step further: both are metadata the copy writes. The labels are
    out for the weaker reason that nobody approves a label.

    One thing that costs is worth knowing rather than discovering, and it is the same one
    `orchestrator/plan_review.py`'s `review_key` names: a document **moved to another
    plan** after its approval keeps that approval. What a person approved is the document,
    and it is unchanged.
    """
    authored = {
        "content": document.content,
        "template": template,
        "title": document.title,
    }
    rendered = json.dumps(authored, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return ApprovalKey(hashlib.sha256(rendered.encode("utf-8")).hexdigest())


def recorded(document: StoreDocument) -> ApprovalKey | None:
    """The digest ``document``'s record names, or ``None`` when it carries no readable one.

    A record this cannot read is answered as no record, which is the safe direction: the
    document is then unapproved, which is what an unreadable approval means anyway.
    """
    held = document.metadata.get(RECORD_KEY)
    if not isinstance(held, dict):
        return None
    key = held.get("key")
    return ApprovalKey(key) if isinstance(key, str) else None


def located(document: StoreDocument) -> str:
    """Where the store says ``document`` is, in the form the store reports it.

    :func:`~orchestrator.plan_store.located` is the one reading of a store's `location`
    answer, and this names the fallback that reading takes when the store reports none:
    the document's own qualified id, which addresses it even where nothing can open it.
    """
    return plan_store.located(document.location, str(document.qualified_id))


def design_document(project: str) -> StoreDocument:
    """``project``'s design document, or ``OSError`` saying why there is not exactly one."""
    return one_document(project, plan_store.read_documents(project))


def one_document(project: str, documents: Sequence[StoreDocument]) -> StoreDocument:
    """The one of ``documents`` that is ``project``'s design document.

    A project holds documents rather than *the* document, so "the design document" is the
    one document of the project. Several is refused rather than guessed at: an approval
    recorded against the wrong one of two reads as sound from every side afterwards, and
    the person who wrote the second document is the one who can say which is which.

    Separate from :func:`design_document` because :func:`assess` has already had to read
    them — whether a planning project holds one at all is half of what ends its exemption
    — and a second read would ask the store the same question twice per launch.
    """
    if not documents:
        raise OSError(
            f"{project} holds no design document, so there is nothing a person could have "
            f"approved; a planning run's `design-doc` node writes one into the plan's own "
            f"project, and `{RECIPE} {project}` records the approval once it is there"
        )
    if len(documents) > 1:
        named = ", ".join(sorted(document.qualified_id for document in documents))
        raise OSError(
            f"{project} holds {len(documents)} documents ({named}), so which of them is the "
            f"design document cannot be decided here; leave the project one document, or "
            f"move the others to a project of their own"
        )
    return documents[0]


def stamped_launch(project: str) -> StampedLaunch | None:
    """The exempt launch that wrote ``project``, or ``None``.

    ``None`` for every project stating no exempt stamp, and for one whose stamp this
    cannot read — an unreadable claim to an exemption is answered as no claim at all,
    which is the direction :func:`recorded` takes for the same reason: the project is then
    gated, which is what an unreadable exemption means anyway.

    The node ids are the stamp's, rather than this repository's second copy of the ids
    `scripts/plan.sh` uses, and :func:`assess` holds them to *agreeing* with the project's
    own tasks rather than to covering them. Drift in either direction is then a failing
    journey rather than a hole: a launch that grows a node and does not name it on the
    stamp holds a task the stamp does not, and a stamp naming a node the launch never wrote
    describes a launch nothing here can see — which the project would grow into silently.

    **A node claimed twice is answered as no claim at all**, for the reason every other
    unreadable shape is. A launch dispatches each of its nodes once, so a repeated id is
    not a set of ids any launch wrote; reading it charitably means collapsing it, and what
    the exemption would then report as the launch's nodes is not what the stamp says. So is
    a :data:`FOLLOW_UPS` stamp naming anything but one node, because that launch writes one.
    """
    match plan_store.project_record(project).get("metadata"):
        # A guard rather than a pattern over the elements: a sequence pattern says how many
        # there are, and what is asked here is of every one of them however many that is.
        case {_STAMP.key: {_STAMP.kind: str() as kind, _STAMP.nodes: [*written]}} if (
            kind in EXEMPT_KINDS
            and all(isinstance(node, str) for node in written)
            and len(set(written)) == len(written)
            and (kind != FOLLOW_UPS or len(written) == 1)
        ):
            # An empty list is answered with the shapes below rather than as an exemption
            # over an empty project: a stamp that names no node bounds the exemption to
            # nothing, and a launch this cannot bound is one this must not exempt.
            nodes = frozenset(NodeId(node) for node in written)
            return StampedLaunch(kind, nodes) if nodes else None
        # Every other shape at once, which is the whole of what "cannot read it" means
        # here: a project stating no stamp, one whose stamp is not a mapping at all, one
        # naming another kind, and one whose `nodes` is absent, is not a sequence, holds
        # something that is not a node id, or names one of them twice.
        case _:
            return None


def planning_launch(project: str) -> frozenset[NodeId] | None:
    """The node ids the planning launch that wrote ``project`` dispatches, or ``None``.

    :func:`stamped_launch` narrowed to the :data:`PLANNING` kind, which is every reading of
    the stamp that is not the gate's own.
    """
    stamped = stamped_launch(project)
    return stamped.nodes if stamped is not None and stamped.kind == PLANNING else None


def exemption(project: str, written: frozenset[NodeId], kind: str = PLANNING) -> str:
    """Why a launch of ``project`` is not asked for an approved design document.

    Stated in the reader's words rather than reported as a flag, because this is the one
    answer that lets a plan dispatch with nobody having approved anything, and it used to
    be indistinguishable from an approval.
    """
    named = ", ".join(sorted(written))
    launch, reason, _ = _LAUNCHES[kind]
    return (
        f"{project} is {launch}: it holds exactly the "
        f"{len(written)} node(s) that launch dispatches ({named}) and no design document "
        f"yet, so it is launched without an approved one — {reason}. It "
        f"stops being exempt as soon as either of those is no longer true"
    )


def lapsed(
    project: str,
    beyond: Sequence[NodeId],
    unheld: Sequence[NodeId],
    documents: Sequence[StoreDocument],
    kind: str = PLANNING,
) -> str:
    """Why ``project``'s exempt stamp no longer exempts it, said beside the refusal.

    A planning project asked for an approval reads like the gate mis-firing unless the
    lapse is named, and naming it is also what makes the bound legible from outside: the
    exemption covers a planning launch, and this says which part of that has passed.

    ``beyond`` and ``unheld`` are the two directions the bound is an agreement in — tasks
    the stamp does not claim, and claims the project does not hold — and they are said
    apart because they owe different readings. The first is a project that has grown past
    the launch that wrote it, which is the ordinary way a planning project stops being one;
    the second is a stamp describing a launch this project is not, which is a claim to an
    exemption over work that has not arrived yet.
    """
    ended = []
    if beyond:
        ended.append(
            f"it holds {len(beyond)} task(s) that launch never wrote ({', '.join(beyond)})"
        )
    if unheld:
        ended.append(
            f"its stamp claims {len(unheld)} node(s) the project does not hold "
            f"({', '.join(unheld)}), so it does not describe this project's launch"
        )
    if documents:
        ended.append(f"it holds {len(documents)} design document(s), so there is something to read")
    return (
        f"({project} is stamped as {_LAUNCHES[kind][2]}, which is exempt from "
        f"this gate — but only for that launch, and {' and '.join(ended)}.)"
    )


def approve(project: str) -> Approved:
    """Record a person's approval of ``project``'s design document, and say where it went.

    Repeating it on unchanged content writes nothing and reports the record it found, so a
    retried command and a second person running it are both harmless. Everything else —
    edited prose, a moved template — is content nobody has approved, and this is what
    approves it.
    """
    document = design_document(project)
    key = approval_key(document, template_fingerprint())
    if recorded(document) == key:
        return Approved(document.qualified_id, located(document), held=True)
    value = {"key": key, "approved_at": datetime.now(UTC).isoformat()}
    written = plan_store.sdk(
        plan_store.client().document_metadata_set(
            str(document.qualified_id), RECORD_KEY, json.dumps(value)
        )
    )
    location = plan_store.located(
        written.location.model_dump(mode="python") if written.location else None,
        written.id.model_dump(),
    )
    return Approved(document.qualified_id, location, held=False)


def assess(project: str) -> Assessment:
    """Whether ``project`` may be launched, and on what — the whole of the gate's answer.

    The two refusals are told apart in the text, because they owe different next actions:
    a project with no design document is waiting on the document being written, and one
    whose document is unapproved is waiting on a person reading it. A project whose
    planning stamp has stopped exempting it carries :func:`lapsed` beside whichever of
    those it gets, so a planning project asked for an approval says why it is being asked.

    The stamp is held to *agreeing* with the project's own tasks rather than to covering
    them, which is the whole of the second bound: the tasks the stamp does not claim end
    the exemption because the project has grown past that launch, and the claims the
    project does not hold end it because a stamp bounded to nodes that are not here is
    bounded to nothing this can check — the project grows into them and stays exempt.

    A project whose task list cannot be read at all is *refused* rather than exempted,
    which falls out of asking for it before the exemption is granted: the store's own
    words become the refusal, because a bound this cannot check is one that cannot hold.
    """
    stamped = stamped_launch(project)
    note: str | None = None
    try:
        documents = plan_store.read_documents(project)
        if stamped is not None:
            held = frozenset(task.node_id for task in plan_store.read_tasks(project))
            beyond = tuple(sorted(held - stamped.nodes))
            unheld = tuple(sorted(stamped.nodes - held))
            if not beyond and not unheld and not documents:
                return Assessment(exemption=exemption(project, stamped.nodes, stamped.kind))
            note = lapsed(project, beyond, unheld, documents, stamped.kind)
        document = one_document(project, documents)
    except OSError as exc:
        return Assessment(refusal=stated(str(exc), note))
    if recorded(document) == approval_key(document, template_fingerprint()):
        return Assessment()
    return Assessment(
        refusal=stated(
            f"{project}'s design document {document.qualified_id} carries no approval for "
            f"what it currently says. It is at {located(document)}: read it, and record "
            f"the approval with `{RECIPE} {project}`",
            note,
        )
    )


def stated(reason: str, note: str | None) -> str:
    """``reason``, with ``note`` after it when there is one to say."""
    return reason if note is None else f"{reason} {note}"


def qualified(project: str) -> str:
    """``project`` itself when it is a qualified id, or ``ValueError`` saying what one is.

    The one thing asked of the argument before anything is asked of the store, and it is
    asked here because this is where text somebody typed crosses into this repository.
    :data:`QUALIFIED` is the same shape :func:`gate_main` holds every argument it inspects
    to — there to tell a project id from a flag value, and here to refuse one — so the
    recipe that *records* an approval and the gate that *reads* one agree on what a
    project id is rather than each having its own idea.

    The source half is checked for shape and not for existence: whether this checkout
    configures a source of that name is the store's answer to give, and asking it costs a
    store read that this refusal exists to happen before.
    """
    if QUALIFIED.fullmatch(project):
        return project
    raise ValueError(
        f"{project!r} is not a qualified project id, so it names a project in no store "
        f"and nothing was read; pass the `<source>:<project>` id you would hand `just "
        f"check-plan`, whose source half is a source this checkout configures"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Record the user's approval of one plan's design document, from `just approve-design`."""
    parser = argparse.ArgumentParser(
        prog="approve-design",
        description="Record the user's approval of one plan project's design document.",
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    args = parser.parse_args(argv)
    try:
        answered = approve(qualified(args.project))
    except (OSError, ValueError) as exc:
        print(f"approve-design: {exc}", file=sys.stderr)
        return 1
    if answered.held:
        print(
            f"approve-design: {answered.document} already carries an approval for what it "
            f"currently says ({answered.location}); nothing was recorded"
        )
        return 0
    print(
        f"approve-design: recorded the approval of {answered.document} ({answered.location}); "
        f"editing it, or moving {TEMPLATE}, leaves it unapproved again"
    )
    return 0


def configured_sources() -> frozenset[str]:
    """Every source name this checkout's plan-store configuration names."""
    settings = plan_store.sdk(plan_store.client().config_show()).settings
    named: set[str] = set()
    for setting in settings:
        parts = setting.key.model_dump().split(".")
        if len(parts) > 1 and parts[0] == "sources":
            named.add(parts[1])
    return frozenset(named)


def gate_main(argv: Sequence[str] | None = None) -> int:
    """Refuse a launch whose plan carries no approved design document.

    Given a launch's own arguments, **as they were typed**. This deliberately does not
    parse them: restating `onepipeline start`'s flag grammar here would be a second
    implementation of somebody else's surface, and this repository has already paid for
    one of those. What it does instead is ask the store about every argument shaped like a
    qualified id whose source this checkout configures — so a project the launch names is
    checked wherever it sits on the command line, and a flag value that is not one is not
    a project any source answers for.

    A candidate whose project cannot be read is refused rather than skipped, and that
    costs nothing a launch was going to keep: the engine reads the same project through
    the same store, so a read that fails here fails there too, and refusing early is the
    difference between a named reason and a launch that got half-way.
    """
    try:
        sources = configured_sources()
    except OSError as exc:
        print(
            f"launch-gate: this checkout's plan store could not be read ({exc}), so whether "
            f"the plan being launched has an approved design document is unknown; run `just "
            f"bootstrap` from the repository root and launch again",
            file=sys.stderr,
        )
        return 2
    refused: list[str] = []
    exempted: list[str] = []
    for argument in argv if argv is not None else sys.argv[1:]:
        if argument.startswith("-") or not QUALIFIED.fullmatch(argument):
            continue
        if argument.split(":", 1)[0] not in sources:
            continue
        try:
            assessed = assess(argument)
        except OSError as exc:
            refused.append(
                f"{argument} could not be read out of the plan store ({exc}), so whether a "
                f"person has approved its design document is unknown"
            )
            continue
        if assessed.refusal is not None:
            refused.append(assessed.refusal)
        elif assessed.exemption is not None:
            exempted.append(assessed.exemption)
    # Said rather than passed over, and said even when something else is refused: a launch
    # that dispatched on the exemption is the one case where nobody has approved anything,
    # and it read exactly like an approved plan for as long as this was silent.
    for reason in exempted:
        print(f"launch-gate: {reason}", file=sys.stderr)
    for reason in refused:
        print(f"launch-gate: {reason}", file=sys.stderr)
    if refused:
        print(
            "launch-gate: nothing was dispatched. A plan is launched once the user has "
            "approved the design document it is read as, which is what a person can judge "
            "and what a plan is put in front of them as",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
