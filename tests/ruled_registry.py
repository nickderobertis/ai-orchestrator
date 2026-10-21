"""A scratch `onevcs` registry built by the real `just repos-apply` from the tracked rules.

Every journey that reads what the installed `onevcs` resolves for an identity — its
publication, its approvals, how long it watches checks — needs the same thing first: a
registry holding a checkout of that identity, installed by the recipe from the tracked
`config/onevcs.rules.yml`. A repository resolves only through a registered checkout, so
:func:`ruled_registry` registers a real git checkout, carrying the identity's real
origin, for every rule the file names, under a scratch registry root and a scratch XDG
configuration home holding no host overlay.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from registered_checkouts import RepoIdentity

from orchestrator.root import REPO_ROOT

#: The tracked base `just repos-apply` installs: the policy each identity publishes under.
TRACKED_RULES = REPO_ROOT / "config" / "onevcs.rules.yml"

#: How every rule in the tracked file names the repository it matches. Reading them
#: back out is what makes a rule added later fail a journey here, rather than at that
#: repository's first publication.
RULE_MATCH = re.compile(
    r"- match: \{host: (?P<host>[^,]+), owner: (?P<owner>[^,]+), name: (?P<name>[^}]+)\}"
)


def checkout(directory: Path, origin: str) -> Path:
    """A real git checkout of `origin`, which is all registration reads of one."""
    directory.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=directory, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=directory, check=True)
    return directory


def apply_registry(
    manifest: Path,
    home: Path,
    *arguments: str,
    config_home: Path | None = None,
    temporary: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real recipe against a scratch registry root.

    ``config_home`` is the XDG configuration home the recipe resolves the host's
    workspaces and rules overlays under; a scratch sibling of ``home`` holding nothing
    when a journey names none, so the tracked files are installed alone whatever this
    host has. ``temporary``, when named, is the recipe's ``TMPDIR``, for a journey that
    reads what a run left there.
    """
    if config_home is None:
        config_home = home.parent / f"{home.name}-config"
    config_home.mkdir(parents=True, exist_ok=True)
    scratch = {"TMPDIR": str(temporary)} if temporary is not None else {}
    return subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), *arguments],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "ONEVCS_HOME": str(home),
            "XDG_CONFIG_HOME": str(config_home),
            **scratch,
        },
        text=True,
        capture_output=True,
    )


def onevcs(
    home: Path, *arguments: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """The installed `onevcs`, reading the registry under ``home``."""
    return subprocess.run(
        ["uv", "run", "--project", str(REPO_ROOT), "onevcs", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        input=stdin,
        capture_output=True,
    )


def ruled_identities() -> tuple[RepoIdentity, ...]:
    """Every identity `config/onevcs.rules.yml` names, in the order it names them."""
    text = TRACKED_RULES.read_text(encoding="utf-8")
    named = tuple(
        f"{rule['host']}/{rule['owner']}/{rule['name']}" for rule in RULE_MATCH.finditer(text)
    )
    assert len(named) == text.count("- match:"), (
        "config/onevcs.rules.yml no longer writes every rule's match as a one-line flow "
        f"mapping, so only {len(named)} of {text.count('- match:')} rules were read back"
    )
    return named


def reported(output: str, key: str) -> str:
    """One field of `onevcs rules check`, without its `(from rule 1)` annotation."""
    for line in output.splitlines():
        name, separator, value = line.partition(": ")
        if separator and name.strip() == key:
            return value.split(" (from ")[0].strip()
    raise AssertionError(f"no {key!r} in:\n{output}")


def ruled_registry(root: Path) -> Path:
    """The registry root under ``root`` holding a checkout of every identity ruled.

    Installed by the real recipe from the tracked rules alone, so `onevcs rules check`
    against the returned root answers what this host's file resolves for each identity.
    """
    paths = [
        checkout(root / "checkouts" / key.rpartition("/")[2], f"https://{key}")
        for key in ruled_identities()
    ]
    manifest = root / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = root / "onevcs"
    result = apply_registry(manifest, home)
    assert result.returncode == 0, result.stdout + result.stderr
    return home
