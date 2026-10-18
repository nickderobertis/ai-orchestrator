"""The channel-writing supervisory members keep their access on every identity they fall to.

`graphs/dag-scope.yaml`'s `check-in` pacemaker raises the run's regular observer update
and its `monitor` raises findings; both write the run's channel. The access they need was
left to each identity's home: `oneharness.identities.toml` states no mode, and the
single-sided `check-in` member can state none. So a check-in turn that fell from the
rate-limited `codex:primary` to `codex:alternate`, whose Codex home configured a read-only
sandbox, could not take the channel's lock, and completed without its surface — a quiet
run with nothing watching it, read by its manager as a healthy one (issue #1898).

Both role files now declare `mode = "bypass"`, and these journeys force exactly that
fallback. The primary Codex identity's provider answers with the usage-limit refusal
oneharness classifies as `quota`, and the alternate identity's home holds
`sandbox_mode = "read-only"`. The provider is a stand-in that obeys that home the way
codex does — a turn whose command line carries no bypass flag runs under the home's
sandbox and cannot write — and records what it was launched with. The check-in turn is
the real member, read out of the shipped graph and run by the real `oneagentgraph`
through the oneharness it links, onto a real run's channel, and read back from that
channel through `just channel-next`. The monitor's config is run through the pinned
`oneharness` CLI with no mode of its own on the command line, so what decides its
access is the file.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] What this module spends
is one attached `just orchestrate` of a single `kind: human` node, which pauses at once and
dispatches nothing, three `oneagentgraph` turns and one `oneharness run` turn whose provider
is the stand-in below — no paid turn. A live run is the one thing a surface needs in order
to have a channel to arrive on, and the `launch-monitor-observer` project's
`launchMonitorObserverWorkspace` key already covers the configs and graph it reads.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys over the real
recipes and the real CLIs rather than a shell test suite, and their cost is the line above.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] No marker selects a tier
here: the module carries none, and the split it declines is the project one, for the
reason above.

llmlint: ignore-file[e2e_not_mocked] Only the paid provider is substituted, at the seam
every journey here substitutes it at: the `codex` binary `PATH` resolves, written below.
The role configs, the shipped graph member, `oneagentgraph`, the pinned `oneharness`, its
fallback classifier, the recipes and the run's channel are all real.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, TypedDict

import pytest
import short_state
import yaml
from planner_channel import next_surface_record
from project_fixtures import project_from_plan
from test_orchestrate_launch_e2e import CandidatePlan
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT
from orchestrator.run_reading import RunId

DAG_SCOPE_GRAPH = REPO_ROOT / "graphs" / "dag-scope.yaml"
CHECK_IN_MEMBER = "check-in"
MONITOR_CONFIG = REPO_ROOT / "oneharness.orchestrator.toml"

#: The launching session this module states rather than inherits, so the run is its own
#: to `just stop`.
LAUNCHING_SESSION = "e2e-supervisory-channel-access"

#: The indirection `oneharness.identities.toml` maps to `CODEX_HOME` on `codex:alternate`.
ALTERNATE_HOME_INDIRECTION = "ORCHESTRATOR_CODEX_ALT_HOME"

#: What a fallback identity's home says about access, which a declared mode must outrank.
WRITE_DENYING_HOME_CONFIG = 'sandbox_mode = "read-only"\napproval_policy = "never"\n'

#: The flag oneharness puts on codex's command line for `bypass`, and the sandbox it grants.
BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"
UNSANDBOXED = "danger-full-access"

#: What the stand-in raises when its sandbox lets it write, so the surface read back is
#: known to be this turn's rather than anything else on the channel.
CHECK_IN_UPDATE = "check-in from the fallback identity: run paused at `approve`, no drafts"

#: A Codex provider binary that obeys its home's sandbox the way codex does, and records it.
#:
#: The primary identity's home is named in `STAND_IN_EXHAUSTED_HOME`; launched there, it
#: answers with codex's own usage-limit stream, which oneharness classifies as `quota`.
#: Anywhere else it holds one rule: a command line carrying the bypass flag runs unsandboxed,
#: and one without it runs under its home's `sandbox_mode`. Only an unsandboxed turn writes,
#: raising the update onto the run named by `ONEPIPELINE_RUN_ID`, the variable the engine
#: exports to an observer member; the other answers, as the incident's turn did, having
#: written nothing.
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] What a journey here asserts is
# the flag oneharness put on the command line, read off the pinned CLI's real argv; this one
# rule is codex's own, the paid provider no deterministic test can run, stated as narrowly as
# the proof needs, so the read-only control proves that home is honoured without the flag.
CODEX_STAND_IN = """#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, tomllib

