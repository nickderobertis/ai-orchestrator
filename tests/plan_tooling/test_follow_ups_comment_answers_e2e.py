"""`just follow-ups <run> --feedback FILE --comments` answering what people asked, end to end.

Everything below the recipe is real: `just follow-ups` and its script, the gathering
`orchestrator/follow_up_comments.py` writes, the launch under the shipped
`graphs/follow-up.yaml`, the installed `onepipeline` driver, and the installed `onetaskgraph`
every ticket, board item and comment is written and read through. The bench, the board — a
`local-md` stand-in named with `--to`, never the live `followups` board — and the mirror
checkout are `tests/plan_tooling/test_follow_ups_recipe_e2e.py`'s and
`tests/plan_tooling/test_follow_ups_handle_comments_recipe_e2e.py`'s, imported rather than
restated. **`tests/e2e/fake_codex.py` stands in for the paid model alone**, running the
commands the answering agent would choose through the real programs.

The module fixture answers three comments on one run's own items in one dispatch:

* a comment clearly retiring the run's own item at `Proposal`, which the dispatch withdraws
  through `board-status --withdraw`, validating and copying the ticket as the task says;
* the same comment on the run's own item a person moved to `Deferred`, where the same
  withdrawal is refused by `board-status` and nothing is copied, so the item stays deferred;
* a comment asking for a corrected fix on a ticket of record schema 5 — the shape a comment on
  onepipeline#438 met — which the dispatch brings to the current schema, carrying the change,
  before `board-status`, `validate` and `copy`.

The credential journeys drive the recipe from a mirror checkout whose `.env` alone holds the
board credential, in both modes, reusing the planted token and the recording store the
handle-comments journeys established.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from published_tools import ONETASKGRAPH_BIN
from test_follow_ups_handle_comments_recipe_e2e import (
    BOARD_CREDENTIAL,
    NO_TEMPLATE,
    PLANTED_CREDENTIAL,
    PROCESS_CREDENTIAL,
    Mirror,
    _commented,
    _filed,
    _handed,
    _mirror,
    _next_second,
)
from test_follow_ups_recipe_e2e import (
    BOARD,
    HOST,
    OK,
    REFUSED,
    REPOSITORY,
    SUFFIX,
    Bench,
    _bench,
    _category,
    _comments,
    _decided_and_copied,
    _draft,
    _item,
    _moved,
    _prompts,
    _ran,
    _run,
    _script,
    _staged,
    _ticket,
)
from waits import timeout as e2e_timeout

from orchestrator import follow_up_tickets as tickets
from orchestrator.plan_store import QualifiedTaskId
from orchestrator.project_store import frontmatter
from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The run's items: a proposal a comment retires, a deferred item the same comment sits on,
#: and a ticket at each older record schema the feedback task brings forward, keyed by that
#: schema — 5, which the onepipeline#438 ticket carried, and 6, the one just before.
PROPOSED_CAUSE = "listing-cursor-skips-last-page"
DEFERRED_CAUSE = "sweep-trailer-omits-a-family"
OLDER_CAUSES = {5: "export-drops-a-column", 6: "retry-loop-never-backs-off"}

REVIEWER = "a-reviewer"
RETIRING = "Handled elsewhere: the listing rewrite removes this, so this ticket won't be needed.\n"
CORRECTING = (
    "The suggested fix is wrong: page the export by cursor in `src/export.py` instead, "
    "as the listing already does.\n"
)
REQUESTED_FIX = "Page the export by cursor in `src/export.py`, as the listing already does."

WITHDREW = "Withdrew this run's proposal: the comment says the listing rewrite handles it."
KEPT_DEFERRED = (
    "Left the item as it is: a person deferred it, and `board-status --withdraw` refused."
)
CORRECTED = "Brought the ticket to the current schema with the corrected fix, and copied it."

DEFERRED_WITNESS = "deferred-withdrawal.witness"

#: The agent bringing an older ticket on disk forward in place, as the rule the task composes
#: says: its record to the current schema, carrying the `frequency` judgment its evidence
#: supports, and its `## Suggested fix` to the fix the comment asks for. Everything else stays
#: as it stands; `board-status` then writes the estimate, its line and the priority.
BRING_FORWARD = """\
import json, pathlib, re, sys
from orchestrator import follow_up_tickets as tickets

