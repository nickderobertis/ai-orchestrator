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
  from the built-in role's bar;
* one required a lockfile to resolve a sibling to an exact version. The sibling
  published a newer one between the task being written and the node being dispatched,
  the worker resolved the newest as that repository's own manifest demands, and the
  judge failed finished, gate-green work for doing the right thing.

The first pair is what the criteria checks below refuse. The second pair and the
version literal are not, and where each question is asked is the line this module is
now drawn along. **Whether a criterion's truth turns on something outside the
dispatch is structural**, so it stays here. **Whether a number is the right number,
and whether the criteria answer a demand their own bar makes, need judgment**, so
:mod:`orchestrator.plan_review`'s judged turn asks them instead — one verdict that can
hold both considerations at once.

Holding proxies for those two here cost three judged rounds on real plans, because the
tiers refused each other's required wording rather than adding up. One review
prescribed its own remedy — pin the immutable version in the criteria — which the
version-literal rule then refused outright. Another refused a criterion for pinning a
spelling in place of a property, while this tier refused the same task for lacking a
literal phrase its criteria stated across three sentences without using those words;
and because a review record is keyed on the task's own content, inserting words to
satisfy a matcher invalidated the record and bought another judged turn.

One shape reaches further outside the dispatch than any of those and is refused here
for exactly that reason: a criterion asserting that somebody else's **released
artifact** exists, or that it carries a named change. Whether a release exists is not a
fact about the finished tree at all, and establishing it means going and reading
another repository. One such criterion required a pin to name a plan-store release
carrying two fixes that no release archive can carry; the worker correctly determined
it could not be satisfied, and the node was killed and settled by hand with the rest of
its work complete and landed. It is an entry of :data:`OUT_OF_DISPATCH` beside the
merge path's own verdict, and it is one of the two this module does **not** ask of an
amendment — :func:`check_amendment` says why.

One shape on that list is **admitted** under a condition, and the condition is the
task's own. A worker may open its session's change request as a draft it holds, and a
throwaway demonstration change request stacked on that draft, when its task's own
``## Additional info`` grants either in the words `config/dispatch-appendix.md`'s
carve-out names — so a criterion whose subject is that draft, or that demonstration
change request, and whose predicate is the publication entry, is a property of state the
worker controls. :data:`AUTHORIZATIONS` holds the two grants and :func:`_admitted` reads
the clause's subject; the same criterion on a task granting nothing is refused exactly as
before, naming the grant to write, and a merge, a landing, a required check's verdict or
a release stays refused whatever the subject says. `docs/plan-review-refusals.md`
records the false refusal this prevents.

One shape is worse than any of them, because no wording of the criteria rescues it:
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

One refusal here is not about a bar at all. Every plan whose node failed above was
written by an operator and launched with no planner, so ``personas/planner.yaml``'s
judge — which exists to catch exactly this — never read those criteria.
:mod:`orchestrator.plan_review` is what closes that, and :func:`main` refuses a task
carrying no review record for what it currently says; this module only reads that
answer, because detecting *who typed* a node is the wrong question and detecting
unreviewed content covers both the hand-written plan and the tweaked one.

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

import hashlib
import re
import shutil
import sys
from collections.abc import Iterator, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from orchestrator import adoption_guard, plan_review, plan_store, publication_guard
from orchestrator.root import REPO_ROOT

#: The operational text every dispatched task carries, and the one source a plan
#: builder copies it from. It was gitignored scratch until this module tracked it,
#: which is how it came to contradict itself for long enough to fail a node.
APPENDIX = Path("config") / "dispatch-appendix.md"

#: The environment variable a planning launch hands that text over in, and the one place
#: that name is composed — `scripts/dispatch-appendix-env.sh` asks this module for both
#: the name and the value rather than spelling either. The party required to copy the
#: appendix into every task is the planner, which works in a worktree of its own while
#: the tracked file above lives in the launching checkout: naming it by a path relative
#: to this checkout told that planner to open a file that does not exist from where it
#: stands, and a manager ended up appending the text by hand.
APPENDIX_ENV = "ORCHESTRATOR_DISPATCH_APPENDIX_TEXT"

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


#: What a criterion resting outside the dispatch is told to do instead. The default,
#: because for every shape but one the correction is the same: say what has to be true
#: on the worker's side of the event.
STATE_THE_PRECONDITION = "State the worker-side precondition instead."

#: And what a criterion about somebody else's released artifact is told instead. A
#: release is not a fact about the finished tree, so there is no worker-side
#: precondition to state: what is left is the content this node commits, plus the check
#: that later compares it against the registry — the corresponding-content shape
#: `personas/planner.yaml` admits for exactly this case.
STATE_WHAT_THE_TREE_CARRIES = (
    "A release is not a property of the finished tree, so state what this node's own "
    "committed content must carry — the pin, the declaration, the record — and name the "
    "check that later compares it against the registry, saying that check's result is "
    "not this node's bar."
)


class OutOfDispatch(NamedTuple):
    """One way a criterion rests on state the worker's own dispatch does not reach."""

    pattern: re.Pattern[str]
    #: A criterion fragment this matches whole, so the journey that drives every entry
    #: has one to drive and the refusal it reads quotes exactly this.
    example: str
    #: What the criterion's author is told to write instead.
    remedy: str = STATE_THE_PRECONDITION
    #: Whether :func:`check_amendment` asks this of an amendment too. False only where
    #: the ordinary mid-run correction is the very thing the entry refuses; the reason
    #: per entry is beside it.
    of_an_amendment: bool = True
    #: Whether a task's own authorization can admit a match of this entry, when the
    #: clause's subject is the thing that authorization lets the worker do. True only
    #: for the publication shapes, because publishing *as a draft* is the one thing on
    #: this list a worker may now do from inside its dispatch; a merge, a landing, a
    #: required check's verdict and a release are as far outside it as they ever were,
    #: whatever the clause's subject is.
    admissible: bool = False


