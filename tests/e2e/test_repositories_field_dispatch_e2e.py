"""A lifecycle node naming its repository in `repositories` dispatches where the alias form does.

The engine's contract puts a node's repository in the task record's own top-level
`repositories` list, as one normalized origin, and reserves `onepipeline.repo` for an
identity that list cannot hold. Every plan this host's planners wrote carried a checkout
alias on the reserved key instead, so every record reached the plan store with
`repositories` empty and the board filed every task's issue in the orchestrator's own
repository. Moving the value into the field is only safe if a dispatch made from it
lands in the same registered checkouts the alias form lands in — which is a claim about
the installed engine handing the node's `repo` to `onevcs` verbatim, and about `onevcs
resolve` answering one identity and one publication checkout for both spellings.

So it is measured rather than argued, in the shape `tests/e2e/test_worker_start_directory_e2e.py`
measures where a worker starts: one plan with two lifecycle nodes, identical but for
how each names the repository, launched for real through `just orchestrate`. The run's
own journal records the session `onevcs` opened for each node, and the session's own
record under the registry names the publication checkout that session resolved and the
execution checkout its per-run clone was cut from — the clone's object store names that
same lender, which is the second witness. The two nodes have to agree on both.

**The session record is read rather than `onevcs status`, and the reason is the
measurement itself.** `status` answers `identity.publication_checkout` for the identity —
the first registered checkout by alias order, which is what the *origin* form resolves —
so for the alias form it reports the identity's answer rather than the session's, and a
pair that disagreed would read as agreeing. `session holders` lists open sessions only.
What each session actually recorded as its publication checkout is in the record `onevcs`
wrote for it, and nothing but that record says.

The identity is a scratch one under a **hosted** origin — a bare origin served through a
fake `ssh`, registered as `github.com/scratchowner/scratchname` — because the origin
form is only a value `repositories` can hold for a hosted identity, and this host's own
checkouts may never be registered or dispatched against by a test. Its publication alias
sorts before its execution alias, as this host's `ai-orchestrator` sorts before
`ai-orchestrator-isolated`: `onevcs resolve <origin>` selects the first registered
checkout of the identity by alias order, which is the fact
`tests/test_registry_resolution.py` gates on this host's live registry.
"""

# The finding these answer is about which Nx project owns this file. It sits beside
# `tests/e2e/test_worker_start_directory_e2e.py`, the journey it is the shape of, in the
# code-keyed tier every launch of the installed engine in this repository is in; what it
# depends on — the record renderer, the recipes, the graphs, the scratch identity — is
# the whole of that key, so no narrower edge exists to put it behind, and a project of
# its own for one launch would split a suite whose fixtures and stand-ins it shares. The
# shell it drives is the launch recipe itself, which no narrower host-tool project
# exercises: the recipe-scoped tier is for journeys that double the engine, and this one
# exists to run it.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-block[shell_test_tiers_stay_split] see above

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

import pytest
from fake_backend import PROMPT_LOG_ENV
from project_fixtures import project_from_plan
from scratch_identity import Identity, seeded
from test_worker_start_directory_e2e import FAKE_BACKEND, LAUNCHER_ENVIRONMENT
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The plan's `name`, which is the run id `onepipeline` mints from it.
RUN_NAME = "repositories-field-dispatch"

#: The hosted identity the scratch pair is registered under: the value the origin-form
#: node writes into `repositories`, and what `onevcs` resolves the alias-form node's
#: alias to.
ORIGIN = "github.com/scratchowner/scratchname"

#: The publication checkout's alias — what the alias-form node names on the reserved
#: key — and the execution checkout's. The first sorts before the second, deliberately:
#: it is the property that makes the origin form select the publication checkout rather
#: than the safety clone, and the one this host's own registry holds by the accident of
#: its checkouts' names.
PUBLICATION_ALIAS = "checkout"
EXECUTION_ALIAS = "checkout-isolated"

#: The two nodes, by how each names the repository.
BY_ORIGIN = "by-origin"
BY_ALIAS = "by-alias"

#: A launching session the journey states rather than inherits: this suite runs
#: inside a dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-repositories-field-dispatch"

#: The task both nodes carry. The work is not the subject; where it is placed is.
TASK = (
    "## What\nReport the directory you are in, changing nothing.\n\n"
    "## Why\nWhere the session placed this node is the subject; the work itself is not.\n\n"
    "## Acceptance criteria\n- The dispatch starts and reports.\n"
)


class SessionOpened(TypedDict):
    """The payload fields of a `session-opened` record this journey reads."""

    token: str
    worktree: str


