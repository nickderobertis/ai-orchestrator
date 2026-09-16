"""`just follow-ups` over a finished run's drafts, driven end to end.

Everything below the recipe is real: `just follow-ups` and `scripts/follow-ups.sh`, the
design-approval gate every launch passes, the installed `onepipeline` driver launching one
direct node under the shipped `graphs/follow-up.yaml` on the shipped
`oneharness.follow-up.toml`, the installed `onetaskgraph` every draft, ticket, board item
and comment is written and read through, the real drafting command the drafts are written
with, and `orchestrator/follow_up_tickets.py`'s own `validate` and `check-run`.

**`tests/e2e/fake_codex.py` stands in for the paid model alone.** The follow-up agent is a
single-sided member whose turn runs in process, so the provider binary is the one seam it
still reaches, and there the stand-in runs the commands a real agent would choose — copy a
ticket it wrote into place, validate it, delete the drafts it consumed, copy it onto the
board, comment on another run's issue — through the real programs. Every record this module
asserts on is one those programs left.

It is reached through `tests/e2e/fake_codex_untrusted_directory.py`, which refuses a turn the
way codex does when the directory it runs in is inside no repository and its argv carries
neither codex's bypass argument nor its repository-check skip. The member's working directory
is its agent graph's scratch directory, which is exactly that case, so a follow-up turn that
stopped running in `bypass` falls through as `untrusted-directory` here as it did on the
first real dispatch, and every phase below fails with it.

**The board is a second local store and never the live `followups` board.** It is added
through the store's own `ONETASKGRAPH_SOURCES__` environment layer and named to the recipe
with `--to`, the way `tests/plan_tooling/test_copy_plan_recipe_e2e.py` stands a local store
in for `plans`: a journey that wrote to the live board would leave issues behind on the one
board every session's tickets accumulate on. The drafts root and the plan-authoring root are
stated to the launch for the same reason, so nothing lands in this checkout's own stores.

One module fixture drives every phase in order, because they are readings of one run's
life: its drafts verified once, then re-dispatched with the manager's feedback over the same
drafts, tickets and board. The refusals ride beside it on runs of their own.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import plan_root_variable
import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import helper
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import follow_up_tickets as tickets
from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.project_store import frontmatter, write_plan_project
from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs, so it is scheduled
#: with every other journey that does — which also keeps this module's one fixture on one
#: worker.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The provider's stand-in, refusing an untrusted directory the way codex does, and the guard
#: covering the identities `ONEHARNESS_BIN_*` cannot reach, so a routing mistake refuses a
#: turn rather than spending one.
FAKE_CODEX = helper("fake_codex_untrusted_directory.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The plan-store release this checkout pins, which is the one a dispatched follow-up agent
#: has to reach whatever else the caller's search path offers.
ADOPTED_PLAN_STORE = (REPO_ROOT / "config" / "onetaskgraph.version").read_text("utf-8").strip()

#: A plan-store CLI of another release, and the command that runs the task's own store
#: instruction with that release ahead of the pinned one; each file's header says what it
#: does and why the launcher's own search path cannot answer this.
OLDER_PLAN_STORE = helper("older-plan-store")
OLDER_PLAN_STORE_VERSION = "0.0.1"
RUN_TASK_STORE_INSTRUCTION = helper("run_task_store_instruction.py")

#: Files the first pass's turn has real programs write into the directory it runs commands
#: from: `pwd`'s answer to where that is, git's answer to whether a repository holds it,
#: which plan store the launched process tree's own search path resolves, and what happened
#: when the store instruction the task hands the agent was run with an older release first.
TURN_DIRECTORY_WITNESS = "turn-directory.witness"
GIT_WITNESS = "git-toplevel.witness"
PLAN_STORE_WITNESS = "plan-store.witness"
STORE_INSTRUCTION_WITNESS = "store-instruction.witness"

#: The launching session this journey states, and everything an enclosing dispatch would
#: otherwise decide for these launches.
LAUNCHING_SESSION = "e2e-follow-ups-recipe"
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
    *follow_up_variables.all_names(),
    plan_root_variable.name(),
)

#: The local store standing in for the board, spelled lowercase because it is spelled into
#: the store's environment layer as well as onto `--to`.
BOARD = "standin"

#: What the recipe names a follow-up run and its project after the run it follows up, the
#: one node that project holds, and the graph and persona that node names.
SUFFIX = "-follow-ups"
NODE = "follow-ups"
GRAPH = "graphs/follow-up.yaml"
PERSONA = "../personas/follow-up.yaml"

#: The tracked template the recipe composes the agent's task from, relative to this checkout.
TEMPLATE = "config/follow-up-task.md"

#: The repository every draft here is about, and the commit its claims were verified at.
REPOSITORY = "github.com/nickderobertis/some-service"
COMMIT = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
VERIFIED_AT = "2026-01-01T00:00:00Z"

#: The machine this journey runs on, which is what `hostname` prints for the agent's turn,
#: and what a ticket the agent stages says in its place until that turn reads `hostname`.
HOST = socket.gethostname()
READ_FROM_HOSTNAME = "host-read-from-hostname"

#: The two root causes the main run's drafts carry: one no board item names yet, and one an
#: earlier run already filed an open issue for.
NEW_CAUSE = "listing-cursor-skips-last-page"
SHARED_CAUSE = "sweep-trailer-omits-a-family"

#: The two schema-2 tickets a run filed before tickets named their repository — the shape of
#: every ticket on the live board: the one a feedback re-dispatch brings to the current
#: shape, and the one it leaves as it was.
REWRITTEN_CAUSE = "export-drops-a-column"
LEFT_CAUSE = "retry-loop-never-backs-off"
LEGACY_FEEDBACK = "Bring the export ticket to the current shape.\n"

#: The manager's feedback, carrying what a naive splice would corrupt: a placeholder the
#: template fills, a replacement backreference, and a `&`.
FEEDBACK = "Tighten the cursor ticket's examples & keep @RUN@ and @TICKET_CONTRACT@ as `\\1`.\n"

#: How long a detached run's one turn is held in flight, so the run is still being driven
#: when the next command asks. Far longer than the launch may take to return.
HOLD_SECONDS = 300

#: What `just follow-ups` exits with: a launch that settled with sound tickets, a ticket
#: that failed the shape, and a refusal before anything was launched.
OK = 0
UNSOUND = 1
REFUSED = 2


class Bench(NamedTuple):
    """The throwaway host every launch here runs on: its environment and its stores."""

    environment: dict[str, str]
    tmp: Path
    drafts_root: Path
    plans: Path
    board: Path
    runs: Path
    graph_state: Path


def _bench(tmp: Path) -> Bench:
    drafts_root, plans, board, runs, graph_state = (
        tmp / name for name in ("follow-ups", "plans", "board", "runs", "graph-state")
    )
    # Created rather than left to the first write: a `local-md` source canonicalizes its
    # root when it is built, so an absent one is refused as a broken source.
    board.mkdir(parents=True)
    environment = dict(os.environ)
    for name in INHERITED:
        environment.pop(name, None)
    root_name, plugin_name, _ = follow_up_variables.all_names()
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    environment[root_name] = str(drafts_root)
    environment[plugin_name] = WRITABLE_PLUGIN
    environment[plan_root_variable.name()] = str(plans)
    environment[f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__PLUGIN"] = WRITABLE_PLUGIN
    environment[f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__CONFIG__ROOT"] = str(board)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # A plan store of another release, ahead of everything the caller offers — the
    # condition the ticket names. It is stated here and read again inside the turn,
    # because the caller's own search path is not where this is decided: `just follow-ups`
    # reaches the driver through `scripts/onepipeline.sh`'s `exec uv run`, which puts this
    # checkout's `.venv/bin` ahead of it for the whole launched process tree. What that
    # leaves — and what a dispatched agent's own shell has instead — is
    # `test_the_launchers_own_search_path_puts_this_checkouts_venv_first` and
    # `tests/e2e/run_task_store_instruction.py`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = (
        f"{OLDER_PLAN_STORE}{os.pathsep}{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    )
    environment["REAL_PLAN_STORE"] = str(ONETASKGRAPH_BIN)
    environment["OLDER_PLAN_STORE_VERSION"] = OLDER_PLAN_STORE_VERSION
    environment["OLDER_PLAN_STORE_DIR"] = str(OLDER_PLAN_STORE)
    environment["XDG_STATE_HOME"] = str(tmp / "state")
    # `oneagentgraph` keeps each member's scratch — its report, the effective harness
    # config it ran under, and everything the turn writes in its working directory —
    # under a state directory of its own, which it takes from this name rather than from
    # `XDG_STATE_HOME`. Without it a completed launch here left
    # `~/.local/state/oneagentgraph/runs/follow-up-*/members/worker/` on the host, which
    # nothing reclaims and no journey can read as its own.
    # `tests/e2e/test_orchestrate_launch_e2e.py` states it the same way.
    environment["ONEAGENTGRAPH_STATE_DIR"] = str(graph_state)
    return Bench(environment, tmp, drafts_root, plans, board, runs, graph_state)


def _run(
    command: list[str], bench: Bench, *, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own recipes and the installed CLIs
        command,
        cwd=REPO_ROOT,
        env=environment or bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )


def _store(bench: Bench, *arguments: str) -> dict[str, object]:
    shown = _run([str(ONETASKGRAPH_BIN), *arguments, "--json"], bench)
    assert shown.returncode == 0, shown.stdout + shown.stderr
    payload: object = json.loads(shown.stdout)
    assert isinstance(payload, dict), payload
    return payload


def _item(bench: Bench, qualified: str) -> dict[str, object]:
    items = _store(bench, "task", "show", qualified)["items"]
    assert isinstance(items, list) and len(items) == 1, items
    record = items[0]
    assert isinstance(record, dict)
    item = record["item"]
    assert isinstance(item, dict)
    return item


def _comments(bench: Bench, qualified: str) -> list[dict[str, object]]:
    comments = _store(bench, "task", "comment", "list", qualified)["comments"]
    assert isinstance(comments, list)
    return comments


def _board_ids(bench: Bench) -> list[str]:
    items = _store(bench, "task", "list", "--source", BOARD)["items"]
    assert isinstance(items, list), items
    return sorted(str(one["id"]) for one in items if isinstance(one, dict))


def _draft(bench: Bench, run: str, title: str) -> Path:
    """One follow-up drafted against ``run`` through the real drafting command, as a manager."""
    body = (
        "## What happened\nThe cursor skipped a page.\n\n"
        "## Where\n`src/cursor.py`.\n\n"
        "## Why it is out of scope\nThe run was about something else.\n\n"
        "## Evidence\nPage 9 of 9 never rendered.\n"
    )
    drafted = subprocess.run(  # noqa: S603 - the drafting command every party drafts with
        [str(REPO_ROOT / "scripts" / "follow-up-draft.sh"), "--as", "manager", "--run", run]
        + ["--title", title, "--repository", REPOSITORY, "--path", "src/cursor.py"],
        cwd=bench.tmp,
        env=bench.environment,
        input=body,
        text=True,
        capture_output=True,
        check=False,
    )
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr
    matched = re.fullmatch(r"drafted (?P<id>\S+) at (?P<path>\S+)\n", drafted.stdout)
    assert matched is not None, drafted.stdout
    return Path(matched["path"])


def _draft_id(run: str, draft: Path) -> str:
    return f"drafts:{run}/drafts/{draft.stem}"


def _ticket(
    run: str,
    cause: str,
    drafts: tuple[str, ...],
    title: str,
    body: str,
    host: str = READ_FROM_HOSTNAME,
) -> tickets.Ticket:
    """A `backlog` ticket, naming ``host`` in its record and in its `## Evidence` section."""
    return tickets.Ticket(
        title=title,
        status=tickets.Status.PROPOSED,
        root_cause=tickets.RootCause(cause),
        repository=tickets.Origin(REPOSITORY),
        created_by_run=tickets.RunId(run),
        owning_runs=(tickets.RunId(run),),
        drafts=tuple(tickets.QualifiedDraftId(draft) for draft in drafts),
        basis=(tickets.Basis(tickets.Origin(REPOSITORY), tickets.Commit(COMMIT)),),
        verified_at=tickets.Timestamp(VERIFIED_AT),
        host=tickets.Host(host),
        body="\n\n".join(
            f"## {heading}\n\n"
            + (
                tickets.impact_section(
                    f"{body}: some-service's readers lose the last page of every listing.",
                    tickets.Severity.HIGH,
                    "readers request the last page by its number",
                    tickets.Severity.MEDIUM,
                )
                if heading == tickets.IMPACT
                else f"{body} ({heading})."
            )
            + (f" Verified on `{host}`." if heading == tickets.EVIDENCE else "")
            for heading in tickets.HEADINGS
        ),
    )


#: A body's `## Impact` section, up to the heading after it.
IMPACT_SECTION = re.compile(rf"## {tickets.IMPACT}\n\n.*?\n\n(?=## )", re.DOTALL)


def _schema_3(ticket: tickets.Ticket) -> str:
    """``ticket`` as schema 3 stored it, the shape the live tickets carry: no `## Impact`."""
    return frontmatter(
        {
            "title": ticket.title,
            "status": ticket.status.written,
            "repositories": [ticket.repository],
            "metadata": {tickets.KEY: tickets.record(ticket) | {"schema": 3}},
        },
        IMPACT_SECTION.sub("", ticket.body, count=1),
    )


def _placed(staged: Path, ticket: Path) -> list[str]:
    """The agent's command putting a staged ticket in place, its host read from `hostname`."""
    return [
        "sh",
        "-c",
        f'sed "s/{READ_FROM_HOSTNAME}/$(hostname)/g" {shlex.quote(str(staged))} '
        f"> {shlex.quote(str(ticket))}",
    ]


