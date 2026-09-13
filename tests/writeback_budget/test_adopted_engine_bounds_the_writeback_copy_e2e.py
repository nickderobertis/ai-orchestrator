"""The adopted engine allows a settlement copy the deadline its items earn, on a real run.

The write-back is what keeps a plan's board in step with its run, and before
https://github.com/nickderobertis/onepipeline/pull/248 the store command it copies
through was killed at a fixed sixty seconds however many items it was writing — so
`root-causes-539-fixes` settled 18/18 with its board silently behind it, the 34-item copy
having outgrown the minute. That landing allows the copy `max(60, per-item budget × items)`
seconds, ten per item unless a launch says otherwise, and this host adopts the shipped
default. `tests/test_adopted_engine_carries_this_plan.py` holds that the adopted release's
history *contains* the landing; this journey holds that the installed engine *does* it,
and it fails on the engine before it, whose driver kills the same copy at sixty seconds.

Nothing about a quick copy tells the two engines apart, so the plan-store CLI the engine
spawns is stood in front of: `held_onetaskgraph.py` beside this journey holds a `project copy`
before handing it to the real store, and records when each call started and whether it
answered. A copy is only killed by a driver that is still alive to kill it, so the run is
kept driven for the whole journey: one agent node is dispatched through the real recipe
and its turn held open by the suite's stand-in backend — no provider turn is spent, and the
paid-provider guard stays first on `PATH` — with nine nodes waiting behind it. That is ten
items, and a deadline of a hundred seconds. Two copies are measured, off the run's own
records:

* one held past the sixty-second floor and inside the computed deadline answers while the
  driver is alive, and the driver reports no failure — where the engine before this one
  kills it at sixty and says so;
* one held past the computed deadline is killed, and the refusal the driver writes and the
  surface it raises both state the deadline and the arithmetic it came from: ten items at
  the launch record's budget.

The stand-in is named by `ONETASKGRAPH_BIN` rather than found on `PATH`, because the
wrapper runs the engine under `uv run`, which puts this checkout's `.venv/bin` — and the
real `onetaskgraph` in it — ahead of anything a caller's `PATH` names.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple, TypeVar

import pytest
from fake_backend import AGENT_DELAY_ENV
from harness_indirections import established_indirections
from held_onetaskgraph import HOLD_ENV, LOG_ENV, REAL_ENV
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import helper, project_from_plan
from published_tools import ONETASKGRAPH_BIN
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The paid provider's stand-ins and the guard over the identities `ONEHARNESS_BIN_*`
#: cannot reach, reached through `helper` so a stand-in this checkout does not have fails
#: here rather than falling through to a real identity.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The plan-store stand-in this project owns, beside this journey. Checked here rather
#: than trusted, because a stand-in the engine cannot spawn fails the launch's plan read
#: with a message about the store rather than about the missing file.
HELD_STORE = Path(__file__).resolve().parent / "held_onetaskgraph.py"
assert HELD_STORE.is_file(), f"the plan-store stand-in is missing at {HELD_STORE}"

#: The session these launches run under, stated rather than inherited: this suite runs
#: inside a dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-writeback-copy-deadline"

#: Every launcher variable an outer dispatch may have exported, and the launch-level
#: budget, which would measure that budget rather than the default this host adopts.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_WRITEBACK_ITEM_BUDGET",
)

#: The node whose dispatch keeps the run driven, and the nodes waiting behind it. With it
#: they are the ten items every copy writes.
HELD_NODE = "work"
WAITING_NODES = tuple(f"later-{index}" for index in range(1, 10))
ITEMS = 1 + len(WAITING_NODES)

#: The fixed backstop every store command ran under before the landing, and still the
#: floor under a copy's deadline.
FLOOR_SECONDS = 60
#: The per-item budget a launch naming none runs under, as the engine ships it.
SHIPPED_ITEM_BUDGET_SECONDS = 10
COMPUTED_DEADLINE_SECONDS = ITEMS * SHIPPED_ITEM_BUDGET_SECONDS

#: Past the floor by a margin no scheduling jitter closes, and inside the computed
#: deadline by more than the real copy of ten items takes to answer after its hold.
LANDING_HOLD_SECONDS = FLOOR_SECONDS + 15
#: Past the computed deadline, so the engine's kill arrives inside the hold.
REFUSED_HOLD_SECONDS = COMPUTED_DEADLINE_SECONDS + 30
#: How long the held node's turn is kept open: longer than everything this journey waits
#: on, so the driver never runs out of work before the journey does.
TURN_HELD_SECONDS = 1800

PATIENCE_SECONDS = 120

#: `tests/e2e/nx_workspace.py`'s group: every step here is a `just` recipe blocking on
#: `uv run`, which waits on the lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

Found = TypeVar("Found")


class DrivenRun(NamedTuple):
    """A live, driven run whose plan-store copies pass through the hold."""

    environment: dict[str, str]
    run: str
    root: Path
    hold: Path
    calls: Path


class CopyCall(NamedTuple):
    """One plan-store call, as `held_onetaskgraph.py` beside this journey recorded it.

    Parsed from the line the stand-in wrote, and refused when a line does not have the
    shape that file writes: the journey's verdicts rest on which process answered and
    when, so a record that lost a field is a broken stand-in, not a call that never
    answered.
    """

    pid: int
    at: float
    event: str
    verb: str
    held: int
    exit: int | None

    @classmethod
    def parse(cls, line: str) -> CopyCall:
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"a plan-store call record is not an object: {line!r}")
        pid, at, event, verb, held = (
            record.get("pid"),
            record.get("at"),
            record.get("event"),
            record.get("verb"),
            record.get("held"),
        )
        answer = record.get("exit")
        if not (
            isinstance(pid, int)
            and isinstance(at, (int, float))
            and event in {"started", "answered"}
            and isinstance(verb, str)
            and isinstance(held, int)
            and (answer is None or isinstance(answer, int))
        ):
            raise ValueError(f"a plan-store call record is malformed: {line!r}")
        if (event == "answered") != (answer is not None):
            raise ValueError(f"only an answered call carries an exit status: {line!r}")
        return cls(pid=pid, at=float(at), event=event, verb=verb, held=held, exit=answer)


class SurfaceLine(NamedTuple):
    """The two fields this journey reads off one line of the run's surface log."""

    kind: str
    message: str

    @classmethod
    def parse(cls, line: str) -> SurfaceLine:
        entry = json.loads(line)
        if not isinstance(entry, dict):
            raise ValueError(f"a surface log line is not an object: {line!r}")
        kind, message = entry.get("kind"), entry.get("message", "")
        if not isinstance(kind, str) or not isinstance(message, str):
            raise ValueError(f"a surface log line carries no string kind and message: {line!r}")
        return cls(kind=kind, message=message)


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 180, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _task(what: str) -> str:
    return f"## What\n{what}\n\n## Why\nKeep the run driven.\n\n## Acceptance criteria\n- Done."


