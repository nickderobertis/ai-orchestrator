"""This host's follow-up wiring over onefollowup, driven as a manager meets it.

Two journeys, and only this host's half of each: what onefollowup and onetaskgraph do with
a batch — verifying, filing, answering — is proven in their own repositories.

**Verifying a run's follow-ups.** A real `just orchestrate` run lands one change on each of
two scratch repositories registered with `onevcs` — one whose `cycle-time` delivery budget
the change exceeds, one whose budget it keeps within — and its node drafts two follow-ups:
one through `$ORCHESTRATOR_FOLLOW_UP_DRAFT`, one through `just follow-up`. The run ends with
every node done, so the real success hook, `scripts/run-ended.sh`, runs `just follow-ups`,
which creates the `follow-ups` plan from `templates/follow-ups.md.j2` and launches it
detached; its steps are real `onefollowup prepare|check|publish|settle`, its `prepare` runs
the configured `batch_preparing` hook, `scripts/follow-up-budget-inputs.sh`, and its
verifying agent is the follow-up graph's single-sided member. A second case ends a run with
a failed node, shows the failure hook launching nothing, and verifies the run's drafts by
hand with `just follow-ups`, to the same outcomes.

**Answering board comments.** Over the first case's destination, a person comments on the
filed ticket; `just follow-ups-answer-comments` gathers it, runs the owning batch's feedback
plan in its flow and settles, answering it once; a second pass answers nothing.

The destination is a local Markdown folder standing in for the `followups` board, seeded
before the run with a ticket record at schema 14 and an earlier run's evidence and reply
comments in the board's marker format, the run's evidence being for that ticket's root
cause. **The paid model alone is doubled**: `tests/e2e/fake_backend.py` for the main run's
worker and judge, whose turn runs the commit and drafting commands a model would, and
`tests/e2e/fake_codex.py` for the follow-up agent, whose turn runs
`tests/e2e/follow_ups/scripted_agent.py` — the agent's choices, made with the real
onefollowup and plan-store commands.

**The review item is the known upstream gap.** `onefollowup.yaml` names the engine's
`onepipeline human-items raise --phase follow-up-review` as the `batch_settled` hook. The
pinned engine refuses that argv, because `raise --phase` requires a `--context` file that
onefollowup's settle does not hand it, so no `follow-up-review` item is raised until that is
fixed upstream. The journey holds the settle step to running the configured hook and to the
engine's refusal of it, so the day either side moves, this fails and says so.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `xdist_group` selects an xdist
worker under this project's `--dist loadgroup`, keeping each module-scoped launch on one
worker; it selects no test tier.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

import follow_up_store
import follow_up_variables
import pytest
import short_state
import yaml
from conftest import git
from fake_backend import RUN_ON_MARKER_ENV
from harness_indirections import established_indirections
from project_fixtures import helper, project_from_plan
from scratch_identity import Identity, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
AGENT = Path(__file__).with_name("scripted_agent.py")
INDIRECTION_CALLER = "tests/e2e/follow_ups/test_follow_ups_e2e.py"
SESSION = "e2e-follow-ups"

#: What an enclosing dispatch exports that would decide these launches for them.
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

#: The two drafts the run's node writes, and the repository they are about.
NEW_TITLE = "The listing cursor skips the last page"
EVIDENCE_TITLE = "The export dialog drops unicode"
ABOUT = "github.com/nickderobertis/some-service"
DRAFT_BODY = (
    "## What happened\nA reader missed results.\n\n## Where\n`src/cursor.py`.\n\n"
    "## Why it is out of scope\nThe run was about something else.\n\n"
    "## Evidence\nSeen in the run's own log.\n"
)

#: The ticket already on the destination, the earlier run that filed it, and its comments.
EXISTING = "export-dialog-drops-unicode"
EXISTING_ITEM = "export-older"
EARLIER = "earlier-run"


def _stored_comment(identifier: str, at: str, body: str) -> str:
    """One comment as the store writes it into a local-md item."""
    return (
        f'\n<!-- onetaskgraph:comment id="{identifier}" author="follow-up" '
        f'created_at="{at}" updated_at="{at}" -->\n### follow-up — {at}\n\n{body}\n\n'
        "<!-- /onetaskgraph:comment -->\n"
    )


EXISTING_COMMENTS = (
    "\n## Comments\n"
    + _stored_comment(
        "20261001T120000Z-000001",
        "2026-10-01T12:00:00Z",
        f"Additional evidence from follow-up run `{EARLIER}`.\n\n"
        "Host build-host: café exported as caf.\n\n"
        f'<!-- orchestrator:follow-up-comment run="{EARLIER}" root_cause="{EXISTING}" -->',
    )
    + _stored_comment(
        "20261001T130000Z-000002",
        "2026-10-01T13:00:00Z",
        f"Answering your comment on follow-up run `{EARLIER}`'s ticket.\n\n"
        "The ticket stands as written.\n\n"
        f'<!-- orchestrator:follow-up-comment run="{EARLIER}" root_cause="{EXISTING}" '
        'kind="reply" answers="20261001T125500Z-000003" verdict="confirms" -->',
    )
)

#: The markers keying each scripted turn, which the composed tasks carry.
COMMIT_MARKER = "Record the measurement in MEASURED.md and commit it."
VERIFY_MARKER = "Verify the follow-up drafts and inputs of batch"
FEEDBACK_MARKER = "Answer the people's comments on batch"

#: A persona no role claims, so its node fails in config validation with no turn.
UNCLAIMED_PERSONA = "no-role-claims-this-name"

#: What the success hook prints once it launched the follow-up run.
LAUNCHED = re.compile(r"follow-ups are being verified in run (\S+); watch it with: just watch")

#: The engine's refusal of the configured review sink, which the settle step records.
SINK = ["onepipeline", "human-items", "raise", "--phase", "follow-up-review"]


class Repository(NamedTuple):
    """One scratch repository a node lands a change on, and its delivery budget."""

    node: str
    identity: Identity
    threshold: int


class Verified(NamedTuple):
    """One case, read back once its follow-up run settled."""

    run: str
    follow_up: str
    store: follow_up_store.Store
    environment: dict[str, str]
    seeded: str
    ended_log: str
    landings: dict[str, str]
    over: Repository
    within: Repository


def _environment(
    root: Path, home: Path, oneharness_bin: str
) -> tuple[dict[str, str], follow_up_store.Store]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in INHERITED and not name.startswith(INHERITED_PREFIXES)
    }
    store = follow_up_store.isolated(root)
    environment = store.over(environment)
    environment["CLAUDE_CODE_SESSION_ID"] = SESSION
    environment["ONEVCS_HOME"] = str(home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    return environment, store


def _just(
    environment: Mapping[str, str], *arguments: str, seconds: int = 300, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own recipes
        ["just", *arguments],  # noqa: S607 - `just` from the search path, as a manager runs it
        cwd=REPO_ROOT,
        env=dict(environment),
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _budget(threshold: int) -> str:
    """This host's `cycle-time` delivery budget at ``threshold`` seconds."""
    return yaml.safe_dump(
        {
            "schema_version": 1,
            "budgets": [
                {
                    "id": "cycle-time",
                    "labels": ["onepipeline"],
                    "measure": "reported",
                    "command": [str(REPO_ROOT / "scripts" / "budget-cycle-time.sh")],
                    "unit": "seconds",
                    "direction": "max",
                    "threshold": threshold,
                }
            ],
        }
    )


