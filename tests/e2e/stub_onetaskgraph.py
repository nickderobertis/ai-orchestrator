#!/usr/bin/env python3
"""A fault injector in front of the real `onetaskgraph`, for one call and one field.

`onepipeline` spawns the plan-store CLI it is told to by ``ONETASKGRAPH_BIN``, so
pointing that here replaces exactly the delegated published CLI at exactly the seam
the engine reaches it through — the recipe, the wrapper script, the engine, its
write-back worker, and the store on disk all stay real. `just plans` is unaffected
either way: that recipe names the installed binary itself rather than reading this
variable, so a journey can read the store back through the real surface while the
engine is still talking to this.

**It is not a stand-in for the store, and that is the point.** Every invocation is
handed to the real binary; a stubbed one *also* runs for real and has one field
injected into the response it actually returned. So the items, the paging cursor,
the per-source plan and every other byte of a stubbed answer are onetaskgraph's own,
and the shape cannot drift away from the real wire format the way a hand-written
payload would. What is injected is the one thing a journey cannot arrange for real
without breaking the store underneath the run it is measuring: a **partial read**,
which is how the store reports that a source it was asked about could not be reached.

Three environment variables steer it, and each exists because the journey has to tell
one call apart from another deterministically:

* ``STUB_ONETASKGRAPH_REAL`` names the real binary. Required, because this one holds
  the name the engine resolves and there is nothing left on `PATH` to fall back to
  that would not be this file again.
* ``STUB_ONETASKGRAPH_LOG`` names a file this appends one line to per invocation —
  the argv it was called with, and ``!stubbed`` before the ones it injected into. It
  is what lets a journey assert which calls a run made **and which it never reached**,
  which for a refusal is the whole measurement: that nothing was copied is not
  observable from the store, because a store nothing wrote looks exactly like a store
  written with what was already there.
* ``STUB_ONETASKGRAPH_PASS_SHOWS`` — how many ``project show`` invocations to pass
  through untouched before injecting into the rest. A launch reads its own plan
  through this same verb before the run starts, and that read has to succeed or there
  is no run to measure; the write-back's destination read is the next one. The count
  is what separates the two, since the two command lines are identical, and the log
  above is what keeps that separation honest — a journey asserts the sequence it
  actually saw rather than trusting this number to still describe the engine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

#: The real plan-store CLI to hand every invocation to.
REAL_ENV = "STUB_ONETASKGRAPH_REAL"
#: Where one line per invocation is appended.
LOG_ENV = "STUB_ONETASKGRAPH_LOG"
#: How many `project show` calls pass through before the rest are injected into.
PASS_SHOWS_ENV = "STUB_ONETASKGRAPH_PASS_SHOWS"

#: The marker a log line carries when this injected into that call's response.
STUBBED = "!stubbed"

#: The source a partial read is reported against. It is the source these journeys
#: root at a temporary directory, and the one every call here names.
PARTIAL_SOURCE = "authoring"


class SourceError(TypedDict):
    """Why one source could not answer, as onetaskgraph reports it."""

    kind: str
    message: str


class PartialRead(TypedDict):
    """One entry of a store response's `errors` array: a source, and what went wrong.

    Typed rather than a nested literal because it is a cross-process contract — this
    file writes it and `onepipeline`'s write-back reads it — and the whole point of
    injecting into a real response is that the shape stays the store's own. A literal
    would drift from that silently; a model makes the two halves reviewable together.
    """

    source: str
    error: SourceError


#: What onetaskgraph reports for a source it could not reach, and what
#: `onepipeline`'s write-back refuses a destination read for carrying.
PARTIAL_ERROR = PartialRead(
    source=PARTIAL_SOURCE,
    error=SourceError(kind="unavailable", message="stub_onetaskgraph: injected partial read"),
)


def _required(name: str) -> str:
    """One steering variable, or a refusal naming it.

    A refusal rather than a default: a stub that silently did nothing useful would
    make a journey pass by measuring a run that never had a fault injected into it,
    which is the one failure this file must not have.
    """
    value = os.environ.get(name)
    if not value:
        print(f"stub_onetaskgraph: {name} must name a value", file=sys.stderr)
        raise SystemExit(2)
    return value


def _log(line: str) -> None:
    with Path(_required(LOG_ENV)).open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def _shows_so_far() -> int:
    """How many `project show` invocations this has already been called for.

    Counted off the log rather than held in a variable, because each invocation is
    its own process: the engine spawns one per call and nothing survives between
    them but the file.
    """
    log = Path(_required(LOG_ENV))
    if not log.is_file():
        return 0
    return sum(
        1 for line in log.read_text(encoding="utf-8").splitlines() if _is_project_show(line.split())
    )


def _is_project_show(argv: list[str]) -> bool:
    return argv[:2] == ["project", "show"]


def main() -> int:
    """Run the real CLI, and inject a partial read into the answers asked for."""
    argv = sys.argv[1:]
    stubbing = _is_project_show(argv) and _shows_so_far() >= int(_required(PASS_SHOWS_ENV))
    _log(" ".join([STUBBED, *argv] if stubbing else argv))

    completed = subprocess.run(
        [_required(REAL_ENV), *argv],
        text=True,
        capture_output=True,
        check=False,
    )
    if not stubbing or completed.returncode != 0:
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        return completed.returncode

    # The real answer with one field injected, so everything a consumer reads besides
    # `errors` is the store's own and stays that way as the wire format moves.
    answered = json.loads(completed.stdout)
    answered["errors"] = [*answered.get("errors", []), PARTIAL_ERROR]
    print(json.dumps(answered))
    sys.stderr.write(completed.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
