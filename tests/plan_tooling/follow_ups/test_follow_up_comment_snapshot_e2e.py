"""A comment-answer pass against the real `github-projects` plugin on the loopback board.

What `orchestrator/follow_up_comments.py` promises is about requests: one copy of the board
into a snapshot, nothing read from the board until settle, one batched read there, and every
write after it a single field-scoped mutation verified from its own response. A local folder
standing in for the board sends no request and keeps no cache, so it can prove none of that;
here the board is `tests/github_board.py`'s loopback GitHub, served in this process, read and
written by the locked plan store through its real plugin with the cache this checkout's
`onetaskgraph.yaml` turns on, and the loopback's own request log is what each test reads.

Each pass is driven through the module's own steps — `gather`, `launch` and `settle` — in
this process, and the answering agent's part is the commands its task names: each quoted
issue read from the snapshot source, each reply posted with `task comment add <board id>
--expect <version> --json`, and the account written from that answer. What a person does on
the board between the steps is done through the store's own verbs where one exists, and
planted on the loopback's row where none does (an edited comment), as the other loopback
journeys plant a person's move.

llmlint: ignore-file[tests_mirror_real_usage, e2e_not_mocked] Each step runs as its command
does, through the module's own `main` with the command's own arguments —
`orchestrator.follow_up_comments`' `gather`, `launch` and `settle`,
`orchestrator.follow_up_tickets`' `board-status`, `copy`, `check-gathering` and
`check-responses` — in this process rather than a child, because these journeys are what holds
those modules to the coverage floor and pytest-cov measures no child process here
(`pyproject.toml`'s `[tool.coverage.run]` patches none); every board request still goes through
the locked store CLI to the loopback. `recover`'s carried entries are written with
`write_pending`, the one writer of `pending.json`, for the states only a lost snapshot or a
vanished item leaves, which no step here can produce.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This project is the tier
for follow-up journeys over the loopback board and the installed store, and its key covers
what these read: `orchestrator/**`, `onetaskgraph.yaml`, `config/**` and the board support
project. A project of its own would add one more data file to the coverage floor's combine
for a suite of under two minutes whose key would differ from this one's by the scripts alone.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import socket
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import github_board as remote
import pytest
from github_board import _GitHubFixture, _Operation
from published_tools import ONETASKGRAPH_BIN

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.root import REPO_ROOT

HOST = socket.gethostname()
RUN = remote.PROPOSED_RUN
BOARD = tickets.BOARD
#: Before every comment the loopback dates, whose clock starts at its issues' creation.
EARLY = "2026-08-25T00:00:00Z"
#: What a person asks, and what a person later rewrites it to.
ASKED = "Is this still happening on the latest release?\n"
REWRITTEN = "Rewritten after the preview: does it also drop page 10?\n"
LATER = "Written after the preview was taken.\n"
#: A person's edit of the issue's own text, made on the board after the preview.
EDITED_BODY = "A person rewrote this issue after the preview."
#: What every reply this module's agent posts says.
RESPONSE = "Checked the ticket against the latest release; it still holds."


@dataclass
class Loopback:
    """The drafts root a pass reads, and the loopback board it reaches, request by request."""

    root: Path
    tmp: Path
    environment: dict[str, str]

    @property
    def requests(self) -> list[remote._GraphQLRequest]:
        return _GitHubFixture.requests

    def mark(self) -> int:
        """Where the request log stands now, so a step's own requests are read off it."""
        return len(_GitHubFixture.requests)

    def since(self, mark: int) -> list[_Operation]:
        """Every operation the board was asked since ``mark``, in order."""
        return [request.operation for request in _GitHubFixture.requests[mark:]]

    def store(self, *arguments: str) -> dict[str, object]:
        """One plan-store command, as a person or the agent runs it: its JSON answer."""
        done = subprocess.run(  # noqa: S603 - the locked plan store
            [str(ONETASKGRAPH_BIN), *arguments, "--json"],
            cwd=REPO_ROOT,
            env=dict(os.environ),
            text=True,
            capture_output=True,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        answer: object = json.loads(done.stdout)
        assert isinstance(answer, dict), answer
        return answer


@pytest.fixture
def loopback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Loopback]:
    """The loopback board served as `followups`, its route kept off Hello Patient's Linear."""
    environment, root = remote._followups_environment(tmp_path)
    routed = tmp_path / "routed-followups"
    routed.mkdir()
    environment |= {
        "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__PLUGIN": plan_store.WRITABLE_PLUGIN,
        "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__CONFIG__ROOT": str(routed),
        "ONETASKGRAPH_SOURCES__FOLLOWUPS__ROUTES__0__REPOSITORIES": "github.com/petsinc/*",
        "ONETASKGRAPH_SOURCES__FOLLOWUPS__ROUTES__0__TO": "routedfollowups",
    }
    with remote._serving_followups(environment, fields=True):
        for name in [one for one in os.environ if one.startswith(remote.FOLLOWUPS_ENV_PREFIX)]:
            monkeypatch.delenv(name)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        yield Loopback(root, tmp_path, environment)


def _filed(loopback: Loopback, cause: str = remote.PROPOSED_CAUSE, **facts: object) -> str:
    """The run's ticket for ``cause``, decided, validated and copied as the agent does; its item.

    Its file is dated before the board's clock, so the boundary its copy gives stands behind
    every comment a test writes.
    """
    ticket = remote._follow_up_ticket(
        remote.SIBLING_REPOSITORY, cause=cause, verified_on=HOST, **facts
    )
    path = remote._written_ticket(loopback.root, ticket)
    for verb in ("board-status", "copy"):
        assert tickets.main([verb, "--board", BOARD, str(path)]) == tickets.SOUND
    os.utime(path, (0, 0))
    record = tickets.read_ticket(path, pending=True)
    assert record.board_item is not None
    return f"{BOARD}:{record.board_item}"


def _said(loopback: Loopback, issue: str, body: str) -> str:
    """A person's comment on ``issue``, through the store's own verb; its id."""
    path = loopback.tmp / f"said-{len(loopback.requests)}.md"
    path.write_text(body, encoding="utf-8")
    added = loopback.store("task", "comment", "add", issue, "--body-file", str(path))
    return str(added["id"])


def _row(issue: str) -> remote._Issue:
    """The loopback's own row of ``issue``."""
    (row,) = [one for one in remote.BOARD.issues if one.content_id == issue.partition(":")[2]]
    return row


# llmlint: ignore-block[tests_mirror_real_usage] What a person does on GitHub is the change
# under test, and a person does it on github.com rather than through this host's plan store,
# whose cache it would touch; the loopback's row is where each such act leaves the board. Every
# read and write the pass makes, before and after, is the locked CLI unchanged.
def _edited_on_board(issue: str, comment: str, body: str, at: str) -> None:
    """A person's edit of one comment, made on GitHub."""
    (held,) = [one for one in _row(issue).comments if one["id"] == comment]
    held["body"], held["updatedAt"] = body, at


