"""The real `pre-push` hook in a checkout nothing provisioned: a publication's worktree.

A publication runs this repository's hook in a worktree `onevcs` cut for it, which no
session start ever ran in, and `scripts/nx.sh` installs nothing. So the hook provisions
the checkout through session setup's own provisioning before the gate. This journey runs
the tracked hook in a copy that arrives with neither `.venv` nor `node_modules`. It shows
the gate is reached over a toolchain that setup built, with every plan-store root its
budgets read, and that none of the session's host steps ran.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_workspace import copy_working_tree, missing_plan_roots, read_a_plan_graph
from offline_installs import NO_NETWORK

#: Every way a caller points uv at an environment, or the hook at a comparison base,
#: dropped so the hook provisions the copy's own and compares against the base named here.
PROVIDED_ENVIRONMENT = (
    "UV_NO_SYNC",
    "UV_PROJECT_ENVIRONMENT",
    "VIRTUAL_ENV",
    "ORCHESTRATOR_COMPARISON_REMOTE",
    "ORCHESTRATOR_COMPARISON_BASE",
    "ONEVCS_COMPARISON_REMOTE",
    "ONEVCS_COMPARISON_BASE",
)
#: What the stand-in `llmlint` says when the gate's first step reaches it. `llmlint` is a
#: published CLI the gate delegates to, doubled at that boundary so the gate ends at its
#: first step rather than spending a judged turn.
LLMLINT_REFUSAL = "stand-in llmlint refuses"


@dataclass(frozen=True)
class Pushed:
    """The copy the hook ran in, and what the hook said."""

    root: Path
    ran: subprocess.CompletedProcess[str]
    env: dict[str, str]


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Journey", "-c", "user.email=j@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def pushed(tmp_path_factory: pytest.TempPathFactory) -> Pushed:
    """A copy of this checkout's tracked files, with no toolchain, after its real hook ran.

    The copy is committed and pushed to an origin of its own, so the hook compares the
    branch against a base it can resolve and finds no launch path changed, as a
    publication of a branch level with its base would. `UV_NO_SYNC=1` is in force, as it
    is under every recipe that publishes. The copy keeps this host's home, so its installs
    come from the host's caches and reach no index (`NO_NETWORK`), and it names its own
    state, caches and an empty sibling list.
    """
    base = tmp_path_factory.mktemp("pre-push")
    root = base / "checkout"
    root.mkdir()
    copy_working_tree(root)
    _git(root, "init", "-q", "--initial-branch", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--no-verify", "-m", "seed")
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True)
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "--no-verify", "origin", "main")
    _git(root, "fetch", "-q", "origin")
    assert not (root / ".venv").exists() and not (root / "node_modules").exists()
    assert missing_plan_roots(root), "the copy arrived holding every plan root"

    tools = base / "tools"
    tools.mkdir()
    # llmlint: ignore[e2e_not_mocked] The subject is the hook's provisioning, which runs real; `llmlint` is the published CLI the gate's next step delegates to, whose judged tier is a paid model turn, and AGENTS.md's "Tests are realistic" doubles exactly that boundary, so the gate ends there.  # noqa: E501
    llmlint = tools / "llmlint"
    llmlint.write_text(f'#!/bin/sh\necho "{LLMLINT_REFUSAL}: $*" >&2\nexit 1\n', encoding="utf-8")
    llmlint.chmod(0o755)
    listing = base / "checkouts"
    listing.write_text("", encoding="utf-8")
    for directory in ("oneagentgraph-state", "tmp"):
        (base / directory).mkdir()
    environment = {
        **{name: value for name, value in os.environ.items() if name not in PROVIDED_ENVIRONMENT},
        "UV_NO_SYNC": "1",
        **NO_NETWORK,
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "ORCHESTRATOR_COMPARISON_BASE": "main",
        "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS": str(listing),
        "XDG_CACHE_HOME": str(base / "cache"),
        "XDG_STATE_HOME": str(base / "state"),
        "ONEVCS_HOME": str(base / "onevcs-home"),
        "ONEAGENTGRAPH_STATE_DIR": str(base / "oneagentgraph-state"),
        "TMPDIR": str(base / "tmp"),
        "UV_TOOL_DIR": str(base / "uv-tools"),
        "UV_TOOL_BIN_DIR": str(base / "uv-tool-bin"),
        "NX_DAEMON": "false",
    }
    ran = subprocess.run(
        [str(root / ".githooks" / "pre-push"), "origin", str(origin)],
        cwd=root,
        env=environment,
        input="",
        text=True,
        capture_output=True,
        check=False,
    )
    return Pushed(root=root, ran=ran, env=environment)


def test_the_hook_provisions_an_unprovisioned_worktree_before_its_gate(pushed: Pushed) -> None:
    """The gate is reached, over a `.venv` and `node_modules` the hook's setup built."""
    root = pushed.root
    said = pushed.ran.stdout + pushed.ran.stderr

    # The gate ran, and ended at its first step: the stand-in llmlint's refusal.
    assert pushed.ran.returncode != 0, said
    assert f"{LLMLINT_REFUSAL}: validate" in pushed.ran.stderr, said
    assert "gate: llmlint validate failed" in pushed.ran.stderr, said
    # It did not end on the toolchain it found missing.
    assert "is not provisioned" not in said
    assert "no onebudgetspec is installed" not in said

    assert (root / ".venv" / "bin" / "python").is_file()
    assert os.access(root / ".venv" / "bin" / "onebudgetspec", os.X_OK)
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    reported = subprocess.run(
        [str(root / "node_modules" / ".bin" / "nx"), "--version"],
        cwd=root,
        env=pushed.env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reported.returncode == 0, reported.stderr
    assert f"Local: v{manifest['devDependencies']['nx']}" in reported.stdout, reported.stdout


def test_the_hooks_provisioning_runs_none_of_the_sessions_host_steps(pushed: Pushed) -> None:
    """A publication's hook provisions its checkout and starts no host sweep or sibling
    bootstrap, as a session start would."""
    stderr = pushed.ran.stderr

    assert "host-sweep:" not in stderr
    assert "host sweep" not in stderr
    assert "sibling checkout" not in stderr
    assert "repos-bootstrap" not in stderr


def test_the_hooks_provisioning_leaves_every_plan_root_its_budgets_read(pushed: Pushed) -> None:
    """Every `local-md` root `onetaskgraph.yaml` configures exists once the hook provisioned,
    so a gate budget reading the plan store measures rather than errors.

    The store refuses every read while any configured root is absent, which is how a
    publication of this repository was once refused: its worktree held no
    `.follow-ups/runs`, and `orchestrator:budgets` errored on the design-document graph's
    budgets. The read here is the one those budgets make, by the copy's own store CLI.
    """
    missing = missing_plan_roots(pushed.root)
    assert not missing, f"configured plan roots left missing: {missing}"
    read = read_a_plan_graph(pushed.root, pushed.env)
    assert read.returncode == 0, read.stdout + read.stderr
