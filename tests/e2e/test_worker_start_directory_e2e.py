"""A dispatched lifecycle worker starts in the worktree its session cut, and the journal says so.

Where a dispatched worker starts had never been measured here. It was reported
fixed twice — once in a wheel the dispatch path never called, once in a crate
release that could not be adopted — so every task this repository dispatches
carried a paragraph telling the worker which directory to commit in, load-bearing
prose whose necessity nobody could confirm. A directory is a fact a launch already
records, so the question belongs to the suite rather than to a person reading a
report.

The journal is the source: `onevcs` appends `session-opened` naming the worktree it
cut for the node's branch, and `oneagentgraph` appends `member-started` naming the
directory it started that node's `worker` member in. A run whose harness took the
launcher's own directory instead — the defect these releases were about — records
the two disagreeing, and the launch directory is in the same journal to compare
against, because the dag-scope members really do belong there.

Everything between the recipe and the model is real: the real `just orchestrate`
and `just repos-apply` recipes, the real `scripts/onepipeline.sh`, the real
`onepipeline` driver, the real `onevcs` registry and its worktrees, the real
`oneagentgraph` graphs in `graphs/`, and the real onejudge conversation those
compose. `tests/e2e/fake_backend.py` stands in for the paid model alone, and it is
also the second witness: the `--cwd` it records is the directory `oneharness` was
told to serve the turn in, so the journal's claim is checked against the placement
itself rather than believed.

The identity is a scratch one — a bare origin and two clones of it, registered
against a scratch `ONEVCS_HOME` — because a test may not register or publish from
this host's own checkouts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

import pytest
from conftest import git
from fake_backend import JUDGE_CONFIG_NAME, PROMPT_LOG_ENV
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model, named to `oneagentgraph` as its harness.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"

#: The plan's `name`, which is the run id `onepipeline` mints from it.
RUN_NAME = "worker-start-directory"

#: The `graphs/node-scope.yaml` member a dispatched plan node runs as. The
#: dag-scope members (`orchestrator`, `check-in`) are the launch's own and belong
#: in the launch directory, which is what makes them the contrast below.
WORKER_MEMBER = "worker"

#: The dag-scope member that drives the run, and so is the one graph member whose
#: place really is the directory the planner launched from.
ORCHESTRATOR_MEMBER = "orchestrator"

#: `oneagentgraph` gives every member a scratch directory named after it and pins
#: that member's harness configs inside it, so the recorded `--config` is what says
#: which member a turn belongs to.
MEMBER_SCRATCH = f"/members/{WORKER_MEMBER}/"

#: The registered alias of the checkout a session clones from. `onevcs` takes an
#: alias here and refuses a path, so the plan names the alias.
EXECUTION_ALIAS = "execution"

#: A launching session the journey states rather than inherits: this suite runs
#: inside a dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-worker-start-directory"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, so a
#: journey that states one is not also carrying the enclosing dispatch's.
LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The scratch identity's policy: merged in the local checkout, verified by a gate
#: that is trivially green. The publication path is not this journey's subject — the
#: worker reports without changing anything, so nothing is ever published — but a
#: registered checkout that matched no rule fails `just repos-apply` outright.
RULES = """version: 2
trailer_prefix: Orchestrator-
rules:
  - match: {path: "*"}
    publication: local-direct
    approvals: none
    gate: {command: ["true"]}
default:
  publication: local-direct
  approvals: none
  gate: {command: ["true"]}
