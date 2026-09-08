"""Hold an amendment to the criteria bar, before the reply carrying it is sent.

`scripts/channel-reply.sh` pipes the staged envelope here and refuses the whole reply
when this answers a reason. That is the one place it can be asked: an amendment reaches a
node through the live channel rather than through the plan store, so `just check-plan` —
which reads a plan — never sees one.

**The bar is :mod:`orchestrator.criteria_guard`'s and none of it is restated here.**
:func:`~orchestrator.criteria_guard.check_amendment` is what decides, and its docstring is
where the choice of which questions apply to an amendment lives; this module is the
envelope reader in front of it.

**The protocol, which is `scripts/channel-reply.sh`'s to read.** The envelope arrives on
stdin. Exit ``0`` means send it; exit ``1`` means refuse it, with the reason and nothing
else on stdout. Any other status is this check having failed to run, which the recipe
reports as such rather than as a verdict — so a non-zero exit here is never silent and
never empty.

**Only the ``amend`` op, and deliberately so.** ``retry`` and ``requeue`` can also change
what a node's judge reads, but each states a **whole task** rather than a correction to
one: a whole task carries an ``## Acceptance criteria`` block to be read on its own and
an ``## Additional info`` section full of the commands this bar refuses inside criteria,
so asking two of the bar's questions over the whole of it would refuse the operational
notes every task is supposed to carry. What reads a whole task is `just check-plan`,
which reads the block and stops at the next heading. An amendment has no such structure:
it is criteria and nothing else, which is why it can be asked here whole.
"""

from __future__ import annotations

import json
import sys
from typing import NamedTuple

from orchestrator.criteria_guard import CriteriaError, check_amendment

#: The one op whose text becomes part of a node's effective task as a correction to its
#: criteria, rather than as a task of its own. `docs/orchestration.md`'s live-edit table
#: is where that field and its effect are specified.
AMEND_OP = "amend"

#: The exit status `scripts/channel-reply.sh` reads as a refusal. It is 1 rather than 2
#: because the recipe tells a refusal from a broken helper by what was *said* — a broken
#: interpreter can exit with any status, including this one.
REFUSED = 1


class Amendment(NamedTuple):
    """One amendment an envelope carries: what to judge, and how to name it if refused."""

    text: str
    #: How the refusal names it, which is what a manager acts on. By its node, because
    #: that is what they are holding in mind when they write one; by its position in the
    #: envelope when the op names no node, which is a shape the verb refuses on its own
    #: and which must still not produce a refusal naming ``None``.
    where: str


def amendments(envelope: object) -> list[Amendment]:
    """Every amendment ``envelope`` carries, in the order it states them.

    Lenient about every shape but the one it acts on. A reply this cannot read is one
    `onepipeline reply` refuses with a message about the envelope it actually got, and a
    second opinion here would replace that with a guess — so anything that is not an
    ``amend`` carrying a text is passed over rather than reported.
    """
    match envelope:
        case {"commands": [*commands]}:
            pass
        case _:
            return []
    found: list[Amendment] = []
    for position, command in enumerate(commands):
        match command:
            # A guard rather than a value pattern: a bare name in a pattern captures,
            # which would judge every op as though it were an amendment.
            case {"op": op, "text": str(text), "id": str(node)} if (
                op == AMEND_OP and text.strip() and node
            ):
                found.append(Amendment(text, f"the amendment for node {node!r}"))
            case {"op": op, "text": str(text)} if op == AMEND_OP and text.strip():
                found.append(Amendment(text, f"the amendment in commands[{position}]"))
            case _:
                continue
    return found


def refusal(envelope: object) -> str | None:
    """Why this envelope's first refused amendment is refused, or ``None`` for none.

    The first rather than all of them: the recipe refuses the whole envelope, so nothing
    is sent either way, and a manager correcting one amendment re-sends the envelope and
    is told about the next.
    """
    for amendment in amendments(envelope):
        try:
            check_amendment(amendment.text, amendment.where)
        except CriteriaError as exc:
            return str(exc)
    return None


def main() -> int:
    """Read one reply envelope on stdin and say whether its amendments may be sent."""
    try:
        # llmlint: ignore[boundary_inputs_validated] The envelope is `onepipeline
        # reply`'s own open contract; every field this reads is narrowed where it is read
        # in `amendments`, and a document this cannot parse is passed to the verb, which
        # refuses it naming what it actually received.
        envelope = json.load(sys.stdin)
    except ValueError:
        return 0
    reason = refusal(envelope)
    if reason is None:
        return 0
    sys.stdout.write(reason)
    return REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
