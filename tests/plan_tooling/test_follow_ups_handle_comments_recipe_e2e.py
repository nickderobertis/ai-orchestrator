"""`just follow-ups-handle-comments` over people's board comments, driven end to end.

Everything below the recipe is real: `just follow-ups-handle-comments` and its script,
`orchestrator/follow_up_comments.py` reading the board, `just follow-ups --feedback` and the
launch it makes under the shipped `graphs/follow-up.yaml`, the installed `onepipeline`
driver, and the installed `onetaskgraph` every ticket, board item and comment is written and
read through. The bench is `tests/plan_tooling/test_follow_ups_recipe_e2e.py`'s, imported
rather than restated, so both journeys launch on the same throwaway host.

**The board is a second local store and never the live `followups` board**, added through
the store's own environment layer and named with `--to`, as that journey and
`tests/plan_tooling/test_copy_plan_recipe_e2e.py` stand one in. **`tests/e2e/fake_codex.py`
stands in for the paid model alone**, running the commands the answering agent would choose
through the real programs: copying the run's ticket again with the status `board-status`
prints, which is how an agent answers a comment on its own issue, and posting one reply to
each comment its task's feedback quotes, read out of the task the turn was given.

One module fixture seeds the board, drives the refusal on a run with nothing new, drives the
re-dispatch on the run with comments — during which a person comments again, before the
replies are posted — and gathers again until nothing is left, then once more after a later
comment: readings of one board's life.

**The board is longer than one page as the recipe lists it.** An earlier run's fifty
tickets sit ahead of the main run's own issue in listing order, so that issue is past the
store's default page — the shape in which `just follow-ups-handle-comments` once read a
board's first fifty items as the whole board and answered "no new feedback" for a run whose
every issue sat on the second page. The fixture records that first page as the installed
store reports it, with no `--page`, so the premise is read rather than assumed.

**The journeys that stand something in front of the store do so where the package reads
it.** Every plan-store read here runs the `onetaskgraph` beside its interpreter and never
one on `PATH`, so a store recording what it was handed, or answering one field of one
listing differently, is installed as the plan store of a mirror checkout's own toolchain
(:func:`_mirrored_toolchain`) and delegates every command to the pinned store.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from published_tools import ONETASKGRAPH_BIN
from test_follow_ups_recipe_e2e import (
    BOARD,
    HOST,
    NODE,
    OK,
    REFUSED,
    SUFFIX,
    Bench,
    _bench,
    _category,
    _comments,
    _decided_and_copied,
    _from_checkout,
    _item,
    _launched_node,
    _moved,
    _prompts,
    _ran,
    _run,
    _script,
    _store,
    _ticket,
)
from waits import timeout as e2e_timeout

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator.plan_store import QualifiedTaskId
from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The root causes seeded on the board: the main run's own issue, and an earlier run's
#: issue the main run left its one marked comment on.
OWN_CAUSE = "listing-cursor-skips-last-page"
SHARED_CAUSE = "sweep-trailer-omits-a-family"
QUIET_CAUSE = "export-drops-a-column"

#: How many tickets a filler run files ahead of the main run's issue in listing order: one
#: default page of the installed store, so the main run's own issue is on the page after
#: it. The store's page size is left unnamed — the point is the page nobody sized.
FILLED = 50

#: The people commenting, as the board records their authors.
REVIEWER = "a-reviewer"
MAINTAINER = "a-maintainer"

#: What each seeded comment says, so the feedback can be read for exactly which reached it.
ANSWERED = "Asked before the run last responded, and already answered.\n"
ON_OWN = "Page 9 still never renders — the `cursor` example needs ```page=9```.\n"
ON_SHARED = "Does this also hit the nightly sweep?\n"
LATER = "One more thing, after the run answered: the weekly sweep too.\n"
DURING = "Written while the dispatch worked: the monthly export too.\n"

#: What every reply the answering agent posts says it did.
RESPONSE = "Copied this run's ticket again with that in its examples."

#: The answering agent's own program: read the feedback its task quotes, and post one reply
#: to each comment there, on the issue that holds it, naming the comment's id. Run in the turn
#: with the task's prompt log, from the launching checkout, through the installed store.
REPLY_TO_FEEDBACK = """\
import json, subprocess, sys, tempfile
from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets

log, run, store = sys.argv[1:4]
with open(log, encoding="utf-8") as stream:
    task = json.loads(stream.read().splitlines()[-1])["prompt"]
