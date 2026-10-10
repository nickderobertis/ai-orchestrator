"""`just plan` runs draft → spikes → finalize → review → design doc, end to end.

Each journey drives the real recipe in a copy of this checkout: `scripts/plan.sh`, its
helpers, `orchestrator/spike_flow.py`, the real `just review-plan`, `just check-plan`, `just
unpublished --acknowledge`, `scripts/finish-plan.sh` and its copy and report, the launch
gate in `scripts/onepipeline.sh`, the pinned plan store, the pinned `onevcs` over a scratch
registry with a real Git origin, and `just approve-design`. **Only the engine's `start` is
doubled**, at the copy's `.venv/bin/onepipeline`, by `tests/plan_tooling/spike_flow_engine.py`:
it does with those same real tools what each stage's run leaves behind — the plan and its
spikes written into the store, each spike's branch cut, committed, preserved and released
through `onevcs` and its report rendered, the plan regenerated from the reports, the design
document rendered — and writes the run's ledger record. The paid reviewer `just review-plan`
spends a turn on is the stand-in every journey here uses.

It runs in a copy because the wrapper runs the engine at its checkout's own
`.venv/bin/onepipeline` and nowhere else, and the copy's migration list names the one plan
that predates budgets, which only a reviewed change to the tracked list could otherwise do.

llmlint: ignore-file[e2e_not_mocked, tests_mirror_real_usage] The engine's `start` is
doubled because this node's acceptance criteria require exactly that boundary and no other:
a real `start` dispatches the paid model and a lifecycle session for every planner, spike
and document node. Everything the doubled start leaves behind is made by the real store,
templates and `onevcs` it calls; every recipe, script and check around it is the real one.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] These journeys copy the
tracked tree, which reads all of it, so they sit in `plan-tooling`'s whole-workspace
`test-docs` target as `test_plan_flow_e2e.py`'s default-board journey does, for the reason
its `COPIES_THE_TRACKED_TREE` states.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `reads_docs` chooses between
`plan-tooling`'s own two targets and routes nothing out of the project, as
`tests/plan_tooling/AGENTS.md` states.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple, NewType, cast

import plan_fixture_source
import pytest
import short_state
from nx_workspace import answering_this_checkouts_origin, copy_working_tree
from project_fixtures import no_budgets
from scratch_identity import PLANNING_FLOW_ORIGIN, seeded
from spike_flow_engine import Node
from test_plan_flow_e2e import (
    DESTINATION,
    FAKE_CODEX,
    INHERITED_ENVIRONMENT,
    PAID_PROVIDER_GUARD,
    PASSING_VERDICT,
    _provisioned,
)
from waits import timeout as e2e_timeout

from orchestrator import design_approval, spike_plan

#: Copies the tracked tree, so it sits in the whole-workspace tier.
pytestmark = [pytest.mark.reads_docs, pytest.mark.xdist_group("plan-spike-flow")]

#: A run id, a plan's qualified project, and a spike's node id.
RunId = NewType("RunId", str)
PlanProject = NewType("PlanProject", str)
SpikeId = NewType("SpikeId", str)

#: The doubled engine, installed over the copy's own.
STAND_IN = Path(__file__).with_name("spike_flow_engine.py")

FIXTURE_SOURCE = plan_fixture_source.SOURCE
LAUNCHING_SESSION = "e2e-plan-spike-flow"

#: Criteria answering every demand the appendix and the built-in bar make.
CRITERIA = [
    "The listing pages at 2,000 nodes within the latency the spike measured as achievable.",
    "A request-level test drives the listing end to end and covers the last page.",
    "Every claim the dispatch makes about the finished work is true of the tree as it "
    "finally stands.",
]

#: The design document the doubled writer answers, beside the budget answers its task quotes.
DESIGN: dict[str, object] = {
    "what": "A paginated node listing.",
    "why": "An operator cannot see past the first screen.",
    "architecture": "One route, one view.",
    "units": [
        {
            "name": "Listing",
            "repository": "ai-orchestrator",
            "part": "",
            "summary": "The listing pages behind an opaque cursor.",
            "reversible": [{"title": "The cursor", "text": "An opaque token."}],
            "decisions": [],
        }
    ],
    "acceptance_criteria": ["The listing pages."],
    "planned_tasks": [
        {
            "task": "feat: page the node listing",
            "unit": "Listing",
            "delivers": "the route",
            "depends_on": "none",
            "location": "the store's own location",
        }
    ],
}


class Workspace(NamedTuple):
    """The copied checkout every journey here runs `just plan` in."""

    checkout: Path
    migrated: PlanProject


class Flow(NamedTuple):
    """One journey's flow: its names, its environment, and what to read afterwards."""

    run: RunId
    plan: PlanProject
    spikes: tuple[SpikeId, ...]
    brief: Path
    environment: dict[str, str]
    log: Path
    runs: Path
    checkout: Path


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Workspace:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    checkout = tmp_path_factory.mktemp("plan-spike-flow") / "checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    answering_this_checkouts_origin(checkout)
    _provisioned(checkout)
    engine = checkout / ".venv" / "bin" / "onepipeline"
    real = engine.with_name("onepipeline-real")
    engine.rename(real)
    # A shell script that hands every call to the stand-in on its second line, followed by
    # the real binary's bytes, which bash never reads: `just check-plan` reads the roles a
    # persona name resolves to out of the installed engine's own bytes, so they stay there.
    engine.write_bytes(
        f"#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} "
        f'{shlex.quote(str(STAND_IN))} "$@"\n'.encode()
        + real.read_bytes()
    )
    engine.chmod(0o755)
    migrated = PlanProject(f"{FIXTURE_SOURCE}:test-{os.getpid()}-migrated-plan")
    with (checkout / "config" / "budgets-migration.yaml").open("a", encoding="utf-8") as listed:
        listed.write(
            f'- project: "{migrated}"\n'
            f'  reason: "`{migrated}` is a journey\'s plan written before budgets existed."\n'
        )
    return Workspace(checkout, migrated)


