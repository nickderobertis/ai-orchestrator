"""No command this repository's prose teaches names a flag the pinned CLI lacks.

This repository is a configuration layer over CLIs it does not build, so its prose is
an operating manual for somebody else's interface. Every command line it writes is a
copy, and a copy drifts silently: `docs/repo-lifecycle.md` came to document
`--execution-checkout` on `recover` (it is on `session open`), `--title` and
`--policy` on `recover` (they are on `publish` and `publish-branch`), and a refusal
message telling an operator to run `just integrate <branch> --repo <checkout>` —
naming a flag `integrate` has never had. The cost is not a typo. The whole point of
routing every VCS operation through `onevcs` is that an agent following this manual
lands on a working path; an agent that types a documented flag and is refused
improvises with raw `git`, which is the failure the manual exists to prevent.

So the surface is resolved from the pinned binary's own `--help`, recursively, by
`tests/published_surface.py`. There is no table of verbs here to keep current: bump
`config/onevcs.version` and this gate re-derives what the tool has and re-reads the
prose against it.

What counts as an invocation is deliberately conservative, because prose quotes
command-shaped text that is not a command — `onepipeline run \\`<id>\\`` in
`docs/orchestration.md` names a run, it does not invoke one. A code span is judged
only when it is unambiguously an invocation: at least one real subcommand resolved,
or a flag present, or an explicit `uv run` prefix. A bare tool name followed by a
word that is not a verb is left alone rather than guessed at. The gap that leaves —
a retired verb named with no flags and no prefix — is closed from the other side by
`test_no_round_era_command_survives_in_the_prose`, which names the retired vocabulary
outright.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest
from published_surface import LONG_FLAG, surface_of

from orchestrator.root import REPO_ROOT

#: The published CLIs whose surface this gate resolves. Two rather than four because
#: these are the two whose verbs and flags this repository's prose actually teaches an
#: operator to type; adding a third is adding its name here.
GATED_TOOLS = ("onevcs", "onepipeline")

#: Directories holding no prose of this repository's own. `.plans` and `runs` are both
#: gitignored local stores — the plan-authoring root and the run journals — and what they
#: hold is routinely prose *about another repository*, naming that repository's recipes,
#: which this repository's justfile has no reason to define. A task record under
#: `runs/*/writeback/` is a copy of a plan for whatever repository the run targeted.
#: Reading either here failed every push made while such a record existed, and what it
#: reported was correct prose held against the wrong tree. Neither is tracked, so neither
#: is prose this repository ships.
NOT_OURS = ("node_modules", ".venv", ".nx", "dist", "scratch", ".git", ".plans", "runs")

#: A fenced code block, whose every line is a candidate invocation. The leading
#: whitespace is load-bearing: `docs/host-setup.md` fences blocks inside list items,
#: and a pattern anchored at column zero would leave those fences in the prose — where
#: their backticks unbalance every inline span after them.
FENCE = re.compile(r"^[ \t]*```.*?^[ \t]*```", re.MULTILINE | re.DOTALL)

#: An inline code span, in any backtick width. Prose here is hard-wrapped, so a span
#: may cross a line break and `DOTALL` is what keeps such a span whole — but never a
#: blank line: a paragraph with an odd number of backticks would otherwise pair one
#: off against the next paragraph's and swallow the prose between them.
INLINE = re.compile(r"(?P<ticks>`+)(?P<code>(?:(?!\n[ \t]*\n).)+?)(?P=ticks)", re.DOTALL)

#: A `just` recipe header. Parameters may carry defaults (`gate remote="" base="":`),
#: so the whole parameter list is skipped rather than one optional word.
RECIPE = re.compile(r"^(?P<name>[a-z][a-z0-9-]*)(?:\s+[^\s:]+)*\s*:\s*$")

#: A token shaped like a subcommand. Placeholders (`<run-id>`, `RUN`, `[FILE]`) and
#: paths (`plan.json`) deliberately do not match, so they end path resolution without
#: being reported as verbs the tool lacks.
SUBCOMMAND_SHAPED = re.compile(r"^[a-z][a-z0-9-]*$")

#: Punctuation prose wraps a token in. Angle and square brackets are NOT stripped:
#: they are what marks `<run-id>` a placeholder rather than a subcommand.
TRIM = "`'\",;:."

#: Every retired round-era name, and what replaced it. The engine drives its DAG
#: continuously to settlement, so none of these exists any more — and prose that still
#: teaches one sends a planner to a verb that is gone.
ROUND_ERA = {
    "run-plan": "`just orchestrate` drives the DAG to settlement on its own",
    "next-round": "there is no verb that advances a run",
    "--round-budget": "`onepipeline start` has no round budget; a run settles",
}


class Invocation(NamedTuple):
    """What a span was judged to be invoking, and the flags that invocation takes."""

    #: How the invocation is written, for the failure message.
    written: str
    #: Every long flag it accepts, from the published surface plus whatever the `just`
    #: wrapper absorbs on the way.
    accepts: frozenset[str]


class Recipe(NamedTuple):
    """One `just` recipe, and what it delegates to.

    A recipe is the operating surface an operator types, and it is deliberately not
    the published command line: `just repos --audit-gate-coverage` renders
    `onevcs repos --audit-gates`. So the flags a recipe accepts are the published
    verb's *plus* whatever its own body names, which is where an absorbed spelling
    lives — read from the body rather than listed here.
    """

    #: The published command paths it reaches, as `(tool, path)`.
    delegates: tuple[tuple[str, tuple[str, ...]], ...]
    #: Long flags the recipe body or a script it runs names itself.
    absorbs: frozenset[str]


class Drift(NamedTuple):
    """One documented thing the pinned CLI does not have."""

    where: str
    written: str
    named: str
    reason: str

    def __str__(self) -> str:
        return f"{self.where}: `{self.written}` names {self.named}, which {self.reason}"


def _prose_files() -> list[Path]:
    """Every markdown document this repository ships, plus the justfile.

    Resolved and de-duplicated because `CLAUDE.md` is a symlink to `AGENTS.md`, and
    reporting the same drift under two names helps nobody.
    """
    found = {
        document.resolve()
        for document in REPO_ROOT.rglob("*.md")
        if not any(part in NOT_OURS for part in document.relative_to(REPO_ROOT).parts)
    }
    return sorted(found) + [REPO_ROOT / "justfile"]


def _spans(document: Path) -> list[str]:
    """Every code span in one document, as one whitespace-normalized line each.

    For the justfile that means the spans inside its *comments*: a recipe body is a
    real invocation held to its published command line by
    `tests/e2e/test_delegated_recipes_e2e.py`, and the comments are the prose an
    operator reads.
    """
    text = document.read_text(encoding="utf-8")
    if document.name == "justfile":
        text = "\n".join(
            line.lstrip().removeprefix("#")
            for line in text.splitlines()
            if line.lstrip()[:1] == "#"
        )
        return [" ".join(match.group("code").split()) for match in INLINE.finditer(text)]
    found = []
    for block in FENCE.finditer(text):
        found.extend(line.strip() for line in block.group().splitlines()[1:-1] if line.strip())
    for match in INLINE.finditer(FENCE.sub("", text)):
        found.append(" ".join(match.group("code").split()))
    return found


def _recipes() -> dict[str, Recipe]:
    """Every `just` recipe, and the published verbs its body reaches.

    Derived from the justfile and the scripts it runs rather than declared here: the
    mapping from recipe to published verb is exactly the thing that moves when a
    wrapper is repointed, so a copy of it would be one more thing to go stale.
    """
    lines = (REPO_ROOT / "justfile").read_text(encoding="utf-8").splitlines()
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        header = RECIPE.match(line)
        if header is not None:
            current = header.group("name")
            bodies[current] = []
            continue
        if not line.strip():
            current = None
            continue
        if current is not None and line[:1] in " \t":
            bodies[current].append(line)
    return {name: _delegation(body) for name, body in bodies.items()}


def _delegation(body: list[str]) -> Recipe:
    """What one recipe body delegates to, and the flags it names itself."""
    text = " ".join(body)
    # A recipe reaches a published CLI directly (`uv run onevcs recover`) or through
    # the wrapper named after that CLI (`./scripts/onepipeline.sh next`), which
    # forwards its arguments untouched. Both spellings resolve to the same tool.
    delegates: list[tuple[str, tuple[str, ...]]] = []
    absorbs = set(LONG_FLAG.findall(text))
    for tool in GATED_TOOLS:
        surface = surface_of(tool)
        for reached in re.finditer(rf"(?:uv run |scripts/){re.escape(tool)}(?:\.sh)?\b", text):
            path: tuple[str, ...] = ()
            for token in text[reached.end() :].split():
                word = token.strip(TRIM)
                if word not in surface.subcommands(path):
                    break
                path = (*path, word)
            delegates.append((tool, path))
    for script in re.findall(r"\./scripts/[\w.-]+\.(?:sh|py)", text):
        named = REPO_ROOT / script.removeprefix("./")
        if named.is_file():
            absorbs.update(LONG_FLAG.findall(named.read_text(encoding="utf-8")))
    return Recipe(tuple(delegates), frozenset(absorbs))


def _accepted_by(delegates: tuple[tuple[str, tuple[str, ...]], ...]) -> frozenset[str]:
    """Every long flag the delegated published verbs accept between them."""
    accepted: set[str] = set()
    for tool, path in delegates:
        surface = surface_of(tool)
        accepted |= set(surface.flags.get(path, frozenset())) | set(surface.flags[()])
    return frozenset(accepted)


def _drift_in(span: str, where: str, recipes: dict[str, Recipe]) -> list[Drift]:
    """Every command or flag one span names that the pinned CLIs do not have."""
    tokens = [token.strip(TRIM) for token in span.split()]
    carries_flag = any(token.startswith("--") for token in tokens)
    found: list[Drift] = []
    invoked: Invocation | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "just" and index + 1 < len(tokens):
            named = tokens[index + 1]
            if SUBCOMMAND_SHAPED.match(named):
                recipe = recipes.get(named)
                if recipe is None:
                    found.append(
                        Drift(where, span, f"the recipe `{named}`", "the justfile does not define")
                    )
                    invoked = None
                elif recipe.delegates:
                    invoked = Invocation(
                        f"just {named}", _accepted_by(recipe.delegates) | recipe.absorbs
                    )
                else:
                    # A recipe reaching neither pinned CLI — `just gate`, `just history`.
                    # Its flags are somebody else's surface, so they are not judged here.
                    invoked = None
                index += 2
                continue
        if token in GATED_TOOLS:
            surface = surface_of(token)
            path: tuple[str, ...] = ()
            ahead = index + 1
            while ahead < len(tokens) and tokens[ahead] in surface.subcommands(path):
                path = (*path, tokens[ahead])
                ahead += 1
            prefixed = tokens[max(0, index - 2) : index] == ["uv", "run"]
            if path or carries_flag or prefixed:
                written = " ".join((token, *path))
                invoked = Invocation(
                    written,
                    frozenset(surface.flags.get(path, frozenset())) | surface.flags[()],
                )
                if (
                    ahead < len(tokens)
                    and SUBCOMMAND_SHAPED.match(tokens[ahead])
                    and surface.subcommands(path)
                ):
                    found.append(
                        Drift(
                            where,
                            span,
                            f"the subcommand `{tokens[ahead]}`",
                            f"`{written}` does not have",
                        )
                    )
            else:
                # A bare tool name in prose, not an invocation. See the module docstring.
                invoked = None
            index = ahead
            continue
        if token.startswith("--") and invoked is not None:
            flag = token.split("=", 1)[0]
            if flag not in invoked.accepts:
                found.append(
                    Drift(where, span, f"the flag `{flag}`", f"`{invoked.written}` does not accept")
                )
        index += 1
    return found


@pytest.mark.reads_docs
def test_no_documented_command_names_a_flag_or_verb_the_pinned_cli_lacks() -> None:
    """Every onevcs and onepipeline command this repository teaches actually exists.

    One test over every document rather than one per file: the prose is read here
    rather than at collection, and a parametrization computed from `docs/` would be
    built in a tier that does not hash it. The whole list is reported at once, because
    an operator correcting drift wants every site, not the first.
    """
    recipes = _recipes()
    drifted: list[Drift] = []
    for document in _prose_files():
        where = str(document.relative_to(REPO_ROOT))
        for span in _spans(document):
            drifted.extend(_drift_in(span, where, recipes))

    assert not drifted, "documented commands the pinned CLIs do not have:\n" + "\n".join(
        f"  - {drift}" for drift in drifted
    )


@pytest.mark.reads_docs
def test_no_round_era_command_survives_in_the_prose() -> None:
    """The retired round vocabulary is gone by name, not merely unreachable.

    The engine reconciles continuously now: there is no verb that advances a run and
    no budget of rounds to spend. The gate above cannot see a retired verb written
    with no flags and no runner prefix — it declines to guess whether such a span is
    an invocation at all — so the names themselves are refused here.
    """
    named: list[str] = []
    for document in _prose_files():
        text = document.read_text(encoding="utf-8")
        where = str(document.relative_to(REPO_ROOT))
        for retired, instead in ROUND_ERA.items():
            if retired in text:
                named.append(f"  - {where} names `{retired}`; {instead}")

    assert not named, "retired round-era vocabulary survives in the prose:\n" + "\n".join(named)
