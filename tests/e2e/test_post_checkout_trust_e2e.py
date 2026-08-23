"""`.githooks/post-checkout` marks the directory git just checked out as trusted.

claude-code keys `hasTrustDialogAccepted` on the exact project path, so a worktree
`onevcs` cut moments before a dispatch starts untrusted and the turn blocks on an
approval nothing can give it. The hook closes that where git already fires: `git
worktree add` runs `post-checkout` in the new worktree, and a per-run clone carries
the lender's `core.hooksPath`.

Nothing is faked. Every journey below drives a real `git worktree add` or a real `git
checkout` in a real repository with the hook activated the way `just bootstrap`
activates it, against a real alternate-Claude configuration on disk, and reads the
result back out of that file.

The other half is that the hook can never fail: git fails the command that ran a
non-zero `post-checkout`, so an absent, unreadable, or already-correct configuration
has to be a silent success or an ordinary branch switch breaks. That is driven here
too, from the states that produce it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

#: The hook under test and its two siblings. All three live in one directory because
#: `core.hooksPath` names a directory: `just bootstrap` points git at `.githooks` once
#: and every hook in it is live, which is the whole of "activated like the others".
HOOKS = REPO_ROOT / ".githooks"
POST_CHECKOUT_HOOK = HOOKS / "post-checkout"

#: A project entry that was already recorded, carrying the session state a real one
#: accumulates. The hook adds a workspace to a configuration an operator is logged
#: into, so replacing rather than extending it would discard real history.
ALREADY_REGISTERED = "/already/registered/workspace"
ALREADY_REGISTERED_ENTRY = {"hasTrustDialogAccepted": True, "history": ["a recorded session"]}

#: The shared implementation the hook marks through, and the prefix it puts on every
#: diagnostic of its own — which is its file's own name rather than a second spelling
#: of it. Git writes its own progress to the same stream, so this prefix is what
#: distinguishes "the hook said nothing" from "the command said something", and the
#: drift gate at the end of this module is what keeps it able to tell them apart.
TRUST_HELPER = REPO_ROOT / "scripts" / "claude-workspace-trust.sh"
HELPER_DIAGNOSTIC = TRUST_HELPER.stem

#: The resolver the shared helper needs in scope to name the two alternate identities'
#: config directories. It is sourced rather than read: this module never spells a config
#: directory or the variables that override them, so a rename there moves these journeys
#: with it instead of leaving them writing somewhere nothing reads.
CONFIG_DIR_RESOLVER = REPO_ROOT / "scripts" / "claude-alt-config-dir.sh"

#: How many configurations a checkout has to reach. Every harness chain names all three
#: claude-code identities — both alternate subscriptions and the primary, which is last
#: on every one of them — so a directory trusted in two of the three is a chain that
#: works until both alternates are exhausted and then blocks on the fallback.
DISPATCH_IDENTITIES = 3


def _identity_config_paths(tmp_path: Path) -> tuple[Path, ...]:
    """Ask the helper which configurations a dispatch of this host can run under.

    The same enumeration the hook marks through, so these journeys cannot drift into
    proving trust somewhere no identity reads: a helper that stopped naming one would
    fail the count here rather than quietly leave that identity untested.
    """
    named = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; source "$2"; claude_trust_config_paths journey',
            "journey",
            str(CONFIG_DIR_RESOLVER),
            str(TRUST_HELPER),
        ],
        text=True,
        capture_output=True,
        env=_environment(tmp_path),
    )
    assert named.returncode == 0, named.stderr
    configs = tuple(Path(line) for line in named.stdout.split())
    assert len(configs) == DISPATCH_IDENTITIES, named.stdout
    return configs


def _identity_configs(tmp_path: Path) -> tuple[Path, ...]:
    """The `.claude.json` of every dispatch identity, under a `HOME` nothing else reads."""
    configs = _identity_config_paths(tmp_path)
    for config in configs:
        config.parent.mkdir(parents=True, exist_ok=True)
    return configs


def _environment(tmp_path: Path) -> dict[str, str]:
    """The environment a checkout runs under: this test's own `HOME`, and nothing else.

    Built from nothing rather than from `os.environ`, because a dispatch exports the
    override that beats `HOME` — inheriting it would send these writes into the
    operator's real configuration, and naming it here to drop it would be the
    restatement the resolver exists to avoid.
    """
    return {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}


def _git(
    *args: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, env=env)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A real repository with the hooks activated exactly as `just bootstrap` does."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for command in (
        ["git", "init", "--initial-branch", "main"],
        ["git", "config", "user.name", "Journey"],
        ["git", "config", "user.email", "journey@example.invalid"],
        # The one activation step, and the same one for every hook in that directory.
        ["git", "config", "core.hooksPath", str(HOOKS)],
    ):
        subprocess.run(command, cwd=checkout, check=True, capture_output=True)
    (checkout / "source.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=checkout, check=True, capture_output=True)
    # A releasing subject, because the commit-msg hook in that same directory is live.
    subprocess.run(
        ["git", "commit", "-m", "feat: seed the journey"],
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    return checkout


def _add_worktree(
    repository: Path, tmp_path: Path, name: str, env: dict[str, str]
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    """Cut a real worktree, the way `onevcs` cuts one for a dispatch."""
    worktree = tmp_path / name
    subprocess.run(["git", "branch", name], cwd=repository, check=True, capture_output=True)
    added = _git("worktree", "add", str(worktree), name, cwd=repository, env=env)
    return worktree, added


def test_a_new_worktree_is_recorded_trusted_for_every_dispatch_identity(
    repository: Path, tmp_path: Path
) -> None:
    """The measured failure, from its own direction: a dispatch's worktree is trusted.

    Every identity, because every one of them is a candidate the chain can fall through
    to and each would meet the same untrusted directory. The primary is the one that
    matters most here and is easiest to miss: it is last on every chain, so trusting
    only the alternates holds until both of those subscriptions are spent — which is
    when the fallback exists to run.
    """
    configs = _identity_configs(tmp_path)
    for config in configs:
        config.write_text(
            json.dumps(
                {"theme": "dark", "projects": {ALREADY_REGISTERED: ALREADY_REGISTERED_ENTRY}}
            ),
            encoding="utf-8",
        )

    worktree, added = _add_worktree(repository, tmp_path, "dispatch", _environment(tmp_path))

    assert added.returncode == 0, added.stderr
    for config in configs:
        recorded = json.loads(config.read_text(encoding="utf-8"))["projects"]
        assert recorded.get(str(worktree.resolve())) == {"hasTrustDialogAccepted": True}, (
            f"{config} does not record {worktree} as trusted, so a dispatch there blocks on "
            f"an approval that cannot arrive: {recorded}"
        )
        assert recorded[ALREADY_REGISTERED] == ALREADY_REGISTERED_ENTRY, (
            "an entry that was already there was replaced rather than preserved, which "
            "discards the session state a real configuration carries"
        )


def test_an_ordinary_branch_switch_records_the_working_tree_it_updated(
    repository: Path, tmp_path: Path
) -> None:
    """The other shape git fires this hook in, and the one an operator runs by hand.

    It is here for both halves of the invariant at once: a branch switch is the ordinary
    checkout the hook must never break, and the directory it updates is one a dispatch
    can be pointed at afterwards, so it is marked like any other.
    """
    configs = _identity_configs(tmp_path)
    for config in configs:
        config.write_text(json.dumps({"projects": {}}), encoding="utf-8")

    switched = _git("checkout", "-b", "second", cwd=repository, env=_environment(tmp_path))

    assert switched.returncode == 0, switched.stderr
    for config in configs:
        recorded = json.loads(config.read_text(encoding="utf-8"))["projects"]
        assert recorded.get(str(repository.resolve())) == {"hasTrustDialogAccepted": True}, recorded


def test_a_second_checkout_of_an_already_trusted_directory_leaves_the_file_alone(
    repository: Path, tmp_path: Path
) -> None:
    """Already correct is the common case, and it has to be silent and cost nothing."""
    configs = _identity_configs(tmp_path)
    for config in configs:
        config.write_text(
            json.dumps({"projects": {str(repository.resolve()): {"hasTrustDialogAccepted": True}}}),
            encoding="utf-8",
        )
    before = {config: config.read_bytes() for config in configs}

    switched = _git("checkout", "-b", "again", cwd=repository, env=_environment(tmp_path))

    assert switched.returncode == 0, switched.stderr
    assert HELPER_DIAGNOSTIC not in switched.stderr
    for config in configs:
        assert config.read_bytes() == before[config]


def test_a_file_checkout_leaves_the_trust_configuration_alone(
    repository: Path, tmp_path: Path
) -> None:
    """The bypass, driven rather than reasoned about.

    Git fires this hook for `git checkout -- <path>` too, reporting a file checkout.
    That rewrites files inside a directory that already existed, so there is nothing new
    to trust — and this hook is on the hot path of a lock every dispatch shares, so
    taking it for a file checkout is cost with nothing bought.
    """
    configs = _identity_configs(tmp_path)
    for config in configs:
        config.write_text(json.dumps({"projects": {}}), encoding="utf-8")
    (repository / "source.txt").write_text("edited away\n", encoding="utf-8")

    restored = _git("checkout", "--", "source.txt", cwd=repository, env=_environment(tmp_path))

    assert restored.returncode == 0, restored.stderr
    assert (repository / "source.txt").read_text(encoding="utf-8") == "seed\n"
    for config in configs:
        assert json.loads(config.read_text(encoding="utf-8")) == {"projects": {}}, (
            "a file checkout took the shared trust lock and wrote, which buys nothing "
            "and is paid on every dispatch"
        )


def test_a_hooks_directory_reached_without_its_repository_still_checks_out(
    repository: Path, tmp_path: Path
) -> None:
    """`core.hooksPath` can name a directory with no `scripts/` beside it.

    The lifecycle points a per-run clone at whatever hooks directory its lender
    resolved, and a hook that assumed the shared helper was always one level up would
    fail the checkout of any repository arranged differently.
    """
    lonely = tmp_path / "lonely-hooks"
    lonely.mkdir()
    (lonely / "post-checkout").write_bytes(POST_CHECKOUT_HOOK.read_bytes())
    (lonely / "post-checkout").chmod(0o755)
    subprocess.run(
        ["git", "config", "core.hooksPath", str(lonely)],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    configs = _identity_configs(tmp_path)
    for config in configs:
        config.write_text(json.dumps({"projects": {}}), encoding="utf-8")

    worktree, added = _add_worktree(repository, tmp_path, "helperless", _environment(tmp_path))

    assert added.returncode == 0, (
        f"a hooks directory with no helper beside it failed the checkout: {added.stderr}"
    )
    assert HELPER_DIAGNOSTIC not in added.stderr, added.stderr
    assert worktree.is_dir()
    for config in configs:
        assert json.loads(config.read_text(encoding="utf-8")) == {"projects": {}}


def _unreadable_by_mode(config: Path) -> None:
    config.write_text(json.dumps({"projects": {}}), encoding="utf-8")
    config.chmod(0o000)


def _unreadable_as_json(config: Path) -> None:
    config.write_text("{broken", encoding="utf-8")


@pytest.mark.parametrize(
    "make_unreadable",
    [
        pytest.param(
            _unreadable_by_mode,
            id="mode",
            marks=pytest.mark.skipif(
                os.geteuid() == 0,
                reason="root reads a mode-000 file, so this state cannot be produced as root",
            ),
        ),
        pytest.param(_unreadable_as_json, id="not-json"),
    ],
)
def test_a_checkout_whose_trust_configuration_is_unreadable_still_succeeds_in_silence(
    repository: Path, tmp_path: Path, make_unreadable: Callable[[Path], None]
) -> None:
    """The state that would otherwise break every checkout in the repository.

    A configuration jq cannot read is the one state the shared helper reports on, and
    a report is a non-zero exit git turns into a failed checkout. Both shapes of
    unreadable are driven because they fail in different places: one before jq is
    handed the file, one inside it.
    """
    configs = _identity_configs(tmp_path)
    for config in configs:
        make_unreadable(config)
    # A mode-000 configuration cannot be read back, so what is compared is what can
    # be: its size and permissions, which any write through the helper's replace path
    # would move.
    before = {config: (config.stat().st_size, config.stat().st_mode) for config in configs}

    worktree, added = _add_worktree(repository, tmp_path, "unreadable", _environment(tmp_path))

    assert added.returncode == 0, (
        f"an unreadable trust configuration failed `git worktree add`, so no dispatch "
        f"could cut a worktree in this repository: {added.stderr}"
    )
    assert HELPER_DIAGNOSTIC not in added.stderr, (
        f"the hook spoke during an ordinary checkout: {added.stderr}"
    )
    assert worktree.is_dir()
    for config, recorded in before.items():
        assert (config.stat().st_size, config.stat().st_mode) == recorded


def test_a_checkout_with_no_trust_configuration_succeeds_and_writes_nothing(
    repository: Path, tmp_path: Path
) -> None:
    """The state of a host nobody has logged these identities into yet."""
    configs = _identity_configs(tmp_path)

    worktree, added = _add_worktree(repository, tmp_path, "unconfigured", _environment(tmp_path))

    assert added.returncode == 0, added.stderr
    assert HELPER_DIAGNOSTIC not in added.stderr, added.stderr
    assert worktree.is_dir()
    for config in configs:
        assert not config.exists(), (
            f"{config} was created for a configuration nobody has logged into yet"
        )
        assert sorted(path.name for path in config.parent.glob(f"{config.name}*")) == [], (
            f"{config.parent} gained a lock or a temporary for a configuration that is not there"
        )


# llmlint: ignore-block[tests_mirror_real_usage] `just bootstrap` cannot be the
# interface here; see this helper's docstring.
def _bootstrap_hooks_path() -> str:
    """The value `just bootstrap` activates this repository's hooks with, from the recipe.

    Read rather than restated: `core.hooksPath` naming a directory is the whole of how
    a third hook becomes live without a fourth setup step, and a recipe that started
    naming a file instead would leave this hook installed and never run.

    Running `just bootstrap` is what a user does and is not what a test can do: it
    installs this host's toolchain, runs session setup, and drives Nx over the whole
    workspace, so invoking it from its own pytest process mutates the environment that
    process is running in and does not end — the reason `llmlint.yml` already excludes
    it from the e2e demand. What is recoverable without running it is the one step that
    decides whether a hook is live, taken from the recipe rather than restated, so a
    recipe that stopped activating this directory fails here.
    """
    shown = subprocess.run(
        ["just", "--justfile", str(REPO_ROOT / "justfile"), "--show", "bootstrap"],
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )
    assert shown.returncode == 0, shown.stderr
    configured: list[str] = re.findall(
        r"^\s*git config core\.hooksPath (\S+)\s*$", shown.stdout, re.M
    )
    assert configured == [str(HOOKS.relative_to(REPO_ROOT))], (
        f"the bootstrap recipe no longer activates {HOOKS.name}: {shown.stdout}"
    )
    return configured[0]


# llmlint: ignore-end[tests_mirror_real_usage]


def test_the_hooks_path_bootstrap_configures_makes_this_hook_live(tmp_path: Path) -> None:
    """The activation claim, driven rather than asserted about file layout.

    A checkout laid out like this one, activated with the recipe's own `git config`
    step verbatim, then cut a real worktree. That covers what a new hook can get wrong
    on its own and what no static check sees: git skips a hook it cannot execute, and
    skips it silently, so a trust entry appearing at the end is the activation.
    """
    checkout = tmp_path / "bootstrapped"
    (checkout / HOOKS.name).mkdir(parents=True)
    shutil.copy2(POST_CHECKOUT_HOOK, checkout / HOOKS.name / POST_CHECKOUT_HOOK.name)
    (checkout / "scripts").mkdir()
    for helper in (TRUST_HELPER, CONFIG_DIR_RESOLVER):
        shutil.copy2(helper, checkout / "scripts" / helper.name)
    for command in (
        ["git", "init", "--initial-branch", "main"],
        ["git", "config", "user.name", "Journey"],
        ["git", "config", "user.email", "journey@example.invalid"],
        ["git", "add", "-A"],
        ["git", "commit", "-m", "feat: seed the bootstrapped checkout"],
    ):
        subprocess.run(command, cwd=checkout, check=True, capture_output=True)
    # The recipe's own activation step, verbatim, in a repository it could have run in.
    # llmlint: ignore[tests_mirror_real_usage] The recipe cannot be run here; see above.
    subprocess.run(
        ["git", "config", "core.hooksPath", _bootstrap_hooks_path()],
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    configs = _identity_configs(tmp_path)
    for config in configs:
        config.write_text(json.dumps({"projects": {}}), encoding="utf-8")

    worktree, added = _add_worktree(checkout, tmp_path, "activated", _environment(tmp_path))

    assert added.returncode == 0, added.stderr
    for config in configs:
        recorded = json.loads(config.read_text(encoding="utf-8"))["projects"]
        assert recorded.get(str(worktree.resolve())) == {"hasTrustDialogAccepted": True}, (
            "the step bootstrap runs did not make this hook live, so it is installed "
            f"and never runs: {recorded}"
        )


def test_the_diagnostic_the_silence_assertions_watch_for_is_the_one_the_helper_emits(
    tmp_path: Path,
) -> None:
    """Ground the prefix every silence assertion above is written against.

    Those assertions are only worth anything while the string they look for is the one
    the helper would actually print: a helper that renamed its prefix would go on
    reporting into a checkout and every one of them would pass without noticing. So the
    prefix is read off the helper's own filename and then measured against the helper,
    on the state that makes it speak.
    """
    directory = tmp_path / "claude-alt"
    directory.mkdir()
    (directory / ".claude.json").write_text("{broken", encoding="utf-8")
    environment = dict(os.environ)
    environment["HOME"] = str(tmp_path)
    environment["ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR"] = str(directory)
    environment["ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR"] = str(directory)

    reported = subprocess.run(
        [str(TRUST_HELPER), str(tmp_path)],
        text=True,
        capture_output=True,
        env=environment,
    )

    assert HELPER_DIAGNOSTIC in reported.stderr, (
        f"the helper reports under some other prefix now, so every assertion in this "
        f"module that the hook stayed silent proves nothing: {reported.stderr!r}"
    )