# Work the dispatch cannot perform: it happens after the worker settles. Patterns rather
# than phrases, because the forms `docs/plan-review-refusals.md` caught this list missing
# differ from the phrase beside them only by a word.
OUT_OF_DISPATCH = (
    OutOfDispatch(re.compile(r"branch publishes"), "branch publishes"),
    # Bounded at two intervening words, because a longer run stops being one clause; and
    # no negation may be one of them, because "a release is not published for it" states
    # the precondition this refusal exists to ask for, and the bare phrase never matched
    # a negation either.
    #
    # The one admissible entry. "The worker's draft is published" and "the demonstration
    # change request is published as a draft" are properties of state the worker controls
    # once its task grants the carve-out `config/dispatch-appendix.md` names, and
    # :func:`_admitted` is what decides that — on the clause's subject, never on the word
    # `draft` appearing somewhere in the criterion.
    OutOfDispatch(
        re.compile(r"\b(?:is|are|was|were|be|been)(?: (?!not\b|never\b|no\b)\w+){0,2} published\b"),
        "is published",
        admissible=True,
    ),
    OutOfDispatch(re.compile(r"pr is merged"), "pr is merged"),
    OutOfDispatch(re.compile(r"pull request is merged"), "pull request is merged"),
    # The draft and the demonstration change request, merged. Neither of the two entries
    # above reaches "the draft is merged", and the admission below would otherwise be
    # read as the word `draft` licensing whatever follows it. A merge is the lifecycle's
    # whatever the subject, so this is refused under every authorization — and it is
    # anchored on those two subjects rather than on a bare `is merged`, which the corpus
    # records refusing "Nothing is merged, pushed, or opened" on a task that passed.
    OutOfDispatch(
        re.compile(
            r"\b(?:draft|demonstration)(?: [\w'’]+){0,3} "
            r"(?:is|are|was|were|gets?|has been|have been) merged\b"
        ),
        "draft is merged",
    ),
    OutOfDispatch(re.compile(r"lands on master"), "lands on master"),
    OutOfDispatch(re.compile(r"lands on main"), "lands on main"),
    OutOfDispatch(re.compile(r"deploy"), "deploy"),
    # The merge path's own verdict: those checks run on the host after publication, and
    # the dispatch has ended before any of them start. It reached this list from an
    # *amendment* rather than from a plan — :func:`check_amendment` quotes the one it
    # cost — and a plan criterion of the same shape is the same unsatisfiable demand.
    #
    # Which is why it is an entry of this list rather than a pattern of the amendment
    # check's own: every entry here is driven through the real `just check-plan` over a
    # real plan by `tests/plan_tooling/test_check_plan_recipe_e2e.py`'s
    # `test_a_criterion_resting_on_work_the_dispatch_cannot_do_is_refused`, which
    # parametrizes over this tuple and refuses each entry's own `example` — so an entry
    # added here is a plan-check journey with nothing to write.
    OutOfDispatch(
        re.compile(r"required checks?(?: \w+){0,2} (?:pass|passes|are green|is green|succeed)"),
        "required checks pass",
    ),
    # Somebody else's released artifact, asserted to carry a named change. The incident
    # is in this module's docstring: a pin was required to name a release carrying two
    # fixes no release archive can carry, and the node was killed by hand with the rest
    # of its work landed. Anchored on a determiner so the artifact noun is the head of
    # its own phrase — "the release notes contain the change" names a file in the tree
    # and is left alone, where a bare `release .* contains` would refuse it. `package`
    # and `bundle` are deliberately not nouns here: a Python package and a built bundle
    # are both ordinary in-tree subjects, and this check refuses a plan outright.
    OutOfDispatch(
        re.compile(
            r"\b(?:an|a|the|that|this|some|any)\b(?: [\w.+-]+){0,2}? "
            r"(?:release|version|wheel|crate|distribution|artifact|archive)s? "
            r"(?:that (?:contains?|carr(?:y|ies)|includes?)"
            r"|contain(?:s|ing)|carr(?:ies|ying)|includ(?:es|ing))\b"
        ),
        "a release that contains",
        remedy=STATE_WHAT_THE_TREE_CARRIES,
        of_an_amendment=False,
    ),
    # And asserted simply to be there. Keyed on the **locus** rather than on the artifact
    # noun, because "the package exists in the lockfile" is a property of the finished
    # tree and `exists on the registry` can be nothing else. Registries are named rather
    # than generalized for the same reason.
    OutOfDispatch(
        re.compile(
            r"\b(?:exists?|is available|are available|is present|shows up|appears) "
            r"(?:on|in|at) (?:the )?(?:registry|crates\.io|pypi|npm|index|upstream)\b"
        ),
        "exists on the registry",
        remedy=STATE_WHAT_THE_TREE_CARRIES,
        of_an_amendment=False,
    ),
)


class Authorization(NamedTuple):
    """One thing a task's own ``## Additional info`` may let its worker do on the host.

    The carve-out in `config/dispatch-appendix.md` names two, each conditional on the
    task saying so in its own words above the operational notes: publishing the session's
    change request early, as a draft the worker holds, and opening a throwaway
    demonstration change request stacked on it. A criterion about either is a property
    of state the worker controls — but only for a worker whose task grants it, because a
    worker of any other task may not do the thing the criterion rests on.
    """

    #: What it is, for the refusal and the test id.
    name: str
    #: How a task grants it: the appendix carve-out's own words, which is what
    #: `personas/planner.yaml` tells a planner to write and what keeps the grant one
    #: sentence a worker, its judge and this check all read alike.
    granted_by: re.Pattern[str]
    #: What the subject of an admitted clause names. The admission is on the subject
    #: rather than on the word appearing anywhere, because "the draft is merged" and
    #: "the demonstration PR lands on main" have to stay refused.
    subject: re.Pattern[str]
    #: What a task writes to grant it, for the refusal that says so.
    grant: str
    #: A criterion this admits whole under that grant, so the journeys that drive every
    #: authorization have one admitted and one refused example each.
    example: str


