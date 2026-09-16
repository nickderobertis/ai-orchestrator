"""What a verified follow-up ticket is, and who owns what on the board.

`tests/plan_tooling/test_follow_ups_recipe_e2e.py` drives the recipe and a scripted agent
through the real store and a real launch. What is proven here is what that journey reaches
only one shape at a time: every way a store item can fail the ticket shape, the ownership
predicates in both directions, and the single-pass fill that brings the manager's feedback
into the task verbatim. The `validate`, `check-run` and `board-status` commands are driven
against the installed `onetaskgraph`, over a drafts root this test names through the helper
that composes that name, and — for `board-status` — a second local store standing in for the
board, whose item is moved the way a person moves it.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import follow_up_variables
import pytest
from published_tools import ONETASKGRAPH_BIN

from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.root import REPO_ROOT

RUN = "listing-run"
OTHER_RUN = "earlier-run"
CAUSE = "cursor-skips-last-page"
REPOSITORY = "github.com/nickderobertis/some-service"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
#: A well-formed hostname that is not this machine's: the validator checks the shape alone.
HOST = "verifier-01.build.example"


IMPACT_PROSE = "Every reader of a listing misses its last page, and the export built on it too."
WORKAROUND = "readers request the last page by its number"


def _impact(
    prose: str = IMPACT_PROSE,
    severity: str = tickets.Severity.HIGH,
    workaround: str = WORKAROUND,
    with_workaround: str = tickets.Severity.MEDIUM,
) -> str:
    return tickets.impact_section(prose, severity, workaround, with_workaround)


IMPACT_TEXT = _impact()


def _body(host: str = HOST, impact: str = IMPACT_TEXT, rejected: str | None = None) -> str:
    """A body of every heading, with ``rejected`` as a `## Rejected fixes` after the fix."""
    sections = []
    for heading in tickets.HEADINGS:
        sections.append(
            f"## {heading}\n\n"
            + (impact if heading == tickets.IMPACT else f"What this ticket says under {heading}.")
            + (f" Verified on `{host}`." if heading == tickets.EVIDENCE else "")
        )
        if heading == tickets.SUGGESTED_FIX and rejected is not None:
            sections.append(f"## {tickets.REJECTED_FIXES}\n\n{rejected}")
    return "\n\n".join(sections)


BODY = _body()
EVIDENCE_TEXT = f"What this ticket says under {tickets.EVIDENCE}. Verified on `{HOST}`."
REJECTED_TEXT = "Paging by offset: it skips a page whenever an item is inserted mid-listing."
FIX_TEXT = f"What this ticket says under {tickets.SUGGESTED_FIX}."

#: A body as schema 4 wrote it: a `## Repository` section, and several suggested fixes.
SCHEMA_4_BODY = BODY.replace(
    "## Examples", f"## Repository\n\n{REPOSITORY}, at `src/cursor.py`.\n\n## Examples"
).replace(
    f"## {tickets.SUGGESTED_FIX}\n\n{FIX_TEXT}",
    "## Suggested fixes\n\n- Either page by cursor.\n- Or retry the last page.",
)

#: Every way a body's fixes are refused, and what the refusal names.
REJECTED_ELSEWHERE = "`## Rejected fixes` section is not directly after `## Suggested fix`"
FIX_REFUSALS = [
    (BODY.replace(f"## {tickets.SUGGESTED_FIX}\n\n{FIX_TEXT}\n\n", ""), "no `## Suggested fix`"),
    (BODY.replace(FIX_TEXT, ""), "the body's `## Suggested fix` section is empty"),
    (_body(rejected="\n"), "the body's `## Rejected fixes` section is empty"),
    (
        _body(rejected=f"{REJECTED_TEXT}\n\n## Rejected fixes\n\n{REJECTED_TEXT}"),
        "the body carries `## Rejected fixes` 2 times; it is optional and appears at most once",
    ),
    (BODY + f"\n\n## Rejected fixes\n\n{REJECTED_TEXT}", REJECTED_ELSEWHERE),
    (f"## Rejected fixes\n\n{REJECTED_TEXT}\n\n" + BODY, REJECTED_ELSEWHERE),
    (
        BODY.replace(
            f"## {tickets.SUGGESTED_FIX}\n",
            f"## Rejected fixes\n\n{REJECTED_TEXT}\n\n## {tickets.SUGGESTED_FIX}\n",
        ),
        REJECTED_ELSEWHERE,
    ),
    (
        BODY.replace("## Examples", "## Repository\n\nThe origin.\n\n## Examples"),
        "carries `## Repository`, which schema 5 retired; bring the ticket to the current shape",
    ),
    (
        BODY.replace(f"## {tickets.SUGGESTED_FIX}\n", "## Suggested fixes\n"),
        "carries `## Suggested fixes`, which schema 5 retired; bring the ticket to the current",
    ),
    (
        SCHEMA_4_BODY,
        "carries `## Repository` and `## Suggested fixes`, which schema 5 retired",
    ),
]

#: Every way a body's `## Impact` section is refused, and what the refusal names.
IMPACT_ORDER = "does not end with its three lines, in this order and with nothing between or after"
IMPACT_REFUSALS = [
    (BODY.replace(f"## Impact\n\n{IMPACT_TEXT}\n\n", ""), "no `## Impact` heading in its place"),
    (_body(impact="\n"), "the body's `## Impact` section is empty"),
    (_body(impact=_impact(prose="")), "`## Impact` section carries no prose before its lines"),
    (
        _body(impact=IMPACT_TEXT.replace(f"- Workaround: {WORKAROUND}\n", "")),
        "`## Impact` section carries no `- Workaround:` line",
    ),
    (
        _body(impact=IMPACT_TEXT + "- Severity: high\n"),
        "`## Impact` section carries its `- Severity:` line 2 times, not once",
    ),
    (
        _body(impact=_impact(severity="severe")),
        "`- Severity:` line names 'severe', which is not a severity",
    ),
    (
        _body(impact=_impact(with_workaround="Low")),
        "`- Severity with the workaround:` line names 'Low', which is not a severity",
    ),
    (_body(impact=_impact(workaround="")), "`- Workaround:` line is empty"),
    (
        _body(
            impact=_impact(severity=tickets.Severity.MEDIUM, with_workaround=tickets.Severity.HIGH)
        ),
        "severity with the workaround `high` is above its severity `medium`",
    ),
    (
        _body(impact=_impact(workaround="none", with_workaround=tickets.Severity.LOW)),
        "workaround is `none`, so its severity with the workaround `low` has to be its "
        "severity `high`",
    ),
    (
        _body(
            impact=f"{IMPACT_PROSE}\n\n- Workaround: {WORKAROUND}\n- Severity: high\n"
            "- Severity with the workaround: medium\n"
        ),
        IMPACT_ORDER,
    ),
    (_body(impact=IMPACT_TEXT.replace("- Workaround:", "A note.\n- Workaround:")), IMPACT_ORDER),
    (_body(impact=IMPACT_TEXT + "\nA paragraph after the lines.\n"), IMPACT_ORDER),
]


#: A sound ticket, which each test below states only its departures from.
SOUND_TICKET = tickets.Ticket(
    title="some-service: the listing cursor skips the last page",
    status=tickets.Status.PROPOSED,
    root_cause=tickets.RootCause(CAUSE),
    repository=tickets.Origin(REPOSITORY),
    created_by_run=tickets.RunId(RUN),
    owning_runs=(tickets.RunId(RUN),),
    drafts=(tickets.QualifiedDraftId(f"drafts:{RUN}/drafts/20260101T000000Z-cursor"),),
    basis=(tickets.Basis(tickets.Origin(REPOSITORY), tickets.Commit(COMMIT)),),
    verified_at=tickets.Timestamp("2026-01-01T00:00:00Z"),
    host=tickets.Host(HOST),
    body=BODY,
)


def _ticket(**departures: object) -> tickets.Ticket:
    return dataclasses.replace(SOUND_TICKET, **departures)


def _item(ticket: tickets.Ticket | None = None) -> dict[str, object]:
    """A store item as `onetaskgraph task show --json` reports a sound ticket."""
    held = ticket or _ticket()
    return {
        "id": f"{RUN}/tickets/{held.root_cause}",
        "title": held.title,
        "content": held.body,
        "status": {"category": held.status.value, "name": held.status.value},
        "labels": [],
        "project": None,
        "repositories": [held.repository],
        "metadata": {tickets.KEY: tickets.record(held)},
    }


def _record(item: dict[str, object]) -> dict[str, object]:
    metadata = item["metadata"]
    assert isinstance(metadata, dict)
    held = metadata[tickets.KEY]
    assert isinstance(held, dict)
    return held


def test_a_sound_item_reads_back_as_the_ticket_it_was_rendered_from() -> None:
    assert tickets.problems(_item(), run=RUN, root_cause=CAUSE) == []
    assert tickets.from_store_item(_item(), run=RUN, root_cause=CAUSE) == _ticket()


def test_the_record_is_the_current_schema_and_carries_the_host_after_verified_at() -> None:
    held = tickets.record(_ticket())

    assert held["schema"] == tickets.SCHEMA == 5
    keys = list(held)
    assert keys == list(tickets.RECORD_KEYS)
    assert keys.index("host") == keys.index("verified_at") + 1
    assert held["host"] == HOST


@pytest.mark.parametrize("status", list(tickets.Status))
def test_each_ticket_status_is_admitted(status: tickets.Status) -> None:
    assert tickets.from_store_item(_item(_ticket(status=status))).status is status


def _set(key: str, value: object) -> Callable[[dict[str, object]], None]:
    def change(item: dict[str, object]) -> None:
        item[key] = value

    return change


def _set_record(key: str, value: object) -> Callable[[dict[str, object]], None]:
    def change(item: dict[str, object]) -> None:
        _record(item)[key] = value

    return change


def _drop_record(key: str) -> Callable[[dict[str, object]], None]:
    def change(item: dict[str, object]) -> None:
        del _record(item)[key]

    return change


def _no_record(item: dict[str, object]) -> None:
    item["metadata"] = {}


def _no_repositories(item: dict[str, object]) -> None:
    del item["repositories"]


def _status(word: str) -> Callable[[dict[str, object]], None]:
    return _set("status", {"category": word, "name": word})


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (_set("project", "listing-run"), "carries a `project`"),
        (_set("repositories", []), "the ticket carries no `repositories`"),
        (_no_repositories, "the ticket carries no `repositories`"),
        (
            _set("repositories", [REPOSITORY, "github.com/nickderobertis/another-service"]),
            "`repositories` names 2 entries",
        ),
        (
            _set("repositories", ["github.com/nickderobertis/another-service"]),
            "names 'github.com/nickderobertis/another-service', not its record's `repository`",
        ),
        (_status("unknown"), "the status is 'unknown'"),
        (_status("deferred"), "the status is 'deferred'"),
        (_set("status", "open"), "the status is 'open'"),
        (_no_record, "carries no `orchestrator.follow-up` metadata record"),
        (_drop_record("basis"), "record is missing basis"),
        (_drop_record("host"), "record is missing host"),
        (_set_record("extra", 1), "carries keys this does not write: extra"),
        (_set_record("schema", 1), "is schema 1, and this reads schema 5"),
        (_set_record("schema", 2), "is schema 2, and this reads schema 5"),
        (_set_record("schema", 3), "is schema 3, and this reads schema 5"),
        (_set_record("schema", 4), "is schema 4, and this reads schema 5"),
        (_set_record("schema", True), "is schema True"),
        (_set_record("root_cause", "Not A Slug"), "is not a kebab-case slug"),
        (_set_record("root_cause", "another-cause"), "is not the file's root cause"),
        (_set_record("repository", "https://github.com/o/r.git"), "is not a normalized origin"),
        (_set_record("created_by_run", "../escape"), "`created_by_run` '../escape' is not a run"),
        (_set_record("created_by_run", OTHER_RUN), "the run whose tickets directory holds it"),
        (_set_record("owning_runs", []), "`owning_runs` is not a non-empty list"),
        (_set_record("owning_runs", [RUN, RUN]), "names a run twice"),
        (_set_record("owning_runs", [OTHER_RUN]), "does not include `created_by_run`"),
        (_set_record("owning_runs", [RUN, 7]), "`owning_runs` entry 7 is not a run id"),
        (_set_record("drafts", []), "`drafts` is not a non-empty list"),
        (_set_record("drafts", ["plans:x/y"]), "is not a qualified draft id"),
        (_set_record("drafts", [f"drafts:{OTHER_RUN}/drafts/x"]), "`owning_runs` does not name"),
        (_set_record("basis", {}), "`basis` is not a non-empty mapping"),
        (_set_record("basis", {REPOSITORY: "HEAD"}), "is not a normalized origin and a 40-char"),
        (_set_record("basis", {"github.com/o/other": COMMIT}), "names no commit for the ticket's"),
        (_set_record("verified_at", "yesterday"), "is not an RFC 3339 UTC time"),
        (_set_record("verified_at", "2026-02-30T00:00:00Z"), "is not an RFC 3339 UTC time"),
        (_set("title", ""), "the title is empty"),
        (_set("title", "some-service: " + "x" * 120), "over the 120 a ticket's title may hold"),
        (_set("title", "other: the listing cursor"), "does not read `some-service: <the root"),
        (_set("title", "some-service: \n"), "surrounding whitespace, a line break"),
        (_set("content", None), "the ticket has no body"),
        (_set("content", BODY.replace("## Examples", "## Samples")), "no `## Examples` heading"),
        (
            _set("content", BODY.replace(EVIDENCE_TEXT, "")),
            "the body's `## Evidence` section is empty",
        ),
        (
            _set("content", _body("another-host")),
            f"`## Evidence` section does not name the host '{HOST}'",
        ),
        *[(_set("content", body), reason) for body, reason in IMPACT_REFUSALS],
        *[(_set("content", body), reason) for body, reason in FIX_REFUSALS],
    ],
)
def test_each_way_an_item_is_not_a_ticket_is_named(
    change: Callable[[dict[str, object]], None], reason: str
) -> None:
    item = copy.deepcopy(_item())
    change(item)

    found = tickets.problems(item, run=RUN, root_cause=CAUSE)

    assert any(reason in problem for problem in found), found
    with pytest.raises(tickets.Refused) as refused:
        tickets.from_store_item(item, run=RUN, root_cause=CAUSE)
    assert reason in str(refused.value)


def test_a_status_outside_the_vocabulary_is_told_what_each_status_means() -> None:
    (problem,) = tickets.problems(copy.deepcopy(_item()) | {"status": "unknown"})

    for status in tickets.Status:
        assert f"`{status}`, {status.meaning}" in problem, problem


def test_a_missing_host_is_named_in_the_one_line_naming_every_missing_key() -> None:
    item = _item()
    del _record(item)["basis"]
    del _record(item)["host"]

    found = tickets.problems(item, run=RUN, root_cause=CAUSE)

    missing = [problem for problem in found if "is missing" in problem]
    assert missing == ["the `orchestrator.follow-up` record is missing basis, host"], found


def test_a_schema_4_ticket_is_refused_naming_its_schema_first() -> None:
    """The shape a ticket carried before one fix: `## Repository`, and `## Suggested fixes`."""
    item = _item()
    _record(item)["schema"] = 4
    item["content"] = SCHEMA_4_BODY

    found = tickets.problems(item, run=RUN, root_cause=CAUSE)

    assert found[0].startswith("the record is schema 4, and this reads schema 5"), found
    assert any(
        "carries `## Repository` and `## Suggested fixes`, which schema 5 retired; bring the "
        "ticket to the current shape" in problem
        for problem in found
    ), found


@pytest.mark.parametrize(
    "body",
    [
        BODY,
        _body(rejected=REJECTED_TEXT),
        _body(rejected="- Retrying the page: it hides the skip rather than removing it."),
    ],
    ids=["without-rejected-fixes", "with-rejected-fixes", "with-a-list-of-rejected-fixes"],
)
def test_a_body_with_one_fix_and_rejected_fixes_once_after_it_or_none_is_sound(body: str) -> None:
    ticket = _ticket(body=body)

    assert tickets.problems(_item(ticket), run=RUN, root_cause=CAUSE) == []
    assert tickets.from_store_item(_item(ticket)).body == body


@pytest.mark.parametrize(
    "impact",
    [
        IMPACT_TEXT,
        _impact(workaround="none", with_workaround=tickets.Severity.HIGH),
        _impact(severity=tickets.Severity.LOW, with_workaround=tickets.Severity.LOW),
        # Prose may run to several paragraphs, and a bullet of its own is prose, not a line.
        _impact(prose="Operators of the service lose a day.\n\n- Users: everyone paging."),
    ],
)
def test_an_impact_section_of_prose_and_one_of_each_line_is_accepted(impact: str) -> None:
    ticket = _ticket(body=_body(impact=impact))

    assert tickets.problems(_item(ticket), run=RUN, root_cause=CAUSE) == []


def test_an_impact_section_is_told_every_problem_it_has_at_once() -> None:
    item = _item(_ticket(body=_body(impact="- Severity: dire\n- Severity: dire\n")))

    found = tickets.problems(item, run=RUN, root_cause=CAUSE)

    assert len(found) == 4, found
    assert "carries no prose before its lines" in found[0]


def test_the_severity_vocabulary_is_ordered_and_means_what_the_contract_states() -> None:
    assert {severity.value: severity.meaning for severity in tickets.Severity} == {
        "critical": (
            "data or work is lost or corrupted, or the affected thing cannot be used at all"
        ),
        "high": (
            "the affected thing fails, or a person must intervene, every time the root cause fires"
        ),
        "medium": "it costs time, resources or quality, but recovers without a person",
        "low": "cosmetic, or rare with negligible cost",
    }
    assert (
        list(tickets.Severity)
        == sorted(
            tickets.Severity, key=lambda one: sum(one.above(other) for other in tickets.Severity)
        )[::-1]
    )
    assert tickets.Severity.CRITICAL.above(tickets.Severity.LOW)
    assert not tickets.Severity.LOW.above(tickets.Severity.LOW)


#: Words that belong to this harness rather than to the repository a ticket is filed against.
ORCHESTRATION_WORDS = re.compile(r"\b(?:runs?|nodes?|dispatch\w*|managers?)\b", re.IGNORECASE)


def test_the_impact_guidance_and_severities_speak_of_the_repository_not_of_orchestration() -> None:
    contract = tickets.ticket_contract(RUN, "followups")
    guidance = contract.split("## Impact\n", 1)[1].split("\n## Examples\n", 1)[0]

    assert ORCHESTRATION_WORDS.findall(guidance) == [], guidance
    for severity in tickets.Severity:
        assert ORCHESTRATION_WORDS.findall(severity.meaning) == [], severity
    for spoken in ("users", "artifacts of the repository"):
        assert spoken in guidance, guidance


def test_a_repositories_entry_is_compared_only_against_a_record_repository_that_is_an_origin() -> (
    None
):
    """A record whose `repository` is no origin is named for that, not for a mismatch too."""
    item = _item()
    _record(item)["repository"] = "not an origin"

    found = tickets.problems(item, run=RUN, root_cause=CAUSE)

    assert any("`repository` 'not an origin' is not a normalized origin" in one for one in found)
    assert not any("not its record's `repository`" in one for one in found), found


@pytest.mark.parametrize(
    "host",
    [
        "",
        "-leading",
        "trailing-",
        "under_score",
        "two..dots",
        "trailing.",
        ".leading",
        "hôte",
        "has space",
        "line\nbreak",
        "a" * 64,
        ".".join(["a" * 63] * 4),
        7,
        None,
    ],
)
def test_a_host_that_is_not_a_hostname_is_refused(host: object) -> None:
    item = copy.deepcopy(_item())
    _record(item)["host"] = host

    found = tickets.problems(item, run=RUN, root_cause=CAUSE)

    assert any(problem.startswith(f"`host` {host!r} is not a hostname") for problem in found), found


@pytest.mark.parametrize(
    "host",
    ["lima-hp", "a", "A-1.b2.example", "a" * 63, ".".join(["a" * 63] * 3 + ["a" * 61])],
)
def test_any_well_formed_hostname_is_accepted_whatever_machine_it_names(host: str) -> None:
    ticket = _ticket(host=tickets.Host(host), body=_body(host))

    assert tickets.problems(_item(ticket), run=RUN, root_cause=CAUSE) == []
    assert tickets.from_store_item(_item(ticket)).host == host


def test_every_problem_is_named_at_once_rather_than_the_first() -> None:
    item = _item()
    item["project"] = "a-project"
    item["title"] = ""
    _record(item)["schema"] = 9

    found = tickets.problems(item)

    assert len(found) == 3, found


def test_a_path_outside_the_ticket_layout_is_refused_by_name(tmp_path: Path) -> None:
    for path in (
        tmp_path / "tasks" / RUN / "drafts" / f"{CAUSE}.md",
        tmp_path / "tasks" / RUN / "tickets" / "Not_A_Slug.md",
        tmp_path / "tasks" / RUN / "tickets" / f"{CAUSE}.txt",
        tmp_path / "elsewhere" / RUN / "tickets" / f"{CAUSE}.md",
    ):
        with pytest.raises(tickets.Refused, match="is not where a ticket is stored"):
            tickets.located_path(path)
    assert tickets.located_path(tickets.ticket_path(tmp_path, RUN, CAUSE)) == (RUN, CAUSE)


EVIDENCE_MARKER = f'<!-- orchestrator:follow-up-comment run="{RUN}" root_cause="{CAUSE}" -->'
COMMENT_ID = "IC_kwDOabc-123"
COMMENT_URL = "https://github.com/nickderobertis/some-service/issues/7#issuecomment-99"


def test_an_evidence_comment_is_owned_by_the_run_its_last_line_names() -> None:
    comment = tickets.render_comment(RUN, CAUSE, "The cursor skipped page 9 too.")

    assert comment.splitlines()[0] == f"Additional evidence from follow-up run `{RUN}`."
    assert comment.rstrip("\n").splitlines()[-1] == EVIDENCE_MARKER
    owner = tickets.comment_owner(comment)
    assert owner == tickets.CommentOwner(RUN, CAUSE)
    assert owner == (RUN, CAUSE, tickets.CommentKind.EVIDENCE, None)
    # The store keeps a body with trailing blank lines; the marker is still the last line.
    assert tickets.comment_owner(comment + "\n\n") == tickets.CommentOwner(RUN, CAUSE)


def test_an_evidence_comment_already_on_the_board_still_reads_as_its_runs_evidence() -> None:
    """A comment written before replies existed, byte for byte, is still its run's evidence."""
    written_before = (
        f"Additional evidence from follow-up run `{RUN}`.\n\nPage 9 again.\n\n{EVIDENCE_MARKER}\n"
    )

    assert tickets.render_comment(RUN, CAUSE, "Page 9 again.") == written_before
    assert tickets.comment_marker(RUN, CAUSE) == EVIDENCE_MARKER
    assert tickets.comment_owner(written_before) == tickets.CommentOwner(
        tickets.RunId(RUN), tickets.RootCause(CAUSE), tickets.CommentKind.EVIDENCE, None
    )


