"""A follow-up ticket's card is the one its record names, written by one guarded store copy.

Every journey drives this checkout's real `orchestrator.follow_up_tickets` commands and the
provisioned plan-store CLI against the loopback `followups` board (`tests/github_board.py`),
and asserts on the board's cards, the ticket's record and the run's receipts afterwards.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import boundary_home
import pytest
from github_board import (
    BOARD,
    CANCELLED,
    CONFIGURED_REPOSITORY,
    PRIVATE_STORE_REPOSITORY,
    PROPOSED_CAUSE,
    PROPOSED_RUN,
    SIBLING_REPOSITORY,
    _decided_status,
    _follow_up_step,
    _follow_up_ticket,
    _followups_command,
    _followups_environment,
    _GitHubFixture,
    _GraphQLRequest,
    _Issue,
    _Operation,
    _seeded_item,
    _serving_followups,
    _written_ticket,
)
from published_tools import ONETASKGRAPH_BIN

from orchestrator import follow_up_tickets

FOLLOWUPS = follow_up_tickets.BOARD
#: The ticket every journey here files, as the store addresses it.
PROPOSED_ID = follow_up_tickets.qualified_id(PROPOSED_RUN, PROPOSED_CAUSE)


@pytest.fixture(autouse=True)
def _boundary_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the fixture repositories public, as the host journeys do."""
    boundary_home.answering(monkeypatch, public=boundary_home.FIXTURE_REPOSITORIES)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] These journeys run in the
# `plan-tooling-follow-ups` project's `test` tier, the dedicated tier for the follow-up commands
# over the installed store; its workspace input covers the commands, the store configuration
# and the loopback board they exercise, and no narrower project holds that board fixture.


