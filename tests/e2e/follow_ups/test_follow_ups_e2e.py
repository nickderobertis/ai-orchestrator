"""This host's follow-up wiring over onefollowup, driven as a manager meets it.

Two journeys, and only this host's half of each: what onefollowup and onetaskgraph do with
a batch — verifying, filing, answering — is proven in their own repositories.

**Verifying a run's follow-ups.** A real `just orchestrate` run lands one change on each of
two scratch repositories registered with `onevcs` — one whose `cycle-time` delivery budget
the change exceeds, one whose budget it keeps within — and its node drafts three follow-ups:
two through `$ORCHESTRATOR_FOLLOW_UP_DRAFT`, one of them about a private repository, and one
through `just follow-up`. The run ends with
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
before the run with a ticket in the shape this host's own module wrote at schema 15:
onepipeline-ui#803's record, relabelled 14 with its `impact`, and its body, read from the
board (`tests/fixtures/follow-ups/onepipeline-ui-803.md`), and an earlier run's evidence
comment in the collapsed block form. The run's evidence is for that ticket's root cause, and
its agent files its new ticket in onefollowup's shipped shape, the severity values only in
the record's `impact`. **The paid model alone is doubled**: `tests/e2e/fake_backend.py`
for the main run's worker and judge, whose turn runs the commit and drafting commands a
model would, and `tests/e2e/fake_codex.py` for the follow-up agent, whose turn runs
`tests/e2e/follow_ups/scripted_agent.py` — the agent's choices, made with the real
onefollowup and plan-store commands.

**The review item.** `onefollowup.yaml` names the engine's `onepipeline human-items raise
--phase follow-up-review --context {context_file}` as the `batch_settled` hook, and settle
hands it the context file it writes, naming the launching manager session as the owner a
launch exports. The journey holds settle to running that configured hook and the engine to
raising one review item naming the batch and its ticket, in the item store the engine keeps
under the journey's own runs root.

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

#: The drafts the run's node writes, and the repositories they are about: two about a public
#: repository, and one about a private repository no registry declares public, which the
#: store records as private.
NEW_TITLE = "The listing cursor skips the last page"
EVIDENCE_TITLE = "The scroll-back journey must survive the first-screen handoff"
PRIVATE_TITLE = "The private exporter retries forever"
ABOUT = "github.com/nickderobertis/some-service"
PRIVATE_ABOUT = "github.com/nickderobertis/private-exporter"
DRAFT_BODY = (
    "## What happened\nA reader missed results.\n\n## Where\n`src/cursor.py`.\n\n"
    "## Why it is out of scope\nThe run was about something else.\n\n"
    "## Evidence\nSeen in the run's own log.\n"
)

#: The ticket already on the destination: onepipeline-ui#803 as the `followups` board holds
#: it, one of the eleven this host's own module wrote at schema 15, its record relabelled 14
#: with the `impact` the relabel wrote from its body lines.
BOARD_TICKET = REPO_ROOT / "tests" / "fixtures" / "follow-ups" / "onepipeline-ui-803.md"
EXISTING = "scroll-journey-survives-first-screen-handoff"
EXISTING_ITEM = "scroll-journey"
#: An earlier run whose evidence comment the ticket carries, in the block form the shipped
#: evidence comment opens with.
EARLIER = "earlier-run"
EARLIER_DRAFT = f"drafts:{EARLIER}/drafts/20261115T090000Z-the-scroll-back-journey-failed-again"


def _stored_comment(identifier: str, at: str, body: str) -> str:
    """One comment as the store writes it into a local-md item."""
    return (
        f'\n<!-- onetaskgraph:comment id="{identifier}" author="follow-up" '
        f'created_at="{at}" updated_at="{at}" -->\n### follow-up — {at}\n\n{body}\n\n'
        "<!-- /onetaskgraph:comment -->\n"
    )


EARLIER_COMMENT = (
    f"<details>\n<summary>Additional evidence from follow-up run {EARLIER}.</summary>\n\n"
    "- Host: build-host\n"
    f"- Basis: {'github.com/nickderobertis/some-service'} at "
    "929baf62ea466e965fec94aa096332ede41a0b67\n"
    f"- Draft: {EARLIER_DRAFT}; author node; node scroll-back; transcript: "
    f"onepipeline transcript {EARLIER} scroll-back\n"
    "- Witness: github.com/nickderobertis/some-service "
    "apps/dag-ui-e2e/read-latency/wire-and-errors.spec.ts:137 at "
    "929baf62ea466e965fec94aa096332ede41a0b67\n\n</details>\n\n"
    "The scroll-back journey failed again on a busy host; an isolated rerun passed.\n\n"
    f'<!-- orchestrator:follow-up-comment run="{EARLIER}" root_cause="{EXISTING}" -->'
)
EXISTING_COMMENTS = "\n## Comments\n" + _stored_comment(
    "20261115T100000Z-000001", "2026-11-15T10:00:00Z", EARLIER_COMMENT
)

#: The estimate line onefollowup writes and rewrites in place, found by its label.
ESTIMATE_LINE = re.compile(r"^- Priority estimate: .*$", re.MULTILINE)

#: The markers keying each scripted turn, which the composed tasks carry.
COMMIT_MARKER = "Record the measurement in MEASURED.md and commit it."
VERIFY_MARKER = "Verify the follow-up drafts and inputs of batch"
FEEDBACK_MARKER = "Answer the people's comments on batch"

#: A persona no role claims, so its node fails in config validation with no turn.
UNCLAIMED_PERSONA = "no-role-claims-this-name"

#: What the success hook prints once it launched the follow-up run.
LAUNCHED = re.compile(r"follow-ups are being verified in run (\S+); watch it with: just watch")

#: The configured review sink, as the settle step records running it.
SINK = [
    "onepipeline",
    "human-items",
    "raise",
    "--phase",
    "follow-up-review",
    "--context",
    "{context_file}",
]


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
    # repository carries that repository's name, which would make every draft written there
    # private; this journey's private draft is private for what it is about instead.
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
    """The ticket an earlier run filed, as `followups` holds it, and its file's text: its
    title, record and body from the board, at the priority its record last estimated, and the
    earlier run's evidence comment."""
    _, front, body = BOARD_TICKET.read_text(encoding="utf-8").split("---\n", 2)
    held = yaml.safe_load(front)
    record = held["metadata"]["orchestrator.follow-up"]
    text = (
        f"---\ntitle: {json.dumps(held['title'])}\nstatus: backlog\n"
        f"priority: {record['board_estimate']}\nmetadata:\n"
        f"  orchestrator.follow-up: {json.dumps(record, ensure_ascii=False)}\n---\n"
        + body.rstrip("\n")
        + "\n"
        + EXISTING_COMMENTS
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
            f'printf %s {body} | "$ORCHESTRATOR_FOLLOW_UP_DRAFT" '
            f"--title {shlex.quote(PRIVATE_TITLE)} --repository {PRIVATE_ABOUT} "
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
    """Each node's status, as `just results` folds the detached run's journal."""
    read = _just(case.environment, "results", run, "--json", seconds=60)
    assert read.returncode == 0, read.stdout + read.stderr
    result = json.loads(read.stdout)
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
    assert drafts == sorted([NEW_TITLE, EVIDENCE_TITLE, PRIVATE_TITLE]), inputs

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
    assert len(disposed) == len(set(disposed)) == 4


def _parts(text: str) -> tuple[dict[str, object], str, str]:
    """A local-md item's front matter, its body, and its comments section."""
    _, front, rest = text.split("---\n", 2)
    body, _, comments = rest.partition("\n## Comments\n")
    held: dict[str, object] = yaml.safe_load(front)
    return held, body, comments


def _record(front: Mapping[str, object]) -> dict[str, object]:
    """The ticket record a local-md item's front matter holds."""
    metadata = front["metadata"]
    assert isinstance(metadata, dict), front
    record = metadata["orchestrator.follow-up"]
    assert isinstance(record, dict), front
    return record


def _sections(body: str) -> dict[str, str]:
    """Each level-2 section of ``body`` by heading, its text trimmed."""
    return {
        part.split("\n", 1)[0]: part.split("\n", 1)[1].strip()
        for part in re.split(r"^## ", body, flags=re.MULTILINE)[1:]
    }


def _record_paths() -> list[tuple[str, list[str]]]:
    """The `orchestrator.follow-up` paths the checkout's `followups` source shows on fields."""
    document = yaml.safe_load((REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8"))
    return [
        (str(entry["field"]), [str(key) for key in entry["path"]])
        for entry in document["sources"]["followups"]["config"]["metadata_fields"]
    ]


def _filed(case: Verified) -> Path:
    (filed,) = [
        item for item in (case.store.board / "tasks").glob("*.md") if item.stem != EXISTING_ITEM
    ]
    return filed


def _collapsed(summary: str) -> str:
    """The opening of one collapsed block, as the shipped templates write one."""
    return f"<details>\n<summary>{summary}</summary>\n\n"


@pytest.mark.xdist_group("follow-ups")
def test_one_new_ticket_lands_on_the_destination_in_the_current_record_format(
    verified: Verified,
) -> None:
    front, _, _ = _parts(_filed(verified).read_text("utf-8"))
    record = _record(front)
    assert record["schema"] == 14
    assert record["created_by_run"] == verified.run
    assert front["status"] == "backlog"
    for field, path in _record_paths():
        value: object = record
        for key in path:
            assert isinstance(value, dict), (field, record)
            value = value.get(key)
        assert value, (field, path, record)


@pytest.mark.xdist_group("follow-ups")
def test_the_new_ticket_carries_the_shipped_shape(verified: Verified) -> None:
    """No ownership section, evidence of prose alone, `## Impact` ending in its two collapsed
    blocks, and a one-unit fix opening with its collapsed locations, then its unit."""
    _, body, _ = _parts(_filed(verified).read_text("utf-8"))
    sections = _sections(body)
    assert "Owning runs" not in sections and "Owning batches" not in sections, list(sections)

    evidence = sections["Evidence"]
    assert "\n" not in evidence and not evidence.startswith(("- ", "Host", "Basis")), evidence
    for held in ("drafts:", scripted_agent_basis(), "onepipeline transcript"):
        assert held not in evidence, (held, evidence)

    impact = sections["Impact"]
    cost = impact.index(_collapsed("Cost of the workaround"))
    calculation = impact.index(_collapsed("Priority calculation"))
    assert cost < calculation, impact
    assert impact.endswith("</details>"), impact
    assert impact[cost:calculation].rstrip().endswith("</details>"), impact
    assert ESTIMATE_LINE.search(impact[calculation:]), impact

    fix = sections["Suggested fix"]
    assert fix.startswith(_collapsed("Contributing locations")), fix
    after = fix.split("</details>", 1)[1]
    assert after.lstrip("\n").startswith("### "), fix
    assert len(re.findall(r"^### ", after, flags=re.MULTILINE)) == 1, fix


def scripted_agent_basis() -> str:
    """The basis commit the scripted agent records."""
    held = (AGENT.read_text(encoding="utf-8").split('BASIS = "', 1)[1]).split('"', 1)[0]
    return held


def _expected_priority(case: Verified, record: Mapping[str, object], occurrences: int) -> str:
    """The priority the configured rubric gives ``record``'s `impact` and frequency at
    ``occurrences``: its severity with the workaround's level, raised one level, capped, when
    it fires consistently or reaches the raise threshold."""
    shown = subprocess.run(  # noqa: S603 - the pinned onefollowup, reading its configuration
        [str(REPO_ROOT / ".venv/bin/onefollowup"), "config", "show"],
        cwd=REPO_ROOT,
        env=case.environment | {"ONEFOLLOWUP_HOOKS__OWNER__SESSION": SESSION},
        text=True,
        capture_output=True,
        check=True,
    )
    config = json.loads(shown.stdout)["config"]
    order = ["urgent", "high", "medium", "low"]
    impact = record["impact"]
    assert isinstance(impact, dict), record
    (level,) = [
        held
        for held in config["rubric"]["levels"]
        if held["name"] == impact["Severity with the workaround"]
    ]
    at = order.index(level["priority"])
    cap = order.index(config["estimate"]["raise_cap"])
    if (
        record.get("frequency") == "consistent" or occurrences >= config["estimate"]["raise_at"]
    ) and at > cap:
        at = max(at - 1, cap)
    return order[at]


@pytest.mark.xdist_group("follow-ups")
def test_the_existing_ticket_keeps_its_record_body_and_comment_and_is_re_estimated(
    verified: Verified,
) -> None:
    """The seeded ticket in the schema-15 shape, after this run's evidence and re-estimate:
    every part but the estimate line as it was, its earlier evidence comment untouched and
    counted, and its priority the rubric's for its record's `impact` and frequency."""
    after = (verified.store.board / "tasks" / f"{EXISTING_ITEM}.md").read_text("utf-8")
    before_front, before_body, before_comments = _parts(verified.seeded)
    after_front, after_body, after_comments = _parts(after)

    unchanged = ESTIMATE_LINE.sub("<estimate>", before_body)
    assert ESTIMATE_LINE.sub("<estimate>", after_body) == unchanged
    assert after_comments.startswith(before_comments), after_comments

    estimated = ("priority_estimate", "board_estimate", "priority")
    record, after_record = _record(before_front), _record(after_front)
    assert {k: v for k, v in after_record.items() if k not in estimated} == {
        k: v for k, v in record.items() if k not in estimated
    }
    assert {k: v for k, v in after_front.items() if k not in ("metadata", "priority")} == {
        k: v for k, v in before_front.items() if k not in ("metadata", "priority")
    }

    # Its own evidence, the earlier run's comment, and this run's: three occurrences, the
    # earlier comment counted as its marker's run's.
    (line,) = ESTIMATE_LINE.findall(after_body)
    assert "; 3 occurrences;" in line, line
    expected = _expected_priority(verified, record, 3)
    assert after_front["priority"] == expected, (after_front["priority"], expected, line)
    assert line.startswith(f"- Priority estimate: {expected} "), line
    assert before_front["priority"] != expected, before_front


@pytest.mark.xdist_group("follow-ups")
def test_the_runs_evidence_comment_opens_with_its_collapsed_sources_and_ends_with_its_marker(
    verified: Verified,
) -> None:
    after = (verified.store.board / "tasks" / f"{EXISTING_ITEM}.md").read_text("utf-8")
    added = _parts(after)[2][len(_parts(verified.seeded)[2]) :]
    body = added.split(" -->\n", 1)[1].split("\n<!-- /onetaskgraph:comment -->", 1)[0]
    body = body.split("\n\n", 1)[1].strip()
    assert body.startswith(_collapsed(f"Additional evidence from follow-up run {verified.run}.")), (
        body
    )
    marker = f'<!-- orchestrator:follow-up-comment run="{verified.run}" root_cause="{EXISTING}" -->'
    assert body.splitlines()[-1] == marker, body


@pytest.mark.xdist_group("follow-ups")
def test_onefollowup_still_reads_the_existing_ticket_s_record(verified: Verified) -> None:
    """onefollowup's own validator accepts the record as it stands on the destination."""
    after = (verified.store.board / "tasks" / f"{EXISTING_ITEM}.md").read_text("utf-8")
    tickets = verified.store.work / "tasks" / verified.run / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    held = tickets / f"{EXISTING}.md"
    held.write_text(after.split("\n## Comments\n")[0] + "\n", encoding="utf-8")
    try:
        validated = subprocess.run(  # noqa: S603 - the pinned onefollowup
            [str(REPO_ROOT / ".venv/bin/onefollowup"), "ticket", "validate"]
            + ["--batch", verified.run, EXISTING],
            cwd=REPO_ROOT,
            # The owner a launch exports, which this read outside the run states for itself.
            env=verified.environment | {"ONEFOLLOWUP_HOOKS__OWNER__SESSION": SESSION},
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        held.unlink()
    assert validated.stdout, validated.stderr
    answer = json.loads(validated.stdout)
    assert not [p for p in answer.get("problems", []) if "schema" in str(p)], answer


@pytest.mark.xdist_group("follow-ups")
def test_settle_runs_the_configured_review_sink_and_the_engine_raises_the_batchs_review(
    verified: Verified,
) -> None:
    """`onefollowup.yaml`'s sink, unmodified, raises one `follow-up-review` item for the batch.

    Read from the item store the engine keeps under this journey's own runs root, through
    the engine's own listing.
    """
    log = (
        Path(verified.environment["ONEPIPELINE_RUNS_DIR"])
        / verified.follow_up
        / "commands"
        / "settle.log"
    ).read_text("utf-8")
    settled = json.loads(log[log.index("{") :].split("\n}\n")[0] + "\n}")
    assert settled["hook"]["argv"] == SINK
    assert settled["hook"]["exit"] == 0, log

    listed = _just(verified.environment, "human-items", "list", "--json", "--all", "--full")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    items = [entry["item"] for entry in json.loads(listed.stdout)]
    reviews = [item for item in items if "follow-up tickets from batch" in json.dumps(item)]
    assert len(reviews) == 1, items
    (review,) = reviews
    assert review["kind"] == "task", review
    assert review["owner"]["session"] == SESSION, review
    assert review["title"] == f"Accept or defer the follow-up tickets from batch {verified.run}"
    held = json.dumps(review)
    assert f"follow-up batch {verified.run} filed or updated" in held, review
    assert _filed(verified).stem in held, review


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


def _stored_draft(case: Verified, title: str) -> dict[str, object]:
    """The draft recorded for ``title``, as the installed plan store reads it back."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    shown = subprocess.run(  # noqa: S603 - the installed plan store, as anyone reads a draft
        [str(REPO_ROOT / ".venv/bin/onetaskgraph"), "--no-interactive", "task", "show"]
        + [f"{follow_up_variables.source()}:{slug}", "--json"],
        cwd=case.store.directory,
        env=case.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    item: dict[str, object] = json.loads(shown.stdout)["items"][0]["item"]
    return item


@pytest.mark.xdist_group("follow-ups")
def test_a_draft_about_private_work_is_recorded_private_beside_the_public_ones(
    verified: Verified,
) -> None:
    """Both drafted through `$ORCHESTRATOR_FOLLOW_UP_DRAFT`, both in the batch's project.

    The store records a draft about a repository no registry declares public as private,
    which it does only on a source declared private, and leaves one about public work public.
    """
    private = _stored_draft(verified, PRIVATE_TITLE)
    public = _stored_draft(verified, NEW_TITLE)

    assert private["project"] == public["project"] == verified.run, (private, public)
    assert private["repositories"] == [PRIVATE_ABOUT], private
    assert private["classification"] == "private", private
    assert public["repositories"] == [ABOUT], public
    assert public.get("classification", "public") == "public", public


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


@pytest.mark.xdist_group("follow-ups")
def test_the_verifying_agent_is_pointed_at_the_task_prepare_wrote(verified: Verified) -> None:
    """The verify node's task names the task file `onefollowup prepare` says it rendered.

    The plan names that file before prepare runs, from onefollowup's working-file layout, so
    this holds the pointer to prepare's own answer; settle has retired the file itself.
    """
    follow_up = Path(verified.environment["ONEPIPELINE_RUNS_DIR"]) / verified.follow_up
    log = (follow_up / "commands" / "prepare.log").read_text("utf-8")
    prepared = json.loads(log[log.index("{") :])
    task_template = Path(prepared["task_template"])

    plan = json.loads((follow_up / "plan.json").read_text("utf-8"))
    (verify,) = [task for task in plan["tasks"] if task["id"] == "verify"]
    assert str(task_template) in verify["task"], verify["task"]
