from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import git

from orchestrator import gitops
from orchestrator.github import GitHubError
from orchestrator.registry import (
    _REGISTRY_VERSION,
    Registry,
    RegistryEntry,
    RegistryError,
    Slug,
    _authenticated_login,
    _coalesce,
    _default_search_roots,
    _parse_entry,
    _print_merge_gate_coverage,
    _serialize,
    _url_identity,
    _validate_alias,
    _validate_gate,
    infer_repository_type,
    main_migrate_gate,
    main_migrate_type,
    main_migrate_workflow,
    main_register,
    main_repos,
    merge_gate_coverage,
    validate_identity_key,
)
from orchestrator.verify import NOOP_GATE
from orchestrator.workspace import normalize_repo


def _clone(origin: Path, path: Path) -> Path:
    git("clone", str(origin), str(path))
    return path


def _commit_and_push(origin: Path, path: Path, content: str) -> None:
    _clone(origin, path)
    (path / "change.txt").write_text(content, encoding="utf-8")
    git("add", "change.txt", cwd=path)
    git("commit", "-m", content, cwd=path)
    git("push", "origin", "main", cwd=path)


def test_resolve_returns_valid_registered_checkout(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    checkout = _clone(origin, tmp_path / "checkout")
    registry = Registry(tmp_path / "registry.json")
    registry.register(str(checkout), repo_type="single-owner")

    assert registry.resolve(str(checkout)) == checkout.resolve()
    assert registry.entries[f"local/{checkout.name}"].workflow == "remote"


def test_repo_ref_prefers_registered_alias_and_rejects_unknown_local_namespace(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    registry = Registry(tmp_path / "registry.json")
    registry.register(str(checkout), repo_type="single-owner")

    ref = registry.repo_ref("local/checkout")
    assert ref.local and ref.url == str(checkout.resolve())
    assert ref.slug == "local/checkout"
    assert registry.checkout_path("local/checkout") == checkout.resolve()

    with pytest.raises(
        RegistryError, match=r"unknown local checkout alias 'local/missing'.*just repos"
    ):
        registry.repo_ref("local/missing")
    with pytest.raises(
        RegistryError, match=r"unknown local checkout alias 'local/missing'.*just repos"
    ):
        registry.checkout_path("local/missing")

    github = registry.repo_ref("acme/widget")
    assert not github.local
    assert github.url == "https://github.com/acme/widget.git"

    explicit_local_owner = registry.repo_ref("https://github.com/local/example.git")
    assert not explicit_local_owner.local
    assert explicit_local_owner.slug == "local/example"
    assert explicit_local_owner.url == "https://github.com/local/example.git"

    remote_checkout = _clone(bare_origin(), tmp_path / "remote-checkout")
    remote_url = "https://github.com/nickderobertis/crozier.git"
    git("remote", "set-url", "origin", remote_url, cwd=remote_checkout)
    registry.register(
        "nickderobertis/crozier",
        remote_checkout,
        workflow="remote",
        repo_type="single-owner",
    )
    remote_alias = registry.repo_ref("nickderobertis/crozier")
    assert remote_alias.local and remote_alias.url == str(remote_checkout.resolve())
    selected = registry.select("nickderobertis/crozier")
    assert selected.publication_checkout == remote_checkout.resolve()
    assert selected.workflow == "remote" and selected.repo_type == "single-owner"


def test_resolve_finds_checkout_by_origin_and_registers(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    checkout = _clone(origin, tmp_path / "search" / "checkout")
    registry = Registry(tmp_path / "registry.json")

    remote_url = "https://github.com/acme/widget.git"
    git("remote", "set-url", "origin", remote_url, cwd=checkout)
    found = registry.resolve(
        "acme/widget", search_roots=[checkout.parent], repo_type="single-owner"
    )
    assert found == checkout.resolve()
    assert registry.entries["acme/widget"].origin == remote_url


def test_resolve_clones_when_no_checkout_exists(
    tmp_path: Path, bare_origin: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = bare_origin()
    url = "https://github.com/acme/cloned.git"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", url)
    destination = tmp_path / "canonical"

    resolved = Registry(tmp_path / "registry.json").resolve(
        "acme/cloned", search_roots=[], clone_into=destination, repo_type="single-owner"
    )

    assert resolved == destination
    assert gitops.is_repo(destination)


def test_register_clones_when_no_checkout_exists(
    tmp_path: Path, bare_origin: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = bare_origin()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_git = shutil.which("git")
    assert real_git is not None
    git_wrapper = bin_dir / "git"
    git_wrapper.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = clone ]; then exec "{real_git}" -c '
        f'url."{origin}".insteadOf=https://github.com/acme/cloned.git "$@"; fi\n'
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    git_wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AI_ORCHESTRATOR_SEARCH_ROOTS", str(tmp_path / "empty"))

    destination = Registry().register("acme/cloned", repo_type="single-owner")

    assert destination == tmp_path / "state" / "repos" / "acme__cloned"
    assert gitops.is_repo(destination)


def test_register_validates_repository_spec(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="empty repo spec"):
        Registry(tmp_path / "registry.json").register(" ")


@pytest.mark.parametrize(
    "payload, expected",
    [
        (
            "not json",
            "could not load registry {path}: Expecting value: line 1 column 1 (char 0)",
        ),
        (
            json.dumps({"x/y": {"path": 3, "origin": "url", "workflow": "remote"}}),
            "registry entry 'x/y' path must be an absolute string",
        ),
        (
            json.dumps({"x/y": {"path": "/tmp/x", "origin": "url", "workflow": "other"}}),
            "registry entry 'x/y' workflow must be 'local' or 'remote'",
        ),
        (
            json.dumps({"x/y": {"path": "/tmp/x", "origin": "bad\norigin", "workflow": "remote"}}),
            "registry entry 'x/y' origin must be a non-empty string",
        ),
    ],
)
def test_malformed_registry_is_rejected(tmp_path: Path, payload: str, expected: str) -> None:
    path = tmp_path / "registry.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(RegistryError) as caught:
        Registry(path)
    assert str(caught.value) == expected.format(path=path)


@pytest.mark.parametrize(
    "payload, match",
    [
        (
            {"version": 5, "identities": {}, "checkouts": {}},
            "versioned format must contain",
        ),
        (
            {"version": 2, "identities": [], "checkouts": {}},
            "identities and checkouts must be JSON objects",
        ),
        (
            {
                "version": 2,
                "identities": {"": {"origin": "/repo", "workflow": "remote"}},
                "checkouts": {},
            },
            "identity key must be a non-empty string",
        ),
        (
            {"version": 2, "identities": {"/repo": {}}, "checkouts": {}},
            "must contain origin and workflow",
        ),
        (
            {
                "version": 2,
                "identities": {"/repo": {"origin": "", "workflow": "remote"}},
                "checkouts": {},
            },
            "origin must be non-empty",
        ),
        (
            {
                "version": 2,
                "identities": {"/repo": {"origin": "/repo", "workflow": "invalid"}},
                "checkouts": {},
            },
            "workflow must be 'local' or 'remote'",
        ),
        (
            {
                "version": 2,
                "identities": {"/wrong": {"origin": "/repo", "workflow": "remote"}},
                "checkouts": {},
            },
            "does not match normalized origin",
        ),
        (
            {
                "version": 2,
                "identities": {"/repo": {"origin": "/repo", "workflow": "remote"}},
                "checkouts": {"x/y": {}},
            },
            "must contain path and identity",
        ),
        (
            {
                "version": 2,
                "identities": {"/repo": {"origin": "/repo", "workflow": "remote"}},
                "checkouts": {"x/y": {"path": "relative", "identity": "/repo"}},
            },
            "path must be an absolute string",
        ),
        (
            {
                "version": 2,
                "identities": {"/repo": {"origin": "/repo", "workflow": "remote"}},
                "checkouts": {"x/y": {"path": "/tmp/repo", "identity": "/missing"}},
            },
            "references unknown identity",
        ),
    ],
)
def test_version_two_registry_validation(
    tmp_path: Path, payload: dict[str, object], match: str
) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RegistryError, match=match):
        Registry(path)


def test_v4_gate_round_trips_and_rejects_malformed_entries(tmp_path: Path) -> None:
    identity = "/repo"
    valid = {
        "version": 4,
        "identities": {
            identity: {
                "origin": identity,
                "workflow": "remote",
                "repo_type": "single-owner",
                "gate": "just check {base}",
            }
        },
        "checkouts": {"x/y": {"path": "/tmp/repo", "identity": identity}},
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert Registry(path).identities[identity].gate == "just check {base}"

    valid["identities"][identity]["gate"] = "bad {unknown}"
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(RegistryError, match="only the .base. placeholder"):
        Registry(path)


def test_v3_migration_detects_gate_from_registered_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "justfile").write_text("check:\n\ttrue\n", encoding="utf-8")
    identity = "/repo"
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "version": 3,
                "identities": {
                    identity: {
                        "origin": identity,
                        "workflow": "remote",
                        "repo_type": "single-owner",
                    }
                },
                "checkouts": {"x/y": {"path": str(checkout.resolve()), "identity": identity}},
            }
        ),
        encoding="utf-8",
    )

    Registry(path).migrate_legacy()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 4
    assert payload["identities"][identity]["gate"] == "just check"


