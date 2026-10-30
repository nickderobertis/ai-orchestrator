"""A comment edited later in the second its gathering quoted is selected again.

A board may date a comment finer than a second, as Linear does, and a reply answers a comment
only as it stood when its gathering quoted it. So a comment's version is carried at the
precision its board reports — through the snapshot's selection record, the feedback file's
quoted `Last changed` and the reply's response account — and an edit later within that same
second is a newer version, unanswered again.

This drives `just follow-ups-answer-comments`'s own two first steps,
`orchestrator.follow_up_comments gather` and `launch`, over the loopback GitHub served as
`followups`, then reads the next gathering's selection. Nothing launches a follow-up run: what
is under test is which version a gathering quotes and which comment the next one selects. The
person's comment and their edit are planted on the loopback's row at an instant the journey
names, the one way a test can date an act a person performs on the board; every read and write
of the gathering is the locked store's.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import follow_up_boundary
import follow_up_variables
import github_board as remote
import pytest
from follow_up_ticket_shape import impact_prose
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.root import REPO_ROOT

BOARD = tickets.BOARD
CAUSE = "the-retry-loop-never-backs-off"
PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python3")
REPOSITORY = f"github.com/{remote.SIBLING_REPOSITORY}"
COMMIT = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
WORKAROUND = "readers retry by hand"
LOCATIONS = f"**{tickets.CONTRIBUTING_LOCATIONS}.**\n- oneharness `src/retry.py`: where it is seen."
UNIT = "### Retry — `oneharness` (`src/retry.py`)\n\n`retry` backs off between attempts."
#: What a launch inherits from the dispatch it runs in, which a journey's own host does not.
INHERITED = ("ONEPIPELINE_RUN_ID", "ONEPIPELINE_NODE_SCRATCH_DIR", "ONEVCS_SESSION")


class Bench(NamedTuple):
    """The throwaway host this journey's gatherings run on."""

    environment: dict[str, str]
    tmp: Path
    drafts_root: Path


def _bench(tmp: Path) -> Bench:
    """A drafts root, an empty runs root and the store's boundary, for this journey alone."""
    drafts_root = tmp / "follow-ups"
    environment = {name: value for name, value in os.environ.items() if name not in INHERITED}
    root_name, plugin_name, _ = follow_up_variables.all_names()
    environment[root_name] = str(drafts_root)
    environment[plugin_name] = WRITABLE_PLUGIN
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp / "runs")
    (drafts_root / "runs").mkdir(parents=True)
    environment["ONETASKGRAPH_SOURCES__FOLLOW_UP_RUNS__CONFIG__ROOT"] = str(drafts_root / "runs")
    routed = tmp / "routed-followups"
    routed.mkdir()
    for name in [one for one in environment if one.startswith(remote.FOLLOWUPS_ENV_PREFIX)]:
        del environment[name]
    environment.update(
        {
            "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__PLUGIN": WRITABLE_PLUGIN,
            "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__CONFIG__ROOT": str(routed),
            "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__VISIBILITY": "private",
            "ONETASKGRAPH_SOURCES__FOLLOWUPS__ROUTES__0__REPOSITORIES": "github.com/hiddenco/*",
            "ONETASKGRAPH_SOURCES__FOLLOWUPS__ROUTES__0__TO": "routedfollowups",
            "ONETASKGRAPH_SECRETS_FILE": str(tmp / "no-secrets.env"),
        }
    )
    environment.update(follow_up_boundary.environment(environment))
    return Bench(environment, tmp, drafts_root)


def _run(bench: Bench, *command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own module and the installed store
        list(command),
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )


