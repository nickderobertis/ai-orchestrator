"""A shebang recipe of the root justfile runs whatever runtime directory the caller inherits.

`just` writes a shebang recipe's body to a file under its temporary directory and
executes it, and with nothing configured that directory is the caller's
`XDG_RUNTIME_DIR`. Harness turns remap that variable to node scratch, but the
publication gate runs in the publishing process's inherited environment, and when the
inherited directory was a `noexec` mount every shebang recipe failed with `Permission
denied (os error 13)` — 110 tests of `channel-reply` refused on one publication of a
branch nothing was wrong with. The justfile's `set tempdir` names a directory this
repository owns instead, and the justfile creates that directory itself on every
recipe run — `just` does not — so it exists wherever this justfile runs from: a
checkout, a worktree, a clone carrying no `.logs`, or a copy of the file alone.

Nothing here is doubled: the journeys drive the real `just` over the real justfile, and
the inherited runtime directory is a real `noexec` mount. This host permits an
unprivileged user namespace, so each journey mounts a `tmpfs` with `noexec` inside
`unshare -rm` — the one way a process without root can produce such a mount — and every
run under it is paired with a control that proves the mount is what makes the difference:
the same recipe from the same justfile with the setting removed, refused for the reason
the publication was.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] Every
journey here is `reads_recipes`: it drives the real justfile and reads nothing of this
repository but the justfile and `.gitignore`, both of which `recipeWorkspace` keys, and a
project of its own for one setting would be a second key naming the same files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_recipes

#: The one shebang recipe the root justfile carries, and the argument that makes its
#: body speak before it reaches anything else: an envelope file that does not exist is
#: refused by the recipe's own first read, with its own message and exit status, so
#: seeing that refusal is seeing the body execute. A body `just` could not execute never
#: gets that far and fails with `just`'s own error instead.
SHEBANG_RECIPE = ("channel-reply", "journey-run", "/nonexistent-envelope")
BODY_RAN = "channel-reply: the envelope file '/nonexistent-envelope' could not be read"
BODY_RAN_STATUS = 2
#: What the publication gate reported when its inherited runtime directory was `noexec`.
NOEXEC_REFUSAL = "Permission denied (os error 13)"
#: The setting under test, as the justfile spells it, and the directory it names.
TEMPDIR_SETTING = 'set tempdir := ".logs/just"'
TEMPDIR = Path(".logs/just")
#: The assignment that creates the directory, as the justfile spells it. A top-level
#: backtick runs on every invocation that runs a recipe, before any body, in the working
#: directory the setting resolves against — the one place in a justfile that can create
#: a directory `just` itself will not.
CREATES_TEMPDIR = "_create_recipe_tempdir := `mkdir -p .logs/just 2>/dev/null || true`"


def _just_environment() -> dict[str, str]:
    """The inherited environment without `JUST_TEMPDIR`, which `just` prefers to the setting.

    A variable naming the temporary directory outranks `set tempdir`, so where the caller
    exports one — Claude Code's shell exports `/tmp`, and a publication launched from such
    a session inherits it — the body is written there whatever the justfile says, and
    every control below runs its body for that reason alone. Dropping it is what leaves
    the justfile's setting, and nothing else, deciding where the body is written.
    """
    return {name: value for name, value in os.environ.items() if name != "JUST_TEMPDIR"}


def _requires_user_namespace() -> None:
    """Skip where this host cannot produce a `noexec` mount without root.

    Named rather than silently passing: a journey that could not mount `noexec` would
    prove nothing about the refusal it exists to prevent, and a host that disables
    unprivileged user namespaces is one this journey cannot run on at all.
    """
    if shutil.which("unshare") is None:
        pytest.skip("no `unshare` on PATH; the noexec mount needs a user namespace")
    probe = subprocess.run(["unshare", "-rm", "true"], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip(
            "this host refuses an unprivileged user namespace, so no noexec tmpfs can be "
            f"mounted for the journey: {probe.stderr.strip()}"
        )


def _run_under_noexec_runtime_dir(
    justfile_dir: Path, mount: Path
) -> subprocess.CompletedProcess[str]:
    """Run the shebang recipe with `XDG_RUNTIME_DIR` a freshly mounted `noexec` tmpfs.

    One shell inside the namespace mounts, then executes `just` with the runtime
    directory pointed at the mount, from the justfile's own directory — the way a
    publication runs the gate: an inherited environment, and no export of its own.
    """
    mount.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            "unshare",
            "-rm",
            "bash",
            "-c",
            'mount -t tmpfs -o noexec none "$1" && cd "$2" && '
            'XDG_RUNTIME_DIR="$1" exec just "${@:3}"',
            "noexec-journey",
            str(mount),
            str(justfile_dir),
            *SHEBANG_RECIPE,
        ],
        text=True,
        capture_output=True,
        env={**_just_environment(), "NO_COLOR": "1"},
    )


def _justfile_copy(directory: Path, *, with_setting: bool) -> Path:
    """The real justfile in a directory of its own, with or without the setting."""
    directory.mkdir(parents=True, exist_ok=True)
    text = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
    assert TEMPDIR_SETTING in text, "the root justfile no longer names its tempdir"
    assert CREATES_TEMPDIR in text, "the root justfile no longer creates its tempdir"
    if not with_setting:
        text = text.replace(f"{TEMPDIR_SETTING}\n", "", 1)
        text = text.replace(f"{CREATES_TEMPDIR}\n", "", 1)
    (directory / "justfile").write_text(text, encoding="utf-8")
    return directory


def test_the_shebang_recipe_runs_when_the_inherited_runtime_directory_is_noexec(
    tmp_path: Path,
) -> None:
    """The real checkout's recipe body executes; the same body without the setting does not."""
    _requires_user_namespace()

    ran = _run_under_noexec_runtime_dir(REPO_ROOT, tmp_path / "runtime")

    assert ran.returncode == BODY_RAN_STATUS, ran.stdout + ran.stderr
    assert BODY_RAN in ran.stderr, ran.stderr
    assert NOEXEC_REFUSAL not in ran.stderr

    # The control: the mount is what refuses a body written under it, and the setting
    # is the whole of what keeps the body out of it.
    control = _justfile_copy(tmp_path / "without-setting", with_setting=False)
    refused = _run_under_noexec_runtime_dir(control, tmp_path / "control-runtime")

    assert refused.returncode != BODY_RAN_STATUS, refused.stdout + refused.stderr
    assert NOEXEC_REFUSAL in refused.stderr, refused.stderr
    assert BODY_RAN not in refused.stderr


