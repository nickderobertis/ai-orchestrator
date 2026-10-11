"""The half of `tests/test_visual_spike_guidance.py` that scans the whole tree.

It sits in the `tree-scans` project.

These grep every tracked file outside `tests/` for a second statement of the cap, so what
they read is every file git tracks and they carry `reads_docs`, which routes them to that
project's whole-workspace `test-docs` target; the tests of that module that read none of it
stay there, in `orchestrator:test`.
"""

from __future__ import annotations

import subprocess

import pytest

from orchestrator.root import REPO_ROOT


@pytest.mark.reads_docs
def test_the_cap_is_stated_in_the_planner_persona_and_nowhere_else() -> None:
    """A second statement of the number is a second answer to drift from the first."""
    tracked = subprocess.run(
        ["git", "grep", "-l", "-E", "-z", r"max_turns`? no larger", "--", ".", ":!tests"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert tracked.returncode in (0, 1), tracked.stderr
    assert [one for one in tracked.stdout.split("\0") if one] == ["personas/planner.yaml"]
