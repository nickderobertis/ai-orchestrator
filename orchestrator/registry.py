"""Persistent repository identities and their local checkout aliases.

Publication workflow belongs to a normalized repository identity. Checkout aliases
only select a path; canonical, safety, and execution clones of the same origin can
therefore never acquire different publication workflows.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import NewType, cast

from . import gitops
from .coordination import advisory_lock, atomic_json
from .workspace import IdentityKey, RepoRef, Workflow, normalize_repo

Slug = NewType("Slug", str)
_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REGISTRY_VERSION = 2


class RegistryError(ValueError):
    """Registry data or a requested checkout failed validation."""


@dataclass(frozen=True)
class RegistryEntry:
    """Compatibility view of one checkout alias plus identity metadata."""

    path: str
    origin: str
    workflow: Workflow


@dataclass(frozen=True)
class RepositoryIdentity:
    origin: str
    workflow: Workflow


@dataclass(frozen=True)
class RegistrySelection:
    """Independent publication and execution decisions for one lifecycle."""

    alias: Slug
    publication_checkout: Path
    execution_checkout: Path
    identity: IdentityKey
    workflow: Workflow


@dataclass(frozen=True)
class CheckoutIdentity:
    """Identity-level publication metadata resolved from any checkout or worktree."""

    identity: IdentityKey
    workflow: Workflow
    publication_checkout: Path


@dataclass(frozen=True)
class WorkflowMigration:
    identity: IdentityKey
    workflow: Workflow
    aliases: tuple[Slug, ...]


@dataclass(frozen=True)
class RefreshResult:
    slug: Slug
    path: str
    refreshed: bool
    reason: str


def _base_dir() -> Path:
    """The orchestrator state root — registry file and managed clones live here."""
    # llmlint: ignore[boundary_inputs_validated] operator config env var, not untrusted input
    override = os.environ.get("AI_ORCHESTRATOR_HOME")
    return Path(override) if override else Path.home() / ".ai-orchestrator"


def _default_search_roots() -> tuple[Path, ...]:
    """Directories scanned for an existing on-disk checkout during ``resolve``."""
    # llmlint: ignore[boundary_inputs_validated] operator config; each root is checked in _find
    override = os.environ.get("AI_ORCHESTRATOR_SEARCH_ROOTS")
    if override is not None:
        return tuple(Path(part) for part in override.split(os.pathsep) if part)
    home = Path.home()
    candidates = (
        Path.cwd().resolve().parent,
        home,
        home / "src",
        home / "code",
        home / "projects",
        home / "repos",
    )
    return tuple(dict.fromkeys(candidates))


def _url_identity(url: str) -> IdentityKey:
    """Normalize equivalent clone URL spellings into one repository identity."""
    value = url.strip().rstrip("/")
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.removeprefix("git@github.com:")
    if value.startswith("ssh://git@github.com/"):
        value = "https://github.com/" + value.removeprefix("ssh://git@github.com/")
    if value.endswith(".git"):
        value = value[:-4]
    if value.startswith("file://"):
        value = value.removeprefix("file://")
    if value.startswith(("/", "./", "../", "~")):
        value = str(Path(value).expanduser().resolve())
    if value.lower().startswith("https://github.com/"):
        value = "https://github.com/" + value[len("https://github.com/") :].lower()
    return IdentityKey(value)


def _valid_origin(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not any(character in value for character in ("\0", "\n", "\r"))
    )


def _validate_alias(slug: object) -> Slug:
    if not isinstance(slug, str) or not _SLUG_PATTERN.fullmatch(slug):
        raise RegistryError(f"registry key {slug!r} must be a normalized owner/name slug")
    return Slug(slug)


def _parse_entry(slug: Slug, value: object) -> RegistryEntry:
    if not isinstance(value, dict):
        raise RegistryError(f"registry has an invalid entry for {str(slug)!r}")
    if set(value) != {"path", "origin", "workflow"}:
        raise RegistryError(f"registry entry {str(slug)!r} must contain path, origin, and workflow")
    path, origin, workflow = value["path"], value["origin"], value["workflow"]
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise RegistryError(f"registry entry {str(slug)!r} path must be an absolute string")
    if not _valid_origin(origin):
        raise RegistryError(f"registry entry {str(slug)!r} origin must be a non-empty string")
    if workflow not in ("local", "remote"):
        raise RegistryError(f"registry entry {str(slug)!r} workflow must be 'local' or 'remote'")
    return RegistryEntry(path, cast(str, origin), cast(Workflow, workflow))


def _conflict_message(identity: IdentityKey, items: Sequence[tuple[Slug, RegistryEntry]]) -> str:
    details = "\n".join(
        f"  {slug}: workflow={entry.workflow} path={entry.path}" for slug, entry in items
    )
    target = shlex.quote(str(items[0][0]))
    return (
        f"registry identity {str(identity)!r} has conflicting workflows:\n{details}\n"
        "Remediate all aliases atomically with: "
        f"just migrate-repo-workflow {target} --workflow <local|remote>"
    )


def _coalesce(
    entries: Mapping[Slug, RegistryEntry], *, allow_conflicts: bool = False
) -> dict[IdentityKey, RepositoryIdentity]:
    grouped: dict[IdentityKey, list[tuple[Slug, RegistryEntry]]] = {}
    for slug, entry in sorted(entries.items()):
        grouped.setdefault(_url_identity(entry.origin), []).append((slug, entry))
    identities: dict[IdentityKey, RepositoryIdentity] = {}
    for identity, items in grouped.items():
        workflows = {entry.workflow for _, entry in items}
        if len(workflows) != 1:
            if allow_conflicts:
                continue
            raise RegistryError(_conflict_message(identity, items))
        identities[identity] = RepositoryIdentity(items[0][1].origin, workflows.pop())
    return identities


def _read_registry(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"could not load registry {path}: {exc}") from exc


def _parse_raw(
    path: Path, raw: object, *, allow_conflicts: bool = False
) -> tuple[dict[Slug, RegistryEntry], dict[IdentityKey, RepositoryIdentity]]:
    if not isinstance(raw, dict):
        raise RegistryError(f"registry {path} must contain a JSON object")
    if "version" not in raw:
        legacy_entries = {
            slug: _parse_entry(slug, value)
            for raw_slug, value in raw.items()
            for slug in (_validate_alias(raw_slug),)
        }
        return legacy_entries, _coalesce(legacy_entries, allow_conflicts=allow_conflicts)
    if set(raw) != {"version", "identities", "checkouts"} or raw.get("version") != 2:
        raise RegistryError(
            f"registry {path} versioned format must contain version=2, identities, and checkouts"
        )
    raw_identities = raw["identities"]
    raw_checkouts = raw["checkouts"]
    if not isinstance(raw_identities, dict) or not isinstance(raw_checkouts, dict):
        raise RegistryError(f"registry {path} identities and checkouts must be JSON objects")
    identities: dict[IdentityKey, RepositoryIdentity] = {}
    for raw_key, value in raw_identities.items():
        if not isinstance(raw_key, str) or not _valid_origin(raw_key):
            raise RegistryError("registry identity key must be a non-empty string")
        if not isinstance(value, dict) or set(value) != {"origin", "workflow"}:
            raise RegistryError(f"registry identity {raw_key!r} must contain origin and workflow")
        origin, workflow = value["origin"], value["workflow"]
        if not _valid_origin(origin):
            raise RegistryError(f"registry identity {raw_key!r} origin must be non-empty")
        if workflow not in ("local", "remote"):
            raise RegistryError(
                f"registry identity {raw_key!r} workflow must be 'local' or 'remote'"
            )
        identity = IdentityKey(raw_key)
        if identity != _url_identity(cast(str, origin)):
            raise RegistryError(
                f"registry identity key {raw_key!r} does not match normalized origin"
            )
        identities[identity] = RepositoryIdentity(cast(str, origin), cast(Workflow, workflow))
    entries: dict[Slug, RegistryEntry] = {}
    for raw_slug, value in raw_checkouts.items():
        slug = _validate_alias(raw_slug)
        if not isinstance(value, dict) or set(value) != {"path", "identity"}:
            raise RegistryError(f"registry checkout {str(slug)!r} must contain path and identity")
        checkout_path, raw_identity = value["path"], value["identity"]
        if not isinstance(checkout_path, str) or not Path(checkout_path).is_absolute():
            raise RegistryError(f"registry checkout {str(slug)!r} path must be an absolute string")
        if not isinstance(raw_identity, str) or IdentityKey(raw_identity) not in identities:
            raise RegistryError(
                f"registry checkout {str(slug)!r} references unknown identity {raw_identity!r}"
            )
        metadata = identities[IdentityKey(raw_identity)]
        entries[slug] = RegistryEntry(checkout_path, metadata.origin, metadata.workflow)
    return entries, identities


def _serialize(entries: Mapping[Slug, RegistryEntry]) -> dict[str, object]:
    identities = _coalesce(entries)
    return {
        "version": _REGISTRY_VERSION,
        "identities": {
            str(identity): asdict(metadata) for identity, metadata in sorted(identities.items())
        },
        "checkouts": {
            str(slug): {"path": entry.path, "identity": str(_url_identity(entry.origin))}
            for slug, entry in sorted(entries.items())
        },
    }


class Registry:
    """A JSON-backed repository identity and checkout-alias registry."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _base_dir() / "repos.json"
        self.entries, self.identities = self._load()

    def _load(self) -> tuple[dict[Slug, RegistryEntry], dict[IdentityKey, RepositoryIdentity]]:
        if not self.path.exists():
            return {}, {}
        return _parse_raw(self.path, _read_registry(self.path))

    def _reload(self) -> None:
        self.entries, self.identities = self._load()

    def save(self) -> None:
        with advisory_lock(f"registry:{self.path.resolve()}"):
            current, _ = self._load()
            current.update(self.entries)
            payload = _serialize(current)
            atomic_json(self.path, payload)
            self.entries = current
            self.identities = _coalesce(current)

    def entry_for_checkout(self, path: str | Path) -> tuple[Slug, RegistryEntry] | None:
        """Resolve an exact or auxiliary checkout without alias-count ambiguity."""
        resolved = Path(path).expanduser().resolve()
        exact = sorted(
            (item for item in self.entries.items() if Path(item[1].path) == resolved),
            key=lambda item: item[0],
        )
        try:
            identity = _url_identity(gitops.remote_url(resolved))
        except (OSError, gitops.GitError):
            identity = None
        if exact:
            matching = (
                [item for item in exact if _url_identity(item[1].origin) == identity]
                if identity is not None
                else exact
            )
            if matching:
                return matching[0]
        if identity is None:
            return None
        matches = sorted(
            (item for item in self.entries.items() if _url_identity(item[1].origin) == identity),
            key=lambda item: item[0],
        )
        return matches[0] if matches else None

    def identity_for_checkout(self, path: str | Path) -> CheckoutIdentity | None:
        """Return identity workflow and registered publication path for any clone/worktree."""
        matched = self.entry_for_checkout(path)
        if matched is None:
            return None
        _, entry = matched
        return CheckoutIdentity(_url_identity(entry.origin), entry.workflow, Path(entry.path))

    @staticmethod
    def _valid_checkout(path: Path, expected_origin: str) -> bool:
        return (
            path.is_dir()
            and gitops.is_repo(path)
            and _url_identity(gitops.remote_url(path)) == _url_identity(expected_origin)
        )

    @staticmethod
    def _find(repo: RepoRef, roots: Sequence[str | Path]) -> Path | None:
        for root_value in roots:
            root = Path(root_value).expanduser()
            if not root.is_dir():
                continue
            for candidate in sorted(
                (path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name
            ):
                try:
                    if Registry._valid_checkout(candidate, repo.url):
                        return candidate.resolve()
                except gitops.GitError:
                    continue
        return None

    def _entries_for_identity(self, identity: IdentityKey) -> list[tuple[Slug, RegistryEntry]]:
        return sorted(
            (item for item in self.entries.items() if _url_identity(item[1].origin) == identity),
            key=lambda item: item[0],
        )

    def _store(
        self,
        repo: RepoRef,
        path: Path,
        workflow: Workflow | None,
        *,
        origin: str | None = None,
    ) -> Path:
        resolved = path.expanduser().resolve()
        actual_origin = origin if origin is not None else gitops.remote_url(resolved)
        identity = _url_identity(actual_origin)
        known = self.identities.get(identity)
        if known is not None and workflow is not None and known.workflow != workflow:
            items = self._entries_for_identity(identity)
            target = shlex.quote(str(items[0][0]))
            raise RegistryError(
                f"workflow={workflow} conflicts with registered identity {str(identity)!r} "
                f"workflow={known.workflow}; omit --workflow to inherit it, or migrate every "
                "alias atomically with: "
                f"just migrate-repo-workflow {target} --workflow {workflow}"
            )
        selected_workflow = known.workflow if known is not None else workflow or "remote"
        alias = Slug(repo.slug)
        previous = self.entries.get(alias)
        if previous is not None and _url_identity(previous.origin) != identity:
            raise RegistryError(
                f"checkout alias {repo.slug!r} already belongs to identity "
                f"{str(_url_identity(previous.origin))!r}"
            )
        for known_alias, entry in self._entries_for_identity(identity):
            self.entries[known_alias] = replace(entry, workflow=selected_workflow)
        self.entries[alias] = RegistryEntry(str(resolved), actual_origin, selected_workflow)
        self.identities[identity] = RepositoryIdentity(actual_origin, selected_workflow)
        self.save()
        return resolved

    def resolve(
        self,
        spec: str,
        *,
        search_roots: Sequence[str | Path] | None = None,
        clone_into: str | Path | None = None,
        default_workflow: Workflow = "remote",
    ) -> Path:
        repo = normalize_repo(spec)
        with advisory_lock(f"registry-resolve:{self.path.resolve()}:{repo.slug}"):
            self._reload()
            return self._resolve_unlocked(
                repo,
                search_roots=search_roots,
                clone_into=clone_into,
                default_workflow=default_workflow,
            )

    def _resolve_unlocked(
        self,
        repo: RepoRef,
        *,
        search_roots: Sequence[str | Path] | None,
        clone_into: str | Path | None,
        default_workflow: Workflow,
    ) -> Path:
        if repo.local:
            path = Path(repo.url)
            if not path.is_dir() or not gitops.is_repo(path):
                raise RegistryError(f"local repo {path} is not a git checkout")
            identity = _url_identity(gitops.remote_url(path))
            inherited = None if identity in self.identities else default_workflow
            return self._store(repo, path, inherited)
        expected_identity = _url_identity(repo.url)
        existing = self.entries.get(Slug(repo.slug))
        if existing is not None:
            try:
                if (
                    self._valid_checkout(Path(existing.path), existing.origin)
                    and _url_identity(existing.origin) == expected_identity
                ):
                    return Path(existing.path)
            except gitops.GitError:
                pass
        for _, candidate in self._entries_for_identity(expected_identity):
            try:
                if self._valid_checkout(Path(candidate.path), candidate.origin):
                    return self._store(repo, Path(candidate.path), None, origin=candidate.origin)
            except gitops.GitError:
                continue
        found = self._find(
            repo, search_roots if search_roots is not None else _default_search_roots()
        )
        if found is not None:
            return self._store(repo, found, default_workflow)
        destination = (
            Path(clone_into).expanduser()
            if clone_into is not None
            else _base_dir() / "repos" / repo.dir_key
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        gitops.clone(repo.url, destination)
        return self._store(repo, destination, default_workflow)

    def select(
        self,
        spec: str,
        *,
        execution_checkout: str | Path | None = None,
        search_roots: Sequence[str | Path] | None = None,
        clone_into: str | Path | None = None,
    ) -> RegistrySelection:
        """Select publication identity/path and an optional exact execution clone."""
        repo = normalize_repo(spec)
        publication = self.resolve(
            spec, search_roots=search_roots, clone_into=clone_into, default_workflow="remote"
        )
        self._reload()
        publication_match = self.entry_for_checkout(publication)
        if publication_match is None:
            raise RegistryError(f"resolved checkout {publication} has no repository identity")
        alias, entry = publication_match
        identity = _url_identity(entry.origin)
        execution = (
            Path(execution_checkout).expanduser().resolve()
            if execution_checkout is not None
            else publication
        )
        if not execution.is_dir() or not gitops.is_repo(execution):
            raise RegistryError(f"execution checkout {execution} is not a git checkout")
        try:
            execution_identity = _url_identity(gitops.remote_url(execution))
        except gitops.GitError as exc:
            raise RegistryError(
                f"could not validate execution checkout {execution}: {exc}"
            ) from exc
        if execution_identity != identity:
            raise RegistryError(
                f"execution checkout {execution} belongs to identity "
                f"{str(execution_identity)!r}, not publication identity {str(identity)!r}"
            )
        return RegistrySelection(
            alias=Slug(repo.slug) if Slug(repo.slug) in self.entries else alias,
            publication_checkout=publication.resolve(),
            execution_checkout=execution,
            identity=identity,
            workflow=self.identities[identity].workflow,
        )

    def register(
        self, spec: str, path: str | Path | None = None, *, workflow: Workflow | None = None
    ) -> Path:
        repo = normalize_repo(spec)
        chosen = (
            Path(path).expanduser().resolve()
            if path is not None
            else (Path(repo.url) if repo.local else self._find(repo, _default_search_roots()))
        )
        if chosen is None:
            raise RegistryError(f"no existing checkout found for {repo.slug}")
        try:
            valid = (
                chosen.is_dir() and gitops.is_repo(chosen)
                if repo.local
                else self._valid_checkout(chosen, repo.url)
            )
            if not valid:
                raise RegistryError(
                    f"{chosen} is not a git checkout whose origin matches {repo.url}"
                )
        except gitops.GitError as exc:
            raise RegistryError(f"could not validate checkout {chosen}: {exc}") from exc
        with advisory_lock(f"registry-resolve:{self.path.resolve()}:{repo.slug}"):
            self._reload()
            return self._store(repo, chosen, workflow)

    @classmethod
    def migrate_identity_workflow(
        cls,
        spec: str,
        workflow: Workflow,
        *,
        path: str | Path | None = None,
    ) -> WorkflowMigration:
        """Atomically migrate every alias of one identity, including legacy conflicts."""
        registry_path = Path(path) if path is not None else _base_dir() / "repos.json"
        with advisory_lock(f"registry:{registry_path.resolve()}"):
            if not registry_path.exists():
                raise RegistryError(
                    f"registry {registry_path} does not exist; register the repository first "
                    "with: just register-repo <repo> --workflow <local|remote>"
                )
            entries, _ = _parse_raw(
                registry_path, _read_registry(registry_path), allow_conflicts=True
            )
            target: IdentityKey | None = None
            direct = entries.get(Slug(spec))
            if direct is not None:
                target = _url_identity(direct.origin)
            candidate = Path(spec).expanduser()
            if target is None and candidate.exists() and gitops.is_repo(candidate):
                target = _url_identity(gitops.remote_url(candidate.resolve()))
            if target is None:
                target = _url_identity(normalize_repo(spec).url)
            aliases = tuple(
                slug
                for slug, entry in sorted(entries.items())
                if _url_identity(entry.origin) == target
            )
            if not aliases:
                raise RegistryError(
                    f"repository identity for {spec!r} is not registered; register it first "
                    "with: just register-repo <repo> --workflow <local|remote>"
                )
            migrated = dict(entries)
            for alias in aliases:
                migrated[alias] = replace(migrated[alias], workflow=workflow)
            atomic_json(registry_path, _serialize(migrated))
        return WorkflowMigration(target, workflow, aliases)

    def refresh(self, slug: str | None = None) -> list[RefreshResult]:
        with advisory_lock(f"registry:{self.path.resolve()}"):
            current, _ = self._load()
            current.update(self.entries)
            self.entries = current
            self.identities = _coalesce(current)
            return self._refresh_entries(slug)

    def _refresh_entries(self, slug: str | None) -> list[RefreshResult]:
        requested_slug = Slug(slug) if slug is not None else None
        if requested_slug is not None and requested_slug not in self.entries:
            raise RegistryError(f"repo {slug!r} is not registered")
        results: list[RefreshResult] = []
        for key in sorted([requested_slug] if requested_slug is not None else self.entries):
            entry = self.entries[key]
            path = Path(entry.path)
            if not path.exists() or not gitops.is_repo(path):
                results.append(
                    RefreshResult(key, entry.path, False, "checkout is missing or not a git repo")
                )
                continue
            try:
                origin_matches = self._valid_checkout(path, entry.origin)
            except gitops.GitError as exc:
                results.append(RefreshResult(key, entry.path, False, str(exc)))
                continue
            if not origin_matches:
                results.append(
                    RefreshResult(key, entry.path, False, "origin does not match registered origin")
                )
                continue
            if gitops.is_dirty(path):
                results.append(RefreshResult(key, entry.path, False, "checkout is dirty"))
                continue
            try:
                with advisory_lock(f"git:{gitops.common_dir(path)}"):
                    gitops.fetch(path)
                    branch = gitops.default_branch(path)
                    if gitops.current_branch(path) != branch:
                        results.append(
                            RefreshResult(
                                key,
                                entry.path,
                                False,
                                f"default branch {branch} is not checked out",
                            )
                        )
                        continue
                    if not gitops.is_ancestor(path, gitops.head_sha(path), f"origin/{branch}"):
                        results.append(
                            RefreshResult(
                                key,
                                entry.path,
                                False,
                                f"checkout has diverged from origin/{branch}; fast-forward refused",
                            )
                        )
                        continue
                    gitops.merge_ff_only(path, f"origin/{branch}")
            except gitops.GitError as exc:
                results.append(RefreshResult(key, entry.path, False, str(exc)))
            else:
                results.append(RefreshResult(key, entry.path, True, f"fast-forwarded {branch}"))
        return results


def main_register(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register a repository checkout alias")
    parser.add_argument("spec")
    parser.add_argument("path", nargs="?")
    parser.add_argument("--workflow", choices=("local", "remote"))
    args = parser.parse_args(argv)
    try:
        registry = Registry()
        path = registry.register(args.spec, args.path, workflow=args.workflow)
        alias = Slug(normalize_repo(args.spec).slug)
        entry = registry.entries[alias]
    except RegistryError as exc:
        parser.error(str(exc))
    print(
        f"registered checkout={path} alias={alias} identity={_url_identity(entry.origin)} "
        f"publication_workflow={entry.workflow}"
    )
    return 0


def main_migrate_workflow(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Atomically migrate publication workflow for a repository identity"
    )
    parser.add_argument("spec", help="registered alias, checkout path, or repository identity")
    parser.add_argument("--workflow", choices=("local", "remote"), required=True)
    args = parser.parse_args(argv)
    try:
        result = Registry.migrate_identity_workflow(args.spec, args.workflow)
    except (RegistryError, gitops.GitError, ValueError) as exc:
        parser.error(str(exc))
    aliases = ",".join(str(alias) for alias in result.aliases)
    print(
        f"migrated identity={result.identity} publication_workflow={result.workflow} "
        f"aliases={aliases}"
    )
    return 0


def main_repos(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List repository identities and checkout aliases")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        registry = Registry()
        refresh = registry.refresh() if args.refresh else []
    except RegistryError as exc:
        parser.error(str(exc))
    if args.format == "json":
        payload: dict[str, object] = {
            "identities": {
                str(identity): asdict(metadata)
                for identity, metadata in sorted(registry.identities.items())
            },
            "checkouts": {
                str(slug): {
                    "path": entry.path,
                    "identity": str(_url_identity(entry.origin)),
                }
                for slug, entry in sorted(registry.entries.items())
            },
        }
        if args.refresh:
            payload["refresh"] = [asdict(result) for result in refresh]
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        for identity, metadata in sorted(registry.identities.items()):
            print(f"identity\t{identity}\t{metadata.workflow}\t{metadata.origin}")
        for slug, entry in sorted(registry.entries.items()):
            print(f"checkout\t{slug}\t{entry.path}\t{_url_identity(entry.origin)}")
        for result in refresh:
            print(f"{result.slug}: {'updated' if result.refreshed else 'skipped'}: {result.reason}")
    return 0