def _receipts(root: Path) -> list[dict[str, object]]:
    """The run's receipts, one object per line, as `copy`, `comment` and `re-estimate` wrote."""
    path = follow_up_tickets.receipts_path(root, PROPOSED_RUN)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _shown(environment: dict[str, str], path: Path) -> dict[str, object]:
    """The ticket at ``path`` as the store reads it back, under the follow-ups environment."""
    run, cause = follow_up_tickets.located_path(path)
    shown = _followups_command(
        environment,
        [str(ONETASKGRAPH_BIN), "task", "show", follow_up_tickets.qualified_id(run, cause)]
        + ["--json"],
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    item: dict[str, object] = json.loads(shown.stdout)["items"][0]["item"]
    return item


def _ticket(environment: dict[str, str], path: Path) -> follow_up_tickets.Ticket:
    """The ticket at ``path``, read through the store as every follow-up command reads it."""
    run, cause = follow_up_tickets.located_path(path)
    return follow_up_tickets.from_store_item(
        _shown(environment, path), run=run, root_cause=cause, pending=True
    )


def _carrying(origin: str) -> list[_Issue]:
    """Every card on the board carrying ``origin`` as the store's origin."""
    return [issue for issue in BOARD.issues if issue.origin == origin]


def _card(issue: _Issue) -> str:
    """``issue`` as the store qualifies it on `followups`."""
    return f"{FOLLOWUPS}:{issue.content_id}"


def _copy(environment: dict[str, str], path: Path) -> tuple[int, str, str]:
    copied = _follow_up_step(environment, "copy", "--board", FOLLOWUPS, str(path))
    return copied.returncode, copied.stdout, copied.stderr


def test_a_copy_whose_create_answer_was_lost_is_retried_onto_the_one_card_it_created(
    tmp_path: Path,
) -> None:
    """The board ran `createIssue` and the answer never arrived; the copy, run again, finishes it.

    The store leaves its intent on the ticket and gives up its lease, so the retry finds the
    card its first attempt created by the create key and finishes that card rather than
    creating a second; the record then names it and holds no intent.
    """
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        _GitHubFixture.dropped_operation = (_Operation.CREATE_ISSUE, "after")
        status, _out, said = _copy(environment, path)
        assert status != follow_up_tickets.SOUND, said
        assert len(BOARD.created) == 1, "the board ran the create"
        interrupted = _ticket(environment, path)
        assert interrupted.creating is not None, "the store kept its intent on the ticket"
        assert interrupted.links == ()

        status, out, said = _copy(environment, path)

    assert status == follow_up_tickets.SOUND, said
    (card,) = _carrying(PROPOSED_ID)
    assert BOARD.created == [card], "the retry created no second card"
    assert json.loads(out)["destination"] == _card(card)
    finished = _ticket(environment, path)
    assert finished.link(FOLLOWUPS) == card.content_id
    assert finished.creating is None
    assert follow_up_tickets.CREATING_KEY not in path.read_text(encoding="utf-8")
    last = _receipts(root)[-1]
    assert (last["command"], last["ticket"], last["card"]) == ("copy", PROPOSED_ID, _card(card))


def test_a_ticket_naming_no_card_updates_the_one_card_carrying_its_origin(
    tmp_path: Path,
) -> None:
    """An old record with no link onto a board already holding its card: updated, none created.

    The shape of the records filed before the store kept its link: the card carries the
    ticket's origin, so the store's own origin rule finds it and the record then names it.
    """
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        held = _seeded_item(
            0,
            run=PROPOSED_RUN,
            cause=path.stem,
            status=follow_up_tickets.Status.FINISHED,
            origin=PROPOSED_ID,
            origin_in_body=True,
        )
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        _GitHubFixture.requests.clear()
        status, out, said = _copy(environment, path)
        created = _GitHubFixture.requests

    assert status == follow_up_tickets.SOUND, said
    assert json.loads(out) == {"action": "updated", "destination": _card(held)}
    assert not [one for one in created if one.operation is _Operation.CREATE_ISSUE]
    assert _carrying(PROPOSED_ID) == [held]
    assert _ticket(environment, path).link(FOLLOWUPS) == held.content_id
    assert _receipts(root)[-1]["card"] == _card(held)


def _legacy(environment: dict[str, str], path: Path, board_item: str) -> bytes:
    """Rewrite the ticket at ``path`` as a schema-12 record bound to ``board_item``; its bytes."""
    ticket = _ticket(environment, path)
    legacy = replace(
        ticket,
        retired=((follow_up_tickets.BINDING_FIELD, board_item),),
        board_estimate=None,
        links=(),
    )
    path.write_text(follow_up_tickets.render(legacy), encoding="utf-8")
    return path.read_bytes()


def test_a_record_bound_by_board_item_is_copied_onto_that_card_and_brought_forward(
    tmp_path: Path,
) -> None:
    """A record from before schema 13 names its card by `board_item`; the copy lands there.

    No card is created, and the record is rewritten at the current schema with the store's own
    link to that card in place of the key.
    """
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        held = _seeded_item(
            0,
            run=PROPOSED_RUN,
            cause=path.stem,
            status=follow_up_tickets.Status.PROPOSED,
            origin=PROPOSED_ID,
            origin_in_body=True,
        )
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        _legacy(environment, path, held.content_id)
        _GitHubFixture.requests.clear()
        status, out, said = _copy(environment, path)
        sent = list(_GitHubFixture.requests)

    assert status == follow_up_tickets.SOUND, said
    assert json.loads(out)["destination"] == _card(held)
    assert not [one for one in sent if one.operation is _Operation.CREATE_ISSUE]
    assert _carrying(PROPOSED_ID) == [held]
    text = path.read_text(encoding="utf-8")
    assert follow_up_tickets.BINDING_FIELD not in text
    migrated = _ticket(environment, path)
    assert migrated.link(FOLLOWUPS) == held.content_id
    assert follow_up_tickets.record(migrated)["schema"] == follow_up_tickets.SCHEMA
    assert _receipts(root)[-1]["card"] == _card(held)


@pytest.mark.parametrize(
    ("gone", "kind"),
    [(True, "target-task-missing"), (False, "target-task-origin")],
    ids=["card-gone", "another-tickets-card"],
)
def test_a_record_bound_to_a_card_that_does_not_verify_is_refused_and_left_as_it_was(
    tmp_path: Path, gone: bool, kind: str
) -> None:
    """A `board_item` naming a card gone, or one carrying another ticket's origin, is refused.

    The store's error kind is reported by name; nothing is created or changed, the record keeps
    its `board_item` byte for byte, and the receipt records the refusal.
    """
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        other = _seeded_item(
            0,
            run="another-run",
            cause="another-cause",
            status=follow_up_tickets.Status.PROPOSED,
            origin="drafts:another-run/tickets/another-cause",
            origin_in_body=True,
        )
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        before = _legacy(environment, path, "I_not_on_the_board" if gone else other.content_id)
        held = (other.title, other.body, other.status, other.state)
        _GitHubFixture.requests.clear()
        status, _out, said = _copy(environment, path)
        sent = list(_GitHubFixture.requests)

    assert status == follow_up_tickets.MISBOUND, said
    assert kind in said
    assert path.read_bytes() == before
    assert not [one for one in sent if one.is_mutation]
    assert (other.title, other.body, other.status, other.state) == held
    assert _carrying(PROPOSED_ID) == []
    last = _receipts(root)[-1]
    outcome = last["outcome"]
    assert isinstance(outcome, dict)
    assert outcome["action"] == "failed"
    assert outcome["error"]["kind"] == kind


@pytest.mark.parametrize(
    "held",
    [
        follow_up_tickets.Status.ACCEPTED,
        follow_up_tickets.Status.UNDER_WAY,
        follow_up_tickets.Status.FINISHED,
        follow_up_tickets.Status.DEFERRED,
    ],
    ids=lambda one: one.value,
)
def test_a_withdrawal_over_a_card_a_person_decided_on_writes_nothing(
    tmp_path: Path, held: follow_up_tickets.Status
) -> None:
    """The store's status guard refuses the withdrawal: the card keeps its status."""
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        status, out, said = _copy(environment, path)
        assert status == follow_up_tickets.SOUND, said
        (card,) = _carrying(PROPOSED_ID)
        _person(environment, "status", "set", _card(card), held.value)
        assert _decided_status(environment, path, "--withdraw") == (
            follow_up_tickets.Status.WITHDRAWN
        )
        before = (card.status, card.state)
        _GitHubFixture.requests.clear()
        status, _out, said = _copy(environment, path)
        sent = list(_GitHubFixture.requests)

    assert status == follow_up_tickets.PROTECTED, said
    assert "never withdraws it" in said
    assert (card.status, card.state) == before
    assert not [one for one in sent if one.is_mutation]
    outcome = _receipts(root)[-1]["outcome"]
    assert isinstance(outcome, dict)
    assert outcome["error"]["kind"] == "destination-status"


def _status_named(status: follow_up_tickets.Status) -> str:
    """The board option name a status the seeded states do not name is held at."""
    return follow_up_tickets.board_option(status)


def test_a_withdrawal_of_a_proposal_closes_its_card_as_not_planned(tmp_path: Path) -> None:
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        assert _copy(environment, path)[0] == follow_up_tickets.SOUND
        (card,) = _carrying(PROPOSED_ID)
        assert _decided_status(environment, path, "--withdraw") == (
            follow_up_tickets.Status.WITHDRAWN
        )
        status, out, said = _copy(environment, path)

    assert status == follow_up_tickets.SOUND, said
    assert json.loads(out) == {"action": "updated", "destination": _card(card)}
    assert (card.status, card.state, card.state_reason) == (CANCELLED.name, "CLOSED", "NOT_PLANNED")
    outcome = _receipts(root)[-1]["outcome"]
    assert isinstance(outcome, dict)
    assert outcome["result"]["status"]["category"] == follow_up_tickets.Status.WITHDRAWN


def test_a_copy_another_attempt_holds_the_create_of_is_refused_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """Two copies of one ticket race: the second, run while the first's create is at the board,
    meets the first's live lease and is refused as `create-in-progress`, writing nothing, while
    the first files the one card."""
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    second: list[tuple[int, str]] = []

    def racing() -> None:
        ran = _follow_up_step(environment, "copy", "--board", FOLLOWUPS, str(path))
        second.append((ran.returncode, ran.stdout + ran.stderr))

    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        racer = threading.Thread(target=racing)

        def while_creating() -> None:
            racer.start()
            racer.join(timeout=60)

        _GitHubFixture.before_operation[_Operation.CREATE_ISSUE] = while_creating
        status, out, said = _copy(environment, path)
        racer.join(timeout=120)

    assert status == follow_up_tickets.SOUND, said
    (card,) = _carrying(PROPOSED_ID)
    assert BOARD.created == [card] and json.loads(out)["destination"] == _card(card)
    ((raced, refused),) = second
    assert raced == follow_up_tickets.CREATE_IN_PROGRESS, refused
    assert "create-in-progress" in refused and "once the lease it names has ended" in refused
    kinds = [
        (one["outcome"].get("error") or {}).get("kind")
        if isinstance(one["outcome"], dict)
        else None
        for one in _receipts(root)
    ]
    assert "create-in-progress" in kinds, kinds


def test_a_create_the_board_completes_late_beside_its_re_create_is_refused_as_duplicated(
    tmp_path: Path,
) -> None:
    """The board commits a held create after the retry's own: two cards carry one create key.

    The store links neither, keeps its intent on the ticket, and names both cards; the copy
    claims no filing, reports the key and both cards, receipts the refusal, and the run's
    closeout check refuses an account calling that ticket filed.
    """
    environment, root = _followups_environment(tmp_path)
    environment["ONETASKGRAPH_SOURCES__FOLLOWUPS__CREATE_QUIESCENCE_SECONDS"] = "1"
    ticket = _follow_up_ticket(SIBLING_REPOSITORY)
    path = _written_ticket(root, ticket)
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        _GitHubFixture.held_create = True
        first, _out, said = _copy(environment, path)
        assert first != follow_up_tickets.SOUND, said
        assert BOARD.created == [], "the board held the first create"
        status, out, said = _copy(environment, path)

    assert status == follow_up_tickets.CREATE_DUPLICATED, said
    assert out == ""
    assert len(BOARD.created) == 2, "the held create completed beside the re-create"
    for card in BOARD.created:
        assert _card(card) in said, (card, said)
    duplicated = _ticket(environment, path)
    assert duplicated.links == ()
    assert duplicated.creating is not None
    (key,) = [one["key"] for one in json.loads(duplicated.creating).values()]
    assert str(key) in said
    outcome = _receipts(root)[-1]["outcome"]
    assert isinstance(outcome, dict)
    assert outcome["error"]["kind"] == "create-duplicated"
    account = follow_up_tickets.open_dispositions(root, PROPOSED_RUN)
    account.write_text(
        json.dumps(
            {
                "schema": follow_up_tickets.DISPOSITIONS_SCHEMA,
                "run": PROPOSED_RUN,
                "drafts": list(ticket.drafts),
                "dispositions": [
                    {
                        "draft": ticket.drafts[0],
                        "disposition": "filed",
                        "root_causes": [ticket.root_cause],
                        "detail": "Copied onto its card.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    checked = _follow_up_step(
        environment, "check-dispositions", "--root", str(root), "--board", FOLLOWUPS, PROPOSED_RUN
    )
    assert checked.returncode == follow_up_tickets.UNSOUND, checked.stdout + checked.stderr


def _person(environment: dict[str, str], *verb: str) -> None:
    """A person's own edit of a card, made through the store's verb as the board shows it."""
    done = _followups_command(environment, [str(ONETASKGRAPH_BIN), "task", *verb, "--json"])
    assert done.returncode == 0, done.stdout + done.stderr


def _edited(environment: dict[str, str], path: Path, words: str) -> None:
    """The agent's edit of its ticket's evidence, in place, keeping what the store wrote."""
    held = _ticket(environment, path)
    body = held.body.replace("(Evidence).", f"(Evidence). {words}", 1)
    assert body != held.body, held.body
    path.write_text(follow_up_tickets.render(replace(held, body=body)), encoding="utf-8")


def _priority_of(card: _Issue) -> str:
    """The priority word the store reads a card's `Priority` field as."""
    return "none" if card.priority is None else card.priority.lower()


def test_a_persons_priority_survives_a_copy_run_without_board_status_first(
    tmp_path: Path,
) -> None:
    """A person sets the card's priority; the ticket's content changes and `copy` runs alone.

    The copy's priority guard names the estimate the card last received, which the card no
    longer holds, so the store writes the new content and keeps the person's priority.
    """
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        assert _copy(environment, path)[0] == follow_up_tickets.SOUND
        (card,) = _carrying(PROPOSED_ID)
        assert _priority_of(card) == "medium"
        _person(environment, "priority", "set", _card(card), "low")
        _edited(environment, path, "A later run saw it again.")
        status, out, said = _copy(environment, path)

    assert status == follow_up_tickets.SOUND, said
    assert json.loads(out) == {"action": "updated", "destination": _card(card)}
    assert "A later run saw it again." in card.body
    assert _priority_of(card) == "low", "the copy overwrote a priority a person set"
    outcome = _receipts(root)[-1]["outcome"]
    assert isinstance(outcome, dict)
    assert outcome["priority_condition"] == {"held": "low", "met": False}
    assert outcome["result"]["priority"] == "low"


def test_a_card_nobody_changed_follows_a_raised_estimate_across_board_status_then_copy(
    tmp_path: Path,
) -> None:
    """Two other runs' evidence raises the estimate; `board-status` then `copy` carry it on."""
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        assert _copy(environment, path)[0] == follow_up_tickets.SOUND
        (card,) = _carrying(PROPOSED_ID)
        for run in ("a-later-run", "a-third-run"):
            body = tmp_path / f"{run}.md"
            body.write_text(
                follow_up_tickets.render_comment(run, path.stem, "Seen again."), encoding="utf-8"
            )
            posted = _follow_up_step(
                environment,
                *("comment", "--board", FOLLOWUPS, "--run", run, "--body-file", str(body)),
                _card(card),
            )
            assert posted.returncode == 0, posted.stdout + posted.stderr
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        raised = _ticket(environment, path).priority_estimate
        status, _out, said = _copy(environment, path)

    assert raised == follow_up_tickets.Priority.HIGH
    assert status == follow_up_tickets.SOUND, said
    assert _priority_of(card) == "high"
    assert _ticket(environment, path).board_estimate is follow_up_tickets.Priority.HIGH


def test_a_record_of_an_older_schema_on_a_card_holding_none_follows_its_estimate(
    tmp_path: Path,
) -> None:
    """A schema-13 record carries no `board_estimate`, so its copy guards `none`: a card at none
    takes the ticket's estimate, and the record is rewritten at the current schema naming it.

    The copy that follows a raised estimate, with no `board-status` between, guards the estimate
    the first copy recorded, which the untouched card still holds, so the card follows it.
    """
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        held = _seeded_item(
            0,
            run=PROPOSED_RUN,
            cause=path.stem,
            status=follow_up_tickets.Status.PROPOSED,
            origin=PROPOSED_ID,
            origin_in_body=True,
        )
        assert held.priority is None
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        linked = replace(
            _ticket(environment, path),
            links=(
                follow_up_tickets.CopyLink(
                    FOLLOWUPS, follow_up_tickets.QualifiedBoardId(_card(held))
                ),
            ),
        )
        text = follow_up_tickets.render(linked)
        older = text.replace(
            f'"schema": {follow_up_tickets.SCHEMA}',
            f'"schema": {follow_up_tickets.BOARD_ESTIMATE_AT - 1}',
        )
        assert older != text and follow_up_tickets.BOARD_ESTIMATE_FIELD not in older
        path.write_text(older, encoding="utf-8")
        status, out, said = _copy(environment, path)
        assert status == follow_up_tickets.SOUND, said
        assert json.loads(out)["destination"] == _card(held)
        assert _priority_of(held) == "medium"
        migrated = _ticket(environment, path)
        stored = follow_up_tickets.record(migrated)
        # Rewritten at the current schema naming the estimate the copy wrote, the store's link
        # and origin carried through as the store wrote them.
        assert stored["schema"] == follow_up_tickets.SCHEMA
        assert stored[follow_up_tickets.BOARD_ESTIMATE_FIELD] == "medium"
        assert migrated.links == linked.links
        assert migrated.origin == linked.origin
        assert migrated.creating is None
        # The agent now judges that the root cause fires consistently, which raises the
        # estimate one level; it writes the facts, the estimate line and the estimate itself.
        with_workaround = follow_up_tickets.stated_with_workaround(migrated.body)
        assert with_workaround is not None
        consistent = follow_up_tickets.Frequency.CONSISTENT
        level = follow_up_tickets.estimate(with_workaround, consistent, 1)
        assert level is follow_up_tickets.Priority.HIGH
        raised = replace(
            migrated,
            frequency=consistent,
            priority_estimate=level,
            priority=level,
            body=follow_up_tickets.with_estimate_line(
                migrated.body, follow_up_tickets.estimate_line(with_workaround, consistent, 1)
            ),
        )
        path.write_text(follow_up_tickets.render(raised), encoding="utf-8")
        status, out, said = _copy(environment, path)

    assert status == follow_up_tickets.SOUND, said
    assert json.loads(out)["destination"] == _card(held)
    assert _priority_of(held) == "high", "an untouched card did not follow the raised estimate"
    outcome = _receipts(root)[-1]["outcome"]
    assert isinstance(outcome, dict)
    assert outcome["priority_condition"] == {"held": "medium", "met": True}
    assert outcome["result"]["priority"] == "high"
    after = follow_up_tickets.record(_ticket(environment, path))
    assert after["schema"] == follow_up_tickets.SCHEMA
    assert after[follow_up_tickets.BOARD_ESTIMATE_FIELD] == "high"


#: The category the store reads each `followups` Status option as.
CATEGORY_OF = {
    "Proposal": follow_up_tickets.Status.PROPOSED.value,
    "Todo": follow_up_tickets.Status.ACCEPTED.value,
    "Deferred": follow_up_tickets.Status.DEFERRED.value,
    "Queued": follow_up_tickets.Status.QUEUED.value,
    "In Progress": follow_up_tickets.Status.UNDER_WAY.value,
    "Done": follow_up_tickets.Status.FINISHED.value,
    "Cancelled": follow_up_tickets.Status.WITHDRAWN.value,
}


def _account(root: Path, filed: dict[str, str]) -> None:
    """The run's account: one draft, filed under each root cause in ``filed`` with its detail."""
    draft = _follow_up_ticket(SIBLING_REPOSITORY).drafts[0]
    path = follow_up_tickets.dispositions_path(root, PROPOSED_RUN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": follow_up_tickets.DISPOSITIONS_SCHEMA,
                "run": PROPOSED_RUN,
                "drafts": [draft],
                "dispositions": [
                    {
                        "draft": draft,
                        "disposition": "filed",
                        "root_causes": sorted(filed),
                        "detail": " ".join(filed[cause] for cause in sorted(filed)),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _checked(environment: dict[str, str], root: Path) -> tuple[int, str]:
    """`check-dispositions --board`, holding that it sends the board nothing: its status, said."""
    _GitHubFixture.requests.clear()
    checked = _follow_up_step(
        environment, "check-dispositions", "--root", str(root), "--board", FOLLOWUPS, PROPOSED_RUN
    )
    assert _GitHubFixture.requests == [], "the closeout check read the board"
    return checked.returncode, " ".join((checked.stdout + checked.stderr).split())


def _as_the_board_holds(root: Path, ticket: str, card: _Issue) -> None:
    """The ticket's last copy receipt says what the board then holds for its card: a copy
    skipped as unchanged by the landed copy whose receipt line it names."""
    receipts = _receipts(root)
    last = [one for one in receipts if one["ticket"] == ticket][-1]
    outcome = last["outcome"]
    assert isinstance(outcome, dict)
    if outcome["action"] == follow_up_tickets.SKIPPED_UNCHANGED:
        since = outcome["unchanged_since"]
        assert isinstance(since, int), outcome
        outcome = receipts[since - 1]["outcome"]
        assert isinstance(outcome, dict)
    result = outcome["result"] if outcome.get("error") is None else outcome["held"]
    assert isinstance(result, dict), outcome
    assert result["status"]["category"] == CATEGORY_OF[card.status], (result, card.status)
    assert result["status"]["name"] == card.status
    assert (result["priority"] or "none") == _priority_of(card), (result, card.priority)


def test_the_closeout_check_accepts_every_write_its_receipts_show_and_reads_no_board(
    tmp_path: Path,
) -> None:
    """A create, an update, a re-copy skipped as unchanged, a re-copy whose status the store
    preserved, one whose priority the guard kept, a withdrawal, and a withdrawal refused as
    `destination-status`: after each, `check-dispositions --board` accepts the account from the
    receipts alone, and each landed receipt's `result` is what the board then holds for that
    card — a skip's, the landed copy's it names."""
    environment, root = _followups_environment(tmp_path)
    kept = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY, cause="kept-cause"))
    withdrawn = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY, cause="gone-cause"))
    protected = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY, cause="held-cause"))
    ids = {
        path: follow_up_tickets.qualified_id(PROPOSED_RUN, path.stem)
        for path in (kept, withdrawn, protected)
    }
    filed: dict[str, str] = {}
    with _serving_followups(environment, fields=True):

        def written(path: Path, *withdraw: str) -> tuple[int, str]:
            _decided_status(environment, path, *withdraw)
            status, out, said = _copy(environment, path)
            filed[path.stem] = "Copied onto its card."
            _account(root, filed)
            return status, out or said

        def accepted(path: Path) -> None:
            assert _checked(environment, root)[0] == follow_up_tickets.SOUND
            (card,) = _carrying(ids[path])
            _as_the_board_holds(root, ids[path], card)

        status, out = written(kept)
        assert (status, json.loads(out)["action"]) == (follow_up_tickets.SOUND, "created")
        accepted(kept)
        _edited(environment, kept, "Seen once more.")
        status, out = written(kept)
        assert json.loads(out)["action"] == "updated"
        accepted(kept)
        status, out = written(kept)
        assert json.loads(out)["action"] == follow_up_tickets.SKIPPED_UNCHANGED
        accepted(kept)
        (card,) = _carrying(ids[kept])
        _person(environment, "status", "set", _card(card), "todo")
        _edited(environment, kept, "And again.")
        status, out = written(kept)
        assert (json.loads(out)["action"], card.status) == ("updated", "Todo")
        accepted(kept)
        _person(environment, "priority", "set", _card(card), "low")
        _edited(environment, kept, "And a fourth time.")
        status, out = written(kept)
        assert (json.loads(out)["action"], _priority_of(card)) == ("updated", "low")
        accepted(kept)

        assert written(withdrawn)[0] == follow_up_tickets.SOUND
        assert written(withdrawn, "--withdraw")[0] == follow_up_tickets.SOUND
        accepted(withdrawn)

        assert written(protected)[0] == follow_up_tickets.SOUND
        (held,) = _carrying(ids[protected])
        _person(environment, "status", "set", _card(held), "todo")
        assert written(protected, "--withdraw")[0] == follow_up_tickets.PROTECTED
        accepted(protected)


def _rewritten(root: Path, lines: list[dict[str, object]], *extra: str) -> None:
    """Replace the run's receipts with ``lines``, then any raw ``extra`` lines."""
    path = follow_up_tickets.receipts_path(root, PROPOSED_RUN)
    text = "".join(json.dumps(one) + "\n" for one in lines) + "".join(f"{one}\n" for one in extra)
    path.write_text(text, encoding="utf-8")


def test_the_closeout_check_refuses_an_account_its_receipts_contradict(tmp_path: Path) -> None:
    """Each contradiction between the account and the receipts of a real run is refused.

    The run copies one ticket, withdraws another, and posts evidence on another run's card with
    `comment` and `re-estimate`; the check accepts those receipts as written. Each case then
    removes a receipt, or alters a copy of one, and the check refuses naming what it found.
    """
    environment, root = _followups_environment(tmp_path)
    copied = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY, cause="copied-cause"))
    gone = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY, cause="gone-cause"))
    evidence = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY, cause="shared-cause"))
    with _serving_followups(environment, fields=True):
        carrier = _seeded_item(
            0,
            run="earlier-run",
            cause="shared-cause",
            status=follow_up_tickets.Status.PROPOSED,
            origin="drafts:earlier-run/tickets/shared-cause",
            origin_in_body=True,
        )
        for path in (copied, gone, evidence):
            _decided_status(environment, path)
        assert _copy(environment, copied)[0] == follow_up_tickets.SOUND
        assert _copy(environment, gone)[0] == follow_up_tickets.SOUND
        _decided_status(environment, gone, "--withdraw")
        assert _copy(environment, gone)[0] == follow_up_tickets.SOUND
        body = tmp_path / "evidence.md"
        body.write_text(
            follow_up_tickets.render_comment(PROPOSED_RUN, "shared-cause", "Seen at the basis."),
            encoding="utf-8",
        )
        for step in (
            ("comment", "--board", FOLLOWUPS, "--run", PROPOSED_RUN, "--body-file", str(body)),
            ("re-estimate", "--board", FOLLOWUPS, "--run", PROPOSED_RUN),
        ):
            done = _follow_up_step(environment, *step, _card(carrier))
            assert done.returncode == 0, done.stdout + done.stderr
        _account(
            root,
            {
                "copied-cause": "Copied onto its card.",
                "gone-cause": "Withdrawn.",
                "shared-cause": f"Posted evidence on {_card(carrier)}.",
            },
        )
        real = _receipts(root)
        assert _checked(environment, root)[0] == follow_up_tickets.SOUND
        copied_id = follow_up_tickets.qualified_id(PROPOSED_RUN, "copied-cause")
        gone_id = follow_up_tickets.qualified_id(PROPOSED_RUN, "gone-cause")

        def at(command: str, ticket: str | None = None) -> int:
            return max(
                index
                for index, one in enumerate(real)
                if one["command"] == command and (ticket is None or one["ticket"] == ticket)
            )

        def without(index: int) -> list[dict[str, object]]:
            return [one for position, one in enumerate(real) if position != index]

        def altered(index: int, **outcome: object) -> list[dict[str, object]]:
            changed = json.loads(json.dumps(real))
            line = changed[index]
            line["outcome"] = {**line["outcome"], **outcome}
            return changed

        copy_at, gone_at = at("copy", copied_id), at("copy", gone_id)
        created = real[copy_at]["outcome"]
        assert isinstance(created, dict)
        estimated = real[at("re-estimate")]["outcome"]
        assert isinstance(estimated, dict)
        foreign = dict(real[copy_at], run="another-run")
        unheld = dict(real[copy_at], ticket=follow_up_tickets.qualified_id(PROPOSED_RUN, "nobody"))
        elsewhere = json.loads(json.dumps(real))
        elsewhere[copy_at]["card"] = f"{FOLLOWUPS}:I_another_card"
        cases = {
            "a copy with no receipt": (
                [one for one in real if one["ticket"] != copied_id],
                [],
                "hold no copy of it",
            ),
            "an evidence comment with no receipt": (
                without(at("comment")),
                [],
                "no evidence comment",
            ),
            "a receipt of another run": ([*real, foreign], [], "names the run 'another-run'"),
            "a receipt for a ticket the account does not hold": (
                [*real, unheld],
                [],
                "which this run's account does not hold",
            ),
            "a receipt naming another card": (elsewhere, [], "names the card"),
            "a refusal where the account says filed": (
                altered(
                    copy_at,
                    action="failed",
                    error={"kind": "refused", "message": "no"},
                    digest=None,
                ),
                [],
                "records the store's refusal `refused`",
            ),
            "a create off Proposal": (
                altered(
                    copy_at,
                    result={**created["result"], "status": {"category": "todo", "name": "Todo"}},
                ),
                [],
                "created the card at category 'todo'",
            ),
            "a withdrawal off Cancelled": (
                altered(
                    gone_at,
                    result={
                        **real[gone_at]["outcome"]["result"],
                        "status": {"category": "backlog", "name": "Proposal"},
                    },
                ),
                [],
                "left the card at category 'backlog'",
            ),
            "a priority the guard does not give": (
                altered(copy_at, result={**created["result"], "priority": "urgent"}),
                [],
                "left the card at priority `urgent`",
            ),
            "a malformed line": (real, ["not a receipt"], "is not JSON"),
            "a schema that is no integer": (
                real,
                [json.dumps(dict(real[copy_at], schema=True))],
                f"is not a receipt of schema {follow_up_tickets.RECEIPT_SCHEMA}",
            ),
            "an outcome its command's model does not read": (
                altered(copy_at, result={**created["result"], "priority": 3}),
                [],
                "holds no copy outcome this reads",
            ),
            "evidence with no re-estimate after it": (
                without(at("re-estimate")),
                [],
                "no `re-estimate` receipt of that card follows it",
            ),
            "a re-estimate writing another estimate than its count gives": (
                altered(at("re-estimate"), priority_estimate="urgent"),
                [],
                "the priority rule gives",
            ),
            "a carrier stated at another count than its re-estimate wrote": (
                altered(at("re-estimate"), occurrences=estimated["occurrences"] + 2),
                [],
                "wrote the estimate line",
            ),
        }
        escaping = _follow_up_step(
            environment,
            *("re-estimate", "--board", FOLLOWUPS, "--run", "../elsewhere", _card(carrier)),
        )
        assert escaping.returncode == follow_up_tickets.UNRUNNABLE, escaping.stderr
        assert "is not a run id" in escaping.stderr
        assert not (root.parent / "elsewhere.jsonl").exists()
        refused = {}
        for case, (lines, extra, said) in cases.items():
            _rewritten(root, lines, *extra)
            status, message = _checked(environment, root)
            refused[case] = (status, said in message, message)
        _rewritten(root, real)
        assert _checked(environment, root)[0] == follow_up_tickets.SOUND

    wrong = {
        case: message
        for case, (status, named, message) in refused.items()
        if (status, named) != (follow_up_tickets.UNSOUND, True)
    }
    assert wrong == {}, wrong


