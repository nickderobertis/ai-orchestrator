"""The follow-up documents name only the statuses, board options and record keys that exist.

`orchestrator/follow_up_tickets.py` is the one source of a ticket's record keys and of the
status categories a ticket carries, and `onetaskgraph.yaml`'s `followups` source is the one
source of the board options those statuses are written to. `docs/orchestration.md` and
`AGENTS.md` describe that arrangement in prose, naming a key or an option in backticks where
a reader needs the exact word — and a word renamed in a source leaves the prose describing a
key or an option nothing writes. So each section's backticked names are read here and
classified by what they look like, and every name of each kind has to be one its source holds:

* a **status category** is any word the installed store's category vocabulary holds, read
  off `onetaskgraph task list --help` rather than restated, and has to be a ticket status;
* a **board option** is a capitalised word or phrase, and has to be named by the `followups`
  source's block of `onetaskgraph.yaml`, its mapping or the comment naming what the board
  carries;
* a **record key** is a name written as a record's (`` record's `name` ``) or as a key
  (`` `name` key ``), or a `snake_case` name, and has to be a key of the ticket's record —
  or, for a `snake_case` name, a setting `onetaskgraph.yaml` holds, or the store's own
  front-matter field a ticket's dependency on an accepted ticket is written in, which the
  module names once as `DEPENDENCY_FIELD` and which is deliberately no record key. The
  record's one optional key, its binding to a board item (`BINDING_FIELD`), is a key too.

That the task the recipe composes carries the module's rendered contract is
`tests/test_follow_up_tickets.py`'s, which composes the tracked `config/follow-up-task.md`
and fails when the contract stops reaching it.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from published_tools import ONETASKGRAPH_BIN

from orchestrator import follow_up_tickets as tickets
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: Each document describing the follow-up tickets, and the heading of its section.
SECTIONS = {
    "AGENTS.md": "### Follow-ups: drafted while a run works, verified once it ends",
    "docs/orchestration.md": "### Follow-ups are drafted, not surfaced",
}

NAMED = re.compile(r"`([^`\n]+)`")
OPTION = re.compile(r"[A-Z][a-z]+(?: [A-Za-z][a-z]+)*")
SNAKE_CASE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
KEY = re.compile(r"record's `(?P<held>[^`\n]+)`|`(?P<keyed>[^`\n]+)` key\b")
HEADING = re.compile(r"^#{1,3} ", re.MULTILINE)


def section(document: str, heading: str) -> str:
    """The text under ``heading`` in ``document``, up to the next heading of its level or above."""
    text = (REPO_ROOT / document).read_text(encoding="utf-8")
    opened = text.index(f"\n{heading}\n") + len(heading) + 2
    following = HEADING.search(text, opened)
    return text[opened : following.start() if following else len(text)]


def store_categories() -> frozenset[str]:
    """Every status category the installed plan store's vocabulary holds."""
    helped = subprocess.run(  # noqa: S603 - the installed plan-store CLI
        [str(ONETASKGRAPH_BIN), "task", "list", "--help"],
        text=True,
        capture_output=True,
        check=True,
    )
    listed = helped.stdout.split("--status", 1)[1].split("\n      --", 1)[0]
    return frozenset(re.findall(r"^\s+- ([a-z-]+):", listed, re.MULTILINE))