def test_origin_identity_normalizes_clone_spellings_and_default_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _url_identity("git@github.com:Owner/Repo.git") == "https://github.com/owner/repo"
    assert _url_identity("ssh://git@github.com/Owner/Repo.git") == "https://github.com/owner/repo"
    assert _url_identity(f"file://{tmp_path}/origin.git") == str(tmp_path / "origin")
    monkeypatch.delenv("AI_ORCHESTRATOR_SEARCH_ROOTS")
    monkeypatch.setenv("HOME", str(tmp_path))
    roots = _default_search_roots()
    assert tmp_path in roots and tmp_path / "repos" in roots


def test_repository_type_inference_uses_authenticated_owner_case_insensitively() -> None:
    origin = "git@github.com:Alice/Widget.git"
    assert infer_repository_type(origin, login=lambda: "aLiCe\n") == "single-owner"
    assert infer_repository_type(origin, login=lambda: "bob") == "team"


def test_authenticated_inference_queries_current_gh_login(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def run(argv, **kwargs):
        seen.extend(argv)
        assert kwargs == {"text": True, "capture_output": True}
        return SimpleNamespace(returncode=0, stdout="alice\n", stderr="")

    monkeypatch.setattr("orchestrator.registry.subprocess.run", run)
    assert _authenticated_login() == "alice"
    assert seen == ["gh", "api", "user", "--jq", ".login"]


def test_authenticated_login_failure_and_registry_error_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orchestrator.registry.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="login first"),
    )
    with pytest.raises(RegistryError, match="login first"):
        _authenticated_login()
    expected = RegistryError("explicit failure")
    with pytest.raises(RegistryError) as caught:
        infer_repository_type(
            "https://github.com/alice/widget.git",
            login=lambda: (_ for _ in ()).throw(expected),
        )
    assert caught.value is expected