feedback = task.split("## Feedback on the previous follow-up run", 1)[1]
for section in feedback.split("### Comment ")[1:]:
    issue = section.split("`", 2)[1]
    fields = dict(
        line[2:].split(": ", 1) for line in section.splitlines() if line.startswith("- ")
    )
    shown = json.loads(
        subprocess.run([store, "task", "show", issue, "--json"], check=True,
                       capture_output=True, text=True).stdout
    )
    cause = shown["items"][0]["item"]["metadata"][tickets.KEY]["root_cause"]
    author = fields["Author"]
    reply = tickets.render_reply(
        run, cause, answers=fields["Comment id"], url=fields["URL"],
        author=None if author == comments.UNKNOWN_AUTHOR else author,
        response=@RESPONSE@,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as body:
        body.write(reply)
    subprocess.run([store, "task", "comment", "add", issue, "--body-file", body.name], check=True)
""".replace("@RESPONSE@", repr(RESPONSE))
QUIET_OLD = "Asked before this run's last copy.\n"

#: The recipe's own line naming the feedback file it wrote.
WROTE = re.compile(r"follow-ups-handle-comments: wrote run \S+'s new board comments to (\S+);")


class Handled(NamedTuple):
    """Every phase of this module's journey, read back before anything is torn down."""

    bench: Bench
    main: tickets.RunId
    quiet: tickets.RunId
    own_issue: QualifiedTaskId
    shared_issue: QualifiedTaskId
    quiet_issue: QualifiedTaskId
    filled: list[QualifiedTaskId]
    #: The board's first page as the installed store lists it with no ``--page``.
    first_page: dict[str, object]
    statuses_before: dict[str, object]
    refused: subprocess.CompletedProcess[str]
    statuses_after_refusal: dict[str, object]
    handled: subprocess.CompletedProcess[str]
    watched: subprocess.CompletedProcess[str]
    follow_up_run: str
    launched: list[str]
    prompts: list[str]
    statuses_after: dict[str, object]
    comments_after_first: dict[str, list[dict[str, object]]]
    first_copy_mtime: float
    again: subprocess.CompletedProcess[str]
    again_prompts: list[str]
    comments_after_again: dict[str, list[dict[str, object]]]
    once_more: subprocess.CompletedProcess[str]
    ledger_refused: subprocess.CompletedProcess[str]
    later: subprocess.CompletedProcess[str]
    later_prompts: list[str]
    comments_after_later: dict[str, list[dict[str, object]]]
    statuses_at_end: dict[str, object]


def _commented(bench: Bench, issue: str, body: str, author: str | None) -> None:
    """A comment written through the store's own verb, as a person or a run writes one."""
    path = bench.tmp / f"comment-{time.monotonic_ns()}.md"
    path.write_text(body, encoding="utf-8")
    _store(
        bench,
        "task",
        "comment",
        "add",
        issue,
        "--body-file",
        str(path),
        *(["--author", author] if author else []),
    )


def _filed(bench: Bench, run: str, cause: str) -> QualifiedTaskId:
    """``run``'s verified ticket for ``cause``, written and copied onto the board; its item."""
    path = tickets.ticket_path(bench.drafts_root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    ticket = _ticket(
        run,
        cause,
        (f"drafts:{run}/drafts/a-consumed-draft",),
        f"some-service: {cause.replace('-', ' ')}",
        "Verified",
        HOST,
    )
    path.write_text(tickets.render(ticket), encoding="utf-8")
    copied = _run(
        [str(ONETASKGRAPH_BIN), "task", "copy", tickets.qualified_id(run, cause), "--to", BOARD],
        bench,
    )
    assert copied.returncode == 0, copied.stdout + copied.stderr
    return QualifiedTaskId(f"{BOARD}:{run}/tickets/{cause}")


def _first_page(bench: Bench) -> dict[str, object]:
    """The board's first page exactly as an unpaged listing sees it."""
    return _store(bench, "task", "list", "--source", BOARD)


def _statuses(bench: Bench) -> dict[str, object]:
    """The category every board item is held at, read page by page to the last."""
    found: dict[str, object] = {}
    page: str | None = None
    while True:
        listed = _store(
            bench, "task", "list", "--source", BOARD, *(["--page", page] if page else [])
        )
        items = listed["items"]
        assert isinstance(items, list), items
        found.update({str(one["id"]): _category(one["item"]) for one in items})
        cursor = listed["next"]
        if cursor is None:
            return found
        assert isinstance(cursor, str), listed
        page = cursor


def _replying(bench: Bench, python: str, log: Path, run: str) -> list[str]:
    """The answering agent's command posting one reply to each comment its task quotes."""
    helper = bench.tmp / "reply-to-feedback.py"
    helper.write_text(REPLY_TO_FEEDBACK, encoding="utf-8")
    return [
        "bash",
        "-c",
        'cd "$1" && exec "$2" "$3" "$4" "$5" "$6"',
        "_",
        str(REPO_ROOT),
        python,
        str(helper),
        str(log),
        run,
        str(ONETASKGRAPH_BIN),
    ]


def _board_comments(bench: Bench, *issues: str) -> dict[str, list[dict[str, object]]]:
    """Every comment each issue holds, as the store lists them."""
    return {issue: _comments(bench, issue) for issue in issues}


def _next_second() -> None:
    """Wait until the store's whole-second clock has moved past every time written so far."""
    time.sleep(1.1)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `tests/plan_tooling` is
# already the Nx project edge this repository keeps for journeys that launch the installed
# engine, keyed on `planToolingWorkspace`, which covers every file these launches read. The
# fixture is module-scoped and spends one launch whose turn is the provider's stand-in.
@pytest.fixture(scope="module")
def handled(tmp_path_factory: pytest.TempPathFactory) -> Handled:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("follow-ups-handle-comments")
    bench = _bench(tmp)
    bench.environment["FAKE_CODEX_RUN_ON_MARKER"] = str(tmp / "commands.json")
    bench.environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(tmp / "commands-ran.jsonl")
    pid = os.getpid()
    main, earlier, filler, quiet = (
        tickets.RunId(f"hc-{role}-{pid}") for role in ("main", "earlier", "filler", "quiet")
    )
    python = str(REPO_ROOT / ".venv" / "bin" / "python3")
    started: list[str] = []
    try:
        # The board as four earlier follow-up runs left it, one of them a whole page of
        # tickets that list ahead of the main run's own issue.
        own_issue = _filed(bench, main, OWN_CAUSE)
        shared_issue = _filed(bench, earlier, SHARED_CAUSE)
        quiet_issue = _filed(bench, quiet, QUIET_CAUSE)
        filled = [_filed(bench, filler, f"filler-{index:02d}") for index in range(FILLED)]
        _next_second()
        _commented(bench, own_issue, ANSWERED, REVIEWER)
        _commented(bench, quiet_issue, QUIET_OLD, REVIEWER)
        _next_second()
        # The quiet run's last response: its ticket copied again after the comment on it.
        _filed(bench, quiet, QUIET_CAUSE)
        # The main run's last response: its one marked comment on the earlier run's issue.
        marked = tickets.render_comment(main, SHARED_CAUSE, "This run hit it too.")
        _commented(bench, shared_issue, marked, None)
        _next_second()
        # What people wrote after it, beside a comment the earlier run owns.
        _commented(bench, own_issue, ON_OWN, REVIEWER)
        _commented(bench, shared_issue, ON_SHARED, MAINTAINER)
        owned = tickets.render_comment(earlier, OWN_CAUSE, "The earlier run's evidence.")
        _commented(bench, own_issue, owned, None)
        # A person accepts the main run's ticket after the run last touched it.
        _moved(bench, own_issue, tickets.Status.ACCEPTED.value)
        first_page = _first_page(bench)
        statuses_before = _statuses(bench)

        # A run whose every comment predates its last response.
        refused = _run(["just", "follow-ups-handle-comments", quiet, "--to", BOARD], bench)
        statuses_after_refusal = _statuses(bench)

        # The main run, answered by an agent that — after the feedback is written, and while
        # its dispatch works — sees a person comment again, then copies its ticket again from
        # the board's status and replies to each comment its feedback quotes.
        own_ticket = tickets.ticket_path(bench.drafts_root, main, OWN_CAUSE)
        log = tmp / "prompts.jsonl"
        during = bench.tmp / "during.md"
        during.write_text(DURING, encoding="utf-8")
        _script(
            bench,
            main,
            [
                _from_checkout(
                    str(ONETASKGRAPH_BIN),
                    "task",
                    "comment",
                    "add",
                    own_issue,
                    "--body-file",
                    str(during),
                    "--author",
                    REVIEWER,
                ),
                # The store's clock is whole seconds: the copy and replies come a second later.
                ["sleep", "1.1"],
                _decided_and_copied(
                    python, str(ONETASKGRAPH_BIN), own_ticket, tickets.qualified_id(main, OWN_CAUSE)
                ),
                _replying(bench, python, log, main),
            ],
        )
        environment = bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(log)}
        handled_run = _run(
            ["just", "follow-ups-handle-comments", main, "--detach", "--to", BOARD],
            bench,
            environment=environment,
        )
        follow_up_run = f"{main}{SUFFIX}"
        started.append(follow_up_run)
        watched = _run(["just", "watch", follow_up_run, "--until", "settled"], bench)
        launched = sorted(path.name for path in bench.runs.glob(f"{main}{SUFFIX}*"))
        statuses_after = _statuses(bench)
        comments_after_first = _board_comments(bench, own_issue, shared_issue)
        first_copy_mtime = own_ticket.stat().st_mtime

        # Gathered again right after it settles: the comment written during the dispatch,
        # answered by a re-dispatch that only replies.
        again_log = tmp / "again-prompts.jsonl"
        _script(bench, main, [_replying(bench, python, again_log, main)])
        again = _run(
            ["just", "follow-ups-handle-comments", main, f"--to={BOARD}"],
            bench,
            environment=bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(again_log)},
        )
        started.append(f"{follow_up_run}-2")
        comments_after_again = _board_comments(bench, own_issue, shared_issue)

        # Once more, with every comment answered: nothing new, and nothing launched.
        once_more = _run(["just", "follow-ups-handle-comments", main, "--to", BOARD], bench)

        # A person writes again, and this time the manager stays attached to the re-dispatch.
        _next_second()
        _commented(bench, shared_issue, LATER, MAINTAINER)
        # First with a run ledger the feedback path cannot search, which it refuses itself.
        unsearchable = tmp / "not-a-ledger"
        unsearchable.write_text("", encoding="utf-8")
        ledger_refused = _run(
            ["just", "follow-ups-handle-comments", main, "--to", BOARD],
            bench,
            environment=bench.environment | {"ONEPIPELINE_RUNS_DIR": str(unsearchable)},
        )
        launched_after_ledger_refusal = sorted(
            path.name for path in bench.runs.glob(f"{main}{SUFFIX}*")
        )
        assert launched_after_ledger_refusal == [follow_up_run, f"{follow_up_run}-2"], (
            launched_after_ledger_refusal
        )
        later_log = tmp / "later-prompts.jsonl"
        _script(bench, main, [_replying(bench, python, later_log, main)])
        later = _run(
            ["just", "follow-ups-handle-comments", main, "--to", BOARD],
            bench,
            environment=bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(later_log)},
        )
        started.append(f"{follow_up_run}-3")
        return Handled(
            bench=bench,
            main=main,
            quiet=quiet,
            own_issue=own_issue,
            shared_issue=shared_issue,
            quiet_issue=quiet_issue,
            filled=filled,
            first_page=first_page,
            statuses_before=statuses_before,
            refused=refused,
            statuses_after_refusal=statuses_after_refusal,
            handled=handled_run,
            watched=watched,
            follow_up_run=follow_up_run,
            launched=launched,
            prompts=_prompts(log),
            statuses_after=statuses_after,
            comments_after_first=comments_after_first,
            first_copy_mtime=first_copy_mtime,
            again=again,
            again_prompts=_prompts(again_log),
            comments_after_again=comments_after_again,
            once_more=once_more,
            ledger_refused=ledger_refused,
            later=later,
            later_prompts=_prompts(later_log),
            comments_after_later=_board_comments(bench, own_issue, shared_issue),
            statuses_at_end=_statuses(bench),
        )
    finally:
        for run in started:
            if (bench.runs / run).exists():
                _run(["just", "stop", run], bench)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _feedback(handled: Handled) -> tuple[Path, str]:
    assert handled.handled.returncode == OK, (
        handled.handled.stdout + handled.handled.stderr + _ran(handled.bench)
    )
    assert handled.watched.returncode == OK, handled.watched.stdout + handled.watched.stderr
    named = WROTE.search(handled.handled.stderr)
    assert named is not None, handled.handled.stderr
    path = Path(named[1])
    return path, path.read_text(encoding="utf-8")


