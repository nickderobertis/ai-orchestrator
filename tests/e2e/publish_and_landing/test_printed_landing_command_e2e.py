"""The landing command the `Stop` hook prints opens a change request carrying a drafted body.

A manager stopped by the hook over a preserved branch is handed a command to land it. A raw
`onevcs publish-branch` opens a change request with an empty description, so the hook names
this repository's drafter graph and the engine prints `onepipeline publish-branch …
--pr-author-graph <graphs/pr-author.yaml>` instead. This journey takes the command the
declared hook printed and runs it **exactly as printed** — through `sh -c`, with this
checkout's locked `.venv/bin` on the search path, which is where a manager's session finds
`onepipeline` — and reads the change request it opened.

Everything below the remote host and the paid provider is real: the hook command as
`.claude/settings.json` declares it, the engine, `onevcs`, git, a real labelled session and
the real `graphs/pr-author.yaml` through the real `oneagentgraph`. Two things are
substituted at their published seams, exactly as `test_publish_branch_e2e.py` substitutes
them: the remote host's decisioning is `tests/e2e/fake_gh.py`, named by `ONEVCS_GH`, and the
paid provider is `tests/e2e/fake_codex.py`, named by `ONEHARNESS_BIN_CODEX` with the paid
identities guarded off the search path.

Which Nx project owns this file is the finding the three directives below answer:
`publish-and-landing`, the directory-owned project keyed on the recipes, scripts and configs
its landing journeys drive, which this one shares with its neighbours there, and which
declares no marker tier.
"""

# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see the docstring
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see the docstring
# llmlint: ignore-file[shell_test_tiers_stay_split] see the docstring

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import short_state
from harness_indirections import established_indirections
from nx_workspace import WORKSPACE_INSTALL_MARKS
from project_fixtures import helper
from unpublished_registry import BASE, UNPOOLED, Registry, git
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A reader of the toolchain this checkout provisioned: every step here runs its tool
#: through a `just` recipe, the installed engine or `uv run`, and no writer touches it.
pytestmark = list(WORKSPACE_INSTALL_MARKS)

FAKE_GH = helper("fake_gh.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
INDIRECTION_CALLER = "tests/e2e/publish_and_landing/test_printed_landing_command_e2e.py"

#: A GitHub origin, so the identity has a host and a change request to open; git still
#: pushes to the throwaway bare origin on disk.
HOSTED_ORIGIN = "https://github.com/acme-corp/hosted.git"
#: The manager session whose run left the branch.
MANAGER = "manager-whose-run-left-a-branch"
#: What the substituted provider answers the drafting turn with.
DRAFTED_BODY = "## What\n\nThe preserved work, landed.\n\n## Why\n\nThe hook named it."
DRAFTER = REPO_ROOT / "graphs" / "pr-author.yaml"


def _hosted(root: Path) -> Registry:
    """A `change-open` identity under a scratch registry, with the drafter's seams set."""
    home = root / "onevcs-home"
    home.mkdir()
    (home / "workspaces.yml").write_text(UNPOOLED, encoding="utf-8")
    (home / "rules.yml").write_text(
        "version: 3\n"
        "trailer_prefix: Orchestrator-\n"
        "rules: []\n"
        "default:\n"
        "  publication: change-open\n"
        "  approvals: required\n",
        encoding="utf-8",
    )
    seed = root / "seed"
    git("init", "-q", "-b", BASE, str(seed), cwd=root)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "init", cwd=seed)
    origin = root / "origin.git"
    git("clone", "-q", "--bare", str(seed), str(origin), cwd=root)
    checkout = root / "checkout"
    git("clone", "-q", str(origin), str(checkout), cwd=root)

    gh_state = root / "gh-state"
    gh_state.mkdir()
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ("ONEPIPELINE_LAUNCHER", "ONEPIPELINE_LAUNCHER_SESSION")
    }
    environment["ONEVCS_HOME"] = str(home)
    # llmlint: ignore[e2e_not_mocked] Only the remote host's decisioning, at onevcs's seam.
    environment["ONEVCS_GH"] = str(FAKE_GH)
    environment["FAKE_GH_STATE"] = str(gh_state)
    environment["FAKE_GH_CALLS"] = str(root / "gh-calls.jsonl")
    environment["FAKE_GH_ORIGIN"] = str(origin)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = (
        f"{PAID_PROVIDER_GUARD}{os.pathsep}{REPO_ROOT / '.venv' / 'bin'}"
        f"{os.pathsep}{environment['PATH']}"
    )
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps({"body": DRAFTED_BODY})])
    environment["FAKE_CODEX_PROMPT_LOG"] = str(root / "prompts.jsonl")
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(root / "launches")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    environment.update(established_indirections(INDIRECTION_CALLER))
    registry = Registry(home=home, checkout=checkout, identity="", environment=environment)
    registered = registry.run("just", "register-repo", str(checkout), "--origin", HOSTED_ORIGIN)
    assert registered.returncode == 0, registered.stderr + registered.stdout
    return registry


def _hook(registry: Registry) -> str:
    """The declared `Stop` command, run as Claude Code runs it; the reason it blocked with."""
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    [command] = [hook["command"] for entry in settings["hooks"]["Stop"] for hook in entry["hooks"]]
    payload = {"session_id": MANAGER, "hook_event_name": "Stop", "stop_hook_active": False}
    answered = subprocess.run(
        ["/bin/sh", "-c", command],
        input=json.dumps(payload),
        cwd=REPO_ROOT,
        env={**registry.environment, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert answered.returncode == 0, answered
    decision = json.loads(answered.stdout)
    assert decision["decision"] == "block", decision
    return str(decision["reason"])


def test_the_landing_command_the_hook_prints_opens_the_change_request_with_the_drafted_body(
    tmp_path: Path,
) -> None:
    registry = _hosted(tmp_path)
    session = registry.open_session(labels={"run": "run-1", "node": "build", "launcher": MANAGER})
    registry.close_session(session)

    reason = _hook(registry)
    [landing] = [
        line.split("land it:", 1)[1].strip()
        for line in reason.splitlines()
        if line.strip().startswith("land it:")
    ]
    assert landing == (
        f"onepipeline publish-branch {session.branch} --repo {registry.checkout} "
        f"--pr-author-graph {DRAFTER}"
    ), reason

    landed = subprocess.run(
        ["/bin/sh", "-c", landing],
        cwd=tmp_path,
        env=registry.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    assert landed.returncode == 0, landed.stderr + landed.stdout

    [record] = sorted((tmp_path / "gh-state").glob("pr-*.json"))
    change = json.loads(record.read_text(encoding="utf-8"))
    body = record.with_suffix(".body")
    assert body.read_text(encoding="utf-8") == DRAFTED_BODY, (
        "the change request the printed command opened does not carry the drafted body"
    )
    assert change["url"] in landed.stdout, landed.stdout
    prompts = (tmp_path / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(prompts) == 1, "the printed command spent exactly one drafting turn"
