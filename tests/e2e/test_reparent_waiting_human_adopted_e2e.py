"""A waiting human approval can be given new prerequisites, and waits for them again.

Before https://github.com/nickderobertis/onepipeline/issues/668 was fixed (its landing is
https://github.com/nickderobertis/onepipeline/pull/777, first cut as 0.61.1), a `kind:
human` node was recorded `waiting` as soon as its prerequisites were done, and `reparent`
refused every node with a recorded status as already started. So once an approval began
waiting, a manager could not put it behind work it had to follow, and every other edit
in the same envelope was refused with it. `tests/test_adopted_engine_carries_this_plan.py`
holds that the adopted release's history contains the landing; this journey holds that the
installed engine does what it landed, through the recipes a manager uses: `just
channel-reply` to edit and attest, `just status` to read the run, and `just orchestrate
--adopt` to drive it.

The run is an approval with one agent node behind it, launched with `--dag-graph off` so
nothing but this journey writes to its channel. The launch hands the run back paused at
the approval. Everything is real — the recipes, the `onepipeline` beneath them, the
envelope validator `config/onemessagebus.yaml` declares, and the run's own records. Only
the paid model is doubled, where every launch journey here doubles it: the dispatched
nodes' turns, and the one judged review turn the validator spends on the added node's
task, scripted to pass.
"""

# The same answers `tests/e2e/test_adopted_engine_reads_an_adopted_run_e2e.py` gives, for
# the same reason: this launches the installed engine through the recipes, so it sits in
# the code-keyed tier every such launch here is in and shares that tier's fixtures, and the
# xdist group is what holds it off the toolchain lock.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[shell_test_tiers_stay_split] see above

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import project_from_plan
from test_adopted_engine_reads_an_adopted_run_e2e import _just, _until
from test_orchestrate_launch_e2e import (
    CandidatePlan,
    EditCommand,
    PlanNode,
    ReplyEnvelope,
    _node,
)
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import timeout as e2e_timeout

from orchestrator.plan_review import Verdict
from orchestrator.root import REPO_ROOT
from orchestrator.run_reading import Decided, Decision, RunId, decide

#: `tests/e2e/nx_workspace.py`'s group: every step here is a `just` recipe blocking on
#: `uv run`, which waits on the lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: A launching session this journey states rather than inherits, so the run is this
#: session's own to adopt and `just stop`.
LAUNCHING_SESSION = "e2e-reparent-waiting-human"

APPROVAL = "approve"
SHIP = "ship"
PREREQUISITE = "prepare"

#: `just channel-reply`'s exit status for a reply refused with nothing appended.
REFUSED = 2

#: The judged review's verdict on the added node's task, as the doubled provider gives it.
PASSING_VERDICT: Verdict = {"passes": True, "findings": []}


def _task(what: str) -> str:
    return (
        f"## What\n\n{what}\n\n## Why\n\nSo the approval has work to come after.\n\n"
        "## Acceptance criteria\n\n- The report names what was prepared.\n"
    )


_PLAN: CandidatePlan = {
    "schema_version": 2,
    "tasks": [
        {"id": APPROVAL, "kind": "human", "task": "Approve the release."},
        _node(id=SHIP, deps=[APPROVAL], task=_task("Report that the release shipped.")),
    ],
}


class PausedRun(NamedTuple):
    """A run handed back at its waiting approval, and the environment every recipe shares."""

    environment: dict[str, str]
    run: RunId


def _reading(paused: PausedRun) -> Decided:
    """`just status --json`, decided by the reader that checks it against the engine's schema.

    `orchestrator.run_reading` validates every field of the document and is held to the
    published schema by `tests/test_run_reading.py`, so nothing here restates its shape.
    """
    status = _just("status", paused.run, "--json", "--no-providers", environment=paused.environment)
    assert status.returncode == 0, status.stdout + status.stderr
    return decide(status.stdout, paused.run)


def _status(paused: PausedRun) -> str:
    """`just status` as a manager reads it, without the host's provider report."""
    status = _just("status", paused.run, "--no-providers", environment=paused.environment)
    assert status.returncode == 0, status.stdout + status.stderr
    return status.stdout


def _awaiting_attestation(paused: PausedRun) -> bool:
    """Whether the run is paused on the approval's attestation alone and nothing drives it."""
    return _reading(paused) == Decided(Decision.PAUSED, f'human action "{APPROVAL}" is waiting')


