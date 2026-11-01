"""A follow-up retry carries forward what an earlier pass settled and placed.

`copy` skips the board for a ticket whose :func:`tickets.copy_digest` matches the last copy of
it that landed, receipting the skip; a re-dispatch carries the account's dispositions forward
unchanged; and `--unanswered` scopes a re-dispatch to the drafts and tickets an earlier pass
left open. Every copy here runs this module's real `copy` command through the installed plan
store against a local store standing in for the board, as `tests/test_follow_up_tickets.py`'s
copy tests do; the recipe-level journeys over the loopback GitHub board are
`tests/plan_tooling/follow_ups/test_follow_up_retry_e2e.py`.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from test_follow_up_tickets import (
    BOARD,
    CAUSE,
    DRAFT,
    NARROWING,
    NARROWING_URL,
    OTHER_DRAFT,
    RUN,
    _accepted_item,
    _account,
    _answers,
    _answers_command,
    _board_item,
    _carrying,
    _copied,
    _disposed,
    _drafted,
    _rendered,
    _ticket,
    _write,
    board,
    drafts_root,
)

from orchestrator import follow_up_tickets as tickets

__all__ = ["board", "drafts_root"]

#: A sentence of evidence an edit adds after a copy.
EDITED = "Seen again on page 10 after the copy."


def _decided_and_copied(path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """`board-status`, then `copy`, as the task sequences them; what `copy` printed."""
    assert tickets.main(["board-status", "--board", BOARD, str(path)]) == tickets.SOUND
    capsys.readouterr()
    status, printed, said = _copied(path, capsys)
    assert status == tickets.SOUND, said
    answered: dict[str, object] = json.loads(printed)
    return answered


def _card_file(destination: str) -> Path:
    """The file the stand-in board keeps ``destination``'s card in."""
    location = _board_item(destination)["location"]
    assert isinstance(location, dict), location
    return Path(str(location["path"]))


def _receipts(root: Path) -> list[dict[str, object]]:
    path = tickets.receipts_path(root, RUN)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _outcome(receipt: dict[str, object]) -> dict[str, object]:
    outcome = receipt["outcome"]
    assert isinstance(outcome, dict), receipt
    return outcome


def _edited(path: Path) -> None:
    """One more sentence of evidence in the ticket at ``path``, its record and link kept."""
    held = tickets.read_ticket(path)
    path.write_text(
        tickets.render(dataclasses.replace(held, body=f"{held.body}\n\n{EDITED}")), "utf-8"
    )


