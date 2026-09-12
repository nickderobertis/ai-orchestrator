"""Every repository-relative path this repository's own prose and diagnostics name.

A dead link in prose costs a reader an hour; a dead link inside a *refusal* costs
them that hour at the worst moment, because the one message telling them how to fix
their plan sends them to a file nobody can open. Both happened here: six sites went
on naming one example plan file after the move to onetaskgraph plan storage deleted
it, three of them refusals, and nothing in this suite noticed.

So the class is closed rather than the instance: this gate reads the real documents
and the real diagnostic strings, finds the repository-relative paths they name, and
fails on one that does not exist. `tests/test_engine_contracts.py`,
`tests/test_decomposition_guidance.py` and `tests/test_phase_and_landing_guidance.py`
are the shelf it belongs on — each reconciles a claim this repository makes against
the thing that would say it had gone stale.

**What it reads, and why not more.** The documents this repository writes for its own
readers (`README.md`, `AGENTS.md`, and every page under `docs/`) and the Python it
prints diagnostics from (`orchestrator/`). Widening it to `scripts/`, `config/` and
`llmlint.yml` finds more genuinely dangling references than one change can repair,
and exempting a real defect to make a gate green is worse than the gate not reaching
it — so that sweep is a follow-up rather than a registry entry here.

**How a path is recognised, and the five things that are not one.** A token is a
candidate when it is path-shaped, it resolves — from the document's own directory,
so a `docs/` page's `../examples/…` link is read the way a reader follows it — onto a
tracked top-level entry of *this* repository, and it names a file by extension or a
directory by trailing slash. That anchoring is the whole of what keeps the heuristic
safe: `GET /health`, `github.com/nickderobertis/some-service` and
`crates/onevcs/src/recover.rs` are not candidates because no repository-relative path
resolves that way. Then:

* a token carrying `<` or `>` is a **placeholder** — `runs/<run-id>/events.jsonl`,
  `.logs/<label>.log` — and names no one file to check;
* a token carrying `*` is a **glob** and is checked by whether it matches anything,
  which is what `nx.json`'s input globs are quoted as;
* an **extension-less** final segment with no trailing slash is not read as a path at
  all, because the quoted phrase *"local config/scripts/docs at this point"* and the
  quoted failure text `cannot read graphs/crozier/crozier-corpus` both look exactly
  like one;
* a **suffix this workspace tracks no file of** belongs to another repository — every
  `.rs` path in `docs/test-suite-disposition.md` and `docs/repo-lifecycle.md` names an
  `onepipeline` or `onevcs` test, and this polyglot workspace tracks no Rust at all;
* a **gitignored** path is host state rather than tracked content — `scratch/personas/`
  is the drafting directory `AGENTS.md` tells a persona author to create, and it exists
  on a host that has drafted one and in no clean checkout, so its presence or absence
  says nothing about whether the prose naming it is right.

`CLAUDE.md` is skipped because it is a symlink to `AGENTS.md`; reading both would
report every finding twice and name a file no author edits.

**The registry below is the exemptions, and it is held to being one.** An entry whose
path starts existing fails too: an exemption that has quietly become a real reference
is a line nobody would otherwise read again.
"""

from __future__ import annotations

import re
import subprocess
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

#: Path-shaped: segments of word characters, dots, hyphens and glob stars, with an
#: optional trailing slash marking a directory. Deliberately no `<`/`>`, so a
#: placeholder token is cut short here rather than matched and then filtered.
PATH_TOKEN = re.compile(r"[A-Za-z0-9_.*][A-Za-z0-9_.*-]*(?:/[A-Za-z0-9_.*][A-Za-z0-9_.*-]*)+/?")
#: Sentence punctuation a path collects when prose ends on it.
TRAILING_PUNCTUATION = ".,;:"


class Reference(NamedTuple):
    """One repository-relative path, and where it is named."""

    path: str
    site: str


class Exemption(NamedTuple):
    """One path this repository names on purpose while it does not exist."""

    path: str
    reason: str


