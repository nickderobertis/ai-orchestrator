"""A follow-up retry carries forward what an earlier pass settled and placed.

Each journey drives the real `just follow-ups` recipe over a generated prior run — three
tickets an earlier pass placed on the board, each with its copy receipt and its draft's
disposition, one of them edited since its copy, and two drafts that pass left unanswered —
with the installed store CLI writing to the loopback `followups` board
(`tests/github_board.py`). The bench is `test_follow_ups_answer_comments_recipe_e2e.py`'s,
imported rather than restated: everything below the recipe is real, and
`tests/e2e/fake_codex.py` stands in for the paid model alone, running the commands the
dispatched task names through the real programs (:data:`AGENT`).

What reached the board is read off the loopback's own record of the requests it served, so
"no write to an unchanged ticket" is a count the board kept rather than a claim of the code
under test. A write to a ticket is both of the ways its card can be written: a mutation naming
the card anywhere in the retry, and the store copy of the ticket — every request the board
served while `copy` ran on it, which the agent's stand-in brackets with the host's monotonic
clock, the one the loopback stamps each request with. A store copy of a ticket the board
already holds as written sends no mutation, but it still reads the card to find that out, and
that read spends the same shared allowance.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import budget_telemetry
import github_board as remote
import pytest
from github_board import _GitHubFixture, _GraphQLRequest
from test_follow_ups_answer_comments_recipe_e2e import (
    BOARD,
    REPOSITORY,
    _python,
    _row,
    _scripted,
    _serving,
)
from test_follow_ups_recipe_e2e import (
    HOST,
    OK,
    REFUSED,
    Bench,
    _bench,
    _draft,
    _draft_id,
    _held_ticket,
    _item,
    _run,
    _ticket,
)

from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.root import REPO_ROOT

pytestmark = [pytest.mark.xdist_group("follow-up-retry")]

PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python3")

#: The tickets the earlier pass placed: two left as they were, one edited since its copy.
UNCHANGED_CAUSES = ("listing-cursor-skips-last-page", "export-drops-a-column")
CHANGED_CAUSE = "sweep-trailer-omits-a-family"
PLACED_CAUSES = (*UNCHANGED_CAUSES, CHANGED_CAUSE)
#: The root causes the retry files for the drafts the earlier pass left unanswered.
NEW_CAUSES = ("retry-loop-never-backs-off", "reclaim-report-counts-twice")
#: What the edit since the copy added to the changed ticket's evidence.
EDITED_EVIDENCE = "Seen again on the nightly sweep after the copy."

#: The heading the scoped re-dispatch's task names its work under.
SCOPE_HEADING = "## The work this re-dispatch is scoped to"

#: The dispatched agent's commands, as its task names them: answer each draft it is to work
#: by writing its ticket and deleting the draft, then ask the board each ticket's status,
#: validate it and copy it; append one disposition per answered draft to the account; and run
#: the account's board-less check. The ticket it writes for a draft is staged by the journey —
#: the model's choice of words — and everything it runs is the real module and store. Scoped,
#: it reads its drafts and tickets from the task's scope section; in full, it copies every
#: ticket of the run and answers the drafts the account does not answer yet.
AGENT = """\
import json, pathlib, re, shutil, subprocess, sys, time

config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
python, run, board = config["python"], config["run"], config["board"]
root = pathlib.Path(config["root"])
module = [python, "-m", "orchestrator.follow_up_tickets"]
prompts = [
    json.loads(line)["prompt"]
    for line in pathlib.Path(config["log"]).read_text(encoding="utf-8").splitlines()
]
task = [prompt for prompt in prompts if f"run `{run}`" in prompt][-1]
account_path = pathlib.Path(config["account"])
account = json.loads(account_path.read_text(encoding="utf-8"))
tickets_directory = root / "tasks" / run / "tickets"
if config["scoped"]:
    scope = task.split(config["heading"], 1)[1].split("\\n## ", 1)[0]
    drafts = re.findall(r"^- draft `([^`]+)`", scope, re.MULTILINE)
    worked = [pathlib.Path(one) for one in re.findall(r"^- ticket `([^`]+)`", scope, re.MULTILINE)]
else:
    answered = {entry["draft"] for entry in account["dispositions"]}
    drafts = [one for one in account["drafts"] if one not in answered]
    worked = sorted(tickets_directory.glob("*.md"))
