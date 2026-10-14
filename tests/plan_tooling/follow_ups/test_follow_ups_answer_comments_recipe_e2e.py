"""`just follow-ups-answer-comments` over people's board comments, driven end to end.

Everything below the recipe is real: `just follow-ups-answer-comments` and its script,
`orchestrator/follow_up_comments.py` copying the board into a snapshot and settling the pass,
`just follow-ups --feedback --comments` and the launches it makes under the shipped
`graphs/follow-up.yaml`, the installed `onepipeline` driver, and the installed `onetaskgraph`
every ticket, board item, comment and snapshot is written and read through. The bench is
`tests/plan_tooling/follow_ups/test_follow_ups_recipe_e2e.py`'s, imported rather than restated.

**The board is the loopback GitHub, never the live `followups` board**: `tests/github_board.py`
serves it in this process as `followups`, and the locked plan store reads and writes it through
its real `github-projects` plugin with the cache this checkout's `onetaskgraph.yaml` turns on,
under a cache home of the journey's own. Its route to Hello Patient's Linear is pointed at an
empty local folder, as every loopback journey points it. What reaches the board is read off the
loopback's own request log, so the journeys hold what the pass asks the board rather than what
a stand-in store says it was asked. A comment a person writes, or edits, is planted on the
loopback's row, dated by the wall clock and signed as that person — the board answers every
comment a write here adds as the token's own account, and a person comments on GitHub, not
through this host's store. **`tests/e2e/fake_codex.py` stands in for the paid model alone**,
running the commands each answering agent's task names through the real programs: each quoted
issue read from the snapshot source, each reply posted with `task comment add <board id>
--expect <version> --json`, and the account written from that answer.

**What the recipe asks the store is recorded too.** Every plan-store read of the package runs
the `onetaskgraph` beside its interpreter, so the recipe runs from a checkout of this repository
whose toolchain carries a plan store that records each command it is handed and hands it to the
pinned store (:func:`_mirrored_toolchain`).

One module fixture walks one board's life, and reads back everything it needs before the board
is torn down: a watermark an earlier gathering left, the comments people wrote since, a dry run,
the real gathering answering three runs at once — one of them a run id carrying a capital — a
gathering with nothing left, a run-scoped one, a detached one, one whose run answers nothing,
and a preview: a dry run whose snapshot is launched with `--from` after the board moved under
it. The board's listing is paged two items at a time, so the narrowed answer spans pages.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, NewType

import follow_up_variables
import github_board as remote
import pytest
from flow_engine_stand_in import UNDOUBLED, give_flow_engine
from github_board import _GitHubFixture, _Operation
from nx_workspace import WORKSPACE_INSTALL_MARKS
from published_tools import ONETASKGRAPH_BIN
from test_follow_ups_recipe_e2e import (
    HELD_PRIORITY,
    HOST,
    OK,
    REFUSED,
    SUFFIX,
    Bench,
    Stored,
    _bench,
    _category,
    _comments,
    _item,
    _moved,
    _ran,
    _run,
    _store,
    _stored,
    _ticket,
)
from waits import timeout as e2e_timeout

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import QualifiedTaskId
from orchestrator.root import REPO_ROOT

#: A reader of the toolchain this checkout provisioned: every step here runs its tool
#: through a `just` recipe or `uv run`, and no writer touches that toolchain, so
#: `tests/e2e/nx_workspace.py` asks no xdist group of it. The group is this module's own,
#: for fixture sharing: `life` is module-scoped and drives the real recipe end to end, so
#: spread across workers the module would build it once per worker.
pytestmark = [
    *WORKSPACE_INSTALL_MARKS,
    pytest.mark.xdist_group("follow-ups-answer-comments-recipe"),
]

RECIPE = "follow-ups-answer-comments"
UNANSWERED = 1
#: The board every gathering here reads: the recipe's own default, served by the loopback.
BOARD = tickets.BOARD
#: The repository every ticket here is about, one the loopback knows under the board's owner.
REPOSITORY = f"github.com/{remote.SIBLING_REPOSITORY}"

#: The people commenting, as the board records their authors, and an app.
REVIEWER = "a-reviewer"
MAINTAINER = "a-maintainer"
APP = "dependabot[bot]"

#: What each seeded comment says, so a feedback file can be read for exactly which reached it.
QUIET_OLD = "Asked long before the watermark, and nobody touched this item since.\n"
ANSWERED_THEN_EDITED = "Asked and answered long before the watermark; edited below.\n"
EDITED_AFTER_WATERMARK = "Edited after the watermark: the 2.x line too?\n"
BEFORE_BOUNDARY = "Answered by the run's copy that follows.\n"
ON_A = "Page 9 still never renders — the `cursor` example needs ```page=9```.\n"
ANSWERED_ELSEWHERE = "Answered by another run's reply.\n"
EDITED = "Answered, then edited after the reply.\n"
EDITED_AGAIN = "Edited after the reply: page 10 as well.\n"
ON_DEFERRED = "Deferred, but is this still worth doing?\n"
BY_APP = "Bumps some-library from 1.0 to 1.1.\n"
ON_B = "Does this also hit the nightly sweep?\n"
ON_C = "The run that marked this saw it too — is it one cause?\n"
ELSEWHERE = "Seen on the other host.\n"
ON_TODO = "Accepted already, but one more detail.\n"
ON_GONE = "Nobody here holds this run.\n"
LATER_A = "One more for run A after it answered.\n"
LATER_B = "One more for run B after it answered.\n"
UNANSWERED_A = "This one run A never answers.\n"
PREVIEWED = "Previewed: does the cursor also skip page 11?\n"
PREVIEW_REWRITTEN = "Rewritten after the preview: and page 12?\n"
AFTER_PREVIEW = "Written after the preview was taken.\n"
#: A person's rewrite of an issue's own text, made after the preview.
REWRITTEN_ISSUE = "A person rewrote this issue after the preview."

#: What every reply the answering agent posts says it did.
RESPONSE = "Checked the ticket against that; it stands as written."

#: The comments the answering agent judges to confirm their issue's root cause.
CONFIRMING = (ON_A,)

#: The answering agent's own program, as its task names the commands: read the feedback its
#: run's task quotes; read each quoted issue from the snapshot source the launch configured,
#: by the snapshot id the comment's `Issue` line names, recording what that read answered; and
#: post one reply to each comment, on the issue that holds it, with `--expect` the version that
#: line gives, recording the reply's id, URL and any fallback from that command's own answer.
#: Run in the turn with the prompt log every turn appends to, so it reads the last prompt
#: whose feedback is its own run's: several runs answer at once here.
REPLY_TO_FEEDBACK = """\
import json, os, re, subprocess, sys, tempfile
from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets

log, run, store, confirming, reads = sys.argv[1:6]
confirming = [text.rstrip() for text in json.loads(confirming)]
own = f"/{comments.FEEDBACK_DIRECTORY}/{run}/"
with open(log, encoding="utf-8") as stream:
    prompts = [json.loads(line)["prompt"] for line in stream.read().splitlines()]
