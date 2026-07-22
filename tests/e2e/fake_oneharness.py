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
"""

from __future__ import annotations

import json
import os
import sys


def main(argv: list[str]) -> int:
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
