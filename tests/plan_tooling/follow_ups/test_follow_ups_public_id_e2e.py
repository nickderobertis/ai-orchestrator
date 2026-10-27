"""A run whose id names a private repository is followed up under its public id alone.

The engine names every run by a public id — `onepipeline status <run> --json`'s `public_id`,
the run id itself unless that names a private repository, and otherwise `run-` and twelve hex
digits — and `just follow-ups` keys every follow-up record of the run by it, so nothing the
flow puts on a public board carries the private name. These journeys drive the real recipe over
two recorded runs the installed engine reads, one named after the sandbox's rule-declared
private repository (`github.com/hiddenco/quietharbor`) and one whose name is public, on the
loopback GitHub served as `followups` with the store's boundary switched on in the bench's own
configuration home (`tests/follow_up_boundary.py`).

**What is doubled.** `tests/e2e/fake_codex.py` stands in for the paid model, running the
commands this module scripts for the follow-up agent's turn through the real programs: the
ticket written under the run the task names, `board-status` and `copy` onto the board, the
evidence comment on another run's issue added or edited by its own marker, and the account.
Each run is launched through the real `just orchestrate`, its one human node attested over
its channel, so the engine itself decides the `public_id` the recipe acts on. The recipes,
the engine, the store, the board and `onevcs` are real.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import boundary_home
import follow_up_boundary
import github_board as remote
import onevcs_state_snapshot
import pytest
from published_tools import ONETASKGRAPH_BIN
from test_follow_ups_answer_comments_recipe_e2e import (
    _commented,
    _filed,
    _serving,
)
from test_follow_ups_recipe_e2e import (
    GATE,
    HOST,
    OK,
    SUFFIX,
    Bench,
    _bench,
    _comments,
    _draft,
    _draft_id,
    _edited,
    _from_checkout,
    _item,
    _left_in,
    _plan_task,
    _prompts,
    _ran,
    _run,
    _script,
    _staged,
    _ticket,
)

from orchestrator import follow_up_drafts as drafts
from orchestrator import follow_up_tickets as tickets
from orchestrator.root import REPO_ROOT
from orchestrator.run_reading import RunId

BOARD = tickets.BOARD
REPOSITORY = f"github.com/{remote.SIBLING_REPOSITORY}"

#: The sandbox's private repository, which the bench's state root registers private by rule.
PRIVATE_REPOSITORY = "github.com/hiddenco/quietharbor"

#: The root causes the two runs' tickets are filed under, and the other run's shared one.
OWN_CAUSE = "the-session-token-is-never-rotated"
SHARED_CAUSE = "the-login-page-retries-without-backoff"

#: The command the agent posts or edits its evidence comment with: it lists the issue's
#: comments, keeps the one whose marker names this run and cause, and edits it, or adds one.
COMMENT_PROGRAM = """\
import json, pathlib, subprocess, sys

store, issue, run, cause, body = sys.argv[1:6]
listed = json.loads(subprocess.run(
    [store, "task", "comment", "list", issue, "--json"], check=True, capture_output=True, text=True
).stdout)["comments"]
from orchestrator import follow_up_tickets as tickets
mine = [one for one in listed
        if tickets.comment_owner(one["body"]) == tickets.CommentOwner(tickets.RunId(run),
                                                                      tickets.RootCause(cause))]
path = pathlib.Path(body)
if mine:
    command = [store, "task", "comment", "edit", issue, mine[0]["id"], "--body-file", str(path)]
else:
    command = [store, "task", "comment", "add", issue, "--body-file", str(path)]
subprocess.run(command + ["--json"], check=True)
"""

#: What the agent writes its account with: the dispositions, under the run the task names.
ACCOUNT_PROGRAM = """\
import json, pathlib, sys

path = pathlib.Path(sys.argv[1])
held = json.loads(path.read_text(encoding="utf-8"))
held["dispositions"] = json.loads(sys.argv[2])
path.write_text(json.dumps(held, indent=2), encoding="utf-8")
"""


def _recorded_run(bench: Bench, run: str) -> None:
    """``run`` launched through the real `just orchestrate` and settled through its channel.

    Its one node is a human action, so nothing is dispatched; attesting it settles the run
    `done`. The engine decides the run's public id at that launch, from the run id and the
    private identities the bench's state root registers.
    """
    _left_in(bench, RunId(run), [{"id": GATE, "kind": "human", "task": _plan_task("Approve.")}])
    _edited(bench, RunId(run), {"op": "attest", "ref": GATE})


def _engine_public_id(bench: Bench, run: str) -> str:
    """The public id the installed engine names ``run`` by: the journey's oracle."""
    read = _run(
        [str(REPO_ROOT / "node_modules" / ".bin" / "onepipeline"), "status", run, "--json"], bench
    )
    assert read.returncode == 0, read.stdout + read.stderr
    reading = json.loads(read.stdout)
    assert reading["word"] == "SETTLED", reading
    return str(reading["public_id"])


