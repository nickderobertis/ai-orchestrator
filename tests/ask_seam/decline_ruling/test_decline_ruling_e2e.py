"""A dispatched agent declines its manager's ruling over a real run's channel, with its reason.

`scripts/decline-ruling.sh` is the command `ORCHESTRATOR_DECLINE_RULING` names, and it is the
engine's `onepipeline decline-ruling` against the run the dispatch belongs to and nothing
else, exactly as `scripts/ask-manager.sh` is the engine's `onepipeline ask`. What a declined
ruling *is* — the typed `declined_ruling` record, the refusal of a user's ruling, the
reference the engine mints — is the engine's, proven by its own suite. What these journeys
prove is this host's wiring: that on a run `just orchestrate` really launched, the wrapper an
agent runs reaches the manager's own `just channel-next` with the reason it gave, and that a
ruling relayed as the user's words through `just channel-reply --as user` is one it cannot
decline, with nothing appended to the channel.

The ruling reference is the one thing composed here rather than read: an agent reads it from
the attribution the engine renders beside a ruling in its dispatch, and nothing here is
dispatched. It is spelled `c<envelope>.<index>` from the receipt the manager's own
`just channel-reply` printed — `reply` is the envelope's id, and the note is its only
command — which is the grammar `tests/test_engine_contracts.py` holds to the engine's
`Reference`, and the declined record's own `reply` is then asserted to name that envelope.
`tests/e2e/launch_monitor_observer/test_worker_declines_a_manager_ruling_e2e.py` takes the
reference from a real dispatch's prompt instead.

Only the paid model is doubled, at the `oneharness` seam, as every journey under
`tests/ask_seam/` doubles it — and these runs never reach it: their frontier is a human gate,
so nothing is dispatched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import short_state
from planner_channel import BUS_CONFIG, just, next_surface_record
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The wrapper under test, run as an agent runs it.
DECLINE_RULING = REPO_ROOT / "scripts" / "decline-ruling.sh"

#: A launching session these journeys state rather than inherit: this suite runs inside a
#: dispatch whose own harness session would otherwise own the runs.
LAUNCHING_SESSION = "e2e-decline-ruling"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, plus what the
#: enclosing dispatch was given to ask and decline with. The wrapper reads the run out of its
#: environment, so a journey that kept these would decline on the *outer* run's channel.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ORCHESTRATOR_ASK_MANAGER",
    "ORCHESTRATOR_DECLINE_RULING",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The agent node behind the gate, which every note is addressed to. Never dispatched.
WORK_NODE = "work"

#: The manager's ruling, the user's, and the reason an agent gives for declining.
MANAGER_NOTE = "Skip the docs page; the reference table is enough."
USER_NOTE = "Keep the docs page: it is what the operators read."
REASON = "The goal is that operators can follow it, and they read the docs page, not the table."


class Held(NamedTuple):
    """A launched run held at its gate, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: str


def _environment(tmp_path: Path) -> dict[str, str]:
    """The environment one launched run and the declines made on it share."""
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(helper("fake_backend.py"))
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(helper("fake_codex.py"))
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    return environment