def _environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment the launch, its stand-ins, and every read of its run share.

    What is substituted, and where: the paid model, at the `oneharness` seam, with the
    guard first on `PATH` for the identities that seam cannot reach; and the plan store,
    through the engine's documented `ONETASKGRAPH_BIN` seam, by a wrapper that runs the
    real `onetaskgraph` for every call and adds only elapsed time. The recipe, its wrapper,
    the engine, its driver and write-back worker, and the store on disk are the real ones.
    """
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    # The real CLI the provider stand-in hands a turn to once its hold ends.
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(__name__))
    environment[AGENT_DELAY_ENV] = str(TURN_HELD_SECONDS)
    environment["ONETASKGRAPH_BIN"] = str(HELD_STORE)
    environment[REAL_ENV] = str(ONETASKGRAPH_BIN)
    return environment


def _waited_for(what: str, look: Callable[[], Found | None], seconds: float) -> Found:
    """Poll `look` until it answers something, or fail naming what never arrived."""
    limit = deadline(seconds)
    while True:
        found = look()
        if found is not None:
            return found
        assert time.monotonic() < limit, f"{what} never arrived within {seconds} seconds"
        time.sleep(0.5)


@pytest.fixture
def driven(
    tmp_path: Path, request: pytest.FixtureRequest, oneharness_bin: str
) -> Iterator[DrivenRun]:
    """Launch the plan with its first copy behind the hold, and stop the run however it ends."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    assert ONETASKGRAPH_BIN.is_file(), (
        f"this checkout's own onetaskgraph is missing at {ONETASKGRAPH_BIN} — run 'just bootstrap'"
    )
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-30:].strip("-")
    run = f"copy-deadline-{os.getpid()}-{named}"
    environment = _environment(tmp_path, oneharness_bin)
    hold = tmp_path / "copy.hold"
    calls = tmp_path / "plan-store-calls.jsonl"
    environment[HOLD_ENV] = str(hold)
    environment[LOG_ENV] = str(calls)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "keep a run driven while its settlement copies are timed"},
                "name": run,
                "tasks": [
                    {"id": HELD_NODE, "persona": "engineer", "task": _task("Report.")},
                    *(
                        {
                            "id": node,
                            "persona": "engineer",
                            "deps": [HELD_NODE],
                            "task": _task("Report."),
                        }
                        for node in WAITING_NODES
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    project = project_from_plan(plan)
    # The first copy the driver makes is the one held past the floor.
    hold.write_text(str(LANDING_HOLD_SECONDS), encoding="utf-8")
    launch = _just(
        "orchestrate",
        project,
        "--dag-graph",
        "off",
        "--detach",
        environment=environment,
        seconds=600,
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    try:
        yield DrivenRun(environment, run, tmp_path / "runs" / run, hold, calls)
    finally:
        hold.unlink(missing_ok=True)
        _just("stop", run, environment=environment, seconds=120)


def _calls(driven: DrivenRun) -> list[CopyCall]:
    """Every plan-store call the engine made, as the hold recorded them."""
    if not driven.calls.is_file():
        return []
    return [
        CopyCall.parse(line)
        for line in driven.calls.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _copy_held_for(driven: DrivenRun, seconds: int) -> CopyCall | None:
    """The first `project copy` that started under a hold of `seconds`."""
    return next(
        (
            call
            for call in _calls(driven)
            if call.event == "started" and call.verb == "project copy" and call.held == seconds
        ),
        None,
    )


def _answer_to(driven: DrivenRun, started: CopyCall) -> CopyCall | None:
    """The answer the call that `started` records gave, once it gave one."""
    return next(
        (call for call in _calls(driven) if call.event == "answered" and call.pid == started.pid),
        None,
    )


def _driver_log(driven: DrivenRun) -> str:
    """What the run's driver wrote to its own log."""
    log = driven.root / "driver.log"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


def _landed_or_refused(driven: DrivenRun, started: CopyCall) -> CopyCall | None:
    """The held copy's answer, or a failure quoting the driver the moment it refused it.

    Failing on the driver's own report rather than waiting out the whole deadline, so a
    copy killed at a fixed floor is named as that: the driver's line states the deadline it
    applied, which is the evidence the refusal is about.
    """
    reported = _driver_log(driven)
    assert "write-back failed" not in reported, (
        f"the copy held {LANDING_HOLD_SECONDS} seconds past the {FLOOR_SECONDS} second floor "
        f"was killed under what should be a deadline of {ITEMS} items × "
        f"{SHIPPED_ITEM_BUDGET_SECONDS} seconds:\n{reported}"
    )
    return _answer_to(driven, started)


def _refusal_surface(driven: DrivenRun, refusal: str) -> str | None:
    """The finding the run raised that names `refusal`, off the run's own surface log."""
    log = driven.root / "channel" / "surfaces.jsonl"
    if not log.is_file():
        return None
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = SurfaceLine.parse(line)
        if entry.kind == "finding" and refusal in entry.message:
            return entry.message
    return None


def test_a_copy_is_allowed_the_deadline_its_items_earn_and_refused_only_past_it(
    driven: DrivenRun,
) -> None:
    """Held past the floor it lands; held past items × budget it is killed, saying so.

    Under the engine before the landing the first copy is killed at sixty seconds and the
    driver writes `project-copy exceeded 60 seconds`, so the journey fails there, quoting
    that line.
    """
    landing = _waited_for(
        "the driver's first copy",
        lambda: _copy_held_for(driven, LANDING_HOLD_SECONDS),
        PATIENCE_SECONDS,
    )
    driven.hold.unlink()
    answered = _waited_for(
        f"an answer from the copy held {LANDING_HOLD_SECONDS} seconds",
        lambda: _landed_or_refused(driven, landing),
        COMPUTED_DEADLINE_SECONDS + PATIENCE_SECONDS,
    )
    assert answered.exit == 0, answered
    assert answered.at - landing.at > FLOOR_SECONDS, (landing, answered)
    # The driver that could have killed the copy was alive to do it: the run is still
    # driven, its held node still in flight.
    status = _just("status", driven.run, environment=driven.environment, seconds=60)
    assert status.returncode == 0, status.stdout + status.stderr
    assert "DRIVER DEAD" not in status.stdout, status.stdout
    assert "write-back failed" not in _driver_log(driven), _driver_log(driven)

    launch_record = json.loads((driven.root / "launch.json").read_text(encoding="utf-8"))
    assert launch_record["writeback_item_budget"] == SHIPPED_ITEM_BUDGET_SECONDS, (
        "the launch did not record the shipped per-item budget, so the deadline below is "
        f"not the one this host adopts: {launch_record.get('writeback_item_budget')!r}"
    )

    # A copy held past that deadline is killed, and the run says which deadline and why.
    # Parking a waiting node is what gives the driver a new snapshot to copy.
    driven.hold.write_text(str(REFUSED_HOLD_SECONDS), encoding="utf-8")
    parked = _just(
        "channel-reply",
        driven.run,
        environment=driven.environment,
        seconds=120,
        stdin=json.dumps(
            {
                "version": 2,
                "commands": [
                    {
                        "op": "cancel",
                        "id": WAITING_NODES[0],
                        "reason": "parked so the run has a new snapshot to project",
                    }
                ],
            }
        ),
    )
    assert parked.returncode == 0, parked.stdout + parked.stderr
    refused = _waited_for(
        f"a copy held {REFUSED_HOLD_SECONDS} seconds",
        lambda: _copy_held_for(driven, REFUSED_HOLD_SECONDS),
        PATIENCE_SECONDS,
    )
    refusal = (
        f"project-copy exceeded {COMPUTED_DEADLINE_SECONDS} seconds "
        f"({ITEMS} items × {SHIPPED_ITEM_BUDGET_SECONDS} seconds per item)"
    )
    _waited_for(
        "the driver's report that the held copy was killed",
        lambda: refusal in _driver_log(driven) or None,
        COMPUTED_DEADLINE_SECONDS + PATIENCE_SECONDS,
    )
    driven.hold.unlink(missing_ok=True)
    surface = _waited_for(
        "the finding the run raised about the killed copy",
        lambda: _refusal_surface(driven, refusal),
        PATIENCE_SECONDS,
    )
    assert f"items: {HELD_NODE}" in surface or HELD_NODE in surface, surface
    assert _answer_to(driven, refused) is None, (
        "the copy held past the computed deadline answered, so the engine never killed it: "
        f"{_calls(driven)}"
    )
