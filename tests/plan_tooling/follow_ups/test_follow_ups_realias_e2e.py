"""A run whose follow-ups were keyed by its private run id is realiased onto its public id.

Before the engine named every run by a public id, a run whose id names a private repository
had its follow-ups filed under that id: its drafts, its tickets, their records and prose, the
origin the board item records, the evidence comments' markers, and those of its later
follow-up runs, `<run id>-follow-ups-2` and `-3`, which write under the original id.
`python -m orchestrator.follow_up_tickets realias <run-id>` rewrites them all in place. This
journey seeds that shape across the loopback `followups` board (`tests/github_board.py`), the
loopback Linear (`tests/linear_board.py`) a host's user layer routes `github.com/hiddenco/*`
to as `hiddenco-followups`, and the drafts root — one issue and one evidence comment of the
run on the board alone, which only the store's metadata query and the run's comment marker
find — beside an unrelated run's issue and comment, drives the verb twice, and then the
recipes: `check-dispositions --board`, a `just follow-ups` pass of the run, and
`just follow-ups-answer-comments`.

**What is doubled.** The paid model, by `tests/e2e/fake_codex.py` running the commands this
module scripts for the follow-up agent's turn; GitHub's Projects API and Linear's GraphQL API,
by their loopback wires, because no test may reach the live board or a Linear workspace. The
run is launched through the real `just orchestrate` and its one human node attested, so the
public id the verb acts on is the one the engine decided at that launch. The incident's writes
are seeded with the store's public boundary off, as it was then; the verb and everything after
it run with it switched on in the journey's own configuration home. The verb, the recipes,
the engine, the store and its plugins, and `onevcs` are real.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict

import boundary_home
import follow_up_boundary
import follow_up_variables
import github_board as remote
import onetaskgraph_user_layer
import onevcs_state_snapshot
import pytest
from github_board import _GitHubFixture, _Operation
from linear_board import LinearBoard, linear_board
from published_tools import ONETASKGRAPH_BIN
from test_follow_ups_answer_comments_recipe_e2e import _commented
from test_follow_ups_public_id_e2e import (
    _engine_public_id,
    _passes_public_boundary,
    _recorded_run,
)
from test_follow_ups_recipe_e2e import (
    HOST,
    OK,
    Bench,
    _bench,
    _draft,
    _draft_id,
    _from_checkout,
    _ran,
    _run,
    _script,
    _staged,
    _ticket,
)

from orchestrator import follow_up_comments as comments_module
from orchestrator import follow_up_drafts as drafts
from orchestrator import follow_up_tickets as tickets
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.xdist_group("follow-ups-realias")

BOARD = tickets.BOARD
LINEAR = onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS
REPOSITORY = f"github.com/{remote.SIBLING_REPOSITORY}"
#: The sandbox's private repository, registered private by rule, which the run id names.
PRIVATE_REPOSITORY = "github.com/hiddenco/quietharbor"

PID = os.getpid()
RUN = f"fix-quietharbor-login-{PID}"
SECOND = f"{RUN}-follow-ups-2"
THIRD = f"{RUN}-follow-ups-3"
OTHER = f"fu-earlier-{PID}"
UNRELATED = f"fu-unrelated-{PID}"

#: The run's root causes: one the run filed, one each of its later follow-up runs filed — the
#: third on the private board — one present on the board alone, and the other run's.
OWN_CAUSE = "the-session-token-is-never-rotated"
SECOND_CAUSE = "the-login-form-forgets-the-user"
PRIVATE_CAUSE = "the-intake-sync-drops-a-field"
BOARD_ONLY_CAUSE = "the-logout-link-is-hidden"
SHARED_CAUSE = "the-login-page-retries-without-backoff"
UNRELATED_CAUSE = "the-profile-page-loads-twice"

PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python3")
MODULE = [PYTHON, "-m", "orchestrator.follow_up_tickets"]
COMMENTS_MODULE = [PYTHON, "-m", "orchestrator.follow_up_comments"]

#: The command the agent posts or edits its evidence comment with, as the public-id journey's.
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


def _layer(tmp: Path, name: str) -> Path:
    """A configuration home whose plan-store user layer routes hiddenco's work to Linear."""
    return onetaskgraph_user_layer.installed(
        tmp / name, onetaskgraph_user_layer.without_private_stores()
    )


def _store(bench: Bench, *arguments: str) -> dict[str, object]:
    done = _run([str(ONETASKGRAPH_BIN), *arguments, "--json"], bench)
    assert done.returncode == 0, done.stdout + done.stderr
    answered: dict[str, object] = json.loads(done.stdout)
    return answered


