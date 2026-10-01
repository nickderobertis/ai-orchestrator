"""The adopted engine's layered document loader reaches the pinned renderer."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] Wheels escape tree keys.
# llmlint: ignore-block[shell_test_tiers_stay_split] Wheels escape tree keys.
# This journey reconciles installed producer binaries, whose contents are outside this
# workspace's cache key. `reads_checkouts` routes it to the uncached test-checkouts target;
# a separate affected project would skip the very wheel replacement this adoption must
# verify. `tests/test_nx_cache_scope.py` holds that routing to the suite's tier partition.
pytestmark = pytest.mark.reads_checkouts


def test_repository_document_extends_host_and_recovers_from_missing_parent(tmp_path: Path) -> None:
    """Resolve and render two layers, refuse a missing parent, then restore it."""
    host = tmp_path / "host"
    repo = tmp_path / "repo"
    layer = repo / ".onepipeline" / "templates"
    host.mkdir()
    layer.mkdir(parents=True)
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    host.joinpath("templates.yaml").write_text(
        "onepipeline_templates: 1\ntemplates:\n  journey:\n    role: document\n"
        "    description: A layered document journey.\n",
        encoding="utf-8",
    )
    parent = host / "journey.md.j2"
    original = (
        "# Journey\n{% block summary %}Host summary.\n{% endblock %}\n"
        "{% block guidance %}Host guidance.\n{% endblock %}\n"
    )
    parent.write_text(original, encoding="utf-8")
    child = layer / "journey.md.j2"
    child.write_text(
        '{% extends "onepipeline/host/journey.md.j2" %}\n'
        "{% block guidance %}Repository guidance.\n{% endblock %}\n",
        encoding="utf-8",
    )
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("ONETASKGRAPH_")},
        "ONEPIPELINE_TEMPLATE_ROOT": str(host),
    }
    command = [
        str(REPO_ROOT / ".venv/bin/onepipeline"),
        "template",
        "resolve",
        "journey",
        "--repo",
        str(repo),
        "--json",
    ]

    def resolve() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def render(loader: str) -> str:
        rendered = subprocess.run(
            [
                str(REPO_ROOT / ".venv/bin/onetaskgraph"),
                "template",
                "render",
                "--template-loader",
                "-",
                "--no-interactive",
                "--json",
            ],
            input=loader,
            cwd=tmp_path,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert rendered.returncode == 0, rendered.stderr
        return str(json.loads(rendered.stdout)["body"])

    resolved = resolve()
    assert resolved.returncode == 0, resolved.stderr
    assert json.loads(resolved.stdout)["chain"] == [
        {"name": "journey.md.j2", "layer": "repository", "path": str(child)},
        {"name": "onepipeline/host/journey.md.j2", "layer": "host", "path": str(parent)},
    ]
    assert render(resolved.stdout) == "# Journey\nHost summary.\nRepository guidance.\n"

    parent.unlink()
    refused = resolve()
    assert refused.returncode != 0
    assert "onepipeline/host/journey.md.j2" in refused.stderr
    parent.write_text(original, encoding="utf-8")
    restored = resolve()
    assert restored.returncode == 0, restored.stderr
    assert render(restored.stdout) == "# Journey\nHost summary.\nRepository guidance.\n"


# llmlint: ignore-end[shell_test_tiers_stay_split]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