def _decided_and_copied(python: str, store: str, ticket: Path, qualified: str) -> list[str]:
    """The agent's step before copying: ask the board the status, write it, validate, copy."""
    return [
        "bash",
        "-c",
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(REPO_ROOT))}\n"
        f"word=$({python} -m orchestrator.follow_up_tickets board-status --board {BOARD} "
        f"{shlex.quote(str(ticket))})\n"
        f'sed -i "s/^status: .*/status: \\"$word\\"/" {shlex.quote(str(ticket))}\n'
        f"{python} -m orchestrator.follow_up_tickets validate {shlex.quote(str(ticket))}\n"
        f"{store} task copy {qualified} --to {BOARD}\n",
    ]


def _category(item: dict[str, object]) -> object:
    status = item["status"]
    assert isinstance(status, dict), status
    return status["category"]


# llmlint: ignore[tests_mirror_real_usage] The board here is a `local-md` store, whose items are
# files, and onetaskgraph 0.2.31 has no verb that changes an item's status (`task` lists, shows,
# walks, copies and comments), so editing the item's file is how a person moves it; the store
# reads the edit back through `task show` below, and the live board is off limits to a test.
def _moved(bench: Bench, qualified: str, word: str) -> None:
    """Move a board item to ``word``, the way a person edits its status on the board."""
    location = _item(bench, qualified)["location"]
    assert isinstance(location, dict), location
    path = Path(str(location["path"]))
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(r"^status: .*$", f"status: {json.dumps(word)}", text, count=1, flags=re.MULTILINE),
        encoding="utf-8",
    )


def _staged(bench: Bench, name: str, text: str) -> Path:
    """What the agent writes in its own scratch before putting it anywhere."""
    staged = bench.tmp / "agent" / name
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(text, encoding="utf-8")
    return staged


def _from_checkout(*command: str) -> list[str]:
    """A command the task tells the agent to run from the launching checkout, run there.

    The follow-up member works in its graph's scratch directory, which configures no plan
    source, so a store command run there knows neither the board nor the authoring source
    the launch exported a root for — which is why the task names the checkout at all.
    """
    return ["bash", "-c", f'cd {shlex.quote(str(REPO_ROOT))} && exec "$@"', "_", *command]


def _script(bench: Bench, marker: str, commands: list[list[str]]) -> None:
    """Tell the provider's stand-in which commands a turn whose task names ``marker`` runs."""
    keyed = Path(bench.environment["FAKE_CODEX_RUN_ON_MARKER"])
    keyed.write_text(json.dumps({marker: commands}), encoding="utf-8")