@pytest.mark.parametrize(
    ("body", "said"),
    [
        (
            lambda run: follow_up_tickets.render_comment(
                run, "shared-cause", "## Impact\n\nAll."
            ).replace("All.", "All."),
            "carries the ticket heading `## Impact`",
        ),
        (
            lambda run: follow_up_tickets.comment_opening(run) + "\n\nSeen again.\n",
            "its last line is",
        ),
        (
            lambda run: "Seen again.\n\n" + follow_up_tickets.comment_marker(run, "shared-cause"),
            "its opening line is",
        ),
    ],
    ids=["a-ticket-heading", "no-marker-last", "another-opening"],
)
def test_comment_refuses_a_body_not_this_runs_shape_and_posts_nothing(
    tmp_path: Path, body: object, said: str
) -> None:
    environment, root = _followups_environment(tmp_path)
    with _serving_followups(environment, fields=True):
        carrier = _seeded_item(
            0,
            run="earlier-run",
            cause="shared-cause",
            status=follow_up_tickets.Status.PROPOSED,
            origin="drafts:earlier-run/tickets/shared-cause",
            origin_in_body=True,
        )
        written = tmp_path / "comment.md"
        assert callable(body)
        written.write_text(body(PROPOSED_RUN), encoding="utf-8")
        _GitHubFixture.requests.clear()
        posted = _follow_up_step(
            environment,
            *("comment", "--board", FOLLOWUPS, "--run", PROPOSED_RUN, "--body-file", str(written)),
            _card(carrier),
        )
        sent = list(_GitHubFixture.requests)

    assert posted.returncode == follow_up_tickets.UNSOUND, posted.stdout + posted.stderr
    assert said in " ".join(posted.stderr.split()), posted.stderr
    assert sent == [] and carrier.comments == []
    assert _receipts(root) == []


