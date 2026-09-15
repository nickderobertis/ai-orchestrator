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
from collections.abc import Callable
from pathlib import Path

import follow_up_variables
import pytest

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


def _body(host: str = HOST) -> str:
    return "\n\n".join(
        f"## {heading}\n\nWhat this ticket says under {heading}."
        + (f" Verified on `{host}`." if heading == tickets.EVIDENCE else "")
        for heading in tickets.HEADINGS
    )


BODY = _body()
EVIDENCE_TEXT = f"What this ticket says under {tickets.EVIDENCE}. Verified on `{HOST}`."


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
        "status": {"category": held.status.value, "name": held.status.written},
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

    assert held["schema"] == tickets.SCHEMA == 3
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
        (_status("draft"), "the status is 'draft'"),
        (_set("status", "open"), "the status is 'open'"),
        (_no_record, "carries no `orchestrator.follow-up` metadata record"),
        (_drop_record("basis"), "record is missing basis"),
        (_drop_record("host"), "record is missing host"),
        (_set_record("extra", 1), "carries keys this does not write: extra"),
        (_set_record("schema", 1), "is schema 1, and this reads schema 3"),
        (_set_record("schema", 2), "is schema 2, and this reads schema 3"),
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


def test_a_schema_2_ticket_is_refused_naming_its_schema_first() -> None:
    """The shape every ticket on the live board carries: schema 2, and no `repositories`."""
    item = _item()
    _record(item)["schema"] = 2
    item["repositories"] = []

    found = tickets.problems(item, run=RUN, root_cause=CAUSE)

    assert found[0].startswith("the record is schema 2, and this reads schema 3"), found
    assert any("the ticket carries no `repositories`" in problem for problem in found), found


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


def test_a_comment_is_owned_by_the_run_its_last_line_names() -> None:
    comment = tickets.render_comment(RUN, CAUSE, "The cursor skipped page 9 too.")

    assert comment.splitlines()[0] == f"Additional evidence from follow-up run `{RUN}`."
    assert comment.rstrip("\n").splitlines()[-1] == (
        f'<!-- orchestrator:follow-up-comment run="{RUN}" root_cause="{CAUSE}" -->'
    )
    assert tickets.comment_owner(comment) == tickets.CommentOwner(RUN, CAUSE)
    # The store keeps a body with trailing blank lines; the marker is still the last line.
    assert tickets.comment_owner(comment + "\n\n") == tickets.CommentOwner(RUN, CAUSE)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Just a comment somebody wrote.",
        tickets.comment_marker(RUN, CAUSE) + "\nand a line after it",
        '<!-- orchestrator:follow-up-comment run="../x" root_cause="c" -->',
    ],
)
def test_a_comment_whose_last_line_is_no_marker_belongs_to_no_run(body: str) -> None:
    assert tickets.comment_owner(body) is None
    assert not tickets.may_change_comment(RUN, body)


def test_a_run_changes_only_its_own_issues_and_comments_and_comments_only_on_others() -> None:
    own, other, unowned = _item(), _item(_ticket(created_by_run=OTHER_RUN)), {"metadata": {}}

    assert tickets.issue_owner(own) == RUN
    assert tickets.may_change_issue(RUN, own)
    assert not tickets.may_change_issue(RUN, other)
    assert not tickets.may_change_issue(RUN, unowned)
    assert not tickets.may_comment_on(RUN, own), "a run edits its own issue, never comments"
    assert tickets.may_comment_on(RUN, other)
    assert not tickets.may_comment_on(RUN, unowned)
    assert tickets.may_change_comment(RUN, tickets.render_comment(RUN, CAUSE, "x"))
    assert not tickets.may_change_comment(RUN, tickets.render_comment(OTHER_RUN, CAUSE, "x"))


TEMPLATE = (
    "Run @RUN@ onto @BOARD@ under @DRAFTS_ROOT@, validating with @VALIDATE@ in @CHECKOUT@.\n"
    "Decide each status with @BOARD_STATUS@.\n"
    "@TICKET_CONTRACT@\n@COMMENT_CONTRACT@\n@REDISPATCH@\n@FEEDBACK@\nAgain, @RUN@.\n"
)
BOARD_STATUS = "python -m orchestrator.follow_up_tickets board-status"


def _contract(run: str = RUN, board: str = "followups", root: str = "/drafts-root") -> str:
    """The ticket contract as a composed task carries it, its values filled."""
    return (
        tickets.ticket_contract(run, board)
        .replace("@DRAFTS_ROOT@", root)
        .replace("@BOARD_STATUS@", BOARD_STATUS)
    )


