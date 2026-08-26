"""Refuse a plan whose node would be failed for something other than its work.

A dispatched node is judged against two things: its own ``## Acceptance criteria``,
and the review bar its persona resolves to. Where those disagree — or where the
criteria are silent about something the bar demands — the judge imports the demand
and applies its own reading of it, and correct, gate-green work is failed on
procedure. Four nodes have been lost that way in this host's history:

* two whose criteria named a *procedure* rather than a property, so a judge that
  could not accept an equivalent route failed the spelling: ``"The branch
  publishes."`` names work that happens after the dispatch settles, and ``"`just a
  && just b && just c` is green."`` names an exact invocation;
* one failed for running the complete gate's components separately, quoting this
  host's own operational appendix back at it;
* one failed for never having *"provided a final verified completion report"* — a
  demand in neither its task nor the shared completion clause, imported wholesale
  from the built-in role's bar.

The first pair is what the criteria checks below refuse. The second pair is what
:func:`resolve_bar` and :data:`DEMANDS` refuse: a demand that will be made of this
node has to be *stated* as a criterion, so the judge checks a criterion the author
wrote instead of one it reconstructed.

A third shape is worse than either, because no wording of the criteria rescues it:
a bar that forbids the dispatch changing project files, under a task that requires
one to change. The judge is then *required* to fail the work the task is *required*
to produce. Three nodes of one plan named ``persona: researcher`` while their tasks
were to edit a document; the first settled ``task-failed`` with the judge citing a
file that does not exist, which is what that contradiction looks like from the
outside, and it cost a run and a relaunch. :func:`check_changes_allowed` refuses the
pairing by name. It reads the resolved bar and the criteria rather than a list of
role names — ``researcher`` reads like the right role for a node whose job is
measurement, and its file-modification clause is invisible from ``personas/``,
which is not where a bare name resolves.

Resolving that bar is the part that cannot be guessed. A plan node's ``persona`` is
a **name**, and a name resolves to a role compiled into ``oneagentgraph`` rather
than to anything in this repository's ``personas/`` directory — so the file a
reader would reach for is not the bar in force. Nor is the pinned
``oneagentgraph`` CLI: a dispatched session runs under the ``oneagentgraph`` that
``onepipeline``'s own lockfile resolved, which is why this module reads the roles
out of the ``onepipeline`` binary. ``AGENTS.md`` records that lesson twice; reading
the wrong one here would validate every plan against a bar no dispatch is given.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections.abc import Iterator, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from orchestrator.root import REPO_ROOT

#: The operational text every dispatched task carries, and the one source a plan
#: builder copies it from. It was gitignored scratch until this module tracked it,
#: which is how it came to contradict itself for long enough to fail a node.
APPENDIX = Path("config") / "dispatch-appendix.md"

#: The shared review contract and completion criterion every dispatch is judged
#: against, whichever role it names.
BASE_CONFIG = Path("config") / "onejudge.base.yaml"

#: Where `oneagentgraph` resolves a persona name that no built-in role claims.
GRAPHS = Path("graphs")

#: The command whose linked `oneagentgraph` is the one a dispatch is judged under.
ENGINE = "onepipeline"

CRITERIA_HEADING = "## Acceptance criteria"


class CriteriaError(ValueError):
    """A plan node would be judged against something its task does not state."""


class Bar(NamedTuple):
    """The review bar one node will actually be judged against."""

    #: Where that bar comes from, phrased for the error message that names it.
    source: str
    #: The review contract and completion criteria the judge is handed, as one blob
    #: to search: what matters here is which demands it makes, not their structure.
    text: str


class Demand(NamedTuple):
    """One demand a bar or a task's own prose makes, and how a criterion states it."""

    name: str
    made_by: re.Pattern[str]
    stated_by: re.Pattern[str]
    remedy: str


#: Every block-scalar header a field may open with. `>` folds its newlines to
#: spaces and `|` keeps them, so the reader below folds or keeps to match and a
#: style change is not a change to any caller.
FOLDING_HEADERS = (">", ">-", ">+")
LITERAL_HEADERS = ("|", "|-", "|+")

_BLOCK_OPEN = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z_][A-Za-z0-9_]*): *(?P<header>[|>][-+]?)$")


