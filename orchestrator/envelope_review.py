"""Hold every live edit that changes what a node's judge reads to the criteria bar.

This is the command validator `config/onemessagebus.yaml` declares on the planner
channel's `replies` queue for every envelope carrying commands. The bus runs it before
anything is appended — for `just channel-reply` and for the engine alike — with the
offered envelope as one line of JSON on stdin, and reads its answer by exit status alone:
``0`` passes the envelope, ``1`` refuses it with this module's stderr as the reason, and
anything else leaves it unjudged, which the bus never sends either. Only a pass is cached,
by the bus, keyed on the envelope's bytes and on what ``--bar-fingerprint`` prints — which
is :func:`bar_in_force`, so moving either tier of the bar invalidates every recorded pass.
That is the one place this bar can be asked: a live edit reaches a node through the
channel rather than through the plan store, so `just check-plan` — which reads a plan —
never sees one, and `just review-plan` never records one.

**The bar is the one a plan is held to, in both of its tiers, and none of it is restated
here.** A plan's task clears `just check-plan` and then `just review-plan`; a live edit's
task clears the same two, in the same order, before the envelope is sent. The
deterministic tier is :mod:`orchestrator.criteria_guard`'s —
:func:`~orchestrator.criteria_guard.check_whole_task` for a whole task and
:func:`~orchestrator.criteria_guard.check_amendment` for a correction alone, whose
docstrings are where the choice of which questions apply to each lives. The judged tier
is :mod:`orchestrator.plan_review`'s: one turn of the same reviewer, under the same
prompt and bar, framed by :data:`~orchestrator.plan_review.LIVE_EDIT_FRAME` for what a
live edit is rather than a plan task. That second tier is what the deterministic one
deliberately stopped asking — whether a number is the right number, whether the criteria
answer a demand their own bar makes, whether a criterion could be falsified — so a live
edit read by the first tier alone was a task whose judge had been handed exactly the
rules nobody checked. This module is the envelope reader in front of both.

**The envelope is judged as it states itself, and no run state is read.** The bus caches a
pass on the envelope's bytes, so a judgement that also depended on the run — its journal,
its checkpoint, its launch plan — would make that cache unsound: the same bytes would pass
or fail with the run's state, and a recorded pass would stand over a run that has since
moved. So an `amend` is read as the correction it is, a `requeue`'s overrides as they are
stated, and an `add` or a `retry` as the whole node it states. What that gives up is the
composition this module once did onto a node's current task, and with it the structural
refusal of a `requeue` folding fields onto a parked node's own `repo` and `deps`; the
engine and the merge path still refuse such a node, and they are the layer that owns it.

**Which resulting tasks owe the judged turn is decided by what the content is, not by
which op carried it**; :attr:`Reviewable.judged` states that rule and why.

**A node an envelope states is held to the plan tier's structural rules as well as its
prose.** What an `add` states, and a `retry`'s replacement, is a node like any a plan
carries — its `adoption`, `consumes`, `merge_policy`, `repo`, `deps` and `title` decide
where it publishes and what it waits on — so it is held to
:data:`orchestrator.structural_guard.GUARDS`, the one list `just check-plan` reads, over
the graph the envelope's own nodes form. It is asked first: it spends no provider turn,
so an envelope it refuses spends none either.

**Four ops state task prose and a fifth may carry one criterion — which is the engine's
own answer rather than this module's.** The live-edit table in `docs/orchestration.md`
states what each op carries: `add` and `retry` each state a **full node mapping**,
`requeue` states **partial node overrides** that may include one, and `amend` states the
binding correction composed onto a node's effective task. A `note` is the fifth and the
one that is two things at once: its `text` is observational prose that touches no
acceptance criterion — holding it to a criteria bar would refuse exactly the corrections a
manager most needs to send — while its optional `criterion` *enters the acceptance
criteria the judge of the conversation it reaches decides against*, which is the whole
reason the weaker op it replaced was removed. So a note's criterion is read exactly as a
correction is and its text is never read. Every other op — `drop`, `reparent`, `cancel`,
`attest`, `complete`, `settle`, `finding` — carries no task prose, so there is nothing
here for it to be judged on.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import NamedTuple, Protocol

from orchestrator import plan_review, structural_guard
from orchestrator.criteria_guard import (
    CriteriaError,
    check_amendment,
    check_whole_task,
    criteria_fingerprint,
    dispatched_nodes,
    resolve_bar,
)

#: The op whose text becomes part of a node's effective task as a correction to its
#: criteria rather than as a task of its own.
AMEND_OP = "amend"

#: The op whose `criterion` — and only that field — is criteria: it enters the acceptance
#: criteria of the conversation the note reaches, so it is read the way an amendment is.
#: Its `text` is the escape every refusal here offers, and is never read.
NOTE_OP = "note"

#: What a refusal of a note's criterion ends with. The bar's own refusals send an author
#: to a `note`, which is where this text already is; the escape it needs is the note's
#: other field.
NOTE_ESCAPE = (
    "This is the note's `criterion`, which binds the judge of the conversation it reaches; "
    "an observation belongs in the note's `text`, which no judge reads as criteria."
)

#: How the engine renders a node's amendment into the task its dispatch receives —
#: `onepipeline`'s `src/plan.rs`, whose `amended` puts this heading, this sentence and
#: the amendment immediately above the operational notes, or at the end of a task that
#: states none. Restated here because a node an `add` or a `retry` states may carry an
#: amendment of its own, and that is text its judge reads;
#: `tests/test_engine_contracts.py` holds the three strings and the placement to the
#: engine's source at the pinned release.
AMENDMENT_HEADING = "## Amendment"
AMENDMENT_PRECEDENCE = (
    "Where this section and the operational notes below disagree, this section wins."
)
ADDITIONAL_INFO_HEADING = "## Additional info"

#: The field a node carries its binding amendment in, in an `add` or `retry` that states
#: one outright.
AMENDMENT = "amendment"

#: The three ops that state a whole task, and how each names the text it states in the
#: refusal a manager reads. A mapping rather than a tuple because the phrase is the
#: difference between a refusal a manager can act on and one they have to locate first:
#: `add` names a node the graph does not have yet, `retry` names its replacement, and
#: `requeue` names the parked node it is amending.
WHOLE_TASK_OPS = {
    "add": "the task added as node",
    "retry": "the replacement task for node",
    "requeue": "the amended task for node",
}

#: Where each of those states the mapping carrying that task. `add` and `retry` state a
#: full node; `requeue` states partial overrides of one, which is a different field and
#: the same shape.
STATED_IN = {"add": "node", "retry": "node", "requeue": "amend"}

#: The field by which a node, or a step of one, declares that it dispatches nobody: it
#: settles `done (no-changes)` without a worker, so no judge ever reads its task. The
#: engine refuses the field beside a `persona`, which is why an absent persona is not
#: the test — a node stating neither is refused by the engine for want of a persona,
#: and a node stating this one is a journal bookmark: a node on the run's own record that
#: nobody works. A follow-up is not recorded this way; `just follow-up` drafts one.
NO_DISPATCH = "expects_no_diff"

#: The exit status the bus reads as a refusal, with this module's stderr as its reason.
REFUSED = 1

#: The exit status the bus reads as *unjudged*: the judged turn a resulting task needed
#: could not answer — no provider took it, or none answered with a verdict — so nothing
#: about the text is known and nothing is sent. Any status but ``0`` and ``1`` is unjudged
#: to the bus; this is the one this module chooses, so a refusal and a turn that answered
#: nothing are never the same word to a manager: a refusal is corrected, this is re-sent
#: once the harness answers.
UNJUDGED = 2

#: The one argument this module takes: print the bar's fingerprint and exit, which is what
#: the bus's pass cache is keyed on beside the envelope's bytes.
BAR_FINGERPRINT_FLAG = "--bar-fingerprint"

#: Every field the pinned engine's ``Node`` accepts. Commands carrying anything else
#: are left to the engine's authoritative decoder instead of letting a node that can
#: never be committed influence this preflight. ``tests/test_engine_contracts.py``
#: reconciles this set with the release source.
NODE_FIELDS = frozenset(
    {
        "id",
        "kind",
        "task",
        "persona",
        "deps",
        "max_turns",
        "expects_no_diff",
        "context",
        "amendment",
        "parked",
        "executor",
        "agent_graph",
        "repo",
        "repo_type",
        "workflow",
        "merge_policy",
        "base_branch",
        "branch",
        "title",
        "body",
        "draft",
        "execution_checkout",
        "steps",
        "resume",
        "adoption",
        "consumes",
        "delivers",
        "pool",
        "overflow",
    }
)


class Judge(Protocol):
    """What spends the judged turn on one rendered prompt and answers with a verdict.

    A parameter of :func:`refusal` rather than a fixed call, so the one boundary a
    verdict crosses into this module is the one a test stands in at; :func:`main` passes
    the real one, which is :func:`orchestrator.plan_review.verdict`. Structural, so a
    scripted judge is one by having this call shape and nothing has to inherit anything.
    """

    def __call__(self, prompt: str) -> plan_review.Verdict:
        """Answer ``prompt`` with a verdict, or raise :class:`OSError` when none came back."""


class ReviewUnanswered(OSError):
    """The judged turn a resulting task needed answered with no verdict.

    Raised out of :func:`refusal` rather than returned as a reason, because it is not a
    verdict about the text: a refusal says the text is wrong, and this says nothing is
    known about it. The message names the text and why no verdict came back, and
    :func:`main` reports it under :data:`UNJUDGED` with its own repair.
    """


# llmlint: ignore[names_match_behavior, changed_behavior_has_e2e] The bar in force is
# the two shared tiers this digests, and a persona's own review contract is deliberately
# outside it: a shipped role's contract is compiled into the engine, and a path persona's
# is a file this fingerprint would have to discover per envelope, which a bar fingerprint
# the bus runs once per judgement cannot do. The docstring states the exclusion so a
# reader is not left to infer it from the name, and the tests move the two fingerprints
# that do invalidate.
def bar_in_force() -> plan_review.BarFingerprint:
    """A digest of both tiers of the bar a live edit is asked, in this checkout.

    Both, because both run: :func:`~orchestrator.criteria_guard.criteria_fingerprint`
    covers the deterministic bar and :func:`~orchestrator.plan_review.edit_bar_fingerprint`
    the judged one — the plan reviewer's prompt and bar files plus the frame a live edit
    is shown. A fingerprint over only one would let a cached pass stand after the other
    moved, which is a pass over a question nobody asked.

    What it deliberately does not digest is the review contract of the **persona** a whole
    task names, which :func:`~orchestrator.criteria_guard.resolve_bar` reads — a shipped
    role's is compiled into the engine and a path's is a file under `personas/`. The
    persona is in the envelope's bytes by *name*, which is the same coverage a plan task's
    record has of it.
    """
    digest = hashlib.sha256()
    digest.update(criteria_fingerprint().encode("utf-8"))
    digest.update(b"\0")
    digest.update(plan_review.edit_bar_fingerprint().encode("utf-8"))
    return plan_review.BarFingerprint(digest.hexdigest())


class Reviewable(NamedTuple):
    """One resulting task an envelope puts in front of a dispatch, and how to name it."""

    #: The text as the dispatch would meet it: a whole task, or an amendment alone.
    text: str
    #: The persona whose bar it will be judged under, where the op states one. `None`
    #: leaves the base config's generic contract, which is what a partial override that
    #: does not restate the persona resolves to here.
    persona: str | None
    #: Whether :attr:`text` is a whole task. A correction alone — a note's criterion, or
    #: an amendment — is criteria and nothing else, so it has no acceptance-criteria
    #: block of its own to find and no sections to walk.
    whole_task: bool
    #: How a refusal names it, which is what a manager acts on.
    where: str
    #: Whether this text owes a judged turn once the free tier takes it. A **novel whole
    #: task** — an added node, a retry's replacement, a requeued node's amended task —
    #: does: it is a complete authored task nothing holds a pass for, and it would have
    #: been reviewed had it arrived in the plan. A correction — an `amend`, or a note's
    #: criterion — does not: it is written in the minute after a manager reads a failure
    #: and has to be answered before the envelope is sent, which is the same reasoning
    #: that exempts an amendment from the released-artifact refusal. The line is drawn at
    #: what the content *is*, not at which op carried it.
    judged: bool = True
    #: One sentence a refusal of this text ends with, where the escape the bar's own
    #: refusal names is not the right one.
    escape: str | None = None


def amended(task: str, amendment: str | None) -> str:
    """``task`` with ``amendment`` rendered into it exactly as the engine renders it.

    Above the operational notes, never after them: a task that states
    :data:`ADDITIONAL_INFO_HEADING` on a line of its own gets the block immediately
    before that line, and one that states none gets it at the end. A blank amendment
    renders nothing, which is what the engine does with one. Matched as a whole line, so
    prose that mentions the heading is not mistaken for the section itself.
    """
    if amendment is None or not amendment.strip():
        return task
    block = f"{AMENDMENT_HEADING}\n{AMENDMENT_PRECEDENCE}\n\n{amendment.strip()}\n"
    at = 0
    for line in task.splitlines(keepends=True):
        if line.rstrip() == ADDITIONAL_INFO_HEADING:
            return f"{task[:at].rstrip()}\n\n{block}\n{task[at:]}"
        at += len(line)
    return f"{task.rstrip()}\n\n{block}"


def _dispatching(stated: Mapping[str, object]) -> dict[str, object] | None:
    """``stated`` with every part of it nothing dispatches from left out, or ``None``.

    A node declaring :data:`NO_DISPATCH` settles without a worker, so its task is prose
    no judge reads and there is nothing here to hold it to — and reading it anyway
    refused a journal bookmark a manager adds mid-run, `{"task": "Report.",
    "expects_no_diff": true}`, for carrying no acceptance criteria. (A follow-up itself
    is drafted with `just follow-up`, not added as a node.) A step of
    a lifecycle node may declare the same thing of itself, so the steps are pruned
    rather than the node: the steps beside it still dispatch and are still read.
    """
    if stated.get(NO_DISPATCH) is True:
        return None
    read = dict(stated)
    steps = read.get("steps")
    if isinstance(steps, list):
        read["steps"] = [
            step
            for step in steps
            if not (isinstance(step, Mapping) and step.get(NO_DISPATCH) is True)
        ]
    return read


def _effective(node: Mapping[str, object], where: str) -> Iterator[Reviewable]:
    """Every task ``node``'s dispatch would read, composed as the engine composes it.

    The node is walked by :func:`~orchestrator.criteria_guard.dispatched_nodes`, which is
    the same reader `just check-plan` walks a plan with — so a stepped node yields one
    reviewable per step, a `kind: human` node yields none, and the id in a refusal is
    spelled the way that command's refusal would spell it. Each task then has the node's
    amendment rendered into it, because that is the text the judge reads: an amendment
    belongs to the node and renders into every agent step of it. A shape that reader
    refuses is passed over rather than reported: those are the shapes the engine refuses
    on its own, naming what it actually received, and a second opinion here would replace
    that with a guess.
    """
    read = _dispatching(node)
    if read is None:
        return
    amendment = read.get(AMENDMENT)
    try:
        nodes = list(dispatched_nodes({"tasks": [read]}))
    except CriteriaError:
        return
    for dispatched in nodes:
        text = amended(dispatched.task, amendment if isinstance(amendment, str) else None)
        yield Reviewable(text, dispatched.persona, True, f"{where} {dispatched.id!r}")


def _stated_nodes(command: Mapping[str, object], op: str) -> Iterator[Reviewable]:
    """Every whole task ``command`` states, read as it is stated.

    An `add` and a `retry` state a full node, so what they result in is that node with
    whatever amendment it states. A `requeue` states overrides of a parked node, read
    here as the overrides alone — the parked node's own persona, amendment and steps are
    the run's, and this module reads no run — so an override stating a task is judged
    under the persona it states, or the generic contract where it states none.
    """
    stated = command.get(STATED_IN[op])
    if not isinstance(stated, Mapping):
        return
    named = command.get("id")
    read = dict(stated)
    if "id" not in read and isinstance(named, str) and named:
        # A `requeue`'s overrides carry no `id` — the engine refuses one that rewrites it
        # — so the parked node's own id is what names its amended task in a refusal.
        read["id"] = named
    yield from _effective(read, WHOLE_TASK_OPS[op])


def _amendment(text: str, named: object, position: int) -> Reviewable:
    """What an `amend` puts in front of a node's judge, read as the correction it is.

    Owes no judged turn, for the reason :attr:`Reviewable.judged` gives. Named by its node,
    because that is what a manager is holding in mind when they write one; by its position
    when the op names none, which is a shape the engine refuses on its own and which must
    still not produce a refusal naming `None`.
    """
    where = (
        f"the amendment for node {named!r}"
        if isinstance(named, str) and named
        else f"the amendment in commands[{position}]"
    )
    return Reviewable(text, None, False, where, False)


def reviewables(envelope: object) -> list[Reviewable]:
    """Every resulting task ``envelope`` carries, in the order it states them.

    Lenient about every shape but the ones it acts on, for the reason :func:`_effective`
    gives: a reply this cannot read is one the engine refuses with a message about the
    envelope it actually got.
    """
    match envelope:
        case {"commands": [*commands]}:
            pass
        case _:
            return []
    found: list[Reviewable] = []
    for position, command in enumerate(commands):
        match command:
            # Guards on the op rather than value patterns, because a bare name in a
            # pattern binds. Blank text is left to the engine, which refuses it itself.
            case {"op": op, "text": str() as text} if op == AMEND_OP and text.strip():
                found.append(_amendment(text, command.get("id"), position))
            # A note's criterion and never its text: the criterion binds the judge of the
            # conversation the note reaches, the text is what a manager sends when the
            # judge should have no opinion.
            case {"op": op, "criterion": str() as criterion, "id": str() as named} if (
                op == NOTE_OP and criterion.strip() and named
            ):
                found.append(
                    Reviewable(
                        criterion,
                        None,
                        False,
                        f"the criterion of the note for node {named!r}",
                        False,
                        NOTE_ESCAPE,
                    )
                )
            case {"op": op, "criterion": str() as criterion} if op == NOTE_OP and criterion.strip():
                found.append(
                    Reviewable(
                        criterion,
                        None,
                        False,
                        f"the criterion of the note in commands[{position}]",
                        False,
                        NOTE_ESCAPE,
                    )
                )
            case {"op": str() as op} if op in WHOLE_TASK_OPS:
                found.extend(_stated_nodes(command, op))
            case _:
                continue
    return found


def _refused(reviewable: Reviewable) -> str | None:
    """Why the deterministic bar refuses ``reviewable``, or ``None`` when it has nothing to say.

    A whole task is asked the whole bar over its criteria and
    :func:`~orchestrator.criteria_guard.check_amendment`'s two questions over every other
    section it opens — the `## Amendment` block a stated node's task carries among them; a
    correction alone is asked those two questions and nothing else. Which is which is
    :mod:`orchestrator.criteria_guard`'s decision, written down beside the questions.
    Asked before the judged turn because it is free and its refusals are the commonest —
    a text it refuses spends no provider turn at all.
    """
    try:
        if reviewable.whole_task:
            check_whole_task(reviewable.text, reviewable.where, resolve_bar(reviewable.persona))
        else:
            check_amendment(reviewable.text, reviewable.where)
    except CriteriaError as exc:
        return f"{exc} {reviewable.escape}" if reviewable.escape else str(exc)
    return None


def _judged(reviewable: Reviewable, judge: Judge) -> str | None:
    """Why the judged review refuses ``reviewable``, or ``None`` when it passes.

    One turn, under :func:`~orchestrator.plan_review.edit_prompt`, which frames the task
    as one a live edit stated rather than one a plan carried. Every finding the
    verdict named is reported, one line each, naming the text the way the refusal names
    it: a reviewer that could see three defects and report one cost this host eleven
    serialised rounds over two plans, and a manager correcting a live edit is under more
    pressure than a plan's author, not less.

    A turn that answered with no verdict is raised as :class:`ReviewUnanswered` rather
    than read either way — nothing is known about the text, so nothing is passed and
    nothing is sent — with the harness's own account of why.
    """
    prompt = plan_review.edit_prompt(reviewable.text, reviewable.persona, reviewable.where)
    try:
        answered = judge(prompt)
    except OSError as exc:
        raise ReviewUnanswered(
            f"{reviewable.where} could not be reviewed, so nothing is known about it: {exc}"
        ) from exc
    if answered["passes"]:
        return None
    findings = "\n".join(
        f"{reviewable.where}: {finding['criterion']} — {finding['why']}"
        for finding in answered["findings"]
    )
    return (
        f"{reviewable.where} was refused by its judged review, which named "
        f"{len(answered['findings'])} criterion(s):\n{findings}"
    )


def refusal(envelope: object, judge: Judge = plan_review.verdict) -> str | None:
    """Why this envelope's first refused resulting task is refused, or ``None`` for none.

    **Two tiers over each resulting task, in cost order.** The deterministic bar first,
    because it is free and refuses the commonest shapes without a provider turn; then, for
    a resulting task that owes one, a judged turn under ``judge`` — which is what asks the
    questions that moved out of the deterministic tier (whether a number is the right
    number, whether the criteria answer a demand their own bar makes, whether they could
    be falsified) of the one route to a task that `just review-plan` never reads. Which
    resulting tasks owe one is :attr:`Reviewable.judged`'s to say.

    The first rather than all of them: the bus refuses the whole envelope, so nothing is
    sent either way, and a manager correcting one command re-sends the envelope and is
    told about the next. Raises :class:`ReviewUnanswered` when a judged turn answered
    nothing.
    """
    for reviewable in reviewables(envelope):
        reason = _refused(reviewable)
        if reason is None and reviewable.judged:
            reason = _judged(reviewable, judge)
        if reason is not None:
            return reason
    return None


def _consumes_keeping(node: dict[str, object], keep: Callable[[str], str | None]) -> None:
    """Rewrite ``node``'s `consumes` keys through ``keep``, dropping a key it answers `None` for.

    Assigned rather than mutated, so a mapping the envelope's own command still holds is
    never changed underneath it; a node carrying no `consumes` mapping is left as it is.
    """
    consumes = node.get("consumes")
    if not isinstance(consumes, Mapping):
        return
    kept: dict[str, object] = {}
    for key, value in consumes.items():
        renamed = keep(key)
        if renamed is not None:
            kept[renamed] = value
    node["consumes"] = kept


def _renaming(old: str, new: str) -> Callable[[str], str | None]:
    """A `consumes` key rewrite moving the entry keyed on ``old`` onto ``new``."""
    return lambda key: new if key == old else key


def _keeping_only(kept: Sequence[str]) -> Callable[[str], str | None]:
    """A `consumes` key rewrite keeping only the entries keyed on one of ``kept``."""
    return lambda key: key if key in kept else None


def _without(graph: dict[str, dict[str, object]], dropped: str, *, cascade: bool) -> None:
    """Remove ``dropped`` from ``graph``, and its dependents with it or its edges to them.

    A `drop`'s two dispositions, as the engine's `compile_drop` applies them: `drop`
    removes every node depending on it, recursively, and `detach` removes only the edge
    each one held. Either way no remaining node consumes a node that has left, which is
    the engine's `NodeDropped` fold.
    """
    graph.pop(dropped, None)
    for node_id, node in list(graph.items()):
        deps = node.get("deps")
        if isinstance(deps, list) and dropped in deps and cascade:
            _without(graph, node_id, cascade=True)
            continue
        if isinstance(deps, list) and dropped in deps:
            node["deps"] = [one for one in deps if one != dropped]
        _consumes_keeping(node, lambda key: None if key == dropped else key)


# llmlint: ignore[modern_domain_modeling] The graph is the envelope's own open node
# records, handed straight to the structural guards as the plan document they read
# leniently. A typed node model here would be a second declaration of the engine's schema,
# and would drop the fields the guards read that such a model did not name (`metadata`,
# `consumes`, whatever a later release adds).
def stated_graph(envelope: object) -> tuple[dict[str, dict[str, object]], list[str]]:
    """The graph ``envelope``'s own nodes form, and the ids of the nodes it states.

    Every command is folded in order onto what the envelope has stated so far, so a later
    command reads what an earlier one did, and each is folded as the engine's `edits.rs`
    compiles and applies it, `consumes` included — because the adoption rules read it: an
    `add` puts its node in; a `retry` puts its replacement in — in place of the node it
    names when an earlier command of this envelope added that node, the replacement then
    inheriting its `deps` and `consumes` when it states no deps of its own, its `delivers`
    on the separate condition of stating none of those, and taking over its dependents,
    their `consumes` rekeyed with them; a `reparent` replaces a node's
    `deps` and keeps only the `consumes` keyed on one of them; and a `drop` removes a node
    as its `dependents` disposition says. `tests/test_engine_contracts.py` holds each of
    those folds to the engine's source at the pinned release. A `requeue` states overrides
    rather than a node, so it moves nothing here, and a command naming a node the envelope
    has not stated moves nothing either: the run's nodes are not read.
    """
    graph: dict[str, dict[str, object]] = {}
    resulting: list[str] = []
    match envelope:
        case {"commands": [*commands]}:
            pass
        case _:
            return graph, resulting
    for command in commands:
        match command:
            # Guards on the op rather than value patterns, for the reason `reviewables`
            # gives: a bare name in a pattern binds.
            # An `add` of an id the graph holds, and a `retry` onto an id it holds, are
            # refused by the engine for their target, so each moves nothing here rather
            # than checking a node that will never be committed.
            case {"op": op, "node": Mapping() as node} if (
                op == "add"
                and set(command) == {"op", "node"}
                and set(node) <= NODE_FIELDS
                and isinstance(node.get("id"), str)
                and node["id"] not in graph
            ):
                graph[node["id"]] = {str(key): value for key, value in node.items()}
                resulting.append(node["id"])
            case {"op": op, "id": str() as old, "node": Mapping() as node} if (
                op == "retry"
                and set(command) == {"op", "id", "node"}
                and set(node) <= NODE_FIELDS
                and isinstance(node.get("id"), str)
                and node["id"] not in graph
            ):
                new = node["id"]
                replacement = {str(key): value for key, value in node.items()}
                superseded = graph.pop(old, None)
                if superseded is not None and not replacement.get("deps"):
                    inherited = superseded.get("deps")
                    replacement["deps"] = list(inherited) if isinstance(inherited, list) else []
                    if "consumes" in superseded:
                        replacement["consumes"] = superseded["consumes"]
                # Its own condition, as the engine's: a replacement restating its deps
                # and saying nothing about tickets still delivers what the superseded
                # node did, so the board item the lineage keeps stays their deliverer.
                # Inherited only in the shape the engine's `Node` gives the field — a
                # list of qualified ticket ids — as `deps` is above; anything else the
                # envelope stated there is the engine's to refuse, and is not carried.
                # llmlint: ignore[changed_behavior_has_e2e] No structural guard reads
                # `delivers`, so this fold changes no verdict an envelope sent over the
                # channel can receive; what it changes is the graph a guard reads, which
                # `tests/test_envelope_review.py` holds to the engine's own fold and
                # `tests/test_engine_contracts.py` holds the pinned engine to. A channel
                # journey would drive the same code to the same verdict either way.
                delivered = superseded.get("delivers") if superseded is not None else None
                # "Stating none" is the engine's `is_empty()`: the field absent, or `[]`.
                # A supplied value of any other shape is stated, and the engine's to judge.
                states_none = "delivers" not in replacement or replacement["delivers"] == []
                if (
                    states_none
                    and isinstance(delivered, list)
                    and all(isinstance(ticket, str) for ticket in delivered)
                ):
                    replacement["delivers"] = list(delivered)
                for other in graph.values():
                    deps = other.get("deps")
                    if isinstance(deps, list) and old in deps:
                        other["deps"] = [new if one == old else one for one in deps]
                        _consumes_keeping(other, _renaming(old, new))
                graph[new] = replacement
                resulting.append(new)
            case {"op": op, "id": str() as named, "deps": [*deps]} if (
                op == "reparent"
                and set(command) == {"op", "id", "deps"}
                and all(isinstance(one, str) for one in deps)
                and named in graph
            ):
                kept = list(deps)
                graph[named]["deps"] = kept
                _consumes_keeping(graph[named], _keeping_only(kept))
            case {"op": op, "id": str() as named, "dependents": disposition} if (
                op == "drop"
                and set(command) == {"op", "id", "dependents"}
                and isinstance(disposition, str)
                and disposition in {"drop", "detach"}
                and named in graph
            ):
                _without(graph, named, cascade=command.get("dependents") == "drop")
            case _:
                continue
    return graph, [node_id for node_id in dict.fromkeys(resulting) if node_id in graph]


def structural_refusal(envelope: object) -> str | None:
    """Every structural refusal of a node ``envelope`` states, or ``None`` for none.

    The rules are :data:`orchestrator.structural_guard.GUARDS`, the list `just check-plan`
    reads, asked over the stated nodes and the dependencies among them the envelope also
    states — which is what lets a rule about a dependency's repository resolve it as it
    would in a plan — and only a refusal *of a stated node* is reported. Every one rather
    than the first, for the reason :func:`orchestrator.publication_guard.refusals` gives,
    and each in its rule's own words, naming the node and the field to correct.

    An envelope stating no node asks nothing, so a note, an amendment or a cancellation
    spends no `onevcs` call.
    """
    graph, resulting = stated_graph(envelope)
    if not resulting:
        return None
    asked = dict.fromkeys(resulting)
    for node_id in resulting:
        deps = graph[node_id].get("deps")
        asked.update(
            dict.fromkeys(one for one in (deps if isinstance(deps, list) else []) if one in graph)
        )
    refused = [
        found
        for found in structural_guard.refusals({"tasks": [graph[node_id] for node_id in asked]})
        if found.node in resulting
    ]
    if not refused:
        return None
    return "\n".join(
        f"node {found.node!r}, as this reply states it: {found.field}: {found.reason}"
        for found in refused
    )


def _said(reason: str, repair: str) -> None:
    """Write one verdict's reason and its repair to stderr, where the bus reads a reason."""
    print(f"{reason}\n{repair}", file=sys.stderr)


