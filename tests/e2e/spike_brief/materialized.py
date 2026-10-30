"""A checkout of the declared planning interfaces.

The tracked `onetaskgraph.yaml` routes nothing to a Linear source — an organization's routes
live in a host's user layer, which the suite never hands a test — so the copy launches against
no real tracker as it stands.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT


def checkout(destination: Path) -> Path:
    declaration = json.loads((Path(__file__).parent / "project.json").read_text())
    for entry in declaration["namedInputs"]["spikeBriefWorkspace"]:
        if not entry.startswith("{workspaceRoot}/"):
            continue
        relative = Path(entry.removeprefix("{workspaceRoot}/"))
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    subprocess.run(
        ["uv", "sync", "--locked", "--offline"],
        cwd=destination,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(120),
        check=True,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_NO_SYNC"}
        },
    )
    # The command symlinks resolve through this checkout's published npm install.
    (destination / "node_modules").symlink_to(REPO_ROOT / "node_modules", target_is_directory=True)
    installed_lint = shutil.which("llmlint")
    assert installed_lint is not None, "provision the published llmlint before this journey"
    shutil.copy2(installed_lint, destination / ".venv/bin/llmlint")
    (destination / ".env").write_text("GH_PROJECTS_TOKEN=fixture-token\n")
    for args in [
        ["init", "-b", "main"],
        ["remote", "add", "origin", "https://github.com/nickderobertis/ai-orchestrator.git"],
        ["config", "user.name", "Brief fixture"],
        ["config", "user.email", "brief@example.test"],
        ["add", "."],
        ["commit", "-m", "test: seed declared planning interfaces"],
    ]:
        subprocess.run(["git", *args], cwd=destination, capture_output=True, check=True)
    return destination
