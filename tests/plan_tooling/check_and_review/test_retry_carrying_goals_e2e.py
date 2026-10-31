"""A live edit restating a plan task that carries `## Goals` is not read as an amendment.

`config/onemessagebus.yaml` declares `scripts/envelope-review.sh` as the validator on the
`replies` queue, and it reads every section of a whole task a `retry` states that no other
reader examines as a manager's amendment, asking the bar's questions of it. `## Goals` is a
section of this host's `plan-task` template, whose outcomes the plan review already held the
criteria to, so a replacement task carrying it is not refused for it — while a section a
manager added above the operational notes of the same rendering still is.

The task is a real rendering: the pinned engine's `template resolve plan-task` piped into the
pinned store's `template render`. The validator runs as the bus runs it, through its script,
both ways: the refusal is the deterministic tier's, before any turn, and the pass reaches the
judged turn, where only the paid provider's process is substituted, at the seam the
review-plan journeys beside this one stand it in at.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] These journeys spawn the same
installed engine, store and scripts as the check-plan journeys beside them, under this
project's `planToolingCheckAndReviewWorkspace`, which names the templates, scripts, config and
package they read; a project of their own would be keyed on those same inputs.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import short_state
from project_fixtures import helper
from published_tools import ONETASKGRAPH_BIN

from orchestrator import envelope_review
from orchestrator.plan_review import Verdict
from orchestrator.root import REPO_ROOT

#: A plan task's answers stating goals, one of them naming the command an operator runs,
#: which a reader asking the bar's questions of the section refuses as a `just` invocation —
#: so a pass below is the exemption working rather than an easy section.
ANSWERS: dict[str, object] = {
    "what": "Page the node listing.",
    "why": "An operator cannot see past the first screen of nodes.",
    "goals": [
        "An operator running `just status` on a 2,000-node run sees every node, a page at a time.",
        "The first page renders within the `listing-latency` budget.",
    ],
    "acceptance_criteria": [
        "The finished tree carries an assertion whose subject is that behaviour.",
    ],
}

#: A manager's correction under a heading of its own, above the operational notes.
AMENDMENT_SECTION = (
    "## Amendment\n\nWhere this and the notes below it disagree, this wins: the finished "
    "branch merges cleanly into its base and its change request's required checks pass."
)

PASSES = Verdict(passes=True, findings=[])

#: The paid provider's stand-in, and the guard covering the identities `ONEHARNESS_BIN_*`
#: cannot reach, as the review-plan journeys beside this one substitute them.
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")


def _rendered(tmp_path: Path) -> str:
    """``ANSWERS`` rendered by the pinned store from the plan-task chain a launch resolves."""
    resolved = subprocess.run(
        [str(REPO_ROOT / "scripts" / "onepipeline.sh"), "template", "resolve", "plan-task"]
        + ["--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(ANSWERS), encoding="utf-8")
    rendered = subprocess.run(
        [str(ONETASKGRAPH_BIN), "template", "render", "--template-loader", "-"]
        + ["--answers", str(path), "--no-interactive"],
        cwd=REPO_ROOT,
        input=resolved.stdout,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "\n## Goals\n" in rendered.stdout, rendered.stdout
    return rendered.stdout


def _retry(task: str) -> str:
    """One reply envelope carrying a `retry` that states ``task`` whole."""
    node = {"id": "work-2", "persona": "engineer", "task": task}
    command = {"op": "retry", "id": "work", "node": node}
    return json.dumps({"version": 2, "author": "planner", "commands": [command]})


def _validate(
    envelope: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """``envelope`` through the validator command `config/onemessagebus.yaml` declares."""
    return subprocess.run(
        [str(REPO_ROOT / "scripts" / "envelope-review.sh")],
        cwd=REPO_ROOT,
        env=environment,
        input=envelope,
        text=True,
        capture_output=True,
        check=False,
    )


def test_a_retrys_plan_task_rendering_carrying_goals_raises_no_unreviewed_section(
    tmp_path: Path,
) -> None:
    """It reaches its judged turn and passes, so the deterministic tier refused nothing."""
    environment = dict(os.environ)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(PASSES)])
    prompts = tmp_path / "prompts.jsonl"
    environment["FAKE_CODEX_PROMPT_LOG"] = str(prompts)
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))

    validated = _validate(_retry(_rendered(tmp_path)), environment)

    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert validated.stderr == "", validated.stderr
    assert prompts.is_file(), "the replacement task never reached its judged turn"
    asked = " ".join(prompts.read_text(encoding="utf-8").split())
    assert "listing-latency" in asked, "the judged turn was not handed the replacement task"


def test_a_section_added_to_the_same_rendering_is_still_refused_by_the_validator_command(
    tmp_path: Path,
) -> None:
    """The command the bus runs refuses it before any turn, naming the section."""
    task = _rendered(tmp_path)
    amended = task.replace(
        "\n## Additional info\n", f"\n{AMENDMENT_SECTION}\n\n## Additional info\n", 1
    )
    assert AMENDMENT_SECTION in amended

    spawned = _validate(_retry(amended))

    assert spawned.returncode == envelope_review.REFUSED, spawned.stderr
    assert "under '## Amendment'" in spawned.stderr, spawned.stderr
    assert "## Goals" not in spawned.stderr, spawned.stderr
