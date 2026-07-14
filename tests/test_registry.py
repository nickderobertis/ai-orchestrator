from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import git

from orchestrator import gitops
from orchestrator.registry import Registry, RegistryEntry, RegistryError, main_register, main_repos


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
    "payload, message",
    [
        ("not json", "could not load registry"),
        (json.dumps({"x/y": {"path": 3, "origin": "url", "workflow": "remote"}}), "path must be"),
        (
            json.dumps({"x/y": {"path": "/tmp/x", "origin": "url", "workflow": "other"}}),
            "workflow must be",
        ),
    ],
)
def test_malformed_registry_is_rejected(tmp_path: Path, payload: str, message: str) -> None:
    path = tmp_path / "registry.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(RegistryError, match=message):
        Registry(path)


def test_workflow_round_trips(tmp_path: Path, bare_origin: Callable[..., Path]) -> None:
    checkout = _clone(bare_origin(), tmp_path / "checkout")
    path = tmp_path / "registry.json"
    registry = Registry(path)
    registry.register(str(checkout), workflow="remote")

    assert Registry(path).entries[f"local/{checkout.name}"].workflow == "remote"


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
    assert dirty_registry.refresh()[0].reason == "checkout is dirty"

    origin = bare_origin()
    checkout = _clone(origin, tmp_path / "diverged")
    registry = Registry(tmp_path / "diverged.json")
    registry.register(str(checkout))
    (checkout / "local.txt").write_text("local", encoding="utf-8")
    git("add", "local.txt", cwd=checkout)
    git("commit", "-m", "local", cwd=checkout)
    _commit_and_push(origin, tmp_path / "other", "remote")

    result = registry.refresh()[0]

    assert not result.refreshed
    assert "fast-forward" in result.reason
    assert (checkout / "local.txt").exists()


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