def test_board_status_takes_a_card_from_the_runs_receipt_only_while_it_carries_the_ticket(
    tmp_path: Path,
) -> None:
    """A record naming no card — what a private destination leaves — reads its receipt's card.

    The card this run's own last copy reached is read once by id and taken only while it
    carries the ticket's origin: a receipt naming another ticket's card binds nothing, and the
    ticket then reads as a proposal of one occurrence.
    """
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        assert _copy(environment, path)[0] == follow_up_tickets.SOUND
        (card,) = _carrying(PROPOSED_ID)
        unlinked = replace(_ticket(environment, path), links=())
        path.write_text(follow_up_tickets.render(unlinked), encoding="utf-8")
        _person(environment, "status", "set", _card(card), "todo")
        assert _decided_status(environment, path) == follow_up_tickets.Status.ACCEPTED
        other = _seeded_item(
            0,
            run="another-run",
            cause="another-cause",
            status=follow_up_tickets.Status.ACCEPTED,
            origin="drafts:another-run/tickets/another-cause",
            origin_in_body=True,
        )
        foreign = dict(_receipts(root)[-1], card=_card(other))
        receipts = follow_up_tickets.receipts_path(root, PROPOSED_RUN)
        with receipts.open("a", encoding="utf-8") as appended:
            appended.write(json.dumps(foreign) + "\n")
        _GitHubFixture.requests.clear()
        decided = _decided_status(environment, path)
        read = list(_GitHubFixture.requests)

    assert decided == follow_up_tickets.Status.PROPOSED, "a foreign receipt bound the ticket"
    assert not [one for one in read if one.is_mutation]
    assert _ticket(environment, path).links == ()


