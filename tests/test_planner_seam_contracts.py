"""The contracts the manager/planner seam restates are reconciled with their source.

`scripts/plan-brief.sh` and `scripts/ask-manager.sh` are shell, and shell cannot import.
So each of them holds a copy of a contract that is owned somewhere else — the task
template `personas/planner.yaml` states — and a copy is only sound while something
reconciles it. The second copy runs the other way: the ask shim declares what its
environment must carry, and the journeys that prove a launch builds it hold their own
list of those names.

Every copy fails quietly if it drifts, which is why they are gated here rather than
reviewed. A `PLAN_REQUIRED_SECTIONS` that no longer matches the template lets a brief
through that is not a task, or refuses one that is. And an input the shim starts
requiring that no journey checks for is a launch path free to stop providing it — which
is exactly how a whole launch path came to export the seam nowhere at all, unnoticed for
every run this host had ever driven. The shim's run-id rule once had a third copy to
match, the grammar the bus's retired `onejudge` codec read a run out of a frame with; the
judge side now reads no run at all, so the rule stands on its own.
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

#: The grammar both planning entry points read a manager's brief through, and the prose
#: that says what a task is written in. `just plan` and `just finish-plan` are two
#: launches of one flow and each is given the same brief, so what a brief has to be is
#: stated once, in the helper they both source — a second copy in either script is a
#: launch path that could accept a brief the other refuses. That template is the
#: **planner's** half of the decomposition doctrine, so it lives in the persona that
#: travels with the dispatch rather than in `AGENTS.md`, which stays in this checkout;
#: `tests/test_decomposition_guidance.py` is what keeps it in exactly one of them.
PLAN_SCRIPT = REPO_ROOT / "scripts" / "plan-brief.sh"
TASK_TEMPLATE = REPO_ROOT / "personas" / "planner.yaml"

#: The shim a dispatched agent asks its manager through, which composes the run's channel
#: directory from the run id before handing the question to `onemessagebus ask`.
ASK_SCRIPT = REPO_ROOT / "scripts" / "ask-manager.sh"

#: A stand-in for the bus that answers nothing and records everything: the arguments it
#: was handed, one per line, and the frame it was handed on stdin. Both sides exec the
#: same one, so what is compared is what each would really have asked with.
BUS_RECORDER = (
    "#!/bin/sh\n"
    ': >"$RECORD_ARGV"\n'
    'for argument in "$@"; do printf \'%s\\n\' "$argument" >>"$RECORD_ARGV"; done\n'
    'cat >"$RECORD_FRAME"\n'
)

#: The environment names that change how the shim asks. Cleared before each side runs, so
#: a dispatch running this suite cannot hand the shim an asker, a node or a reply window
#: the persona's block has no way to state.
ASK_OVERRIDES = (
    "ONEPIPELINE_CHANNEL_ASKER",
    "ORCHESTRATOR_ASK_MANAGER_NODE",
    "ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS",
)

#: The bus configuration every launch hands the engine, read out of the launch wrapper
#: rather than restated: `--bus-config "${script_dir%/scripts}/<path>"`, resolved against
#: this checkout. It is the file the shim opens, and the file whose parse the engine then
#: records for the run, which is how the two sides reach one policy by different routes.
LAUNCH_WRAPPER = REPO_ROOT / "scripts" / "onepipeline.sh"
LAUNCH_BUS_CONFIG = re.compile(r'--bus-config "\$\{script_dir%/scripts\}/(?P<path>[^"]+)"')

#: The run both sides are probed on, and the record the engine writes for it. Nothing is
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

#: How `scripts/ask-manager.sh` declares an environment variable it cannot ask without,
#: in the `Environment:` block of its own header. The `(optional)` ones are deliberately
#: not matched: a launch that provides none of them is still a launch an agent can ask
#: from.
REQUIRED_INPUT = re.compile(r"^#\s+([A-Z0-9_]+)\s+\(required\)", re.MULTILINE)

#: `PLAN_REQUIRED_SECTIONS=("## What" "## Why" "## Acceptance criteria")`, read out of
#: the helper rather than restated, so this gate compares the shell's own list.
DECLARED_SECTIONS = re.compile(r"PLAN_REQUIRED_SECTIONS=\(([^)]*)\)")

#: How `personas/planner.yaml` names the same three, in the list that requires them of
#: every task: "Write every node's task — and every step's task — with these headings,
#: in this order:", followed by one bullet per heading. Deliberately stopping at
#: `## Additional info`: the same list states that fourth heading and states it as
#: conditional, so it is not one a brief can be refused for lacking. Matched as code
#: spans, so the prose around them can be reworded without this gate caring, and
#: matched against flattened prose, because the persona is a hard-wrapped YAML block
#: scalar and every one of these phrases is longer than the line it sits on.
TEMPLATE_LIST = re.compile(
    r"Write every node's task.*?in this order:(?P<named>.*?)`## Additional info`"
)
SPAN = re.compile(r"`(##[^`]+)`")


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a wrapped phrase reads as one line."""
    return " ".join(prose.split())