def _turn(bench: Bench, key: str, draft: Path | None, other_issue: str) -> list[list[str]]:
    """The scripted agent's commands for one pass over the run the task names ``key``."""
    python = str(REPO_ROOT / ".venv" / "bin" / "python3")
    ticket = tickets.ticket_path(bench.drafts_root, key, OWN_CAUSE)
    shared = tickets.ticket_path(bench.drafts_root, key, SHARED_CAUSE)
    drafted = (_draft_id(key, draft),) if draft is not None else ()
    if draft is not None:
        written = _ticket(
            key,
            OWN_CAUSE,
            drafted,
            f"{remote.SIBLING_REPOSITORY.name}: the session token is never rotated",
            "Verified",
            HOST,
            repository=REPOSITORY,
        )
        local = _ticket(
            key,
            SHARED_CAUSE,
            drafted,
            f"{remote.SIBLING_REPOSITORY.name}: the login page retries without backoff",
            "Verified again",
            HOST,
            repository=REPOSITORY,
        )
        ticket.parent.mkdir(parents=True, exist_ok=True)
        staged_own = _staged(bench, f"{key}-own.md", tickets.render(written))
        staged_shared = _staged(bench, f"{key}-shared.md", tickets.render(local))
    comment = _staged(
        bench,
        f"{key}-comment.md",
        tickets.render_comment(key, SHARED_CAUSE, f"Seen again in follow-up run `{key}`."),
    )
    helper = _staged(bench, "comment.py", COMMENT_PROGRAM)
    account = _staged(bench, "account.py", ACCOUNT_PROGRAM)
    module = f"{shlex.quote(python)} -m orchestrator.follow_up_tickets"
    lines = ["set -euo pipefail", f"cd {shlex.quote(str(REPO_ROOT))}"]
    if draft is not None:
        lines += [
            f"cp {shlex.quote(str(staged_own))} {shlex.quote(str(ticket))}",
            f"cp {shlex.quote(str(staged_shared))} {shlex.quote(str(shared))}",
        ]
    lines += [
        f"word=$({module} board-status --board {BOARD} {shlex.quote(str(ticket))})",
        f'sed -i "s/^status: .*/status: \\"$word\\"/" {shlex.quote(str(ticket))}',
        f"{module} validate {shlex.quote(str(ticket))}",
        f"{module} copy --board {BOARD} {shlex.quote(str(ticket))}",
        f"{module} board-status --board {BOARD} {shlex.quote(str(shared))} > /dev/null",
        f"{module} validate {shlex.quote(str(shared))}",
        shlex.join(
            [python, str(helper), str(ONETASKGRAPH_BIN), other_issue, key, SHARED_CAUSE]
            + [str(comment)]
        ),
        f"{module} re-estimate --board {BOARD} {shlex.quote(other_issue)}",
    ]
    if draft is not None:
        entries = [
            {
                "draft": _draft_id(key, draft),
                "disposition": tickets.Disposition.FILED.value,
                "root_causes": [OWN_CAUSE, SHARED_CAUSE],
                "detail": f"{BOARD} issue for {OWN_CAUSE}; evidence on {other_issue}",
            }
        ]
        lines += [
            shlex.join(
                [python, str(account), str(tickets.dispositions_path(bench.drafts_root, key))]
                + [json.dumps(entries)]
            ),
            f"rm {shlex.quote(str(draft))}",
        ]
    return [_from_checkout("bash", "-c", "\n".join(lines))]


class Pass(NamedTuple):
    """One `just follow-ups` over a run, and what its turn was handed."""

    result: subprocess.CompletedProcess[str]
    prompts: list[str]
    follow_up_run: str