def test_a_ticket_a_route_now_sends_elsewhere_still_names_its_card_and_moves_nowhere(
    tmp_path: Path,
) -> None:
    """A card filed on `followups` before a host route sent the ticket's repository elsewhere is
    still the card the record names: `board-status` reads it there, and `copy` names it to the
    store, which refuses to write a card its ticket no longer routes to — reported by name, with
    nothing written to either board, since only a person moves it."""
    environment, root = _followups_environment(tmp_path)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    routed = tmp_path / "routed-followups"
    routed.mkdir()
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        assert _copy(environment, path)[0] == follow_up_tickets.SOUND
        (card,) = _carrying(PROPOSED_ID)
        environment.update(
            {
                "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__PLUGIN": "local-md",
                "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__CONFIG__ROOT": str(routed),
                "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__VISIBILITY": "private",
                "ONETASKGRAPH_SOURCES__FOLLOWUPS__ROUTES__0__REPOSITORIES": (
                    f"github.com/{SIBLING_REPOSITORY}"
                ),
                "ONETASKGRAPH_SOURCES__FOLLOWUPS__ROUTES__0__TO": "routedfollowups",
            }
        )
        _person(environment, "status", "set", _card(card), "todo")
        assert _decided_status(environment, path) == follow_up_tickets.Status.ACCEPTED
        _edited(environment, path, "Seen after the route moved.")
        before = card.body
        _GitHubFixture.requests.clear()
        status, out, said = _copy(environment, path)
        sent = list(_GitHubFixture.requests)

    assert status == follow_up_tickets.MISBOUND, said
    assert "misrouted" in said and out == ""
    assert card.body == before and not [one for one in sent if one.is_mutation]
    assert BOARD.created == [card]
    assert not any(routed.rglob("*.md")), "the routed board received the ticket"
    last = _receipts(root)[-1]
    assert (last["card"], last["outcome"]["error"]["kind"]) == (_card(card), "misrouted")


