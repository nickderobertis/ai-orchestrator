"""E2E coverage for nested pytest isolation from an enclosing dispatch.

Both journeys run a real nested pytest session with the outer state exported, which
is the only way to prove what a suite *inherits*: a fixture asserting on its own
process cannot distinguish "scrubbed" from "never set here".
"""

from __future__ import annotations

import os
import subprocess
import sys

from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT
from orchestrator.harnesses import (
    JUDGE_HARNESS_ENV,
    PROCESS_WIDE_HARNESS_ENV,
    WORKER_HARNESS_ENV,
)

#: The journeys the leak actually broke, run nested under an exported selection. The
#: first states no selection at all, so every identity it observes must come from the
#: configured chains; the second launches the orchestrator role, whose whole claim is
#: that it resolves its own config — and oneharness's process-wide variable beats a
#: config, so an inherited one decides it. Both assert which provider was spawned, so
#: this stays a proof about behaviour rather than about a variable name.
_SELECTION_MODULE = "tests/e2e/test_harness_side_selection_e2e.py"
INHERITANCE_SENSITIVE_JOURNEYS = (
    f"{_SELECTION_MODULE}::test_with_no_selection_at_all_both_sides_resolve_their_configured_chains",
    f"{_SELECTION_MODULE}::test_the_orchestrator_role_is_outside_this_seam",
)


def test_real_pytest_path_ignores_parent_orchestrator_channel() -> None:
    environment = {
        **os.environ,
        "AI_ORCHESTRATOR_CHANNEL_DIR": "/definitely/not/a/test/channel",
        "AI_ORCHESTRATOR_CHANNEL_RUN_ID": "outer-live-run",
        "AI_ORCHESTRATOR_CHANNEL_FUTURE": "private",
    }

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/e2e/channel_environment_probe.py", "-q"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_real_pytest_path_ignores_an_enclosing_dispatchs_harness_selection() -> None:
    """A worker's own gate must not read the harness choice its dispatch was given.

    This is the environment a `--worker-harness` dispatch really runs its gate in:
    the wrapper resolves the per-side value into oneharness's process-wide variable
    and exports that, so all three arrive together. With only the per-side pair
    scrubbed, the third one reached the suite and the selection journeys failed on an
    unmodified `main` — a false failure on the very evidence the change is judged by.

    So the nested session runs the journeys that inherit their environment, beside a
    probe that reads it directly. Each journey asserts which provider was actually
    spawned, and inherits nothing to spawn it from.
    """
    environment = {
        **os.environ,
        WORKER_HARNESS_ENV: "codex",
        JUDGE_HARNESS_ENV: "codex",
        PROCESS_WIDE_HARNESS_ENV: "claude-code:alternate2",
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/e2e/harness_selection_environment_probe.py",
            *INHERITANCE_SENSITIVE_JOURNEYS,
            "-q",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
