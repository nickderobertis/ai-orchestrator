"""`just follow-ups-answer-comments` over comments on the tickets of two owning batches.

`onefollowup comments answer` owns the pass: it gathers each person's comment for the batch
that filed its ticket, runs the `comments.launch_command` `onefollowup.yaml` configures —
`just follow-ups-answer-batch <batch> <feedback>` — once per batch, and settles only when
every batch succeeded. This module holds this host's half: that the recipe runs that verb in
a flow, that each batch's feedback plan is rendered from `templates/follow-up-feedback.md.j2`
with its answering task through the host's `follow-up-task` template layer and joins the
flow, and what a manager sees when one batch's run fails.

Each case files two tickets on a local destination standing in for the board, one per
batch, through onefollowup's own verbs — `draft`, `prepare` with the configured
`batch_preparing` hook over a recorded run root, the follow-up agent's choices
(`scripted_agent.py file`), `publish` and `settle` — so onefollowup holds both as filed by a
batch of this host. A person then comments on each ticket. **The paid model alone is
doubled**: `tests/e2e/fake_codex.py` runs the follow-up agent's turn as
`scripted_agent.py feedback` for its batch, the real store and onefollowup verbs answering
each comment. In the failing case the provider dies on one batch's every turn, the way
`FAKE_CODEX_UNAVAILABLE_ATTEMPTS` kills a launch, so that batch's feedback run fails while
the other's runs.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `xdist_group` selects an xdist
worker under this project's `--dist loadgroup`, keeping each module-scoped pass on one
worker; it selects no test tier.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple, NewType

import boundary_home
import follow_up_store
import follow_up_variables
import pytest
import short_state
from harness_indirections import established_indirections
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
AGENT = Path(__file__).with_name("scripted_agent.py")
INDIRECTION_CALLER = "tests/e2e/follow_ups/test_follow_up_comments_e2e.py"
SESSION = "e2e-follow-up-comments"
ABOUT = "github.com/nickderobertis/some-service"

#: What an enclosing dispatch exports that would decide these invocations for them.
INHERITED_PREFIXES = ("ONEFOLLOWUP_",)
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_RUN_ROOT",
    "ONEPIPELINE_NODE_ID",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ONEPIPELINE_FLOW",
    "ONEPIPELINE_HOOK",
    "ONEVCS_SESSION",
    "ORCHESTRATOR_ASK_MANAGER",
    "ONETASKGRAPH_DEFAULT_SOURCES",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    *follow_up_variables.all_names(),
    follow_up_store.RUNS_ROOT,
)

#: A follow-up batch: the public id of the run whose drafts it holds.
BatchId = NewType("BatchId", str)
#: The id the store gave a comment on a ticket.
CommentId = NewType("CommentId", str)

ALPHA, BETA = BatchId("alpha-batch"), BatchId("beta-batch")
#: The two owning batches, each filing one ticket for its own root cause.
BATCHES = {ALPHA: "alpha-root-cause", BETA: "beta-root-cause"}
DRAFT_BODY = (
    "## What happened\nA reader missed results.\n\n## Where\n`src/cursor.py`.\n\n"
    "## Why it is out of scope\nThe run was about something else.\n\n"
    "## Evidence\nSeen in the run's own log.\n"
)
#: What the person asks on each ticket.
QUESTION = "Why is this high and not medium?\n"
#: The follow-up agent's task names its batch so, which keys each scripted turn to its batch.
FEEDBACK_MARKER = "Answer the people's comments on batch `{batch}`"


class Ticket(NamedTuple):
    """One filed ticket and the person's comment on it."""

    batch: BatchId
    item: Path
    comment: CommentId


class Pass(NamedTuple):
    """One case: its environment, the tickets, and what the recipe did."""

    environment: dict[str, str]
    store: follow_up_store.Store
    runs: Path
    tickets: dict[BatchId, Ticket]
    answered: subprocess.CompletedProcess[str]
    gathered_after: dict[str, object]


