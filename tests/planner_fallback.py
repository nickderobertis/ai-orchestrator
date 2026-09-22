# llmlint: ignore-file[code_lands_in_the_domain_that_owns_it] This reader has two consumers in
# two Nx projects — the offline drift gate in `orchestrator:test` and the host-tool journey in
# `ask-seam-planner-fallback-ask:test` — so it cannot live inside either without one project
# importing across the other's boundary. `tests/` is where this repository keeps every such
# cross-tier helper, which the ask-seam journeys already import (`published_tools`,
# `follow_up_variables`, `plan_root_variable`).
"""The ask `personas/planner.yaml` states for a dispatch whose launch exported no shim.

That block is prose in a system prompt and `scripts/ask-manager.sh` is a script, so the
two are one invocation written twice: an offline gate compares the argv and the frame
each composes, and a host-tool journey asks the published bus with each and compares the
question a manager is then handed. Both need the same block, read the same way, so
reading it lives here rather than in either of them.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

#: The fallback, as the persona writes it: a fenced shell block invoking the bus. Read as
#: a fence rather than by line number, so the prose around it can be reworded freely.
FALLBACK_BLOCK = re.compile(r"```sh\n(?P<snippet>.*?)```", re.DOTALL)

#: The question the block carries as its placeholder, which is what makes it runnable as
#: written — and what the shim is handed beside it, so the two frames differ in nothing a
#: comparison is not about.
PLACEHOLDER_QUESTION = "1) ...  2) ..."

#: How the block states the reply window, so a journey can shorten it rather than wait
#: this host's window out. The shim takes the same shortening as the verb's own
#: `--timeout`.
TIMEOUT_OPTION = re.compile(r"--timeout \d+")


def fallback_snippet(persona: Path) -> str:
    """The persona's fallback, dedented out of its block scalar and ready to run.

    Required to be the only fenced block that invokes the bus: a persona stating two of
    them fails here rather than leaving a planner to pick between them.
    """
    stated = persona.read_text(encoding="utf-8")
    blocks = [
        textwrap.dedent(found["snippet"])
        for found in FALLBACK_BLOCK.finditer(stated)
        if "onemessagebus" in found["snippet"]
    ]
    assert len(blocks) == 1, (
        f"{persona.name} states {len(blocks)} shell blocks that invoke the bus, and both "
        "readers of this block want the one fallback a planner with no shim runs"
    )
    return blocks[0]


def shortened_fallback(persona: Path, window: int) -> str:
    """`fallback_snippet`, waiting `window` seconds for the reply instead of its own.

    A substitution that found nothing fails here: a block that stopped stating a
    `--timeout` would otherwise leave a caller waiting out this host's whole window
    without saying so.
    """
    shortened, substituted = TIMEOUT_OPTION.subn(f"--timeout {window}", fallback_snippet(persona))
    assert substituted == 1, (
        f"{persona.name}'s fallback passes {substituted} `--timeout` options, so the "
        "window a caller meant to shorten is not the one it would wait out"
    )
    return shortened
