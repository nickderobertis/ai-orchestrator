"""A dispatched worker declines its manager's ruling itself, on a real launch.

A manager's ruling is a strong default every party below the manager may decline when it
judges the ruling wrong for the user's goal — only through `$ORCHESTRATOR_DECLINE_RULING`,
with its reason, and never silently (`config/onejudge.base.yaml`,
`templates/dispatch-appendix.md`). A user ruling binds, and the verb refuses to decline one
(`tests/ask_seam/decline_ruling/`).

The journey launches a real run through `just orchestrate`, holds it at a human gate,
carries one manager note to the node behind the gate through `just channel-reply`, opens the
gate and adopts the run so the node is really dispatched. Only the paid model is doubled, at
the `oneharness` seam (`tests/e2e/fake_backend.py`): its worker-side turn finds the
manager's ruling reference in the attribution the engine rendered into its own prompt and
runs `"$ORCHESTRATOR_DECLINE_RULING" --ruling <ref>` with its reason, from its own inherited
environment. The run's channel then hands the manager, through the engine's channel read
`just channel-next` wraps, the one `declined-ruling` surface carrying the worker's reason.

A dispatched judge runs in oneharness's `read-only` mode and cannot run the command yet,
so the appendix has it state a departure in its verdict, and a judge raising its own surface
is a later change. What this host owes it now is the route: every turn's environment is read
off the turn's own provider invocation, as `test_dispatch_environment_e2e.py` reads it, so
the wrapper and the run it names are shown to reach the worker side and the judge side alike.
"""


# Why this file is in the launch project rather than behind a narrower edge: it sits beside
# `test_orchestrate_launch_e2e.py`, in the code-keyed tier every launch of the installed
# engine in this repository is in; what it depends on — the recipes, the engine, the graphs,
# the personas and the prompts a dispatch is composed from — is the whole of that key, so no
# narrower edge exists to put it behind.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import short_state
from fake_backend import (
    DECLINE_REASON_ENV,
    DECLINE_RECORD_ENV,
    DECLINE_RULING_ENV,
    DISPATCHED_MEMBER,
    ENVIRONMENT_KEYS_ENV,
    JUDGE_CONFIG_NAME,
    MANAGER_RULING,
    MEMBER_OF_CONFIG,
    PROMPT_LOG_ENV,
    DeclineRecord,
    RecordedTurn,
)
from planner_channel import next_surface_record
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

# What the recipes these journeys run leave in the temporary directory is this module's own
# and goes with it (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: A launching session this journey states rather than inherits: this suite runs inside a
#: dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-worker-declines-a-ruling"

#: Every name a launcher identity and the enclosing dispatch reach a launch through, so the
#: run's dispatches carry this launch's values rather than the outer one's.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ORCHESTRATOR_ASK_MANAGER",
    DECLINE_RULING_ENV,
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

GATE_NODE = "gate"
WORK_NODE = "work"
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"

#: The wrapper every dispatch must be handed, in this checkout.
DECLINE_WRAPPER = REPO_ROOT / "scripts" / "decline-ruling.sh"

#: The manager's ruling the node is handed, and the reason its worker declines it with.
MANAGER_NOTE = "Drop the failure-path test; the happy path is enough for this node."
REASON = (
    "The user's goal is a change proven on its failure path too; dropping it hides a regression."
)


class Dispatched(NamedTuple):
    """What the launched run left: its environment, its id, and every turn the model saw."""

    environment: dict[str, str]
    run: str
    turns: list[RecordedTurn]
    decline: DeclineRecord


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 180, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _task(what: str) -> str:
    return (
        f"## What\n{what}\n\n## Why\nThe ruling is the subject.\n\n## Acceptance criteria\n- Done."
    )


def _reply(environment: dict[str, str], run: str, *commands: dict[str, Any]) -> dict[str, Any]:
    """Send one edit envelope as a manager types it, and read the engine's receipt."""
    sent = _just(
        "channel-reply",
        run,
        environment=environment,
        seconds=120,
        stdin=json.dumps({"version": 4, "commands": list(commands)}),
    )
    assert sent.returncode == 0, sent.stderr + sent.stdout
    # `cast` rather than a validating read: the engine owns the receipt.
    receipt = cast(dict[str, Any], json.loads(sent.stdout))
    assert receipt.get("state") == "applied" and receipt.get("commands") == "applied", receipt
    return receipt