path, fix = pathlib.Path(sys.argv[1]), sys.argv[2]
text = path.read_text(encoding="utf-8")
prefix = f'  "{tickets.KEY}": '
(line,) = [held for held in text.splitlines() if held.startswith(prefix)]
record = json.loads(line.removeprefix(prefix))
record["schema"] = tickets.SCHEMA
record[tickets.FREQUENCY_FIELD] = str(tickets.Frequency.INTERMITTENT)
text = text.replace(line, prefix + json.dumps(record), 1)
text = re.sub(
    rf"(## {tickets.SUGGESTED_FIX}\\n\\n).*?(\\n\\n## )", lambda held: held[1] + fix + held[2],
    text, count=1, flags=re.DOTALL,
)
path.write_text(text, encoding="utf-8")
"""

#: The answering agent's own program: post one reply to each comment its task quotes, then
#: write the account the task is held to, from the action it took on each comment's issue.
ANSWER_EACH_COMMENT = """\
import json, re, subprocess, sys, tempfile
from orchestrator import follow_up_tickets as tickets

log, run, store, actions = sys.argv[1:5]
actions = json.loads(actions)
with open(log, encoding="utf-8") as stream:
    task = json.loads(stream.read().splitlines()[-1])["prompt"]
account = re.search(r"account is a JSON document at `([^`]+)`", task)[1]
gathered = re.search(r'"feedback": "([^"]+)"', task)[1]
gathering = tickets.read_gathering(task.split("## The comments to answer", 1)[1])
responses = []
for section, one in zip(task.split("### Comment ")[1:], gathering.quoted, strict=True):
    fields = dict(
        line[2:].split(": ", 1) for line in section.splitlines() if line.startswith("- ")
    )
    shown = json.loads(
        subprocess.run([store, "task", "show", one.issue, "--json"], check=True,
                       capture_output=True, text=True).stdout
    )
    cause = shown["items"][0]["item"]["metadata"][tickets.KEY]["root_cause"]
    action = actions[one.issue]
    reply = tickets.render_reply(
        run, cause, answers=one.comment, url=fields["URL"], author=fields["Author"],
        response=action, verdict=tickets.Verdict.DOES_NOT_CONFIRM,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as body:
        body.write(reply)
    subprocess.run([store, "task", "comment", "add", one.issue, "--body-file", body.name],
                   check=True)
    listed = json.loads(
        subprocess.run([store, "task", "comment", "list", one.issue, "--json"], check=True,
                       capture_output=True, text=True).stdout
    )["comments"]
    (posted,) = [
        held["id"] for held in listed
        if (owner := tickets.comment_owner(held["body"])) is not None
        and owner.run == run and owner.answers == one.comment
    ]
    responses.append({
        "comment": one.comment, "issue": one.issue, "action": action, "reply": posted,
        "verdict": tickets.Verdict.DOES_NOT_CONFIRM.value,
    })
with open(account, "w", encoding="utf-8") as stream:
    json.dump({"schema": tickets.RESPONSES_SCHEMA, "run": run, "feedback": gathered,
               "responses": responses}, stream, indent=2)
"""


class Older(NamedTuple):
    """One ticket filed at an older record schema, before and after the dispatch."""

    issue: QualifiedTaskId
    ticket: Path
    before: dict[str, object]
    #: What `validate` printed of it, and exited with, before the agent brought it forward.
    witness: str
    after: dict[str, object]
    validated: subprocess.CompletedProcess[str]


class Answered(NamedTuple):
    """The one dispatch's journey, read back before anything is torn down."""

    bench: Bench
    run: tickets.RunId
    proposed_issue: QualifiedTaskId
    deferred_issue: QualifiedTaskId
    gathering: Path
    result: subprocess.CompletedProcess[str]
    prompts: list[str]
    witness: str
    proposed_after: dict[str, object]
    deferred_after: dict[str, object]
    comments_after: dict[str, list[dict[str, object]]]
    checked: subprocess.CompletedProcess[str]
    #: Each older ticket by the schema it was filed at.
    older: dict[int, Older]


def _gathered(bench: Bench, run: str, board: str = BOARD) -> Path:
    """``run``'s new comments on ``board``, gathered by the module the recipe gathers with."""
    written = _run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python3"),
            "-m",
            "orchestrator.follow_up_comments",
            "feedback",
            "--root",
            str(bench.drafts_root),
            "--board",
            board,
            run,
        ],
        bench,
    )
    assert written.returncode == 0, written.stdout + written.stderr
    return Path(written.stdout.strip())


