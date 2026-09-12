"""What this checkout says about its plan store, held to the store it configures.

Two documents name the source a plan of this repository is stored in and quote the
repository that source files its issues in, and each has a file that decides it: the
name is read back out of the prose and looked up in the real `onetaskgraph.yaml` rather
than restated here, because a claim checked against a literal in this file would survive
the configuration moving underneath it — and a store the prose names but nothing reaches
is exactly what a half-finished retreat would leave behind.

The one claim here that reads no prose is the artifact half of "in force by merging":
whether the installed plan-store CLI ships any of the crate the two landings that
document classifies that way actually touch, measured on the binary this checkout
installed.
"""

from __future__ import annotations

import re

import pytest
from plan_sources import read_default_sources, read_plan_sources
from published_tools import ONETASKGRAPH_BIN

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The documents that state where a plan of this repository lives.
MANAGER = "AGENTS.md"
ORCHESTRATION = "docs/orchestration.md"
#: The GitHub Projects source this repository configures and plans against. Named as a
#: literal because this source is never repointed — a live run's settlements are
#: projected back to the project it was launched from — so a value derived from the file
#: could not fail when somebody edited it.
BOARD = "plans"
#: The sentence both documents open their plan-store claim with. The source name inside
#: it is the prose's own answer to "where is a plan of this repository stored", and it
#: is what every check below looks up in the configuration.
PLANNED_AGAINST = re.compile(
    r"A plan of (?:\*\*)?this(?:\*\*)? repository is stored on the `([^`]+)` "
    r"GitHub Projects board"
)
#: The section that records where a plan lives. Every other mention points at it rather
#: than restating it.
STORE_SECTION = "Where a plan of this repository lives"


def _flat(text: str) -> str:
    """One line, so a claim is found wherever the paragraph happens to wrap."""
    return " ".join(text.split())


