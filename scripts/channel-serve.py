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
and this filter is that one reconciliation, as onejudge 0.4.0 writes it and
`onepipeline` 0.8.1 reads it:

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
  measured against onepipeline 0.8.1 by dumping this command's whole environment on
  a real launch — but that is a per-release export while the composed task is the
  contract this filter already validates, so the task stays the source. The export
  is gated by `tests/e2e/test_orchestrate_launch_e2e.py`, which stands a probe where
  this file stands and re-takes the measurement rather than trusting this paragraph.
* **What to surface.** The last assistant message of the conversation is what the
  monitor just said, which is the thing the planner is being asked to answer.

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
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NewType, TypedDict

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

#: The surface kind a monitor's supervisor boundary is raised under. `channel
#: serve` takes a free-form kind here, unlike `onepipeline surface --kind`, so this
#: names what the surface *is* rather than borrowing the pacemaker's word for it.
SURFACE_KIND = "monitor"

#: Which `onepipeline` answers the planner. Named so the binary is a seam a journey
#: can drive, exactly as `ONEAGENTGRAPH_ONEHARNESS_BIN` is for a dispatch; unset, the
#: release this checkout pins is used.
ONEPIPELINE_BIN = "ONEPIPELINE_BIN"


class ConversationMessage(TypedDict, total=False):
    """One turn of the monitor's conversation, as onejudge writes it into the frame.

    `total=False` for the same reason as the frame that carries it: a message missing
    `role` or `content`, or carrying something other than text, is checked for at the
    one place it is read rather than assumed away by this declaration.
    """

    role: str
    content: str


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


class ObserverFrame(TypedDict):
    """What `onepipeline channel serve` reads: one surface to raise for the planner."""

    kind: str
    message: str
    blocking: bool


class SupervisorResponse(TypedDict, total=False):
    """The ruling onejudge acts on, and the shape `channel serve` already answers in."""

    completion: bool
    message: str
    reason: str


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
    if frame.get("op") != "supervisor":
        return fail(
            f"only the `supervisor` op reaches the planner channel, got {frame.get('op')!r}",
            "leave `evals` and `assessment` unset for this member, since neither has a "
            "planner question to ask",
        )
    return frame


def named_run(frame: SupervisorFrame) -> RunId | None:
    """The run this member is watching, as its composed task opens by naming it."""
    task = frame.get("task")
    named = RUN_IN_COMPOSED_TASK.search(task) if isinstance(task, str) else None
    return RunId(named.group(1)) if named is not None else None


def surface_for(frame: SupervisorFrame, run: RunId) -> ObserverFrame | int:
    """Turn one supervisor frame into the surface the planner is asked to answer."""
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
            f"the monitor said nothing for run {run}, so there is nothing to ask the planner about",
            f"read its turns with `just monitor {run} --filter monitor` to see why the "
            "turn produced no message",
        )
    return ObserverFrame(kind=SURFACE_KIND, message=spoken[-1], blocking=False)


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


def main() -> int:
    frame = read_frame(sys.stdin.read())
    if isinstance(frame, int):
        return frame
    run = named_run(frame)
    if run is None:
        return fail(
            "the composed task does not name its run, so there is no channel to serve",
            "give this member no `task` of its own, or open one with `{task}`, so the "
            "run-level task reaches it",
        )
    if SAFE_RUN_ID.match(run) is None:
        return fail(
            f"the composed task names {run!r}, which this filter will not pass to "
            "`onepipeline channel serve` as a run",
            "a run id is one word of letters, digits, `_`, `.`, and `-`; check the "
            "run-level task's opening line against the runs `just runs` lists",
        )
    surface = surface_for(frame, run)
    if isinstance(surface, int):
        return surface

    binary = onepipeline_binary(run)
    if isinstance(binary, int):
        return binary
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
    ruling = ruling_from(served.stdout.strip(), run)
    if isinstance(ruling, int):
        return ruling
    # Re-serialized from the ruling this validated rather than echoed through, so
    # nothing reaches onejudge that was not checked to be a ruling.
    print(json.dumps(ruling, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
