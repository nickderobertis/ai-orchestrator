"""Where the repositories this host routes are checked out, and what each one defines.

Several guards here reconcile this repository's own configuration against another
repository — a repo-specific persona's review bar against the recipes that
repository has. All of them need the same two facts first:
which identity each listed checkout really is, and what its justfile defines. Stating
that twice would let two guards disagree about which checkout they searched, so both
resolve it from here.

`config/onevcs.checkouts` is the tracked list `just repos-apply` registers, and it is
read rather than restated: a checkout added there is one these guards then cover. A
path this host does not have is skipped for the reason the recipe skips it — the list
names both of the layouts this repository dispatches from, and each host holds one.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from orchestrator.root import REPO_ROOT

#: `host/owner/name`, the identity `onevcs` files a repository under. It is the
#: vocabulary every set and helper keyed by a repository uses, rather than bare text:
#: the rules file matches on its three parts, the registry files identities under it,
#: and `onevcs rules check` takes it as its argument.
RepoIdentity = str

#: The tracked checkout list `just repos-apply` registers by default.
TRACKED_CHECKOUTS = REPO_ROOT / "config" / "onevcs.checkouts"


def listed_checkout_paths() -> tuple[Path, ...]:
    """Every directory the tracked list says a checkout of some repository lives in.

    In the order listed and without duplicates, because the first listed checkout of
    an identity is the one a reconciliation searches. `~` is expanded the way the
    recipe expands it; nothing else about a path is interpreted, since registration
    resolves a checkout's identity from its own `origin` rather than from its path.
    """
    found: list[Path] = []
    for line in TRACKED_CHECKOUTS.read_text(encoding="utf-8").splitlines():
        entry = line.partition("#")[0].strip()
        if not entry:
            continue
        path = Path(entry).expanduser()
        if path not in found:
            found.append(path)
    return tuple(found)


def normalized_identity(origin: str) -> RepoIdentity:
    """The identity `onevcs` files an origin URL under: `host/owner/name`.

    Both spellings this host's checkouts carry are handled — `https://host/owner/name`
    with or without `.git`, and git's scp-like `user@host:owner/name` — because which
    one a clone has is an accident of how it was made.
    """
    without_scheme = re.sub(r"^[a-z][a-z0-9+.-]*://", "", origin.strip())
    user, _, remainder = without_scheme.rpartition("@")
    if user:
        # scp-like: the host is separated from the path by a colon, not a slash.
        remainder = remainder.replace(":", "/", 1)
    return remainder.removesuffix(".git").removesuffix("/")


def registered_checkouts() -> dict[RepoIdentity, Path]:
    """Each identity this host actually holds a checkout of, and where.

    Git is asked which identity a checkout really is rather than the path being
    trusted to say, so a directory named after one repository but cloned from another
    resolves to what it is. A host holding none of them resolves nothing, which is
    what each caller reports rather than asserts on.
    """
    found: dict[RepoIdentity, Path] = {}
    for path in listed_checkout_paths():
        if not (path / ".git").exists():
            continue
        origin = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
        )
        if origin.returncode != 0:
            continue
        found.setdefault(normalized_identity(origin.stdout), path)
    return found


class Recipes(NamedTuple):
    """The recipes a repository defines, and whether they could be read at all."""

    #: False when `just` could not parse the repository's recipes, so `names` is not
    #: evidence of anything and a reconciliation must report rather than assert.
    readable: bool
    names: frozenset[str]


def defined_recipes(root: Path) -> Recipes:
    """Every recipe `root`'s own justfile defines.

    Read through the tool that owns the definitions rather than by scanning text:
    `just --dump` is the recipe runner's own parse of its justfile, so what comes back
    is the set a `just <recipe>` run there would really resolve against — aliases and
    imports included, comments and documentation already gone.
    """
    dumped = subprocess.run(
        ["just", "--dump", "--dump-format", "json"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if dumped.returncode != 0:
        return Recipes(readable=False, names=frozenset())
    document = json.loads(dumped.stdout)
    return Recipes(
        readable=True,
        names=frozenset(document["recipes"]) | frozenset(document.get("aliases") or {}),
    )