def followups_block() -> str:
    """The `followups` source's block of `onetaskgraph.yaml`, comments included."""
    text = (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")
    opened = text.index("\n  followups:\n") + 1
    following = re.compile(r"^  \S", re.MULTILINE).search(text, opened + 1)
    return text[opened : following.start() if following else len(text)]


def unheld(prose: str, categories: frozenset[str]) -> set[str]:
    """Every name ``prose`` gives a status category, board option or record key none holds."""
    configuration = (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")
    options = followups_block()
    found = set()
    for name in NAMED.findall(prose):
        if name in categories and name not in tuple(tickets.Status):
            found.add(f"`{name}` is a status category no ticket carries")
        elif OPTION.fullmatch(name) and f"`{name}`" not in options and f": {name}\n" not in options:
            found.add(f"`{name}` is a board option the `followups` source does not name")
        elif (
            SNAKE_CASE.fullmatch(name)
            and name not in (*tickets.RECORD_KEYS, tickets.BINDING_FIELD)
            and name != tickets.DEPENDENCY_FIELD
            and not re.search(rf"^\s*{re.escape(name)}:", configuration, re.MULTILINE)
        ):
            found.add(f"`{name}` is a record key the ticket does not carry")
    for matched in KEY.finditer(prose):
        name = matched["held"] or matched["keyed"]
        if name not in (*tickets.RECORD_KEYS, tickets.BINDING_FIELD):
            found.add(f"`{name}` is a record key the ticket does not carry")
    return found


@pytest.mark.parametrize(("document", "heading"), SECTIONS.items())
def test_each_follow_up_section_names_only_what_the_module_and_configuration_hold(
    document: str, heading: str
) -> None:
    prose = section(document, heading)

    assert unheld(prose, store_categories()) == set(), (
        f"{document}'s section {heading!r} names what neither "
        "orchestrator/follow_up_tickets.py nor onetaskgraph.yaml holds"
    )


@pytest.mark.parametrize(("document", "heading"), SECTIONS.items())
def test_each_follow_up_section_describes_the_host_and_the_proposal(
    document: str, heading: str
) -> None:
    """Each section names the host key and the two options the user's decision moves between."""
    prose = section(document, heading)

    for named in ("record's `host`", "`Proposal`", "`Todo`"):
        assert named in prose, f"{document}'s section {heading!r} does not name {named}"
    flat = " ".join(prose.split())
    for described in ("closes a proposal as not planned", "never withdraws a ticket the board"):
        assert described in flat, f"{document}'s section {heading!r} does not say {described!r}"


@pytest.mark.parametrize(("document", "heading"), SECTIONS.items())
def test_each_follow_up_section_files_a_tickets_issue_in_its_root_causes_repository(
    document: str, heading: str
) -> None:
    """Each section says where a ticket's issue is created, and no longer says the board's own."""
    flat = " ".join(section(document, heading).split())

    for described in (
        "issue is created in the repository its root cause lives in",
        "under the board's owner",
        "as an item of the one board",
    ):
        assert described in flat, f"{document}'s section {heading!r} does not say {described!r}"
    assert "board's own repository" not in flat, (
        f"{document}'s section {heading!r} still says a ticket is filed in the board's own "
        "repository"
    )


def test_the_check_names_every_status_option_or_key_neither_source_holds() -> None:
    """The check can fail: a name of each kind that nothing holds is reported, and no other."""
    prose = (
        "A ticket lands in `Accepted` and then `Todo` or `Deferred`, is `unknown` rather than "
        "`draft` or `backlog`, and its record's `hostname` sits beside the `verified_on` key "
        "and `host`; `default_sources` is a setting; it depends on an accepted ticket as "
        "`depends_on`, never as `depended_on`."
    )

    assert unheld(prose, store_categories()) == {
        "`Accepted` is a board option the `followups` source does not name",
        "`unknown` is a status category no ticket carries",
        "`hostname` is a record key the ticket does not carry",
        "`verified_on` is a record key the ticket does not carry",
        "`depended_on` is a record key the ticket does not carry",
    }


def test_every_ticket_status_is_a_category_of_the_installed_store() -> None:
    """A ticket's status is read as the store's category, so each has to be one."""
    categories = store_categories()

    assert set(tickets.Status) <= categories, sorted(categories)
    assert tickets.Status.DEFERRED.value == "draft" in categories


@pytest.mark.parametrize(("document", "heading"), SECTIONS.items())
def test_each_follow_up_section_carries_the_modules_status_vocabulary(
    document: str, heading: str
) -> None:
    """Each section copies what `status_vocabulary()` renders; line wrapping is its own."""
    flat = " ".join(section(document, heading).split())

    assert " ".join(tickets.status_vocabulary().split()) in flat, (
        f"{document}'s section {heading!r} does not carry the status vocabulary "
        "`python -m orchestrator.follow_up_tickets statuses` prints"
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] This module is the
# documents' drift gate, and every test in it is selected by its file-wide `reads_docs`
# marker into the tier that reads this repository's prose; a new reading of the same two
# sections joins the module rather than founding a project for one test.
@pytest.mark.parametrize(("document", "heading"), SECTIONS.items())
def test_each_follow_up_section_writes_every_ticket_against_the_boards_accepted_fixes(
    document: str, heading: str
) -> None:
    """Each section states the manager's view of the dependency on an accepted ticket."""
    flat = " ".join(section(document, heading).split())

    for described in (
        "written against the board's accepted fixes",
        "`Todo`, `Queued`, `In Progress`, and `Done` where the fix has not reached the basis",
        f"as the store's own `{tickets.DEPENDENCY_FIELD}` edge",
        "`onetaskgraph task deps` walks it from either end",
        "A `Proposal` or `Deferred` item's fix is never assumed",
        "The same-root-cause path is unchanged",
    ):
        assert described in flat, f"{document}'s section {heading!r} does not say {described!r}"
    assert f"record's `{tickets.DEPENDENCY_FIELD}`" not in flat, (
        f"{document}'s section {heading!r} calls the store's edge a record key"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def test_the_manager_document_says_accepted_means_todo_and_names_the_command() -> None:
    flat = " ".join(section("AGENTS.md", SECTIONS["AGENTS.md"]).split())

    for said in (
        "A dispatch briefed to pick up accepted follow-up tickets selects `Todo` items only",
        "`python -m orchestrator.follow_up_tickets statuses` prints",
        "**Every listing of the board is `python -m orchestrator.follow_up_tickets "
        "board-items`.** It takes `--board`, an optional `--search TEXT` and the accepted "
        "filter as repeated `--status` flags, reads the pinned plan store through every `next` "
        "page until none remains — refusing a cursor it has already followed — and prints one "
        "combined JSON result",
        "**A ticket is copied onto the board item it is bound to, and nowhere else.** Its "
        f"record's `{tickets.BINDING_FIELD}` key holds that item's native id, and `python -m "
        "orchestrator.follow_up_tickets board-status` and its `copy` are what write it",
        f"when two items carry one ticket's `{tickets.ORIGIN_KEY}`, naming the run's own open "
        "item and leaving each withdrawn duplicate a comment naming that item",
        "Both refuse, naming both ids, a destination the store reports that differs from the "
        "binding, and every copy the task prescribes goes through `copy`",
    ):
        assert said in flat, said