argv = sys.argv[1:]
if {"--version", "-v"}.intersection(argv):
    print("codex-cli 0.0.0 (channel-access stand-in)")
    raise SystemExit(0)
home = os.environ.get("CODEX_HOME", "")
record = {"argv": argv, "codex_home": home}

def emit(*events):
    for event in events:
        print(json.dumps(event), flush=True)

def append(entry):
    with open(os.environ["STAND_IN_RECORD"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\\n")

if home == os.environ["STAND_IN_EXHAUSTED_HOME"]:
    limit = "You've hit your usage limit. Try again at Dec 31st, 2027 6:00 AM."
    append({**record, "answered": "quota"})
    emit(
        {"type": "thread.started", "thread_id": "exhausted"},
        {"type": "turn.started"},
        {"type": "error", "message": limit},
        {"type": "turn.failed", "error": {"message": limit}},
    )
    raise SystemExit(1)

if "--dangerously-bypass-approvals-and-sandbox" in argv:
    sandbox = "danger-full-access"
else:
    sandbox = tomllib.loads(pathlib.Path(home, "config.toml").read_text("utf-8"))["sandbox_mode"]
record["sandbox"] = sandbox

said = "nothing to raise"
if os.environ.get("STAND_IN_RAISES") and sandbox == "danger-full-access":
    raised = subprocess.run(
        ["onepipeline", "surface", "--kind", "check-in", os.environ["ONEPIPELINE_RUN_ID"]],
        input=os.environ["STAND_IN_RAISES"], text=True, capture_output=True,
    )
    record["raised"] = raised.returncode
    said = f"raised the update (exit {raised.returncode}): {raised.stderr.strip()}"
elif os.environ.get("STAND_IN_RAISES"):
    record["raised"] = None
    said = f"could not take the channel lock under the {sandbox} sandbox"
append(record)
emit(
    {"type": "thread.started", "thread_id": "answered"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "message", "type": "agent_message", "text": said}},
    {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0,
                                         "output_tokens": 1}},
)
"""


class Launch(TypedDict, total=False):
    """One launch of the stand-in, as it recorded itself."""

    argv: list[str]
    codex_home: str
    answered: str
    sandbox: str
    raised: int | None


class PausedRun(NamedTuple):
    """A real run waiting on a human approval, and the environment its recipes share."""

    environment: dict[str, str]
    run: RunId


class Homes(NamedTuple):
    """The two Codex identities' homes: the exhausted primary, and the write-denying fallback."""

    primary: Path
    alternate: Path


def _homes(tmp_path: Path) -> Homes:
    homes = Homes(tmp_path / "codex-primary-home", tmp_path / "codex-alternate-home")
    homes.primary.mkdir()
    homes.alternate.mkdir()
    (homes.alternate / "config.toml").write_text(WRITE_DENYING_HOME_CONFIG, encoding="utf-8")
    return homes


