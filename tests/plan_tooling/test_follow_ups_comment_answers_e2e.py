"""`just follow-ups <run> --feedback FILE --comments` answering what people asked, end to end.

Everything below the recipe is real: `just follow-ups` and its script, the gathering
`orchestrator/follow_up_comments.py` writes, the launch under the shipped
`graphs/follow-up.yaml`, the installed `onepipeline` driver, and the installed `onetaskgraph`
every ticket, board item and comment is written and read through. The bench, the board — a
`local-md` stand-in named with `--to`, never the live `followups` board — and the mirror
checkout are `tests/plan_tooling/test_follow_ups_recipe_e2e.py`'s and
`tests/plan_tooling/test_follow_ups_answer_comments_recipe_e2e.py`'s, imported rather than
restated. **`tests/e2e/fake_codex.py` stands in for the paid model alone**, running the
commands the answering agent would choose through the real programs.

The module fixture answers ten comments in one dispatch: one clearly retiring the run's own
`Proposal`, one only asking about another, the retiring one again on an item a person
deferred, one asking for a corrected fix on a ticket of each older schema — schema 5 being the
shape onepipeline#438 met — and one on another run's older item. The credential journeys drive
the recipe in both modes from a mirror checkout whose `.env` alone holds the board credential.
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
from test_follow_ups_answer_comments_recipe_e2e import (
    BOARD_CREDENTIAL,
    NO_ENGINE,
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
    Stored,
    _bench,
    _category,
    _comments,
    _decided_and_copied,
    _draft,
    _item,
    _moved,
    _ran,
    _re_estimate,
    _run,
    _script,
    _staged,
    _stored,
    _ticket,
)
from waits import timeout as e2e_timeout

from orchestrator import follow_up_tickets as tickets
from orchestrator.plan_store import QualifiedTaskId
from orchestrator.project_store import frontmatter
from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The run's items: a proposal a comment retires, a proposal a comment only asks about, a
#: deferred item the retiring comment sits on, and a ticket at each older record schema the
#: feedback task brings forward, keyed by that schema — 1, with no `host`, no `repositories`
#: and no `## Impact`; 2, with a `host` but neither of the others; 3, with `repositories` but no
#: `## Impact`; 4, the last with the headings schema 5 retired; 5, which the onepipeline#438
#: ticket carried; and 6, the one before the current.
PROPOSED_CAUSE = "listing-cursor-skips-last-page"
UNSURE_CAUSE = "lock-file-left-after-crash"
DEFERRED_CAUSE = "sweep-trailer-omits-a-family"
#: One per :data:`tickets.PRIOR_SCHEMAS`, so a schema added there without a cause here fails at
#: import rather than going undriven.
OLDER_CAUSES = dict(
    zip(
        tickets.PRIOR_SCHEMAS,
        (
            "digest-mails-twice",
            "search-ignores-accents",
            "upload-times-out-early",
            "report-drops-a-footer",
            "export-drops-a-column",
            "retry-loop-never-backs-off",
        ),
        strict=True,
    )
)
#: Another run's schema-6 item this run once commented on, which a person then asks about:
#: the rule's last clause brings it forward by `re-estimate` alone, after this run's reply.
OTHER_RUN_CAUSE = "watcher-misses-a-rename"

REVIEWER = "a-reviewer"
RETIRING = "Handled elsewhere: the listing rewrite removes this, so this ticket won't be needed.\n"
CORRECTING = (
    "The suggested fix is wrong: page the export by cursor in `src/export.py` instead, "
    "as the listing already does.\n"
)
ASKING = "Is this still needed once the listing rewrite lands? I can't tell from the evidence.\n"
SEEN_AGAIN = "Seen again on the current release: the watcher missed a rename under `src/`.\n"
REQUESTED_FIX = "Page the export by cursor in `src/export.py`, as the listing already does."

WITHDREW = "Withdrew this run's proposal: the comment says the listing rewrite handles it."
KEPT_UNSURE = (
    "Replied and left the item as it is: the comment asks whether it is still needed rather "
    "than saying it is not."
)
KEPT_DEFERRED = (
    "Left the item as it is: a person deferred it, and `board-status --withdraw` refused."
)
RE_ESTIMATED = "Counted this sighting as one more occurrence and re-estimated the item."
CORRECTED = "Brought the ticket to the current schema with the corrected fix, and copied it."

DEFERRED_WITNESS = "deferred-withdrawal.witness"

#: The agent's edit bringing an older ticket forward; the estimate is left to `board-status`.
BRING_FORWARD = """\
import json, pathlib, re, subprocess, sys
from orchestrator import follow_up_tickets as tickets

