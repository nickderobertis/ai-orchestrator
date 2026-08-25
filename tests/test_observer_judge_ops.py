"""The bar no persona can decline is one the planner channel still serves.

onejudge asks a judge side three things, and only one of them is a supervisor ruling.
Two — `evals` and `assessment` — a persona can decline, and
`tests/test_planner_channel_personas.py` is where every channel-served member is held to
declining them. The third cannot be declined at all, which is why it is closed the other
way round and gated here.

`oneagentgraph` merges a persona's `user.done_when` as a *second* bar alongside the
base's rather than over it, so a null one adds nothing, and `user.done_when_replaces_base`
is refused outright with nothing to replace it with. A `kind: onejudge` member therefore
always carries a bar it is always asked to score, once the conversation ends, whether the
supervisor ruled complete or the turn cap ran out — measured against onejudge 0.5.3 with
a `kind: command` judge that logged every op it was asked, and independently of the two
keys a persona can decline. So `done_when` is closed by being
**served**: `scripts/channel-serve.py` raises the criterion to the planner and relays the
`completion` they rule with as the score, which is the only answer that is not an
invention, since the planner is that member's judge side.

That asymmetry is why `assessment: null` alone was not the fix. It removed `assess` and
left the monitor dying at the very end of every run on `got 'judge'`. Each half alone
leaves the member dying, so they are gated apart: declining what can be declined is a
property of the personas, and going on serving what cannot is a property of the filter.
A change that narrowed the filter back to `supervisor` alone would reintroduce a death
nothing else here would catch — the member would start, watch the whole run, and die at
settlement.
"""

from __future__ import annotations

import re

from orchestrator.root import REPO_ROOT

#: The bar that cannot be declined, and the op onejudge asks its judge side to score it
#: with. Written as a path because it is nested, which is the only reason the reader
#: below walks one rather than reading a column-0 key.
UNDECLINABLE = "user.done_when"
SCORED_AS = "judge"

#: The filter that has to go on serving that op, how it declares which ops it serves,
#: and how it binds a name in that declaration to the op it stands for. Read out of the
#: script rather than restated: this gate's whole job is that the two agree, and the
#: script is the one that has to be right.
CHANNEL_FILTER_SCRIPT = REPO_ROOT / "scripts" / "channel-serve.py"
SERVED_OPS = re.compile(r"SERVED_OPS = \((?P<ops>[^)]*)\)")
OP_CONSTANT = re.compile(r'^(?P<name>[A-Z_]+) = "(?P<op>[a-z-]+)"$', re.MULTILINE)


def test_the_bar_no_persona_can_decline_is_one_the_filter_still_serves() -> None:
    """The other half, and the one a persona cannot close for itself.

    Every channel-served member carries a `user.done_when` whether or not it asks for
    one — a persona's is merged as a second bar rather than over the base's, and
    `done_when_replaces_base` is refused with nothing to replace it with — and onejudge
    asks its judge side to score that bar once the conversation ends. So the member's
    survival rests on the filter going on serving that op, and a change that narrowed it
    back to `supervisor` alone would reintroduce a death nothing else here would catch:
    the member would start, watch the whole run, and die at settlement.
    """
    script = CHANNEL_FILTER_SCRIPT.read_text(encoding="utf-8")
    served = SERVED_OPS.search(script)
    assert served is not None, (
        f"{CHANNEL_FILTER_SCRIPT.name} no longer declares SERVED_OPS, so which ops it "
        "answers cannot be read; update this gate together with that declaration"
    )
    # The declaration names constants, so each is resolved to the op it stands for. A
    # name that binds to nothing is left as itself, which fails below by name rather
    # than being silently read as some other op.
    bound = {found.group("name"): found.group("op") for found in OP_CONSTANT.finditer(script)}
    answers = {bound.get(name.strip(), name.strip()) for name in served.group("ops").split(",")}

    assert SCORED_AS in answers, (
        f"{CHANNEL_FILTER_SCRIPT.name} serves {sorted(answers)}, which does not include the "
        f"`{SCORED_AS}` op. Every channel-served member carries a `{UNDECLINABLE}` it cannot "
        "decline, onejudge asks that op to score it once the conversation ends, and a "
        "refusal there kills the member at the end of every run it watches"
    )