def _ran(bench: Bench) -> str:
    """What every scripted command reported, for a failure message to quote."""
    log = Path(bench.environment["FAKE_CODEX_RUN_ON_MARKER_LOG"])
    return log.read_text(encoding="utf-8") if log.is_file() else "(no scripted command ran)"


def _prompts(log: Path) -> list[str]:
    if not log.is_file():
        return []
    return [json.loads(line)["prompt"] for line in log.read_text(encoding="utf-8").splitlines()]


def _launched_node(bench: Bench, follow_up_run: str) -> dict[str, object]:
    """The one node the engine recorded dispatching for a run."""
    tasks = _launched_plan(bench, follow_up_run)["tasks"]
    assert isinstance(tasks, list) and len(tasks) == 1, tasks
    node = tasks[0]
    assert isinstance(node, dict), node
    return node


def _launched_plan(bench: Bench, follow_up_run: str) -> dict[str, object]:
    """The plan the engine recorded for a run, which is the task it dispatched."""
    plan: object = json.loads((bench.runs / follow_up_run / "plan.json").read_text("utf-8"))
    assert isinstance(plan, dict)
    return plan


class Pass(NamedTuple):
    """One `just follow-ups` over the main run, and what its turn was given."""

    result: subprocess.CompletedProcess[str]
    run: str
    prompts: list[str]


class Followed(NamedTuple):
    """Every phase of this module's journey, read back before anything is torn down."""

    bench: Bench
    main: str
    other: str
    consumed: list[Path]
    other_before: dict[str, object]
    empty: subprocess.CompletedProcess[str]
    empty_run: str
    first: Pass
    first_turn: Turn
    new_after_first: dict[str, object]
    other_after_first: dict[str, object]
    comments_after_first: list[dict[str, object]]
    board_after_first: list[str]
    new_after_move: dict[str, object]
    second: Pass
    new_after_second: dict[str, object]
    local_after_second: dict[str, object]
    other_after_second: dict[str, object]
    comments_after_second: list[dict[str, object]]
    board_after_second: list[str]
    edited_body: str
    edited_comment: str
    unsound: subprocess.CompletedProcess[str]
    unsound_ticket: Path
    legacy: Pass
    legacy_board_before: list[str]
    legacy_board_after: list[str]
    legacy_before: dict[str, object]
    legacy_local: dict[str, object]
    legacy_after: dict[str, object]
    legacy_body: str
    rewritten_ticket: Path
    left_ticket: Path
    detached: subprocess.CompletedProcess[str]
    detached_seconds: float
    detached_run: str
    mine: subprocess.CompletedProcess[str]
    driving: subprocess.CompletedProcess[str]
    exempt_gate: subprocess.CompletedProcess[str]
    tampered_gate: subprocess.CompletedProcess[str]


class Turn(NamedTuple):
    """The follow-up member's one turn, as `oneharness` reported it.

    `directory` is where that report was written, which is the member's scratch directory;
    `ran` is the identity that took the turn (empty when none did), `fell_through` each
    identity passed over with its reason, and `command` and `status` are the ones the running
    identity was given and ended with.
    """

    directory: Path
    ran: str
    fell_through: tuple[tuple[str, str], ...]
    command: tuple[str, ...]
    status: str


def _turn(bench: Bench, follow_up_run: str) -> Turn | None:
    """The member's turn, found through `just transcript` — where an operator reads it.

    The transcript names the member's turn report, and that report is `oneharness`'s own account
    of which identity ran and why the ones before it did not.
    """
    transcript = _run(["just", "transcript", follow_up_run], bench)
    named = re.findall(r"^\s+report worker (\S+/report\.json)$", transcript.stdout, re.MULTILINE)
    if len(named) != 1:
        return None
    path = Path(named[0])
    # llmlint: ignore[boundary_inputs_validated] `oneharness`'s own turn report, at the path the
    # transcript names; every field read here is narrowed into `Turn` and asserted on there.
    report = json.loads(path.read_text(encoding="utf-8"))
    fallback = report["fallback"]
    ran = next((one for one in report["results"] if one["harness_id"] == fallback["ran"]), None)
    return Turn(
        directory=path.parent,
        ran=str(fallback["ran"] or ""),
        fell_through=tuple(
            (str(one["harness"]), str(one["reason"])) for one in fallback["fell_through"]
        ),
        command=tuple(str(word) for word in ran["command"]) if ran else (),
        status=str(ran["status"]) if ran else "",
    )


def _prompt_log(bench: Bench, name: str) -> Path:
    """Where one pass records the task its turn was given, named before the pass runs.

    A turn's own commands read it: the store instruction the agent was handed is in the
    task and nowhere else, so a command that runs that instruction has to find it there.
    """
    return bench.tmp / f"prompts-{name}.jsonl"


