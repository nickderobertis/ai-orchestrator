"""A live run kept driven, with every plan-store call it makes passed through a recorder.

Both journeys in this project watch the settlement write-back of a real run, and a
write-back only runs while a driver is alive to run it. So both need the same run: one
agent node dispatched through the real `just orchestrate` and its turn held open by the
suite's stand-in backend — no provider turn is spent, and the paid-provider guard stays
first on `PATH` — with nodes waiting behind it; and the plan-store CLI the engine spawns
stood in front of by `held_onetaskgraph.py`, which records every call and hands it to the
real store. What each journey observes differs. How the run is launched, recorded, read
and stopped does not, and is written here once.

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
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, NewType

import pytest
import short_state
from fake_backend import AGENT_DELAY_ENV
from harness_indirections import established_indirections
from held_onetaskgraph import HOLD_ENV, LOG_ENV, REAL_ENV
from project_fixtures import helper, project_from_plan
from published_tools import ONETASKGRAPH_BIN
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A run's id, as `just orchestrate` names it and every run recipe takes it.
RunId = NewType("RunId", str)
#: A qualified plan-store project id, `<source>:<native-id>`.
ProjectId = NewType("ProjectId", str)
#: A plan node's id, as the plan and the run's journal spell it.
NodeId = NewType("NodeId", str)

#: The paid provider's stand-ins and the guard over the identities `ONEHARNESS_BIN_*`
#: cannot reach, reached through `helper` so a stand-in this checkout does not have fails
#: here rather than falling through to a real identity.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The plan-store stand-in this project owns. Checked here rather than trusted, because a
#: stand-in the engine cannot spawn fails the launch's plan read with a message about the
#: store rather than about the missing file.
HELD_STORE = Path(__file__).resolve().parent / "held_onetaskgraph.py"
assert HELD_STORE.is_file(), f"the plan-store stand-in is missing at {HELD_STORE}"

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

#: How long the held node's turn is kept open: longer than everything a journey here
#: waits on, so the driver never runs out of work before the journey does.
TURN_HELD_SECONDS = 1800

PATIENCE_SECONDS = 120

#: Where the engine appends one line per projection attempt, under the run's directory —
#: `cli::WRITEBACK_PROJECTIONS_FILE` in the adopted `onepipeline`.
PROJECTIONS_FILE = "writeback-projections.jsonl"

#: The source the engine's shadow project lives in, which is what a `--member` names.
SHADOW_SOURCE = "onepipeline-writeback"

#: The engine's `engine::CANCEL_GRACE_ENV`: how long a cancelled dispatch has to stop itself
#: before it is torn down. The shipped default is sized for a real turn to commit what it
#: has; a journey that cancels a held turn names a shorter one, because the stand-in takes
#: no redirection and the node settles only once the teardown has reaped it.
CANCEL_GRACE_ENV = "ONEPIPELINE_CANCEL_GRACE_SECONDS"
#: This checkout's bus configuration, which every read of a run's channel names.
BUS_CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"


class DrivenRun(NamedTuple):
    """A live, driven run whose plan-store calls pass through the recorder."""

    environment: dict[str, str]
    run: RunId
    project: ProjectId
    root: Path
    hold: Path
    calls: Path


class StoreCall(NamedTuple):
    """One plan-store call, as `held_onetaskgraph.py` recorded it.

    Parsed from the line the stand-in wrote, and refused when a line does not have the
    shape that file writes: a journey's verdicts rest on which process answered, when,
    and with what arguments, so a record that lost a field is a broken stand-in, not a
    call that never answered.
    """

    pid: int
    at: float
    event: str
    verb: str
    args: tuple[str, ...]
    held: int
    exit: int | None

    @classmethod
    def parse(cls, line: str) -> StoreCall:
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"a plan-store call record is not an object: {line!r}")
        pid, at, event, verb, arguments, held = (
            record.get("pid"),
            record.get("at"),
            record.get("event"),
            record.get("verb"),
            record.get("args"),
            record.get("held"),
        )
        answer = record.get("exit")
        if not (
            isinstance(pid, int)
            and isinstance(at, (int, float))
            and event in {"started", "answered"}
            and isinstance(verb, str)
            and isinstance(arguments, list)
            and all(isinstance(argument, str) for argument in arguments)
            and isinstance(held, int)
            and (answer is None or isinstance(answer, int))
        ):
            raise ValueError(f"a plan-store call record is malformed: {line!r}")
        if (event == "answered") != (answer is not None):
            raise ValueError(f"only an answered call carries an exit status: {line!r}")
        return cls(
            pid=pid,
            at=float(at),
            event=event,
            verb=verb,
            args=tuple(arguments),
            held=held,
            exit=answer,
        )

    @property
    def members(self) -> tuple[str, ...]:
        """Every task this call named with `--member`, in the order it named them."""
        return tuple(
            self.args[index + 1]
            for index, argument in enumerate(self.args[:-1])
            if argument == "--member"
        )


class Projection(NamedTuple):
    """One projection attempt, as the engine recorded it in the run's projection record.

    Only the fields a journey here reads, each checked against the shape the adopted
    engine's divergence entry 73 declares, so a record that moved is named as that rather
    than read as an attempt that carried nothing.
    """

    scope: str
    whole_because: str | None
    items: tuple[str, ...]
    outcome: str
    failure_class: str | None
    kind: str | None
    reason: str | None
    duration_ms: int
    #: The copy report's own `spent` object verbatim, and `None` where the destination
    #: meters nothing — every local Markdown one. Required present, so a record that
    #: stopped carrying it fails here instead of reading as a store that metered nothing.
    spent: dict[str, object] | None
    #: The copy report's action counts — `created`, `updated`, `unchanged`, `orphaned` —
    #: and the `reopened` the engine derives beside them, or `None` where no copy report
    #: was read. Required present for the same reason `spent` is.
    actions: dict[str, int] | None

    @classmethod
    def parse(cls, line: str) -> Projection:
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"a projection record is not an object: {line!r}")
        items = record.get("items")
        nullable = ("whole_because", "class", "kind", "reason")
        if not (
            record.get("scope") in {"whole", "members"}
            and record.get("outcome") in {"projected", "failed"}
            and isinstance(items, list)
            and all(isinstance(item, str) for item in items)
            and all(record.get(name) is None or isinstance(record[name], str) for name in nullable)
            and isinstance(record.get("duration_ms"), int)
            and "spent" in record
            and (record["spent"] is None or isinstance(record["spent"], dict))
            and "actions" in record
            and (
                record["actions"] is None
                or (
                    isinstance(record["actions"], dict)
                    and all(isinstance(count, int) for count in record["actions"].values())
                )
            )
        ):
            raise ValueError(f"a projection record is malformed: {line!r}")
        return cls(
            scope=record["scope"],
            whole_because=record.get("whole_because"),
            items=tuple(items),
            outcome=record["outcome"],
            failure_class=record.get("class"),
            kind=record.get("kind"),
            reason=record.get("reason"),
            duration_ms=record["duration_ms"],
            spent=record["spent"],
            actions=record["actions"],
        )


def just(
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


def reply(driven: DrivenRun, *commands: dict[str, object]) -> int:
    """Send one envelope of commands to the run through the real channel recipe.

    Answers the id the bus's receipt gave the envelope — a transport receipt, saying where
    the envelope went and not that the graph took it — which is what `outcome` reads the
    engine's decision by.
    """
    replied = just(
        "channel-reply",
        driven.run,
        environment=driven.environment,
        seconds=120,
        stdin=json.dumps({"version": 2, "commands": list(commands)}),
    )
    assert replied.returncode == 0, replied.stdout + replied.stderr
    receipt = json.loads(replied.stdout.splitlines()[-1])
    assert isinstance(receipt, dict) and receipt.get("queue") == "commands", replied.stdout
    identifier = receipt.get("id")
    assert isinstance(identifier, int), replied.stdout
    return identifier


def outcome(driven: DrivenRun, envelope: int) -> dict[str, object]:
    """The engine's answer to the command envelope `envelope`, read through the bus.

    Correlated by the id the envelope was sent under, so a journey reading two outcomes
    cannot read one twice; a `retry` the engine refused — one sent while the cancelled
    dispatch it names is still in flight — is answered `applied: false` with its reason,
    which is what a journey reads before it waits on a projection that will never come.
    """

    def look() -> dict[str, object] | None:
        read = subprocess.run(
            [
                str(REPO_ROOT / ".venv" / "bin" / "onemessagebus"),
                "status",
                "command-outcomes",
                "--config",
                str(BUS_CONFIG),
                "--transport-dir",
                str(driven.root / "channel"),
            ],
            cwd=REPO_ROOT,
            env=driven.environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert read.returncode == 0, read.stdout + read.stderr
        (queue,) = json.loads(read.stdout)
        settled = [record for record in queue["waiting"] if record.get("id") == envelope]
        return settled[0] if settled else None

    return waited_for(f"the engine's outcome for envelope {envelope}", look, PATIENCE_SECONDS)


def apply(driven: DrivenRun, *commands: dict[str, object]) -> None:
    """Send one envelope and require the engine to have applied it."""
    decided = outcome(driven, reply(driven, *commands))
    assert decided.get("applied") is True, decided


def task(what: str) -> str:
    return f"## What\n{what}\n\n## Why\nKeep the run driven.\n\n## Acceptance criteria\n- Done."


def waited_for[Found](what: str, look: Callable[[], Found | None], seconds: float) -> Found:
    """Poll `look` until it answers something, or fail naming what never arrived."""
    limit = deadline(seconds)
    while True:
        found = look()
        if found is not None:
            return found
        assert time.monotonic() < limit, f"{what} never arrived within {seconds} seconds"
        time.sleep(0.5)


def _environment(tmp_path: Path, oneharness_bin: str, caller: str, session: str) -> dict[str, str]:
    """The environment the launch, its stand-ins, and every read of its run share.

    What is substituted, and where: the paid model, at the `oneharness` seam, with the
    guard first on `PATH` for the identities that seam cannot reach; and the plan store,
    through the engine's documented `ONETASKGRAPH_BIN` seam, by a wrapper that runs the
    real `onetaskgraph` for every call and adds only a record and, when asked, elapsed
    time. The recipe, its wrapper, the engine, its driver and write-back worker, and the
    store on disk are the real ones.
    """
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = session
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    # The real CLI the provider stand-in hands a turn to once its hold ends.
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(caller))
    environment[AGENT_DELAY_ENV] = str(TURN_HELD_SECONDS)
    environment["ONETASKGRAPH_BIN"] = str(HELD_STORE)
    environment[REAL_ENV] = str(ONETASKGRAPH_BIN)
    return environment


@contextmanager
def launched(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    oneharness_bin: str,
    *,
    caller: str,
    session: str,
    prefix: str,
    goal: str,
    held_node: NodeId,
    waiting_nodes: Sequence[NodeId],
    first_hold_seconds: int | None = None,
    independent_nodes: Sequence[NodeId] = (),
    cancel_grace_seconds: int | None = None,
) -> Iterator[DrivenRun]:
    """Launch a held node with `waiting_nodes` behind it, and stop the run however it ends.

    `first_hold_seconds`, when given, holds every `project copy` that starts before the
    journey moves the hold, which is how the first copy the driver makes is held.
    `independent_nodes` are roots of their own, dispatched and held beside `held_node`, so
    a journey that cancels or retries the held node keeps a driver alive through it.
    `cancel_grace_seconds` names the run's own cancel grace, for a journey that has to wait
    a cancelled dispatch out.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    assert ONETASKGRAPH_BIN.is_file(), (
        f"this checkout's own onetaskgraph is missing at {ONETASKGRAPH_BIN} — run 'just bootstrap'"
    )
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-30:].strip("-")
    run = RunId(f"{prefix}-{os.getpid()}-{named}")
    environment = _environment(tmp_path, oneharness_bin, caller, session)
    hold = tmp_path / "copy.hold"
    calls = tmp_path / "plan-store-calls.jsonl"
    environment[HOLD_ENV] = str(hold)
    environment[LOG_ENV] = str(calls)
    if cancel_grace_seconds is not None:
        environment[CANCEL_GRACE_ENV] = str(cancel_grace_seconds)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": goal},
                "name": run,
                "tasks": [
                    {"id": held_node, "persona": "engineer", "task": task("Report.")},
                    *(
                        {"id": node, "persona": "engineer", "task": task("Report.")}
                        for node in independent_nodes
                    ),
                    *(
                        {
                            "id": node,
                            "persona": "engineer",
                            "deps": [held_node],
                            "task": task("Report."),
                        }
                        for node in waiting_nodes
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    project = ProjectId(project_from_plan(plan))
    if first_hold_seconds is not None:
        hold.write_text(str(first_hold_seconds), encoding="utf-8")
    launch = just(
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
        yield DrivenRun(environment, run, project, tmp_path / "runs" / run, hold, calls)
    finally:
        hold.unlink(missing_ok=True)
        just("stop", run, environment=environment, seconds=120)


def store_calls(driven: DrivenRun) -> list[StoreCall]:
    """Every plan-store call the engine made, as the recorder wrote them."""
    if not driven.calls.is_file():
        return []
    return [
        StoreCall.parse(line)
        for line in driven.calls.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def copies_started(calls: Sequence[StoreCall]) -> list[StoreCall]:
    """The `project copy` calls among `calls`, one per call, as each started."""
    return [call for call in calls if call.event == "started" and call.verb == "project copy"]


def projections(driven: DrivenRun) -> list[Projection]:
    """Every projection attempt the run has recorded, in order."""
    record = driven.root / PROJECTIONS_FILE
    if not record.is_file():
        return []
    return [
        Projection.parse(line)
        for line in record.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def quiescent(driven: DrivenRun, *, quiet_seconds: float = 5.0) -> list[Projection]:
    """The run's projection record once no store call is in flight and nothing new arrives.

    Every started call answered, and neither the call log nor the record grew across
    `quiet_seconds`, so a journey reads the state a transition left rather than one an
    attempt still in flight is about to change.
    """

    def look() -> list[Projection] | None:
        calls, recorded = store_calls(driven), projections(driven)
        answered = {call.pid for call in calls if call.event == "answered"}
        if not recorded or any(
            call.pid not in answered for call in calls if call.event == "started"
        ):
            return None
        time.sleep(quiet_seconds)
        if len(store_calls(driven)) == len(calls) and len(projections(driven)) == len(recorded):
            return recorded
        return None

    return waited_for("the run's projections to go quiet", look, PATIENCE_SECONDS)


def driver_log(driven: DrivenRun) -> str:
    """What the run's driver wrote to its own log."""
    log = driven.root / "driver.log"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


def member_id(project: ProjectId, node: NodeId) -> str:
    """The shadow task a `--member` names for `node`, as the adopted engine spells it.

    `writeback::member_id` in `onepipeline`: the shadow source, then the project's
    qualified id and the node id, each as lowercase hex of its bytes.
    """
    return f"{SHADOW_SOURCE}:{project.encode().hex()}/{node.encode().hex()}"
