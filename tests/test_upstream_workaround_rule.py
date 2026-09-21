"""The canonical-example rule of `AGENTS.md`, held to what a test can hold of it.

The rule is stated once, under the heading `SECTION` names, and is not restated here.
This module is its drift gate, on the shelf `tests/test_decomposition_guidance.py` and
`tests/test_tracked_path_references.py` sit on: each reconciles a claim this
repository's prose makes against the thing that would say it had gone stale.

What each check catches:

* **Stated once.** A second copy of the rule, which is how two statements are edited
  apart until a reader cannot tell which one governs. The heading appears once in
  `AGENTS.md`, `ANCHOR` is in that section and in no other tracked prose, and the
  pointer `POINTING_SECTION` opens with may name the section and may not restate it.
* **A self-described workaround names the upstream issue that retires it.** A fill
  nobody surfaced: a header under `scripts/` that calls itself a workaround, in the
  vocabulary `WORKAROUND_VOCABULARY` lists, and names no issue or pull request under
  `https://github.com/nickderobertis/`. Written to miss rather than over-refuse — a
  header naming the URL passes whatever else it says.
* **Retiring and retired.** A fill deleted without its entry moving, and a retired fill
  that regrew. `RETIRING` is required to exist while listed and is exempt from the
  vocabulary check; `RETIRED` maps each deleted fill to what replaced it and is required
  not to exist. The two names and shapes are the contract every later node of the plan
  this gate landed in restates.

**Why the vocabulary is a list rather than a judgment.** Whether a script *is* a
workaround is a reading of what the library offers and what the script does, which a
matcher cannot make and a judged tier can only make nondeterministically. What a matcher
can hold is the script's own account of itself: a header that says *workaround*,
*stop-gap* or *until onevcs ships* has already made the judgment, and all this check
asks is that it finish the sentence with where the fix is tracked. A phrase missing from
the list is a header this gate does not read, never a header it has cleared.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The document that states the rule, and the section heading it states it under.
DOCUMENT = "AGENTS.md"
SECTION = "### A canonical example of the libraries, not a workaround layer"
#: The section's GitHub anchor, which is how the one permitted pointer names it.
SECTION_ANCHOR = "#a-canonical-example-of-the-libraries-not-a-workaround-layer"
#: The section that opens with the one pointer to the rule.
POINTING_SECTION = "## Command surface"
#: The rule's anchor sentence, verbatim. Held to appearing once in this repository's
#: prose, so a second statement of the rule fails here by the words it would have to use.
ANCHOR = "Code that works around an upstream gap is an anti-pattern here"

#: Every tracked prose file other than `AGENTS.md` the anchor sentence may not appear in.
#: `CLAUDE.md` is the symlink to `AGENTS.md` and is not listed for the reason
#: `tests/test_tracked_path_references.py` skips it.
OTHER_PROSE = (
    "README.md",
    "docs/**/*.md",
    "personas/**/*.yaml",
    "config/dispatch-appendix.md",
)

#: The section of `AGENTS.md` that lists every tool this harness configures, one bold
#: backticked name per bullet, which `TOOLS` is held to below.
ROSTER_SECTION = "## The tools this harness configures"
ROSTER_ENTRY = re.compile(r"(?m)^- \*\*`([^`]+)`\*\*")
#: The tools this harness configures, as a header would name them. `onepipeline-ui` is
#: spelled before `onepipeline` so the alternation takes the longer name whole.
TOOLS = (
    "onepipeline-ui",
    "onepipeline",
    "onevcs",
    "oneagentgraph",
    "onejudge",
    "oneharness",
    "onetaskgraph",
    "onemessagebus",
    "llmlint",
)
_TOOL = "(?:" + "|".join(re.escape(tool) for tool in TOOLS) + ")"
#: The words a header uses when it calls itself a workaround. One pattern, so the whole
#: list is read in one place and a phrase is added by extending it.
WORKAROUND_VOCABULARY = re.compile(
    "|".join(
        (
            r"\bstop-?gap\b",
            r"\bworkaround\b",
            r"\bwork(?:s|ing|ed)?\s+around\b",
            r"\bpaper(?:s|ing|ed)?\s+over\b",
            rf"\buntil\s+(?:the\s+)?{_TOOL}\b",
            rf"\bbecause\s+(?:the\s+)?{_TOOL}\s+does\s+not\s+(?:yet\s+)?"
            r"(?:offer|accept|answer|ship|emit|carry|take)\b",
        )
    ),
    re.IGNORECASE,
)
#: An issue or pull request of one of this host's own repositories, which is where a
#: surfaced gap is tracked.
UPSTREAM_REFERENCE = re.compile(
    r"https://github\.com/nickderobertis/[A-Za-z0-9_.-]+/(?:issues|pull)/\d+"
)

#: The fills the plan this gate landed in deletes in its later nodes, each exempt from
#: the vocabulary check while it exists and required to exist while listed. A later node
#: moves what it deletes from here to `RETIRED`.
RETIRING: tuple[str, ...] = (
    "scripts/sweep.sh",
    "scripts/watch-run.sh",
    "scripts/watch-render.py",
    "scripts/stop-unwatched-guard.py",
    "scripts/stop-unwatched-guard.sh",
    "scripts/oneharness-agent.sh",
    "scripts/oneharness-orchestrator.sh",
    "scripts/oneharness-stream.py",
    "scripts/draft-pr-body.sh",
    "scripts/land-branch.sh",
    "scripts/supervision-readings.py",
    "scripts/hold-run-lease.sh",
    "scripts/claude-workspace-trust.sh",
    "scripts/dag-ui-server.js",
    "scripts/dag-ui-screens.sh",
    ".githooks/post-checkout",
    "docs/run-root-reclamation.md",
)
#: Each fill already deleted, mapped to the library verb or configuration that replaced
#: it, and required not to exist.
RETIRED: dict[str, str] = {}


def _text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a hard-wrapped sentence may be quoted as one line."""
    return " ".join(prose.split())


