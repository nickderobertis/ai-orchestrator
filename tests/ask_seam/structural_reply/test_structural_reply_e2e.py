"""A reply stating a node `just check-plan` would refuse is refused where it is sent.

`config/onemessagebus.yaml` names `orchestrator/envelope_review.py` as the validator on the
planner channel's `replies` queue, and that validator holds every node an envelope states —
an `add`, a `retry`'s replacement — to the structural rules the plan tier applies, over the
graph the envelope's own nodes form. Driven here against a live run: a real `just
orchestrate` launch whose agent nodes wait behind a human gate nobody attests, so nothing is
ever dispatched; the envelope a manager sends through `just channel-reply`, which the bus
judges before appending anything to the run's `commands` queue; and the real `onevcs`
answering about a scratch registry whose `library` really declares a wheel and whose rules
really resolve `local-direct` for both identities. Only the paid provider is doubled, as
`tests/ask_seam/channel_reply/test_channel_reply_e2e.py` doubles it, and no turn of it is
spent: every node here declares `expects_no_diff`, so no prose tier reads it, and the
structural rules still hold it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from planner_channel import BUS_CONFIG
from project_fixtures import helper, project_from_plan
from scratch_identity import registered
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: Every journey here spends a real launch, and every step is a `just` recipe blocking
#: on `uv run`; `tests/e2e/nx_workspace.py` says why that shares one worker.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The stand-ins for the paid model, exactly as the channel-reply journeys use them.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The launcher and dispatch identities the enclosing dispatch would otherwise hand in.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The wheel this host installs, which the scratch producer declares.
WHEEL = ("pypi", "pypi:onepipeline-cli")

#: What `just channel-reply` exits with when the bus's validator refuses an envelope.
REPLY_REFUSED = 1


class Live(NamedTuple):
    """A launched run to reply on, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: str
    channel: Path


def _declaring(*declared: tuple[str, str]) -> str:
    """A `release-targets.toml` declaring each ``(name, id)`` target and nothing a probe runs."""
    rows = "".join(
        f'\n[[target]]\nid = "{artifact}"\nname = "{name}"\n'
        f'what = "The {name} target, as a scratch producer declares it."\n'
        f"published_by = \"Nothing: a journey's stand-in for a producer's declaration.\"\n"
        for name, artifact in declared
    )
    return f'schema_version = 3\nprobe = "scripts/release-probe.sh"\n{rows}'


def _task(what: str) -> str:
    return f"## What\n{what}\n\n## Why\nHold the run.\n\n## Acceptance criteria\n- Done."


def _environment(tmp_path: Path) -> dict[str, str]:
    """A sandboxed launch environment reading the scratch registry and a scratch ledger."""
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment.pop("VIRTUAL_ENV", None)
    environment["CLAUDE_CODE_SESSION_ID"] = "e2e-structural-reply"
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["ONEVCS_HOME"] = str(tmp_path / "registry" / "onevcs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(tmp_path / "review-launches")
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


def _just(
    *arguments: str, environment: dict[str, str], stdin: str | None = None, seconds: float = 180
) -> subprocess.CompletedProcess[str]:
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


@pytest.fixture
def live(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Live]:
    """A run holding a producer in the releasing `library`, behind an unattested gate."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    registered(
        tmp_path / "registry", ["library", "service"], declarations={"library": _declaring(WHEEL)}
    )
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-24:].strip("-")
    run = f"structural-{os.getpid()}-{named}"
    plan = tmp_path / f"{run}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "hold a run open for a manager's live edit"},
                "name": run,
                "tasks": [
                    {"id": "gate", "kind": "human", "task": _task("Approve.")},
                    {
                        "id": "producer",
                        "persona": "engineer",
                        "repo": "library",
                        "title": "feat: release the library",
                        "deps": ["gate"],
                        "task": _task("Release."),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    environment = _environment(tmp_path)
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
        yield Live(environment, run, tmp_path / "runs" / run / "channel")
    finally:
        _just("stop", run, environment=environment, seconds=60)


def _reply(live: Live, *commands: dict[str, object]) -> subprocess.CompletedProcess[str]:
    """Send one envelope over the live channel, as a manager types it."""
    envelope = {"version": 2, "author": "planner", "commands": list(commands)}
    return _just(
        "channel-reply", live.run, environment=live.environment, stdin=json.dumps(envelope)
    )


def _queued(live: Live) -> int:
    """How many command envelopes the run's `commands` queue holds, per the bus's `status`."""
    status = subprocess.run(
        [
            "onemessagebus",
            "status",
            "commands",
            "--config",
            str(BUS_CONFIG),
            "--transport-dir",
            str(live.channel),
        ],
        cwd=REPO_ROOT,
        env=live.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert status.returncode == 0, status.stderr
    (queue,) = json.loads(status.stdout)
    return int(queue["records"])


#: A second producer landing in the releasing repository, stated by the envelope itself.
RELEASING = {
    "id": "library-2",
    "repo": "library",
    "title": "feat: release the library again",
    "deps": ["gate"],
    "expects_no_diff": True,
    "task": "Release.",
}

#: A consumer publishing `local-direct` in `service`, adopting `fast` behind that producer.
ADOPTING = {
    "id": "follow-up",
    "repo": "service",
    "title": "feat: record the follow-up",
    "deps": ["library-2"],
    "adoption": "fast",
    "expects_no_diff": True,
    "task": "Report.",
}


#: The two corrections the refusal names that drop the release edge inside the envelope.
REMEDIES: tuple[dict[str, object], ...] = (
    {"op": "reparent", "id": "follow-up", "deps": ["gate"]},
    {"op": "drop", "id": "library-2", "dependents": "detach"},
)


def test_a_node_behind_a_release_the_same_reply_states_is_refused_until_its_edge_goes(
    live: Live,
) -> None:
    """Refused in the adoption rule's own words; each correction it names is accepted.

    The envelope adds a node landing in the releasing `library` and a node behind it that
    adopts `fast` while publishing `local-direct` — the shape
    `orchestrator/adoption_guard.py` refuses at the plan tier. The bus's validator refuses
    it here, naming the node and the field, and nothing reaches the run's `commands` queue
    and no judged turn is spent. "Drop the edge if the work does not need the release" is
    the refusal's own remedy: the same envelope with a `reparent` of that unstarted node
    onto the gate, or with a detaching `drop` of the producer, leaves a graph no rule
    refuses, and each is accepted onto the queue as sent.
    """
    before = _queued(live)

    refused = _reply(live, {"op": "add", "node": RELEASING}, {"op": "add", "node": ADOPTING})

    assert refused.returncode == REPLY_REFUSED, refused.stdout + refused.stderr
    assert (
        "this reply states a node this host's structural plan rules refuse — node "
        "'follow-up', as this reply states it: adoption: this node adopts `fast` behind a "
        "dependency that releases"
    ) in refused.stderr, refused.stderr
    assert "'local-direct'" in refused.stderr, refused.stderr
    assert "nothing was sent" in refused.stderr, refused.stderr
    assert _queued(live) == before, "the refused envelope reached the run's command queue"
    attempts = Path(live.environment["FAKE_CODEX_ATTEMPT_LOG"])
    assert not attempts.exists(), "a structural refusal spent a judged turn"

    for remedy in REMEDIES:
        landed = _reply(
            live, {"op": "add", "node": RELEASING}, {"op": "add", "node": ADOPTING}, remedy
        )

        assert landed.returncode == 0, f"{remedy['op']}: {landed.stdout}{landed.stderr}"
        assert json.loads(landed.stdout)["queue"] == "commands", landed.stdout

    assert _queued(live) == before + 2
