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
import os
import re
import subprocess
from collections.abc import Mapping
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


def listed_checkout_paths(manifest: Path = TRACKED_CHECKOUTS) -> tuple[Path, ...]:
    """Every directory ``manifest`` says a checkout of some repository lives in.

    In the order listed and without duplicates, because the first listed checkout of
    an identity is the one a reconciliation searches. `~` is expanded the way the
    recipe expands it; nothing else about a path is interpreted, since registration
    resolves a checkout's identity from its own `origin` rather than from its path.
    """
    found: list[Path] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
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


def held_checkouts(manifest: Path = TRACKED_CHECKOUTS) -> dict[RepoIdentity, list[Path]]:
    """Every checkout ``manifest`` lists that this host holds, grouped by identity, in order.

    Git is asked which identity a checkout really is rather than the path being
    trusted to say, so a directory named after one repository but cloned from another
    resolves to what it is. A path this host does not have is left out, for the reason
    the recipe skips it: the list names both layouts this repository dispatches from.
    """
    found: dict[RepoIdentity, list[Path]] = {}
    for path in listed_checkout_paths(manifest):
        if not (path / ".git").exists():
            continue
        origin = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
        )
        if origin.returncode != 0:
            continue
        found.setdefault(normalized_identity(origin.stdout), []).append(path)
    return found


def registered_checkouts() -> dict[RepoIdentity, Path]:
    """Each identity this host actually holds a checkout of, and where.

    The first listed checkout of each, which is the one a reconciliation searches. A
    host holding none of them resolves nothing, which is what each caller reports rather
    than asserts on.
    """
    return {identity: paths[0] for identity, paths in held_checkouts().items()}


class Disagreement(NamedTuple):
    """One identity whose origin form and first listed alias resolve to different checkouts."""

    identity: RepoIdentity
    #: The checkout `onevcs resolve <identity>` selects as the publication checkout.
    by_origin: Path
    #: The alias the manifest lists first for the identity, and the checkout it resolves to.
    first_alias: str
    by_first_alias: Path


class Resolutions(NamedTuple):
    """What comparing the two spellings of every multi-checkout identity found."""

    #: The identities with more than one held checkout whose two spellings agree.
    agreeing: tuple[RepoIdentity, ...]
    disagreeing: tuple[Disagreement, ...]
    #: Identities with more than one checkout that one spelling or the other could not
    #: be resolved for, each with why. Reported rather than counted either way: an
    #: identity this registry does not hold is not evidence about how it selects.
    unresolved: dict[RepoIdentity, str]


def _resolved_publication_checkout(repo: str, home: Path | None) -> Path | None:
    """The publication checkout `onevcs resolve` answers for ``repo``, or ``None``."""
    environment: Mapping[str, str] = (
        os.environ if home is None else {**os.environ, "ONEVCS_HOME": str(home)}
    )
    asked = subprocess.run(
        ["onevcs", "resolve", repo],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if asked.returncode != 0:
        return None
    try:
        answer = json.loads(asked.stdout)
    except ValueError:
        return None
    match answer:
        case {"publication_checkout": str(checkout)} if checkout:
            return Path(checkout).resolve()
        case _:
            return None


def publication_resolutions(
    manifest: Path = TRACKED_CHECKOUTS, home: Path | None = None
) -> Resolutions:
    """Compare, per identity with several held checkouts, its origin form with its first alias.

    Among several checkouts of one identity, `onevcs resolve <origin>` selects the one
    whose alias sorts first: `store::resolve` at `onevcs` v0.19.3 answers the identity-key
    branch with ``registry.checkouts.iter().find(|(_, c)| c.identity == key)`` over a
    ``BTreeMap`` keyed by alias, so which checkout a plan naming its repository by origin
    publishes from is decided by alias order. This host's publication checkouts sort
    first by the accident of their names — `ai-orchestrator` before
    `ai-orchestrator-isolated` — and this is what notices a checkout registered later
    under a name that sorts earlier, before a publication is redirected into it. The
    live answer is read rather than the rule restated: both spellings are asked of the
    installed `onevcs`, under ``home`` when one is named and the ambient registry
    otherwise. The alias is the checkout's directory name, which is what `onevcs
    register` derives it from.
    """
    agreeing: list[RepoIdentity] = []
    disagreeing: list[Disagreement] = []
    unresolved: dict[RepoIdentity, str] = {}
    for identity, paths in held_checkouts(manifest).items():
        if len(paths) < 2:
            continue
        first_alias = paths[0].name
        by_origin = _resolved_publication_checkout(identity, home)
        by_first_alias = _resolved_publication_checkout(first_alias, home)
        if by_origin is None:
            unresolved[identity] = f"`onevcs resolve {identity}` answered no publication checkout"
        elif by_first_alias is None:
            unresolved[identity] = (
                f"`onevcs resolve {first_alias}` answered no publication checkout"
            )
        elif by_origin == by_first_alias:
            agreeing.append(identity)
        else:
            disagreeing.append(Disagreement(identity, by_origin, first_alias, by_first_alias))
    return Resolutions(tuple(agreeing), tuple(disagreeing), unresolved)


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
