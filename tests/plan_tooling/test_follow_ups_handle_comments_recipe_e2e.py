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
prints, which is how an agent answers a comment on its own issue.

One module fixture seeds the board, drives the refusal on a run with nothing new, drives the
re-dispatch on the run with comments, and asks again once the run has answered — readings of
one board's life.
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

#: The people commenting, as the board records their authors.
REVIEWER = "a-reviewer"
MAINTAINER = "a-maintainer"

#: What each seeded comment says, so the feedback can be read for exactly which reached it.
ANSWERED = "Asked before the run last responded, and already answered.\n"
ON_OWN = "Page 9 still never renders — the `cursor` example needs ```page=9```.\n"
ON_SHARED = "Does this also hit the nightly sweep?\n"
LATER = "One more thing, after the run answered: the weekly sweep too.\n"
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
    statuses_before: dict[str, object]
    refused: subprocess.CompletedProcess[str]
    statuses_after_refusal: dict[str, object]
    handled: subprocess.CompletedProcess[str]
    watched: subprocess.CompletedProcess[str]
    follow_up_run: str
    launched: list[str]
    prompts: list[str]
    statuses_after: dict[str, object]
    again: subprocess.CompletedProcess[str]
    ledger_refused: subprocess.CompletedProcess[str]
    later: subprocess.CompletedProcess[str]
    later_prompts: list[str]


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


def _statuses(bench: Bench) -> dict[str, object]:
    """The category every board item is held at."""
    items = _store(bench, "task", "list", "--source", BOARD)["items"]
    assert isinstance(items, list), items
    return {str(one["id"]): _category(one["item"]) for one in items}


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
    main, earlier, quiet = (
        tickets.RunId(f"hc-{role}-{pid}") for role in ("main", "earlier", "quiet")
    )
    python = str(REPO_ROOT / ".venv" / "bin" / "python3")
    started: list[str] = []
    try:
        # The board as three earlier follow-up runs left it.
        own_issue = _filed(bench, main, OWN_CAUSE)
        shared_issue = _filed(bench, earlier, SHARED_CAUSE)
        quiet_issue = _filed(bench, quiet, QUIET_CAUSE)
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
        statuses_before = _statuses(bench)

        # A run whose every comment predates its last response.
        refused = _run(["just", "follow-ups-handle-comments", quiet, "--to", BOARD], bench)
        statuses_after_refusal = _statuses(bench)

        # The main run, answered by an agent that copies its ticket again from the board's
        # status.
        own_ticket = tickets.ticket_path(bench.drafts_root, main, OWN_CAUSE)
        _script(
            bench,
            main,
            [
                _decided_and_copied(
                    python, str(ONETASKGRAPH_BIN), own_ticket, tickets.qualified_id(main, OWN_CAUSE)
                )
            ],
        )
        log = tmp / "prompts.jsonl"
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

        # Asked again once the run has answered, there is nothing new.
        again = _run(["just", "follow-ups-handle-comments", main, f"--to={BOARD}"], bench)

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
        assert launched_after_ledger_refusal == launched, launched_after_ledger_refusal
        later_log = tmp / "later-prompts.jsonl"
        later = _run(
            ["just", "follow-ups-handle-comments", main, "--to", BOARD],
            bench,
            environment=bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(later_log)},
        )
        started.append(f"{follow_up_run}-2")
        return Handled(
            bench=bench,
            main=main,
            quiet=quiet,
            own_issue=own_issue,
            shared_issue=shared_issue,
            quiet_issue=quiet_issue,
            statuses_before=statuses_before,
            refused=refused,
            statuses_after_refusal=statuses_after_refusal,
            handled=handled_run,
            watched=watched,
            follow_up_run=follow_up_run,
            launched=launched,
            prompts=_prompts(log),
            statuses_after=statuses_after,
            again=again,
            ledger_refused=ledger_refused,
            later=later,
            later_prompts=_prompts(later_log),
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
    assert "none is a person's newer than its last response at " in refused.stderr
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
        assert f"- URL: {url}\n- Author: {author}\n" in feedback, feedback
        assert body.rstrip() in feedback
    for absent in (ANSWERED, "This run hit it too.", "The earlier run's evidence.", QUIET_OLD):
        assert absent.rstrip() not in feedback, feedback


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
    assert f"task copy {tickets.qualified_id(handled.main, OWN_CAUSE)}" in ran, (
        "the answering agent never copied the ticket, so the status was never put to the test"
    )


def test_once_the_run_has_answered_the_same_comments_are_not_feedback_again(
    handled: Handled,
) -> None:
    _feedback(handled)
    again = handled.again

    assert again.returncode == REFUSED, again.stdout + again.stderr
    assert f"run {handled.main} has no new feedback" in again.stderr


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
    assert ON_SHARED.rstrip() not in feedback and ON_OWN.rstrip() not in feedback
    second = f"{handled.follow_up_run}-2"
    assert f"follow-ups: launching run {second}" in later.stderr
    (prompt,) = handled.later_prompts
    assert feedback.rstrip() in prompt
    results = _run(["just", "results", second], handled.bench)
    assert re.search(rf"^\s+{NODE}\s+done$", results.stdout, re.MULTILINE), results.stdout


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
    assert not (handled.bench.runs / f"{handled.follow_up_run}-3").exists()


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
    assert not (handled.bench.runs / f"{handled.follow_up_run}-3").exists()


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
