"""`just plan` turns a manager's brief into a planner dispatch that can ask questions back.

The manager's job is writing the brief and reviewing what comes back, not assembling
a plan file. What that hands to a recipe is the one shape nobody can check by eye:

* the persona named as the **path** `../personas/planner.yaml`. The bare name
  `planner` resolves to a role compiled into `oneagentgraph`, so a launch that got
  this wrong runs a different role with nothing anywhere to say so — which is why the
  journey below reads the file's own words back out of the dispatch's prompt rather
  than reading the plan and believing it;
* `ORCHESTRATOR_ASK_MANAGER` in the launch environment, without which the planner
  cannot stop at a decision fork and must guess;
* the watch command printed, because a question nobody reads is a question that
  times out into a synthesized verdict;
* and no observer graph, which is the one of the four that is a *choice* rather than
  a shape. The journal, the ownership row, the surfaces and the DAG UI place are
  `onepipeline start`'s own and hold whether or not an agent watches — so what a
  dag-scope graph would add to a planning run is a monitor comparing it against the
  plan it has not written yet. A caller who names one keeps it.

Nothing validates a `onepipeline` plan document — there is no `--dry-run` and no
validate verb, and `oneagentgraph validate` checks a graph rather than a plan — so
the only proof that a generated plan is launchable is a launch. These journeys make
one, through the real recipe, the real `scripts/plan.sh`, the real
`scripts/onepipeline.sh`, the real driver, and the real `graphs/` agent graphs.
`tests/e2e/fake_backend.py` stands in for the paid model alone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict, cast

import pytest
from fake_backend import (
    DISPATCHED_MEMBER,
    ENVIRONMENT_KEYS_ENV,
    JUDGE_CONFIG_NAME,
    MEMBER_OF_CONFIG,
    PROMPT_LOG_ENV,
)
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model, and the provider binary beneath it — the second
#: is what covers a single-sided member, which runs oneharness in process and so
#: spawns no CLI for the first to be.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

#: A launching session these journeys state rather than inherit, and everything else
#: an enclosing dispatch would otherwise decide for them.
LAUNCHING_SESSION = "e2e-plan-recipe"
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: A run's own name on the ledger, which a plan's `name` becomes. Distinguished from
#: the prose it is derived from, because what makes a string a run id is where it came
#: from — and here that is a brief's filename, which is not one until it is sanitized.
RunId = NewType("RunId", str)

#: Where `scripts/plan.sh` writes what it generates, relative to this checkout.
PLAN_DIRECTORY = REPO_ROOT / "scratch" / "plans"

#: This repository's planner persona, and the one sentence of each of its two sides
#: that the dispatch is read for. Held against the file first, so a persona rewrite
#: fails here — naming this test — instead of quietly making the launch assertion
#: vacuous.
PLANNER_PERSONA_FILE = REPO_ROOT / "personas" / "planner.yaml"
PERSONA_REF = "../personas/planner.yaml"
AGENT_ROLE = (
    "You implement none of it: your deliverable is a plan another agent could "
    "execute without re-deriving it."
)
JUDGE_BAR = "You are a principal engineer reviewing a plan before anyone builds from it."

#: The seam the dispatched planner reaches its manager through.
ASK_MANAGER_ENV = "ORCHESTRATOR_ASK_MANAGER"

#: The brief this module's launch is made from, and the run it is launched under.
#: Written to a temporary directory rather than taken from `examples/`, so the launch
#: journeys read none of this repository's prose and stay in the code-only test tier.
BRIEF = """## What
Decide whether the paginated listing's cursor is an opaque token or a node id.

## Why
The browser view cannot deep-link to a page until that is settled, and both halves
are blocked on the answer.