#: Every path in scope that is absent on purpose. Each names why, because the next
#: reader's question is whether it is this list or the prose that is wrong.
EXEMPTIONS = (
    Exemption(
        ".github/workflows/",
        "deliberately absent: CI is deferred with this proof-of-concept, and AGENTS.md "
        "names the directory as the thing to add when it graduates",
    ),
    Exemption(
        "docs/contract.md",
        "onevcs's own document, quoted by AGENTS.md as the place the release-targets "
        "file's conventional path is specified",
    ),
    Exemption(
        "docs/contract-divergences.md",
        "onepipeline's own document, quoted by AGENTS.md and docs/telemetry.md for the "
        "divergence entries that specify the plan-node release fields",
    ),
    Exemption(
        "docs/protocol.md",
        "onejudge's own document; docs/onejudge-integration.md quotes it as the display "
        "text of a link whose target is its URL at the pinned release",
    ),
    Exemption(
        "docs/metadata.md",
        "onetaskgraph's own document, quoted by AGENTS.md as the human statement of the "
        "rule its github-projects source files each task issue by",
    ),
    Exemption(
        "docs/x.md",
        "an illustrative placeholder in orchestrator/criteria_guard.py's own examples of "
        "criterion prose that cites a file without demanding a change to it",
    ),
    Exemption(
        "orchestrator/y.py",
        "the second half of that same illustrative pair",
    ),
)
EXEMPTED_PATHS = frozenset(exemption.path for exemption in EXEMPTIONS)


def _tracked() -> tuple[str, ...]:
    """Every path git tracks here, which is what "tracked" means everywhere below."""
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return tuple(entry for entry in listed.stdout.split("\0") if entry)


TRACKED = _tracked()
#: A candidate's first segment must be one of these. A repository-relative path starts
#: at the repository root, so anything else is some other repository's path or prose.
TOP_LEVEL = frozenset(entry.split("/", 1)[0] for entry in TRACKED)
#: Every file suffix this workspace tracks. A path wearing any other one names a file
#: in a repository that is not this one — `.rs`, above all.
TRACKED_SUFFIXES = frozenset(suffix for suffix in (Path(p).suffix for p in TRACKED) if suffix)


def scanned_documents() -> tuple[str, ...]:
    """The prose and the diagnostics in scope, in the order git lists them."""
    return tuple(
        entry
        for entry in TRACKED
        if not (REPO_ROOT / entry).is_symlink()
        and (
            (entry.endswith(".md") and ("/" not in entry or entry.startswith("docs/")))
            or (entry.startswith("orchestrator/") and entry.endswith(".py"))
        )
    )


def _resolved(bare: str, directory: str) -> str | None:
    """``bare`` as a path from the repository root, or ``None`` when it leaves it.

    A page under `docs/` links a sibling directory as `../examples/…`, which is the
    form a reader follows and the form the dangling link this gate was written for
    was written in. Only a `../` token is read against ``directory``: every other path
    this repository's prose and diagnostics name is written from the repository root,
    and resolving those against the document would turn `examples/…` quoted in
    `orchestrator/criteria_guard.py` into `orchestrator/examples/…`. A leading `./` is
    the same root-relative path with a command's spelling — `./scripts/nx.sh reset` is
    run at the root — so it is stripped rather than resolved.
    """
    prefix = directory.split("/") if directory and bare.startswith("../") else []
    segments: list[str] = []
    for segment in prefix + bare.split("/"):
        match segment:
            case "" | ".":
                continue
            case ".." if segments:
                segments.pop()
            case "..":
                return None
            case _:
                segments.append(segment)
    return "/".join(segments) or None


def _checkable(token: str, directory: str) -> str | None:
    """``token`` as a repository-relative path to check, or ``None`` when it is not one."""
    candidate = token.rstrip(TRAILING_PUNCTUATION)
    bare = candidate.rstrip("/")
    if "/" not in bare:
        return None
    resolved = _resolved(bare, directory)
    if resolved is None or resolved.split("/", 1)[0] not in TOP_LEVEL:
        return None
    tail = resolved.rsplit("/", 1)[-1]
    if not candidate.endswith("/") and "." not in tail:
        return None
    suffix = Path(resolved).suffix
    if suffix and suffix not in TRACKED_SUFFIXES:
        return None
    return resolved + "/" if candidate.endswith("/") else resolved