def _pass(bench: Bench, name: str, main: str, *extra: str) -> Pass:
    log = _prompt_log(bench, name)
    environment = bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(log)}
    result = _run(
        ["just", "follow-ups", main, "--to", BOARD, *extra], bench, environment=environment
    )
    runs = sorted(path.name for path in bench.runs.glob(f"{main}{SUFFIX}*"))
    return Pass(result, runs[-1] if runs else "", _prompts(log))


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `tests/plan_tooling` is
# already the Nx project edge this repository keeps for journeys that launch the installed
# engine, keyed on `planToolingWorkspace`, which covers every file these launches read. The
# fixture is module-scoped and spends six launches whose turns are the provider's stand-in.
@pytest.fixture(scope="module")
def followed(tmp_path_factory: pytest.TempPathFactory) -> Followed:  # noqa: PLR0915 - one journey
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("follow-ups-recipe")
    bench = _bench(tmp)
    bench.environment["FAKE_CODEX_RUN_ON_MARKER"] = str(tmp / "commands.json")
    bench.environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(tmp / "commands-ran.jsonl")
    pid = os.getpid()
    main, other = f"fu-main-{pid}", f"fu-earlier-{pid}"
    python = str(REPO_ROOT / ".venv" / "bin" / "python3")
    store = str(ONETASKGRAPH_BIN)
    started: list[str] = []
    try:
        # An earlier run's verified ticket, already an open issue on the board.
        earlier = _ticket(
            other,
            SHARED_CAUSE,
            (f"drafts:{other}/drafts/an-earlier-draft",),
            "some-service: the sweep trailer omits a family it never examined",
            "Filed by the earlier run",
            HOST,
        )
        earlier_path = tickets.ticket_path(bench.drafts_root, other, SHARED_CAUSE)
        earlier_path.parent.mkdir(parents=True)
        earlier_path.write_text(tickets.render(earlier), encoding="utf-8")
        copied = _run(
            [store, "task", "copy", tickets.qualified_id(other, SHARED_CAUSE), "--to", BOARD], bench
        )
        assert copied.returncode == 0, copied.stdout + copied.stderr
        other_issue = f"{BOARD}:{other}/tickets/{SHARED_CAUSE}"
        # A person deferred the earlier run's ticket: it is still open, and takes evidence.
        # llmlint: ignore-block[tests_mirror_real_usage] A person's move of a `local-md` item
        # has no store verb in onetaskgraph 0.2.31, for the reason `_moved`'s directive gives.
        _moved(bench, other_issue, tickets.Status.DEFERRED.written)
        # llmlint: ignore-end[tests_mirror_real_usage]
        other_before = _item(bench, other_issue)

        # A run with nothing to verify.
        empty_run = f"fu-empty-{pid}"
        empty = _run(["just", "follow-ups", empty_run, "--to", BOARD], bench)

        # The main run's two drafts, one per root cause.
        new_draft = _draft(bench, main, "The listing cursor skips the last page")
        shared_draft = _draft(bench, main, "The sweep trailer omits a family")
        new_ticket = tickets.ticket_path(bench.drafts_root, main, NEW_CAUSE)
        shared_ticket = tickets.ticket_path(bench.drafts_root, main, SHARED_CAUSE)
        title = "some-service: the listing cursor skips the last page"
        first_ticket = _ticket(main, NEW_CAUSE, (_draft_id(main, new_draft),), title, "Verified")
        shared = _ticket(
            main,
            SHARED_CAUSE,
            (_draft_id(main, shared_draft),),
            "some-service: the sweep trailer omits a family",
            "Verified again",
        )
        comment = tickets.render_comment(
            main, SHARED_CAUSE, "This run hit it at `scripts/sweep.sh:12`."
        )
        validate = [python, "-m", "orchestrator.follow_up_tickets", "validate"]
        _script(
            bench,
            main,
            [
                # First, while this run's drafts are still there to list: run the store
                # instruction the task itself hands the agent, with a plan store of
                # another release ahead of the pinned one on the search path that
                # resolves it. The helper's header says why that search path is the
                # agent's own rather than the launcher's.
                [
                    python,
                    str(RUN_TASK_STORE_INSTRUCTION),
                    "--prompt-log",
                    str(_prompt_log(bench, "first")),
                    "--run",
                    main,
                    "--checkout",
                    str(REPO_ROOT),
                    "--witness",
                    STORE_INSTRUCTION_WITNESS,
                ],
                ["mkdir", "-p", str(new_ticket.parent)],
                _placed(_staged(bench, "new.md", tickets.render(first_ticket)), new_ticket),
                _placed(_staged(bench, "shared.md", tickets.render(shared)), shared_ticket),
                [*validate, str(new_ticket), str(shared_ticket)],
                ["rm", str(new_draft), str(shared_draft)],
                _decided_and_copied(
                    python, store, new_ticket, tickets.qualified_id(main, NEW_CAUSE)
                ),
                _from_checkout(
                    store,
                    "task",
                    "comment",
                    "add",
                    other_issue,
                    "--body-file",
                    str(_staged(bench, "comment.md", comment)),
                ),
                # Where the turn runs its commands, written down there by the programs
                # themselves: `pwd` says which directory, git whether a repository holds it.
                ["sh", "-c", f"pwd -P > {TURN_DIRECTORY_WITNESS}"],
                [
                    "sh",
                    "-c",
                    f"git rev-parse --show-toplevel > {GIT_WITNESS} 2>&1; "
                    f'echo "exit $?" >> {GIT_WITNESS}',
                ],
                # Which plan store this turn's own search path resolves, and what that
                # program reports itself to be.
                [
                    "sh",
                    "-c",
                    f"command -v onetaskgraph > {PLAN_STORE_WITNESS} 2>&1; "
                    f"onetaskgraph --version >> {PLAN_STORE_WITNESS} 2>&1",
                ],
            ],
        )
        first = _pass(bench, "first", main)
        started.append(first.run)
        first_turn = _turn(bench, first.run)
        # The turn rides on the failure, because a turn that fell through says why in its report
        # and nowhere this recipe prints.
        assert first.result.returncode == OK, (
            f"{first.result.stdout}{first.result.stderr}{first_turn}"
        )
        assert first_turn is not None, "the transcript names no turn report for the member"
        new_issue = f"{BOARD}:{main}/tickets/{NEW_CAUSE}"
        assert new_issue in _board_ids(bench), _ran(bench)
        new_after_first = _item(bench, new_issue)
        other_after_first = _item(bench, other_issue)
        comments_after_first = _comments(bench, other_issue)
        board_after_first = _board_ids(bench)

        # The user defers the new ticket: its board item is moved to `draft` by hand.
        # llmlint: ignore-block[tests_mirror_real_usage] A person's move of a `local-md` item
        # has no store verb in onetaskgraph 0.2.31, for the reason `_moved`'s directive gives.
        _moved(bench, new_issue, tickets.Status.DEFERRED.written)
        # llmlint: ignore-end[tests_mirror_real_usage]
        new_after_move = _item(bench, new_issue)

        # The manager's feedback, re-dispatched over the same run: this run's issue is edited
        # by copying its ticket again, and its comment on the earlier run's issue is edited.
        edited = _ticket(
            main, NEW_CAUSE, first_ticket.drafts, title, "Verified, examples tightened"
        )
        edited_comment = tickets.render_comment(
            main, SHARED_CAUSE, "This run hit it at `scripts/sweep.sh:12` and `:40`."
        )
        feedback = _staged(bench, "feedback.md", FEEDBACK)
        # The agent finds its own comment by the marker the module recognises, and edits it.
        pick = _staged(
            bench,
            "pick-comment.py",
            "import json, sys\n"
            "from orchestrator import follow_up_tickets as t\n"
            "comments = json.load(sys.stdin)['comments']\n"
            f"mine = [c['id'] for c in comments if t.may_change_comment({main!r}, c['body'])]\n"
            "print(mine[0])\n",
        )
        edited_file = _staged(bench, "comment-edited.md", edited_comment)
        edit = (
            f"set -euo pipefail\ncd {shlex.quote(str(REPO_ROOT))}\n"
            f"id=$({store} task comment list {other_issue} --json | {python} {pick})\n"
            f'{store} task comment edit {other_issue} "$id" --body-file {edited_file}\n'
        )
        _script(
            bench,
            main,
            [
                # Staged as the proposal it was first written as; the board decides.
                _placed(_staged(bench, "new-edited.md", tickets.render(edited)), new_ticket),
                _decided_and_copied(
                    python, store, new_ticket, tickets.qualified_id(main, NEW_CAUSE)
                ),
                ["bash", "-c", edit],
            ],
        )
        second = _pass(bench, "second", main, "--feedback", str(feedback))
        started.append(second.run)
        assert second.result.returncode == OK, second.result.stdout + second.result.stderr
        new_after_second = _item(bench, new_issue)
        local_after_second = _item(bench, tickets.qualified_id(main, NEW_CAUSE))
        other_after_second = _item(bench, other_issue)
        comments_after_second = _comments(bench, other_issue)
        board_after_second = _board_ids(bench)

        # A run whose agent leaves a ticket that fails the shape, unvalidated.
        unsound_run = f"fu-unsound-{pid}"
        unsound_draft = _draft(bench, unsound_run, "A draft left unsound")
        unsound_ticket = tickets.ticket_path(bench.drafts_root, unsound_run, "left-unsound")
        broken = _ticket(
            unsound_run,
            "left-unsound",
            (_draft_id(unsound_run, unsound_draft),),
            "some-service: left unsound",
            "Unsound",
        )
        rendered = tickets.render(broken).replace(
            'status: "backlog"', 'status: "backlog"\nproject: "a-project"'
        )
        _script(
            bench,
            unsound_run,
            [
                ["mkdir", "-p", str(unsound_ticket.parent)],
                ["cp", str(_staged(bench, "unsound.md", rendered)), str(unsound_ticket)],
            ],
        )
        unsound = _run(["just", "follow-ups", unsound_run, "--to", BOARD], bench)
        started.append(f"{unsound_run}{SUFFIX}")

        # A run whose tickets were filed at schema 3, before a ticket stated its impact — the
        # shape of the tickets already on the live board — one of them accepted there by a
        # person. The manager's feedback re-dispatch rewrites that one and leaves the other.
        legacy_run = f"fu-legacy-{pid}"
        legacy_drafts = (f"drafts:{legacy_run}/drafts/a-consumed-draft",)
        rewritten_ticket = tickets.ticket_path(bench.drafts_root, legacy_run, REWRITTEN_CAUSE)
        left_ticket = tickets.ticket_path(bench.drafts_root, legacy_run, LEFT_CAUSE)
        for cause, path in ((REWRITTEN_CAUSE, rewritten_ticket), (LEFT_CAUSE, left_ticket)):
            filed = _ticket(
                legacy_run,
                cause,
                legacy_drafts,
                f"some-service: {cause.replace('-', ' ')}",
                "Filed before impact",
                HOST,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_schema_3(filed), encoding="utf-8")
            copied = _run(
                [store, "task", "copy", tickets.qualified_id(legacy_run, cause), "--to", BOARD],
                bench,
            )
            assert copied.returncode == 0, copied.stdout + copied.stderr
        legacy_issue = f"{BOARD}:{legacy_run}/tickets/{REWRITTEN_CAUSE}"
        _moved(bench, legacy_issue, tickets.Status.ACCEPTED.written)
        legacy_before = _item(bench, legacy_issue)
        rewritten = _ticket(
            legacy_run,
            REWRITTEN_CAUSE,
            legacy_drafts,
            f"some-service: {REWRITTEN_CAUSE.replace('-', ' ')}",
            "Brought to the current shape",
        )
        _script(
            bench,
            legacy_run,
            [
                # Rewritten as a new proposal would be, its host read from `hostname`; the
                # board decides the status it is copied with.
                _placed(_staged(bench, "legacy.md", tickets.render(rewritten)), rewritten_ticket),
                _decided_and_copied(
                    python,
                    store,
                    rewritten_ticket,
                    tickets.qualified_id(legacy_run, REWRITTEN_CAUSE),
                ),
            ],
        )
        legacy_feedback = _staged(bench, "legacy-feedback.md", LEGACY_FEEDBACK)
        legacy_board_before = _board_ids(bench)
        legacy = _pass(bench, "legacy", legacy_run, "--feedback", str(legacy_feedback))
        started.append(legacy.run)
        legacy_board_after = _board_ids(bench)
        legacy_local = _item(bench, tickets.qualified_id(legacy_run, REWRITTEN_CAUSE))
        legacy_after = _item(bench, legacy_issue)

        # A detached launch whose one turn is held, so its run is still being driven.
        detach_run = f"fu-detach-{pid}"
        _draft(bench, detach_run, "A draft verified by a detached run")
        _script(bench, "nothing-matches-this-marker", [])
        held = bench.environment | {"FAKE_CODEX_HOLD_SECONDS": str(HOLD_SECONDS)}
        clock = time.monotonic()
        detached = _run(
            ["just", "follow-ups", detach_run, "--detach", "--to", BOARD], bench, environment=held
        )
        detached_seconds = time.monotonic() - clock
        detached_run = f"{detach_run}{SUFFIX}"
        started.append(detached_run)
        mine = _run(["just", "runs", "--mine"], bench)
        driving = _run(["just", "follow-ups", detached_run, "--to", BOARD], bench)

        # The exemption, read by the gate itself: the recipe's own project, and a copy of it
        # that gained a second node under the same stamp.
        project = f"authoring:{main}{SUFFIX}"
        exempt_gate = _run(
            [str(REPO_ROOT / ".venv" / "bin" / "orchestrator-launch-gate"), project], bench
        )
        plan = _launched_plan(bench, second.run)
        tasks = plan["tasks"]
        assert isinstance(tasks, list)
        write_plan_project(
            bench.plans,
            {
                "schema_version": 3,
                "goal": {"text": "a follow-ups project somebody added work to"},
                "name": f"{main}-tampered",
                "tasks": [
                    {
                        key: value
                        for key, value in tasks[0].items()
                        if key in ("id", "persona", "agent_graph", "task")
                    },
                    {
                        "id": "work-somebody-added",
                        "persona": PERSONA,
                        "task": "## What\nMore work.\n",
                    },
                ],
            },
            native_id=f"{main}-tampered",
            project_metadata={"orchestrator.plan-kind": {"kind": "follow-ups", "nodes": [NODE]}},
        )
        tampered_gate = _run(
            [
                str(REPO_ROOT / ".venv" / "bin" / "orchestrator-launch-gate"),
                f"authoring:{main}-tampered",
            ],
            bench,
        )
        return Followed(
            bench=bench,
            main=main,
            other=other,
            consumed=[new_draft, shared_draft],
            other_before=other_before,
            empty=empty,
            empty_run=empty_run,
            first=first,
            first_turn=first_turn,
            new_after_first=new_after_first,
            other_after_first=other_after_first,
            comments_after_first=comments_after_first,
            board_after_first=board_after_first,
            new_after_move=new_after_move,
            second=second,
            new_after_second=new_after_second,
            local_after_second=local_after_second,
            other_after_second=other_after_second,
            comments_after_second=comments_after_second,
            board_after_second=board_after_second,
            edited_body=edited.body,
            edited_comment=edited_comment,
            unsound=unsound,
            unsound_ticket=unsound_ticket,
            legacy=legacy,
            legacy_board_before=legacy_board_before,
            legacy_board_after=legacy_board_after,
            legacy_before=legacy_before,
            legacy_local=legacy_local,
            legacy_after=legacy_after,
            legacy_body=rewritten.body.replace(READ_FROM_HOSTNAME, HOST),
            rewritten_ticket=rewritten_ticket,
            left_ticket=left_ticket,
            detached=detached,
            detached_seconds=detached_seconds,
            detached_run=detached_run,
            mine=mine,
            driving=driving,
            exempt_gate=exempt_gate,
            tampered_gate=tampered_gate,
        )
    finally:
        for run in started:
            if run and (bench.runs / run).exists():
                _run(["just", "stop", run], bench)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def test_a_run_with_no_drafts_or_tickets_ends_at_one_line_and_launches_nothing(
    followed: Followed,
) -> None:
    empty = followed.empty

    assert empty.returncode == OK, empty.stdout + empty.stderr
    assert empty.stdout.splitlines() == [
        f"follow-ups: run {followed.empty_run} holds no follow-up drafts and no tickets under "
        f"{followed.bench.drafts_root}, so there is nothing to verify and no follow-up run was "
        "launched"
    ]
    assert not (followed.bench.runs / f"{followed.empty_run}{SUFFIX}").exists()
    assert not (followed.bench.plans / "projects" / f"{followed.empty_run}{SUFFIX}.md").exists()


