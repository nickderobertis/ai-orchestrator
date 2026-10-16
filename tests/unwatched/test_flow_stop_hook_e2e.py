"""A planning flow holds its manager's session from its start to its exit, between runs too.

`just plan` launches a run, works between runs — a review, a check — and launches more. A
session that owed only the runs that happened to exist could end its turn in a step between
two of them, and the flow then launched runs and asked questions nothing was woken for. So
the recipe runs inside the engine's `onepipeline flow run`, owned by the launching session,
and the `Stop` entry `.claude/settings.json` registers — the engine's own `stop-guard`,
through `onepipeline unwatched` — owes that flow a watch for as long as it lives.

One journey drives that end to end: the real `.claude/settings.json` Stop entry, run as the
harness runs it; the real `just plan`, `just watch` and `just unwatched` recipes; and the
installed engine registering the flow, recording each run's `flow`, watching and judging.
**Only the engine's `start` is doubled**, by `tests/plan_tooling/plan_flow/spike_flow_engine.py`
at a copy's `.venv/bin/onepipeline`, as `just plan`'s own spike journeys double it: it writes
what each stage's run leaves behind with the real store, and the launch record a real
`start` writes, `flow` included. The paid reviewer is the stand-in every planning journey
uses, held in flight to keep the flow in its review step — a step with no run live — for
two and a half wake budgets.

It also records the budget `flow-wake-gap` (`tests/unwatched/budgets.yaml`): each watch it
arms through the `watch` recipe is timed on the wall clock from spawn to exit, as a fraction
of the wake budget, with its ending and the deadline its terms record configured. The
journey asserts no time; whether the figure is within the threshold is the budget check's.

It runs in a copy because the wrapper runs the engine at its checkout's own
`.venv/bin/onepipeline` and nowhere else. The copy holds the tracked files of the trees a
planning flow reads and nothing more, every one of them in `unwatchedWorkspace`, so this
journey stays in the project's cached `test` target, keyed on what it reads.

llmlint: ignore-file[e2e_not_mocked, tests_mirror_real_usage] The engine's `start` is doubled
because this node's acceptance criteria require exactly that boundary and no other: a real
`start` dispatches the paid model for every planner node. Everything around it — the flow,
every watch, the guard's verdicts, each recipe — is the real one, and the launch record the
double writes is the engine's own shape, reconciled against it by
`tests/test_engine_contracts.py` through `tests/e2e/probe_run_root.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict

import budget_telemetry
import plan_fixture_source
import pytest
import short_state
from nx_workspace import OFFLINE_INSTALLS, TOOLCHAIN_WRITER_MARKS, answering_this_checkouts_origin
from project_fixtures import helper, no_budgets
from run_snapshot import is_staging_name
from scratch_identity import PLANNING_FLOW_ORIGIN, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A writer: the copy's `.venv` is synced into the copy, never this checkout's. One worker,
#: because the flow, its watches and the guard's verdicts are one sequence.
pytestmark = [*TOOLCHAIN_WRITER_MARKS, pytest.mark.xdist_group("flow-stop-hook")]

#: The double, installed over the copy's engine, and the real engine this checkout pins.
STAND_IN = REPO_ROOT / "tests" / "plan_tooling" / "plan_flow" / "spike_flow_engine.py"
ONEPIPELINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"

#: The settings file whose `Stop` entry the harness runs at the end of a turn.
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

#: The tracked trees and files a planning flow reads, which is the whole of the copy: the
#: scripts and personas it runs, as `plan-tooling-plan-flow` declares them for the same
#: flow. `unwatchedWorkspace` declares each one, so the journey's key covers what it copies.
COPIED_TREES = ("config", "graphs", "orchestrator", "templates")
COPIED_SCRIPTS = (
    "apply-repo-registry.sh",
    "ask-manager-env.sh",
    "ask-manager.sh",
    "claude-alt-config-dir.sh",
    "codex-alt-home.sh",
    "credentials-env.sh",
    "dispatch-env-hook.sh",
    "dispatch-env.sh",
    "finish-plan.sh",
    "follow-up-draft.sh",
    "follow-up-env.sh",
    "follow-ups.sh",
    "launch-flow.sh",
    "launcher-session.sh",
    "llmlint-fingerprint.sh",
    "llmlint-judge.sh",
    "llmlint-oneharness.sh",
    "llmlint-runtime-env.sh",
    "setup-llmlint.sh",
    "lock-timeout.sh",
    "onepipeline.sh",
    "orchestrate.sh",
    "plan-brief.sh",
    "plan-check.sh",
    "plan-checklist.sh",
    "plan-root-env.sh",
    "plan-store.sh",
    "plan.sh",
    "registered-checkouts.sh",
    "render-diagram.sh",
    "require-provisioned.sh",
    "review-plan.sh",
    "run-ended.sh",
    "template-env.sh",
)
COPIED_FILES = (
    *(f"scripts/{script}" for script in COPIED_SCRIPTS),
    "personas/planner.yaml",
    "personas/design-doc.yaml",
    ".gitignore",
    "justfile",
    "pyproject.toml",
    "uv.lock",
    "onetaskgraph.yaml",
    "oneharness.toml",
    "oneharness.dispatch.toml",
    "oneharness.identities.toml",
    "oneharness.judge.toml",
    "oneharness.plan-review.toml",
    "oneharness.plan-review-whole.toml",
    "oneharness.llmlint.toml",
)

#: The wake budget this journey runs under: 10 seconds standing for the 2100 this host
#: sets, so a watch gives up at the budget less the engine's two-second wake reserve.
WAKE_BUDGET = 10
#: How long the flow is held in its review step, with no run live: two and a half budgets,
#: so at least two of the watches armed across it end on their deadline.
HELD_BUDGETS = 2.5

#: The id the harness gives a manager's session, which the Stop hook and a flow's owner share.
SessionId = NewType("SessionId", str)

#: The launching session, and the stand-ins for the paid reviewer and its guard.
SESSION = SessionId("e2e-flow-stop-hook")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
PASSING_VERDICT = json.dumps({"passes": True, "findings": []})
DESTINATION = "destination"

#: Everything an enclosing dispatch would otherwise decide for this journey — the suite's
#: own `VIRTUAL_ENV` among them, which names this checkout's `.venv` rather than the copy's.
INHERITED_ENVIRONMENT = (
    "VIRTUAL_ENV",
    "ONEPIPELINE_FLOW",
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)


class Ending(IntEnum):
    """The `watch --flow` endings this journey reaches, by exit status."""

    ELAPSED = 5
    RUN_JOINED = 8
    FLOW_FAILED = 9

    @property
    def condition(self) -> str:
        """The condition word the watch's own JSON ending names beside that status."""
        return self.name.lower().replace("_", "-")


