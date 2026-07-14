"""Persistent canonical repo checkout registry.

This module is intentionally not wired into the lifecycle yet; the next change
will consume it when constructing worktrees and choosing merge behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

from . import gitops
from .workspace import RepoRef, normalize_repo

Workflow = Literal["local", "remote"]


class RegistryError(ValueError):
    """Registry data or a requested checkout failed validation."""


@dataclass(frozen=True)
class RegistryEntry:
    path: str
    origin: str
    workflow: Workflow


@dataclass(frozen=True)
class RefreshResult:
    slug: str
    path: str
    refreshed: bool
    reason: str


def _default_search_roots() -> tuple[Path, ...]:
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


def _url_identity(url: str) -> str:
    """Normalize common equivalent clone URL spellings for comparisons."""
    value = url.strip().rstrip("/")
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.removeprefix("git@github.com:")
    if value.endswith(".git"):
        value = value[:-4]
    if value.startswith("file://"):
        value = value.removeprefix("file://")
    if value.startswith(("/", "./", "../", "~")):
        value = str(Path(value).expanduser().resolve())
    return value


class Registry:
    """A JSON-backed map from normalized repo slug to canonical checkout."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path) if path is not None else Path.home() / ".ai-orchestrator" / "repos.json"
        )
        self.entries = self._load()

    def _load(self) -> dict[str, RegistryEntry]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"could not load registry {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RegistryError(f"registry {self.path} must contain a JSON object")
        result: dict[str, RegistryEntry] = {}
        for slug, value in raw.items():
            if not isinstance(slug, str) or not slug or not isinstance(value, dict):
                raise RegistryError(f"registry {self.path} has an invalid entry for {slug!r}")
            if set(value) != {"path", "origin", "workflow"}:
                raise RegistryError(
                    f"registry entry {slug!r} must contain path, origin, and workflow"
                )
            path, origin, workflow = value["path"], value["origin"], value["workflow"]
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise RegistryError(f"registry entry {slug!r} path must be an absolute string")
            if not isinstance(origin, str) or not origin:
                raise RegistryError(f"registry entry {slug!r} origin must be a non-empty string")
            if workflow not in ("local", "remote"):
                raise RegistryError(f"registry entry {slug!r} workflow must be 'local' or 'remote'")
            result[slug] = RegistryEntry(path, origin, cast(Workflow, workflow))
        return result

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {slug: asdict(entry) for slug, entry in sorted(self.entries.items())}
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

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

    def _store(
        self, repo: RepoRef, path: Path, workflow: Workflow, *, origin: str | None = None
    ) -> Path:
        resolved = path.expanduser().resolve()
        actual_origin = origin if origin is not None else gitops.remote_url(resolved)
        self.entries[repo.slug] = RegistryEntry(str(resolved), actual_origin, workflow)
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
        if repo.local:
            path = Path(repo.url)
            if not path.is_dir() or not gitops.is_repo(path):
                raise RegistryError(f"local repo {path} is not a git checkout")
            return self._store(repo, path, "local")
        existing = self.entries.get(repo.slug)
        if existing is not None:
            try:
                if self._valid_checkout(Path(existing.path), existing.origin) and _url_identity(
                    existing.origin
                ) == _url_identity(repo.url):
                    return Path(existing.path)
            except gitops.GitError:
                pass
        found = self._find(
            repo, search_roots if search_roots is not None else _default_search_roots()
        )
        if found is not None:
            return self._store(repo, found, default_workflow)
        destination = (
            Path(clone_into).expanduser()
            if clone_into is not None
            else Path.home() / ".ai-orchestrator" / "repos" / repo.dir_key
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        gitops.clone(repo.url, destination)
        return self._store(repo, destination, default_workflow)

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
        return self._store(repo, chosen, workflow or ("local" if repo.local else "remote"))

    def refresh(self, slug: str | None = None) -> list[RefreshResult]:
        if slug is not None and slug not in self.entries:
            raise RegistryError(f"repo {slug!r} is not registered")
        results: list[RefreshResult] = []
        for key in sorted([slug] if slug is not None else self.entries):
            entry = self.entries[key]
            path = Path(entry.path)
            if not path.exists() or not gitops.is_repo(path):
                results.append(
                    RefreshResult(key, entry.path, False, "checkout is missing or not a git repo")
                )
                continue
            if gitops.is_dirty(path):
                results.append(RefreshResult(key, entry.path, False, "checkout is dirty"))
                continue
            try:
                gitops.fetch(path)
                branch = gitops.default_branch(path)
                if gitops.current_branch(path) != branch:
                    results.append(
                        RefreshResult(
                            key, entry.path, False, f"default branch {branch} is not checked out"
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
    parser = argparse.ArgumentParser(description="Register a canonical repo checkout")
    parser.add_argument("spec")
    parser.add_argument("path", nargs="?")
    parser.add_argument("--workflow", choices=("local", "remote"))
    args = parser.parse_args(argv)
    try:
        path = Registry().register(args.spec, args.path, workflow=args.workflow)
    except RegistryError as exc:
        parser.error(str(exc))
    print(path)
    return 0


def main_repos(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List canonical repo checkouts")
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
            "repos": {slug: asdict(entry) for slug, entry in sorted(registry.entries.items())}
        }
        if args.refresh:
            payload["refresh"] = [asdict(result) for result in refresh]
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        for slug, entry in sorted(registry.entries.items()):
            print(f"{slug}\t{entry.path}\t{entry.workflow}\t{entry.origin}")
        for result in refresh:
            print(f"{result.slug}: {'updated' if result.refreshed else 'skipped'}: {result.reason}")
    return 0