def _comment_id(listed: list[dict[str, object]], body: str) -> str:
    (identifier,) = [str(one["id"]) for one in listed if one["body"] == body]
    return identifier


def _replies(
    listed: list[dict[str, object]], run: str
) -> dict[str, tuple[tickets.CommentOwner, dict[str, object]]]:
    """Every reply ``run`` posted among ``listed``, by the id of the comment it answers."""
    found: dict[str, tuple[tickets.CommentOwner, dict[str, object]]] = {}
    for one in listed:
        owner = tickets.comment_owner(str(one["body"]))
        if owner is not None and owner.run == run and owner.kind is tickets.CommentKind.REPLY:
            assert owner.answers is not None and owner.answers not in found, listed
            found[owner.answers] = (owner, one)
    return found


def _comment_url(handled: Handled, issue: str, body: str) -> str:
    location = _item(handled.bench, issue)["location"]
    assert isinstance(location, dict), location
    (identifier,) = [one["id"] for one in _comments(handled.bench, issue) if one["body"] == body]
    return f"{Path(str(location['path'])).as_uri()}#comment-{identifier}"


def test_a_run_with_no_new_feedback_is_refused_naming_it_and_launches_nothing(
    handled: Handled,
) -> None:
    refused, bench = handled.refused, handled.bench

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert f"run {handled.quiet} has no new feedback" in refused.stderr
    assert (
        "none is a person's that no reply of this run answers and that changed after "
        in refused.stderr
    )
    assert "nothing was launched. Run it again once somebody comments" in refused.stderr
    assert not (bench.runs / f"{handled.quiet}{SUFFIX}").exists()
    assert not (bench.plans / "projects" / f"{handled.quiet}{SUFFIX}.md").exists()
    assert not (bench.drafts_root / comments.FEEDBACK_DIRECTORY / handled.quiet).exists()


