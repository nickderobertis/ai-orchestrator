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

import plan_root_variable
import pytest
from fake_backend import (
    DISPATCHED_MEMBER,
    ENVIRONMENT_KEYS_ENV,
    JUDGE_CONFIG_NAME,
    MEMBER_OF_CONFIG,
    PROMPT_LOG_ENV,
)
from project_fixtures import read_project_plan
from scratch_identity import seeded
from shared_dispatch_bar import shared_completion_bar
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
    # The plan-authoring root a *planning* launch exports, read from the one place that
    # composes it. This suite runs inside a dispatch and launches planners of its own, so
    # a journey that kept it would write and read through whatever directory some
    # enclosing launch chose rather than through the one these launches resolve.
    plan_root_variable.name(),
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
PLAN_DIRECTORY = REPO_ROOT / ".plans"


def _project_record(project: str) -> Path:
    return PLAN_DIRECTORY / "projects" / f"{project}.md"


def _remove_project(project: str) -> None:
    _project_record(project).unlink(missing_ok=True)
    tasks = PLAN_DIRECTORY / "tasks" / project
    if tasks.is_dir():
        for task in tasks.iterdir():
            task.unlink(missing_ok=True)
        tasks.rmdir()


#: The two checkouts the generated node names, as `scripts/plan.sh` defaults them: this
#: repository's publication checkout and the registered safety clone a planner's
#: worktree is cut from. Every journey here seeds its own pair under these names, so the
#: recipe's own defaults resolve — against a scratch registry, never this host's.
#: Pointing one of these launches at the real registry would have it clone, cut a
#: worktree, and reclaim run roots in the directories live dispatches are working in.
PUBLICATION_ALIAS = "ai-orchestrator"
EXECUTION_ALIAS = "ai-orchestrator-isolated"

#: The `graphs/node-scope.yaml` member a dispatched plan node runs as, and the journal
#: records that say where it was started and where its session cut a worktree.
WORKER_MEMBER = "worker"
MEMBER_STARTED = "member-started"
SESSION_OPENED = "session-opened"

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

#: The plan project the brief below names, which the design-doc node is given so it can
#: read the plan the planner node wrote. Qualified, because that is what a plan store
#: command is given and an unqualified id names a project in no store.
PLAN_PROJECT = "authoring:cursor-shape"

#: The brief this module's launch is made from, and the run it is launched under.
#: Written to a temporary directory rather than taken from `examples/`, so the launch
#: journeys read none of this repository's prose and stay in the code-only test tier.
BRIEF = f"""## What
Decide whether the paginated listing's cursor is an opaque token or a node id.

Plan project: {PLAN_PROJECT}

## Why
The browser view cannot deep-link to a page until that is settled, and both halves
are blocked on the answer.

## Acceptance criteria
- The cursor's shape and its type are stated.
- What an exhausted page answers is stated.
"""
RUN = RunId("plan-recipe-e2e")

#: The planner node's own id, which every launch writes and the second node depends on.
PLANNER_NODE = "plan"

#: The field a per-node agent graph is named in, which the loader resolves against the
#: directory a run was launched from before it records the plan. Nothing this module
#: launches carries one — the node that does is written by the tail, in a launch of its
#: own — so the only place it is read here is where a shipped example is compared against
#: what the recipe would write.
AGENT_GRAPH_FIELD = "agent_graph"

#: The turn budget this launch states, so the flag that carries it is proven to reach
#: the generated node rather than only to be accepted.
TURN_BUDGET = 40

#: The shipped brief and the plan this repository ships as what it produces.
SHIPPED_BRIEF = "examples/planner-brief.example.md"
SHIPPED_PROJECT = "examples:planner-brief-example"


class JournalEvent(TypedDict):
    """One record the run appended, in the three fields this journey reads.

    `onepipeline` owns the whole contract; these are stated rather than restated from
    it, because what this journey asks the journal is one question: which directory.
    """

    kind: str
    labels: dict[str, str]
    payload: dict[str, str]


class Planned(NamedTuple):
    """One `just plan` launch: how it ended, what it wrote, and the turns it reached."""

    launch: subprocess.CompletedProcess[str]
    #: The generated plan document, as JSON.
    plan: PlanDocument
    #: Every event the run appended, in order.
    journal: list[JournalEvent]
    #: Every harness turn the run reached, as the fake backend recorded it.
    turns: list[TurnRecord]
    #: What the run recorded of itself on the ledger, under the id the launch printed.
    run_root: Path
    #: `just runs --mine`, read by the session that launched it, while it is on the
    #: ledger. Captured here rather than in a test because it is a claim about this
    #: launch: what a *later* read reports is a claim about the ledger's retention.
    listed: str
    #: The project record the recipe wrote, read before the fixture removes it again.
    project_record: str