path, fix, impact = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8")
prefix = f'  "{tickets.KEY}": '
(line,) = [held for held in text.splitlines() if held.startswith(prefix)]
record = json.loads(line.removeprefix(prefix))
record["schema"] = tickets.SCHEMA
if "host" not in record:
    record["host"] = subprocess.run(
        ["hostname"], check=True, capture_output=True, text=True
    ).stdout.strip()
    text = re.sub(
        rf"(## {tickets.EVIDENCE}\\n\\n.*?)(\\n\\n## )",
        lambda held: f"{held[1]} Verified on `{record['host']}`.{held[2]}",
        text, count=1, flags=re.DOTALL,
    )
if not re.search(r"(?m)^repositories: ", text):
    text = re.sub(
        r"(?m)^(status: .*)$",
        lambda held: held[1] + "\\nrepositories: " + json.dumps([record["repository"]]),
        text, count=1,
    )
if f"## {tickets.IMPACT}\\n" not in text:
    examples = "## Examples\\n"
    text = text.replace(examples, f"## {tickets.IMPACT}\\n\\n{impact}\\n\\n{examples}", 1)
text = re.sub(r"## Repository\\n\\n.*?\\n\\n(?=## )", "", text, count=1, flags=re.DOTALL)
text = text.replace("## Suggested fixes\\n", f"## {tickets.SUGGESTED_FIX}\\n", 1)
record[tickets.FREQUENCY_FIELD] = str(tickets.Frequency.INTERMITTENT)
text = text.replace(line, prefix + json.dumps(record), 1)
text = re.sub(
    rf"(## {tickets.SUGGESTED_FIX}\\n\\n).*?(\\n\\n## )", lambda held: held[1] + fix + held[2],
    text, count=1, flags=re.DOTALL,
)
path.write_text(text, encoding="utf-8")
"""

#: The answering agent's program; ``actions`` is keyed by issue, not by comment, and the reply
#: on each issue in ``confirming`` is marked as confirming its root cause.
ANSWER_EACH_COMMENT = """\
import json, re, subprocess, sys, tempfile
from orchestrator import follow_up_tickets as tickets

log, run, store, actions, confirming = sys.argv[1:6]
actions = json.loads(actions)
confirming = json.loads(confirming)
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
    verdict = (
        tickets.Verdict.CONFIRMS if one.issue in confirming else tickets.Verdict.DOES_NOT_CONFIRM
    )
    reply = tickets.render_reply(
        run, cause, answers=one.comment, url=fields["URL"], author=fields["Author"],
        response=action, verdict=verdict,
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
        "verdict": verdict.value,
    })
with open(account, "w", encoding="utf-8") as stream:
    json.dump({"schema": tickets.RESPONSES_SCHEMA, "run": run, "feedback": gathered,
               "responses": responses}, stream, indent=2)
"""


class Filed(NamedTuple):
    """One item filed at an older record schema before the dispatch: its ticket, as it stood."""

    issue: QualifiedTaskId
    ticket: Path
    before: dict[str, object]
    #: The `## Impact` section the agent writes from the ticket's evidence where it has none.
    impact: str


class Older(NamedTuple):
    """One ticket filed at an older record schema, before and after the dispatch."""

    issue: QualifiedTaskId
    ticket: Path
    before: dict[str, object]
    impact: str
    #: What `validate` printed of it, and exited with, before the agent brought it forward.
    witness: str
    after: dict[str, object]
    validated: subprocess.CompletedProcess[str]


class Answered(NamedTuple):
    """The one dispatch's journey, read back before anything is torn down."""

    bench: Bench
    run: tickets.RunId
    proposed_issue: QualifiedTaskId
    unsure_issue: QualifiedTaskId
    deferred_issue: QualifiedTaskId
    gathering: Path
    result: subprocess.CompletedProcess[str]
    #: The node's task as the store holds it, read right after the launch.
    stored: Stored
    witness: str
    proposed_after: dict[str, object]
    unsure_after: dict[str, object]
    deferred_after: dict[str, object]
    comments_after: dict[str, list[dict[str, object]]]
    checked: subprocess.CompletedProcess[str]
    #: Each older ticket by the schema it was filed at.
    older: dict[int, Older]
    #: Another run's schema-6 item this run marked, before and after the dispatch.
    other_run: Filed
    other_run_after: dict[str, object]


