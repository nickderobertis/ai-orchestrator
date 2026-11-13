"""Hold every live edit that changes what a node's judge reads to the criteria bar.

This is the engine's **envelope reviewer** on this host: `config/onepipeline.launch.yaml`
names `scripts/envelope-review.sh` as `envelope_reviewer`, so every reply envelope carrying
edits — sent by `just channel-reply` or by any other author — is offered here once, after
the engine's own validation and before any of its commands is applied. One document crosses
stdin, the one onepipeline's `docs/contract-divergences.md` (entry 45) states: the run's
`goal`, every node the envelope introduces or changes as a `changes` list carrying the
`op` that produced each — the node as the whole envelope leaves it — and the `plan` they are
being edited into. The engine runs it as a message hook (onemessagebus's `hooks.md`): a
pass **prints the document back byte for byte** and exits ``0``, which the engine reads as a
keep; ``1`` refuses the envelope whole with this module's stderr as the reason, naming the
node it objects to on an ``objection:`` line; and anything else — a pass printing nothing or
something else included, which would be a drop or a change of a document the reviewer does
not own — leaves it unjudged, which the engine never applies either. Only a pass is
recorded, by the engine, under the run's own `validator-passes/`, keyed on the document
and on what `envelope_reviewer_bar` — `scripts/envelope-review-bar.sh`, which prints
``--bar-fingerprint``, that is :func:`bar_in_force` — prints, so moving either tier of the
bar runs the review again.
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

**The document is judged as the engine states it, and nothing else is read.** The engine
keys a recorded pass on the document's bytes, so a judgement that also read the run — its
journal, its checkpoint — would make that record unsound. So an `amend` is read as the
correction its node now carries, and an `add`, a `retry` or a `requeue` as the whole node
the envelope leaves; a `reparent` changes edges and no prose, so nothing here asks
anything of it — the plan it leaves is the engine's to refuse.

**Which resulting tasks owe the judged turn is decided by what the content is, not by
which op carried it**; :attr:`Reviewable.judged` states that rule and why.

**A node an envelope states is held to the plan tier's structural rules as well as its
prose.** What an `add` states, and a `retry`'s replacement, is a node like any a plan
carries — its `adoption`, `consumes`, `merge_policy`, `repo`, `deps` and `title` decide
where it publishes and what it waits on — so it is held to
:data:`orchestrator.structural_guard.GUARDS`, the one list `just check-plan` reads, over
the plan the document carries. It is asked first: it spends no provider turn, so an
envelope it refuses spends none either.

**Which ops put prose in front of a judge is the engine's answer rather than this
module's.** `add`, `retry` and a `requeue` carrying an amendment each leave a whole node,
and `amend` leaves a node whose binding correction is composed onto its task. A `note`'s
optional `criterion` also enters the acceptance criteria the judge of the conversation it
reaches decides against, but the pinned engine does not offer a note to its reviewer, so a
note's criterion is held to no bar until the engine offers it. Every other op — `drop`,
`cancel`, `attest`, `complete`, `settle`, `finding` — carries no task prose and is not in
the document's `changes`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Iterator, Mapping, Sequence
from typing import Literal, NamedTuple, NewType, Protocol

from orchestrator import plan_review, structural_guard
from orchestrator.criteria_guard import (
    AMENDMENT_HEADING,
    CRITERIA_HEADING,
    CriteriaError,
    check_amendment,
    check_whole_task,
    criteria_fingerprint,
    dispatched_nodes,
    resolve_bar,
)
from orchestrator.publication_guard import Refusal

#: The op whose text becomes part of a node's effective task as a correction to its
#: criteria rather than as a task of its own.
AMEND_OP = "amend"

#: How the engine renders a node's amendment into the task its dispatch receives —
#: `onepipeline`'s `src/plan.rs`, whose `amended` puts the amendment's clauses at the end
#: of the task's `## Acceptance criteria`, under :data:`AMENDMENT_HEADING` and opening with
#: this sentence, and gives a task stating no criteria section one — immediately above the
#: operational notes, or at the end of a task that states none. Restated here because a
#: node an `add` or a `retry` states may carry an amendment of its own, and that is text
#: its judge reads; `tests/test_engine_contracts.py` holds the strings, the clause shaping
#: and the placement to the engine's source at the pinned release. The heading itself is
#: :mod:`orchestrator.criteria_guard`'s, because that is the reader that has to find it.
AMENDMENT_PRECEDENCE = (
    "This node's manager amended its bar. Every clause below is an acceptance criterion "
    "of this node and takes precedence over the whole task: where a clause here "
    "contradicts a criterion above it, anything the task says elsewhere, or an "
    "operational note below, the clause here wins and the contradicted one no longer "
    "binds."
)
#: The same authority, naming the user: the sentence the engine opens an amendment with
#: when it is the user's words, relayed by the manager.
USER_AMENDMENT_PRECEDENCE = (
    "The user amended this node's bar. Every clause below is an acceptance criterion "
    "of this node and takes precedence over the whole task: where a clause here "
    "contradicts a criterion above it, anything the task says elsewhere, or an "
    "operational note below, the clause here wins and the contradicted one no longer "
    "binds."
)
ADDITIONAL_INFO_HEADING = "## Additional info"

#: The attribution the engine stores a ruling under — `onepipeline`'s `src/ruling.rs`,
#: whose `attribution` writes one of these three sentences and whose `origin_of` reads a
#: stored first line as one only where it is byte for byte one of them. A recorded
#: amendment carries it as its first line, and `amended` opens the block with it and picks
#: the precedence sentence by its party, so it is restated here for the same reason the
#: precedence is; `tests/test_engine_contracts.py` holds all three to the engine's source.
ATTRIBUTION_OPENING = "Ruling from the "
USER_ATTRIBUTION = (
    f"{ATTRIBUTION_OPENING}user, relayed by the manager: it binds. Follow it as written; "
    "a user's ruling cannot be declined."
)
UNREFERENCED_MANAGER_ATTRIBUTION = (
    f"{ATTRIBUTION_OPENING}manager: a strong default you may decline with your reason "
    "through `onepipeline decline-ruling`."
)


#: A ruling reference as the engine mints one — `c<envelope>.<index>` or `a<reply>` — told
#: apart from the prose it is read out of. Only `_minted` decides that a string is one.
RulingReference = NewType("RulingReference", str)


def manager_attribution(reference: RulingReference) -> str:
    """The attribution of a manager's ruling the engine names by ``reference``."""
    return (
        f"{ATTRIBUTION_OPENING}manager (ruling reference `{reference}`): a strong default "
        "you may decline with your reason. To decline it, run `onepipeline decline-ruling "
        f'"$ONEPIPELINE_RUN_ID" --ruling {reference}` with the reason on standard input; '
        "the manager is told, and may take it to the user."
    )


