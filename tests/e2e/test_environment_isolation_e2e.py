"""E2E coverage for nested pytest isolation from a live orchestrator channel."""

from __future__ import annotations

import os
import subprocess
import sys

from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT


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
