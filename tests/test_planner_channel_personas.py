"""No member served by the planner channel is asked a question it cannot answer.

A member whose judge side is `onemessagebus serve --codec monitor` — the binding this host
declares in `config/onemessagebus.yaml` — is served only what a planner can rule on:
`supervisor` at each turn boundary, and a `boolean` `judge` scoring its completion bar. The
binding refuses `assess` and a `numeric` `judge`, with exit 2 — after which `oneagentgraph`
classifies the member `provider-failure`/`protocol` and kills it, and the run carries on
reporting `ACTIVE` with nobody watching it. Measured against onejudge 0.13.1 with a
`kind: command` judge that logged every op it was asked:

- `assessment` is asked as `assess`, once the conversation ends.
- `evals` is asked as `judge`, one call per criterion, once the conversation ends.
- `user.done_when` is asked as `judge`, once the conversation ends — **always**, whether
  the supervisor ruled complete or the turn cap ran out.

The first two are **declared unset**, which a persona can do and which this file
enforces: `assess` is refused outright, and every eval is one more `judge` the planner
would be asked — a `numeric` one refused. The third cannot be declined at all, and is
closed by being *served* instead; that half is `tests/test_observer_judge_ops.py`, which
holds the judge side to the binding that serves it rather than to what a persona declares.

None of this is a property of one line in one persona, so the gate is not written
against one. A persona is a **delta** over `config/onejudge.base.yaml`, and the base is
shared with the dispatched workers that legitimately want all three keys. So the
question is always about the *merged* pair: a key the base declares and the persona says
nothing about is a key the member inherits, which is exactly how the monitor came to be
asked questions its judge side had to refuse. Which members those are is read out of the
graph documents rather than listed, so a second observer wired to the same binding is
covered the day it is wired.

Reading the merge is only as good as knowing how a persona says "not asked", and that is
the producers' to decide rather than this repository's. The two keys do not take the
same word — `assessment` is free text whose unset form `onejudge schema` prints as
`null`, while `evals` is a **sequence** that `oneagentgraph persona validate` refuses a
null one of, so the empty list is the only thing that unsets it. Both spellings are
reconciled below against the installed releases instead of restated, which is why part
of this file runs in the uncached tier: a claim about an installed release belongs
outside every cache key over this workspace.

`tests/e2e/test_monitor_survives_the_channel_e2e.py` is the other half of the seam,
reading the merged configuration out of a real launch — the only thing that proves the
delta merge honours an unset key at all. This file is the cheap gate that stops one
being declared in the first place, and it holds the two halves to the same keys and the
same spellings, so a persona following the advice in one cannot be failed by the other.
"""

from __future__ import annotations

import ast
import re
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

#: The judge side this gate is about, as a graph document's `judge.command` spells it:
#: the bus's `serve` verb running this host's `monitor` binding. Both words, because
#: another binding served by the same verb would refuse a different set of ops.
CHANNEL_SERVE = "onemessagebus serve"
CHANNEL_CODEC = "--codec monitor"

#: The directory holding this repository's agent-graph documents, every one of which
#: could wire a member's judge side to that binding.
GRAPHS = REPO_ROOT / "graphs"

#: The onejudge keys that ask a question the planner channel cannot answer AND that a
#: persona is able to decline. Both are optional and both default to unasked; what makes
#: them a hazard is that a persona inherits whatever the base declares. `user.done_when`
#: is deliberately NOT here — it cannot be declined at all, and is closed by the filter
#: going on serving the op that scores it, which `tests/test_observer_judge_ops.py` gates.
DECLINABLE = ("evals", "assessment")
#: The unset form of each declinable key, the spelling to advise first. Declaring a key
#: this way is what overrides a base that asks it; absence would inherit instead.
#: Per key because the two do not accept the same word — `evals` is a sequence, so the
#: empty list unsets it where `null` unsets `assessment`.
#: `tests/test_planner_channel_personas.py` holds both to what the producers accept.
UNSET_BY = {"assessment": ("null", "Null", "NULL", "~"), "evals": ("[]",)}

#: How deep one level of a onejudge config document is indented.
NESTING = 2

