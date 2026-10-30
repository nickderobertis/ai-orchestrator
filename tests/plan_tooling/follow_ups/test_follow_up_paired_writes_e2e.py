"""No follow-up ticket write lands half done: every way to create, close or withdraw one.

A ticket reaches the board through `board-status`, which decides the status it is copied with
from the board, and `copy`, which every write but a withdrawal makes under the store's
`preserve-destination` status policy, so an item the board holds keeps its Status and its
issue's state and a new one takes the ticket's proposal. Each other way the follow-up task's
tools give an agent to create, close or withdraw a ticket is driven here against the loopback
GitHub board (`tests/github_board.py`), read and written by the locked plan store through its
real `github-projects` plugin:

* **`copy` of a status `board-status` did not decide** — a new ticket written at a status other
  than a proposal's, and a withdrawal of an item a person accepted, are refused before anything
  is written; a proposal written as finished, and a stale proposal over an item the engine
  closed as done, are copied with the item's Status and its issue's state left as they stand.
* **A copy after a person moved the item** — whenever `board-status` last ran, a copy reads no
  status of its own to write: one that is not a withdrawal keeps what the person chose, and a
  withdrawal reads the item's live status and refuses one a person accepted or deferred. A copy
  reads nothing beyond its one `task locate` and the store's own `--target-task` verification.
* **A raw issue write with the board credential the agent holds** — an issue closed as not
  planned with no Status write beside it — which nothing in this repository can refuse as it is
  made: the rendered task forbids it (`tests/test_follow_up_tickets.py`), and
  `check-dispositions --board` refuses the run after it, naming the item and its status. The
  check accepts the run's items when they are in line, and refuses a withdrawn ticket whose
  item was never closed.
* **A direct plan-store CLI write on a board item** — the run's own proposal moved by `task
  status set` with no `board-status` deciding it — which nothing here can refuse as it is made
  either: the rendered task forbids it, and `check-dispositions --board` refuses the run after
  it, naming the item and the status it holds.

llmlint: ignore-file[tests_mirror_real_usage, e2e_not_mocked] Each command runs through
`orchestrator.follow_up_tickets`' own `main` with the command's own arguments, in this process,
because these journeys hold that module to the coverage floor and pytest-cov measures no child
process here (`pyproject.toml`'s `[tool.coverage.run]` patches none); every board request still
goes through the locked store CLI to the loopback. What a person or the engine does on GitHub is
planted on the loopback's row, as the other loopback journeys plant it; the raw issue write is
sent to the loopback's REST issue endpoint, as an agent holding the board credential sends it.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The task that added these
journeys requires them over the loopback board its `test_follow_up_comment_snapshot_e2e.py`
fixture serves, which lives in this `plan-tooling-follow-ups` project and whose key already
covers what they read; a project of their own would re-key that shared fixture for one module.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import github_board as remote
import pytest
from test_follow_up_comment_snapshot_e2e import HOST, Loopback, _row, loopback

from orchestrator import follow_up_tickets as tickets

__all__ = ["loopback"]

BOARD = tickets.BOARD
RUN = remote.PROPOSED_RUN
#: The one draft every ticket here was verified from, as `remote._follow_up_ticket` names it.
DRAFT = f"drafts:{RUN}/drafts/noticed"


def _written(loopback: Loopback, cause: str) -> Path:
    """The run's new ticket for ``cause``, as the agent writes it before `board-status`."""
    ticket = remote._follow_up_ticket(remote.SIBLING_REPOSITORY, cause=cause, verified_on=HOST)
    return remote._written_ticket(loopback.root, ticket)


def _status_written(path: Path, word: str) -> None:
    """Write ``word`` as the ticket's `status`, as the agent writes the word it was given."""
    text = path.read_text(encoding="utf-8")
    held = tickets.read_ticket(path, pending=True).status.value
    assert f'status: "{held}"' in text, text
    path.write_text(text.replace(f'status: "{held}"', f'status: "{word}"', 1), encoding="utf-8")


def _decided(path: Path, capsys: pytest.CaptureFixture[str], *, withdraw: bool = False) -> str:
    """`board-status` on the ticket, its printed word written as the ticket's status."""
    capsys.readouterr()
    arguments = ["board-status", "--board", BOARD, *(["--withdraw"] if withdraw else []), str(path)]
    assert tickets.main(arguments) == tickets.SOUND, capsys.readouterr().err
    word = capsys.readouterr().out.strip()
    _status_written(path, word)
    return word