def block_scalar(document: str, key: str) -> tuple[str, str] | None:
    """The header ``key`` opens with and its block **normalized for reading**.

    Read with a reader written for this shape rather than with a YAML library: the
    workspace installs none, and a persona fragment reaches this module as bytes
    lifted out of a binary rather than as a file some parser could be pointed at.

    Normalized, not parsed, and the difference is deliberate rather than a shortfall
    to fix later. A `>` block comes back as one whitespace-collapsed line and a `|`
    block with its line structure and surrounding blank lines trimmed — so paragraph
    breaks inside a folded block, and the clip/keep/strip distinction between `|`,
    `|+`, and `|-`, are not preserved. Nothing here depends on them: every caller
    either searches the value for the demands it makes or compares it against a
    prompt that was itself whitespace-normalized, because that is the only comparison
    a model's rendering of these fields supports. A caller that needed the exact
    bytes would need a parser, and would have to say so.
    """
    lines = document.splitlines()
    for index, line in enumerate(lines):
        opened = _BLOCK_OPEN.match(line)
        if opened is None or opened["key"] != key:
            continue
        indent = len(opened["indent"])
        block: list[str] = []
        for following in lines[index + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            block.append(following)
        inner = min((len(one) - len(one.lstrip()) for one in block if one.strip()), default=0)
        stripped = [one[inner:] if one.strip() else "" for one in block]
        header = opened["header"]
        if header in FOLDING_HEADERS:
            return header, " ".join(" ".join(stripped).split())
        return header, "\n".join(stripped).strip("\n")
    return None


_INLINE = re.compile(
    r"^ *(?P<key>[A-Za-z_][A-Za-z0-9_]*): +(?P<value>\"[^\"]*\"|'[^']*'|[^\s#|>][^#]*?) *$"
)


def field(document: str, key: str) -> str | None:
    """``key``'s value normalized for reading, in whichever shape a persona writes it.

    The shipped roles use both: `engineer` opens `done_when`'s neighbours as block
    scalars while `researcher` and `reviewer` state theirs as one quoted line. A
    reader that knew only the block form dropped those two roles' own completion
    bars silently, which is the half of their review contract that is enforced
    alongside the shared one.
    """
    block = block_scalar(document, key)
    if block is not None:
        return block[1]
    for line in document.splitlines():
        inline = _INLINE.match(line)
        if inline is not None and inline["key"] == key:
            value = inline["value"]
            unquoted = value[1:-1] if value[:1] in {'"', "'"} and value[-1:] == value[:1] else value
            return unquoted.strip()
    return None


_REPLACES_BASE = re.compile(r"^\s+done_when_replaces_base:\s*true\s*$", re.MULTILINE)

_TOP_LEVEL_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]*):(?:\s|$)")


def _top_level_key(line: str) -> str | None:
    """The key ``line`` opens at column zero, or ``None`` when it opens none."""
    named = _TOP_LEVEL_KEY.match(line)
    return None if named is None else named[1]


def yaml_fragment(text: str) -> str:
    """``text`` truncated at the first line that cannot continue its own document.

    The shipped roles sit in the binary as bare string literals laid end to end, so
    the byte after one persona's trailing newline is the first byte of the next
    one's, and nothing delimits them. Two rules do that here, because neither is
    enough alone. A line that is neither blank, indented, nor a column-zero key
    cannot continue this document, which is what ends a persona whose successor's
    name literal was laid down adjacent to it (`… action available.` then
    `planner# A general …`). And a column-zero key that *repeats* one already read
    opens a second document, which is what ends a persona whose successor begins
    with a comment block instead (`  max_turns: 8` then `# A general implementation
    role. …`): every shipped role declares `name`, so the next one's declaration is
    always the repeat that stops this one. The trailing comment run those leave
    behind is dropped, so a fragment ends at its own last field.
    """
    lines = text.split("\n")
    opening = _top_level_key(lines[0])
    seen = set() if opening is None else {opening}
    fragment = [lines[0]]
    for line in lines[1:]:
        if not line.strip() or line[0].isspace() or line.startswith("#"):
            fragment.append(line)
            continue
        key = _top_level_key(line)
        if key is None or key in seen:
            break
        seen.add(key)
        fragment.append(line)
    while fragment and (not fragment[-1].strip() or fragment[-1].startswith("#")):
        fragment.pop()
    return "\n".join(fragment)


#: How far past its anchor a shipped persona can run. Generous: what bounds the
#: fragment is `yaml_fragment`, and this only bounds the decode.
PERSONA_WINDOW = 64 * 1024


@lru_cache(maxsize=1)
def _engine_bytes() -> bytes:
    """The dispatching binary, whose linked `oneagentgraph` ships the roles."""
    found = shutil.which(ENGINE)
    # tests/test_criteria_guard.py covers this with substituted engine bytes, and no
    # recipe journey can: `uv run` puts this checkout's `.venv/bin` on the child's
    # PATH, so the engine the guard resolves is there whatever PATH a caller sets.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    if found is None:
        raise CriteriaError(
            f"cannot resolve any node's review bar: {ENGINE!r} is not on PATH, and its "
            "linked `oneagentgraph` is what ships the role a `persona` name resolves to; "
            "run `just bootstrap` and retry"
        )
    return Path(found).resolve().read_bytes()


