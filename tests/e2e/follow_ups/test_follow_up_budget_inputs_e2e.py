"""The `batch_preparing` hook over a recorded run, through the real engine, onevcs and
onebudgetspec.

`scripts/follow-up-budget-inputs.sh` is what `onefollowup prepare` runs to learn which of a
finished run's landed changes went over their delivery budgets. Every verb it composes is the
real one: `onepipeline telemetry --changes` reads a run root recorded here in the shapes the
engine writes, `onevcs resolve` answers from a state root registering three scratch
repositories, and `onebudgetspec` measures each change's `cycle-time` delivery budget with
this host's own `scripts/budget-cycle-time.sh`. Nothing is doubled.

Three changes landed: one in a repository whose budget it exceeds, one within its
repository's budget, and one in a repository holding no budgets file. The hook's answer is a
onefollowup inputs document holding exactly one `budget-overrun` input, naming the first
change's node, landing and budget, and one `budget-unanswered` input for the third.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boundary_home
import yaml
from probe_run_root import run_root

from orchestrator.root import REPO_ROOT

HOOK = REPO_ROOT / "scripts" / "follow-up-budget-inputs.sh"
ENGINE = REPO_ROOT / "node_modules" / ".bin" / "onepipeline"
LANDING = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

#: The three repositories, by what their budgets file holds against a 20-second cycle.
OVER = "github.com/nickderobertis/budget-over"
WITHIN = "github.com/nickderobertis/budget-within"
UNBUDGETED = "github.com/nickderobertis/budget-none"
#: A repository whose budget's command fails, so its measurement errors.
BROKEN = "github.com/nickderobertis/budget-broken"

#: Each landed change's node, the repository it landed in, and the cycle it took.
CHANGES = {
    "slow-change": (OVER, 20),
    "quick-change": (WITHIN, 20),
    "unbudgeted-change": (UNBUDGETED, 20),
}


def _budget(threshold: int) -> str:
    """A budgets file holding this host's `cycle-time` delivery budget at ``threshold``."""
    return yaml.safe_dump(
        {
            "schema_version": 1,
            "budgets": [
                {
                    "id": "cycle-time",
                    "labels": ["onepipeline"],
                    "measure": "reported",
                    "command": [str(REPO_ROOT / "scripts" / "budget-cycle-time.sh")],
                    "unit": "seconds",
                    "direction": "max",
                    "threshold": threshold,
                }
            ],
        }
    )


# llmlint: ignore-block[tests_mirror_real_usage] This node's acceptance criteria name this
# test's shape: the real `onepipeline telemetry --changes` over a fixture run directory, so the
# hook's per-change wiring is held over changes no launch could land on four repositories with
# chosen cycles. The run root is written in the shapes the engine writes and read only through
# the engine's own verb; the same hook over a real run's landings is
# `tests/e2e/follow_ups/test_follow_ups_e2e.py`, which launches one.
def recorded_run(runs: Path, run: str, changes: dict[str, tuple[str, int]]) -> None:
    """A run root the engine reads, each node landing one change after its cycle."""
    root = run_root(runs, run)
    plan = {
        "schema_version": 3,
        "name": run,
        "goal": {"text": "Land three changes"},
        "tasks": [
            {"id": node, "repo": repository, "title": "feat: land", "persona": "engineer"}
            | {"task": "## What\nLand."}
            for node, (repository, _) in changes.items()
        ],
    }
    (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    journal: list[dict[str, object]] = []

    def event(node: str | None, seconds: int, source: str, kind: str, payload: object) -> None:
        stamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
        journal.append(
            {
                "v": 2,
                "ts": stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "stream": "recorded-delivery",
                "seq": len(journal),
                "source": source,
                "kind": kind,
                "labels": {"run_id": run, **({"node": node} if node else {})},
                "payload": payload,
            }
        )

    event(None, 0, "pipeline", "run-started", {"plan": plan})
    for node, (repository, cycle) in changes.items():
        event(node, 0, "pipeline", "node-dispatched", {"node": node})
        event(
            node,
            cycle,
            "vcs",
            "merge-completed",
            {
                "identity": repository,
                "landing": LANDING,
                "landed_at": (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=cycle))
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
            },
        )
        event(node, cycle, "pipeline", "node-settled", {"status": "done", "outcome": "merged"})
    (root / "events.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in journal), encoding="utf-8"
    )


# llmlint: ignore-end[tests_mirror_real_usage]