def _copy(path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    capsys.readouterr()
    status = tickets.main(["copy", "--board", BOARD, str(path)])
    return status, " ".join(capsys.readouterr().err.split())


def _filed(loopback: Loopback, cause: str, capsys: pytest.CaptureFixture[str]) -> tuple[Path, str]:
    """The run's ticket for ``cause`` decided and copied as the task sequences it; its item."""
    path = _written(loopback, cause)
    _decided(path, capsys)
    status, said = _copy(path, capsys)
    assert status == tickets.SOUND, said
    # The store records its link to the item its copy created on the ticket.
    item = tickets.read_ticket(path, pending=True).link(BOARD)
    assert item is not None
    return path, f"{BOARD}:{item}"


class _Card(NamedTuple):
    """What the loopback shows of one card: its Status option, its issue's state, and why
    that issue closed. A tuple still, so a test states an expected card positionally."""

    status: str | None
    state: str
    closed_as: str | None


def _card(issue: str) -> _Card:
    """The loopback's own row for ``issue``, as the card it shows."""
    row = _row(issue)
    return _Card(row.status, row.state, row.state_reason)


def _accounted(loopback: Loopback, causes: list[str]) -> None:
    """The run's account: its one draft filed, supporting each of ``causes``."""
    drafts = loopback.root / "tasks" / RUN / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "noticed.md").write_text(
        '---\ntitle: "noticed"\n---\n\n## What happened\n\nSomething.\n', encoding="utf-8"
    )
    account = tickets.open_dispositions(loopback.root, RUN)
    document = json.loads(account.read_text(encoding="utf-8"))
    document["dispositions"] = [
        {
            "draft": DRAFT,
            "disposition": tickets.Disposition.FILED.value,
            "root_causes": causes,
            "detail": "Its claim holds at the basis, and the tickets carry it.",
        }
    ]
    account.write_text(json.dumps(document), encoding="utf-8")


