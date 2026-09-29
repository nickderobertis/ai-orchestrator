"""The contracts the manager/planner seam restates are reconciled with their source.

A copy of a contract owned somewhere else is only sound while something reconciles it.
`scripts/plan-brief.sh`'s copy of the plan-task layout is reconciled in
`tests/plan_tooling/test_task_templates.py`, where the template is rendered. Two copies
are held here. The engine's `onepipeline ask`, which `scripts/ask-manager.sh` runs,
refuses without the run it asks on, and the journeys that prove a launch builds that hold
their own list of the names they measure. And the persona's fallback ask is a statement
of the engine's channel layout a planner runs when its launch exported no adapter.

Every copy fails quietly if it drifts, which is why they are gated here rather than
reviewed. An input the ask starts requiring that no journey checks for is a launch path
free to stop providing it — which is exactly how a whole launch path came to export the
seam nowhere at all, unnoticed for every run this host had ever driven.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from planner_fallback import PLACEHOLDER_QUESTION, fallback_snippet

from orchestrator.root import REPO_ROOT

#: The planner persona, which states the fallback ask a planner runs when its launch
#: exported no adapter.
TASK_TEMPLATE = REPO_ROOT / "personas" / "planner.yaml"

#: The adapter a dispatched agent asks its manager through, and the one input the verb it
#: runs refuses to ask without: the run whose channel the question goes on.
ASK_SCRIPT = REPO_ROOT / "scripts" / "ask-manager.sh"
REQUIRED_ASK_INPUT = "ONEPIPELINE_RUN_ID"

#: A stand-in for the bus that answers nothing and records everything: the arguments it
#: was handed, one per line, and the frame it was handed on stdin.
BUS_RECORDER = (
    "#!/bin/sh\n"
    ': >"$RECORD_ARGV"\n'
    'for argument in "$@"; do printf \'%s\\n\' "$argument" >>"$RECORD_ARGV"; done\n'
    'cat >"$RECORD_FRAME"\n'
)

#: The environment name that changes how a question is asked, cleared before the
#: fallback runs so a dispatch running this suite cannot hand it an asker.
ASK_OVERRIDES = ("ONEPIPELINE_CHANNEL_ASKER",)

#: What the engine's `ask` raises, which the fallback has to raise too: one blocking
#: frame of this kind and source on this queue of the run's channel directory.
QUESTION_QUEUE = "surfaces"
QUESTION_KIND = "planner-question"
QUESTION_SOURCE = "proposal"

#: The run the fallback is probed on, and the record the engine writes for it. Nothing is
#: launched and the bus never runs here, so the recorded policy is a one-line stand-in
#: rather than a configuration: what this gate asks is that the fallback passes whatever
#: its run's record holds, and a document no policy could be is what makes a fallback
#: composing its own — or reading another file — visible rather than plausible.
ASK_PROBE_RUN = "seam-probe-run"
LAUNCH_RECORD = "launch.json"
RECORDED_POLICY = {"seam-probe": "the messaging policy this run's launch record carries"}


#: The journeys that measure what each launch shape hands a dispatch, and the shape
#: their list of required inputs is written in. Read textually rather than imported: it
#: is a pytest module whose import would collect fixtures, and reading a declaration is
#: what every other gate in this file does.
LAUNCH_JOURNEYS = REPO_ROOT / "tests" / "ask_seam" / "launch" / "test_launch_ask_seam_e2e.py"
CHECKED_INPUT = re.compile(r'Input\(\s*"([A-Z0-9_]+)"')


def test_every_input_the_wrapper_requires_is_one_a_launch_is_measured_for(
    tmp_path: Path,
) -> None:
    """A launch is proven to build what the ask refuses without.

    The installed verb, through the adapter, is asked with that input unset and has to
    refuse naming it — so a release that stopped requiring it, or renamed it, fails here —
    and the launch journeys have to measure it in a real dispatch's environment. A required
    input nothing measures is a launch path free to drop it, and the failure lands on some
    agent's first blocking question rather than in the suite.
    """
    environment = {name: value for name, value in os.environ.items() if name != REQUIRED_ASK_INPUT}
    refused = subprocess.run(
        [str(ASK_SCRIPT), "--timeout", "1", "Which way?"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert REQUIRED_ASK_INPUT in refused.stderr, refused.stderr

    measured = set(CHECKED_INPUT.findall(LAUNCH_JOURNEYS.read_text(encoding="utf-8")))

    assert REQUIRED_ASK_INPUT in measured, (
        f"the ask requires {REQUIRED_ASK_INPUT}, and {LAUNCH_JOURNEYS.name} measures no "
        "launch shape for it"
    )


def _asked(command: list[str], directory: Path, runs_dir: str | None) -> tuple[list[str], str]:
    """Run the fallback against a recording stand-in for the bus, and read what it asked with.

    The stand-in shadows any real `onemessagebus` on PATH — where the persona's fallback,
    which travels into any repository, finds its bus; it answers nothing, so what is
    measured is the invocation the fallback composes rather than a reply. It runs in a
    working directory of its own with the run's record already written under it, so a
    `runs_dir` left unset exercises the `runs` default without writing into this checkout.
    """
    scratch = directory / "scratch"
    scratch.mkdir(parents=True)
    recorder = directory / "onemessagebus"
    recorder.write_text(BUS_RECORDER, encoding="utf-8")
    recorder.chmod(0o755)
    argv_at, frame_at = directory / "argv", directory / "frame"
    root = Path(runs_dir) if runs_dir is not None else directory / "runs"
    (root / ASK_PROBE_RUN).mkdir(parents=True, exist_ok=True)
    (root / ASK_PROBE_RUN / LAUNCH_RECORD).write_text(
        json.dumps({"run_id": ASK_PROBE_RUN, "bus_config": RECORDED_POLICY}), encoding="utf-8"
    )
    environment = {
        **{name: value for name, value in os.environ.items() if name not in ASK_OVERRIDES},
        "PATH": f"{directory}{os.pathsep}{os.environ['PATH']}",
        "ONEPIPELINE_RUN_ID": ASK_PROBE_RUN,
        "ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch),
        "RECORD_ARGV": str(argv_at),
        "RECORD_FRAME": str(frame_at),
    }
    if runs_dir is None:
        environment.pop("ONEPIPELINE_RUNS_DIR", None)
    else:
        environment["ONEPIPELINE_RUNS_DIR"] = runs_dir

    asked = subprocess.run(
        command,
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert asked.returncode == 0, f"{command} exited {asked.returncode}: {asked.stderr}"
    return argv_at.read_text(encoding="utf-8").splitlines(), frame_at.read_text(encoding="utf-8")


def _invocation(argv: list[str]) -> tuple[tuple[str, ...], dict[str, str]]:
    """One side's argv as its positional words and its options, so flag order is free.

    The bus does not care in what order it is handed `--transport-dir` and `--timeout`,
    so neither does this comparison; what it holds is which queue is asked, which
    options are passed, and what each one is passed.
    """
    positional: list[str] = []
    options: dict[str, str] = {}
    remaining = list(argv)
    while remaining:
        word = remaining.pop(0)
        if not word.startswith("--"):
            positional.append(word)
        elif word == "--blocking":
            options[word] = ""
        else:
            assert remaining, f"{word} was passed with no value: {argv}"
            options[word] = remaining.pop(0)
    return tuple(positional), options


@pytest.mark.parametrize("runs_dir", [None, "/tmp/elsewhere/runs"])
def test_the_personas_fallback_asks_as_the_engine_does(
    tmp_path: Path, runs_dir: str | None
) -> None:
    """A planner whose launch exported no adapter raises the question the engine would.

    `personas/planner.yaml` becomes every planner's own system prompt and travels into
    whatever repository is being planned against, and a blocking question that reaches
    nobody produces no other signal. So its fallback is run against a recording stand-in
    for the bus and held to the engine's `ask`: one blocking `planner-question` frame of
    source `proposal` on the `surfaces` queue of the run's channel directory — under a runs
    root that is set and one that is not, because the derivation carries a default — under
    the policy the engine recorded for the run, written here by hand so a fallback reading
    anything else could not pass by coincidence.
    `tests/ask_seam/planner_fallback_ask/test_planner_fallback_ask_e2e.py` asks with the
    fallback and with the adapter on a run `just orchestrate` really launched and compares
    what the manager is handed.
    """
    snippet = fallback_snippet(TASK_TEMPLATE)
    bash = shutil.which("bash")
    assert bash is not None, "bash is not on this host's PATH"

    argv, frame = _asked([bash, "-c", snippet], tmp_path / "persona", runs_dir=runs_dir)

    positional, options = _invocation(argv)
    assert positional == ("ask", QUESTION_QUEUE), (
        f"{TASK_TEMPLATE.name}'s fallback asks `{' '.join(positional)}`"
    )
    assert "--blocking" in options, options
    root = Path(runs_dir) if runs_dir is not None else Path("runs")
    assert options.get("--transport-dir") == str(root / ASK_PROBE_RUN / "channel"), options
    asked_under = Path(options.get("--config", ""))
    if not asked_under.is_absolute():
        asked_under = tmp_path / "persona" / asked_under
    assert asked_under.is_file(), (
        f"{TASK_TEMPLATE.name}'s fallback asks under {asked_under}, which it did not write; "
        "a planner running it would ask under the bus's defaults rather than this run's policy"
    )
    assert json.loads(asked_under.read_text(encoding="utf-8")) == RECORDED_POLICY, (
        f"{TASK_TEMPLATE.name}'s fallback asks under "
        f"{asked_under.read_text(encoding='utf-8')!r}, not the policy the engine recorded "
        f"for this run ({json.dumps(RECORDED_POLICY)})"
    )
    assert json.loads(frame) == {
        "kind": QUESTION_KIND,
        "message": PLACEHOLDER_QUESTION,
        "source": QUESTION_SOURCE,
    }, f"{TASK_TEMPLATE.name}'s fallback composes {frame!r}"


def test_the_personas_fallback_names_no_retired_request_path() -> None:
    """The retired channel-serve request path is gone from the fallback for good.

    The gate above compares two invocations and would pass a persona that stated the
    supported one *and* the retired one beside it, which is what a reader would then
    have to choose between. `onepipeline channel serve` was the request path the bus
    replaced, and a planner piping a frame into it now asks nobody.
    """
    stated = TASK_TEMPLATE.read_text(encoding="utf-8")

    assert "channel serve" not in stated, (
        f"{TASK_TEMPLATE.name} still tells a planner to ask over `onepipeline channel "
        "serve`, which is the request path `onemessagebus ask` replaced"
    )