def _flow(
    workspace: Workspace,
    tmp_path: Path,
    key: str,
    *,
    spikes: bool = True,
    budgets: bool = True,
    plan: PlanProject | None = None,
    session: bool = True,
    topics: tuple[str, ...] = ("listing", "quota"),
    **scenario: object,
) -> Flow:
    """A brief, a scratch registry and the doubled engine's scenario for one journey."""
    run = RunId(f"spike-flow-{key}-{os.getpid()}")
    qualified = plan or PlanProject(f"{FIXTURE_SOURCE}:test-{os.getpid()}-{key}-plan")
    named = tuple(SpikeId(f"spike-{key}-{topic}") for topic in topics if spikes)
    repository = str(scenario.get("repository", PLANNING_FLOW_ORIGIN))
    identity = seeded(tmp_path / "identity", origin=repository)
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    log = tmp_path / "starts.jsonl"
    log.touch()
    destination = tmp_path / "destination"
    destination.mkdir()
    bin_dir = workspace.checkout / ".venv" / "bin"
    environment.update(identity.environment)
    if session:
        environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment |= {
        "ONEVCS_HOME": str(identity.home),
        "ONEPIPELINE_RUNS_DIR": str(tmp_path / "runs"),
        "XDG_STATE_HOME": str(short_state.state_home(tmp_path)),
        "UV_NO_SYNC": "1",
        # llmlint: ignore[e2e_not_mocked] Only the paid reviewer's provider is substituted.
        "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
        # llmlint: ignore[e2e_not_mocked] Only the paid reviewer's provider is substituted.
        "PATH": f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}",
        "FAKE_CODEX_ANSWERS": json.dumps([PASSING_VERDICT]),
        f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__PLUGIN": "local-md",
        f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__CONFIG__ROOT": str(destination),
        "ONETASKGRAPH_DEFAULT_SOURCES": f"authoring,{FIXTURE_SOURCE},{DESTINATION}",
        "FAKE_ENGINE_REAL": str(bin_dir / "onepipeline-real"),
        "FAKE_ENGINE_STORE": str(bin_dir / "onetaskgraph"),
        "FAKE_ENGINE_ONEVCS": str(bin_dir / "onevcs"),
        "FAKE_ENGINE_LOG": str(log),
        "FAKE_ENGINE_SCENARIO": json.dumps(
            {
                "run": run,
                "plan": qualified,
                "root": str(plan_fixture_source.root()),
                "repository": PLANNING_FLOW_ORIGIN,
                "task": {
                    "what": "Page the node listing.",
                    "why": "An operator cannot see past the first screen.",
                    "acceptance_criteria": CRITERIA,
                },
                "spike_task": {
                    "what": "Measure the listing at 2,000 nodes.",
                    "why": "The listing's budget needs a measured number.",
                    "acceptance_criteria": CRITERIA,
                },
                "spikes": list(named),
                "budgets": no_budgets([repository]) if budgets else None,
                "design": DESIGN,
                **scenario,
            }
        ),
    }
    brief = tmp_path / f"{run}.md"
    brief.write_text(
        "## What\nPlan the paginated listing.\n\n"
        f"Plan project: {qualified}\n\n"
        "## Why\nAn operator cannot see past the first screen.\n\n"
        "## Acceptance criteria\n- The plan pages the listing.\n",
        encoding="utf-8",
    )
    return Flow(
        run, qualified, named, brief, environment, log, tmp_path / "runs", workspace.checkout
    )


def _just(flow: Flow, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=flow.checkout,
        env=flow.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )


def _plan(flow: Flow, *flags: str) -> subprocess.CompletedProcess[str]:
    return _just(flow, "plan", str(flow.brief), "--name", flow.run, "--to", DESTINATION, *flags)