def test_copy_skips_a_ticket_unchanged_since_its_landed_copy_naming_that_receipt(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second copy writes nothing, and its receipt names the first copy's line.

    The receipt carries the digest the ticket's record and dependencies give, the one the
    landed copy recorded, and `check-dispositions --board` reads the placement from the landed
    copy the skip names.
    """
    _drafted(drafts_root, RUN, "a-cursor-draft")
    path = _write(drafts_root, _carrying())
    first = _decided_and_copied(path, capsys)
    card = _card_file(str(first["destination"]))
    held = card.read_bytes()

    second = _decided_and_copied(path, capsys)

    assert second == {"action": tickets.SKIPPED_UNCHANGED, "destination": first["destination"]}
    assert card.read_bytes() == held, "the skipped copy wrote the card"
    landed, skipped = _receipts(drafts_root)
    digest = tickets.copy_digest(tickets.read_ticket(path))
    assert _outcome(landed)["digest"] == digest
    assert _outcome(skipped) == {
        "action": tickets.SKIPPED_UNCHANGED,
        "digest": digest,
        "unchanged_since": 1,
        "error": None,
        "held": None,
        "priority_condition": None,
        "result": None,
        "status_condition": None,
    }
    assert skipped["card"] == landed["card"] == first["destination"]
    tickets.open_dispositions(drafts_root, RUN).write_text(
        json.dumps(_account(_disposed())), encoding="utf-8"
    )
    arguments = ["check-dispositions", "--root", str(drafts_root), "--board", BOARD, RUN]
    assert tickets.main(arguments) == tickets.SOUND, capsys.readouterr().err
    # A third copy still stands on the one that landed, never on the skip before it.
    _decided_and_copied(path, capsys)
    assert _outcome(_receipts(drafts_root)[-1])["unchanged_since"] == 1


def test_a_changed_record_is_copied_and_its_receipt_records_the_new_digest(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(drafts_root, _ticket())
    destination = _decided_and_copied(path, capsys)["destination"]
    _edited(path)

    copied = _decided_and_copied(path, capsys)

    assert copied == {"action": "updated", "destination": destination}
    assert EDITED in str(_board_item(str(destination))["content"])
    landed, edited = _receipts(drafts_root)
    assert _outcome(edited)["digest"] == tickets.copy_digest(tickets.read_ticket(path))
    assert _outcome(edited)["digest"] != _outcome(landed)["digest"]


def test_a_changed_depends_on_set_is_copied(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ticket's text stays; only its store `depends_on` gains an accepted ticket.

    Copied straight away, so nothing but the dependency set differs from the landed copy:
    `board-status` would first ask for the entry's `## Related tickets` line, a text change of
    its own.
    """
    accepted = next(status for status in tickets.Status if status.accepted)
    _accepted_item(board, NARROWING, accepted.value, NARROWING_URL)
    path = _write(drafts_root, _ticket())
    _decided_and_copied(path, capsys)
    held = tickets.read_ticket(path)
    path.write_text(tickets.render(dataclasses.replace(held, depends_on=(NARROWING,))), "utf-8")
    assert tickets.read_ticket(path).depends_on == (NARROWING,)

    status, printed, said = _copied(path, capsys)

    assert status == tickets.SOUND, said
    assert json.loads(printed)["action"] != tickets.SKIPPED_UNCHANGED
    landed, copied = _receipts(drafts_root)
    assert _outcome(copied)["action"] != tickets.SKIPPED_UNCHANGED
    assert _outcome(copied)["digest"] == tickets.copy_digest(tickets.read_ticket(path))
    assert _outcome(copied)["digest"] != _outcome(landed)["digest"]


def test_a_landed_copy_receipted_without_a_digest_is_copied_again(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A schema-1 receipt says nothing about what the card holds, so the ticket is copied."""
    path = _write(drafts_root, _ticket())
    _decided_and_copied(path, capsys)
    receipts = tickets.receipts_path(drafts_root, RUN)
    (landed,) = _receipts(drafts_root)
    del _outcome(landed)["digest"]
    receipts.write_text(json.dumps(landed | {"schema": 1}) + "\n", encoding="utf-8")

    copied = _decided_and_copied(path, capsys)

    assert copied["action"] == "unchanged", "the store was asked, and found nothing to change"
    assert _outcome(_receipts(drafts_root)[-1])["digest"] == tickets.copy_digest(
        tickets.read_ticket(path)
    )


def test_the_digest_covers_the_content_and_dependencies_and_nothing_a_copy_stamps() -> None:
    ticket = _ticket()
    digest = tickets.copy_digest(ticket)

    assert tickets.DIGEST.fullmatch(digest)
    stamped = dataclasses.replace(
        ticket,
        board_estimate=tickets.Priority.HIGH,
        links=(tickets.CopyLink(BOARD, f"{BOARD}:{RUN}/tickets/{CAUSE}"),),
        reserved=(("onetaskgraph.copy_locations", "{}"),),
    )
    assert tickets.copy_digest(stamped) == digest
    for changed in (
        dataclasses.replace(ticket, depends_on=(NARROWING,)),
        dataclasses.replace(ticket, status=tickets.Status.WITHDRAWN),
        dataclasses.replace(ticket, title=f"{ticket.title}, again"),
        dataclasses.replace(ticket, body=f"{ticket.body}\n\n{EDITED}"),
        # A ticket `board-status` has not yet estimated, which a scoped retry reads pending.
        dataclasses.replace(ticket, priority_estimate=None),
    ):
        assert tickets.copy_digest(changed) != digest


@pytest.mark.parametrize(
    ("outcome", "refused"),
    [
        ({"action": tickets.SKIPPED_UNCHANGED, "digest": "0" * 64}, "`unchanged_since`"),
        ({"action": "updated", "unchanged_since": 1, "result": {}}, "`unchanged_since`"),
        (
            {"action": tickets.SKIPPED_UNCHANGED, "unchanged_since": 1, "digest": "0" * 64}
            | {"result": {}},
            "writes no `result`",
        ),
        ({"action": "skipped", "digest": "0" * 64}, "only a copy that landed"),
        ({"action": "updated", "result": {}, "digest": "not hex"}, "digest"),
        ({"action": "updated", "result": {}, "digest": f"{'0' * 64}0"}, "digest"),
    ],
    ids=[
        "skip-naming-nothing",
        "write-naming-a-skip",
        "skip-with-result",
        "digest-on-a-skip-of-the-store",
        "digest-not-sha256",
        "digest-past-sha256",
    ],
)
def test_a_copy_outcome_holds_its_schema_two_keys_to_its_action(
    outcome: dict[str, object], refused: str
) -> None:
    with pytest.raises(ValueError, match=refused):
        tickets.CopyOutcomeReceipt.model_validate(outcome)


def test_a_receipt_line_whose_schema_two_keys_contradict_it_refuses_the_account(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A schema-1 line with a digest, and a skip naming no landed copy of its ticket and card."""
    _drafted(drafts_root, RUN, "a-cursor-draft")
    path = _write(drafts_root, _carrying())
    _decided_and_copied(path, capsys)
    _decided_and_copied(path, capsys)
    tickets.open_dispositions(drafts_root, RUN).write_text(
        json.dumps(_account(_disposed())), encoding="utf-8"
    )
    receipts = tickets.receipts_path(drafts_root, RUN)
    landed, skipped = _receipts(drafts_root)

    def skip(**departures: object) -> str:
        return json.dumps(skipped | {"outcome": _outcome(skipped) | departures})

    lines = [
        json.dumps(landed | {"schema": 1}),
        skip(unchanged_since=2),
        skip(unchanged_since=99),
        skip(digest="f" * 64),
        json.dumps(skipped | {"card": f"{BOARD}:another-card"}),
    ]
    with receipts.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")

    arguments = ["check-dispositions", "--root", str(drafts_root), "--board", BOARD, RUN]
    assert tickets.main(arguments) == tickets.UNSOUND
    refused = " ".join(capsys.readouterr().err.split())
    assert f"receipt line 3 of {receipts} is of schema 1, which carries no `digest`" in refused
    for at in (4, 5, 6, 7):
        assert f"receipt line {at} of {receipts} records a copy `skipped-unchanged`" in refused


def test_a_re_dispatch_holds_the_account_to_every_disposition_it_carried(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What an earlier pass answered is carried by the next launch and checked unchanged.

    Changed or removed, the carried entry is refused by `check-dispositions` and by the next
    `open-dispositions`, which leaves the account and what it carried as they were.
    """
    _drafted(drafts_root, RUN, "a-cursor-draft", "a-sweep-draft")
    _write(drafts_root, _carrying())
    first = tickets.open_dispositions(drafts_root, RUN)
    assert not tickets.carried_path(drafts_root, RUN).exists(), "a first pass carries nothing"
    answered = _disposed()
    first.write_text(
        json.dumps(_account(answered, drafts=sorted([DRAFT, OTHER_DRAFT]))), encoding="utf-8"
    )

    tickets.open_dispositions(drafts_root, RUN)

    carried = json.loads(tickets.carried_path(drafts_root, RUN).read_text(encoding="utf-8"))
    assert carried == {
        "schema": tickets.DISPOSITIONS_SCHEMA,
        "run": RUN,
        "dispositions": [answered],
    }
    rewritten = answered | {"detail": "Answered again by the retry."}
    first.write_text(
        json.dumps(
            _account(
                rewritten,
                _disposed(OTHER_DRAFT, tickets.Disposition.TOO_LOW_IMPACT.value, []),
                drafts=sorted([DRAFT, OTHER_DRAFT]),
            )
        ),
        encoding="utf-8",
    )
    held = first.read_bytes()

    assert tickets.main(["check-dispositions", "--root", str(drafts_root), RUN]) == tickets.UNSOUND
    said = " ".join(capsys.readouterr().err.split())
    assert f"the disposition of {DRAFT!r} was carried forward from the earlier pass" in said
    with pytest.raises(tickets.Refused, match="was carried forward from the earlier pass"):
        tickets.open_dispositions(drafts_root, RUN)
    assert first.read_bytes() == held

    first.write_text(
        json.dumps(
            _account(
                answered,
                _disposed(OTHER_DRAFT, tickets.Disposition.TOO_LOW_IMPACT.value, []),
                drafts=sorted([DRAFT, OTHER_DRAFT]),
            )
        ),
        encoding="utf-8",
    )
    assert tickets.main(["check-dispositions", "--root", str(drafts_root), RUN]) == tickets.SOUND


def test_a_carried_account_that_is_not_one_is_refused(drafts_root: Path) -> None:
    path = tickets.carried_path(drafts_root, RUN)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": 1, "run": RUN, "dispositions": [[]]}), "utf-8")

    with pytest.raises(tickets.Refused, match="is not this run's carried account: entry 0"):
        tickets.read_carried(drafts_root, RUN)


def test_the_unanswered_scope_is_the_open_drafts_and_the_tickets_no_copy_confirms(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Answered drafts and tickets unchanged since their landed copy are out of scope."""
    with pytest.raises(tickets.Refused, match=f"verify every draft with 'just follow-ups {RUN}'"):
        tickets.retry_scope(drafts_root, RUN)
    _drafted(drafts_root, RUN, "a-cursor-draft", "a-sweep-draft")
    placed = _write(drafts_root, _carrying())
    _decided_and_copied(placed, capsys)
    edited = _write(drafts_root, _ticket(root_cause=tickets.RootCause("sweep-drops-a-family")))
    _decided_and_copied(edited, capsys)
    _edited(edited)
    never = _write(drafts_root, _ticket(root_cause=tickets.RootCause("export-drops-a-column")))
    unreadable = _write(drafts_root, _ticket(root_cause=tickets.RootCause("not-a-ticket")), "x")
    tickets.open_dispositions(drafts_root, RUN).write_text(
        json.dumps(_account(_disposed(), drafts=sorted([DRAFT, OTHER_DRAFT]))), encoding="utf-8"
    )

    scope = tickets.retry_scope(drafts_root, RUN)

    assert scope == tickets.RetryScope((OTHER_DRAFT,), tuple(sorted((edited, never, unreadable))))
    tickets.dispositions_path(drafts_root, RUN).write_text('{"drafts": 1}', encoding="utf-8")
    with pytest.raises(tickets.Refused, match="is not this run's account"):
        tickets.retry_scope(drafts_root, RUN)
    # An entry that is no disposition answers nothing: the account is refused, not read past.
    for entry in ([DRAFT], _disposed() | {"disposition": "dropped"}):
        tickets.dispositions_path(drafts_root, RUN).write_text(
            json.dumps(_account(entry, drafts=sorted([DRAFT, OTHER_DRAFT]))),
            encoding="utf-8",
        )
        with pytest.raises(tickets.Refused, match="is not this run's account: (entry 0|the disp)"):
            tickets.retry_scope(drafts_root, RUN)


def test_a_scoped_re_dispatch_names_only_its_open_work_and_refuses_the_feedback_mode(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _drafted(drafts_root, RUN, "a-cursor-draft", "a-sweep-draft")
    placed = _write(drafts_root, _carrying())
    _decided_and_copied(placed, capsys)
    _edited(placed)
    tickets.open_dispositions(drafts_root, RUN).write_text(
        json.dumps(_account(_disposed(), drafts=sorted([DRAFT, OTHER_DRAFT]))), encoding="utf-8"
    )

    assert tickets.main(_answers_command(drafts_root, "--unanswered")) == tickets.SOUND
    answered = json.loads(capsys.readouterr().out)

    assert answered["redispatch"] is True
    assert answered["unanswered"] == {"drafts": [OTHER_DRAFT], "tickets": [str(placed)]}
    task = _rendered(answered).stdout
    scope = task.split("## The work this re-dispatch is scoped to", 1)[1].split("\n## ", 1)[0]
    assert f"- draft `{OTHER_DRAFT}`" in scope
    assert f"- ticket `{placed}`" in scope
    assert DRAFT not in scope.replace(OTHER_DRAFT, "")
    assert "## The work this re-dispatch is scoped to" not in _rendered(_answers()).stdout
    with pytest.raises(tickets.Refused, match="a comment gathering answers comments"):
        _answers(
            tickets.Mode.FEEDBACK,
            feedback="A gathering.",
            scope=tickets.retry_scope(drafts_root, RUN),
        )
