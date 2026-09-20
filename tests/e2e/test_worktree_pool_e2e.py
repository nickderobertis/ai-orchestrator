"""A pooled identity hands its second session the slot its first one returned, warm.

The worktree pool exists because some languages build large outputs — a Rust
`target/`, a `node_modules/` — that a session cutting a fresh worktree builds from
nothing and deletes on close, every node of every run, and the disk wears for it.
`config/onevcs.workspaces.yml` sizes this host's pool per identity and `just
repos-apply` installs it; what this journey holds is that the pinned `onevcs` — the CLI
the manager verbs run — places a session on a warm slot when the file says so, returns
the slot on close with the ignored build output intact, and places the next session on
it with its own branch checked out. `tests/test_linked_libraries.py` holds the other
half, that the copy a dispatch opens its session through is a release carrying the same
pool.

The identity is a scratch one against a scratch `ONEVCS_HOME`, seeded and registered
through the real recipe with a workspaces file pooling it, because a test may never open
sessions against this host's own checkouts: a `session open` reclaims run roots on the
way in, and the real registry's run roots are live dispatches.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] Placed
beside `tests/e2e/test_run_root_lease_e2e.py`, the journey it is modelled on,
which drives the same installed `onevcs` through the same recipe from this project; the
two are one subject — where a session is placed — and a project of this journey's own
would be a change to `nx.json`, `orchestrator/project.json` and `tests/nx_inputs.py` for
both at once, which is the module docstring of `tests/e2e/test_repo_registry_apply_e2e.py`'s
standing follow-up rather than this journey's to make.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The same edge, for the
same reason: the cost class is the lease journey's — a seeded identity, a registry apply
and a handful of real `onevcs` sessions — and the project both would sit behind is that
deferred follow-up.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

from conftest import git
from scratch_identity import Identity, pooling, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The alias every session here is opened against, which becomes each slot's lender.
EXECUTION_ALIAS = "execution"
#: What the seed ignores, standing in for the build output a warm slot exists to keep.
BUILD_OUTPUT = "target/"
#: The file a session writes under it, and what a fresh worktree would have to rebuild.
BUILT = Path("target") / "built.marker"
#: A file a session leaves untracked and unignored, which a return cleans away.
STRAY = Path("scratch.txt")
#: The ignored path the identity's `delete` rule names — this repository's own rule
#: names `.logs/`, the innermost stage's log — and the file a session leaves under it.
DELETED_ON_RETURN = ".logs/"
LEAKED_LOG = Path(".logs") / "check.log"
#: What the identity's `maintain` command leaves in a slot's worktree, so that the
#: command ran there is observable; the command itself is `touch`, spawned with no
#: shell exactly as the workspaces file's `cargo sweep` would be.
MAINTAINED = "maintained.marker"
#: A workspaces file pooling every seeded identity at two slots, deleting the log on
#: every return and maintaining idle slots with a marker: the shape of this host's own
#: rule for a Rust identity, with a command a scratch worktree can run.
POOLED_WITH_RULES = (
    "version: 1\n"
    "default:\n"
    "  pool: 2\n"
    "  overflow: unlimited\n"
    f'  delete: ["{DELETED_ON_RETURN}"]\n'
    f'  maintain: {{command: ["touch", "{MAINTAINED}"], timeout: 1m}}\n'
    "rules: []\n"
)
#: The branch the second session pins, so its checkout is observable as its own.
SECOND_BRANCH = "claude/second"
#: What `session open` exits with when the identity is full, per onevcs's contract: its
#: own code, so a caller can tell "come back later" from "this request was wrong".
POOL_EXHAUSTED = 4


class Session(NamedTuple):
    """One really-opened session, as `session open` reported it."""

    token: str
    worktree: Path
    branch: str


# The keys of `pool status --json` these journeys read, and only those: the document
# is `onevcs`'s contract, its `PoolStatus` type, and a restatement of the whole of it
# here would be a second schema nothing reconciles. Each key below is asserted on by a
# journey, so a rename fails here by name rather than drifting silently.
class Capacity(TypedDict):
    """The capacity numbers a journey holds an identity to."""

    slots: int
    idle: int
    in_use: int
    admits: int | Literal["unlimited"]
    admitted: bool


class SlotState(TypedDict, total=False):
    """A slot's tagged state: `idle`, or `in-use` with the session holding it."""

    state: Literal["idle", "in-use", "maintaining", "broken"]
    session: str