#: The demonstration one first, because :func:`_admitted` takes the first subject that
#: matches and "the demonstration draft is published" is about the demonstration change
#: request — a task authorizing only early publication has not authorized that.
AUTHORIZATIONS = (
    Authorization(
        "a demonstration change request",
        re.compile(
            r"\bauthori[sz]e\w*(?: [\w'’]+){0,3} (?:throwaway )?demonstration change request"
            r"|\bdemonstration change request(?: [\w'’]+){0,3} (?:is |are )?authori[sz]e\w*"
        ),
        re.compile(r"\bdemonstration\b"),
        "a throwaway demonstration change request is authorized",
        "the demonstration change request is published as a draft against the session branch",
    ),
    Authorization(
        "early publication",
        re.compile(r"\bchange request may be published early\b"),
        re.compile(r"\bdraft\b"),
        "the change request may be published early",
        "the worker's draft is published carrying the evidence",
    ),
)

_EVERY_AUTHORIZATION = frozenset(one.name for one in AUTHORIZATIONS)

#: Where one clause ends and the next begins, for reading the subject of the clause a
#: refused phrase sits in. Deliberately coarse: a subject this cannot read is refused
#: with the wording to use, which costs one rewrite, where a subject read across a
#: clause boundary admits a criterion about something else.
_CLAUSE_BOUNDARY = re.compile(r"[\n;,(:]|\band\b|\bor\b|—|-\s")

#: The task's own `## Additional info`, as distinct from the operational appendix that
#: opens under the same heading. A task carries the heading twice when its author wrote
#: anything of their own — the appendix is copied in verbatim below it, heading included
#: — so the author's section is the block the first opening holds, and it is read only
#: when the level-2 heading that closes that block is the second opening: the
#: appendix's, directly below it, which is where every author is told to write. A task
#: opening it once has no section of its own, and what the engine appends after the
#: appendix — `## Planner context`, the cross-repository references — is never read,
#: however it is worded. That last clause is why the boundary is the block's own
#: closing heading rather than the *last* opening: the engine appends a carried note's
#: text verbatim, so a note quoting a task spells this heading on a line of its own, and
#: bounding at the last opening pulled everything above that line — the appendix's own
#: carve-out sentences and the note's — into the read, admitting a criterion on a task
#: that granted nothing. Written to miss: an author's section closed by any other
#: heading is read as no section, which costs one move of the text to above the
#: appendix, where reading it would trust text the author did not write.
_OPENS_ADDITIONAL_INFO = re.compile(r"^## Additional info[ \t]*$", re.MULTILINE)


def own_additional_info(task: str) -> str:
    """The text ``task``'s author wrote under ``## Additional info``, above the appendix."""
    opens = list(_OPENS_ADDITIONAL_INFO.finditer(task))
    if len(opens) < 2:
        return ""
    closes = SECTION_HEADING.search(task, opens[0].end())
    if closes is None or closes.start() != opens[1].start():
        return ""
    return task[opens[0].end() : opens[1].start()]


#: Where one sentence of a grant's carrier ends: a terminal mark and the space after it,
#: or a line break. A grant is read one sentence at a time, because whether a sentence
#: grants is decided by the whole of it and not by a phrase inside it.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?;])\s+|\n")

#: What turns a sentence naming a carve-out into its opposite. A sentence carrying one
#: of these grants nothing, however the rest of it is worded: *"a demonstration change
#: request is not authorized"* and *"never authorize a throwaway demonstration change
#: request"* both name the carve-out in the words the appendix uses to grant it, and a
#: reader that matched the phrase alone admitted a criterion the task had just
#: forbidden. Written to miss rather than to over-admit: a genuine grant whose own
#: sentence also says "not" about something else is not read, which costs one rewrite
#: into the sentence the appendix names, where reading it would admit a publication a
#: task had refused.
_NEGATED = re.compile(r"\b(?:not|never|no|nor|neither|without)\b|n't\b")


def authorizations(text: str) -> frozenset[str]:
    """The names of every authorization ``text`` grants, in the carve-out's own words.

    Read sentence by sentence, and a sentence that negates is not a grant — see
    :data:`_NEGATED` for why the phrase alone cannot decide it.
    """
    granted: set[str] = set()
    for sentence in _SENTENCE_BOUNDARY.split(text):
        if _NEGATED.search(sentence.lower()):
            continue
        granted.update(one.name for one in AUTHORIZATIONS if one.granted_by.search(sentence))
    return frozenset(granted)


def _admitted(
    block: str, rests_on: re.Match[str], entry: OutOfDispatch, granted: frozenset[str]
) -> Authorization | None:
    """The authorization that admits ``rests_on``, or ``None`` when it stays refused.

    Read on the subject of the clause the match sits in: the text from the previous
    clause boundary to the match. A subject naming a demonstration change request needs
    that authorization; one naming the draft needs early publication; any other subject
    — the branch, the change request unqualified, the node's own publication — is refused
    exactly as before, and so is every entry that is not ``admissible`` whatever the
    subject says.
    """
    if not entry.admissible:
        return None
    lowered = block.lower()
    boundaries = _CLAUSE_BOUNDARY.finditer(lowered, 0, rests_on.start())
    starts = [0, *(found.end() for found in boundaries)]
    subject = lowered[max(starts) : rests_on.start()]
    for one in AUTHORIZATIONS:
        if one.subject.search(subject):
            return one if one.name in granted else None
    return None


