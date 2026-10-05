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

import onevcs_state_snapshot

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
    """The checkouts a list names that this host holds, and a finding for each it could not read."""

    #: Each identity's held checkouts, in the order listed.
    held: dict[RepoIdentity, list[Path]]
    #: A listed checkout this host has but could not resolve an identity for, each with
    #: its path and why — for the caller to settle as drift rather than drop.
    unreadable: tuple[str, ...]


#: An identity as `onevcs` files one: a host, then an owner, then a name, any of them
#: further nested, with no whitespace. An origin normalizing to anything else names no
#: repository this host could register.
IDENTITY = re.compile(r"^[^/\s]+(?:/[^/\s]+){2,}$")


def _identity_of(path: Path) -> RepoIdentity | str:
    """``path``'s identity from its own `origin`, or raises :class:`UnreadableSibling`."""
    origin = sibling_git(path, "remote", "get-url", "origin")
    if origin.returncode != 0:
        raise UnreadableSibling(
            f"{path} is a listed git checkout that answered no origin "
            f"({origin.stderr.decode('utf-8', 'replace').strip()}); an identity is read off "
            "its origin, so give it one or drop it from config/onevcs.checkouts"
        )
    try:
        url = origin.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise UnreadableSibling(
            f"{path}'s origin is not UTF-8 ({error}), so it names no identity; correct "
            "that checkout's `remote.origin.url`"
        ) from error
    identity = normalized_identity(url)
    if not IDENTITY.match(identity):
        raise UnreadableSibling(
            f"{path}'s origin {url!r} names no host/owner/name identity; correct that "
            "checkout's `remote.origin.url`"
        )
    return identity


def discover_checkouts(manifest: Path = TRACKED_CHECKOUTS) -> Discovery:
    """Every checkout ``manifest`` lists that this host holds, grouped by identity, in order.

    Git is asked which identity a checkout really is rather than the path being
    trusted to say, so a directory named after one repository but cloned from another
    resolves to what it is. A path this host does not have — or has as no git checkout —
    is left out, for the reason the recipe skips it: the list names both layouts this
    repository dispatches from. A checkout this host has but cannot enter, run git in, or
    read an identity off is named in ``unreadable`` instead, and every other is kept.
    """
    found: dict[RepoIdentity, list[Path]] = {}
    unreadable: list[str] = []
    for path in listed_checkout_paths(manifest):
        try:
            (path / ".git").stat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as error:
            unreadable.append(
                f"{path} cannot be read ({error.strerror or error}), so which repository it "
                "checks out is unknown; restore this process's access to it"
            )
            continue
        try:
            identity = _identity_of(path)
        except UnreadableSibling as error:
            unreadable.append(str(error))
            continue
        found.setdefault(identity, []).append(path)
    return Discovery(found, tuple(unreadable))


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
    #: Listed checkouts whose identity could not be read at all, as discovery names them.
    unreadable: tuple[str, ...] = ()


def _resolved_publication_checkout(repo: str, home: Path | None) -> Path:
    """The publication checkout `onevcs resolve` answers for ``repo``.

    Raises :class:`UnreadableSibling` naming why for every way the answer cannot be read —
    no answer, a CLI that cannot run, bytes that are not UTF-8, a document that does not
    parse or one of a shape this reader does not know — so each is a finding for the caller
    to settle rather than an exception or a silent miss.
    """
    # The host's identities are only in the registry copy, which the suite exports to no
    # process: `tests/onevcs_state_snapshot.py` says why.
    environment: Mapping[str, str] = (
        onevcs_state_snapshot.host_registry_environment()
        if home is None
        else {**os.environ, "ONEVCS_HOME": str(home)}
    )
    asked_for = f"`onevcs resolve {repo}`"
    try:
        asked = subprocess.run(
            ["onevcs", "resolve", repo], env=environment, capture_output=True, check=False
        )
    except OSError as error:
        raise UnreadableSibling(f"{asked_for} could not run ({error})") from error
    if asked.returncode != 0:
        raise UnreadableSibling(
            f"{asked_for} answered no publication checkout "
            f"({asked.stderr.decode('utf-8', 'replace').strip()})"
        )
    try:
        answer = json.loads(asked.stdout.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise UnreadableSibling(
            f"{asked_for} answered bytes that are not UTF-8 ({error})"
        ) from error
    except ValueError as error:
        raise UnreadableSibling(
            f"{asked_for} answered a document that is not JSON ({error})"
        ) from error
    match answer:
        case {"publication_checkout": str(checkout)} if checkout:
            return Path(checkout).resolve()
        case _:
            raise UnreadableSibling(
                f"{asked_for} answered a document naming no `publication_checkout`: {answer!r}"
            )


def publication_resolutions(
    manifest: Path = TRACKED_CHECKOUTS, home: Path | None = None
) -> Resolutions:
    """Compare, per identity with several held checkouts, its origin form with its first alias.

    Among several checkouts of one identity, `onevcs resolve <origin>` selects the one
    whose alias sorts first: `store::resolve` at `onevcs` v0.21.0 answers the identity-key
    branch with ``registry.checkouts.iter().find(|(_, c)| c.identity == key)`` over a
    ``BTreeMap`` keyed by alias, so which checkout a plan naming its repository by origin
    publishes from is decided by alias order. This host's publication checkouts sort
    first by the accident of their names — `ai-orchestrator` before
    `ai-orchestrator-isolated` — and this is what notices a checkout registered later
    under a name that sorts earlier, before a publication is redirected into it. The
    live answer is read rather than the rule restated: both spellings are asked of the
    installed `onevcs`, under ``home`` when one is named and the suite's copy of this
    host's registry otherwise. The alias is the checkout's directory name, which is what `onevcs
    register` derives it from.
    """
    agreeing: list[RepoIdentity] = []
    disagreeing: list[Disagreement] = []
    unresolved: dict[RepoIdentity, str] = {}
    discovered = discover_checkouts(manifest)
    for identity, paths in discovered.held.items():
        if len(paths) < 2:
            continue
        first_alias = paths[0].name
        try:
            by_origin = _resolved_publication_checkout(identity, home)
            by_first_alias = _resolved_publication_checkout(first_alias, home)
        except UnreadableSibling as error:
            unresolved[identity] = str(error)
            continue
        if by_origin == by_first_alias:
            agreeing.append(identity)
        else:
            disagreeing.append(Disagreement(identity, by_origin, first_alias, by_first_alias))
    return Resolutions(tuple(agreeing), tuple(disagreeing), unresolved, discovered.unreadable)


def resolution_findings(resolved: Resolutions) -> list[str]:
    """Every way ``resolved`` shows the live registry selecting other than its first alias.

    A disagreement, an identity one spelling or the other could not be resolved for, and a
    listed checkout whose identity could not be read: each is a finding about the checkouts
    this host holds and the registry over them, worded for a reader to act on alone, for
    the caller to settle with `tests/sibling_facts.py`'s `settle_drift`.
    """
    return [
        *(
            f"{found.identity}: `onevcs resolve {found.identity}` publishes from "
            f"{found.by_origin}, while its first listed checkout {found.first_alias!r} is "
            f"{found.by_first_alias}; a checkout registered under a name that sorts earlier "
            "has redirected this identity's publications"
            for found in resolved.disagreeing
        ),
        *(
            f"{identity}: {why}, so which of its checkouts publishes cannot be compared"
            for identity, why in sorted(resolved.unresolved.items())
        ),
        *resolved.unreadable,
    ]


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