@pytest.fixture
def held(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Held]:
    """A launched run whose frontier is a human gate, so its channel outlives the launch.

    `--dag-graph off`, so the monitor raises nothing of its own on the channel these
    journeys read; the launch returns once the gate is reached, and nothing is running.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-48:].strip("-")
    run = f"decline-ruling-{named}"
    environment = _environment(tmp_path)
    plan = tmp_path / "held.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "hold a channel open for an agent to decline a ruling on"},
                "name": run,
                "tasks": [
                    {
                        "id": "gate",
                        "kind": "human",
                        "task": "## What\nApprove.\n\n## Why\nHold the run.\n\n"
                        "## Acceptance criteria\n- Approved.",
                    },
                    {
                        "id": WORK_NODE,
                        "persona": "engineer",
                        "deps": ["gate"],
                        "task": "## What\nReport.\n\n## Why\nNever reached.\n\n"
                        "## Acceptance criteria\n- Reported.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = just(
        "orchestrate", project_from_plan(plan), "--dag-graph", "off", environment=environment
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    try:
        yield Held(environment, run)
    finally:
        just("stop", run, environment=environment, seconds=60)


def _noted(held: Held, text: str, *party: str) -> str:
    """Send one note to the work node through `just channel-reply`, and its ruling reference.

    `party` is the recipe's own `--as` flag where the note is the user's words relayed;
    absent, the note is the manager's own ruling, which is the default.
    """
    envelope = {
        "version": 4,
        "commands": [{"op": "note", "id": WORK_NODE, "addressee": "worker", "text": text}],
    }
    sent = subprocess.run(
        ["just", "channel-reply", held.run, *party],
        cwd=REPO_ROOT,
        env=held.environment,
        input=json.dumps(envelope),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert sent.returncode == 0, sent.stderr + sent.stdout
    # `cast` rather than a validating read: the engine owns the receipt, and each member
    # read here is asserted.
    receipt = cast(dict[str, Any], json.loads(sent.stdout))
    assert receipt.get("state") == "applied" and receipt.get("notes") == {WORK_NODE: "applied"}, (
        receipt
    )
    return f"c{receipt['reply']}.0"


def _declined(
    held: Held,
    *arguments: str,
    stdin: str | None = None,
    cwd: Path,
    run: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real wrapper the way a dispatched agent does: its run in its environment."""
    environment = dict(held.environment)
    if run is not None:
        environment["ONEPIPELINE_RUN_ID"] = run
    return subprocess.run(  # noqa: S603 - the real wrapper, as an agent runs it
        [str(DECLINE_RULING), *arguments],
        cwd=cwd,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def _records(held: Held) -> dict[str, int]:
    """How many records each of the run's channel queues holds, as the bus reports them.

    `onemessagebus status` reads every queue without claiming anything, which is how a
    refusal is shown to have appended nothing rather than only to have said so.
    """
    looked = subprocess.run(
        [
            "onemessagebus",
            "status",
            "--config",
            str(BUS_CONFIG),
            "--transport-dir",
            str(Path(held.environment["ONEPIPELINE_RUNS_DIR"]) / held.run / "channel"),
        ],
        cwd=REPO_ROOT,
        env=held.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert looked.returncode == 0, looked.stderr
    return {queue["queue"]: queue["records"] for queue in json.loads(looked.stdout)}


def _worktree(tmp_path: Path) -> Path:
    """Where a dispatched agent declines from: a directory that is not this checkout."""
    directory = tmp_path / "worktree"
    directory.mkdir()
    return directory


def test_a_declined_manager_note_reaches_the_managers_channel_read_with_its_reason(
    held: Held, tmp_path: Path
) -> None:
    """The agent declines with its reason on stdin; the manager's next read is that surface.

    Run from a directory that is not this checkout, as a lifecycle worker runs it, so the
    wrapper finds the engine from its own location and the run from its environment. What
    the manager reads through `just channel-next` — the verb `onepipeline next` — is one
    non-blocking `declined-ruling` surface whose typed record names the node, the ruling,
    its party and kind, the envelope that carried it, its text, and the reason.
    """
    ruling = _noted(held, MANAGER_NOTE)

    declined = _declined(
        held, "--ruling", ruling, stdin=f"{REASON}\n", cwd=_worktree(tmp_path), run=held.run
    )

    assert declined.returncode == 0, declined.stderr + declined.stdout
    surface = next_surface_record(held.run, held.environment)
    assert surface is not None, "the manager's channel read handed out nothing"
    assert surface["kind"] == "declined-ruling", surface
    assert surface["blocking"] is False, surface
    record = cast(dict[str, Any], surface)["declined_ruling"]
    assert record == {
        "node": WORK_NODE,
        "ruling": ruling,
        "provenance": "manager",
        "kind": "note",
        "reply": int(ruling[1:].split(".")[0]),
        "text": MANAGER_NOTE,
        "reason": REASON,
    }, record
    assert REASON in surface["message"], surface["message"]


def test_the_reason_may_be_a_file_named_after_the_ruling(held: Held, tmp_path: Path) -> None:
    """The verb's other form: the reason in a file, named by a path relative to the agent."""
    ruling = _noted(held, MANAGER_NOTE)
    directory = _worktree(tmp_path)
    (directory / "why.txt").write_text(f"{REASON}\n", encoding="utf-8")

    declined = _declined(held, "--ruling", ruling, "why.txt", cwd=directory, run=held.run)

    assert declined.returncode == 0, declined.stderr + declined.stdout
    surface = next_surface_record(held.run, held.environment)
    assert surface is not None and surface["kind"] == "declined-ruling", surface
    assert cast(dict[str, Any], surface)["declined_ruling"]["reason"] == REASON, surface


def test_a_users_ruling_cannot_be_declined_and_nothing_is_appended(
    held: Held, tmp_path: Path
) -> None:
    """A note relayed `--as user` binds: declining it exits 2, naming why, and adds nothing.

    The manager's note beside it is declinable on the same run, so what decides the refusal
    is the party the ruling was recorded under, never the run or the reference's shape.
    """
    manager = _noted(held, MANAGER_NOTE)
    user = _noted(held, USER_NOTE, "--as", "user")
    before = _records(held)

    refused = _declined(
        held, "--ruling", user, stdin=f"{REASON}\n", cwd=_worktree(tmp_path), run=held.run
    )

    assert refused.returncode == 2, refused.stderr + refused.stdout
    assert f"ruling `{user}` is the user's" in refused.stderr, refused.stderr
    assert "nothing was appended" in refused.stderr, refused.stderr
    assert _records(held) == before, "a refused decline still appended to the run's channel"
    assert next_surface_record(held.run, held.environment) is None

    accepted = _declined(
        held, "--ruling", manager, stdin=f"{REASON}\n", cwd=tmp_path / "worktree", run=held.run
    )
    assert accepted.returncode == 0, accepted.stderr + accepted.stdout


def test_a_process_given_no_run_is_refused_before_anything_is_declined(
    held: Held, tmp_path: Path
) -> None:
    """Outside a dispatch there is no run to name, so the wrapper refuses and appends nothing."""
    ruling = _noted(held, MANAGER_NOTE)
    before = _records(held)

    refused = _declined(held, "--ruling", ruling, stdin=f"{REASON}\n", cwd=_worktree(tmp_path))

    assert refused.returncode == 2, refused.stderr + refused.stdout
    assert "ONEPIPELINE_RUN_ID is not set" in refused.stderr, refused.stderr
    assert _records(held) == before