def _checked(loopback: Loopback, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    capsys.readouterr()
    arguments = ["check-dispositions", "--root", str(loopback.root), "--board", BOARD, RUN]
    status = tickets.main(arguments)
    held = capsys.readouterr()
    return status, " ".join((held.out + held.err).split())


def test_a_new_ticket_copied_at_a_status_board_status_did_not_decide_creates_nothing(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _written(loopback, "new-ticket-written-as-accepted")
    assert _decided(path, capsys) == tickets.Status.PROPOSED.value
    _status_written(path, tickets.Status.ACCEPTED.value)
    issues = len(remote.BOARD.issues)

    status, said = _copy(path, capsys)

    assert status == tickets.UNDECIDED, said
    assert "carries the status `todo`, where the board holds no item for it" in said, said
    assert "`board-status` decides `backlog`" in said, said
    assert len(remote.BOARD.issues) == issues, "a refused copy created an issue"
    assert tickets.read_ticket(path, pending=True).link(BOARD) is None


def test_a_ticket_withdrawn_before_any_copy_is_created_closed_as_not_planned(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    """A withdrawal reaching no item reads no status and creates one closed as not planned."""
    path = _written(loopback, "withdrawn-before-it-was-filed")
    assert _decided(path, capsys, withdraw=True) == tickets.Status.WITHDRAWN.value

    status, said = _copy(path, capsys)

    assert status == tickets.SOUND, said
    item = tickets.read_ticket(path, pending=True).link(BOARD)
    assert item is not None
    assert _card(f"{BOARD}:{item}") == ("Cancelled", "CLOSED", "NOT_PLANNED")


def test_a_withdrawal_board_status_did_not_decide_leaves_an_accepted_item_open_at_todo(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    path, issue = _filed(loopback, "accepted-then-withdrawn-by-hand", capsys)
    loopback.store("task", "status", "set", issue, tickets.Status.ACCEPTED.value)
    before = _card(issue)
    assert before == ("Todo", "OPEN", None), before
    _status_written(path, tickets.Status.WITHDRAWN.value)

    status, said = _copy(path, capsys)

    assert status == tickets.PROTECTED, said
    assert "a person accepted or deferred, so this run never withdraws it" in said, said
    assert _card(issue) == before


def test_a_ticket_written_as_done_leaves_its_proposal_open(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    path, issue = _filed(loopback, "proposal-written-as-done", capsys)
    before = _card(issue)
    assert before == ("Proposal", "OPEN", None), before
    _status_written(path, tickets.Status.FINISHED.value)

    status, said = _copy(path, capsys)

    assert status == tickets.SOUND, said
    assert _card(issue) == before


def test_a_stale_proposal_never_reopens_an_item_the_engine_closed_as_done(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    path, issue = _filed(loopback, "closed-by-a-landing", capsys)
    # The engine's closure write for a landing whose change closed the issue: the card at
    # `Done` and the issue closed as completed, through the store's own paired write.
    loopback.store("task", "status", "set", issue, tickets.Status.FINISHED.value)
    before = _card(issue)
    assert before == ("Done", "CLOSED", "COMPLETED"), before

    status, said = _copy(path, capsys)

    assert status == tickets.SOUND, said
    assert _card(issue) == before


def test_a_withdrawal_onto_an_item_at_a_status_no_ticket_carries_writes_nothing(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    """A person moved the item to an option the store places at no ticket status: a withdrawal,
    which reads the item's status to know it may close it, writes nothing."""
    path, issue = _filed(loopback, "moved-to-an-unmapped-option", capsys)
    # A person's move on github.com, to an option the `followups` mapping names for nothing.
    _row(issue).status = remote.NEEDS_ATTENTION.name
    before = _card(issue)
    _status_written(path, tickets.Status.WITHDRAWN.value)
    writes = len(remote._GitHubFixture.requests)

    status, said = _copy(path, capsys)

    assert status == tickets.UNPLACED, said
    assert "at category 'unknown', which no ticket carries" in said, said
    assert _card(issue) == before
    assert not [
        request for request in remote._GitHubFixture.requests[writes:] if request.is_mutation
    ], "a refused copy wrote to the board"


def _operations(since: int) -> Counter[str]:
    """The loopback's requests from ``since`` on, counted by the document each sent."""
    return Counter(str(request.operation) for request in remote._GitHubFixture.requests[since:])


def test_a_copy_reads_its_item_only_through_its_locate_and_the_stores_verification(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    """A copy straight after its `board-status` and one long after it read alike: its `task
    locate` follows the store's link by id, and the store's `--target-task` verification reads
    the item once more, and no status of the item is read for the copy to write."""
    path, issue = _filed(loopback, "re-copied-after-its-board-status", capsys)
    _decided(path, capsys)
    paired = len(remote._GitHubFixture.requests)
    assert _copy(path, capsys)[0] == tickets.SOUND
    assert _operations(paired)["issue"] == 2, _operations(paired)

    unpaired = len(remote._GitHubFixture.requests)
    assert _copy(path, capsys)[0] == tickets.SOUND
    assert _operations(unpaired)["issue"] == 2, _operations(unpaired)
    assert _card(issue)[:2] == ("Proposal", "OPEN")


@pytest.mark.parametrize(
    ("moved_to", "shown", "withdraw"),
    [
        (tickets.Status.ACCEPTED, "Todo", True),
        (tickets.Status.DEFERRED, "Deferred", True),
        (tickets.Status.ACCEPTED, "Todo", False),
        (tickets.Status.DEFERRED, "Deferred", False),
    ],
    ids=["todo-withdrawal", "deferred-withdrawal", "todo", "deferred"],
)
def test_a_copy_after_a_person_moved_the_item_keeps_what_they_chose(
    loopback: Loopback,
    capsys: pytest.CaptureFixture[str],
    moved_to: tickets.Status,
    shown: str,
    withdraw: bool,
) -> None:
    """`board-status` decides at `Proposal`; a person then moves the item; the copy that follows
    keeps the person's status under `preserve-destination`, and a withdrawal reads the item's
    live status and refuses, writing nothing: the card stays as the person left it either way."""
    path, issue = _filed(loopback, f"moved-after-{moved_to.value}-{withdraw}".lower(), capsys)
    decided = _decided(path, capsys, withdraw=withdraw)
    assert decided == (tickets.Status.WITHDRAWN if withdraw else tickets.Status.PROPOSED).value
    loopback.store("task", "status", "set", issue, moved_to.value)
    before = _card(issue)
    assert before == (shown, "OPEN", None), before
    reads = len(remote._GitHubFixture.requests)

    status, said = _copy(path, capsys)

    if withdraw:
        assert status == tickets.PROTECTED, said
        # Its `task locate` follows the store's link by id, then it reads the live status once.
        assert _operations(reads)["issue"] == 2, "the copy did not read the item's live status"
        assert not [
            request for request in remote._GitHubFixture.requests[reads:] if request.is_mutation
        ], "a refused copy wrote to the board"
    else:
        assert status == tickets.SOUND, said
    assert _card(issue) == before


def _closed_by_raw_write(issue: str) -> remote.RestRequest:
    """Close ``issue`` as not planned the way an agent holding the board credential could:
    one `PATCH` to GitHub's REST issue endpoint, which writes the issue and no board field.
    The request the loopback logged for it."""
    return remote.closed_through_rest(_row(issue), os.environ, "not_planned")


def test_check_dispositions_accepts_items_in_line_and_refuses_a_raw_close_naming_its_status(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    """The run's proposal and its withdrawal are in line; then the proposal's issue is closed
    as not planned with the board credential, no Status written beside it, and the check
    refuses the run naming the item and the status the board holds it at."""
    _, proposal = _filed(loopback, "a-proposal-left-open", capsys)
    withdrawn = _written(loopback, "a-duplicate-withdrawn")
    _decided(withdrawn, capsys)
    assert _copy(withdrawn, capsys)[0] == tickets.SOUND
    _decided(withdrawn, capsys, withdraw=True)
    assert _copy(withdrawn, capsys)[0] == tickets.SOUND
    withdrawn_item = f"{BOARD}:{tickets.read_ticket(withdrawn, pending=True).link(BOARD)}"
    assert _card(withdrawn_item) == ("Cancelled", "CLOSED", "NOT_PLANNED")
    _accounted(loopback, ["a-proposal-left-open", "a-duplicate-withdrawn"])

    status, said = _checked(loopback, capsys)
    assert status == tickets.SOUND, said

    raw = _closed_by_raw_write(proposal)
    assert raw.authorization == "Bearer fixture-token", raw
    assert raw.body == {"state": "closed", "state_reason": "not_planned"}, raw
    before = _card(proposal)
    assert before == ("Proposal", "CLOSED", "NOT_PLANNED"), before

    status, said = _checked(loopback, capsys)

    assert status == tickets.UNSOUND, said
    assert f"{proposal}, which this run created for the ticket a-proposal-left-open, is at " in said
    assert "`Cancelled` (category `cancelled`), not `Proposal`" in said, said
    assert withdrawn_item not in said, said
    assert _card(proposal) == before, "the check wrote to the board"
    assert _card(proposal).status == "Proposal", "the card's own Status option moved"


def test_check_dispositions_refuses_a_withdrawn_ticket_whose_item_was_never_closed(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    path, issue = _filed(loopback, "withdrawn-but-never-copied", capsys)
    _accounted(loopback, ["withdrawn-but-never-copied"])
    _decided(path, capsys, withdraw=True)
    before = _card(issue)

    status, said = _checked(loopback, capsys)

    assert status == tickets.UNSOUND, said
    assert (
        f"{issue}, the item of the ticket withdrawn-but-never-copied this run withdrew, is at "
        "`Proposal` (category `backlog`), not `Cancelled`"
    ) in said, said
    assert _card(issue) == before == ("Proposal", "OPEN", None)

    assert _copy(path, capsys)[0] == tickets.SOUND
    status, said = _checked(loopback, capsys)
    assert status == tickets.SOUND, said


def test_check_dispositions_refuses_a_proposal_the_store_cli_moved_without_board_status(
    loopback: Loopback, capsys: pytest.CaptureFixture[str]
) -> None:
    """The run's own proposal moved to `Todo` by the plan-store CLI's own status write, with no
    `board-status` deciding it: the check refuses the run naming the item and the status it
    holds, where the ticket still carries `backlog`."""
    _, issue = _filed(loopback, "moved-by-the-store-cli", capsys)
    _accounted(loopback, ["moved-by-the-store-cli"])
    status, said = _checked(loopback, capsys)
    assert status == tickets.SOUND, said

    loopback.store("task", "status", "set", issue, tickets.Status.ACCEPTED.value)
    before = _card(issue)
    assert before == ("Todo", "OPEN", None), before

    status, said = _checked(loopback, capsys)

    assert status == tickets.UNSOUND, said
    assert (
        f"{issue}, which this run created for the ticket moved-by-the-store-cli, is at `Todo` "
        "(category `todo`), not `Proposal`, and its ticket carries `backlog`"
    ) in said, said
    assert _card(issue) == before, "the check wrote to the board"