def _pass(  # noqa: PLR0913 - each is one thing a pass is named and scripted by
    bench: Bench,
    run: str,
    key: str,
    draft: Path | None,
    other: str,
    name: str,
    *,
    named: str | None = None,
) -> Pass:
    """One `just follow-ups` over ``run``, named on its command line as ``named`` — the run id
    unless a pass names the run by the public id a board holds."""
    log = bench.tmp / f"prompts-{name}.jsonl"
    _script(bench, f"Verify the follow-up drafts run `{key}`", _turn(bench, key, draft, other))
    result = _run(
        ["just", "follow-ups", named or run, "--to", BOARD],
        bench,
        environment=bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(log)},
    )
    launched = sorted(path.name for path in bench.runs.glob(f"{run}{SUFFIX}*"))
    return Pass(result, _prompts(log), launched[-1] if launched else "")


class Board(NamedTuple):
    """Every issue the loopback holds, and every comment on each, as the board serves them."""

    issues: dict[str, str]
    comments: dict[str, dict[str, str]]


def _board() -> Board:
    issues = {f"{row.content_id}": f"{row.title}\n{row.body}" for row in remote.BOARD.issues}
    comments = {
        f"{row.content_id}": {str(one["id"]): str(one["body"]) for one in row.comments}
        for row in remote.BOARD.issues
    }
    return Board(issues, comments)


