"""Real-CLI coverage for the two settings this host makes about its supervisory turns.

Both onejudge judge sides state a non-writing mode at their own top level, and every
supervisory Codex role switches the ChatGPT-app connector tools off; no worker role or
shared parent carries either. Why is `docs/onejudge-integration.md`'s, "The mode a judge
runs in". The role files are the one source of both settings: this module asks the real
`oneharness` what each resolves to, attributed to that file, and what argv a dry run of
each identity would build — never a paid turn.
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from typing import Any

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The judge sides onejudge frames, each named by the wiring that dispatches it.
JUDGE_ROLES = ("oneharness.judge.toml", "oneharness.design-doc-judge.toml")

#: The modes a judge may run in without a ruling: both grant reading and change nothing.
JUDGE_MODES = {"plan", "default"}

#: The Codex roles whose turn is not a worker's.
CONNECTOR_ROLES = (
    "oneharness.judge.toml",
    "oneharness.orchestrator.toml",
    "oneharness.check-in.toml",
    "oneharness.plan-review.toml",
    "oneharness.pr-author.toml",
    "oneharness.design-doc-judge.toml",
    "oneharness.llmlint.toml",
)

#: codex-cli's switch for the ChatGPT-app connector tools, as the argv pair codex reads.
CONNECTORS_OFF = ["-c", "features.apps=false"]

#: The roles whose turn is a worker's, and the parents nothing runs against.
WORKER_ROLES = ("oneharness.toml", "oneharness.follow-up.toml", "oneharness.design-doc.toml")
SHARED_PARENTS = ("oneharness.identities.toml", "oneharness.dispatch.toml")


def _environment() -> dict[str, str]:
    """The suite's environment less `ONEHARNESS_*`, whose overrides beat every file.

    A worker runs this suite from inside a dispatch, so an inherited override would be
    read in place of the file under test.
    """
    return {key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")}


# llmlint: ignore-block[shell_test_tiers_stay_split] Not a shell suite, and the only tool
# driven is the worktree's own pinned `oneharness`, in `config` and `--print-command` dry
# runs that spawn no harness, install nothing and reach no network — the same tier and the
# same CLI as `test_oneharness_timeout_e2e.py` and `test_oneharness_config_equivalence_e2e.py`
# beside it, which this module's sibling assertions live with.
def _resolved(oneharness_bin: str, config: str) -> dict[str, Any]:
    """What `oneharness config` resolves `config` to, run from the repository root."""
    proc = subprocess.run(
        [oneharness_bin, "config", "--config", config, "--compact"],
        cwd=REPO_ROOT,
        env=_environment(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert proc.returncode == 0, f"{config} did not resolve: {proc.stderr}"
    resolved: dict[str, Any] = json.loads(proc.stdout)
    return resolved


def _planned_command(oneharness_bin: str, config: str, harness_id: str) -> str:
    """The argv a run of `config` would hand `harness_id`, from a dry run that spends nothing."""
    proc = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            config,
            "--harness",
            harness_id,
            "--print-command",
            "--prompt",
            "x",
        ],
        cwd=REPO_ROOT,
        env=_environment(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert proc.returncode == 0, f"{config} did not plan {harness_id}: {proc.stderr}"
    commands = [
        line.strip().removeprefix("command: ")
        for line in proc.stdout.splitlines()
        if line.strip().startswith("command: ")
    ]
    assert len(commands) == 1, f"{config} planned {commands} for {harness_id}"
    return commands[0]


# llmlint: ignore-end[shell_test_tiers_stay_split]


def _carries_pair(args: list[str] | None, pair: list[str]) -> bool:
    """Whether `pair` appears in `args` as adjacent elements."""
    values = args or []
    return any(values[index : index + len(pair)] == pair for index in range(len(values)))


def _identities(oneharness_bin: str, config: str, harness: str) -> list[str]:
    """The chain's identities on one harness, as `config` resolves its chain."""
    chain: list[str] = _resolved(oneharness_bin, config)["harnesses"]["value"]
    return [identity for identity in chain if identity.partition(":")[0] == harness]


def test_the_judge_roles_are_the_judges_the_wiring_names() -> None:
    """The two files held below are exactly the judge sides onejudge is handed."""
    node_scope = (REPO_ROOT / "graphs" / "node-scope.yaml").read_text(encoding="utf-8")
    design_doc = (REPO_ROOT / "graphs" / "design-doc.yaml").read_text(encoding="utf-8")
    base = (REPO_ROOT / "config" / "onejudge.base.yaml").read_text(encoding="utf-8")
    assert "judge:\n      oneharness_config: ../oneharness.judge.toml" in node_scope
    assert "judge:\n      oneharness_config: ../oneharness.design-doc-judge.toml" in design_doc
    assert "judge_config: oneharness.judge.toml" in base