def _said_on_board(issue: str, body: str, at: str) -> str:
    """A person's new comment, written on GitHub; its id."""
    row = _row(issue)
    identifier = f"IC_person_{len(row.comments)}"
    row.comments.append(
        {
            "id": identifier,
            "author": {"login": "a-person"},
            "createdAt": at,
            "updatedAt": at,
            "body": body,
            "url": f"{row.content()['url']}#issuecomment-{identifier}",
        }
    )
    return identifier


def _rewritten_on_board(issue: str, text: str) -> None:
    """A person's rewrite of the issue's own text, made on GitHub, its record kept."""
    row = _row(issue)
    content, separator, record = row.body.partition("\n\n<!-- onetaskgraph.metadata")
    assert separator, row.body
    row.body = f"{text}{separator}{record}"


# llmlint: ignore-end[tests_mirror_real_usage]


@dataclass
class Pass:
    """One pass as the recipe takes it: what it printed, its snapshot and what it launched."""

    report: list[str] = field(default_factory=list)
    snapshot: comments.Snapshot | None = None
    plan: comments.Plan | None = None


def _gathered(loopback: Loopback, *arguments: str) -> Pass:
    """`gather` with ``arguments``, then `launch`, as `scripts/follow-ups-answer-comments.sh`."""
    intent, plan = loopback.tmp / "intent.json", loopback.tmp / "plan.json"
    held = Pass()
    status = comments.main(
        ["gather", "--root", str(loopback.root), "--intent", str(intent), *arguments]
    )
    assert status == comments.DONE
    named = json.loads(intent.read_text(encoding="utf-8"))["snapshot"]
    if named is not None:
        held.snapshot = comments.read_snapshot(Path(named))
        status = comments.main(
            ["launch", "--root", str(loopback.root), "--intent", str(intent), "--plan", str(plan)]
        )
        assert status == comments.DONE
        held.plan = comments.read_plan(plan, loopback.root)
    return held


def _answered(
    loopback: Loopback,
    plan: comments.Plan,
    verdict: tickets.Verdict = tickets.Verdict.DOES_NOT_CONFIRM,
    *,
    edit: Callable[[Path], None] | None = None,
) -> dict[str, list[dict[str, object]]]:
    """The answering agent's part, as its task names it: each quoted issue read from the
    snapshot, each reply posted with `--expect` the version its `Issue` line gives, and the
    account written from each reply's own answer. ``edit`` changes the run's ticket first.

    Returns what each read of the snapshot answered, by issue.
    """
    read: dict[str, list[dict[str, object]]] = {}
    environment = comments.snapshot_environment(plan.snapshot)
    latest: dict[str, str] = {}
    for run, feedback in plan.runs:
        gathering = tickets.read_gathering(feedback.read_text(encoding="utf-8"))
        responses = []
        for quoted, fields in zip(gathering.quoted, gathering.metadata, strict=True):
            line = comments.ISSUE_VALUE.fullmatch(str(fields.issue))
            assert line is not None, fields
            shown = subprocess.run(  # noqa: S603 - the locked plan store
                [str(ONETASKGRAPH_BIN), "task", "show", line["snapshot_id"], "--json"],
                cwd=REPO_ROOT,
                env={**os.environ, **environment},
                text=True,
                capture_output=True,
                check=True,
            )
            read.setdefault(quoted.issue, []).append(json.loads(shown.stdout))
            item = json.loads(shown.stdout)["items"][0]["item"]
            cause = item["metadata"][tickets.KEY]["root_cause"]
            if edit is not None:
                edit(tickets.ticket_path(loopback.root, run, cause))
            body = loopback.tmp / f"reply-{quoted.comment}.md"
            body.write_text(
                tickets.render_reply(
                    run,
                    cause,
                    answers=quoted.comment,
                    url=str(fields.url),
                    author=fields.author,
                    response=RESPONSE,
                    verdict=verdict,
                ),
                encoding="utf-8",
            )
            # After a reply on the same issue, the version that reply's answer carried.
            version = latest.get(line["issue"], line["version"])
            expect = [] if version == comments.NO_VERSION else ["--expect", version]
            posted = loopback.store(
                "task", "comment", "add", line["issue"], "--body-file", str(body), *expect
            )
            result = posted.get("result")
            if version != comments.NO_VERSION and isinstance(result, dict):
                # An item changed within the last minute gets no version, so the next reply on
                # it names none either.
                latest[line["issue"]] = str(result.get("version") or comments.NO_VERSION)
            fallback = posted.get("fallback")
            responses.append(
                {
                    "comment": quoted.comment,
                    "issue": quoted.issue,
                    "action": RESPONSE,
                    "reply": posted["id"],
                    "reply_url": posted.get("url"),
                    "reply_fallback": fallback.get("reason")
                    if isinstance(fallback, dict)
                    else None,
                    "verdict": verdict.value,
                }
            )
        tickets.responses_path(feedback).write_text(
            json.dumps(
                {
                    "schema": tickets.RESPONSES_SCHEMA,
                    "run": run,
                    "feedback": feedback.name,
                    "responses": responses,
                }
            ),
            encoding="utf-8",
        )
        assert (
            tickets.main(["check-responses", "--board", BOARD, "--feedback", str(feedback), run])
            == tickets.SOUND
        )
    return read


def _settled(loopback: Loopback, plan: comments.Plan) -> tuple[int, list[str]]:
    said: list[str] = []
    launched = {run: f"{run}-follow-ups" for run, _ in plan.runs}
    return comments.settle(loopback.root, plan, launched, say=said.append), said


WRITES = frozenset(
    {
        _Operation.ADD_COMMENT_WHOLE,
        _Operation.UPDATE_ISSUE_WHOLE,
        _Operation.UPDATE_FIELD_WHOLE,
        _Operation.CLEAR_FIELD_WHOLE,
    }
)


#: The one batched read `task show-many` sends: its items' details, each with its comments, in
#: one aliased request — under the document that also carries their edges for a single item,
#: and under the plain batch for several.
BATCHED_READS = frozenset({_Operation.ISSUE_COPY_DETAILS, _Operation.ISSUE_DETAILS})


class _Batched:
    """Equal to whichever of :data:`BATCHED_READS` a request log names: settle's one read."""

    def __eq__(self, other: object) -> bool:
        return other in BATCHED_READS

    def __hash__(self) -> int:
        return hash(_Operation.ISSUE_DETAILS)

    def __repr__(self) -> str:
        return "<the batched read>"


BATCHED_READ = _Batched()