def _module(bench: Bench, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _run([*MODULE, *arguments], bench)


def _filed(  # noqa: PLR0913 - every part of one ticket the run filed
    bench: Bench,
    cause: str,
    drafted: tuple[str, ...],
    title: str,
    prose: str,
    repository: str = REPOSITORY,
) -> str:
    """The run's ticket for ``cause``, written under its run id and copied, as it was; its item."""
    path = tickets.ticket_path(bench.drafts_root, RUN, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    ticket = _ticket(RUN, cause, drafted, title, prose, HOST, repository=repository)
    path.write_text(tickets.render(ticket), encoding="utf-8")
    for verb in ("board-status", "copy"):
        done = _module(bench, verb, "--board", BOARD, str(path))
        assert done.returncode == 0, done.stdout + done.stderr
    # Where the store placed it: a routed ticket's private board records no link back.
    placed = _module(
        bench,
        "board-items",
        "--board",
        BOARD,
        "--ticket",
        str(path),
        "--origin",
        tickets.qualified_id(RUN, cause),
    )
    assert placed.returncode == 0, placed.stdout + placed.stderr
    (item,) = json.loads(placed.stdout)["items"]
    return str(item["id"])


def _other_issue(bench: Bench, run: str, cause: str) -> str:
    """Another run's ticket, filed and bound the way that run filed it; its item."""
    path = tickets.ticket_path(bench.drafts_root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    ticket = _ticket(
        run,
        cause,
        (f"drafts:{run}/drafts/a-consumed-draft",),
        f"{remote.SIBLING_REPOSITORY.name}: {cause.replace('-', ' ')}",
        "Verified",
        HOST,
        repository=REPOSITORY,
    )
    path.write_text(tickets.render(ticket), encoding="utf-8")
    for verb in ("board-status", "copy"):
        done = _module(bench, verb, "--board", BOARD, str(path))
        assert done.returncode == 0, done.stdout + done.stderr
    return f"{BOARD}:{tickets.read_ticket(path).link(BOARD)}"


def _evidence(bench: Bench, issue: str, run: str, cause: str, evidence: str) -> str:
    """An evidence comment ``run``'s marker names, added through the store's own verb; its id."""
    body = _staged(bench, f"evidence-{issue.replace(':', '-')}-{cause}.md", "")
    body.write_text(tickets.render_comment(run, cause, evidence), encoding="utf-8")
    added = _store(bench, "task", "comment", "add", issue, "--body-file", str(body))
    return str(added["id"])


class HeldItem(TypedDict):
    """The text and comments read back from either board."""

    title: str
    body: str
    comments: dict[str, str]
    origin: NotRequired[str | None]
    host: NotRequired[str | None]


class Held(NamedTuple):
    """Everything both boards hold, as each serves it, read whole for comparison."""

    github: dict[str, HeldItem]
    linear: dict[str, HeldItem]


def _held(linear: LinearBoard) -> Held:
    github: dict[str, HeldItem] = {
        str(row.content_id): {
            "title": row.title,
            "body": row.body,
            "origin": row.origin,
            "host": row.host,
            "comments": {str(one["id"]): str(one["body"]) for one in row.comments},
        }
        for row in remote.BOARD.issues
    }
    lined: dict[str, HeldItem] = {
        str(issue["id"]): {
            "title": issue["title"],
            "body": issue["description"],
            "comments": {str(one["id"]): str(one["body"]) for one in issue["comments"]["nodes"]},
        }
        for issue in linear.issues()
    }
    return Held(github, lined)


def _writes(linear: LinearBoard) -> tuple[int, int]:
    """How many writes each board has been sent: GitHub's mutations, Linear's mutations."""
    github = sum(1 for request in _GitHubFixture.requests if request.is_mutation)
    lined = sum(
        1
        for request in linear.requests()
        if str(request["operation"]).endswith(("Create", "Update", "Delete"))
    )
    return github, lined


class Realiased(NamedTuple):
    """The seeded shape, both invocations, and every recipe after them, read before teardown."""

    bench: Bench
    public: str
    items: dict[str, str]
    comments: dict[str, tuple[str, str]]
    unrelated_issue: str
    unrelated_comment: str
    before: Held
    created_before: int
    first: subprocess.CompletedProcess[str]
    after: Held
    created_after: int
    inventory: str
    second: subprocess.CompletedProcess[str]
    writes_around_second: tuple[tuple[int, int], tuple[int, int]]
    inventory_again: str
    records: dict[str, str]
    board_check: subprocess.CompletedProcess[str]
    followed: subprocess.CompletedProcess[str]
    after_pass: Held
    created_after_pass: int
    routed: subprocess.CompletedProcess[str]
    routed_snapshot: dict[str, object]
    #: The response account a gathering left the run before realiasing, as check-responses
    #: read it after, from where realiasing moved it.
    responses_checked: subprocess.CompletedProcess[str]
    moved_feedback: Path
    #: The invocation the board refused a comment edit partway through, and the inventory it
    #: left, before the invocation that completed.
    refused: subprocess.CompletedProcess[str]
    refused_inventory: str
    created_after_refused: int
    #: The invocation whose comment edit the board answered with a body other than it sent,
    #: before the invocation that completed.
    rewritten: subprocess.CompletedProcess[str]
    #: The invocation whose comment edit the board lost the answer to, so no part landed.
    unlanded: subprocess.CompletedProcess[str]
    #: The run's ticket records once an invocation whose board read was refused had moved
    #: them, and once an invocation whose copy of an item the board refused had stopped.
    moved_tickets: dict[str, str]
    copy_refused: subprocess.CompletedProcess[str]
    tickets_after_copy_refused: dict[str, str]
    created_after_copy_refused: int


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling-follow-ups`
# is the Nx project edge this repository keeps for follow-up journeys that launch the installed
# engine; the fixture spends one launch whose one turn is the provider's stand-in.
# llmlint: ignore-block[e2e_not_mocked] Three boundaries are doubled and no more: the paid
# provider process; GitHub's Projects API, the loopback board, because the live board is shared
# by every run of this repository; and Linear's API, the loopback wire, because no check may
# reach a Linear workspace. The verb, recipes, engine, store, routing and every record read back
# are real.
@pytest.fixture(scope="module")
def realiased(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Realiased]:  # noqa: PLR0915 - one incident, then each reading of it
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("follow-ups-realias")
    bench = _bench(tmp)
    environment = bench.environment
    environment["FAKE_CODEX_RUN_ON_MARKER"] = str(tmp / "commands.json")
    environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(tmp / "commands-ran.jsonl")
    for name in [one for one in environment if one.startswith(remote.FOLLOWUPS_ENV_PREFIX)]:
        del environment[name]
    environment["ONETASKGRAPH_SECRETS_FILE"] = str(tmp / "no-secrets.env")
    home = boundary_home.registered_home(
        tmp / "state", public=follow_up_boundary.PUBLIC_IDENTITIES, private=[PRIVATE_REPOSITORY]
    )
    # The incident was filed before the host registered the private repository the run id
    # names, so the boundary held no term for it: seeded under a state root registering the
    # public identities alone, then read under the one that registers it.
    unregistered = boundary_home.registered_home(
        tmp / "seed-state", public=follow_up_boundary.PUBLIC_IDENTITIES
    )
    environment.update(boundary_home.environment(environment, unregistered))
    layer = _layer(tmp, "config-home")
    environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(layer)
    with (
        linear_board() as linear,
        pytest.MonkeyPatch.context() as patched,
        remote._serving_followups(environment, fields=True),
    ):
        patched.setenv(onetaskgraph_user_layer.XDG_CONFIG_HOME, str(layer))
        environment.update(linear.environment(LINEAR))
        environment.update(linear.environment(onetaskgraph_user_layer.ORGANIZATION_PLANS))
        remote.BOARD.real_time = True
        patched.setenv(
            follow_up_variables.root_name(), environment[follow_up_variables.root_name()]
        )
        # Seeded as the incident wrote it, before any source declared a visibility here.
        declared = {
            name: environment.pop(name)
            for name in [one for one in environment if one.endswith("__VISIBILITY")]
        }

        # The incident: drafted while the engine named the run by its id, before it recorded one.
        drafted = {
            cause: _draft(bench, RUN, f"Draft for {cause}")
            for cause in (OWN_CAUSE, SECOND_CAUSE, PRIVATE_CAUSE, SHARED_CAUSE, "kept")
        }
        ids = {cause: _draft_id(RUN, path) for cause, path in drafted.items()}
        other_issue = _other_issue(bench, OTHER, SHARED_CAUSE)
        items = {
            OWN_CAUSE: _filed(
                bench,
                OWN_CAUSE,
                (ids[OWN_CAUSE],),
                f"{remote.SIBLING_REPOSITORY.name}: the session token is never rotated",
                f"Verified in run `{RUN}`",
            ),
            SECOND_CAUSE: _filed(
                bench,
                SECOND_CAUSE,
                (ids[SECOND_CAUSE],),
                f"{remote.SIBLING_REPOSITORY.name}: the login form forgets the user",
                f"Verified in follow-up run `{SECOND}` of run {RUN}",
            ),
            PRIVATE_CAUSE: _filed(
                bench,
                PRIVATE_CAUSE,
                (ids[PRIVATE_CAUSE],),
                "quietharbor: the intake sync drops a field",
                f"Verified in follow-up run `{THIRD}`",
                repository=PRIVATE_REPOSITORY,
            ),
            BOARD_ONLY_CAUSE: _filed(
                bench,
                BOARD_ONLY_CAUSE,
                (ids["kept"],),
                f"{remote.SIBLING_REPOSITORY.name}: the logout link is hidden",
                f"Verified in follow-up run `{THIRD}`",
            ),
        }
        # The shared root cause's local ticket, which its evidence comment stands for.
        shared = tickets.ticket_path(bench.drafts_root, RUN, SHARED_CAUSE)
        shared.write_text(
            tickets.render(
                _ticket(
                    RUN,
                    SHARED_CAUSE,
                    (ids[SHARED_CAUSE],),
                    f"{remote.SIBLING_REPOSITORY.name}: the login page retries without backoff",
                    f"Verified again in run `{RUN}`",
                    HOST,
                    repository=REPOSITORY,
                )
            ),
            encoding="utf-8",
        )
        placed = _module(bench, "board-status", "--board", BOARD, str(shared))
        assert placed.returncode == 0, placed.stdout + placed.stderr
        comments = {
            "shared": (
                other_issue,
                _evidence(bench, other_issue, RUN, SHARED_CAUSE, f"Seen again in run `{RUN}`."),
            ),
            "second": (
                other_issue,
                _evidence(
                    bench, other_issue, RUN, SECOND_CAUSE, f"Seen in follow-up run `{SECOND}`."
                ),
            ),
            "board-only": (
                items[BOARD_ONLY_CAUSE],
                _evidence(
                    bench,
                    items[BOARD_ONLY_CAUSE],
                    RUN,
                    BOARD_ONLY_CAUSE,
                    f"Seen in follow-up run `{THIRD}`.",
                ),
            ),
        }
        unrelated_comment = _evidence(
            bench, other_issue, UNRELATED, UNRELATED_CAUSE, f"Seen in run `{UNRELATED}`."
        )
        for issue in (other_issue, items[BOARD_ONLY_CAUSE]):
            estimated = _module(bench, "re-estimate", "--board", BOARD, issue)
            assert estimated.returncode == 0, estimated.stdout + estimated.stderr
        # The board-only issue's ticket and its draft are gone from the drafts root.
        tickets.ticket_path(bench.drafts_root, RUN, BOARD_ONLY_CAUSE).unlink()
        for cause in (OWN_CAUSE, SECOND_CAUSE, PRIVATE_CAUSE, SHARED_CAUSE):
            drafted[cause].unlink()
        account = {
            "schema": tickets.DISPOSITIONS_SCHEMA,
            "run": RUN,
            "drafts": sorted(ids.values()),
            "dispositions": [
                {
                    "draft": ids[cause],
                    "disposition": tickets.Disposition.FILED.value,
                    "root_causes": [cause],
                    "detail": detail,
                }
                for cause, detail in (
                    (OWN_CAUSE, f"Filed by run {RUN}."),
                    (SECOND_CAUSE, f"Filed by follow-up run {SECOND}."),
                    (PRIVATE_CAUSE, f"Filed on the private board by {THIRD}."),
                    (SHARED_CAUSE, f"Evidence on {other_issue}, another run's issue."),
                )
            ]
            + [
                {
                    "draft": ids["kept"],
                    "disposition": tickets.Disposition.NOT_REPRODUCIBLE.value,
                    "root_causes": [],
                    "detail": f"Not reproduced by {THIRD}.",
                }
            ],
        }
        path = tickets.dispositions_path(bench.drafts_root, RUN)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(account, indent=2) + "\n", encoding="utf-8")

        # A person asked on one of the run's issues, and a gathering of the run quoted it: the
        # run's reply and its response account are part of what the incident left.
        asked = _commented(bench, items[SECOND_CAUSE], "Does it forget the user on logout?", None)
        intent, plan = tmp / "gathering-intent.json", tmp / "gathering-plan.json"
        root = str(bench.drafts_root)
        for step in (
            ["gather", "--root", root, "--intent", str(intent), "--to", BOARD, "--run", RUN],
            ["launch", "--root", root, "--intent", str(intent), "--plan", str(plan)],
        ):
            done = _run([*COMMENTS_MODULE, *step], bench)
            assert done.returncode == 0, done.stdout + done.stderr
        ((_, feedback),) = comments_module.read_plan(plan, bench.drafts_root).runs
        held_comments = _store(bench, "task", "comment", "list", items[SECOND_CAUSE])["comments"]
        assert isinstance(held_comments, list), held_comments
        (listed,) = [one for one in held_comments if isinstance(one, dict) and one["id"] == asked]
        reply_body = _staged(
            bench,
            "reply.md",
            tickets.render_reply(
                RUN,
                SECOND_CAUSE,
                answers=asked,
                url=str(listed["url"]),
                author=None,
                response=f"Answered by follow-up run `{SECOND}`.",
                verdict=tickets.Verdict.DOES_NOT_CONFIRM,
            ),
        )
        reply = str(
            _store(
                bench, "task", "comment", "add", items[SECOND_CAUSE], "--body-file", str(reply_body)
            )["id"]
        )
        comments["reply"] = (items[SECOND_CAUSE], reply)
        tickets.responses_path(feedback).write_text(
            json.dumps(
                {
                    "schema": tickets.RESPONSES_SCHEMA,
                    "run": RUN,
                    "feedback": feedback.name,
                    "responses": [
                        {
                            "comment": asked,
                            "issue": items[SECOND_CAUSE],
                            "action": f"Answered in run {RUN}.",
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
        before = _held(linear)
        created_before = len(remote.BOARD.created)

        # The engine records the run, naming it by its public id.
        environment[onevcs_state_snapshot.ONEVCS_HOME] = str(home)
        _recorded_run(bench, RUN)
        public = _engine_public_id(bench, RUN)
        environment.update(declared)
        environment[follow_up_boundary.visibility_variable(BOARD)] = "public"
        # The local sources the host declares private beside its public boards, so a follow-up
        # launch's own project, which names the run, is screened as the host's is; not
        # `drafts`, whose copies onto a public board would then carry no origin to bind by.
        (tmp / "authoring").mkdir(exist_ok=True)
        for source in ("FOLLOW_UP_RUNS", "AUTHORING"):
            environment[f"ONETASKGRAPH_SOURCES__{source}__VISIBILITY"] = "private"
        patched.setenv(onetaskgraph_user_layer.XDG_CONFIG_HOME, str(layer))

        realias = [*MODULE, "realias", "--root", str(bench.drafts_root), RUN]

        def tickets_now() -> dict[str, str]:
            held = tickets.ticket_path(bench.drafts_root, public, OWN_CAUSE).parent
            return {one.name: one.read_text(encoding="utf-8") for one in sorted(held.glob("*.md"))}

        def refusing(operation: _Operation) -> subprocess.CompletedProcess[str]:
            _GitHubFixture.refused_operation = operation
            _GitHubFixture.refused_after = 0
            try:
                return _run(realias, bench)
            finally:
                _GitHubFixture.refused_operation = None
                _GitHubFixture.refused_after = 0

        # The board refuses the read that finds the run's items, after the run's records have
        # moved under its public id and before any item is copied: what the records then hold.
        refusing(_Operation.SEARCH)
        moved_tickets = tickets_now()
        # The board refuses the copy that re-points the first item: the invocation stops with
        # the ticket it staged for that copy as it stood, and the invocations after complete.
        copy_refused = refusing(_Operation.UPDATE_ISSUE)
        tickets_after_copy_refused = tickets_now()
        created_after_copy_refused = len(remote.BOARD.created)
        # The board refuses the second comment edit: the invocation stops there, listing what
        # it rewrote before it, and the next one completes from where the run's records stand.
        _GitHubFixture.refused_operation = _Operation.UPDATE_COMMENT
        _GitHubFixture.refused_after = 1
        try:
            refused = _run(realias, bench)
        finally:
            _GitHubFixture.refused_operation = None
            _GitHubFixture.refused_after = 0
        refused_path = tickets.realias_path(bench.drafts_root, public)
        refused_inventory = (
            refused_path.read_text(encoding="utf-8") if refused_path.is_file() else ""
        )
        created_after_refused = len(remote.BOARD.created)
        # The board answers a comment edit with the body it already held, the edit unapplied:
        # the invocation stops on that answer, and the comment still names the run for the
        # invocation that completes.
        sent_as_is = remote.BOARD.stored_comment
        remote.BOARD.stored_comment = lambda held, _sent: held
        try:
            rewritten = _run(realias, bench)
        finally:
            remote.BOARD.stored_comment = sent_as_is
        # Lose the next comment edit before applying it, whether the store sends its
        # plain document or the whole-issue twin used by a guarded cache write.
        lost_edits: list[_Operation] = []

        def lose_comment_edit(operation: _Operation) -> None:
            lost_edits.append(operation)
            _GitHubFixture.dropped_operation = (operation, "before")

        comment_edits = (_Operation.UPDATE_COMMENT, _Operation.UPDATE_COMMENT_WHOLE)
        for operation in comment_edits:
            _GitHubFixture.before_operation[operation] = partial(lose_comment_edit, operation)
        try:
            unlanded = _run(realias, bench)
            assert lost_edits, "the lost-comment fault was never reached"
            assert _GitHubFixture.dropped_operation is None, lost_edits
        finally:
            for operation in comment_edits:
                _GitHubFixture.before_operation.pop(operation, None)
            _GitHubFixture.dropped_operation = None
        first = _run(realias, bench)
        after = _held(linear)
        created_after = len(remote.BOARD.created)
        inventory_path = tickets.realias_path(bench.drafts_root, public)
        inventory = inventory_path.read_text(encoding="utf-8") if inventory_path.is_file() else ""
        writes_before = _writes(linear)
        second = _run(realias, bench)
        writes_after = _writes(linear)
        inventory_again = (
            inventory_path.read_text(encoding="utf-8") if inventory_path.is_file() else ""
        )
        records = {
            str(one.relative_to(bench.drafts_root)): one.read_text(encoding="utf-8")
            for one in sorted(bench.drafts_root.rglob("*"))
            if one.is_file()
        }

        board_check = _module(
            bench, "check-dispositions", "--root", str(bench.drafts_root), "--board", BOARD, public
        )
        moved_feedback = (
            bench.drafts_root / comments_module.FEEDBACK_DIRECTORY / public / feedback.name
        )
        responses_checked = _module(
            bench, "check-responses", "--board", BOARD, "--feedback", str(moved_feedback), public
        )

        # A follow-ups pass of the run, keyed by its public id, over its own issue and comment.
        quoted = shlex.quote(str(tickets.ticket_path(bench.drafts_root, public, OWN_CAUSE)))
        module = shlex.join(MODULE)
        helper = _staged(bench, "comment.py", COMMENT_PROGRAM)
        comment = _staged(
            bench,
            "comment.md",
            tickets.render_comment(public, SHARED_CAUSE, f"Seen again in run `{public}`."),
        )
        _script(
            bench,
            f"Verify the follow-up drafts run `{public}`",
            [
                _from_checkout(
                    "bash",
                    "-c",
                    "\n".join(
                        [
                            "set -euo pipefail",
                            f"word=$({module} board-status --board {BOARD} {quoted})",
                            f'sed -i "s/^status: .*/status: \\"$word\\"/" {quoted}',
                            f"{module} validate {quoted}",
                            f"{module} copy --board {BOARD} {quoted}",
                            shlex.join(
                                [PYTHON, str(helper), str(ONETASKGRAPH_BIN), other_issue, public]
                                + [SHARED_CAUSE, str(comment)]
                            ),
                            f"{module} re-estimate --board {BOARD} {shlex.quote(other_issue)}",
                        ]
                    ),
                )
            ],
        )
        followed = _run(["just", "follow-ups", RUN, "--to", BOARD], bench)
        if followed.returncode != OK:
            followed = subprocess.CompletedProcess(
                followed.args, followed.returncode, followed.stdout, followed.stderr + _ran(bench)
            )
        after_pass = _held(linear)
        created_after_pass = len(remote.BOARD.created)

        _commented(bench, items[OWN_CAUSE], "Does this also hit the admin login?", "a-person")
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
        yield Realiased(
            bench=bench,
            public=public,
            items=items,
            comments=comments,
            unrelated_issue=other_issue,
            unrelated_comment=unrelated_comment,
            before=before,
            created_before=created_before,
            first=first,
            after=after,
            created_after=created_after,
            inventory=inventory,
            second=second,
            writes_around_second=(writes_before, writes_after),
            inventory_again=inventory_again,
            records=records,
            board_check=board_check,
            followed=followed,
            after_pass=after_pass,
            created_after_pass=created_after_pass,
            routed=routed,
            routed_snapshot=snapshot,
            responses_checked=responses_checked,
            moved_feedback=moved_feedback,
            refused=refused,
            refused_inventory=refused_inventory,
            created_after_refused=created_after_refused,
            rewritten=rewritten,
            unlanded=unlanded,
            moved_tickets=moved_tickets,
            copy_refused=copy_refused,
            tickets_after_copy_refused=tickets_after_copy_refused,
            created_after_copy_refused=created_after_copy_refused,
        )


# llmlint: ignore-end[e2e_not_mocked]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _native(qualified: str) -> str:
    return qualified.partition(":")[2]


def _board_of(qualified: str) -> str:
    return qualified.partition(":")[0]


def _texts(held: Held, qualified: str) -> HeldItem:
    side = held.linear if _board_of(qualified) == LINEAR else held.github
    return side[_native(qualified)]


def test_the_engine_names_the_run_by_a_public_id_other_than_its_id(realiased: Realiased) -> None:
    assert realiased.public.startswith("run-") and realiased.public != RUN
    assert realiased.first.returncode == OK, realiased.first.stdout + realiased.first.stderr
    assert realiased.public in realiased.first.stdout


def test_the_inventory_lists_every_seeded_item_and_comment_of_the_run_and_nothing_else(
    realiased: Realiased,
) -> None:
    inventory = json.loads(realiased.inventory)
    assert inventory["run"] == realiased.public
    listed = {(entry["kind"], entry["id"]): entry["destination"] for entry in inventory["entries"]}
    assert listed == {("item", item): _board_of(item) for item in realiased.items.values()} | {
        ("comment", comment): _board_of(issue) for issue, comment in realiased.comments.values()
    }
    assert set(listed.values()) == {BOARD, LINEAR}
    for entry in inventory["entries"]:
        assert isinstance(entry["url"], str) and entry["url"].startswith("http"), entry
    assert realiased.unrelated_comment not in json.dumps(inventory)
    assert realiased.unrelated_issue not in [entry["id"] for entry in inventory["entries"]]


def test_each_is_rewritten_in_place_and_names_the_public_id_alone(realiased: Realiased) -> None:
    assert realiased.created_after == realiased.created_before
    assert sorted(realiased.after.github) == sorted(realiased.before.github)
    assert sorted(realiased.after.linear) == sorted(realiased.before.linear)
    for qualified in realiased.items.values():
        held = _texts(realiased.after, qualified)
        was = _texts(realiased.before, qualified)
        assert sorted(held["comments"]) == sorted(was["comments"]), qualified
        text = json.dumps(held)
        assert RUN in json.dumps(was), qualified
        assert RUN not in text, qualified
        assert realiased.public in text, qualified
        if _board_of(qualified) == BOARD:
            for part in (held["title"], held["body"], held["origin"]):
                assert _passes_public_boundary(realiased.bench, str(part)), part
    for issue, comment in realiased.comments.values():
        body = _texts(realiased.after, issue)["comments"][comment]
        assert RUN not in body, body
        assert realiased.public in body, body
        owner = tickets.comment_owner(body)
        assert owner is not None and owner.run == realiased.public, body
        assert _passes_public_boundary(realiased.bench, body), body
    # The name is a term the boundary refuses, so passing is the public id's doing.
    assert not _passes_public_boundary(realiased.bench, f"run {RUN}")


def test_the_unrelated_runs_issue_and_comment_are_byte_identical(realiased: Realiased) -> None:
    was = _texts(realiased.before, realiased.unrelated_issue)
    held = _texts(realiased.after, realiased.unrelated_issue)
    assert {key: held[key] for key in ("title", "body", "origin", "host")} == {
        key: was[key] for key in ("title", "body", "origin", "host")
    }
    assert (
        held["comments"][realiased.unrelated_comment]
        == was["comments"][realiased.unrelated_comment]
    )


def test_the_records_move_under_the_public_id_and_keep_the_engine_id_only_as_it(
    realiased: Realiased,
) -> None:
    keyed = {path: text for path, text in realiased.records.items() if RUN in path}
    assert keyed == {}, sorted(keyed)
    # The engine's id is kept only where a reader reaches the run by it: the draft project
    # comment routing reads, and each draft's own `run_id` and the transcript command derived
    # from it, in the private `drafts` source.
    holding = sorted(path for path, text in realiased.records.items() if RUN in text)
    assert all(
        path == f"projects/{realiased.public}.md"
        or path.startswith(f"tasks/{realiased.public}/drafts/")
        for path in holding
    ), holding
    assert drafts.engine_run(realiased.bench.drafts_root, realiased.public) == RUN
    (draft_path,) = [
        path for path in realiased.records if path.startswith(f"tasks/{realiased.public}/drafts/")
    ]
    draft = drafts.parse(realiased.records[draft_path])
    assert (draft.run, draft.engine_run) == (realiased.public, RUN)
    assert RUN not in draft.body + draft.title
    board_only = tickets.ticket_path(
        realiased.bench.drafts_root, realiased.public, BOARD_ONLY_CAUSE
    )
    assert board_only.is_file()


def test_a_second_invocation_writes_nothing_and_the_same_inventory(realiased: Realiased) -> None:
    assert realiased.second.returncode == OK, realiased.second.stdout + realiased.second.stderr
    before, after = realiased.writes_around_second
    assert after == before
    assert realiased.inventory_again == realiased.inventory


def test_the_runs_account_holds_on_the_board_under_its_public_id(realiased: Realiased) -> None:
    assert realiased.board_check.returncode == OK, (
        realiased.board_check.stdout + realiased.board_check.stderr
    )


def test_a_follow_ups_pass_recognizes_its_issues_and_comments_and_duplicates_none(
    realiased: Realiased,
) -> None:
    assert realiased.followed.returncode == OK, (
        realiased.followed.stdout + realiased.followed.stderr
    )
    assert realiased.created_after_pass == realiased.created_after
    assert sorted(realiased.after_pass.github) == sorted(realiased.after.github)
    assert sorted(realiased.after_pass.linear) == sorted(realiased.after.linear)
    for issue in realiased.after.github:
        assert sorted(realiased.after_pass.github[issue]["comments"]) == sorted(
            realiased.after.github[issue]["comments"]
        ), issue


def test_the_comment_answering_recipe_routes_a_persons_comment_to_the_run(
    realiased: Realiased,
) -> None:
    assert realiased.routed.returncode == OK, realiased.routed.stdout + realiased.routed.stderr
    selection = realiased.routed_snapshot.get("selection")
    assert isinstance(selection, dict), realiased.routed_snapshot
    assert realiased.public in selection, selection
    assert RUN not in json.dumps(realiased.routed_snapshot)


def test_a_gatherings_response_account_holds_on_the_board_under_the_public_id(
    realiased: Realiased,
) -> None:
    """The run's quoted comment, its reply and the account naming both moved with the run."""
    checked = realiased.responses_checked
    assert checked.returncode == OK, checked.stdout + checked.stderr
    moved = realiased.moved_feedback
    assert moved.is_file(), moved
    assert RUN not in moved.read_text(encoding="utf-8")
    account = tickets.responses_path(moved).read_text(encoding="utf-8")
    assert RUN not in account and realiased.public in account, account


def test_a_copy_the_board_refuses_leaves_the_tickets_as_they_stood_and_a_rerun_completes(
    realiased: Realiased,
) -> None:
    copy_refused = realiased.copy_refused
    assert copy_refused.returncode != OK, copy_refused.stdout + copy_refused.stderr
    assert "refused" in copy_refused.stderr, copy_refused.stderr
    assert realiased.created_after_copy_refused == realiased.created_before
    assert realiased.moved_tickets, "no ticket of the run had moved under its public id"
    assert realiased.tickets_after_copy_refused == realiased.moved_tickets
    assert realiased.first.returncode == OK, realiased.first.stdout + realiased.first.stderr


def test_an_edit_the_board_refuses_stops_the_verb_listing_what_it_rewrote_and_a_rerun_completes(
    realiased: Realiased,
) -> None:
    refused = realiased.refused
    assert refused.returncode != OK, refused.stdout + refused.stderr
    assert "refused" in refused.stderr, refused.stderr
    assert realiased.created_after_refused == realiased.created_before
    listed = json.loads(realiased.refused_inventory)["entries"]
    rewritten = [entry["id"] for entry in listed if entry["kind"] == "comment"]
    assert len(rewritten) == 1, listed
    # The completing invocation lists that comment too, though it no longer names the run.
    completed = json.loads(realiased.inventory)["entries"]
    assert set(rewritten) <= {entry["id"] for entry in completed if entry["kind"] == "comment"}
    assert realiased.first.returncode == OK, realiased.first.stdout + realiased.first.stderr


def test_an_edit_the_board_answers_with_another_body_stops_the_verb_and_a_rerun_completes(
    realiased: Realiased,
) -> None:
    """The edit's answer is held to the body sent, through the verb and the real store."""
    stopped = realiased.rewritten
    assert stopped.returncode != OK, stopped.stdout + stopped.stderr
    assert "answered a body or parts other than it sent" in stopped.stderr, stopped.stderr
    assert "read the comment on the board before realiasing again" in stopped.stderr
    assert realiased.first.returncode == OK, realiased.first.stdout + realiased.first.stderr


def test_an_edit_no_part_of_which_landed_stops_the_verb_and_a_rerun_completes(
    realiased: Realiased,
) -> None:
    """A comment edit whose part did not land is never read as rewritten.

    The adopted store refuses the lost HTTP answer with a non-zero exit, for a plain
    comment edit as well as its guarded whole-issue twin. The verb stops on that refusal
    before :func:`tickets.edited_version` reads an answer; the next invocation rewrites
    the comment and completes.
    """
    stopped = realiased.unlanded
    assert stopped.returncode != OK, stopped.stdout + stopped.stderr
    assert "connection closed before message completed" in stopped.stderr, stopped.stderr
    assert "name its public id" not in stopped.stdout, stopped.stdout
    assert realiased.first.returncode == OK, realiased.first.stdout + realiased.first.stderr