def builtin_persona(name: str) -> str | None:
    """The shipped role ``name``, as the persona fragment a dispatch is merged from.

    ``None`` when no role claims that name, which is the whole of the distinction
    `personas/README.md` draws: a name nothing ships is read as a path instead.
    """
    data = _engine_bytes()
    anchor = f"\nname: {name}\n".encode()
    found = data.find(anchor)
    if found < 0:
        return None
    # tests/test_criteria_guard.py covers this with substituted engine bytes, and no
    # recipe journey can: `uv run` puts this checkout's `.venv/bin` on the child's
    # PATH, so the engine the guard resolves is there whatever PATH a caller sets.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    if data.find(anchor, found + 1) >= 0:
        raise CriteriaError(
            f"{ENGINE} carries more than one persona declaring `name: {name}`, so which "
            f"one a dispatch is judged under cannot be read from it; name that node's "
            f"persona as a path relative to {GRAPHS}/ and report the release"
        )
    # The window is decoded leniently because it is cut at a byte offset and its tail
    # routinely lands mid-character; the *fragment* may not be, and is checked below.
    fragment = yaml_fragment(
        data[found + 1 : found + 1 + PERSONA_WINDOW].decode("utf-8", "replace")
    )
    # tests/test_criteria_guard.py covers this with substituted engine bytes, and no
    # recipe journey can: `uv run` puts this checkout's `.venv/bin` on the child's
    # PATH, so the engine the guard resolves is there whatever PATH a caller sets.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    if "\ufffd" in fragment or "user:" not in fragment or field(fragment, "persona") is None:
        raise CriteriaError(
            f"the shipped role {name!r} was found in {ENGINE} but does not read back as a "
            f"whole persona carrying a `user.persona`, so the bar it is judged under "
            f"cannot be read from it; name that node's persona as a path relative to "
            f"{GRAPHS}/ and report the release"
        )
    return fragment


_SHIPPED_NAME = re.compile(rb"\nname: (?P<name>[A-Za-z][\w-]*)\n")


def builtin_persona_names() -> tuple[str, ...]:
    """Every role name the linked `oneagentgraph` ships, in the order it declares them.

    Read out of the same binary :func:`builtin_persona` lifts a role from, so a
    release that adds, drops, or renames one is followed here with nothing to edit.
    That matters for one caller only — the refusal below has to offer a persona that
    would actually dispatch — and offering a name from a list written here would be
    offering whatever was shipped on the day it was written.
    """
    return tuple(
        dict.fromkeys(found["name"].decode() for found in _SHIPPED_NAME.finditer(_engine_bytes()))
    )


def _reviewing(fragment: str) -> tuple[str | None, str | None, bool]:
    """A fragment's review contract, its own completion bar, and whether it replaces."""
    return (
        field(fragment, "persona"),
        field(fragment, "done_when"),
        _REPLACES_BASE.search(fragment) is not None,
    )


def resolve_bar(persona: str | None) -> Bar:
    """The bar a node naming ``persona`` will actually be judged against.

    Three resolutions, in the order `oneagentgraph` applies them: no persona leaves
    the base config's generic contract standing; a name a shipped role claims wins
    over everything in `personas/`; and any other name is read as a path relative
    to `graphs/`, which is why `crozier/crozier-corpus` and `orchestrator` fail a
    dispatch rather than quietly running some built-in.
    """
    base = (REPO_ROOT / BASE_CONFIG).read_text(encoding="utf-8")
    shared_contract, shared_done_when, _ = _reviewing(base)
    if persona is None:
        generic = f"the generic review contract in {BASE_CONFIG}"
        return Bar(generic, _stated(_joined(shared_contract, shared_done_when), generic))

    fragment = builtin_persona(persona)
    if fragment is not None:
        source = f"the `{persona}` role built into the `oneagentgraph` {ENGINE} links"
    else:
        named = (REPO_ROOT / GRAPHS / persona).resolve()
        if not named.is_relative_to(REPO_ROOT):
            raise CriteriaError(
                f"persona {persona!r} resolves to {named}, outside this checkout. A ref is "
                f"read relative to {GRAPHS}/ in the directory a run is launched from, so a "
                f"plan naming a file outside it dispatches only on the one host that has "
                f"that file; keep the persona in `personas/` and name it from {GRAPHS}/"
            )
        if not named.is_file():
            raise CriteriaError(
                f"persona {persona!r} is neither a role the `oneagentgraph` {ENGINE} links "
                f"ships nor a readable path: a dispatch resolves it as "
                f"{GRAPHS / persona} and settles `failed` before a harness starts"
            )
        source = f"{_within(named)}, named as a path relative to {GRAPHS}/"
        # Validated for the two fields a bar composes from, and no further. This
        # workspace ships no YAML parser, and the authoritative validator of a
        # persona's full shape is `just validate-personas` — which runs the same
        # `oneagentgraph` a dispatch merges the file with, so a second and weaker
        # opinion of that schema here could only refuse a file that dispatches fine.
        # llmlint: ignore[boundary_inputs_validated] see the note above this line
        fragment = named.read_text(encoding="utf-8")
        if field(fragment, "persona") is None and field(fragment, "done_when") is None:
            raise CriteriaError(
                f"{source} states neither `user.persona` nor `user.done_when`, so a node "
                f"naming it is reviewed against {BASE_CONFIG}'s generic contract rather "
                f"than against that file; `just validate-personas` checks its shape"
            )

    contract, done_when, replaces = _reviewing(fragment)
    return Bar(
        source,
        _stated(
            _joined(
                shared_contract if contract is None else contract,
                done_when if replaces else _joined(shared_done_when, done_when),
            ),
            source,
        ),
    )