def test_a_dry_run_launched_with_from_answers_exactly_what_it_showed_and_reads_the_board_once(
    loopback: Loopback,
) -> None:
    """The preview is what is answered, from a copy nothing else changes.

    A dry run copies the board into a snapshot and prints the `--from` that launches it. Then,
    on the board, a person writes a new comment, rewrites the comment the preview selected and
    rewrites the issue's own text. The launch from the snapshot sends no request at all; the
    feedback quotes the comment as the preview showed it, the agent reads it and the issue
    that way from the snapshot source, and its reply is the one write it makes. Settle reads
    the board once, in one batched request, before any write of its own, and withholds the
    ticket's copy because a person changed the item. The next gathering selects the new
    comment and the rewritten one.
    """
    issue = _filed(loopback)
    asked = _said(loopback, issue, ASKED)

    before = loopback.mark()
    preview = _gathered(loopback, "--since", EARLY, "--dry-run")
    gathering = loopback.since(before)
    assert preview.plan is None and preview.snapshot is None
    (directory,) = comments.snapshots_directory(loopback.root).iterdir()
    snapshot = comments.read_snapshot(directory)
    assert snapshot.dry_run and snapshot.selection == {
        RUN: (
            comments.Chosen(
                issue, asked, snapshot.selection[RUN][0].last_changed, comments.body_sha256(ASKED)
            ),
        )
    }
    # One narrowed search page and one batched copy of the item it found: nothing else.
    assert gathering == [_Operation.SEARCH, _Operation.ISSUE_COPY_DETAILS], gathering
    assert not (loopback.root / comments.FEEDBACK_DIRECTORY / RUN).exists()

    later = _said_on_board(issue, LATER, "2026-08-26T03:00:00Z")
    _edited_on_board(issue, asked, REWRITTEN, "2026-08-26T03:00:01Z")
    _rewritten_on_board(issue, EDITED_BODY)

    before = loopback.mark()
    status = comments.main(
        ["gather", "--root", str(loopback.root), "--intent", str(loopback.tmp / "i.json")]
        + ["--from", str(directory)]
    )
    assert status == comments.DONE
    status = comments.main(
        ["launch", "--root", str(loopback.root), "--intent", str(loopback.tmp / "i.json")]
        + ["--plan", str(loopback.tmp / "p.json")]
    )
    assert status == comments.DONE
    assert loopback.since(before) == [], "the launch from the snapshot asked the board"
    plan = comments.read_plan(loopback.tmp / "p.json", loopback.root)
    ((_, feedback),) = plan.runs
    text = feedback.read_text(encoding="utf-8")
    assert tickets.quoted_comments(text) == [tickets.Quoted(issue, tickets.CommentId(asked))]
    assert "Rewritten after the preview" not in text and LATER.strip() not in text
    assert f"```text\n{ASKED.rstrip()}\n```" in text
    # The board's source keeps a cache, so the feedback names the version the reply expects.
    held = snapshot.item(issue)
    assert held is not None and held.version is not None
    assert f"- Issue: {comments.issue_line(issue, held.snapshot_id, held.version)}\n" in text

    before = loopback.mark()
    assert (
        tickets.main(["check-gathering", "--board", BOARD, "--feedback", str(feedback), RUN])
        == tickets.SOUND
    )
    read = _answered(loopback, plan)
    dispatch = loopback.since(before)
    assert dispatch == [_Operation.ADD_COMMENT_WHOLE], dispatch
    (shown,) = read[issue]
    assert EDITED_BODY not in shown["items"][0]["item"]["content"]
    assert [one["body"] for one in shown["comments"]] == [ASKED]

    before = loopback.mark()
    status, said = _settled(loopback, plan)
    settle = loopback.since(before)
    assert status == comments.UNANSWERED, said
    assert settle == [BATCHED_READ], settle
    assert "verified on the board with one batched read of 1 item(s)" in said
    assert f"  {issue}: ticket-copy withheld: the board's content changed since the snapshot" in (
        said
    )
    (pending,) = comments.read_pending(loopback.root)
    assert (pending.write, pending.state, pending.item) == ("ticket-copy", "withheld", issue)

    again = _gathered(loopback, "--since", EARLY)
    assert again.snapshot is not None
    assert [one.comment for one in again.snapshot.selection[RUN]] == [asked, later]
    assert again.snapshot.selection[RUN][0].body_sha256 == comments.body_sha256(REWRITTEN)


def _withdrawing(path: Path) -> None:
    """The agent's withdrawal: `cancelled` written as the ticket's status, nothing else."""
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('status: "backlog"', 'status: "cancelled"', 1), encoding="utf-8")


def _evidenced(loopback: Loopback, issue: str) -> None:
    """Another run's evidence on ``issue``: one more occurrence of its root cause."""
    _said(
        loopback,
        issue,
        tickets.render_comment("another-run", remote.PROPOSED_CAUSE, "Seen on another host."),
    )


def _one_pass(
    loopback: Loopback,
    verdict: tickets.Verdict = tickets.Verdict.DOES_NOT_CONFIRM,
    *,
    edit: Callable[[Path], None] | None = None,
    before_settle: Callable[[], None] | None = None,
) -> tuple[Pass, int, list[str], list[_Operation]]:
    """A whole unscoped pass over one person's comment: gathered, answered and settled.

    ``before_settle`` is what happens on the board between the answer and settle's read.
    Returns the pass, settle's status and report, and what settle sent the board.
    """
    held = _gathered(loopback, "--since", EARLY)
    assert held.plan is not None and held.plan.runs, held
    _answered(loopback, held.plan, verdict, edit=edit)
    if before_settle is not None:
        before_settle()
    before = loopback.mark()
    status, said = _settled(loopback, held.plan)
    return held, status, said, loopback.since(before)


def test_a_priority_a_person_set_before_settle_reads_the_board_stands_and_nothing_writes_it(
    loopback: Loopback,
) -> None:
    """The re-estimate follows the stored estimate only: a person's priority is never rewritten."""
    issue = _filed(loopback)
    _evidenced(loopback, issue)
    _said(loopback, issue, ASKED)

    def priority_set_by_a_person() -> None:
        _row(issue).priority = "Low"

    _, status, said, sent = _one_pass(
        loopback, tickets.Verdict.CONFIRMS, before_settle=priority_set_by_a_person
    )

    assert status == comments.DONE, said
    # The one batched read, then the estimate line and record alone: no field write, so the
    # person's priority is not written over.
    assert sent == [BATCHED_READ, _Operation.UPDATE_ISSUE_WHOLE], sent
    assert _row(issue).priority == "Low"
    # The confirmation still made three occurrences, which the record and its line state.
    record = json.loads(_row(issue).body.split("<!-- onetaskgraph.metadata\n", 1)[1][:-4])
    assert record[tickets.KEY][tickets.ESTIMATE_FIELD] == "high", said