def main(argv: Sequence[str] | None = None, judge: Judge = plan_review.verdict) -> int:
    """Judge one reply envelope on stdin, or print the bar's fingerprint.

    ``judge`` is the one judged turn, real by default; a test hands over a scripted one
    at this boundary rather than standing the harness in below it.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == [BAR_FINGERPRINT_FLAG]:
        print(bar_in_force())
        return 0
    if arguments:
        _said(
            f"envelope-review: {arguments!r} is not what this validator takes",
            f"it reads one envelope on stdin, or takes {BAR_FINGERPRINT_FLAG} alone",
        )
        return UNJUDGED
    try:
        # llmlint: ignore[boundary_inputs_validated] The envelope is the channel's open
        # contract and the bus has already parsed it as a JSON object before any validator
        # runs; every field this reads is narrowed where it is read in `reviewables` and
        # `stated_graph`, and a shape neither reads moves nothing.
        envelope = json.load(sys.stdin)
    except ValueError as unreadable:
        _said(
            f"the envelope handed to this validator is not JSON ({unreadable})",
            "nothing is known about it, so nothing was sent; send the envelope as one JSON object",
        )
        return UNJUDGED
    structural = structural_refusal(envelope)
    if structural is not None:
        _said(
            f"this reply states a node this host's structural plan rules refuse — {structural}",
            "nothing was sent, so no other command in it was applied either; correct the "
            "field each refusal names and send the whole envelope again",
        )
        return REFUSED
    try:
        reason = refusal(envelope, judge)
    except ReviewUnanswered as unanswered:
        _said(
            f"the task prose in this reply could not be judged: {unanswered}",
            "nothing was sent; once the harness answers — 'oneharness doctor' says why it did "
            "not — send the whole envelope again unchanged",
        )
        return UNJUDGED
    if reason is None:
        return 0
    _said(
        f"this reply states task prose a judge would hold its worker to as work — {reason}",
        "nothing was sent, so no other command in it was applied either; correct it and "
        "send the whole envelope again",
    )
    return REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