class PlanNode(TypedDict, total=False):
    """One node a generated plan carries, in the fields this suite reads.

    `total=False` because a field is absent on one node or the other: only the design-doc
    node carries `deps` and `agent_graph`, only the planner node carries `max_turns`, a
    `--direct` launch carries no `title` or placement pair — and one absence is still the
    point, since `done_when` is refused by the loader outright, so a generated plan
    carrying one could never be launched at all.
    """

    id: str
    persona: str
    task: str
    title: str
    deps: list[str]
    agent_graph: str
    max_turns: int
    repo: str
    execution_checkout: str
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
    """The environment one `just plan` launch runs in, against a registry of its own.

    The identity is seeded under the two aliases the recipe defaults to, rather than the
    defaults being overridden per launch: what is under test is the plan this recipe
    writes when nobody tells it anything, and a journey that passed `--repo` would be
    proving a flag instead. Seeded for every launch, refusals included, because the one
    thing none of them may do is reach this host's own registry.
    """
    identity = seeded(tmp_path, publication=PUBLICATION_ALIAS, execution=EXECUTION_ALIAS)
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEVCS_HOME"] = str(identity.home)
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
    generated = _project_record(RUN)
    try:
        launch = _just(
            "plan",
            str(brief),
            "--name",
            RUN,
            "--max-turns",
            str(TURN_BUDGET),
            # The planner and nothing after it. What this fixture is about is the launch
            # a brief becomes, and the tail after it is about the plan a planner writes —
            # which a stand-in provider does not write, so every step of it would be a
            # refusal about a project nothing authored. `just finish-plan` is driven whole
            # in tests/plan_tooling/test_finish_plan_recipe_e2e.py, where a stand-in does
            # author one.
            "--no-design-doc",
            environment=environment,
        )
        assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
        assert generated.is_file(), f"`just plan` wrote no plan at {generated}"
        assert turns.is_file(), f"the launch reached no harness turn, so {turns} is absent"
        listing = _just("runs", "--mine", environment=environment, seconds=120)
        assert listing.returncode == 0, f"the ledger could not be read:\n{listing.stderr}"
        journal = Path(environment["ONEPIPELINE_RUNS_DIR"]) / RUN / "events.jsonl"
        assert journal.is_file(), f"the launch recorded no journal at {journal}"
        return Planned(
            launch=launch,
            # `cast` rather than a validating read: the recipe writes this document and
            # `PlanDocument` states what it promises; the subscripts below fail loudly
            # if it is not that shape, which is the assertion.
            plan=cast(
                PlanDocument,
                json.loads(
                    (Path(environment["ONEPIPELINE_RUNS_DIR"]) / RUN / "plan.json").read_text(
                        encoding="utf-8"
                    )
                ),
            ),
            # `cast` for the same reason: `onepipeline` owns the journal's record
            # contract, `JournalEvent` states only the three fields this journey reads,
            # and a record missing one fails at the subscript that wanted it.
            journal=[
                cast(JournalEvent, json.loads(line))
                for line in journal.read_text(encoding="utf-8").splitlines()
            ],
            # `cast` for that reason once more: the fake backend owns this record and
            # `TurnRecord` states the fields this journey reads, so a line missing one
            # fails at the subscript that wanted it rather than passing unexamined.
            turns=[
                cast(TurnRecord, json.loads(line))
                for line in turns.read_text(encoding="utf-8").splitlines()
            ],
            run_root=Path(environment["ONEPIPELINE_RUNS_DIR"]) / RUN,
            listed=listing.stdout + listing.stderr,
            project_record=generated.read_text(encoding="utf-8"),
        )
    finally:
        _just("stop", RUN, environment=environment, seconds=60)
        _remove_project(RUN)


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


def _node(plan: PlanDocument, node_id: str) -> PlanNode:
    """The one node of ``plan`` with that id, or a failure naming what the plan has."""
    found = [node for node in plan["tasks"] if node.get("id") == node_id]
    assert len(found) == 1, (
        f"the plan carries {len(found)} node(s) called {node_id!r}; its nodes are "
        f"{[node.get('id') for node in plan['tasks']]}"
    )
    return found[0]


@pytest.mark.xdist_group("plan-recipe")
def test_the_generated_plan_is_one_planner_node_isolated_and_carrying_the_brief(
    planned: Planned,
) -> None:
    """The plan this launch writes is the planner node and nothing else.

    Which nodes exist is asserted here because it is what everything else is read
    against, and because the count is the load-bearing half of this change: the document
    a person reviews the plan as has to be written from *reviewed* content, and a run
    cannot interject a review between its own nodes — so the design-doc node is not one
    of this run's, it is `just finish-plan`'s own launch after the review has passed.
    A plan that grew it back would be a document written from content nobody had read.
    The planner node's own fields are the rest, and every one of them is one a
    manager would otherwise have to remember. The brief
    reaching the node **verbatim** is the load-bearing one: it is the manager's own
    words, in the template every task this repository dispatches is written in, and a
    recipe that reformatted or summarized it on the way would be editing the request
    between the two people it is passing between.

    `repo` and `execution_checkout` are the pair that decides *where* the planner works,
    and they are here because their absence cost real work. Without them the node is a
    direct node, a direct node works in the launch directory, and that is the shared
    canonical checkout `AGENTS.md` forbids authoring in: a planner dispatched that way
    cut a branch there, committed, and left it checked out, which failed a finished
    publication at its last step and destroyed the manager's own plan files. `done_when`
    is still asserted absent for its own reason — the loader refuses a node carrying
    one, so a generated plan with it could never be launched at all.
    """
    plan = planned.plan
    assert plan["schema_version"] == 3, plan
    assert plan["name"] == RUN, plan
    assert plan["goal"]["text"].strip(), "the plan states no goal"
    assert [node.get("id", "") for node in plan["tasks"]] == [PLANNER_NODE], plan["tasks"]

    node = _node(plan, PLANNER_NODE)
    assert node["task"] == BRIEF.rstrip(), (
        "the brief did not reach the node verbatim; the manager's words are the task:\n"
        f"{node['task']!r}"
    )
    assert node["persona"] == PERSONA_REF, (
        f"the node names the persona {node['persona']!r}; a bare name resolves to a role "
        f"compiled into the tool and this repository's file is never read"
    )
    assert node["max_turns"] == TURN_BUDGET, node
    assert node.get("repo") == PUBLICATION_ALIAS, (
        f"the planner node names {node.get('repo')!r} as its publication checkout; with "
        f"none it is a direct node working in the shared canonical checkout: {node}"
    )
    assert node.get("execution_checkout") == EXECUTION_ALIAS, (
        f"the planner node names {node.get('execution_checkout')!r} as its execution "
        f"checkout, so its worktree is not cut from the safety clone: {node}"
    )
    assert "done_when" not in node, f"a node carrying done_when is refused at load: {node}"