def _older(ticket: tickets.Ticket, schema: int) -> str:
    """``ticket`` as ``schema`` 5 or 6 stored it: today's headings, no estimate or frequency."""
    record = {
        key: value
        for key, value in tickets.record(ticket).items()
        if key not in (tickets.ESTIMATE_FIELD, tickets.FREQUENCY_FIELD, tickets.BINDING_FIELD)
    }
    return frontmatter(
        {
            "title": ticket.title,
            "status": ticket.status.value,
            "repositories": [ticket.repository],
            "metadata": {tickets.KEY: record | {"schema": schema}},
        },
        ticket.body,
    )


def _withdrawal_refused(python: str, witness: Path, ticket: Path) -> list[str]:
    """The agent's withdrawal of a ticket, copied only if `board-status --withdraw` allows it.

    What it printed and exited with goes to ``witness``, and the copy is chained on its
    success, so a refusal is exactly what leaves the item as the board holds it.
    """
    decide = shlex.join(
        [python, "-m", "orchestrator.follow_up_tickets", "board-status", "--board", BOARD]
    )
    quoted = shlex.quote(str(witness))
    return [
        "bash",
        "-c",
        f"set -uo pipefail\ncd {shlex.quote(str(REPO_ROOT))}\n"
        f"if word=$({decide} --withdraw {shlex.quote(str(ticket))} 2>> {quoted}); then\n"
        f'  echo "decided $word" >> {quoted}\n'
        f"  {python} -m orchestrator.follow_up_tickets copy --board {BOARD} "
        f"{shlex.quote(str(ticket))} && echo copied >> {quoted}\n"
        f"else\n"
        f'  echo "refused $?" >> {quoted}\n'
        f"fi\n",
    ]


def _brought_forward(bench: Bench, python: str, witness: Path, ticket: Path) -> list[str]:
    """The agent's step over an older ticket: `validate` first, then bring it forward.

    What `validate` printed and exited with goes to ``witness`` — the refusal naming the older
    schema the task keys its rule on — and the ticket is then edited in place.
    """
    helper = _staged(bench, "bring-forward.py", BRING_FORWARD)
    validate = shlex.join([python, "-m", "orchestrator.follow_up_tickets", "validate", str(ticket)])
    quoted = shlex.quote(str(witness))
    return [
        "bash",
        "-c",
        f"set -uo pipefail\ncd {shlex.quote(str(REPO_ROOT))}\n"
        f"{validate} >> {quoted} 2>&1\n"
        f'echo "exit $?" >> {quoted}\n'
        f"{shlex.join([python, str(helper), str(ticket), REQUESTED_FIX])}\n",
    ]


def _answering(
    bench: Bench, python: str, log: Path, run: str, actions: dict[str, str]
) -> list[str]:
    """The agent's command replying to each quoted comment, recording ``actions`` by issue."""
    helper = _staged(bench, "answer-each-comment.py", ANSWER_EACH_COMMENT)
    return [
        "bash",
        "-c",
        'cd "$1" && exec "$2" "$3" "$4" "$5" "$6" "$7"',
        "_",
        str(REPO_ROOT),
        python,
        str(helper),
        str(log),
        run,
        str(ONETASKGRAPH_BIN),
        json.dumps(actions),
    ]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `tests/plan_tooling` is
