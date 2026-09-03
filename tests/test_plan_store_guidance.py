"""What this checkout says about its plan store, held to the store it configures.

Two numbers and one name are stated in prose here, and each has a file that decides it:
the adopted `onetaskgraph` release, and the repository the `plans` source creates its
project and task issues in. Both moved together in one change — the release below the
adopted one refuses the `repository` field as unknown and the adopted one refuses a
write without it — so a document naming one of them at the wrong value describes a host
that cannot write a plan to its board at all, which is the state this pair was adopted
to leave behind.

A third claim is gated the same way and for a sharper reason: **which source this
repository plans against**. It was a local Markdown store for one day in August 2026,
while two upstream defects made a second plan on the board corrupt every plan on it;
both were repaired and adopted, and the store is the board again. What survives that
episode is the gate, not the workaround: the documents are held to naming the source
`onetaskgraph.yaml` actually defaults to, read back out of the prose and looked up in
the real configuration rather than restated here, because a claim checked against a
literal in this file would survive the configuration moving underneath it — and because
a store the prose names but nothing reaches is exactly what the retreat's own
half-finished shape would have left behind.

Gated rather than trusted because a version in prose goes stale silently: nothing else
in this repository reads these sentences, so the next bump would leave them describing
the release before it. The release check is deliberately a closed one — every
`onetaskgraph <version>` in that document is the adopted one — so a release this host no
longer runs is named relatively, as *the release below the adopted one*, rather than by
a number that reads like something installed here.

One release is nameable beside the adopted one, and only while it has to be: the floor
`tests/plan_store_pin.py` declares, on a host pinned below it. A pin behind a fix on
purpose reads exactly like a pin nobody moved, so a document describing one is required
to say so and to quote what it is trading for — and at or past that floor both the
widening and the requirement fall away on their own, because they are derived from the
pin rather than set beside it. This host is at the floor, so a passage still saying the
pin is held fails here.
"""

from __future__ import annotations

import re

import pytest
from plan_sources import read_default_sources, read_plan_sources
from plan_store_pin import BLOCKED_BY, PACING_FLOOR, held_below_the_pacing_floor

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The documents that state either value, and what each is asked to carry.
MANAGER = "AGENTS.md"
ORCHESTRATION = "docs/orchestration.md"
#: The published source the manager document reads the live lane's own rule out of. It
#: is cited at a tag, and a tag is a version claim in a spelling the release check below
#: cannot see — `v0.2.11` is not `onetaskgraph 0.2.11` — so it is asked for separately.
LANE_SOURCE = "crates/onetaskgraph-github-projects/tests/live.rs"
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
#: The section that records where a plan lives, and what the store cost to get back.
#: Every other mention points at it rather than restating it.
STORE_SECTION = "Where a plan of this repository lives"


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
    """`AGENTS.md` names the adopted onetaskgraph, and names the pin's own value.

    A closed set of one, widened to two by exactly one thing: a **declared hold**. While
    `config/onetaskgraph.version` sits below `PACING_FLOOR` the document has to be able to
    name the release it is held below, or it cannot say what this host is missing. At or
    past that floor the set narrows back to one on its own, which is where this host is:
    the release is simply the one installed, and a passage still naming another comes due
    here rather than standing as a note about a bump that already happened.
    """
    adopted = _adopted_release()
    nameable = {adopted} | ({PACING_FLOOR} if held_below_the_pacing_floor() else set())
    stated = set(re.findall(r"onetaskgraph (\d+\.\d+\.\d+)", _document(MANAGER)))
    assert stated == nameable, (
        f"{MANAGER} names onetaskgraph {sorted(stated)} where config/onetaskgraph.version "
        f"reads {adopted} and the release(s) it may also name are {sorted(nameable)}; "
        "re-date that passage with the release this host installs"
    )