def _send(paused: PausedRun, *commands: EditCommand) -> subprocess.CompletedProcess[str]:
    """Send one edit envelope through the manager's recipe, as a manager types it."""
    envelope: ReplyEnvelope = {"version": 3, "commands": list(commands)}
    return subprocess.run(
        ["just", "channel-reply", paused.run],
        cwd=REPO_ROOT,
        env=paused.environment,
        input=json.dumps(envelope),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _applied(paused: PausedRun, *commands: EditCommand) -> None:
    """Send an envelope and hold that the engine's receipt says it was applied."""
    sent = _send(paused, *commands)
    assert sent.returncode == 0, sent.stdout + sent.stderr
    receipt = json.loads(sent.stdout.strip().splitlines()[-1])
    assert receipt.get("state") == "applied", sent.stdout + sent.stderr


def _refused(paused: PausedRun, *commands: EditCommand) -> str:
    """Send an envelope and hold that it was refused with nothing appended."""
    sent = _send(paused, *commands)
    assert sent.returncode == REFUSED, f"exit {sent.returncode}:\n{sent.stdout}{sent.stderr}"
    assert sent.stdout == "", f"a refused reply printed a receipt anyway: {sent.stdout}"
    return sent.stderr


def _results(paused: PausedRun) -> str:
    """`just results` for the run: one line per node, naming how it settled."""
    results = _just("results", paused.run, environment=paused.environment, seconds=120)
    assert results.returncode == 0, results.stdout + results.stderr
    return results.stdout


def _settled(paused: PausedRun, node: str) -> str | None:
    """The status `just results` reports `node` settled with, or `None` while it has not."""
    for line in _results(paused).splitlines():
        words = line.split()
        if len(words) >= 2 and words[0] == node:
            return words[1] if words[1] in {"done", "failed", "skipped"} else None
    return None


def _adopted(paused: PausedRun) -> None:
    """Drive the run with a fresh driver until it hands the run back or settles."""
    adopting = _just("orchestrate", "--adopt", paused.run, environment=paused.environment)
    assert adopting.returncode == 0, adopting.stdout + adopting.stderr


@pytest.fixture
def paused(tmp_path: Path, oneharness_bin: str) -> Iterator[PausedRun]:
    """Launch the plan attached and hand the run over once it waits on the approval."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    # The recipes and the engine are real; the paid provider is doubled where every launch
    # journey here doubles it.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(tmp_path, oneharness_bin, session=LAUNCHING_SESSION)
    # The judged review turn the validator spends on the added node's task.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider's answer is scripted.
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(PASSING_VERDICT)])
    plan = tmp_path / "reparent-waiting-human.plan.json"
    plan.write_text(json.dumps(_PLAN), encoding="utf-8")
    project = project_from_plan(plan)
    paused = PausedRun(environment, RunId(project.split(":", 1)[1]))
    try:
        launched = _just("orchestrate", project, "--dag-graph", "off", environment=environment)
        assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}{launched.stderr}"
        _until("the approval to wait", lambda: _awaiting_attestation(paused))
        yield paused
    finally:
        _just("stop", paused.run, environment=environment, seconds=120)


def test_a_waiting_approval_reparented_onto_unfinished_work_waits_for_it_again(
    paused: PausedRun,
) -> None:
    """Reparent the waiting approval onto new work: it is re-gated, then attested.

    Under the engine before the landing the reparent below was refused as already started,
    and the `add` beside it with it, so the approval stayed attestable ahead of the work.
    """
    # An envelope attesting the approval and then reparenting it is refused whole: the
    # attestation is judged done before the reparent, and is not applied on its own.
    said = _refused(
        paused,
        {"op": "attest", "ref": APPROVAL},
        {"op": "reparent", "id": APPROVAL, "deps": [PREREQUISITE]},
    )
    assert f"reparent: node '{APPROVAL}'" in said, said
    assert _awaiting_attestation(paused), "a refused envelope attested the approval"

    prerequisite: PlanNode = {
        "id": PREREQUISITE,
        "persona": "engineer",
        "task": _task(f"Prepare the release run {paused.run} approves."),
    }
    _applied(
        paused,
        {
            "op": "add",
            "node": prerequisite,
        },
        {"op": "reparent", "id": APPROVAL, "deps": [PREREQUISITE]},
    )

    # Re-gated: the run no longer waits on the approval, the work it now follows is what
    # is next, and an attestation is refused while that work is unfinished.
    # Undriven, unended and waiting on no decision: the reader's word for that is
    # `crashed`, and here it means only that the ready work has no driver yet.
    regated = _reading(paused)
    assert regated.decision is Decision.CRASHED, regated
    status = _status(paused)
    assert f"{PREREQUISITE}: ready" in status, status
    assert f"attest {paused.run} {APPROVAL}" not in status, status
    said = _refused(paused, {"op": "attest", "ref": APPROVAL})
    assert f"'{APPROVAL}' is not a ready, waiting human action" in said, said

    # Driven, the new work runs and settles, and the approval waits on its attestation
    # again with the node behind it still undispatched.
    _adopted(paused)
    assert _awaiting_attestation(paused), _status(paused)
    assert _settled(paused, PREREQUISITE) == "done", _results(paused)
    assert _settled(paused, SHIP) is None, _results(paused)

    _applied(paused, {"op": "attest", "ref": APPROVAL})
    _adopted(paused)
    finished = _reading(paused)
    assert finished.decision is Decision.ENDED, finished
    assert "SETTLED" in _status(paused), _status(paused)
    assert {_settled(paused, node) for node in (APPROVAL, PREREQUISITE, SHIP)} == {"done"}, (
        _results(paused)
    )