def _environment(root: Path, oneharness_bin: str) -> tuple[dict[str, str], follow_up_store.Store]:
    inherited = {
        name: value
        for name, value in os.environ.items()
        if name not in INHERITED and not name.startswith(INHERITED_PREFIXES)
    }
    # The repository the tickets are about is registered public, as the store classifies a
    # ticket by its repository and a private one never reaches a destination declared public.
    environment = boundary_home.environment(
        inherited, boundary_home.registered_home(root, public=(ABOUT,))
    )
    store = follow_up_store.isolated(root)
    environment = store.over(environment)
    environment["CLAUDE_CODE_SESSION_ID"] = SESSION
    environment["ONEFOLLOWUP_HOOKS__OWNER__SESSION"] = SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["PATH"] = (
        f"{PAID_PROVIDER_GUARD}{os.pathsep}{REPO_ROOT / '.venv' / 'bin'}{os.pathsep}"
        f"{REPO_ROOT / 'node_modules' / '.bin'}{os.pathsep}{environment['PATH']}"
    )
    environment.update(established_indirections(INDIRECTION_CALLER))
    return environment, store


def _ran(
    environment: Mapping[str, str], argv: list[str], *, stdin: str = "", seconds: int = 300
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's recipes and the installed verbs
        argv,
        cwd=REPO_ROOT,
        env=dict(environment),
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _ok(environment: Mapping[str, str], argv: list[str], stdin: str = "") -> str:
    ran = _ran(environment, argv, stdin=stdin)
    assert ran.returncode == 0, f"{argv}: {ran.stdout}{ran.stderr}"
    return ran.stdout


def _finished_run(environment: Mapping[str, str], root: Path, batch: BatchId) -> None:
    """Launch a one-command plan named ``batch`` to completion, the run whose drafts the
    batch holds, so its `batch_preparing` hook reads a run the engine recorded."""
    plan = root / f"{batch}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Finish, landing nothing"},
                "name": batch,
                "tasks": [{"id": "finish", "kind": "command", "command": ["true"]}],
            }
        ),
        encoding="utf-8",
    )
    _ok(
        environment,
        ["scripts/onepipeline.sh", "start", project_from_plan(plan, batch)]
        + ["--launch-config", "config/onepipeline.workflows.yaml", "--dag-graph", "off"],
    )
    status = json.loads(_ok(environment, ["scripts/onepipeline.sh", "status", batch, "--json"]))
    assert status["public_id"] == batch, status


def _filed(
    environment: dict[str, str], store: follow_up_store.Store, root: Path
) -> dict[BatchId, Path]:
    """File one ticket per batch through onefollowup's own verbs; answer each one's file."""
    for batch, cause in BATCHES.items():
        _finished_run(environment, root, batch)
        _ok(
            environment,
            ["onefollowup", "draft", "--batch", batch, "--title", f"The {cause} drafted"]
            + ["--repository", ABOUT],
            DRAFT_BODY,
        )
        _ok(environment, ["onefollowup", "prepare", "--batch", batch])
        _ok(environment, [sys.executable, str(AGENT), "file", str(REPO_ROOT), batch, cause])
        _ok(environment, ["onefollowup", "publish", "--batch", batch])
        _ok(environment, ["onefollowup", "settle", "--batch", batch])
    found: dict[BatchId, Path] = {}
    for item in (store.board / "tasks").glob("*.md"):
        text = item.read_text("utf-8")
        for batch, cause in BATCHES.items():
            # `scripted_agent.py file` titles each ticket for its root cause.
            if f"some-service: {cause} fails" in text.split("\n---\n", 1)[0]:
                found[batch] = item
    assert sorted(found) == sorted(BATCHES), sorted(path.name for path in store.board.rglob("*"))
    return found


def _commented(
    environment: Mapping[str, str], store: follow_up_store.Store, item: Path
) -> CommentId:
    """A person's comment on ``item``, through the store; answer its id."""
    added = subprocess.run(  # noqa: S603 - a person writing through the store
        ["onetaskgraph", "task", "comment", "add", f"{follow_up_store.DESTINATION}:{item.stem}"]
        + ["--author", "alice", "--json", "--no-interactive"],
        cwd=store.directory,
        env=dict(environment),
        input=QUESTION,
        text=True,
        capture_output=True,
        check=False,
    )
    assert added.returncode == 0, added.stdout + added.stderr
    return CommentId(str(json.loads(added.stdout)["id"]))