"""

#: The committer this journey's own seed commit carries. `tests/conftest.py` exports
#: one per test, and the launch fixture below is module-scoped, so it is set up
#: before that function-scoped fixture has run.
GIT_IDENTITY = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")


class Placement(TypedDict):
    """The one payload field both journal records this journey reads carry."""

    worktree: str


class JournalEvent(TypedDict):
    """One journal record, in the terms this journey reads it.

    `onepipeline` pins the whole record contract and every event carries more than
    this; these are the three fields the question needs, stated rather than
    restated from the engine's schema.
    """

    kind: str
    labels: dict[str, str]
    payload: Placement


class TurnRecord(TypedDict):
    """One harness turn, as `tests/e2e/fake_backend.py` recorded it being pinned."""

    config: str | None
    cwd: str | None


class Launched(NamedTuple):
    """One settled lifecycle run, and the two records that answer this question."""

    #: Every event the run appended, in order.
    journal: list[JournalEvent]
    #: Every turn the fake backend served, as it was pinned.
    turns: list[TurnRecord]


def _seed_identity(root: Path) -> tuple[Path, Path]:
    """A bare origin with one commit on `main`, and the two clones of it to register."""
    origin = root / "origin.git"
    seed = root / "seed"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("init", "-q", "-b", "main", str(seed))
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    publication = root / "publication"
    execution = root / EXECUTION_ALIAS
    git("clone", "-q", str(origin), str(publication))
    git("clone", "-q", str(origin), str(execution))
    return publication, execution


def _register(root: Path, home: Path, checkouts: tuple[Path, ...]) -> None:
    """Bring a scratch registry up to a scratch configuration, through the real recipe."""
    manifest = root / "onevcs.checkouts"
    manifest.write_text("".join(f"{path}\n" for path in checkouts), encoding="utf-8")
    rules = root / "onevcs.rules.yml"
    rules.write_text(RULES, encoding="utf-8")
    applied = subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules)],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert applied.returncode == 0, f"repos-apply failed:\n{applied.stdout}\n{applied.stderr}"


def _plan(root: Path, publication: Path) -> Path:
    """A one-node lifecycle plan against the scratch identity."""
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "Record where a dispatched lifecycle worker starts"},
                "name": RUN_NAME,
                "tasks": [
                    {
                        "id": "placement",
                        "repo": str(publication),
                        "execution_checkout": EXECUTION_ALIAS,
                        "persona": "engineer",
                        "task": (
                            "## What\nReport the directory you are in, changing nothing.\n\n"
                            "## Why\nThe journal's record of that directory is the subject; "
                            "the work itself is not.\n\n"
                            "## Acceptance criteria\n- The dispatch starts and reports.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


@pytest.fixture(scope="module")
def launched(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Launched]:
    """Launch one lifecycle node for real, and hand every question its settled run."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("worker-start-directory")
    home = root / "onevcs"
    home.mkdir()
    publication, execution = _seed_identity(root)
    _register(root, home, (publication, execution))

    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    # Every root this run writes under, so its registry, its worktrees, its ledger,
    # and its graph scratch are all this journey's and none of them the host's.
    environment["ONEVCS_HOME"] = str(home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(root / "state")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    prompt_log = root / "turns.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)

    launch = subprocess.run(
        ["just", "orchestrate", str(_plan(root, publication))],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert launch.returncode == 0, f"the launch did not settle:\n{launch.stdout}\n{launch.stderr}"
    journal = root / "runs" / RUN_NAME / "events.jsonl"
    assert journal.is_file(), f"the launch recorded no journal at {journal}"
    # `onepipeline` writes the journal and `fake_backend.py` writes the turn log, so
    # both schemas are stated above rather than validated here; the cast says which.
    yield Launched(
        journal=[
            cast(JournalEvent, json.loads(line))
            for line in journal.read_text(encoding="utf-8").splitlines()
        ],
        turns=[
            cast(TurnRecord, json.loads(line))
            for line in prompt_log.read_text(encoding="utf-8").splitlines()
        ],
    )


def _started(journal: list[JournalEvent], member: str) -> list[str]:
    """Every directory the journal records a `member` having been started in."""
    return [
        event["payload"]["worktree"]
        for event in journal
        if event.get("kind") == "member-started" and event.get("labels", {}).get("member") == member
    ]


def _sessions(journal: list[JournalEvent]) -> set[str]:
    """Every worktree `onevcs` recorded cutting for this run's branches."""
    return {
        event["payload"]["worktree"] for event in journal if event.get("kind") == "session-opened"
    }


@pytest.mark.xdist_group("worker-start-directory")
def test_the_journal_records_the_worker_starting_in_the_worktree_its_session_cut(
    launched: Launched,
) -> None:
    """The directory the dispatched member was started in is the node's own worktree.

    This is the measurement the prose in `AGENTS.md` and
    `docs/onejudge-integration.md` now states. The failing shape is specific: a
    dispatch whose harness took the launcher's directory records the launch
    directory here, or the `.` an unplaced member gets, and either one disagrees
    with the worktree `onevcs` cut for the branch in the very same journal.
    """
    started = _started(launched.journal, WORKER_MEMBER)
    assert started, "the journal records no dispatched worker at all"
    cut = _sessions(launched.journal)
    assert cut, "the journal records no lifecycle session"
    assert set(started) <= cut, (
        f"the worker was started in {sorted(set(started) - cut)}, which no session cut; "
        f"the sessions this run opened were {sorted(cut)}"
    )
    assert str(REPO_ROOT) not in started, (
        "the worker was started in the directory the run was launched from, not in its worktree"
    )


@pytest.mark.xdist_group("worker-start-directory")
def test_the_launch_directory_is_where_the_run_s_own_driver_belongs(launched: Launched) -> None:
    """The same journal puts the dag-scope orchestrator in the launch directory.

    Without this the assertion above is weaker than it reads: a journal that
    recorded one directory for everything would satisfy it whenever the launch
    happened to be under a worktree. The orchestrator drives the run rather than
    doing its work, so the directory it is started in is the one the planner
    launched from — and that it differs from the worker's is the whole placement.
    """
    driver = set(_started(launched.journal, ORCHESTRATOR_MEMBER))
    assert driver == {str(REPO_ROOT)}, (
        f"the run's driver was started in {sorted(driver)}, not in the launch directory {REPO_ROOT}"
    )
    assert not driver & set(_started(launched.journal, WORKER_MEMBER)), (
        "the driver and the dispatched worker were started in the same directory"
    )


@pytest.mark.xdist_group("worker-start-directory")
def test_the_worker_s_harness_was_told_to_serve_the_turn_in_that_directory(
    launched: Launched,
) -> None:
    """What the journal claims is what `oneharness` was actually handed.

    The journal is `oneagentgraph` describing its own intent, and an intent that
    never reached the harness is exactly how this was twice reported fixed while
    still broken. `--cwd` is where a turn is really served, so the agent turns of
    the `worker` member are the second witness — and they have to name the same
    directories, not merely some worktree.
    """
    agent_turns = [
        turn
        for turn in launched.turns
        if MEMBER_SCRATCH in (turn["config"] or "")
        and Path(turn["config"]).name != JUDGE_CONFIG_NAME
    ]
    assert agent_turns, "the fake backend served no worker agent turn"
    served = {turn["cwd"] for turn in agent_turns}
    assert served == set(_started(launched.journal, WORKER_MEMBER)), (
        f"the harness served the worker's turns in {sorted(served, key=str)}, while the "
        f"journal records it started in {sorted(set(_started(launched.journal, WORKER_MEMBER)))}"
    )