class SlotStatus(TypedDict):
    """One slot: its number, its lender, its state, and the maintenance stamp."""

    number: int
    execution_checkout: str
    state: SlotState
    last_maintained: str | None
    last_outcome: str | None


class PoolStatus(TypedDict):
    """The two halves of the document."""

    capacity: Capacity
    slots: list[SlotStatus]


def _onevcs(identity: Identity, *arguments: str) -> subprocess.CompletedProcess[str]:
    """The pinned CLI, against the scratch registry."""
    return subprocess.run(
        ["uv", "run", "onevcs", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(identity.home), **identity.environment},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _open(identity: Identity, *arguments: str) -> Session:
    opened = _onevcs(identity, "session", "open", EXECUTION_ALIAS, *arguments)
    assert opened.returncode == 0, f"session open failed:\n{opened.stdout}\n{opened.stderr}"
    reported = json.loads(opened.stdout.strip())
    return Session(
        token=reported["token"], worktree=Path(reported["worktree"]), branch=reported["branch"]
    )


def _close(identity: Identity, session: Session) -> None:
    closed = _onevcs(identity, "session", "close", session.token)
    assert closed.returncode == 0, f"session close failed:\n{closed.stdout}\n{closed.stderr}"


def _pool_status(identity: Identity) -> PoolStatus:
    status = _onevcs(identity, "pool", "status", EXECUTION_ALIAS, "--json")
    assert status.returncode == 0, f"pool status failed:\n{status.stdout}\n{status.stderr}"
    document: PoolStatus = json.loads(status.stdout)
    return document


def _the_one_slot(status: PoolStatus) -> SlotStatus:
    """The slot a pool holding exactly one reports, with its number and lender."""
    slots = status["slots"]
    assert len(slots) == 1, f"the pool holds {len(slots)} slots, not the one this journey cut"
    return slots[0]


def test_a_second_session_reuses_the_returned_slot_with_its_build_output_intact(
    tmp_path: Path,
) -> None:
    """The first session's slot is returned on close and handed to the second, warm.

    The first session writes under the seed's ignored `target/` and leaves an untracked
    file beside it; the return keeps the one and cleans the other. The second session,
    pinned to a branch of its own, opens in the same worktree with that branch checked
    out and the build output still there, `pool status` names the slot as in use by it
    with the checkout it was cut from as its lender, and the second close returns it.
    """
    identity = seeded(tmp_path, workspaces=pooling(2), ignored=(BUILD_OUTPUT,))

    first = _open(identity)
    assert first.worktree.parent.parent.name == "pool", (
        f"the first session was placed under {first.worktree}, not on a pooled slot"
    )
    slot_number = int(first.worktree.parent.name)
    (first.worktree / BUILT).parent.mkdir()
    (first.worktree / BUILT).write_text("built by the first session\n", encoding="utf-8")
    (first.worktree / STRAY).write_text("left behind\n", encoding="utf-8")
    _close(identity, first)

    returned = _the_one_slot(_pool_status(identity))
    assert returned["state"] == {"state": "idle"}, returned
    assert returned["number"] == slot_number, returned

    second = _open(identity, "--branch", SECOND_BRANCH)

    assert second.worktree == first.worktree, (
        f"the second session was placed at {second.worktree}, not on the returned slot"
    )
    assert second.branch == SECOND_BRANCH
    assert git("branch", "--show-current", cwd=second.worktree).strip() == SECOND_BRANCH
    assert (second.worktree / BUILT).read_text(encoding="utf-8") == "built by the first session\n"
    assert not (second.worktree / STRAY).exists(), "the return kept an untracked file"
    in_use = _the_one_slot(_pool_status(identity))
    assert in_use["number"] == slot_number, in_use
    assert in_use["state"] == {"state": "in-use", "session": second.token}, in_use
    assert Path(in_use["execution_checkout"]) == identity.execution.resolve(), in_use

    _close(identity, second)

    final = _pool_status(identity)
    assert _the_one_slot(final)["state"] == {"state": "idle"}, final
    assert (final["capacity"]["slots"], final["capacity"]["idle"], final["capacity"]["in_use"]) == (
        1,
        1,
        0,
    ), final["capacity"]


def test_a_return_deletes_what_the_rule_names_and_maintenance_runs_in_the_idle_slot(
    tmp_path: Path,
) -> None:
    """The identity's `delete` and `maintain` rules act on a returned slot, and only then.

    A session leaves a log under the ignored `.logs/` the rule names and build output
    under the ignored `target/` it does not; the return deletes the first and keeps the
    second. `onevcs pool maintain` — the verb the engine's idle branch calls on the
    schedule `config/onepipeline.maintenance.yaml` names — then runs the command in the
    idle slot's worktree, stamps the slot, and answers `not-due` inside the span on the
    next ask, so nothing runs twice; a slot in use is left alone.
    """
    identity = seeded(
        tmp_path, workspaces=POOLED_WITH_RULES, ignored=(BUILD_OUTPUT, DELETED_ON_RETURN)
    )
    session = _open(identity)
    (session.worktree / LEAKED_LOG).parent.mkdir()
    (session.worktree / LEAKED_LOG).write_text("the innermost stage's log\n", encoding="utf-8")
    (session.worktree / BUILT).parent.mkdir()
    (session.worktree / BUILT).write_text("built\n", encoding="utf-8")

    withheld = _onevcs(identity, "pool", "maintain", EXECUTION_ALIAS, "--json")
    assert withheld.returncode == 0, withheld.stdout + withheld.stderr
    assert json.loads(withheld.stdout)["identities"][0]["outcome"] == {
        "slots": [{"number": 1, "outcome": {"in-use": {"session": session.token}}}]
    }, withheld.stdout
    assert not (session.worktree / MAINTAINED).exists(), "a slot in use was maintained"
    _close(identity, session)

    assert not (session.worktree / LEAKED_LOG).parent.exists(), "the return kept .logs/"
    assert (session.worktree / BUILT).read_text(encoding="utf-8") == "built\n"

    ran = _onevcs(identity, "pool", "maintain", EXECUTION_ALIAS, "--json")

    assert ran.returncode == 0, ran.stdout + ran.stderr
    [slot_ran] = json.loads(ran.stdout)["identities"][0]["outcome"]["slots"]
    assert slot_ran["number"] == 1, slot_ran
    assert slot_ran["outcome"]["ran"]["outcome"] == "succeeded", slot_ran
    assert (session.worktree / MAINTAINED).exists(), "the maintain command did not run in the slot"
    stamped = _the_one_slot(_pool_status(identity))
    assert stamped["last_outcome"] == "succeeded", stamped
    assert stamped["last_maintained"] is not None, stamped

    again = _onevcs(identity, "pool", "maintain", EXECUTION_ALIAS, "--older-than", "1d", "--json")

    assert again.returncode == 0, again.stdout + again.stderr
    assert json.loads(again.stdout)["identities"][0]["outcome"] == {
        "slots": [
            {"number": 1, "outcome": {"not-due": {"last_maintained": stamped["last_maintained"]}}}
        ]
    }, again.stdout


def test_a_full_identity_refuses_the_next_open_by_its_own_code_until_a_slot_returns(
    tmp_path: Path,
) -> None:
    """With one slot and no overflow, a second concurrent open is refused, naming the holder.

    The refusal is `PoolExhausted`, exit 4 — the code the adopted engine reads to hold a
    node under `workspace-wait` and requeue it rather than fail it — and it names the
    session holding the slot. Once that session closes, the same open is admitted onto
    the returned slot: the failure path and its recovery.
    """
    identity = seeded(tmp_path, workspaces=pooling(1, overflow=0))
    holder = _open(identity)

    refused = _onevcs(identity, "session", "open", EXECUTION_ALIAS, "--branch", SECOND_BRANCH)

    assert refused.returncode == POOL_EXHAUSTED, refused.stdout + refused.stderr
    assert holder.token in refused.stderr, refused.stderr
    assert "overflow" in refused.stderr, refused.stderr
    capacity = _pool_status(identity)["capacity"]
    assert capacity["admitted"] is False, capacity
    assert capacity["admits"] == 0, capacity

    _close(identity, holder)
    admitted = _open(identity, "--branch", SECOND_BRANCH)

    assert admitted.worktree == holder.worktree
    assert admitted.branch == SECOND_BRANCH
    _close(identity, admitted)