#: A gathering for one run, composed from the module's own reads and writer: every person's
#: comment on the issues ``run`` owns or has marked with its own comment. It is composed here
#: rather than by `just follow-ups-answer-comments`, whose routing sends a comment to the run
#: owning its issue alone: this journey's subject is what the dispatch does with a gathering
#: that also quotes another run's item, which `check-gathering` admits for a run that marked
#: it and a manager may hand `just follow-ups --comments` directly.
GATHER_FOR = """\
import sys
from datetime import UTC, datetime
from pathlib import Path
from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets

root, board, run = sys.argv[1:4]
issues = comments.commented_issues(board, comments.moment(sys.argv[4], "since"))
chosen = [
    comments.Selected(issue, comment.id, comments.comment_url(issue, comment),
                      comment.author or comments.UNKNOWN_AUTHOR, comment.last_changed,
                      comment.body)
    for issue in issues
    if issue.owner == run or any(
        (owner := tickets.comment_owner(held.body)) is not None and owner.run == run
        for held in issue.comments
    )
    for comment in issue.comments
    if tickets.comment_owner(comment.body) is None
]
print(comments.write_feedback(Path(root), run, board, chosen, None, datetime.now(UTC)))
"""

#: Before every comment these journeys write.
GATHERED_SINCE = "2026-01-01T00:00:00Z"


# llmlint: ignore-block[tests_mirror_real_usage] The recipe routes a comment to the run
# owning its issue alone, so no recipe writes a gathering that also quotes another run's item
# this run marked — which `check-gathering` admits and a manager may hand `just follow-ups
# --comments` — and that dispatch is this journey's subject; the gathering is composed
# through the recipe's own module reads and writer instead.
def _gathered(bench: Bench, run: str, board: str = BOARD) -> Path:
    """``run``'s comments on ``board``, gathered through the module the recipe gathers with."""
    written = _run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python3"),
            "-c",
            GATHER_FOR,
            str(bench.drafts_root),
            board,
            run,
            GATHERED_SINCE,
        ],
        bench,
    )
    assert written.returncode == 0, written.stdout + written.stderr
    return Path(written.stdout.strip())


# llmlint: ignore-end[tests_mirror_real_usage]


def test_the_older_tickets_are_refused_by_validate_as_the_schema_history_says(
    tmp_path: Path,
) -> None:
    """Each older ticket, run through the real `validate`, is refused as its schema's history says.

    So the tickets the journeys file are what a run at each older schema wrote: the validator
    names the schema, a `host` missing before :data:`tickets.HOST_AT`, no `repositories`
    before :data:`tickets.REPOSITORIES_AT`, and the headings :data:`tickets.RETIRED_AT`
    retired before it. :data:`tickets.IMPACT_AT` is not told apart here, because below
    :data:`tickets.RETIRED_AT` the retired headings are what the body is refused for.
    """
    bench = _bench(tmp_path)
    run = f"ca-history-{os.getpid()}"
    for schema, cause in OLDER_CAUSES.items():
        path = tickets.ticket_path(bench.drafts_root, run, cause)
        path.parent.mkdir(parents=True, exist_ok=True)
        ticket = _ticket(
            run,
            cause,
            (f"drafts:{run}/drafts/a-consumed-draft",),
            f"some-service: {cause.replace('-', ' ')}",
            f"Filed at schema {schema}",
            HOST,
            frequency=None,
        )
        path.write_text(_older(ticket, schema), encoding="utf-8")
        validated = _run(
            [
                str(REPO_ROOT / ".venv" / "bin" / "python3"),
                "-m",
                "orchestrator.follow_up_tickets",
                "validate",
                str(path),
            ],
            bench,
        )
        said = validated.stdout + validated.stderr
        assert validated.returncode == 1, said
        assert f"the record is schema {schema}, and this reads schema {tickets.SCHEMA}" in said
        missing = said.split(" record is missing ", 1)[1].split("\n", 1)[0]
        assert ("host" in missing) == (schema < tickets.HOST_AT), (schema, said)
        assert ("carries no `repositories`" in said) == (schema < tickets.REPOSITORIES_AT), (
            schema,
            said,
        )
        retired = " and ".join(f"`## {heading}`" for heading in tickets.RETIRED_HEADINGS)
        assert (f"carries {retired}, which schema {tickets.RETIRED_AT} retired" in said) == (
            schema < tickets.RETIRED_AT
        ), (schema, said)


