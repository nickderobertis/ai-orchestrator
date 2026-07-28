"""The committed per-role harness routing: one file per side of the harness.

Each role's oneharness config names its own priority chain, and those chains are
deliberately different: workers take the alternate Claude subscription first, the
judge and llmlint stay on Codex, and the long-lived orchestrator must not queue in
front of the workers for the subscription they depend on. Nothing else in the
suite pins those orders, so a routing edit meant for one role could silently
change another's.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from orchestrator import REPO_ROOT

WORKER_CONFIG = REPO_ROOT / "oneharness.toml"
JUDGE_CONFIG = REPO_ROOT / "oneharness.judge.toml"
LLMLINT_CONFIG = REPO_ROOT / "oneharness.llmlint.toml"
ORCHESTRATOR_CONFIG = REPO_ROOT / "oneharness.orchestrator.toml"


def _config(path: Path) -> dict[str, Any]:
    # `Any` is what tomllib returns: these configs are arbitrarily nested harness and
    # variant tables, and the assertions below compare whole subtrees rather than
    # reading through a fixed shape, so a narrower type would only be a cast.
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("config", "harnesses"),
    [
        (WORKER_CONFIG, ["claude-code:alternate", "codex"]),
        (JUDGE_CONFIG, ["codex", "claude-code:primary"]),
        (LLMLINT_CONFIG, ["codex"]),
        (ORCHESTRATOR_CONFIG, ["codex", "claude-code:alternate"]),
    ],
)
def test_each_role_keeps_its_own_harness_priority(config: Path, harnesses: list[str]) -> None:
    assert _config(config)["harnesses"] == harnesses


def test_orchestrator_reuses_the_worker_alternate_variant_verbatim() -> None:
    """Two configs name the same alternate identity; hold them to one definition.

    The variant carries the portable `env_from` indirection and the credential
    masking that stops ambient API/OAuth values from shadowing subscription auth —
    a copy that drifted would silently change how one role authenticates.
    """
    worker = _config(WORKER_CONFIG)["harness"]["claude-code"]
    orchestrator = _config(ORCHESTRATOR_CONFIG)["harness"]["claude-code"]
    assert orchestrator["variant"]["alternate"] == worker["variant"]["alternate"]
    assert orchestrator["env"] == worker["env"]
    assert orchestrator["variant"]["alternate"]["env_from"] == {
        "CLAUDE_CONFIG_DIR": "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR"
    }


def test_orchestrator_runs_fallback_and_records_agent_side_history() -> None:
    orchestrator = _config(ORCHESTRATOR_CONFIG)
    assert orchestrator["run_mode"] == "fallback"
    assert orchestrator["history"] is True
    # `role` is the conversation side, not the persona: telemetry and the DAG UI
    # read the orchestrator's own session as an agent-side one.
    assert orchestrator["history_labels"] == {"role": "agent"}