def test_identity_coalescing_rejects_type_conflicts_and_team_local() -> None:
    origin = "https://github.com/acme/widget.git"
    with pytest.raises(RegistryError, match="conflicting repo types"):
        _coalesce(
            {
                Slug("acme/one"): RegistryEntry("/one", origin, "remote", "single-owner"),
                Slug("acme/two"): RegistryEntry("/two", origin, "remote", "team"),
            }
        )
    with pytest.raises(RegistryError, match="cannot combine.*team.*local"):
        _coalesce({Slug("acme/widget"): RegistryEntry("/one", origin, "local", "team")})
    with pytest.raises(RegistryError, match="unclassified"):
        _serialize({Slug("acme/widget"): RegistryEntry("/one", origin, "remote", None)})
    with pytest.raises(RegistryError, match="repo_type must be"):
        _serialize({Slug("acme/widget"): RegistryEntry("/one", origin, "remote", "other")})


@pytest.mark.parametrize(
    "metadata, match",
    [
        ({"origin": "https://github.com/acme/widget.git", "workflow": "remote"}, "repo_type"),
        (
            {
                "origin": "https://github.com/acme/widget.git",
                "workflow": "remote",
                "repo_type": "other",
            },
            "repo_type must be",
        ),
        (
            {
                "origin": "https://github.com/acme/widget.git",
                "workflow": "local",
                "repo_type": "team",
            },
            "cannot combine",
        ),
    ],
)
def test_schema_v3_rejects_invalid_repository_type_metadata(
    tmp_path: Path, metadata: dict[str, str], match: str
) -> None:
    identity = "https://github.com/acme/widget"
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "version": 3,
                "identities": {identity: metadata},
                "checkouts": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match=match):
        Registry(path)


@pytest.mark.parametrize(
    "origin, login, match",
    [
        ("/not/github", lambda: "alice", "not GitHub"),
        ("https://github.com/alice/widget.git", lambda: "", "login is empty"),
        (
            "https://github.com/alice/widget.git",
            lambda: (_ for _ in ()).throw(OSError("gh unavailable")),
            "authenticated GitHub user",
        ),
    ],
)
def test_repository_type_inference_fails_closed(
    origin: str, login: Callable[[], str], match: str
) -> None:
    with pytest.raises(RegistryError, match=match):
        infer_repository_type(origin, login=login)


@pytest.mark.parametrize("legacy_version", [None, 2])
def test_flat_and_v2_registries_upgrade_atomically_to_v4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_version: int | None
) -> None:
    path = tmp_path / "registry.json"
    origin = "https://github.com/acme/widget.git"
    entry = {"path": str((tmp_path / "checkout").resolve()), "origin": origin, "workflow": "remote"}
    if legacy_version is None:
        payload: dict[str, object] = {"acme/widget": entry}
    else:
        identity = "https://github.com/acme/widget"
        payload = {
            "version": 2,
            "identities": {identity: {"origin": origin, "workflow": "remote"}},
            "checkouts": {"acme/widget": {"path": entry["path"], "identity": identity}},
        }
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("orchestrator.registry.infer_repository_type", lambda _origin: "team")

    Registry(path).migrate_legacy()

    upgraded = json.loads(path.read_text(encoding="utf-8"))
    assert upgraded["version"] == 4
    assert next(iter(upgraded["identities"].values()))["gate"] == "<no-op>"
    metadata = next(iter(upgraded["identities"].values()))
    assert metadata["repo_type"] == "team" and metadata["workflow"] == "remote"