def test_the_feedback_file_carries_exactly_the_persons_comments_after_the_runs_last_response(
    handled: Handled,
) -> None:
    path, feedback = _feedback(handled)

    assert path.parent == handled.bench.drafts_root / comments.FEEDBACK_DIRECTORY / handled.main
    assert feedback.count("### Comment ") == 2, feedback
    for issue, body, author in (
        (handled.own_issue, ON_OWN, REVIEWER),
        (handled.shared_issue, ON_SHARED, MAINTAINER),
    ):
        url = _comment_url(handled, issue, body)
        identifier = _comment_id(handled.comments_after_first[issue], body)
        assert f"- Comment id: {identifier}\n- URL: {url}\n- Author: {author}\n" in feedback, (
            feedback
        )
        assert body.rstrip() in feedback
    assert feedback.splitlines()[0].startswith("<!-- orchestrator:follow-up-feedback boundary=")
    for absent in (ANSWERED, "This run hit it too.", "The earlier run's evidence.", QUIET_OLD):
        assert absent.rstrip() not in feedback, feedback


def test_a_comment_on_an_issue_past_the_boards_first_page_is_gathered(
    handled: Handled,
) -> None:
    """The board is longer than one page as the recipe lists it; the own issue is past it."""
    first = handled.first_page
    items = first["items"]
    assert isinstance(items, list), first
    listed = [str(one["id"]) for one in items]

    assert first["errors"] == []
    assert first["next"] is not None, "the board fits one page, so nothing here is past it"
    assert handled.own_issue not in listed, listed
    assert handled.shared_issue in listed, listed
    assert set(handled.statuses_before) > set(listed)
    assert set(handled.statuses_before) >= {*handled.filled, handled.own_issue}

    _, feedback = _feedback(handled)
    assert f"on `{handled.own_issue}`, this run's issue" in feedback, feedback
    assert ON_OWN.rstrip() in feedback


def test_the_recipe_re_dispatches_once_through_the_feedback_path_with_the_file_verbatim(
    handled: Handled,
) -> None:
    _, feedback = _feedback(handled)
    bench = handled.bench

    # `--detach` reached the feedback path, which returns at the launch record.
    assert handled.handled.stdout.splitlines() == [
        f"follow-up run: {handled.follow_up_run}",
        f"watch it with: just watch {handled.follow_up_run}",
    ]
    assert handled.launched == [handled.follow_up_run]
    node = _launched_node(bench, handled.follow_up_run)
    assert node["id"] == NODE
    task = str(node["task"])
    assert "## Feedback on the previous follow-up run" in task
    assert feedback.rstrip() in task
    (prompt,) = handled.prompts
    assert feedback.rstrip() in prompt
    results = _run(["just", "results", handled.follow_up_run], bench)
    assert re.search(rf"^\s+{NODE}\s+done$", results.stdout, re.MULTILINE), results.stdout


def test_no_board_items_status_changes_including_one_a_person_moved_after_the_run(
    handled: Handled,
) -> None:
    _feedback(handled)
    ran = _ran(handled.bench)

    assert handled.statuses_before[handled.own_issue] == tickets.Status.ACCEPTED.value
    assert handled.statuses_after_refusal == handled.statuses_before
    assert handled.statuses_after == handled.statuses_before, ran
    assert handled.statuses_at_end == handled.statuses_before, ran
    assert f"task copy {tickets.qualified_id(handled.main, OWN_CAUSE)}" in ran, (
        "the answering agent never copied the ticket, so the status was never put to the test"
    )


def test_each_quoted_comment_gets_one_reply_on_its_issue_naming_it_and_its_author(
    handled: Handled,
) -> None:
    """The re-dispatch replied to each comment its feedback quoted, and to nothing else."""
    _feedback(handled)
    ran = _ran(handled.bench)
    after = handled.comments_after_first

    for issue, body, author, cause in (
        (handled.own_issue, ON_OWN, REVIEWER, OWN_CAUSE),
        (handled.shared_issue, ON_SHARED, MAINTAINER, SHARED_CAUSE),
    ):
        identifier = _comment_id(after[issue], body)
        replies = _replies(after[issue], handled.main)
        assert set(replies) == {identifier}, (after, ran)
        owner, reply = replies[identifier]
        assert owner == tickets.CommentOwner(
            handled.main, tickets.RootCause(cause), tickets.CommentKind.REPLY, identifier
        )
        url = _comment_url(handled, issue, body)
        text = str(reply["body"])
        assert text.splitlines()[0] == (
            f"Reply from follow-up run `{handled.main}` to @{author}'s comment: {url}"
        )
        assert RESPONSE in text
    (during,) = [one for one in after[handled.own_issue] if one["body"] == DURING]
    assert during["author"] == REVIEWER
    assert str(during["id"]) not in _replies(after[handled.own_issue], handled.main), (
        "a comment written during the dispatch was replied to without being quoted"
    )
    evidence = [
        one
        for one in after[handled.shared_issue]
        if (owner := tickets.comment_owner(str(one["body"]))) is not None
        and owner.run == handled.main
        and owner.kind is tickets.CommentKind.EVIDENCE
    ]
    assert [one["body"] for one in evidence] == [
        tickets.render_comment(handled.main, SHARED_CAUSE, "This run hit it too.")
    ], "the run's one evidence comment is not still its only one"
    assert f"task copy {tickets.qualified_id(handled.main, OWN_CAUSE)}" in ran


