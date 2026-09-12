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
from fake_backend import (
    JUDGE_CONFIG_NAME,
    JUDGE_SEND_BACK_ENV,
    PROMPT_LOG_ENV,
    RUN_ON_MARKER_ENV,
)
from project_fixtures import project_from_plan
from scratch_identity import seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model, named to `oneagentgraph` as its harness.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"

#: The plan's `name`, which is the run id `onepipeline` mints from it.
RUN_NAME = "worker-start-directory"

#: The `graphs/node-scope.yaml` member a dispatched plan node runs as. The
#: dag-scope members (`monitor`, `check-in`) are the launch's own and belong
#: in the launch directory, which is what makes them the contrast below.
WORKER_MEMBER = "worker"

#: The dag-scope member that watches the run, and so is the one graph member whose
#: place really is the directory the planner launched from.
MONITOR_MEMBER = "monitor"

#: `oneagentgraph` gives every member a scratch directory named after it and pins
#: that member's harness configs inside it, so the recorded `--config` is what says
#: which member a turn belongs to.
MEMBER_SCRATCH = f"/members/{WORKER_MEMBER}/"

#: The registered alias of the checkout a session clones from. `onevcs` takes an
#: alias here and refuses a path, so the plan names the alias.
EXECUTION_ALIAS = "execution"

#: The phrase the task carries that tells the stand-in this is the turn that commits.
#:
#: The worker commits because the adopted engine fails a lifecycle dispatch whose branch
#: is level with its base and which declared no `expects_no_diff` — `empty-branch`,
#: https://github.com/nickderobertis/onepipeline/pull/229 — and that declaration
#: settles a node *without* dispatching it, which is the opposite of what a journey
#: about where a dispatch starts needs. So the stand-in does what a real worker asked
#: for a change does: writes one file where it stands and commits it there. The commit
#: is the evidence's second half, too — it is made in the worker's own working
#: directory, so the branch it lands on is the branch of the worktree the worker started
#: in.
COMMIT_MARKER = "Leave one file recording where you stood, and commit it."

