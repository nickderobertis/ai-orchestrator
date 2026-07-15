from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import git

from orchestrator import gitops
from orchestrator.registry import (
    Registry,
    RegistryEntry,
    RegistryError,
    Slug,
    main_register,
    main_repos,
)


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
    registry.register(str(checkout))

    assert registry.resolve(str(checkout)) == checkout.resolve()


def test_resolve_finds_checkout_by_origin_and_registers(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    origin = bare_origin()
    checkout = _clone(origin, tmp_path / "search" / "checkout")
    registry = Registry(tmp_path / "registry.json")

    remote_url = "https://github.com/acme/widget.git"
    git("remote", "set-url", "origin", remote_url, cwd=checkout)
    found = registry.resolve("acme/widget", search_roots=[checkout.parent])
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
        "acme/cloned", search_roots=[], clone_into=destination
    )

    assert resolved == destination
    assert gitops.is_repo(destination)


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


def test_workflow_round_trips(tmp_path: Path, bare_origin: Callable[..., Path]) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    path = tmp_path / "registry.json"
    registry = Registry(path)
    registry.register(str(checkout), workflow="remote")

    assert Registry(path).entries[f"local/{checkout.name}"].workflow == "remote"


def test_checkout_lookup_resolves_unique_origin_and_rejects_ambiguity(
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
        str((tmp_path / "other").resolve()), str(origin), "remote"
    )
    assert registry.entry_for_checkout(auxiliary) is None
    assert registry.entry_for_checkout(tmp_path / "not-a-repo") is None


def test_refresh_fast_forwards(tmp_path: Path, bare_origin: Callable[..., Path]) -> None:
    origin = bare_origin()
    checkout = _clone(origin, tmp_path / "checkout")
    registry = Registry(tmp_path / "registry.json")
    registry.register(str(checkout))
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
    dirty_registry.register(str(dirty))
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
    registry.register(str(checkout))
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
    registry.register(str(checkout))
    git("remote", "set-url", "origin", str(tmp_path / "different.git"), cwd=checkout)

    result = registry.refresh()[0]

    assert not result.refreshed
    assert result.reason == "origin does not match registered origin"


def test_repo_cli_text_json_and_register_errors(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    assert main_register([str(checkout), "--workflow", "remote"]) == 0
    capsys.readouterr()

    assert main_repos([]) == 0
    assert f"local/{checkout.name}\t{checkout.resolve()}\tremote" in capsys.readouterr().out
    assert main_repos(["--format", "json"]) == 0
    assert '"repos"' in capsys.readouterr().out

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