def _repositories(root: Path) -> tuple[Repository, Repository, Path]:
    """Two scratch repositories in one `onevcs` registry, each carrying its budget."""
    # The repository the drafts are about is registered public: the store classifies a draft
    # by its repository, and one the registry cannot classify is private, which a public
    # batch project refuses.
    over = seeded(
        root / "over", publication="over-publication", execution="over-execution", public=(ABOUT,)
    )
    within = seeded(root / "within", publication="within-publication", execution="within-execution")
    for identity, threshold in ((over, 0), (within, 1_000_000)):
        (identity.publication / "budgets.yaml").write_text(_budget(threshold), encoding="utf-8")
        git("add", "budgets.yaml", cwd=identity.publication)
        git(
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=ai-orchestrator-test",
            "commit",
            "-qm",
            "chore: budget",
            cwd=identity.publication,
        )
        git("push", "-q", "origin", "main", cwd=identity.publication)
        git("pull", "-q", "origin", "main", cwd=identity.execution)
    registered = subprocess.run(  # noqa: S603 - the pinned onevcs, into one registry
        [str(REPO_ROOT / ".venv/bin/onevcs"), "register", str(within.publication)],
        env={**os.environ, "ONEVCS_HOME": str(over.home)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert registered.returncode == 0, registered.stdout + registered.stderr
    registered = subprocess.run(  # noqa: S603 - the pinned onevcs, into one registry
        [str(REPO_ROOT / ".venv/bin/onevcs"), "register", str(within.execution)],
        env={**os.environ, "ONEVCS_HOME": str(over.home)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert registered.returncode == 0, registered.stdout + registered.stderr
    # Both scratch repositories are public, as a public repository's worker drafts from.
    # onefollowup records the drafting worktree in the draft, and a worktree of a private
    # repository carries that repository's name, which the store refuses on the drafts
    # source this host declares no visibility for — the gap drafted upstream to onefollowup.
    public = root / "public.rules.yml"
    public.write_text(
        (root / "over" / "onevcs.rules.yml")
        .read_text(encoding="utf-8")
        .replace("visibility: private", "visibility: public"),
        encoding="utf-8",
    )
    applied = subprocess.run(  # noqa: S603 - the pinned onevcs, into the same registry
        [str(REPO_ROOT / ".venv/bin/onevcs"), "rules", "apply", "--base", str(public)],
        env={**os.environ, "ONEVCS_HOME": str(over.home)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    return (
        Repository("land-over", over, 0),
        Repository("land-within", within, 1_000_000),
        over.home,
    )


def _seed_destination(store: follow_up_store.Store) -> str:
    """The ticket an earlier run filed, as `followups` holds one today, and its file's text."""
    record = {
        "schema": 14,
        "root_cause": EXISTING,
        "repository": ABOUT,
        "created_by_run": EARLIER,
        "owning_runs": [EARLIER],
        "drafts": [f"drafts:{EARLIER}/drafts/noticed"],
        "basis": {ABOUT: "4b825dc642cb6eb9a060e54bf8d69288fbee4904"},
        "verified_at": "2026-10-01T11:30:00Z",
        "host": "build-host",
        "priority_estimate": "medium",
        "board_estimate": "medium",
        "frequency": "intermittent",
    }
    text = (
        '---\ntitle: "some-service: the export dialog drops unicode titles"\nstatus: backlog\n'
        f"priority: medium\nmetadata:\n  orchestrator.follow-up: {json.dumps(record)}\n---\n"
        "## Root cause\n\nThe export dialog drops unicode titles.\n" + EXISTING_COMMENTS
    )
    path = store.board / "tasks" / f"{EXISTING_ITEM}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def _draft_commands(scratch_marker: str, log: Path) -> list[list[str]]:
    """What the node's worker runs: commit its change, then draft once through each road."""
    justfile = shlex.quote(str(REPO_ROOT / "justfile"))
    body, logged = shlex.quote(DRAFT_BODY), shlex.quote(str(log))
    return [
        [
            "sh",
            "-c",
            "[ -f MEASURED.md ] || { echo measured > MEASURED.md && git add MEASURED.md "
            "&& git -c user.email=test@example.com -c user.name=ai-orchestrator-test "
            "commit -qm 'feat: record the measurement'; }",
        ],
        [
            "bash",
            "-c",
            f'[ "$ONEPIPELINE_NODE_ID" = land-over ] || exit 0; '
            f'sentinel="$ONEPIPELINE_NODE_SCRATCH_DIR/{scratch_marker}"; '
            '[ -e "$sentinel" ] && exit 0; '
            f'printf %s {body} | "$ORCHESTRATOR_FOLLOW_UP_DRAFT" '
            f"--title {shlex.quote(NEW_TITLE)} --repository {ABOUT} --path src/cursor.py "
            f"2>>{logged} && "
            f'printf %s {body} | just --justfile {justfile} follow-up "$ONEPIPELINE_RUN_ID" '
            f"--title {shlex.quote(EVIDENCE_TITLE)} --repository {ABOUT} 2>>{logged} "
            '&& touch "$sentinel"',
        ],
    ]


def _plan(root: Path, run: str, repositories: tuple[Repository, ...], failing: bool) -> Path:
    tasks: list[dict[str, object]] = [
        {
            "id": repository.node,
            "title": f"feat: {repository.node}",
            "repo": str(repository.identity.publication),
            "execution_checkout": repository.identity.execution.name,
            "persona": "engineer",
            "task": (
                f"## What\n\n{COMMIT_MARKER}\n\n## Why\n\nIts follow-ups are the subject.\n\n"
                "## Acceptance criteria\n\n- MEASURED.md is committed.\n"
            ),
        }
        for repository in repositories
    ]
    if failing:
        tasks.append(
            {
                "id": "breaks",
                "persona": UNCLAIMED_PERSONA,
                "task": "Report.\n\n## Acceptance criteria\n- Reported.",
            }
        )
    plan = root / f"{run}.plan.json"
    plan.write_text(
        json.dumps(
            {"schema_version": 3, "goal": {"text": "Land two changes"}, "name": run, "tasks": tasks}
        ),
        encoding="utf-8",
    )
    return plan


def _scripted(root: Path, environment: dict[str, str], batch: str) -> None:
    """Key the doubled models' turns to the commands each would run."""
    worker = root / "worker-commands.json"
    commands = _draft_commands("drafted", root / "drafting.log")
    worker.write_text(json.dumps({COMMIT_MARKER: commands}), encoding="utf-8")
    environment[RUN_ON_MARKER_ENV] = str(worker)
    agent = root / "agent-commands.json"
    python = sys.executable
    agent.write_text(
        json.dumps(
            {
                VERIFY_MARKER: [
                    [python, str(AGENT), "verify", str(REPO_ROOT), batch, NEW_TITLE]
                    + [EVIDENCE_TITLE, f"{follow_up_store.DESTINATION}:{EXISTING_ITEM}", EXISTING]
                ],
                FEEDBACK_MARKER: [[python, str(AGENT), "feedback", str(REPO_ROOT), batch]],
            }
        ),
        encoding="utf-8",
    )
    environment["FAKE_CODEX_RUN_ON_MARKER"] = str(agent)
    environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(root / "agent-ran.jsonl")


def _settled(environment: Mapping[str, str], run: str) -> None:
    watched = _just(
        environment, "watch", run, "--until", "settled", "--timeout", "600", seconds=660
    )
    assert watched.returncode == 0, watched.stdout + watched.stderr


def _landings(environment: Mapping[str, str], run: str) -> dict[str, str]:
    read = _just(environment, "telemetry", run, "--changes", "--json", seconds=60)
    assert read.returncode == 0, read.stderr
    changes = json.loads(read.stdout.strip().splitlines()[-1])["changes"]
    return {str(change["node"]): str(change["landing"]) for change in changes if change["landing"]}


def _verified(root: Path, oneharness_bin: str, failing: bool) -> Iterator[Verified]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    over, within, home = _repositories(root)
    environment, store = _environment(root, home, oneharness_bin)
    seeded_text = _seed_destination(store)
    run = f"follow-ups-{'failed' if failing else 'done'}-{os.getpid()}"
    _scripted(root, environment, run)
    follow_up = ""
    try:
        launched = _just(
            environment,
            "orchestrate",
            project_from_plan(_plan(root, run, (over, within), failing), run),
            "--dag-graph",
            "off",
            seconds=900,
        )
        hook = "failure" if failing else "success"
        log_path = root / "runs" / run / "hooks" / f"{hook}.log"
        log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
        if failing:
            assert "no follow-up run was launched" in log, launched.stderr + log
            assert not (root / "runs" / f"{run}-follow-ups").exists()
            by_hand = _just(environment, "follow-ups", run, seconds=300)
            assert by_hand.returncode == 0, by_hand.stdout + by_hand.stderr
            follow_up = by_hand.stdout.split("follow-up run: ", 1)[1].split(";")[0]
        else:
            assert launched.returncode == 0, launched.stdout + launched.stderr
            matched = LAUNCHED.search(log)
            assert matched is not None, log
            follow_up = matched.group(1)
        assert follow_up == f"{run}-follow-ups", log
        assert (root / "runs" / follow_up / "launch.json").is_file(), (
            sorted(path.name for path in (root / "runs").iterdir()),
            log,
        )
        _settled(environment, follow_up)
        yield Verified(
            run,
            follow_up,
            store,
            environment,
            seeded_text,
            log,
            _landings(environment, run),
            over,
            within,
        )
    finally:
        for stopped in (run, follow_up):
            if stopped and (root / "runs" / stopped).exists():
                _just(environment, "stop", stopped, seconds=60)


@pytest.fixture(scope="module", params=[False, True], ids=["success-hook", "by-hand"])
def verified(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[Verified]:
    yield from _verified(tmp_path_factory.mktemp("follow-ups"), oneharness_bin, request.param)


def _state(case: Verified) -> dict[str, object]:
    path = case.store.work / "state" / f"{case.run}.json"
    state: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return state


def _results(case: Verified, run: str) -> dict[str, str]:
    result = json.loads(
        (Path(case.environment["ONEPIPELINE_RUNS_DIR"]) / run / "result.json").read_text()
    )
    return {str(node["id"]): str(node["status"]) for node in result["nodes"]}


@pytest.mark.xdist_group("follow-ups")
def test_every_step_of_the_follow_up_plan_settles_done(verified: Verified) -> None:
    assert _results(verified, verified.follow_up) == dict.fromkeys(
        ("prepare", "verify", "check", "publish", "settle"), "done"
    )


@pytest.mark.xdist_group("follow-ups")
def test_the_batch_was_prepared_with_both_drafts_and_exactly_the_one_overrun(
    verified: Verified,
) -> None:
    inputs = _state(verified)["inputs"]
    assert isinstance(inputs, list)
    drafts = sorted(str(entry["title"]) for entry in inputs if entry["source"] == "draft")
    assert drafts == sorted([NEW_TITLE, EVIDENCE_TITLE]), inputs

    overruns = [entry for entry in inputs if entry["source"] == "budget-overrun"]
    assert len(overruns) == 1, inputs
    detail = json.loads(overruns[0]["body"])
    assert detail["node"] == verified.over.node
    assert detail["landing"] == verified.landings[verified.over.node]
    assert detail["budget"] == "cycle-time"
    assert verified.within.node in verified.landings
    assert not any(
        verified.within.node in json.dumps(entry) for entry in inputs if entry["source"] != "draft"
    )
    assert not [entry for entry in inputs if entry["source"] == "budget-unanswered"], inputs


@pytest.mark.xdist_group("follow-ups")
def test_every_draft_and_the_overrun_carry_one_disposition(verified: Verified) -> None:
    answers = json.loads(
        (verified.store.work / "dispositions" / f"{verified.run}.json").read_text("utf-8")
    )
    disposed = [entry["draft"] for entry in answers["dispositions"]]
    inputs = _state(verified)["inputs"]
    assert isinstance(inputs, list)
    assert sorted(disposed) == sorted(str(entry["id"]) for entry in inputs)
    assert len(disposed) == len(set(disposed)) == 3


@pytest.mark.xdist_group("follow-ups")
def test_one_new_ticket_lands_on_the_destination_in_the_current_record_format(
    verified: Verified,
) -> None:
    items = sorted((verified.store.board / "tasks").glob("*.md"))
    filed = [item for item in items if item.stem != EXISTING_ITEM]
    assert len(filed) == 1, items
    front = yaml.safe_load(filed[0].read_text("utf-8").split("---\n")[1])
    record = front["metadata"]["orchestrator.follow-up"]
    assert record["schema"] == 14
    assert record["created_by_run"] == verified.run
    assert front["status"] == "backlog"


@pytest.mark.xdist_group("follow-ups")
def test_the_existing_ticket_keeps_its_record_and_comments_and_gains_this_runs_evidence(
    verified: Verified,
) -> None:
    after = (verified.store.board / "tasks" / f"{EXISTING_ITEM}.md").read_text("utf-8")
    before_front, after_front = verified.seeded.split("\n---\n")[0], after.split("\n---\n")[0]
    assert after_front == before_front
    assert after.startswith(verified.seeded.rstrip("\n")), after
    added = after[len(verified.seeded.rstrip("\n")) :]
    marker = f'<!-- orchestrator:follow-up-comment run="{verified.run}" root_cause="{EXISTING}" -->'
    assert marker in added, added

    # onefollowup's own validator accepts the record as it stands on the destination.
    tickets = verified.store.work / "tasks" / verified.run / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    held = tickets / f"{EXISTING}.md"
    held.write_text(after.split("\n## Comments\n")[0] + "\n", encoding="utf-8")
    try:
        validated = subprocess.run(  # noqa: S603 - the pinned onefollowup
            [str(REPO_ROOT / ".venv/bin/onefollowup"), "ticket", "validate"]
            + ["--batch", verified.run, EXISTING],
            cwd=REPO_ROOT,
            env=verified.environment,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        held.unlink()
    answer = json.loads(validated.stdout)
    assert not [p for p in answer.get("problems", []) if "schema" in str(p)], answer


@pytest.mark.xdist_group("follow-ups")
def test_settle_runs_the_configured_review_sink_and_the_engine_refuses_it_for_its_context(
    verified: Verified,
) -> None:
    """The known upstream gap: no `follow-up-review` item is raised until it is fixed."""
    log = (
        Path(verified.environment["ONEPIPELINE_RUNS_DIR"])
        / verified.follow_up
        / "commands"
        / "settle.log"
    ).read_text("utf-8")
    settled = json.loads(log[log.index("{") :].split("\n}\n")[0] + "\n}")
    assert settled["hook"]["argv"] == SINK
    assert settled["hook"]["exit"] == 2


def _replies(case: Verified, item: Path, comment: str) -> int:
    return item.read_text("utf-8").count(f'kind="reply" answers="{comment}"')


@pytest.mark.xdist_group("follow-ups")
def test_a_persons_comment_is_answered_once_and_a_second_pass_answers_nothing(
    verified: Verified,
) -> None:
    if "-failed-" in verified.run:
        pytest.skip("answered once, over the success-hook case's destination")
    items = [i for i in (verified.store.board / "tasks").glob("*.md") if i.stem != EXISTING_ITEM]
    item = items[0]
    commented = subprocess.run(  # noqa: S603 - a person writing through the store
        [str(REPO_ROOT / ".venv/bin/onetaskgraph"), "task", "comment", "add"]
        + [f"{follow_up_store.DESTINATION}:{item.stem}", "--author", "alice", "--json"],
        cwd=verified.store.directory,
        env=verified.environment,
        input="Why is this high and not medium?\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert commented.returncode == 0, commented.stderr
    comment = json.loads(commented.stdout)["id"]

    first = _just(verified.environment, "follow-ups-answer-comments", seconds=900)
    assert first.returncode == 0, first.stdout + first.stderr
    assert _replies(verified, item, comment) == 1
    text = item.read_text("utf-8")
    assert f'run="{verified.run}"' in text.split(f'answers="{comment}"')[0].rsplit("<!--", 1)[-1]

    second = _just(verified.environment, "follow-ups-answer-comments", seconds=900)
    assert second.returncode == 0, second.stdout + second.stderr
    assert _replies(verified, item, comment) == 1


def _draft_record(case: Verified, title: str) -> dict[str, object]:
    """The draft record onefollowup stored for ``title``, as its front matter holds it."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    text = (case.store.drafts / "tasks" / f"{slug}.md").read_text("utf-8")
    record: dict[str, object] = yaml.safe_load(text.split("---\n")[1])["metadata"][
        "orchestrator.follow-up-draft"
    ]
    return record


@pytest.mark.xdist_group("follow-ups")
def test_each_draft_names_who_drafted_it_and_where_its_turns_are_read(
    verified: Verified,
) -> None:
    """The worker's draft names its node and its transcript read; the manager's names neither."""
    worker = _draft_record(verified, NEW_TITLE)
    assert worker["run"] == verified.run
    assert worker["author"] == {"kind": "node", "node": verified.over.node, "member": None}
    assert worker["transcript"] == f"onepipeline transcript {verified.run} {verified.over.node}"

    manager = _draft_record(verified, EVIDENCE_TITLE)
    assert manager["run"] == verified.run
    assert manager["author"] == {"kind": "manager", "node": None, "member": None}
    assert manager["transcript"] is None


#: The draft the run's monitor writes, after the run, through the same drafting command.
MONITOR_TITLE = "The monitor saw a stale document"


def _monitor_draft(case: Verified, member: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the drafting command the monitor's prompt names
        [str(REPO_ROOT / "scripts" / "follow-up-draft.sh"), "--as", "monitor"]
        + ["--member", member, "--run", case.run, "--title", MONITOR_TITLE]
        + ["--repository", ABOUT],
        cwd=REPO_ROOT,
        env=case.environment,
        input=DRAFT_BODY,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


@pytest.mark.xdist_group("follow-ups")
def test_the_monitor_drafts_as_its_member_with_the_read_of_its_own_turns(
    verified: Verified,
) -> None:
    """A monitor's draft names its member and the stream read that shows the turns it saw."""
    if "-failed-" in verified.run:
        pytest.skip("drafted once, into the success-hook case's batch")

    drafted = _monitor_draft(verified, "monitor")

    assert drafted.returncode == 0, drafted.stdout + drafted.stderr
    path = Path(json.loads(drafted.stdout)["path"])
    front = yaml.safe_load(path.read_text("utf-8").split("---\n")[1])
    record = front["metadata"]["orchestrator.follow-up-draft"]
    assert record["run"] == verified.run
    assert record["author"] == {"kind": "monitor", "node": None, "member": "monitor"}
    # The stamp is a command line whose filter word is the member's JSON filter.
    transcript = record["transcript"]
    assert isinstance(transcript, str), record
    assert shlex.split(transcript) == [
        "onepipeline",
        "monitor",
        verified.run,
        "--filter",
        '{"include":[{"member":"monitor"}]}',
    ], transcript


def _drafted_from(
    directory: Path, environment: Mapping[str, str], run: str, title: str
) -> dict[str, str]:
    """A manager's draft of ``run`` written from ``directory``, and onefollowup's answer."""
    drafted = subprocess.run(  # noqa: S603 - the drafting command, from another directory
        [str(REPO_ROOT / "scripts" / "follow-up-draft.sh"), "--as", "manager", "--run", run]
        + ["--title", title, "--repository", ABOUT],
        cwd=directory,
        env=dict(environment),
        input=DRAFT_BODY,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr
    answer: dict[str, str] = json.loads(drafted.stdout)
    return answer


@pytest.mark.xdist_group("follow-ups")
def test_a_draft_from_another_directory_reads_this_checkouts_configuration_unless_named(
    verified: Verified, tmp_path: Path
) -> None:
    """Drafted from outside the checkout, a draft lands where this checkout's files route it.

    With no store named, the store is this checkout's `onetaskgraph.yaml` and the configuration
    its `onefollowup.yaml`, whatever directory the caller drafts from; a named configuration
    file routes the draft to the source it names instead.
    """
    if "-failed-" in verified.run:
        pytest.skip("drafted once, into the success-hook case's batch")
    environment = {
        name: value
        for name, value in verified.environment.items()
        if name != follow_up_variables.ONEFOLLOWUP_STORE
    }
    elsewhere = tmp_path / "another-worktree"
    elsewhere.mkdir()

    defaulted = _drafted_from(elsewhere, environment, verified.run, "Drafted from elsewhere")

    assert defaulted["draft"].startswith(f"{follow_up_variables.source()}:"), defaulted
    assert Path(defaulted["path"]).is_relative_to(verified.store.drafts), defaulted

    named = tmp_path / "onefollowup.yaml"
    named.write_text(
        yaml.safe_dump({"destination": "followups", "drafts_source": "follow-up-runs"}),
        encoding="utf-8",
    )
    environment[follow_up_variables.ONEFOLLOWUP_CONFIG] = str(named)

    overridden = _drafted_from(elsewhere, environment, verified.run, "Drafted under a named file")

    assert overridden["draft"].startswith("follow-up-runs:"), overridden
    assert Path(overridden["path"]).is_relative_to(verified.store.plans), overridden


@pytest.mark.xdist_group("follow-ups")
def test_a_node_draft_with_no_node_named_reads_the_whole_runs_turns(verified: Verified) -> None:
    """Drafted as a node where the engine named none, the draft points at the run's transcript."""
    if "-failed-" in verified.run:
        pytest.skip("drafted once, into the success-hook case's batch")
    environment = {
        name: value for name, value in verified.environment.items() if name != "ONEPIPELINE_NODE_ID"
    }
    drafted = subprocess.run(  # noqa: S603 - the drafting command a dispatch is handed
        [str(REPO_ROOT / "scripts" / "follow-up-draft.sh"), "--run", verified.run]
        + ["--title", "Drafted by an unnamed node", "--repository", ABOUT],
        cwd=REPO_ROOT,
        env=environment,
        input=DRAFT_BODY,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr

    record = _draft_record(verified, "Drafted by an unnamed node")
    # onefollowup records a node author whose node is not named as an unresolved one.
    assert record["author"] == {"kind": "node", "node": "unresolved", "member": None}
    assert record["transcript"] == f"onepipeline transcript {verified.run}"