def _compose(*, feedback: str | None = None, redispatch: bool = False) -> str:
    return tickets.compose(
        TEMPLATE,
        run=RUN,
        board="followups",
        drafts_root=Path("/drafts-root"),
        validate="python -m orchestrator.follow_up_tickets validate",
        board_status=BOARD_STATUS,
        checkout=Path("/checkout"),
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
    assert tickets.comment_contract(RUN, "followups") in task
    assert f"onetaskgraph task copy drafts:{RUN}/tickets/<root-cause> --to followups" in task
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
        feedback="Merge the two cursor tickets.\n",
        redispatch=True,
    )

    assert tickets.PLACEHOLDER.search(task) is None
    assert _contract() in task
    assert tickets.comment_contract(RUN, "followups") in task
    assert "## This is a re-dispatch" in task
    assert "Merge the two cursor tickets." in task
    steps = task.split("## What to do, in order", 1)[1].split("## The verified ticket", 1)[0]
    decided = steps.index(f"`{BOARD_STATUS} --board followups <path of the ticket>`")
    assert decided < steps.index("**Put each ticket on the board.**"), steps


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
        f"When `board-status` exits {tickets.OUTSIDE_OWNER}, or `onetaskgraph task copy` refuses "
        "the ticket"
    ) in flat
    assert (
        "copy nothing for that ticket, never retry it with `repositories` removed or changed to "
        "get it filed, and report what was printed"
    ) in flat
    assert '\nrepositories: ["<normalized origin the root cause lives in' in contract
    assert f"A new ticket is `{tickets.Status.PROPOSED}`" in contract
    assert "A ticket the board already holds carries the status the board holds it at" in contract
    assert f"withdraws is `{tickets.Status.WITHDRAWN}`" in contract
    assert "unless the board shows it as accepted: then copy nothing, leave the local" in contract
    assert "report that you would have withdrawn it and why" in contract
    assert "Run `hostname` on the machine you run on and write exactly what it prints" in contract
    assert "@BOARD_STATUS@ --board followups <path of the ticket>" in contract


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
        "its `repositories` naming its record's `repository` and its `host` read from this "
        "machine with `hostname`"
    ) in flat


@pytest.mark.parametrize(
    ("template", "reason"),
    [
        (TEMPLATE + "@UNKNOWN@", "placeholders nothing fills: UNKNOWN"),
        (TEMPLATE.replace("@FEEDBACK@", ""), "missing placeholders: FEEDBACK"),
        (TEMPLATE.replace("@BOARD_STATUS@", ""), "missing placeholders: BOARD_STATUS"),
        (TEMPLATE + "@COMMENT_CONTRACT@", "more than once: COMMENT_CONTRACT"),
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


def test_validate_reads_a_rendered_ticket_through_the_store_as_sound(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(drafts_root, _ticket())

    assert tickets.main(["validate", str(path)]) == tickets.SOUND
    assert tickets.read_ticket(path) == _ticket()
    assert "is a sound ticket" in capsys.readouterr().out


@pytest.mark.parametrize("status", list(tickets.Status))
def test_the_store_reads_each_status_a_ticket_is_written_with_as_that_status(
    drafts_root: Path, status: tickets.Status
) -> None:
    """A status is the store's category, written in whichever word the store reads as it."""
    path = _write(drafts_root, _ticket(status=status))
    run, cause = tickets.located_path(path)
    item = plan_store.one_item(
        plan_store.store_json(["task", "show", tickets.qualified_id(run, cause)]), "task"
    )

    assert item["status"] == {"category": status.value, "name": status.written}
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
    """Move the board item to ``word``, the way a person edits the item's status.

    A `local-md` item is a file, and the installed store has no verb that changes a status,
    so a person moves one by editing it; the store reads the edit back through `task show`.
    """
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
    _moved(destination, held.written)
    assert _board_category(destination) == held.value

    status, printed, _ = _decided(ticket, capsys)

    assert (status, printed) == (tickets.SOUND, f"{held.written}\n")
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
        assert tickets.UNPLACED not in (tickets.SOUND, tickets.UNRUNNABLE, tickets.ACCEPTED)
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
        _moved(_on_board(ticket), held.written)

    assert _decided(ticket, capsys, "--withdraw") == (tickets.SOUND, "cancelled\n", "")


@pytest.mark.parametrize("held", [status for status in tickets.Status if status.accepted])
def test_a_withdrawal_of_a_ticket_the_board_shows_as_accepted_is_refused_and_moves_nothing(
    board: Path, drafts_root: Path, capsys: pytest.CaptureFixture[str], held: tickets.Status
) -> None:
    ticket = _write(drafts_root, _ticket())
    before = ticket.read_text(encoding="utf-8")
    destination = _on_board(ticket)
    _moved(destination, held.written)

    status, printed, reported = _decided(ticket, capsys, "--withdraw")

    assert status == tickets.ACCEPTED
    assert tickets.ACCEPTED not in (tickets.SOUND, tickets.UNRUNNABLE, tickets.UNPLACED)
    assert printed == ""
    assert f"holds this ticket's item at `{held}`" in reported
    assert "this run never withdraws it" in reported
    assert _board_category(destination) == held.value
    assert ticket.read_text(encoding="utf-8") == before


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
            + ["--board", "b", "--validate", "v", "--board-status", "s", "--checkout", "/c"],
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
    )

    assert status == tickets.UNRUNNABLE
    assert "missing placeholders" in capsys.readouterr().err
