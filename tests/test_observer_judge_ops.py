"""A member served by the planner channel is asked nothing that channel cannot answer.

`scripts/channel-serve.py` is a judge side that serves only what a planner can rule
on: `supervisor` at each turn boundary, and `judge` scoring the `user.done_when`.
Everything else onejudge can ask a judge side is refused by name — after which
`oneagentgraph` classifies the member `provider-failure`/`protocol` and kills it, and
the run carries on reporting `ACTIVE` with nobody watching it. Measured against
onejudge 0.4.0 with a `kind: command` judge that logged every op it was asked:

- `assessment` is asked as `assess`, once the conversation ends.
- `evals` is asked as `judge`, one call per criterion, once the conversation ends.
- `user.done_when` is asked as `judge`, once the conversation ends — **always**,
  whether the supervisor ruled complete or the turn cap ran out, and independently
  of the other two.

The three are not closed the same way, and that is the whole shape of this gate. The
first two are **declared unset**, which a persona can do and this file enforces. The
third **cannot be**: `oneagentgraph` merges a persona's `user.done_when` as a *second*
bar alongside the base's rather than over it, so a null one adds nothing, and
`user.done_when_replaces_base` is refused outright with nothing to replace it with. A
`kind: onejudge` member therefore always carries a bar it is always asked to score. So
`done_when` is closed by being **served** instead: `scripts/channel-serve.py` raises
the criterion to the planner and relays the `completion` they rule with as the score,
which is the only answer that is not an invention, since the planner is this member's
judge side. This file holds both halves — the two keys nobody may declare, and the op
the filter may never stop serving — because each one alone leaves the member dying.

That asymmetry is why `assessment: null` alone was not the fix. It removed `assess` and
left the monitor dying at the very end of every run on `got 'judge'`.

None of this is a property of one line in one persona, so this gate is not written
against one. A persona is a **delta** over `config/onejudge.base.yaml`, and the base is
shared with the dispatched workers that legitimately want all three. So the question is
always about the *merged* pair: a key the base declares and the persona says nothing
about is a key the member inherits, which is exactly how the monitor came to be asked
questions its judge side had to refuse. A future key added to the base fails here until
every channel-served persona declares it unset, and a persona that grows one of its own
fails here immediately.

Which members those are is read from the graph documents rather than listed, so a
second observer wired to the same filter is covered the day it is wired. What the
merged configuration a real launch actually hands the member carries is the other half
of this, and a declaration cannot answer it — that is
`tests/e2e/test_monitor_survives_the_channel_e2e.py`'s, which reads it out of a launch
and then watches the member live to the graph's settlement.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

#: The judge side this gate is about, as a graph document names it in an argv.
CHANNEL_FILTER = "scripts/channel-serve.py"

#: The directory holding this repository's agent-graph documents, every one of which
#: could wire a member's judge side to that filter.
GRAPHS = REPO_ROOT / "graphs"

#: The onejudge keys that ask a question the planner channel cannot answer AND that a
#: persona is able to decline. Both are optional and both default to unasked; what makes
#: them a hazard is that a persona inherits whatever the base declares. `user.done_when`
#: is deliberately NOT here — it cannot be declined, and `SERVED_OPS` below is how it is
#: closed instead.
DECLINABLE = ("evals", "assessment")

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

#: The unset form, as `onejudge schema` prints it (`assessment: null`). A key declared
#: this way is declared *not* to be asked, which is what a persona needs in order to
#: override a base that asks it — absence would inherit instead.
UNSET = "null"

#: How deep one level of a onejudge config document is indented.
NESTING = 2

#: How a graph document names a member, its refs, and its judge block. Read with an
#: indentation walk rather than a YAML library, as every other reader of these
#: documents here does: the workspace installs none, and `oneagentgraph validate` —
#: which `just check` runs over `graphs/` — is what holds them well-formed.
MEMBERS_KEY = "members:"


class MemberBlock(NamedTuple):
    """One member's name and the document lines declaring it, as the walk found them.

    The two carry distinct meanings and are read apart — the name reaches a failure
    message, the lines are walked for refs — so they are named rather than positional.
    """

    #: The member's own key in the `members:` mapping.
    name: str
    #: Every line indented under that key, in document order.
    lines: list[str]


class ChannelServedMember(NamedTuple):
    """One member whose judge side is the planner channel, and what it is merged from."""

    #: Where it is declared, for a failure that names the file to fix.
    graph: str
    #: The member's name in that document.
    name: str
    #: Its `base_config` ref, resolved against the graph document's own directory.
    base_config: str | None
    #: Its `persona` ref, resolved the same way.
    persona: str | None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _channel_served_members() -> list[ChannelServedMember]:
    """Every member of every shipped graph whose judge side is `channel-serve.py`.

    Discovered rather than listed. The bug this gate is about is one a *second*
    member wired to this filter would have on the day it was wired, and a list would
    cover it only if somebody remembered to add it here as well.
    """
    found: list[ChannelServedMember] = []
    for document in sorted(GRAPHS.glob("*.yaml")):
        lines = document.read_text(encoding="utf-8").splitlines()
        try:
            opened = next(index for index, line in enumerate(lines) if line == MEMBERS_KEY)
        except StopIteration:
            continue
        name: str | None = None
        block: list[str] = []
        blocks: list[MemberBlock] = []
        for line in lines[opened + 1 :]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if _indent(line) == 0:
                break
            if _indent(line) == 2 and line.rstrip().endswith(":"):
                if name is not None:
                    blocks.append(MemberBlock(name=name, lines=block))
                name, block = line.strip().rstrip(":"), []
                continue
            block.append(line)
        if name is not None:
            blocks.append(MemberBlock(name=name, lines=block))
        found.extend(
            ChannelServedMember(
                graph=str(document.relative_to(REPO_ROOT)),
                name=found_block.name,
                base_config=_ref(found_block.lines, "base_config"),
                persona=_ref(found_block.lines, "persona"),
            )
            for found_block in blocks
            if _serves_the_channel(found_block.lines)
        )
    return found


def _serves_the_channel(body: list[str]) -> bool:
    """Whether this member's `judge:` block spawns the planner-channel filter.

    Scoped to that block rather than matched anywhere in the member, because naming
    the filter as an *agent* side would be a different thing entirely — the question
    here is what onejudge asks its supervisor.
    """
    inside = False
    for line in body:
        if _indent(line) == 4:
            inside = line.strip() == "judge:"
        elif inside and CHANNEL_FILTER in line:
            return True
    return False


def _ref(body: list[str], key: str) -> str | None:
    """One of the member's own top-level refs, as the document spells it."""
    for line in body:
        if _indent(line) == 4 and line.strip().startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return None


