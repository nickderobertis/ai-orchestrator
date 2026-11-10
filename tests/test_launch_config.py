"""`config/onepipeline.launch.yaml` names only commands and files this checkout carries.

The engine reads the document once, at a launch, and resolves every path in it against the
launch directory, which `scripts/onepipeline.sh` makes this checkout's root. A path it
resolves (the template root, the bus configuration, the maintenance schedule, the drafting
graph) is refused at that launch when it names nothing; a command it spawns (the hooks, the
envelope reviewer and its bar) is spawned only later — a run-end hook once the run has
ended, the reviewer on the first reply carrying an edit — so one that cannot start is
recorded in a log nobody is reading for it. So both are held here, before any launch:
every key the document states names a file of this checkout, and every command key names
one git keeps executable. `tests/e2e/launch_monitor_observer/test_orchestrate_launch_e2e.py`
holds what a real launch records from it.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from orchestrator.root import REPO_ROOT

#: The tracked launch config every `start` names.
LAUNCH_CONFIG = REPO_ROOT / "config" / "onepipeline.launch.yaml"

#: The keys whose value is a command the engine spawns in the launch directory.
COMMANDS = (
    "dispatch_env_hook",
    "success_hook",
    "failure_hook",
    "envelope_reviewer",
    "envelope_reviewer_bar",
)

#: The keys whose value is a path the engine resolves against the launch directory.
PATHS = ("template_root", "bus_config", "maintenance_config", "pr_author_graph")


def _stated() -> dict[str, str]:
    """Every top-level key the document states, with its value as text."""
    loaded = yaml.safe_load(LAUNCH_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{LAUNCH_CONFIG.name} is not a mapping"
    return {str(key): str(value) for key, value in loaded.items()}


@pytest.mark.parametrize("key", COMMANDS)
def test_every_command_the_launch_config_names_is_an_executable_tracked_file(key: str) -> None:
    """A command that could not start would be found only when the engine first spawns it."""
    command = _stated()[key]
    staged = subprocess.run(
        ["git", "ls-files", "--stage", "--", command],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert staged.startswith("100755 "), (
        f"{LAUNCH_CONFIG.name} names {command!r} as {key}, which git does not keep as an "
        f"executable file: {staged!r}"
    )


@pytest.mark.parametrize("key", PATHS)
def test_every_path_the_launch_config_names_is_in_this_checkout(key: str) -> None:
    """A path relative to the checkout root, naming something the checkout carries."""
    path = _stated()[key]
    assert not path.startswith("/"), f"{key} is {path!r}, which no other checkout carries"
    assert (REPO_ROOT / path).exists(), f"{LAUNCH_CONFIG.name} names {path!r} as {key}"


def test_the_document_declares_the_schema_the_pinned_engine_writes() -> None:
    """`schema_version: 13` is the first that carries every key the document states."""
    assert _stated()["schema_version"] == "13"
