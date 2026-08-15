#!/usr/bin/env python3
"""Serve the planner channel as the monitor member's judge side.

`onepipeline channel serve RUN` is the published server side of the planner
channel: it takes one observer frame on stdin, queues it as a planner surface,
blocks until the planner answers, and writes that answer to stdout. onejudge's
judge-side command provider is the other end of the same conversation: it writes
one supervisor frame to a command's stdin and reads one response object back.

The two halves already agree on the **response**. `channel serve` answers with
exactly `{"completion": ..., "message": ..., "reason": ...}`, which is the object
onejudge's `supervisor` op expects, so nothing here touches it — it is passed
through byte for byte. What the two do not agree on is the **request**, and this
filter is that one reconciliation, as onejudge 0.3.10 writes it and `onepipeline`
0.5.0 reads it:

    onejudge  ->  {"op": "supervisor", "task", "persona", "done_when",
                   "worktree", "history_name", "messages": [...], "session"}
    serve     <-  {"kind", "message", "blocking"?, "node"?}

Handing onejudge's frame over unchanged is refused by name — `the observer emitted
a bad frame: unknown field 'op'` — and the member then dies with `provider produced
no output`, which is how a graph that wired the two together directly loses its
monitor on the first turn while the run carries on undriven by it.

Two values have to be recovered from that frame rather than read from the
environment, and both are recovered from what the frame itself carries:

* **The run id.** `onepipeline` exports no variable naming it to an observer
  member — measured by dumping the judge command's whole environment on a real
  launch — but the task it composes for the graph opens by naming the run, so the
  id is read from there.
* **What to surface.** The last assistant message of the conversation is what the
  monitor just said, which is the thing the planner is being asked to answer.

The surface is raised **non-blocking**. Blocking it would hold the run at
`awaiting-planner` on every monitor turn, which would end the attached launch's
settle-and-return contract and stop the frontier to ask a question about
watching rather than about work. A planner who never answers leaves the monitor
waiting, which costs the run nothing: it is a watcher, and the engine drives the
graph without it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

#: How the composed dag-scope task names its run, on its first line:
#: ``onepipeline run `scheduler-research`.``. That opening is the published
#: composed-task contract for this graph, and the only place an observer member is
#: told which run it is watching.
RUN_IN_COMPOSED_TASK = re.compile(r"onepipeline run `([^`]+)`")

#: The surface kind a monitor's supervisor boundary is raised under. `channel
#: serve` takes a free-form kind here, unlike `onepipeline surface --kind`, so this
#: names what the surface *is* rather than borrowing the pacemaker's word for it.
SURFACE_KIND = "monitor"


def fail(message: str) -> int:
    """Report why no supervisor answer can be produced, the way onejudge reads it."""
    print(f"channel-serve: {message}", file=sys.stderr)
    return 2


def onepipeline_binary() -> str:
    """The `onepipeline` this checkout pins, or whatever a PATH lookup finds.

    Resolved from this file's own location rather than the caller's environment: a
    judge command is spawned by `oneagentgraph` with the member's scratch as its
    working directory, so a relative path or an inherited PATH would decide which
    release answers the planner.
    """
    pinned = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "onepipeline"
    return str(pinned) if pinned.is_file() else "onepipeline"


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return fail("onejudge sent no supervisor frame on stdin")
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError as error:
        return fail(f"the supervisor frame onejudge sent is not JSON: {error}")
    if not isinstance(frame, dict):
        return fail(f"the supervisor frame must be a JSON object, got {type(frame).__name__}")

    operation = frame.get("op")
    if operation != "supervisor":
        return fail(
            f"only the `supervisor` op reaches the planner channel, got {operation!r}; "
            "an eval or assessment call has no planner question to ask"
        )

    task = frame.get("task")
    named = RUN_IN_COMPOSED_TASK.search(task) if isinstance(task, str) else None
    if named is None:
        return fail(
            "the composed task does not name its run, so there is no channel to serve; "
            "it must open `onepipeline run `<run-id>``"
        )
    run = named.group(1)

    messages = frame.get("messages")
    if not isinstance(messages, list):
        return fail("the supervisor frame carries no `messages` conversation")
    said = [
        message.get("content")
        for message in messages
        if isinstance(message, dict) and message.get("role") == "assistant"
    ]
    spoken = [content for content in said if isinstance(content, str) and content.strip()]
    if not spoken:
        return fail(
            f"the monitor said nothing for run {run}, so there is nothing to ask the planner about"
        )

    surface = json.dumps(
        {"kind": SURFACE_KIND, "message": spoken[-1], "blocking": False},
        ensure_ascii=False,
    )
    try:
        served = subprocess.run(
            [onepipeline_binary(), "channel", "serve", run],
            input=surface + "\n",
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        return fail(f"could not run `onepipeline channel serve {run}`: {error}")

    if served.returncode != 0:
        detail = served.stderr.strip() or f"exit {served.returncode}"
        return fail(f"the planner channel refused the surface for run {run}: {detail}")

    answer = served.stdout.strip()
    if not answer:
        return fail(
            f"the planner channel closed without answering the surface for run {run}; "
            "the run may have settled while the monitor was waiting"
        )
    # Passed through unchanged: this is already onejudge's supervisor response object,
    # and re-encoding it here would make this filter an author of verdicts.
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