def test_a_bare_copy_of_the_justfile_creates_the_directory_and_runs_the_recipe(
    tmp_path: Path,
) -> None:
    """The justfile alone, in a directory holding nothing else, runs its shebang recipe.

    The directory the setting names is created by the justfile on the way to the body,
    not found: this is the state every journey that copies the justfile beside its own
    fixtures runs in, and the one a tracked placeholder could never reach — twelve
    delegation journeys refused `channel-reply` naming this path before the body ran.
    """
    checkout = _justfile_copy(tmp_path / "checkout", with_setting=True)
    assert not (checkout / ".logs").exists()

    ran = subprocess.run(
        ["just", *SHEBANG_RECIPE],
        cwd=checkout,
        env=_just_environment(),
        text=True,
        capture_output=True,
    )

    assert ran.returncode == BODY_RAN_STATUS, ran.stderr
    assert BODY_RAN in ran.stderr, ran.stderr
    assert (checkout / TEMPDIR).is_dir()
    # `just` removes the temporary directory it made for the body once the body has
    # run, so the directory the setting names holds nothing a tree would notice.
    assert list((checkout / TEMPDIR).iterdir()) == []

    # The control says the assignment is what creates it: the setting alone, in the
    # same bare directory, fails naming the path — `just` creates nothing itself.
    control = _justfile_copy(tmp_path / "setting-alone", with_setting=True)
    setting_alone = (control / "justfile").read_text(encoding="utf-8")
    (control / "justfile").write_text(
        setting_alone.replace(f"{CREATES_TEMPDIR}\n", "", 1), encoding="utf-8"
    )
    refused = subprocess.run(
        ["just", *SHEBANG_RECIPE],
        cwd=control,
        env=_just_environment(),
        text=True,
        capture_output=True,
    )
    assert refused.returncode != BODY_RAN_STATUS, refused.stderr
    assert str(control / TEMPDIR) in refused.stderr, refused.stderr
    assert BODY_RAN not in refused.stderr