def test_a_withdrawal_is_written_only_while_the_item_is_still_a_proposal(
    loopback: Loopback,
) -> None:
    """The agent withdraws in its ticket's file; settle writes it only at `Proposal`."""
    issue = _filed(loopback)
    _said(loopback, issue, ASKED)

    def moved_to_todo() -> None:
        _row(issue).status = "Todo"

    _, status, said, sent = _one_pass(loopback, edit=_withdrawing, before_settle=moved_to_todo)

    assert status == comments.DONE, said
    assert f"  {issue}: not withdrawn: the board holds it at Todo" in said
    assert sent == [BATCHED_READ], sent
    assert (_row(issue).status, _row(issue).state) == ("Todo", "OPEN")

    other = _filed(loopback, "a-second-cause")
    _said(loopback, other, ASKED)
    _, status, said, sent = _one_pass(loopback, edit=_withdrawing)

    assert status == comments.DONE, said
    assert f"  {other}: withdrawal written (status)" in said
    assert sent == [BATCHED_READ, _Operation.UPDATE_FIELD_WHOLE, _Operation.UPDATE_ISSUE_WHOLE], (
        sent,
        said,
    )
    assert (_row(other).status, _row(other).state) == (remote.CANCELLED.name, "CLOSED")


def test_a_person_editing_between_settles_read_and_its_write_keeps_what_the_write_does_not_set(
    loopback: Loopback,
) -> None:
    """The residual race, as stated: a field the write sets is overwritten, any other kept.

    The ticket's copy changes the issue's text alone — the confirmation's second occurrence
    rewrites its estimate line — and a person edits the issue's title and its text in the
    instant between settle's read and that write.
    """
    issue = _filed(loopback)
    _said(loopback, issue, ASKED)
    raced = "A person's title, set while settle wrote."

    def edited_while_settle_wrote() -> None:
        _row(issue).title = raced
        _rewritten_on_board(issue, "A person's text, set while settle wrote.")

    _GitHubFixture.before_operation[_Operation.UPDATE_ISSUE_WHOLE] = edited_while_settle_wrote
    _, status, said, sent = _one_pass(loopback, tickets.Verdict.CONFIRMS)

    assert status == comments.DONE, said
    assert sent == [BATCHED_READ, _Operation.UPDATE_ISSUE_WHOLE], sent
    (write,) = [one for one in loopback.requests if one.operation is _Operation.UPDATE_ISSUE_WHOLE]
    written = write.variables.get("input")
    assert isinstance(written, dict) and "title" not in written and "body" in written, written
    assert _row(issue).title == raced, "a field the write did not set was overwritten"
    assert "A person's text" not in _row(issue).body, "the field the write set was not written"
    assert "2 occurrences" in _row(issue).body