def _impact(body: str) -> str:
    """The prose and lines of ``body``'s `## Impact` section, as a ticket's evidence states them."""
    return body.split(f"## {tickets.IMPACT}\n\n", 1)[1].split("\n\n## ", 1)[0].strip()


def _older(ticket: tickets.Ticket, schema: int) -> str:
    """``ticket`` as ``schema`` stored it: no estimate, frequency or item binding.

    ``ticket`` carries no estimate line, which schema 7 added. Before
    :data:`tickets.IMPACT_AT` its body has no `## Impact`, before
    :data:`tickets.REPOSITORIES_AT` it names no `repositories`, and before
    :data:`tickets.HOST_AT` its record names no `host` and its `## Evidence` no machine. From
    schema 5 (:data:`tickets.RETIRED_AT`) its body keeps today's headings; before it, the body
    carries the two that schema retired — `## Repository`, after `## Root cause`, and
    `## Suggested fixes` in place of `## Suggested fix`.
    """
    assert tickets.PRIOR_SCHEMAS[0] <= schema < tickets.SCHEMA, schema
    assert f"- {tickets.ESTIMATE_LINE}:" not in ticket.body, ticket.body
    body = ticket.body
    if schema < tickets.IMPACT_AT:
        body = body.replace(f"## {tickets.IMPACT}\n\n{_impact(body)}\n\n\n", "", 1)
        assert f"## {tickets.IMPACT}\n" not in body, body
    if schema < tickets.HOST_AT:
        body = body.replace(f" Verified on `{ticket.host}`.", "", 1)
        assert f"`{ticket.host}`" not in body, body
    if schema < tickets.RETIRED_AT:
        repository, suggested = tickets.RETIRED_HEADINGS
        following = tickets.IMPACT if schema >= tickets.IMPACT_AT else "Examples"
        body = body.replace(
            f"## {following}\n",
            f"## {repository}\n\n{ticket.repository}\n\n## {following}\n",
            1,
        ).replace(f"## {tickets.SUGGESTED_FIX}\n", f"## {suggested}\n", 1)
    dropped = {tickets.ESTIMATE_FIELD, tickets.FREQUENCY_FIELD, tickets.BINDING_FIELD}
    if schema < tickets.HOST_AT:
        dropped.add("host")
    record = {key: value for key, value in tickets.record(ticket).items() if key not in dropped}
    held: dict[str, object] = {"title": ticket.title, "status": ticket.status.value}
    if schema >= tickets.REPOSITORIES_AT:
        held["repositories"] = [ticket.repository]
    held["metadata"] = {tickets.KEY: record | {"schema": schema}}
    return frontmatter(held, body)


def _file_older(bench: Bench, run: str, cause: str, schema: int) -> Filed:
    """``run``'s ticket for ``cause`` written at ``schema`` and copied onto the board as is."""
    path = tickets.ticket_path(bench.drafts_root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
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
        [str(ONETASKGRAPH_BIN), "task", "copy", tickets.qualified_id(run, cause), "--to", BOARD],
        bench,
    )
    assert copied.returncode == 0, copied.stdout + copied.stderr
    issue = QualifiedTaskId(f"{BOARD}:{run}/tickets/{cause}")
    return Filed(issue, path, _item(bench, issue), _impact(filed.body))


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


