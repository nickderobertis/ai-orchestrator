"""A cross-DAG dependency on a live holder's node acknowledges it at launch; nothing else does.

`docs/orchestration.md`'s *The concurrency interlock, and when to depend instead* is
what a manager decides a refused launch with, and every branch of it is driven here
through the installed engine under `just orchestrate`: a first run holds a lifecycle
node live on a scratch repository identity — its session open, its stand-in worker
taking its time — and three second launches meet it. One whose every node on that
identity depends on `run:<first>#<node>` launches unacknowledged and records the holder
it waits for as `concurrent-deferred`; the same plan without the dependency is refused,
naming the holding run, its node and the dependency that would acknowledge it; and that
plan with `--acknowledge-concurrent` launches, recording `concurrent-acknowledged` over
the holder. The dependency is written the way a plan authored here carries one, as the
`onepipeline.deps` task metadata `orchestrator/project_store.py` stores a cross-DAG
reference under.

Everything is real — the recipes, `scripts/onepipeline.sh`, the installed `onepipeline`,
`onevcs`'s session registry and the identity's checkouts — and the only thing doubled is
the paid model, at the boundary every launch journey here doubles it. The identity, its
registry and the runs root are this journey's scratch, so no host session is ever a
holder here.
"""

# Placed beside every other launch of the installed engine in `tests/e2e`, whose shared
# fixtures and stand-ins it uses; the xdist group below keeps it off the toolchain lock.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[shell_test_tiers_stay_split] a pytest journey, not a shell suite

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict, cast

import pytest
from fake_backend import AGENT_DELAY_ENV
from nx_workspace import WORKSPACE_INSTALL_MARKS
from project_fixtures import project_from_plan
from scratch_identity import seeded
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A reader of the toolchain this checkout provisioned: every step here runs its tool
#: through a `just` recipe or `uv run`, and no writer touches that toolchain, so
#: `tests/e2e/nx_workspace.py` asks no xdist group of it.
pytestmark = list(WORKSPACE_INSTALL_MARKS)

#: The identifiers the interlock records are keyed by: a run's id, a node's id within its
#: plan, an `onevcs` session token, and a cross-DAG reference `run:<run>#<node>`.
RunId = NewType("RunId", str)
NodeId = NewType("NodeId", str)
SessionToken = NewType("SessionToken", str)
Dependency = NewType("Dependency", str)

#: A launching session this journey states rather than inherits, so every run here is
#: one `just stop` treats as this session's own.
LAUNCHING_SESSION = "e2e-concurrency-interlock"

#: The first run, and the node whose open session is the live holder.
HOLDER_RUN = RunId("interlock-holder")
HOLDING_NODE = NodeId("holding")
#: The one reference that acknowledges the holder: a dependency on another node of the
#: holding run, even one downstream of it, does not.
DEPENDENCY = Dependency(f"run:{HOLDER_RUN}#{HOLDING_NODE}")

#: The second plan's node on the shared identity, in every launch below.
SECOND_NODE = NodeId("second")

EXECUTION_ALIAS = "execution"

#: How long the stand-in worker holds each dispatch: far longer than the journey takes,
#: so the holder is live for every launch below; the fixture stops every run it started.
HELD_SECONDS = 600
PATIENCE_SECONDS = 240

REFUSED = 2
#: The refusal's opening, as the engine writes it — `tests/test_engine_contracts.py`
#: reconciles this and the three words below against the engine's source at the pin.
REFUSAL = "concurrent project work refused for run "
FLAG_REMEDY = "pass --acknowledge-concurrent"
DEFERRED = "concurrent-deferred"
ACKNOWLEDGED = "concurrent-acknowledged"


class DeferredHolder(TypedDict):
    """One holder a dependency covers, as `concurrent-deferred` names it."""

    identity: str
    session: SessionToken
    owner_pid: int
    run: RunId
    node: NodeId
    dependency: Dependency
    dependents: list[NodeId]


class DeferredRecord(TypedDict):
    """The `concurrent-deferred` payload: the launching run and every covered holder."""

    launching: RunId
    holders: list[DeferredHolder]


class AcknowledgedHolder(TypedDict, total=False):
    """One live holder the flag acknowledged; the keys past `owner_pid` are optional."""

    session: SessionToken
    owner_pid: int
    identity: str
    run: RunId
    node: NodeId
    dependency: Dependency


class AcknowledgedRuns(TypedDict):
    """The launching run and the sessions it was launched alongside."""

    launching: RunId
    holding_sessions: list[SessionToken]


class AcknowledgedRecord(TypedDict):
    """The `concurrent-acknowledged` payload."""

    shared_identities: list[str]
    runs: AcknowledgedRuns
    holders: list[AcknowledgedHolder]