def _stated(bar: str, source: str) -> str:
    """``bar`` when it says anything at all, refused by name when it does not.

    A bar composes from two files this module does not own — the base config, and
    either a role lifted out of a binary or a persona ref — so a field renamed in
    either would leave every node resolving to an empty bar and every plan passing
    for the one reason that means nothing.
    """
    # tests/test_criteria_guard.py covers this, and no recipe journey can: the recipe
    # runs in this checkout, whose base config states both halves of the shared bar,
    # and a persona ref cannot empty it — an absent field falls back to that config's.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    if not bar:
        raise CriteriaError(
            f"{source} states neither a review contract nor a completion bar, so no node "
            f"can be checked against it; if a field was renamed, {BASE_CONFIG} and the "
            f"reader in this module have to move together"
        )
    return bar


def _within(path: Path) -> str:
    """``path`` said the way a reader of this checkout would say it.

    A persona ref is resolved from `graphs/`, so `../personas/orchestrator.yaml`
    lands back inside the checkout and reads best relative to it — but nothing stops
    a ref climbing out of it, and naming such a file by a relative path that no
    longer starts here would be worse than saying where it really is.
    """
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def _joined(*parts: str | None) -> str:
    return "\n\n".join(part for part in parts if part)


# Work the dispatch cannot perform: it happens after the worker settles.
OUT_OF_DISPATCH = (
    "branch publishes",
    "is published",
    "pr is merged",
    "pull request is merged",
    "lands on master",
    "lands on main",
    "deploy",
)

# Procedure rather than property. A criterion naming an invocation is one a judge
# can fail on spelling.
PROCEDURE = (
    (re.compile(r"`[^`]*\bjust\s"), "names a `just` invocation"),
    (re.compile(r"&&"), "names a chained shell command"),
    (re.compile(r"`[^`]*\b(npm|pnpm|nx|cargo|pytest|git)\s"), "names a shell invocation"),
)

# A criterion with no content of its own. Deferring to prose elsewhere gives the
# judge a target it has to reconstruct, and it reconstructs a wording demand: one
# node asked for "an honest justification of the shape described above" and failed
# gate-green work whose justification was honest but phrased differently.
DEFERRAL = re.compile(
    r"\b(?:of the shape|as|exactly as|in the form|matching the wording|per the)\s+"
    r"(?:described|specified|stated|set out|given|above|below)\b|\bthe wording above\b",
    re.I,
)

# A demand for a particular string rather than a particular property. The worker can
# satisfy the property and still be failed on the spelling.
PHRASE = re.compile(r"\b(verbatim|word for word|the exact (?:phrase|wording|words))\b", re.I)

#: Demands whose absence from the criteria has already failed finished work. Each is
#: enforced only where it is actually made — in the resolved bar, or in the task's
#: own prose outside the criteria — so a role that stops making one stops requiring
#: it here, and nothing has to be re-stated when an upstream bar moves.
DEMANDS = (
    Demand(
        "proof end to end",
        re.compile(r"\bend[- ]to[- ]end\b", re.I),
        re.compile(r"\bend[- ]to[- ]end\b", re.I),
        "name the test or journey that proves this node's behavior end to end",
    ),
    Demand(
        "a completion report",
        re.compile(r"\b(?:report|surfac)\w*", re.I),
        re.compile(r"\breport\w*", re.I),
        "say what the finished dispatch reports and what evidence it names",
    ),
)


