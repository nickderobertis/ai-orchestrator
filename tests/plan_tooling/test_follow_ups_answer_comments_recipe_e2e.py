"""`just follow-ups-answer-comments` over people's board comments, driven end to end.

Everything below the recipe is real: `just follow-ups-answer-comments` and its script,
`orchestrator/follow_up_comments.py` reading the board, `just follow-ups --feedback
--comments` and the launches it makes under the shipped `graphs/follow-up.yaml`, the
installed `onepipeline` driver, and the installed `onetaskgraph` every ticket, board item and
comment is written and read through. The bench is
`tests/plan_tooling/test_follow_ups_recipe_e2e.py`'s, imported rather than restated.

**The board is a second local store and never the live `followups` board**, added through
the store's own environment layer and named with `--to`. **`tests/e2e/fake_codex.py` stands
in for the paid model alone**, running the commands each answering agent would choose
through the real programs: posting one reply to each comment its task's feedback quotes, read
out of the task that turn was given, re-estimating an issue a reply confirms, and writing the
response account that task is held to.

**What the recipe asks the store is read off the store itself.** Every plan-store read of the
package runs the `onetaskgraph` beside its interpreter and never one on `PATH`, so the recipe
is run from a checkout of this repository whose toolchain carries a plan store that records
each command it is handed and what it answered, and hands every one to the pinned store
(:func:`_mirrored_toolchain`, the way `tests/plan_tooling/test_follow_ups_comment_answers_e2e.py`'s
credential journeys install one). The launching checkout is every file of this one, linked,
with that toolchain beside it, so the launches are this repository's and nothing else.

One module fixture walks one board's life: a watermark an earlier gathering left, the
comments people wrote since, a dry run, the real gathering answering three runs at once — one
of them a run id carrying a capital — a gathering with nothing left, a run-scoped one, a
detached one, and one whose run answers nothing. The board is paged two items at a time, so
the narrowed answer spans pages.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import pytest
from nx_workspace import WORKSPACE_INSTALL_MARKS
from published_tools import ONETASKGRAPH_BIN
from test_follow_ups_recipe_e2e import (
    BOARD,
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
from orchestrator.plan_store import QualifiedTaskId
from orchestrator.root import REPO_ROOT

#: A reader of the toolchain this checkout provisioned: every step here runs its tool
#: through a `just` recipe or `uv run`, and no writer touches that toolchain, so
#: `tests/e2e/nx_workspace.py` asks no xdist group of it. The group is this module's own,
#: for fixture sharing: `life` is module-scoped and drive the real recipe end to
#: end, so spread across workers the module builds it once per worker — measured,
#: 120 to 170 s on each of four workers against 151 s for the whole module on one.
pytestmark = [
    *WORKSPACE_INSTALL_MARKS,
    pytest.mark.xdist_group("follow-ups-answer-comments-recipe"),
]

RECIPE = "follow-ups-answer-comments"
UNANSWERED = 1

#: The people commenting, as the board records their authors, and an app.
REVIEWER = "a-reviewer"
MAINTAINER = "a-maintainer"
APP = "dependabot[bot]"

#: A repository of another owner than the board's, which a ticket may still be about.
FOREIGN_REPOSITORY = "github.com/some-other-owner/some-library"

#: What each seeded comment says, so a feedback file can be read for exactly which reached it.
QUIET_OLD = "Asked long before the watermark, and nobody touched this item since.\n"
FOREIGN = "Asked and answered long before the watermark; edited below.\n"
FOREIGN_EDITED = "Edited after the watermark: the library's 2.x line too?\n"
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

#: What every reply the answering agent posts says it did.
RESPONSE = "Copied this run's ticket again with that in its examples."

#: The comments the answering agent judges to confirm their issue's root cause.
CONFIRMING = (ON_A,)

#: The answering agent's own program: read the feedback its run's task quotes, and post one
#: reply to each comment there, on the issue that holds it, naming the comment's id and
#: carrying its verdict — re-estimating the issue after each reply that confirms its root
#: cause, as the task says. Run in the turn with the prompt log every turn appends to, so it
#: reads the last prompt whose feedback is its own run's: several runs answer at once here.
REPLY_TO_FEEDBACK = """\
import json, re, subprocess, sys, tempfile
from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets

log, run, store, board, confirming = sys.argv[1:6]
confirming = [text.rstrip() for text in json.loads(confirming)]
own = f"/{comments.FEEDBACK_DIRECTORY}/{run}/"
with open(log, encoding="utf-8") as stream:
    prompts = [json.loads(line)["prompt"] for line in stream.read().splitlines()]