def test_a_title_a_comment_corrects_reaches_the_board_item_as_that_field_alone(
    loopback: Loopback,
) -> None:
    """The agent retitles its ticket in light of a comment; settle writes the title, one field of
    one update verified from its own answer, and the item's text and status are left as they are."""
    issue = _filed(loopback)
    _said(loopback, issue, ASKED)
    retitled = "oneharness: a ticket lands as a proposal on every board"
    before = _row(issue).body

    def retitle(path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        path.write_text(re.sub(r'(?m)^title: ".*"$', f'title: "{retitled}"', text, count=1))

    _, status, said, sent = _one_pass(loopback, edit=retitle)

    assert status == comments.DONE, said
    assert sent == [BATCHED_READ, _Operation.UPDATE_ISSUE_WHOLE], sent
    assert f"  {issue}: ticket-copy written (title)" in said, said
    (write,) = [one for one in loopback.requests if one.operation is _Operation.UPDATE_ISSUE_WHOLE]
    written = write.variables.get("input")
    assert isinstance(written, dict) and written.get("title") == retitled and "body" not in written
    assert _row(issue).title == retitled
    assert _row(issue).body == before
    assert _row(issue).status == remote.PROPOSAL.name
    assert comments.read_pending(loopback.root) == []


def test_a_withheld_ticket_write_is_a_conflict_while_the_edit_stands_and_retried_once_undone(
    loopback: Loopback,
) -> None:
    """Withheld because a person rewrote the issue after the snapshot, the write is surfaced as a
    conflict and sent nowhere while that rewrite stands; once the issue is back as the snapshot
    held it, the next pass writes it, and nothing is pending."""
    issue = _filed(loopback)
    _evidenced(loopback, issue)
    _said(loopback, issue, ASKED)
    snapshotted = _row(issue).body

    def rewritten_by_a_person() -> None:
        _rewritten_on_board(issue, EDITED_BODY)

    _, status, said, sent = _one_pass(
        loopback, tickets.Verdict.CONFIRMS, before_settle=rewritten_by_a_person
    )

    assert status == comments.UNANSWERED, said
    # The ticket's whole write — its priority, estimate line and record — is withheld.
    assert sent == [BATCHED_READ], sent
    withheld = [
        entry for entry in comments.read_pending(loopback.root) if entry.state == "withheld"
    ]
    assert [(one.write, one.item) for one in withheld] == [("ticket-copy", issue)], withheld

    status, said, sent = _next_pass(loopback)

    assert status == comments.UNANSWERED, said
    assert sent == [BATCHED_READ], sent
    assert f"  pending ticket-copy of {issue}: conflict: a person changed content" in (
        "\n".join(said)
    ), said
    assert EDITED_BODY in _row(issue).body

    _row(issue).body = snapshotted
    status, said, sent = _next_pass(loopback)

    assert status == comments.DONE, said
    assert sent == [BATCHED_READ, _Operation.UPDATE_FIELD_WHOLE, _Operation.UPDATE_ISSUE_WHOLE], (
        sent
    )
    assert f"  pending ticket-copy of {issue}: retried " in "\n".join(said), said
    assert comments.read_pending(loopback.root) == []
    assert "3 occurrences" in _row(issue).body


def test_a_partial_ticket_update_is_pending_and_the_next_pass_sends_only_what_did_not_land(
    loopback: Loopback,
) -> None:
    """Recorded `partial` naming each part; retried on the issue update alone; then removed."""
    issue = _filed(loopback)
    _evidenced(loopback, issue)
    _said(loopback, issue, ASKED)
    _GitHubFixture.refused_operation = _Operation.UPDATE_ISSUE_WHOLE

    held, status, said, sent = _one_pass(loopback, tickets.Verdict.CONFIRMS)

    assert status == comments.UNANSWERED, said
    assert sent == [BATCHED_READ, _Operation.UPDATE_FIELD_WHOLE, _Operation.UPDATE_ISSUE_WHOLE]
    (entry,) = comments.read_pending(loopback.root)
    assert (entry.write, entry.state, entry.item) == ("ticket-copy", "partial", issue)
    assert [(part.part, part.outcome) for part in entry.parts] == [
        ("fields", "landed"),
        ("issue", "rejected"),
    ]
    assert set(entry.intended) == {"priority", "content", "record"}
    assert f"needs attention: 1 write(s) pending in {comments.pending_path(loopback.root)}" in said
    assert f"  pending: ticket-copy of {issue} (partial): " in "\n".join(said)
    # Every reply was verified, so the pending copy holds no watermark.
    assert held.snapshot is not None
    assert comments.read_watermark(loopback.root, BOARD) == (
        held.snapshot.queried_at - comments.OVERLAP
    )

    _GitHubFixture.refused_operation = None
    again = _gathered(loopback, "--since", EARLY)
    assert again.plan is not None and again.plan.runs == ()
    before = loopback.mark()
    status, said = _settled(loopback, again.plan)

    assert status == comments.DONE, said
    assert loopback.since(before) == [BATCHED_READ, _Operation.UPDATE_ISSUE_WHOLE]
    (retry,) = [one for one in loopback.requests[before:] if one.operation in WRITES]
    retried = retry.variables.get("input")
    assert isinstance(retried, dict) and "body" in retried, retried
    assert comments.read_pending(loopback.root) == []
    assert "3 occurrences" in _row(issue).body and _row(issue).priority == "High"


def test_a_retry_that_lands_in_part_or_loses_its_answer_updates_its_entry_until_it_lands(
    loopback: Loopback,
) -> None:
    """A ticket write lost before it reached the board is `unknown`; its retry landing only its
    fields is `partial`, intending the issue alone; that retry lost is `unknown` again; and the
    next lands it, after which nothing is pending."""
    issue = _filed(loopback)
    _evidenced(loopback, issue)
    _said(loopback, issue, ASKED)
    _GitHubFixture.dropped_operation = (_Operation.UPDATE_FIELD_WHOLE, "before")

    _, status, said, _ = _one_pass(loopback, tickets.Verdict.CONFIRMS)

    assert status == comments.UNANSWERED, said
    (entry,) = comments.read_pending(loopback.root)
    assert (entry.write, entry.state) == ("ticket-copy", "unknown"), entry
    assert set(entry.intended) == {"priority", "content", "record"}

    _GitHubFixture.refused_operation = _Operation.UPDATE_ISSUE_WHOLE
    status, said, sent = _next_pass(loopback)

    assert status == comments.UNANSWERED, said
    assert sent == [BATCHED_READ, _Operation.UPDATE_FIELD_WHOLE, _Operation.UPDATE_ISSUE_WHOLE]
    (entry,) = comments.read_pending(loopback.root)
    assert (entry.write, entry.state) == ("ticket-copy", "partial"), entry
    assert set(entry.intended) == {"priority", "content", "record"}, entry
    assert f"  pending ticket-copy of {issue}: retry partial: " in "\n".join(said), said
    assert _row(issue).priority == "High"

    _GitHubFixture.refused_operation = None
    _GitHubFixture.dropped_operation = (_Operation.UPDATE_ISSUE_WHOLE, "before")
    status, said, sent = _next_pass(loopback)

    assert status == comments.UNANSWERED, said
    (entry,) = comments.read_pending(loopback.root)
    assert (entry.write, entry.state) == ("ticket-copy", "unknown"), entry
    assert set(entry.intended) == {"content", "record"}, entry
    assert sent == [BATCHED_READ, _Operation.UPDATE_ISSUE_WHOLE], sent

    status, said, sent = _next_pass(loopback)

    assert status == comments.DONE, said
    assert sent == [BATCHED_READ, _Operation.UPDATE_ISSUE_WHOLE], sent
    assert comments.read_pending(loopback.root) == []
    assert "3 occurrences" in _row(issue).body


def _next_pass(loopback: Loopback) -> tuple[int, list[str], list[_Operation]]:
    """A later pass that gathers nothing new: what its settle said and sent the board."""
    again = _gathered(loopback, "--since", EARLY)
    assert again.plan is not None and again.plan.runs == (), again
    before = loopback.mark()
    status, said = _settled(loopback, again.plan)
    return status, said, loopback.since(before)


def _ticket_gone(loopback: Loopback) -> Callable[[], None]:
    """The run's ticket file taken away before settle, so the item is re-estimated alone."""

    def gone() -> None:
        tickets.ticket_path(loopback.root, RUN, remote.PROPOSED_CAUSE).unlink()

    return gone


def test_a_refused_priority_write_is_failed_and_retried_alone_and_a_persons_change_conflicts(
    loopback: Loopback,
) -> None:
    """A re-estimate's priority is its own write: refused, it is recorded `failed` with the error,
    the estimate line and record still land, and the next pass sends the priority alone. A
    person's change to that priority since is surfaced as a conflict instead, and written over
    by nothing."""
    issue = _filed(loopback)
    _evidenced(loopback, issue)
    _said(loopback, issue, ASKED)
    _GitHubFixture.refused_operation = _Operation.UPDATE_FIELD_WHOLE

    _, status, said, sent = _one_pass(
        loopback, tickets.Verdict.CONFIRMS, before_settle=_ticket_gone(loopback)
    )

    assert status == comments.UNANSWERED, said
    # A refused write takes the store's cached copy of the item out of service, so the write
    # after it reads the item once first, and the report names that read.
    assert sent == [
        BATCHED_READ,
        _Operation.UPDATE_FIELD_WHOLE,
        _Operation.ISSUE,
        _Operation.UPDATE_ISSUE_WHOLE,
    ], sent
    assert any(line.startswith(f"  fallback read: {issue}: ") for line in said), said
    assert "fallback reads: 1" in " ".join(said)
    (entry,) = comments.read_pending(loopback.root)
    assert (entry.write, entry.state, dict(entry.intended)) == (
        "priority",
        "failed",
        {"priority": "high"},
    )
    assert entry.error is not None and "updateFieldWhole refused by the fixture" in entry.error
    assert "3 occurrences" in _row(issue).body and _row(issue).priority == "Medium"

    _GitHubFixture.refused_operation = None
    _row(issue).priority = "Low"
    status, said, sent = _next_pass(loopback)

    assert status == comments.UNANSWERED, said
    assert sent == [BATCHED_READ], "a priority a person changed was written over"
    assert f"  pending priority of {issue}: conflict: a person changed priority since" in "\n".join(
        said
    )
    assert comments.read_pending(loopback.root) == [entry]

    _row(issue).priority = "Medium"
    status, said, sent = _next_pass(loopback)

    assert status == comments.DONE, said
    assert sent == [BATCHED_READ, _Operation.UPDATE_FIELD_WHOLE], sent
    (retry,) = [one for one in loopback.requests if one.operation is _Operation.UPDATE_FIELD_WHOLE][
        -1:
    ]
    retried = retry.variables.get("input")
    assert isinstance(retried, dict) and retried["fieldId"] == "FIELD_priority", retried
    assert _row(issue).priority == "High" and comments.read_pending(loopback.root) == []


@pytest.mark.parametrize("dropped", ["after", "before"])
def test_a_withdrawal_whose_answer_was_lost_is_unknown_then_cleared_or_retried_once(
    loopback: Loopback, dropped: Literal["before", "after"]
) -> None:
    """Lost after it landed, the next read shows it withdrawn and nothing is sent again; lost
    before it reached the board, the next read shows a proposal and it is sent once more."""
    issue = _filed(loopback)
    _said(loopback, issue, ASKED)
    _GitHubFixture.dropped_operation = (_Operation.UPDATE_FIELD_WHOLE, dropped)

    held, status, said, sent = _one_pass(loopback, edit=_withdrawing)

    assert status == comments.UNANSWERED, said
    (entry,) = comments.read_pending(loopback.root)
    assert (entry.write, entry.state) == ("withdrawal", "unknown"), entry
    assert "needs attention: 1 write(s) pending" in "\n".join(said)
    assert held.snapshot is not None and comments.read_watermark(loopback.root, BOARD) == (
        held.snapshot.queried_at - comments.OVERLAP
    ), "a pending withdrawal held the watermark"

    status, said, sent = _next_pass(loopback)

    assert status == comments.DONE, said
    assert comments.read_pending(loopback.root) == []
    if dropped == "after":
        assert sent == [BATCHED_READ], "a withdrawal that had landed was sent again"
        assert f"  pending withdrawal of {issue}: the board holds it; cleared" in said
    else:
        assert sent == [
            BATCHED_READ,
            _Operation.UPDATE_FIELD_WHOLE,
            _Operation.UPDATE_ISSUE_WHOLE,
        ], sent
        assert f"  pending withdrawal of {issue}: retried status; landed" in said
    assert _row(issue).status == remote.CANCELLED.name


def test_a_re_estimate_settle_cannot_make_is_named_and_writes_nothing_and_holds_nothing(
    loopback: Loopback,
) -> None:
    """An item whose record is of a schema no reader here accepts — one a newer host wrote — is
    re-estimated by nothing: settle names why, writes nothing to it, records nothing pending,
    and the pass, whose every reply was verified, ends done with its watermark moved."""
    issue = _filed(loopback)
    _evidenced(loopback, issue)
    _said(loopback, issue, ASKED)
    gone = _ticket_gone(loopback)

    def written_by_a_newer_host() -> None:
        gone()
        _row(issue).body = re.sub(r'"schema":\d+', '"schema":99', _row(issue).body, count=1)

    held, status, said, sent = _one_pass(
        loopback, tickets.Verdict.CONFIRMS, before_settle=written_by_a_newer_host
    )

    assert status == comments.DONE, said
    assert sent == [BATCHED_READ], sent
    refused = [line for line in said if line.startswith(f"  {issue}: not re-estimated: ")]
    assert len(refused) == 1 and "record of schema 99" in refused[0], said
    assert comments.read_pending(loopback.root) == []
    assert held.snapshot is not None
    assert comments.read_watermark(loopback.root, BOARD) == (
        held.snapshot.queried_at - comments.OVERLAP
    )


def test_recovery_writes_a_pending_entrys_intended_values_to_an_issue_no_comment_quotes_and_no_more(
    loopback: Loopback,
) -> None:
    """A later pass quoting comments on another issue alone recovers the earlier entry, writing
    to its issue only what the entry intends, and to no other issue it does not quote."""
    issue = _filed(loopback)
    _evidenced(loopback, issue)
    _said(loopback, issue, ASKED)
    _GitHubFixture.refused_operation = _Operation.UPDATE_FIELD_WHOLE
    _one_pass(loopback, tickets.Verdict.CONFIRMS, before_settle=_ticket_gone(loopback))
    _GitHubFixture.refused_operation = None
    other = _filed(loopback, "a-second-cause")
    unrelated = _filed(loopback, "an-unrelated-cause")
    _said(loopback, other, ASKED)
    before_unrelated = (_row(unrelated).body, _row(unrelated).priority, _row(unrelated).status)

    held, status, said, sent = _one_pass(loopback)

    assert held.snapshot is not None
    assert {one.issue for one in held.snapshot.selection[RUN]} == {other}
    assert status == comments.DONE, said
    writes = [one for one in loopback.requests[-len(sent) :] if one.operation in WRITES]
    assert [one.operation for one in writes] == [_Operation.UPDATE_FIELD_WHOLE], sent
    assert _row(issue).priority == "High"
    assert (_row(unrelated).body, _row(unrelated).priority, _row(unrelated).status) == (
        before_unrelated
    )


def test_a_reply_the_board_does_not_hold_holds_the_watermark_and_writes_nothing(
    loopback: Loopback,
) -> None:
    issue = _filed(loopback)
    _said(loopback, issue, ASKED)
    held = _gathered(loopback, "--since", EARLY)
    assert held.plan is not None
    _answered(loopback, held.plan, tickets.Verdict.CONFIRMS)
    ((_, feedback),) = held.plan.runs
    account = tickets.responses_path(feedback)
    document = json.loads(account.read_text(encoding="utf-8"))
    document["responses"][0]["reply"] = "IC_never_posted"
    account.write_text(json.dumps(document), encoding="utf-8")

    before = loopback.mark()
    status, said = _settled(loopback, held.plan)

    assert status == comments.UNANSWERED, said
    assert loopback.since(before) == [BATCHED_READ], "an unverified run's tickets were written"
    assert "watermark: left as it was, because a launched run was not answered soundly" in said
    assert comments.read_watermark(loopback.root, BOARD) is None


def test_an_item_the_snapshots_copy_could_not_capture_is_named_quoted_from_nowhere_and_read_again(
    loopback: Loopback,
) -> None:
    """An item whose comments kept changing while the copy read them is reported `failed`.

    Its comments span pages, and a person edits one of them each time the copy reads past the
    first, so no two reads agree — and an item changed within the minute before a read carries
    no change token to read it by, so the store reads it again rather than settling on one.
    The dry run and the launch name it with the copy's error, none of its comments is quoted,
    the pass moves no watermark, and the next gathering — the edits over — selects its
    comment rather than losing it.
    """
    issue = _filed(loopback)
    captured = _filed(loopback, "a-second-cause")
    for at, body in enumerate(("The first question.\n", "A second.\n", "A third.\n")):
        _said_on_board(issue, body, f"2026-08-26T02:00:0{at}Z")
    asked = _said(loopback, captured, ASKED)
    remote.BOARD.comment_page = 1
    edits = iter(range(100))

    def edited_while_read() -> None:
        now = datetime.now(UTC).strftime(comments.MOMENT_FORMAT)
        _edited_on_board(issue, "IC_person_0", f"Edited again ({next(edits)}).\n", now)
        _GitHubFixture.before_operation[_Operation.ISSUE_DETAIL] = edited_while_read

    _GitHubFixture.before_operation[_Operation.ISSUE_DETAIL] = edited_while_read
    preview = _gathered(loopback, "--since", EARLY, "--dry-run")
    assert preview.snapshot is None
    (directory,) = comments.snapshots_directory(loopback.root).iterdir()
    snapshot = comments.read_snapshot(directory)
    (failed,) = [one for one in snapshot.left if one.comment is None]
    assert failed.issue == issue and failed.reason.startswith("not captured: "), failed
    assert issue not in {one.id for one in snapshot.items}
    assert [one.comment for one in snapshot.selection[RUN]] == [asked]
    assert not snapshot.captured_all

    _GitHubFixture.before_operation.pop(_Operation.ISSUE_DETAIL, None)
    status = comments.main(
        ["gather", "--root", str(loopback.root), "--intent", str(loopback.tmp / "i.json")]
        + ["--from", str(directory)]
    )
    assert status == comments.DONE
    said: list[str] = []
    plan = comments.claim(loopback.root, directory, fresh=False, detach=False, say=said.append)
    assert any(line.startswith(f"not captured: {issue}: ") for line in said), said
    _answered(loopback, plan)
    status, said = _settled(loopback, plan)

    assert status == comments.DONE, said
    assert "watermark: left as it was, because the snapshot did not capture every item" in said
    assert comments.read_watermark(loopback.root, BOARD) is None
    again = _gathered(loopback, "--since", EARLY)
    assert again.snapshot is not None
    assert [one.comment for one in again.snapshot.selection[RUN]] == [
        "IC_person_0",
        "IC_person_1",
        "IC_person_2",
    ]


@pytest.mark.parametrize("moved", ["refreshed-by-another-process", "own-replies", "cleared"])
def test_what_moves_the_cache_after_a_snapshot_moves_nothing_a_reader_of_the_snapshot_sees(
    loopback: Loopback, moved: str
) -> None:
    """Every read of a quoted issue after the snapshot answers the snapshot's own versions.

    Between the dry run and the launch, the board changes and a second process refreshes the
    host's cache from it; or the pass's own replies advance the cache as they land; or the
    cache is cleared. `check-gathering`, the agent's reads and `check-responses` read the
    snapshot source either way, at exactly the versions `snapshot.json` records, and send no
    request. The only board reads the log holds are the gathering's, settle's one batched read,
    and — after a clear — the one fallback read each reply's write makes, which the account
    records and settle names and counts.
    """
    issues = [_filed(loopback), _filed(loopback, "a-second-cause")]
    asked = [_said(loopback, issue, ASKED) for issue in issues]
    gathering_starts = loopback.mark()
    _gathered(loopback, "--since", EARLY, "--dry-run")
    gathering = loopback.since(gathering_starts)
    (directory,) = comments.snapshots_directory(loopback.root).iterdir()
    snapshot = comments.read_snapshot(directory)
    versions = {one.id: one.version for one in snapshot.items}
    assert set(versions) == set(issues) and all(versions.values()), versions

    match moved:
        case "refreshed-by-another-process":
            _edited_on_board(issues[0], asked[0], REWRITTEN, "2026-08-26T03:00:00Z")
            refreshed = loopback.store("task", "show", issues[0])
            assert refreshed["items"][0]["item"]["version"] != versions[issues[0]]
        case "cleared":
            cleared = subprocess.run(  # noqa: S603 - the locked plan store
                [str(ONETASKGRAPH_BIN), "cache", "clear", BOARD],
                cwd=REPO_ROOT,
                env=dict(os.environ),
                capture_output=True,
                text=True,
                check=False,
            )
            assert cleared.returncode == 0, cleared.stderr
        case _:
            # The run's own replies are what move the cache, below.
            assert moved == "own-replies", moved

    launch_starts = loopback.mark()
    status = comments.main(
        ["gather", "--root", str(loopback.root), "--intent", str(loopback.tmp / "i.json")]
        + ["--from", str(directory)]
    )
    assert status == comments.DONE
    plan = comments.claim(loopback.root, directory, fresh=False, detach=False, say=lambda _: None)
    ((_, feedback),) = plan.runs
    assert (
        tickets.main(["check-gathering", "--board", BOARD, "--feedback", str(feedback), RUN])
        == tickets.SOUND
    )
    assert loopback.since(launch_starts) == [], "the launch or check-gathering asked the board"

    answer_starts = loopback.mark()
    read = _answered(loopback, plan)
    answering = loopback.since(answer_starts)
    for issue in issues:
        (shown,) = read[issue]
        item = shown["items"][0]["item"]
        assert item["metadata"][comments.COPIED_VERSION_KEY] == versions[issue]
        assert [one["body"] for one in shown["comments"]] == [ASKED]
    expected = [_Operation.ADD_COMMENT_WHOLE] * len(issues)
    if moved == "cleared":
        expected = [_Operation.ISSUE, _Operation.ADD_COMMENT_WHOLE] * len(issues)
    assert answering == expected, answering
    if moved == "own-replies":
        # The replies advanced the cache past the snapshot; a reader of the snapshot after
        # them still reads the snapshot's versions, and the board is not asked.
        after_replies = loopback.mark()
        assert (
            tickets.main(["check-gathering", "--board", BOARD, "--feedback", str(feedback), RUN])
            == tickets.SOUND
        )
        for issue in issues:
            snapshot_id = next(one.snapshot_id for one in snapshot.items if one.id == issue)
            (detail,) = comments.shown_details([snapshot_id], snapshot.settings())
            assert (
                comments.detail_item(detail, snapshot_id)["metadata"][comments.COPIED_VERSION_KEY]
                == versions[issue]
            )
            assert len(comments.detail_comments(detail, snapshot_id)) == 1
        assert loopback.since(after_replies) == []

    settle_starts = loopback.mark()
    status, said = _settled(loopback, plan)
    settling = loopback.since(settle_starts)

    assert status == comments.DONE, said
    assert settling == [BATCHED_READ], settling
    fallbacks = [line for line in said if line.startswith("  fallback read: ")]
    assert len(fallbacks) == (len(issues) if moved == "cleared" else 0), said
    assert f"fallback reads: {len(fallbacks)}" in " ".join(said)
    # The gathering's one search page and one batched copy of both items, exactly; the answering
    # and settling requests were each held to their exact sequence above.
    assert gathering == [_Operation.SEARCH, _Operation.ISSUE_COPY_DETAILS], gathering


def _carried(loopback: Loopback, **fields: object) -> comments.PendingEntry:
    """A pending write an earlier pass left, as `pending.json` records one."""
    entry = comments.PendingEntry(
        snapshot=str(loopback.tmp / "a-snapshot-gone-since"),
        run=RUN,
        item=f"{BOARD}:I_never_held",
        write="priority",
        state="failed",
        snapshot_version=None,
        seen_version="a-version-long-gone",
        intended={"priority": "high"},
        parts=(),
        error="refused",
        recorded_at="2026-08-26T00:00:00Z",
    )
    return dataclasses.replace(entry, **fields)


def test_a_carried_ticket_copy_of_an_item_its_run_does_not_own_is_kept_and_never_sent(
    loopback: Loopback,
) -> None:
    """A copy is of the run's own ticket for the item's root cause, so an entry naming another
    run's item names no ticket to copy: it is surfaced and kept, and nothing is written."""
    issue = _filed(loopback)
    shown = loopback.store("task", "show", issue)["items"]
    assert isinstance(shown, list)
    version = shown[0]["item"]["version"]
    comments.write_pending(
        loopback.root,
        [
            _carried(
                loopback,
                run="another-run",
                item=issue,
                write="ticket-copy",
                seen_version=version,
                intended={"repositories": ["github.com/nickderobertis/somewhere-else"]},
            )
        ],
    )

    status, said, sent = _next_pass(loopback)

    assert status == comments.UNANSWERED, said
    assert sent == [BATCHED_READ], sent
    assert (
        f"  pending ticket-copy of {issue}: {issue} carries no root cause of a ticket of run "
        "another-run's to copy, so nothing was written; kept"
    ) in said, said
    (kept,) = comments.read_pending(loopback.root)
    assert (kept.run, kept.write) == ("another-run", "ticket-copy")


def test_carried_writes_the_board_or_their_snapshot_cannot_answer_for_are_kept_unwritten(
    loopback: Loopback,
) -> None:
    """An entry is never dropped for want of a read: an item the board no longer answers for,
    or one whose snapshot is gone or holds it no more, is kept and surfaced rather than written
    over a person's change nothing can rule out; a retry the board refuses again stays pending."""
    issue = _filed(loopback)
    uncommented = _filed(loopback, "a-second-cause")
    _said(loopback, issue, ASKED)
    held = _gathered(loopback, "--since", EARLY, "--dry-run")
    assert held.snapshot is None
    (directory,) = comments.snapshots_directory(loopback.root).iterdir()
    assert uncommented not in {one.id for one in comments.read_snapshot(directory).items}
    missing = _carried(loopback)
    gone = _carried(loopback, item=issue)
    elsewhere = _carried(loopback, item=uncommented, snapshot=str(directory), write="ticket-copy")
    elsewhere = dataclasses.replace(
        elsewhere, write="ticket-copy", intended={"title": "A title nobody wrote"}
    )
    comments.write_pending(loopback.root, [missing, gone, elsewhere])
    again = _gathered(loopback, "--since", EARLY)
    assert again.plan is not None

    before = loopback.mark()
    status, said = _settled(loopback, again.plan)

    assert status == comments.UNANSWERED, said
    assert loopback.since(before) == [BATCHED_READ], "a write was sent with nothing to rule on it"
    assert any(
        line.startswith(f"the batched read could not answer for {missing.item}") for line in said
    )
    assert f"  pending priority of {missing.item}: the board did not answer for it; kept" in said
    assert sum(" conflict: a person changed " in line for line in said) == 2, said
    assert comments.read_pending(loopback.root) == [missing, gone, elsewhere]

    comments.write_pending(loopback.root, [])
    shown = loopback.store("task", "show", issue)["items"][0]["item"]
    retried = _carried(loopback, item=issue, seen_version=shown["version"])
    comments.write_pending(loopback.root, [retried])
    _GitHubFixture.refused_operation = _Operation.UPDATE_FIELD_WHOLE
    status, said = _settled(loopback, again.plan)

    assert status == comments.UNANSWERED, said
    (kept,) = comments.read_pending(loopback.root)
    assert (kept.state, kept.intended) == ("failed", {"priority": "high"}), kept
    assert kept.error is not None and "refused by the fixture" in kept.error
    assert f"  pending priority of {issue}: retry failed: " in "\n".join(said)


def test_a_re_estimate_that_changes_nothing_writes_nothing(loopback: Loopback) -> None:
    """With no ticket of the run's here, a reply that confirms nothing leaves the item as it was."""
    issue = _filed(loopback)
    _said(loopback, issue, ASKED)

    _, status, said, sent = _one_pass(loopback, before_settle=_ticket_gone(loopback))

    assert status == comments.DONE, said
    assert sent == [BATCHED_READ], sent


def test_a_ticket_whose_repositories_differ_is_copied_whole_and_a_refused_copy_retried_as_one(
    loopback: Loopback,
) -> None:
    """The one write `task update` cannot make goes as the ticket's copy, named so in pending.

    The item reached the board before tickets listed `repositories`, so it holds none; the
    agent brings the ticket forward with its one repository. Settle copies it whole — the first
    copy refused at its issue write and recorded `ticket-copy` — and the next pass copies it
    again, after which the item lists the repository and nothing is pending.
    """
    issue = _filed(loopback)
    path = tickets.ticket_path(loopback.root, RUN, remote.PROPOSED_CAUSE)
    brought_forward = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(r"(?m)^repositories: .*\n", "", brought_forward, count=1), encoding="utf-8"
    )
    loopback.store("task", "copy", tickets.qualified_id(RUN, remote.PROPOSED_CAUSE), "--to", BOARD)
    os.utime(path, (0, 0))
    repository = str(remote._hosted(remote.SIBLING_REPOSITORY))
    assert _repositories(loopback, issue) == []
    _said(loopback, issue, ASKED)

    def bring_forward(ticket: Path) -> None:
        ticket.write_text(brought_forward, encoding="utf-8")

    _GitHubFixture.refused_operation = _Operation.UPDATE_ISSUE_WHOLE
    before = loopback.mark()
    _, status, said, sent = _one_pass(loopback, edit=bring_forward)

    assert status == comments.UNANSWERED, said
    # The batched read, the copy's issue write the board refused, and the store's one read of
    # the item after that refusal, which its answer omits and the recipe names as a fallback.
    assert sent == [BATCHED_READ, _Operation.UPDATE_ISSUE_WHOLE, _Operation.ISSUE], sent
    (refused,) = [
        one for one in loopback.requests[before:] if one.operation is _Operation.UPDATE_ISSUE_WHOLE
    ]
    # Under `--expect` the copy sends only the part that differs: the body, whose record
    # carries the repositories, and no title or project field.
    assert set(_input(refused)) == {"id", "body"}, refused.variables
    assert f"  fallback read: {issue}: the store read the item once after the board refused " in (
        "\n".join(said)
    ), said
    assert "board writes: 1; fallback reads: 1" in said, said
    (entry,) = comments.read_pending(loopback.root)
    assert (entry.write, entry.item) == ("ticket-copy", issue), entry
    assert entry.intended["repositories"] == [repository], entry
    assert f"  pending: ticket-copy of {issue} ({entry.state}): " in "\n".join(said), said
    assert _repositories(loopback, issue) == []

    _GitHubFixture.refused_operation = None
    before = loopback.mark()
    status, said, sent = _next_pass(loopback)

    assert status == comments.DONE, said
    assert sent == [BATCHED_READ, _Operation.UPDATE_ISSUE_WHOLE], sent
    (retried,) = [one for one in loopback.requests[before:] if one.operation in WRITES]
    assert set(_input(retried)) == {"id", "body"}, retried.variables
    assert "board writes: 1; fallback reads: 0" in said, said
    assert f"  pending ticket-copy of {issue}: retried " in "\n".join(said), said
    assert comments.read_pending(loopback.root) == []
    assert _repositories(loopback, issue) == [repository]


def _input(request: remote._GraphQLRequest) -> dict[str, object]:
    """The input a mutation sent the board."""
    sent = request.variables.get("input")
    assert isinstance(sent, dict), request.variables
    return sent


def _repositories(loopback: Loopback, issue: str) -> object:
    """The repositories the board's item of ``issue`` lists, as the store reads it."""
    items = loopback.store("task", "show", issue)["items"]
    assert isinstance(items, list), items
    return items[0]["item"].get("repositories")