def _passes_public_boundary(bench: Bench, text: str) -> bool:
    checked = subprocess.run(  # noqa: S603 - the pinned onevcs, under the bench's state root
        [str(boundary_home.ONEVCS), "boundary", "check", "--destination", "public"]
        + ["--input", "-"],
        input=json.dumps({"destination": "public", "text": [text]}),
        env=bench.environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert checked.returncode in (0, 1), checked.stdout + checked.stderr
    return checked.returncode == 0


class Followed(NamedTuple):
    """Both runs followed up twice, and a person's comment routed, read back before teardown."""

    bench: Bench
    private_run: str
    public_id: str
    public_run: str
    public_key: str
    first: Pass
    board_after_first: Board
    second: Pass
    board_after_second: Board
    equal_first: Pass
    other_issue: str
    own_issue: str
    routed: subprocess.CompletedProcess[str]
    routed_snapshot: dict[str, object]
    records: dict[str, str]
    #: `just follow-ups` over a public id whose recorded run the engine names otherwise, and the
    #: follow-up runs before and after it.
    forged: subprocess.CompletedProcess[str]
    launched_around_forged: tuple[list[str], list[str]]


def _records(root: Path) -> dict[str, str]:
    """Every file under the drafts root, by its path relative to it, and what it holds."""
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling-follow-ups`
# is the Nx project edge this repository keeps for follow-up journeys that launch the installed
# engine; the fixture spends three launches whose one turn is the provider's stand-in.
@pytest.fixture(scope="module")
def followed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Followed]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("follow-ups-public-id")
    bench = _bench(tmp)
    # llmlint: ignore-block[e2e_not_mocked] Only the paid provider process is substituted, as
    # this repository's test rule allows: its stand-in runs the scripted agent commands through
    # the real programs, and the recipe, the engine, the store and the board it reaches are real.
    bench.environment["FAKE_CODEX_RUN_ON_MARKER"] = str(tmp / "commands.json")
    bench.environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(tmp / "commands-ran.jsonl")
    # llmlint: ignore-end[e2e_not_mocked]
    pid = os.getpid()
    private_run = f"fix-quietharbor-login-{pid}"
    public_run = f"fix-session-login-{pid}"
    with _serving(bench):
        # The state root registers the sandbox's private repository private by rule, beside
        # every public identity the follow-up fixtures name, so its name is a boundary term.
        home = boundary_home.registered_home(
            tmp / "state",
            public=follow_up_boundary.PUBLIC_IDENTITIES,
            private=[PRIVATE_REPOSITORY],
        )
        bench.environment[onevcs_state_snapshot.ONEVCS_HOME] = str(home)
        # The local sources a host declares private beside the boards it declares public, so
        # the follow-up launch's own project, which names the run, is screened as the host's is.
        # Not `drafts`: the store keeps no `onetaskgraph.origin` on a copy out of a source
        # declared private onto a public board, and a ticket's binding is read by it.
        # A source declared private is built on every dependency walk, so its root is made.
        (tmp / "authoring").mkdir(exist_ok=True)
        for source in ("FOLLOW_UP_RUNS", "AUTHORING"):
            bench.environment[f"ONETASKGRAPH_SOURCES__{source}__VISIBILITY"] = "private"
        _recorded_run(bench, private_run)
        _recorded_run(bench, public_run)
        public_id = _engine_public_id(bench, private_run)
        public_key = _engine_public_id(bench, public_run)

        other = f"fu-earlier-{pid}"
        other_issue = str(_filed(bench, other, SHARED_CAUSE))

        draft = _draft(bench, private_run, "The session token is never rotated")
        first = _pass(bench, private_run, public_id, draft, other_issue, "first")
        assert first.result.returncode == OK, (
            first.result.stdout + first.result.stderr + _ran(bench)
        )
        board_after_first = _board()
        # Named by the public id a board holds, as a comment's routing names it.
        second = _pass(bench, private_run, public_id, None, other_issue, "second", named=public_id)
        board_after_second = _board()

        equal_draft = _draft(bench, public_run, "The session token is never rotated")
        equal_first = _pass(bench, public_run, public_key, equal_draft, other_issue, "equal")

        # A public id whose draft project records the private run, though the engine names that
        # run otherwise: the recipe refuses it rather than follow the run up under it.
        forged_key = "run-000000000000"
        drafts.record_engine_run(bench.drafts_root, forged_key, private_run)
        launched_before = sorted(path.name for path in bench.runs.glob(f"{private_run}{SUFFIX}*"))
        forged = _run(["just", "follow-ups", forged_key, "--to", BOARD], bench)
        launched_after = sorted(path.name for path in bench.runs.glob(f"{private_run}{SUFFIX}*"))

        own_issue = f"{BOARD}:{_record(bench, public_id)[tickets.BINDING_FIELD]}"
        _commented(bench, own_issue, "Does this also hit the admin login?", "a-person")
        routed = _run(["just", "follow-ups-answer-comments", "--dry-run"], bench)
        named = [
            line.removeprefix("snapshot: ")
            for line in routed.stdout.splitlines()
            if line.startswith("snapshot: ")
        ]
        snapshot = (
            json.loads((Path(named[0]) / "snapshot.json").read_text(encoding="utf-8"))
            if named
            else {}
        )
        yield Followed(
            bench=bench,
            private_run=private_run,
            public_id=public_id,
            public_run=public_run,
            public_key=public_key,
            first=first,
            board_after_first=board_after_first,
            second=second,
            board_after_second=board_after_second,
            equal_first=equal_first,
            other_issue=other_issue,
            own_issue=own_issue,
            routed=routed,
            routed_snapshot=snapshot,
            records=_records(bench.drafts_root),
            forged=forged,
            launched_around_forged=(launched_before, launched_after),
        )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def test_the_engine_names_the_private_run_by_a_hashed_id_and_the_public_run_by_its_own(
    followed: Followed,
) -> None:
    assert followed.public_id.startswith("run-") and len(followed.public_id) == 16
    assert followed.public_id != followed.private_run
    assert followed.public_key == followed.public_run


def test_each_pass_settles_and_hands_the_agent_the_public_id_and_never_the_run_id(
    followed: Followed,
) -> None:
    for each in (followed.first, followed.second, followed.equal_first):
        assert each.result.returncode == OK, each.result.stdout + each.result.stderr
    for each in (followed.first, followed.second):
        assert each.follow_up_run.startswith(followed.private_run), each.follow_up_run
        (prompt,) = each.prompts
        assert followed.public_id in prompt
        assert f"`{followed.private_run}`" not in prompt
        assert f"/{followed.private_run}/" not in prompt


def test_every_ticket_record_qualified_id_and_marker_names_the_public_id(
    followed: Followed,
) -> None:
    ticket_records = {path: text for path, text in followed.records.items() if "/tickets/" in path}
    assert {path.split("/")[1] for path in ticket_records} >= {followed.public_id}
    for path, text in ticket_records.items():
        if path.split("/")[1] != followed.public_id:
            continue
        assert followed.private_run not in path, path
        assert followed.private_run not in text, path
    record = _record(followed.bench, followed.public_id)
    assert record["created_by_run"] == followed.public_id
    assert record["owning_runs"] == [followed.public_id]
    held_drafts = record["drafts"]
    assert isinstance(held_drafts, list) and held_drafts, record
    assert all(str(one).startswith(f"drafts:{followed.public_id}/") for one in held_drafts)
    # The engine's own id is kept beside the records only where a reader needs it to reach the
    # run: the draft project the comment routing reads, in the private `drafts` source.
    held = [
        path
        for path, text in followed.records.items()
        if followed.private_run in text and path.split("/")[0] in ("tasks", "dispositions")
    ]
    assert held == [], held
    assert drafts.engine_run(followed.bench.drafts_root, followed.public_id) == (
        followed.private_run
    )


def test_every_item_and_comment_the_board_receives_names_the_public_id_and_passes(
    followed: Followed,
) -> None:
    board = followed.board_after_second
    texts = [*board.issues.values()] + [
        body for comments in board.comments.values() for body in comments.values()
    ]
    assert any(followed.public_id in text for text in texts)
    for text in texts:
        assert followed.private_run not in text, text
        assert _passes_public_boundary(followed.bench, text), text
    # The name is a term the boundary refuses, so passing is the public id's doing.
    assert not _passes_public_boundary(followed.bench, f"run {followed.private_run}")
    other = board.comments[followed.other_issue.partition(":")[2]]
    owners = {tickets.comment_owner(body) for body in other.values()}
    assert (
        tickets.CommentOwner(tickets.RunId(followed.public_id), tickets.RootCause(SHARED_CAUSE))
        in owners
    )


def test_a_second_pass_recognizes_its_own_issue_and_comment_and_creates_neither_again(
    followed: Followed,
) -> None:
    before, after = followed.board_after_first, followed.board_after_second
    assert sorted(after.issues) == sorted(before.issues)
    for issue, comments in before.comments.items():
        assert sorted(after.comments[issue]) == sorted(comments), issue


def test_the_comment_answering_recipe_routes_a_comment_on_the_runs_issue_to_its_public_id(
    followed: Followed,
) -> None:
    assert followed.routed.returncode == 0, followed.routed.stdout + followed.routed.stderr
    selection = followed.routed_snapshot.get("selection")
    assert isinstance(selection, dict), followed.routed_snapshot
    assert followed.public_id in selection, selection
    assert followed.private_run not in json.dumps(followed.routed_snapshot)
    # The public id a board holds is the run the recipe follows up: its draft project says so.
    resolved = _run(
        [str(REPO_ROOT / ".venv" / "bin" / "python3"), "-m", "orchestrator.follow_up_tickets"]
        + ["engine-run", "--root", str(followed.bench.drafts_root), followed.public_id],
        followed.bench,
    )
    assert resolved.stdout.strip() == followed.private_run, resolved.stderr


def test_a_run_named_by_its_own_id_writes_exactly_what_it_always_wrote(
    followed: Followed,
) -> None:
    key = followed.public_run
    records = {
        path: text for path, text in followed.records.items() if path.split("/")[1:2] == [key]
    }
    assert records, sorted(followed.records)
    project = followed.records[f"projects/{key}.md"]
    assert project == drafts.render_project(key)
    assert drafts.engine_run(followed.bench.drafts_root, key) is None
    ticket_text = followed.records[f"tasks/{key}/tickets/{OWN_CAUSE}.md"]
    assert drafts.ENGINE_RUN_KEY not in ticket_text
    record = _record(followed.bench, key)
    assert record["created_by_run"] == key
    assert record["owning_runs"] == [key]
    marked = [
        body
        for body in _comments_of(followed.bench, followed.other_issue)
        if body.endswith(tickets.comment_marker(key, SHARED_CAUSE) + "\n")
    ]
    assert len(marked) == 1


def _record(bench: Bench, key: str) -> dict[str, object]:
    """The ticket record the board item of ``key``'s own ticket carries."""
    metadata = _item(bench, tickets.qualified_id(key, OWN_CAUSE))["metadata"]
    assert isinstance(metadata, dict), metadata
    record = metadata[tickets.KEY]
    assert isinstance(record, dict), metadata
    return record


def _comments_of(bench: Bench, issue: str) -> list[str]:
    return [str(one["body"]) for one in _comments(bench, issue)]


def test_a_public_id_the_engine_names_another_run_by_is_refused_and_launches_nothing(
    followed: Followed,
) -> None:
    forged = followed.forged
    assert forged.returncode != OK, forged.stdout + forged.stderr
    said = forged.stdout + forged.stderr
    assert f"which the engine names '{followed.public_id}'" in said, said
    before, after = followed.launched_around_forged
    assert after == before
