"""A landing a test runs is nobody's supervised work unless the test names its owner.

The landing recipes register a landing as the supervised work of whichever manager session
their environment names, in whichever runs root it names. This suite runs inside a dispatch
whose environment names its manager's session and that host's real runs root, so a landing
a journey ran through `just integrate` once registered as that manager's flow on the real
host — one the manager's `Stop` hook then owed. `tests/conftest.py` scrubs those names from
every test; this journey proves it end to end, by running the journey that leaked as an
inner suite under a caller that names a session and a runs root, and reading that runs root
afterwards. The other half — a journey that names its own owner and runs root registers its
landings there — is what `test_manager_landing_supervision_e2e.py` drives.
"""

# The finding these answer is which Nx project owns this file: `publish-and-landing`, the
# directory-owned project whose journeys it runs, and whose key is the recipes and wrappers
# those journeys drive.
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[shell_test_tiers_stay_split] see above

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The journey whose `just integrate` registered a flow on the dispatching host.
LEAKED = REPO_ROOT / "tests" / "e2e" / "publish_and_landing" / "test_integrate_local_direct_e2e.py"
CALLER_SESSION = "caller-manager-session"


def test_a_landing_run_by_the_suite_registers_no_flow_for_the_session_running_it(
    tmp_path: Path,
) -> None:
    caller_runs = tmp_path / "caller-runs"
    caller_runs.mkdir()
    caller = {
        **os.environ,
        "ONEPIPELINE_RUNS_DIR": str(caller_runs),
        "ONEPIPELINE_LAUNCHER": "claude-code",
        "ONEPIPELINE_LAUNCHER_SESSION": CALLER_SESSION,
        "CLAUDE_CODE_SESSION_ID": CALLER_SESSION,
    }
    inner = subprocess.run(
        ["uv", "run", "pytest", str(LEAKED), "-p", "no:cacheprovider", "-n", "0"],
        cwd=REPO_ROOT,
        env=caller,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    assert inner.returncode == 0, inner.stdout + inner.stderr
    assert "2 passed" in inner.stdout, inner.stdout
    # Both of its trains ran through the engine's supervised verb, and neither became the
    # caller's: nothing was written under the caller's runs root, for any session.
    assert sorted(path.name for path in caller_runs.iterdir()) == [], list(caller_runs.rglob("*"))