written = []
for draft in drafts:
    staged = config["staged"][draft]
    target = tickets_directory / f"{staged['cause']}.md"
    shutil.copyfile(staged["file"], target)
    written.append(target)
    (root / "tasks" / run / "drafts" / f"{draft.rsplit('/', 1)[1]}.md").unlink()
    account["dispositions"].append({
        "draft": draft, "disposition": "filed", "root_causes": [staged["cause"]],
        "detail": "Its claim holds at the basis, and its ticket is on the board.",
    })
for ticket in [*worked, *[one for one in written if one not in worked]]:
    word = subprocess.run([*module, "board-status", "--board", board, str(ticket)],
                          check=True, capture_output=True, text=True).stdout.strip()
    text = ticket.read_text(encoding="utf-8")
    ticket.write_text(re.sub(r'^status: .*$', f'status: "{word}"', text, count=1,
                             flags=re.MULTILINE), encoding="utf-8")
    subprocess.run([*module, "validate", str(ticket)], check=True)
    started = time.monotonic()
    subprocess.run([*module, "copy", "--board", board, str(ticket)], check=True)
    with open(config["windows"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"ticket": str(ticket), "start": started,
                                 "end": time.monotonic()}) + "\\n")
account_path.write_text(json.dumps(account, indent=2) + "\\n", encoding="utf-8")
subprocess.run([*module, "check-dispositions", "--root", str(root), run], check=True)
"""


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] These journeys run in the
# `plan-tooling-follow-ups` project's `test` tier, the Nx edge this repository keeps for
# follow-up journeys that launch the installed engine: they drive `just follow-ups`, whose launch
# reads `scripts/`, `config/`, `templates/` and `orchestrator/` — what
# `planToolingFollowUpsWorkspace` covers — so a project of their own would be keyed on the same
# files, and no narrower project holds the loopback board they write to.
@pytest.fixture
def bench(tmp_path: Path) -> Iterator[Bench]:
    """A follow-ups bench whose board is the loopback served as `followups`."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    held = _bench(tmp_path)
    held.environment.update(
        {
            # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
            "FAKE_CODEX_RUN_ON_MARKER": str(tmp_path / "commands.json"),
            "FAKE_CODEX_RUN_ON_MARKER_LOG": str(tmp_path / "commands-ran.jsonl"),
            "FAKE_CODEX_PROMPT_LOG": str(tmp_path / "prompts.jsonl"),
            "UV_NO_SYNC": "1",
        }
    )
    # llmlint: ignore[e2e_not_mocked] Only GitHub Projects is substituted by its loopback API double; the recipe, engine and plan store are real, and no check may write tickets to the live board.  # noqa: E501
    with _serving(held):
        yield held


class Prior(NamedTuple):
    """The run an earlier follow-up pass left: its tickets' cards, account and new drafts."""

    run: str
    cards: dict[str, str]
    account: list[dict[str, object]]
    unanswered: tuple[str, ...]
    staged: dict[str, dict[str, str]]