## Acceptance criteria
- The cursor's shape and its type are stated.
- What an exhausted page answers is stated.
"""
RUN = RunId("plan-recipe-e2e")

#: The turn budget this launch states, so the flag that carries it is proven to reach
#: the generated node rather than only to be accepted.
TURN_BUDGET = 40

#: The shipped brief and the plan this repository ships as what it produces.
SHIPPED_BRIEF = "examples/planner-brief.example.md"
SHIPPED_PLAN = REPO_ROOT / "examples" / "single-node-planner.plan.json"


class Planned(NamedTuple):
    """One `just plan` launch: how it ended, what it wrote, and the turns it reached."""

    launch: subprocess.CompletedProcess[str]
    #: The generated plan document, as JSON.
    plan: PlanDocument
    #: Every harness turn the run reached, as the fake backend recorded it.
    turns: list[TurnRecord]
    #: What the run recorded of itself on the ledger, under the id the launch printed.
    run_root: Path
    #: `just runs --mine`, read by the session that launched it, while it is on the
    #: ledger. Captured here rather than in a test because it is a claim about this
    #: launch: what a *later* read reports is a claim about the ledger's retention.
    listed: str


class PlanNode(TypedDict, total=False):
    """The one node a generated plan carries, in the fields this suite reads.

    `total=False` because the absent ones are the point: a planner node carries no
    `repo` and no `done_when`, and asserting that is asserting they are missing.
    """

    id: str
    persona: str
    task: str
    max_turns: int
    repo: str
    done_when: str


class PlanDocument(TypedDict):
    """A generated plan, in the terms the recipe promises it."""

    schema_version: int
    goal: dict[str, str]
    name: str
    tasks: list[PlanNode]


class TurnRecord(TypedDict):
    """One recorded harness turn. `tests/e2e/fake_backend.py` owns this schema."""

    config: str | None
    prompt: str
    system: str
    environment: dict[str, str | None]


def _environment(tmp_path: Path) -> dict[str, str]:
    """The environment one `just plan` launch runs in."""
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


def _just(
    *args: str, environment: dict[str, str], seconds: float = 300
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


@pytest.fixture(scope="module")
def planned(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Planned:
    """Launch a planner through the real recipe once, and hand every question its run.

    One launch for every claim here rather than one each: writing the document,
    launching it, resolving the persona path, and passing the ask-manager seam down
    are four halves of the same act, and a fixture per claim would pay for a whole
    dispatch to re-prove the same one.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("plan-recipe")
    environment = _environment(tmp_path)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    turns = tmp_path / "turns.jsonl"
    environment[PROMPT_LOG_ENV] = str(turns)
    environment[ENVIRONMENT_KEYS_ENV] = ASK_MANAGER_ENV
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(BRIEF, encoding="utf-8")
    generated = PLAN_DIRECTORY / f"{RUN}.plan.json"
    try:
        launch = _just(
            "plan",
            str(brief),
            "--name",
            RUN,
            "--max-turns",
            str(TURN_BUDGET),
            environment=environment,
        )
        assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
        assert generated.is_file(), f"`just plan` wrote no plan at {generated}"
        assert turns.is_file(), f"the launch reached no harness turn, so {turns} is absent"
        listing = _just("runs", "--mine", environment=environment, seconds=120)
        assert listing.returncode == 0, f"the ledger could not be read:\n{listing.stderr}"
        return Planned(
            launch=launch,
            # `cast` rather than a validating read: the recipe writes this document and
            # `PlanDocument` states what it promises; the subscripts below fail loudly
            # if it is not that shape, which is the assertion.
            plan=cast(PlanDocument, json.loads(generated.read_text(encoding="utf-8"))),
            turns=[
                cast(TurnRecord, json.loads(line))
                for line in turns.read_text(encoding="utf-8").splitlines()
            ],
            run_root=Path(environment["ONEPIPELINE_RUNS_DIR"]) / RUN,
            listed=listing.stdout + listing.stderr,
        )
    finally:
        _just("stop", RUN, environment=environment, seconds=60)
        generated.unlink(missing_ok=True)


def _worker_turns(planned: Planned) -> list[TurnRecord]:
    """Every turn that served the dispatched node's own member, either side of it."""
    return [turn for turn in planned.turns if "/members/worker/" in (turn["config"] or "")]


def _member(turn: TurnRecord) -> str | None:
    """Which member of which graph took a turn, read the way the backend reads it.

    `oneagentgraph` names every member's scratch after it and pins that member's
    configs inside it, so the recorded `--config` is what says whose turn this is — and
    it is the only thing that does: an observer's monitor reaches the same stand-in
    through the same branch as a dispatched worker.
    """
    named = MEMBER_OF_CONFIG.search(turn["config"] or "")
    return None if named is None else named.group(1)


def _side(turn: TurnRecord) -> str:
    """Which side of the conversation a turn served, read the way the backend reads it."""
    return Path(turn["config"] or "").name


def _flattened(prose: str) -> str:
    """Collapse every run of whitespace, so a persona sentence may be quoted as one line.

    The persona's two sides are YAML block scalars: each is hard-wrapped at an indent,
    and that same wrapping is what reaches the prompt. A sentence long enough to be
    unambiguous is longer than the line it sits on, so comparing on flattened text
    reads the sentence rather than the wrapping — and reflowing the file then leaves
    this journey proving what it did before rather than silently proving nothing.
    """
    return " ".join(prose.split())