def budgeted_host(tmp_path: Path) -> dict[str, str]:
    """An environment whose onevcs registers the four repositories, three with a budget."""
    home = boundary_home.registered_home(tmp_path, public=(OVER, WITHIN, UNBUDGETED, BROKEN))
    checkouts = tmp_path / "onevcs-checkouts"
    (checkouts / "github-com__nickderobertis__budget-over" / "budgets.yaml").write_text(
        _budget(10), encoding="utf-8"
    )
    (checkouts / "github-com__nickderobertis__budget-within" / "budgets.yaml").write_text(
        _budget(100), encoding="utf-8"
    )
    broken = yaml.safe_load(_budget(10))
    broken["budgets"][0]["command"] = ["sh", "-c", "echo the measurement failed >&2; exit 7"]
    (checkouts / "github-com__nickderobertis__budget-broken" / "budgets.yaml").write_text(
        yaml.safe_dump(broken), encoding="utf-8"
    )
    environment = boundary_home.environment(None, home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    return environment


def _hook(run: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own hook
        [str(HOOK)],
        input=json.dumps({"schema_version": 1, "batch": run}),
        env=environment,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )


def test_the_hook_names_the_one_change_over_its_budget_and_the_one_it_could_not_check(
    tmp_path: Path,
) -> None:
    environment = budgeted_host(tmp_path)
    run = f"budgeted-{os.getpid()}"
    recorded_run(tmp_path / "runs", run, CHANGES)

    answered = _hook(run, environment)

    assert answered.returncode == 0, answered.stderr
    document = json.loads(answered.stdout)
    assert document["schema_version"] == 1
    inputs = document["inputs"]
    assert all(set(entry) == {"id", "source", "summary", "detail"} for entry in inputs), inputs
    assert all(isinstance(value, str) for entry in inputs for value in entry.values()), inputs
    assert len({entry["id"] for entry in inputs}) == len(inputs), inputs

    overruns = [entry for entry in inputs if entry["source"] == "budget-overrun"]
    assert len(overruns) == 1, inputs
    overrun = overruns[0]
    detail = json.loads(overrun["detail"])
    assert (detail["node"], detail["landing"], detail["budget"]) == (
        "slow-change",
        LANDING,
        "cycle-time",
    ), detail
    assert (detail["actual"], detail["threshold"], detail["repository"]) == (20, 10, OVER)
    assert "slow-change" in overrun["summary"] and "cycle-time" in overrun["summary"]

    unanswered = [entry for entry in inputs if entry["source"] == "budget-unanswered"]
    assert [json.loads(entry["detail"])["node"] for entry in unanswered] == ["unbudgeted-change"]
    assert json.loads(unanswered[0]["detail"])["repository"] == UNBUDGETED
    assert len(inputs) == 2, inputs
    assert not any("quick-change" in entry["detail"] for entry in inputs), inputs


def test_a_measurement_that_errors_is_relayed_as_unanswered_rather_than_dropped(
    tmp_path: Path,
) -> None:
    """onebudgetspec's over-only report leaves an errored measurement out, saying so by its exit.

    So the hook relays that exit as one `budget-unanswered` input naming every change it may
    concern, beside the overrun the same run's other change still reports.
    """
    environment = budgeted_host(tmp_path)
    run = f"errored-{os.getpid()}"
    recorded_run(tmp_path / "runs", run, {"broken-change": (BROKEN, 20), "slow-change": (OVER, 20)})

    answered = _hook(run, environment)

    assert answered.returncode == 0, answered.stderr
    inputs = json.loads(answered.stdout)["inputs"]
    assert sorted(entry["source"] for entry in inputs) == [
        "budget-overrun",
        "budget-unanswered",
    ], inputs
    (errored,) = [entry for entry in inputs if entry["source"] == "budget-unanswered"]
    detail = json.loads(errored["detail"])
    assert sorted(detail["nodes"]) == ["broken-change", "slow-change"], detail
    assert "exited 3" in detail["reason"], detail


def test_an_envelope_that_is_not_onefollowups_is_refused_with_its_repair(tmp_path: Path) -> None:
    environment = budgeted_host(tmp_path)

    refused = subprocess.run(  # noqa: S603 - this checkout's own hook
        [str(HOOK)],
        input=json.dumps({"schema_version": 2, "batch": "some-run"}),
        env=environment,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )

    assert refused.returncode == 2, refused.stderr
    assert refused.stdout == ""
    assert "hooks.batch_preparing" in refused.stderr, refused.stderr


def test_a_run_that_landed_nothing_gives_no_inputs(tmp_path: Path) -> None:
    environment = budgeted_host(tmp_path)
    run = f"unlanded-{os.getpid()}"
    recorded_run(tmp_path / "runs", run, {})

    answered = _hook(run, environment)

    assert answered.returncode == 0, answered.stderr
    assert json.loads(answered.stdout) == {"schema_version": 1, "inputs": []}


def test_a_run_the_engine_does_not_know_fails_the_hook_rather_than_answering_none(
    tmp_path: Path,
) -> None:
    environment = budgeted_host(tmp_path)

    answered = _hook("never-launched", environment)

    assert answered.returncode != 0
    assert answered.stdout == ""