def _turn_environment(
    base: dict[str, str], tmp_path: Path, homes: Homes, *, raises: str | None
) -> dict[str, str]:
    """`base` with the stand-in first on `PATH` and each Codex identity given its home.

    `ONEHARNESS_BIN_CODEX` is dropped so `codex` resolves on `PATH` for both variants
    alike; Claude's provider stays the launch environment's paid-provider guard, which
    refuses loudly should the chain ever pass both Codex identities.
    """
    front = tmp_path / "provider"
    front.mkdir(exist_ok=True)
    stand_in = front / "codex"
    stand_in.write_text(CODEX_STAND_IN, encoding="utf-8")
    stand_in.chmod(0o755)
    environment = {
        name: value for name, value in base.items() if not name.startswith("ONEHARNESS_BIN_")
    }
    environment.update(
        {
            "CODEX_HOME": str(homes.primary),
            ALTERNATE_HOME_INDIRECTION: str(homes.alternate),
            # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
            "STAND_IN_EXHAUSTED_HOME": str(homes.primary),
            "STAND_IN_RECORD": str(tmp_path / "launches.jsonl"),
            "PATH": os.pathsep.join((str(front), str(REPO_ROOT / ".venv" / "bin"), base["PATH"])),
            "ONEAGENTGRAPH_STATE_DIR": str(tmp_path / "graph-state"),
        }
    )
    environment.setdefault("XDG_STATE_HOME", str(short_state.state_home(tmp_path)))
    if raises is not None:
        environment["STAND_IN_RAISES"] = raises
    return environment


def _launches(environment: dict[str, str]) -> list[Launch]:
    record = Path(environment["STAND_IN_RECORD"])
    assert record.is_file(), "no Codex identity was ever launched"
    # llmlint: ignore[boundary_inputs_validated] The stand-in's own JSON, written by the
    # script above; each field read out of it is compared against a literal.
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]


def _options(launch: Launch) -> list[str]:
    """The options a launch was given, without the prompt that would bury them."""
    return [word for word in launch["argv"] if word.startswith("-")]


def _check_in_graph(tmp_path: Path, config: Path) -> Path:
    """The shipped `check-in` member alone, its turn run against `config`.

    Every field is the shipped graph's but two: the paths, made absolute because the copy
    sits elsewhere, and the `schedule`, dropped because a run of the member is this
    journey's to start rather than its thirty-minute clock's.
    """
    shipped = yaml.safe_load(DAG_SCOPE_GRAPH.read_text(encoding="utf-8"))
    member = dict(shipped["members"][CHECK_IN_MEMBER])
    member.pop("schedule")
    member["oneharness_config"] = str(config)
    member["persona"] = str((DAG_SCOPE_GRAPH.parent / member["persona"]).resolve())
    graph = tmp_path / "check-in.yaml"
    graph.write_text(
        yaml.safe_dump(
            {
                "version": shipped["version"],
                "name": "check-in-fallback",
                "members": {CHECK_IN_MEMBER: member},
            }
        ),
        encoding="utf-8",
    )
    return graph