#: Every document a copy looks a ticket's counterpart up with, by its origin.
ORIGIN_LOOKUPS = (
    _Operation.ORIGIN_LOOKUP,
    _Operation.ORIGIN_LOOKUPS,
    _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS,
)


def _lookups(requests: list[_GraphQLRequest]) -> list[_Operation]:
    """Each origin lookup among ``requests``."""
    return [one.operation for one in requests if one.operation in ORIGIN_LOOKUPS]


def _record_existing(environment: dict[str, str], root: Path) -> dict[str, object]:
    """Run `record-existing-tickets` as `docs/host-setup.md` gives it, naming no root: the
    store's configured `drafts` root, which ``environment`` points at ``root``."""
    recorded = _follow_up_step(environment, "record-existing-tickets")
    assert recorded.returncode == follow_up_tickets.SOUND, recorded.stdout + recorded.stderr
    answer: dict[str, object] = json.loads(recorded.stdout)
    assert answer["list"] == str(follow_up_tickets.existing_tickets_path(root)), answer
    return answer


def test_recording_existing_tickets_keeps_an_unlinked_earlier_ticket_on_its_one_card(
    tmp_path: Path,
) -> None:
    """An earlier run's ticket with no link, its card already filed: listed, then updated.

    `record-existing-tickets` lists it once, reading and writing nothing on the board and
    changing no ticket, and run again changes nothing; the ticket's next copy keeps the store's
    origin lookup, which finds the card carrying its origin, so the board still holds one card
    for it — while a ticket written after the list is created with no lookup at all.
    """
    environment, root = _followups_environment(tmp_path)
    run = follow_up_tickets.RunId("earlier-run")
    earlier = replace(
        _follow_up_ticket(SIBLING_REPOSITORY),
        created_by_run=run,
        owning_runs=(run,),
        drafts=(follow_up_tickets.QualifiedDraftId(f"drafts:{run}/drafts/noticed"),),
    )
    path = _written_ticket(root, earlier)
    origin = follow_up_tickets.qualified_id("earlier-run", path.stem)
    with _serving_followups(environment, fields=True):
        held = _seeded_item(
            0,
            run="earlier-run",
            cause=path.stem,
            status=follow_up_tickets.Status.ACCEPTED,
            origin=origin,
            origin_in_body=True,
        )
        held.priority = "High"
        card_before = (held.body, held.status, held.priority)
        ticket_before = path.read_bytes()
        _GitHubFixture.requests.clear()
        first = _record_existing(environment, root)
        listed = follow_up_tickets.existing_tickets_path(root).read_bytes()
        again = _record_existing(environment, root)
        recorded_requests = list(_GitHubFixture.requests)
        assert (held.body, held.status, held.priority) == card_before
        assert path.read_bytes() == ticket_before, "recording changed the ticket"

        # Its record names no card, so it reads as the proposal it was when written.
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        _GitHubFixture.requests.clear()
        status, out, said = _copy(environment, path)
        earlier_copy = list(_GitHubFixture.requests)

        later = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
        assert _decided_status(environment, later) == follow_up_tickets.Status.PROPOSED
        _GitHubFixture.requests.clear()
        later_status, later_out, later_said = _copy(environment, later)
        later_copy = list(_GitHubFixture.requests)

    assert recorded_requests == [], "recording the existing tickets asked the board"
    assert first == {
        "list": str(follow_up_tickets.existing_tickets_path(root)),
        "tickets": 1,
        "recorded": True,
    }
    assert again == dict(first, recorded=False)
    assert follow_up_tickets.existing_tickets_path(root).read_bytes() == listed
    assert follow_up_tickets.existing_tickets(root) == frozenset({origin})
    assert status == follow_up_tickets.SOUND, said
    assert json.loads(out) == {"action": "updated", "destination": _card(held)}
    assert _lookups(earlier_copy) != [], "the listed ticket's copy skipped the origin lookup"
    assert _carrying(origin) == [held], "the earlier ticket gained a second card"
    assert held.status == card_before[1], "the copy moved a status a person set"
    assert later_status == follow_up_tickets.SOUND, later_said
    assert json.loads(later_out)["action"] == "created"
    assert _lookups(later_copy) == [], "a ticket written after the list was looked up"
    assert len(_carrying(PROPOSED_ID)) == 1


