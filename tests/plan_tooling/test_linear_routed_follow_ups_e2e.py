"""A petsinc root cause's follow-up ticket lands in Hello Patient's Linear, the rest on the board.

The tracked `followups` board routes every `github.com/petsinc/*` repository to the
`hellopatient-followups` Linear source, and every follow-up command takes `followups` as the
root of those boards and acts on each ticket at the board its repository routes it to. One
copy of this checkout carries the whole life of a ticket on each destination, phase by phase,
through the real recipes:

* **filing** — `just follow-ups` over a run whose drafts name a petsinc root cause and a
  nickderobertis one files the first in Linear at `Proposed` and the second on the board at
  `Proposal`, and its attached closeout's board check across both destinations is green;
* **re-dispatch** — the same run re-dispatched binds both tickets and files no duplicate;
* **the seeded search** — a later run whose root causes another run already filed on each
  destination finds each ticket through `board-items`' search by root cause, asking no
  question that lists the board or the scoped project, and comments its evidence there;
* **evidence and re-estimate** — that evidence comment and its `re-estimate` raise the Linear
  ticket's estimate by the occurrence rule, and a priority a person then sets stands;
* **comments** — a person's comment on the Linear ticket is gathered by
  `just follow-ups-answer-comments`, routed to the run owning that ticket, and answered once,
  each board of the family keeping a watermark of its own;
* **delivery** — a plan node delivering the Linear ticket at `Todo` moves it to `Queued`
  before any worker turn and to `Done` when the node settles.

**`hellopatient-followups` is a folder of Markdown here** (`linear_stand_in.py` beside this
module says why and how), and the copy's own plan-store CLI records each command it is handed
before running the real one, which is how the seeded search is shown to list nothing.
GitHub's Projects API is the loopback board `tests/github_board.py` serves, and the paid model
is the provider's stand-in. The recipes, the engine, the store and its routing, the follow-up
tooling, and every record read back are real.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import github_board
import plan_root_variable
import pytest
import short_state
from fake_backend import TURN_GATE_ENV, TURN_GATE_REACHED, TURN_GATE_RELEASED
from github_board import (
    FOLLOWUPS_ENV_PREFIX,
    FOLLOWUPS_OPTIONS,
    FOLLOWUPS_PROJECT_NUMBER,
    LINEAR_FOLLOWUPS_ENDPOINT_ENV,
    SIBLING_REPOSITORY,
    _board_cost,
    _follow_up_ticket,
    _GitHubFixture,
    _hosted,
    _Repository,
    _serving_board,
)
from linear_stand_in import provisioned, stand_in
from nx_workspace import answering_this_checkouts_origin, copy_working_tree
from project_fixtures import designed, helper
from waits import timeout as e2e_timeout

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_check
from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.project_store import write_plan_project

#: Building a copy of this checkout reads everything git tracks, which routes this module to
#: the tier that reads this repository's prose; and its one fixture runs on one worker.
pytestmark = [pytest.mark.reads_docs, pytest.mark.xdist_group("linear-follow-ups")]

#: The provider's stand-ins and the guard over every identity they cannot reach: the follow-up
#: member's own, which refuses an untrusted directory the way codex does, and the pair a
#: launched plan's two-party members are answered by.
FAKE_CODEX_UNTRUSTED = helper("fake_codex_untrusted_directory.py")
FAKE_CODEX = helper("fake_codex.py")
FAKE_BACKEND = helper("fake_backend.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
#: A reviewer's one answer for a launched plan: a pass naming no finding.
PASSING_VERDICT = json.dumps({"passes": True, "findings": []})

#: The two destinations, by the names the tracked configuration gives them.
BOARD = tickets.BOARD
LINEAR = "hellopatient-followups"

#: The repository each destination files: a petsinc one the board's route sends to Linear, and
#: one of the board's owner the loopback board knows.
PETSINC = _Repository("petsinc", "hp-api")
LIBRARY = SIBLING_REPOSITORY

#: The runs of this life: the one that files, the one that already filed what a later run
#: finds, and that later run; and the two runs whose evidence only ever arrives as a comment.
PID = os.getpid()
FILER = f"fu-filer-{PID}"
EARLIER = f"fu-earlier-{PID}"
LATER = f"fu-later-{PID}"
WITNESSED = f"fu-witness-{PID}"
LATEST = f"fu-latest-{PID}"

#: The filer's root causes, one per destination, and the later run's, which the earlier run
#: already filed on each.
FILED_LINEAR = "intake-form-drops-a-field"
FILED_BOARD = "listing-cursor-skips-last-page"
SEEDED_LINEAR = "intake-sync-retries-forever"
SEEDED_BOARD = "export-drops-a-column"

#: The machine this journey runs on, which a ticket's record and evidence name.
HOST = socket.gethostname()

#: The launching session these launches state, and what an enclosing dispatch would
#: otherwise decide for them.
LAUNCHING_SESSION = "e2e-linear-routed-follow-ups"
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ONEVCS_SESSION",
    "ORCHESTRATOR_ASK_MANAGER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "FAKE_CODEX_HOLD_SECONDS",
    "VIRTUAL_ENV",
    *follow_up_variables.all_names(),
    plan_root_variable.name(),
)

#: A person's comment on the filer's Linear ticket, and the reply the answering agent posts.
ASKED = "Does the intake form drop the field on the mobile app as well?\n"
ANSWERED = "It does: the verification at this run's basis covered both clients."

#: The priority a person sets the earlier run's Linear ticket to, which no estimate rewrites.
HELD_PRIORITY = tickets.Priority.LOW

#: The plan a node of which delivers the filer's Linear ticket.
DELIVERING_PROJECT = f"delivering-{PID}"
FIRST_NODE = "first"
DELIVERING_NODE = "delivering"

#: The copy's plan-store CLI: what it was handed, recorded one JSON line apiece, then the
#: installed program run on the same arguments. Recording is the whole of what it adds.
RECORDING_STORE = """#!@PYTHON@
import fcntl, json, os, sys
if "STORE_RECORD" in os.environ:
    with open(os.environ["STORE_RECORD"], "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(sys.argv[1:]) + "\\n")
os.execv("@REAL@", ["@REAL@", *sys.argv[1:]])
"""

#: The agent's own program for its run's disposition account: read the artifact the recipe
#: opened before the dispatch, fill in its `dispositions`, and leave its `drafts` as they are.
ACCOUNT_PROGRAM = """\
import json, pathlib, sys

path = pathlib.Path(sys.argv[1])
held = json.loads(path.read_text(encoding="utf-8"))
held["dispositions"] = json.loads(sys.argv[2])
path.write_text(json.dumps(held, indent=2), encoding="utf-8")
"""

#: The answering agent's own program: read the gathering its run's task quotes, post one reply
#: to each comment there on the issue holding it, and account for each reply.
REPLY_PROGRAM = """\
import json, re, subprocess, sys, tempfile
from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets

log, run, store, response = sys.argv[1:5]
with open(log, encoding="utf-8") as stream:
    prompts = [json.loads(line)["prompt"] for line in stream.read().splitlines()]
task = [prompt for prompt in prompts if f"/{comments.FEEDBACK_DIRECTORY}/{run}/" in prompt][-1]
account = re.search(r"account is a JSON document at `([^`]+)`", task)[1]
gathered = re.search(r'"feedback": "([^"]+)"', task)[1]
gathering = tickets.read_gathering(task.split("## The comments to answer", 1)[1])
responses = []
for section, one in zip(task.split("### Comment ")[1:], gathering.quoted, strict=True):
    fields = dict(
        line[2:].split(": ", 1) for line in section.splitlines() if line.startswith("- ")
    )
    shown = json.loads(subprocess.run([store, "task", "show", one.issue, "--json"], check=True,
                                      capture_output=True, text=True).stdout)
    cause = shown["items"][0]["item"]["metadata"][tickets.KEY]["root_cause"]
    reply = tickets.render_reply(
        run, cause, answers=one.comment, url=fields["URL"], author=fields["Author"],
        response=response, verdict=tickets.Verdict.DOES_NOT_CONFIRM,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as body:
        body.write(reply)
    added = json.loads(subprocess.run(
        [store, "task", "comment", "add", one.issue, "--body-file", body.name, "--json"],
        check=True, capture_output=True, text=True).stdout)
    responses.append({"comment": one.comment, "issue": one.issue, "action": response,
                      "reply": added["id"], "verdict": tickets.Verdict.DOES_NOT_CONFIRM.value})
with open(account, "w", encoding="utf-8") as stream:
    json.dump({"schema": tickets.RESPONSES_SCHEMA, "run": run, "feedback": gathered,
               "responses": responses}, stream, indent=2)
"""


class Bench(NamedTuple):
    """The copied checkout every recipe runs in, its stores, and the environment they share."""

    checkout: Path
    linear: Path
    tmp: Path
    drafts_root: Path
    authoring: Path
    runs: Path
    record: Path
    environment: dict[str, str]

    @property
    def python(self) -> str:
        """The copy's own interpreter, which imports the copy's own package."""
        return str(self.checkout / ".venv" / "bin" / "python3")

    @property
    def store(self) -> str:
        """The copy's own plan-store CLI, the recording one."""
        return str(self.checkout / ".venv" / "bin" / "onetaskgraph")


def _recording(checkout: Path) -> None:
    """Install the recording plan-store CLI as the copy's own, in front of the installed one."""
    installed = checkout / ".venv" / "bin" / "onetaskgraph"
    real = installed.with_name("onetaskgraph-installed")
    installed.rename(real)
    # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] The published CLI is the one boundary this suite doubles a store at, and this one delegates every command to the installed program unchanged: no real store reports which commands it was asked, which is what says the seeded search lists nothing.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    installed.write_text(
        RECORDING_STORE.replace("@PYTHON@", str(checkout / ".venv" / "bin" / "python3")).replace(
            "@REAL@", str(real)
        ),
        encoding="utf-8",
    )
    installed.chmod(0o755)


def _environment(tmp: Path, remote: Mapping[str, str]) -> dict[str, str]:
    """What every launch here runs under: its own stores, ledger and provider stand-ins."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in INHERITED and not name.startswith(FOLLOWUPS_ENV_PREFIX)
    }
    # The copy's `hellopatient-followups` is a folder, which takes no Linear endpoint.
    environment.update(
        {name: value for name, value in remote.items() if name != LINEAR_FOLLOWUPS_ENDPOINT_ENV}
    )
    environment[f"{FOLLOWUPS_ENV_PREFIX}CONFIG__PACING__MIN_MUTATION_INTERVAL_MS"] = "0"
    root_name, plugin_name, _ = follow_up_variables.all_names()
    environment[root_name] = str(tmp / "follow-ups")
    environment[plugin_name] = WRITABLE_PLUGIN
    environment[plan_root_variable.name()] = str(tmp / "authoring")
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp / "runs")
    environment["ONETASKGRAPH_SECRETS_FILE"] = str(tmp / "no-secrets.env")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp))
    environment["ONEAGENTGRAPH_STATE_DIR"] = str(tmp / "graph-state")
    environment["FAKE_CODEX_RUN_ON_MARKER"] = str(tmp / "commands.json")
    environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(tmp / "commands-ran.jsonl")
    environment["FAKE_CODEX_PROMPT_LOG"] = str(tmp / "prompts.jsonl")
    environment["STORE_RECORD"] = str(tmp / "store-calls.jsonl")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX_UNTRUSTED)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    # A follow-up task is rendered from its template, so its launch runs under the
    # `require_rendered` default every launch outside the suite does.
    environment.pop(plan_check.REQUIRE_RENDERED_ENV, None)
    return environment


def _run(bench: Bench, *command: str, seconds: float = 900) -> subprocess.CompletedProcess[str]:
    """One command run in the copied checkout, as an operator runs it there."""
    return subprocess.run(  # noqa: S603 - the copy's own recipes and installed programs
        list(command),
        cwd=bench.checkout,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _store(bench: Bench, *arguments: str) -> dict[str, object]:
    shown = _run(bench, bench.store, *arguments, "--json", seconds=120)
    assert shown.returncode == 0, shown.stdout + shown.stderr
    payload: object = json.loads(shown.stdout)
    assert isinstance(payload, dict), payload
    return payload


def _one(listed: object) -> dict[str, object]:
    """The one entry a store answer's `items` holds."""
    assert isinstance(listed, list) and len(listed) == 1, listed
    (entry,) = listed
    assert isinstance(entry, dict), entry
    return entry


def _item(bench: Bench, qualified: str) -> dict[str, object]:
    read = _one(_store(bench, "task", "show", qualified)["items"])
    item = read["item"]
    assert isinstance(item, dict), read
    return item


def _status(item: Mapping[str, object]) -> tuple[str, str]:
    """An item's status category and the name its source reads it at, without case.

    The stand-in folder writes a state name as its lower-case key; Linear answers the name as
    the mapping spells it, so the name is compared without case. A copy carries a status by
    its category's own word, which the folder keeps as it is handed it, where Linear writes
    the state its mapping names for that category.
    """
    status = item["status"]
    assert isinstance(status, dict), item
    return str(status["category"]), str(status["name"]).casefold()


def _comments(bench: Bench, qualified: str) -> list[dict[str, object]]:
    listed = _store(bench, "task", "comment", "list", qualified)["comments"]
    assert isinstance(listed, list), listed
    return listed


def _record(item: Mapping[str, object]) -> Mapping[str, object]:
    metadata = item["metadata"]
    assert isinstance(metadata, dict), item
    held = metadata[tickets.KEY]
    assert isinstance(held, dict), metadata
    return held


def _linear_items(bench: Bench) -> list[str]:
    """Every item file the Linear stand-in holds, by path under its root."""
    return sorted(str(path.relative_to(bench.linear)) for path in bench.linear.rglob("*.md"))


def _board_issues() -> int:
    """How many issues have been created on the loopback board since it was served."""
    return len(github_board.BOARD.created)


def _draft(bench: Bench, run: str, title: str, repository: _Repository) -> str:
    """One draft against ``run``, through the real drafting command a manager uses; its id."""
    drafted = subprocess.run(  # noqa: S603 - the drafting command every party drafts with
        [str(bench.checkout / "scripts" / "follow-up-draft.sh"), "--as", "manager"]
        + ["--run", run, "--title", title, "--repository", _hosted(repository)]
        + ["--path", "src/intake.py"],
        cwd=bench.tmp,
        env=bench.environment,
        input=(
            "## What happened\nA field went missing.\n\n## Where\n`src/intake.py`.\n\n"
            "## Why it is out of scope\nThe run was about something else.\n\n"
            "## Evidence\nThe saved record lacked it.\n"
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr
    path = Path(drafted.stdout.split(" at ", 1)[1].strip())
    return f"drafts:{run}/drafts/{path.stem}"


def _ticket(
    run: str, cause: str, repository: _Repository, drafts: tuple[str, ...]
) -> tickets.Ticket:
    """``run``'s ticket for ``cause``, verified on this machine, as an agent writes it.

    It carries no estimate, estimate line or priority, because those are `board-status`'s to
    write before the ticket is copied.
    """
    base = _follow_up_ticket(repository, cause=cause)
    return dataclasses.replace(
        base,
        created_by_run=tickets.RunId(run),
        owning_runs=(tickets.RunId(run),),
        drafts=tuple(tickets.QualifiedDraftId(draft) for draft in drafts),
        host=tickets.Host(HOST),
        body=base.body.replace(str(base.host), HOST),
    )


def _staged(bench: Bench, name: str, text: str) -> Path:
    """What the agent writes in its own scratch before putting it anywhere."""
    staged = bench.tmp / "agent" / name
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(text, encoding="utf-8")
    return staged


def _in_checkout(bench: Bench, script: str) -> list[str]:
    """A shell command the task tells the agent to run from the launching checkout.

    It names where the copy's plan store records what it is handed, because a dispatched
    turn's environment is the engine's to compose and carries no name of this journey's.
    """
    record = shlex.quote(str(bench.record))
    return [
        "bash",
        "-c",
        f"set -euo pipefail\nexport STORE_RECORD={record}\n"
        f"cd {shlex.quote(str(bench.checkout))}\n{script}",
    ]


def _module(bench: Bench) -> str:
    return f"{shlex.quote(bench.python)} -m orchestrator.follow_up_tickets"


def _searched(bench: Bench, repository: _Repository, cause: str, witness: Path) -> list[str]:
    """The task's search by root cause, on the board the ticket's repository routes it to."""
    return _in_checkout(
        bench,
        f"{_module(bench)} board-items --board {BOARD} --repository "
        f"{_hosted(repository)} --metadata {tickets.root_cause_query(cause)} "
        f"> {shlex.quote(str(witness))}",
    )


def _placed_and_copied(bench: Bench, staged: Path, ticket: Path, draft: str) -> list[str]:
    """Put a ticket in place, delete the draft it consumed, decide its status, validate, copy."""
    run, name = draft.removeprefix("drafts:").split("/drafts/")
    consumed = bench.drafts_root / "tasks" / run / "drafts" / f"{name}.md"
    module = _module(bench)
    return _in_checkout(
        bench,
        f"mkdir -p {shlex.quote(str(ticket.parent))}\n"
        f"cp {shlex.quote(str(staged))} {shlex.quote(str(ticket))}\n"
        f"rm -f {shlex.quote(str(consumed))}\n" + _decided_and_copied(module, ticket),
    )


def _decided_and_copied(module: str, ticket: Path) -> str:
    """The step before every copy: ask `board-status`, write the word, validate, then copy."""
    quoted = shlex.quote(str(ticket))
    return (
        f"word=$({module} board-status --board {BOARD} {quoted})\n"
        f'sed -i "s/^status: .*/status: \\"$word\\"/" {quoted}\n'
        f"{module} validate {quoted}\n"
        f"{module} copy --board {BOARD} {quoted}\n"
    )


def _accounting(bench: Bench, run: str, entries: list[dict[str, object]]) -> list[str]:
    """The agent's command accounting for every draft it was given."""
    helper_path = _staged(bench, f"account-{run}-{time.monotonic_ns()}.py", ACCOUNT_PROGRAM)
    return [
        bench.python,
        str(helper_path),
        str(tickets.dispositions_path(bench.drafts_root, run)),
        json.dumps(entries),
    ]


def _filed(draft: str, cause: str, detail: str) -> dict[str, object]:
    return {
        "draft": draft,
        "disposition": tickets.Disposition.FILED.value,
        "root_causes": [cause],
        "detail": detail,
    }


def _scripted(bench: Bench, marker: str, commands: list[list[str]]) -> None:
    """Tell the provider's stand-in which commands a turn whose task names ``marker`` runs."""
    Path(bench.environment["FAKE_CODEX_RUN_ON_MARKER"]).write_text(
        json.dumps({marker: commands}), encoding="utf-8"
    )


def _ran(bench: Bench) -> str:
    """What every scripted command reported, for a failure message to quote."""
    log = Path(bench.environment["FAKE_CODEX_RUN_ON_MARKER_LOG"])
    return log.read_text(encoding="utf-8") if log.is_file() else "(no scripted command ran)"


def _store_calls(bench: Bench) -> list[list[str]]:
    """Every command the copy's plan-store CLI was handed since the record was last cleared."""
    if not bench.record.is_file():
        return []
    return [json.loads(line) for line in bench.record.read_text(encoding="utf-8").splitlines()]


def _filed_earlier(bench: Bench, cause: str, repository: _Repository) -> str:
    """The earlier run's ticket for ``cause``, filed the way its run filed it; its item.

    Written beside that run's drafts, then decided, validated and copied through the follow-up
    commands the task prescribes, so its estimate and priority are the ones `board-status`
    wrote and its item the one the store's route placed it on.
    """
    path = tickets.ticket_path(bench.drafts_root, EARLIER, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    earlier = _ticket(EARLIER, cause, repository, (f"drafts:{EARLIER}/drafts/an-earlier-draft",))
    path.write_text(tickets.render(earlier), encoding="utf-8")
    filed = _run(bench, *_in_checkout(bench, _decided_and_copied(_module(bench), path)))
    assert filed.returncode == 0, filed.stdout + filed.stderr
    return str(json.loads(filed.stdout.strip().splitlines()[-1])["destination"])


def _commented(bench: Bench, issue: str, body: str, author: str | None = None) -> str:
    path = _staged(bench, f"comment-{time.monotonic_ns()}.md", body)
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


class Life(NamedTuple):
    """Every phase of the two destinations' life, read back before anything is torn down."""

    bench: Bench
    filed: subprocess.CompletedProcess[str]
    filed_linear_item: str
    filed_board_item: str
    linear_after_filing: dict[str, object]
    board_after_filing: dict[str, object]
    linear_files_after_filing: list[str]
    board_issues_after_filing: int
    redispatched: subprocess.CompletedProcess[str]
    redispatch_copies: list[dict[str, object]]
    linear_files_after_redispatch: list[str]
    board_issues_after_redispatch: int
    bindings: dict[str, tickets.Ticket]
    seeded_linear_item: str
    seeded_board_item: str
    later: subprocess.CompletedProcess[str]
    later_searches: dict[str, list[str]]
    later_store_calls: list[list[str]]
    later_board_requests: tuple[list[str], list[str]]
    linear_files_after_later: list[str]
    board_issues_after_later: int
    seeded_linear_before: dict[str, object]
    seeded_linear_after: dict[str, object]
    seeded_linear_comments: list[dict[str, object]]
    held: subprocess.CompletedProcess[str]
    seeded_linear_held: dict[str, object]
    board_check: subprocess.CompletedProcess[str]
    asked: str
    answered: subprocess.CompletedProcess[str]
    replies: list[dict[str, object]]
    watermarks: dict[str, bool]
    answered_again: subprocess.CompletedProcess[str]
    delivered_at_the_gate: dict[str, object]
    delivered_launch: subprocess.CompletedProcess[str]
    delivered_after: dict[str, object]


@pytest.fixture(scope="module")
def routed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path, Path]]:
    """One copy of this checkout, its Linear stand-in, and its recording plan store."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("linear-follow-ups")
    checkout = tmp / "checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    answering_this_checkouts_origin(checkout)
    linear = tmp / LINEAR
    linear.mkdir()
    # llmlint: ignore[e2e_not_mocked] This node's amended criteria require a `local-md` source to stand in for `hellopatient-followups`, because no check may reach the production Linear workspace; `tests/test_plan_source_roots.py` holds the tracked source to the Linear plugin's schema.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    stand_in(checkout, LINEAR, linear, copied=[tickets.Status.PROPOSED.value])
    provisioned(checkout)
    _recording(checkout)
    yield tmp, checkout, linear


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501 - a directive is one line, and the three rules it names run past the limit
# tests/plan_tooling/AGENTS.md states this project's split: `reads_docs` routes a journey that
# builds a copy of this checkout to `plan-tooling:test-docs`, keyed on the whole workspace because
# copying the tracked tree reads all of it, as `test_linear_routed_plans_e2e.py` does. The fixture
# is module-scoped and every test below reads that one fixture rather than launching.
# llmlint: ignore-block[e2e_not_mocked] Three boundaries are doubled and no more: the paid
# provider process; GitHub's Projects API, the loopback board, because the live board is shared
# by every run of this repository; and Linear, the folder the copy declares
# `hellopatient-followups` as, because no check may reach the production workspace. The recipes,
# engine, store, routing, follow-up tooling and every record read back are real.
@pytest.fixture(scope="module")
def life(routed: tuple[Path, Path, Path], oneharness_bin: str) -> Iterator[Life]:  # noqa: PLR0915 - one life, phase by phase
    tmp, checkout, linear = routed
    with (
        _serving_board(options=FOLLOWUPS_OPTIONS, number=FOLLOWUPS_PROJECT_NUMBER) as remote,
        pytest.MonkeyPatch.context() as patched,
    ):
        environment = _environment(tmp, remote)
        # This process reads each run's tickets back through the store, from the drafts root
        # the launches name.
        for name in follow_up_variables.all_names()[:2]:
            patched.setenv(name, environment[name])
        bench = Bench(
            checkout=checkout,
            linear=linear,
            tmp=tmp,
            drafts_root=Path(environment[follow_up_variables.root_name()]),
            authoring=Path(environment[plan_root_variable.name()]),
            runs=Path(environment["ONEPIPELINE_RUNS_DIR"]),
            record=Path(environment["STORE_RECORD"]),
            environment=environment,
        )
        bench.authoring.mkdir()
        module = _module(bench)
        # The board set up the way the operator sets up the live one, which creates the
        # `Priority` field its source maps.
        fields = _run(bench, "just", "plans", "sources", "fields", BOARD, "--apply")
        assert fields.returncode == 0, fields.stdout + fields.stderr

        # Filing: one draft per destination, verified into one ticket each and copied.
        linear_draft = _draft(bench, FILER, "The intake form drops a field", PETSINC)
        board_draft = _draft(bench, FILER, "The listing cursor skips the last page", LIBRARY)
        linear_path = tickets.ticket_path(bench.drafts_root, FILER, FILED_LINEAR)
        board_path = tickets.ticket_path(bench.drafts_root, FILER, FILED_BOARD)
        linear_ticket = _ticket(FILER, FILED_LINEAR, PETSINC, (linear_draft,))
        board_ticket = _ticket(FILER, FILED_BOARD, LIBRARY, (board_draft,))
        filing = [
            _searched(bench, PETSINC, FILED_LINEAR, tmp / "filer-search-linear.json"),
            _searched(bench, LIBRARY, FILED_BOARD, tmp / "filer-search-board.json"),
            _placed_and_copied(
                bench,
                _staged(bench, "linear.md", tickets.render(linear_ticket)),
                linear_path,
                linear_draft,
            ),
            _placed_and_copied(
                bench,
                _staged(bench, "board.md", tickets.render(board_ticket)),
                board_path,
                board_draft,
            ),
        ]
        account = [
            _filed(linear_draft, FILED_LINEAR, "Copied onto its Linear issue."),
            _filed(board_draft, FILED_BOARD, "Copied onto its board issue."),
        ]
        _scripted(bench, FILER, [*filing, _accounting(bench, FILER, account)])
        filed = _run(bench, "just", "follow-ups", FILER)
        assert filed.returncode == 0, filed.stdout + filed.stderr + _ran(bench)
        filed_linear_item = f"{LINEAR}:{tickets.read_ticket(linear_path).board_item}"
        filed_board_item = f"{BOARD}:{tickets.read_ticket(board_path).board_item}"
        linear_after_filing = _item(bench, filed_linear_item)
        board_after_filing = _item(bench, filed_board_item)
        linear_files_after_filing = _linear_items(bench)
        board_issues_after_filing = _board_issues()

        # Re-dispatch: the same run, its two tickets decided and copied again.
        feedback = _staged(bench, "feedback.md", "Copy both tickets again as they stand.\n")
        copies = tmp / "redispatch-copies.jsonl"
        recopied = [
            _in_checkout(
                bench,
                f"word=$({module} board-status --board {BOARD} {shlex.quote(str(path))})\n"
                f'sed -i "s/^status: .*/status: \\"$word\\"/" {shlex.quote(str(path))}\n'
                f"{module} validate {shlex.quote(str(path))}\n"
                f"{module} copy --board {BOARD} {shlex.quote(str(path))} "
                f">> {shlex.quote(str(copies))}\n",
            )
            for path in (linear_path, board_path)
        ]
        _scripted(bench, FILER, [*recopied, _accounting(bench, FILER, account)])
        redispatched = _run(bench, "just", "follow-ups", FILER, "--feedback", str(feedback))
        assert redispatched.returncode == 0, redispatched.stdout + redispatched.stderr + _ran(bench)
        redispatch_copies = [
            json.loads(line) for line in copies.read_text(encoding="utf-8").splitlines()
        ]
        linear_files_after_redispatch = _linear_items(bench)
        board_issues_after_redispatch = _board_issues()
        bindings = {
            FILED_LINEAR: tickets.read_ticket(linear_path),
            FILED_BOARD: tickets.read_ticket(board_path),
        }

        # The seeded search: an earlier run already filed both of the later run's root causes,
        # and one more run left evidence on the Linear one.
        seeded_linear_item = _filed_earlier(bench, SEEDED_LINEAR, PETSINC)
        seeded_board_item = _filed_earlier(bench, SEEDED_BOARD, LIBRARY)
        _commented(
            bench,
            seeded_linear_item,
            tickets.render_comment(WITNESSED, SEEDED_LINEAR, "Seen again in the nightly sync."),
        )
        seeded_linear_before = _item(bench, seeded_linear_item)
        later_drafts = {
            SEEDED_LINEAR: _draft(bench, LATER, "The intake sync retries forever", PETSINC),
            SEEDED_BOARD: _draft(bench, LATER, "The export drops a column", LIBRARY),
        }
        later_commands: list[list[str]] = []
        for cause, repository, item in (
            (SEEDED_LINEAR, PETSINC, seeded_linear_item),
            (SEEDED_BOARD, LIBRARY, seeded_board_item),
        ):
            later_commands.append(_searched(bench, repository, cause, tmp / f"later-{cause}.json"))
            local = tickets.ticket_path(bench.drafts_root, LATER, cause)
            staged = _staged(
                bench,
                f"later-{cause}.md",
                tickets.render(_ticket(LATER, cause, repository, (later_drafts[cause],))),
            )
            evidence = _staged(
                bench,
                f"evidence-{cause}.md",
                tickets.render_comment(LATER, cause, f"This run reproduced it ({cause})."),
            )
            later_commands.append(
                _in_checkout(
                    bench,
                    f"mkdir -p {shlex.quote(str(local.parent))}\n"
                    f"cp {shlex.quote(str(staged))} {shlex.quote(str(local))}\n"
                    f"{module} board-status --board {BOARD} {shlex.quote(str(local))} "
                    "> /dev/null\n"
                    f"{module} validate {shlex.quote(str(local))}\n"
                    f"{shlex.quote(bench.store)} task comment add {item} --body-file "
                    f"{shlex.quote(str(evidence))}\n"
                    f"{module} re-estimate --board {BOARD} {item}\n",
                )
            )
        later_account = [
            _filed(
                later_drafts[cause],
                cause,
                f"Evidence added as a comment on {item}, the earlier run's open ticket.",
            )
            for cause, item in (
                (SEEDED_LINEAR, seeded_linear_item),
                (SEEDED_BOARD, seeded_board_item),
            )
        ]
        _scripted(bench, LATER, [*later_commands, _accounting(bench, LATER, later_account)])
        bench.record.write_text("", encoding="utf-8")
        _GitHubFixture.requests.clear()
        later = _run(bench, "just", "follow-ups", LATER)
        assert later.returncode == 0, later.stdout + later.stderr + _ran(bench)
        later_store_calls = _store_calls(bench)
        later_board_requests = _board_cost(list(_GitHubFixture.requests))
        later_searches = {
            cause: [
                str(one["id"])
                for one in json.loads((tmp / f"later-{cause}.json").read_text("utf-8"))["items"]
            ]
            for cause in (SEEDED_LINEAR, SEEDED_BOARD)
        }
        linear_files_after_later = _linear_items(bench)
        board_issues_after_later = _board_issues()
        seeded_linear_after = _item(bench, seeded_linear_item)
        seeded_linear_comments = _comments(bench, seeded_linear_item)

        # A person sets the Linear ticket's priority, and one more run's evidence arrives.
        _store(bench, "task", "priority", "set", seeded_linear_item, HELD_PRIORITY.value)
        _commented(
            bench,
            seeded_linear_item,
            tickets.render_comment(LATEST, SEEDED_LINEAR, "And once more on the next sync."),
        )
        held = _run(
            bench,
            bench.python,
            "-m",
            "orchestrator.follow_up_tickets",
            "re-estimate",
            "--board",
            BOARD,
            seeded_linear_item,
        )
        seeded_linear_held = _item(bench, seeded_linear_item)

        # The board check across both destinations, as the recipe runs it after settlement.
        board_check = _run(
            bench,
            bench.python,
            "-m",
            "orchestrator.follow_up_tickets",
            "check-dispositions",
            "--root",
            str(bench.drafts_root),
            "--board",
            BOARD,
            FILER,
        )

        # A person comments on the filer's Linear ticket, and the gathering answers it once.
        asked = _commented(bench, filed_linear_item, ASKED, "Ada")
        replying = _staged(bench, "reply.py", REPLY_PROGRAM)
        _scripted(
            bench,
            FILER,
            [
                _in_checkout(
                    bench,
                    shlex.join(
                        [
                            bench.python,
                            str(replying),
                            bench.environment["FAKE_CODEX_PROMPT_LOG"],
                            FILER,
                            bench.store,
                            ANSWERED,
                        ]
                    ),
                )
            ],
        )
        answered = _run(bench, "just", "follow-ups-answer-comments")
        assert answered.returncode == 0, answered.stdout + answered.stderr + _ran(bench)
        replies = [
            held_comment
            for held_comment in _comments(bench, filed_linear_item)
            if (owner := tickets.comment_owner(str(held_comment["body"]))) is not None
            and owner.answers == asked
        ]
        watermarks = {
            board: comments.watermark_path(bench.drafts_root, board).is_file()
            for board in (BOARD, LINEAR)
        }
        answered_again = _run(bench, "just", "follow-ups-answer-comments", "--dry-run")

        # Delivery: a person accepts the filer's Linear ticket, and a plan node delivers it.
        _store(bench, "task", "status", "set", filed_linear_item, tickets.Status.ACCEPTED.value)
        gate = bench.tmp / "turn-gate"
        launch = _delivering_launch(bench, oneharness_bin, filed_linear_item, gate)
        try:
            _at_the_turn_gate(launch, gate)
            delivered_at_the_gate = _queued_or_as_it_stands(bench, filed_linear_item)
        finally:
            (gate / TURN_GATE_RELEASED).touch()
            stdout, stderr = launch.communicate(timeout=e2e_timeout(900))
        delivered_launch = subprocess.CompletedProcess(
            launch.args, launch.returncode, stdout, stderr
        )
        delivered_after = _item(bench, filed_linear_item)

        yield Life(
            bench=bench,
            filed=filed,
            filed_linear_item=filed_linear_item,
            filed_board_item=filed_board_item,
            linear_after_filing=linear_after_filing,
            board_after_filing=board_after_filing,
            linear_files_after_filing=linear_files_after_filing,
            board_issues_after_filing=board_issues_after_filing,
            redispatched=redispatched,
            redispatch_copies=redispatch_copies,
            linear_files_after_redispatch=linear_files_after_redispatch,
            board_issues_after_redispatch=board_issues_after_redispatch,
            bindings=bindings,
            seeded_linear_item=seeded_linear_item,
            seeded_board_item=seeded_board_item,
            later=later,
            later_searches=later_searches,
            later_store_calls=later_store_calls,
            later_board_requests=later_board_requests,
            linear_files_after_later=linear_files_after_later,
            board_issues_after_later=board_issues_after_later,
            seeded_linear_before=seeded_linear_before,
            seeded_linear_after=seeded_linear_after,
            seeded_linear_comments=seeded_linear_comments,
            held=held,
            seeded_linear_held=seeded_linear_held,
            board_check=board_check,
            asked=asked,
            answered=answered,
            replies=replies,
            watermarks=watermarks,
            answered_again=answered_again,
            delivered_at_the_gate=delivered_at_the_gate,
            delivered_launch=delivered_launch,
            delivered_after=delivered_after,
        )


def _delivering_launch(
    bench: Bench, oneharness_bin: str, delivers: str, gate: Path
) -> subprocess.Popen[str]:
    """Write, design and approve a two-node plan whose second node delivers ``delivers``; launch it.

    The first node holds the second back, and its turn is held at the paid-model boundary, so
    the ticket is read at the moment the run has claimed its plan and no worker has taken a
    turn — the moment a claim made before the first dispatch has to be readable.
    """
    criteria = "## Acceptance criteria\n- The task settles.\n"
    write_plan_project(
        bench.authoring,
        {
            "schema_version": 3,
            "name": DELIVERING_PROJECT,
            "tasks": [
                {
                    "id": FIRST_NODE,
                    "persona": "engineer",
                    "title": "test: the node ahead of the delivering one",
                    "task": f"## What\nReply with done.\n\n## Why\nHold it back.\n\n{criteria}",
                },
                {
                    "id": DELIVERING_NODE,
                    "persona": "engineer",
                    "title": "test: the node that delivers the ticket",
                    "deps": [FIRST_NODE],
                    "delivers": [delivers],
                    "task": f"## What\nReply with done.\n\n## Why\nDeliver it.\n\n{criteria}",
                },
            ],
        },
        native_id=DELIVERING_PROJECT,
    )
    project = f"authoring:{DELIVERING_PROJECT}"
    designed(
        "authoring",
        DELIVERING_PROJECT,
        [
            {
                "task": "test: the node ahead of the delivering one",
                "delivers": "the hold",
                "depends_on": "none",
                "location": str(bench.authoring),
            },
            {
                "task": "test: the node that delivers the ticket",
                "delivers": "the ticket",
                "depends_on": "first",
                "location": str(bench.authoring),
            },
        ],
        bench.environment,
    )
    approved = _run(bench, "just", "approve-design", project, seconds=300)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    gate.mkdir()
    environment = dict(bench.environment)
    environment[TURN_GATE_ENV] = str(gate)
    environment["ONEPIPELINE_RUNS_DIR"] = str(bench.tmp / "launched")
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([PASSING_VERDICT])
    environment.pop("FAKE_CODEX_RUN_ON_MARKER", None)
    # A plan written by hand rather than rendered, which the suite's own opt-out admits.
    environment[plan_check.REQUIRE_RENDERED_ENV] = "false"
    return subprocess.Popen(  # noqa: S603 - this repository's own recipe, in the copy
        ["just", "orchestrate", project, "--dag-graph", "off"],  # noqa: S607 - `just` as an operator runs it
        cwd=bench.checkout,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _at_the_turn_gate(launch: subprocess.Popen[str], gate: Path) -> None:
    """Wait until the launch's first worker turn is held at the paid-model boundary."""
    deadline = time.monotonic() + e2e_timeout(300)
    while not (gate / TURN_GATE_REACHED).exists():
        if launch.poll() is not None:
            stdout, stderr = launch.communicate()
            raise AssertionError(f"the launch ended before any worker turn: {stdout}{stderr}")
        if time.monotonic() > deadline:
            raise AssertionError(f"no worker turn reached the gate at {gate}")
        time.sleep(0.2)


def _queued_or_as_it_stands(bench: Bench, issue: str) -> dict[str, object]:
    """The ticket once the run's claim reads `queued`, or as it stands at the deadline."""
    deadline = time.monotonic() + e2e_timeout(120)
    while True:
        item = _item(bench, issue)
        if _status(item)[0] == tickets.Status.QUEUED or time.monotonic() > deadline:
            return item
        time.sleep(0.2)


def test_one_run_files_a_petsinc_root_cause_in_linear_and_another_on_the_board(
    life: Life,
) -> None:
    """The petsinc ticket lands in the Linear stand-in at `Proposed`, the other at `Proposal`.

    `Proposed` is the state the tracked mapping writes `backlog` as; the stand-in reads the
    copy at that category, under the word the copy carried (`linear_stand_in.py` says why).
    """
    assert life.filed_linear_item.startswith(f"{LINEAR}:{FILER}/tickets/"), life.filed_linear_item
    # The category the tracked mapping writes as `Proposed`, under the word the copy carried.
    assert _status(life.linear_after_filing) == ("backlog", "backlog")
    assert tickets.linear_state(tickets.Status.PROPOSED) == "Proposed"
    assert life.linear_files_after_filing == [f"tasks/{FILER}/tickets/{FILED_LINEAR}.md"]
    assert _record(life.linear_after_filing)["repository"] == _hosted(PETSINC)
    assert _status(life.board_after_filing) == ("backlog", "proposal")
    assert life.board_issues_after_filing == 1
    assert _record(life.board_after_filing)["repository"] == _hosted(LIBRARY)


def test_the_closeouts_board_check_across_both_destinations_is_green(life: Life) -> None:
    """The attached recipe's own check after settlement, and the same check run again by hand.

    The recipe runs `check-dispositions --board followups` once the run settles and exits
    non-zero on a refusal, so its exit is that check's answer; the run by hand prints it.
    """
    assert life.filed.returncode == 0, life.filed.stdout + life.filed.stderr
    assert life.board_check.returncode == tickets.SOUND, life.board_check.stderr
    assert "accounts for every draft this dispatch was given" in life.board_check.stdout


def test_a_re_dispatch_binds_both_tickets_and_files_no_duplicate(life: Life) -> None:
    linear_native = life.filed_linear_item.partition(":")[2]
    board_native = life.filed_board_item.partition(":")[2]
    assert [copy["destination"] for copy in life.redispatch_copies] == [
        life.filed_linear_item,
        life.filed_board_item,
    ], life.redispatch_copies
    assert {copy["action"] for copy in life.redispatch_copies} <= {"updated", "unchanged"}
    assert life.linear_files_after_redispatch == life.linear_files_after_filing
    assert life.board_issues_after_redispatch == life.board_issues_after_filing == 1
    linear, board = life.bindings[FILED_LINEAR], life.bindings[FILED_BOARD]
    assert (linear.board_item, linear.link(LINEAR), linear.link(BOARD)) == (
        linear_native,
        linear_native,
        None,
    )
    assert (board.board_item, board.link(BOARD), board.link(LINEAR)) == (
        board_native,
        board_native,
        None,
    )


def test_a_later_run_finds_each_destinations_open_ticket_by_root_cause_and_lists_nothing(
    life: Life,
) -> None:
    """Each search answers the earlier run's open ticket, and nothing new is filed.

    The copy's plan store records every command it was handed: each listing of the Linear
    stand-in names a narrowing question, and the loopback board saw no request walking its
    items or searching it unnarrowed.
    """
    assert life.later_searches == {
        SEEDED_LINEAR: [life.seeded_linear_item],
        SEEDED_BOARD: [life.seeded_board_item],
    }
    assert life.seeded_linear_item.startswith(f"{LINEAR}:"), life.seeded_linear_item
    assert life.seeded_board_item.startswith(f"{BOARD}:"), life.seeded_board_item
    listings = [
        call for call in life.later_store_calls if call[:2] == ["task", "list"] and LINEAR in call
    ]
    assert listings, f"no listing of {LINEAR} was recorded: {life.later_store_calls}"
    assert all(set(call) & {"--metadata", "--origin", "--search"} for call in listings), listings
    assert life.later_board_requests == ([], [])
    assert len(life.linear_files_after_later) == len(life.linear_files_after_filing) + 1, (
        "the later run filed a Linear ticket beside the earlier run's"
    )
    assert life.board_issues_after_later == life.board_issues_after_filing + 1, (
        "the later run filed a board issue beside the earlier run's"
    )


def test_evidence_and_a_re_estimate_raise_the_linear_estimate_and_a_persons_priority_stands(
    life: Life,
) -> None:
    """Three occurrences raise a medium estimate to high; a person's later priority is kept."""
    marked = [tickets.comment_owner(str(one["body"])) for one in life.seeded_linear_comments]
    assert [(owner.run if owner else None) for owner in marked] == [WITNESSED, LATER]
    assert _record(life.seeded_linear_before)[tickets.ESTIMATE_FIELD] == "medium"
    assert life.seeded_linear_before[tickets.PRIORITY_FIELD] == "medium"
    assert _record(life.seeded_linear_after)[tickets.ESTIMATE_FIELD] == "high"
    assert life.seeded_linear_after[tickets.PRIORITY_FIELD] == "high"
    assert "3 occurrences; raised one level" in str(life.seeded_linear_after["content"])

    assert life.held.returncode == tickets.SOUND, life.held.stderr
    assert json.loads(life.held.stdout) == {
        "item": life.seeded_linear_item,
        tickets.ESTIMATE_FIELD: "high",
        "occurrences": 4,
        tickets.PRIORITY_FIELD: HELD_PRIORITY.value,
    }
    assert life.seeded_linear_held[tickets.PRIORITY_FIELD] == HELD_PRIORITY.value
    assert _record(life.seeded_linear_held)[tickets.ESTIMATE_FIELD] == "high"


def test_a_persons_comment_on_the_linear_ticket_is_answered_once_by_its_run(life: Life) -> None:
    """Gathered from the Linear stand-in, routed to the filer, answered once, each board marked."""
    output = life.answered.stdout + life.answered.stderr
    selected = [line for line in output.splitlines() if line.startswith("selected: ")]
    assert len(selected) == 1, output
    assert f"on {life.filed_linear_item} goes to run {FILER}" in selected[0], selected
    assert f"read {LINEAR!r} for comments since" in output, output
    assert f"read {BOARD!r} for comments since" in output, output
    assert len(life.replies) == 1, life.replies
    (reply,) = life.replies
    owner = tickets.comment_owner(str(reply["body"]))
    assert owner is not None and owner.run == FILER and owner.root_cause == FILED_LINEAR
    assert life.watermarks == {BOARD: True, LINEAR: True}
    again = life.answered_again.stdout
    assert life.answered_again.returncode == 0, again + life.answered_again.stderr
    assert not [line for line in again.splitlines() if line.startswith("selected: ")], again
    assert f"answered by reply {reply['id']}" in again, again


def test_a_node_delivering_the_linear_ticket_queues_it_and_settles_it_done(life: Life) -> None:
    launch = life.delivered_launch
    assert _status(life.delivered_at_the_gate) == ("queued", "queued"), (
        f"before any worker turn the ticket read {life.delivered_at_the_gate['status']}\n"
        f"{launch.stdout}{launch.stderr}"
    )
    assert launch.returncode == 0, launch.stdout + launch.stderr
    assert json.loads(launch.stdout.strip().splitlines()[-1])["settlement"] == "complete"
    assert _status(life.delivered_after) == ("done", "done")


# llmlint: ignore-end[e2e_not_mocked]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501 - a directive is one line, and the three rules it names run past the limit