def test_gathering_again_quotes_only_the_comment_written_during_the_dispatch(
    handled: Handled,
) -> None:
    """Older than the dispatch's copy and replies, and still gathered: no reply names it."""
    _feedback(handled)
    again, bench = handled.again, handled.bench
    own = handled.comments_after_first[handled.own_issue]
    (during,) = [one for one in own if one["body"] == DURING]
    moment = comments.moment(during["updated_at"], "the comment written during the dispatch")
    replied = [
        comments.moment(one["updated_at"], "a reply")
        for listed in handled.comments_after_first.values()
        for _, one in _replies(listed, handled.main).values()
    ]
    assert replied and all(moment < at for at in replied), (during, replied)
    assert moment.timestamp() < handled.first_copy_mtime

    assert again.returncode == OK, again.stdout + again.stderr + _ran(bench)
    named = WROTE.search(again.stderr)
    assert named is not None, again.stderr
    feedback = Path(named[1]).read_text(encoding="utf-8")
    assert feedback.count("### Comment ") == 1, feedback
    assert f"- Comment id: {during['id']}\n" in feedback
    assert DURING.rstrip() in feedback
    for absent in (ANSWERED, ON_OWN, ON_SHARED, QUIET_OLD):
        assert absent.rstrip() not in feedback, feedback
    second = f"{handled.follow_up_run}-2"
    assert f"follow-ups: launching run {second}" in again.stderr
    (prompt,) = handled.again_prompts
    assert feedback.rstrip() in prompt
    results = _run(["just", "results", second], bench)
    assert re.search(rf"^\s+{NODE}\s+done$", results.stdout, re.MULTILINE), results.stdout

    for issue in (handled.own_issue, handled.shared_issue):
        before = _replies(handled.comments_after_first[issue], handled.main)
        now = _replies(handled.comments_after_again[issue], handled.main)
        assert {key: now[key] for key in before} == before, "an earlier reply changed"
    replies = _replies(handled.comments_after_again[handled.own_issue], handled.main)
    owner, _ = replies[str(during["id"])]
    assert owner.root_cause == OWN_CAUSE
    assert len(replies) == 2, replies


def test_once_every_comment_is_answered_gathering_finds_nothing_and_launches_nothing(
    handled: Handled,
) -> None:
    _feedback(handled)
    once_more = handled.once_more

    assert once_more.returncode == REFUSED, once_more.stdout + once_more.stderr
    assert f"run {handled.main} has no new feedback" in once_more.stderr
    assert "nothing was launched" in once_more.stderr
    assert "follow-ups: launching run" not in once_more.stderr


def test_a_later_comment_is_re_dispatched_again_attached_carrying_only_itself(
    handled: Handled,
) -> None:
    later = handled.later
    ran = _ran(handled.bench)

    assert later.returncode == OK, later.stdout + later.stderr + ran
    named = WROTE.search(later.stderr)
    assert named is not None, later.stderr
    feedback = Path(named[1]).read_text(encoding="utf-8")
    assert feedback.count("### Comment ") == 1, feedback
    assert LATER.rstrip() in feedback
    for absent in (ON_SHARED, ON_OWN, DURING):
        assert absent.rstrip() not in feedback, feedback
    third = f"{handled.follow_up_run}-3"
    assert f"follow-ups: launching run {third}" in later.stderr
    (prompt,) = handled.later_prompts
    assert feedback.rstrip() in prompt
    results = _run(["just", "results", third], handled.bench)
    assert re.search(rf"^\s+{NODE}\s+done$", results.stdout, re.MULTILINE), results.stdout
    shared = handled.comments_after_later[handled.shared_issue]
    identifier = _comment_id(shared, LATER)
    replies = _replies(shared, handled.main)
    assert set(replies) == {identifier, _comment_id(shared, ON_SHARED)}, replies
    owner, reply = replies[identifier]
    assert owner.root_cause == SHARED_CAUSE
    assert (
        str(reply["body"])
        .splitlines()[0]
        .startswith(f"Reply from follow-up run `{handled.main}` to @{MAINTAINER}'s comment: ")
    )


def test_a_refusal_of_the_feedback_path_is_the_recipes_and_launches_nothing(
    handled: Handled,
) -> None:
    refused = handled.ledger_refused

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert "follow-ups-handle-comments: wrote run" in refused.stderr
    assert "follow-ups: the run ledger at " in refused.stderr
    assert "cannot be searched" in refused.stderr
    assert "follow-ups: launching run" not in refused.stderr


def test_a_drafts_root_that_cannot_be_resolved_is_refused_before_the_board_is_read(
    handled: Handled,
) -> None:
    not_a_directory = handled.bench.tmp / "drafts-root-is-a-file"
    not_a_directory.write_text("", encoding="utf-8")
    environment = handled.bench.environment | {
        follow_up_variables.root_name(): str(not_a_directory)
    }

    refused = _run(
        ["just", "follow-ups-handle-comments", handled.main, "--to", BOARD],
        handled.bench,
        environment=environment,
    )

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert "follow-ups-handle-comments: the 'drafts' plan source could not be resolved" in (
        refused.stderr
    )
    assert "wrote run" not in refused.stderr