def _configuration() -> str:
    return (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")


def _configured_repository() -> str:
    named = re.search(r"^\s+repository:\s*(\S+)\s*$", _configuration(), re.MULTILINE)
    assert named is not None, (
        "onetaskgraph.yaml's `plans` source has to name the repository its issues are "
        "created in; the adopted release refuses a write without it"
    )
    return named.group(1)


def _document(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def _named_in_prose(name: str) -> str:
    """The source that document tells a reader a plan of this repository is stored in."""
    named = PLANNED_AGAINST.search(_flat(_document(name)))
    assert named is not None, (
        f"{name} has to state which source a plan of this repository is stored in, in the "
        "sentence this gate reads — otherwise nothing holds it to the configured store"
    )
    return named.group(1)


@pytest.mark.parametrize("name", [MANAGER, ORCHESTRATION])
def test_the_repository_the_documents_quote_is_the_repository_the_source_names(
    name: str,
) -> None:
    """Both documents quote the `plans` source's own repository, not a remembered one."""
    configured = _configured_repository()
    quoted = set(re.findall(r"`repository: (\S+)`", _document(name)))
    assert quoted == {configured}, (
        f"{name} quotes `repository: {sorted(quoted)}` where onetaskgraph.yaml names "
        f"{configured}; a board's issues are filed where that file says and nowhere else"
    )


@pytest.mark.parametrize("name", [MANAGER, ORCHESTRATION])
def test_the_source_each_document_names_is_one_the_configuration_defaults_to(
    name: str,
) -> None:
    """Prose naming a store nobody reads is the failure this check exists to catch.

    The name is taken from the sentence itself and looked up in the real
    `onetaskgraph.yaml`: a document left naming a source that was renamed, deleted, or
    dropped from `default_sources` describes a store no command reaches, and reads
    exactly like current guidance.
    """
    planned = _named_in_prose(name)
    sources = read_plan_sources(_configuration())
    defaults = read_default_sources(_configuration())
    assert planned in sources, (
        f"{name} tells a reader a plan of this repository is stored under `{planned}`, "
        f"which onetaskgraph.yaml does not configure at all (it configures {sorted(sources)})"
    )
    assert planned in defaults, (
        f"{name} names the `{planned}` source, which onetaskgraph.yaml configures but does "
        f"not default to (it defaults to {defaults}); a plan stored there goes unlisted by "
        "every read that names no source"
    )
    assert planned == BOARD, (
        f"{name} sends a reader to `{planned}` where this repository plans on the "
        f"`{BOARD}` board; see {MANAGER}, '{STORE_SECTION}'"
    )


def test_both_documents_name_the_same_plan_source() -> None:
    """One store, so the two documents cannot send a reader to different ones."""
    assert _named_in_prose(MANAGER) == _named_in_prose(ORCHESTRATION), (
        f"{MANAGER} and {ORCHESTRATION} name different plan sources, so which one holds "
        "this repository's plans depends on which document the reader opened"
    )


#: The crate whose source the live-lane landings under "in force by merging" change,
#: and the one this CLI must not be shipping if that classification is to hold. Named
#: rather than derived: what is being gated is a claim about *this* crate, and a check
#: that asked "does the binary carry every crate it declares" would answer a different
#: question and pass while the classification silently stopped being true.
LIVE_ONLY_CRATE = "onetaskgraph-live"
#: A crate the archive does carry, so the absence above is read as an absence rather
#: than as this measurement having stopped working. Without it a stripped binary, a
#: changed build profile, or a `strings` that found nothing would all pass.
SHIPPED_CRATE = "onetaskgraph-core"
#: How a Rust binary spells the crate a compiled-in source file came from. Panic
#: locations survive stripping, which is what makes this readable at all on the
#: published artifact.
CRATE_SOURCE_PATH = "crates/{crate}/src"


def test_the_installed_plan_store_cli_ships_none_of_the_live_crate() -> None:
    """The artifact half of "in force by merging", measured on the installed binary.

    Three landings this document classifies that way — the gate-selection repair, the
    startup-sweep repair, and the root-causes plan's own allowance re-check — are
    ancestors of the adopted release, so `git tag --contains` answers that the release
    carries them. The archive does not, and every check in this repository that reads a
    pin passes either way, which is exactly the reading that sent one dispatch looking
    for a bump nobody made.

    So the classification is held to the artifact rather than to the ancestry. The only
    compiled source any of them touches is `onetaskgraph-live`, which is a
    dev-dependency of two crates and of nothing else, so the published CLI carries no
    part of it — and the day that stops being true is the day those landings really are
    adopted through this pin and the paragraph saying otherwise is wrong.

    Both directions are asserted. A crate the binary *does* carry has to be found, or an
    absence proves nothing: a stripped binary that embedded no paths at all would satisfy
    a one-sided check while saying nothing about either landing.
    """
    assert ONETASKGRAPH_BIN.is_file(), (
        f"this checkout's own onetaskgraph is missing at {ONETASKGRAPH_BIN} — run 'just bootstrap'"
    )
    compiled = ONETASKGRAPH_BIN.read_bytes()
    shipped = CRATE_SOURCE_PATH.format(crate=SHIPPED_CRATE).encode()
    live = CRATE_SOURCE_PATH.format(crate=LIVE_ONLY_CRATE).encode()

    assert shipped in compiled, (
        f"the installed plan-store CLI embeds no source path for {SHIPPED_CRATE}, so "
        "this measurement can no longer tell a crate the archive omits from one it "
        f"carries. Re-read how {ONETASKGRAPH_BIN} is built before trusting the "
        "absence below"
    )
    assert live not in compiled, (
        f"the installed plan-store CLI now carries {LIVE_ONLY_CRATE}, the crate holding "
        "the only compiled source that the gate-selection, startup-sweep and "
        f"allowance re-check landings touch. {MANAGER} classifies all three as in force "
        "by merging with no pin to move, and that is now wrong: re-read the paragraph "
        "naming pull/433, pull/538 and pull/821 against what this release actually ships"
    )
