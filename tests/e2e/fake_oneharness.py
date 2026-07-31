"""A stand-in `oneharness` CLI that serves a history store a test wrote.

The monitor's history source *shells out* to `oneharness history list`, so covering
how it parses that response means driving the real subprocess boundary rather than
patching the function behind it. ``--mock-harness`` can create real history only for
the mock runs it launches; it cannot seed the arbitrary canned session shapes,
timestamps, and labels these history-reader tests must consume. This serves that
recorded store while preserving the subprocess boundary.

`FAKE_ONEHARNESS_STORE` names a JSON file holding ``{"sessions": [...]}`` exactly as
``oneharness history list --all-projects --format json`` emits it. Keep this
deterministic and stdlib-only — it is spawned as a subprocess.

Two optional variables let a test observe and shape the *cost* of that boundary,
which is the point when what is under test is how often a reader crosses it and
whether it blocks anything else meanwhile:

``FAKE_ONEHARNESS_INVOCATION_LOG``
    A file each invocation appends its argument line to, before doing anything
    else, so a test can count real crossings and tell when one is in flight.
``FAKE_ONEHARNESS_DELAY_SECONDS``
    How long to stall before answering — the real command reads the whole store
    and takes about a second, which the recorded one otherwise hides.
"""

from __future__ import annotations

import json
import os
import sys
import time


def main(argv: list[str]) -> int:
    log = os.environ.get("FAKE_ONEHARNESS_INVOCATION_LOG")
    if log is not None:
        # One short line per open/append: concurrent invocations must each be
        # recorded whole, and a lone write below the pipe-buffer size is.
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(" ".join(argv) + "\n")
    if delay := os.environ.get("FAKE_ONEHARNESS_DELAY_SECONDS"):
        time.sleep(float(delay))
    store = os.environ.get("FAKE_ONEHARNESS_STORE")
    if store is None:
        print("fake-oneharness: FAKE_ONEHARNESS_STORE is not set", file=sys.stderr)
        return 2
    if argv[:2] != ["history", "list"]:
        print(f"fake-oneharness: unsupported invocation {argv}", file=sys.stderr)
        return 2
    with open(store, encoding="utf-8") as handle:
        recorded = json.load(handle)
    print(json.dumps(recorded["sessions"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
