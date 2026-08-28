"""What this checkout says about its plan store, held to the store it configures.

Two numbers and one name are stated in prose here, and each has a file that decides it:
the adopted `onetaskgraph` release, and the repository the `plans` source creates its
project and task issues in. Both moved together in one change — the release below the
adopted one refuses the `repository` field as unknown and the adopted one refuses a
write without it — so a document naming one of them at the wrong value describes a host
that cannot write a plan to its board at all, which is the state this pair was adopted
to leave behind.

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

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The documents that state either value, and what each is asked to carry.
MANAGER = "AGENTS.md"
ORCHESTRATION = "docs/orchestration.md"
#: The published source the manager document reads the live lane's own rule out of. It
#: is cited at a tag, and a tag is a version claim in a spelling the release check below
#: cannot see — `v0.2.11` is not `onetaskgraph 0.2.11` — so it is asked for separately.
LANE_SOURCE = "crates/onetaskgraph-github-projects/tests/live.rs"


def _flat(text: str) -> str:
    """One line, so a claim is found wherever the paragraph happens to wrap."""
    return " ".join(text.split())


def _adopted_release() -> str:
    return (REPO_ROOT / "config" / "onetaskgraph.version").read_text(encoding="utf-8").strip()


def _configured_repository() -> str:
    text = (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")
    named = re.search(r"^\s+repository:\s*(\S+)\s*$", text, re.MULTILINE)
    assert named is not None, (
        "onetaskgraph.yaml's `plans` source has to name the repository its issues are "
        "created in; the adopted release refuses a write without it"
    )
    return named.group(1)


def _document(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


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