@pytest.mark.parametrize(
    ("author", "opening"),
    [
        ("a-reviewer", f"Reply from follow-up run `{RUN}` to @a-reviewer's comment: "),
        (None, f"Reply from follow-up run `{RUN}` to the comment: "),
    ],
)
def test_a_reply_opens_naming_the_comment_and_ends_with_a_marker_naming_its_id(
    author: str | None, opening: str
) -> None:
    reply = tickets.render_reply(
        RUN,
        CAUSE,
        answers=COMMENT_ID,
        url=COMMENT_URL,
        author=author,
        response="\nCopied the ticket again with page 9 in its examples.\n",
    )

    marker = (
        f'<!-- orchestrator:follow-up-comment run="{RUN}" root_cause="{CAUSE}" kind="reply" '
        f'answers="{COMMENT_ID}" -->'
    )
    assert reply == (
        f"{opening}{COMMENT_URL}\n\nCopied the ticket again with page 9 in its examples.\n\n"
        f"{marker}\n"
    )
    assert tickets.reply_marker(RUN, CAUSE, COMMENT_ID) == marker
    assert tickets.comment_owner(reply) == tickets.CommentOwner(
        tickets.RunId(RUN),
        tickets.RootCause(CAUSE),
        tickets.CommentKind.REPLY,
        tickets.CommentId(COMMENT_ID),
    )
    assert tickets.may_change_comment(RUN, reply)
    assert not tickets.may_change_comment(OTHER_RUN, reply)


