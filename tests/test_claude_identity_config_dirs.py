"""`scripts/claude-alt-config-dir.sh` resolves every Claude identity's config directory.

Four identities read their `CLAUDE_CONFIG_DIR` through an indirection this helper is the
one source of, and per indirection a non-empty value in the environment wins, then the
host's identities file, then a `$HOME`-relative default. That file lives outside every
checkout so a host whose `~/.claude` is some other account can say so without editing a
config every other host shares.

Everything here drives the real helper under a real bash, sourced and called the way
every wrapper does, in an environment built from nothing: `HOME` is each test's own and
`XDG_CONFIG_HOME` is either set to a path the test chose or absent, so an identities file
the host keeps under its real `~/.config` never reaches a verdict.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

HELPER = REPO_ROOT / "scripts" / "claude-alt-config-dir.sh"
PARSER = REPO_ROOT / "scripts" / "credentials-env.sh"

#: Each indirection and its `$HOME`-relative default, as the contract states them.
DEFAULTS = {
    "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": ".claude-alt",
    "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": ".claude-alt2",
    "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR": ".claude-primary-backup",
    "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR": ".claude",
}

#: The two indirections the primary-backup identity and the env-driven primary added.
NEW_INDIRECTIONS = (
    "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR",
    "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR",
)

#: Where the identities file sits beneath whichever config home applies.
IDENTITIES_FILE = Path("ai-orchestrator") / "claude-identities.env"


class Resolved(NamedTuple):
    """What one call of `resolve_claude_alt_config_dir` left behind."""

    returncode: int
    #: Each indirection as the calling shell sees it afterwards; empty when unset.
    exported: dict[str, str]
    stderr: str


def _resolve(environment: dict[str, str], *, helper: Path = HELPER) -> Resolved:
    """Source the helper, resolve, and print each indirection — or exit with its refusal."""
    script = (
        'source "$1"\n'
        "resolve_claude_alt_config_dir claude-identity-test || exit $?\n"
        f"for name in {' '.join(DEFAULTS)}; do\n"
        '    printf "%s=%s\\n" "$name" "${!name-}"\n'
        "done\n"
    )
    completed = subprocess.run(
        ["bash", "-c", script, "claude-identity-test", str(helper)],
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", **environment},
        check=False,
    )
    exported = dict(line.split("=", 1) for line in completed.stdout.splitlines())
    return Resolved(completed.returncode, exported, completed.stderr)


def _write_identities(config_home: Path, contents: str) -> Path:
    """Write the identities file beneath `config_home`, returning its path."""
    identities = config_home / IDENTITIES_FILE
    identities.parent.mkdir(parents=True, exist_ok=True)
    identities.write_text(contents, encoding="utf-8")
    return identities


def test_with_nothing_set_every_identity_resolves_to_its_default(tmp_path: Path) -> None:
    """A host exporting nothing and keeping no identities file gets the four defaults."""
    resolved = _resolve({"HOME": str(tmp_path)})

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.exported == {name: str(tmp_path / leaf) for name, leaf in DEFAULTS.items()}
    assert resolved.stderr == ""
    # Nothing is created: an absent directory is `auth` to claude-code, which falls through.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("indirection", NEW_INDIRECTIONS)
def test_a_non_empty_override_in_the_environment_is_honoured(
    tmp_path: Path, indirection: str
) -> None:
    override = tmp_path / "chosen-elsewhere"

    resolved = _resolve({"HOME": str(tmp_path), indirection: str(override)})

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.exported[indirection] == str(override)


@pytest.mark.parametrize("indirection", NEW_INDIRECTIONS)
def test_a_relative_override_is_refused_naming_the_variable_and_not_its_value(
    tmp_path: Path, indirection: str
) -> None:
    override = "relative/override-that-must-not-appear"

    resolved = _resolve({"HOME": str(tmp_path), indirection: override})

    assert resolved.returncode != 0
    assert indirection in resolved.stderr
    assert "must be absolute" in resolved.stderr
    assert override not in resolved.stderr
    assert resolved.exported == {}, "a refused resolution still reached the caller's next line"


@pytest.mark.parametrize("unusable", ["regular-file", "unsearchable-directory"])
@pytest.mark.parametrize("indirection", list(DEFAULTS))
def test_an_existing_path_that_is_not_an_accessible_directory_is_refused(
    tmp_path: Path, indirection: str, unusable: str
) -> None:
    """An override naming something claude-code could not use as its config directory.

    The refusal names the variable that decides it and never the path the operator wrote.
    """
    override = tmp_path / "path-that-must-not-appear"
    if unusable == "regular-file":
        override.write_text("", encoding="utf-8")
    else:
        override.mkdir()
        override.chmod(0o000)
    try:
        resolved = _resolve({"HOME": str(tmp_path), indirection: str(override)})
    finally:
        override.chmod(0o755)

    assert resolved.returncode == 2
    assert f"named by {indirection} is not an accessible directory" in resolved.stderr
    assert override.name not in resolved.stderr
    assert resolved.exported == {}, "a refused resolution still reached the caller's next line"


@pytest.mark.parametrize(
    "config_home",
    ["absolute", "unset", "empty", "relative"],
)
def test_the_identities_file_is_read_from_the_config_home_that_applies(
    tmp_path: Path, config_home: str
) -> None:
    """An absolute `XDG_CONFIG_HOME` places the file; anything else falls back to `HOME`.

    The absolute case also plants a file under `$HOME/.config` naming another directory,
    so it proves the XDG file is the one read rather than merely one of two agreeing.
    """
    home = tmp_path / "home"
    chosen = tmp_path / "from-the-file"
    environment = {"HOME": str(home)}
    under_home = f"ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR={chosen}\n"
    match config_home:
        case "absolute":
            xdg = tmp_path / "xdg"
            environment["XDG_CONFIG_HOME"] = str(xdg)
            _write_identities(xdg, under_home)
            under_home = f"ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR={tmp_path / 'decoy'}\n"
        case "empty":
            environment["XDG_CONFIG_HOME"] = ""
        case "relative":
            environment["XDG_CONFIG_HOME"] = "relative/config"
        case "unset":
            pass
        case unknown:
            raise AssertionError(f"{unknown} is not a config home this journey knows how to set")
    _write_identities(home / ".config", under_home)

    resolved = _resolve(environment)

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.exported["ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR"] == str(chosen)
    assert resolved.exported["ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR"] == str(
        home / ".claude-primary-backup"
    )


def test_the_environment_wins_over_the_identities_file_name_by_name(tmp_path: Path) -> None:
    """The WSL box's two lines, with one of them overridden for a single command."""
    home = tmp_path / "home"
    _write_identities(
        home / ".config",
        "# this host's ~/.claude is the primary-backup account\n"
        f'ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR="{home / ".claude"}"\n'
        f"export ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR={home / '.claude-primary'}\n",
    )
    override = tmp_path / "for-this-command"

    resolved = _resolve(
        {"HOME": str(home), "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR": str(override)}
    )

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.exported["ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR"] == str(override)
    # Read in the credentials dialect: quotes and an `export` prefix are the file's own.
    assert resolved.exported["ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR"] == str(
        home / ".claude"
    )