def _resumed(
    flow: Flow, done: subprocess.CompletedProcess[str]
) -> subprocess.CompletedProcess[str]:
    """Run the one command a stopped or detached flow printed to carry it on."""
    said = done.stderr
    marker = "carry the flow on with: "
    assert marker in said, f"the flow printed no command to carry it on:\n{said}"
    command = said.rsplit(marker, 1)[1].splitlines()[0].rstrip(".")
    assert command.startswith("just plan "), command
    return _just(flow, *shlex.split(command)[1:])


def _starts(flow: Flow) -> list[dict[str, object]]:
    return [json.loads(line) for line in flow.log.read_text(encoding="utf-8").splitlines()]


def _runs_started(flow: Flow) -> list[str]:
    return [str(start["run"]) for start in _starts(flow)]


def _store(flow: Flow, *arguments: str) -> dict[str, object]:
    shown = subprocess.run(
        [str(flow.checkout / ".venv" / "bin" / "onetaskgraph"), *arguments, "--json"],
        cwd=flow.checkout,
        env=flow.environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    answered: dict[str, object] = json.loads(shown.stdout)
    return answered


# llmlint: ignore[suppressions_justified] The plan store owns this open JSON listing; the
# fields read off each item are narrowed where they are read.
def _items(listed: dict[str, object]) -> list[dict[str, Any]]:
    """The items of one of the store's JSON listings."""
    return cast(list[dict[str, Any]], listed["items"])


def _stage_runs(flow: Flow) -> list[str]:
    return [flow.run, f"{flow.run}-spikes", f"{flow.run}-finalize", f"{flow.run}-design"]


def _branches(flow: Flow) -> list[str]:
    return [f"{flow.plan.partition(':')[2]}/{spike}" for spike in flow.spikes]


def _acknowledged(flow: Flow) -> dict[str, str]:
    """Each branch this session acknowledged, and the reason it gave, from `just unpublished`."""
    listed = _just(flow, "unpublished", "--host", "--json", "--no-disk")
    rows = json.loads(listed.stdout) if listed.stdout.strip() else []
    return {
        str(row["branch"]): str(row["acknowledgement"]["reason"])
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("acknowledgement"), dict)
    }


@pytest.fixture(scope="module")
def spiked(
    workspace: Workspace, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Flow, subprocess.CompletedProcess[str]]:
    """One whole flow whose draft wrote spikes, driven by `just plan` and nothing else."""
    flow = _flow(workspace, tmp_path_factory.mktemp("spiked"), "spiked")
    return flow, _plan(flow)


def test_a_drafts_spikes_are_stamped_checked_and_launched_with_a_branch_template_naming_the_plan(
    spiked: tuple[Flow, subprocess.CompletedProcess[str]],
) -> None:
    flow, done = spiked
    assert done.returncode == 0, done.stdout + done.stderr
    spikes_project = spike_plan.spikes_project(flow.plan)

    (record,) = _items(_store(flow, "project", "show", spikes_project))
    metadata = record["item"]["metadata"]
    assert metadata[design_approval.PLAN_KIND] == {
        design_approval.STAMP_KIND: design_approval.SPIKES,
        design_approval.STAMP_NODES: list(flow.spikes),
    }
    assert metadata["onepipeline.name"] == f"{flow.run}-spikes"
    # Checked: the check accepted the spikes project's two nodes, as well as the plan's one.
    assert f"check-plan: {len(flow.spikes)} dispatched node(s)" in done.stdout + done.stderr
    assert "check-plan: 1 dispatched node(s)" in done.stdout + done.stderr
    (launched,) = [start for start in _starts(flow) if start["run"] == f"{flow.run}-spikes"]
    arguments = launched["arguments"]
    assert isinstance(arguments, list)
    template = arguments[arguments.index("--branch-template") + 1]
    assert template == f"{flow.plan.partition(':')[2]}/{{{{ node.id }}}}"
    assert launched["project"] == spikes_project


def test_every_preserved_spike_branch_is_acknowledged_naming_the_plan(
    spiked: tuple[Flow, subprocess.CompletedProcess[str]],
) -> None:
    flow, done = spiked
    assert done.returncode == 0, done.stdout + done.stderr

    acknowledged = _acknowledged(flow)
    for branch in _branches(flow):
        assert branch in acknowledged, f"{branch} was not acknowledged: {acknowledged}"
        assert flow.plan in acknowledged[branch]
        assert "until the plan's main run succeeds" in acknowledged[branch]


