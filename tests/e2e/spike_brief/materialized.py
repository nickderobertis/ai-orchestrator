"""A checkout of the declared planning interfaces, with only its unused Linear route removed."""

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
    configuration = destination / "onetaskgraph.yaml"
    lines = configuration.read_text().splitlines()
    start = lines.index("  plans:")
    end = start + 1
    while end < len(lines) and (lines[end].startswith("    ") or not lines[end].strip()):
        end += 1
    route = lines.index("    routes:", start, end)
    assert lines[route + 1 : route + 3] == [
        '      - repositories: ["github.com/petsinc/*"]',
        "        to: hellopatient",
    ], "plans route shape moved; refuse before launching against a real source"
    del lines[route : route + 3]
    configuration.write_text("\n".join(lines) + "\n")
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
