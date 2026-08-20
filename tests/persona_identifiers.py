"""What a persona names *of* the repository it reviews, and whether that repository has it.

`tests/persona_recipes.py` reconciles the commands a persona demands. This reconciles the
other half of the same failure, and the two are deliberately separate checks because they
are different failures: a recipe that disappears is a command nobody can run, while an
identifier that changes meaning is a command that still runs and now does the opposite.

That distinction is measured rather than imagined. `personas/crozier/crozier-corpus.yaml`
told a supervisor to hold a worker to a `Corpus { .., matched: &[] }` registration that
starts EMPTY and grows as files begin to match. crozier's `tests/e2e.rs` declares
`unmatched` — the residual *exclusion* list, empty by default so that every expected file
is gated, shrinking as gaps close — and `just fixtures-candidates`, the command that bar
named, survives only as a backward-compatible alias for `just fixtures-gaps`. So the
recipe reconciliation passed on every name that file used, while the model those names
described had inverted: a supervisor applying it fails a correct registration, and a
worker complying with it inverts the one array the gate reads.

Two classes of name are held here, chosen because each can be resolved against the
repository itself rather than against a reader's memory:

- **Paths.** A backticked name containing `/` is a path into the reviewed repository, and
  `git ls-files` says whether that repository has it. Globs (`src/*.rs`) and directory
  names nested anywhere in the tree (`expected/`) both resolve.
- **Structured-literal fields.** A backticked `Type { field: … }` literal names fields of
  a declaration that repository owns, so the declaration is parsed and asked. Presence of
  the bare word would prove nothing — `matched:` occurs in crozier's own prose ("Fully
  matched: all 182 files") — which is why this resolves through the declaration, and why a
  bare backticked word is deliberately *not* a class here.

Brace-delimited declarations are what this parses, which is every declaration the tracked
catalog names today. A type it cannot find that way is reported rather than passed over,
so extending the parser is a failure a reader is told about instead of a gap they have to
notice.
"""

from __future__ import annotations

import re
import subprocess
from fnmatch import fnmatch
from functools import cache
from pathlib import Path
from typing import NamedTuple

from orchestrator.root import REPO_ROOT

#: A backticked span, which is how every name in this catalog's prose is written.
BACKTICKED = re.compile(r"`(?P<span>[^`\n]+)`")

#: A path into the reviewed repository: a backticked span made only of path characters
#: with at least one `/` in it. The slash is what separates a path from an ordinary
#: backticked word — a bare `.json`, an `x-fern-enum`, a `justfile` — none of which can be
#: resolved against a repository without guessing at where to look for them.
PATH_SPAN = re.compile(r"^[A-Za-z0-9_.*/-]*/[A-Za-z0-9_.*/-]*$")

#: A structured literal in prose: `Type { field: value, .. }`. The body is brace-free, so
#: a nested literal is left alone rather than mis-parsed.
LITERAL = re.compile(r"(?P<type>[A-Z][A-Za-z0-9_]*)\s*\{(?P<body>[^{}]*)\}")

#: One fragment of a block body, which both a literal and a declaration separate the same
#: two ways — by comma and by line.
FIELD_SEPARATOR = re.compile(r"[,\n]")

#: A field named at the start of such a fragment. Anchored at the fragment's start rather
#: than searched for, so a colon inside a doc comment or a value cannot be read as one.
FIELD = re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?P<field>[a-z_][A-Za-z0-9_]*)[ \t]*:")

#: How a declaration of a type opens, in the brace-delimited languages this parses.
DECLARATION = r"\b(?:struct|class|interface|enum|type)\s+{type}\b[^\n{{]*\{{"

#: The same opening, in the POSIX syntax `git grep` takes, used only to narrow which files
#: are read. `\b` and `\s` are GNU extensions this deliberately avoids so the search means
#: the same thing wherever the suite runs.
DECLARATION_SEARCH = r"(struct|class|interface|enum|type)[[:space:]]+{type}([^A-Za-z0-9_]|$)"