#: What the stand-in runs when its task carries that marker: one file, one commit, in the
#: directory `oneharness` served the turn in. Idempotent because a supervisor may send
#: the dispatch back for another turn, and a second turn must not fail on a file the
#: first one already committed. The committer is stated because the launch is made from
#: a module-scoped fixture, before `tests/conftest.py` has exported one for the test.
COMMIT_COMMANDS = [
    [
        "sh",
        "-c",
        "[ -f PLACEMENT.md ] || { pwd > PLACEMENT.md && git add PLACEMENT.md "
        "&& git -c user.email=test@example.com -c user.name=ai-orchestrator-test "
        "commit -qm 'feat: record where the worker stood'; }",
    ]
]

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
                            f"## What\n{COMMIT_MARKER}\n\n"
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
    identity = seeded(root, execution=EXECUTION_ALIAS)

    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    # Every root this run writes under, so its registry, its worktrees, its ledger,
    # and its graph scratch are all this journey's and none of them the host's.
    environment["ONEVCS_HOME"] = str(identity.home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(root / "state")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    prompt_log = root / "turns.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)
    keyed = root / "commands.json"
    keyed.write_text(json.dumps({COMMIT_MARKER: COMMIT_COMMANDS}), encoding="utf-8")
    environment[RUN_ON_MARKER_ENV] = str(keyed)
    # The stand-in's supervisor sends the worker back once, so the conversation has a
    # turn that opens on the supervisor's own words — which is the turn the provenance
    # journey below reads the `supervisor` stamp off. The commit above is idempotent,
    # so a second worker turn changes nothing about where it stood.
    # llmlint: ignore[expensive_tests_stay_behind_their_own_edge] a turn more, not a launch more
    environment[JUDGE_SEND_BACK_ENV] = "1"

    launch = subprocess.run(
        [
            "just",
            "orchestrate",
            project_from_plan(_plan(root, identity.publication)),
        ],
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
    # Which is why this question belonged to the journal at all — the module docstring
    # above names both records and why they are the source.
    # llmlint: ignore[tests_mirror_real_usage] No view reports a member's start directory.
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
def test_the_launch_directory_is_where_the_runs_own_monitor_belongs(launched: Launched) -> None:
    """The same journal puts the dag-scope monitor in the launch directory.

    Without this the assertion above is weaker than it reads: a journal that
    recorded one directory for everything would satisfy it whenever the launch
    happened to be under a worktree. The monitor watches the run rather than doing
    its work, so the directory it is started in is the one the planner launched from
    — and that it differs from the worker's is the whole placement.
    """
    watcher = set(_started(launched.journal, MONITOR_MEMBER))
    assert watcher == {str(REPO_ROOT)}, (
        f"the run's monitor was started in {sorted(watcher)}, not in the launch "
        f"directory {REPO_ROOT}"
    )
    assert not watcher & set(_started(launched.journal, WORKER_MEMBER)), (
        "the monitor and the dispatched worker were started in the same directory"
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


def _worker_turns(journal: list[JournalEvent], kind: str) -> list[dict[str, object]]:
    """Every `kind` turn event the dispatched worker's conversation published, in order."""
    # The turn events carry more than `Placement`; what is read off them here is the
    # provenance stamp, so the payload is read as the open map it is.
    #
    # Read off the journal because, on the adopted engine, nothing else shows the stamp:
    # `just transcript` prints a turn's number and its tool lines, and `just monitor`
    # summarizes a `turn-started` by its status, outcome, message and reason — neither
    # renders `origin`. The stamp is the producer's half of turn provenance, and the
    # reader that acts on it is the engine landing `AGENTS.md` records as not yet
    # adopted, so the relay putting it in the journal is the whole of what this host
    # can observe. The day a view renders it, this read moves there.
    # llmlint: ignore[tests_mirror_real_usage] No view renders a turn's `origin`; see above.
    return [
        cast(dict[str, object], event["payload"])
        for event in journal
        if event.get("kind") == kind and event.get("labels", {}).get("member") == WORKER_MEMBER
    ]


@pytest.mark.xdist_group("worker-start-directory")
def test_every_turn_the_dispatch_published_says_who_authored_it(launched: Launched) -> None:
    """The same journal stamps each of the worker's turns with its author.

    The linked oneagentgraph 0.3.19 stamps a turn's opening with `origin` — `task` for
    the composed task the member was opened on, `supervisor` for words its own
    simulated supervisor generated, `delivered` for text a manager handed the graph —
    and the engine relays the stamp into this journal untouched. It is the producer's
    half of what tells a manager's note from a supervisor's turn: a monitor once read a
    simulated supervisor's *"Stop work on this dispatch"* as the planner's and
    cancelled a live dispatch on it, because a turn that only named a role could not
    say who wrote it. Observed on a real dispatch rather than inferred from the pins,
    because a relay that dropped the field would leave every version file reading
    current and every turn unattributed.
    """
    opened = _worker_turns(launched.journal, "turn-started")
    assert opened, "the journal records no turn of the dispatched worker"
    first = next(turn for turn in opened if turn.get("role") == "assistant")
    assert first.get("origin") == "task", (
        f"the worker's opening turn is not stamped as the composed task: {first}"
    )
    # The stand-in's supervisor sends the worker back once, so the next worker turn
    # opens on the supervisor's own words and is stamped as theirs.
    later = [turn for turn in opened if turn.get("role") == "assistant" and turn is not first]
    assert later, "the supervisor never sent the worker back for another turn"
    assert {turn.get("origin") for turn in later} == {"supervisor"}, (
        f"a later worker turn is not stamped as its supervisor's: {later}"
    )
    said = [
        turn
        for turn in _worker_turns(launched.journal, "turn-message")
        if turn.get("role") == "user"
    ]
    assert said, "the journal records no words of the worker's supervising side"
    assert {turn.get("origin") for turn in said} == {"supervisor"}, (
        f"the supervising side's words reached the journal unattributed: {said}"
    )
