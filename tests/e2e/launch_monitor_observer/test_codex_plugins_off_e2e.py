"""Every Codex process this host starts leaves Codex's plugins off, on both roads.

A Codex process with plugins on downloads Codex's ~29 MB remote plugin catalog when it
starts, and this host starts one for every usage probe and every Codex turn. The
releases this host adopts start Codex with plugins off unless a config opts in
(oneharness https://github.com/nickderobertis/oneharness/pull/1457), and there are two
roads to hold to that, governed by two different pins:

- the **spawned-CLI road**: `scripts/oneharness-usage.sh`, the host's own probe, runs the
  `oneharness` CLI `config/oneharness.version` installs — the same CLI a two-party
  member's turn spawns from `PATH` under `config/onejudge.base.yaml`'s `bin: oneharness`;
- the **in-process road**: a onejudge naming no `bin` runs its turn through the
  `oneharness-core` linked into it rather than through any `oneharness` binary, as the
  engine's linked libraries do for the turns no CLI is spawned for. It is driven here
  through the pinned `onejudge` CLI's in-process provider, on the same onejudge release
  the adopted engine links — which this module reads off the engine npm package's SBOM rather
  than assumes.

Codex is the one thing doubled, at its process boundary: a recording `codex` first on
`PATH` that writes each launch's argv and `CODEX_HOME` to a log, then answers as Codex
would — `app-server --stdio` over JSON-RPC for a probe, `tests/e2e/fake_codex.py`'s
`exec --json` stream for a turn. Each Codex identity's home is a scratch directory whose
`config.toml` says nothing about plugins, so what decides is the argv the pinned releases
build from this repository's own `oneharness.*.toml` configs, and never a host stopgap.
`tests/test_linked_libraries.py` holds both cores to the release that brought the default.
"""

# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] Nothing here is expensive:
# Codex is doubled, no model turn is spent, and both tools driven are the ones this
# workspace's own lockfile installs into `.venv`, the same tier and CLIs as
# tests/e2e/launch_monitor_observer/test_oneharness_reset_credits_e2e.py beside it.

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pytest
from harness_configs import extends_chain, harness_routing
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator import npm_installs
from orchestrator.root import REPO_ROOT

FAKE_CODEX = helper("fake_codex.py")
JUDGE_DOUBLE = helper("judge_protocol_double.py")
USAGE_WRAPPER = REPO_ROOT / "scripts" / "oneharness-usage.sh"
ONEJUDGE = REPO_ROOT / ".venv" / "bin" / "onejudge"
#: The agent side's role config: the one a dispatched worker's turn runs against.
AGENT_CONFIG = REPO_ROOT / "oneharness.toml"


def _codex_identities() -> dict[str, str]:
    """Every Codex identity the agent side's chain names, and the variable its home is read
    from: the variant's own `env_from` for `CODEX_HOME`, else `CODEX_HOME` itself — both
    read off the configs, so an identity added to the chain is probed here too."""
    env_from: dict[str, dict[str, str]] = {}
    for config in reversed(extends_chain(AGENT_CONFIG)):
        declared = harness_routing(config).get("harness", {}).get("codex", {})
        for name, variant in declared.get("variant", {}).items():
            # A child's `env_from` adds to its parent's by key, as `oneharness` merges it.
            env_from[name] = {**env_from.get(name, {}), **variant.get("env_from", {})}
    chain = harness_routing(AGENT_CONFIG).get("harnesses", [])
    return {
        identity: env_from.get(identity.removeprefix("codex:"), {}).get("CODEX_HOME", "CODEX_HOME")
        for identity in chain
        if identity.startswith("codex:")
    }


CODEX_IDENTITIES = _codex_identities()
#: The engine npm package, whose platform SBOM says which onejudge a dispatch runs.
ENGINE_DISTRIBUTION = "onepipeline-cli"

#: A rate-limit answer with one populated window, so a probe reads as an answer.
RATE_LIMITS = {
    "rateLimitsByLimitId": {
        "codex": {
            "limitId": "codex",
            "limitName": None,
            "primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 1_792_000_000},
            "secondary": None,
            "planType": "pro",
        }
    }
}