def test_workflow_migration_also_classifies_other_legacy_remote_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "registry.json"
    identity = "https://github.com/acme/widget"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "identities": {
                    identity: {
                        "origin": "https://github.com/acme/widget.git",
                        "workflow": "remote",
                    }
                },
                "checkouts": {
                    "acme/widget": {
                        "path": str((tmp_path / "checkout").resolve()),
                        "identity": identity,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "orchestrator.registry.infer_repository_type", lambda _origin: "single-owner"
    )

    Registry.migrate_identity_workflow("acme/widget", "remote", path=path)

    metadata = json.loads(path.read_text(encoding="utf-8"))["identities"][identity]
    assert metadata["repo_type"] == "single-owner"


def test_legacy_migration_failure_keeps_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "registry.json"
    payload = {
        "acme/widget": {
            "path": str((tmp_path / "checkout").resolve()),
            "origin": "https://github.com/acme/widget.git",
            "workflow": "remote",
        }
    }
    original = json.dumps(payload)
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        "orchestrator.registry.infer_repository_type",
        lambda _origin: (_ for _ in ()).throw(RegistryError("no login")),
    )

    with pytest.raises(RegistryError, match="no login"):
        Registry(path).migrate_legacy()
    assert path.read_text(encoding="utf-8") == original

    monkeypatch.setattr("orchestrator.registry.infer_repository_type", lambda _origin: "team")
    Registry(path).migrate_legacy()
    recovered = json.loads(path.read_text(encoding="utf-8"))
    assert recovered["version"] == 4
    assert next(iter(recovered["identities"].values()))["repo_type"] == "team"


def test_workflow_round_trips(tmp_path: Path, bare_origin: Callable[..., Path]) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    path = tmp_path / "registry.json"
    registry = Registry(path)
    registry.register(str(checkout), workflow="remote", repo_type="single-owner")

    assert Registry(path).entries[f"local/{checkout.name}"].workflow == "remote"