def test_the_recipe_launches_one_direct_node_under_the_graph_that_settles_on_the_members_exit(
    followed: Followed,
) -> None:
    first = followed.first

    assert first.result.returncode == OK, first.result.stdout + first.result.stderr
    assert first.run == f"{followed.main}{SUFFIX}"
    plan = _launched_plan(followed.bench, first.run)
    assert plan["name"] == first.run
    node = _launched_node(followed.bench, first.run)
    assert node["id"] == NODE
    assert node["persona"] == PERSONA
    assert str(node["agent_graph"]).endswith(GRAPH)
    assert "repo" not in node and "execution_checkout" not in node, (
        "a direct node names no repository"
    )
    results = _run(["just", "results", first.run], followed.bench)
    assert re.search(rf"^\s+{NODE}\s+done$", results.stdout, re.MULTILINE), results.stdout
    assert (
        f"launch-gate: authoring:{followed.main}{SUFFIX} is the project a follow-ups launch writes"
        in first.result.stderr
    ), first.result.stderr


def test_the_members_turn_runs_on_codex_and_runs_commands_from_a_directory_no_repository_holds(
    followed: Followed,
) -> None:
    """The turn a `default`-mode member lost on its first real dispatch, taken here.

    The stand-in refuses a codex turn run outside every repository unless its argv carries
    codex's bypass argument, so a codex identity running at all is the config's `bypass`
    reaching the turn the engine really dispatched. What the turn did from its directory is
    read back from the programs that did it.
    """
    turn = followed.first_turn

    assert turn.ran.startswith("codex:"), turn
    assert all(reason != "untrusted-directory" for _, reason in turn.fell_through), (
        f"a codex identity fell through as an untrusted directory: {turn}"
    )
    assert turn.status == "ok", turn
    assert "--dangerously-bypass-approvals-and-sandbox" in turn.command, turn.command

    directory = turn.directory.resolve()
    witness = directory / TURN_DIRECTORY_WITNESS
    assert witness.is_file(), f"no command the turn ran wrote into {directory}"
    assert witness.read_text(encoding="utf-8").strip() == str(directory), (
        "the turn's `pwd` did not name the member's scratch directory as where it ran"
    )
    git = (directory / GIT_WITNESS).read_text(encoding="utf-8")
    assert "not a git repository" in git, git
    assert git.rstrip().endswith("exit 128"), git


