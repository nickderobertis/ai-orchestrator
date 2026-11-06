"""The tracked release rule drives the pinned CLI against recorded GitHub answers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

RECORDING = Path(__file__).with_name("release_holdup_recording")
IDENTITY = "github.com/nickderobertis/printobserver"


@pytest.mark.parametrize("declared", [True, False])
def test_tracked_release_rule_finds_the_release_and_labels_its_check(
    tmp_path: Path, declared: bool
) -> None:
    """Without a matching rule no host call is made; with it the real CLI reads both queries."""
    home = tmp_path / "onevcs"
    home.mkdir()
    if declared:
        shutil.copyfile(REPO_ROOT / "config/onevcs.releases.yml", home / "releases.yml")
    gh = tmp_path / "gh"
    # llmlint: ignore-block[e2e_not_mocked] The task requires recorded GitHub answers
    # through onevcs's published ONEVCS_GH seam. Only gh is doubled: the pinned CLI,
    # tracked release rules, git checkout, registration, classification and rendering
    # all run for real, as tests/AGENTS.md permits at the delegated CLI boundary.
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        f"root = pathlib.Path({str(RECORDING)!r})\n"
        "for args in root.glob('*.args'):\n"
        "    if sys.argv[1:] == args.read_text().splitlines():\n"
        "        print(args.with_suffix('.json').read_text())\n"
        "        break\n"
        "else:\n"
        "    sys.exit('unrecorded GitHub query: ' + repr(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    # llmlint: ignore-end[e2e_not_mocked]
    environment = {**os.environ, "ONEVCS_HOME": str(home), "ONEVCS_GH": str(gh)}
    checkout = tmp_path / "printobserver"
    checkout.mkdir()
    for arguments in (
        ["init", "-q", "-b", "main"],
        [
            "-c",
            "user.name=Replay",
            "-c",
            "user.email=replay@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        ["remote", "add", "origin", "https://github.com/nickderobertis/printobserver.git"],
    ):
        subprocess.run(["git", *arguments], cwd=checkout, check=True, capture_output=True)
    binary = str(REPO_ROOT / ".venv/bin/onevcs")
    registered = subprocess.run(
        [binary, "register", str(checkout)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert registered.returncode == 0, registered.stderr
    command = [binary, "release", "holdup", IDENTITY]
    answer = subprocess.run(
        [*command, "--json"], env=environment, capture_output=True, text=True, timeout=30
    )
    assert answer.returncode == 0, answer.stderr
    data = json.loads(answer.stdout)
    assert data["declared"] is declared
    if not declared:
        assert data["changes"] == []
        assert data["cost"] == {"graphql_points": 0, "rest_requests": 0}
        return
    change = data["changes"][0]
    assert change["url"] == "https://github.com/nickderobertis/printobserver/pull/298"
    check = next(check for check in change["checks"] if check["name"] == "supply-chain")
    assert check["required"] is False
    assert check["conclusion"] == "failure"
    rendered = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
    assert rendered.returncode == 0, rendered.stderr
    assert change["url"] in rendered.stdout
    assert "supply-chain (not required — does not block)" in rendered.stdout
    assert check["url"] in rendered.stdout
