"""The tracked `post-checkout` hook provisions a worktree wherever a session opens one.

`onevcs` opens a session with `git worktree add` and reuses a pooled slot with a branch
`checkout`, and both run the hook every harness passes through. This journey runs both,
for real, in a scratch clone carrying this repository's tracked `.githooks/`, whose hook
runs the real `scripts/session-setup.sh --provision-only` in a tree
`tests/e2e/provisioning.py` builds: its real `uv sync` and frozen `bun install` install
the adopted published tools from this host's caches under `NO_NETWORK`, so nothing is
doubled and nothing is downloaded. It shows a file checkout provisions nothing, and that a
failed provisioning leaves the checkout done, says what failed and how to re-run it, and
ends the hook with provisioning's own status, which is what `onevcs` records as the
session's provisioning failure.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest
from offline_installs import NO_NETWORK
from provisioning import setup_environment, setup_repo

from orchestrator.root import REPO_ROOT

#: Every way a caller points uv at an environment, dropped so the hook provisions the
#: checked-out tree's own `.venv` rather than the one running this suite.
PROVIDED_ENVIRONMENT = ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV")
PROVISIONING = "post-checkout: provisioning "
RERUN = "scripts/session-setup.sh --provision-only"
#: The session worktree's directory name, spaced so the re-run command the hook prints has
#: to quote it to be run.
SESSION_DIRECTORY = "session worktree"
#: The status the stand-in setup on `status-seven` exits with: one the real setup never
#: does (it exits 0, 1 or 2), so seeing it proves the hook passed the status through.
DISTINCTIVE_STATUS = 7


class Landed(NamedTuple):
    """What a worktree held once a checkout into it returned."""

    branch: str
    onevcs_pin: str


@dataclass(frozen=True)
class Checkouts:
    """The scratch clone, the session worktree added from it, and what each step said."""

    clone: Path
    session: Path
    file_checkout: subprocess.CompletedProcess[str]
    worktree_add: subprocess.CompletedProcess[str]
    slot_checkout: subprocess.CompletedProcess[str]
    broken_pin: subprocess.CompletedProcess[str]
    after_broken_pin: Landed
    status_seven: subprocess.CompletedProcess[str]
    hook_on_status_seven: subprocess.CompletedProcess[str]


def _git(
    root: Path, env: dict[str, str], *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=Journey", "-c", "user.email=j@example.invalid", *args],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def _commit_branch(clone: Path, env: dict[str, str], branch: str, edit: Path, text: str) -> None:
    """A branch off `main` whose one commit writes `text` to `edit`, back on `main` after.

    Built with the hooks switched off, so making the fixture provisions nothing; every
    checkout the journey asserts on runs with them on.
    """
    off = ("-c", "core.hooksPath=/dev/null")
    _git(clone, env, *off, "switch", "-q", "-c", branch, "main")
    edit.write_text(text, encoding="utf-8")
    _git(clone, env, "add", "-A")
    _git(clone, env, *off, "commit", "-q", "-m", f"fix: {branch}")
    _git(clone, env, *off, "switch", "-q", "main")


@pytest.fixture(scope="module")
def checkouts(tmp_path_factory: pytest.TempPathFactory) -> Checkouts:
    """Every checkout the journey makes, in order, each with the hook on."""
    base = tmp_path_factory.mktemp("post-checkout")
    seed = setup_repo(base, repo=base / "seed")
    shutil.copytree(REPO_ROOT / ".githooks", seed / ".githooks")
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    env = {
        **{
            name: value
            for name, value in setup_environment(base).items()
            if name not in PROVIDED_ENVIRONMENT and not name.startswith("GIT_")
        },
        **NO_NETWORK,
        # The interpreter running this suite. The sandboxed `HOME` hides the interpreters
        # uv manages under the real one, and uv would otherwise take whichever newer one
        # PATH offers first, for which no locked wheel exists.
        "UV_PYTHON": os.path.realpath(sys.executable),
        # No sibling list reaches this host's registered checkouts; `--provision-only`
        # bootstraps none, and this keeps that true should it ever try.
        "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS": str(base / "no-siblings"),
    }
    (base / "no-siblings").write_text("", encoding="utf-8")
    _git(seed, env, "init", "-q", "--initial-branch", "main")
    _git(seed, env, "add", "-A")
    _git(seed, env, "commit", "-q", "--no-verify", "-m", "fix: seed")

    # A clone arrives with no hooks configured, so its own checkout provisioned nothing:
    # every provisioning below is the tracked hook's.
    clone = base / "clone"
    _git(base, env, "clone", "-q", str(seed), str(clone))
    _git(clone, env, "config", "core.hooksPath", ".githooks")
    assert not (clone / ".venv").exists() and not (clone / "node_modules").exists()
    _commit_branch(clone, env, "slot", clone / "README.md", "the slot's next session\n")
    _commit_branch(clone, env, "broken-pin", clone / "config" / "onevcs.version", "not-a-version\n")
    # llmlint: ignore[e2e_not_mocked] The subject here is the hook, not setup: the real setup exits only 0, 1 or 2, so passing a distinctive status through can only be shown by a setup that exits with one; the real setup's own failure is driven unreplaced on `broken-pin`.  # noqa: E501
    _commit_branch(
        clone,
        env,
        "status-seven",
        clone / "scripts" / "session-setup.sh",
        "#!/usr/bin/env bash\n"
        'echo "stand-in setup: the locked install refused" >&2\n'
        f"exit {DISTINCTIVE_STATUS}\n",
    )

    file_checkout = _git(clone, env, "checkout", "HEAD", "--", "README.md", check=False)
    session = base / SESSION_DIRECTORY
    worktree_add = _git(
        clone, env, "worktree", "add", "-q", "-b", "session", str(session), "main", check=False
    )
    slot_checkout = _git(clone, env, "checkout", "-q", "slot", check=False)
    broken_pin = _git(session, env, "checkout", "-q", "broken-pin", check=False)
    after_broken_pin = Landed(
        branch=_head_branch(session),
        onevcs_pin=(session / "config" / "onevcs.version").read_text(encoding="utf-8"),
    )
    status_seven = _git(session, env, "checkout", "-q", "status-seven", check=False)
    # Git reports a failed `post-checkout` as its own status 1 whatever the hook exited
    # with, while `onevcs` reads the hook's own status. So the hook is also run as git runs
    # it — from the tree's root, with the previous head, the new head and the branch flag —
    # to observe the status it exits with.
    head = _git(session, env, "rev-parse", "HEAD").stdout.strip()
    hook_on_status_seven = subprocess.run(
        [str(session / ".githooks" / "post-checkout"), head, head, "1"],
        cwd=session,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return Checkouts(
        clone=clone,
        session=session,
        file_checkout=file_checkout,
        worktree_add=worktree_add,
        slot_checkout=slot_checkout,
        broken_pin=broken_pin,
        after_broken_pin=after_broken_pin,
        status_seven=status_seven,
        hook_on_status_seven=hook_on_status_seven,
    )


def _head_branch(root: Path) -> str:
    return subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _rerun(said: str) -> str:
    """The re-run command a failed hook printed: its last line's text after `Re-run: `."""
    found = re.search(r"Re-run: (.+)$", said, re.MULTILINE)
    assert found is not None, said
    return found.group(1)


