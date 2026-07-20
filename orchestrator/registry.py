"""Persistent repository identities and their local checkout aliases.

Publication workflow belongs to a normalized repository identity. Checkout aliases
only select a path; canonical, safety, and execution clones of the same origin can
therefore never acquire different publication workflows.
"""

# llmlint: ignore-file[changed_behavior_has_e2e] registry migration/CLI journeys are e2e;
# legacy workflow/type backfill combinations reuse the unit-proven atomic helper.
# llmlint: ignore-file[modern_domain_modeling] gate None exists only while loading v2/v3;
# v4 deliberately serializes command templates or the explicit no-op sentinel.

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import NewType, cast

from . import gitops
from .coordination import advisory_lock, atomic_json, observe_harness
from .verify import NOOP_GATE, detect_gate_candidates
from .workspace import IdentityKey, RepoRef, RepositoryType, Workflow, normalize_repo

Slug = NewType("Slug", str)
_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REGISTRY_VERSION = 4
_REPOSITORY_TYPES = ("single-owner", "team")


def _is_local_alias_spec(spec: str) -> bool:
    """Return whether ``spec`` is a bare alias in the reserved local namespace."""
    stripped = spec.strip()
    return bool(_SLUG_PATTERN.fullmatch(stripped) and stripped.startswith("local/"))


class RegistryError(ValueError):
    """Registry data or a requested checkout failed validation."""


def _validate_repository_type(value: object) -> RepositoryType:
    if value not in _REPOSITORY_TYPES:
        raise RegistryError("repo_type must be 'single-owner' or 'team'")
    return cast(RepositoryType, value)


@dataclass(frozen=True)
class RegistryEntry:
    """Compatibility view of one checkout alias plus identity metadata."""

    path: str
    origin: str
    workflow: Workflow
    repo_type: RepositoryType | None = "single-owner"
    gate: str | None = NOOP_GATE


@dataclass(frozen=True)
class RepositoryIdentity:
    origin: str
    workflow: Workflow
    repo_type: RepositoryType | None
    gate: str | None = NOOP_GATE


@dataclass(frozen=True)
class RegistrySelection:
    """Independent publication and execution decisions for one lifecycle."""

    alias: Slug
    publication_checkout: Path
    execution_checkout: Path
    identity: IdentityKey
    workflow: Workflow
    repo_type: RepositoryType
    gate: str


@dataclass(frozen=True)
class CheckoutIdentity:
    """Identity-level publication metadata resolved from any checkout or worktree."""

    identity: IdentityKey
    workflow: Workflow
    repo_type: RepositoryType
    publication_checkout: Path
    gate: str


@dataclass(frozen=True)
class WorkflowMigration:
    identity: IdentityKey
    workflow: Workflow
    aliases: tuple[Slug, ...]


@dataclass(frozen=True)
class RepositoryTypeMigration:
    identity: IdentityKey
    repo_type: RepositoryType
    workflow: Workflow
    aliases: tuple[Slug, ...]


@dataclass(frozen=True)
class GateMigration:
    identity: IdentityKey
    gate: str
    aliases: tuple[Slug, ...]


def _validate_gate(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(c in value for c in ("\0", "\n", "\r"))
    ):
        raise RegistryError("gate must be a non-empty, single-line command template")
    if "{" in value.replace("{base}", "") or "}" in value.replace("{base}", ""):
        raise RegistryError("gate may contain only the {base} placeholder")
    try:
        parsed = shlex.split(value)
    except ValueError as exc:
        raise RegistryError(f"gate is not a valid command template: {exc}") from exc
    if not parsed:
        raise RegistryError("gate must contain an executable command")
    for part in parsed:
        surrounding = part.replace("{base}", "")
        if "{base}" in part and re.search(r"[\s;&|`$()<>]", surrounding):
            raise RegistryError("gate {base} placeholder must be an argv value, not command source")
    shells = {"sh", "bash", "dash", "zsh", "ksh"}
    for shell_index, executable in enumerate(parsed):
        if Path(executable).name not in shells:
            continue
        for option_index in range(shell_index + 1, len(parsed)):
            option = parsed[option_index]
            if not option.startswith("-"):
                break
            if "c" in option[1:]:
                source_index = option_index + 1
                if source_index < len(parsed) and "{base}" in parsed[source_index]:
                    raise RegistryError(
                        "gate {base} placeholder must be an argv value, not shell source"
                    )
                break
    return value


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


