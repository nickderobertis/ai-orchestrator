from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import git

from orchestrator.registry import Registry, main_register, main_repos


def test_registry_register_discover_and_refresh_journey(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    origin = bare_origin()
    checkout = tmp_path / "dev" / "widget"
    git("clone", str(origin), str(checkout))

    assert main_register([str(checkout)]) == 0
    assert str(checkout.resolve()) in capsys.readouterr().out

    registry_path = home / ".ai-orchestrator" / "repos.json"
    registry_path.unlink()
    remote_url = "https://github.com/acme/widget.git"
    git("remote", "set-url", "origin", remote_url, cwd=checkout)
    registry = Registry()
    assert registry.resolve("acme/widget", search_roots=[checkout.parent]) == checkout.resolve()
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", remote_url)

    writer = tmp_path / "writer"
    git("clone", str(origin), str(writer))
    (writer / "new.txt").write_text("new\n", encoding="utf-8")
    git("add", "new.txt", cwd=writer)
    git("commit", "-m", "upstream", cwd=writer)
    git("push", "origin", "main", cwd=writer)

    assert main_repos(["--refresh", "--format", "json"]) == 0
    output = capsys.readouterr().out
    assert '"refreshed": true' in output
    assert (checkout / "new.txt").read_text(encoding="utf-8") == "new\n"