#: How a graph document names a member, its refs, and its judge block. Read with an
#: indentation walk rather than a YAML library, as every other reader of these
#: documents here does: the workspace installs none, and `oneagentgraph validate` —
#: which `just check` runs over `graphs/` — is what holds them well-formed.
MEMBERS_KEY = "members:"

#: The two producers whose declarations `UNSET_BY` restates, as this workspace installs
#: them. `scripts/python-install.sh` puts both here, and these are read rather than
#: whatever is on PATH: a fallen-through PATH would reconcile this repository's gate
#: against some other checkout's releases, which is the failure `AGENTS.md` records for
#: every reader of `<root>/.venv/bin`.
ONEJUDGE = REPO_ROOT / ".venv" / "bin" / "onejudge"
ONEAGENTGRAPH = REPO_ROOT / ".venv" / "bin" / "oneagentgraph"

#: The real-launch half of this seam, and how it names the keys it refuses to see in a
#: merged config. Read out of that file rather than restated, because the point of the
#: assertion below is that the two agree and a restatement would agree with itself.
JOURNEY = REPO_ROOT / "tests" / "e2e" / "test_monitor_survives_the_channel_e2e.py"
JOURNEY_DECLINED = re.compile(r"^DECLINED = (?P<mapping>\{[^}]*\})", re.MULTILINE)


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
    """Every member of every shipped graph whose judge side is this host's monitor binding.

    Discovered rather than listed. The bug this gate is about is one a *second*
    member wired to this binding would have on the day it was wired, and a list would
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
    """Whether this member's `judge:` block runs this host's monitor binding.

    Scoped to that block rather than matched anywhere in the member, because the question
    here is what onejudge asks its supervisor. The block's lines are joined first, since a
    command's argv is written as a folded scalar and its words fall on several lines.
    """
    inside = False
    judge: list[str] = []
    for line in body:
        if _indent(line) == 4:
            inside = line.strip() == "judge:"
        elif inside:
            judge.append(line.strip())
    joined = " ".join(judge)
    return CHANNEL_SERVE in joined and CHANNEL_CODEC in joined


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
        if asked in UNSET_BY[path.split(".")[-1]]:
            return None
        return Asked(declared_in=str(declared.relative_to(REPO_ROOT)), value=asked)
    return None


def _validates(fragment: str) -> subprocess.CompletedProcess[str]:
    """Put one persona fragment to the real `oneagentgraph persona validate`.

    A whole persona is neither needed nor honest here: a persona *is* a onejudge config
    fragment, so the fragment declaring the one key is exactly what the validator is
    being asked about.
    """
    with tempfile.TemporaryDirectory() as scratch:
        written = Path(scratch) / "fragment.yaml"
        written.write_text(fragment, encoding="utf-8")
        return subprocess.run(
            [str(ONEAGENTGRAPH), "persona", "validate", str(written)],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )


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
        f"its judge side to `{CHANNEL_SERVE} {CHANNEL_CODEC}`, so every assertion in this "
        "file is vacuous; check this reader against those documents"
    )


@pytest.mark.parametrize("path", DECLINABLE)
def test_no_channel_served_member_is_asked_a_question_the_channel_cannot_answer(
    path: str,
) -> None:
    """A member served by the planner channel declares both of these unset.

    Asserted against the merged pair, which is the only place the answer lives: a
    persona that says nothing about a key the base declares is asked the base's
    question, and the binding refuses it and the member dies. Declaring the key
    unset in the persona is what says "not asked" loudly enough to override a base that
    asks — in the spelling `UNSET_BY` carries for that key, which is the producers' to
    decide and not the same word for both.
    """
    key = path.split(".")[-1]
    unset = UNSET_BY[key][0]
    for member in _channel_served_members():
        asked = _effective(member, path)
        assert asked is None, (
            f"{member.graph}'s `{member.name}` member is served by `{CHANNEL_SERVE} "
            f"{CHANNEL_CODEC}`, which answers only what a planner can rule on, but "
            f"{asked.declared_in} asks it for `{path}` ({asked.value!r}). onejudge would put "
            f"that question to the planner channel, the binding would refuse it, and "
            f"oneagentgraph would kill the member and leave the run unwatched. Declare "
            f"`{key}: {unset}` in {member.persona} to override the base, or stop asking it there"
        )


@pytest.mark.reads_checkouts
def test_the_unset_spellings_this_gate_accepts_are_the_ones_the_producers_declare() -> None:
    """Every spelling in `UNSET_BY` is one the installed releases actually accept.

    The class gate is only as good as its idea of how "not asked" is written, so both
    halves are read back off the tools: `onejudge schema` for the key it prints an
    unset form for, and `oneagentgraph persona validate` for every spelling a
    maintainer could be told to write. A release that moved either would fail here
    rather than in a run whose monitor quietly stopped watching.
    """
    printed = subprocess.run(
        [str(ONEJUDGE), "schema"], text=True, capture_output=True, timeout=60, check=False
    )
    assert printed.returncode == 0, f"`onejudge schema` failed:\n{printed.stderr}"
    advised = UNSET_BY["assessment"][0]
    assert any(line.startswith(f"assessment: {advised}") for line in printed.stdout.splitlines()), (
        f"onejudge no longer prints an unset assessment as `{advised}`, so the spelling "
        f"this gate advises is one the producer may no longer read as unset:\n{printed.stdout}"
    )

    for key, spellings in UNSET_BY.items():
        for spelling in spellings:
            checked = _validates(f"{key}: {spelling}\n")
            assert checked.returncode == 0, (
                f"`oneagentgraph persona validate` refuses `{key}: {spelling}`, which "
                f"{Path('tests/test_observer_judge_ops.py')} accepts as unset — a persona "
                f"declaring it would be refused by `just check`, and a persona *told* to "
                f"declare it by that gate's own remediation would be refused too:\n"
                f"{checked.stdout}{checked.stderr}"
            )


@pytest.mark.reads_checkouts
def test_a_null_evals_is_still_refused_so_the_empty_list_is_still_the_only_unset() -> None:
    """The refusal is what makes `evals` a different word from `assessment`.

    Asserted rather than assumed, because the per-key split above costs a reader
    something and should stop costing it the moment the producer stops requiring it. If
    a release starts accepting `evals: null`, this fails and says so, instead of leaving
    a split nobody can find the reason for.
    """
    refused = _validates("evals: null\n")
    assert refused.returncode != 0, (
        "`oneagentgraph persona validate` now accepts `evals: null`, so the empty list is "
        f"no longer the only spelling that unsets `evals`; re-take the measurement in "
        f"UNSET_BY and in this file's docstring before relying on either:\n{refused.stdout}"
    )
    assert "expected a sequence" in (refused.stdout + refused.stderr), (
        "`evals: null` is still refused, but no longer for being a null where a sequence "
        f"belongs; the reason this gate is split per key may have moved:\n"
        f"{refused.stdout}{refused.stderr}"
    )


def test_both_halves_of_this_seam_agree_on_what_unset_means() -> None:
    """The declaration gate and the real-launch journey read one contract, not two.

    Each half alone leaves the member dying. The journey reads the merged config a
    launch actually wrote and cannot say what a future persona may declare; the
    declaration gate says that and cannot prove the merge honours it. So both the keys
    and the spellings have to match, and the spelling half is the one that bites: a
    persona is told to write `evals: []` because that is the only form the validator
    accepts, and a journey that read anything but `null` as still asked would fail the
    member on the very spelling this repository advises.
    """
    source = JOURNEY.read_text(encoding="utf-8")
    found = JOURNEY_DECLINED.search(source)
    assert found is not None, (
        f"{JOURNEY.name} no longer declares `DECLINED` as a mapping of key to the values "
        "that unset it, so the real-launch half of this seam cannot be read; move this "
        "assertion with it"
    )
    journey = ast.literal_eval(found["mapping"])

    assert set(journey) == set(DECLINABLE), (
        f"{JOURNEY.name} gates {sorted(journey)} while tests/test_observer_judge_ops.py "
        f"gates {sorted(DECLINABLE)}. A key only one of them covers is a question the "
        "other would let a launch put to the planner channel, and the member dies on the "
        "refusal at the end of a run it watched"
    )
    for key, spellings in UNSET_BY.items():
        advised = spellings[0]
        assert advised in journey[key], (
            f"a persona is advised to unset `{key}` as `{advised}`, but {JOURNEY.name} "
            f"accepts only {list(journey[key])} as unset for it — so a persona following "
            "that advice passes the declaration gate and is failed by the launch that "
            "reads what oneagentgraph wrote"
        )
