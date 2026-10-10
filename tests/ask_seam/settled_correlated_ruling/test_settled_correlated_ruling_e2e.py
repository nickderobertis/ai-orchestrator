"""A correlated ruling on a settled run reaches its asker, and leaves the run owed.

The stop hook tells a manager to close a settled run, and the questions still standing on
that run are answered by correlation: the monitor's end-of-run `monitor-completion`
question, non-blocking, raised through this host's own binding, and any blocking question
a dispatch asked through `scripts/ask-manager.sh`. Before onepipeline #667 the engine's
`onepipeline reply` took a `completion: true` verdict on a settled run as the run's own
completion request — so the asker never heard the ruling — or refused it. This journey
proves the adopted engine gives each ruling to its question instead.

One run, launched through `just orchestrate` and left settled with its driver exited.
Two correlated questions stand on it: a blocking one from the real `onepipeline ask`,
which `scripts/ask-manager.sh` execs, and a non-blocking `monitor-completion` from the
monitor's judge side, spawned exactly as `graphs/dag-scope.yaml` declares it under a real
`onejudge` conversation. The manager answers each one with `onepipeline reply
--correlation`, through `scripts/onepipeline.sh`, and a `completion: true` verdict that
carries a reason. Then:

* the verb exits 0 with the `delivered` receipt;
* each asker reads that ruling, so the dispatch's `ask` prints it and the conversation
  scores its bar with it;
* the run's journal gains one `planner-replied` per ruling and no `completion-requested`;
* `just unfinished` still lists the run, because answering a question is not closing it.

On an engine that swallows the ruling as the run's completion, the asker never hears it
and the journal records `completion-requested`. On an engine that refuses the ruling, the
verb does not exit 0. Either one fails here.

Only the paid model is doubled, at the `oneharness` seam, exactly as
`tests/ask_seam/channel_reply/test_channel_reply_e2e.py` doubles it. The run is read
through the engine's verbs and the bus's own `status`, never through its files, except
for the driver's pid, which the launch record holds; see `_until_the_driver_lets_go`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple, NewType, cast

import monitor_conversation
import pytest
import short_state
from monitor_conversation import Conversation, Taken
from nx_workspace import WORKSPACE_INSTALL_MARKS
from planner_channel import BUS_CONFIG
from project_fixtures import helper, project_from_plan
from test_observer_judge_ops import judge_argv
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT
from orchestrator.unfinished import UNWATCHED

#: The wrapper a dispatched agent asks through: it execs the engine's own `onepipeline ask`.
ASK_MANAGER = REPO_ROOT / "scripts" / "ask-manager.sh"

#: This repository's entry point to the installed engine, which `onepipeline reply` is
#: reached through. The `channel-reply` recipe is deliberately not used here, because the
#: verb under test is the engine's.
ONEPIPELINE_SH = REPO_ROOT / "scripts" / "onepipeline.sh"

#: The stand-in for the paid model and the provider binary beneath it, and the guard over
#: the identities `ONEHARNESS_BIN_*` cannot reach.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The observer graph and member whose judge side raises the non-blocking question.
DAG_SCOPE_GRAPH = "graphs/dag-scope.yaml"
MONITOR_MEMBER = "monitor"

#: The variables the engine names an observer member's run and asker with.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"
ASKER_ENV = "ONEPIPELINE_CHANNEL_ASKER"
MONITOR_ASKER = "dag-scope-monitor"

#: The kind the monitor's binding raises its completion bar under, and the kind
#: `onepipeline ask` raises a dispatch's question under.
COMPLETION_QUESTION = "monitor-completion"
ASKED_QUESTION = "planner-question"

#: A launching session this journey states rather than inherits, because the suite runs
#: inside a dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-settled-correlated-ruling"

#: Every name the enclosing dispatch's launcher identity, run and asker reach the verbs
#: through. A journey that left them set would be asking the *outer* run's channel.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: Launched settling and unwatched: no observer graph, because its monitor and pacemaker
#: would raise surfaces of their own on the same channel, and no run-end hooks, because a
#: follow-up launch is not this journey's subject.
SETTLES_UNWATCHED = ("--dag-graph", "off", "--success-hook=", "--failure-hook=")

#: The reply window each question is asked under: long enough that no asker gives up
#: before the manager answers, which would report the bus's `timeout` instead.
ASK_WINDOW_SECONDS = int(e2e_timeout(300))

#: The receipt a correlated verdict with no commands is answered with, by its keys.
DELIVERED_RECEIPT_KEYS = ["reply", "state", "verdict"]

#: The journal kinds this journey counts: the ruling journalled as the planner's reply,
#: and the run's completion request a ruling must never be.
PLANNER_REPLIED = "planner-replied"
COMPLETION_REQUESTED = "completion-requested"

#: A onepipeline run id, and the correlation the bus stamped on one question: two strings
#: distinguished by where they came from, so neither is passed where the other belongs.
RunId = NewType("RunId", str)
Correlation = NewType("Correlation", str)

#: Run ids this suite's own. Keyed on the checkout and this worker's process, because two
#: checkouts and two suites of one checkout run at once on this host.
SUITE = hashlib.sha256(f"{REPO_ROOT}\0{os.getpid()}".encode()).hexdigest()[:8]
RUN = RunId(f"settled-ruling-{SUITE}")

#: A reader of the toolchain this checkout provisioned: every step here runs its tool
#: through a `just` recipe or `uv run`, and no writer touches that toolchain, so
#: `tests/e2e/nx_workspace.py` asks no xdist group of it.
pytestmark = list(WORKSPACE_INSTALL_MARKS)


class Settled(NamedTuple):
    """A run that settled with its driver gone, and the environment its verbs run under."""

    environment: dict[str, str]
    run: RunId


def _environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment the launch and every verb against its run share."""
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment.pop("VIRTUAL_ENV", None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # The real CLI the stand-in delegates every turn to, with a scripted answer.
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    return environment


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
    )


