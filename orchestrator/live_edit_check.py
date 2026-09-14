"""Hold every live edit that changes what a node's judge reads to the criteria bar.

`scripts/channel-reply.sh` pipes the staged envelope here and refuses the whole reply
when this answers a reason. That is the one place it can be asked: a live edit reaches a
node through the channel rather than through the plan store, so `just check-plan` — which
reads a plan — never sees one, and `just review-plan` never records one.

**The bar is the one a plan is held to, in both of its tiers, and none of it is restated
here.** A plan's task clears `just check-plan` and then `just review-plan`; a live edit's
resulting task clears the same two, in the same order, before the envelope is sent. The
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
rules nobody checked. This module is the envelope reader in front of both, plus the
register that keeps what they cleared.

**Which resulting tasks owe the judged turn is decided by what the content is, not by
which op carried it.** A **novel whole task** — an added node, a retry's replacement, a
requeued node's amended task — is a complete authored task nothing holds a pass for, and
would have been reviewed had it arrived in the plan; it owes one. A **correction** to a
node a review already cleared — an `amend`, or a note's criterion — does not: it is
written in the minute after a manager reads a failure and has to be answered before the
envelope is sent, and a model call there would make every mid-run correction wait and
give a manager a reason to route around the guard. That is the same reasoning that
exempts an amendment from the released-artifact refusal, and it is
:attr:`Reviewable.judged`'s to state per resulting task.

**The protocol, which is `scripts/channel-reply.sh`'s to read.** The envelope arrives on
stdin and the run's own root, when the recipe could compose one, is the first argument.
Exit ``0`` means send it; exit ``1`` means refuse it, with the reason and nothing else on
stdout; exit ``3`` means the judged turn a resulting task needed could not answer, with
that reason on stdout — nothing is known about the text, nothing is sent, and the repair
is to send the envelope again once the harness answers rather than to correct it; exit
``4`` means a node the envelope results in is refused by a structural rule, with every
such refusal on stdout. Any other status is this check having failed to run, which the
recipe reports as such rather than as a verdict — so a non-zero exit here is never silent
and never empty.

**A resulting node is held to the plan tier's structural rules as well as its prose.**
What an `add` states, and what a `retry` or `requeue` returns with its overrides folded in
the way the engine folds them, is a node like any a plan carries — its `adoption`,
`consumes`, `merge_policy`, `repo`, `deps` and `title` decide where it publishes and what
it waits on — so it is held to :data:`orchestrator.structural_guard.GUARDS`, the one list
`just check-plan` reads, over the graph the whole envelope leaves: a dependency's
repository and release targets resolve as they do for a plan. It is asked first and never
recorded: its answer is `onevcs`'s about this host, not a property of text a register
could key, and it spends no provider turn, so an envelope it refuses spends none either.
The incident it exists for is a `requeue` amending `adoption: fast` onto a node publishing
`local-direct` behind a releasing dependency, which this check accepted and which then
failed an hour of work at its last step on the refusal :mod:`orchestrator.adoption_guard`
already carried.

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

**Keyed on the whole effective task, composed as the engine composes it, rather than on
the text the envelope happens to carry.** What a node's judge reads is the task the
dispatch receives, and the engine composes that from the node as it stands on the run:
an `amend` is the node's *current* task — its steps, its persona — with the new text
rendered under `## Amendment` immediately above the operational notes; a `requeue` is the
parked node's own record with the overrides written over it, `parked` removed, so a
requeue that restates no persona keeps the parked node's and is judged under that
persona's bar; an `add` and a `retry` are the node they state. :class:`Nodes` is where
the run's current nodes are read from — the checkpoint every reader leaves under the run
root, and the journal past the offset that checkpoint names, folded by the engine's own
four rules — so the key is over the text a dispatch will read rather than over the text
the plan was launched with. Two consequences are the point of it: the same amendment on
two different nodes is two different reviews, because it composes onto two different
tasks; and two ops that result in one effective task are one review, whichever op each
was — provided both owed the same tiers. A bare amendment composed onto its node is a
whole task the free tier alone cleared, and its record is keyed as exactly that, so an
`add` or a `retry` later stating that same effective text still spends the judged turn
a novel whole task owes; :func:`~orchestrator.plan_review.edit_key` says why. A
requeue whose overrides change no task results in what the node already
dispatched under, which the plan reviewed or an earlier reply recorded, so it reads
nothing. :func:`amended` and :func:`_requeued` restate the engine's composition, and
`tests/test_engine_contracts.py` holds both to its source at the pinned release.

**The digest decides whether either tier runs at all.** Every resulting task is digested
under the bar in force — both tiers of it, see :func:`bar_in_force` — and a digest the
register holds is applied untouched, while one it does not hold runs the free tier, then
spends the judged turn it owes, then is recorded so that the same text never spends a
second. Three readers passed the amendment that cost a node precisely because each of
them read a *region*: the amendment rode inside a `retry`'s replacement task, under a
heading of its own — which is where `AGENTS.md` says to put one — so the amendment check
declined it for not being an `amend`, and the task-level bar read the acceptance-criteria
block and stopped at the next heading. A key over the whole effective task has no such
region to miss, and reads the `## Amendment` block the engine itself renders.

**The register is per run, and this is the decision the node that added it had to make.**
A plan task keeps its review in the metadata of its own store document; a replacement
node has no store document, so the digest needs somewhere else to live and there were two
places: written back onto the node in the run's own state, or kept in a register of this
run's own. The run's own state is the **engine's** record of itself — its journal, its
plan, its channel — which nothing here writes to, and there is no path by which a reply
could put a field on a node anyway. So it is a register, at :data:`REGISTER` under the
run root, beside the derived `checkpoint.json` a reader already leaves there and named so
that it can never be mistaken for one of the engine's own records. It survives the run
because it is a file, which is what makes the second reply of a run find what the first
one wrote; it dies with the run, which is right, because a key is a claim about text a
manager sent to *this* run under the bar that was in force while it was live.

**A pass is recorded for text, not for an envelope that went out.** The register is
written even when a later command in the same envelope is refused and nothing is sent:
what a record says is that this resulting task cleared the bar, and that stays true of
text nobody applied. The alternative — recording only what was sent — would re-run the
bar over text a manager is in the middle of correcting, which is the case it most wants
to be cheap.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol, TypedDict

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
#: states none. Restated here because the effective task is what a judge reads and so
#: what a review has to be keyed on; `tests/test_engine_contracts.py` holds the three
#: strings and the placement to the engine's source at the pinned release.
AMENDMENT_HEADING = "## Amendment"
AMENDMENT_PRECEDENCE = (
    "Where this section and the operational notes below disagree, this section wins."
)
ADDITIONAL_INFO_HEADING = "## Additional info"

#: The field a node carries its binding amendment in, on the run's graph and in an
#: `add` or `retry` that states one outright.
AMENDMENT = "amendment"

#: Where a run's own record of its nodes is read from, under the run root. The
#: checkpoint is the derived fold every reader leaves there, and it names how far into
#: the journal it read; the journal past that point is folded here by the engine's own
#: rules, and a run with no checkpoint yet is folded from its launch plan.
CHECKPOINT = "checkpoint.json"
LAUNCH_PLAN = "plan.json"
JOURNAL = "events.jsonl"

#: The journal records whose operations move a node, and where each carries them: the
#: `kind` words of `onepipeline`'s `PipelineKind::EditCommitted` and `CommandAccepted`,
#: and the payload key its projection folds both from. Both kinds, as the engine's own
#: replay folds both — which of them a command is journalled under is the emitter's
#: decision, from whether any operation it compiled changes what a reader folds, so a
#: reader folding both is right whatever a later build puts where. Held to the engine's
#: declarations by `tests/test_engine_contracts.py`.
COMMITTED_KINDS = ("edit-committed", "command-accepted")
OPERATIONS = "operations"

#: Where the checkpoint keeps the graph's nodes and how far into the journal it read —
#: `onepipeline`'s `Checkpoint { coverage, state }`, `Coverage { bytes }`, the projected
#: `RunState { graph }` and the graph as written, `{ concurrency, nodes }`. Paths into
#: the engine's derived record rather than this module's own shape, and held to the
#: engine's declarations by `tests/test_engine_contracts.py`.
CHECKPOINT_NODES = ("state", "graph", "nodes")
CHECKPOINT_COVERAGE = ("coverage", "bytes")

#: The compiled operations of a committed command's record that move what a node's
#: dispatch reads, and the fields each is read for — `onepipeline`'s `Operation` enum,
#: serialized by `kind` in kebab case, at the five variants that fold a node: added
#: whole, dropped, parked, its task amended, requeued with overrides. Restated as data rather
#: than as patterns so that `tests/test_engine_contracts.py` can hold every kind and
#: every field to the engine's declaration.
FOLDED: dict[str, tuple[str, ...]] = {
    "node-added": ("node",),
    "node-dropped": ("node",),
    "node-parked": ("node",),
    "task-amended": ("node", "text"),
    "node-requeued": ("node", "amend"),
}

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

#: The exit status `scripts/channel-reply.sh` reads as a refusal. It is 1 rather than 2
#: because the recipe tells a refusal from a broken helper by what was *said* — a broken
#: interpreter can exit with any status, including this one.
REFUSED = 1

#: The exit status the recipe reads as *not reviewed*: the judged turn a resulting task
#: needed could not answer — no provider took it, or none answered with a verdict — so
#: nothing about the text is known and nothing is sent. A status of its own because the
#: repair is different from a refusal's: a refusal is corrected, this is re-sent once the
#: harness answers, and the recipe says which. The reason is on stdout as a refusal's is.
UNANSWERED = 3

#: The exit status the recipe reads as a **structural** refusal: a node the envelope
#: results in is one a rule of :data:`orchestrator.structural_guard.GUARDS` refuses. A
#: status of its own because what is wrong is a node's fields rather than task prose a
#: judge would read, and the recipe's sentence for a prose refusal would say otherwise.
STRUCTURALLY_REFUSED = 4

#: The wire marker that lets ``channel-reply`` distinguish this module's structural
#: verdict from an interpreter that happens to exit 4 after writing a diagnostic. The
#: recipe removes it before presenting the guard's own refusal message to the manager.
STRUCTURAL_WIRE_PREFIX = "live-edit-check:structural-refusal:"

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


#: Where one run's register of cleared live-edit reviews lives, under that run's own
#: root. A file rather than a directory, and named for this repository rather than for
#: the channel, so that it cannot be read as one of the engine's own records of the run.
REGISTER = "orchestrator-live-edit-reviews.json"

#: The shape :data:`REGISTER` is written in. Read back only when it matches, so a
#: register written by a later shape is treated as holding nothing — which costs a
#: re-check and never a wrong pass.
REGISTER_VERSION = 1


class ReviewUnanswered(OSError):
    """The judged turn a resulting task needed answered with no verdict.

    Raised out of :func:`refusal` rather than returned as a reason, because it is not a
    verdict about the text: a refusal says the text is wrong, and this says nothing is
    known about it. The message names the text and why no verdict came back, and the
    recipe reports it under :data:`UNANSWERED` with its own repair.
    """


# llmlint: ignore[names_match_behavior, changed_behavior_has_e2e] The bar in force is
# the two shared tiers this digests, and a persona's own review contract is deliberately
# outside it: the persona is in every key by *name*, through `edit_key`, which is exactly
# the coverage a plan task's record has of it under `review_key`. Digesting the resolved
# contract here and not there would let a plan record stand where the same text's
# live-edit record fell, over one moved persona file; the docstring states the exclusion
# so a reader is not left to infer it from the name. There is therefore no invalidation
# on a persona file to drive: a record surviving one is the decided behaviour, held for
# both record kinds alike, and the tests move the two fingerprints that do invalidate.
def bar_in_force() -> plan_review.BarFingerprint:
    """A digest of both tiers of the bar a live edit is asked, in this checkout.

    Both, because both run: :func:`~orchestrator.criteria_guard.criteria_fingerprint`
    covers the deterministic bar and :func:`~orchestrator.plan_review.edit_bar_fingerprint`
    the judged one — the plan reviewer's prompt and bar files plus the two frames a live
    edit is shown. A key under only one would let a record stand after the other moved,
    which is a pass over a question nobody asked; moving either invalidates every record
    this host holds for live edits, exactly as `personas/planner.yaml` moving invalidates
    a plan's.

    What it deliberately does not digest is the review contract of the **persona** a whole
    task names, which :func:`~orchestrator.criteria_guard.resolve_bar` reads — a shipped
    role's is compiled into the engine and a path's is a file under `personas/`. The
    persona is in the key by *name*, through :func:`~orchestrator.plan_review.edit_key`,
    which is the same coverage a plan task's record has of it.
    """
    digest = hashlib.sha256()
    digest.update(criteria_fingerprint().encode("utf-8"))
    digest.update(b"\0")
    digest.update(plan_review.edit_bar_fingerprint().encode("utf-8"))
    return plan_review.BarFingerprint(digest.hexdigest())


class Reviewable(NamedTuple):
    """One resulting task an envelope puts in front of a dispatch, and how to name it."""

    #: The text as the dispatch would meet it: a whole task, or an amendment composed
    #: onto one.
    text: str
    #: The persona whose bar it will be judged under, where the op states one. `None`
    #: leaves the base config's generic contract, which is what a partial override that
    #: does not restate the persona resolves to — the node's own is not in the envelope.
    persona: str | None
    #: Whether :attr:`text` is a whole task. A correction alone — a note's criterion, or
    #: an amendment whose node this run cannot read — is criteria and nothing else, so it
    #: has no acceptance-criteria block of its own to find and no sections to walk.
    whole_task: bool
    #: How a refusal names it, which is what a manager acts on.
    where: str
    #: Whether this text owes a judged turn once the free tier takes it. A **novel whole
    #: task** — an added node, a retry's replacement, a requeued node's amended task —
    #: does: it is a complete authored task nothing holds a pass for, and it would have
    #: been reviewed had it arrived in the plan. A correction to a node that was already
    #: reviewed — an `amend`, or a note's criterion — does not: it is written in the
    #: minute after a manager reads a failure and has to be answered before the envelope
    #: is sent, which is the same reasoning that exempts an amendment from the
    #: released-artifact refusal. The line is drawn at what the content *is*, not at
    #: which op carried it.
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


def _requeued(existing: Mapping[str, object], overrides: Mapping[str, object]) -> dict[str, object]:
    """The node a `requeue` returns to the frontier, as the engine composes it.

    The existing node's record with `parked` removed and every override written over
    its own field — a shallow merge, key by key, which is the engine's `NodeRequeued`
    fold. So a requeue that restates no persona keeps the parked node's own, and its
    amended task is judged under that persona's bar rather than the generic one.
    """
    merged = {key: value for key, value in existing.items() if key != "parked"}
    merged.update(overrides)
    return merged


def _fold(nodes: dict[str, dict[str, object]], operation: Mapping[str, object]) -> None:
    """Apply one compiled operation of a committed record to ``nodes``.

    The five operations of :data:`FOLDED`, folded as the engine's own replay folds them:
    a node added whole, dropped, parked, amended, or requeued. The park matters because
    it is the engine precondition for a later requeue. Every other operation moves
    edges, notes or settlements that this check does not read, and a record whose fields
    are not the shapes the engine writes is passed over rather than guessed at.
    """
    kind = operation.get("kind")
    if not isinstance(kind, str) or kind not in FOLDED:
        return
    read = {field: operation.get(field) for field in FOLDED[kind]}
    # Guards on the kind rather than value patterns, for the reason `reviewables` gives:
    # a bare name in a pattern binds. The node is narrowed once per arm to the shape that
    # kind writes it in — a whole mapping for a node added, an id for the rest.
    match read:
        case {"node": Mapping() as whole} if kind == "node-added" and isinstance(
            whole.get("id"), str
        ):
            nodes[whole["id"]] = {str(key): value for key, value in whole.items()}
        case {"node": str() as node} if kind == "node-dropped" and node in nodes:
            nodes.pop(node)
        case {"node": str() as node} if kind == "node-parked" and node in nodes:
            nodes[node]["parked"] = True
        case {"node": str() as node, "text": str() as text} if (
            kind == "task-amended" and node in nodes
        ):
            nodes[node][AMENDMENT] = text
        case {"node": str() as node, "amend": Mapping() as overrides} if (
            kind == "node-requeued" and node in nodes
        ):
            nodes[node] = _requeued(nodes[node], overrides)
        case {"node": str() as node} if kind == "node-requeued" and node in nodes:
            nodes[node] = _requeued(nodes[node], {})
        case _:
            return


def _at(document: object, path: tuple[str, ...]) -> object:
    """The value ``path`` reaches into ``document``, or ``None`` where it reaches nothing."""
    held = document
    for key in path:
        if not isinstance(held, Mapping):
            return None
        held = held.get(key)
    return held


def _journal_offset(journal: Path, covered: object) -> int | None:
    """Where the checkpoint stopped reading ``journal``, or ``None`` when that cannot be trusted.

    A coverage that is not a count, that runs past the journal, or that does not land
    on a record boundary is a checkpoint this cannot resume from, and its nodes are not
    used either: folding from the launch plan over the whole journal is the safe
    direction, since it re-reads records rather than skipping any or applying one twice.
    A checkpoint that read nothing is resumed from the start, journal or no journal.
    """
    if not isinstance(covered, int) or covered < 0:
        return None
    if covered == 0:
        return 0
    try:
        with journal.open("rb") as handle:
            handle.seek(covered - 1)
            return covered if handle.read(1) == b"\n" else None
    except OSError:
        return None


class Nodes:
    """The run's current nodes, as the run's own record of itself says they are.

    Read from the checkpoint every reader leaves under the run root — the engine's own
    fold of the journal up to the offset the checkpoint names — and then from the
    journal past that offset, folded by the same four rules; a run with no usable
    checkpoint is folded from its launch plan over the whole journal. That is what makes
    a live edit's key a key over the task the dispatch will actually read: an `amend`
    composes onto the task the node has *now*, amendments and requeues included, rather
    than onto the text the plan was launched with.

    A root this cannot read holds no nodes, and an op naming a node it does not hold is
    reviewed from what the envelope states alone — the engine refuses such an op for
    itself, so nothing here guesses at the node.
    """

    def __init__(self, nodes: dict[str, dict[str, object]]) -> None:
        self.nodes = nodes

    @classmethod
    def read(cls, root: Path | None) -> Nodes:
        """The current nodes under ``root``, or none when there is no root to read."""
        if root is None:
            return cls({})
        nodes: dict[str, dict[str, object]] = {}
        offset: int | None = None
        checkpoint = _document(root / CHECKPOINT)
        listed = _at(checkpoint, CHECKPOINT_NODES)
        if isinstance(listed, list):
            offset = _journal_offset(root / JOURNAL, _at(checkpoint, CHECKPOINT_COVERAGE))
            if offset is not None:
                nodes = _by_id(listed)
        if offset is None:
            match _document(root / LAUNCH_PLAN):
                case {"tasks": [*listed]}:
                    nodes = _by_id(listed)
                case _:
                    nodes = {}
            offset = 0
        try:
            with (root / JOURNAL).open("rb") as handle:
                handle.seek(offset)
                for raw in handle:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    match event:
                        case {"kind": str() as kind, "payload": Mapping() as payload} if (
                            kind in COMMITTED_KINDS
                        ):
                            operations = payload.get(OPERATIONS)
                            for operation in operations if isinstance(operations, list) else []:
                                if isinstance(operation, Mapping):
                                    _fold(nodes, operation)
                        case _:
                            continue
        except OSError:
            pass
        return cls(nodes)

    def get(self, node_id: object) -> dict[str, object] | None:
        """The node ``node_id`` names, or ``None`` when this run holds none by that id."""
        if not isinstance(node_id, str):
            return None
        held = self.nodes.get(node_id)
        return dict(held) if held is not None else None


def _document(path: Path) -> object:
    """``path`` parsed as JSON, or ``None`` when it cannot be read as any."""
    try:
        # llmlint: ignore[boundary_inputs_validated] The engine's own record of the run;
        # every field this reads is narrowed by the pattern that reads it, and a record
        # that matches none is treated as absent.
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _by_id(listed: list[object]) -> dict[str, dict[str, object]]:
    """``listed`` nodes keyed by id, keeping only the mappings that state one."""
    nodes: dict[str, dict[str, object]] = {}
    for node in listed:
        if isinstance(node, Mapping) and isinstance(node.get("id"), str):
            nodes[node["id"]] = {str(key): value for key, value in node.items()}
    return nodes


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


def _effective(node: Mapping[str, object], where: str, *, judged: bool) -> Iterator[Reviewable]:
    """Every task ``node``'s dispatch would read, composed as the engine composes it.

    The node is walked by :func:`~orchestrator.criteria_guard.dispatched_nodes`, which is
    the same reader `just check-plan` walks a plan with — so a stepped node yields one
    reviewable per step, a `kind: human` node yields none, and the id in a refusal is
    spelled the way that command's refusal would spell it. Each task then has the node's
    amendment rendered into it, because that is the text the judge reads: an amendment
    belongs to the node and renders into every agent step of it. A shape that reader
    refuses is passed over rather than reported: those are the shapes `onepipeline
    reply` refuses on its own, naming what it actually received, and a second opinion
    here would replace that with a guess.
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
        yield Reviewable(text, dispatched.persona, True, f"{where} {dispatched.id!r}", judged)


