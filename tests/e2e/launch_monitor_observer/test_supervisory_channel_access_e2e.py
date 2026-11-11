"""The channel-writing supervisory member keeps its access on every identity it falls to.

`graphs/dag-scope.yaml`'s `monitor` raises findings and the run's progress report; both
write the run's channel. The access it needs was once left to each identity's home:
`oneharness.identities.toml` states no mode. So a supervisory turn that fell from the
rate-limited `codex:primary` to `codex:alternate`, whose Codex home configured a read-only
sandbox, could not take the channel's lock, and completed without its surface — a quiet
run with nothing watching it, read by its manager as a healthy one (issue #1898).

The monitor's role file now declares `mode = "bypass"`, and this journey forces exactly
that fallback. The primary Codex identity's provider answers with the usage-limit refusal
oneharness classifies as `quota`, and the alternate identity's home holds
`sandbox_mode = "read-only"`. The provider is a stand-in that obeys that home the way
codex does — a turn whose command line carries no bypass flag runs under the home's
sandbox and cannot write — and records what it was launched with. The monitor's config
is run through the pinned `oneharness` CLI with no mode of its own on the command line, so
what decides its access is the file.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] What this module spends
is one `oneharness run` turn whose provider is the stand-in below — no paid turn — and the
`launch-monitor-observer` project's `launchMonitorObserverWorkspace` key already covers the
config it reads.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys over the real
recipes and the real CLIs rather than a shell test suite, and their cost is the line above.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] No marker selects a tier
here: the module carries none, and the split it declines is the project one, for the
reason above.

llmlint: ignore-file[e2e_not_mocked] Only the paid provider is substituted, at the seam
every journey here substitutes it at: the `codex` binary `PATH` resolves, written below.
The role config, the pinned `oneharness` and its fallback classifier are all real.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple, TypedDict

import short_state
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

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

#: A Codex provider binary that obeys its home's sandbox the way codex does, and records it.
#:
#: The primary identity's home is named in `STAND_IN_EXHAUSTED_HOME`; launched there, it
#: answers with codex's own usage-limit stream, which oneharness classifies as `quota`.
#: Anywhere else it holds one rule: a command line carrying the bypass flag runs unsandboxed,
#: and one without it runs under its home's `sandbox_mode`, which it records.
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] What a journey here asserts is
# the flag oneharness put on the command line, read off the pinned CLI's real argv; this one
# rule is codex's own, the paid provider no deterministic test can run, stated as narrowly as
# the proof needs, so the read-only control proves that home is honoured without the flag.
CODEX_STAND_IN = """#!/usr/bin/env python3
import json, os, pathlib, sys, tomllib

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

said = f"ran under the {sandbox} sandbox"
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


def _turn_environment(base: dict[str, str], tmp_path: Path, homes: Homes) -> dict[str, str]:
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


def _fallback_launch(environment: dict[str, str], homes: Homes) -> Launch:
    """The one launch on the fallback identity, after the primary's one quota refusal."""
    launches = _launches(environment)
    assert [launch["codex_home"] for launch in launches] == [
        str(homes.primary),
        str(homes.alternate),
    ], f"the chain did not fall from codex:primary to codex:alternate once: {launches}"
    assert launches[0].get("answered") == "quota", launches[0]
    return launches[1]


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
