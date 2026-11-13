"""Where the repositories this host registers are checked out, and what each one defines.

Several guards here reconcile this repository's own configuration against another
repository — a repo-specific persona's review bar against the recipes that
repository has. All of them need the same two facts first:
which checkouts this host holds of each identity, and what its justfile defines. Stating
that twice would let two guards disagree about which checkout they searched, so both
resolve it from here.

Which checkouts exist is this host's onevcs registry's to say, read through the installed
`onevcs repos` — the listing `just repos` prints — and never from a tracked list: which
repositories a host works on is the host's own registration, so registering one changes
nothing in this repository. A sibling this host has not registered, or registers at a path
it does not have, is skipped, and each caller decides what an absent sibling means.

The host's registry is read from the suite's copy of it,
`onevcs_state_snapshot.host_registry_environment()`, because the root the suite exports
registers no identity: `tests/onevcs_state_snapshot.py` says why.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import NamedTuple

import onevcs_state_snapshot

#: `host/owner/name`, the identity `onevcs` files a repository under. It is the
#: vocabulary every set and helper keyed by a repository uses, rather than bare text:
#: the rules file matches on its three parts, the registry files identities under it,
#: and `onevcs rules check` takes it as its argument.
RepoIdentity = str

#: What `onevcs repos` prints for a registry holding no checkout at all.
NOTHING_REGISTERED = "no repositories registered"


class UnreadableSibling(ValueError):
    """A registered sibling's live state could not be read at all.

    Its message is the finding — which checkout, what was asked of it, and why it could
    not answer — for the caller to settle as drift: what a sibling's checkout holds is
    that repository's own to move, so failing to read it is no defect of this one.
    """


def sibling_git(checkout: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    """``git *arguments`` run inside ``checkout``; :class:`UnreadableSibling` where it cannot run.

    Run in the checkout rather than pointed at it with `-C`, so a checkout this process
    cannot enter is an OS error naming it, rather than an exit status indistinguishable
    from git answering that a ref or a file is absent. Bytes, because decoding what a
    sibling holds is each reader's own question.
    """
    try:
        return subprocess.run(
            ["git", *arguments], cwd=checkout, capture_output=True, timeout=120, check=False
        )
    except OSError as error:
        raise UnreadableSibling(
            f"{checkout} cannot be read: `git {arguments[0]}` could not run there ({error})"
        ) from error


class Discovery(NamedTuple):
    """The registered checkouts this host holds, and a finding for each it could not read."""

    #: Each identity's held checkouts, in the order the registry lists them.
    held: dict[RepoIdentity, list[Path]]
    #: Why the registry, or a checkout it names, could not be read — for the caller to
    #: settle as drift rather than drop.
    unreadable: tuple[str, ...]


def _environment(home: Path | None) -> Mapping[str, str]:
    """The environment one registry read runs in: ``home``'s, or the suite's host copy."""
    if home is None:
        return onevcs_state_snapshot.host_registry_environment()
    return {**os.environ, onevcs_state_snapshot.ONEVCS_HOME: str(home)}


def parse_listing(listing: str) -> dict[RepoIdentity, list[Path]]:
    """Each identity `onevcs repos` lists, with its checkout paths in the order listed.

    An identity line is `identity<TAB>gate` at the margin, and each checkout under it an
    indented `alias<TAB>path`. Raises :class:`UnreadableSibling` naming the first line of
    any other shape, since a listing this reader does not know says nothing about which
    checkouts exist.
    """
    registered: dict[RepoIdentity, list[Path]] = {}
    current: list[Path] | None = None
    for line in listing.splitlines():
        if not line.strip() or line.strip() == NOTHING_REGISTERED:
            continue
        indented = line[:1].isspace()
        match line.strip().split("\t"):
            case [identity, _gate] if identity and not indented:
                current = registered.setdefault(identity, [])
            case [_alias, path] if path and indented and current is not None:
                current.append(Path(path))
            case _:
                raise UnreadableSibling(
                    f"`onevcs repos` printed a line of no known shape: {line!r}"
                )
    return registered


def registry_listing(home: Path | None = None) -> dict[RepoIdentity, list[Path]]:
    """Every identity the registry under ``home`` lists, with its checkouts, in its order.

    ``home`` names a registry root; omitted, this host's registry as the suite copied it.
    Raises :class:`UnreadableSibling` where the installed `onevcs` cannot answer.
    """
    try:
        asked = subprocess.run(
            ["onevcs", "repos"],
            env=_environment(home),
            capture_output=True,
            timeout=120,
            check=False,
        )
    except OSError as error:
        raise UnreadableSibling(f"`onevcs repos` could not run ({error})") from error
    if asked.returncode != 0:
        raise UnreadableSibling(
            "`onevcs repos` could not list this host's registry "
            f"({asked.stderr.decode('utf-8', 'replace').strip()})"
        )
    try:
        listing = asked.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnreadableSibling(
            f"`onevcs repos` printed bytes that are not UTF-8 ({error})"
        ) from error
    return parse_listing(listing)


def registered_checkout_paths(home: Path | None = None) -> tuple[Path, ...]:
    """Every checkout path the registry lists, in its order; none where it cannot be read.

    For a caller that only needs to know where siblings live, such as the suite's guard
    on which directories are outside this workspace.
    """
    try:
        listed = registry_listing(home)
    except UnreadableSibling:
        return ()
    return tuple(path for paths in listed.values() for path in paths)


def scratch_registry(home: Path, checkouts: Iterable[Path]) -> Path:
    """A registry root at ``home`` in which the installed `onevcs` registered ``checkouts``.

    Registered one by one, in order, through `onevcs register` — the verb a host's own
    registration runs — so what a reader then lists is what the real CLI recorded. Each
    checkout's identity is resolved from its own `origin`.
    """
    for checkout in checkouts:
        registered = subprocess.run(
            ["onevcs", "register", str(checkout)],
            env={**os.environ, onevcs_state_snapshot.ONEVCS_HOME: str(home)},
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert registered.returncode == 0, (
            f"`onevcs register {checkout}` failed: {registered.stdout}{registered.stderr}"
        )
    return home


def discover_checkouts(home: Path | None = None) -> Discovery:
    """Every checkout the registry lists that this host holds, grouped by identity, in order.

    A registered path this host does not have — or has as no git checkout — is left out,
    as a sibling this host never registered is. A path this host has but cannot read is
    named in ``unreadable`` instead, as is a registry `onevcs` could not list, and every
    other is kept.
    """
    try:
        listed = registry_listing(home)
    except UnreadableSibling as error:
        return Discovery({}, (str(error),))
    found: dict[RepoIdentity, list[Path]] = {}
    unreadable: list[str] = []
    for identity, paths in listed.items():
        for path in paths:
            try:
                (path / ".git").stat()
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as error:
                unreadable.append(
                    f"{path}, a registered checkout of {identity}, cannot be read "
                    f"({error.strerror or error}); restore this process's access to it"
                )
                continue
            found.setdefault(identity, []).append(path)
    return Discovery(found, tuple(unreadable))


class Recipes(NamedTuple):
    """The recipes a repository defines, and whether they could be read at all."""

    #: False when `just` could not parse the repository's recipes, so `names` is not
    #: evidence of anything and a reconciliation must report rather than assert.
    readable: bool
    names: frozenset[str]
    #: Why the recipes could not be read where the runner could not run there at all;
    #: empty where it ran and refused or answered in a shape this reader does not know.
    unreadable: str = ""


def defined_recipes(root: Path) -> Recipes:
    """Every recipe `root`'s own justfile defines.

    Read through the tool that owns the definitions rather than by scanning text:
    `just --dump` is the recipe runner's own parse of its justfile, so what comes back
    is the set a `just <recipe>` run there would really resolve against — aliases and
    imports included, comments and documentation already gone.
    """
    try:
        dumped = subprocess.run(
            ["just", "--dump", "--dump-format", "json"],
            cwd=root,
            text=True,
            errors="replace",
            capture_output=True,
        )
    except OSError as error:
        return Recipes(
            readable=False,
            names=frozenset(),
            unreadable=f"{root} cannot be read: `just --dump` could not run there ({error})",
        )
    if dumped.returncode != 0:
        return Recipes(readable=False, names=frozenset())
    # The dump is the sibling's justfile as the runner parses it, so a document of a shape
    # this reader does not know is unreadable like a justfile `just` refuses, not a crash.
    try:
        document = json.loads(dumped.stdout)
    except ValueError:
        return Recipes(readable=False, names=frozenset())
    match document:
        case {"recipes": dict(recipes)} if isinstance(document.get("aliases") or {}, dict):
            return Recipes(
                readable=True,
                names=frozenset(recipes) | frozenset(document.get("aliases") or {}),
            )
        case _:
            return Recipes(readable=False, names=frozenset())