# already the Nx project edge this repository keeps for journeys that launch the installed
# engine, keyed on `planToolingWorkspace`, which covers every file this launch reads. The
# fixture is module-scoped and spends one launch whose turn is the provider's stand-in.
@pytest.fixture(scope="module")
def answered(tmp_path_factory: pytest.TempPathFactory) -> Answered:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("follow-ups-comment-answers")
    bench = _bench(tmp)
    bench.environment["FAKE_CODEX_RUN_ON_MARKER"] = str(tmp / "commands.json")
    bench.environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(tmp / "commands-ran.jsonl")
    run = tickets.RunId(f"ca-main-{os.getpid()}")
    python = str(REPO_ROOT / ".venv" / "bin" / "python3")
    follow_up_run = f"{run}{SUFFIX}"
    try:
        proposed_issue = _filed(bench, run, PROPOSED_CAUSE)
        deferred_issue = _filed(bench, run, DEFERRED_CAUSE)
        _moved(bench, deferred_issue, tickets.Status.DEFERRED.value)
        older: dict[int, tuple[QualifiedTaskId, Path, dict[str, object]]] = {}
        for schema, cause in OLDER_CAUSES.items():
            path = tickets.ticket_path(bench.drafts_root, run, cause)
            filed = _ticket(
                run,
                cause,
                (f"drafts:{run}/drafts/a-consumed-draft",),
                f"some-service: {cause.replace('-', ' ')}",
                f"Filed at schema {schema}",
                HOST,
                frequency=None,
            )
            path.write_text(_older(filed, schema), encoding="utf-8")
            copied = _run(
                [str(ONETASKGRAPH_BIN), "task", "copy", tickets.qualified_id(run, cause)]
                + ["--to", BOARD],
                bench,
            )
            assert copied.returncode == 0, copied.stdout + copied.stderr
            issue = QualifiedTaskId(f"{BOARD}:{run}/tickets/{cause}")
            older[schema] = (issue, path, _item(bench, issue))

        # After the run last responded, so the gathering quotes each of these.
        _next_second()
        _commented(bench, proposed_issue, RETIRING, REVIEWER)
        _commented(bench, deferred_issue, RETIRING, REVIEWER)
        for issue, _, _ in older.values():
            _commented(bench, issue, CORRECTING, REVIEWER)
        gathering = _gathered(bench, run)

        witness = tmp / DEFERRED_WITNESS
        log = tmp / "prompts.jsonl"
        actions = {proposed_issue: WITHDREW, deferred_issue: KEPT_DEFERRED}
        commands = [
            _decided_and_copied(
                python, tickets.ticket_path(bench.drafts_root, run, PROPOSED_CAUSE), "--withdraw"
            ),
            _withdrawal_refused(
                python, witness, tickets.ticket_path(bench.drafts_root, run, DEFERRED_CAUSE)
            ),
        ]
        for schema, (issue, path, _) in older.items():
            commands += [
                _brought_forward(bench, python, tmp / f"schema-{schema}.witness", path),
                _decided_and_copied(python, path),
            ]
            actions[issue] = CORRECTED
        _script(bench, run, [*commands, _answering(bench, python, log, run, actions)])
        result = _run(
            ["just", "follow-ups", run, "--feedback", str(gathering), "--comments", "--to", BOARD],
            bench,
            environment=bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(log)},
        )
        checked = _run(
            [python, "-m", "orchestrator.follow_up_tickets", "check-responses"]
            + ["--board", BOARD, "--feedback", str(gathering), run],
            bench,
        )
        return Answered(
            bench=bench,
            run=run,
            proposed_issue=proposed_issue,
            deferred_issue=deferred_issue,
            gathering=gathering,
            result=result,
            prompts=_prompts(log),
            witness=witness.read_text(encoding="utf-8") if witness.is_file() else "",
            proposed_after=_item(bench, proposed_issue),
            deferred_after=_item(bench, deferred_issue),
            comments_after={
                issue: _comments(bench, issue)
                for issue in (proposed_issue, deferred_issue, *(held[0] for held in older.values()))
            },
            checked=checked,
            older={
                schema: Older(
                    issue=issue,
                    ticket=path,
                    before=before,
                    witness=(
                        (tmp / f"schema-{schema}.witness").read_text(encoding="utf-8")
                        if (tmp / f"schema-{schema}.witness").is_file()
                        else ""
                    ),
                    after=_item(bench, issue),
                    validated=_run(
                        [python, "-m", "orchestrator.follow_up_tickets", "validate", str(path)],
                        bench,
                    ),
                )
                for schema, (issue, path, before) in older.items()
            },
        )
    finally:
        if (bench.runs / follow_up_run).exists():
            _run(["just", "stop", follow_up_run], bench)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _settled(answered: Answered) -> None:
    assert answered.result.returncode == OK, (
        answered.result.stdout + answered.result.stderr + _ran(answered.bench)
    )