@pytest.mark.xdist_group("plan-recipe")
def test_the_dispatched_planner_works_in_a_worktree_and_not_in_the_launch_checkout(
    planned: Planned,
) -> None:
    """The claim the plan document cannot make: where the planner was actually started.

    A node naming a `repo` is only half the fix — the other half is that the dispatch
    really lands in the worktree its session cut, and nothing in the document says so.
    The run's own journal does: `onevcs` appends `session-opened` naming the worktree it
    cut, `oneagentgraph` appends `member-started` naming the directory it started the
    dispatched member in, and the launch directory is in the same journal to be excluded
    against. That last exclusion is the incident: a planner started in the launch
    directory is a planner in the shared canonical checkout, which is where it cut a
    branch, committed to it, and left the checkout on it.
    """
    # No view reports where a member was started: `just status` carries what a node is
    # doing and `just work-status` the session's own worktree, and neither is the
    # directory the harness was handed. The journal is where that is recorded.
    # llmlint: ignore[tests_mirror_real_usage] No view reports a member's start directory.
    started = [
        event["payload"]["worktree"]
        for event in planned.journal
        if event.get("kind") == MEMBER_STARTED
        and event.get("labels", {}).get("member") == WORKER_MEMBER
    ]
    assert started, "the run recorded no dispatched planner at all"
    cut = {
        event["payload"]["worktree"]
        for event in planned.journal
        if event.get("kind") == SESSION_OPENED
    }
    assert cut, "the run opened no lifecycle session, so the planner cut no worktree"
    assert set(started) <= cut, (
        f"the planner was started in {sorted(set(started) - cut)}, which no session cut; "
        f"the sessions this run opened were {sorted(cut)}"
    )
    assert str(REPO_ROOT) not in started, (
        "the planner was started in the checkout the launch was made from, which "
        "concurrent orchestrators share and this repository forbids authoring in"
    )


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


