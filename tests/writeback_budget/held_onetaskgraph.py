#!/usr/bin/env python3
"""A hold in front of the real `onetaskgraph`'s `project copy`, and a record of each call.

`onepipeline` spawns the plan-store CLI that ``ONETASKGRAPH_BIN`` names, so pointing that
here puts this file at exactly the seam the engine's settlement write-back reaches the
store through. Everything is handed to the real binary afterwards: the store on disk, the
items a copy writes and the answer the engine reads are all onetaskgraph's own. The one
thing added is **time** — how long a `project copy` takes before it starts — which is the
single variable a journey about the copy's deadline needs and the real store cannot be
asked for. A store made slow this way is still a correct store, where a store made slow by
breaking it would be measuring a different failure.

Three environment variables steer it:

* ``HELD_ONETASKGRAPH_REAL`` names the real binary every call is handed to.
* ``HELD_ONETASKGRAPH_HOLD`` names a file. When it exists as a `project copy` starts, the
  copy waits the whole number of seconds the file holds before the real binary runs; when
  it does not, the copy is not held. Read per call, so a journey moves the hold between
  copies of one live run without restarting anything.
* ``HELD_ONETASKGRAPH_LOG`` names a file this appends one JSON line to as each call starts
  and another as it answers, each carrying the call's whole argument list. A call the
  engine killed inside its hold has a `started` line and no `answered` one, which is how a
  journey tells a copy that was allowed to finish from one that was not, without trusting
  the engine's account of its own deadline; and the arguments are how a journey reads
  which tasks a copy named, without trusting the engine's account of what it carried.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REAL_ENV = "HELD_ONETASKGRAPH_REAL"
HOLD_ENV = "HELD_ONETASKGRAPH_HOLD"
LOG_ENV = "HELD_ONETASKGRAPH_LOG"

COPY = ("project", "copy")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"held_onetaskgraph: {name} must name a value", file=sys.stderr)
        raise SystemExit(2)
    return value


def _record(log: Path, **fields: object) -> None:
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "at": time.time(), **fields}) + "\n")


def _held_seconds(hold: Path) -> int:
    """How long this copy waits: what the hold file says, or nothing when there is none."""
    try:
        return int(hold.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return 0


def main() -> int:
    real = _required(REAL_ENV)
    log = Path(_required(LOG_ENV))
    hold = Path(_required(HOLD_ENV))
    arguments = sys.argv[1:]
    verb = " ".join(arguments[:2])
    held = _held_seconds(hold) if tuple(arguments[:2]) == COPY else 0
    _record(log, event="started", verb=verb, args=arguments, held=held)
    time.sleep(held)
    answered = subprocess.run([real, *arguments], check=False)
    _record(log, event="answered", verb=verb, args=arguments, held=held, exit=answered.returncode)
    return answered.returncode


if __name__ == "__main__":
    raise SystemExit(main())