class JournalEvent(TypedDict):
    """One journal record, in the terms this journey reads it.

    `onepipeline` pins the whole record contract and every event carries more than
    this; these are the three fields the question needs, stated rather than restated
    from the engine's schema.
    """

    kind: str
    labels: dict[str, str]
    payload: SessionOpened


class SessionRecord(TypedDict):
    """The fields of a session's own record this journey reads.

    `onevcs` owns the record and writes more than this; these are the three fields the
    question needs, stated rather than restated from its schema.
    """

    publication_checkout: str
    execution_checkout: str
    clone: str


class Placement(NamedTuple):
    """Where one node's session put it, read from the session's record and from git."""

    #: The checkout the session recorded it publishes from.
    publication: Path
    #: The registered checkout the session recorded cutting its clone from.
    execution: Path
    #: The registered checkout that clone's object store says lent it — git's own record
    #: of the same fact, read so the session record is corroborated rather than believed.
    lender: Path
    #: The worktree the journal says the session cut.
    worktree: Path


class Launched(NamedTuple):
    """One settled run of the two-node plan, and where each node was placed."""

    identity: Identity
    placements: dict[str, Placement]


def _plan(root: Path) -> Path:
    """One plan, two lifecycle nodes: the same work, the repository named two ways.

    `project_from_plan` writes each node through this repository's own record renderer,
    which puts a `host/owner/name` `repo` into the record's `repositories` and anything
    else onto `onepipeline.repo` — so the two nodes reach the store in exactly the two
    shapes the contract keeps, and the engine's loader reads both back to one `repo`.
    """
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Record where a node naming its repository each way is placed"},
                "name": RUN_NAME,
                "tasks": [
                    {
                        "id": node_id,
                        "repo": repo,
                        "execution_checkout": EXECUTION_ALIAS,
                        "persona": "engineer",
                        "title": f"feat: place {node_id}",
                        "task": TASK,
                    }
                    for node_id, repo in ((BY_ORIGIN, ORIGIN), (BY_ALIAS, PUBLICATION_ALIAS))
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


def _session_record(token: str, home: Path) -> SessionRecord:
    """The record `onevcs` wrote for one session, under the scratch registry.

    Read for the reason the module docstring gives: no verb reports a closed session's
    own publication checkout, and `status` answers the identity's instead. The cast says
    `onevcs` owns the shape; the subscripts below fail loudly on a record without it.
    """
    # llmlint: ignore[tests_mirror_real_usage] no verb reports this; see the docstring
    record = home / "sessions" / f"{token}.json"
    assert record.is_file(), f"onevcs recorded no session {token} under {home}"
    return cast(SessionRecord, json.loads(record.read_text(encoding="utf-8")))


def _lender(clone: Path) -> Path:
    """The registered checkout ``clone`` shares its object store with.

    A per-run clone is cut from the execution checkout and borrows its objects rather
    than copying them, and git records whose in `objects/info/alternates` — so that file
    names the *lender*, the registered execution checkout, where the clone itself is a
    directory that exists for one run and is reclaimed with it.
    """
    # llmlint: ignore[tests_mirror_real_usage] no view reports the lender; see the docstring
    alternates = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "--git-path", "objects/info/alternates"],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert alternates.returncode == 0, f"{clone} is no git clone:\n{alternates.stderr}"
    recorded = Path(os.path.join(clone, alternates.stdout.strip()))
    assert recorded.is_file(), f"{clone} borrows no object store: {recorded} is absent"
    (lent,) = recorded.read_text(encoding="utf-8").split()
    # `<checkout>/.git/objects` is what the file names; the checkout is what a plan names.
    return Path(lent).resolve().parent.parent


def _placement(event: JournalEvent, home: Path) -> Placement:
    """Where the session one journal record names put its node."""
    record = _session_record(event["payload"]["token"], home)
    return Placement(
        publication=Path(record["publication_checkout"]).resolve(),
        execution=Path(record["execution_checkout"]).resolve(),
        lender=_lender(Path(record["clone"])),
        worktree=Path(event["payload"]["worktree"]).resolve(),
    )


