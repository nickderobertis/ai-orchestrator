"""A schema-15 ticket and evidence comment, filed and read back on the GitHub board and on Linear.

The follow-up commands — `board-status`, `copy`, `comment`, `re-estimate` — run for real through
the installed plan store under this repository's committed `onetaskgraph.yaml`, with a user
layer that routes the organization's repositories from `followups` to the example layer's
`hiddenco-followups` Linear tracker and nothing else. GitHub's wire is the loopback board in
`tests/github_board.py`, which refuses a text-field value over 1,024 bytes whole as GitHub
does, and Linear's is the loopback in `tests/linear_board.py`, which stores a description the
way Linear does. What each destination holds is read back through the store and held to the
store's own contract (onetaskgraph's `docs/metadata.md`, *`github-projects` and `linear` can
also show a value*): each projected value on a GitHub text field, shortened to its whole lines
and one elision line over the limit, and one collapsed `Fields` section at the end of a Linear
issue's description, which a read of its content never returns.

The agent writes each ticket as the task tells it: its parts plain and its witnesses in the
record, its drafts still on disk, so `board-status` writes the blocks and reads where each
draft came from. A run's own schema-14 ticket reaches schema 15 only by its run's rewrite, and
another run's schema-14 item keeps its schema and body through a `re-estimate`.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

import follow_up_boundary
import github_board
import onetaskgraph_user_layer
import pytest
from follow_up_ticket_shape import FIX, LOCATION, before_schema_15
from github_board import (
    PROPOSED_RUN,
    SIBLING_REPOSITORY,
    TEXT_FIELD_LIMIT,
    CommentParts,
    _decided_status,
    _follow_up_step,
    _follow_up_ticket,
    _followups_command,
    _followups_environment,
    _hosted,
    _Issue,
    _Repository,
    _serving_followups,
    _written_ticket,
    as_schema,
    comment_parts,
    written_drafts,
)
from linear_board import LinearBoard, linear_board, linear_stored
from published_tools import ONETASKGRAPH_BIN

from orchestrator import follow_up_drafts as drafts
from orchestrator import follow_up_tickets as tickets

#: The board every command takes, and the Linear tracker the layer routes the organization's
#: follow-up tickets to.
BOARD = tickets.BOARD
LINEAR = onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS
#: A repository of the organization, whose ticket the layer routes to that tracker.
ORGANIZATION = _Repository("hiddenco", "qh-api")
#: Another run, whose items this run comments on and re-estimates.
OTHER_RUN = "earlier-run"

#: The two destinations a ticket lands on, by the repository it is about.
DESTINATIONS = {"github": SIBLING_REPOSITORY, "linear": ORGANIZATION}

#: A rejected fix and a related ticket, which stay visible sections of the body.
REJECTED = "Retrying the page: it hides the skip rather than removing it."
RELATED = (
    "- [some-service: the export drops a page](https://github.com/o/s/issues/71) — related: "
    "It has a similar cursor root cause but needs an independent fix."
)
#: The marker the store writes on the line before Linear's `Fields` section.
FIELDS_MARKER = "<!-- `onetaskgraph.fields` -->"

#: What the sample-ticket spike read off the live board: its longest rendered `basis` and
#: `drafts` values, in characters, the second over a GitHub text field's limit.
LONGEST_BASIS = 975
LONGEST_DRAFTS = 1671


class Boards(NamedTuple):
    """Both loopbacks serving, the environment every command runs under, and the drafts root."""

    environment: dict[str, str]
    root: Path
    linear: LinearBoard
    tmp: Path


@pytest.fixture
def boards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Boards]:
    """`followups` on the GitHub loopback, its fields set up as the operator sets them up, and the
    organization's tracker on the Linear loopback, routed to by the user layer."""
    environment, root = _followups_environment(tmp_path)
    environment = follow_up_boundary.environment(environment)
    example = onetaskgraph_user_layer.example()
    example["sources"] = {
        BOARD: {
            "routes": [
                {"repositories": [onetaskgraph_user_layer.ORGANIZATION_REPOSITORIES], "to": LINEAR}
            ]
        },
        LINEAR: example["sources"][LINEAR],
    }
    home = onetaskgraph_user_layer.installed(tmp_path / "config", example)
    environment["XDG_CONFIG_HOME"] = str(home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    with linear_board() as linear, _serving_followups(environment, fields=True):
        environment |= linear.environment(LINEAR)
        yield Boards(environment, root, linear, tmp_path)


def _agent_ticket(repository: _Repository, cause: str) -> tickets.Ticket:
    """A schema-15 ticket as the agent writes it: its parts plain, a rejected fix and a related
    ticket, its witnesses supplied, and no draft source or transcript, which `board-status`
    writes."""
    written = _follow_up_ticket(repository, cause=cause, verified_on="verifier.example")
    body = written.body.replace(
        f"## {tickets.EVIDENCE}",
        f"## {tickets.RELATED_TICKETS}\n\n{RELATED}\n\n## {tickets.EVIDENCE}",
    ).replace(f"{FIX}\n\n## ", f"{FIX}\n\n## {tickets.REJECTED_FIXES}\n\n{REJECTED}\n\n## ", 1)
    return dataclasses.replace(written, body=body, draft_sources=None)


def _filed(boards: Boards, ticket: tickets.Ticket) -> tuple[Path, str]:
    """``ticket`` written plain beside its drafts, decided and copied as the agent does: its path
    and the item the copy reached."""
    written_drafts(boards.root, ticket)
    path = _written_ticket(boards.root, ticket, tickets._rendered(ticket, ticket.body))
    assert _decided_status(boards.environment, path) == tickets.Status.PROPOSED
    validated = _follow_up_step(boards.environment, "validate", str(path))
    assert validated.returncode == tickets.SOUND, validated.stdout + validated.stderr
    copied = _follow_up_step(boards.environment, "copy", "--board", BOARD, str(path))
    assert copied.returncode == tickets.SOUND, copied.stdout + copied.stderr
    return path, str(json.loads(copied.stdout)["destination"])


def _shown(boards: Boards, item: str) -> dict[str, object]:
    """``item`` as the store reads it back, by id."""
    shown = _followups_command(
        boards.environment, [str(ONETASKGRAPH_BIN), "task", "show", item, "--json"]
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    held = json.loads(shown.stdout)["items"][0]["item"]
    assert isinstance(held, dict), held
    return held


def _record(item: Mapping[str, object]) -> dict[str, object]:
    metadata = item["metadata"]
    assert isinstance(metadata, dict)
    held = metadata[tickets.KEY]
    assert isinstance(held, dict)
    return held


def _row(item: str) -> _Issue:
    """The GitHub loopback's own row of ``item``."""
    (row,) = [
        issue
        for issue in github_board.BOARD.issues_created_and_kept()
        if f"{BOARD}:{issue.content_id}" == item
    ]
    return row


def _value(record: Mapping[str, object], path: tuple[str, ...]) -> object:
    held: object = record
    for step in path:
        held = held.get(step) if isinstance(held, Mapping) else None
    return held


def _rendered(value: object) -> str | None:
    """A projected value as the store renders it for a GitHub text field: a string as itself, an
    array one element per line, an object one `KEY: VALUE` line per entry in byte order of its
    keys; `None` where there is no value."""
    match value:
        case None:
            return None
        case str():
            return value
        case list():
            return "\n".join(str(one) for one in value)
        case Mapping():
            return "\n".join(
                f"{key}: {value[key]}" for key in sorted(value, key=lambda key: key.encode())
            )
    raise AssertionError(f"no projected value is a {type(value).__name__}")


def _on_field(text: str) -> str:
    """``text`` as a GitHub text field holds it: whole, or its longest prefix of whole lines that
    fits the limit with one line feed and the elision line, then that line."""
    if len(text.encode()) <= TEXT_FIELD_LIMIT:
        return text
    lines = text.split("\n")
    for kept in range(len(lines), -1, -1):
        left = len(lines) - kept
        elision = f"… {left} more line{'' if left == 1 else 's'} in this item's metadata"
        shown = "\n".join([*lines[:kept], elision])
        if len(shown.encode()) <= TEXT_FIELD_LIMIT:
            return shown
    raise AssertionError(text)


def _fields_section(record: Mapping[str, object]) -> str:
    """The `Fields` section the store writes for ``record`` in Linear's `+++` form, its marker on
    the line before: one bullet per projected value, in configuration order."""
    bullets = []
    for field, path in tickets.BOARD_FIELDS:
        value = _value(record, path)
        match value:
            case str() if value:
                bullets.append(f"- **{field}**: `{value}`")
            case list() if value:
                bullets += [f"- **{field}**:", *(f"  - `{one}`" for one in value)]
            case Mapping() if value:
                ordered = sorted(value, key=lambda key: str(key).encode())
                bullets += [f"- **{field}**:", *(f"  - `{key}: {value[key]}`" for key in ordered)]
    return f"{FIELDS_MARKER}\n\n+++ Fields\n\n" + "\n".join(bullets) + "\n\n+++"


def _linear_issue(boards: Boards, item: str) -> dict[str, object]:
    (issue,) = [one for one in boards.linear.issues() if f"{LINEAR}:{one['id']}" == item]
    return issue


def _section(body: str, heading: str) -> str:
    return dict(drafts.sections(body))[heading].strip()


# llmlint: ignore-block[e2e_not_mocked, expensive_tests_stay_behind_their_own_edge]  # noqa: E501 - directive rule lists occupy one line
# Only GitHub's and Linear's wires are simulated, by the loopback boards every follow-up journey
# here uses, because the real ones are live boards every run shares; the plan store, its two
# plugins and the follow-up module stay real. These journeys belong to plan-tooling-follow-ups,
# as tests/plan_tooling/AGENTS.md requires, behind that project's own edge, whose key is the
# subject's and is shared by every journey in it.
@pytest.mark.parametrize("destination", sorted(DESTINATIONS))
def test_a_schema_15_ticket_lands_blocked_with_its_record_on_its_fields(
    boards: Boards, destination: str
) -> None:
    """The body read back from each destination has no `## Owning runs`, carries its three long
    parts each as exactly its authored block with the agent's content inside, leaves every other
    part visible and passes the module's own validation; the record read back holds the drafts'
    own sources and transcripts and the agent's witnesses; and the destination shows the seven
    values — on GitHub's text fields, on Linear in one collapsed `Fields` section the content read
    back never carries."""
    written = _agent_ticket(DESTINATIONS[destination], f"cursor-skips-on-{destination}")
    _, item = _filed(boards, written)
    assert item.startswith(f"{BOARD if destination == 'github' else LINEAR}:"), item
    shown = _shown(boards, item)
    content = tickets.item_content(shown)

    # A Linear item belongs to the project its tracker scopes every issue to, which a ticket
    # itself never names: the rest of it is held as the module holds a ticket.
    assert tickets.problems({**shown, "project": None}) == [], tickets.problems(shown)
    assert f"## {tickets.OWNING_RUNS}" not in content
    held = tickets.blocks(content)
    assert held.stray == [], held.stray
    assert [found["summary"] for found in held.found] == list(tickets.BLOCK_SUMMARIES)
    impact = _section(content, tickets.IMPACT)
    cost, calculation, locations = (
        content[found.start("body") : found.end("body")].strip() for found in held.found
    )
    plain = _section(written.body, tickets.IMPACT)
    costs = plain.split(f"**{tickets.WORKAROUND_COST}.**\n", 1)[1].split("\n\n- Severity:", 1)[0]
    assert cost == f"- {tickets.WORKAROUND_LINE}: none\n{costs.strip()}", cost
    assert calculation.splitlines() == [
        "- Severity: medium",
        "- Severity with the workaround: medium",
        tickets.estimate_line(tickets.Severity.MEDIUM, tickets.Frequency.INTERMITTENT, 1),
    ], calculation
    assert locations == LOCATION, locations
    visible = impact.split("<details>", 1)[0]
    for part in tickets.IMPACT_PARTS[:-1]:
        assert f"**{part}.**" in visible, (part, visible)
    assert not re.search(r"(?m)^- (?:Severity|Workaround|Priority estimate)", visible), visible
    fix = _section(content, tickets.SUGGESTED_FIX)
    opening, _, units = fix.partition("<details>")
    assert opening.strip() == FIX.split("\n\n", 1)[0], opening
    assert units.split("</details>", 1)[1].strip() == FIX.split("\n\n", 2)[2], units
    for heading, text in (
        (tickets.ROOT_CAUSE, _section(written.body, tickets.ROOT_CAUSE)),
        ("Examples", _section(written.body, "Examples")),
        (tickets.REJECTED_FIXES, REJECTED),
        (tickets.RELATED_TICKETS, RELATED),
        (tickets.EVIDENCE, _section(written.body, tickets.EVIDENCE)),
    ):
        assert _section(content, heading) == text, heading

    record = _record(shown)
    (draft,) = written.drafts
    assert record["schema"] == tickets.SCHEMA
    assert record[tickets.DRAFT_SOURCES_FIELD] == {draft: "author node; node verify"}
    assert record[tickets.TRANSCRIPTS_FIELD] == {
        draft: f"onepipeline transcript {PROPOSED_RUN} verify"
    }
    assert record[tickets.WITNESSES_FIELD] == list(written.witnesses)
    if destination == "github":
        row = _row(item)
        shown_on = {"Host": row.host, **row.texts}
        assert shown_on == {
            field: _on_field(text)
            for field, path in tickets.BOARD_FIELDS
            if (text := _rendered(_value(record, path))) is not None
        }, shown_on
        return
    description = str(_linear_issue(boards, item)["description"])
    before_slot = description.split("<!-- onetaskgraph.metadata", 1)[0].rstrip()
    expected = linear_stored(_fields_section(record), boards.linear.rewrites)
    assert before_slot.endswith(expected), before_slot[-1500:]
    for kept in (FIELDS_MARKER, "+++ Fields", "**Owning runs**"):
        assert kept not in str(shown["content"]), kept


def _long_drafts(run: str) -> tuple[tickets.QualifiedDraftId, ...]:
    """Drafts of ``run`` whose rendered sources come to exactly :data:`LONGEST_DRAFTS`
    characters, every one under its own id, as the longest ticket on the live board named them."""
    stem = "20261112T195514Z-a-read-only-judge-declined-a-manager-ruling-in-prose-only-"
    line = f"drafts:{run}/drafts/{stem}00: author node; node verify"
    count = (LONGEST_DRAFTS + 1) // (len(line) + 1)
    names = [f"{stem}{at:02d}" for at in range(count)]
    shortfall = LONGEST_DRAFTS - (count * len(line) + count - 1)
    names[-1] += "x" * shortfall
    return tuple(tickets.QualifiedDraftId(f"drafts:{run}/drafts/{name}") for name in names)


def _long_basis(origin: str) -> tuple[tickets.Basis, ...]:
    """A basis whose rendering comes to exactly :data:`LONGEST_BASIS` characters: the ticket's own
    repository and others it was verified in, the last one's name padded to the length."""
    own = f"{origin}: {'0' * 40}"
    entries = {origin: "0" * 40}
    rendered = len(own)
    at = 0
    while True:
        name = f"github.com/nickderobertis/verified-in-{at:02d}"
        line = len(f"{name}: {'0' * 40}") + 1
        if rendered + line > LONGEST_BASIS:
            break
        entries[name], rendered, at = f"{at % 10}" * 40, rendered + line, at + 1
    last = f"github.com/nickderobertis/verified-in-{at - 1:02d}"
    entries[f"{last}{'x' * (LONGEST_BASIS - rendered)}"] = entries.pop(last)
    return tuple(
        sorted(
            tickets.Basis(tickets.Origin(key), tickets.Commit(value))
            for key, value in entries.items()
        )
    )


def test_a_ticket_of_the_live_boards_size_copies_with_its_drafts_shortened_on_the_field(
    boards: Boards,
) -> None:
    """A ticket filed at the size the sample-ticket spike read on the live board — its rendered
    `basis` of 975 characters, its rendered `drafts` of 1,671 — copies onto the GitHub board, whose
    loopback refuses a value over 1,024 bytes whole as GitHub does: the `Drafts` field holds the
    store's shortened form, the `Basis` field the whole value, and the record read back every
    draft."""
    origin = _hosted(SIBLING_REPOSITORY)
    many = _long_drafts(PROPOSED_RUN)
    basis = _long_basis(origin)
    written = dataclasses.replace(
        _agent_ticket(SIBLING_REPOSITORY, "judge-declines-in-prose"),
        drafts=many,
        basis=basis,
        witnesses=(f"{origin} src/ruling.rs:12 at {'0' * 40}",),
    )
    rendered_basis = _rendered({entry.origin: entry.commit for entry in basis})
    assert rendered_basis is not None and len(rendered_basis) == LONGEST_BASIS
    _, item = _filed(boards, written)
    record = _record(_shown(boards, item))
    drafts_text = _rendered(record[tickets.DRAFT_SOURCES_FIELD])
    assert drafts_text is not None and len(drafts_text) == LONGEST_DRAFTS > TEXT_FIELD_LIMIT
    sources = record[tickets.DRAFT_SOURCES_FIELD]
    assert isinstance(sources, dict) and sorted(sources) == sorted(many), sources
    assert record["drafts"] == list(many)
    row = _row(item)
    assert row.texts["Drafts"] == _on_field(drafts_text) != drafts_text
    assert row.texts["Drafts"].endswith("more lines in this item's metadata"), row.texts["Drafts"]
    assert len(row.texts["Drafts"].encode()) <= TEXT_FIELD_LIMIT
    assert row.texts["Basis"] == rendered_basis


def _schema_14_ticket(boards: Boards, run: str, cause: str) -> tuple[Path, tickets.Ticket]:
    """``run``'s ticket as schema 14 wrote it — `## Owning runs` in its body, its parts plain,
    none of schema 15's keys — copied onto the GitHub board through the store's own `task copy`."""
    written = dataclasses.replace(
        _follow_up_ticket(SIBLING_REPOSITORY, cause=cause, verified_on="verifier.example"),
        created_by_run=tickets.RunId(run),
        owning_runs=(tickets.RunId(run),),
        drafts=(tickets.QualifiedDraftId(f"drafts:{run}/drafts/noticed"),),
        priority_estimate=tickets.Priority.MEDIUM,
        priority=tickets.Priority.MEDIUM,
    )
    older = as_schema(
        dataclasses.replace(
            written,
            body=tickets.with_estimate_line(
                written.body,
                tickets.estimate_line(tickets.Severity.MEDIUM, tickets.Frequency.INTERMITTENT, 1),
            ),
        ),
        tickets.OWNED_LAST_AT,
    )
    path = _written_ticket(boards.root, older)
    validated = _follow_up_step(boards.environment, "validate", str(path))
    assert validated.returncode == tickets.SOUND, validated.stdout + validated.stderr
    return path, older


def _local(boards: Boards, path: Path) -> tickets.Ticket:
    """The ticket at ``path`` as the store reads it back from the drafts root."""
    run, cause = tickets.located_path(path)
    return tickets.from_store_item(_shown(boards, tickets.qualified_id(run, cause)), pending=True)


def test_a_runs_own_schema_14_ticket_reaches_schema_15_by_its_rewrite_and_copy(
    boards: Boards,
) -> None:
    """`board-status` and `copy` keep a run's own schema-14 ticket at schema 14; the run's ordinary
    rewrite — `## Owning runs` removed, its witnesses written, its parts left plain — then
    reaches the board at schema 15 in the new shape, its fields written."""
    path, older = _schema_14_ticket(boards, PROPOSED_RUN, "cursor-skips-at-schema-14")
    written_drafts(boards.root, older)
    assert _decided_status(boards.environment, path) == tickets.Status.PROPOSED
    copied = _follow_up_step(boards.environment, "copy", "--board", BOARD, str(path))
    assert copied.returncode == tickets.SOUND, copied.stdout + copied.stderr
    item = str(json.loads(copied.stdout)["destination"])
    first = _shown(boards, item)
    assert _record(first)["schema"] == tickets.OWNED_LAST_AT
    assert f"## {tickets.OWNING_RUNS}" in str(first["content"])
    assert "<details>" not in str(first["content"])

    held = _local(boards, path)
    rewritten = dataclasses.replace(
        held,
        schema=tickets.SCHEMA,
        body=re.sub(
            rf"\n\n## {tickets.OWNING_RUNS}\n\n.*?(?=\n\n## )", "", held.body, flags=re.DOTALL
        ),
        witnesses=(f"{_hosted(SIBLING_REPOSITORY)} src/cursor.py:41 at {'0' * 40}",),
        draft_sources=None,
    )
    path.write_text(tickets._rendered(rewritten, rewritten.body), encoding="utf-8")
    assert _decided_status(boards.environment, path) == tickets.Status.PROPOSED
    validated = _follow_up_step(boards.environment, "validate", str(path))
    assert validated.returncode == tickets.SOUND, validated.stdout + validated.stderr
    again = _follow_up_step(boards.environment, "copy", "--board", BOARD, str(path))
    assert again.returncode == tickets.SOUND, again.stdout + again.stderr
    assert json.loads(again.stdout) == {"action": "updated", "destination": item}

    after = _shown(boards, item)
    content = tickets.item_content(after)
    assert tickets.problems(after) == [], tickets.problems(after)
    assert _record(after)["schema"] == tickets.SCHEMA
    assert f"## {tickets.OWNING_RUNS}" not in content
    assert [found["summary"] for found in tickets.blocks(content).found] == list(
        tickets.BLOCK_SUMMARIES
    )
    assert _row(item).texts["Witnesses"] == rewritten.witnesses[0]


def test_a_re_estimate_of_another_runs_schema_14_item_keeps_its_schema_and_body(
    boards: Boards,
) -> None:
    """This run's evidence on another run's schema-14 item, then its `re-estimate`: the item keeps
    its schema and every byte of its body but the estimate line, which states the same inputs
    with one more occurrence, the second evidence comment the recount reads."""
    path, _ = _schema_14_ticket(boards, OTHER_RUN, "cursor-skips-for-another-run")
    copied = _followups_command(
        boards.environment,
        [str(ONETASKGRAPH_BIN), "task", "copy", tickets.qualified_id(OTHER_RUN, path.stem)]
        + ["--to", BOARD, "--json"],
    )
    assert copied.returncode == 0, copied.stdout + copied.stderr
    (entry,) = json.loads(copied.stdout)["items"]
    item = str(entry["destination"])
    before = _shown(boards, item)
    assert _record(before)["schema"] == tickets.OWNED_LAST_AT

    posted = _follow_up_step(
        boards.environment,
        *comment_parts(boards.tmp / "comments", PROPOSED_RUN, path.stem, "Seen again."),
        item,
    )
    assert posted.returncode == tickets.SOUND, posted.stdout + posted.stderr
    estimated = _follow_up_step(
        boards.environment, "re-estimate", "--board", BOARD, "--run", PROPOSED_RUN, item
    )
    assert estimated.returncode == tickets.SOUND, estimated.stdout + estimated.stderr
    assert json.loads(estimated.stdout)["occurrences"] == 2

    after = _shown(boards, item)
    medium, intermittent = tickets.Severity.MEDIUM, tickets.Frequency.INTERMITTENT
    old_line = tickets.estimate_line(medium, intermittent, 1)
    new_line = tickets.estimate_line(medium, intermittent, 2)
    assert _record(after) == _record(before)
    assert tickets.item_content(after) == tickets.item_content(before).replace(old_line, new_line)
    assert f"## {tickets.OWNING_RUNS}" in tickets.item_content(after)
    assert before_schema_15(tickets.item_content(after)).count("<details>") == 0


def _comments(boards: Boards, item: str) -> list[dict[str, object]]:
    listed = _followups_command(
        boards.environment, [str(ONETASKGRAPH_BIN), "task", "comment", "list", item, "--json"]
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    held = json.loads(listed.stdout)["comments"]
    assert isinstance(held, list), held
    return held


def _comment_receipts(boards: Boards) -> list[tickets.Receipt]:
    receipts, malformed = tickets.read_receipts(boards.root, PROPOSED_RUN)
    assert malformed == [], malformed
    return [one for one in receipts if one.command is tickets.ReceiptCommand.COMMENT]


@pytest.mark.parametrize("destination", sorted(DESTINATIONS))
def test_an_evidence_comment_is_written_and_edited_from_its_parts_in_its_one_order(
    boards: Boards, destination: str
) -> None:
    """`comment` writes this run's evidence on another run's item, and `--edit` rewrites it in
    place: read back, it opens with one block whose summary is the comment's opening line and
    holds its metadata, then what was observed, its `Bears on the ticket:` paragraph and its
    budget question, then its marker last. It counts and belongs as a comment in the older shape
    does; a block naming another run is refused; and an edit of another run's comment is refused
    with nothing written. Each write is receipted as the closeout reads it."""
    other = dataclasses.replace(
        _agent_ticket(DESTINATIONS[destination], f"shared-cause-on-{destination}"),
        created_by_run=tickets.RunId(OTHER_RUN),
        owning_runs=(tickets.RunId(OTHER_RUN),),
        drafts=(tickets.QualifiedDraftId(f"drafts:{OTHER_RUN}/drafts/noticed"),),
    )
    _, item = _filed(boards, other)
    cause = other.root_cause
    older = (
        f"{tickets.comment_opening('a-third-run')}\n\nSeen at the third run.\n\n"
        f"{tickets.comment_marker('a-third-run', cause)}\n"
    )
    said = boards.tmp / "older-comment.md"
    said.write_text(older, encoding="utf-8")
    added = _followups_command(
        boards.environment,
        [str(ONETASKGRAPH_BIN), "task", "comment", "add", item, "--body-file", str(said)],
    )
    assert added.returncode == 0, added.stdout + added.stderr
    written_drafts(
        boards.root,
        dataclasses.replace(
            other, drafts=(tickets.QualifiedDraftId(f"drafts:{PROPOSED_RUN}/drafts/noticed"),)
        ),
    )
    origin = _hosted(SIBLING_REPOSITORY)
    words = comment_parts(
        boards.tmp / "comments",
        PROPOSED_RUN,
        cause,
        "The listing skipped page 9 again.",
        bears_on="its root cause also holds for a full last page.",
        question="Should the budget change?",
        sources={
            "host": "verifier.example",
            "basis": {origin: "0" * 40},
            "drafts": [f"drafts:{PROPOSED_RUN}/drafts/noticed"],
            "witnesses": [f"{origin} src/cursor.py:41 at {'0' * 40}"],
        },
    )
    posted = _follow_up_step(boards.environment, *words, item)
    assert posted.returncode == tickets.SOUND, posted.stdout + posted.stderr

    mine = [one for one in _comments(boards, item) if one["body"] != older.rstrip("\n")]
    mine = [one for one in mine if tickets.comment_owner(str(one["body"])) is not None]
    (comment,) = [
        one
        for one in mine
        if tickets.comment_owner(str(one["body"])) == (PROPOSED_RUN, cause, "evidence", None, None)
    ]
    body = str(comment["body"])
    lines = [line for line in body.splitlines() if line.strip()]
    assert lines[:2] == ["<details>", f"<summary>{tickets.comment_summary(PROPOSED_RUN)}</summary>"]
    block = body.split("</details>", 1)[0]
    draft = f"drafts:{PROPOSED_RUN}/drafts/noticed"
    for bullet in (
        "- Host: verifier.example",
        f"- Basis: {origin} at {'0' * 40}",
        f"- Draft: {draft}; author node; node verify; transcript: onepipeline transcript "
        f"{PROPOSED_RUN} verify",
        f"- Witness: {origin} src/cursor.py:41 at {'0' * 40}",
    ):
        assert bullet in tickets.item_content({"content": block}), (bullet, block)
    after = body.split("</details>", 1)[1]
    parts = [
        "The listing skipped page 9 again.",
        "Bears on the ticket: its root cause also holds for a full last page.",
        "Should the budget change?",
        tickets.comment_marker(PROPOSED_RUN, cause),
    ]
    assert [after.index(part) for part in parts] == sorted(after.index(part) for part in parts)
    assert lines[-1] == tickets.comment_marker(PROPOSED_RUN, cause)
    assert tickets.comment_summary(PROPOSED_RUN) not in after
    assert tickets.comment_opening(PROPOSED_RUN) not in body
    for ours in (body, older):
        run = PROPOSED_RUN if ours == body else "a-third-run"
        assert tickets.run_comment_problems(run, ours) == [], ours
    renamed = body.replace(
        tickets.comment_summary(PROPOSED_RUN), tickets.comment_summary("a-third-run")
    )
    assert any(
        "its opening block is summarised" in one
        for one in tickets.run_comment_problems(PROPOSED_RUN, renamed)
    )
    owner, older_owner = tickets.comment_owner(body), tickets.comment_owner(older)
    assert owner is not None and older_owner is not None
    assert owner.kind is older_owner.kind is tickets.CommentKind.EVIDENCE
    assert tickets.counts_as_occurrence(body, OTHER_RUN, cause)
    assert tickets.counts_as_occurrence(older, OTHER_RUN, cause)
    estimated = _follow_up_step(
        boards.environment, "re-estimate", "--board", BOARD, "--run", PROPOSED_RUN, item
    )
    assert estimated.returncode == tickets.SOUND, estimated.stdout + estimated.stderr
    assert json.loads(estimated.stdout)["occurrences"] == 3

    edited = comment_parts(
        boards.tmp / "edited", PROPOSED_RUN, cause, "The listing skipped pages 8 and 9."
    )
    refused = _follow_up_step(
        boards.environment, *edited, "--edit", str(_older_id(boards, item, older)), item
    )
    assert refused.returncode == tickets.UNSOUND, refused.stdout + refused.stderr
    assert "a run edits only its own evidence comment" in " ".join(refused.stderr.split())
    assert "refused, nothing posted" in refused.stderr
    again = _follow_up_step(boards.environment, *edited, "--edit", str(comment["id"]), item)
    assert again.returncode == tickets.SOUND, again.stdout + again.stderr
    held = _comments(boards, item)
    assert len(held) == 2, held
    (rewritten,) = [one for one in held if one["id"] == comment["id"]]
    assert "The listing skipped pages 8 and 9." in str(rewritten["body"])
    assert tickets.run_comment_problems(PROPOSED_RUN, str(rewritten["body"])) == []
    assert any(str(one["body"]).rstrip("\n") == older.rstrip("\n") for one in held)

    # The post and the edit, each receipted naming the one comment and the item's root cause,
    # which is what the closeout's comment check reads.
    receipts = _comment_receipts(boards)
    assert [receipt.card for receipt in receipts] == [item, item], receipts
    outcomes = [receipt.outcome for receipt in receipts]
    written = [one for one in outcomes if isinstance(one, tickets.CommentOutcomeReceipt)]
    assert [(one.id, one.root_cause) for one in written] == [(str(comment["id"]), cause)] * 2, (
        outcomes
    )


def _older_id(boards: Boards, item: str, body: str) -> object:
    """The id of the comment on ``item`` whose body is ``body``."""
    (held,) = [
        one["id"]
        for one in _comments(boards, item)
        if str(one["body"]).rstrip("\n") == body.rstrip("\n")
    ]
    return held


@pytest.mark.parametrize("destination", sorted(DESTINATIONS))
@pytest.mark.parametrize(
    ("parts", "said"),
    [
        ({"evidence": "## Impact\n\nAll."}, "the evidence carries the ticket heading `## Impact`"),
        (
            {"evidence": "Seen.", "question": "Raise it?\n<details>"},
            "the question carries a `<details>` or `</details>` line",
        ),
        ({"evidence": " "}, "the evidence file is empty"),
        ({"evidence": "Seen.", "question": ""}, "the question file is empty"),
        ({"evidence": "Seen.", "question": " \n"}, "the question file is empty"),
        ({"evidence": "Seen.", "cause": "Not_A_Slug"}, "is not a kebab-case slug"),
        (
            {"evidence": "Seen.", "sources": {"host": "verifier.example"}},
            "the sources are not one JSON object holding exactly",
        ),
    ],
    ids=[
        "a-heading",
        "a-block-line",
        "empty",
        "empty-question",
        "blank-question",
        "a-cause",
        "sources",
    ],
)
def test_comment_refuses_a_part_on_either_destination_before_anything_is_written(
    boards: Boards, destination: str, parts: CommentParts, said: str
) -> None:
    other = dataclasses.replace(
        _agent_ticket(DESTINATIONS[destination], f"refused-on-{destination}"),
        created_by_run=tickets.RunId(OTHER_RUN),
        owning_runs=(tickets.RunId(OTHER_RUN),),
        drafts=(tickets.QualifiedDraftId(f"drafts:{OTHER_RUN}/drafts/noticed"),),
    )
    _, item = _filed(boards, other)
    words = comment_parts(
        boards.tmp / "comments",
        PROPOSED_RUN,
        str(parts.get("cause", other.root_cause)),
        str(parts["evidence"]),
        question=parts.get("question"),
        sources=parts.get("sources"),
    )
    refused = _follow_up_step(boards.environment, *words, item)
    assert refused.returncode == tickets.UNSOUND, refused.stdout + refused.stderr
    assert said in " ".join(refused.stderr.split()), refused.stderr
    assert _comments(boards, item) == []
    assert _comment_receipts(boards) == []


# llmlint: ignore-end[e2e_not_mocked, expensive_tests_stay_behind_their_own_edge]
