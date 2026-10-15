"""The suite's plan-store user-layer boundary, held against the installed store.

`tests/conftest.py` moves the config home to a mirror of the host's with
`PLAN_STORE_USER_LAYER` left out. These ask the locked plan-store CLI itself, through
`config show`, which file each setting came from: so the directory name the fixture
leaves out is the one the store reads, and a store the suite starts reads no user layer.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import PLAN_STORE_USER_LAYER
from published_tools import ONETASKGRAPH_BIN


def _origins(environment: dict[str, str], cwd: Path) -> dict[str, str]:
    """Every setting the store resolves in ``cwd``, mapped to the file it came from."""
    shown = subprocess.run(
        [str(ONETASKGRAPH_BIN), "config", "show", "--json"],
        env=environment,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    return {
        setting["key"]: setting["origin"].get("path", "")
        for setting in json.loads(shown.stdout)["settings"]
    }


def _without_store_variables() -> dict[str, str]:
    """This process's environment with no setting stated through the store's variables."""
    return {key: value for key, value in os.environ.items() if not key.startswith("ONETASKGRAPH_")}


def test_the_store_reads_its_user_layer_from_the_directory_the_suite_leaves_out(
    tmp_path: Path,
) -> None:
    config_home = tmp_path / "config-home"
    layer = config_home / PLAN_STORE_USER_LAYER / "config.yaml"
    layer.parent.mkdir(parents=True)
    layer.write_text("sources:\n  probe:\n    plugin: local-md\n    config:\n      root: probe\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    origins = _origins(
        {**_without_store_variables(), "XDG_CONFIG_HOME": str(config_home)}, elsewhere
    )

    assert origins.get("sources.probe.plugin") == str(layer), origins


def test_a_store_the_suite_starts_reads_no_user_layer(tmp_path: Path) -> None:
    origins = _origins(_without_store_variables(), tmp_path)

    read = [path for path in origins.values() if Path(path).parent.name == PLAN_STORE_USER_LAYER]
    assert read == [], read