@pytest.fixture(scope="module")
def launched(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Launched]:
    """Launch the two-node plan for real, and hand every question where each node landed."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("repositories-field-dispatch")
    identity = seeded(root, publication=PUBLICATION_ALIAS, execution=EXECUTION_ALIAS, origin=ORIGIN)

    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    # Every root this run writes under, so its registry, its worktrees, its ledger,
    # and its graph scratch are all this journey's and none of them the host's — and
    # the fake `ssh` the hosted origin is served through, which every fetch a session
    # makes goes over.
    environment["ONEVCS_HOME"] = str(identity.home)
    environment.update(identity.environment)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(root / "state")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment[PROMPT_LOG_ENV] = str(root / "turns.jsonl")

    launch = subprocess.run(
        ["just", "orchestrate", project_from_plan(_plan(root))],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(400),
        check=False,
    )
    assert launch.returncode == 0, f"the launch did not settle:\n{launch.stdout}\n{launch.stderr}"
    journal = root / "runs" / RUN_NAME / "events.jsonl"
    assert journal.is_file(), f"the launch recorded no journal at {journal}"
    # `onepipeline` writes the journal, so its schema is stated above rather than
    # validated here; the cast says which. The journal is where the session a node
    # opened is recorded, as `test_worker_start_directory_e2e.py` reads it.
    # llmlint: ignore[tests_mirror_real_usage] no view reports a node's session token
    opened = [
        cast(JournalEvent, json.loads(line))
        for line in journal.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("kind") == "session-opened"
    ]
    placements = {
        event["labels"]["node"]: _placement(event, identity.home)
        for event in opened
        if "node" in event.get("labels", {})
    }
    yield Launched(identity=identity, placements=placements)


@pytest.mark.xdist_group("repositories-field-dispatch")
def test_both_spellings_open_a_session_and_the_journal_names_each(launched: Launched) -> None:
    """Every node was dispatched as a lifecycle node, whichever way it named the repository.

    The failing shape is specific: a node whose `repo` the engine did not read out of
    `repositories` is a direct node, which opens no session and leaves no record here.
    """
    assert set(launched.placements) == {BY_ORIGIN, BY_ALIAS}, (
        f"the journal records sessions for {sorted(launched.placements)}, not for both nodes"
    )


@pytest.mark.xdist_group("repositories-field-dispatch")
def test_the_origin_form_publishes_from_the_checkout_the_alias_form_does(
    launched: Launched,
) -> None:
    """`onevcs` resolves the origin and the alias to one identity and one publication checkout.

    That checkout is the registered publication clone — not the execution clone the
    same identity also registers, which the origin form selects the moment its alias
    sorts first. That failing shape was induced once, while this journey was written, by
    renaming the publication alias to sort after the safety clone: this assertion then
    failed with the origin form publishing from the safety clone. The measurement the
    module docstring describes, on the installed engine and the `onevcs` it links.
    """
    by_origin = launched.placements[BY_ORIGIN]
    by_alias = launched.placements[BY_ALIAS]
    assert by_origin.publication == by_alias.publication, (
        f"the origin form publishes from {by_origin.publication} and the alias form from "
        f"{by_alias.publication}; the two spellings name different checkouts"
    )
    assert by_origin.publication == launched.identity.publication.resolve(), (
        f"both forms publish from {by_origin.publication}, which is not the registered "
        f"publication checkout {launched.identity.publication}"
    )


@pytest.mark.xdist_group("repositories-field-dispatch")
def test_the_origin_form_executes_in_a_clone_of_the_checkout_the_alias_form_does(
    launched: Launched,
) -> None:
    """Both sessions cut their worktree from a clone lent by the registered execution checkout.

    The clone itself is per run and per node, so the two are different directories by
    construction; what has to agree is the lender — the registered safety clone the
    plan named as `execution_checkout` — and the origin form's worktree has to be its
    own rather than the alias form's.
    """
    by_origin = launched.placements[BY_ORIGIN]
    by_alias = launched.placements[BY_ALIAS]
    assert by_origin.execution == by_alias.execution, (
        f"the origin form's clone was cut from {by_origin.execution} and the alias form's "
        f"from {by_alias.execution}"
    )
    assert by_origin.execution == launched.identity.execution.resolve(), (
        f"both clones were cut from {by_origin.execution}, which is not the registered "
        f"execution checkout {launched.identity.execution}"
    )
    for placed in (by_origin, by_alias):
        assert placed.lender == placed.execution, (
            f"the session recorded cutting its clone from {placed.execution}, while the "
            f"clone's own object store was lent by {placed.lender}"
        )
    assert by_origin.worktree != by_alias.worktree, (
        f"both nodes were placed in one worktree, {by_origin.worktree}"
    )
    assert by_origin.worktree.is_relative_to(launched.identity.home), (
        f"the origin form's worktree {by_origin.worktree} was cut outside the scratch "
        f"registry's workspaces"
    )


# llmlint: ignore-end[shell_test_tiers_stay_split]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