@pytest.fixture(scope="module")
def dispatched(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[Dispatched]:
    """Launch the gated plan, carry a manager note to its node, open the gate, and adopt.

    The launch hands the run back at its gate, so the note is applied to a run nothing
    drives and is carried to the node's first dispatch; the adopt then drives the run to
    settlement, dispatching the node once, with this host's observer graph left off so the
    only surface on the channel afterwards is the one the worker raised.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("worker-declines-a-ruling")
    run = f"worker-declines-{os.getpid()}"
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(helper("fake_backend.py"))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(helper("fake_codex.py"))
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    turns_log = tmp_path / "turns.jsonl"
    decline_record = tmp_path / "decline.json"
    environment[PROMPT_LOG_ENV] = str(turns_log)
    environment[ENVIRONMENT_KEYS_ENV] = f"{RUN_ID_ENV},{DECLINE_RULING_ENV}"
    environment[DECLINE_REASON_ENV] = REASON
    environment[DECLINE_RECORD_ENV] = str(decline_record)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "a worker declines its manager's ruling with its reason"},
                "name": run,
                "tasks": [
                    {"id": GATE_NODE, "kind": "human", "task": _task("Approve.")},
                    {
                        "id": WORK_NODE,
                        "persona": "engineer",
                        "deps": [GATE_NODE],
                        "task": _task("Report, changing nothing."),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = _just(
        "orchestrate",
        project_from_plan(plan),
        "--dag-graph",
        "off",
        environment=environment,
        seconds=600,
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    try:
        _reply(
            environment,
            run,
            {"op": "note", "id": WORK_NODE, "addressee": "both", "text": MANAGER_NOTE},
        )
        _reply(environment, run, {"op": "attest", "ref": GATE_NODE})
        adopted = _just("orchestrate", "--adopt", run, environment=environment, seconds=600)
        assert adopted.returncode == 0, f"the adopt failed:\n{adopted.stdout}\n{adopted.stderr}"
        assert turns_log.is_file(), f"no harness turn was recorded at {turns_log}"
        assert decline_record.is_file() and decline_record.read_text(encoding="utf-8"), (
            "no worker-side turn of the dispatched node was handed a manager's ruling to "
            "decline, so the attribution never reached the worker's prompt"
        )
        # `cast` rather than a validating read: `tests/e2e/fake_backend.py` writes both, and
        # `RecordedTurn` states the turn's shape beside the code that writes it.
        turns = [
            cast(RecordedTurn, json.loads(line))
            for line in turns_log.read_text(encoding="utf-8").splitlines()
        ]
        decline = cast(DeclineRecord, json.loads(decline_record.read_text(encoding="utf-8")))
        yield Dispatched(environment, run, turns, decline)
    finally:
        _just("stop", run, environment=environment, seconds=60)


def _sides(turns: list[RecordedTurn]) -> tuple[list[RecordedTurn], list[RecordedTurn]]:
    """The dispatched member's worker-side turns and its judge-side turns."""
    worker: list[RecordedTurn] = []
    judge: list[RecordedTurn] = []
    for turn in turns:
        named = MEMBER_OF_CONFIG.search(turn["config"] or "")
        if named is None or named.group(1) != DISPATCHED_MEMBER:
            continue
        (judge if Path(turn["config"] or "").name == JUDGE_CONFIG_NAME else worker).append(turn)
    return worker, judge


def test_the_worker_declines_the_managers_ruling_and_the_manager_reads_its_reason(
    dispatched: Dispatched,
) -> None:
    """The worker ran the wrapper itself, and the manager's next read is its decline.

    The reference it named is the one the engine rendered beside the manager's note in the
    worker's own prompt, and the surface's typed record carries the note's text and the
    worker's reason, so what reached the manager is the departure the worker raised.
    """
    decline = dispatched.decline
    assert decline["wrapper"] == str(DECLINE_WRAPPER), decline
    assert Path(decline["config"] or "").name != JUDGE_CONFIG_NAME, decline
    assert decline.get("status") == 0, decline

    surface = next_surface_record(dispatched.run, dispatched.environment)
    assert surface is not None, "the run's channel handed the manager nothing"
    assert surface["kind"] == "declined-ruling", surface
    assert surface["blocking"] is False, surface
    record = cast(dict[str, Any], surface)["declined_ruling"]
    assert (record["node"], record["ruling"], record["provenance"], record["kind"]) == (
        WORK_NODE,
        decline["ruling"],
        "manager",
        "note",
    ), record
    assert record["text"] == MANAGER_NOTE, record
    assert record["reason"] == REASON, record
    assert next_surface_record(dispatched.run, dispatched.environment) is None, (
        "the run raised more than the one decline, so the worker's is not the only departure"
    )


def test_the_ruling_reached_both_sides_and_only_the_worker_declined_it(
    dispatched: Dispatched,
) -> None:
    """The attributed note reached the worker's first turn and the judge's turns, and the one
    decline record names a worker-side config, so the decline was the worker's own."""
    worker, judge = _sides(dispatched.turns)
    assert worker and judge, "the node was not dispatched as a two-party conversation"
    assert MANAGER_RULING.search(worker[0]["prompt"]), (
        "the worker's first turn was not handed the manager's attributed note"
    )
    assert any(MANAGER_RULING.search(turn["prompt"]) for turn in judge), (
        "no judge-side turn was handed the manager's attributed note"
    )
    assert Path(dispatched.decline["config"] or "") in {
        Path(turn["config"] or "") for turn in worker
    }, dispatched.decline


def test_both_sides_of_a_dispatch_are_handed_the_wrapper_and_the_run_it_names(
    dispatched: Dispatched,
) -> None:
    """Read off each turn's own provider invocation: every worker-side and every judge-side
    turn of the dispatched node carries `ORCHESTRATOR_DECLINE_RULING` naming this checkout's
    wrapper and `ONEPIPELINE_RUN_ID` naming this run — the run the wrapper declines on."""
    worker, judge = _sides(dispatched.turns)
    for side, turns in (("worker", worker), ("judge", judge)):
        assert turns, f"no {side}-side turn of the dispatched node was recorded"
        carried = {
            (turn["environment"].get(DECLINE_RULING_ENV), turn["environment"].get(RUN_ID_ENV))
            for turn in turns
        }
        assert carried == {(str(DECLINE_WRAPPER), dispatched.run)}, (
            f"the {side} side was handed {sorted(map(str, carried))}; a decline from it "
            "would reach no wrapper, or the wrong run"
        )
