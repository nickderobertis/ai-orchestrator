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
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs, so it is scheduled
#: with every other journey that does — which also keeps this module's one fixture on one
#: worker.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The provider's stand-in, and the guard covering the identities `ONEHARNESS_BIN_*` cannot
#: reach, so a routing mistake refuses a turn rather than spending one.
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

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

#: The repository every draft here is about, and the commit its claims were verified at.
REPOSITORY = "github.com/nickderobertis/some-service"
COMMIT = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
VERIFIED_AT = "2026-01-01T00:00:00Z"

#: The two root causes the main run's drafts carry: one no board item names yet, and one an
#: earlier run already filed an open issue for.
NEW_CAUSE = "listing-cursor-skips-last-page"
SHARED_CAUSE = "sweep-trailer-omits-a-family"

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


def _bench(tmp: Path) -> Bench:
    drafts_root, plans, board, runs = (
        tmp / name for name in ("follow-ups", "plans", "board", "runs")
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
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["XDG_STATE_HOME"] = str(tmp / "state")
    return Bench(environment, tmp, drafts_root, plans, board, runs)


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


def _ticket(run: str, cause: str, drafts: tuple[str, ...], title: str, body: str) -> tickets.Ticket:
    return tickets.Ticket(
        title=title,
        status=tickets.Status.OPEN,
        root_cause=tickets.RootCause(cause),
        repository=tickets.Origin(REPOSITORY),
        created_by_run=tickets.RunId(run),
        owning_runs=(tickets.RunId(run),),
        drafts=tuple(tickets.QualifiedDraftId(draft) for draft in drafts),
        basis=(tickets.Basis(tickets.Origin(REPOSITORY), tickets.Commit(COMMIT)),),
        verified_at=tickets.Timestamp(VERIFIED_AT),
        body="\n\n".join(f"## {heading}\n\n{body} ({heading})." for heading in tickets.HEADINGS),
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
    new_after_first: dict[str, object]
    other_after_first: dict[str, object]
    comments_after_first: list[dict[str, object]]
    board_after_first: list[str]
    second: Pass
    new_after_second: dict[str, object]
    other_after_second: dict[str, object]
    comments_after_second: list[dict[str, object]]
    board_after_second: list[str]
    edited_body: str
    edited_comment: str
    unsound: subprocess.CompletedProcess[str]
    unsound_ticket: Path
    detached: subprocess.CompletedProcess[str]
    detached_seconds: float
    detached_run: str
    mine: subprocess.CompletedProcess[str]
    driving: subprocess.CompletedProcess[str]
    exempt_gate: subprocess.CompletedProcess[str]
    tampered_gate: subprocess.CompletedProcess[str]


def _pass(bench: Bench, name: str, main: str, *extra: str) -> Pass:
    log = bench.tmp / f"prompts-{name}.jsonl"
    environment = bench.environment | {"FAKE_CODEX_PROMPT_LOG": str(log)}
    result = _run(
        ["just", "follow-ups", main, "--to", BOARD, *extra], bench, environment=environment
    )
    runs = sorted(path.name for path in bench.runs.glob(f"{main}{SUFFIX}*"))
    return Pass(result, runs[-1] if runs else "", _prompts(log))


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `tests/plan_tooling` is
# already the Nx project edge this repository keeps for journeys that launch the installed
# engine, keyed on `planToolingWorkspace`, which covers every file these launches read. The
# fixture is module-scoped and spends five launches whose turns are the provider's stand-in.
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
        )
        earlier_path = tickets.ticket_path(bench.drafts_root, other, SHARED_CAUSE)
        earlier_path.parent.mkdir(parents=True)
        earlier_path.write_text(tickets.render(earlier), encoding="utf-8")
        copied = _run(
            [store, "task", "copy", tickets.qualified_id(other, SHARED_CAUSE), "--to", BOARD], bench
        )
        assert copied.returncode == 0, copied.stdout + copied.stderr
        other_issue = f"{BOARD}:{other}/tickets/{SHARED_CAUSE}"
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
                ["mkdir", "-p", str(new_ticket.parent)],
                [
                    "cp",
                    str(_staged(bench, "new.md", tickets.render(first_ticket))),
                    str(new_ticket),
                ],
                [
                    "cp",
                    str(_staged(bench, "shared.md", tickets.render(shared))),
                    str(shared_ticket),
                ],
                [*validate, str(new_ticket), str(shared_ticket)],
                ["rm", str(new_draft), str(shared_draft)],
                _from_checkout(
                    store, "task", "copy", tickets.qualified_id(main, NEW_CAUSE), "--to", BOARD
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
            ],
        )
        first = _pass(bench, "first", main)
        started.append(first.run)
        assert first.result.returncode == OK, first.result.stdout + first.result.stderr
        new_issue = f"{BOARD}:{main}/tickets/{NEW_CAUSE}"
        assert new_issue in _board_ids(bench), _ran(bench)
        new_after_first = _item(bench, new_issue)
        other_after_first = _item(bench, other_issue)
        comments_after_first = _comments(bench, other_issue)
        board_after_first = _board_ids(bench)

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
                [
                    "cp",
                    str(_staged(bench, "new-edited.md", tickets.render(edited))),
                    str(new_ticket),
                ],
                [*validate, str(new_ticket)],
                _from_checkout(
                    store, "task", "copy", tickets.qualified_id(main, NEW_CAUSE), "--to", BOARD
                ),
                ["bash", "-c", edit],
            ],
        )
        second = _pass(bench, "second", main, "--feedback", str(feedback))
        started.append(second.run)
        assert second.result.returncode == OK, second.result.stdout + second.result.stderr
        new_after_second = _item(bench, new_issue)
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
            'status: "todo"', 'status: "todo"\nproject: "a-project"'
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
            new_after_first=new_after_first,
            other_after_first=other_after_first,
            comments_after_first=comments_after_first,
            board_after_first=board_after_first,
            second=second,
            new_after_second=new_after_second,
            other_after_second=other_after_second,
            comments_after_second=comments_after_second,
            board_after_second=board_after_second,
            edited_body=edited.body,
            edited_comment=edited_comment,
            unsound=unsound,
            unsound_ticket=unsound_ticket,
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
        f"onetaskgraph task list --source drafts --project {main} --json",
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
        f"onetaskgraph task list --source {BOARD} --search <text> --json",
        "every issue you created or updated with its URL",
        "every dropped draft with its reason",
        "every finding that should have been surfaced live",
    ):
        assert instruction in flat, instruction
    contract = tickets.ticket_contract(main, BOARD).replace("@DRAFTS_ROOT@", str(root))
    assert contract in task, "the ticket shape is not the one the module renders"
    assert tickets.comment_contract(main, BOARD) in task, "board ownership is not the module's"
    assert "## This is a re-dispatch" not in task
    assert "## Feedback on the previous follow-up run" not in task


def test_a_verified_ticket_lands_on_the_board_with_its_shape_and_no_project_or_repositories(
    followed: Followed,
) -> None:
    landed = followed.new_after_first

    ticket = tickets.from_store_item(landed)
    assert ticket.created_by_run == followed.main
    assert ticket.root_cause == NEW_CAUSE
    assert ticket.basis == (tickets.Basis(tickets.Origin(REPOSITORY), tickets.Commit(COMMIT)),)
    assert landed["project"] is None
    assert landed["repositories"] == []
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
    before, after = followed.other_before, followed.other_after_first

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

    assert followed.board_after_second == followed.board_after_first, "a re-dispatch added an item"
    assert followed.new_after_second["content"] == followed.edited_body
    assert followed.new_after_second["content"] != followed.new_after_first["content"]
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