def test_the_finalize_planner_runs_after_the_spikes_and_its_closeout_before_the_tail(
    spiked: tuple[Flow, subprocess.CompletedProcess[str]],
) -> None:
    flow, done = spiked
    assert done.returncode == 0, done.stdout + done.stderr

    assert _runs_started(flow) == _stage_runs(flow)
    (finalize,) = [start for start in _starts(flow) if start["run"] == f"{flow.run}-finalize"]
    assert finalize["project"] == f"authoring:{flow.run}-finalize"
    # The finalize planner linked its task to each report and branch, which the tail's
    # review and check read and its copy carried to the destination.
    (task,) = _items(
        _store(
            flow,
            "task",
            "list",
            "--source",
            FIXTURE_SOURCE,
            "--project",
            flow.plan.partition(":")[2],
        )
    )
    content = str(task["item"]["content"])
    for spike, branch in zip(flow.spikes, _branches(flow), strict=True):
        assert (
            f"- `{spike}`: report `{spike}-report`" in content and f"branch `{branch}`" in content
        )
    assert "holds the plan at" in done.stdout + done.stderr


def test_no_node_of_the_flow_launches_under_the_planner_panel(
    spiked: tuple[Flow, subprocess.CompletedProcess[str]],
) -> None:
    """The draft, finalize and spike nodes name no graph; the document keeps its own.

    `graphs/planner.yaml` judges a planner with the plan checklist beside its reviewer, and
    onejudge refuses that panel while this host's reviewer runs in a writable mode the
    graph cannot accept on the split's behalf, so no node of the flow names it yet.
    """
    flow, done = spiked
    assert done.returncode == 0, done.stdout + done.stderr

    graphs: dict[str, list[object]] = {}
    for start in _starts(flow):
        source, _, native = str(start["project"]).partition(":")
        listed = _items(_store(flow, "task", "list", "--source", source, "--project", native))
        graphs[str(start["run"])] = [
            item["item"]["metadata"].get("onepipeline.agent_graph") for item in listed
        ]
    assert graphs[flow.run] == [None]
    assert graphs[f"{flow.run}-finalize"] == [None]
    assert graphs[f"{flow.run}-spikes"] == [None] * len(flow.spikes)
    assert graphs[f"{flow.run}-design"] == ["graphs/design-doc.yaml"]
    assert not any("planner.yaml" in str(graph) for named in graphs.values() for graph in named)


def test_the_tail_reports_what_the_plan_checklist_cost_the_flow(
    spiked: tuple[Flow, subprocess.CompletedProcess[str]],
) -> None:
    """Both `planning` budgets are checked over the flow just finished, as its last step.

    Measured once the flow has ended, the way a landed change's cycle time is, and over the
    flow this launch ran: its draft run and each review of its plan since it launched. The
    finalize planner revised the plan the draft wrote, so the tail's review spent the
    checklist on it, and that run is the one each budget's breakdown names.
    """
    flow, done = spiked
    assert done.returncode == 0, done.stdout + done.stderr

    reported = done.stderr
    for budget in ("plan-checklist-added-seconds", "plan-checklist-tokens"):
        assert f"budget {budget}: actual" in reported, reported
    assert f"draft run {flow.run}: no checklist call recorded" in reported, reported
    assert "plan-review run " in reported and f" of {flow.plan}" in reported, reported


def test_a_draft_that_wrote_no_spikes_goes_straight_to_the_tail(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "unspiked", spikes=False)

    done = _plan(flow)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _runs_started(flow) == [flow.run, f"{flow.run}-design"]
    assert not (flow.runs / f"{flow.run}-spikes").exists()


