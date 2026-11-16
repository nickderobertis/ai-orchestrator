"""Real-CLI coverage for the settings this host makes about its supervisory turns.

Both onejudge judge sides state `read-only` at their own top level, never a planning mode,
and leave what that mode withholds to oneharness: Codex is handed its read-only sandbox by
the mode and by no judge file, and Claude Code is handed the mode's own reading tool set,
beside the one remaining fill, `--strict-mcp-config`. The dispatch judge alone adds one
permission, a run-level `allowed_tools` naming the decline command, which oneharness applies
to each judge run alone: the absolute path `$ORCHESTRATOR_DECLINE_RULING` names becomes the
only command the turn may run, on Claude Code's per-run flags and on Codex's per-run overlay
home, and the design-document judge carries none. Every supervisory Codex role switches
the ChatGPT-app connector tools off; no worker role or shared parent carries any of it. Why
is `docs/onejudge-integration.md`'s, "The mode a judge runs in". The role files are the one
source of each setting: this module asks the real `oneharness` what each resolves to,
attributed to that file, and what argv a dry run of each identity would build — never a
paid turn. The argv a mode builds is oneharness's, so it is read off the CLI rather than
restated here, and a release that changes it shows up as a changed argv below.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Not expensive: the
module's 21 journeys each ask the installed `oneharness` for a resolved config, a dry-run
argv or its harness list — no provider turn, no launch, no network — and none takes more
than a fraction of a second (`pytest --durations`), so a project of its own would buy no
skipped work.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import tomllib
from typing import Any

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The judge sides onejudge frames, each named by the wiring that dispatches it.
JUDGE_ROLES = ("oneharness.judge.toml", "oneharness.design-doc-judge.toml")

#: The judge that runs inside a node's dispatch, and the one run-level grant it states: the
#: decline command, written as the variable every dispatch inherits.
GRANTED_JUDGE = "oneharness.judge.toml"
DECLINE_GRANT = ["Bash($ORCHESTRATOR_DECLINE_RULING:*)"]

#: The design-document judge, which runs in `just plan`'s flow rather than a dispatch and
#: is granted nothing beyond reading.
UNGRANTED_JUDGE = "oneharness.design-doc-judge.toml"

#: The real decline wrapper, as every dispatch's `ORCHESTRATOR_DECLINE_RULING` names it:
#: the value a judge run resolves the grant's variable to.
DECLINE_WRAPPER = str(REPO_ROOT / "scripts" / "decline-ruling.sh")

#: The mode each judge file states for itself, never inherits, and oneharness enforces per
#: harness: a sandbox on a Codex turn, a reading tool set on a Claude Code one.
JUDGE_MODE = "read-only"

#: The modes that put a planning instruction or a planning mode in front of every turn.
PLANNING_MODES = {"plan"}

#: Codex's read-only sandbox as the mode passes it.
MODE_SANDBOX = "--sandbox read-only"

#: The one switch a judge file adds to the Claude Code turn the mode builds: it loads no
#: MCP server, which the mode itself still loads under `bypassPermissions`.
NO_MCP_SERVERS = "--strict-mcp-config"

#: The read-only mode's Claude Code tool set at the pinned oneharness, web reads included
#: by the manager's ruling. Each judge's dry-run argv is read for the tools it is offered
#: and compared with this, so a oneharness release that widens or narrows the set fails
#: here rather than reaching every judge unread.
READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]

#: The Codex roles whose turn is not a worker's.
CONNECTOR_ROLES = (
    "oneharness.judge.toml",
    "oneharness.orchestrator.toml",
    "oneharness.pr-author.toml",
    "oneharness.review-reply.toml",
    "oneharness.design-doc-judge.toml",
    "oneharness.llmlint.toml",
)

#: codex-cli's switch for the ChatGPT-app connector tools, as the argv pair codex reads.
CONNECTORS_OFF = ["-c", "features.apps=false"]

#: The roles whose turn is a worker's, and the parents nothing runs against.
WORKER_ROLES = (
    "oneharness.toml",
    "oneharness.board-live.toml",
    "oneharness.follow-up.toml",
    "oneharness.design-doc.toml",
)
SHARED_PARENTS = ("oneharness.identities.toml", "oneharness.dispatch.toml")


def _environment() -> dict[str, str]:
    """The suite's environment less `ONEHARNESS_*`, whose overrides beat every file.

    A worker runs this suite from inside a dispatch, so an inherited override would be
    read in place of the file under test. `ORCHESTRATOR_DECLINE_RULING` is set to this
    checkout's wrapper, as every dispatch's environment sets it, because the dispatch
    judge's grant resolves through it and a run with it unset is refused before spawning.
    `ONEPIPELINE_NODE_SCRATCH_DIR` is set for the same reason: every dispatch is handed one,
    and the dispatched roles' variants take `XDG_RUNTIME_DIR` from it, so a run without it is
    refused too. Every run here is a `config`, `list` or `--print-command` read that spawns
    nothing and writes nothing there, so the temporary directory stands in for it.
    """
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")
    }
    environment["ORCHESTRATOR_DECLINE_RULING"] = DECLINE_WRAPPER
    environment["ONEPIPELINE_NODE_SCRATCH_DIR"] = tempfile.gettempdir()
    return environment


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


def _planned(oneharness_bin: str, config: str, harness_id: str) -> dict[str, Any]:
    """The one result a dry run of `config` plans for `harness_id`: its argv, as a list, and
    the environment a per-run overlay would set on the harness — spending nothing."""
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
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=_environment(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert proc.returncode == 0, f"{config} did not plan {harness_id}: {proc.stderr}"
    results: list[dict[str, Any]] = json.loads(proc.stdout)["results"]
    assert len(results) == 1, f"{config} planned {results} for {harness_id}"
    return results[0]


def _planned_command(oneharness_bin: str, config: str, harness_id: str) -> str:
    """The argv a run of `config` would hand `harness_id`, as one string."""
    return " ".join(_planned(oneharness_bin, config, harness_id)["command"])


def _grants_run_allowlists(oneharness_bin: str, harness: str) -> bool:
    """Whether the pinned release applies a run-level allowlist on `harness` on this platform,
    as `oneharness list` reports it; a harness that does not withholds the grant."""
    proc = subprocess.run(
        [oneharness_bin, "list", "--format", "json"],
        cwd=REPO_ROOT,
        env=_environment(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert proc.returncode == 0, proc.stderr
    listed = {entry["id"]: entry for entry in json.loads(proc.stdout)["harnesses"]}
    supported = listed[harness]["supports_run_allowed_tools"]
    assert isinstance(supported, bool), listed[harness]
    return supported


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


def _flag_values(command: list[str], flag: str) -> list[str] | None:
    """The words a planned argv names after its one `flag`, or `None` when it has none."""
    if flag not in command:
        return None
    assert command.count(flag) == 1, f"expected at most one {flag} in: {command}"
    values: list[str] = []
    for word in command[command.index(flag) + 1 :]:
        if word.startswith("-"):
            break
        values.append(word)
    return values


def _offered_tools(command: list[str]) -> list[str]:
    """The tools a planned Claude Code argv names after its one `--tools` flag."""
    offered = _flag_values(command, "--tools")
    assert offered is not None, f"expected exactly one --tools in: {command}"
    return offered


@pytest.mark.parametrize("config", JUDGE_ROLES)
def test_every_judge_runs_read_only_from_its_own_file(oneharness_bin: str, config: str) -> None:
    """The mode is the judge file's, resolves as that file's, and is never a planning mode."""
    declared = tomllib.loads((REPO_ROOT / config).read_text(encoding="utf-8")).get("mode")
    assert declared not in PLANNING_MODES, (
        f"{config} states the planning mode {declared!r}; this config also drives the "
        "simulated user, which would then frame the worker's next turn as a plan"
    )
    assert declared == JUDGE_MODE, (
        f"{config} must state mode = {JUDGE_MODE!r} at its own top level; it states {declared!r}"
    )

    mode = _resolved(oneharness_bin, config)["mode"]
    assert mode == {"value": JUDGE_MODE, "source": config}, (
        f"{config} declares mode {declared!r} but oneharness resolves {mode}"
    )


@pytest.mark.parametrize("config", JUDGE_ROLES)
def test_every_codex_judge_takes_its_read_only_sandbox_from_the_mode(
    oneharness_bin: str, config: str
) -> None:
    """Codex's argv carries the mode's sandbox, once, and the judge file pins none of its own."""
    args = _resolved(oneharness_bin, config)["harness"]["codex"]["args"]
    own: list[str] = args["value"] or []
    assert not any("sandbox" in word or word == "-s" for word in own), (
        f"{config} pins a sandbox by hand in its [harness.codex] args: {own}"
    )

    codex = _identities(oneharness_bin, config, "codex")
    assert codex, f"{config} names no Codex identity to hold to the sandbox"
    granted = config == GRANTED_JUDGE and _grants_run_allowlists(oneharness_bin, "codex")
    for identity in codex:
        planned = _planned(oneharness_bin, config, identity)
        command = " ".join(planned["command"])
        # The grant reaches Codex only as a per-run overlay home whose `rules/` holds the
        # decline command's prefix rule; the argv, and so the sandbox, is unchanged.
        overlay = planned.get("overlay_env")
        if granted:
            assert overlay == {
                "CODEX_HOME": "<run-overlay>/codex-home",
                "ONEHARNESS_RUN_OVERLAY": "<run-overlay>",
            }, f"{identity} under {config}: {overlay}"
        else:
            assert overlay is None, f"{identity} under {config} is overlaid: {overlay}"
        assert MODE_SANDBOX in command, f"{identity} under {config}: {command}"
        assert command.count("--sandbox") == 1, f"{identity} under {config}: {command}"
        assert "sandbox_mode=" not in command, f"{identity} under {config}: {command}"
        assert "--dangerously-bypass-approvals-and-sandbox" not in command, command
        assert "workspace-write" not in command, command
        assert "danger-full-access" not in command, command


@pytest.mark.parametrize("config", JUDGE_ROLES)
def test_every_claude_judge_is_offered_the_read_only_tool_set_and_no_mcp_server(
    oneharness_bin: str, config: str
) -> None:
    """Claude Code gets the mode's reading tools, nothing more, and no MCP server.

    The judge file states only `--strict-mcp-config`, because the mode still loads MCP
    servers under `bypassPermissions` and their tools would run unprompted.
    """
    args = _resolved(oneharness_bin, config)["harness"]["claude-code"]["args"]
    assert args == {"value": [NO_MCP_SERVERS], "source": config}, (
        f"{config} must state exactly [{NO_MCP_SERVERS!r}] in its own [harness.claude-code] "
        f"args; oneharness resolves {args}"
    )

    claude = _identities(oneharness_bin, config, "claude-code")
    assert claude, f"{config} names no Claude Code identity to hold to its tools"
    granted = config == GRANTED_JUDGE and _grants_run_allowlists(oneharness_bin, "claude-code")
    for identity in claude:
        planned = _planned(oneharness_bin, config, identity)
        command = planned["command"]
        assert planned.get("overlay_env") is None, f"{identity} under {config}: {planned}"
        assert command.count(NO_MCP_SERVERS) == 1, f"{identity} under {config}: {command}"
        assert not {"Write", "Edit", "MultiEdit", "NotebookEdit"} & set(command), command
        if granted:
            # The grant's one shell: `Bash` is offered, but `dontAsk` refuses every call no
            # `--allowedTools` rule names, and the only `Bash` rule is the decline wrapper's
            # absolute path — never a bare `Bash`, and never the variable's text.
            assert _offered_tools(command) == [*READ_ONLY_TOOLS, "Bash"], command
            assert _flag_values(command, "--permission-mode") == ["dontAsk"], command
            assert _flag_values(command, "--allowedTools") == [
                *READ_ONLY_TOOLS,
                f"Bash({DECLINE_WRAPPER}:*)",
            ], f"{identity} under {config}: {command}"
        else:
            assert _offered_tools(command) == READ_ONLY_TOOLS, f"{identity} under {config}"
            assert _flag_values(command, "--permission-mode") == ["bypassPermissions"], command
            assert "--allowedTools" not in command, f"{identity} under {config}: {command}"
    for identity in _identities(oneharness_bin, config, "codex"):
        command = _planned_command(oneharness_bin, config, identity)
        assert "--tools" not in command, f"{identity} under {config}: {command}"
        assert NO_MCP_SERVERS not in command, f"{identity} under {config}: {command}"


def test_only_the_dispatch_judge_states_a_run_level_grant_and_no_judge_a_sync_policy(
    oneharness_bin: str,
) -> None:
    """The dispatch judge's file states exactly the decline command as its run-level
    allowlist; the design-document judge resolves to none; neither carries any `[sync]` field.
    """
    granted = _resolved(oneharness_bin, GRANTED_JUDGE)
    assert granted["allowed_tools"] == {"value": DECLINE_GRANT, "source": GRANTED_JUDGE}, (
        f"{GRANTED_JUDGE} must state exactly {DECLINE_GRANT} as its own run-level "
        f"allowed_tools; oneharness resolves {granted['allowed_tools']}"
    )
    ungranted = _resolved(oneharness_bin, UNGRANTED_JUDGE)
    assert ungranted["allowed_tools"]["value"] is None, (
        f"{UNGRANTED_JUDGE} grants {ungranted['allowed_tools']}; the design-document judge "
        "is granted nothing beyond reading"
    )
    for config, resolved in ((GRANTED_JUDGE, granted), (UNGRANTED_JUDGE, ungranted)):
        for name, harness in resolved["harness"].items():
            assert harness.get("allowed_tools", {}).get("value") is None, (config, name)
            for variant, fields in harness.get("variant", {}).items():
                assert fields.get("allowed_tools", {}).get("value") is None, (config, variant)
        sync = resolved["sync"]
        assert sync["harness"] == {}, f"{config} resolves a [sync] policy: {sync}"
        assert all(field["value"] is None for key, field in sync.items() if key != "harness"), (
            f"{config} resolves a [sync] policy: {sync}"
        )


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
def test_no_worker_role_or_shared_parent_carries_any_judge_setting(
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
    assert own.get("mode") != JUDGE_MODE, f"{config} states a judge's mode: {own['mode']!r}"
    codex_args = own.get("harness", {}).get("codex", {}).get("args")
    claude_args = own.get("harness", {}).get("claude-code", {}).get("args") or []
    assert not _carries_pair(codex_args, CONNECTORS_OFF), f"{config} carries {codex_args}"
    assert NO_MCP_SERVERS not in claude_args, f"{config} carries {claude_args}"

    resolved = _resolved(oneharness_bin, config)
    assert resolved["mode"]["value"] != JUDGE_MODE, f"{config} resolves {resolved['mode']}"
    resolved_args = resolved["harness"]["codex"]["args"]["value"]
    assert not _carries_pair(resolved_args, CONNECTORS_OFF), f"{config} resolves {resolved_args}"
    resolved_claude = resolved["harness"]["claude-code"]["args"]["value"] or []
    assert NO_MCP_SERVERS not in resolved_claude, f"{config} resolves {resolved_claude}"