#: The widths the engine parses each part of a ruling reference into: `u64`, and `u32` for
#: a command's index, and the most significant digits a `u64` can have.
_U64, _U32 = 2**64, 2**32
_U64_DIGITS = len(str(_U64 - 1))


def _number(text: str, bound: int) -> int | None:
    """``text`` as the number the engine parses it into, where it reads it below ``bound``.

    Bounded before it is converted: the engine's parse accepts leading zeros, so they are
    dropped, and what is left is refused unread when it has more digits than a `u64` holds
    — a line carrying thousands of digits is ordinary amendment text, not a reference, and
    Python refuses to convert a run that long at all.
    """
    if not text or not all(digit in "0123456789" for digit in text):
        return None
    significant = text.lstrip("0")
    if len(significant) > _U64_DIGITS:
        return None
    number = int(significant or "0")
    return number if number < bound else None


def _minted(reference: str) -> RulingReference | None:
    """``reference`` as the engine's `Reference::from_str` reads it and its `Display` writes
    it back — command ``index`` of envelope ``envelope`` as ``c<envelope>.<index>``, or the
    reply that answered a question as ``a<reply>`` — or nothing where it reads none."""
    if reference.startswith("c"):
        envelope, dot, index = reference[1:].partition(".")
        command = (_number(envelope, _U64), _number(index, _U32))
        if not dot or command[0] is None or command[1] is None:
            return None
        return RulingReference(f"c{command[0]}.{command[1]}")
    reply = _number(reference[1:], _U64) if reference.startswith("a") else None
    return None if reply is None else RulingReference(f"a{reply}")


#: Whose words a ruling is, as the engine's `Provenance` spells the two parties.
Party = Literal["user", "manager"]


class Attribution(NamedTuple):
    """The attribution line a stored ruling opens with, and the party it names."""

    party: Party
    line: str


class Split(NamedTuple):
    """A stored ruling as the engine splits it: its attribution, where its first line is
    exactly one the engine writes, and the ruling's own text."""

    attribution: Attribution | None
    text: str


