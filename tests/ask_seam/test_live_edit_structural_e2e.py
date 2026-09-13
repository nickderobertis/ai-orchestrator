"""A reply resulting in a node `just check-plan` would refuse is refused where it is sent.

`just channel-reply` holds every node an envelope results in — an `add`, a `retry`'s
replacement, a `requeue` with its overrides folded in — to the structural rules the plan
tier applies, over the graph the envelope leaves. Driven here against a live run: a real
`just orchestrate` launch whose agent nodes wait behind a human gate nobody attests, so
nothing is ever dispatched; the `cancel` and `requeue` a manager sends through `just
channel-reply`; and the real `onevcs` answering about a scratch registry whose `library`
really declares a wheel and whose rules really resolve `local-direct` for both
identities. Only the paid provider
is doubled, as `tests/ask_seam/test_channel_reply_e2e.py` doubles it, and no turn of it is
spent: a requeue whose overrides change no task owes none.
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

#: What `just channel-reply` exits with when it refuses.
REPLY_REFUSED = 2


class Live(NamedTuple):
    """A launched run to reply on, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: str
    root: Path


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
    environment["CLAUDE_CODE_SESSION_ID"] = "e2e-live-edit-structural"
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["ONEVCS_HOME"] = str(tmp_path / "registry" / "onevcs")
    # llmlint: ignore-block[live_tier_compiles_and_requires_credential] This journey must
    # prove the deterministic structural check runs before any paid turn. Its explicit
    # no-provider executable makes an attempted credentialed call fail, while the two
    # published CLI seams below supply only the launch metadata needed to reach the reply.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    # llmlint: ignore-end[live_tier_compiles_and_requires_credential]
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
    """A run whose consumer waits on a releasing producer, both behind an unattested gate."""
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
                "goal": {"text": "hold a run open for a manager's requeue"},
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
                    {
                        "id": "consumer",
                        "persona": "engineer",
                        "repo": "service",
                        "title": "feat: adopt the release",
                        "deps": ["producer"],
                        "adoption": "published",
                        "task": _task("Adopt."),
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
        yield Live(environment, run, tmp_path / "runs" / run)
    finally:
        _just("stop", run, environment=environment, seconds=60)


def _reply(live: Live, *commands: dict[str, object]) -> subprocess.CompletedProcess[str]:
    """Send one envelope over the live channel, as a manager types it."""
    envelope = {"version": 2, "author": "planner", "commands": list(commands)}
    return _just(
        "channel-reply", live.run, environment=live.environment, stdin=json.dumps(envelope)
    )


def _committed(live: Live) -> list[object]:
    """Every command this run's journal records as committed to the graph."""
    # llmlint: ignore[tests_mirror_real_usage] No operator view renders an edit's absence.
    journal = live.root / "events.jsonl"
    recorded = []
    for line in journal.read_text(encoding="utf-8").splitlines() if journal.is_file() else []:
        event = json.loads(line) if line.strip() else {}
        payload = event.get("payload") if event.get("kind") == "edit-committed" else None
        if isinstance(payload, dict) and isinstance(payload.get("command"), dict):
            recorded.append(payload["command"])
    return recorded


def test_a_requeue_amending_fast_onto_a_local_direct_node_behind_a_release_is_refused(
    live: Live,
) -> None:
    """The incident, refused with the adoption rule's own message; its correction lands.

    The parked consumer adopts `published`. Folding `adoption: fast` over it returns a node
    publishing `local-direct` behind a dependency whose repository releases, which
    `orchestrator/adoption_guard.py` refuses at the plan tier — so the reply is refused
    here and never reaches the graph. The correction that refusal names, a policy that
    opens a change request, is accepted and committed.
    """
    parked = _reply(live, {"op": "cancel", "id": "consumer", "reason": "park it for the requeue"})
    assert parked.returncode == 0, parked.stdout + parked.stderr

    fast = {"op": "requeue", "id": "consumer", "amend": {"adoption": "fast"}}
    refused = _reply(live, fast)

    assert refused.returncode == REPLY_REFUSED, refused.stdout + refused.stderr
    assert (
        f"this reply to run {live.run} results in a node this host's structural plan rules "
        "refuse — node 'consumer', as this reply leaves it: adoption: this node adopts `fast` "
        "behind a dependency that releases"
    ) in refused.stderr, refused.stderr
    assert "'local-direct'" in refused.stderr, refused.stderr
    assert "State `adoption: published`" in refused.stderr, refused.stderr
    assert "nothing was sent" in refused.stderr, refused.stderr
    assert fast not in _committed(live), "the refused requeue reached the graph"

    corrected = {
        "op": "requeue",
        "id": "consumer",
        "amend": {"adoption": "fast", "merge_policy": "change-open"},
    }
    replacement = {
        "op": "retry",
        "id": "consumer",
        "node": {
            "id": "consumer-again",
            "persona": "engineer",
            "repo": "service",
            "title": "feat: adopt the release",
            "deps": ["producer"],
            "adoption": "fast",
            "task": _task("Adopt."),
        },
    }
    retried = _reply(live, replacement)

    assert retried.returncode == REPLY_REFUSED, retried.stdout + retried.stderr
    assert "node 'consumer-again', as this reply leaves it: adoption:" in retried.stderr, (
        retried.stderr
    )
    assert replacement not in _committed(live), "the refused retry reached the graph"

    # A retry of a node the run does not hold moves nothing in the check, so it is the
    # engine's refusal that answers it — even though its replacement, were it a node,
    # would adopt `fast` behind the releasing producer exactly as the one above did.
    absent = {
        "op": "retry",
        "id": "no-such-node",
        "node": {
            "id": "ghost",
            "repo": "service",
            "title": "feat: record the follow-up",
            "deps": ["producer"],
            "adoption": "fast",
            "expects_no_diff": True,
            "task": "Report.",
        },
    }
    unknown = _reply(live, absent)

    assert unknown.returncode == REPLY_REFUSED, unknown.stdout + unknown.stderr
    assert "structural plan rules refuse" not in unknown.stderr, unknown.stderr
    assert "retry: no node 'no-such-node'" in unknown.stderr, unknown.stderr

    landed = _reply(live, corrected)

    assert landed.returncode == 0, landed.stdout + landed.stderr
    assert corrected in _committed(live), "the corrected requeue did not land"


def test_an_added_node_reparented_off_the_release_in_the_same_reply_is_accepted(
    live: Live,
) -> None:
    """The rules are asked over the graph the whole envelope leaves, `reparent` included.

    An added node adopting `fast` behind the releasing producer, publishing `local-direct`,
    is refused on its own. "Drop the edge if the work does not need the release" is that
    refusal's third remedy, and a `reparent` of the same unstarted node onto the gate in
    the same envelope takes it: the node then waits on nothing that releases, so the reply
    is accepted and the engine commits both commands as sent. The node declares
    `expects_no_diff`, so nothing would dispatch from it and no prose tier reads it; the
    structural rules still hold it, as they do at the plan tier.
    """
    added = {
        "op": "add",
        "node": {
            "id": "follow-up",
            "repo": "service",
            "title": "feat: record the follow-up",
            "deps": ["producer"],
            "adoption": "fast",
            "expects_no_diff": True,
            "task": "Report.",
        },
    }
    alone = _reply(live, added)

    assert alone.returncode == REPLY_REFUSED, alone.stdout + alone.stderr
    assert "node 'follow-up', as this reply leaves it: adoption:" in alone.stderr, alone.stderr
    assert added not in _committed(live), "the refused add reached the graph"

    reparented = {"op": "reparent", "id": "follow-up", "deps": ["gate"]}
    landed = _reply(live, added, reparented)

    assert landed.returncode == 0, landed.stdout + landed.stderr
    committed = _committed(live)
    assert added in committed and reparented in committed, committed


def test_a_requeue_whose_release_edge_the_same_reply_drops_is_accepted(live: Live) -> None:
    """A `drop` detaching the releasing producer, then the `fast` requeue, lands whole.

    Folded in order, the drop leaves the consumer depending on nothing, so the requeue the
    first journey saw refused on its own returns a node no structural rule refuses.
    """
    parked = _reply(live, {"op": "cancel", "id": "consumer", "reason": "park it for the requeue"})
    assert parked.returncode == 0, parked.stdout + parked.stderr
    dropped = {"op": "drop", "id": "producer", "dependents": "detach"}
    fast = {"op": "requeue", "id": "consumer", "amend": {"adoption": "fast"}}

    landed = _reply(live, dropped, fast)

    assert landed.returncode == 0, landed.stdout + landed.stderr
    committed = _committed(live)
    assert dropped in committed and fast in committed, committed