def _scripted(root: Path, environment: dict[str, str], failing: BatchId | None) -> None:
    """Key the follow-up agent's turn for each batch to the commands it runs; the provider
    dies on every turn of ``failing``'s, the way a launch that started and then failed does."""
    script = root / "agent-commands.json"
    keyed = {
        FEEDBACK_MARKER.format(batch=batch): (
            [["bash", "-c", "kill -KILL $PPID"]]
            if batch == failing
            else [[sys.executable, str(AGENT), "feedback", str(REPO_ROOT), batch]]
        )
        for batch in BATCHES
    }
    script.write_text(json.dumps(keyed), encoding="utf-8")
    environment["FAKE_CODEX_RUN_ON_MARKER"] = str(script)
    environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(root / "agent-ran.jsonl")


def _gathered(environment: Mapping[str, str]) -> dict[str, object]:
    answer: dict[str, object] = json.loads(
        _ok(environment, ["onefollowup", "comments", "gather", "--dry-run"])
    )
    return answer


def _stop_feedback_runs(environment: Mapping[str, str], runs: Path) -> None:
    for path in runs.glob("*-answers-*"):
        _ran(environment, ["just", "stop", path.name], seconds=60)


def _pass(root: Path, oneharness_bin: str, failing: BatchId | None) -> Pass:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment, store = _environment(root, oneharness_bin)
    runs = root / "runs"
    filed = _filed(environment, store, root)
    tickets = {
        batch: Ticket(batch, item, _commented(environment, store, item))
        for batch, item in filed.items()
    }
    _scripted(root, environment, failing)
    try:
        answered = _ran(environment, ["just", "follow-ups-answer-comments"], seconds=900)
        return Pass(environment, store, runs, tickets, answered, _gathered(environment))
    finally:
        _stop_feedback_runs(environment, runs)