def _attribution_party(line: str) -> Party | None:
    """Whose ruling ``line`` attributes, where it is exactly an attribution the engine writes."""
    if line == USER_ATTRIBUTION:
        return "user"
    if line == UNREFERENCED_MANAGER_ATTRIBUTION:
        return "manager"
    opening = f"{ATTRIBUTION_OPENING}manager (ruling reference `"
    if not line.startswith(opening):
        return None
    # The engine parses the reference and compares the whole line with the attribution it
    # would write for what it parsed, so a reference it reads but would spell differently —
    # leading zeros — makes the line ordinary text, here as there.
    minted = _minted(line[len(opening) :].split("`", 1)[0])
    if minted is not None and line == manager_attribution(minted):
        return "manager"
    return None


def split_attribution(text: str) -> Split:
    """``text`` split as the engine's `split_attribution` splits a stored ruling: no
    attribution and ``text`` unchanged where its first line is not exactly one of the
    attributions above."""
    trimmed = text.lstrip()
    first, _, rest = trimmed.partition("\n")
    party = _attribution_party(first)
    if party is None:
        return Split(None, text)
    return Split(Attribution(party, first), rest)


#: The field a node carries its binding amendment in: the correction an `amend` set, or
#: one an `add` or `retry` states outright.
AMENDMENT = "amendment"

#: The ops whose node is read as a whole task, and how each names it in the refusal a
#: manager reads. A mapping rather than a tuple because the phrase is the difference
#: between a refusal a manager can act on and one they have to locate first: `add` names a
#: node the graph did not have, `retry` names its replacement, and `requeue` names the
#: parked node it amended.
WHOLE_TASK_OPS = {
    "add": "the task added as node",
    "retry": "the replacement task for node",
    "requeue": "the amended task for node",
}

#: The op whose node changed only edges, which the structural rules read and no judge does.
REPARENT_OP = "reparent"

#: Every op the engine lists in the document's `changes`, which is every op this module
#: reads; `tests/test_engine_contracts.py` holds it to the engine's declared list.
CHANGE_OPS = frozenset({*WHOLE_TASK_OPS, AMEND_OP, REPARENT_OP})

#: The ops whose node the structural rules are asked about: a node an `add` or a `retry`
#: states is one a plan could carry, and nothing else in the document is new.
STATED_OPS = frozenset({"add", "retry"})

#: The prefix of the stderr line the engine reads a declared objection from, as its
#: `docs/contract-divergences.md` entry 45 declares it.
OBJECTION_PREFIX = "objection:"

#: The field by which a node, or a step of one, declares that it dispatches nobody: it
#: settles `done (no-changes)` without a worker, so no judge ever reads its task. The
#: engine refuses the field beside a `persona`, which is why an absent persona is not
#: the test — a node stating neither is refused by the engine for want of a persona,
#: and a node stating this one is a journal bookmark: a node on the run's own record that
#: nobody works. A follow-up is not recorded this way; `just follow-up` drafts one.
NO_DISPATCH = "expects_no_diff"

#: The exit status the engine reads as a refusal, with this module's stderr as its reason.
REFUSED = 1

#: The exit status the engine reads as *unjudged*: the judged turn a resulting task needed
#: could not answer — no provider took it, or none answered with a verdict — so nothing
#: about the text is known and nothing is applied. Any status but ``0`` and ``1`` is
#: unjudged to the engine; this is the one this module chooses, so a refusal and a turn
#: that answered nothing are never the same word to a manager: a refusal is corrected,
#: this is re-sent once the harness answers.
UNJUDGED = 2

#: The one argument this module takes: print the bar's fingerprint and exit, which is what
#: the engine keys a recorded pass on beside the document's bytes.
BAR_FINGERPRINT_FLAG = "--bar-fingerprint"


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
# the engine runs once per lookup cannot do. The docstring states the exclusion so a
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
    persona is in the document's bytes by *name*, which is the same coverage a plan task's
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
    #: The persona whose bar it will be judged under, where the node states one. `None`
    #: leaves the base config's generic contract.
    persona: str | None
    #: Whether :attr:`text` is a whole task. An amendment alone is criteria and nothing
    #: else, so it has no acceptance-criteria block of its own to find and no sections to
    #: walk.
    whole_task: bool
    #: How a refusal names it, which is what a manager acts on.
    where: str
    #: The node it belongs to, which a refusal declares on its ``objection:`` line.
    node: str
    #: Whether this text owes a judged turn once the free tier takes it. A **novel whole
    #: task** — an added node, a retry's replacement, a requeued node's amended task —
    #: does: it is a complete authored task nothing holds a pass for, and it would have
    #: been reviewed had it arrived in the plan. A correction — an `amend` — does not: it
    #: is written in the minute after a manager reads a failure and has to be answered
    #: before the envelope is sent, which is the same reasoning that exempts an amendment
    #: from the released-artifact refusal. The line is drawn at what the content *is*, not
    #: at which op carried it.
    judged: bool = True


