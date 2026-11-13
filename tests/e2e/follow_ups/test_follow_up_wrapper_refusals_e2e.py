"""The drafting wrappers' help and refusals, driven as a manager and a dispatch meet them.

`just follow-up` and `scripts/follow-up-draft.sh` hand onefollowup what the environment and
their arguments say; before they do, each refuses what it cannot hand on — a missing run, a
flag given no value, an author it does not know, a monitor that names no member, a run the
engine names no public id for, and the provenance the manager's recipe decides itself — with
a diagnostic saying what to do. Each case runs the real recipe or script with the real engine
over a runs root of the test's own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

DRAFT = REPO_ROOT / "scripts" / "follow-up-draft.sh"
BODY = "## What happened\nx\n\n## Where\ny\n\n## Why it is out of scope\nz\n\n## Evidence\nw\n"
DRAFTED = ["--title", "Never drafted", "--repository", "github.com/nickderobertis/some-service"]


class Refusal(NamedTuple):
    """One invocation, the status it ends with, and what its diagnostic must say."""

    argv: list[str]
    status: int
    says: str


CASES = {
    "recipe-help": Refusal(["just", "follow-up", "--help"], 0, "Records one unverified follow-up"),
    "recipe-no-run": Refusal(["just", "follow-up", *DRAFTED], 2, "name the run this follow-up"),
    "recipe-provenance": Refusal(
        ["just", "follow-up", "some-run", "--as", "node", *DRAFTED], 2, "is this recipe's to decide"
    ),
    "draft-help": Refusal([str(DRAFT), "--help"], 0, "## What happened"),
    "draft-no-value": Refusal([str(DRAFT), *DRAFTED, "--run"], 2, "--run was given no value"),
    "draft-no-run": Refusal([str(DRAFT), *DRAFTED], 2, "no run is named"),
    "draft-unknown-author": Refusal(
        [str(DRAFT), "--as", "robot", "--run", "some-run", *DRAFTED], 2, "node, monitor or manager"
    ),
    "draft-monitor-no-member": Refusal(
        [str(DRAFT), "--as", "monitor", "--run", "some-run", *DRAFTED], 2, "names its member"
    ),
    "draft-unknown-run": Refusal(
        [str(DRAFT), "--as", "manager", "--run", "never-launched", *DRAFTED],
        2,
        "named no public id for run 'never-launched'",
    ),
}


@pytest.mark.parametrize("case", list(CASES))
def test_a_drafting_wrapper_refuses_what_it_cannot_hand_on(case: str, tmp_path: Path) -> None:
    refusal = CASES[case]
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ("ONEPIPELINE_RUN_ID", "ONEPIPELINE_NODE_ID")
    }
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")

    ran = subprocess.run(  # noqa: S603 - this checkout's own recipe and drafting command
        refusal.argv,
        cwd=REPO_ROOT,
        env=environment,
        input=BODY,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert ran.returncode == refusal.status, ran.stdout + ran.stderr
    assert refusal.says in ran.stdout + ran.stderr, ran.stdout + ran.stderr