def test_the_brief_template_the_plan_recipe_requires_is_the_one_the_doctrine_states() -> None:
    """`just plan` refuses exactly the sections a task is required to have.

    The brief a manager writes IS the dispatched task, so the recipe checks it against
    the template every task here is written in — and holds the copy of that template in
    shell, where nothing can import the prose that owns it. Drift in either direction
    is silent and costly: a section dropped here lets a brief through that dispatches a
    planner with no review bar, and one added here refuses a brief that is a perfectly
    good task — which is why `## Additional info`, stated in the same list but stated as
    conditional, is on neither side of this comparison.
    """
    declared = DECLARED_SECTIONS.search(PLAN_SCRIPT.read_text(encoding="utf-8"))
    assert declared is not None, f"{PLAN_SCRIPT.name} declares no PLAN_REQUIRED_SECTIONS"
    required = tuple(re.findall(r'"([^"]+)"', declared.group(1)))
    assert required, f"{PLAN_SCRIPT.name} requires no section at all"

    listed = TEMPLATE_LIST.search(_flat(TASK_TEMPLATE.read_text(encoding="utf-8")))
    assert listed is not None, (
        f"{TASK_TEMPLATE.name} no longer states the task template in the list this gate "
        "reads it from; update the pattern here together with that list"
    )
    stated = tuple(SPAN.findall(listed.group("named")))

    assert required == stated, (
        f"{PLAN_SCRIPT.name} requires {list(required)} of a brief, but "
        f"{TASK_TEMPLATE.name} states a task is written with {list(stated)}"
    )


def test_every_input_the_wrapper_requires_is_one_a_launch_is_measured_for() -> None:
    """A launch is proven to build exactly what the shim refuses without.

    The shim's header names each variable it cannot ask without, and the journeys name
    each one they read out of a real dispatch's environment. Only one direction is an
    error: a required input nothing measures is a launch path free to drop it, and the
    failure lands on some agent's first blocking question rather than in the suite. The
    journeys may check *more* than the shim strictly requires — the seam itself is one
    such name, since a shim never reads the variable that names it.
    """
    required = set(REQUIRED_INPUT.findall(ASK_SCRIPT.read_text(encoding="utf-8")))
    assert required, (
        f"{ASK_SCRIPT.name}'s header declares no required environment at all; this gate "
        "reads the `(required)` lines of its `Environment:` block"
    )

    measured = set(CHECKED_INPUT.findall(LAUNCH_JOURNEYS.read_text(encoding="utf-8")))

    assert required <= measured, (
        f"{ASK_SCRIPT.name} requires {sorted(required - measured)} of the environment a "
        f"launch builds, and {LAUNCH_JOURNEYS.name} measures no launch shape for it"
    )


def _mirrored_shim(directory: Path, recorder: Path) -> Path:
    """The real shim in a checkout of its own, whose locked bus is ``recorder``.

    The shim runs the `onemessagebus` at its own checkout's `.venv/bin`, resolved from its
    location exactly as its configuration is and never from PATH, so a stand-in reaches
    it only from there. The mirror holds the real script and a copy of the real
    configuration at the same relative path, so what the shim asks under is that policy.
    """
    checkout = directory / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "config").mkdir()
    shutil.copy2(ASK_SCRIPT, checkout / "scripts" / ASK_SCRIPT.name)
    handed = _handed_to_every_launch()
    shutil.copy2(handed, checkout / "config" / handed.name)
    bus = checkout / ".venv" / "bin" / "onemessagebus"
    bus.parent.mkdir(parents=True)
    bus.symlink_to(recorder)
    return checkout / "scripts" / ASK_SCRIPT.name


def _asked(
    command: list[str], directory: Path, runs_dir: str | None, *, shim: bool = False
) -> tuple[list[str], str]:
    """Run one side against a recording stand-in for the bus, and read what it asked with.

    The stand-in shadows any real `onemessagebus` on PATH — where the persona's fallback,
    which travels into any repository, finds its bus — and, with ``shim``, stands as the
    locked bus of the checkout the real shim is run from; it answers nothing, so what is
    measured is the invocation each side composes rather than a reply. Each side runs in a
    working directory of its own with the run's record already written under it, so a
    `runs_dir` left unset exercises the `runs` default both sides fall back to without
    either of them writing into this checkout.
    """
    scratch = directory / "scratch"
    scratch.mkdir(parents=True)
    recorder = directory / "onemessagebus"
    recorder.write_text(BUS_RECORDER, encoding="utf-8")
    recorder.chmod(0o755)
    if shim:
        command = [*command, str(_mirrored_shim(directory, recorder)), PLACEHOLDER_QUESTION]
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


