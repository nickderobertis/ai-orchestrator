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

import dataclasses
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
import short_state
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
#: which plan store the launched process tree's own search path resolves, what happened
#: when the store instruction the task hands the agent was run with an older release first,
#: and what happened when the task's *validate* instruction was run the same way.
TURN_DIRECTORY_WITNESS = "turn-directory.witness"
GIT_WITNESS = "git-toplevel.witness"
PLAN_STORE_WITNESS = "plan-store.witness"
STORE_INSTRUCTION_WITNESS = "store-instruction.witness"
VALIDATE_WITNESS = "validate-older-first.witness"
#: The older plan store's own log of what it served during that validate, under the bench.
VALIDATE_SERVED = "validate-older-first.served"
#: What the task's own accepted-items listing answered, run as the task spells it; what its
#: duplicate search by text answered for the shared root cause, run the same way; what the
#: validators printed for the three refused shapes; and what `board-status` printed on the
#: re-dispatch for the ticket whose accepted item a person had un-accepted.
ACCEPTED_WITNESS = "accepted-items.witness"
SEARCH_WITNESS = "duplicate-search.witness"
REFUSED_WITNESS = "refused-shapes.witness"
REDERIVE_WITNESS = "rederive.witness"

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

#: The interpreter the recipe spells into every `-m orchestrator.follow_up_tickets` command
#: it composes, as `scripts/follow-ups.sh` spells it: beside the script, unnormalized.
RECIPE_PYTHON = str(REPO_ROOT / "scripts" / ".." / ".venv" / "bin" / "python3")
#: The local store standing in for the board, spelled lowercase because it is spelled into
#: the store's environment layer as well as onto `--to`.
BOARD = "standin"
#: How many items one page of the stand-in holds, set through the store's own environment
#: layer for the setting: small enough that the board spans pages before the first pass
#: runs, so a listing that read one page would read it as smaller than it is — the shape
#: of the live board at 102 items answering 50.
PAGE_SIZE = 2

#: What the recipe names a follow-up run and its project after the run it follows up, the
#: one node that project holds, and the graph and persona that node names.
SUFFIX = "-follow-ups"
NODE = "follow-ups"
GRAPH = "graphs/follow-up.yaml"
PERSONA = "../personas/follow-up.yaml"

#: The tracked template the recipe composes the agent's task from, relative to this checkout.
TEMPLATE = "config/follow-up-task.md"

#: The repository every draft here is about, and the commit its claims were verified at;
#: and a second repository of the board's owner, which the accepted fix that narrows a ticket
#: here lives in, because the accepted reading is of the whole board and not of one repository.
REPOSITORY = "github.com/nickderobertis/some-service"
OTHER_REPOSITORY = "github.com/nickderobertis/other-service"
COMMIT = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
VERIFIED_AT = "2026-01-01T00:00:00Z"

#: The machine this journey runs on, which is what `hostname` prints for the agent's turn,
#: and what a ticket the agent stages says in its place until that turn reads `hostname`.
HOST = socket.gethostname()
READ_FROM_HOSTNAME = "host-read-from-hostname"

#: The root causes the main run's drafts carry: one no board item names yet, one an earlier
#: run already filed an open issue for, one an accepted ticket's fix removes outright, and
#: one a proposed ticket's fix would remove — which is never assumed.
NEW_CAUSE = "listing-cursor-skips-last-page"
SHARED_CAUSE = "sweep-trailer-omits-a-family"
EVAPORATED_CAUSE = "retry-storm-on-a-flaky-listing"
RELATED_CAUSE = "sweep-follows-a-symlink"

#: Three items of other root causes the board holds before the first pass, each with the
#: `url` the board reports for an item, and the fix each states: an accepted one, filed in
#: the other repository, that narrows `NEW_CAUSE`'s impact; an accepted one that removes
#: `EVAPORATED_CAUSE`; and a proposal that would remove `RELATED_CAUSE`.
NARROWING_CAUSE = "export-paginates-by-offset"
REMOVING_CAUSE = "retry-loop-never-backs-off"
PROPOSED_CAUSE = "sweep-ignores-a-symlink"
#: Two withdrawn items of the earlier run about the sweep trailer, which the store lists
#: before that run's open item for `SHARED_CAUSE`: what puts the open item the duplicate
#: search has to find on the search's second page, past every item the first page holds.
#: The text the agent searches the board for, and the fix each withdrawn item stated.
SEARCHED_TEXT = "sweep trailer"
WITHDRAWN_CAUSES = ("sweep-trailer-counts-a-family-twice", "sweep-trailer-misses-a-family")
WITHDRAWN_FIX = "Count each family the sweep trailer reports once"
ISSUES = "https://github.com/nickderobertis/some-service/issues/"
OTHER_ISSUES = "https://github.com/nickderobertis/other-service/issues/"
NARROWING_URL, REMOVING_URL, PROPOSED_URL = OTHER_ISSUES + "41", ISSUES + "42", ISSUES + "43"
#: What the ticket says where the accepted fix narrowed it, and where a proposal is related;
#: the severity lines the narrowed ticket carries, below the ones a ticket carries otherwise;
#: and what the related ticket says once that proposal is accepted and removes its root cause.
ASSUMED = f"Assuming the fix in {NARROWING_URL} lands, the export is unaffected."
NARROWED_CAUSE = f"Once {NARROWING_URL} lands, only the listing's own cursor is affected."
REFIXED = (
    f"- Paging the export by cursor here too: rejected because {NARROWING_URL} already does that."
)
#: The fix that remains right once the narrowing one is in, in the displaced one's place.
REPLACEMENT_FIX = (
    f"Have the listing hand its cursor to the export: chosen because {NARROWING_URL} already "
    "pages the export by cursor, so nothing here pages it again."
)
NARROWED_SEVERITY = (tickets.Severity.MEDIUM, tickets.Severity.LOW)
FULL_SEVERITY = (tickets.Severity.HIGH, tickets.Severity.MEDIUM)
RELATED = f"Related: {PROPOSED_URL} proposes the sweep fix, and is not assumed."
WITHDRAWN = f"Withdrawn: the accepted fix in {PROPOSED_URL} removes this root cause too."
#: The first pass's report, naming the draft dropped under the accepted item's URL.
DROPPED_REPORT = (
    f"Dropped drafts: the `{EVAPORATED_CAUSE}` draft, because the accepted fix in "
    f"{REMOVING_URL} removes its root cause too."
)

#: The three ticket shapes the first pass's turn writes and has refused: an edge of the
#: related kind, edges naming two sources, and a far end whose URL the body never names.
REFUSED_RELATED_KIND = "refused-related-kind"
REFUSED_TWO_SOURCES = "refused-two-sources"
REFUSED_URL_ABSENT = "refused-url-absent"