def _brought_forward(bench: Bench, python: str, witness: Path, held: Filed) -> list[str]:
    """The agent's step over an older ticket: `validate` first, then bring it forward.

    What `validate` printed and exited with goes to ``witness`` — the refusal naming the older
    schema the task keys its rule on — and the ticket is then edited in place, filling what
    its schema lacked: `host` from `hostname`, `repositories` from its record, and
    ``held.impact`` as its `## Impact` section.
    """
    ticket = held.ticket
    helper = _staged(bench, "bring-forward.py", BRING_FORWARD)
    validate = shlex.join([python, "-m", "orchestrator.follow_up_tickets", "validate", str(ticket)])
    quoted = shlex.quote(str(witness))
    return [
        "bash",
        "-c",
        f"set -uo pipefail\ncd {shlex.quote(str(REPO_ROOT))}\n"
        f"{validate} >> {quoted} 2>&1\n"
        f'echo "exit $?" >> {quoted}\n'
        f"{shlex.join([python, str(helper), str(ticket), REQUESTED_FIX, held.impact])}\n",
    ]


def _answering(
    bench: Bench,
    python: str,
    log: Path,
    run: str,
    actions: dict[str, str],
    confirming: tuple[str, ...] = (),
) -> list[str]:
    """The agent's command replying to each quoted comment, recording ``actions`` by issue."""
    helper = _staged(bench, "answer-each-comment.py", ANSWER_EACH_COMMENT)
    return [
        "bash",
        "-c",
        'cd "$1" && exec "$2" "$3" "$4" "$5" "$6" "$7" "$8"',
        "_",
        str(REPO_ROOT),
        python,
        str(helper),
        str(log),
        run,
        str(ONETASKGRAPH_BIN),
        json.dumps(actions),
        json.dumps(list(confirming)),
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
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    bench.environment["FAKE_CODEX_RUN_ON_MARKER"] = str(tmp / "commands.json")
    bench.environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(tmp / "commands-ran.jsonl")
    run = tickets.RunId(f"ca-main-{os.getpid()}")
    python = str(REPO_ROOT / ".venv" / "bin" / "python3")
    follow_up_run = f"{run}{SUFFIX}"
    try:
        proposed_issue = _filed(bench, run, PROPOSED_CAUSE)
        unsure_issue = _filed(bench, run, UNSURE_CAUSE)
        deferred_issue = _filed(bench, run, DEFERRED_CAUSE)
        _moved(bench, deferred_issue, tickets.Status.DEFERRED.value)
        older = {
            schema: _file_older(bench, run, cause, schema) for schema, cause in OLDER_CAUSES.items()
        }
        # Another run's item, which this run marked with an evidence comment of its own.
        other_run = _file_older(
            bench, f"ca-other-{os.getpid()}", OTHER_RUN_CAUSE, tickets.PRIOR_SCHEMA
        )
        _commented(
            bench,
            other_run.issue,
            tickets.render_comment(run, OTHER_RUN_CAUSE, "Seen again in this run."),
            None,
        )

        # After the run last responded, so the gathering quotes each of these.
        _next_second()
        _commented(bench, proposed_issue, RETIRING, REVIEWER)
        _commented(bench, unsure_issue, ASKING, REVIEWER)
        _commented(bench, deferred_issue, RETIRING, REVIEWER)
        for held in older.values():
            _commented(bench, held.issue, CORRECTING, REVIEWER)
        _commented(bench, other_run.issue, SEEN_AGAIN, REVIEWER)
        gathering = _gathered(bench, run)

        witness = tmp / DEFERRED_WITNESS
        log = tmp / "prompts.jsonl"
        actions = {
            proposed_issue: WITHDREW,
            unsure_issue: KEPT_UNSURE,
            deferred_issue: KEPT_DEFERRED,
            other_run.issue: RE_ESTIMATED,
        }
        commands = [
            _decided_and_copied(
                python, tickets.ticket_path(bench.drafts_root, run, PROPOSED_CAUSE), "--withdraw"
            ),
            _withdrawal_refused(
                python, witness, tickets.ticket_path(bench.drafts_root, run, DEFERRED_CAUSE)
            ),
        ]
        for schema, held in older.items():
            commands += [
                _brought_forward(bench, python, tmp / f"schema-{schema}.witness", held),
                _decided_and_copied(python, held.ticket),
            ]
            actions[held.issue] = CORRECTED
        _script(
            bench,
            run,
            [
                *commands,
                _answering(bench, python, log, run, actions, (other_run.issue,)),
                _re_estimate(python, other_run.issue),
            ],
        )
        result = _run(
            ["just", "follow-ups", run, "--feedback", str(gathering), "--comments", "--to", BOARD],
            bench,
            environment=bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(log)},
        )
        stored = _stored(bench, follow_up_run)
        checked = _run(
            [python, "-m", "orchestrator.follow_up_tickets", "check-responses"]
            + ["--board", BOARD, "--feedback", str(gathering), run],
            bench,
        )
        return Answered(
            bench=bench,
            run=run,
            proposed_issue=proposed_issue,
            unsure_issue=unsure_issue,
            deferred_issue=deferred_issue,
            gathering=gathering,
            result=result,
            stored=stored,
            witness=witness.read_text(encoding="utf-8") if witness.is_file() else "",
            proposed_after=_item(bench, proposed_issue),
            unsure_after=_item(bench, unsure_issue),
            deferred_after=_item(bench, deferred_issue),
            comments_after={
                issue: _comments(bench, issue)
                for issue in (
                    proposed_issue,
                    unsure_issue,
                    deferred_issue,
                    *(held.issue for held in older.values()),
                    other_run.issue,
                )
            },
            checked=checked,
            older={
                schema: Older(
                    issue=held.issue,
                    ticket=held.ticket,
                    before=held.before,
                    impact=held.impact,
                    witness=(
                        (tmp / f"schema-{schema}.witness").read_text(encoding="utf-8")
                        if (tmp / f"schema-{schema}.witness").is_file()
                        else ""
                    ),
                    after=_item(bench, held.issue),
                    validated=_run(
                        [
                            python,
                            "-m",
                            "orchestrator.follow_up_tickets",
                            "validate",
                            str(held.ticket),
                        ],
                        bench,
                    ),
                )
                for schema, held in older.items()
            },
            other_run=other_run,
            other_run_after=_item(bench, other_run.issue),
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


def test_a_comment_that_does_not_clearly_retire_a_proposal_is_answered_and_changes_nothing(
    answered: Answered,
) -> None:
    """Asking whether a proposal is still needed is no ruling that it is not: replied to only."""
    _settled(answered)

    assert _category(answered.unsure_after) == tickets.Status.PROPOSED.value
    assert _reply_to(answered, answered.unsure_issue, ASKING).kind is tickets.CommentKind.REPLY
    account = json.loads(tickets.responses_path(answered.gathering).read_text(encoding="utf-8"))
    kept = [entry for entry in account["responses"] if entry["issue"] == answered.unsure_issue]
    assert [entry["action"] for entry in kept] == [KEPT_UNSURE], account
    assert answered.checked.returncode == OK, answered.checked.stdout + answered.checked.stderr


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
    retired = [f"## {heading}\n" for heading in tickets.RETIRED_HEADINGS]
    held_before = str(older.before["content"])
    assert all((heading in held_before) is (schema < tickets.RETIRED_AT) for heading in retired), (
        held_before
    )
    # What the schema lacked, as it stood on the board before the dispatch filled it.
    assert (f"## {tickets.IMPACT}\n" in held_before) is (schema >= tickets.IMPACT_AT), held_before
    assert bool(older.before.get("repositories")) is (schema >= tickets.REPOSITORIES_AT), (
        older.before
    )
    assert ("host" in before[tickets.KEY]) is (schema >= tickets.HOST_AT), before
    assert (f"`{HOST}`" in held_before) is (schema >= tickets.HOST_AT), held_before

    after = older.after
    metadata = after["metadata"]
    assert isinstance(metadata, dict)
    assert metadata[tickets.KEY]["schema"] == tickets.SCHEMA
    ticket = tickets.from_store_item(after)
    fix = ticket.body.split(f"## {tickets.SUGGESTED_FIX}\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert fix == REQUESTED_FIX, ticket.body
    assert f"Filed at schema {schema} (Examples)." in ticket.body, "the rest of the ticket moved"
    assert not any(heading in ticket.body for heading in retired), ticket.body
    assert ticket.frequency is tickets.Frequency.INTERMITTENT
    assert ticket.priority_estimate is not None, "board-status wrote no estimate"
    assert ticket.host == HOST
    assert f"Verified on `{HOST}`." in ticket.body, ticket.body
    assert after["repositories"] == [REPOSITORY]
    impact = ticket.body.split(f"## {tickets.IMPACT}\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert impact.startswith(older.impact), impact
    assert f"- {tickets.ESTIMATE_LINE}:" in impact, "board-status wrote no estimate line"
    assert older.validated.returncode == OK, older.validated.stdout + older.validated.stderr
    assert "is a sound ticket" in older.validated.stdout + older.validated.stderr
    assert _reply_to(answered, older.issue, CORRECTING).kind is tickets.CommentKind.REPLY
    assert answered.checked.returncode == OK, answered.checked.stdout + answered.checked.stderr


def test_another_runs_older_item_a_comment_asks_about_is_re_estimated_after_the_reply(
    answered: Answered,
) -> None:
    """The rule's last clause on another run's item: `re-estimate` alone, after a confirming reply.

    The item is brought to the current schema with its estimate, and nothing else of it moves:
    its body is the other run's, less the estimate line `re-estimate` adds.
    """
    _settled(answered)
    before = answered.other_run.before
    after = answered.other_run_after
    held_before = before["metadata"]
    held_after = after["metadata"]
    assert isinstance(held_before, dict) and isinstance(held_after, dict)
    assert held_before[tickets.KEY]["schema"] == tickets.PRIOR_SCHEMA
    assert held_after[tickets.KEY]["schema"] == tickets.SCHEMA
    assert held_after[tickets.KEY][tickets.ESTIMATE_FIELD] is not None
    assert {
        key: value
        for key, value in held_after[tickets.KEY].items()
        if key not in ("schema", tickets.ESTIMATE_FIELD)
    } == {key: value for key, value in held_before[tickets.KEY].items() if key != "schema"}
    assert f"- {tickets.ESTIMATE_LINE}:" in str(after["content"]), after["content"]
    assert _category(after) == _category(before)
    reply = _reply_to(answered, answered.other_run.issue, SEEN_AGAIN)
    assert reply.kind is tickets.CommentKind.REPLY, reply
    assert reply.verdict is tickets.Verdict.CONFIRMS, reply
    assert answered.checked.returncode == OK, answered.checked.stdout + answered.checked.stderr


def test_the_dispatched_task_carries_the_investigation_bound_the_exception_and_the_schema_rule(
    answered: Answered,
) -> None:
    """The node's task, read back out of the store: the feedback mode, carrying all three rules.

    The engine's own check of the stored item passes, so it is the rendering its provenance
    records, and the store regenerates the same body from the answers it kept.
    """
    _settled(answered)
    stored = answered.stored
    assert stored.checked.returncode == OK, stored.checked.stdout + stored.checked.stderr
    assert stored.content == stored.rerendered
    task = stored.content
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


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, e2e_not_mocked, tests_mirror_real_usage]  # noqa: E501 - llmlint reads a directive's rule list off one line
# These recipe journeys run in the plan_tooling Nx project, the dedicated tier for journeys
# that drive the recipe over the installed store; its planToolingWorkspace input covers the
# recipe, the credential helper and the store configuration they run, and each ends before
# any launch. The store the mirror carries is the published CLI's boundary, where this suite
# doubles a store and nowhere above it: it delegates every command to the pinned store and
# records only which credential it was handed, which no real store reports. That trace is the
# one observation of the property these journeys owe — which credential the recipe hands its
# store — because the recipe by design prints no credential value, and each journey also holds
# what a user sees: the exit status, the diagnostic and that no run launched.
@pytest.mark.parametrize(
    ("held", "handed"),
    [(None, PLANTED_CREDENTIAL), (PROCESS_CREDENTIAL, PROCESS_CREDENTIAL)],
    ids=["from-the-file", "the-process-value-wins"],
)
def test_the_comments_mode_reads_the_board_with_the_credential_this_checkout_supplies(
    tmp_path: Path, held: str | None, handed: str
) -> None:
    """`--comments` run directly: the gathering's board read is handed the `.env` token.

    A manager running `just follow-ups … --comments` without the answer-comments recipe was
    refused for a missing `GH_PROJECTS_TOKEN` while the token sat in that file.
    """
    mirror = _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")
    gathering = _gathered(mirror.bench, mirror.run)

    result = _follow_ups_from(
        mirror, held, "--feedback", str(gathering), "--comments", "--to", BOARD
    )

    assert result.returncode == REFUSED, result.stdout + result.stderr
    assert _handed(mirror) == {handed}, mirror.trace.read_text(encoding="utf-8")
    # Past the board read, the mirror has no engine to state the template with, so nothing launched.
    assert NO_ENGINE in result.stderr, result.stderr
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
    assert NO_ENGINE in result.stderr, result.stderr
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


def _mode_arguments(mirror: Mirror, comments: bool) -> tuple[str, ...]:
    """The arguments of one mode over ``mirror``, with a draft for the initial one to count."""
    _draft(mirror.bench, mirror.run, "The cursor skips the last page")
    if not comments:
        return ("--to", BOARD)
    return ("--feedback", str(_gathered(mirror.bench, mirror.run)), "--comments", "--to", BOARD)


@pytest.mark.parametrize("comments", [False, True], ids=["initial", "comments"])
@pytest.mark.parametrize("shape", ["malformed", "not-a-file"])
def test_a_credential_file_the_helper_refuses_stops_the_recipe_before_any_store_command(
    tmp_path: Path, comments: bool, shape: str
) -> None:
    """A `.env` the helper refuses is refused under this recipe's own name, in either mode.

    A malformed line is refused for the whole file rather than launching over half of it, and
    a `.env` that is there but no readable file is somebody's credential the recipe would
    otherwise run without. No store command — the board read included — runs first.
    """
    malformed = "not-an-assignment-with-a-value-that-must-not-appear\n"
    mirror = _mirror(tmp_path, malformed if shape == "malformed" else None)
    env_file = mirror.checkout / ".env"
    if shape == "not-a-file":
        env_file.mkdir()

    result = _follow_ups_from(mirror, None, *_mode_arguments(mirror, comments))

    assert result.returncode == REFUSED, result.stdout + result.stderr
    refused = (
        f"follow-ups: malformed credential line 1 in {env_file}; "
        "write it as KEY=VALUE or make it a '#' comment, then retry"
        if shape == "malformed"
        else f"follow-ups: the credential file at {env_file} is not a readable regular file; "
        "fix its type or permissions, then retry"
    )
    assert refused in result.stderr, result.stderr
    assert "must-not-appear" not in result.stderr, "a credential file's line reached a diagnostic"
    assert not mirror.trace.exists(), "a store command ran over a credential file it refused"
    _nothing_launched(mirror)


@pytest.mark.parametrize("comments", [False, True], ids=["initial", "comments"])
@pytest.mark.parametrize("sabotage", ["missing", "unreadable"])
def test_the_recipe_refuses_before_any_store_command_when_the_credentials_helper_is_unreadable(
    tmp_path: Path, comments: bool, sabotage: str
) -> None:
    """The one source of the credential gone or unreadable: refused naming it, in either mode."""
    mirror = _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")
    helper = mirror.checkout / "scripts" / "credentials-env.sh"
    if sabotage == "missing":
        helper.unlink()
    else:
        helper.chmod(0)
        if os.access(helper, os.R_OK):
            pytest.skip("this user reads a file with no permission bits: nothing is unreadable")

    result = _follow_ups_from(mirror, None, *_mode_arguments(mirror, comments))

    assert result.returncode == REFUSED, result.stdout + result.stderr
    assert (
        f"follow-ups: required helper is not a readable regular file: {helper}; "
        "restore it from the repository or run 'just bootstrap', then retry"
    ) in result.stderr, result.stderr
    assert not mirror.trace.exists(), "a store command ran without the checkout's credential"
    _nothing_launched(mirror)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, e2e_not_mocked, tests_mirror_real_usage]  # noqa: E501 - llmlint reads a directive's rule list off one line
