"""Real session-setup journeys kept behind their own host-tool target."""

from __future__ import annotations

import subprocess

from nx_workspace import shares_workspace_install
from published_tools import ONETASKGRAPH_BIN

from orchestrator.root import REPO_ROOT


@shares_workspace_install
def test_session_setup_keeps_the_locked_plan_store_and_plan_root_in_force() -> None:
    """The real setup verifies the lock-installed CLI and ensures the plan root exists."""
    plans = REPO_ROOT / ".plans"
    provisioned = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "session-setup.sh")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert provisioned.returncode == 0, provisioned.stdout + provisioned.stderr
    adopted = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    assert f"ready (onetaskgraph: {adopted} at {ONETASKGRAPH_BIN})" in provisioned.stderr
    assert (plans / "tasks").is_dir()
    assert (plans / "projects").is_dir()