#: Every YAML comment line, which states how a persona is wired into *this* repository —
#: the base config it deltas over, a lint directive — rather than anything about the
#: repository it reviews. `tests/persona_recipes.py` deliberately reads them, because a
#: dead recipe named in a comment is still a dead recipe; a path in one is this
#: repository's own and would resolve nowhere in the reviewed one.
YAML_COMMENT = re.compile(r"^[ \t]*#.*$", re.MULTILINE)


class Literal(NamedTuple):
    """One `Type { field: … }` a persona writes, and the fields it names."""

    type_name: str
    fields: frozenset[str]


class NamedIdentifiers(NamedTuple):
    """What one persona names of the repository it reviews."""

    #: How a failure names the demand's source: repository-relative for a tracked file.
    named: str
    #: The repository it is for, which is the subdirectory it lives in.
    repository: str
    #: Every path into that repository its prose names.
    paths: frozenset[str]
    #: Every structured literal its prose writes, in the order written.
    literals: tuple[Literal, ...]

    def __bool__(self) -> bool:
        """Whether this persona names anything for the reconciliation to resolve."""
        return bool(self.paths or self.literals)


class Declaration(NamedTuple):
    """One declaration of a type in the reviewed repository, and the fields it declares."""

    #: Repository-relative `path:line`, so a report says where the answer came from.
    where: str
    fields: frozenset[str]


def fields_in(body: str) -> frozenset[str]:
    """Every field `body` names at its own top level.

    Nested braces are blanked before the fields are read, so a field of an inner block
    cannot be mistaken for one of this block's. Newlines survive that blanking, because
    a line break is one of the two ways a body separates its fields.
    """
    flattened: list[str] = []
    depth = 0
    for character in body:
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        elif depth == 0 or character == "\n":
            flattened.append(character)
        else:
            flattened.append(" ")
    fragments = FIELD_SEPARATOR.split("".join(flattened))
    return frozenset(
        match["field"] for match in (FIELD.match(fragment) for fragment in fragments) if match
    )


def named_identifiers(prose: str) -> tuple[frozenset[str], tuple[Literal, ...]]:
    """Every path and structured literal `prose` names of the repository it is about."""
    spans = [match["span"] for match in BACKTICKED.finditer(prose)]
    paths = frozenset(span for span in spans if PATH_SPAN.match(span))
    literals = tuple(
        Literal(type_name=match["type"], fields=fields_in(match["body"]))
        for span in spans
        for match in LITERAL.finditer(span)
    )
    return paths, tuple(literal for literal in literals if literal.fields)


def identifiers_of(named: str, repository: str, prose: str) -> NamedIdentifiers:
    """Read one persona's prose into what the reconciliation needs of it."""
    paths, literals = named_identifiers(prose)
    return NamedIdentifiers(named=named, repository=repository, paths=paths, literals=literals)


def identifiers_at(path: Path) -> NamedIdentifiers:
    """Read one persona file, ignoring the YAML comments that are about this repository."""
    try:
        named = str(path.relative_to(REPO_ROOT))
    except ValueError:
        named = str(path)  # a persona a test built, which lives outside this checkout
    return identifiers_of(
        named=named,
        repository=path.parent.name,
        prose=YAML_COMMENT.sub("", path.read_text(encoding="utf-8")),
    )


@cache
def tracked_names(checkout: Path) -> frozenset[str]:
    """Every path `checkout` tracks, plus every directory prefix of one.

    Git's own index is what a repository *has*: a build artifact or a stray local file is
    not something a persona may name, and an ignored one is not something a reader of that
    repository would find. Directory prefixes are included because a persona names
    directories (`expected/`) as readily as files.
    """
    listed = subprocess.run(
        ["git", "-C", str(checkout), "ls-files", "-z"],
        text=True,
        capture_output=True,
    )
    if listed.returncode != 0:
        return frozenset()
    found: set[str] = set()
    for entry in listed.stdout.split("\0"):
        if not entry:
            continue
        found.add(entry)
        directory = Path(entry).parent
        while str(directory) != ".":
            found.add(str(directory))
            directory = directory.parent
    return frozenset(found)