@pytest.mark.xdist_group("plan-recipe")
def test_the_generated_plan_is_one_direct_planner_node_carrying_the_brief_verbatim(
    planned: Planned,
) -> None:
    """The document is exactly what a planner dispatch needs and nothing else.

    Every field here is one a manager would otherwise have to remember. The brief
    reaching the node **verbatim** is the load-bearing one: it is the manager's own
    words, in the template every task this repository dispatches is written in, and a
    recipe that reformatted or summarized it on the way would be editing the request
    between the two people it is passing between.

    `repo` and `done_when` are asserted absent for different reasons. A planner
    authors no target-project content, so a `repo` would cut a lifecycle worktree and
    verify a gate for a change nobody makes; and `done_when` is refused by the loader
    outright, so a generated plan carrying one could never be launched at all.
    """
    plan = planned.plan
    assert plan["schema_version"] == 2, plan
    assert plan["name"] == RUN, plan
    assert plan["goal"]["text"].strip(), "the plan states no goal"
    assert len(plan["tasks"]) == 1, plan["tasks"]

    node = plan["tasks"][0]
    assert node["task"] == BRIEF, (
        "the brief did not reach the node verbatim; the manager's words are the task:\n"
        f"{node['task']!r}"
    )
    assert node["persona"] == PERSONA_REF, (
        f"the node names the persona {node['persona']!r}; a bare name resolves to a role "
        f"compiled into the tool and this repository's file is never read"
    )
    assert node["max_turns"] == TURN_BUDGET, node
    assert "repo" not in node, f"a planner node cut a lifecycle worktree: {node}"
    assert "done_when" not in node, f"a node carrying done_when is refused at load: {node}"


@pytest.mark.xdist_group("plan-recipe")
def test_the_persona_path_resolves_to_this_repositorys_planner_file(planned: Planned) -> None:
    """Both sides of the dispatch were given `personas/planner.yaml`'s own words.

    This is the claim the plan document cannot make. `../personas/planner.yaml` and the
    bare name `planner` are both accepted by the launcher, and only one of them reads
    this file — the other resolves to a role compiled into `oneagentgraph` that happens
    to share the name. Nothing in a launch, a ledger, or a status view distinguishes
    them, so the file's text is read back out of the prompts the run actually gave.

    Read per side, because a persona is two contracts and they land in different
    places: the agent's role is composed into its system prompt, and the supervisor's
    bar arrives in the judge side's prompt.
    """
    persona = _flattened(PLANNER_PERSONA_FILE.read_text(encoding="utf-8"))
    assert AGENT_ROLE in persona and JUDGE_BAR in persona, (
        f"{PLANNER_PERSONA_FILE.name} no longer states the sentences this journey reads "
        "the dispatch for; update them together, or the assertions below prove nothing"
    )

    worker = _worker_turns(planned)
    assert worker, "the run dispatched no worker"
    agent_side = [turn for turn in worker if _side(turn) != JUDGE_CONFIG_NAME]
    judge_side = [turn for turn in worker if _side(turn) == JUDGE_CONFIG_NAME]
    assert agent_side and judge_side, f"the dispatch was one-sided: {[_side(t) for t in worker]}"

    assert any(AGENT_ROLE in _flattened(turn["system"]) for turn in agent_side), (
        "the planner persona's own role never reached the agent side, so the node ran "
        "the built-in role of the same name:\n"
        + "\n".join(turn["system"][:400] for turn in agent_side)
    )
    assert any(JUDGE_BAR in _flattened(turn["prompt"]) for turn in judge_side), (
        "the planner persona's own review bar never reached the judge side:\n"
        + "\n".join(turn["prompt"][:400] for turn in judge_side)
    )


@pytest.mark.xdist_group("plan-recipe")
def test_the_dispatched_planner_can_reach_its_manager(planned: Planned) -> None:
    """`ORCHESTRATOR_ASK_MANAGER` reaches the dispatch holding something it can run.

    A planner that cannot ask must guess and continue, which is the confidently-wrong
    plan the manager/planner split exists to prevent — and the failure is silent, since
    an unset variable looks exactly like a planner that had no questions. So the seam is
    read out of the dispatch's own environment, and the path it holds is checked to be
    executable rather than merely present: a name that is not runnable is the same
    failure one step later.
    """
    dispatched = _worker_turns(planned)
    assert dispatched, "the run dispatched no worker"
    named = {turn["environment"].get(ASK_MANAGER_ENV) for turn in dispatched}
    assert len(named) == 1, f"the dispatch was given more than one {ASK_MANAGER_ENV}: {named}"
    wrapper = named.pop()
    assert wrapper is not None, (
        f"{ASK_MANAGER_ENV} never reached the dispatched planner, which therefore has no "
        "way to ask a question and must guess instead"
    )
    assert os.access(wrapper, os.X_OK), f"{ASK_MANAGER_ENV} names {wrapper}, which is not runnable"


