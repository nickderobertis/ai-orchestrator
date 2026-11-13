"""What this host states about human items, held to the engine release it pins.

The design review's undo refuses on facts the engine writes, and `AGENTS.md` tells a manager the
`note` verb's flag and the channel kind a resolution arrives under. Each is reconciled with the
release that defines it: its source at the tag `config/onepipeline.version` names, read from a
registered checkout, or the verb the install runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from registered_checkouts import discover_checkouts

from orchestrator import design_approval
from orchestrator.root import REPO_ROOT

MANAGER = REPO_ROOT / "AGENTS.md"
ENGINE_PIN = REPO_ROOT / "config" / "onepipeline.version"
ENGINE = REPO_ROOT / "node_modules" / ".bin" / "onepipeline"


def _engine_checkout() -> Path:
    """A registered checkout of the engine, or a failure naming the remedy, and never a skip."""
    for path in discover_checkouts().held.get("github.com/nickderobertis/onepipeline", []):
        return path
    raise AssertionError(
        "no registered checkout of onepipeline on this host, so nothing here can reconcile "
        "what this repository states about it; clone it and register the clone with "
        "`onevcs register <path>`"
    )


def _at_the_pin(path: str) -> str:
    """One file of the engine at the adopted tag, never the checkout's working tree."""
    tag = f"v{ENGINE_PIN.read_text(encoding='utf-8').strip()}"
    return subprocess.run(
        ["git", "-C", str(_engine_checkout()), "show", f"{tag}:{path}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_design_review_undo_reads_the_launch_facts_the_adopted_engine_writes() -> None:
    """The driver claim's key, the runs root and the launch record, as the pinned engine has them.

    The undo refuses on them, so a renamed one would read as a plan that never launched.
    """
    assert f'DRIVER_KEY: &str = "{design_approval.DRIVER_CLAIM}"' in _at_the_pin("src/taskgraph.rs")
    ledger = _at_the_pin("src/ledger.rs")
    assert f'RUNS_DIR_ENV: &str = "{design_approval.RUNS_ROOT_ENV}"' in ledger
    assert f'DEFAULT_RUNS_DIR: &str = "{design_approval.DEFAULT_RUNS_ROOT}"' in ledger
    assert f'self.dir.join("{design_approval.LAUNCH_RECORD}")' in ledger


def test_the_human_item_note_flag_and_surface_kind_agents_md_states_are_the_engines() -> None:
    """The `note` verb's text flag, from its own help, and the kind, from the pinned contract."""
    stated = " ".join(MANAGER.read_text(encoding="utf-8").split())
    assert "`just human-items note <item>`, its text given as `--text`" in stated
    assert "as a non-blocking `human-item` surface" in stated

    help_text = subprocess.run(
        [str(ENGINE), "human-items", "note", "--help"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "--text <TEXT>" in help_text
    contract = " ".join(_at_the_pin("docs/contract.md").split())
    assert "surface of kind `human-item`" in contract