def _assert_provisioned(root: Path, ran: subprocess.CompletedProcess[str]) -> None:
    assert ran.returncode == 0, ran.stderr
    assert f"{PROVISIONING}{root}" in ran.stderr, ran.stderr
    assert (root / ".venv" / "bin" / "python").is_file(), ran.stderr
    assert (root / ".venv" / "bin" / "onevcs").is_file(), ran.stderr
    assert (root / "node_modules" / ".bin" / "onepipeline").exists(), ran.stderr
    # `--provision-only`: the checkout's toolchain and none of a session's host steps.
    assert "host sweep" not in ran.stderr
    assert "sibling checkout" not in ran.stderr


def test_a_worktree_add_provisions_the_new_worktree(checkouts: Checkouts) -> None:
    """How `onevcs` opens a session: the new worktree arrives with its toolchain."""
    _assert_provisioned(checkouts.session, checkouts.worktree_add)


def test_a_branch_checkout_provisions_an_existing_worktree(checkouts: Checkouts) -> None:
    """How a pooled slot is reused: the branch checkout provisions the tree it lands in."""
    _assert_provisioned(checkouts.clone, checkouts.slot_checkout)
    assert _head_branch(checkouts.clone) == "slot"


def test_a_file_checkout_provisions_nothing(checkouts: Checkouts) -> None:
    """A file checkout, git's third argument `0`, ran no provisioning: it came first, and
    the clone was still without a toolchain until the branch checkout after it."""
    ran = checkouts.file_checkout
    assert ran.returncode == 0, ran.stderr
    assert PROVISIONING not in ran.stderr
    assert "session-setup:" not in ran.stderr


def test_a_failed_provisioning_completes_the_checkout_and_says_how_to_rerun(
    checkouts: Checkouts,
) -> None:
    """The real setup refusing an unreadable pin: the branch is still checked out, and
    the hook prints the step's own message and the re-run command with its status."""
    ran = checkouts.broken_pin
    assert ran.returncode == 1, ran.stderr
    assert "invalid adopted onevcs version in " in ran.stderr, ran.stderr
    assert "post-checkout: provisioning failed (exit 1)" in ran.stderr, ran.stderr
    assert _rerun(ran.stderr).endswith(f" && {RERUN}"), ran.stderr
    assert checkouts.after_broken_pin == Landed("broken-pin", "not-a-version\n")


def test_the_hook_exits_with_the_status_provisioning_failed_with(checkouts: Checkouts) -> None:
    """A distinctive status from setup is the hook's own exit status, unchanged, and the
    branch checkout that ran it still completed and failed."""
    checkout = checkouts.status_seven
    assert checkout.returncode != 0, checkout.stderr
    assert "stand-in setup: the locked install refused" in checkout.stderr, checkout.stderr
    assert _head_branch(checkouts.session) == "status-seven"

    ran = checkouts.hook_on_status_seven
    assert ran.returncode == DISTINCTIVE_STATUS, ran.stderr
    assert "stand-in setup: the locked install refused" in ran.stderr, ran.stderr
    assert f"post-checkout: provisioning failed (exit {DISTINCTIVE_STATUS})" in ran.stderr


def test_the_rerun_command_runs_provisioning_in_the_worktree_from_anywhere(
    checkouts: Checkouts,
) -> None:
    """The printed command, run from another directory, reaches that worktree's setup even
    though the worktree's path holds a space: it runs the stand-in, which exits 7 again."""
    command = _rerun(checkouts.hook_on_status_seven.stderr)
    assert " " in str(checkouts.session)

    rerun = subprocess.run(
        ["bash", "-c", command],
        cwd=checkouts.clone,
        text=True,
        capture_output=True,
        check=False,
    )

    assert rerun.returncode == DISTINCTIVE_STATUS, (command, rerun.stderr)
    assert "stand-in setup: the locked install refused" in rerun.stderr, rerun.stderr