def test_the_members_scratch_stays_under_this_journeys_own_bench(followed: Followed) -> None:
    """Nothing this launch wrote for its member is left in the host's persistent state.

    `oneagentgraph` keeps a member's scratch — the report `just transcript` names, the
    effective harness config the turn ran under, and every file the turn wrote in its
    working directory — under its own state directory, which it reads from
    `ONEAGENTGRAPH_STATE_DIR` and not from `XDG_STATE_HOME`. This journey redirected the
    second and not the first, so every completed launch left
    `~/.local/state/oneagentgraph/runs/follow-up-*/members/worker/` behind on the host:
    storage nobody reclaims, and a directory a concurrent dispatch is writing into at the
    same time.

    Read off the launch that already happened rather than off a live process, so it still
    answers once every run here has been stopped: the member report is the one artifact
    whose resolved location says where all of that went, and the turn's own `pwd` witness
    beside it says the turn ran there too.
    """
    bench, turn = followed.bench, followed.first_turn
    bench_state = bench.graph_state.resolve()

    assert turn.directory.resolve().is_relative_to(bench_state), (
        f"the member report was written to {turn.directory}, outside this journey's own "
        f"state directory {bench_state}; a launch here is leaving agent-graph scratch in "
        "the host's persistent state"
    )
    witness = turn.directory.resolve() / TURN_DIRECTORY_WITNESS
    assert witness.is_file() and Path(witness.read_text(encoding="utf-8").strip()).is_relative_to(
        bench_state
    ), f"the turn ran its commands outside {bench_state}"


def test_the_composed_task_names_the_plan_store_in_full_and_never_bare(
    followed: Followed,
) -> None:
    """Every store instruction the agent is given resolves to one program, not to a name.

    The agent works in its graph's scratch directory, where a bare `onetaskgraph` is
    answered by whatever that dispatch's search path offers first — and this journey's
    launches offer another release there deliberately.
    """
    (task,) = followed.first.prompts

    assert str(ONETASKGRAPH_BIN) in task, (
        f"the composed task never names {ONETASKGRAPH_BIN}, so what a store command there "
        "resolves to is the dispatch's search path's to decide"
    )
    bare = [line for line in task.splitlines() if "`onetaskgraph " in line]
    assert not bare, f"the composed task still names a bare plan-store invocation: {bare}"


def _compose_command(bench: Bench, run: str, plan_store: str) -> list[str]:
    """The compose command `scripts/follow-ups.sh` runs, with ``plan_store`` in its place.

    Every other word is the recipe's own — this checkout's interpreter, the tracked
    template, the run's real drafts root, the board this journey stands in, and the two
    commands the task is written with — so what a refusal below answers is the one value
    that differs.
    """
    python = str(REPO_ROOT / ".venv" / "bin" / "python3")
    written = f'"{python}" -m orchestrator.follow_up_tickets'
    return [
        python,
        "-m",
        "orchestrator.follow_up_tickets",
        "compose",
        "--template",
        str(REPO_ROOT / TEMPLATE),
        "--root",
        str(bench.drafts_root),
        "--run",
        run,
        "--board",
        BOARD,
        "--validate",
        f"{written} validate",
        "--board-status",
        f"{written} board-status",
        "--checkout",
        str(REPO_ROOT),
        "--plan-store",
        plan_store,
    ]


def test_a_plan_store_the_dispatch_could_not_run_composes_no_task_at_all(
    followed: Followed,
) -> None:
    """The composer refuses a program the dispatch could not run, before a task exists.

    Driven where `scripts/follow-ups.sh` drives it: the real `compose` command, spawned
    through this checkout's own interpreter over the tracked template and the run's real
    drafts root, with only `--plan-store` differing between the three invocations. That is
    the boundary the refusal defends — the recipe writes this command's standard output
    straight into the task the launch dispatches, so a value it let through would reach the
    agent as a store instruction that fails after the launch, with the task already written
    and two real runs' worth of evidence for what that costs.

    Each refusal is read as *no task composed* rather than as a message alone: an empty
    standard output is what keeps the recipe from launching, and the sound invocation
    beside them is what says the rest of the argv is not what any of them answered. The
    third is absolute and really executable — a symlink to the pinned CLI — and is refused
    only for the space in its directory, which the shell a store instruction runs in would
    read as two words.
    """
    bench, run = followed.bench, followed.main

    composed = _run(_compose_command(bench, run, str(ONETASKGRAPH_BIN)), bench)
    assert composed.returncode == OK, composed.stdout + composed.stderr
    assert str(ONETASKGRAPH_BIN) in composed.stdout

    unquotable = bench.tmp / "plan store"
    unquotable.mkdir(exist_ok=True)
    (unquotable / "onetaskgraph").symlink_to(ONETASKGRAPH_BIN)
    for named, refusal in (
        ("onetaskgraph", "is not an absolute path"),
        (str(bench.tmp / "no-such-plan-store"), "is not an executable file"),
        (str(unquotable / "onetaskgraph"), "does not read as part of one word"),
    ):
        refused = _run(_compose_command(bench, run, named), bench)
        assert refused.returncode == REFUSED, refused.stdout + refused.stderr
        assert refusal in refused.stderr, refused.stderr
        assert refused.stdout == "", (
            f"a plan store the dispatch could not run still composed a task: {refused.stdout}"
        )


def _unprovisioned(root: Path) -> Path:
    """A checkout of this repository nobody has bootstrapped, as a real host would have it.

    What `scripts/follow-ups.sh` reads before it resolves the plan store, copied as it
    stands: its own `scripts/`, the `config/` it names its template from, the
    `orchestrator/` package the draft inventory and the board constant are read out of —
    which the interpreter it falls back to can import from here because that package is
    stdlib-only — and the `onetaskgraph.yaml` every plan source is resolved against, which
    a checkout without one answers for as a store configuring no sources at all. What is
    deliberately absent is `.venv/`, so this checkout has neither the pinned plan-store CLI
    nor an interpreter of its own: the state of every checkout before `just bootstrap` has
    run in it.
    """
    for directory in ("scripts", "config", "orchestrator"):
        shutil.copytree(REPO_ROOT / directory, root / directory, dirs_exist_ok=True)
    shutil.copy2(REPO_ROOT / "onetaskgraph.yaml", root / "onetaskgraph.yaml")
    return root


def test_an_unprovisioned_checkout_refuses_rather_than_naming_the_store_on_the_path(
    followed: Followed, tmp_path: Path
) -> None:
    """The recipe names its own checkout's plan store or none, and never the path's.

    This is the shape both real runs behind issue 1057 had: a host carrying a plan store
    of another release on its search path, and a checkout whose own pinned copy is not
    there. Spelling the program in full closes that only while the program spelled is the
    pinned one — resolving it from the search path instead would write another release's
    path into the task and hand the agent the same wrong program with the task's own
    authority behind it. So an unbootstrapped checkout is refused here, and what it is
    refused with is where to repair it.

    Everything about the condition is real: a checkout of this repository's own `scripts/`,
    `config/` and `orchestrator/` with no `.venv/` — the state of every checkout before
    `just bootstrap` runs in it — driven with this bench's ordinary environment, whose
    search path does offer a plan store. The run it is asked about really holds a draft,
    written through the real drafting command, so the recipe passes its inventory and
    reaches the resolution rather than ending at the line for a run with nothing to verify.
    """
    bench = followed.bench
    run = f"fu-unprovisioned-{os.getpid()}"
    _draft(bench, run, "The sweep trailer omits a family")
    checkout = _unprovisioned(tmp_path / "checkout")
    assert shutil.which("onetaskgraph", path=bench.environment["PATH"]) is not None, (
        "this bench's search path offers no plan store, so a refusal below would say "
        "nothing about preferring the checkout's own"
    )

    refused = subprocess.run(  # noqa: S603 - this repository's own recipe, in a copy of it
        [str(checkout / "scripts" / "follow-ups.sh"), run],
        cwd=checkout,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )

    assert refused.returncode == REFUSED, refused.stdout + refused.stderr
    assert f"has no plan-store CLI at {checkout}/.venv/bin/onetaskgraph" in refused.stderr, (
        refused.stderr
    )
    assert "just bootstrap" in refused.stderr, refused.stderr
    assert not (bench.plans / "projects" / f"{run}{SUFFIX}.md").exists(), (
        "the recipe wrote a project for a run whose plan store it could not resolve"
    )
    assert not (bench.runs / f"{run}{SUFFIX}").exists(), (
        "the recipe launched a run whose plan store it could not resolve"
    )