def test_a_new_ticket_lands_one_card_with_no_lookup_and_its_re_copy_updates_it(
    tmp_path: Path,
) -> None:
    """Once the list is recorded, a new proposal is created with no origin lookup, onto exactly
    one card, and copying it again follows its link onto that card, creating none."""
    environment, root = _followups_environment(tmp_path)
    assert _record_existing(environment, root)["tickets"] == 0
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        _GitHubFixture.requests.clear()
        status, out, said = _copy(environment, path)
        created = list(_GitHubFixture.requests)
        assert status == follow_up_tickets.SOUND, said
        _edited(environment, path, "Seen again.")
        _GitHubFixture.requests.clear()
        again, again_out, again_said = _copy(environment, path)
        recopied = list(_GitHubFixture.requests)

    (card,) = _carrying(PROPOSED_ID)
    assert BOARD.created == [card]
    assert json.loads(out) == {"action": "created", "destination": _card(card)}
    assert _lookups(created) == [], "a new ticket's create sent an origin lookup"
    assert again == follow_up_tickets.SOUND, again_said
    assert json.loads(again_out) == {"action": "updated", "destination": _card(card)}
    assert _lookups(recopied) == []
    assert "Seen again." in card.body


def test_a_new_tickets_create_interrupted_after_filing_is_retried_onto_that_card(
    tmp_path: Path,
) -> None:
    """A create sent with no lookup whose answer was lost: the retry finishes the same card.

    The store keeps its intent on the ticket whether it looked a counterpart up or not, and a
    record carrying an intent keeps the store's own rules, so the retry finds the card its first
    attempt filed by its create key rather than creating a second.
    """
    environment, root = _followups_environment(tmp_path)
    _record_existing(environment, root)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        _GitHubFixture.dropped_operation = (_Operation.CREATE_ISSUE, "after")
        _GitHubFixture.requests.clear()
        status, _out, said = _copy(environment, path)
        first = list(_GitHubFixture.requests)
        assert status != follow_up_tickets.SOUND, said
        assert len(BOARD.created) == 1, "the board ran the create"
        assert _lookups(first) == [], "the first attempt looked its counterpart up"
        assert _ticket(environment, path).creating is not None

        status, out, said = _copy(environment, path)

    assert status == follow_up_tickets.SOUND, said
    (card,) = _carrying(PROPOSED_ID)
    assert BOARD.created == [card], "the retry created a second card"
    assert json.loads(out)["destination"] == _card(card)
    finished = _ticket(environment, path)
    assert finished.link(FOLLOWUPS) == card.content_id and finished.creating is None


def test_a_ticket_on_a_private_destination_keeps_its_lookup_and_its_one_card(
    tmp_path: Path,
) -> None:
    """On a destination declared private the store records no link to the card, so the record
    never names it: each copy keeps the store's origin lookup, and copying the ticket again, as
    a re-dispatch of the run does, updates the one card."""
    environment, root = _followups_environment(tmp_path)
    # The board and the repository its issues are created in are both private, as a host
    # declaring a destination private keeps them.
    environment["ONETASKGRAPH_SOURCES__FOLLOWUPS__VISIBILITY"] = "private"
    environment["ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__REPOSITORY"] = str(
        PRIVATE_STORE_REPOSITORY
    )
    environment["ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__CREATION_OWNERS"] = (
        f"{PRIVATE_STORE_REPOSITORY.owner},{CONFIGURED_REPOSITORY.owner}"
    )
    _record_existing(environment, root)
    path = _written_ticket(root, _follow_up_ticket(PRIVATE_STORE_REPOSITORY))
    with _serving_followups(environment, fields=True):
        BOARD.public = False
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        _GitHubFixture.requests.clear()
        status, out, said = _copy(environment, path)
        created = list(_GitHubFixture.requests)
        assert status == follow_up_tickets.SOUND, said
        assert _ticket(environment, path).links == (), "the store linked a private card"
        _edited(environment, path, "Seen again.")
        _GitHubFixture.requests.clear()
        again, again_out, again_said = _copy(environment, path)
        recopied = list(_GitHubFixture.requests)

    (card,) = _carrying(PROPOSED_ID)
    assert BOARD.created == [card], "the private destination gained a second card"
    assert json.loads(out) == {"action": "created", "destination": _card(card)}
    assert _lookups(created) != [], "a copy onto a private destination skipped its lookup"
    assert again == follow_up_tickets.SOUND, again_said
    assert json.loads(again_out) == {"action": "updated", "destination": _card(card)}
    assert _lookups(recopied) != []