def resolves(named: str, tracked: frozenset[str]) -> bool:
    """Whether `named` reaches something the repository tracks.

    A path is matched three ways because a persona writes it all three: exactly, as a glob
    over the tree (`src/*.rs`), and as a trailing fragment of a deeper path — which is how
    a per-fixture directory like `expected/` is named without repeating every fixture it
    sits under.
    """
    target = named.strip("/")
    if not target:
        return False
    if target in tracked:
        return True
    if "*" in target and any(fnmatch(entry, target) for entry in tracked):
        return True
    suffix = f"/{target}"
    return any(entry.endswith(suffix) for entry in tracked)


def _braced_body(source: str, opening: int) -> str | None:
    """The text between the brace at `opening` and its match, or `None` if unbalanced."""
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    return None


def declarations_of(checkout: Path, type_name: str) -> tuple[Declaration, ...]:
    """Every brace-delimited declaration of `type_name` the repository tracks.

    `git grep` narrows which tracked files are worth reading — a corpus repository holds
    tens of thousands of them — and the parse below is what actually decides, since the
    search is a plain-text one and cannot tell a declaration from a mention of it.
    """
    pattern = DECLARATION_SEARCH.format(type=re.escape(type_name))
    found = subprocess.run(
        ["git", "-C", str(checkout), "grep", "-lIE", pattern],
        text=True,
        capture_output=True,
    )
    if found.returncode != 0:
        return ()
    opening = re.compile(DECLARATION.format(type=re.escape(type_name)))
    declarations: list[Declaration] = []
    for relative in sorted(filter(None, found.stdout.splitlines())):
        source = (checkout / relative).read_text(encoding="utf-8", errors="replace")
        for match in opening.finditer(source):
            body = _braced_body(source, match.end() - 1)
            if body is None:
                continue
            line = source.count("\n", 0, match.start()) + 1
            declarations.append(Declaration(where=f"{relative}:{line}", fields=fields_in(body)))
    return tuple(declarations)


def undefined_identifiers(identifiers: NamedIdentifiers, checkout: Path) -> str | None:
    """What `identifiers` names that `checkout` does not have, reported, or `None`.

    Every report names the three things a reader needs to act without re-deriving the
    investigation: what made the demand, which name it used, and where that name was
    looked for — the checkout, and for a field the declaration site that answered.
    """
    tracked = tracked_names(checkout)
    if not tracked:
        return (
            f"`git ls-files` listed nothing in {checkout}, so {identifiers.named} could "
            "not be reconciled against it at all"
        )
    findings = [
        f"{identifiers.named} names the path `{named}`, which the registered checkout "
        f"{checkout} does not track"
        for named in sorted(identifiers.paths)
        if not resolves(named, tracked)
    ]
    for literal in identifiers.literals:
        findings.extend(_undeclared_fields(identifiers.named, literal, checkout))
    if not findings:
        return None
    return "\n".join(
        [
            *findings,
            "a supervisor holding a worker to a name that repository does not have fails "
            "finished work against prose only this host wrote, so correct the persona "
            "against the repository it reviews",
        ]
    )


def _undeclared_fields(named: str, literal: Literal, checkout: Path) -> list[str]:
    """What `literal` names that no declaration of its type in `checkout` declares."""
    declarations = declarations_of(checkout, literal.type_name)
    if not declarations:
        return [
            f"{named} writes a `{literal.type_name} {{ … }}` literal, and the registered "
            f"checkout {checkout} declares no brace-delimited `{literal.type_name}` at all"
        ]
    declared: frozenset[str] = frozenset().union(*(found.fields for found in declarations))
    sites = ", ".join(found.where for found in declarations)
    return [
        f"{named} writes `{literal.type_name} {{ {field}: … }}`, which the "
        f"`{literal.type_name}` declared at {sites} in the registered checkout {checkout} "
        f"does not declare — it declares: {', '.join(sorted(declared))}"
        for field in sorted(literal.fields - declared)
    ]