#: The two schema-4 tickets a run filed before a ticket stated one fix — the shape of every
#: ticket on the live board: the one a feedback re-dispatch brings to the current shape, and
#: the one it leaves as it was.
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
    environment["ONETASKGRAPH_PAGE_SIZE"] = str(PAGE_SIZE)
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
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp))
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
    """Every item the board holds, read through the module's own every-page listing.

    The board spans pages here by design, so a read of the store's first page would
    answer a board smaller than it is: this reads it the way the agent's task does.
    """
    listed = _run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python3"),
            "-m",
            "orchestrator.follow_up_tickets",
            "board-items",
            "--board",
            BOARD,
        ],
        bench,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    items = json.loads(listed.stdout)["items"]
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
    rejected: str | None = None,
    depends_on: tuple[str, ...] = (),
    impact_note: str = "",
    severity: tuple[tickets.Severity, tickets.Severity] = FULL_SEVERITY,
    root_cause_note: str = "",
    suggested_fix: str | None = None,
    repository: str = REPOSITORY,
) -> tickets.Ticket:
    """A `backlog` ticket, naming ``host`` in its record and in its `## Evidence` section.

    ``rejected``, when given, is its `## Rejected fixes` section, directly after the fix;
    ``depends_on`` names the accepted items it is written against, ``impact_note`` is what
    its `## Impact` prose says about them, or about a related proposal, ``severity`` is
    that section's severity with no workaround and with it, ``root_cause_note`` is what
    `## Root cause` says once an accepted fix narrowed where the cause bites, and
    ``suggested_fix`` the one fix `## Suggested fix` states in place of the plain one. The
    ticket is of ``repository``, its title and its basis alike.
    """
    return tickets.Ticket(
        title=title,
        status=tickets.Status.PROPOSED,
        root_cause=tickets.RootCause(cause),
        repository=tickets.Origin(repository),
        created_by_run=tickets.RunId(run),
        owning_runs=(tickets.RunId(run),),
        drafts=tuple(tickets.QualifiedDraftId(draft) for draft in drafts),
        basis=(tickets.Basis(tickets.Origin(repository), tickets.Commit(COMMIT)),),
        verified_at=tickets.Timestamp(VERIFIED_AT),
        host=tickets.Host(host),
        body="\n\n".join(
            f"## {heading}\n\n"
            + (
                tickets.impact_section(
                    f"{body}: some-service's readers lose the last page of every listing. "
                    f"{impact_note}".rstrip(),
                    severity[0],
                    "readers request the last page by its number",
                    severity[1],
                )
                if heading == tickets.IMPACT
                else (
                    suggested_fix
                    if suggested_fix is not None and heading == tickets.SUGGESTED_FIX
                    else f"{body} ({heading})."
                )
                + (f" {root_cause_note}" if root_cause_note and heading == "Root cause" else "")
            )
            + (f" Verified on `{host}`." if heading == tickets.EVIDENCE else "")
            + (
                f"\n\n## {tickets.REJECTED_FIXES}\n\n{rejected}"
                if rejected is not None and heading == tickets.SUGGESTED_FIX
                else ""
            )
            for heading in tickets.HEADINGS
        ),
        depends_on=tuple(tickets.QualifiedBoardId(held) for held in depends_on),
    )


def _board_item(
    bench: Bench,
    run: str,
    cause: str,
    status: str,
    url: str,
    fix: str,
    repository: str = REPOSITORY,
) -> str:
    """An item of ``run`` the stand-in board holds, put there the way a run's ticket is: its id.

    The ticket, of ``repository``, is written beside ``run``'s drafts and copied onto the
    board through the store's own `task copy`, then moved to ``status`` through its
    `task status set` — the two verbs a follow-up run and a person use. The one thing
    neither verb produces is the `url` a real board reports for an issue, so that key alone
    is written into the copied `local-md` record, which is what such an item is, and the
    store reads it back as the item's `url`, exactly as the GitHub board reports an issue's.
    """
    ticket = _ticket(
        run,
        cause,
        (f"drafts:{run}/drafts/a-draft",),
        f"{tickets.repository_name(repository)}: {cause}",
        fix,
        repository=repository,
    )
    path = tickets.ticket_path(bench.drafts_root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tickets.render(ticket).replace(READ_FROM_HOSTNAME, HOST), encoding="utf-8")
    copied = _run(
        [str(ONETASKGRAPH_BIN), "task", "copy", tickets.qualified_id(run, cause), "--to", BOARD],
        bench,
    )
    assert copied.returncode == 0, copied.stdout + copied.stderr
    qualified = f"{BOARD}:{run}/tickets/{cause}"
    _moved(bench, qualified, status)
    location = _item(bench, qualified)["location"]
    assert isinstance(location, dict), location
    record = Path(str(location["path"]))
    text = record.read_text(encoding="utf-8")
    # llmlint: ignore-block[tests_mirror_real_usage] No store verb sets a `local-md` item's
    # `url`: a real board reports one for every issue on its own, and a stand-in can only
    # carry one in its record, which is what `tests/test_follow_up_tickets.py` does for the
    # one status word the store refuses to set.
    record.write_text(
        text.replace("\n---\n", f"\nurl: {json.dumps(url)}\n---\n", 1), encoding="utf-8"
    )
    # llmlint: ignore-end[tests_mirror_real_usage]
    assert _item(bench, qualified)["url"] == url, "the store does not report the url written"
    return qualified


def _withdrawn_duplicate(bench: Bench, qualified: str) -> str:
    """A second item carrying ``qualified``'s origin, listed ahead of it and withdrawn: its id.

    What a copy that timed out after writing leaves and a retry then duplicates: two items
    of one board, each recording the same ticket as its `onetaskgraph.origin`. No store
    verb makes that on purpose — a copy that finds the origin updates the item — so the
    stand-in's `local-md` record is copied beside the original under a name the store lists
    first, which is exactly what such an item is there; the withdrawal is the store's own
    `task status set`, as the run that withdrew it made it.
    """
    location = _item(bench, qualified)["location"]
    assert isinstance(location, dict), location
    original = Path(str(location["path"]))
    # llmlint: ignore-block[tests_mirror_real_usage] No store verb writes a second item
    # carrying an origin a first already carries; a stand-in can only hold one as a record
    # beside it, which is what a timed-out copy's duplicate is there.
    shutil.copyfile(original, original.with_name(f"a-{original.name}"))
    # llmlint: ignore-end[tests_mirror_real_usage]
    native = qualified.removeprefix(f"{BOARD}:").rsplit("/", 1)
    duplicate = f"{BOARD}:{native[0]}/a-{native[1]}"
    _moved(bench, duplicate, tickets.Status.WITHDRAWN.value)
    assert _origin(_item(bench, duplicate)) == _origin(_item(bench, qualified))
    listed = _board_ids(bench)
    assert listed.index(duplicate) < listed.index(qualified), listed
    return duplicate


def _origin(item: dict[str, object]) -> str:
    metadata = item["metadata"]
    assert isinstance(metadata, dict), item
    return str(metadata[tickets.ORIGIN_KEY])


def _deps(bench: Bench, qualified: str, *direction: str) -> list[tuple[str, str, str]]:
    """The edges the store walks from ``qualified``: each as (from, to, kind)."""
    edges = _store(bench, "task", "deps", qualified, *direction)["items"]
    assert isinstance(edges, list), edges
    walked = []
    for edge in edges:
        assert isinstance(edge, dict), edge
        start, end = edge["from"], edge["to"]
        assert isinstance(start, dict) and isinstance(end, dict), edge
        walked.append((str(start["id"]), str(end["id"]), str(edge["kind"])))
    return walked


#: What a schema-4 ticket's `## Suggested fixes` offered: options, rather than one fix.
OPTIONS = (
    "- Either page the export by cursor.\n"
    "- Or keep offsets and re-read the last page.\n"
    "- Or drop the column from the export."
)
#: What the rewrite keeps as its one fix, and what it moves into `## Rejected fixes`.
ONE_FIX = "Page the export by cursor in `src/export.py`, as the listing already does."
REJECTED = (
    "- Keeping offsets and re-reading the last page: it hides the skipped rows rather than "
    "removing the cause.\n"
    "- Dropping the column: readers of the export need it."
)