def _stated_nodes(command: Mapping[str, object], op: str, current: Nodes) -> Iterator[Reviewable]:
    """Every whole task ``command`` results in, on the run as it stands.

    An `add` and a `retry` state a full node, so what they result in is that node with
    whatever amendment it states. A `requeue` states overrides of a parked node, so what
    it results in is the **existing** node with those overrides merged onto it — the
    engine's own fold — which is what keeps the parked node's persona, its amendment and
    its steps in force under overrides that restate none of them. A requeue of a node this
    run cannot read is composed from the overrides alone, which is what the envelope
    states and all that can be known of it.
    """
    stated = command.get(STATED_IN[op])
    if not isinstance(stated, Mapping):
        return
    named = command.get("id")
    node: Mapping[str, object] = stated
    unchanged: set[tuple[str, str | None]] = set()
    if op == "requeue":
        existing = current.get(named)
        if existing is not None:
            node = _requeued(existing, stated)
            # What the parked node already dispatched under is not novel: the plan
            # reviewed it, or the live edit that last set it was recorded here. Only a
            # task the overrides changed — or a task now judged under another persona —
            # is put to the bar, so a requeue that moves a turn budget alone reads nothing.
            unchanged = {
                (before.text, before.persona)
                for before in _effective(existing, WHOLE_TASK_OPS[op], judged=True)
            }
    read = dict(node)
    if "id" not in read and isinstance(named, str) and named:
        # A `requeue`'s overrides carry no `id` — the engine refuses one that rewrites it
        # — so the parked node's own id is what names its amended task in a refusal.
        read["id"] = named
    for resulting in _effective(read, WHOLE_TASK_OPS[op], judged=True):
        if (resulting.text, resulting.persona) not in unchanged:
            yield resulting


