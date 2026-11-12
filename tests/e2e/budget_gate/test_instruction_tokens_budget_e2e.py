"""The pre-push hook refuses a push whose instruction text outgrows its token budget.

`.githooks/pre-push` runs `gate_within_budgets` (`scripts/gate-budgets.sh`), which runs the
gate and then holds the push to the root `budgets.yaml` with the installed onebudgetspec.
These journeys drive that step as the hook calls it — the real step, the installed
onebudgetspec, the root file's own `agents-md-instruction-tokens` and
`planner-prompt-instruction-tokens` entries, unchanged, and the real
`orchestrator/instruction_tokens.py` — over a temporary checkout whose `AGENTS.md` and
`personas/planner.yaml` are sized against the thresholds the tracked entries state. Only the
complete gate the step wraps is stood in by a gate that succeeds, as every journey beside
this one does.

llmlint: ignore-file[e2e_not_mocked] The gate the step runs first is a stand-in that exits
0, because running the complete `just gate` inside a test is a second gate run; the budget
refusal under test — the hook's step, onebudgetspec, the tracked entries and the measuring
module — all runs for real, and every publishing push runs the real gate through it.

llmlint: ignore-file[tests_mirror_real_usage] The journeys call `gate_within_budgets`, the
hook's step, rather than `.githooks/pre-push` whole, because the hook's own line runs the
complete gate; the hook line naming the step is what every publication runs.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] What these spend is about a
second apiece — a stand-in gate, one onebudgetspec check and two Python starts, no launch and
no paid turn — and what they read is `scripts/gate-budgets.sh`, `scripts/lock-timeout.sh`,
`orchestrator/instruction_tokens.py` and `budgets.yaml`, which this project's key already
covers; a project of their own would narrow that key at the price of a catalog entry in each
place `tests/nx_inputs.py` and `tests/tree_scans/test_nx_cache_scope.py` hold every project to.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from waits import end_carrying
from waits import timeout as e2e_timeout

from orchestrator.instruction_tokens import BYTES_PER_TOKEN
from orchestrator.root import REPO_ROOT

STEP = REPO_ROOT / "scripts" / "gate-budgets.sh"
ROOT_BUDGETS = REPO_ROOT / "budgets.yaml"
AGENTS = "agents-md-instruction-tokens"
PLANNER = "planner-prompt-instruction-tokens"
#: What the measuring module needs beside itself to run from another checkout.
MODULES = ("instruction_tokens.py", "root.py")


@pytest.fixture(autouse=True)
def _nothing_outlives_the_test(tmp_path: Path) -> Iterator[None]:
    yield
    left = end_carrying(tmp_path)
    assert not left, f"processes {left} this test started were still running when it ended"


def _tracked() -> dict[str, dict[str, object]]:
    document = yaml.safe_load(ROOT_BUDGETS.read_text(encoding="utf-8"))
    return {
        budget["id"]: budget for budget in document["budgets"] if budget["id"] in (AGENTS, PLANNER)
    }


def _bytes_for(budget: str, *, over: bool) -> int:
    """A text size one token past the tracked threshold, or comfortably within it."""
    threshold = float(str(_tracked()[budget]["threshold"]))
    tokens = threshold + 1 if over else threshold / 2
    return math.ceil(tokens * BYTES_PER_TOKEN)


def _checkout(root: Path, *, agents: int, prompt: int) -> Path:
    """A checkout holding the module, the two tracked entries and texts of these sizes."""
    checkout = root / "checkout"
    (checkout / "orchestrator").mkdir(parents=True)
    for name in MODULES:
        shutil.copy(REPO_ROOT / "orchestrator" / name, checkout / "orchestrator" / name)
    (checkout / ".venv").symlink_to(REPO_ROOT / ".venv")
    (checkout / "personas").mkdir()
    (checkout / "AGENTS.md").write_text("a" * agents, encoding="utf-8")
    (checkout / "personas" / "planner.yaml").write_text(
        yaml.safe_dump({"system_prompt": "p" * prompt}, width=1 << 30), encoding="utf-8"
    )
    (checkout / "budgets.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "budgets": list(_tracked().values())}),
        encoding="utf-8",
    )
    gate = root / "stand-in-gate"
    gate.write_text("#!/usr/bin/env bash\necho gate-ran >&2\n", encoding="utf-8")
    gate.chmod(0o755)
    return checkout


def _push(root: Path, checkout: Path) -> subprocess.CompletedProcess[str]:
    """Run the hook's budget step over the checkout's budgets file, with the stand-in gate."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ORCHESTRATOR_GATE_PUSH", "ORCHESTRATOR_GATE_SCRATCH"}
    }
    environment.update(
        {
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "ONEPIPELINE_RUNS_DIR": str(root / "runs"),
        }
    )
    for directory in ("state", "cache", "runs"):
        (root / directory).mkdir(exist_ok=True)
    return subprocess.run(
        [
            "bash",
            "-c",
            f'. "{STEP}"\ngate_within_budgets "$1" "$2"',
            "step",
            str(checkout / "budgets.yaml"),
            str(root / "stand-in-gate"),
        ],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _verdict(stdout: str, budget: str) -> str:
    for line in stdout.splitlines():
        if line.startswith(f"budget {budget}:"):
            return line.rsplit(" — ", 1)[1].split(";", 1)[0]
    raise AssertionError(f"no result for {budget} in {stdout}")


def test_a_root_file_over_its_budget_refuses_the_push(tmp_path: Path) -> None:
    checkout = _checkout(
        tmp_path,
        agents=_bytes_for(AGENTS, over=True),
        prompt=_bytes_for(PLANNER, over=False),
    )

    pushed = _push(tmp_path, checkout)

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert "gate-ran" in pushed.stderr, pushed.stderr
    assert _verdict(pushed.stdout, AGENTS) == "over", pushed.stdout
    assert _verdict(pushed.stdout, PLANNER) == "within", pushed.stdout
    assert f"budget {AGENTS} is over" in pushed.stderr, pushed.stderr
    assert "this push is refused" in pushed.stderr, pushed.stderr


def test_a_planner_prompt_over_its_budget_refuses_the_push(tmp_path: Path) -> None:
    checkout = _checkout(
        tmp_path,
        agents=_bytes_for(AGENTS, over=False),
        prompt=_bytes_for(PLANNER, over=True),
    )

    pushed = _push(tmp_path, checkout)

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    assert _verdict(pushed.stdout, PLANNER) == "over", pushed.stdout
    assert _verdict(pushed.stdout, AGENTS) == "within", pushed.stdout
    assert f"budget {PLANNER} is over" in pushed.stderr, pushed.stderr


def test_both_within_lets_the_push_through(tmp_path: Path) -> None:
    checkout = _checkout(
        tmp_path,
        agents=_bytes_for(AGENTS, over=False),
        prompt=_bytes_for(PLANNER, over=False),
    )

    pushed = _push(tmp_path, checkout)

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert _verdict(pushed.stdout, AGENTS) == "within", pushed.stdout
    assert _verdict(pushed.stdout, PLANNER) == "within", pushed.stdout
    assert "refused" not in pushed.stderr, pushed.stderr