def test_a_board_the_store_cannot_read_is_refused_naming_what_to_check(handled: Handled) -> None:
    refused = _run(
        ["just", "follow-ups-handle-comments", handled.main, "--to", "no-such-board"],
        handled.bench,
    )

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert "follow-up-comments: refused: " in refused.stderr
    assert "Check that --board names a source" in refused.stderr
    assert "wrote run" not in refused.stderr
    assert not (handled.bench.runs / f"{handled.follow_up_run}-4").exists()


def test_naming_no_board_reads_the_followups_board_and_launches_nothing_without_it(
    handled: Handled,
) -> None:
    """The default board is the live one, so it is read here with no credential to reach it.

    The board's token is taken out of the environment and the store's machine-wide secrets
    file pointed at an empty one, so the read is refused before anything leaves this host.
    """
    no_secrets = handled.bench.tmp / "no-secrets.env"
    no_secrets.write_text("", encoding="utf-8")
    environment = handled.bench.environment | {"ONETASKGRAPH_SECRETS_FILE": str(no_secrets)}
    environment.pop("GH_PROJECTS_TOKEN", None)

    refused = _run(
        ["just", "follow-ups-handle-comments", handled.main],
        handled.bench,
        environment=environment,
    )

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert f"source {tickets.BOARD} could not answer" in refused.stderr
    assert "GH_PROJECTS_TOKEN is missing or empty" in refused.stderr
    assert "wrote run" not in refused.stderr
    assert not (handled.bench.runs / f"{handled.follow_up_run}-4").exists()


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        ((), "name the run whose board comments to handle as the first word"),
        (("--to", BOARD), "name the run whose board comments to handle as the first word"),
        (("a/run",), "'a/run' is not a run id"),
        (("some-run", "--to"), "--to was given no value"),
        (("some-run", "--to="), "--to was given no value"),
        (("some-run", "--feedback", "file.md"), "'--feedback' is not an option of this recipe"),
    ],
)
def test_an_invocation_the_recipe_cannot_read_is_refused_before_the_board_is_read(
    arguments: tuple[str, ...], refusal: str, handled: Handled
) -> None:
    refused = _run(["just", "follow-ups-handle-comments", *arguments], handled.bench)

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert f"follow-ups-handle-comments: {refusal}" in refused.stderr
    assert "wrote run" not in refused.stderr


def test_a_checkout_nobody_provisioned_is_refused_naming_bootstrap(tmp_path: Path) -> None:
    """The recipe and its script alone, in a checkout with no `.venv` to read the board with."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    (tmp_path / "scripts").mkdir()
    shutil.copy2(REPO_ROOT / "justfile", tmp_path / "justfile")
    script = "scripts/follow-ups-handle-comments.sh"
    shutil.copy2(REPO_ROOT / script, tmp_path / script)

    refused = subprocess.run(
        ["just", "follow-ups-handle-comments", "some-run"],  # noqa: S607 - the recipe itself
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert "has no Python interpreter at" in refused.stderr
    assert "provision this checkout with 'just bootstrap'" in refused.stderr


# llmlint: ignore-block[shell_test_tiers_stay_split] The finding is about which Nx project
# owns these journeys, which is a property of the module rather than of any one of them:
# each drives the recipe every journey of this module drives, from the `plan-tooling`
# project whose `planToolingWorkspace` key already covers everything they read — the
# `scripts/**` the mirror copies, the `justfile`, `orchestrator/**` — so a project of their
# own would split one recipe's journeys across tiers behind no narrower key.

#: The board credential's name, the value these journeys plant in a mirror's `.env` — never
#: a real token — and the value a process already holding the name keeps.
BOARD_CREDENTIAL = "GH_PROJECTS_TOKEN"
PLANTED_CREDENTIAL = "not-a-real-token-planted-by-this-journey"
PROCESS_CREDENTIAL = "the-value-this-process-already-defines"

#: Records which board credential the store was handed, installed as the mirror checkout's
#: own plan-store CLI: every command is answered by the pinned store, and this appends one
#: line per invocation it served, because a credential is on no command line and the store's
#: own answer over a `local-md` board needs none.
CREDENTIAL_RECORDING_STORE = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "${GH_PROJECTS_TOKEN-<unset>}" >>"$CREDENTIAL_TRACE"
exec "$REAL_PLAN_STORE" "$@"
"""

#: `scripts/follow-ups.sh`'s own line for a run with no drafts, as the mirror's run has none.
NOTHING_TO_VERIFY = "there is nothing to verify and no follow-up run was launched"


class Mirror(NamedTuple):
    """A checkout of this repository's real scripts, with a toolchain and `.env` of its own."""

    checkout: Path
    trace: Path
    bench: Bench
    run: str
    issue: QualifiedTaskId


def _mirrored_toolchain(checkout: Path, store: str) -> Path:
    """``checkout``'s `.venv`: this checkout's locked toolchain with ``store`` as its plan store.

    Every plan-store read of the package runs the `onetaskgraph` beside the interpreter
    that imported the SDK — `orchestrator.plan_store.locked_binary`, which consults no
    search path — so a store standing in front of the pinned one is that file or nothing.
    This checkout's own `.venv/bin/onetaskgraph` is the real install and never a journey's
    to replace, so the mirror is given a virtual environment of its own: the same
    `pyvenv.cfg`, the same `lib` — the SDK, and this package's editable install — and the
    same interpreter, reached through a symlink so `sys.executable` names the mirror's
    `bin`, with ``store`` beside it under the CLI's name. Returns that file.
    """
    venv = checkout / ".venv"
    (venv / "bin").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ".venv" / "pyvenv.cfg", venv / "pyvenv.cfg")
    (venv / "lib").symlink_to(REPO_ROOT / ".venv" / "lib")
    (venv / "bin" / "python3").symlink_to(REPO_ROOT / ".venv" / "bin" / "python3")
    installed = venv / "bin" / ONETASKGRAPH_BIN.name
    # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] The published CLI is the one boundary this suite doubles a store at, and every ``store`` installed here delegates every command to the pinned one, recording what it was handed or rewriting one field of one answer: no real store reports which credential reached it, and none answers a page cursor twice, so the journeys that need either can be driven against nothing else.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    installed.write_text(store, encoding="utf-8")
    installed.chmod(0o755)
    return installed


