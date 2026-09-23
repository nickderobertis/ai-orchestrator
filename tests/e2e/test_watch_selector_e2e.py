"""`just watch` over a live run: the heartbeat and the three endings only a live run reaches.

`tests/e2e/test_watch_recipe_e2e.py` is the other half and drives the same recipe over
the checked-in runs, which is where the two run-level endings, the ending line's cursor
and unread count, and the parser's refusals are held. A recorded run will not change
again, so a wait over one never runs out, no blocking surface is ever waiting, and a
watch that returns on its first pass is never silent long enough to write a heartbeat —
`surface-waiting`, `elapsed`, `node-settled` and the heartbeat are all out of reach of
it, because the engine proves a run is driven from its live driver process rather than
from anything a fixture could write. So a fixture here **starts a real run**, holds it
live, and leaves one unanswered blocking question on it, and those four are driven over
that through the recipe. This is where `AGENTS.md`'s requirement that every watch
heartbeat carry the unread-surface count is asserted, since it is the only place a
heartbeat exists.

The count is driven at both ends rather than at one. A recorded run reports nothing
unread and the recipe module asserts it says so as a zero; the live run here holds
exactly one unanswered question and is asserted to report a number. A check that accepted
either form at both ends would pass over a watch that had stopped counting, and the count
is what the watch rule calls a HARD REQUIREMENT.

`node-settled` is driven over that same run, and where it is reachable is the whole of
why it needs one. Two endings are checked before any condition a caller named and cannot
be skipped, though either can be named — a complete graph answers `settled`, a run
nothing is driving answers `nothing-driving` — so no recorded run can answer on a node at
all, every one of them being undriven. A *live* run can, and only while another node holds the graph
incomplete and its driver working. The plan below is two nodes for exactly that reason.

A waiting surface is **not** one of those two: that check is itself a selector, so it
ends a wait only when `surface` was asked for. A bare `--until node-settled` does not ask
for it, which is why the journey below returns on the node while this run's question is
still waiting.

The statuses are read from `AGENTS.md`'s watch rule through `tests/watch_rule.py` rather
than written here: the rule is the one statement a supervisor branches on, and
`tests/test_watch_surface_drift.py` holds it to the engine.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
repository runs one Nx project and splits its test tiers by pytest marker, which is a
settled design rather than an omission. Every test here is `reads_checkouts` — its
subject is the engine wheel installed under `.venv`, which lives outside every `nx.json`
key — so it belongs in the *uncached* `orchestrator:test-checkouts` target, where a memo
over this workspace would replay a green across the very upgrade this module exists to
catch. A project of its own would need a key over the same nothing.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] An *edge* is the same
demand as a key in different words, and these journeys are in the uncached tier for the
reason above: what they watch for is an installed producer moving, which no edge over
this tree can express, so there is no edge to put them behind. The live journeys are not
cheap — they spend one real launch — and that is bounded rather than waved past: the
launch is **one**, module-scoped and shared by both, its worker is a stand-in that
answers without doing any work, and the waits are seconds. Three readings a supervisor
depends on have no other way to be taken against the engine that will really answer
them, which is what that launch buys.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_backend import AGENT_DELAY_ENV, ASK_QUESTION_ENV, ASK_RECORD_ENV, ASK_TIMEOUT_ENV
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import project_from_plan

# The launch environment and the shape of a candidate plan both have one source, and it
# is the module that owns the launch journeys — the fake provider, the guarded PATH, the
# alternate-identity indirections, the isolated state root, and the plan model in the
# published schema's own field names. `tests/e2e/test_monitor_quiet_turn_e2e.py` reaches
# the first the same way and says why: copying its twenty lines is how a journey comes to
# run against a seam the rest of the suite has moved off, and a second plan model here
# would be the same thing one layer up.
from test_orchestrate_launch_e2e import CandidatePlan, _node
from test_orchestrate_launch_e2e import _environment as _launched_environment
from test_watch_recipe_e2e import (
    Heartbeat,
    ending_line,
    heartbeats,
    returned,
    unread_count,
    watch,
)
from waits import deadline
from waits import timeout as e2e_timeout
from watch_rule import rule

ROOT = Path(__file__).resolve().parents[2]

#: `reads_checkouts` for the reason the docstring gives, and one xdist group because the
#: live journeys below launch a run and then poll `just` recipes while it holds — every
#: one of those blocks on the `uv` lock that a journey re-provisioning this checkout
#: takes. `--dist loadgroup` co-locates one group *name* and says nothing about two, so
#: the module names the one the writers of that lock already name rather than a private
#: one, which would be as concurrent with them as declaring nothing.
pytestmark = [
    pytest.mark.reads_checkouts,
    pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP),
]

#: What the dispatched worker asks its manager, through the real
#: `scripts/ask-manager.sh`. The text is arbitrary; that a *blocking* surface exists and
#: nothing has answered it is the whole of what the journeys below need.
ASKED = "Which of the two bases should this node publish against?"
#: How long that question stays open. The wrapper waits this long for a reply that never
#: comes, and the run carries an unanswered blocking surface for exactly that window —
#: so it bounds the live state these journeys read rather than any one wait inside it.
#: Generous against a loaded host, and the fixture's teardown ends the run long before
#: it elapses.
QUESTION_HELD_SECONDS = 600
#: What a worker's turn does before it answers. Short, because it is what the *second*
#: node of the plan below spends before settling: that settlement is the state the
#: node-settled journey reads, and a long hold would only make the fixture slower to
#: reach it. The node holding the run open is held by its unanswered question instead,
#: for the whole window above.
WORKER_HELD_SECONDS = 5
#: How long a watch that is meant to run out is given. Small, because it is spent
#: waiting on purpose, and comfortably inside the window above.
ELAPSING_SECONDS = 6
#: The heartbeat clock those journeys ask for. One second, so a wait of the length above
#: is several intervals of silence rather than one.
TICK_SECONDS = 1
#: How long the live run is given to raise its question before a journey reads it.
SURFACE_SECONDS = 240


#: The plan the fixture launches: two nodes with nothing between them, dispatched
#: together. One of them claims the question — the stand-in lets exactly one turn of a
#: run ask, whichever reaches it first — and blocks on it for the window above, which is
#: what keeps the graph incomplete and its driver working. The other has nothing to wait
#: for and settles, which is the only way a live run can answer on a node at all: the
#: engine ranks a node settling below a complete graph and below nothing driving the
#: run, so the ending exists only while some *other* node is still holding the run open.
#:
#: Which of the two asks is a race and deliberately not decided here. Both journeys are
#: written about "a node", never about a named one, so the race decides nothing they
#: assert.
_TWO_NODES: CandidatePlan = {
    "schema_version": 2,
    "concurrency": 2,
    "tasks": [
        _node(
            id=name,
            task=(
                f"## What\nReport, as {name}.\n\n## Why\nSo the run has two nodes to "
                "schedule.\n\n## Acceptance criteria\n- Reported."
            ),
        )
        for name in ("first", "second")
    ],
}


class LiveRun(NamedTuple):
    """A launched run holding one unanswered blocking question, and what drives it."""

    environment: dict[str, str]
    run: str
    #: The attached launch. Held so the driver stays alive, and the only process this
    #: module signals: it is the one this module started, and its pid is captured here
    #: rather than matched for.
    launch: subprocess.Popen[str]


def _status(live: LiveRun) -> str:
    """`just status` for this run, cut at the boundary its own view embeds.

    That view is two documents in one stream — the run's lines, then `oneagentgraph
    health`'s JSON about the *host* — and `AGENTS.md`'s watch rule says to cut before
    looking for words in it. A journey polling for the unread-surface line is exactly a
    reader that would otherwise match the other subject.
    """
    reported = subprocess.run(
        ["just", "status", live.run],
        cwd=ROOT,
        env=live.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    whole = reported.stdout + reported.stderr
    return whole.split("\n  providers:", 1)[0]


@pytest.fixture(scope="module")
def live_run(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[LiveRun]:
    """Launch a run, hold it live, and leave one blocking question unanswered on it.

    Three of the wrapper's readings are about a run that is **live**, and no recorded run
    can be made to produce any of them: a wait over one never runs out, no surface is
    ever waiting, and a watch that returns on its first pass is never silent long enough
    to write a heartbeat. So this launches one.

    `--dag-graph off` deliberately: with no monitor and no pacemaker the only surface
    this run can raise is the worker's own question, which is what lets the journeys
    below read the unread count as a number rather than as whatever a supervisory member
    happened to say. The question is put through the real `scripts/ask-manager.sh` from
    inside the dispatched turn, which is where a real agent puts one — so the surface is
    blocking, unanswered, and *unabandoned*, the last of which is what the engine reads.

    Attached rather than detached, because a detached driver exits once the graph has
    nothing left to schedule and a run nothing is driving answers `nothing-driving` to
    every wait — which would replace all three readings with one.

    Module-scoped: the watches below only read, so one launch answers both, and a launch
    is the expensive thing here. Teardown kills the launch this fixture started, by the
    handle it started it with, and then ends the run through the supported verb.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("watch-selector-live")
    environment = _launched_environment(tmp_path, oneharness_bin)
    environment[AGENT_DELAY_ENV] = str(WORKER_HELD_SECONDS)
    environment[ASK_QUESTION_ENV] = ASKED
    environment[ASK_RECORD_ENV] = str(tmp_path / "asked.json")
    environment[ASK_TIMEOUT_ENV] = str(QUESTION_HELD_SECONDS)
    plan = tmp_path / "watch-live.plan.json"
    plan.write_text(json.dumps(_TWO_NODES), encoding="utf-8")
    project = project_from_plan(plan)
    run = project.split(":", 1)[1]
    launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
        ["just", "orchestrate", project, "--dag-graph", "off"],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    live = LiveRun(environment=environment, run=run, launch=launch)
    try:
        yield live
    finally:
        launch.kill()
        launch.wait(timeout=e2e_timeout(60))
        subprocess.run(
            ["just", "stop", run],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
            stdin=subprocess.DEVNULL,
        )


@pytest.fixture(scope="module")
def question_waiting(live_run: LiveRun) -> LiveRun:
    """The same run, once its worker's question is really on the channel.

    Waited for through `just status`, which is where an operator reads it and which
    prints the unread-surface line `AGENTS.md` forbids a watch to drop — so the
    precondition and one of the things under test are read from the same place, and a
    launch that never raised the question fails here naming what the view said instead
    of leaving a watch below to report the wrong ending for the right reason.
    """
    limit = deadline(SURFACE_SECONDS)
    seen = ""
    while time.monotonic() < limit:
        seen = _status(live_run)
        if "planner update(s) waiting" in seen:
            return live_run
        time.sleep(1.0)
    pytest.fail(f"run {live_run.run} never raised its worker's question:\n{seen}")


@pytest.fixture(scope="module")
def node_settled(question_waiting: LiveRun) -> LiveRun:
    """The same run, once one of its two nodes has settled and the other still holds it.

    Read out of the run's own merged stream through `just monitor`, which is a read-only
    view an operator uses, and waited for rather than assumed: the watch below returns on
    a settlement it finds *at or past its cursor*, so a journey that ran before one
    existed would be asserting about a wait that had nothing to answer.

    Depends on the question being up as well, because that is what holds the graph
    incomplete: a node settling is the ending the engine ranks last, so the moment the
    other node stops working the run either completes or stops being driven, and both of
    those outrank it.
    """
    limit = deadline(SURFACE_SECONDS)
    seen = ""
    while time.monotonic() < limit:
        streamed = subprocess.run(
            ["just", "monitor", question_waiting.run, "--all"],
            cwd=ROOT,
            env=question_waiting.environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
            stdin=subprocess.DEVNULL,
        )
        seen = streamed.stdout + streamed.stderr
        if "node-settled" in seen:
            return question_waiting
        time.sleep(1.0)
    pytest.fail(f"no node of run {question_waiting.run} ever settled:\n{seen}")


def test_a_node_settling_on_a_live_run_ends_the_watch_at_its_own_status(
    node_settled: LiveRun,
) -> None:
    """`node-settled`, over the only state that can produce it.

    A complete graph and a run nothing is driving are checked before any condition a
    caller named, so this ending is answerable only while another node keeps the run
    incomplete and its driver working. That is exactly what this run is: one node settled,
    one blocked on a question nobody has answered.

    The question does **not** stand in the way, and that is a property of the selector
    rather than an accident of timing: a waiting surface ends a wait only when `surface`
    was asked for, and `--until node-settled` does not ask for it.

    Which is why no recorded run reaches it and why this journey is worth its launch. The
    condition was accepted by the parser and its precedence driven before this, but the
    status the wrapper branches on for it came only from a doubled verb — so what the
    installed engine returns when a node really settles was, until here, this
    repository's own guess agreeing with itself.

    `--until node-settled` rather than a named node, because which of the two nodes
    claimed the question is a race the fixture deliberately does not decide.
    """
    watched = watch(
        node_settled.run,
        "--until",
        "node-settled",
        "--timeout",
        str(ELAPSING_SECONDS),
        environment=node_settled.environment,
    )

    assert returned(watched).condition == "node-settled", watched.said
    assert watched.status == rule().statuses["node-settled"], watched.said
    ended = ending_line(watched)
    assert ended["condition"] == "node-settled", watched.said
    assert ended["ending"] == f"node-settled {returned(watched).node}", watched.said


def test_a_blocking_question_on_a_live_run_ends_the_watch_at_its_own_status(
    question_waiting: LiveRun,
) -> None:
    """`surface-waiting`, over a run that really has one waiting.

    The condition a supervisor most needs and the one no recorded run can produce: a
    blocking question, unanswered, with its asker still listening. `AGENTS.md` says a
    blocking surface produces no other signal until somebody reads it — the run reports
    plain `ACTIVE` — so a watch that could not return on it would leave the question
    unread for as long as it stood, which is the silence this whole command exists
    against.

    The count is read off the engine's own ending line as well as the status, because
    that clause is the one thing the watch rule forbids dropping and a watch that
    returned on the surface while saying nothing about it would satisfy the status alone.
    """
    watched = watch(
        question_waiting.run,
        "--tick-interval",
        str(TICK_SECONDS),
        environment=question_waiting.environment,
    )

    assert returned(watched).condition == "surface-waiting", watched.said
    assert watched.status == rule().statuses["surface-waiting"], watched.said
    ended = ending_line(watched)
    counted = unread_count(ended["unread"])
    assert counted is not None and counted >= 1, (
        f"the watch returned on a blocking surface and its ending line reports no "
        f"unread planner surface: {ended.group(0)!r}"
    )


def test_a_wait_that_runs_out_on_a_live_run_reports_elapsed_and_says_so_as_it_waits(
    question_waiting: LiveRun,
) -> None:
    """`elapsed`, the heartbeat, and the unread count — the three a static root cannot give.

    `--until settled` is what makes this reachable beside the journey above: it waits
    *through* the blocking surface rather than returning on it, so with the run live, its
    driver attached and its worker held, nothing fires and the wait runs out. That is the
    only way to be in the state at all — a recorded run answers on its first pass, and a
    watch that returns at once is never silent long enough to write a heartbeat.

    All three are asserted from one watch because they are one reading: a supervisor
    watching a live run is owed a line per interval of silence, and every one of those
    lines is owed the number of planner updates nobody has read. Losing either half is
    what leaves silence and death indistinguishable from outside.
    """
    tick, wait = str(TICK_SECONDS), str(ELAPSING_SECONDS)

    watched = watch(
        question_waiting.run,
        "--until",
        "settled",
        "--tick-interval",
        tick,
        "--timeout",
        wait,
        environment=question_waiting.environment,
    )

    assert returned(watched).condition == "elapsed", watched.said
    assert watched.status == rule().statuses["elapsed"], watched.said
    beats = heartbeats(watched)
    assert beats, (
        f"a {wait}s wait at a {tick}s tick over a live run wrote no heartbeat, so from "
        f"outside it is indistinguishable from a watch that died:\n{watched.said}"
    )
    recorded = [record for record in watched.records if isinstance(record, Heartbeat)]
    assert len(recorded) == len(beats), watched.said
    for line, record in zip(beats, recorded, strict=True):
        counted = unread_count(line)
        assert counted is not None and counted >= 1, (
            f"this run holds an unanswered blocking question and a heartbeat of the "
            f"watch over it reports no unread planner surface: {line!r}"
        )
        assert record.unread == counted, (line, record)