def _amendments(text: str, named: object, position: int, current: Nodes) -> Iterator[Reviewable]:
    """What an `amend` results in: the node's own task with this text rendered into it.

    The node is read from the run as it stands, so the effective task is the one the
    node's next dispatch will read — its current task, or each of its steps, under this
    amendment in place of any earlier one. A node this run cannot read leaves the
    amendment to be read alone, as the correction it is: the engine refuses an `amend`
    naming no node of the graph for itself. Neither shape owes a judged turn, for the
    reason :attr:`Reviewable.judged` gives.

    By its node, because that is what a manager is holding in mind when they write one;
    by its position when the op names none, which is a shape the verb refuses on its own
    and which must still not produce a refusal naming `None`.
    """
    existing = current.get(named)
    if existing is not None:
        node = {**existing, AMENDMENT: text}
        yield from _effective(node, "the effective task of node", judged=False)
        return
    where = (
        f"the amendment for node {named!r}"
        if isinstance(named, str) and named
        else f"the amendment in commands[{position}]"
    )
    yield Reviewable(text, None, False, where, False)


def reviewables(envelope: object, current: Nodes | None = None) -> list[Reviewable]:
    """Every resulting task ``envelope`` carries, in the order it states them.

    ``current`` is the run's nodes as they stand, which an `amend` and a `requeue` compose
    onto; none is a run this could not read, under which each is composed from what the
    envelope states alone. Lenient about every shape but the ones it acts on, for the
    reason :func:`_stated_nodes` gives: a reply this cannot read is one `onepipeline
    reply` refuses with a message about the envelope it actually got.
    """
    match envelope:
        case {"commands": [*commands]}:
            pass
        case _:
            return []
    nodes = Nodes({}) if current is None else current
    found: list[Reviewable] = []
    for position, command in enumerate(commands):
        match command:
            # Guards on the op rather than value patterns, because a bare name in a
            # pattern binds. Blank text is left to the verb, which refuses it itself.
            case {"op": op, "text": str() as text} if op == AMEND_OP and text.strip():
                found.extend(_amendments(text, command.get("id"), position, nodes))
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
                found.extend(_stated_nodes(command, op, nodes))
            case _:
                continue
    return found