#: What a criterion naming an invocation is told to do instead. Kept as the default
#: rather than inlined at the raise, because the shape below it — a development step
#: prescribed in place of the property that step produces — is not a command anybody
#: can move into `## Additional info` unchanged.
RUN_IT_ELSEWHERE = (
    "Criteria state properties; put the command in '## Additional info' and say that "
    "running the pieces separately is fine."
)


class Procedure(NamedTuple):
    """One way a criterion prescribes a procedure in place of a property, and its remedy."""

    pattern: re.Pattern[str]
    why: str
    #: What may stand immediately after a match without the criterion being a demand
    #: to run anything. ``None`` where nothing may.
    exempt: re.Pattern[str] | None = None
    #: What the criterion's author is told to write instead. Per entry, because the
    #: correction differs by shape: an invocation moves to the operational notes, while
    #: a prescribed development step is replaced by the property it produces.
    remedy: str = RUN_IT_ELSEWHERE


#: This repository's own plan-reading recipes, which a criterion **about** the plan
#: tooling has to be able to name. "`just check-plan` refuses a plan naming its
#: repository twice" states what that command does, and the node whose job is to make
#: it do that cannot say so without naming it. None of these runs over a worker's own
#: change — they read, review, or launch a plan — so naming one is never the "run this
#: exact invocation" demand :data:`PROCEDURE` exists to refuse, which is what makes
#: this an exemption rather than a hole: `just gate` and `just check` are still
#: refused, and they are the ones a judge fails finished work on the spelling of.
PLAN_TOOLING = re.compile(r"\s*(?:check-plan|review-plan|orchestrate|plans)\b")

