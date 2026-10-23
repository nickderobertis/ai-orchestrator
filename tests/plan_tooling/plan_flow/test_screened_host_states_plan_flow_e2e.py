"""`just plan` drafts, reviews, documents and copies a plan in each host state.

The tracked `onetaskgraph.yaml` switches the store's public boundary on and declares
`authoring` private, so a planning flow runs screened on every host. A host meets that in one
of three states (`onetaskgraph_user_layer.HostState`): no user layer; a user layer of
`config/onetaskgraph.user-layer.example.yaml`'s shape, over synthetic names, declaring its
organization's sources and its own stores private; and that layer in a process holding no
credential for the organization's sources. One whole flow runs in each, over a plan naming no
private repository — the planner's draft, the review, the design document's launch and the
copy of the plan with its document onto a destination of its own — and each must leave the
plan and its one document there — but in the uncredentialed state, where the pinned store's
copy authenticates every private source and so is refused naming the organization's source
and its missing credential, with nothing written to the destination.

The flow is `test_plan_flow_e2e.py`'s, its environment and its stand-ins imported rather than
restated: only the paid model's answers are scripted, and the recipes, the engine, the store,
its layer and `onevcs` are real.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import plan_fixture_source
import pytest
from fake_backend import PROMPT_LOG_ENV
from onetaskgraph_user_layer import (
    LINEAR_KEY_ENV,
    ORGANIZATION_FOLLOWUPS,
    ORGANIZATION_PLANS,
    HostState,
    host_state,
)
from test_plan_flow_e2e import (
    DESIGN_RUN_SUFFIX,
    DESTINATION,
    FIXTURE_SOURCE,
    Stored,
    _environment,
    _just,
)

from orchestrator.root import REPO_ROOT

#: What the pinned store's copy says in the uncredentialed state: it authenticates every
#: private source, and the organization's cannot.
COPY_REFUSAL = f"authentication for this source failed: set environment variable {LINEAR_KEY_ENV}"

# What the recipes these journeys run leave in the temporary directory is this module's own
# and goes with it (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling-plan-flow` is
# the leaf project keyed on `planToolingPlanFlowWorkspace`, the edge this rule asks for, and the
# whole-flow journey this one reuses already runs behind it. That key names the recipes,
# scripts, templates and package `just plan` drives, so narrowing it would memoize a verdict
# over a tree never run.
@pytest.mark.parametrize("state", list(HostState), ids=[state.value for state in HostState])
def test_a_plan_is_drafted_reviewed_documented_and_copied(
    state: HostState, tmp_path: Path, oneharness_bin: str
) -> None:
    """`just plan` exits clean, and the destination holds the plan and its one document."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    unique = f"test-{os.getpid()}-screened-{state.value}"
    run = f"screened-flow-{state.value}-{os.getpid()}"
    stored = Stored(
        project=unique,
        qualified=f"{FIXTURE_SOURCE}:{unique}",
        task_title="feat: page the node listing",
        second_task_title="feat: follow the cursor from the view",
        repository="github.com/nickderobertis/onepipeline",
        second_repository="github.com/nickderobertis/onepipeline-ui",
        document=f"{unique}-document",
        document_qualified=f"{FIXTURE_SOURCE}:{unique}-document",
        document_path=plan_fixture_source.root() / "documents" / f"{unique}-document.md",
    )
    destination = tmp_path / "board"
    destination.mkdir()
    environment = _environment(tmp_path, stored, destination=DESTINATION, declared_at=destination)
    for name, value in host_state(state, tmp_path / "host").items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(
        "## What\nDecide the cursor's shape.\n\n"
        f"Plan project: {stored.qualified}\n\n"
        "## Why\nThe view cannot deep-link until it is settled.\n\n"
        "## Acceptance criteria\n- The cursor's shape and its type are stated.\n",
        encoding="utf-8",
    )
    try:
        launch = _just(
            "plan", str(brief), "--name", run, "--to", DESTINATION, environment=environment
        )
    finally:
        for ended in (run, f"{run}{DESIGN_RUN_SUFFIX}"):
            _just("stop", ended, environment=environment, seconds=60)
            (REPO_ROOT / ".plans" / "projects" / f"{ended}.md").unlink(missing_ok=True)
            tasks = REPO_ROOT / ".plans" / "tasks" / ended
            if tasks.is_dir():
                shutil.rmtree(tasks)

    said = launch.stdout + launch.stderr
    landed = sorted(str(one.relative_to(destination)) for one in destination.rglob("*.md"))
    if state is HostState.ORGANIZATION_UNCREDENTIALED and launch.returncode != 0:
        # The pinned store's copy gap: drafted, reviewed and documented, the plan's copy is
        # refused naming the organization's source and its missing credential alone.
        assert COPY_REFUSAL in said and "refused the copy of" in said, said
        # Dependency reads may report both unavailable sources before the copy starts.
        # Only the copy's refusal identifies the source that actually blocked this write.
        refusals = [line for line in said.splitlines() if "could not do it:" in line]
        assert len(refusals) == 1, said
        assert refusals[0] in [
            f"copy-plan: onetaskgraph: source {source} could not do it: {COPY_REFUSAL}"
            for source in (ORGANIZATION_PLANS, ORGANIZATION_FOLLOWUPS)
        ], said
        assert stored.document_path.is_file(), f"the flow never wrote its document:\n{said}"
        assert landed == [], f"a refused copy left {landed} on the destination"
        return
    assert launch.returncode == 0, f"the flow failed:\n{said}"
    assert landed == [
        f"documents/{stored.document}.md",
        f"projects/{unique}.md",
        f"tasks/{unique}/decide-the-cursor.md",
        f"tasks/{unique}/read-the-cursor.md",
    ], f"the flow left {landed} on the destination"


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