def test_resume_spikes_finds_kept_branches_in_another_registered_repository(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(
        workspace,
        tmp_path,
        "cross-repo",
        topics=("listing",),
        repository="github.com/nickderobertis/onevcs",
    )
    onevcs = str(flow.checkout / ".venv" / "bin" / "onevcs")
    registered = subprocess.run(
        [onevcs, "register", str(flow.checkout)],
        cwd=flow.checkout,
        env=flow.environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert registered.returncode == 0, registered.stderr
    drafted = _plan(flow, "--detach")
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr
    spiked = _resumed(flow, drafted)
    assert spiked.returncode == 0, spiked.stdout + spiked.stderr
    assert _runs_started(flow) == [flow.run, f"{flow.run}-spikes"]
    command = [onevcs, "recoverable", "--label", f"run={flow.run}-spikes", "--json"]
    scoped = subprocess.run(
        command, cwd=flow.checkout, env=flow.environment, capture_output=True, text=True, check=True
    )
    assert json.loads(scoped.stdout) == []
    all_identities = subprocess.run(
        command, cwd=Path("/"), env=flow.environment, capture_output=True, text=True, check=True
    )
    (kept,) = json.loads(all_identities.stdout)
    spike = flow.spikes[0]
    assert kept["labels"]["node"] == spike
    registry = Path(flow.environment["ONEVCS_HOME"])
    records = list((registry / "sessions").glob("*.json"))
    assert records, "the real session lifecycle wrote no records"
    for record in records:
        if json.loads(record.read_text()).get("labels", {}).get("run") == f"{flow.run}-spikes":
            record.unlink()
    forgotten = subprocess.run(
        command, cwd=Path("/"), env=flow.environment, capture_output=True, text=True, check=True
    )
    assert json.loads(forgotten.stdout) == []
    resumed = _plan(flow, "--resume", "spikes")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert _runs_started(flow) == _stage_runs(flow), "the resume relaunched completed spikes"
    assert (
        f"- `{spike}`: report `{spike}-report`; branch `{kept['branch']['branch']}`"
        in _finalize_note(flow)
    )


def test_a_spikes_run_with_a_node_not_done_stops_before_finalize_and_resumes_without_relaunching(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "failing", fail=["spike-failing-quota"])

    stopped = _plan(flow)

    assert stopped.returncode != 0, stopped.stdout + stopped.stderr
    assert "spike-failing-quota failed" in stopped.stderr, stopped.stderr
    assert _runs_started(flow) == [flow.run, f"{flow.run}-spikes"]
    # The resume reads the spikes run again rather than launching it: until a retry has
    # settled the failed spike, it stops at the same place, naming the same node.
    again = _resumed(flow, stopped)
    assert again.returncode != 0, again.stdout + again.stderr
    assert "spike-failing-quota failed" in again.stderr, again.stderr
    assert _runs_started(flow) == [flow.run, f"{flow.run}-spikes"], "the resume relaunched"


def test_a_spikes_launch_that_left_no_run_stops_rather_than_passing_for_no_spikes(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "refused", refuse_spikes=True)

    stopped = _plan(flow)

    assert stopped.returncode != 0, stopped.stdout + stopped.stderr
    assert f"run {flow.run}-spikes has not settled" in stopped.stderr, stopped.stderr
    assert _runs_started(flow) == [flow.run, f"{flow.run}-spikes"]


def test_a_spike_that_settled_done_without_keeping_its_branch_stops_naming_it(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "landed", outcomes={"spike-landed-listing": "merged"})

    stopped = _plan(flow)

    assert stopped.returncode != 0, stopped.stdout + stopped.stderr
    assert "spike-landed-listing done with the outcome merged, not preserved" in stopped.stderr
    assert f"{flow.run}-finalize" not in _runs_started(flow)


def test_a_detached_flow_prints_the_command_that_carries_each_stage_on(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "detached")

    done = _plan(flow, "--detach")
    assert done.returncode == 0, done.stdout + done.stderr
    assert _runs_started(flow) == [flow.run]
    stages = [done]
    while "carry the flow on with: " in stages[-1].stderr and len(stages) < 6:
        stages.append(_resumed(flow, stages[-1]))
        assert stages[-1].returncode == 0, stages[-1].stdout + stages[-1].stderr

    assert _runs_started(flow) == _stage_runs(flow)
    assert "holds the plan at" in stages[-1].stdout + stages[-1].stderr
    # The last resume carried on after a finalize that had completed: it launched neither
    # the spikes nor the finalize again, and went on to the tail.
    last_starts = [start for start in _starts(flow)][-1]
    assert last_starts["run"] == f"{flow.run}-design"
    assert "--resume finalize" in stages[-2].stderr


@pytest.mark.parametrize("stage", ["spikes", "finalize"])
def test_a_taken_stage_run_id_is_refused_before_anything_launches(
    workspace: Workspace, tmp_path: Path, stage: str
) -> None:
    """Another flow already launched under the id this one's stage would run as."""
    flow = _flow(workspace, tmp_path, f"taken-{stage}")
    scenario = json.loads(flow.environment["FAKE_ENGINE_SCENARIO"])
    other = flow._replace(
        brief=tmp_path / "other.md",
        environment={
            **flow.environment,
            "FAKE_ENGINE_SCENARIO": json.dumps({**scenario, "run": f"{flow.run}-{stage}"}),
        },
    )
    other.brief.write_text(flow.brief.read_text(encoding="utf-8"), encoding="utf-8")
    taken = _just(
        other, "plan", str(other.brief), "--name", f"{flow.run}-{stage}", "--no-design-doc"
    )
    assert taken.returncode == 0, taken.stdout + taken.stderr
    launched = _starts(flow)

    refused = _plan(flow)

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert f"run '{flow.run}-{stage}' already exists" in refused.stderr
    assert _starts(flow) == launched, "the refused flow launched something"


def test_a_plan_predating_budgets_goes_through_spikes_and_finalize_to_an_approvable_document(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "migrated", budgets=False, plan=workspace.migrated)

    done = _plan(flow)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _runs_started(flow) == _stage_runs(flow)
    approved = _just(flow, "approve-design", flow.plan)
    assert approved.returncode == 0, approved.stdout + approved.stderr


def test_a_plan_missing_its_budget_answers_is_refused_at_its_check_before_any_spike(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "unbudgeted", budgets=False)

    refused = _plan(flow)

    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert f"{flow.plan} carries no `orchestrator.plan-budgets` metadata" in refused.stderr
    assert _runs_started(flow) == [flow.run]


@pytest.mark.parametrize(
    ("flags", "said"),
    [
        (("--resume", "drafts"), "--resume names the stage a planning flow carries on from"),
        (("--resume", "spikes finalize"), "--resume names the stage a planning flow carries on"),
        (("--resume", "spikes", "--no-design-doc"), "--no-design-doc stops the flow after"),
        (("--resume=drafts",), "--resume names the stage a planning flow carries on from"),
    ],
    ids=["no-such-stage", "two-stages", "with-no-design-doc", "joined-no-such-stage"],
)
def test_a_resume_naming_no_stage_or_no_tail_is_refused_before_anything_launches(
    workspace: Workspace, tmp_path: Path, flags: tuple[str, ...], said: str
) -> None:
    flow = _flow(workspace, tmp_path, "badresume")

    refused = _just(flow, "plan", str(flow.brief), "--name", flow.run, *flags)

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert said in refused.stderr, refused.stderr
    assert _starts(flow) == []


def test_a_resume_before_the_draft_has_settled_stops_saying_so(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "early")

    stopped = _plan(flow, "--resume", "spikes")

    assert stopped.returncode == 1, stopped.stdout + stopped.stderr
    assert f"run {flow.run}, the draft planner, has not settled" in stopped.stderr
    assert "--resume spikes" in stopped.stderr
    assert _starts(flow) == []


def test_a_spikes_project_the_check_refuses_stops_before_any_spike_launches(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "unkept", spike_publish=False)

    stopped = _plan(flow)

    assert stopped.returncode == 1, stopped.stdout + stopped.stderr
    assert 'declares `onepipeline.publish: "preserve"`' in stopped.stdout + stopped.stderr
    assert f"the check above refused or could not read {spike_plan.spikes_project(flow.plan)}" in (
        stopped.stderr
    )
    assert _runs_started(flow) == [flow.run]


def test_a_plan_in_a_source_the_store_cannot_read_stops_before_its_spikes(
    workspace: Workspace, tmp_path: Path
) -> None:
    """A brief naming a source nothing configures leaves no spikes project to read."""
    unconfigured = PlanProject(f"nowhere-configured:test-{os.getpid()}-plan")
    flow = _flow(workspace, tmp_path, "unconfigured", plan=unconfigured, write=False)

    stopped = _plan(flow)

    assert stopped.returncode == 2, stopped.stdout + stopped.stderr
    assert f"the spikes project {unconfigured}-spikes could not be read or stamped" in (
        stopped.stderr
    )
    assert _runs_started(flow) == [flow.run]


def test_spikes_whose_branches_onevcs_never_recorded_stop_before_finalize(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "unrecorded", unrecorded=True)

    stopped = _plan(flow)

    assert stopped.returncode == 1, stopped.stdout + stopped.stderr
    assert "records no kept branch" in stopped.stderr, stopped.stderr
    assert _runs_started(flow) == [flow.run, f"{flow.run}-spikes"]


def test_a_spike_branch_that_cannot_be_acknowledged_stops_before_finalize(
    workspace: Workspace, tmp_path: Path
) -> None:
    """With no manager session to key it on, an acknowledgement is refused, and said."""
    flow = _flow(workspace, tmp_path, "unacknowledged", session=False)

    stopped = _plan(flow)

    assert stopped.returncode == 2, stopped.stdout + stopped.stderr
    assert "could not be acknowledged (status 2)" in stopped.stderr, stopped.stderr
    assert "--resume spikes" in stopped.stderr
    assert f"{flow.run}-finalize" not in _runs_started(flow)


def test_a_finalize_that_did_not_settle_stops_and_its_resume_does_not_relaunch_it(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "unfinalized", fail_finalize=True)

    stopped = _plan(flow)

    assert stopped.returncode == 1, stopped.stdout + stopped.stderr
    assert f"run {flow.run}-finalize, the finalize planner, did not settle" in stopped.stderr
    assert "--resume finalize" in stopped.stderr
    again = _resumed(flow, stopped)
    assert again.returncode == 1, again.stdout + again.stderr
    assert f"run {flow.run}-finalize, the finalize planner, has not settled" in again.stderr
    # Carried on from the spikes instead, the flow reads the spikes and the finalize that
    # both exist rather than refusing their ids as taken, and launches neither again.
    earlier = _plan(flow, "--resume", "spikes")
    assert earlier.returncode == 1, earlier.stdout + earlier.stderr
    assert f"run {flow.run}-finalize, the finalize planner, has not settled" in earlier.stderr
    assert _runs_started(flow) == _stage_runs(flow)[:3], "a resume relaunched a stage"


def test_a_plan_its_review_refuses_stops_before_any_spike_launches(
    workspace: Workspace, tmp_path: Path
) -> None:
    """A criterion the reviewer refuses is the planner's to correct before spikes measure."""
    flow = _flow(workspace, tmp_path, "unreviewed")
    refusal = {
        "passes": False,
        "findings": [
            {
                "criterion": CRITERIA[0],
                "why": "the latency it names is not one the task states as a number",
            }
        ],
    }
    flow.environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(refusal)])

    stopped = _plan(flow)

    assert stopped.returncode == 1, stopped.stdout + stopped.stderr
    assert f"the review above refused or could not read {flow.plan}" in stopped.stderr
    assert "--resume spikes" in stopped.stderr
    assert _runs_started(flow) == [flow.run]