def _reply_to(answered: Answered, issue: str, text: str) -> tickets.CommentOwner:
    """The one reply of the run answering the person's comment ``text`` on ``issue``."""
    listed = answered.comments_after[issue]
    (asked,) = [str(one["id"]) for one in listed if one["body"] == text]
    replies = [
        owner
        for one in listed
        if (owner := tickets.comment_owner(str(one["body"]))) is not None
        and owner.run == answered.run
        and owner.answers == asked
    ]
    assert len(replies) == 1, listed
    return replies[0]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] These journeys read the
# one launch the module fixture spends, in the plan_tooling Nx project: the dedicated tier for
# journeys that drive the installed engine, whose planToolingWorkspace input covers the
# recipe, the task modules and the store configuration they run, and whose turns are the
# provider's stand-in.
def test_a_comment_clearly_retiring_the_runs_own_proposal_withdraws_it_and_is_answered(
    answered: Answered,
) -> None:
    """The one status change a comment dispatch makes: closed as not planned, and replied to."""
    _settled(answered)

    assert _category(answered.proposed_after) == tickets.Status.WITHDRAWN.value
    assert _reply_to(answered, answered.proposed_issue, RETIRING).kind is tickets.CommentKind.REPLY
    assert answered.checked.returncode == OK, answered.checked.stdout + answered.checked.stderr
    account = json.loads(tickets.responses_path(answered.gathering).read_text(encoding="utf-8"))
    withdrawn = [
        entry for entry in account["responses"] if entry["issue"] == answered.proposed_issue
    ]
    assert [entry["action"] for entry in withdrawn] == [WITHDREW], account


def test_the_same_comment_on_a_deferred_item_leaves_its_status_as_a_person_set_it(
    answered: Answered,
) -> None:
    """`board-status --withdraw` refuses an item a person deferred, so nothing is copied."""
    _settled(answered)

    assert _category(answered.deferred_after) == tickets.Status.DEFERRED.value
    assert f"refused {tickets.PROTECTED}" in answered.witness, answered.witness
    assert "copied" not in answered.witness, answered.witness
    assert _reply_to(answered, answered.deferred_issue, RETIRING).kind is tickets.CommentKind.REPLY
    account = json.loads(tickets.responses_path(answered.gathering).read_text(encoding="utf-8"))
    kept = [entry for entry in account["responses"] if entry["issue"] == answered.deferred_issue]
    assert [entry["action"] for entry in kept] == [KEPT_DEFERRED], account