def amended(task: str, amendment: str | None) -> str:
    """``task`` with ``amendment`` rendered into it exactly as the engine renders it.

    One acceptance section: the amendment's clauses — :func:`amendment_clauses` — go at
    the end of the task's :data:`CRITERIA_HEADING` section, under
    :data:`AMENDMENT_HEADING` and opening with :data:`AMENDMENT_PRECEDENCE` — or, where the
    amendment's first line attributes it to the user, that line and
    :data:`USER_AMENDMENT_PRECEDENCE`; a manager's attribution line opens the block above
    the manager's sentence. A task
    stating no criteria section is given one, immediately before an
    :data:`ADDITIONAL_INFO_HEADING` stated on a line of its own, or at the end of a task
    that states none. A blank amendment renders nothing, which is what the engine does
    with one. Headings are matched as whole lines, so prose that mentions one is not
    mistaken for the section itself.
    """
    if amendment is None or not amendment.strip():
        return task
    attribution = split_attribution(amendment.strip()).attribution
    user = attribution is not None and attribution.party == "user"
    precedence = USER_AMENDMENT_PRECEDENCE if user else AMENDMENT_PRECEDENCE
    opening = f"{attribution.line}\n" if attribution is not None else ""
    block = f"{AMENDMENT_HEADING}\n{opening}{precedence}\n\n{amendment_clauses(amendment.strip())}"
    end = _criteria_section_end(task)
    if end is not None:
        bar, rest = task[:end], task[end:]
        if not rest.strip():
            return f"{bar.rstrip()}\n\n{block}"
        return f"{bar.rstrip()}\n\n{block}\n{rest}"
    block = f"{CRITERIA_HEADING}\n\n{block}"
    at = 0
    for line in _lines(task):
        if line.rstrip() == ADDITIONAL_INFO_HEADING:
            return f"{task[:at].rstrip()}\n\n{block}\n{task[at:]}"
        at += len(line)
    return f"{task.rstrip()}\n\n{block}"


def amendment_clauses(amendment: str) -> str:
    """``amendment`` as the clauses of a criteria section, one bullet each, as the engine
    shapes it.

    Every word is kept and only the shape changes: a bullet or numbered item is one
    clause, a paragraph is one clause with its lines joined, and a heading becomes a bold
    label, because a heading inside the criteria section would end it. An attribution
    first line is who the ruling is from, never one of its clauses, so it is left out.
    """
    amendment = split_attribution(amendment).text
    lines: list[str] = []
    clause: str | None = None
    for line in amendment.split("\n"):
        trimmed = line.strip()
        text = _heading_text(trimmed)
        item = _list_item(trimmed) if text is None else None
        if not trimmed or text is not None or item is not None:
            if clause is not None:
                lines.append(f"- {clause}")
            clause = item
            if text:
                lines.extend(("", f"**{text}**", ""))
        elif clause is None:
            clause = trimmed
        else:
            clause = f"{clause} {trimmed}"
    if clause is not None:
        lines.append(f"- {clause}")
    rendered = ""
    blank = True
    for line in lines:
        if not line:
            if not blank:
                rendered += "\n"
            blank = True
            continue
        rendered += f"{line}\n"
        blank = False
    return rendered


def _heading_text(line: str) -> str | None:
    """A heading's text, for a line that is one: its ``#``s and the space after them gone."""
    text = line.lstrip("#")
    if len(text) == len(line) or not (text == "" or text.startswith(" ")):
        return None
    return text.strip()


def _list_item(line: str) -> str | None:
    """A list item's text, for a line that is one: its bullet or number and a space gone."""
    for marker in ("- ", "* ", "+ "):
        if line.startswith(marker):
            return line[len(marker) :].strip()
    digits = len(line) - len(line.lstrip("0123456789"))
    rest = line[digits:]
    if digits and rest[:2] in (". ", ") "):
        return rest[2:].strip()
    return None


