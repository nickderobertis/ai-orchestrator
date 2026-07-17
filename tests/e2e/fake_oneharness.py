"""A stand-in `oneharness` CLI that serves a history store a test wrote.

The monitor's history source *shells out* to `oneharness history list`, so covering
how it parses that response means driving the real subprocess boundary rather than
patching the function behind it. The real CLI can only report sessions it itself
ran, and the sessions this source exists to find are dispatched agent runs — the
paid harness the offline gate cannot spawn. This serves a recorded store instead,
which is what lets a test state the labels and records those sessions would have had.

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
