"""The history directory `tests/e2e/project_fixtures.py` mints lives as long as its process.

Every process that imports that module mints one directory under the shared temporary
root, and the `TemporaryDirectory` it holds at module scope is what removes it. The only
honest way to read a lifetime is from outside it: a real interpreter imports the module,
says where the directory is, and exits, and this reads the directory at both ends.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from orchestrator.root import REPO_ROOT

#: The child: import the module the way a test tier does — the suite's own `pythonpath`
#: entries on its path — say where the directory is, hold until told, and exit normally.
CHILD = """
import sys
sys.path[:0] = ["tests", "tests/e2e"]
import project_fixtures
print(project_fixtures._HISTORY, flush=True)
sys.stdin.readline()
"""


def test_the_history_directory_exists_while_its_interpreter_lives_and_is_gone_after() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    try:
        printed = child.stdout.readline().strip()
        assert printed, child.stderr.read() if child.stderr else ""
        history = Path(printed)
        assert history.name.startswith("ai-orchestrator-fixture-history-"), history
        assert history.is_dir(), f"{history} was named but not created at import"

        _, reported = child.communicate(input="\n", timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

    assert child.returncode == 0, reported
    assert not history.exists(), (
        f"{history} survived the interpreter that imported project_fixtures; the "
        f"`TemporaryDirectory` held at module scope is what removes it at exit"
    )