def test_an_empty_environment_value_does_not_shadow_the_identities_file(tmp_path: Path) -> None:
    """Only a non-empty value in the environment wins, so an empty export is no override."""
    home = tmp_path / "home"
    chosen = tmp_path / "from-the-file"
    _write_identities(home / ".config", f"ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR={chosen}\n")

    resolved = _resolve({"HOME": str(home), "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR": ""})

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.exported["ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR"] == str(chosen)


@pytest.mark.parametrize(
    "shape",
    [
        "directory",
        pytest.param(
            "unreadable",
            marks=pytest.mark.skipif(
                os.geteuid() == 0,
                reason="root reads a mode-000 file, so this state cannot be produced as root",
            ),
        ),
    ],
)
def test_an_identities_file_that_exists_but_cannot_be_read_is_refused(
    tmp_path: Path, shape: str
) -> None:
    home = tmp_path / "home"
    identities = home / ".config" / IDENTITIES_FILE
    identities.parent.mkdir(parents=True)
    if shape == "directory":
        identities.mkdir()
    else:
        identities.write_text("ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=/x\n", encoding="utf-8")
        identities.chmod(0o000)

    try:
        resolved = _resolve({"HOME": str(home)})
    finally:
        if shape == "unreadable":
            identities.chmod(0o644)

    assert resolved.returncode == 2
    assert f"the Claude identities file at {identities} is not a readable regular file" in (
        resolved.stderr
    )
    assert resolved.exported == {}


def test_a_name_the_identities_file_does_not_admit_is_refused_with_its_line(
    tmp_path: Path,
) -> None:
    """A credential written into the wrong file is refused where it is written, unechoed."""
    home = tmp_path / "home"
    identities = _write_identities(
        home / ".config",
        "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=/a-value-that-must-not-appear\n"
        "GH_PROJECTS_TOKEN=ghp_a_token_that_must_not_appear\n",
    )

    resolved = _resolve({"HOME": str(home)})

    assert resolved.returncode == 2
    assert f"line 2 in {identities} names GH_PROJECTS_TOKEN" in resolved.stderr
    assert "it admits only" in resolved.stderr
    assert "must-not-appear" not in resolved.stderr
    assert resolved.exported == {}


def test_a_malformed_identities_line_is_refused_with_its_line(tmp_path: Path) -> None:
    home = tmp_path / "home"
    identities = _write_identities(
        home / ".config",
        "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=/a-value-that-must-not-appear\n"
        "a line with no assignment\n",
    )

    resolved = _resolve({"HOME": str(home)})

    assert resolved.returncode == 2
    assert f"malformed Claude identities line 2 in {identities}" in resolved.stderr
    assert "KEY=VALUE" in resolved.stderr
    assert "must-not-appear" not in resolved.stderr
    assert resolved.exported == {}