#: How a review bar says this dispatch may not change the repository. Matched against
#: the *resolved* bar rather than against a persona name, so a role whose wording
#: moves upstream moves this with it and a role that stops saying it stops being
#: refused. The shipped `researcher` says it twice — once as a review instruction
#: ("Refuse the work if the agent modified project files instead of only answering
#: the question") and once as its own completion bar ("no project files were
#: changed") — and either alone is the whole conflict. Every alternative carries its
#: own negation or refusal, so a bar *granting* the licence ("may modify project
#: files") is not read as withholding it.
FORBIDS_CHANGES = re.compile(
    r"\bno (?:\w+ )?files? (?:were|was|are|is) (?:changed|modified|edited|touched)\b"
    r"|\brefuse[^.]*\b(?:modif|chang|edit|touch)\w* "
    r"(?:any |the )?(?:project|repository|repo|source|tracked) files\b"
    r"|\bwithout (?:changing|modifying|editing|touching) "
    r"(?:any |the )?(?:project|repository|repo|source|tracked) files\b",
    re.I,
)

#: A backticked token shaped like a file or directory path. Shape is all this reads —
#: nothing here asks git whether the path exists or is tracked, because a plan is
#: checked against repositories this checkout has never seen. Backticks are the whole
#: of the precision: unquoted, `and/or` is a path and `e.g.` is a file, and this check
#: refuses a plan outright — so it is written to **miss** a criterion that names its
#: file in prose rather than to refuse a sound one. That trade is deliberate; a false
#: refusal blocks correct work and gets worked around, which is worse than the gap.
QUOTED_PATH = re.compile(r"`(?P<path>[\w.@+-]+(?:/[\w.@+-]*)+|[\w@+-]+\.[A-Za-z]\w{0,7})`")

#: One word that asserts something changed. Word-level and nothing more — what ties it
#: to a path is :func:`check_changes_allowed` requiring both inside one criterion, and
#: no grammar here says the path is the thing that changed. Reading-only verbs are
#: deliberately absent: "the answer cites `docs/x.md`" and "the report names
#: `orchestrator/y.py`" are what a role forbidden to touch the tree is for, and
#: neither may be read as a demand to edit it.
CHANGE_WORD = re.compile(
    r"\b(?:add(?:s|ed|ing)?|updat(?:e|es|ed|ing)|edit(?:s|ed|ing)?|creat(?:e|es|ed|ing)"
    r"|modif(?:y|ies|ied|ying)|remov(?:e|es|ed|ing)|delet(?:e|es|ed|ing)"
    r"|renam(?:e|es|ed|ing)|replac(?:e|es|ed|ing)|introduc(?:e|es|ed|ing)"
    r"|extend(?:s|ed|ing)?|gain(?:s|ed|ing)?|writ(?:e|es|ing|ten)|rewrit(?:e|es|ten)"
    r"|commit(?:s|ted|ting)?|chang(?:e|es|ed|ing)|new)\b",
    re.I,
)

#: Actions that make a named path evidence rather than an output. A change word may
#: still occur later in the criterion while describing what that evidence is about —
#: "read `template.md` ... describing what the branch changes" is the incident that
#: exposed that distinction. The nearest action to the path decides which noun that
#: action governs; ties remain changes so ambiguity cannot erase a real conflict.
READ_WORD = re.compile(
    r"\b(?:read(?:s|ing)?|quot(?:e|es|ed|ing)|cit(?:e|es|ed|ing)|consult(?:s|ed|ing)?"
    r"|inspect(?:s|ed|ing)?|review(?:s|ed|ing)?|follow(?:s|ed|ing)?)\b",
    re.I,
)


_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def criteria_items(block: str) -> Iterator[str]:
    """Each criterion of ``block`` on its own, with any continuation lines it wraps to.

    One criterion is the unit the conflict below is read at. Searching the whole block
    would pair a path named in one criterion with a verb belonging to another — "the
    answer cites `docs/x.md`" beside "a new section of the report" is not a demand to
    edit that file, and refusing it would be exactly the false refusal this check is
    written to avoid.
    """
    current: list[str] = []
    for line in block.splitlines():
        if _BULLET.match(line) and current:
            yield "\n".join(current)
            current = []
        if line.strip():
            current.append(line)
        elif current:
            yield "\n".join(current)
            current = []
    if current:
        yield "\n".join(current)


