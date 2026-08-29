"""What this checkout says about its plan store, held to the store it configures.

Two numbers and one name are stated in prose here, and each has a file that decides it:
the adopted `onetaskgraph` release, and the repository the `plans` source creates its
project and task issues in. Both moved together in one change — the release below the
adopted one refuses the `repository` field as unknown and the adopted one refuses a
write without it — so a document naming one of them at the wrong value describes a host
that cannot write a plan to its board at all, which is the state this pair was adopted
to leave behind.

A third claim is gated the same way and for a sharper reason: **which source this
repository plans against**. That is a temporary retreat from the board, and a retreat
nobody can tell from a decision is one the next reader adopts as the design — so the
documents are held to naming the source `onetaskgraph.yaml` actually defaults to, to
recording both defects that forced the retreat, and to naming both conditions that
retire it. The name is read back out of the prose and looked up in the real
configuration rather than restated here, because a claim checked against a literal in
this file would survive the configuration moving underneath it.

Gated rather than trusted because a version in prose goes stale silently: nothing else
in this repository reads these sentences, so the next bump would leave them describing
the release before it. The release check is deliberately a closed one — every
`onetaskgraph <version>` in that document is the adopted one — so a release this host no
longer runs is named relatively, as *the release below the adopted one*, rather than by
a number that reads like something installed here.
"""

from __future__ import annotations

import re

import pytest
from plan_sources import read_default_sources, read_plan_sources

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The documents that state either value, and what each is asked to carry.
MANAGER = "AGENTS.md"
ORCHESTRATION = "docs/orchestration.md"
#: The published source the manager document reads the live lane's own rule out of. It
#: is cited at a tag, and a tag is a version claim in a spelling the release check below
#: cannot see — `v0.2.11` is not `onetaskgraph 0.2.11` — so it is asked for separately.
LANE_SOURCE = "crates/onetaskgraph-github-projects/tests/live.rs"
#: The GitHub Projects source this repository configures and does not plan against.
#: Named as a literal because the retreat's whole shape is that this source is *not*
#: edited: a value derived from the file could not fail when somebody edited it.
BOARD = "plans"
#: The sentence both documents open their plan-store claim with. The source name inside
#: it is the prose's own answer to "where is a plan of this repository stored", and it
#: is what every check below looks up in the configuration.
PLANNED_AGAINST = re.compile(
    r"A plan of (?:\*\*)?this(?:\*\*)? repository is stored under the `([^`]+)` source"
)
#: The section that records the retreat once. Every other mention points at it.
RETREAT_SECTION = "Where a plan of this repository lives"


def _flat(text: str) -> str:
    """One line, so a claim is found wherever the paragraph happens to wrap."""
    return " ".join(text.split())


def _adopted_release() -> str:
    return (REPO_ROOT / "config" / "onetaskgraph.version").read_text(encoding="utf-8").strip()


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


def test_the_release_the_manager_document_names_is_the_release_this_host_installs() -> None:
    """`AGENTS.md` names the adopted onetaskgraph, and names the pin's own value."""
    adopted = _adopted_release()
    stated = set(re.findall(r"onetaskgraph (\d+\.\d+\.\d+)", _document(MANAGER)))
    assert stated == {adopted}, (
        f"{MANAGER} names onetaskgraph {sorted(stated)} where config/onetaskgraph.version "
        f"reads {adopted}; re-date that passage with the release this host installs"
    )


def test_the_tag_the_lane_source_is_cited_at_is_the_release_this_host_installs() -> None:
    """The manager document reads that lane's rule at the tag this host runs, not an older one.

    A citation to a tag ages exactly like a version does — the file it names can change
    under it — and this one is the whole evidence for what the live lane requires, so it
    moves with the pin rather than being left pointing at the release before it.
    """
    adopted = _adopted_release()
    cited = f"`{LANE_SOURCE}` at tag `v{adopted}`"
    assert cited in _flat(_document(MANAGER)), (
        f"{MANAGER} has to read the live lane's rule at the release this host installs, "
        f"which is {cited}; re-read that file at the new tag rather than re-dating the "
        "sentence alone"
    )


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
    assert planned != BOARD, (
        f"{name} sends a reader to the `{BOARD}` board, which cannot hold more than one "
        f"plan today; see {MANAGER}, '{RETREAT_SECTION}'"
    )