def _mirror(
    tmp_path: Path, credentials: str | None, store: str = CREDENTIAL_RECORDING_STORE
) -> Mirror:
    """The recipe's checkout, the board it reads, and the run that owns an issue there.

    `scripts/credentials-env.sh` reads the `.env` at the root of the checkout it is sourced
    from, and this checkout's is the host's — a real file, never a journey's to write. So the
    recipe is driven from a copy of the real `scripts/` and `justfile` beside a toolchain
    mirroring this checkout's (:func:`_mirrored_toolchain`) whose plan store is ``store``,
    with the `.env` this journey states. The board is the same `local-md` stand-in every
    journey here reads, holding one issue the run owns with a person's comment nobody
    answered; the run's ticket file is not under the drafts root, so `scripts/follow-ups.sh`
    launches nothing after the feedback is written and says so.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path)
    checkout = tmp_path / "mirror"
    shutil.copytree(REPO_ROOT / "scripts", checkout / "scripts")
    shutil.copy2(REPO_ROOT / "justfile", checkout / "justfile")
    _mirrored_toolchain(checkout, store)
    if credentials is not None:
        (checkout / ".env").write_text(credentials, encoding="utf-8")
    run = tickets.RunId(f"hc-mirror-{os.getpid()}")
    issue = _filed(bench, run, OWN_CAUSE)
    tickets.ticket_path(bench.drafts_root, run, OWN_CAUSE).unlink()
    _commented(bench, issue, ON_OWN, REVIEWER)
    return Mirror(checkout, tmp_path / "credential.trace", bench, run, issue)


def _handled_from(
    mirror: Mirror,
    credential: str | None,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """`just follow-ups-handle-comments` on the mirror, holding ``credential`` or no name.

    ``environment`` is what the store the mirror carries reads, over the bench's own.
    """
    environment = {
        name: value
        for name, value in (mirror.bench.environment | (environment or {})).items()
        if name != BOARD_CREDENTIAL
    }
    if credential is not None:
        environment[BOARD_CREDENTIAL] = credential
    # llmlint: ignore[e2e_not_mocked] The pinned store answers every command; what stands in front of it, as the mirror's own locked plan store, records which credential it was handed — on no command line and in no answer over a `local-md` board — or rewrites one field of one answer, and changes nothing else the recipe reads.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    environment["CREDENTIAL_TRACE"] = str(mirror.trace)
    return subprocess.run(  # noqa: S603 - this checkout's own recipe over the installed store
        ["just", "follow-ups-handle-comments", mirror.run, "--to", BOARD],  # noqa: S607 - `just` from the search path, as an operator invokes it
        cwd=mirror.checkout,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )


def _handed(mirror: Mirror) -> set[str]:
    """Every credential value the store was handed, one per command the recipe ran."""
    assert mirror.trace.is_file(), "the recipe read the board through no store command"
    handed = mirror.trace.read_text(encoding="utf-8").splitlines()
    assert handed, "the recipe read the board through no store command"
    return set(handed)


@pytest.mark.parametrize(
    ("held", "handed"),
    [(None, PLANTED_CREDENTIAL), (PROCESS_CREDENTIAL, PROCESS_CREDENTIAL)],
    ids=["from-the-file", "the-process-value-wins"],
)
def test_the_board_read_is_handed_the_credential_this_checkout_supplies(
    tmp_path: Path, held: str | None, handed: str
) -> None:
    """The token only in the checkout's `.env` reaches the board read; a held one is kept.

    Every other board-reading entry point sources `scripts/credentials-env.sh` first, and this
    recipe did not: on a host that correctly keeps the token only in that file it was refused
    by the store's own `missing or empty` until a person exported the file by hand.
    """
    mirror = _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")

    result = _handled_from(mirror, held)

    assert result.returncode == OK, result.stdout + result.stderr
    assert _handed(mirror) == {handed}, mirror.trace.read_text(encoding="utf-8")
    named = WROTE.search(result.stderr)
    assert named is not None, result.stderr
    feedback = Path(named[1]).read_text(encoding="utf-8")
    assert f"on `{mirror.issue}`, this run's issue" in feedback, feedback
    assert ON_OWN.rstrip() in feedback
    assert NOTHING_TO_VERIFY in result.stdout, result.stdout
    assert not list(mirror.bench.runs.glob(f"{mirror.run}{SUFFIX}*"))
    assert PLANTED_CREDENTIAL not in result.stdout + result.stderr, "a credential value was printed"


def test_a_credential_file_the_helper_refuses_refuses_the_recipe_before_the_board_is_read(
    tmp_path: Path,
) -> None:
    """The helper loads and then refuses the file itself: the recipe stops there, attributably.

    A malformed line is what an interrupted edit of `.env` leaves behind, and the helper
    refuses the whole file rather than launching over half of it. That refusal reaches the
    operator under this recipe's own name, and nothing reads the board first.
    """
    mirror = _mirror(tmp_path, "not-an-assignment-with-a-value-that-must-not-appear\n")

    result = _handled_from(mirror, None)

    assert result.returncode == REFUSED, result.stdout + result.stderr
    assert (
        f"follow-ups-handle-comments: malformed credential line 1 in {mirror.checkout / '.env'}; "
        "write it as KEY=VALUE or make it a '#' comment, then retry"
    ) in result.stderr, result.stderr
    assert "must-not-appear" not in result.stderr, "a credential file's line reached a diagnostic"
    assert not mirror.trace.exists(), "the board was read over a credential file the helper refused"
    assert not (mirror.bench.drafts_root / comments.FEEDBACK_DIRECTORY / mirror.run).exists()
    assert "wrote run" not in result.stderr
    assert not list(mirror.bench.runs.glob(f"{mirror.run}{SUFFIX}*"))


def _refusals(stderr: str, program: str) -> list[str]:
    """The lines ``program`` refused in, without the name it prefixed each with.

    What is around them differs by what ran the program: `just` adds a line saying the
    recipe failed, and bash says where a helper's syntax error is before either refuses.
    """
    return [
        line.removeprefix(f"{program}: ")
        for line in stderr.splitlines()
        if line.startswith(f"{program}: ")
    ]


@pytest.mark.parametrize(
    "sabotage",
    ["missing", "unreadable", "unloadable"],
    ids=["helper-missing", "helper-unreadable", "helper-unloadable"],
)
def test_the_recipe_refuses_before_the_board_is_read_when_the_credentials_helper_cannot_be_loaded(
    tmp_path: Path, sabotage: str
) -> None:
    """The one source of the credential is gone, unreadable or broken: refused naming it.

    In the shape `scripts/plan-store.sh` refuses the same thing — its own refusal, driven on
    the same checkout, differs from this recipe's only in the program each names — and
    nothing is read first.
    """
    mirror = _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")
    helper = mirror.checkout / "scripts" / "credentials-env.sh"
    match sabotage:
        case "missing":
            helper.unlink()
        case "unreadable":
            helper.chmod(0)
            if os.access(helper, os.R_OK):
                pytest.skip("this user reads a file with no permission bits: nothing is unreadable")
        case "unloadable":
            # A file `.` fails on rather than one whose function then fails: a syntax error
            # is what an interrupted edit leaves behind.
            helper.write_text("export_host_credentials() {\n", encoding="utf-8")

    result = _handled_from(mirror, None)
    wrapper = subprocess.run(  # noqa: S603 - the wrapper whose refusal this recipe's mirrors
        [str(mirror.checkout / "scripts" / "plan-store.sh"), "true"],
        cwd=mirror.checkout,
        env=mirror.bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert result.returncode == REFUSED, result.stdout + result.stderr
    assert str(helper) in result.stderr, result.stderr
    assert "run 'just bootstrap', then retry" in result.stderr, result.stderr
    assert wrapper.returncode == REFUSED, wrapper.stdout + wrapper.stderr
    assert _refusals(result.stderr, "follow-ups-handle-comments") == _refusals(
        wrapper.stderr, "plan-store"
    ), result.stderr + wrapper.stderr
    assert not mirror.trace.exists(), "the board was read without the checkout's credential"
    assert not (mirror.bench.drafts_root / comments.FEEDBACK_DIRECTORY / mirror.run).exists()
    assert "wrote run" not in result.stderr
    assert not list(mirror.bench.runs.glob(f"{mirror.run}{SUFFIX}*"))


#: A cursor that never advances: what the store below answers for every page.
REPEATING_CURSOR = "ab"

#: A store whose every task listing reports :data:`REPEATING_CURSOR` as its `next`: the
#: pinned store answers the query with the page it was *not* asked to resume, and this
#: rewrites that page's one field. Every other command is the pinned store's own answer,
#: which keeps the double at the one boundary this suite doubles a store at — the published
#: CLI, where the package's locked-install rule reads it — and in the one field the
#: pathology is. It logs each listing's command line, so which pages the recipe asked for is
#: read off what the store was asked rather than inferred.
REPEATING_STORE = """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = task ] && [ "${2:-}" = list ]; then
    printf '%s\\n' "$*" >>"$REPEATING_STORE_LOG"
    kept=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --page) shift 2 ;;
            *) kept+=("$1"); shift ;;
        esac
    done
    "$REAL_PLAN_STORE" "${kept[@]}" | "$REPEATING_STORE_PYTHON" -c '