def _section(document: str, heading: str) -> str:
    """The prose under ``heading``, up to the next heading of its level or above."""
    prose = _text(document)
    assert prose.count(f"\n{heading}\n") == 1, f"{document} does not state {heading!r} exactly once"
    body = prose.split(f"\n{heading}\n", 1)[1]
    level = len(heading) - len(heading.lstrip("#"))
    cut = re.search(rf"(?m)^#{{1,{level}}}\s", body)
    return body if cut is None else body[: cut.start()]


def _listed_prose() -> tuple[str, ...]:
    """Every file the `OTHER_PROSE` patterns name in this checkout, in path order."""
    found = {
        str(path.relative_to(REPO_ROOT))
        for pattern in OTHER_PROSE
        for path in REPO_ROOT.glob(pattern)
        if path.is_file() and not path.is_symlink()
    }
    return tuple(sorted(found))


#: How a Python module's docstring opens, when it is the first thing after the comments.
DOCSTRING_OPENS = re.compile(r'^\s*[rRuU]?("""|\'\'\')')


def header_of(path: Path) -> str:
    """The leading comment block of ``path``: everything before its first code line.

    A shebang is skipped; a blank line is part of the header, so a block broken by one
    still reads whole; a Python module docstring opening the file is part of the header.
    A directive line (`# shellcheck …`, `# llmlint: …`) is a comment like any other,
    which is a miss this gate accepts rather than a second grammar it parses.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    comment = re.compile(r"^\s*(//|/\*|\*)" if path.suffix == ".js" else r"^\s*#")
    header: list[str] = []
    for number, line in enumerate(lines):
        if not line.strip() or comment.match(line):
            header.append(line)
            continue
        opened = DOCSTRING_OPENS.match(line) if path.suffix == ".py" else None
        if opened is not None:
            quote = opened.group(1)
            rest = "\n".join(lines[number:])
            closed = rest.find(quote, opened.end())
            header.append(rest if closed < 0 else rest[: closed + len(quote)])
        break
    return "\n".join(header)


def workaround_phrase(path: Path) -> str | None:
    """The first vocabulary phrase ``path``'s header uses of itself, or ``None``.

    ``None`` for a header that says nothing of the kind, and for one that names an
    upstream reference whatever else it says — the miss-rather-than-over-refuse rule.
    """
    header = header_of(path)
    if UPSTREAM_REFERENCE.search(header):
        return None
    matched = WORKAROUND_VOCABULARY.search(_flat(header))
    return None if matched is None else matched.group(0)


def scripts() -> tuple[str, ...]:
    """Every file under `scripts/`, at any depth, less the fills a later node retires.

    Recursive rather than one level deep, so a helper somebody files in a subdirectory is
    read by this gate on the day it is added rather than on the day somebody notices.
    """
    return tuple(
        sorted(
            relative
            for path in (REPO_ROOT / "scripts").rglob("*")
            if path.is_file()
            for relative in (str(path.relative_to(REPO_ROOT)),)
            if relative not in RETIRING
        )
    )


def test_the_rule_is_stated_once_under_its_own_heading() -> None:
    section = _flat(_section(DOCUMENT, SECTION))
    assert ANCHOR in section, (
        f"{DOCUMENT}'s {SECTION!r} no longer states {ANCHOR!r}; the gate holds that sentence "
        "as the rule's anchor, so restate it there or move the anchor with it"
    )
    assert _flat(_text(DOCUMENT)).count(ANCHOR) == 1, (
        f"{DOCUMENT} states {ANCHOR!r} more than once; the rule is stated under {SECTION!r} "
        "and pointed at from anywhere else"
    )


def test_the_section_sits_under_what_this_repo_is() -> None:
    """The rule is part of what the repository *is*, so it is stated there, not in a corner."""
    whole = "## What this repo is"
    assert f"\n{SECTION}\n" in _section(DOCUMENT, whole), (
        f"{DOCUMENT} no longer states {SECTION!r} under {whole!r}"
    )


@pytest.mark.parametrize("document", _listed_prose())
def test_no_other_prose_restates_the_anchor_sentence(document: str) -> None:
    assert ANCHOR not in _flat(_text(document)), (
        f"{document} restates {ANCHOR!r}, which {DOCUMENT}'s {SECTION!r} owns; point at the "
        "section instead of copying it, or the two copies drift apart"
    )


def test_the_command_surface_opens_with_a_pointer_and_not_the_sentence() -> None:
    """A maintainer reads `## Command surface` before writing a script, so the pointer is there."""
    pointing = _section(DOCUMENT, POINTING_SECTION)
    first_paragraph = _flat(pointing.strip().split("\n\n", 1)[0])
    assert f"]({SECTION_ANCHOR})" in first_paragraph, (
        f"{DOCUMENT}'s {POINTING_SECTION!r} no longer opens with a link to {SECTION_ANCHOR}"
    )
    assert ANCHOR not in _flat(pointing), (
        f"{DOCUMENT}'s {POINTING_SECTION!r} restates {ANCHOR!r}; the pointer names the "
        "section and the section states the rule"
    )


def test_the_vocabulary_names_every_tool_the_roster_configures() -> None:
    """`TOOLS` is the roster's list, so a tool added there is one a header can name."""
    listed = set(ROSTER_ENTRY.findall(_section(DOCUMENT, ROSTER_SECTION)))
    assert listed == set(TOOLS), (
        f"{DOCUMENT}'s {ROSTER_SECTION!r} lists {sorted(listed)} and TOOLS names "
        f"{sorted(TOOLS)}; a header saying `until <tool> …` of a tool the vocabulary does "
        "not name is one this gate does not read"
    )