def _schema_4(ticket: tickets.Ticket) -> str:
    """``ticket`` as schema 4 stored it, the shape the live tickets carry.

    A `## Repository` section after `## Impact`, and a `## Suggested fixes` offering options.
    """
    body = re.sub(
        rf"## {tickets.SUGGESTED_FIX}\n\n.*?\n\n(?=## )",
        f"## Suggested fixes\n\n{OPTIONS}\n\n",
        ticket.body,
        count=1,
        flags=re.DOTALL,
    ).replace(
        "\n\n## Examples\n\n",
        f"\n\n## Repository\n\n{ticket.repository}, at `src/export.py`.\n\n## Examples\n\n",
        1,
    )
    return frontmatter(
        {
            "title": ticket.title,
            "status": ticket.status.value,
            "repositories": [ticket.repository],
            "metadata": {tickets.KEY: tickets.record(ticket) | {"schema": 4}},
        },
        body,
    )


def _placed(staged: Path, ticket: Path) -> list[str]:
    """The agent's command putting a staged ticket in place, its host read from `hostname`."""
    return [
        "sh",
        "-c",
        f'sed "s/{READ_FROM_HOSTNAME}/$(hostname)/g" {shlex.quote(str(staged))} '
        f"> {shlex.quote(str(ticket))}",
    ]


def _decided_and_copied(python: str, ticket: Path, *extra: str) -> list[str]:
    """The agent's step before copying: ask the board the status, write it, validate, copy.

    ``extra`` is `--withdraw` for a ticket this run withdraws. The copy is the module's own
    `copy`, which the task prescribes: the store's copy held to the ticket's binding.
    """
    return [
        "bash",
        "-c",
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(REPO_ROOT))}\n"
        f"word=$({python} -m orchestrator.follow_up_tickets board-status --board {BOARD} "
        f"{shlex.join(extra)} {shlex.quote(str(ticket))})\n"
        f'sed -i "s/^status: .*/status: \\"$word\\"/" {shlex.quote(str(ticket))}\n'
        f"{python} -m orchestrator.follow_up_tickets validate {shlex.quote(str(ticket))}\n"
        f"{python} -m orchestrator.follow_up_tickets copy --board {BOARD} "
        f"{shlex.quote(str(ticket))}\n",
    ]


def _refused_shapes(python: str, witness: Path, shaped: dict[str, Path]) -> list[str]:
    """The agent's commands over the three shapes the tooling refuses, and their removal.

    Each is validated as the task says; the one whose shape passes is then asked
    `board-status` as the task says; what every command printed and exited with is kept
    in ``witness``; and the three files are removed, as an agent rewrites a refused
    ticket. Nothing copies, because nothing was let through.
    """
    validate = shlex.join([python, "-m", "orchestrator.follow_up_tickets", "validate"])
    decide = shlex.join(
        [python, "-m", "orchestrator.follow_up_tickets", "board-status", "--board", BOARD]
    )
    lines = [f"set -uo pipefail\ncd {shlex.quote(str(REPO_ROOT))}\n"]
    for name, path in shaped.items():
        lines.append(
            f"echo '== validate {name}' >> {shlex.quote(str(witness))}\n"
            f"{validate} {shlex.quote(str(path))} >> {shlex.quote(str(witness))} 2>&1\n"
            f'echo "exit $?" >> {shlex.quote(str(witness))}\n'
        )
    absent = shaped[REFUSED_URL_ABSENT]
    lines.append(
        f"echo '== board-status {REFUSED_URL_ABSENT}' >> {shlex.quote(str(witness))}\n"
        f"{decide} {shlex.quote(str(absent))} >> {shlex.quote(str(witness))} 2>&1\n"
        f'echo "exit $?" >> {shlex.quote(str(witness))}\n'
    )
    lines.append("rm " + " ".join(shlex.quote(str(path)) for path in shaped.values()) + "\n")
    return ["bash", "-c", "".join(lines)]


def _refused_then_kept(python: str, witness: Path, ticket: Path) -> list[str]:
    """The re-dispatch's first step over its ticket: ask the board, and copy only if it says so.

    What `board-status` printed and exited with goes to ``witness``, and the copy is chained
    on its success, so a refusal is exactly what keeps the ticket off the board.
    """
    decide = shlex.join(
        [python, "-m", "orchestrator.follow_up_tickets", "board-status", "--board", BOARD]
    )
    quoted = shlex.quote(str(witness))
    return [
        "bash",
        "-c",
        f"set -uo pipefail\ncd {shlex.quote(str(REPO_ROOT))}\n"
        f"if word=$({decide} {shlex.quote(str(ticket))} 2>> {quoted}); then\n"
        f'  echo "decided $word" >> {quoted}\n'
        f"  {python} -m orchestrator.follow_up_tickets copy --board {BOARD} "
        f"{shlex.quote(str(ticket))} && echo copied >> {quoted}\n"
        f"else\n"
        f'  echo "refused $?" >> {quoted}\n'
        f"fi\n",
    ]


def _category(item: dict[str, object]) -> object:
    status = item["status"]
    assert isinstance(status, dict), status
    return status["category"]


def _moved(bench: Bench, qualified: str, category: str) -> None:
    """Move a board item to ``category``, through the store's own verb for it.

    What a person does on the live board, which is off limits to a test: the board here is
    a `local-md` stand-in, and the adopted store sets an item's status there the same way
    it sets one anywhere — by category, changing nothing else about the item.
    """
    moved = _store(bench, "task", "status", "set", qualified, category)
    assert moved["status"] == _item(bench, qualified)["status"], moved


def _staged(bench: Bench, name: str, text: str) -> Path:
    """What the agent writes in its own scratch before putting it anywhere."""
    staged = bench.tmp / "agent" / name
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(text, encoding="utf-8")
    return staged


def _without_this_checkouts_venv(path: str) -> str:
    """``path`` with this checkout's ``.venv/bin`` removed, as a dispatched agent's shell has it.

    `uv run` puts that directory first for the whole launched process tree, so a search
    path that still carries it resolves `onetaskgraph` to the pinned one however the rest
    is ordered — which is why the bench's own ordering proves nothing here and this is
    composed instead: the incident's shell had no `.venv/bin` at all.
    """
    venv_bin = REPO_ROOT / ".venv" / "bin"
    kept = [entry for entry in path.split(os.pathsep) if entry and Path(entry) != venv_bin]
    assert kept, "the search path has nothing left once this checkout's .venv/bin is removed"
    return os.pathsep.join(kept)


def _validated_older_first(bench: Bench, python: str, ticket: Path, served: Path) -> list[str]:
    """The task's validate instruction, run as the incident's shell ran it.

    The interpreter is this checkout's own `.venv/bin/python3`, spelled in full as the
    task spells it, while the search path the command runs under has a plan store of
    another release first and this checkout's `.venv/bin` nowhere — the shape of the
    re-dispatch whose every ticket was refused with `the store reads … from None`. The
    witness records what `onetaskgraph` resolved to under that path, what validate
    printed, and its exit status; ``served`` is the older program's own log, absolute
    because the store is run from the checkout rather than from the turn's directory.
    """
    without_venv = _without_this_checkouts_venv(bench.environment["PATH"])
    path = f"{OLDER_PLAN_STORE}{os.pathsep}{without_venv}"
    # Every operand is quoted, the interpreter included: the checkout path it carries is
    # wherever this suite happens to run, and one holding a shell metacharacter would
    # otherwise change the command rather than name the program.
    validate = shlex.join([python, "-m", "orchestrator.follow_up_tickets", "validate", str(ticket)])
    return [
        "sh",
        "-c",
        f"PATH={shlex.quote(path)}; export PATH\n"
        f"command -v onetaskgraph > {VALIDATE_WITNESS} 2>&1\n"
        f"OLDER_PLAN_STORE_LOG={shlex.quote(str(served))} {validate} >> {VALIDATE_WITNESS} 2>&1\n"
        f'echo "exit $?" >> {VALIDATE_WITNESS}\n',
    ]


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
    narrowing_item: str
    removing_item: str
    proposed_item: str
    evaporated_draft: Path
    related_after_first: dict[str, object]
    new_deps_after_first: list[tuple[str, str, str]]
    narrowing_dependents_after_first: list[tuple[str, str, str]]
    unchanged_by_after_first: list[tuple[str, str, str]]
    refused_witness: str
    first_transcript: subprocess.CompletedProcess[str]
    new_after_move: dict[str, object]
    narrowing_after_move: dict[str, object]
    second: Pass
    rederive_witness: str
    new_deps_after_second: list[tuple[str, str, str]]
    new_after_second: dict[str, object]
    related_after_second: dict[str, object]
    related_deps_after_second: list[tuple[str, str, str]]
    local_after_second: dict[str, object]
    other_after_second: dict[str, object]
    comments_after_second: list[dict[str, object]]
    board_after_second: list[str]
    duplicate_issue: str
    duplicate_before_second: dict[str, object]
    board_before_second: list[str]
    duplicate_after_second: dict[str, object]
    duplicate_comments_after_second: list[dict[str, object]]
    new_ticket_after_second: str
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