def test_an_empty_identities_value_is_refused_rather_than_read_as_the_default(
    tmp_path: Path,
) -> None:
    """`NAME=` in the file is a value, and an empty one is not an absolute directory.

    The primary's directory is planted, so a regression to the default would resolve cleanly.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    identities = _write_identities(home / ".config", "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=\n")

    resolved = _resolve({"HOME": str(home)})

    assert resolved.returncode == 2, resolved.stderr
    assert "primary Claude config path must be absolute" in resolved.stderr
    assert f"write ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR in {identities}" in resolved.stderr
    assert resolved.exported == {}


def test_an_identities_value_is_taken_literally_and_so_fails_the_absolute_path_rule(
    tmp_path: Path,
) -> None:
    """The file expands nothing: `$HOME/x` is those characters, which is not an absolute path."""
    home = tmp_path / "home"
    identities = _write_identities(
        home / ".config", "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=$HOME/elsewhere\n"
    )

    resolved = _resolve({"HOME": str(home)})

    assert resolved.returncode == 2
    assert "primary Claude config path must be absolute" in resolved.stderr
    assert f"write ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR in {identities}" in resolved.stderr
    assert "$HOME/elsewhere" not in resolved.stderr
    assert str(home / "elsewhere") not in resolved.stderr
    assert resolved.exported == {}


def test_the_identities_file_is_parsed_by_the_credentials_parser_and_nothing_else(
    tmp_path: Path,
) -> None:
    """The helper beside no parser still serves a host with no file, and refuses one with.

    That is the observable half of "one parser": with `scripts/credentials-env.sh` gone,
    a host whose file is absent resolves exactly as before, and a host whose file exists
    is refused by name rather than read some second way.
    """
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    lonely = scripts / HELPER.name
    shutil.copy2(HELPER, lonely)
    home = tmp_path / "home"

    without_file = _resolve({"HOME": str(home)}, helper=lonely)
    _write_identities(home / ".config", "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=/x\n")
    with_file = _resolve({"HOME": str(home)}, helper=lonely)
    shutil.copy2(PARSER, scripts / PARSER.name)
    with_parser = _resolve({"HOME": str(home)}, helper=lonely)

    assert without_file.returncode == 0, without_file.stderr
    assert with_file.returncode == 2
    assert f"needs the parser at {scripts / PARSER.name}" in with_file.stderr
    assert with_parser.returncode == 0, with_parser.stderr
    assert with_parser.exported["ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR"] == "/x"


@pytest.mark.parametrize("with_parser", [True, False], ids=["parser-beside", "no-parser"])
@pytest.mark.parametrize("hidden", [False, True], ids=["nothing-there", "beneath-unsearchable"])
def test_an_absent_identities_file_resolves_the_defaults_silently(
    tmp_path: Path, with_parser: bool, hidden: bool
) -> None:
    """A file this process sees nothing at is absent, whether or not the parser is there.

    Beneath a config directory this process may not search, a file somebody wrote is as
    invisible as none at all: both resolve the four defaults with nothing said, and so
    does a checkout whose helper has no parser beside it to ask.
    """
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / HELPER.name
    shutil.copy2(HELPER, helper)
    if with_parser:
        shutil.copy2(PARSER, scripts / PARSER.name)
    home = tmp_path / "home"
    config_home = home / ".config"
    locked = config_home / IDENTITIES_FILE.parent
    locked.mkdir(parents=True)
    if hidden:
        _write_identities(config_home, "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=/must-not-appear\n")
        locked.chmod(0o000)
    try:
        resolved = _resolve({"HOME": str(home)}, helper=helper)
    finally:
        locked.chmod(0o755)

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stderr == ""
    assert resolved.exported == {name: str(home / leaf) for name, leaf in DEFAULTS.items()}


def test_a_parser_that_is_readable_but_will_not_load_is_refused_by_name(tmp_path: Path) -> None:
    """A parser that fails as it is sourced stops the helper before the file is read at all."""
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / HELPER.name
    shutil.copy2(HELPER, helper)
    broken = scripts / PARSER.name
    broken.write_text("return 3\n", encoding="utf-8")
    home = tmp_path / "home"
    _write_identities(
        home / ".config", "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=/a-value-that-must-not-appear\n"
    )

    resolved = _resolve({"HOME": str(home)}, helper=helper)

    assert resolved.returncode == 2
    assert f"the parser at {broken} is readable but could not be loaded" in resolved.stderr
    assert "a-value-that-must-not-appear" not in resolved.stderr
    assert resolved.exported == {}


def test_resolving_a_name_that_is_not_an_identity_indirection_is_refused(tmp_path: Path) -> None:
    """The per-identity resolver the trust marker calls routes the four indirections only."""
    typo = "ORCHESTRATOR_CLAUDE_PRIMARY_BAKCUP_CONFIG_DIR"
    completed = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"\n'
            "status=0\n"
            'resolve_claude_identity_config_dir claude-identity-test "$2" || status=$?\n'
            'printf "%s" "${!2-}"\n'
            'exit "$status"\n',
            "claude-identity-test",
            str(HELPER),
            typo,
        ],
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        check=False,
    )

    assert completed.returncode == 2
    assert f"{typo} is not a Claude identity's config indirection" in completed.stderr
    assert all(name in completed.stderr for name in DEFAULTS), completed.stderr
    assert completed.stdout == "", "a refused name was still exported to the caller"