def test_the_launchers_own_search_path_puts_this_checkouts_venv_first(
    followed: Followed,
) -> None:
    """Why putting an older plan store ahead of the caller's path proves nothing on its own.

    This bench places one there, and the launched process tree still resolves a bare
    `onetaskgraph` to the pinned binary — because `just follow-ups` reaches the driver
    through `scripts/onepipeline.sh`'s `exec uv run`, which prepends this checkout's
    `.venv/bin` for everything below it. So this is a reading of the launcher and never a
    guard of the pin: an assertion resting on it goes green against a task that spells the
    store as a bare name, which is what the first attempt at that guard did.

    What the two real runs hit is the search path a dispatched agent's *own* shell
    re-derives, and the guard for that is
    `test_the_dispatched_agent_runs_the_pinned_plan_store_the_task_names`.
    """
    witness = followed.first_turn.directory.resolve() / PLAN_STORE_WITNESS
    assert witness.is_file(), (
        f"no command the turn ran wrote {PLAN_STORE_WITNESS} into {followed.first_turn.directory}"
    )
    resolved, reported = witness.read_text(encoding="utf-8").splitlines()[:2]

    assert Path(resolved) == ONETASKGRAPH_BIN, (
        f"the launched process tree resolves `onetaskgraph` to {resolved}, where `uv run` "
        f"was expected to put this checkout's {ONETASKGRAPH_BIN} first"
    )
    assert reported == f"onetaskgraph {ADOPTED_PLAN_STORE}", (
        f"the binary at {ONETASKGRAPH_BIN} reports {reported!r}, where this checkout pins "
        f"{ADOPTED_PLAN_STORE}"
    )


def test_the_dispatched_agent_runs_the_pinned_plan_store_the_task_names(
    followed: Followed,
) -> None:
    """A store command the task hands the agent reaches the pinned release, older one first.

    This is the guard for issue 1057, and what it drives is the resolution a dispatched
    agent actually performs: the instruction is read out of the task that turn was given
    and run as that task spells it, in the turn, with a plan store of another release ahead
    of the pinned one on the path that resolves it. A task naming the program in full
    survives that; the bare `onetaskgraph` this template carried before the pin does not,
    and two real runs were answered by another release exactly there.

    Three readings, because a resolution rule alone would be this journey reading its own
    search path back to itself: what the instruction resolved to, that the older program's
    own log says it never served it, and that the command really listed this run's drafts —
    so a witness written by an instruction that did nothing cannot pass.
    """
    witness = followed.first_turn.directory.resolve() / STORE_INSTRUCTION_WITNESS
    assert witness.is_file(), (
        f"no command the turn ran wrote {STORE_INSTRUCTION_WITNESS} into "
        f"{followed.first_turn.directory}; {_ran(followed.bench)}"
    )
    # llmlint: ignore[boundary_inputs_validated] The witness this journey's own helper wrote,
    # at the path this journey named; every field read here is asserted on below.
    probe = json.loads(witness.read_text(encoding="utf-8"))
    assert probe["problem"] is None, probe["problem"]

    assert probe["resolved"] == str(ONETASKGRAPH_BIN), (
        f"the store instruction the task hands the agent, {probe['command']}, resolves to "
        f"{probe['resolved']} when a plan store of another release is ahead of the pinned "
        f"one; this checkout pins {ONETASKGRAPH_BIN}"
    )
    served = witness.with_name(witness.name + ".served")
    assert not served.is_file(), (
        f"the older plan store served the task's own instruction: {served.read_text('utf-8')}"
    )
    assert probe["returncode"] == 0, probe
    listed = json.loads(probe["stdout"])["items"]
    assert sorted(str(one["id"]) for one in listed) == sorted(
        _draft_id(followed.main, draft) for draft in followed.consumed
    ), probe


def test_the_member_is_given_the_composed_task_whole(followed: Followed) -> None:
    first = followed.first
    node = _launched_node(followed.bench, first.run)

    assert len(first.prompts) == 1, first.prompts
    assert first.prompts[0].strip() == str(node["task"]).strip(), (
        "the follow-up agent's turn was not given the task the recipe composed, whole"
    )


def test_the_composed_task_carries_every_instruction_and_renders_both_contracts(
    followed: Followed,
) -> None:
    (task,) = followed.first.prompts
    main, root = followed.main, followed.bench.drafts_root
    # Prose wraps where the template's lines end, so a phrase is looked for in the task with
    # its whitespace collapsed; the contracts below are compared byte for byte.
    flat = " ".join(task.split())

    for instruction in (
        f"`{root}/tasks/{main}/drafts/`",
        f"{ONETASKGRAPH_BIN} task list --source drafts --project {main} --json",
        "`transcript` command",
        f"from the checkout that launched you, `{REPO_ROOT}`",
        "registered checkouts `onevcs repos`",
        "`git fetch origin`",
        "`git show origin/<base>:<path>`",
        "`git grep <pattern> origin/<base>`",
        "Never check out, commit to, or otherwise modify any checkout, and never clone",
        "**Record the basis first.**",
        "**Verify each draft against that basis.**",
        "**Drop a draft you cannot verify, or one the basis already fixes.**",
        "should have been raised during the run",
        "naming the draft, its author and why it was blocking",
        "rather than filing it quietly as a ticket",
        "**Group what stands by root cause**",
        "-m orchestrator.follow_up_tickets validate <path of the ticket>",
        "Then delete the draft files that ticket consumed",
        "`root_cause` and `repository`, then by titles and text",
        f"{ONETASKGRAPH_BIN} task list --source {BOARD} --search <text> --json",
        "every issue you created or updated with its URL",
        "every dropped draft with its reason",
        "every finding that should have been surfaced live",
        "**Decide each ticket's status from the board, before every copy.**",
        f"-m orchestrator.follow_up_tickets board-status --board {BOARD} <path of the ticket>`",
        "Run `hostname` on the machine you run on and write exactly what it prints, both as "
        "`host` and in the `## Evidence` section, never a value you type or recall",
    ):
        assert instruction in flat, instruction
    steps = task.split("## What to do, in order", 1)[1].split("## The verified ticket", 1)[0]
    decided = re.search(r"`(\S+ -m orchestrator\.follow_up_tickets board-status) --board", steps)
    assert decided is not None, steps
    assert decided.start() < steps.index("**Put each ticket on the board.**"), steps
    contract = (
        tickets.ticket_contract(main, BOARD)
        .replace("@DRAFTS_ROOT@", str(root))
        .replace("@BOARD_STATUS@", decided[1])
        .replace("@PLAN_STORE@", str(ONETASKGRAPH_BIN))
    )
    assert contract in task, "the ticket shape is not the one the module renders"
    ownership = tickets.comment_contract(main, BOARD).replace("@PLAN_STORE@", str(ONETASKGRAPH_BIN))
    assert ownership in task, "board ownership is not the module's"
    assert task.count(tickets.status_vocabulary()) == 1, "the status vocabulary is not there once"
    assert "## This is a re-dispatch" not in task
    assert "## Feedback on the previous follow-up run" not in task