def _written(bench: Bench, run: str, cause: str, draft: str) -> Path:
    """``run``'s ticket for ``cause``, consuming ``draft``, as the agent writes one."""
    path = tickets.ticket_path(bench.drafts_root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    ticket = _ticket(
        run,
        cause,
        (draft,),
        f"{remote.SIBLING_REPOSITORY.name}: {cause.replace('-', ' ')}",
        f"The {cause.replace('-', ' ')} invariant is broken",
        HOST,
        repository=REPOSITORY,
    )
    path.write_text(tickets.render(ticket), encoding="utf-8")
    return path


def _placed(bench: Bench, path: Path) -> str:
    """Ask the board the ticket's status, validate it and copy it, as the earlier pass did."""
    module = [PYTHON, "-m", "orchestrator.follow_up_tickets"]
    decided = _run([*module, "board-status", "--board", BOARD, str(path)], bench)
    assert decided.returncode == OK, decided.stdout + decided.stderr
    text = path.read_text(encoding="utf-8")
    word = decided.stdout.strip()
    path.write_text(
        re.sub(r"^status: .*$", f'status: "{word}"', text, count=1, flags=re.MULTILINE), "utf-8"
    )
    for step in (["validate", str(path)], ["copy", "--board", BOARD, str(path)]):
        done = _run([*module, *step], bench)
        assert done.returncode == OK, done.stdout + done.stderr
    record = _item(bench, tickets.qualified_id(*tickets.located_path(path)))["metadata"]
    assert isinstance(record, dict), record
    return str(record[plan_store.COPIES_KEY][BOARD])


def _prior(bench: Bench, run: str) -> Prior:
    """A run an earlier pass worked: three placed tickets, one edited since, two drafts open."""
    cards: dict[str, str] = {}
    account: list[dict[str, object]] = []
    consumed = []
    for cause in PLACED_CAUSES:
        draft = f"drafts:{run}/drafts/consumed-{cause}"
        consumed.append(draft)
        cards[cause] = _placed(bench, _written(bench, run, cause, draft))
        account.append(
            {
                "draft": draft,
                "disposition": tickets.Disposition.FILED.value,
                "root_causes": [cause],
                "detail": f"Filed as the ticket for {cause}.",
            }
        )
    # The edit since the copy: one more sentence of evidence, its record and link kept.
    changed = tickets.ticket_path(bench.drafts_root, run, CHANGED_CAUSE)
    held = _held_ticket(bench, changed)
    edited = dataclasses.replace(
        held, body=held.body.replace("Verified on", f"{EDITED_EVIDENCE} Verified on")
    )
    changed.write_text(tickets.render(edited), encoding="utf-8")
    unanswered = []
    staged: dict[str, dict[str, str]] = {}
    for cause in NEW_CAUSES:
        drafted = _draft_id(run, _draft(bench, run, f"The {cause.replace('-', ' ')}"))
        unanswered.append(drafted)
        ticket = _ticket(
            run,
            cause,
            (drafted,),
            f"{remote.SIBLING_REPOSITORY.name}: {cause.replace('-', ' ')}",
            f"The {cause.replace('-', ' ')} invariant is broken",
            HOST,
            repository=REPOSITORY,
        )
        file = bench.tmp / f"staged-{cause}.md"
        file.write_text(tickets.render(ticket), encoding="utf-8")
        staged[drafted] = {"cause": cause, "file": str(file)}
    path = tickets.dispositions_path(bench.drafts_root, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": tickets.DISPOSITIONS_SCHEMA,
                "run": run,
                "drafts": sorted([*consumed, *unanswered]),
                "dispositions": account,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return Prior(run, cards, account, tuple(sorted(unanswered)), staged)


def _agent(bench: Bench, prior: Prior, *, scoped: bool) -> None:
    """Script the dispatched turn of ``prior``'s follow-up run to run :data:`AGENT`."""
    config = bench.tmp / f"agent-{prior.run}.json"
    config.write_text(
        json.dumps(
            {
                "python": PYTHON,
                "run": prior.run,
                "board": BOARD,
                "root": str(bench.drafts_root),
                "log": bench.environment["FAKE_CODEX_PROMPT_LOG"],
                "account": str(tickets.dispositions_path(bench.drafts_root, prior.run)),
                "scoped": scoped,
                "heading": SCOPE_HEADING,
                "staged": prior.staged,
                "windows": str(_windows_path(bench, prior.run)),
            }
        ),
        encoding="utf-8",
    )
    command = _python(bench, "agent.py", AGENT, str(config))
    _scripted(
        bench,
        {
            prior.run: [
                ["bash", "-c", 'cd "$1" && shift && exec "$@"', "_", str(REPO_ROOT), *command]
            ]
        },
    )


def _windows_path(bench: Bench, run: str) -> Path:
    """Where the agent's stand-in records when each of its copies ran."""
    return bench.tmp / f"copy-windows-{run}.jsonl"


def _copy_requests(bench: Bench, run: str, retried: Retried, cause: str) -> list[_GraphQLRequest]:
    """Every request the board served while the retry's `copy` ran on ``cause``'s ticket."""
    path = _windows_path(bench, run)
    windows = (
        [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        if path.is_file()
        else []
    )
    ticket = str(tickets.ticket_path(bench.drafts_root, run, cause))
    return [
        request
        for window in windows
        if window["ticket"] == ticket
        for request in retried.requests
        if window["start"] <= request.received <= window["end"]
    ]


def _writes(
    bench: Bench, prior: Prior, retried: Retried, cause: str, card: str
) -> list[_GraphQLRequest]:
    """Every write to ``cause``'s ticket in the retry: its card's mutations and its copy."""
    mutations = remote.writes_naming(_row(card), retried.requests)
    copied = _copy_requests(bench, prior.run, retried, cause)
    return [*mutations, *(one for one in copied if one not in mutations)]


class Retried(NamedTuple):
    """What the retry's recipe did, and every request the board served while it ran."""

    status: int
    said: str
    requests: list[_GraphQLRequest]


def _retried(bench: Bench, prior: Prior, *arguments: str) -> Retried:
    before = len(_GitHubFixture.requests)
    done = _run(["just", "follow-ups", prior.run, *arguments], bench)
    return Retried(
        done.returncode, done.stdout + done.stderr, list(_GitHubFixture.requests[before:])
    )


def _task(bench: Bench, run: str) -> str:
    """The task the retry's dispatch was handed, as the provider's stand-in received it."""
    log = Path(bench.environment["FAKE_CODEX_PROMPT_LOG"])
    prompts = [json.loads(line)["prompt"] for line in log.read_text("utf-8").splitlines()]
    return [prompt for prompt in prompts if f"run `{run}`" in prompt][-1]


def _account(bench: Bench, run: str) -> list[dict[str, object]]:
    held = json.loads(tickets.dispositions_path(bench.drafts_root, run).read_text("utf-8"))
    entries = held["dispositions"]
    assert isinstance(entries, list), held
    return entries


def _receipts(bench: Bench, run: str) -> list[dict[str, object]]:
    path = tickets.receipts_path(bench.drafts_root, run)
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def _checked_on_board(bench: Bench, run: str) -> None:
    """The completion check over receipts, as the closeout runs it, passes."""
    checked = _run(
        [PYTHON, "-m", "orchestrator.follow_up_tickets", "check-dispositions"]
        + ["--root", str(bench.drafts_root), "--board", BOARD, run],
        bench,
    )
    assert checked.returncode == OK, checked.stdout + checked.stderr


def _filed_cards(bench: Bench, run: str, causes: tuple[str, ...]) -> dict[str, str]:
    """The card each of ``causes``'s tickets reached, by its record's own link."""
    found = {}
    for cause in causes:
        record = _item(bench, tickets.qualified_id(run, cause))["metadata"]
        assert isinstance(record, dict), record
        found[cause] = str(record[plan_store.COPIES_KEY][BOARD])
    return found


def _recorded(
    mode: str, bench: Bench, prior: Prior, retried: Retried, cards: dict[str, str]
) -> None:
    """Each placed ticket's writes in this retry, as uncounted parts of the run's board points."""
    points = sum(remote._request_points(request) for request in retried.requests)
    for cause, card in sorted(cards.items()):
        writes = _writes(bench, prior, retried, cause, card)
        unchanged = cause in UNCHANGED_CAUSES
        budget_telemetry.record(
            remote.RUN_POINTS_BUDGET,
            f"{mode} retry, {'unchanged' if unchanged else 'changed or new'} ticket {cause}",
            sum(remote._request_points(request) for request in writes),
            unit="points",
            counted=False,
            unchanged_ticket_writes=len(writes) if unchanged else 0,
            changed_or_new_ticket_writes=0 if unchanged else len(writes),
            retry_points=points,
            retry_requests=len(retried.requests),
        )


def _common(bench: Bench, prior: Prior, retried: Retried) -> dict[str, str]:
    """What both retries owe: carried dispositions, filed drafts, the edit on its card."""
    entries = _account(bench, prior.run)
    for carried in prior.account:
        assert entries.count(carried) == 1, (carried, entries)
    filed = {str(entry["draft"]): entry for entry in entries}
    assert sorted(filed) == sorted(
        [*(str(one["draft"]) for one in prior.account), *prior.unanswered]
    )
    for draft in prior.unanswered:
        assert filed[draft]["disposition"] == tickets.Disposition.FILED.value, filed[draft]
    new_cards = _filed_cards(bench, prior.run, NEW_CAUSES)
    for card in new_cards.values():
        assert _row(card).body, card
    assert EDITED_EVIDENCE in _row(prior.cards[CHANGED_CAUSE]).body, retried.said
    assert _writes(bench, prior, retried, CHANGED_CAUSE, prior.cards[CHANGED_CAUSE])
    unchanged = {
        cause: _writes(bench, prior, retried, cause, prior.cards[cause])
        for cause in UNCHANGED_CAUSES
    }
    assert {cause: len(writes) for cause, writes in unchanged.items()} == dict.fromkeys(
        UNCHANGED_CAUSES, 0
    ), {cause: [one.operation for one in writes] for cause, writes in unchanged.items()}
    _checked_on_board(bench, prior.run)
    return new_cards


def test_an_unanswered_retry_works_only_the_open_drafts_and_the_changed_ticket(
    bench: Bench,
) -> None:
    """`--unanswered` names only the open drafts and the edited ticket, and writes nothing else.

    The task's scope section is the dispatch's whole work list; every prior disposition
    stands in the final account, both open drafts are filed onto new cards, the edited ticket's
    card carries the edit, and the board was sent no write naming either unchanged ticket.
    """
    prior = _prior(bench, f"retry-scoped-{os.getpid()}")
    _agent(bench, prior, scoped=True)

    retried = _retried(bench, prior, "--unanswered")

    assert retried.status == OK, retried.said
    scope = _task(bench, prior.run).split(SCOPE_HEADING, 1)[1].split("\n## ", 1)[0]
    assert sorted(re.findall(r"^- draft `([^`]+)`", scope, re.MULTILINE)) == list(prior.unanswered)
    changed = str(tickets.ticket_path(bench.drafts_root, prior.run, CHANGED_CAUSE))
    assert re.findall(r"^- ticket `([^`]+)`", scope, re.MULTILINE) == [changed]
    new_cards = _common(bench, prior, retried)
    _recorded("scoped", bench, prior, retried, {**prior.cards, **new_cards})


def test_a_full_re_dispatch_skips_every_ticket_unchanged_since_its_copy(bench: Bench) -> None:
    """A bare `--feedback` re-dispatch copies every ticket, and the copy skips the unchanged.

    The agent runs `copy` on every ticket of the run, as the full re-dispatch's task says; the
    board is sent no write naming either unchanged ticket, and each of their last receipts
    says it was skipped as unchanged, naming the receipt of the copy it stands on.
    """
    prior = _prior(bench, f"retry-full-{os.getpid()}")
    _agent(bench, prior, scoped=False)
    feedback = bench.tmp / "feedback.md"
    feedback.write_text("File the two drafts the last pass never reached.\n", encoding="utf-8")

    retried = _retried(bench, prior, "--feedback", str(feedback))

    assert retried.status == OK, retried.said
    new_cards = _common(bench, prior, retried)
    receipts = _receipts(bench, prior.run)
    for cause in UNCHANGED_CAUSES:
        identifier = tickets.qualified_id(prior.run, cause)
        copies = [
            (at, one)
            for at, one in enumerate(receipts, start=1)
            if one["command"] == "copy" and one["ticket"] == identifier
        ]
        (first_at, first), (_last_at, last) = copies[0], copies[-1]
        assert len(copies) == 2, copies
        assert last["outcome"]["action"] == "skipped-unchanged", last
        assert last["outcome"]["unchanged_since"] == first_at, (last, first_at)
        assert last["card"] == first["card"] == prior.cards[cause]
        assert last["outcome"]["digest"] == first["outcome"]["digest"]
    _recorded("full", bench, prior, retried, {**prior.cards, **new_cards})


def test_unanswered_on_a_run_with_no_prior_account_is_refused_naming_the_full_form(
    bench: Bench,
) -> None:
    """With no account an earlier pass left, there is nothing to carry: the full form is named."""
    run = f"retry-unaccounted-{os.getpid()}"
    _draft(bench, run, "The listing cursor skips the last page")

    refused = _run(["just", "follow-ups", run, "--unanswered"], bench)

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert f"'just follow-ups {run}'" in refused.stderr, refused.stderr
    assert not tickets.dispositions_path(bench.drafts_root, run).exists()
    assert not bench.runs.is_dir() or not any(bench.runs.glob(f"{run}*")), "a run was launched"


def test_unanswered_beside_comments_is_refused_naming_the_comment_answering_recipe(
    bench: Bench,
) -> None:
    """A comment gathering answers comments, never drafts, so the two flags are refused together."""
    run = f"retry-comments-{os.getpid()}"
    feedback = bench.tmp / "gathering.md"
    feedback.write_text("A gathering.\n", encoding="utf-8")

    refused = _run(
        ["just", "follow-ups", run, "--unanswered", "--comments", "--feedback", str(feedback)],
        bench,
    )

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert "just follow-ups-answer-comments" in refused.stderr, refused.stderr
    assert f"'just follow-ups {run} --unanswered'" in refused.stderr, refused.stderr


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