def test_both_documents_name_the_same_plan_source() -> None:
    """One store, so the two documents cannot send a reader to different ones."""
    assert _named_in_prose(MANAGER) == _named_in_prose(ORCHESTRATION), (
        f"{MANAGER} and {ORCHESTRATION} name different plan sources, so which one holds "
        "this repository's plans depends on which document the reader opened"
    )


def test_the_manager_document_records_the_retreat_and_both_defects_behind_it() -> None:
    """The retreat reads as a retreat, with both defects that forced it named."""
    text = _flat(_document(MANAGER))
    planned = _named_in_prose(MANAGER)
    assert f"## {RETREAT_SECTION}" in _document(MANAGER), (
        f"{MANAGER} has to carry a `{RETREAT_SECTION}` section; every other mention of "
        f"`{planned}` points at it rather than restating it"
    )
    assert "retreat" in text, (
        f"{MANAGER} has to say that planning under `{planned}` is a retreat: an "
        "undocumented retreat is indistinguishable from a decision, and the next reader "
        "takes it for the intended design"
    )
    assert "discards the query it is handed" in text, (
        f"{MANAGER} has to name the first defect — onetaskgraph's `github-projects` source "
        "discards the query it is handed, so a read scoped to one project answers with "
        "every project's tasks"
    )
    assert "renames a destination project to its own native identifier" in text, (
        f"{MANAGER} has to name the second defect — onepipeline's settlement write-back "
        "renames a destination project to its own native identifier"
    )
    assert "writes no labels" in text, (
        f"{MANAGER} has to say the settlement write-back writes no labels; a defect named "
        "by half is one nobody can tell has been fixed"
    )


def test_the_manager_document_names_both_conditions_that_retire_the_retreat() -> None:
    """A retreat with no stated exit is one nobody knows to undo."""
    text = _flat(_document(MANAGER))
    assert "honours the query it is handed" in text, (
        f"{MANAGER} has to name the first retirement condition: a released onetaskgraph "
        "whose `github-projects` source honours the query it is handed"
    )
    assert "preserves a destination project's title and its labels" in text, (
        f"{MANAGER} has to name the second retirement condition: a released onepipeline "
        "whose write-back preserves a destination project's title and its labels"
    )


def test_the_manager_document_says_retiring_the_retreat_is_a_deletion() -> None:
    """The revert is subtraction, and saying so is what stops a second migration."""
    text = _flat(_document(MANAGER))
    planned = _named_in_prose(MANAGER)
    assert f"Delete the `{planned}` source from `onetaskgraph.yaml`" in text, (
        f"{MANAGER} has to say that retiring the retreat deletes the `{planned}` source "
        f"rather than re-editing the `{BOARD}` source"
    )
    assert f"never a re-edit of the `{BOARD}` source" in text, (
        f"{MANAGER} has to say the retirement is never a re-edit of the `{BOARD}` source: "
        "a retreat that repointed it would need a second migration to undo, and a "
        "half-applied second migration is what this shape exists to avoid"
    )


@pytest.mark.parametrize("name", [MANAGER, ORCHESTRATION])
def test_no_document_sends_a_reader_to_author_a_new_plan_on_the_board(name: str) -> None:
    """Both documents forbid the board outright rather than merely preferring the store."""
    text = _flat(_document(name))
    forbidden = (
        f"Never author a new plan on the `{BOARD}` board.",
        f"no new plan of it is authored on the `{BOARD}` board",
    )
    assert any(sentence in text for sentence in forbidden), (
        f"{name} has to say outright that no new plan of this repository is authored on "
        f"the `{BOARD}` board; preferring the local store leaves the board readable as an "
        "option, and one plan too many on it corrupts every plan there"
    )
