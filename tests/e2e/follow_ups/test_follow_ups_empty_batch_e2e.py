"""`just follow-ups` over a batch onefollowup reports empty, and over one it does not.

The recipe asks the installed `onefollowup snapshot` whether a finished run's batch holds
anything to verify, and launches the follow-up run only when its answer's `empty` is false.
Each case drives the real recipe over a run root recorded in the shapes the engine writes
(`test_follow_up_budget_inputs_e2e.recorded_run`), a follow-up store of its own, and the
configured `batch_preparing` hook, `scripts/follow-up-budget-inputs.sh`, answering from a
onevcs state root of the test's own. Nothing is doubled but the paid model, which the
launched case's verifying agent never reaches before it is stopped.

A run that drafted nothing and landed nothing gives an empty batch: the recipe prints its
one line, creates no follow-up plan and starts no run. A run that drafted nothing but landed
a change over its delivery budget gives a batch holding one `budget-overrun` input, and the
recipe launches its follow-up run.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import follow_up_store
import follow_up_variables
from project_fixtures import helper
from test_follow_up_budget_inputs_e2e import OVER, Landed, budgeted_host, recorded_run
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

PAID_PROVIDER_GUARD = helper("no-paid-provider")
FAKE_BACKEND = helper("fake_backend.py")

#: What an enclosing dispatch exports that would decide these invocations for them.
INHERITED = (
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_RUN_ROOT",
    "ONEPIPELINE_NODE_ID",
    "ONEPIPELINE_FLOW",
    "ONEPIPELINE_HOOK",
    "ONETASKGRAPH_DEFAULT_SOURCES",
    *follow_up_variables.all_names(),
    follow_up_store.RUNS_ROOT,
)


def _host(tmp_path: Path) -> tuple[dict[str, str], follow_up_store.Store]:
    inherited = {
        name: value
        for name, value in budgeted_host(tmp_path).items()
        if name not in INHERITED and not name.startswith("ONEFOLLOWUP_")
    }
    store = follow_up_store.isolated(tmp_path)
    environment = store.over(inherited)
    environment["CLAUDE_CODE_SESSION_ID"] = "e2e-follow-ups-empty-batch"
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    return environment, store


def _just(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own recipes
        ["just", *arguments],  # noqa: S607 - `just` from the search path, as a manager runs it
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )


# llmlint: ignore-block[tests_mirror_real_usage] The task names `recorded_run` as these cases'
# fixture: a budget-overrun input needs a change landed on a registered repository after a
# chosen cycle, which no launch here can produce, and the run root is read only through the
# engine's own verb. The same empty and drafting endings driven through a real `just
# orchestrate` launch are `tests/run_end_hooks/test_run_end_hooks_e2e.py`'s.
def test_a_batch_onefollowup_reports_empty_launches_no_follow_up_run(tmp_path: Path) -> None:
    environment, store = _host(tmp_path)
    runs = tmp_path / "runs"
    run = f"drafted-nothing-{os.getpid()}"
    recorded_run(runs, run, {})

    answered = _just(environment, "follow-ups", run)

    assert answered.returncode == 0, answered.stdout + answered.stderr
    assert answered.stdout == (
        f"batch {run} of run {run} holds no follow-up drafts; no follow-up run was launched\n"
    ), answered.stdout + answered.stderr
    assert sorted(path.name for path in runs.iterdir()) == [run]
    assert list(store.plans.iterdir()) == []


def test_a_batch_holding_a_budget_overrun_launches_its_follow_up_run(tmp_path: Path) -> None:
    environment, store = _host(tmp_path)
    runs = tmp_path / "runs"
    run = f"overran-{os.getpid()}"
    recorded_run(runs, run, {"slow-change": Landed(OVER, 20)})
    follow_up = f"{run}-follow-ups"

    try:
        answered = _just(environment, "follow-ups", run)

        assert answered.returncode == 0, answered.stdout + answered.stderr
        assert answered.stdout == (
            f"follow-up run: {follow_up}; watch it with: just watch {follow_up}\n"
        ), answered.stdout + answered.stderr
        assert (runs / follow_up / "launch.json").is_file(), answered.stderr
        assert list(store.plans.iterdir()) != []
    finally:
        if (runs / follow_up).exists():
            _just(environment, "stop", follow_up)


def test_a_snapshot_that_fails_creates_and_launches_nothing(tmp_path: Path) -> None:
    """A landed change onevcs cannot resolve fails the hook, so `snapshot` fails with it."""
    environment, store = _host(tmp_path)
    runs = tmp_path / "runs"
    run = f"unresolvable-{os.getpid()}"
    recorded_run(
        runs, run, {"stray-change": Landed("github.com/nickderobertis/budget-unregistered", 20)}
    )

    answered = _just(environment, "follow-ups", run)

    assert answered.returncode != 0, answered.stdout + answered.stderr
    assert answered.stdout == ""
    assert "could not resolve the publication checkout" in answered.stderr, answered.stderr
    assert sorted(path.name for path in runs.iterdir()) == [run]
    assert list(store.plans.iterdir()) == []


# llmlint: ignore-end[tests_mirror_real_usage]
