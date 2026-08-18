#!/usr/bin/env python3
"""A judge-side command provider that records the environment an observer member ran in.

What `onepipeline` puts in the environment of the agent graph it attaches with
`--dag-graph` is a per-release fact, and this repository states one about it: that
`ONEPIPELINE_RUN_ID` is exported to an observer member, set to the run id. A
statement like that outlives the release it was measured against unless something
measures it again, which is exactly how the opposite claim survived here for as
long as it did.

So this stands where `scripts/channel-serve.py` stands — as the `judge.command` of a
`kind: onejudge` observer member — and writes its own whole environment out for
`tests/e2e/test_orchestrate_launch_e2e.py` to read. It is the same measurement a
maintainer would take by hand, kept in the suite so a release that moves the export
fails a check rather than a paragraph.

It answers with a real ruling rather than exiting non-zero, because a judge command
that fails kills the member: onejudge reads this process's stdout as the planner's,
and the only ruling that leaves the run alone is a non-completion. Nothing here is a
verdict about the run's work — the member it supervises exists for the length of one
launched journey and settles no plan.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TypedDict

#: Where to write the recorded environment. Named by the journey rather than fixed,
#: so each launch reads back the environment of its own observer member.
ENVIRONMENT_PATH_ENV = "OBSERVER_ENVIRONMENT_PATH"


class SupervisorRuling(TypedDict):
    """The object onejudge's `supervisor` op reads back from a judge-side command.

    The same shape `scripts/channel-serve.py` re-serializes onto its own stdout,
    stated here too because this probe stands in that file's place and answers the
    same conversation.
    """

    completion: bool
    message: str
    reason: str


#: What this answers the monitor with. A non-completion, because a completion from a
#: judge side is a ruling that the member's work is finished, and this one only watches.
RULING: SupervisorRuling = {
    "completion": False,
    "message": "recorded the observer environment; keep watching",
    "reason": "this run is a measurement of the observer environment, not a plan to settle",
}


def main() -> int:
    """Record the environment, answer the frame, and leave the run running."""
    # Read the supervisor frame even though nothing here parses it: onejudge writes it
    # to this process's stdin, and exiting without draining it is what would show up as
    # a broken pipe on the writing side rather than as this measurement.
    sys.stdin.read()
    recorded = os.environ.get(ENVIRONMENT_PATH_ENV)
    if not recorded:
        print(f"observer-environment: {ENVIRONMENT_PATH_ENV} names no path", file=sys.stderr)
        return 2
    # Written beside the destination and renamed, because the journey polls for this
    # file: a reader that opened a partial write would see an environment missing
    # whatever had not been flushed yet and report it as an export that is not there.
    destination = Path(recorded)
    staged = destination.with_suffix(f".{os.getpid()}.partial")
    staged.write_text(json.dumps(dict(os.environ), sort_keys=True), encoding="utf-8")
    staged.replace(destination)
    print(json.dumps(RULING))
    return 0


if __name__ == "__main__":
    sys.exit(main())
