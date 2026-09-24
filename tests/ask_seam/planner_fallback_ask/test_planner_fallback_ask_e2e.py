"""The planner persona's fallback puts the shim's question in front of the manager.

`personas/planner.yaml` becomes every planner's own system prompt, and it states what to
do when a launch exported no `ORCHESTRATOR_ASK_MANAGER`: make the ask the shim would have
made. That is one invocation written twice — once as prose a model reads, once as
`scripts/ask-manager.sh` — and one of the two copies travels into whatever repository is
being planned against, where nothing in this checkout can be cited. A planner that asks
the wrong way asks nobody, and a blocking question produces no other signal.

`tests/test_planner_seam_contracts.py` holds the two to one invocation option for option,
`--config` included: the shim opens the file every launch hands the engine, and the
fallback opens the parse of that same file which the engine recorded for its run. What an
argv comparison cannot answer is whether either invocation is one the published bus takes
at all, and whether the question then reaches the manager. So each side asks here on a run
`just orchestrate` really launched — reading its policy out of the launch record the
engine really wrote — and what `just channel-next` hands the manager is compared.

The launch's frontier is a human gate, so nothing is dispatched and the channel outlives
the launch. Only the paid provider is doubled.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, TypedDict

import pytest
import short_state
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from planner_channel import Surface, just, next_surface_record
from planner_fallback import PLACEHOLDER_QUESTION, shortened_fallback
from project_fixtures import helper, project_from_plan
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The two statements of one ask.
PLANNER_PERSONA = REPO_ROOT / "personas" / "planner.yaml"
ASK_SCRIPT = REPO_ROOT / "scripts" / "ask-manager.sh"

#: The stand-in for the paid model, and the provider binary beneath it. This journey never
#: reaches either — a human gate dispatches nothing — and both are named for the reason the
#: journeys beside it name them: a launch that came to dispatch would spend real quota.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: A launching session this journey states rather than inherits: this suite runs inside a
#: dispatch whose own harness session would otherwise own the run.
LAUNCHING_SESSION = "e2e-planner-fallback"

#: Every name that would otherwise answer for a side. This suite runs inside a dispatch
#: whose own run, reply window, asker and node the shim reads, and whose runs root the
#: fallback would compose a channel and a launch record from.
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_RUNS_DIR",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ORCHESTRATOR_ASK_MANAGER",
    "ONEMESSAGEBUS_CONFIG",
    "ONEMESSAGEBUS_TRANSPORT_DIR",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The run this journey launches, and the node that holds it open.
RUN = "planner-fallback-ask"
GATE_NODE = "gate"

#: How long each side waits for the reply nobody sends. Waiting it out is how the question
#: comes to rest on the channel with nobody attending it, which is the state a manager then
#: reads; short, because nothing else about this journey is slow.
WINDOW_SECONDS = 5

#: How the bus reports what it stamped on a question. Read off each ask's own answer, so
#: the surface a side is compared on is the one that side raised and no other.
ANSWER_CORRELATION = "correlation"

#: How long the manager's read is given to reach one side's question. Load-scaled, and
#: generous because every `just channel-next` in that loop is a real recipe: the run raises
#: surfaces of its own — its human gate among them — and each is read past on the way.
SURFACE_PATIENCE_SECONDS = 120


class Launched(NamedTuple):
    """A live run to ask on, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: str


class Question(TypedDict):
    """A planner's question as a manager is handed it: the text, and that it blocks."""

    message: str
    blocking: bool


def _environment(tmp_path: Path) -> dict[str, str]:
    """The environment the launch and both asks share, with the host's own state out.

    `HOME` is sandboxed because host state under the real one — an operator's plan-store
    secrets, a Claude or codex configuration directory — otherwise reaches a launch this
    journey makes, and a journey about this checkout would then be answering about that.
    """
    environment = {name: value for name, value in os.environ.items() if name not in INHERITED}
    home = tmp_path / "home"
    home.mkdir()
    environment["HOME"] = str(home)
    # uv resolves this project's environment on every `uv run` and caches it under the real
    # `HOME`. Re-resolving per journey costs minutes and a network, neither of which this
    # journey is about.
    environment["UV_CACHE_DIR"] = os.environ.get("UV_CACHE_DIR") or str(
        Path.home() / ".cache" / "uv"
    )
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    return environment


#: This module launches a real run through `just`, which reaches its tools through `uv
#: run` and so waits on the exclusive lock a journey re-provisioning this checkout holds.
#: `tests/e2e/nx_workspace.py`'s group, for the reason `tests/test_nx_cache_scope.py`
#: records: `--dist loadgroup` co-locates only tests sharing one name.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)


