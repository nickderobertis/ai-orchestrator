"""A follow-up ticket's priority: estimated from recorded facts, held to a person's override.

What `orchestrator/follow_up_tickets.py` decides about priority, driven through its real
commands against the installed `onetaskgraph`, over a drafts root and a second local store
standing in for the board, as `tests/test_follow_up_tickets.py` stands one in: the estimate
over every severity, frequency and count; the occurrence count recounted off a board item's
comments at every computation; the lockstep rule `board-status` applies before every copy,
with each person's change made through the store's own `task priority set`; the narrow
`re-estimate` a run makes on another run's item; and what `validate`, `check-dispositions`
and `check-responses` refuse. `tests/e2e/test_onetaskgraph_host_e2e.py` takes the same
writes to a GitHub Projects board's `Priority` field, and the follow-ups recipe journeys
under `tests/plan_tooling/` take them through a dispatched run.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] The module under test decides
through the installed plan store, which it reaches through the typed SDK its own package
pins, so driving that store is exercising `orchestrator/` rather than a host tool beside it:
`tests/test_follow_up_tickets.py` drives the same store for the same module in the same
`orchestrator:test` target, keyed on the code workspace that covers both.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable
from pathlib import Path

import follow_up_variables
import onetaskgraph_sdk
import pytest
from follow_up_ticket_shape import FIX, impact_prose

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.project_store import TASKS_DIRECTORY, frontmatter

RUN = "listing-run"
OTHER_RUN = "earlier-run"
LATER_RUN = "later-run"
CAUSE = "cursor-skips-last-page"
REPOSITORY = "github.com/nickderobertis/some-service"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
HOST = "verifier-01.build.example"
#: The local store standing in for the board, as `tests/test_follow_up_tickets.py` names it.
BOARD = "ticketboard"
Severity = tickets.Severity
Priority = tickets.Priority
Frequency = tickets.Frequency


#: The outcome every test ticket's `## Impact` states, and the workaround a schema-7 one names.
OUTCOME = "Every reader of a listing misses its last page."
OLD_WORKAROUND = "readers retry the last page by its number"


def _impact(with_workaround: Severity, line: str | None, *, structured: bool = True) -> str:
    """An `## Impact` section at ``with_workaround``, closed by ``line`` when there is one.

    ``structured`` writes it as schema 8 does, with no acceptable workaround; otherwise as
    schema 7 did: bare prose, and a workaround that leaves the severity where it was, which
    that schema's validator accepted.
    """
    section = tickets.impact_section(
        impact_prose(OUTCOME, workaround="none") if structured else OUTCOME,
        with_workaround,
        "none" if structured else OLD_WORKAROUND,
        with_workaround,
    )
    return section if line is None else f"{section}{line}\n"


def _body(with_workaround: Severity, line: str | None, *, structured: bool = True) -> str:
    sections = []
    for heading in tickets.HEADINGS:
        match heading:
            case tickets.IMPACT:
                text = _impact(with_workaround, line, structured=structured)
            case tickets.SUGGESTED_FIX if structured:
                text = FIX
            case tickets.EVIDENCE:
                text = f"What this ticket says under {heading}. Verified on `{HOST}`."
            case _:
                text = f"What this ticket says under {heading}."
        sections.append(f"## {heading}\n\n{text}")
    return "\n\n".join(sections)


def _ticket(
    run: str = RUN,
    *,
    with_workaround: Severity = Severity.MEDIUM,
    frequency: Frequency | None = Frequency.INTERMITTENT,
    cause: str = CAUSE,
    structured: bool = True,
) -> tickets.Ticket:
    """A sound current ticket of ``run``, its estimate what its facts give one occurrence."""
    line = tickets.estimate_line(with_workaround, frequency, 1)
    return tickets.Ticket(
        title=f"some-service: {cause}",
        status=tickets.Status.PROPOSED,
        root_cause=tickets.RootCause(cause),
        repository=tickets.Origin(REPOSITORY),
        created_by_run=tickets.RunId(run),
        owning_runs=(tickets.RunId(run),),
        drafts=(tickets.QualifiedDraftId(f"drafts:{run}/drafts/a-draft"),),
        basis=(tickets.Basis(tickets.Origin(REPOSITORY), tickets.Commit(COMMIT)),),
        verified_at=tickets.Timestamp("2026-01-01T00:00:00Z"),
        host=tickets.Host(HOST),
        body=_body(with_workaround, line, structured=structured),
        priority_estimate=tickets.estimate(with_workaround, frequency, 1),
        frequency=frequency,
    )


def _older(ticket: tickets.Ticket, schema: int) -> str:
    """``ticket`` as an older ``schema`` stored it: the current record but for its schema.

    From schema 7 on its priority is the estimate `board-status` wrote; schema 6 stored no
    estimate, no frequency and no estimate line, so those go too.
    """
    record = tickets.record(ticket)
    record["schema"] = schema
    body = ticket.body
    fields: dict[str, object] = {"priority": str(ticket.priority_estimate)}
    if schema < tickets.ESTIMATE_AT:
        fields = {}
        del record[tickets.ESTIMATE_FIELD]
        record.pop(tickets.FREQUENCY_FIELD, None)
        body = re.sub(r"^- Priority estimate:[^\n]*\n?", "", body, flags=re.MULTILINE)
    return frontmatter(
        {
            "title": ticket.title,
            "status": ticket.status.value,
            "repositories": [ticket.repository],
            **fields,
            "metadata": {tickets.KEY: record},
        },
        body,
    )


def _schema_6(ticket: tickets.Ticket) -> str:
    """``ticket`` as schema 6 stored it: no estimate, no frequency, no estimate line."""
    return _older(ticket, tickets.UNESTIMATED_SCHEMA)


@pytest.fixture
def drafts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A drafts root the installed store reads, named the way a launch exports it."""
    root = tmp_path / "follow-ups"
    root.mkdir()
    monkeypatch.setenv(follow_up_variables.root_name(), str(root))
    monkeypatch.setenv(follow_up_variables.plugin_name(), WRITABLE_PLUGIN)
    return root