def _github_owner(origin: str) -> str:
    """Return the owner from a normalized GitHub origin, never from a spec default."""
    normalized = str(_url_identity(origin))
    match = re.fullmatch(r"https://github\.com/([^/]+)/[^/]+", normalized, re.IGNORECASE)
    if match is None:
        raise RegistryError(
            f"cannot infer repository type: normalized origin {normalized!r} is not GitHub; "
            "pass --repo-type explicitly"
        )
    return match.group(1)


def _authenticated_login() -> str:
    proc = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = proc.stderr.strip() or proc.stdout.strip() or "no login returned"
        raise RegistryError(
            "cannot infer repository type from the authenticated GitHub user: "
            f"{detail}; pass --repo-type explicitly"
        )
    return proc.stdout.strip()


def infer_repository_type(
    origin: str, *, login: Callable[[], str] = _authenticated_login
) -> RepositoryType:
    """Classify a GitHub identity by authenticated login versus origin owner."""
    owner = _github_owner(origin)
    try:
        authenticated = login().strip()
    except RegistryError:
        raise
    except Exception as exc:
        raise RegistryError(
            "cannot infer repository type from the authenticated GitHub user; "
            "pass --repo-type explicitly"
        ) from exc
    if not authenticated:
        raise RegistryError(
            "cannot infer repository type: authenticated GitHub login is empty; "
            "pass --repo-type explicitly"
        )
    return "single-owner" if authenticated.casefold() == owner.casefold() else "team"


def _valid_origin(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not any(character in value for character in ("\0", "\n", "\r"))
    )


def validate_identity_key(value: str) -> IdentityKey:
    """Accept only a canonical normalized origin identity from external data."""
    if not _valid_origin(value) or str(_url_identity(value)) != value:
        raise RegistryError(f"repository identity {value!r} is not a normalized origin")
    return IdentityKey(value)


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
    return RegistryEntry(path, cast(str, origin), cast(Workflow, workflow), None)


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
        repo_types = {entry.repo_type for _, entry in items if entry.repo_type is not None}
        if len(repo_types) > 1:
            raise RegistryError(f"registry identity {str(identity)!r} has conflicting repo types")
        repo_type = next(iter(repo_types), None)
        workflow = workflows.pop()
        if repo_type == "team" and workflow == "local":
            raise RegistryError(
                f"registry identity {str(identity)!r} cannot combine repo_type=team "
                "with workflow=local"
            )
        gates = {entry.gate for _, entry in items if entry.gate is not None}
        if len(gates) > 1:
            raise RegistryError(f"registry identity {str(identity)!r} has conflicting gates")
        identities[identity] = RepositoryIdentity(
            items[0][1].origin, workflow, repo_type, next(iter(gates), None)
        )
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
        legacy_entries: dict[Slug, RegistryEntry] = {}
        for raw_slug, value in raw.items():
            slug = _validate_alias(raw_slug)
            entry = _parse_entry(slug, value)
            legacy_entries[slug] = replace(
                entry, repo_type=("single-owner" if entry.workflow == "local" else None)
            )
        return legacy_entries, _coalesce(legacy_entries, allow_conflicts=allow_conflicts)
    version = raw.get("version")
    if set(raw) != {"version", "identities", "checkouts"} or version not in (2, 3, 4):
        raise RegistryError(
            f"registry {path} versioned format must contain version=2, version=3, or version=4, "
            "identities, and checkouts"
        )
    raw_identities = raw["identities"]
    raw_checkouts = raw["checkouts"]
    if not isinstance(raw_identities, dict) or not isinstance(raw_checkouts, dict):
        raise RegistryError(f"registry {path} identities and checkouts must be JSON objects")
    identities: dict[IdentityKey, RepositoryIdentity] = {}
    for raw_key, value in raw_identities.items():
        if not isinstance(raw_key, str) or not _valid_origin(raw_key):
            raise RegistryError("registry identity key must be a non-empty string")
        expected = {"origin", "workflow"}
        if version in (3, 4):
            expected.add("repo_type")
        if version == 4:
            expected.add("gate")
        if not isinstance(value, dict) or set(value) != expected:
            fields = (
                "origin, workflow, repo_type, and gate"
                if version == 4
                else ("origin, workflow, and repo_type" if version == 3 else "origin and workflow")
            )
            raise RegistryError(f"registry identity {raw_key!r} must contain {fields}")
        origin, workflow = value["origin"], value["workflow"]
        if not _valid_origin(origin):
            raise RegistryError(f"registry identity {raw_key!r} origin must be non-empty")
        if workflow not in ("local", "remote"):
            raise RegistryError(
                f"registry identity {raw_key!r} workflow must be 'local' or 'remote'"
            )
        raw_repo_type = value.get("repo_type")
        if version in (3, 4) and raw_repo_type not in _REPOSITORY_TYPES:
            raise RegistryError(
                f"registry identity {raw_key!r} repo_type must be 'single-owner' or 'team'"
            )
        repo_type = cast(RepositoryType | None, raw_repo_type)
        if version == 2 and workflow == "local":
            repo_type = "single-owner"
        if repo_type == "team" and workflow == "local":
            raise RegistryError(
                f"registry identity {raw_key!r} cannot combine repo_type=team with workflow=local"
            )
        identity = IdentityKey(raw_key)
        if identity != _url_identity(cast(str, origin)):
            raise RegistryError(
                f"registry identity key {raw_key!r} does not match normalized origin"
            )
        gate = _validate_gate(value["gate"]) if version == 4 else None
        identities[identity] = RepositoryIdentity(
            cast(str, origin), cast(Workflow, workflow), repo_type, gate
        )
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
        entries[slug] = RegistryEntry(
            checkout_path, metadata.origin, metadata.workflow, metadata.repo_type, metadata.gate
        )
    return entries, identities