@pytest.mark.xdist_group("plan-recipe")
def test_the_launch_prints_the_command_that_answers_this_planners_questions(
    planned: Planned,
) -> None:
    """The watch command is printed whole, so arming it is a copy-paste.

    This is the mechanical half of the never-unmonitored rule. A planner's question is
    blocking and its channel answers its own timeouts, so a manager who has not armed
    the reader does not merely miss the question — the agent is handed a synthesized
    verdict and plans on it. Composing the command from memory under time pressure is
    what this removes, so it has to be there in full, run id and all.
    """
    printed = planned.launch.stdout + planned.launch.stderr
    assert f"just channel-next {RUN}" in printed, (
        f"the launch never named the command that reads this planner's questions:\n{printed}"
    )


@pytest.mark.xdist_group("plan-recipe")
def test_a_planning_run_records_itself_with_no_observer_watching_it(planned: Planned) -> None:
    """The launch attaches no observer, and is on the ledger in full regardless.

    Both halves are one claim, because the first was long justified by denying the
    second: this recipe named `graphs/dag-scope.yaml` on every planning run, and said
    it did so "so the run gets a journal, an ownership row, planner surfaces, and a
    place in the DAG UI". None of those comes from an agent graph — they are the
    ledger `onepipeline start` writes and the channel it serves, and `--dag-graph`
    ships defaulting to `off` precisely because no agent is required to run a plan.
    What the graph adds is the two observer members, and a planning run is the run
    they can say least about: its **output is the plan**, so a monitor watching it is
    comparing it against a document that does not exist yet.

    So the run's own record is read for all four, and the turns are read for who took
    them. Nothing else distinguishes an observed run from an unobserved one — a
    monitor member reaches the same stand-in as the dispatch, and a launch that
    quietly attached one would still settle.
    """
    took_turns = {_member(turn) for turn in planned.turns}
    assert took_turns == {DISPATCHED_MEMBER}, (
        f"a planning run took turns as {sorted(str(member) for member in took_turns)}; "
        f"only the dispatched {DISPATCHED_MEMBER!r} may, and an observer member here is "
        "watching the run for drift from a plan the run is what writes"
    )

    assert planned.run_root.is_dir(), (
        f"the launch printed run {RUN} and the ledger has no run under it at "
        f"{planned.run_root}, so the id a manager was told to watch is not this run's"
    )
    journal = planned.run_root / "events.jsonl"
    assert journal.is_file() and journal.stat().st_size > 0, (
        f"the run recorded no journal at {journal}"
    )
    recorded = json.loads((planned.run_root / "launch.json").read_text(encoding="utf-8"))
    assert recorded["run_id"] == RUN, recorded
    assert recorded.get("graph") is None, (
        f"the launch recorded {recorded.get('graph')!r} as its observer graph; a planning "
        "run is launched with none"
    )
    assert recorded.get("session") == LAUNCHING_SESSION, (
        f"the run is owned by {recorded.get('session')!r} rather than by the session that "
        f"launched it, so `just runs --mine` and `just stop` disown it: {recorded}"
    )
    assert RUN in planned.listed, (
        f"`just runs --mine` does not name run {RUN}, so it has no ownership row the "
        f"launching session can act on:\n{planned.listed}"
    )


class Observer(NamedTuple):
    """One caller-named observer: what was typed, and what the launch must record."""

    what: str
    #: The run this case launches under, which is also the plan it writes.
    run: RunId
    #: What the caller types after the brief.
    arguments: tuple[str, ...]
    #: The graph the launch record must then name, relative to this checkout, or
    #: `None` where the caller asked for no observer at all.
    named: str | None


#: The observer a caller may name, and what the launch must then record as its graph.
#: Both spellings, and both directions: naming this host's monitor graph is the reason
#: the flag reaches the launch at all, and naming `off` is the spelling that used to be
#: refused for contradicting a default it agreed with.
OBSERVERS = (
    Observer(
        "this host's monitor graph",
        RunId("plan-recipe-observer-graph"),
        ("--dag-graph", "graphs/dag-scope.yaml"),
        "graphs/dag-scope.yaml",
    ),
    Observer("no observer, joined", RunId("plan-recipe-observer-off"), ("--dag-graph=off",), None),
)