def _lines(task: str) -> list[str]:
    """``task``'s lines with their newlines kept, split on a newline alone as the engine
    splits them, so an offset into the list is an offset into ``task``."""
    *whole, last = task.split("\n")
    return [f"{line}\n" for line in whole] + ([last] if last else [])


def _criteria_section_end(task: str) -> int | None:
    """Where ``task``'s criteria section ends — the next level-two heading, or the end —
    or ``None`` for a task stating no :data:`CRITERIA_HEADING` as a whole line."""
    at = 0
    inside = False
    for line in _lines(task):
        trimmed = line.rstrip()
        if inside and trimmed.startswith("##") and not trimmed.startswith("###"):
            return at
        if trimmed == CRITERIA_HEADING:
            inside = True
        at += len(line)
    return len(task) if inside else None


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
    named = read.get("id")
    try:
        nodes = list(dispatched_nodes({"tasks": [read]}))
    except CriteriaError:
        return
    for dispatched in nodes:
        text = amended(dispatched.task, amendment if isinstance(amendment, str) else None)
        yield Reviewable(
            text,
            dispatched.persona,
            True,
            f"{where} {dispatched.id!r}",
            named if isinstance(named, str) else dispatched.id,
        )


def _changes(document: object) -> Iterator[tuple[str, Mapping[str, object]]]:
    """Each ``(op, node)`` of the document's `changes`, in the order the engine wrote them.

    Lenient about every other shape, for the reason :func:`_effective` gives: a document
    this cannot read is one the engine wrote, and it refuses nothing here by guessing.
    """
    match document:
        case {"changes": [*changes]}:
            pass
        case _:
            return
    for change in changes:
        match change:
            case {"op": str() as op, "node": Mapping() as node} if op in CHANGE_OPS:
                yield op, node
            case _:
                continue


def reviewables(document: object) -> list[Reviewable]:
    """Every resulting task the document's `changes` put in front of a dispatch, in order.

    A whole-task op is read as the node it leaves; an `amend` as the correction that node
    now carries, named by its node; a `reparent` carries no prose and yields nothing.
    """
    found: list[Reviewable] = []
    for op, node in _changes(document):
        if op in WHOLE_TASK_OPS:
            found.extend(_effective(node, WHOLE_TASK_OPS[op]))
            continue
        named = node.get("id")
        text = node.get(AMENDMENT)
        # A blank or absent amendment is the engine's to refuse; it composes nothing.
        if op == AMEND_OP and isinstance(named, str) and isinstance(text, str) and text.strip():
            found.append(
                Reviewable(text, None, False, f"the amendment for node {named!r}", named, False)
            )
    return found