task = [prompt for prompt in prompts if own in prompt][-1]
account = re.search(r"account is a JSON document at `([^`]+)`", task)[1]
gathered = re.search(r'"feedback": "([^"]+)"', task)[1]
gathering = tickets.read_gathering(task.split("## The comments to answer", 1)[1])
responses = []
latest = {}
for one, fields, text in zip(
    gathering.quoted, gathering.metadata, gathering.texts, strict=True
):
    verdict = (
        tickets.Verdict.CONFIRMS if text in confirming else tickets.Verdict.DOES_NOT_CONFIRM
    )
    line = comments.ISSUE_VALUE.fullmatch(fields.issue)
    shown = json.loads(
        subprocess.run([store, "task", "show", line["snapshot_id"], "--json"], check=True,
                       capture_output=True, text=True).stdout
    )
    with open(reads, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"run": run, "issue": one.issue, "shown": shown}) + "\\n")
    cause = shown["items"][0]["item"]["metadata"][tickets.KEY]["root_cause"]
    reply = tickets.render_reply(
        run, cause, answers=one.comment, url=fields.url,
        author=None if fields.author == comments.UNKNOWN_AUTHOR else fields.author,
        response=@RESPONSE@, verdict=verdict,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as body:
        body.write(reply)
    # After a reply on the same issue, the version that reply's answer carried.
    version = latest.get(line["issue"], line["version"])
    expect = [] if version == comments.NO_VERSION else ["--expect", version]
    posted = json.loads(
        subprocess.run([store, "task", "comment", "add", line["issue"], "--body-file", body.name,
                        *expect, "--json"], check=True, capture_output=True, text=True).stdout
    )
    if version != comments.NO_VERSION and (posted_result := posted.get("result")):
        # An item changed within the last minute gets no version, so the next reply on it
        # names none either.
        latest[line["issue"]] = posted_result.get("version") or comments.NO_VERSION
    fallback = posted.get("fallback")
    responses.append({
        "comment": one.comment, "issue": one.issue, "action": @RESPONSE@,
        "reply": posted["id"], "reply_url": posted.get("url"),
        "reply_fallback": fallback.get("reason") if isinstance(fallback, dict) else None,
        "verdict": verdict.value,
    })
with open(account, "w", encoding="utf-8") as stream:
    json.dump({"schema": tickets.RESPONSES_SCHEMA, "run": run, "feedback": gathered,
               "responses": responses}, stream, indent=2)
""".replace("@RESPONSE@", repr(RESPONSE))

#: A command a turn runs before anything else: wait until every named launch record exists,
#: then say so in ``witness``. Were the launches one after another, each waiting on the one
#: before it to settle, the first run's turn would wait here until its own bound and say so.
AWAIT_LAUNCHES = """\
import pathlib, sys, time
witness, *records = sys.argv[1:]
deadline = time.monotonic() + 300
while not all(pathlib.Path(record).is_file() for record in records):
    if time.monotonic() > deadline:
        pathlib.Path(witness).write_text("not every launch record appeared", encoding="utf-8")
        sys.exit(1)
    time.sleep(0.5)
pathlib.Path(witness).write_text("every launch record existed", encoding="utf-8")
"""

#: A command a turn runs before answering in the detached phase: wait until the journey says
#: the recipe has returned, so nothing this run does can happen before that.
AWAIT_RELEASE = """\
import pathlib, sys, time
release = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 300
while not release.is_file() and time.monotonic() < deadline:
    time.sleep(0.5)
"""


#: Records every command the recipe hands its plan store, one JSON line apiece, then answers
#: with the pinned store's own output. Installed as the launching checkout's own plan-store
#: CLI, which is the one boundary this suite doubles a store at: no real store reports which
#: commands it was asked.
RECORDING_STORE = """#!@PYTHON@
import fcntl, json, os, subprocess, sys
argv = sys.argv[1:]
done = subprocess.run([os.environ["REAL_PLAN_STORE"], *argv])
# A launch hands the store an environment of its own making, which names no record: what
# is recorded is what the recipe's own steps ask, where the journey named one.
if "STORE_RECORD" in os.environ:
    with open(os.environ["STORE_RECORD"], "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps({"argv": argv, "status": done.returncode}) + "\\n")
sys.exit(done.returncode)
""".replace("@PYTHON@", str(REPO_ROOT / ".venv" / "bin" / "python3"))

#: What a launching checkout does not link from this one: its own toolchain, which it carries
#: instead, and what is this checkout's own state rather than the repository's.
UNLINKED = frozenset({".venv", ".git", ".nx", ".logs", ".env", "scratch"})

#: How many items one page of the board's search answers here, so a narrowed answer spans pages.
PAGE_SIZE = 2


def _mirrored_toolchain(checkout: Path, store: str, *, engine: bool = False) -> Path:
    """``checkout``'s `.venv`: this checkout's locked toolchain with ``store`` as its plan store.

    Every plan-store read of the package runs the `onetaskgraph` beside the interpreter
    that imported the SDK — `orchestrator.plan_store.locked_binary`, which consults no
    search path — so a store standing in front of the pinned one is that file or nothing.
    This checkout's own `.venv/bin/onetaskgraph` is the real install and never a journey's
    to replace, so the mirror is given a virtual environment of its own: the same
    `pyvenv.cfg`, the same `lib` — the SDK, and this package's editable install — and the
    same interpreter, reached through a symlink so `sys.executable` names the mirror's
    `bin`, with ``store`` beside it under the CLI's name. With ``engine``, every other
    installed program is linked beside them too, so a launch from that checkout runs this
    checkout's engine. Returns the store's file.
    """
    venv = checkout / ".venv"
    (venv / "bin").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ".venv" / "pyvenv.cfg", venv / "pyvenv.cfg")
    (venv / "lib").symlink_to(REPO_ROOT / ".venv" / "lib")
    (venv / "bin" / "python3").symlink_to(REPO_ROOT / ".venv" / "bin" / "python3")
    installed = venv / "bin" / ONETASKGRAPH_BIN.name
    if engine:
        for program in (REPO_ROOT / ".venv" / "bin").iterdir():
            if not (venv / "bin" / program.name).exists() and program.name != installed.name:
                (venv / "bin" / program.name).symlink_to(program)
    # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] The published CLI is the one boundary this suite doubles a store at, and every ``store`` installed here delegates every command to the pinned one, recording what it was handed or rewriting one field of one answer: no real store reports which credential reached it or which commands it was asked, and none answers a page cursor twice, so the journeys that need any of those can be driven against nothing else.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    installed.write_text(store, encoding="utf-8")
    installed.chmod(0o755)
    return installed


def _launching_checkout(tmp: Path, store: str = RECORDING_STORE) -> Path:
    """Every file of this checkout, linked, beside a toolchain whose plan store is ``store``."""
    checkout = tmp / "checkout"
    checkout.mkdir()
    for entry in REPO_ROOT.iterdir():
        if entry.name not in UNLINKED:
            (checkout / entry.name).symlink_to(entry)
    _mirrored_toolchain(checkout, store, engine=True)
    return checkout


@contextlib.contextmanager
def _serving(bench: Bench) -> Iterator[None]:
    """The loopback board served as `followups` for every process ``bench`` starts.

    Its route to Hello Patient's Linear goes to an empty local folder instead, its listing is
    paged at :data:`PAGE_SIZE`, and a comment a write adds is dated by the wall clock, which is
    what every boundary and watermark here is reasoned in.
    """
    routed = bench.tmp / "routed-followups"
    routed.mkdir(exist_ok=True)
    for name in [one for one in bench.environment if one.startswith(remote.FOLLOWUPS_ENV_PREFIX)]:
        del bench.environment[name]
    bench.environment.update(
        {
            "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__PLUGIN": plan_store.WRITABLE_PLUGIN,
            "ONETASKGRAPH_SOURCES__ROUTEDFOLLOWUPS__CONFIG__ROOT": str(routed),
            "ONETASKGRAPH_SOURCES__FOLLOWUPS__ROUTES__0__REPOSITORIES": "github.com/petsinc/*",
            "ONETASKGRAPH_SOURCES__FOLLOWUPS__ROUTES__0__TO": "routedfollowups",
            "ONETASKGRAPH_PAGE_SIZE": str(PAGE_SIZE),
            "ONETASKGRAPH_SECRETS_FILE": str(bench.tmp / "no-secrets.env"),
        }
    )
    with remote._serving_followups(bench.environment, fields=True):
        remote.BOARD.real_time = True
        yield


def _row(issue: str) -> remote._Issue:
    """The loopback's own row of ``issue``."""
    (row,) = [one for one in remote.BOARD.issues if one.content_id == issue.partition(":")[2]]
    return row


# llmlint: ignore-block[tests_mirror_real_usage] What a person does on GitHub is the change
# under test, and a person does it on github.com rather than through this host's plan store;
# the loopback's row is where each such act leaves the board. Every read and write the recipe
# makes, before and after, is the locked CLI unchanged.
def _commented(bench: Bench, issue: str, body: str, author: str | None) -> str:
    """A person's comment, or an app's, written on GitHub; its id."""
    del bench
    return str(remote.planted_comment(_row(issue), body, author)["id"])


def _edited(issue: str, identifier: str, body: str) -> None:
    """A person's edit of one comment, made on GitHub."""
    remote.planted_edit(_row(issue), identifier, body)


def _rewritten(issue: str, text: str) -> None:
    """A person's rewrite of the issue's own text, made on GitHub, its record kept."""
    row = _row(issue)
    _, separator, record = row.body.partition("\n\n<!-- onetaskgraph.metadata")
    assert separator, row.body
    row.body = f"{text}{separator}{record}"


# llmlint: ignore-end[tests_mirror_real_usage]


def _marked(bench: Bench, issue: str, body: str) -> str:
    """A run's own comment — evidence or a reply — written through the store's verb; its id."""
    path = bench.tmp / f"comment-{time.monotonic_ns()}.md"
    path.write_text(body, encoding="utf-8")
    added = _store(bench, "task", "comment", "add", issue, "--body-file", str(path))
    return str(added["id"])


def _filed(
    bench: Bench, run: str, cause: str, *, host: str = HOST, again: bool = False
) -> QualifiedTaskId:
    """``run``'s verified ticket for ``cause``, written, copied onto the board and bound; its item.

    Placed and copied through the module's own verbs, as the follow-up agent does: the first
    copy binds the ticket to the item it creates, and a later copy (``again``) follows it.
    """
    path = tickets.ticket_path(bench.drafts_root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    if again:
        # The agent writes its ticket again before it copies it, which is what dates its
        # last response.
        path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        ticket = _ticket(
            run,
            cause,
            (f"drafts:{run}/drafts/a-consumed-draft",),
            f"{remote.SIBLING_REPOSITORY.name}: {cause.replace('-', ' ')}",
            "Verified",
            host,
            estimated=True,
            repository=REPOSITORY,
        )
        path.write_text(tickets.render(ticket), encoding="utf-8")
    for verb in ("board-status", "copy"):
        done = _run(
            [str(REPO_ROOT / ".venv" / "bin" / "python3"), "-m", "orchestrator.follow_up_tickets"]
            + [verb, "--board", BOARD, str(path)],
            bench,
        )
        assert done.returncode == 0, done.stdout + done.stderr
    record = _item(bench, tickets.qualified_id(run, cause))["metadata"]
    assert isinstance(record, dict), record
    return QualifiedTaskId(f"{BOARD}:{record[tickets.KEY][tickets.BINDING_FIELD]}")


def _reply(bench: Bench, run: str, issue: str, answers: str, cause: str) -> str:
    """``run``'s reply to one comment, posted through the store's own verb; its id."""
    (answered,) = [one for one in _comments(bench, issue) if one["id"] == answers]
    author = answered["author"]
    return _marked(
        bench,
        issue,
        tickets.render_reply(
            run,
            cause,
            answers=answers,
            url=str(answered["url"]),
            author=author if isinstance(author, str) else None,
            response="Answered before this journey's gatherings.",
            verdict=tickets.Verdict.DOES_NOT_CONFIRM,
        ),
    )


#: How long GitHub holds back an item's change token after the item last changed.
CHANGE_TOKEN_DELAY = 60


def _settled_on_board() -> None:
    """Wait until every item written so far is past the window GitHub gives no token in."""
    time.sleep(CHANGE_TOKEN_DELAY + 2)


def _next_second() -> None:
    """Wait until the board's whole-second clock has moved past every time written so far."""
    time.sleep(1.1)


class Recorded(NamedTuple):
    """One store command the recipe made, and how it exited."""

    argv: list[str]
    status: int


def _recorded(path: Path) -> list[Recorded]:
    if not path.is_file():
        return []
    return [
        Recorded(held["argv"], held["status"])
        for held in map(json.loads, path.read_text(encoding="utf-8").splitlines())
    ]


class Invocation(NamedTuple):
    """One run of the recipe: its exit, its report, the store's record, and the board's log."""

    result: subprocess.CompletedProcess[str]
    recorded: list[Recorded]
    #: Every request the board was sent while it ran, in order.
    requests: list[remote._GraphQLRequest]

    @property
    def report(self) -> str:
        return self.result.stdout

    @property
    def snapshot(self) -> comments.Snapshot | None:
        """The snapshot its report names, read back."""
        named = [line for line in self.report.splitlines() if line.startswith("snapshot: ")]
        return comments.read_snapshot(Path(named[0].removeprefix("snapshot: "))) if named else None

    def since(self) -> str:
        snapshot = self.snapshot
        assert snapshot is not None, self.report
        return comments.instant(min(one.since for one in snapshot.starts.values()))

    def operations(self) -> list[_Operation]:
        return [request.operation for request in self.requests]


#: A flow's id, a namespace the engine keeps apart from runs'.
FlowId = NewType("FlowId", str)


class Life(NamedTuple):
    """Every phase of one board's life, read back before anything is torn down."""

    bench: Bench
    checkout: Path
    runs: dict[str, tickets.RunId]
    issues: dict[str, QualifiedTaskId]
    #: Each seeded comment's id, by what it says, and each comment's URL on the board.
    ids: dict[str, str]
    urls: dict[str, str]
    #: The reply that answered :data:`ANSWERED_ELSEWHERE`, which run B posted on A's issue.
    elsewhere_reply: str
    watermark_before: bytes
    unrelated_ticket: Path
    unrelated_before: str
    unrelated_mtime: float
    statuses_before: dict[str, object]
    dry: Invocation
    after_dry: dict[str, bytes]
    issue_dry: Invocation
    real: Invocation
    real_started: datetime
    real_ended: datetime
    launched: list[str]
    #: The flows the real gathering left under the runs root, and the flow each run it
    #: launched records in its launch record (empty for none).
    flows: list[FlowId]
    flow_of: dict[tickets.RunId, FlowId]
    witnesses: dict[str, str]
    stored: Stored
    feedback: dict[str, str]
    comments_after_real: dict[str, list[dict[str, object]]]
    statuses_after_real: dict[str, object]
    own_after_real: dict[str, object]
    watermark_after_real: bytes
    again: Invocation
    watermark_after_again: bytes
    scoped: Invocation
    watermark_after_scoped: bytes
    launched_after_scoped: list[str]
    detached: Invocation
    detached_replies_at_return: list[dict[str, object]]
    detached_checked: subprocess.CompletedProcess[str]
    watermark_after_detached: bytes
    unanswered: Invocation
    watermark_after_unanswered: bytes
    reselected: Invocation
    preview: Invocation
    preview_after_dry: dict[str, bytes]
    launched_from: Invocation
    preview_reads: list[dict[str, object]]
    preview_feedback: str
    watermark_after_preview: bytes
    after_preview: Invocation


def _answer(
    bench: Bench,
    checkout: Path,
    record: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> Invocation:
    """`just follow-ups-answer-comments` from ``checkout``, its store recording to ``record``."""
    record.unlink(missing_ok=True)
    before = len(_GitHubFixture.requests)
    result = subprocess.run(  # noqa: S603 - this checkout's own recipe over the installed store
        ["just", RECIPE, *arguments],  # noqa: S607 - `just` from the search path, as an operator invokes it
        cwd=checkout,
        env=(environment or bench.environment) | {"STORE_RECORD": str(record)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )
    return Invocation(result, _recorded(record), list(_GitHubFixture.requests[before:]))


def _replying(bench: Bench, run: str, log: Path, reads: Path) -> list[str]:
    """The answering agent's command posting one reply to each comment its run's task quotes."""
    helper = bench.tmp / "reply-to-feedback.py"
    helper.write_text(REPLY_TO_FEEDBACK, encoding="utf-8")
    return [
        "bash",
        "-c",
        'cd "$1" && exec "$2" "$3" "$4" "$5" "$6" "$7" "$8"',
        "_",
        str(REPO_ROOT),
        str(REPO_ROOT / ".venv" / "bin" / "python3"),
        str(helper),
        str(log),
        run,
        str(ONETASKGRAPH_BIN),
        json.dumps(CONFIRMING),
        str(reads),
    ]


def _python(bench: Bench, name: str, program: str, *arguments: str) -> list[str]:
    helper = bench.tmp / name
    helper.write_text(program, encoding="utf-8")
    return [str(REPO_ROOT / ".venv" / "bin" / "python3"), str(helper), *arguments]


def _scripted(bench: Bench, scripts: dict[str, list[list[str]]]) -> None:
    """Tell the provider's stand-in which commands each run's turn runs, keyed by run id."""
    keyed = Path(bench.environment["FAKE_CODEX_RUN_ON_MARKER"])
    keyed.write_text(json.dumps(scripts), encoding="utf-8")


def _statuses(bench: Bench) -> dict[str, object]:
    """The category every board item is held at, read page by page to the last."""
    found: dict[str, object] = {}
    page: str | None = None
    while True:
        listed = _store(
            bench, "task", "list", "--source", BOARD, *(["--page", page] if page else [])
        )
        items = listed["items"]
        assert isinstance(items, list), listed
        found.update({str(one["id"]): _category(one["item"]) for one in items})
        cursor = listed["next"]
        if cursor is None:
            return found
        assert isinstance(cursor, str), listed
        page = cursor


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _follow_up_runs(bench: Bench) -> list[str]:
    """Every run under the runs root, past the engine's own `.flows`, which holds no run."""
    if not bench.runs.is_dir():
        return []
    return sorted(path.name for path in bench.runs.iterdir() if path.name != ".flows")


def _watermark(bench: Bench) -> bytes:
    path = comments.watermark_path(bench.drafts_root, BOARD)
    return path.read_bytes() if path.is_file() else b""


def _urls() -> dict[str, str]:
    """Every comment's URL on the board, by its id."""
    return {
        str(comment["id"]): str(comment["url"])
        for issue in remote.BOARD.issues
        for comment in issue.comments
    }


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling-follow-ups`
# is already the Nx project edge this repository keeps for follow-up journeys that launch the
# installed engine, keyed on `planToolingFollowUpsWorkspace`, which covers every file these
# launches read. The fixture is module-scoped and spends seven launches whose turns are the
# provider's stand-in, and every test down to this block's end reads that one fixture rather
# than launching.
@pytest.fixture(scope="module")
def life(tmp_path_factory: pytest.TempPathFactory) -> Life:  # noqa: PLR0915 - one board's life, phase by phase
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("follow-ups-answer-comments")
    bench = _bench(tmp)
    checkout = _launching_checkout(tmp)
    record = tmp / "store-record.jsonl"
    log = tmp / "prompts.jsonl"
    reads = tmp / "agent-reads.jsonl"
    bench.environment.update(
        {
            # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
            "FAKE_CODEX_RUN_ON_MARKER": str(tmp / "commands.json"),
            "FAKE_CODEX_RUN_ON_MARKER_LOG": str(tmp / "commands-ran.jsonl"),
            "FAKE_CODEX_PROMPT_LOG": str(log),
            # The launching checkout's `uv run` would otherwise sync the environment it
            # links, which is this checkout's own and never a journey's to rewrite.
            "UV_NO_SYNC": "1",
        }
    )
    pid = os.getpid()
    runs = {
        "A": tickets.RunId(f"ac-a-{pid}"),
        "B": tickets.RunId(f"ac-B-{pid}"),
        "C": tickets.RunId(f"ac-c-{pid}"),
        "G": tickets.RunId(f"ac-g-{pid}"),
    }
    started: list[str] = []
    with _serving(bench):
        try:
            issues = {
                "a": _filed(bench, runs["A"], "listing-cursor-skips-last-page"),
                "a-deferred": _filed(bench, runs["A"], "export-drops-a-column"),
                "a-todo": _filed(bench, runs["A"], "retry-loop-never-backs-off"),
                "a-answered": _filed(bench, runs["A"], "library-parses-dates-wrong"),
                "a-elsewhere": _filed(
                    bench, runs["A"], "sweep-follows-a-symlink", host="other-host"
                ),
                "b": _filed(bench, runs["B"], "sweep-trailer-omits-a-family"),
                "c": _filed(bench, runs["C"], "cache-key-ignores-the-locale"),
                "c-quiet": _filed(bench, runs["C"], "cache-evicts-too-early"),
                "g": _filed(bench, runs["G"], "listing-export-drops-a-row"),
            }
            # Run G's records are gone from this host; its board issue outlived them.
            shutil.rmtree(bench.drafts_root / "tasks" / runs["G"])
            # A ticket of run A that no comment names and that `validate` refuses: a dispatch
            # or a recipe still ranging over the run's tickets reads, rewrites or refuses it.
            unrelated_ticket = tickets.ticket_path(bench.drafts_root, runs["A"], "an-unsound-cause")
            unrelated_ticket.write_text(
                tickets.render(
                    _ticket(runs["A"], "an-unsound-cause", (), "some-service: x", "Left", HOST)
                ).replace('status: "backlog"', 'status: "backlog"\nproject: "a-project"'),
                encoding="utf-8",
            )
            unrelated_before = unrelated_ticket.read_text(encoding="utf-8")
            unrelated_mtime = unrelated_ticket.stat().st_mtime
            _moved(bench, issues["a-deferred"], tickets.Status.DEFERRED.value)
            _moved(bench, issues["a-todo"], tickets.Status.ACCEPTED.value)
            # A person sets the priority of run A's proposal by hand, which no re-estimate
            # rewrites.
            _store(bench, "task", "priority", "set", issues["a"], HELD_PRIORITY)
            ids: dict[str, str] = {}
            ids[QUIET_OLD] = _commented(bench, issues["c-quiet"], QUIET_OLD, REVIEWER)
            answered = issues["a-answered"]
            ids[ANSWERED_THEN_EDITED] = _commented(
                bench, answered, ANSWERED_THEN_EDITED, MAINTAINER
            )
            _reply(
                bench, runs["A"], answered, ids[ANSWERED_THEN_EDITED], "library-parses-dates-wrong"
            )
            _next_second()
            # The watermark an earlier complete gathering left, between those and what
            # follows.
            # llmlint: ignore[tests_mirror_real_usage] A gathering the recipe completes leaves its watermark `OVERLAP` — fifteen minutes — behind its own query, so the only way to reach one that excludes a comment written here is to wait that long; this writes the file an earlier gathering would have left with the module's own writer, which is the recipe's only writer of it.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
            comments.write_watermark(
                bench.drafts_root,
                BOARD,
                datetime.now(UTC).replace(microsecond=0) + comments.OVERLAP,
            )
            watermark_before = _watermark(bench)
            _next_second()
            # Run A's last response: an evidence comment on run C's issue.
            marker = tickets.render_comment(runs["A"], "cache-key-ignores-the-locale", "Seen in A.")
            ids[marker] = _marked(bench, issues["c"], marker)
            ids[BEFORE_BOUNDARY] = _commented(bench, issues["c"], BEFORE_BOUNDARY, REVIEWER)
            _next_second()
            # Run C's last response: its ticket copied again, after that comment.
            _filed(bench, runs["C"], "cache-key-ignores-the-locale", again=True)
            _next_second()
            ids[ON_A] = _commented(bench, issues["a"], ON_A, REVIEWER)
            ids[ANSWERED_ELSEWHERE] = _commented(bench, issues["a"], ANSWERED_ELSEWHERE, REVIEWER)
            elsewhere_reply = _reply(
                bench,
                runs["B"],
                issues["a"],
                ids[ANSWERED_ELSEWHERE],
                "listing-cursor-skips-last-page",
            )
            ids[EDITED] = _commented(bench, issues["a"], EDITED, MAINTAINER)
            _reply(bench, runs["A"], issues["a"], ids[EDITED], "listing-cursor-skips-last-page")
            ids[ON_DEFERRED] = _commented(bench, issues["a-deferred"], ON_DEFERRED, REVIEWER)
            ids[BY_APP] = _commented(bench, issues["a"], BY_APP, APP)
            ids[ON_B] = _commented(bench, issues["b"], ON_B, MAINTAINER)
            ids[ON_C] = _commented(bench, issues["c"], ON_C, REVIEWER)
            ids[ELSEWHERE] = _commented(bench, issues["a-elsewhere"], ELSEWHERE, REVIEWER)
            ids[ON_TODO] = _commented(bench, issues["a-todo"], ON_TODO, REVIEWER)
            ids[ON_GONE] = _commented(bench, issues["g"], ON_GONE, REVIEWER)
            _next_second()
            _edited(issues["a"], ids[EDITED], EDITED_AGAIN)
            _edited(answered, ids[ANSWERED_THEN_EDITED], EDITED_AFTER_WATERMARK)
            statuses_before = _statuses(bench)

            dry = _answer(bench, checkout, record, "--dry-run")
            after_dry = _snapshot(bench.drafts_root)
            issue_dry = _answer(bench, checkout, record, "--issue", issues["a"], "--dry-run")

            # Every run's turn waits for all three launch records before it answers.
            records = [
                str(bench.runs / f"{runs[key]}{SUFFIX}" / "launch.json") for key in ("A", "B", "C")
            ]
            _scripted(
                bench,
                {
                    runs[key]: [
                        _python(
                            bench,
                            "await-launches.py",
                            AWAIT_LAUNCHES,
                            str(tmp / f"witness-{key}"),
                            *records,
                        ),
                        _replying(bench, runs[key], log, reads),
                    ]
                    for key in ("A", "B", "C")
                },
            )
            real_started = datetime.now(UTC)
            real = _answer(bench, checkout, record)
            real_ended = datetime.now(UTC)
            launched = _follow_up_runs(bench)
            started.extend(launched)
            feedback = {
                key: "\n".join(
                    path.read_text(encoding="utf-8")
                    for path in sorted(
                        (bench.drafts_root / comments.FEEDBACK_DIRECTORY / runs[key]).glob("2*.md")
                    )
                )
                for key in ("A", "B", "C")
            }
            stored = _stored(bench, f"{runs['B'].lower()}{SUFFIX}")
            comments_after_real = {key: _comments(bench, issue) for key, issue in issues.items()}
            statuses_after_real = _statuses(bench)
            own_after_real = _item(bench, issues["a"])
            watermark_after_real = _watermark(bench)

            # With every comment answered: nothing selected, nothing launched.
            again = _answer(bench, checkout, record)
            watermark_after_again = _watermark(bench)

            # A comment for run A and one for run B, and a gathering narrowed to run A.
            _next_second()
            ids[LATER_A] = _commented(bench, issues["a"], LATER_A, REVIEWER)
            ids[LATER_B] = _commented(bench, issues["b"], LATER_B, REVIEWER)
            _scripted(bench, {runs["A"]: [_replying(bench, runs["A"], log, reads)]})
            scoped = _answer(bench, checkout, record, "--run", runs["A"])
            watermark_after_scoped = _watermark(bench)
            launched_after_scoped = _follow_up_runs(bench)
            started.extend(set(launched_after_scoped) - set(started))

            # Detached: run B's comment, answered only once the journey says the recipe
            # returned.
            release = tmp / "release-detached"
            _scripted(
                bench,
                {
                    runs["B"]: [
                        _python(bench, "await-release.py", AWAIT_RELEASE, str(release)),
                        _replying(bench, runs["B"], log, reads),
                    ]
                },
            )
            detached = _answer(bench, checkout, record, "--detach")
            detached_replies_at_return = [
                one
                for one in _comments(bench, issues["b"])
                if (owner := tickets.comment_owner(str(one["body"]))) is not None
                and owner.answers == ids[LATER_B]
            ]
            release.write_text("returned", encoding="utf-8")
            started.extend(set(_follow_up_runs(bench)) - set(started))
            for follow_up in set(_follow_up_runs(bench)) - set(launched_after_scoped):
                _run(["just", "watch", follow_up, "--until", "settled"], bench)
            printed = re.search(r"once it settles, check it with: (.+)$", detached.report, re.M)
            detached_checked = _run(shlex.split(printed[1]) if printed else ["false"], bench)
            watermark_after_detached = _watermark(bench)

            # A run whose dispatch answers nothing: its comment is selected again after it.
            _next_second()
            ids[UNANSWERED_A] = _commented(bench, issues["a"], UNANSWERED_A, REVIEWER)
            _scripted(bench, {runs["A"]: [["true"]]})
            unanswered = _answer(bench, checkout, record)
            started.extend(set(_follow_up_runs(bench)) - set(started))
            watermark_after_unanswered = _watermark(bench)
            reselected = _answer(bench, checkout, record, "--dry-run")

            # The preview: a dry run's snapshot, launched with `--from` after the board moved
            # under it — a new comment, the previewed comment rewritten, the issue's own text
            # rewritten. The comment run A left unanswered is answered here too.
            _next_second()
            ids[PREVIEWED] = _commented(bench, issues["a"], PREVIEWED, REVIEWER)
            # GitHub gives no change token for an item changed within the last minute, so the
            # preview waits that out: its snapshot then holds the item at a settled version,
            # which every reply launched from it expects.
            _settled_on_board()
            preview = _answer(bench, checkout, record, "--dry-run")
            preview_after_dry = _snapshot(bench.drafts_root)
            _next_second()
            ids[AFTER_PREVIEW] = _commented(bench, issues["a"], AFTER_PREVIEW, MAINTAINER)
            _edited(issues["a"], ids[PREVIEWED], PREVIEW_REWRITTEN)
            _rewritten(issues["a"], REWRITTEN_ISSUE)
            reads.unlink(missing_ok=True)
            _scripted(bench, {runs["A"]: [_replying(bench, runs["A"], log, reads)]})
            printed = re.search(r"^launch exactly this with: (.+)$", preview.report, re.M)
            assert printed is not None, preview.report
            launched_from = _answer(bench, checkout, record, *shlex.split(printed[1])[2:])
            started.extend(set(_follow_up_runs(bench)) - set(started))
            preview_reads = [
                json.loads(line) for line in reads.read_text(encoding="utf-8").splitlines()
            ]
            (preview_feedback,) = [
                path.read_text(encoding="utf-8")
                for path in (bench.drafts_root / comments.FEEDBACK_DIRECTORY / runs["A"]).glob(
                    "2*.md"
                )
                if comments.recorded_snapshot(path.read_text(encoding="utf-8"))
                == (preview.snapshot.directory if preview.snapshot else None)
            ]
            watermark_after_preview = _watermark(bench)
            after_preview = _answer(bench, checkout, record, "--dry-run")
            urls = _urls()

            return Life(
                bench=bench,
                checkout=checkout,
                runs=runs,
                issues=issues,
                ids=ids,
                urls=urls,
                elsewhere_reply=elsewhere_reply,
                watermark_before=watermark_before,
                unrelated_ticket=unrelated_ticket,
                unrelated_before=unrelated_before,
                unrelated_mtime=unrelated_mtime,
                statuses_before=statuses_before,
                dry=dry,
                after_dry=after_dry,
                issue_dry=issue_dry,
                real=real,
                real_started=real_started,
                real_ended=real_ended,
                launched=launched,
                flows=sorted(FlowId(path.name) for path in (bench.runs / ".flows").glob("*")),
                flow_of={
                    tickets.RunId(run): FlowId(
                        str(
                            json.loads((bench.runs / run / "launch.json").read_text("utf-8")).get(
                                "flow", ""
                            )
                        )
                    )
                    for run in launched
                },
                witnesses={
                    key: (tmp / f"witness-{key}").read_text(encoding="utf-8")
                    if (tmp / f"witness-{key}").is_file()
                    else ""
                    for key in ("A", "B", "C")
                },
                stored=stored,
                feedback=feedback,
                comments_after_real=comments_after_real,
                statuses_after_real=statuses_after_real,
                own_after_real=own_after_real,
                watermark_after_real=watermark_after_real,
                again=again,
                watermark_after_again=watermark_after_again,
                scoped=scoped,
                watermark_after_scoped=watermark_after_scoped,
                launched_after_scoped=launched_after_scoped,
                detached=detached,
                detached_replies_at_return=detached_replies_at_return,
                detached_checked=detached_checked,
                watermark_after_detached=watermark_after_detached,
                unanswered=unanswered,
                watermark_after_unanswered=watermark_after_unanswered,
                reselected=reselected,
                preview=preview,
                preview_after_dry=preview_after_dry,
                launched_from=launched_from,
                preview_reads=preview_reads,
                preview_feedback=preview_feedback,
                watermark_after_preview=watermark_after_preview,
                after_preview=after_preview,
            )
        finally:
            for run in started:
                if (bench.runs / run).exists():
                    _run(["just", "stop", run], bench)


def _named(report: str) -> Counter[str]:
    """Every comment URL a report names as selected or left out, with how often."""
    return Counter(
        line.split(": ", 1)[1].split(" on ", 1)[0]
        for line in report.splitlines()
        if line.startswith(("selected: ", "left out: "))
    )


def _line_for(report: str, url: str) -> str:
    (line,) = [line for line in report.splitlines() if f" {url} on " in line]
    return line


def _said(life: Life, invocation: Invocation, text: str) -> str:
    """The line ``invocation`` reports the comment that says ``text`` on."""
    return _line_for(invocation.report, life.urls[life.ids[text]])


def _settled(life: Life) -> None:
    assert life.real.result.returncode == OK, (
        life.real.report + life.real.result.stderr + _ran(life.bench)
    )


def _searches(invocation: Invocation) -> list[str]:
    """Every board search ``invocation`` sent, as the query string GitHub was asked."""
    return [
        request.string("search")
        for request in invocation.requests
        if request.operation is _Operation.SEARCH
    ]


#: Every write a pass makes: the agent's replies and settle's field-scoped writes.
WRITES = frozenset(
    {
        _Operation.ADD_COMMENT_WHOLE,
        _Operation.UPDATE_ISSUE_WHOLE,
        _Operation.UPDATE_FIELD_WHOLE,
        _Operation.CLEAR_FIELD_WHOLE,
    }
)


def test_every_board_read_a_gathering_makes_is_narrowed_to_this_hosts_items(life: Life) -> None:
    """The copy's search names this host and the board alone, and nothing it did not return is
    read: no comment listing of its own, no item the search did not answer."""
    _settled(life)
    for invocation in (life.dry, life.real, life.again, life.detached):
        searches = _searches(invocation)
        assert searches, invocation.report
        for search in searches:
            assert search.startswith(f"project:{remote.CONFIGURED_REPOSITORY.owner}/"), search
            assert f'"{HOST}"' in search, search
        assert _Operation.COMMENTS not in invocation.operations()
        snapshot = invocation.snapshot
        assert snapshot is not None
        # The quiet item nobody touched since the planted watermark is never copied by the
        # gatherings that start there; the later ones reach back over it.
        if invocation in (life.dry, life.real):
            assert life.issues["c-quiet"] not in {one.id for one in snapshot.items}


def test_the_dry_run_names_where_each_comment_would_go_and_why_the_rest_are_left_out(
    life: Life,
) -> None:
    dry = life.dry
    assert dry.result.returncode == OK, dry.report + dry.result.stderr
    runs, issues = life.runs, life.issues

    def said(text: str) -> str:
        return _said(life, dry, text)

    assert said(ON_A).endswith(f"would go to run {runs['A']}")
    assert said(EDITED).endswith(f"would go to run {runs['A']}")
    assert said(ON_DEFERRED).endswith(f"would go to run {runs['A']}")
    assert said(ANSWERED_THEN_EDITED).endswith(f"would go to run {runs['A']}")
    assert said(ON_B).endswith(f"would go to run {runs['B']}")
    assert said(ON_C).endswith(f"would go to run {runs['C']}")
    assert said(ANSWERED_ELSEWHERE).endswith(f": answered by reply {life.elsewhere_reply}")
    marker = next(text for text in life.ids if "Seen in A." in text)
    assert said(marker).endswith(f": marked by run {runs['A']}")
    # Verified on another host, so the store's narrowing never returned it to be read.
    assert issues["a-elsewhere"] not in dry.report
    assert said(ON_TODO).endswith(": item at Todo")
    assert said(BY_APP).endswith(f": bot author {APP}")
    assert f"at or before run {runs['C']}'s boundary " in said(BEFORE_BOUNDARY)
    assert said(ON_GONE).endswith(f": no records for owning run {runs['G']} here")
    assert life.urls[life.ids[QUIET_OLD]] not in dry.report
    assert issues["c-quiet"] not in dry.report


def test_the_dry_run_writes_its_snapshot_and_nothing_else(life: Life) -> None:
    """The snapshot is the one thing a dry run writes: no feedback file, no `boundary.txt`, no
    watermark, nothing on the board — and it ends naming the `--from` that launches it."""
    dry = life.dry
    assert dry.result.returncode == OK, dry.report + dry.result.stderr
    snapshot = dry.snapshot
    assert snapshot is not None and snapshot.dry_run
    written = [path for path in life.after_dry if Path(path).is_relative_to(snapshot.directory)]
    rest = {path for path in life.after_dry if path not in written}
    assert not [path for path in rest if "/feedback/" in path and path.endswith(".md")]
    assert not any(comments.BOUNDARY_FILE in path for path in life.after_dry)
    assert life.after_dry[str(comments.watermark_path(life.bench.drafts_root, BOARD))] == (
        life.watermark_before
    )
    assert comments.launched_record(snapshot.directory) is None, "nothing launched the preview"
    assert "wrote run" not in dry.report
    assert [one for one in dry.operations() if one in WRITES] == []
    assert dry.report.splitlines()[-1] == (
        f"launch exactly this with: just {RECIPE} --from {snapshot.directory}"
    )


@pytest.mark.parametrize("phase", ["dry", "real"])
def test_each_report_names_every_comment_its_snapshot_holds_once_and_names_the_store_it_read(
    life: Life, phase: str
) -> None:
    invocation = life.dry if phase == "dry" else life.real
    assert invocation.result.returncode == OK, invocation.report + invocation.result.stderr
    named = _named(invocation.report)
    assert set(named.values()) == {1}, named
    snapshot = invocation.snapshot
    assert snapshot is not None
    held = Counter(
        comments.comment_url(issue, comment)
        for issue in comments.snapshot_issues(snapshot)
        for comment in issue.comments
    )
    assert named == held, (named, held)
    version = subprocess.run(  # noqa: S603 - the pinned store this checkout installs
        [str(ONETASKGRAPH_BIN), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    store = (
        f"plan store: {version}; the narrowed query returned {len(snapshot.items)} item(s) "
        f"commented on since {invocation.since()}"
    )
    lines = invocation.report.splitlines()
    assert (lines[-2] if phase == "dry" else lines[-1]) == store, lines[-3:]


def test_the_real_gathering_launches_one_run_per_owning_run_all_at_once(life: Life) -> None:
    _settled(life)
    runs = life.runs
    assert life.launched == sorted(f"{runs[key]}{SUFFIX}" for key in ("A", "B", "C"))
    # Each run's turn waited until all three launch records existed before it answered.
    assert life.witnesses == dict.fromkeys(("A", "B", "C"), "every launch record existed")
    assert life.stored.metadata["onepipeline.id"] == "follow-ups"
    assert life.stored.checked.returncode == OK, life.stored.checked.stderr


def test_the_gathering_and_every_run_it_launches_are_one_flow(life: Life) -> None:
    """The whole gathering holds its session as one flow, its launches joined to it.

    Each `scripts/follow-ups.sh` it starts enters a flow too, and runs in the one already
    entered rather than nesting, so the manager arms one watch for all of it.
    """
    _settled(life)
    (flow,) = set(life.flow_of.values())
    assert flow.startswith("follow-ups-answer-comments") and flow in life.flows, life.flow_of
    assert sorted(life.flow_of) == life.launched, life.flow_of


def test_each_feedback_file_quotes_only_its_own_runs_comments(life: Life) -> None:
    _settled(life)
    quoted = {
        key: {one.comment for one in tickets.quoted_comments(text)}
        for key, text in life.feedback.items()
    }
    ids = life.ids
    assert quoted == {
        "A": {ids[ON_A], ids[EDITED], ids[ON_DEFERRED], ids[ANSWERED_THEN_EDITED]},
        "B": {ids[ON_B]},
        "C": {ids[ON_C]},
    }
    assert EDITED_AGAIN.rstrip() in life.feedback["A"]
    assert EDITED_AFTER_WATERMARK.rstrip() in life.feedback["A"]


def test_every_quoted_comment_carries_exactly_one_reply_and_each_run_checked_sound(
    life: Life,
) -> None:
    _settled(life)
    report = life.real.report
    assert "verified on the board with one batched read of " in report
    for key, text in life.feedback.items():
        run = life.runs[key]
        for one in tickets.quoted_comments(text):
            issue = next(name for name, held in life.issues.items() if held == one.issue)
            replies = [
                held
                for held in life.comments_after_real[issue]
                if (owner := tickets.comment_owner(str(held["body"]))) is not None
                and owner.run == run
                and owner.answers == one.comment
                and str(held["body"]).count(RESPONSE)
            ]
            assert len(replies) == 1, (one, replies)
            block = report.split(f"run {run} (follow-up run ", 1)[1]
            assert block.split("\n", 1)[0].endswith("check-responses passed"), block
            assert f"  reply: {replies[0]['url']}" in block.split("\nrun ", 1)[0], block


def _target(request: remote._GraphQLRequest) -> str:
    """The issue an item read or a reply names, by its content id, as the loopback received it."""
    if request.operation is _Operation.ADD_COMMENT:
        payload = request.variables.get("input")
        assert isinstance(payload, dict), request.variables
        return str(payload.get("subjectId"))
    return str(request.variables.get("id"))


def _unread_reply(sent: list[tuple[_Operation, str]]) -> str | None:
    """The first reply in ``sent`` whose own issue had no unspent read before it, if any.

    Each read is spent by one reply to the same issue, so a read of one issue never stands in
    for a reply to another, however the launched runs interleave; every read is spent by the end.
    """
    unspent: Counter[str] = Counter()
    for operation, target in sent:
        if operation is _Operation.ISSUE:
            unspent[target] += 1
        elif unspent[target] == 0:
            return f"a reply to {target} with no read of {target} before it"
        else:
            unspent[target] -= 1
    left = +unspent
    return f"reads no reply spent: {dict(left)}" if left else None


def test_a_pass_over_items_changed_within_the_minute_reads_each_before_its_reply(
    life: Life,
) -> None:
    """The no-version path, counted exactly: the copy, then each reply's own item read.

    Every quoted item here was commented on in the second before the gathering, and GitHub
    gives no change token for an item changed within the last minute, so the snapshot holds
    none of them at a version. Each reply is then sent with no `--expect`, which the store
    answers by reading the item before its plain write. After the replies, settle's one batched
    read, then its one write — the confirming reply's estimate line, on an item the replies had
    just changed, so read first as well.
    """
    _settled(life)
    snapshot = life.real.snapshot
    assert snapshot is not None
    quoted = [one.issue for text in life.feedback.values() for one in tickets.quoted_comments(text)]
    assert all(held.version is None for held in snapshot.items if held.id in set(quoted)), (
        snapshot.items
    )
    operations = life.real.operations()
    head = [_Operation.SEARCH, _Operation.ISSUE_COPY_DETAILS]
    tail = [_Operation.ISSUE_DETAILS, _Operation.ISSUE, _Operation.UPDATE_ISSUE]
    assert operations[: len(head)] == head, operations
    assert operations[-len(tail) :] == tail, operations
    # The launched runs are dispatched at once, so one run's read may land between another's
    # read and its reply: the replies are counted exactly, and each is preceded by a read of
    # its own issue, paired off the loopback's log by the issue each request names.
    sent = [
        (request.operation, _target(request))
        for request in life.real.requests[len(head) : -len(tail)]
    ]
    assert sorted(operation for operation, _ in sent) == sorted(
        [_Operation.ISSUE, _Operation.ADD_COMMENT] * len(quoted)
    ), operations
    assert _unread_reply(sent) is None, (_unread_reply(sent), sent)
    # The pairing has teeth on this very log: one read retargeted to another replied issue
    # keeps every count, yet leaves a reply whose own issue was never read before it.
    replied = sorted({target for operation, target in sent if operation is _Operation.ADD_COMMENT})
    assert len(replied) > 1, sent
    first = next(
        index for index, (operation, _) in enumerate(sent) if operation is _Operation.ISSUE
    )
    other = next(target for target in replied if target != sent[first][1])
    mismatched = [*sent[:first], (_Operation.ISSUE, other), *sent[first + 1 :]]
    assert _unread_reply(mismatched) is not None, mismatched


def test_the_narrow_dispatch_leaves_the_rest_of_the_board_and_an_unrelated_ticket_alone(
    life: Life,
) -> None:
    _settled(life)
    assert life.statuses_after_real == life.statuses_before
    assert life.unrelated_ticket.read_text(encoding="utf-8") == life.unrelated_before
    assert life.unrelated_ticket.stat().st_mtime == life.unrelated_mtime
    # The confirming reply re-estimated its issue, and the person's priority stands.
    assert life.own_after_real["priority"] == HELD_PRIORITY
    held = life.own_after_real["metadata"]
    assert isinstance(held, dict) and held[tickets.KEY][tickets.ESTIMATE_FIELD] is not None


def test_a_complete_gathering_advances_the_watermark_and_the_next_one_starts_there(
    life: Life,
) -> None:
    _settled(life)
    before = json.loads(life.watermark_before)
    snapshot = life.real.snapshot
    assert snapshot is not None
    assert life.real_started.replace(microsecond=0) <= snapshot.queried_at <= life.real_ended
    queried = snapshot.queried_at - comments.OVERLAP
    held = json.loads(life.watermark_after_real)
    # It moves to the query less the overlap, never back past the watermark it started from.
    expected = max(
        comments.moment(before["since"], "the watermark"), queried.replace(microsecond=0)
    )
    assert held["since"] == comments.instant(expected), (held, before)
    said = "advanced to" if expected > comments.moment(before["since"], "it") else "kept at"
    assert f"watermark: {said} {held['since']} in " in life.real.report
    again = life.again.snapshot
    assert again is not None
    assert comments.instant(again.starts[BOARD].since) == held["since"]


def test_a_second_gathering_selects_nothing_launches_nothing_and_says_so(life: Life) -> None:
    _settled(life)
    again = life.again
    assert again.result.returncode == OK, again.report + again.result.stderr
    assert "nothing to answer: no comment was selected, so nothing was launched" in again.report
    assert "selected: " not in again.report
    assert "launched follow-up run" not in again.report
    assert "wrote run" not in again.report


# llmlint: ignore-block[shell_test_tiers_stay_split] Its tier is the module's: it drives the
# recipe every journey here drives, from the `plan-tooling-follow-ups` project whose
# `planToolingFollowUpsWorkspace` key already covers what it reads, so a project of its own would
# split one recipe's journeys across tiers behind no narrower key.
def test_a_run_scoped_gathering_launches_only_that_run_and_leaves_the_watermark(
    life: Life,
) -> None:
    scoped = life.scoped
    assert scoped.result.returncode == OK, scoped.report + scoped.result.stderr + _ran(life.bench)
    runs = life.runs
    assert set(life.launched_after_scoped) - set(life.launched) == {f"{runs['A']}{SUFFIX}-2"}
    owners = {issue: key[0].upper() for key, issue in life.issues.items()}
    for line in scoped.report.splitlines():
        if line.startswith(("selected: ", "left out: ")):
            issue = line.split(" on ", 1)[1].split(" ", 1)[0].rstrip(":")
            assert owners[QualifiedTaskId(issue)] == "A", line
    assert life.urls[life.ids[LATER_B]] not in scoped.report
    assert life.watermark_after_scoped == life.watermark_after_again
    assert f"the gathering was scoped to run {runs['A']}'s comments" in scoped.report
    # Each item run A's tickets are bound to, copied by id: the board is searched nowhere.
    assert _searches(scoped) == []
    snapshot = scoped.snapshot
    assert snapshot is not None
    bound = {issue for key, issue in life.issues.items() if key.startswith("a")}
    assert {one.id for one in snapshot.items} == bound, snapshot.items


# llmlint: ignore-end[shell_test_tiers_stay_split]


# llmlint: ignore-block[shell_test_tiers_stay_split] The task this journey answers names
# this module as where `--issue` is driven through the recipe, and its tier is the module's:
# it drives the recipe every journey here drives, from the `plan-tooling-follow-ups` project whose
# `planToolingFollowUpsWorkspace` key already covers what it reads, so a project of its own would
# split one recipe's journeys across tiers behind no narrower key.
def test_an_issue_scoped_dry_run_reads_and_selects_only_that_issue(life: Life) -> None:
    """`--issue <id> --dry-run`: that one item and its comments, copied by id with no search,
    and the comments on it an unscoped dry run selects, and nothing else."""
    issue_dry = life.issue_dry
    assert issue_dry.result.returncode == OK, issue_dry.report + issue_dry.result.stderr
    own = life.issues["a"]
    named = [
        line
        for line in issue_dry.report.splitlines()
        if line.startswith(("selected: ", "left out: "))
    ]
    assert named, issue_dry.report
    assert all(f" on {own}" in line for line in named), named
    selected = [line for line in named if line.startswith("selected: ")]
    assert selected == [
        line
        for line in life.dry.report.splitlines()
        if line.startswith("selected: ") and f" on {own} " in line
    ], (selected, life.dry.report)
    assert {line.split(" on ", 1)[0] for line in selected} == {
        f"selected: {life.urls[life.ids[text]]}" for text in (ON_A, EDITED)
    }
    assert _searches(issue_dry) == []
    snapshot = issue_dry.snapshot
    assert snapshot is not None and [one.id for one in snapshot.items] == [own]
    assert "wrote run" not in issue_dry.report
    assert issue_dry.report.splitlines()[-2].endswith(
        f"read 1 item(s) run {life.runs['A']}'s tickets are bound to, each directly, and listed "
        "none"
    ), issue_dry.report


# llmlint: ignore-end[shell_test_tiers_stay_split]


def test_a_detached_gathering_returns_before_its_run_settles_naming_how_to_check_it(
    life: Life,
) -> None:
    detached = life.detached
    assert detached.result.returncode == OK, detached.report + detached.result.stderr
    run = life.runs["B"]
    # The comment the run-scoped gathering left waiting is selected, and only it.
    assert _said(life, detached, LATER_B).endswith(f"goes to run {run}")
    assert life.detached_replies_at_return == [], "the run answered before the recipe returned"
    follow_up = f"{run}{SUFFIX}-2"
    assert (life.bench.runs / follow_up / "launch.json").is_file()
    assert f"run {run}: launched follow-up run {follow_up}; watch it with: just watch " in (
        detached.report
    )
    assert "check it with: " in detached.report
    assert life.detached_checked.returncode == OK, (
        life.detached_checked.stdout + life.detached_checked.stderr + _ran(life.bench)
    )
    assert life.watermark_after_detached == life.watermark_after_again


def test_a_comment_its_run_did_not_answer_is_selected_again_and_the_watermark_holds(
    life: Life,
) -> None:
    unanswered = life.unanswered
    assert unanswered.result.returncode == UNANSWERED, unanswered.report + unanswered.result.stderr
    run = life.runs["A"]
    assert f"run {run} (follow-up run {run}{SUFFIX}-3): check-responses failed" in (
        unanswered.report
    )
    assert "watermark: left as it was, because a launched run was not answered soundly" in (
        unanswered.report
    )
    assert life.watermark_after_unanswered == life.watermark_after_again
    assert _said(life, life.reselected, UNANSWERED_A).endswith(f"would go to run {run}")


def test_a_preview_launched_from_its_snapshot_answers_exactly_what_it_showed(life: Life) -> None:
    """The dry run's snapshot is what is answered, whatever the board did after it.

    After the preview a person wrote a new comment, rewrote the previewed comment and rewrote
    the issue's own text. The launch from the snapshot is not refused, and from the start of
    `--from` to the first launched turn's reply it asks the board nothing at all. The feedback
    quotes the previewed comment as the preview showed it, the agent reads it and the issue that way
    from the snapshot source, and the new comment is quoted nowhere. A sound launch of the
    unscoped snapshot moves the watermark to its query less the overlap, and the next gathering
    selects the new comment and the rewritten one.
    """
    preview, launched = life.preview, life.launched_from
    assert preview.result.returncode == OK, preview.report + preview.result.stderr
    # The person's rewrite of the issue since the preview withholds the ticket's write, which
    # the pass ends naming rather than reading as done.
    assert launched.result.returncode == UNANSWERED, (
        launched.report + launched.result.stderr + _ran(life.bench)
    )
    assert "ticket-copy withheld: the board's content changed since the snapshot" in (
        launched.report
    )
    assert "needs attention: 1 write(s) pending in " in launched.report
    snapshot = preview.snapshot
    assert snapshot is not None
    assert [one for one in preview.operations() if one in WRITES] == []
    assert not any(
        Path(path).name == f"{snapshot.directory.name}.md" for path in life.preview_after_dry
    ), "the dry run wrote a feedback file"
    # Nothing reached the board from `--from` to the first reply a launched turn posted.
    operations = launched.operations()
    # The preview waited out the minute GitHub holds a change token back for, so its snapshot
    # holds the item at a settled version: the first reply expects it, and nothing is asked of
    # the board before it. That reply changes the item, so its answer carries no version, and
    # the second reply on the item names none: the store reads the item, then writes plainly.
    # Then settle's one batched read, and no write, its ticket's being withheld.
    (held,) = [one for one in snapshot.items if one.id == life.issues["a"]]
    assert held.version is not None, snapshot.items
    assert operations == [
        _Operation.ADD_COMMENT_WHOLE,
        _Operation.ISSUE,
        _Operation.ADD_COMMENT,
        _Operation.ISSUE_COPY_DETAILS,
    ], operations
    run = life.runs["A"]
    quoted = tickets.quoted_comments(life.preview_feedback)
    previewed, unanswered = life.ids[PREVIEWED], life.ids[UNANSWERED_A]
    assert {one.comment for one in quoted} == {previewed, unanswered}, quoted
    assert f"```text\n{PREVIEWED.rstrip()}\n```" in life.preview_feedback
    assert PREVIEW_REWRITTEN.rstrip() not in life.preview_feedback
    assert AFTER_PREVIEW.rstrip() not in life.preview_feedback
    for read in life.preview_reads:
        assert read["run"] == run
        shown = read["shown"]
        assert REWRITTEN_ISSUE not in shown["items"][0]["item"]["content"]
        bodies = [one["body"] for one in shown["comments"]]
        assert PREVIEWED in bodies and PREVIEW_REWRITTEN not in bodies, bodies
        assert AFTER_PREVIEW not in bodies, bodies
    before = json.loads(life.watermark_after_unanswered)
    held = json.loads(life.watermark_after_preview)
    expected = max(
        comments.moment(before["since"], "the watermark"),
        (snapshot.queried_at - comments.OVERLAP).replace(microsecond=0),
    )
    assert held["since"] == comments.instant(expected), (held, before)
    # The rewritten comment was answered as the preview showed it, so it is owed again; the
    # comment written after the preview was never quoted.
    after = life.after_preview
    assert _said(life, after, PREVIEWED).endswith(f"would go to run {run}")
    assert _said(life, after, AFTER_PREVIEW).endswith(f"would go to run {run}")


#: The recipe's own plan store, as :data:`RECORDING_STORE`, except that while the file
#: `$SHORTENED_ONCE` names exists, the first ticket update it is handed setting a priority beside
#: the issue's text reaches the pinned store without that text — its `--body-file` and its
#: `--metadata` — and the file is removed. The pinned store then really writes the priority
#: alone and answers, from its own mutation's response, with the one `fields` part it sent: a
#: write whose landed parts are a strict subset of the parts the recipe requested.
SHORTENING_STORE = """#!@PYTHON@
import fcntl, json, os, subprocess, sys
argv = sys.argv[1:]
once = os.environ.get("SHORTENED_ONCE")
if (
    once and os.path.exists(once) and argv[:2] == ["task", "update"]
    and "--priority" in argv and "--body-file" in argv
):
    os.remove(once)
    kept = []
    words = iter(argv)
    for word in words:
        if word in ("--body-file", "--metadata"):
            next(words)
        else:
            kept.append(word)
    argv = kept
done = subprocess.run([os.environ["REAL_PLAN_STORE"], *argv])
if "STORE_RECORD" in os.environ:
    with open(os.environ["STORE_RECORD"], "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps({"argv": argv, "status": done.returncode}) + "\\n")
sys.exit(done.returncode)
""".replace("@PYTHON@", str(REPO_ROOT / ".venv" / "bin" / "python3"))


@pytest.mark.parametrize("settled", [True, False], ids=["expected-version", "no-version"])
def test_a_write_landing_fewer_parts_than_it_asked_for_is_partial_and_the_next_pass_completes_it(
    tmp_path: Path, settled: bool
) -> None:
    """Settle's ticket update asked for the priority and the issue's text; only the priority
    landed. The pass records it `partial`, names it as needing attention and keeps the unlanded
    fields pending, while its verified reply still advances the watermark; the next pass, which
    gathers nothing for it, sends only the text that did not land, and nothing is pending.

    With ``settled``, the agent waits out the minute GitHub gives an item changed by its reply
    no version in, so settle's read holds a version and the write goes `--expect`, answering in
    parts: the `fields` part alone, the `issue` part the text rides on never reported. Without,
    settle's read holds none, and the write is the store's read and plain write, answering with
    the task it wrote, whose text is not what was asked.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path)
    # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] The published store is the one boundary this suite doubles at, and it changes one command's words once: the pinned store then writes and answers that shortened write itself, which is the only way a real store reports fewer parts than its caller asked for.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    checkout = _launching_checkout(tmp_path, SHORTENING_STORE)
    record, log, reads = (tmp_path / name for name in ("record.jsonl", "prompts.jsonl", "reads"))
    once = tmp_path / "shortened-once"
    bench.environment.update(
        {
            # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
            "FAKE_CODEX_RUN_ON_MARKER": str(tmp_path / "commands.json"),
            "FAKE_CODEX_RUN_ON_MARKER_LOG": str(tmp_path / "commands-ran.jsonl"),
            "FAKE_CODEX_PROMPT_LOG": str(log),
            "UV_NO_SYNC": "1",
        }
    )
    run = tickets.RunId(f"ac-short-{os.getpid()}")
    cause = "listing-cursor-skips-last-page"
    launched: list[str] = []
    with _serving(bench):
        try:
            issue = _filed(bench, run, cause)
            # Another run's evidence, so the confirming reply makes 3 occurrences and the
            # estimate moves the priority as well as the issue's estimate line and record.
            _marked(bench, issue, tickets.render_comment("another-run", cause, "Seen elsewhere."))
            _next_second()
            _commented(bench, issue, ON_A, REVIEWER)
            turn = [_replying(bench, run, log, reads)]
            if settled:
                turn.append(["sleep", str(CHANGE_TOKEN_DELAY + 2)])
            _scripted(bench, {run: turn})
            once.write_text("once", encoding="utf-8")
            first = _answer(
                bench,
                checkout,
                record,
                environment=bench.environment | {"SHORTENED_ONCE": str(once)},
            )
            launched.extend(_follow_up_runs(bench))
            watermark = _watermark(bench)
            row = _row(issue)
            priority, body = row.priority, row.body
            pending = comments.read_pending(bench.drafts_root)
            again = _answer(bench, checkout, record)
            after = _row(issue)
            left = comments.read_pending(bench.drafts_root)
        finally:
            for one in launched:
                if (bench.runs / one).exists():
                    _run(["just", "stop", one], bench)

    assert not once.exists(), "the store never shortened a write"
    assert first.result.returncode == UNANSWERED, first.report + first.result.stderr + _ran(bench)
    (entry,) = pending
    assert (entry.write, entry.state, entry.item) == ("ticket-copy", "partial", issue), entry
    assert set(entry.intended) == {"priority", "content", "record"}, entry
    assert priority == "High" and "3 occurrences" not in body, (priority, body)
    assert "needs attention: 1 write(s) pending in " in first.report, first.report
    assert f"  pending: ticket-copy of {issue} (partial): " in first.report, first.report
    operations = first.operations()
    replied = max(
        index
        for index, one in enumerate(operations)
        if one in (_Operation.ADD_COMMENT, _Operation.ADD_COMMENT_WHOLE)
    )
    if settled:
        assert entry.error == "the response reported no landed issue part", entry
        assert [(part.part, part.outcome) for part in entry.parts] == [("fields", "landed")]
        assert operations[replied + 1 :] == [
            _Operation.ISSUE_COPY_DETAILS,
            _Operation.UPDATE_FIELD_WHOLE,
        ], operations
    else:
        assert entry.error == (
            "the task it answered does not hold the requested content, record"
        ), entry
        assert entry.parts == (), entry
        assert operations[replied + 1 :] == [
            _Operation.ISSUE_COPY_DETAILS,
            _Operation.ISSUE,
            _Operation.UPDATE_FIELD,
        ], operations
    # The reply was verified, so the pending write holds no watermark.
    assert json.loads(watermark)["since"], watermark

    assert again.result.returncode == OK, again.report + again.result.stderr
    assert left == [], left
    assert f"  pending ticket-copy of {issue}: retried content, record; landed" in (again.report), (
        again.report
    )
    retries = [one for one in again.requests if one.operation in WRITES | {_Operation.UPDATE_ISSUE}]
    assert [one.operation for one in retries] == [
        _Operation.UPDATE_ISSUE_WHOLE if settled else _Operation.UPDATE_ISSUE
    ], again.operations()
    sent = retries[0].variables.get("input")
    assert isinstance(sent, dict) and "body" in sent and "title" not in sent, sent
    assert after.priority == "High" and "3 occurrences" in after.body, after.body


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] These drive the same
# recipe from the same `plan-tooling-follow-ups` project, whose
# `planToolingFollowUpsWorkspace` key already covers everything they read; none of them
# launches, and a project of their own would split one recipe's journeys across tiers behind
# no narrower key.


class Board(NamedTuple):
    bench: Bench
    checkout: Path
    record: Path


@pytest.fixture
def board(tmp_path: Path) -> Iterator[Board]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path)
    bench.environment["UV_NO_SYNC"] = "1"
    with _serving(bench):
        yield Board(bench, _launching_checkout(tmp_path), tmp_path / "store-record.jsonl")


def test_with_no_watermark_the_store_is_asked_from_the_earliest_instant_local_records_give(
    board: Board,
) -> None:
    bench = board.bench
    first, later = (
        tickets.RunId(f"wm-first-{os.getpid()}"),
        tickets.RunId(f"wm-later-{os.getpid()}"),
    )
    issue = _filed(bench, first, "listing-cursor-skips-last-page")
    _next_second()
    _filed(bench, later, "sweep-trailer-omits-a-family")
    written = tickets.ticket_path(bench.drafts_root, first, "listing-cursor-skips-last-page")
    earliest = comments.instant(datetime.fromtimestamp(written.stat().st_mtime, UTC))
    _next_second()
    _commented(bench, issue, ON_A, REVIEWER)

    scratch = board.bench.tmp / "tmpdir"
    scratch.mkdir()
    dry = _answer(
        bench,
        board.checkout,
        board.record,
        "--dry-run",
        environment=bench.environment | {"TMPDIR": str(scratch)},
    )

    assert dry.result.returncode == OK, dry.report + dry.result.stderr
    assert not [entry for entry in scratch.iterdir() if entry.name.startswith("tmp.")]
    assert dry.since() == earliest
    assert dry.report.splitlines()[0] == (
        f"read {BOARD!r} for comments since {earliest} (derived from local records: run "
        f"{first}'s ticket files' last write)"
    )
    assert not comments.watermark_path(bench.drafts_root, BOARD).exists()


def test_a_run_its_records_leave_unbounded_is_refused_and_since_is_passed_exactly(
    board: Board,
) -> None:
    bench = board.bench
    run = tickets.RunId(f"wm-unbounded-{os.getpid()}")
    issue = _filed(bench, run, "listing-cursor-skips-last-page")
    tickets.ticket_path(bench.drafts_root, run, "listing-cursor-skips-last-page").unlink()
    _commented(bench, issue, ON_A, REVIEWER)

    refused = _answer(bench, board.checkout, board.record)

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"run(s) {run} under {bench.drafts_root}" in refused.result.stderr
    assert "--since <RFC3339>" in refused.result.stderr
    assert refused.requests == []
    assert _follow_up_runs(bench) == []

    since = "2026-02-03T04:05:06Z"
    named = _answer(bench, board.checkout, board.record, "--dry-run", "--since", since)

    assert named.result.returncode == OK, named.report + named.result.stderr
    assert named.since() == since
    copies = [one.argv for one in named.recorded if one.argv[:2] == ["task", "copy"]]
    assert copies and all(argv[argv.index("--commented-since") + 1] == since for argv in copies), (
        copies
    )


def test_a_host_where_no_run_filed_a_ticket_reads_nothing_and_launches_nothing(
    board: Board,
) -> None:
    quiet = _answer(board.bench, board.checkout, board.record)

    assert quiet.result.returncode == OK, quiet.report + quiet.result.stderr
    assert "has filed a follow-up ticket, so no comment on" in quiet.report
    assert "nothing was read and nothing was launched" in quiet.report
    assert quiet.requests == []
    assert not comments.watermark_path(board.bench.drafts_root, BOARD).exists()


@pytest.mark.parametrize(
    ("held", "refusal"),
    [
        ('{"schema": 1}', "is not schema"),
        ("not json", "is not JSON"),
        (
            json.dumps(
                {"schema": 1, "board": BOARD, "since": "noon", "queried_at": "2026-01-01T00:00:00Z"}
            ),
            "reports 'noon', which is not an RFC 3339 time",
        ),
    ],
    ids=["another-shape", "not-json", "not-a-time"],
)
def test_a_watermark_the_recipe_cannot_read_is_refused_naming_it_before_the_board_is_read(
    board: Board, held: str, refusal: str
) -> None:
    _filed(board.bench, tickets.RunId(f"wm-broken-{os.getpid()}"), MIRROR_CAUSE)
    path = comments.watermark_path(board.bench.drafts_root, BOARD)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(held, encoding="utf-8")

    refused = _answer(board.bench, board.checkout, board.record)

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"the watermark {path}" in refused.result.stderr
    assert refusal in refused.result.stderr
    assert refused.requests == []
    assert path.read_text(encoding="utf-8") == held


def test_a_gathering_that_selects_nothing_still_records_the_boundary_it_computed(
    board: Board,
) -> None:
    """A run whose every comment its last response answered: its boundary is recorded, so a
    later copy of its ticket does not move it, and the complete gathering moves the
    watermark."""
    bench = board.bench
    run = tickets.RunId(f"wm-bounded-{os.getpid()}")
    issue = _filed(bench, run, MIRROR_CAUSE)
    _next_second()
    asked = _commented(bench, issue, BEFORE_BOUNDARY, REVIEWER)
    _next_second()
    _filed(bench, run, MIRROR_CAUSE, again=True)
    written = tickets.ticket_path(bench.drafts_root, run, MIRROR_CAUSE).stat().st_mtime
    boundary = datetime.fromtimestamp(written, UTC)

    gathered = _answer(bench, board.checkout, board.record, "--since", "2026-01-01T00:00:00Z")

    assert gathered.result.returncode == OK, gathered.report + gathered.result.stderr
    assert _line_for(gathered.report, _urls()[asked]).endswith(
        f": at or before run {run}'s boundary {comments.instant(boundary)}"
    )
    assert "nothing to answer: no comment was selected, so nothing was launched" in (
        gathered.report
    )
    recorded = comments.boundary_path(bench.drafts_root, run).read_text(encoding="utf-8")
    assert recorded == comments.boundary_line(boundary) + "\n"
    assert comments.read_watermark(bench.drafts_root, BOARD) is not None
    assert _follow_up_runs(bench) == []

    # `--since` names the start over the watermark that gathering left.
    named = _answer(
        bench, board.checkout, board.record, "--dry-run", "--since", "2026-01-02T00:00:00Z"
    )
    assert named.result.returncode == OK, named.report + named.result.stderr
    assert named.since() == "2026-01-02T00:00:00Z"
    assert "(named with --since)" in named.report.splitlines()[0]


def _previewed(board: Board, *arguments: str) -> Path:
    """A dry run's snapshot over the board's one waiting comment: the directory it names."""
    preview = _answer(board.bench, board.checkout, board.record, "--dry-run", *arguments)
    assert preview.result.returncode == OK, preview.report + preview.result.stderr
    snapshot = preview.snapshot
    assert snapshot is not None
    return snapshot.directory


def _claimed(bench: Bench, directory: Path) -> subprocess.CompletedProcess[str]:
    """``directory`` claimed by the recipe's own steps, launching no run: its gathering naming
    the snapshot with `--from`, then its launch step, whose answer this is."""
    module = [str(REPO_ROOT / ".venv" / "bin" / "python3"), "-m", "orchestrator.follow_up_comments"]
    intent = bench.tmp / f"claim-{directory.name}.json"
    plan = bench.tmp / f"plan-{directory.name}.json"
    gathered = _run(
        [*module, "gather", "--root", str(bench.drafts_root), "--intent", str(intent)]
        + ["--detach", "--from", str(directory)],
        bench,
    )
    assert gathered.returncode == 0, gathered.stdout + gathered.stderr
    return _run(
        [*module, "launch", "--root", str(bench.drafts_root), "--intent", str(intent)]
        + ["--plan", str(plan)],
        bench,
    )


def test_each_reason_from_refuses_a_snapshot_is_refused_by_the_recipe_with_nothing_written(
    board: Board,
) -> None:
    """Malformed, already launched, past its launchable age, launched past by a later snapshot
    of the same board, or named beside another gathering's flag: refused, naming the fresh dry
    run to take instead, with no feedback file, boundary or watermark written and nothing
    launched — and never because the board moved after the snapshot."""
    bench = board.bench
    run = tickets.RunId(f"from-{os.getpid()}")
    issue = _filed(bench, run, MIRROR_CAUSE)
    _next_second()
    _commented(bench, issue, ON_MIRROR, REVIEWER)
    scoped = _previewed(board, "--run", run)
    unscoped = _previewed(board)
    before = _snapshot(bench.drafts_root)

    def refused(*arguments: str, said: str) -> None:
        answer = _answer(bench, board.checkout, board.record, *arguments)
        assert answer.result.returncode == REFUSED, answer.report + answer.result.stderr
        assert said in answer.result.stderr, answer.result.stderr
        assert answer.requests == [], "a refused launch asked the board"
        written = {path for path in _snapshot(bench.drafts_root) if path not in before}
        assert {path for path in written if comments.SNAPSHOT_LAUNCHED not in path} == set()
        assert _follow_up_runs(bench) == []

    for flag in (
        ("--since", "2026-01-01T00:00:00Z"),
        ("--run", run),
        ("--issue", issue),
        ("--to", BOARD),
        ("--dry-run",),
    ):
        refused("--from", str(unscoped), *flag, said=f"{flag[0]} cannot be named beside it")
    record = unscoped / comments.SNAPSHOT_RECORD
    held = record.read_text(encoding="utf-8")
    record.write_text("{", encoding="utf-8")
    refused("--from", str(unscoped), said="the snapshot is malformed")
    record.write_text(held, encoding="utf-8")
    stale = json.loads(held) | {"queried_at": "2026-01-01T00:00:00Z"}
    record.write_text(json.dumps(stale), encoding="utf-8")
    refused("--from", str(unscoped), said=f"more than {comments.SNAPSHOT_LAUNCHABLE_FOR} ago")
    record.write_text(held, encoding="utf-8")
    # The board moving after the snapshot is no reason to refuse it: what moved is the next
    # pass's to read. Claimed by the recipe's own steps — its gathering naming the snapshot
    # with `--from`, then its launch step's claim — it may not be claimed again.
    _commented(bench, issue, "Written after both snapshots.\n", REVIEWER)
    _claimed(bench, unscoped)
    claimed = comments.launched_record(unscoped)
    assert claimed is not None
    before = _snapshot(bench.drafts_root)
    refused(
        "--from",
        str(unscoped),
        said=f"it was already launched at {claimed['claimed_at']}; gather a fresh one with "
        f"`just {RECIPE} --dry-run`",
    )
    refused(
        "--from",
        str(scoped),
        said=f"the later snapshot {unscoped}",
    )


def test_scoped_snapshots_of_one_run_overlap_and_those_of_two_runs_launch_independently(
    board: Board,
) -> None:
    """A run's later scoped snapshot, once launched, launches past its earlier one; another
    run's snapshot, taken before either, stays launchable, since no comment is in both."""
    bench = board.bench
    first, second = tickets.RunId(f"one-{os.getpid()}"), tickets.RunId(f"two-{os.getpid()}")
    for run, cause in ((first, MIRROR_CAUSE), (second, "export-drops-a-column")):
        issue = _filed(bench, run, cause)
        _next_second()
        _commented(bench, issue, ON_MIRROR, REVIEWER)
    other_run = _previewed(board, "--run", second)
    earlier = _previewed(board, "--run", first)
    later = _previewed(board, "--run", first)
    claimed = _claimed(bench, later)
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr

    refused = _answer(bench, board.checkout, board.record, "--from", str(earlier))

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"the later snapshot {later}" in refused.result.stderr, refused.result.stderr
    assert refused.requests == [], "a refused launch asked the board"
    launched = _claimed(bench, other_run)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert f"wrote run {second}'s comments to " in launched.stdout, launched.stdout


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        (("--run", "a/run"), "--run 'a/run' is not a run id"),
        (("--since", "yesterday"), "--since reports 'yesterday'"),
        (("--to",), "argument --to: expected one argument"),
        (("some-run",), "unrecognized arguments: some-run"),
    ],
)
def test_an_invocation_the_recipe_cannot_read_is_refused_before_the_board_is_read(
    board: Board, arguments: tuple[str, ...], refusal: str
) -> None:
    refused = _answer(board.bench, board.checkout, board.record, *arguments)

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert refusal in refused.result.stderr
    assert refused.requests == []


def test_a_board_the_store_cannot_read_is_refused_naming_what_to_check(board: Board) -> None:
    _filed(board.bench, tickets.RunId(f"nb-{os.getpid()}"), "listing-cursor-skips-last-page")

    refused = _answer(board.bench, board.checkout, board.record, "--to", "no-such-board")

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert "Check that --to names a source this checkout's plan store configures" in (
        refused.result.stderr
    )
    assert _follow_up_runs(board.bench) == []


def test_naming_no_board_reads_the_followups_board_and_launches_nothing_without_its_credential(
    board: Board,
) -> None:
    """The default is `followups`, whose credential a process without one cannot read with."""
    _filed(board.bench, tickets.RunId(f"df-{os.getpid()}"), "listing-cursor-skips-last-page")
    environment = {
        name: value
        for name, value in board.bench.environment.items()
        if name != "GH_PROJECTS_TOKEN"
    }

    refused = _answer(board.bench, board.checkout, board.record, environment=environment)

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"the copy of {tickets.BOARD!r}" in refused.result.stderr, refused.result.stderr
    assert _follow_up_runs(board.bench) == []


def test_a_drafts_root_that_cannot_be_resolved_is_refused_before_the_board_is_read(
    board: Board,
) -> None:
    not_a_directory = board.bench.tmp / "drafts-root-is-a-file"
    not_a_directory.write_text("", encoding="utf-8")
    environment = board.bench.environment | {follow_up_variables.root_name(): str(not_a_directory)}

    refused = _answer(board.bench, board.checkout, board.record, environment=environment)

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"{RECIPE}: the 'drafts' plan source could not be resolved" in refused.result.stderr
    assert refused.requests == []


def test_a_scratch_directory_that_cannot_be_made_is_refused_before_the_board_is_read(
    board: Board,
) -> None:
    _filed(board.bench, tickets.RunId(f"tmp-{os.getpid()}"), MIRROR_CAUSE)
    not_a_directory = board.bench.tmp / "tmpdir-is-a-file"
    not_a_directory.write_text("", encoding="utf-8")

    refused = _answer(
        board.bench,
        board.checkout,
        board.record,
        environment=board.bench.environment | {"TMPDIR": str(not_a_directory)},
    )

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"{RECIPE}: a scratch directory for the gathering could not be created" in (
        refused.result.stderr
    )
    assert refused.requests == []


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def test_a_checkout_nobody_provisioned_is_refused_naming_bootstrap(tmp_path: Path) -> None:
    """The recipe and its script alone, in a checkout with no `.venv` to read the board with."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    (tmp_path / "scripts").mkdir()
    shutil.copy2(REPO_ROOT / "justfile", tmp_path / "justfile")
    script = f"scripts/{RECIPE}.sh"
    shutil.copy2(REPO_ROOT / script, tmp_path / script)

    refused = subprocess.run(
        ["just", RECIPE],  # noqa: S607 - the recipe itself
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert "has no Python interpreter at" in refused.stderr
    assert "provision this checkout with 'just bootstrap'" in refused.stderr


# The board credential, read from the checkout's own `.env`: from a mirror of the recipe's
# scripts, with no engine, whose plan store records the credential it was handed.

#: The board credential's name, the value these journeys plant in a mirror's `.env` — never
#: a real token — and the value a process already holding the name keeps.
BOARD_CREDENTIAL = "GH_PROJECTS_TOKEN"
PLANTED_CREDENTIAL = "not-a-real-token-planted-by-this-journey"
PROCESS_CREDENTIAL = "the-value-this-process-already-defines"

#: Records which board credential the store was handed, installed as the mirror checkout's
#: own plan-store CLI: every command is answered by the pinned store, and this appends one
#: line per invocation it served, because a credential is on no command line and the store's
#: own answer to it says nothing of which token it read with.
CREDENTIAL_RECORDING_STORE = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "${GH_PROJECTS_TOKEN-<unset>}" >>"$CREDENTIAL_TRACE"
exec "$REAL_PLAN_STORE" "$@"
"""

#: Where the mirror's launch stops: that checkout carries this repository's `scripts/`,
#: `justfile`, a plan store and an engine that doubles only the launch flow's `flow run`
#: (`give_flow_engine`), so the first other verb — stating the follow-up task's template —
#: is refused. That is past the board read this mirror exists for, and before any task is
#: created or anything launched.
ENGINE_STOP = UNDOUBLED

#: The cause of the one issue a mirror's run owns, and the person's comment on it.
MIRROR_CAUSE = "listing-cursor-skips-last-page"
ON_MIRROR = "Page 9 still never renders.\n"


class Mirror(NamedTuple):
    """A checkout of this repository's real scripts, with a toolchain and `.env` of its own."""

    checkout: Path
    trace: Path
    bench: Bench
    run: str
    issue: QualifiedTaskId


@contextlib.contextmanager
def _mirror(
    tmp_path: Path, credentials: str | None, store: str = CREDENTIAL_RECORDING_STORE
) -> Iterator[Mirror]:
    """The recipe's checkout, the board it reads, and the run that owns an issue there.

    `scripts/credentials-env.sh` reads the `.env` at the root of the checkout it is sourced
    from, and this checkout's is the host's — a real file, never a journey's to write. So the
    recipe is driven from a copy of the real `scripts/` and `justfile` beside a toolchain
    mirroring this checkout's (:func:`_mirrored_toolchain`) whose plan store is ``store``,
    with the `.env` this journey states. The board is the loopback every journey here reads,
    holding one issue the run owns with a person's comment nobody answered. That checkout's
    engine doubles only the launch flow, so a launch stops where its task would be created —
    after the board read this exists for, and before any launch.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path)
    checkout = tmp_path / "mirror"
    shutil.copytree(REPO_ROOT / "scripts", checkout / "scripts")
    shutil.copy2(REPO_ROOT / "justfile", checkout / "justfile")
    _mirrored_toolchain(checkout, store)
    # llmlint: ignore[e2e_not_mocked] The engine is a published CLI, which this suite doubles at its binary and nowhere above it: the stand-in only runs the launcher it is handed inside the flow it names and refuses every other verb, while the real wrapper and recipe run unchanged; a real engine would launch the run these journeys stop before.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    give_flow_engine(checkout)
    if credentials is not None:
        (checkout / ".env").write_text(credentials, encoding="utf-8")
    run = tickets.RunId(f"ac-mirror-{os.getpid()}")
    with _serving(bench):
        issue = _filed(bench, run, MIRROR_CAUSE)
        _next_second()
        _commented(bench, issue, ON_MIRROR, REVIEWER)
        yield Mirror(checkout, tmp_path / "credential.trace", bench, run, issue)


def _answered_from(
    mirror: Mirror,
    credential: str | None,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """The recipe on the mirror, holding ``credential`` or no name.

    ``environment`` is what the store the mirror carries reads, over the bench's own.
    """
    environment = {
        name: value
        for name, value in (mirror.bench.environment | (environment or {})).items()
        if name != BOARD_CREDENTIAL
    }
    if credential is not None:
        environment[BOARD_CREDENTIAL] = credential
    # llmlint: ignore[e2e_not_mocked] The pinned store answers every command; what stands in front of it, as the mirror's own locked plan store, records which credential it was handed — on no command line and in no answer — or rewrites one field of one answer, and changes nothing else the recipe reads.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    environment["CREDENTIAL_TRACE"] = str(mirror.trace)
    return subprocess.run(  # noqa: S603 - this checkout's own recipe over the installed store
        ["just", RECIPE, *arguments],  # noqa: S607 - `just` from the search path, as an operator invokes it
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


# llmlint: ignore-block[shell_test_tiers_stay_split] The finding is about which Nx project
# owns these journeys, which is a property of the module rather than of any one of them:
# each drives the recipe every journey of this module drives, from the `plan-tooling-follow-ups`
# project whose `planToolingFollowUpsWorkspace` key already covers everything they read — the
# `scripts/**` the mirror copies, the `justfile`, `orchestrator/**` — so a project of their
# own would split one recipe's journeys across tiers behind no narrower key.


@pytest.mark.parametrize(
    ("held", "handed"),
    [(None, PLANTED_CREDENTIAL), (PROCESS_CREDENTIAL, PROCESS_CREDENTIAL)],
    ids=["from-the-file", "the-process-value-wins"],
)
def test_the_board_read_is_handed_the_credential_this_checkout_supplies(
    tmp_path: Path, held: str | None, handed: str
) -> None:
    """The token only in the checkout's `.env` reaches the board read; a held one is kept."""
    with _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n") as mirror:
        result = _answered_from(mirror, held)

    assert result.returncode == UNANSWERED, result.stdout + result.stderr
    assert _handed(mirror) == {handed}, mirror.trace.read_text(encoding="utf-8")
    (feedback,) = (mirror.bench.drafts_root / comments.FEEDBACK_DIRECTORY / mirror.run).glob(
        "2*.md"
    )
    assert ON_MIRROR.rstrip() in feedback.read_text(encoding="utf-8")
    assert ENGINE_STOP in result.stderr, result.stderr
    assert f"run {mirror.run}: its follow-up run did not launch" in result.stdout
    assert not list(mirror.bench.runs.glob(f"{mirror.run}{SUFFIX}*"))
    assert PLANTED_CREDENTIAL not in result.stdout + result.stderr, "a credential value was printed"


#: A store whose `--version` fails and which answers every other command as the pinned one.
VERSIONLESS_STORE = """#!/usr/bin/env bash
set -euo pipefail
[ "${1:-}" != --version ] || exit 1
exec "$REAL_PLAN_STORE" "$@"
"""


def test_a_store_that_reports_no_version_is_named_so_in_the_report(tmp_path: Path) -> None:
    """The report names the release it ran against, or says it could not."""
    # llmlint: ignore[e2e_not_mocked] The published CLI is the one boundary this suite doubles a store at, and no pinned store fails its own `--version`: this answers every other command with the pinned store's own answer.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    with _mirror(tmp_path, None, VERSIONLESS_STORE) as mirror:
        dry = _answered_from(mirror, "fixture-token", "--dry-run")

    assert dry.returncode == OK, dry.stdout + dry.stderr
    store = mirror.checkout / ".venv" / "bin" / ONETASKGRAPH_BIN.name
    assert dry.stdout.splitlines()[-2].startswith(
        f"plan store: {store}, which reported no version; the narrowed query returned 1 item(s)"
    ), dry.stdout


def test_a_credential_file_the_helper_refuses_refuses_the_recipe_before_the_board_is_read(
    tmp_path: Path,
) -> None:
    """A malformed `.env` is refused whole, under this recipe's own name, before any read."""
    with _mirror(tmp_path, "not-an-assignment-with-a-value-that-must-not-appear\n") as mirror:
        result = _answered_from(mirror, None)

    assert result.returncode == REFUSED, result.stdout + result.stderr
    assert (
        f"{RECIPE}: malformed credential line 1 in {mirror.checkout / '.env'}; "
        "write it as KEY=VALUE or make it a '#' comment, then retry"
    ) in result.stderr, result.stderr
    assert "must-not-appear" not in result.stderr, "a credential file's line reached a diagnostic"
    assert not mirror.trace.exists(), "the board was read over a credential file the helper refused"
    assert not (mirror.bench.drafts_root / comments.FEEDBACK_DIRECTORY / mirror.run).exists()


def _refusals(stderr: str, program: str) -> list[str]:
    """The lines ``program`` refused in, without the name it prefixed each with."""
    return [
        line.removeprefix(f"{program}: ")
        for line in stderr.splitlines()
        if line.startswith(f"{program}: ")
    ]


@pytest.mark.parametrize(
    ("helper_name", "what"),
    [("follow-up-env.sh", "drafts-root helper"), ("plan-brief.sh", "run-id helper")],
)
def test_the_recipe_refuses_before_the_board_is_read_when_another_helper_cannot_be_loaded(
    tmp_path: Path, helper_name: str, what: str
) -> None:
    """Each helper the recipe sources is refused by name, and nothing is read or launched."""
    with _mirror(tmp_path, None) as mirror:
        helper = mirror.checkout / "scripts" / helper_name
        helper.write_text("broken() {\n", encoding="utf-8")

        result = _answered_from(mirror, None)

        assert result.returncode == REFUSED, result.stdout + result.stderr
        assert f"{RECIPE}: the {what} at {helper} is readable but could not be loaded" in (
            result.stderr
        )
        assert not (mirror.bench.drafts_root / comments.FEEDBACK_DIRECTORY / mirror.run).exists()
        helper.unlink()

        missing = _answered_from(mirror, None)

    assert missing.returncode == REFUSED, missing.stdout + missing.stderr
    assert f"{RECIPE}: required helper is not a readable regular file: {helper}" in (missing.stderr)


@pytest.mark.parametrize(
    "sabotage",
    ["missing", "unreadable", "unloadable"],
    ids=["helper-missing", "helper-unreadable", "helper-unloadable"],
)
def test_the_recipe_refuses_before_the_board_is_read_when_the_credentials_helper_cannot_be_loaded(
    tmp_path: Path, sabotage: str
) -> None:
    """The one source of the credential is gone, unreadable or broken: refused naming it, in
    the shape `scripts/plan-store.sh` refuses the same thing, and nothing is read first."""
    with _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n") as mirror:
        helper = mirror.checkout / "scripts" / "credentials-env.sh"
        match sabotage:
            case "missing":
                helper.unlink()
            case "unreadable":
                helper.chmod(0)
                if os.access(helper, os.R_OK):
                    pytest.skip(
                        "this user reads a file with no permission bits: nothing is unreadable"
                    )
            case "unloadable":
                helper.write_text("export_host_credentials() {\n", encoding="utf-8")

        result = _answered_from(mirror, None)
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
    assert _refusals(result.stderr, RECIPE) == _refusals(wrapper.stderr, "plan-store"), (
        result.stderr + wrapper.stderr
    )
    assert not mirror.trace.exists(), "the board was read without the checkout's credential"


#: A cursor that never advances: what the store below answers for every page.
REPEATING_CURSOR = "ab"

#: A store whose every task listing reports :data:`REPEATING_CURSOR` as its `next`: the
#: pinned store answers the query with the page it was *not* asked to resume, and this
#: rewrites that page's one field. Every other command is the pinned store's own answer. It
#: logs each listing's command line, so which pages the recipe asked for is read off what
#: the store was asked rather than inferred. A run-scoped gathering lists the drafts source
#: for the tickets its run filed, which is the one listing this module pages itself.
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
    """Followed again, the same page would be read twice — or, from a cursor that never
    advances, for ever: refused naming it, and nothing is written or launched."""
    # llmlint: ignore[e2e_not_mocked] The published CLI is the one boundary this suite doubles a store at, and no real store answers a cursor twice to drive this against: the pinned store answers every command, and one field of one listing's answer is rewritten.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    with _mirror(tmp_path, None, REPEATING_STORE) as mirror:
        asked_log = tmp_path / "pages-asked.log"
        refused = _answered_from(
            mirror,
            "fixture-token",
            "--run",
            mirror.run,
            environment={
                "REPEATING_STORE_LOG": str(asked_log),
                "REPEATING_STORE_PYTHON": str(mirror.checkout / ".venv" / "bin" / "python3"),
                "REPEATING_CURSOR": REPEATING_CURSOR,
            },
        )

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert f"answered the page cursor {REPEATING_CURSOR!r} again after it was already followed" in (
        refused.stderr
    )
    asked = asked_log.read_text(encoding="utf-8").splitlines()
    assert len(asked) == 2, asked
    assert "--page" not in asked[0], asked
    assert f"--page {REPEATING_CURSOR}" in asked[1], asked
    assert not (mirror.bench.drafts_root / comments.FEEDBACK_DIRECTORY / mirror.run).exists()
    assert not list(mirror.bench.runs.glob(f"{mirror.run}{SUFFIX}*"))


# llmlint: ignore-end[shell_test_tiers_stay_split]


def test_the_narrow_mode_is_refused_without_the_gathering_it_is_about(tmp_path: Path) -> None:
    """`--comments` says what a feedback file is, so it says nothing without one, and the
    refusal names the recipe that writes one."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path)

    refused = _run(["just", "follow-ups", "some-run", "--comments"], bench)

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert "--comments says which kind of feedback file this is, and none was named" in (
        refused.stderr
    )
    assert f"just {RECIPE}" in refused.stderr
    assert "follow-ups: launching run" not in refused.stderr


def test_the_run_ids_the_module_routes_to_are_the_ones_the_launcher_takes() -> None:
    """A second spelling of a grammar drifts, so the module's is read against the launcher's."""
    brief = (REPO_ROOT / "scripts" / "plan-brief.sh").read_text(encoding="utf-8")
    (declared,) = re.findall(r"^PLAN_SAFE_RUN_ID='\^(.*)\$'$", brief, re.MULTILINE)

    assert declared == comments.LAUNCHABLE_RUN.pattern