class Cleared(TypedDict):
    """One record of the register: what cleared the bar, and when, under what name."""

    #: When the pass was granted, as an ISO-8601 instant in UTC.
    cleared_at: str
    #: How the refusal would have named the text — the same phrase a manager reads.
    where: str


#: The alphabet and length of a review key on disk: the SHA-256 hex digest
#: :func:`~orchestrator.plan_review.content_key` writes. An entry keyed by anything else
#: is not one this module wrote, and is dropped on read.
KEY_DIGITS = frozenset("0123456789abcdef")
KEY_LENGTH = 64


def _is_key(key: object) -> bool:
    """Whether ``key`` is spelled the way a review key is written."""
    return isinstance(key, str) and len(key) == KEY_LENGTH and set(key) <= KEY_DIGITS


def _cleared(entry: object) -> Cleared | None:
    """``entry`` as one record, or ``None`` when it is not the shape this module writes."""
    match entry:
        case {"cleared_at": str(cleared_at), "where": str(where)}:
            return Cleared(cleared_at=cleared_at, where=where)
        case _:
            return None


def _read_register(path: Path | None) -> dict[plan_review.ReviewKey, Cleared]:
    """What ``path`` records as already cleared, or nothing when it records nothing.

    Every unreadable shape answers the same way — an empty register, or an entry left
    out of one — because the safe direction is to check again: a register this cannot
    read is one whose passes go unclaimed, which costs a re-run of the free bar and a
    repeated judged turn, where reading a shape it does not understand as a pass would
    let text through that nothing examined. So the document is read only at the version
    this writes, and each entry only when its key is a digest and its record the two
    fields :class:`Cleared` names.
    """
    if path is None:
        return {}
    try:
        # llmlint: ignore[boundary_inputs_validated] Narrowed immediately below, to the
        # one shape it is read in, entry by entry; every other shape answers as nothing.
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    match held:
        case {"version": int(version), "reviews": dict(reviews)} if version == REGISTER_VERSION:
            return {
                plan_review.ReviewKey(key): cleared
                for key, entry in reviews.items()
                if _is_key(key)
                for cleared in [_cleared(entry)]
                if cleared is not None
            }
        case _:
            return {}