@pytest.mark.parametrize("schema", sorted(OLDER_CAUSES))
def test_a_comment_correcting_an_older_ticket_brings_it_to_the_current_schema_with_the_change(
    answered: Answered, schema: int
) -> None:
    """Brought forward before it is decided, validated and copied, rather than left stale."""
    _settled(answered)
    older = answered.older[schema]
    before = older.before["metadata"]
    assert isinstance(before, dict)
    assert before[tickets.KEY]["schema"] == schema
    assert (
        f"the record is schema {schema}, and this reads schema {tickets.SCHEMA}; bring "
        "the ticket to the current shape"
    ) in older.witness, older.witness
    assert f"exit {tickets.UNSOUND}" in older.witness, older.witness

    after = older.after
    metadata = after["metadata"]
    assert isinstance(metadata, dict)
    assert metadata[tickets.KEY]["schema"] == tickets.SCHEMA
    ticket = tickets.from_store_item(after)
    fix = ticket.body.split(f"## {tickets.SUGGESTED_FIX}\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert fix == REQUESTED_FIX, ticket.body
    assert f"Filed at schema {schema} (Examples)." in ticket.body, "the rest of the ticket moved"
    assert ticket.frequency is tickets.Frequency.INTERMITTENT
    assert ticket.priority_estimate is not None, "board-status wrote no estimate"
    assert ticket.host == HOST
    assert after["repositories"] == [REPOSITORY]
    assert older.validated.returncode == OK, older.validated.stdout + older.validated.stderr
    assert "is a sound ticket" in older.validated.stdout + older.validated.stderr
    assert _reply_to(answered, older.issue, CORRECTING).kind is tickets.CommentKind.REPLY
    assert answered.checked.returncode == OK, answered.checked.stdout + answered.checked.stderr


def test_the_dispatched_task_carries_the_investigation_bound_the_exception_and_the_schema_rule(
    answered: Answered,
) -> None:
    """What the member was handed is the composed feedback task, carrying all three rules."""
    _settled(answered)
    (task,) = answered.prompts
    flat = " ".join(task.split())

    assert "## Investigating what a comment asks" in task
    assert " ".join(tickets.WITHDRAWAL_EXCEPTION.split()) in flat
    assert "a ticket of an older schema is brought to the current shape before it is copied" in (
        flat
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _follow_ups_from(
    mirror: Mirror,
    credential: str | None,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """`just follow-ups` on the mirror, holding ``credential`` or no name at all."""
    held = {
        name: value
        for name, value in (mirror.bench.environment | (environment or {})).items()
        if name != BOARD_CREDENTIAL
    }
    if credential is not None:
        held[BOARD_CREDENTIAL] = credential
    # llmlint: ignore[e2e_not_mocked] The pinned store answers every command; what stands in front of it, as the mirror's own locked plan store, records which credential it was handed, which is on no command line and in no answer over a `local-md` board.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    held["CREDENTIAL_TRACE"] = str(mirror.trace)
    return subprocess.run(  # noqa: S603 - this checkout's own recipe over the installed store
        ["just", "follow-ups", mirror.run, *arguments],  # noqa: S607 - `just` from the search path, as an operator invokes it
        cwd=mirror.checkout,
        env=held,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )


def _nothing_launched(mirror: Mirror) -> None:
    assert not list(mirror.bench.runs.glob(f"{mirror.run}{SUFFIX}*")), "a run was launched"


@pytest.mark.parametrize(
    ("held", "handed"),
    [(None, PLANTED_CREDENTIAL), (PROCESS_CREDENTIAL, PROCESS_CREDENTIAL)],
    ids=["from-the-file", "the-process-value-wins"],
)
def test_the_comments_mode_reads_the_board_with_the_credential_this_checkout_supplies(
    tmp_path: Path, held: str | None, handed: str
) -> None:
    """`--comments` run directly: the gathering's board read is handed the `.env` token.

    A manager running `just follow-ups … --comments` without the handle-comments wrapper was
    refused for a missing `GH_PROJECTS_TOKEN` while the token sat in that file.
    """
    mirror = _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")
    gathering = _gathered(mirror.bench, mirror.run)

    result = _follow_ups_from(
        mirror, held, "--feedback", str(gathering), "--comments", "--to", BOARD
    )

    assert result.returncode == REFUSED, result.stdout + result.stderr
    assert _handed(mirror) == {handed}, mirror.trace.read_text(encoding="utf-8")
    # Past the board read, the mirror has no template to compose from, so nothing launched.
    assert NO_TEMPLATE in result.stderr, result.stderr
    _nothing_launched(mirror)
    assert PLANTED_CREDENTIAL not in result.stdout + result.stderr, "a credential value was printed"


@pytest.mark.parametrize(
    ("held", "handed"),
    [(None, PLANTED_CREDENTIAL), (PROCESS_CREDENTIAL, PROCESS_CREDENTIAL)],
    ids=["from-the-file", "the-process-value-wins"],
)
def test_the_initial_mode_hands_the_store_the_credential_this_checkout_supplies(
    tmp_path: Path, held: str | None, handed: str
) -> None:
    """The verification mode establishes the same credential before its first store command.

    Before it launches, that mode asks the store whether the board `--to` names is one this
    checkout configures; its board reads come after, and the dispatch inherits the same
    environment.
    """
    mirror = _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")
    _draft(mirror.bench, mirror.run, "The cursor skips the last page")

    result = _follow_ups_from(mirror, held, "--to", BOARD)

    assert result.returncode == REFUSED, result.stdout + result.stderr
    assert _handed(mirror) == {handed}, mirror.trace.read_text(encoding="utf-8")
    assert NO_TEMPLATE in result.stderr, result.stderr
    _nothing_launched(mirror)
    assert PLANTED_CREDENTIAL not in result.stdout + result.stderr, "a credential value was printed"


def test_with_no_credential_anywhere_the_default_board_is_refused_by_the_store_and_nothing_runs(
    tmp_path: Path,
) -> None:
    """No token in the process, in `.env` or in the store's secrets file: the store says so.

    The gathering names the default `followups` board, which is the live one, so the read is
    refused for its missing credential before anything leaves this host, and nothing launches.
    """
    mirror = _mirror(tmp_path, None)
    shutil.copy2(REPO_ROOT / "onetaskgraph.yaml", mirror.checkout / "onetaskgraph.yaml")
    standin = _gathered(mirror.bench, mirror.run).read_text(encoding="utf-8")
    gathering = tmp_path / "followups-gathering.md"
    gathering.write_text(
        standin.replace(f"`{BOARD}`", f"`{tickets.BOARD}`").replace(
            f"{BOARD}:", f"{tickets.BOARD}:"
        ),
        encoding="utf-8",
    )
    no_secrets = tmp_path / "no-secrets.env"
    no_secrets.write_text("", encoding="utf-8")

    result = _follow_ups_from(
        mirror,
        None,
        "--feedback",
        str(gathering),
        "--comments",
        environment={"ONETASKGRAPH_SECRETS_FILE": str(no_secrets)},
    )

    assert result.returncode == REFUSED, result.stdout + result.stderr
    assert "GH_PROJECTS_TOKEN is missing or empty" in result.stderr, result.stderr
    assert f"is not a gathering of '{tickets.BOARD}' this run could answer" in result.stderr
    assert _handed(mirror) == {"<unset>"}, mirror.trace.read_text(encoding="utf-8")
    _nothing_launched(mirror)


@pytest.mark.parametrize("comments", [False, True], ids=["initial", "comments"])
def test_a_credential_file_the_helper_refuses_stops_the_recipe_before_any_store_command(
    tmp_path: Path, comments: bool
) -> None:
    """A malformed `.env` is refused under this recipe's own name, in either mode, unread.

    The helper refuses the whole file rather than launching over half of it, and no store
    command — the board read included — runs first.
    """
    mirror = _mirror(tmp_path, "not-an-assignment-with-a-value-that-must-not-appear\n")
    _draft(mirror.bench, mirror.run, "The cursor skips the last page")
    arguments = ("--to", BOARD)
    if comments:
        arguments = (
            "--feedback",
            str(_gathered(mirror.bench, mirror.run)),
            "--comments",
            *arguments,
        )

    result = _follow_ups_from(mirror, None, *arguments)

    assert result.returncode == REFUSED, result.stdout + result.stderr
    assert (
        f"follow-ups: malformed credential line 1 in {mirror.checkout / '.env'}; "
        "write it as KEY=VALUE or make it a '#' comment, then retry"
    ) in result.stderr, result.stderr
    assert "must-not-appear" not in result.stderr, "a credential file's line reached a diagnostic"
    assert not mirror.trace.exists(), "a store command ran over a credential file it refused"
    _nothing_launched(mirror)