def _serialize(entries: Mapping[Slug, RegistryEntry]) -> dict[str, object]:
    identities = _coalesce(entries)
    missing = [
        str(identity) for identity, metadata in identities.items() if metadata.repo_type is None
    ]
    if missing:
        raise RegistryError(
            "cannot persist unclassified repository identities: "
            + ", ".join(sorted(missing))
            + "; pass --repo-type explicitly"
        )
    for metadata in identities.values():
        _validate_repository_type(metadata.repo_type)
        _validate_gate(metadata.gate)
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


def _classify_legacy_entries(
    entries: Mapping[Slug, RegistryEntry],
) -> dict[Slug, RegistryEntry]:
    """Return fully classified entries, preserving all-or-nothing callers."""
    classified = dict(entries)
    identities = _coalesce(classified)
    for identity, metadata in identities.items():
        if metadata.repo_type is not None:
            continue
        repo_type: RepositoryType = (
            "single-owner"
            if metadata.workflow == "local"
            else infer_repository_type(metadata.origin)
        )
        workflow: Workflow = "remote" if repo_type == "team" else metadata.workflow
        for alias, entry in list(classified.items()):
            if _url_identity(entry.origin) == identity:
                classified[alias] = replace(entry, workflow=workflow, repo_type=repo_type)
    return classified


def _backfill_gates(entries: Mapping[Slug, RegistryEntry]) -> dict[Slug, RegistryEntry]:
    migrated = dict(entries)
    for identity in _coalesce(migrated):
        aliases = [item for item in migrated.items() if _url_identity(item[1].origin) == identity]
        known = next((entry.gate for _, entry in aliases if entry.gate is not None), None)
        if known is None:
            candidates = detect_gate_candidates(aliases[0][1].path)
            known = candidates[0] if candidates else NOOP_GATE
        for alias, entry in aliases:
            migrated[alias] = replace(entry, gate=known)
    return migrated


