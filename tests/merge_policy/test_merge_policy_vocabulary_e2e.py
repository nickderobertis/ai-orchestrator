"""The `merge_policy` vocabulary this repository's prose teaches is the launcher's own.

Three documents restate the policy names a plan node may carry — the routing policy,
the plan schema, and the planner doctrine each need them in front of the reader — and
none of them is the source. The source is the published engine, which enumerates what
it accepts in the refusal it writes for what it does not, so each journey here hands the
real `just orchestrate` a lifecycle plan carrying one policy and reads what it said:
one deliberately impossible policy is the whole published list, and each retired
spelling is refused by name.

Every journey spends a real launch, which is why this is a project of its own, keyed on
what the launcher reads and on the three documents these tests hold to it, rather than a
marker-routed tier of the broad `orchestrator` project. A launch is pointed at an agent
graph that is not there: `onepipeline start` validates the plan before it reads anything
else, so a policy it accepted is one whose only complaint is the graph, and nothing runs
either way.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import short_state
from harness_indirections import established_indirections
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: Every launch here reaches `onepipeline` through `uv run`, which waits on this
#: checkout's project-environment lock, so these belong in the one group AGENTS.md's
#: four-worker invariant names or a launch waits out the journey that holds it.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The paid provider's stand-ins and the guard covering the identities `ONEHARNESS_BIN_*`
#: cannot reach. Reached through `helper` rather than composed from this module's own
#: directory, for the reason that function states: a stand-in named at a path this
#: checkout does not have is not a stand-in, and oneharness falls through to a real
#: identity rather than failing. No launch here is meant to reach a turn at all — every
#: one is refused at plan validation — and these are what make a plan the launcher
#: unexpectedly accepted cost a failed assertion rather than a paid turn.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The session these launches run under, stated as the ambient harness variable a real
#: manager session carries rather than as the value `scripts/onepipeline.sh` derives
#: from it.
LAUNCHING_SESSION = "e2e-merge-policy"

#: Every launcher variable an outer dispatch may have exported into this suite. A journey
#: that inherited one would be launching as whoever dispatched it.
LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The plan schema the published crate reads, as the shipped example projects write it.
PLAN_SCHEMA_VERSION = 3

#: Every file that restates the `merge_policy` vocabulary in prose. Three, because
#: the routing policy, the plan schema, and the planner doctrine each need it in
#: front of the reader; none of them is its source.
MERGE_POLICY_PROSE = ("AGENTS.md", "docs/orchestration.md", "docs/repo-lifecycle.md")

#: The files that also promise the retired spellings are refused by name. `AGENTS.md`
#: is not among them: that promise is history, and the root document carries the
#: published vocabulary alone.
RETIRED_SPELLINGS_PROSE = ("docs/orchestration.md", "docs/repo-lifecycle.md")

#: A policy name as those files write it. Every published name is `local-` or
#: `change-` prefixed, which is what lets a whole document be swept for the
#: vocabulary rather than one sentence parsed out of it — so a name dropped from a
#: list and a name invented in a paragraph both fail.
POLICY_IN_PROSE = re.compile(r"`((?:local|change)-[a-z]+)`")

#: Words of that shape which belong to a **different** engine vocabulary, and so are
#: not the merge policy this sweep is looking for: `change-draft` is a settlement
#: *outcome* — what a publication held back by an unarrived release answers — written in
#: prose exactly as a policy is. Exempted by name rather than by heuristic, and the name
#: is not unchecked: `tests/test_engine_contracts.py` reconciles the outcome vocabulary
#: against the engine's own settlement sites, so a word that stopped being an outcome
#: fails there, while a word of this shape that is neither a policy nor an outcome still
#: fails here.
ANOTHER_VOCABULARY = frozenset({"change-draft"})

#: The retired spellings, anchored on the "old"/"older" each file introduces them
#: with so the pattern cannot wander onto another slash-separated triple. Prose is
#: hard-wrapped, so every gap here is any whitespace rather than a space.
RETIRED_IN_PROSE = re.compile(r"\bold(?:er)?\s+`([a-z-]+)`\s+/\s+`([a-z-]+)`\s+/\s+`([a-z-]+)`")

#: A `merge_policy` no vocabulary will contain, used to make the launcher enumerate
#: the one it does accept.
UNKNOWN_POLICY = "no-such-merge-policy"

#: How `onepipeline` refuses a policy: by naming the value, then listing what it
#: would have taken. That list is the vocabulary's one source.
REFUSED_POLICY = re.compile(r"unknown variant `([^`]+)`, expected one of ((?:`[^`]+`(?:, )?)+)")


def _environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment every launch here runs its refused plan under."""
    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(__name__))
    # Keeps a launch's history and sibling state out of the host's.
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
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
def merge_policy_launch(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Callable[[str], str]:
    """Hand the real launcher a lifecycle plan carrying one policy, and report what it said."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("merge-policy")
    environment = _environment(tmp_path, oneharness_bin)
    absent_graph = str(tmp_path / "absent" / "dag-scope.yaml")
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "name": "merge-policy-probe",
        "tasks": [
            {
                "id": "service",
                "title": "feat: add service",
                "task": "## What\nAdd the service.\n\n## Why\nIt is required.\n\n"
                "## Acceptance criteria\n- The service works.\n",
                "repo": "ai-orchestrator-isolated",
            }
        ],
    }

    def launch(policy: str) -> str:
        plan["tasks"][0]["merge_policy"] = policy
        written = tmp_path / "candidate.plan.json"
        written.write_text(json.dumps(plan), encoding="utf-8")
        refused = _just(
            "orchestrate",
            project_from_plan(written),
            "--detach",
            "--dag-graph",
            absent_graph,
            environment=environment,
            seconds=120,
        )
        assert refused.returncode != 0, f"merge_policy {policy!r} reached a real launch"
        return refused.stderr + refused.stdout

    return launch


@pytest.fixture(scope="module")
def published_merge_policies(merge_policy_launch: Callable[[str], str]) -> frozenset[str]:
    """The `merge_policy` vocabulary, from the only thing that decides it.

    `onepipeline` enumerates what it accepts in the refusal it writes for what it
    does not, so one deliberately impossible policy is the whole published list.
    """
    reported = merge_policy_launch(UNKNOWN_POLICY)
    refusal = REFUSED_POLICY.search(reported)
    assert refusal is not None, f"the launcher enumerated no vocabulary:\n{reported}"
    assert refusal.group(1) == UNKNOWN_POLICY, reported
    return frozenset(re.findall(r"`([^`]+)`", refusal.group(2)))


@pytest.mark.parametrize("relative_path", MERGE_POLICY_PROSE)
def test_the_merge_policy_vocabulary_in_prose_is_the_published_one(
    relative_path: str, published_merge_policies: frozenset[str]
) -> None:
    """No document here names a `merge_policy` the launcher would refuse, or omits one it takes.

    The vocabulary is the published crate's, and prose that restates it is a copy —
    which is the shape that goes stale in silence. A planner following a stale copy
    writes a plan that dies at launch, which is what the adoption of these names
    already did once.
    """
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    named = set(POLICY_IN_PROSE.findall(text)) - ANOTHER_VOCABULARY
    assert named == set(published_merge_policies), (
        f"{relative_path} names merge policies {sorted(named)}; the launcher accepts "
        f"{sorted(published_merge_policies)}"
    )


def test_the_retired_merge_policy_spellings_are_refused_by_name(
    merge_policy_launch: Callable[[str], str], published_merge_policies: frozenset[str]
) -> None:
    """Every file promising the old spellings are refused by name names spellings that are.

    The other half of the same contract: a reader is told what their pre-adoption
    plan will do, and the promise is only worth the launch that keeps it. One set of
    launches for both files, because a file that spelled the triple differently
    is itself the drift this rejects.
    """
    quoted = {
        relative_path: RETIRED_IN_PROSE.search(
            (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        )
        for relative_path in RETIRED_SPELLINGS_PROSE
    }
    missing = sorted(path for path, found in quoted.items() if found is None)
    assert not missing, f"{missing} no longer name the retired merge-policy spellings"
    spellings = {path: found.groups() for path, found in quoted.items() if found is not None}
    assert len(set(spellings.values())) == 1, (
        f"the files disagree on the retired spellings: {spellings}"
    )

    for spelling in next(iter(spellings.values())):
        assert spelling not in published_merge_policies, f"`{spelling}` is a published policy"
        assert f"unknown variant `{spelling}`" in merge_policy_launch(spelling), (
            f"the launcher does not refuse `{spelling}` by name"
        )