@pytest.mark.parametrize("observer", OBSERVERS, ids=lambda row: row.run)
def test_a_caller_who_names_an_observer_launches_with_the_one_they_named(
    tmp_path: Path, observer: Observer
) -> None:
    """`--dag-graph` is the caller's to state, in either direction and either spelling.

    It was refused outright, on the grounds that the recipe always named one itself and
    the flag cannot be given twice — so an operator who wanted a planning run watched,
    or who wanted to say `off` explicitly, was told to write the plan by hand instead.
    Now the recipe adds its default only when neither spelling was typed.

    Read from the run's own launch record rather than from the command line: the record
    is what the driver kept, resolved to an absolute path by the crate that accepted the
    flag, which is proof the flag arrived and was understood.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(BRIEF, encoding="utf-8")
    generated = PLAN_DIRECTORY / f"{observer.run}.plan.json"
    environment = _environment(tmp_path)

    # `--detach` because what is under test is the launch, not the run it drives: the
    # record is written by the time the launch record is printed, and an attached one
    # would pay for a whole watched planning dispatch to read the same field.
    launch = _just(
        "plan",
        str(brief),
        "--name",
        observer.run,
        "--detach",
        *observer.arguments,
        environment=environment,
    )
    try:
        assert launch.returncode == 0, (
            f"`just plan {' '.join(observer.arguments)}` was refused:\n"
            f"{launch.stdout}\n{launch.stderr}"
        )
        recorded = json.loads(
            (Path(environment["ONEPIPELINE_RUNS_DIR"]) / observer.run / "launch.json").read_text(
                encoding="utf-8"
            )
        )
        expected = None if observer.named is None else str(REPO_ROOT / observer.named)
        assert recorded.get("graph") == expected, (
            f"the caller named {observer.what} and the launch recorded "
            f"{recorded.get('graph')!r} as its observer graph"
        )
    finally:
        _just("stop", observer.run, environment=environment, seconds=60)
        generated.unlink(missing_ok=True)


class Malformed(NamedTuple):
    """One `--dag-graph` that names no graph, and the refusal it must produce."""

    what: str
    #: The run it would have launched under, and the plan it writes on the way.
    run: RunId
    #: What the caller types after the brief.
    arguments: tuple[str, ...]
    #: A fragment of the refusal, which is `onepipeline start`'s own here rather than
    #: this recipe's — the two spellings are refused at different depths and this is
    #: what says which one answered.
    refusal: str


#: The two spellings that name no graph at all: a valueless `--dag-graph`, which is a
#: mistyped observer, and an empty joined one, which is what a shell writes when it
#: expands a variable to nothing — the same pair `--name` and `--max-turns` are each
#: guarded against above. Measured rather than predicted: the first is refused by the
#: argument parser before the run exists, and the second resolves its empty ref to the
#: launch directory and dies when the driver cannot read a graph out of it.
MALFORMED = (
    Malformed(
        "a valueless observer",
        RunId("plan-recipe-observer-valueless"),
        ("--dag-graph",),
        "a value is required for '--dag-graph <REF>'",
    ),
    Malformed(
        "an empty observer",
        RunId("plan-recipe-observer-empty"),
        ("--dag-graph=",),
        "cannot read",
    ),
)


@pytest.mark.parametrize("malformed", MALFORMED, ids=lambda row: row.run)
def test_an_observer_that_names_no_graph_refuses_the_launch(
    tmp_path: Path, malformed: Malformed
) -> None:
    """A `--dag-graph` that names nothing ends the launch, rather than taking the default.

    This is the half of the pass-through that has to be measured rather than reasoned
    about. The recipe used to refuse every `--dag-graph` itself; now it forwards one,
    so what becomes of a spelling that names nothing is `onepipeline start`'s answer
    and not this recipe's. The failure a planner would never notice is a typo absorbed
    into an ordinary unwatched launch — the run id printed, the plan written, and the
    observer the caller asked for silently not there — so what is asserted is that the
    launch fails and that nothing was dispatched under it.

    The plan is written before the launch is made, by design, so a refused launch
    leaves one behind at the path the next launch would read; the teardown takes it.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(BRIEF, encoding="utf-8")
    generated = PLAN_DIRECTORY / f"{malformed.run}.plan.json"
    environment = _environment(tmp_path)
    turns = tmp_path / "turns.jsonl"
    environment[PROMPT_LOG_ENV] = str(turns)

    launch = _just(
        "plan",
        str(brief),
        "--name",
        malformed.run,
        "--detach",
        *malformed.arguments,
        environment=environment,
    )
    try:
        assert launch.returncode != 0, (
            f"`just plan {' '.join(malformed.arguments)}` launched {malformed.what}:\n"
            f"{launch.stdout}\n{launch.stderr}"
        )
        reported = launch.stderr + launch.stdout
        assert malformed.refusal in reported, (
            f"{malformed.what} was refused for some other reason:\n{reported}"
        )
        assert not turns.exists(), (
            f"a refused launch dispatched a turn anyway:\n{turns.read_text(encoding='utf-8')}"
        )
    finally:
        _just("stop", malformed.run, environment=environment, seconds=60)
        generated.unlink(missing_ok=True)