def _handed_to_every_launch() -> Path:
    """The bus configuration `scripts/onepipeline.sh` hands the engine, read off the script.

    Read rather than restated, so a repointed `--bus-config` fails here: that file is the
    one the shim opens, and the one whose parse the engine records for every run it starts.
    """
    declared = LAUNCH_BUS_CONFIG.search(LAUNCH_WRAPPER.read_text(encoding="utf-8"))
    assert declared is not None, (
        f"{LAUNCH_WRAPPER.name} no longer hands a launch `--bus-config` under its own "
        "checkout, so this gate cannot read which policy the engine records for a run"
    )
    return REPO_ROOT / declared["path"]


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
def test_the_personas_fallback_asks_exactly_as_the_shim_does(
    tmp_path: Path, runs_dir: str | None
) -> None:
    """A planner whose launch exported no shim asks the way the shim would have.

    `personas/planner.yaml` becomes every planner's own system prompt and travels into
    whatever repository is being planned against, so a fallback naming a retired request
    path is wrong everywhere a planner runs — and it is the one message a planner cannot
    afford to get wrong, because a blocking question that reaches nobody produces no
    other signal. The persona's block and `scripts/ask-manager.sh` are two statements of
    one invocation, so both are run against a recording stand-in for the bus and compared:
    the queue, every option and its value, the channel directory each derives from the
    run, and the frame each composes. Under a runs root that is set and one that is not,
    because the derivation carries a default and a persona restating only the set case
    would send a planner to whatever `runs` its working directory holds.

    **`--config` is compared like everything else**, as the two ends of one identity. The
    shim opens the file every launch hands the engine, read here off `scripts/onepipeline.sh`
    rather than restated. The fallback opens the parse of that same file which the engine
    recorded for the run it is asking on, so its `--config` document must be that record —
    written here by hand, carrying a policy no real configuration would, so a fallback
    reading anything else could not pass by coincidence. That the record really is that
    file's parse is `tests/ask_seam/launch/test_launch_ask_seam_e2e.py`'s, per launch shape.
    Neither end may drift: a repointed `--bus-config`, a fallback that read another file,
    and a fallback that passed no configuration at all each fail here.
    """
    snippet = fallback_snippet(TASK_TEMPLATE)
    bash = shutil.which("bash")
    assert bash is not None, "bash is not on this host's PATH"

    persona_argv, persona_frame = _asked(
        [bash, "-c", snippet], tmp_path / "persona", runs_dir=runs_dir
    )
    shim_argv, shim_frame = _asked([bash], tmp_path / "shim", runs_dir=runs_dir, shim=True)

    persona_positional, persona_options = _invocation(persona_argv)
    shim_positional, shim_options = _invocation(shim_argv)
    assert persona_positional == shim_positional, (
        f"{TASK_TEMPLATE.name}'s fallback asks `{' '.join(persona_positional)}` where "
        f"{ASK_SCRIPT.name} asks `{' '.join(shim_positional)}`"
    )

    handed = _handed_to_every_launch()
    shim_config = Path(shim_options.pop("--config", ""))
    assert shim_config.relative_to(tmp_path / "shim" / "checkout") == handed.relative_to(
        REPO_ROOT
    ), (
        f"{ASK_SCRIPT.name} asks under {shim_config}, where every launch hands the engine "
        f"{handed}; the shim and the run's own record are no longer one policy"
    )
    assert shim_config.read_bytes() == handed.read_bytes(), (
        f"the mirror's {shim_config} is not a copy of {handed}, so this compares two policies"
    )
    asked_under = Path(persona_options.pop("--config", ""))
    assert asked_under.is_file(), (
        f"{TASK_TEMPLATE.name}'s fallback asks under {asked_under}, which it did not write; "
        "a planner running it would ask under the bus's defaults rather than this run's policy"
    )
    assert json.loads(asked_under.read_text(encoding="utf-8")) == RECORDED_POLICY, (
        f"{TASK_TEMPLATE.name}'s fallback asks under "
        f"{asked_under.read_text(encoding='utf-8')!r}, not the policy the engine recorded "
        f"for this run ({json.dumps(RECORDED_POLICY)}); the two sides read different "
        f"configurations, and only one of them is {handed}"
    )

    assert persona_options == shim_options, (
        f"{TASK_TEMPLATE.name}'s fallback passes {persona_options} where {ASK_SCRIPT.name} "
        f"passes {shim_options}; the two have stopped stating one invocation"
    )
    assert json.loads(persona_frame) == json.loads(shim_frame), (
        f"{TASK_TEMPLATE.name}'s fallback composes {persona_frame!r} where "
        f"{ASK_SCRIPT.name} composes {shim_frame!r}"
    )


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
