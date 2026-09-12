#!/usr/bin/env python3
"""A paid provider's binary that refuses the turn instead of spending a subscription.

`ONEHARNESS_BIN_*` keys on a harness **id** and no spelling of it reaches a variant, so
it cannot cover `claude-code:alternate`, `:alternate2`, `:primary`, or `codex:alternate`
— the identities a journey only reaches by accident, and where a billed run and a free
one look identical from its assertions. `PATH` is the one seam every variant of every
family does share, so `no-paid-provider/` holds one entry per provider binary, each a
symlink here, and that directory goes first.

Both families, because either one alone leaves a hole: the guard covered `claude` only,
so every `codex` variant of a chain resolved the real binary. Measured on one publication
journey with the drafting turn live: unmodified it ran 10m25s against a real provider and
was heading into its own 720-second bound; with the provider substituted it passed in
2m10s.

Keep this deterministic and stdlib-only — this file *is* the provider binary.
"""

from __future__ import annotations

import os
import sys

#: What a version probe is spelled as. `--version` is what both families answer; `-v` is
#: accepted beside it so a probe that abbreviates is not read as a turn.
VERSION_PROBE = frozenset({"--version", "-v"})

#: A version this stand-in claims. Answering the probe is deliberate: a provider binary
#: that failed one is classified as not installed and its candidate is skipped, which
#: proves nothing about routing. The value itself is never asserted on.
VERSION = "0.0.0 (no-paid-provider guard)"

#: The refusal, and the string a journey matches to prove the routing failed loudly. It
#: names the directory so an operator reading a failed run finds this seam directly, and
#: it says nothing about *which* provider was reached — the invoked name is appended
#: below, so one constant stays the substring every family's refusal carries.
REFUSAL = (
    "no-paid-provider: a turn was routed to a paid provider, which this suite never "
    "intends to reach; tests/e2e/no-paid-provider/ refused it rather than spend a paid "
    "subscription"
)


#: The harness id whose `ONEHARNESS_BIN_*` seam each provider binary belongs to. That
#: seam is what a journey uses to script the *bare* id's provider, and it reaches no
#: variant — so the variants of the same family arrive here instead, and a journey that
#: has already declared its stand-in should get that stand-in rather than a refusal.
BIN_ENV = {"claude": "ONEHARNESS_BIN_CLAUDE_CODE", "codex": "ONEHARNESS_BIN_CODEX"}

#: Where a stand-in this will hand a turn to has to live. A journey names its own, so
#: the path is under this repository's tests; anything outside them is refused rather
#: than executed, because the one thing this file exists to prevent is a turn reaching a
#: provider somebody is billed for — and a variable naming an arbitrary binary would be
#: exactly that hole wearing this file's name.
STAND_IN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

#: What is said when a journey declared a stand-in this may not run.
OUTSIDE_THE_SUITE = (
    "no-paid-provider: {variable} names {path!r}, which is outside this repository's "
    "tests, so it is refused rather than run: the one thing this stand-in exists to "
    "prevent is a turn reaching a provider somebody is billed for"
)


def stand_in(invoked: str) -> str | None:
    """The scripted provider this journey already declared for `invoked`'s family."""
    variable = BIN_ENV.get(invoked)
    named = os.environ.get(variable) if variable is not None else None
    if not named:
        return None
    # `realpath`, not `abspath`: the containment check is the whole guard, and a symlink
    # planted under `tests/` pointing at the real provider satisfies a lexical check while
    # executing exactly the binary this file exists to keep a turn away from. Both sides
    # resolve, so a checkout reached through a symlinked path still compares like for like.
    resolved = os.path.realpath(named)
    if os.path.commonpath((resolved, STAND_IN_ROOT)) != STAND_IN_ROOT:
        print(OUTSIDE_THE_SUITE.format(variable=variable, path=named), file=sys.stderr)
        raise SystemExit(1)
    return resolved


def main(argv: list[str], invoked: str) -> int:
    if VERSION_PROBE.intersection(argv):
        print(VERSION)
        return 0
    # A variant of a family whose bare id this journey already scripted. `ONEHARNESS_BIN_*`
    # keys on the id and reaches no variant, so without this a journey that declared its
    # stand-in would still be refused here the moment its chain moved past the first
    # candidate — which every chain does, and which is not what that journey is about.
    scripted = stand_in(invoked)
    if scripted is not None:
        os.execv(scripted, [scripted, *argv])
    print(f"{REFUSAL} (invoked as {invoked})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], os.path.basename(sys.argv[0])))