#: Bounds the re-arming while the flow checks its plan, which a real flow does in seconds.
MOST_WAKES = 20

#: The receipt the recipe prints first, naming the flow and the recipe that watches it.
RECEIPT = re.compile(
    r"^flow (\S+): watch it with: just watch --flow (\S+)$",
    re.M,
)


# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The producer is the harness, whose
# Stop payload exists on this host only as a process handing one to a hook; the guard's own
# `--help` names the two keys it reads, which `tests/unwatched/test_unwatched_and_stop_hook_e2e.py`
# reconciles for the same shape.
class StopPayload(TypedDict):
    """The Stop payload the harness hands a hook, in the fields this journey sends."""

    session_id: SessionId
    transcript_path: str
    hook_event_name: str
    stop_hook_active: bool


class Copy(NamedTuple):
    """The copied checkout the flow runs in, its engine doubled."""

    checkout: Path


#: A run's id, and a flow's: two namespaces the engine keeps apart.
RunId = NewType("RunId", str)
FlowId = NewType("FlowId", str)
#: Where a watch's ending says to re-arm from, which only a later watch reads.
Cursor = NewType("Cursor", str)


class Flow(NamedTuple):
    """One planning flow in flight: its names, environment and the files that steer it."""

    run: RunId
    environment: dict[str, str]
    runs: Path
    review_holding: Path
    review_release: Path
    spikes_release: Path


class Wake(NamedTuple):
    """One watch the manager armed and what woke it."""

    status: int
    #: The condition the watch's JSON ending named, which is what its exit status means.
    condition: str
    cursor: Cursor
    elapsed: float
    configured: float