def _store(bench: Bench, *arguments: str) -> dict[str, object]:
    done = _run(bench, str(ONETASKGRAPH_BIN), *arguments, "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    answered: dict[str, object] = json.loads(done.stdout)
    return answered


def _ticket(run: str) -> tickets.Ticket:
    """``run``'s sound, proposed ticket for :data:`CAUSE`, verified on this host."""
    severity = (tickets.Severity.HIGH, tickets.Severity.MEDIUM)
    sections = []
    for heading in tickets.HEADINGS:
        match heading:
            case tickets.IMPACT:
                text = tickets.impact_section(
                    impact_prose("Readers see a retry storm.", workaround=WORKAROUND),
                    severity[0],
                    WORKAROUND,
                    severity[1],
                )
            case tickets.SUGGESTED_FIX:
                text = f"Back the retry off ({heading}).\n\n{LOCATIONS}\n\n{UNIT}"
            case tickets.EVIDENCE:
                text = f"Verified ({heading}). Verified on `{socket.gethostname()}`."
            case _:
                text = f"Verified ({heading})."
        sections.append(f"## {heading}\n\n{text}")
    return tickets.Ticket(
        title=f"{remote.SIBLING_REPOSITORY.name}: the retry loop never backs off",
        status=tickets.Status.PROPOSED,
        root_cause=tickets.RootCause(CAUSE),
        repository=tickets.Origin(REPOSITORY),
        created_by_run=tickets.RunId(run),
        owning_runs=(tickets.RunId(run),),
        drafts=(tickets.QualifiedDraftId(f"drafts:{run}/drafts/a-consumed-draft"),),
        basis=(tickets.Basis(tickets.Origin(REPOSITORY), tickets.Commit(COMMIT)),),
        verified_at=tickets.Timestamp("2026-01-01T00:00:00Z"),
        host=tickets.Host(socket.gethostname()),
        body="\n\n".join(sections),
        frequency=tickets.Frequency.INTERMITTENT,
    )


def _filed(bench: Bench, run: str) -> str:
    """``run``'s ticket, written, decided and copied onto the board as the agent does; its item."""
    path = tickets.ticket_path(bench.drafts_root, run, CAUSE)
    path.parent.mkdir(parents=True)
    path.write_text(tickets.render(_ticket(run)), encoding="utf-8")
    for verb in ("board-status", "copy"):
        done = _run(
            bench, PYTHON, "-m", "orchestrator.follow_up_tickets", verb, "--board", BOARD, str(path)
        )
        assert done.returncode == 0, done.stdout + done.stderr
    items = _store(bench, "task", "show", tickets.qualified_id(run, CAUSE))["items"]
    assert isinstance(items, list), items
    # The store records its link to the item its copy created on the ticket.
    return str(items[0]["item"]["metadata"][plan_store.COPIES_KEY][BOARD])


def _reply(bench: Bench, run: str, issue: str, answers: str) -> str:
    """``run``'s reply to one comment, posted through the store's own verb; its id."""
    listed = _store(bench, "task", "comment", "list", issue)["comments"]
    assert isinstance(listed, list), listed
    (answered,) = [one for one in listed if one["id"] == answers]
    body = bench.tmp / f"reply-{answers}.md"
    body.write_text(
        tickets.render_reply(
            run,
            CAUSE,
            answers=answers,
            url=str(answered["url"]),
            author=answered["author"] if isinstance(answered["author"], str) else None,
            response="Answered it.",
            verdict=tickets.Verdict.DOES_NOT_CONFIRM,
        ),
        encoding="utf-8",
    )
    return str(_store(bench, "task", "comment", "add", issue, "--body-file", str(body))["id"])


def _module(bench: Bench, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own module over the installed store
        [PYTHON, "-m", "orchestrator.follow_up_comments", *arguments],
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )


def _gathered(bench: Bench, run: str, *extra: str) -> tuple[str, Path]:
    """One `--run` gathering of ``run``'s items, and its intent file."""
    intent = bench.tmp / f"intent-{len(list(bench.tmp.glob('intent-*')))}.json"
    done = _module(
        bench,
        "gather",
        "--root",
        str(bench.drafts_root),
        "--intent",
        str(intent),
        "--to",
        BOARD,
        "--run",
        run,
        *extra,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout + done.stderr, intent


def _row(issue: str) -> remote._Issue:
    (row,) = [one for one in remote.BOARD.issues if one.content_id == issue.partition(":")[2]]
    return row


def _instant(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Versioned(NamedTuple):
    """The quoted version, then the next gathering's report with and without the late edit."""

    quoted: str
    snapshot_version: str
    unedited: str
    edited: str
    comment: str
    run: str


@pytest.fixture(scope="module")
def versioned(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Versioned]:
    tmp = tmp_path_factory.mktemp("comment-version")
    bench = _bench(tmp)
    run = f"fu-version-{os.getpid()}"
    with remote._serving_followups(bench.environment, fields=True):
        remote.BOARD.real_time = True
        issue = _filed(bench, run)
        # A person comments, dated finer than a second and after the run's own responses.
        asked_at = datetime.now(UTC).replace(microsecond=100000) + timedelta(seconds=2)
        # llmlint: ignore-block[tests_mirror_real_usage] What a person does on GitHub is the act
        # under test, and a person comments and edits on github.com rather than through this host's
        # store, so the loopback's row is where each act lands; it is dated at the instant named
        # because the property is about two acts within one second, which a wall clock cannot be
        # made to produce. Every read and write of the gathering is the locked store's.
        planted = remote.planted_comment(
            _row(issue), "Does this back off?\n", "a-person", _instant(asked_at)
        )
        # llmlint: ignore-end[tests_mirror_real_usage]
        asked = str(planted["id"])

        # The gathering quotes it, and writes the feedback its launch hands the run.
        _, intent = _gathered(bench, run)
        plan = bench.tmp / "plan.json"
        launched = _module(
            bench,
            "launch",
            *("--root", str(bench.drafts_root), "--intent", str(intent), "--plan", str(plan)),
        )
        assert launched.returncode == 0, launched.stdout + launched.stderr
        written = comments.read_plan(plan, bench.drafts_root)
        ((_, feedback),) = written.runs
        snapshot = comments.read_snapshot(written.snapshot)
        (chosen,) = snapshot.selection[tickets.RunId(run)]
        gathering = tickets.read_gathering(feedback.read_text(encoding="utf-8"))
        (fields,) = gathering.metadata

        # The run replies to the version it was handed, and accounts for it as the agent does.
        reply = _reply(bench, run, issue, asked)
        tickets.responses_path(feedback).write_text(
            json.dumps(
                {
                    "schema": tickets.RESPONSES_SCHEMA,
                    "run": run,
                    "feedback": feedback.name,
                    "responses": [
                        {
                            "comment": asked,
                            "issue": issue,
                            "action": "Answered it.",
                            "reply": reply,
                            "reply_url": None,
                            "reply_fallback": None,
                            "verdict": tickets.Verdict.DOES_NOT_CONFIRM.value,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        unedited, _ = _gathered(bench, run, "--dry-run")

        # The person edits the comment later in the same second the gathering quoted.
        # llmlint: ignore-block[tests_mirror_real_usage] The person's edit, made on github.com,
        # for the reason the comment above states.
        remote.planted_edit(
            _row(issue),
            asked,
            "Does this back off, and for how long?\n",
            _instant(asked_at.replace(microsecond=900000)),
        )
        # llmlint: ignore-end[tests_mirror_real_usage]
        edited, _ = _gathered(bench, run, "--dry-run")
        yield Versioned(
            quoted=str(fields.last_changed),
            snapshot_version=chosen.last_changed,
            unedited=unedited,
            edited=edited,
            comment=asked,
            run=run,
        )


def _line(report: str, comment: str) -> str:
    (line,) = [one for one in report.splitlines() if comment in one]
    return line


def test_the_gathering_quotes_the_comment_at_the_precision_its_board_dates_it(
    versioned: Versioned,
) -> None:
    assert versioned.quoted.endswith(".100000Z"), versioned.quoted
    assert versioned.snapshot_version == versioned.quoted


def test_the_reply_answers_the_comment_as_it_was_quoted(versioned: Versioned) -> None:
    assert "answered by reply" in _line(versioned.unedited, versioned.comment), versioned.unedited


def test_an_edit_later_in_the_quoted_second_is_selected_again(versioned: Versioned) -> None:
    line = _line(versioned.edited, versioned.comment)
    assert line.endswith(f"would go to run {versioned.run}"), versioned.edited