def test_every_self_described_workaround_names_the_upstream_issue_that_retires_it() -> None:
    found = [
        (relative, phrase)
        for relative in scripts()
        for phrase in (workaround_phrase(REPO_ROOT / relative),)
        if phrase is not None
    ]
    assert not found, "\n".join(
        f"{path}'s header calls it a workaround ({phrase!r}) and names no upstream issue or "
        "pull request; surface the gap upstream and name its URL in the header, or reword a "
        "header that says it loosely of a thin adapter"
        for path, phrase in found
    )


@pytest.mark.parametrize("path", RETIRING)
def test_a_retiring_fill_exists_while_it_is_listed(path: str) -> None:
    assert (REPO_ROOT / path).exists(), (
        f"{path} is listed in RETIRING and does not exist; the node that deleted it moves "
        "the entry to RETIRED, naming what replaced it"
    )


def test_no_retired_fill_exists() -> None:
    regrown = sorted(path for path in RETIRED if (REPO_ROOT / path).exists())
    assert not regrown, "\n".join(
        f"{path} was retired — replaced by {RETIRED[path]} — and exists again; delete it "
        "rather than letting the fill regrow beside what replaced it"
        for path in regrown
    )


def test_the_two_tables_are_disjoint_and_every_retirement_names_its_replacement() -> None:
    assert not set(RETIRING) & set(RETIRED), "a fill is listed as both retiring and retired"
    unnamed = sorted(path for path, replacement in RETIRED.items() if not replacement.strip())
    assert not unnamed, f"{unnamed} name nothing as what replaced them"


#: A header, and whether the vocabulary reads it as a self-described workaround. The
#: detector is worth nothing unless it reads each phrase the list promises and misses a
#: header that merely names a library — or names an upstream URL, which clears any.
HEADERS = (
    ("# A stop-gap until the verb ships.\n", True),
    ("# A stopgap.\n", True),
    ("# This is a workaround for the missing flag.\n", True),
    ("# Works around a flag the engine lacks.\n", True),
    ("# Papers over a contract gap.\n", True),
    ("# Kept until onevcs answers this itself.\n", True),
    ("# Kept because onepipeline does not yet offer the verb.\n", True),
    ("# Kept because the onemessagebus does not accept a codec here.\n", True),
    ("# The one entry point every onepipeline recipe goes through.\n", False),
    ("# Adapt llmlint's judge to the sandboxed host.\n", False),
    ("# A workaround (https://github.com/nickderobertis/onevcs/issues/12).\n", False),
)


@pytest.mark.parametrize(("header", "is_finding"), HEADERS, ids=[row[0].strip() for row in HEADERS])
def test_the_vocabulary_reads_what_it_promises(
    tmp_path: Path, header: str, is_finding: bool
) -> None:
    script = tmp_path / "probe.sh"
    script.write_text(f"#!/usr/bin/env bash\n{header}set -euo pipefail\n", encoding="utf-8")

    assert (workaround_phrase(script) is not None) is is_finding


def test_the_header_stops_at_the_first_code_line(tmp_path: Path) -> None:
    """A comment after the first code line is the body's, not the header's."""
    script = tmp_path / "later.sh"
    script.write_text(
        "#!/usr/bin/env bash\n# The one entry point.\n\nset -euo pipefail\n# a workaround\n",
        encoding="utf-8",
    )
    assert workaround_phrase(script) is None
    module = tmp_path / "later.py"
    module.write_text('"""A stop-gap.\n\nMore."""\n\nimport os  # workaround\n', encoding="utf-8")
    assert header_of(module) == '"""A stop-gap.\n\nMore."""'
    assert workaround_phrase(module) == "stop-gap"