#: Each record's keys as the engine's contract states them, which a read holds a payload
#: to before typing it, so an added or dropped key fails here rather than reading as absent.
DEFERRED_KEYS = frozenset(DeferredRecord.__annotations__)
DEFERRED_HOLDER_KEYS = frozenset(DeferredHolder.__annotations__)
ACKNOWLEDGED_KEYS = frozenset(AcknowledgedRecord.__annotations__)
ACKNOWLEDGED_HOLDER_KEYS = frozenset(AcknowledgedHolder.__annotations__)
ACKNOWLEDGED_HOLDER_REQUIRED = frozenset({"session", "owner_pid"})


class Holder(NamedTuple):
    """The first run, live and holding the scratch identity through its node's session."""

    root: Path
    environment: dict[str, str]
    publication: Path
    session: SessionToken


class Launch(NamedTuple):
    """One second launch: the recipe's own answer, and the run id it would mint."""

    run: RunId
    completed: subprocess.CompletedProcess[str]

    @property
    def said(self) -> str:
        return self.completed.stdout + self.completed.stderr


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 300
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _plan(root: Path, run: RunId, node: NodeId, publication: Path, deps: list[Dependency]) -> str:
    """A one-node lifecycle plan on the scratch identity, stored as a local project."""
    plan = root / f"{run}.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": f"Work the shared repository as {run}"},
                "name": run,
                "tasks": [
                    {
                        "id": node,
                        "repo": str(publication),
                        "execution_checkout": EXECUTION_ALIAS,
                        "persona": "engineer",
                        "deps": deps,
                        "task": (
                            "## What\nReport, taking your time.\n\n## Why\nSo the session "
                            "this node opens is live while another launch meets it.\n\n"
                            "## Acceptance criteria\n- Reported.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project_from_plan(plan, run)


def _payloads(root: Path, run: RunId, kind: str) -> list[dict[str, object]]:
    """The payload of every record of ``kind`` in the run's own journal.

    Read off the journal rather than through `just monitor`, because what is asserted is
    the payload of a launch record — which holder it names, under which dependency — and
    no view renders a launch record's payload.
    """
    journal = root / "runs" / run / "events.jsonl"
    if not journal.is_file():
        return []
    found = []
    # llmlint: ignore[tests_mirror_real_usage] No view renders a launch record's payload.
    for line in journal.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if isinstance(event, dict) and event.get("kind") == kind:
            payload = event.get("payload")
            assert isinstance(payload, dict), f"a {kind} record carries no payload: {event}"
            found.append(payload)
    return found


def _deferred(root: Path, run: RunId) -> list[DeferredRecord]:
    """Every `concurrent-deferred` the run journalled, each held to the contract's keys."""
    records = []
    for payload in _payloads(root, run, DEFERRED):
        assert set(payload) == DEFERRED_KEYS, f"{DEFERRED} carries {sorted(payload)}"
        holders = payload["holders"]
        assert isinstance(holders, list), payload
        for entry in holders:
            assert isinstance(entry, dict) and set(entry) == DEFERRED_HOLDER_KEYS, payload
        records.append(cast(DeferredRecord, payload))
    return records


def _acknowledged(root: Path, run: RunId) -> list[AcknowledgedRecord]:
    """Every `concurrent-acknowledged` the run journalled, held to the contract's keys."""
    records = []
    for payload in _payloads(root, run, ACKNOWLEDGED):
        assert set(payload) == ACKNOWLEDGED_KEYS, f"{ACKNOWLEDGED} carries {sorted(payload)}"
        holders = payload["holders"]
        assert isinstance(holders, list), payload
        for entry in holders:
            assert isinstance(entry, dict), payload
            assert ACKNOWLEDGED_HOLDER_REQUIRED <= set(entry) <= ACKNOWLEDGED_HOLDER_KEYS, entry
        records.append(cast(AcknowledgedRecord, payload))
    return records


def _until[T](what: str, ready: Callable[[], T | None], *, seconds: float = PATIENCE_SECONDS) -> T:
    """Poll ``ready`` until it answers something, or fail naming what never arrived."""
    limit = deadline(seconds)
    while time.monotonic() < limit:
        if (answer := ready()) is not None:
            return answer
        time.sleep(1.0)
    raise AssertionError(f"{what} never arrived within {seconds}s")


def _opened_session(root: Path, run: RunId) -> SessionToken | None:
    """The token of the session the run's node opened, once the run has recorded one."""
    for payload in _payloads(root, run, "session-opened"):
        token = payload.get("token")
        if isinstance(token, str) and token:
            return SessionToken(token)
    return None


@pytest.fixture(scope="module")
def holder(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Holder]:
    """Launch the first run detached and hold it until its node's session is open."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("concurrency-interlock")
    identity = seeded(root, execution=EXECUTION_ALIAS)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(root, oneharness_bin, session=LAUNCHING_SESSION)
    environment.update(identity.environment)
    # The registry every launch here reads its holders from is this journey's own.
    environment["ONEVCS_HOME"] = str(identity.home)
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    project = _plan(root, HOLDER_RUN, HOLDING_NODE, identity.publication, [])
    try:
        launched = _just(
            "orchestrate", project, "--detach", "--dag-graph", "off", environment=environment
        )
        assert launched.returncode == 0, (
            f"the first launch failed:\n{launched.stdout}{launched.stderr}"
        )
        session = _until("the holding node's session", lambda: _opened_session(root, HOLDER_RUN))
        yield Holder(root, environment, identity.publication, session)
    finally:
        _just("stop", HOLDER_RUN, environment=environment, seconds=120)


def _second(holder: Holder, run: RunId, deps: list[Dependency], *flags: str) -> Iterator[Launch]:
    """Launch a second plan against the held identity, and stop it if it started."""
    project = _plan(holder.root, run, SECOND_NODE, holder.publication, deps)
    completed = _just(
        "orchestrate",
        project,
        "--detach",
        "--dag-graph",
        "off",
        *flags,
        environment=holder.environment,
    )
    try:
        yield Launch(run, completed)
    finally:
        if completed.returncode == 0:
            _just("stop", run, environment=holder.environment, seconds=120)


@pytest.fixture
def depending(holder: Holder) -> Iterator[Launch]:
    """The second plan, its one node on the identity depending on the holding node."""
    yield from _second(holder, RunId("interlock-depending"), [DEPENDENCY])


@pytest.fixture
def undeclared(holder: Holder) -> Iterator[Launch]:
    """The second plan with no dependency, launched as a manager first would."""
    yield from _second(holder, RunId("interlock-undeclared"), [])


@pytest.fixture
def acknowledging(holder: Holder) -> Iterator[Launch]:
    """The same undeclared plan, launched past the refusal with the flag."""
    yield from _second(holder, RunId("interlock-acknowledging"), [], "--acknowledge-concurrent")


def test_a_dependency_on_the_holding_node_launches_and_records_the_deferral(
    holder: Holder, depending: Launch
) -> None:
    """Every node on the identity reaches `run:<first>#<node>`, so the launch is not refused.

    And the run says why it was not: `concurrent-deferred` names the holder by its
    session, run and node, the dependency that covers it and the plan nodes that wait on
    it. Without the flag, so no `concurrent-acknowledged` is written.
    """
    assert depending.completed.returncode == 0, (
        f"the dependent launch was refused:\n{depending.said}"
    )
    assert REFUSAL not in depending.said, depending.said
    deferred = _deferred(holder.root, depending.run)
    assert len(deferred) == 1, f"the run journalled {len(deferred)} {DEFERRED} records"
    (record,) = deferred
    assert record["launching"] == depending.run, record
    (named,) = record["holders"]
    assert named["session"] == holder.session, record
    assert named["run"] == HOLDER_RUN, record
    assert named["node"] == HOLDING_NODE, record
    assert named["dependency"] == DEPENDENCY, record
    assert named["dependents"] == [SECOND_NODE], record
    assert not _acknowledged(holder.root, depending.run), (
        "a launch made without the flag journalled an acknowledgement"
    )


def test_the_same_plan_without_the_dependency_is_refused_naming_the_remedy(
    holder: Holder, undeclared: Launch
) -> None:
    """Refused before a run is minted, naming the holding run, its node and the dependency.

    The refusal is what a manager decides from, so each part it is documented to carry is
    asserted on its own: whose run the holder belongs to, which node, which plan node does
    not reach it, the reference that would acknowledge it, and the flag.
    """
    said = undeclared.said
    assert undeclared.completed.returncode == REFUSED, (
        f"the undeclared launch exited {undeclared.completed.returncode}:\n{said}"
    )
    assert f"{REFUSAL}'{undeclared.run}':" in said, said
    assert f"for run '{HOLDER_RUN}' node '{HOLDING_NODE}'" in said, said
    assert f"plan node(s) '{SECOND_NODE}' do not depend on it" in said, said
    assert f"`{DEPENDENCY}` under `onepipeline.deps`" in said, said
    assert FLAG_REMEDY in said, said
    assert not (holder.root / "runs" / undeclared.run).exists(), "a refused launch minted a run"


def test_the_flag_launches_past_the_holder_and_records_the_acknowledgement(
    holder: Holder, acknowledging: Launch
) -> None:
    """`--acknowledge-concurrent` launches the undeclared plan and records whom it races.

    The holder is no dependency's, so its entry names its session, run and node and no
    `dependency`, and nothing is deferred.
    """
    assert acknowledging.completed.returncode == 0, (
        f"the acknowledged launch was refused:\n{acknowledging.said}"
    )
    acknowledged = _acknowledged(holder.root, acknowledging.run)
    assert len(acknowledged) == 1, f"the run journalled {len(acknowledged)} {ACKNOWLEDGED}"
    (record,) = acknowledged
    assert record["runs"]["launching"] == acknowledging.run, record
    assert record["runs"]["holding_sessions"] == [holder.session], record
    (named,) = record["holders"]
    assert named.get("session") == holder.session, record
    assert named.get("run") == HOLDER_RUN, record
    assert named.get("node") == HOLDING_NODE, record
    assert "dependency" not in named, record
    assert not _deferred(holder.root, acknowledging.run), (
        "a holder no dependency covers was journalled as deferred"
    )
