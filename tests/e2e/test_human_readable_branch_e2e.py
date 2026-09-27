"""A dispatched lifecycle node is cut a prefixed branch named after its plan and its node.

Pins, a bill-of-materials reconciliation and a prose gate can all read current while
the branch a launch cuts is unchanged, so the branch is read from the one place a run
records it: the `session-opened` event in the run's own journal. Everything between the
recipe and the model is real, as in `tests/e2e/test_worker_start_directory_e2e.py`, and
the identity, its registry and the prefix configured on it are this journey's scratch.
The plan comes from a `local-md` source, whose tasks carry no `key`, so the default
template falls back to the plan's name.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
import short_state
from fake_backend import PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from project_fixtures import project_from_plan
from scratch_identity import seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model, named to `oneagentgraph` as its harness.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"

#: The plan's `name`: the run id `onepipeline` mints from it, and the part of the branch
#: the default template renders for a task with no key.
RUN_NAME = "human-readable-branch"

NODE_ID = "readable"

#: The persona the node is dispatched as. Named so the assertion below can say the
#: branch does not carry it, which the repository-and-persona names this host cut did.
PERSONA = "engineer"

EXECUTION_ALIAS = "execution"

#: The scratch host's branch prefix. Not this host's: a journey's fixture, written into
#: the journey's own `ONEVCS_HOME` and nowhere else.
SCRATCH_PREFIX = "scratch-host/"

#: The host file `onevcs` reads the prefix from, beneath its state root, and the shape
#: the adopted release documents for it.
BRANCHES_FILE = "branches.yml"
BRANCHES = f"version: 1\nprefix: {SCRATCH_PREFIX}\n"

#: The two environment layers that would beat the host file and the default template
#: respectively. Removed so the enclosing shell cannot decide what this journey measures.
OVERRIDES = ("ONEVCS_BRANCH_PREFIX", "ONEPIPELINE_BRANCH_TEMPLATE")

#: The phrase that tells the stand-in this is the turn that commits. The worker commits
#: for the reason `tests/e2e/test_worker_start_directory_e2e.py` gives: a lifecycle node
#: whose branch ends level with its base settles `empty-branch`.
COMMIT_MARKER = "Leave one file behind on your branch, and commit it."

#: One file, one commit, idempotent across a supervisor's send-back. The committer is
#: stated because the launch is made from a module-scoped fixture.
COMMIT_COMMANDS = [
    [
        "sh",
        "-c",
        "[ -f BRANCH.md ] || { git branch --show-current > BRANCH.md && git add BRANCH.md "
        "&& git -c user.email=test@example.com -c user.name=ai-orchestrator-test "
        "commit -qm 'feat: record the branch the worker stood on'; }",
    ]
]

#: A launching session the journey states rather than inherits, and every name a
#: launcher identity reaches `scripts/onepipeline.sh` through.
LAUNCHING_SESSION = "e2e-human-readable-branch"
LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)


def _plan(root: Path, publication: Path) -> Path:
    """A one-node lifecycle plan against the scratch identity, with no task key."""
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "Cut a lifecycle node a branch a person can read"},
                "name": RUN_NAME,
                "tasks": [
                    {
                        "id": NODE_ID,
                        "repo": str(publication),
                        "execution_checkout": EXECUTION_ALIAS,
                        "persona": PERSONA,
                        "task": (
                            f"## What\n{COMMIT_MARKER}\n\n"
                            "## Why\nThe branch the run records cutting is the subject; "
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


class SessionOpened(NamedTuple):
    """The fields of one `session-opened` record this journey reads, parsed from it.

    The journal carries two such records per session — the engine's own, and the one
    `onevcs` publishes, which alone adds the layer the prefix was resolved from — so a
    session is identified by its token, and ``prefix`` is ``None`` on the engine's.
    """

    token: str
    branch: str
    prefix: str | None
    prefix_from: str | None

    @classmethod
    def parse(cls, record: object) -> SessionOpened:
        """One journal record, refused by name unless it carries what this journey reads."""
        assert isinstance(record, dict), f"a journal record is not an object: {record!r}"
        payload = record.get("payload")
        assert isinstance(payload, dict), f"a session-opened record has no payload: {record}"
        token, branch = payload.get("token"), payload.get("branch")
        assert isinstance(token, str), f"a session-opened record names no token: {payload}"
        assert isinstance(branch, str), f"a session-opened record names no branch: {payload}"
        applied = payload.get("branch_prefix")
        if applied is None:
            return cls(token=token, branch=branch, prefix=None, prefix_from=None)
        assert isinstance(applied, dict), f"the applied prefix is not a record: {applied!r}"
        return cls(
            token=token,
            branch=branch,
            prefix=str(applied.get("prefix")),
            prefix_from=str(applied.get("from")),
        )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `xdist_group` selects
# an xdist worker under this suite's `--dist loadgroup`, not a test tier; the tiers here
# split by what a test reads, which is what each one's Nx cache key has to cover.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The task this journey
# answers requires it in the `tests/e2e` tier, in the shape of
# `tests/e2e/test_worker_start_directory_e2e.py`, a tree that pre-dates this change; which
# Nx project owns that tree is a property of the tree, and re-homing the launch journeys
# into a new project is enforcement configuration this change may not move to pass.
@pytest.fixture(scope="module")
def opened(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[list[SessionOpened]]:
    """Launch one lifecycle node for real, and hand over every `session-opened` record."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("human-readable-branch")
    identity = seeded(root, execution=EXECUTION_ALIAS)
    (identity.home / BRANCHES_FILE).write_text(BRANCHES, encoding="utf-8")

    environment = dict(os.environ)
    for name in (*LAUNCHER_ENVIRONMENT, *OVERRIDES):
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    # Every root this run writes under is the journey's own and none of them the host's.
    environment["ONEVCS_HOME"] = str(identity.home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment[PROMPT_LOG_ENV] = str(root / "turns.jsonl")
    keyed = root / "commands.json"
    keyed.write_text(json.dumps({COMMIT_MARKER: COMMIT_COMMANDS}), encoding="utf-8")
    environment[RUN_ON_MARKER_ENV] = str(keyed)

    # llmlint: ignore-block[shell_test_tiers_stay_split] A pytest journey over the real
    # `just` recipe rather than a shell suite, placed beside every other launch journey in
    # this directory as `tests/e2e/test_worker_start_directory_e2e.py` is.
    launch = subprocess.run(
        ["just", "orchestrate", project_from_plan(_plan(root, identity.publication))],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    # llmlint: ignore-end[shell_test_tiers_stay_split]
    assert launch.returncode == 0, f"the launch did not settle:\n{launch.stdout}\n{launch.stderr}"
    journal = root / "runs" / RUN_NAME / "events.jsonl"
    assert journal.is_file(), f"the launch recorded no journal at {journal}"
    # llmlint: ignore[tests_mirror_real_usage] No view reports a node's cut branch name.
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    yield [SessionOpened.parse(event) for event in events if event.get("kind") == "session-opened"]


def _cut(opened: list[SessionOpened]) -> dict[str, str]:
    return {session.token: session.branch for session in opened}


@pytest.mark.xdist_group("human-readable-branch")
def test_the_node_is_cut_the_host_prefix_then_its_plan_and_node(
    opened: list[SessionOpened],
) -> None:
    """Exactly `<prefix><plan name>/<node id>`: containment would pass beside a token."""
    cut = _cut(opened)
    assert len(cut) == 1, f"the run opened {len(cut)} sessions, not one: {opened}"
    assert set(cut.values()) == {f"{SCRATCH_PREFIX}{RUN_NAME}/{NODE_ID}"}, (
        f"the node was cut {sorted(cut.values())}, not the scratch host's prefix followed "
        f"by the plan's name and the node's id: {opened}"
    )


@pytest.mark.xdist_group("human-readable-branch")
def test_the_prefix_came_from_the_host_file(opened: list[SessionOpened]) -> None:
    """The prefix came from `branches.yml`, the layer `AGENTS.md` names, not another."""
    applied = [session for session in opened if session.prefix is not None]
    assert applied, f"no session-opened record says a prefix was applied: {opened}"
    for session in applied:
        assert session.prefix == SCRATCH_PREFIX, f"the applied prefix was {session.prefix!r}"
        assert BRANCHES_FILE in str(session.prefix_from), (
            f"the prefix was resolved from {session.prefix_from!r}, not the host file"
        )


@pytest.mark.xdist_group("human-readable-branch")
def test_the_branch_carries_no_session_token_and_no_repository_or_persona(
    opened: list[SessionOpened],
) -> None:
    """Apart from the equality, so a regression names which unreadable part came back."""
    for token, branch in _cut(opened).items():
        assert token not in branch, f"the branch {branch!r} carries its session token {token!r}"
        parts = branch.split("/")
        assert "onevcs" not in parts, f"the branch {branch!r} is still under onevcs's namespace"
        assert PERSONA not in parts, f"the branch {branch!r} carries the node's persona"
        assert not {"origin", "publication", EXECUTION_ALIAS} & set(parts), (
            f"the branch {branch!r} carries the repository's name"
        )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