# llmlint: ignore-block[e2e_not_mocked] Codex is the paid provider, the one boundary AGENTS.md's realistic-tests invariant doubles: a real Codex cannot be made to report its own argv, and the subjects — the pinned `oneharness` CLI behind the real wrapper, and the pinned `onejudge` running its linked core — run for real.  # noqa: E501 - a directive is one line
RECORDING_CODEX = f"""#!{sys.executable}
import json, os, sys

with open(os.environ["RECORDED_CODEX_LAUNCHES"], "a", encoding="utf-8") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "home": os.environ.get("CODEX_HOME")}}) + "\\n")
if sys.argv[1:2] != ["app-server"]:
    os.execv(sys.executable, [sys.executable, {str(FAKE_CODEX)!r}, *sys.argv[1:]])
RATE_LIMITS = json.loads({json.dumps(json.dumps(RATE_LIMITS))})
for line in sys.stdin:
    request = json.loads(line)
    match request.get("method"):
        case "initialize":
            result = {{"userAgent": "recording-codex"}}
        case "account/rateLimits/read":
            result = RATE_LIMITS
        case _:
            continue
    print(json.dumps({{"jsonrpc": "2.0", "id": request["id"], "result": result}}), flush=True)
"""
# llmlint: ignore-end[e2e_not_mocked]

#: A Codex home as a host without the stopgap has one: present, and silent on plugins.
SILENT_CODEX_CONFIG = (
    '# A scratch Codex home: no [features] table, no stopgap.\nmodel_reasoning_effort = "low"\n'
)


class Launch(NamedTuple):
    """One process the recording `codex` was started as."""

    argv: list[str]
    #: The `CODEX_HOME` it was handed, which says which identity started it.
    home: str | None


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] This helper is the acceptance criterion’s predicate — the last plugins setting across the argv spellings oneharness emits is off — rather than a Codex parser. The real CLI and onejudge journeys below supply the recorded argv it reads.  # noqa: E501 - a directive is one line
def plugins_setting(argv: Sequence[str]) -> bool | None:
    """The plugins setting Codex reads off `argv`: the last one in either spelling.

    Codex takes `--enable <feature>` / `--disable <feature>` and `-c features.<feature>=`
    (or `--config`) alike, and the last one wins. `None` is an argv that sets nothing,
    which leaves Codex's own default — plugins on — in force.
    """
    setting: bool | None = None
    for index, word in enumerate(argv):
        following = argv[index + 1] if index + 1 < len(argv) else None
        match (word, following):
            case ("--enable" | "--disable", "plugins"):
                setting = word == "--enable"
            case ("-c" | "--config", str(assignment)):
                key, _, value = assignment.partition("=")
                if key.strip() == "features.plugins":
                    setting = value.strip().strip("\"'").lower() == "true"
    return setting


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


class Sandbox(NamedTuple):
    """A host with no Codex but the recording one and no state but its own."""

    environment: dict[str, str]
    log: Path
    homes: dict[str, Path]

    def launches(self) -> list[Launch]:
        if not self.log.exists():
            return []
        return [
            Launch(**json.loads(line))
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def _sandbox(tmp_path: Path) -> Sandbox:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(RECORDING_CODEX, encoding="utf-8")
    codex.chmod(0o755)
    homes: dict[str, Path] = {}
    for identity, variable in CODEX_IDENTITIES.items():
        home = tmp_path / identity.replace(":", "-")
        home.mkdir()
        (home / "config.toml").write_text(SILENT_CODEX_CONFIG, encoding="utf-8")
        homes[variable] = home
    path = f"{bin_dir}:/usr/bin:/bin"
    for binary in ("oneharness", "claude"):
        assert shutil.which(binary, path=path) is None, f"a `{binary}` is reachable on {path}"
    scratch = tmp_path / "node-scratch"
    scratch.mkdir()
    log = tmp_path / "codex-launches.ndjson"
    # Built from nothing, so no inherited `ONEHARNESS_*`, `CODEX_HOME` or host identity
    # directory reaches either road; every Claude identity resolves to an empty scratch
    # directory, and with no `claude` on PATH those candidates are skipped, never run.
    environment = {
        "PATH": path,
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "home" / ".config"),
        "XDG_STATE_HOME": str(tmp_path / "home" / ".local" / "state"),
        "ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch),
        "RECORDED_CODEX_LAUNCHES": str(log),
        "FAKE_CODEX_PROMPT_LOG": str(tmp_path / "fake-codex-prompts.ndjson"),
        **{variable: str(home) for variable, home in homes.items()},
        **{
            variable: str(tmp_path / variable.lower())
            for variable in (
                "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR",
                "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR",
                "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR",
                "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR",
            )
        },
    }
    for home in homes.values():
        assert "plugins" not in (home / "config.toml").read_text(encoding="utf-8")
    return Sandbox(environment=environment, log=log, homes=homes)