# The finding these answer is about which Nx project owns this file, and that is a
# property of the `tests/e2e` tree rather than of the two journeys below: `AGENTS.md`
# records which suites were split out of the broad tier and why, this change moves no
# test between tiers, and re-homing that tree is test selection rather than anything
# here. The `xdist_group` on the second is what it is everywhere in this suite — a
# worker under `--dist loadgroup`, so a module-scoped fixture that spends a real
# launch is not run twice — and never a tier.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above
@pytest.mark.xdist_group("plan-recipe")
def test_the_project_a_planning_launch_writes_says_it_is_a_planning_project(
    planned: Planned,
) -> None:
    """The one exemption from the design-document approval every other launch is gated on.

    A planning run's output *is* the plan, so the document it will be reviewed as does
    not exist when it is launched. It is exempt because the project says so about itself
    and not because anything recognises its shape — a two-node project somebody wrote by
    hand is not exempt. That this launch reached a dispatch at all is the other half of
    the proof: the gate runs on every `just plan` as it does on every `just orchestrate`,
    so a marker the recipe stopped writing would refuse this fixture rather than reach
    this assertion.

    **The stamp names the nodes this launch writes, and the exemption is bounded to
    them** — so what is asserted is the gate's own account of that bound, read off this
    launch's own output rather than out of the record. The record's serialization belongs
    to the store and has changed under this journey before; the sentence the gate prints
    belongs to the gate, and it names every node it read. A launch whose stamp and plan
    disagree is one whose own next launch is refused, and this is where that disagreement
    shows up as a failing journey rather than as a plan that stayed exempt for life.
    """
    written = sorted(str(node["id"]) for node in planned.plan["tasks"])
    reported = planned.launch.stdout + planned.launch.stderr
    exempted = [line for line in reported.splitlines() if "launch-gate:" in line]
    assert exempted, (
        f"the launch never reported the exemption it dispatched on, so a plan somebody "
        f"read and a plan nobody had to read are one answer here:\n{reported}"
    )
    (stated,) = exempted
    assert "is the plan a planning launch is writing" in stated, stated
    assert f"{len(written)} node(s) that launch dispatches ({', '.join(written)})" in stated, (
        f"the gate read this launch's exemption as bounded to something other than the "
        f"{written} the plan holds, so the next launch of this project is refused:\n"
        f"{stated}\n\nthe record it read:\n{planned.project_record}"
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


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


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
        _remove_project(observer.run)


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
        "graph reference is blank",
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
        _remove_project(malformed.run)


@pytest.mark.reads_docs
def test_the_shipped_example_plan_is_what_the_shipped_brief_produces(tmp_path: Path) -> None:
    """The example this repository ships is the recipe's own output, not a hand-written copy.

    An example plan is the document an operator copies, so one that has drifted from
    what the recipe writes teaches a shape the recipe would never produce. Regenerating
    it here from the shipped brief is what keeps the two the same artifact — including
    the run id, which `onepipeline` normalizes on the way (a `.` becomes a `-`), so the
    example's `name` is also the run `just plan` would tell a manager to watch.

    One field is compared as the recipe wrote it rather than as the run recorded it.
    `agent_graph` names a document relative to the directory a run is launched from, and
    the loader resolves it against that directory before writing `plan.json` — so the
    run's copy is an absolute path into whichever checkout launched it, and an example
    carrying one would be a committed record of this machine. The example keeps the
    relative ref, and the resolution is undone here.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    shipped = read_project_plan(SHIPPED_PROJECT)
    environment = _environment(tmp_path)
    # `--detach` and a runs root of this journey's own: what is under test is the
    # document, and the run it starts is stopped on the way out.
    launch = _just("plan", SHIPPED_BRIEF, "--detach", environment=environment)
    try:
        assert launch.returncode == 0, f"{SHIPPED_BRIEF} did not launch:\n{launch.stderr}"
        generated_plan = json.loads(
            (
                Path(environment["ONEPIPELINE_RUNS_DIR"])
                # llmlint: ignore[suppressions_justified] CLI fixture validates this title.
                / cast(str, shipped["name"])
                / "plan.json"
            ).read_text(encoding="utf-8")
        )
        for node in generated_plan["tasks"]:
            named = node.get(AGENT_GRAPH_FIELD)
            if named is not None:
                node[AGENT_GRAPH_FIELD] = str(Path(named).relative_to(REPO_ROOT))
        assert generated_plan == shipped, (
            f"{SHIPPED_PROJECT} is not what `just plan {SHIPPED_BRIEF}` writes; "
            f"regenerate it from the brief rather than editing it by hand"
        )
    finally:
        _just("stop", str(shipped["name"]), environment=environment, seconds=60)
        # llmlint: ignore[suppressions_justified] The CLI fixture validates this title.
        _remove_project(cast(str, shipped["name"]))


#: The run a `--direct` launch is made under here, kept apart from every other launch
#: in this module so the plan it writes is unambiguously its own.
DIRECT_RUN = RunId("plan-recipe-direct")

#: What the `--direct` task has to say, in the two parts that failed a dispatch when
#: only one of them was said. The placement is what the recipe already printed on its
#: own receipt; the exemption is the half that was nowhere, and it is the half a judge
#: reads — the shared completion clause in `config/onejudge.base.yaml` demands "every
#: change this dispatch made committed" of every dispatch alike, and a `--direct`
#: planner may not commit at all.
DIRECT_PLACEMENT = "may write only to\ngitignored paths, may not commit"
DIRECT_EXEMPTION = (
    "with every change this dispatch made committed and nothing\nhalf-applied left behind"
)
DIRECT_OUTCOME = "a clean `git status` is\nthe correct and complete outcome"


@pytest.mark.reads_docs
def test_a_direct_launch_states_its_commit_exemption_in_the_task_it_dispatches(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`--direct` says in the dispatched task what the shared bar would otherwise fail it for.

    A `--direct` dispatch works in the shared canonical checkout, so it may write only to
    gitignored paths and may not commit — and the one completion clause every dispatch on
    this host is judged against demands every change committed. A planner that did
    correct, verified work settled `task-failed` against exactly that. The clause is
    right and is shared, so it does not move; the exemption is stated in the task of the
    one dispatch it is true of, which is the only place both the worker and its judge
    read it.

    Read out of the prompt the dispatched worker was really given, which is where a
    worker meets this and the only place it can be observed: a recipe that composed the
    task correctly and never got it as far as the dispatch would pass any read of what it
    wrote and fail the one thing the exemption is for.

    Attached rather than detached, unlike every other launch here, because the dispatch
    *is* the subject: a detached launch returns before the turn that carries this.

    The exemption is quoted from `config/onejudge.base.yaml` through
    `shared_completion_bar()`, not restated: a task naming a bar the base config no
    longer states would exempt a dispatch from nothing.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(BRIEF, encoding="utf-8")
    # llmlint: ignore-block[e2e_not_mocked] Only the paid provider process is
    # substituted, by `_environment`, which carries its own reason at that seam;
    # `REAL_ONEHARNESS_BIN` points the fake backend's passthrough at the real
    # `oneharness`, so it un-doubles rather than doubles. `just plan`, the recipe and
    # the launch are the real ones.
    environment = _environment(tmp_path)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    recorded = tmp_path / "turns.jsonl"
    environment[PROMPT_LOG_ENV] = str(recorded)
    # llmlint: ignore-end[e2e_not_mocked]

    # `--no-design-doc`, for the reason the module fixture carries it: what is under test
    # is the task this launch dispatches, and the tail after it is about a plan a stand-in
    # planner does not write — so every step of it would be a refusal about a project
    # nothing authored.
    launch = _just(
        "plan",
        str(brief),
        "--name",
        DIRECT_RUN,
        "--direct",
        "--no-design-doc",
        environment=environment,
    )
    try:
        assert launch.returncode == 0, f"`just plan --direct` failed:\n{launch.stderr}"
        assert "--direct dispatches it into this checkout" in launch.stderr, (
            "the launch did not report itself as a direct placement, so whatever it "
            f"dispatched is not the shape this exemption is about:\n{launch.stderr}"
        )
        # llmlint: ignore-block[tests_mirror_real_usage] The effective prompt is the
        # only place a dispatched task is observable; no published view carries it. The
        # fake backend writes this JSONL itself and TurnRecord states the schema it owns
        # on both ends, so this reads a test-owned file rather than reaching past
        # somebody else's validation.
        turns = [
            cast(TurnRecord, json.loads(line))
            for line in recorded.read_text(encoding="utf-8").splitlines()
        ]
        # llmlint: ignore-end[tests_mirror_real_usage]
        dispatched = [
            _flattened(turn["prompt"]) for turn in turns if _member(turn) == WORKER_MEMBER
        ]
        assert dispatched, f"no dispatched turn was recorded in {recorded}"

        assert any(_flattened(BRIEF) in prompt for prompt in dispatched), (
            "the brief did not reach the dispatch verbatim; the manager's words are the "
            f"task and everything the recipe appends comes after them:\n{dispatched}"
        )
        for stated, what in (
            (DIRECT_PLACEMENT, "that it works in a checkout it does not own and may not commit"),
            (DIRECT_EXEMPTION, "which clause of the shared bar it is exempt from"),
            (DIRECT_OUTCOME, "what the correct outcome is instead"),
        ):
            assert any(_flattened(stated) in prompt for prompt in dispatched), (
                f"the dispatched task does not say {what}, so the worker and its judge "
                f"read the shared bar with nothing in the task that answers it:\n{dispatched}"
            )
        assert _flattened(DIRECT_EXEMPTION) in _flattened(shared_completion_bar()), (
            "the task quotes a demand `config/onejudge.base.yaml` no longer makes, so it "
            f"exempts this dispatch from nothing:\n{shared_completion_bar()}"
        )
    finally:
        _just("stop", DIRECT_RUN, environment=environment, seconds=60)
        _remove_project(DIRECT_RUN)


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
    # The placement flags, whose empty forms are the same shell expansion the two above
    # guard against — and whose half-set combination is the one a caller reaches by
    # ordering rather than by typing an empty value. Half a placement is not a smaller
    # mistake than none: an execution checkout with no repository names a clone nothing
    # is cut from, and a repository with no execution checkout puts the dispatch back in
    # the shared checkout this change exists to keep it out of.
    Refusal("an unnamed repository", ("--repo",), "--repo was given no value"),
    Refusal("an empty repository", ("--repo=",), "--repo was given no value"),
    Refusal("an empty separated repository", ("--repo", ""), "--repo was given no value"),
    Refusal(
        "an unnamed execution checkout",
        ("--execution-checkout",),
        "--execution-checkout was given no value",
    ),
    Refusal(
        "an empty execution checkout",
        ("--execution-checkout=",),
        "--execution-checkout was given no value",
    ),
    Refusal(
        "half a placement",
        ("--direct", "--repo", "elsewhere"),
        "a node carries both or neither",
    ),
    # A destination for a flow that stops before there is anything to copy. Refused
    # rather than ignored, because the two readings of it are opposite: one caller means
    # "finish this plan into that source" and the other means "launch the planner alone",
    # and silently taking the second discards a destination somebody typed.
    Refusal(
        "a destination and no document to copy",
        ("--to", "elsewhere", "--no-design-doc"),
        "--to names the destination the tail copies this plan into",
    ),
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


#: The grammar both planning entry points read a brief through, driven below as what it
#: is: a sourced shell library whose interface is its functions. `just plan` and `just
#: finish-plan` cannot reach the states these journeys are about — each tests the brief
#: for a readable regular file before it reads one, so a path that cannot be read is
#: refused by that test and never reaches the readers underneath it. What is driven here
#: is therefore the boundary itself, with nothing doubled: the real file, sourced by a
#: real bash, over real paths, reading through the real `cat` on this host.
BRIEF_GRAMMAR = REPO_ROOT / "scripts" / "plan-brief.sh"

#: The two functions of it that read a brief, and what each is asked. Both are covered
#: because a reader repaired in one of them and not the other is exactly the state this
#: is about: the first decides whether a brief is a task, the second what plan it names,
#: and either one reporting a failed read as a statement about the brief's content sends
#: its author to rewrite a file that was never the problem.
BRIEF_READERS = ("plan_brief_is_a_task", "plan_brief_project")

#: Every way this grammar says a refusal is about the file rather than about what the file
#: says. Two, because the two readers reach that answer at different depths and both are
#: right: `plan_brief_is_a_task` tests the path for a readable regular file first and
#: refuses there, and `plan_brief_project` is handed a brief a caller has already tested
#: and so meets the failure in the reader itself.
READ_REFUSALS = ("could not be read", "is not a readable file")

#: What only a refusal about the content may carry. This is the half that makes these
#: journeys about distinguishability rather than about wording: both sentences are what an
#: unreadable brief was refused with before the reader was repaired, so a reader that
#: answered one of them again would pass every "was it refused" assertion while sending
#: its author to edit a file that was never the problem.
CONTENT_REFUSALS = ("states no '## ", "names no plan project")


def _grammar(function: str, brief: Path) -> subprocess.CompletedProcess[str]:
    """Run one reader of the real grammar over ``brief``, in a real bash.

    The helper establishes `set -e` on its own account, so a refusal leaves the shell at
    the status the function returned and this reads it rather than reasoning about it.
    """
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"\n"$2" plan "$3"',
            "grammar",
            str(BRIEF_GRAMMAR),
            function,
            str(brief),
        ],
        capture_output=True,
        text=True,
        timeout=e2e_timeout(60),
        cwd=REPO_ROOT,
        check=False,
    )


@pytest.mark.parametrize("function", BRIEF_READERS)
@pytest.mark.parametrize("unreadable", ("absent", "directory"))
def test_a_brief_that_cannot_be_read_is_refused_as_unread_rather_than_as_wrong(
    tmp_path: Path, function: str, unreadable: str
) -> None:
    """A read that failed is reported as one, never as a statement about the content.

    `grep` answers a missing section with 1 and a read error with 2, and bash's `read`
    answers a genuine end of file and a failed read alike — so a reader that consults
    neither status reports a brief it could not read as a brief that is wrong. Measured
    on this file before the reader was repaired, called the way both launchers call it —
    after a `||`, which suppresses the `set -e` the helper establishes: `plan_brief_project`
    over a path that does not exist answered *"names no plan project ... add a line reading
    'Plan project: <source>:<project>'"*, which is an instruction to edit a file that is
    not there, and over a directory it fell out of the loop with an unset `line`.

    Both shapes are driven because they fail at different moments — one cannot be opened
    and one opens and then refuses to be read — and a reader that only checked whether
    the path existed would part them.
    """
    brief = tmp_path / "brief.md"
    if unreadable == "directory":
        brief.mkdir()

    refused = _grammar(function, brief)

    assert refused.returncode != 0, f"an unreadable brief was accepted:\n{refused.stdout}"
    reported = refused.stderr + refused.stdout
    assert any(refusal in reported for refusal in READ_REFUSALS), (
        f"{function} refused an unreadable brief without saying it could not read it, so "
        f"its author is told something about the brief's content instead:\n{reported}"
    )
    stated = [line for line in reported.splitlines() if line.startswith("plan: ")]
    assert stated and all("; " in line for line in stated), (
        f"the refusal states no remedy beside its cause:\n{reported}"
    )
    for refusal in CONTENT_REFUSALS:
        assert refusal not in reported, (
            f"{function} refused a brief it could not read as though it had read one: "
            f"{refusal!r} is a statement about content, and there is none to state "
            f"anything about:\n{reported}"
        )


@pytest.mark.parametrize(
    ("function", "content", "names"),
    (
        ("plan_brief_is_a_task", "## What\nx\n\n## Why\ny\n", "states no '## Acceptance criteria'"),
        ("plan_brief_project", "## What\nx\n", "names no plan project"),
    ),
    ids=("a missing section", "no plan project"),
)
def test_a_brief_that_reads_and_is_wrong_is_still_refused_for_what_it_says(
    tmp_path: Path, function: str, content: str, names: str
) -> None:
    """The other half: a readable brief is judged on its content, and says so.

    Without this the journey above is satisfied by a reader that refused everything as
    unreadable, which would be the same defect pointing the other way — an author told to
    check a path when what is wrong is the brief. The two together are the property: what
    is refused names which of the two happened.
    """
    brief = tmp_path / "brief.md"
    brief.write_text(content, encoding="utf-8")

    refused = _grammar(function, brief)

    assert refused.returncode != 0, f"a brief that is not a task was accepted:\n{refused.stdout}"
    reported = refused.stderr + refused.stdout
    assert names in reported, reported
    for refusal in READ_REFUSALS:
        assert refusal not in reported, (
            f"{function} refused a brief it read perfectly well as one it could not read, "
            f"which sends its author to check a path that is fine:\n{reported}"
        )


@pytest.mark.parametrize("terminated", (True, False))
def test_a_brief_the_readers_can_read_is_read_whole(tmp_path: Path, terminated: bool) -> None:
    """And a brief that is a task still parses, including its last line.

    The reader these journeys are about replaced a `read` loop whose `|| [ -n "$line" ]`
    was there for a final line with no newline — and which caught a failed read as one
    too. Dropping that condition is only sound if the last line is still read, so both
    endings are driven and the declaration is deliberately the last line of the brief.
    """
    brief = tmp_path / "brief.md"
    declaration = "Plan project: authoring:cursor-shape"
    brief.write_text(
        f"## What\nx\n\n## Why\ny\n\n## Acceptance criteria\n- z\n\n{declaration}"
        + ("\n" if terminated else ""),
        encoding="utf-8",
    )

    assert _grammar("plan_brief_is_a_task", brief).returncode == 0, (
        "a brief carrying every required section was refused"
    )
    read = _grammar("plan_brief_project", brief)

    assert read.returncode == 0, f"a brief naming its plan project was refused:\n{read.stderr}"
    assert read.stdout == "authoring:cursor-shape", (
        f"the plan project was not read off the brief's last line:\n{read.stdout!r}"
    )


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
    environment = _environment(tmp_path)

    launch = _just("plan", str(brief), "--detach", environment=environment)
    try:
        assert launch.returncode == 0, f"{brief.name} did not launch:\n{launch.stderr}"
        named = json.loads(
            (Path(environment["ONEPIPELINE_RUNS_DIR"]) / "cursor-shape" / "plan.json").read_text(
                encoding="utf-8"
            )
        )["name"]
        assert named == "cursor-shape", named
        assert f"just channel-next {named}" in launch.stdout + launch.stderr, launch.stderr
    finally:
        _just("stop", "cursor-shape", environment=environment, seconds=60)
        _remove_project("cursor-shape")


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


#: A brief written as a task in every way except that it names no plan project. Kept
#: apart from `BRIEF` above, which does name one, so the two journeys below differ in
#: exactly that line and nothing else.
BRIEF_NAMING_NO_PLAN = """## What
Decide whether the paginated listing's cursor is an opaque token or a node id.

## Why
The browser view cannot deep-link to a page until that is settled.

## Acceptance criteria
- The cursor's shape and its type are stated.
"""

#: A brief missing a required section, which is the refusal the one below is held to the
#: exit status of: both are a brief that is not yet a task, so both end the same way.
BRIEF_MISSING_A_SECTION = "## What\nDecide the cursor.\n\n## Why\nThe view is blocked.\n"


def test_a_brief_naming_no_plan_project_is_refused_like_a_brief_missing_a_section(
    tmp_path: Path,
) -> None:
    """The design-doc node has no other way to find the plan, so the brief has to name it.

    Nothing hands one node's output to a later node, and a plan written to an ignored path
    in the planner's own worktree does not outlive the run — so a launch that accepted a
    brief naming no project would dispatch a document writer at a plan it cannot open, and
    the manager would find out from the dispatch rather than from the launch. It is
    refused where a brief that is not yet a task is refused, and it says what to add,
    because a refusal a manager has to go and read the recipe to answer is one they route
    around.

    The two exit statuses are compared rather than asserted at a literal, so the pair
    cannot part: an operator scripting `just plan` branches on the status, and a new
    refusal that ended differently would be a second thing to branch on.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    naming_none = tmp_path / "names-no-plan.md"
    naming_none.write_text(BRIEF_NAMING_NO_PLAN, encoding="utf-8")
    missing_a_section = tmp_path / "thin.md"
    missing_a_section.write_text(BRIEF_MISSING_A_SECTION, encoding="utf-8")
    environment = _environment(tmp_path)

    refused = _just("plan", str(naming_none), environment=environment)
    compared = _just("plan", str(missing_a_section), environment=environment)

    assert refused.returncode != 0, (
        f"a brief naming no plan project was launched:\n{refused.stdout}{refused.stderr}"
    )
    assert refused.returncode == compared.returncode, (
        f"a brief naming no plan project exits {refused.returncode} where a brief missing "
        f"a required section exits {compared.returncode}; both are a brief that is not yet "
        "a task and a caller branching on the status has to treat them alike"
    )
    reported = refused.stderr + refused.stdout
    assert "names no plan project" in reported, reported
    assert "Plan project: <source>:<project>" in reported, (
        f"the refusal never says what line to add:\n{reported}"
    )
    assert "--no-design-doc" in reported, (
        f"the refusal never names the opt-out, so the only way out of it looks like "
        f"editing a brief that may not need one:\n{reported}"
    )


class UnusablePlanLine(NamedTuple):
    """One brief that declares a plan project this launch cannot use."""

    what: str
    #: The brief as written, declaration and all.
    brief: str
    #: The fragment the refusal has to carry, which is what tells the two apart.
    names: str


#: The declarations that are present and unusable. Each is kept apart from a brief that
#: declares nothing, because the two want opposite remedies: one is answered by adding a
#: line, and every one of these by repairing a line that is already there.
UNUSABLE_PLAN_LINES = (
    UnusablePlanLine(
        "two plan projects",
        BRIEF_NAMING_NO_PLAN.replace(
            "## Why",
            "Plan project: authoring:cursor-shape\nPlan project: authoring:cursor-colour\n\n## Why",
            1,
        ),
        "names 2 plan projects",
    ),
    UnusablePlanLine(
        "an unqualified plan project",
        BRIEF_NAMING_NO_PLAN.replace("## Why", "Plan project: cursor-shape\n\n## Why", 1),
        "is not a qualified id",
    ),
    UnusablePlanLine(
        "a plan project line naming nothing",
        BRIEF_NAMING_NO_PLAN.replace("## Why", "Plan project:\n\n## Why", 1),
        "is not a qualified id",
    ),
)


@pytest.mark.parametrize("unusable", UNUSABLE_PLAN_LINES, ids=lambda row: row.what)
def test_a_brief_declaring_a_plan_project_it_cannot_use_is_refused_as_a_bad_value(
    tmp_path: Path, unusable: UnusablePlanLine
) -> None:
    """A declaration that is present and unusable is refused as one, not as an absence.

    The value is read out of a manager's prose and is the only thing parsed out of it, so
    the two ways it can be unusable are the two this launch has to tell apart from "no
    line at all": two declarations, where taking the earlier one silently dispatches the
    document writer at a plan its author may not have meant, and a value that names a
    project in no store, where reporting it as a missing line sends a manager looking for
    a line that is already in front of them.

    Held to the same shape every other refusal here is: a non-zero exit, and a `plan: `
    line stating the cause and a remedy beside it.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    brief = tmp_path / "unusable.md"
    brief.write_text(unusable.brief, encoding="utf-8")

    refused = _just("plan", str(brief), environment=_environment(tmp_path))

    assert refused.returncode != 0, (
        f"a brief declaring {unusable.what} was launched:\n{refused.stdout}{refused.stderr}"
    )
    reported = refused.stderr + refused.stdout
    assert unusable.names in reported, reported
    assert "names no plan project" not in reported, (
        f"the refusal reports a declaration that is there as one that is not, so the "
        f"remedy it offers is to add a second line:\n{reported}"
    )
    stated = [line for line in reported.splitlines() if line.startswith("plan: ")]
    assert stated and all("; " in line for line in stated), (
        f"the refusal states no remedy beside its cause:\n{reported}"
    )


#: The run the opt-out journey below launches under, kept apart from every other launch
#: in this module so the plan it writes is unambiguously its own.
NO_DESIGN_DOC_RUN = RunId("plan-recipe-no-design-doc")


def test_the_opt_out_drops_the_tail_and_the_brief_requirement_the_tail_is_the_reason_for(
    tmp_path: Path,
) -> None:
    """`--no-design-doc` drops the whole tail, its brief requirement, and nothing else.

    The document is produced by default because it is what a person approves before a plan
    is dispatched, so opting out has to be explicit — and it has to opt out of the
    *requirement* with it: the `Plan project:` line exists so the tail can find the plan
    it is finishing, and demanding one would refuse a launch that has no use for the
    answer. This brief names none, so the launch succeeding is the assertion.

    The flag is consumed here rather than forwarded, and the launch succeeding says that
    too: `onepipeline start` is closed over its own surface and refuses a flag it does not
    know, so a run recorded under this name is a run the flag never reached.

    `--detach`, because what is under test is the document this writes and the launch it
    makes, not the dispatches that follow.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    brief = tmp_path / "names-no-plan.md"
    brief.write_text(BRIEF_NAMING_NO_PLAN, encoding="utf-8")
    environment = _environment(tmp_path)

    launch = _just(
        "plan",
        str(brief),
        "--name",
        NO_DESIGN_DOC_RUN,
        "--detach",
        "--no-design-doc",
        environment=environment,
    )
    try:
        assert launch.returncode == 0, (
            "`just plan --no-design-doc` was refused, so either the flag reached "
            f"`onepipeline start` or the brief was still held to naming a plan:\n"
            f"{launch.stdout}{launch.stderr}"
        )
        run_root = Path(environment["ONEPIPELINE_RUNS_DIR"]) / NO_DESIGN_DOC_RUN
        assert (run_root / "launch.json").is_file(), (
            f"no run was recorded at {run_root}, so nothing says the launch was made"
        )
        # `cast` rather than a validating read, for the reason the module's other launches
        # give: the recipe writes this document, `PlanDocument` states what it promises,
        # and the subscripts below fail loudly if it is not that shape.
        plan = cast(PlanDocument, json.loads((run_root / "plan.json").read_text(encoding="utf-8")))
        assert [node.get("id") for node in plan["tasks"]] == [PLANNER_NODE], (
            f"`--no-design-doc` still wrote {[node.get('id') for node in plan['tasks']]}"
        )
        assert plan["tasks"][0]["task"] == BRIEF_NAMING_NO_PLAN.rstrip(), (
            "the one node's task is not the brief verbatim; opting out of the tail "
            f"changes nothing about the planner:\n{plan['tasks'][0]['task']!r}"
        )
        # And nothing after the planner ran: the tail's own run is named from this one,
        # so a ledger holding it would be the opt-out having been read as an opt-out of
        # the node alone.
        assert not (
            Path(environment["ONEPIPELINE_RUNS_DIR"]) / f"{NO_DESIGN_DOC_RUN}-design"
        ).exists(), "`--no-design-doc` launched the design document anyway"
    finally:
        _just("stop", NO_DESIGN_DOC_RUN, environment=environment, seconds=60)
        _remove_project(NO_DESIGN_DOC_RUN)


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
    for name in ("plan.sh", "plan-brief.sh", "credentials-env.sh", "ask-manager-env.sh"):
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