task = [prompt for prompt in prompts if own in prompt][-1]
account = re.search(r"account is a JSON document at `([^`]+)`", task)[1]
gathered = re.search(r'"feedback": "([^"]+)"', task)[1]
gathering = tickets.read_gathering(task.split("## The comments to answer", 1)[1])
responses = []
for section, one, text in zip(
    task.split("### Comment ")[1:], gathering.quoted, gathering.texts, strict=True
):
    verdict = (
        tickets.Verdict.CONFIRMS if text in confirming else tickets.Verdict.DOES_NOT_CONFIRM
    )
    fields = dict(
        line[2:].split(": ", 1) for line in section.splitlines() if line.startswith("- ")
    )
    shown = json.loads(
        subprocess.run([store, "task", "show", one.issue, "--json"], check=True,
                       capture_output=True, text=True).stdout
    )
    cause = shown["items"][0]["item"]["metadata"][tickets.KEY]["root_cause"]
    author = fields["Author"]
    reply = tickets.render_reply(
        run, cause, answers=one.comment, url=fields["URL"],
        author=None if author == comments.UNKNOWN_AUTHOR else author,
        response=@RESPONSE@, verdict=verdict,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as body:
        body.write(reply)
    subprocess.run([store, "task", "comment", "add", one.issue, "--body-file", body.name],
                   check=True)
    listed = json.loads(
        subprocess.run([store, "task", "comment", "list", one.issue, "--json"], check=True,
                       capture_output=True, text=True).stdout
    )["comments"]
    posted = [
        held["id"] for held in listed
        if (owner := tickets.comment_owner(held["body"])) is not None
        and owner.run == run and owner.answers == one.comment
    ]
    if verdict is tickets.Verdict.CONFIRMS:
        subprocess.run([sys.executable, "-m", "orchestrator.follow_up_tickets", "re-estimate",
                        "--board", board, one.issue], check=True)
    responses.append({
        "comment": one.comment, "issue": one.issue,
        "action": @RESPONSE@, "reply": posted[-1], "verdict": verdict.value,
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


#: Records every command the recipe hands its plan store and what that store answered, as
#: one JSON line apiece, then answers with the pinned store's own output. Installed as the
#: launching checkout's own plan-store CLI, which is the one boundary this suite doubles a
#: store at: no real store reports which commands it was asked, and the property this
#: journey holds is exactly which listings and comment reads the recipe makes.
RECORDING_STORE = """#!@PYTHON@
import fcntl, json, os, subprocess, sys
argv = sys.argv[1:]
recorded = argv[:2] == ["task", "list"] or argv[:3] == ["task", "comment", "list"]
done = subprocess.run(
    [os.environ["REAL_PLAN_STORE"], *argv], stdout=subprocess.PIPE if recorded else None
)
if recorded:
    sys.stdout.buffer.write(done.stdout)
    sys.stdout.flush()
# A launch hands the store an environment of its own making, which names no record: what
# is recorded is what the recipe's own steps ask, where the journey named one.
if "STORE_RECORD" in os.environ:
    with open(os.environ["STORE_RECORD"], "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps({
            "argv": argv,
            "status": done.returncode,
            "out": done.stdout.decode("utf-8") if recorded else None,
        }) + "\\n")
sys.exit(done.returncode)
""".replace("@PYTHON@", str(REPO_ROOT / ".venv" / "bin" / "python3"))

#: What a launching checkout does not link from this one: its own toolchain, which it carries
#: instead, and what is this checkout's own state rather than the repository's.
UNLINKED = frozenset({".venv", ".git", ".nx", ".logs", ".env", "scratch"})


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


def _launching_checkout(tmp: Path) -> Path:
    """Every file of this checkout, linked, beside a toolchain whose plan store records."""
    checkout = tmp / "checkout"
    checkout.mkdir()
    for entry in REPO_ROOT.iterdir():
        if entry.name not in UNLINKED:
            (checkout / entry.name).symlink_to(entry)
    _mirrored_toolchain(checkout, RECORDING_STORE, engine=True)
    return checkout


def _commented(bench: Bench, issue: str, body: str, author: str | None) -> str:
    """A comment written through the store's own verb, as a person or a run writes one; its id."""
    path = bench.tmp / f"comment-{time.monotonic_ns()}.md"
    path.write_text(body, encoding="utf-8")
    added = _store(
        bench,
        "task",
        "comment",
        "add",
        issue,
        "--body-file",
        str(path),
        *(["--author", author] if author else []),
    )
    return str(added["id"])


def _edited(bench: Bench, issue: str, identifier: str, body: str) -> None:
    path = bench.tmp / f"edit-{time.monotonic_ns()}.md"
    path.write_text(body, encoding="utf-8")
    _store(bench, "task", "comment", "edit", issue, identifier, "--body-file", str(path))


def _filed(
    bench: Bench,
    run: str,
    cause: str,
    *,
    host: str = HOST,
    repository: str | None = None,
) -> QualifiedTaskId:
    """``run``'s verified ticket for ``cause``, written, copied onto the board and bound; its item.

    Its binding is what a gathering scoped to its run or its issue reads that item by.
    """
    path = tickets.ticket_path(bench.drafts_root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    ticket = _ticket(
        run,
        cause,
        (f"drafts:{run}/drafts/a-consumed-draft",),
        f"{(repository or 'github.com/nickderobertis/some-service').rsplit('/', 1)[1]}: "
        f"{cause.replace('-', ' ')}",
        "Verified",
        host,
        estimated=True,
        **({"repository": repository} if repository else {}),
    )
    path.write_text(tickets.render(ticket), encoding="utf-8")
    # Placed and copied through the module's own verbs, as the follow-up agent does: the
    # first copy binds the ticket to the item it creates, and a later copy follows it.
    for verb in ("board-status", "copy"):
        done = _run(
            [str(REPO_ROOT / ".venv" / "bin" / "python3"), "-m", "orchestrator.follow_up_tickets"]
            + [verb, "--board", BOARD, str(path)],
            bench,
        )
        assert done.returncode == 0, done.stdout + done.stderr
    record = _item(bench, tickets.qualified_id(run, cause))["metadata"]
    assert isinstance(record, dict), record
    native = record[tickets.KEY][tickets.BINDING_FIELD]
    return QualifiedTaskId(f"{BOARD}:{native}")


def _reply(bench: Bench, run: str, issue: str, answers: str, cause: str) -> str:
    """``run``'s reply to one comment, posted through the store's own verb; its id."""
    (answered,) = [one for one in _comments(bench, issue) if one["id"] == answers]
    author = answered["author"]
    return _commented(
        bench,
        issue,
        tickets.render_reply(
            run,
            cause,
            answers=answers,
            url=f"{issue}#comment-{answers}",
            author=author if isinstance(author, str) else None,
            response="Answered before this journey's gatherings.",
            verdict=tickets.Verdict.DOES_NOT_CONFIRM,
        ),
        None,
    )


def _next_second() -> None:
    """Wait until the store's whole-second clock has moved past every time written so far."""
    time.sleep(1.1)


class Recorded(NamedTuple):
    """One store command the recipe made, and what the pinned store answered it."""

    argv: list[str]
    out: str | None


def _recorded(path: Path) -> list[Recorded]:
    if not path.is_file():
        return []
    return [
        Recorded(held["argv"], held["out"])
        for held in map(json.loads, path.read_text(encoding="utf-8").splitlines())
    ]


def _option(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def _board_listings(recorded: list[Recorded]) -> list[Recorded]:
    return [
        one
        for one in recorded
        if one.argv[:2] == ["task", "list"] and _option(one.argv, "--source") == BOARD
    ]


def _listed_items(recorded: list[Recorded]) -> list[dict[str, object]]:
    return [
        item for one in _board_listings(recorded) for item in json.loads(one.out or "{}")["items"]
    ]


class Invocation(NamedTuple):
    """One run of the recipe: its exit, its report, its diagnostics, and the store's record."""

    result: subprocess.CompletedProcess[str]
    recorded: list[Recorded]

    @property
    def report(self) -> str:
        return self.result.stdout

    def since(self) -> str:
        (first, *_) = _board_listings(self.recorded)
        since = _option(first.argv, "--commented-since")
        assert since is not None, first.argv
        return since


class Life(NamedTuple):
    """Every phase of one board's life, read back before anything is torn down."""

    bench: Bench
    checkout: Path
    runs: dict[str, tickets.RunId]
    issues: dict[str, QualifiedTaskId]
    #: Each seeded comment's id, by what it says.
    ids: dict[str, str]
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


def _answer(
    bench: Bench,
    checkout: Path,
    record: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> Invocation:
    """`just follow-ups-answer-comments` from ``checkout``, its store recording to ``record``."""
    record.unlink(missing_ok=True)
    result = subprocess.run(  # noqa: S603 - this checkout's own recipe over the installed store
        ["just", RECIPE, *arguments],  # noqa: S607 - `just` from the search path, as an operator invokes it
        cwd=checkout,
        env=(environment or bench.environment) | {"STORE_RECORD": str(record)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )
    return Invocation(result, _recorded(record))


def _replying(bench: Bench, run: str, log: Path) -> list[str]:
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
        BOARD,
        json.dumps(CONFIRMING),
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
    return sorted(path.name for path in bench.runs.iterdir()) if bench.runs.is_dir() else []


def _watermark(bench: Bench) -> bytes:
    path = comments.watermark_path(bench.drafts_root, BOARD)
    return path.read_bytes() if path.is_file() else b""


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `tests/plan_tooling` is
# already the Nx project edge this repository keeps for journeys that launch the installed
# engine, keyed on `planToolingWorkspace`, which covers every file these launches read. The
# fixture is module-scoped and spends six launches whose turns are the provider's stand-in,
# and every test down to this block's end reads that one fixture rather than launching.
@pytest.fixture(scope="module")
def life(tmp_path_factory: pytest.TempPathFactory) -> Life:  # noqa: PLR0915 - one board's life, phase by phase
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("follow-ups-answer-comments")
    bench = _bench(tmp)
    checkout = _launching_checkout(tmp)
    record = tmp / "store-record.jsonl"
    log = tmp / "prompts.jsonl"
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
    try:
        issues = {
            "a": _filed(bench, runs["A"], "listing-cursor-skips-last-page"),
            "a-deferred": _filed(bench, runs["A"], "export-drops-a-column"),
            "a-todo": _filed(bench, runs["A"], "retry-loop-never-backs-off"),
            "a-foreign": _filed(
                bench, runs["A"], "library-parses-dates-wrong", repository=FOREIGN_REPOSITORY
            ),
            "a-elsewhere": _filed(bench, runs["A"], "sweep-follows-a-symlink", host="other-host"),
            "b": _filed(bench, runs["B"], "sweep-trailer-omits-a-family"),
            "c": _filed(bench, runs["C"], "cache-key-ignores-the-locale"),
            "c-quiet": _filed(bench, runs["C"], "cache-evicts-too-early"),
            "g": _filed(bench, runs["G"], "listing-export-drops-a-row"),
        }
        # Run G's records are gone from this host; its board issue outlived them.
        shutil.rmtree(bench.drafts_root / "tasks" / runs["G"])
        # A ticket of run A that no comment names and that `validate` refuses: a dispatch or a
        # recipe still ranging over the run's tickets reads, rewrites or refuses it.
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
        # A person sets the priority of run A's proposal by hand, which no re-estimate rewrites.
        _store(bench, "task", "priority", "set", issues["a"], HELD_PRIORITY)
        ids: dict[str, str] = {}
        ids[QUIET_OLD] = _commented(bench, issues["c-quiet"], QUIET_OLD, REVIEWER)
        ids[FOREIGN] = _commented(bench, issues["a-foreign"], FOREIGN, MAINTAINER)
        _reply(bench, runs["A"], issues["a-foreign"], ids[FOREIGN], "library-parses-dates-wrong")
        _next_second()
        # The watermark an earlier complete gathering left, between those and what follows.
        # llmlint: ignore[tests_mirror_real_usage] A gathering the recipe completes leaves its watermark `OVERLAP` — fifteen minutes — behind its own query, so the only way to reach one that excludes a comment written here is to wait that long; this writes the file an earlier gathering would have left with the module's own writer, which is the recipe's only writer of it.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
        comments.write_watermark(
            bench.drafts_root, BOARD, datetime.now(UTC).replace(microsecond=0) + comments.OVERLAP
        )
        watermark_before = _watermark(bench)
        _next_second()
        # Run A's last response: an evidence comment on run C's issue.
        marker = tickets.render_comment(runs["A"], "cache-key-ignores-the-locale", "Seen in A.")
        ids[marker] = _commented(bench, issues["c"], marker, None)
        ids[BEFORE_BOUNDARY] = _commented(bench, issues["c"], BEFORE_BOUNDARY, REVIEWER)
        _next_second()
        # Run C's last response: its ticket copied again, after that comment.
        _filed(bench, runs["C"], "cache-key-ignores-the-locale")
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
        _edited(bench, issues["a"], ids[EDITED], EDITED_AGAIN)
        _edited(bench, issues["a-foreign"], ids[FOREIGN], FOREIGN_EDITED)
        statuses_before = _statuses(bench)

        dry = _answer(bench, checkout, record, "--dry-run", "--to", BOARD)
        after_dry = _snapshot(bench.drafts_root)
        issue_dry = _answer(
            bench, checkout, record, "--issue", issues["a"], "--dry-run", "--to", BOARD
        )

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
                    _replying(bench, runs[key], log),
                ]
                for key in ("A", "B", "C")
            },
        )
        real_started = datetime.now(UTC)
        real = _answer(bench, checkout, record, "--to", BOARD)
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
        again = _answer(bench, checkout, record, "--to", BOARD)
        watermark_after_again = _watermark(bench)

        # A comment for run A and one for run B, and a gathering narrowed to run A.
        _next_second()
        ids[LATER_A] = _commented(bench, issues["a"], LATER_A, REVIEWER)
        ids[LATER_B] = _commented(bench, issues["b"], LATER_B, REVIEWER)
        _scripted(bench, {runs["A"]: [_replying(bench, runs["A"], log)]})
        scoped = _answer(bench, checkout, record, "--run", runs["A"], "--to", BOARD)
        watermark_after_scoped = _watermark(bench)
        launched_after_scoped = _follow_up_runs(bench)
        started.extend(set(launched_after_scoped) - set(started))

        # Detached: run B's comment, answered only once the journey says the recipe returned.
        release = tmp / "release-detached"
        _scripted(
            bench,
            {
                runs["B"]: [
                    _python(bench, "await-release.py", AWAIT_RELEASE, str(release)),
                    _replying(bench, runs["B"], log),
                ]
            },
        )
        detached = _answer(bench, checkout, record, "--detach", "--to", BOARD)
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
        detached_checked = _run(
            shlex.split(printed[1]) if printed else ["false"],
            bench,
        )
        watermark_after_detached = _watermark(bench)

        # A run whose dispatch answers nothing: its comment is selected again after it.
        _next_second()
        ids[UNANSWERED_A] = _commented(bench, issues["a"], UNANSWERED_A, REVIEWER)
        _scripted(bench, {runs["A"]: [["true"]]})
        unanswered = _answer(bench, checkout, record, "--to", BOARD)
        started.extend(set(_follow_up_runs(bench)) - set(started))
        watermark_after_unanswered = _watermark(bench)
        reselected = _answer(bench, checkout, record, "--dry-run", "--to", BOARD)

        return Life(
            bench=bench,
            checkout=checkout,
            runs=runs,
            issues=issues,
            ids=ids,
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
        )
    finally:
        for run in started:
            if (bench.runs / run).exists():
                _run(["just", "stop", run], bench)


def _url(life: Life, issue: str, identifier: str) -> str:
    location = _item(life.bench, issue)["location"]
    assert isinstance(location, dict), location
    return comments.comment_url_parts(None, str(location["path"]), identifier, None, issue)


def _named(report: str) -> Counter[str]:
    """Every comment URL a report names as selected or left out, with how often."""
    return Counter(
        line.split(": ", 1)[1].split(" on ", 1)[0]
        for line in report.splitlines()
        if line.startswith(("selected: ", "left out: "))
    )


def _located(item: dict[str, object]) -> str:
    """Where a listed item lives on the stand-in board, which its comment URLs are built on."""
    held = item["item"]
    assert isinstance(held, dict), item
    location = held["location"]
    assert isinstance(location, dict), held
    return str(location["path"])


def _held_urls(recorded: list[Recorded]) -> Counter[str]:
    """Every comment the store answered the gathering on the items its query returned.

    The gathering reads each item's comments once, before anything is launched, so its read
    is the first recorded for that item; a later one is a check reading back replies the
    dispatches posted since the report was written.
    """
    located = {str(item["id"]): _located(item) for item in _listed_items(recorded)}
    first: dict[str, Recorded] = {}
    for one in recorded:
        if one.argv[:3] == ["task", "comment", "list"] and one.argv[3] in located:
            first.setdefault(one.argv[3], one)
    return Counter(
        comments.comment_url_parts(None, located[issue], comment["id"], None, issue)
        for issue, one in first.items()
        for comment in json.loads(one.out or "{}")["comments"]
    )


def _line_for(report: str, url: str) -> str:
    (line,) = [line for line in report.splitlines() if f" {url} on " in line]
    return line


def _settled(life: Life) -> None:
    assert life.real.result.returncode == OK, (
        life.real.report + life.real.result.stderr + _ran(life.bench)
    )


def test_every_listing_the_command_makes_is_narrowed_and_reads_comments_only_where_it_returned(
    life: Life,
) -> None:
    """Both properties on a real run, read off the store: no board listing the store did not
    narrow to this host's items, and no comment read of an item the narrowed listing did not
    return — and nothing names a repository, an owner or a repository set."""
    _settled(life)
    for invocation in (life.dry, life.real, life.again, life.detached):
        listings = _board_listings(invocation.recorded)
        assert listings, invocation.recorded
        for one in listings:
            flags = {word for word in one.argv if word.startswith("--")}
            assert "--commented-since" in flags, one.argv
            assert _option(one.argv, "--metadata") == comments.host_query(HOST), one.argv
            assert flags <= {
                "--source",
                "--commented-since",
                "--metadata",
                "--page",
                "--json",
                "--no-interactive",
            }, one.argv
        returned = {str(item["id"]) for item in _listed_items(invocation.recorded)}
        read = [
            one.argv[3]
            for one in invocation.recorded
            if one.argv[:3] == ["task", "comment", "list"]
        ]
        assert read, invocation.recorded
        assert set(read) <= returned, (set(read) - returned, returned)
        # The quiet item nobody touched since the planted watermark is never returned, nor
        # read, by the gatherings that start there; the later ones reach back over it.
        if invocation in (life.dry, life.real):
            assert life.issues["c-quiet"] not in returned


def test_every_page_of_the_narrowed_answer_is_read_and_both_runs_on_different_pages_are_sent(
    life: Life,
) -> None:
    _settled(life)
    listings = _board_listings(life.real.recorded)
    assert len(listings) > 1, "the narrowed answer fit one page; the premise did not hold"
    cursors = [_option(one.argv, "--page") for one in listings]
    answered = [json.loads(one.out or "{}")["next"] for one in listings]
    assert cursors == [None, *answered[:-1]], (cursors, answered)
    assert answered[-1] is None
    pages = [{str(item["id"]) for item in json.loads(one.out or "{}")["items"]} for one in listings]
    page_of = {issue: at for at, page in enumerate(pages) for issue in page}
    assert page_of[life.issues["a"]] != page_of[life.issues["b"]], pages
    report = life.real.report
    assert _line_for(report, _url(life, life.issues["a"], life.ids[ON_A])).endswith(
        f"goes to run {life.runs['A']}"
    )
    assert _line_for(report, _url(life, life.issues["b"], life.ids[ON_B])).endswith(
        f"goes to run {life.runs['B']}"
    )


def test_the_dry_run_names_where_each_comment_would_go_and_why_the_rest_are_left_out(
    life: Life,
) -> None:
    dry = life.dry
    assert dry.result.returncode == OK, dry.report + dry.result.stderr
    runs, issues, ids = life.runs, life.issues, life.ids

    def said(key: str, text: str) -> str:
        return _line_for(dry.report, _url(life, issues[key], ids[text]))

    assert said("a", ON_A).endswith(f"would go to run {runs['A']}")
    assert said("a", EDITED).endswith(f"would go to run {runs['A']}")
    assert said("a-deferred", ON_DEFERRED).endswith(f"would go to run {runs['A']}")
    assert said("a-foreign", FOREIGN).endswith(f"would go to run {runs['A']}")
    assert said("b", ON_B).endswith(f"would go to run {runs['B']}")
    assert said("c", ON_C).endswith(f"would go to run {runs['C']}")
    assert said("a", ANSWERED_ELSEWHERE).endswith(f": answered by reply {life.elsewhere_reply}")
    marker = next(text for text in ids if text.startswith("<!--") or "Seen in A." in text)
    assert said("c", marker).endswith(f": marked by run {runs['A']}")
    # Verified on another host, so the store's narrowing never returned it to be read.
    assert issues["a-elsewhere"] not in dry.report
    status = _item(life.bench, issues["a-todo"])["status"]
    assert isinstance(status, dict)
    assert said("a-todo", ON_TODO).endswith(f": item at {status['name']}")
    assert said("a", BY_APP).endswith(f": bot author {APP}")
    assert f"at or before run {runs['C']}'s boundary " in said("c", BEFORE_BOUNDARY)
    assert said("g", ON_GONE).endswith(f": no records for owning run {runs['G']} here")
    assert _url(life, issues["c-quiet"], ids[QUIET_OLD]) not in dry.report
    assert issues["c-quiet"] not in dry.report


def test_the_dry_run_launches_nothing_and_writes_nothing(life: Life) -> None:
    assert life.dry.result.returncode == OK, life.dry.report + life.dry.result.stderr
    # The snapshot was taken right after the dry run, before any launch: the only
    # feedback directory is the one no gathering wrote, and the watermark is as planted.
    assert not [path for path in life.after_dry if "/feedback/" in path and path.endswith(".md")]
    assert (
        life.after_dry[str(comments.watermark_path(life.bench.drafts_root, BOARD))]
        == life.watermark_before
    )
    assert not any(comments.BOUNDARY_FILE in path for path in life.after_dry)
    assert "wrote run" not in life.dry.report


@pytest.mark.parametrize("phase", ["dry", "real"])
def test_each_report_names_every_returned_comment_once_and_ends_with_the_store_it_read(
    life: Life, phase: str
) -> None:
    invocation = life.dry if phase == "dry" else life.real
    assert invocation.result.returncode == OK, invocation.report + invocation.result.stderr
    named = _named(invocation.report)
    assert set(named.values()) == {1}, named
    assert named == _held_urls(invocation.recorded), (named, _held_urls(invocation.recorded))
    version = subprocess.run(  # noqa: S603 - the pinned store this checkout installs
        [str(ONETASKGRAPH_BIN), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    items = len(_listed_items(invocation.recorded))
    assert invocation.report.splitlines()[-1] == (
        f"plan store: {version}; the narrowed query returned {items} item(s) commented on "
        f"since {invocation.since()}"
    )


def test_the_real_gathering_launches_one_run_per_owning_run_all_at_once(life: Life) -> None:
    _settled(life)
    runs = life.runs
    assert life.launched == sorted(f"{runs[key]}{SUFFIX}" for key in ("A", "B", "C"))
    # Each run's turn waited until all three launch records existed before it answered.
    assert life.witnesses == dict.fromkeys(("A", "B", "C"), "every launch record existed")
    assert life.stored.metadata["onepipeline.id"] == "follow-ups"
    assert life.stored.checked.returncode == OK, life.stored.checked.stderr


def test_each_feedback_file_quotes_only_its_own_runs_comments(life: Life) -> None:
    _settled(life)
    quoted = {
        key: {one.comment for one in tickets.quoted_comments(text)}
        for key, text in life.feedback.items()
    }
    ids = life.ids
    assert quoted == {
        "A": {ids[ON_A], ids[EDITED], ids[ON_DEFERRED], ids[FOREIGN]},
        "B": {ids[ON_B]},
        "C": {ids[ON_C]},
    }
    assert EDITED_AGAIN.rstrip() in life.feedback["A"]
    assert FOREIGN_EDITED.rstrip() in life.feedback["A"]


def test_every_quoted_comment_carries_exactly_one_reply_and_each_run_checked_sound(
    life: Life,
) -> None:
    _settled(life)
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
            url = _url(life, one.issue, str(replies[0]["id"]))
            block = life.real.report.split(f"run {run} (follow-up run ", 1)[1]
            assert block.split("\n", 1)[0].endswith("check-responses passed"), block
            assert f"  reply: {url}" in block.split("\nrun ", 1)[0], block


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
    held = json.loads(life.watermark_after_real)
    queried = comments.moment(held["queried_at"], "the watermark")
    assert life.real_started.replace(microsecond=0) <= queried <= life.real_ended
    assert comments.moment(held["since"], "the watermark") == queried - comments.OVERLAP
    assert f"watermark: advanced to {held['since']}" in life.real.report
    assert life.again.since() == held["since"]


def test_a_second_gathering_selects_nothing_launches_nothing_and_says_so(life: Life) -> None:
    _settled(life)
    again = life.again
    assert again.result.returncode == OK, again.report + again.result.stderr
    assert "nothing to answer: no comment was selected, so nothing was launched" in again.report
    assert "selected: " not in again.report
    assert "launched follow-up run" not in again.report
    assert "wrote run" not in again.report


# llmlint: ignore-block[shell_test_tiers_stay_split] Its tier is the module's: it drives the
# recipe every journey here drives, from the `plan-tooling` project whose
# `planToolingWorkspace` key already covers what it reads, so a project of its own would
# split one recipe's journeys across tiers behind no narrower key.
def test_a_run_scoped_gathering_launches_only_that_run_and_leaves_the_watermark(
    life: Life,
) -> None:
    scoped = life.scoped
    assert scoped.result.returncode == OK, scoped.report + scoped.result.stderr + _ran(life.bench)
    runs = life.runs
    assert set(life.launched_after_scoped) - set(life.launched) == {f"{runs['A']}{SUFFIX}-2"}
    for line in scoped.report.splitlines():
        if line.startswith(("selected: ", "left out: ")):
            issue = line.split(" on ", 1)[1].split(" ", 1)[0].rstrip(":")
            assert issue.split(":", 1)[1].startswith(f"{runs['A']}/"), line
    assert _url(life, life.issues["b"], life.ids[LATER_B]) not in scoped.report
    assert life.watermark_after_scoped == life.watermark_after_again
    assert f"the gathering was scoped to run {runs['A']}'s comments" in scoped.report
    # Each item run A's tickets are bound to, read by id: the board is listed nowhere.
    assert _board_listings(scoped.recorded) == []
    bound = {issue for key, issue in life.issues.items() if key.startswith("a")}
    shown = {one.argv[2] for one in scoped.recorded if one.argv[:2] == ["task", "show"]}
    read = {one.argv[3] for one in scoped.recorded if one.argv[:3] == ["task", "comment", "list"]}
    # `check-responses` also reads the local ticket of each issue it answered on, to hold it
    # to its shape: run A's tickets in the drafts source, and nothing of the board.
    local = {one for one in shown if one.startswith(f"{tickets.SOURCE}:")}
    assert all(one.startswith(f"{tickets.SOURCE}:{runs['A']}/tickets/") for one in local), local
    assert shown - local == read == bound, (shown, read, bound)


# llmlint: ignore-end[shell_test_tiers_stay_split]


# llmlint: ignore-block[shell_test_tiers_stay_split] The task this journey answers names
# this module as where `--issue` is driven through the recipe, and its tier is the module's:
# it drives the recipe every journey here drives, from the `plan-tooling` project whose
# `planToolingWorkspace` key already covers what it reads, so a project of its own would
# split one recipe's journeys across tiers behind no narrower key.
def test_an_issue_scoped_dry_run_reads_and_selects_only_that_issue(life: Life) -> None:
    """`--issue <id> --dry-run`: that one item and its comments, read by id with no listing,
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
        f"selected: {_url(life, own, life.ids[text])}" for text in (ON_A, EDITED)
    }
    assert _board_listings(issue_dry.recorded) == []
    shown = [one.argv[2] for one in issue_dry.recorded if one.argv[:2] == ["task", "show"]]
    read = [
        one.argv[3] for one in issue_dry.recorded if one.argv[:3] == ["task", "comment", "list"]
    ]
    assert shown == read == [own], (shown, read)
    assert "wrote run" not in issue_dry.report
    assert issue_dry.report.splitlines()[-1].endswith(
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
    assert _line_for(detached.report, _url(life, life.issues["b"], life.ids[LATER_B])).endswith(
        f"goes to run {run}"
    )
    assert life.detached_replies_at_return == [], "the run answered before the recipe returned"
    follow_up = f"{run}{SUFFIX}-2"
    assert (life.bench.runs / follow_up / "launch.json").is_file()
    assert f"run {run}: launched follow-up run {follow_up}; watch it with: just watch " in (
        detached.report
    )
    assert (
        f"check it with: {shlex.quote(str(life.checkout / '.venv' / 'bin' / 'python3'))}"
        in (detached.report)
        or "check it with: " in detached.report
    )
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
    url = _url(life, life.issues["a"], life.ids[UNANSWERED_A])
    assert _line_for(life.reselected.report, url).endswith(f"would go to run {run}")


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] These drive the same
# recipe from the same `plan-tooling` project, whose `planToolingWorkspace` key already covers
# everything they read; none of them launches, and a project of their own would split one
# recipe's journeys across tiers behind no narrower key.


class Board(NamedTuple):
    bench: Bench
    checkout: Path
    record: Path


@pytest.fixture
def board(tmp_path: Path) -> Board:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path)
    bench.environment["UV_NO_SYNC"] = "1"
    return Board(bench, _launching_checkout(tmp_path), tmp_path / "store-record.jsonl")


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
    _commented(bench, issue, ON_A, REVIEWER)

    scratch = board.bench.tmp / "tmpdir"
    scratch.mkdir()
    dry = _answer(
        bench,
        board.checkout,
        board.record,
        "--dry-run",
        "--to",
        BOARD,
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

    refused = _answer(bench, board.checkout, board.record, "--to", BOARD)

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"run(s) {run} under {bench.drafts_root}" in refused.result.stderr
    assert "--since <RFC3339>" in refused.result.stderr
    assert _board_listings(refused.recorded) == []
    assert _follow_up_runs(bench) == []

    since = "2026-02-03T04:05:06Z"
    named = _answer(
        bench, board.checkout, board.record, "--dry-run", "--since", since, "--to", BOARD
    )

    assert named.result.returncode == OK, named.report + named.result.stderr
    assert {_option(one.argv, "--commented-since") for one in _board_listings(named.recorded)} == {
        since
    }


def test_a_host_where_no_run_filed_a_ticket_reads_nothing_and_launches_nothing(
    board: Board,
) -> None:
    quiet = _answer(board.bench, board.checkout, board.record, "--to", BOARD)

    assert quiet.result.returncode == OK, quiet.report + quiet.result.stderr
    assert "has filed a follow-up ticket, so no comment on" in quiet.report
    assert "nothing was read and nothing was launched" in quiet.report
    assert _board_listings(quiet.recorded) == []
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

    refused = _answer(board.bench, board.checkout, board.record, "--to", BOARD)

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"the watermark {path}" in refused.result.stderr
    assert refusal in refused.result.stderr
    assert _board_listings(refused.recorded) == []
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
    _filed(bench, run, MIRROR_CAUSE)
    written = tickets.ticket_path(bench.drafts_root, run, MIRROR_CAUSE).stat().st_mtime
    boundary = datetime.fromtimestamp(written, UTC)

    gathered = _answer(
        bench, board.checkout, board.record, "--since", "2026-01-01T00:00:00Z", "--to", BOARD
    )

    assert gathered.result.returncode == OK, gathered.report + gathered.result.stderr
    location = _item(bench, issue)["location"]
    assert isinstance(location, dict), location
    url = comments.comment_url_parts(None, str(location["path"]), asked, None, issue)
    assert _line_for(gathered.report, url).endswith(
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
        bench,
        board.checkout,
        board.record,
        "--dry-run",
        "--since",
        "2026-01-02T00:00:00Z",
        "--to",
        BOARD,
    )
    assert named.result.returncode == OK, named.report + named.result.stderr
    assert named.since() == "2026-01-02T00:00:00Z"
    assert "(named with --since)" in named.report.splitlines()[0]


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
    assert _board_listings(refused.recorded) == []


def test_a_board_the_store_cannot_read_is_refused_naming_what_to_check(board: Board) -> None:
    _filed(board.bench, tickets.RunId(f"nb-{os.getpid()}"), "listing-cursor-skips-last-page")

    refused = _answer(board.bench, board.checkout, board.record, "--to", "no-such-board")

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert "Check that --to names a source this checkout's plan store configures" in (
        refused.result.stderr
    )
    assert _follow_up_runs(board.bench) == []


def test_naming_no_board_reads_the_followups_board_and_launches_nothing_without_it(
    board: Board,
) -> None:
    """The default is the live `followups` board, which no journey holds a credential for."""
    _filed(board.bench, tickets.RunId(f"df-{os.getpid()}"), "listing-cursor-skips-last-page")
    environment = {
        name: value
        for name, value in board.bench.environment.items()
        if name != "GH_PROJECTS_TOKEN"
    }
    # The store falls back to its machine-wide secrets file for a name the environment
    # lacks, and a host that keeps the board's token there would read the live board;
    # naming a file that does not exist keeps this host's token out of the journey.
    environment["ONETASKGRAPH_SECRETS_FILE"] = str(board.bench.tmp / "no-secrets.env")

    refused = _answer(board.bench, board.checkout, board.record, environment=environment)

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"source {tickets.BOARD} could not answer" in refused.result.stderr
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
    assert _board_listings(refused.recorded) == []


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
        "--to",
        BOARD,
        environment=board.bench.environment | {"TMPDIR": str(not_a_directory)},
    )

    assert refused.result.returncode == REFUSED, refused.report + refused.result.stderr
    assert f"{RECIPE}: a scratch directory for the gathering could not be created" in (
        refused.result.stderr
    )
    assert _board_listings(refused.recorded) == []


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
#: own answer over a `local-md` board needs none.
CREDENTIAL_RECORDING_STORE = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "${GH_PROJECTS_TOKEN-<unset>}" >>"$CREDENTIAL_TRACE"
exec "$REAL_PLAN_STORE" "$@"
"""

#: `scripts/follow-ups.sh`'s own refusal where the mirror's launch stops: that checkout
#: carries this repository's `scripts/`, `justfile` and a plan store and nothing else, so
#: there is no engine to state the follow-up task's template with — which is past the board
#: read this mirror exists for, and before any task is created or anything launched.
NO_ENGINE = "has no engine at"

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


def _mirror(
    tmp_path: Path, credentials: str | None, store: str = CREDENTIAL_RECORDING_STORE
) -> Mirror:
    """The recipe's checkout, the board it reads, and the run that owns an issue there.

    `scripts/credentials-env.sh` reads the `.env` at the root of the checkout it is sourced
    from, and this checkout's is the host's — a real file, never a journey's to write. So the
    recipe is driven from a copy of the real `scripts/` and `justfile` beside a toolchain
    mirroring this checkout's (:func:`_mirrored_toolchain`) whose plan store is ``store``,
    with the `.env` this journey states. The board is the same `local-md` stand-in every
    journey here reads, holding one issue the run owns with a person's comment nobody
    answered. That checkout carries no engine, so a launch stops where its task would be
    created — after the board read this exists for, and before any launch.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path)
    checkout = tmp_path / "mirror"
    shutil.copytree(REPO_ROOT / "scripts", checkout / "scripts")
    shutil.copy2(REPO_ROOT / "justfile", checkout / "justfile")
    _mirrored_toolchain(checkout, store)
    if credentials is not None:
        (checkout / ".env").write_text(credentials, encoding="utf-8")
    run = tickets.RunId(f"ac-mirror-{os.getpid()}")
    issue = _filed(bench, run, MIRROR_CAUSE)
    _next_second()
    _commented(bench, issue, ON_MIRROR, REVIEWER)
    return Mirror(checkout, tmp_path / "credential.trace", bench, run, issue)


def _answered_from(
    mirror: Mirror,
    credential: str | None,
    *,
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
    # llmlint: ignore[e2e_not_mocked] The pinned store answers every command; what stands in front of it, as the mirror's own locked plan store, records which credential it was handed — on no command line and in no answer over a `local-md` board — or rewrites one field of one answer, and changes nothing else the recipe reads.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    environment["CREDENTIAL_TRACE"] = str(mirror.trace)
    return subprocess.run(  # noqa: S603 - this checkout's own recipe over the installed store
        ["just", RECIPE, "--to", BOARD],  # noqa: S607 - `just` from the search path, as an operator invokes it
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
# each drives the recipe every journey of this module drives, from the `plan-tooling`
# project whose `planToolingWorkspace` key already covers everything they read — the
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
    mirror = _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")

    result = _answered_from(mirror, held)

    assert result.returncode == UNANSWERED, result.stdout + result.stderr
    assert _handed(mirror) == {handed}, mirror.trace.read_text(encoding="utf-8")
    (feedback,) = (mirror.bench.drafts_root / comments.FEEDBACK_DIRECTORY / mirror.run).glob(
        "2*.md"
    )
    assert ON_MIRROR.rstrip() in feedback.read_text(encoding="utf-8")
    assert NO_ENGINE in result.stderr, result.stderr
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
    """The report's last line names the release it ran against, or says it could not."""
    # llmlint: ignore[e2e_not_mocked] The published CLI is the one boundary this suite doubles a store at, and no pinned store fails its own `--version`: this answers every other command with the pinned store's own answer.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    mirror = _mirror(tmp_path, None, VERSIONLESS_STORE)
    environment = mirror.bench.environment | {"CREDENTIAL_TRACE": str(mirror.trace)}

    dry = subprocess.run(  # noqa: S603 - this checkout's own recipe over the installed store
        ["just", RECIPE, "--dry-run", "--to", BOARD],  # noqa: S607 - `just` from the search path, as an operator invokes it
        cwd=mirror.checkout,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )

    assert dry.returncode == OK, dry.stdout + dry.stderr
    store = mirror.checkout / ".venv" / "bin" / ONETASKGRAPH_BIN.name
    assert dry.stdout.splitlines()[-1].startswith(
        f"plan store: {store}, which reported no version; the narrowed query returned 1 item(s)"
    ), dry.stdout


def test_a_credential_file_the_helper_refuses_refuses_the_recipe_before_the_board_is_read(
    tmp_path: Path,
) -> None:
    """A malformed `.env` is refused whole, under this recipe's own name, before any read."""
    mirror = _mirror(tmp_path, "not-an-assignment-with-a-value-that-must-not-appear\n")

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
    mirror = _mirror(tmp_path, None)
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
    mirror = _mirror(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")
    helper = mirror.checkout / "scripts" / "credentials-env.sh"
    match sabotage:
        case "missing":
            helper.unlink()
        case "unreadable":
            helper.chmod(0)
            if os.access(helper, os.R_OK):
                pytest.skip("this user reads a file with no permission bits: nothing is unreadable")
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
#: the store was asked rather than inferred.
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
    """Followed again, the same page would be gathered twice — or, from a cursor that never
    advances, for ever: refused naming it, and nothing is written or launched."""
    # llmlint: ignore[e2e_not_mocked] The published CLI is the one boundary this suite doubles a store at, and no real store answers a cursor twice to drive this against: the pinned store answers every command, and one field of one listing's answer is rewritten.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    mirror = _mirror(tmp_path, None, REPEATING_STORE)
    asked_log = tmp_path / "pages-asked.log"

    refused = _answered_from(
        mirror,
        None,
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
    assert "--commented-since" in asked[0] and "--page" not in asked[0], asked
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

    refused = _run(["just", "follow-ups", "some-run", "--comments", "--to", BOARD], bench)

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