def _engine(
    settled: Settled, *arguments: str, stdin: str | None = None, seconds: float = 120
) -> subprocess.CompletedProcess[str]:
    """Run one verb of the installed engine through this repository's entry point."""
    return subprocess.run(  # noqa: S603 - this repository's own onepipeline entry point
        ["bash", str(ONEPIPELINE_SH), *arguments],
        cwd=REPO_ROOT,
        env=settled.environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _settling_project(tmp_path: Path) -> str:
    """One agent node that reports and changes nothing, so the run settles on its own."""
    plan = tmp_path / f"{RUN}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "settle, leaving questions for a manager to rule on"},
                "name": RUN,
                "tasks": [
                    {
                        "id": "settled",
                        "task": "## What\nReport.\n\n## Why\nSettle the run.\n\n"
                        "## Acceptance criteria\n- Reported.",
                        "expects_no_diff": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project_from_plan(plan)


# llmlint: ignore-block[tests_mirror_real_usage] The one record read here is the driver's
# pid. Whether the driver *process* has exited is the premise of the journey, and no view
# answers it: `status` prints `SETTLED` in place of its liveness word once the work is
# complete, and `just orchestrate --adopt` would start a driver rather than report one.
# `tests/unwatched/test_unwatched_launch_shapes_e2e.py` reads the same field for the same
# reason.
def _until_the_driver_lets_go(settled: Settled, *, seconds: float = 300) -> None:
    """Wait for the process that drove the run to have exited.

    Only `ProcessLookupError` ends the wait. A `PermissionError` is a pid that exists under
    another user, which on this shared host is a reused pid, so treating it as gone would
    pass for the wrong reason.
    """
    record = Path(settled.environment["ONEPIPELINE_RUNS_DIR"]) / settled.run / "launch.json"
    driver = int(json.loads(record.read_text(encoding="utf-8"))["pid"])
    limit = deadline(seconds)
    while time.monotonic() < limit:
        try:
            os.kill(driver, 0)
        except ProcessLookupError:
            return
        time.sleep(0.5)
    raise AssertionError(f"the driver {driver} of run {settled.run} never exited")


# llmlint: ignore-end[tests_mirror_real_usage]


@pytest.fixture
def settled(tmp_path: Path, oneharness_bin: str) -> Settled:
    """A run launched through the real recipe, settled, with nothing driving it."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _environment(tmp_path, oneharness_bin)
    launch = _just(
        "orchestrate", _settling_project(tmp_path), *SETTLES_UNWATCHED, environment=environment
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    run = Settled(environment, RUN)
    _until_the_driver_lets_go(run)
    status = _engine(run, "status", RUN, "--no-providers")
    assert status.returncode == 0, status.stderr
    assert "SETTLED" in status.stdout, f"the run did not settle:\n{status.stdout}"
    return run


#: One event as `onepipeline monitor` renders it: when, which graph, then the kind.
RENDERED_EVENT = re.compile(r"^\S+\s+graph:\S+\s+(?P<kind>[a-z][a-z-]*)\b")


def _journal(settled: Settled) -> Counter[str]:
    """How many of each event kind the run's journal holds, read through `monitor --all`.

    `--all` reads every event in the store through no profile, so a kind no profile shows
    is still counted.
    """
    streamed = _engine(settled, "monitor", settled.run, "--all")
    assert streamed.returncode == 0, f"`onepipeline monitor` failed:\n{streamed.stderr}"
    kinds = Counter(
        rendered["kind"]
        for line in streamed.stdout.splitlines()
        if (rendered := RENDERED_EVENT.match(line))
    )
    assert kinds, f"`onepipeline monitor --all` rendered no event:\n{streamed.stdout}"
    return kinds


def _waiting(settled: Settled) -> list[dict[str, Any]]:
    """The surfaces the run's channel holds unread, as the bus's own `status` reads them.

    `status` claims nothing, so reading a question here leaves it exactly as it stands.
    """
    channel = Path(settled.environment["ONEPIPELINE_RUNS_DIR"]) / settled.run / "channel"
    read = subprocess.run(
        ["onemessagebus", "status", "surfaces", "--config", str(BUS_CONFIG)]
        + ["--transport-dir", str(channel)],
        cwd=REPO_ROOT,
        env=settled.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert read.returncode == 0, f"`onemessagebus status surfaces` failed:\n{read.stderr}"
    # `cast` for the reason `_journal` gives: the bus owns `status`'s shape.
    (state,) = cast(list[dict[str, Any]], json.loads(read.stdout))
    return cast(list[dict[str, Any]], state["waiting"])


def _question(
    settled: Settled, kind: str, asking: subprocess.Popen[str], *, seconds: float = 120
) -> dict[str, Any]:
    """Wait for the first unread question of `kind`, failing with what the asker said."""
    limit = deadline(seconds)
    while True:
        asked = [one for one in _waiting(settled) if one.get("kind") == kind]
        if asked:
            return asked[0]
        if asking.poll() is not None or time.monotonic() >= limit:
            stdout, stderr = _reaped(asking)
            raise AssertionError(
                f"no `{kind}` question reached run {settled.run}'s channel (the asker exited "
                f"{asking.returncode}):\n{stderr}\n{stdout[-2000:]}\n{_waiting(settled)}"
            )
        time.sleep(0.2)


def _reaped(started: subprocess.Popen[str]) -> tuple[str, str]:
    """Kill a process this journey started, wait for it, and hand back what it printed.

    Its whole group only where it leads one, which is how the asker is started; the
    conversation shares this suite's group, so signalling that group would end the suite.
    A process that already ended, or whose output was already read, hands back nothing.
    """
    with contextlib.suppress(ProcessLookupError):
        if os.getpgid(started.pid) == started.pid:
            os.killpg(started.pid, signal.SIGKILL)
        else:
            started.kill()
    try:
        return started.communicate(timeout=e2e_timeout(60))
    except (subprocess.TimeoutExpired, ValueError):
        return "", ""


def _ruled(settled: Settled, correlation: Correlation, ruling: dict[str, Any]) -> dict[str, Any]:
    """Answer one question by its correlation through `onepipeline reply`, and read the receipt."""
    answered = _engine(
        settled, "reply", settled.run, "--correlation", correlation, stdin=json.dumps(ruling)
    )
    assert answered.returncode == 0, (
        f"`onepipeline reply --correlation {correlation}` on a settled run exited "
        f"{answered.returncode}:\n{answered.stdout}{answered.stderr}"
    )
    # `cast` rather than a validating read: the receipt is the engine's, and each member is
    # asserted where the journey reads it.
    receipt = cast(dict[str, Any], json.loads(answered.stdout))
    assert sorted(receipt) == DELIVERED_RECEIPT_KEYS, receipt
    assert receipt["verdict"] == "delivered", receipt
    assert receipt["state"] == "delivered", receipt
    return receipt


BLOCKING_RULING = {"completion": True, "reason": "the key covers docs; ship it"}
MONITOR_RULING = {"completion": True, "reason": "the watch reported every drift it saw"}


def test_a_correlated_ruling_on_a_settled_run_reaches_its_asker_and_leaves_the_run_owed(
    settled: Settled, tmp_path: Path
) -> None:
    """Both questions a settled run can still hold are answered, and the run stays owed."""
    before = _journal(settled)
    assert before[COMPLETION_REQUESTED] == 0, before

    asking_environment = {**settled.environment, RUN_ID_ENV: settled.run}
    asking = subprocess.Popen(  # noqa: S603 - the real wrapper, as a dispatched agent runs it
        [str(ASK_MANAGER), "--timeout", str(ASK_WINDOW_SECONDS), "Should the key cover docs?"],
        cwd=REPO_ROOT,
        env=asking_environment,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    judging = monitor_conversation.start(
        Conversation(
            Taken("read the detailed stream"), judge_argv(DAG_SCOPE_GRAPH, MONITOR_MEMBER), 1
        ),
        {**settled.environment, RUN_ID_ENV: settled.run, ASKER_ENV: MONITOR_ASKER},
        tmp_path / "monitor",
    )
    try:
        blocking = _question(settled, ASKED_QUESTION, asking)
        assert blocking["blocking"] is True, blocking
        monitor = _question(settled, COMPLETION_QUESTION, judging)
        assert monitor["blocking"] is False, monitor
        assert monitor["correlation"] != blocking["correlation"], (blocking, monitor)

        _ruled(settled, Correlation(blocking["correlation"]), BLOCKING_RULING)
        _ruled(settled, Correlation(monitor["correlation"]), MONITOR_RULING)

        # Read before either asker is waited on, so an engine that swallowed the rulings as
        # the run's completion fails here, naming that, rather than as an asker's timeout.
        after = _journal(settled)
        assert after[COMPLETION_REQUESTED] == 0, (
            f"a question's ruling was journalled as the run asking to complete: {after}"
        )
        assert after[PLANNER_REPLIED] == before[PLANNER_REPLIED] + 2, (before, after)

        try:
            out, err = asking.communicate(timeout=e2e_timeout(120))
        except subprocess.TimeoutExpired as waited:
            raise AssertionError(
                "the asking agent was never handed the ruling its correlation named"
            ) from waited
        assert asking.returncode == 0, f"the asking agent read no ruling:\n{err}{out}"
        # The bus's own answer line, whose `reply.reply` is the ruling the manager sent.
        read_back = cast(dict[str, Any], json.loads(out))
        assert read_back["answer"] == "reply", read_back
        assert read_back["correlation"] == blocking["correlation"], read_back
        for field, value in BLOCKING_RULING.items():
            assert read_back["reply"]["reply"][field] == value, read_back

        held = monitor_conversation.finish(judging)
        assert held.completed.returncode == 0, held.completed.stderr
        assert (held.report or {})["verdicts"] == [
            {
                "criterion": monitor_conversation.DONE_WHEN,
                "kind": "boolean",
                "verdict": {"value": True, "reason": MONITOR_RULING["reason"]},
            }
        ], held.report
    finally:
        _reaped(asking)
        _reaped(judging)

    owed = _just(
        "unfinished", "--session", LAUNCHING_SESSION, environment=settled.environment, seconds=120
    )
    assert owed.returncode == UNWATCHED, f"{owed.stdout}{owed.stderr}"
    assert re.search(rf"\b{re.escape(settled.run)}\b", owed.stdout), (
        f"`just unfinished` no longer lists run {settled.run}, so answering its questions "
        f"closed it:\n{owed.stdout}{owed.stderr}"
    )
