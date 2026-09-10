#!/usr/bin/env python3
"""Serve the planner channel as the monitor member's judge side.

`onepipeline channel serve RUN` is the published server side of the planner
channel: it takes one observer frame on stdin, queues it as a planner surface,
blocks until the planner answers, and writes that answer to stdout. onejudge's
judge-side command provider is the other end of the same conversation: it writes
one supervisor frame to a command's stdin and reads one response object back.

The two halves already agree on the **response**. `channel serve` answers with
exactly `{"completion": ..., "message": ..., "reason": ...}`, which is the object
onejudge's `supervisor` op expects. What they do not agree on is the **request**,
and this filter is that one reconciliation, as onejudge 0.7.0 writes it and
`onepipeline` 0.26.1 reads it:

    onejudge  ->  {"op": "supervisor", "task", "persona", "done_when",
                   "worktree", "history_name", "messages": [...], "session"}
    serve     <-  {"kind", "message", "blocking"?, "node"?}

Handing onejudge's frame over unchanged is refused by name — `the observer emitted
a bad frame: unknown field 'op'` — and the member then dies with `provider produced
no output`, which is how a graph that wired the two together directly loses its
monitor on the first turn while the run carries on unwatched.

Two values have to be recovered before a surface can be raised, and both are read
out of what the frame itself carries:

* **The run id.** The task `onepipeline` composes for the graph opens by naming
  the run, so the id is read from there. The environment names it too —
  `ONEPIPELINE_RUN_ID` is set to the run id on both sides of an observer member,
  measured against onepipeline 0.26.1 by dumping this command's whole environment on
  a real launch — but that is a per-release export while the composed task is the
  contract this filter already validates, so the task stays the source. The export
  is gated by `tests/e2e/test_orchestrate_launch_e2e.py`, which stands a probe where
  this file stands and re-takes the measurement rather than trusting this paragraph.
* **What to surface.** Almost nothing, which is the whole of this filter's reporting
  policy: a monitor's report reaches the planner through the `finding` op it issues
  itself, and never through the prose a turn happened to end in. The one thing raised
  from a turn is a turn the monitor did not take — see below.

**A monitor reports through the `finding` op, and this filter raises no surface for
what it says.** That is a deletion rather than a filter, and the vocabulary went with
it. While prose was raised automatically, a monitor with a finding to file had three
moves and none was clean: prose alone, which loses the operation's node attribution and
its structured kind; the operation *and* prose, which is two surfaces for one finding;
or the operation and a quiet-turn sentinel, which is one surface and a false statement
its own judge then scores against a bar about surfacing everything observed. It chose
the middle every time — of `root-causes-94-plan`'s 54 surfaces, 19 are findings and 8
are `monitor` prose, and all 8 duplicate the finding immediately before them, raised
three to thirty-five seconds later, six of them byte-identical. That is 30% of the
monitor-authored surfaces carrying nothing the operator had not been handed seconds
earlier, against a `personas/orchestrator.yaml` that promises a finding arrives once.
A planner may not filter the unread-surface line — a blocking surface produces no
other signal until it is read — so every duplicate degrades the one indicator that
discipline exists to protect.

Suppressing prose that merely *resembles* a recent finding was the obvious alternative
and the same measurement rules it out: two of those eight were restatements at very low
token overlap, so content matching would catch at most six of eight while risking the
suppression of a genuine follow-up. Removing the path removes the choice instead.

**A monitor that looked and found nothing is not a monitor that failed**, and
conflating the two removed this host's whole supervisory tier for two hours at a time.
Until this filter learned the difference, a frame carrying no assistant content was
refused as a protocol failure, `oneagentgraph` recorded `member-died
{"rule":"provider-failure","cause":"protocol"}`, and the run went on reporting `ACTIVE`
with nothing watching it and nothing announcing the loss. Observed on
`spanish-language-tutor-upgrade`, which lost its observer five minutes in and ran
roughly two hours that way while every other indicator stayed green.

So liveness is now the whole question a turn's content is asked, and there are two
answers rather than three:

* **any assistant content at all** — the monitor took its turn. No surface is raised,
  nothing is queued for anybody to read, and onejudge is answered with a non-completion
  it can act on, so the member lives and keeps watching. What the monitor *meant* to
  report is on the channel already if it filed a `finding`, and is nowhere if it did
  not — which is its supervisor's to catch, not this filter's;
* **no assistant content at all** — a failure, because that is a real provider defect
  and it is what a turn the agent side lost looks like from here.

There is deliberately no third answer and no fixed string anywhere on this path. A
sentinel is a vocabulary, a vocabulary can be got wrong, and the monitor was scored on
getting it wrong.

**One thing is still read out of the content, and it is the opposite of a report.** A
turn the agent side lost does not always arrive empty: the harness writes its own
stream into the last assistant message instead — measured off this host's
`runs/rc-fixes-brief` channel, fifteen JSON-RPC frames and 21,531 characters, most of
it the prompt echoed back, ending in `method: error` and a `turn/completed` whose
`status` is `failed`. That is the same provider defect as an empty turn wearing
different clothes, and reading it as content would let a monitor whose agent side is
failing look healthy for the rest of the run. So `transcript_frames` and
`lost_turn_error` stay, and a transcript that **proves** the turn was lost is raised as
a bounded `monitor-failed` line naming the cause and the identity. That surface is this
filter reporting on the member, not the monitor reporting on the run, which is why it
survives a change that took away every other raise.

**What went with the prose path is the transcript machinery that was compensating for
it.** A machine transcript no failure can be proven inside used to get a bounded
`monitor-transcript` surface of its own, and that existed for exactly one reason: prose
was republished verbatim, so 26 oversized surfaces measured on this host — every one
`status: completed` with `error: null` — put 176.1 MB of protocol on the channel as the
monitor's own words. With nothing republished there is nothing to bound. An unprovable
transcript is now content like any other: it raises no surface, the member is answered
and lives, and the run already keeps the turn where `just monitor <run> --filter
monitor` reads it.

Structured output is the heavier alternative and one constraint rules it out: oneharness
validates a structured answer against the complete response, so `stream = true` and
`schema_file` cannot both hold, and turning streaming off for the run's long-lived
watcher would trade away the per-turn visibility a manager supervises with.

The surface is raised **non-blocking**. Blocking it would hold the run at
`awaiting-planner` on every monitor turn, which would end the attached launch's
settle-and-return contract and stop the frontier to ask a question about watching
rather than about work. A planner who never answers leaves the monitor waiting,
which costs the run nothing: it is a watcher, and the engine drives the graph
without it.

**Nothing here answers on the planner's behalf.** onejudge reads this process's
stdout as the planner's ruling, so a fabricated `{"completion": ...}` would
continue or settle a run nobody ruled on. Every path that cannot reach a real
answer — including a channel response this cannot recognise — exits non-zero with
what went wrong and what to do about it.

**Two ops reach this filter, and both are the planner's own question.** onejudge asks
`supervisor` at each turn boundary and `judge` once the conversation ends, to score the
`user.done_when` — always, whether the supervisor ruled complete or the turn cap ran
out, and independently of `evals` and `assessment` (measured on onejudge 0.7.0 with a
`kind: command` judge that logged every op). That second one has no configuration
escape: `oneagentgraph` refuses a persona that replaces the base's bar with nothing, so
a `kind: onejudge` member always has a `done_when` and is always asked to score it. It
used to be refused here, which killed the monitor at the end of every run it watched —
`got 'judge'`, `provider-failure`/`protocol` — after which the run went on being driven
and reporting `ACTIVE` with nobody watching.

It is served the same way `supervisor` is, and for the same reason: the planner **is**
this member's judge side, so the planner scores the criterion. The criterion is raised
as its own non-blocking surface and the ruling that comes back is the score —
`completion` becomes the boolean, and the prose beside it becomes the rationale.
Nothing is invented, and a planner who never answers costs nothing: `channel serve`
times out with its own non-completion, which is the conservative reading (`unsatisfied`)
rather than a fabricated success. The one thing this op cannot take from the frame is
the run, because onejudge writes no `task` into it; it is read from `ONEPIPELINE_RUN_ID`
instead, which `onepipeline` exports to both sides of an observer member and which
`tests/e2e/test_orchestrate_launch_e2e.py` re-measures on a real launch every gate run.

`assess` and a non-boolean `judge` are still refused by name. Both come only from
`assessment` and `evals`, which `tests/test_observer_judge_ops.py` forbids any
channel-served persona from carrying, so neither can arrive from this repository's own
graphs — and a `completion` boolean is not a score on a 1-to-5 scale in any case.

**One answer is recognised, and it is not addressed to this reader at all.** The
channel is a durable queue with two readers — this one, which wants a supervisor
ruling, and the engine's reconciler, which wants graph edits — and through
onepipeline 0.8.x it arbitrated between them by arrival order. So a manager's live
graph edit — `{"version":1,"commands":[…]}` carrying no boolean `completion` —
reached this reader whenever it got there first. Forty of this host's recorded
dag-scope runs died on it, refused as "not a supervisor ruling" and killed. The
timing was the worst part: it fired precisely while a manager was supervising,
because the manager's own correction was what killed the watcher.

**The adopted release routes a reply by the halves it carries**, which is that
failure fixed at its source: `Channel::answer_if_verdict` puts a commands-only
envelope on the command path alone and leaves the pending surface standing, and
`Channel::claim_reply` hands this reader one verdict per claim and passes over any
such envelope an older build already wrote. One carrying both a verdict and edits
goes to both. So the branch below is no longer on the path an edit takes — it is
what stands between a run and that death if a release ever regresses, and it is
driven directly rather than through the channel for exactly that reason.

Such an answer is **recognised, reported to the monitor, and not acted on**, and the
member survives it. Not acted on is the measured half. `onepipeline reply` applies an
envelope's commands *itself*, before the envelope is queued for any reader: measured
against onepipeline 0.26.1 by replying `{"op":"add", …}` to a real run, which answers
`{"reply":0,"state":"applied","commands":"applied"}` and records `edit-committed` there
and then. So the
edit has already reached the engine by the time it arrives here, and this reader has
nothing left to route. Handing it back with a second `onepipeline reply` — the obvious
repair, and the one to resist — **re-applies** it: the same measurement, re-submitted,
is refused with `add: node 'added-by-the-edit' already exists`, and an op with no such
guard (`retry`, `cancel`, `requeue`) would simply be applied twice. Nothing here is
worth a duplicated graph edit.

What is left is to survive and to say so. The monitor is answered with a
**non-completion** naming the edits that arrived, which is not answering for the
planner: a non-completion settles nothing, completes nothing, and rules on no work,
and all it says is that this surface has not been answered yet — which is what
happened, since the planner sent the engine an edit rather than answering the monitor.
Any prose riding beside the edits is carried through, because this reader is the last
thing holding it.

Two things hold that reasoning to the release rather than to this paragraph, because
if `reply` ever stopped applying commands itself, ignoring one here *would* lose it:
`tests/e2e/test_monitor_survives_the_channel_e2e.py` sends a real commands-only
envelope on a real run's channel, asserts the verb answered it the way this paragraph
quotes, asserts it reached the graph, and asserts the member lives through it. The
durable fix is upstream — a reply routed by its intended reader rather than claimed by
arrival — and this reader holds the same line independently of it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict, cast

#: A onepipeline run id. Distinguished from the task prose and message text it is
#: parsed out of, because the only thing that makes it a run id is where it was found.
RunId = NewType("RunId", str)

#: How the composed dag-scope task names its run, on its first line:
#: ``onepipeline run `scheduler-research`.``. That opening is the published
#: composed-task contract for this graph, and the only place an observer member is
#: told which run it is watching in a way that does not depend on the release.
RUN_IN_COMPOSED_TASK = re.compile(r"onepipeline run `([^`]+)`")

#: What a run id read out of that task may be, checked before it is used. It is read
#: from somebody else's prose and then put to two uses that a free-form string does not
#: survive: it becomes an argv word handed to `onepipeline channel serve`, and that verb
#: resolves it as the `runs/<run-id>/` directory. Deliberately a superset of what
#: `onepipeline` mints from a plan's name — `serve-e2e`, `planner-supervises-monitor` —
#: rather than a second copy of that grammar, because this is the boundary check and not
#: the grammar's other home: it refuses only what those two uses cannot survive. A
#: leading `-` would be read as a flag rather than a run; a `/`, a `.` opening what could
#: be `..`, or a NUL or other control character would name something other than the run.
SAFE_RUN_ID = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")

#: What the monitor is told when its turn produced content. Nothing in it is compared
#: against anything the monitor wrote — this is the answer to *every* turn that was
#: taken — and it says where a report goes, because the `finding` op is now the only
#: route and a monitor that wrote its observation as prose has reported it to nobody.
TURN_TAKEN_ACKNOWLEDGED = (
    "Your turn was taken and no planner surface was raised for it: prose reaches "
    "nobody. A report reaches the planner only as a `finding` op in an "
    "`onepipeline reply` envelope, which arrives once and carries the node it is "
    "about. Keep reading the detailed stream and file the next thing you find."
)

#: And why that ruling is a non-completion, for the reader who meets it in a transcript.
TURN_TAKEN_REASON = "the monitor took its turn; a report reaches the planner as a finding"

#: The kind a turn that failed instead of speaking is raised under. Its own kind
#: rather than a differently worded `monitor`, because the planner-facing views name
#: the kinds a run has queued: it is what an operator who may not filter that line can
#: read off it without opening one.
SURFACE_KIND_OF_A_FAILED_TURN = "monitor-failed"

#: How much of a failure's cause may reach the surface message. The cause is the one
#: part of the line copied out of somebody else's transcript, so it is the one part
#: bounded. Bounding it rather than clamping the composed line is deliberate: a clamp
#: would cut off the remedy, which is the half a reader acts on. The run id is left
#: whole for the same reason — it is rendered inside a command they copy.
CAUSE_LIMIT = 80

#: What a cause may not carry into a one-line surface as itself, collapsed to a single
#: space before it is bounded. The text is the losing harness's: a newline in it would
#: make the line two, an escape sequence would rewrite what the reader's terminal shows,
#: and a run of ordinary spaces is only ever noise in a line this short.
COLLAPSED_IN_A_CAUSE = re.compile(r"[\s\x00-\x1f\x7f]+")

#: What the line says when the transcript records a failed turn and nothing about why,
#: and when it names no harness. Naming one it does not name would send a planner to
#: the wrong quota.
NO_CAUSE_RECORDED = "no cause recorded"
UNIDENTIFIED_HARNESS = "an unidentified harness"

#: How a codex transcript names the home it was credentialed from, and the variable
#: that says which of this host's two codex identities that is —
#: `scripts/codex-alt-home.sh` owns the path, so it is compared rather than copied.
CODEX_HOME_IN_TRANSCRIPT = "codexHome"
CODEX_ALT_HOME = "ORCHESTRATOR_CODEX_ALT_HOME"
CODEX_IDENTITY = "codex"
CODEX_ALTERNATE_IDENTITY = "codex:alternate"

#: Which `onepipeline` answers the planner. Named so the binary is a seam a journey
#: can drive, exactly as `ONEAGENTGRAPH_ONEHARNESS_BIN` is for a dispatch; unset, the
#: release this checkout pins is used.
ONEPIPELINE_BIN = "ONEPIPELINE_BIN"

#: How `onepipeline` names the run to both sides of an observer member. The scoring op
#: is the one frame that carries no `task`, so this is its only source; measured against
#: onepipeline 0.26.1 and re-measured on a real launch by
#: `tests/e2e/test_orchestrate_launch_e2e.py` every gate run.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"

#: The extra fields a scoring frame carries, checked before either is used.
SCORE_KIND = "kind"
SCORE_CRITERION = "criterion"

#: What a reply envelope carries when it is a live graph edit, and the field that makes
#: one a ruling instead. `completion` is the whole discriminator: onejudge acts on that
#: boolean and on nothing else, so an answer without it is not a ruling however much
#: prose rides beside it, and an answer with it is one however many edits do.
LIVE_EDIT_COMMANDS = "commands"
RULING_VERDICT = "completion"

#: How one live-edit command names what it does and what it does it to, for the line a
#: planner reads when the engine would not take it. Neither is required of a command by
#: anything here — `onepipeline` owns that schema — so both are read defensively.
EDIT_OP = "op"
EDIT_TARGET = "id"
UNNAMED_EDIT = "an unnamed edit"

#: The prose a planner may send beside their edits. Relayed to the monitor rather than
#: dropped: an envelope carrying both is a manager saying something as well as changing
#: the graph, and this reader is the only thing that would have carried the saying.
PLANNER_PROSE = ("message", "reason")

#: What the monitor is told when the planner's answer was a graph edit. Its own turn
#: was not answered, so it is told that and told to carry on: a monitor that read this
#: as a verdict on its watch would stop watching.
ANSWERED_THE_ENGINE = (
    "The planner's answer to this surface was a live graph edit addressed to the "
    "engine, not a ruling on your watch: {named}. `onepipeline reply` applied it when "
    "it was sent, so there is nothing here to route and nothing has been re-sent. Your "
    "surface has not been answered — keep watching the detailed stream and raise what "
    "you find."
)
PLANNER_ALSO_SAID = " The planner also said: {said}"

#: The two ops this filter serves. `supervisor` is the turn boundary; `judge` is the
#: end-of-conversation score of the `user.done_when`, which no configuration can turn
#: off for a `kind: onejudge` member.
SUPERVISOR_OP = "supervisor"
SCORE_OP = "judge"
SERVED_OPS = (SUPERVISOR_OP, SCORE_OP)

#: The only score shape a planner ruling can answer: `completion` is a boolean, so a
#: boolean criterion is a relay and a numeric one would be an invention.
BOOLEAN_SCORE = "boolean"

#: The kind the completion criterion is raised under. Its own kind, like a lost turn's,
#: because the planner-facing views name the kinds a run has queued and this one is a
#: different question from a monitor's observation: it arrives once, at the end.
SURFACE_KIND_OF_A_COMPLETION = "monitor-completion"

#: How that surface asks it. The criterion is quoted whole rather than bounded — it is
#: this operator's own configuration rather than a foreign transcript, and a planner
#: cannot rule on a bar they were shown half of.
SCORE_ASKED = (
    "The monitor's conversation has ended and onejudge is scoring that conversation "
    "against the monitor's own completion bar. You are this member's judge side, so the "
    "score is yours: did this watch meet the bar quoted below? Reply `completion: true` "
    "if it did, `false` if it did not. THE RUN IS NOT BLOCKED ON THIS and nothing waits "
    "for you: the surface is non-blocking, no answer is read as `false`, and the run "
    "settles either way.\n\n{criterion}"
)

#: What the score says when the planner said nothing else. `rationale` is optional to
#: onejudge, but a score with no reason beside it is unreadable in a transcript.
SCORE_UNEXPLAINED = "the planner ruled on this member's completion bar over the channel"

#: Why that ruling is a non-completion, for the reader who sees it in the transcript.
NOT_A_RULING = "the planner sent a graph edit rather than a ruling on this surface"


class ConversationMessage(TypedDict, total=False):
    """One turn of the monitor's conversation, as onejudge writes it into the frame.

    `total=False` for the same reason as the frame that carries it: a message missing
    `role` or `content`, or carrying something other than text, is checked for at the
    one place it is read rather than assumed away by this declaration.
    """

    role: str
    content: str


# The five models below state a wire format this file does not own, so the one thing
# keeping them true is `tests/test_lost_turn_wire_contract.py`: it reconciles each field
# against the installed producer — what it emits on a real lost turn, and what its own
# generated protocol schema declares — and fails the gate on drift. Add a field here and
# that gate refuses it until it names who declares it upstream.
class TurnError(TypedDict, total=False):
    """What a harness records about a turn it could not take.

    `total=False`, and read defensively, for the reason every other wire model here is:
    this is the losing harness's own vocabulary. `codexErrorInfo` is the classification
    a planner acts on — `usageLimitExceeded` names the quota to go and look at — and
    `message` is the paragraph beside it.
    """

    message: str
    code: str
    codexErrorInfo: str


class TurnRecord(TypedDict, total=False):
    """One turn as its harness reports it, narrowed to what says it was lost."""

    id: str
    status: str
    error: TurnError


class FrameParams(TypedDict, total=False):
    """The payload of one streamed frame, narrowed to the two that carry a failure."""

    turn: TurnRecord
    error: TurnError


class HarnessResult(TypedDict, total=False):
    """A reply frame, narrowed to the one field that says which identity is running.

    A codex stream opens with the home it was credentialed from, which is what tells
    this host's two codex identities apart.
    """

    codexHome: str


class TranscriptFrame(TypedDict, total=False):
    """One line of the machine transcript a lost turn leaves behind.

    The harness's own stream rather than onejudge's or `onepipeline`'s, and the only
    wire format here that reaches this filter by accident: it arrives as the monitor's
    "last assistant message" when the turn failed instead of producing one.
    """

    method: str
    result: HarnessResult
    params: FrameParams


class SupervisorFrame(TypedDict, total=False):
    """What onejudge writes to a judge-side command provider's stdin.

    `total=False` because this is somebody else's wire format, read defensively:
    every field is checked before use rather than assumed present, so a release that
    changes the frame fails here by name instead of surfacing nonsense to a planner.
    """

    op: str
    task: str
    persona: str
    done_when: str
    worktree: str
    history_name: str
    messages: list[ConversationMessage]
    session: str
    # The scoring op's own two, which no `supervisor` frame carries and the other way
    # round: onejudge writes `{"op":"judge","kind","criterion","messages"}` and nothing
    # else, so every field here is read defensively at the one place it is used.
    kind: str
    criterion: str


class ObserverFrame(TypedDict):
    """What `onepipeline channel serve` reads: one surface to raise for the planner."""

    kind: str
    message: str
    blocking: bool


class TurnTaken(NamedTuple):
    """A turn the monitor produced content on, which raises nothing and asks nobody.

    Its own type rather than a `None`, because it is not an absence: it is the answer
    to the ordinary turn, and it is what most turns of a healthy run get. Naming it is
    what stops the branch that answers it from being read as the branch that handles a
    missing value — and what stops the next reader from folding it back into the
    refusal it was folded into before, which is the whole defect.

    Carries nothing, deliberately. Nothing the monitor wrote is read, compared, or
    quoted back: the ruling is composed from this file's own words, so no prose of the
    monitor's can reach a planner by riding out on this path.
    """


class LiveEdit(NamedTuple):
    """A manager's graph edit, recognised where a ruling was expected.

    Both fields are for *saying* what arrived, which is the whole of what this reader
    does with one: the edits themselves are already applied by the time the envelope
    gets here, so nothing is kept to act on. Named rather than positional because the
    two go to different halves of the same sentence.
    """

    #: The edits it carries, named the way a planner would recognise them.
    named: str
    #: Whatever prose rode beside them, or `""`. This reader is the last thing that
    #: could carry it to the monitor.
    said: str


class SupervisorResponse(TypedDict, total=False):
    """The ruling onejudge acts on, and the shape `channel serve` already answers in."""

    completion: bool
    message: str
    reason: str


class CriterionScore(TypedDict):
    """What onejudge's `judge` op reads back: one boolean score and why.

    `rationale` is optional to onejudge and always sent anyway — a bare boolean in a
    transcript says which way the criterion went and nothing about who decided it, and
    here the answer to that is "the planner did", which is the interesting half.
    """

    value: bool
    rationale: str


def fail(problem: str, remedy: str) -> int:
    """Report why no planner ruling could be produced, and what to do about it."""
    print(f"channel-serve: {problem}; {remedy}", file=sys.stderr)
    return 2


def onepipeline_binary(run: RunId) -> str | int:
    """The `onepipeline` this checkout pins, or the one `ONEPIPELINE_BIN` names.

    Resolved from this file's own location rather than from a bare name: a judge
    command is spawned with the run's launch directory as its working directory and
    whatever PATH the graph inherited, so a lookup would let the ambient environment
    decide which release answers the planner.

    An overriding value is resolved to an executable before it is spawned, rather than
    left to fail as it is executed. The seam exists so a journey can point this at a
    stand-in channel, and the environment it is read from is the graph's rather than
    this filter's — so an empty value, a path where nothing is installed, and a file
    without the bit set are all named as the cause here, where what is wrong with them
    can still be said.
    """
    named = os.environ.get(ONEPIPELINE_BIN)
    if named is None:
        pinned = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "onepipeline"
        return str(pinned) if pinned.is_file() else "onepipeline"
    usable = shutil.which(named)
    if usable is None:
        return fail(
            f"could not run `channel serve {run}`: {ONEPIPELINE_BIN} names {named!r}, "
            "which is not an executable this filter can run",
            "restore the pinned toolchain with `just bootstrap`, or point "
            f"{ONEPIPELINE_BIN} at a usable onepipeline",
        )
    return usable


def read_frame(raw: str) -> SupervisorFrame | int:
    """Parse and check onejudge's supervisor frame, or report why it cannot be served."""
    if not raw.strip():
        return fail(
            "onejudge sent no supervisor frame on stdin",
            "this filter is a onejudge judge-side command provider and is not meant to "
            "be run by hand; wire it through `graphs/dag-scope.yaml`",
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        return fail(
            f"the supervisor frame onejudge sent is not JSON ({error})",
            "check the onejudge release against the frame recorded in this file's "
            "header, and re-measure it if the release moved",
        )
    if not isinstance(parsed, dict):
        return fail(
            f"the supervisor frame must be a JSON object, got {type(parsed).__name__}",
            "check the onejudge release against the frame recorded in this file's header",
        )
    frame: SupervisorFrame = parsed
    if frame.get("op") not in SERVED_OPS:
        return fail(
            f"only the {' and '.join(f'`{op}`' for op in SERVED_OPS)} ops reach the planner "
            f"channel, got {frame.get('op')!r}",
            "leave `evals` and `assessment` unset for this member, since neither asks "
            "anything a planner can rule on",
        )
    return frame


def named_run(frame: SupervisorFrame) -> RunId | None:
    """The run this member is watching, as its composed task opens by naming it."""
    task = frame.get("task")
    named = RUN_IN_COMPOSED_TASK.search(task) if isinstance(task, str) else None
    return RunId(named.group(1)) if named is not None else None


def transcript_frames(spoken: str) -> list[TranscriptFrame] | None:
    """The machine transcript this message *is*, or `None` when it is the monitor's prose.

    A turn the monitor completed answers in prose. A turn its agent side lost answers
    with the harness's own stream instead — one JSON object per line and nothing else —
    so "every non-blank line parses as a JSON object" is what tells the two apart
    without guessing at the vocabulary inside either.
    """
    frames: list[TranscriptFrame] = []
    for line in spoken.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        # `cast` after the `isinstance` above: `TranscriptFrame` is a `TypedDict`, so no
        # runtime check can establish it — the producer owns this shape, and the matches
        # below read it defensively rather than trusting the annotation.
        frames.append(cast(TranscriptFrame, parsed))
    return frames or None


def lost_turn_error(frames: list[TranscriptFrame]) -> TurnError | None:
    """What a lost turn recorded: `{}` if it recorded nothing, `None` if it was not lost.

    Read from the end backwards, because a transcript ends in what became of the turn.
    Two shapes say it was lost and neither is one harness's own: a terminal turn status
    of `failed`, and an error frame. A turn that failed carrying no error object is
    still a turn nobody took, which is why the empty record and the `None` are different
    answers rather than one falsy one.

    Recognising only a failure — never "this looks like a transcript" — is deliberate
    and unchanged: widening this vocabulary is how the next harness's fourth shape
    outruns the classifier, and a cause guessed at sends a planner to the wrong quota.
    A transcript it cannot prove was lost gets no surface at all now: it is content the
    member produced, so the member is answered and lives, and the run keeps the turn
    where `just monitor <run> --filter monitor` reads it. Nothing here guesses.
    """
    for frame in reversed(frames):
        match frame:
            # Both casts: the pattern has already proved `recorded` is a `dict`, and
            # `TurnError` is a total-optional `TypedDict` whose keys are the harness's to
            # populate — every reader re-checks each key, so the cast narrows the
            # annotation without asserting anything the match did not establish.
            case {"params": {"turn": {"status": "failed", "error": dict() as recorded}}}:
                return cast(TurnError, recorded)
            case {"method": "error", "params": {"error": dict() as recorded}}:
                return cast(TurnError, recorded)
            case {"params": {"turn": {"status": "failed"}}} | {"method": "error"}:
                return TurnError()
    return None


def clipped(cause: str) -> str:
    """One cause as a single bounded line, so what is built around it stays readable."""
    named = COLLAPSED_IN_A_CAUSE.sub(" ", cause).strip()
    return named if len(named) <= CAUSE_LIMIT else named[: CAUSE_LIMIT - 1].rstrip() + "\u2026"


def cause_of(error: TurnError) -> str:
    """The shortest true name for why the turn was lost.

    A classification is preferred to prose — `usageLimitExceeded` is the whole answer,
    where the sentence beside it is a paragraph about buying credits — and the prose is
    the fallback rather than the omission, because a harness that classifies nothing
    still says something. Each candidate is normalized before it is judged empty, so a
    value that is only whitespace or control characters falls through to the next.
    """
    for stated in (error.get("codexErrorInfo"), error.get("code"), error.get("message")):
        named = clipped(stated) if isinstance(stated, str) else ""
        if named:
            return named
    return NO_CAUSE_RECORDED


def identity_in(frames: list[TranscriptFrame]) -> str:
    """The harness identity the lost turn ran as, as its own transcript names it.

    Which quota to go and look at is the actionable half of the line, and the two codex
    identities have separate ones. The transcript names the home it was credentialed
    from, so the alternate is recognised by comparing that against
    `ORCHESTRATOR_CODEX_ALT_HOME` — every launch exports it, and
    `scripts/codex-alt-home.sh` owns the path — rather than by a second copy of it here.
    A transcript naming no home at all is reported as naming none.
    """
    for frame in frames:
        result = frame.get("result")
        home = result.get(CODEX_HOME_IN_TRANSCRIPT) if isinstance(result, dict) else None
        if isinstance(home, str) and home.strip():
            alternate = os.environ.get(CODEX_ALT_HOME)
            named_the_alternate = alternate is not None and os.path.normpath(
                alternate
            ) == os.path.normpath(home)
            return CODEX_ALTERNATE_IDENTITY if named_the_alternate else CODEX_IDENTITY
    return UNIDENTIFIED_HARNESS


def failed_turn_surface(
    error: TurnError, frames: list[TranscriptFrame], spoken: str, run: RunId
) -> ObserverFrame:
    """One lost turn, named rather than transcribed.

    The transcript itself stays out of the message on purpose: this is the line the
    planner cannot filter, and the run already keeps the turn where the remedy points.
    """
    return ObserverFrame(
        kind=SURFACE_KIND_OF_A_FAILED_TURN,
        message=(
            f"monitor turn failed: {cause_of(error)} on {identity_in(frames)}. "
            f"It said nothing, so there is nothing to answer; its {len(spoken)}-character "
            f"transcript is not repeated here. "
            f"Read it with `just monitor {run} --filter monitor`."
        ),
        blocking=False,
    )


def ruling_for_a_turn_taken() -> SupervisorResponse:
    """Answer a monitor that took its turn, without asking the planner anything.

    Composed here rather than served, because there is nothing to serve: no surface was
    raised, so no planner was asked and there is no ruling of theirs to relay. That is
    not an answer on their behalf — a non-completion settles nothing, completes nothing,
    and rules on no work. What it does is keep the member alive, which is the whole of
    the fix: `fail()` here exits non-zero, `oneagentgraph` records that as `member-died
    {"rule":"provider-failure","cause":"protocol"}`, and the run goes on reporting
    `ACTIVE` with nothing watching it.
    """
    return SupervisorResponse(
        completion=False,
        message=TURN_TAKEN_ACKNOWLEDGED,
        reason=TURN_TAKEN_REASON,
    )


def answer_for(frame: SupervisorFrame, run: RunId) -> ObserverFrame | TurnTaken | int:
    """What one supervisor frame gets: a surface, a bare acknowledgement, or a refusal.

    `TurnTaken` is the ordinary answer and the one a healthy run gets on nearly every
    turn: the monitor produced content, so it is alive, and nothing is raised — its
    report, if it had one, is already on the channel as a `finding` op it issued itself.
    Nothing about what it said is read beyond that it said something, which is the whole
    of the reporting policy this file now has.

    The two exceptions are both about a turn the monitor did not take. A frame with no
    assistant content at all is refused, because that is a real provider defect. And a
    last message that `transcript_frames` reads as the harness's own stream, with a
    failure `lost_turn_error` can *prove* inside it, is the same defect wearing content:
    it raises the one surface left on this path. A transcript nothing can be proven
    about is left alone as content, because the bound that used to be put on it existed
    only to keep republished prose off the channel and there is no republished prose.
    """
    messages = frame.get("messages")
    if not isinstance(messages, list):
        return fail(
            f"the supervisor frame for run {run} carries no `messages` conversation",
            "check the onejudge release against the frame recorded in this file's header",
        )
    spoken = [
        message["content"]
        for message in messages
        if isinstance(message, dict)
        and message.get("role") == "assistant"
        and isinstance(message.get("content"), str)
        and message["content"].strip()
    ]
    if not spoken:
        return fail(
            f"the monitor said nothing at all for run {run} — no assistant message — "
            "which is a turn its agent side lost rather than a turn that found nothing; "
            "a monitor with no finding still takes its turn and says something, and is "
            "answered without a surface",
            f"read its turns with `just monitor {run} --filter monitor` to see why the "
            "turn produced no message",
        )
    said = spoken[-1]
    frames = transcript_frames(said)
    if frames is not None:
        lost = lost_turn_error(frames)
        if lost is not None:
            return failed_turn_surface(lost, frames, said, run)
    return TurnTaken()


def ruling_from(answer: str, run: RunId) -> SupervisorResponse | int:
    """Check the channel's answer is a ruling onejudge can act on before relaying it."""
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError as error:
        return fail(
            f"the planner channel answered run {run} with something that is not JSON ({error})",
            "check the onepipeline release against the response recorded in this file's "
            "header, and re-measure it if the release moved",
        )
    if not isinstance(parsed, dict) or not isinstance(parsed.get("completion"), bool):
        return fail(
            f"the planner channel's answer for run {run} is not a supervisor ruling: "
            f"{answer[:200]}",
            "a ruling is a JSON object carrying a boolean `completion`; check the "
            "onepipeline release against this file's header",
        )
    # llmlint: ignore[boundary_inputs_validated] `message` and `reason` are checked for
    # type but never for presence, deliberately: `scripts/planner-verdict.sh` renders an
    # approve as `{"completion": true, "reason": "approved"}` with no `message` at all,
    # so requiring either field would refuse this repository's own published verdict and
    # kill the monitor on the first approval. `completion` is the ruling; the prose
    # beside it is optional to onejudge, and that is the boundary this validates.
    for optional in ("message", "reason"):
        if optional in parsed and not isinstance(parsed[optional], str):
            return fail(
                f"the planner channel's `{optional}` for run {run} is not text: "
                f"{parsed[optional]!r}",
                f"`{optional}` is prose onejudge hands to the monitor; check the "
                "onepipeline release against this file's header",
            )
    # Fields beyond `completion`, `message`, and `reason` are relayed rather than refused
    # or stripped, deliberately: onejudge — not this filter — decides what a ruling may
    # carry, so refusing them here would kill the monitor on an additive onepipeline
    # release that onejudge itself is happy with, and stripping them would quietly
    # withhold from onejudge what the planner sent it. The three fields onejudge acts on
    # are all checked above; nothing that reaches it unchecked can change the ruling,
    # which is the boundary this validates.
    # llmlint: ignore[boundary_inputs_validated] onejudge, not this filter, rules on extras.
    return parsed


def live_edit(answer: str) -> LiveEdit | None:
    """The graph edits this answer carries, when it is not a ruling for this reader.

    `None` for everything else, including an answer that is not JSON at all: what is
    wrong with those is `ruling_from`'s to report, and recognising only what this can
    prove is a live edit keeps a malformed ruling from being quietly re-addressed to
    the engine.

    A boolean `completion` is what makes an answer a ruling, so an envelope carrying
    one is left alone however many edits ride with it — onejudge can act on it, and
    relaying it is what this reader is for. What is recognised here is the envelope
    with edits and no verdict: `onepipeline`'s reconciler is the reader that grammar
    belongs to, and it reached this one only because a durable queue hands each reply
    to whoever asks first.
    """
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or isinstance(parsed.get(RULING_VERDICT), bool):
        return None
    commands = parsed.get(LIVE_EDIT_COMMANDS)
    if not isinstance(commands, list) or not commands:
        return None
    said = " ".join(
        parsed[field].strip()
        for field in PLANNER_PROSE
        if isinstance(parsed.get(field), str) and parsed[field].strip()
    )
    return LiveEdit(named=named_edits(commands), said=said)


def named_edits(commands: list[object]) -> str:
    """The edits an envelope carries, named as the planner who sent them would.

    Read defensively and bounded, because this is somebody else's payload rendered
    into a line a planner acts on: `onepipeline` owns what a command may be, and a
    command that names no op is reported as naming none rather than as `None`.
    """
    named = []
    for command in commands:
        stated = command.get(EDIT_OP) if isinstance(command, dict) else None
        target = command.get(EDIT_TARGET) if isinstance(command, dict) else None
        spelled = stated if isinstance(stated, str) and stated.strip() else UNNAMED_EDIT
        if isinstance(target, str) and target.strip():
            spelled = f"{spelled} {target.strip()}"
        named.append(spelled)
    return clipped(", ".join(named))


def ruling_for_a_live_edit(edit: LiveEdit) -> SupervisorResponse:
    """Tell the monitor what became of its surface, and leave the edit alone.

    Always a non-completion, and never an exit status: the member has to survive an
    answer that was never meant for it, and a completion would settle a watch nobody
    ruled on. Deliberately inert about the edit itself — see this file's header for the
    measurement that says why re-sending it would apply it twice.
    """
    message = ANSWERED_THE_ENGINE.format(named=edit.named)
    if edit.said:
        message += PLANNER_ALSO_SAID.format(said=edit.said)
    return SupervisorResponse(completion=False, message=message, reason=NOT_A_RULING)


def served_by_the_planner(
    surface: ObserverFrame, run: RunId, binary: str
) -> SupervisorResponse | int:
    """Raise one surface and hand back the ruling the planner answered it with.

    The whole round trip both ops share: a surface out, a ruling back, and every way
    that can fail reported as a cause and a remedy rather than as an answer. A live edit
    claimed here is recognised as not this reader's and turned into the non-completion
    that keeps the member alive — see `ruling_for_a_live_edit` and this file's header.
    """
    try:
        served = subprocess.run(
            [binary, "channel", "serve", run],
            input=json.dumps(surface, ensure_ascii=False) + "\n",
            text=True,
            capture_output=True,
            check=False,
        )
    # `ValueError` beside `OSError` is what makes this boundary total: `subprocess.run`
    # raises it, not `OSError`, for an argv word it cannot encode. The checks above are
    # what stop one reaching here, and this is the guarantee that no path out of this
    # filter is a traceback instead of a cause and a remedy.
    except (OSError, ValueError) as error:
        return fail(
            f"could not run `{binary} channel serve {run}` ({error})",
            "restore the pinned toolchain with `just bootstrap`, or point "
            f"{ONEPIPELINE_BIN} at a usable onepipeline",
        )

    if served.returncode != 0:
        return fail(
            f"the planner channel refused the surface for run {run}: "
            f"{served.stderr.strip() or f'exit {served.returncode}'}",
            f"check the run id with `just runs`, and that it is still live with "
            f"`just status {run}`",
        )
    if not served.stdout.strip():
        return fail(
            f"the planner channel closed without answering the surface for run {run}",
            f"check with `just status {run}` whether the run settled while the monitor "
            "was waiting, which leaves nothing to answer it",
        )
    answer = served.stdout.strip()
    # An answer addressed to the engine's reconciler rather than to this reader is
    # recognised and survived, not refused: exiting here is what killed the monitor of
    # every run whose manager corrected it. See this file's header.
    addressed_elsewhere = live_edit(answer)
    if addressed_elsewhere is not None:
        return ruling_for_a_live_edit(addressed_elsewhere)
    return ruling_from(answer, run)


def scored(frame: SupervisorFrame, run: RunId, binary: str) -> CriterionScore | int:
    """Put this member's completion bar to the planner, and relay how they ruled.

    A boolean criterion only: a planner ruling is a `completion` boolean, so it relays
    onto a boolean score and would have to be invented onto anything else. Nothing here
    can arrive from this repository's graphs — a non-boolean `judge` comes only from
    `evals`, which no channel-served persona may carry — so this is the boundary rather
    than a branch anybody takes.
    """
    kind = frame.get("kind")
    criterion = frame.get("criterion")
    if kind != BOOLEAN_SCORE or not isinstance(criterion, str) or not criterion.strip():
        return fail(
            f"the planner channel scores a `{BOOLEAN_SCORE}` criterion and run {run} was "
            f"asked for {kind!r} ({criterion!r})",
            "leave `evals` unset for this member: a planner rules with a `completion` "
            "boolean, which is not a score on a scale",
        )
    ruled = served_by_the_planner(
        ObserverFrame(
            kind=SURFACE_KIND_OF_A_COMPLETION,
            message=SCORE_ASKED.format(criterion=criterion.strip()),
            blocking=False,
        ),
        run,
        binary,
    )
    if isinstance(ruled, int):
        return ruled
    said = ruled.get("reason") or ruled.get("message") or ""
    return CriterionScore(value=ruled["completion"], rationale=said.strip() or SCORE_UNEXPLAINED)


def main() -> int:
    frame = read_frame(sys.stdin.read())
    if isinstance(frame, int):
        return frame
    scoring = frame.get("op") == SCORE_OP
    # Each op is held to its OWN source for the run, and they are different sources: the
    # composed task is a contract this filter already validates, while the scoring frame
    # carries no `task` at all and leaves the environment as the only place `onepipeline`
    # names the run to it. Reporting them separately is what keeps each refusal pointed
    # at the thing its own caller can fix.
    if scoring:
        named = os.environ.get(RUN_ID_ENV, "")
        if not named:
            return fail(
                f"{RUN_ID_ENV} names no run, and a `{SCORE_OP}` frame carries no task to "
                "read one out of, so there is no channel to serve",
                f"{RUN_ID_ENV} is exported to both sides of an observer member; check "
                "the onepipeline release against this file's header if it is missing",
            )
    else:
        found = named_run(frame)
        if found is None:
            return fail(
                "the composed task does not name its run, so there is no channel to serve",
                "give this member no `task` of its own, or open one with `{task}`, so the "
                "run-level task reaches it",
            )
        named = found
    run = RunId(named)
    if SAFE_RUN_ID.match(run) is None:
        return fail(
            f"the run is named {run!r}, which this filter will not pass to "
            "`onepipeline channel serve` as a run",
            "a run id is one word of letters, digits, `_`, `.`, and `-`; check the "
            f"run-level task's opening line, or {RUN_ID_ENV}, against `just runs`",
        )

    binary = onepipeline_binary(run)
    if isinstance(binary, int):
        return binary
    if scoring:
        score = scored(frame, run, binary)
        if isinstance(score, int):
            return score
        print(json.dumps(score, ensure_ascii=False))
        return 0

    answer = answer_for(frame, run)
    match answer:
        case int():
            return answer
        # An ordinary turn is answered here and the channel is never opened, which is
        # the half that matters as much as the member surviving: raising the monitor's
        # prose as a surface is what queued eight duplicate updates behind nineteen
        # findings on one run, burying the blocking questions sharing that queue.
        case TurnTaken():
            print(json.dumps(ruling_for_a_turn_taken(), ensure_ascii=False))
            return 0
    ruling = served_by_the_planner(answer, run, binary)
    if isinstance(ruling, int):
        return ruling
    # Re-serialized from the ruling this validated rather than echoed through, so
    # nothing reaches onejudge that was not checked to be a ruling.
    print(json.dumps(ruling, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