@pytest.fixture
def launched(tmp_path: Path) -> Iterator[Launched]:
    """One real run whose frontier is a human gate, so its channel outlives the launch.

    `--dag-graph off` deliberately: with this host's observer graph attached, the monitor
    and the pacemaker raise surfaces of their own on the same channel, and the two this
    journey compares would be read out of theirs.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _environment(tmp_path)
    plan = tmp_path / "fallback.plan.json"
    # llmlint: ignore-block[modern_domain_modeling] A plan is a JSON document handed to
    # `project_from_plan` as text, and every journey in this project writes one the same
    # way — see `tests/ask_seam/ask_manager/test_ask_manager_e2e.py`. A model of the
    # engine's plan schema declared here would be this repository's second statement of a schema the
    # engine owns, which is the drift these gates exist to prevent.
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "hold a channel open for a planner's question"},
                "name": RUN,
                "tasks": [
                    {
                        "id": GATE_NODE,
                        "kind": "human",
                        "task": "## What\nApprove.\n\n## Why\nHold the run.\n\n"
                        "## Acceptance criteria\n- Approved.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    # llmlint: ignore-end[modern_domain_modeling]

    launch = just(
        "orchestrate", project_from_plan(plan), "--dag-graph", "off", environment=environment
    )

    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    root = Path(environment["ONEPIPELINE_RUNS_DIR"]) / RUN
    assert root.is_dir(), (
        f"the launch created no run named {RUN}, so neither side below would be asking on "
        f"a channel anybody launched:\n{launch.stdout}\n{launch.stderr}"
    )
    assert (root / "launch.json").is_file(), (
        f"run {RUN} has no launch record, so the fallback has no policy of its own to read; "
        "tests/ask_seam/launch/test_launch_ask_seam_e2e.py is what holds what that record "
        "carries"
    )
    try:
        yield Launched(environment, RUN)
    finally:
        just("stop", RUN, environment=environment, seconds=60)


def _asked(command: list[str], launched: Launched, scratch: Path) -> Question:
    """Ask on the live run with nobody replying, then read the surface it raised.

    The ask ends in the bus's named `timeout` — asserted, because an invocation the bus
    refused would raise no surface at all and leave this comparing nothing. The question is
    then read through `just channel-next`, the manager's own verb, and matched by the
    correlation the bus reported to this ask, so a side is never compared on a surface some
    other side or the run itself raised.
    """
    scratch.mkdir(parents=True)
    asked = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env={
            **launched.environment,
            "ONEPIPELINE_RUN_ID": launched.run,
            "ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch),
        },
        capture_output=True,
        text=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    answer = json.loads(asked.stdout)
    assert answer["answer"] == "timeout", (
        f"{command} did not reach the bus and wait for an answer: {asked.stdout}{asked.stderr}"
    )
    correlation = answer[ANSWER_CORRELATION]

    read: list[Surface] = []
    until = deadline(SURFACE_PATIENCE_SECONDS)
    while time.monotonic() < until:
        surface = next_surface_record(launched.run, launched.environment)
        if surface is None:
            continue
        read.append(surface)
        if surface.get(ANSWER_CORRELATION) == correlation:
            return Question(message=surface["message"], blocking=surface["blocking"])
    raise AssertionError(
        f"the question {command} asked (correlation {correlation}) never reached the "
        f"manager's own read of run {launched.run}; it handed out {read}"
    )


def test_the_personas_fallback_lands_the_question_the_shim_lands(
    launched: Launched, tmp_path: Path
) -> None:
    """Two ways of asking, one question in front of the manager.

    Each side asks on the run just launched — the fallback reading its bus configuration
    out of that run's own launch record, the shim opening the file the launch handed the
    engine — and what `just channel-next` hands the manager is compared. Two equal
    surfaces are two asks a manager cannot tell apart, which is what the fallback promises
    a planner whose launch handed it no shim.
    """
    bash = shutil.which("bash")
    assert bash is not None, "bash is not on this host's PATH"

    persona = _asked(
        [bash, "-c", shortened_fallback(PLANNER_PERSONA, WINDOW_SECONDS)],
        launched,
        tmp_path / "persona-scratch",
    )
    shim = _asked(
        [bash, str(ASK_SCRIPT), "--timeout", str(WINDOW_SECONDS), PLACEHOLDER_QUESTION],
        launched,
        tmp_path / "shim-scratch",
    )

    assert persona == shim, (
        f"{PLANNER_PERSONA.name}'s fallback puts {persona} in front of a manager where "
        f"{ASK_SCRIPT.name} puts {shim}; the two ask different questions"
    )
    assert persona == Question(message=PLACEHOLDER_QUESTION, blocking=True), (
        f"neither side asks the blocking question a manager answers: {persona}"
    )


def test_the_personas_fallback_names_no_retired_request_path() -> None:
    """The retired channel-serve request path is gone from the persona for good.

    The comparisons elsewhere would pass a persona that stated the supported way to ask
    *and* the retired one beside it, which is what a planner would then have to choose
    between. `onepipeline channel serve` was the request path the bus replaced, and a
    planner piping a frame into it asks nobody.
    """
    stated = PLANNER_PERSONA.read_text(encoding="utf-8")

    assert "channel serve" not in stated, (
        f"{PLANNER_PERSONA.name} still tells a planner to ask over `onepipeline channel "
        "serve`, which is the request path `onemessagebus ask` replaced"
    )