def permitting_roles() -> tuple[str, ...]:
    """The shipped roles whose bar does **not** forbid this dispatch changing the tree.

    Resolved rather than listed, for the same reason the conflict itself is: a name
    offered as the correction has to be one whose bar really permits the work. A role
    the binary declares twice cannot be resolved at all, and is passed over rather
    than turned into an unrelated refusal — the plan's author is being told which
    persona to name, not audited on the engine's packaging.
    """
    permitted = []
    for name in builtin_persona_names():
        try:
            bar = resolve_bar(name)
        # tests/test_criteria_guard.py covers this with substituted engine bytes, and no
        # recipe journey can: what decides it is what the installed binary declares, and
        # `uv run` puts this checkout's `.venv/bin` on the child's PATH, so the engine
        # the guard resolves is there whatever PATH a caller sets.
        # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
        except CriteriaError:
            continue
        if FORBIDS_CHANGES.search(bar.text) is None:
            permitted.append(name)
    return tuple(permitted)


def _corrections() -> str:
    """The `persona:` values that would resolve this conflict, said as one remedy.

    One sentence whatever the engine ships, rather than a remedy per cardinality: an
    engine declaring no permitting role at all is a real state — a plan is checked
    against whichever release this host installed — and it deserves the same sentence
    with an empty list rather than a second message nothing here can drive.
    """
    permitted = ", ".join(f"`{name}`" for name in permitting_roles())
    # tests/test_criteria_guard.py covers the empty list with substituted engine bytes,
    # and no recipe journey can: this checkout's installed binary ships four permitting
    # roles, and `uv run` puts `.venv/bin` — where that binary is — on the child's PATH,
    # so a journey could only ever drive the list this release happens to have.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    return (
        f"name a persona whose bar permits the edit "
        f"({permitted or 'none of the shipped roles does'}), or move the editing into "
        f"a node of its own"
    )


def _condensed(criterion: str) -> str:
    """``criterion`` as the one line an error message can quote it on."""
    return " ".join(criterion.split())


def _path_requires_change(criterion: str, path: re.Match[str]) -> bool:
    """Whether the action governing ``path`` says it changes rather than reads it."""
    actions = [
        (min(abs(action.start() - path.start()), abs(action.end() - path.end())), True)
        for action in CHANGE_WORD.finditer(criterion)
    ]
    actions.extend(
        (min(abs(action.start() - path.start()), abs(action.end() - path.end())), False)
        for action in READ_WORD.finditer(criterion)
    )
    return min(actions, default=(0, False), key=lambda action: (action[0], not action[1]))[1]


def check_changes_allowed(block: str, node_id: str, bar: Bar) -> None:
    """Raise :class:`CriteriaError` if the bar forbids the change the criteria require.

    Neither half is a fault on its own, and that is why this is checked as a pairing:
    a bar that refuses any change to the tree is exactly right for a node that only
    answers a question, and a criterion asserting a file changed is exactly right for
    a node that edits one. Together they describe a node no worker can settle.
    """
    forbidden = FORBIDS_CHANGES.search(bar.text)
    if forbidden is None:
        return
    for criterion in criteria_items(block):
        path = QUOTED_PATH.search(criterion)
        if path is None or not _path_requires_change(criterion, path):
            continue
        raise CriteriaError(
            f"{node_id}: {bar.source} forbids this dispatch changing project files "
            f"({_condensed(forbidden.group(0))!r}), and its criteria require "
            f"`{path['path']}` to "
            f"change ({_condensed(criterion)!r}). The judge is then required to fail the "
            f"work the task is required to produce, so no worker can settle this node — "
            f"{_corrections()}."
        )


def criteria_block(task: str) -> str:
    """The task's ``## Acceptance criteria`` block, and nothing after it."""
    return _split(task)[1]


def _split(task: str) -> tuple[str, str]:
    """The task's prose outside its criteria block, and the criteria block itself."""
    start = task.find(CRITERIA_HEADING)
    if start < 0:
        raise CriteriaError(f"no {CRITERIA_HEADING!r} section")
    opened = start + len(CRITERIA_HEADING)
    # Stop at the next heading of any depth. The operational appendix is spelled
    # `### ...`, so matching only `\n## ` swept it into the criteria block and made
    # the guard fire on commands that were never criteria.
    ends = re.search(r"\n#{2,}\s", task[opened:])
    stops = len(task) if ends is None else opened + ends.start()
    return task[:start] + task[stops:], task[opened:stops]


_HEADING = re.compile(r"^#{2,}\s+(?P<title>.+)$", re.MULTILINE)


def _section_of(prose: str, at: int) -> str:
    """The heading ``at`` falls under, for an error that says where a demand is made."""
    headings = [found for found in _HEADING.finditer(prose) if found.start() < at]
    return f"`{headings[-1]['title'].strip()}`" if headings else "its opening prose"