@pytest.fixture
def board(drafts_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A second local store standing in for the board, beside the drafts root."""
    root = tmp_path / "board"
    root.mkdir()
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__PLUGIN", WRITABLE_PLUGIN)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__CONFIG__ROOT", str(root))
    return root


def _write(root: Path, ticket: tickets.Ticket, text: str | None = None) -> Path:
    path = tickets.ticket_path(root, ticket.created_by_run, ticket.root_cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else tickets.render(ticket), encoding="utf-8")
    return path


def _shown(qualified: str) -> dict[str, object]:
    return dict(plan_store.task_record(qualified))


def _held(qualified: str) -> dict[str, object]:
    metadata = _shown(qualified)["metadata"]
    assert isinstance(metadata, dict)
    record = metadata[tickets.KEY]
    assert isinstance(record, dict)
    return record


def _line(content: object) -> str:
    """The one estimate line ``content`` carries."""
    assert isinstance(content, str)
    (line,) = re.findall(r"^- Priority estimate:[^\n]*$", content, flags=re.MULTILINE)
    return line


def _person_sets(qualified: str, priority: Priority) -> None:
    """A person's change of the item's priority, through the store's own verb for it."""
    set_to = plan_store.sdk(plan_store.client().task_priority_set(qualified, priority.value))
    assert set_to.priority.value == priority.value


def _comment(qualified: str, body: str, author: str | None = None) -> str:
    added = plan_store.sdk(
        plan_store.client().task_comment_add(qualified, body=body, author=author)
    )
    return str(added.id.model_dump())


def _evidence(qualified: str, run: str) -> str:
    return _comment(qualified, tickets.render_comment(run, CAUSE, f"Run {run} saw it too."))


def _reply(qualified: str, run: str, verdict: tickets.Verdict) -> str:
    asked = _comment(qualified, "I hit this as well.\n", author="a-person")
    return _comment(
        qualified,
        tickets.render_reply(
            run,
            CAUSE,
            answers=asked,
            url="https://example.invalid/c",
            author="a-person",
            response="Counted.",
            verdict=verdict,
        ),
    )


def _decided(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Run `board-status` as the agent does, and return the word it printed."""
    status = tickets.main(["board-status", "--board", BOARD, str(path)])
    captured = capsys.readouterr()
    assert status == tickets.SOUND, captured.err
    return captured.out


def _copied(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """`board-status`, then the follow-up `copy`, as the task sequences them: the item's id."""
    assert _decided(path, capsys) in {f"{status.value}\n" for status in tickets.Status}
    validated = tickets.main(["validate", str(path)])
    assert validated == tickets.SOUND, capsys.readouterr().err
    capsys.readouterr()
    assert tickets.main(["copy", "--board", BOARD, str(path)]) == tickets.SOUND
    destination = json.loads(capsys.readouterr().out)["destination"]
    assert tickets.board_estimate_problems(destination) == [], "the copy's line and record part"
    return str(destination)


def _refacted(path: Path, **facts: object) -> None:
    """Rewrite the ticket at ``path`` with changed facts, keeping what the tools wrote.

    What an agent does when re-verification changes a fact: the severities or the frequency
    judgment. The estimate, the estimate line and the priority are left as `board-status`
    last wrote them, for it to rewrite.
    """
    held = tickets.read_ticket(path)
    severity = facts.get("with_workaround")
    body = held.body
    if isinstance(severity, Severity):
        body = re.sub(r"^- Severity: \w+$", f"- Severity: {severity}", body, flags=re.MULTILINE)
        body = re.sub(
            r"^- Severity with the workaround: \w+$",
            f"- Severity with the workaround: {severity}",
            body,
            flags=re.MULTILINE,
        )
    frequency = facts.get("frequency", held.frequency)
    assert frequency is None or isinstance(frequency, Frequency)
    path.write_text(
        tickets.render(dataclasses.replace(held, body=body, frequency=frequency)),
        encoding="utf-8",
    )


@pytest.mark.parametrize("severity", list(Severity))
@pytest.mark.parametrize("frequency", [Frequency.CONSISTENT, Frequency.INTERMITTENT, None])
@pytest.mark.parametrize("occurrences", [1, 2, 3, 4])
def test_the_estimate_is_the_severity_with_the_workaround_raised_one_level_only_when_it_should(
    severity: Severity, frequency: Frequency | None, occurrences: int
) -> None:
    base = {
        Severity.CRITICAL: Priority.URGENT,
        Severity.HIGH: Priority.HIGH,
        Severity.MEDIUM: Priority.MEDIUM,
        Severity.LOW: Priority.LOW,
    }[severity]
    # One level, capped at `high`: only a severity still critical estimates `urgent`.
    raised = {
        Priority.URGENT: Priority.URGENT,
        Priority.HIGH: Priority.HIGH,
        Priority.MEDIUM: Priority.HIGH,
        Priority.LOW: Priority.MEDIUM,
    }[base]
    expected = raised if frequency is Frequency.CONSISTENT or occurrences >= 3 else base

    assert tickets.estimate(severity, frequency, occurrences) is expected
    line = tickets.estimate_line(severity, frequency, occurrences)
    assert line.startswith(f"- Priority estimate: {expected} (severity with the workaround ")
    assert f"; {occurrences} occurrence" in line
    assert tickets.parse_estimate_line(line) == (expected, severity, frequency, occurrences)


def test_the_estimate_line_says_why_it_was_raised_or_not() -> None:
    assert tickets.estimate_line(Severity.MEDIUM, Frequency.INTERMITTENT, 2) == (
        "- Priority estimate: medium (severity with the workaround medium; fires "
        "intermittently; 2 occurrences; not raised)"
    )
    assert tickets.estimate_line(Severity.LOW, None, 3) == (
        "- Priority estimate: medium (severity with the workaround low; frequency not "
        "judged; 3 occurrences; raised one level because it has 3 or more occurrences)"
    )
    assert tickets.estimate_line(Severity.HIGH, Frequency.CONSISTENT, 1) == (
        "- Priority estimate: high (severity with the workaround high; fires consistently; "
        "1 occurrence; already high, where a raise is capped, so not raised though it fires "
        "consistently)"
    )
    assert tickets.estimate_line(Severity.HIGH, None, 3) == (
        "- Priority estimate: high (severity with the workaround high; frequency not judged; "
        "3 occurrences; already high, where a raise is capped, so not raised though it has 3 "
        "or more occurrences)"
    )
    assert tickets.estimate_line(Severity.MEDIUM, Frequency.CONSISTENT, 3) == (
        "- Priority estimate: high (severity with the workaround medium; fires consistently; "
        "3 occurrences; raised one level because it fires consistently and it has 3 or more "
        "occurrences)"
    )
    assert tickets.estimate_line(Severity.CRITICAL, Frequency.CONSISTENT, 5) == (
        "- Priority estimate: urgent (severity with the workaround critical; fires "
        "consistently; 5 occurrences; already urgent, so not raised though it fires "
        "consistently and it has 3 or more occurrences)"
    )


@pytest.mark.parametrize(
    "line",
    [
        "- Priority estimate: high (severity with the workaround medium; fires "
        "intermittently; 2 occurrences; not raised)",
        "- Priority estimate: medium (severity with the workaround severe; fires "
        "intermittently; 2 occurrences; not raised)",
        "- Priority estimate: medium (severity with the workaround medium; often; 2 "
        "occurrences; not raised)",
        "- Priority estimate: medium",
    ],
    ids=["level-disagrees-with-its-facts", "no-severity", "no-frequency", "no-reason"],
)
def test_a_line_that_is_not_what_its_own_facts_render_is_no_estimate_line(line: str) -> None:
    assert tickets.parse_estimate_line(line) is None


def test_the_line_replaces_its_predecessor_or_follows_the_severity_lines_and_nothing_else() -> None:
    old = tickets.estimate_line(Severity.MEDIUM, Frequency.INTERMITTENT, 1)
    new = tickets.estimate_line(Severity.MEDIUM, Frequency.INTERMITTENT, 3)
    body = _body(Severity.MEDIUM, old)

    assert tickets.with_estimate_line(body, new) == body.replace(old, new)
    bare = _body(Severity.MEDIUM, None)
    added = tickets.with_estimate_line(bare, new)
    mitigated = "- Severity with the workaround: medium\n"
    assert added == bare.replace(mitigated, f"{mitigated}{new}\n")
    with pytest.raises(tickets.Refused, match="no severity to estimate a priority from"):
        tickets.with_estimate_line("## Root cause\n\nNo impact here.\n", new)


@pytest.mark.parametrize(
    ("held", "stored", "expected"),
    [
        (None, None, Priority.HIGH),
        (Priority.MEDIUM, Priority.MEDIUM, Priority.HIGH),
        (Priority.LOW, Priority.MEDIUM, Priority.LOW),
        (Priority.NONE, Priority.MEDIUM, Priority.NONE),
        (Priority.NONE, None, Priority.HIGH),
        (Priority.LOW, None, Priority.LOW),
    ],
    ids=[
        "no-item",
        "following",
        "held-by-a-person",
        "cleared-by-a-person",
        "schema-6-none",
        "schema-6-set",
    ],
)
def test_the_priority_follows_the_estimate_only_while_the_board_holds_what_was_stored(
    held: Priority | None, stored: Priority | None, expected: Priority
) -> None:
    assert tickets.follows_estimate(held, stored, Priority.HIGH) is expected


def test_only_other_runs_evidence_and_confirming_replies_count_as_occurrences(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = _copied(_write(drafts_root, _ticket(OTHER_RUN)), capsys)
    assert tickets.occurrences(issue, OTHER_RUN, CAUSE) == 1
    assert tickets.occurrences(None, OTHER_RUN, CAUSE) == 1, "a ticket with no item yet counts one"

    _evidence(issue, OTHER_RUN)  # the creating run's own evidence
    unmarked_reply = tickets.render_reply(
        RUN,
        CAUSE,
        answers="c-9",
        url="u",
        author=None,
        response="An old reply.",
        verdict=tickets.Verdict.CONFIRMS,
    ).replace(' verdict="confirms"', "")
    assert tickets.comment_owner(unmarked_reply) is not None
    _comment(issue, unmarked_reply)  # a reply without a verdict
    _reply(issue, RUN, tickets.Verdict.DOES_NOT_CONFIRM)
    _comment(
        issue,
        f"{tickets.DUPLICATE_OPENING.format(survivor='x', run=RUN)}\n\n"
        f"{tickets.DUPLICATE_MARKER.format(run=RUN, survivor='x')}\n",
    )
    _comment(issue, "Me too!\n", author="a-person")  # unmarked, a person's
    # Another run's evidence and a confirming reply, each marked for another root cause.
    _comment(issue, tickets.render_comment(RUN, "another-cause", "Something else."))
    _comment(
        issue,
        tickets.render_reply(
            LATER_RUN,
            "another-cause",
            answers="c-8",
            url="u",
            author=None,
            response="It confirms another cause.",
            verdict=tickets.Verdict.CONFIRMS,
        ),
    )

    assert tickets.occurrences(issue, OTHER_RUN, CAUSE) == 1, (
        "a comment that does not count counted"
    )

    _evidence(issue, RUN)
    _reply(issue, LATER_RUN, tickets.Verdict.CONFIRMS)

    assert tickets.occurrences(issue, OTHER_RUN, CAUSE) == 3


def test_a_comment_added_directly_to_the_board_is_counted_by_the_next_touch(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing stored stands in for the recount: a touch with no other change counts it."""
    path = _write(drafts_root, _ticket())
    issue = _copied(path, capsys)
    _evidence(issue, OTHER_RUN)
    _decided(path, capsys)
    first = tickets.read_ticket(path).body

    _evidence(issue, LATER_RUN)
    _decided(path, capsys)
    second = tickets.read_ticket(path)

    assert "; 2 occurrences; not raised)" in _line(first)
    assert _line(second.body) == tickets.estimate_line(Severity.MEDIUM, Frequency.INTERMITTENT, 3)
    assert second.priority_estimate is Priority.HIGH
    assert second.priority is Priority.HIGH, "the board still held the stored estimate"


def test_a_ticket_with_no_board_item_takes_its_estimate(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(drafts_root, _ticket(with_workaround=Severity.HIGH))
    written = tickets.render(_ticket(with_workaround=Severity.HIGH))
    assert 'priority: "none"' in written

    assert _decided(path, capsys) == "backlog\n"
    decided = tickets.read_ticket(path)
    assert decided.priority is decided.priority_estimate is Priority.HIGH
    issue = _copied(path, capsys)

    shown = _shown(issue)
    assert shown["priority"] == "high"
    assert _held(issue)[tickets.ESTIMATE_FIELD] == "high"


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"with_workaround": Severity.HIGH}, Priority.HIGH),
        ({"frequency": Frequency.CONSISTENT}, Priority.HIGH),
    ],
    ids=["impact-changed", "frequency-changed"],
)
def test_a_ticket_changed_after_its_board_status_is_copied_only_after_a_further_one(
    board: Path,
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    facts: dict[str, object],
    expected: Priority,
) -> None:
    """The task's one `board-status` per ticket, and the further one a later change owes.

    A ticket is written with no estimate, which `validate` refuses until `board-status` writes
    it. A change to its `## Impact` or `frequency` after that leaves the estimate line stale,
    so `validate` and the copy refuse it and nothing reaches the board, until a further
    `board-status` rewrites the estimate the copy then carries.
    """
    written = _ticket()
    body = re.sub(r"^- Priority estimate:[^\n]*\n", "", written.body, flags=re.MULTILINE)
    path = _write(drafts_root, written)
    _replace_record(path, priority_estimate=None)
    path.write_text(path.read_text(encoding="utf-8").replace(written.body, body), encoding="utf-8")
    assert tickets.main(["validate", str(path)]) == tickets.UNSOUND
    assert "record is missing priority_estimate" in " ".join(capsys.readouterr().err.split())
    assert _decided(path, capsys) == "backlog\n"
    assert tickets.main(["validate", str(path)]) == tickets.SOUND
    capsys.readouterr()

    _refacted(path, **facts)

    assert tickets.main(["validate", str(path)]) == tickets.UNSOUND
    assert "estimate line" in capsys.readouterr().err
    assert tickets.main(["copy", "--board", BOARD, str(path)]) == tickets.UNRUNNABLE
    capsys.readouterr()
    assert not any(board.rglob("*.md")), "a ticket whose estimate is stale reached the board"
    issue = _copied(path, capsys)
    assert _held(issue)[tickets.ESTIMATE_FIELD] == expected.value
    assert _shown(issue)["priority"] == expected.value
    assert f"- Priority estimate: {expected.value} (" in _line(_shown(issue)["content"])


def test_a_board_priority_at_the_stored_estimate_follows_a_changed_estimate(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(drafts_root, _ticket())
    issue = _copied(path, capsys)
    assert _shown(issue)["priority"] == "medium"

    _refacted(path, frequency=Frequency.CONSISTENT)
    _copied(path, capsys)

    assert _shown(issue)["priority"] == "high"
    assert _held(issue)[tickets.ESTIMATE_FIELD] == "high"


def test_a_persons_priority_survives_a_changed_estimate_while_the_new_one_is_stored(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(drafts_root, _ticket())
    issue = _copied(path, capsys)
    _person_sets(issue, Priority.LOW)

    _refacted(path, with_workaround=Severity.CRITICAL)
    _copied(path, capsys)

    assert _shown(issue)["priority"] == "low", "a person's priority was rewritten"
    assert _held(issue)[tickets.ESTIMATE_FIELD] == "urgent"
    assert "- Priority estimate: urgent (" in _line(_shown(issue)["content"])
    assert tickets.read_ticket(path).priority is Priority.LOW


def test_a_person_setting_the_priority_back_to_the_estimate_hands_it_back(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(drafts_root, _ticket())
    issue = _copied(path, capsys)
    _person_sets(issue, Priority.LOW)
    _refacted(path, with_workaround=Severity.CRITICAL)
    _copied(path, capsys)
    assert _shown(issue)["priority"] == "low"

    _person_sets(issue, Priority.URGENT)
    _refacted(path, with_workaround=Severity.HIGH)
    _copied(path, capsys)

    assert _shown(issue)["priority"] == "high", "the priority did not follow once handed back"
    assert _held(issue)[tickets.ESTIMATE_FIELD] == "high"


@pytest.mark.parametrize(
    ("person", "expected"),
    [(None, Priority.MEDIUM), (Priority.LOW, Priority.LOW)],
    ids=["priority-none-follows", "priority-set-is-left-alone"],
)
def test_a_schema_6_item_follows_only_while_its_priority_is_none(
    board: Path,
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    person: Priority | None,
    expected: Priority,
) -> None:
    """A schema-6 item stored no estimate, so only a priority of `none` is one to follow.

    The item is put on the board the way a schema-6 run left it — its own ticket copied by
    the store — and the local ticket is read by `board-status` still at schema 6, then
    brought to schema 7 once its frequency is recorded.
    """
    older = _ticket(frequency=None)
    path = _write(drafts_root, older, _schema_6(older))
    run, cause = tickets.located_path(path)
    copied = plan_store.sdk(
        plan_store.client().task_copy([tickets.qualified_id(run, cause)], to=BOARD)
    )
    destination = copied.items[0].root.destination
    assert destination is not None
    issue = destination.model_dump()
    assert _held(issue)["schema"] == tickets.UNESTIMATED_SCHEMA
    if person is not None:
        _person_sets(issue, person)

    assert _decided(path, capsys) == "backlog\n", "a schema-6 ticket was not read"
    assert tickets.main(["validate", str(path)]) == tickets.UNSOUND
    assert "carries no `frequency`" in capsys.readouterr().err
    _refacted(path, frequency=Frequency.INTERMITTENT)
    _copied(path, capsys)

    assert _shown(issue)["priority"] == expected.value
    assert _held(issue)["schema"] == tickets.SCHEMA
    assert _held(issue)[tickets.ESTIMATE_FIELD] == "medium"


def _rubric_ticket(
    severity: Severity, with_workaround: Severity, frequency: Frequency | None
) -> tickets.Ticket:
    """A current ticket whose two severities are ``severity`` and ``with_workaround``.

    Its workaround is acceptable — it states the three costs — where the two differ, and
    `none` where they are equal, as the rubric's workaround rule requires.
    """
    workaround = "none" if severity is with_workaround else "adopt and settle the run first"
    impact = tickets.impact_section(
        impact_prose(OUTCOME, workaround=workaround), severity, workaround, with_workaround
    )
    ticket = _ticket(frequency=frequency)
    held = ticket.body.split("## Impact\n\n", 1)[1].split("\n\n## ", 1)[0]
    return dataclasses.replace(ticket, body=ticket.body.replace(held, impact.strip()))


def test_the_approved_625_facts_estimate_medium_and_a_persons_priority_stands(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """onepipeline#625 as the user approved it: raw `high`, `low` with the workaround,
    firing consistently, seen twice — estimated `medium`, one level for its consistency.

    Driven through `board-status`, `validate` and `copy`, the commands an agent runs, and
    then a person's priority on the item survives the next copy.
    """
    path = _write(drafts_root, _rubric_ticket(Severity.HIGH, Severity.LOW, Frequency.CONSISTENT))
    issue = _copied(path, capsys)
    _evidence(issue, LATER_RUN)

    _copied(path, capsys)

    assert _line(_shown(issue)["content"]) == (
        "- Priority estimate: medium (severity with the workaround low; fires consistently; "
        "2 occurrences; raised one level because it fires consistently)"
    )
    assert _shown(issue)["priority"] == "medium"
    assert _held(issue)[tickets.ESTIMATE_FIELD] == "medium"
    _person_sets(issue, Priority.URGENT)
    _copied(path, capsys)
    assert _shown(issue)["priority"] == "urgent", "a person's priority was rewritten"
    assert _held(issue)[tickets.ESTIMATE_FIELD] == "medium"


@pytest.mark.parametrize(
    ("with_workaround", "frequency", "others", "expected", "why"),
    [
        (Severity.HIGH, Frequency.INTERMITTENT, 1, Priority.HIGH, "not raised"),
        (
            Severity.HIGH,
            Frequency.INTERMITTENT,
            2,
            Priority.HIGH,
            "already high, where a raise is capped, so not raised though it has 3 or more "
            "occurrences",
        ),
        (
            Severity.HIGH,
            Frequency.CONSISTENT,
            2,
            Priority.HIGH,
            "already high, where a raise is capped, so not raised though it fires consistently "
            "and it has 3 or more occurrences",
        ),
        (Severity.CRITICAL, Frequency.INTERMITTENT, 1, Priority.URGENT, "not raised"),
        (
            Severity.CRITICAL,
            Frequency.CONSISTENT,
            2,
            Priority.URGENT,
            "already urgent, so not raised though it fires consistently and it has 3 or more "
            "occurrences",
        ),
        (Severity.MEDIUM, Frequency.INTERMITTENT, 1, Priority.MEDIUM, "not raised"),
        (
            Severity.MEDIUM,
            Frequency.INTERMITTENT,
            2,
            Priority.HIGH,
            "raised one level because it has 3 or more occurrences",
        ),
        (
            Severity.MEDIUM,
            Frequency.CONSISTENT,
            2,
            Priority.HIGH,
            "raised one level because it fires consistently and it has 3 or more occurrences",
        ),
    ],
    ids=[
        "high-2-occurrences",
        "high-3-occurrences-capped",
        "high-both-raises-capped",
        "critical-2-occurrences",
        "critical-both-raises-urgent",
        "medium-2-occurrences",
        "medium-3-occurrences-raised",
        "medium-both-raises-once",
    ],
)
def test_board_status_caps_a_raise_at_high_and_only_a_critical_residual_is_urgent(  # noqa: PLR0913 - one case's facts
    board: Path,
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    with_workaround: Severity,
    frequency: Frequency,
    others: int,
    expected: Priority,
    why: str,
) -> None:
    """The ticket's own evidence plus ``others`` runs' comments, recounted by `board-status`."""
    path = _write(drafts_root, _rubric_ticket(with_workaround, with_workaround, frequency))
    issue = _copied(path, capsys)
    for run in (LATER_RUN, OTHER_RUN)[:others]:
        _evidence(issue, run)

    _copied(path, capsys)

    occurrences = 1 + others
    assert _line(_shown(issue)["content"]) == (
        f"- Priority estimate: {expected} (severity with the workaround {with_workaround}; "
        f"{'fires consistently' if frequency is Frequency.CONSISTENT else 'fires intermittently'}"
        f"; {occurrences} occurrences; {why})"
    )
    assert _shown(issue)["priority"] == expected.value
    assert tickets.read_ticket(path).priority_estimate is expected


def test_board_status_prints_the_status_alone_whatever_it_writes(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(drafts_root, _ticket())
    issue = _copied(path, capsys)
    tickets.main(["board-status", "--board", BOARD, "--withdraw", str(path)])
    assert capsys.readouterr().out == "cancelled\n"
    _person_sets(issue, Priority.URGENT)

    assert tickets.main(["board-status", "--board", BOARD, str(path)]) == tickets.SOUND
    assert capsys.readouterr().out == "backlog\n"


def _owned_elsewhere(
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    schema_6: bool = False,
    schema: int = tickets.SCHEMA,
) -> str:
    """An item another run filed, at `todo`, carrying a metadata key of its own: its id.

    An item of an older ``schema`` is copied by the store itself, as that schema's run left
    it; ``schema_6`` is that for schema 6.
    """
    schema = tickets.UNESTIMATED_SCHEMA if schema_6 else schema
    older = _ticket(
        OTHER_RUN,
        frequency=None if schema < tickets.ESTIMATE_AT else Frequency.INTERMITTENT,
        structured=schema >= tickets.STRUCTURE_AT,
    )
    if schema != tickets.SCHEMA:
        path = _write(drafts_root, older, _older(older, schema))
        run, cause = tickets.located_path(path)
        copied = plan_store.sdk(
            plan_store.client().task_copy([tickets.qualified_id(run, cause)], to=BOARD)
        )
        destination = copied.items[0].root.destination
        assert destination is not None
        issue = str(destination.model_dump())
    else:
        issue = _copied(_write(drafts_root, older), capsys)
    plan_store.sdk(plan_store.client().task_metadata_set(issue, "x.kept", '{"by": "a person"}'))
    plan_store.sdk(plan_store.client().task_status_set(issue, "todo"))
    return issue


def _re_estimated(issue: str, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    status = tickets.main(["re-estimate", "--board", BOARD, issue])
    captured = capsys.readouterr()
    assert status == tickets.SOUND, captured.err
    printed: object = json.loads(captured.out)
    assert isinstance(printed, dict)
    return printed


def _metadata_but_the_record(qualified: str) -> dict[str, object]:
    metadata = _shown(qualified)["metadata"]
    assert isinstance(metadata, dict)
    return {key: value for key, value in metadata.items() if key != tickets.KEY}


#: Every schema `re-estimate` reads, as the item's record declares it, and what it writes.
RE_ESTIMATED_SCHEMAS = pytest.mark.parametrize(
    ("schema", "written"),
    [
        (tickets.SCHEMA, tickets.SCHEMA),
        (tickets.PRIOR_SCHEMA, tickets.PRIOR_SCHEMA),
        (tickets.UNESTIMATED_SCHEMA, tickets.ESTIMATE_AT),
    ],
    ids=["schema-8", "schema-7", "schema-6"],
)


@RE_ESTIMATED_SCHEMAS
def test_re_estimate_raises_a_following_priority_and_changes_only_the_estimate(
    board: Path,
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    schema: int,
    written: int,
) -> None:
    """A schema-7 item keeps its schema: restructuring its body is its own run's to do."""
    schema_6 = schema == tickets.UNESTIMATED_SCHEMA
    issue = _owned_elsewhere(drafts_root, capsys, schema=schema)
    if schema_6:
        _person_sets(issue, Priority.NONE)
    before = _shown(issue)
    kept = _metadata_but_the_record(issue)
    _evidence(issue, RUN)
    _evidence(issue, LATER_RUN)

    printed = _re_estimated(issue, capsys)

    after = _shown(issue)
    line = tickets.estimate_line(Severity.MEDIUM, None if schema_6 else Frequency.INTERMITTENT, 3)
    assert printed == {
        "item": issue,
        "priority_estimate": "high",
        "occurrences": 3,
        "priority": "high",
    }
    assert _held(issue)["schema"] == written
    assert _held(issue)[tickets.ESTIMATE_FIELD] == "high"
    content = before["content"]
    assert isinstance(content, str)
    if schema_6:
        mitigated = "- Severity with the workaround: medium\n"
        assert after["content"] == content.replace(mitigated, f"{mitigated}{line}\n", 1)
    else:
        assert after["content"] == content.replace(_line(content), line)
    assert _metadata_but_the_record(issue) == kept
    assert after["status"] == before["status"] == {"category": "todo", "name": "todo"}
    assert after["priority"] == "high"
    assert tickets.board_estimate_problems(issue) == []

    again = _re_estimated(issue, capsys)
    assert again == printed
    assert _shown(issue) == after, "a re-estimate with nothing new to count wrote something"


@RE_ESTIMATED_SCHEMAS
def test_re_estimate_leaves_a_persons_priority_and_still_moves_the_estimate(
    board: Path,
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    schema: int,
    written: int,
) -> None:
    issue = _owned_elsewhere(drafts_root, capsys, schema=schema)
    _person_sets(issue, Priority.LOW)
    _evidence(issue, RUN)
    _reply(issue, LATER_RUN, tickets.Verdict.CONFIRMS)

    printed = _re_estimated(issue, capsys)

    assert printed["priority"] == "low"
    assert _shown(issue)["priority"] == "low"
    assert _held(issue)[tickets.ESTIMATE_FIELD] == "high"
    assert _held(issue)["schema"] == written
    assert "; 3 occurrences; raised one level" in _line(_shown(issue)["content"])


@pytest.mark.parametrize(
    ("origin", "refused"),
    [
        ("another-tickets-item", "which is not the ticket its record describes"),
        ("an-item-created-by-hand", "it carries no `onetaskgraph.origin`"),
        ("an-item-naming-itself", "which is no `drafts` ticket"),
    ],
    ids=["another-tickets-item", "an-item-created-by-hand", "an-item-naming-itself"],
)
def test_re_estimate_refuses_an_item_not_copied_from_the_ticket_its_record_describes(
    board: Path,
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    origin: str,
    refused: str,
) -> None:
    """The item is written as that ticket, so it has to be the item copied from it."""
    issue = _owned_elsewhere(drafts_root, capsys)
    _evidence(issue, RUN)
    if origin == "another-tickets-item":
        # The store owns its origin, so the record is what can come to describe another ticket.
        record = _held(issue) | {"root_cause": "another-cause"}
        plan_store.sdk(
            plan_store.client().task_metadata_set(issue, tickets.KEY, json.dumps(record))
        )
    elif origin == "an-item-naming-itself":
        # An origin naming the item its own record is bound to reads as a ticket's own origin,
        # which is sound on a ticket and never on the board item copied from one.
        record = _held(issue)
        native = issue.removeprefix(f"{BOARD}:")
        plan_store.sdk(
            plan_store.client().task_metadata_set(
                issue, tickets.KEY, json.dumps(record | {tickets.BINDING_FIELD: native})
            )
        )
        ticket = tickets.qualified_id(str(record["created_by_run"]), str(record["root_cause"]))
        (copied,) = [path for path in board.rglob("*.md") if ticket in path.read_text()]
        copied.write_text(copied.read_text(encoding="utf-8").replace(ticket, issue), "utf-8")
        assert _shown(issue)["metadata"][tickets.ORIGIN_KEY] == issue
    else:
        # A person writes an item on the local board by hand, as a copy of this one's file
        # carrying no origin, since no copy made it.
        (copied,) = [path for path in board.rglob("*.md") if tickets.ORIGIN_KEY in path.read_text()]
        by_hand = copied.with_name("by-hand.md")
        by_hand.write_text(
            "".join(
                line
                for line in copied.read_text(encoding="utf-8").splitlines(keepends=True)
                if tickets.ORIGIN_KEY not in line
            ),
            encoding="utf-8",
        )
        issue = f"{BOARD}:{by_hand.relative_to(board / TASKS_DIRECTORY).with_suffix('').as_posix()}"
    before = _shown(issue)

    assert tickets.main(["re-estimate", "--board", BOARD, issue]) == tickets.UNSOUND
    said = " ".join(capsys.readouterr().err.split())
    assert f"{issue} is not the item copied from the ticket its" in said
    assert refused in said, said
    assert _shown(issue) == before, "a re-estimate wrote to an item it had refused"


def test_re_estimate_refuses_an_item_it_cannot_estimate(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = _owned_elsewhere(drafts_root, capsys)
    elsewhere = tickets.main(["re-estimate", "--board", "another-board", issue])
    assert elsewhere == tickets.UNSOUND
    assert "is not an item of the board 'another-board'" in capsys.readouterr().err
    held = json.dumps(_held(issue))
    plan_store.sdk(plan_store.client().task_metadata_set(issue, tickets.KEY, '{"schema": 5}'))
    assert tickets.main(["re-estimate", "--board", BOARD, issue]) == tickets.UNSOUND
    refused = " ".join(capsys.readouterr().err.split())
    assert "record of schema 5, and this reads schema 6, 7 or 8" in refused
    plan_store.sdk(plan_store.client().task_metadata_set(issue, tickets.KEY, '{"schema": 7}'))
    assert tickets.main(["re-estimate", "--board", BOARD, issue]) == tickets.UNSOUND
    assert "no follow-up ticket this can estimate" in capsys.readouterr().err
    plan_store.sdk(plan_store.client().task_metadata_set(issue, tickets.KEY, held))

    unimpacted = _owned_elsewhere(drafts_root, capsys)
    with_no_impact = drafts_root / "no-impact.md"
    with_no_impact.write_text("## Root cause\n\nNothing about impact.\n", encoding="utf-8")
    plan_store.sdk(plan_store.client().task_content_set(unimpacted, file=str(with_no_impact)))
    assert tickets.main(["re-estimate", "--board", BOARD, unimpacted]) == tickets.UNSOUND
    assert "states no `- Severity with the workaround:` severity" in capsys.readouterr().err

    missing = tickets.main(["re-estimate", "--board", BOARD, f"{BOARD}:no/such/item"])
    assert missing == tickets.UNRUNNABLE

    unsound = _owned_elsewhere(drafts_root, capsys)
    record = _held(unsound) | {"host": "not a host!"}
    plan_store.sdk(plan_store.client().task_metadata_set(unsound, tickets.KEY, json.dumps(record)))
    before = _shown(unsound)
    assert tickets.main(["re-estimate", "--board", BOARD, unsound]) == tickets.UNSOUND
    assert "record is not sound: `host` 'not a host!' is not a hostname" in (
        capsys.readouterr().err
    )
    assert _shown(unsound) == before, "a re-estimate wrote from a record it had refused"


@pytest.mark.parametrize(
    ("malformed", "refused"),
    [
        (lambda content, line: content.replace(line, f"{line}\n{line}"), "## Impact` section"),
        (
            lambda content, _line: content.replace(
                "- Severity: medium\n", "- Severity: medium\n- Severity: medium\n"
            ),
            "## Impact` section",
        ),
        (lambda content, _line: content.replace("- Workaround: none\n", ""), "## Impact` section"),
        (
            lambda content, _line: content.replace("## Suggested fix", "## Something else"),
            "carries no `## Suggested fix` heading in its place",
        ),
        (
            lambda content, _line: re.sub(
                r"## Owning runs\n.*", "## Owning runs\n", content, flags=re.DOTALL
            ),
            "## Owning runs` section is empty",
        ),
    ],
    ids=[
        "two-estimate-lines",
        "a-repeated-severity-line",
        "no-workaround-line",
        "a-missing-section",
        "an-empty-section",
    ],
)
def test_re_estimate_refuses_a_body_it_would_otherwise_write_back_malformed(
    board: Path,
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    malformed: Callable[[str, str], str],
    refused: str,
) -> None:
    """The whole content is written back, so all of it is held to a ticket's shape first."""
    issue = _owned_elsewhere(drafts_root, capsys)
    content = _shown(issue)["content"]
    assert isinstance(content, str)
    edited = drafts_root / "malformed.md"
    edited.write_text(malformed(content, _line(content)), encoding="utf-8")
    plan_store.sdk(plan_store.client().task_content_set(issue, file=str(edited)))
    _evidence(issue, RUN)
    before = _shown(issue)

    assert tickets.main(["re-estimate", "--board", BOARD, issue]) == tickets.UNSOUND
    said = " ".join(capsys.readouterr().err.split())
    assert f"{issue}'s content is not sound: the body" in said
    assert refused in said, said
    assert _shown(issue) == before, "a malformed body was written back"


@pytest.mark.parametrize(
    "schema",
    [0, -1, tickets.SCHEMA + 1, "7", 7.0, True, None],
    ids=["zero", "a-negative-schema", "a-later-schema", "a-word", "a-float", "a-boolean", "none"],
)
def test_board_status_refuses_a_bound_items_record_of_no_schema_it_reads(
    board: Path,
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    schema: object,
) -> None:
    """Read as an older record, it would hand a person's priority back to the estimate."""
    path = _write(drafts_root, _ticket())
    issue = _copied(path, capsys)
    _person_sets(issue, Priority.LOW)
    record = _held(issue) | {"schema": schema}
    plan_store.sdk(plan_store.client().task_metadata_set(issue, tickets.KEY, json.dumps(record)))

    assert tickets.main(["board-status", "--board", BOARD, str(path)]) == tickets.UNRUNNABLE
    refused = " ".join(capsys.readouterr().err.split())
    assert f"record of schema {schema!r}, which no follow-up tool reads" in refused
    assert tickets.main(["re-estimate", "--board", BOARD, issue]) == tickets.UNSOUND
    refused = " ".join(capsys.readouterr().err.split())
    assert f"record of schema {schema!r}, and this reads schema 6, 7 or 8" in refused
    assert _shown(issue)["priority"] == "low"


def test_the_priority_vocabulary_is_the_stores_own() -> None:
    """The levels a ticket carries are the pinned store's `Priority`, word for word and in order.

    The SDK's enum is generated from the store's own schema at the release the lock pins, so a
    release that added, dropped or reordered a level fails here rather than in a copy.
    """
    store = [one.value for one in onetaskgraph_sdk.Priority]
    assert {one.value for one in Priority} == set(store)
    assert [one.value for one in tickets.ESTIMATES] == [one for one in store if one != "none"]


def test_a_priority_the_store_could_not_have_reported_is_refused_by_name() -> None:
    """The store's typed answer holds a priority to its vocabulary, and so does this reader."""
    assert tickets.held_priority({"priority": "high"}) is Priority.HIGH
    assert tickets.held_priority({}) is Priority.NONE
    with pytest.raises(OSError, match="the store reported the priority 'soon'"):
        tickets.held_priority({"priority": "soon"})


@pytest.mark.parametrize("stored", [None, "critical"], ids=["no-estimate", "no-level"])
def test_a_schema_7_item_storing_no_estimate_is_refused_rather_than_read_as_schema_6(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str], stored: str | None
) -> None:
    """Read as schema 6, a hand-edited record would hand a person's priority to the estimate."""
    path = _write(drafts_root, _ticket())
    issue = _copied(path, capsys)
    _person_sets(issue, Priority.LOW)
    record = {key: value for key, value in _held(issue).items() if key != "priority_estimate"}
    if stored is not None:
        record["priority_estimate"] = stored
    plan_store.sdk(plan_store.client().task_metadata_set(issue, tickets.KEY, json.dumps(record)))
    before = _shown(issue)

    assert tickets.main(["board-status", "--board", BOARD, str(path)]) == tickets.UNRUNNABLE
    refused = " ".join(capsys.readouterr().err.split())
    assert f"storing the `priority_estimate` {stored!r}, which is no estimate" in refused
    assert tickets.main(["re-estimate", "--board", BOARD, issue]) == tickets.UNSOUND
    refused = " ".join(capsys.readouterr().err.split())
    assert (
        "which is no estimate `board-status` or `re-estimate` writes" in refused
        if stored is None
        else "record is not sound: `priority_estimate` 'critical' is not one of" in refused
    ), refused
    assert _shown(issue) == before, "a refused read wrote something"


def test_a_frequency_that_is_no_judgment_is_refused_rather_than_read_as_absent(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stored `frequency` outside the vocabulary would quietly drop a consistent raise."""
    issue = _owned_elsewhere(drafts_root, capsys)
    _write(drafts_root, _ticket(), None)
    _evidence(issue, RUN)
    _filed_account(drafts_root)
    _re_estimated(issue, capsys)
    record = _held(issue) | {tickets.FREQUENCY_FIELD: "often"}
    plan_store.sdk(plan_store.client().task_metadata_set(issue, tickets.KEY, json.dumps(record)))
    before = _shown(issue)

    assert tickets.main(["re-estimate", "--board", BOARD, issue]) == tickets.UNSOUND
    refused = " ".join(capsys.readouterr().err.split())
    assert "record is not sound: `frequency` 'often' is not one of" in refused
    assert _shown(issue) == before, "a refused re-estimate wrote something"
    status, refused = _dispositions(drafts_root, capsys)
    assert status == tickets.UNSOUND
    assert "carries the `frequency` 'often', which is not one of" in refused


def _replace_record(path: Path, **changes: object) -> None:
    """Rewrite the record in the ticket at ``path``, a `None` value dropping its key."""
    held = tickets.read_ticket(path, pending=True)
    record = tickets.record(held)
    for key, value in changes.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    path.write_text(
        frontmatter(
            {
                "title": held.title,
                "status": held.status.value,
                "priority": held.priority.value,
                "repositories": [held.repository],
                "metadata": {tickets.KEY: record},
            },
            held.body,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"priority_estimate": None}, "record is missing priority_estimate"),
        ({"priority_estimate": "soon"}, "`priority_estimate` 'soon' is not one of"),
        ({"priority_estimate": "high"}, "estimate line states `medium`, where the record's"),
        ({"frequency": "sometimes"}, "`frequency` 'sometimes' is not one of"),
        ({"frequency": None}, "carries no `frequency`, and a ticket about to be copied"),
    ],
    ids=[
        "missing-estimate",
        "mistyped-estimate",
        "line-disagrees",
        "mistyped-frequency",
        "no-freq",
    ],
)
def test_validate_refuses_a_ticket_whose_estimate_or_frequency_is_not_its_own(
    drafts_root: Path, capsys: pytest.CaptureFixture[str], changes: dict[str, object], reason: str
) -> None:
    path = _write(drafts_root, _ticket())
    assert tickets.main(["validate", str(path)]) == tickets.SOUND
    capsys.readouterr()
    _replace_record(path, **changes)

    assert tickets.main(["validate", str(path)]) == tickets.UNSOUND
    assert reason in " ".join(capsys.readouterr().err.split())


def test_validate_refuses_a_front_matter_priority_the_store_would_not_carry(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(drafts_root, _ticket())
    path.write_text(
        path.read_text(encoding="utf-8").replace('priority: "none"', 'priority: "soonish"'),
        encoding="utf-8",
    )

    assert tickets.main(["validate", str(path)]) == tickets.UNSOUND
    assert "soonish" in capsys.readouterr().err


def test_validate_refuses_an_estimate_line_counting_more_than_any_count_reaches(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A digit run past what a count reaches is a line refused, never a conversion that raises."""
    path = _write(drafts_root, _ticket())
    text = path.read_text(encoding="utf-8")
    counted = re.search(r"; (1) occurrence;", text)
    assert counted is not None, text
    path.write_text(
        text[: counted.start(1)] + "9" * 5000 + " occurrences;" + text[counted.end() :],
        encoding="utf-8",
    )

    assert tickets.main(["validate", str(path)]) == tickets.UNSOUND
    assert "line is not as `board-status` renders it" in capsys.readouterr().err


def _filed_account(root: Path) -> None:
    """This run's account filing one draft under its ticket for :data:`CAUSE`."""
    draft = f"drafts:{RUN}/drafts/a-draft"
    path = tickets.dispositions_path(root, RUN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": tickets.DISPOSITIONS_SCHEMA,
                "run": RUN,
                "drafts": [draft],
                "dispositions": [
                    {
                        "draft": draft,
                        "disposition": "filed",
                        "root_causes": [CAUSE],
                        "detail": "It holds.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _dispositions(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    status = tickets.main(["check-dispositions", "--root", str(root), "--board", BOARD, RUN])
    return status, " ".join(capsys.readouterr().err.split())


def test_check_dispositions_holds_a_copied_tickets_item_to_its_recount_and_its_priority(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(drafts_root, _ticket())
    issue = _copied(path, capsys)
    _filed_account(drafts_root)
    assert _dispositions(drafts_root, capsys)[0] == tickets.SOUND

    _person_sets(issue, Priority.LOW)
    status, refused = _dispositions(drafts_root, capsys)
    assert status == tickets.UNSOUND
    assert f"{issue} holds the priority `low`, where the ticket this run copied onto it" in refused

    _copied(path, capsys)
    assert _dispositions(drafts_root, capsys)[0] == tickets.SOUND
    _evidence(issue, OTHER_RUN)
    _evidence(issue, LATER_RUN)
    status, refused = _dispositions(drafts_root, capsys)
    assert status == tickets.UNSOUND
    assert f"{issue}'s record stores the estimate 'medium', where its 3 occurrences" in refused
    assert f"{issue}'s `## Impact` estimate line reads" in refused


def test_check_dispositions_refuses_evidence_left_on_a_schema_6_item_it_did_not_re_estimate(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = _owned_elsewhere(drafts_root, capsys, schema_6=True)
    _write(drafts_root, _ticket(), None)
    _evidence(issue, RUN)
    _filed_account(drafts_root)

    status, refused = _dispositions(drafts_root, capsys)
    assert status == tickets.UNSOUND
    assert (
        f"{issue} carries no `orchestrator.follow-up` record of schema 7 or later: it declares "
        "schema 6"
    ) in refused
    assert "run `re-estimate` on it" in refused

    _re_estimated(issue, capsys)
    assert _dispositions(drafts_root, capsys)[0] == tickets.SOUND

    no_creator = _held(issue) | {"created_by_run": ""}
    plan_store.sdk(
        plan_store.client().task_metadata_set(issue, tickets.KEY, json.dumps(no_creator))
    )
    status, refused = _dispositions(drafts_root, capsys)
    assert status == tickets.UNSOUND
    assert (
        f"{issue} carries no `orchestrator.follow-up` record of schema 7 or later with a severity"
    ) in refused


def test_check_dispositions_holds_another_runs_item_to_the_line_its_record_renders(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = _owned_elsewhere(drafts_root, capsys)
    _write(drafts_root, _ticket(), None)
    _evidence(issue, RUN)
    _filed_account(drafts_root)
    _re_estimated(issue, capsys)
    assert _dispositions(drafts_root, capsys)[0] == tickets.SOUND

    content = _shown(issue)["content"]
    assert isinstance(content, str)
    stale = drafts_root / "stale.md"
    stale.write_text(
        content.replace(_line(content), tickets.estimate_line(Severity.MEDIUM, None, 2)),
        encoding="utf-8",
    )
    plan_store.sdk(plan_store.client().task_content_set(issue, file=str(stale)))

    status, refused = _dispositions(drafts_root, capsys)
    assert status == tickets.UNSOUND
    assert f"{issue}'s `## Impact` estimate line reads" in refused
    assert "frequency not judged" in refused


def _gathered(root: Path, issue: str, asked: str) -> Path:
    """A gathering quoting the one comment ``asked`` on ``issue``, as the gatherer writes it."""
    item = _shown(issue)
    (held,) = [
        one
        for one in plan_store.sdk(plan_store.client().task_comment_list(issue)).comments
        if one.id.model_dump() == asked
    ]
    location = item.get("location")
    path = location.get("path") if isinstance(location, dict) else None
    url = comments.comment_url_parts(
        None, path if isinstance(path, str) else None, asked, held.url, issue
    )
    changed = comments.moment(held.updated_at or held.created_at, "the comment").strftime(
        comments.MOMENT_FORMAT
    )
    directory = root / "feedback" / RUN
    directory.mkdir(parents=True, exist_ok=True)
    feedback = directory / "20260101T000000Z.md"
    feedback.write_text(
        comments.render(RUN, BOARD, [], None)
        + f"{tickets.QUOTED_COMMENT_HEADING}1: on `{issue}`\n\n"
        + f"{tickets.quoted_comment(issue, asked)}\n\n"
        + f"- Comment id: {asked}\n- URL: {url}\n- Author: {held.author}\n"
        + f"- Last changed: {changed}\n- Issue title: {item['title']}\n\n"
        + f"````text\n{held.body.rstrip()}\n````\n",
        encoding="utf-8",
    )
    return feedback


def _account(
    feedback: Path,
    issue: str,
    asked: str,
    reply: str,
    verdict: object,
    schema: int = tickets.RESPONSES_SCHEMA,
) -> None:
    response: dict[str, object] = {
        "comment": asked,
        "issue": issue,
        "action": "Nothing to change; it confirms the root cause.",
        "reply": reply,
    }
    if verdict is not None:
        response["verdict"] = verdict
    tickets.responses_path(feedback).write_text(
        json.dumps(
            {
                "schema": schema,
                "run": RUN,
                "feedback": feedback.name,
                "responses": [response],
            }
        ),
        encoding="utf-8",
    )


def _responses(feedback: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    status = tickets.main(["check-responses", "--board", BOARD, "--feedback", str(feedback), RUN])
    return status, " ".join(capsys.readouterr().err.split())


def test_check_responses_holds_each_verdict_to_its_marker_and_a_confirmation_to_its_recount(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = _copied(_write(drafts_root, _ticket()), capsys)
    _evidence(issue, OTHER_RUN)
    asked = _comment(issue, "Seen again on page 12.\n", author="a-person")
    feedback = _gathered(drafts_root, issue, asked)
    reply = _comment(
        issue,
        tickets.render_reply(
            RUN,
            CAUSE,
            answers=asked,
            url="u",
            author="a-person",
            response="It confirms the cause.",
            verdict=tickets.Verdict.CONFIRMS,
        ),
    )

    _account(feedback, issue, asked, reply, None)
    status, refused = _responses(feedback, capsys)
    assert status == tickets.UNSOUND
    assert "entry 0 is missing keys: verdict" in refused

    _account(feedback, issue, asked, reply, "maybe")
    status, refused = _responses(feedback, capsys)
    assert status == tickets.UNSOUND
    assert "states the verdict 'maybe', where every response states one of" in refused

    _account(feedback, issue, asked, reply, "does-not-confirm")
    status, refused = _responses(feedback, capsys)
    assert status == tickets.UNSOUND
    assert "states the verdict `does-not-confirm`, and its reply" in refused
    assert "marker states `confirms`" in refused

    _account(feedback, issue, asked, reply, "confirms")
    status, refused = _responses(feedback, capsys)
    assert status == tickets.UNSOUND, "a confirmation left its issue's estimate as it was"
    assert f"{issue}'s record stores the estimate 'medium', where its 3 occurrences" in refused

    _re_estimated(issue, capsys)
    assert _responses(feedback, capsys)[0] == tickets.SOUND

    record = _held(issue) | {tickets.ESTIMATE_FIELD: "high"}
    content = _shown(issue)["content"]
    assert isinstance(content, str)
    stale = drafts_root / "stale.md"
    stale.write_text(
        content.replace(
            _line(content), tickets.estimate_line(Severity.MEDIUM, Frequency.CONSISTENT, 3)
        ),
        encoding="utf-8",
    )
    plan_store.sdk(plan_store.client().task_content_set(issue, file=str(stale)))
    assert _held(issue) == record
    status, refused = _responses(feedback, capsys)
    assert status == tickets.UNSOUND
    assert f"{issue}'s `## Impact` estimate line reads" in refused
    assert "fires consistently" in refused


def test_check_responses_refuses_a_verdict_its_legacy_reply_marker_does_not_state(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reply posted before markers carried a verdict states none, which no account matches."""
    issue = _copied(_write(drafts_root, _ticket()), capsys)
    asked = _comment(issue, "Not seen here.\n", author="a-person")
    feedback = _gathered(drafts_root, issue, asked)
    rendered = tickets.render_reply(
        RUN,
        CAUSE,
        answers=asked,
        url="u",
        author="a-person",
        response="It does not confirm the cause.",
        verdict=tickets.Verdict.DOES_NOT_CONFIRM,
    )
    legacy = rendered.replace(' verdict="does-not-confirm"', "")
    assert legacy != rendered, "the marker carried no verdict attribute to strip"
    reply = _comment(issue, legacy)
    assert tickets.comment_owner(legacy) is not None, "the legacy marker names no run"

    _account(feedback, issue, asked, reply, "does-not-confirm")
    status, refused = _responses(feedback, capsys)
    assert status == tickets.UNSOUND
    assert "states the verdict `does-not-confirm`, and its reply" in refused
    assert "marker states none" in refused


def test_check_responses_refuses_an_account_written_to_the_schema_before_verdicts(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A schema-1 account states no verdict, so it answers nothing the current check reads.

    It is refused by its schema rather than read for what it lacks, whatever its replies say.
    """
    issue = _copied(_write(drafts_root, _ticket()), capsys)
    asked = _comment(issue, "Seen again.\n", author="a-person")
    feedback = _gathered(drafts_root, issue, asked)
    reply = _comment(
        issue,
        tickets.render_reply(
            RUN,
            CAUSE,
            answers=asked,
            url="u",
            author="a-person",
            response="It does not confirm the cause.",
            verdict=tickets.Verdict.DOES_NOT_CONFIRM,
        ),
    )
    prior = tickets.RESPONSES_SCHEMA - 1
    assert prior == 1

    _account(feedback, issue, asked, reply, None, schema=prior)
    status, refused = _responses(feedback, capsys)
    assert status == tickets.UNSOUND
    assert f"its `schema` is {prior}, and this reads schema {tickets.RESPONSES_SCHEMA}" in refused

    _account(feedback, issue, asked, reply, "does-not-confirm")
    assert _responses(feedback, capsys)[0] == tickets.SOUND


def test_check_responses_refuses_a_confirmation_whose_marker_names_another_root_cause(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reply naming another root cause is no occurrence of this one, so it confirms nothing.

    Once the issue is re-estimated from what its comments do count, the account passes only
    with the marker naming the root cause the issue's record describes.
    """
    issue = _copied(_write(drafts_root, _ticket()), capsys)
    asked = _comment(issue, "Seen again.\n", author="a-person")
    feedback = _gathered(drafts_root, issue, asked)
    elsewhere = _comment(
        issue,
        tickets.render_reply(
            RUN,
            "another-cause",
            answers=asked,
            url="u",
            author="a-person",
            response="It confirms the cause.",
            verdict=tickets.Verdict.CONFIRMS,
        ),
    )
    _re_estimated(issue, capsys)

    _account(feedback, issue, asked, elsewhere, "confirms")
    status, refused = _responses(feedback, capsys)
    assert status == tickets.UNSOUND
    assert f"its reply {elsewhere}'s marker names the root cause `another-cause`" in refused
    assert f"record describes '{CAUSE}'; it confirms nothing there" in refused