def test_the_directory_follows_the_working_directory_the_setting_resolves_against(
    tmp_path: Path,
) -> None:
    """Run with `--working-directory`, the directory is created where the body is written.

    `just --justfile X --working-directory Y` is how this repository's own recipes are
    reached from elsewhere, and the setting resolves against `Y`, so the assignment
    has to create it there and not beside the justfile.
    """
    checkout = _justfile_copy(tmp_path / "checkout", with_setting=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    ran = subprocess.run(
        [
            "just",
            "--justfile",
            str(checkout / "justfile"),
            "--working-directory",
            str(elsewhere),
            *SHEBANG_RECIPE,
        ],
        env=_just_environment(),
        text=True,
        capture_output=True,
    )

    assert ran.returncode == BODY_RAN_STATUS, ran.stderr
    assert BODY_RAN in ran.stderr, ran.stderr
    assert (elsewhere / TEMPDIR).is_dir()
    assert not (checkout / ".logs").exists()


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def test_a_newly_cut_worktree_with_no_logs_runs_the_recipe_and_stays_clean(
    tmp_path: Path,
) -> None:
    """A worktree cut from a commit carrying no `.logs` at all runs the recipe under `noexec`.

    Built from the real justfile and the real `.gitignore`, because the ignore file is
    the other half of the answer: the justfile creates the directory in a tree that
    never carried it, and `/.logs/` is what keeps that directory, and a body in flight
    under it, out of `git status`. Nothing else creates it — no hook, no setup, no
    export — so what the worktree ends with is what the recipe run put there.
    """
    _requires_user_namespace()
    source = _justfile_copy(tmp_path / "source", with_setting=True)
    shutil.copy2(REPO_ROOT / ".gitignore", source / ".gitignore")
    _git("init", "--initial-branch", "main", cwd=source)
    _git("config", "user.name", "Journey", cwd=source)
    _git("config", "user.email", "journey@example.invalid", cwd=source)
    _git("add", "-A", cwd=source)
    assert not any(
        path.startswith(".logs/") for path in _git("ls-files", cwd=source).stdout.split()
    )
    _git("commit", "-q", "-m", "feat: name the recipe tempdir", cwd=source)

    worktree = tmp_path / "worktree"
    _git("worktree", "add", "-q", str(worktree), "-b", "cut", cwd=source)
    assert not (worktree / ".logs").exists()

    ran = _run_under_noexec_runtime_dir(worktree, tmp_path / "runtime")
    assert ran.returncode == BODY_RAN_STATUS, ran.stdout + ran.stderr
    assert BODY_RAN in ran.stderr, ran.stderr
    assert (worktree / TEMPDIR).is_dir()
    # A body in flight is written to an ignored path, so a recipe never dirties the
    # tree it runs in, and neither does the directory the justfile made for it.
    (worktree / TEMPDIR / "just-in-flight").mkdir()
    (worktree / ".logs" / "check.log").write_text("evidence\n", encoding="utf-8")
    assert _git("status", "--porcelain", cwd=worktree).stdout == ""


def test_this_checkout_tracks_nothing_under_the_directory() -> None:
    """The mechanism is the justfile, not the index: nothing under `.logs` is committed."""
    tracked = subprocess.run(
        ["git", "ls-files", "--", ".logs"], cwd=REPO_ROOT, text=True, capture_output=True
    )
    assert tracked.returncode == 0, tracked.stderr
    assert tracked.stdout == "", tracked.stdout
