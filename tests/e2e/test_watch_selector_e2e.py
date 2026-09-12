"""`just watch` against the engine this checkout installed, over runs it really recorded.

`tests/e2e/test_watch_recipe_e2e.py` is the other half and doubles the verb, because no
run can be made to produce every terminal condition on demand and because the wrapper's
rendering has to be provable whatever the engine writes. This half gives up that control
to buy the one thing a double cannot: the conditions a supervisor types reach a **real**
parser, over a **real** run, and what it refuses is refused here too.

Which is the half that matters for the selector. The engine declares its `--until`
vocabulary in its own source and renders the flag with a placeholder, so nothing in
`--help` says which spellings are read — a double would be this repository restating its
own guess and agreeing with it. `tests/test_watch_surface_drift.py` reconciles the table
the wrapper prints against that parser; this module drives the same question through the
recipe an operator actually types, and through the two refusals that make an accepted
condition mean anything.

**What a static runs root cannot produce, this module launches rather than delegates.**
Recorded runs will not change again, so a wait over one never runs out, no blocking
surface is ever waiting, and a watch that returns on its first pass is never silent long
enough to write a heartbeat — `surface-waiting`, `elapsed` and the heartbeat are all out
of reach of them. Those three used to be left to the doubled verb next door, which is a
statement about the wrapper's rendering and not about the engine: it proves the wrapper
branches on a status, never that the installed engine returns that status for that state.
So a fixture here **starts a real run**, holds it live, and leaves one unanswered
blocking question on it, and the three are driven over that.

The count on those lines is driven at both ends rather than at one. A recorded run
reports nothing unread and is asserted to say so as a zero; the live run holds exactly
one unanswered question and is asserted to report a number. A check that accepted either
form at both ends would pass over a wrapper that had stopped counting, and the count is
what `AGENTS.md`'s watch rule calls a HARD REQUIREMENT.

`node-settled` is driven over that same run, and where it is reachable is the whole of
why it needs one. Two endings are checked before any condition a caller named and cannot
be skipped, though either can be named — a complete graph answers `settled`, a run
nothing is driving answers `nothing-driving` — so no recorded run can answer on a node at
all, every one of them being undriven. A *live* run can, and only while another node holds the graph
incomplete and its driver working. The plan below is two nodes for exactly that reason.

A waiting surface is **not** one of those two: that check is itself a selector, so it
ends a wait only when `surface` was asked for. A bare `--until node-settled` does not ask
for it, which is why the journey below returns on the node while this run's question is
still waiting — and why its induced-failure form, which asks for `surface` beside it,
answers `surface-waiting` instead.

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

llmlint: ignore-file[tests_mirror_real_usage] The statuses and the cursor prefix are read
back from `scripts/watch-run.sh --print-surface` rather than written here, and that is
this repository's settled answer to the opposite rule: the wrapper is the one source of
which status means which condition, so a copy in this module would be a second statement
of that contract with nothing reconciling it, and it would go on asserting the old
meanings after the wrapper's moved. `tests/e2e/test_watch_recipe_e2e.py` reads it the
same way for the same reason. What is driven through the operator-facing command is every
behaviour under test; only the table the assertions are *compared against* comes from
that mode.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_backend import AGENT_DELAY_ENV, ASK_QUESTION_ENV, ASK_RECORD_ENV
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
from waits import deadline
from waits import timeout as e2e_timeout

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

#: The checked-in runs this drives the recipe over, so what the engine is asked is fixed
#: by this tree rather than by whatever this host happens to have run.
RECORDED_RUNS = ROOT / "tests" / "fixtures" / "timeline-runs"
#: The wrapper's own account of the surface it restates, for the vocabulary and the
#: statuses. Read rather than copied: `scripts/watch-run.sh` is the one source of both,
#: and a copy here would go on asserting the meanings it had before the wrapper moved.
WRAPPER = ROOT / "scripts" / "watch-run.sh"

#: A recorded run that settled complete, and the ending it answers with.
SETTLED_RUN = "gate-parity-2"
#: A recorded run nothing is driving, and the ending it answers with. It is also the run
#: whose graph holds several nodes, one of them settled `failed` — which is what makes a
#: condition naming that node answerable rather than refused.
UNDRIVEN_RUN = "triage-by-root-cause-2"
#: The node of that run a selector is allowed to name.
NAMED_NODE = "basis"
ABSENT_NODE = "no-such-node-in-this-graph"
UNKNOWN_CONDITION = "when-the-wind-changes"

#: How long the recipe is given. Every invocation here reads the store once, or is
#: refused before it reads at all, so this is a bound on a wedge rather than on a wait.
BOUND_SECONDS = 240


class Row(NamedTuple):
    """One row of the wrapper's own account of itself."""

    kind: str
    rest: str


