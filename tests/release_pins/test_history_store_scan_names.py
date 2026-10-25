"""The history-store scan's recognisers, held to the names the installed oneharness uses.

`tests/tree_scans/test_history_store_never_pruned.py` refuses a tracked recipe, script or
hook that clears the oneharness history store or removes a path naming it. Both of its
recognisers — the store's clear verb, and the store's directory and the variable that moves
it — are oneharness's to name, and a renamed verb or a moved store would leave that scan
matching nothing while it still passes. So they are reconciled here, beside the other
gates on what the pinned tools do, against the CLI `config/oneharness.version` installs.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import ModuleType

from orchestrator.root import REPO_ROOT

#: The `oneharness` CLI this checkout pins, which names the store and the verb.
ONEHARNESS = REPO_ROOT / ".venv" / "bin" / "oneharness"
#: The scan whose recognisers are reconciled, loaded by its path: it is another project's.
SCAN = REPO_ROOT / "tests" / "tree_scans" / "test_history_store_never_pruned.py"


def _scan() -> ModuleType:
    spec = importlib.util.spec_from_file_location("history_store_scan", SCAN)
    assert spec is not None and spec.loader is not None, SCAN
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_recognisers_are_the_installed_oneharness_own_names(tmp_path: Path) -> None:
    """The clear verb, its `--yes`, the store's variable and its directory, from oneharness.

    A renamed verb or a moved store would leave this scan matching nothing while it still
    passes, so each recogniser is held to what the pinned CLI says or does: its help names
    `history clear`, the `--yes` that makes it delete, and `ONEHARNESS_HISTORY_DIR`; and a
    real history run under an empty state home, with no configuration and no variable,
    puts its store at a path the store recogniser names.
    """
    scan = _scan()
    helped = subprocess.run(
        [str(ONEHARNESS), "history", "clear", "--help"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert scan.CLEAR.search("oneharness history clear"), "the scan no longer recognises the verb"
    assert "Usage: oneharness history clear" in helped, helped
    assert "--yes" in helped and "Actually delete" in helped, helped
    assert scan.STORE.search("ONEHARNESS_HISTORY_DIR") and "ONEHARNESS_HISTORY_DIR" in helped, (
        helped
    )

    pointers = tmp_path / "pointers.jsonl"
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")
    }
    environment |= {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        "MOCK_STDOUT": json.dumps({"result": "ok"}),
    }
    subprocess.run(
        [str(ONEHARNESS), "run", "--no-config", "--history", "--history-pointer-file"]
        + [str(pointers), "--harness", "codex", "--mock-harness", "codex"]
        + ["--prompt", "where is the store"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    (pointer,) = [json.loads(line) for line in pointers.read_text("utf-8").splitlines()]
    assert Path(pointer["history_dir"]) == tmp_path / "state" / "oneharness" / "history"
    assert scan.STORE.search(pointer["history_dir"]), pointer