@pytest.mark.parametrize("answers", ["", "has space", 'a"quote', "an>angle", "tab\there"])
def test_a_reply_to_an_id_outside_the_grammar_is_refused(answers: str) -> None:
    with pytest.raises(tickets.Refused, match="is not one a reply's marker can carry"):
        tickets.render_reply(
            RUN, CAUSE, answers=answers, url=COMMENT_URL, author=None, response="Done."
        )


def _marker(attributes: str) -> str:
    return f'<!-- orchestrator:follow-up-comment run="{RUN}" root_cause="{CAUSE}"{attributes} -->'


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Just a comment somebody wrote.",
        tickets.comment_marker(RUN, CAUSE) + "\nand a line after it",
        '<!-- orchestrator:follow-up-comment run="../x" root_cause="c" -->',
        _marker(' kind="evidence"'),
        _marker(' kind="answer" answers="c-1"'),
        _marker(' kind=""'),
        _marker(' kind="reply"'),
        _marker(' kind="reply" answers=""'),
        _marker(' kind="reply" answers="has space"'),
        _marker(' kind="reply" answers="an>angle"'),
        _marker(' answers="c-1"'),
        _marker(' kind="evidence" answers="c-1"'),
        _marker(' answers="c-1" kind="reply"'),
    ],
)
def test_a_comment_whose_last_line_is_no_marker_belongs_to_no_run(body: str) -> None:
    assert tickets.comment_owner(body) is None
    assert not tickets.may_change_comment(RUN, body)