def test_a_verified_ticket_lands_on_the_board_with_its_shape_its_one_repository_and_no_project(
    followed: Followed,
) -> None:
    landed = followed.new_after_first

    ticket = tickets.from_store_item(landed)
    assert _category(landed) == tickets.Status.PROPOSED, "a new ticket did not land as a proposal"
    assert ticket.host == HOST, "the ticket's host is not what `hostname` printed"
    assert f"`{HOST}`" in ticket.body.split(f"## {tickets.EVIDENCE}", 1)[1]
    assert ticket.created_by_run == followed.main
    assert ticket.root_cause == NEW_CAUSE
    assert ticket.basis == (tickets.Basis(tickets.Origin(REPOSITORY), tickets.Commit(COMMIT)),)
    assert landed["project"] is None
    assert landed["repositories"] == [REPOSITORY] == [ticket.repository]
    metadata = landed["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["onetaskgraph.origin"] == tickets.qualified_id(followed.main, NEW_CAUSE)
    assert set(metadata[tickets.KEY]) == set(tickets.RECORD_KEYS)
    assert not any(path.exists() for path in followed.consumed), "a consumed draft was left"
    assert f"{BOARD}:{followed.main}/tickets/{SHARED_CAUSE}" not in followed.board_after_first, (
        "a root cause another run already filed was copied onto the board a second time"
    )


def test_another_runs_open_issue_gets_one_marked_comment_and_is_otherwise_untouched(
    followed: Followed,
) -> None:
    """The earlier run's issue is held at `draft`, where a person deferred it, and stays there."""
    before, after = followed.other_before, followed.other_after_first

    assert _category(before) == tickets.Status.DEFERRED, "the other run's item was not deferred"
    for field in ("title", "content", "status", "metadata", "project", "repositories"):
        assert after[field] == before[field], field
    (comment,) = followed.comments_after_first
    body = str(comment["body"])
    assert body.splitlines()[0] == f"Additional evidence from follow-up run `{followed.main}`."
    assert tickets.comment_owner(body) == tickets.CommentOwner(followed.main, SHARED_CAUSE)


def test_a_feedback_re_dispatch_edits_this_runs_issue_and_comment_instead_of_adding_any(
    followed: Followed,
) -> None:
    second = followed.second

    assert second.result.returncode == OK, second.result.stdout + second.result.stderr
    assert second.run == f"{followed.main}{SUFFIX}-2"
    (task,) = second.prompts
    heading = "## Feedback on the previous follow-up run"
    assert heading in task
    assert FEEDBACK.rstrip() in task.split(heading, 1)[1], "the feedback was not spliced verbatim"
    assert "## This is a re-dispatch" in task
    assert f"an issue run `{followed.main}` created is **edited**" in task
    flat = " ".join(task.split())
    assert (
        f"an existing ticket of run `{followed.main}` is copied again carrying the board's" in flat
    )
    assert "a ticket of an older schema is brought to the current shape" in flat

    assert followed.board_after_second == followed.board_after_first, "a re-dispatch added an item"
    assert followed.new_after_second["content"] == followed.edited_body.replace(
        READ_FROM_HOSTNAME, HOST
    )
    assert followed.new_after_second["content"] != followed.new_after_first["content"]


def test_a_re_copy_keeps_the_status_a_person_moved_the_board_item_to(
    followed: Followed,
) -> None:
    """The user deferred the ticket between the passes, and the re-dispatch left it deferred.

    The re-dispatch's agent staged its edited ticket as the proposal it was first written as
    and asked `board-status` before copying, so what it copied carried the board's status.
    """
    deferred = tickets.Status.DEFERRED

    assert _category(followed.new_after_first) == tickets.Status.PROPOSED
    assert _category(followed.new_after_move) == deferred, "the board item was not moved"
    assert _category(followed.new_after_second) == deferred, "a re-copy undid the deferral"
    assert _category(followed.local_after_second) == deferred
    assert followed.new_after_second["content"] != followed.new_after_move["content"]
    (comment,) = followed.comments_after_second
    assert comment["id"] == followed.comments_after_first[0]["id"]
    assert str(comment["body"]).strip() == followed.edited_comment.strip()
    for field in ("title", "content", "status", "metadata"):
        assert followed.other_after_second[field] == followed.other_before[field], field


def test_an_attached_run_names_every_ticket_that_fails_the_shape(followed: Followed) -> None:
    unsound = followed.unsound

    assert unsound.returncode == UNSOUND, unsound.stdout + unsound.stderr
    assert f"{followed.unsound_ticket} is not a sound ticket" in unsound.stderr
    assert "carries a `project`" in unsound.stderr


def test_a_feedback_re_dispatch_brings_a_schema_3_ticket_to_the_current_shape_updating_its_item(
    followed: Followed,
) -> None:
    """The back-fill of the live tickets: rewritten stating their impact, left accepted.

    Both tickets were schema 3 and on the board, and a person had accepted one. The re-dispatch
    rewrote that one and asked `board-status` before copying it, which updated the item the
    board already held — the same origin, no item added — rather than filing a second; the
    other it never touched, and the attached closeout names it rather than passing it.
    """
    legacy, before, local, after = (
        followed.legacy,
        followed.legacy_before,
        followed.legacy_local,
        followed.legacy_after,
    )
    accepted = tickets.Status.ACCEPTED

    held_before = before["metadata"]
    assert isinstance(held_before, dict)
    assert held_before[tickets.KEY]["schema"] == 3
    assert f"## {tickets.IMPACT}" not in str(before["content"]), (
        "the schema-3 board item already stated its impact"
    )
    assert _category(before) == accepted, "the schema-3 board item was not moved"

    (task,) = legacy.prompts
    assert (
        "its `## Impact` section written from the evidence the ticket already carries, "
        "re-verifying only a claim that no longer holds"
    ) in " ".join(task.split())
    for shown in (local, after):
        ticket = tickets.from_store_item(shown)
        impact = ticket.body.split(f"## {tickets.IMPACT}\n", 1)[1].split("\n## ", 1)[0]
        assert "- Severity: high" in impact and "- Severity with the workaround: medium" in impact
        assert ticket.host == HOST, "the rewritten ticket's host is not what `hostname` printed"
        assert f"`{HOST}`" in ticket.body.split(f"## {tickets.EVIDENCE}", 1)[1]
        assert shown["repositories"] == [REPOSITORY] == [ticket.repository]
        metadata = shown["metadata"]
        assert isinstance(metadata, dict)
        assert metadata[tickets.KEY]["schema"] == tickets.SCHEMA
        assert _category(shown) == accepted, "bringing a ticket to the current shape undid it"
    metadata_after = after["metadata"]
    assert isinstance(metadata_after, dict)
    origin = tickets.qualified_id(*tickets.located_path(followed.rewritten_ticket))
    assert metadata_after["onetaskgraph.origin"] == held_before["onetaskgraph.origin"] == origin
    assert followed.legacy_board_after == followed.legacy_board_before, (
        "bringing a ticket to the current shape filed a second item instead of updating its own"
    )
    assert after["content"] == followed.legacy_body
    assert after["content"] != before["content"]

    assert legacy.result.returncode == UNSOUND, legacy.result.stdout + legacy.result.stderr
    assert f"{followed.left_ticket} is not a sound ticket" in legacy.result.stderr
    assert "the record is schema 3, and this reads schema 4" in legacy.result.stderr
    assert f"{followed.rewritten_ticket} is not a sound ticket" not in legacy.result.stderr


def test_a_detached_launch_returns_at_once_with_two_lines_and_a_run_its_session_owns(
    followed: Followed,
) -> None:
    detached, run = followed.detached, followed.detached_run

    assert detached.returncode == OK, detached.stdout + detached.stderr
    assert detached.stdout.splitlines() == [
        f"follow-up run: {run}",
        f"watch it with: just watch {run}",
    ]
    assert followed.detached_seconds < HOLD_SECONDS, "the detached launch waited for its turn"
    launch = json.loads((followed.bench.runs / run / "launch.json").read_text("utf-8"))
    assert launch["session"] == LAUNCHING_SESSION
    assert followed.mine.returncode == 0, followed.mine.stderr
    assert re.search(rf"^\*?\s*{re.escape(run)}\s+\[mine\]", followed.mine.stdout, re.MULTILINE), (
        followed.mine.stdout
    )


def test_a_run_something_is_still_driving_is_refused(followed: Followed) -> None:
    driving = followed.driving

    assert driving.returncode == REFUSED, driving.stdout + driving.stderr
    assert f"run '{followed.detached_run}' is still being driven" in driving.stderr
    assert not (followed.bench.plans / "projects" / f"{followed.detached_run}{SUFFIX}.md").exists()


def test_the_exemption_holds_only_while_the_stamp_names_exactly_the_one_node(
    followed: Followed,
) -> None:
    assert followed.exempt_gate.returncode == 0, followed.exempt_gate.stderr
    assert "is the project a follow-ups launch writes" in followed.exempt_gate.stderr

    tampered = followed.tampered_gate
    assert tampered.returncode == 1, tampered.stdout + tampered.stderr
    assert "1 task(s) that launch never wrote (work-somebody-added)" in tampered.stderr
    assert "nothing was dispatched" in tampered.stderr