import json, sys
page = json.load(sys.stdin)
page["next"] = sys.argv[1]
json.dump(page, sys.stdout)
' "$REPEATING_CURSOR"
    exit 0
fi
exec "$REAL_PLAN_STORE" "$@"
"""


def test_a_store_answering_a_page_cursor_again_is_refused_by_name_before_anything_is_written(
    tmp_path: Path,
) -> None:
    """The recipe over a board whose store answers the cursor it was just handed.

    Followed again, the same page would be gathered twice — or, from a cursor that never
    advances, for ever. The recipe asks for the first page, follows the cursor once, is
    answered the same cursor, and refuses naming it: no third page is asked for, no feedback
    is written and nothing is launched.
    """
    # llmlint: ignore[e2e_not_mocked] The published CLI is the one boundary this suite doubles a store at, and no real store answers a cursor twice to drive this against: the pinned store answers every command, and one field of one listing's answer is rewritten.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    mirror = _mirror(tmp_path, None, REPEATING_STORE)
    asked_log = tmp_path / "pages-asked.log"

    refused = _handled_from(
        mirror,
        None,
        environment={
            "REPEATING_STORE_LOG": str(asked_log),
            "REPEATING_STORE_PYTHON": str(mirror.checkout / ".venv" / "bin" / "python3"),
            "REPEATING_CURSOR": REPEATING_CURSOR,
        },
    )

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert (
        f"refused: listing the board {BOARD!r} answered the page cursor {REPEATING_CURSOR!r} "
        "again after it was already followed; refusing to read the same page twice"
    ) in refused.stderr, refused.stderr
    asked = asked_log.read_text(encoding="utf-8").splitlines()
    assert len(asked) == 2, asked
    assert "--page" not in asked[0] and f"--page {REPEATING_CURSOR}" in asked[1], asked
    assert "wrote run" not in refused.stderr
    assert not (mirror.bench.drafts_root / comments.FEEDBACK_DIRECTORY / mirror.run).exists()
    assert not list(mirror.bench.runs.glob(f"{mirror.run}{SUFFIX}*"))


# llmlint: ignore-end[shell_test_tiers_stay_split]