def _target_identity(entries: Mapping[Slug, RegistryEntry], spec: str) -> IdentityKey:
    direct = entries.get(Slug(spec))
    if direct is not None:
        return _url_identity(direct.origin)
    candidate = Path(spec).expanduser()
    if candidate.exists() and gitops.is_repo(candidate):
        return _url_identity(gitops.remote_url(candidate.resolve()))
    return _url_identity(normalize_repo(spec).url)


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

    def repo_ref(self, spec: str) -> RepoRef:
        """Normalize ``spec``, preferring an exact registered checkout alias."""
        normalized = normalize_repo(spec)
        entry = self.entries.get(Slug(spec.strip()))
        if entry is not None:
            alias = str(Slug(spec.strip()))
            owner, name = alias.split("/", 1)
            return RepoRef(owner, name, entry.path, local=True)
        if _is_local_alias_spec(spec):
            raise RegistryError(
                f"unknown local checkout alias {spec.strip()!r}; run 'just repos' "
                "to list registered checkouts"
            )
        return normalized

    def checkout_path(self, spec: str | Path) -> Path:
        """Resolve a checkout alias or preserve an explicit filesystem path."""
        raw = str(spec)
        entry = self.entries.get(Slug(raw.strip()))
        if entry is not None:
            return Path(entry.path)
        if _is_local_alias_spec(raw):
            raise RegistryError(
                f"unknown local checkout alias {raw.strip()!r}; run 'just repos' "
                "to list registered checkouts"
            )
        return Path(raw).expanduser().resolve()

    def _set_identity_type(
        self, identity: IdentityKey, repo_type: RepositoryType, *, workflow: Workflow | None = None
    ) -> None:
        metadata = self.identities[identity]
        selected_workflow = workflow or metadata.workflow
        if repo_type == "team":
            selected_workflow = "remote"
        for alias, entry in self._entries_for_identity(identity):
            self.entries[alias] = replace(entry, workflow=selected_workflow, repo_type=repo_type)
        self.identities[identity] = RepositoryIdentity(
            metadata.origin, selected_workflow, repo_type, metadata.gate
        )

    def _classify_missing(self) -> None:
        """Classify every legacy identity in memory so schema v4 can be written."""
        for identity, metadata in list(self.identities.items()):
            if metadata.repo_type is not None:
                continue
            repo_type: RepositoryType = (
                "single-owner"
                if metadata.workflow == "local"
                else infer_repository_type(metadata.origin)
            )
            self._set_identity_type(identity, repo_type)

    def _detect_missing_gates(self) -> None:
        for identity, metadata in list(self.identities.items()):
            if metadata.gate is not None:
                continue
            aliases = self._entries_for_identity(identity)
            candidates = detect_gate_candidates(aliases[0][1].path) if aliases else []
            gate = candidates[0] if candidates else NOOP_GATE
            for alias, entry in aliases:
                self.entries[alias] = replace(entry, gate=gate)
            self.identities[identity] = replace(metadata, gate=gate)

    def migrate_legacy(self) -> None:
        """Lazily upgrade a flat/v2/v3 registry to v4 in one atomic replacement."""
        if not self.path.exists():
            return
        with advisory_lock(f"registry:{self.path.resolve()}"):
            raw = _read_registry(self.path)
            if isinstance(raw, dict) and raw.get("version") == _REGISTRY_VERSION:
                self._reload()
                return
            self._reload()
            self._classify_missing()
            self._detect_missing_gates()
            atomic_json(self.path, _serialize(self.entries))

    def repository_type(
        self, identity: IdentityKey, *, override: RepositoryType | None = None
    ) -> RepositoryType:
        """Resolve a run override or the stored/inferred identity type."""
        if override is not None:
            return _validate_repository_type(override)
        metadata = self.identities.get(identity)
        if metadata is None:
            raise RegistryError(f"repository identity {str(identity)!r} is not registered")
        raw = _read_registry(self.path) if self.path.exists() else None
        if metadata.repo_type is None or not (
            isinstance(raw, dict) and raw.get("version") == _REGISTRY_VERSION
        ):
            self.migrate_legacy()
            metadata = self.identities[identity]
        assert metadata.repo_type is not None
        return metadata.repo_type

    def save(self) -> None:
        with advisory_lock(f"registry:{self.path.resolve()}"):
            current, _ = self._load()
            current.update(self.entries)
            self.entries = current
            self.identities = _coalesce(current)
            self._classify_missing()
            self._detect_missing_gates()
            payload = _serialize(self.entries)
            atomic_json(self.path, payload)
            self.identities = _coalesce(self.entries)

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

    def identity_for_checkout(
        self, path: str | Path, *, repo_type: RepositoryType | None = None
    ) -> CheckoutIdentity | None:
        """Return identity workflow and registered publication path for any clone/worktree."""
        matched = self.entry_for_checkout(path)
        if matched is None:
            return None
        _, entry = matched
        identity = _url_identity(entry.origin)
        effective_type = self.repository_type(identity, override=repo_type)
        metadata = self.identities[identity]
        if metadata.gate is None:
            self.migrate_legacy()
            metadata = self.identities[identity]
        assert metadata.gate is not None
        return CheckoutIdentity(
            identity, metadata.workflow, effective_type, Path(entry.path), metadata.gate
        )

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
        repo_type: RepositoryType | None,
        gate: str | None = None,
        *,
        origin: str | None = None,
    ) -> Path:
        if repo_type is not None:
            _validate_repository_type(repo_type)
        resolved = path.expanduser().resolve()
        actual_origin = origin if origin is not None else gitops.remote_url(resolved)
        identity = _url_identity(actual_origin)
        known = self.identities.get(identity)
        if gate is not None:
            _validate_gate(gate)
        selected_gate = known.gate if known is not None else gate
        if selected_gate is None:
            candidates = detect_gate_candidates(resolved)
            selected_gate = candidates[0] if candidates else NOOP_GATE
        selected_gate = _validate_gate(selected_gate)
        if known is not None and gate is not None and known.gate != gate:
            raise RegistryError(
                f"gate={gate!r} conflicts with registered identity {str(identity)!r}; "
                "migrate every alias atomically with: just migrate-repo-gate <repo> --gate <cmd>"
            )
        if known is not None and workflow is not None and known.workflow != workflow:
            items = self._entries_for_identity(identity)
            target = shlex.quote(str(items[0][0]))
            raise RegistryError(
                f"workflow={workflow} conflicts with registered identity {str(identity)!r} "
                f"workflow={known.workflow}; omit --workflow to inherit it, or migrate every "
                "alias atomically with: "
                f"just migrate-repo-workflow {target} --workflow {workflow}"
            )
        if (
            known is not None
            and known.repo_type is not None
            and repo_type is not None
            and known.repo_type != repo_type
        ):
            items = self._entries_for_identity(identity)
            target = shlex.quote(str(items[0][0]))
            raise RegistryError(
                f"repo_type={repo_type} conflicts with registered identity {str(identity)!r} "
                f"repo_type={known.repo_type}; omit --repo-type to inherit it, or migrate "
                f"atomically with: just migrate-repo-type {target} --repo-type {repo_type}"
            )
        selected_workflow = known.workflow if known is not None else workflow or "remote"
        selected_type = (
            known.repo_type if known is not None and known.repo_type is not None else repo_type
        )
        if selected_type is None:
            selected_type = (
                "single-owner"
                if selected_workflow == "local"
                else infer_repository_type(actual_origin)
            )
        if selected_type == "team" and selected_workflow == "local":
            raise RegistryError("repo_type=team cannot be registered with workflow=local")
        if selected_type == "team":
            selected_workflow = "remote"
        alias = Slug(repo.slug)
        previous = self.entries.get(alias)
        if previous is not None and _url_identity(previous.origin) != identity:
            raise RegistryError(
                f"checkout alias {repo.slug!r} already belongs to identity "
                f"{str(_url_identity(previous.origin))!r}"
            )
        for known_alias, entry in self._entries_for_identity(identity):
            self.entries[known_alias] = replace(
                entry, workflow=selected_workflow, repo_type=selected_type, gate=selected_gate
            )
        self.entries[alias] = RegistryEntry(
            str(resolved), actual_origin, selected_workflow, selected_type, selected_gate
        )
        self.identities[identity] = RepositoryIdentity(
            actual_origin, selected_workflow, selected_type, selected_gate
        )
        self.save()
        return resolved

    def resolve(
        self,
        spec: str,
        *,
        search_roots: Sequence[str | Path] | None = None,
        clone_into: str | Path | None = None,
        default_workflow: Workflow = "remote",
        repo_type: RepositoryType | None = None,
    ) -> Path:
        repo = self.repo_ref(spec)
        with advisory_lock(f"registry-resolve:{self.path.resolve()}:{repo.slug}"):
            self._reload()
            return self._resolve_unlocked(
                repo,
                search_roots=search_roots,
                clone_into=clone_into,
                default_workflow=default_workflow,
                repo_type=repo_type,
            )

    def _resolve_unlocked(
        self,
        repo: RepoRef,
        *,
        search_roots: Sequence[str | Path] | None,
        clone_into: str | Path | None,
        default_workflow: Workflow,
        repo_type: RepositoryType | None,
    ) -> Path:
        if repo.local:
            path = Path(repo.url)
            if not path.is_dir() or not gitops.is_repo(path):
                raise RegistryError(f"local repo {path} is not a git checkout")
            identity = _url_identity(gitops.remote_url(path))
            inherited = None if identity in self.identities else default_workflow
            return self._store(repo, path, inherited, repo_type)
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
                    return self._store(
                        repo, Path(candidate.path), None, repo_type, origin=candidate.origin
                    )
            except gitops.GitError:
                continue
        found = self._find(
            repo, search_roots if search_roots is not None else _default_search_roots()
        )
        if found is not None:
            return self._store(repo, found, default_workflow, repo_type)
        destination = (
            Path(clone_into).expanduser()
            if clone_into is not None
            else _base_dir() / "repos" / repo.dir_key
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        # llmlint: ignore[changed_behavior_has_e2e] Clone itself is an established registry E2E
        # boundary; the tracked lifecycle E2E proves the shared setup event's journal and
        # telemetry propagation for repository setup operations.
        started = time.monotonic()
        gitops.clone(repo.url, destination)
        observe_harness(
            "setup-finished",
            {"operation": "clone", "seconds": max(0.0, time.monotonic() - started)},
        )
        return self._store(repo, destination, default_workflow, repo_type)

    def select(
        self,
        spec: str,
        *,
        execution_checkout: str | Path | None = None,
        search_roots: Sequence[str | Path] | None = None,
        clone_into: str | Path | None = None,
        repo_type: RepositoryType | None = None,
    ) -> RegistrySelection:
        """Select publication identity/path and an optional exact execution clone."""
        repo = self.repo_ref(spec)
        self._reload()
        try:
            expected = (
                _url_identity(gitops.remote_url(Path(repo.url)))
                if repo.local
                else _url_identity(repo.url)
            )
        except (OSError, gitops.GitError):
            expected = _url_identity(repo.url)
        candidates = self._entries_for_identity(expected)
        preferred = self.entries.get(Slug(repo.slug))
        ordered = (
            [(Slug(repo.slug), preferred)] + [item for item in candidates if item[0] != repo.slug]
            if preferred is not None and _url_identity(preferred.origin) == expected
            else candidates
        )
        publication = next(
            (
                Path(entry.path)
                for _, entry in ordered
                if self._valid_checkout(Path(entry.path), entry.origin)
            ),
            None,
        )
        if publication is None:
            publication = self.resolve(
                spec,
                search_roots=search_roots,
                clone_into=clone_into,
                default_workflow="remote",
                repo_type=repo_type,
            )
        self._reload()
        publication_match = self.entry_for_checkout(publication)
        if publication_match is None:
            raise RegistryError(f"resolved checkout {publication} has no repository identity")
        alias, entry = publication_match
        identity = _url_identity(entry.origin)
        effective_type = self.repository_type(identity, override=repo_type)
        execution = (
            self.checkout_path(execution_checkout).expanduser().resolve()
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
            repo_type=effective_type,
            gate=self.identities[identity].gate or NOOP_GATE,
        )

    def register(
        self,
        spec: str,
        path: str | Path | None = None,
        *,
        workflow: Workflow | None = None,
        repo_type: RepositoryType | None = None,
        gate: str | None = None,
    ) -> Path:
        try:
            repo = normalize_repo(spec)
        except ValueError as exc:
            raise RegistryError(str(exc)) from exc
        with advisory_lock(f"registry-resolve:{self.path.resolve()}:{repo.slug}"):
            self._reload()
            chosen = (
                Path(path).expanduser().resolve()
                if path is not None
                else (Path(repo.url) if repo.local else self._find(repo, _default_search_roots()))
            )
            if chosen is None:
                chosen = _base_dir() / "repos" / repo.dir_key
                chosen.parent.mkdir(parents=True, exist_ok=True)
                gitops.clone(repo.url, chosen)
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
            return self._store(repo, chosen, workflow, repo_type, gate)

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
                entry = migrated[alias]
                if workflow == "local" and entry.repo_type == "team":
                    raise RegistryError(
                        "cannot migrate workflow to local for repo_type=team; migrate the "
                        "repository type to single-owner first"
                    )
                migrated[alias] = replace(
                    entry,
                    workflow=workflow,
                    repo_type=("single-owner" if workflow == "local" else entry.repo_type),
                )
            migrated = _backfill_gates(_classify_legacy_entries(migrated))
            atomic_json(registry_path, _serialize(migrated))
        return WorkflowMigration(target, workflow, aliases)

    @classmethod
    def migrate_identity_type(
        cls,
        spec: str,
        repo_type: RepositoryType,
        *,
        path: str | Path | None = None,
    ) -> RepositoryTypeMigration:
        """Atomically change type for one identity; team always uses remote workflow."""
        _validate_repository_type(repo_type)
        registry_path = Path(path) if path is not None else _base_dir() / "repos.json"
        with advisory_lock(f"registry:{registry_path.resolve()}"):
            if not registry_path.exists():
                raise RegistryError(
                    f"registry {registry_path} does not exist; register the repository first "
                    "with: just register-repo <repo> --repo-type <single-owner|team>"
                )
            entries, _ = _parse_raw(registry_path, _read_registry(registry_path))
            target = _target_identity(entries, spec)
            aliases = tuple(
                slug
                for slug, entry in sorted(entries.items())
                if _url_identity(entry.origin) == target
            )
            if not aliases:
                raise RegistryError(
                    f"repository identity for {spec!r} is not registered; register it first "
                    "with: just register-repo <repo> --repo-type <single-owner|team>"
                )
            migrated = dict(entries)
            workflow = migrated[aliases[0]].workflow
            if repo_type == "team":
                workflow = "remote"
            for alias in aliases:
                migrated[alias] = replace(migrated[alias], repo_type=repo_type, workflow=workflow)
            migrated = _backfill_gates(_classify_legacy_entries(migrated))
            atomic_json(registry_path, _serialize(migrated))
        return RepositoryTypeMigration(target, repo_type, workflow, aliases)

    @classmethod
    def migrate_identity_gate(
        cls, spec: str, gate: str, *, path: str | Path | None = None
    ) -> GateMigration:
        """Atomically change the verification gate for every alias of an identity."""
        selected_gate = _validate_gate(gate)
        registry_path = Path(path) if path is not None else _base_dir() / "repos.json"
        with advisory_lock(f"registry:{registry_path.resolve()}"):
            if not registry_path.exists():
                raise RegistryError(f"registry {registry_path} does not exist; register it first")
            entries, _ = _parse_raw(registry_path, _read_registry(registry_path))
            target = _target_identity(entries, spec)
            aliases = tuple(
                slug
                for slug, entry in sorted(entries.items())
                if _url_identity(entry.origin) == target
            )
            if not aliases:
                raise RegistryError(
                    f"repository identity for {spec!r} is not registered; use a known alias "
                    "from 'just repos' or register it with 'just register-repo <checkout>'"
                )
            migrated = dict(entries)
            for alias in aliases:
                migrated[alias] = replace(migrated[alias], gate=selected_gate)
            migrated = _backfill_gates(_classify_legacy_entries(migrated))
            atomic_json(registry_path, _serialize(migrated))
        return GateMigration(target, selected_gate, aliases)

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
    parser.add_argument("--repo-type", choices=_REPOSITORY_TYPES)
    parser.add_argument("--gate", help="identity gate command template; {base} is substituted")
    args = parser.parse_args(argv)
    try:
        registry = Registry()
        path = registry.register(
            args.spec, args.path, workflow=args.workflow, repo_type=args.repo_type, gate=args.gate
        )
        alias = Slug(normalize_repo(args.spec).slug)
        entry = registry.entries[alias]
    except RegistryError as exc:
        parser.error(str(exc))
    print(
        f"registered checkout={path} alias={alias} identity={_url_identity(entry.origin)} "
        f"repository_type={entry.repo_type} publication_workflow={entry.workflow} gate={entry.gate}"
    )
    candidates = detect_gate_candidates(path)
    if candidates:
        print("ranked gate candidates:")
        for index, candidate in enumerate(candidates, 1):
            print(f"  {index}. {candidate}")
    if entry.gate == NOOP_GATE:
        print(
            "WARNING: repository registered unproven; no gate resolved. "
            "Use just migrate-repo-gate <repo> --gate <command> after investigation.",
            file=sys.stderr,
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


def main_migrate_type(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Atomically migrate repository type for a repository identity"
    )
    parser.add_argument("spec", help="registered alias, checkout path, or repository identity")
    parser.add_argument("--repo-type", choices=_REPOSITORY_TYPES, required=True)
    args = parser.parse_args(argv)
    try:
        result = Registry.migrate_identity_type(args.spec, args.repo_type)
    except (RegistryError, gitops.GitError, ValueError) as exc:
        parser.error(str(exc))
    aliases = ",".join(str(alias) for alias in result.aliases)
    print(
        f"migrated identity={result.identity} repository_type={result.repo_type} "
        f"publication_workflow={result.workflow} aliases={aliases}"
    )
    return 0


def main_migrate_gate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Atomically migrate verification gate for a repository identity"
    )
    parser.add_argument("spec", help="registered alias, checkout path, or repository identity")
    parser.add_argument("--gate", required=True)
    args = parser.parse_args(argv)
    try:
        result = Registry.migrate_identity_gate(args.spec, args.gate)
    except (RegistryError, gitops.GitError, ValueError) as exc:
        parser.error(
            f"{exc}. Choose a valid command template (optionally using {{base}} as an argv "
            "value), then retry; use 'just repos' to confirm the identity."
        )
    aliases = ",".join(str(alias) for alias in result.aliases)
    print(f"migrated identity={result.identity} gate={result.gate} aliases={aliases}")
    return 0


def main_repos(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List repository identities and checkout aliases")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        registry = Registry()
        registry.migrate_legacy()
        refresh = registry.refresh() if args.refresh else []
    except RegistryError as exc:
        parser.error(str(exc))
    if args.format == "json":
        payload: dict[str, object] = {
            "identities": {
                str(identity): {
                    **asdict(metadata),
                    "default_merge_policy": (
                        "none"
                        if metadata.repo_type == "team"
                        else ("direct" if metadata.workflow == "local" else "auto")
                    ),
                }
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
            policy = (
                "none"
                if metadata.repo_type == "team"
                else ("direct" if metadata.workflow == "local" else "auto")
            )
            print(
                f"identity\t{identity}\t{metadata.repo_type}\t{metadata.workflow}\t"
                f"{policy}\t{metadata.origin}"
            )
        for slug, entry in sorted(registry.entries.items()):
            print(f"checkout\t{slug}\t{entry.path}\t{_url_identity(entry.origin)}")
        for result in refresh:
            print(f"{result.slug}: {'updated' if result.refreshed else 'skipped'}: {result.reason}")
    return 0