class Register:
    """The resulting tasks one run has already cleared, and where that is kept."""

    def __init__(self, path: Path | None) -> None:
        #: `None` when the recipe could not compose a run root — an unresolvable run
        #: reference, which it forwards to the verb rather than guessing at. Nothing is
        #: read and nothing is kept, so every reviewable is checked and its judged turn
        #: spent again; the bar is the same one, so that is a repeated answer at a
        #: repeated cost rather than a different answer.
        self.path = path
        self.reviews = _read_register(path)
        self.added = 0

    def holds(self, key: plan_review.ReviewKey) -> bool:
        """Whether this run has already cleared the content ``key`` digests."""
        return key in self.reviews

    def record(self, key: plan_review.ReviewKey, where: str) -> None:
        """Keep ``key`` as content this run cleared, naming what it was."""
        self.reviews[key] = Cleared(cleared_at=datetime.now(UTC).isoformat(), where=where)
        self.added += 1

    def flush(self) -> str | None:
        """Write what was added, or say why it could not be kept.

        Written through a sibling and renamed, so a reader never meets a half-written
        register; a reply that loses that rename to a concurrent one loses a record
        rather than a pass, and the next reply re-checks the text it was about.

        A register that could not be written is **never** a refusal: the envelope was
        judged and its answer stands, and all that is lost is the saving. The caller says
        so on stderr rather than dropping it, because a saving that silently stopped
        happening is one nobody would ever look for.
        """
        if self.path is None or not self.added:
            return None
        document = json.dumps(
            {"version": REGISTER_VERSION, "reviews": self.reviews}, indent=1, sort_keys=True
        )
        beside = self.path.with_name(f"{self.path.name}.{os.getpid()}")
        try:
            beside.write_text(f"{document}\n", encoding="utf-8")
            os.replace(beside, self.path)
        except OSError as unwritable:
            return (
                f"live-edit-check: this run's register of reviewed task content could not "
                f"be kept at {self.path} ({unwritable}), so the next reply re-reads what "
                f"this one cleared; nothing about what was sent is changed"
            )
        return None