def test_a_run_changes_only_its_own_issues_and_comments_and_adds_evidence_only_on_others() -> None:
    own, other, unowned = _item(), _item(_ticket(created_by_run=OTHER_RUN)), {"metadata": {}}
    reply = tickets.CommentKind.REPLY

    assert tickets.issue_owner(own) == RUN
    assert tickets.may_change_issue(RUN, own)
    assert not tickets.may_change_issue(RUN, other)
    assert not tickets.may_change_issue(RUN, unowned)
    assert not tickets.may_comment_on(RUN, own), "a run edits its own issue, never adds evidence"
    assert not tickets.may_comment_on(RUN, own, tickets.CommentKind.EVIDENCE)
    assert tickets.may_comment_on(RUN, other)
    assert not tickets.may_comment_on(RUN, unowned)
    assert tickets.may_comment_on(RUN, own, reply), "a reply answers a person on its own issue"
    assert tickets.may_comment_on(RUN, other, reply)
    assert not tickets.may_comment_on(RUN, unowned, reply)
    assert tickets.may_change_comment(RUN, tickets.render_comment(RUN, CAUSE, "x"))
    assert not tickets.may_change_comment(RUN, tickets.render_comment(OTHER_RUN, CAUSE, "x"))
    others_reply = tickets.render_reply(
        OTHER_RUN, CAUSE, answers=COMMENT_ID, url=COMMENT_URL, author=None, response="x"
    )
    assert not tickets.may_change_comment(RUN, others_reply)


TEMPLATE = (
    "Run @RUN@ onto @BOARD@ under @DRAFTS_ROOT@, validating with @VALIDATE@ in @CHECKOUT@.\n"
    "Decide each status with @BOARD_STATUS@, and read the store with @PLAN_STORE@.\n"
    "@STATUS_VOCABULARY@\n"
    "@TICKET_CONTRACT@\n@COMMENT_CONTRACT@\n@REDISPATCH@\n@FEEDBACK@\nAgain, @RUN@.\n"
)
BOARD_STATUS = "python -m orchestrator.follow_up_tickets board-status"

#: The plan-store program a composed task carries, spelled in full the way the recipe
#: resolves it. This checkout's own installed CLI rather than a path written down here,
#: because the composer refuses anything that is not an executable file: what the task
#: names has to be a program the dispatch can run.
PLAN_STORE = str(ONETASKGRAPH_BIN)


def _contract(run: str = RUN, board: str = "followups", root: str = "/drafts-root") -> str:
    """The ticket contract as a composed task carries it, its values filled."""
    return (
        tickets.ticket_contract(run, board)
        .replace("@DRAFTS_ROOT@", root)
        .replace("@BOARD_STATUS@", BOARD_STATUS)
        .replace("@PLAN_STORE@", PLAN_STORE)
    )


def _ownership(run: str = RUN, board: str = "followups") -> str:
    """Board ownership as a composed task carries it, its one value filled."""
    return tickets.comment_contract(run, board).replace("@PLAN_STORE@", PLAN_STORE)


def _compose(*, feedback: str | None = None, redispatch: bool = False) -> str:
    return tickets.compose(
        TEMPLATE,
        run=RUN,
        board="followups",
        drafts_root=Path("/drafts-root"),
        validate="python -m orchestrator.follow_up_tickets validate",
        board_status=BOARD_STATUS,
        checkout=Path("/checkout"),
        plan_store=PLAN_STORE,
        feedback=feedback,
        redispatch=redispatch,
    )


def test_the_composed_task_fills_every_placeholder_and_renders_both_contracts() -> None:
    task = _compose()

    assert tickets.PLACEHOLDER.search(task) is None, task
    assert task.startswith(
        "Run listing-run onto followups under /drafts-root, validating with python -m "
    )
    assert task.rstrip().endswith("Again, listing-run.")
    assert _contract() in task
    assert _ownership() in task
    assert f"{PLAN_STORE} task copy drafts:{RUN}/tickets/<root-cause> --to followups" in task
    assert "`onetaskgraph " not in task, (
        "a composed task names the plan store in full, never a bare program name a "
        "dispatch would resolve from its own search path"
    )
    assert tickets.comment_marker(RUN, "<root-cause>") in task
    assert "This is a re-dispatch" not in task
    assert "Feedback on the previous follow-up run" not in task


def test_a_value_the_template_names_more_than_once_is_filled_everywhere() -> None:
    task = tickets.compose(
        TEMPLATE + "Copy onto @BOARD@ from @DRAFTS_ROOT@ with @VALIDATE@.\n",
        run=RUN,
        board="followups",
        drafts_root=Path("/drafts-root"),
        validate="v",
        board_status="s",
        checkout=Path("/checkout"),
        plan_store=PLAN_STORE,
        feedback=None,
        redispatch=False,
    )

    assert task.rstrip().endswith("Copy onto followups from /drafts-root with v.")


@pytest.mark.reads_docs
def test_the_tracked_template_composes_into_a_task_carrying_the_rendered_contract() -> None:
    """The template the recipe composes from carries the module's contract whole.

    And it names the status decision as a step of its own, before the step that copies.
    """
    template = (REPO_ROOT / "config" / "follow-up-task.md").read_text(encoding="utf-8")

    task = tickets.compose(
        template,
        run=RUN,
        board="followups",
        drafts_root=Path("/drafts-root"),
        validate="python -m orchestrator.follow_up_tickets validate",
        board_status=BOARD_STATUS,
        checkout=Path("/checkout"),
        plan_store=PLAN_STORE,
        feedback="Merge the two cursor tickets.\n",
        redispatch=True,
    )

    assert tickets.PLACEHOLDER.search(task) is None
    assert _contract() in task
    assert _ownership() in task
    # Every store instruction names the program the recipe resolved, in full: a bare name
    # is answered by the dispatch's own search path.
    assert PLAN_STORE in task
    bare = [line for line in task.splitlines() if "`onetaskgraph " in line]
    assert not bare, f"the tracked template still names a bare plan-store invocation: {bare}"
    assert "## This is a re-dispatch" in task
    assert "Merge the two cursor tickets." in task
    steps = task.split("## What to do, in order", 1)[1].split("## The verified ticket", 1)[0]
    decided = steps.index(f"`{BOARD_STATUS} --board followups <path of the ticket>`")
    assert decided < steps.index("**Put each ticket on the board.**"), steps

    vocabulary = tickets.status_vocabulary()
    assert task.count(vocabulary) == 1, "the task does not carry the status vocabulary once"
    assert f"## What each board status means\n\n{vocabulary}" in task
    flat = " ".join(task.split())
    for rule in (
        "a ticket the board holds at `Deferred` is copied carrying `draft`",
        "this run never withdraws a deferred item",
        "an item at `Deferred` receives this run's one comment like any other open item",
        "An item at `Deferred` is open: no agent picks it up to work on, but it is searched "
        "like any other open item",
    ):
        assert rule in flat, rule


def _tracked_task(*, feedback: str | None = None, redispatch: bool = True) -> str:
    """The task the recipe composes from the tracked template, its whitespace collapsed."""
    template = (REPO_ROOT / "config" / "follow-up-task.md").read_text(encoding="utf-8")
    return tickets.compose(
        template,
        run=RUN,
        board="followups",
        drafts_root=Path("/drafts-root"),
        validate="python -m orchestrator.follow_up_tickets validate",
        board_status=BOARD_STATUS,
        checkout=Path("/checkout"),
        plan_store=PLAN_STORE,
        feedback=feedback,
        redispatch=redispatch,
    )