@pytest.mark.reads_docs
def test_the_shipped_example_plan_is_what_the_shipped_brief_produces(tmp_path: Path) -> None:
    """The example this repository ships is the recipe's own output, not a hand-written copy.

    An example plan is the document an operator copies, so one that has drifted from
    what the recipe writes teaches a shape the recipe would never produce. Regenerating
    it here from the shipped brief is what keeps the two the same artifact — including
    the run id, which `onepipeline` normalizes on the way (a `.` becomes a `-`), so the
    example's `name` is also the run `just plan` would tell a manager to watch.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    shipped = json.loads(SHIPPED_PLAN.read_text(encoding="utf-8"))
    regenerated = PLAN_DIRECTORY / f"{shipped['name']}.plan.json"
    environment = _environment(tmp_path)
    # `--detach` and a runs root of this journey's own: what is under test is the
    # document, and the run it starts is stopped on the way out.
    launch = _just("plan", SHIPPED_BRIEF, "--detach", environment=environment)
    try:
        assert launch.returncode == 0, f"{SHIPPED_BRIEF} did not launch:\n{launch.stderr}"
        assert json.loads(regenerated.read_text(encoding="utf-8")) == shipped, (
            f"{SHIPPED_PLAN.name} is not what `just plan {SHIPPED_BRIEF}` writes; "
            f"regenerate it from the brief rather than editing it by hand"
        )
    finally:
        _just("stop", str(shipped["name"]), environment=environment, seconds=60)
        regenerated.unlink(missing_ok=True)


class Refusal(NamedTuple):
    """One malformed invocation, and the fragment its refusal must carry."""

    what: str
    arguments: tuple[str, ...]
    names: str


#: Every way of asking for a planner that cannot produce one. Each is refused before a
#: run is ever started, so a manager who mistyped gets a sentence rather than a run.
REFUSALS = (
    Refusal("no brief", (), "no brief was named"),
    Refusal("a flag first", ("--name", "cursor"), "must be the brief"),
    Refusal("a missing brief", ("scratch/absent-brief.md",), "is not a readable file"),
    Refusal("an unnamed run", ("--name",), "--name was given no value"),
    Refusal("an unnamed budget", ("--max-turns",), "--max-turns was given no value"),
    # The joined spelling of each, whose value can be present and empty — a shell that
    # expanded a variable to nothing writes exactly this. Refused rather than read as
    # the flag being absent, which would launch under a name the caller did not choose.
    Refusal("an empty run name", ("--name=",), "--name was given no value"),
    Refusal("an empty budget", ("--max-turns=",), "--max-turns was given no value"),
    Refusal("a turn budget that is not one", ("--max-turns", "soon"), "not a positive whole"),
    Refusal("a name the engine would rewrite", ("--name", "cursor.shape"), "is not a run id"),
)


@pytest.mark.parametrize("refusal", REFUSALS, ids=lambda row: row.what)
def test_a_planner_that_cannot_be_launched_is_refused_with_a_cause_and_a_remedy(
    tmp_path: Path, refusal: Refusal
) -> None:
    """A mistyped launch names what is wrong and what to do, and starts nothing.

    The brief has to exist for the flag cases, so the ones that are about a flag are
    given a real one — otherwise they would be refused for the brief and prove nothing
    about the flag.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    names_its_own_brief = refusal.what in ("no brief", "a flag first", "a missing brief")
    leading = () if names_its_own_brief else (str(brief),)

    refused = _just("plan", *leading, *refusal.arguments, environment=_environment(tmp_path))

    assert refused.returncode != 0, f"{refusal.what} was not refused:\n{refused.stdout}"
    reported = refused.stderr + refused.stdout
    assert refusal.names in reported, reported
    stated = [line for line in reported.splitlines() if line.startswith("plan: ")]
    assert stated and all("; " in line for line in stated), (
        f"the refusal states no remedy beside its cause:\n{reported}"
    )