@pytest.mark.parametrize("config", JUDGE_ROLES)
def test_every_judge_runs_in_the_non_writing_mode_its_own_file_states(
    oneharness_bin: str, config: str
) -> None:
    """The mode is the judge file's, resolves as that file's, and reaches each harness."""
    declared = tomllib.loads((REPO_ROOT / config).read_text(encoding="utf-8")).get("mode")
    assert declared in JUDGE_MODES, (
        f"{config} must state its judge's mode at its own top level, one of "
        f"{sorted(JUDGE_MODES)}; it states {declared!r}, so onejudge frames that judge "
        "by its read-only default"
    )

    mode = _resolved(oneharness_bin, config)["mode"]
    assert mode == {"value": declared, "source": config}, (
        f"{config} declares mode {declared!r} but oneharness resolves {mode}"
    )

    for codex in _identities(oneharness_bin, config, "codex"):
        command = _planned_command(oneharness_bin, config, codex)
        assert "--dangerously-bypass-approvals-and-sandbox" not in command, command
        assert "workspace-write" not in command, command
        assert "danger-full-access" not in command, command
    for claude in _identities(oneharness_bin, config, "claude-code"):
        command = _planned_command(oneharness_bin, config, claude)
        assert f"--permission-mode {'dontAsk' if declared == 'default' else declared}" in (
            command
        ), command


@pytest.mark.parametrize("config", CONNECTOR_ROLES)
def test_every_supervisory_codex_role_switches_the_connector_tools_off(
    oneharness_bin: str, config: str
) -> None:
    """Both Codex identities are handed the switch; no Claude candidate is."""
    args = _resolved(oneharness_bin, config)["harness"]["codex"]["args"]
    assert _carries_pair(args["value"], CONNECTORS_OFF) and args["source"] == config, (
        f"{config} must pass {CONNECTORS_OFF} in its own [harness.codex] args; "
        f"oneharness resolves {args}"
    )

    codex = _identities(oneharness_bin, config, "codex")
    assert codex == ["codex:primary", "codex:alternate"], codex
    for identity in codex:
        command = _planned_command(oneharness_bin, config, identity)
        assert " ".join(CONNECTORS_OFF) in command, f"{identity} under {config}: {command}"
    for identity in _identities(oneharness_bin, config, "claude-code"):
        command = _planned_command(oneharness_bin, config, identity)
        assert "features.apps" not in command, f"{identity} under {config}: {command}"


def test_the_llmlint_role_keeps_its_sandbox_grant_beside_the_switch(oneharness_bin: str) -> None:
    """The switch is appended to llmlint's arguments rather than replacing its grant."""
    args = _resolved(oneharness_bin, "oneharness.llmlint.toml")["harness"]["codex"]["args"]
    assert args["value"][:2] == [
        "-c",
        'sandbox_permissions=["disk-full-read-access","network-full-access"]',
    ], args


@pytest.mark.parametrize("config", WORKER_ROLES + SHARED_PARENTS)
def test_no_worker_role_or_shared_parent_carries_either_setting(
    oneharness_bin: str, config: str
) -> None:
    """Each setting stays on the roles it is a decision about.

    A worker role may state a mode of its own — the follow-up agent's `bypass` is its
    decision — but never a judge's, and a shared parent states none, since every child
    would inherit it.
    """
    own = tomllib.loads((REPO_ROOT / config).read_text(encoding="utf-8"))
    if config in SHARED_PARENTS:
        assert "mode" not in own, f"{config} states a mode: {own['mode']!r}"
    assert own.get("mode") not in JUDGE_MODES, f"{config} states a judge's mode: {own['mode']!r}"
    codex_args = own.get("harness", {}).get("codex", {}).get("args")
    assert not _carries_pair(codex_args, CONNECTORS_OFF), f"{config} carries {codex_args}"

    resolved = _resolved(oneharness_bin, config)
    assert resolved["mode"]["value"] not in JUDGE_MODES, f"{config} resolves {resolved['mode']}"
    resolved_args = resolved["harness"]["codex"]["args"]["value"]
    assert not _carries_pair(resolved_args, CONNECTORS_OFF), f"{config} resolves {resolved_args}"
