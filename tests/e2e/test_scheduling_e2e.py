"""The suite's own scheduling constraint, proved against a real distributed session.

`load_sensitive` is only worth anything if xdist acts on it, and everything about
that is invisible from inside a test: the marker is applied, the suite is green, and
the family is spread across the workers anyway. So this drives a real
`pytest -n 2 --dist loadgroup` session over the real plugin and reads which worker
ran what — the same way the leak guard's own journeys drive a real session rather
than asserting on the guard's internals.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

from scheduling import LOAD_SENSITIVE_GROUP
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT

TESTS_DIR = REPO_ROOT / "tests"
#: `[gw1] [ 25%] PASSED path::test@group` — the worker, and the node id the
#: controller scheduled, which is where xdist encodes the group.
_REPORT = re.compile(r"^\[(gw\d+)\] \[[^\]]*\] PASSED (\S+)", re.MULTILINE)
#: Enough constrained tests that landing on one worker cannot be a coincidence of
#: two, and enough unconstrained ones to keep both workers busy either way.
_CONSTRAINED = 4
_UNCONSTRAINED = 8

_SESSION = textwrap.dedent(
    f"""\
    import pytest


    @pytest.mark.load_sensitive
    @pytest.mark.parametrize("index", range({_CONSTRAINED}))
    def test_constrained(index: int) -> None:
        assert index >= 0


    @pytest.mark.parametrize("index", range({_UNCONSTRAINED}))
    def test_unconstrained(index: int) -> None:
        assert index >= 0
    """
)


def _distributed(directory: Path, *plugins: str) -> dict[str, str]:
    """Run a real two-worker session and return each passing node id's worker."""
    session = directory / "test_declared_scheduling.py"
    session.write_text(_SESSION, encoding="utf-8")
    plugin_arguments = [argument for plugin in plugins for argument in ("-p", plugin)]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *plugin_arguments,
            "-p",
            "no:cacheprovider",
            "-v",
            "-n",
            "2",
            "--dist",
            "loadgroup",
            str(session),
        ],
        cwd=directory,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(TESTS_DIR), "HOME": str(directory)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reported = {node: worker for worker, node in _REPORT.findall(result.stdout)}
    assert len(reported) == _CONSTRAINED + _UNCONSTRAINED, result.stdout
    return reported


def test_a_load_sensitive_family_is_scheduled_onto_one_worker(tmp_path: Path) -> None:
    """No two of the family are ever in flight at once, and both workers still work."""
    reported = _distributed(tmp_path, "scheduling")

    constrained = {node: worker for node, worker in reported.items() if "test_constrained" in node}
    assert len(constrained) == _CONSTRAINED
    assert all(node.endswith(f"@{LOAD_SENSITIVE_GROUP}") for node in constrained), constrained
    assert len(set(constrained.values())) == 1, (
        f"the family was spread across workers, so two of them can be in flight: {constrained}"
    )
    # Otherwise a single-worker session would satisfy the assertion above for free.
    assert len(set(reported.values())) == 2, reported


def test_an_undeclared_family_reaches_the_scheduler_as_ordinary_tests(tmp_path: Path) -> None:
    """The grouping is the plugin's doing, not something xdist infers from the name."""
    reported = _distributed(tmp_path)

    assert not [node for node in reported if node.endswith(f"@{LOAD_SENSITIVE_GROUP}")], reported