@pytest.fixture(scope="module")
def copy(tmp_path_factory: pytest.TempPathFactory) -> Copy:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    if not ONEPIPELINE.is_file():
        pytest.skip(f"this checkout has no installed engine at {ONEPIPELINE}")
    checkout = tmp_path_factory.mktemp("flow-stop-hook") / "checkout"
    checkout.mkdir()
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--", *COPIED_TREES, *COPIED_FILES],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    for relative in filter(None, listed.split("\0")):
        source = REPO_ROOT / relative
        if not source.exists():
            continue
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    answering_this_checkouts_origin(checkout)
    provided = ("UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "UV_NO_SYNC")
    synced = subprocess.run(
        ["uv", "sync", "--locked"],
        cwd=checkout,
        env={
            **{name: value for name, value in os.environ.items() if name not in provided},
            **OFFLINE_INSTALLS,
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    assert synced.returncode == 0, f"the copy could not be provisioned:\n{synced.stderr}"
    engine = checkout / ".venv" / "bin" / "onepipeline"
    real = engine.with_name("onepipeline-real")
    engine.rename(real)
    # The double on its second line, then the real binary's bytes, which bash never reads:
    # `just check-plan` reads the roles a persona name resolves to out of the installed
    # engine's own bytes, as `just plan`'s spike journeys install it.
    engine.write_bytes(
        f'#!/usr/bin/env bash\nexec {sys.executable} {STAND_IN} "$@"\n'.encode() + real.read_bytes()
    )
    engine.chmod(0o755)
    return Copy(checkout)


def _flow(copy: Copy, tmp_path: Path) -> Flow:
    """The environment one `just plan` flow runs in, with its review and spikes held."""
    run = RunId(f"flow-stop-hook-{os.getpid()}")
    plan = f"{plan_fixture_source.SOURCE}:test-{os.getpid()}-flow-stop-hook-plan"
    identity = seeded(tmp_path / "identity", origin=PLANNING_FLOW_ORIGIN)
    environment = {
        name: value for name, value in os.environ.items() if name not in INHERITED_ENVIRONMENT
    }
    destination = tmp_path / "destination"
    destination.mkdir()
    bin_dir = copy.checkout / ".venv" / "bin"
    criteria = [
        "The listing pages at 2,000 nodes within the latency the spike measured as achievable.",
        "A request-level test drives the listing end to end and covers the last page.",
        "Every claim the dispatch makes about the finished work is true of the tree as it "
        "finally stands.",
    ]
    held = tmp_path / "held"
    held.mkdir()
    environment |= identity.environment | {
        "CLAUDE_CODE_SESSION_ID": SESSION,
        "ONEVCS_HOME": str(identity.home),
        "ONEPIPELINE_RUNS_DIR": str(tmp_path / "runs"),
        "ONEPIPELINE_WAKE_BUDGET": str(WAKE_BUDGET),
        "XDG_STATE_HOME": str(short_state.state_home(tmp_path)),
        "UV_NO_SYNC": "1",
        # llmlint: ignore[e2e_not_mocked] Only the paid reviewer's provider is substituted.
        "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
        # llmlint: ignore[e2e_not_mocked] Only the paid reviewer's provider is substituted.
        "PATH": f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}",
        "FAKE_CODEX_ANSWERS": json.dumps([PASSING_VERDICT]),
        "FAKE_CODEX_HOLD_SECONDS": str(e2e_timeout(300)),
        "FAKE_CODEX_HOLDING": str(held / "review-holding"),
        "FAKE_CODEX_HOLD_RELEASE": str(held / "review-release"),
        f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__PLUGIN": "local-md",
        f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__CONFIG__ROOT": str(destination),
        "ONETASKGRAPH_DEFAULT_SOURCES": f"authoring,{plan_fixture_source.SOURCE},{DESTINATION}",
        "FAKE_ENGINE_REAL": str(bin_dir / "onepipeline-real"),
        "FAKE_ENGINE_STORE": str(bin_dir / "onetaskgraph"),
        "FAKE_ENGINE_ONEVCS": str(bin_dir / "onevcs"),
        "FAKE_ENGINE_LOG": str(tmp_path / "starts.jsonl"),
        # llmlint: ignore[modern_domain_modeling] The scenario crosses a process boundary as
        # JSON in the environment, and the double types it on the far side as its own
        # `Scenario`, which this journey cannot import: it is a script beside another
        # project's journeys, run as a program, never a module on this suite's path.
        "FAKE_ENGINE_SCENARIO": json.dumps(
            {
                "run": run,
                "plan": plan,
                "root": str(plan_fixture_source.root()),
                "repository": PLANNING_FLOW_ORIGIN,
                "task": {
                    "what": "Page the node listing.",
                    "why": "An operator cannot see past the first screen.",
                    "acceptance_criteria": criteria,
                },
                "spike_task": {
                    "what": "Measure the listing at 2,000 nodes.",
                    "why": "The listing's budget needs a measured number.",
                    "acceptance_criteria": criteria,
                },
                "spikes": [f"spike-{os.getpid()}-listing"],
                "budgets": no_budgets([PLANNING_FLOW_ORIGIN]),
                "design": {},
                "hold": {
                    "suffix": "-spikes",
                    "release": str(held / "spikes-release"),
                    "seconds": e2e_timeout(300),
                },
            }
        ),
    }
    brief = tmp_path / f"{run}.md"
    brief.write_text(
        "## What\nPlan the paginated listing.\n\n"
        f"Plan project: {plan}\n\n"
        "## Why\nAn operator cannot see past the first screen.\n\n"
        "## Acceptance criteria\n- The plan pages the listing.\n",
        encoding="utf-8",
    )
    environment["FLOW_BRIEF"] = str(brief)
    return Flow(
        run,
        environment,
        tmp_path / "runs",
        held / "review-holding",
        held / "review-release",
        held / "spikes-release",
    )


def _until(what: str, ready: Callable[[], bool], *, seconds: float = 300) -> None:
    deadline = time.monotonic() + e2e_timeout(seconds)
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.2)
    raise AssertionError(f"waited for {what} and it did not happen")


def _written_terms(terms: Path) -> set[Path]:
    """The watch terms the engine has finished writing.

    The engine stages each terms file beside its final name and renames it into place, so
    a listing taken mid-write sees a file that is gone by the time it is read.
    """
    if not terms.is_dir():
        return set()
    return {path for path in terms.iterdir() if not is_staging_name(path.name)}


def _recipe(flow: Flow, *arguments: str) -> subprocess.CompletedProcess[str]:
    """One of this checkout's recipes, as the manager's session runs it."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=flow.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _hook(flow: Flow) -> subprocess.CompletedProcess[str]:
    """The registered `Stop` command, run as the harness runs it, for this session's turn."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    (command,) = [
        hook["command"]
        for matcher in settings["hooks"]["Stop"]
        for hook in matcher["hooks"]
        if hook["type"] == "command"
    ]
    payload: StopPayload = {
        "session_id": SESSION,
        "transcript_path": "/dev/null",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    return subprocess.run(
        ["/bin/sh", "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={**flow.environment, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
        cwd=REPO_ROOT,
        timeout=e2e_timeout(60),
    )


def _folded(flow: Flow) -> None:
    """Have the engine fold every run's summary, so the guard decides from current ones."""
    listed = subprocess.run(
        [str(ONEPIPELINE), "runs"],
        env=flow.environment,
        cwd=flow.runs,
        capture_output=True,
        text=True,
        check=False,
        timeout=e2e_timeout(60),
    )
    assert listed.returncode == 0, listed.stderr


def _blocked(flow: Flow) -> str:
    _folded(flow)
    answered = _hook(flow)
    assert answered.returncode == 0, answered.stderr
    decision = json.loads(answered.stdout) if answered.stdout.strip() else {}
    assert decision.get("decision") == "block", (
        f"the Stop hook let the turn end:\n{answered.stdout}{answered.stderr}"
    )
    return str(decision["reason"])


def _allowed(flow: Flow, why: str) -> None:
    _folded(flow)
    answered = _hook(flow)
    assert answered.returncode == 0, answered.stderr
    assert "block" not in answered.stdout, f"{why}, and the hook blocked:\n{answered.stdout}"


class Watching:
    """The manager's watches of one flow, each armed through the `watch` recipe and timed."""

    def __init__(self, flow: Flow, flow_id: FlowId, logs: Path) -> None:
        self.flow, self.flow_id, self.logs = flow, flow_id, logs
        self.wakes: list[Wake] = []
        self.process: subprocess.Popen[str] | None = None
        self.spawned = 0.0
        self.configured = 0.0

    def arm(self, cursor: Cursor | None = None) -> None:
        """Arm one watch, as the manager does, and wait until the engine has its terms."""
        log = self.logs / f"watch-{len(self.wakes)}.log"
        arguments = ["just", "watch", "--flow", self.flow_id, "--log", str(log)]
        if cursor is not None:
            arguments += ["--cursor", cursor]
        terms = self.flow.runs / ".flows" / self.flow_id / "watch-terms"
        before = _written_terms(terms)
        armed_at = datetime.now(UTC)
        self.spawned = time.monotonic()
        self.process = subprocess.Popen(
            arguments,
            cwd=REPO_ROOT,
            env=self.flow.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )

        process = self.process

        def recorded() -> bool:
            return bool(_written_terms(terms) - before)

        # A watch re-armed from a cursor something has already passed (a run that joined
        # between two watches) returns at once, before it records any terms; its ending is
        # then read by `woken` like any other, and it configured no deadline to report.
        _until(
            "the armed watch to record its terms or to end",
            lambda: recorded() or process.poll() is not None,
        )
        if not recorded():
            self.configured = 0.0
            return
        (written,) = _written_terms(terms) - before
        deadline = json.loads(written.read_text(encoding="utf-8"))["deadline"]
        assert deadline is not None, "the watch recipe armed a watch with no deadline"
        stamp = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
        self.configured = (stamp - armed_at).total_seconds()

    def woken(self) -> Wake:
        """Wait for the armed watch to return, and record what woke it and when."""
        assert self.process is not None
        printed, _ = self.process.communicate(timeout=e2e_timeout(120))
        elapsed = time.monotonic() - self.spawned
        status = self.process.returncode
        self.process = None
        returned = [json.loads(line) for line in printed.splitlines() if line.startswith("{")]
        assert returned, f"the watch exited {status} and printed no ending:\n{printed}"
        ending = returned[-1]
        wake = Wake(
            status,
            str(ending["condition"]),
            Cursor(str(ending["cursor"])),
            elapsed,
            self.configured,
        )
        self.wakes.append(wake)
        budget_telemetry.record(
            "flow-wake-gap",
            f"wake {len(self.wakes)}: {wake.condition}",
            elapsed / WAKE_BUDGET,
            unit="of the wake budget",
            combine="max",
            # The deadline is the watch's own terms, context for the observed figure only.
            seconds_observed=round(elapsed, 2),
            seconds_deadline_configured=round(wake.configured, 2),
        )
        return wake

    @staticmethod
    def ended(wake: Wake, ending: Ending) -> bool:
        """Whether ``wake`` is ``ending``: its exit status and the engine's own word for it."""
        return wake.status == ending and wake.condition == ending.condition

    def stop(self) -> None:
        if self.process is not None:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=e2e_timeout(60))


@contextmanager
def _planning(copy: Copy, tmp_path: Path) -> Iterator[tuple[Flow, subprocess.Popen[str], Path]]:
    """One real `just plan` launched in the copy, killed with everything it started after."""
    flow = _flow(copy, tmp_path)
    stderr = tmp_path / "plan.stderr"
    with stderr.open("w", encoding="utf-8") as sink:
        launched = subprocess.Popen(
            [
                "just",
                "plan",
                flow.environment["FLOW_BRIEF"],
                "--name",
                flow.run,
                "--to",
                DESTINATION,
            ],
            cwd=copy.checkout,
            env=flow.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=sink,
            text=True,
            start_new_session=True,
        )
    try:
        yield flow, launched, stderr
    finally:
        flow.review_release.touch()
        flow.spikes_release.touch()
        if launched.poll() is None:
            os.killpg(launched.pid, signal.SIGKILL)
        launched.wait(timeout=e2e_timeout(60))


def test_a_planning_flow_holds_the_session_between_its_runs_until_it_is_closed(
    copy: Copy, tmp_path: Path
) -> None:
    """The guard owes a live flow a watch, a flow watch covers its runs, and a death is closed.

    The flow sits in its review step with its draft run settled and closed and no run
    live, which is exactly where a session that owed only runs used to end its turn.
    """
    with _planning(copy, tmp_path) as (flow, launched, stderr):
        _held_until_closed(flow, launched, stderr, tmp_path)


def _held_until_closed(
    flow: Flow, launched: subprocess.Popen[str], stderr: Path, tmp_path: Path
) -> None:
    """Drive one flow from its receipt to its closure, asking the guard at each state."""

    def receipt() -> re.Match[str] | None:
        return RECEIPT.search(stderr.read_text(encoding="utf-8"))

    _until("the recipe to name its flow", lambda: receipt() is not None)
    found = receipt()
    assert found is not None
    flow_id = FlowId(found.group(1))
    assert found.group(2) == flow_id, found.group(0)
    first = stderr.read_text(encoding="utf-8").splitlines()[:2]
    assert first == [
        f"flow {flow_id}: watch it with: onepipeline watch --flow {flow_id}",
        found.group(0),
    ], f"the recipe's first lines do not name its flow and the watch for it:\n{first}"

    # The draft run settles; the flow moves on into its review, where no run is live.
    _until(
        "the flow to reach its review step or end",
        lambda: flow.review_holding.is_file() or launched.poll() is not None,
    )
    assert flow.review_holding.is_file(), (
        f"the flow ended before its review:\n{stderr.read_text(encoding='utf-8')}"
    )
    held_from = time.monotonic()
    draft = json.loads((flow.runs / flow.run / "launch.json").read_text(encoding="utf-8"))
    assert draft["flow"] == flow_id, draft
    assert (flow.runs / flow.run / "result.json").is_file(), "the draft run has not settled"
    closed = _recipe(
        flow, "unwatched", "--acknowledge", flow.run, "--reason", "the draft run settled"
    )
    assert closed.returncode == 0, closed.stdout + closed.stderr

    # Nothing watches the flow: the turn may not end, and the guard says what to arm.
    reason = _blocked(flow)
    assert f"flow {flow_id}" in reason, reason
    assert f"watch --flow {flow_id}" in reason, reason
    assert flow.run not in reason.replace(flow_id, ""), (
        f"the closed draft run is still owed:\n{reason}"
    )

    watching = Watching(flow, flow_id, tmp_path)
    try:
        watching.arm()
        _allowed(flow, "a watch of the flow under the wake budget is armed")
        # Held across two and a half budgets, every watch armed in the review ends on its
        # deadline and is re-armed from its cursor, so the session is woken throughout.
        cursor = watching.woken().cursor
        while time.monotonic() - held_from < HELD_BUDGETS * WAKE_BUDGET:
            watching.arm(cursor)
            wake = watching.woken()
            assert Watching.ended(wake, Ending.ELAPSED), f"a watch in the review step ended {wake}"
            cursor = wake.cursor
        assert sum(Watching.ended(wake, Ending.ELAPSED) for wake in watching.wakes) >= 2, (
            watching.wakes
        )
        assert launched.poll() is None, "the flow ended while it was held in its review"

        # The review ends and the flow checks its plan, then launches the spikes run: the
        # watch armed across that wakes for the run, re-armed on every deadline before it.
        watching.arm(cursor)
        flow.review_release.touch()
        joined = watching.woken()
        while Watching.ended(joined, Ending.ELAPSED) and len(watching.wakes) < MOST_WAKES:
            watching.arm(joined.cursor)
            joined = watching.woken()
        assert Watching.ended(joined, Ending.RUN_JOINED), f"the new run woke the watch: {joined}"
        spikes = flow.runs / f"{flow.run}-spikes" / "launch.json"
        assert json.loads(spikes.read_text(encoding="utf-8"))["flow"] == flow_id

        # Re-armed from its cursor, the flow's watch covers the run it joined.
        watching.arm(joined.cursor)
        _allowed(flow, "the flow's watch covers the spikes run while the flow lives")

        # The flow dies: the watch says so, and the guard owes its closure.
        os.killpg(launched.pid, signal.SIGKILL)
        launched.wait(timeout=e2e_timeout(60))
        died = watching.woken()
        assert Watching.ended(died, Ending.FLOW_FAILED), f"the flow's death woke it: {died}"
    finally:
        watching.stop()

    reason = _blocked(flow)
    assert f"flow {flow_id}" in reason and "DIED" in reason, reason
    assert f"--acknowledge-flow {flow_id}" in reason, reason
    refused = _recipe(flow, "unwatched", "--acknowledge-flow", flow_id, "--reason", "")
    assert refused.returncode != 0, "a blank reason closed the flow"
    acknowledged = _recipe(
        flow, "unwatched", "--acknowledge-flow", flow_id, "--reason", "the journey killed it"
    )
    assert acknowledged.returncode == 0, acknowledged.stdout + acknowledged.stderr
    # Its run is judged on its own once the flow is gone; closing it leaves nothing owed.
    spikes_run = f"{flow.run}-spikes"
    reason = _blocked(flow)
    assert f"flow {flow_id}" not in reason, reason
    assert spikes_run in reason, reason
    closed = _recipe(
        flow, "unwatched", "--acknowledge", spikes_run, "--reason", "its flow was killed"
    )
    assert closed.returncode == 0, closed.stdout + closed.stderr
    _allowed(flow, "the flow and every run of it are closed")