# Procedure rather than property. A criterion naming an invocation is one a judge
# can fail on spelling.
PROCEDURE = (
    Procedure(re.compile(r"`[^`]*\bjust\s"), "names a `just` invocation", PLAN_TOOLING),
    Procedure(re.compile(r"&&"), "names a chained shell command"),
    Procedure(re.compile(r"`[^`]*\b(npm|pnpm|nx|cargo|pytest|git)\s"), "names a shell invocation"),
    # Red before green. A worker is told to do it in `config/dispatch-appendix.md`; as a
    # *criterion* it asks for a development step the finished tree cannot carry, so a
    # judge can only take the worker's word for it. `docs/plan-review-refusals.md` is
    # what the two spellings below are drawn from.
    Procedure(
        re.compile(
            r"\b(?:observ\w+|seen|demonstrated|shown)\s+(?:to\s+)?fail\w*\b[^.]{0,160}?"
            r"\bbefore it passes\b"
            r"|\bfail(?:s|ing)?\s+(?:for|against)\s+(?:the|its)\s+intended reason\b"
            r"[^.]{0,120}?\bbefore\b",
            re.I,
        ),
        "prescribe a red-before-green development step",
        remedy=(
            "Criteria state properties of the finished tree; ask for the property that "
            "step produces — an assertion whose subject is the behaviour this change "
            "adds, so removing that behaviour fails it — and put the step itself in "
            "'## Additional info'."
        ),
    ),
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


class RestsOutside(NamedTuple):
    """A match of :data:`OUT_OF_DISPATCH` that no authorization admitted."""

    rests_on: re.Match[str]
    entry: OutOfDispatch
    #: The authorization whose subject the clause named, when the match would have been
    #: admitted under it and the task grants none — so the refusal can say which grant
    #: to write rather than which precondition to state.
    wanted: Authorization | None


def _out_of_dispatch(
    block: str, *, of_an_amendment: bool = False, granted: frozenset[str] = frozenset()
) -> RestsOutside | None:
    """The first thing ``block`` rests on that the worker's own dispatch never reaches.

    One reader for both carriers of criteria — a node's ``## Acceptance criteria`` and
    the amendment that overrides one — so :data:`OUT_OF_DISPATCH` is the single answer
    to the question rather than a list each caller re-walks its own way. Which entries
    that answer is taken over is the carrier's: an amendment is asked only the entries
    whose refusal means the same thing mid-run, which is a field of each entry rather
    than a second list.

    ``granted`` is what the carrier's own text authorizes, read by :func:`authorizations`
    — a task's from its own ``## Additional info``, an amendment's from the amendment
    itself — and a match :func:`_admitted` admits under one of them is passed over so
    scanning continues, the way :func:`_prescribed` continues past an exempt match.

    The entry is returned beside its match because the correction differs by shape, the
    way :func:`_prescribed` returns its procedure.
    """
    lowered = block.lower()
    # The sentence that grants a carve-out names a publication — "may be published
    # early" — and is a grant rather than a criterion about one. It reaches this reader
    # only in an amendment, which carries its grant in its own text; a task's grant lives
    # under its `## Additional info`, outside the criteria block.
    grants = [span.span() for one in AUTHORIZATIONS for span in one.granted_by.finditer(lowered)]
    for entry in OUT_OF_DISPATCH:
        if of_an_amendment and not entry.of_an_amendment:
            continue
        for rests_on in entry.pattern.finditer(lowered):
            if any(start <= rests_on.start() < end for start, end in grants):
                continue
            if _admitted(block, rests_on, entry, granted) is not None:
                continue
            wanted = _admitted(block, rests_on, entry, _EVERY_AUTHORIZATION)
            return RestsOutside(rests_on, entry, wanted)
    return None


def _unauthorized(wanted: Authorization | None, carrier: str) -> str:
    """How a refusal says that a grant, rather than a precondition, is what is missing."""
    if wanted is None:
        return ""
    return (
        f" A criterion about {wanted.name} is admitted only for a task whose own "
        f"`## Additional info` grants it above the operational appendix, in the carve-out's "
        f'words — "{wanted.grant}" — and {carrier} does not.'
    )


def _prescribed(block: str) -> tuple[re.Match[str], Procedure] | tuple[None, None]:
    """The first procedure ``block`` prescribes in place of a property, and which one.

    Every match is read against its own exemption before it is reported, so a
    criterion naming this repository's plan tooling is passed over and scanning
    continues — one criterion may name `just check-plan` and the next `just gate`,
    and stopping at the first match would report neither or the wrong one.

    The entry is returned rather than its `why` alone because the correction differs by
    shape: a command moves into the operational notes, where a prescribed development
    step has to be replaced by the property it produces.
    """
    for procedure in PROCEDURE:
        for named in procedure.pattern.finditer(block):
            if procedure.exempt is not None and procedure.exempt.match(block, named.end()):
                continue
            return named, procedure
    return None, None


def check_backticks_pair(block: str, node_id: str) -> None:
    """Raise :class:`CriteriaError` for a criterion whose inline code never closes.

    Every pattern below reads inline code by pairing backticks, and an unpaired one
    pairs with the next criterion's instead: the run then spans criteria the author
    wrote separately, and the refusal quotes a match that begins in one and ends in
    another. One such block held seventeen backticks and was refused for "naming a
    shell invocation", quoting a span that ended at the word `git` inside the phrase
    "real git repositories" three criteria later — a refusal about nothing, whose only
    correction was to make a sound criterion vaguer.

    So the imbalance is refused first and by name, before any pattern reads the block.
    It is counted per criterion rather than over the whole block, which answers both
    halves of the same question: a block whose total is odd has at least one criterion
    whose own count is odd, and quoting that one criterion is what tells its author
    where the stray backtick is.
    """
    for criterion in criteria_items(block):
        if criterion.count("`") % 2:
            raise CriteriaError(
                f"{node_id}: a criterion leaves a backtick run unclosed "
                f"({_condensed(criterion)!r}). Inline code is read by pairing backticks, "
                f"so an unclosed run pairs with the next criterion's and every check over "
                f"this block quotes a span crossing criteria their author wrote apart. "
                f"Close the run, or drop the stray backtick."
            )


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


#: Where a task **opens** its acceptance criteria: the heading spelled at the start of
#: a line, rather than wherever that text happens to occur. Prose that merely names the
#: heading — inside inline code, or mid-sentence — is not the block, and reading it as
#: one began the block at that mention and ended it at the next heading after it, which
#: left every criterion the node actually stated sitting in the prose half. Both halves
#: of that fail, and the quiet one is the dangerous one: the loud half is a refusal
#: quoting a span from prose, and the quiet half is a task whose every criterion goes
#: unexamined by the one tier standing between a bad criterion and a failed dispatch.
#:
#: It is the **whole** heading rather than a line that starts with one, which is the
#: same distinction one step further out. `## Acceptance criteria examples` is a
#: different heading opening a different section, and reading it as this one fails both
#: of those ways again: beside the real heading it made a sound task read as opening its
#: criteria twice and refused it, and *alone* it silently handed back the block that
#: heading opens — every line of it something no criterion ever claimed. Trailing
#: whitespace is not part of the spelling, because it is not part of what a reader sees.
OPENS_CRITERIA = re.compile(rf"^{re.escape(CRITERIA_HEADING)}[ \t]*$", re.MULTILINE)

#: Where that block ends: the next heading of any depth, located the same way. The
#: operational appendix is spelled `### ...`, so matching only `## ` swept it into the
#: criteria block and made the guard fire on commands that were never criteria.
CLOSES_CRITERIA = re.compile(r"^#{2,}\s", re.MULTILINE)


def criteria_block(task: str) -> str:
    """The block ``task``'s acceptance-criteria heading opens, and nothing after it.

    A task that opens that heading **more than once** is refused by name rather than
    read. Which of two blocks states the node's bar cannot be decided from such a task:
    the judge is handed the whole task and reads both, so a reader here that picked
    either would be checking one while the dispatch is judged against the other. That is
    the same answer `orchestrator/plan_store.py` gives a record opening `metadata` twice
    — a block this cannot identify unambiguously is better refused than guessed at.
    """
    opens = list(OPENS_CRITERIA.finditer(task))
    if not opens:
        raise CriteriaError(f"no {CRITERIA_HEADING!r} section")
    if len(opens) > 1:
        raise CriteriaError(
            f"the task opens {CRITERIA_HEADING!r} {len(opens)} times, so which block "
            f"states this node's bar cannot be read from it — a judge is handed the whole "
            f"task and reads both. Leave one block of criteria, and say whatever else that "
            f"heading was introducing in prose that does not open it."
        )
    opened = opens[0].end()
    ends = CLOSES_CRITERIA.search(task, opened)
    return task[opened : len(task) if ends is None else ends.start()]


def check(task: str, node_id: str, bar: Bar) -> None:
    """Raise :class:`CriteriaError` if ``task`` would be judged on something it omits."""
    block = criteria_block(task)

    check_backticks_pair(block, node_id)
    outside = _out_of_dispatch(block, granted=authorizations(own_additional_info(task)))
    if outside is not None:
        raise CriteriaError(
            f"{node_id}: criteria name '{outside.rests_on.group(0)}' — that is work the "
            f"dispatch cannot do, so finished work fails against it."
            f"{_unauthorized(outside.wanted, 'this task')} {outside.entry.remedy}"
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
    named, procedure = _prescribed(block)
    if named is not None and procedure is not None:
        raise CriteriaError(
            f"{node_id}: criteria {procedure.why} ({_condensed(named.group(0))!r}). "
            f"{procedure.remedy}"
        )
    check_changes_allowed(block, node_id, bar)


#: The sections of a task some other reader already answers for, and the one section
#: this module's own refusals send an author to. Everything else in a task is a section
#: somebody added, and on the live channel that somebody is a manager writing an
#: amendment into a replacement task — which is the region :func:`unreviewed_sections`
#: hands to :func:`check_amendment`.
#:
#: Each is left alone for its own reason, and the reasons are why this is a set of
#: headings rather than a rule about depth. `## What` and `## Why` are the descriptive
#: halves of the template every plan here is written in and no deterministic tier reads
#: them, so asking the bar's questions of them would refuse, on a retry, prose that was
#: accepted as a plan. :data:`CRITERIA_HEADING` has a reader already and it is
#: :func:`check`. And `## Additional info` is where every refusal in this module tells an
#: author to put the commands it refuses — it is also where the tracked appendix is
#: carried verbatim, and that text names `pkill`, `git status` and a `just` invocation, so
#: a reader that examined it would refuse every task carrying the text this host requires.
DESCRIBED_ELSEWHERE = frozenset({"## What", "## Why", CRITERIA_HEADING, "## Additional info"})

#: Where a task's own sections begin: a level-2 heading on a line of its own. Level two
#: exactly, so a `###` subsection stays inside the section that opened it — the appendix
#: is one `## Additional info` holding two `###` blocks, and splitting at every depth
#: would lift those out of the one section this reader is required to leave alone.
SECTION_HEADING = re.compile(r"^##[ \t]+(?P<heading>\S.*?)[ \t]*$", re.MULTILINE)


def unreviewed_sections(task: str) -> Iterator[tuple[str, str]]:
    """Each section of ``task`` no other reader examines, as its heading and its body.

    The region this exists for is the one that cost a node: a manager retried a failed
    node, wrote an amendment into the replacement task under a heading of its own — the
    placement `AGENTS.md` instructs, above the operational notes — and three readers
    passed it. :func:`check_amendment` declines a `retry` by design, :func:`check` reads
    the acceptance-criteria block and stops at the next heading, and nothing at all read
    what sat between them.

    Text before the first heading is deliberately not yielded, and neither is a `###`
    block: what this locates is a **section somebody opened**, which is the shape the
    instruction produces. That is the same "written to miss rather than to over-refuse"
    trade every other reader here makes — an amendment buried inside `## Why` goes
    unread, and refusing prose that a plan was accepted with would be worse.
    """
    opens = list(SECTION_HEADING.finditer(task))
    for position, opened in enumerate(opens):
        heading = f"## {opened['heading']}"
        if heading in DESCRIBED_ELSEWHERE:
            continue
        ends = opens[position + 1].start() if position + 1 < len(opens) else len(task)
        yield heading, task[opened.end() : ends]


def check_whole_task(task: str, node_id: str, bar: Bar) -> None:
    """Raise :class:`CriteriaError` for anything in ``task`` a judge would hold a worker to.

    The whole task rather than the one block a heading opens, which is the difference
    between this and :func:`check` and the whole of why it exists. A plan's task reaches
    a dispatch only through `just check-plan` and `just review-plan`, which between them
    read its criteria and put its content in front of a judge; a **live edit** states a
    whole task on the channel and passes neither, so the region between its criteria and
    its operational notes reaches a dispatch unread.

    The criteria block is asked the whole bar, exactly as a plan's is. Every other
    section it opens is asked :func:`check_amendment`'s two questions, because that is
    what the text in one *is*: a correction a manager wrote in the minute after reading a
    failure, which is what an amendment is wherever it is carried.
    """
    check(task, node_id, bar)
    for heading, body in unreviewed_sections(task):
        check_amendment(body, f"{node_id}, under {heading!r}")


def check_amendment(text: str, where: str) -> None:
    """Raise :class:`CriteriaError` if an amendment would be judged the way a criterion is.

    An ``amend`` replaces the binding amendment that becomes part of a node's effective
    task, and that task is what the node's judge reads — so an amendment *is* criteria,
    written in the minute after a manager read a failure, which is far more pressure
    than a plan is ever written under. Five were written during one run of this host and
    three cost a node each: *"its change request's required checks pass"* names work that
    happens after the agent step has ended; *"do not re-research it and do not rewrite
    the comment whole"* and *"preserve that result as evidence and stop there"* each
    prescribe a mechanism, and in both cases the route they forbade was the one that
    found the answer. Every one of them settled correct, committed, gate-green work as a
    task failure.

    **Two of the bar's questions, and the rest are deliberately left out.** An amendment
    is an overriding *correction* to a node's criteria rather than the whole bar, so a
    question that only means something over a whole bar cannot be asked of one:

    * :func:`check_changes_allowed` needs the node's resolved review bar, and a reply
      envelope names a node id rather than a persona; nor does an amendment choose one.
    * The two :data:`OUT_OF_DISPATCH` entries about somebody else's released artifact
      are skipped, which is what their ``of_an_amendment`` says. An amendment binds the
      *next* dispatch of a node the manager is watching, and the correction most often
      worth amending mid-run is the one naming the release that has just landed — so
      here those two would refuse the ordinary case rather than the unreachable one.
      Every other entry is asked, because a base branch and a merge path are no nearer
      to a mid-run dispatch than they were to the plan's author.
    * :data:`DEFERRAL` and :data:`PHRASE` refuse a criterion whose content is somewhere
      else or is a particular string. An amendment arrives composed onto the task it
      corrects, so the prose it points at is right there, and a correction about wording
      — a commit subject, a heading — is a legitimate thing to amend.

    **The authorizations are asked of an amendment on the same terms as of a task, and
    read from the amendment's own text.** An amendment carries no ``## Additional info``
    and the reply envelope names a node rather than a task, so the grant that admits a
    criterion about the worker's draft, or about a demonstration change request, has to
    be in the amendment itself — which is where a manager granting it mid-run writes it
    anyway, since the amendment is composed onto the task above the operational notes.

    What is asked first is neither question but the precondition for both: an unclosed
    backtick run makes every pattern below read inline code that was never written, and
    the quote in the refusal then spans text its author wrote apart.

    An amendment that names a mechanism or rests outside the dispatch has an escape that
    a plan's criteria do not, and every refusal here says so: an observation belongs in a
    ``note``, which touches no acceptance criterion at all.
    """
    check_backticks_pair(text, where)
    outside = _out_of_dispatch(text, of_an_amendment=True, granted=authorizations(text))
    if outside is not None:
        raise CriteriaError(
            f"{where}: it names '{outside.rests_on.group(0)}' — that is work the dispatch "
            f"cannot do, so this amendment holds the worker to state that arrives after it "
            f"is gone.{_unauthorized(outside.wanted, 'this amendment')} "
            f"{outside.entry.remedy} Or send it as a `note`, which touches no acceptance "
            f"criterion."
        )
    named, procedure = _prescribed(text)
    if named is not None and procedure is not None:
        raise CriteriaError(
            f"{where}: it {procedure.why} ({_condensed(named.group(0))!r}). An amendment "
            f"becomes part of the node's effective task, so a judge cannot tell a "
            f"mechanism you preferred from a property the node owes. State the outcome "
            f"the finished tree must carry, or send it as a `note`, which touches no "
            f"acceptance criterion."
        )


def appendix_text() -> str:
    """The appendix exactly as a task has to carry it: one contiguous stripped block.

    One reader for both ends of that requirement, which is what makes it answerable from
    where a planner stands. :func:`check_appendix` demands this text as a substring, and
    `scripts/dispatch-appendix-env.sh` exports *this* text under :data:`APPENDIX_ENV`, so
    what a planning dispatch is handed is byte-for-byte what the check requires rather
    than a second rendering of the same file.
    """
    return (REPO_ROOT / APPENDIX).read_text(encoding="utf-8").strip()


def check_appendix(task: str, node_id: str) -> None:
    """Raise :class:`CriteriaError` if ``task`` does not carry the current appendix.

    The appendix is copied into every node's task by whatever builds the plan, so a
    builder cloned before an appendix fix silently reintroduces the wording that fix
    removed. That is not hypothetical: the buried cheap-loop rule cost one node about
    84 minutes after it had already been written down, and the sentinel that waited on
    two of the complete gate's three parts failed another for running them separately.

    It follows that **editing that file invalidates this check for every plan already
    authored**, which is expected rather than a defect in either. A plan is checked and
    launched against the appendix of its day; a plan authored afterwards carries the new
    text, and one authored before is refused here until its tasks are rebuilt from the
    current file — which is what the refusal already says to do.
    """
    if appendix_text() not in task:
        raise CriteriaError(
            f"{node_id}: task does not carry the current operational appendix. Rebuild it "
            f"from the text in ${APPENDIX_ENV}, which every planning launch hands its "
            f"dispatch, or from {REPO_ROOT / APPENDIX} on this host — rather than from an "
            f"older builder's copy."
        )


#: Everything that decides an answer :func:`check_whole_task` and :func:`check_amendment`
#: give, hashed together so that a record of a pass is a claim about content **under a
#: bar** rather than about content alone. Its own source, because the patterns above
#: *are* the bar; and the base config, because the shared review contract composes into
#: every :func:`resolve_bar`. `__file__` rather than a spelled path: a constant naming
#: this module would go on naming it after the module moved, and a fingerprint that
#: silently stopped covering the bar is worse than none.
#:
#: :data:`APPENDIX` is deliberately not here. It decides :func:`check_appendix`, which
#: no caller of this fingerprint runs, and what exempts the section carrying it is that
#: section's **heading** rather than its text — so hashing it would invalidate every
#: record for an edit to prose no reader of this bar had read.
BAR_SOURCES = (Path(__file__), REPO_ROOT / BASE_CONFIG)


def criteria_fingerprint() -> str:
    """A digest of the deterministic bar in force, over this checkout's copy of it.

    Distinct from :func:`orchestrator.plan_review.bar_fingerprint`, which digests the
    **judged** review bar. Both are hex strings of the same length and neither is
    meaningful in the other's place, so they are never interchanged: a record kept under
    one says nothing about the answer the other would give.
    """
    digest = hashlib.sha256()
    for source in BAR_SOURCES:
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class Node(NamedTuple):
    """One node of a plan that dispatches an agent, read at the plan's boundary."""

    #: How this node is named in a refusal: its own id, or `<lifecycle>/<step>`.
    id: str
    #: The name or path its review bar resolves from; `None` leaves the base config's.
    persona: str | None
    task: str


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    """``value`` as a plan object, refused by name when it is something else.

    A plan is what `orchestrator/plan_store.py` assembles out of the answers
    onetaskgraph returns for a qualified project, so every container this walks is
    untrusted input.
    Refusing it here names the field a plan's author has to fix in the store record it
    came from; reaching in and hoping raises whatever `AttributeError` the shape
    happens to produce, six frames from anything they can act on.
    """
    if not isinstance(value, Mapping):
        raise CriteriaError(
            f"{where} is {type(value).__name__}, not an object; a plan is assembled from "
            f"the qualified onetaskgraph project `just check-plan` reads, and its nodes are "
            f"that project's task records — see examples/tasks/tracked-release/"
        )
    return value


def _listed(value: object, where: str) -> Sequence[Any]:
    """``value`` as a plan list, refused by name when it is something else."""
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise CriteriaError(
            f"{where} is {type(value).__name__}, not a list; a plan states its nodes and "
            f"steps as lists, and a lifecycle node's steps are the `onepipeline.steps` its "
            f"own task record carries — see examples/tasks/tracked-release/service.md"
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
    # tests/test_criteria_guard.py covers this, and no recipe journey can: `read_plan`
    # sets `tasks` on every plan it assembles, so a project whose store answer has no task
    # arrives here as an empty list rather than a missing key. The two refusals below this
    # one are reachable from a real project and are driven by
    # tests/plan_tooling/test_check_plan_recipe_e2e.py.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    if "tasks" not in document:
        raise CriteriaError(
            "the plan states no `tasks`, so there is nothing here to launch; a plan is the "
            "qualified onetaskgraph project `just orchestrate` launches, and its tasks are "
            "that project's task records — see examples/projects/tracked-release.md"
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
    """Check every dispatched node of ``plan``; return how many were checked.

    The criteria first and this host's publication policy after, which is the order the
    two cost a plan's author: a criterion its own judge would fail on is a wrong bar
    whatever repository it lands in, and where it lands is what
    :mod:`orchestrator.publication_guard` then asks about — and whether the release it
    waits on could ever arrive there is :mod:`orchestrator.adoption_guard`'s, asked last.
    """
    checked = 0
    for node in dispatched_nodes(plan):
        check(node.task, node.id, resolve_bar(node.persona))
        check_appendix(node.task, node.id)
        checked += 1
    publication_guard.check_plan(plan)
    adoption_guard.check_plan(plan)
    return checked


#: The executable this repository's checks are registered as, which
#: :mod:`orchestrator.plan_check` hands the engine's own plan check. Passed relative to
#: the working directory that command gives the verb, which is this checkout's root.
PLAN_CHECK_SCRIPT = Path("scripts") / "plan-check.sh"

#: How the two paths name themselves in what they print. An operator reading an accepted
#: plan has to know which loader read it, because only one of them is the launch's own
#: and the other leaves a structural refusal for the launch to make.
THROUGH_ENGINE = (
    f"read through `{ENGINE} plan check`, with this repository's checks registered as "
    f"{PLAN_CHECK_SCRIPT.as_posix()}"
)
DIRECTLY = (
    f"read with this repository's checks alone: the `{ENGINE}` this command resolved "
    f"carries no `plan check`, so the engine's own loader did not read this plan and a "
    f"launch may still refuse its structure"
)


class Counted(NamedTuple):
    """How many dispatched nodes an accepted plan states, or why that is unknown."""

    #: The count, or ``None`` when the plan could not be re-read to take one.
    nodes: int | None
    #: What stopped that read, for the line that has to say the count is missing.
    reason: str | None = None


def accepted(counted: Counted, path: str) -> str:
    """What an accepted plan reports, in one wording both paths print.

    The count is what stops a plan whose nodes were all skipped for the wrong reason
    reading like a clean one — so where it is unknown the line says so, and why, rather
    than printing a zero that means something else entirely.
    """
    read = (
        f"{counted.nodes} dispatched node(s) state the bar they are judged against"
        if counted.nodes is not None
        else f"every dispatched node states the bar it is judged against, though how "
        f"many there are is unknown here: {counted.reason}"
    )
    return (
        f"check-plan: {read}, and every task carries a review record for its current "
        f"authored content ({path})"
    )


def check_directly(project: str) -> int:
    """Check ``project`` with this repository's checks alone, against an engine with no
    `plan check`.

    The path this command took before the engine had a verb to register a check with,
    kept rather than deleted because it is the answer for a host whose engine predates
    that verb — and because it is the same checks over the same plan, so the two paths
    agree by construction rather than by being kept in step. What it cannot do is make
    the loader's own refusals: those are the engine's, and a plan checked this way is
    still refused by the launch for a structural error this never looks at.
    """
    try:
        plan, records = plan_store.read_project(project)
    except (OSError, ValueError) as exc:
        print(
            f"check-plan: cannot read project {project}: {exc}; pass the qualified "
            "project id you are about to hand `just orchestrate`",
            file=sys.stderr,
        )
        return 2
    try:
        checked = check_plan(plan)
    except (
        CriteriaError,
        publication_guard.PublicationError,
        adoption_guard.AdoptionError,
    ) as exc:
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
    try:
        unreviewed = plan_review.unreviewed(records)
    # tests/test_criteria_guard.py covers this: the review bar is composed from this
    # checkout's own tracked files, so a recipe journey run from here cannot remove one.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    except OSError as exc:
        print(
            f"check-plan: cannot fingerprint the review bar this plan would be reviewed "
            f"against: {exc}; run `just bootstrap` from the repository root and retry",
            file=sys.stderr,
        )
        return 2
    if unreviewed:
        named = ", ".join(task.node_id for task in unreviewed)
        # The qualification is unconditional rather than a branch on this plan's own
        # store, which would be a second answer to `plan_review.unwritable`'s question
        # and a diagnostic no journey could reach without writing to the live board. A
        # record is an entry of a task's own Markdown document, so a plan held anywhere
        # else cannot carry one — and saying so here is what stops an operator reading
        # `review-plan` as a step that will clear a board plan. Running it says which
        # store it is, immediately and without spending a turn.
        print(
            f"check-plan: {len(unreviewed)} task(s) carry no review record for their current "
            f"authored content: {named}. Nothing has reviewed those criteria, which is how "
            f"a plan written under time pressure reaches a dispatch. Review them with "
            f"`just review-plan {project}`, which records a pass only for a plan held "
            f"in a local Markdown store.",
            file=sys.stderr,
        )
        return 1
    print(accepted(Counted(checked), DIRECTLY))
    return 0