def check(task: str, node_id: str, bar: Bar) -> None:
    """Raise :class:`CriteriaError` if ``task`` would be judged on something it omits."""
    prose, block = _split(task)

    lowered = block.lower()
    for phrase in OUT_OF_DISPATCH:
        if phrase in lowered:
            raise CriteriaError(
                f"{node_id}: criteria name '{phrase}' — that is work the dispatch cannot "
                f"do, so finished work fails against it. State the worker-side "
                f"precondition instead."
            )
    deferred = DEFERRAL.search(block)
    if deferred:
        raise CriteriaError(
            f"{node_id}: criteria defer their content to prose elsewhere "
            f"({deferred.group(0)!r}). A criterion the judge has to reconstruct is one it "
            f"reconstructs as a wording demand. State the property here, in full."
        )
    quoted = PHRASE.search(block)
    if quoted:
        raise CriteriaError(
            f"{node_id}: criteria demand a particular string ({quoted.group(0)!r}) rather "
            f"than a particular property. Ask for the content in '## Additional info'; "
            f"criteria state what must be true of the tree."
        )
    for pattern, why in PROCEDURE:
        named = pattern.search(block)
        if named:
            raise CriteriaError(
                f"{node_id}: criteria {why} ({named.group(0)!r}). Criteria state properties; "
                f"put the command in '## Additional info' and say that running the pieces "
                f"separately is fine."
            )
    check_changes_allowed(block, node_id, bar)
    check_demands(prose, block, node_id, bar)


def check_demands(prose: str, block: str, node_id: str, bar: Bar) -> None:
    """Raise :class:`CriteriaError` for a demand this node is held to but does not state.

    The demand is looked for where it will actually be made: in the resolved review
    bar, and in the task's own prose outside the criteria — the appendix a task
    carries lives there, and a demand it makes is one the judge reads and the
    criteria never answer.
    """
    for demand in DEMANDS:
        if demand.stated_by.search(block):
            continue
        if demand.made_by.search(bar.text):
            where = bar.source
        else:
            made = demand.made_by.search(prose)
            if made is None:
                continue
            where = f"this task's own {_section_of(prose, made.start())}"
        raise CriteriaError(
            f"{node_id}: {where} demands {demand.name}, and the criteria are silent about "
            f"it — so the judge imports the demand and applies its own reading of it, "
            f"which has already failed finished work. State it as a criterion: "
            f"{demand.remedy}."
        )


def check_appendix(task: str, node_id: str) -> None:
    """Raise :class:`CriteriaError` if ``task`` does not carry the current appendix.

    The appendix is copied into every node's task by whatever builds the plan, so a
    builder cloned before an appendix fix silently reintroduces the wording that fix
    removed. That is not hypothetical: the buried cheap-loop rule cost one node about
    84 minutes after it had already been written down, and the sentinel that waited on
    two of the complete gate's three parts failed another for running them separately.
    """
    current = (REPO_ROOT / APPENDIX).read_text(encoding="utf-8").strip()
    if current not in task:
        raise CriteriaError(
            f"{node_id}: task does not carry the current operational appendix. Rebuild it "
            f"from {APPENDIX} rather than from an older builder's copy."
        )


class Node(NamedTuple):
    """One node of a plan that dispatches an agent, read at the plan's boundary."""

    #: How this node is named in a refusal: its own id, or `<lifecycle>/<step>`.
    id: str
    #: The name or path its review bar resolves from; `None` leaves the base config's.
    persona: str | None
    task: str


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    """``value`` as a plan object, refused by name when it is something else.

    A plan is a JSON document some other tool wrote, so every container this walks
    is untrusted input. Refusing it here names the field a plan's author has to fix;
    reaching in and hoping raises whatever `AttributeError` the shape happens to
    produce, six frames from anything they can act on.
    """
    if not isinstance(value, Mapping):
        raise CriteriaError(
            f"{where} is {type(value).__name__}, not an object; a plan states its nodes "
            f"as JSON objects — see examples/tracked-graph.example.json"
        )
    return value


def _listed(value: object, where: str) -> Sequence[Any]:
    """``value`` as a plan list, refused by name when it is something else."""
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise CriteriaError(
            f"{where} is {type(value).__name__}, not a list; a plan states its nodes and "
            f"steps as JSON arrays — see examples/tracked-graph.example.json"
        )
    return value