def _refused(reviewable: Reviewable) -> str | None:
    """Why the deterministic bar refuses ``reviewable``, or ``None`` when it has nothing to say.

    A whole task is asked the whole bar over its criteria and
    :func:`~orchestrator.criteria_guard.check_amendment`'s two questions over every other
    section it opens — the `## Amendment` block an effective task carries among them; a
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
    than read either way — nothing is known about the text, so nothing is recorded and
    nothing is sent — with the harness's own account of why and the repair it names.
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


def refusal(
    envelope: object,
    register: Register,
    judge: Judge = plan_review.verdict,
    current: Nodes | None = None,
) -> str | None:
    """Why this envelope's first refused resulting task is refused, or ``None`` for none.

    **Two tiers over each resulting task the register does not hold, in cost order.** The
    deterministic bar first, because it is free and refuses the commonest shapes without
    a provider turn; then, for a resulting task that owes one, a judged turn under
    ``judge`` — which is what asks the questions that moved out of the deterministic tier
    (whether a number is the right number, whether the criteria answer a demand their
    own bar makes, whether they could be falsified) of the one route to a task that
    `just review-plan` never reads. Which resulting tasks owe one is
    :attr:`Reviewable.judged`'s to say. A text that clears is recorded under
    :func:`bar_in_force`, so the next op resulting in it spends nothing.

    ``current`` is the run's nodes as they stand, which the effective task of an `amend`
    or a `requeue` is composed onto; the register's key is over that effective task, so
    the same amendment on two different nodes is two different reviews, and two ops that
    result in one effective task are one — where both owed the same tiers, which the key
    carries beside the text so that a correction's free pass never stands in for a
    novel whole task's judged turn.

    The first rather than all of them: the recipe refuses the whole envelope, so nothing
    is sent either way, and a manager correcting one command re-sends the envelope and is
    told about the next. Everything cleared before that point is recorded, so the
    re-send pays for the correction alone. Raises :class:`ReviewUnanswered` when a judged
    turn answered nothing, with everything cleared before it still recorded.
    """
    bar = bar_in_force()
    for reviewable in reviewables(envelope, current):
        key = plan_review.edit_key(
            reviewable.text,
            reviewable.persona,
            bar,
            whole_task=reviewable.whole_task,
            judged=reviewable.judged,
        )
        if register.holds(key):
            continue
        reason = _refused(reviewable)
        if reason is None and reviewable.judged:
            reason = _judged(reviewable, judge)
        if reason is not None:
            return reason
        register.record(key, reviewable.where)
    return None


def _consumes_keeping(node: dict[str, object], keep: Callable[[str], str | None]) -> None:
    """Rewrite ``node``'s `consumes` keys through ``keep``, dropping a key it answers `None` for.

    Assigned rather than mutated, so a mapping the run's own node record still holds is
    never changed underneath it; a node carrying no `consumes` mapping is left as it is.
    """
    consumes = node.get("consumes")
    if not isinstance(consumes, Mapping):
        return
    kept: dict[str, object] = {}
    for key, value in consumes.items():
        renamed = keep(str(key))
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


# llmlint: ignore[modern_domain_modeling] The graph is the engine's own open node records,
# handed straight back to the structural guards as the plan document they read leniently —
# the representation `Nodes` already holds them in. A typed node model here would be a
# second declaration of the engine's schema, and would drop the fields the guards read
# that such a model did not name (`metadata`, `consumes`, whatever a later release adds).
def resulting_graph(
    envelope: object, current: Nodes
) -> tuple[dict[str, dict[str, object]], list[str]]:
    """The graph ``envelope`` leaves on the run, and the ids of the nodes it results in.

    Every command is folded in order onto the run's current nodes, so a later command
    reads what an earlier one did, and each is folded as the engine's `edits.rs` compiles
    and applies it, `consumes` included — because the adoption rules read it: an `add`
    puts its node in; a `retry` puts its replacement in place of the node it names — the
    replacement inheriting that node's `deps` and `consumes` when it states no deps of its
    own — and redirects that node's dependents to it, their `consumes` rekeyed with them;
    a `requeue` folds its overrides onto the parked node exactly as :func:`_requeued` does;
    a `reparent` replaces a node's `deps` and keeps only the `consumes` keyed on one of
    them; and a `drop` removes a node as its `dependents` disposition says.
    `tests/test_engine_contracts.py` holds each of those folds to the engine's source at
    the pinned release. The nodes it results in are the ones an `add`, a `retry` or a
    `requeue` returned and that the envelope left in the graph. A command naming a node
    the graph does not hold, or stating a shape the engine refuses, moves nothing — that
    refusal is `onepipeline reply`'s to make, naming what it received.
    """
    graph = {node_id: dict(node) for node_id, node in current.nodes.items()}
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
            # An `add` of an id the graph holds, and a `retry` of a node it does not hold
            # or onto an id it does, are refused by the engine for their target, so each
            # moves nothing here rather than checking a node that will never be committed.
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
                and old in graph
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
                for other in graph.values():
                    deps = other.get("deps")
                    if isinstance(deps, list) and old in deps:
                        other["deps"] = [new if one == old else one for one in deps]
                        _consumes_keeping(other, _renaming(old, new))
                graph[new] = replacement
                resulting.append(new)
            case {"op": op, "id": str() as named} if (
                op == "requeue"
                and set(command) <= {"op", "id", "amend"}
                and set(command) >= {"op", "id"}
                and named in graph
                and graph[named].get("parked") is True
                and (
                    "amend" not in command
                    or (
                        isinstance(command["amend"], Mapping)
                        and set(command["amend"]) <= NODE_FIELDS - {"id", "deps"}
                    )
                )
            ):
                overrides = command.get("amend")
                graph[named] = _requeued(
                    graph[named], overrides if isinstance(overrides, Mapping) else {}
                )
                resulting.append(named)
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


def structural_refusal(envelope: object, current: Nodes | None = None) -> str | None:
    """Every structural refusal of a node ``envelope`` results in, or ``None`` for none.

    The rules are :data:`orchestrator.structural_guard.GUARDS`, the list `just check-plan`
    reads, asked over the resulting nodes and the dependencies they name — which is what
    lets a rule about a dependency's repository resolve it as it would in a plan — and
    only a refusal *of a resulting node* is reported: a node the envelope leaves as it was
    is not this reply's to answer for. Every one rather than the first, for the reason
    :func:`orchestrator.publication_guard.refusals` gives, and each in its rule's own
    words, naming the node and the field to correct.

    An envelope resulting in no node asks nothing, so a note, an amendment or a
    cancellation spends no `onevcs` call.
    """
    graph, resulting = resulting_graph(envelope, Nodes({}) if current is None else current)
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
        f"node {found.node!r}, as this reply leaves it: {found.field}: {found.reason}"
        for found in refused
    )


def _run_root(argument: str | None) -> Path | None:
    """The run root the argument names, or ``None`` when it names none.

    The argument is `scripts/channel-reply.sh`'s composition of a run reference it has
    already matched against the ask-manager grammar and the ledger root `onepipeline`
    itself configures — so what is checked here is the one thing that composition can
    still get wrong, that the directory exists: a run the ledger does not hold has no
    register to read and nowhere to keep one, and a path written beneath it would be
    this module inventing a run root. Said on stderr, because a register silently not
    kept is the saving nobody would look for.
    """
    if argument is None:
        return None
    root = Path(argument)
    # llmlint: ignore[boundary_inputs_validated] The argument is the recipe's own
    # composition of `onepipeline`'s configured ledger root and a run reference it has
    # already matched against the ask-manager grammar — the same input the recipe
    # suppresses this rule for, and for the same reason: everything beneath it is a
    # lenient read whose failure costs a repeated check, and the one write is the
    # register, kept in the run's own root. A stricter check here would be a second
    # opinion on the verb's own configuration, and would refuse a register for a run
    # the verb itself accepts a reply to.
    if not root.is_dir():
        print(
            f"live-edit-check: {root} is not a run root this ledger holds, so no register "
            f"of reviewed task content is read or kept for this reply and no node of it is "
            f"read; nothing about what is sent is changed",
            file=sys.stderr,
        )
        return None
    return root


def main(argv: Sequence[str] | None = None, judge: Judge = plan_review.verdict) -> int:
    """Read one reply envelope on stdin and say whether its task prose may be sent.

    ``judge`` is the one judged turn, real by default; a test hands over a scripted one
    at this boundary rather than standing the harness in below it.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    root = arguments[0] if arguments and arguments[0] else None
    try:
        # llmlint: ignore[boundary_inputs_validated] The envelope is `onepipeline
        # reply`'s own open contract; every field this reads is narrowed where it is read
        # in `reviewables`, and a document this cannot parse is passed to the verb, which
        # refuses it naming what it actually received.
        envelope = json.load(sys.stdin)
    except ValueError:
        return 0
    run_root = _run_root(root)
    current = Nodes.read(run_root)
    # llmlint: ignore[boundary_inputs_validated] Asked in front of `onepipeline reply` on
    # purpose, exactly as the prose tiers below are: this is the one place a node an
    # envelope results in can be held to the plan tier's rules before it is sent. Every
    # command field `resulting_graph` acts on is narrowed by the pattern that reads it and
    # every node field by the guard that reads it; a shape neither reads moves nothing and
    # is left to the verb, which refuses it naming what it received.
    structural = structural_refusal(envelope, current)
    if structural is not None:
        sys.stdout.write(f"{STRUCTURAL_WIRE_PREFIX}{structural}")
        return STRUCTURALLY_REFUSED
    register = Register(None if run_root is None else run_root / REGISTER)
    status = REFUSED
    try:
        reason = refusal(envelope, register, judge, current)
    except ReviewUnanswered as unanswered:
        # What cleared before the turn that answered nothing is still a pass over text,
        # so it is kept; the envelope is not sent, and the recipe says why and what to do.
        reason, status = str(unanswered), UNANSWERED
    unkept = register.flush()
    if unkept is not None:
        print(unkept, file=sys.stderr)
    if reason is None:
        return 0
    sys.stdout.write(reason)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