def _refused(reviewable: Reviewable) -> str | None:
    """Why the deterministic bar refuses ``reviewable``, or ``None`` when it has nothing to say.

    A whole task is asked the whole bar over its criteria and
    :func:`~orchestrator.criteria_guard.check_amendment`'s two questions over every other
    section it opens — the `### Amendment` block a stated node's task carries among them; a
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
        return str(exc)
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


class Refused(NamedTuple):
    """The first refused resulting task: the node a refusal objects to, and why."""

    #: The node the engine names as the one objected to, on the ``objection:`` line.
    node: str
    #: The reason, in the bar's own words, that is the refusal's stderr.
    reason: str


def refusal(document: object, judge: Judge = plan_review.verdict) -> Refused | None:
    """The first refused resulting task's node and why it is refused, or ``None`` for none.

    **Two tiers over each resulting task, in cost order.** The deterministic bar first,
    because it is free and refuses the commonest shapes without a provider turn; then, for
    a resulting task that owes one, a judged turn under ``judge`` — which is what asks the
    questions that moved out of the deterministic tier (whether a number is the right
    number, whether the criteria answer a demand their own bar makes, whether they could
    be falsified) of the one route to a task that `just review-plan` never reads. Which
    resulting tasks owe one is :attr:`Reviewable.judged`'s to say.

    The first rather than all of them: the engine refuses the whole envelope, so nothing is
    applied either way, and a manager correcting one command re-sends the envelope and is
    told about the next. Raises :class:`ReviewUnanswered` when a judged turn answered
    nothing.
    """
    for reviewable in reviewables(document):
        reason = _refused(reviewable)
        if reason is None and reviewable.judged:
            reason = _judged(reviewable, judge)
        if reason is not None:
            return Refused(reviewable.node, reason)
    return None


def structural_refusal(document: object) -> list[Refusal]:
    """Every structural refusal of a node the document's `add` and `retry` changes state.

    The rules are :data:`orchestrator.structural_guard.GUARDS`, the list `just check-plan`
    reads, asked over each stated node and the dependencies of it the document's `plan`
    carries — which is what lets a rule about a dependency's repository resolve it as it
    would in a plan — and only a refusal *of a stated node* is reported. Every one rather
    than the first, for the reason :func:`orchestrator.publication_guard.refusals` gives,
    each in its rule's own words, naming the node and the field to correct.

    A document stating no node asks nothing, so an amendment or a reparent spends no
    `onevcs` call.
    """
    stated = {
        node["id"]: dict(node)
        for op, node in _changes(document)
        if op in STATED_OPS and isinstance(node.get("id"), str)
    }
    if not stated:
        return []
    planned: dict[str, dict[str, object]] = {}
    match document:
        case {"plan": {"tasks": [*tasks]}}:
            planned = {
                task["id"]: dict(task)
                for task in tasks
                if isinstance(task, Mapping) and isinstance(task.get("id"), str)
            }
        case _:
            pass
    asked = {**stated}
    for node in stated.values():
        deps = node.get("deps")
        for dep in deps if isinstance(deps, list) else []:
            if isinstance(dep, str) and dep in planned and dep not in asked:
                asked[dep] = planned[dep]
    return [
        found
        for found in structural_guard.refusals({"tasks": list(asked.values())})
        if found.node in stated
    ]


def _said(reason: str, repair: str, *objected: str) -> None:
    """Write one verdict's reason, its repair and each node it objects to, to stderr.

    Stderr is where the engine reads a reason; an ``objection:`` line per node is how it
    learns which of the envelope's nodes to name, and it lifts those lines out of what it
    quotes back.
    """
    print(f"{reason}\n{repair}", file=sys.stderr)
    for node in dict.fromkeys(objected):
        print(f"{OBJECTION_PREFIX} {node}", file=sys.stderr)


def _kept(offered: bytes) -> None:
    """Print the document back exactly as it was read, which is how a hook keeps it."""
    sys.stdout.flush()
    sys.stdout.buffer.write(offered)
    sys.stdout.buffer.flush()


def main(argv: Sequence[str] | None = None, judge: Judge = plan_review.verdict) -> int:
    """Judge one envelope document on stdin, printing it back on a pass, or the fingerprint.

    ``judge`` is the one judged turn, real by default; a test hands over a scripted one
    at this boundary rather than standing the harness in below it.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == [BAR_FINGERPRINT_FLAG]:
        print(bar_in_force())
        return 0
    if arguments:
        _said(
            f"envelope-review: {arguments!r} is not what this reviewer takes",
            f"it reads one envelope document on stdin, or takes {BAR_FINGERPRINT_FLAG} alone",
        )
        return UNJUDGED
    # The exact bytes, not a decoding of them: a pass prints them back, and the engine
    # tells a keep from a change by bytes, never by parsed value.
    offered = sys.stdin.buffer.read()
    try:
        # llmlint: ignore[boundary_inputs_validated] The document is the engine's own,
        # serialized from its published plan shapes before any reviewer runs; every field
        # this reads is narrowed where it is read in `_changes` and `structural_refusal`,
        # and a shape neither reads moves nothing.
        document = json.loads(offered)
    except ValueError as unreadable:
        _said(
            f"the envelope document handed to this reviewer is not JSON ({unreadable})",
            "nothing is known about it, so nothing was applied; send the envelope again",
        )
        return UNJUDGED
    structural = structural_refusal(document)
    if structural:
        stated = "\n".join(
            f"node {found.node!r}, as this reply states it: {found.field}: {found.reason}"
            for found in structural
        )
        _said(
            f"this reply states a node this host's structural plan rules refuse — {stated}",
            "nothing was applied, so no other command in it was applied either; correct the "
            "field each refusal names and send the whole envelope again",
            *(found.node for found in structural),
        )
        return REFUSED
    try:
        refused = refusal(document, judge)
    except ReviewUnanswered as unanswered:
        _said(
            f"the task prose in this reply could not be judged: {unanswered}",
            "nothing was applied; once the harness answers — 'oneharness doctor' says why it "
            "did not — send the whole envelope again unchanged",
        )
        return UNJUDGED
    if refused is None:
        _kept(offered)
        return 0
    _said(
        f"this reply states task prose a judge would hold its worker to as work — {refused.reason}",
        "nothing was applied, so no other command in it was applied either; correct it and "
        "send the whole envelope again",
        refused.node,
    )
    return REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
