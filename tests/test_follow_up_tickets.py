"""What a verified follow-up ticket is, and who owns what on the board.

`tests/plan_tooling/test_follow_ups_recipe_e2e.py` drives the recipe and a scripted agent
through the real store and a real launch. What is proven here is what that journey reaches
only one shape at a time: every way a store item can fail the ticket shape, the ownership
predicates in both directions, and the single-pass fill that brings the manager's feedback
into the task verbatim. The `validate` and `check-run` commands are driven against the
installed `onetaskgraph`, over a drafts root this test names through the helper that
composes that name.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable
from pathlib import Path

import follow_up_variables
import pytest

from orchestrator import follow_up_tickets as tickets
from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.root import REPO_ROOT

RUN = "listing-run"
OTHER_RUN = "earlier-run"
CAUSE = "cursor-skips-last-page"
REPOSITORY = "github.com/nickderobertis/some-service"
COMMIT = "0123456789abcdef0123456789abcdef01234567"

BODY = "\n\n".join(
    f"## {heading}\n\nWhat this ticket says under {heading}." for heading in tickets.HEADINGS
)


#: A sound ticket, which each test below states only its departures from.
SOUND_TICKET = tickets.Ticket(
    title="some-service: the listing cursor skips the last page",
    status=tickets.Status.OPEN,
    root_cause=tickets.RootCause(CAUSE),
    repository=tickets.Origin(REPOSITORY),
    created_by_run=tickets.RunId(RUN),
    owning_runs=(tickets.RunId(RUN),),
    drafts=(tickets.QualifiedDraftId(f"drafts:{RUN}/drafts/20260101T000000Z-cursor"),),
    basis=(tickets.Basis(tickets.Origin(REPOSITORY), tickets.Commit(COMMIT)),),
    verified_at=tickets.Timestamp("2026-01-01T00:00:00Z"),
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
        "status": {"category": held.status, "name": held.status},
        "labels": [],
        "project": None,
        "repositories": [],
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


def test_a_withdrawn_ticket_is_still_a_ticket() -> None:
    withdrawn = _ticket(status=tickets.Status.WITHDRAWN)
    assert tickets.from_store_item(_item(withdrawn)).status == tickets.Status.WITHDRAWN


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


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (_set("project", "listing-run"), "carries a `project`"),
        (_set("repositories", [REPOSITORY]), "carries `repositories`"),
        (_set("status", {"category": "done", "name": "done"}), "the status is 'done'"),
        (_no_record, "carries no `orchestrator.follow-up` metadata record"),
        (_drop_record("basis"), "record is missing basis"),
        (_set_record("extra", 1), "carries keys this does not write: extra"),
        (_set_record("schema", 2), "is schema 2"),
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
            _set("content", BODY.replace("What this ticket says under Evidence.", "")),
            "the body's `## Evidence` section is empty",
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
    "@TICKET_CONTRACT@\n@COMMENT_CONTRACT@\n@REDISPATCH@\n@FEEDBACK@\nAgain, @RUN@.\n"
)


def _compose(*, feedback: str | None = None, redispatch: bool = False) -> str:
    return tickets.compose(
        TEMPLATE,
        run=RUN,
        board="followups",
        drafts_root=Path("/drafts-root"),
        validate="python -m orchestrator.follow_up_tickets validate",
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
    assert (
        tickets.ticket_contract(RUN, "followups").replace("@DRAFTS_ROOT@", "/drafts-root") in task
    )
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
        checkout=Path("/checkout"),
        feedback=None,
        redispatch=False,
    )

    assert task.rstrip().endswith("Copy onto followups from /drafts-root with v.")


@pytest.mark.reads_docs
def test_the_tracked_template_composes_into_a_task_with_nothing_left_unfilled() -> None:
    """The template the recipe composes from is one this module accepts, whole."""
    template = (REPO_ROOT / "config" / "follow-up-task.md").read_text(encoding="utf-8")

    task = tickets.compose(
        template,
        run=RUN,
        board="followups",
        drafts_root=Path("/drafts-root"),
        validate="python -m orchestrator.follow_up_tickets validate",
        checkout=Path("/checkout"),
        feedback="Merge the two cursor tickets.\n",
        redispatch=True,
    )

    assert tickets.PLACEHOLDER.search(task) is None
    assert tickets.comment_contract(RUN, "followups") in task
    assert "## This is a re-dispatch" in task
    assert "Merge the two cursor tickets." in task


def test_the_contract_renders_the_ticket_with_every_key_and_heading() -> None:
    contract = tickets.ticket_contract(RUN, "followups")

    for key in tickets.RECORD_KEYS:
        assert f'"{key}"' in contract, key
    for heading in tickets.HEADINGS:
        assert f"## {heading}" in contract, heading
    assert "no `project`" in contract and "no `repositories`" in contract


def test_feedback_reaches_the_task_verbatim_under_its_own_heading() -> None:
    feedback = "Merge @RUN@'s two cursor tickets & drop `\\1`; keep @TICKET_CONTRACT@ literal.\n"

    task = _compose(feedback=feedback, redispatch=True)

    heading = "## Feedback on the previous follow-up run"
    assert heading in task
    assert feedback.rstrip() in task.split(heading, 1)[1]
    assert "## This is a re-dispatch" in task
    assert "an issue run `listing-run` created is **edited**" in task


@pytest.mark.parametrize(
    ("template", "reason"),
    [
        (TEMPLATE + "@UNKNOWN@", "placeholders nothing fills: UNKNOWN"),
        (TEMPLATE.replace("@FEEDBACK@", ""), "missing placeholders: FEEDBACK"),
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


def test_validate_names_each_problem_of_a_ticket_the_store_reads(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rendered = tickets.render(_ticket()).replace('status: "todo"', 'status: "todo"\nproject: "p"')
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
            + ["--board", "b", "--validate", "v", "--checkout", "/c"],
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
        + ["--board", "b", "--validate", "v", "--checkout", "/c"]
    )

    assert status == tickets.UNRUNNABLE
    assert "missing placeholders" in capsys.readouterr().err