def _surface() -> tuple[Row, ...]:
    reported = subprocess.run(
        [str(WRAPPER), "--print-surface"],
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    return tuple(
        Row(kind=kind, rest=rest)
        for kind, _, rest in (line.partition(" ") for line in reported.stdout.splitlines())
    )


def _statuses() -> dict[str, int]:
    """Each terminal condition the wrapper branches on, and the status it gives it."""
    found: dict[str, int] = {}
    for row in _surface():
        if row.kind == "status":
            code, _, condition = row.rest.partition(" ")
            found[condition] = int(code)
    return found


def _unbounded_wait() -> str:
    for row in _surface():
        if row.kind == "timeout-unbounded":
            return row.rest
    raise AssertionError("the wrapper's surface names no unbounded wait")


def _watch(
    run: str, *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """The real recipe, and nothing doubled.

    Over the checked-in runs root unless a caller names its own, which the live journeys
    below do: theirs is a run this module launched, and it lives under that launch's own
    isolated root rather than beside the recorded ones.
    """
    return subprocess.run(
        ["just", "watch", run, *arguments],
        cwd=ROOT,
        env=environment or {**os.environ, "ONEPIPELINE_RUNS_DIR": str(RECORDED_RUNS)},
        text=True,
        capture_output=True,
        timeout=BOUND_SECONDS,
        check=False,
        stdin=subprocess.DEVNULL,
    )


#: How many planner updates a heartbeat or a terminal line says are unread, out of the
#: clause the wrapper composes. Read back rather than matched whole, because the number
#: is the thing: `AGENTS.md`'s watch rule calls this line a HARD REQUIREMENT and what it
#: forbids losing is the count, not the sentence around it.
UNREAD_COUNT = re.compile(r"(\d+) planner update\(s\) unread")
#: The other form of the same clause, for a run with nothing waiting. Asserted beside the
#: count above and never instead of it: a check that accepted either would pass on a
#: watch that had stopped counting, which is the state this line exists to make visible.
NOTHING_UNREAD = "no planner update is unread"


def _rendered(kind: str, reported: str) -> list[str]:
    """Every line of one rendered kind the wrapper wrote — `heartbeat`, `terminal`."""
    return [line for line in reported.splitlines() if line.startswith(f"{kind}  ")]


class RecordedEnding(NamedTuple):
    """One ending a recorded run really produces, and the run that produces it."""

    run: str
    #: The engine's own word for it, which is also what the wrapper's table calls it.
    condition: str


#: Two of the five endings the wrapper branches on: `settled` and `nothing-driving` are
#: what a run that will not change again can answer, and `node-settled` is asked for by
#: name below.
RECORDED_ENDINGS = (
    RecordedEnding(run=SETTLED_RUN, condition="settled"),
    RecordedEnding(run=UNDRIVEN_RUN, condition="nothing-driving"),
)


@pytest.mark.parametrize("ending", RECORDED_ENDINGS, ids=lambda row: row.condition)
def test_each_ending_a_recorded_run_produces_is_reported_at_its_own_status(
    ending: RecordedEnding,
) -> None:
    """The recipe hands back the status the engine chose, over a run the engine read.

    This is the half `tests/e2e/test_watch_recipe_e2e.py` cannot answer: there the
    status is whatever the double was told to exit with, so what it proves is that the
    wrapper branches on the status rather than on prose. Here the status is the real
    engine's own verdict on a real run, which is what says the two tables still agree.
    """
    result = _watch(ending.run, "--timeout", "0")

    assert result.returncode == _statuses()[ending.condition], result.stdout + result.stderr
    assert ending.run in result.stdout + result.stderr


@pytest.mark.parametrize("ending", RECORDED_ENDINGS, ids=lambda row: row.condition)
def test_the_unread_surface_clause_reaches_the_caller_from_the_real_stream(
    ending: RecordedEnding,
) -> None:
    """The one signal the watch rule forbids filtering out, off the engine's own record.

    A count of zero is the point rather than a weakness: "nothing is unread" and "this
    watch stopped telling you" are the two states a supervisor most needs told apart, so
    the clause has to be *there* whatever the number is.

    **The zero is asserted as a zero**, which is what makes the live journeys below mean
    something: they read a positive count off a run holding one unanswered question, and
    a wrapper that had stopped counting altogether would satisfy a check that accepted
    either form at both ends. Read the pair together — nothing waiting here, one thing
    waiting there — and it is the count reaching the caller rather than the sentence.
    """
    result = _watch(ending.run, "--timeout", "0")

    assert result.returncode == _statuses()[ending.condition], result.stdout + result.stderr
    terminal = _rendered("terminal", result.stdout)
    assert len(terminal) == 1, result.stdout
    assert NOTHING_UNREAD in terminal[0], terminal[0]


#: The two node conditions, as a caller spells them: the word and the shape.
NODE_CONDITIONS = ("node-settled", f"node={NAMED_NODE}")


@pytest.mark.parametrize("condition", NODE_CONDITIONS)
def test_a_node_condition_is_accepted_and_does_not_displace_the_run_level_answer(
    condition: str,
) -> None:
    """Both node conditions reach the parser, are taken, and change no ending here.

    Two properties in one drive, and the second is the one worth the journey. The
    condition is **accepted** — the verb goes on to read the run and report an ending,
    where one it did not read is refused at the command line with nothing streamed. And
    the ending it reports is the run-level one it would have reported anyway: this run is
    driven by nothing, and `nothing-driving` is one of the two endings checked before any
    condition a caller named. That is what makes a node condition safe to ask for — a
    wait told to return on a node can never hide a run nobody is driving — and asking for
    one here is how it is proven rather than read out of the engine's source.

    It is also why `node-settled` is unreachable over a recorded run, which
    `tests/test_watch_surface_drift.py` declares at length: every run here is undriven,
    so one of the endings above it answers first, whatever `--until` it was given. The
    ending itself is held against the doubled verb next door.
    """
    result = _watch(UNDRIVEN_RUN, "--until", condition, "--timeout", "0")

    assert result.returncode == _statuses()["nothing-driving"], result.stdout + result.stderr
    assert UNDRIVEN_RUN in result.stdout + result.stderr
    # Accepted rather than refused: a condition the verb does not read is answered at the
    # command line, and this one produced the rendered stream of a run that was read.
    assert NAMED_NODE in result.stdout, result.stdout


def test_a_condition_the_selector_does_not_offer_is_refused_and_no_ending_is_reported() -> None:
    """What the engine refuses, this refuses — and reports no terminal condition for it.

    The refusal is the engine's own and reaches the operator unchanged, naming the
    vocabulary it does have. What the wrapper owes beside it is that its own summary
    claims no ending: a watch refused before it watched anything must not read as a run
    that settled, which is the silence-as-progress the whole command exists to end.
    """
    result = _watch(UNDRIVEN_RUN, "--until", UNKNOWN_CONDITION, "--timeout", "0")

    assert result.returncode not in _statuses().values(), result.stdout + result.stderr
    assert UNKNOWN_CONDITION in result.stderr
    # The engine's refusal names the whole vocabulary, so the words are on that
    # descriptor by design; what must claim no ending is the one line the wrapper
    # writes about how the watch ended.
    summary = [line for line in result.stderr.splitlines() if line.startswith("watch: ")]
    assert len(summary) == 1, result.stderr
    assert "none of the terminal conditions" in summary[0], summary[0]


def test_a_condition_naming_a_node_the_graph_does_not_hold_is_refused() -> None:
    """The refusal that makes an accepted condition mean something.

    A parser that read any text after the shape as a node would accept everything, and
    the two journeys above would then prove nothing about validation. So the other side
    is driven: the refusal names the node that is not there and the ids that are, which
    is what lets a supervisor correct the command without reading the run.
    """
    result = _watch(UNDRIVEN_RUN, "--until", f"node={ABSENT_NODE}", "--timeout", "0")

    assert result.returncode not in _statuses().values(), result.stdout + result.stderr
    assert ABSENT_NODE in result.stderr
    assert NAMED_NODE in result.stderr


def test_the_unbounded_wait_this_recipe_offers_is_one_the_verb_takes() -> None:
    """`--timeout none` is what lets a supervisor write no loop, so it has to parse.

    Driven over a run that has settled, which returns on the first pass whatever the
    wait says — and under this module's own bound, so a build that took the value and
    then blocked fails here rather than wedging the tier.
    """
    result = _watch(SETTLED_RUN, "--timeout", _unbounded_wait())

    assert result.returncode == _statuses()["settled"], result.stdout + result.stderr


def test_the_cursor_a_real_watch_prints_is_one_a_later_watch_resumes_from() -> None:
    """The round trip, against the engine that mints the cursor rather than a double.

    A caller anchors on the word the wrapper emits the cursor under, hands the token
    straight back, and gets a watch that does not repeat what the first one showed. The
    token's *shape* is the engine's, so a build that started minting something the
    wrapper will not put in a command line is caught here rather than by a double
    agreeing with this repository's own guess.
    """
    first = _watch(SETTLED_RUN, "--timeout", "0")
    assert first.returncode == _statuses()["settled"], first.stdout + first.stderr

    prefix = next(row.rest for row in _surface() if row.kind == "cursor-prefix")
    cursors = [
        line.split(" ", 1)[1] for line in first.stdout.splitlines() if line.startswith(f"{prefix} ")
    ]
    assert len(cursors) == 1, first.stdout

    resumed = _watch(SETTLED_RUN, "--cursor", cursors[0], "--timeout", "0")

    assert resumed.returncode == _statuses()["settled"], resumed.stdout + resumed.stderr
    assert "node-settled" not in resumed.stdout


def test_the_recorded_run_this_module_names_holds_the_node_it_drives_a_condition_with() -> None:
    """The fixture is read rather than trusted, so a shape is driven with a real id.

    A recorded run edited under this module would turn the two accepted-condition
    journeys into assertions about a node nothing holds: the verb would refuse them for
    naming an absent node, and the refusal journey below would pass for the wrong
    reason.
    """
    plan = json.loads((RECORDED_RUNS / UNDRIVEN_RUN / "plan.json").read_text(encoding="utf-8"))
    ids = [task.get("id") for task in plan.get("tasks", [])]

    assert NAMED_NODE in ids, ids
    assert ABSENT_NODE not in ids


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
    environment["ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"] = str(QUESTION_HELD_SECONDS)
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
        if UNREAD_COUNT.search(seen) or "planner update(s) waiting" in seen:
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
    result = _watch(
        node_settled.run,
        "--until",
        "node-settled",
        "--timeout",
        str(ELAPSING_SECONDS),
        environment=node_settled.environment,
    )

    assert result.returncode == _statuses()["node-settled"], result.stdout + result.stderr
    terminal = _rendered("terminal", result.stdout)
    assert len(terminal) == 1, result.stdout
    assert "node-settled" in terminal[0], terminal[0]


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

    The count is read off the wrapper's own terminal line as well as the status, because
    that clause is the one thing the watch rule forbids dropping and a watch that
    returned on the surface while saying nothing about it would satisfy the status alone.
    """
    result = _watch(
        question_waiting.run,
        "--tick-interval",
        str(TICK_SECONDS),
        environment=question_waiting.environment,
    )

    assert result.returncode == _statuses()["surface-waiting"], result.stdout + result.stderr
    terminal = _rendered("terminal", result.stdout)
    assert len(terminal) == 1, result.stdout
    counted = UNREAD_COUNT.search(terminal[0])
    assert counted is not None and int(counted.group(1)) >= 1, (
        f"the watch returned on a blocking surface and its terminal line reports no "
        f"unread planner update: {terminal[0]!r}"
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

    result = _watch(
        question_waiting.run,
        "--until",
        "settled",
        "--tick-interval",
        tick,
        "--timeout",
        wait,
        environment=question_waiting.environment,
    )

    assert result.returncode == _statuses()["elapsed"], result.stdout + result.stderr
    heartbeats = _rendered("heartbeat", result.stdout)
    assert heartbeats, (
        f"a {wait}s wait at a {tick}s tick over a live run wrote no heartbeat, so from "
        f"outside it is indistinguishable from a watch that died:\n{result.stdout}"
    )
    for line in heartbeats:
        counted = UNREAD_COUNT.search(line)
        assert counted is not None and int(counted.group(1)) >= 1, (
            f"this run holds an unanswered blocking question and a heartbeat of the "
            f"watch over it reports no unread planner update: {line!r}"
        )
        assert NOTHING_UNREAD not in line, line