def test_the_manager_document_says_the_plan_store_pin_is_held_and_what_blocks_it() -> None:
    """A pin held below a release carrying a fix says so, or it reads as an oversight.

    This is the one shape of staleness the release check above cannot catch: a pin that
    is behind on purpose looks exactly like a pin nobody moved, and the next reader
    either bumps it — trading an occasional visible refusal on the copy path for a
    silent write-back refusal that loses every settlement projection — or spends the
    diagnosis again. So a host below the floor has to name the release it is held below
    and quote what it is trading for, and a host at or past it has to stop saying so:
    this one is past it, and a sentence left behind would describe a hold that ended.
    """
    text = _flat(_document(MANAGER))
    held = held_below_the_pacing_floor()
    says_held = "`config/onetaskgraph.version` is deliberately held below it" in text
    assert says_held is held, (
        f"config/onetaskgraph.version reads {_adopted_release()} against a "
        f"{PACING_FLOOR} floor, so {MANAGER} "
        + ("has to say the pin is held" if held else "must stop saying the pin is held")
    )
    if not held:
        return
    assert BLOCKED_BY in text, (
        f"{MANAGER} has to quote {BLOCKED_BY} — the refusal a hold is trading for — or a "
        "reader cannot tell a deliberate hold from a pin nobody moved"
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


def test_the_manager_document_records_what_the_store_cost_to_get_back() -> None:
    """Both repaired defects stay named, because a fix nobody can name is one nobody can check.

    The board held one plan at a time until two upstream defects were repaired. Naming
    them is what lets the next reader tell a working store from one that has silently
    regressed to the same behaviour: a scoped read that answers with every project's
    tasks looks exactly like a board with one plan on it.
    """
    text = _flat(_document(MANAGER))
    assert f"## {STORE_SECTION}" in _document(MANAGER), (
        f"{MANAGER} has to carry a `{STORE_SECTION}` section; every other mention of the "
        "plan store points at it rather than restating it"
    )
    assert "discarded the query it was handed" in text, (
        f"{MANAGER} has to name the first defect — onetaskgraph's `github-projects` source "
        "discarded the query it was handed, so a read scoped to one project answered with "
        "every project's tasks"
    )
    assert "renamed a destination project to its own native identifier" in text, (
        f"{MANAGER} has to name the second defect — onepipeline's settlement write-back "
        "renamed a destination project to its own native identifier"
    )
    assert "wrote no labels" in text, (
        f"{MANAGER} has to say the settlement write-back wrote no labels; a defect named "
        "by half is one nobody can tell has been fixed"
    )


def test_the_manager_document_says_the_store_was_proven_against_the_real_board() -> None:
    """A repair adopted but never driven is one this host has no evidence for.

    Both defects are invisible from every gate here: a discarded query answers, and a
    degraded projection settles green. So the document is held to saying the pair was
    measured against the real board with two projects on it, which is the only condition
    under which either could have been observed at all.
    """
    text = _flat(_document(MANAGER))
    assert "against the real board with two" in text, (
        f"{MANAGER} has to say the repaired store was measured against the real board with "
        "two projects on it; one project on the board is the state in which both defects "
        "are invisible"
    )


def test_the_manager_document_keeps_the_best_effort_warning_the_repair_did_not_fix() -> None:
    """The projection is still best-effort, and the repair is the moment that gets forgotten.

    A reader who has just been told the write-back preserves what it projects is the one
    most likely to start trusting a settled board as the record of a run. It is not one:
    the projection runs off the reconcile loop, so a settlement that never landed settles
    the run exactly like one that did.
    """
    text = _flat(_document(MANAGER))
    assert "best-effort" in text, (
        f"{MANAGER} has to keep saying the write-back is best-effort; the repair changed "
        "what a projection writes, not whether a green run proves it landed"
    )


def test_the_manager_document_says_the_board_source_is_never_repointed() -> None:
    """The one value on that source that no change may move, and why."""
    text = _flat(_document(MANAGER))
    assert "moves a running plan's store out from under it" in text, (
        f"{MANAGER} has to say why the `{BOARD}` source is never repointed: a live run's "
        "settlements are projected back to the project it was launched from"
    )


def test_the_manager_document_says_which_landing_this_pin_does_not_carry() -> None:
    """A landed fix that ships in no artifact is the one shape no pin gate can catch.

    `config/onetaskgraph.version` names a release archive, and the change that put that
    repository's shell scripts into its own project graph touches no crate source — so
    release-plz cut nothing for it, there is no version to move to, and every check in
    this repository that reads a pin passes whether or not anybody knows the fix exists.
    A reader reconciling which landed fixes are in force here therefore finds one with no
    pin behind it and no failing check to explain that, and the reading available to them
    is the wrong one: a bump nobody made. One dispatch of this repository was already
    failed on it. So the document is held to naming that landing, citing where it landed,
    and saying the fix is in force by merging rather than by adoption.
    """
    text = _flat(_document(MANAGER))
    assert "https://github.com/nickderobertis/onetaskgraph/pull/273" in text, (
        f"{MANAGER} has to cite the change request that put onetaskgraph's shell scripts "
        "into its project graph; a claim about a landing with no release behind it is "
        "readable only against the landing itself"
    )
    assert "touches no crate source" in text, (
        f"{MANAGER} has to say why no release carries that landing — it touches no crate "
        "source, so there is no versioned artifact for release-plz to bump"
    )
    assert "**is in force**, by merging" in text, (
        f"{MANAGER} has to say that fix is in force by merging; a landed fix left "
        "unclassified reads as one this host has failed to adopt"
    )