def _codex_launches(sandbox: Sandbox) -> list[Launch]:
    """Every launch that is Codex doing work, leaving out the `--version` probes."""
    return [launch for launch in sandbox.launches() if launch.argv != ["--version"]]


# llmlint: ignore-block[shell_test_tiers_stay_split] A pytest journey, not a shell suite: it runs the real wrapper over the `oneharness` this workspace's own lockfile installs into `.venv`, against a local stand-in, in well under a second, the same tier and CLI as tests/e2e/launch_monitor_observer/test_oneharness_reset_credits_e2e.py.  # noqa: E501 - a directive is one line
def _probe(sandbox: Sandbox, oneharness_bin: str, *arguments: str) -> dict[str, object]:
    completed = subprocess.run(
        [str(USAGE_WRAPPER), *arguments, "--format", "json"],
        cwd=REPO_ROOT,
        env={**sandbox.environment, "ONEHARNESS_BIN": oneharness_bin},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report: dict[str, object] = json.loads(completed.stdout)
    return report


# llmlint: ignore-end[shell_test_tiers_stay_split]


def test_the_hosts_usage_probe_starts_every_codex_identity_with_plugins_off(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The spawned-CLI road: the wrapper, the pinned CLI, the agent side's own chain."""
    sandbox = _sandbox(tmp_path)

    report = _probe(
        sandbox,
        oneharness_bin,
        "--config",
        str(AGENT_CONFIG),
        "--harness",
        ",".join(CODEX_IDENTITIES),
    )

    identities = report["identities"]
    assert isinstance(identities, list)
    assert [identity["availability"]["state"] for identity in identities] == ["available"] * len(
        CODEX_IDENTITIES
    ), identities
    launches = _codex_launches(sandbox)
    assert sorted(launch.home or "" for launch in launches) == sorted(
        str(home) for home in sandbox.homes.values()
    ), f"each Codex identity's probe should start Codex once, in its own home: {launches}"
    for launch in launches:
        assert launch.argv[:1] == ["app-server"], launch
        assert plugins_setting(launch.argv) is False, (
            f"the usage probe started Codex in {launch.home} without plugins off, so it "
            f"downloads the plugin catalog on start: {launch.argv}"
        )


def _engine_linked_onejudge() -> str:
    """The onejudge the adopted engine links, read off its npm platform SBOM."""
    platform = npm_installs.platform_binary(ENGINE_DISTRIBUTION).parent.parent
    (sbom,) = (platform / "sboms").iterdir()
    document = json.loads(sbom.read_text("utf-8"))
    (linked,) = {
        str(component["version"])
        for component in document["components"]
        if component["name"] == "onejudge"
    }
    return linked


@pytest.fixture(scope="module")
def onejudge_bin(adopted_onejudge_version: str) -> str:
    """The pinned onejudge, which must be the release the adopted engine links."""
    if not ONEJUDGE.is_file():
        pytest.fail(f"worktree-local onejudge missing at {ONEJUDGE} — run 'just bootstrap'")
    reported = subprocess.run(
        [str(ONEJUDGE), "--version"], text=True, capture_output=True, timeout=30, check=False
    )
    assert reported.stdout.strip() == f"onejudge {adopted_onejudge_version}", reported
    linked = _engine_linked_onejudge()
    assert linked == adopted_onejudge_version, (
        f"the adopted engine links onejudge {linked} while config/onejudge.version names "
        f"{adopted_onejudge_version}, so this journey would not drive what a dispatch runs"
    )
    return str(ONEJUDGE)


def _one_turn(tmp_path: Path, sandbox: Sandbox, onejudge_bin: str, agent_config: Path) -> None:
    """One worker turn through onejudge's in-process provider, judged by a protocol double.

    The worker side names no `bin`, which is what keeps it in process; the judge side is a
    `command` provider so that the only Codex process the turn starts is the worker's.
    """
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True, timeout=30)
    config = tmp_path / "onejudge.yaml"
    config.write_text(
        json.dumps(
            {
                "provider": {
                    "kind": "split",
                    "skill": {"kind": "oneharness"},
                    "judge": {
                        "kind": "command",
                        "command": [sys.executable, str(JUDGE_DOUBLE), "judge", "0"],
                    },
                },
                "task": "Say hello.",
                "user": {
                    "persona": "A reviewer.",
                    "done_when": "the worker said hello",
                    "max_turns": 1,
                },
                "evals": [],
                "assessment": None,
            }
        ),
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    completed = subprocess.run(
        [onejudge_bin, "run", str(config), "--format", "json", "--output", str(report)],
        cwd=work,
        env={**sandbox.environment, "ONEHARNESS_CONFIG": str(agent_config)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(report.read_text(encoding="utf-8"))["transcript"], completed.stderr


def test_a_dispatch_turn_in_the_linked_onejudge_starts_codex_with_plugins_off(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """The in-process road: the agent side's role config, run inside onejudge's own core."""
    sandbox = _sandbox(tmp_path)

    _one_turn(tmp_path, sandbox, onejudge_bin, AGENT_CONFIG)

    launches = _codex_launches(sandbox)
    assert [launch.argv[:1] for launch in launches] == [["exec"]], launches
    (turn,) = launches
    assert turn.home == str(sandbox.homes["CODEX_HOME"]), (
        f"the worker's chain should fall through to codex:primary's home: {turn}"
    )
    assert plugins_setting(turn.argv) is False, (
        f"the in-process turn started Codex without plugins off, so it downloads the "
        f"plugin catalog on start: {turn.argv}"
    )


def test_a_config_that_opts_in_reaches_codex_with_plugins_left_on(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """The control: a role that opts in is read as on, so the reading above can fail.

    Layered over the same agent config, `plugins = true` on the Codex harness is the one
    opt-in the releases accept; a reading that called every argv "off" would pass both
    journeys above and fail here.
    """
    sandbox = _sandbox(tmp_path)
    opted_in = tmp_path / "oneharness.opt-in.toml"
    opted_in.write_text(
        f'extends = "{AGENT_CONFIG}"\n\n[harness.codex]\nplugins = true\n', encoding="utf-8"
    )

    _one_turn(tmp_path, sandbox, onejudge_bin, opted_in)

    (turn,) = _codex_launches(sandbox)
    assert plugins_setting(turn.argv) is not False, turn.argv


@pytest.mark.parametrize(
    ("argv", "setting"),
    [
        (["exec", "-c", "features.plugins=false", "--json", "hi"], False),
        (["app-server", "--disable", "plugins", "--stdio"], False),
        (["exec", "-c", "features.plugins=false", "--enable", "plugins"], True),
        (["app-server", "--enable", "plugins", "--config", "features.plugins=false"], False),
        (["exec", "-c", "features.apps=false", "--json", "hi"], None),
    ],
    ids=["config-off", "disable", "enable-wins-last", "config-wins-last", "unset"],
)
def test_the_last_plugins_setting_in_either_spelling_is_the_one_read(
    argv: list[str], setting: bool | None
) -> None:
    assert plugins_setting(argv) is setting
