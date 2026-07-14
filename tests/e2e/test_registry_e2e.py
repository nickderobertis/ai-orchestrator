from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import git

from orchestrator.registry import Registry, RegistryEntry


def _cli(name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).parent / name
    return subprocess.run([str(executable), *args], text=True, capture_output=True, check=check)


def test_registry_register_discover_and_refresh_journey(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    origin = bare_origin()
    checkout = tmp_path / "dev" / "widget"
    git("clone", str(origin), str(checkout))

    registered = _cli("orchestrator-register-repo", str(checkout))
    assert str(checkout.resolve()) in registered.stdout

    registry_path = home / ".ai-orchestrator" / "repos.json"
    registry_path.unlink()
    remote_url = "https://github.com/acme/widget.git"
    git("remote", "set-url", "origin", remote_url, cwd=checkout)
    registry = Registry()
    assert registry.resolve("acme/widget", search_roots=[checkout.parent]) == checkout.resolve()
    persisted = Registry()
    assert persisted.entries["acme/widget"].path == str(checkout.resolve())
    assert persisted.resolve("acme/widget", search_roots=[]) == checkout.resolve()

    # Route the discovered checkout back to the local bare remote for the
    # offline refresh leg, and persist the corresponding origin identity.
    git("remote", "set-url", "origin", str(origin), cwd=checkout)
    registry.entries["acme/widget"] = RegistryEntry(str(checkout.resolve()), str(origin), "remote")
    registry.save()

    writer = tmp_path / "writer"
    git("clone", str(origin), str(writer))
    (writer / "new.txt").write_text("new\n", encoding="utf-8")
    git("add", "new.txt", cwd=writer)
    git("commit", "-m", "upstream", cwd=writer)
    git("push", "origin", "main", cwd=writer)

    listed = _cli("orchestrator-repos", "--refresh", "--format", "json")
    output = listed.stdout
    assert '"refreshed": true' in output
    assert (checkout / "new.txt").read_text(encoding="utf-8") == "new\n"


@pytest.mark.parametrize(
    "payload, error",
    [
        (
            "not json",
            "could not load registry {path}: Expecting value: line 1 column 1 (char 0)",
        ),
        ("[]", "registry {path} must contain a JSON object"),
        (
            json.dumps({"not-a-slug": {"path": "/tmp", "origin": "url", "workflow": "remote"}}),
            "registry key 'not-a-slug' must be a normalized owner/name slug",
        ),
        (
            json.dumps({"x/y": {"path": "/tmp"}}),
            "registry entry 'x/y' must contain path, origin, and workflow",
        ),
        (
            json.dumps({"x/y": {"path": 3, "origin": "url", "workflow": "remote"}}),
            "registry entry 'x/y' path must be an absolute string",
        ),
        (
            json.dumps({"x/y": {"path": "/tmp", "origin": "", "workflow": "remote"}}),
            "registry entry 'x/y' origin must be a non-empty string",
        ),
        (
            json.dumps({"x/y": {"path": "/tmp", "origin": "url", "workflow": "other"}}),
            "registry entry 'x/y' workflow must be 'local' or 'remote'",
        ),
    ],
)
def test_repos_cli_reports_invalid_registry_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
    error: str,
) -> None:
    home = tmp_path / "home"
    registry_path = home / ".ai-orchestrator" / "repos.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(payload, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    result = _cli("orchestrator-repos", check=False)

    expected = (
        "usage: orchestrator-repos [-h] [--refresh] [--format {text,json}]\n"
        f"orchestrator-repos: error: {error.format(path=registry_path)}\n"
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == expected
    assert "Traceback" not in result.stderr