def test_a_retried_spike_is_finalized_from_its_report_and_the_branch_its_retry_kept(
    workspace: Workspace, tmp_path: Path
) -> None:
    """The engine retries a spike as a node of its own, which keeps a branch named for it."""
    flow = _flow(workspace, tmp_path, "retried", retried=["spike-retried-quota"])

    done = _plan(flow)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _runs_started(flow) == _stage_runs(flow)
    branch = f"{flow.plan.partition(':')[2]}/spike-retried-quota-2"
    assert branch in _acknowledged(flow), _acknowledged(flow)
    (task,) = _items(
        _store(
            flow,
            "task",
            "list",
            "--source",
            FIXTURE_SOURCE,
            "--project",
            flow.plan.partition(":")[2],
        )
    )
    content = str(task["item"]["content"])
    assert "- `spike-retried-quota`: report `spike-retried-quota-report`" in content, content
    assert f"branch `{branch}`" in content, content


def test_a_detached_spikes_launch_the_engine_refused_stops_with_its_status(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _flow(workspace, tmp_path, "refused-detached", refuse_spikes=True)

    detached = _plan(flow, "--detach")
    assert detached.returncode == 0, detached.stdout + detached.stderr
    stopped = _resumed(flow, detached)

    assert stopped.returncode == 2, stopped.stdout + stopped.stderr
    assert f"run {flow.run}-spikes, the spikes, could not be launched (status 2)" in stopped.stderr
    assert "--resume spikes" in stopped.stderr.rsplit("could not be launched", 1)[1]
    assert _runs_started(flow) == [flow.run, f"{flow.run}-spikes"]


def test_a_resume_refuses_a_run_of_its_stages_name_launched_from_another_project(
    workspace: Workspace, tmp_path: Path
) -> None:
    """Another flow's run under this flow's `-spikes` name is not read as this flow's spikes."""
    flow = _flow(workspace, tmp_path, "squatted")
    detached = _plan(flow, "--detach")
    assert detached.returncode == 0, detached.stdout + detached.stderr
    scenario = json.loads(flow.environment["FAKE_ENGINE_SCENARIO"])
    other = flow._replace(
        brief=tmp_path / "other.md",
        environment={
            **flow.environment,
            "FAKE_ENGINE_SCENARIO": json.dumps({**scenario, "run": f"{flow.run}-spikes"}),
        },
    )
    other.brief.write_text(flow.brief.read_text(encoding="utf-8"), encoding="utf-8")
    squatted = _just(
        other, "plan", str(other.brief), "--name", f"{flow.run}-spikes", "--no-design-doc"
    )
    assert squatted.returncode == 0, squatted.stdout + squatted.stderr

    stopped = _plan(flow, "--resume", "spikes")

    assert stopped.returncode == 1, stopped.stdout + stopped.stderr
    assert (
        f"run {flow.run}-spikes was launched from 'authoring:{flow.run}-spikes', not from "
        f"{spike_plan.spikes_project(flow.plan)}"
    ) in stopped.stderr
    assert _runs_started(flow) == [flow.run, f"{flow.run}-spikes"], "a stage was launched"


def test_a_resume_from_finalize_still_runs_spikes_that_never_ran(
    workspace: Workspace, tmp_path: Path
) -> None:
    """Naming a later stage is never a way past spikes the draft wrote and nothing launched."""
    flow = _flow(workspace, tmp_path, "skipahead")
    detached = _plan(flow, "--detach")
    assert detached.returncode == 0, detached.stdout + detached.stderr

    resumed = _plan(flow, "--resume", "finalize")

    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert _runs_started(flow) == _stage_runs(flow)
    assert set(_branches(flow)) <= set(_acknowledged(flow)), _acknowledged(flow)


def _stacked(workspace: Workspace, tmp_path: Path, key: str, *, fail: bool = False) -> Flow:
    """A flow whose draft wrote a harness spike and two area spikes depending on it."""
    harness, listing, quota = (f"spike-{key}-{topic}" for topic in ("harness", "listing", "quota"))
    return _flow(
        workspace,
        tmp_path,
        key,
        topics=("harness", "listing", "quota"),
        spike_deps={listing: [harness], quota: [harness]},
        unmeasured=[harness],
        builds_on=listing,
        fail=[harness] if fail else [],
    )


def _ledger(flow: Flow) -> dict[str, Node]:
    """The spikes run's ledger nodes by id, as the engine's `result.json` records them."""
    record = json.loads((flow.runs / f"{flow.run}-spikes" / "result.json").read_text("utf-8"))
    nodes = cast(list[Node], record["nodes"])
    return {node["id"]: node for node in nodes}


def _descends(flow: Flow, ancestor: str, head: str) -> bool:
    """Whether ``head`` descends from ``ancestor`` on the identity's own origin."""
    origin = flow.runs.parent / "identity" / "origin.git"
    return (
        subprocess.run(
            ["git", "-C", str(origin), "merge-base", "--is-ancestor", ancestor, head],
            check=False,
        ).returncode
        == 0
    )


def _finalize_note(flow: Flow) -> str:
    """The finalize planner's task, which carries the note `spike_flow.py` wrote for it."""
    (task,) = _items(
        _store(flow, "task", "list", "--source", "authoring", "--project", f"{flow.run}-finalize")
    )
    return str(task["item"]["content"])


def _plan_task(flow: Flow) -> str:
    (task,) = _items(
        _store(
            flow,
            "task",
            "list",
            "--source",
            FIXTURE_SOURCE,
            "--project",
            flow.plan.partition(":")[2],
        )
    )
    return str(task["item"]["content"])


def test_area_spikes_start_from_the_harness_spikes_kept_branch_and_finalize_links_both(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _stacked(workspace, tmp_path, "stacked")
    harness, listing, quota = flow.spikes

    done = _plan(flow)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _runs_started(flow) == _stage_runs(flow)
    ledger = _ledger(flow)
    for area in (listing, quota):
        assert ledger[area]["head"] != ledger[harness]["head"]
        assert _descends(flow, ledger[harness]["head"], ledger[area]["head"]), (
            f"{area}'s kept branch does not descend from {harness}'s kept head"
        )
    assert not _descends(flow, ledger[listing]["head"], ledger[quota]["head"])
    acknowledged = _acknowledged(flow)
    for branch in _branches(flow):
        assert branch in acknowledged, f"{branch} was not acknowledged: {acknowledged}"
    note = _finalize_note(flow)
    for area in (listing, quota):
        assert f"- `{area}`: above it, `{harness}`" in note, note
    assert f"- `{harness}`: above it" not in note, note
    # The task builds on the listing spike: it links that spike and the harness above it,
    # and nothing the quota spike measured.
    content = _plan_task(flow)
    for spike in (listing, harness):
        assert f"- `{spike}`: report `{spike}-report`" in content, content
    assert f"`{quota}`" not in content, content
    checked = _just(flow, "check-plan", flow.plan)
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_a_failed_harness_spike_stops_naming_it_and_each_skipped_dependent_then_its_retry_finalizes(
    workspace: Workspace, tmp_path: Path
) -> None:
    flow = _stacked(workspace, tmp_path, "unharnessed", fail=True)
    harness, listing, quota = flow.spikes

    stopped = _plan(flow)

    assert stopped.returncode == 1, stopped.stdout + stopped.stderr
    assert f"{harness} failed" in stopped.stderr, stopped.stderr
    assert f"skipped {listing}, {quota} behind it as dependents" in stopped.stderr
    assert "retrying the failed spike re-runs its dependents on its replacement's branch" in (
        stopped.stderr
    )
    assert "--resume spikes" in stopped.stderr
    assert _runs_started(flow) == [flow.run, f"{flow.run}-spikes"]

    # The manager's `retry` of the harness, settled by the driver: its replacement keeps a
    # branch of its own, and both area spikes run from it.
    native = flow.plan.partition(":")[2]
    retried = subprocess.run(
        [sys.executable, str(STAND_IN), "retry", spike_plan.spikes_project(flow.plan)]
        + [f"{native}/{{{{ node.id }}}}"],
        cwd=flow.checkout,
        env=flow.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert retried.returncode == 0, retried.stdout + retried.stderr
    resumed = _resumed(flow, stopped)

    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert _runs_started(flow) == _stage_runs(flow), "the resume relaunched the spikes"
    ledger = _ledger(flow)
    replacement = f"{harness}-2"
    for area in (listing, quota):
        assert _descends(flow, ledger[replacement]["head"], ledger[area]["head"])
    assert f"{native}/{replacement}" in _acknowledged(flow), _acknowledged(flow)
    note = _finalize_note(flow)
    assert f"- `{harness}`: report `{harness}-report`; branch `{native}/{replacement}`" in note
    assert f"- `{listing}`: above it, `{harness}`" in note, note
    content = _plan_task(flow)
    assert f"- `{harness}`: report `{harness}-report`" in content, content
    assert f"branch `{native}/{replacement}`" in content, content
