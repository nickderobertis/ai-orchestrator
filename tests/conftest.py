"""Shared test fixtures.

The e2e fixtures build a onejudge base whose provider is `command`, pointed at
`tests/e2e/fake_backend.py`. That swaps only the paid model/harness for a
deterministic double; everything else (the merge, the effective config, the real
`onejudge` CLI and its loop) runs for real.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from orchestrator import BASE_CONFIG, PERSONA_DIR, REPO_ROOT
from orchestrator.config import load_yaml

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"


@pytest.fixture(scope="session")
def onejudge_bin() -> str:
    """Resolve the onejudge binary, failing loudly if the gate's dep is missing."""
    found = shutil.which("onejudge")
    if not found:
        pytest.fail("onejudge not on PATH — run 'just bootstrap' (the e2e gate needs it)")
    return found


@pytest.fixture
def command_base(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that writes a `command`-provider base config.

    Derived from the real base so the shared agent preamble is genuine; only the
    provider is swapped to the fake backend and the turn cap lowered for speed.
    """

    def _make(max_turns: int = 4) -> Path:
        base = load_yaml(BASE_CONFIG)
        base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
        base.setdefault("user", {})["max_turns"] = max_turns
        base["session"] = "e2e"
        path = tmp_path / "command.base.yaml"
        path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
        return path

    return _make


@pytest.fixture
def personas_dir() -> Path:
    return PERSONA_DIR