def _run_check_in(environment: dict[str, str], tmp_path: Path, config: Path) -> None:
    """Run the member once through the real `oneagentgraph`, and hold that it settled."""
    run_dir = tmp_path / "member"
    run_dir.mkdir(exist_ok=True)
    composed = f"onepipeline run `{environment['ONEPIPELINE_RUN_ID']}`."
    ran = subprocess.run(
        [
            "oneagentgraph",
            "run",
            str(_check_in_graph(tmp_path, config)),
            "--task",
            composed,
            "--dir",
            str(run_dir),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, f"the check-in member did not settle:\n{ran.stdout}\n{ran.stderr}"


def _fallback_launch(environment: dict[str, str], homes: Homes) -> Launch:
    """The one launch on the fallback identity, after the primary's one quota refusal."""
    launches = _launches(environment)
    assert [launch["codex_home"] for launch in launches] == [
        str(homes.primary),
        str(homes.alternate),
    ], f"the chain did not fall from codex:primary to codex:alternate once: {launches}"
    assert launches[0].get("answered") == "quota", launches[0]
    return launches[1]


@pytest.fixture
def paused(tmp_path: Path, oneharness_bin: str) -> Iterator[PausedRun]:
    """A real run, paused at a human approval and dispatching nothing, whose channel is live."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(tmp_path, oneharness_bin, session=LAUNCHING_SESSION)
    plan_file = tmp_path / "supervisory-channel-access.plan.json"
    plan: CandidatePlan = {
        "schema_version": 2,
        "tasks": [{"id": "approve", "kind": "human", "task": "Approve the release."}],
    }
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    project = project_from_plan(plan_file)
    run = RunId(project.split(":", 1)[1])
    try:
        launched = subprocess.run(
            ["just", "orchestrate", project, "--dag-graph", "off"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
            stdin=subprocess.DEVNULL,
        )
        assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}{launched.stderr}"
        yield PausedRun({**environment, "ONEPIPELINE_RUN_ID": run}, run)
    finally:
        subprocess.run(
            ["just", "stop", run],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )


def _without_mode(tmp_path: Path, config: Path) -> Path:
    """`config` as it stood before it declared a mode: the access left to the identity home."""
    copy = tmp_path / f"undeclared-{config.name}"
    lines = [
        f'extends = "{REPO_ROOT / "oneharness.identities.toml"}"'
        if line.startswith("extends = ")
        else line
        for line in config.read_text(encoding="utf-8").splitlines()
        if not line.startswith("mode = ")
    ]
    copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return copy


def test_the_check_in_update_reaches_the_channel_from_a_write_denying_fallback_identity(
    tmp_path: Path, paused: PausedRun
) -> None:
    """Primary out of quota, fallback home read-only: the update still arrives, and is read back.

    The control comes first, on the same run and the same homes: the shipped member run
    against a copy of its config with the mode line removed falls through the same way and
    completes having raised nothing, which is the incident — and what proves the home
    really denies the write, so the declared mode is what changed the outcome.
    """
    config = REPO_ROOT / "oneharness.check-in.toml"
    homes = _homes(tmp_path)

    undeclared_at = tmp_path / "undeclared"
    undeclared_at.mkdir()
    undeclared = _turn_environment(paused.environment, undeclared_at, homes, raises=CHECK_IN_UPDATE)
    _run_check_in(undeclared, undeclared_at, _without_mode(undeclared_at, config))
    denied = _fallback_launch(undeclared, homes)
    assert BYPASS_FLAG not in denied["argv"] and denied["sandbox"] == "read-only", denied
    assert denied["raised"] is None, denied
    assert next_surface_record(paused.run, paused.environment) is None, (
        "a surface reached the channel from a turn that could not write it"
    )

    declared_at = tmp_path / "declared"
    declared_at.mkdir()
    declared = _turn_environment(paused.environment, declared_at, homes, raises=CHECK_IN_UPDATE)
    _run_check_in(declared, declared_at, config)
    launched = _fallback_launch(declared, homes)
    assert BYPASS_FLAG in launched["argv"], (
        f"{config.name} launched codex:alternate without {BYPASS_FLAG}, so its home's "
        f"read-only sandbox decided the turn's access: {_options(launched)}"
    )
    assert launched["sandbox"] == UNSANDBOXED, launched
    assert launched["raised"] == 0, launched

    surface = next_surface_record(paused.run, paused.environment)
    assert surface is not None, "the fallback identity's update never reached the run's channel"
    assert surface["message"] == CHECK_IN_UPDATE, surface
    assert surface.get("kind") == "check-in", surface
    assert surface["blocking"] is False, surface


def test_the_monitor_falls_to_a_write_denying_identity_in_bypass_mode(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The monitor's file alone puts its fallback identity in bypass, over a read-only home.

    Run through the pinned `oneharness` CLI with no `--mode`, so nothing but
    `oneharness.orchestrator.toml` can be what decided it.
    """
    homes = _homes(tmp_path)
    environment = _turn_environment(
        _launched_environment(tmp_path, oneharness_bin, session=LAUNCHING_SESSION),
        tmp_path,
        homes,
        raises=None,
    )
    ran = subprocess.run(
        [oneharness_bin, "run", "--config", str(MONITOR_CONFIG), "--prompt", "watch the run"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, f"the monitor's turn did not run:\n{ran.stdout}\n{ran.stderr}"

    launched = _fallback_launch(environment, homes)
    assert BYPASS_FLAG in launched["argv"], (
        f"{MONITOR_CONFIG.name} launched codex:alternate without {BYPASS_FLAG}, so its "
        f"home's read-only sandbox decided whether the monitor can write the channel: "
        f"{_options(launched)}"
    )
    assert launched["sandbox"] == UNSANDBOXED, launched