@cache
def _ignored(path: str) -> bool:
    """Whether git ignores ``path`` here, which makes it host state rather than content."""
    asked = subprocess.run(
        ["git", "check-ignore", "-q", path.rstrip("/")],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    return asked.returncode == 0


def _exists(path: str) -> bool:
    """Whether ``path`` — a plain path or a glob — names anything under this checkout."""
    bare = path.rstrip("/")
    if "*" in bare:
        return any(REPO_ROOT.glob(bare))
    return (REPO_ROOT / bare).exists()


def dangling_references(text: str, site: str) -> tuple[Reference, ...]:
    """Every repository-relative path ``text`` names that this checkout does not have."""
    found: list[Reference] = []
    directory = site.rsplit("/", 1)[0] if "/" in site else ""
    for number, line in enumerate(text.splitlines(), 1):
        for match in PATH_TOKEN.finditer(line):
            path = _checkable(match.group(0), directory)
            if path is None or _exists(path) or _ignored(path):
                continue
            found.append(Reference(path, f"{site}:{number}"))
    return tuple(found)


@pytest.mark.reads_docs
def test_every_checkable_path_the_scanned_documents_name_exists_in_this_checkout() -> None:
    """The gate itself, over the real documents and the real refusal strings.

    "Checkable" is the whole claim: what a candidate is, and the five shapes that are
    not one, are the module docstring's — a path this detector never looks at is not a
    path it says anything about.
    """
    dangling = [
        reference
        for document in scanned_documents()
        for reference in dangling_references(
            (REPO_ROOT / document).read_text(encoding="utf-8"), document
        )
        if reference.path not in EXEMPTED_PATHS
    ]
    assert not dangling, "\n".join(
        f"{reference.site} names {reference.path}, which does not exist" for reference in dangling
    )


@pytest.mark.reads_docs
def test_the_scan_covers_the_repaired_documents_and_skips_the_agents_symlink() -> None:
    """The scope is what makes the gate mean anything, so it is asserted rather than assumed."""
    scanned = set(scanned_documents())
    assert {
        "README.md",
        "AGENTS.md",
        "docs/orchestration.md",
        "orchestrator/criteria_guard.py",
    } <= scanned
    assert "CLAUDE.md" not in scanned, "the AGENTS.md symlink would double every finding"


#: The deleted plan file, assembled from its parts rather than written out. Every
#: fixture below needs the exact reference the six sites carried, and a module that
#: spelled it contiguously would be a seventh tracked file naming a path this
#: repository no longer has — the very thing the gate exists to end, reintroduced by
#: the gate's own proof. Assembling it keeps the fixtures exact and leaves nothing for
#: a reader grepping for the stale reference to find.
LEGACY_FILE = ".".join(("tracked-graph", "example", "json"))
LEGACY_EXAMPLE = "/".join(("examples", LEGACY_FILE))

#: The six sites as they read before this gate existed, quoted from the tree that
#: carried them — the source indentation of the three refusals dropped, since what is
#: being proven is the text each one printed. Each is one of the references the
#: plan-store move left behind, and the gate is worth nothing unless it fails on all six.
DANGLING_TREE = (
    ("README.md", f"just orchestrate {LEGACY_EXAMPLE}\n"),
    (
        "AGENTS.md",
        "`kind: human` nodes carry only the action prose. Start from\n"
        f"     `{LEGACY_EXAMPLE}`. A node carrying `done_when` is refused\n",
    ),
    (
        "docs/orchestration.md",
        f"See\n[`{LEGACY_FILE}`](../{LEGACY_EXAMPLE}) for direct,\n",
    ),
    (
        "orchestrator/criteria_guard.py",
        'f"{where} is {type(value).__name__}, not an object; a plan states its nodes "\n'
        f'f"as JSON objects — see {LEGACY_EXAMPLE}"\n',
    ),
    (
        "orchestrator/criteria_guard.py",
        'f"{where} is {type(value).__name__}, not a list; a plan states its nodes and "\n'
        f'f"steps as JSON arrays — see {LEGACY_EXAMPLE}"\n',
    ),
    (
        "orchestrator/criteria_guard.py",
        '"the plan states no `tasks`, so there is nothing here to launch; a plan is the "\n'
        f'"file `just orchestrate` loads — see {LEGACY_EXAMPLE}"\n',
    ),
)


@pytest.mark.reads_docs
@pytest.mark.parametrize(("site", "text"), DANGLING_TREE)
def test_the_gate_fails_against_the_tree_that_carried_the_stale_example(
    site: str, text: str
) -> None:
    """Each of the six, put back verbatim, is reported by this checkout's own detector."""
    dangling = dangling_references(text, site)

    assert [reference.path for reference in dangling] == [LEGACY_EXAMPLE], (
        f"{site} carrying its pre-repair text was not reported"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize("exemption", EXEMPTIONS, ids=lambda item: item.path)
def test_an_exemption_that_has_become_a_real_path_is_refused(exemption: Exemption) -> None:
    """An exemption is a claim that a path is absent on purpose, so it is held to it."""
    assert not _ignored(exemption.path), (
        f"{exemption.path} is gitignored, so its presence is host state rather than a "
        "claim about this repository; drop the entry and let the ignore filter answer"
    )
    assert not _exists(exemption.path), (
        f"{exemption.path} now exists, so its exemption — {exemption.reason} — is stale; "
        "delete the entry rather than leaving a line nobody reads again"
    )