def _named(node: Mapping[str, Any], where: str, fallback: str) -> str:
    """A node's own id, refused when it is present and not a string.

    Silently falling back would rename the node in every refusal this guard prints,
    so the one message the plan's author has to act on would name something their
    plan does not contain.
    """
    identifier = node.get("id", fallback)
    if not isinstance(identifier, str):
        raise CriteriaError(
            f"{where} states `id` as {type(identifier).__name__}, not a string; quote it as "
            f"a JSON string, because a node's id is what names it in a run's journal, in "
            f"its branch, and in every refusal here"
        )
    return identifier


def dispatched_nodes(plan: object) -> Iterator[Node]:
    """Every node of ``plan`` that dispatches an agent, in the order it states them.

    A `kind: human` node carries an action a person performs rather than a task a
    judge reads, and a lifecycle node carrying `steps` runs each of them on one
    branch — so the step is the dispatch and the node above it is not.
    """
    document = _mapping(plan, "the plan")
    if "tasks" not in document:
        raise CriteriaError(
            "the plan states no `tasks`, so there is nothing here to launch; a plan is the "
            "file `just orchestrate` loads — see examples/tracked-graph.example.json"
        )
    tasks = _listed(document["tasks"], "the plan's `tasks`")
    for index, task in enumerate(tasks):
        read = _mapping(task, f"`tasks[{index}]`")
        yield from _nodes(read, _named(read, f"`tasks[{index}]`", f"tasks[{index}]"))


#: The one `kind` a plan may state. Everything else is an agent node, which it says
#: by carrying no `kind` at all — so an unrecognized value is not a third shape, it
#: is a node whose author believed it was one. See docs/orchestration.md, "Node shapes".
HUMAN = "human"


def _nodes(node: Mapping[str, Any], node_id: str) -> Iterator[Node]:
    match node:
        # A guard rather than a value pattern: a bare name in a pattern captures.
        case {"kind": kind} if kind == HUMAN:
            return
        case {"kind": other}:
            raise CriteriaError(
                f"{node_id}: `kind` is {other!r}; the only kind a plan states is "
                f"{HUMAN!r}, and an agent node says so by carrying no `kind` at all — "
                f"drop the field or correct it, because an unrecognized one dispatches"
            )
        case {"steps": steps}:
            for index, step in enumerate(_listed(steps, f"{node_id}'s `steps`")):
                where = f"{node_id}'s `steps[{index}]`"
                read = _mapping(step, where)
                yield from _nodes(read, f"{node_id}/{_named(read, where, str(index))}")
        case {"task": str() as task}:
            persona = node.get("persona")
            if persona is not None and not isinstance(persona, str):
                raise CriteriaError(
                    f"{node_id}: `persona` is {type(persona).__name__}, not a string; state "
                    f"either a shipped role's name or a path relative to {GRAPHS}/, because "
                    f"that value is handed to `oneagentgraph` verbatim"
                )
            yield Node(node_id, persona, task)
        case _:
            raise CriteriaError(
                f"{node_id}: node dispatches an agent but states no `task` string, so "
                f"there are no acceptance criteria for its judge to review against"
            )


def check_plan(plan: object) -> int:
    """Check every dispatched node of ``plan``; return how many were checked."""
    checked = 0
    for node in dispatched_nodes(plan):
        check(node.task, node.id, resolve_bar(node.persona))
        check_appendix(node.task, node.id)
        checked += 1
    return checked


def main(argv: Sequence[str] | None = None) -> int:
    """Check a plan file before it is launched, from `just check-plan`."""
    parser = argparse.ArgumentParser(
        description=(
            "Refuse a plan whose node would be judged against a demand its task does not state."
        )
    )
    parser.add_argument("plan", type=Path, metavar="PLAN.json")
    args = parser.parse_args(argv)
    try:
        document = args.plan.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"check-plan: cannot read {args.plan}: {exc}; pass the path of the plan file "
            f"you are about to hand `just orchestrate`",
            file=sys.stderr,
        )
        return 2
    try:
        plan = json.loads(document)
    except ValueError as exc:
        print(
            f"check-plan: cannot read {args.plan}: it is not JSON ({exc}); correct the "
            f"document and retry",
            file=sys.stderr,
        )
        return 2
    try:
        checked = check_plan(plan)
    except CriteriaError as exc:
        print(f"check-plan: {exc}", file=sys.stderr)
        return 1
    # tests/test_criteria_guard.py covers this, and no recipe journey can: the recipe
    # runs in this checkout, which by construction holds the review configuration
    # whose absence this reports.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    except OSError as exc:
        print(
            f"check-plan: cannot read this checkout's own review configuration: {exc}; run "
            f"`just bootstrap` from the repository root and retry",
            file=sys.stderr,
        )
        return 2
    print(f"check-plan: {checked} dispatched node(s) state the bar they are judged against")
    return 0