def _pass(bench: Bench, name: str, main: str, *extra: str, report: str | None = None) -> Pass:
    log = _prompt_log(bench, name)
    environment = bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(log)}
    if report is not None:
        # llmlint: ignore-block[e2e_not_mocked] Only the paid provider process is substituted:
        # the report is the answer text the turn ends with, which the real engine journals and
        # `just transcript` renders, and a paid turn's answer would be nondeterministic.
        environment["FAKE_CODEX_ANSWERS"] = json.dumps([report])
        # llmlint: ignore-end[e2e_not_mocked]
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
        _moved(bench, other_issue, tickets.Status.DEFERRED.value)
        other_before = _item(bench, other_issue)
        # Three items of other root causes, as the board reports them: two a person
        # accepted, whose fixes bear on this run's drafts, and one still a proposal.
        narrowing_item = _board_item(
            bench,
            other,
            NARROWING_CAUSE,
            tickets.Status.ACCEPTED.value,
            NARROWING_URL,
            "Page the export by cursor, which leaves the listing's readers the last page",
            repository=OTHER_REPOSITORY,
        )
        removing_item = _board_item(
            bench,
            other,
            REMOVING_CAUSE,
            tickets.Status.ACCEPTED.value,
            REMOVING_URL,
            "Back the retry loop off, which ends every retry storm",
        )
        proposed_item = _board_item(
            bench,
            other,
            PROPOSED_CAUSE,
            tickets.Status.PROPOSED.value,
            PROPOSED_URL,
            "Have the sweep ignore symlinks",
        )
        # Two withdrawn items about the sweep trailer the store lists ahead of the open one.
        for at, cause in enumerate(WITHDRAWN_CAUSES):
            _board_item(
                bench,
                other,
                cause,
                tickets.Status.WITHDRAWN.value,
                ISSUES + str(50 + at),
                WITHDRAWN_FIX,
            )
        assert len(_board_ids(bench)) > PAGE_SIZE, "the board does not span pages"

        # A run with nothing to verify.
        empty_run = f"fu-empty-{pid}"
        empty = _run(["just", "follow-ups", empty_run, "--to", BOARD], bench)

        # The main run's four drafts, one per root cause.
        new_draft = _draft(bench, main, "The listing cursor skips the last page")
        shared_draft = _draft(bench, main, "The sweep trailer omits a family")
        evaporated_draft = _draft(bench, main, "A flaky listing sets off a retry storm")
        related_draft = _draft(bench, main, "The sweep follows a symlink")
        new_ticket = tickets.ticket_path(bench.drafts_root, main, NEW_CAUSE)
        shared_ticket = tickets.ticket_path(bench.drafts_root, main, SHARED_CAUSE)
        related_ticket = tickets.ticket_path(bench.drafts_root, main, RELATED_CAUSE)
        title = "some-service: the listing cursor skips the last page"
        # Written against the accepted narrowing fix: it depends on that item, and its
        # `## Impact` says what it assumes, by the item's URL.
        first_ticket = _ticket(
            main,
            NEW_CAUSE,
            (_draft_id(main, new_draft),),
            title,
            "Verified",
            depends_on=(narrowing_item,),
            impact_note=ASSUMED,
            severity=NARROWED_SEVERITY,
            root_cause_note=NARROWED_CAUSE,
            suggested_fix=REPLACEMENT_FIX,
            rejected=REFIXED,
        )
        # Related to a proposal, which is named as related and never assumed: no entry.
        related = _ticket(
            main,
            RELATED_CAUSE,
            (_draft_id(main, related_draft),),
            "some-service: the sweep follows a symlink",
            "Verified beside a proposal",
            impact_note=RELATED,
        )
        # The three shapes the tooling refuses, each under a root cause of its own.
        shaped = {
            name: tickets.ticket_path(bench.drafts_root, main, name)
            for name in (REFUSED_RELATED_KIND, REFUSED_TWO_SOURCES, REFUSED_URL_ABSENT)
        }
        refused_texts = {
            REFUSED_RELATED_KIND: tickets.render(
                _ticket(
                    main,
                    REFUSED_RELATED_KIND,
                    (_draft_id(main, new_draft),),
                    f"some-service: {REFUSED_RELATED_KIND}",
                    "Refused",
                    depends_on=(narrowing_item,),
                    impact_note=ASSUMED,
                )
            ).replace('"item": "task"}', '"item": "task", "kind": "related"}'),
            REFUSED_TWO_SOURCES: tickets.render(
                _ticket(
                    main,
                    REFUSED_TWO_SOURCES,
                    (_draft_id(main, new_draft),),
                    f"some-service: {REFUSED_TWO_SOURCES}",
                    "Refused",
                    depends_on=(narrowing_item, f"elsewhere:{other}/tickets/{NARROWING_CAUSE}"),
                    impact_note=ASSUMED,
                )
            ),
            REFUSED_URL_ABSENT: tickets.render(
                _ticket(
                    main,
                    REFUSED_URL_ABSENT,
                    (_draft_id(main, new_draft),),
                    f"some-service: {REFUSED_URL_ABSENT}",
                    "Refused",
                    depends_on=(narrowing_item,),
                )
            ),
        }
        refused_witness = bench.tmp / REFUSED_WITNESS
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
                # The board's accepted items, listed as the task's own step spells it.
                [
                    python,
                    str(RUN_TASK_STORE_INSTRUCTION),
                    "--prompt-log",
                    str(_prompt_log(bench, "first")),
                    "--board",
                    BOARD,
                    "--checkout",
                    str(REPO_ROOT),
                    "--witness",
                    ACCEPTED_WITNESS,
                ],
                # The duplicate search for the shared root cause, as the task spells it.
                [
                    python,
                    str(RUN_TASK_STORE_INSTRUCTION),
                    "--prompt-log",
                    str(_prompt_log(bench, "first")),
                    "--board",
                    BOARD,
                    "--search",
                    SEARCHED_TEXT,
                    "--checkout",
                    str(REPO_ROOT),
                    "--witness",
                    SEARCH_WITNESS,
                ],
                ["mkdir", "-p", str(new_ticket.parent)],
                _placed(_staged(bench, "new.md", tickets.render(first_ticket)), new_ticket),
                _placed(_staged(bench, "shared.md", tickets.render(shared)), shared_ticket),
                _placed(_staged(bench, "related.md", tickets.render(related)), related_ticket),
                [*validate, str(new_ticket), str(shared_ticket), str(related_ticket)],
                # The draft the accepted removing fix evaporates: no ticket, the draft gone.
                ["rm", str(evaporated_draft)],
                # The three refused shapes, written, refused, and removed.
                *[
                    _placed(_staged(bench, f"{name}.md", text), shaped[name])
                    for name, text in refused_texts.items()
                ],
                _refused_shapes(python, refused_witness, shaped),
                # The same validate instruction, under the search path the incident's
                # shell had: the older plan store first and this checkout's `.venv/bin`
                # absent. The helper says what it records and why.
                _validated_older_first(bench, python, shared_ticket, bench.tmp / VALIDATE_SERVED),
                ["rm", str(new_draft), str(shared_draft), str(related_draft)],
                _decided_and_copied(python, new_ticket),
                _decided_and_copied(python, related_ticket),
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
        first = _pass(bench, "first", main, report=DROPPED_REPORT)
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
        related_after_first = _item(bench, f"{BOARD}:{main}/tickets/{RELATED_CAUSE}")
        new_deps_after_first = _deps(bench, new_issue)
        narrowing_dependents_after_first = _deps(
            bench, narrowing_item, "--direction", "depended-on-by"
        )
        unchanged_by_after_first = [
            edge
            for item in (removing_item, proposed_item)
            for edge in _deps(bench, item, "--direction", "depended-on-by")
        ]
        first_transcript = _run(["just", "transcript", first.run], bench)

        # The user defers the new ticket: its board item is moved to `draft`. And they
        # un-accept the narrowing ticket the new one was written against.
        _moved(bench, new_issue, tickets.Status.DEFERRED.value)
        new_after_move = _item(bench, new_issue)
        _moved(bench, narrowing_item, tickets.Status.PROPOSED.value)
        narrowing_after_move = _item(bench, narrowing_item)
        # And they accept the proposal whose fix removes the related ticket's root cause.
        _moved(bench, proposed_item, tickets.Status.ACCEPTED.value)
        related_issue = f"{BOARD}:{main}/tickets/{RELATED_CAUSE}"
        # The related ticket evaporates on this pass: withdrawn under the now-accepted item,
        # depending on it, with the reason in its text naming that item.
        withdrawn = _ticket(
            main,
            RELATED_CAUSE,
            related.drafts,
            related.title,
            "Verified beside a proposal",
            depends_on=(proposed_item,),
            impact_note=WITHDRAWN,
        )

        # A timed-out copy left a second item carrying the new ticket's origin, which the
        # store lists ahead of the live one and which has since been withdrawn — the state
        # in which a re-copy once updated the closed duplicate and left the live issue stale.
        duplicate_issue = _withdrawn_duplicate(bench, new_issue)
        duplicate_before_second = _item(bench, duplicate_issue)
        board_before_second = _board_ids(bench)

        # The manager's feedback, re-dispatched over the same run: this run's issue is edited
        # by copying its ticket again, and its comment on the earlier run's issue is edited.
        # The ticket is staged bound to the withdrawn duplicate, as the store's own
        # correspondence names it; the copy has to rebind it to the live item.
        edited = _ticket(
            main, NEW_CAUSE, first_ticket.drafts, title, "Verified, examples tightened"
        )
        misbound = dataclasses.replace(
            edited,
            board_item=tickets.BoardItemId(duplicate_issue.removeprefix(f"{BOARD}:")),
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
        rederive_witness = bench.tmp / REDERIVE_WITNESS
        _script(
            bench,
            main,
            [
                # First, the ticket as the last pass left it — depending on an item a person
                # has since un-accepted: the board refuses it, and nothing is copied.
                _refused_then_kept(python, rederive_witness, new_ticket),
                # Re-derived against the board as it now is: no entry, no assumption, and
                # staged as the proposal it was first written as; the board decides.
                _placed(
                    _staged(bench, "new-edited.md", tickets.render(misbound, board=BOARD)),
                    new_ticket,
                ),
                # The related ticket, evaporated by the newly accepted fix: withdrawn.
                _placed(
                    _staged(bench, "related-withdrawn.md", tickets.render(withdrawn)),
                    related_ticket,
                ),
                _decided_and_copied(python, related_ticket, "--withdraw"),
                _decided_and_copied(python, new_ticket),
                ["bash", "-c", edit],
            ],
        )
        second = _pass(bench, "second", main, "--feedback", str(feedback))
        started.append(second.run)
        assert second.result.returncode == OK, second.result.stdout + second.result.stderr
        new_deps_after_second = _deps(bench, new_issue)
        new_after_second = _item(bench, new_issue)
        related_after_second = _item(bench, related_issue)
        related_deps_after_second = _deps(bench, related_issue)
        local_after_second = _item(bench, tickets.qualified_id(main, NEW_CAUSE))
        other_after_second = _item(bench, other_issue)
        comments_after_second = _comments(bench, other_issue)
        board_after_second = _board_ids(bench)
        duplicate_after_second = _item(bench, duplicate_issue)
        duplicate_comments_after_second = _comments(bench, duplicate_issue)
        new_ticket_after_second = new_ticket.read_text(encoding="utf-8")

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

        # A run whose tickets were filed at schema 4, carrying `## Repository` and options under
        # `## Suggested fixes` — the shape of the tickets already on the live board — one of
        # them accepted there by a person. The manager's feedback re-dispatch rewrites that
        # one and leaves the other.
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
                "Filed before one fix",
                HOST,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_schema_4(filed), encoding="utf-8")
            copied = _run(
                [store, "task", "copy", tickets.qualified_id(legacy_run, cause), "--to", BOARD],
                bench,
            )
            assert copied.returncode == 0, copied.stdout + copied.stderr
        legacy_issue = f"{BOARD}:{legacy_run}/tickets/{REWRITTEN_CAUSE}"
        _moved(bench, legacy_issue, tickets.Status.ACCEPTED.value)
        legacy_before = _item(bench, legacy_issue)
        rewritten = _ticket(
            legacy_run,
            REWRITTEN_CAUSE,
            legacy_drafts,
            f"some-service: {REWRITTEN_CAUSE.replace('-', ' ')}",
            "Brought to the current shape",
            rejected=REJECTED,
        )
        # Its one fix is the one the evidence supports; the other options it offered are
        # rejected, each with why.
        rewritten = dataclasses.replace(
            rewritten,
            body=rewritten.body.replace("Brought to the current shape (Suggested fix).", ONE_FIX),
        )
        _script(
            bench,
            legacy_run,
            [
                # Rewritten as a new proposal would be, its host read from `hostname`; the
                # board decides the status it is copied with.
                _placed(_staged(bench, "legacy.md", tickets.render(rewritten)), rewritten_ticket),
                _decided_and_copied(python, rewritten_ticket),
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
            consumed=[new_draft, shared_draft, evaporated_draft, related_draft],
            other_before=other_before,
            empty=empty,
            empty_run=empty_run,
            first=first,
            first_turn=first_turn,
            new_after_first=new_after_first,
            other_after_first=other_after_first,
            comments_after_first=comments_after_first,
            board_after_first=board_after_first,
            narrowing_item=narrowing_item,
            removing_item=removing_item,
            proposed_item=proposed_item,
            evaporated_draft=evaporated_draft,
            related_after_first=related_after_first,
            new_deps_after_first=new_deps_after_first,
            narrowing_dependents_after_first=narrowing_dependents_after_first,
            unchanged_by_after_first=unchanged_by_after_first,
            refused_witness=refused_witness.read_text(encoding="utf-8"),
            first_transcript=first_transcript,
            new_after_move=new_after_move,
            narrowing_after_move=narrowing_after_move,
            second=second,
            rederive_witness=rederive_witness.read_text(encoding="utf-8"),
            new_deps_after_second=new_deps_after_second,
            new_after_second=new_after_second,
            related_after_second=related_after_second,
            related_deps_after_second=related_deps_after_second,
            local_after_second=local_after_second,
            other_after_second=other_after_second,
            comments_after_second=comments_after_second,
            board_after_second=board_after_second,
            duplicate_issue=duplicate_issue,
            duplicate_before_second=duplicate_before_second,
            board_before_second=board_before_second,
            duplicate_after_second=duplicate_after_second,
            duplicate_comments_after_second=duplicate_comments_after_second,
            new_ticket_after_second=new_ticket_after_second,
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
        "--board-items",
        f"{written} board-items",
        "--copy",
        f"{written} copy",
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
    answered = json.loads(probe["stdout"])
    assert answered["pages"] > 1, "the drafts fit one page, so the task's paging is untested"
    assert sorted(str(one["id"]) for one in answered["items"]) == sorted(
        _draft_id(followed.main, draft) for draft in followed.consumed
    ), probe


def test_the_validate_instruction_runs_the_locked_plan_store_with_an_older_one_first(
    followed: Followed,
) -> None:
    """Validate reaches the locked plan store when the agent's shell resolves another.

    This is the shape of the follow-up re-dispatch whose worker resolved `onetaskgraph`
    to a `~/.local/bin` copy of release 0.2.12 while running validate on
    `.venv/bin/python3`: that release's `task show` reports no `location`, so every
    ticket was refused with `the store reads … from None`. The package's plan-store
    reads now run the CLI beside the interpreter rather than the search path's, and
    this drives that where it was hit — in the turn, with the older plan store first on
    the path and this checkout's `.venv/bin` absent from it.

    Three readings again, because a resolution rule alone would read the search path
    back to itself: that the path really resolved `onetaskgraph` to the older program,
    that validate reported the ticket sound and exited so, and that the older program's
    own log says it served nothing.
    """
    witness = followed.first_turn.directory.resolve() / VALIDATE_WITNESS
    assert witness.is_file(), (
        f"no command the turn ran wrote {VALIDATE_WITNESS} into "
        f"{followed.first_turn.directory}; {_ran(followed.bench)}"
    )
    resolved, *reported, status = witness.read_text(encoding="utf-8").splitlines()

    assert Path(resolved) == OLDER_PLAN_STORE / "onetaskgraph", (
        f"the search path validate ran under resolves `onetaskgraph` to {resolved}, so "
        "the condition this journey exists for — an older plan store ahead, and this "
        "checkout's .venv/bin absent — was never set up"
    )
    ticket = tickets.ticket_path(followed.bench.drafts_root, followed.main, SHARED_CAUSE)
    assert reported == [f"{tickets.PROG}: {ticket} is a sound ticket"], reported
    assert status == f"exit {tickets.SOUND}", (resolved, reported, status)
    served = followed.bench.tmp / VALIDATE_SERVED
    assert not served.is_file() or served.read_text(encoding="utf-8") == "", (
        f"the older plan store served the task's validate instruction: {served.read_text('utf-8')}"
    )


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
        f"-m orchestrator.follow_up_tickets board-items --board {BOARD} --search <text>`",
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
    validated = re.search(r"`(\S+ -m orchestrator\.follow_up_tickets validate) <path", steps)
    assert validated is not None, steps
    listed = re.search(r"`(\S+ -m orchestrator\.follow_up_tickets board-items) --board", task)
    assert listed is not None, task
    copied = re.search(r"`(\S+ -m orchestrator\.follow_up_tickets copy) --board", task)
    assert copied is not None, task
    contract = (
        tickets.ticket_contract(main, BOARD)
        .replace("@DRAFTS_ROOT@", str(root))
        .replace("@VALIDATE@", validated[1])
        .replace("@BOARD_STATUS@", decided[1])
        .replace("@BOARD_ITEMS@", listed[1])
        .replace("@COPY@", copied[1])
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

    assert followed.board_after_second == followed.board_before_second, (
        "a re-dispatch added an item"
    )
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


def test_a_feedback_re_dispatch_brings_a_schema_4_ticket_to_one_fix_updating_its_item(
    followed: Followed,
) -> None:
    """The back-fill of the live tickets: rewritten to one fix, left accepted.

    Both tickets were schema 4 and on the board — a `## Repository` section, and options under
    `## Suggested fixes` — and a person had accepted one. The re-dispatch rewrote that one and
    asked `board-status` before copying it, which updated the item the board already held —
    the same origin, no item added — rather than filing a second; the other it never touched,
    and the attached closeout names it rather than passing it.
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
    assert held_before[tickets.KEY]["schema"] == 4
    before_headings = re.findall(r"^## (.+)$", str(before["content"]), re.MULTILINE)
    assert "Repository" in before_headings and "Suggested fixes" in before_headings, before
    assert OPTIONS in str(before["content"]), "the schema-4 board item offered no options"
    assert _category(before) == accepted, "the schema-4 board item was not moved"

    (task,) = legacy.prompts
    flat_task = " ".join(task.split())
    for step in (
        "its `## Repository` section removed, any path the ticket still needs moved into "
        "`## Root cause`",
        "its `## Suggested fixes` rewritten as `## Suggested fix`, stating the one fix the "
        "ticket's evidence supports",
        "every other option it offered moved into `## Rejected fixes`, with why each was not "
        "chosen",
        "its `## Impact` section written from the evidence the ticket already carries",
    ):
        assert step in flat_task, step
    for shown in (local, after):
        ticket = tickets.from_store_item(shown)
        headings = re.findall(r"^## (.+)$", ticket.body, re.MULTILINE)
        assert headings == [
            "Root cause",
            "Impact",
            "Examples",
            "Evidence",
            "Suggested fix",
            "Rejected fixes",
            "Owning runs",
        ], headings
        fix = ticket.body.split(f"## {tickets.SUGGESTED_FIX}\n\n", 1)[1].split("\n\n## ", 1)[0]
        assert fix == ONE_FIX
        assert REJECTED in ticket.body.split(f"## {tickets.REJECTED_FIXES}\n\n", 1)[1]
        assert ticket.host == HOST, "the rewritten ticket's host is not what `hostname` printed"
        assert shown["repositories"] == [REPOSITORY] == [ticket.repository]
        metadata = shown["metadata"]
        assert isinstance(metadata, dict)
        assert metadata[tickets.KEY]["schema"] == tickets.SCHEMA == 6
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
    assert "the record is schema 4, and this reads schema 6" in legacy.result.stderr
    assert "carries `## Repository` and `## Suggested fixes`" in " ".join(
        legacy.result.stderr.split()
    )
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


def test_the_task_lists_the_boards_accepted_items_and_that_listing_selects_exactly_them(
    followed: Followed,
) -> None:
    """The step's own listing, run as the task spells it, answers every accepted item and no other.

    Read off the witness the turn wrote: the argv is the one the composed task fixes — the
    module's own every-page listing on this checkout's interpreter, its `--status` flags
    rendered from the module's vocabulary — and what it answered is the two accepted items,
    never the proposal, never the deferred item of the other run, never a withdrawn one.
    """
    (task,) = followed.first.prompts
    flat = " ".join(task.split())
    python = RECIPE_PYTHON
    assert (
        "**Write each ticket as if the board's accepted fixes were already in.** List the "
        "board's accepted items — those at `Todo` (`todo`), `Queued` (`queued`), `In Progress` "
        f'(`in-progress`) and `Done` (`done`) — with `"{python}" -m '
        f"orchestrator.follow_up_tickets board-items --board {BOARD} --status todo --status "
        "queued --status in-progress --status done`, which answers every page, the whole board"
    ) in flat
    witness = followed.first_turn.directory.resolve() / ACCEPTED_WITNESS
    assert witness.is_file(), (
        f"no command the turn ran wrote {ACCEPTED_WITNESS} into {followed.first_turn.directory}; "
        f"{_ran(followed.bench)}"
    )
    # llmlint: ignore[boundary_inputs_validated] The witness this journey's own helper wrote,
    # at the path this journey named; every field read here is asserted on below.
    probe = json.loads(witness.read_text(encoding="utf-8"))
    assert probe["problem"] is None, probe["problem"]
    assert probe["command"] == [
        python,
        "-m",
        "orchestrator.follow_up_tickets",
        "board-items",
        "--board",
        BOARD,
        *(word for status in tickets.Status if status.accepted for word in ("--status", status)),
    ], probe["command"]
    assert probe["returncode"] == 0, probe
    listed = json.loads(probe["stdout"])["items"]
    assert sorted(str(one["id"]) for one in listed) == sorted(
        [followed.narrowing_item, followed.removing_item]
    ), probe
    assert followed.proposed_item not in {str(one["id"]) for one in listed}


def test_the_duplicate_search_finds_the_open_item_past_the_stores_first_page(
    followed: Followed,
) -> None:
    """The task's search by text, run as spelled, answers an open item the first page lacks.

    The premise is read off the store itself, under the bench's page size: its own
    `task list --search` answers a first page of the two withdrawn sweep-trailer items and a
    `next` cursor, without the earlier run's open item for the shared root cause. The
    listing the task hands the agent answers that item — so the duplicate search decides
    "another run already has an open issue for this" from the whole board, which is the
    reading a one-page search got wrong on the live board.
    """
    bench = followed.bench
    other_issue = f"{BOARD}:{followed.other}/tickets/{SHARED_CAUSE}"
    first_page = _store(bench, "task", "list", "--source", BOARD, "--search", SEARCHED_TEXT)
    on_first_page = [str(one["id"]) for one in first_page["items"] if isinstance(one, dict)]
    assert first_page.get("next"), "the store's own search answered no cursor: one page held it"
    assert other_issue not in on_first_page, "the open item sits on the first page"
    assert on_first_page == [
        f"{BOARD}:{followed.other}/tickets/{cause}" for cause in WITHDRAWN_CAUSES
    ], on_first_page

    witness = followed.first_turn.directory.resolve() / SEARCH_WITNESS
    assert witness.is_file(), (
        f"no command the turn ran wrote {SEARCH_WITNESS} into {followed.first_turn.directory}; "
        f"{_ran(bench)}"
    )
    # llmlint: ignore[boundary_inputs_validated] The witness this journey's own helper wrote,
    # at the path this journey named; every field read here is asserted on below.
    probe = json.loads(witness.read_text(encoding="utf-8"))
    assert probe["problem"] is None, probe["problem"]
    assert probe["command"] == [
        RECIPE_PYTHON,
        "-m",
        "orchestrator.follow_up_tickets",
        "board-items",
        "--board",
        BOARD,
        "--search",
        SEARCHED_TEXT,
    ], probe["command"]
    assert probe["returncode"] == 0, probe
    found = [str(one["id"]) for one in json.loads(probe["stdout"])["items"]]
    assert other_issue in found, found
    assert found == [*on_first_page, other_issue], found
    assert _category(_item(bench, other_issue)) == tickets.Status.DEFERRED, (
        "the found item is closed"
    )


def test_a_ticket_narrowed_by_an_accepted_fix_lands_depending_on_it_with_the_url_in_its_impact(
    followed: Followed,
) -> None:
    """The board's dependency walk reports the edge from both ends, and the body names the URL."""
    new_issue = f"{BOARD}:{followed.main}/tickets/{NEW_CAUSE}"
    landed = tickets.from_store_item(followed.new_after_first)

    assert followed.new_deps_after_first == [(new_issue, followed.narrowing_item, "blocks")]
    assert (new_issue, followed.narrowing_item, "blocks") in (
        followed.narrowing_dependents_after_first
    )
    impact = landed.body.split(f"## {tickets.IMPACT}\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert ASSUMED in impact, impact
    assert NARROWING_URL in impact
    assert _severity_lines(impact) == NARROWED_SEVERITY, "the narrowed severity did not land"
    cause = landed.body.split("## Root cause\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert NARROWED_CAUSE in cause, "the narrowed root cause did not land"
    fix = landed.body.split(f"## {tickets.SUGGESTED_FIX}\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert fix == REPLACEMENT_FIX, "the fix that remains right once the accepted one is in"
    assert NARROWING_URL in fix, "the replacement does not say which accepted fix it is chosen on"
    rejected = landed.body.split(f"## {tickets.REJECTED_FIXES}\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert rejected == REFIXED, "the fix the accepted one displaced was not recorded as rejected"
    # The accepted fix lives in another repository of the board's owner: the reading is of the
    # whole board, and a ticket here depends on it across repositories.
    accepted = _item(followed.bench, followed.narrowing_item)
    assert accepted["repositories"] == [OTHER_REPOSITORY] != [landed.repository]
    assert NARROWING_URL.startswith(OTHER_ISSUES)
    # `task show` carries no `depends_on`: the walk above is the one read of the edge.
    assert tickets.DEPENDENCY_FIELD not in followed.new_after_first
    assert landed.depends_on == (), "a board item read without its edges depends on nothing"


def test_a_draft_an_accepted_fix_removes_is_dropped_under_that_items_url_and_reaches_no_board(
    followed: Followed,
) -> None:
    assert not followed.evaporated_draft.exists(), "the evaporated draft was left"
    assert not any(EVAPORATED_CAUSE in held for held in followed.board_after_first), (
        followed.board_after_first
    )
    assert not tickets.ticket_path(
        followed.bench.drafts_root, followed.main, EVAPORATED_CAUSE
    ).exists()
    # The report is the turn's answer, which the run's journal keeps and `just transcript`
    # renders — where an operator reads a settled node's report.
    transcript = followed.first_transcript
    assert transcript.returncode == 0, transcript.stdout + transcript.stderr
    assert DROPPED_REPORT in " ".join(transcript.stdout.split()), transcript.stdout
    assert REMOVING_URL in transcript.stdout


def _severity_lines(impact: str) -> tuple[tickets.Severity, tickets.Severity]:
    """The severity with no workaround and with it, as an `## Impact` section states them."""
    lines = {line["label"]: line["value"].strip() for line in tickets.IMPACT_LINE.finditer(impact)}
    return (
        tickets.Severity(lines[tickets.SEVERITY_LINE]),
        tickets.Severity(lines[tickets.MITIGATED_LINE]),
    )


def test_a_ticket_related_to_a_proposal_lands_unchanged_with_no_edge_naming_it_as_related(
    followed: Followed,
) -> None:
    """The unchanged fate against both accepted fixes, and a proposal named as related only."""
    related_issue = f"{BOARD}:{followed.main}/tickets/{RELATED_CAUSE}"
    landed = tickets.from_store_item(followed.related_after_first)

    assert related_issue in followed.board_after_first
    assert followed.narrowing_dependents_after_first == [
        (f"{BOARD}:{followed.main}/tickets/{NEW_CAUSE}", followed.narrowing_item, "blocks")
    ], "an accepted fix that does not bear on the related ticket changed it"
    assert followed.unchanged_by_after_first == [], (
        "the accepted fix that bears on nothing, or the proposal, was depended on"
    )
    assert _category(followed.related_after_first) == tickets.Status.PROPOSED
    assert RELATED in landed.body and PROPOSED_URL in landed.body
    impact = landed.body.split(f"## {tickets.IMPACT}\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert _severity_lines(impact) == FULL_SEVERITY


def test_a_re_dispatch_withdraws_its_own_item_a_newly_accepted_fix_evaporates_depending_on_it(
    followed: Followed,
) -> None:
    """The proposal was accepted between the passes, and its fix removes the related cause.

    The re-dispatch withdrew this run's own item for it under the withdrawal rules — the
    board held it as a proposal nobody accepted — with the reason naming the accepted item,
    on which the withdrawn ticket now depends.
    """
    related_issue = f"{BOARD}:{followed.main}/tickets/{RELATED_CAUSE}"
    withdrawn = tickets.from_store_item(followed.related_after_second)

    assert _category(followed.related_after_first) == tickets.Status.PROPOSED
    assert _category(followed.related_after_second) == tickets.Status.WITHDRAWN
    assert followed.related_deps_after_second == [(related_issue, followed.proposed_item, "blocks")]
    assert (related_issue, followed.proposed_item, "blocks") in _deps(
        followed.bench, followed.proposed_item, "--direction", "depended-on-by"
    )
    assert WITHDRAWN in withdrawn.body and PROPOSED_URL in withdrawn.body
    assert followed.board_after_second == followed.board_before_second, "a withdrawal added an item"


def test_the_three_forbidden_shapes_are_refused_naming_each_problem_and_reach_no_board(
    followed: Followed,
) -> None:
    """`validate` refuses the two edge shapes and `board-status` the URL the body never names."""
    witness = followed.refused_witness
    flat = " ".join(witness.split())
    sections = re.split(r"== ", witness)[1:]
    assert [section.split("\n", 1)[0] for section in sections] == [
        f"validate {REFUSED_RELATED_KIND}",
        f"validate {REFUSED_TWO_SOURCES}",
        f"validate {REFUSED_URL_ABSENT}",
        f"board-status {REFUSED_URL_ABSENT}",
    ], witness
    related, two, absent, decided = (" ".join(section.split()) for section in sections)

    assert related.endswith(f"exit {tickets.UNSOUND}"), related
    assert f"entry {followed.narrowing_item!r} is of kind 'related'" in related
    assert two.endswith(f"exit {tickets.UNSOUND}"), two
    assert f"entries name 2 sources (elsewhere, {BOARD})" in two
    assert absent.endswith(f"exit {tickets.SOUND}"), absent
    assert "is a sound ticket" in absent
    assert decided.endswith(f"exit {tickets.NOT_ACCEPTED}"), decided
    assert (
        f"entry {followed.narrowing_item!r} names an item whose URL {NARROWING_URL} the ticket's "
        "body never names"
    ) in decided
    assert "copy nothing for this ticket" in flat
    for name in (REFUSED_RELATED_KIND, REFUSED_TWO_SOURCES, REFUSED_URL_ABSENT):
        assert not any(name in held for held in followed.board_after_first), name
        assert not tickets.ticket_path(followed.bench.drafts_root, followed.main, name).exists()


def test_a_re_copy_past_a_withdrawn_duplicate_reaches_the_live_item_and_binds_the_ticket_to_it(
    followed: Followed,
) -> None:
    """Two items carry one ticket's origin; the re-dispatch's copy reaches the open one alone.

    The duplicate the store lists first was withdrawn, and the re-dispatch's ticket was
    staged bound to it — the correspondence the store's own copy followed when it updated a
    closed duplicate and left the live issue stale. The task's `board-status` and `copy`,
    run as the turn ran them, rebind the ticket to the run's open item, leave the duplicate
    one comment naming that item, and copy the edit there: the duplicate keeps the body it
    was withdrawn with.
    """
    main, duplicate = followed.main, followed.duplicate_issue
    new_issue = f"{BOARD}:{main}/tickets/{NEW_CAUSE}"
    before = followed.duplicate_before_second
    after = followed.duplicate_after_second
    assert _category(before) == _category(after) == tickets.Status.WITHDRAWN
    assert _origin(before) == _origin(followed.new_after_first), "the premise: one origin, twice"
    assert (after["title"], after["content"]) == (before["title"], before["content"]), (
        "the re-copy wrote the withdrawn duplicate"
    )

    local = followed.local_after_second
    assert followed.new_after_second["content"] == local["content"], "the live item is stale"
    assert followed.new_after_second["content"] != after["content"]
    metadata = local["metadata"]
    assert isinstance(metadata, dict), local
    assert metadata[tickets.KEY][tickets.BINDING_FIELD] == new_issue.removeprefix(f"{BOARD}:")
    assert metadata[tickets.ORIGIN_KEY] == new_issue
    assert tickets.ORIGIN_KEY + ": " + duplicate not in followed.new_ticket_after_second

    (notice,) = followed.duplicate_comments_after_second
    body = str(notice["body"]).strip()
    assert body.endswith(tickets.DUPLICATE_MARKER.format(run=main, survivor=new_issue)), body
    assert f"continues on {new_issue}" in body, body
    assert tickets.comment_owner(body) is None, "the notice reads as a run's own comment"


def test_a_re_dispatch_is_refused_on_an_un_accepted_item_and_re_derives_the_ticket_without_it(
    followed: Followed,
) -> None:
    """A person un-accepted the narrowing ticket between the passes.

    `board-status` on the unchanged ticket exits `NOT_ACCEPTED` naming the entry and what
    the board holds, nothing is copied on it, and the re-derived ticket — no entry, no
    assumption — updates the same board item, whose dependency walk then reports no edge.
    """
    witness = " ".join(followed.rederive_witness.split())

    assert _category(followed.narrowing_after_move) == tickets.Status.PROPOSED
    assert witness.endswith(f"refused {tickets.NOT_ACCEPTED}"), witness
    assert "copied" not in witness and "decided" not in witness, witness
    assert (
        f"entry {followed.narrowing_item!r} names an item the board holds at 'backlog', not at "
        "an accepted status (`todo`, `queued`, `in-progress`, `done`), so its fix is not assumed"
    ) in witness
    assert "re-derive it against the board as it now is" in witness
    (task,) = followed.second.prompts
    assert "are re-derived from the board as it now is on every pass" in " ".join(task.split())

    assert followed.new_deps_after_first != []
    assert followed.new_deps_after_second == []
    # Only the withdrawn duplicate, which no copy reaches, keeps the edge it was left with.
    dependents = _deps(followed.bench, followed.narrowing_item, "--direction", "depended-on-by")
    assert [edge for edge in dependents if edge[0] != followed.duplicate_issue] == [], dependents
    edited = tickets.from_store_item(followed.new_after_second)
    assert NARROWING_URL not in edited.body and ASSUMED not in edited.body
    impact = edited.body.split(f"## {tickets.IMPACT}\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert _severity_lines(impact) == FULL_SEVERITY, "the narrowed severity outlived the entry"
    assert REPLACEMENT_FIX not in edited.body, "the replacement fix outlived the entry"
    assert f"## {tickets.REJECTED_FIXES}" not in edited.body, "the displaced fix outlived the entry"
    assert followed.new_after_second["content"] == followed.edited_body.replace(
        READ_FROM_HOSTNAME, HOST
    )
    assert followed.board_after_second == followed.board_before_second
    assert _deps(followed.bench, tickets.qualified_id(followed.main, NEW_CAUSE)) == []