def test_registration_infers_and_persists_type_from_normalized_github_origin(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    git("remote", "set-url", "origin", "https://github.com/acme/widget.git", cwd=checkout)
    monkeypatch.setattr("orchestrator.registry.infer_repository_type", lambda _origin: "team")
    path = tmp_path / "registry.json"

    Registry(path).register(str(checkout))

    payload = json.loads(path.read_text(encoding="utf-8"))
    metadata = next(iter(payload["identities"].values()))
    assert payload["version"] == 4
    assert metadata["repo_type"] == "team" and metadata["workflow"] == "remote"


def test_repo_type_registration_conflict_requires_type_migration(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = _clone(origin, tmp_path / "canonical")
    safety = _clone(origin, tmp_path / "safety")
    registry = Registry(tmp_path / "registry.json")
    registry.register(str(canonical), repo_type="single-owner")
    with pytest.raises(RegistryError, match="migrate-repo-type"):
        registry.register(str(safety), repo_type="team")
    with pytest.raises(RegistryError, match="not registered"):
        registry.repository_type("https://github.com/missing/repo")


def test_migrate_legacy_missing_registry_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    registry = Registry(path)
    assert registry.migrate_legacy() is None
    assert not path.exists() and registry.entries == {}


def test_repository_type_override_is_run_only_and_team_migration_normalizes_workflow(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    path = tmp_path / "registry.json"
    registry = Registry(path)
    registry.register(str(checkout), workflow="remote", repo_type="single-owner")
    identity = next(iter(registry.identities))

    assert registry.repository_type(identity, override="team") == "team"
    assert Registry(path).identities[identity].repo_type == "single-owner"

    migrated = Registry.migrate_identity_type(str(checkout), "team", path=path)
    assert migrated.repo_type == "team" and migrated.workflow == "remote"
    stored = Registry(path).identities[identity]
    assert stored.repo_type == "team" and stored.workflow == "remote"

    with pytest.raises(RegistryError, match="cannot migrate workflow to local"):
        Registry.migrate_identity_workflow(str(checkout), "local", path=path)


def test_team_registration_rejects_local_workflow(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    with pytest.raises(RegistryError, match="team.*workflow=local"):
        Registry(tmp_path / "registry.json").register(
            str(checkout), workflow="local", repo_type="team"
        )


def test_registration_inherits_identity_workflow_and_rejects_conflict_atomically(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = _clone(origin, tmp_path / "canonical")
    safety = _clone(origin, tmp_path / "safety")
    path = tmp_path / "registry.json"
    registry = Registry(path)
    registry.register(str(canonical), workflow="local")
    before = path.read_text(encoding="utf-8")

    with pytest.raises(RegistryError, match="conflicts with registered identity.*migrate"):
        registry.register(str(safety), workflow="remote")
    assert path.read_text(encoding="utf-8") == before

    registry.register(str(safety))
    reloaded = Registry(path)
    assert reloaded.entries["local/canonical"].workflow == "local"
    assert reloaded.entries["local/safety"].workflow == "local"
    assert len(reloaded.identities) == 1


def test_explicit_workflow_migration_updates_every_alias_in_one_write(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = _clone(origin, tmp_path / "canonical")
    safety = _clone(origin, tmp_path / "safety")
    path = tmp_path / "registry.json"
    registry = Registry(path)
    registry.register(str(canonical), workflow="remote", repo_type="single-owner")
    registry.register(str(safety))

    result = Registry.migrate_identity_workflow("local/canonical", "local", path=path)

    assert result.workflow == "local"
    assert result.aliases == ("local/canonical", "local/safety")
    migrated = Registry(path)
    assert {entry.workflow for entry in migrated.entries.values()} == {"local"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 4
    assert list(payload["identities"].values())[0]["workflow"] == "local"


def test_interrupted_workflow_migration_preserves_every_alias(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = bare_origin()
    canonical = _clone(origin, tmp_path / "canonical")
    safety = _clone(origin, tmp_path / "safety")
    path = tmp_path / "registry.json"
    registry = Registry(path)
    registry.register(str(canonical), workflow="remote", repo_type="single-owner")
    registry.register(str(safety))
    before = path.read_text(encoding="utf-8")

    def interrupted(_source: str, _destination: Path) -> None:
        raise OSError("simulated migration interruption")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError, match="simulated migration interruption"):
        Registry.migrate_identity_workflow("local/canonical", "local", path=path)

    assert path.read_text(encoding="utf-8") == before
    assert {entry.workflow for entry in Registry(path).entries.values()} == {"remote"}
    assert not list(tmp_path.glob(".registry.json.*"))


def test_legacy_agreeing_aliases_normalize_and_keep_all_checkout_paths(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    canonical = _clone(origin, tmp_path / "canonical")
    safety = _clone(origin, tmp_path / "safety")
    auxiliary = _clone(origin, tmp_path / "auxiliary")
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "local/canonical": {
                    "path": str(canonical.resolve()),
                    "origin": str(origin),
                    "workflow": "local",
                },
                "local/safety": {
                    "path": str(safety.resolve()),
                    "origin": str(origin),
                    "workflow": "local",
                },
            }
        ),
        encoding="utf-8",
    )

    registry = Registry(path)
    assert len(registry.identities) == 1
    assert {Path(entry.path) for entry in registry.entries.values()} == {canonical, safety}
    auxiliary_match = registry.entry_for_checkout(auxiliary)
    assert auxiliary_match is not None and auxiliary_match[1].workflow == "local"
    registry.save()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload["checkouts"]) == {"local/canonical", "local/safety"}


def test_legacy_conflict_names_exact_entries_and_migration_command(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "local/canonical": {
                    "path": str((tmp_path / "canonical").resolve()),
                    "origin": str(origin),
                    "workflow": "local",
                },
                "owner/repo": {
                    "path": str((tmp_path / "safety").resolve()),
                    "origin": str(origin),
                    "workflow": "remote",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError) as caught:
        Registry(path)
    error = str(caught.value)
    assert "local/canonical: workflow=local" in error
    assert "owner/repo: workflow=remote" in error
    assert "just migrate-repo-workflow local/canonical --workflow <local|remote>" in error

    Registry.migrate_identity_workflow("owner/repo", "local", path=path)
    assert {entry.workflow for entry in Registry(path).entries.values()} == {"local"}


def test_checkout_lookup_resolves_shared_identity_despite_multiple_aliases(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    first = gitops.clone(origin, tmp_path / "first")
    auxiliary = gitops.clone(origin, tmp_path / "auxiliary")
    registry = Registry(tmp_path / "registry.json")
    registry.register(str(first), workflow="local")
    exact = registry.entry_for_checkout(first)
    assert exact is not None and Path(exact[1].path) == first
    matched = registry.entry_for_checkout(auxiliary)
    assert matched is not None and Path(matched[1].path) == first

    second_slug = "other/checkout"
    registry.entries[Slug(second_slug)] = RegistryEntry(
        str((tmp_path / "other").resolve()), str(origin), "local"
    )
    shared = registry.entry_for_checkout(auxiliary)
    assert shared is not None and shared[1].workflow == "local"
    assert registry.entry_for_checkout(tmp_path / "not-a-repo") is None
    assert registry.identity_for_checkout(tmp_path / "not-a-repo") is None


def test_selection_rejects_missing_and_different_execution_checkouts(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    canonical = _clone(bare_origin(), tmp_path / "canonical")
    other = _clone(bare_origin(), tmp_path / "other")
    registry = Registry(tmp_path / "registry.json")
    registry.register(str(canonical), workflow="local")

    with pytest.raises(RegistryError, match="execution checkout .* is not a git checkout"):
        registry.select(str(canonical), execution_checkout=tmp_path / "missing")
    with pytest.raises(RegistryError, match="not publication identity"):
        registry.select(str(canonical), execution_checkout=other)


def test_alias_cannot_be_reassigned_to_another_identity(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    first = _clone(bare_origin(), tmp_path / "one" / "checkout")
    second = _clone(bare_origin(), tmp_path / "two" / "checkout")
    registry = Registry(tmp_path / "registry.json")
    registry.register(str(first), repo_type="single-owner")

    with pytest.raises(RegistryError, match="already belongs to identity"):
        registry.register(str(second), repo_type="single-owner")


def test_refresh_fast_forwards(tmp_path: Path, bare_origin: Callable[..., Path]) -> None:
    origin = bare_origin()
    checkout = _clone(origin, tmp_path / "checkout")
    registry = Registry(tmp_path / "registry.json")
    registry.register(str(checkout), repo_type="single-owner")
    _commit_and_push(origin, tmp_path / "writer", "upstream")

    result = registry.refresh()

    assert result[0].refreshed
    assert (checkout / "change.txt").read_text(encoding="utf-8") == "upstream"


def test_refresh_refuses_dirty_and_non_ff_checkouts(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    dirty_origin = bare_origin()
    dirty = _clone(dirty_origin, tmp_path / "dirty")
    dirty_registry = Registry(tmp_path / "dirty.json")
    dirty_registry.register(str(dirty), repo_type="single-owner")
    (dirty / "untracked").write_text("dirty", encoding="utf-8")
    dirty_head = gitops.head_sha(dirty)
    dirty_tracking = gitops.ref_sha(dirty, "origin/main")
    _commit_and_push(dirty_origin, tmp_path / "dirty-writer", "dirty-upstream")
    assert gitops.ref_sha(dirty_origin, "main") != dirty_tracking

    dirty_result = dirty_registry.refresh()[0]

    assert not dirty_result.refreshed
    assert dirty_result.reason == "checkout is dirty"
    assert gitops.head_sha(dirty) == dirty_head
    assert gitops.ref_sha(dirty, "origin/main") == dirty_tracking
    assert (dirty / "untracked").read_text(encoding="utf-8") == "dirty"

    origin = bare_origin()
    checkout = _clone(origin, tmp_path / "diverged")
    registry = Registry(tmp_path / "diverged.json")
    registry.register(str(checkout), repo_type="single-owner")
    (checkout / "local.txt").write_text("local", encoding="utf-8")
    git("add", "local.txt", cwd=checkout)
    git("commit", "-m", "local", cwd=checkout)
    local_head = gitops.head_sha(checkout)
    local_branch = gitops.current_branch(checkout)
    tracking_before = gitops.ref_sha(checkout, "origin/main")
    _commit_and_push(origin, tmp_path / "other", "remote")

    result = registry.refresh()[0]

    assert not result.refreshed
    assert result.reason == "checkout has diverged from origin/main; fast-forward refused"
    assert gitops.ref_sha(checkout, "origin/main") != tracking_before
    assert gitops.ref_sha(checkout, "origin/main") == gitops.ref_sha(origin, "main")
    assert gitops.current_branch(checkout) == local_branch
    assert gitops.head_sha(checkout) == local_head
    assert not gitops.is_dirty(checkout)
    assert (checkout / "local.txt").read_text(encoding="utf-8") == "local"


def test_registry_rejects_invalid_shapes_and_missing_slug(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(RegistryError, match="JSON object"):
        Registry(path)

    path.write_text(json.dumps({"x/y": {"path": "/tmp"}}), encoding="utf-8")
    with pytest.raises(RegistryError, match="must contain"):
        Registry(path)

    registry = Registry(tmp_path / "missing.json")
    with pytest.raises(RegistryError, match="not registered"):
        registry.refresh("x/y")


def test_refresh_reports_missing_checkout(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.json")
    registry.entries["x/y"] = RegistryEntry("/definitely/missing", "url", "remote")

    result = registry.refresh()[0]

    assert not result.refreshed
    assert "missing" in result.reason


def test_refresh_reports_changed_origin_without_fetching(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    registry = Registry(tmp_path / "registry.json")
    registry.register(str(checkout), repo_type="single-owner")
    git("remote", "set-url", "origin", str(tmp_path / "different.git"), cwd=checkout)

    result = registry.refresh()[0]

    assert not result.refreshed
    assert result.reason == "origin does not match registered origin"


def test_refresh_reports_non_default_checkout_and_migration_errors(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    registry_path = tmp_path / "registry.json"
    registry = Registry(registry_path)
    registry.register(str(checkout), workflow="remote", repo_type="single-owner")
    git("switch", "-c", "feature", cwd=checkout)

    result = registry.refresh()[0]
    assert not result.refreshed and "default branch main is not checked out" in result.reason

    with pytest.raises(RegistryError, match="does not exist"):
        Registry.migrate_identity_workflow("x/y", "local", path=tmp_path / "missing.json")
    with pytest.raises(RegistryError, match="is not registered"):
        Registry.migrate_identity_workflow("unknown/repo", "local", path=registry_path)


def test_migration_accepts_checkout_path_and_cli_reports_errors(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    home = tmp_path / "home"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home))
    Registry().register(str(checkout), workflow="remote", repo_type="single-owner")

    migrated = Registry.migrate_identity_workflow(str(checkout), "local")
    assert migrated.workflow == "local"
    gate = Registry.migrate_identity_gate(str(checkout), "just check {base}")
    assert gate.gate == "just check {base}"
    assert Registry().entry_for_checkout(checkout)[1].gate == "just check {base}"
    with pytest.raises(SystemExit):
        main_migrate_workflow(["missing/repo", "--workflow", "local"])
    with pytest.raises(SystemExit):
        main_migrate_type(["missing/repo", "--repo-type", "team"])
    with pytest.raises(SystemExit):
        main_migrate_gate(["missing/repo", "--gate", "true"])


def test_gate_migration_validates_registry_and_command(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="non-empty"):
        Registry.migrate_identity_gate("x/y", "", path=tmp_path / "missing.json")
    with pytest.raises(RegistryError, match="valid command template"):
        Registry.migrate_identity_gate("x/y", "sh -c '", path=tmp_path / "missing.json")
    with pytest.raises(RegistryError, match="not shell source"):
        Registry.migrate_identity_gate(
            "x/y", "env bash -ec '{base}'", path=tmp_path / "missing.json"
        )
    with pytest.raises(RegistryError, match="does not exist"):
        Registry.migrate_identity_gate("x/y", "true", path=tmp_path / "missing.json")
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 4, "identities": {}, "checkouts": {}}))
    with pytest.raises(RegistryError, match="not registered"):
        Registry.migrate_identity_gate("x/y", "true", path=path)


def test_gate_template_rejects_command_source_placeholder() -> None:
    with pytest.raises(RegistryError, match="argv value, not command source"):
        _validate_gate("just gate prefix;{base}")


def test_gate_template_allows_placeholder_after_non_command_shell_argument() -> None:
    assert _validate_gate("bash script.sh {base}") == "bash script.sh {base}"


def test_registry_identity_alias_and_entry_validation_boundaries() -> None:
    identity = "https://github.com/acme/widget"
    assert validate_identity_key(identity) == identity
    with pytest.raises(RegistryError, match="not a normalized origin"):
        validate_identity_key("https://github.com/acme/widget.git")

    assert _validate_alias("acme/widget") == "acme/widget"
    with pytest.raises(RegistryError, match="normalized owner/name slug"):
        _validate_alias("acme")
    with pytest.raises(RegistryError, match="invalid entry"):
        _parse_entry(Slug("acme/widget"), [])


def test_coalesce_rejects_conflicting_identity_gates() -> None:
    origin = "https://github.com/acme/widget.git"
    entries = {
        Slug("acme/one"): RegistryEntry("/one", origin, "remote", "single-owner", "just check"),
        Slug("acme/two"): RegistryEntry("/two", origin, "remote", "single-owner", "just gate"),
    }
    with pytest.raises(RegistryError, match="conflicting gates"):
        _coalesce(entries)


def test_type_migration_requires_registry_and_known_identity(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="does not exist"):
        Registry.migrate_identity_type("acme/widget", "team", path=tmp_path / "missing.json")
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 3, "identities": {}, "checkouts": {}}))
    with pytest.raises(RegistryError, match="not registered"):
        Registry.migrate_identity_type("acme/widget", "team", path=path)


def test_repo_cli_text_json_and_register_errors(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    origin = bare_origin()
    checkout = _clone(origin, tmp_path / "checkout")
    assert (
        main_register([str(checkout), "--workflow", "remote", "--repo-type", "single-owner"]) == 0
    )
    capsys.readouterr()

    assert main_repos([]) == 0
    output = capsys.readouterr().out
    assert f"identity\t{str(origin).removesuffix('.git')}\tsingle-owner\tremote" in output
    assert f"checkout\tlocal/{checkout.name}\t{checkout.resolve()}" in output
    assert main_repos(["--format", "json"]) == 0
    assert '"identities"' in capsys.readouterr().out
    assert main_repos(["--refresh", "--format", "json"]) == 0
    assert '"refresh"' in capsys.readouterr().out
    assert main_migrate_type([str(checkout), "--repo-type", "team"]) == 0
    assert "repository_type=team" in capsys.readouterr().out
    assert main_migrate_gate([str(checkout), "--gate", "true"]) == 0
    assert "gate=true" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main_register(["acme/missing", str(tmp_path / "absent")])


def test_env_overrides_base_dir_and_search_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator import registry as reg

    base = tmp_path / "state"
    roots = tmp_path / "roots"
    roots.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(base))
    monkeypatch.setenv("AI_ORCHESTRATOR_SEARCH_ROOTS", str(roots))
    assert Registry().path == base / "repos.json"
    assert reg._default_search_roots() == (roots,)


def test_default_state_is_isolated_from_the_real_home() -> None:
    # Proves the autouse isolation fixture redirects the default registry away from
    # the real ~/.ai-orchestrator, so the suite can never touch a developer's state.
    isolated = Path(os.environ["AI_ORCHESTRATOR_HOME"]) / "repos.json"
    assert Registry().path == isolated
    assert (Path.home() / ".ai-orchestrator") not in Registry().path.parents


@pytest.mark.reads_docs
def test_documented_registry_schema_version_tracks_contract() -> None:
    root = Path(__file__).parents[1]
    assert f"Schema-v{_REGISTRY_VERSION}" in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert f"version {_REGISTRY_VERSION} format" in (root / "docs" / "repo-lifecycle.md").read_text(
        encoding="utf-8"
    )
    assert NOOP_GATE in (root / "docs" / "repo-lifecycle.md").read_text(encoding="utf-8")


def test_a_registration_alias_is_not_a_spec_that_resolves_back_to_its_checkout() -> None:
    """The gotcha `docs/host-setup.md` warns an operator about, pinned.

    A path registration produces the alias `local/<name>`, but feeding that alias
    back in is parsed as a GitHub `owner/name` — a different repository identity
    that registration would go and clone. The document's warning is only true while
    these two resolutions differ, so pin both rather than the prose alone.
    """
    from_path = normalize_repo("~/projects/ai-orchestrator")
    assert from_path.local
    assert from_path.slug == "local/ai-orchestrator"
    assert from_path.url == str(Path("~/projects/ai-orchestrator").expanduser())

    from_alias = normalize_repo("local/ai-orchestrator")
    assert not from_alias.local
    assert from_alias.url == "https://github.com/local/ai-orchestrator.git"
    assert from_alias.url != from_path.url


@pytest.mark.reads_docs
def test_host_setup_warns_against_registering_the_alias_form() -> None:
    document = (Path(__file__).parents[1] / "docs" / "host-setup.md").read_text(encoding="utf-8")

    assert "~/projects/ai-orchestrator" in document
    assert normalize_repo("local/ai-orchestrator").url in document


def test_merge_gate_coverage_reports_hook_checks_and_unknown(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    hook = checkout / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\n", encoding="utf-8")
    hook.chmod(0o755)
    identity = validate_identity_key("https://github.com/acme/widget")

    github = SimpleNamespace(
        default_branch=lambda repo: "master",
        required_status_checks=lambda repo, branch: ("gate",),
    )
    covered = merge_gate_coverage(identity, checkout, github=github)
    assert covered.meets_coverage_criteria
    assert covered.default_branch == "master"
    assert covered.required_checks == ("gate",)
    _print_merge_gate_coverage(covered)
    output = capsys.readouterr()
    assert "executable pre-push hook" in output.out
    assert "required PR status checks on master (gate)" in output.out
    assert output.err == ""

    hook.unlink()

    def unavailable(repo: str) -> str:
        raise GitHubError("forbidden")

    unknown = merge_gate_coverage(
        identity,
        checkout,
        github=SimpleNamespace(
            default_branch=unavailable,
            required_status_checks=lambda repo, branch: (),
        ),
    )
    assert not unknown.meets_coverage_criteria
    assert unknown.github_status == "unknown"
    _print_merge_gate_coverage(unknown)
    output = capsys.readouterr()
    assert "required_pr_status_checks=unknown (forbidden)" in output.out
    assert "required PR status checks are unknown" in output.err

    local = merge_gate_coverage(
        validate_identity_key(str(bare_origin()).removesuffix(".git")),
        checkout,
    )
    assert local.github_status == "not-applicable"
    _print_merge_gate_coverage(local)
    assert "no GitHub origin" in capsys.readouterr().out