def _lines(document: str) -> list[str]:
    """A config document's lines, with the blank and comment ones dropped.

    Dropped rather than skipped per-reader, because a comment is what would otherwise
    end a block walk early: a `#` at column 0 inside a `user:` block reads as the next
    top-level key to an indentation walk, and the key under it would then be reported
    as undeclared — which is the answer that makes this gate pass wrongly.
    """
    return [
        line for line in document.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


def _asks(document: str, path: str) -> str | None:
    """What a onejudge config document declares at `path`, or `None` if it says nothing.

    The declared *value* rather than a boolean, because the two ways of saying nothing
    are what this gate turns on: a document that omits the key inherits whatever it is
    merged over, while one that declares it `null` overrides that with the unset form.
    """
    lines = _lines(document)
    named = path.split(".")
    for depth, parent in enumerate(named[:-1]):
        opened = next(
            (
                index
                for index, line in enumerate(lines)
                if _indent(line) == depth * NESTING and line.strip() == f"{parent}:"
            ),
            None,
        )
        if opened is None:
            return None
        body = []
        for line in lines[opened + 1 :]:
            if _indent(line) <= depth * NESTING:
                break
            body.append(line)
        lines = body
    indent = (len(named) - 1) * NESTING
    for line in lines:
        if _indent(line) == indent and line.lstrip().startswith(f"{named[-1]}:"):
            return line.split(":", 1)[1].strip()
    return None


class Asked(NamedTuple):
    """One question the merged configuration puts to a member, and who put it there.

    Both halves reach the failure message and they say different things — which file
    to edit, and what it currently asks — so neither is read by position.
    """

    #: The repository-relative file whose declaration won the merge.
    declared_in: str
    #: The value it declares at that path, as the document spells it.
    value: str


def _effective(member: ChannelServedMember, path: str) -> Asked | None:
    """What the merged base ⊕ persona asks at `path`, and which file asked it.

    `None` when nothing asks — either because neither file declares the key, or
    because the persona declares it unset and so overrides a base that asks.
    """
    for ref in (member.persona, member.base_config):
        if ref is None:
            continue
        declared = (GRAPHS / ref).resolve()
        assert declared.is_file(), f"{member.graph} names {ref}, which is not a file"
        asked = _asks(declared.read_text(encoding="utf-8"), path)
        if asked is None:
            continue
        if asked == UNSET:
            return None
        return Asked(declared_in=str(declared.relative_to(REPO_ROOT)), value=asked)
    return None


def test_this_repository_wires_a_member_judge_side_to_the_planner_channel() -> None:
    """The gate below is about a real member, and says so before it passes vacuously.

    Everything this file asserts is a `for` loop over what `_channel_served_members`
    discovered, so a reader that stopped finding them — a graph reworded, the filter
    renamed, the indentation walk out of step with the documents — would report a
    clean gate over nothing at all.
    """
    served = _channel_served_members()
    assert served, (
        f"no member of any document in {GRAPHS.relative_to(REPO_ROOT)} was found to route "
        f"its judge side to {CHANNEL_FILTER}, so every assertion in this file is vacuous; "
        "check this reader against those documents"
    )


@pytest.mark.parametrize("path", DECLINABLE)
def test_no_channel_served_member_is_asked_a_question_the_channel_cannot_answer(
    path: str,
) -> None:
    """A member served by the planner channel declares both of these unset.

    Asserted against the merged pair, which is the only place the answer lives: a
    persona that says nothing about a key the base declares is asked the base's
    question, and `scripts/channel-serve.py` refuses it by name and dies. Declaring the
    key `null` in the persona is what says "not asked" loudly enough to override a base
    that asks — and is the form `onejudge schema` gives for it.
    """
    for member in _channel_served_members():
        asked = _effective(member, path)
        assert asked is None, (
            f"{member.graph}'s `{member.name}` member is served by {CHANNEL_FILTER}, which "
            f"answers only what a planner can rule on, but {asked.declared_in} asks it for "
            f"`{path}` ({asked.value!r}). onejudge would put that question to the planner "
            f"channel, the filter would refuse it, and oneagentgraph would kill the member "
            f"and leave the run unwatched. Declare `{path.split('.')[-1]}: {UNSET}` in "
            f"{member.persona} to override the base, or stop asking it there"
        )


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