def _older_schema(_environment: dict[str, str], path: Path, _card_id: str) -> None:
    text = path.read_text(encoding="utf-8")
    older = text.replace(
        f'"schema": {follow_up_tickets.SCHEMA}', f'"schema": {follow_up_tickets.PRIOR_SCHEMA}'
    )
    assert older != text
    path.write_text(older, encoding="utf-8")


def _ticket_rewritten(environment: dict[str, str], path: Path, **departures: object) -> None:
    """The ticket at ``path`` rewritten in place with ``departures``, as the store reads it."""
    held = _ticket(environment, path)
    path.write_text(follow_up_tickets.render(replace(held, **departures)), encoding="utf-8")


#: Each sign on a ticket written after the host's list that it may have been filed before, as a
#: rewrite of its record, or of its run's receipts, given the card already carrying its origin.
FILED_BEFORE: dict[str, Callable[[dict[str, str], Path, str], None]] = {
    "an older schema": _older_schema,
    "an estimate a copy wrote": lambda environment, path, _card_id: _ticket_rewritten(
        environment, path, board_estimate=follow_up_tickets.Priority.MEDIUM
    ),
}


@pytest.mark.parametrize("sign", list(FILED_BEFORE), ids=list(FILED_BEFORE))
def test_a_ticket_showing_it_may_have_been_filed_keeps_the_lookup_and_its_one_card(
    sign: str, tmp_path: Path
) -> None:
    """With the host's list recorded, a ticket it does not name whose record or receipts show it
    may have been filed already is copied by the store's own rules, onto the one card carrying
    its origin, rather than created without a lookup beside it."""
    environment, root = _followups_environment(tmp_path)
    _record_existing(environment, root)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        held = _seeded_item(
            0,
            run=PROPOSED_RUN,
            cause=path.stem,
            status=follow_up_tickets.Status.PROPOSED,
            origin=PROPOSED_ID,
            origin_in_body=True,
        )
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        FILED_BEFORE[sign](environment, path, _card(held))
        status, out, said = _copy(environment, path)

    assert status == follow_up_tickets.SOUND, said
    assert json.loads(out)["destination"] == _card(held)
    assert _carrying(PROPOSED_ID) == [held] and BOARD.created == [], "a second card was filed"


#: What an agent's rewrite of a ticket its run already filed drops: every key the store and the
#: copy wrote, or only the link and the estimate, keeping where the store says each copy opens.
REWRITES: dict[str, dict[str, object]] = {
    "every key the store wrote": {
        "links": (),
        "origin": None,
        "board_estimate": None,
        "reserved": (),
    },
    "its link but not the copy's locations": {"links": (), "board_estimate": None},
}


@pytest.mark.parametrize("dropped", list(REWRITES), ids=list(REWRITES))
def test_a_ticket_rewritten_without_its_link_after_its_runs_copy_keeps_its_one_card(
    dropped: str, tmp_path: Path
) -> None:
    """The agent rewrites a ticket its run already filed, dropping the keys the store and the
    copy wrote: what it kept, or its run's receipt of that copy, keeps the store's origin
    lookup, which finds the card, so its next copy updates it rather than filing a second.

    Rewritten with its text unchanged, the ticket is what its landed copy put on the card, so
    that copy is skipped and the board is sent nothing; the lookup is the copy of the ticket
    once its text changed too."""
    environment, root = _followups_environment(tmp_path)
    _record_existing(environment, root)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        assert _copy(environment, path)[0] == follow_up_tickets.SOUND
        (card,) = _carrying(PROPOSED_ID)
        if dropped != "every key the store wrote":
            assert _ticket(environment, path).reserved, "the copy recorded no location"
        _ticket_rewritten(environment, path, **REWRITES[dropped])
        _GitHubFixture.requests.clear()
        status, out, said = _copy(environment, path)
        assert status == follow_up_tickets.SOUND, said
        assert json.loads(out) == {
            "action": follow_up_tickets.SKIPPED_UNCHANGED,
            "destination": _card(card),
        }
        assert _GitHubFixture.requests == [], "an unchanged ticket's copy was sent to the board"
        held = _ticket(environment, path)
        _ticket_rewritten(environment, path, body=f"{held.body}\n\nSeen again after the rewrite.")
        _GitHubFixture.requests.clear()
        status, out, said = _copy(environment, path)
        sent = list(_GitHubFixture.requests)

    assert status == follow_up_tickets.SOUND, said
    copied = json.loads(out)
    assert copied["action"] != "created" and copied["destination"] == _card(card)
    assert BOARD.created == [card], "the rewritten ticket gained a second card"
    assert _lookups(sent) != [], "the rewritten ticket's copy skipped the lookup"


def test_a_current_record_naming_its_card_as_its_own_origin_is_refused_and_files_nothing(
    tmp_path: Path,
) -> None:
    """Only a record from before the binding was retired may name its card as its own origin;
    a current one doing so is refused before the store is asked to copy, so with the host's
    list recorded it still files no card beside the one already carrying its origin."""
    environment, root = _followups_environment(tmp_path)
    _record_existing(environment, root)
    path = _written_ticket(root, _follow_up_ticket(SIBLING_REPOSITORY))
    with _serving_followups(environment, fields=True):
        held = _seeded_item(
            0,
            run=PROPOSED_RUN,
            cause=path.stem,
            status=follow_up_tickets.Status.PROPOSED,
            origin=PROPOSED_ID,
            origin_in_body=True,
        )
        assert _decided_status(environment, path) == follow_up_tickets.Status.PROPOSED
        _ticket_rewritten(environment, path, origin=follow_up_tickets.QualifiedTaskId(_card(held)))
        status, out, said = _copy(environment, path)

    assert status != follow_up_tickets.SOUND and out == "", said
    assert "remove it" in said
    assert _carrying(PROPOSED_ID) == [held] and BOARD.created == [], "a second card was filed"


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
