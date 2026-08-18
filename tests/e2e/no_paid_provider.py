#!/usr/bin/env python3
"""A `claude` that refuses the turn instead of spending a paid subscription.

`ONEHARNESS_BIN_*` keys on a harness id and no spelling of it reaches a variant, so
it cannot cover `claude-code:alternate`, `:alternate2`, or `:primary` — the identities
a journey only reaches by accident, and where a billed run and a free one look
identical from its assertions. `PATH` is the one seam every variant does share, so
`no-paid-provider/claude` symlinks here and that directory goes first.

Keep this deterministic and stdlib-only — this file *is* the provider binary.
"""

from __future__ import annotations

import sys

#: What a version probe is spelled as. `--version` is claude-code's own; `-v` is
#: accepted beside it so a probe that abbreviates is not read as a turn.
VERSION_PROBE = frozenset({"--version", "-v"})

#: A version this stand-in claims. Answering the probe is deliberate: a `claude` that
#: failed one is classified as not installed and its candidate is skipped, which proves
#: nothing about routing. The value itself is never asserted on.
VERSION = "0.0.0 (no-paid-provider guard)"

#: The refusal, and the string a journey matches to prove the routing failed loudly.
#: It names the file so an operator reading a failed run finds this seam directly.
REFUSAL = (
    "no-paid-provider: a turn was routed to the claude-code provider, which this "
    "suite never intends to reach; tests/e2e/no-paid-provider/claude refused it "
    "rather than spend a paid subscription"
)


def main(argv: list[str]) -> int:
    if VERSION_PROBE.intersection(argv):
        print(VERSION)
        return 0
    print(REFUSAL, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