@pytest.fixture(scope="module")
def answered(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Pass:
    return _pass(tmp_path_factory.mktemp("comments-answered"), oneharness_bin, None)


@pytest.fixture(scope="module")
def one_failed(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Pass:
    return _pass(tmp_path_factory.mktemp("comments-one-failed"), oneharness_bin, ALPHA)


def _replies(ticket: Ticket) -> int:
    return ticket.item.read_text("utf-8").count(f'kind="reply" answers="{ticket.comment}"')


def _feedback_runs(case: Pass) -> dict[BatchId, str]:
    """Each feedback run under the runs root, by the batch it answers."""
    found: dict[BatchId, str] = {}
    for path in sorted(case.runs.glob("*-answers-*")):
        batch = next(one for one in BATCHES if path.name.startswith(f"{one}-answers-"))
        assert batch not in found, sorted(case.runs.iterdir())
        found[batch] = path.name
    return found


def _launch(case: Pass, run: str) -> dict[str, object]:
    record: dict[str, object] = json.loads((case.runs / run / "launch.json").read_text("utf-8"))
    return record


def _nodes(case: Pass, run: str) -> dict[str, str]:
    read = _ran(case.environment, ["just", "results", run, "--json"], seconds=60)
    assert read.returncode == 0, read.stdout + read.stderr
    return {str(node["id"]): str(node["status"]) for node in json.loads(read.stdout)["nodes"]}


class FeedbackPlan(NamedTuple):
    """A feedback plan as the store holds it: its own record and its tasks' records."""

    project: dict[str, Any]
    tasks: list[dict[str, Any]]


def _project(case: Pass, run: str) -> FeedbackPlan:
    """The feedback plan ``run`` was launched from, with its one task, as the store holds them."""
    project = str(_launch(case, run)["project"])
    shown = json.loads(
        _ok(case.environment, ["onetaskgraph", "project", "show", project, "--json"])
    )
    listed = json.loads(
        _ok(
            case.environment,
            ["onetaskgraph", "task", "list", "--source", "follow-up-runs", "--project", project]
            + ["--json", "--no-interactive"],
        )
    )
    return FeedbackPlan(shown["items"][0]["item"], [one["item"] for one in listed["items"]])


#: Where a gathering starts on a destination no comment pass has settled on.
UNSETTLED = "no watermark yet: every comment"


def _after(case: Pass) -> dict[str, object]:
    """The snapshot a dry gathering after the pass wrote, as onefollowup recorded it."""
    snapshot = Path(str(case.gathered_after["snapshot"])) / "snapshot.json"
    held: dict[str, object] = json.loads(snapshot.read_text("utf-8"))
    return held


def _still_selected(case: Pass) -> set[CommentId]:
    """Which of the people's comments a dry gathering after the pass still selects to answer."""
    selection = json.dumps(_after(case)["selection"])
    return {ticket.comment for ticket in case.tickets.values() if ticket.comment in selection}


def _starts_from(case: Pass) -> str:
    """Where that gathering starts on the destination: only a settle moves its watermark."""
    starts = _after(case)["starts"]
    assert isinstance(starts, dict), starts
    return str(starts[follow_up_store.DESTINATION]["origin"])


@pytest.mark.xdist_group("follow-up-comments")
def test_dry_run_reports_exactly_what_onefollowups_dry_gathering_reports(tmp_path: Path) -> None:
    environment, store = _environment(tmp_path, "")
    filed = _filed(environment, store, tmp_path)
    for item in filed.values():
        _commented(environment, store, item)

    recipe = _ran(environment, ["just", "follow-ups-answer-comments", "--dry-run"])
    gathered = _ran(environment, ["onefollowup", "comments", "gather", "--dry-run"])

    assert recipe.returncode == gathered.returncode == 0, recipe.stderr + gathered.stderr
    ours, theirs = json.loads(recipe.stdout), json.loads(gathered.stdout)
    # Each dry gathering names the snapshot directory it wrote, stamped with its own second.
    for answer in (ours, theirs):
        answer.pop("snapshot")
        answer.pop("launch_command")
    assert ours == theirs
    assert ours["items"] == len(BATCHES), ours
    assert not list((tmp_path / "runs").glob("*-answers-*"))


@pytest.mark.xdist_group("follow-up-comments")
def test_each_batch_runs_its_own_feedback_plan_in_the_recipes_flow(answered: Pass) -> None:
    assert answered.answered.returncode == 0, answered.answered.stdout + answered.answered.stderr
    runs = _feedback_runs(answered)
    assert sorted(runs) == sorted(BATCHES), sorted(path.name for path in answered.runs.iterdir())
    flows = {str(_launch(answered, run).get("flow") or "") for run in runs.values()}
    assert len(flows) == 1 and "" not in flows, flows
    for batch, run in runs.items():
        assert _nodes(answered, run) == {"answer": "done"}, (batch, run)


@pytest.mark.xdist_group("follow-up-comments")
def test_each_feedback_plan_is_rendered_from_the_hosts_templates(answered: Pass) -> None:
    for batch, run in _feedback_runs(answered).items():
        held = _project(answered, run)
        provenance = held.project["metadata"]["onetaskgraph.template"]
        assert Path(provenance["template"]).name == "follow-up-feedback.md.j2", provenance
        (task,) = held.tasks
        assert "follow-up-task" in str(task["metadata"]["onetaskgraph.template"]), task["metadata"]
        assert FEEDBACK_MARKER.format(batch=batch) in str(task["content"]), task["content"]


@pytest.mark.xdist_group("follow-up-comments")
def test_every_comment_is_answered_once_and_then_settled(answered: Pass) -> None:
    for ticket in answered.tickets.values():
        assert _replies(ticket) == 1, ticket.item.read_text("utf-8")
    assert _still_selected(answered) == set(), answered.gathered_after
    assert _starts_from(answered) != UNSETTLED, _after(answered)


@pytest.mark.xdist_group("follow-up-comments")
def test_a_failed_batch_leaves_the_other_batch_answered_nothing_settled_and_names_it(
    one_failed: Pass,
) -> None:
    result = one_failed.answered
    assert result.returncode != 0, result.stdout + result.stderr
    assert ALPHA in result.stdout + result.stderr, result.stdout + result.stderr
    runs = _feedback_runs(one_failed)
    assert sorted(runs) == sorted(BATCHES), sorted(path.name for path in one_failed.runs.iterdir())
    assert _nodes(one_failed, runs[BETA]) == {"answer": "done"}
    assert _nodes(one_failed, runs[ALPHA]) != {"answer": "done"}
    assert _replies(one_failed.tickets[BETA]) == 1
    assert _replies(one_failed.tickets[ALPHA]) == 0
    # Nothing settled: no watermark was written, and the failed batch's comment is still
    # gathered to answer.
    assert _starts_from(one_failed) == UNSETTLED, _after(one_failed)
    assert _still_selected(one_failed) == {one_failed.tickets[ALPHA].comment}, _after(one_failed)