@pytest.mark.reads_docs
def test_the_composed_task_asks_for_one_concrete_fix_and_optional_rejected_fixes() -> None:
    task = _tracked_task(redispatch=False)
    flat = " ".join(task.split())
    example = task.split("````markdown\n", 1)[1].split("````", 1)[0]

    for said in (
        "**`## Suggested fix` states one concrete fix**: a single change, or a single set of "
        "changes that together remove the root cause, never a list of options or alternatives "
        "to choose between.",
        "`## Rejected fixes` is optional: when another fix was considered, it comes directly "
        "after `## Suggested fix` and gives each rejected fix with why it was rejected",
        "<a simple explanation of the root cause, naming the paths inside the repository where "
        "it lives>",
        "<the one fix this ticket recommends: a single change, or a single set of changes that "
        "together remove the root cause, concrete enough that whoever picks it up has nothing "
        "left to choose. Never a list of options or alternatives to choose between>",
    ):
        assert said in flat, said
    headings = re.findall(r"^## (.+)$", example, re.MULTILINE)
    assert headings == [
        "Root cause",
        "Impact",
        "Examples",
        "Evidence",
        "Suggested fix",
        "Rejected fixes",
        "Owning runs",
    ], headings
    assert "## Repository" not in task and "## Suggested fixes" not in example
    rejected = example.split("## Rejected fixes\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert rejected.startswith("<optional: leave this section out when no other fix was"), rejected
    assert "each fix that was considered and not chosen, and why it was rejected" in rejected


@pytest.mark.reads_docs
def test_the_composed_re_dispatch_brings_an_older_ticket_to_one_fix_and_no_repository() -> None:
    flat = " ".join(_tracked_task().split())

    assert (
        "a ticket of an older schema is brought to the current shape before it is copied, its "
        "`repositories` naming its record's `repository`, its `host` read from this machine with "
        "`hostname`, and its `## Impact` section written from the evidence the ticket already "
        "carries, re-verifying only a claim that no longer holds; its `## Repository` section "
        "removed, any path the ticket still needs moved into `## Root cause`; its "
        "`## Suggested fixes` rewritten as `## Suggested fix`, stating the one fix the ticket's "
        "evidence supports; and every other option it offered moved into `## Rejected fixes`, "
        "with why each was not chosen; then it is validated again."
    ) in flat


@pytest.mark.reads_docs
def test_the_composed_task_states_the_reply_rules_once_and_every_other_text_points_to_them() -> (
    None
):
    task = _tracked_task(feedback="Merge the two cursor tickets.\n")
    flat = " ".join(task.split())
    ownership = task.split("## Ownership on the board\n", 1)[1].split("## This is a re-dispatch")[0]
    flat_ownership = " ".join(ownership.split())

    reply = tickets.reply_marker(RUN, "<root-cause>", "<comment id>")
    assert f"`{reply}`" in ownership
    assert 'kind="reply" answers="<comment id>"' in reply
    for rule in (
        "A comment belongs to the run named in its **last line**, whichever of the two kinds "
        "below it is",
        "may edit or delete only comments whose marker names `listing-run`",
        "It goes only on an open issue another run created for the same root cause, which "
        "receives at most **one** from this run",
        "edit it in place with",
        "This run never adds an evidence comment to an issue it created: it edits that issue by "
        "copying its ticket again instead.",
        "Each comment the feedback below quotes under a `### Comment` heading, with its id and "
        "URL, gets exactly **one** new reply from this run, on the issue that holds that "
        "comment, whichever run owns that issue.",
        "`answers` is the id of the comment it answers, exactly as the feedback gives it",
        "`<root-cause>` is the `root_cause` in the ticket record of the issue the reply is "
        "posted on",
        f"`{tickets.reply_opening(RUN, '<comment URL>', '<author>')}`",
        f"`{tickets.reply_opening(RUN, '<comment URL>', None)}` when the feedback reports no "
        "author",
        f"Post it with `{PLAN_STORE} task comment add`, after the actions it reports.",
        "**A reply is never edited to answer a different comment**",
        "A reply never counts as this run's one evidence comment, and never carries evidence in "
        "place of the ticket or the evidence comment.",
        "A comment that appears on the board during this dispatch is left for the next "
        "gathering, and feedback the manager wrote quotes no board comment, so it gets no reply.",
    ):
        assert rule in flat_ownership, rule
    assert flat.count("new reply") == 1, "the reply rule is stated more than once"
    redispatch = " ".join(
        task.split("## This is a re-dispatch", 1)[1].split("## Feedback")[0].split()
    )
    assert '"Ownership on the board" above binds every change' in redispatch
    assert "as those rules say" in redispatch
    assert "never commented on" not in redispatch and "joined by a second" not in redispatch
    assert not re.search(r"never comments? on an issue (?:it|this run) created", flat), flat


def test_the_contract_renders_the_ticket_with_every_key_heading_and_status_rule() -> None:
    contract = tickets.ticket_contract(RUN, "followups")

    for key in tickets.RECORD_KEYS:
        assert f'"{key}"' in contract, key
    for heading in tickets.HEADINGS:
        assert f"## {heading}" in contract, heading
    assert "no `project`" in contract
    flat = " ".join(contract.split())
    assert (
        "**Its `repositories` names exactly one normalized origin, its record's `repository`**"
    ) in flat
    assert (
        "Its issue is created in that one repository and added to the board as an item, and "
        "that repository must belong to the board's owner"
    ) in flat
    assert f"{tickets.OUTSIDE_OWNER} when the ticket's repository is not one of the board's" in flat
    assert (
        f"When `board-status` exits {tickets.OUTSIDE_OWNER}, or `@PLAN_STORE@ task copy` refuses "
        "the ticket"
    ) in flat
    assert (
        "copy nothing for that ticket, never retry it with `repositories` removed or changed to "
        "get it filed, and report what was printed"
    ) in flat
    assert '\nrepositories: ["<normalized origin the root cause lives in' in contract
    assert f"A new ticket is `{tickets.Status.PROPOSED}`" in contract
    assert "A ticket the board already holds carries the status the board holds it at" in contract
    assert f"withdraws is `{tickets.Status.WITHDRAWN}`" in flat
    assert (
        "unless the board shows it as accepted or deferred: this run never withdraws a deferred "
        "item or an accepted one, so copy nothing, leave the local"
    ) in flat
    assert "report that you would have withdrawn it and why" in flat
    assert "Run `hostname` on the machine you run on and write exactly what it prints" in contract
    assert "@BOARD_STATUS@ --board followups <path of the ticket>" in contract

    impact = contract.split("\n## Impact\n\n", 1)[1].split("\n## Examples\n", 1)[0]
    assert contract.index("\n## Root cause\n") < contract.index("\n## Impact\n"), contract
    assert tickets.HEADINGS[: tickets.HEADINGS.index(tickets.IMPACT) + 2] == (
        "Root cause",
        "Impact",
        "Examples",
    )
    for label in ("Severity", "Workaround", "Severity with the workaround"):
        assert re.search(rf"^- {label}: <", impact, re.MULTILINE), label
    flat_impact = " ".join(impact.split())
    assert "the negative outcome when the root cause fires, and what it affects" in flat_impact
    assert (
        "Then the three lines below, each exactly once, in this order, with nothing between or "
        "after them"
    ) in flat_impact
    for severity in tickets.Severity:
        assert f"`{severity}`, {severity.meaning}" in flat_impact, severity
    assert "`critical`, " in flat_impact.split("`high`, ", 1)[0], "most severe first"
    assert "The severity with the workaround is never above the severity" in flat_impact
    assert "a workaround of exactly `none` leaves the two the same" in flat_impact


def test_feedback_reaches_the_task_verbatim_under_its_own_heading() -> None:
    feedback = "Merge @RUN@'s two cursor tickets & drop `\\1`; keep @TICKET_CONTRACT@ literal.\n"

    task = _compose(feedback=feedback, redispatch=True)

    heading = "## Feedback on the previous follow-up run"
    assert heading in task
    assert feedback.rstrip() in task.split(heading, 1)[1]
    assert "## This is a re-dispatch" in task
    assert "an issue run `listing-run` created is **edited**" in task
    flat = " ".join(task.split())
    assert (
        f"an existing ticket of run `listing-run` is copied again carrying the board's status, "
        f"which `{BOARD_STATUS} --board followups <path of the ticket>` prints"
    ) in flat
    assert (
        "a ticket of an older schema is brought to the current shape before it is copied, "
        "its `repositories` naming its record's `repository`, its `host` read from this "
        "machine with `hostname`, and its `## Impact` section written from the evidence the "
        "ticket already carries, re-verifying only a claim that no longer holds"
    ) in flat


@pytest.mark.parametrize(
    ("template", "reason"),
    [
        (TEMPLATE + "@UNKNOWN@", "placeholders nothing fills: UNKNOWN"),
        (TEMPLATE.replace("@FEEDBACK@", ""), "missing placeholders: FEEDBACK"),
        (TEMPLATE.replace("@BOARD_STATUS@", ""), "missing placeholders: BOARD_STATUS"),
        (TEMPLATE + "@COMMENT_CONTRACT@", "more than once: COMMENT_CONTRACT"),
        (TEMPLATE + "@STATUS_VOCABULARY@", "more than once: STATUS_VOCABULARY"),
        (TEMPLATE.replace("@STATUS_VOCABULARY@", ""), "missing placeholders: STATUS_VOCABULARY"),
    ],
)
def test_a_template_that_does_not_name_each_placeholder_once_is_refused(
    template: str, reason: str
) -> None:
    with pytest.raises(tickets.Refused, match=reason):
        tickets.compose(
            template,
            run=RUN,
            board="followups",
            drafts_root=Path("/r"),
            validate="v",
            board_status="s",
            checkout=Path("/checkout"),
            plan_store=PLAN_STORE,
            feedback=None,
            redispatch=False,
        )


@pytest.mark.parametrize(
    ("plan_store", "refusal"),
    [
        ("onetaskgraph", "is not an absolute path"),
        ("/no/such/plan/store", "is not an executable file"),
    ],
    ids=["a-bare-name", "a-program-that-is-not-there"],
)
def test_a_plan_store_the_dispatch_could_not_run_as_written_is_refused(
    plan_store: str, refusal: str
) -> None:
    """The two ways the program a task names is not one the dispatch can run.

    The agent reads its task in its agent graph's scratch directory, so a relative or bare
    name there is answered by that dispatch's own search path — the defect the full
    spelling closes — and a path to nothing is a "command not found" the dispatch meets
    after the launch, with the task already written.
    """
    with pytest.raises(tickets.Refused, match=refusal):
        tickets.compose(
            TEMPLATE,
            run=RUN,
            board="followups",
            drafts_root=Path("/drafts-root"),
            validate="v",
            board_status="s",
            checkout=Path("/checkout"),
            plan_store=plan_store,
            feedback=None,
            redispatch=False,
        )


def test_a_plan_store_the_shell_would_not_read_as_one_word_is_refused(tmp_path: Path) -> None:
    """An absolute, executable path is still no program when the shell would split it.

    Every store instruction in the task is shell embedded in Markdown and is read as
    written, so a path carrying a space — or a quote, a backtick, a `$` — reaches the
    dispatch as several words or as shell syntax rather than as the one program the recipe
    resolved. This is the same defect as a bare name arriving by another route, so it is
    refused where the other two are, before any task exists.
    """
    directory = tmp_path / "plan store"
    directory.mkdir()
    program = directory / "onetaskgraph"
    program.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    program.chmod(0o755)

    with pytest.raises(tickets.Refused, match="does not read as part of one word"):
        tickets.compose(
            TEMPLATE,
            run=RUN,
            board="followups",
            drafts_root=Path("/drafts-root"),
            validate="v",
            board_status="s",
            checkout=Path("/checkout"),
            plan_store=str(program),
            feedback=None,
            redispatch=False,
        )


@pytest.fixture
def drafts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A drafts root the installed store reads, named the way a launch exports it."""
    root = tmp_path / "follow-ups"
    root.mkdir()
    monkeypatch.setenv(follow_up_variables.root_name(), str(root))
    monkeypatch.setenv(follow_up_variables.plugin_name(), WRITABLE_PLUGIN)
    return root


def _write(root: Path, ticket: tickets.Ticket, text: str | None = None) -> Path:
    path = tickets.ticket_path(root, ticket.created_by_run, ticket.root_cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else tickets.render(ticket), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "body", [BODY, _body(rejected=REJECTED_TEXT)], ids=["one-fix", "one-fix-and-rejected-fixes"]
)
def test_validate_reads_a_rendered_ticket_through_the_store_as_sound(
    drafts_root: Path, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    path = _write(drafts_root, _ticket(body=body))

    assert tickets.main(["validate", str(path)]) == tickets.SOUND
    assert tickets.read_ticket(path) == _ticket(body=body)
    assert "is a sound ticket" in capsys.readouterr().out


@pytest.mark.parametrize("status", list(tickets.Status))
def test_the_store_reads_each_status_a_ticket_is_written_with_as_that_status(
    drafts_root: Path, status: tickets.Status
) -> None:
    """A status is written as its category's own word, and the store reads it back as that.

    Every one of them, `in-progress` included: the adopted store reads the canonical word
    as its own category, so this repository spells none of them some other way.
    """
    path = _write(drafts_root, _ticket(status=status))
    run, cause = tickets.located_path(path)
    item = plan_store.one_item(
        plan_store.store_json(["task", "show", tickets.qualified_id(run, cause)]), "task"
    )

    assert item["status"] == {"category": status.value, "name": status.value}
    assert tickets.read_ticket(path).status is status


def test_validate_names_each_problem_of_a_ticket_the_store_reads(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rendered = tickets.render(_ticket()).replace(
        'status: "backlog"', 'status: "backlog"\nproject: "p"'
    )
    path = _write(drafts_root, _ticket(), rendered)

    assert tickets.main(["validate", str(path)]) == tickets.UNSOUND
    reported = capsys.readouterr().err
    assert f"{path} is not a sound ticket" in reported
    assert "carries a `project`" in reported


@pytest.mark.parametrize(("body", "reason"), IMPACT_REFUSALS + FIX_REFUSALS)
def test_the_validate_command_names_each_body_problem_of_a_ticket_the_store_reads(
    drafts_root: Path, body: str, reason: str
) -> None:
    """The command the follow-up agent and the recipe's closeout run, over a written ticket."""
    path = _write(drafts_root, _ticket(body=body))

    validated = subprocess.run(  # noqa: S603 - this checkout's own module, as an agent runs it
        [sys.executable, "-m", "orchestrator.follow_up_tickets", "validate", str(path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert validated.returncode == tickets.UNSOUND, validated.stdout + validated.stderr
    assert f"{path} is not a sound ticket" in validated.stderr
    assert reason in " ".join(validated.stderr.split()), validated.stderr


def test_validate_refuses_a_ticket_the_store_cannot_read_or_reads_from_elsewhere(
    drafts_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tickets.ticket_path(drafts_root, RUN, "never-written")
    elsewhere = _write(tmp_path / "another-root", _ticket())
    _write(drafts_root, _ticket())

    assert tickets.main(["validate", str(missing), str(elsewhere)]) == tickets.UNSOUND
    reported = capsys.readouterr().err
    assert "the store could not read" in reported
    assert "validate a ticket under the drafts root" in reported


def test_check_run_validates_every_ticket_a_run_holds(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert tickets.main(["check-run", "--root", str(drafts_root), RUN]) == tickets.SOUND
    _write(drafts_root, _ticket())
    broken = _ticket(root_cause="second-cause", title="some-service: a second cause")
    _write(drafts_root, broken, tickets.render(broken).replace("## Examples", "## Samples"))

    assert tickets.main(["check-run", "--root", str(drafts_root), RUN]) == tickets.UNSOUND
    captured = capsys.readouterr()
    assert f"{CAUSE}.md is a sound ticket" in captured.out
    assert "second-cause.md is not a sound ticket" in captured.err


#: The local store standing in for the board `board-status` asks, spelled lowercase because
#: it is spelled into the store's environment layer as well as onto `--board`.
BOARD = "ticketboard"
#: A status a person could type onto a board item that the store's vocabulary cannot place.
UNPLACEABLE = "waiting-on-vendor"


@pytest.fixture
def board(drafts_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A second local store standing in for the board, beside the drafts root."""
    root = tmp_path / "board"
    root.mkdir()
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__PLUGIN", WRITABLE_PLUGIN)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__CONFIG__ROOT", str(root))
    return root


def _on_board(ticket: Path) -> str:
    """Copy ``ticket`` onto the board through the store, as the agent does; its item's id."""
    run, cause = tickets.located_path(ticket)
    copied = plan_store.store_json(
        ["task", "copy", tickets.qualified_id(run, cause), "--to", BOARD]
    )
    destination = copied["items"][0]["destination"]
    assert isinstance(destination, str), copied
    return destination


def _board_item(destination: str) -> dict[str, object]:
    return dict(plan_store.one_item(plan_store.store_json(["task", "show", destination]), "task"))


def _moved(destination: str, word: str) -> None:
    """Move the board item to ``word``, the way a person moves an item on the board.

    Through the store's own `task status set` for a word the store's vocabulary places,
    which is every move a person makes on the real board. :data:`UNPLACEABLE` is the one
    word here that names no category, so the store refuses to set it; that one is written
    into the `local-md` item's file, which is what a `local-md` item is, and the store
    reads the edit back through `task show`.
    """
    if word != UNPLACEABLE:
        plan_store.store_json(["task", "status", "set", destination, word])
        return
    location = _board_item(destination)["location"]
    assert isinstance(location, dict)
    path = Path(str(location["path"]))
    text = path.read_text(encoding="utf-8")
    moved = re.sub(r"^status: .*$", f"status: {json.dumps(word)}", text, count=1, flags=re.M)
    assert moved != text or f"status: {json.dumps(word)}" in text, text
    path.write_text(moved, encoding="utf-8")


def _board_category(destination: str) -> object:
    status = _board_item(destination)["status"]
    assert isinstance(status, dict)
    return status["category"]


def _decided(ticket: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str, str]:
    status = tickets.main(["board-status", "--board", BOARD, *extra, str(ticket)])
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_board_status_answers_backlog_for_a_ticket_the_board_holds_no_item_for(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = _write(drafts_root, _ticket(status=tickets.Status.ACCEPTED))

    assert _decided(ticket, capsys) == (tickets.SOUND, "backlog\n", "")
    assert not any(board.rglob("*.md")), "deciding a status wrote to the board"


@pytest.mark.parametrize("held", list(tickets.Status))
def test_board_status_answers_the_status_the_board_holds_the_item_at(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str], held: tickets.Status
) -> None:
    ticket = _write(drafts_root, _ticket())
    destination = _on_board(ticket)
    _moved(destination, held.value)
    assert _board_category(destination) == held.value

    status, printed, _ = _decided(ticket, capsys)

    assert (status, printed) == (tickets.SOUND, f"{held.value}\n")
    written = _write(drafts_root, _ticket(status=tickets.Status(held)))
    assert tickets.read_ticket(written).status is held, "the printed word is not that status"


def test_board_status_refuses_an_item_the_store_cannot_place_with_a_status_of_its_own(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = _write(drafts_root, _ticket())
    destination = _on_board(ticket)
    _moved(destination, UNPLACEABLE)
    assert _board_category(destination) == "unknown"

    for extra in ((), ("--withdraw",)):
        status, printed, reported = _decided(ticket, capsys, *extra)

        assert status == tickets.UNPLACED, extra
        assert tickets.UNPLACED not in (
            tickets.SOUND,
            tickets.UNRUNNABLE,
            tickets.PROTECTED,
        )
        assert printed == ""
        assert "at category 'unknown', which no ticket carries" in reported


@pytest.mark.parametrize("held", [None, tickets.Status.PROPOSED, tickets.Status.WITHDRAWN])
def test_a_withdrawal_closes_a_ticket_nobody_accepted(
    board: Path,
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    held: tickets.Status | None,
) -> None:
    ticket = _write(drafts_root, _ticket())
    if held is not None:
        _moved(_on_board(ticket), held.value)

    assert _decided(ticket, capsys, "--withdraw") == (tickets.SOUND, "cancelled\n", "")


def test_withdrawal_is_refused_at_every_accepted_status_and_the_deferred_one_and_no_other() -> None:
    decided = [status for status in tickets.Status if status.protected_from_withdrawal]

    assert decided == [
        tickets.Status.ACCEPTED,
        tickets.Status.DEFERRED,
        tickets.Status.QUEUED,
        tickets.Status.UNDER_WAY,
        tickets.Status.FINISHED,
    ]
    assert not tickets.Status.DEFERRED.accepted
    assert tickets.Status.QUEUED.accepted, "a claimed ticket was accepted before it was claimed"
    assert [status for status in tickets.Status if status.selected] == [tickets.Status.ACCEPTED]


@pytest.mark.parametrize(
    "held", [status for status in tickets.Status if status.protected_from_withdrawal]
)
def test_a_withdrawal_of_a_ticket_a_person_accepted_or_deferred_is_refused_and_moves_nothing(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str], held: tickets.Status
) -> None:
    ticket = _write(drafts_root, _ticket())
    before = ticket.read_text(encoding="utf-8")
    destination = _on_board(ticket)
    _moved(destination, held.value)

    status, printed, reported = _decided(ticket, capsys, "--withdraw")

    assert status == tickets.PROTECTED == 4
    assert tickets.PROTECTED not in (tickets.SOUND, tickets.UNRUNNABLE, tickets.UNPLACED)
    assert printed == ""
    assert (
        f"holds this ticket's item at `{held}` ({held.meaning}), which a person accepted or "
        "deferred, so this run never withdraws it"
    ) in " ".join(reported.split())
    assert "this run never withdraws it" in reported
    assert _board_category(destination) == held.value
    assert ticket.read_text(encoding="utf-8") == before


def test_board_status_over_a_deferred_item_prints_draft_and_refuses_to_withdraw_it(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A person deferred the item: a copy carries `draft`, and a withdrawal is refused."""
    ticket = _write(drafts_root, _ticket())
    destination = _on_board(ticket)
    _moved(destination, "draft")
    assert _board_category(destination) == tickets.Status.DEFERRED == "draft"

    assert _decided(ticket, capsys) == (tickets.SOUND, "draft\n", "")
    status, printed, reported = _decided(ticket, capsys, "--withdraw")

    assert (status, printed) == (tickets.PROTECTED, "")
    assert f"at `draft` ({tickets.Status.DEFERRED.meaning})" in " ".join(reported.split())
    assert "this run never withdraws it" in reported
    assert _board_category(destination) == "draft"
    deferred = _write(drafts_root, _ticket(status=tickets.Status.DEFERRED))
    assert tickets.read_ticket(deferred).status is tickets.Status.DEFERRED


def test_board_status_over_a_queued_item_prints_queued_and_refuses_to_withdraw_it(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A launched run claimed the item: a re-copy carries `queued`, and a withdrawal is refused.

    The command is run as an agent runs it, over the `local-md` stand-in board the fixtures
    stand up, so what answers is the installed store reading the item a person's edit left.
    A withdrawal is refused for the reason every accepted status is: the ticket was at `Todo`
    before a run could claim it, and only a person undoes that.
    """
    ticket = _write(drafts_root, _ticket())
    destination = _on_board(ticket)
    _moved(destination, "queued")
    assert _board_category(destination) == tickets.Status.QUEUED == "queued"

    kept = subprocess.run(  # noqa: S603 - this checkout's own module, as an agent runs it
        [
            sys.executable,
            "-m",
            "orchestrator.follow_up_tickets",
            "board-status",
            "--board",
            BOARD,
            str(ticket),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert (kept.returncode, kept.stdout, kept.stderr) == (tickets.SOUND, "queued\n", "")
    assert tickets.status_before_copy("queued", withdraw=False) is tickets.Status.QUEUED

    status, printed, reported = _decided(ticket, capsys, "--withdraw")

    assert (status, printed) == (tickets.PROTECTED, "")
    assert f"at `queued` ({tickets.Status.QUEUED.meaning})" in " ".join(reported.split())
    assert "this run never withdraws it" in reported
    assert _board_category(destination) == "queued"


def test_the_status_vocabulary_says_what_each_status_is_and_that_only_todo_is_picked_up() -> None:
    vocabulary = tickets.status_vocabulary()
    bullets = [line for line in vocabulary.splitlines() if line.startswith("- ")]
    shown = [
        "Board status `Proposal`",
        "Board status `Todo`",
        "Board status `Deferred`",
        "Board status `Queued`",
        "Board status `In Progress`",
        "Closed as completed",
        "Closed as not planned",
    ]

    assert len(bullets) == len(tickets.Status) == len(shown)
    for bullet, status, place in zip(bullets, tickets.Status, shown, strict=True):
        assert bullet.startswith(f"- **{place}**, written `{status.value}`: "), bullet
        assert status.meaning in bullet, bullet
        assert "Who moves an item there: " in bullet, bullet
    selected = [
        bullet for bullet in bullets if "**Selected** by an agent sent to pick up" in bullet
    ]
    assert selected == [bullets[1]], selected
    assert all(
        "Not selected by an agent sent to pick up accepted tickets" in bullet
        for bullet in bullets
        if bullet not in selected
    )
    assert "Who moves an item there: only a person, which is what accepting" in bullets[1]
    assert "Who moves an item there: only a person." in bullets[2]
    deferred = tickets.Status.DEFERRED.meaning
    for said in ("deferred for later", "not accepted", "picked up by no agent", "new evidence"):
        assert said in deferred, said
    assert vocabulary.rstrip("\n").splitlines()[-1] == (
        'A brief to pick up "accepted" follow-up tickets means the items at `Todo` and nothing '
        "else: never an item at `Proposal`, `Deferred`, `Queued` or `In Progress`, and never a "
        "closed one."
    )


def test_the_statuses_command_prints_the_vocabulary_exactly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    printed = subprocess.run(  # noqa: S603 - this checkout's own module, as an agent runs it
        [sys.executable, "-m", "orchestrator.follow_up_tickets", "statuses"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert (printed.returncode, printed.stdout, printed.stderr) == (
        tickets.SOUND,
        tickets.status_vocabulary(),
        "",
    )
    assert tickets.main(["statuses"]) == tickets.SOUND
    assert capsys.readouterr().out == tickets.status_vocabulary()


def test_board_status_that_cannot_ask_the_board_is_unrunnable(
    board: Path,
    drafts_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = _write(drafts_root, _ticket())

    assert tickets.main(["board-status", "--board", "no-such-board", str(ticket)]) == (
        tickets.UNRUNNABLE
    )
    assert 'no source named "no-such-board"' in capsys.readouterr().err

    stray = tmp_path / "not-a-ticket.md"
    assert tickets.main(["board-status", "--board", BOARD, str(stray)]) == tickets.UNRUNNABLE
    assert "is not where a ticket is stored" in capsys.readouterr().err

    # A dry-run answer naming neither a new item nor an existing one, which the installed
    # store does not give: the one answer here that stands in for the store. Its settings
    # list is empty, so the board configures no owner and the dry run is what is asked.
    monkeypatch.setattr(plan_store, "store_json", lambda _arguments: {"items": [], "settings": []})
    assert tickets.main(["board-status", "--board", BOARD, str(ticket)]) == tickets.UNRUNNABLE
    assert "naming neither a new item nor an existing one" in capsys.readouterr().err


#: A repository under an owner the committed `followups` source does not configure.
FOREIGN_REPOSITORY = "github.com/contoso/work"


@pytest.mark.parametrize(
    ("repository", "under"),
    [
        (REPOSITORY, True),
        ("github.com/nickderobertis/another-service", True),
        (FOREIGN_REPOSITORY, False),
        ("github.com/nickderobertis-fork/some-service", False),
        ("gitlab.com/nickderobertis/some-service", False),
    ],
)
def test_a_repository_is_under_the_owner_only_as_github_com_owner_name(
    repository: str, under: bool
) -> None:
    assert tickets.under_owner(repository, "nickderobertis") is under


def test_board_status_refuses_a_ticket_outside_the_configured_owner_naming_both(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Against the committed `followups` source, whose `config show` names its owner.

    Nothing reaches the board: the owner is read from the store's configuration, and the
    refusal is decided before the dry-run copy that would ask GitHub anything.
    """
    ticket = _write(
        drafts_root,
        _ticket(
            repository=tickets.Origin(FOREIGN_REPOSITORY),
            title="work: the listing cursor skips the last page",
            basis=(tickets.Basis(tickets.Origin(FOREIGN_REPOSITORY), tickets.Commit(COMMIT)),),
        ),
    )
    owner = plan_store.configured_settings()[f"sources.{tickets.BOARD}.config.owner"]

    status = tickets.main(["board-status", "--board", tickets.BOARD, str(ticket)])

    captured = capsys.readouterr()
    assert status == tickets.OUTSIDE_OWNER == 5
    assert captured.out == ""
    assert f"{FOREIGN_REPOSITORY!r} is not a repository of the board's owner {owner!r}" in (
        captured.err
    )
    assert "never change `repositories` to get it filed" in captured.err


def test_board_status_that_cannot_read_the_owner_or_the_ticket_repository_is_unrunnable(
    drafts_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unreadable = _ticket(root_cause=tickets.RootCause("unreadable-repository"))
    ticket = _write(
        drafts_root,
        unreadable,
        tickets.render(unreadable).replace(f'"repository": "{REPOSITORY}"', '"repository": "x"'),
    )

    assert tickets.main(["board-status", "--board", tickets.BOARD, str(ticket)]) == (
        tickets.UNRUNNABLE
    )
    assert "names no `repository` in its `orchestrator.follow-up` record" in (
        capsys.readouterr().err
    )

    # An owner the store does not give, since its schema holds the setting to a login: the
    # one answer here that stands in for the store.
    monkeypatch.setattr(
        plan_store, "configured_settings", lambda: {f"sources.{tickets.BOARD}.config.owner": ""}
    )
    assert tickets.main(["board-status", "--board", tickets.BOARD, str(ticket)]) == (
        tickets.UNRUNNABLE
    )
    assert "configures an owner '' that names no account" in capsys.readouterr().err


def test_inventory_counts_a_runs_drafts_and_tickets(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    drafts = drafts_root / "tasks" / RUN / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "one.md").write_text("x", encoding="utf-8")
    (drafts / "two.md").write_text("x", encoding="utf-8")
    _write(drafts_root, _ticket())

    assert tickets.main(["inventory", "--root", str(drafts_root), RUN]) == tickets.SOUND
    assert capsys.readouterr().out == "2 1\n"
    assert tickets.inventory(drafts_root, "nothing-here") == (0, 0)


def test_compose_marks_a_run_holding_tickets_as_a_re_dispatch(
    drafts_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    template = tmp_path / "template.md"
    template.write_text(TEMPLATE, encoding="utf-8")
    arguments = [
        "compose",
        "--template",
        str(template),
        "--root",
        str(drafts_root),
        "--run",
        RUN,
        "--board",
        "followups",
        "--validate",
        "v",
        "--board-status",
        "s",
        "--checkout",
        "/checkout",
        "--plan-store",
        PLAN_STORE,
    ]

    assert tickets.main(arguments) == tickets.SOUND
    assert "This is a re-dispatch" not in capsys.readouterr().out
    _write(drafts_root, _ticket())
    assert tickets.main(arguments) == tickets.SOUND
    assert "This is a re-dispatch" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (["inventory", "--root", "/r", "../escape"], "is not a run id"),
        (
            ["compose", "--template", "/no/such/template", "--root", "/r", "--run", RUN]
            + ["--board", "b", "--validate", "v", "--board-status", "s", "--checkout", "/c"]
            + ["--plan-store", PLAN_STORE],
            "No such file",
        ),
    ],
)
def test_an_invocation_that_cannot_run_is_its_own_status(
    arguments: list[str], reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert tickets.main(arguments) == tickets.UNRUNNABLE
    assert reason in capsys.readouterr().err


def test_a_command_line_the_parser_refuses_exits_unrunnable_saying_what_to_do(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exited:
        tickets.main(["validate"])
    assert exited.value.code == tickets.UNRUNNABLE
    assert "run it with --help for the contract" in capsys.readouterr().err


def test_a_template_the_compose_command_refuses_is_unrunnable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    template = tmp_path / "template.md"
    template.write_text("@RUN@ only", encoding="utf-8")

    status = tickets.main(
        ["compose", "--template", str(template), "--root", str(tmp_path), "--run", RUN]
        + ["--board", "b", "--validate", "v", "--board-status", "s", "--checkout", "/c"]
        + ["--plan-store", PLAN_STORE]
    )

    assert status == tickets.UNRUNNABLE
    assert "missing placeholders" in capsys.readouterr().err