def test_a_brief_that_exists_but_cannot_be_read_is_refused_like_an_absent_one(
    tmp_path: Path,
) -> None:
    """The guard tests readability, not just presence, so a mode-0 brief fails here too.

    The parametrization above covers the missing brief, which a presence test alone
    catches. Only this case reaches the readability condition; without it, a guard that
    dropped that condition would refuse the brief for a section it could not read.

    A mode of `000` does not by itself make a file unreadable: `root`, and anything
    else holding `CAP_DAC_READ_SEARCH` or sitting on a filesystem that ignores modes,
    satisfies the `[ -r ]` the guard runs, and there is no portable mode that refuses
    them. So the setup is measured rather than assumed — the scenario is proven with
    the same `access(2)` the guard consults before anything is asserted about it, and
    the one case where it cannot be built is skipped by name rather than passing as a
    readability test that read a readable brief.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    brief = tmp_path / "unreadable.md"
    brief.write_text(BRIEF, encoding="utf-8")
    brief.chmod(0o000)
    if os.access(brief, os.R_OK):
        brief.chmod(0o644)
        pytest.skip("this user reads a mode-0 file, so an unreadable brief cannot be set up")

    refused = _just("plan", str(brief), environment=_environment(tmp_path))

    try:
        assert refused.returncode != 0, f"an unreadable brief launched:\n{refused.stdout}"
        reported = refused.stderr + refused.stdout
        assert f"the brief '{brief}' is not a readable file" in reported, reported
        assert "check the path, or write the brief there first" in reported, reported
    finally:
        brief.chmod(0o644)


def test_a_brief_filename_is_sanitized_into_the_run_id_the_engine_would_mint(
    tmp_path: Path,
) -> None:
    """A derived name is made into one the engine will not rewrite, rather than refused.

    `onepipeline` mints the run id from the plan's `name` and rewrites what it cannot
    use — measured, `with.dots` becomes run `with-dots`. The recipe prints the run id in
    the command it tells a manager to arm, so a name that survived here and was rewritten
    there would send them to a run that does not exist. A brief filename is not the
    manager's choice of run id, so it is sanitized into one; an explicit `--name` is,
    and is refused instead, which the parametrization above covers.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    brief = tmp_path / "cursor.shape.md"
    brief.write_text(BRIEF, encoding="utf-8")
    generated = PLAN_DIRECTORY / "cursor-shape.plan.json"
    environment = _environment(tmp_path)

    launch = _just("plan", str(brief), "--detach", environment=environment)
    try:
        assert launch.returncode == 0, f"{brief.name} did not launch:\n{launch.stderr}"
        named = json.loads(generated.read_text(encoding="utf-8"))["name"]
        assert named == "cursor-shape", named
        assert f"just channel-next {named}" in launch.stdout + launch.stderr, launch.stderr
    finally:
        _just("stop", "cursor-shape", environment=environment, seconds=60)
        generated.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("what", "brief", "names"),
    [
        ("nothing at all", "", "is empty"),
        (
            "no acceptance criteria",
            "## What\nDecide the cursor.\n\n## Why\nThe view is blocked.\n",
            "'## Acceptance criteria'",
        ),
        (
            "no motivation",
            "## What\nDecide the cursor.\n\n## Acceptance criteria\n- Stated.\n",
            "'## Why'",
        ),
        (
            "no statement of the work",
            "## Why\nThe view is blocked.\n\n## Acceptance criteria\n- Stated.\n",
            "'## What'",
        ),
    ],
    ids=("empty", "no-criteria", "no-why", "no-what"),
)
def test_a_brief_that_is_not_a_task_is_refused_before_a_planner_is_dispatched(
    tmp_path: Path, what: str, brief: str, names: str
) -> None:
    """The brief IS the dispatched task, so it is refused unless it is written as one.

    `## Acceptance criteria` is the load-bearing section: it is the node's whole review
    bar — there is no second place to state one, and a plan carrying `done_when` is
    refused at load — so a brief without it dispatches a planner judged on nothing the
    manager asked for, and the manager finds out from the plan that comes back. `## Why`
    is the other one a diff can never recover, which is why it is required rather than
    inferred.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    written = tmp_path / "thin.md"
    written.write_text(brief, encoding="utf-8")

    refused = _just("plan", str(written), environment=_environment(tmp_path))

    assert refused.returncode != 0, f"a brief with {what} was launched:\n{refused.stdout}"
    reported = refused.stderr + refused.stdout
    assert names in reported, reported


def test_a_planner_that_could_not_ask_questions_is_not_launched_at_all(tmp_path: Path) -> None:
    """No ask-manager wrapper means no launch, rather than a planner that must guess.

    The seam is the whole reason planning can be split out of the manager's session, and
    a launch that silently dropped it would produce the confidently-wrong plan the split
    exists to prevent — with nothing to distinguish it from a planner that had no
    questions. So a checkout missing the wrapper is refused before a run is started.
    Driven by running the real script beside the helper that establishes the seam but
    without the wrapper that helper names, which is what a half-restored checkout looks
    like.
    """
    detached = tmp_path / "checkout" / "scripts"
    detached.mkdir(parents=True)
    for name in ("plan.sh", "ask-manager-env.sh"):
        copied = detached / name
        copied.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        copied.chmod(0o755)
    copied = detached / "plan.sh"
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")

    refused = subprocess.run(  # noqa: S603 - the real script, from a checkout without the seam
        [str(copied), str(brief)],
        cwd=tmp_path,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert "ask-manager wrapper is not an executable file" in refused.stderr, refused.stderr
    assert not (tmp_path / "scratch").exists(), "a plan was written for a launch that cannot ask"


#: A `python3` that cannot write a plan. Stands in where the recipe resolves an
#: interpreter, which is a checkout's own `.venv` first and PATH only without one.
BROKEN_INTERPRETER = "#!/usr/bin/env bash\necho 'half a plan'\nexit 1\n"


def _detached_recipe(tmp_path: Path) -> Path:
    """A copy of the recipe's script and the seam it requires, outside this checkout.

    All three, because the recipe refuses to launch a planner that could not ask
    questions before it writes anything — the helper that establishes the seam, and the
    wrapper that helper insists on — so a copy carrying only itself would be refused for
    the wrong reason.
    """
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("plan.sh", "ask-manager-env.sh", "ask-manager.sh"):
        copied = scripts / name
        copied.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        copied.chmod(0o755)
    return scripts / "plan.sh"


def test_a_plan_directory_that_cannot_be_created_is_refused_before_the_launch(
    tmp_path: Path,
) -> None:
    """A checkout that cannot be written to is said out loud rather than exited through.

    The recipe writes its plan under the working directory, so a read-only one is a real
    state — and the failure it produces without a guard is `mkdir`'s own message and an
    exit status, which names neither what was being attempted nor what to do. Driven by
    running the real script from a directory nothing may be created in.
    """
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    read_only = tmp_path / "read-only"
    read_only.mkdir(mode=0o500)

    refused = subprocess.run(  # noqa: S603 - the real script, in a directory it cannot write
        [str(_detached_recipe(tmp_path)), str(brief)],
        cwd=read_only,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert "could not be created" in refused.stderr, refused.stderr


def test_a_plan_that_could_not_be_written_leaves_no_half_written_file_behind(
    tmp_path: Path,
) -> None:
    """A partial plan is removed, because the next launch would otherwise read it.

    The plan is written by redirecting an interpreter's output into the file the launch
    then names, so an interpreter that fails midway leaves a truncated document at
    exactly the path a rerun — or a manager relaunching it with `just orchestrate` —
    would pick up. The refusal has to take it with it.
    """
    recipe = _detached_recipe(tmp_path)
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    interpreters = tmp_path / "bin"
    interpreters.mkdir()
    broken = interpreters / "python3"
    # llmlint: ignore[e2e_not_mocked] The recipe is real; a dying interpreter is the input.
    broken.write_text(BROKEN_INTERPRETER, encoding="utf-8")
    broken.chmod(0o755)
    working = tmp_path / "working"
    working.mkdir()
    environment = _environment(tmp_path)
    environment["PATH"] = f"{interpreters}{os.pathsep}{environment['PATH']}"

    refused = subprocess.run(  # noqa: S603 - the real script, with an interpreter that fails
        [str(recipe), str(brief), "--name", "half-written"],
        cwd=working,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert "could not be written" in refused.stderr, refused.stderr
    assert not (working / "scratch" / "plans" / "half-written.plan.json").exists(), (
        "a half-written plan was left where the next launch would read it"
    )
